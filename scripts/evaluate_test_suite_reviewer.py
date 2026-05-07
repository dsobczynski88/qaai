#!/usr/bin/env python3
"""MLflow evaluation harness for test_suite_reviewer LangGraph.

Treats the pipeline as:
  - Binary classifier: overall_verdict ∈ {Yes, No}
  - Multi-label classifier: M1-M5 × {Yes, No, N-A}

Logging Strategy:
  - Manual logging only (autolog disabled for API stability across MLflow versions)
  - Explicitly logs: params, metrics, artifacts, per-record predictions
  - Compatible with MLflow >= 3.12.0

Usage:
    uv run python scripts/evaluate_test_suite_reviewer.py \
        --fixture tests/fixtures/mlflow_eval/eval_inputs.jsonl \
        --labels tests/fixtures/mlflow_eval/eval_outputs_labels.jsonl \
        --run-name "baseline-$(git rev-parse --short HEAD)" \
        --max-concurrent 10
"""
import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any, List, Dict

import mlflow

from sklearn.metrics import (
    accuracy_score,
    f1_score,
    precision_recall_fscore_support,
    confusion_matrix,
)
import matplotlib.pyplot as plt
import numpy as np

from autoqa.components.test_suite_reviewer.pipeline import RTMReviewerRunnable
from autoqa.components.test_suite_reviewer.core import Requirement, TestCase
from autoqa.components.clients import RateLimitOpenAIClient
from autoqa.core.config import settings


# ============================================================================
# Utility Functions
# ============================================================================

def _git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"]).decode().strip()
    except Exception:
        return "unknown"


def _git_dirty() -> bool:
    try:
        return bool(subprocess.check_output(["git", "status", "--porcelain"]).decode().strip())
    except Exception:
        return False


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def _prompt_versions_manifest(prompt_config) -> dict:
    """Capture each Jinja2 template's path + content hash."""
    out = {}
    for role in ("decomposer", "summarizer", "coverage", "synthesizer"):
        if not hasattr(prompt_config, role):
            continue
        filename = getattr(prompt_config, role)
        path = Path(__file__).parent.parent / "autoqa" / "prompts" / filename
        out[role] = {
            "filename": filename,
            "sha256": _file_sha256(path) if path.exists() else None,
        }
    return out


# ============================================================================
# Pipeline Execution
# ============================================================================

async def _run_pipeline_batch(client, model, inputs, max_concurrent):
    """Run test_suite_reviewer graph against all inputs in parallel."""
    graph = RTMReviewerRunnable(client=client, model=model)
    sem = asyncio.Semaphore(max_concurrent)

    async def run_one(idx, row):
        async with sem:
            t0 = time.perf_counter()
            try:
                result = await graph.graph.ainvoke({
                    "requirement": Requirement(**row["requirement"]),
                    "test_cases": [TestCase(**tc) for tc in row["test_cases"]],
                })
                return idx, row, result, time.perf_counter() - t0, None
            except Exception as e:
                return idx, row, None, time.perf_counter() - t0, e

    completed = await asyncio.gather(
        *(run_one(i, row) for i, row in enumerate(inputs)),
        return_exceptions=False,
    )
    return completed


# ============================================================================
# Scoring & Metrics
# ============================================================================

def _score_predictions(completed, labels):
    """Extract predictions vs ground-truth labels."""
    predictions = []
    skipped = 0
    
    for c in completed:
        idx, row, state, latency, error = c
        if error or state is None:
            skipped += 1
            print(f"  [SKIP] Record {idx}: {error if error else 'No state returned'}")
            continue
        
        sa = state.get("synthesized_assessment")
        if sa is None:
            skipped += 1
            print(f"  [SKIP] Record {idx}: No synthesized_assessment in state")
            continue
        
        gt_labels = labels[idx]
        
        # Binary overall verdict
        pred_overall = sa.overall_verdict
        gt_overall = gt_labels["Overall_Verdict"]
        
        # Per-rubric M1-M5
        per_rubric = {}
        for i, finding in enumerate(sa.mandatory_findings):
            code = finding.code
            pred_verdict = finding.verdict
            gt_verdict = gt_labels[code]
            per_rubric[code] = {
                "predicted": pred_verdict,
                "ground_truth": gt_verdict,
                "match": pred_verdict == gt_verdict,
            }
        
        predictions.append({
            "record_idx": idx,
            "req_id": row["requirement"]["req_id"],
            "ground_truth_overall": gt_overall,
            "predicted_overall": pred_overall,
            "match_overall": pred_overall == gt_overall,
            "per_rubric": per_rubric,
            "latency_s": latency,
        })
    
    return predictions, skipped


def _aggregate_metrics(predictions, rubric_codes=["M1", "M2", "M3", "M4", "M5"]):
    """Compute MLflow metric scalars from per-record predictions."""
    if not predictions:
        return {}
    
    # Binary overall verdict
    y_true = [p["ground_truth_overall"] for p in predictions]
    y_pred = [p["predicted_overall"] for p in predictions]
    acc = accuracy_score(y_true, y_pred)
    prec, recall, f1, _ = precision_recall_fscore_support(
        y_true, y_pred, average="binary", pos_label="Yes", zero_division=0,
    )
    
    metrics = {
        "overall_accuracy":  acc,
        "overall_precision": prec,
        "overall_recall":    recall,
        "overall_f1":        f1,
    }
    
    # Per-rubric M1-M5
    per_rubric_f1 = []
    for code in rubric_codes:
        rub_y_true = [p["per_rubric"][code]["ground_truth"] for p in predictions]
        rub_y_pred = [p["per_rubric"][code]["predicted"] for p in predictions]
        
        # Filter out N-A for binary metrics
        binary_pairs = [(gt, pred) for gt, pred in zip(rub_y_true, rub_y_pred) if gt != "N-A"]
        
        if binary_pairs:
            binary_true = [gt for gt, _ in binary_pairs]
            binary_pred = [pred for _, pred in binary_pairs]
            
            metrics[f"rubric_accuracy.{code}"] = accuracy_score(binary_true, binary_pred)
            r_f1 = f1_score(binary_true, binary_pred, average="macro", zero_division=0)
            metrics[f"rubric_f1.{code}"] = r_f1
            per_rubric_f1.append(r_f1)
    
    if per_rubric_f1:
        metrics["rubric_macro_f1"] = float(np.mean(per_rubric_f1))
    
    # Latency
    lats = [p["latency_s"] for p in predictions]
    metrics["mean_latency_s"] = float(np.mean(lats))
    metrics["p50_latency_s"]  = float(np.percentile(lats, 50))
    metrics["p95_latency_s"]  = float(np.percentile(lats, 95))
    metrics["p99_latency_s"]  = float(np.percentile(lats, 99))
    
    return metrics


def _plot_confusion_matrix(predictions, out_path):
    """Generate confusion matrix for overall_verdict."""
    y_true = [p["ground_truth_overall"] for p in predictions]
    y_pred = [p["predicted_overall"] for p in predictions]
    labels = ["Yes", "No"]
    cm = confusion_matrix(y_true, y_pred, labels=labels)
    
    fig, ax = plt.subplots(figsize=(5, 5))
    im = ax.imshow(cm, cmap="Blues")
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels)
    ax.set_yticks(range(len(labels)))
    ax.set_yticklabels(labels)
    
    for i in range(len(labels)):
        for j in range(len(labels)):
            ax.text(j, i, str(cm[i][j]), ha="center", va="center", color="black", fontsize=14)
    
    ax.set_xlabel("Predicted", fontsize=12)
    ax.set_ylabel("Ground Truth", fontsize=12)
    ax.set_title("Overall Verdict — Confusion Matrix", fontsize=14)
    plt.tight_layout()
    plt.savefig(out_path, dpi=120)
    plt.close(fig)


def _plot_per_rubric_breakdown(predictions, out_path):
    """Generate per-rubric accuracy bar chart."""
    rubric_codes = ["M1", "M2", "M3", "M4", "M5"]
    accuracies = []
    
    for code in rubric_codes:
        rub_y_true = [p["per_rubric"][code]["ground_truth"] for p in predictions]
        rub_y_pred = [p["per_rubric"][code]["predicted"] for p in predictions]
        
        # Filter N-A
        binary_pairs = [(gt, pred) for gt, pred in zip(rub_y_true, rub_y_pred) if gt != "N-A"]
        
        if binary_pairs:
            binary_true = [gt for gt, _ in binary_pairs]
            binary_pred = [pred for _, pred in binary_pairs]
            accuracies.append(accuracy_score(binary_true, binary_pred))
        else:
            accuracies.append(0.0)
    
    fig, ax = plt.subplots(figsize=(8, 5))
    ax.bar(rubric_codes, accuracies, color="steelblue")
    ax.set_ylim(0, 1.0)
    ax.set_ylabel("Accuracy", fontsize=12)
    ax.set_xlabel("Rubric Code", fontsize=12)
    ax.set_title("Per-Rubric Accuracy (M1-M5)", fontsize=14)
    ax.grid(axis="y", alpha=0.3)
    plt.tight_layout()
    plt.savefig(out_path, dpi=120)
    plt.close(fig)


def _plot_per_rubric_confusion_matrices(predictions, out_dir):
    """Generate individual confusion matrices for each rubric code."""
    rubric_codes = ["M1", "M2", "M3", "M4", "M5"]
    
    for code in rubric_codes:
        rub_y_true = [p["per_rubric"][code]["ground_truth"] for p in predictions]
        rub_y_pred = [p["per_rubric"][code]["predicted"] for p in predictions]
        
        # Filter N-A
        binary_pairs = [(gt, pred) for gt, pred in zip(rub_y_true, rub_y_pred) if gt != "N-A"]
        
        if not binary_pairs:
            continue
        
        binary_true = [gt for gt, _ in binary_pairs]
        binary_pred = [pred for _, pred in binary_pairs]
        
        labels = ["Yes", "No"]
        cm = confusion_matrix(binary_true, binary_pred, labels=labels)
        
        fig, ax = plt.subplots(figsize=(4, 4))
        im = ax.imshow(cm, cmap="Greens")
        ax.set_xticks(range(len(labels)))
        ax.set_xticklabels(labels)
        ax.set_yticks(range(len(labels)))
        ax.set_yticklabels(labels)
        
        for i in range(len(labels)):
            for j in range(len(labels)):
                ax.text(j, i, str(cm[i][j]), ha="center", va="center", color="black", fontsize=12)
        
        ax.set_xlabel("Predicted", fontsize=10)
        ax.set_ylabel("Ground Truth", fontsize=10)
        ax.set_title(f"{code} Confusion Matrix", fontsize=12)
        plt.tight_layout()
        plt.savefig(out_dir / f"confusion_matrix_{code}.png", dpi=120)
        plt.close(fig)


# ============================================================================
# Main Evaluation Loop
# ============================================================================

async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixture", type=Path, required=True, help="eval_inputs.jsonl path")
    ap.add_argument("--labels", type=Path, required=True, help="eval_outputs_labels.jsonl path")
    ap.add_argument("--run-name", required=False, help="MLflow run name")
    ap.add_argument("--max-concurrent", type=int, default=10)
    ap.add_argument("--experiment-name", default="test_suite_reviewer-mlflow-eval")
    args = ap.parse_args()

    # Load data
    inputs = [json.loads(l) for l in args.fixture.read_text(encoding="utf-8").splitlines() if l.strip()]
    labels = [json.loads(l) for l in args.labels.read_text(encoding="utf-8").splitlines() if l.strip()]
    
    assert len(inputs) == len(labels), f"Mismatch: {len(inputs)} inputs vs {len(labels)} labels"



    # NOTE: MLflow autolog disabled - we manually log all params, metrics, and artifacts
    # for full control and API stability across MLflow versions (>= 3.12.0)
    
    mlflow.set_experiment(args.experiment_name)
    
    with mlflow.start_run(run_name=args.run_name):
        # Pin reproducibility knobs
        client = RateLimitOpenAIClient(api_key=os.getenv("BEDROCK_API_KEY"))
        model = os.getenv("BEDROCK_MODEL", settings.model)
        prompt_config = settings.prompt_config

        mlflow.log_params({
            "component":  "test_suite_reviewer",
            "model":      model,
            "git_sha":    _git_sha(),
            "git_dirty":  _git_dirty(),
            "prompt_decomposer":  prompt_config.decomposer,
            "prompt_summarizer":  prompt_config.summarizer,
            "prompt_coverage":    prompt_config.coverage,
            "prompt_synthesizer": prompt_config.synthesizer,
            "fixture_path":   str(args.fixture),
            "fixture_sha256": _file_sha256(args.fixture),
            "fixture_size":   len(inputs),
            "max_concurrent": args.max_concurrent,
        })
        mlflow.set_tags({
            "env":               os.getenv("AUTOQA_ENV", "local"),
            "prompt_set_label":  os.getenv("PROMPT_SET_LABEL", "default"),
        })

        # Run pipeline
        print(f"[eval] Running pipeline on {len(inputs)} records (max_concurrent={args.max_concurrent})...")
        t0 = time.perf_counter()
        completed = await _run_pipeline_batch(client, model, inputs, args.max_concurrent)
        wall_clock_s = time.perf_counter() - t0
        
        predictions, skipped = _score_predictions(completed, labels)
        print(f"[eval] Completed {len(predictions)} predictions, skipped {skipped}, wall_clock={wall_clock_s:.1f}s")

        # Aggregate metrics
        metrics = _aggregate_metrics(predictions)
        metrics["aggregator_skip_rate"] = skipped / max(len(inputs), 1)
        metrics["wall_clock_s"] = wall_clock_s
        mlflow.log_metrics(metrics)

        # Per-record step metrics
        for p in predictions:
            mlflow.log_metric("record_overall_correct", int(p["match_overall"]), step=p["record_idx"])
            mlflow.log_metric("record_latency_s", p["latency_s"], step=p["record_idx"])

        # Artifacts
        run_dir = Path("logs") / "mlflow_run"
        run_dir.mkdir(parents=True, exist_ok=True)
        
        (run_dir / "predictions.jsonl").write_text(
            "\n".join(json.dumps(p) for p in predictions), encoding="utf-8")
        
        failures = [p for p in predictions if not p["match_overall"]]
        (run_dir / "failures.jsonl").write_text(
            "\n".join(json.dumps(p) for p in failures), encoding="utf-8")
        
        _plot_confusion_matrix(predictions, run_dir / "confusion_matrix_overall.png")
        _plot_per_rubric_breakdown(predictions, run_dir / "per_rubric_accuracy.png")
        _plot_per_rubric_confusion_matrices(predictions, run_dir)
        
        (run_dir / "prompt_versions.json").write_text(
            json.dumps(_prompt_versions_manifest(prompt_config), indent=2), encoding="utf-8")
        
        mlflow.log_artifacts(str(run_dir))

        print(f"\n[mlflow] Run complete:")
        print(f"  overall_accuracy={metrics.get('overall_accuracy', 0):.3f}")
        print(f"  rubric_macro_f1={metrics.get('rubric_macro_f1', 0):.3f}")
        print(f"  wall_clock={wall_clock_s:.1f}s")
        print(f"  skipped={skipped}/{len(inputs)}")


if __name__ == "__main__":
    asyncio.run(main())
