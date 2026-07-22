"""Unit tests for the hazard final-assessor verdict aggregation.

The overall_verdict is computed deterministically in
``_FinalAssessorNode._aggregate_verdict``: Yes iff every MANDATORY finding
(H1-H6) verdict is in {Yes, N-A}. R7 is a recommended (advisory) criterion and
is excluded — an R7 = No must never flip the verdict.
"""
import pytest

from qaai.agents.hazard_risk_reviewer.core import HazardFinding
from qaai.agents.hazard_risk_reviewer.nodes import _FinalAssessorNode

pytestmark = pytest.mark.unit

# code -> dimension (positional mapping mirroring the HazardDimension literal).
_DIMENSIONS = {
    "H1": "Hazard Record Completeness and Semantic Integrity",
    "H2": "Software Contribution and Cause Coverage",
    "H3": "Pre-Mitigation Risk and Exploitability Characterization",
    "H4": "Risk Control Identification, Allocation, and Coverage",
    "H5": "Verification Depth and Hazard-Path Effectiveness",
    "H6": "Residual Risk Closure and Acceptability Decision",
    "R7": "HSHA Update and Newly Identified Hazard / Hazardous Situation Capture",
}


def _findings(**verdicts: str) -> list[HazardFinding]:
    """Build the 7-cell rubric, defaulting every unspecified code to Yes."""
    return [
        HazardFinding(
            code=code,
            dimension=_DIMENSIONS[code],
            verdict=verdicts.get(code, "Yes"),
            rationale="test",
        )
        for code in _DIMENSIONS
    ]


def test_all_yes_is_yes():
    assert _FinalAssessorNode._aggregate_verdict(_findings()) == "Yes"


def test_r7_no_does_not_flip_verdict():
    """R7 = No with every mandatory dimension passing -> overall stays Yes."""
    assert _FinalAssessorNode._aggregate_verdict(_findings(R7="No")) == "Yes"


def test_mandatory_no_flips_verdict():
    assert _FinalAssessorNode._aggregate_verdict(_findings(H3="No")) == "No"


def test_h5_na_is_allowed():
    assert _FinalAssessorNode._aggregate_verdict(_findings(H5="N-A")) == "Yes"


def test_mandatory_no_overrides_r7_yes():
    assert _FinalAssessorNode._aggregate_verdict(_findings(H1="No", R7="Yes")) == "No"


# --- partial-Yes (Yellow) signal -------------------------------------------------


def test_partial_yes_still_passes_verdict():
    """A partial-Yes has verdict='Yes', so it must not flip overall_verdict."""
    findings = _findings()
    findings[0].partial = True  # H1 partial-Yes
    assert findings[0].verdict == "Yes"
    assert _FinalAssessorNode._aggregate_verdict(findings) == "Yes"


def test_partial_defaults_false():
    """partial defaults to False on every finding when not supplied."""
    assert all(f.partial is False for f in _findings())


def test_partial_alias_coerced_to_yes_plus_flag():
    """An LLM 'Partial' verdict string is rewritten to verdict='Yes', partial=True."""
    f = HazardFinding(
        code="H2",
        dimension=_DIMENSIONS["H2"],
        verdict="Partial",
        rationale="cause coverage is thin",
    )
    assert f.verdict == "Yes"
    assert f.partial is True


def test_partial_alias_across_full_rubric_stays_yes():
    """A rubric whose findings are all partial-Yes (via alias) still aggregates to Yes."""
    findings = [
        HazardFinding(
            code=code,
            dimension=_DIMENSIONS[code],
            verdict="Partial",
            rationale="test",
        )
        for code in _DIMENSIONS
    ]
    assert all(f.verdict == "Yes" and f.partial for f in findings)
    assert _FinalAssessorNode._aggregate_verdict(findings) == "Yes"
