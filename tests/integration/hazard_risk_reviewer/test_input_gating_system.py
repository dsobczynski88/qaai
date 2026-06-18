"""System-level (full-pipeline) test for the hazard reviewer's input gate.

See tests/integration/test_suite_reviewer/test_input_gating_system.py for the
rationale; here the bad-input record is a hazard missing required SHA fields.
"""
import json

import pytest

from qaai.agents.hazard_risk_reviewer.pipeline import HazardReviewerRunnable
from qaai.viewer.generator import write_viewer_hz

pytestmark = pytest.mark.integration


async def test_skipped_record_renders_empty_with_warning(
    tmp_path, stub_llm_client, review_settings, hazard_input_missing_fields
):
    graph = HazardReviewerRunnable(
        client=stub_llm_client, model="stub-model", cache_manager=None
    ).graph
    state = {**hazard_input_missing_fields, "cache_mode": review_settings.cache_mode}

    result = await graph.ainvoke(state)

    assert stub_llm_client.call_count == 0
    assert result.get("review_status") == "skipped"
    assert result.get("hazard_assessment") is None

    outputs = tmp_path / "outputs.jsonl"
    outputs.write_text(json.dumps(result, default=str) + "\n", encoding="utf-8")

    viewer_path = write_viewer_hz(outputs)
    html = viewer_path.read_text(encoding="utf-8")

    assert 'id="missing-warning"' in html
    assert '"review_status": "skipped"' in html
    assert '"missing_fields"' in html and "final_risk_rating" in html
