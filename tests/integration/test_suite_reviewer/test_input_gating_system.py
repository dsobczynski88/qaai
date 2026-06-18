"""System-level (full-pipeline) test for the test-suite reviewer's input gate.

Drives a bad-input record through the real compiled graph, writes outputs.jsonl
exactly as the API service would, renders the HTML viewer, and asserts the
system-level behavior: the rubric renders empty and the missing-fields warning
banner is present. The skip path performs no LLM calls, so this needs no live
endpoint (a call-counting stub client stands in).
"""
import json

import pytest

from qaai.agents.test_suite_reviewer.pipeline import RTMReviewerRunnable
from qaai.viewer.generator import write_viewer

pytestmark = pytest.mark.integration


async def test_skipped_record_renders_empty_with_warning(
    tmp_path, stub_llm_client, review_settings, rtm_input_no_test_cases
):
    graph = RTMReviewerRunnable(client=stub_llm_client, model="stub-model").graph
    state = {**rtm_input_no_test_cases, "cache_mode": review_settings.cache_mode}

    result = await graph.ainvoke(state)

    # No inference happened and no assessment was produced.
    assert stub_llm_client.call_count == 0
    assert result.get("review_status") == "skipped"
    assert result.get("synthesized_assessment") is None

    outputs = tmp_path / "outputs.jsonl"
    outputs.write_text(json.dumps(result, default=str) + "\n", encoding="utf-8")

    viewer_path = write_viewer(outputs)
    html = viewer_path.read_text(encoding="utf-8")

    # System-level: warning banner present and the skipped record (with its
    # missing fields) is embedded. The rubric renders empty client-side because
    # no synthesized_assessment was produced (asserted at the state level above).
    assert 'id="missing-warning"' in html
    assert '"review_status": "skipped"' in html
    assert '"missing_fields"' in html and "test_cases" in html
