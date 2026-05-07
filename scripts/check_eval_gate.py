#!/usr/bin/env python3
"""Assert evaluation metrics meet minimum thresholds.

Usage:
    python scripts/check_eval_gate.py \
        --min-overall-accuracy 0.85 \
        --min-rubric-macro-f1 0.80 \
        --max-aggregator-skip-rate 0.05 \
        --experiment-name test_suite_reviewer-mlflow-eval
"""
import argparse
import sys
import mlflow
from mlflow.tracking import MlflowClient


def main():
    ap = argparse.ArgumentParser(description="Gate on MLflow evaluation metrics")
    ap.add_argument("--min-overall-accuracy", type=float, required=True)
    ap.add_argument("--min-rubric-macro-f1", type=float, required=True)
    ap.add_argument("--max-aggregator-skip-rate", type=float, required=True)
    ap.add_argument("--experiment-name", default="test_suite_reviewer-mlflow-eval")
    args = ap.parse_args()
    
    client = MlflowClient()
    
    try:
        experiment = client.get_experiment_by_name(args.experiment_name)
        if not experiment:
            print(f"❌ Experiment not found: {args.experiment_name}", file=sys.stderr)
            return 1
    except Exception as e:
        print(f"❌ Error fetching experiment: {e}", file=sys.stderr)
        return 1
    
    runs = client.search_runs(
        experiment_ids=[experiment.experiment_id],
        order_by=["start_time DESC"],
        max_results=1
    )
    
    if not runs:
        print(f"❌ No runs found in experiment: {args.experiment_name}", file=sys.stderr)
        return 1
    
    metrics = runs[0].data.metrics
    overall_acc = metrics.get("overall_accuracy", 0.0)
    rubric_f1 = metrics.get("rubric_macro_f1", 0.0)
    skip_rate = metrics.get("aggregator_skip_rate", 1.0)
    
    print(f"Latest run metrics:")
    print(f"  overall_accuracy={overall_acc:.3f}")
    print(f"  rubric_macro_f1={rubric_f1:.3f}")
    print(f"  aggregator_skip_rate={skip_rate:.3f}")
    print()
    
    failed = False
    
    if overall_acc < args.min_overall_accuracy:
        print(f"❌ overall_accuracy {overall_acc:.3f} < {args.min_overall_accuracy}")
        failed = True
    else:
        print(f"✅ overall_accuracy {overall_acc:.3f} >= {args.min_overall_accuracy}")
    
    if rubric_f1 < args.min_rubric_macro_f1:
        print(f"❌ rubric_macro_f1 {rubric_f1:.3f} < {args.min_rubric_macro_f1}")
        failed = True
    else:
        print(f"✅ rubric_macro_f1 {rubric_f1:.3f} >= {args.min_rubric_macro_f1}")
    
    if skip_rate > args.max_aggregator_skip_rate:
        print(f"❌ aggregator_skip_rate {skip_rate:.3f} > {args.max_aggregator_skip_rate}")
        failed = True
    else:
        print(f"✅ aggregator_skip_rate {skip_rate:.3f} <= {args.max_aggregator_skip_rate}")
    
    if failed:
        print("\n❌ Evaluation gate FAILED")
        return 1
    
    print("\n✅ All gates passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
