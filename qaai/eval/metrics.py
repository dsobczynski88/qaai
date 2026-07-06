"""Flatten the nested scoring result into a flat ``mlflow.log_metrics`` dict.

MLflow metric keys may contain letters, digits, ``_ - . / :`` and spaces, so the
``rubric_accuracy.M1`` style used by the existing evaluate-langgraph-mlflow protocol
is valid and kept for continuity.
"""
from __future__ import annotations

from typing import Any, Dict, Optional


def flatten_metrics(nested: Dict[str, Any], cost: Optional[Dict[str, float]] = None) -> Dict[str, float]:
    """Turn compute_metrics() output into flat scalar metrics for MLflow."""
    flat: Dict[str, float] = {}

    for k in ("n_total", "n_scored", "skip_rate"):
        if k in nested:
            flat[k] = float(nested[k])

    overall = nested.get("overall")
    if overall:
        for k, v in overall.items():
            flat[f"overall_{k}"] = float(v)

    per_rubric = nested.get("per_rubric")
    if per_rubric:
        for code, cell in per_rubric.items():
            flat[f"rubric_accuracy.{code}"] = float(cell["accuracy"])
            flat[f"rubric_f1.{code}"] = float(cell["f1_macro"])

    if "rubric_macro_f1" in nested:
        flat["rubric_macro_f1"] = float(nested["rubric_macro_f1"])
    if "helper_invariant_pass_rate" in nested:
        flat["helper_invariant_pass_rate"] = float(nested["helper_invariant_pass_rate"])

    latency = nested.get("latency")
    if latency:
        flat["mean_latency_s"] = float(latency["mean_s"])
        flat["p50_latency_s"] = float(latency["p50_s"])
        flat["p95_latency_s"] = float(latency["p95_s"])
        flat["p99_latency_s"] = float(latency["p99_s"])

    if cost:
        for k, v in cost.items():
            flat[k] = float(v)

    return flat
