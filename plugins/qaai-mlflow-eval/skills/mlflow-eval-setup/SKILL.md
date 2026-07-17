---
name: mlflow-eval-setup
description: |
  Set up spec-driven MLflow evaluation for a QAAI reviewer (or a new project) and
  prepare its labelled dataset. Authors an eval spec (eval/specs/<name>.yaml) that
  maps a reviewer's input / output / label schema — verdict path, rubric list + code/
  verdict fields, label keys — so the harness works without code changes. Prepares the
  three-file dataset (actual_inputs.jsonl / actual_outputs.jsonl / actual_labels.jsonl)
  via scripts/convert_to_eval.py, including converting the existing gold_dataset*.jsonl.
  Use when the user asks to "set up MLflow evaluation", "onboard a new eval dataset /
  model", "define the eval input/output/label schema", "map a reviewer for scoring",
  "convert gold data to the eval format", or "prepare an evaluation study". Pairs with
  mlflow-eval-run (execute a study), mlflow-eval-metrics (tune params/metrics),
  mlflow-eval-inspect (view results), and mlflow-eval-sample-size (size the dataset).
---

# mlflow-eval-setup

Stand up an evaluation study for a QAAI reviewer: write the spec that describes its
schema, then produce the labelled three-file dataset the harness scores against.

## Mission

1. Pick / author the **eval spec** for the reviewer under `eval/specs/<name>.yaml`.
2. Produce the **three-file dataset** under `eval/datasets/<name>/`.
3. Verify a score-only smoke run works before any live evaluation.

## The eval spec (the "different eval models" abstraction)

One YAML per reviewer/project. It is the ONLY thing that changes between eval schemas —
the harness (`qaai/eval/`) never hard-codes field names. Fields:

- `component` — one of `test_suite_reviewer` / `hazard_risk_reviewer` / `test_case_reviewer`
  (selects the runnable + input builder in `qaai/eval/runners.py`). For a brand-new
  pipeline, add an entry to the `COMPONENTS` registry there.
- `prompt_set` — default prompt set; `--prompt-set` overrides at run time.
- `input` — maps a graph-state key to a dotted path in an `actual_inputs` row (run mode).
- `output.verdict_path` / `output.rubric` — where the prediction lives in an output row.
- `labels.verdict_key` / `labels.rubric_keys` — how to read the flat answer key.
- `scoring.advisory_codes` — cells excluded from the overall verdict (e.g. `R6`, `R7`).

Three ready specs ship in `eval/specs/`: `test_suite_reviewer.yaml`,
`hazard_risk_reviewer.yaml`, `test_case_reviewer.yaml`. Copy the closest one and edit.

## Prepare the dataset

The canonical dataset is row-aligned — row *i* of every file describes the same item:

    eval/datasets/<name>/actual_inputs.jsonl        # graph input          (run mode)
                        /actual_outputs.jsonl        # ANSWER KEY, output shape
                        /actual_labels.jsonl         # ANSWER KEY, flat projection
                        /predictions/<ts>/...        # one live run's PREDICTIONS (predicted_*)

All three committed files describe the **actual** (labelled) truth: inputs, and the expected
answer in two shapes. Nothing in the dataset is a prediction. Predictions are produced by
`--mode run`, which writes `predictions/<ts>/` holding the graph's outputs plus their flat
projection (mlflow-eval-run). Keep that distinction straight and the rest of the harness
follows; blur it and you will "measure" 100% accuracy.

From the existing gold fixtures (gives working data immediately):

```bash
uv run python scripts/convert_to_eval.py gold \
  --input tests/fixtures/gold/gold_dataset_labeled.jsonl \
  --out eval/datasets/test_suite \
  --spec eval/specs/test_suite_reviewer.yaml --synthesize-outputs
```

`--synthesize-outputs` renders the labels into graph-output shape via
`datasets.py::synthesize_outputs` — that *is* the answer key, and it is the intended,
correct artifact. Because it equals the labels by construction, score-only mode against it
returns 1.000 and self-tags `oracle_selftest`; it exercises the plumbing offline for
smoke/CI and measures nothing about the reviewer. To harvest outputs from a prior run:

```bash
uv run python scripts/convert_to_eval.py outputs \
  --input logs/run-<ts>/outputs.jsonl --out eval/datasets/test_suite
```

You can also skip committing data and point the harness at your own files with
`--dataset-dir` (or `--actual-inputs/--actual-outputs/--actual-labels`).

## Prerequisites

- Deps already present (dev group): `mlflow`, `scikit-learn`, `matplotlib`, `numpy`.
- MLflow tracking defaults to `file:./mlruns` (gitignored). Datasets under `eval/` are
  committed; `mlruns/` and `mlflow.db` are not.

## Verification

```bash
uv run python scripts/evaluate_with_mlflow.py \
  --spec eval/specs/test_suite_reviewer.yaml \
  --dataset-dir eval/datasets/test_suite --mode score --run-name setup-smoke
```
Expect a run with `overall_accuracy`, per-rubric metrics, and artifacts. Oracle data
scores 1.0 and tags itself `oracle_selftest=true` — that only proves the plumbing; real
evaluation uses `--mode run` (mlflow-eval-run) or your own `actual_outputs`.

A stronger offline check, if you have both answer-key files: flattening `actual_outputs.jsonl`
must reproduce `actual_labels.jsonl` exactly, since the two converters are inverses.

```bash
uv run python -c "
from qaai.eval.spec import load_spec
from qaai.eval.datasets import load_jsonl, outputs_to_labels
s = load_spec('eval/specs/test_suite_reviewer.yaml')
d = 'eval/datasets/test_suite'
assert outputs_to_labels(s, load_jsonl(f'{d}/actual_outputs.jsonl')) == load_jsonl(f'{d}/actual_labels.jsonl')
print('answer key is self-consistent')"
```
The harness enforces this at run time too, failing with the offending row index.

## Pitfalls

- Label key casing matters: gold uses `Overall_Verdict` (capitalized) — the spec's
  `labels.verdict_key` must match your data exactly.
- CLAUDE.md refers to `tests/fixtures/mlflow_eval/`, but the real placeholder dir is
  `tests/fixtures/mlflow/`. Eval datasets live under the separate committed `eval/` tree.
- Advisory cells (`R6`/`R7`) must be listed in `scoring.advisory_codes` so they never
  flip the overall verdict — mirrors the reviewers' own aggregation.

## Out of scope

- Generating labelled data from scratch (use the `generate-*-dataset` skills).
- Running the study (mlflow-eval-run) and reading results (mlflow-eval-inspect).
