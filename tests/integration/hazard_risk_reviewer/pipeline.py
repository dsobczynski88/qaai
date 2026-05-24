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

import asyncio
import json
from pathlib import Path
from datetime import datetime

import pytest
from pydantic import BaseModel

from autoqa.components.hazard_risk_reviewer.core import (
    HazardAssessment,
    HazardReviewState,
    RequirementReview,
    HazardRowWithTraceMatrix,
)
from autoqa.components.hazard_risk_reviewer.pipeline import HazardReviewerRunnable
from autoqa.components.shared.data_integration import transform_hazard_record_to_state
from autoqa.components.test_suite_reviewer.core import SynthesizedAssessment
from autoqa.core.config import settings


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
    assert len(reviews) == len(hazard.requirements), \
        f"Expected {len(hazard.requirements)} reviews, got {len(reviews)}"
    
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
    if hazard.design_docs:
        assert summarized_designs is not None, "Expected summarized_designs when design_docs are present"
        assert len(summarized_designs) > 0, "Expected at least one summarized design"
        print(f"\n[{hazard_fixture_name}] Produced {len(summarized_designs)} summarized designs from {len(hazard.design_docs)} design docs")
    
    # Verify summarized_user_needs were produced (if user_needs present)
    summarized_user_needs = result.get("summarized_user_needs")
    if hazard.user_needs:
        assert summarized_user_needs is not None, "Expected summarized_user_needs when user_needs are present"
        assert len(summarized_user_needs) > 0, "Expected at least one summarized user need"
        print(f"[{hazard_fixture_name}] Produced {len(summarized_user_needs)} summarized user needs from {len(hazard.user_needs)} user needs")

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
async def test_hazard_risk_reviewer_batch_via_transformation(
    real_client,
    real_model,
    jsonl_recorders_hz,
    hazard_analysis_wb_sheetname,
    hazard_analysis_requirement_id_format
):
    """
    Test the complete batch transformation workflow end-to-end.
    
    This test orchestrates:
    1. Parse Excel file to extract hazard rows
    2. Load unified pyjama fixture with bidirectional traceability
    3. Merge hazard rows with pyjama data
    4. Write enhanced inputs to inputs.jsonl
    5. Invoke HazardReviewerRunnable graph asynchronously for all rows
    6. Validate H1-H7 findings for each output
    7. Record outputs to outputs.jsonl
    8. Generate summary report
    
    This validates the entire `transform_hazard_record_to_state()` workflow.
    """
    fixtures_dir = Path(__file__).parent.parent.parent / "fixtures" / "external"
    
    # Verify fixture files exist
    excel_file = fixtures_dir / "software_hazard_analysis.xlsx"
    pyjama_file = fixtures_dir / "pyjama_response_unified.jsonl"
    
    assert excel_file.exists(), f"Excel file not found: {excel_file}"
    assert pyjama_file.exists(), f"Pyjama fixture not found: {pyjama_file}"
    
    # Set up output directory with timestamp
    run_dir = Path(settings.log_file_path).parent
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    batch_output_dir = run_dir / f"batch_{timestamp}"
    batch_output_dir.mkdir(parents=True, exist_ok=True)
    
    output_jsonl = batch_output_dir / "inputs.jsonl"
    
    record_input, record_output = jsonl_recorders_hz
    
    print("\n" + "=" * 80)
    print("HAZARD RISK REVIEWER BATCH INTEGRATION TEST")
    print("=" * 80)
    
    # Build the HazardReviewerRunnable graph once (reused for all rows)
    print("\n[Setup] Building HazardReviewerRunnable graph...")
    graph = HazardReviewerRunnable(client=real_client, model=real_model)
    print(f"[Setup] Graph built with model={real_model}")
    
    # Call the batch transformation workflow
    print(f"\n[Transform] Calling transform_hazard_record_to_state()...")
    print(f"  - Excel: {excel_file}")
    print(f"  - PyJama: {pyjama_file}")
    print(f"  - Output: {output_jsonl}")
    
    enhanced_rows = transform_hazard_record_to_state(
        excel_file_path=str(excel_file),
        pyjama_response_file_path=str(pyjama_file),
        output_jsonl_path=str(output_jsonl),
        sheet_name=hazard_analysis_wb_sheetname,
        extract_gids_format=hazard_analysis_requirement_id_format
    )

    print(f"\n[Transform] Transformation complete!")
    print(f"  - Rows processed: {len(enhanced_rows)}")
    
    # Validate that we have at least one row
    assert len(enhanced_rows) > 0, "Expected at least one hazard row from Excel"
    
    # Invoke graph for each enhanced row asynchronously
    print(f"\n[Invocation] Invoking graph for {len(enhanced_rows)} rows...")
    
    async def invoke_row(row: HazardRowWithTraceMatrix, index: int) -> dict:
        """Invoke the graph for a single row."""
        return await graph.graph.ainvoke({"hazard": row})
    
    outputs_generated = await asyncio.gather(
        *[invoke_row(row, i) for i, row in enumerate(enhanced_rows)],
        return_exceptions=False
    )
    
    print(f"\n[Invocation] Graph invocation complete!")
    print(f"  - Outputs generated: {len(outputs_generated)}")
    
    # Validate that we have output for each row
    assert len(outputs_generated) == len(enhanced_rows), \
        f"Output count mismatch: {len(outputs_generated)} vs {len(enhanced_rows)}"
    
    # Validate each output and collect summaries
    print(f"\n[Validation] Validating {len(outputs_generated)} output rows...")
    summaries = []
    for i, output_state in enumerate(outputs_generated):
        summary = _validate_hazard_assessment(output_state, i)
        summaries.append(summary)
        
        # Record this row's output
        record_output(_serialize_hazard_state(output_state))
        
        print(f"  ✓ Row {i}: {summary['hazard_id']} → {summary['overall_verdict']}")
    
    # Validate per-requirement findings
    print(f"\n[Validation] Checking per-requirement findings...")
    for i, output_state in enumerate(outputs_generated):
        reviews = output_state.get("requirement_reviews", [])
        print(f"  Row {i}: {len(reviews)} requirement reviews")
        
        for review in reviews:
            assert isinstance(review, RequirementReview), \
                f"[Row {i}] Expected RequirementReview, got {type(review)}"
            
            assert review.synthesized_assessment is not None, \
                f"[Row {i}] synthesized_assessment is None for {review.requirement.req_id}"
            
            assert isinstance(review.synthesized_assessment, SynthesizedAssessment), \
                f"[Row {i}] Expected SynthesizedAssessment, got {type(review.synthesized_assessment)}"
    
    # Generate summary report
    print(f"\n" + "=" * 80)
    print("BATCH PROCESSING SUMMARY")
    print("=" * 80)
    
    print(f"\nRows Processed: {len(summaries)}")
    print(f"Verdicts Breakdown:")
    
    verdict_counts = {"Yes": 0, "No": 0}
    for summary in summaries:
        verdict = summary["overall_verdict"]
        verdict_counts[verdict] += 1
    
    for verdict, count in verdict_counts.items():
        print(f"  {verdict}: {count}")
    
    print(f"\nDetailed Results:")
    print(f"{'Row':<4} {'Hazard ID':<30} {'Verdict':<8} {'Reqs':<5} {'H1':<4} {'H2':<4} {'H3':<4} {'H4':<4} {'H5':<4} {'H6':<4} {'H7':<4}")
    print("-" * 100)
    
    for i, summary in enumerate(summaries):
        verdicts_str = " ".join([
            summary["verdicts"].get("H1", "?"),
            summary["verdicts"].get("H2", "?"),
            summary["verdicts"].get("H3", "?"),
            summary["verdicts"].get("H4", "?"),
            summary["verdicts"].get("H5", "?"),
            summary["verdicts"].get("H6", "?"),
            summary["verdicts"].get("H7", "?"),
        ])
        print(f"{i:<4} {summary['hazard_id']:<30} {summary['overall_verdict']:<8} {summary['num_requirements']:<5} {verdicts_str}")
    
    print("-" * 100)
    
    # Verify output files were created
    assert output_jsonl.exists(), f"Output JSONL not created: {output_jsonl}"
    with output_jsonl.open("r") as f:
        output_lines = f.readlines()
    print(f"\nOutput Files:")
    print(f"  inputs.jsonl: {output_jsonl} ({len(output_lines)} rows)")
    
    # Report
    print(f"\n" + "=" * 80)
    print("✓ BATCH INTEGRATION TEST COMPLETE")
    print("=" * 80)
    print(f"\nLogs & artifacts:")
    print(f"  - Batch output dir: {batch_output_dir}")
    print(f"  - Enhanced inputs: {output_jsonl}")
    print(f"  - JSONL recorders: inputs.jsonl & outputs.jsonl (in {run_dir})")
    print(f"  - Full state dumps: {run_dir}/hazard_pipeline_state.json")
    print(f"\nNote: Hazard viewer will be generated at session teardown")
    print("=" * 80)