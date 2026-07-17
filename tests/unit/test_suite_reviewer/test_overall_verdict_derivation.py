"""SynthesizedAssessment.overall_verdict is derived, not trusted.

The synthesizer emits the per-cell verdicts and the summary verdict in one LLM call, so
they can disagree. Observed on 2/20 records with gpt-5.4-mini: cells `M2=No, M3=No`
returned alongside `overall_verdict=Yes`. A SoP-gating verdict that contradicts the
evidence printed next to it is worse than a wrong cell -- the reviewer reads "Yes" and
stops. Mirrors hazard_risk_reviewer's _FinalAssessorNode._aggregate_verdict.
"""
import pytest

from qaai.agents.shared.core import Requirement
from qaai.agents.test_suite_reviewer.core import MandatoryFinding, SynthesizedAssessment

pytestmark = pytest.mark.unit

_DIMS = {
    "M1": "Functional", "M2": "Negative", "M3": "Boundary",
    "M4": "Spec Coverage", "M5": "Terminology", "R6": "Design Alignment",
}


def _assessment(overall, **cells):
    return SynthesizedAssessment(
        requirement=Requirement(req_id="REQ-1", text="t"),
        overall_verdict=overall,
        mandatory_findings=[
            MandatoryFinding(code=c, dimension=_DIMS[c], verdict=v, rationale="r")
            for c, v in cells.items()
        ],
    )


ALL_YES = {"M1": "Yes", "M2": "Yes", "M3": "Yes", "M4": "Yes", "M5": "Yes"}


def test_verdict_survives_when_it_already_agrees():
    assert _assessment("Yes", **ALL_YES).overall_verdict == "Yes"
    assert _assessment("No", **{**ALL_YES, "M4": "No"}).overall_verdict == "No"


def test_na_cells_do_not_flip_the_verdict():
    """N-A means "not applicable", not a failure."""
    assert _assessment("Yes", **{**ALL_YES, "M2": "N-A", "M3": "N-A"}).overall_verdict == "Yes"


def test_llm_claiming_yes_over_a_failing_cell_is_corrected():
    """The exact pilot contradiction: M2=No, M3=No, yet the LLM summarised 'Yes'."""
    a = _assessment("Yes", **{**ALL_YES, "M2": "No", "M3": "No"})
    assert a.overall_verdict == "No"


def test_llm_claiming_no_over_all_passing_cells_is_corrected():
    """The derivation is symmetric -- it fixes spurious rejections too, which would
    otherwise fail a compliant test suite."""
    assert _assessment("No", **ALL_YES).overall_verdict == "Yes"


@pytest.mark.parametrize("failing", ["M1", "M2", "M3", "M4", "M5"])
def test_any_single_mandatory_no_flips_the_verdict(failing):
    assert _assessment("Yes", **{**ALL_YES, failing: "No"}).overall_verdict == "No"


def test_advisory_r6_never_flips_the_verdict():
    """R6 is recommended only. An R6=No must not gate the SoP verdict."""
    assert _assessment("Yes", **ALL_YES, R6="No").overall_verdict == "Yes"
    assert _assessment("Yes", **ALL_YES, R6="N-A").overall_verdict == "Yes"


def test_r6_alone_cannot_derive_a_verdict():
    """With no mandatory cells there is nothing to derive from, so the stated verdict
    stands rather than being invented from an advisory row."""
    assert _assessment("Yes", R6="No").overall_verdict == "Yes"


def test_empty_findings_leave_the_verdict_untouched():
    a = SynthesizedAssessment(
        requirement=Requirement(req_id="REQ-1", text="t"),
        overall_verdict="Yes",
        mandatory_findings=[],
    )
    assert a.overall_verdict == "Yes"


def test_correction_is_logged(caplog):
    """A silent correction would hide a real synthesizer defect.

    The handler is attached to the target logger directly: setup_logging() sets
    ``propagate = False`` on the ``qaai`` logger (logging_config.py), so records never
    reach caplog's root handler.
    """
    import logging

    target = logging.getLogger("qaai.agents.test_suite_reviewer.core")
    target.addHandler(caplog.handler)
    original_level = target.level
    target.setLevel(logging.WARNING)
    try:
        _assessment("Yes", **{**ALL_YES, "M2": "No"})
    finally:
        target.removeHandler(caplog.handler)
        target.setLevel(original_level)

    assert "contradicts its own mandatory findings" in caplog.text
    assert "REQ-1" in caplog.text
