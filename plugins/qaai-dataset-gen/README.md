# qaai-dataset-gen

A repo-committed Claude Code plugin for building **labelled eval datasets** for the QAAI
reviewer pipelines. Five skills wrap the Dataset Studio engine (`qaai/dataset_studio/`)
and land their output where the MLflow harness already reads from
(`eval/datasets/<type>/actual/<ts>/`):

| Skill | Purpose |
|-------|---------|
| `generate-rtm-dataset` | Requirement + traced test cases, scored on the M1–M5 (+R6) rubric |
| `generate-tc-dataset` | One test case + upstream requirements, scored on the 5 review objectives |
| `generate-hazard-dataset` | ISO 14971 hazard row + traces, scored on H1–H6 (+R7) |
| `dataset-ingest` | Turn a completed run (`logs/run-<ts>/`) into a reviewable dataset |
| `dataset-review` | The workflow around all of them: scaffold/ingest → validate → review → score |

Two ways to get a dataset. **Author** one with a `generate-*` skill when you need records
that do not exist yet — controlled class balance, specific failure modes. **Ingest** a
real run with `dataset-ingest` when the pipeline has already reviewed live material and
you only need a human to judge its answers; the inputs are then real by construction.

## Why this exists

`eval/datasets/test_suite/actual/2026-07-17_12-01-00/description.md` records an 800-row generated dataset that was
**discarded**: it scored **kappa 0.000** because its labels were not grounded in its
content — templated test steps labelled `Yes` by fiat. The reviewer rejected all 40
piloted records and was right to.

So generation here is deliberately *not* fully automatic. The model authors grounded
content; a validator enforces everything mechanical; and a human reviews each sample in a
browser before the set is trusted. The governing rule, carried into every skill:

> A row earns `Yes` only if a competent reviewer reading it would agree, and a known-bad
> row must carry a **real deficiency visible in the text** — not merely a missing row.

## Activation

This plugin is registered in the repo-root local marketplace
(`.claude-plugin/marketplace.json`). From a Claude Code session in this repo:

```
/plugin marketplace add .
/plugin install qaai-dataset-gen@qaai-mlflow-eval
```

(The marketplace is *named* `qaai-mlflow-eval` for historical reasons — it predates this
plugin — but it now registers both. The name after `@` is the marketplace, not the plugin.)

(If your Claude Code build resolves the local `source` differently, point it at the plugin
directory explicitly: `/plugin marketplace add ./plugins/qaai-dataset-gen`.)

Prefer zero-install? The five `SKILL.md` directories under `skills/` also work as project
skills if copied to `.claude/skills/` — they auto-load with no marketplace step.

## Quick start — from an existing run

```bash
# Convert a completed review run into a dataset and open it for correction.
uv run python -m qaai.dataset_studio ingest logs/run-<ts> --edit
```

The labels start as the model's own answers; correcting them is the review. See
`dataset-ingest`. Everything from step 4 below applies unchanged.

## Quick start — authoring from scratch

```bash
# 1. Scaffold a timestamped dataset folder. Prints the path; never touches an existing set.
uv run python -m qaai.dataset_studio new --type test_suite

# 2. Author actual_inputs.jsonl + actual_labels.jsonl into that folder.
#    This is the skill's job — invoke /generate-rtm-dataset (or -tc- / -hazard-).

# 3. Derive the outputs from the labels. Never hand-write this file: deriving it is
#    what makes the answer key agree with itself (check V050) by construction.
uv run python -m qaai.dataset_studio sync-outputs eval/datasets/test_suite/actual/<ts>

# 4. Validate against the LIVE Pydantic models + the eval spec. Must exit 0.
uv run python -m qaai.dataset_studio validate eval/datasets/test_suite/actual/<ts>

# 5. Review every sample in a browser. Edits the input AND the verdict cells; each
#    change is appended to edits.log in the dataset folder.
uv run python -m qaai.dataset_studio edit eval/datasets/test_suite/actual/<ts>

# 6. Score it with the existing harness — no conversion, no renaming.
uv run python scripts/evaluate_with_mlflow.py \
  --spec eval/specs/test_suite_reviewer.yaml \
  --dataset-dir eval/datasets/test_suite/actual/<ts> --mode run
```

## What lands on disk

```
eval/datasets/<type>/<YYYY-MM-DD_HH-MM-SS>/
  actual_inputs.jsonl    # graph input rows          (authored)
  actual_outputs.jsonl   # answer key, output shape  (derived via sync-outputs)
  actual_labels.jsonl    # flat answer key           (authored)
  description.md         # provenance, class balance, statistical posture
  edits.log              # append-only reviewer audit trail
```

Timestamp format and timezone match `logs/run-<ts>/` and `predictions/<ts>/`, so a
dataset, the run that scored it, and its predictions all sort and read alike.

## The validator

`validate` never judges whether a label is *right* — only a human can. It enforces
everything else, and refuses to let a dataset reach the harness broken:

| Check | Catches |
|---|---|
| `V002` | the three files drifting out of row alignment |
| `V010` / `V020` | rows that do not match the live reviewer models |
| `V030` / `V031` | bad label keys; `N-A` on a code that forbids it |
| `V040` | an overall verdict contradicting its own rubric cells |
| `V041` | a missing mandatory cell, or cells out of spec order |
| `V050` | `actual_outputs` and `actual_labels` disagreeing |
| `V061` / `V071` | findings citing evidence that is not in the row |
| `V090` | a single-class set, or a rubric cell with no negative example |

`uv run python -m qaai.dataset_studio validate --list-checks` prints the full catalog.

The engine, CLI, and tests live in the main repo (`qaai/dataset_studio/`,
`qaai/viewer/dataset_editor/`, `tests/unit/dataset_studio/`); this plugin is the skill
layer over them. Sibling plugin: **qaai-mlflow-eval**, which scores the datasets this one
produces.
