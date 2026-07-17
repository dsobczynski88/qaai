"""Unit tests for the actual-vs-predicted diff loader + viewer (no LLM, no network).

Covers the logic that decides *where the reviewer deviated*: per-cell diff detection,
mandatory-vs-advisory tagging, skipped (soft-failed) rows, and the self-contained HTML
render smoke.
"""
import json

import pytest

from qaai.eval.compare import load_comparison, write_compare
from qaai.eval.datasets import synthesize_outputs, write_jsonl
from qaai.eval.harness import _write_prediction_set
from qaai.eval.spec import load_spec

pytestmark = pytest.mark.unit

RTM_SPEC = "eval/specs/test_suite_reviewer.yaml"

# Answer key (the ACTUAL side). Row 0 all-Yes; row 1 has M3=N-A; row 2 all-Yes.
ACTUAL_LABELS = [
    {"Overall_Verdict": "Yes", "M1": "Yes", "M2": "Yes", "M3": "Yes", "M4": "Yes", "M5": "Yes"},
    {"Overall_Verdict": "Yes", "M1": "Yes", "M2": "Yes", "M3": "N-A", "M4": "Yes", "M5": "Yes"},
    {"Overall_Verdict": "Yes", "M1": "Yes", "M2": "Yes", "M3": "Yes", "M4": "Yes", "M5": "Yes"},
]


def _inputs(n):
    return [{"requirement": {"req_id": f"REQ-{i}", "text": f"req {i}"},
             "test_cases": [{"test_id": f"TC-{i}", "description": "d"}]} for i in range(n)]


def _build_run(tmp_path, spec, pred_outputs):
    """Write a parent answer-key dataset + a predictions/<ts>/ folder; return the pred dir."""
    ds_dir = tmp_path / "dataset"
    ds_dir.mkdir()
    write_jsonl(ds_dir / "actual_inputs.jsonl", _inputs(len(ACTUAL_LABELS)))
    write_jsonl(ds_dir / "actual_outputs.jsonl", synthesize_outputs(spec, ACTUAL_LABELS))
    write_jsonl(ds_dir / "actual_labels.jsonl", ACTUAL_LABELS)

    n = len(pred_outputs)
    pred_dir = _write_prediction_set(
        ds_dir / "predictions", spec, _inputs(n), pred_outputs,
        {
            "component": "test_suite_reviewer", "spec": "test_suite_reviewer",
            "model": "gpt-test", "prompt_set": "test_suite_reviewer_v3",
            "git_sha": "abc1234", "mlflow_run_id": "run-42",
            "source_inputs_path": str(ds_dir / "actual_inputs.jsonl"),
            "source_outputs_path": str(ds_dir / "actual_outputs.jsonl"),
            "n_records": n,
        },
    )
    return pred_dir


def test_matching_prediction_has_no_diff(tmp_path):
    spec = load_spec(RTM_SPEC)
    pred_outputs = synthesize_outputs(spec, ACTUAL_LABELS)  # predicted == actual
    records = load_comparison(_build_run(tmp_path, spec, pred_outputs))

    assert len(records) == len(ACTUAL_LABELS)
    assert all(r["verdict_match"] for r in records)
    assert all(r["diff"] == [] for r in records)
    # metadata surfaced for the viewer header
    assert records[0]["run_meta"]["mlflow_run_id"] == "run-42"
    assert records[0]["codes"] == ["M1", "M2", "M3", "M4", "M5", "R6"]


def test_flipped_verdict_and_cell_show_up_in_diff(tmp_path):
    spec = load_spec(RTM_SPEC)
    pred_labels = [dict(l) for l in ACTUAL_LABELS]
    pred_labels[1] = {**pred_labels[1], "Overall_Verdict": "No", "M1": "No"}  # flip row 1
    pred_outputs = synthesize_outputs(spec, pred_labels)
    records = load_comparison(_build_run(tmp_path, spec, pred_outputs))

    r0, r1 = records[0], records[1]
    assert r0["verdict_match"] and r0["diff"] == []
    assert not r1["verdict_match"]
    cells = {d["cell"] for d in r1["diff"]}
    assert cells == {"Overall_Verdict", "M1"}
    # a matching cell (M2) must NOT appear in the diff
    assert "M2" not in cells
    # both flipped cells are mandatory
    assert all(d["mandatory"] for d in r1["diff"])


def test_advisory_mismatch_flagged_non_mandatory(tmp_path):
    spec = load_spec(RTM_SPEC)  # R6 is advisory
    # answer key carries R6 on row 0; prediction disagrees on it
    actual = [{**ACTUAL_LABELS[0], "R6": "Yes"}]
    pred = [{**ACTUAL_LABELS[0], "R6": "No"}]
    ds_dir = tmp_path / "dataset"; ds_dir.mkdir()
    write_jsonl(ds_dir / "actual_labels.jsonl", actual)
    write_jsonl(ds_dir / "actual_outputs.jsonl", synthesize_outputs(spec, actual))
    pred_dir = _write_prediction_set(
        ds_dir / "predictions", spec, _inputs(1), synthesize_outputs(spec, pred),
        {"spec": "test_suite_reviewer",
         "source_outputs_path": str(ds_dir / "actual_outputs.jsonl")},
    )
    records = load_comparison(pred_dir)

    r6 = [d for d in records[0]["diff"] if d["cell"] == "R6"]
    assert r6 and r6[0]["mandatory"] is False
    # verdict still matches — an advisory mismatch never flips it
    assert records[0]["verdict_match"]


def test_skipped_prediction_is_marked(tmp_path):
    spec = load_spec(RTM_SPEC)
    pred_outputs = synthesize_outputs(spec, ACTUAL_LABELS)
    pred_outputs[1] = None  # soft-failed row
    records = load_comparison(_build_run(tmp_path, spec, pred_outputs))

    assert records[1]["predicted_skipped"] is True
    assert records[1]["predicted"]["verdict"] is None
    assert records[1]["predicted_output"] is None
    assert not records[1]["verdict_match"]


def test_write_compare_emits_self_contained_html(tmp_path):
    spec = load_spec(RTM_SPEC)
    pred_labels = [dict(l) for l in ACTUAL_LABELS]
    pred_labels[0] = {**pred_labels[0], "Overall_Verdict": "No", "M4": "No"}
    pred_dir = _build_run(tmp_path, spec, synthesize_outputs(spec, pred_labels))

    out = write_compare(pred_dir)
    assert out == pred_dir / "compare.html"
    html = out.read_text(encoding="utf-8")
    assert html
    assert '<script id="DATA"' in html          # data embedded for offline file:// open
    assert "REQ-0" in html                       # entity id rendered into the payload
    assert "http://" not in html and "https://" not in html  # fully self-contained


def test_missing_predicted_outputs_errors_clearly(tmp_path):
    spec = load_spec(RTM_SPEC)
    pred_dir = _build_run(tmp_path, spec, synthesize_outputs(spec, ACTUAL_LABELS))
    (pred_dir / "predicted_outputs.jsonl").unlink()
    with pytest.raises(FileNotFoundError, match="predicted_outputs"):
        load_comparison(pred_dir)


def test_limited_run_aligns_against_full_answer_key(tmp_path):
    """A --limit run predicts fewer rows than the answer key has; alignment must hold."""
    spec = load_spec(RTM_SPEC)
    pred_outputs = synthesize_outputs(spec, ACTUAL_LABELS[:2])  # only 2 of 3
    records = load_comparison(_build_run(tmp_path, spec, pred_outputs))
    assert len(records) == 2
    assert all(r["verdict_match"] for r in records)
