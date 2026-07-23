# Single Test Case Reviewer dataset — HealthCore EHR (seed batch)

**Revision 1** of the Test Case Reviewer answer key —
`eval/datasets/test_case/actual/pilot-20-record/`.

This is the test-case-reviewer counterpart of the committed RTM pilot at
`eval/datasets/test_suite/actual/pilot-20-record/`, built with the same tooling
(`python -m qaai.dataset_studio`) and the same governing rule, so every editing and
evaluation command is identical apart from the reviewer-specific schema.

## Domain & product
- Domain: Medical device software (SaMD / health software, IEC 82304)
- Product: HealthCore EHR — clinical ordering, medication management, pharmacy inventory, and access control
- Compliance frame: IEC 62304, ISO 14971, FDA 21 CFR 820.30
- Continues the `REQ-HC-*` / `TC-HC-*` series shared with the RTM pilot and
  `tests/fixtures/gold/gold_dataset-tc.jsonl`; several rows are grounded adaptations of that fixture.

## Why this set exists

It is the grounded seed batch for the Test Case Reviewer, mirroring why the RTM pilot
exists: to test one hypothesis — *does a content-grounded answer key yield Cohen's
kappa > 0?* — before scaling to a study-sized set. The governing rule, carried over
verbatim from the RTM pilot: **a row earns `Yes` only if a competent reviewer reading
it would agree**, and a known-bad row must carry a **real deficiency visible in the
text** — not merely a missing row. Every known-bad here names a specific failing
objective tied to concrete evidence (a blank ExpectedResult number, an unverified final
step, an inverted step order, an off-topic test).

## The rubric (five review objectives)

Read `eval/specs/test_case_reviewer.yaml` (authoritative) and
`qaai/agents/test_case_reviewer/core.py::TCReviewState`. Five objectives, binary Yes/No:

| # | Objective id | Mandatory? |
|---|---|---|
| 1 | `expected_result_support` | yes |
| 2 | `expected_result_spec_align` | yes |
| 3 | `test_case_achieves` | yes |
| 4 | `test_case_logical_sequence` | yes |
| 5 | `test_case_setup_clarity` | **advisory** |

`Overall_Verdict = Yes` iff every **mandatory** objective is `Yes` (partial-Yes counts as
Yes). The fifth objective is advisory — a `No` there never flips the verdict, exactly
like R6 in the RTM reviewer and R7 in the hazard reviewer. **This answer key includes the
`test_case_setup_clarity` column as a full Yes/No cell** in both `actual_labels.jsonl` and
`actual_outputs.jsonl`, mirroring the way the hazard pilot carries its advisory `R7`
column (rather than the RTM pilot, which omits its advisory R6). The cell is scored and
reported like any other, but is excluded from the `Overall_Verdict` derivation — gate
V040 derives the verdict from the four mandatory objectives only — so a
`test_case_setup_clarity = No` on a known-good row does not flip the verdict.

## Layout & revisions

Identical to the RTM pilot's layout:

```
eval/datasets/test_case/actual/
  pilot-20-record/            <- this revision
    actual_inputs.jsonl             graph input          (run mode)
    actual_outputs.jsonl            ANSWER KEY, output shape (derived by sync-outputs)
    actual_labels.jsonl             ANSWER KEY, flat projection
    description.md                  this file
    edits.log                       append-only audit trail
    predictions/<ts>/               runs scored against THIS revision
  <later ts>/                     <- corrections land here, not in place
```

Row *i* of the three `actual_*` files is the same item; that positional alignment is the
dataset's core invariant. A revision is **never edited in place** — corrections are saved
as a new timestamped sibling. This set was hand-authored (not ingested), so it has no
`source.json`; and it uses the modern `dataset_studio` flow, so there is no legacy
`source_gold.jsonl` (the RTM pilot's `source_gold.jsonl` was a `convert_to_eval.py`
artifact; the maintained path derives `actual_outputs.jsonl` from labels via
`sync-outputs`).

## Class distribution
- Known good (Overall_Verdict = Yes): 10
  - Full-green (all objectives Yes, no partial): 5
  - Yes-partial on a mandatory objective (Overall = Yes): 4
  - Advisory `test_case_setup_clarity = No`, Overall = Yes: 1 (TC-HC-014-A)
- Known bad (Overall_Verdict = No): 10
- Total: 20 — 20 unique test cases, one record each

The full-green vs. yes-partial split is **authoring metadata**, not part of the scored
answer key: the `partial` flag is not a label field, so a yes-partial row and a
full-green row carry identical mandatory labels (all mandatory objectives Yes). The
sub-class and the partial-bearing objective are recorded in each row's `notes`/`sub_class`
for the human reviewer. Four of the ten known-good rows are yes-partial, placing the
partial on each of the four mandatory objectives in turn (spec_align, support, achieves,
logical_sequence). The fifth good in that group, **TC-HC-014-A, is the advisory
demonstrator**: its setup is trivial, so `test_case_setup_clarity = No`, but all four
mandatory objectives are Yes and `Overall_Verdict` stays Yes — the direct parallel to the
single `R7 = No` known-good in the hazard pilot.

## Failure-mode distribution (known bads)

| Primary objective (No) | Count | Records | Shape |
|---|---|---|---|
| `expected_result_support` | 3 | TC-HC-001-A, TC-HC-015-A, TC-HC-016-A | isolated |
| `test_case_achieves` | 2 | TC-HC-012A, TC-HC-030-A | isolated |
| `test_case_logical_sequence` | 2 | TC-HC-012C, TC-HC-033-A | isolated |
| `expected_result_spec_align` | 3 | TC-HC-040-A, TC-HC-018-A, TC-HC-022-A | cascade |

**Cascades are intentional and follow the aggregator's own semantics** (see
`single_test_aggregator` v9 and `qaai/dataset_studio/rules.py`). `expected_result_spec_align`
is a count over per-requirement coverage: a verdict of `No` means *zero* requirement
coverage, i.e. an off-topic test — which necessarily also fails `expected_result_support`
(nothing to support) and `test_case_achieves` (nothing verified). So the three
spec_align-primary rows carry `support=No, spec_align=No, achieves=No, logical=Yes`. Only
`test_case_logical_sequence` and the advisory `test_case_setup_clarity` are test-level
independent, which is why isolated single-cell failures are authored for support,
achieves, and logical_sequence but spec_align is exercised via the cascade shape. This
mirrors the RTM pilot's M1→M4 cascade (a functional-No record with no positive-path test
genuinely leaves the spec uncovered).

Resulting per-cell **No** coverage (what V090 checks): support 6, spec_align 3, achieves
5, logical_sequence 2 — every mandatory cell has at least one negative example.

**Advisory `test_case_setup_clarity` distribution:** 19 Yes / 1 No, the single No on a
known-good row (TC-HC-014-A). This is identical in shape to the hazard pilot's advisory
R7 column (19 Yes / 1 No, the No on a known-good). No known-bad carries a
`test_case_setup_clarity = No`, so the advisory cell never coincides with — or
contributes to — a `No` verdict.

## Statistical posture
- **This is a seed batch, not the study.** n=20 gives a 95% CI of roughly ±0.22 on
  overall accuracy — directional only. Its purpose is to confirm a grounded dataset
  yields kappa > 0.
- Scale target: **≥400 unique test cases, one row each** (385 is the worst-case p=0.5
  requirement for ±0.05; 450 absorbs skip_rate). See `qaai/eval/sample_size.py`.
- One row per unique test case ⇒ mean cluster size 1 ⇒ **design effect 1**, so the
  i.i.d. confidence interval is valid by construction.
- Per-cell counts here (2–6) are far below the ~30/cell needed for stable per-objective
  metrics. Read per-objective numbers as anecdote until scaled.
- Labels are authored, not adjudicated by a second reviewer. At scale, adjudicate a
  stratified sample and report reviewer-vs-label Cohen's kappa.

## Schema references
- Eval spec (authoritative rubric): `eval/specs/test_case_reviewer.yaml` — 5 objectives,
  first four mandatory + `test_case_setup_clarity` advisory. The advisory column is
  included in this answer key as a Yes/No cell (mirroring the hazard pilot's R7) and is
  excluded from the verdict derivation; round-trips via V050.
- Input shape: `test_case` (one TestCase) + `requirements` (list of Requirement)
  [+ optional `design_docs`] — see `qaai/agents/test_case_reviewer/core.py::TCReviewState`
  and `tests/fixtures/gold/gold_dataset-tc.jsonl`.
- Output shape: `aggregated_assessment.overall_verdict` +
  `aggregated_assessment.evaluated_checklist` (`id` / `verdict`).
- Per-objective Yes/partial/No rules: `qaai/prompts/single_test_aggregator/v9.0.0/template.jinja2`.

## Provenance
- Hand-authored from scratch (several rows are grounded adaptations of
  `tests/fixtures/gold/gold_dataset-tc.jsonl`, with requirement tracings tightened so
  each known-bad has a clean primary objective).
- `actual_outputs.jsonl` is **not hand-written**; it is derived from `actual_labels.jsonl`
  by `uv run python -m qaai.dataset_studio sync-outputs <dir>` (this is what makes the
  answer key agree with itself). Do not hand-edit it.
- Created: pilot-20-record (US/Central). Edits since creation: see `edits.log`.

## How to produce a successor / operate this set (identical to the RTM pilot)

```bash
# Author a new revision from scratch
uv run python -m qaai.dataset_studio new --type test_case
# ... author actual_inputs.jsonl + actual_labels.jsonl (row-aligned) ...
uv run python -m qaai.dataset_studio sync-outputs <dir>      # derive actual_outputs
uv run python -m qaai.dataset_studio validate  <dir>         # must exit 0
uv run python -m qaai.dataset_studio edit      <dir>         # human review in browser

# Or convert a completed review run into a pre-filled (UNREVIEWED) set
uv run python -m qaai.dataset_studio ingest logs/run-<ts> --edit

# Score the live pipeline against this answer key (writes predictions/<ts>/)
uv run python scripts/evaluate_with_mlflow.py --spec eval/specs/test_case_reviewer.yaml \
  --dataset-dir eval/datasets/test_case/actual/pilot-20-record --mode run --limit 20
uv run python -m qaai.eval.compare eval/datasets/test_case/actual/pilot-20-record/predictions/<ts>/
```

## Verification gates before spending a run

Checks are enforced by `uv run python -m qaai.dataset_studio validate <dir>` — it must
exit 0 (this revision does).

1. `unique test-case text == n_records` (design effect 1). Not machine-checked; V070
   catches duplicate `test_id`s, not duplicate prose.
2. Rubric rule holds for every row: `Overall_Verdict == Yes` iff all four **mandatory**
   objectives are `Yes` — check `V040`.
3. Round-trip: `outputs_to_labels(spec, actual_outputs) == actual_labels` — check `V050`,
   the same function the scorer uses.
4. Every mandatory cell has a negative example — check `V090`.
5. Pilot gate (not machine-checked): `cohen_kappa > 0` and `prevalence_pred_positive > 0`
   on the first scored run. A constant predictor means labels and content disagree — fix
   the data, do not scale up. Read it off the MLflow run.
