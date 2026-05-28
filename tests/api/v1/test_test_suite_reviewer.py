import pytest
from fastapi import status

from autoqa.components.test_suite_reviewer.core import Requirement, TestCase

from autoqa.components.test_case_reviewer.core import (
    Requirement as TCRequirement,
    TestCase as TCTestCase,
)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_rtm_review_happy_path(client, sample_requirement, sample_test_cases, sample_design_docs):
    """POST /api/v1/review with valid input returns 200 and assessment."""
    payload = {
        "thread_id": "test-rtm-happy-001",
        "requirement": sample_requirement.model_dump(),
        "test_cases": [tc.model_dump() for tc in sample_test_cases],
        "design_docs": [dd.model_dump() for dd in sample_design_docs],
    }
    response = await client.post("/api/v1/review", json=payload)
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    
    # Verify response structure
    assert "thread_id" in data
    assert data["thread_id"] == "test-rtm-happy-001"
    assert "synthesized_assessment" in data
    
    # Verify assessment structure
    assessment = data["synthesized_assessment"]
    assert "overall_verdict" in assessment
    assert assessment["overall_verdict"] in ("Yes", "No")
    assert "mandatory_findings" in assessment
    assert len(assessment["mandatory_findings"]) == 5
    
    # Verify M1-M5 codes
    codes = [f["code"] for f in assessment["mandatory_findings"]]
    assert codes == ["M1", "M2", "M3", "M4", "M5"]


@pytest.mark.asyncio
async def test_rtm_review_missing_requirement(client):
    """POST /api/v1/review without requirement returns 422."""
    payload = {
        "thread_id": "test-rtm-missing-req",
        "test_cases": [],
    }
    response = await client.post("/api/v1/review", json=payload)
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


@pytest.mark.asyncio
async def test_rtm_review_empty_test_cases(client, sample_requirement):
    """POST /api/v1/review with empty test_cases returns 422."""
    payload = {
        "thread_id": "test-rtm-empty-tc",
        "requirement": sample_requirement.model_dump(),
        "test_cases": [],
    }
    response = await client.post("/api/v1/review", json=payload)
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


@pytest.mark.asyncio
async def test_rtm_review_missing_thread_id(client, sample_requirement, sample_test_cases):
    """POST /api/v1/review without thread_id returns 422."""
    payload = {
        "requirement": sample_requirement.model_dump(),
        "test_cases": [tc.model_dump() for tc in sample_test_cases],
    }
    response = await client.post("/api/v1/review", json=payload)
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


@pytest.mark.asyncio
async def test_rtm_review_invalid_requirement_structure(client):
    """POST /api/v1/review with malformed requirement returns 422."""
    payload = {
        "thread_id": "test-rtm-invalid-req",
        "requirement": {"invalid_field": "value"},  # Missing req_id and text
        "test_cases": [
            {
                "test_id": "TC-001",
                "description": "Test",
                "setup": "Setup",
                "steps": "Steps",
                "expectedResults": "Results",
            }
        ],
    }
    response = await client.post("/api/v1/review", json=payload)
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY