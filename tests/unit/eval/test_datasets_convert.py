"""Unit tests for dataset loading + converters (no LLM)."""
import json

import pytest

from qaai.eval.datasets import (
    gold_to_eval,
    load_dataset,
    synthesize_outputs,
    write_jsonl,
)
from qaai.eval.spec import load_spec

pytestmark = pytest.mark.unit


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
    write_jsonl(tmp_path / "eval_outputs_labels.jsonl", [{"Overall_Verdict": "Yes"}])
    with pytest.raises(FileNotFoundError):
        load_dataset(tmp_path, mode="score")


def test_load_dataset_run_requires_inputs(tmp_path):
    write_jsonl(tmp_path / "eval_outputs_labels.jsonl", [{"Overall_Verdict": "Yes"}])
    with pytest.raises(FileNotFoundError):
        load_dataset(tmp_path, mode="run")


def test_load_dataset_score_ok(tmp_path):
    write_jsonl(
        tmp_path / "eval_outputs.jsonl",
        [{"synthesized_assessment": {"overall_verdict": "Yes", "mandatory_findings": []}}],
    )
    write_jsonl(tmp_path / "eval_outputs_labels.jsonl", [{"Overall_Verdict": "Yes"}])
    ds = load_dataset(tmp_path, mode="score")
    assert len(ds) == 1
    assert ds.outputs[0]["synthesized_assessment"]["overall_verdict"] == "Yes"
