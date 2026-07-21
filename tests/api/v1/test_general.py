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


async def test_usage_endpoint_reports_limits_and_totals(client):
    """GET /usage exposes the shared limiter's RPM/TPM utilization + rolling totals
    (centralized, all-users monitoring on a single instance)."""
    response = await client.get("/api/v1/usage")
    assert response.status_code == status.HTTP_200_OK
    body = response.json()

    assert "rate_limits" in body and "totals" in body
    rpm = body["rate_limits"]["rpm"]
    assert set(rpm) == {"used", "limit"}
    assert rpm["used"] >= 0 and rpm["limit"] > 0
    # TPM limiter is configured by default settings, so it should report too.
    tpm = body["rate_limits"]["tpm"]
    assert tpm is None or set(tpm) == {"used", "limit"}
    # Totals come from the TokenUsageTracker.summary() shape.
    assert "total_tokens" in body["totals"]


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
