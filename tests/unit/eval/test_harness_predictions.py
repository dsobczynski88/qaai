"""Unit tests for the harness's ground-truth resolution + prediction persistence (no LLM).

These cover the pieces that decide *what gets compared to what* — the place where a bug
produces a plausible-looking accuracy rather than an error.
"""
import json

import pytest

from qaai.eval.datasets import EvalDataset, load_jsonl, synthesize_outputs
from qaai.eval.harness import _ground_truth, _write_prediction_set
from qaai.eval.spec import load_spec

pytestmark = pytest.mark.unit

RTM_SPEC = "eval/specs/test_suite_reviewer.yaml"

LABELS = [
    {"Overall_Verdict": "No", "M1": "Yes", "M2": "No", "M3": "N-A", "M4": "Yes", "M5": "Yes"},
    {"Overall_Verdict": "Yes", "M1": "Yes", "M2": "Yes", "M3": "Yes", "M4": "Yes", "M5": "Yes"},
]


def _dataset(spec, *, with_outputs=True, labels=None):
    labels = LABELS if labels is None else labels
    return EvalDataset(
        labels=labels,
        inputs=[{"requirement": {"req_id": f"R{i}", "text": "t"}} for i in range(len(labels))],
        outputs=synthesize_outputs(spec, LABELS) if with_outputs else [],
    )


def test_ground_truth_prefers_the_answer_key_outputs():
    spec = load_spec(RTM_SPEC)
    labels, source = _ground_truth(spec, _dataset(spec), 2)
    assert source == "actual_outputs"
    assert labels == LABELS


def test_ground_truth_falls_back_to_flat_labels():
    spec = load_spec(RTM_SPEC)
    labels, source = _ground_truth(spec, _dataset(spec, with_outputs=False), 2)
    assert source == "actual_labels"
    assert labels == LABELS


def test_ground_truth_respects_limit():
    spec = load_spec(RTM_SPEC)
    labels, _ = _ground_truth(spec, _dataset(spec), 1)
    assert labels == LABELS[:1]


def test_ground_truth_raises_when_dataset_contradicts_itself():
    """actual_outputs.jsonl and actual_labels.jsonl must agree; drift is not survivable."""
    spec = load_spec(RTM_SPEC)
    drifted = [dict(LABELS[0]), {**LABELS[1], "M4": "No"}]  # flat file disagrees on row 1
    ds = _dataset(spec, labels=drifted)
    with pytest.raises(ValueError, match="row 1"):
        _ground_truth(spec, ds, 2)


def test_ground_truth_tolerates_extra_cells_in_outputs():
    """An R6 present in the outputs but absent from the flat answer key is extra info,
    not a contradiction."""
    spec = load_spec(RTM_SPEC)
    ds = _dataset(spec, labels=[{k: v for k, v in LABELS[0].items()}])
    ds.outputs = synthesize_outputs(spec, [{**LABELS[0], "R6": "No"}])
    labels, source = _ground_truth(spec, ds, 1)
    assert source == "actual_outputs"
    assert labels[0]["R6"] == "No"


def test_write_prediction_set_emits_a_rescorable_dataset(tmp_path):
    spec = load_spec(RTM_SPEC)
    inputs = [{"requirement": {"req_id": f"R{i}", "text": "t"}} for i in range(len(LABELS))]
    outputs = synthesize_outputs(spec, LABELS)
    pred_dir = _write_prediction_set(tmp_path, spec, inputs, outputs, {"model": "m", "n_records": 2})

    assert pred_dir.parent == tmp_path
    # predicted_* names mirror the parent's actual_*, so --mode score reads it with no special-casing.
    assert load_jsonl(pred_dir / "predicted_inputs.jsonl") == inputs
    assert load_jsonl(pred_dir / "predicted_outputs.jsonl") == outputs
    assert load_jsonl(pred_dir / "predicted_labels.jsonl") == LABELS
    assert json.loads((pred_dir / "run_metadata.json").read_text(encoding="utf-8"))["model"] == "m"


def test_write_prediction_set_preserves_row_alignment_for_failures(tmp_path):
    """A None output must round-trip as a null row, keeping row i aligned to input i."""
    spec = load_spec(RTM_SPEC)
    inputs = [{"requirement": {"req_id": "R0", "text": "t"}}, {"requirement": {"req_id": "R1", "text": "t"}}]
    outputs = [None, synthesize_outputs(spec, LABELS)[1]]
    pred_dir = _write_prediction_set(tmp_path, spec, inputs, outputs, {})

    assert (pred_dir / "predicted_outputs.jsonl").read_text(encoding="utf-8").splitlines()[0] == "null"
    rows = load_jsonl(pred_dir / "predicted_labels.jsonl")
    assert len(rows) == 2
    assert rows[0] == {"Overall_Verdict": None}
