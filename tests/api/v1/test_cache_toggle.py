"""API-level tests for the 'use cache' toggle → cache_mode mapping.

The reviewer pipelines are stubbed (no JAMA / no LLM) so we can assert exactly
what cache_mode the route forwards to the service for each value of use_cache.
"""
from unittest.mock import AsyncMock

import pytest
from fastapi import status

from qaai.api.main import app
from qaai.api.services import (
    PROMPT_SET_BASELINE,
    PROMPT_SET_EDGE_CASE,
    HazardReviewService,
    RTMReviewService,
)


@pytest.fixture
def dummy_html(tmp_path):
    f = tmp_path / "out.html"
    f.write_text("<html><body>ok</body></html>", encoding="utf-8")
    return str(f)


async def test_test_suite_use_cache_false_maps_to_off(submit_and_wait, dummy_html):
    rec = {}

    async def fake_run(baseline_id, thread_id, cache_mode="partial", test_mode=None,
                       prompt_set="test_suite_reviewer_v3"):
        rec["cache_mode"] = cache_mode
        rec["test_mode"] = test_mode
        rec["prompt_set"] = prompt_set
        return dummy_html

    app.state.rtm_service.run_from_baseline = AsyncMock(side_effect=fake_run)

    resp = await submit_and_wait(
        "/api/v1/test-suite-review", json={"baseline_id": "B", "use_cache": False}
    )
    assert resp.status_code == status.HTTP_200_OK
    assert rec["cache_mode"] == "off"


async def test_test_suite_use_cache_default_is_partial(submit_and_wait, dummy_html):
    rec = {}

    async def fake_run(baseline_id, thread_id, cache_mode="partial", test_mode=None,
                       prompt_set="test_suite_reviewer_v3"):
        rec["cache_mode"] = cache_mode
        rec["test_mode"] = test_mode
        rec["prompt_set"] = prompt_set
        return dummy_html

    app.state.rtm_service.run_from_baseline = AsyncMock(side_effect=fake_run)

    # use_cache omitted → schema default True → cache_mode "partial"
    resp = await submit_and_wait("/api/v1/test-suite-review", json={"baseline_id": "B"})
    assert resp.status_code == status.HTTP_200_OK
    assert rec["cache_mode"] == "partial"


async def test_test_case_use_cache_false_maps_to_off(submit_and_wait, dummy_html):
    rec = {}

    async def fake_run(baseline_id, thread_id, cache_mode="partial", test_mode=None,
                       prompt_set="test_suite_reviewer_v3"):
        rec["cache_mode"] = cache_mode
        rec["test_mode"] = test_mode
        rec["prompt_set"] = prompt_set
        return dummy_html

    app.state.test_case_service.run_from_baseline = AsyncMock(side_effect=fake_run)

    resp = await submit_and_wait(
        "/api/v1/test-case-review", json={"baseline_id": "B", "use_cache": False}
    )
    assert resp.status_code == status.HTTP_200_OK
    assert rec["cache_mode"] == "off"


async def test_hazard_use_cache_false_maps_to_off(submit_and_wait, dummy_html):
    rec = {}

    async def fake_run(*, file_bytes, filename, project_name, thread_id_prefix,
                       sheet_name="SHA Table", cache_mode="partial", test_mode=None,
                       prompt_set="test_suite_reviewer_v3", extract_gids_format="GID-\\d+"):
        rec["cache_mode"] = cache_mode
        rec["test_mode"] = test_mode
        return dummy_html

    app.state.hazard_service.run_from_excel_upload = AsyncMock(side_effect=fake_run)

    resp = await submit_and_wait(
        "/api/v1/hazard-risk-review",
        files={"file": ("h.xlsx", b"binary", "application/vnd.ms-excel")},
        data={"project_name": "P", "use_cache": "false"},
    )
    assert resp.status_code == status.HTTP_200_OK
    assert rec["cache_mode"] == "off"


async def test_hazard_use_cache_default_is_partial(submit_and_wait, dummy_html):
    rec = {}

    async def fake_run(*, file_bytes, filename, project_name, thread_id_prefix,
                       sheet_name="SHA Table", cache_mode="partial", test_mode=None,
                       prompt_set="test_suite_reviewer_v3", extract_gids_format="GID-\\d+"):
        rec["cache_mode"] = cache_mode
        rec["test_mode"] = test_mode
        return dummy_html

    app.state.hazard_service.run_from_excel_upload = AsyncMock(side_effect=fake_run)

    resp = await submit_and_wait(
        "/api/v1/hazard-risk-review",
        files={"file": ("h.xlsx", b"binary", "application/vnd.ms-excel")},
        data={"project_name": "P"},  # use_cache omitted → Form default True
    )
    assert resp.status_code == status.HTTP_200_OK
    assert rec["cache_mode"] == "partial"


# --- explicit cache_mode (the UI radio) → forwarded verbatim -----------------

@pytest.mark.parametrize("mode", ["off", "partial", "full"])
async def test_test_suite_explicit_cache_mode_forwarded(submit_and_wait, dummy_html, mode):
    rec = {}

    async def fake_run(baseline_id, thread_id, cache_mode="partial", test_mode=None,
                       prompt_set="test_suite_reviewer_v3"):
        rec["cache_mode"] = cache_mode
        return dummy_html

    app.state.rtm_service.run_from_baseline = AsyncMock(side_effect=fake_run)

    resp = await submit_and_wait(
        "/api/v1/test-suite-review", json={"baseline_id": "B", "cache_mode": mode}
    )
    assert resp.status_code == status.HTTP_200_OK
    assert rec["cache_mode"] == mode


@pytest.mark.parametrize("mode", ["off", "partial", "full"])
async def test_test_case_explicit_cache_mode_forwarded(submit_and_wait, dummy_html, mode):
    rec = {}

    async def fake_run(baseline_id, thread_id, cache_mode="partial", test_mode=None,
                       prompt_set="test_suite_reviewer_v3"):
        rec["cache_mode"] = cache_mode
        return dummy_html

    app.state.test_case_service.run_from_baseline = AsyncMock(side_effect=fake_run)

    resp = await submit_and_wait(
        "/api/v1/test-case-review", json={"baseline_id": "B", "cache_mode": mode}
    )
    assert resp.status_code == status.HTTP_200_OK
    assert rec["cache_mode"] == mode


@pytest.mark.parametrize("mode", ["off", "partial", "full"])
async def test_hazard_explicit_cache_mode_forwarded(submit_and_wait, dummy_html, mode):
    rec = {}

    async def fake_run(*, file_bytes, filename, project_name, thread_id_prefix,
                       sheet_name="SHA Table", cache_mode="partial", test_mode=None,
                       prompt_set="test_suite_reviewer_v3", extract_gids_format="GID-\\d+"):
        rec["cache_mode"] = cache_mode
        return dummy_html

    app.state.hazard_service.run_from_excel_upload = AsyncMock(side_effect=fake_run)

    resp = await submit_and_wait(
        "/api/v1/hazard-risk-review",
        files={"file": ("h.xlsx", b"binary", "application/vnd.ms-excel")},
        data={"project_name": "P", "cache_mode": mode},
    )
    assert resp.status_code == status.HTTP_200_OK
    assert rec["cache_mode"] == mode


async def test_explicit_cache_mode_overrides_use_cache(submit_and_wait, dummy_html):
    rec = {}

    async def fake_run(baseline_id, thread_id, cache_mode="partial", test_mode=None,
                       prompt_set="test_suite_reviewer_v3"):
        rec["cache_mode"] = cache_mode
        return dummy_html

    app.state.rtm_service.run_from_baseline = AsyncMock(side_effect=fake_run)

    # use_cache=False alone would map to "off", but an explicit cache_mode wins.
    resp = await submit_and_wait(
        "/api/v1/test-suite-review",
        json={"baseline_id": "B", "use_cache": False, "cache_mode": "full"},
    )
    assert resp.status_code == status.HTTP_200_OK
    assert rec["cache_mode"] == "full"


@pytest.mark.parametrize("sent,expected", [(True, True), (False, False)])
async def test_test_suite_test_mode_propagates(submit_and_wait, dummy_html, sent, expected):
    rec = {}

    async def fake_run(baseline_id, thread_id, cache_mode="partial", test_mode=None,
                       prompt_set="test_suite_reviewer_v3"):
        rec["test_mode"] = test_mode
        return dummy_html

    app.state.rtm_service.run_from_baseline = AsyncMock(side_effect=fake_run)

    resp = await submit_and_wait(
        "/api/v1/test-suite-review",
        json={"baseline_id": "B", "test_mode": sent},
    )
    assert resp.status_code == status.HTTP_200_OK
    assert rec["test_mode"] is expected


@pytest.mark.parametrize("sent,expected", [("true", True), ("false", False)])
async def test_hazard_test_mode_propagates(submit_and_wait, dummy_html, sent, expected):
    rec = {}

    async def fake_run(*, file_bytes, filename, project_name, thread_id_prefix,
                       sheet_name="SHA Table", cache_mode="partial", test_mode=None,
                       prompt_set="test_suite_reviewer_v3", extract_gids_format="GID-\\d+"):
        rec["test_mode"] = test_mode
        return dummy_html

    app.state.hazard_service.run_from_excel_upload = AsyncMock(side_effect=fake_run)

    resp = await submit_and_wait(
        "/api/v1/hazard-risk-review",
        files={"file": ("h.xlsx", b"binary", "application/vnd.ms-excel")},
        data={"project_name": "P", "test_mode": sent},
    )
    assert resp.status_code == status.HTTP_200_OK
    assert rec["test_mode"] is expected


# --- "Include Edge Case Analysis" toggle → prompt_set selection ---------------

@pytest.mark.parametrize(
    "sent,expected_set",
    [(True, "test_suite_reviewer_v4"), (False, "test_suite_reviewer_v3")],
)
async def test_test_suite_edge_case_toggle_selects_prompt_set(
    submit_and_wait, dummy_html, sent, expected_set
):
    rec = {}

    async def fake_run(baseline_id, thread_id, cache_mode="partial", test_mode=None,
                       prompt_set="test_suite_reviewer_v3"):
        rec["prompt_set"] = prompt_set
        return dummy_html

    app.state.rtm_service.run_from_baseline = AsyncMock(side_effect=fake_run)

    resp = await submit_and_wait(
        "/api/v1/test-suite-review",
        json={"baseline_id": "B", "include_edge_case_analysis": sent},
    )
    assert resp.status_code == status.HTTP_200_OK
    assert rec["prompt_set"] == expected_set


async def test_test_suite_edge_case_default_is_baseline(submit_and_wait, dummy_html):
    rec = {}

    async def fake_run(baseline_id, thread_id, cache_mode="partial", test_mode=None,
                       prompt_set="test_suite_reviewer_v3"):
        rec["prompt_set"] = prompt_set
        return dummy_html

    app.state.rtm_service.run_from_baseline = AsyncMock(side_effect=fake_run)

    # include_edge_case_analysis omitted → schema default False → baseline v3
    resp = await submit_and_wait("/api/v1/test-suite-review", json={"baseline_id": "B"})
    assert resp.status_code == status.HTTP_200_OK
    assert rec["prompt_set"] == "test_suite_reviewer_v3"


@pytest.mark.parametrize(
    "sent,expected_set",
    [("true", "test_suite_reviewer_v4"), ("false", "test_suite_reviewer_v3")],
)
async def test_hazard_edge_case_toggle_selects_prompt_set(
    submit_and_wait, dummy_html, sent, expected_set
):
    rec = {}

    async def fake_run(*, file_bytes, filename, project_name, thread_id_prefix,
                       sheet_name="SHA Table", cache_mode="partial", test_mode=None,
                       prompt_set="test_suite_reviewer_v3", extract_gids_format="GID-\\d+"):
        rec["prompt_set"] = prompt_set
        return dummy_html

    app.state.hazard_service.run_from_excel_upload = AsyncMock(side_effect=fake_run)

    resp = await submit_and_wait(
        "/api/v1/hazard-risk-review",
        files={"file": ("h.xlsx", b"binary", "application/vnd.ms-excel")},
        data={"project_name": "P", "include_edge_case_analysis": sent},
    )
    assert resp.status_code == status.HTTP_200_OK
    assert rec["prompt_set"] == expected_set


# --- service _select() fallback (no graph building; sentinel runnables) --------

@pytest.mark.parametrize("service_cls,kw", [
    (RTMReviewService, "rtm_runnables"),
    (HazardReviewService, "hazard_runnables"),
])
def test_select_falls_back_to_baseline_for_unknown_set(service_cls, kw):
    v3, v4 = object(), object()
    svc = service_cls(
        client=None, model="m",
        **{kw: {PROMPT_SET_BASELINE: v3, PROMPT_SET_EDGE_CASE: v4}},
    )
    assert svc._select(PROMPT_SET_EDGE_CASE) is v4
    assert svc._select(PROMPT_SET_BASELINE) is v3
    assert svc._select("nonexistent_set") is v3   # unknown → baseline
    assert svc._select(None) is v3                 # None → baseline
