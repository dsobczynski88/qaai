---
name: mlflow-eval-run
description: |
  Run an MLflow evaluation study over a QAAI reviewer against a labelled dataset and log
  a tracked run (params, metrics, artifacts). Supports two modes: score-only (read
  pre-computed eval_outputs.jsonl, no LLM) and run+score (invoke the compiled LangGraph
  on eval_inputs.jsonl via bounded asyncio concurrency, saving a timestamped prediction
  set, then score). Scores the reviewer as a binary classifier on overall_verdict plus a
  per-rubric multi-cell classifier (M1-M5+R6 / H1-H6+R7 / 5 TC objectives), with
  helper-invariant and skip-rate signals.
  Use when the user asks to "run the evaluation", "evaluate the pipeline with MLflow",
  "score the reviewer against gold", "measure reviewer accuracy", "A/B two prompt sets",
  "track an eval run", or "compare prompt versions". Requires a spec + dataset from
  mlflow-eval-setup; view results with mlflow-eval-inspect.
---

# mlflow-eval-run

Execute one evaluation study = one MLflow run. Driver: `scripts/evaluate_with_mlflow.py`
→ `qaai/eval/harness.py::evaluate`.

## Mission

Load spec + dataset → gather predictions (score or run) → score vs the answer key → log
params, metrics, and artifacts to MLflow as a single comparable run.

## Predicted vs. actual — read this first

The dataset's `eval_outputs.jsonl` is the **answer key**: the labelled outputs, in
graph-output shape. It is the **ACTUAL** side. It is not a prediction and was never meant
to be one.

**PREDICTED** values only exist once the graph runs. `--mode run` invokes the reviewer over
`eval_inputs.jsonl` and writes a timestamped prediction set:

```
eval/datasets/test_suite/
  eval_inputs.jsonl                     # graph input
  eval_outputs.jsonl                    # answer key, output shape   <- ACTUAL
  eval_outputs_labels.jsonl             # answer key, flat           <- ACTUAL (projection)
  predictions/2026-07-16_17-05-33/
    eval_outputs.jsonl                  # what the graph produced
    eval_outputs_labels.jsonl           # those outputs, flattened   <- PREDICTED
    run_metadata.json                   # mlflow run_id, git sha, model, prompt versions
```

Both sides are flattened by the same function (`datasets.py::outputs_to_labels`, the exact
inverse of `synthesize_outputs`), which is what makes them comparable. Accuracy = predicted
vs actual.

Ground truth is read from `eval_outputs.jsonl` when present, else `eval_outputs_labels.jsonl`;
the choice is logged as the param `ground_truth_source`. If both exist and disagree on any
labelled cell, the run **fails** with the row index — a self-contradicting dataset makes every
downstream number meaningless.

## Run+score (live LLM — needs .env)

This is what measures the reviewer. `cache_mode` is forced to `off` (fresh, no reuse):

```bash
uv run python scripts/evaluate_with_mlflow.py \
  --spec eval/specs/test_suite_reviewer.yaml \
  --dataset-dir eval/datasets/test_suite \
  --mode run --prompt-set test_suite_reviewer_v4 \
  --max-concurrent 5 --limit 20 --run-name v4-edge-case
```

Key flags: `--prompt-set` (override the spec), `--max-concurrent` (parallel graph
invocations), `--limit N` (first N records — truncates inputs and ground truth together),
`--allow-prod` (permit a base_url containing "prod" — off by default as a charge guard),
`--predictions-dir` (default `<dataset-dir>/predictions`), `--no-save-predictions`,
`--no-trace` (disable autolog), `--experiment` / `--run-name`, `--tracking-uri`.

Predictions are written **before** scoring, so a scoring bug never discards the expensive
LLM outputs. Start with a small `--limit` to prove the plumbing before spending a full run.

## Score-only (no LLM — fast, offline)

Scores an existing `eval_outputs.jsonl` against `eval_outputs_labels.jsonl`. Its real use is
**re-scoring a past run for free** — new metrics, no LLM spend:

```bash
uv run python scripts/evaluate_with_mlflow.py \
  --spec eval/specs/test_suite_reviewer.yaml \
  --eval-outputs eval/datasets/test_suite/predictions/<ts>/eval_outputs.jsonl \
  --eval-outputs-labels eval/datasets/test_suite/eval_outputs_labels.jsonl \
  --mode score --run-name rescore-<ts>
```

Pointing `--dataset-dir` at the *parent* set instead scores the answer key against itself —
see the oracle pitfall below.

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
  fixture sha256, n_records, `ground_truth_source`, plus any spec `mlflow.params`.
- **Metrics** — `overall_{accuracy,precision,recall,f1,f1_macro,balanced_accuracy,
  cohen_kappa}`, `overall_prevalence_{gt,pred}_positive`, `exact_match_rate`,
  `rubric_{accuracy,f1,balanced_accuracy,kappa}.<code>`, `rubric_support.<code>[.<class>]`,
  `rubric_macro_f1`, `helper_invariant_pass_rate`, `skip_rate`, latency percentiles (run
  mode), token/cost (run mode).
- **Tags** — `env`, `owner`, plus `oracle_selftest=true` when applicable (see pitfalls).
- **Artifacts** — predictions.jsonl, failures.jsonl, per_rubric.csv, confusion_matrix.png,
  per_rubric_confusion.png, prompt_versions.json, fixture_metadata.json, eval_outputs.jsonl
  (run mode).

Read `balanced_accuracy` / `f1_macro` over plain accuracy when a class dominates, and check
`rubric_support.<code>.<class>` before trusting any per-cell number — a cell resting on ~80
minority rows carries roughly a ±0.08 interval regardless of how clean its accuracy looks.

## Pitfalls

- **Scoring the committed dataset with `--mode score` reports 1.000.** That is the answer key
  matching itself — a plumbing check, not a measurement. The run self-labels
  `oracle_selftest=true` and prints a warning. Only `--mode run` measures the reviewer.
- Score mode needs `eval_outputs.jsonl`; if missing, run `--mode run` first or synthesize
  oracle outputs (mlflow-eval-setup). The loader raises a clear error otherwise.
- Records whose graph run raises or fails `is_complete` count toward `skip_rate` and are
  excluded from accuracy — watch that metric, a spike means silent regressions. It also
  shrinks the effective N: size confidence intervals off `n_scored`, not `n_records`
  (mlflow-eval-sample-size).
- `--limit` truncates inputs AND the aligned ground truth together, so metrics stay valid.
- A rubric code the answer key doesn't label (e.g. R6) is simply not scored — a live run
  still predicts it, so expect predicted rows to carry cells the actual rows lack.

## Out of scope

- Defining the spec / dataset (mlflow-eval-setup).
- Adding new metrics or params (mlflow-eval-metrics).
