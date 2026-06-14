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
from qaai.core.telemetry import TokenUsageTracker

# All test-run artifacts (logs, JSONL records, telemetry, viewers, graph pngs) go
# under logs/tests/ to keep them separate from production/API-server runs under
# logs/. Setting this BEFORE importing qaai.api.main (below) ensures both the
# import-time create_app() and every start_new_run() resolve to logs/tests.
settings.log_base_dir = "./logs/tests"

from qaai.components.clients import (
    RateLimitOpenAIClient
)

from qaai.components.hazard_risk_reviewer.core import (
    HazardRowWithTraceMatrix
)

from qaai.components.test_suite_reviewer.core import (
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
    from qaai.components.hazard_risk_reviewer.loader import (
        parse_sha_excel,
        merge_hazard_with_pyjama_traceability,
    )
    from qaai.components.hazard_risk_reviewer.core import HazardTraceMatrix

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
        inputs_path = test_run_dir / "inputs.jsonl"
        outputs_path = test_run_dir / "outputs.jsonl"
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


