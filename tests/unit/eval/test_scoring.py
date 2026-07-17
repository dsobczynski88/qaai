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
        mlflow={
            "metrics_enabled": [
                "overall", "per_rubric", "exact_match", "latency", "cost", "helper_invariant",
            ]
        },
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


# --- Metrics for imbalanced / partially-correct batches -------------------------------


def _always_yes_batch():
    """3 true-Yes + 1 true-No, model predicts Yes every time.

    The majority-guesser: accuracy flatters it at 0.75 while it never once catches a No.
    """
    outs = [_out("Yes", {"M1": "Yes"}) for _ in range(4)]
    labels = [{"Overall_Verdict": "Yes", "M1": "Yes"} for _ in range(3)]
    labels.append({"Overall_Verdict": "No", "M1": "No"})
    return outs, labels


def test_balanced_accuracy_and_kappa_expose_majority_guessing():
    spec = make_spec()
    outs, labels = _always_yes_batch()
    m = compute_metrics(spec, build_records(spec, outs, labels))["overall"]
    # 3 of 4 right...
    assert m["accuracy"] == 0.75
    # ...but recall on No is 0, so the chance-corrected views collapse.
    assert m["balanced_accuracy"] == 0.5      # (3/3 + 0/1) / 2
    assert m["cohen_kappa"] == 0.0            # po == pe == 0.75
    assert m["f1_macro"] == pytest.approx(0.4286, abs=1e-4)  # (0.857 + 0.0) / 2
    assert m["f1"] == pytest.approx(0.8571, abs=1e-4)        # positive-class only


def test_prevalence_counts_report_the_skew():
    spec = make_spec()
    outs, labels = _always_yes_batch()
    m = compute_metrics(spec, build_records(spec, outs, labels))["overall"]
    assert m["prevalence_gt_positive"] == 0.75
    assert m["prevalence_pred_positive"] == 1.0
    assert m["support_positive"] == 3 and m["support_negative"] == 1


def test_per_rubric_support_by_class_and_kappa():
    spec = make_spec()
    outs, labels = _always_yes_batch()
    cell = compute_metrics(spec, build_records(spec, outs, labels))["per_rubric"]["M1"]
    assert cell["support"] == 4
    assert cell["support_by_class"] == {"Yes": 3, "No": 1}
    assert cell["balanced_accuracy"] == 0.5
    assert cell["cohen_kappa"] == 0.0


def test_kappa_omitted_when_undefined():
    """A cell with no class variability has undefined kappa — omit, never log nan."""
    spec = make_spec()
    cells = {"M1": "Yes"}
    m = compute_metrics(spec, build_records(spec, [_out("Yes", cells)], [{"Overall_Verdict": "Yes", **cells}]))
    assert "cohen_kappa" not in m["per_rubric"]["M1"]
    assert "cohen_kappa" not in m["overall"]
    flat = flatten_metrics(m)
    assert "rubric_kappa.M1" not in flat
    assert "overall_cohen_kappa" not in flat


# --- Exact match ----------------------------------------------------------------------


def test_exact_match_ignores_advisory_r6():
    """All mandatory cells right but R6 wrong is still an exact match — R6 is advisory."""
    spec = make_spec()
    pred = {"M1": "Yes", "M2": "Yes", "M3": "Yes", "M4": "Yes", "M5": "Yes", "R6": "No"}
    label = {"Overall_Verdict": "Yes", "M1": "Yes", "M2": "Yes", "M3": "Yes", "M4": "Yes", "M5": "Yes", "R6": "Yes"}
    m = compute_metrics(spec, build_records(spec, [_out("Yes", pred)], [label]))
    assert m["exact_match_rate"] == 1.0
    assert m["per_rubric"]["R6"]["accuracy"] == 0.0  # still scored, just not counted


def test_exact_match_fails_on_single_mandatory_cell():
    spec = make_spec()
    pred = {"M1": "Yes", "M2": "Yes", "M3": "No", "M4": "Yes", "M5": "Yes"}
    label = {"Overall_Verdict": "Yes", "M1": "Yes", "M2": "Yes", "M3": "Yes", "M4": "Yes", "M5": "Yes"}
    m = compute_metrics(spec, build_records(spec, [_out("Yes", pred)], [label]))
    assert m["exact_match_rate"] == 0.0


def test_exact_match_rate_is_row_level():
    """Stricter than per-cell accuracy: one bad cell sinks the whole row."""
    spec = make_spec()
    good = {"M1": "Yes", "M2": "Yes", "M3": "Yes", "M4": "Yes", "M5": "Yes"}
    bad = {**good, "M5": "No"}
    label = {"Overall_Verdict": "Yes", **good}
    m = compute_metrics(spec, build_records(spec, [_out("Yes", good), _out("Yes", bad)], [label, label]))
    assert m["exact_match_rate"] == 0.5
    assert m["exact_match_n"] == 2
    # 9 of 10 cells matched, but only 1 of 2 rows is fully clean.
    assert m["per_rubric"]["M1"]["accuracy"] == 1.0


def test_exact_match_skips_rows_with_no_labelled_cells():
    """An unlabelled row must not count as a free pass."""
    spec = make_spec()
    labels = [{"Overall_Verdict": "Yes"}, {"Overall_Verdict": "Yes", "M1": "Yes"}]
    outs = [_out("Yes", {"M1": "Yes"}), _out("Yes", {"M1": "Yes"})]
    m = compute_metrics(spec, build_records(spec, outs, labels))
    assert m["exact_match_n"] == 1
    assert m["exact_match_rate"] == 1.0


def test_flatten_exposes_new_metric_keys():
    spec = make_spec()
    outs, labels = _always_yes_batch()
    flat = flatten_metrics(compute_metrics(spec, build_records(spec, outs, labels)))
    for key in (
        "overall_f1_macro",
        "overall_balanced_accuracy",
        "overall_cohen_kappa",
        "overall_prevalence_gt_positive",
        "exact_match_rate",
        "rubric_balanced_accuracy.M1",
        "rubric_kappa.M1",
        "rubric_support.M1",
        "rubric_support.M1.Yes",
        "rubric_support.M1.No",
    ):
        assert key in flat, key
    assert flat["rubric_support.M1.No"] == 1.0
    assert all(isinstance(v, float) for v in flat.values())  # MLflow needs numerics


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
