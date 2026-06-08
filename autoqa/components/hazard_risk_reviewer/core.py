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
from typing import Annotated, Any, List, Literal, Optional, TypedDict

from pydantic import BaseModel, ConfigDict, Field, RootModel

from autoqa.components.shared.core import (
    DecomposedRequirement,
    Requirement,
    TestCase,
    DesignDocument as SharedDesignDocument,
    Verdict,
    VerdictNA,
)
from autoqa.components.test_suite_reviewer.core import (
    EvaluatedSpec,
    SynthesizedAssessment,
    TestSuite,
)

# Import PyJamaRequest for type hints (optional import)
try:
    from pyjama.langgraph.nodes import PyJamaRequest
except ImportError:
    PyJamaRequest = Any  # type: ignore


__all__ = [
    "HazardRowFromExcel",
    "HazardPackageFromExcel",
    "HazardTraceMatrix",
    "HazardRowWithTraceMatrix",
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
# Same semantics as the shared Verdict / VerdictNA; aliased (not redefined) so the
# hazard-specific names remain available while sharing one source of truth.
HazardVerdict = Verdict
HazardVerdictNA = VerdictNA

# Backward compatibility alias
DesignDocument = SharedDesignDocument


class HazardRowFromExcel(BaseModel):
    """
    Models a single row from the SHA (Software Hazard Analysis) Excel table.
    
    Field names use snake_case, but they are mapped to Excel column headers via aliases.
    The Excel loader strips whitespace from column headers before populating row dictionaries.
    """
    model_config = ConfigDict(populate_by_name=True)
    
    hazard_id: str = Field(
        default="",
        alias='SHA ID Number',
        description="Unique hazard identifier"
    )
    hazardous_situation_id: str = Field(
        default="",
        alias='Hazardous Situation ID'
    )
    hazard: str = Field(
        default="",
        alias='Hazard'
    )
    hazardous_situation: str = Field(
        default="",
        alias='Hazardous Situation'
    )
    function: str = Field(
        default="",
        alias='Function'
    )
    ots_software: str = Field(
        default="",
        alias='OTS Software (if OTS, identify component)',
        description="OTS software component if applicable"
    )
    hazardous_sequence_of_events: str = Field(
        default="",
        alias='Hazardous sequence of events'
    )
    software_related_causes: str = Field(
        default="",
        alias='S/W Related Cause(s)'
    )
    harm: str = Field(
        default="",
        alias='Harm'
    )
    severity: str = Field(
        default="",
        alias='Severity'
    )
    exploitability_pre_mitigation: str = Field(
        default="",
        alias='Exploitability - (Cyber) (Pre-Mitigation)'
    )
    probability_of_harm_pre_mitigation: str = Field(
        default="",
        alias='Probability of Harm (software/Use-Related) (Pre-Mitigation)'
    )
    initial_risk_rating: str = Field(
        default="",
        alias='Initial Risk Rating'
    )
    risk_control_measures: str = Field(
        default="",
        alias='Risk Control Measures'
    )
    demonstration_of_effectiveness: str = Field(
        default="",
        alias='Demonstration of Effectiveness (Trace to Verification)'
    )
    severity_of_harm_post_mitigation: str = Field(
        default="",
        alias='Severity of Harm (Post-Mitigation)'
    )
    exploitability_post_mitigation: str = Field(
        default="",
        alias='Exploitability - (Cyber)'
    )
    probability_of_harm_post_mitigation: str = Field(
        default="",
        alias='Probability of Harm (software/Use-Related)'
    )
    final_risk_rating: str = Field(
        default="",
        alias='Final Risk Rating'
    )
    new_hs_reference: str = Field(
        default="",
        alias='New HS if applicable If yes, reference new row with SHA ID'
    )
    sw_fmea_trace: str = Field(
        default="",
        alias='System DFMEA Trace'
    )
    sra_link: str = Field(
        default="",
        alias='SRA Link'
    )
    urra_item: str = Field(
        default="",
        alias='URRA Item'
    )
    residual_risk_acceptability: str = Field(
        default="",
        alias='Residual Risk Acceptability (Rationale for Acceptability per GQP-10-02, Risk Management Report)'
    )
    row_specific_controls_references: Optional[List[str]] = Field(
        default_factory=list,
        description="List of unique JAMA IDs from the Risk Control Measures column",
    )

class HazardPackageFromExcel(BaseModel):
    rows: List[HazardRowFromExcel]
    all_controls_references: Optional[List[str]]
    

class HazardTraceMatrix(BaseModel):
    """
    Single hazard line item in ISO 14971 / IEC 62304 traceable form.

    String fields mirror the standard hazard register columns. Traced
    artifacts (requirements, test_cases, design_docs) bundle everything the
    pipeline needs to evaluate H1-H7 coverage in a single in-memory object.
    """
    requirements: List[Requirement] = Field(
        default_factory=list,
        description="Requirements traced to this hazard.",
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


class HazardRowWithTraceMatrix(HazardRowFromExcel):
    requirements_traceability: Optional[HazardTraceMatrix] = Field(
        ..., description="The upstream and downstream relationships for all associated controls in row"
    )


class HazardPackage(BaseModel):
    """A list of HazardRecord items — accepted form for batch review."""
    hazards: List[HazardRowWithTraceMatrix]


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
    # Does NOT inherit shared.core.BaseReviewState: this state declares narrower
    # JAMA types (typed PyJamaRequest, jama_data/metadata as List[Any]/Any) and
    # adds pyjama_test_mode, so the fields are kept local by design.
    # Caching control: "off" | "partial" (default) | "full". Threaded from the
    # API/service into every node; see autoqa.core.cache.ReviewCacheManager.
    cache_mode: str
    hazard: HazardRowWithTraceMatrix
    pyjama_request: Optional[PyJamaRequest]
    # Per-run cache-only JAMA override (None ⇒ use the node/config default).
    pyjama_test_mode: Optional[bool]
    jama_data: Optional[List[Any]]
    jama_metadata: Optional[Any]
    requirement_reviews: Annotated[List[RequirementReview], operator.add]
    summarized_designs: Optional[List[HazardSummarizedDesignSpec]]
    summarized_user_needs: Optional[List[HazardSummarizedUserNeed]]
    hazard_findings: Annotated[List[HazardFinding], operator.add]
    hazard_assessment: Optional[HazardAssessment]
