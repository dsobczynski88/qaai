---
name: dataset-ingest
description: |
  Turn a completed QAAI review run into a reviewable eval dataset: point at a run folder
  (logs/run-<ts>/ from the API or pytest, or an eval predictions/<ts>/ set), convert its
  inputs.jsonl + outputs.jsonl into the row-aligned three-file answer key under
  eval/datasets/<type>/actual/<ts>/, then open the browser editor so a human can correct
  the model's labels and record why. Handles reviewer-type detection and the ragged
  inputs-vs-outputs alignment problem. Use when the user asks to "review the run", "open
  the report so I can edit it", "turn this run into a dataset", "label the baseline run",
  "make ground truth from logs/run-...", or "ingest the predictions". Sibling to
  dataset-review (which covers editing and validating once the folder exists) and to the
  generate-*-dataset skills (which author records from scratch instead).
---

# dataset-ingest

Driver: `python -m qaai.dataset_studio ingest` → `qaai/dataset_studio/ingest.py`.

## What this is for

The `generate-*-dataset` skills write records from scratch. This one goes the other
direction: it takes a run the pipeline **already did** and turns it into a dataset a human
can correct. That is usually the cheaper path to ground truth — the requirements and test
cases are real, and the reviewer only has to judge the model's answers rather than invent
material.

```
run the graph  →  ingest  →  edit in the browser  →  validate  →  score
```

## Ingest

```bash
uv run python -m qaai.dataset_studio ingest logs/run-2026-07-20_08-00-00 --edit
```

Accepts any of three folder conventions, probed in this order:

| Folder | Files it looks for |
|---|---|
| `logs/run-<ts>/` (API or pytest run) | `inputs.jsonl` + `outputs.jsonl` |
| `<dataset>/predictions/<ts>/` (`--mode run`) | `predicted_inputs.jsonl` + `predicted_outputs.jsonl` |
| an existing dataset folder | `actual_inputs.jsonl` + `actual_outputs.jsonl` |

Or name the files directly with `--inputs` / `--outputs`.

Useful flags: `--type` (skip detection), `--out DIR` (exact target instead of a fresh
timestamped one), `--reviewer NAME`, `--quiet` (print only the path), `--edit` (open the
editor immediately, with `--port` / `--no-browser`).

Output lands in `eval/datasets/<type>/actual/<YYYY-MM-DD_HH-MM-SS>/`:

```
actual_inputs.jsonl    projected out of the output state (see alignment, below)
actual_outputs.jsonl   the run's full graph state, verbatim
actual_labels.jsonl    flattened via outputs_to_labels — the same function the scorer uses
description.md         marked UNREVIEWED, with the provenance
source.json            source paths + sha256, git sha, skipped items, n_records
edits.log              seeded with one `ingest` line
```

## ⚠ The two things to get right

**1. The labels are the model's answers, not ground truth.** Scoring a freshly ingested
set returns accuracy 1.000 — it is being compared against the predictions it came from.
It is a starting point for review and nothing else. `description.md` says so in the file;
do not report that number, and do not let the set reach the harness unreviewed.

**2. Alignment.** `qaai/api/services.py::_run_batch_review` writes every input up front
but appends an output only for items whose graph run did not raise, so `outputs.jsonl`
can be **shorter** than `inputs.jsonl` — while row-`i`-is-the-same-item across all three
files is the eval dataset's core invariant. The ingester therefore emits **one row per
output row** and projects each input back out of the output state (which carries the
input keys). Items with no output are listed in `source.json` and reported on stderr.
Never zip the two files positionally to "fix" a count mismatch; that silently pairs one
item's requirement with another's verdict.

If the CLI reports skipped items, check `source.json` → `skipped` and decide whether the
failures were representative before drawing conclusions from what survived.

## Reviewer-type detection

Detected from the assessment key present in the output state — `synthesized_assessment`
→ `test_suite`, `aggregated_assessment` → `test_case`, `hazard_assessment` → `hazard` —
read from each type's `eval/specs/*.yaml`, scanning past rows whose run produced nothing.
Pass `--type` if a run is too soft-failed to identify.

## Then review it

The ingested rows are **full**-shape (real graph state), so the editor exposes more than a
scaffolded set does: verdict, `partial`, rationale, and the citation lists
(`cited_test_case_ids`, `uncovered_spec_ids`), plus the assessment-level fields under
"Assessment fields". The input pane is editable too — correct a garbled requirement rather
than labelling around it.

For each record: read the input, judge the verdict yourself, correct the cells the model
got wrong, and **write a reviewer note saying why**. The note is the point — it is what
makes a label defensible later, and it gets its own untruncated `feedback` line in
`edits.log`.

`--edit` starts the editor with `--allow-invalid`, deliberately: a run can contain the
model's own contradictions (a `V040` where the overall verdict disagrees with its cells),
and blocking the save would trap the reviewer with no way to record the fix. Validation
still runs and still reports; resolve the findings before scoring.

See **dataset-review** for the editor controls, the check codes, and reading `edits.log`.

## Verify before scoring

```bash
uv run python -m qaai.dataset_studio validate eval/datasets/<type>/actual/<ts>
```

Must exit 0. Then hand the folder to the `qaai-mlflow-eval` plugin as `--dataset-dir`.

## Out of scope

- Authoring records from scratch → `generate-rtm-dataset` / `-tc-` / `-hazard-`.
- Editor mechanics, check codes, Save-As → `dataset-review`.
- Producing a run in the first place → the API endpoints, or `mlflow-eval-run --mode run`.
