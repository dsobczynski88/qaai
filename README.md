# AutoQA — AI-Powered DHF Reviewer for Medical Device Software

## Background

AutoQA is a software quality tool designed to assist QA engineers and regulatory teams in reviewing Design History File artifacts for medical device software developed under FDA guidance and the IEC 62304 / ISO 14971 lifecycle standards. These reviews must demonstrate that every software requirement is adequately verified by a corresponding test case, that each test case is itself well-formed, and that hazards in the risk register are mitigated by traceable controls. In practice, they are labor-intensive processes prone to coverage gaps, inconsistent rationale, and missed edge cases.

AutoQA exposes three complementary reviewers, each implemented as an independent LangGraph pipeline that emits a structured, SoP-gating rubric:

| Reviewer | What it scores | Output rubric |
|----------|----------------|---------------|
| **Test Suite Reviewer (RTM)** — `/api/v1/review` | One requirement against its associated test suite | M1-M5 mandatory findings (Functional / Negative / Boundary / Spec Coverage / Terminology) → binary Yes/No coverage verdict |
| **Hazard Coverage Reviewer** — `/api/v1/hazard-review` | One hazard register entry against its traced requirements + test cases + design docs | H1-H7 mandatory findings (Hazard Record Completeness / Software Contribution / Pre-Mitigation Risk / Risk Control Adequacy / Verification Depth / Residual Risk Closure / HSHA Update) → binary Yes/No verdict |
| **Single Test Case Reviewer** — library only (`autoqa.components.test_case_reviewer`) | One test case against its requirements and a checklist of review objectives | Per-objective Yes/No verdicts (with a `partial` flag for material gaps) → binary Yes/No overall verdict |

All three reviewers cite the artifact IDs that support each finding, return short comments clarifying any gaps, and emit closed-ended clarification questions so reviewers can quickly confirm whether flagged gaps are real or N/A in context.

---

## Pipeline Architecture

Every reviewer is a LangGraph `StateGraph` that fans out via the `Send` API for maximum parallelism, then fans back in via `operator.add` reducers before a synthesizer node aggregates findings against the rubric. Each run also writes a Mermaid graph PNG (`graph.png`, `hazard_graph.png`, or `tc_graph.png`) into the run's log folder alongside `autoqa.log`.

### Test Suite Reviewer (RTM coverage)

```
START
  ↓
┌──────────────────────────────────────┐
│ DECOMPOSER       SUMMARIZER          │  ← parallel
│ Breaks requirement into atomic specs │  ← structures raw test cases
└──────────────────────────────────────┘
  ↓ (fan-in)
┌──────────────────────────────────────┐
│ COVERAGE_ROUTER  (sync point)        │
└──────────────────────────────────────┘
  ↓ Send × N (one per decomposed spec)
┌──────────────────────────────────────┐
│ SPEC_EVALUATOR × N  (parallel)       │  ← one LLM call per spec
└──────────────────────────────────────┘
  ↓ (fan-in: operator.add accumulates coverage_analysis)
┌──────────────────────────────────────┐
│ SYNTHESIZER  (MoA-inspired)          │  ← holistic assessment across all specs
└──────────────────────────────────────┘
  ↓
END
```

### Hazard Coverage Reviewer

The hazard pipeline reuses the test suite reviewer as an atomic subgraph: each requirement traced from a `HazardRecord` is reviewed in parallel by invoking the full RTM graph for that requirement. The hazard-level evaluators (H1, H2, H3, H7) run immediately in parallel with the requirement reviews, while H4 and H5 wait for requirement reviews to complete. H6 validates residual risk closure after H3, H4, and H5 complete. Finally, a deterministic aggregator assembles all seven findings into the H1-H7 rubric.

```
START
  ├──→ h1_evaluator ────────────────────────┐
  ├──→ h2_evaluator ────────────────────────┤
  ├──→ h3_evaluator ──────────┐             │
  ├──→ h7_evaluator ──────────├─────────────┤
  └──→ dispatch_requirement_reviews         │
          ↓                   │             │
      requirement_reviewer × N│             │
          ↓                   │             │
      ┌───┴────┐              │             │
      h4       h5             │             │
      └───┬────┘              │             │
          └──────→ h6 ──────────┘             │
                   ↓                          │
              final_assessment ←──────────────┘
                   ↓
                  END
```

**Key improvements:**
- H1, H2, H3, H7 run immediately (parallel with requirement_reviewer)
- H4, H5 run after requirement_reviews complete
- H6 runs after H3, H4, H5 complete (validates residual risk against upstream evidence)
- Final assessor waits for all 7 findings
- Estimated wall-clock reduction: ~30-40% vs sequential execution

### Single Test Case Reviewer

A test case plus its traced requirements and a review-objectives checklist enter at `START`. The decomposer splits each requirement into atomic specs; a no-op `coverage_router` then fans out **three independent waves of Sends** — one per review axis (coverage / logical / prereqs) — to per-spec evaluators that run in parallel. The aggregator synthesizes the three accumulated `SpecAnalysis` lists into a single `TestCaseAssessment` with the review-objectives checklist populated.

```
START
  ↓
┌──────────────────────────────────────┐
│ DECOMPOSER (sequential per req)      │
└──────────────────────────────────────┘
  ↓
┌──────────────────────────────────────┐
│ COVERAGE_ROUTER (sync point)         │
└──────────────────────────────────────┘
  ↓ 3× Send × N (parallel waves per axis)
┌─────────────┬─────────────┬──────────┐
│ COVERAGE    │ LOGICAL     │ PREREQS  │
│ EVAL × N    │ EVAL × N    │ EVAL × N │
└─────────────┴─────────────┴──────────┘
  ↓ (operator.add reducers fan in per axis)
┌──────────────────────────────────────┐
│ AGGREGATOR  (MoA-like synthesis)     │
└──────────────────────────────────────┘
  ↓
END
```

### Test Suite Reviewer output fields

| Field | Description |
|-------|-------------|
| `decomposed_requirement` | Requirement broken into atomic, dimension-agnostic specs (`spec_id`, `description`, `acceptance_criteria`, `rationale`). Dimension classification happens later, per covering test case, rather than at decomposition time. |
| `test_suite` | Structured summary of each test case — objective, protocol, acceptance criteria |
| `coverage_analysis` | Per-spec verdict: `covered_exists` (bool), `covered_by_test_cases` (list of `{test_case_id, dimensions[], rationale}` where each covering TC is labelled with the dimension(s) it exercises — any subset of `functional`, `negative`, `boundary` — and may cover multiple dimensions simultaneously), and a V&V `coverage_rationale` |
| `synthesized_assessment` | SoP-gating rubric: `overall_verdict` (`Yes`/`No`), `mandatory_findings` (exactly five items M1–M5 with Yes/No/N-A verdicts, cited TC IDs, and uncovered spec IDs), short `comments` clarifying gaps, and a list of `clarification_questions` that the reviewer can answer to confirm whether identified gaps are real or N/A in context |

The `overall_verdict` aggregates deterministically: it is `Yes` only when every mandatory finding is `Yes` or `N-A`; any single `No` flips it to `No`. `N-A` is permitted only on M2 (Negative) and M3 (Boundary) when the requirement has no validation surface or no threshold/limit surface respectively.

### Hazard Coverage Reviewer output fields

| Field | Description |
|-------|-------------|
| `requirement_reviews` | One `RequirementReview` per requirement traced from the `HazardRecord`, each carrying the M1-M5 `synthesized_assessment` plus the RTM byproducts (`decomposed_requirement`, `test_suite`, `coverage_analysis`) — the full evidence chain that drove the hazard verdict |
| `hazard_assessment.mandatory_findings` | Exactly seven items in order — H1 Hazard Record Completeness and Semantic Integrity, H2 Software Contribution and Cause Coverage, H3 Pre-Mitigation Risk and Exploitability Characterization, H4 Risk Control Identification, Allocation, and Coverage, H5 Verification Depth and Hazard-Path Effectiveness, H6 Residual Risk Closure and Acceptability Decision, H7 HSHA Update and Newly Identified Hazard / Hazardous Situation Capture — each with a `Yes` / `No` (or `N-A` on H5 only) verdict, cited `req_id`s and `test_id`s, and `unblocked_items` (sequence-of-events steps without controlling requirements on H4, controls without verifying tests on H5) |
| `hazard_assessment.overall_verdict` | `Yes` iff every finding is `Yes` or `N-A`; `No` otherwise (computed deterministically, never by the LLM) |
| `hazard_assessment.comments` / `clarification_questions` | Same shape as the RTM reviewer — short prose plus closed-ended questions to drive reviewer follow-up |

### Single Test Case Reviewer output fields

| Field | Description |
|-------|-------------|
| `decomposed_requirements` | Each traced requirement broken into atomic specs (same `DecomposedSpec` shape as the RTM reviewer) |
| `coverage_analysis` / `logical_structure_analysis` / `prereqs_analysis` | Three parallel `SpecAnalysis` lists — one per axis — each entry: `{spec_id, exists (bool), assessment}` |
| `aggregated_assessment.evaluated_checklist` | The input `review_objectives` checklist populated with `verdict` (`Yes`/`No`), a `partial` flag (drives Yellow rendering when verdict is `Yes` but coverage is materially incomplete), and an `assessment` rationale per item |
| `aggregated_assessment.overall_verdict` | `Yes` iff every objective is `Yes`; partial-Yes still counts as `Yes` |
| `aggregated_assessment.comments` / `clarification_questions` | Same shape as the other reviewers |

---

## Getting Started

### Prerequisites

- Python 3.12+
- [`uv`](https://docs.astral.sh/uv/) package manager
- An OpenAI API key

### Installation

```bash
git clone <repo-url>
cd autoqa
uv sync --frozen
```

### Environment Setup

Create a `.env` file in the repo root:

```env
# Required
API_KEY=<your api key>
API_BASE_URL=<your api url>
API_MODEL=<your model name>

# Optional — defaults shown
MAX_REQUESTS_PER_MINUTE=490
MAX_TOKENS_PER_MINUTE=200000
MAX_OUTPUT_TOKENS=16000

# Production settings (optional)
ENVIRONMENT=development  # Set to 'production' to disable /docs and /redoc
ALLOWED_ORIGINS=*        # Comma-separated list for CORS (e.g., https://app.example.com)
```

**Environment Variable Reference**:

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `OPENAI_API_KEY` | Yes | — | API key for the LLM service |
| `OPENAI_API_BASE_URL` | Yes | — | Base URL for the API endpoint |
| `OPENAI_MODEL` | Yes | — | Model identifier (e.g., `gpt-4o`, `gpt-4o-mini`) |
| `MAX_REQUESTS_PER_MINUTE` | No | 490 | Rate limit for API requests (buffer under 500 RPM) |
| `MAX_TOKENS_PER_MINUTE` | No | 200000 | Token rate limit (adjust based on your account tier) |
| `MAX_OUTPUT_TOKENS` | No | 16000 | Maximum output tokens per request (Haiku supports up to 16K) |
| `ENVIRONMENT` | No | `development` | Set to `production` to disable interactive API docs |
| `ALLOWED_ORIGINS` | No | `*` | CORS allowed origins (comma-separated, use `*` for development only) |

---

## Running Tests

```bash
# Unit tests (no API key required — all LLM calls are mocked)
uv run pytest tests/unit/ -v

# Integration tests (requires a live OPENAI_API_KEY in .env)
uv run pytest -m integration -v

# Parameterized batch run — 10 HC requirement records, records inputs.jsonl + outputs.jsonl
uv run pytest tests/integration/test_pipeline.py::test_pipeline_parametrized -m integration -s

uv run pytest tests/integration/test_pipeline.py::test_pipeline_parametrized_standard_coverage -m integration -s

uv run pytest tests/integration/test_pipeline.py::test_pipeline_parametrized_advanced_coverage -m integration -s

# Hazard coverage reviewer pipeline (uses tests/fixtures/sample_hazard.json)
uv run pytest tests/integration/test_hazard_pipeline.py -m integration -s
```

The unit test suite covers all pipeline nodes with both plain-JSON and markdown-wrapped LLM response variants. JSONL fixture files in `tests/fixtures/` make it easy to add new test scenarios — append a line to the relevant file and it is automatically picked up by `@pytest.mark.parametrize`.

The integration test suite includes a session-scoped `jsonl_recorders` fixture that writes `inputs.jsonl` and `outputs.jsonl` to the active `logs/run-.../` folder, enabling offline analysis of model outputs across a batch of requirements. On session teardown the fixture also invokes `autoqa.viewer.write_viewer` to emit a self-contained HTML reviewer UI (`viewer.html`) whenever `outputs.jsonl` has records — no manual step required.

All run artifacts are written to a timestamped `logs/run-<datetime>/` directory:

| File | Contents |
|------|----------|
| `autoqa.log` | Structured application logs |
| `graph.png` | Mermaid diagram of the compiled LangGraph |
| `pipeline_state.json` | Full serialized state from a single pipeline run |
| `inputs.jsonl` | Input records fed to the parametrized test |
| `outputs.jsonl` | Serialized pipeline state for each parametrized run |
| `viewer.html` | Single-file HTML reviewer UI built from `outputs.jsonl` — auto-generated at session teardown |

---

## HTML Reviewer Viewer

Each batch run auto-emits `viewer.html` alongside `outputs.jsonl`. It is a single static file with inlined JSON and vanilla JavaScript — no server, CDN, or build step. Open it directly in a browser to page through the batch.

**Left panel (information):**
- `req_id` chip + full requirement text
- Clickable test-case list — opens a modal with the raw TC and its AI-parsed summary (objective, protocol, acceptance criteria)
- Coverage Assessment: overall Yes/No verdict badge (green/orange) plus a bulleted M1–M5 findings table with Yes/No/N-A chips, cited TC IDs, and uncovered spec IDs
- A "Decomposed specs & coverage analysis →" link opens a dialog showing every decomposed spec color-coded light green (covered) or light orange (uncovered), with per-spec covering TCs and dimension chips (`functional` / `negative` / `boundary`)
- Synthesizer `comments` and `clarification_questions` (rendered only when non-empty)

**Right panel (feedback capture):**
- 1–5 reviewer rating radios
- Free-text notes
- Prev / Save & Next navigation with a progress counter
- Ratings + notes persist to browser `localStorage` (keyed by `req_id`) and are exportable as a JSON blob via the header's **Export feedback JSON** button

### Regenerating the viewer manually

```bash
# module form
uv run python -m autoqa.viewer logs/run-<ts>/outputs.jsonl

# skill form (equivalent — the skill at .claude/skills/visualize-batch-outputs
# is a thin CLI wrapper over autoqa.viewer)
uv run python .claude/skills/visualize-batch-outputs/generate_viewer.py \
  logs/run-<ts>/outputs.jsonl
```

Use the `-o <path>` flag to redirect output. The viewer is also importable:

```python
from autoqa.viewer import write_viewer
write_viewer("logs/run-2026-04-22-09-00-00/outputs.jsonl")
```

### Package layout

```
autoqa/viewer/
├── __init__.py   # public API: build_viewer, write_viewer, HTML_TEMPLATE
├── __main__.py   # enables `python -m autoqa.viewer`
├── generator.py  # build_viewer / write_viewer / CLI main()
└── template.py   # HTML_TEMPLATE raw string (placeholders: {{TITLE}}, {{SOURCE}}, {{RUN_KEY}}, {{DATA}})
```

---

## API Usage

### Starting the Server

```bash
uv run uvicorn autoqa.api.main:app --reload
```

The interactive API documentation is available at `http://localhost:8000/docs` once the server is running. At startup the lifespan handler builds a single shared `RTMReviewerRunnable` and reuses it inside the hazard pipeline's `RequirementReviewerNode`, so the RTM graph compiles and renders `graph.png` only once per process even though both endpoints exercise it.

### Endpoint Reference

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/v1/health` | Health check for load balancers and monitoring |
| `POST` | `/api/v1/review` | Submit a requirement + test suite for RTM coverage analysis (M1-M5 rubric) |
| `POST` | `/api/v1/hazard-review` | Submit a `HazardRecord` (hazard line item + traced requirements / test cases / design docs) for hazard mitigation coverage analysis (H1-H7 rubric) |

#### Health Check Endpoint

The `/api/v1/health` endpoint returns service availability status:

```bash
curl http://localhost:8000/api/v1/health
```

**Response (healthy)**:
```json
{
  "status": "healthy",
  "version": "0.2.0",
  "services": {
    "rtm_service": "available",
    "hazard_service": "available"
  }
}
```

**Response (unhealthy)**: Returns 503 status code with error details.

---

### Test Suite Reviewer — `/api/v1/review`

#### Quickstart: curl

```bash
curl -X POST http://localhost:8000/api/v1/review \
  -H "Content-Type: application/json" \
  -d '{
    "thread_id": "review-session-001",
    "requirement": {
      "req_id": "SRS-042",
      "text": "The system shall generate an audible and visual alarm within 5 seconds when the measured glucose concentration exceeds the user-configured high threshold."
    },
    "test_cases": [
      {
        "test_id": "TC-101",
        "description": "Verify high-glucose alarm activation",
        "setup": "Device powered on, high threshold set to 180 mg/dL",
        "steps": "Simulate glucose reading of 200 mg/dL via test fixture",
        "expectedResults": "Audible alarm sounds and alert banner displayed within 5 seconds"
      },
      {
        "test_id": "TC-102",
        "description": "Verify no alarm fires when reading is below threshold",
        "setup": "Device powered on, high threshold set to 180 mg/dL",
        "steps": "Simulate glucose reading of 150 mg/dL",
        "expectedResults": "No alarm triggered"
      }
    ]
  }'
```

**Example response:**

```json
{
  "status": "completed",
  "thread_id": "review-session-001",
  "coverage_analysis": [
    {
      "spec_id": "SRS-042-01",
      "covered_exists": true,
      "covered_by_test_cases": [
        {
          "test_case_id": "TC-101",
          "dimensions": ["functional"],
          "rationale": "TC-101 verifies the alarm fires above the configured threshold within the required 5-second window."
        },
        {
          "test_case_id": "TC-102",
          "dimensions": ["negative"],
          "rationale": "TC-102 verifies no alarm fires when the reading is below threshold."
        }
      ],
      "coverage_rationale": "TC-101 covers the positive case above threshold; TC-102 covers the below-threshold negative case. No test exercises the exact threshold value (180 mg/dL), leaving the boundary dimension uncovered for this spec."
    }
  ],
  "decomposed_requirement": {},
  "test_suite": {},
  "synthesized_assessment": {
    "requirement": {"req_id": "SRS-042", "text": "..."},
    "overall_verdict": "No",
    "mandatory_findings": [
      {"code": "M1", "dimension": "Functional", "verdict": "Yes", "rationale": "TC-101 verifies alarm activation above threshold.", "cited_test_case_ids": ["TC-101"], "uncovered_spec_ids": []},
      {"code": "M2", "dimension": "Negative", "verdict": "Yes", "rationale": "TC-102 verifies no alarm below threshold.", "cited_test_case_ids": ["TC-102"], "uncovered_spec_ids": []},
      {"code": "M3", "dimension": "Boundary", "verdict": "No", "rationale": "No test exercises the exact 180 mg/dL threshold or the 5-second timing boundary.", "cited_test_case_ids": [], "uncovered_spec_ids": []},
      {"code": "M4", "dimension": "Spec Coverage", "verdict": "Yes", "rationale": "all specs covered", "cited_test_case_ids": [], "uncovered_spec_ids": []},
      {"code": "M5", "dimension": "Terminology", "verdict": "Yes", "rationale": "aligned", "cited_test_case_ids": [], "uncovered_spec_ids": []}
    ],
    "comments": "Boundary coverage is absent at the exact 180 mg/dL threshold and at the upper edge of the 5-second latency window.",
    "clarification_questions": [
      "Should a boundary test at exactly 180 mg/dL be added, or is boundary behavior covered by a separate latency-specific requirement?"
    ]
  }
}
```

#### Quickstart: Python

```python
import asyncio
import httpx

payload = {
    "thread_id": "review-session-001",
    "requirement": {
        "req_id": "SRS-042",
        "text": "The system shall generate an audible and visual alarm within 5 seconds when the measured glucose concentration exceeds the user-configured high threshold."
    },
    "test_cases": [
        {
            "test_id": "TC-101",
            "description": "Verify high-glucose alarm activation",
            "setup": "Device powered on, high threshold set to 180 mg/dL",
            "steps": "Simulate glucose reading of 200 mg/dL via test fixture",
            "expectedResults": "Audible alarm sounds and alert banner displayed within 5 seconds"
        }
    ]
}

async def main():
    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(
            "http://localhost:8000/api/v1/review",
            json=payload,
        )
        result = response.json()

    for spec in result["coverage_analysis"]:
        dims = sorted({d for ctc in spec["covered_by_test_cases"] for d in ctc["dimensions"]})
        tcs = [ctc["test_case_id"] for ctc in spec["covered_by_test_cases"]]
        print(f"[{spec['spec_id']}] covered={spec['covered_exists']}  dimensions={dims}")
        print(f"  Covered by: {tcs}")
        print(f"  Rationale:  {spec['coverage_rationale']}\n")

    assessment = result.get("synthesized_assessment") or {}
    print(f"Overall verdict: {assessment.get('overall_verdict')}")
    for finding in assessment.get("mandatory_findings", []):
        print(f"  [{finding['code']} {finding['dimension']}] {finding['verdict']} — {finding['rationale']}")
    if assessment.get("comments"):
        print(f"\nComments: {assessment['comments']}")
    for q in assessment.get("clarification_questions", []):
        print(f"  ? {q}")

asyncio.run(main())
```

---

### Hazard Coverage Reviewer — `/api/v1/hazard-review`

The endpoint accepts a single `HazardRecord` carrying the hazard register fields (per ISO 14971 / IEC 62304) plus the requirements, test cases, and design docs traced to that hazard. The pipeline fans out one parallel RTM review per traced requirement, then applies the H1-H5 rubric across the full hazard envelope. A complete sample input is at `tests/fixtures/sample_hazard.json`.

#### Quickstart: curl

```bash
curl -X POST http://localhost:8000/api/v1/hazard-review \
  -H "Content-Type: application/json" \
  -d @tests/fixtures/sample_hazard_request.json
```

where `sample_hazard_request.json` wraps the fixture with a `thread_id`:

```json
{
  "thread_id": "hazard-session-001",
  "hazard": {
    "hazard_id": "HAZ-PUMP-001",
    "hazardous_situation_id": "HS-PUMP-001",
    "hazard": "Over-infusion of medication due to software loop hang",
    "hazardous_situation": "Patient receives medication at the maximum pump rate continuously...",
    "function": "Continuous infusion rate control loop",
    "ots_software": "FreeRTOS 10.4.3",
    "hazardous_sequence_of_events": "1. Periodic timer ISR fails to fire... 2. Rate-control loop continues...",
    "software_related_causes": "Scheduler stall under heavy task load; missing independent watchdog...",
    "harm_severity_rationale": "External risk controls reduce but do not eliminate...",
    "harm": "Severe over-infusion with potential for life-threatening overdose",
    "severity": "Catastrophic",
    "exploitability_pre_mitigation": "Not applicable",
    "probability_of_harm_pre_mitigation": "Probable",
    "initial_risk_rating": "Unacceptable",
    "risk_control_measures": "REQ-PUMP-101 mandates an independent hardware watchdog...",
    "demonstration_of_effectiveness": "Verified by TC-PUMP-201, TC-PUMP-202, TC-PUMP-203.",
    "severity_of_harm_post_mitigation": "Catastrophic",
    "exploitability_post_mitigation": "Not applicable",
    "probability_of_harm_post_mitigation": "Remote",
    "final_risk_rating": "Acceptable",
    "new_hs_reference": "",
    "sw_fmea_trace": "FMEA-PUMP-RC-001",
    "sra_link": "SRA-PUMP-2025-12",
    "urra_item": "URRA-PUMP-RC-001",
    "residual_risk_acceptability": "Per GQP-10-02 Risk Management Report, residual risk is acceptable...",
    "requirements": [
      {"req_id": "REQ-PUMP-101", "text": "The rate-control loop shall be monitored by an independent hardware watchdog..."},
      {"req_id": "REQ-PUMP-102", "text": "The UI thread shall render an Alarm Mode banner..."}
    ],
    "test_cases": [
      {"test_id": "TC-PUMP-201", "description": "Functional verification of watchdog heartbeat...", "setup": "...", "steps": "...", "expectedResults": "..."},
      {"test_id": "TC-PUMP-202", "description": "Fault injection — simulate scheduler stall...", "setup": "...", "steps": "...", "expectedResults": "..."},
      {"test_id": "TC-PUMP-203", "description": "Boundary — heartbeat latency at 200 ms threshold...", "setup": "...", "steps": "...", "expectedResults": "..."}
    ],
    "design_docs": [
      {"doc_id": "DD-PUMP-RC-001", "name": "Rate Control Loop and Watchdog Architecture", "description": "..."}
    ]
  }
}
```

#### Quickstart: Python

```python
import asyncio
import json
from pathlib import Path

import httpx

hazard = json.loads(Path("tests/fixtures/sample_hazard.json").read_text())
payload = {"thread_id": "hazard-session-001", "hazard": hazard}

async def main():
    async with httpx.AsyncClient(timeout=300) as client:
        response = await client.post(
            "http://localhost:8000/api/v1/hazard-review",
            json=payload,
        )
        result = response.json()

    assessment = result.get("hazard_assessment") or {}
    print(f"Hazard {assessment.get('hazard_id')}: {assessment.get('overall_verdict')}")
    for f in assessment.get("mandatory_findings", []):
        print(f"  [{f['code']} {f['dimension']}] {f['verdict']} — {f['rationale']}")
        if f.get("cited_req_ids"):
            print(f"      cited reqs:  {f['cited_req_ids']}")
        if f.get("cited_test_case_ids"):
            print(f"      cited tests: {f['cited_test_case_ids']}")
        if f.get("unblocked_items"):
            print(f"      unblocked:   {f['unblocked_items']}")

    # Drill into each per-requirement RTM assessment that fed the H1-H5 roll-up
    for review in result.get("requirement_reviews", []):
        sa = review.get("synthesized_assessment") or {}
        print(f"\n  {review['requirement']['req_id']}: {sa.get('overall_verdict')}")
        for mf in sa.get("mandatory_findings", []):
            print(f"    [{mf['code']}] {mf['verdict']} — {mf['rationale']}")

asyncio.run(main())
```

H5 (Verification Depth and Hazard-Path Effectiveness) is the only finding that may be `N-A` — it applies when `software_related_causes` indicates no software cause, in which case test-case verification is not required for that hazard. H1, H2, H3, H4, H6, and H7 must always resolve to `Yes` or `No`.

---

### Single Test Case Reviewer — library API

The single-test-case reviewer is currently library-only (no HTTP endpoint). Construct a `TCReviewerRunnable` directly and invoke its compiled graph. The default review-objectives checklist lives at `autoqa/components/test_case_reviewer/review_objectives.yaml`; load it with `load_default_review_objectives()` or substitute your own list of `ReviewObjective` rows.

```python
import asyncio

from autoqa.components.clients import RateLimitOpenAIClient
from autoqa.components.shared.core import Requirement, TestCase
from autoqa.components.test_case_reviewer.nodes import load_default_review_objectives
from autoqa.components.test_case_reviewer.pipeline import TCReviewerRunnable
from autoqa.core.config import settings


async def main():
    client = RateLimitOpenAIClient(api_key=settings.openai_api_key)
    runnable = TCReviewerRunnable(client=client, model=settings.model)

    test_case = TestCase(
        test_id="TC-101",
        description="Verify high-glucose alarm activation",
        setup="Device powered on, high threshold set to 180 mg/dL",
        steps="Simulate glucose reading of 200 mg/dL via test fixture",
        expectedResults="Audible alarm sounds and alert banner displayed within 5 seconds",
    )
    requirements = [
        Requirement(
            req_id="SRS-042",
            text="The system shall generate an audible and visual alarm within 5 seconds when the measured glucose concentration exceeds the user-configured high threshold.",
        )
    ]

    result = await runnable.graph.ainvoke({
        "test_case": test_case,
        "requirements": requirements,
        "review_objectives": load_default_review_objectives(),
    })

    assessment = result.get("aggregated_assessment")
    print(f"Overall verdict: {assessment.overall_verdict}")
    for item in assessment.evaluated_checklist:
        partial = " (partial)" if item.partial else ""
        print(f"  [{item.id}] {item.verdict}{partial} — {item.assessment}")
    if assessment.comments:
        print(f"\nComments: {assessment.comments}")
    for q in assessment.clarification_questions:
        print(f"  ? {q}")

asyncio.run(main())
```

The pipeline emits three independent `SpecAnalysis` lists on the final state — `coverage_analysis`, `logical_structure_analysis`, and `prereqs_analysis` — which the aggregator collapses into the populated `evaluated_checklist`. Inspect the per-axis lists directly when you need to see why the aggregator settled on a given verdict.

---

## Production Deployment Guidance

### Overview

AutoQA is designed to run as a containerized FastAPI service behind a reverse proxy. This section covers deployment patterns, security hardening, observability, and operational considerations for production environments subject to FDA 21 CFR Part 11 / EU MDR Annex I software lifecycle controls.

### Architecture Patterns

#### Single-Instance Deployment (Development / Small Teams)

```
┌─────────────────────────────────────────────┐
│  Reverse Proxy (nginx / Traefik)           │
│  - TLS termination                          │
│  - Rate limiting (optional)                 │
│  - Request logging                          │
└─────────────────┬───────────────────────────┘
                  │
                  ↓
┌─────────────────────────────────────────────┐
│  AutoQA FastAPI Service                     │
│  - uvicorn --workers 1                      │
│  - In-memory checkpointer                   │
│  - Shared RTMReviewerRunnable               │
└─────────────────┬───────────────────────────┘
                  │
                  ↓
┌─────────────────────────────────────────────┐
│  OpenAI-Compatible LLM API                  │
│  (OpenAI / Azure OpenAI / self-hosted)      │
└─────────────────────────────────────────────┘
```

**Characteristics:**
- Single uvicorn worker process
- Suitable for ≤10 concurrent review sessions
- No shared state persistence (thread history lost on restart)
- Simplest to deploy and debug

**When to use:** Internal QA team tools, proof-of-concept deployments, environments where review sessions complete within a single request/response cycle.

#### Multi-Worker Deployment (Production / High Availability)

```
┌─────────────────────────────────────────────┐
│  Load Balancer (ALB / nginx / Traefik)     │
│  - TLS termination                          │
│  - Health checks (/api/v1/health)           │
│  - Sticky sessions (optional)               │
└─────────────────┬───────────────────────────┘
                  │
        ┌─────────┴─────────┬─────────────┐
        ↓                   ↓             ↓
┌───────────────┐  ┌───────────────┐  ┌───────────────┐
│ AutoQA Pod 1  │  │ AutoQA Pod 2  │  │ AutoQA Pod N  │
│ uvicorn       │  │ uvicorn       │  │ uvicorn       │
│ --workers 4   │  │ --workers 4   │  │ --workers 4   │
└───────┬───────┘  └───────┬───────┘  └───────┬───────┘
        │                  │                  │
        └──────────────────┴──────────────────┘
                           │
                           ↓
        ┌──────────────────────────────────────┐
        │  Shared Checkpointer (PostgreSQL)    │
        │  - Thread state persistence          │
        │  - Cross-pod session continuity      │
        └──────────────────────────────────────┘
                           │
                           ↓
        ┌──────────────────────────────────────┐
        │  OpenAI-Compatible LLM API           │
        └──────────────────────────────────────┘
```

**Characteristics:**
- Multiple pods/containers, each running 2-4 uvicorn workers
- Shared PostgreSQL checkpointer for thread state
- Horizontal scaling via pod replication
- Health-check-driven auto-recovery

**When to use:** Production environments with >10 concurrent users, regulatory audit requirements for session persistence, high-availability SLAs.

### Container Image

#### Dockerfile

```dockerfile
# syntax=docker/dockerfile:1
FROM python:3.12-slim AS builder

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Set working directory
WORKDIR /app

# Copy dependency files
COPY pyproject.toml uv.lock ./

# Install dependencies into a virtual environment
RUN uv sync --frozen --no-dev

# Runtime stage
FROM python:3.12-slim

WORKDIR /app

# Copy virtual environment from builder
COPY --from=builder /app/.venv /app/.venv

# Copy application code
COPY autoqa/ ./autoqa/
COPY .env.production .env

# Create non-root user
RUN useradd -m -u 1000 autoqa && \
    chown -R autoqa:autoqa /app

USER autoqa

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
  CMD python -c "import httpx; httpx.get('http://localhost:8000/api/v1/health', timeout=5.0)"

# Run uvicorn
CMD ["/app/.venv/bin/uvicorn", "autoqa.api.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "4", \
     "--log-config", "autoqa/core/logging_config.json"]
```

#### Build and Run

```bash
# Build
docker build -t autoqa:latest .

# Run (single container)
docker run -d \
  --name autoqa \
  -p 8000:8000 \
  --env-file .env.production \
  --restart unless-stopped \
  autoqa:latest

# View logs
docker logs -f autoqa

# Health check
curl http://localhost:8000/api/v1/health
```

### Kubernetes Deployment

#### Deployment Manifest

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: autoqa
  namespace: qa-tools
  labels:
    app: autoqa
    version: v0.2.0
spec:
  replicas: 3
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxSurge: 1
      maxUnavailable: 0
  selector:
    matchLabels:
      app: autoqa
  template:
    metadata:
      labels:
        app: autoqa
        version: v0.2.0
    spec:
      securityContext:
        runAsNonRoot: true
        runAsUser: 1000
        fsGroup: 1000
      containers:
      - name: autoqa
        image: your-registry.io/autoqa:v0.2.0
        imagePullPolicy: IfNotPresent
        ports:
        - containerPort: 8000
          name: http
          protocol: TCP
        env:
        - name: ENVIRONMENT
          value: "production"
        - name: OPENAI_API_KEY
          valueFrom:
            secretKeyRef:
              name: autoqa-secrets
              key: openai-api-key
        - name: OPENAI_API_BASE_URL
          valueFrom:
            configMapKeyRef:
              name: autoqa-config
              key: api-base-url
        - name: OPENAI_MODEL
          valueFrom:
            configMapKeyRef:
              name: autoqa-config
              key: model
        - name: MAX_REQUESTS_PER_MINUTE
          value: "490"
        - name: MAX_TOKENS_PER_MINUTE
          value: "200000"
        - name: MAX_OUTPUT_TOKENS
          value: "16000"
        - name: ALLOWED_ORIGINS
          value: "https://qa.example.com,https://app.example.com"
        resources:
          requests:
            cpu: "1000m"
            memory: "2Gi"
          limits:
            cpu: "2000m"
            memory: "4Gi"
        livenessProbe:
          httpGet:
            path: /api/v1/health
            port: 8000
          initialDelaySeconds: 30
          periodSeconds: 30
          timeoutSeconds: 10
          failureThreshold: 3
        readinessProbe:
          httpGet:
            path: /api/v1/health
            port: 8000
          initialDelaySeconds: 10
          periodSeconds: 10
          timeoutSeconds: 5
          failureThreshold: 2
        volumeMounts:
        - name: logs
          mountPath: /app/logs
      volumes:
      - name: logs
        emptyDir: {}
---
apiVersion: v1
kind: Service
metadata:
  name: autoqa
  namespace: qa-tools
spec:
  type: ClusterIP
  selector:
    app: autoqa
  ports:
  - port: 80
    targetPort: 8000
    protocol: TCP
    name: http
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: autoqa-config
  namespace: qa-tools
data:
  api-base-url: "https://api.openai.com/v1"
  model: "gpt-4o"
---
apiVersion: v1
kind: Secret
metadata:
  name: autoqa-secrets
  namespace: qa-tools
type: Opaque
stringData:
  openai-api-key: "sk-..."
```

#### Ingress (TLS Termination)

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: autoqa-ingress
  namespace: qa-tools
  annotations:
    cert-manager.io/cluster-issuer: "letsencrypt-prod"
    nginx.ingress.kubernetes.io/rate-limit: "100"
    nginx.ingress.kubernetes.io/ssl-redirect: "true"
spec:
  ingressClassName: nginx
  tls:
  - hosts:
    - autoqa.example.com
    secretName: autoqa-tls
  rules:
  - host: autoqa.example.com
    http:
      paths:
      - path: /
        pathType: Prefix
        backend:
          service:
            name: autoqa
            port:
              number: 80
```

### Security Hardening

#### 1. API Key Management

**DO NOT** commit API keys to version control. Use one of:

- **Kubernetes Secrets** (shown above)
- **AWS Secrets Manager** / **Azure Key Vault** / **GCP Secret Manager**
- **HashiCorp Vault**

**Example: AWS Secrets Manager integration**

```python
# autoqa/core/config.py (add to Settings class)
import boto3
from botocore.exceptions import ClientError

def _load_secret_from_aws(secret_name: str, region: str = "us-east-1") -> str:
    client = boto3.client("secretsmanager", region_name=region)
    try:
        response = client.get_secret_value(SecretId=secret_name)
        return response["SecretString"]
    except ClientError as e:
        raise RuntimeError(f"Failed to retrieve secret {secret_name}: {e}")

class Settings(BaseSettings):
    openai_api_key: str = Field(
        default_factory=lambda: (
            os.getenv("OPENAI_API_KEY") or
            _load_secret_from_aws("autoqa/openai-api-key")
        )
    )
```

#### 2. CORS Configuration

**Development:**
```env
ALLOWED_ORIGINS=*
```

**Production:**
```env
ALLOWED_ORIGINS=https://qa.example.com,https://app.example.com
```

The FastAPI CORS middleware (in `autoqa/api/main.py`) parses this comma-separated list and rejects requests from unlisted origins.

#### 3. Rate Limiting

AutoQA includes client-side rate limiting (via `RateLimitOpenAIClient`) to stay under LLM provider quotas. Add **server-side** rate limiting at the reverse proxy layer:

**nginx:**
```nginx
http {
    limit_req_zone $binary_remote_addr zone=autoqa:10m rate=10r/s;

    server {
        location /api/ {
            limit_req zone=autoqa burst=20 nodelay;
            proxy_pass http://autoqa:8000;
        }
    }
}
```

**Traefik:**
```yaml
http:
  middlewares:
    autoqa-ratelimit:
      rateLimit:
        average: 10
        burst: 20
  routers:
    autoqa:
      rule: "Host(`autoqa.example.com`)"
      middlewares:
        - autoqa-ratelimit
      service: autoqa
```

#### 4. Input Validation

All API endpoints use Pydantic models with strict validation. Additional hardening:

- **Max payload size:** Configure uvicorn `--limit-max-requests` and nginx `client_max_body_size`
- **Timeout enforcement:** Set `httpx.AsyncClient(timeout=...)` in client code (already configured)
- **Schema validation:** Pydantic models reject malformed JSON and enforce field constraints

#### 5. Audit Logging

AutoQA logs all pipeline invocations to `logs/run-<timestamp>/autoqa.log`. For regulatory compliance:

**Structured logging to stdout (for log aggregation):**

```python
# autoqa/core/logging_config.json
{
  "version": 1,
  "disable_existing_loggers": false,
  "formatters": {
    "json": {
      "class": "pythonjsonlogger.jsonlogger.JsonFormatter",
      "format": "%(asctime)s %(name)s %(levelname)s %(message)s"
    }
  },
  "handlers": {
    "console": {
      "class": "logging.StreamHandler",
      "formatter": "json",
      "stream": "ext://sys.stdout"
    }
  },
  "root": {
    "level": "INFO",
    "handlers": ["console"]
  }
}
```

**Ship logs to a SIEM:**
- **ELK Stack** (Elasticsearch + Logstash + Kibana)
- **Splunk**
- **Datadog** / **New Relic**
- **AWS CloudWatch Logs**

**Key fields to index:**
- `thread_id` (correlates requests within a review session)
- `req_id` / `hazard_id` (artifact under review)
- `overall_verdict` (Yes/No outcome)
- `user_id` (if authentication is added)
- `timestamp`, `duration_ms`, `model`, `token_count`

### Observability

#### Health Checks

The `/api/v1/health` endpoint returns:

```json
{
  "status": "healthy",
  "version": "0.2.0",
  "services": {
    "rtm_service": "available",
    "hazard_service": "available"
  }
}
```

**Unhealthy response (503):**
```json
{
  "status": "unhealthy",
  "version": "0.2.0",
  "services": {
    "rtm_service": "unavailable",
    "hazard_service": "unavailable"
  },
  "error": "Failed to initialize RTM reviewer: ..."
}
```

Configure load balancer health checks to poll this endpoint every 30s and remove unhealthy pods from rotation.

#### Metrics (Prometheus)

Add `prometheus-fastapi-instrumentator` for automatic metrics export:

```bash
uv add prometheus-fastapi-instrumentator
```

```python
# autoqa/api/main.py
from prometheus_fastapi_instrumentator import Instrumentator

app = FastAPI(...)
Instrumentator().instrument(app).expose(app, endpoint="/metrics")
```

**Key metrics:**
- `http_requests_total{method, endpoint, status}` — request count by endpoint
- `http_request_duration_seconds{method, endpoint}` — latency histogram
- `autoqa_pipeline_duration_seconds{pipeline}` — custom metric for RTM/hazard/TC pipeline wall-clock time
- `autoqa_llm_tokens_total{model, operation}` — token consumption tracking

**Grafana dashboard queries:**
```promql
# P95 latency for /api/v1/review
histogram_quantile(0.95, 
  rate(http_request_duration_seconds_bucket{endpoint="/api/v1/review"}[5m])
)

# Request rate by endpoint
sum(rate(http_requests_total[5m])) by (endpoint)

# Error rate (5xx responses)
sum(rate(http_requests_total{status=~"5.."}[5m])) / 
sum(rate(http_requests_total[5m]))
```

#### Distributed Tracing (OpenTelemetry)

For multi-service environments, add OpenTelemetry instrumentation:

```bash
uv add opentelemetry-api opentelemetry-sdk opentelemetry-instrumentation-fastapi
```

```python
# autoqa/api/main.py
from opentelemetry import trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

trace.set_tracer_provider(TracerProvider())
trace.get_tracer_provider().add_span_processor(
    BatchSpanProcessor(OTLPSpanExporter(endpoint="http://jaeger:4317"))
)

app = FastAPI(...)
FastAPIInstrumentor.instrument_app(app)
```

Traces will include:
- HTTP request spans (endpoint, method, status, duration)
- LLM call spans (model, prompt tokens, completion tokens, latency)
- Database spans (if using PostgreSQL checkpointer)

### Environment-Specific Configuration

#### Development

```env
ENVIRONMENT=development
ALLOWED_ORIGINS=*
OPENAI_MODEL=gpt-4o-mini  # Cheaper model for testing
MAX_REQUESTS_PER_MINUTE=50
MAX_TOKENS_PER_MINUTE=50000
```

- Interactive API docs enabled at `/docs` and `/redoc`
- Permissive CORS
- Lower rate limits to avoid quota exhaustion

#### Staging

```env
ENVIRONMENT=production
ALLOWED_ORIGINS=https://staging.example.com
OPENAI_MODEL=gpt-4o
MAX_REQUESTS_PER_MINUTE=490
MAX_TOKENS_PER_MINUTE=200000
```

- API docs disabled
- Restricted CORS
- Production-equivalent rate limits
- Separate OpenAI project/key for cost tracking

#### Production

```env
ENVIRONMENT=production
ALLOWED_ORIGINS=https://qa.example.com,https://app.example.com
OPENAI_MODEL=gpt-4o
MAX_REQUESTS_PER_MINUTE=490
MAX_TOKENS_PER_MINUTE=200000
MAX_OUTPUT_TOKENS=16000

# Optional: Azure OpenAI
OPENAI_API_BASE_URL=https://your-resource.openai.azure.com/
OPENAI_API_VERSION=2024-02-15-preview
```

- API docs disabled
- Strict CORS
- Full rate limits
- Consider Azure OpenAI for enterprise SLAs and data residency

### Scaling Considerations

#### Vertical Scaling (Single Pod)

**Bottleneck:** LLM API latency (typically 2-10s per call)

**Optimization:**
- Increase uvicorn workers: `--workers 4` (1 per CPU core)
- Each worker handles requests concurrently via asyncio
- Effective concurrency = `workers × asyncio tasks per worker`

**Resource sizing:**
- **CPU:** 1-2 cores per worker (mostly I/O-bound, not CPU-intensive)
- **Memory:** 1-2 GB per worker (LangGraph state + prompt templates)
- **Recommended:** 4 workers × 2 GB = 8 GB pod

#### Horizontal Scaling (Multiple Pods)

**When to scale out:**
- Request queue depth > 10 (check load balancer metrics)
- P95 latency > 30s (indicates worker saturation)
- CPU utilization > 70% sustained

**Kubernetes HPA (Horizontal Pod Autoscaler):**

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: autoqa-hpa
  namespace: qa-tools
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: autoqa
  minReplicas: 2
  maxReplicas: 10
  metrics:
  - type: Resource
    resource:
      name: cpu
      target:
        type: Utilization
        averageUtilization: 70
  - type: Resource
    resource:
      name: memory
      target:
        type: Utilization
        averageUtilization: 80
  behavior:
    scaleUp:
      stabilizationWindowSeconds: 60
      policies:
      - type: Percent
        value: 50
        periodSeconds: 60
    scaleDown:
      stabilizationWindowSeconds: 300
      policies:
      - type: Pods
        value: 1
        periodSeconds: 60
```

#### Rate Limit Tuning

**OpenAI rate limits (as of 2024):**

| Tier | RPM | TPM | Max Tokens/Request |
|------|-----|-----|--------------------|
| Free | 3 | 40K | 4K |
| Tier 1 | 500 | 200K | 16K |
| Tier 2 | 5000 | 2M | 16K |
| Tier 3 | 10000 | 10M | 16K |

**AutoQA defaults (Tier 1):**
```env
MAX_REQUESTS_PER_MINUTE=490  # 2% buffer under 500 RPM
MAX_TOKENS_PER_MINUTE=200000
```

**Multi-pod deployments:**

If running 3 pods, each pod should configure:
```env
MAX_REQUESTS_PER_MINUTE=163  # 490 / 3
MAX_TOKENS_PER_MINUTE=66666  # 200000 / 3
```

Alternatively, use a **shared rate limiter** (Redis-backed) to coordinate across pods:

```python
# autoqa/components/clients.py (future enhancement)
import redis.asyncio as redis
from aiolimiter import AsyncLimiter

class SharedRateLimitOpenAIClient:
    def __init__(self, redis_url: str, ...):
        self.redis = redis.from_url(redis_url)
        self.limiter = AsyncLimiter(
            max_rate=490,
            time_period=60,
            storage=RedisStorage(self.redis)
        )
```

### Backup and Disaster Recovery

#### State Persistence

**Current:** In-memory checkpointer (thread state lost on restart)

**Production:** PostgreSQL checkpointer (planned)

```python
# autoqa/api/main.py (future)
from langgraph.checkpoint.postgres import PostgresSaver

checkpointer = PostgresSaver(
    connection_string=settings.postgres_url,
    serde=JsonPlusSerializer(),
)

runnable = RTMReviewerRunnable(
    client=client,
    model=settings.model,
    checkpointer=checkpointer,
)
```

**Backup strategy:**
- **PostgreSQL:** Daily automated backups via `pg_dump` or managed service snapshots
- **Retention:** 30 days for audit compliance
- **Restore testing:** Quarterly DR drills

#### Log Archival

**Regulatory requirement:** Retain all review session logs for 7+ years (FDA 21 CFR Part 11)

**Implementation:**
- Ship logs to S3 / Azure Blob / GCS with lifecycle policies
- Compress and encrypt at rest
- Tag with `project_id`, `device_id`, `submission_id` for retrieval

**Example: S3 lifecycle policy**
```json
{
  "Rules": [
    {
      "Id": "archive-autoqa-logs",
      "Status": "Enabled",
      "Transitions": [
        {"Days": 90, "StorageClass": "STANDARD_IA"},
        {"Days": 365, "StorageClass": "GLACIER"}
      ],
      "Expiration": {"Days": 2555}
    }
  ]
}
```

### Cost Optimization

#### LLM Token Usage

**Typical costs (GPT-4o as of 2024):**
- Input: $2.50 / 1M tokens
- Output: $10.00 / 1M tokens

**Per-review estimates:**

| Pipeline | Avg Input Tokens | Avg Output Tokens | Cost per Review |
|----------|------------------|-------------------|------------------|
| RTM (1 req, 5 TCs) | 8,000 | 3,000 | $0.05 |
| Hazard (1 hazard, 3 reqs) | 15,000 | 5,000 | $0.09 |
| Test Case (1 TC, 2 reqs) | 5,000 | 2,000 | $0.03 |

**Monthly cost projection (1000 reviews/month):**
- RTM: $50
- Hazard: $90
- Total: ~$140/month

**Cost reduction strategies:**

1. **Use cheaper models for decomposition/summarization:**
   ```python
   # Use gpt-4o-mini for non-critical nodes
   decomposer_model = "gpt-4o-mini"  # $0.15/$0.60 per 1M tokens
   synthesizer_model = "gpt-4o"      # Keep expensive model for final verdict
   ```

2. **Prompt compression:**
   - Remove redundant context from prompts
   - Use shorter system messages
   - Compress test case descriptions (extract only setup/steps/expected)

3. **Caching (future):**
   - Cache decomposed requirements (reuse across multiple test suite updates)
   - Cache test case summaries

4. **Batch processing:**
   - Group multiple requirements into a single LLM call (trade latency for cost)

### Compliance and Validation

#### FDA 21 CFR Part 11 / EU MDR Considerations

AutoQA is a **software tool used in the design and development** of medical device software. It is **not** a medical device itself, but its outputs may be included in regulatory submissions (DHF, 510(k), PMA).

**Key requirements:**

1. **Audit Trail:**
   - All review sessions logged with `thread_id`, `req_id`, `timestamp`, `user_id`, `verdict`
   - Logs immutable and retained for device lifetime + 2 years (FDA) or 10 years (EU MDR)

2. **Validation:**
   - AutoQA must be validated per IEC 62304 Annex B (SOUP) or as a custom tool
   - Validation package includes:
     - Requirements specification (this README + API docs)
     - Test protocol (pytest suite in `tests/`)
     - Test results (CI/CD pipeline outputs)
     - Traceability matrix (requirements → test cases)

3. **Version Control:**
   - Pin AutoQA version in production (`autoqa:v0.2.0`)
   - Document version in DHF submissions
   - Re-validate on major version upgrades

4. **Access Control:**
   - Add authentication (OAuth2 / SAML) to API endpoints
   - Role-based access: QA Engineer (read/write), Auditor (read-only)

**Example: OAuth2 integration (future)**

```python
# autoqa/api/auth.py
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token")

async def get_current_user(token: str = Depends(oauth2_scheme)):
    # Validate JWT token, extract user_id
    user = await verify_token(token)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
        )
    return user

# Apply to endpoints
@app.post("/api/v1/review")
async def review_endpoint(
    request: ReviewRequest,
    user: User = Depends(get_current_user),
):
    # Log user_id with request
    logger.info(f"Review initiated by {user.id} for {request.requirement.req_id}")
    ...
```

### Troubleshooting

#### Common Issues

**1. Health check fails with "rtm_service: unavailable"**

**Cause:** LangGraph compilation failed during startup

**Solution:**
- Check logs for `Failed to compile RTM graph: ...`
- Verify `OPENAI_API_KEY` is set and valid
- Ensure `OPENAI_MODEL` is a supported model (e.g., `gpt-4o`, not `gpt-3.5-turbo`)

**2. Requests timeout after 120s**

**Cause:** Hazard pipeline with many requirements exceeds default timeout

**Solution:**
- Increase client timeout: `httpx.AsyncClient(timeout=300)`
- Increase uvicorn timeout: `--timeout-keep-alive 300`
- Optimize: Reduce number of traced requirements per hazard

**3. Rate limit errors (429 from OpenAI)**

**Cause:** `MAX_REQUESTS_PER_MINUTE` / `MAX_TOKENS_PER_MINUTE` set too high

**Solution:**
- Check your OpenAI account tier at https://platform.openai.com/account/limits
- Reduce limits in `.env` to match your tier
- For multi-pod deployments, divide limits by pod count

**4. Out-of-memory errors in Kubernetes**

**Cause:** Too many uvicorn workers for allocated memory

**Solution:**
- Reduce `--workers` (e.g., from 4 to 2)
- Increase pod memory limit: `resources.limits.memory: 4Gi`
- Check for memory leaks: `kubectl top pod autoqa-xxx`

**5. Inconsistent verdicts across runs**

**Cause:** LLM non-determinism (temperature > 0)

**Solution:**
- Set `temperature=0` in LLM calls (already configured in prompts)
- Pin model version (e.g., `gpt-4o-2024-05-13` instead of `gpt-4o`)
- For critical reviews, run pipeline 3× and take majority vote

---

## Current Work

AutoQA is under active development. The following capabilities are on the near-term roadmap:

**Individual test case reviews** — In addition to per-specification coverage scoring, a planned node will provide a deep-dive review of each individual test case: assessing completeness, checking for ambiguous pass/fail criteria, and flagging test cases that do not satisfy IEC 62304 traceability requirements.

**Additional medical device document types** — The review pipeline will be extended beyond RTM artifacts to support other regulatory and quality documents common in medical device software development, including Software Requirements Specifications (SRS), risk management files per ISO 14971, and records of Software of Unknown Provenance (SOUP).

**Persistent thread state** — The current implementation uses an in-memory checkpointer, meaning thread history is lost on server restart. A database-backed checkpointer (SQLite or PostgreSQL) is planned to support long-running review sessions and audit trail preservation.

**Batch review endpoint** — A `POST /api/v1/batch-review` endpoint is planned to accept a full RTM table (multiple requirements and their associated test cases) and run the pipeline concurrently, returning a consolidated coverage report suitable for regulatory submission packages.