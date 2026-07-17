# RTM Study Dataset — HealthCore EHR (seed batch)

## Domain & product
- Domain: Medical device software (SaMD / health software, IEC 82304)
- Product: HealthCore EHR — clinical ordering, medication management, and access control
- Compliance frame: IEC 62304, ISO 14971, FDA 21 CFR 820.30
- Continues the `REQ-HC-*` / `TC-HC-*` series of `tests/fixtures/gold/gold_dataset_labeled.jsonl`
  (which occupies REQ-HC-001..009); this set starts at REQ-HC-010.

## Why this set exists

It replaces `eval/datasets/test_suite` (800 rows), whose labels are **not grounded in its
content**. That set's test steps are templated placeholders — *"Execute primary action
specified in requirement. Verify success."* — yet rows are labelled `Yes` by fiat. The
reviewer rejected all 40 piloted records (accuracy 0.500, **kappa 0.000**) and was **right**
to: on a row labelled `Yes` it returned *"M4 = No: automatic import of heart rate, blood
pressure, and SpO2 is not covered"* and *"M5 = No: generic vocabulary rather than the
requirement's terms"*. You cannot measure accuracy against labels that are wrong.

The governing rule here: **a row earns `Yes` only if a competent reviewer reading it would
agree**, and a known-bad row must carry a **real deficiency visible in the text** — not
merely a missing row.

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
- Input shape: `tests/fixtures/gold/gold_dataset_labeled.jsonl` (requirement + test_cases + labels)
- Eval spec (authoritative rubric): `eval/specs/test_suite_reviewer.yaml` — **M1–M5 mandatory
  + R6 advisory**, 6 findings. (The `generate-rtm-dataset` skill's prose says 5 and points at
  `autoqa/*` paths; it is stale. The spec and the live Pydantic models win.)
- Output shape: `qaai/agents/test_suite_reviewer/core.py::RTMReviewState`

## Files
- `source_gold.jsonl` — hand-authored source of truth (requirement + test_cases + labels)
- `eval_inputs.jsonl` / `eval_outputs.jsonl` / `eval_outputs_labels.jsonl` — generated from it
  by `scripts/convert_to_eval.py gold --synthesize-outputs`. `eval_outputs*.jsonl` are the
  **answer key** (actual); predictions come only from `--mode run`.

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
1. `unique requirement text == n_records` (design effect 1).
2. Rubric rule holds for every row: `Overall_Verdict == Yes` iff all M1–M5 ∈ {Yes, N-A}.
3. M1/M4/M5 are never N-A.
4. Round-trip: `outputs_to_labels(spec, eval_outputs) == eval_outputs_labels`.
5. Pilot gate: `cohen_kappa > 0` and `prevalence_pred_positive > 0`. A constant predictor
   means labels and content disagree — fix the data, do not scale up.
