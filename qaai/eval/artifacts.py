"""Write per-run evaluation artifacts (the Langfuse-like inspection surface).

Everything here lands in the MLflow run's artifact directory:
    predictions.jsonl     per-record gt/pred + rubric + latency (audit trail)
    failures.jsonl        subset where overall_match is False (quick regression set)
    per_rubric.csv        rubric_code, accuracy, f1, support
    confusion_matrix.png  overall-verdict confusion matrix
    prompt_versions.json   prompt-set provenance (role -> version + sha256)
    fixture_metadata.json  dataset identity (paths, sha256, sizes, label distribution)
"""
from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

from qaai.eval.scoring import RecordResult
from qaai.eval.spec import EvalSpec


def _json_default(obj: Any) -> Any:
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    return str(obj)


def write_predictions(run_dir: Path, records: List[RecordResult]) -> Path:
    path = run_dir / "predictions.jsonl"
    path.write_text(
        "\n".join(json.dumps(r.to_json(), default=_json_default) for r in records) + "\n",
        encoding="utf-8",
    )
    return path


def write_failures(run_dir: Path, records: List[RecordResult]) -> Path:
    fails = [r for r in records if r.scored and r.overall_match is False]
    path = run_dir / "failures.jsonl"
    path.write_text(
        "\n".join(json.dumps(r.to_json(), default=_json_default) for r in fails) + "\n",
        encoding="utf-8",
    )
    return path


def write_per_rubric_csv(run_dir: Path, nested_metrics: Dict[str, Any]) -> Optional[Path]:
    per_rubric = nested_metrics.get("per_rubric")
    if not per_rubric:
        return None
    path = run_dir / "per_rubric.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["rubric_code", "accuracy", "f1_macro", "support"])
        for code, cell in per_rubric.items():
            w.writerow([code, f"{cell['accuracy']:.4f}", f"{cell['f1_macro']:.4f}", cell["support"]])
    return path


def write_confusion_matrix(run_dir: Path, spec: EvalSpec, records: List[RecordResult]) -> Optional[Path]:
    scored = [r for r in records if r.scored]
    if not scored:
        return None
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from sklearn.metrics import confusion_matrix

    y_true = [r.gt_overall for r in scored]
    y_pred = [r.pred_overall for r in scored]
    pos, neg = spec.scoring.positive_label, spec.scoring.negative_label
    observed = list(dict.fromkeys([pos, neg] + sorted(set(y_true) | set(y_pred))))
    cm = confusion_matrix(y_true, y_pred, labels=observed)

    fig, ax = plt.subplots(figsize=(4, 4))
    ax.imshow(cm, cmap="Blues")
    ax.set_xticks(range(len(observed)))
    ax.set_xticklabels(observed)
    ax.set_yticks(range(len(observed)))
    ax.set_yticklabels(observed)
    for i in range(len(observed)):
        for j in range(len(observed)):
            ax.text(j, i, str(cm[i][j]), ha="center", va="center", color="black")
    ax.set_xlabel("Predicted")
    ax.set_ylabel("Ground truth")
    ax.set_title(f"{spec.name} — overall verdict")
    fig.tight_layout()
    path = run_dir / "confusion_matrix.png"
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path


def write_prompt_versions(run_dir: Path, provenance: Dict[str, Any]) -> Path:
    path = run_dir / "prompt_versions.json"
    path.write_text(json.dumps(provenance, indent=2), encoding="utf-8")
    return path


def write_fixture_metadata(run_dir: Path, spec: EvalSpec, records: List[RecordResult], meta: Dict[str, Any]) -> Path:
    dist = Counter(r.gt_overall for r in records if r.gt_overall is not None)
    payload = {**meta, "label_distribution": dict(dist)}
    path = run_dir / "fixture_metadata.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def write_all(
    run_dir: Path,
    spec: EvalSpec,
    records: List[RecordResult],
    nested_metrics: Dict[str, Any],
    provenance: Dict[str, Any],
    fixture_meta: Dict[str, Any],
) -> Path:
    """Write every artifact into ``run_dir`` and return it (ready for mlflow.log_artifacts)."""
    run_dir.mkdir(parents=True, exist_ok=True)
    write_predictions(run_dir, records)
    write_failures(run_dir, records)
    write_per_rubric_csv(run_dir, nested_metrics)
    write_confusion_matrix(run_dir, spec, records)
    write_prompt_versions(run_dir, provenance)
    write_fixture_metadata(run_dir, spec, records, fixture_meta)
    return run_dir
