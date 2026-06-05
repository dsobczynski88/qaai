import io

import pytest
from fastapi import status


@pytest.mark.integration
async def test_hazard_risk_review_happy_path(client, tmp_path):
    """POST /api/v1/hazard-risk-review with valid Excel file returns 200 HTML viewer."""
    # Integration test — requires a real SHA Excel file at a known path.
    pytest.skip("Requires real SHA Excel fixture; wire up in integration suite.")


async def test_hazard_risk_review_missing_file(client):
    """POST /api/v1/hazard-risk-review without file returns 422."""
    response = await client.post(
        "/api/v1/hazard-risk-review",
        data={"project_name": "Test Project"},
    )
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


async def test_hazard_risk_review_missing_project_name(client):
    """POST /api/v1/hazard-risk-review without project_name returns 422."""
    response = await client.post(
        "/api/v1/hazard-risk-review",
        files={"file": ("sha.xlsx", io.BytesIO(b"fake"), "application/octet-stream")},
    )
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


async def test_hazard_risk_review_invalid_file_type(client):
    """POST /api/v1/hazard-risk-review with non-Excel file returns 400."""
    response = await client.post(
        "/api/v1/hazard-risk-review",
        data={"project_name": "Test Project"},
        files={"file": ("report.txt", io.BytesIO(b"not excel"), "text/plain")},
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST
