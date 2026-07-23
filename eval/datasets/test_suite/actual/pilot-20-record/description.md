# RTM Study Dataset — HealthCore EHR (seed batch)

**Revision 1** of the Test Suite Reviewer answer key —
`eval/datasets/test_suite/actual/pilot-20-record/`.

> **Amendment (2026-07-21):** each row was given a per-requirement `design_docs`
> SDD (a multi-paragraph Software Design Description), and an **R6 Design Alignment**
> label was added to every row (see *R6 Design Alignment* below). These were applied
> to this revision in place rather than as a new sibling.

## Domain & product
- Domain: Medical device software (SaMD / health software, IEC 82304)
- Product: HealthCore EHR — clinical ordering, medication management, and access control
- Compliance frame: IEC 62304, ISO 14971, FDA 21 CFR 820.30
- Continues the `REQ-HC-*` / `TC-HC-*` series of `tests/fixtures/gold/gold_dataset_labeled.jsonl`
  (which occupies REQ-HC-001..009); this set starts at REQ-HC-010.

## Why this set exists

It replaces an earlier **800-row generated set** (not in the repo) whose labels were **not
grounded in its content**. That set's test steps were templated placeholders — *"Execute
primary action specified in requirement. Verify success."* — yet rows were labelled `Yes`
by fiat. The reviewer rejected all 40 piloted records (accuracy 0.500, **kappa 0.000**) and
was **right** to: on a row labelled `Yes` it returned *"M4 = No: automatic import of heart
rate, blood pressure, and SpO2 is not covered"* and *"M5 = No: generic vocabulary rather
than the requirement's terms"*. You cannot measure accuracy against labels that are wrong.

The governing rule here: **a row earns `Yes` only if a competent reviewer reading it would
agree**, and a known-bad row must carry a **real deficiency visible in the text** — not
merely a missing row.

## Layout & revisions

Every answer key lives in its own timestamped folder under `<type>/actual/`:

```
eval/datasets/test_suite/actual/
  pilot-20-record/            <- this revision
    actual_inputs.jsonl             graph input          (run mode)
    actual_outputs.jsonl            ANSWER KEY, output shape
    actual_labels.jsonl             ANSWER KEY, flat projection
    source_gold.jsonl               hand-authored source of truth
    description.md                  this file
    edits.log                       append-only audit trail
    predictions/<ts>/               runs scored against THIS revision
  <later ts>/                     <- corrections land here, not in place
```

Row *i* of the three `actual_*` files is the same item; that positional alignment is the
dataset's core invariant.

**A revision is never edited in place.** Corrections are saved as a new timestamped sibling
(the editor's *Save As*), so a scored run always has the exact answer key it was scored
against still on disk. `predictions/<ts>/` hangs off the revision it scored — moving it
away from its parent breaks `qaai.eval.compare`'s automatic answer-key resolution.

`edits.log` records every change, every `accept`, and one untruncated `feedback` line per
record carrying the reviewer's written justification. Ingested sets also carry `source.json`
(source paths + sha256, git sha, skipped items); this revision was hand-authored, so it has
none.

The flat `eval/datasets/test_suite/actual_*.jsonl` layout this set used to occupy is gone.
Nothing in `qaai/` hardcodes either shape — the dataset directory is always a runtime
argument (`--dataset-dir`).

## Class distribution
- Known good (overall_verdict = Yes): 10
- Known bad  (overall_verdict = No):  10
- Total: 20 — 20 unique requirement texts, one record each

## Failure-mode distribution (known bads)
| Cell | Count | Records |
|---|---|---|
| M1 Functional No | 2 | REQ-HC-020, REQ-HC-028 |
| M2 Negative No | 2 | REQ-HC-021, REQ-HC-025 |
| M3 Boundary No | 2 | REQ-HC-022, REQ-HC-026 |
| M4 Spec Coverage No | 4 | REQ-HC-023, REQ-HC-027, + REQ-HC-020, REQ-HC-028 |
| M5 Terminology No | 2 | REQ-HC-024, REQ-HC-029 |

M4 carries 4 because the two M1-failure records have **no positive-path test at all**, which
genuinely leaves the functional spec uncovered. Labelling those M4=Yes would have been a
convenient fiction; the whole point of this set is that labels follow from content.

## R6 Design Alignment (advisory)
Every row now carries a `design_docs` SDD and an `R6` label. R6 is **advisory** —
excluded from `overall_verdict` exactly like M1–M5's relationship is not (a No never
flips the verdict). Distribution: **18 Yes / 2 No**.

The two `R6 = No` rows are **grounded**: their design doc was deliberately authored to
omit the enforcement the requirement demands, so the misalignment is visible in the text.
This is **orthogonal to the M1–M5 known-bads** — only the design doc was changed, no M-cell
label moved.

| R6 = No | Requirement | Design gap |
|---|---|---|
| REQ-HC-010 | account lock after 5 failed logins / 15-min window + lockout notice | SDD tracks failures and vaguely "may suspend," but never specifies the 5-attempt/15-min lock or the account-naming notice |
| REQ-HC-013 | end session after 20 min idle + require re-authentication | SDD describes the session module but omits automatic termination and the re-auth gate |

## Statistical posture
- **This is a seed batch, not the study.** n=20 gives a 95% CI of roughly ±0.22 — directional
  only. Its purpose is to test one hypothesis: *does a grounded dataset yield kappa > 0?*
- Scale target: **≥400 unique requirements, one row each** (385 is the worst-case p=0.5
  requirement for ±0.05; 450 absorbs skip_rate). See `qaai/eval/sample_size.py`.
- One row per unique requirement text ⇒ mean cluster size 1 ⇒ **design effect 1**, so the
  i.i.d. confidence interval is valid by construction. (The old set had 133 unique texts
  across 800 rows — one recurring 56× — which breaches ±0.05 at ICC ≳ 0.3 regardless of n.)
- Per-cell counts here (2–4) are far below the ~30/cell needed for per-rubric metrics. Read
  per-cell numbers as anecdote until scaled.
- Labels are authored, not adjudicated by a second reviewer. At scale, adjudicate a
  stratified ~60 and report reviewer-vs-label Cohen's kappa.

## Schema references
- Input shape: `requirement` + `test_cases` + `design_docs` (one `DD-HC-*` SDD per row);
  cf. `tests/fixtures/gold/gold_dataset_labeled.jsonl`.
- Eval spec (authoritative rubric): `eval/specs/test_suite_reviewer.yaml` — **M1–M5 mandatory
  + R6 advisory**, 6 findings. R6 is excluded from `overall_verdict`. This answer key now
  **includes** the R6 column (18 Yes / 2 No); it round-trips through `V050` like the others.
- Output shape: `qaai/agents/test_suite_reviewer/core.py::RTMReviewState`

## Provenance

`source_gold.jsonl` is the hand-authored source of truth (requirement + test_cases + labels).
The three `actual_*` files were generated from it by
`scripts/convert_to_eval.py gold --synthesize-outputs`.

Do not hand-write `actual_outputs.jsonl`. Deriving it from the labels is what makes the
answer key agree with itself; the maintained command is now:

```bash
uv run python -m qaai.dataset_studio sync-outputs <dataset-dir>
```

The `actual_*` files are the **answer key** (ACTUAL). Predictions only come from
`--mode run`, written as `predicted_*` under `predictions/<ts>/`.

## How to produce a successor

Two entry points, both finished the same way:

```bash
# Author a new set from scratch (controlled class balance, specific failure modes)
uv run python -m qaai.dataset_studio new --type test_suite

# Or convert a completed review run into one pre-filled with the model's own answers
uv run python -m qaai.dataset_studio ingest logs/run-<ts> --edit
```

Then review every record in the browser and validate:

```bash
uv run python -m qaai.dataset_studio edit     <dataset-dir>
uv run python -m qaai.dataset_studio validate <dataset-dir>   # must exit 0
```

An ingested set's labels are **the model's own answers**, not ground truth — scoring one
unreviewed returns 1.000 against the predictions it came from. The grounding rule above is
the gate on both paths: a `Yes` must be one a competent reviewer would agree with, and a
`No` must point at a deficiency visible in the text.

## Assumptions and rulings
- **M3 = N-A** where the requirement states no threshold, limit, or timing edge (e.g.
  REQ-HC-011 sorting, REQ-HC-014 audit content). Where a number appears (5 attempts, 4000 mg,
  20 minutes, 90 days, 10 characters, 60 seconds), a boundary test is required for M3 = Yes.
- **M2 = N-A** where the requirement exposes no validation or blocked-path surface (REQ-HC-024
  banner display, REQ-HC-029 notification delivery).
- REQ-HC-012 boundary ruling: the requirement rejects doses that *exceed* the maximum, so
  exactly 4000 mg is **accepted** and 4001 mg rejected. REQ-HC-019 likewise: "at least 10
  characters" ⇒ 10 accepted, 9 rejected.
- REQ-HC-024 and REQ-HC-029 are the M5 cases: the test cases verify real behaviour but in
  drifted vocabulary ("quarantine flag" / "patient summary widget" vs the requirement's
  "Isolation Precautions" banner on the "patient header"; "abnormal result alert" /
  "message center" vs "critical lab value notification" / "secure inbox").

## Verification gates before spending a run

Checks 2–4 are mechanical and are enforced by
`uv run python -m qaai.dataset_studio validate <dataset-dir>` — it must exit 0.

1. `unique requirement text == n_records` (design effect 1). **Not** machine-checked; the
   validator's `V070` catches duplicate ids, not duplicate prose.
2. Rubric rule holds for every row: `Overall_Verdict == Yes` iff all M1–M5 ∈ {Yes, N-A} —
   check `V040`.
3. M1/M4/M5 are never N-A — check `V031` (`V030` covers the label keys and the verdict
   vocabulary itself).
4. Round-trip: `outputs_to_labels(spec, actual_outputs) == actual_labels` — check `V050`,
   run through the same function the scorer uses.
5. Pilot gate: `cohen_kappa > 0` and `prevalence_pred_positive > 0`. A constant predictor
   means labels and content disagree — fix the data, do not scale up. Not machine-checked;
   read it off the MLflow run.
