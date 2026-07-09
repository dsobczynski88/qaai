"""Input-gate unit tests for the test-suite (RTM) reviewer.

Confirm that the graph short-circuits — performing **zero LLM/inference calls**
and producing an empty (skipped) result — when a requirement lacks traced test
cases or requirement text, and that a missing design-docs list does NOT skip
(the graph proceeds normally).

All tests use the call-counting `stub_llm_client` fixture (review_settings.test_mode)
and cache_mode "off"; data fixtures live in tests/conftest.py.
"""
import pytest
from langgraph.graph import END

from qaai.agents.test_suite_reviewer.nodes import (
    validate_rtm_inputs,
    route_after_gate_rtm,
)
from qaai.agents.shared.gate import SKIP_STATUS
from qaai.agents.test_suite_reviewer.pipeline import RTMReviewerRunnable

pytestmark = pytest.mark.unit


def _build_graph(stub_llm_client):
    # cache_manager=None ⇒ no node caches; the stub client makes no network calls.
    return RTMReviewerRunnable(client=stub_llm_client, model="stub-model").graph


async def test_inputs_with_no_traced_test_cases_are_skipped(
    stub_llm_client, review_settings, rtm_input_no_test_cases
):
    graph = _build_graph(stub_llm_client)
    state = {**rtm_input_no_test_cases, "cache_mode": review_settings.cache_mode}

    result = await graph.ainvoke(state)

    assert result.get("review_status") == "skipped"
    assert "test_cases" in result.get("missing_fields", [])
    assert result.get("synthesized_assessment") is None  # viewer renders empty
    assert stub_llm_client.call_count == 0  # graph ended before any inference


async def test_inputs_with_no_requirement_text_are_skipped(
    stub_llm_client, review_settings, rtm_input_no_requirement_text
):
    graph = _build_graph(stub_llm_client)
    state = {**rtm_input_no_requirement_text, "cache_mode": review_settings.cache_mode}

    result = await graph.ainvoke(state)

    assert result.get("review_status") == "skipped"
    assert "requirement_text" in result.get("missing_fields", [])
    assert result.get("synthesized_assessment") is None
    assert stub_llm_client.call_count == 0


async def test_inputs_with_no_design_docs_are_completed(
    stub_llm_client, review_settings, rtm_input_no_design_docs
):
    # Missing design docs must NOT gate the review: the validation predicate
    # reports nothing missing and the gate router fans out to the work nodes.
    assert validate_rtm_inputs(rtm_input_no_design_docs) == []

    graph = _build_graph(stub_llm_client)
    state = {**rtm_input_no_design_docs, "cache_mode": review_settings.cache_mode}

    result = await graph.ainvoke(state)

    # Gate let the graph proceed (not skipped) and the work nodes ran (the stub
    # client was invoked at least once). A full assessment isn't asserted here
    # because the LLM is stubbed.
    assert result.get("review_status") != "skipped"
    assert stub_llm_client.call_count > 0


# --- "Include Design Summaries" toggle → conditional design_summarizer branch ---


def test_route_after_gate_rtm_skips_design_by_default():
    """The gate router omits design_summarizer unless the toggle is set, and
    routes to END when the input gate marked the record skipped."""
    assert route_after_gate_rtm({}) == ["decomposer", "summarizer"]
    assert route_after_gate_rtm({"include_design_summaries": False}) == [
        "decomposer", "summarizer",
    ]
    assert route_after_gate_rtm({"include_design_summaries": True}) == [
        "decomposer", "summarizer", "design_summarizer",
    ]
    assert route_after_gate_rtm({"review_status": SKIP_STATUS}) == END
    # Skip status wins even when the design toggle is on.
    assert route_after_gate_rtm(
        {"review_status": SKIP_STATUS, "include_design_summaries": True}
    ) == END


async def test_design_summarizer_runs_only_when_toggle_on(
    stub_llm_client, review_settings, sample_requirement, sample_test_cases,
    sample_design_docs,
):
    """With design docs present, the design_summarizer branch adds LLM calls only
    when include_design_summaries is True; with it False the branch is skipped yet
    the graph still completes (coverage_router join fires on the remaining two)."""
    assert sample_design_docs, "fixture must supply design docs for this test"
    graph = _build_graph(stub_llm_client)
    base = {
        "requirement": sample_requirement,
        "test_cases": sample_test_cases,
        "design_docs": sample_design_docs,
        "cache_mode": review_settings.cache_mode,
    }

    # Toggle OFF: design branch skipped, graph still proceeds past the gate.
    off = await graph.ainvoke({**base, "include_design_summaries": False})
    assert off.get("review_status") != "skipped"
    calls_off = stub_llm_client.call_count

    # Toggle ON: a fresh graph/client so counts are comparable; the design
    # branch now runs and issues at least one additional call.
    on_client = stub_llm_client.__class__()
    graph_on = _build_graph(on_client)
    on = await graph_on.ainvoke({**base, "include_design_summaries": True})
    assert on.get("review_status") != "skipped"
    assert on_client.call_count > calls_off, (
        "design_summarizer branch should add LLM calls when the toggle is on"
    )
