"""Unit tests for the generalized reviewer cache (ReviewCacheManager) and the
cache_mode gating threaded through the node base classes and the per-spec
evaluators of the test-suite / test-case reviewers.

Disk files are immutable + timestamped ({node}_{version}_{ts}.json); reads select
the newest. Cache modes are "off" | "on" (default) | "test".

No live LLM calls — a counting mock client is used throughout so we can assert
exactly how many times the LLM was invoked (i.e. whether a cache hit occurred).
"""
import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel

import qaai.core.cache as cache_mod
from qaai.core.cache import ReviewCacheManager, _sanitize
from qaai.agents.shared.nodes import CacheRequiredError, StandardLLMNode
from qaai.agents.shared.core import DecomposedSpec, Requirement, TestCase
from qaai.agents.test_suite_reviewer.core import EvaluatedSpec, TestSuite
from qaai.agents.test_suite_reviewer.nodes import (
    SingleSpecEvaluatorNode,
    dispatch_coverage as rtm_dispatch_coverage,
)
from qaai.agents.test_case_reviewer.core import SpecAnalysis
from qaai.agents.test_case_reviewer.nodes import (
    SingleSpecCoverageNode,
    dispatch_requirement_pipeline as tc_dispatch_requirement_pipeline,
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


def _stamp_seq(monkeypatch, *stamps):
    """Force _now_timestamp to yield the given timestamps in order."""
    it = iter(stamps)
    monkeypatch.setattr(cache_mod, "_now_timestamp", lambda: next(it))


@pytest.fixture
def cache(tmp_path):
    return ReviewCacheManager(cache_dir=tmp_path)


def _only_file(directory, prefix):
    matches = sorted(directory.glob(f"{prefix}*.json"))
    assert matches, f"no file matching {prefix}* under {directory}"
    return matches


# ---------------------------------------------------------------------------
# 1. ReviewCacheManager — timestamped, append-only, newest-wins
# ---------------------------------------------------------------------------


async def test_set_get_roundtrip_timestamped_file(cache, tmp_path):
    await cache.set(
        entity_id="REQ-1", node_name="decomposernode", prompt_version="v1.0.0",
        result_dict={"value": "hi"}, prompt_tokens=3, completion_tokens=2, model="m",
    )
    # cache_dir / entity_id / {node}_{version}_{timestamp}.json
    files = _only_file(tmp_path / "REQ-1", "decomposernode_v1.0.0_")
    assert len(files) == 1
    got = await cache.get("REQ-1", "decomposernode", "v1.0.0")
    assert got["result"] == {"value": "hi"}
    assert got["meta"]["entity_id"] == "REQ-1"


async def test_set_appends_new_file_newest_wins(cache, tmp_path, monkeypatch):
    _stamp_seq(
        monkeypatch,
        "2026_01_01_00_00_00_000001",
        "2026_01_01_00_00_00_000002",
    )
    await cache.set("REQ-1", "n", "v1.0.0", {"value": "a"}, 0, 0, "m")
    await cache.set("REQ-1", "n", "v1.0.0", {"value": "b"}, 0, 0, "m")

    files = sorted((tmp_path / "REQ-1").glob("n_v1.0.0_*.json"))
    assert len(files) == 2  # append, not overwrite
    got = await cache.get("REQ-1", "n", "v1.0.0")
    assert got["result"] == {"value": "b"}  # newest timestamp wins


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
    _only_file(tmp_path / "REQ-1" / "test_suite_reviewer_v3", "coverage_evaluator_v8.0.0_")
    _only_file(tmp_path / "REQ-1" / "test_suite_reviewer_v4", "coverage_evaluator_v8.0.0_")

    v3 = await cache.get("REQ-1", "coverage_evaluator", "v8.0.0", "test_suite_reviewer_v3")
    v4 = await cache.get("REQ-1", "coverage_evaluator", "v8.0.0", "test_suite_reviewer_v4")
    assert v3["result"] == {"value": "v3"}
    assert v4["result"] == {"value": "v4"}
    assert v3["meta"]["prompt_set"] == "test_suite_reviewer_v3"
    assert v4["meta"]["prompt_set"] == "test_suite_reviewer_v4"

    # An un-namespaced read does not pick up a namespaced entry
    assert await cache.get("REQ-1", "coverage_evaluator", "v8.0.0") is None


async def test_include_design_namespaces_cache(cache, tmp_path):
    """The design-summary discriminator (ds0/ds1) keys design-sensitive results so
    a with-design result never aliases a without-design one for the same
    entity/set/node/version."""
    await cache.set(
        "REQ-1", "synthesizer", "v8.0.0", {"value": "with"}, 0, 0, "m",
        prompt_set="test_suite_reviewer_v3", include_design=True,
    )
    await cache.set(
        "REQ-1", "synthesizer", "v8.0.0", {"value": "without"}, 0, 0, "m",
        prompt_set="test_suite_reviewer_v3", include_design=False,
    )
    # Distinct files under the same set folder — ds token in the filename stem
    _only_file(tmp_path / "REQ-1" / "test_suite_reviewer_v3", "synthesizer_v8.0.0_ds1_")
    _only_file(tmp_path / "REQ-1" / "test_suite_reviewer_v3", "synthesizer_v8.0.0_ds0_")

    on = await cache.get("REQ-1", "synthesizer", "v8.0.0", "test_suite_reviewer_v3", include_design=True)
    off = await cache.get("REQ-1", "synthesizer", "v8.0.0", "test_suite_reviewer_v3", include_design=False)
    assert on["result"] == {"value": "with"}
    assert off["result"] == {"value": "without"}
    assert on["meta"]["include_design"] is True
    assert off["meta"]["include_design"] is False


async def test_include_design_none_preserves_legacy_layout(cache, tmp_path):
    """include_design=None (unaffected nodes / other reviewers) keeps the legacy
    un-discriminated filename and key."""
    await cache.set("REQ-1", "decomposer", "v5.0.0", {"value": "x"}, 0, 0, "m")
    _only_file(tmp_path / "REQ-1", "decomposer_v5.0.0_")
    got = await cache.get("REQ-1", "decomposer", "v5.0.0")
    assert got["result"] == {"value": "x"}
    # no ds token was written
    assert not sorted((tmp_path / "REQ-1").glob("decomposer_v5.0.0_ds*"))


async def test_design_sensitive_node_keys_cache_by_flag(cache):
    """A design_sensitive node's cache read hits only its own mode; the opposite
    mode is a miss → a live LLM call — driven by state['include_design_summaries'].
    A non-sensitive node ignores the flag entirely (discriminator is None)."""
    node, client = make_dummy_node(cache)
    node.design_sensitive = True

    # design ON: miss → 1 live call, writes ds1
    client.chat_completion.return_value.choices[0].message.content = json.dumps({"value": "on"})
    await node({"entity": "REQ-1", "include_design_summaries": True, "cache_mode": "on"})
    assert client.chat_completion.await_count == 1
    # design ON again: hit → no new call
    await node({"entity": "REQ-1", "include_design_summaries": True, "cache_mode": "on"})
    assert client.chat_completion.await_count == 1
    # design OFF: different discriminator → miss → live call
    await node({"entity": "REQ-1", "include_design_summaries": False, "cache_mode": "on"})
    assert client.chat_completion.await_count == 2

    assert node._design_discriminator({"include_design_summaries": True}) is True
    assert node._design_discriminator({"include_design_summaries": False}) is False
    # A non-sensitive node never discriminates.
    node.design_sensitive = False
    assert node._design_discriminator({"include_design_summaries": True}) is None


# --- run-scoped purge (success-gating) ------------------------------------


async def test_purge_run_removes_only_this_runs_files(cache, tmp_path, monkeypatch):
    """purge_run drops only files written at/after ``since``; earlier good runs
    for the same entity survive and remain the newest readable result."""
    _stamp_seq(
        monkeypatch,
        "2026_01_01_00_00_00_000000",  # old (good) run
        "2026_06_15_00_00_00_000000",  # this (failed) run
    )
    await cache.set("REQ-1", "n", "v1.0.0", {"value": "old"}, 0, 0, "m")
    since = datetime(2026, 6, 1)
    await cache.set("REQ-1", "n", "v1.0.0", {"value": "new"}, 0, 0, "m")

    await cache.purge_run("REQ-1", since)

    files = sorted((tmp_path / "REQ-1").glob("n_v1.0.0_*.json"))
    assert len(files) == 1  # only the recent file removed
    got = await cache.get("REQ-1", "n", "v1.0.0")
    assert got["result"] == {"value": "old"}  # earlier good run restored as newest


async def test_purge_run_scoped_to_prompt_set(cache, tmp_path, monkeypatch):
    _stamp_seq(monkeypatch, "2026_06_15_00_00_00_000000", "2026_06_15_00_00_00_000001")
    since = datetime(2026, 6, 1)
    await cache.set(
        "REQ-1", "n", "v1.0.0", {"value": "v3"}, 0, 0, "m",
        prompt_set="test_suite_reviewer_v3",
    )
    await cache.set(
        "REQ-1", "n", "v1.0.0", {"value": "v4"}, 0, 0, "m",
        prompt_set="test_suite_reviewer_v4",
    )
    await cache.purge_run("REQ-1", since, "test_suite_reviewer_v3")

    assert await cache.get("REQ-1", "n", "v1.0.0", "test_suite_reviewer_v3") is None
    assert await cache.get("REQ-1", "n", "v1.0.0", "test_suite_reviewer_v4") is not None


async def test_purge_run_missing_entity_is_noop(cache):
    await cache.purge_run("REQ-DOES-NOT-EXIST", datetime(2026, 1, 1))


# --- full-entity purge (manual clear) -------------------------------------


async def test_purge_entity_removes_only_that_entity(cache, tmp_path):
    await cache.set("REQ-1", "decomposernode", "v1.0.0", {"value": "a"}, 0, 0, "m")
    await cache.set("REQ-1", "synthesizer", "v8.0.0", {"value": "b"}, 0, 0, "m")
    await cache.set("REQ-2", "decomposernode", "v1.0.0", {"value": "c"}, 0, 0, "m")

    await cache.purge_entity("REQ-1")

    assert not (tmp_path / "REQ-1").exists()
    assert await cache.get("REQ-1", "decomposernode", "v1.0.0") is None
    assert await cache.get("REQ-2", "decomposernode", "v1.0.0") is not None


async def test_purge_missing_entity_is_noop(cache):
    await cache.purge_entity("REQ-DOES-NOT-EXIST")


async def test_entity_and_node_name_sanitized(cache, tmp_path):
    await cache.set("REQ/1:x", "node spec/1", "v1.0.0", {"value": "a"}, 0, 0, "m")
    files = list(tmp_path.rglob("*.json"))
    assert len(files) == 1
    assert "/" not in files[0].name and ":" not in files[0].name
    assert files[0].parent.name == _sanitize("REQ/1:x")
    assert await cache.get("REQ/1:x", "node spec/1", "v1.0.0") is not None


async def test_redis_absent_disk_only(tmp_path):
    cache = ReviewCacheManager(cache_dir=tmp_path, redis_url=None)
    await cache.set("E", "n", "v1.0.0", {"value": "x"}, 0, 0, "m")
    got = await cache.get("E", "n", "v1.0.0")
    assert got["result"]["value"] == "x"


async def test_relative_cache_dir_anchored_to_project_root(tmp_path, monkeypatch):
    """A relative CACHE_DIR resolves against PROJECT_ROOT, not the process cwd —
    so a set() from one working directory is read back as a tier-3 HIT from
    another (regression guard for the phantom-MISS bug)."""
    anchor = tmp_path / "repo_root"
    anchor.mkdir()
    monkeypatch.setattr(cache_mod, "PROJECT_ROOT", anchor)

    (tmp_path / "cwd_a").mkdir()
    monkeypatch.chdir(tmp_path / "cwd_a")
    mgr = cache_mod.ReviewCacheManager(cache_dir="shared/runs")
    assert mgr.cache_dir == anchor / "shared" / "runs"
    await mgr.set(
        "TEST-88", "singlespeccoveragenode_REQ_100-S3", "v3.0.0",
        {"value": "x"}, 0, 0, "m",
    )

    (tmp_path / "cwd_b").mkdir()
    monkeypatch.chdir(tmp_path / "cwd_b")
    mgr2 = cache_mod.ReviewCacheManager(cache_dir="shared/runs")
    assert mgr2.cache_dir == anchor / "shared" / "runs"
    got = await mgr2.get("TEST-88", "singlespeccoveragenode_REQ_100-S3", "v3.0.0")
    assert got is not None  # cross-cwd HIT
    assert got["result"] == {"value": "x"}
    assert got["meta"]["cache_tier_origin"] == 3


async def test_absolute_cache_dir_honored_unchanged(tmp_path):
    mgr = ReviewCacheManager(cache_dir=tmp_path / "abs_cache")
    assert mgr.cache_dir == tmp_path / "abs_cache"


def test_cache_dir_defaults_to_shared_runs():
    """Regression guard: the reviewer cache defaults under ./shared/runs and the
    pyjama JAMA source cache under ./shared — never ./cache."""
    from qaai.core.config import Settings
    from pyjama.utils.jama_constants import CACHE_SOURCE_ROOT

    assert Settings.model_fields["cache_dir"].default == "./shared/runs"
    assert CACHE_SOURCE_ROOT.startswith("./shared")


async def test_legacy_untimestamped_file_still_hits(cache, tmp_path):
    """A legacy pre-timestamp file ({node}_{version}.json) lacking a 'meta' block
    must still read as a HIT (fallback), not a MISS."""
    entity_dir = tmp_path / "TEST-1"
    entity_dir.mkdir()
    (entity_dir / "n_v1.0.0.json").write_text(
        json.dumps({"result": {"value": "legacy"}}), encoding="utf-8"
    )
    got = await cache.get("TEST-1", "n", "v1.0.0")
    assert got is not None
    assert got["result"] == {"value": "legacy"}
    assert got["meta"]["cache_tier_origin"] == 3


def test_extract_prompt_version():
    assert ReviewCacheManager.extract_prompt_version("hazard_h1/v1.0.0/template.jinja2") == "v1.0.0"
    assert ReviewCacheManager.extract_prompt_version("synthesizer/v8.0.0/template.jinja2") == "v8.0.0"
    assert ReviewCacheManager.extract_prompt_version("flat.jinja2") == "default"


# ---------------------------------------------------------------------------
# 2. Base-node cache_mode gating (off | on | test)
# ---------------------------------------------------------------------------


async def test_on_interim_node_caches(cache):
    state = {"entity": "REQ-1", "cache_mode": "on"}
    node, client = make_dummy_node(cache)
    await node(state)
    assert client.chat_completion.call_count == 1  # miss → LLM ran

    node2, client2 = make_dummy_node(cache)
    result = await node2(state)
    assert client2.chat_completion.call_count == 0  # hit → no LLM
    assert result["out"].value == "ok"


async def test_on_final_node_always_reruns(cache):
    state = {"entity": "REQ-1", "cache_mode": "on"}
    node, client = make_dummy_node(cache, is_final=True)
    await node(state)
    assert client.chat_completion.call_count == 1

    # Written through (on writes every node) but a final node never reads under
    # "on" — it re-runs for a fresh result.
    node2, client2 = make_dummy_node(cache, is_final=True)
    await node2(state)
    assert client2.chat_completion.call_count == 1


async def test_test_mode_uses_cache_including_final(cache):
    # Populate the cache with an "on" run (writes even the final node)...
    node, _ = make_dummy_node(cache, is_final=True)
    await node({"entity": "REQ-1", "cache_mode": "on"})

    # ...then a "test" run reads it back with no LLM call (even for the final node).
    node2, client2 = make_dummy_node(cache, is_final=True)
    out = await node2({"entity": "REQ-1", "cache_mode": "test"})
    assert client2.chat_completion.call_count == 0
    assert out["out"].value == "ok"


async def test_test_mode_miss_raises(cache):
    state = {"entity": "REQ-1", "cache_mode": "test"}
    node, client = make_dummy_node(cache)
    with pytest.raises(CacheRequiredError):
        await node(state)
    assert client.chat_completion.call_count == 0  # never calls the LLM


async def test_off_mode_writes_but_does_not_read(cache, tmp_path):
    state = {"entity": "REQ-1", "cache_mode": "off"}
    node, client = make_dummy_node(cache)
    await node(state)
    assert list(tmp_path.rglob("*.json"))  # off DOES write (timestamped)

    node2, client2 = make_dummy_node(cache)
    await node2(state)
    assert client2.chat_completion.call_count == 1  # off never reads → re-runs


async def test_default_mode_is_on(cache):
    # No cache_mode in state → treated as "on" (interim node caches).
    state = {"entity": "REQ-1"}
    node, client = make_dummy_node(cache)
    await node(state)
    node2, client2 = make_dummy_node(cache)
    await node2(state)
    assert client2.chat_completion.call_count == 0


async def test_no_cache_manager_means_no_caching(tmp_path):
    state = {"entity": "REQ-1", "cache_mode": "on"}
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


def _rtm_spec_state(spec_id: str, cache_mode="on"):
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
    names = sorted(p.name for p in (tmp_path / "REQ-1").glob("*.json"))
    assert len(names) == 2
    assert any(n.startswith("singlespecevaluatornode_S1_v7.0.0_") for n in names)
    assert any(n.startswith("singlespecevaluatornode_S2_v7.0.0_") for n in names)


async def test_rtm_per_spec_rerun_hits_cache(cache):
    node, client = _make_rtm_spec_node(cache)
    out1 = await node(_rtm_spec_state("S1"))
    assert client.chat_completion.call_count == 1
    assert isinstance(out1["coverage_analysis"][0], EvaluatedSpec)

    node2, client2 = _make_rtm_spec_node(cache)
    await node2(_rtm_spec_state("S1"))
    assert client2.chat_completion.call_count == 0  # cache hit


async def test_rtm_per_spec_off_mode_reruns_but_writes(cache, tmp_path):
    node, client = _make_rtm_spec_node(cache)
    await node(_rtm_spec_state("S1", cache_mode="off"))
    node2, client2 = _make_rtm_spec_node(cache)
    await node2(_rtm_spec_state("S1", cache_mode="off"))
    assert client2.chat_completion.call_count == 1  # off never reads
    assert list(tmp_path.rglob("*.json"))  # but off DOES write


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


def _tc_spec_state(spec_id: str, cache_mode="on"):
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
    _only_file(tmp_path / "TEST-1", "singlespeccoveragenode_S1_v3.0.0_")

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


def test_rtm_dispatch_default_cache_mode_is_on():
    from qaai.agents.test_suite_reviewer.core import DecomposedRequirement

    req = Requirement(req_id="REQ-1", text="x")
    specs = [DecomposedSpec(spec_id="S1", description="d", acceptance_criteria="a", rationale="r")]
    decomposed = DecomposedRequirement(requirement=req, decomposed_specifications=specs)
    suite = TestSuite(requirement=req, test_cases=[], summary=[])
    sends = rtm_dispatch_coverage({
        "requirement": req, "decomposed_requirement": decomposed, "test_suite": suite,
    })
    assert all(s.arg["cache_mode"] == "on" for s in sends)


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
    sends = rtm_dispatch_coverage({
        "requirement": req, "decomposed_requirement": decomposed,
        "test_suite": suite, "summarized_designs": designs,
    })
    assert len(sends) == 2
    assert all(s.arg["summarized_designs"] == designs for s in sends)

    sends_none = rtm_dispatch_coverage({
        "requirement": req, "decomposed_requirement": decomposed,
        "test_suite": suite,
    })
    assert all(s.arg["summarized_designs"] is None for s in sends_none)


def test_tc_dispatch_propagates_cache_mode():
    req = Requirement(req_id="REQ-1", text="x")
    tc = TestCase(test_id="TEST-1", description="d", in_baseline=True)
    # Decomposition mode fans out one Send per requirement to requirement_pipeline;
    # the dispatcher must thread cache_mode through to each Send payload.
    sends = tc_dispatch_requirement_pipeline({
        "test_case": tc, "requirements": [req], "cache_mode": "off",
    })
    assert len(sends) == 1
    assert sends[0].node == "requirement_pipeline"
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
