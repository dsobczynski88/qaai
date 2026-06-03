"""API-level tests for the 'use cache' toggle → cache_mode mapping.

The reviewer pipelines are stubbed (no JAMA / no LLM) so we can assert exactly
what cache_mode the route forwards to the service for each value of use_cache.
"""
from unittest.mock import AsyncMock

import pytest
from fastapi import status

from autoqa.api.main import app


@pytest.fixture
def dummy_html(tmp_path):
    f = tmp_path / "out.html"
    f.write_text("<html><body>ok</body></html>", encoding="utf-8")
    return str(f)


async def test_test_suite_use_cache_false_maps_to_off(client, dummy_html):
    rec = {}

    async def fake_run(baseline_id, thread_id, cache_mode="partial"):
        rec["cache_mode"] = cache_mode
        return dummy_html

    app.state.rtm_service.run_from_baseline = AsyncMock(side_effect=fake_run)

    resp = await client.post(
        "/api/v1/test-suite-review", json={"baseline_id": "B", "use_cache": False}
    )
    assert resp.status_code == status.HTTP_200_OK
    assert rec["cache_mode"] == "off"


async def test_test_suite_use_cache_default_is_partial(client, dummy_html):
    rec = {}

    async def fake_run(baseline_id, thread_id, cache_mode="partial"):
        rec["cache_mode"] = cache_mode
        return dummy_html

    app.state.rtm_service.run_from_baseline = AsyncMock(side_effect=fake_run)

    # use_cache omitted → schema default True → cache_mode "partial"
    resp = await client.post("/api/v1/test-suite-review", json={"baseline_id": "B"})
    assert resp.status_code == status.HTTP_200_OK
    assert rec["cache_mode"] == "partial"


async def test_test_case_use_cache_false_maps_to_off(client, dummy_html):
    rec = {}

    async def fake_run(baseline_id, thread_id, cache_mode="partial"):
        rec["cache_mode"] = cache_mode
        return dummy_html

    app.state.test_case_service.run_from_baseline = AsyncMock(side_effect=fake_run)

    resp = await client.post(
        "/api/v1/test-case-review", json={"baseline_id": "B", "use_cache": False}
    )
    assert resp.status_code == status.HTTP_200_OK
    assert rec["cache_mode"] == "off"


async def test_hazard_use_cache_false_maps_to_off(client, dummy_html):
    rec = {}

    async def fake_run(*, file_bytes, filename, project_name, thread_id_prefix,
                       sheet_name="SHA Table", cache_mode="partial"):
        rec["cache_mode"] = cache_mode
        return dummy_html

    app.state.hazard_service.run_from_excel_upload = AsyncMock(side_effect=fake_run)

    resp = await client.post(
        "/api/v1/hazard-risk-review",
        files={"file": ("h.xlsx", b"binary", "application/vnd.ms-excel")},
        data={"project_name": "P", "use_cache": "false"},
    )
    assert resp.status_code == status.HTTP_200_OK
    assert rec["cache_mode"] == "off"


async def test_hazard_use_cache_default_is_partial(client, dummy_html):
    rec = {}

    async def fake_run(*, file_bytes, filename, project_name, thread_id_prefix,
                       sheet_name="SHA Table", cache_mode="partial"):
        rec["cache_mode"] = cache_mode
        return dummy_html

    app.state.hazard_service.run_from_excel_upload = AsyncMock(side_effect=fake_run)

    resp = await client.post(
        "/api/v1/hazard-risk-review",
        files={"file": ("h.xlsx", b"binary", "application/vnd.ms-excel")},
        data={"project_name": "P"},  # use_cache omitted → Form default True
    )
    assert resp.status_code == status.HTTP_200_OK
    assert rec["cache_mode"] == "partial"
