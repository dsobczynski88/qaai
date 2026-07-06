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
- **Run → Metrics** → `overall_accuracy`, `rubric_accuracy.M1..`, `skip_rate`,
  `helper_invariant_pass_rate`, latency, cost.
- **Run → Artifacts** → the inspection surface:
  - `predictions.jsonl` — every record's gt vs pred + per-rubric cells + latency.
  - `failures.jsonl` — just the overall-verdict mismatches (quick regression triage).
  - `per_rubric.csv` — per-cell accuracy / macro-F1 / support.
  - `confusion_matrix.png` — overall verdict Yes/No.
  - `prompt_versions.json` — exact prompt-set provenance (role → version + sha256).
  - `fixture_metadata.json` — dataset identity + label distribution.

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
- `helper_invariant_pass_rate < 1.0` means the reviewer's verdict contradicts its own
  rubric on some records; open those in `failures.jsonl` / `predictions.jsonl`.

## Out of scope

- Producing runs (mlflow-eval-run) and choosing metrics (mlflow-eval-metrics).
