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
from autoqa.utils import make_output_directory

# All test-run artifacts (logs, JSONL records, telemetry) go under logs/tests/
# to keep them separate from production/API-server runs under logs/.
_TEST_RUN_DIR = Path(make_output_directory("./logs/tests"))
_TEST_LOG_FILE = str(_TEST_RUN_DIR / "autoqa.log")

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
        file_path=str(_TEST_RUN_DIR / "token_usage.jsonl"),
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
def hazard_analysis_requirement_id_format():
    return "REQ-PUMP-\\d+"


@pytest.fixture(scope="session", autouse=True)
def configure_test_logger():
    """Configure the logger for test runs to write to the run directory's autoqa.log.
    This is autouse=True so it runs automatically for all test sessions."""
    # Configure the test pipeline logger
    test_logger = ProjectLogger("autoqa.test.pipeline", _TEST_LOG_FILE).config()
    
    # Also configure other loggers that might be used
    for logger_name in ["autoqa.hazard_pipeline", "autoqa.api.rtm", "autoqa.api.hazard"]:
        logger = logging.getLogger(logger_name)
        if not logger.handlers:  # Only add handlers if not already configured
            proj_logger = ProjectLogger(logger_name, _TEST_LOG_FILE).config()
    
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


def _recorder_fixture(viewer_fn: str, label: str):
    """Factory for session-scoped JSONL recording fixtures.

    Clears inputs.jsonl / outputs.jsonl at session start, yields
    (record_input, record_output) append functions, then auto-generates the
    appropriate HTML viewer at session teardown.
    """
    import importlib

    @pytest.fixture(scope="session")
    def _fixture():
        inputs_path = _TEST_RUN_DIR / "inputs.jsonl"
        outputs_path = _TEST_RUN_DIR / "outputs.jsonl"
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
            mod = importlib.import_module("autoqa.viewer.generator")
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


