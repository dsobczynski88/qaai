---
name: generate-tc-dataset
description: |
  Generate a labelled dataset for evaluating the QAAI test_case_reviewer LangGraph as a
  binary classifier. Each input is a single TestCase plus its upstream requirements; each
  label row carries the overall verdict and the five review objectives
  (expected_result_support, expected_result_spec_align, test_case_achieves,
  test_case_logical_sequence, test_case_setup_clarity). Records are labelled "known good"
  (Overall_Verdict=Yes, sub-classified full-green or yes-partial) or "known bad"
  (Overall_Verdict=No with a designated primary_failure objective). Writes
  actual_inputs.jsonl + actual_labels.jsonl + description.md into a timestamped folder
  under eval/datasets/test_case/, derives actual_outputs.jsonl, and must pass
  `dataset_studio validate`. Default domain is medical-device software (SiMD / SaMD /
  IEC 82304). Use when the user asks to synthesize a labelled dataset for the
  per-test-case checklist reviewer or evaluate it as a classifier. Siblings:
  generate-rtm-dataset, generate-hazard-dataset, dataset-review.
---

# generate-tc-dataset

Authors records for `qaai.agents.test_case_reviewer`. Live schema:
`qaai/agents/test_case_reviewer/core.py`. Authoritative rubric:
`eval/specs/test_case_reviewer.yaml`. The objective definitions and their scoring tiers
live in `qaai/prompts/single_test_aggregator/<latest>/template.jinja2` — **read it**, as
it is where the per-objective Yes/partial/No rules are actually specified.

Where the RTM skill reviews a *suite* against one requirement, this reviews **one test
case** against one or more upstream requirements.

This skill is a **content generator**. It writes files; it does not invoke the graph.

## Persona

A **Principal Test Architect** who reviews individual verification protocols for
regulated software: is this test executable as written, does it verify what the
requirement asks, and does its evidence prove the outcome?

## Inputs to gather

If unspecified, ask once with `AskUserQuestion`: **domain + product** (default
medical-device software), **sample count**, **class balance** (default 50/50), and the
**output folder** — default whatever
`dataset_studio new --type test_case --quiet` prints.

## Files to write

- **`actual_inputs.jsonl`**:
  ```json
  {"test_case": {"test_id": "TC-HC-014-A", "description": "...", "setup": "...",
                 "steps": "Step: 1. ...\nStep: 2. ...",
                 "expectedResults": "ExpectedResult: 1. ...\nExpectedResult: 2. ..."},
   "requirements": [{"req_id": "REQ-HC-014", "text": "..."}]}
  ```
  `design_docs` is optional. A test case may trace to **several** requirements — that is
  what makes the per-objective count tiers (below) meaningful, so include multi-requirement
  records deliberately.

- **`actual_labels.jsonl`**:
  ```json
  {"Overall_Verdict": "No", "expected_result_support": "Yes",
   "expected_result_spec_align": "Yes", "test_case_achieves": "No",
   "test_case_logical_sequence": "Yes", "test_case_setup_clarity": "Yes",
   "class": "known_bad", "primary_failure": "test_case_achieves"}
  ```

- **`actual_outputs.jsonl`** — **do not hand-write**; run
  `uv run python -m qaai.dataset_studio sync-outputs <dir>`.

- **`description.md`** — replace every `TODO`.

## The five objectives

Ids are fixed and ordered. **No `N-A`** — `EvaluatedReviewObjective.verdict` is
`Yes`/`No` only.

| id | Checks |
|---|---|
| `expected_result_support` | Expected results give measurable, observable evidence for what each requirement asks. |
| `expected_result_spec_align` | Results reflect all conditions in the requirement; nothing vague or missing. |
| `test_case_achieves` | The final steps actually verify the intended outcome. |
| `test_case_logical_sequence` | Steps flow coherently from setup through execution to verification. |
| `test_case_setup_clarity` | Environment and prerequisites are documented well enough to repeat the run. |

`Overall_Verdict = "Yes"` iff every **mandatory** objective is `Yes` — that is the first
four; `test_case_setup_clarity` is advisory and never gates it. Partial-Yes counts as Yes.

> ⚠ **`test_case_setup_clarity` is advisory.** It is the TC rubric's R6/R7: every
> shipping aggregator prompt fixes its `mandatory` flag to `false`, and
> `eval/specs/test_case_reviewer.yaml` lists it in `advisory_codes` to match. A row whose
> **only** `No` is `test_case_setup_clarity` therefore has `Overall_Verdict = "Yes"`.
>
> Label it that way deliberately — those rows are the only ones that prove the advisory
> exclusion works end to end, and `V040` will reject the set if you write `No` instead.

Note also that `TestCaseAssessment._validate_overall_verdict` computes the expected
verdict and then does nothing with it — the model will **not** catch a contradiction
here. Check `V040` is the only guard, so run `validate`.

## Partial (yellow) semantics

`partial=true` requires `verdict="Yes"` and marks materially incomplete coverage. It never
appears with `No`. Three objectives have explicit count tiers in the prompt — mirror them:

- `expected_result_spec_align`: all requirements covered → Yes; *some* covered → Yes +
  partial; none → No.
- `expected_result_support`: all requirements supported with concrete evidence → Yes;
  some supported, or all supported but vaguely → Yes + partial; none → No.
- `test_case_achieves`: final steps verify all requirements → Yes; some, or all but
  superficially → Yes + partial; none → No.

Multi-requirement records are the natural way to produce the partial tier. Only full-shape
outputs carry `partial`; minimal answer keys do not, so record the intent in
`description.md`.

## Class definitions

- **known_good / full-green** — every objective `Yes`, no partials. The test case is
  executable, well-sequenced, and its expected results prove the requirement.
- **known_good / yes-partial** — `Overall_Verdict = Yes` but at least one objective is a
  partial-Yes (typically a two-requirement test that covers one requirement thoroughly and
  the other thinly).
- **known_bad** — at least one mandatory objective is `No`, with a deficiency **visible in
  the text**. Name it in `primary_failure`.

### Failure modes to draw on
- `expected_result_support` — expected results are unmeasurable ("system works
  correctly", "no issues observed").
- `expected_result_spec_align` — results verify none of the conditions the requirement
  states.
- `test_case_achieves` — the test sets up and exercises the behavior but never checks the
  outcome; the last step is an action, not a verification.
- `test_case_logical_sequence` — verification precedes the action, or a step depends on
  state a later step establishes.
- `test_case_setup_clarity` — prerequisites are missing or contradictory (no account
  state, no device mode, no starting data). Advisory: on its own it does **not** make
  `Overall_Verdict` a `No`. Include a few such rows so the exclusion is exercised.

Distribute failures across the objectives so per-rubric metrics have signal.

## Sample size

Same regimes as `generate-rtm-dataset`: `n ≈ 0.96 / ε²` per class for a 95% CI on overall
accuracy (±10% → 96/class; ±7% → 196; ±5% → 384); ≥30 examples per active
(objective × verdict) cell for per-rubric metrics. **Default: 100 known-good + 100
known-bad**, with the known-goods split roughly 60/40 full-green to yes-partial.
Below 50/class, exploratory only. Size off `n_scored` — `scripts/sample_size.py`.

**One row per unique test case.** Reusing a test case inflates the design effect.

## Procedure

1. `DIR=$(uv run python -m qaai.dataset_studio new --type test_case --quiet)`
2. State the product concretely.
3. For each record write the upstream requirement(s) first, then the test case that does
   (or deliberately fails to do) what they ask. Use the numbered
   `Step: N. ...` / `ExpectedResult: N. ...` convention.
4. **No templated steps.** "Execute primary action specified in requirement. Verify
   success." is what got a previous dataset discarded at kappa 0.000.
5. Derive each objective's verdict from what the test case actually contains, then set
   `Overall_Verdict` from the mandatory objectives.
6. Write the two JSONL files into `$DIR`, row-aligned.
7. `uv run python -m qaai.dataset_studio sync-outputs "$DIR"`
8. `uv run python -m qaai.dataset_studio validate "$DIR"` — **must exit 0.**
9. Fill in `description.md`, including how you handled the `test_case_setup_clarity`
   drift.
10. Tell the user to review: `uv run python -m qaai.dataset_studio edit "$DIR"`.

## Verification checklist

- All `test_id` / `req_id` values unique; one row per unique test case.
- Every record has ≥1 upstream requirement; some have several.
- No objective is ever `N-A`.
- `Overall_Verdict = Yes` iff every mandatory objective is `Yes`.
- `test_case_setup_clarity` is never the sole `No`.
- Known-bad rows name a `primary_failure` that is genuinely visible in the text.
- Failures spread across the five objectives.
- Row *i* of the three files describes the same test case.
- `description.md` counts match the actual row counts.
- `dataset_studio validate` exits 0.

## Out of scope

- Running the pipeline or computing metrics — the qaai-mlflow-eval plugin.
- Suite-level datasets — `generate-rtm-dataset`.
- Hazard datasets — `generate-hazard-dataset`.
