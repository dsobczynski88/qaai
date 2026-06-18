"""Unit tests for the test-case reviewer's no-decomposition mode.

When "Include requirement decomposition analysis" is OFF, the graph drops the
decomposer node and fans coverage out per requirement (not per spec), judging
the test case directly against the original requirement text. These tests make
no LLM/network calls — they exercise topology, the per-requirement dispatcher,
the requirement-level coverage node, and the aggregator's None-safety.
"""
import pytest
from langgraph.types import Send

from qaai.agents.shared.core import Requirement, TestCase
from qaai.agents.test_case_reviewer.core import ReviewObjective
from qaai.agents.test_case_reviewer.nodes import (
    SingleReqCoverageNode,
    dispatch_coverage_by_requirement,
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


def test_no_decomposition_graph_has_no_decomposer(stub_llm_client):
    graph = TCReviewerRunnable(
        client=stub_llm_client, model="stub-model",
        prompt_config=PromptConfig.from_set("test_case_reviewer_v3"),
        include_decomposition=False,
    ).graph
    nodes = set(graph.get_graph().nodes.keys())
    assert "decomposer" not in nodes
    assert {"coverage_evaluator", "aggregator", "coverage_router"} <= nodes


def test_decomposition_graph_keeps_decomposer(stub_llm_client):
    graph = TCReviewerRunnable(
        client=stub_llm_client, model="stub-model",
        prompt_config=PromptConfig.from_set("test_case_reviewer_v2"),
        include_decomposition=True,
    ).graph
    assert "decomposer" in set(graph.get_graph().nodes.keys())


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
        prompt_template="single_test_aggregator/v7.0.0/template.jinja2",
    )
    state = {
        "test_case": _tc(),
        "requirements": _reqs(),
        "review_objectives": [ReviewObjective(id="o1", description="d")],
        # decomposed_requirements intentionally absent (no-decomposition mode)
        "coverage_analysis": [],
    }
    assert node._validate_state(state) is True  # no longer requires decomposed_requirements
    payload = node._build_payload(state)  # must not raise on missing key
    assert payload["decomposed_requirements"] == []
