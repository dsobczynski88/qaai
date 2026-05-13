import json
import pytest
from tests.helpers import make_mock_client, load_jsonl
from autoqa.components.test_suite_reviewer.nodes import DecomposerNode
from autoqa.components.test_suite_reviewer.core import DecomposedRequirement, Requirement

MOCK_RESPONSE = json.dumps({
    "requirement": {"req_id": "REQ-001", "text": "The system shall display an alert when sensor reading exceeds 100 mg/dL."},
    "decomposed_specifications": [
        {
            "spec_id": "S-001",
            "description": "Alert fires when reading exceeds 100 mg/dL",
            "acceptance_criteria": "Alert visible within 1s of threshold breach",
            "rationale": "Primary happy-path behavior",
        }
    ],
})

MARKDOWN_WRAPPED_RESPONSE = f"```json\n{MOCK_RESPONSE}\n```"


@pytest.fixture
def node():
    return DecomposerNode(
        client=make_mock_client(MOCK_RESPONSE),
        model="test-model",
        response_model=DecomposedRequirement,
        system_prompt="sys",
    )


def test_validate_state_missing_requirement(node):
    assert node._validate_state({}) is False


def test_validate_state_valid(node, sample_requirement):
    assert node._validate_state({"requirement": sample_requirement}) is True


def test_build_payload(node, sample_requirement):
    payload = node._build_payload({"requirement": sample_requirement})
    assert payload["requirement_id"] == "REQ-001"
    assert "requirement" in payload


async def test_call_mock_success(node, sample_requirement):
    result = await node({"requirement": sample_requirement})
    assert "decomposed_requirement" in result
    assert isinstance(result["decomposed_requirement"], DecomposedRequirement)
    assert len(result["decomposed_requirement"].decomposed_specifications) == 1


async def test_call_missing_requirement(node):
    result = await node({})
    assert result == {"decomposed_requirement": None}


async def test_call_invalid_json(sample_requirement):
    bad_node = DecomposerNode(
        client=make_mock_client("not json at all"),
        model="test-model",
        response_model=DecomposedRequirement,
        system_prompt="sys",
    )
    result = await bad_node({"requirement": sample_requirement})
    assert result == {"decomposed_requirement": None}


async def test_call_markdown_wrapped(sample_requirement):
    node = DecomposerNode(
        client=make_mock_client(MARKDOWN_WRAPPED_RESPONSE),
        model="test-model",
        response_model=DecomposedRequirement,
        system_prompt="sys",
    )
    result = await node({"requirement": sample_requirement})
    assert "decomposed_requirement" in result
    assert isinstance(result["decomposed_requirement"], DecomposedRequirement)


_CASES = load_jsonl("decomposer_cases.jsonl")


@pytest.mark.parametrize("case", _CASES, ids=[c["id"] for c in _CASES])
async def test_call_parametrized(case):
    req = Requirement.model_validate(case["input_state"]["requirement"])
    node = DecomposerNode(
        client=make_mock_client(case["mock_response"]),
        model="test-model",
        response_model=DecomposedRequirement,
        system_prompt="sys",
    )
    result = await node({"requirement": req})
    exp = case["expected"]
    if exp["not_null"]:
        assert result["decomposed_requirement"] is not None
        assert len(result["decomposed_requirement"].decomposed_specifications) == exp["num_specs"]
    else:
        assert result["decomposed_requirement"] is None
