import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import List, Optional

from langgraph.checkpoint.memory import MemorySaver

from autoqa.components.clients import RateLimitOpenAIClient
from autoqa.core.cache import ReviewCacheManager
from autoqa.components.hazard_risk_reviewer.core import HazardRowWithTraceMatrix
from autoqa.components.shared.data_integration import PyJamaNodeConfig
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
    """

    _logger = logging.getLogger("autoqa.api.rtm")

    def __init__(
        self,
        client: RateLimitOpenAIClient,
        model: str,
        model_kwargs: dict = {},
        rtm_runnable: Optional[RTMReviewerRunnable] = None,
        pyjama_config: Optional[PyJamaNodeConfig] = None,
        cache_manager: Optional["ReviewCacheManager"] = None,
    ):
        self.pyjama_config = pyjama_config
        self.graph = rtm_runnable or RTMReviewerRunnable(
            client, model, model_kwargs, checkpointer=MemorySaver(),
            pyjama_config=pyjama_config, cache_manager=cache_manager,
        )

    async def run_from_baseline(
        self, baseline_id: str, thread_id_prefix: str, cache_mode: str = "partial"
    ) -> str:
        """Fetch a JAMA baseline, run the RTM graph for every requirement, return viewer.html path."""
        from autoqa.components.shared.data_integration import (
            DataIntegrationNode,
            PyJamaRequest,
            PYJAMA_AVAILABLE,
            transform_test_suite_review_to_state,
        )
        from autoqa.viewer.generator import write_viewer
        from autoqa.core.config import settings

        if not PYJAMA_AVAILABLE:
            raise ValueError("PyJama is not installed — JAMA baseline fetching unavailable.")

        self._logger.info("Starting batch RTM review for baseline %s", baseline_id)

        node = DataIntegrationNode(pyjama_config=self.pyjama_config)
        result = await node({
            "pyjama_request": PyJamaRequest(
                baseline_id=baseline_id,
                request_type="test_suite_review",
            )
        })
        jama_data = result.get("jama_data", [])
        if not jama_data:
            raise ValueError(f"No data returned from JAMA for baseline '{baseline_id}'")

        state_dicts = transform_test_suite_review_to_state(jama_data)
        self._logger.info("Baseline %s: %d requirements to review", baseline_id, len(state_dicts))

        run_dir = Path(settings.log_file_path).parent
        outputs_path = run_dir / "outputs.jsonl"
        outputs_path.write_text("", encoding="utf-8")

        start = time.perf_counter()
        for i, state_dict in enumerate(state_dicts):
            thread_id = f"{thread_id_prefix}-{i:03d}"
            config = {"configurable": {"thread_id": thread_id}}
            final_state = await self.graph.graph.ainvoke(
                {**state_dict, "cache_mode": cache_mode}, config
            )
            self._logger.info("[%d/%d] Completed requirement review", i + 1, len(state_dicts))
            with outputs_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(final_state, default=_json_default) + "\n")

        viewer_path = write_viewer(outputs_path)
        if viewer_path is None:
            raise ValueError("Baseline review produced no output records")

        elapsed = time.perf_counter() - start
        self._logger.info(
            "Batch RTM review complete: %d requirements in %.1fs, viewer at %s",
            len(state_dicts), elapsed, viewer_path,
        )
        return str(viewer_path)


class HazardReviewService:
    """
    Wraps the compiled hazard review pipeline for use by the FastAPI layer.
    Instantiated once at application startup and stored on app.state.

    The RTMReviewerRunnable is passed through to HazardReviewerRunnable so the
    inner RTM subgraph (used per-requirement) is built only once across both services.
    """

    _logger = logging.getLogger("autoqa.api.hazard")

    def __init__(
        self,
        client: RateLimitOpenAIClient,
        model: str,
        model_kwargs: dict = {},
        rtm_runnable: Optional[RTMReviewerRunnable] = None,
        pyjama_config: Optional[PyJamaNodeConfig] = None,
        cache_manager: Optional["ReviewCacheManager"] = None,
    ):
        self.pyjama_config = pyjama_config
        # NOTE: we deliberately do NOT reuse a cache-enabled RTM runnable here.
        # The embedded test-suite subgraph must stay internally uncached — the
        # whole-subgraph result is cached as one blob per requirement by
        # RequirementReviewerNode (see ReviewCacheManager / "full" subgraph
        # caching). HazardReviewerRunnable builds its own uncached RTM when
        # rtm_runnable is None. The shared cache_manager still caches the
        # hazard's own H1-H7 / summarizer / req-blob nodes.
        self.graph = HazardReviewerRunnable(
            client,
            model,
            model_kwargs,
            checkpointer=MemorySaver(),
            rtm_runnable=rtm_runnable,
            cache_manager=cache_manager,
        )

    def _parse_uploaded_excel(
        self, file_bytes: bytes, filename: str, sheet_name: str
    ) -> List[HazardRowWithTraceMatrix]:
        from autoqa.components.hazard_risk_reviewer.loader import parse_sha_excel
        from autoqa.components.hazard_risk_reviewer.core import HazardTraceMatrix

        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
            tmp.write(file_bytes)
            tmp_path = tmp.name

        try:
            rows = parse_sha_excel(tmp_path, sheet_name=sheet_name).rows
            if not rows:
                raise ValueError(f"No hazard rows found in sheet '{sheet_name}' of '{filename}'")
            self._logger.info("Found %d hazard rows in %s", len(rows), filename)
            return [
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
        finally:
            os.unlink(tmp_path)

    async def run_from_excel_upload(
        self,
        file_bytes: bytes,
        filename: str,
        project_name: str,
        thread_id_prefix: str,
        sheet_name: str = "SHA Table",
        cache_mode: str = "partial",
    ) -> str:
        """Parse an uploaded SHA Excel file and run the hazard graph for every row.

        Runs with Excel-derived data only (no JAMA traceability).
        """
        from autoqa.viewer.generator import write_viewer_hz
        from autoqa.core.config import settings

        self._logger.info("Starting upload hazard review: %s (project=%s)", filename, project_name)
        hazard_rows = self._parse_uploaded_excel(file_bytes, filename, sheet_name)

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
            final_state = await self.graph.graph.ainvoke(
                {"hazard": hazard_row, "cache_mode": cache_mode}, config
            )
            self._logger.info(
                "[%d/%d] Hazard review complete for %s", i + 1, len(hazard_rows), hazard_row.hazard_id
            )
            with outputs_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(final_state, default=_json_default) + "\n")

        viewer_path = write_viewer_hz(outputs_path)
        if viewer_path is None:
            raise ValueError("Hazard upload review produced no output records")

        elapsed = time.perf_counter() - start
        self._logger.info(
            "Hazard upload review complete: %d rows in %.1fs, viewer at %s",
            len(hazard_rows), elapsed, viewer_path,
        )
        return str(viewer_path)


class TestCaseReviewService:
    """
    Wraps the compiled LangGraph test_case_reviewer pipeline for use by the FastAPI layer.
    Instantiated once at application startup and stored on app.state.
    """

    _logger = logging.getLogger("autoqa.api.test_case")

    def __init__(
        self,
        client: RateLimitOpenAIClient,
        model: str,
        model_kwargs: dict = {},
        pyjama_config: Optional[PyJamaNodeConfig] = None,
        cache_manager: Optional["ReviewCacheManager"] = None,
    ):
        self.pyjama_config = pyjama_config
        self.graph = TCReviewerRunnable(
            client, model, model_kwargs, checkpointer=MemorySaver(),
            pyjama_config=pyjama_config, cache_manager=cache_manager,
        )

    async def run_from_baseline(
        self, baseline_id: str, thread_id_prefix: str, cache_mode: str = "partial"
    ) -> str:
        """Fetch a JAMA baseline, run the TC graph for every test case, return viewer_tc.html path."""
        from autoqa.components.shared.data_integration import (
            DataIntegrationNode,
            PyJamaRequest,
            PYJAMA_AVAILABLE,
            transform_test_case_review_to_state,
        )
        from autoqa.viewer.generator import write_viewer_tc
        from autoqa.core.config import settings

        if not PYJAMA_AVAILABLE:
            raise ValueError("PyJama is not installed — JAMA baseline fetching unavailable.")

        self._logger.info("Starting batch TC review for baseline %s", baseline_id)

        node = DataIntegrationNode(pyjama_config=self.pyjama_config)
        result = await node({
            "pyjama_request": PyJamaRequest(
                baseline_id=baseline_id,
                request_type="test_case_review",
            )
        })
        jama_data = result.get("jama_data", [])
        if not jama_data:
            raise ValueError(f"No data returned from JAMA for baseline '{baseline_id}'")

        state_dicts = transform_test_case_review_to_state(jama_data)
        self._logger.info("Baseline %s: %d test cases to review", baseline_id, len(state_dicts))

        default_objectives = load_default_review_objectives()
        run_dir = Path(settings.log_file_path).parent
        outputs_path = run_dir / "outputs.jsonl"
        outputs_path.write_text("", encoding="utf-8")

        start = time.perf_counter()
        for i, state_dict in enumerate(state_dicts):
            thread_id = f"{thread_id_prefix}-{i:03d}"
            graph_input = {
                **state_dict,
                "review_objectives": state_dict.get("review_objectives") or default_objectives,
                "design_docs": state_dict.get("design_docs") or [],
                "cache_mode": cache_mode,
            }
            config = {"configurable": {"thread_id": thread_id}}
            final_state = await self.graph.graph.ainvoke(graph_input, config)
            self._logger.info("[%d/%d] Completed test case review", i + 1, len(state_dicts))
            with outputs_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(final_state, default=_json_default) + "\n")

        viewer_path = write_viewer_tc(outputs_path)
        if viewer_path is None:
            raise ValueError("Baseline TC review produced no output records")

        elapsed = time.perf_counter() - start
        self._logger.info(
            "Batch TC review complete: %d test cases in %.1fs, viewer at %s",
            len(state_dicts), elapsed, viewer_path,
        )
        return str(viewer_path)
