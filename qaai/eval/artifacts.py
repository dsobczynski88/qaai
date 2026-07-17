"""Write per-run evaluation artifacts (the Langfuse-like inspection surface).

Everything here lands in the MLflow run's artifact directory:
    predictions.jsonl     per-record gt/pred + rubric + latency (audit trail)
    failures.jsonl        subset where overall_match is False (quick regression set)
    per_rubric.csv        rubric_code, accuracy, f1, balanced acc, kappa, per-class support
    confusion_matrix.png  overall-verdict confusion matrix
    per_rubric_confusion.png  one confusion-matrix panel per mandatory rubric cell
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


def write_per_rubric_csv(run_dir: Path, spec: EvalSpec, nested_metrics: Dict[str, Any]) -> Optional[Path]:
    per_rubric = nested_metrics.get("per_rubric")
    if not per_rubric:
        return None
    # One support column per verdict class, so a reader can see at a glance which
    # cells rest on a handful of rows.
    classes = [spec.scoring.positive_label, spec.scoring.negative_label, spec.scoring.na_label]
    path = run_dir / "per_rubric.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(
            ["rubric_code", "accuracy", "f1_macro", "balanced_accuracy", "cohen_kappa", "support"]
            + [f"support_{c}" for c in classes]
        )
        for code, cell in per_rubric.items():
            by_class = cell.get("support_by_class", {})
            kappa = cell.get("cohen_kappa")
            w.writerow(
                [
                    code,
                    f"{cell['accuracy']:.4f}",
                    f"{cell['f1_macro']:.4f}",
                    f"{cell['balanced_accuracy']:.4f}",
                    "n/a" if kappa is None else f"{kappa:.4f}",
                    cell["support"],
                ]
                + [by_class.get(c, 0) for c in classes]
            )
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


def write_per_rubric_confusion_matrices(
    run_dir: Path, spec: EvalSpec, records: List[RecordResult]
) -> Optional[Path]:
    """Small-multiples confusion matrix, one panel per rubric cell.

    The overall matrix says *how often* the verdict is wrong; this says *which cell*
    drove it and in which direction (e.g. M3 systematically predicting Yes where the
    label is N-A).
    """
    if not spec.output.rubric:
        return None
    codes = [c for c in spec.output.rubric.codes if c not in spec.scoring.advisory_codes]
    panels = []
    for code in codes:
        pairs = [
            (rc["gt"], rc["pred"])
            for r in records
            for rc in r.per_rubric
            if rc["code"] == code and rc["gt"] is not None and rc["pred"] is not None
        ]
        if pairs:
            panels.append((code, pairs))
    if not panels:
        return None

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from sklearn.metrics import confusion_matrix

    classes = [spec.scoring.positive_label, spec.scoring.negative_label, spec.scoring.na_label]
    fig, axes = plt.subplots(1, len(panels), figsize=(3.1 * len(panels), 3.4), squeeze=False)
    for ax, (code, pairs) in zip(axes[0], panels):
        gt = [g for g, _ in pairs]
        pred = [p for _, p in pairs]
        cm = confusion_matrix(gt, pred, labels=classes)
        ax.imshow(cm, cmap="Blues")
        ax.set_xticks(range(len(classes)))
        ax.set_xticklabels(classes, fontsize=8)
        ax.set_yticks(range(len(classes)))
        ax.set_yticklabels(classes, fontsize=8)
        for i in range(len(classes)):
            for j in range(len(classes)):
                ax.text(j, i, str(cm[i][j]), ha="center", va="center", fontsize=8, color="black")
        ax.set_title(f"{code} (n={len(pairs)})", fontsize=9)
        ax.set_xlabel("Predicted", fontsize=8)
    axes[0][0].set_ylabel("Ground truth", fontsize=8)
    fig.suptitle(f"{spec.name} — per-rubric cells", fontsize=10)
    fig.tight_layout()
    path = run_dir / "per_rubric_confusion.png"
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
    write_per_rubric_csv(run_dir, spec, nested_metrics)
    write_confusion_matrix(run_dir, spec, records)
    write_per_rubric_confusion_matrices(run_dir, spec, records)
    write_prompt_versions(run_dir, provenance)
    write_fixture_metadata(run_dir, spec, records, fixture_meta)
    return run_dir
