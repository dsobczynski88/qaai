"""CI gate: assert the latest MLflow run for an experiment clears metric thresholds.

Reads the most recent run in an experiment and exits non-zero if any threshold is
violated. Tune thresholds against a measured baseline (leave a small buffer).

Example:
    uv run python scripts/check_eval_gate.py \
        --experiment test_suite_reviewer \
        --min-overall-accuracy 0.85 --min-rubric-macro-f1 0.80 --max-skip-rate 0.05
"""
import argparse
import os
import sys


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--experiment", required=True)
    ap.add_argument("--tracking-uri", default=os.getenv("MLFLOW_TRACKING_URI", "file:./mlruns"))
    ap.add_argument("--min-overall-accuracy", type=float)
    ap.add_argument("--min-rubric-macro-f1", type=float)
    ap.add_argument("--max-skip-rate", type=float)
    args = ap.parse_args()

    import mlflow
    from mlflow.tracking import MlflowClient

    mlflow.set_tracking_uri(args.tracking_uri)
    client = MlflowClient()
    exp = client.get_experiment_by_name(args.experiment)
    if exp is None:
        print(f"[gate] experiment {args.experiment!r} not found", file=sys.stderr)
        return 2
    runs = client.search_runs([exp.experiment_id], order_by=["attributes.start_time DESC"], max_results=1)
    if not runs:
        print(f"[gate] no runs in {args.experiment!r}", file=sys.stderr)
        return 2
    metrics = runs[0].data.metrics

    checks = [
        ("overall_accuracy", args.min_overall_accuracy, "min"),
        ("rubric_macro_f1", args.min_rubric_macro_f1, "min"),
        ("skip_rate", args.max_skip_rate, "max"),
    ]
    failed = False
    for name, threshold, kind in checks:
        if threshold is None:
            continue
        actual = metrics.get(name)
        if actual is None:
            print(f"[gate] MISSING {name} (threshold {kind} {threshold})", file=sys.stderr)
            failed = True
            continue
        ok = actual >= threshold if kind == "min" else actual <= threshold
        status = "OK  " if ok else "FAIL"
        print(f"[gate] {status} {name}={actual:.4f} ({kind} {threshold})")
        failed = failed or not ok

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
