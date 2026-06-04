# AutoQA Testing Guide

AutoQA provides three LangGraph-based reviewers — **Test Suite Reviewer** (RTM), **Test Case Reviewer**, and **Hazard Risk Reviewer** — each exposed as a compiled async pipeline and as a FastAPI endpoint. This guide covers how to run the key integration and API tests, what fixture files are available, what inputs each reviewer requires, and what output structures to expect.

---

## Prerequisites

```bash
# Install dependencies
uv sync
```

Configure credentials in a repo-root `.env` (see `autoqa/core/config.py` for the full list):

```env
# App / server (FastAPI) — required
API_KEY=<your-key>
API_BASE_URL=<your-api-url>
API_MODEL=gpt-4o

# Integration tests read their own live-LLM credentials (a conftest safety
# check rejects any base URL containing "prod")
PYTEST_API_KEY=<your-key>
PYTEST_BASE_URL=<your-api-url>
PYTEST_MODEL=gpt-4o
```

Integration tests are marked `@pytest.mark.integration`. Run them with:

```bash
uv run pytest -m integration
```

Fetch latest pyjama-fastapi package:

```bash
uv lock --upgrade-package pyjama
uv sync
```

--upgrade-package pyjama re-resolves only pyjama to the latest commit on the tracked branch and rewrites the pin in uv.lock; uv sync installs it.

If uv has the repo cached and won't re-fetch, force it:

```bash
uv lock --upgrade-package pyjama --refresh-package pyjama
uv sync
```

---

## Fixture Files

Location: `tests/fixtures/external/`

| File                                 | Contents                                                                                                                                                             |
| ------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `test_suite_review_all_fields.jsonl` | RTM rows — each a requirement with traced test cases + design docs (e.g. REQ-PUMP-SW-042 infusion rate limiting). A `*_min_fields.jsonl` variant holds minimal rows. |
| `test_case_review_all_fields.jsonl`  | Test-case rows — each a test case with its upstream requirements + design docs (e.g. TC-PUMP-202 watchdog fault injection). A `*_min_fields.jsonl` variant exists.   |
| `software_hazard_analysis.xlsx`      | SHA workbook consumed by the hazard reviewer's Excel upload path (`parse_sha_excel`).                                                                                |
| `pyjama_response_unified.jsonl`      | Recorded unified PyJama traceability response, used to build hazard traceability in tests.                                                                           |

JSONL files are newline-delimited; each line is a self-contained input object matching the
corresponding reviewer's graph-input schema. The hazard integration test assembles its
`HazardRowWithTraceMatrix` input **programmatically** from the Excel + PyJama fixtures (there is no
single `hazard_full_traceability.jsonl` file). Labelled gold datasets live under
`tests/fixtures/gold/` and per-node mocks under `tests/fixtures/mock/`.

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
uv run pytest tests/integration/test_case_reviewer/pipeline.py::test_test_case_reviewer
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
uv run pytest tests/api/v1/test_test_suite_reviewer.py::test_test_suite_review_happy_path
```

**Endpoint:** `POST /api/v1/test-suite-review`

**Request body** (`BaselineRequest` — the endpoint fetches the baseline from JAMA; it does not
accept an inline requirement/test-case payload):

```json
{
  "baseline_id": "BASE-84429",
  "use_cache": true
}
```

**Validated in response:** HTTP 200, `Content-Type: text/html` (a downloadable viewer). The
per-requirement structured assessments (M1–M5 + R6) are written to `outputs.jsonl` in the run
directory — see [Expected Output Structures](#expected-output-structures).

---

### Test Case Reviewer

```bash
uv run pytest tests/api/v1/test_test_case_reviewer.py::test_tc_review_happy_path
```

**Endpoint:** `POST /api/v1/test-case-review`

**Request body** (same `BaselineRequest` shape as the RTM endpoint — the baseline is fetched from
JAMA and every test case in it is reviewed against the five standard objectives from
`review_objectives.yaml`):

```json
{
  "baseline_id": "BASE-84429",
  "use_cache": true
}
```

**Validated in response:** HTTP 200, `Content-Type: text/html`. Each test case's
`aggregated_assessment` (with a 5-item `evaluated_checklist`) is written to `outputs.jsonl`.

---

### Hazard Risk Reviewer

```bash
uv run pytest tests/api/v1/test_hazard_risk_reviewer.py::test_hazard_risk_review_happy_path
```

**Endpoint:** `POST /api/v1/hazard-risk-review`

**Request:** `multipart/form-data` (the endpoint parses an uploaded SHA Excel file — it does not
accept an inline `HazardRecord` JSON body):

| Form field     | Required | Default     | Notes                              |
| -------------- | -------- | ----------- | ---------------------------------- |
| `project_name` | Yes      | —           | Project or product name            |
| `file`         | Yes      | —           | SHA Excel file (`.xlsx`/`.xls`)    |
| `sheet_name`   | No       | `SHA Table` | Worksheet holding the hazard table |
| `use_cache`    | No       | `true`      | Partial caching vs full recompute  |

```bash
curl -X POST http://localhost:8000/api/v1/hazard-risk-review \
  -F "project_name=Infusion Pump" \
  -F "file=@tests/fixtures/external/software_hazard_analysis.xlsx" \
  -F "sheet_name=SHA Table" \
  --output autoqa_hazard_review.html
```

**Validated in response:** HTTP 200, `Content-Type: text/html`. Each row's `hazard_assessment`
(7 H-code findings) and `requirement_reviews` are written to `outputs.jsonl`.

---

## Graph Input Data Model

> The HTTP endpoints take a `baseline_id` (RTM/TC) or an Excel upload (hazard), **not** the objects
> below. These are the per-record **graph inputs** — the shape that JAMA/Excel data is transformed
> into before invocation, and what the integration tests construct directly. Use them when calling a
> reviewer's compiled graph (or `*Service.run_from_*`) programmatically.

### Test Suite Reviewer graph input

| Field         | Type               | Required | Notes                                 |
| ------------- | ------------------ | -------- | ------------------------------------- |
| `requirement` | `Requirement`      | Yes      | `{req_id?: string, text: string}`     |
| `test_cases`  | `TestCase[]`       | Yes      | Max 1000 items                        |
| `design_docs` | `DesignDocument[]` | No       | Enables R6 (Design Alignment) finding |

**`TestCase`:** `{test_id, description, setup?, steps?, expectedResults?, in_baseline?}`

**`DesignDocument`:** `{doc_id, name, description}`

---

### Test Case Reviewer graph input

| Field               | Type                | Required | Notes                                       |
| ------------------- | ------------------- | -------- | ------------------------------------------- |
| `test_case`         | `TestCase`          | Yes      |                                             |
| `requirements`      | `Requirement[]`     | Yes      | At least one required                       |
| `review_objectives` | `ReviewObjective[]` | No       | Defaults to 5 standard objectives from YAML |
| `design_docs`       | `DesignDocument[]`  | No       |                                             |

**`ReviewObjective`:** `{id: string, description: string, mandatory: bool}`

---

### Hazard Risk Reviewer graph input

The hazard graph consumes a `HazardRecord` (these fields are populated from each SHA Excel row;
traceability is merged in by the graph's data-integration + transform nodes):

**`HazardRecord` fields:**

| Field                                 | Type                                    |
| ------------------------------------- | --------------------------------------- |
| `hazard_id`                           | `string`                                |
| `hazardous_situation_id`              | `string`                                |
| `hazard`                              | `string`                                |
| `hazardous_situation`                 | `string`                                |
| `function`                            | `string`                                |
| `ots_software`                        | `string`                                |
| `hazardous_sequence_of_events`        | `string`                                |
| `software_related_causes`             | `string`                                |
| `harm`                                | `string`                                |
| `severity`                            | `string`                                |
| `exploitability_pre_mitigation`       | `string`                                |
| `probability_of_harm_pre_mitigation`  | `string`                                |
| `initial_risk_rating`                 | `string`                                |
| `risk_control_measures`               | `string`                                |
| `demonstration_of_effectiveness`      | `string`                                |
| `severity_of_harm_post_mitigation`    | `string`                                |
| `exploitability_post_mitigation`      | `string`                                |
| `probability_of_harm_post_mitigation` | `string`                                |
| `final_risk_rating`                   | `string`                                |
| `residual_risk_acceptability`         | `string`                                |
| `requirements`                        | `Requirement[]` — software requirements |
| `test_cases`                          | `TestCase[]` — verification tests       |
| `design_docs`                         | `DesignDocument[]`                      |
| `user_needs`                          | `Requirement[]`                         |
| `system_requirements`                 | `Requirement[]`                         |

Additional optional traceability fields: `new_hs_reference`, `sw_fmea_trace`, `sra_link`, `urra_item`, `harm_severity_rationale`.

At the API layer, hazard rows are not posted as JSON — they are read from an uploaded SHA Excel file
by `POST /api/v1/hazard-risk-review` (multipart), one graph invocation per row.

---

## Expected Output Structures

> The endpoints return an HTML viewer file. The structures below are the **serialized graph state**
> written one-per-record to `outputs.jsonl` in the run directory (and the same shape the integration
> tests assert on). Keys like `status` / `thread_id` are present on the per-record state.

### Test Suite Reviewer — RTM state

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

### Test Case Reviewer — TC state

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

### Hazard Risk Reviewer — hazard state

```json
{
  "status": "completed",
  "thread_id": "...",
  "hazard": {<HazardRecord for this row>},
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

| Reviewer             | `overall_verdict = "Yes"` when…                    | `partial = true` when…                                                         |
| -------------------- | -------------------------------------------------- | ------------------------------------------------------------------------------ |
| **Test Suite (RTM)** | All M1–M5 are `"Yes"` or `"N-A"` — R6 is excluded  | `verdict="Yes"` but spec coverage is incomplete                                |
| **Test Case**        | All **mandatory** checklist objectives are `"Yes"` | `verdict="Yes"` but not all specs covered (e.g., `expected_result_spec_align`) |
| **Hazard Risk**      | All H1–H7 are `"Yes"` or `"N-A"`                   | N/A — hazard findings do not use `partial`                                     |

**H-code N-A rule:** Only H5 (`Verification Depth and Hazard-Path Effectiveness`) may return `"N-A"` — when `software_related_causes` indicates no software cause. H1–H4, H6–H7 must be `"Yes"` or `"No"`. `overall_verdict` is computed deterministically by the `final_assessor` node, never by the LLM.
