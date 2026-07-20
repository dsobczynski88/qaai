---
name: generate-hazard-dataset
description: |
  Generate a labelled dataset for evaluating the QAAI hazard_risk_reviewer LangGraph as a
  binary classifier. Each input is one ISO 14971 / IEC 62304 hazard-register row
  (HazardRowWithTraceMatrix — SHA columns plus traced requirements, test cases, design
  docs, and user needs); each label row carries the overall verdict and the H1-H6
  mandatory rubric plus the R7 advisory cell. Records are labelled "known good"
  (Overall_Verdict=Yes, including the H5=N-A non-software-cause variant) or "known bad"
  (Overall_Verdict=No with a designated primary_failure). Writes actual_inputs.jsonl +
  actual_labels.jsonl + description.md into a timestamped folder under
  eval/datasets/hazard/, derives actual_outputs.jsonl, and must pass
  `dataset_studio validate`. Default domain is medical-device software (SiMD / SaMD /
  IEC 62304 / IEC 82304). Use when the user asks to synthesize a labelled hazard dataset,
  build eval data for the H1-H6 rubric, or evaluate the hazard reviewer as a classifier.
  Siblings: generate-rtm-dataset, generate-tc-dataset, dataset-review.
---

# generate-hazard-dataset

Authors records for `qaai.agents.hazard_risk_reviewer`. Live schema:
`qaai/agents/hazard_risk_reviewer/core.py`. Authoritative rubric:
`eval/specs/hazard_risk_reviewer.yaml`. Per-dimension criteria:
`qaai/prompts/hazard_h1/` … `hazard_h6/`, `hazard_r7/` — **read the latest version of
each before labelling**; they carry the actual Yes/No conditions.

This skill is a **content generator**. It writes files; it does not invoke the graph.

## ⚠ The rubric is SEVEN cells, not five

Any earlier description of this dataset as H1–H5 is **stale**. The live
`HazardFinding.code` literal and the eval spec both declare:

| Code | Dimension | May be N-A | Gates the verdict |
|---|---|---|---|
| H1 | Hazard Record Completeness and Semantic Integrity | no | yes |
| H2 | Software Contribution and Cause Coverage | no | yes |
| H3 | Pre-Mitigation Risk and Exploitability Characterization | no | yes |
| H4 | Risk Control Identification, Allocation, and Coverage | no | yes |
| H5 | Verification Depth and Hazard-Path Effectiveness | **yes** | yes |
| H6 | Residual Risk Closure and Acceptability Decision | no | yes |
| R7 | HSHA Update and Newly Identified Hazard / Hazardous Situation Capture | **yes** | **no — advisory** |

The `dimension` strings above are a `Literal` in the model — copy them **verbatim** into
any full-shape output.

`Overall_Verdict = "Yes"` iff every **mandatory** finding (H1–H6) is in `{Yes, N-A}`.
**An R7 of `No` never flips it** — the same rule as R6 in the RTM reviewer. The final
assessor computes this in code, never the LLM.

**Only H5 may be `N-A`**, in the narrow case where `software_related_causes` indicates no
software cause, leaving no software hazard path to verify.

## Persona

A **Senior Medical Device Safety Software Architect** and independent hazard-analysis
reviewer specializing in ISO 14971, IEC 62304, and IEC 61508 — the voice the H1–R7
prompts themselves adopt.

## Inputs to gather

If unspecified, ask once with `AskUserQuestion`: **domain + product** (default
medical-device software), **sample count**, **class balance** (default 50/50), and the
**output folder** — default whatever `dataset_studio new --type hazard --quiet` prints.

## Files to write

- **`actual_inputs.jsonl`** — one `HazardRowWithTraceMatrix` per line, under a `hazard`
  key. **Use snake_case field names, not the Excel aliases** (`hazard_id`, not
  `"SHA ID Number"`); the model accepts both, but the editor and every other tool key on
  field names.

  ```json
  {"hazard": {
     "hazard_id": "SHA-042",
     "hazardous_situation_id": "HS-012",
     "hazard": "Unintended medication dose delivered",
     "hazardous_situation": "Pump delivers a bolus while the pump is in standby",
     "function": "Bolus delivery control",
     "hazardous_sequence_of_events": "1. Clinician arms a bolus. 2. Pump enters standby. 3. Standby transition fails to clear the armed-bolus flag. 4. Bolus is delivered unattended.",
     "software_related_causes": "Standby state transition does not reset the armed-bolus flag (state-machine defect).",
     "harm": "Opioid overdose; respiratory depression",
     "severity": "Critical",
     "probability_of_harm_pre_mitigation": "Occasional",
     "exploitability_pre_mitigation": "N/A - no network exposure",
     "initial_risk_rating": "High",
     "risk_control_measures": "REQ-PUMP-118: standby transition SHALL clear any armed bolus and log the clear.",
     "demonstration_of_effectiveness": "TC-PUMP-118-A, TC-PUMP-118-B",
     "severity_of_harm_post_mitigation": "Critical",
     "probability_of_harm_post_mitigation": "Remote",
     "final_risk_rating": "Low",
     "residual_risk_acceptability": "Acceptable per GQP-10-02; residual risk outweighed by clinical benefit.",
     "requirements_traceability": {
        "requirements": [{"req_id": "REQ-PUMP-118", "text": "..."}],
        "test_cases": [{"test_id": "TC-PUMP-118-A", "description": "...", "setup": "...", "steps": "...", "expectedResults": "..."}],
        "design_docs": [{"doc_id": "DD-PUMP-07", "name": "...", "description": "..."}],
        "user_needs": [], "system_requirements": []
     }}}
  ```

  Severity / probability / rating fields are **free-text strings**, not enums — use a
  consistent vocabulary within one dataset and state it in `description.md`.

- **`actual_labels.jsonl`**:
  ```json
  {"Overall_Verdict": "No", "H1": "Yes", "H2": "Yes", "H3": "Yes", "H4": "No",
   "H5": "Yes", "H6": "Yes", "R7": "Yes",
   "class": "known_bad", "primary_failure": "H4"}
  ```
  R7 is optional in the labels (advisory), but include it when the record carries enough
  context to grade it.

- **`actual_outputs.jsonl`** — **do not hand-write**; run
  `uv run python -m qaai.dataset_studio sync-outputs <dir>`.

- **`description.md`** — replace every `TODO`.

## What each dimension actually checks

Grade from the record's own fields. Summarized here; the prompts are authoritative.

- **H1 Completeness / semantic integrity** — the identifiers, hazard, hazardous
  situation, sequence of events, and harm are populated, non-placeholder, and form a
  coherent causal chain: cause → sequence → hazardous situation → harm. Strict enough to
  catch broken hazard logic, not so strict that wording nits fail a clear chain.
- **H2 Software contribution** — whether software contributes is documented, and if so
  the mechanisms are covered (logic/state/data/timing/failure behavior, and
  cybersecurity misuse or compromise where relevant), consistent with the design docs.
- **H3 Pre-mitigation risk** — `severity`, `probability_of_harm_pre_mitigation`, and
  `initial_risk_rating` are populated and non-placeholder; an `SRA Link` is cited when a
  cybersecurity cause is implied; and the rating is a reasonable joint reading of
  severity, probability, and exploitability.
- **H4 Risk control identification** — every software cause has a risk control, the
  controls are allocated to concrete requirements, and `risk_control_measures` cites
  them traceably.
- **H5 Verification depth** — `demonstration_of_effectiveness` traces to tests that
  exercise the **hazard path**, not merely the happy path of the control requirement.
  `N-A` only when there is no software cause to verify.
- **H6 Residual risk closure** — post-mitigation severity / probability / final rating
  are populated and consistent with the controls and their verification, and
  `residual_risk_acceptability` gives a real rationale rather than a bare "acceptable".
- **R7 HSHA update (advisory)** — newly identified hazards or hazardous situations are
  captured and cross-referenced (`new_hs_reference`).

## Class definitions

- **known_good / full-green** — H1–H6 all `Yes`. Every field is populated, the causal
  chain holds, controls trace to requirements, verification exercises the hazard path,
  and residual risk is closed with a real rationale.
- **known_good / H5-N-A** — a hazard with **no software-related cause** (hardware,
  mechanical, or purely use-related). `H5 = "N-A"`, the rest `Yes`, verdict `Yes`. Include
  a handful: N-A handling is a real behavior worth measuring, and H5 is the only cell that
  can exercise it.
- **known_bad** — at least one of H1–H6 is `No`, with a deficiency **visible in the
  record**. Name it in `primary_failure`.

### Failure modes to draw on
- **H1** — the sequence of events does not actually lead to the stated harm, or the
  hazardous situation is a restatement of the hazard.
- **H2** — `software_related_causes` says "software error" with no mechanism, while the
  design docs show a specific state/timing behavior.
- **H3** — `initial_risk_rating` is `Low` beside `severity: Catastrophic` and
  `probability: Probable`; or a cyber cause with no SRA link.
- **H4** — a documented software cause has no corresponding control, or
  `risk_control_measures` is prose with no requirement id.
- **H5** — `demonstration_of_effectiveness` cites a test that verifies the control
  requirement's happy path but never drives the hazardous sequence.
- **H6** — `final_risk_rating` improves with no corresponding control, or
  `residual_risk_acceptability` is a bare "Acceptable" with no rationale.
- **R7** — a new hazardous situation is implied but never cross-referenced. Remember an
  R7-only `No` still leaves `Overall_Verdict = Yes`; include a couple of these
  deliberately, as they are exactly the rows that catch a scorer wrongly gating on R7.

Distribute failures across H1–H6.

## Cross-rubric consistency

Hazard cells are **coupled** — let them follow from the content rather than assigning
them independently:

- No software cause (H5 = N-A) usually means H2 is graded on the *absence* being properly
  documented, not on mechanism coverage.
- If H4 is `No` because a cause has no control, H5 usually cannot be `Yes` — there is
  nothing to verify — and H6 cannot honestly close.
- If H3's pre-mitigation rating is incoherent, H6's post-mitigation comparison inherits
  the incoherence.

A record whose cells contradict each other is a labelling bug, not a hard case.

## Sample size

Same regimes as the sibling skills: `n ≈ 0.96 / ε²` per class for a 95% CI on overall
accuracy (±10% → 96/class; ±7% → 196; ±5% → 384); ≥30 examples per active
(dimension × verdict) cell for per-rubric metrics — with **seven** cells that is a larger
bill than the RTM rubric.

**Default: 60 known-good (including ~10 H5-N-A) + 60 known-bad**, spread ~10 per failing
dimension across H1–H6. Below 50/class, exploratory only. Size off `n_scored` —
`scripts/sample_size.py`.

**One row per unique hazard.** Reusing a hazard inflates the design effect.

## Procedure

1. `DIR=$(uv run python -m qaai.dataset_studio new --type hazard --quiet)`
2. State the product concretely (e.g. "FluxPump 4000, a Class II PCA infusion pump, IEC
   62304 Class B") and fix the severity / probability / rating vocabulary.
3. For each record write the hazard chain first — cause → sequence → hazardous situation
   → harm — then the controls, then the traced requirements and test cases that verify
   them. The traceability matrix must reference ids that exist in the record.
4. **No placeholder register fields.** "TBD", "N/A" everywhere, or one-word entries are
   what a real H1 review rejects; if you want that failure, make it a deliberate
   known-bad and label H1 accordingly.
5. Derive each cell from the record, honour the coupling rules, then set
   `Overall_Verdict` from H1–H6 only.
6. Write the two JSONL files into `$DIR`, row-aligned.
7. `uv run python -m qaai.dataset_studio sync-outputs "$DIR"`
8. `uv run python -m qaai.dataset_studio validate "$DIR"` — **must exit 0.**
9. Fill in `description.md`, including the rating vocabulary and any grading rulings.
10. Tell the user to review: `uv run python -m qaai.dataset_studio edit "$DIR"`.

## Verification checklist

- All `hazard_id` values unique; one row per unique hazard.
- Register fields use snake_case, not Excel aliases.
- Every record's `requirements_traceability` cites ids that appear in the record.
- Labels carry H1–H6; R7 included where gradable.
- `N-A` appears **only** on H5 (and optionally R7) — never on H1–H4 or H6.
- `Overall_Verdict = Yes` iff H1–H6 ⊆ {Yes, N-A}; R7 excluded.
- At least one record has R7 = `No` with `Overall_Verdict` still `Yes`.
- At least a few records are H5 = `N-A` with no software cause.
- Known-bad rows name a `primary_failure` genuinely visible in the record.
- Cells are mutually consistent (see "Cross-rubric consistency").
- Row *i* of the three files describes the same hazard.
- `dataset_studio validate` exits 0.

## Out of scope

- Running the pipeline or computing metrics — the qaai-mlflow-eval plugin.
- Loading hazards from Excel — that is `qaai/agents/hazard_risk_reviewer/loader.py` and
  the `/api/v1/hazard-risk-review` endpoint.
- Requirement- or test-case-level datasets — `generate-rtm-dataset`,
  `generate-tc-dataset`.
