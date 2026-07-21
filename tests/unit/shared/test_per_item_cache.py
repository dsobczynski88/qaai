"""Per-item (PER_ITEM_CACHE) caching for BatchedLLMNode.

A batched node can cache each ITEM under its own entity id so the result is
shared across every graph entity that references that item — the concrete case
being a design-doc summary keyed by doc_id and reused across every requirement /
hazard that cites the doc (instead of being re-summarized per requirement).

No live LLM calls — an echo client returns one summary per doc in the batch it
receives and counts how many docs it was asked to summarize, so we can assert
exactly which docs hit the cache vs went to the "LLM".
"""
import json
from typing import Any, List

import pytest
from pydantic import BaseModel, RootModel

from qaai.core.cache import ReviewCacheManager
from qaai.agents.shared.nodes import BatchedLLMNode
from qaai.agents.shared.core import DesignDocument
from qaai.agents.test_suite_reviewer.core import (
    SummarizedDesignSpec,
    SummarizedDesignSpecList,
)
from qaai.agents.test_suite_reviewer.nodes import DesignSummarizerNode

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Minimal per-item node + an echo client that summarizes exactly the batch docs
# ---------------------------------------------------------------------------


class _DocSummary(BaseModel):
    doc_id: str
    text: str = "s"


class _DocSummaryList(RootModel[List[_DocSummary]]):
    pass


class _Doc(BaseModel):
    doc_id: str
    name: str = "n"
    description: str = "d"


class _EchoClient:
    """Returns one summary per design_doc in the user payload; records the doc_ids
    it was asked to summarize on each call so tests can assert cache misses."""

    def __init__(self, drop: set[str] | None = None):
        self.calls = 0
        self.summarized_ids: list[list[str]] = []
        self._drop = drop or set()

    async def chat_completion(self, *, model: str, messages: list, **kwargs) -> Any:
        payload = json.loads(messages[-1]["content"])
        ids = [d["doc_id"] for d in payload["design_docs"]]
        self.calls += 1
        self.summarized_ids.append(ids)
        body = json.dumps(
            [{"doc_id": i, "text": "s"} for i in ids if i not in self._drop]
        )

        class _Msg:
            content = body

        class _Choice:
            message = _Msg()

        class _Usage:
            prompt_tokens = 10
            completion_tokens = 4

        class _Result:
            choices = [_Choice()]
            usage = _Usage()

        return _Result()


class _PerItemNode(BatchedLLMNode):
    BATCH_SIZE = 10
    PER_ITEM_CACHE = True

    def _validate_state(self, state) -> bool:
        return True

    def _get_items(self, state) -> list:
        return state["docs"]

    def _get_item_cache_id(self, item) -> str:
        return item.doc_id

    def _item_cache_prompt_set(self):
        return None

    def _restore_item_from_cache(self, cached: dict) -> _DocSummary:
        return _DocSummary.model_validate(cached["result"])

    def _build_batch_payload(self, state, batch) -> dict:
        return {"design_docs": [{"doc_id": d.doc_id} for d in batch]}

    def _unwrap_batch_result(self, parsed) -> list:
        return list(parsed.root)

    def _build_result(self, state, summaries) -> dict:
        return {"summaries": summaries}

    def _get_skip_response(self) -> dict:
        return {}


def _per_item_node(cache, client):
    return _PerItemNode(
        client=client,
        model="m",
        system_prompt="s",
        response_model=_DocSummaryList,
        cache_manager=cache,
        prompt_version="v1.0.0",
    )


@pytest.fixture
def cache(tmp_path):
    return ReviewCacheManager(cache_dir=tmp_path)


# ---------------------------------------------------------------------------
# 1. A doc summarized once is reused; only new docs hit the LLM
# ---------------------------------------------------------------------------


async def test_shared_doc_summarized_once_across_calls(cache, tmp_path):
    client = _EchoClient()
    node = _per_item_node(cache, client)

    r1 = await node({"docs": [_Doc(doc_id="DD-A"), _Doc(doc_id="DD-B")], "cache_mode": "on"})
    assert sorted(s.doc_id for s in r1["summaries"]) == ["DD-A", "DD-B"]
    assert client.summarized_ids == [["DD-A", "DD-B"]]  # both computed on the cold run

    # Second call shares DD-A, adds DD-C — only DD-C should reach the LLM.
    r2 = await node({"docs": [_Doc(doc_id="DD-A"), _Doc(doc_id="DD-C")], "cache_mode": "on"})
    assert sorted(s.doc_id for s in r2["summaries"]) == ["DD-A", "DD-C"]
    assert client.summarized_ids[-1] == ["DD-C"]  # DD-A came from cache

    # Each doc is cached under its OWN doc_id folder (not a requirement).
    assert (tmp_path / "DD-A").is_dir()
    assert (tmp_path / "DD-B").is_dir()
    assert (tmp_path / "DD-C").is_dir()


async def test_result_order_follows_input_not_cache(cache):
    client = _EchoClient()
    node = _per_item_node(cache, client)
    await node({"docs": [_Doc(doc_id="DD-A")], "cache_mode": "on"})  # warm DD-A

    # DD-A is a hit, DD-B/DD-C are misses; output must stay in input order.
    r = await node(
        {"docs": [_Doc(doc_id="DD-B"), _Doc(doc_id="DD-A"), _Doc(doc_id="DD-C")], "cache_mode": "on"}
    )
    assert [s.doc_id for s in r["summaries"]] == ["DD-B", "DD-A", "DD-C"]


async def test_off_mode_recomputes_but_still_writes_per_doc(cache, tmp_path):
    client = _EchoClient()
    node = _per_item_node(cache, client)
    await node({"docs": [_Doc(doc_id="DD-A")], "cache_mode": "on"})
    # off never reads, so DD-A is recomputed even though it is cached.
    await node({"docs": [_Doc(doc_id="DD-A")], "cache_mode": "off"})
    assert client.summarized_ids == [["DD-A"], ["DD-A"]]
    # ...and off still writes a new timestamped file (append-only history).
    files = list((tmp_path / "DD-A").glob("*.json"))
    assert len(files) == 2


async def test_dropped_item_skips_when_require_complete(cache):
    # The LLM drops DD-B — with REQUIRE_COMPLETE_BATCH the node skips rather than
    # returning a partial summary set.
    client = _EchoClient(drop={"DD-B"})
    node = _per_item_node(cache, client)
    result = await node({"docs": [_Doc(doc_id="DD-A"), _Doc(doc_id="DD-B")], "cache_mode": "on"})
    assert result == {}


# ---------------------------------------------------------------------------
# 2. The real RTM DesignSummarizerNode reuses a doc summary across requirements
# ---------------------------------------------------------------------------


class _DesignEchoClient:
    """Echoes a valid SummarizedDesignSpec per design_doc in the payload."""

    def __init__(self):
        self.calls = 0

    async def chat_completion(self, *, model: str, messages: list, **kwargs) -> Any:
        payload = json.loads(messages[-1]["content"])
        self.calls += 1
        body = json.dumps([
            {
                "doc_id": d["doc_id"],
                "design_intent": "intent",
                "implements": "impl",
                "key_components": ["C"],
                "verification_hooks": ["h"],
            }
            for d in payload["design_docs"]
        ])

        class _Msg:
            content = body

        class _Choice:
            message = _Msg()

        class _Usage:
            prompt_tokens = 10
            completion_tokens = 4

        class _Result:
            choices = [_Choice()]
            usage = _Usage()

        return _Result()


def _make_requirement(req_id: str):
    from qaai.agents.shared.core import Requirement
    return Requirement(req_id=req_id, text="req text")


async def test_design_summary_shared_across_requirements(cache):
    client = _DesignEchoClient()
    node = DesignSummarizerNode(
        client=client,
        model="m",
        response_model=SummarizedDesignSpecList,
        system_prompt="s",
        cache_manager=cache,
        prompt_version="v1.0.0",
    )
    doc = DesignDocument(doc_id="DD-SHARED", name="n", description="d")

    # Requirement REQ-1 summarizes the doc.
    r1 = await node({"requirement": _make_requirement("REQ-1"), "design_docs": [doc], "cache_mode": "on"})
    assert [s.doc_id for s in r1["summarized_designs"]] == ["DD-SHARED"]
    assert client.calls == 1

    # A DIFFERENT requirement (REQ-2) citing the same doc reuses the summary —
    # the doc-keyed cache means no second LLM call.
    r2 = await node({"requirement": _make_requirement("REQ-2"), "design_docs": [doc], "cache_mode": "on"})
    assert [s.doc_id for s in r2["summarized_designs"]] == ["DD-SHARED"]
    assert client.calls == 1  # still 1 — served from the DD-SHARED cache


async def test_design_summary_payload_omits_requirement(cache):
    """The doc-intrinsic payload must not carry the requirement (that is what makes
    the per-doc cache entry shareable)."""
    node = DesignSummarizerNode(
        client=_DesignEchoClient(),
        model="m",
        response_model=SummarizedDesignSpecList,
        system_prompt="s",
        cache_manager=cache,
        prompt_version="v1.0.0",
    )
    doc = DesignDocument(doc_id="DD-A", name="n", description="d")
    payload = node._build_batch_payload({"requirement": _make_requirement("REQ-1")}, [doc])
    assert "requirement" not in payload
    assert payload["design_docs"][0]["doc_id"] == "DD-A"
