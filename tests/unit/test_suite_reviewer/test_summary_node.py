import json
import pytest
from tests.helpers import make_mock_client, load_jsonl
from autoqa.components.test_suite_reviewer.nodes import SummaryNode
from autoqa.components.test_suite_reviewer.core import TestSuite, TestCase

MOCK_RESPONSE = json.dumps({
    "requirement": {"req_id": "REQ-001", "text": "The system shall display an alert when sensor reading exceeds 100 mg/dL."},
    "test_cases": [
        {
            "test_id": "TC-001",
            "description": "Verify alert fires above threshold",
            "setup": "Sensor connected",
            "steps": "Set reading to 105",
            "expectedResults": "Alert displayed",
        }
    ],
    "summary": [
        {
            "test_case_id": "TC-001",
            "objective": "Verify alert fires when reading exceeds 100 mg/dL",
            "verifies": "REQ-001",
            "protocol": ["Set reading to 105", "Observe UI"],
            "acceptance_criteria": ["Alert displayed within 1s"],
            "is_generated": False,
        }
    ],
})

MARKDOWN_WRAPPED_RESPONSE = f"```json\n{MOCK_RESPONSE}\n```"


@pytest.fixture
def node():
    return SummaryNode(
        client=make_mock_client(MOCK_RESPONSE),
        model="test-model",
        response_model=TestSuite,
        system_prompt="sys",
    )


def test_validate_state_missing_test_cases(node):
    assert node._validate_state({}) is False


def test_validate_state_valid(node, sample_test_cases):
    assert node._validate_state({"test_cases": sample_test_cases}) is True


def test_build_payload(node, sample_test_cases):
    payload = node._build_payload({"test_cases": sample_test_cases})
    assert isinstance(payload, list)
    assert len(payload) == 2
    assert payload[0]["test_id"] == "TC-001"


async def test_call_mock_success(node, sample_test_cases):
    result = await node({"test_cases": sample_test_cases})
    assert "test_suite" in result
    assert isinstance(result["test_suite"], TestSuite)
    assert len(result["test_suite"].summary) == 1


async def test_call_missing_test_cases(node):
    result = await node({})
    assert result == {"test_suite": None}


async def test_call_invalid_json(sample_test_cases):
    bad_node = SummaryNode(
        client=make_mock_client("not json at all"),
        model="test-model",
        response_model=TestSuite,
        system_prompt="sys",
    )
    result = await bad_node({"test_cases": sample_test_cases})
    assert result == {"test_suite": None}


async def test_call_markdown_wrapped(sample_test_cases):
    node = SummaryNode(
        client=make_mock_client(MARKDOWN_WRAPPED_RESPONSE),
        model="test-model",
        response_model=TestSuite,
        system_prompt="sys",
    )
    result = await node({"test_cases": sample_test_cases})
    assert "test_suite" in result
    assert isinstance(result["test_suite"], TestSuite)


_CASES = load_jsonl("summarizer_cases.jsonl")


@pytest.mark.parametrize("case", _CASES, ids=[c["id"] for c in _CASES])
async def test_call_parametrized(case):
    test_cases = [TestCase.model_validate(tc) for tc in case["input_state"]["test_cases"]]
    node = SummaryNode(
        client=make_mock_client(case["mock_response"]),
        model="test-model",
        response_model=TestSuite,
        system_prompt="sys",
    )
    result = await node({"test_cases": test_cases})
    exp = case["expected"]
    if exp["not_null"]:
        assert result["test_suite"] is not None
        assert len(result["test_suite"].summary) == exp["num_summaries"]
    else:
        assert result["test_suite"] is None
