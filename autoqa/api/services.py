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
        self, baseline_id: str, thread_id_prefix: str, cache_mode: str = "partial",
        test_mode: Optional[bool] = None,
    ) -> str:
        """Fetch a JAMA baseline, run the RTM graph for every requirement, return viewer.html path.

        test_mode (None ⇒ use the config's default) runs the JAMA fetch cache-only
        with no live API calls when True.
        """
        from autoqa.components.shared.data_integration import (
            DataIntegrationNode,
            PyJamaRequest,
            PYJAMA_AVAILABLE,
            transform_test_suite_review_to_state,
        )
        from autoqa.viewer.generator import write_viewer
        from autoqa.core.logging_config import start_new_run

        if not PYJAMA_AVAILABLE:
            raise ValueError("PyJama is not installed — JAMA baseline fetching unavailable.")

        # Fresh run folder for THIS review, before the JAMA fetch so pyjama logs land here.
        run_dir = start_new_run()
        self.graph.write_graph_png(run_dir)

        self._logger.info(
            "Starting batch RTM review for baseline %s (test_mode=%s)", baseline_id, test_mode
        )

        cfg = self.pyjama_config
        if cfg is not None and test_mode is not None:
            cfg = cfg.model_copy(update={"test_mode": test_mode})
        node = DataIntegrationNode(pyjama_config=cfg)
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

        inputs_path = run_dir / "inputs.jsonl"
        with inputs_path.open("w", encoding="utf-8") as f:
            for state_dict in state_dicts:
                f.write(json.dumps(state_dict, default=_json_default) + "\n")

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
            pyjama_config=pyjama_config,
        )

    @staticmethod
    def _build_bidirectional_request(project_name: str, identifiers: List[str]):
        """Build a forward-looking bidirectional_trace PyJamaRequest.

        Aligns the hazard reviewer with the pyjama bidirectional_trace example:
        the hazard row's control references become the JAMA identifiers, and the
        graph's data_integration + transform nodes fetch and merge the
        per-requirement traceability onto the hazard.

        The installed pyjama 1.0.0 does NOT expose request_type
        "bidirectional_trace" (its PyJamaRequest Literal only allows
        test_suite_review / test_case_review / hierarchical_trace), so this is
        capability-gated: it raises a clear error until pyjama is upgraded. It is
        never reached from the default Excel flow (run_from_excel_upload).
        """
        from autoqa.components.shared.data_integration import PyJamaRequest, PYJAMA_AVAILABLE

        if not PYJAMA_AVAILABLE or PyJamaRequest is None:
            raise ValueError("PyJama is not installed — bidirectional_trace fetch unavailable.")
        try:
            return PyJamaRequest(
                request_type="bidirectional_trace",
                project_name=project_name,
                identifiers=identifiers,
            )
        except Exception as e:  # ValidationError on installed pyjama 1.0.0
            raise ValueError(
                "request_type='bidirectional_trace' is not supported by the installed "
                "pyjama version. Upgrade pyjama-fastapi to a release that exposes "
                f"bidirectional_trace to enable JAMA-sourced hazard traceability. ({e})"
            ) from e

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
        test_mode: Optional[bool] = None,
    ) -> str:
        """Parse an uploaded SHA Excel file and run the hazard graph for every row.

        Per row, the GIDs extracted into ``row_specific_controls_references`` plus
        the project name drive a JAMA ``bidirectional_trace`` fetch, which the
        graph's data_integration + transform nodes merge onto the hazard's
        ``requirements_traceability``. Rows with no GIDs fall back to the
        Excel-only (empty traceability) path. ``test_mode`` (None ⇒ config
        default) runs that fetch cache-only with no live JAMA API calls.
        """
        from autoqa.viewer.generator import write_viewer_hz
        from autoqa.core.logging_config import start_new_run
        from autoqa.components.shared.data_integration import PYJAMA_AVAILABLE

        # Fresh run folder for THIS review, before the JAMA fetch so pyjama logs land here.
        run_dir = start_new_run()
        self.graph.write_graph_png(run_dir)

        self._logger.info(
            "Starting upload hazard review: %s (project=%s, test_mode=%s)",
            filename, project_name, test_mode,
        )
        hazard_rows = self._parse_uploaded_excel(file_bytes, filename, sheet_name)

        inputs_path = run_dir / "inputs.jsonl"
        with inputs_path.open("w", encoding="utf-8") as f:
            for hazard_row in hazard_rows:
                f.write(json.dumps(hazard_row, default=_json_default) + "\n")

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

            graph_input = {"hazard": hazard_row, "cache_mode": cache_mode}
            if test_mode is not None:
                graph_input["pyjama_test_mode"] = test_mode

            # Build a JAMA bidirectional_trace request from this row's GIDs so the
            # graph fetches and merges traceability. Skip when PyJama is missing or
            # the row has no control references (Excel-only fallback for that row).
            identifiers = hazard_row.row_specific_controls_references or []
            if PYJAMA_AVAILABLE and project_name and identifiers:
                graph_input["pyjama_request"] = self._build_bidirectional_request(
                    project_name, identifiers
                )
                self._logger.info(
                    "Hazard %s: bidirectional_trace fetch for %d identifiers",
                    hazard_row.hazard_id, len(identifiers),
                )

            final_state = await self.graph.graph.ainvoke(graph_input, config)
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
        self, baseline_id: str, thread_id_prefix: str, cache_mode: str = "partial",
        test_mode: Optional[bool] = None,
    ) -> str:
        """Fetch a JAMA baseline, run the TC graph for every test case, return viewer_tc.html path.

        test_mode (None ⇒ use the config's default) runs the JAMA fetch cache-only
        with no live API calls when True.
        """
        from autoqa.components.shared.data_integration import (
            DataIntegrationNode,
            PyJamaRequest,
            PYJAMA_AVAILABLE,
            transform_test_case_review_to_state,
        )
        from autoqa.viewer.generator import write_viewer_tc
        from autoqa.core.logging_config import start_new_run

        if not PYJAMA_AVAILABLE:
            raise ValueError("PyJama is not installed — JAMA baseline fetching unavailable.")

        # Fresh run folder for THIS review, before the JAMA fetch so pyjama logs land here.
        run_dir = start_new_run()
        self.graph.write_graph_png(run_dir)

        self._logger.info(
            "Starting batch TC review for baseline %s (test_mode=%s)", baseline_id, test_mode
        )

        cfg = self.pyjama_config
        if cfg is not None and test_mode is not None:
            cfg = cfg.model_copy(update={"test_mode": test_mode})
        node = DataIntegrationNode(pyjama_config=cfg)
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

        inputs_path = run_dir / "inputs.jsonl"
        with inputs_path.open("w", encoding="utf-8") as f:
            for state_dict in state_dicts:
                f.write(json.dumps(state_dict, default=_json_default) + "\n")

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
