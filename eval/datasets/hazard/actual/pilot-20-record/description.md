# Hazard Coverage Study Dataset — FluxPump 4000 (seed batch)

**Revision 1** of the Hazard Coverage Reviewer answer key —
`eval/datasets/hazard/actual/pilot-20-record/`.

This is the hazard-reviewer counterpart of the committed RTM seed at
`eval/datasets/test_suite/actual/pilot-20-record/`, produced through the identical
`qaai.dataset_studio` flow (`new → author → sync-outputs → validate → edit`) so every
editing and evaluation operation is the same across the two reviewers.

## Domain & product
- Domain: Medical-device software (SiMD — software in a medical device)
- Product: **FluxPump 4000**, a Class II patient-controlled-analgesia (PCA) infusion pump,
  IEC 62304 software safety Class B
- Compliance frame: ISO 14971 (risk management), IEC 62304 (software lifecycle),
  IEC 82304-1, FDA 21 CFR 820.30
- ID families: `HAZ-PUMP-1xx` / `HS-PUMP-1xx` (hazards) · `REQ-PUMP-1xx` (requirements) ·
  `TC-PUMP-1xx` / `MECH-TC-1xx` (verification) · `DD-PUMP-1x` (design docs) ·
  `FMEA-PUMP-*` · `SRA-PUMP-*` · `URRA-PUMP-*`

## Why this set exists

It is the grounded seed for evaluating the hazard reviewer as a binary classifier on
`overall_verdict`, mirroring the RTM seed's purpose. The governing rule is carried over
verbatim from the RTM pilot: **a row earns `Yes` only if a competent reviewer reading it
would agree**, and a known-bad row must carry a **real deficiency visible in the register
text** — not merely a missing field. An 800-row RTM predecessor was discarded for breaking
this rule (it scored **kappa 0.000** because its labels were not grounded in its content).
Every known-bad here names a `primary_failure` whose deficiency is legible in the hazard
record itself (see the rulings below).

## Rubric (authoritative: `eval/specs/hazard_risk_reviewer.yaml`)

Seven cells: **H1–H6 mandatory + R7 recommended**.

| Code | Dimension | May be N-A | Gates verdict |
|---|---|---|---|
| H1 | Hazard Record Completeness and Semantic Integrity | no | yes |
| H2 | Software Contribution and Cause Coverage | no | yes |
| H3 | Pre-Mitigation Risk and Exploitability Characterization | no | yes |
| H4 | Risk Control Identification, Allocation, and Coverage | no | yes |
| H5 | Verification Depth and Hazard-Path Effectiveness | **yes** | yes |
| H6 | Residual Risk Closure and Acceptability Decision | no | yes |
| R7 | HSHA Update and Newly Identified Hazard / Hazardous Situation Capture | yes | **no — advisory** |

`Overall_Verdict = "Yes"` iff every **mandatory** finding (H1–H6) ∈ {Yes, N-A}.
**R7 never flips the verdict** (mirrors the RTM reviewer's R6 advisory). Only **H5** may be
`N-A` — the narrow case of a hazard with no software-related cause, leaving no software
hazard path to verify.

## Layout & revisions

Identical convention to the RTM set. Every answer key lives in its own timestamped folder
under `<type>/actual/`:

```
eval/datasets/hazard/actual/
  pilot-20-record/            <- this revision
    actual_inputs.jsonl             graph input           (run mode)
    actual_outputs.jsonl            ANSWER KEY, output shape (derived by sync-outputs)
    actual_labels.jsonl             ANSWER KEY, flat projection
    description.md                  this file
    edits.log                       append-only audit trail
    predictions/<ts>/               runs scored against THIS revision
  <later ts>/                     <- corrections land here, not in place
```

Row *i* of the three `actual_*` files is the same hazard; that positional alignment is the
dataset's core invariant. **A revision is never edited in place** — corrections are saved as
a new timestamped sibling (the editor's *Save As*), so a scored run always keeps the exact
answer key it was scored against. `predictions/<ts>/` hangs off the revision it scored.

This set was **hand-authored**, so it carries no `source.json` (that provenance record is
written only by `dataset_studio ingest`). `actual_outputs.jsonl` is **derived from the
labels** via `sync-outputs` (the oracle/minimal shape: `hazard_assessment.overall_verdict`
plus a `mandatory_findings` list of `{code, verdict}` for H1–H6 + R7) — never hand-written,
which is what makes the answer key agree with itself (validator check `V050`).

## Class distribution
- Known good (`Overall_Verdict = Yes`): **10**
  - Full-green (H1–H6 all Yes, software-related): 8
  - H5-N-A (non-software hazard; H5 = "N-A", H1–H4/H6 Yes): 2  (HAZ-PUMP-109, -110)
- Known bad (`Overall_Verdict = No`): **10**
- Total: **20** — 20 unique hazards, one record each (design effect 1)

### Advisory R7 — grounded balanced mix (2/20 ≈ 10%, mixed classes)

Mirroring the test_suite set's treatment of its advisory R6 cell, the advisory **R7**
("HSHA / newly-identified hazardous-situation capture") carries a grounded No on **2 of 20
rows (10%)**, each visible in the record text as a newly-identified hazardous situation that
is *implied but left uncaptured* (`new_hs_reference` empty) — never a bare label flip:

- **HAZ-PUMP-108** (good, `Overall_Verdict = Yes`) — a KVO-transition-during-alarm-silence
  situation is noted but not cross-referenced. This is also the deliberate **advisory trap**:
  H1–H6 are all Yes so the verdict stays **Yes**, catching a scorer that wrongly gates on R7.
- **HAZ-PUMP-116** (bad, `Overall_Verdict = No`, primary H3) — the missing message-authenticity
  control plausibly implies a distinct new hazardous situation (acceptance of spoofed control
  commands, not merely spoofed telemetry) that the record does not capture.

R7 is `advisory_codes: [R7]` in the spec and is **excluded from `overall_verdict`**, so
neither row's verdict is affected. Distribution: **R7 = 18 Yes / 2 No.** The 400-row build
inherits this ~10% grounded-R7 pattern.

## Failure-mode distribution (known bads)
| Primary cell | Count | Records |
|---|---|---|
| H1 Completeness / Semantic Integrity | 2 | HAZ-PUMP-111, -112 |
| H2 Software Contribution / Cause Coverage | 2 | HAZ-PUMP-113, -114 |
| H3 Pre-Mitigation Risk / Exploitability | 2 | HAZ-PUMP-115, -116 |
| H4 Risk Control Identification / Allocation | 2 | HAZ-PUMP-117, -118 |
| H5 Verification Depth | 1 | HAZ-PUMP-119 |
| H6 Residual Risk Closure | 1 | HAZ-PUMP-120 |

HAZ-PUMP-117 also carries **H5 = No** as an honest cascade: cause (b) has no allocated
control, so its hazard path cannot be verified (per the skill's cross-rubric coupling rule).
Every mandatory cell H1–H6 therefore has at least one `No` example, so each is scorable as a
classifier (validator `V090`).

## Statistical posture
- **Seed batch, not the study.** n=20 gives a 95% CI of roughly ±0.22 on overall accuracy —
  directional only. Its purpose is the same single hypothesis the RTM seed tested: *does a
  grounded hazard dataset yield kappa > 0?*
- Scale target for the hazard rubric is heavier than RTM: with **seven** cells,
  ≥30 examples per active (dimension × verdict) cell is a larger bill. Per-cell counts here
  (1–2 negatives) are anecdote until scaled. See `qaai/eval/sample_size.py`
  (95%/±0.05 needs 385 at p=0.5).
- One row per unique hazard ⇒ mean cluster size 1 ⇒ design effect 1, so the i.i.d. interval
  is valid by construction.
- Labels are authored, not adjudicated by a second reviewer. At scale, adjudicate a
  stratified sample and report reviewer-vs-label Cohen's kappa.

## Rating vocabulary (fixed across the set)
Severity, probability, exploitability, and risk-rating fields are **free-text strings** in
the SHA schema; this set fixes one vocabulary so the reviewer grades consistent inputs:
- **Severity:** Negligible < Minor < Serious < Critical < Catastrophic
- **Probability of harm:** Improbable < Remote < Occasional < Probable < Frequent
- **Exploitability (cyber):** `N/A - no network exposure` | Low | Medium | High
- **Risk rating:** Acceptable < Low < Medium < High < Unacceptable

## Assumptions and rulings (why each known-bad's primary cell is a No)
- **H1** — HAZ-PUMP-111: `hazardous_sequence_of_events` is a single non-causal sentence
  ("Alarm condition occurs.") and `hazardous_situation` merely restates the hazard — no
  coherent cause→sequence→situation→harm chain. HAZ-PUMP-112: `harm` is "TBD" and the
  sequence terminates at a stale display without reaching a clinical consequence, yet
  severity is Critical.
- **H2** — HAZ-PUMP-113: `software_related_causes` is only "Software error" with no
  mechanism, while `DD-PUMP-13` documents specific fixed-point rounding/timing behaviour it
  fails to cover. HAZ-PUMP-114: the FSOE describes an unauthenticated network message
  altering safety limits, but the cause statement covers only a parsing bug and omits the
  cybersecurity/authentication misuse mechanism.
- **H3** — HAZ-PUMP-115: `initial_risk_rating` "Acceptable" is incoherent with Catastrophic
  severity × Frequent probability. HAZ-PUMP-116: a cyber cause is implied (exploitability
  High, spoofing path) but the `SRA Link` is blank, so pre-mitigation exploitability is
  uncharacterized.
- **H4** — HAZ-PUMP-117: documented cause (b) "the committed dose is not independently
  cross-checked before delivery" has no allocated control (only debounce is covered) →
  H5 cascades to No. HAZ-PUMP-118: a real control requirement exists in traceability and its
  tests exercise the hazard path (H5 = Yes), but the register's `risk_control_measures` field
  is vague prose ("Software mitigations are in place") citing no requirement id, so controls
  are not traceably allocated.
- **H5** — HAZ-PUMP-119: `demonstration_of_effectiveness` cites only a nominal
  power-on/display test; no test drives internal temperature past the 60 °C limit to
  exercise the shutdown hazard path.
- **H6** — HAZ-PUMP-120: post-mitigation probability is "TBD", the "Low" final rating is
  unsupported, and `residual_risk_acceptability` is a bare "Acceptable." with no rationale.
- **H5 = N-A** — HAZ-PUMP-109 (cracked cassette) and -110 (luer disconnect under vibration)
  have `software_related_causes = "None ..."`; controls are mechanical/procedural
  requirements that still pass H4, and there is no software hazard path to verify → H5 = N-A.
  Placeholder values ("TBD") are deliberate content deficiencies, not blank fields — they
  pass the graph's input gate (which only rejects empty required fields) and are exactly what
  the corresponding H-dimension review is expected to catch.

## Schema references
- Eval spec (authoritative rubric): `eval/specs/hazard_risk_reviewer.yaml` — H1–H6 mandatory
  + R7 recommended, 7 findings. R7 is excluded from `overall_verdict`.
- Input shape: `qaai/agents/hazard_risk_reviewer/core.py::HazardRowWithTraceMatrix`
  (24 SHA register fields, snake_case, + `requirements_traceability`). Each input row must
  populate the 15 required fields in
  `qaai/agents/hazard_risk_reviewer/constants.py::HAZARD_RISK_REVIEWER_REQUIRED_HAZARD_FIELDS`
  and carry ≥1 traced control, or the graph's `validate_hazard_inputs` gate skips it.
- Output shape: `qaai/agents/hazard_risk_reviewer/core.py::HazardReviewState` /
  `HazardAssessment`.

## Provenance
Hand-authored against the FluxPump 4000 product frame. The `actual_inputs.jsonl` /
`actual_labels.jsonl` files were authored directly; `actual_outputs.jsonl` was derived from
the labels by:

```bash
uv run python -m qaai.dataset_studio sync-outputs eval/datasets/hazard/actual/pilot-20-record
```

The `actual_*` files are the **answer key** (ACTUAL). Predictions only come from
`--mode run`, written as `predicted_*` under `predictions/<ts>/`.

## How to produce a successor
```bash
# author a new set from scratch (controlled class balance, specific failure modes)
uv run python -m qaai.dataset_studio new --type hazard

# or convert a completed review run into one pre-filled with the model's own answers
uv run python -m qaai.dataset_studio ingest logs/run-<ts> --edit
```
Then review every record in the browser and validate:
```bash
uv run python -m qaai.dataset_studio edit     eval/datasets/hazard/actual/pilot-20-record
uv run python -m qaai.dataset_studio validate eval/datasets/hazard/actual/pilot-20-record   # must exit 0
```

## Verification gates before spending a run
Mechanical checks are enforced by `dataset_studio validate` (must exit 0): row alignment
(`V002`), input model conformance (`V010`), N-A only on H5/R7 (`V031`), verdict derivation
(`V040`), answer-key self-agreement (`V050`), unique hazard ids (`V070`), both classes +
per-cell negative coverage (`V090`). Not machine-checked: grounding (a `Yes` a competent
reviewer would agree with; a `No` pointing at a visible deficiency) — that is what the
editor and a human reviewer are for — and the pilot gate `cohen_kappa > 0`, read off the
MLflow run once scored.
