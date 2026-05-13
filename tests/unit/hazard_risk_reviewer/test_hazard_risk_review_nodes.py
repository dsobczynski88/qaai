"""
Unit tests for the hazard risk reviewer pipeline.

Each unit test exercises one node or dispatcher in isolation with a mocked
LLM client (per-dimension Hn evaluators + final assessor) or a mocked
RTMReviewerRunnable (RequirementReviewerNode), mirroring the patterns in
tests/unit/test_summary_node.py and test_decomposer_node.py.

Verdict convention: each H1-H7 finding is binary Yes/No (H5 may also be
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
    HazardEvaluatorNode,
    H6EvaluatorNode,
    dispatch_requirement_reviews,
    dispatch_hazard_evaluators_early,
    dispatch_hazard_evaluators_late,
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


H1_YES = json.dumps(_hf("H1", "Hazard Record Completeness and Semantic Integrity", "Yes",
                        rationale="Chain is consistent."))
H1_NO = json.dumps(_hf("H1", "Hazard Record Completeness and Semantic Integrity", "No",
                       rationale="Hazardous situation is empty."))
H2_YES = json.dumps(_hf("H2", "Software Contribution and Cause Coverage", "Yes",
                        rationale="Software causes are well-defined."))
H3_YES = json.dumps(_hf("H3", "Pre-Mitigation Risk and Exploitability Characterization", "Yes",
                        rationale="Pre-mitigation risk is properly characterized."))
H3_NO = json.dumps(_hf("H3", "Pre-Mitigation Risk and Exploitability Characterization", "No",
                       rationale="Pre-mitigation risk characterization is incomplete.",
                       unblocked_items=["Exploitability rating missing"]))
H4_YES = json.dumps(_hf("H4", "Risk Control Identification, Allocation, and Coverage", "Yes",
                        rationale="All software causes are controlled by REQ-PUMP-101.",
                        cited_req_ids=["REQ-PUMP-101"]))
H4_NO = json.dumps(_hf("H4", "Risk Control Identification, Allocation, and Coverage", "No",
                       rationale="No controlling requirement for the scheduler-stall cause.",
                       unblocked_items=["Scheduler stall under heavy task load"]))
H5_YES = json.dumps(_hf("H5", "Verification Depth and Hazard-Path Effectiveness", "Yes",
                        rationale="Fault injection and boundary tests verify the controls.",
                        cited_req_ids=["REQ-PUMP-101"],
                        cited_test_case_ids=["TC-PUMP-202", "TC-PUMP-203"]))
H5_NO = json.dumps(_hf("H5", "Verification Depth and Hazard-Path Effectiveness", "No",
                       rationale="Only the functional happy path is exercised.",
                       cited_test_case_ids=["TC-PUMP-201"],
                       unblocked_items=["REQ-PUMP-101 watchdog latch behavior"]))
H5_NA = json.dumps(_hf("H5", "Verification Depth and Hazard-Path Effectiveness", "N-A",
                       rationale="No software-related causes — software verification depth is not applicable."))
H6_YES = json.dumps(_hf("H6", "Residual Risk Closure and Acceptability Decision", "Yes",
                        rationale="Probability downgrade Probable to Remote is supported by verified controls."))
H6_NO = json.dumps(_hf("H6", "Residual Risk Closure and Acceptability Decision", "No",
                       rationale="Probability downgrade is unsupported by software verification evidence."))
H7_YES = json.dumps(_hf("H7", "HSHA Update and Newly Identified Hazard / Hazardous Situation Capture", "Yes",
                        rationale="HSHA is properly updated with new hazards."))
H7_NO = json.dumps(_hf("H7", "HSHA Update and Newly Identified Hazard / Hazardous Situation Capture", "No",
                       rationale="New hazards not captured in HSHA."))

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


def _make_h_node(dimension_code: str, required_fields: tuple, mock_response: str) -> HazardEvaluatorNode:
    """Factory for HazardEvaluatorNode with mocked LLM."""
    return HazardEvaluatorNode(
        client=make_mock_client(mock_response),
        model="test-model",
        system_prompt="sys",
        model_kwargs={},
        dimension_code=dimension_code,
        required_fields=required_fields,
    )


def _make_h6_node(mock_response: str) -> H6EvaluatorNode:
    """Factory for H6EvaluatorNode with mocked LLM."""
    return H6EvaluatorNode(
        client=make_mock_client(mock_response),
        model="test-model",
        response_model=HazardFinding,
        system_prompt="sys",
        model_kwargs={},
    )


def test_h1_validate_state_missing_hazard():
    node = _make_h_node("H1", ("hazard_id", "hazard"), H1_YES)
    assert node._validate_state({}) is False


def test_h1_validate_state_present(sample_hazard):
    node = _make_h_node("H1", ("hazard_id", "hazard"), H1_YES)
    assert node._validate_state({"hazard": sample_hazard}) is True


def test_h1_build_payload_only_h1_fields(sample_hazard):
    node = _make_h_node("H1", ("hazard_id", "hazard", "harm"), H1_YES)
    payload = node._build_payload({"hazard": sample_hazard})
    assert payload["hazard_id"] == sample_hazard.hazard_id
    assert "hazard" in payload and "harm" in payload
    # H1 should not leak post-mitigation fields
    assert "final_risk_rating" not in payload
    assert "residual_risk_acceptability" not in payload


async def test_h1_call_yes(sample_hazard):
    node = _make_h_node("H1", ("hazard_id", "hazard"), H1_YES)
    result = await node({"hazard": sample_hazard})
    findings = result["hazard_findings"]
    assert len(findings) == 1
    f = findings[0]
    assert isinstance(f, HazardFinding)
    assert f.code == "H1" and f.verdict == "Yes"


async def test_h2_call_yes(sample_hazard):
    node = _make_h_node("H2", ("hazard_id", "hazard"), H2_YES)
    result = await node({"hazard": sample_hazard})
    findings = result["hazard_findings"]
    assert len(findings) == 1
    f = findings[0]
    assert f.code == "H2" and f.verdict == "Yes"


def test_h3_validate_only_needs_hazard(sample_hazard):
    """H3 is an early evaluator — only needs hazard, not requirement_reviews."""
    node = _make_h_node("H3", ("hazard_id",), H3_YES)
    assert node._validate_state({"hazard": sample_hazard}) is True


async def test_h3_call_no_with_unblocked_items(sample_hazard):
    node = _make_h_node("H3", ("hazard_id",), H3_NO)
    result = await node({"hazard": sample_hazard})
    findings = result["hazard_findings"]
    assert len(findings) == 1
    f = findings[0]
    assert f.verdict == "No"
    assert f.unblocked_items == ["Exploitability rating missing"]


def test_h4_validate_requires_reviews(sample_hazard):
    """H4 is a late evaluator — needs hazard + requirement_reviews."""
    node = _make_h_node("H4", ("hazard_id",), H4_YES)
    assert node._validate_state({"hazard": sample_hazard}) is False
    assert node._validate_state({"hazard": sample_hazard, "requirement_reviews": []}) is True


def test_h4_payload_summarises_reviews(sample_hazard):
    node = _make_h_node("H4", ("hazard_id",), H4_YES)
    review = _good_review(sample_hazard.requirements[0])
    payload = node._build_payload({"hazard": sample_hazard, "requirement_reviews": [review]})
    assert payload["hazard_id"] == sample_hazard.hazard_id
    assert isinstance(payload["requirement_reviews"], list)
    assert len(payload["requirement_reviews"]) == 1
    summary = payload["requirement_reviews"][0]
    # Summary should expose only the M1-M5 cells, not the full RTM artefacts.
    assert summary["requirement"]["req_id"] == "REQ-PUMP-101"
    assert summary["synthesized_assessment"]["overall_verdict"] == "Yes"
    assert [f["code"] for f in summary["synthesized_assessment"]["mandatory_findings"]] == [
        "M1", "M2", "M3", "M4", "M5",
    ]


async def test_h5_call_na_when_no_software_causes(sample_hazard):
    node = _make_h_node("H5", ("hazard_id",), H5_NA)
    review = _good_review(sample_hazard.requirements[0])
    result = await node({"hazard": sample_hazard, "requirement_reviews": [review]})
    findings = result["hazard_findings"]
    assert len(findings) == 1
    assert findings[0].verdict == "N-A"


def test_h6_validate_requires_h3_h4_h5_findings(sample_hazard):
    """H6 needs H3, H4, H5 findings to validate residual risk."""
    node = _make_h6_node(H6_YES)
    h3 = HazardFinding.model_validate_json(H3_YES)
    h4 = HazardFinding.model_validate_json(H4_YES)
    h5 = HazardFinding.model_validate_json(H5_YES)
    
    # Missing findings
    assert node._validate_state({"hazard": sample_hazard, "hazard_findings": []}) is False
    # All three present
    assert node._validate_state({
        "hazard": sample_hazard,
        "hazard_findings": [h3, h4, h5],
    }) is True


async def test_h6_payload_carries_upstream_findings(sample_hazard):
    node = _make_h6_node(H6_YES)
    h3 = HazardFinding.model_validate_json(H3_YES)
    h4 = HazardFinding.model_validate_json(H4_YES)
    h5 = HazardFinding.model_validate_json(H5_YES)
    payload = node._build_payload({
        "hazard": sample_hazard,
        "hazard_findings": [h3, h4, h5],
    })
    assert payload["h3_finding"]["verdict"] == "Yes"
    assert payload["h4_finding"]["verdict"] == "Yes"
    assert payload["h5_finding"]["verdict"] == "Yes"
    # Post-mitigation fields must be in the payload so H6 can grade closure.
    assert "final_risk_rating" in payload
    assert "residual_risk_acceptability" in payload


async def test_h7_call_yes(sample_hazard):
    node = _make_h_node("H7", ("hazard_id", "new_hs_reference"), H7_YES)
    result = await node({"hazard": sample_hazard})
    findings = result["hazard_findings"]
    assert len(findings) == 1
    f = findings[0]
    assert f.code == "H7" and f.verdict == "Yes"


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
        "hazard_findings": [
            HazardFinding.model_validate_json(H1_YES),
            HazardFinding.model_validate_json(H2_YES),
            HazardFinding.model_validate_json(H3_YES),
            HazardFinding.model_validate_json(H4_YES),
            HazardFinding.model_validate_json(H5_YES),
            HazardFinding.model_validate_json(H6_YES),
            HazardFinding.model_validate_json(H7_YES),
        ]
    }


def _mixed_findings_with_no() -> dict:
    return {
        "hazard_findings": [
            HazardFinding.model_validate_json(H1_YES),
            HazardFinding.model_validate_json(H2_YES),
            HazardFinding.model_validate_json(H3_NO),
            HazardFinding.model_validate_json(H4_NO),
            HazardFinding.model_validate_json(H5_NO),
            HazardFinding.model_validate_json(H6_NO),
            HazardFinding.model_validate_json(H7_YES),
        ]
    }


def test_final_validate_requires_all_seven_findings(sample_hazard):
    node = _final_node()
    # Missing one finding
    incomplete = {"hazard": sample_hazard, **_all_yes_findings()}
    incomplete["hazard_findings"] = incomplete["hazard_findings"][:6]  # Only 6 findings
    assert node._validate_state(incomplete) is False
    # All 7 present
    assert node._validate_state({"hazard": sample_hazard, **_all_yes_findings()}) is True


def test_aggregate_verdict_pure_function():
    findings_yes = _all_yes_findings()["hazard_findings"]
    findings_with_na = [
        HazardFinding.model_validate_json(H1_YES),
        HazardFinding.model_validate_json(H2_YES),
        HazardFinding.model_validate_json(H3_YES),
        HazardFinding.model_validate_json(H4_YES),
        HazardFinding.model_validate_json(H5_NA),
        HazardFinding.model_validate_json(H6_YES),
        HazardFinding.model_validate_json(H7_YES),
    ]
    findings_with_no = _mixed_findings_with_no()["hazard_findings"]
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
    assert len(assessment.mandatory_findings) == 7
    assert [f.code for f in assessment.mandatory_findings] == ["H1", "H2", "H3", "H4", "H5", "H6", "H7"]
    # The LLM-written prose comes through verbatim.
    assert assessment.comments == ""
    assert assessment.clarification_questions == []


async def test_final_call_any_no_produces_no_overall(sample_hazard):
    node = _final_node(FINAL_PROSE_INADEQUATE)
    state = {"hazard": sample_hazard, **_mixed_findings_with_no()}
    result = await node(state)
    assessment = result["hazard_assessment"]
    assert assessment.overall_verdict == "No"
    assert len(assessment.mandatory_findings) == 7
    h3 = next(f for f in assessment.mandatory_findings if f.code == "H3")
    assert h3.verdict == "No"
    assert "Watchdog" in assessment.comments


async def test_final_call_skip_when_findings_missing(sample_hazard):
    node = _final_node()
    incomplete = {"hazard": sample_hazard, **_all_yes_findings()}
    incomplete["hazard_findings"] = incomplete["hazard_findings"][:5]  # Only 5 findings
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


# --- dispatch functions -------------------------------------------------


def test_dispatch_early_evaluators(sample_hazard):
    """H1, H2, H3, H7 dispatch immediately from START."""
    sends = dispatch_hazard_evaluators_early({"hazard": sample_hazard})
    assert len(sends) == 4
    nodes = [s.node for s in sends]
    assert set(nodes) == {"h1_evaluator", "h2_evaluator", "h3_evaluator", "h7_evaluator"}
    for send in sends:
        assert send.arg["hazard"] is sample_hazard


def test_dispatch_early_no_hazard():
    sends = dispatch_hazard_evaluators_early({})
    assert sends == []


def test_dispatch_late_evaluators(sample_hazard):
    """H4, H5 dispatch after requirement_reviews complete."""
    review = _good_review(sample_hazard.requirements[0])
    sends = dispatch_hazard_evaluators_late({
        "hazard": sample_hazard,
        "requirement_reviews": [review],
    })
    assert len(sends) == 2
    nodes = [s.node for s in sends]
    assert set(nodes) == {"h4_evaluator", "h5_evaluator"}
    for send in sends:
        assert send.arg["hazard"] is sample_hazard
        assert send.arg["requirement_reviews"] == [review]


def test_dispatch_late_missing_reviews(sample_hazard):
    sends = dispatch_hazard_evaluators_late({"hazard": sample_hazard})
    assert sends == []


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
