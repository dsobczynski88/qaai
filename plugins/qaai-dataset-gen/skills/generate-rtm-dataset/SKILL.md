---
name: generate-rtm-dataset
description: |
  Generate a labelled Requirements Traceability Matrix (RTM) dataset for evaluating the
  QAAI test_suite_reviewer LangGraph as a binary classifier. Each input is a Requirement
  plus traced TestCases; each label row carries the overall verdict and the M1-M5
  mandatory rubric (plus the optional R6 advisory cell). Records are labelled "known
  good" (Overall_Verdict=Yes) or "known bad" (Overall_Verdict=No, with a real deficiency
  visible in the text). Writes actual_inputs.jsonl + actual_labels.jsonl + description.md
  into a timestamped folder under eval/datasets/test_suite/actual/, derives actual_outputs.jsonl,
  and must pass `dataset_studio validate`. Default domain is medical-device software
  (SiMD / SaMD / IEC 82304); other domains can be requested. Includes power-analysis
  guidance on sample size. Use when the user asks to synthesize a labelled RTM dataset,
  build eval data for the test-suite reviewer, or evaluate that pipeline as a classifier.
  Siblings: generate-tc-dataset, generate-hazard-dataset, dataset-review.
---

# generate-rtm-dataset

Authors records for `qaai.agents.test_suite_reviewer`. Live schema:
`qaai/agents/test_suite_reviewer/core.py`. Authoritative rubric:
`eval/specs/test_suite_reviewer.yaml`.

This skill is a **content generator**. It writes files; it does not invoke the graph.
Running the graph against these inputs is `--mode run` in the qaai-mlflow-eval plugin.

**Read `eval/specs/test_suite_reviewer.yaml` and
`qaai/agents/test_suite_reviewer/core.py` before generating.** The spec and the live
Pydantic models win over anything written here.

## Persona

A **Principal Requirements Engineer & Test Architect** with 15+ years across medical
devices, aerospace, automotive, and enterprise software. Working knowledge of IEEE 29148;
IEC 62304 / IEC 82304 / ISO 14971; ISO 26262 and DO-178C for non-default domains;
black-box / gray-box strategies; boundary-value analysis, equivalence partitioning, and
state-transition testing.

## Inputs to gather

If unspecified, ask once with `AskUserQuestion`:

1. **Domain + product** — default medical-device software (SiMD / SaMD, IEC 82304).
   Pick ONE concrete product (e.g. "FluxPump 4000, a Class II PCA infusion pump, IEC
   62304 Class B") and anchor every record to it.
2. **Sample count** — see "Sample size" below. Do not silently default to 200 if the
   user asked for 10.
3. **Class balance** — default 50/50.
4. **Output folder** — default: whatever `dataset_studio new --type test_suite --quiet`
   prints. Never write into `eval/datasets/test_suite/actual/pilot-20-record/` directly; that is the committed
   pilot.

## Files to write

Into the scaffolded folder:

- **`actual_inputs.jsonl`** — one graph input row per line:
  ```json
  {"requirement": {"req_id": "REQ-PUMP-001", "text": "..."},
   "test_cases": [{"test_id": "TC-PUMP-001-A", "description": "...", "setup": "...",
                   "steps": "Step: 1. ...\nStep: 2. ...",
                   "expectedResults": "ExpectedResult: 1. ...\nExpectedResult: 2. ..."}]}
  ```
  `design_docs` is optional. Authoring metadata (`expected_gap`, `rationale`) may ride
  along — the validator allows extra keys and the graph ignores them.

- **`actual_labels.jsonl`** — the flat answer key, row-aligned:
  ```json
  {"Overall_Verdict": "No", "M1": "Yes", "M2": "Yes", "M3": "N-A", "M4": "No",
   "M5": "Yes", "class": "known_bad", "primary_failure": "M4"}
  ```
  Allowed non-rubric keys: `id`, `class`, `primary_failure`, `notes`. (`reviewer_note`,
  `reviewed_by`, `reviewed_at` are written by the editor — do not author them.)

- **`actual_outputs.jsonl`** — **do not hand-write.** Run
  `uv run python -m qaai.dataset_studio sync-outputs <dir>`. Deriving it from the labels
  is what makes check `V050` pass by construction.

- **`description.md`** — replace every `TODO` in the scaffolded stub.

## The rubric — six cells, not five

`SynthesizedAssessment.mandatory_findings` is **M1–M5 mandatory + R6 advisory**:

| Code | Dimension | May be N-A | Gates the verdict |
|---|---|---|---|
| M1 | Functional | no | yes |
| M2 | Negative | **yes** | yes |
| M3 | Boundary | **yes** | yes |
| M4 | Spec Coverage | no | yes |
| M5 | Terminology | no | yes |
| R6 | Design Alignment | **yes** | **no — advisory only** |

- `Overall_Verdict = "Yes"` iff every **mandatory** cell (M1–M5) is in `{Yes, N-A}`.
  **An R6 of `No` never flips the verdict.** This is enforced in code by
  `SynthesizedAssessment._derive_overall_verdict`, so a contradiction is silently
  corrected at load — and flagged by check `V040`.
- R6 is **optional in the labels**. The committed pilot omits it entirely, which is
  legal. Include it only if the records carry design documents.
- `M2 = N-A` when the requirement exposes no validation or blocked-path surface.
  `M3 = N-A` when it names no threshold, limit, or timing edge. M1/M4/M5 are never N-A.
- `partial=true` requires `verdict="Yes"`; it never appears with No or N-A.

## Class definitions

### Known good (`Overall_Verdict = "Yes"`)
The test suite verifies every spec the requirement carries. M1 Yes; M2 Yes or N-A; M3 Yes
or N-A; M4 Yes; M5 Yes. A reviewer reading the record must agree with each cell.

### Known bad (`Overall_Verdict = "No"`)
At least one mandatory cell is `No`, and the deficiency is **visible in the text** — a
missing positive-path test, no negative case where the requirement plainly has an error
surface, no test at the stated numeric limit, an uncovered spec, or genuinely drifted
vocabulary. A row is not "bad" merely because a test is absent from a list; the absence
has to matter.

Record the failing cell in `primary_failure` and distribute failures across M1–M5 so
per-rubric metrics have signal.

**Watch the coupling.** A record with no positive-path test at all fails M1 *and* leaves
the functional spec uncovered, so M4 is also `No`. The committed pilot documents exactly
this: "Labelling those M4=Yes would have been a convenient fiction." Let the cells follow
from the content.

### Failure modes to draw on
- **M1** — a clear functional behavior with no positive-path test.
- **M2** — validation/error surfaces with no negative-path test.
- **M3** — a named threshold/limit/role transition with no boundary test. Get the
  inequality right: "shall not exceed 4000 mg" means 4000 is *accepted* and 4001 rejected;
  "at least 10 characters" means 10 is accepted and 9 rejected.
- **M4** — the requirement decomposes to several specs and one has no covering test.
- **M5** — tests verify real behavior but in drifted vocabulary (requirement says
  "Isolation Precautions banner on the patient header"; the test says "quarantine flag on
  the patient summary widget").

## Sample size

**Regime 1 — overall accuracy.** 95% CI with margin ε at worst-case p=0.5:
`n ≈ 0.96 / ε²` per class.

| Margin ε | Per class | Total (50/50) |
|---|---|---|
| ±10% | 96 | ~200 |
| ±7% | 196 | ~400 |
| ±5% | 384 | ~800 |

**Regime 2 — per-rubric cells.** Aim for ≥30 examples per active (rubric × verdict)
cell. A practical floor is ~30 known-bads per failing rubric (~150 known-bads) plus
50–100 known-goods.

**Regime 3 — comparing two prompt versions.** ~150–200 paired examples detects a
5-point shift at 80% power (McNemar).

**Default recommendation: 100 known-good + 100 known-bad.** Below 50/class, treat results
as exploratory. Use `uv run python scripts/sample_size.py ci --confidence 0.95
--margin 0.05 --p 0.5` and size off `n_scored`, not `n_records`.

**One row per unique requirement text.** Reusing a requirement across rows inflates the
design effect and invalidates the i.i.d. confidence interval — the discarded 800-row set
had 133 unique texts across 800 rows, one recurring 56 times.

## Procedure

1. `DIR=$(uv run python -m qaai.dataset_studio new --type test_suite --quiet)`
2. State the product concretely. Every requirement and test anchors to it.
3. Write each requirement in 1–3 sentences using SHALL / SHOULD / MAY — specific enough
   that decomposing into 3–6 specs is natural. Sequential `req_id`s, all unique.
4. Author the traced test cases with realistic `setup` / `steps` / `expectedResults`.
   Use the numbered multi-line convention shown above. **No templated placeholder
   steps** — "Execute primary action specified in requirement. Verify success." is
   exactly what got the previous dataset thrown out.
5. Derive each label cell from what the test cases actually do, then set
   `Overall_Verdict` from the mandatory cells. Never assert a verdict first and
   back-fill the cells.
6. Write `actual_inputs.jsonl` and `actual_labels.jsonl` into `$DIR`, row-aligned.
7. `uv run python -m qaai.dataset_studio sync-outputs "$DIR"`
8. `uv run python -m qaai.dataset_studio validate "$DIR"` — **must exit 0.** Fix and
   repeat; do not report success on a non-zero exit.
9. Fill in `description.md` (class distribution, failure-mode table, statistical posture,
   and any rubric rulings you made — e.g. which requirements you judged to have no
   boundary surface).
10. Tell the user to review the samples:
    `uv run python -m qaai.dataset_studio edit "$DIR"`.

## Verification checklist

- All `req_id` / `test_id` values unique and consistently prefixed.
- One row per unique requirement text.
- Every requirement has ≥1 traced test case.
- Requirements use SHALL / SHOULD / MAY — no "the system will" / "should probably".
- Test steps are concrete and product-specific, never templated.
- `Overall_Verdict = Yes` iff M1–M5 ⊆ {Yes, N-A}; R6 excluded.
- M1 / M4 / M5 are never `N-A`.
- Known-bad rows name their failing cell in `primary_failure`, and it is genuinely
  visible in the record.
- Failures are spread across M1–M5; no single cell dominates unless asked.
- Row *i* of the three files describes the same requirement.
- `description.md` counts match the actual row counts.
- `dataset_studio validate` exits 0.

## Out of scope

- Running the pipeline or computing metrics — the qaai-mlflow-eval plugin.
- Test-case-level datasets — `generate-tc-dataset`.
- Hazard-traceable datasets — `generate-hazard-dataset`.
- Mixed-domain sets — run once per domain and concatenate.
