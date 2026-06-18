"""Unit tests for the hazard reviewer's bidirectional_trace data-integration
transform (no live JAMA calls).

Covers `transform_bidirectional_trace_to_state` (per-requirement entries ->
single aggregated HazardTraceMatrix) and the graph node factory
`make_transform_node_bidirectional_trace` (merge onto the in-state hazard,
no-op in Excel/local mode).
"""
import pytest

from qaai.agents.shared.data_integration import (
    transform_bidirectional_trace_to_state,
    make_transform_node_bidirectional_trace,
)
from qaai.agents.hazard_risk_reviewer.core import (
    HazardRowWithTraceMatrix,
    HazardTraceMatrix,
)


# Test-data fixtures (`jama_data`, `bare_hazard`) live in tests/conftest.py so
# they can be shared across the unit and integration suites.


# ---------------------------------------------------------------------------
# transform_bidirectional_trace_to_state
# ---------------------------------------------------------------------------


def test_aggregates_and_dedups_across_entries(jama_data):
    matrix = transform_bidirectional_trace_to_state(jama_data)

    assert isinstance(matrix, HazardTraceMatrix)
    # One requirement per entry, no dedup collapse (distinct ids)
    assert [r.req_id for r in matrix.requirements] == ["REQ-1", "REQ-2"]
    # TC-1 shared across both entries -> deduped; TC-2 unique
    assert sorted(tc.test_id for tc in matrix.test_cases) == ["TC-1", "TC-2"]
    # DD-1 shared -> single entry
    assert [dd.doc_id for dd in matrix.design_docs] == ["DD-1"]
    # SYS-1 shared across both entries -> deduped
    assert [sr.req_id for sr in matrix.system_requirements] == ["SYS-1"]
    # Nested user needs flattened and deduped (UND-1 shared, UND-2 unique)
    assert sorted(un.req_id for un in matrix.user_needs) == ["UND-1", "UND-2"]


def test_in_review_baseline_maps_to_in_baseline(jama_data):
    matrix = transform_bidirectional_trace_to_state(jama_data)
    by_id = {tc.test_id: tc for tc in matrix.test_cases}
    assert by_id["TC-1"].in_baseline is True
    assert by_id["TC-2"].in_baseline is False


def test_empty_input_returns_empty_matrix():
    matrix = transform_bidirectional_trace_to_state([])
    assert isinstance(matrix, HazardTraceMatrix)
    assert matrix.requirements == []
    assert matrix.test_cases == []
    assert matrix.design_docs == []
    assert matrix.system_requirements == []
    assert matrix.user_needs == []


def test_malformed_entries_are_skipped_not_raised():
    data = [
        "not-a-dict",
        {"requirement": None, "test_cases": None, "design_docs": None},
        {"requirement": {"req_id": "REQ-9", "text": "ok"}},
    ]
    matrix = transform_bidirectional_trace_to_state(data)
    assert [r.req_id for r in matrix.requirements] == ["REQ-9"]


# ---------------------------------------------------------------------------
# make_transform_node_bidirectional_trace
# ---------------------------------------------------------------------------


def test_node_merges_jama_data_onto_hazard(bare_hazard, jama_data):
    transform = make_transform_node_bidirectional_trace()
    out = transform({"hazard": bare_hazard, "jama_data": jama_data})

    assert "hazard" in out
    merged = out["hazard"]
    # Original hazard fields preserved; traceability populated from JAMA
    assert merged.hazard_id == "HAZ-1"
    assert [r.req_id for r in merged.requirements_traceability.requirements] == ["REQ-1", "REQ-2"]
    assert sorted(tc.test_id for tc in merged.requirements_traceability.test_cases) == ["TC-1", "TC-2"]


def test_node_is_noop_without_jama_data(bare_hazard):
    transform = make_transform_node_bidirectional_trace()
    # Local/Excel mode: no jama_data -> no-op, hazard untouched
    assert transform({"hazard": bare_hazard}) == {}
    assert transform({"hazard": bare_hazard, "jama_data": []}) == {}


def test_node_is_noop_when_jama_data_present_but_no_hazard(jama_data):
    transform = make_transform_node_bidirectional_trace()
    assert transform({"jama_data": jama_data}) == {}
