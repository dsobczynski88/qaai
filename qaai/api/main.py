import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles

from qaai.api.middleware import limit_request_size, log_requests


class NoCacheStaticFiles(StaticFiles):
    """StaticFiles that disables blind browser caching of the UI assets.

    Starlette already emits ``ETag``/``Last-Modified``; adding ``Cache-Control:
    no-cache`` forces the browser to revalidate (cheap 304) instead of silently
    reusing a stale ``index.html`` / ``script.js`` from a previous session — which
    otherwise causes UI edits (new form fields, etc.) to never appear.
    """

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response
from qaai.api.jobs import JobManager
from qaai.api.routes import router
from qaai.api.services import HazardReviewService, RTMReviewService, TestCaseReviewService
from qaai.agents.clients import RateLimitOpenAIClient
from qaai.core.cache import ReviewCacheManager
from qaai.core.config import settings
from qaai.core.telemetry import TokenUsageTracker
from qaai.core.logging_config import install_run_routing


logger = logging.getLogger("qaai.api.main")


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
        from qaai.agents.shared.data_integration import PyJamaNodeConfig

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
    # Initialize telemetry tracker before client. file_path=None ⇒ the tracker
    # resolves its target from settings.telemetry_file_path at each write, so the
    # per-request start_new_run() re-points token_usage.jsonl into that run folder.
    telemetry_tracker = TokenUsageTracker(
        file_path=None,
        input_cost_per_million=settings.token_cost_input_per_m,
        output_cost_per_million=settings.token_cost_output_per_m,
    )
    logger.info("Telemetry tracker initialized (per-run token_usage.jsonl)")

    # Initialize OpenAI client with all required parameters
    client = RateLimitOpenAIClient(
        api_key=settings.openai_api_key,
        base_url=settings.url,
        max_requests_per_minute=settings.max_requests_per_minute,
        max_tokens_per_minute=settings.max_tokens_per_minute,
        telemetry_tracker=telemetry_tracker,
    )


    # max_tokens handles large outputs (100+ test cases) without truncation.
    # Some models expect max_completion_tokens instead — driven by config, not a literal.
    if settings.model in settings.models_using_max_completion_tokens:
        settings.model_kwargs.update({"max_completion_tokens": settings.max_output_tokens})
    else:
        settings.model_kwargs.update({"max_tokens": settings.max_output_tokens})

    logger.info("Initializing QAAI services...")
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

    # Each reviewer service builds one compiled graph per prompt set (v3 baseline
    # / v4 edge-case), selected per request by the "Include Edge Case Analysis"
    # toggle. The standalone RTM graphs are cache-enabled; the hazard graphs each
    # embed their OWN uncached RTM (its subgraph result is cached as one blob per
    # requirement, namespaced by prompt set, instead).
    app.state.rtm_service = RTMReviewService(
        client,
        settings.model,
        model_kwargs=settings.model_kwargs,
        pyjama_config=pyjama_config,
        cache_manager=cache_manager,
    )
    app.state.hazard_service = HazardReviewService(
        client,
        settings.model,
        # Intentional: pass model_kwargs (max_tokens) here too, so the hazard
        # graphs and their embedded RTM respect max_output_tokens like the RTM/TC
        # services do (avoids truncating large completions). Do not drop this.
        model_kwargs=settings.model_kwargs,
        pyjama_config=pyjama_config,
        cache_manager=cache_manager,
    )
    app.state.test_case_service = TestCaseReviewService(
        client=client,
        model=settings.model,
        model_kwargs=settings.model_kwargs,
        pyjama_config=pyjama_config,
        cache_manager=cache_manager,
    )

    # Backwards-compat: existing callers reference app.state.service for the RTM service.
    app.state.service = app.state.rtm_service

    # Expose the shared client + telemetry tracker so GET /api/v1/usage can report
    # centralized, all-users RPM/TPM utilization and rolling token/cost totals. On
    # a single instance these are the only limiter/tracker, so they see everything.
    app.state.llm_client = client
    app.state.telemetry_tracker = telemetry_tracker

    # Background job registry: reviews run async (202 + poll) so the upstream proxy
    # never sees a multi-minute idle request and can't return a 504.
    app.state.job_manager = JobManager()

    logger.info("QAAI services initialized successfully")
    yield

    logger.info("Shutting down QAAI services...")
    telemetry_tracker.log_summary()


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    # Install console + per-run routing logging handlers ONCE at boot. We
    # deliberately do NOT create a run folder here — the first logs/run-<ts>-<uuid>/
    # folder is created by the first review (start_new_run inside the service).
    # Each review binds its own run folder via the current_run_dir contextvar, and
    # the routing handler resolves it per record, so concurrent reviews write to
    # isolated log files with no global handler swap. Boot/lifespan logs go to
    # stdout (captured by the process manager / container runtime).
    install_run_routing()

    startup_logger = logging.getLogger("qaai.api.main")
    startup_logger.info("Application startup initiated")
    
    environment = os.getenv("ENVIRONMENT", "development")
    is_production = environment == "production"

    app = FastAPI(
        title="QAAI Reviewer API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if not is_production else None,
        redoc_url="/redoc" if not is_production else None,
    )

    app.middleware("http")(log_requests)
    app.middleware("http")(limit_request_size)
    # Note: no app.state.run_dir — run folders are created per request, not at boot.

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

    # Serve the repo-root docs/ HTML guide at /guide (before the "/" catch-all so it
    # wins). Guarded: an installed deploy without docs/ simply skips the mount.
    docs_dir = Path(__file__).resolve().parents[2] / "docs"
    if docs_dir.is_dir():
        app.mount("/guide", StaticFiles(directory=str(docs_dir), html=True), name="guide")

    # Serve the built Vue SPA (qaai/web/dist) at "/". Must be mounted AFTER the API
    # router so /api/v1/* routes take precedence. During migration (or before the
    # SPA has been built) fall back to the legacy static/ dir so Python-only dev and
    # existing deployments keep working. Hash-mode routing in the SPA means no
    # server-side SPA fallback is required.
    web_dist = Path(__file__).resolve().parents[1] / "web" / "dist"
    legacy_static = Path(__file__).resolve().parent / "static"
    if web_dist.is_dir():
        ui_dir = str(web_dist)
    else:
        ui_dir = str(legacy_static)
        logger.warning(
            "Vue SPA build not found at %s — serving legacy static UI. "
            "Run `npm install && npm run build` in qaai/web.",
            web_dist,
        )
    os.makedirs(ui_dir, exist_ok=True)
    app.mount("/", NoCacheStaticFiles(directory=ui_dir, html=True), name="static")

    logger.info("QAAI API created (environment: %s)", environment)
    return app


app = create_app()
