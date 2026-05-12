"""Integration tests for test_suite_reviewer with design documents.

Tests the R6 Design Alignment criterion added in v8.0.0 of the synthesizer.
"""
import asyncio
import json
import logging
import pytest
from pathlib import Path
from autoqa.core.config import settings
from autoqa.components.test_suite_reviewer.pipeline import RTMReviewerRunnable
from autoqa.components.test_suite_reviewer.core import (
    Requirement, TestCase, DesignDocument, SynthesizedAssessment,
)


@pytest.fixture
def sample_requirement_with_design():
    """Requirement that should have design alignment."""
    return Requirement(
        req_id="REQ-DESIGN-001",
        text=(
            "The system shall display a real-time glucose alert when the sensor "
            "reading exceeds 180 mg/dL. The alert shall be visible within 1 second "
            "and shall include the current reading value and timestamp."
        ),
    )


@pytest.fixture
def sample_test_cases_with_design():
    """Test cases covering the requirement."""
    return [
        TestCase(
            test_id="TC-DESIGN-001",
            description="Verify alert fires when reading exceeds threshold",
            setup="Glucose sensor connected and calibrated",
            steps="1. Set sensor reading to 185 mg/dL\n2. Observe UI",
            expectedResults="Alert displayed within 1s showing reading and timestamp",
        ),
        TestCase(
            test_id="TC-DESIGN-002",
            description="Verify no alert at threshold boundary",
            setup="Glucose sensor connected",
            steps="1. Set sensor reading to 180 mg/dL\n2. Observe UI",
            expectedResults="No alert displayed",
        ),
        TestCase(
            test_id="TC-DESIGN-003",
            description="Verify no alert below threshold",
            setup="Glucose sensor connected",
            steps="1. Set sensor reading to 175 mg/dL\n2. Observe UI",
            expectedResults="No alert displayed",
        ),
        TestCase(
            test_id="TC-DESIGN-004",
            description="Verify alert content includes required fields",
            setup="Glucose sensor connected",
            steps="1. Set sensor reading to 200 mg/dL\n2. Capture alert content",
            expectedResults="Alert shows: glucose value (200 mg/dL) and timestamp",
        ),
    ]


@pytest.fixture
def sample_design_docs_aligned():
    """Design documents that align with the requirement."""
    return [
        DesignDocument(
            doc_id="DD-ALERT-001",
            name="Glucose Alert System Design",
            description=(
                "The AlertManager component monitors glucose sensor readings in real-time. "
                "When a reading exceeds the configured threshold (180 mg/dL), it triggers "
                "an AlertView component that displays a modal dialog. The AlertView renders "
                "the current glucose value, timestamp, and severity indicator. The alert "
                "rendering pipeline is optimized to complete within 500ms from trigger to "
                "display, ensuring the 1-second requirement is met with margin."
            ),
        ),
        DesignDocument(
            doc_id="DD-SENSOR-001",
            name="Sensor Data Pipeline",
            description=(
                "The SensorDataPipeline receives glucose readings from the hardware sensor "
                "at 1Hz frequency. Each reading is timestamped, validated for range "
                "(20-600 mg/dL), and published to the AlertManager via an event bus. "
                "The pipeline maintains a 10-reading circular buffer for trend analysis."
            ),
        ),
    ]


@pytest.fixture
def sample_design_docs_misaligned():
    """Design documents that do NOT align with the requirement."""
    return [
        DesignDocument(
            doc_id="DD-UNRELATED-001",
            name="User Authentication Module",
            description=(
                "The AuthenticationService handles user login, session management, "
                "and role-based access control. It integrates with LDAP for enterprise "
                "deployments and supports multi-factor authentication."
            ),
        ),
        DesignDocument(
            doc_id="DD-PARTIAL-001",
            name="Data Logging System",
            description=(
                "The DataLogger records all sensor readings to a local SQLite database "
                "for audit and compliance purposes. Logs are retained for 90 days."
            ),
        ),
    ]


@pytest.mark.integration
async def test_pipeline_with_design_docs_aligned(
    real_client, real_model, 
    sample_requirement_with_design, 
    sample_test_cases_with_design,
    sample_design_docs_aligned,
    jsonl_recorders
):
    """Test pipeline with design docs that align with the requirement.
    
    Expected: R6 verdict should be 'Yes' because design docs describe
    the alert system implementation that matches the requirement.
    """
    logger = logging.getLogger("autoqa.test.pipeline")
    model_kwargs = {"max_tokens": settings.max_output_tokens}
    
    graph = RTMReviewerRunnable(
        client=real_client, 
        model=real_model,
        model_kwargs=model_kwargs
    )
    
    initial_state = {
        "requirement": sample_requirement_with_design,
        "test_cases": sample_test_cases_with_design,
        "design_docs": sample_design_docs_aligned,
    }
    
    # Record input for viewer
    record_input, record_output = jsonl_recorders
    from tests.helpers import serialize_state
    record_input(serialize_state(initial_state))
    
    result = await graph.graph.ainvoke(initial_state)
    
    # Record output for viewer
    record_output(serialize_state(result))
    
    # Verify design summarizer ran
    assert result.get("summarized_designs") is not None, "Design summarizer should have run"
    assert len(result["summarized_designs"]) > 0, "Should have summarized design docs"
    
    logger.info(f"\n[design_summarizer] Generated {len(result['summarized_designs'])} summaries:")
    for sd in result["summarized_designs"]:
        logger.info(f"  {sd.doc_id}: {sd.design_intent[:80]}")
    
    # Verify synthesized assessment has 6 findings (M1-M5, R6)
    sa = result.get("synthesized_assessment")
    assert sa is not None, "Should have synthesized assessment"
    assert isinstance(sa, SynthesizedAssessment)
    
    findings = sa.mandatory_findings
    assert len(findings) == 6, f"Expected 6 findings (M1-M5, R6), got {len(findings)}"
    
    codes = [f.code for f in findings]
    assert codes == ["M1", "M2", "M3", "M4", "M5", "R6"], f"Expected M1-M5, R6, got {codes}"
    
    # Find R6 finding
    r6 = next((f for f in findings if f.code == "R6"), None)
    assert r6 is not None, "R6 finding should exist"
    assert r6.dimension == "Design Alignment"
    
    logger.info(f"\n[R6 Finding]")
    logger.info(f"  Verdict: {r6.verdict}")
    logger.info(f"  Rationale: {r6.rationale}")
    
    # R6 should be Yes because design docs align
    assert r6.verdict == "Yes", (
        f"R6 should be 'Yes' with aligned design docs, got '{r6.verdict}'. "
        f"Rationale: {r6.rationale}"
    )
    
    # Verify R6 doesn't affect overall verdict
    # Even if R6 were No, overall_verdict should only depend on M1-M5
    m1_m5_verdicts = [f.verdict for f in findings if f.code != "R6"]
    expected_overall = "Yes" if all(v in ("Yes", "N-A") for v in m1_m5_verdicts) else "No"
    
    assert sa.overall_verdict == expected_overall, (
        f"overall_verdict should be {expected_overall} based on M1-M5 only, "
        f"got {sa.overall_verdict}. R6 should NOT affect overall_verdict."
    )
    
    # Save state for inspection
    output_path = Path(settings.log_file_path).parent / "pipeline_state_with_design_aligned.json"
    from tests.helpers import serialize_state
    output_path.write_text(json.dumps(serialize_state(result), indent=2))
    logger.info(f"\n[full_state] saved → {output_path}")


@pytest.mark.integration
async def test_pipeline_with_design_docs_misaligned(
    real_client, real_model,
    sample_requirement_with_design,
    sample_test_cases_with_design,
    sample_design_docs_misaligned,
    jsonl_recorders
):
    """Test pipeline with design docs that do NOT align with the requirement.
    
    Expected: R6 verdict should be 'No' because design docs don't describe
    the alert system implementation.
    """
    logger = logging.getLogger("autoqa.test.pipeline")
    model_kwargs = {"max_tokens": settings.max_output_tokens}
    
    graph = RTMReviewerRunnable(
        client=real_client,
        model=real_model,
        model_kwargs=model_kwargs
    )
    
    initial_state = {
        "requirement": sample_requirement_with_design,
        "test_cases": sample_test_cases_with_design,
        "design_docs": sample_design_docs_misaligned,
    }
    
    # Record input for viewer
    record_input, record_output = jsonl_recorders
    from tests.helpers import serialize_state
    record_input(serialize_state(initial_state))
    
    result = await graph.graph.ainvoke(initial_state)
    
    # Record output for viewer
    record_output(serialize_state(result))
    
    # Verify design summarizer ran
    assert result.get("summarized_designs") is not None
    assert len(result["summarized_designs"]) > 0
    
    logger.info(f"\n[design_summarizer] Generated {len(result['summarized_designs'])} summaries:")
    for sd in result["summarized_designs"]:
        logger.info(f"  {sd.doc_id}: {sd.design_intent[:80]}")
    
    # Verify R6 finding
    sa = result.get("synthesized_assessment")
    assert sa is not None
    
    findings = sa.mandatory_findings
    assert len(findings) == 6
    
    r6 = next((f for f in findings if f.code == "R6"), None)
    assert r6 is not None
    
    logger.info(f"\n[R6 Finding]")
    logger.info(f"  Verdict: {r6.verdict}")
    logger.info(f"  Rationale: {r6.rationale}")
    
    # R6 should be No because design docs don't align
    assert r6.verdict == "No", (
        f"R6 should be 'No' with misaligned design docs, got '{r6.verdict}'. "
        f"Rationale: {r6.rationale}"
    )
    
    # Verify R6=No doesn't flip overall verdict
    m1_m5_verdicts = [f.verdict for f in findings if f.code != "R6"]
    expected_overall = "Yes" if all(v in ("Yes", "N-A") for v in m1_m5_verdicts) else "No"
    
    assert sa.overall_verdict == expected_overall, (
        f"overall_verdict should be {expected_overall} based on M1-M5 only, "
        f"got {sa.overall_verdict}. R6=No should NOT flip overall_verdict."
    )
    
    # Save state
    output_path = Path(settings.log_file_path).parent / "pipeline_state_with_design_misaligned.json"
    from tests.helpers import serialize_state
    output_path.write_text(json.dumps(serialize_state(result), indent=2))
    logger.info(f"\n[full_state] saved → {output_path}")


@pytest.mark.integration
async def test_pipeline_without_design_docs(
    real_client, real_model,
    sample_requirement_with_design,
    sample_test_cases_with_design,
    jsonl_recorders
):
    """Test pipeline without design docs.
    
    Expected: R6 verdict should be 'N-A' because no design docs exist.
    """
    logger = logging.getLogger("autoqa.test.pipeline")
    model_kwargs = {"max_tokens": settings.max_output_tokens}
    
    graph = RTMReviewerRunnable(
        client=real_client,
        model=real_model,
        model_kwargs=model_kwargs
    )
    
    initial_state = {
        "requirement": sample_requirement_with_design,
        "test_cases": sample_test_cases_with_design,
        # No design_docs provided
    }
    
    # Record input for viewer
    record_input, record_output = jsonl_recorders
    from tests.helpers import serialize_state
    record_input(serialize_state(initial_state))
    
    result = await graph.graph.ainvoke(initial_state)
    
    # Record output for viewer
    record_output(serialize_state(result))
    
    # Verify design summarizer was skipped
    summarized_designs = result.get("summarized_designs")
    assert summarized_designs is None or len(summarized_designs) == 0, (
        "Design summarizer should skip when no design docs provided"
    )
    
    # Verify R6 finding
    sa = result.get("synthesized_assessment")
    assert sa is not None
    
    findings = sa.mandatory_findings
    assert len(findings) == 6
    
    r6 = next((f for f in findings if f.code == "R6"), None)
    assert r6 is not None
    
    logger.info(f"\n[R6 Finding]")
    logger.info(f"  Verdict: {r6.verdict}")
    logger.info(f"  Rationale: {r6.rationale}")
    
    # R6 should be N-A because no design docs exist
    assert r6.verdict == "N-A", (
        f"R6 should be 'N-A' when no design docs exist, got '{r6.verdict}'. "
        f"Rationale: {r6.rationale}"
    )
    
    # Verify overall verdict still works correctly
    m1_m5_verdicts = [f.verdict for f in findings if f.code != "R6"]
    expected_overall = "Yes" if all(v in ("Yes", "N-A") for v in m1_m5_verdicts) else "No"
    
    assert sa.overall_verdict == expected_overall
    
    # Save state
    output_path = Path(settings.log_file_path).parent / "pipeline_state_without_design.json"
    from tests.helpers import serialize_state
    output_path.write_text(json.dumps(serialize_state(result), indent=2))
    logger.info(f"\n[full_state] saved → {output_path}")
