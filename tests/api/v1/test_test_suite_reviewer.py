import pytest
from fastapi import status


@pytest.mark.integration
async def test_test_suite_review_happy_path(client):
    """POST /api/v1/test-suite-review with valid baseline_id returns 200 HTML viewer."""
    response = await client.post("/api/v1/test-suite-review", json={"baseline_id": "BASE-84429"})
    assert response.status_code == status.HTTP_200_OK
    assert "text/html" in response.headers.get("content-type", "")


async def test_test_suite_review_missing_baseline_id(client):
    """POST /api/v1/test-suite-review without baseline_id returns 422."""
    response = await client.post("/api/v1/test-suite-review", json={})
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


async def test_test_suite_review_invalid_json(client):
    """POST /api/v1/test-suite-review with invalid JSON returns 422."""
    response = await client.post(
        "/api/v1/test-suite-review",
        content=b"not valid json",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


async def test_test_suite_review_null_baseline_id(client):
    """POST /api/v1/test-suite-review with null baseline_id returns 422."""
    response = await client.post("/api/v1/test-suite-review", json={"baseline_id": None})
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
