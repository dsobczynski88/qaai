"""
Integration test to verify backward compatibility of pipelines with data integration layer.

Tests that existing test patterns (local data input) still work after adding
data_integration and transform nodes to the pipelines.
"""
import pytest
from qaai.components.test_suite_reviewer.pipeline import RTMReviewerRunnable
from qaai.components.test_case_reviewer.pipeline import TCReviewerRunnable
from qaai.components.test_suite_reviewer.core import Requirement, TestCase
from qaai.components.test_case_reviewer.core import ReviewObjective


@pytest.mark.integration
async def test_rtm_pipeline_backward_compatibility(real_client, real_model):
    """Test that RTM pipeline works with local data input (no JAMA)."""
    # Create pipeline without pyjama_config (backward compatible)
    graph = RTMReviewerRunnable(
        client=real_client,
        model=real_model,
        model_kwargs={"max_tokens": 4096},
    )
    
    # Local data input (existing pattern)
    requirement = Requirement(
        req_id="REQ-TEST-001",
        text="The system shall validate user input before processing."
    )
    test_cases = [
        TestCase(
            test_id="TC-TEST-001",
            description="Validate input validation",
            setup="User logged in",
            steps="1. Enter invalid data\n2. Submit form",
            expectedResults="Error message displayed"
        )
    ]
    
    # Invoke graph with local data (no pyjama_request)
    result = await graph.graph.ainvoke({
        "requirement": requirement,
        "test_cases": test_cases,
    })
    
    # Verify pipeline ran successfully
    assert result.get("decomposed_requirement") is not None
    assert result.get("test_suite") is not None
    assert result.get("coverage_analysis") is not None
    assert len(result["coverage_analysis"]) > 0
    assert result.get("synthesized_assessment") is not None
    
    # Verify JAMA fields are absent (local mode)
    assert result.get("jama_data") is None
    assert result.get("jama_metadata") is None


@pytest.mark.integration
async def test_tc_pipeline_backward_compatibility(real_client, real_model):
    """Test that TC pipeline works with local data input (no JAMA)."""
    # Create pipeline without pyjama_config (backward compatible)
    graph = TCReviewerRunnable(
        client=real_client,
        model=real_model,
        model_kwargs={"max_tokens": 4096},
    )
    
    # Local data input (existing pattern)
    test_case = TestCase(
        test_id="TC-TEST-001",
        description="Validate input validation",
        setup="User logged in",
        steps="1. Enter invalid data\n2. Submit form",
        expectedResults="Error message displayed"
    )
    requirements = [
        Requirement(
            req_id="REQ-TEST-001",
            text="The system shall validate user input before processing."
        )
    ]
    review_objectives = [
        ReviewObjective(
            id="expected_result_support",
            description="Expected result is supported by requirement"
        ),
        ReviewObjective(
            id="test_case_achieves",
            description="Test case achieves its stated objective"
        ),
    ]
    
    # Invoke graph with local data (no pyjama_request)
    result = await graph.graph.ainvoke({
        "test_case": test_case,
        "requirements": requirements,
        "review_objectives": review_objectives,
    })
    
    # Verify pipeline ran successfully
    assert result.get("decomposed_requirements") is not None
    assert result.get("coverage_analysis") is not None
    assert len(result["coverage_analysis"]) > 0
    assert result.get("aggregated_assessment") is not None
    
    # Verify JAMA fields are absent (local mode)
    assert result.get("jama_data") is None
    assert result.get("jama_metadata") is None
