---
name: mlflow-eval-compare
description: |
  Render a record-by-record, side-by-side actual-vs-predicted diff for one QAAI evaluation
  run. Given a predictions/<ts>/ folder from a --mode run study, writes a single
  self-contained compare.html that shows, per record: the graph inputs, the actual (answer
  key) vs predicted overall verdict + rubric cells (M1-M5 / H1-H7 / TC objectives) with every
  deviation highlighted, and an expandable raw actual_outputs vs predicted_outputs JSON
  drill-down — plus a study-level mismatch summary. Auto-resolves the spec and parent dataset
  from run_metadata.json, so the only argument is the folder. Use when the user asks to
  "compare actual vs predicted", "view the eval diff", "which records deviated / flipped",
  "see the side-by-side results", "why did this record's verdict change", or "eyeball where
  the reviewer disagreed with gold". The visual, per-record counterpart to mlflow-eval-inspect
  (which is the aggregate MLflow view). Consumes the output of mlflow-eval-run.
---

# mlflow-eval-compare

Turn one run's predictions into a browsable, offline actual-vs-predicted diff.

## One command

```bash
python -m qaai.eval.compare eval/datasets/test_suite/predictions/<ts>/
# or the script wrapper:
uv run python scripts/compare_eval.py eval/datasets/test_suite/predictions/<ts>/ --open
# -> wrote eval/datasets/test_suite/predictions/<ts>/compare.html  (N records, K verdict mismatches)
```

Open `compare.html` in a browser (or pass `--open`). It is fully self-contained — inline
CSS/JS, data embedded in a `<script id="DATA">` tag — so it opens with `file://`, needs no
server, and is a shippable regulatory artifact.

Driver: `qaai/eval/compare.py::load_comparison` (builds the merged records) →
`qaai/viewer/generator.py::build_viewer_compare` (renders the HTML).

## What it auto-resolves (nothing to pass but the folder)

Every `predictions/<ts>/` folder carries a `run_metadata.json` written by
`--mode run` (mlflow-eval-run). The tool reads it to find:

- the **eval spec** — from `metadata['spec']` → `eval/specs/<name>.yaml` (searched up from the
  folder, then CWD). This is what makes the diff **schema-agnostic**: RTM (M1-M5+R6), hazard
  (H1-H6+R7), and test-case (5 objectives) all work with no code change.
- the **parent answer key** — from `source_outputs_path` / `source_inputs_path`, falling back to
  `<dataset-dir>/predictions/<ts>` → up two levels.

Override either with `--spec eval/specs/<name>.yaml` or `--dataset-dir <path>` if a folder was
moved or the metadata is incomplete.

## What the viewer shows

- **Study summary** (top of every record): N records, verdict-mismatch count + rate, and
  per-rubric-cell mismatch counts; plus the run's model / prompt_set / git sha / mlflow_run_id.
- **Per record**: a `VERDICT MATCH` / `VERDICT MISMATCH` / `NO PREDICTION (skipped)` flag, then
  an **actual | predicted** table — one row for the overall verdict, one per rubric code — where
  any deviating cell is highlighted (`← diff`; advisory cells like R6/R7 are shaded softer since
  they never flip the verdict).
- **Graph inputs →** and **Raw actual vs predicted output →** open modals: the input
  requirement/test-cases (and raw input JSON), and the two full graph-output objects side by side.
- A reviewer **feedback** panel (rating + notes) persisted to `localStorage`, keyed by the run
  timestamp; **Export feedback JSON** downloads it.

Soft-failed rows (the graph raised / returned nothing) render as `predicted = null` with a
skipped flag — they are the same rows that count toward `skip_rate` in mlflow-eval-run.

## Relationship to failures.jsonl

mlflow-eval-run already logs a `failures.jsonl` (the overall-verdict mismatches) as an MLflow
artifact. `compare.html` is the **human** view of the same disagreements plus the matches and the
full rationale: the study summary's verdict-mismatch count should equal that run's
`failures.jsonl` length. Use `failures.jsonl` for machine triage, `compare.html` to actually read
what deviated and why.

## Pitfalls

- Needs a real `predictions/<ts>/` folder — i.e. a `--mode run` study (mlflow-eval-run). A
  score-only run makes no predictions; there is nothing to diff. The committed dataset has no
  predictions folder until you run one.
- `R6` / `R7` appear as rubric rows but the answer key usually omits them, so they render `— vs —`
  (no diff) — expected, they are advisory.
- If the predictions folder was moved away from its dataset, pass `--dataset-dir` (the answer key
  is not inside the folder — only the predicted side is).

## Out of scope / see also

- **mlflow-eval-run** — produces the `predictions/<ts>/` folder this reads.
- **mlflow-eval-inspect** — the aggregate MLflow view (metrics, confusion matrices, run
  comparison, CI gate). This skill is its per-record, visual complement.
