"""
Unit smoke tests for the test_case_reviewer component nodes.

Exercises each node's __call__ with a mocked LLM response and asserts the
shape of the state-update dict it returns. These tests do NOT exercise the
LangGraph wiring — see tests/integration/ for end-to-end coverage.
"""
import json
import pytest

from tests.helpers import make_mock_client

from autoqa.components.test_case_reviewer import (
    AggregatorNode,
    DecomposedRequirement,
    DecomposedSpec,
    OverallAnalysis,
    OverallLogicalNode,
    OverallPrereqsNode,
    Requirement,
    ReviewObjective,
    SingleSpecCoverageNode,
    SpecAnalysis,
    TCDecomposerNode,
    TestCase,
    TestCaseAssessment,
    dispatch_coverage,
    load_default_review_objectives,
)
from autoqa.components.shared.nodes import DecomposerNode


# ---------------------------------------------------------------------------
# Fixtures local to this module (avoid coupling to test_suite_reviewer
# conftest fixtures, which use the suite-level shapes).
# ---------------------------------------------------------------------------


@pytest.fixture
def tc_requirement():
    return Requirement(req_id="REQ-001", text="System shall alert when reading exceeds 100 mg/dL.")


@pytest.fixture
def tc_test_case():
    return TestCase(
        test_id="TC-001",
        description="Alert fires above threshold",
        setup="Sensor connected, calibrated; user logged in.",
        steps="1. Set reading=105. 2. Observe UI.",
        expectedResults="Alert displayed within 1s.",
    )


@pytest.fixture
def tc_decomposed_spec():
    return DecomposedSpec(
        spec_id="S-001",
        description="Alert fires when reading > 100 mg/dL",
        acceptance_criteria="Alert visible within 1s of breach",
        rationale="Happy path",
    )


@pytest.fixture
def tc_decomposed_requirement(tc_requirement, tc_decomposed_spec):
    return DecomposedRequirement(
        requirement=tc_requirement,
        decomposed_specifications=[tc_decomposed_spec],
    )


# ---------------------------------------------------------------------------
# Decomposer (sequential loop)
# ---------------------------------------------------------------------------


_DECOMP_RESPONSE = json.dumps({
    "requirement": {"req_id": "REQ-001", "text": "System shall alert when reading exceeds 100 mg/dL."},
    "decomposed_specifications": [
        {
            "spec_id": "S-001",
            "description": "Alert fires when reading > 100 mg/dL",
            "acceptance_criteria": "Alert visible within 1s",
            "rationale": "Happy path",
        }
    ],
})


def _make_tc_decomposer(response: str) -> TCDecomposerNode:
    inner = DecomposerNode(
        client=make_mock_client(response),
        model="test-model",
        response_model=DecomposedRequirement,
        system_prompt="sys",
    )
    return TCDecomposerNode(inner=inner)


async def test_tc_decomposer_validate_state_empty():
    node = _make_tc_decomposer(_DECOMP_RESPONSE)
    assert node._validate_state({}) is False
    assert node._validate_state({"requirements": []}) is False


async def test_tc_decomposer_loops_over_requirements(tc_requirement):
    node = _make_tc_decomposer(_DECOMP_RESPONSE)
    state = {"requirements": [tc_requirement, tc_requirement]}
    result = await node(state)
    assert "decomposed_requirements" in result
    assert isinstance(result["decomposed_requirements"], list)
    assert len(result["decomposed_requirements"]) == 2
    assert all(isinstance(d, DecomposedRequirement) for d in result["decomposed_requirements"])


async def test_tc_decomposer_skip_no_requirements():
    node = _make_tc_decomposer(_DECOMP_RESPONSE)
    result = await node({})
    assert result == {"decomposed_requirements": None}


async def test_tc_decomposer_inner_failure_returns_none(tc_requirement):
    node = _make_tc_decomposer("not json at all")
    result = await node({"requirements": [tc_requirement]})
    # All inner calls fail → empty list → return None
    assert result == {"decomposed_requirements": None}


# ---------------------------------------------------------------------------
# Per-axis single-spec evaluators
# ---------------------------------------------------------------------------


_SPEC_ANALYSIS_RESPONSE = json.dumps({
    "spec_id": "S-001",
    "exists": True,
    "assessment": "Test case verifies the threshold-exceeded happy path; minor ambiguity around alert latency wording.",
})


async def test_coverage_node_returns_specanalysis(tc_test_case, tc_requirement, tc_decomposed_spec):
    """Coverage axis remains per-spec (Send fan-out target). One SpecAnalysis per call."""
    node = SingleSpecCoverageNode(
        client=make_mock_client(_SPEC_ANALYSIS_RESPONSE),
        model="test-model",
        system_prompt="sys",
    )
    state = {
        "test_case": tc_test_case,
        "requirement": tc_requirement,
        "decomposed_spec": tc_decomposed_spec,
    }
    result = await node(state)
    assert "coverage_analysis" in result
    assert len(result["coverage_analysis"]) == 1
    assert isinstance(result["coverage_analysis"][0], SpecAnalysis)
    assert result["coverage_analysis"][0].spec_id == "S-001"


async def test_coverage_node_skip_on_missing_state():
    node = SingleSpecCoverageNode(
        client=make_mock_client(_SPEC_ANALYSIS_RESPONSE),
        model="test-model",
        system_prompt="sys",
    )
    result = await node({})
    assert result == {"coverage_analysis": []}


async def test_coverage_node_invalid_json_returns_empty(tc_test_case, tc_requirement, tc_decomposed_spec):
    node = SingleSpecCoverageNode(
        client=make_mock_client("not json"),
        model="test-model",
        system_prompt="sys",
    )
    state = {
        "test_case": tc_test_case,
        "requirement": tc_requirement,
        "decomposed_spec": tc_decomposed_spec,
    }
    result = await node(state)
    assert result == {"coverage_analysis": []}


# ---------------------------------------------------------------------------
# Test-case-level axis evaluators (single LLM call each, no Send fan-out).
# Logical-structure and prereqs are properties of the test case as a whole
# from v3 onwards; they take the full TCReviewState and return a scalar
# OverallAnalysis (not List[SpecAnalysis]).
# ---------------------------------------------------------------------------


_OVERALL_ANALYSIS_RESPONSE = json.dumps({
    "exists": True,
    "assessment": "Steps 1-2 establish the patient/dose preconditions, Step 3 sends the prescription, Step 4 inspects the modal — clean precondition → stimulus → verification.",
})


@pytest.mark.parametrize(
    "node_cls,output_key",
    [
        (OverallLogicalNode, "logical_structure_analysis"),
        (OverallPrereqsNode, "prereqs_analysis"),
    ],
)
async def test_overall_axis_node_returns_overallanalysis(node_cls, output_key, tc_test_case, tc_requirement):
    """OverallLogicalNode / OverallPrereqsNode take the full TCReviewState and
    return a scalar OverallAnalysis (no spec_id field). Single LLM call per node,
    no Send fan-out."""
    node = node_cls(
        client=make_mock_client(_OVERALL_ANALYSIS_RESPONSE),
        model="test-model",
        response_model=OverallAnalysis,
        system_prompt="sys",
    )
    state = {
        "test_case": tc_test_case,
        "requirements": [tc_requirement],
    }
    result = await node(state)
    assert output_key in result
    assert isinstance(result[output_key], OverallAnalysis)
    assert result[output_key].exists is True
    assert "Step" in result[output_key].assessment


@pytest.mark.parametrize(
    "node_cls,output_key",
    [
        (OverallLogicalNode, "logical_structure_analysis"),
        (OverallPrereqsNode, "prereqs_analysis"),
    ],
)
async def test_overall_axis_node_skip_on_missing_state(node_cls, output_key):
    node = node_cls(
        client=make_mock_client(_OVERALL_ANALYSIS_RESPONSE),
        model="test-model",
        response_model=OverallAnalysis,
        system_prompt="sys",
    )
    result = await node({})
    assert result == {output_key: None}


# ---------------------------------------------------------------------------
# Dispatcher (only coverage fans out per spec from v3 onwards)
# ---------------------------------------------------------------------------


def test_dispatch_coverage_emits_one_send_per_spec(tc_test_case, tc_decomposed_requirement, tc_requirement, tc_decomposed_spec):
    second_dr = DecomposedRequirement(
        requirement=Requirement(req_id="REQ-002", text="Second req."),
        decomposed_specifications=[
            DecomposedSpec(spec_id="S-002", description="d", acceptance_criteria="ac", rationale="r"),
            DecomposedSpec(spec_id="S-003", description="d", acceptance_criteria="ac", rationale="r"),
        ],
    )
    state = {
        "test_case": tc_test_case,
        "decomposed_requirements": [tc_decomposed_requirement, second_dr],
    }
    sends = dispatch_coverage(state)
    assert len(sends) == 3  # 1 + 2
    for s in sends:
        assert s.node == "coverage_evaluator"
        assert "test_case" in s.arg
        assert "requirement" in s.arg
        assert "decomposed_spec" in s.arg
    # First Send carries REQ-001 with S-001
    assert sends[0].arg["requirement"].req_id == "REQ-001"
    assert sends[0].arg["decomposed_spec"].spec_id == "S-001"
    # Third Send carries REQ-002 with S-003
    assert sends[2].arg["requirement"].req_id == "REQ-002"
    assert sends[2].arg["decomposed_spec"].spec_id == "S-003"


def test_dispatch_coverage_empty_state():
    assert dispatch_coverage({}) == []
    assert dispatch_coverage({"test_case": None, "decomposed_requirements": []}) == []


# ---------------------------------------------------------------------------
# Aggregator
# ---------------------------------------------------------------------------


def _aggregator_response(test_case, requirement, decomposed_requirement, objectives):
    # Emit a representative mix: most Yes, one Yes-with-partial, one No so the
    # response exercises the full verdict surface. Indices 0/1 are Yes,
    # index 2 is Yes+partial, index 3 is No, index 4 is Yes.
    rows = []
    for i, o in enumerate(objectives):
        if i == 2:
            rows.append({
                "id": o.id, "description": o.description,
                "verdict": "Yes", "partial": True,
                "assessment": f"Partial-Yes for {o.id}: spec S-001 met but with residual ambiguity.",
            })
        elif i == 3:
            rows.append({
                "id": o.id, "description": o.description,
                "verdict": "No", "partial": False,
                "assessment": f"No for {o.id}: spec S-001 not adequately addressed.",
            })
        else:
            rows.append({
                "id": o.id, "description": o.description,
                "verdict": "Yes", "partial": False,
                "assessment": f"Yes for {o.id}: spec S-001 fully addressed.",
            })
    return json.dumps({
        "test_case": test_case.model_dump(),
        "requirements": [requirement.model_dump()],
        "decomposed_requirements": [decomposed_requirement.model_dump()],
        "evaluated_checklist": rows,
        "overall_verdict": "No",
        "comments": "Index-3 objective drives a No.",
        "clarification_questions": ["Is the spec missing or simply not yet documented?"],
    })


async def test_aggregator_returns_assessment(tc_test_case, tc_requirement, tc_decomposed_requirement):
    objectives = load_default_review_objectives()
    response = _aggregator_response(tc_test_case, tc_requirement, tc_decomposed_requirement, objectives)
    node = AggregatorNode(
        client=make_mock_client(response),
        model="test-model",
        response_model=TestCaseAssessment,
        system_prompt="sys",
    )
    state = {
        "test_case": tc_test_case,
        "requirements": [tc_requirement],
        "decomposed_requirements": [tc_decomposed_requirement],
        "review_objectives": objectives,
        "coverage_analysis": [SpecAnalysis(spec_id="S-001", exists=True, assessment="ok")],
        "logical_structure_analysis": OverallAnalysis(exists=True, assessment="Steps 1-2 set up, Step 3 stimulates, Step 4 verifies — clean flow."),
        "prereqs_analysis": OverallAnalysis(exists=True, assessment="Setup names sensor, user role, and calibration state."),
    }
    result = await node(state)
    assert "aggregated_assessment" in result
    assessment = result["aggregated_assessment"]
    assert isinstance(assessment, TestCaseAssessment)
    assert len(assessment.evaluated_checklist) == 5
    assert all(o.assessment for o in assessment.evaluated_checklist)
    assert assessment.overall_verdict in ("Yes", "No")
    assert assessment.overall_verdict == "No"  # index-3 drives it
    # Verify the partial-yellow row survived round-trip
    partial_rows = [o for o in assessment.evaluated_checklist if o.partial]
    assert len(partial_rows) == 1
    assert partial_rows[0].verdict == "Yes"
    assert assessment.clarification_questions == ["Is the spec missing or simply not yet documented?"]


async def test_aggregator_skip_on_missing_state():
    node = AggregatorNode(
        client=make_mock_client("{}"),
        model="test-model",
        response_model=TestCaseAssessment,
        system_prompt="sys",
    )
    result = await node({})
    assert result == {"aggregated_assessment": None}


# ---------------------------------------------------------------------------
# Review-objectives loader
# ---------------------------------------------------------------------------


def test_load_default_review_objectives():
    objectives = load_default_review_objectives()
    assert len(objectives) == 5
    ids = [o.id for o in objectives]
    assert "expected_result_support" in ids
    assert "test_case_setup_clarity" in ids
    for o in objectives:
        assert isinstance(o, ReviewObjective)
        assert o.description  # non-empty
