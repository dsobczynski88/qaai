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
from qaai.eval.datasets import EvalDataset
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


def evaluate(
    spec: EvalSpec,
    dataset: EvalDataset,
    *,
    mode: str,
    run_name: Optional[str] = None,
    experiment: Optional[str] = None,
    prompt_set: Optional[str] = None,
    max_concurrent: int = 10,
    limit: Optional[int] = None,
    allow_prod: bool = False,
    trace: bool = True,
    tracking_uri: Optional[str] = None,
) -> Dict[str, Any]:
    """Run one evaluation study and log it as a single MLflow run.

    ``mode='score'`` scores pre-computed eval_outputs (no LLM). ``mode='run'`` invokes
    the graph on eval_inputs first. Returns a small summary dict for the CLI.
    """
    import mlflow

    prompt_set = prompt_set or spec.prompt_set
    provenance = mr.prompt_provenance(prompt_set)

    if tracking_uri:
        mlflow.set_tracking_uri(tracking_uri)
    mlflow.set_experiment(experiment or mr.experiment_name(spec))

    run_dir = Path(tempfile.mkdtemp(prefix="qaai_eval_"))
    cost: Optional[Dict[str, float]] = None
    model = "unknown"

    # --- Gather outputs (either freshly produced or read from the dataset) ---
    if mode == "run":
        from qaai.core.telemetry import TokenUsageTracker
        from qaai.eval import runners

        inputs = dataset.inputs[:limit] if limit else dataset.inputs
        labels = dataset.labels[: len(inputs)]
        tracker = TokenUsageTracker(file_path=str(run_dir / "token_usage.jsonl"))
        client, model = runners.build_client(allow_prod=allow_prod, telemetry_tracker=tracker)
        outputs, latencies, completes, errors = asyncio.run(
            runners.run_and_collect(
                spec.component, inputs,
                client=client, model=model, prompt_set=prompt_set,
                max_concurrent=max_concurrent,
            )
        )
        entity_ids = [_entity_id(r) for r in inputs]
        # Persist the produced outputs so the run is reproducible / re-scorable offline.
        (run_dir / "eval_outputs.jsonl").write_text(
            "\n".join(json.dumps(o, default=_json_default) if o is not None else "null" for o in outputs) + "\n",
            encoding="utf-8",
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

    with mlflow.start_run(run_name=run_name) as run:
        params = mr.build_params(
            spec, dataset, mode=mode, model=model, prompt_set=prompt_set,
            max_concurrent=max_concurrent, provenance=provenance,
        )
        mlflow.log_params(params)
        mlflow.set_tags(mr.build_tags(spec))
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

    return {
        "run_id": run_id,
        "experiment": experiment or mr.experiment_name(spec),
        "metrics": flat,
        "n_records": len(records),
        "n_scored": nested.get("n_scored"),
        "artifacts_dir": str(run_dir),
    }
