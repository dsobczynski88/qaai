import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from langgraph.checkpoint.memory import MemorySaver

from autoqa.api.routes import router
from autoqa.api.services import HazardReviewService, RTMReviewService, TestCaseReviewService
from autoqa.components.clients import RateLimitOpenAIClient
from autoqa.components.test_suite_reviewer.pipeline import RTMReviewerRunnable
from autoqa.core.config import settings
from autoqa.core.constants import MAX_REQUEST_BODY_SIZE

logger = logging.getLogger("autoqa.api.main")


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Log all API requests with timing and correlation IDs.
    
    Adds a unique request_id to each request for tracing and logs request/response
    details including timing, status codes, and errors.
    """
    
    async def dispatch(self, request: Request, call_next: Callable):
        """Process request with logging.
        
        Args:
            request: Incoming HTTP request.
            call_next: Next middleware or route handler.
            
        Returns:
            Response with X-Request-ID header added.
        """
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id
        
        request_logger = logging.getLogger("autoqa.api.requests")
        start_time = time.perf_counter()
        
        request_logger.info(
            f"Request started: {request.method} {request.url.path}",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "client": request.client.host if request.client else None,
            }
        )
        
        try:
            response = await call_next(request)
            elapsed = time.perf_counter() - start_time
            
            request_logger.info(
                f"Request completed: {request.method} {request.url.path} - {response.status_code}",
                extra={
                    "request_id": request_id,
                    "status_code": response.status_code,
                    "elapsed_seconds": elapsed,
                }
            )
            
            response.headers["X-Request-ID"] = request_id
            return response
            
        except Exception as e:
            elapsed = time.perf_counter() - start_time
            request_logger.error(
                f"Request failed: {request.method} {request.url.path}",
                extra={
                    "request_id": request_id,
                    "error": str(e),
                    "elapsed_seconds": elapsed,
                },
                exc_info=True
            )
            raise


@asynccontextmanager
async def lifespan(app: FastAPI):
    client = RateLimitOpenAIClient(
        api_key=settings.openai_api_key,
        max_requests_per_minute=settings.max_requests_per_minute,
        max_tokens_per_minute=settings.max_tokens_per_minute,
    )
    # Configure model_kwargs with max_tokens to handle large outputs (100+ test cases)
    # Haiku supports up to 16K output tokens; this ensures the summarizer can process
    # all test cases without truncation
    model_kwargs = {"max_tokens": settings.max_output_tokens}
    
    logger.info("Initializing AutoQA services...")
    logger.info(f"Model: {settings.model}")
    logger.info(f"Max requests per minute: {settings.max_requests_per_minute}")
    logger.info(f"Max tokens per minute: {settings.max_tokens_per_minute}")
    
    # Build the RTM subgraph once and share it between both services so the
    # compiled graph + Mermaid PNG render only happen on a single import.
    rtm_runnable = RTMReviewerRunnable(
        client=client,
        model=settings.model,
        model_kwargs=model_kwargs,
        checkpointer=MemorySaver(),
    )
    app.state.rtm_service = RTMReviewService(
        client, settings.model, rtm_runnable=rtm_runnable
    )
    app.state.hazard_service = HazardReviewService(
        client, settings.model, rtm_runnable=rtm_runnable
    )
    
    # Initialize test case service (independent graph, no sharing)
    app.state.test_case_service = TestCaseReviewService(
        client=client,
        model=settings.model,
        model_kwargs=model_kwargs,
    )
    
    # Backwards-compat: existing callers reference app.state.service for the RTM service.
    app.state.service = app.state.rtm_service
    
    logger.info("AutoQA services initialized successfully")
    yield
    
    logger.info("Shutting down AutoQA services...")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application.
    
    Sets up middleware for:
    - Request logging with correlation IDs
    - Request size limits
    - CORS (if configured)
    - Response compression
    
    Returns:
        FastAPI: Configured application instance.
    """
    # Determine if we're in production based on environment
    import os
    environment = os.getenv("ENVIRONMENT", "development")
    is_production = environment == "production"
    
    app = FastAPI(
        title="AutoQA Reviewer API",
        version="0.2.0",
        lifespan=lifespan,
        docs_url="/docs" if not is_production else None,
        redoc_url="/redoc" if not is_production else None,
    )
    
    # Request logging middleware
    app.add_middleware(RequestLoggingMiddleware)
    
    # Request size limit middleware
    @app.middleware("http")
    async def limit_request_size(request: Request, call_next: Callable):
        """Reject requests with bodies larger than MAX_REQUEST_BODY_SIZE."""
        if request.method == "POST":
            content_length = request.headers.get("content-length")
            if content_length and int(content_length) > MAX_REQUEST_BODY_SIZE:
                return JSONResponse(
                    status_code=413,
                    content={"detail": "Request body too large (max 10MB)"}
                )
        return await call_next(request)
    
    # CORS middleware (configure allowed_origins based on your deployment)
    allowed_origins = os.getenv("ALLOWED_ORIGINS", "*").split(",")
    if allowed_origins != ["*"]:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=allowed_origins,
            allow_credentials=True,
            allow_methods=["POST", "GET"],
            allow_headers=["*"],
        )
    
    # Response compression
    app.add_middleware(GZipMiddleware, minimum_size=1000)
    
    app.include_router(router)
    
    logger.info(f"AutoQA API created (environment: {environment})")
    return app


app = create_app()
