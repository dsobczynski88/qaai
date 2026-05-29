import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import List, Optional

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver

from autoqa.api.schemas import (
    HazardBatchReviewResponse,
    HazardReviewFromExcelRequest,
    HazardReviewRequest,
    HazardReviewResponse,
    ReviewFromBaselineRequest,
    ReviewRequest,
    ReviewResponse,
    TestCaseReviewFromBaselineRequest,
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


def _json_default(obj):
    """Fallback JSON serializer for Pydantic models in LangGraph state dicts."""
    if hasattr(obj, 'model_dump'):
        return obj.model_dump()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


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
        self.pyjama_config = pyjama_config
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


    async def run_from_baseline(self, request: ReviewFromBaselineRequest) -> str:
        """Fetch a JAMA baseline, run the RTM graph for every requirement, return viewer.html path."""
        from autoqa.components.shared.data_integration import (
            DataIntegrationNode,
            PyJamaRequest,
            PYJAMA_AVAILABLE,
            transform_test_suite_review_to_state,
        )
        from autoqa.viewer.generator import write_viewer
        from autoqa.core.config import settings

        logger = logging.getLogger("autoqa.api.rtm")

        if not PYJAMA_AVAILABLE:
            raise ValueError("PyJama is not installed — JAMA baseline fetching unavailable.")

        logger.info("Starting batch RTM review for baseline %s", request.baseline_id)

        node = DataIntegrationNode(pyjama_config=self.pyjama_config)
        result = await node({
            "pyjama_request": PyJamaRequest(
                baseline_id=request.baseline_id,
                request_type="test_suite_review",
            )
        })
        jama_data = result.get("jama_data", [])
        if not jama_data:
            raise ValueError(f"No data returned from JAMA for baseline '{request.baseline_id}'")

        state_dicts = transform_test_suite_review_to_state(jama_data)
        logger.info("Baseline %s: %d requirements to review", request.baseline_id, len(state_dicts))

        run_dir = Path(settings.log_file_path).parent
        outputs_path = run_dir / "outputs.jsonl"
        outputs_path.write_text("", encoding="utf-8")

        start = time.perf_counter()
        for i, state_dict in enumerate(state_dicts):
            thread_id = f"{request.thread_id_prefix}-{i:03d}"
            config = {"configurable": {"thread_id": thread_id}}
            final_state = await self.graph.graph.ainvoke(state_dict, config)
            logger.info("[%d/%d] Completed requirement review", i + 1, len(state_dicts))
            with outputs_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(final_state, default=_json_default) + "\n")

        viewer_path = write_viewer(outputs_path)
        if viewer_path is None:
            raise ValueError("Baseline review produced no output records")

        elapsed = time.perf_counter() - start
        logger.info(
            "Batch RTM review complete: %d requirements in %.1fs, viewer at %s",
            len(state_dicts), elapsed, viewer_path,
        )
        return str(viewer_path)


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
        pyjama_config: Optional[PyJamaNodeConfig] = None,
    ):
        self.pyjama_config = pyjama_config
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
        2. Invoke graph sequentially for each enhanced hazard row
           - Creates thread_id from prefix + hazard_id
           - Builds HazardReviewRequest with fully-traced hazard
           - Invokes graphs one at a time so shared requirement reviews are
             served from the req_id-keyed cache instead of recomputed
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
        
        # Step 3: Invoke graph sequentially so each hazard's requirement reviews
        # populate the req_id-keyed cache before the next hazard runs. The
        # RequirementReviewerNode dedup ("once per unique requirement per run",
        # nodes.py:406-409) only holds when hazards are serialized — under
        # asyncio.gather every hazard sharing a requirement misses the cache and
        # recomputes the non-deterministic RTM subgraph, diverging H4/H5/H6.
        logger.info("[Step 2] Invoking graph sequentially for %d rows", len(enhanced_rows))
        try:
            results: List[HazardReviewResponse] = []
            for i, row in enumerate(enhanced_rows):
                results.append(await invoke_row(row, i))
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


    async def run_from_excel_upload(
        self,
        file_bytes: bytes,
        filename: str,
        project_name: str,
        thread_id_prefix: str,
        sheet_name: str = "SHA Table",
    ) -> str:
        """Parse an uploaded SHA Excel file and run the hazard graph for every row.

        JAMA traceability is not fetched in this path — hazard rows run with the
        Excel-derived data only (H1/H2/H3/H7 rubric). For full H1-H7 analysis
        including H4/H5, use /hazard-review/from-excel with a pre-fetched JAMA JSONL.
        """
        from autoqa.components.hazard_risk_reviewer.loader import parse_sha_excel
        from autoqa.components.hazard_risk_reviewer.core import HazardTraceMatrix, HazardRowWithTraceMatrix
        from autoqa.viewer.generator import write_viewer_hz
        from autoqa.core.config import settings

        logger = logging.getLogger("autoqa.api.hazard")

        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
            tmp.write(file_bytes)
            tmp_path = tmp.name

        try:
            logger.info("Parsing uploaded SHA Excel: %s (project=%s)", filename, project_name)
            excel_results = parse_sha_excel(tmp_path, sheet_name=sheet_name)
            rows = excel_results.rows

            if not rows:
                raise ValueError(
                    f"No hazard rows found in sheet '{sheet_name}' of '{filename}'"
                )
            logger.info("Found %d hazard rows in %s", len(rows), filename)

            hazard_rows: List[HazardRowWithTraceMatrix] = [
                HazardRowWithTraceMatrix(
                    **row.model_dump(),
                    requirements_traceability=HazardTraceMatrix(
                        requirements=[],
                        test_cases=[],
                        design_docs=[],
                        user_needs=[],
                        system_requirements=[],
                    ),
                )
                for row in rows
            ]

            run_dir = Path(settings.log_file_path).parent
            outputs_path = run_dir / "outputs.jsonl"
            outputs_path.write_text("", encoding="utf-8")

            start = time.perf_counter()
            for i, hazard_row in enumerate(hazard_rows):
                thread_id = (
                    f"{thread_id_prefix}-{hazard_row.hazard_id}"
                    if hazard_row.hazard_id
                    else f"{thread_id_prefix}-{i:03d}"
                )
                config = {"configurable": {"thread_id": thread_id}}
                final_state = await self.graph.graph.ainvoke({"hazard": hazard_row}, config)
                logger.info("[%d/%d] Hazard review complete for %s", i + 1, len(hazard_rows), hazard_row.hazard_id)
                with outputs_path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(final_state, default=_json_default) + "\n")

            viewer_path = write_viewer_hz(outputs_path)
            if viewer_path is None:
                raise ValueError("Hazard upload review produced no output records")

            elapsed = time.perf_counter() - start
            logger.info(
                "Hazard upload review complete: %d rows in %.1fs, viewer at %s",
                len(hazard_rows), elapsed, viewer_path,
            )
            return str(viewer_path)

        finally:
            os.unlink(tmp_path)


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
        self.pyjama_config = pyjama_config
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

    async def run_from_baseline(self, request: TestCaseReviewFromBaselineRequest) -> str:
        """Fetch a JAMA baseline, run the TC graph for every test case, return viewer_tc.html path."""
        from autoqa.components.shared.data_integration import (
            DataIntegrationNode,
            PyJamaRequest,
            PYJAMA_AVAILABLE,
            transform_test_case_review_to_state,
        )
        from autoqa.viewer.generator import write_viewer_tc
        from autoqa.core.config import settings

        logger = logging.getLogger("autoqa.api.test_case")

        if not PYJAMA_AVAILABLE:
            raise ValueError("PyJama is not installed — JAMA baseline fetching unavailable.")

        logger.info("Starting batch TC review for baseline %s", request.baseline_id)

        node = DataIntegrationNode(pyjama_config=self.pyjama_config)
        result = await node({
            "pyjama_request": PyJamaRequest(
                baseline_id=request.baseline_id,
                request_type="test_case_review",
            )
        })
        jama_data = result.get("jama_data", [])
        if not jama_data:
            raise ValueError(f"No data returned from JAMA for baseline '{request.baseline_id}'")

        state_dicts = transform_test_case_review_to_state(jama_data)
        logger.info("Baseline %s: %d test cases to review", request.baseline_id, len(state_dicts))

        default_objectives = load_default_review_objectives()

        run_dir = Path(settings.log_file_path).parent
        outputs_path = run_dir / "outputs.jsonl"
        outputs_path.write_text("", encoding="utf-8")

        start = time.perf_counter()
        for i, state_dict in enumerate(state_dicts):
            thread_id = f"{request.thread_id_prefix}-{i:03d}"
            graph_input = {
                **state_dict,
                "review_objectives": state_dict.get("review_objectives") or default_objectives,
                "design_docs": state_dict.get("design_docs") or [],
            }
            config = {"configurable": {"thread_id": thread_id}}
            final_state = await self.graph.graph.ainvoke(graph_input, config)
            logger.info("[%d/%d] Completed test case review", i + 1, len(state_dicts))
            with outputs_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(final_state, default=_json_default) + "\n")

        viewer_path = write_viewer_tc(outputs_path)
        if viewer_path is None:
            raise ValueError("Baseline TC review produced no output records")

        elapsed = time.perf_counter() - start
        logger.info(
            "Batch TC review complete: %d test cases in %.1fs, viewer at %s",
            len(state_dicts), elapsed, viewer_path,
        )
        return str(viewer_path)
