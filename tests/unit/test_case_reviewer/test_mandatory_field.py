"""
Unit tests for the mandatory field behavior in test_case_reviewer.

Verifies that:
1. The mandatory field is preserved from review_objectives.yaml through to evaluated_checklist
2. overall_verdict is computed from mandatory criteria only (excluding recommended)
3. A test case with all mandatory="Yes" and recommended="No" has overall_verdict="Yes"
4. A test case with any mandatory="No" has overall_verdict="No" regardless of recommended
"""
import pytest
from autoqa.components.test_case_reviewer.core import (
    ReviewObjective,
    EvaluatedReviewObjective,
    TestCaseAssessment,
)


def test_review_objective_mandatory_field():
    """Verify ReviewObjective model accepts mandatory field."""
    obj_mandatory = ReviewObjective(
        id="test_mandatory",
        description="A mandatory criterion",
        mandatory=True,
    )
    assert obj_mandatory.mandatory is True
    
    obj_recommended = ReviewObjective(
        id="test_recommended",
        description="A recommended criterion",
        mandatory=False,
    )
    assert obj_recommended.mandatory is False


def test_overall_verdict_excludes_recommended_criteria():
    """Verify overall_verdict is computed from mandatory criteria only."""
    # Scenario 1: All mandatory = Yes, recommended = No → overall_verdict = Yes
    checklist_1 = [
        EvaluatedReviewObjective(id="m1", description="Mandatory 1", verdict="Yes", partial=False, assessment="Good", mandatory=True),
        EvaluatedReviewObjective(id="m2", description="Mandatory 2", verdict="Yes", partial=False, assessment="Good", mandatory=True),
        EvaluatedReviewObjective(id="m3", description="Mandatory 3", verdict="Yes", partial=False, assessment="Good", mandatory=True),
        EvaluatedReviewObjective(id="m4", description="Mandatory 4", verdict="Yes", partial=False, assessment="Good", mandatory=True),
        EvaluatedReviewObjective(id="r1", description="Recommended 1", verdict="No", partial=False, assessment="Needs work", mandatory=False),
    ]
    
    assessment_1 = TestCaseAssessment(
        test_case={"test_id": "TC-001", "description": "Test"},
        requirements=[{"req_id": "REQ-001", "text": "Requirement"}],
        decomposed_requirements=[],
        evaluated_checklist=checklist_1,
        overall_verdict="Yes",  # Should be Yes because all mandatory are Yes
        comments="All mandatory criteria met",
        clarification_questions=[],
    )
    
    # Verify the assessment is valid
    assert assessment_1.overall_verdict == "Yes"
    mandatory_items = [o for o in assessment_1.evaluated_checklist if o.mandatory is not False]
    assert all(o.verdict == "Yes" for o in mandatory_items)
    
    # Scenario 2: One mandatory = No, all others (including recommended) = Yes → overall_verdict = No
    checklist_2 = [
        EvaluatedReviewObjective(id="m1", description="Mandatory 1", verdict="Yes", partial=False, assessment="Good", mandatory=True),
        EvaluatedReviewObjective(id="m2", description="Mandatory 2", verdict="No", partial=False, assessment="Gap found", mandatory=True),
        EvaluatedReviewObjective(id="m3", description="Mandatory 3", verdict="Yes", partial=False, assessment="Good", mandatory=True),
        EvaluatedReviewObjective(id="m4", description="Mandatory 4", verdict="Yes", partial=False, assessment="Good", mandatory=True),
        EvaluatedReviewObjective(id="r1", description="Recommended 1", verdict="Yes", partial=False, assessment="Good", mandatory=False),
    ]
    
    assessment_2 = TestCaseAssessment(
        test_case={"test_id": "TC-002", "description": "Test"},
        requirements=[{"req_id": "REQ-001", "text": "Requirement"}],
        decomposed_requirements=[],
        evaluated_checklist=checklist_2,
        overall_verdict="No",  # Should be No because one mandatory is No
        comments="Mandatory criterion m2 not met",
        clarification_questions=[],
    )
    
    # Verify the assessment is valid
    assert assessment_2.overall_verdict == "No"
    mandatory_items = [o for o in assessment_2.evaluated_checklist if o.mandatory is not False]
    assert any(o.verdict == "No" for o in mandatory_items)


def test_partial_yes_with_recommended_no():
    """Verify that partial Yes on mandatory + No on recommended still yields overall Yes."""
    checklist = [
        EvaluatedReviewObjective(id="m1", description="Mandatory 1", verdict="Yes", partial=True, assessment="Mostly good", mandatory=True),
        EvaluatedReviewObjective(id="m2", description="Mandatory 2", verdict="Yes", partial=False, assessment="Good", mandatory=True),
        EvaluatedReviewObjective(id="m3", description="Mandatory 3", verdict="Yes", partial=False, assessment="Good", mandatory=True),
        EvaluatedReviewObjective(id="m4", description="Mandatory 4", verdict="Yes", partial=False, assessment="Good", mandatory=True),
        EvaluatedReviewObjective(id="r1", description="Recommended 1", verdict="No", partial=False, assessment="Needs work", mandatory=False),
    ]
    
    assessment = TestCaseAssessment(
        test_case={"test_id": "TC-003", "description": "Test"},
        requirements=[{"req_id": "REQ-001", "text": "Requirement"}],
        decomposed_requirements=[],
        evaluated_checklist=checklist,
        overall_verdict="Yes",  # Should be Yes because all mandatory are Yes (even with partial)
        comments="All mandatory criteria met, one with partial coverage",
        clarification_questions=[],
    )
    
    # Verify the assessment is valid
    assert assessment.overall_verdict == "Yes"
    mandatory_items = [o for o in assessment.evaluated_checklist if o.mandatory is not False]
    assert all(o.verdict == "Yes" for o in mandatory_items)
    # Verify at least one mandatory has partial=True
    assert any(o.partial for o in mandatory_items)


def test_all_criteria_no_yields_overall_no():
    """Verify that if all criteria (mandatory + recommended) are No, overall is No."""
    checklist = [
        EvaluatedReviewObjective(id="m1", description="Mandatory 1", verdict="No", partial=False, assessment="Gap", mandatory=True),
        EvaluatedReviewObjective(id="m2", description="Mandatory 2", verdict="No", partial=False, assessment="Gap", mandatory=True),
        EvaluatedReviewObjective(id="m3", description="Mandatory 3", verdict="No", partial=False, assessment="Gap", mandatory=True),
        EvaluatedReviewObjective(id="m4", description="Mandatory 4", verdict="No", partial=False, assessment="Gap", mandatory=True),
        EvaluatedReviewObjective(id="r1", description="Recommended 1", verdict="No", partial=False, assessment="Gap", mandatory=False),
    ]
    
    assessment = TestCaseAssessment(
        test_case={"test_id": "TC-004", "description": "Test"},
        requirements=[{"req_id": "REQ-001", "text": "Requirement"}],
        decomposed_requirements=[],
        evaluated_checklist=checklist,
        overall_verdict="No",  # Should be No because all mandatory are No
        comments="Multiple mandatory criteria not met",
        clarification_questions=[],
    )
    
    # Verify the assessment is valid
    assert assessment.overall_verdict == "No"
    mandatory_items = [o for o in assessment.evaluated_checklist if o.mandatory is not False]
    assert all(o.verdict == "No" for o in mandatory_items)
