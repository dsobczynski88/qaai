"""
Data integration layer for LangGraph pipelines.

Provides a unified interface for fetching data from multiple sources:
- Option 1: Local/Direct (JSON objects, JSONL files) - used by tests
- Option 2: JAMA Baseline (live API fetch via PyJama) - used by production

The DataIntegrationNode is a conditional node that:
1. Checks if state contains a pyjama_request
2. If yes: fetches from JAMA and returns {jama_data, jama_metadata}
3. If no: returns {} (no-op, data already in state)

Transform utilities convert raw JAMA responses into pipeline state formats.
"""

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from qaai.core.constants import INPUT_JSONL_FILENAME

# Import hazard-related models
try:
    from qaai.components.hazard_risk_reviewer.core import (
        HazardPackageFromExcel,
        HazardRowFromExcel,
        HazardRowWithTraceMatrix,
        HazardTraceMatrix,
    )
    HAZARD_MODELS_AVAILABLE = True
except ImportError:
    HAZARD_MODELS_AVAILABLE = False
    HazardPackageFromExcel = None
    HazardRowFromExcel = None
    HazardRowWithTraceMatrix = None
    HazardTraceMatrix = None

# Re-export PyJama classes for convenience
try:
    from pyjama.langgraph.nodes import (
        PyJamaDataSourceNode,
        PyJamaNodeConfig,
        PyJamaRequest,
    )
    from pyjama.utils.cache_manager import CacheMode
    from pyjama.langgraph.transforms import (
        transform_test_suite_review_to_state as _pyjama_transform_test_suite,
        transform_test_case_review_to_state as _pyjama_transform_test_case,
    )
    PYJAMA_AVAILABLE = True
except ImportError:
    PYJAMA_AVAILABLE = False
    PyJamaDataSourceNode = None
    PyJamaNodeConfig = None
    PyJamaRequest = None
    _pyjama_transform_test_suite = None
    _pyjama_transform_test_case = None


logger = logging.getLogger(__name__)


# --- AutoQA <-> pyjama logging boundary shim ---------------------------------
# pyjama's PyJamaDataSourceNode.__init__ unconditionally (a) creates its OWN
# logs/run-<ts>/ folder via make_output_directory and (b) attaches a FileHandler
# to the "projectlog.pyjama_api" logger via ProjectLogger. That produces a second
# run folder next to AutoQA's and accumulates handlers across the per-request
# rebuilt nodes. We patch both module-level symbols so pyjama instead logs into
# AutoQA's single active run folder, of which AutoQA's setup_logging is the sole
# FileHandler owner. nodes.py imports these as module-level names, so rebinding
# them on the module takes effect for every node it builds.
if PYJAMA_AVAILABLE:
    import pyjama.langgraph.nodes as _pyjama_nodes

    def _qaai_run_log_dir(fold_path=None):
        """Return AutoQA's ACTIVE run directory (logs/run-<ts>/) so pyjama writes
        pyjama.log there instead of creating its own logs/run-<ts>/ folder."""
        from qaai.core.config import settings

        run_dir = Path(settings.log_file_path).parent
        run_dir.mkdir(parents=True, exist_ok=True)
        return str(run_dir)

    class _NoOpProjectLogger:
        """Drop-in for pyjama's ProjectLogger that attaches NO handlers.

        AutoQA's setup_logging owns the single 'projectlog.pyjama_api' FileHandler
        (-> run_dir/pyjama.log) and re-points it on every start_new_run(), so
        pyjama must not add its own handler (which would duplicate lines and
        accumulate across rebuilt nodes). get_logger() still returns the real
        named logger so pyjama's @timing decorators and PyJamaTraceMatrix resolve
        to the same logger AutoQA configured.
        """

        def __init__(self, name, log_file=None):
            self._name = name

        def config(self):
            return self

        def get_logger(self):
            return logging.getLogger(self._name)

    _pyjama_nodes.make_output_directory = _qaai_run_log_dir
    _pyjama_nodes.ProjectLogger = _NoOpProjectLogger


__all__ = [
    "PyJamaRequest",
    "PyJamaNodeConfig",
    "PyJamaDataSourceNode",
    "DataIntegrationNode",
    "transform_test_suite_review_to_state",
    "transform_test_case_review_to_state",
    "transform_hazard_record_to_state",
    "transform_bidirectional_trace_to_state",
    "make_transform_node_test_suite_review",
    "make_transform_node_test_case_review",
    "make_transform_node_bidirectional_trace",
    "PYJAMA_AVAILABLE",
]


class DataIntegrationNode:
    """
    LangGraph-compatible node that conditionally fetches data from JAMA.
    
    This node serves as the entry point for all pipelines, supporting two modes:
    
    Mode 1 (Local/Direct):
        - State contains requirement/test_case + test_cases/requirements directly
        - pyjama_request is None or absent
        - Node returns {} (no-op, data already in state)
        - Used by: pytest fixtures, batch JSONL processing
    
    Mode 2 (JAMA Baseline):
        - State contains pyjama_request with baseline_id
        - Node fetches from JAMA via PyJamaDataSourceNode
        - Returns {jama_data: [...], jama_metadata: {...}}
        - Used by: API endpoints, live integration workflows
    
    Usage:
        # In pipeline.py
        from qaai.components.shared.data_integration import DataIntegrationNode
        
        data_integration = DataIntegrationNode(pyjama_config)
        sg.add_node("data_integration", data_integration)
        sg.add_edge(START, "data_integration")
    
    State Requirements:
        Input state may contain:
            - pyjama_request: Optional[PyJamaRequest] - triggers JAMA fetch
        
        Output state (JAMA mode only):
            - jama_data: List[Dict] - raw JAMA response
            - jama_metadata: Dict - fetch metadata (count, baseline_id, etc.)
    """
    
    def __init__(self, pyjama_config: Optional["PyJamaNodeConfig"] = None):
        """
        Initialize the data integration node.
        
        Args:
            pyjama_config: Optional PyJama configuration. If None, JAMA mode
                          will attempt lazy initialization from environment
                          variables (JAMA_HOST_ADDRESS, JAMA_CLIENT_ID,
                          JAMA_CLIENT_SECRET).
        """
        self.pyjama_config = pyjama_config
        # PyJamaDataSourceNodes cached per effective test_mode override
        # (None / True / False), since a per-call override must use a config
        # whose test_mode matches.
        self._pyjama_nodes: Dict[Any, "PyJamaDataSourceNode"] = {}

        if not PYJAMA_AVAILABLE:
            logger.warning(
                "PyJama not available. JAMA baseline fetching will be disabled. "
                "Install pyjama to enable: pip install pyjama"
            )

    def _build_config(self, test_mode_override: Optional[bool]) -> "PyJamaNodeConfig":
        """Resolve the effective PyJamaNodeConfig, applying a per-call test_mode override.

        When a base config was provided, a non-None override is applied via
        model_copy. Otherwise the config is built from environment variables
        (JAMA_* + PYJAMA_TEST_MODE); in test_mode credentials are optional.
        """
        config = self.pyjama_config

        if config is None:
            host = os.getenv("JAMA_HOST_ADDRESS")
            client_id = os.getenv("JAMA_CLIENT_ID")
            client_secret = os.getenv("JAMA_CLIENT_SECRET")
            env_test_mode = os.getenv("PYJAMA_TEST_MODE", "false").lower() == "true"
            test_mode = test_mode_override if test_mode_override is not None else env_test_mode

            # Live (non-test) fetching needs full credentials; test_mode is cache-only.
            if not test_mode and not all([host, client_id, client_secret]):
                raise ValueError(
                    "PyJama config not provided and environment variables not set. "
                    "Either pass pyjama_config to DataIntegrationNode or set: "
                    "JAMA_HOST_ADDRESS, JAMA_CLIENT_ID, JAMA_CLIENT_SECRET (or enable test_mode)."
                )

            return PyJamaNodeConfig(
                host_address=host,
                client_id=client_id,
                client_secret=client_secret,
                max_concurrent=100,
                cache_mode=CacheMode.USE,  # OFF / USE / REFRESH
                test_mode=test_mode,
            )

        if test_mode_override is not None and test_mode_override != config.test_mode:
            config = config.model_copy(update={"test_mode": test_mode_override})
        return config

    def _get_pyjama_node(self, test_mode_override: Optional[bool] = None) -> "PyJamaDataSourceNode":
        """Lazily build (and cache) the PyJama node for the effective test_mode."""
        if not PYJAMA_AVAILABLE:
            raise RuntimeError(
                "PyJama is not installed. Cannot fetch from JAMA baseline. "
                "Install pyjama: pip install pyjama"
            )

        node = self._pyjama_nodes.get(test_mode_override)
        if node is None:
            config = self._build_config(test_mode_override)
            node = PyJamaDataSourceNode(config)
            self._pyjama_nodes[test_mode_override] = node
            logger.info(
                "PyJama node initialized (test_mode=%s)", config.test_mode
            )
        return node

    async def __call__(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute the node: conditionally fetch from JAMA or pass through.
        
        Args:
            state: LangGraph state dict, may contain 'pyjama_request'
        
        Returns:
            Empty dict (local mode) or {jama_data, jama_metadata} (JAMA mode)
        
        Raises:
            RuntimeError: If JAMA mode requested but PyJama not available
            ValueError: If JAMA config missing
            Exception: If JAMA API call fails
        """
        pyjama_request = state.get("pyjama_request")

        if pyjama_request is None:
            # Local mode: data already in state, no-op
            logger.debug("Local mode: pyjama_request not present, skipping JAMA fetch")
            return {}

        # JAMA mode: fetch from baseline. A per-call test_mode override threaded
        # through graph state (pyjama_test_mode) selects cache-only behaviour.
        test_mode_override = state.get("pyjama_test_mode")
        logger.info(
            "JAMA mode: fetching data for request_type=%s (test_mode=%s)",
            pyjama_request.request_type if hasattr(pyjama_request, 'request_type') else 'unknown',
            test_mode_override,
        )

        # Lazy init PyJama node for the effective test_mode, then delegate.
        pyjama_node = self._get_pyjama_node(test_mode_override)
        try:
            result = await pyjama_node(state)
            logger.info(
                "JAMA fetch successful: %d items retrieved",
                len(result.get("jama_data", []))
            )
            return result
        except Exception as e:
            logger.error("JAMA fetch failed: %s", str(e), exc_info=True)
            raise


def _coerce_state_models_to_qaai(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Re-validate a transform state-entry's model fields into qaai models.

    pyjama's transforms emit pyjama's own Requirement / TestCase / DesignDoc
    classes (and its TestCase uses ``in_review_baseline``), but qaai's pipeline
    models (e.g. TestSuite, RTMReviewState) require the
    qaai.components.shared.core classes (TestCase uses ``in_baseline``). Passing
    the pyjama instances straight through makes Pydantic v2 raise a ``model_type``
    error downstream (e.g. in SummaryNode._build_result). This rebuilds each
    field as the qaai class, mapping ``in_review_baseline -> in_baseline``.

    Defensive: accepts pyjama model instances, qaai instances, or plain dicts.
    Idempotent for already-qaai entries.
    """
    from qaai.components.shared.core import Requirement, TestCase, DesignDocument

    def _dump(o: Any) -> Dict[str, Any]:
        return o.model_dump() if hasattr(o, "model_dump") else dict(o)

    def to_req(o: Any) -> "Requirement":
        d = _dump(o)
        return Requirement(**{k: d.get(k) for k in ("req_id", "text")})

    def to_tc(o: Any) -> "TestCase":
        d = _dump(o)
        if "in_review_baseline" in d:  # pyjama -> qaai field rename
            d["in_baseline"] = d.pop("in_review_baseline")
        keep = {"test_id", "description", "setup", "steps", "expectedResults", "in_baseline"}
        return TestCase(**{k: v for k, v in d.items() if k in keep})

    def to_dd(o: Any) -> "DesignDocument":
        d = _dump(o)
        return DesignDocument(**{k: d.get(k) for k in ("doc_id", "name", "description")})

    out = dict(entry)
    if out.get("requirement") is not None:
        out["requirement"] = to_req(out["requirement"])
    if out.get("test_case") is not None:
        out["test_case"] = to_tc(out["test_case"])
    if "test_cases" in out:
        out["test_cases"] = [to_tc(x) for x in (out["test_cases"] or [])]
    if "requirements" in out:
        out["requirements"] = [to_req(x) for x in (out["requirements"] or [])]
    if "design_docs" in out:
        out["design_docs"] = [to_dd(x) for x in (out["design_docs"] or [])]
    return out


def transform_test_suite_review_to_state(
    jama_data: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """
    Transform JAMA test_suite_review data into RTMReviewState format.
    
    Wraps pyjama.langgraph.transforms.transform_test_suite_review_to_state()
    with additional validation and error handling.
    
    Args:
        jama_data: Raw JAMA response from get_test_suite_reviewer_structure()
    
    Returns:
        List of state dicts, one per requirement, each containing:
            - requirement: Requirement model
            - test_cases: List[TestCase]
            - design_docs: List[DesignDoc] (if present)
    
    Raises:
        RuntimeError: If PyJama not available
        ValueError: If transformation fails
    
    Example:
        >>> jama_data = [
        ...     {
        ...         "requirement": {"req_id": "REQ-101", "text": "..."},
        ...         "test_cases": [{"test_id": "TC-201", ...}],
        ...         "design_docs": []
        ...     }
        ... ]
        >>> states = transform_test_suite_review_to_state(jama_data)
        >>> len(states)
        1
        >>> states[0]["requirement"].req_id
        'REQ-101'
    """
    if not PYJAMA_AVAILABLE or _pyjama_transform_test_suite is None:
        raise RuntimeError(
            "PyJama is not installed. Cannot transform JAMA data. "
            "Install pyjama: pip install pyjama"
        )
    
    logger.info("Transforming %d test_suite_review entries to state format", len(jama_data))
    
    try:
        transformed = _pyjama_transform_test_suite(jama_data)
        # pyjama returns its own Requirement/TestCase/DesignDoc classes; coerce to
        # qaai shared.core models so downstream nodes (TestSuite, etc.) validate.
        transformed = [_coerce_state_models_to_qaai(e) for e in transformed]
        logger.info("Successfully transformed %d entries", len(transformed))
        return transformed
    except Exception as e:
        logger.error("Transform failed: %s", str(e), exc_info=True)
        raise ValueError(f"Failed to transform JAMA data: {e}") from e


def transform_test_case_review_to_state(
    jama_data: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """
    Transform JAMA test_case_review data into TCReviewState format.
    
    Wraps pyjama.langgraph.transforms.transform_test_case_review_to_state()
    with additional validation and error handling.
    
    Args:
        jama_data: Raw JAMA response from get_test_case_reviewer_structure()
    
    Returns:
        List of state dicts, one per test case, each containing:
            - test_case: TestCase model
            - requirements: List[Requirement]
            - design_docs: List[DesignDoc] (if present)
    
    Raises:
        RuntimeError: If PyJama not available
        ValueError: If transformation fails
    
    Example:
        >>> jama_data = [
        ...     {
        ...         "test_case": {"test_id": "TC-201", ...},
        ...         "requirements": [{"req_id": "REQ-101", "text": "..."}],
        ...         "design_docs": []
        ...     }
        ... ]
        >>> states = transform_test_case_review_to_state(jama_data)
        >>> len(states)
        1
        >>> states[0]["test_case"].test_id
        'TC-201'
    """
    if not PYJAMA_AVAILABLE or _pyjama_transform_test_case is None:
        raise RuntimeError(
            "PyJama is not installed. Cannot transform JAMA data. "
            "Install pyjama: pip install pyjama"
        )
    
    logger.info("Transforming %d test_case_review entries to state format", len(jama_data))
    
    try:
        transformed = _pyjama_transform_test_case(jama_data)
        # pyjama returns its own Requirement/TestCase/DesignDoc classes; coerce to
        # qaai shared.core models so downstream nodes validate.
        transformed = [_coerce_state_models_to_qaai(e) for e in transformed]
        logger.info("Successfully transformed %d entries", len(transformed))
        return transformed
    except Exception as e:
        logger.error("Transform failed: %s", str(e), exc_info=True)
        raise ValueError(f"Failed to transform JAMA data: {e}") from e


def transform_hazard_record_to_state(
    excel_file_path: str,
    pyjama_response_file_path: str,
    output_jsonl_path: str = INPUT_JSONL_FILENAME,
    sheet_name: str = "SHA Table",
    extract_gids_format: str = "GID-\\d+",
) -> List[HazardRowWithTraceMatrix]:
    """
    Data transformation: Excel → PyJama Fixture → Enhanced JSONL

    Implements the data preparation pipeline for hazard risk review:
    1. Parse Excel via parse_sha_excel() to extract hazard rows and control references
    2. Load unified pyjama fixture (single JSONL with bidirectional trace for all identifiers)
    3. For each Excel row:
       a. Build requirements_traceability field using merge_hazard_with_pyjama_traceability()
       b. Filter pyjama responses: keep only items where req_id is in row_specific_controls_references
       c. Write to output JSONL
    
    This function handles data transformation only. Graph invocation and orchestration
    are the responsibility of the caller (see scripts/run_hazard_pipeline.py for an example).

    Args:
        excel_file_path: Path to software_hazard_analysis.xlsx
        pyjama_response_file_path: Path to unified pyjama response JSONL.
                                   Each line has: {requirement, system_requirements, test_cases, design_docs}
        output_jsonl_path: Where to write the enhanced inputs.jsonl with requirements_traceability

    Returns:
        List of HazardRowWithTraceMatrix, one per Excel row with requirements_traceability populated

    Raises:
        FileNotFoundError: If Excel or pyjama fixture files not found
        ValueError: If data structure validation fails
    
    Example:
        >>> enhanced_rows = transform_hazard_record_to_state(
        ...     excel_file_path="hazards.xlsx",
        ...     pyjama_response_file_path="pyjama.jsonl",
        ...     output_jsonl_path="enhanced_inputs.jsonl"
        ... )
        >>> # Now invoke graph separately:
        >>> graph = HazardReviewerRunnable(client=client, model=model)
        >>> outputs = await asyncio.gather(*[
        ...     graph.graph.ainvoke({"hazard": row})
        ...     for row in enhanced_rows
        ... ])
    """
    from qaai.components.hazard_risk_reviewer.loader import (
        parse_sha_excel,
        merge_hazard_with_pyjama_traceability,
    )

    excel_path = Path(excel_file_path)
    pyjama_path = Path(pyjama_response_file_path)
    output_path = Path(output_jsonl_path)

    if not excel_path.exists():
        raise FileNotFoundError(f"Excel file not found: {excel_path}")
    if not pyjama_path.exists():
        raise FileNotFoundError(f"Pyjama response file not found: {pyjama_path}")

    logger.info("=" * 80)
    logger.info("HAZARD RECORD TRANSFORMATION WORKFLOW")
    logger.info("=" * 80)

    # Step 1: Parse Excel to extract hazard rows and all control references
    logger.info("[Step 1] Parsing Excel file: %s", excel_path)
    excel_results: HazardPackageFromExcel = parse_sha_excel(
        str(excel_path),
        sheet_name=sheet_name,
        extract_gids_format=extract_gids_format
    )
    excel_rows: List[HazardRowFromExcel] = excel_results.rows
    all_controls_references: List[str] = excel_results.all_controls_references or []
    logger.info("[Step 1] Extracted %d hazard rows, %d unique control references", len(excel_rows), len(all_controls_references))

    # Step 2: Load and index unified pyjama response
    logger.info("[Step 2] Loading unified pyjama response from: %s", pyjama_path)
    pyjama_lookup: Dict[str, Dict[str, Any]] = {}
    with pyjama_path.open("r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            data = json.loads(line)
            req_id = data.get("requirement", {}).get("req_id")
            if req_id:
                pyjama_lookup[req_id] = data
    logger.info("[Step 2] Indexed %d pyjama traceability entries", len(pyjama_lookup))

    # Step 3: Merge each Excel row with filtered pyjama traceability and write to JSONL
    logger.info("[Step 3] Merging Excel rows with pyjama traceability")
    enhanced_rows: List[HazardRowWithTraceMatrix] = []
    with output_path.open("w", encoding="utf-8") as f:
        for i, row in enumerate(excel_rows):
            # Merge row with pyjama traceability to create HazardRowWithTraceMatrix
            enhanced_row: HazardRowWithTraceMatrix = merge_hazard_with_pyjama_traceability(row, pyjama_lookup)
            enhanced_rows.append(enhanced_row)
            
            # Serialize model to JSON for JSONL output
            f.write(enhanced_row.model_dump_json(ensure_ascii=False) + "\n")
            
            # Log traceability count from the structured HazardTraceMatrix
            num_trace = (
                len(enhanced_row.requirements_traceability.requirements)
                if enhanced_row.requirements_traceability else 0
            )
            logger.debug(
                "  Row %d: %s -> %d traceability items",
                i,
                enhanced_row.hazardous_situation_id,
                num_trace
            )

    logger.info("[Step 3] Written %d enhanced rows to: %s", len(enhanced_rows), output_path)

    # Transformation complete
    logger.info("=" * 80)
    logger.info("HAZARD RECORD TRANSFORMATION COMPLETE")
    logger.info("=" * 80)
    logger.info("Output Summary:")
    logger.info("  - Rows processed: %d HazardRowWithTraceMatrix models", len(enhanced_rows))
    logger.info("  - JSONL written to: %s", output_path)
    logger.info("  - Graph invocation is the responsibility of the caller")
    logger.info("=" * 80)

    return enhanced_rows


def make_transform_node_test_suite_review():
    """
    Create a LangGraph-compatible transform node for the test_suite_reviewer pipeline.

    Converts JAMA test_suite_review data (jama_data) to RTMReviewState format when
    present; returns {} (no-op) when data is already in state.
    """
    def transform(state) -> dict:
        jama_data = state.get("jama_data")

        if jama_data:
            logger.info("Transforming %d JAMA entries to RTMReviewState format", len(jama_data))
            transformed = transform_test_suite_review_to_state(jama_data)
            if transformed:
                req = transformed[0].get("requirement")
                logger.info(
                    "Transform successful: requirement=%s, test_cases=%d",
                    req.req_id if req else "unknown",
                    len(transformed[0].get("test_cases", [])),
                )
                return transformed[0]
            logger.warning("Transform returned empty result")
            return {}

        logger.debug("Local mode: skipping JAMA transform")
        return {}

    return transform


def transform_bidirectional_trace_to_state(
    jama_data: List[Dict[str, Any]]
) -> "HazardTraceMatrix":
    """
    Aggregate JAMA bidirectional_trace data into a single HazardTraceMatrix.

    The bidirectional_trace request (see the pyjama-fastapi
    bidirectional_trace example) returns one entry per requirement, each with
    four keys: ``requirement``, ``system_requirements``, ``test_cases``, and
    ``design_docs``. The hazard reviewer evaluates ONE hazard with MANY traced
    requirements, so unlike the per-requirement / per-test-case transforms this
    one collapses every entry into a single HazardTraceMatrix bundling all
    traced artifacts (deduplicated).

    This mirrors the accumulation logic of
    hazard_risk_reviewer.loader.merge_hazard_with_pyjama_traceability, but
    iterates over the already-filtered bidirectional response (the JAMA fetch
    only returns the requested identifiers) rather than a row-id lookup.

    Note: ``request_type="bidirectional_trace"`` is not exposed by the installed
    pyjama 1.0.0 (its PyJamaRequest Literal only allows test_suite_review /
    test_case_review / hierarchical_trace). This transform deliberately keys off
    the SHAPE of ``jama_data``, never off request_type, so it is forward
    compatible: it works as soon as a pyjama version emitting this shape is
    installed, and never raises on the current version.

    Args:
        jama_data: Raw bidirectional_trace response (list of per-requirement dicts).

    Returns:
        A single HazardTraceMatrix with deduplicated requirements, test_cases,
        design_docs, system_requirements, and flattened user_needs. Returns an
        empty HazardTraceMatrix on empty/invalid input (never raises).
    """
    if not HAZARD_MODELS_AVAILABLE or HazardTraceMatrix is None:
        raise RuntimeError(
            "Hazard models unavailable — cannot build HazardTraceMatrix from JAMA data."
        )

    from qaai.components.shared.core import Requirement, TestCase, DesignDocument

    requirements: List[Any] = []
    test_cases: List[Any] = []
    design_docs: List[Any] = []
    system_requirements: List[Any] = []
    user_needs: List[Any] = []

    for entry in jama_data or []:
        if not isinstance(entry, dict):
            continue

        # Requirement (one per entry)
        req_data = entry.get("requirement")
        if isinstance(req_data, dict):
            try:
                req_obj = Requirement(**req_data)
                if req_obj not in requirements:
                    requirements.append(req_obj)
            except Exception:
                pass

        # Test cases (union, dedup)
        for tc_data in entry.get("test_cases") or []:
            if not isinstance(tc_data, dict):
                continue
            try:
                tc_data = dict(tc_data)
                # Raw pyjama test cases carry `in_review_baseline`; the shared
                # TestCase model uses `in_baseline`. Map it explicitly so the
                # flag survives (Pydantic v2 silently drops the unknown key).
                if "in_review_baseline" in tc_data:
                    tc_data["in_baseline"] = tc_data.pop("in_review_baseline")
                tc_obj = TestCase(**tc_data)
                if tc_obj not in test_cases:
                    test_cases.append(tc_obj)
            except Exception:
                pass

        # Design docs (union, dedup)
        for dd_data in entry.get("design_docs") or []:
            if not isinstance(dd_data, dict):
                continue
            try:
                dd_obj = DesignDocument(**dd_data)
                if dd_obj not in design_docs:
                    design_docs.append(dd_obj)
            except Exception:
                pass

        # System requirements (union, dedup) + nested user needs (flatten, dedup)
        for sys_req_data in entry.get("system_requirements") or []:
            if not isinstance(sys_req_data, dict):
                continue
            try:
                sys_req_obj = Requirement(**{
                    k: v for k, v in sys_req_data.items() if k != "user_needs"
                })
                if sys_req_obj not in system_requirements:
                    system_requirements.append(sys_req_obj)
            except Exception:
                pass

            for un_data in sys_req_data.get("user_needs") or []:
                if not isinstance(un_data, dict):
                    continue
                try:
                    un_obj = Requirement(**un_data)
                    if un_obj not in user_needs:
                        user_needs.append(un_obj)
                except Exception:
                    pass

    matrix = HazardTraceMatrix(
        requirements=requirements,
        test_cases=test_cases,
        design_docs=design_docs,
        system_requirements=system_requirements,
        user_needs=user_needs,
    )
    logger.info(
        "Aggregated bidirectional_trace: %d requirements, %d test_cases, "
        "%d design_docs, %d system_requirements, %d user_needs",
        len(requirements), len(test_cases), len(design_docs),
        len(system_requirements), len(user_needs),
    )
    return matrix


def make_transform_node_bidirectional_trace():
    """
    Create a LangGraph-compatible transform node for the hazard_risk_reviewer.

    Converts JAMA bidirectional_trace data (jama_data) into a single
    HazardTraceMatrix and merges it onto the hazard already in state, populating
    `requirements_traceability`. Returns {} (no-op) when jama_data is absent —
    i.e. the Excel/local path where the hazard already carries its traceability.
    """
    def transform(state) -> dict:
        jama_data = state.get("jama_data")
        if not jama_data:
            logger.debug("Local mode: skipping bidirectional_trace transform")
            return {}

        hazard = state.get("hazard")
        if hazard is None:
            logger.warning(
                "bidirectional_trace transform: jama_data present but no hazard in "
                "state — skipping merge"
            )
            return {}

        matrix = transform_bidirectional_trace_to_state(jama_data)
        merged = hazard.model_copy(update={"requirements_traceability": matrix})
        logger.info(
            "Transform successful: merged JAMA traceability onto hazard %s "
            "(%d requirements)",
            getattr(hazard, "hazard_id", "unknown"),
            len(matrix.requirements),
        )
        return {"hazard": merged}

    return transform


def make_transform_node_test_case_review():
    """
    Create a LangGraph-compatible transform node for the test_case_reviewer pipeline.

    Converts JAMA test_case_review data (jama_data) to TCReviewState format when
    present; returns {} (no-op) when data is already in state.
    """
    def transform(state) -> dict:
        jama_data = state.get("jama_data")

        if jama_data:
            logger.info("Transforming %d JAMA entries to TCReviewState format", len(jama_data))
            transformed = transform_test_case_review_to_state(jama_data)
            if transformed:
                tc = transformed[0].get("test_case")
                logger.info(
                    "Transform successful: test_case=%s, requirements=%d",
                    tc.test_id if tc else "unknown",
                    len(transformed[0].get("requirements", [])),
                )
                return transformed[0]
            logger.warning("Transform returned empty result")
            return {}

        logger.debug("Local mode: skipping JAMA transform")
        return {}

    return transform
