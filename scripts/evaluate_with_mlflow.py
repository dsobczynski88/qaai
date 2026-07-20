"""MLflow evaluation harness for the QAAI reviewer pipelines (spec-driven).

Scores a reviewer as a binary classifier (overall_verdict) + per-rubric classifier
against a labelled three-file dataset, and logs one MLflow run with params, metrics,
and artifacts. The eval schema is described by a YAML spec (eval/specs/*.yaml), so the
same harness works for every reviewer/project.

Examples:
    # score pre-computed outputs (no LLM)
    uv run python scripts/evaluate_with_mlflow.py \
        --spec eval/specs/test_suite_reviewer.yaml \
        --dataset-dir eval/datasets/test_suite --mode score --run-name gold-baseline

    # run the graph live, then score (needs .env)
    uv run python scripts/evaluate_with_mlflow.py \
        --spec eval/specs/test_suite_reviewer.yaml \
        --dataset-dir eval/datasets/test_suite --mode run \
        --prompt-set test_suite_reviewer_v4 --max-concurrent 5 --limit 20
"""
import argparse
import os
from pathlib import Path

from qaai.eval.datasets import load_dataset
from qaai.eval.harness import evaluate
from qaai.eval.spec import load_spec


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--spec", type=Path, required=True, help="Path to eval/specs/<name>.yaml")
    ap.add_argument("--dataset-dir", type=Path, help="Dir with actual_inputs/actual_outputs/actual_labels.jsonl")
    ap.add_argument("--actual-inputs", type=Path, help="Override path to actual_inputs.jsonl")
    ap.add_argument("--actual-outputs", type=Path, help="Override path to actual_outputs.jsonl")
    ap.add_argument("--actual-labels", type=Path, help="Override path to actual_labels.jsonl")
    ap.add_argument("--mode", choices=("score", "run"), default="score")
    ap.add_argument("--model", help="Override the model (run mode; default settings.model / API_MODEL)")
    ap.add_argument("--prompt-set", help="Override the spec's prompt_set (run mode)")
    ap.add_argument("--run-name")
    ap.add_argument("--experiment", help="Override the spec's experiment name")
    ap.add_argument("--max-concurrent", type=int, default=10)
    ap.add_argument("--limit", type=int, help="Only evaluate the first N records")
    ap.add_argument("--allow-prod", action="store_true", help="Permit a base_url containing 'prod'")
    ap.add_argument("--no-trace", action="store_true", help="Disable MLflow LangGraph autolog tracing")
    ap.add_argument(
        "--predictions-dir",
        type=Path,
        help="Where run mode saves its timestamped prediction set (default <dataset-dir>/predictions)",
    )
    ap.add_argument(
        "--no-save-predictions",
        action="store_true",
        help="Do not persist a prediction set (run mode); outputs stay in MLflow artifacts only",
    )
    ap.add_argument(
        "--tracking-uri",
        default=os.getenv("MLFLOW_TRACKING_URI", "file:./mlruns"),
        help="MLflow tracking URI (default file:./mlruns)",
    )
    args = ap.parse_args()

    if args.model and args.mode == "score":
        print("[mlflow] WARNING: --model is ignored in --mode score (no LLM client is built).")

    spec = load_spec(args.spec)
    dataset = load_dataset(
        args.dataset_dir,
        mode=args.mode,
        inputs_path=args.actual_inputs,
        outputs_path=args.actual_outputs,
        labels_path=args.actual_labels,
    )
    summary = evaluate(
        spec, dataset,
        mode=args.mode,
        run_name=args.run_name,
        experiment=args.experiment,
        model=args.model,
        prompt_set=args.prompt_set,
        max_concurrent=args.max_concurrent,
        limit=args.limit,
        allow_prod=args.allow_prod,
        trace=not args.no_trace,
        tracking_uri=args.tracking_uri,
        predictions_dir=args.predictions_dir,
        save_predictions=not args.no_save_predictions,
    )

    m = summary["metrics"]
    print(f"[mlflow] experiment={summary['experiment']} run_id={summary['run_id']}")
    print(f"[mlflow] records={summary['n_records']} scored={summary['n_scored']} "
          f"skip_rate={m.get('skip_rate', 0):.3f}")
    if "overall_accuracy" in m:
        print(f"[mlflow] overall_accuracy={m['overall_accuracy']:.3f} "
              f"overall_f1={m.get('overall_f1', float('nan')):.3f} "
              f"rubric_macro_f1={m.get('rubric_macro_f1', float('nan')):.3f}")
    if "helper_invariant_pass_rate" in m:
        print(f"[mlflow] helper_invariant_pass_rate={m['helper_invariant_pass_rate']:.3f}")
    print(f"[mlflow] ground_truth_source={summary['ground_truth_source']}")
    print(f"[mlflow] artifacts staged at {summary['artifacts_dir']}")
    if summary.get("predictions_dir"):
        print(f"[mlflow] predictions saved to {summary['predictions_dir']}")
        print(f"[mlflow]   re-score offline: --mode score --actual-outputs "
              f"{Path(summary['predictions_dir']) / 'predicted_outputs.jsonl'} "
              f"--actual-labels <answer-key labels>")
    if summary.get("oracle_selftest"):
        print(
            "[mlflow] WARNING: every prediction matched the answer key exactly. This is an "
            "oracle self-test - it measures the harness, not the reviewer. Use --mode run "
            "to produce real predictions. (Tagged oracle_selftest=true.)"
        )


if __name__ == "__main__":
    main()
