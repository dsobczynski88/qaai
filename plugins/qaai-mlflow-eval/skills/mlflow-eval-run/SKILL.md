---
name: mlflow-eval-run
description: |
  Run an MLflow evaluation study over a QAAI reviewer against a labelled dataset and log
  a tracked run (params, metrics, artifacts). Supports two modes: score-only (read
  pre-computed eval_outputs.jsonl, no LLM) and run+score (invoke the compiled LangGraph
  on eval_inputs.jsonl via bounded asyncio concurrency, then score). Scores the reviewer
  as a binary classifier on overall_verdict plus a per-rubric multi-cell classifier
  (M1-M5+R6 / H1-H6+R7 / 5 TC objectives), with helper-invariant and skip-rate signals.
  Use when the user asks to "run the evaluation", "evaluate the pipeline with MLflow",
  "score the reviewer against gold", "A/B two prompt sets", "track an eval run", or
  "compare prompt versions". Requires a spec + dataset from mlflow-eval-setup; view
  results with mlflow-eval-inspect.
---

# mlflow-eval-run

Execute one evaluation study = one MLflow run. Driver: `scripts/evaluate_with_mlflow.py`
→ `qaai/eval/harness.py::evaluate`.

## Mission

Load spec + dataset → gather predictions (score or run) → score vs labels → log params,
metrics, and artifacts to MLflow as a single comparable run.

## Score-only (no LLM — fast, offline)

Scores an existing `eval_outputs.jsonl` against `eval_outputs_labels.jsonl`:

```bash
uv run python scripts/evaluate_with_mlflow.py \
  --spec eval/specs/test_suite_reviewer.yaml \
  --dataset-dir eval/datasets/test_suite \
  --mode score --run-name gold-baseline
```

Use this to score data you already produced, or a prior run's outputs harvested via
`convert_to_eval.py outputs`.

## Run+score (live LLM — needs .env)

Invokes the graph on `eval_inputs.jsonl`, persists produced `eval_outputs.jsonl`, then
scores. `cache_mode` is forced to `off` (fresh, no reuse):

```bash
uv run python scripts/evaluate_with_mlflow.py \
  --spec eval/specs/test_suite_reviewer.yaml \
  --dataset-dir eval/datasets/test_suite \
  --mode run --prompt-set test_suite_reviewer_v4 \
  --max-concurrent 5 --limit 20 --run-name v4-edge-case
```

Key flags: `--prompt-set` (override the spec), `--max-concurrent` (parallel graph
invocations), `--limit N` (first N records), `--allow-prod` (permit a base_url
containing "prod" — off by default as a charge guard), `--no-trace` (disable autolog),
`--experiment` / `--run-name`, `--tracking-uri`.

## A/B comparing prompt sets

Run twice, flipping `--prompt-set`, into the same experiment:

```bash
for ps in test_suite_reviewer_v3 test_suite_reviewer_v4; do
  uv run python scripts/evaluate_with_mlflow.py \
    --spec eval/specs/test_suite_reviewer.yaml --dataset-dir eval/datasets/test_suite \
    --mode run --prompt-set $ps --run-name $ps
done
```
Then compare with mlflow-eval-inspect. Each run pins prompt versions + git sha as params,
so the diff is exact. To size the dataset needed to trust an A/B delta, see
mlflow-eval-sample-size.

## What gets logged

- **Params** — component, model, prompt_set, git sha/dirty, per-role prompt versions,
  fixture sha256, n_records, plus any spec `mlflow.params`.
- **Metrics** — `overall_{accuracy,precision,recall,f1}`, `rubric_accuracy.<code>`,
  `rubric_f1.<code>`, `rubric_macro_f1`, `helper_invariant_pass_rate`, `skip_rate`,
  latency percentiles (run mode), token/cost (run mode).
- **Artifacts** — predictions.jsonl, failures.jsonl, per_rubric.csv, confusion_matrix.png,
  prompt_versions.json, fixture_metadata.json, eval_outputs.jsonl (run mode).

## Pitfalls

- Score mode needs `eval_outputs.jsonl`; if missing, run `--mode run` first or synthesize
  oracle outputs (mlflow-eval-setup). The loader raises a clear error otherwise.
- Records whose graph run raises or fails `is_complete` count toward `skip_rate` and are
  excluded from accuracy — watch that metric, a spike means silent regressions.
- `--limit` truncates inputs AND the aligned labels together, so metrics stay valid.

## Out of scope

- Defining the spec / dataset (mlflow-eval-setup).
- Adding new metrics or params (mlflow-eval-metrics).
