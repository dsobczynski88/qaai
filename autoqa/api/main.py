import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from langgraph.checkpoint.memory import MemorySaver

from autoqa.api.middleware import limit_request_size, log_requests
from autoqa.api.routes import router
from autoqa.api.services import HazardReviewService, RTMReviewService, TestCaseReviewService
from autoqa.components.clients import RateLimitOpenAIClient
from autoqa.components.test_suite_reviewer.pipeline import RTMReviewerRunnable
from autoqa.core.cache import ReviewCacheManager
from autoqa.core.config import settings
from autoqa.core.telemetry import TokenUsageTracker
from autoqa.core.logging_config import create_timestamped_run_directory, setup_logging


logger = logging.getLogger("autoqa.api.main")


def build_pyjama_config():
    """Build optional PyJama config from settings.

    test_mode (cache-only, no live JAMA) defaults to settings.pyjama_test_mode
    (PYJAMA_TEST_MODE) and is the server-wide default; the API "test mode" toggle
    overrides it per request. In test_mode credentials are optional, so a config
    is still built even when JAMA_HOST_ADDRESS is unset.
    """
    test_mode = settings.pyjama_test_mode
    if not settings.jama_host_address and not test_mode:
        return None

    try:
        from autoqa.components.shared.data_integration import PyJamaNodeConfig

        pyjama_config = PyJamaNodeConfig(
            host_address=settings.jama_host_address,
            client_id=settings.jama_client_id,
            client_secret=settings.jama_client_secret,
            test_mode=test_mode,
        )
        logger.info(
            "PyJama config initialized (host: %s, test_mode: %s)",
            settings.jama_host_address, test_mode,
        )
        return pyjama_config
    except Exception as exc:
        logger.warning("Could not initialize PyJama config: %s", exc)
        return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize telemetry tracker before client
    telemetry_tracker = TokenUsageTracker(
        file_path=settings.telemetry_file_path,
        input_cost_per_million=settings.token_cost_input_per_m,
        output_cost_per_million=settings.token_cost_output_per_m,
    )
    logger.info("Telemetry tracker initialized (file: %s)", settings.telemetry_file_path)

    # Initialize OpenAI client with all required parameters
    client = RateLimitOpenAIClient(
        api_key=settings.openai_api_key,
        base_url=settings.url,
        max_requests_per_minute=settings.max_requests_per_minute,
        max_tokens_per_minute=settings.max_tokens_per_minute,
        telemetry_tracker=telemetry_tracker,
    )

    # max_tokens handles large outputs (100+ test cases) without truncation.
    model_kwargs = {"max_tokens": settings.max_output_tokens}

    logger.info("Initializing AutoQA services...")
    logger.info("Model: %s", settings.model)
    logger.info("API Base URL: %s", settings.url or "default (OpenAI)")
    logger.info("Max requests per minute: %s", settings.max_requests_per_minute)
    logger.info("Max tokens per minute: %s", settings.max_tokens_per_minute)

    pyjama_config = build_pyjama_config()

    # One shared cache, used by all three reviewers. Per-run behaviour is driven
    # by the UI's "use cache" toggle (→ cache_mode in graph state). The global
    # ENABLE_CACHE switch turns it off entirely.
    cache_manager = None
    if settings.enable_cache:
        cache_manager = ReviewCacheManager(
            cache_dir=settings.cache_dir,
            redis_url=settings.redis_url,
            telemetry_tracker=telemetry_tracker,
        )
        logger.info("Review cache enabled (dir: %s)", settings.cache_dir)

    # RTM runnable used by the standalone test-suite endpoint — cache-enabled.
    # The hazard reviewer deliberately builds its OWN uncached embedded RTM
    # (its subgraph result is cached as one blob per requirement instead).
    rtm_runnable = RTMReviewerRunnable(
        client=client,
        model=settings.model,
        model_kwargs=model_kwargs,
        checkpointer=MemorySaver(),
        cache_manager=cache_manager,
    )

    app.state.rtm_service = RTMReviewService(
        client,
        settings.model,
        rtm_runnable=rtm_runnable,
        pyjama_config=pyjama_config,
    )
    app.state.hazard_service = HazardReviewService(
        client,
        settings.model,
        pyjama_config=pyjama_config,
        cache_manager=cache_manager,
    )
    app.state.test_case_service = TestCaseReviewService(
        client=client,
        model=settings.model,
        model_kwargs=model_kwargs,
        pyjama_config=pyjama_config,
        cache_manager=cache_manager,
    )

    # Backwards-compat: existing callers reference app.state.service for the RTM service.
    app.state.service = app.state.rtm_service

    logger.info("AutoQA services initialized successfully")
    yield

    logger.info("Shutting down AutoQA services...")
    telemetry_tracker.log_summary()


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    # Initialize logging FIRST, before anything else
    run_dir = create_timestamped_run_directory(base_logs_dir="./logs")
    setup_logging(run_dir)
    
    # Now get logger after logging is configured
    startup_logger = logging.getLogger("autoqa.api.main")
    startup_logger.info("Application startup initiated")
    
    environment = os.getenv("ENVIRONMENT", "development")
    is_production = environment == "production"

    app = FastAPI(
        title="AutoQA Reviewer API",
        version="0.2.0",
        lifespan=lifespan,
        docs_url="/docs" if not is_production else None,
        redoc_url="/redoc" if not is_production else None,
    )
    
    # Store run directory in app state for reference
    app.state.run_dir = run_dir

    app.middleware("http")(log_requests)
    app.middleware("http")(limit_request_size)

    allowed_origins = os.getenv("ALLOWED_ORIGINS", "*").split(",")
    if allowed_origins != ["*"]:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=allowed_origins,
            allow_credentials=True,
            allow_methods=["POST", "GET"],
            allow_headers=["*"],
        )

    app.add_middleware(GZipMiddleware, minimum_size=1000)

    app.include_router(router)

    # Must be mounted AFTER the API router so API routes take precedence.
    static_dir = os.path.join(os.path.dirname(__file__), "static")
    os.makedirs(static_dir, exist_ok=True)
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")

    logger.info("AutoQA API created (environment: %s)", environment)
    return app


app = create_app()
