"""
Unit tests for the synthesizer node (M1-M5 verdict logic).

The synthesizer node takes decomposed requirements, test suite, and coverage
analysis, then produces a SynthesizedAssessment with five mandatory findings
(M1-M5) and an overall verdict.

Key invariants to test:
- M1 (Functional): never N-A
- M2 (Negative): may be N-A if no validation surface
- M3 (Boundary): may be N-A if no threshold/limit
- M4 (Spec Coverage): never N-A
- M5 (Terminology): never N-A
- partial=True only allowed with verdict="Yes"
- overall_verdict="Yes" iff all findings in {Yes, N-A}
"""
import json
import pytest

from tests.helpers import make_mock_client, load_jsonl
from autoqa.components.test_suite_reviewer.nodes import make_synthesizer_node as make_synthesizer
from autoqa.components.test_suite_reviewer.core import (
    SynthesizedAssessment,
    Requirement,
    DecomposedRequirement,
    TestSuite,
    EvaluatedSpec,
)


# TODO: Create tests/fixtures/mock/synthesizer_cases.jsonl with mock responses
# For now, use inline mock responses


MOCK_ALL_YES = json.dumps({
    "requirement": {"req_id": "REQ-001", "text": "System shall alert when reading exceeds 100 mg/dL."},
    "overall_verdict": "Yes",
    "mandatory_findings": [
        {
            "code": "M1",
            "dimension": "Functional",
            "verdict": "Yes",
            "partial": False,
            "rationale": "TC-001 verifies the happy path.",
            "cited_test_case_ids": ["TC-001"],
            "uncovered_spec_ids": [],
        },
        {
            "code": "M2",
            "dimension": "Negative",
            "verdict": "Yes",
            "partial": False,
            "rationale": "TC-002 verifies error handling.",
            "cited_test_case_ids": ["TC-002"],
            "uncovered_spec_ids": [],
        },
        {
            "code": "M3",
            "dimension": "Boundary",
            "verdict": "Yes",
            "partial": False,
            "rationale": "TC-003 verifies threshold at 100 mg/dL.",
            "cited_test_case_ids": ["TC-003"],
            "uncovered_spec_ids": [],
        },
        {
            "code": "M4",
            "dimension": "Spec Coverage",
            "verdict": "Yes",
            "partial": False,
            "rationale": "All specs covered.",
            "cited_test_case_ids": [],
            "uncovered_spec_ids": [],
        },
        {
            "code": "M5",
            "dimension": "Terminology",
            "verdict": "Yes",
            "partial": False,
            "rationale": "Terminology aligned.",
            "cited_test_case_ids": [],
            "uncovered_spec_ids": [],
        },
    ],
    "comments": "",
    "clarification_questions": [],
})


MOCK_M2_NA = json.dumps({
    "requirement": {"req_id": "REQ-002", "text": "System shall display glucose reading."},
    "overall_verdict": "Yes",
    "mandatory_findings": [
        {
            "code": "M1",
            "dimension": "Functional",
            "verdict": "Yes",
            "partial": False,
            "rationale": "TC-010 verifies display.",
            "cited_test_case_ids": ["TC-010"],
            "uncovered_spec_ids": [],
        },
        {
            "code": "M2",
            "dimension": "Negative",
            "verdict": "N-A",
            "partial": False,
            "rationale": "No validation surface — display-only requirement.",
            "cited_test_case_ids": [],
            "uncovered_spec_ids": [],
        },
        {
            "code": "M3",
            "dimension": "Boundary",
            "verdict": "N-A",
            "partial": False,
            "rationale": "No threshold or limit.",
            "cited_test_case_ids": [],
            "uncovered_spec_ids": [],
        },
        {
            "code": "M4",
            "dimension": "Spec Coverage",
            "verdict": "Yes",
            "partial": False,
            "rationale": "All specs covered.",
            "cited_test_case_ids": [],
            "uncovered_spec_ids": [],
        },
        {
            "code": "M5",
            "dimension": "Terminology",
            "verdict": "Yes",
            "partial": False,
            "rationale": "Terminology aligned.",
            "cited_test_case_ids": [],
            "uncovered_spec_ids": [],
        },
    ],
    "comments": "",
    "clarification_questions": [],
})


MOCK_M1_NO = json.dumps({
    "requirement": {"req_id": "REQ-003", "text": "System shall reject invalid inputs."},
    "overall_verdict": "No",
    "mandatory_findings": [
        {
            "code": "M1",
            "dimension": "Functional",
            "verdict": "No",
            "partial": False,
            "rationale": "No test case verifies the rejection behavior.",
            "cited_test_case_ids": [],
            "uncovered_spec_ids": ["S-003-01"],
        },
        {
            "code": "M2",
            "dimension": "Negative",
            "verdict": "Yes",
            "partial": False,
            "rationale": "TC-020 verifies error message.",
            "cited_test_case_ids": ["TC-020"],
            "uncovered_spec_ids": [],
        },
        {
            "code": "M3",
            "dimension": "Boundary",
            "verdict": "N-A",
            "partial": False,
            "rationale": "No threshold.",
            "cited_test_case_ids": [],
            "uncovered_spec_ids": [],
        },
        {
            "code": "M4",
            "dimension": "Spec Coverage",
            "verdict": "No",
            "partial": False,
            "rationale": "Spec S-003-01 not covered.",
            "cited_test_case_ids": [],
            "uncovered_spec_ids": ["S-003-01"],
        },
        {
            "code": "M5",
            "dimension": "Terminology",
            "verdict": "Yes",
            "partial": False,
            "rationale": "Terminology aligned.",
            "cited_test_case_ids": [],
            "uncovered_spec_ids": [],
        },
    ],
    "comments": "Missing functional coverage for rejection behavior.",
    "clarification_questions": ["Is TC-030 planned to cover S-003-01?"],
})


MOCK_PARTIAL_YES = json.dumps({
    "requirement": {"req_id": "REQ-004", "text": "System shall validate dose range 0-200 mL/hr."},
    "overall_verdict": "Yes",
    "mandatory_findings": [
        {
            "code": "M1",
            "dimension": "Functional",
            "verdict": "Yes",
            "partial": True,
            "rationale": "TC-040 verifies nominal dose, but edge cases at 0 and 200 are implicit.",
            "cited_test_case_ids": ["TC-040"],
            "uncovered_spec_ids": [],
        },
        {
            "code": "M2",
            "dimension": "Negative",
            "verdict": "Yes",
            "partial": False,
            "rationale": "TC-041 verifies rejection of -1 and 201.",
            "cited_test_case_ids": ["TC-041"],
            "uncovered_spec_ids": [],
        },
        {
            "code": "M3",
            "dimension": "Boundary",
            "verdict": "Yes",
            "partial": True,
            "rationale": "TC-042 tests 200 mL/hr but not 0 mL/hr.",
            "cited_test_case_ids": ["TC-042"],
            "uncovered_spec_ids": [],
        },
        {
            "code": "M4",
            "dimension": "Spec Coverage",
            "verdict": "Yes",
            "partial": False,
            "rationale": "All specs covered.",
            "cited_test_case_ids": [],
            "uncovered_spec_ids": [],
        },
        {
            "code": "M5",
            "dimension": "Terminology",
            "verdict": "Yes",
            "partial": False,
            "rationale": "Terminology aligned.",
            "cited_test_case_ids": [],
            "uncovered_spec_ids": [],
        },
    ],
    "comments": "Partial coverage on M1 and M3 — edge cases could be more explicit.",
    "clarification_questions": [],
})


@pytest.fixture
def sample_state(sample_requirement, sample_decomposed_requirement, sample_test_suite):
    """Minimal state for synthesizer node."""
    return {
        "requirement": sample_requirement,
        "decomposed_requirement": sample_decomposed_requirement,
        "test_suite": sample_test_suite,
        "coverage_analysis": [
            EvaluatedSpec(
                spec_id="S-001",
                covered_exists=True,
                covered_by_test_cases=[],
            )
        ],
    }


def _make_synthesizer(mock_response: str):
    """Factory for synthesizer node with mocked LLM."""
    return make_synthesizer(
        client=make_mock_client(mock_response),
        model="test-model",
    )


async def test_synthesizer_all_yes(sample_state):
    """All findings Yes → overall_verdict Yes."""
    node = _make_synthesizer(MOCK_ALL_YES)
    result = await node(sample_state)
    
    assert "synthesized_assessment" in result
    sa = result["synthesized_assessment"]
    assert isinstance(sa, SynthesizedAssessment)
    assert sa.overall_verdict == "Yes"
    assert len(sa.mandatory_findings) == 5
    assert [f.code for f in sa.mandatory_findings] == ["M1", "M2", "M3", "M4", "M5"]
    assert all(f.verdict == "Yes" for f in sa.mandatory_findings)
    assert all(f.partial is False for f in sa.mandatory_findings)


async def test_synthesizer_m2_na_still_yes(sample_state):
    """M2=N-A (no validation surface) → overall_verdict still Yes."""
    node = _make_synthesizer(MOCK_M2_NA)
    result = await node(sample_state)
    
    sa = result["synthesized_assessment"]
    assert sa.overall_verdict == "Yes"
    m2 = next(f for f in sa.mandatory_findings if f.code == "M2")
    assert m2.verdict == "N-A"
    m3 = next(f for f in sa.mandatory_findings if f.code == "M3")
    assert m3.verdict == "N-A"


async def test_synthesizer_m1_no_drives_overall_no(sample_state):
    """M1=No → overall_verdict No."""
    node = _make_synthesizer(MOCK_M1_NO)
    result = await node(sample_state)
    
    sa = result["synthesized_assessment"]
    assert sa.overall_verdict == "No"
    m1 = next(f for f in sa.mandatory_findings if f.code == "M1")
    assert m1.verdict == "No"
    assert m1.partial is False  # partial never allowed with No
    assert "S-003-01" in m1.uncovered_spec_ids


async def test_synthesizer_partial_yes_allowed(sample_state):
    """partial=True only with verdict=Yes."""
    node = _make_synthesizer(MOCK_PARTIAL_YES)
    result = await node(sample_state)
    
    sa = result["synthesized_assessment"]
    assert sa.overall_verdict == "Yes"
    m1 = next(f for f in sa.mandatory_findings if f.code == "M1")
    assert m1.verdict == "Yes"
    assert m1.partial is True
    m3 = next(f for f in sa.mandatory_findings if f.code == "M3")
    assert m3.verdict == "Yes"
    assert m3.partial is True


async def test_synthesizer_skip_on_missing_state():
    """Missing required state → return None."""
    node = _make_synthesizer(MOCK_ALL_YES)
    result = await node({})
    assert result == {"synthesized_assessment": None}


async def test_synthesizer_invalid_json_returns_none(sample_state):
    """Unparseable LLM response → return None."""
    node = _make_synthesizer("not json at all")
    result = await node(sample_state)
    assert result == {"synthesized_assessment": None}


# TODO: Once synthesizer_cases.jsonl is created, add parametrized test:
# @pytest.mark.parametrize("case", load_jsonl("synthesizer_cases.jsonl"), ids=lambda c: c["id"])
# async def test_synthesizer_parametrized(case):
#     ...
