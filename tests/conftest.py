import truststore
truststore.inject_into_ssl()

import json
import logging
import os
from pathlib import Path
import pytest
from dotenv import load_dotenv

load_dotenv()

from httpx import AsyncClient, ASGITransport

from autoqa.core.config import settings
from autoqa.core.telemetry import TokenUsageTracker
from autoqa.prj_logger import ProjectLogger

from autoqa.components.clients import (
    RateLimitOpenAIClient
)

from autoqa.components.hazard_risk_reviewer.core import (
    HazardRowWithTraceMatrix
)

from autoqa.components.test_suite_reviewer.core import (
    Requirement,
    TestCase,
    DecomposedSpec,
    DecomposedRequirement,
    SummarizedTestCase,
    TestSuite,
    EvaluatedSpec,
    DesignDocument,
)

from autoqa.api.main import app, lifespan


@pytest.fixture(scope="session")
def token_tracker():
    """Session-scoped token usage tracker.

    Accumulates prompt/completion tokens and simulated cost across all
    integration tests in the session. Calls log_summary() at teardown so
    the totals appear in autoqa.log and are written to token_usage.jsonl.

    Cost rates are read from settings (TOKEN_COST_INPUT_PER_M /
    TOKEN_COST_OUTPUT_PER_M in .env). Defaults: $0.15 / $0.60 per 1M tokens.
    """
    tracker = TokenUsageTracker(
        file_path=settings.telemetry_file_path,
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
def hazard_analysis_wb_sheetname():
    return "SHA Table"

@pytest.fixture
def hazard_analysis_requirement_id_format():
    return "REQ-PUMP-\\d+"


@pytest.fixture(scope="session", autouse=True)
def configure_test_logger():
    """Configure the logger for test runs to write to the run directory's autoqa.log.
    This is autouse=True so it runs automatically for all test sessions."""
    log_file = settings.log_file_path
    
    # Configure the test pipeline logger
    test_logger = ProjectLogger("autoqa.test.pipeline", log_file).config()
    
    # Also configure other loggers that might be used
    for logger_name in ["autoqa.hazard_pipeline", "autoqa.api.rtm", "autoqa.api.hazard"]:
        logger = logging.getLogger(logger_name)
        if not logger.handlers:  # Only add handlers if not already configured
            proj_logger = ProjectLogger(logger_name, log_file).config()
    
    yield
    
    # Cleanup: flush and close handlers
    for logger_name in ["autoqa.test.pipeline", "autoqa.hazard_pipeline", "autoqa.api.rtm", "autoqa.api.hazard"]:
        logger = logging.getLogger(logger_name)
        for handler in logger.handlers:
            handler.flush()


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


def _load_hazard_fixture(include_design_docs: bool, gids_format: str = "REQ-PUMP-\\d+") -> HazardRowWithTraceMatrix:
    """Assemble HazardRowWithTraceMatrix from Excel + unified pyjama traceability response."""
    from autoqa.components.hazard_risk_reviewer.loader import (
        parse_sha_excel,
        merge_hazard_with_pyjama_traceability,
    )
    from autoqa.components.hazard_risk_reviewer.core import HazardTraceMatrix

    fixtures_dir = Path(__file__).parent / "fixtures" / "external"

    excel_results = parse_sha_excel(
        file_path=str(fixtures_dir / "software_hazard_analysis.xlsx"),
        extract_gids_format=gids_format,
    )
    excel_rows = excel_results.rows
    if not excel_rows:
        raise ValueError("No hazard rows found in Excel file")

    pyjama_lookup = {}
    with (fixtures_dir / "pyjama_response_unified.jsonl").open(encoding="utf-8") as f:
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
def hazard_full_traceability(hazard_analysis_requirement_id_format):
    """Full-traceability HazardRowWithTraceMatrix: requirements, test_cases, design_docs, user_needs,
    and system_requirements all populated.

    Used for the M1-M5 + R6 (6 findings per requirement) test path.
    """
    return _load_hazard_fixture(include_design_docs=True, gids_format=hazard_analysis_requirement_id_format)


@pytest.fixture(scope="session")
def jsonl_recorders():
    """Session-scoped fixture that clears inputs.jsonl / outputs.jsonl once at session
    start, yields (record_input, record_output) append functions, then auto-generates
    the HTML viewer at session teardown if outputs.jsonl has records."""
    run_dir = Path(settings.log_file_path).parent
    inputs_path = run_dir / "inputs.jsonl"
    outputs_path = run_dir / "outputs.jsonl"
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
        from autoqa.viewer import write_viewer
        out = write_viewer(outputs_path)
    except Exception as exc:
        print(f"\n[viewer] skipped: {exc}")
    else:
        if out is not None:
            print(f"\n[viewer] wrote {out}")


@pytest.fixture(scope="session")
def jsonl_recorders_tc():
    """TC-flavored counterpart to jsonl_recorders: same inputs.jsonl/outputs.jsonl
    contract, but renders the test-case viewer (viewer_tc.html) at session teardown
    via write_viewer_tc instead of the RTM write_viewer."""
    run_dir = Path(settings.log_file_path).parent
    inputs_path = run_dir / "inputs.jsonl"
    outputs_path = run_dir / "outputs.jsonl"
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
        from autoqa.viewer.generator import write_viewer_tc
        out = write_viewer_tc(outputs_path)
    except Exception as exc:
        print(f"\n[viewer_tc] skipped: {exc}")
    else:
        if out is not None:
            print(f"\n[viewer_tc] wrote {out}")


@pytest.fixture(scope="session")
def jsonl_recorders_hz():
    """Hazard-flavored counterpart to jsonl_recorders: same inputs.jsonl/outputs.jsonl
    contract, but renders the hazard viewer (viewer_hz.html) at session teardown
    via write_viewer_hz instead of the RTM write_viewer.
    
    Use this fixture for hazard_risk_reviewer integration tests to generate
    a viewer that displays HazardReviewState records with H1-H7 findings.
    """
    run_dir = Path(settings.log_file_path).parent
    inputs_path = run_dir / "inputs.jsonl"
    outputs_path = run_dir / "outputs.jsonl"
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
        from autoqa.viewer.generator import write_viewer_hz
        out = write_viewer_hz(outputs_path)
    except Exception as exc:
        print(f"\n[viewer_hz] skipped: {exc}")
    else:
        if out is not None:
            print(f"\n[viewer_hz] wrote {out}")


