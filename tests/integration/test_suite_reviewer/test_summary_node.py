import pytest
from autoqa.components.test_suite_reviewer.nodes import make_summarizer_node
from autoqa.components.test_suite_reviewer.core import TestSuite


@pytest.mark.integration
async def test_summary_node_happy_path(real_client, real_model, sample_test_cases):
    node = make_summarizer_node(real_client, real_model)
    result = await node({"test_cases": sample_test_cases})

    assert result["test_suite"] is not None
    assert isinstance(result["test_suite"], TestSuite)
    assert len(result["test_suite"].summary) > 0
