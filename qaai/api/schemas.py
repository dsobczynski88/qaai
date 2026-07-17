from typing import Literal, Optional

from pydantic import BaseModel, Field


class BaselineRequest(BaseModel):
    """Request body for baseline-driven review endpoints."""
    baseline_id: str = Field(..., description="JAMA baseline ID, e.g. 'BASE-84429'")
    cache_mode: Optional[Literal["off", "on", "test", "partial", "full"]] = Field(
        default=None,
        description=(
            "Explicit cache mode (the UI radio toggle). 'off' = never read; always "
            "re-run every node and save a new timestamped result. 'on' = reuse the "
            "newest cached interim results and only re-run the final assessment "
            "fresh. 'test' = recreate the report entirely from cache with no LLM "
            "calls (fails if any node result is missing). When omitted, falls back "
            "to the legacy 'use_cache' boolean. Legacy 'partial'/'full' are "
            "accepted and map to 'on'/'test'."
        ),
    )
    use_cache: bool = Field(
        default=True,
        description=(
            "Deprecated, kept for backward compatibility. Ignored when 'cache_mode' "
            "is set. True maps to 'on', False to 'off'."
        ),
    )
    test_mode: Optional[bool] = Field(
        default=None,
        description=(
            "Cache-only JAMA: when True, fetch from the disk cache only and make "
            "no live JAMA API calls (invalid/mock credentials are tolerated). "
            "None falls back to the server default (PYJAMA_TEST_MODE)."
        ),
    )
    include_edge_case_analysis: bool = Field(
        default=False,
        description=(
            "When True, use the edge-case prompt set (test_suite_reviewer_v4, "
            "edge-case decomposer v6) for the test-suite review; when False use "
            "the baseline set (test_suite_reviewer_v3). Cached results are "
            "namespaced by prompt set so the two never alias."
        ),
    )
    include_decomposition_analysis: bool = Field(
        default=True,
        description=(
            "Test-case reviewer only. When True (default), decompose each "
            "requirement into specs and review coverage per spec "
            "(test_case_reviewer_v2). When False, skip decomposition and review "
            "the test case directly against the original requirement text "
            "(test_case_reviewer_v3) — faster, coarser. Ignored by other endpoints."
        ),
    )
    include_design_summaries: bool = Field(
        default=False,
        description=(
            "Test-suite reviewer only. When True, run the design_summarizer node "
            "so design context feeds per-spec coverage and synthesis; when False "
            "(default) that branch is skipped in the graph. Cached results for "
            "design-sensitive nodes are keyed by this flag (ds0/ds1) so toggling "
            "never reads back a result computed under the other mode."
        ),
    )
    baseline_review_type: Literal["requirements", "tests"] = Field(
        default="tests",
        description=(
            "Test-suite reviewer only. Which kind of baseline this is: 'tests' "
            "(baseline items are test cases, traced upstream to their requirements "
            "— the original behavior, request_type='test_suite_review') or "
            "'requirements' (baseline items are requirement ids directly, fetched "
            "via request_type='requirement_review'). Both produce the identical "
            "per-requirement structure, so only the JAMA fetch differs."
        ),
    )
