"""Integration test for the hazard risk reviewer pipeline.

Mirrors test_pipeline_full_state in tests/integration/test_pipeline.py:
runs the full graph against a real LLM, asserts the final state shape, and
writes hazard_pipeline_state.json under the active run directory for
manual inspection (and downstream review via the
review-hazard-mitigation-coverage skill).

Includes parallelism verification test to confirm H1, H2, H3, H7 run
concurrently with requirement_reviewer, H4, H5 wait for requirement_reviews,
and H6 waits for H3, H4, H5.
"""

import json
import tempfile
from pathlib import Path

import pytest
from pydantic import BaseModel

from autoqa.components.hazard_risk_reviewer.core import (
    HazardAssessment,
    HazardReviewState,
    RequirementReview,
)
from autoqa.components.hazard_risk_reviewer.pipeline import HazardReviewerRunnable
from autoqa.components.shared.data_integration import transform_hazard_record_to_state
from autoqa.components.test_suite_reviewer.core import SynthesizedAssessment
from autoqa.core.config import settings


# --- Collection-time row enumeration -----------------------------------------
# pytest resolves parametrization at collection time, before fixtures run, so we
# load the hazard rows here rather than from fixtures. These constants mirror the
# conftest fixtures hazard_analysis_wb_sheetname / hazard_analysis_requirement_id_format,
# which return plain literals.
_FIXTURES_DIR = Path(__file__).parent.parent.parent / "fixtures" / "external"
_EXCEL_FILE = _FIXTURES_DIR / "software_hazard_analysis.xlsx"
_PYJAMA_FILE = _FIXTURES_DIR / "pyjama_response_unified.jsonl"
_SHEET_NAME = "SHA Table"
_GID_FORMAT = "REQ-PUMP-\\d+"


def _load_enhanced_rows():
    """Enumerate HazardRowWithTraceMatrix rows from the Excel fixture for
    parametrization. Writes the transform's JSONL to a throwaway temp path so
    collection never touches the run directory (each item records its own input
    via record_input). Returns [] if fixtures are missing so collection skips
    gracefully instead of erroring."""
    try:
        return transform_hazard_record_to_state(
            excel_file_path=str(_EXCEL_FILE),
            pyjama_response_file_path=str(_PYJAMA_FILE),
            output_jsonl_path=str(Path(tempfile.gettempdir()) / "hazard_rows_collection.jsonl"),
            sheet_name=_SHEET_NAME,
            extract_gids_format=_GID_FORMAT,
        )
    except FileNotFoundError:
        return []


_ENHANCED_ROWS = _load_enhanced_rows()


def _serialize_hazard_state(state: dict) -> dict:
    """
    Recursive Pydantic-aware serializer for HazardReviewState. Mirrors
    tests/helpers.py::serialize_state but handles nested BaseModel lists
    inside RequirementReview.
    """
    out: dict = {}
    for key, value in state.items():
        if isinstance(value, BaseModel):
            out[key] = value.model_dump()
        elif isinstance(value, list):
            out[key] = [
                v.model_dump() if isinstance(v, BaseModel) else v
                for v in value
            ]
        else:
            out[key] = value
    return out


@pytest.mark.integration
@pytest.mark.parametrize(
    "hazard_fixture_name,expected_findings_per_req",
    [
        ("hazard_full_traceability", 6),  # all fields: M1-M5 + R6 (with design_docs)
    ],
)
async def test_hazard_risk_reviewer(real_client, real_model, hazard_fixture_name, expected_findings_per_req, jsonl_recorders_hz, request):
    """Run the full hazard pipeline end-to-end against a real LLM.
    
    Parametrized to test both:
    - Min fields (sample_hazard): No design_docs or user_needs, produces M1-M5 + R6 N-A
    - All fields (hazard_full_traceability): Full traceability with design_docs,
      user_needs, system_requirements, produces M1-M5 + R6
    
    Expected behavior:
    - RTM sub-pipeline receives design_docs (if present) and produces R6 verdicts
    - User needs are summarized by HazardNeedsSummarizerNode (if present)
    - Design docs are summarized by HazardDesignSummarizerNode (if present)
    - H4 evaluator receives summarized_designs in its payload (if present)
    - H5 evaluator receives summarized_user_needs in its payload (if present)
    - Each requirement review has expected number of mandatory findings
    - Overall hazard assessment has 7 findings (H1-H7)
    
    Records input/output to inputs.jsonl and outputs.jsonl for hazard viewer generation.
    """
    # Get the fixture dynamically
    hazard = request.getfixturevalue(hazard_fixture_name)
    
    record_input, record_output = jsonl_recorders_hz
    
    graph = HazardReviewerRunnable(client=real_client, model=real_model)
    initial_state: HazardReviewState = {"hazard": hazard}
    
    # Record input
    record_input({"hazard": hazard.model_dump()})
    
    result: HazardReviewState = await graph.graph.ainvoke(initial_state)
    
    # Record output
    record_output(_serialize_hazard_state(result))

    # Per-requirement RTM evidence — one review per traced requirement.
    reviews = result.get("requirement_reviews", [])
    assert len(reviews) == len(hazard.requirements_traceability.requirements), \
        f"Expected {len(hazard.requirements_traceability.requirements)} reviews, got {len(reviews)}"
    
    for r in reviews:
        assert isinstance(r, RequirementReview), f"Expected RequirementReview, got {type(r)}"
        # The wrapped RTM subgraph should have produced an assessment
        # for each requirement (it may be None on parser failure, but should
        # populate for a well-formed sample).
        assert isinstance(r.synthesized_assessment, SynthesizedAssessment), \
            f"Expected SynthesizedAssessment for {r.requirement.req_id}, got {type(r.synthesized_assessment)}"
        
        # Verify expected number of mandatory findings
        num_findings = len(r.synthesized_assessment.mandatory_findings)
        assert num_findings == expected_findings_per_req, \
            f"Expected {expected_findings_per_req} mandatory findings for {r.requirement.req_id}, got {num_findings}"
        
        # If design_docs present, verify R6 is present and has Yes/No verdict
        if expected_findings_per_req == 6:
            r6_finding = next((f for f in r.synthesized_assessment.mandatory_findings if f.code == "R6"), None)
            assert r6_finding is not None, \
                f"R6 finding should be present for {r.requirement.req_id} when design_docs are provided"
            assert r6_finding.verdict in ("Yes", "No"), \
                f"R6 verdict should be Yes or No (not N-A) for {r.requirement.req_id}, got {r6_finding.verdict}"

    # Verify summarized_designs were produced (if design_docs present)
    summarized_designs = result.get("summarized_designs")
    if hazard.requirements_traceability.design_docs:
        assert summarized_designs is not None, "Expected summarized_designs when design_docs are present"
        assert len(summarized_designs) > 0, "Expected at least one summarized design"
        print(f"\n[{hazard_fixture_name}] Produced {len(summarized_designs)} summarized designs from {len(hazard.requirements_traceability.design_docs)} design docs")
    
    # Verify summarized_user_needs were produced (if user_needs present)
    summarized_user_needs = result.get("summarized_user_needs")
    if hazard.requirements_traceability.user_needs:
        assert summarized_user_needs is not None, "Expected summarized_user_needs when user_needs are present"
        assert len(summarized_user_needs) > 0, "Expected at least one summarized user need"
        print(f"[{hazard_fixture_name}] Produced {len(summarized_user_needs)} summarized user needs from {len(hazard.requirements_traceability.user_needs)} user needs")

    # Hazard-level H1-H7 verdict (binary Yes/No; H5 may also be N-A).
    assessment = result.get("hazard_assessment")
    assert isinstance(assessment, HazardAssessment), f"Expected HazardAssessment, got {type(assessment)}"
    assert assessment.hazard_id == hazard.hazard_id
    assert assessment.overall_verdict in ("Yes", "No"), \
        f"Expected overall_verdict to be Yes or No, got {assessment.overall_verdict}"
    
    num_hazard_findings = len(assessment.mandatory_findings)
    assert num_hazard_findings == 7, \
        f"Expected 7 hazard-level findings (H1-H7), got {num_hazard_findings}"
    
    actual_codes = [f.code for f in assessment.mandatory_findings]
    expected_codes = ["H1", "H2", "H3", "H4", "H5", "H6", "H7"]
    assert actual_codes == expected_codes, \
        f"Expected codes {expected_codes}, got {actual_codes}"
    
    expected_dimensions = [
        "Hazard Record Completeness and Semantic Integrity",
        "Software Contribution and Cause Coverage",
        "Pre-Mitigation Risk and Exploitability Characterization",
        "Risk Control Identification, Allocation, and Coverage",
        "Verification Depth and Hazard-Path Effectiveness",
        "Residual Risk Closure and Acceptability Decision",
        "HSHA Update and Newly Identified Hazard / Hazardous Situation Capture",
    ]
    actual_dimensions = [f.dimension for f in assessment.mandatory_findings]
    assert actual_dimensions == expected_dimensions, \
        f"Dimension mismatch. Expected {expected_dimensions}, got {actual_dimensions}"
    
    for f in assessment.mandatory_findings:
        if f.code == "H5":
            assert f.verdict in ("Yes", "No", "N-A"), \
                f"H5 verdict should be Yes, No, or N-A, got {f.verdict}"
        else:
            assert f.verdict in ("Yes", "No"), \
                f"{f.code} verdict should be Yes or No, got {f.verdict}"
    
    # overall_verdict invariant: Yes iff every dimension is Yes or N-A.
    expected_overall = "Yes" if all(
        f.verdict in ("Yes", "N-A") for f in assessment.mandatory_findings
    ) else "No"
    assert assessment.overall_verdict == expected_overall, \
        f"Overall verdict mismatch. Expected {expected_overall}, got {assessment.overall_verdict}"

    # Save detailed state for manual inspection
    output_path = Path(settings.log_file_path).parent / "hazard_pipeline_state.json"
    output_path.write_text(json.dumps(_serialize_hazard_state(result), indent=2))
    
    # Print summary
    print(f"\n[{hazard_fixture_name}] saved → {output_path}")
    print(f"[{hazard_fixture_name}] hazard_id = {assessment.hazard_id}")
    print(f"[{hazard_fixture_name}] overall_verdict = {assessment.overall_verdict}")
    print(f"[{hazard_fixture_name}] Hazard-level findings (H1-H7):")
    for f in assessment.mandatory_findings:
        print(f"  {f.code} {f.dimension}: {f.verdict} — {f.rationale}")
    print(f"[{hazard_fixture_name}] Requirement-level findings ({expected_findings_per_req} per req):")
    for r in reviews:
        print(f"  {r.requirement.req_id}: {len(r.synthesized_assessment.mandatory_findings)} findings")
        for f in r.synthesized_assessment.mandatory_findings:
            print(f"    {f.code}: {f.verdict}")
    
    # Note: inputs.jsonl and outputs.jsonl are recorded for hazard viewer generation
    # The hazard viewer (viewer_hz.html) will be auto-generated at session teardown
    # by the jsonl_recorders_hz fixture


def _validate_hazard_assessment(output_state: dict, row_index: int) -> dict:
    """
    Validate a single hazard output state and extract summary info.
    
    Args:
        output_state: Result state from graph.ainvoke()
        row_index: Row number for logging
    
    Returns:
        Summary dict with validation results
    
    Raises:
        AssertionError: If validation fails
    """
    assessment = output_state.get("hazard_assessment")
    assert isinstance(assessment, HazardAssessment), \
        f"[Row {row_index}] Expected HazardAssessment, got {type(assessment)}"
    
    assert assessment.overall_verdict in ("Yes", "No"), \
        f"[Row {row_index}] overall_verdict must be Yes or No, got {assessment.overall_verdict}"
    
    num_findings = len(assessment.mandatory_findings)
    assert num_findings == 7, \
        f"[Row {row_index}] Expected 7 findings (H1-H7), got {num_findings}"
    
    # Validate each finding
    for finding in assessment.mandatory_findings:
        if finding.code == "H5":
            assert finding.verdict in ("Yes", "No", "N-A"), \
                f"[Row {row_index}] H5 verdict must be Yes, No, or N-A, got {finding.verdict}"
        else:
            assert finding.verdict in ("Yes", "No"), \
                f"[Row {row_index}] {finding.code} verdict must be Yes or No, got {finding.verdict}"
    
    # Validate overall_verdict invariant
    expected_overall = "Yes" if all(
        f.verdict in ("Yes", "N-A") for f in assessment.mandatory_findings
    ) else "No"
    assert assessment.overall_verdict == expected_overall, \
        f"[Row {row_index}] overall_verdict={assessment.overall_verdict} contradicts findings"
    
    # Extract summary
    verdicts = {f.code: f.verdict for f in assessment.mandatory_findings}
    
    return {
        "hazard_id": assessment.hazard_id,
        "overall_verdict": assessment.overall_verdict,
        "verdicts": verdicts,
        "num_requirements": len(output_state.get("requirement_reviews", [])),
    }


@pytest.mark.integration
@pytest.mark.parametrize(
    "enhanced_row",
    _ENHANCED_ROWS,
    ids=[row.hazard_id or f"row{i}" for i, row in enumerate(_ENHANCED_ROWS)],
)
async def test_hazard_risk_reviewer_batch_via_transformation(
    enhanced_row, real_client, real_model, jsonl_recorders_hz
):
    """Review one Excel-derived hazard row end-to-end against a real LLM.

    The hazard rows from software_hazard_analysis.xlsx are enumerated at
    collection time (see _ENHANCED_ROWS) and parametrized so each row is its own
    pytest item (id = hazard_id) — they pass/fail independently and can be
    selected individually. Rows run sequentially (pytest default), so the
    req_id-keyed disk cache populated by earlier rows is reused by later ones.
    """
    record_input, record_output = jsonl_recorders_hz
    record_input({"hazard": enhanced_row.model_dump()})

    graph = HazardReviewerRunnable(client=real_client, model=real_model)
    output_state = await graph.graph.ainvoke({"hazard": enhanced_row})
    record_output(_serialize_hazard_state(output_state))

    hazard_id = enhanced_row.hazard_id or "<no-id>"
    summary = _validate_hazard_assessment(output_state, hazard_id)

    # Per-requirement RTM evidence — each traced requirement must have a
    # synthesized assessment from the wrapped RTM subgraph.
    reviews = output_state.get("requirement_reviews", [])
    for review in reviews:
        assert isinstance(review, RequirementReview), \
            f"[{hazard_id}] Expected RequirementReview, got {type(review)}"
        assert review.synthesized_assessment is not None, \
            f"[{hazard_id}] synthesized_assessment is None for {review.requirement.req_id}"
        assert isinstance(review.synthesized_assessment, SynthesizedAssessment), \
            f"[{hazard_id}] Expected SynthesizedAssessment, got {type(review.synthesized_assessment)}"

    print(
        f"  {summary['hazard_id']} -> {summary['overall_verdict']} "
        f"({len(reviews)} reqs) | {summary['verdicts']}"
    )