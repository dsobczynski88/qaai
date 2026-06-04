"""Unit tests for the hazard reviewer's bidirectional_trace data-integration
transform (no live JAMA calls).

Covers `transform_bidirectional_trace_to_state` (per-requirement entries ->
single aggregated HazardTraceMatrix) and the graph node factory
`make_transform_node_bidirectional_trace` (merge onto the in-state hazard,
no-op in Excel/local mode).
"""
import pytest

from autoqa.components.shared.data_integration import (
    transform_bidirectional_trace_to_state,
    make_transform_node_bidirectional_trace,
)
from autoqa.components.hazard_risk_reviewer.core import (
    HazardRowWithTraceMatrix,
    HazardTraceMatrix,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _jama_data():
    """Two-entry bidirectional_trace response with overlapping artifacts.

    REQ-1 and REQ-2 both trace to TC-1 and DD-1 (shared) so we can assert
    deduplication; REQ-2 adds TC-2. SYS-1 is shared across both entries and
    carries nested user needs UND-1 (shared) and UND-2 (unique).
    """
    return [
        {
            "requirement": {"req_id": "REQ-1", "text": "Req one text"},
            "system_requirements": [
                {
                    "req_id": "SYS-1",
                    "text": "System req one",
                    "user_needs": [{"req_id": "UND-1", "text": "Need one"}],
                }
            ],
            "test_cases": [
                {
                    "test_id": "TC-1",
                    "description": "Test one",
                    "setup": "s",
                    "steps": "st",
                    "expectedResults": "er",
                    "in_review_baseline": True,
                }
            ],
            "design_docs": [{"doc_id": "DD-1", "name": "Design one", "description": "d"}],
        },
        {
            "requirement": {"req_id": "REQ-2", "text": "Req two text"},
            "system_requirements": [
                {
                    "req_id": "SYS-1",
                    "text": "System req one",
                    "user_needs": [
                        {"req_id": "UND-1", "text": "Need one"},
                        {"req_id": "UND-2", "text": "Need two"},
                    ],
                }
            ],
            "test_cases": [
                {"test_id": "TC-1", "description": "Test one", "setup": "s",
                 "steps": "st", "expectedResults": "er", "in_review_baseline": True},
                {"test_id": "TC-2", "description": "Test two", "in_review_baseline": False},
            ],
            "design_docs": [{"doc_id": "DD-1", "name": "Design one", "description": "d"}],
        },
    ]


# ---------------------------------------------------------------------------
# transform_bidirectional_trace_to_state
# ---------------------------------------------------------------------------


def test_aggregates_and_dedups_across_entries():
    matrix = transform_bidirectional_trace_to_state(_jama_data())

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


def test_in_review_baseline_maps_to_in_baseline():
    matrix = transform_bidirectional_trace_to_state(_jama_data())
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


def _bare_hazard():
    """A hazard row with empty traceability (the Excel-parsed starting point)."""
    return HazardRowWithTraceMatrix(
        hazard_id="HAZ-1",
        requirements_traceability=HazardTraceMatrix(),
    )


def test_node_merges_jama_data_onto_hazard():
    transform = make_transform_node_bidirectional_trace()
    out = transform({"hazard": _bare_hazard(), "jama_data": _jama_data()})

    assert "hazard" in out
    merged = out["hazard"]
    # Original hazard fields preserved; traceability populated from JAMA
    assert merged.hazard_id == "HAZ-1"
    assert [r.req_id for r in merged.requirements_traceability.requirements] == ["REQ-1", "REQ-2"]
    assert sorted(tc.test_id for tc in merged.requirements_traceability.test_cases) == ["TC-1", "TC-2"]


def test_node_is_noop_without_jama_data():
    transform = make_transform_node_bidirectional_trace()
    hazard = _bare_hazard()
    # Local/Excel mode: no jama_data -> no-op, hazard untouched
    assert transform({"hazard": hazard}) == {}
    assert transform({"hazard": hazard, "jama_data": []}) == {}


def test_node_is_noop_when_jama_data_present_but_no_hazard():
    transform = make_transform_node_bidirectional_trace()
    assert transform({"jama_data": _jama_data()}) == {}
