"""BatchedLLMNode must not judge on a truncated batch (no LLM; a stub client).

Observed with gpt-5.4-mini: the summarizer returns ONE summary for a batch of several
test cases. A partial set is the dangerous failure — it looks like a valid result, so
every downstream node judges the requirement having seen a fraction of its evidence and
emits a confident, wrong verdict. Skipping surfaces it as skip_rate instead.
"""
from typing import Any, List

import pytest
from pydantic import BaseModel, RootModel

from qaai.agents.shared.nodes import BatchedLLMNode

pytestmark = pytest.mark.unit


class _Item(BaseModel):
    id: str


class _ItemList(RootModel[List[_Item]]):
    pass


class _StubClient:
    """Returns a fixed JSON body regardless of the prompt."""

    def __init__(self, body: str):
        self.body = body

    async def chat_completion(self, **kwargs) -> Any:
        body = self.body

        class _Msg:
            content = body

        class _Choice:
            message = _Msg()

        class _Result:
            choices = [_Choice()]
            usage = None

        return _Result()


class _Node(BatchedLLMNode):
    BATCH_SIZE = 25

    def _validate_state(self, state) -> bool:
        return True

    def _get_items(self, state) -> list:
        return state["items"]

    def _build_batch_payload(self, state, batch) -> dict:
        return {"items": batch}

    def _unwrap_batch_result(self, parsed) -> list:
        return list(parsed.root)

    def _build_result(self, state, summaries) -> dict:
        return {"summaries": summaries}

    def _get_skip_response(self) -> dict:
        return {}


def _node(body: str, *, strict: bool = True) -> _Node:
    n = _Node(client=_StubClient(body), model="m", system_prompt="s", response_model=_ItemList)
    n.REQUIRE_COMPLETE_BATCH = strict
    return n


async def test_complete_batch_returns_every_summary():
    node = _node('[{"id": "a"}, {"id": "b"}]')
    result = await node({"items": ["a", "b"]})
    assert [i.id for i in result["summaries"]] == ["a", "b"]


async def test_truncated_batch_skips_instead_of_returning_partial():
    """Two items in, one summary back -> skip, not a half-answer."""
    node = _node('[{"id": "a"}]')
    assert await node({"items": ["a", "b"]}) == {}


async def test_over_long_batch_also_skips():
    """A count mismatch in either direction means the response doesn't describe the input."""
    node = _node('[{"id": "a"}, {"id": "b"}, {"id": "c"}]')
    assert await node({"items": ["a", "b"]}) == {}


async def test_opt_out_allows_a_partial_batch():
    """REQUIRE_COMPLETE_BATCH=False restores the old warn-and-continue behaviour."""
    node = _node('[{"id": "a"}]', strict=False)
    result = await node({"items": ["a", "b"]})
    assert [i.id for i in result["summaries"]] == ["a"]


async def test_unparseable_batch_still_skips():
    node = _node("not json at all")
    assert await node({"items": ["a"]}) == {}
