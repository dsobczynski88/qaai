"""Unit tests for the test-case reviewer's no-decomposition mode.

When "Include requirement decomposition analysis" is OFF, the graph drops the
decomposer node and fans coverage out per requirement (not per spec), judging
the test case directly against the original requirement text. These tests make
no LLM/network calls — they exercise topology, the per-requirement dispatcher,
the requirement-level coverage node, and the aggregator's None-safety.
"""
import pytest
from langgraph.types import Send

from qaai.agents.shared.core import (
    DecomposedRequirement,
    DecomposedSpec,
    Requirement,
    TestCase,
)
from qaai.agents.test_case_reviewer.core import SpecAnalysis
from qaai.agents.test_case_reviewer.nodes import (
    RequirementCoveragePipelineNode,
    SingleReqCoverageNode,
    dispatch_coverage_by_requirement,
    dispatch_requirement_pipeline,
    make_aggregator_node,
    make_coverage_single_node,
)
from qaai.agents.test_case_reviewer.pipeline import TCReviewerRunnable
from qaai.core.config import PromptConfig

pytestmark = pytest.mark.unit


def _tc() -> TestCase:
    return TestCase(test_id="TC-1", description="d", setup="s", steps="st",
                    expectedResults="er")


def _reqs():
    return [
        Requirement(req_id="REQ-1", text="The system shall do X."),
        Requirement(req_id="REQ-2", text="The system shall do Y."),
    ]


def _decomposed(req: Requirement, n_specs: int) -> DecomposedRequirement:
    specs = [
        DecomposedSpec(
            spec_id=f"{req.req_id}-S{i}", description="d",
            acceptance_criteria="a", rationale="r",
        )
        for i in range(1, n_specs + 1)
    ]
    return DecomposedRequirement(requirement=req, decomposed_specifications=specs)


def test_no_decomposition_graph_has_no_decomposer(stub_llm_client):
    graph = TCReviewerRunnable(
        client=stub_llm_client, model="stub-model",
        prompt_config=PromptConfig.from_set("test_case_reviewer_v3"),
        include_decomposition=False,
    ).graph
    nodes = set(graph.get_graph().nodes.keys())
    # No decomposition stage at all in this mode — coverage fans out per requirement.
    assert "requirement_pipeline" not in nodes
    assert {"coverage_evaluator", "aggregator", "coverage_router"} <= nodes


def test_decomposition_graph_uses_requirement_pipeline(stub_llm_client):
    graph = TCReviewerRunnable(
        client=stub_llm_client, model="stub-model",
        prompt_config=PromptConfig.from_set("test_case_reviewer_v2"),
        include_decomposition=True,
    ).graph
    nodes = set(graph.get_graph().nodes.keys())
    # Decompose→coverage is fused into the per-requirement requirement_pipeline node;
    # there is no standalone decomposer or coverage_evaluator node in this mode.
    assert "requirement_pipeline" in nodes
    assert "coverage_evaluator" not in nodes


def test_dispatch_by_requirement_one_send_per_requirement():
    state = {"test_case": _tc(), "requirements": _reqs(), "cache_mode": "off"}
    sends = dispatch_coverage_by_requirement(state)

    assert len(sends) == 2
    assert all(isinstance(s, Send) and s.node == "coverage_evaluator" for s in sends)
    req_ids = {s.arg["requirement"].req_id for s in sends}
    assert req_ids == {"REQ-1", "REQ-2"}
    for s in sends:
        # No decomposed_spec in the no-decomposition payload; cache_mode threaded.
        assert "decomposed_spec" not in s.arg
        assert s.arg["cache_mode"] == "off"
        assert s.arg["test_case"].test_id == "TC-1"


def test_dispatch_by_requirement_safe_noop_on_missing_state():
    assert dispatch_coverage_by_requirement({}) == []
    assert dispatch_coverage_by_requirement({"test_case": _tc(), "requirements": []}) == []


# ---------------------------------------------------------------------------
# Decomposition-mode per-requirement fan-out (requirement_pipeline)
# ---------------------------------------------------------------------------


def test_dispatch_requirement_pipeline_one_send_per_requirement():
    state = {"test_case": _tc(), "requirements": _reqs(), "cache_mode": "off"}
    sends = dispatch_requirement_pipeline(state)

    assert len(sends) == 2
    assert all(isinstance(s, Send) and s.node == "requirement_pipeline" for s in sends)
    req_ids = {s.arg["requirement"].req_id for s in sends}
    assert req_ids == {"REQ-1", "REQ-2"}
    for s in sends:
        assert s.arg["cache_mode"] == "off"
        assert s.arg["test_case"].test_id == "TC-1"


def test_dispatch_requirement_pipeline_safe_noop_on_missing_state():
    assert dispatch_requirement_pipeline({}) == []
    assert dispatch_requirement_pipeline({"test_case": _tc(), "requirements": []}) == []


async def test_requirement_pipeline_node_decomposes_then_covers_specs():
    """The fused node runs its one decomposition then one coverage call per spec,
    returning both reduced keys list-wrapped so operator.add accumulates them."""
    req = _reqs()[0]
    dr = _decomposed(req, n_specs=3)

    async def fake_decomposer(state):
        assert state["requirement"] is req
        assert state["cache_mode"] == "off"
        return {"decomposed_requirement": dr}

    seen_specs = []

    async def fake_coverage(state):
        spec = state["decomposed_spec"]
        seen_specs.append(spec.spec_id)
        assert state["requirement"] is dr.requirement
        return {"coverage_analysis": [
            SpecAnalysis(spec_id=spec.spec_id, exists=True, assessment="ok")
        ]}

    node = RequirementCoveragePipelineNode(fake_decomposer, fake_coverage)
    out = await node({"test_case": _tc(), "requirement": req, "cache_mode": "off"})

    # One DecomposedRequirement, list-wrapped for operator.add.
    assert out["decomposed_requirements"] == [dr]
    # One SpecAnalysis per spec, all specs covered.
    assert [a.spec_id for a in out["coverage_analysis"]] == [f"{req.req_id}-S{i}" for i in (1, 2, 3)]
    assert set(seen_specs) == {f"{req.req_id}-S{i}" for i in (1, 2, 3)}


async def test_requirement_pipeline_node_soft_skips_on_failed_decomposition():
    """A None decomposition yields empty reduced updates (no coverage calls),
    so the reducer treats this requirement as a no-op rather than crashing."""
    async def fake_decomposer(state):
        return {"decomposed_requirement": None}

    async def fake_coverage(state):  # pragma: no cover - must not be called
        raise AssertionError("coverage must not run when decomposition fails")

    node = RequirementCoveragePipelineNode(fake_decomposer, fake_coverage)
    out = await node({"test_case": _tc(), "requirement": _reqs()[0], "cache_mode": "off"})
    assert out == {"decomposed_requirements": [], "coverage_analysis": []}


async def test_requirement_pipeline_node_soft_skips_on_missing_payload():
    async def fake_decomposer(state):  # pragma: no cover - must not be called
        raise AssertionError("decomposer must not run on incomplete payload")

    async def fake_coverage(state):  # pragma: no cover - must not be called
        raise AssertionError("coverage must not run on incomplete payload")

    node = RequirementCoveragePipelineNode(fake_decomposer, fake_coverage)
    out = await node({"cache_mode": "off"})
    assert out == {"decomposed_requirements": [], "coverage_analysis": []}


def test_req_coverage_node_validation_and_payload(stub_llm_client):
    node = make_coverage_single_node(
        stub_llm_client, "stub-model", {},
        prompt_template="single_test_coverage_eval/v4.0.0/template.jinja2",
        include_decomposition=False,
    )
    assert isinstance(node, SingleReqCoverageNode)

    state = {"test_case": _tc(), "requirement": _reqs()[0]}
    assert node._validate_state(state) is True
    # Missing requirement -> invalid (would skip).
    assert node._validate_state({"test_case": _tc()}) is False

    payload = node._build_payload(state)
    assert "decomposed_spec" not in payload
    assert payload["requirement"]["req_id"] == "REQ-1"
    # Cache key disambiguated by req_id, not spec_id.
    assert node._get_cache_node_name(state).endswith("_REQ-1")


def test_aggregator_payload_handles_missing_decomposed_requirements(stub_llm_client):
    node = make_aggregator_node(
        stub_llm_client, "stub-model", {},
        prompt_template="single_test_aggregator/v9.0.0/template.jinja2",
    )
    state = {
        "test_case": _tc(),
        "requirements": _reqs(),
        # review_objectives are embedded in the aggregator prompt (v8/v9), not passed as state.
        # decomposed_requirements intentionally absent (no-decomposition mode)
        "coverage_analysis": [],
    }
    assert node._validate_state(state) is True  # no longer requires decomposed_requirements
    payload = node._build_payload(state)  # must not raise on missing key
    assert payload["decomposed_requirements"] == []
