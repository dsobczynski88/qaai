"""Unit tests for the generalized reviewer cache (ReviewCacheManager) and the
cache_mode gating threaded through the node base classes and the per-spec
evaluators of the test-suite / test-case reviewers.

No live LLM calls — a counting mock client is used throughout so we can assert
exactly how many times the LLM was invoked (i.e. whether a cache hit occurred).
"""
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel

from qaai.core.cache import ReviewCacheManager, _sanitize
from qaai.agents.shared.nodes import StandardLLMNode
from qaai.agents.shared.core import DecomposedSpec, Requirement, TestCase
from qaai.agents.test_suite_reviewer.core import EvaluatedSpec, TestSuite
from qaai.agents.test_suite_reviewer.nodes import (
    SingleSpecEvaluatorNode,
    dispatch_coverage as rtm_dispatch_coverage,
)
from qaai.agents.test_case_reviewer.core import SpecAnalysis
from qaai.agents.test_case_reviewer.nodes import (
    SingleSpecCoverageNode,
    dispatch_coverage as tc_dispatch_coverage,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_counting_client(content: str, prompt_tokens: int = 10, completion_tokens: int = 5):
    """Mock client whose chat_completion returns `content`; tracks call_count."""
    usage = MagicMock()
    usage.prompt_tokens = prompt_tokens
    usage.completion_tokens = completion_tokens
    choice = MagicMock()
    choice.message.content = content
    completion = MagicMock()
    completion.choices = [choice]
    completion.usage = usage
    client = MagicMock()
    client.chat_completion = AsyncMock(return_value=completion)
    return client


class _Result(BaseModel):
    value: str


class _DummyNode(StandardLLMNode):
    """Minimal StandardLLMNode that caches on state['entity']."""

    def _validate_state(self, state):
        return state.get("entity") is not None

    def _get_cache_entity_id(self, state):
        return state.get("entity")

    def _build_payload(self, state):
        return {"x": 1}

    def _format_response(self, parsed):
        return {"out": parsed}


def make_dummy_node(cache, *, is_final=False):
    client = make_counting_client(json.dumps({"value": "ok"}))
    node = _DummyNode(
        client=client,
        model="m",
        response_model=_Result,
        system_prompt="sys",
        cache_manager=cache,
        prompt_version="v1.0.0",
        is_final_output=is_final,
    )
    return node, client


@pytest.fixture
def cache(tmp_path):
    return ReviewCacheManager(cache_dir=tmp_path)


# ---------------------------------------------------------------------------
# 1. ReviewCacheManager
# ---------------------------------------------------------------------------


async def test_set_get_roundtrip_one_folder_per_entity(cache, tmp_path):
    await cache.set(
        entity_id="REQ-1", node_name="decomposernode", prompt_version="v1.0.0",
        result_dict={"value": "hi"}, prompt_tokens=3, completion_tokens=2, model="m",
    )
    # One level: cache_dir / entity_id / node_version.json
    expected = tmp_path / "REQ-1" / "decomposernode_v1.0.0.json"
    assert expected.exists()
    got = await cache.get("REQ-1", "decomposernode", "v1.0.0")
    assert got["result"] == {"value": "hi"}
    assert got["meta"]["entity_id"] == "REQ-1"


async def test_version_bump_is_a_miss(cache):
    await cache.set("REQ-1", "n", "v1.0.0", {"value": "hi"}, 0, 0, "m")
    assert await cache.get("REQ-1", "n", "v1.0.0") is not None
    assert await cache.get("REQ-1", "n", "v2.0.0") is None


async def test_prompt_set_namespaces_cache(cache, tmp_path):
    """Two prompt sets sharing entity/node/version never alias; each lands in its
    own subfolder and an un-namespaced read still misses."""
    await cache.set(
        "REQ-1", "coverage_evaluator", "v8.0.0", {"value": "v3"}, 0, 0, "m",
        prompt_set="test_suite_reviewer_v3",
    )
    await cache.set(
        "REQ-1", "coverage_evaluator", "v8.0.0", {"value": "v4"}, 0, 0, "m",
        prompt_set="test_suite_reviewer_v4",
    )
    # Per-set subfolders under the entity, not a single clobbered file
    assert (tmp_path / "REQ-1" / "test_suite_reviewer_v3" / "coverage_evaluator_v8.0.0.json").exists()
    assert (tmp_path / "REQ-1" / "test_suite_reviewer_v4" / "coverage_evaluator_v8.0.0.json").exists()

    v3 = await cache.get("REQ-1", "coverage_evaluator", "v8.0.0", "test_suite_reviewer_v3")
    v4 = await cache.get("REQ-1", "coverage_evaluator", "v8.0.0", "test_suite_reviewer_v4")
    assert v3["result"] == {"value": "v3"}
    assert v4["result"] == {"value": "v4"}
    # The set name is recorded in the entry meta (regulatory provenance)
    assert v3["meta"]["prompt_set"] == "test_suite_reviewer_v3"
    assert v4["meta"]["prompt_set"] == "test_suite_reviewer_v4"

    # An un-namespaced read does not pick up a namespaced entry
    assert await cache.get("REQ-1", "coverage_evaluator", "v8.0.0") is None


async def test_purge_entity_removes_only_that_entity(cache, tmp_path):
    await cache.set("REQ-1", "decomposernode", "v1.0.0", {"value": "a"}, 0, 0, "m")
    await cache.set("REQ-1", "synthesizer", "v8.0.0", {"value": "b"}, 0, 0, "m")
    await cache.set("REQ-2", "decomposernode", "v1.0.0", {"value": "c"}, 0, 0, "m")

    await cache.purge_entity("REQ-1")

    assert not (tmp_path / "REQ-1").exists()
    assert await cache.get("REQ-1", "decomposernode", "v1.0.0") is None
    assert await cache.get("REQ-1", "synthesizer", "v8.0.0") is None
    # The other entity is untouched
    assert await cache.get("REQ-2", "decomposernode", "v1.0.0") is not None


async def test_purge_entity_scoped_to_prompt_set(cache, tmp_path):
    """purge_entity(prompt_set=...) drops only that set's namespace, leaving the
    other set and the legacy un-namespaced entry intact."""
    await cache.set(
        "REQ-1", "coverage_evaluator", "v8.0.0", {"value": "v3"}, 0, 0, "m",
        prompt_set="test_suite_reviewer_v3",
    )
    await cache.set(
        "REQ-1", "coverage_evaluator", "v8.0.0", {"value": "v4"}, 0, 0, "m",
        prompt_set="test_suite_reviewer_v4",
    )
    await cache.set("REQ-1", "decomposernode", "v1.0.0", {"value": "base"}, 0, 0, "m")

    await cache.purge_entity("REQ-1", "test_suite_reviewer_v3")

    assert not (tmp_path / "REQ-1" / "test_suite_reviewer_v3").exists()
    assert await cache.get(
        "REQ-1", "coverage_evaluator", "v8.0.0", "test_suite_reviewer_v3"
    ) is None
    # The other set and the un-namespaced entry survive
    assert await cache.get(
        "REQ-1", "coverage_evaluator", "v8.0.0", "test_suite_reviewer_v4"
    ) is not None
    assert await cache.get("REQ-1", "decomposernode", "v1.0.0") is not None


async def test_purge_missing_entity_is_noop(cache):
    # Purging an entity that was never cached must not raise.
    await cache.purge_entity("REQ-DOES-NOT-EXIST")


async def test_entity_and_node_name_sanitized(cache, tmp_path):
    await cache.set("REQ/1:x", "node spec/1", "v1.0.0", {"value": "a"}, 0, 0, "m")
    files = list(tmp_path.rglob("*.json"))
    assert len(files) == 1
    # No path separators or colons leaked into the folder or filename
    assert "/" not in files[0].name and ":" not in files[0].name
    assert files[0].parent.name == _sanitize("REQ/1:x")
    # And it round-trips
    assert await cache.get("REQ/1:x", "node spec/1", "v1.0.0") is not None


async def test_redis_absent_disk_only(tmp_path):
    cache = ReviewCacheManager(cache_dir=tmp_path, redis_url=None)
    await cache.set("E", "n", "v1.0.0", {"value": "x"}, 0, 0, "m")
    got = await cache.get("E", "n", "v1.0.0")
    assert got["result"]["value"] == "x"


def test_extract_prompt_version():
    assert ReviewCacheManager.extract_prompt_version("hazard_h1/v1.0.0/template.jinja2") == "v1.0.0"
    assert ReviewCacheManager.extract_prompt_version("synthesizer/v8.0.0/template.jinja2") == "v8.0.0"
    assert ReviewCacheManager.extract_prompt_version("flat.jinja2") == "default"


# ---------------------------------------------------------------------------
# 2. Base-node cache_mode gating
# ---------------------------------------------------------------------------


async def test_partial_interim_node_caches(cache):
    state = {"entity": "REQ-1", "cache_mode": "partial"}
    node, client = make_dummy_node(cache)
    await node(state)
    assert client.chat_completion.call_count == 1  # miss → LLM ran

    node2, client2 = make_dummy_node(cache)
    result = await node2(state)
    assert client2.chat_completion.call_count == 0  # hit → no LLM
    assert result["out"].value == "ok"


async def test_partial_final_node_always_reruns(cache):
    state = {"entity": "REQ-1", "cache_mode": "partial"}
    node, client = make_dummy_node(cache, is_final=True)
    await node(state)
    assert client.chat_completion.call_count == 1

    # File was written (write is allowed under partial) but a final node never
    # reads under partial — it re-runs for a fresh result.
    node2, client2 = make_dummy_node(cache, is_final=True)
    await node2(state)
    assert client2.chat_completion.call_count == 1


async def test_full_final_node_uses_cache(cache):
    state = {"entity": "REQ-1", "cache_mode": "full"}
    node, client = make_dummy_node(cache, is_final=True)
    await node(state)

    node2, client2 = make_dummy_node(cache, is_final=True)
    await node2(state)
    assert client2.chat_completion.call_count == 0  # hit under full


async def test_off_mode_neither_reads_nor_writes(cache, tmp_path):
    state = {"entity": "REQ-1", "cache_mode": "off"}
    node, client = make_dummy_node(cache)
    await node(state)
    node2, client2 = make_dummy_node(cache)
    await node2(state)
    assert client2.chat_completion.call_count == 1  # no read
    assert list(tmp_path.rglob("*.json")) == []  # no write


async def test_default_mode_is_partial(cache):
    # No cache_mode in state → treated as "partial" (interim node caches).
    state = {"entity": "REQ-1"}
    node, client = make_dummy_node(cache)
    await node(state)
    node2, client2 = make_dummy_node(cache)
    await node2(state)
    assert client2.chat_completion.call_count == 0


async def test_no_cache_manager_means_no_caching(tmp_path):
    state = {"entity": "REQ-1", "cache_mode": "partial"}
    client = make_counting_client(json.dumps({"value": "ok"}))
    node = _DummyNode(
        client=client, model="m", response_model=_Result, system_prompt="s",
        cache_manager=None, prompt_version="v1.0.0",
    )
    await node(state)
    await node(state)
    assert client.chat_completion.call_count == 2


# ---------------------------------------------------------------------------
# 3. Per-spec evaluators — disambiguation by spec_id + cache hit on rerun
# ---------------------------------------------------------------------------


def _rtm_spec_state(spec_id: str, cache_mode="partial"):
    req = Requirement(req_id="REQ-1", text="The system shall do X.")
    spec = DecomposedSpec(spec_id=spec_id, description="d", acceptance_criteria="a", rationale="r")
    suite = TestSuite(requirement=req, test_cases=[], summary=[])
    return {"requirement": req, "decomposed_spec": spec, "test_suite": suite, "cache_mode": cache_mode}


def _make_rtm_spec_node(cache):
    content = json.dumps({"spec_id": "S1", "covered_exists": True, "covered_by_test_cases": []})
    client = make_counting_client(content)
    node = SingleSpecEvaluatorNode(
        client=client, model="m", system_prompt="s",
        cache_manager=cache, prompt_version="v7.0.0",
    )
    return node, client


async def test_rtm_per_spec_two_specs_distinct_files(cache, tmp_path):
    node, _ = _make_rtm_spec_node(cache)
    await node(_rtm_spec_state("S1"))
    await node(_rtm_spec_state("S2"))
    files = sorted(p.name for p in (tmp_path / "REQ-1").glob("*.json"))
    assert files == [
        "singlespecevaluatornode_S1_v7.0.0.json",
        "singlespecevaluatornode_S2_v7.0.0.json",
    ]


async def test_rtm_per_spec_rerun_hits_cache(cache):
    node, client = _make_rtm_spec_node(cache)
    out1 = await node(_rtm_spec_state("S1"))
    assert client.chat_completion.call_count == 1
    assert isinstance(out1["coverage_analysis"][0], EvaluatedSpec)

    node2, client2 = _make_rtm_spec_node(cache)
    await node2(_rtm_spec_state("S1"))
    assert client2.chat_completion.call_count == 0  # cache hit


async def test_rtm_per_spec_off_mode_no_cache(cache, tmp_path):
    node, client = _make_rtm_spec_node(cache)
    await node(_rtm_spec_state("S1", cache_mode="off"))
    node2, client2 = _make_rtm_spec_node(cache)
    await node2(_rtm_spec_state("S1", cache_mode="off"))
    assert client2.chat_completion.call_count == 1
    assert list(tmp_path.rglob("*.json")) == []


async def test_rtm_per_spec_payload_includes_summarized_designs():
    from qaai.agents.test_suite_reviewer.core import SummarizedDesignSpec

    node, client = _make_rtm_spec_node(cache=None)  # no cache → always calls LLM
    state = _rtm_spec_state("S1", cache_mode="off")
    state["summarized_designs"] = [
        SummarizedDesignSpec(
            doc_id="DOC-1", design_intent="intent", implements="REQ-1",
            key_components=["c1"], verification_hooks=["h1"],
        )
    ]
    await node(state)
    messages = client.chat_completion.call_args.kwargs["messages"]
    payload = json.loads(messages[-1]["content"])
    assert "summarized_designs" in payload
    assert payload["summarized_designs"][0]["doc_id"] == "DOC-1"


async def test_rtm_per_spec_payload_null_designs_when_absent():
    node, client = _make_rtm_spec_node(cache=None)
    await node(_rtm_spec_state("S1", cache_mode="off"))
    messages = client.chat_completion.call_args.kwargs["messages"]
    payload = json.loads(messages[-1]["content"])
    assert payload["summarized_designs"] is None


def _tc_spec_state(spec_id: str, cache_mode="partial"):
    tc = TestCase(test_id="TEST-1", description="d", in_baseline=True)
    req = Requirement(req_id="REQ-1", text="The system shall do X.")
    spec = DecomposedSpec(spec_id=spec_id, description="d", acceptance_criteria="a", rationale="r")
    return {"test_case": tc, "requirement": req, "decomposed_spec": spec, "cache_mode": cache_mode}


def _make_tc_spec_node(cache):
    content = json.dumps({"spec_id": "S1", "exists": True, "assessment": "ok"})
    client = make_counting_client(content)
    node = SingleSpecCoverageNode(
        client=client, model="m", system_prompt="s",
        cache_manager=cache, prompt_version="v3.0.0",
    )
    return node, client


async def test_tc_per_spec_caches_under_test_id(cache, tmp_path):
    node, client = _make_tc_spec_node(cache)
    out = await node(_tc_spec_state("S1"))
    assert isinstance(out["coverage_analysis"][0], SpecAnalysis)
    assert (tmp_path / "TEST-1" / "singlespeccoveragenode_S1_v3.0.0.json").exists()

    node2, client2 = _make_tc_spec_node(cache)
    await node2(_tc_spec_state("S1"))
    assert client2.chat_completion.call_count == 0  # cache hit


# ---------------------------------------------------------------------------
# 4. cache_mode propagation through Send dispatchers
# ---------------------------------------------------------------------------


def test_rtm_dispatch_propagates_cache_mode():
    from qaai.agents.test_suite_reviewer.core import DecomposedRequirement

    req = Requirement(req_id="REQ-1", text="x")
    specs = [
        DecomposedSpec(spec_id="S1", description="d", acceptance_criteria="a", rationale="r"),
        DecomposedSpec(spec_id="S2", description="d", acceptance_criteria="a", rationale="r"),
    ]
    decomposed = DecomposedRequirement(requirement=req, decomposed_specifications=specs)
    suite = TestSuite(requirement=req, test_cases=[], summary=[])
    sends = rtm_dispatch_coverage({
        "requirement": req, "decomposed_requirement": decomposed,
        "test_suite": suite, "cache_mode": "off",
    })
    assert len(sends) == 2
    assert all(s.arg["cache_mode"] == "off" for s in sends)


def test_rtm_dispatch_propagates_summarized_designs():
    from qaai.agents.test_suite_reviewer.core import (
        DecomposedRequirement,
        SummarizedDesignSpec,
    )

    req = Requirement(req_id="REQ-1", text="x")
    specs = [
        DecomposedSpec(spec_id="S1", description="d", acceptance_criteria="a", rationale="r"),
        DecomposedSpec(spec_id="S2", description="d", acceptance_criteria="a", rationale="r"),
    ]
    decomposed = DecomposedRequirement(requirement=req, decomposed_specifications=specs)
    suite = TestSuite(requirement=req, test_cases=[], summary=[])
    designs = [
        SummarizedDesignSpec(
            doc_id="DOC-1",
            design_intent="intent",
            implements="REQ-1",
            key_components=["c1"],
            verification_hooks=["h1"],
        )
    ]
    # Present: every Send carries the same design list.
    sends = rtm_dispatch_coverage({
        "requirement": req, "decomposed_requirement": decomposed,
        "test_suite": suite, "summarized_designs": designs,
    })
    assert len(sends) == 2
    assert all(s.arg["summarized_designs"] == designs for s in sends)

    # Absent: key still present (null-safe), set to None.
    sends_none = rtm_dispatch_coverage({
        "requirement": req, "decomposed_requirement": decomposed,
        "test_suite": suite,
    })
    assert all(s.arg["summarized_designs"] is None for s in sends_none)


def test_tc_dispatch_propagates_cache_mode():
    from qaai.agents.test_case_reviewer.core import DecomposedRequirement

    req = Requirement(req_id="REQ-1", text="x")
    specs = [DecomposedSpec(spec_id="S1", description="d", acceptance_criteria="a", rationale="r")]
    decomposed = DecomposedRequirement(requirement=req, decomposed_specifications=specs)
    tc = TestCase(test_id="TEST-1", description="d", in_baseline=True)
    sends = tc_dispatch_coverage({
        "test_case": tc, "decomposed_requirements": [decomposed], "cache_mode": "off",
    })
    assert len(sends) == 1
    assert sends[0].arg["cache_mode"] == "off"


# ---------------------------------------------------------------------------
# 5. Pipeline wiring — Option A: the hazard reviewer's embedded RTM is uncached
# ---------------------------------------------------------------------------


def test_rtm_runnable_stores_cache_manager(cache):
    from qaai.agents.test_suite_reviewer.pipeline import RTMReviewerRunnable

    client = make_counting_client("{}")
    rtm = RTMReviewerRunnable(client, "m", cache_manager=cache)
    assert rtm.cache_manager is cache


def test_hazard_embedded_rtm_is_uncached(cache):
    """The hazard reviewer's own nodes share the cache, but its embedded
    test-suite subgraph must NOT self-cache — its result is cached as one
    blob per requirement by RequirementReviewerNode instead."""
    from qaai.agents.hazard_risk_reviewer.pipeline import HazardReviewerRunnable

    client = make_counting_client("{}")
    hz = HazardReviewerRunnable(client, "m", cache_manager=cache)
    assert hz.cache_manager is cache
    assert hz.rtm.cache_manager is None
