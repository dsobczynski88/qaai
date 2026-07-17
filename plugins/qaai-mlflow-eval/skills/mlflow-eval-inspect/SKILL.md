---
name: mlflow-eval-inspect
description: |
  Inspect and compare QAAI MLflow evaluation runs — the Langfuse-like view. Launch the
  MLflow UI, compare runs side by side (params x metrics), read per-run artifacts
  (predictions.jsonl, failures.jsonl, per_rubric.csv, confusion_matrix.png,
  prompt_versions.json), and enable per-node LLM span tracing via
  mlflow.langchain.autolog() so each graph execution is browsable in the Traces tab. Also
  covers the CI regression gate (scripts/check_eval_gate.py). Use when the user asks to
  "open the MLflow UI", "compare runs", "inspect predictions / failures / traces", "why
  did this record fail", "diff two prompt sets' results", "see the confusion matrix", or
  "gate CI on eval metrics". Views the output of mlflow-eval-run.
---

# mlflow-eval-inspect

Browse, compare, and gate on evaluation runs.

## Open the UI

```bash
uv run mlflow ui                      # http://localhost:5000, backend file:./mlruns
```

- **Experiment list** → runs named by `--run-name` (e.g. prompt set + git sha).
- **Compare** (select 2+ runs) → param deltas next to metric deltas; sort by
  `overall_f1` / `rubric_macro_f1` to rank prompt sets.
- **Run → Metrics** → `overall_accuracy`, `overall_balanced_accuracy`, `overall_cohen_kappa`,
  `exact_match_rate`, `rubric_accuracy.M1..`, `rubric_support.M1.No`, `skip_rate`,
  `helper_invariant_pass_rate`, latency, cost.
- **Run → Artifacts** → the inspection surface:
  - `predictions.jsonl` — every record's gt vs pred + per-rubric cells + latency.
  - `failures.jsonl` — just the overall-verdict mismatches (quick regression triage).
  - `per_rubric.csv` — per-cell accuracy / macro-F1 / balanced accuracy / kappa / per-class support.
  - `confusion_matrix.png` — overall verdict Yes/No.
  - `per_rubric_confusion.png` — one panel per rubric cell: *which* cell drove a wrong
    verdict, and in which direction.
  - `prompt_versions.json` — exact prompt-set provenance (role → version + sha256).
  - `fixture_metadata.json` — dataset identity + label distribution.

## From a run back to its predictions on disk

A `--mode run` study also writes `<dataset>/predictions/<ts>/` containing the graph's
`predicted_outputs.jsonl`, their flat projection `predicted_labels.jsonl` (the PREDICTED
values), the `predicted_inputs.jsonl` it scored, and `run_metadata.json` — which carries
`mlflow_run_id`, so any prediction set maps
back to the run that made it, and vice versa. Use it to re-score a past run offline against
new metrics without paying for the LLM again (mlflow-eval-run, score-only). Compare two
timestamped sets directly:

```bash
uv run python -c "
from qaai.eval.datasets import load_jsonl
a = load_jsonl('eval/datasets/test_suite/predictions/<ts_a>/predicted_labels.jsonl')
b = load_jsonl('eval/datasets/test_suite/predictions/<ts_b>/predicted_labels.jsonl')
print([i for i,(x,y) in enumerate(zip(a,b)) if x != y])  # rows where the runs disagree"
```

## Per-node tracing (Langfuse-like)

Run+score runs enable `mlflow.langchain.autolog()` by default, so each graph invocation's
LLM calls appear as spans under the run's **Traces** tab (inputs/outputs per node).
Disable with `--no-trace` on large runs (autolog can produce many spans). Score-only runs
have no LLM calls, hence no traces — inspect their `predictions.jsonl` instead.

## Programmatic comparison

```python
from mlflow.tracking import MlflowClient
c = MlflowClient()
exp = c.get_experiment_by_name("test_suite_reviewer")
runs = c.search_runs([exp.experiment_id], order_by=["metrics.overall_f1 DESC"])
for r in runs:
    print(r.data.tags.get("mlflow.runName"), r.data.metrics.get("overall_f1"))
```

## CI regression gate

```bash
uv run python scripts/check_eval_gate.py --experiment test_suite_reviewer \
  --min-overall-accuracy 0.85 --min-rubric-macro-f1 0.80 --max-skip-rate 0.05
```
Reads the latest run for the experiment and exits non-zero if any threshold is violated.
Tune thresholds to a measured baseline minus a small buffer.

## Pitfalls

- The `file:./mlruns` backend is single-writer and is deprecated as of Feb 2026; for a
  shared/CI setup migrate to `sqlite:///mlflow.db` (set `--tracking-uri`). `mlflow.db` is
  already gitignored.
- A high `skip_rate` silently depresses accuracy denominators — always read it alongside
  accuracy.
- A run tagged `oracle_selftest=true` scored the answer key against itself and is pinned at
  1.000 by construction. It proves the harness works; it says nothing about the reviewer.
  Filter it out when ranking runs: `tags.oracle_selftest != "true"`.
- `helper_invariant_pass_rate < 1.0` means the reviewer's verdict contradicts its own
  rubric on some records; open those in `failures.jsonl` / `predictions.jsonl`.

## Out of scope

- Producing runs (mlflow-eval-run) and choosing metrics (mlflow-eval-metrics).
