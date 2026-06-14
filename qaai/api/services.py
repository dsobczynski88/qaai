import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Optional

from langgraph.checkpoint.memory import MemorySaver

from qaai.agents.clients import RateLimitOpenAIClient
from qaai.core.cache import ReviewCacheManager
from qaai.agents.hazard_risk_reviewer.core import HazardRowWithTraceMatrix
from qaai.agents.shared.data_integration import PyJamaNodeConfig
from qaai.agents.hazard_risk_reviewer.pipeline import HazardReviewerRunnable
from qaai.agents.test_suite_reviewer.pipeline import RTMReviewerRunnable
from qaai.agents.test_case_reviewer.pipeline import TCReviewerRunnable
from qaai.agents.test_case_reviewer.nodes import load_default_review_objectives
from qaai.core.config import PromptConfig
from qaai.core.constants import INPUT_JSONL_FILENAME, OUTPUT_JSONL_FILENAME


# Prompt sets selected by the "Include Edge Case Analysis" toggle. v4 enables the
# edge-case decomposer (v6); v3 is the baseline (decomposer v5). The selection is
# applied to both the test-suite reviewer and the hazard reviewer's embedded RTM.
PROMPT_SET_EDGE_CASE = "test_suite_reviewer_v4"
PROMPT_SET_BASELINE = "test_suite_reviewer_v3"
PROMPT_SETS = (PROMPT_SET_BASELINE, PROMPT_SET_EDGE_CASE)


def resolve_prompt_set(include_edge_case_analysis: bool) -> str:
    """Map the UI/API toggle to a prompt-set name."""
    return PROMPT_SET_EDGE_CASE if include_edge_case_analysis else PROMPT_SET_BASELINE


def _json_default(obj):
    """Fallback JSON serializer for Pydantic models in LangGraph state dicts."""
    if hasattr(obj, 'model_dump'):
        return obj.model_dump()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


async def _run_batch_review(
    *,
    logger: logging.Logger,
    run_dir: Path,
    items: list,
    graph,
    thread_id_fn,
    graph_input_fn,
    viewer_writer,
    item_noun: str,
) -> str:
    """Shared batch loop for the three reviewer services.

    Writes ``inputs.jsonl``, invokes the compiled ``graph`` once per item while
    appending each final state to ``outputs.jsonl``, renders the viewer, and
    returns its path. The per-reviewer differences — thread-id source, graph
    input shape, and which ``write_viewer*`` to call — are injected as callables.
    """
    inputs_path = run_dir / INPUT_JSONL_FILENAME
    with inputs_path.open("w", encoding="utf-8") as f:
        for item in items:
            f.write(json.dumps(item, default=_json_default) + "\n")

    outputs_path = run_dir / OUTPUT_JSONL_FILENAME
    outputs_path.write_text("", encoding="utf-8")

    start = time.perf_counter()
    for i, item in enumerate(items):
        config = {"configurable": {"thread_id": thread_id_fn(i, item)}}
        final_state = await graph.ainvoke(graph_input_fn(i, item), config)
        logger.info("[%d/%d] Completed %s review", i + 1, len(items), item_noun)
        with outputs_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(final_state, default=_json_default) + "\n")

    viewer_path = viewer_writer(outputs_path)
    if viewer_path is None:
        raise ValueError(f"{item_noun} review produced no output records")

    elapsed = time.perf_counter() - start
    logger.info(
        "Batch %s review complete: %d items in %.1fs, viewer at %s",
        item_noun, len(items), elapsed, viewer_path,
    )
    return str(viewer_path)


class RTMReviewService:
    """
    Wraps the compiled LangGraph RTM pipeline for use by the FastAPI layer.
    Instantiated once at application startup and stored on app.state.

    Holds one compiled RTMReviewerRunnable per prompt set (v3 baseline / v4
    edge-case); run_from_baseline picks the graph for the requested set.
    """

    _logger = logging.getLogger("qaai.api.rtm")

    def __init__(
        self,
        client: RateLimitOpenAIClient,
        model: str,
        model_kwargs: dict = {},
        rtm_runnables: Optional[Dict[str, RTMReviewerRunnable]] = None,
        pyjama_config: Optional[PyJamaNodeConfig] = None,
        cache_manager: Optional["ReviewCacheManager"] = None,
    ):
        self.pyjama_config = pyjama_config
        # One cache-enabled runnable per prompt set. Callers (lifespan) can inject
        # pre-built runnables; otherwise build the default v3/v4 pair here.
        self.graphs = rtm_runnables or {
            ps: RTMReviewerRunnable(
                client, model, model_kwargs, checkpointer=MemorySaver(),
                prompt_config=PromptConfig.from_set(ps),
                pyjama_config=pyjama_config, cache_manager=cache_manager,
            )
            for ps in PROMPT_SETS
        }

    def _select(self, prompt_set: Optional[str]) -> RTMReviewerRunnable:
        """Resolve the runnable for a prompt set, defaulting to the baseline set."""
        return (
            self.graphs.get(prompt_set)
            or self.graphs.get(PROMPT_SET_BASELINE)
            or next(iter(self.graphs.values()))
        )

    async def run_from_baseline(
        self, baseline_id: str, thread_id_prefix: str, cache_mode: str = "partial",
        test_mode: Optional[bool] = None, prompt_set: str = PROMPT_SET_BASELINE,
    ) -> str:
        """Fetch a JAMA baseline, run the RTM graph for every requirement, return viewer.html path.

        test_mode (None ⇒ use the config's default) runs the JAMA fetch cache-only
        with no live API calls when True. prompt_set selects the v3/v4 graph.
        """
        from qaai.agents.shared.data_integration import (
            DataIntegrationNode,
            PyJamaRequest,
            PYJAMA_AVAILABLE,
            transform_test_suite_review_to_state,
        )
        from qaai.viewer.generator import write_viewer
        from qaai.core.logging_config import start_new_run

        if not PYJAMA_AVAILABLE:
            raise ValueError("PyJama is not installed — JAMA baseline fetching unavailable.")

        runnable = self._select(prompt_set)
        # Fresh run folder for THIS review, before the JAMA fetch so pyjama logs land here.
        run_dir = start_new_run()
        runnable.write_graph_png(run_dir)
        self._logger.info("RTM review using prompt set '%s'", prompt_set)

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

        return await _run_batch_review(
            logger=self._logger,
            run_dir=run_dir,
            items=state_dicts,
            graph=runnable.graph,
            thread_id_fn=lambda i, _item: f"{thread_id_prefix}-{i:03d}",
            graph_input_fn=lambda _i, state_dict: {**state_dict, "cache_mode": cache_mode},
            viewer_writer=write_viewer,
            item_noun="requirement",
        )


class HazardReviewService:
    """
    Wraps the compiled hazard review pipeline for use by the FastAPI layer.
    Instantiated once at application startup and stored on app.state.

    The RTMReviewerRunnable is passed through to HazardReviewerRunnable so the
    inner RTM subgraph (used per-requirement) is built only once across both services.
    """

    _logger = logging.getLogger("qaai.api.hazard")

    def __init__(
        self,
        client: RateLimitOpenAIClient,
        model: str,
        model_kwargs: dict = {},
        hazard_runnables: Optional[Dict[str, HazardReviewerRunnable]] = None,
        pyjama_config: Optional[PyJamaNodeConfig] = None,
        cache_manager: Optional["ReviewCacheManager"] = None,
    ):
        self.pyjama_config = pyjama_config
        # One hazard graph per prompt set (v3/v4), each embedding its own
        # uncached RTM subgraph built from that set's prompt_config. The
        # embedded test-suite subgraph stays internally uncached — the
        # whole-subgraph result is cached as one blob per requirement by
        # RequirementReviewerNode, namespaced by prompt set. The shared
        # cache_manager still caches the hazard's own H1-H7 / summarizer nodes.
        self.graphs = hazard_runnables or {
            ps: HazardReviewerRunnable(
                client,
                model,
                model_kwargs,
                checkpointer=MemorySaver(),
                prompt_config=PromptConfig.from_set(ps),
                cache_manager=cache_manager,
                pyjama_config=pyjama_config,
            )
            for ps in PROMPT_SETS
        }

    def _select(self, prompt_set: Optional[str]) -> HazardReviewerRunnable:
        """Resolve the hazard runnable for a prompt set, defaulting to baseline."""
        return (
            self.graphs.get(prompt_set)
            or self.graphs.get(PROMPT_SET_BASELINE)
            or next(iter(self.graphs.values()))
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
        from qaai.agents.shared.data_integration import PyJamaRequest, PYJAMA_AVAILABLE

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
        from qaai.agents.hazard_risk_reviewer.loader import parse_sha_excel
        from qaai.agents.hazard_risk_reviewer.core import HazardTraceMatrix

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
        prompt_set: str = PROMPT_SET_BASELINE,
    ) -> str:
        """Parse an uploaded SHA Excel file and run the hazard graph for every row.

        Per row, the GIDs extracted into ``row_specific_controls_references`` plus
        the project name drive a JAMA ``bidirectional_trace`` fetch, which the
        graph's data_integration + transform nodes merge onto the hazard's
        ``requirements_traceability``. Rows with no GIDs fall back to the
        Excel-only (empty traceability) path. ``test_mode`` (None ⇒ config
        default) runs that fetch cache-only with no live JAMA API calls.
        ``prompt_set`` selects the v3/v4 stack for the embedded RTM subgraph.
        """
        from qaai.viewer.generator import write_viewer_hz
        from qaai.core.logging_config import start_new_run
        from qaai.agents.shared.data_integration import PYJAMA_AVAILABLE

        runnable = self._select(prompt_set)
        # Fresh run folder for THIS review, before the JAMA fetch so pyjama logs land here.
        run_dir = start_new_run()
        runnable.write_graph_png(run_dir)
        self._logger.info("Hazard review using prompt set '%s'", prompt_set)

        self._logger.info(
            "Starting upload hazard review: %s (project=%s, test_mode=%s)",
            filename, project_name, test_mode,
        )
        hazard_rows = self._parse_uploaded_excel(file_bytes, filename, sheet_name)

        def _hazard_thread_id(i, hazard_row):
            return (
                f"{thread_id_prefix}-{hazard_row.hazard_id}"
                if hazard_row.hazard_id
                else f"{thread_id_prefix}-{i:03d}"
            )

        def _hazard_graph_input(i, hazard_row):
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
            return graph_input

        return await _run_batch_review(
            logger=self._logger,
            run_dir=run_dir,
            items=hazard_rows,
            graph=runnable.graph,
            thread_id_fn=_hazard_thread_id,
            graph_input_fn=_hazard_graph_input,
            viewer_writer=write_viewer_hz,
            item_noun="hazard",
        )


class TestCaseReviewService:
    """
    Wraps the compiled LangGraph test_case_reviewer pipeline for use by the FastAPI layer.
    Instantiated once at application startup and stored on app.state.
    """

    _logger = logging.getLogger("qaai.api.test_case")

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
        from qaai.agents.shared.data_integration import (
            DataIntegrationNode,
            PyJamaRequest,
            PYJAMA_AVAILABLE,
            transform_test_case_review_to_state,
        )
        from qaai.viewer.generator import write_viewer_tc
        from qaai.core.logging_config import start_new_run

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

        return await _run_batch_review(
            logger=self._logger,
            run_dir=run_dir,
            items=state_dicts,
            graph=self.graph.graph,
            thread_id_fn=lambda i, _item: f"{thread_id_prefix}-{i:03d}",
            graph_input_fn=lambda _i, state_dict: {
                **state_dict,
                "review_objectives": state_dict.get("review_objectives") or default_objectives,
                "design_docs": state_dict.get("design_docs") or [],
                "cache_mode": cache_mode,
            },
            viewer_writer=write_viewer_tc,
            item_noun="test case",
        )
