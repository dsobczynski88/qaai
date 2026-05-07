---
name: generate-hazard-dataset
description: |
  Generate a synthetic dataset for evaluating the autoqa hazard_risk_reviewer
  LangGraph as a binary classifier. Produces three files — inputs.jsonl,
  outputs.jsonl, and a description.md metadata file — where each input is a
  HazardRecord (ISO 14971 hazard-register fields + traced requirements,
  test_cases, design_docs) and each output is the full HazardReviewState
  (per-requirement RequirementReview list with embedded SynthesizedAssessment,
  scalar h1..h5 findings, aggregated HazardAssessment). Records are explicitly
  labelled "known good" — sub-classified as full-green (all H1-H5 = Yes) or
  H4-NA (non-software-related hazard so H4 = "N-A") — or "known bad" with a
  designated primary_failure objective in {H1, H2, H3, H4, H5}. Default domain
  is medical-device software (SiMD / SaMD / IEC 14971 / IEC 62304 / IEC 82304);
  other domains can be requested. Includes power-analysis guidance on how many
  samples per class and per-rubric cell are needed for confident ML evaluation.
  Use when the user asks to synthesize a labelled dataset for the
  hazard_risk_reviewer, build training/eval data for the H1-H5 rubric, mock
  hazard pipeline inputs at scale, or evaluate the hazard_risk_reviewer as a
  classifier. Sibling to generate-rtm-dataset and generate-tc-dataset.
---

# generate-hazard-dataset

Generate a labelled, schema-conformant dataset whose inputs match the
`hazard_risk_reviewer` graph's expected entry state and whose outputs match the
serialized `HazardReviewState`. The dataset is binary-labelled (known good /
known bad) with two known-good sub-classes (full-green, H4-NA) and a designated
primary failure dimension on every known-bad, so it can drive ML evaluation of
the LangGraph as a classifier.

This skill is a **content generator**. It writes three files to disk; it does
not invoke the LangGraph pipeline. To compare these synthetic outputs to actual
pipeline runs, point `scripts/run_hazard_pipeline.py` at the generated
`inputs.jsonl` and compare its produced `outputs.jsonl` against the synthesized
one. Note: unlike RTM and TC, hazard runs are not yet wired into the
`jsonl_recorders` pytest fixture in `tests/conftest.py` — use the script.

## Sibling skills

This is the hazard-risk-reviewer counterpart of `generate-rtm-dataset` and
`generate-tc-dataset`. The three target different graphs:

| | `generate-rtm-dataset` (test_suite_reviewer) | `generate-tc-dataset` (test_case_reviewer) | `generate-hazard-dataset` (this skill) |
|---|---|---|---|
| Input shape | one Requirement + List[TestCase] | one TestCase + List[Requirement] | one HazardRecord (24 register fields + List[Requirement] + List[TestCase] + List[DesignDocument]) |
| Output rubric | M1-M5 mandatory (Yes/No/N-A) | 5 review objectives (Yes/No + partial flag) | H1-H5 mandatory (Yes/No; H4 may be N-A) — plus an embedded M1-M5 SynthesizedAssessment per traced requirement |
| Sub-classes on known-good | n/a | full-green vs yes-partial | full-green vs H4-NA |
| Reference fixture | `tests/fixtures/gold_dataset.jsonl` | `tests/fixtures/gold_dataset-tc.jsonl` | `tests/fixtures/sample_hazard.json` |
| Pydantic source-of-truth | `autoqa/components/test_suite_reviewer/core.py` | `autoqa/components/test_case_reviewer/core.py` | `autoqa/components/hazard_risk_reviewer/core.py` |

If the user wants requirement-coverage data (one requirement, many TCs),
redirect to `generate-rtm-dataset`. If the user wants per-test-case checklist
data (one TC, many requirements), redirect to `generate-tc-dataset`. This skill
is hazard-risk-reviewer specific.

## Persona

Adopt the voice of a **Principal V&V Architect for safety-critical medical
device software**, with deep working knowledge of:

- ISO 14971 (risk management for medical devices) — hazardous situation chains,
  pre/post-mitigation risk characterization, residual risk acceptability
- IEC 62304 (medical device software lifecycle) — software safety
  classification, risk control measures, verification depth
- IEC 82304-1 (health software product safety)
- IEC 61508 (functional safety) — for non-medical safety-critical domains
- IEEE 29148 (requirements engineering)
- ISO 26262 (automotive) / DO-178C (aerospace) — for non-default domains
- Black-box / white-box / fault-injection / boundary / negative test design
- Hazard-register conventions (FSOE = "Final Sequence Of Events" or
  "hazardous sequence of events"), FMEA traceability, SRA/URRA linkage

## When to invoke

- "Generate a labelled hazard-risk dataset / training set / gold dataset"
- "Create N known-good and M known-bad hazard records for evaluating the
  hazard pipeline as a classifier"
- "Synthesize fixtures matching `tests/fixtures/sample_hazard.json` shape, but
  at scale, with deliberate H1 / H2 / H3 / H4 / H5 failure modes"
- "Mock hazard pipeline outputs for ML evaluation / power analysis / metric
  calibration on the H1-H5 rubric"
- User mentions ROC, F1, accuracy, calibration, or sample-size estimation in
  the context of `hazard_risk_reviewer` / `HazardReviewerRunnable`
- User references `tests/fixtures/sample_hazard.json` or
  `tests/fixtures/generated/hazard_dataset/inputs.jsonl` and asks for a larger
  / labelled / per-class version
- User asks for examples per failure dimension (e.g. "10 hazards that fail
  H4 because of missing fault-injection tests")

## Inputs to gather before generating

If the user has not specified, ask once with `AskUserQuestion`:

1. **Domain** — default `medical-device software (SaMD / SiMD / IEC 82304 /
   IEC 62304 / ISO 14971)`. Other accepted values: `aerospace (DO-178C /
   ARP4761A)`, `automotive (ISO 26262)`, `industrial (IEC 61508)`,
   `enterprise SaaS`. Choose ONE realistic product within the domain (e.g.
   PCA infusion pump SiMD, glucose-monitor SaMD, EHR clinical-decision-support).
2. **Total sample count** — see "Sample-size guidance" below for recommended
   defaults.
3. **Class balance** — default 50/50 known-good / known-bad. Within
   known-goods, default sub-balance is 70% full-green / 30% H4-NA. The user
   can override.
4. **Failure-dimension distribution within known-bads** — default uniform
   across H1, H2, H3, H4, H5 (20% each). Can be skewed per request.
5. **Output directory** — default `tests/fixtures/generated/hazard_dataset/`
   (creates it if missing). Files: `inputs.jsonl`, `outputs.jsonl`,
   `description.md`.

## Schemas (single source of truth — DO NOT duplicate)

The skill MUST conform to the live Pydantic models. Read them before
generating to make sure field names / types / literals are exact:

- **Inputs (one record per line of `inputs.jsonl`)** — the full `HazardRecord`
  schema (24 hazard-register scalar string fields + `requirements` (≥1) +
  `test_cases` + `design_docs`) plus four label fields used by the dataset.
  The label fields are NOT part of `HazardRecord` and must be stripped before
  piping into the pipeline:
  ```json
  {
    "hazard_id": "...", "hazardous_situation_id": "...",
    "hazard": "...", "hazardous_situation": "...",
    "function": "...", "ots_software": "...",
    "hazardous_sequence_of_events": "...",
    "software_related_causes": "...",
    "harm_severity_rationale": "...", "harm": "...",
    "severity": "...",
    "exploitability_pre_mitigation": "...",
    "probability_of_harm_pre_mitigation": "...",
    "initial_risk_rating": "...",
    "risk_control_measures": "...",
    "demonstration_of_effectiveness": "...",
    "severity_of_harm_post_mitigation": "...",
    "exploitability_post_mitigation": "...",
    "probability_of_harm_post_mitigation": "...",
    "final_risk_rating": "...",
    "new_hs_reference": "...", "sw_fmea_trace": "...",
    "sra_link": "...", "urra_item": "...",
    "residual_risk_acceptability": "...",
    "requirements": [{"req_id": "...", "text": "..."}, ...],
    "test_cases":   [{"test_id": "...", "description": "...",
                      "setup": "...", "steps": "...",
                      "expectedResults": "..."}, ...],
    "design_docs":  [{"doc_id": "...", "name": "...",
                      "description": "..."}, ...],

    "expected_overall_verdict": "Yes" | "No",
    "primary_failure":          "H1" | "H2" | "H3" | "H4" | "H5" | null,
    "expected_h4_na":           true | false,
    "description":              "<design-intent explainer>"
  }
  ```
  `primary_failure` is `null` for all known-good records. `expected_h4_na`
  is `true` only for the H4-NA known-good sub-class (and in that case
  `software_related_causes` must be empty/"none/not applicable" so the
  pipeline emits H4 = "N-A").

- **Outputs (one record per line of `outputs.jsonl`)** — full
  `HazardReviewState` serialized. Source of truth:
  `autoqa/components/hazard_risk_reviewer/core.py`. Critical sub-models:
  - `HazardRecord` (echoed back as `state["hazard"]`)
  - `RequirementReview` (one per traced requirement; carries
    `synthesized_assessment: SynthesizedAssessment` from
    `autoqa/components/test_suite_reviewer/core.py` plus
    `decomposed_requirement`, `test_suite`, `coverage_analysis`)
  - `HazardFinding` (`code: Literal["H1","H2","H3","H4","H5"]`, `dimension`,
    `verdict: Literal["Yes","No","N-A"]`, `rationale`, `cited_req_ids`,
    `cited_test_case_ids`, `unblocked_items`)
  - `HazardAssessment` (`hazard_id`, `overall_verdict: Literal["Yes","No"]`,
    `mandatory_findings` (exactly 5, H1..H5 in order), `comments`,
    `clarification_questions`)
  - `HazardReviewState` carries scalar `h1_finding`..`h5_finding` slots
    plus `requirement_reviews` and `hazard_assessment`.

- **Rubric semantics** — read the prompts to mirror the verdict criteria:
  - `autoqa/prompts/hazard_h1_evaluator-v1.jinja2` — Hazard Statement
    Completeness (Yes/No)
  - `autoqa/prompts/hazard_h2_evaluator-v1.jinja2` — Pre-Mitigation Risk
    (Yes/No)
  - `autoqa/prompts/hazard_h3_evaluator-v1.jinja2` — Risk Control Adequacy
    (Yes/No), keys off **M1=Yes** in each per-requirement
    SynthesizedAssessment
  - `autoqa/prompts/hazard_h4_evaluator-v1.jinja2` — Verification Depth
    (Yes/No/**N-A**), keys off **M2 ∈ {Yes,N-A}** AND
    **M3 ∈ {Yes,N-A}** in each per-requirement SynthesizedAssessment;
    N-A iff `software_related_causes` indicates none
  - `autoqa/prompts/hazard_h5_evaluator-v1.jinja2` — Residual Risk Closure
    (Yes/No)
  - `autoqa/prompts/hazard_final_assessor-v1.jinja2` — LLM writes only
    `comments` + `clarification_questions`; `mandatory_findings` and
    `overall_verdict` are computed deterministically by
    `_FinalAssessorNode`.
  - `autoqa/prompts/synthesizer-v6.jinja2` — embedded M1-M5 rubric for each
    `RequirementReview.synthesized_assessment`.

- **Final-assessor invariant** (deterministic — emit it exactly):
  ```
  hazard_assessment.overall_verdict = "Yes"
    iff every f in mandatory_findings has f.verdict in {"Yes","N-A"}
  else "No"
  ```
  Only H4 may be `"N-A"`. H1, H2, H3, H5 ∈ {`"Yes"`, `"No"`} only.

## Class definitions

### Known good (label = 1, overall_verdict = "Yes")

Two sub-classes:

**Full-green** — software-related hazard whose H1-H5 findings are all `"Yes"`:
- H1 Yes: hazard / hazardous_situation / FSOE / function / harm populated
  and form a traceable causal chain; severity rationale substantive and
  consistent with assigned severity.
- H2 Yes: severity, exploitability_pre_mitigation,
  probability_of_harm_pre_mitigation, initial_risk_rating populated;
  rationale substantive; rating consistent with severity × probability.
- H3 Yes: every step in `hazardous_sequence_of_events` AND every entry in
  `software_related_causes` is controlled by ≥1 requirement whose embedded
  RTM `synthesized_assessment.mandatory_findings[M1].verdict == "Yes"`.
- H4 Yes: every controlling requirement has M2 ∈ {Yes, N-A} AND
  M3 ∈ {Yes, N-A} in its embedded SynthesizedAssessment (so the suite
  exercises validation/error surfaces and threshold/limit/role-transition
  boundaries where applicable).
- H5 Yes: post-mitigation severity / exploitability / probability /
  final_risk_rating populated; sw_fmea_trace, sra_link, urra_item
  populated; probability downgrade supported by H4=Yes verification.
- `expected_h4_na = false`. `primary_failure = null`.

**H4-NA** — non-software-related hazard whose H4 finding is `"N-A"`:
- `software_related_causes` set to `"None"` / `"Not applicable — mechanical
  cause only"` (or equivalent). Hazard itself is real (severity / probability
  populated); risk controls cited are mechanical / procedural fail-safes
  rather than software requirements.
- H1, H2, H3 = `"Yes"` (controls exist; FSOE steps are mechanical, traced to
  non-software requirements that still pass M1=Yes).
- H4 = `"N-A"` — pipeline maps the no-software-cause condition to N-A.
- H5 = `"Yes"` — post-mitigation supported by H4=N-A path (the residual-risk
  closure reasoning explicitly notes that software-verification is not the
  closure mechanism).
- `expected_h4_na = true`. `primary_failure = null`.

In both sub-classes, every `RequirementReview.synthesized_assessment` must
have `overall_verdict == "Yes"` (every M1-M5 finding ∈ {Yes, N-A}).

### Known bad (label = 0, overall_verdict = "No")

At least one mandatory hazard finding has `verdict == "No"`. Encode the
deliberate failure dimension in `primary_failure ∈ {H1, H2, H3, H4, H5}`. Per
dimension:

- **H1 fail (Hazard Statement Completeness)**
  - `hazardous_sequence_of_events` is empty / "TBD" / a single non-causal
    sentence (chain broken).
  - **OR** `hazardous_situation` does not connect to `harm` (semantic gap —
    e.g. situation describes a setup state but never reaches a clinical
    consequence).
  - **OR** `harm_severity_rationale` is blank or contradicts `severity`
    (e.g. severity = "Catastrophic" but rationale = "minor inconvenience").
  - Cite the specific empty / incoherent fields verbatim in the expected
    `h1_finding.rationale`.

- **H2 fail (Pre-Mitigation Risk)**
  - `initial_risk_rating` inconsistent with severity × probability ×
    exploitability — e.g. Catastrophic × Probable but rated "Acceptable",
    or Catastrophic × Frequent but rated "Tolerable".
  - **OR** ≥1 of `severity`, `probability_of_harm_pre_mitigation`,
    `exploitability_pre_mitigation` is "TBD" / blank.
  - **OR** `harm_severity_rationale` is one-word / non-substantive
    ("Severe.").
  - The expected `h2_finding.rationale` cites the inconsistent field pair
    or the missing field.

- **H3 fail (Risk Control Adequacy)**
  - At least one verbatim-quotable step in `hazardous_sequence_of_events`
    has no controlling requirement — e.g. FSOE step 3 describes "UI thread
    fails to silence the nominal-rate display" but no requirement covers UI
    behaviour.
  - **OR** at least one entry in `software_related_causes` has no
    controlling requirement — e.g. `software_related_causes` lists
    "missing range check on commanded_rate" but no requirement covers
    input validation.
  - **OR** a controlling requirement has `M1 == "No"` in its embedded
    `synthesized_assessment` (its TC suite has no positive functional
    coverage of the spec).
  - The expected `h3_finding.unblocked_items` MUST be a non-empty list of
    verbatim quotes from `hazardous_sequence_of_events` and / or
    `software_related_causes` — substring-checkable against the source
    fields.

- **H4 fail (Verification Depth)** — hazard MUST be software-related (so
  H4 ≠ "N-A"). Two sub-paths, captured separately for power-analysis:
  - **M2=No path**: at least one controlling requirement has `M2 == "No"`
    in its embedded SynthesizedAssessment — its TC suite has no negative /
    fault-injection / error-surface test. Design the requirement around an
    explicit error / validation surface (e.g. "shall reject commanded rates
    above 200 mL/hr") and include only happy-path TCs.
  - **M3=No path**: at least one controlling requirement has `M3 == "No"`
    — its TC suite has no boundary test at the threshold / limit /
    role-transition. Design the requirement around an explicit numeric
    threshold (e.g. "200 ms heartbeat latency") and include only nominal-
    load TCs.
  - The other H findings remain `"Yes"` (isolate the H4 failure). Expected
    `h4_finding.unblocked_items` lists the controlling-requirement IDs
    whose negative / boundary verification is missing.

- **H5 fail (Residual Risk Closure)** — keep H1-H4 = `"Yes"` so this is
  *isolated* H5; cascading from H3=No to H5 implicit-No is allowed in the
  corpus but should not be the dominant pattern.
  - `severity_of_harm_post_mitigation` / `probability_of_harm_post_mitigation`
    / `exploitability_post_mitigation` / `final_risk_rating` is blank /
    "TBD".
  - **OR** ≥1 of `sw_fmea_trace`, `sra_link`, `urra_item` is blank.
  - **OR** the post-mitigation probability claims a downgrade
    (e.g. Probable → Remote) but `demonstration_of_effectiveness` does
    not link to test cases that actually exercise the hazardous path
    (i.e. the H4 verification basis for the downgrade is absent in
    practice even though H4 itself is rated Yes for the corpus's purposes).
  - **OR** `residual_risk_acceptability` is empty / vacuous ("Acceptable.").

Distribute failures roughly evenly across H1-H5 so per-rubric-cell
metrics have signal — do not let any single dimension dominate ≥40% of
known-bads unless the user asked for that distribution.

## Cross-rubric consistency rules (enforce or the dataset becomes self-contradictory)

The hazard pipeline embeds an entire `test_suite_reviewer` subgraph per
traced requirement. The synthesised `outputs.jsonl` therefore expresses
*two* coupled rubrics — the H1-H5 hazard-level rubric AND the M1-M5
per-requirement rubric — and must keep them mutually consistent:

- **For H4-NA known-good**: `software_related_causes` semantically empty
  ("None / mechanical only") AND `h4_finding.verdict == "N-A"` AND every
  `requirement_reviews[i].synthesized_assessment.overall_verdict == "Yes"`.
- **For H3 known-bad**: every entry in `h3_finding.unblocked_items` is a
  literal substring of `hazard.hazardous_sequence_of_events` and / or
  `hazard.software_related_causes` — verifiable by string search, not just
  paraphrase.
- **For H4 known-bad via M2=No**: the failing requirement's `text` must
  describe an error / validation / fault surface (so the embedded RTM
  synthesizer cannot legitimately set M2 = "N-A"). The failing requirement's
  embedded `synthesized_assessment.mandatory_findings[M2].verdict == "No"`,
  with `cited_test_case_ids = []` and `uncovered_spec_ids` populated
  appropriately.
- **For H4 known-bad via M3=No**: the failing requirement's `text` must
  name an explicit threshold / limit / role-transition (so M3 cannot
  legitimately be "N-A"). The failing requirement's embedded
  `synthesized_assessment.mandatory_findings[M3].verdict == "No"`.
- **For H3 known-bad with M1=No path**: the failing requirement's embedded
  `synthesized_assessment.mandatory_findings[M1].verdict == "No"` AND the
  controlling-requirement reference in `h3_finding.cited_req_ids` includes
  it.
- `hazard_assessment.mandatory_findings` is **always** length 5, codes in
  fixed order `["H1","H2","H3","H4","H5"]`, dimensions in fixed order
  `["Hazard Statement Completeness","Pre-Mitigation Risk","Risk Control
  Adequacy","Verification Depth","Residual Risk Closure"]`.
- `requirement_reviews` length == `len(hazard.requirements)`.
- `hazard_assessment.hazard_id == hazard.hazard_id`.
- The deterministic `overall_verdict` invariant
  (Yes iff all findings ∈ {Yes, N-A}) holds on every output record. Compute
  it programmatically — do NOT have the LLM "decide" the overall verdict.

## Sample-size guidance (statistical power)

Provide the user a recommendation grounded in three estimation regimes:

**Regime 1 — Single binary metric (overall H-rubric accuracy)**

For a 95% CI with margin of error ε on a Bernoulli success rate, under a
worst-case 50% prior:

```
n_per_class ≈ (1.96² × 0.25) / ε²  ≈  0.96 / ε²
```

| Margin ε | Per-class samples | Total (50/50 split) |
|---|---|---|
| ±10% | 96 | ≈ 200 |
| ±7%  | 196 | ≈ 400 |
| ±5%  | 384 | ≈ 800 |
| ±3%  | 1067 | ≈ 2200 |

**Regime 2 — Per-rubric-cell metric (H1-H5 × {Yes, No, N-A})**

H1, H2, H3, H5 each have 2 active cells (Yes, No). H4 has 3 active cells
(Yes, No, N-A). That is **11 active cells**. For ≥30 examples in each
(rubric × verdict) cell as a working floor for stable per-rubric F1/recall:
~30 known-bads per failing dimension × 5 dimensions ≈ 150 known-bads, plus
≥50 known-goods (≥15 of which should be H4-NA so the H4=N-A cell is
populated) ≈ **200-220 records**.

**Regime 3 — Embedded M-rubric coverage (free dual return)**

Because every hazard record carries N traced requirements, the corpus
*also* yields N × records per-requirement RTM `SynthesizedAssessment`s. By
construction:
- every H3 known-bad with the M1=No path produces ≥1 RTM record with M1=No
- every H4 known-bad via M2=No path produces ≥1 RTM record with M2=No
- every H4 known-bad via M3=No path produces ≥1 RTM record with M3=No
- every full-green known-good produces N all-Yes RTM records

Reviewers using `evaluate-langgraph-mlflow` therefore get *two* confusion
matrices (H-rubric and M-rubric) from the same corpus. State this dual
return in the description.md so consumers know not to recount samples.

**Default recommendation**: 50 known-good (35 full-green + 15 H4-NA) +
50 known-bad (10 each across H1-H5) = **100 total** at ±10% CI for a
first-pass smoke evaluation; scale to 200 (100/100, with 30 H4-NA inside
the known-goods) for ±7% CI plus per-rubric-cell readability. Below
50/class, treat results as exploratory only.

State the recommendation explicitly, then ask which sample size the user
wants. Do not silently default to 200 if the user asked for 10.

## Generation procedure

1. **Confirm domain + product**. State the chosen product concretely
   (e.g., "FluxPump 4000 — a Class II PCA infusion pump SiMD, IEC 62304
   Class B, ISO 14971 risk class High"). All hazards anchor to this
   product. Reuse the `HAZ-PUMP-*` / `REQ-PUMP-*` / `TC-PUMP-*` /
   `DD-PUMP-*` / `FMEA-PUMP-*` / `SRA-PUMP-*` / `URRA-PUMP-*` ID family
   already in `tests/fixtures/sample_hazard.json` if you stay on infusion
   pumps, picking starting numbers (e.g. `HAZ-PUMP-100+`) that don't
   collide with the existing fixture. Other domains use a comparable
   convention (`HAZ-CGM-*`, `HAZ-EHR-*`, `HAZ-AVI-*`).
2. **Pre-author hazard library**. Author 8-15 candidate hazardous
   situations for the chosen product, each plausibly producing a chain:
   `function → FSOE → software_related_causes → hazardous_situation → harm`.
   Each hazard must be unambiguously software-related OR clearly
   non-software-related (the latter only for H4-NA known-goods).
3. **For each record**, decide the label up front (full-green / H4-NA /
   known-bad with primary_failure ∈ {H1..H5}) and write the
   `description` field as one sentence stating the design intent. This
   keeps the deliberate failure mode explicit for downstream evaluators.
4. **Author the 24 hazard-register fields**. Match the language style in
   `sample_hazard.json` — concise but specific, with real-looking
   numbers (heartbeat latencies, dose limits, pressure thresholds, etc.):
   - For known-bads, deliberately introduce the exact failure mode
     described in the corresponding "H_n fail" section above.
   - For full-green known-goods, every field is populated and consistent.
   - For H4-NA known-goods, `software_related_causes` is empty / "None /
     mechanical-only"; the rest of the register is fully populated;
     risk-control measures cite mechanical (non-software) requirements.
5. **Author traced requirements**. ≥1 per record (Pydantic
   `min_length=1`). Use SHALL / SHOULD / MAY phrasing. For H4 known-bads,
   make sure ≥1 requirement explicitly names an error/validation surface
   (M2=No path) or a numeric threshold (M3=No path) — otherwise the
   embedded RTM rubric will legitimately rate that dimension N-A and the
   H4-No verdict will not survive a real pipeline run.
6. **Author traced test cases**. Each TC has `test_id`, `description`,
   `setup`, `steps`, `expectedResults`. For full-green records: include
   functional + negative + boundary coverage across the controlling
   requirements. For H4 known-bads: deliberately omit the missing
   negative/boundary tests; include only happy-path TCs that satisfy M1
   but fail M2 or M3.
7. **Author traced design docs**. ≥1 per record is realistic; describe
   the architecture / control-flow / safe-state behaviour relevant to
   the hazard. They are pass-through context; they don't drive verdicts.
8. **Build the per-requirement `RequirementReview` for each traced
   requirement.** For each, synthesise the embedded
   `synthesized_assessment` deterministically per the M1-M5 rubric in
   `autoqa/prompts/synthesizer-v6.jinja2`:
   - Decompose the requirement into 3-5 atomic specs in
     `decomposed_requirement.decomposed_specifications`. spec_ids
     `<req_id>-<NN>`.
   - Build `test_suite.summary` — one `SummarizedTestCase` per traced TC
     (`is_generated=false`).
   - Build `coverage_analysis` — one `EvaluatedSpec` per spec, with
     `covered_by_test_cases` listing each covering TC's
     `dimensions: List[Literal["functional","negative","boundary"]]`.
   - Emit `mandatory_findings[5]` (M1-M5) per the deterministic rubric:
     M1/M4/M5 never N-A; M2/M3 N-A iff the requirement has no
     validation surface / no threshold; `partial=true` only with
     `verdict="Yes"`. `synthesized_assessment.overall_verdict = "Yes"`
     iff every M finding ∈ {Yes, N-A}.
9. **Build the scalar `h1_finding` … `h5_finding`** by applying the
   H-rubric prompts deterministically (cite verbatim FSOE steps,
   software_related_causes entries, req_ids, test_ids):
   - For H3, populate `cited_req_ids` (controlling reqs) and
     `unblocked_items` (verbatim FSOE/software-cause quotes lacking
     control) where applicable.
   - For H4, populate `cited_test_case_ids` (TCs exercising the
     hazardous path) and `unblocked_items` (controlling reqs lacking
     M2/M3 verification).
   - H4=N-A only when `software_related_causes` indicates none.
10. **Assemble the `hazard_assessment`** deterministically:
    - `hazard_id = hazard.hazard_id`
    - `mandatory_findings = [h1_finding, h2_finding, h3_finding,
      h4_finding, h5_finding]` — exactly 5, in code order
    - `overall_verdict = "Yes"` iff every finding ∈ {Yes, N-A}, else "No"
    - `comments`: empty for clean known-goods; 1-2 sentences citing the
      specific gap for known-bads (and for known-goods with residual
      ambiguity)
    - `clarification_questions`: 0-2 closed-ended questions; never
      empty for known-bads (use them to surface the gap as a question
      to engineering).
11. **Validate** every output record:
    - `HazardRecord.model_validate({...input minus the 4 label
      fields...})` round-trips cleanly.
    - `HazardAssessment.model_validate(record["hazard_assessment"])`
      passes.
    - `SynthesizedAssessment.model_validate(rr["synthesized_assessment"])`
      passes for every `requirement_reviews[i]`.
    - The deterministic overall_verdict invariant holds.
12. **Write three files**:
    - `inputs.jsonl` — one input record per line.
    - `outputs.jsonl` — one output record per line, in the same order.
    - `description.md` — the metadata file (see structure below).

## description.md structure

```markdown
# Synthetic hazard_risk_reviewer Dataset — <Product Name>

## Domain & product
- Domain: <e.g., Medical Device Software (SiMD, IEC 62304 Class B)>
- Product: <e.g., FluxPump 4000 PCA infusion pump (Class II SiMD)>
- Compliance frame: ISO 14971, IEC 62304 (Class B), IEC 82304-1, FDA 21 CFR 820.30

## Class distribution
- Known good: N records
  - Full-green (H1-H5 all Yes, software-related): X
  - H4-NA (non-software-related; H4 = "N-A", H1/H2/H3/H5 Yes): Y
- Known bad (overall_verdict=No): M records
- Total: N+M

## Per-rubric failure distribution (known-bad)
- H1 Hazard Statement Completeness No: a
- H2 Pre-Mitigation Risk No: b
- H3 Risk Control Adequacy No: c
- H4 Verification Depth No (M2=No path): d1
- H4 Verification Depth No (M3=No path): d2
- H5 Residual Risk Closure No: e
(Sum = M)

## Embedded M1-M5 distribution (across all RequirementReview items)
- All-Yes per-requirement reviews: r0
- M1=No (driving H3 known-bads): r1
- M2=No (driving H4 known-bads, M2 path): r2
- M3=No (driving H4 known-bads, M3 path): r3
- M4=No: r4
- M5=No: r5

## Statistical posture
- Margin of error on overall H-rubric accuracy at 95% CI: ±X% (with current N+M)
- Per-rubric-cell minimum count (H1-H5 × {Yes, No, N-A}, 11 active cells): ≥30 / not yet
- Embedded M-rubric posture: <state which M-cells reach ≥30, which don't>
- Recommendation if scaling up: <next milestone>

## Schema references
- Input shape: tests/fixtures/sample_hazard.json
               autoqa/components/hazard_risk_reviewer/core.py::HazardRecord
- Output shape: autoqa/components/hazard_risk_reviewer/core.py::HazardReviewState
                autoqa/components/hazard_risk_reviewer/core.py::HazardAssessment
- H1-H5 rubric: autoqa/prompts/hazard_h{1..5}_evaluator-v1.jinja2
- Final-assessor prose: autoqa/prompts/hazard_final_assessor-v1.jinja2
- Embedded M1-M5 rubric: autoqa/prompts/synthesizer-v6.jinja2
- Pipeline runner: scripts/run_hazard_pipeline.py

## Assumptions and choices
- <Product-specific simplifications>
- <How H4-NA was distinguished from H4=No (which causes are "non-software")>
- <Whether any cascade beyond the declared primary_failure is allowed>
- <Anything an evaluator should know before running ML metrics>
```

## Verification checklist (run before claiming done)

- ✓ All `hazard_id` / `hazardous_situation_id` / `req_id` / `test_id` /
  `doc_id` values are unique with consistent prefixes; same product
  anchoring across the corpus.
- ✓ Every record's `requirements` list has ≥1 entry (Pydantic
  `min_length=1`).
- ✓ Each input round-trips through
  `HazardRecord.model_validate({...minus the 4 label fields...})`.
- ✓ Each output's `hazard_assessment` round-trips through
  `HazardAssessment.model_validate(...)`.
- ✓ Each output's every `requirement_reviews[i].synthesized_assessment`
  round-trips through `SynthesizedAssessment.model_validate(...)`.
- ✓ `mandatory_findings` is exactly 5 items, codes
  `["H1","H2","H3","H4","H5"]` in order, dimensions in matching order.
- ✓ Only H4 may carry `verdict == "N-A"`; H1, H2, H3, H5 ∈ {Yes, No}
  only.
- ✓ `overall_verdict == "Yes"` iff every finding ∈ {Yes, N-A} —
  deterministic check; fail loudly if any record violates this.
- ✓ `requirement_reviews` length == `len(hazard.requirements)` on every
  record.
- ✓ For every full-green known-good: H1=H2=H3=H4=H5="Yes",
  `software_related_causes` non-empty, every embedded
  `synthesized_assessment.overall_verdict == "Yes"`.
- ✓ For every H4-NA known-good: `software_related_causes` empty/"None"
  variant, `h4_finding.verdict == "N-A"`, H1=H2=H3=H5="Yes", every
  embedded `synthesized_assessment.overall_verdict == "Yes"`.
- ✓ For every known-bad: `primary_failure` is non-null AND the named
  `h{n}_finding.verdict == "No"`.
- ✓ For every H3 known-bad: `h3_finding.unblocked_items` is non-empty
  AND every entry is a literal substring of
  `hazard.hazardous_sequence_of_events` or
  `hazard.software_related_causes`.
- ✓ For every H4 known-bad: hazard is software-related (`h4_finding.verdict
  != "N-A"`) AND ≥1 embedded
  `synthesized_assessment.mandatory_findings[M2 or M3].verdict == "No"`.
- ✓ inputs.jsonl line N corresponds to outputs.jsonl line N (same
  `hazard_id`).
- ✓ Across all known-bads, H1-H5 failure counts roughly match the
  requested distribution (no single dimension ≥40% unless asked).
- ✓ description.md numbers match the file row counts.

## Out of scope

- Running the pipeline against the generated inputs (use
  `uv run python scripts/run_hazard_pipeline.py path/to/inputs.jsonl`
  for that — the script writes a real `outputs.jsonl` next to the
  synthesised one for diff comparison).
- Computing actual ML metrics (accuracy, F1, ROC, calibration, per-cell
  confusion matrices) — this skill produces the dataset; metric
  computation is downstream via `evaluate-langgraph-mlflow`.
- Cross-domain mixed datasets — pick one domain per generation run; if
  multi-domain is needed, run the skill twice and concatenate.
- A1-A5 *advisory* findings — the pipeline emits H1-H5 mandatory only.
  Advisory rubric is reviewer-applied at review time via the
  `review-hazard-mitigation-coverage` skill, NOT pipeline-emitted, so
  this skill does not label or generate it.
- RTM-style (one-requirement-many-test-cases) data — use
  `generate-rtm-dataset`. Per-test-case checklist data — use
  `generate-tc-dataset`.
