---
name: dataset-review
description: |
  Run the end-to-end QAAI eval-dataset workflow: scaffold a timestamped dataset folder
  under eval/datasets/<type>/actual/<ts>/, generate records with the matching generate-*-dataset
  skill, derive the answer key, validate every row against the live Pydantic models,
  open the browser editor so a human can accept or correct each sample, and hand the
  result to the MLflow harness. Also covers reviewing or repairing an EXISTING dataset,
  branching one with Save-As, and reading its edits.log audit trail (including the
  per-record `feedback` lines). Use when the user
  asks to "build an eval dataset", "review the generated dataset", "check my dataset",
  "fix the labels", "open the dataset editor", "why does validate fail", or names a
  check code (V040, V050, ...). Sibling to generate-rtm-dataset, generate-tc-dataset,
  and generate-hazard-dataset (which author the records) and to the qaai-mlflow-eval
  plugin (which scores the result).
---

# dataset-review

Driver: `python -m qaai.dataset_studio` → `qaai/dataset_studio/`.

The one rule this workflow exists to enforce:

> **A row earns `Yes` only if a competent reviewer reading it would agree, and a
> known-bad row must carry a real deficiency visible in the text — not merely a missing
> row.**

`eval/datasets/test_suite/actual/2026-07-17_12-01-00/description.md` records what happens otherwise: an 800-row
generated set scored **kappa 0.000** and was discarded, because its labels were not
grounded in its content. The validator cannot detect that; a human in the editor can.
Never skip step 5.

## The loop

```bash
# 1. Scaffold. Prints a fresh timestamped folder; never overwrites an existing set.
DIR=$(uv run python -m qaai.dataset_studio new --type test_suite --quiet)

# 2. Author actual_inputs.jsonl + actual_labels.jsonl into $DIR
#    -> invoke generate-rtm-dataset / generate-tc-dataset / generate-hazard-dataset

# 3. Derive the answer key in output shape. Do NOT hand-write actual_outputs.jsonl.
uv run python -m qaai.dataset_studio sync-outputs "$DIR"

# 4. Validate. Must exit 0 before proceeding.
uv run python -m qaai.dataset_studio validate "$DIR"

# 5. Human review — the step that actually decides whether the set is worth anything.
uv run python -m qaai.dataset_studio edit "$DIR"

# 6. Score with the existing harness. No conversion, no renaming.
uv run python scripts/evaluate_with_mlflow.py \
  --spec eval/specs/test_suite_reviewer.yaml --dataset-dir "$DIR" --mode run
```

`--type` is one of `test_suite` | `test_case` | `hazard`, and is inferred from the path
on every subcommand but `new`.

## Commands

| Command | What it does |
|---|---|
| `new --type T [--base-dir D] [--title S] [--from-dataset DIR] [--quiet]` | Creates `eval/datasets/T/<ts>/` with the five-file skeleton. `--from-dataset` branches an existing set (copies the JSONL, not the log). `--quiet` prints only the path, for scripting. |
| `sync-outputs DIR [--force]` | Regenerates `actual_outputs.jsonl` from `actual_labels.jsonl`. |
| `validate DIR [--strict] [--json] [--rows A:B] [--checks C] [--skip C] [--list-checks] [--max-findings N]` | Checks the set against the live models + eval spec. |
| `edit DIR [--read-only] [--no-browser] [--port N] [--reviewer NAME] [--allow-invalid] [--dump-html PATH]` | Serves the sample editor on loopback. |

Exit codes: `0` clean · `1` errors (or warnings under `--strict`) · `2` missing files ·
`3` usage · `4` bad spec.

## The editor

Each page is one sample: the **generated input on the left, fully editable**, and the
**generated output's verdict cells on the right**.

- The input form is generated from the reviewer's own graph-state model, so every field
  the pipeline reads is editable — requirement text, each test case's steps and expected
  results (with Add/Remove), or the hazard register fields and their traces.
- The output pane offers only verdicts the rubric permits: `N-A` appears solely on codes
  that allow it, and advisory codes are marked as such.
- A live **derived verdict** readout recomputes the overall verdict from the cells and
  turns red when it disagrees with the stated one — the same rule as check `V040`. It is
  a warning, not the gate: the server re-validates on save and is the authority.
- **Reviewer note** captures *why* you agree with (or corrected) the labels. It is stored
  in the labels row as `reviewer_note` and ignored by the scorer.
- **Accept** records that you reviewed a row without changing it, so `edits.log` measures
  review coverage rather than only mutations.
- **Save** writes in place; **Save As…** writes a new timestamped sibling and leaves the
  source untouched.

Saving validates first: on any error nothing is written and the findings open in a modal.
Override deliberately with `--allow-invalid` (logged as `force-save`).

The server binds loopback only, mints a per-process token that the page must send as a
header, and refuses to write anywhere but the directory it was started on.

## edits.log

Tab-separated, append-only, seven fields, one line per action, written **after** the
JSONL lands so it can never claim a write that did not happen:

```
2026-07-19T10:35:02.114-05:00	edit	row=0007	actual_labels.jsonl	M3	"Yes" -> "No"	by=dsobc
2026-07-19T10:35:20.006-05:00	accept	row=0008	-	-	-	by=dsobc
2026-07-19T10:36:41.250-05:00	save	row=-----	actual_inputs.jsonl,...	-	rows=20 edits=12 validation=pass	by=dsobc
```

`path` uses the same dotted + `[i]` rendering as a validation finding, so a finding greps
straight out of the log. Parse it with
`qaai.dataset_studio.editlog.read_edits(dataset_dir)`.

## Reading validation findings

| Check | Meaning and usual fix |
|---|---|
| `V002` | Row counts differ across the three files. Positional alignment is the dataset's core invariant — find the short file. |
| `V010` / `V020` | A row does not match the live model. The `path` names the offending field. |
| `V021` | An output row carries no assessment. An answer key must have one. |
| `V030` | A label key is unknown, or a verdict is outside the vocabulary. Overall verdict is binary; `N-A` is a cell-level value only. |
| `V031` | `N-A` on a code that forbids it (`test_suite`: only M2/M3/R6 · `hazard`: only H5/R7 · `test_case`: never). |
| `V040` | The stated verdict contradicts its own cells. Fix the cells or the verdict — the message prints the derivation. |
| `V041` | A mandatory cell is missing, or cells are out of spec order. Advisory cells (R6/R7) may legitimately be absent. |
| `V050` | `actual_outputs` and `actual_labels` disagree. Almost always means the outputs were hand-written: rerun `sync-outputs`. |
| `V060` | `partial=true` beside a non-Yes verdict. |
| `V061` / `V071` | A finding cites no evidence, or cites an id absent from the row (e.g. a test case deleted in the editor). |
| `V080` | `description.md` is missing or still a stub. |
| `V090` | Single-class set, or a rubric cell with no negative example — that cell cannot be scored as a classifier. |

Errors block; warnings do not unless `--strict`.

## Two output shapes, both valid

- **minimal** — verdict plus `{code, verdict}` cells. What `sync-outputs` produces and
  what the committed pilot uses. Full-model validation is skipped for these rows because
  the shape deliberately omits fields the model requires.
- **full** — a real graph run's state. Held to the complete model, and the citation
  checks (`V061`/`V071`) apply.

Prefer minimal for generated datasets: it is the answer key, not a simulated run, and it
cannot drift from the labels.

## Out of scope

- Running the reviewer graphs — that is `--mode run` in the qaai-mlflow-eval plugin.
- Computing metrics — `scripts/evaluate_with_mlflow.py`.
- Sizing the set — `scripts/sample_size.py` (95% / ±0.05 needs 385 at p=0.5, 196 at
  p=0.85; size off `n_scored`, not `n_records`).
