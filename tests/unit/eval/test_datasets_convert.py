"""Unit tests for dataset loading + converters (no LLM)."""
import json
from pathlib import Path

import pytest

from qaai.eval.datasets import (
    gold_to_eval,
    load_dataset,
    load_jsonl,
    new_predictions_dir,
    outputs_to_labels,
    synthesize_outputs,
    write_jsonl,
)
from qaai.eval.spec import load_spec

pytestmark = pytest.mark.unit

RTM_SPEC = "eval/specs/test_suite_reviewer.yaml"
COMMITTED_DATASET = Path("eval/datasets/test_suite/actual/2026-07-17_12-01-00")


def test_gold_to_eval_roundtrip(tmp_path):
    gold = tmp_path / "gold.jsonl"
    rows = [
        {
            "requirement": {"req_id": "R1", "text": "t"},
            "test_cases": [{"test_id": "TC1", "description": "d"}],
            "labels": {"Overall_Verdict": "No", "M1": "Yes"},
        },
        {
            "requirement": {"req_id": "R2", "text": "t2"},
            "test_cases": [],
            "labels": {"Overall_Verdict": "Yes", "M1": "No"},
        },
    ]
    gold.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")

    inputs, labels = gold_to_eval(gold)
    assert len(inputs) == len(labels) == 2
    assert "labels" not in inputs[0]
    assert inputs[0]["requirement"]["req_id"] == "R1"
    assert labels[0] == {"Overall_Verdict": "No", "M1": "Yes"}


def test_synthesize_outputs_are_scorable(tmp_path):
    spec = load_spec("eval/specs/test_suite_reviewer.yaml")
    labels = [{"Overall_Verdict": "No", "M1": "Yes", "M2": "N-A", "M3": "N-A", "M4": "No", "M5": "Yes"}]
    outs = synthesize_outputs(spec, labels)
    verdict, rubric = spec.extract_prediction(outs[0])
    assert verdict == "No"
    assert rubric["M4"] == "No"


def test_load_dataset_score_requires_outputs(tmp_path):
    write_jsonl(tmp_path / "actual_labels.jsonl", [{"Overall_Verdict": "Yes"}])
    with pytest.raises(FileNotFoundError):
        load_dataset(tmp_path, mode="score")


def test_load_dataset_run_requires_inputs(tmp_path):
    write_jsonl(tmp_path / "actual_labels.jsonl", [{"Overall_Verdict": "Yes"}])
    with pytest.raises(FileNotFoundError):
        load_dataset(tmp_path, mode="run")


def test_load_dataset_score_ok(tmp_path):
    write_jsonl(
        tmp_path / "actual_outputs.jsonl",
        [{"synthesized_assessment": {"overall_verdict": "Yes", "mandatory_findings": []}}],
    )
    write_jsonl(tmp_path / "actual_labels.jsonl", [{"Overall_Verdict": "Yes"}])
    ds = load_dataset(tmp_path, mode="score")
    assert len(ds) == 1
    assert ds.outputs[0]["synthesized_assessment"]["overall_verdict"] == "Yes"


# --- outputs_to_labels: graph outputs -> flat answer-key form -------------------------


def test_outputs_to_labels_inverts_synthesize_outputs():
    spec = load_spec(RTM_SPEC)
    labels = [
        {"Overall_Verdict": "No", "M1": "Yes", "M2": "N-A", "M3": "N-A", "M4": "No", "M5": "Yes"},
        {"Overall_Verdict": "Yes", "M1": "Yes", "M2": "Yes", "M3": "Yes", "M4": "Yes", "M5": "Yes"},
    ]
    assert outputs_to_labels(spec, synthesize_outputs(spec, labels)) == labels


def test_outputs_to_labels_roundtrips_the_committed_dataset():
    """The shipped answer key is its own fixture: outputs -> labels must reproduce it exactly.

    This is what licenses using the same extractor for both sides of the comparison.
    """
    spec = load_spec(RTM_SPEC)
    got = outputs_to_labels(spec, load_jsonl(COMMITTED_DATASET / "actual_outputs.jsonl"))
    assert got == load_jsonl(COMMITTED_DATASET / "actual_labels.jsonl")


def test_outputs_to_labels_keeps_soft_failed_rows_aligned():
    """A failed graph run must still occupy its row, or every later row scores against
    the wrong answer."""
    spec = load_spec(RTM_SPEC)
    good = synthesize_outputs(spec, [{"Overall_Verdict": "Yes", "M1": "Yes"}])[0]
    rows = outputs_to_labels(spec, [None, good, None])
    assert len(rows) == 3
    assert rows[0] == {"Overall_Verdict": None}
    assert rows[1]["Overall_Verdict"] == "Yes"


def test_outputs_to_labels_omits_codes_absent_from_the_output():
    """Absent cells are omitted, not written as None — that is what makes the round-trip exact."""
    spec = load_spec(RTM_SPEC)
    out = synthesize_outputs(spec, [{"Overall_Verdict": "Yes", "M1": "Yes"}])
    assert outputs_to_labels(spec, out) == [{"Overall_Verdict": "Yes", "M1": "Yes"}]


def test_outputs_to_labels_reads_pydantic_state():
    """Run mode hands back graph state holding Pydantic models, not plain dicts."""
    from qaai.agents.shared.core import Requirement
    from qaai.agents.test_suite_reviewer.core import MandatoryFinding, SynthesizedAssessment

    spec = load_spec(RTM_SPEC)
    sa = SynthesizedAssessment(
        requirement=Requirement(req_id="R1", text="t"),
        overall_verdict="No",
        mandatory_findings=[
            MandatoryFinding(code="M1", dimension="Functional", verdict="Yes", rationale="ok"),
            MandatoryFinding(code="M4", dimension="Spec Coverage", verdict="No", rationale="gap"),
        ],
    )
    assert outputs_to_labels(spec, [{"synthesized_assessment": sa}]) == [
        {"Overall_Verdict": "No", "M1": "Yes", "M4": "No"}
    ]


def test_new_predictions_dir_is_append_only(tmp_path):
    """Each run gets its own timestamped dir; existing prediction sets are never clobbered."""
    first = new_predictions_dir(tmp_path)
    (first / "predicted_outputs.jsonl").write_text("{}", encoding="utf-8")
    assert first.exists() and first.parent == tmp_path
    assert new_predictions_dir(tmp_path).exists()
    assert (first / "predicted_outputs.jsonl").read_text(encoding="utf-8") == "{}"
