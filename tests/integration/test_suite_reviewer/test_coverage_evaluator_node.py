import pytest
from autoqa.components.test_suite_reviewer.nodes import make_coverage_evaluator
from autoqa.components.test_suite_reviewer.core import EvaluatedSpec


@pytest.mark.integration
async def test_coverage_evaluator_node_happy_path(
    real_client, real_model,
    sample_requirement, sample_decomposed_requirement, sample_test_suite
):
    node = make_coverage_evaluator(real_client, real_model)
    result = await node({
        "requirement": sample_requirement,
        "decomposed_requirement": sample_decomposed_requirement,
        "test_suite": sample_test_suite,
    })

    assert len(result["coverage_analysis"]) == len(
        sample_decomposed_requirement.decomposed_specifications
    )
    assert all(isinstance(e, EvaluatedSpec) for e in result["coverage_analysis"])
    for e in result["coverage_analysis"]:
        if e.covered_exists:
            assert len(e.covered_by_test_cases) > 0
            for ctc in e.covered_by_test_cases:
                assert ctc.dimensions, "each covering TC must carry ≥1 dimension"
                for d in ctc.dimensions:
                    assert d in {"functional", "negative", "boundary"}
        else:
            assert e.covered_by_test_cases == []
