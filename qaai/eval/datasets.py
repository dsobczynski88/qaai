"""Load and convert the row-aligned three-file eval dataset.

Canonical layout under a dataset directory::

    eval_inputs.jsonl          # graph input rows           (run+score)
    eval_outputs.jsonl         # graph output-state rows    (score-only)
    eval_outputs_labels.jsonl  # flat answer-key rows        (always)

Rows are positionally aligned: row *i* of each file describes the same item.

Converters
    gold_to_eval()        gold_dataset_labeled.jsonl -> eval_inputs + eval_outputs_labels
    synthesize_outputs()  build oracle eval_outputs from labels (offline demo/tests)
    passthrough_outputs() a live run's outputs.jsonl (full state) -> eval_outputs
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from qaai.eval.spec import EvalSpec

INPUTS_NAME = "eval_inputs.jsonl"
OUTPUTS_NAME = "eval_outputs.jsonl"
LABELS_NAME = "eval_outputs_labels.jsonl"


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
    ip = Path(inputs_path) if inputs_path else (d / INPUTS_NAME if d else None)
    op = Path(outputs_path) if outputs_path else (d / OUTPUTS_NAME if d else None)
    lp = Path(labels_path) if labels_path else (d / LABELS_NAME if d else None)

    labels = load_jsonl(lp) if lp and lp.exists() else []
    inputs = load_jsonl(ip) if ip and ip.exists() else []
    outputs = load_jsonl(op) if op and op.exists() else []

    if not labels:
        raise FileNotFoundError(f"labels file not found or empty: {lp}")
    if mode == "score" and not outputs:
        raise FileNotFoundError(
            f"score mode needs {OUTPUTS_NAME} but none found at {op}. "
            f"Run with --mode run to produce them, or point --dataset-dir at a set that has them."
        )
    if mode == "run" and not inputs:
        raise FileNotFoundError(f"run mode needs {INPUTS_NAME} but none found at {ip}.")

    return EvalDataset(
        labels=labels, inputs=inputs, outputs=outputs,
        inputs_path=ip, outputs_path=op, labels_path=lp,
    )


# ── Converters ──────────────────────────────────────────────────────────────

def gold_to_eval(gold_path: Union[str, Path]) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Convert gold_dataset_labeled.jsonl -> (eval_inputs rows, eval_outputs_labels rows).

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
    """Build oracle eval_outputs rows from labels (predictions == labels).

    Used only for the committed sample dataset and offline smoke/CI so score-only mode
    has something to score without invoking the LLM. Real evaluation uses run-produced
    or user-supplied eval_outputs.
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


def passthrough_outputs(outputs_jsonl_path: Union[str, Path]) -> List[Dict[str, Any]]:
    """A live run's outputs.jsonl rows are already valid eval_outputs (state dicts)."""
    return load_jsonl(outputs_jsonl_path)
