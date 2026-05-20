import pytest
from fastapi import status

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