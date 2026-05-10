from contextlib import asynccontextmanager

from fastapi import FastAPI
from langgraph.checkpoint.memory import MemorySaver

from autoqa.api.routes import router
from autoqa.api.services import HazardReviewService, RTMReviewService
from autoqa.components.clients import RateLimitOpenAIClient
from autoqa.components.test_suite_reviewer.pipeline import RTMReviewerRunnable
from autoqa.core.config import settings


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
    # Backwards-compat: existing callers reference app.state.service for the RTM service.
    app.state.service = app.state.rtm_service
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="AutoQA Reviewer API",
        version="0.2.0",
        lifespan=lifespan,
    )
    app.include_router(router)
    return app


app = create_app()
