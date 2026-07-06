"""Unit tests for predicted-vs-label scoring (deterministic, no LLM)."""
import pytest

from qaai.eval.metrics import flatten_metrics
from qaai.eval.scoring import build_records, compute_metrics
from qaai.eval.spec import EvalSpec

pytestmark = pytest.mark.unit


def make_spec() -> EvalSpec:
    return EvalSpec(
        name="t",
        component="test_suite_reviewer",
        output={
            "verdict_path": "synthesized_assessment.overall_verdict",
            "rubric": {
                "list_path": "synthesized_assessment.mandatory_findings",
                "code_field": "code",
                "verdict_field": "verdict",
                "codes": ["M1", "M2", "M3", "M4", "M5", "R6"],
            },
        },
        labels={"verdict_key": "Overall_Verdict", "rubric_keys": ["M1", "M2", "M3", "M4", "M5", "R6"]},
        scoring={"advisory_codes": ["R6"]},
    )


def _out(overall, cells):
    return {
        "synthesized_assessment": {
            "overall_verdict": overall,
            "mandatory_findings": [{"code": c, "verdict": v} for c, v in cells.items()],
        }
    }


def test_all_correct():
    spec = make_spec()
    cells = {"M1": "Yes", "M2": "N-A", "M3": "N-A", "M4": "Yes", "M5": "Yes"}
    label = {"Overall_Verdict": "Yes", **cells}
    m = compute_metrics(spec, build_records(spec, [_out("Yes", cells)], [label]))
    assert m["overall"]["accuracy"] == 1.0
    assert m["skip_rate"] == 0.0
    assert m["helper_invariant_pass_rate"] == 1.0
    assert m["rubric_macro_f1"] == 1.0


def test_overall_mismatch_scores_zero():
    spec = make_spec()
    m = compute_metrics(
        spec, build_records(spec, [_out("Yes", {"M1": "No"})], [{"Overall_Verdict": "No", "M1": "No"}])
    )
    assert m["overall"]["accuracy"] == 0.0


def test_helper_invariant_flags_self_contradiction():
    spec = make_spec()
    # model says overall Yes but its own M1 = No -> deterministic rule -> No
    cells = {"M1": "No", "M2": "Yes", "M3": "Yes", "M4": "Yes", "M5": "Yes"}
    label = {"Overall_Verdict": "No", **cells}
    m = compute_metrics(spec, build_records(spec, [_out("Yes", cells)], [label]))
    assert m["helper_invariant_pass_rate"] == 0.0


def test_missing_output_counts_as_skip():
    spec = make_spec()
    m = compute_metrics(spec, build_records(spec, [None], [{"Overall_Verdict": "Yes"}]))
    assert m["n_scored"] == 0
    assert m["skip_rate"] == 1.0
    assert "overall" not in m


def test_incomplete_flag_excludes_record():
    spec = make_spec()
    recs = build_records(
        spec, [_out("Yes", {"M1": "Yes"})], [{"Overall_Verdict": "Yes", "M1": "Yes"}], completes=[False]
    )
    m = compute_metrics(spec, recs)
    assert m["n_scored"] == 0


def test_partial_batch_skip_rate():
    spec = make_spec()
    outs = [_out("Yes", {"M1": "Yes"}), None]
    labels = [{"Overall_Verdict": "Yes", "M1": "Yes"}, {"Overall_Verdict": "No"}]
    m = compute_metrics(spec, build_records(spec, outs, labels))
    assert m["n_total"] == 2 and m["n_scored"] == 1
    assert m["skip_rate"] == 0.5


def test_flatten_metrics_keys():
    spec = make_spec()
    cells = {"M1": "Yes", "M2": "N-A", "M3": "N-A", "M4": "Yes", "M5": "Yes"}
    label = {"Overall_Verdict": "Yes", **cells}
    flat = flatten_metrics(compute_metrics(spec, build_records(spec, [_out("Yes", cells)], [label])))
    assert flat["overall_accuracy"] == 1.0
    assert "rubric_accuracy.M1" in flat
    assert "rubric_f1.M5" in flat
    assert "skip_rate" in flat


def test_extraction_from_pydantic_state():
    """Run-mode outputs hold Pydantic models; extraction must read them too."""
    from qaai.agents.test_suite_reviewer.core import MandatoryFinding, SynthesizedAssessment
    from qaai.agents.shared.core import Requirement

    spec = make_spec()
    sa = SynthesizedAssessment(
        requirement=Requirement(req_id="R1", text="t"),
        overall_verdict="Yes",
        mandatory_findings=[
            MandatoryFinding(code="M1", dimension="Functional", verdict="Yes", rationale="ok"),
            MandatoryFinding(code="M2", dimension="Negative", verdict="N-A", rationale="na"),
            MandatoryFinding(code="M3", dimension="Boundary", verdict="N-A", rationale="na"),
            MandatoryFinding(code="M4", dimension="Spec Coverage", verdict="Yes", rationale="ok"),
            MandatoryFinding(code="M5", dimension="Terminology", verdict="Yes", rationale="ok"),
            MandatoryFinding(code="R6", dimension="Design Alignment", verdict="Yes", rationale="ok"),
        ],
    )
    state = {"synthesized_assessment": sa}
    label = {"Overall_Verdict": "Yes", "M1": "Yes", "M2": "N-A", "M3": "N-A", "M4": "Yes", "M5": "Yes"}
    m = compute_metrics(spec, build_records(spec, [state], [label]))
    assert m["overall"]["accuracy"] == 1.0
    assert m["n_scored"] == 1
