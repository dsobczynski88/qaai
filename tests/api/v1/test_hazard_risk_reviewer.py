import pytest
from fastapi import status

from autoqa.components.test_suite_reviewer.core import Requirement, TestCase

from autoqa.components.test_case_reviewer.core import (
    Requirement as TCRequirement,
    TestCase as TCTestCase,
)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_hazard_review_happy_path(client, hazard_full_traceability):
    """POST /api/v1/hazard-review with valid input returns 200."""
    payload = {
        "thread_id": "test-hazard-happy-001",
        "hazard": hazard_full_traceability.model_dump(),
    }
    response = await client.post("/api/v1/hazard-review", json=payload)
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    
    # Verify response structure
    assert "thread_id" in data
    assert data["thread_id"] == "test-hazard-happy-001"
    assert "hazard_assessment" in data
    
    # Verify assessment structure
    assessment = data["hazard_assessment"]
    assert "overall_verdict" in assessment
    assert assessment["overall_verdict"] in ("Yes", "No")
    assert "mandatory_findings" in assessment
    assert len(assessment["mandatory_findings"]) == 7
    
    # Verify H1-H7 codes
    codes = [f["code"] for f in assessment["mandatory_findings"]]
    assert codes == ["H1", "H2", "H3", "H4", "H5", "H6", "H7"]
    
    # Verify requirement reviews
    assert "requirement_reviews" in data
    assert len(data["requirement_reviews"]) == len(hazard_full_traceability.requirements)


@pytest.mark.asyncio
async def test_hazard_review_missing_hazard(client):
    """POST /api/v1/hazard-review without hazard returns 422."""
    payload = {"thread_id": "test-hazard-missing"}
    response = await client.post("/api/v1/hazard-review", json=payload)
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


@pytest.mark.asyncio
async def test_hazard_review_invalid_hazard_structure(client):
    """POST /api/v1/hazard-review with malformed hazard returns 422."""
    payload = {
        "thread_id": "test-hazard-invalid",
        "hazard": {
            "hazard_id": "HAZ-001",
            # Missing required fields
        },
    }
    response = await client.post("/api/v1/hazard-review", json=payload)
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY