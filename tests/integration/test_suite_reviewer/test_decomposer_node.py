import pytest
from autoqa.components.test_suite_reviewer.nodes import make_decomposer_node
from autoqa.components.test_suite_reviewer.core import DecomposedRequirement


@pytest.mark.integration
async def test_decomposer_node_happy_path(real_client, real_model, sample_requirement):
    node = make_decomposer_node(real_client, real_model)
    result = await node({"requirement": sample_requirement})

    assert result["decomposed_requirement"] is not None
    assert isinstance(result["decomposed_requirement"], DecomposedRequirement)
    assert len(result["decomposed_requirement"].decomposed_specifications) > 0
