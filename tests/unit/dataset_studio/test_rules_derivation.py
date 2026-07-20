"""Verdict derivation: three reviewers, three rules, one implementation.

The agreement test at the bottom is the anti-drift binding — it asserts our
spec-driven derivation matches what the live ``SynthesizedAssessment`` validator
computes. If someone changes ``ADVISORY_CODES`` or the N-A pass rule in the reviewer,
that test fails here.
"""

import itertools

import pytest

from qaai.agents.test_suite_reviewer.core import Requirement, SynthesizedAssessment
from qaai.dataset_studio.registry import dataset_type_for, load_type_spec
from qaai.dataset_studio.rules import NA_ALLOWED, derive_overall_verdict

pytestmark = pytest.mark.unit


@pytest.fixture
def rtm_spec():
    return load_type_spec(dataset_type_for("test_suite"))


@pytest.fixture
def hazard_spec():
    return load_type_spec(dataset_type_for("hazard"))


@pytest.fixture
def tc_spec():
    return load_type_spec(dataset_type_for("test_case"))


# ── test_suite (M1-M5 mandatory, R6 advisory) ───────────────────────────────

def test_rtm_all_yes(rtm_spec):
    rubric = {c: "Yes" for c in ["M1", "M2", "M3", "M4", "M5", "R6"]}
    assert derive_overall_verdict("test_suite", rtm_spec, rubric) == "Yes"


def test_rtm_mandatory_no_flips(rtm_spec):
    rubric = {"M1": "Yes", "M2": "No", "M3": "Yes", "M4": "No", "M5": "Yes", "R6": "Yes"}
    assert derive_overall_verdict("test_suite", rtm_spec, rubric) == "No"


def test_rtm_advisory_r6_no_does_not_flip(rtm_spec):
    """R6 is recommended only — a No there must never gate the verdict."""
    rubric = {"M1": "Yes", "M2": "Yes", "M3": "Yes", "M4": "Yes", "M5": "Yes", "R6": "No"}
    assert derive_overall_verdict("test_suite", rtm_spec, rubric) == "Yes"


def test_rtm_na_counts_as_pass(rtm_spec):
    rubric = {"M1": "Yes", "M2": "N-A", "M3": "N-A", "M4": "Yes", "M5": "Yes"}
    assert derive_overall_verdict("test_suite", rtm_spec, rubric) == "Yes"


def test_rtm_no_mandatory_cells_derives_nothing(rtm_spec):
    """Mirrors the live validator's `if not mandatory: return self`."""
    assert derive_overall_verdict("test_suite", rtm_spec, {"R6": "No"}) is None
    assert derive_overall_verdict("test_suite", rtm_spec, {}) is None


# ── hazard (H1-H6 mandatory, R7 advisory) ───────────────────────────────────

def test_hazard_advisory_r7_no_does_not_flip(hazard_spec):
    rubric = {c: "Yes" for c in ["H1", "H2", "H3", "H4", "H5", "H6"]}
    rubric["R7"] = "No"
    assert derive_overall_verdict("hazard", hazard_spec, rubric) == "Yes"


def test_hazard_h5_na_counts_as_pass(hazard_spec):
    rubric = {c: "Yes" for c in ["H1", "H2", "H3", "H4", "H6", "R7"]}
    rubric["H5"] = "N-A"
    assert derive_overall_verdict("hazard", hazard_spec, rubric) == "Yes"


def test_hazard_mandatory_no_flips(hazard_spec):
    rubric = {c: "Yes" for c in ["H1", "H2", "H4", "H5", "H6", "R7"]}
    rubric["H3"] = "No"
    assert derive_overall_verdict("hazard", hazard_spec, rubric) == "No"


def test_hazard_r7_excluded_comes_from_the_spec(hazard_spec):
    """The exclusion must be read from scoring.advisory_codes, not hard-coded."""
    assert hazard_spec.scoring.advisory_codes == ["R7"]
    assert "R7" not in hazard_spec.mandatory_codes


# ── test_case (first four mandatory; setup_clarity advisory) ────────────────

TC_CODES = [
    "expected_result_support",
    "expected_result_spec_align",
    "test_case_achieves",
    "test_case_logical_sequence",
    "test_case_setup_clarity",
]


def test_tc_all_yes(tc_spec):
    assert derive_overall_verdict("test_case", tc_spec, dict.fromkeys(TC_CODES, "Yes")) == "Yes"


def test_tc_single_no_flips(tc_spec):
    rubric = dict.fromkeys(TC_CODES, "Yes")
    rubric["test_case_achieves"] = "No"
    assert derive_overall_verdict("test_case", tc_spec, rubric) == "No"


def test_tc_advisory_setup_clarity_does_not_flip_the_verdict(tc_spec):
    """A sole setup_clarity "No" must still be an overall "Yes".

    This is the exact row the spec and the pipeline used to disagree on: every shipping
    single_test_aggregator prompt (v6/v8/v9) fixes setup_clarity to mandatory=false and
    rolls up mandatory objectives only, so a scorer treating it as mandatory derived
    "No" against a pipeline emitting "Yes" — silently, on every such row.
    """
    rubric = dict.fromkeys(TC_CODES, "Yes")
    rubric["test_case_setup_clarity"] = "No"
    assert derive_overall_verdict("test_case", tc_spec, rubric) == "Yes"


def test_tc_per_row_mandatory_flags_override_the_spec(tc_spec):
    """EvaluatedReviewObjective.mandatory is a per-row data field, so a row may disagree
    with the spec default in either direction and the row wins."""
    rubric = dict.fromkeys(TC_CODES, "Yes")
    rubric["test_case_setup_clarity"] = "No"

    # Promoted by the row: now it gates the verdict.
    promoted = dict.fromkeys(TC_CODES, True)
    assert derive_overall_verdict("test_case", tc_spec, rubric, mandatory_flags=promoted) == "No"

    # Demoted by the row, matching the spec default: it does not.
    demoted = {**promoted, "test_case_setup_clarity": False}
    assert derive_overall_verdict("test_case", tc_spec, rubric, mandatory_flags=demoted) == "Yes"


def test_tc_na_is_not_a_pass(tc_spec):
    """EvaluatedReviewObjective.verdict is Verdict (Yes/No) — N-A has no meaning."""
    assert NA_ALLOWED["test_case"] == frozenset()
    rubric = dict.fromkeys(TC_CODES, "Yes")
    rubric["test_case_achieves"] = "N-A"
    assert derive_overall_verdict("test_case", tc_spec, rubric) == "No"


# ── the anti-drift binding ──────────────────────────────────────────────────

@pytest.mark.parametrize(
    "verdicts",
    list(itertools.product(["Yes", "No", "N-A"], repeat=3)),
)
def test_rtm_derivation_agrees_with_the_live_model_validator(rtm_spec, verdicts):
    """Our spec-driven rule must equal SynthesizedAssessment._derive_overall_verdict.

    Varies M2/M3/R6 (the three N-A-capable cells) across every combination and holds
    M1/M4/M5 at Yes, then compares. The live validator silently rewrites
    overall_verdict, so whatever it lands on is ground truth.
    """
    m2, m3, r6 = verdicts
    cells = {"M1": "Yes", "M2": m2, "M3": m3, "M4": "Yes", "M5": "Yes", "R6": r6}
    dims = {
        "M1": "Functional", "M2": "Negative", "M3": "Boundary",
        "M4": "Spec Coverage", "M5": "Terminology", "R6": "Design Alignment",
    }
    live = SynthesizedAssessment.model_validate({
        "requirement": Requirement(req_id="REQ-1", text="t").model_dump(),
        # Deliberately wrong so the validator must correct it; if it ever stops
        # correcting, this test catches that too.
        "overall_verdict": "Yes",
        "mandatory_findings": [
            {"code": c, "dimension": dims[c], "verdict": v, "rationale": "r"}
            for c, v in cells.items()
        ],
    })
    assert derive_overall_verdict("test_suite", rtm_spec, cells) == live.overall_verdict


def test_na_allowed_matches_the_finding_model_docs(rtm_spec, hazard_spec):
    """Sanity-check the N-A table against the codes each spec actually declares."""
    assert NA_ALLOWED["test_suite"] <= set(rtm_spec.output.rubric.codes)
    assert NA_ALLOWED["hazard"] <= set(hazard_spec.output.rubric.codes)
