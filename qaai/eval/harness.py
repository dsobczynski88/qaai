"""End-to-end evaluation orchestrator: load -> (run|read) -> score -> log to MLflow.

The CLI (`scripts/evaluate_with_mlflow.py`) is a thin wrapper around ``evaluate()``.
Kept out of ``qaai/eval/__init__`` so importing the package stays light (no mlflow).
"""
from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from qaai.eval.artifacts import _json_default, write_all
from qaai.eval.datasets import (
    ACTUAL_LABELS_NAME,
    ACTUAL_OUTPUTS_NAME,
    METADATA_NAME,
    PREDICTED_INPUTS_NAME,
    PREDICTED_LABELS_NAME,
    PREDICTED_OUTPUTS_NAME,
    PREDICTIONS_DIRNAME,
    EvalDataset,
    file_sha256,
    new_predictions_dir,
    outputs_to_labels,
    write_jsonl,
)
from qaai.eval.metrics import flatten_metrics
from qaai.eval.scoring import build_records, compute_metrics
from qaai.eval.spec import EvalSpec
from qaai.eval import mlflow_run as mr


def _entity_id(row: Any) -> Optional[str]:
    """Best-effort human id for a record (for predictions.jsonl readability)."""
    if not isinstance(row, dict):
        return None
    req = row.get("requirement")
    if isinstance(req, dict) and req.get("req_id"):
        return req["req_id"]
    tc = row.get("test_case")
    if isinstance(tc, dict) and tc.get("test_id"):
        return tc["test_id"]
    if row.get("hazard_id"):
        return row["hazard_id"]
    haz = row.get("hazard")
    if isinstance(haz, dict) and haz.get("hazard_id"):
        return haz["hazard_id"]
    return None


def _ground_truth(spec: EvalSpec, dataset: EvalDataset, n: int) -> tuple[List[Dict[str, Any]], str]:
    """Resolve the ACTUAL values for run mode, preferring the answer-key actual_outputs.jsonl.

    ``actual_outputs.jsonl`` is the labelled dataset's outputs — the answer key in graph-output
    shape — so flattening it yields ground truth directly. ``actual_labels.jsonl`` is
    its flat projection and should say the same thing; when both exist they are cross-checked
    on the keys the answer key defines, and any disagreement raises. A dataset that
    contradicts itself makes every number downstream meaningless, so it fails loudly here
    rather than scoring against a coin flip.
    """
    key_labels = dataset.labels[:n]
    if not dataset.outputs:
        return key_labels, "actual_labels"

    derived = outputs_to_labels(spec, dataset.outputs[:n])
    for i, (d_row, k_row) in enumerate(zip(derived, key_labels)):
        # The answer-key file defines which cells are labelled; extra keys in the derived
        # row (e.g. an R6 the flat file omits) are additional information, not a conflict.
        clashes = {k: (k_row[k], d_row.get(k)) for k in k_row if k in d_row and d_row[k] != k_row[k]}
        if clashes:
            raise ValueError(
                f"Dataset is internally inconsistent at row {i}: {ACTUAL_OUTPUTS_NAME} and "
                f"{ACTUAL_LABELS_NAME} disagree on {clashes} (key: (labels, outputs)). "
                f"Fix the dataset before evaluating."
            )
    return derived, "actual_outputs"


def _write_prediction_set(
    base_dir: Path,
    spec: EvalSpec,
    inputs: List[Any],
    outputs: List[Any],
    metadata: Dict[str, Any],
) -> Path:
    """Persist one run's predictions as a timestamped, self-contained dataset fragment.

    The ``predicted_*`` filenames inside the timestamped directory mirror the parent's
    ``actual_*`` answer key, so the result is re-scorable with ``--mode score`` (pointing the
    ``--actual-*`` flags at these files) without any special-casing. The inputs this run
    scored are copied in too, so the folder stands alone.
    """
    pred_dir = new_predictions_dir(base_dir)
    write_jsonl(pred_dir / PREDICTED_INPUTS_NAME, inputs)
    (pred_dir / PREDICTED_OUTPUTS_NAME).write_text(
        "\n".join(json.dumps(o, default=_json_default) if o is not None else "null" for o in outputs) + "\n",
        encoding="utf-8",
    )
    # The PREDICTED values: the graph's own outputs in answer-key shape.
    write_jsonl(pred_dir / PREDICTED_LABELS_NAME, outputs_to_labels(spec, outputs))
    (pred_dir / METADATA_NAME).write_text(json.dumps(metadata, indent=2, default=str), encoding="utf-8")
    return pred_dir


def evaluate(
    spec: EvalSpec,
    dataset: EvalDataset,
    *,
    mode: str,
    run_name: Optional[str] = None,
    experiment: Optional[str] = None,
    model: Optional[str] = None,
    prompt_set: Optional[str] = None,
    max_concurrent: int = 10,
    limit: Optional[int] = None,
    allow_prod: bool = False,
    trace: bool = True,
    tracking_uri: Optional[str] = None,
    predictions_dir: Optional[Path] = None,
    save_predictions: bool = True,
) -> Dict[str, Any]:
    """Run one evaluation study and log it as a single MLflow run.

    ``mode='score'`` scores pre-computed actual_outputs (no LLM). ``mode='run'`` invokes the
    graph on actual_inputs first, then persists what it produced to a timestamped prediction
    set (see ``_write_prediction_set``) so the run can be re-scored offline later.
    Returns a small summary dict for the CLI.
    """
    import mlflow

    prompt_set = prompt_set or spec.prompt_set
    provenance = mr.prompt_provenance(prompt_set)

    if tracking_uri:
        mlflow.set_tracking_uri(tracking_uri)
    mlflow.set_experiment(experiment or mr.experiment_name(spec))

    run_dir = Path(tempfile.mkdtemp(prefix="qaai_eval_"))
    cost: Optional[Dict[str, float]] = None
    model_override = model  # capture the caller's override before `model` is reused for the resolved value
    model = "unknown"
    gt_source = "actual_labels"
    pred_dir: Optional[Path] = None

    # --- Gather outputs (either freshly produced or read from the dataset) ---
    if mode == "run":
        from qaai.core.telemetry import TokenUsageTracker
        from qaai.eval import runners

        inputs = dataset.inputs[:limit] if limit else dataset.inputs
        # ACTUAL values: the answer key (actual_outputs.jsonl), cross-checked against its
        # flat projection. --limit truncates inputs and ground truth together.
        labels, gt_source = _ground_truth(spec, dataset, len(inputs))
        tracker = TokenUsageTracker(file_path=str(run_dir / "token_usage.jsonl"))
        client, model = runners.build_client(
            allow_prod=allow_prod, telemetry_tracker=tracker, model_override=model_override
        )
        outputs, latencies, completes, errors = asyncio.run(
            runners.run_and_collect(
                spec.component, inputs,
                client=client, model=model, prompt_set=prompt_set,
                max_concurrent=max_concurrent,
            )
        )
        entity_ids = [_entity_id(r) for r in inputs]
        # Staged into the MLflow artifacts too, so a run is self-describing in the UI.
        (run_dir / PREDICTED_OUTPUTS_NAME).write_text(
            "\n".join(json.dumps(o, default=_json_default) if o is not None else "null" for o in outputs) + "\n",
            encoding="utf-8",
        )
        if save_predictions:
            base = predictions_dir or (
                (dataset.inputs_path.parent / PREDICTIONS_DIRNAME) if dataset.inputs_path else None
            )
            if base:
                pred_dir = _write_prediction_set(
                    Path(base), spec, inputs, outputs,
                    {
                        "component": spec.component,
                        "spec": spec.name,
                        "model": model,
                        "prompt_set": prompt_set,
                        "prompt_versions": {r: p.get("version") for r, p in provenance.get("prompts", {}).items()},
                        "git_sha": mr.git_sha(),
                        "git_dirty": mr.git_dirty(),
                        "source_inputs_path": str(dataset.inputs_path),
                        "source_outputs_path": str(dataset.outputs_path) if dataset.outputs_path else None,
                        "source_fixture_sha256": file_sha256(dataset.inputs_path),
                        "ground_truth_source": gt_source,
                        "n_records": len(outputs),
                        "limit": limit,
                    },
                )
        s = tracker.summary()
        cost = {
            "total_input_tokens": s["total_prompt_tokens"],
            "total_output_tokens": s["total_completion_tokens"],
            "estimated_cost_usd": s["total_cost_usd"],
        }
    else:  # score-only
        model = provenance.get("set", "n/a")
        n = min(len(dataset.outputs), len(dataset.labels))
        if limit:
            n = min(n, limit)
        outputs = dataset.outputs[:n]
        labels = dataset.labels[:n]
        latencies = None
        completes = None
        errors = None
        src = dataset.inputs[:n] if dataset.inputs else outputs
        entity_ids = [_entity_id(r) for r in src]

    # --- Score ---
    records = build_records(
        spec, outputs, labels,
        entity_ids=entity_ids, latencies=latencies, completes=completes, errors=errors,
    )
    nested = compute_metrics(spec, records)
    flat = flatten_metrics(nested, cost=cost)

    # --- Log to MLflow ---
    if trace and mode == "run":
        mr.enable_tracing()

    # A score run whose "predictions" match the answer key on every record is scoring the
    # answer key against itself — the committed oracle dataset does exactly this. It is a
    # valid plumbing check but not a measurement, and a silent 1.000 is the failure mode
    # that looks most like success, so label it in MLflow and say so on stdout.
    oracle_selftest = (
        mode == "score"
        and bool(records)
        and all(r.overall_match for r in records if r.scored)
        and nested.get("n_scored", 0) > 0
    )

    with mlflow.start_run(run_name=run_name) as run:
        params = mr.build_params(
            spec, dataset, mode=mode, model=model, prompt_set=prompt_set,
            max_concurrent=max_concurrent, provenance=provenance,
        )
        params["ground_truth_source"] = gt_source
        mlflow.log_params(params)
        tags = mr.build_tags(spec)
        if oracle_selftest:
            tags["oracle_selftest"] = "true"
        mlflow.set_tags(tags)
        mlflow.log_metrics(flat)
        fixture_meta = {
            "spec": spec.name,
            "component": spec.component,
            "mode": mode,
            "labels_path": str(dataset.labels_path) if dataset.labels_path else None,
            "n_records": len(records),
        }
        write_all(run_dir, spec, records, nested, provenance, fixture_meta)
        mlflow.log_artifacts(str(run_dir))
        run_id = run.info.run_id

    # The prediction set is written before scoring so a crash never discards the expensive
    # LLM outputs; the run_id only exists afterwards, so stamp it in now.
    if pred_dir:
        meta_path = pred_dir / METADATA_NAME
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["mlflow_run_id"] = run_id
        meta["mlflow_experiment"] = experiment or mr.experiment_name(spec)
        meta_path.write_text(json.dumps(meta, indent=2, default=str), encoding="utf-8")

    return {
        "run_id": run_id,
        "experiment": experiment or mr.experiment_name(spec),
        "metrics": flat,
        "n_records": len(records),
        "n_scored": nested.get("n_scored"),
        "artifacts_dir": str(run_dir),
        "predictions_dir": str(pred_dir) if pred_dir else None,
        "ground_truth_source": gt_source,
        "oracle_selftest": oracle_selftest,
    }
