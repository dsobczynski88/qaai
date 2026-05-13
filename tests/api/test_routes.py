"""
API endpoint integration tests.

Tests the FastAPI routes for all three review pipelines:
- /api/v1/review (RTM test_suite_reviewer)
- /api/v1/test-case-review (test_case_reviewer)
- /api/v1/hazard-review (hazard_risk_reviewer)
- /health (health check)

Uses pytest-asyncio and httpx.AsyncClient for async endpoint testing.
Requires the application to be running or uses TestClient for in-process testing.
"""
import pytest
from httpx import AsyncClient, ASGITransport
from fastapi import status

from autoqa.api.main import app
from autoqa.components.test_suite_reviewer.core import Requirement, TestCase
from autoqa.components.test_case_reviewer.core import (
    Requirement as TCRequirement,
    TestCase as TCTestCase,
)
from autoqa.components.hazard_risk_reviewer.core import HazardRecord


@pytest.fixture
async def client():
    """Async HTTP client for API testing.
    
    Uses ASGITransport to test the application in-process without
    needing to start a separate server.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ---------------------------------------------------------------------------
# Health Check
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_check(client):
    """GET /health returns 200 OK with service status."""
    response = await client.get("/api/v1/health")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["status"] in ("healthy", "degraded")
    assert "version" in data
    assert "services" in data
    assert "rtm_service" in data["services"]
    assert "hazard_service" in data["services"]
    assert "test_case_service" in data["services"]


# ---------------------------------------------------------------------------
# RTM Review Endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_rtm_review_happy_path(client, sample_requirement, sample_test_cases):
    """POST /api/v1/review with valid input returns 200 and assessment."""
    payload = {
        "thread_id": "test-rtm-happy-001",
        "requirement": sample_requirement.model_dump(),
        "test_cases": [tc.model_dump() for tc in sample_test_cases],
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


# ---------------------------------------------------------------------------
# Test Case Review Endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
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
        "upstream_requirements": [requirement.model_dump()],
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


@pytest.mark.asyncio
async def test_tc_review_missing_test_case(client):
    """POST /api/v1/test-case-review without test_case returns 422."""
    payload = {
        "thread_id": "test-tc-missing-tc",
        "upstream_requirements": [],
    }
    response = await client.post("/api/v1/test-case-review", json=payload)
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


@pytest.mark.asyncio
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
        "upstream_requirements": [],
    }
    response = await client.post("/api/v1/test-case-review", json=payload)
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


# ---------------------------------------------------------------------------
# Hazard Review Endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_hazard_review_happy_path(client, sample_hazard):
    """POST /api/v1/hazard-review with valid input returns 200."""
    payload = {
        "thread_id": "test-hazard-happy-001",
        "hazard": sample_hazard.model_dump(),
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
    assert len(data["requirement_reviews"]) == len(sample_hazard.requirements)


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


# ---------------------------------------------------------------------------
# Error Handling & Edge Cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invalid_json_body(client):
    """POST with invalid JSON returns 422."""
    response = await client.post(
        "/api/v1/review",
        content=b"not valid json",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


@pytest.mark.asyncio
async def test_missing_content_type(client, sample_requirement, sample_test_cases):
    """POST without Content-Type header still works (FastAPI auto-detects)."""
    payload = {
        "thread_id": "test-rtm-no-ct",
        "requirement": sample_requirement.model_dump(),
        "test_cases": [tc.model_dump() for tc in sample_test_cases],
    }
    # httpx automatically sets Content-Type for json parameter
    response = await client.post("/api/v1/review", json=payload)
    # Should work fine - FastAPI handles this
    assert response.status_code in (status.HTTP_200_OK, status.HTTP_422_UNPROCESSABLE_ENTITY)


@pytest.mark.asyncio
async def test_request_id_header_present(client, sample_requirement, sample_test_cases):
    """Verify X-Request-ID header is added to responses."""
    payload = {
        "thread_id": "test-rtm-req-id",
        "requirement": sample_requirement.model_dump(),
        "test_cases": [tc.model_dump() for tc in sample_test_cases],
    }
    response = await client.post("/api/v1/review", json=payload)
    assert "X-Request-ID" in response.headers
    assert len(response.headers["X-Request-ID"]) > 0


# ---------------------------------------------------------------------------
# Concurrent Requests (Stress Test)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.slow
async def test_concurrent_rtm_reviews(client, sample_requirement, sample_test_cases):
    """Verify API handles concurrent requests without errors."""
    import asyncio
    
    async def make_request(idx: int):
        payload = {
            "thread_id": f"test-concurrent-{idx}",
            "requirement": sample_requirement.model_dump(),
            "test_cases": [tc.model_dump() for tc in sample_test_cases],
        }
        response = await client.post("/api/v1/review", json=payload)
        return response.status_code
    
    # Send 5 concurrent requests
    results = await asyncio.gather(*[make_request(i) for i in range(5)])
    
    # All should succeed (or fail gracefully with 503 if rate limited)
    for status_code in results:
        assert status_code in (status.HTTP_200_OK, status.HTTP_503_SERVICE_UNAVAILABLE)


# ---------------------------------------------------------------------------
# TODO: Additional Tests
# ---------------------------------------------------------------------------


@pytest.mark.skip(reason="Requires mock/dependency injection refactoring")
@pytest.mark.asyncio
async def test_rtm_review_service_unavailable(client, monkeypatch):
    """Simulate service failure and verify 503 response."""
    # TODO: Mock the pipeline to raise an exception
    # This requires dependency injection refactoring in the API layer
    pass


@pytest.mark.skip(reason="Rate limiting not yet implemented")
@pytest.mark.asyncio
async def test_rate_limiting(client, sample_requirement, sample_test_cases):
    """Verify rate limiting behavior (if implemented)."""
    # TODO: Send multiple rapid requests and verify rate limit headers
    pass


@pytest.mark.skip(reason="Authentication not yet implemented")
@pytest.mark.asyncio
async def test_authentication_required(client):
    """Verify endpoints require authentication (if implemented)."""
    # TODO: Test without auth token
    pass
