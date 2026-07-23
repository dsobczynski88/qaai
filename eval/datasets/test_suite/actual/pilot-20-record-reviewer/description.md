# RTM Study Dataset — HealthCore EHR (reviewed)

**Revision 2** of the Test Suite Reviewer answer key —
`eval/datasets/test_suite/actual/pilot-20-record-reviewer/`.

Successor to **Revision 1** (`../pilot-20-record/`). This revision **reconciles the
M1–M5 rubric cells with the reviewer's own written feedback**, recorded as the `feedback`
lines in Revision 1's `edits.log`. It is a new timestamped sibling — Revision 1 is left
untouched so any run already scored against it keeps its exact answer key on disk.

> Everything in Revision 1's description (domain, product, grounding rule, layout, schema
> references, R6 amendment) still holds. This file documents **only what changed in
> Revision 2 and why**.

## Why this revision exists

In Revision 1 a reviewer (dsobc) worked through all 20 rows in the dataset editor,
attaching a written `reviewer_note` to each and, in the same pass, editing rubric cells
**in place**. Cross-checking the three on-disk sources — `source_gold.jsonl` (the original
grounded verdicts), `actual_labels.jsonl` (the edited labels), and the notes themselves —
showed that the notes were all captured, but **several rows' rubric cells had drifted out of
agreement with their own note.** The edits over-flipped `M1`/`M2`/`M3` to `No` on
known-bad rows past what the note (or the original single-failure design) supports — e.g.
REQ-HC-023's note explicitly says "the tests only cover: Positive test … Negative test …",
yet both `M1` and `M2` read `No`.

An answer key whose cells contradict the reviewer's stated reasoning is exactly the
"labels that disagree with content" failure Revision 1 was built to avoid, one layer down
(per-cell instead of per-row). Revision 2 makes the cells follow the notes.

## Alignment principle

For every row, each rubric cell is aligned to what the reviewer note actually says; **where
a note is silent on a cell, the `source_gold` value is restored.**

- A note that acknowledges a test exists ⇒ that cell is `Yes`.
- A note that names a real gap ⇒ that cell is `No`.
- A requirement stating no threshold/limit/timing ⇒ `M3 = N-A` (Revision 1's own ruling).

`Overall_Verdict` remains derived, never hand-set: `Yes` iff every M1–M5 ∈ {Yes, N-A}
(spec `eval/specs/test_suite_reviewer.yaml`; R6 stays advisory and excluded). Every changed
row was re-checked against this rule (validator `V040`).

## Changes from Revision 1

`row` is the 0-based `actual_*` row index. Cells shown as `M1/M2/M3/M4/M5 (Overall)`.

| row | REQ | Rev 1 | **Rev 2** | Cells changed | Grounded in the note |
|---|---|---|---|---|---|
| 0006 | HC-016 | No/Y/N-A/Y/Y **(No)** | Y/Y/N-A/Y/Y **(Yes)** | M1→Yes, Overall→Yes | "the test cases satisfy the requirement functionality … not necessarily a change to the input dataset" — a requirement-clarity clarification, not a test deficiency. Recovered to **good**. |
| 0009 | HC-019 | Y/Y/No/No/Y (No) | Y/Y/**Yes**/No/Y (No) | M3→Yes | "the basic and literal boundary coverage is intact" (TC-019-C tests the 9/10-char boundary) → M3=Yes. M4 stays No for the untested implicit sub-requirement (that the reason be a coherent statement). Stays **bad**. |
| 0010 | HC-020 | No/Y/No/No/Y (No) | No/Y/**N-A**/No/Y (No) | M3→N-A | No numeric threshold in the requirement; note is silent on boundary → M3=N-A. |
| 0011 | HC-021 | No/No/No/No/Y (No) | **Yes**/No/**N-A**/No/Y (No) | M1→Yes, M3→N-A | Note disputes only the duplicate-MRN negative and the rejection-message; TC-021-A is a valid positive registration → M1=Yes. No threshold → M3=N-A. |
| 0012 | HC-022 | No/No/No/No/Y (No) | **Yes**/No/No/No/Y (No) | M1→Yes | TC-022-A (both vitals exceed → alert) is a valid positive path → M1=Yes. Note's gaps (single-factor negatives, exact-4h boundary, compound coverage) keep M2/M3/M4=No. |
| 0013 | HC-023 | No/No/N-A/No/Y (No) | **Yes/Yes**/N-A/No/Y (No) | M1→Yes, M2→Yes | Note acknowledges the STAT positive (TC-023-A) and the missing-priority negative (TC-023-B). The gap (Routine, label MRN, label order ID) sits on M4=No. |
| 0015 | HC-025 | Y/No/N-A/No/Y (No) | Y/No/N-A/**Yes**/Y (No) | M4→Yes | The note's internal-IP point is a hedged clarification ("if nothing else, there should be a clarification question"), not a hard spec-coverage failure → M4=Yes. M2=No (no 1-factor-insufficient negative) keeps it bad. |
| 0017 | HC-027 | No/No/N-A/No/Y (No) | **Yes/Yes**/N-A/No/Y (No) | M1→Yes, M2→Yes | Document (TC-027-A) and duplicate-block (TC-027-B) happy paths are present; the completeness gap the note lists (>30-min no-block, admin-time capture, other roles) sits on M4=No. |

Rows **0000–0005, 0007, 0008, 0014, 0016, 0018, 0019** were already consistent with their
notes and are unchanged. `reviewer_note`, `reviewed_by`, and `reviewed_at` are carried
forward verbatim on every row.

### Two Overall-flipping decisions

Only two changes move a binary class label (the thing the harness scores); both were
confirmed with the reviewer before applying:

- **HC-016 → Yes.** The note affirms functional coverage and frames the "all other roles"
  concern as a requirement clarification, explicitly "not … a change to the input dataset."
- **HC-019 → stays No.** The note corrects the boundary cell up (M3→Yes) but identifies a
  genuine untested implicit sub-requirement, which is a real `M4` spec-coverage gap.

## Class distribution (Revision 2)

- Known good (overall_verdict = Yes): **9** — REQ-HC-010…018
- Known bad  (overall_verdict = No):  **11** — REQ-HC-019…029
- Total: 20 (was 10 / 10 in Revision 1)

The shift is the net of the two decisions above: **016 recovered** to good, **019 demoted**
to bad on a reviewer-identified M4 gap. All other Overall verdicts are unchanged; the
remaining edits are per-cell corrections within rows whose Overall verdict did not move.

## Files

| File | Provenance in this revision |
|---|---|
| `actual_inputs.jsonl` | Copied verbatim from Revision 1 (already carries the reviewer-added `TC-HC-015-C` and the immutability `TC-HC-014-B`). |
| `actual_labels.jsonl` | Revision 1 labels with the 12 cell edits above; notes/timestamps preserved. |
| `actual_outputs.jsonl` | **Regenerated** from `actual_labels.jsonl` via `dataset_studio sync-outputs` (`synthesize_outputs`), so it round-trips (`V050`). Do not hand-edit. |
| `source_gold.jsonl` | Carried forward verbatim as the **pre-review** source. As in Revision 1 it predates the R6 amendment and `TC-HC-015-C`; `actual_labels.jsonl` is the reviewed authority. |
| `edits.log` | Revision 1's log copied forward, then the 12 `edit` lines + a `save-as` marker appended. |

## Reproduce / verify

```bash
uv run python -m qaai.dataset_studio validate \
  eval/datasets/test_suite/actual/pilot-20-record-reviewer   # exits 0
```

Validation passes with 0 errors (`V040` rubric rule, `V031` N-A rule, `V050`
labels↔outputs round-trip). Statistical posture is unchanged from Revision 1: n=20 is a
directional seed batch (95% CI ≈ ±0.22), one row per unique requirement text (design
effect 1); read per-cell counts as anecdote until the set is scaled.
