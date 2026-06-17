from typing import Literal, Optional

from pydantic import BaseModel, Field


class BaselineRequest(BaseModel):
    """Request body for baseline-driven review endpoints."""
    baseline_id: str = Field(..., description="JAMA baseline ID, e.g. 'BASE-84429'")
    cache_mode: Optional[Literal["off", "partial", "full"]] = Field(
        default=None,
        description=(
            "Explicit cache mode (the UI radio toggle). 'off' = no cache read or "
            "write; 'partial' = reuse cached interim analysis but regenerate the "
            "final assessment fresh (Use results to update cache); 'full' = reuse "
            "everything including the final assessment (Use cached results). When "
            "omitted, falls back to the legacy 'use_cache' boolean."
        ),
    )
    use_cache: bool = Field(
        default=True,
        description=(
            "Deprecated, kept for backward compatibility. Ignored when 'cache_mode' "
            "is set. True maps to 'partial', False to 'off'."
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
