"""Input-gate unit tests for the single-test-case reviewer.

Confirm the graph short-circuits — zero LLM/inference calls, empty (skipped)
result — when a test case has no traced upstream requirements or no step text.

Uses the call-counting `stub_llm_client` fixture and cache_mode "off"; data
fixtures live in tests/conftest.py.
"""
import pytest

from qaai.agents.test_case_reviewer.pipeline import TCReviewerRunnable

pytestmark = pytest.mark.unit


def _build_graph(stub_llm_client):
    return TCReviewerRunnable(client=stub_llm_client, model="stub-model").graph


async def test_inputs_with_no_traced_requirements_are_skipped(
    stub_llm_client, review_settings, tc_input_no_requirements
):
    graph = _build_graph(stub_llm_client)
    state = {**tc_input_no_requirements, "cache_mode": review_settings.cache_mode}

    result = await graph.ainvoke(state)

    assert result.get("review_status") == "skipped"
    assert "requirements" in result.get("missing_fields", [])
    assert result.get("aggregated_assessment") is None  # viewer renders empty
    assert stub_llm_client.call_count == 0


async def test_inputs_with_no_test_case_steps_text_are_skipped(
    stub_llm_client, review_settings, tc_input_no_steps
):
    graph = _build_graph(stub_llm_client)
    state = {**tc_input_no_steps, "cache_mode": review_settings.cache_mode}

    result = await graph.ainvoke(state)

    assert result.get("review_status") == "skipped"
    assert "test_case_steps" in result.get("missing_fields", [])
    assert result.get("aggregated_assessment") is None
    assert stub_llm_client.call_count == 0
