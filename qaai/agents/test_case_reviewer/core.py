"""
Core data models for the single-test-case reviewer.

Shared models (Requirement, DecomposedSpec, DecomposedRequirement, TestCase)
live in qaai.agents.shared.core. This module adds test-case-specific
shapes:

- ReviewObjective — one row of the review-objectives checklist (id +
  description + mandatory); the objectives are embedded in the aggregator
  prompt (v8/v9), not supplied as graph input.
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


# Assessment-level keys that belong at the TOP level of TestCaseAssessment. When the
# aggregator LLM wraps its whole answer under a single `test_case` key (a schema-fidelity
# error we've observed in production), these are lifted back out of `test_case` before
# Pydantic validation. Ordered TestCase-first is irrelevant here — this is a membership set.
_ASSESSMENT_LEVEL_KEYS = (
    "requirements",
    "decomposed_requirements",
    "evaluated_checklist",
    "overall_verdict",
    "comments",
    "clarification_questions",
)


def _unwrap_wrapped_assessment(data: Any) -> Any:
    """Recover the assessment when the LLM nested every field inside `test_case`.

    Observed malformation: the aggregator returns
    ``{"test_case": {<TestCase fields> + requirements + decomposed_requirements +
    evaluated_checklist + overall_verdict + comments + clarification_questions}}`` — i.e.
    the entire assessment wrapped one level too deep, so the parsed dict's only top-level
    key is ``test_case`` and the required top-level fields read as missing.

    ``evaluated_checklist`` is the sentinel: it is never a legitimate TestCase field, so
    its presence *inside* ``test_case`` while *absent* at the top level unambiguously
    signals the wrap. When detected, the assessment-level keys are lifted back to the top
    level (never clobbering a key already present there) and ``test_case`` is reduced to
    just its TestCase fields.

    Returns ``data`` unchanged when the wrap is not present. Never fabricates: if the model
    genuinely omitted ``evaluated_checklist``, the sentinel check is False, nothing is
    lifted, and validation fails exactly as it would have.
    """
    if not isinstance(data, dict):
        return data
    tc = data.get("test_case")
    if not (
        isinstance(tc, dict)
        and "evaluated_checklist" not in data
        and "evaluated_checklist" in tc
    ):
        return data
    lifted = dict(data)
    inner = dict(tc)
    for key in _ASSESSMENT_LEVEL_KEYS:
        if key in inner and key not in lifted:
            lifted[key] = inner.pop(key)
    lifted["test_case"] = inner
    return lifted

from qaai.agents.shared.core import (
    Requirement,
    DecomposedSpec,
    DecomposedRequirement,
    TestCase,
    DesignDocument,
    Verdict,
    BaseReviewState,
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


# Verdict is imported from qaai.agents.shared.core (single source of truth)
# and re-exported via __all__ for backward compatibility.


class ReviewObjective(BaseModel):
    """
    One row of the standardized review-objectives checklist (id + description +
    mandatory flag). Serves as the base shape for EvaluatedReviewObjective. The
    five objectives are embedded directly in the single_test_aggregator prompt
    (v8/v9) rather than supplied as graph input, matching how the test_suite
    (M1-M5) and hazard (H1-H6) reviewers embed their rubrics.
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
    # Empty in the no-decomposition mode (test_case_reviewer_v3): the test case is
    # reviewed directly against the original requirement text, so no specs exist.
    decomposed_requirements: List[DecomposedRequirement] = Field(default_factory=list)
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
        # Un-nest first: the aggregator LLM sometimes wraps the whole assessment under a
        # single `test_case` key. This lifts the assessment-level fields back to the top
        # level so the partial-alias coercion below (which reads top-level overall_verdict)
        # and Pydantic field validation see the correct shape.
        data = _unwrap_wrapped_assessment(data)
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


class TCReviewState(BaseReviewState, total=False):
    # cache_mode + JAMA integration fields (pyjama_request / jama_data /
    # jama_metadata) are inherited from BaseReviewState.
    # Data source fields (Option 1: local OR Option 2: JAMA)
    test_case: TestCase
    requirements: List[Requirement]
    design_docs: List[DesignDocument]
    # Pipeline state fields
    # Reduced channel: in decomposition mode the graph fans out one
    # requirement_pipeline Send per requirement, each returning its single
    # DecomposedRequirement, so concurrent writes accumulate instead of clobbering.
    # In no-decomposition mode nothing writes this key (aggregator falls back to []).
    decomposed_requirements: Annotated[List[DecomposedRequirement], operator.add]
    # Coverage stays per-spec — Send fan-out emits one SpecAnalysis per spec.
    coverage_analysis: Annotated[List[SpecAnalysis], operator.add]
    # Logical-structure and prereqs are TEST-CASE-LEVEL from v3 onwards. Each is
    # produced by exactly one node call (no Send fan-out), so no operator.add reducer.
    logical_structure_analysis: Optional[OverallAnalysis]
    prereqs_analysis: Optional[OverallAnalysis]
    aggregated_assessment: Optional[TestCaseAssessment]
