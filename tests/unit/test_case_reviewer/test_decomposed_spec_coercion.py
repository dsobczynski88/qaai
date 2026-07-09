"""Regression tests for the DecomposedRequirement "specs under the wrong key" malformation.

In decomposition mode (prompt set test_case_reviewer_v2, aggregator
single_test_aggregator/v8.0.0) the aggregator LLM echoes back ``decomposed_requirements``,
each item of which must carry ``decomposed_specifications: List[DecomposedSpec]``. The model
intermittently nests the specs under the WRONG key ``decomposed_requirements`` (reusing the
parent list's own name), so Pydantic reports ``decomposed_specifications`` as a missing
required field, the whole ``TestCaseAssessment`` fails to validate, and the item is dropped
(and its cache purged) — the failure observed for TEST-2146.

``DecomposedRequirement._coerce_spec_field_name`` (a ``mode="before"`` validator in
qaai/agents/shared/core.py) recovers deterministically by re-homing the mislabeled list to
``decomposed_specifications`` when the canonical key is absent. These tests pin that behavior
and its non-fabrication guarantee. No LLM/network calls. Mirrors test_aggregator_unwrap.py.
"""
import copy
import json

import pytest
from pydantic import ValidationError

from qaai.agents.shared.core import DecomposedRequirement
from qaai.agents.test_case_reviewer.core import TestCaseAssessment

pytestmark = pytest.mark.unit


def _spec() -> dict:
    return {
        "spec_id": "REQ-1-S1",
        "description": "The service shall do the thing.",
        "acceptance_criteria": "Given a valid request, the thing is done.",
        "rationale": "Explicit: preserves the shall-statement.",
    }


def _wellformed_assessment() -> dict:
    """A correct, minimal TestCaseAssessment payload in decomposition mode."""
    return {
        "test_case": {
            "test_id": "PRJ-TEST-2146",
            "description": "Exercise the thing",
            "setup": "Preconditions established.",
            "steps": "Step 1. Do the thing. Step 2. Verify.",
            "expectedResults": "Thing done; verification passes.",
        },
        "requirements": [
            {"req_id": "REQ-1", "text": "The service shall do the thing."},
            {"req_id": "REQ-2", "text": "The service shall verify the thing."},
        ],
        "decomposed_requirements": [
            {
                "requirement": {"req_id": "REQ-1", "text": "The service shall do the thing."},
                "decomposed_specifications": [_spec()],
            },
            {
                "requirement": {"req_id": "REQ-2", "text": "The service shall verify the thing."},
                "decomposed_specifications": [{**_spec(), "spec_id": "REQ-2-S1"}],
            },
        ],
        "evaluated_checklist": [
            {
                "id": "expected_result_support",
                "description": "d1",
                "mandatory": True,
                "verdict": "Yes",
                "partial": False,
                "assessment": "a1",
            },
            {
                "id": "expected_result_spec_align",
                "description": "d2",
                "mandatory": True,
                "verdict": "Yes",
                "partial": False,
                "assessment": "a2",
            },
            {
                "id": "test_case_achieves",
                "description": "d3",
                "mandatory": True,
                "verdict": "Yes",
                "partial": False,
                "assessment": "a3",
            },
            {
                "id": "test_case_logical_sequence",
                "description": "d4",
                "mandatory": True,
                "verdict": "Yes",
                "partial": False,
                "assessment": "a4",
            },
            {
                "id": "test_case_setup_clarity",
                "description": "d5",
                "mandatory": False,
                "verdict": "Yes",
                "partial": False,
                "assessment": "a5",
            },
        ],
        "overall_verdict": "Yes",
        "comments": "",
        "clarification_questions": [],
    }


def _mislabel_specs(assessment: dict) -> dict:
    """Rename each item's ``decomposed_specifications`` -> ``decomposed_requirements``
    (the observed malformation)."""
    out = copy.deepcopy(assessment)
    for item in out["decomposed_requirements"]:
        item["decomposed_requirements"] = item.pop("decomposed_specifications")
    return out


def test_item_level_coercion():
    """A DecomposedRequirement dict with specs under the wrong key validates and exposes
    them under decomposed_specifications."""
    item = {
        "requirement": {"req_id": "REQ-1", "text": "The service shall do the thing."},
        "decomposed_requirements": [_spec()],  # wrong key
    }
    dr = DecomposedRequirement.model_validate(item)
    assert len(dr.decomposed_specifications) == 1
    assert dr.decomposed_specifications[0].spec_id == "REQ-1-S1"


def test_full_assessment_with_mislabeled_specs():
    """The real TEST-2146 failure shape: a TestCaseAssessment whose decomposed_requirements
    items nest specs under the wrong key validates cleanly."""
    malformed = _mislabel_specs(_wellformed_assessment())
    # Precondition: the malformation is present.
    assert "decomposed_requirements" in malformed["decomposed_requirements"][0]
    assert "decomposed_specifications" not in malformed["decomposed_requirements"][0]

    assessment = TestCaseAssessment.model_validate(malformed)

    assert len(assessment.decomposed_requirements) == 2
    assert [len(d.decomposed_specifications) for d in assessment.decomposed_requirements] == [1, 1]
    assert assessment.decomposed_requirements[0].decomposed_specifications[0].spec_id == "REQ-1-S1"
    assert assessment.overall_verdict == "Yes"


def test_node_parse_path_recovers():
    """End-to-end through the base-node parser: the raw malformed JSON string is recovered
    into a populated TestCaseAssessment instead of a skip (all-choices-failed) response."""
    from qaai.agents.shared.nodes import BaseLLMNode

    raw = json.dumps(_mislabel_specs(_wellformed_assessment()))

    class _Msg:
        content = raw

    class _Choice:
        message = _Msg()

    class _Result:
        choices = [_Choice()]

    parsed = BaseLLMNode._parse_llm_response(_Result(), TestCaseAssessment, "AggregatorNode")
    assert isinstance(parsed, TestCaseAssessment)
    assert len(parsed.decomposed_requirements) == 2
    assert parsed.decomposed_requirements[1].decomposed_specifications[0].spec_id == "REQ-2-S1"


def test_wellformed_passthrough():
    """A correctly-keyed payload validates unchanged (the coercion is a no-op)."""
    assessment = TestCaseAssessment.model_validate(_wellformed_assessment())
    assert len(assessment.decomposed_requirements) == 2
    assert assessment.decomposed_requirements[0].decomposed_specifications[0].spec_id == "REQ-1-S1"


def test_genuine_omission_still_fails():
    """No fabrication: an item with neither decomposed_specifications nor any alias must
    still raise — the coercion only re-homes a list that already exists."""
    item = {"requirement": {"req_id": "REQ-1", "text": "The service shall do the thing."}}
    with pytest.raises(ValidationError):
        DecomposedRequirement.model_validate(item)
