"""Run the committed 20-record hazard pilot through the real API and emit the viewer.

This drives ``POST /api/v1/hazard-risk-review`` end-to-end (HTTP -> route -> job ->
``HazardReviewService`` -> graph -> ``write_viewer_hz``) over the FluxPump 4000 pilot at
``eval/datasets/hazard/actual/pilot-20-record/`` (copied to
``tests/fixtures/hazard/hazard_pilot_20.jsonl`` as the reusable source of truth, with a
matching real ``hazard_pilot_20.xlsx`` SHA workbook).

Why the parse seam is stubbed: the Excel-upload path (``_parse_uploaded_excel``) wraps each
row with an EMPTY ``HazardTraceMatrix`` and expects JAMA to supply the traced requirements /
test cases / design docs. The Excel format structurally cannot carry traceability, but the
pilot's value IS its embedded trace matrix. There is no live JAMA project for FluxPump, so we
inject the traceability-rich ``HazardRowWithTraceMatrix`` records (already present in the
JSONL) at that one seam while everything downstream runs for real. A real ``.xlsx`` is still
POSTed so the endpoint, multipart handling, job flow, and viewer generation are exercised.

Marked ``integration`` (real LLM calls). 20 records run sequentially
(``HAZARD_MAX_CONCURRENT_REVIEWS`` default 1), each fanning out the embedded RTM per traced
requirement, so this is slow and consumes real tokens.
"""
import json
from pathlib import Path

import pytest
from fastapi import status

from qaai.agents.hazard_risk_reviewer.core import HazardRowWithTraceMatrix
from qaai.api.services import HazardReviewService

_FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "hazard"
_JSONL = _FIXTURE_DIR / "hazard_pilot_20.jsonl"
_XLSX = _FIXTURE_DIR / "hazard_pilot_20.xlsx"
# Where the rendered report is copied so it can be launched in a browser.
_VIEWER_OUT = Path(__file__).parent.parent.parent / "logs" / "hazard_pilot_viewer.html"


def _load_pilot_records() -> list[HazardRowWithTraceMatrix]:
    rows: list[HazardRowWithTraceMatrix] = []
    with _JSONL.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                rows.append(HazardRowWithTraceMatrix(**json.loads(line)["hazard"]))
    return rows


@pytest.mark.integration
async def test_hazard_pilot_through_api_emits_viewer(submit_and_wait, monkeypatch):
    """Full 20-record hazard pilot -> HTML report, saved to logs/hazard_pilot_viewer.html."""
    records = _load_pilot_records()
    assert len(records) == 20, f"expected 20 pilot records, got {len(records)}"

    # Inject the traceability-rich records at the file-decode seam (see module docstring).
    monkeypatch.setattr(
        HazardReviewService,
        "_parse_uploaded_excel",
        lambda self, *args, **kwargs: records,
    )

    xlsx_bytes = _XLSX.read_bytes()
    response = await submit_and_wait(
        "/api/v1/hazard-risk-review",
        files={
            "file": (
                "hazard_pilot_20.xlsx",
                xlsx_bytes,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
        data={
            "project_name": "FluxPump 4000",
            "sheet_name": "SHA Table",
            "identifier_pattern": r"REQ-PUMP-\d+",
            "cache_mode": "on",
            "include_edge_case_analysis": "false",
        },
        max_wait=1800,
    )

    assert response.status_code == status.HTTP_200_OK, response.text
    assert "text/html" in response.headers.get("content-type", "")

    _VIEWER_OUT.parent.mkdir(parents=True, exist_ok=True)
    _VIEWER_OUT.write_bytes(response.content)
    # Sanity: the hazard viewer payload embeds the per-record DATA blob.
    assert b'id="DATA"' in response.content
