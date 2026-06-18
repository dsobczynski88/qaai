"""System-level (full-pipeline) test for the test-case reviewer's input gate.

See tests/integration/test_suite_reviewer/test_input_gating_system.py for the
rationale; here the bad-input record is a test case with no traced requirements.
"""
import json

import pytest

from qaai.agents.test_case_reviewer.pipeline import TCReviewerRunnable
from qaai.viewer.generator import write_viewer_tc

pytestmark = pytest.mark.integration


async def test_skipped_record_renders_empty_with_warning(
    tmp_path, stub_llm_client, review_settings, tc_input_no_requirements
):
    graph = TCReviewerRunnable(client=stub_llm_client, model="stub-model").graph
    state = {**tc_input_no_requirements, "cache_mode": review_settings.cache_mode}

    result = await graph.ainvoke(state)

    assert stub_llm_client.call_count == 0
    assert result.get("review_status") == "skipped"
    assert result.get("aggregated_assessment") is None

    outputs = tmp_path / "outputs.jsonl"
    outputs.write_text(json.dumps(result, default=str) + "\n", encoding="utf-8")

    viewer_path = write_viewer_tc(outputs)
    html = viewer_path.read_text(encoding="utf-8")

    assert 'id="missing-warning"' in html
    assert '"review_status": "skipped"' in html
    assert '"missing_fields"' in html and "requirements" in html
