"""
Shared Pydantic models reused across reviewer components
(test_suite_reviewer, test_case_reviewer, hazard_risk_reviewer).
"""

from pydantic import BaseModel, Field
from typing import Optional, List


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