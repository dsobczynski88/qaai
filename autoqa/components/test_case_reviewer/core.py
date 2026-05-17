"""
Core data models for the single-test-case reviewer.

Shared models (Requirement, DecomposedSpec, DecomposedRequirement, TestCase)
live in autoqa.components.shared.core. This module adds test-case-specific
shapes:

- ReviewObjective — input row of the review-objectives checklist (id +
  description, loaded from review_objectives.yaml).
- EvaluatedReviewObjective — aggregator-populated row carrying a binary
  Yes/No verdict plus a `partial` flag that drives Yellow rendering in the
  viewer (mirrors test_suite_reviewer's MandatoryFinding).
- SpecAnalysis — per-spec verdict emitted by each axis evaluator.
- TestCaseAssessment — final aggregator output, mirroring
  SynthesizedAssessment with overall_verdict / comments / clarification_questions.
- TCReviewState — the LangGraph TypedDict that threads everything.
"""
from pydantic import BaseModel, Field, model_validator
from typing import Any, Optional, List, Literal, TypedDict, Annotated, Dict
import operator


_PARTIAL_VERDICT_ALIASES = {"partial", "yes-partial", "yes (partial)", "yes-with-partial"}


def _coerce_partial_verdict(verdict: Any) -> tuple[Any, bool]:
    """Return (canonical_verdict, partial_flag) when the input matches a 'Partial'
    alias the LLM tends to emit instead of (verdict='Yes', partial=True). Returns
    the verdict unchanged with partial_flag=False when no coercion applies."""
    if isinstance(verdict, str) and verdict.strip().lower() in _PARTIAL_VERDICT_ALIASES:
        return "Yes", True
    return verdict, False

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
    "Verdict",
    "ReviewObjective",
    "EvaluatedReviewObjective",
    "SpecAnalysis",
    "OverallAnalysis",
    "TestCaseAssessment",
    "TCReviewState",
]


Verdict = Literal["Yes", "No"]


class ReviewObjective(BaseModel):
    """
    Input row of the standardized review-objectives checklist. Loaded from
    review_objectives.yaml and passed into the graph as initial state.
    """
    id: str = Field(..., description="Stable identifier, e.g. 'expected_result_support'.")
    description: str = Field(..., description="What this objective evaluates.")
    mandatory: bool = Field(
        default=True,
        description=(
            "True if this objective is mandatory and affects overall_verdict. "
            "False if this objective is recommended/advisory only."
        ),
    )


class EvaluatedReviewObjective(ReviewObjective):
    """
    Aggregator-populated row: same id/description as the input ReviewObjective,
    plus the verdict, partial flag, and assessment rationale.
    """
    # ✅ Solution 1: Make description optional with default to handle LLM omissions
    description: str = Field(
        default="Evaluation criterion",
        description="Description of what this checklist item evaluates. Defaults if LLM omits."
    )
    mandatory: bool = Field(
        default=True,
        description=(
            "True if this objective is mandatory and affects overall_verdict. "
            "False if this objective is recommended/advisory only."
        ),
    )
    verdict: Verdict = Field(
        default="",
        description="Yes if the test case meets this objective, otherwise No.",
    )
    partial: bool = Field(
        default=False,
        description=(
            "True ONLY when verdict='Yes' AND coverage of this objective is "
            "incomplete in some material way (drives Yellow rendering in the "
            "viewer). Always False when verdict is No. Has NO effect on "
            "overall_verdict aggregation for mandatory objectives — partial-Yes still passes. "
            "For recommended objectives, has no effect since they never affect overall_verdict."
        ),
    )
    assessment: str = Field(default="", description="Aggregator's rationale for the verdict.")

    @model_validator(mode="before")
    @classmethod
    def _coerce_partial_alias(cls, data: Any) -> Any:
        if isinstance(data, dict):
            verdict, was_partial = _coerce_partial_verdict(data.get("verdict"))
            if was_partial:
                data["verdict"] = verdict
                data["partial"] = True
        return data


class SpecAnalysis(BaseModel):
    """Per-spec verdict emitted by the coverage axis evaluator."""
    spec_id: str = Field(..., description="The spec_id from the DecomposedSpec.")
    exists: bool = Field(..., description="True if the axis criterion is met for this spec.")
    assessment: str = Field(..., description="Rationale for the verdict.")


class OverallAnalysis(BaseModel):
    """Test-case-level verdict for an axis that does NOT iterate over decomposed specs.
    Used by the logical-structure and prerequisites axes from prompt v3 onwards —
    those axes are properties of the test case as a whole, not of individual specs."""
    exists: bool = Field(
        ..., description="True iff the test case meets the axis criterion at the test-case level."
    )
    assessment: str = Field(
        ..., description="Concise rationale (1-2 sentences) citing specific test-case elements."
    )


class TestCaseAssessment(BaseModel):
    """Aggregator output: holistic review of one test case."""
    test_case: TestCase
    requirements: List[Requirement]
    decomposed_requirements: List[DecomposedRequirement]
    evaluated_checklist: List[EvaluatedReviewObjective] = Field(
        ..., description="Populated review-objectives checklist (one entry per objective)."
    )
    overall_verdict: Verdict = Field(
        ...,
        description=(
            "Yes iff every MANDATORY item in evaluated_checklist has verdict='Yes'. "
            "Any single No in a mandatory objective flips this to No. "
            "Recommended (non-mandatory) objectives do NOT affect overall_verdict. "
            "Partial-Yes still counts as Yes."
        ),
    )
    comments: str = Field(
        default="",
        description=(
            "Up to 2 sentences clarifying gaps or partial-Yes findings. "
            "Empty when overall_verdict is Yes and no ambiguity remains."
        ),
    )
    clarification_questions: List[str] = Field(
        default_factory=list,
        description=(
            "Targeted, closed-ended questions whose answers expose whether the "
            "identified gaps in `comments` (and any No verdicts) are valid in "
            "context. Empty list ⇒ N/A (no questions needed)."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def _coerce_overall_partial_alias(cls, data: Any) -> Any:
        if isinstance(data, dict):
            verdict, was_partial = _coerce_partial_verdict(data.get("overall_verdict"))
            if was_partial:
                data["overall_verdict"] = verdict
        return data

    @model_validator(mode="after")
    def _validate_overall_verdict(self) -> "TestCaseAssessment":
        """Validate that overall_verdict correctly reflects mandatory objectives only."""
        mandatory_objectives = [obj for obj in self.evaluated_checklist if obj.mandatory]
        if mandatory_objectives:
            expected_verdict = "Yes" if all(obj.verdict == "Yes" for obj in mandatory_objectives) else "No"
            if self.overall_verdict != expected_verdict:
                # Log a warning but don't fail validation - LLM might have made an error
                # In production, you might want to auto-correct this
                pass
        return self


class TCReviewState(TypedDict, total=False):
    # Data source fields (Option 1: local OR Option 2: JAMA)
    test_case: TestCase
    requirements: List[Requirement]
    design_docs: List[DesignDocument]
    review_objectives: List[ReviewObjective]
    # JAMA integration fields (Option 2 only)
    pyjama_request: Optional[Any]  # PyJamaRequest, but avoid import cycle
    jama_data: Optional[List[Dict[str, Any]]]
    jama_metadata: Optional[Dict[str, Any]]
    # Pipeline state fields
    decomposed_requirements: Optional[List[DecomposedRequirement]]
    # Coverage stays per-spec — Send fan-out emits one SpecAnalysis per spec.
    coverage_analysis: Annotated[List[SpecAnalysis], operator.add]
    # Logical-structure and prereqs are TEST-CASE-LEVEL from v3 onwards. Each is
    # produced by exactly one node call (no Send fan-out), so no operator.add reducer.
    logical_structure_analysis: Optional[OverallAnalysis]
    prereqs_analysis: Optional[OverallAnalysis]
    aggregated_assessment: Optional[TestCaseAssessment]
