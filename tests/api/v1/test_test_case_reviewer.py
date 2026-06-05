import pytest
from fastapi import status


@pytest.mark.integration
async def test_tc_review_happy_path(submit_and_wait):
    """Submit /api/v1/test-case-review, poll the job, and download the HTML viewer."""
    response = await submit_and_wait(
        "/api/v1/test-case-review", json={"baseline_id": "BASE-84429"}, max_wait=600
    )
    assert response.status_code == status.HTTP_200_OK
    assert "text/html" in response.headers.get("content-type", "")


async def test_tc_review_missing_baseline_id(client):
    """POST /api/v1/test-case-review without baseline_id returns 422."""
    response = await client.post("/api/v1/test-case-review", json={})
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


async def test_tc_review_invalid_json(client):
    """POST /api/v1/test-case-review with invalid JSON returns 422."""
    response = await client.post(
        "/api/v1/test-case-review",
        content=b"not valid json",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


async def test_tc_review_null_baseline_id(client):
    """POST /api/v1/test-case-review with null baseline_id returns 422."""
    response = await client.post("/api/v1/test-case-review", json={"baseline_id": None})
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
