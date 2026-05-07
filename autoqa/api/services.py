import logging
import time
from typing import Optional

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver

from autoqa.api.schemas import (
    HazardReviewRequest,
    HazardReviewResponse,
    ReviewRequest,
    ReviewResponse,
)
from autoqa.components.clients import RateLimitOpenAIClient
from autoqa.components.hazard_risk_reviewer.pipeline import HazardReviewerRunnable
from autoqa.components.test_suite_reviewer.pipeline import RTMReviewerRunnable
from autoqa.prj_logger import format_elapsed_time


class RTMReviewService:
    """
    Wraps the compiled LangGraph RTM pipeline for use by the FastAPI layer.
    Instantiated once at application startup and stored on app.state.

    Accepts an optional pre-built RTMReviewerRunnable so a single compiled
    graph can be shared with HazardReviewService at lifespan time.
    """

    def __init__(
        self,
        client: RateLimitOpenAIClient,
        model: str,
        model_kwargs: dict = {},
        rtm_runnable: Optional[RTMReviewerRunnable] = None,
    ):
        self.graph = rtm_runnable or RTMReviewerRunnable(
            client, model, model_kwargs, checkpointer=MemorySaver()
        )

    async def run(self, request: ReviewRequest) -> ReviewResponse:
        logger = logging.getLogger("autoqa.api.rtm")
        start_time = time.perf_counter()
        
        config: RunnableConfig = {"configurable": {"thread_id": request.thread_id}}
        graph_input = {
            "requirement": request.requirement,
            "test_cases": request.test_cases,
        }
        final_state = await self.graph.graph.ainvoke(graph_input, config)
        
        end_time = time.perf_counter()
        elapsed = end_time - start_time
        elapsed_str = format_elapsed_time(elapsed)
        
        logger.info(f"RTM review graph invocation for thread {request.thread_id} completed in {elapsed_str}")
        
        return ReviewResponse(
            status="completed",
            thread_id=request.thread_id,
            coverage_analysis=final_state.get("coverage_analysis", []),
            decomposed_requirement=final_state.get("decomposed_requirement"),
            test_suite=final_state.get("test_suite"),
            synthesized_assessment=final_state.get("synthesized_assessment"),
        )


class HazardReviewService:
    """
    Wraps the compiled hazard review pipeline for use by the FastAPI layer.
    Instantiated once at application startup and stored on app.state.

    Accepts an optional shared RTMReviewerRunnable so the inner test_suite_reviewer
    subgraph (used by RequirementReviewerNode) is built once across both services.
    """

    def __init__(
        self,
        client: RateLimitOpenAIClient,
        model: str,
        model_kwargs: dict = {},
        rtm_runnable: Optional[RTMReviewerRunnable] = None,
    ):
        self.graph = HazardReviewerRunnable(
            client,
            model,
            model_kwargs,
            checkpointer=MemorySaver(),
            rtm_runnable=rtm_runnable,
        )

    async def run(self, request: HazardReviewRequest) -> HazardReviewResponse:
        logger = logging.getLogger("autoqa.api.hazard")
        start_time = time.perf_counter()
        
        config: RunnableConfig = {"configurable": {"thread_id": request.thread_id}}
        final_state = await self.graph.graph.ainvoke({"hazard": request.hazard}, config)
        
        end_time = time.perf_counter()
        elapsed = end_time - start_time
        elapsed_str = format_elapsed_time(elapsed)
        
        logger.info(f"Hazard review graph invocation for thread {request.thread_id} completed in {elapsed_str}")
        
        return HazardReviewResponse(
            status="completed",
            thread_id=request.thread_id,
            hazard=request.hazard,
            hazard_assessment=final_state.get("hazard_assessment"),
            requirement_reviews=final_state.get("requirement_reviews", []),
        )
