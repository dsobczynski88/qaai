import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import Callable

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from langgraph.checkpoint.memory import MemorySaver

from autoqa.api.routes import router
from autoqa.api.services import HazardReviewService, RTMReviewService, TestCaseReviewService
from autoqa.components.clients import RateLimitOpenAIClient
from autoqa.components.test_suite_reviewer.pipeline import RTMReviewerRunnable
from autoqa.core.config import settings
from autoqa.core.constants import MAX_REQUEST_BODY_SIZE


logger = logging.getLogger("autoqa.api.main")
request_logger = logging.getLogger("autoqa.api.requests")


def build_pyjama_config():
    """Build optional PyJama config from settings."""
    if not settings.jama_host_address:
        return None

    try:
        from autoqa.components.shared.data_integration import PyJamaNodeConfig

        pyjama_config = PyJamaNodeConfig(
            host_address=settings.jama_host_address,
            client_id=settings.jama_client_id,
            client_secret=settings.jama_client_secret,
        )
        logger.info("PyJama config initialized (host: %s)", settings.jama_host_address)
        return pyjama_config
    except Exception as exc:
        logger.warning("Could not initialize PyJama config: %s", exc)
        return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    client = RateLimitOpenAIClient(
        api_key=settings.openai_api_key,
        max_requests_per_minute=settings.max_requests_per_minute,
        max_tokens_per_minute=settings.max_tokens_per_minute,
    )

    # Configure model_kwargs with max_tokens to handle large outputs (100+ test cases).
    # Haiku supports up to 16K output tokens; this ensures the summarizer can process
    # all test cases without truncation.
    model_kwargs = {"max_tokens": settings.max_output_tokens}

    logger.info("Initializing AutoQA services...")
    logger.info("Model: %s", settings.model)
    logger.info("Max requests per minute: %s", settings.max_requests_per_minute)
    logger.info("Max tokens per minute: %s", settings.max_tokens_per_minute)

    pyjama_config = build_pyjama_config()

    # Build the RTM subgraph once and share it between both services so the
    # compiled graph + Mermaid PNG render only happen on a single import.
    rtm_runnable = RTMReviewerRunnable(
        client=client,
        model=settings.model,
        model_kwargs=model_kwargs,
        checkpointer=MemorySaver(),
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
        rtm_runnable=rtm_runnable,
        pyjama_config=pyjama_config,
    )
    app.state.test_case_service = TestCaseReviewService(
        client=client,
        model=settings.model,
        model_kwargs=model_kwargs,
        pyjama_config=pyjama_config,
    )

    # Backwards-compat: existing callers reference app.state.service for the RTM service.
    app.state.service = app.state.rtm_service

    logger.info("AutoQA services initialized successfully")
    yield

    logger.info("Shutting down AutoQA services...")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    environment = os.getenv("ENVIRONMENT", "development")
    is_production = environment == "production"

    app = FastAPI(
        title="AutoQA Reviewer API",
        version="0.2.0",
        lifespan=lifespan,
        docs_url="/docs" if not is_production else None,
        redoc_url="/redoc" if not is_production else None,
    )

    @app.middleware("http")
    async def log_requests(request: Request, call_next: Callable):
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id

        start_time = time.perf_counter()

        request_logger.info(
            "Request started: %s %s",
            request.method,
            request.url.path,
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "client": request.client.host if request.client else None,
            },
        )

        try:
            response = await call_next(request)
        except Exception as exc:
            elapsed = time.perf_counter() - start_time
            request_logger.error(
                "Request failed: %s %s",
                request.method,
                request.url.path,
                extra={
                    "request_id": request_id,
                    "error": str(exc),
                    "elapsed_seconds": elapsed,
                },
                exc_info=True,
            )
            raise

        elapsed = time.perf_counter() - start_time

        request_logger.info(
            "Request completed: %s %s - %s",
            request.method,
            request.url.path,
            response.status_code,
            extra={
                "request_id": request_id,
                "status_code": response.status_code,
                "elapsed_seconds": elapsed,
            },
        )

        response.headers["X-Request-ID"] = request_id
        return response

    @app.middleware("http")
    async def limit_request_size(request: Request, call_next: Callable):
        """Reject requests with bodies larger than MAX_REQUEST_BODY_SIZE."""
        if request.method == "POST":
            content_length = request.headers.get("content-length")
            if content_length and int(content_length) > MAX_REQUEST_BODY_SIZE:
                return JSONResponse(
                    status_code=413,
                    content={"detail": "Request body too large (max 10MB)"},
                )

        return await call_next(request)

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

    # Serve the HTML frontend from autoqa/api/static/ at the root path.
    # Must be mounted AFTER the API router so API routes take precedence.
    static_dir = os.path.join(os.path.dirname(__file__), "static")
    os.makedirs(static_dir, exist_ok=True)
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")

    logger.info("AutoQA API created (environment: %s)", environment)
    return app


app = create_app()