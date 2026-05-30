import pytest
from fastapi import status

from autoqa.components.test_case_reviewer.core import (
    Requirement as TCRequirement,
    TestCase as TCTestCase,
)


@pytest.mark.integration
async def test_tc_review_happy_path(client):
    """POST /api/v1/test-case-review with valid input returns 200."""
    test_case = TCTestCase(
        test_id="TC-001",
        description="Alert fires above threshold",
        setup="Sensor connected",
        steps="Set reading to 105",
        expectedResults="Alert displayed",
    )
    requirement = TCRequirement(
        req_id="REQ-001",
        text="System shall alert when reading exceeds 100 mg/dL.",
    )
    payload = {
        "thread_id": "test-tc-happy-001",
        "test_case": test_case.model_dump(),
        "requirements": [requirement.model_dump()],
    }
    response = await client.post("/api/v1/test-case-review", json=payload)
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    
    # Verify response structure
    assert "thread_id" in data
    assert data["thread_id"] == "test-tc-happy-001"
    assert "aggregated_assessment" in data
    
    # Verify assessment structure
    assessment = data["aggregated_assessment"]
    assert "overall_verdict" in assessment
    assert assessment["overall_verdict"] in ("Yes", "No")
    assert "evaluated_checklist" in assessment
    assert len(assessment["evaluated_checklist"]) == 5
    
    # Verify checklist IDs
    checklist_ids = {item["id"] for item in assessment["evaluated_checklist"]}
    expected_ids = {
        "expected_result_support",
        "expected_result_spec_align",
        "test_case_achieves",
        "test_case_logical_sequence",
        "test_case_setup_clarity",
    }
    assert checklist_ids == expected_ids


async def test_tc_review_missing_test_case(client):
    """POST /api/v1/test-case-review without test_case returns 422."""
    payload = {
        "thread_id": "test-tc-missing-tc",
        "requirements": [],
    }
    response = await client.post("/api/v1/test-case-review", json=payload)
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


async def test_tc_review_empty_requirements(client):
    """POST /api/v1/test-case-review with empty requirements returns 422."""
    test_case = TCTestCase(
        test_id="TC-001",
        description="Test",
        setup="Setup",
        steps="Steps",
        expectedResults="Results",
    )
    payload = {
        "thread_id": "test-tc-empty-req",
        "test_case": test_case.model_dump(),
        "requirements": [],
    }
    response = await client.post("/api/v1/test-case-review", json=payload)
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY