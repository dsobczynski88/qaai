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
    DesignDocument,
)

__all__ = [
    "Requirement",
    "DecomposedSpec",
    "DecomposedRequirement",
    "TestCase",
    "DesignDocument",
    "SummarizedTestCase",
    "SummarizedTestCaseList",
    "SummarizedDesignSpec",
    "SummarizedDesignSpecList",
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
    in_baseline: bool = Field(
        default=False,
        description="True if this test case is in the current baseline under review"
    )


class SummarizedTestCaseList(RootModel[List[SummarizedTestCase]]):
    """Wrapper for v4+ summarizer responses that return only the summary array.
    
    This wrapper enables Pydantic validation for List[SummarizedTestCase] responses
    from the LLM. Uses Pydantic v2's RootModel to wrap the list while maintaining
    full validation capabilities.
    
    The root field is automatically available and provides list-like behavior.
    """
    pass


class SummarizedDesignSpec(BaseModel):
    """Summarized design document for coverage evaluation."""
    doc_id: str = Field(..., description="Design document identifier")
    design_intent: str = Field(
        ..., 
        description="Core design objective or architectural decision"
    )
    implements: str = Field(
        ..., 
        description="What requirement aspects this design addresses"
    )
    key_components: List[str] = Field(
        ..., 
        description="Major components, modules, or interfaces involved"
    )
    verification_hooks: List[str] = Field(
        ..., 
        description="Observable behaviors or interfaces that enable testing"
    )


class SummarizedDesignSpecList(RootModel[List[SummarizedDesignSpec]]):
    """Wrapper for design summarizer responses (Pydantic v2 RootModel)."""
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
            "True if at least one test case in TestSuite covers "
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
    """Single item in the M1-M5 SoP-gating rubric plus R6 recommended criterion.
    
    Note: Despite the name 'MandatoryFinding', this model now includes both
    mandatory (M1-M5) and recommended (R6) findings for backward compatibility.
    Only M1-M5 affect overall_verdict; R6 is advisory only.
    """
    code: Literal["M1", "M2", "M3", "M4", "M5", "R6"]
    dimension: Literal[
        "Functional", "Negative", "Boundary", "Spec Coverage", "Terminology", "Design Alignment"
    ]
    verdict: VerdictNA = Field(
        ...,
        description=(
            "Yes / No / N-A. Only M2, M3, and R6 may be N-A. "
            "M2 N-A: requirement has no validation surface. "
            "M3 N-A: requirement has no threshold/limit surface. "
            "R6 N-A: no design documents exist. "
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
            "mismatches (or say 'aligned'). For R6 describe design alignment or "
            "state 'no design docs' when N-A."
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
            "Yes iff every MANDATORY item (M1-M5) in mandatory_findings has verdict in {Yes, N-A}. "
            "Any single No in M1-M5 flips this to No. R6 (recommended) does NOT affect overall_verdict."
        ),
    )
    mandatory_findings: List[MandatoryFinding] = Field(
        ...,
        description=(
            "Exactly 6 items, in order: M1 Functional, M2 Negative, M3 Boundary, "
            "M4 Spec Coverage, M5 Terminology, R6 Design Alignment. "
            "Note: R6 is recommended only and does NOT affect overall_verdict."
        ),
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
    design_docs: List[DesignDocument]
    decomposed_requirement: Optional[DecomposedRequirement]
    test_suite: Optional[TestSuite]
    summarized_designs: Optional[List[SummarizedDesignSpec]]
    coverage_analysis: Annotated[List[EvaluatedSpec], operator.add]
    synthesized_assessment: Optional[SynthesizedAssessment]
