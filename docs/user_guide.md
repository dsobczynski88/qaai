# AutoQA Testing Guide

AutoQA provides three LangGraph-based reviewers — **Test Suite Reviewer** (RTM), **Test Case Reviewer**, and **Hazard Risk Reviewer** — each exposed as a compiled async pipeline and as a FastAPI endpoint. This guide covers how to run the key integration and API tests, what fixture files are available, what inputs each reviewer requires, and what output structures to expect.

---

## Prerequisites

```bash
# Install dependencies
uv sync

# Required environment variable
export OPENAI_API_KEY=<your-key>

# Optional overrides
export AUTOQA_MODEL=gpt-4o                  # default: gpt-4o
export AUTOQA_FANOUT_CONCURRENCY=5          # RTM parallel spec evaluators (default: 5)
                                             # TC reviewer uses 10 by default
```

Integration tests are marked `@pytest.mark.integration`. Run them with:

```bash
uv run pytest -m integration
```

---

## Fixture Files

Location: `tests/fixtures/external/`

| File | Rows | Coverage |
|------|------|----------|
| `test_suite_review_all_fields.jsonl` | 3 | REQ-PUMP-SW-042 (infusion rate limiting), REQ-EHR-AUTH-015 (MFA login), REQ-TELE-VIDEO-023 (adaptive video) — each with 4 test cases + 3 design docs |
| `test_case_review_all_fields.jsonl` | 3 | TC-PUMP-202 (watchdog fault injection), TC-EHR-AUTH-015-D (MFA bypass prevention), TC-TELE-VIDEO-023-C (video stabilization timer) — each with upstream requirements + design docs |
| `hazard_full_traceability.jsonl` | 1 | HAZ-PUMP-001 (over-infusion software loop hang) — catastrophic severity, full hierarchical traceability (user needs → system reqs → software reqs), test cases, and design docs |

Each file is newline-delimited JSON. Each line is a self-contained input object matching the corresponding reviewer's request schema.

---

## Integration Tests

These tests run the full compiled LangGraph pipeline end-to-end against the fixture files above. Each test loads all rows from its fixture, fans them out concurrently via `asyncio.gather`, and records inputs and outputs to `.jsonl` files in the run directory.

### Test Suite Reviewer

```bash
uv run pytest tests/integration/test_suite_reviewer/pipeline.py::test_test_suite_reviewer
```

**What it validates:**
- One `SynthesizedAssessment` produced per requirement row
- All 6 findings present (M1, M2, M3, M4, M5, R6)
- `overall_verdict=Yes` iff all M1–M5 are `"Yes"` or `"N-A"` (R6 does not affect overall verdict)
- `partial=True` only when `verdict="Yes"` and coverage is incomplete; `partial=False` on `"No"` and `"N-A"`

**Output files written:** `inputs.jsonl`, `outputs.jsonl` in the test run directory.

---

### Test Case Reviewer

```bash
uv run pytest tests/integration/test_case_reviewer/pipeline.py::test_case_suite_reviewer
```

**What it validates:**
- One `TestCaseAssessment` produced per test case row
- `evaluated_checklist` contains exactly 5 items with IDs:
  - `expected_result_support`
  - `expected_result_spec_align`
  - `test_case_achieves`
  - `test_case_logical_sequence`
  - `test_case_setup_clarity`
- `overall_verdict=Yes` iff all mandatory checklist objectives are `"Yes"`
- `expected_result_spec_align` verdict derived from spec coverage count: 0 covered → `("No", False)`, all covered → `("Yes", False)`, partial → `("Yes", True)`

**Output files written:** `inputs.jsonl`, `outputs.jsonl` in the test run directory.

---

### Hazard Risk Reviewer

```bash
uv run pytest tests/integration/hazard_risk_reviewer/pipeline.py::test_hazard_risk_reviewer
```

**What it validates:**
- One `RequirementReview` per traced requirement in the hazard record (expected: 6)
- Each `RequirementReview` contains a `SynthesizedAssessment` (M1–M5 + R6)
- `HazardAssessment` with exactly 7 findings (H1–H7)
- `overall_verdict=Yes` iff all H1–H7 are `"Yes"` or `"N-A"` — only H5 may be `"N-A"`
- H1–H4, H6–H7 verdicts must be `"Yes"` or `"No"` only

**Output files written:** `hazard_pipeline_state.json` (full graph state for manual inspection), `inputs.jsonl`, `outputs.jsonl`.

---

## API Happy-Path Tests

These tests spin up the FastAPI app via the test client and exercise the full request/response cycle for each endpoint.

### Test Suite Reviewer

```bash
uv run pytest tests/api/v1/test_test_suite_reviewer.py::test_rtm_review_happy_path
```

**Endpoint:** `POST /api/v1/review`

**Minimal payload:**
```json
{
  "thread_id": "test-thread-001",
  "requirement": {
    "req_id": "REQ-001",
    "text": "The system shall..."
  },
  "test_cases": [
    {
      "test_id": "TC-001",
      "description": "Verify that...",
      "setup": "Initialize system",
      "steps": "1. Do X\n2. Do Y",
      "expectedResults": "System responds with Z"
    }
  ],
  "design_docs": [
    {
      "doc_id": "DOC-001",
      "name": "Architecture Spec",
      "description": "..."
    }
  ]
}
```

**Validated in response:** HTTP 200, `synthesized_assessment` present, M1–M5 findings all present.

---

### Test Case Reviewer

```bash
uv run pytest tests/api/v1/test_test_case_reviewer.py::test_tc_review_happy_path
```

**Endpoint:** `POST /api/v1/test-case-review`

**Minimal payload:**
```json
{
  "thread_id": "tc-thread-001",
  "test_case": {
    "test_id": "TC-001",
    "description": "Verify that...",
    "setup": "...",
    "steps": "...",
    "expectedResults": "..."
  },
  "requirements": [
    {
      "req_id": "REQ-001",
      "text": "The system shall..."
    }
  ]
}
```

`design_docs` and `review_objectives` are optional. When `review_objectives` is omitted, the five standard objectives from `review_objectives.yaml` are used.

**Validated in response:** HTTP 200, `aggregated_assessment` present, `evaluated_checklist` has 5 items.

---

### Hazard Risk Reviewer

```bash
uv run pytest tests/api/v1/hazard_risk_reviewer.py::test_hazard_review_happy_path
```

**Endpoint:** `POST /api/v1/hazard-review`

**Minimal payload:**
```json
{
  "thread_id": "haz-thread-001",
  "hazard": {
    "hazard_id": "HAZ-001",
    "hazardous_situation_id": "HS-001",
    "hazard": "Over-infusion",
    "hazardous_situation": "Patient receives incorrect dose",
    "function": "Drug delivery",
    "ots_software": "None",
    "hazardous_sequence_of_events": "...",
    "software_related_causes": "...",
    "harm": "Patient injury",
    "severity": "Catastrophic",
    "exploitability_pre_mitigation": "Probable",
    "probability_of_harm_pre_mitigation": "Probable",
    "initial_risk_rating": "Unacceptable",
    "risk_control_measures": "...",
    "demonstration_of_effectiveness": "...",
    "severity_of_harm_post_mitigation": "Catastrophic",
    "exploitability_post_mitigation": "Remote",
    "probability_of_harm_post_mitigation": "Remote",
    "final_risk_rating": "Acceptable",
    "residual_risk_acceptability": "Acceptable",
    "requirements": [{"req_id": "REQ-001", "text": "..."}],
    "test_cases": [{"test_id": "TC-001", "description": "..."}],
    "design_docs": [],
    "user_needs": [],
    "system_requirements": []
  }
}
```

**Validated in response:** HTTP 200, `hazard_assessment` present with 7 H-code findings, `requirement_reviews` length matches traced requirements.

---

## Input Schema Reference

### Test Suite Reviewer — `POST /api/v1/review`

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `thread_id` | `string` | Yes | Alphanumeric, dashes, underscores; max 100 chars |
| `requirement` | `Requirement` | Yes | `{req_id?: string, text: string}` |
| `test_cases` | `TestCase[]` | Yes | Max 1000 items |
| `design_docs` | `DesignDocument[]` | No | Enables R6 (Design Alignment) finding |

**`TestCase`:** `{test_id, description, setup?, steps?, expectedResults?, in_baseline?}`

**`DesignDocument`:** `{doc_id, name, description}`

---

### Test Case Reviewer — `POST /api/v1/test-case-review`

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `thread_id` | `string` | Yes | |
| `test_case` | `TestCase` | Yes | |
| `requirements` | `Requirement[]` | Yes | At least one required |
| `review_objectives` | `ReviewObjective[]` | No | Defaults to 5 standard objectives from YAML |
| `design_docs` | `DesignDocument[]` | No | |

**`ReviewObjective`:** `{id: string, description: string, mandatory: bool}`

---

### Hazard Risk Reviewer — `POST /api/v1/hazard-review`

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `thread_id` | `string` | Yes | |
| `hazard` | `HazardRecord` | Yes | See full field list below |

**`HazardRecord` fields:**

| Field | Type |
|-------|------|
| `hazard_id` | `string` |
| `hazardous_situation_id` | `string` |
| `hazard` | `string` |
| `hazardous_situation` | `string` |
| `function` | `string` |
| `ots_software` | `string` |
| `hazardous_sequence_of_events` | `string` |
| `software_related_causes` | `string` |
| `harm` | `string` |
| `severity` | `string` |
| `exploitability_pre_mitigation` | `string` |
| `probability_of_harm_pre_mitigation` | `string` |
| `initial_risk_rating` | `string` |
| `risk_control_measures` | `string` |
| `demonstration_of_effectiveness` | `string` |
| `severity_of_harm_post_mitigation` | `string` |
| `exploitability_post_mitigation` | `string` |
| `probability_of_harm_post_mitigation` | `string` |
| `final_risk_rating` | `string` |
| `residual_risk_acceptability` | `string` |
| `requirements` | `Requirement[]` — software requirements |
| `test_cases` | `TestCase[]` — verification tests |
| `design_docs` | `DesignDocument[]` |
| `user_needs` | `Requirement[]` |
| `system_requirements` | `Requirement[]` |

Additional optional traceability fields: `new_hs_reference`, `sw_fmea_trace`, `sra_link`, `urra_item`, `harm_severity_rationale`.

#### Batch Route — `POST /api/v1/hazard-review/from-excel`

```json
{
  "thread_id_prefix": "batch-run-01",
  "file_path": "/abs/path/to/sha_table.xlsx",
  "sheet_name": "SHA Table"
}
```

---

## Expected Output Structures

### Test Suite Reviewer — `ReviewResponse`

```json
{
  "status": "completed",
  "thread_id": "...",
  "synthesized_assessment": {
    "requirement": {"req_id": "REQ-001", "text": "..."},
    "overall_verdict": "Yes | No",
    "mandatory_findings": [
      {
        "code": "M1 | M2 | M3 | M4 | M5 | R6",
        "dimension": "Functional | Negative | Boundary | Spec Coverage | Terminology | Design Alignment",
        "verdict": "Yes | No | N-A",
        "partial": false,
        "rationale": "One sentence.",
        "cited_test_case_ids": ["TC-001"],
        "uncovered_spec_ids": []
      }
    ],
    "comments": "...",
    "clarification_questions": []
  },
  "coverage_analysis": [
    {
      "spec_id": "SPEC-1",
      "covered_exists": true,
      "covered_by_test_cases": [
        {"test_case_id": "TC-001", "dimensions": ["functional"], "rationale": "..."}
      ]
    }
  ],
  "decomposed_requirement": {...},
  "test_suite": {...},
  "design_docs": [...]
}
```

---

### Test Case Reviewer — `TestCaseReviewResponse`

```json
{
  "status": "completed",
  "thread_id": "...",
  "aggregated_assessment": {
    "test_case": {...},
    "requirements": [...],
    "overall_verdict": "Yes | No",
    "evaluated_checklist": [
      {
        "id": "expected_result_support | expected_result_spec_align | test_case_achieves | test_case_logical_sequence | test_case_setup_clarity",
        "description": "...",
        "mandatory": true,
        "verdict": "Yes | No",
        "partial": false,
        "assessment": "..."
      }
    ],
    "comments": "...",
    "clarification_questions": []
  },
  "coverage_analysis": [...],
  "logical_structure_analysis": {"exists": true, "assessment": "..."},
  "prereqs_analysis": {"exists": true, "assessment": "..."},
  "decomposed_requirements": [...],
  "design_docs": [...]
}
```

---

### Hazard Risk Reviewer — `HazardReviewResponse`

```json
{
  "status": "completed",
  "thread_id": "...",
  "hazard": {<HazardRecord echoed from request>},
  "hazard_assessment": {
    "hazard_id": "HAZ-001",
    "overall_verdict": "Yes | No",
    "mandatory_findings": [
      {
        "code": "H1 | H2 | H3 | H4 | H5 | H6 | H7",
        "dimension": "<dimension name>",
        "verdict": "Yes | No | N-A",
        "rationale": "One sentence.",
        "cited_req_ids": ["REQ-001"],
        "cited_test_case_ids": ["TC-001"],
        "unblocked_items": []
      }
    ],
    "comments": "...",
    "clarification_questions": []
  },
  "requirement_reviews": [
    {
      "requirement": {"req_id": "REQ-001", "text": "..."},
      "synthesized_assessment": {<SynthesizedAssessment — same shape as RTM output>},
      "decomposed_requirement": {...},
      "test_suite": {...},
      "coverage_analysis": [...]
    }
  ]
}
```

---

## Verdict Logic Quick Reference

| Reviewer | `overall_verdict = "Yes"` when… | `partial = true` when… |
|----------|----------------------------------|------------------------|
| **Test Suite (RTM)** | All M1–M5 are `"Yes"` or `"N-A"` — R6 is excluded | `verdict="Yes"` but spec coverage is incomplete |
| **Test Case** | All **mandatory** checklist objectives are `"Yes"` | `verdict="Yes"` but not all specs covered (e.g., `expected_result_spec_align`) |
| **Hazard Risk** | All H1–H7 are `"Yes"` or `"N-A"` | N/A — hazard findings do not use `partial` |

**H-code N-A rule:** Only H5 (`Residual Risk Acceptability`) may return `"N-A"`. H1–H4, H6–H7 must be `"Yes"` or `"No"`.
