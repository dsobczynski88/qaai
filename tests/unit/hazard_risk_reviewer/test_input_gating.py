"""Input-gate unit tests for the hazard risk reviewer.

Confirm the graph short-circuits — zero LLM/inference calls, empty (skipped)
result — when a hazard references no risk-control requirements, or is missing
any field in HAZARD_RISK_REVIEWER_REQUIRED_HAZARD_FIELDS.

Uses the call-counting `stub_llm_client` fixture and cache_mode "off"; data
fixtures live in tests/conftest.py.
"""
import pytest

from qaai.agents.hazard_risk_reviewer.pipeline import HazardReviewerRunnable

pytestmark = pytest.mark.unit


def _build_graph(stub_llm_client):
    # cache_manager=None keeps the embedded RTM subgraph + hazard nodes cache-free.
    return HazardReviewerRunnable(
        client=stub_llm_client, model="stub-model", cache_manager=None
    ).graph


async def test_inputs_with_no_traced_controls_are_skipped(
    stub_llm_client, review_settings, hazard_input_no_controls
):
    graph = _build_graph(stub_llm_client)
    state = {**hazard_input_no_controls, "cache_mode": review_settings.cache_mode}

    result = await graph.ainvoke(state)

    assert result.get("review_status") == "skipped"
    assert "risk_control_requirements" in result.get("missing_fields", [])
    assert result.get("hazard_assessment") is None  # viewer renders empty
    assert stub_llm_client.call_count == 0


async def test_inputs_with_missing_hazard_fields_are_skipped(
    stub_llm_client, review_settings, hazard_input_missing_fields
):
    graph = _build_graph(stub_llm_client)
    state = {**hazard_input_missing_fields, "cache_mode": review_settings.cache_mode}

    result = await graph.ainvoke(state)

    assert result.get("review_status") == "skipped"
    missing = result.get("missing_fields", [])
    # The blanked required fields are reported (see hazard_input_missing_fields).
    assert {"harm", "severity", "final_risk_rating"}.issubset(set(missing))
    assert result.get("hazard_assessment") is None
    assert stub_llm_client.call_count == 0
