from typing import List, Optional
from pydantic import BaseModel, Field, field_validator
from autoqa.core.constants import MAX_THREAD_ID_LENGTH, MAX_TEST_CASES_PER_REQUIREMENT
from autoqa.components.test_suite_reviewer.core import (
    Requirement,
    TestCase,
    DesignDocument,
    EvaluatedSpec,
    DecomposedRequirement,
    TestSuite,
    SynthesizedAssessment,
)
from autoqa.components.hazard_risk_reviewer.core import (
    HazardAssessment,
    HazardRecord,
    RequirementReview,
)
from autoqa.components.test_case_reviewer.core import (
    ReviewObjective,
    SpecAnalysis,
    OverallAnalysis,
    TestCaseAssessment,
)


class ReviewRequest(BaseModel):
    """Request model for RTM review endpoint.
    
    Attributes:
        thread_id: Unique identifier for the review session (max 100 chars).
        requirement: Software requirement to review.
        test_cases: Associated test cases (max 1000 per request).
        design_docs: Optional design documents for additional context.
    """
    thread_id: str = Field(
        ...,
        min_length=1,
        max_length=MAX_THREAD_ID_LENGTH,
        description="Review session ID (alphanumeric, dash, underscore only)"
    )
    requirement: Requirement
    test_cases: List[TestCase] = Field(
        ...,
        max_length=MAX_TEST_CASES_PER_REQUIREMENT,
        description="Test cases to evaluate against the requirement"
    )
    design_docs: Optional[List[DesignDocument]] = Field(
        default=None,
        description="Optional design documents for additional context"
    )
    
    @field_validator('thread_id')
    @classmethod
    def validate_thread_id(cls, v: str) -> str:
        """Ensure thread_id contains only safe characters.
        
        Args:
            v: The thread_id value to validate.
            
        Returns:
            str: The validated thread_id.
            
        Raises:
            ValueError: If thread_id contains unsafe characters.
        """
        if not v.replace('-', '').replace('_', '').isalnum():
            raise ValueError(
                "thread_id must contain only alphanumeric characters, dashes, and underscores"
            )
        return v


class ReviewResponse(BaseModel):
    status: str
    thread_id: str
    coverage_analysis: List[EvaluatedSpec]
    decomposed_requirement: Optional[DecomposedRequirement] = None
    test_suite: Optional[TestSuite] = None
    synthesized_assessment: Optional[SynthesizedAssessment] = None
    design_docs: List[DesignDocument] = []


class HazardReviewRequest(BaseModel):
    """Request model for hazard review endpoint.
    
    Attributes:
        thread_id: Unique identifier for the review session (max 100 chars).
        hazard: Hazard record with traced requirements, test cases, and design docs.
    """
    thread_id: str = Field(
        ...,
        min_length=1,
        max_length=MAX_THREAD_ID_LENGTH,
        description="Review session ID (alphanumeric, dash, underscore only)"
    )
    hazard: HazardRecord
    
    @field_validator('thread_id')
    @classmethod
    def validate_thread_id(cls, v: str) -> str:
        """Ensure thread_id contains only safe characters.
        
        Args:
            v: The thread_id value to validate.
            
        Returns:
            str: The validated thread_id.
            
        Raises:
            ValueError: If thread_id contains unsafe characters.
        """
        if not v.replace('-', '').replace('_', '').isalnum():
            raise ValueError(
                "thread_id must contain only alphanumeric characters, dashes, and underscores"
            )
        return v


class HazardReviewResponse(BaseModel):
    status: str
    thread_id: str
    hazard: HazardRecord
    hazard_assessment: Optional[HazardAssessment] = None
    requirement_reviews: List[RequirementReview] = []


class TestCaseReviewRequest(BaseModel):
    """Request model for test case review endpoint.
    
    Attributes:
        thread_id: Unique identifier for the review session (max 100 chars).
        test_case: Test case to review.
        requirements: Traced requirements (1 or more).
        review_objectives: Optional custom checklist (defaults to standard 5).
        design_docs: Optional design documents for additional context.
    """
    thread_id: str = Field(
        ...,
        min_length=1,
        max_length=MAX_THREAD_ID_LENGTH,
        description="Review session ID (alphanumeric, dash, underscore only)"
    )
    test_case: TestCase
    requirements: List[Requirement] = Field(
        ...,
        min_length=1,
        description="Requirements traced to this test case (at least one required)"
    )
    review_objectives: Optional[List[ReviewObjective]] = Field(
        default=None,
        description="Custom review checklist (defaults to standard 5 objectives if omitted)"
    )
    design_docs: Optional[List[DesignDocument]] = Field(
        default=None,
        description="Optional design documents for additional context"
    )
    
    @field_validator('thread_id')
    @classmethod
    def validate_thread_id(cls, v: str) -> str:
        """Ensure thread_id contains only safe characters.
        
        Args:
            v: The thread_id value to validate.
            
        Returns:
            str: The validated thread_id.
            
        Raises:
            ValueError: If thread_id contains unsafe characters.
        """
        if not v.replace('-', '').replace('_', '').isalnum():
            raise ValueError(
                "thread_id must contain only alphanumeric characters, dashes, and underscores"
            )
        return v


class TestCaseReviewResponse(BaseModel):
    """Response model for test case review endpoint.
    
    Attributes:
        status: Request lifecycle status ("completed", "failed", etc.).
        thread_id: Echo of the request thread_id for client correlation.
        test_case: The reviewed test case.
        requirements: The traced requirements.
        decomposed_requirements: Atomic specs decomposed from requirements.
        coverage_analysis: Per-spec coverage verdicts.
        logical_structure_analysis: Test-case-level logical flow verdict.
        prereqs_analysis: Test-case-level prerequisites verdict.
        aggregated_assessment: Final assessment with 5-objective checklist.
        design_docs: Design documents provided in the request.
    """
    status: str
    thread_id: str
    test_case: TestCase
    requirements: List[Requirement]
    decomposed_requirements: Optional[List[DecomposedRequirement]] = None
    coverage_analysis: List[SpecAnalysis] = []
    logical_structure_analysis: Optional[OverallAnalysis] = None
    prereqs_analysis: Optional[OverallAnalysis] = None
    aggregated_assessment: Optional[TestCaseAssessment] = None
    design_docs: List[DesignDocument] = []
