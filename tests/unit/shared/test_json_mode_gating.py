"""JSON mode must not be requested for nodes whose schema root is an array.

``response_format={"type": "json_object"}`` constrains the model to emit a JSON object.
A ``RootModel[List[...]]`` node needs a top-level array. Asked for both, the model obeys
the API and abandons the schema — returning one bare item, a ``{"summaries": [...]}``
wrapper, or ``{}``. Verified against gpt-5.4-mini: with JSON mode a 3-test-case batch
came back as 1 bare object; without it, a correct 3-element array.
"""
from typing import List

import pytest
from pydantic import BaseModel, RootModel

from qaai.agents.shared.nodes import StandardLLMNode

pytestmark = pytest.mark.unit


class _Item(BaseModel):
    id: str


class _ItemList(RootModel[List[_Item]]):
    pass


class _RecordingClient:
    """Captures the kwargs the node sends, and returns a trivial valid object."""

    def __init__(self):
        self.kwargs = None

    async def chat_completion(self, **kwargs):
        self.kwargs = kwargs

        class _Msg:
            content = '{"id": "a"}'

        class _Choice:
            message = _Msg()

        class _Result:
            choices = [_Choice()]
            usage = None

        return _Result()


class _Node(StandardLLMNode):
    def _validate_state(self, state) -> bool:
        return True

    def _build_payload(self, state) -> dict:
        return {}

    def _format_response(self, parsed) -> dict:
        return {"ok": parsed}


def _node(response_model):
    client = _RecordingClient()
    node = _Node(client=client, model="m", system_prompt="s", response_model=response_model)
    return node, client


async def test_array_root_model_does_not_request_json_object_mode():
    node, client = _node(_ItemList)
    assert node._wants_array_response() is True
    await node._chat_completion([{"role": "user", "content": "x"}])
    assert "response_format" not in client.kwargs


async def test_object_root_model_still_requests_json_object_mode():
    """The JSON-mode protection for large object outputs must stay intact."""
    node, client = _node(_Item)
    assert node._wants_array_response() is False
    await node._chat_completion([{"role": "user", "content": "x"}])
    assert client.kwargs["response_format"] == {"type": "json_object"}


async def test_explicit_model_kwargs_response_format_still_wins():
    node, client = _node(_Item)
    node.model_kwargs = {"response_format": {"type": "text"}}
    await node._chat_completion([{"role": "user", "content": "x"}])
    assert client.kwargs["response_format"] == {"type": "text"}


@pytest.mark.parametrize(
    "model_path, name",
    [
        ("qaai.agents.test_suite_reviewer.core", "SummarizedTestCaseList"),
        ("qaai.agents.test_suite_reviewer.core", "SummarizedDesignSpecList"),
        ("qaai.agents.hazard_risk_reviewer.core", "HazardSummarizedDesignSpecList"),
        ("qaai.agents.hazard_risk_reviewer.core", "HazardSummarizedUserNeedList"),
    ],
)
def test_real_array_response_models_are_detected(model_path, name):
    """Every RootModel[List[...]] the reviewers actually use must be recognised, or it
    silently regresses to returning one item per batch."""
    import importlib

    model = getattr(importlib.import_module(model_path), name)
    assert model.model_json_schema().get("type") == "array"
    node, _ = _node(model)
    assert node._wants_array_response() is True
