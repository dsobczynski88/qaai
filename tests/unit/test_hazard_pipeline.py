"""
Unit tests for the hazard risk reviewer pipeline.

Each unit test exercises one node or dispatcher in isolation with a mocked
LLM client (per-dimension Hn evaluators + final assessor) or a mocked
RTMReviewerRunnable (RequirementReviewerNode), mirroring the patterns in
tests/unit/test_summary_node.py and test_decomposer_node.py.

Verdict convention: each H1-H5 finding is binary Yes/No (H4 may also be
N-A); overall_verdict is computed deterministically by the final_assessor
node and is Yes iff every dimension is Yes or N-A.
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from autoqa.components.hazard_risk_reviewer.core import (
    HazardAssessment,
    HazardFinding,
    RequirementReview,
)
from autoqa.components.hazard_risk_reviewer.nodes import (
    RequirementReviewerNode,
    _FinalAssessorNode,
    _H1EvaluatorNode,
    _H2EvaluatorNode,
    _H3EvaluatorNode,
    _H4EvaluatorNode,
    _H5EvaluatorNode,
    dispatch_requirement_reviews,
)
from autoqa.components.hazard_risk_reviewer.core import FinalAssessorProse
from autoqa.components.test_suite_reviewer.core import (
    MandatoryFinding,
    SynthesizedAssessment,
)
from tests.helpers import make_mock_client


# --- canonical mock responses -------------------------------------------


def _hf(code: str, dimension: str, verdict: str, **extras) -> dict:
    """Build a HazardFinding-shaped dict ready to JSON-encode for an LLM mock."""
    out = {
        "code": code,
        "dimension": dimension,
        "verdict": verdict,
        "rationale": extras.get("rationale", f"{code} {verdict}"),
        "cited_req_ids": extras.get("cited_req_ids", []),
        "cited_test_case_ids": extras.get("cited_test_case_ids", []),
        "unblocked_items": extras.get("unblocked_items", []),
    }
    return out


H1_YES = json.dumps(_hf("H1", "Hazard Statement Completeness", "Yes",
                        rationale="Chain is consistent."))
H1_NO = json.dumps(_hf("H1", "Hazard Statement Completeness", "No",
                       rationale="Hazardous situation is empty."))
H2_YES = json.dumps(_hf("H2", "Pre-Mitigation Risk", "Yes",
                        rationale="Risk profile is populated and consistent."))
H3_YES = json.dumps(_hf("H3", "Risk Control Adequacy", "Yes",
                        rationale="All software causes are controlled by REQ-PUMP-101.",
                        cited_req_ids=["REQ-PUMP-101"]))
H3_NO = json.dumps(_hf("H3", "Risk Control Adequacy", "No",
                       rationale="No controlling requirement for the scheduler-stall cause.",
                       unblocked_items=["Scheduler stall under heavy task load"]))
H4_YES = json.dumps(_hf("H4", "Verification Depth", "Yes",
                        rationale="Fault injection and boundary tests verify the controls.",
                        cited_req_ids=["REQ-PUMP-101"],
                        cited_test_case_ids=["TC-PUMP-202", "TC-PUMP-203"]))
H4_NO = json.dumps(_hf("H4", "Verification Depth", "No",
                       rationale="Only the functional happy path is exercised.",
                       cited_test_case_ids=["TC-PUMP-201"],
                       unblocked_items=["REQ-PUMP-101 watchdog latch behavior"]))
H4_NA = json.dumps(_hf("H4", "Verification Depth", "N-A",
                       rationale="No software-related causes — software verification depth is not applicable."))
H5_YES = json.dumps(_hf("H5", "Residual Risk Closure", "Yes",
                        rationale="Probability downgrade Probable to Remote is supported by verified controls."))
H5_NO = json.dumps(_hf("H5", "Residual Risk Closure", "No",
                       rationale="Probability downgrade is unsupported by software verification evidence."))

FINAL_PROSE_EMPTY = json.dumps({"comments": "", "clarification_questions": []})
FINAL_PROSE_INADEQUATE = json.dumps({
    "comments": "Watchdog control claims are not backed by fault-injection tests.",
    "clarification_questions": [
        "Is fault-injection coverage of REQ-PUMP-101 planned in a separate test campaign?",
    ],
})


def _make_mandatory_findings_yes() -> list[MandatoryFinding]:
    """Return five M1-M5 findings, all Yes — for fabricating a 'good' RTM SynthesizedAssessment."""
    return [
        MandatoryFinding(code="M1", dimension="Functional", verdict="Yes",
                         rationale="happy path verified", cited_test_case_ids=["TC-PUMP-201"]),
        MandatoryFinding(code="M2", dimension="Negative", verdict="Yes",
                         rationale="fault injection verified", cited_test_case_ids=["TC-PUMP-202"]),
        MandatoryFinding(code="M3", dimension="Boundary", verdict="Yes",
                         rationale="boundary verified", cited_test_case_ids=["TC-PUMP-203"]),
        MandatoryFinding(code="M4", dimension="Spec Coverage", verdict="Yes", rationale="all specs covered"),
        MandatoryFinding(code="M5", dimension="Terminology", verdict="Yes", rationale="aligned"),
    ]


def _good_review(req) -> RequirementReview:
    """Build a RequirementReview that carries a fully-Yes RTM SynthesizedAssessment."""
    sa = SynthesizedAssessment(
        requirement=req,
        overall_verdict="Yes",
        mandatory_findings=_make_mandatory_findings_yes(),
        comments="",
        clarification_questions=[],
    )
    return RequirementReview(requirement=req, synthesized_assessment=sa)


# --- per-dimension Hn evaluator nodes -----------------------------------


def _make_node(cls, mock_response: str, response_model=HazardFinding):
    return cls(
        client=make_mock_client(mock_response),
        model="test-model",
        response_model=response_model,
        system_prompt="sys",
    )


def test_h1_validate_state_missing_hazard():
    node = _make_node(_H1EvaluatorNode, H1_YES)
    assert node._validate_state({}) is False


def test_h1_validate_state_present(sample_hazard):
    node = _make_node(_H1EvaluatorNode, H1_YES)
    assert node._validate_state({"hazard": sample_hazard}) is True


def test_h1_build_payload_only_h1_fields(sample_hazard):
    node = _make_node(_H1EvaluatorNode, H1_YES)
    payload = node._build_payload({"hazard": sample_hazard})
    assert payload["hazard_id"] == sample_hazard.hazard_id
    assert "hazard" in payload and "harm" in payload
    # H1 should not leak post-mitigation fields
    assert "final_risk_rating" not in payload
    assert "residual_risk_acceptability" not in payload


async def test_h1_call_yes(sample_hazard):
    node = _make_node(_H1EvaluatorNode, H1_YES)
    result = await node({"hazard": sample_hazard})
    f = result["h1_finding"]
    assert isinstance(f, HazardFinding)
    assert f.code == "H1" and f.verdict == "Yes"


async def test_h2_call_yes(sample_hazard):
    node = _make_node(_H2EvaluatorNode, H2_YES)
    result = await node({"hazard": sample_hazard})
    f = result["h2_finding"]
    assert f.code == "H2" and f.verdict == "Yes"


def test_h3_validate_requires_reviews(sample_hazard):
    node = _make_node(_H3EvaluatorNode, H3_YES)
    assert node._validate_state({"hazard": sample_hazard}) is False
    assert node._validate_state({"hazard": sample_hazard, "requirement_reviews": []}) is True


def test_h3_payload_summarises_reviews(sample_hazard):
    node = _make_node(_H3EvaluatorNode, H3_YES)
    review = _good_review(sample_hazard.requirements[0])
    payload = node._build_payload({"hazard": sample_hazard, "requirement_reviews": [review]})
    assert payload["hazard_id"] == sample_hazard.hazard_id
    assert "hazardous_sequence_of_events" in payload
    assert isinstance(payload["requirement_reviews"], list)
    assert len(payload["requirement_reviews"]) == 1
    summary = payload["requirement_reviews"][0]
    # Summary should expose only the M1-M5 cells, not the full RTM artefacts.
    assert summary["requirement"]["req_id"] == "REQ-PUMP-101"
    assert summary["synthesized_assessment"]["overall_verdict"] == "Yes"
    assert [f["code"] for f in summary["synthesized_assessment"]["mandatory_findings"]] == [
        "M1", "M2", "M3", "M4", "M5",
    ]


async def test_h3_call_no_with_unblocked_items(sample_hazard):
    node = _make_node(_H3EvaluatorNode, H3_NO)
    review = _good_review(sample_hazard.requirements[0])
    result = await node({"hazard": sample_hazard, "requirement_reviews": [review]})
    f = result["h3_finding"]
    assert f.verdict == "No"
    assert f.unblocked_items == ["Scheduler stall under heavy task load"]


async def test_h4_call_na_when_no_software_causes(sample_hazard):
    node = _make_node(_H4EvaluatorNode, H4_NA)
    review = _good_review(sample_hazard.requirements[0])
    result = await node({"hazard": sample_hazard, "requirement_reviews": [review]})
    assert result["h4_finding"].verdict == "N-A"


async def test_h5_payload_carries_upstream_findings(sample_hazard):
    node = _make_node(_H5EvaluatorNode, H5_YES)
    h1 = HazardFinding.model_validate_json(H1_YES)
    h2 = HazardFinding.model_validate_json(H2_YES)
    h3 = HazardFinding.model_validate_json(H3_YES)
    h4 = HazardFinding.model_validate_json(H4_YES)
    payload = node._build_payload({
        "hazard": sample_hazard,
        "h1_finding": h1, "h2_finding": h2, "h3_finding": h3, "h4_finding": h4,
    })
    assert payload["h1_finding"]["verdict"] == "Yes"
    assert payload["h4_finding"]["cited_test_case_ids"] == ["TC-PUMP-202", "TC-PUMP-203"]
    # Post-mitigation fields must be in the payload so H5 can grade closure.
    assert "final_risk_rating" in payload
    assert "residual_risk_acceptability" in payload


# --- final assessor (deterministic verdict) -----------------------------


def _final_node(prose_response: str = FINAL_PROSE_EMPTY) -> _FinalAssessorNode:
    return _FinalAssessorNode(
        client=make_mock_client(prose_response),
        model="test-model",
        response_model=FinalAssessorProse,
        system_prompt="sys",
    )


def _all_yes_findings() -> dict:
    return {
        "h1_finding": HazardFinding.model_validate_json(H1_YES),
        "h2_finding": HazardFinding.model_validate_json(H2_YES),
        "h3_finding": HazardFinding.model_validate_json(H3_YES),
        "h4_finding": HazardFinding.model_validate_json(H4_YES),
        "h5_finding": HazardFinding.model_validate_json(H5_YES),
    }


def _mixed_findings_with_no() -> dict:
    return {
        "h1_finding": HazardFinding.model_validate_json(H1_YES),
        "h2_finding": HazardFinding.model_validate_json(H2_YES),
        "h3_finding": HazardFinding.model_validate_json(H3_NO),
        "h4_finding": HazardFinding.model_validate_json(H4_NO),
        "h5_finding": HazardFinding.model_validate_json(H5_NO),
    }


def test_final_validate_requires_all_five_findings(sample_hazard):
    node = _final_node()
    bad = {"hazard": sample_hazard, **_all_yes_findings()}
    bad.pop("h3_finding")
    assert node._validate_state(bad) is False
    assert node._validate_state({"hazard": sample_hazard, **_all_yes_findings()}) is True


def test_aggregate_verdict_pure_function():
    findings_yes = list(_all_yes_findings().values())
    findings_with_na = [
        HazardFinding.model_validate_json(H1_YES),
        HazardFinding.model_validate_json(H2_YES),
        HazardFinding.model_validate_json(H3_YES),
        HazardFinding.model_validate_json(H4_NA),
        HazardFinding.model_validate_json(H5_YES),
    ]
    findings_with_no = list(_mixed_findings_with_no().values())
    assert _FinalAssessorNode._aggregate_verdict(findings_yes) == "Yes"
    assert _FinalAssessorNode._aggregate_verdict(findings_with_na) == "Yes"
    assert _FinalAssessorNode._aggregate_verdict(findings_with_no) == "No"


async def test_final_call_all_yes_produces_yes_overall(sample_hazard):
    node = _final_node(FINAL_PROSE_EMPTY)
    state = {"hazard": sample_hazard, **_all_yes_findings()}
    result = await node(state)
    assessment = result["hazard_assessment"]
    assert isinstance(assessment, HazardAssessment)
    assert assessment.hazard_id == sample_hazard.hazard_id
    assert assessment.overall_verdict == "Yes"
    assert [f.code for f in assessment.mandatory_findings] == ["H1", "H2", "H3", "H4", "H5"]
    # The LLM-written prose comes through verbatim.
    assert assessment.comments == ""
    assert assessment.clarification_questions == []


async def test_final_call_any_no_produces_no_overall(sample_hazard):
    node = _final_node(FINAL_PROSE_INADEQUATE)
    state = {"hazard": sample_hazard, **_mixed_findings_with_no()}
    result = await node(state)
    assessment = result["hazard_assessment"]
    assert assessment.overall_verdict == "No"
    h3 = next(f for f in assessment.mandatory_findings if f.code == "H3")
    assert h3.verdict == "No"
    assert h3.unblocked_items == ["Scheduler stall under heavy task load"]
    assert "Watchdog" in assessment.comments


async def test_final_call_skip_when_findings_missing(sample_hazard):
    node = _final_node()
    incomplete = {"hazard": sample_hazard, **_all_yes_findings()}
    incomplete.pop("h2_finding")
    result = await node(incomplete)
    assert result == {"hazard_assessment": None}


async def test_final_call_invalid_prose_still_aggregates(sample_hazard):
    """When the LLM emits unparseable JSON, the deterministic verdict still
    holds and the prose falls back to empty."""
    node = _final_node("not json at all")
    state = {"hazard": sample_hazard, **_all_yes_findings()}
    result = await node(state)
    assessment = result["hazard_assessment"]
    assert isinstance(assessment, HazardAssessment)
    assert assessment.overall_verdict == "Yes"
    assert assessment.comments == ""


# --- dispatch_requirement_reviews ---------------------------------------


def test_dispatch_no_hazard():
    sends = dispatch_requirement_reviews({})
    assert sends == []


def test_dispatch_per_requirement(sample_hazard):
    sends = dispatch_requirement_reviews({"hazard": sample_hazard})
    assert len(sends) == len(sample_hazard.requirements)
    for send, req in zip(sends, sample_hazard.requirements):
        assert send.node == "requirement_reviewer"
        assert send.arg["hazard"] is sample_hazard
        assert send.arg["requirement"] is req


# --- RequirementReviewerNode --------------------------------------------


def _fake_rtm_runnable(rtm_final_state: dict) -> MagicMock:
    rtm = MagicMock()
    rtm.graph = MagicMock()
    rtm.graph.ainvoke = AsyncMock(return_value=rtm_final_state)
    return rtm


async def test_req_reviewer_happy_path(sample_hazard):
    requirement = sample_hazard.requirements[0]
    rtm_assessment = SynthesizedAssessment(
        requirement=requirement,
        overall_verdict="Yes",
        mandatory_findings=_make_mandatory_findings_yes(),
        comments="",
        clarification_questions=[],
    )
    rtm = _fake_rtm_runnable({
        "synthesized_assessment": rtm_assessment,
        "decomposed_requirement": None,
        "test_suite": None,
        "coverage_analysis": [],
    })
    node = RequirementReviewerNode(rtm)

    result = await node({"hazard": sample_hazard, "requirement": requirement})
    reviews = result["requirement_reviews"]
    assert len(reviews) == 1
    assert reviews[0].requirement.req_id == requirement.req_id
    assert reviews[0].synthesized_assessment.overall_verdict == "Yes"

    rtm.graph.ainvoke.assert_awaited_once()
    rtm_input = rtm.graph.ainvoke.await_args.args[0]
    assert rtm_input["requirement"] is requirement
    assert rtm_input["test_cases"] == sample_hazard.test_cases


async def test_req_reviewer_subgraph_failure_returns_empty_review(sample_hazard):
    requirement = sample_hazard.requirements[0]
    rtm = MagicMock()
    rtm.graph = MagicMock()
    rtm.graph.ainvoke = AsyncMock(side_effect=RuntimeError("simulated subgraph failure"))
    node = RequirementReviewerNode(rtm)

    result = await node({"hazard": sample_hazard, "requirement": requirement})
    reviews = result["requirement_reviews"]
    assert len(reviews) == 1
    assert reviews[0].requirement.req_id == requirement.req_id
    assert reviews[0].synthesized_assessment is None


async def test_req_reviewer_skips_when_payload_incomplete():
    rtm = _fake_rtm_runnable({})
    node = RequirementReviewerNode(rtm)
    result = await node({"hazard": None, "requirement": None})
    assert result == {"requirement_reviews": []}
    rtm.graph.ainvoke.assert_not_awaited()
