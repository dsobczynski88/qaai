"""
Shared Pydantic models reused across reviewer components
(test_suite_reviewer, test_case_reviewer, hazard_risk_reviewer).
"""

from pydantic import BaseModel, Field, model_validator
from typing import Optional, List, Literal, Any, Dict, TypedDict


# Alias keys the aggregator LLM has been observed to (or plausibly may) nest the
# decomposed-spec list under instead of the canonical `decomposed_specifications`.
_SPEC_KEY_ALIASES = ("decomposed_requirements", "decomposed_specs", "specifications", "specs")


# Binary Yes/No verdict shared by every reviewer rubric; the N-A variant is used
# where a dimension can be not-applicable (e.g. test_suite M-findings, hazard H5).
Verdict = Literal["Yes", "No"]
VerdictNA = Literal["Yes", "No", "N-A"]


class BaseReviewState(TypedDict, total=False):
    """Cache-control + JAMA-integration fields common to reviewer graph states.

    Inherited by RTMReviewState and TCReviewState (identical field types). The
    hazard state declares its own narrower types (typed PyJamaRequest, plus a
    pyjama_test_mode override) so it does not inherit this base.
    """
    # Caching control: "off" | "on" (default) | "test". Threaded from the
    # API/service into every node; see qaai.core.cache.ReviewCacheManager.
    cache_mode: str
    # When True the RTM design_summarizer runs and its output feeds spec/synth;
    # threaded per-request (like cache_mode) and folded into the cache key of
    # design-sensitive nodes so the two modes never alias. Default False.
    include_design_summaries: bool
    # JAMA integration fields (Option 2 only)
    pyjama_request: Optional[Any]  # PyJamaRequest, but avoid import cycle
    jama_data: Optional[List[Dict[str, Any]]]
    jama_metadata: Optional[Dict[str, Any]]
    # Input-gate outcome (see qaai.agents.shared.gate). Set to "skipped" with a
    # populated skip_reason / missing_fields when required inputs are absent and
    # the graph short-circuits to END before any LLM call. Read per-record by the
    # viewer to render the missing-fields warning banner.
    review_status: Optional[str]
    skip_reason: Optional[str]
    missing_fields: Optional[List[str]]


class Requirement(BaseModel):
    """Software requirement model."""
    req_id: Optional[str] = None
    text: str


class DecomposedSpec(BaseModel):
    spec_id: str
    description: str
    acceptance_criteria: str
    rationale: str


class DecomposedRequirement(BaseModel):
    requirement: Requirement
    decomposed_specifications: List[DecomposedSpec]

    @model_validator(mode="before")
    @classmethod
    def _coerce_spec_field_name(cls, data: Any) -> Any:
        """Accept the specs list under a wrong key and normalize to
        'decomposed_specifications'. The aggregator LLM (single_test_aggregator v8,
        decomposition mode) intermittently nests specs under 'decomposed_requirements'
        (the parent list's own name), which reads as the required field missing.
        Only re-homes when the canonical key is absent — never clobbers a real value,
        never fabricates."""
        if isinstance(data, dict) and "decomposed_specifications" not in data:
            for alias in _SPEC_KEY_ALIASES:
                if isinstance(data.get(alias), list):
                    data = dict(data)
                    data["decomposed_specifications"] = data.pop(alias)
                    break
        return data


class DesignDocument(BaseModel):
    """Design document linked via traceability."""
    doc_id: str = Field(..., description="Unique design document identifier")
    name: str = Field(..., description="Design document title")
    description: str = Field(..., description="Design document description")


class TestCase(BaseModel):
    test_id: str
    description: str
    setup: Optional[str] = None
    steps: Optional[str] = None
    expectedResults: Optional[str] = None
    in_baseline: bool = Field(
        default=False,
        description="True if this test case is in the current baseline under review"
    )