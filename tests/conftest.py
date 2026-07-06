import truststore
truststore.inject_into_ssl()

import json
import os
from pathlib import Path
import pytest
from dotenv import load_dotenv

load_dotenv()

from httpx import AsyncClient, ASGITransport

from qaai.core.config import settings
from qaai.core.constants import INPUT_JSONL_FILENAME, OUTPUT_JSONL_FILENAME
from qaai.core.telemetry import TokenUsageTracker

# All test-run artifacts (logs, JSONL records, telemetry, viewers, graph pngs) go
# under logs/tests/ to keep them separate from production/API-server runs under
# logs/. Setting this BEFORE importing qaai.api.main (below) ensures both the
# import-time create_app() and every start_new_run() resolve to logs/tests.
settings.log_base_dir = "./logs/tests"

from qaai.agents.clients import (
    RateLimitOpenAIClient
)

from qaai.agents.hazard_risk_reviewer.core import (
    HazardRowWithTraceMatrix,
    HazardTraceMatrix,
)
from qaai.agents.hazard_risk_reviewer.constants import (
    HAZARD_RISK_REVIEWER_REQUIRED_HAZARD_FIELDS,
)

from qaai.agents.test_suite_reviewer.core import (
    Requirement,
    TestCase,
    DecomposedSpec,
    DecomposedRequirement,
    SummarizedTestCase,
    TestSuite,
    EvaluatedSpec,
    DesignDocument,
)

from qaai.api.main import app, lifespan

from tests.helpers import load_jsonl, resolve_fixture_path


# Default fixture files per parametrized reviewer test, used when --input-file
# is not supplied. Keyed by test function name.
_DEFAULT_INPUT_FILES = {
    "test_test_case_reviewer": "test_case_review_all_fields.jsonl",
    "test_test_suite_reviewer": "test_suite_review_all_fields.jsonl",
}


def pytest_addoption(parser):
    """Register CLI options to select the integration tests' input fixture file.

    Bare filenames are resolved through the tests/fixtures/ search order
    (mock -> gold -> local -> external -> root) by load_jsonl /
    resolve_fixture_path.
    """
    parser.addoption(
        "--input-file",
        action="store",
        default=None,
        help=(
            "Fixture filename to use as the integration test input. JSONL for the "
            "test_case / test_suite reviewers; the .xlsx SHA workbook for the hazard "
            "reviewer. Resolved across tests/fixtures/{local,external,...}. "
            "Defaults to each reviewer's standard fixture when omitted."
        ),
    )
    parser.addoption(
        "--pyjama-file",
        action="store",
        default=None,
        help=(
            "Hazard reviewer only: pyjama traceability JSONL filename. "
            "Defaults to pyjama_response_unified.jsonl."
        ),
    )
    # Default graph-invocation settings, surfaced by the `review_settings`
    # fixture so tests never hard-code them. Each is overridable at the command
    # line, e.g. `uv run pytest -m unit --cache-mode on`.
    parser.addoption(
        "--cache-mode",
        action="store",
        default="off",
        choices=["off", "on", "test"],
        help="cache_mode threaded into reviewer graph state (default: off).",
    )
    parser.addoption(
        "--test-mode",
        action="store",
        default="true",
        help=(
            "When true, tests use the call-counting stub_llm_client instead of a "
            "real LLM client (default: true)."
        ),
    )
    parser.addoption(
        "--include-edge-case-analysis",
        action="store",
        default="false",
        help=(
            "Selects the edge-case prompt set for the test-suite / hazard "
            "reviewers (default: false)."
        ),
    )


def _as_bool(value) -> bool:
    """Coerce a CLI string ('true'/'1'/'yes') or bool to a bool."""
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _row_id(row: dict, index: int) -> str:
    """Best-effort stable id for a parametrized fixture row.

    Prefers the test_case.test_id (TC reviewer) or requirement.req_id (RTM
    reviewer), falling back to a positional id so an arbitrary custom file still
    collects without erroring.
    """
    try:
        if "test_case" in row:
            return row["test_case"]["test_id"]
        if "requirement" in row:
            return row["requirement"]["req_id"]
    except (KeyError, TypeError):
        pass
    return f"row{index}"


def pytest_generate_tests(metafunc):
    """Parametrize the test_case / test_suite reviewer tests over JSONL rows.

    Resolution happens at collection time (pytest_generate_tests), so the
    --input-file option is available before fixtures run. Falls back to each
    reviewer's default fixture when the option is omitted.
    """
    fn = metafunc.function.__name__
    if fn not in _DEFAULT_INPUT_FILES or "row" not in metafunc.fixturenames:
        return

    fixture_name = metafunc.config.getoption("--input-file") or _DEFAULT_INPUT_FILES[fn]
    rows = load_jsonl(fixture_name)
    ids = [_row_id(row, i) for i, row in enumerate(rows)]
    metafunc.parametrize("row", rows, ids=ids)


@pytest.fixture(scope="session")
def test_run_dir():
    """Single per-session run folder under logs/tests for integration artifacts.

    Calls start_new_run() (base resolves to settings.log_base_dir = ./logs/tests),
    which also wires setup_logging() so node/app logs land in this folder's
    qaai.log — mirroring how the API produces one folder per review batch. Only
    integration tests depend on this (via token_tracker / jsonl_recorders), so API
    tests don't get an extra session folder — they get the per-request folder that
    the service's own start_new_run() creates.
    """
    from qaai.core.logging_config import start_new_run

    return start_new_run()


@pytest.fixture(scope="session")
def token_tracker(test_run_dir):
    """Session-scoped token usage tracker.

    Accumulates prompt/completion tokens and simulated cost across all
    integration tests in the session. Calls log_summary() at teardown so
    the totals appear in qaai.log and are written to token_usage.jsonl.

    file_path=None ⇒ the tracker resolves its target from settings.telemetry_file_path
    (re-pointed into test_run_dir by start_new_run). Cost rates are read from
    settings (TOKEN_COST_INPUT_PER_M / TOKEN_COST_OUTPUT_PER_M in .env).
    """
    tracker = TokenUsageTracker(
        file_path=None,
        input_cost_per_million=settings.token_cost_input_per_m,
        output_cost_per_million=settings.token_cost_output_per_m,
    )
    yield tracker
    tracker.log_summary()


@pytest.fixture
def real_client(token_tracker):
    """Provide a real OpenAI client for integration tests.

    Security: Validates that PYTEST_BASE_URL is not a production endpoint.
    Injects the session-scoped token_tracker so all LLM calls are recorded.
    """
    api_key = os.getenv("PYTEST_API_KEY")
    base_url = os.getenv("PYTEST_BASE_URL")
    if not api_key:
        pytest.skip("PYTEST_API_KEY not set -- skipping integration test")

    # Security check: prevent accidental use of production endpoints
    if base_url and "prod" in base_url.lower():
        pytest.fail(
            "PYTEST_BASE_URL appears to be a production endpoint. "
            "Integration tests must use test/staging endpoints only."
        )

    return RateLimitOpenAIClient(
        api_key=api_key,
        base_url=base_url,
        telemetry_tracker=token_tracker,
    )


@pytest.fixture
def real_model():
    return os.getenv("PYTEST_MODEL")


@pytest.fixture
async def client():
    """Async HTTP client for API testing.

    Uses ASGITransport to test the application in-process without
    needing to start a separate server. Wraps the app with its lifespan
    context manager to ensure startup events (service initialization) run.
    """
    async with lifespan(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac


@pytest.fixture
def submit_and_wait(client):
    """Drive the async-job review flow: POST -> 202 {job_id} -> poll -> result.

    Reviews now run as background jobs (so the upstream proxy can't 504 on a long
    request). This helper submits, polls GET /jobs/{id} on the shared event loop
    until the job is terminal, then returns the GET /jobs/{id}/result response.
    If submission itself is rejected (validation 4xx, not 202), that response is
    returned unchanged so error-path tests work too.
    """
    import asyncio
    import time

    async def _submit_and_wait(url, *, json=None, files=None, data=None, max_wait=5.0):
        submit = await client.post(url, json=json, files=files, data=data)
        if submit.status_code != 202:
            return submit
        job_id = submit.json()["job_id"]
        deadline = time.monotonic() + max_wait
        while time.monotonic() < deadline:
            status_resp = await client.get(f"/api/v1/jobs/{job_id}")
            if status_resp.json()["status"] in ("completed", "failed"):
                return await client.get(f"/api/v1/jobs/{job_id}/result")
            await asyncio.sleep(0.01)
        raise AssertionError(f"job {job_id} did not finish within {max_wait}s")

    return _submit_and_wait


@pytest.fixture
def hazard_analysis_requirement_id_format():
    return "REQ-PUMP-\\d+"


def _load_test_suite_fixture() -> dict:
    """Load requirement, test_cases, and design_docs from test_suite_review_all_fields.jsonl."""
    fixture_path = Path(__file__).parent / "fixtures" / "external" / "test_suite_review_all_fields.jsonl"
    with fixture_path.open("r", encoding="utf-8") as f:
        data = json.loads(f.readline())
    return {
        "requirement": Requirement.model_validate(data["requirement"]),
        "test_cases": [TestCase.model_validate(tc) for tc in data["test_cases"]],
        "design_docs": [DesignDocument.model_validate(dd) for dd in data.get("design_docs", [])],
    }


@pytest.fixture
def sample_requirement():
    return _load_test_suite_fixture()["requirement"]


@pytest.fixture
def sample_test_cases():
    return _load_test_suite_fixture()["test_cases"]


@pytest.fixture
def sample_design_docs():
    return _load_test_suite_fixture()["design_docs"]


@pytest.fixture
def sample_decomposed_requirement(sample_requirement):
    specs = [
        DecomposedSpec(
            spec_id="S-001",
            description="Alert fires when reading > 100 mg/dL",
            acceptance_criteria="Alert visible within 1s",
            rationale="Happy path",
        ),
        DecomposedSpec(
            spec_id="S-002",
            description="No alert when reading <= 100 mg/dL",
            acceptance_criteria="No alert at exactly 100 mg/dL",
            rationale="Boundary",
        ),
    ]
    return DecomposedRequirement(
        requirement=sample_requirement,
        decomposed_specifications=specs,
    )


@pytest.fixture
def sample_test_suite(sample_requirement, sample_test_cases):
    summaries = [
        SummarizedTestCase(
            test_case_id=tc.test_id,
            objective=tc.description,
            verifies=sample_requirement.req_id,
            protocol=[tc.steps],
            acceptance_criteria=[tc.expectedResults],
        )
        for tc in sample_test_cases
    ]
    return TestSuite(
        requirement=sample_requirement,
        test_cases=sample_test_cases,
        summary=summaries,
    )


def _load_hazard_fixture(
    include_design_docs: bool,
    gids_format: str,
    excel_file: str = "software_hazard_analysis.xlsx",
    pyjama_file: str = "pyjama_response_unified.jsonl",
) -> HazardRowWithTraceMatrix:
    """Assemble HazardRowWithTraceMatrix from Excel + unified pyjama traceability response.

    excel_file / pyjama_file are fixture filenames resolved across
    tests/fixtures/{local,external,...} via resolve_fixture_path.
    """
    from qaai.agents.hazard_risk_reviewer.loader import (
        parse_sha_excel,
        merge_hazard_with_pyjama_traceability,
    )
    from qaai.agents.hazard_risk_reviewer.core import HazardTraceMatrix

    excel_results = parse_sha_excel(
        file_path=str(resolve_fixture_path(excel_file)),
        extract_gids_format=gids_format,
    )
    excel_rows = excel_results.rows
    if not excel_rows:
        raise ValueError("No hazard rows found in Excel file")

    pyjama_lookup = {}
    with resolve_fixture_path(pyjama_file).open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                data = json.loads(line)
                req_id = data.get("requirement", {}).get("req_id")
                if req_id:
                    pyjama_lookup[req_id] = data

    enhanced_row = merge_hazard_with_pyjama_traceability(excel_rows[0], pyjama_lookup)

    if not include_design_docs:
        trace = enhanced_row.requirements_traceability
        new_trace = HazardTraceMatrix(
            requirements=trace.requirements if trace else [],
            test_cases=trace.test_cases if trace else [],
            design_docs=[],
            user_needs=[],
            system_requirements=trace.system_requirements if trace else [],
        )
        enhanced_row = HazardRowWithTraceMatrix(
            **enhanced_row.model_dump(exclude={"requirements_traceability"}),
            requirements_traceability=new_trace,
        )

    return enhanced_row


@pytest.fixture
def hazard_full_traceability(request, hazard_analysis_requirement_id_format):
    """Full-traceability HazardRowWithTraceMatrix: requirements, test_cases, design_docs, user_needs,
    and system_requirements all populated.

    Used for the M1-M5 + R6 (6 findings per requirement) test path. Honors the
    --input-file (Excel SHA workbook) and --pyjama-file (traceability JSONL)
    options, falling back to the standard fixtures when omitted.
    """
    excel_file = request.config.getoption("--input-file") or "software_hazard_analysis.xlsx"
    pyjama_file = request.config.getoption("--pyjama-file") or "pyjama_response_unified.jsonl"
    return _load_hazard_fixture(
        include_design_docs=True,
        gids_format=hazard_analysis_requirement_id_format,
        excel_file=excel_file,
        pyjama_file=pyjama_file,
    )


def _recorder_fixture(viewer_fn: str, label: str):
    """Factory for session-scoped JSONL recording fixtures.

    Clears inputs.jsonl / outputs.jsonl at session start, yields
    (record_input, record_output) append functions, then auto-generates the
    appropriate HTML viewer at session teardown.
    """
    import importlib

    @pytest.fixture(scope="session")
    def _fixture(test_run_dir):
        inputs_path = test_run_dir / INPUT_JSONL_FILENAME
        outputs_path = test_run_dir / OUTPUT_JSONL_FILENAME
        inputs_path.write_text("", encoding="utf-8")
        outputs_path.write_text("", encoding="utf-8")

        def record_input(data: dict) -> None:
            with inputs_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(data) + "\n")

        def record_output(data: dict) -> None:
            with outputs_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(data) + "\n")

        yield record_input, record_output

        try:
            mod = importlib.import_module("qaai.viewer.generator")
            out = getattr(mod, viewer_fn)(outputs_path)
        except Exception as exc:
            print(f"\n[{label}] skipped: {exc}")
        else:
            if out is not None:
                print(f"\n[{label}] wrote {out}")

    _fixture.__name__ = label
    return _fixture


jsonl_recorders    = _recorder_fixture("write_viewer",    "jsonl_recorders")
jsonl_recorders_tc = _recorder_fixture("write_viewer_tc", "jsonl_recorders_tc")
jsonl_recorders_hz = _recorder_fixture("write_viewer_hz", "jsonl_recorders_hz")


# ===========================================================================
# Graph-invocation settings + stub client
# ===========================================================================


@pytest.fixture
def review_settings(request):
    """Default reviewer graph settings, overridable from the command line.

    Centralizes the knobs tests pass into a reviewer graph (cache_mode,
    test_mode, include_edge_case_analysis) so individual tests don't hard-code
    them. Defaults come from the CLI options registered in pytest_addoption and
    can be overridden per run, e.g. `uv run pytest -m unit --cache-mode on`.
    """
    from types import SimpleNamespace

    return SimpleNamespace(
        cache_mode=request.config.getoption("--cache-mode"),
        test_mode=_as_bool(request.config.getoption("--test-mode")),
        include_edge_case_analysis=_as_bool(
            request.config.getoption("--include-edge-case-analysis")
        ),
    )


class _StubLLMClient:
    """Call-counting stand-in for RateLimitOpenAIClient — makes no network calls.

    Mimics the OpenAI ChatCompletion response shape consumed by the node base
    classes (``choices[0].message.content`` + ``usage``). ``call_count`` lets a
    test assert that a graph short-circuited before any inference
    (``stub_llm_client.call_count == 0``). The returned content is intentionally
    minimal — these tests exercise the input-gate skip paths, which never call
    the client.
    """

    def __init__(self, content: str = "{}"):
        self.call_count = 0
        self._content = content
        self.telemetry_tracker = None

    async def chat_completion(self, *args, **kwargs):
        from types import SimpleNamespace

        self.call_count += 1
        usage = SimpleNamespace(prompt_tokens=0, completion_tokens=0)
        choice = SimpleNamespace(message=SimpleNamespace(content=self._content))
        return SimpleNamespace(choices=[choice], usage=usage)


@pytest.fixture
def stub_llm_client():
    """A fresh call-counting stub LLM client (realizes review_settings.test_mode)."""
    return _StubLLMClient()


# ===========================================================================
# JAMA bidirectional-trace sample data (moved here from
# tests/unit/test_hazard_bidirectional_transform.py per the conftest-only
# fixture convention)
# ===========================================================================


@pytest.fixture
def jama_data():
    """Two-entry bidirectional_trace response with overlapping artifacts.

    REQ-1 and REQ-2 both trace to TC-1 and DD-1 (shared) so we can assert
    deduplication; REQ-2 adds TC-2. SYS-1 is shared across both entries and
    carries nested user needs UND-1 (shared) and UND-2 (unique).
    """
    return [
        {
            "requirement": {"req_id": "REQ-1", "text": "Req one text"},
            "system_requirements": [
                {
                    "req_id": "SYS-1",
                    "text": "System req one",
                    "user_needs": [{"req_id": "UND-1", "text": "Need one"}],
                }
            ],
            "test_cases": [
                {
                    "test_id": "TC-1",
                    "description": "Test one",
                    "setup": "s",
                    "steps": "st",
                    "expectedResults": "er",
                    "in_review_baseline": True,
                }
            ],
            "design_docs": [{"doc_id": "DD-1", "name": "Design one", "description": "d"}],
        },
        {
            "requirement": {"req_id": "REQ-2", "text": "Req two text"},
            "system_requirements": [
                {
                    "req_id": "SYS-1",
                    "text": "System req one",
                    "user_needs": [
                        {"req_id": "UND-1", "text": "Need one"},
                        {"req_id": "UND-2", "text": "Need two"},
                    ],
                }
            ],
            "test_cases": [
                {"test_id": "TC-1", "description": "Test one", "setup": "s",
                 "steps": "st", "expectedResults": "er", "in_review_baseline": True},
                {"test_id": "TC-2", "description": "Test two", "in_review_baseline": False},
            ],
            "design_docs": [{"doc_id": "DD-1", "name": "Design one", "description": "d"}],
        },
    ]


@pytest.fixture
def bare_hazard():
    """A hazard row with empty traceability (the Excel-parsed starting point)."""
    return HazardRowWithTraceMatrix(
        hazard_id="HAZ-1",
        requirements_traceability=HazardTraceMatrix(),
    )


# ===========================================================================
# Bad-input fixtures for the input-gate (skip) tests
# ===========================================================================
#
# Each returns the graph-input dict (sans cache_mode) for an invalid-input
# scenario. Tests merge in cache_mode from review_settings and assert the graph
# skips early (review_status == "skipped", zero LLM calls) — or, for the
# "completed" case, that the gate lets the graph proceed.


@pytest.fixture
def rtm_input_no_test_cases():
    """RTM: a requirement with text but zero traced test cases -> skip."""
    return {
        "requirement": Requirement(req_id="REQ-1", text="The system shall do X."),
        "test_cases": [],
    }


@pytest.fixture
def rtm_input_no_requirement_text():
    """RTM: traced test cases but blank requirement text -> skip."""
    return {
        "requirement": Requirement(req_id="REQ-1", text=""),
        "test_cases": [
            TestCase(test_id="TC-1", description="d", setup="s", steps="st",
                     expectedResults="er"),
        ],
    }


@pytest.fixture
def rtm_input_no_design_docs():
    """RTM: valid requirement + test cases, no design docs -> proceeds (no skip)."""
    return {
        "requirement": Requirement(req_id="REQ-1", text="The system shall do X."),
        "test_cases": [
            TestCase(test_id="TC-1", description="d", setup="s", steps="st",
                     expectedResults="er"),
        ],
        "design_docs": [],
    }


@pytest.fixture
def tc_input_no_requirements():
    """TC: a test case with steps but zero upstream requirements -> skip."""
    return {
        "test_case": TestCase(test_id="TC-1", description="d", setup="s", steps="st",
                              expectedResults="er"),
        "requirements": [],
    }


@pytest.fixture
def tc_input_no_steps():
    """TC: upstream requirements present but the test case has no step text -> skip."""
    return {
        "test_case": TestCase(test_id="TC-1", description="d", setup="s", steps="",
                              expectedResults="er"),
        "requirements": [Requirement(req_id="REQ-1", text="The system shall do X.")],
    }


def _full_hazard(**overrides) -> HazardRowWithTraceMatrix:
    """Build a fully-populated HazardRowWithTraceMatrix (all required fields set,
    one traced control requirement). `overrides` blank/replace specific fields."""
    fields = {f: f"{f} value" for f in HAZARD_RISK_REVIEWER_REQUIRED_HAZARD_FIELDS}
    fields.update(overrides)
    trace = HazardTraceMatrix(requirements=[Requirement(req_id="REQ-1", text="ctrl")])
    return HazardRowWithTraceMatrix(
        **fields,
        row_specific_controls_references=["REQ-1"],
        requirements_traceability=trace,
    )


@pytest.fixture
def hazard_input_no_controls():
    """Hazard: all required fields present but no traced control requirements -> skip."""
    hazard = _full_hazard()
    hazard = hazard.model_copy(update={
        "row_specific_controls_references": [],
        "requirements_traceability": HazardTraceMatrix(),
    })
    return {"hazard": hazard}


@pytest.fixture
def hazard_input_missing_fields():
    """Hazard: required fields blanked out -> skip (lists the missing field names)."""
    # Blank a representative subset of required fields.
    blanked = {"harm": "", "severity": "", "final_risk_rating": ""}
    return {"hazard": _full_hazard(**blanked)}


