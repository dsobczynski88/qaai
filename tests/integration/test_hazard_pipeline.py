"""
Integration test for the hazard risk reviewer pipeline.

Mirrors test_pipeline_full_state in tests/integration/test_pipeline.py:
runs the full graph against a real LLM, asserts the final state shape, and
writes hazard_pipeline_state.json under the active run directory for
manual inspection (and downstream review via the
review-hazard-mitigation-coverage skill).
"""

import json
from pathlib import Path

import pytest
from pydantic import BaseModel

from autoqa.components.hazard_risk_reviewer.core import (
    HazardAssessment,
    HazardReviewState,
    RequirementReview,
)
from autoqa.components.hazard_risk_reviewer.pipeline import HazardReviewerRunnable
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
async def test_hazard_pipeline_full_state(real_client, real_model, sample_hazard):
    """Run the full hazard pipeline end-to-end against a real LLM."""
    graph = HazardReviewerRunnable(client=real_client, model=real_model)
    initial_state: HazardReviewState = {"hazard": sample_hazard}
    result: HazardReviewState = await graph.graph.ainvoke(initial_state)

    # Per-requirement RTM evidence — one review per traced requirement.
    reviews = result.get("requirement_reviews", [])
    assert len(reviews) == len(sample_hazard.requirements)
    for r in reviews:
        assert isinstance(r, RequirementReview)
        # The wrapped RTM subgraph should have produced an M1-M5 assessment
        # for each requirement (it may be None on parser failure, but should
        # populate for a well-formed sample).
        assert isinstance(r.synthesized_assessment, SynthesizedAssessment)
        assert len(r.synthesized_assessment.mandatory_findings) == 5

    # Hazard-level H1-H5 verdict (binary Yes/No; H4 may also be N-A).
    assessment = result.get("hazard_assessment")
    assert isinstance(assessment, HazardAssessment)
    assert assessment.hazard_id == sample_hazard.hazard_id
    assert assessment.overall_verdict in ("Yes", "No")
    assert len(assessment.mandatory_findings) == 5
    assert [f.code for f in assessment.mandatory_findings] == ["H1", "H2", "H3", "H4", "H5"]
    assert [f.dimension for f in assessment.mandatory_findings] == [
        "Hazard Statement Completeness",
        "Pre-Mitigation Risk",
        "Risk Control Adequacy",
        "Verification Depth",
        "Residual Risk Closure",
    ]
    for f in assessment.mandatory_findings:
        if f.code == "H4":
            assert f.verdict in ("Yes", "No", "N-A")
        else:
            assert f.verdict in ("Yes", "No")
    # overall_verdict invariant: Yes iff every dimension is Yes or N-A.
    expected_overall = "Yes" if all(
        f.verdict in ("Yes", "N-A") for f in assessment.mandatory_findings
    ) else "No"
    assert assessment.overall_verdict == expected_overall

    output_path = Path(settings.log_file_path).parent / "hazard_pipeline_state.json"
    output_path.write_text(json.dumps(_serialize_hazard_state(result), indent=2))
    print(f"\n[hazard_full_state] saved → {output_path}")
    print(f"[hazard_full_state] verdict = {assessment.overall_verdict}")
    for f in assessment.mandatory_findings:
        print(f"  {f.code} {f.dimension}: {f.verdict} — {f.rationale}")
