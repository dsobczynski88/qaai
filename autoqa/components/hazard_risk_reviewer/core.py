"""Core data models for the hazard risk reviewer.

A HazardRecord bundles a single hazard line item (per ISO 14971 / IEC 62304)
with its traced requirements, test cases, and design documents. The pipeline
evaluates whether the cited requirements + test cases provide reasonable
assurance of safety against the hazard, applying the H1-H7 mandatory rubric
defined by the review-hazard-mitigation-coverage skill.

Verdicts are binary Yes/No (matching test_suite_reviewer's M1-M5 and
test_case_reviewer's checklist conventions); H5 alone may be N-A when the
hazard has no software_related_causes. overall_verdict is Yes iff every
mandatory_findings[i].verdict is in {Yes, N-A}, else No.

HazardAssessment mirrors SynthesizedAssessment from test_suite_reviewer.core:
mandatory findings only, no advisories. Advisory items defined in
the skill are reviewer-applied at review time, not pipeline-generated.

HazardAssessment carries hazard_id (a back-reference) rather than the full
HazardRecord to keep the final assessor's JSON output reliable; the full
hazard is preserved in HazardReviewState["hazard"] and returned alongside the
assessment by the API layer.
"""

import operator
from typing import Annotated, List, Literal, Optional, TypedDict

from pydantic import BaseModel, Field, RootModel

from autoqa.components.shared.core import (
    DecomposedRequirement,
    Requirement,
    TestCase,
    DesignDocument as SharedDesignDocument,
)
from autoqa.components.test_suite_reviewer.core import (
    EvaluatedSpec,
    SynthesizedAssessment,
    TestSuite,
)


__all__ = [
    "HazardRecord",
    "HazardPackage",
    "RequirementReview",
    "HazardSummarizedDesignSpec",
    "HazardSummarizedDesignSpecList",
    "HazardSummarizedUserNeed",
    "HazardSummarizedUserNeedList",
    "HazardCode",
    "HazardDimension",
    "HazardVerdict",
    "HazardVerdictNA",
    "HazardFinding",
    "HazardAssessment",
    "FinalAssessorProse",
    "HazardReviewState",
]


HazardCode = Literal["H1", "H2", "H3", "H4", "H5", "H6", "H7"]
HazardDimension = Literal[
    "Hazard Record Completeness and Semantic Integrity",
    "Software Contribution and Cause Coverage",
    "Pre-Mitigation Risk and Exploitability Characterization",
    "Risk Control Identification, Allocation, and Coverage",
    "Verification Depth and Hazard-Path Effectiveness",
    "Residual Risk Closure and Acceptability Decision",
    "HSHA Update and Newly Identified Hazard / Hazardous Situation Capture",
]
HazardVerdict = Literal["Yes", "No"]
HazardVerdictNA = Literal["Yes", "No", "N-A"]

# Backward compatibility alias
DesignDocument = SharedDesignDocument


class HazardRecord(BaseModel):
    """
    Single hazard line item in ISO 14971 / IEC 62304 traceable form.

    String fields mirror the standard hazard register columns. Traced
    artifacts (requirements, test_cases, design_docs) bundle everything the
    pipeline needs to evaluate H1-H7 coverage in a single in-memory object.
    """
    hazard_id: str = Field(..., description="Unique hazard identifier")
    hazardous_situation_id: str
    hazard: str
    hazardous_situation: str
    function: str
    ots_software: str = Field(..., description="OTS software component if applicable")
    hazardous_sequence_of_events: str
    software_related_causes: str
    harm_severity_rationale: str
    harm: str
    severity: str
    exploitability_pre_mitigation: str
    probability_of_harm_pre_mitigation: str
    initial_risk_rating: str
    risk_control_measures: str
    demonstration_of_effectiveness: str
    severity_of_harm_post_mitigation: str
    exploitability_post_mitigation: str
    probability_of_harm_post_mitigation: str
    final_risk_rating: str
    new_hs_reference: str
    sw_fmea_trace: str
    sra_link: str
    urra_item: str
    residual_risk_acceptability: str
    requirements: List[Requirement] = Field(
        ..., min_length=1,
        description="Requirements traced to this hazard (must include at least one).",
    )
    test_cases: List[TestCase] = Field(default_factory=list)
    design_docs: List[DesignDocument] = Field(default_factory=list)
    user_needs: List[Requirement] = Field(
        default_factory=list,
        description="User needs that trace to this hazard (optional).",
    )
    system_requirements: List[Requirement] = Field(
        default_factory=list,
        description="System-level requirements that trace to this hazard (optional).",
    )


class HazardPackage(BaseModel):
    """A list of HazardRecord items — accepted form for batch review."""
    hazards: List[HazardRecord]


class HazardSummarizedDesignSpec(BaseModel):
    """Summarized design document for hazard mitigation evaluation."""
    doc_id: str = Field(..., description="Design document identifier")
    design_intent: str = Field(
        ..., 
        description="Core design objective or architectural decision"
    )
    hazard_controls: str = Field(
        ..., 
        description="How this design implements risk controls or safety mechanisms"
    )
    key_components: List[str] = Field(
        ..., 
        description="Major components, modules, or interfaces involved"
    )
    verification_hooks: List[str] = Field(
        ..., 
        description="Observable behaviors that enable hazard-path testing"
    )
    failure_modes: List[str] = Field(
        default_factory=list,
        description="Documented failure modes, error conditions, or safety fallbacks"
    )


class HazardSummarizedDesignSpecList(RootModel[List[HazardSummarizedDesignSpec]]):
    """Wrapper for hazard design summarizer responses."""
    pass


class HazardSummarizedUserNeed(BaseModel):
    """Summarized user need for hazard objective evaluation."""
    need_id: str = Field(..., description="User need identifier")
    user_goal: str = Field(
        ..., 
        description="What the user wants to accomplish"
    )
    safety_context: str = Field(
        ..., 
        description="Safety-relevant context or constraints"
    )
    traced_hazards: List[str] = Field(
        default_factory=list,
        description="Hazard IDs this user need traces to"
    )
    acceptance_criteria: List[str] = Field(
        ..., 
        description="Observable criteria that satisfy this user need"
    )


class HazardSummarizedUserNeedList(RootModel[List[HazardSummarizedUserNeed]]):
    """Wrapper for hazard user needs summarizer responses."""
    pass


class RequirementReview(BaseModel):
    """
    Per-requirement evidence collected by invoking test_suite_reviewer for
    each requirement traced from a HazardRecord. Carries the M1-M5
    SynthesizedAssessment plus the pipeline byproducts that contributed to
    it (decomposed specs, summarized TCs, per-spec coverage_analysis).
    """
    requirement: Requirement
    synthesized_assessment: Optional[SynthesizedAssessment] = None
    decomposed_requirement: Optional[DecomposedRequirement] = None
    test_suite: Optional[TestSuite] = None
    coverage_analysis: List[EvaluatedSpec] = Field(default_factory=list)


class HazardFinding(BaseModel):
    """Single item in the H1-H7 SoP-gating rubric."""
    code: HazardCode
    dimension: HazardDimension
    verdict: HazardVerdictNA = Field(
        ...,
        description=(
            "Yes / No / N-A. Only H5 may be N-A (when "
            "software_related_causes indicates no software cause). "
            "H1, H2, H3, H4, H6, H7 must be Yes or No."
        ),
    )
    rationale: str = Field(
        ...,
        description=(
            "One sentence describing the verdict at the hazard level. Cite "
            "specific req_ids / test_ids / FSOE step text where applicable."
        ),
    )
    cited_req_ids: List[str] = Field(
        default_factory=list,
        description="Requirement IDs supporting this finding.",
    )
    cited_test_case_ids: List[str] = Field(
        default_factory=list,
        description="Test case IDs supporting this finding.",
    )
    unblocked_items: List[str] = Field(
        default_factory=list,
        description=(
            "Populated when verdict=No with specific missing/broken elements. "
            "Verbatim quotes from the source fields where applicable."
        ),
    )


class HazardAssessment(BaseModel):
    """Aggregated H1-H7 SoP-gating rubric for a single hazard."""
    hazard_id: str = Field(
        ...,
        description="Back-reference to the HazardRecord this assessment evaluates.",
    )
    overall_verdict: HazardVerdict = Field(
        ...,
        description=(
            "Yes iff every mandatory_findings[i].verdict ∈ {Yes, N-A}. "
            "No otherwise. Computed deterministically by the final_assessor "
            "node, never by the LLM."
        ),
    )
    mandatory_findings: List[HazardFinding] = Field(
        ...,
        description="Exactly 7 items, in order: H1-H7.",
    )
    comments: str = Field(
        default="",
        description=(
            "Up to 2 sentences clarifying gaps. Empty string when "
            "overall_verdict is Yes and no ambiguity remains."
        ),
    )
    clarification_questions: List[str] = Field(
        default_factory=list,
        description=(
            "Targeted, direct, closed-ended questions whose answers expose "
            "whether the identified gaps are valid or N/A in context."
        ),
    )


class FinalAssessorProse(BaseModel):
    """LLM output of the final_assessor node — only the prose fields.

    The deterministic verdict aggregation (mandatory_findings list and
    overall_verdict) is computed in node code from the upstream H1-H7
    findings, not by the LLM. The LLM is only responsible for the
    cross-cutting comments and clarification questions.
    """
    comments: str = Field(default="")
    clarification_questions: List[str] = Field(default_factory=list)


class HazardReviewState(TypedDict, total=False):
    hazard: HazardRecord
    requirement_reviews: Annotated[List[RequirementReview], operator.add]
    summarized_designs: Optional[List[HazardSummarizedDesignSpec]]
    summarized_user_needs: Optional[List[HazardSummarizedUserNeed]]
    hazard_findings: Annotated[List[HazardFinding], operator.add]
    hazard_assessment: Optional[HazardAssessment]
