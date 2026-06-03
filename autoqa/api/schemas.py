from pydantic import BaseModel, Field


class BaselineRequest(BaseModel):
    """Request body for baseline-driven review endpoints."""
    baseline_id: str = Field(..., description="JAMA baseline ID, e.g. 'BASE-84429'")
    use_cache: bool = Field(
        default=True,
        description=(
            "Reuse cached intermediate results. When enabled the final "
            "assessment is still regenerated fresh (partial caching). Disable "
            "to recompute everything from scratch."
        ),
    )
