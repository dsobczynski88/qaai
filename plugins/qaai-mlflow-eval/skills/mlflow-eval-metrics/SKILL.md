---
name: mlflow-eval-metrics
description: |
  Add, remove, or tune the params, tags, and metrics logged by a QAAI MLflow evaluation
  run. Two levers: (1) declarative — edit the spec's mlflow block (params / tags /
  metrics_enabled) and scoring block (advisory_codes, rubric_class_mode) to toggle metric
  families and pin reproducibility knobs without touching code; (2) code — extend
  qaai/eval/scoring.py (a new derived signal) and qaai/eval/metrics.py (surface it as a
  flat MLflow metric). Use when the user asks to "add a metric", "log an extra param /
  tag", "remove latency/cost metrics", "track a new rubric signal", "change how N-A is
  scored", or "customize what the eval logs". Complements mlflow-eval-run (produces the
  run) and mlflow-eval-inspect (views the metrics).
---

# mlflow-eval-metrics

Control exactly what an evaluation run records. Prefer the declarative lever; drop to code
only for a genuinely new computed metric.

## Declarative: the spec `mlflow` + `scoring` blocks

In `eval/specs/<name>.yaml`:

```yaml
scoring:
  advisory_codes: [R6]          # cells excluded from overall verdict + rubric_macro_f1
  rubric_class_mode: multiclass  # or binary_collapse (fold N-A into the positive class)

mlflow:
  experiment: test_suite_reviewer
  params: { dataset_label: healthcore-v1, reviewer: rtm }   # extra pinned params
  tags:   { owner: dsobc, env: local }                       # queryable metadata
  metrics_enabled: [overall, per_rubric, latency, cost, helper_invariant]
```

`metrics_enabled` toggles whole families:
- `overall` — accuracy / precision / recall / f1 on the verdict.
- `per_rubric` — per-cell accuracy + macro-F1, and `rubric_macro_f1`.
- `helper_invariant` — does the predicted verdict match the deterministic rubric rule?
- `latency` — mean/p50/p95/p99 (run mode only).
- `cost` — token totals + estimated USD (run mode only).

Remove a family by dropping it from the list; add pinned params/tags by editing the maps.
`params`/`tags` merge on top of the always-logged catalogue in `qaai/eval/mlflow_run.py`.

## Code: add a brand-new metric

1. Compute it in `qaai/eval/scoring.py::compute_metrics` (gate it behind a name in
   `metrics_enabled`). Return it in the nested dict.
2. Flatten it in `qaai/eval/metrics.py::flatten_metrics` into a scalar MLflow key (keys
   may contain letters, digits, and `_ - . / :`).
3. Add a unit test in `tests/unit/eval/test_scoring.py` with a deterministic table.

The always-logged param catalogue (git sha, model, per-role prompt versions, fixture
sha) lives in `qaai/eval/mlflow_run.py::build_params` — extend there for a new *param*.

## Pitfalls

- MLflow metrics must be numeric; log strings as params/tags instead.
- Adding an advisory code to `advisory_codes` removes it from `rubric_macro_f1` but it is
  still scored per-cell (visible in per_rubric.csv) — intended.
- Changing a metric's meaning breaks cross-run comparability; bump a spec `params` label
  (e.g. `scoring_rev: 2`) so old runs stay distinguishable in the UI.

## Verification

Re-run any study (mlflow-eval-run) and confirm the added/removed keys appear/disappear in
the run's metrics, and that `tests/unit/eval` still passes.

## Out of scope

- Running studies (mlflow-eval-run) and comparing runs (mlflow-eval-inspect).
