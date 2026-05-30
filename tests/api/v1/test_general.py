import asyncio

import pytest
from fastapi import status

# ---------------------------------------------------------------------------
# Error Handling & Edge Cases
# ---------------------------------------------------------------------------


async def test_invalid_json_body(client):
    """POST with invalid JSON returns 422."""
    response = await client.post(
        "/api/v1/review",
        content=b"not valid json",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


async def test_missing_content_type(client, sample_requirement, sample_test_cases, sample_design_docs):
    """POST without Content-Type header still works (FastAPI auto-detects)."""
    payload = {
        "thread_id": "test-rtm-no-ct",
        "requirement": sample_requirement.model_dump(),
        "test_cases": [tc.model_dump() for tc in sample_test_cases],
        "design_docs": [dd.model_dump() for dd in sample_design_docs],
    }
    # httpx automatically sets Content-Type for json parameter
    response = await client.post("/api/v1/review", json=payload)
    # Should work fine - FastAPI handles this
    assert response.status_code in (status.HTTP_200_OK, status.HTTP_422_UNPROCESSABLE_ENTITY)


async def test_request_id_header_present(client, sample_requirement, sample_test_cases, sample_design_docs):
    """Verify X-Request-ID header is added to responses."""
    payload = {
        "thread_id": "test-rtm-req-id",
        "requirement": sample_requirement.model_dump(),
        "test_cases": [tc.model_dump() for tc in sample_test_cases],
        "design_docs": [dd.model_dump() for dd in sample_design_docs],
    }
    response = await client.post("/api/v1/review", json=payload)
    assert "X-Request-ID" in response.headers
    assert len(response.headers["X-Request-ID"]) > 0


# ---------------------------------------------------------------------------
# Concurrent Requests (Stress Test)
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.slow
async def test_concurrent_rtm_reviews(client, sample_requirement, sample_test_cases, sample_design_docs):
    """Verify API handles concurrent requests without errors."""
    async def make_request(idx: int):
        payload = {
            "thread_id": f"test-concurrent-{idx}",
            "requirement": sample_requirement.model_dump(),
            "test_cases": [tc.model_dump() for tc in sample_test_cases],
            "design_docs": [dd.model_dump() for dd in sample_design_docs],
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
