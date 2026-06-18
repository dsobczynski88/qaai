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


def _field(obj, name):
    """Read ``name`` from a Pydantic model or a plain dict (final states hold both)."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def rtm_is_complete(state: dict) -> bool:
    """An RTM item is complete iff the synthesizer produced the full M1-M5+R6 rubric
    on top of non-empty per-spec coverage (a soft-failed decomposer leaves coverage empty)."""
    assessment = state.get("synthesized_assessment")
    findings = _field(assessment, "mandatory_findings")
    return (
        assessment is not None
        and isinstance(findings, list) and len(findings) == 6
        and bool(state.get("coverage_analysis"))
    )


def tc_is_complete(state: dict) -> bool:
    """A test-case item is complete iff the aggregator produced a non-empty checklist
    over non-empty per-spec coverage."""
    assessment = state.get("aggregated_assessment")
    checklist = _field(assessment, "evaluated_checklist")
    return (
        assessment is not None
        and isinstance(checklist, list) and len(checklist) > 0
        and bool(state.get("coverage_analysis"))
    )


def hazard_is_complete(state: dict) -> bool:
    """A hazard item is complete iff the final assessor produced the full H1-H7 rubric.
    (requirement_reviews may legitimately be empty for non-software hazards, so it is
    not required here.)"""
    assessment = state.get("hazard_assessment")
    findings = _field(assessment, "mandatory_findings")
    return assessment is not None and isinstance(findings, list) and len(findings) == 7


# ── Missing-required-records checks (advisory, input-based) ──
# Each returns human-readable notes for a review item whose core traced
# verification inputs are entirely absent. Distinct from the is_complete checks
# above (which judge the produced output): an item can have its inputs yet still
# fail to complete, or be flagged here yet still complete. The item id is carried
# alongside in the run log, so the note text stays id-free.

def rtm_missing_records(item: dict) -> list:
    """RTM: flag a requirement with zero traced test cases."""
    if not item.get("test_cases"):
        return ["No test cases are traced to this requirement."]
    return []


def tc_missing_records(item: dict) -> list:
    """Test case: flag a test case with zero upstream requirements."""
    if not item.get("requirements"):
        return ["No upstream requirements are traced to this test case."]
    return []


def hazard_missing_records(item) -> list:
    """Hazard: flag a hazard row with zero referenced risk-control requirements."""
    if not getattr(item, "row_specific_controls_references", None):
        return ["No risk-control requirements are referenced for this hazard."]
    return []


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
    entity_id_fn=None,
    is_complete_fn=None,
    missing_records_fn=None,
    cache_manager=None,
    prompt_set: Optional[str] = None,
    progress=None,
) -> str:
    """Shared batch loop for the three reviewer services.

    Writes ``inputs.jsonl``, invokes the compiled ``graph`` once per item while
    appending each final state to ``outputs.jsonl``, renders the viewer, and
    returns its path. The per-reviewer differences — thread-id source, graph
    input shape, and which ``write_viewer*`` to call — are injected as callables.

    Success-gating (the "only cache results from clean runs" toggle): when
    ``cache_manager`` + ``entity_id_fn`` are supplied, an item whose graph run
    raises, or whose final state fails ``is_complete_fn``, has its cache entries
    purged (scoped to ``prompt_set``) so a failed/incomplete run is never reused.
    A hard error no longer aborts the whole batch — the item is skipped and the
    remaining items still run; the job only fails if *nothing* produced output.

    Live progress + run log: when a ``progress`` handle (the background ``Job``)
    is supplied, ``begin(total)`` is called once the item count is known and
    ``record_item(ok=...)`` advances it per item, so GET /jobs/{id} can report
    ``[done/total]`` + ETA to the frontend. Problem notes — an errored item, an
    incomplete output, or (via ``missing_records_fn(item) -> list[str]``) a review
    item whose required traced inputs are absent — are collected into a
    problems-only ``run_log`` that is both pushed onto ``progress`` (shown live)
    and embedded into the viewer (the "View log" button). ``missing_records_fn``
    is advisory: a flagged item that still produces a complete output is *not*
    counted as failed.
    """
    inputs_path = run_dir / INPUT_JSONL_FILENAME
    with inputs_path.open("w", encoding="utf-8") as f:
        for item in items:
            f.write(json.dumps(item, default=_json_default) + "\n")

    outputs_path = run_dir / OUTPUT_JSONL_FILENAME
    outputs_path.write_text("", encoding="utf-8")

    async def _purge(i: int, item, reason: str) -> None:
        if cache_manager is None or entity_id_fn is None:
            return
        entity_id = entity_id_fn(i, item)
        if not entity_id:
            return
        logger.warning(
            "Purging cache for %s %s (%s) — not reusable", item_noun, entity_id, reason
        )
        try:
            await cache_manager.purge_entity(entity_id, prompt_set)
        except Exception as exc:  # purge is best-effort
            logger.warning("Cache purge failed for %s — %s", entity_id, exc)

    # Problems-only run log: one {item_id, level, text} dict per errored /
    # incomplete / missing-input item. Shared by reference with progress.messages
    # (shown live in the UI) and embedded into the viewer ("View log" button).
    run_log: list = []

    def _log(item_id: Optional[str], level: str, text: str) -> None:
        entry = {"item_id": item_id, "level": level, "text": text}
        run_log.append(entry)
        if progress is not None:
            progress.add_message(entry)

    if progress is not None:
        progress.begin(len(items))

    start = time.perf_counter()
    succeeded = 0
    failed = 0
    for i, item in enumerate(items):
        item_id = entity_id_fn(i, item) if entity_id_fn else None
        label = item_id or f"#{i + 1}"

        # Note absent required inputs up front (advisory — does not fail the item).
        if missing_records_fn is not None:
            for note in missing_records_fn(item) or []:
                logger.warning("[%d/%d] %s %s: %s", i + 1, len(items), item_noun, label, note)
                _log(item_id, "warning", note)

        config = {"configurable": {"thread_id": thread_id_fn(i, item)}}
        try:
            final_state = await graph.ainvoke(graph_input_fn(i, item), config)
        except Exception as exc:
            failed += 1
            logger.error(
                "[%d/%d] %s review errored, skipping item: %s",
                i + 1, len(items), item_noun, exc, exc_info=True,
            )
            _log(item_id, "error", f"{item_noun.capitalize()} {label}: review errored — item skipped.")
            await _purge(i, item, "run errored")
            if progress is not None:
                progress.record_item(ok=False)
            continue

        if is_complete_fn is not None and not is_complete_fn(final_state):
            # Surface the (incomplete) result in the viewer, but never reuse it.
            failed += 1
            logger.warning(
                "[%d/%d] %s review incomplete — recording output but purging cache",
                i + 1, len(items), item_noun,
            )
            _log(item_id, "warning",
                 f"{item_noun.capitalize()} {label}: incomplete output (rubric/coverage missing).")
            await _purge(i, item, "incomplete output")
            if progress is not None:
                progress.record_item(ok=False)
        else:
            succeeded += 1
            logger.info("[%d/%d] Completed %s review", i + 1, len(items), item_noun)
            if progress is not None:
                progress.record_item(ok=True)

        with outputs_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(final_state, default=_json_default) + "\n")

    viewer_path = viewer_writer(outputs_path, log_entries=run_log)
    if viewer_path is None:
        raise ValueError(f"{item_noun} review produced no output records")

    elapsed = time.perf_counter() - start
    logger.info(
        "Batch %s review complete: %d items in %.1fs (%d clean, %d failed/incomplete), viewer at %s",
        item_noun, len(items), elapsed, succeeded, failed, viewer_path,
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
        self.cache_manager = cache_manager
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
        progress=None,
    ) -> str:
        """Fetch a JAMA baseline, run the RTM graph for every requirement, return viewer.html path.

        test_mode (None ⇒ use the config's default) runs the JAMA fetch cache-only
        with no live API calls when True. prompt_set selects the v3/v4 graph.
        progress (the background Job) receives live per-requirement progress.
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
            entity_id_fn=lambda _i, state_dict: getattr(state_dict.get("requirement"), "req_id", None),
            is_complete_fn=rtm_is_complete,
            missing_records_fn=rtm_missing_records,
            cache_manager=self.cache_manager,
            prompt_set=prompt_set,
            progress=progress,
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
        self.cache_manager = cache_manager
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
        """Build a bidirectional_trace PyJamaRequest from a hazard row's controls.

        The hazard row's control references (extracted from the Risk Control
        Measures column via the identifier_pattern / extract_gids_format knob)
        become the JAMA identifiers, and the graph's data_integration + transform
        nodes fetch and merge the per-requirement traceability onto the hazard.

        Reached from run_from_excel_upload for every row that yields at least one
        identifier. The installed pyjama exposes request_type "bidirectional_trace"
        (its PyJamaRequest Literal includes it); the try/except below still guards
        older pyjama builds whose Literal lacks it. In test_mode the fetch is
        served strictly from ./cache/source/identifiers/ keyed by identifier, so
        project_name only needs to be non-empty there.
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
        except Exception as e:  # ValidationError on older pyjama builds without bidirectional_trace
            raise ValueError(
                "request_type='bidirectional_trace' is not supported by the installed "
                "pyjama version. Upgrade pyjama-fastapi to a release that exposes "
                f"bidirectional_trace to enable JAMA-sourced hazard traceability. ({e})"
            ) from e

    def _parse_uploaded_excel(
        self,
        file_bytes: bytes,
        filename: str,
        sheet_name: str,
        extract_gids_format: str = "GID-\\d+",
    ) -> List[HazardRowWithTraceMatrix]:
        from qaai.agents.hazard_risk_reviewer.loader import parse_sha_excel
        from qaai.agents.hazard_risk_reviewer.core import HazardTraceMatrix

        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
            tmp.write(file_bytes)
            tmp_path = tmp.name

        try:
            rows = parse_sha_excel(
                tmp_path, sheet_name=sheet_name, extract_gids_format=extract_gids_format
            ).rows
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
        extract_gids_format: str = "GID-\\d+",
        progress=None,
    ) -> str:
        """Parse an uploaded SHA Excel file and run the hazard graph for every row.

        Per row, the identifiers extracted into ``row_specific_controls_references``
        (matched against ``extract_gids_format`` in the Risk Control Measures
        column) plus the project name drive a JAMA ``bidirectional_trace`` fetch,
        which the graph's data_integration + transform nodes merge onto the
        hazard's ``requirements_traceability``. Rows with no matching identifiers
        fall back to the Excel-only (empty traceability) path. ``test_mode``
        (None ⇒ config default) runs that fetch cache-only with no live JAMA API
        calls. ``prompt_set`` selects the v3/v4 stack for the embedded RTM
        subgraph. ``extract_gids_format`` defaults to the production ``GID-\\d+``
        scheme; pass e.g. ``REQ-PUMP-\\d+`` for the sample SHA workbook.
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
        hazard_rows = self._parse_uploaded_excel(
            file_bytes, filename, sheet_name, extract_gids_format=extract_gids_format
        )

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
            entity_id_fn=lambda _i, hazard_row: getattr(hazard_row, "hazard_id", None),
            is_complete_fn=hazard_is_complete,
            missing_records_fn=hazard_missing_records,
            cache_manager=self.cache_manager,
            prompt_set=prompt_set,
            progress=progress,
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
        self.cache_manager = cache_manager
        self.graph = TCReviewerRunnable(
            client, model, model_kwargs, checkpointer=MemorySaver(),
            pyjama_config=pyjama_config, cache_manager=cache_manager,
        )

    async def run_from_baseline(
        self, baseline_id: str, thread_id_prefix: str, cache_mode: str = "partial",
        test_mode: Optional[bool] = None, progress=None,
    ) -> str:
        """Fetch a JAMA baseline, run the TC graph for every test case, return viewer_tc.html path.

        test_mode (None ⇒ use the config's default) runs the JAMA fetch cache-only
        with no live API calls when True. progress (the background Job) receives
        live per-test-case progress.
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
            entity_id_fn=lambda _i, state_dict: getattr(state_dict.get("test_case"), "test_id", None),
            is_complete_fn=tc_is_complete,
            missing_records_fn=tc_missing_records,
            cache_manager=self.cache_manager,
            prompt_set=None,  # test-case reviewer uses the default un-namespaced cache
            progress=progress,
        )
