import asyncio
import logging
import time
from typing import List, Optional

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver

from autoqa.api.schemas import (
    HazardBatchReviewResponse,
    HazardReviewFromExcelRequest,
    HazardReviewRequest,
    HazardReviewResponse,
    ReviewRequest,
    ReviewResponse,
    TestCaseReviewRequest,
    TestCaseReviewResponse,
)
from autoqa.components.clients import RateLimitOpenAIClient
from autoqa.components.hazard_risk_reviewer.core import HazardRowWithTraceMatrix
from autoqa.components.shared.data_integration import (
    PyJamaNodeConfig,
    transform_hazard_record_to_state,
)
from autoqa.components.hazard_risk_reviewer.pipeline import HazardReviewerRunnable
from autoqa.components.test_suite_reviewer.pipeline import RTMReviewerRunnable
from autoqa.components.test_case_reviewer.pipeline import TCReviewerRunnable
from autoqa.components.test_case_reviewer.nodes import load_default_review_objectives
from autoqa.prj_logger import format_elapsed_time


class RTMReviewService:
    """
    Wraps the compiled LangGraph RTM pipeline for use by the FastAPI layer.
    Instantiated once at application startup and stored on app.state.

    Accepts an optional pre-built RTMReviewerRunnable so a single compiled
    graph can be shared with HazardReviewService at lifespan time.
    
    Supports both local data input and JAMA baseline fetching via pyjama_config.
    """

    def __init__(
        self,
        client: RateLimitOpenAIClient,
        model: str,
        model_kwargs: dict = {},
        rtm_runnable: Optional[RTMReviewerRunnable] = None,
        pyjama_config: Optional[PyJamaNodeConfig] = None,
    ):
        self.graph = rtm_runnable or RTMReviewerRunnable(
            client, model, model_kwargs, checkpointer=MemorySaver(), pyjama_config=pyjama_config
        )

    async def run(self, request: ReviewRequest) -> ReviewResponse:
        logger = logging.getLogger("autoqa.api.rtm")
        start_time = time.perf_counter()
        
        config: RunnableConfig = {"configurable": {"thread_id": request.thread_id}}
        graph_input = {
            "requirement": request.requirement,
            "test_cases": request.test_cases,
            "design_docs": request.design_docs or [],
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
            design_docs=request.design_docs or [],
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

    async def run_from_excel(
        self, request: HazardReviewFromExcelRequest
    ) -> HazardBatchReviewResponse:
        """
        Batch hazard review from Excel + JAMA traceability files.
        
        Workflow:
        1. Transform Excel rows with JAMA traceability using transform_hazard_record_to_state()
           - Parses Excel to extract hazard rows and control references
           - Loads unified JAMA response JSONL with bidirectional traceability
           - Merges each row with filtered traceability to create HazardRowWithTraceMatrix
           - Writes enhanced inputs to JSONL for inspection
        2. Invoke graph concurrently for each enhanced hazard row
           - Creates thread_id from prefix + hazard_id
           - Builds HazardReviewRequest with fully-traced hazard
           - Invokes graph via asyncio.gather() for parallel processing
        3. Aggregate and return results as batch response
        
        Args:
            request: HazardReviewFromExcelRequest with Excel path, JAMA JSONL path, and prefix
        
        Returns:
            HazardBatchReviewResponse with per-hazard review results
        """
        logger = logging.getLogger("autoqa.api.hazard")
        start_time = time.perf_counter()
        
        logger.info(
            "Starting batch hazard review from Excel: file_path=%s, pyjama_response_file_path=%s, "
            "thread_id_prefix=%s",
            request.file_path,
            request.pyjama_response_file_path,
            request.thread_id_prefix,
        )
        
        # Step 1: Transform Excel + Pyjama into enhanced HazardRowWithTraceMatrix list
        logger.info("[Step 1] Transforming Excel rows with JAMA traceability")
        try:
            enhanced_rows: List[HazardRowWithTraceMatrix] = transform_hazard_record_to_state(
                excel_file_path=request.file_path,
                pyjama_response_file_path=request.pyjama_response_file_path,
                output_jsonl_path="inputs.jsonl",
            )
            logger.info("[Step 1] Transformation complete: %d enhanced rows", len(enhanced_rows))
        except Exception as e:
            logger.error("[Step 1] Transformation failed: %s", str(e), exc_info=True)
            raise
        
        # Step 2: Define async worker to invoke graph for a single row
        async def invoke_row(
            row: HazardRowWithTraceMatrix, index: int
        ) -> HazardReviewResponse:
            """Invoke the graph for a single hazard row."""
            # Build thread_id from prefix + hazard_id, fallback to index if hazard_id missing
            thread_id = (
                f"{request.thread_id_prefix}-{row.hazard_id}"
                if row.hazard_id
                else f"{request.thread_id_prefix}-{index}"
            )
            
            # Create request with enhanced row
            review_request = HazardReviewRequest(
                thread_id=thread_id,
                hazard=row,
            )
            
            # Invoke graph via existing run() method
            return await self.run(review_request)
        
        # Step 3: Invoke graph concurrently for all rows
        logger.info("[Step 2] Invoking graph concurrently for %d rows", len(enhanced_rows))
        try:
            results: List[HazardReviewResponse] = await asyncio.gather(
                *[invoke_row(row, i) for i, row in enumerate(enhanced_rows)],
                return_exceptions=False,
            )
            logger.info("[Step 2] Graph invocation complete: %d results collected", len(results))
        except Exception as e:
            logger.error("[Step 2] Graph invocation failed: %s", str(e), exc_info=True)
            raise
        
        # Step 4: Build and return batch response
        end_time = time.perf_counter()
        elapsed = end_time - start_time
        elapsed_str = format_elapsed_time(elapsed)
        
        logger.info(
            "Batch hazard review from Excel completed in %s: "
            "%d rows processed, %d results returned",
            elapsed_str,
            len(enhanced_rows),
            len(results),
        )
        
        return HazardBatchReviewResponse(
            status="completed",
            thread_id_prefix=request.thread_id_prefix,
            total=len(results),
            results=results,
        )


class TestCaseReviewService:
    """
    Wraps the compiled LangGraph test_case_reviewer pipeline for use by the FastAPI layer.
    Instantiated once at application startup and stored on app.state.
    
    Supports both local data input and JAMA baseline fetching via pyjama_config.
    """

    def __init__(
        self,
        client: RateLimitOpenAIClient,
        model: str,
        model_kwargs: dict = {},
        pyjama_config: Optional[PyJamaNodeConfig] = None,
    ):
        self.graph = TCReviewerRunnable(
            client, model, model_kwargs, checkpointer=MemorySaver(), pyjama_config=pyjama_config
        )

    async def run(self, request: TestCaseReviewRequest) -> TestCaseReviewResponse:
        logger = logging.getLogger("autoqa.api.test_case")
        start_time = time.perf_counter()
        
        config: RunnableConfig = {"configurable": {"thread_id": request.thread_id}}
        
        # Load default review objectives if not provided
        review_objectives = request.review_objectives
        if review_objectives is None:
            review_objectives = load_default_review_objectives()
        
        graph_input = {
            "test_case": request.test_case,
            "requirements": request.requirements,
            "review_objectives": review_objectives,
            "design_docs": request.design_docs or [],
        }
        final_state = await self.graph.graph.ainvoke(graph_input, config)
        
        end_time = time.perf_counter()
        elapsed = end_time - start_time
        elapsed_str = format_elapsed_time(elapsed)
        
        logger.info(
            f"Test case review graph invocation for thread {request.thread_id} "
            f"completed in {elapsed_str}"
        )
        
        return TestCaseReviewResponse(
            status="completed",
            thread_id=request.thread_id,
            test_case=request.test_case,
            requirements=request.requirements,
            decomposed_requirements=final_state.get("decomposed_requirements"),
            coverage_analysis=final_state.get("coverage_analysis", []),
            logical_structure_analysis=final_state.get("logical_structure_analysis"),
            prereqs_analysis=final_state.get("prereqs_analysis"),
            aggregated_assessment=final_state.get("aggregated_assessment"),
            design_docs=request.design_docs or [],
        )
