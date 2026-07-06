"""Regression tests for the AggregatorNode "wrapped under test_case" malformation.

In production run logs/run-2026-07-06_14-27-40 (entity PRJ01713-TEST-2085) the
aggregator LLM emitted a complete, correct assessment but nested every field inside a
single ``test_case`` key. The parsed dict's only top-level key was ``test_case``, so
``TestCaseAssessment`` validation reported ``requirements`` / ``evaluated_checklist`` /
``overall_verdict`` as missing, all choices failed to parse, and the whole item was
dropped (and its cache purged).

``_unwrap_wrapped_assessment`` (folded into
``TestCaseAssessment._coerce_overall_partial_alias``) recovers the assessment
deterministically by lifting the assessment-level fields back out of ``test_case``.
These tests pin that behavior — and its non-fabrication guarantee. No LLM/network calls.

The fixture ``aggregator_wrapped_in_test_case.json`` is a trimmed but structurally exact
copy of the real failing response.
"""
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from qaai.agents.test_case_reviewer.core import TestCaseAssessment

pytestmark = pytest.mark.unit

FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "fixtures" / "mock" / "aggregator_wrapped_in_test_case.json"
)


def _load_wrapped() -> dict:
    """Fresh copy of the malformed (wrapped-under-test_case) payload each call."""
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_wrapped_assessment_is_recovered():
    wrapped = _load_wrapped()
    # Precondition: the malformation — the only top-level key is test_case, and the
    # assessment fields are nested one level too deep inside it.
    assert list(wrapped.keys()) == ["test_case"]
    assert "evaluated_checklist" in wrapped["test_case"]

    assessment = TestCaseAssessment.model_validate(wrapped)

    assert assessment.overall_verdict == "Yes"
    assert len(assessment.evaluated_checklist) == 5
    assert len(assessment.requirements) == 2
    assert len(assessment.decomposed_requirements) == 2
    # test_case is reduced to a real TestCase — no assessment fields leaked into it.
    assert assessment.test_case.test_id == "PRJ01713-TEST-2085"
    dumped_tc = assessment.test_case.model_dump()
    assert "evaluated_checklist" not in dumped_tc
    assert "requirements" not in dumped_tc
    assert "overall_verdict" not in dumped_tc


def test_wellformed_assessment_passthrough():
    """A correctly-shaped payload validates unchanged (normalizer is a no-op)."""
    wrapped = _load_wrapped()
    tc = dict(wrapped["test_case"])
    correct = {
        "requirements": tc.pop("requirements"),
        "decomposed_requirements": tc.pop("decomposed_requirements"),
        "evaluated_checklist": tc.pop("evaluated_checklist"),
        "overall_verdict": tc.pop("overall_verdict"),
        "comments": tc.pop("comments"),
        "clarification_questions": tc.pop("clarification_questions"),
        "test_case": tc,
    }
    assessment = TestCaseAssessment.model_validate(correct)
    assert assessment.overall_verdict == "Yes"
    assert len(assessment.evaluated_checklist) == 5
    assert len(assessment.requirements) == 2


def test_genuine_omission_still_fails():
    """No fabrication: if evaluated_checklist is truly absent everywhere, validation
    must still fail — the normalizer only re-homes fields that already exist."""
    wrapped = _load_wrapped()
    tc = dict(wrapped["test_case"])
    tc.pop("evaluated_checklist")
    tc.pop("overall_verdict")
    # Lift requirements to the top level so the sentinel (evaluated_checklist) is absent
    # at BOTH levels — i.e. the model genuinely did not produce the checklist.
    payload = {"requirements": tc.pop("requirements"), "test_case": tc}
    with pytest.raises(ValidationError):
        TestCaseAssessment.model_validate(payload)


def test_partial_alias_survives_unnest():
    """The folded-in un-nest step must not break the existing 'Partial' -> 'Yes'
    overall_verdict coercion (which reads the now-lifted top-level verdict)."""
    wrapped = _load_wrapped()
    wrapped["test_case"]["overall_verdict"] = "Partial"
    assessment = TestCaseAssessment.model_validate(wrapped)
    assert assessment.overall_verdict == "Yes"


def test_recovered_via_node_parse_path():
    """End-to-end through the base-node parser: the raw wrapped string is recovered into
    a populated TestCaseAssessment instead of a skip (all-choices-failed) response."""
    from qaai.agents.shared.nodes import BaseLLMNode

    raw = FIXTURE.read_text(encoding="utf-8")

    class _Msg:
        content = raw

    class _Choice:
        message = _Msg()

    class _Result:
        choices = [_Choice()]

    parsed = BaseLLMNode._parse_llm_response(_Result(), TestCaseAssessment, "AggregatorNode")
    assert isinstance(parsed, TestCaseAssessment)
    assert parsed.overall_verdict == "Yes"
    assert len(parsed.evaluated_checklist) == 5
    assert len(parsed.requirements) == 2
