import truststore
truststore.inject_into_ssl()

import json
import logging
import os
from pathlib import Path
import pytest
from dotenv import load_dotenv
load_dotenv()
from autoqa.core.config import settings
from autoqa.prj_logger import ProjectLogger

from autoqa.components.clients import RateLimitOpenAIClient
from autoqa.components.hazard_risk_reviewer.core import HazardRecord
from autoqa.components.test_suite_reviewer.core import (
    Requirement,
    TestCase,
    DecomposedSpec,
    DecomposedRequirement,
    SummarizedTestCase,
    TestSuite,
    EvaluatedSpec,
)

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


@pytest.fixture
def sample_requirement():
    return Requirement(
        req_id="REQ-001",
        text="The system shall display an alert when sensor reading exceeds 100 mg/dL.",
    )

@pytest.fixture
def sample_test_cases():
    return [
        TestCase(
            test_id="TC-001",
            description="Verify alert fires above threshold",
            setup="Sensor connected",
            steps="Set reading to 105",
            expectedResults="Alert displayed",
        ),
        TestCase(
            test_id="TC-002",
            description="Verify no alert below threshold",
            setup="Sensor connected",
            steps="Set reading to 95",
            expectedResults="No alert",
        ),
    ]


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
            test_case_id="TC-001",
            objective="Verify alert above threshold",
            verifies="REQ-001",
            protocol=["Set reading to 105", "Check UI for alert"],
            acceptance_criteria=["Alert shown within 1s"],
        ),
        SummarizedTestCase(
            test_case_id="TC-002",
            objective="Verify no alert below threshold",
            verifies="REQ-001",
            protocol=["Set reading to 95", "Check UI"],
            acceptance_criteria=["No alert displayed"],
        ),
    ]
    return TestSuite(
        requirement=sample_requirement,
        test_cases=sample_test_cases,
        summary=summaries,
    )


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


@pytest.fixture
def real_client():
    """Provide a real OpenAI client for integration tests.
    
    Security: Validates that PYTEST_BASE_URL is not a production endpoint.
    """
    api_key = os.getenv("PYTEST_API_KEY")
    base_url = os.getenv("PYTEST_BASE_URL")
    if not api_key:
        pytest.skip("PYTEST_API_KEY not set — skipping integration test")
    
    # Security check: prevent accidental use of production endpoints
    if base_url and "prod" in base_url.lower():
        pytest.fail(
            "PYTEST_BASE_URL appears to be a production endpoint. "
            "Integration tests must use test/staging endpoints only."
        )
    
    return RateLimitOpenAIClient(api_key=api_key, base_url=base_url)


@pytest.fixture
def real_model():
    return os.getenv("PYTEST_MODEL")

@pytest.fixture
def sample_hazard():
    """Load the canonical sample HazardRecord from tests/fixtures/external/sample_hazard.json."""
    fixture_path = Path(__file__).parent / "fixtures" / "external" / "sample_hazard.json"
    with fixture_path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return HazardRecord.model_validate(data)
