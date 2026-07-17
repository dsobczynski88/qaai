"""Direct tests for the SummarizedTestCaseList repairs.

The replay corpus (test_malformed_replay.py) pins the *observed* payloads; these pin the
*invariants* the registry promises for every payload: pure, idempotent, and a strict no-op
when the malformation is absent, so a well-formed response is never altered and a genuinely
broken one still fails validation instead of being fabricated into something plausible.
"""
import pytest

from qaai.agents.shared.json_repair_registry import (
    apply_repairs,
    unwrap_summarized_test_case_list,
    wrap_bare_summarized_test_case,
)
from qaai.agents.test_suite_reviewer.core import SummarizedTestCaseList

pytestmark = pytest.mark.unit


def _summary(tc_id="TC-1"):
    return {
        "test_case_id": tc_id,
        "objective": "o",
        "verifies": "v",
        "protocol": ["step"],
        "acceptance_criteria": ["ac"],
    }


# --- wrap_bare_summarized_test_case ---------------------------------------------------


def test_wrap_lifts_a_bare_summary_into_a_list():
    assert wrap_bare_summarized_test_case(_summary()) == [_summary()]


def test_wrap_is_a_noop_on_a_well_formed_list():
    payload = [_summary()]
    assert wrap_bare_summarized_test_case(payload) is payload


def test_wrap_is_idempotent():
    once = wrap_bare_summarized_test_case(_summary())
    assert wrap_bare_summarized_test_case(once) == once


def test_wrap_ignores_a_dict_without_the_marker_fields():
    """Only test_case_id + objective together identify the shape; anything else is left
    for validation to reject."""
    other = {"test_case_id": "TC-1"}  # no objective
    assert wrap_bare_summarized_test_case(other) is other
    assert wrap_bare_summarized_test_case({}) == {}


# --- unwrap_summarized_test_case_list -------------------------------------------------


@pytest.mark.parametrize("alias", ["summaries", "response", "summarized_test_cases", "test_cases"])
def test_unwrap_lifts_the_list_out_of_a_wrapper_key(alias):
    assert unwrap_summarized_test_case_list({alias: [_summary()]}) == [_summary()]


def test_unwrap_discards_the_req_id_echo():
    """Observed shape: {"req_id": ..., "summaries": [...]}. The model has no req_id field."""
    got = unwrap_summarized_test_case_list({"req_id": "REQ-1", "summaries": [_summary()]})
    assert got == [_summary()]


def test_unwrap_defers_a_bare_summary_to_the_wrap_repair():
    """A bare summary must not be mistaken for a wrapper even if it carries an alias key."""
    bare = {**_summary(), "test_cases": ["not a summary list"]}
    assert unwrap_summarized_test_case_list(bare) is bare


def test_unwrap_is_a_noop_when_no_alias_holds_a_list():
    payload = {"summaries": "not a list"}
    assert unwrap_summarized_test_case_list(payload) is payload


# --- the registered pipeline ----------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(_summary(), id="bare-singleton"),
        pytest.param({"summaries": [_summary()]}, id="summaries-wrapper"),
        pytest.param({"response": [_summary()]}, id="response-wrapper"),
        pytest.param({"req_id": "REQ-1", "summaries": [_summary(), _summary("TC-2")]}, id="req_id-echo"),
        pytest.param([_summary()], id="already-valid"),
    ],
)
def test_pipeline_recovers_every_known_shape(payload):
    repaired = apply_repairs(SummarizedTestCaseList, payload)
    model = SummarizedTestCaseList.model_validate(repaired)
    assert len(model.root) >= 1
    assert model.root[0].test_case_id == "TC-1"


def test_pipeline_leaves_genuine_garbage_unrecoverable():
    """An empty object carries no data to re-home; repairing it would mean inventing one.
    It must still fail validation so the node soft-skips."""
    with pytest.raises(Exception):
        SummarizedTestCaseList.model_validate(apply_repairs(SummarizedTestCaseList, {}))
