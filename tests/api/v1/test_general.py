import pytest
from fastapi import status

# ---------------------------------------------------------------------------
# Error Handling & Edge Cases
# ---------------------------------------------------------------------------


async def test_invalid_json_body(client):
    """POST with invalid JSON returns 422."""
    response = await client.post(
        "/api/v1/test-suite-review",
        content=b"not valid json",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


async def test_missing_baseline_id(client):
    """POST without required baseline_id returns 422."""
    response = await client.post("/api/v1/test-suite-review", json={})
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


async def test_request_id_header_present(client):
    """Verify X-Request-ID header is added to every response."""
    response = await client.get("/api/v1/health")
    assert "X-Request-ID" in response.headers
    assert len(response.headers["X-Request-ID"]) > 0


# ---------------------------------------------------------------------------
# TODO: Additional Tests
# ---------------------------------------------------------------------------


@pytest.mark.skip(reason="Requires mock/dependency injection refactoring")
@pytest.mark.asyncio
async def test_rtm_review_service_unavailable(client, monkeypatch):
    """Simulate service failure and verify 503 response."""
    pass


@pytest.mark.skip(reason="Rate limiting not yet implemented")
@pytest.mark.asyncio
async def test_rate_limiting(client):
    """Verify rate limiting behavior (if implemented)."""
    pass


@pytest.mark.skip(reason="Authentication not yet implemented")
@pytest.mark.asyncio
async def test_authentication_required(client):
    """Verify endpoints require authentication (if implemented)."""
    pass
