"""Load and convert the row-aligned three-file eval dataset.

Canonical layout under a dataset directory (the **answer key**, the ACTUAL values)::

    actual_inputs.jsonl   # graph input rows           (run+score)
    actual_outputs.jsonl  # graph output-state rows    (score-only)
    actual_labels.jsonl   # flat answer-key rows        (always)

Rows are positionally aligned: row *i* of each file describes the same item.

A live run additionally writes a timestamped *prediction* set beside the dataset::

    predictions/<ts>/predicted_inputs.jsonl   # the inputs this run scored (self-contained copy)
                    /predicted_outputs.jsonl  # what the graph actually produced
                    /predicted_labels.jsonl   # those outputs, flattened = PREDICTED
                    /run_metadata.json         # provenance tying it to an MLflow run

The distinction that matters: the parent ``actual_outputs.jsonl`` is the **answer key**
(the ACTUAL values); a ``predictions/<ts>/predicted_labels.jsonl`` holds the
**PREDICTED** values from one graph run. Scoring compares the two.

Converters
    gold_to_eval()        gold_dataset_labeled.jsonl -> actual_inputs + actual_labels
    synthesize_outputs()  labels -> oracle actual_outputs (the answer key, in output shape)
    outputs_to_labels()   actual_outputs -> flat labels (the exact inverse of the above)
    passthrough_outputs() a live run's outputs.jsonl (full state) -> actual_outputs
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from qaai.eval.spec import EvalSpec

# Answer-key set (the ACTUAL values) — the committed dataset.
ACTUAL_INPUTS_NAME = "actual_inputs.jsonl"
ACTUAL_OUTPUTS_NAME = "actual_outputs.jsonl"
ACTUAL_LABELS_NAME = "actual_labels.jsonl"
# Prediction set (the PREDICTED values) — one timestamped folder per live run.
PREDICTED_INPUTS_NAME = "predicted_inputs.jsonl"
PREDICTED_OUTPUTS_NAME = "predicted_outputs.jsonl"
PREDICTED_LABELS_NAME = "predicted_labels.jsonl"
PREDICTIONS_DIRNAME = "predictions"
METADATA_NAME = "run_metadata.json"


def load_jsonl(path: Union[str, Path]) -> List[Dict[str, Any]]:
    text = Path(path).read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def write_jsonl(path: Union[str, Path], rows: List[Dict[str, Any]]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")


def file_sha256(path: Union[str, Path]) -> Optional[str]:
    p = Path(path)
    if not p.exists():
        return None
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


@dataclass
class EvalDataset:
    labels: List[Dict[str, Any]]
    inputs: List[Dict[str, Any]]
    outputs: List[Dict[str, Any]]
    inputs_path: Optional[Path] = None
    outputs_path: Optional[Path] = None
    labels_path: Optional[Path] = None

    def __len__(self) -> int:
        return len(self.labels)


def load_dataset(
    dataset_dir: Optional[Union[str, Path]] = None,
    *,
    mode: str = "score",
    inputs_path: Optional[Union[str, Path]] = None,
    outputs_path: Optional[Union[str, Path]] = None,
    labels_path: Optional[Union[str, Path]] = None,
) -> EvalDataset:
    """Load a dataset from a directory or explicit per-file paths.

    ``mode='score'`` requires outputs + labels; ``mode='run'`` requires inputs + labels.
    Missing optional files load as empty lists.
    """
    d = Path(dataset_dir) if dataset_dir else None
    ip = Path(inputs_path) if inputs_path else (d / ACTUAL_INPUTS_NAME if d else None)
    op = Path(outputs_path) if outputs_path else (d / ACTUAL_OUTPUTS_NAME if d else None)
    lp = Path(labels_path) if labels_path else (d / ACTUAL_LABELS_NAME if d else None)

    labels = load_jsonl(lp) if lp and lp.exists() else []
    inputs = load_jsonl(ip) if ip and ip.exists() else []
    outputs = load_jsonl(op) if op and op.exists() else []

    if not labels:
        raise FileNotFoundError(f"labels file not found or empty: {lp}")
    if mode == "score" and not outputs:
        raise FileNotFoundError(
            f"score mode needs {ACTUAL_OUTPUTS_NAME} but none found at {op}. "
            f"Run with --mode run to produce them, or point --dataset-dir at a set that has them."
        )
    if mode == "run" and not inputs:
        raise FileNotFoundError(f"run mode needs {ACTUAL_INPUTS_NAME} but none found at {ip}.")

    return EvalDataset(
        labels=labels, inputs=inputs, outputs=outputs,
        inputs_path=ip, outputs_path=op, labels_path=lp,
    )


# ── Converters ──────────────────────────────────────────────────────────────

def gold_to_eval(gold_path: Union[str, Path]) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Convert gold_dataset_labeled.jsonl -> (actual_inputs rows, actual_labels rows).

    Gold rows look like ``{requirement, test_cases[, design_docs], labels:{Overall_Verdict, M1..}}``.
    Inputs echo everything except ``labels``; labels un-nest the ``labels`` object into a flat row.
    """
    rows = load_jsonl(gold_path)
    inputs: List[Dict[str, Any]] = []
    labels: List[Dict[str, Any]] = []
    for row in rows:
        label_obj = row.get("labels") or {}
        inputs.append({k: v for k, v in row.items() if k != "labels"})
        labels.append(dict(label_obj))
    return inputs, labels


def _set_path(root: Dict[str, Any], dotted: str, value: Any) -> None:
    cur = root
    parts = dotted.split(".")
    for part in parts[:-1]:
        cur = cur.setdefault(part, {})
    cur[parts[-1]] = value


def synthesize_outputs(spec: EvalSpec, labels: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Build oracle actual_outputs rows from labels (predictions == labels).

    Used only for the committed sample dataset and offline smoke/CI so score-only mode
    has something to score without invoking the LLM. Real evaluation uses run-produced
    or user-supplied actual_outputs.
    """
    out_rows: List[Dict[str, Any]] = []
    rub = spec.output.rubric
    keys = spec.labels.rubric_keys or (rub.codes if rub else [])
    for label in labels:
        row: Dict[str, Any] = {}
        _set_path(row, spec.output.verdict_path, label.get(spec.labels.verdict_key))
        if rub:
            findings = [
                {rub.code_field: code, rub.verdict_field: label[code]}
                for code in keys
                if code in label
            ]
            _set_path(row, rub.list_path, findings)
        out_rows.append(row)
    return out_rows


def new_predictions_dir(base: Union[str, Path]) -> Path:
    """Create and return ``<base>/<timestamp>/`` for one run's predictions.

    Uses the same timestamp format and timezone as
    ``qaai.core.logging_config.create_timestamped_run_directory`` (``logs/run-<ts>/``), so
    a prediction set and its run log sort and read alike. Append-only by construction:
    each run gets a new directory, none are overwritten.
    """
    from datetime import datetime

    from qaai.core.logging_config import US_CENTRAL

    d = Path(base) / datetime.now(tz=US_CENTRAL).strftime("%Y-%m-%d_%H-%M-%S")
    d.mkdir(parents=True, exist_ok=True)
    return d


def outputs_to_labels(spec: EvalSpec, outputs: List[Optional[Any]]) -> List[Dict[str, Any]]:
    """Flatten graph output rows into answer-key label rows — the inverse of synthesize_outputs.

    Applied to a live run's outputs this yields the **predicted** values; applied to the
    dataset's own ``actual_outputs.jsonl`` it yields the **actual** values. Same function
    both ways, which is what makes the two sides directly comparable.

    Reads plain dicts and Pydantic graph state alike (via ``spec.extract_prediction``).
    A row that soft-failed is kept as ``{verdict_key: None}`` rather than dropped —
    positional alignment with actual_inputs is the dataset's core invariant, so a missing
    prediction must still occupy its row.

    A rubric code absent from the output is omitted rather than written as None, so this
    round-trips ``synthesize_outputs`` exactly (an answer key with no R6 column produces
    no R6 key).

    ``outputs`` is positionally aligned with the dataset's ``actual_inputs`` rows.
    """
    rub = spec.output.rubric
    keys = spec.labels.rubric_keys or (rub.codes if rub else [])
    rows: List[Dict[str, Any]] = []
    for out in outputs:
        if out is None:
            rows.append({spec.labels.verdict_key: None})
            continue
        verdict, rubric = spec.extract_prediction(out)
        row: Dict[str, Any] = {spec.labels.verdict_key: verdict}
        row.update({code: rubric[code] for code in keys if code in rubric})
        rows.append(row)
    return rows


def passthrough_outputs(outputs_jsonl_path: Union[str, Path]) -> List[Dict[str, Any]]:
    """A live run's outputs.jsonl rows are already valid actual_outputs (state dicts)."""
    return load_jsonl(outputs_jsonl_path)
