"""
Core data models for RTM review agent (test suite reviewer).

Shared models (Requirement, DecomposedSpec, DecomposedRequirement, TestCase)
live in autoqa.components.shared.core and are re-exported here for
backward compatibility with existing call sites.
"""

from pydantic import BaseModel, Field, RootModel
import operator
from typing import Optional, List, Literal, TypedDict, Annotated

from autoqa.components.shared.core import (
    Requirement,
    DecomposedSpec,
    DecomposedRequirement,
    TestCase,
)

__all__ = [
    "Requirement",
    "DecomposedSpec",
    "DecomposedRequirement",
    "TestCase",
    "SummarizedTestCase",
    "SummarizedTestCaseList",
    "TestSuite",
    "Dimension",
    "Verdict",
    "VerdictNA",
    "CoveringTestCase",
    "EvaluatedSpec",
    "MandatoryFinding",
    "SynthesizedAssessment",
    "RTMReviewState",
]


Dimension = Literal["functional", "negative", "boundary"]
Verdict = Literal["Yes", "No"]
VerdictNA = Literal["Yes", "No", "N-A"]


class SummarizedTestCase(BaseModel):
    test_case_id: str
    objective: str
    verifies: str
    protocol: List[str]
    acceptance_criteria: List[str]
    is_generated: bool = False


class SummarizedTestCaseList(RootModel[List[SummarizedTestCase]]):
    """Wrapper for v4+ summarizer responses that return only the summary array.
    
    This wrapper enables Pydantic validation for List[SummarizedTestCase] responses
    from the LLM. Uses Pydantic v2's RootModel to wrap the list while maintaining
    full validation capabilities.
    
    The root field is automatically available and provides list-like behavior.
    """
    pass


class TestSuite(BaseModel):
    requirement: Requirement
    test_cases: List[TestCase]
    summary: List[SummarizedTestCase]


class CoveringTestCase(BaseModel):
    """A single test case that covers a decomposed spec, with the
    dimension(s) of coverage that TC exercises. A TC may cover multiple
    dimensions of the same spec (e.g. both functional and boundary)."""
    test_case_id: str = Field(..., description="Test case ID from TestSuite.summary")
    dimensions: List[Dimension] = Field(
        ...,
        description=(
            "Dimension(s) of the spec this test case covers. "
            "functional = verifies core positive behavior; "
            "negative = exercises invalid input, error condition, or failure mode; "
            "boundary = probes a threshold, numeric limit, or role/tag transition."
        ),
    )
    rationale: str = Field(
        ..., description="One-line justification for this TC's dimension labeling."
    )


class EvaluatedSpec(BaseModel):
    """Per-spec coverage verdict from an evaluator node."""
    spec_id: str = Field(..., description="The spec_id from the DecomposedSpec")
    covered_exists: bool = Field(
        ...,
        description=(
            "True if at least one non-AI-generated test case in TestSuite covers "
            "any dimension of this spec, otherwise False."
        ),
    )
    covered_by_test_cases: List[CoveringTestCase] = Field(
        ...,
        description=(
            "Test cases from TestSuite.summary that cover this spec, each annotated "
            "with the dimension(s) they exercise and a per-TC V&V rationale. "
            "Empty list when covered_exists is False."
        ),
    )


class MandatoryFinding(BaseModel):
    """Single item in the M1-M5 SoP-gating rubric."""
    code: Literal["M1", "M2", "M3", "M4", "M5"]
    dimension: Literal[
        "Functional", "Negative", "Boundary", "Spec Coverage", "Terminology"
    ]
    verdict: VerdictNA = Field(
        ...,
        description=(
            "Yes / No / N-A. Only M2 and M3 may be N-A (when the requirement has "
            "no validation surface or no threshold/limit surface respectively). "
            "M1, M4, M5 must be Yes or No."
        ),
    )
    partial: bool = Field(
        default=False,
        description=(
            "True ONLY when verdict='Yes' AND coverage of the requirement is "
            "incomplete in some material way (drives Yellow rendering in the "
            "viewer). Always False when verdict is No or N-A. Has NO effect on "
            "overall_verdict aggregation — partial-Yes still passes SoP gating."
        ),
    )
    rationale: str = Field(
        ...,
        description=(
            "One sentence. For M1-M3 cite TC IDs. For M4 list the uncovered "
            "spec_ids (or say 'all covered'). For M5 list specific vocabulary "
            "mismatches (or say 'aligned')."
        ),
    )
    cited_test_case_ids: List[str] = Field(
        default_factory=list,
        description="TC IDs supporting this finding. Required for M1-M3 when verdict=Yes.",
    )
    uncovered_spec_ids: List[str] = Field(
        default_factory=list,
        description="Populated only on M4 when verdict=No — specs with no covering TC.",
    )


class SynthesizedAssessment(BaseModel):
    """Aggregated, SoP-gating coverage rubric for a single requirement."""
    requirement: Requirement
    overall_verdict: Verdict = Field(
        ...,
        description=(
            "Yes iff every item in mandatory_findings has verdict in {Yes, N-A}. "
            "Any single No flips this to No."
        ),
    )
    mandatory_findings: List[MandatoryFinding] = Field(
        ...,
        description="Exactly 5 items, in order: M1 Functional, M2 Negative, M3 Boundary, M4 Spec Coverage, M5 Terminology.",
    )
    comments: str = Field(
        default="",
        description=(
            "Up to 2 sentences clarifying gaps. Empty string when overall_verdict is Yes "
            "and no ambiguity remains."
        ),
    )
    clarification_questions: List[str] = Field(
        default_factory=list,
        description=(
            "Targeted, direct, closed-ended questions whose answers expose whether "
            "the identified gaps in `comments` (and any No mandatory findings) are "
            "valid or applicable in context. Empty list ⇒ N/A (no questions needed)."
        ),
    )


class RTMReviewState(TypedDict, total=False):
    requirement: Requirement
    test_cases: List[TestCase]
    decomposed_requirement: Optional[DecomposedRequirement]
    test_suite: Optional[TestSuite]
    coverage_analysis: Annotated[List[EvaluatedSpec], operator.add]
    synthesized_assessment: Optional[SynthesizedAssessment]
