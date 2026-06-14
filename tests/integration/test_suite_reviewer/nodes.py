import pytest

from qaai.components.test_suite_reviewer.core import (
    DecomposedRequirement,
    TestSuite,
    EvaluatedSpec,
)
from qaai.components.test_suite_reviewer.nodes import (
    make_decomposer_node,
    make_summarizer_node,
    make_coverage_evaluator,
)

@pytest.mark.integration
async def test_decomposer_node(real_client, real_model, sample_requirement):
    """Test the decomposer node in isolation."""
    node = make_decomposer_node(real_client, real_model, model_kwargs={})
    result = await node({"requirement": sample_requirement})

    assert result["decomposed_requirement"] is not None
    assert isinstance(result["decomposed_requirement"], DecomposedRequirement)
    assert len(result["decomposed_requirement"].decomposed_specifications) > 0

    print(f"\n[decomposer] {len(result['decomposed_requirement'].decomposed_specifications)} specs generated")
    for s in result["decomposed_requirement"].decomposed_specifications:
        print(f"  {s.spec_id}: {s.description[:60]}")


@pytest.mark.integration
async def test_summarizer_node(real_client, real_model, sample_requirement, sample_test_cases):
    """Test the summarizer node in isolation."""
    node = make_summarizer_node(real_client, real_model, model_kwargs={})
    result = await node({
        "requirement": sample_requirement,
        "test_cases": sample_test_cases,
    })

    assert result["test_suite"] is not None
    assert isinstance(result["test_suite"], TestSuite)
    assert len(result["test_suite"].summary) > 0

    print(f"\n[summarizer] {len(result['test_suite'].summary)} summaries produced")


@pytest.mark.integration
async def test_coverage_evaluator_node(
    real_client, real_model,
    sample_requirement, sample_decomposed_requirement, sample_test_suite
):
    """Test the coverage evaluator node in isolation."""
    node = make_coverage_evaluator(real_client, real_model, model_kwargs={})
    result = await node({
        "requirement": sample_requirement,
        "decomposed_requirement": sample_decomposed_requirement,
        "test_suite": sample_test_suite,
    })

    assert len(result["coverage_analysis"]) == len(
        sample_decomposed_requirement.decomposed_specifications
    )
    assert all(isinstance(e, EvaluatedSpec) for e in result["coverage_analysis"])

    print(f"\n[coverage] {len(result['coverage_analysis'])} specs evaluated")
    for e in result["coverage_analysis"]:
        if e.covered_exists:
            assert len(e.covered_by_test_cases) > 0
            for ctc in e.covered_by_test_cases:
                assert ctc.dimensions, "each covering TC must carry ≥1 dimension"
                for d in ctc.dimensions:
                    assert d in {"functional", "negative", "boundary"}
            dims = sorted({d for ctc in e.covered_by_test_cases for d in ctc.dimensions})
            print(f"  {e.spec_id}: covered={e.covered_exists}, dimensions={dims}")
        else:
            assert e.covered_by_test_cases == []
