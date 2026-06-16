# QAAI — AI-Powered DHF Reviewer for Medical Device Software

## Background

QAAI is a software quality tool designed to assist QA engineers and regulatory teams in reviewing Design History File artifacts for medical device software developed under FDA guidance and the IEC 62304 / ISO 14971 lifecycle standards. These reviews must demonstrate that every software requirement is adequately verified by a corresponding test case, that each test case is itself well-formed, and that hazards in the risk register are mitigated by traceable controls. In practice, they are labor-intensive processes prone to coverage gaps, inconsistent rationale, and missed edge cases.

QAAI exposes three complementary reviewers, each implemented as an independent LangGraph pipeline that emits a structured, SoP-gating rubric:

| Reviewer                                                          | What it scores                                                                       | Output rubric                                                                                                                                                                                                  |
| ---------------------------------------------------------------- | ------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Test Suite Reviewer (RTM)** — `POST /api/v1/test-suite-review` | One requirement against its associated test suite                                    | M1-M5 mandatory findings (Functional / Negative / Boundary / Spec Coverage / Terminology) + R6 advisory (Design Alignment) → binary Yes/No coverage verdict                                                    |
| **Test Case Reviewer** — `POST /api/v1/test-case-review`         | One test case against its requirements and a checklist of review objectives          | Five review objectives (4 mandatory + 1 advisory), each with a Yes/No verdict and a `partial` flag for material gaps → binary Yes/No overall verdict                                                            |
| **Hazard Risk Reviewer** — `POST /api/v1/hazard-risk-review`     | One hazard register entry against its traced requirements + test cases + design docs | H1-H7 mandatory findings (Hazard Record Completeness / Software Contribution / Pre-Mitigation Risk / Risk Control Adequacy / Verification Depth / Residual Risk Closure / HSHA Update) → binary Yes/No verdict  |

All three reviewers cite the artifact IDs that support each finding, return short comments clarifying any gaps, and emit closed-ended clarification questions so reviewers can quickly confirm whether flagged gaps are real or N/A in context.

---

## Pipeline Architecture

Every reviewer is a LangGraph `StateGraph` that fans out via the `Send` API for maximum parallelism, then fans back in via `operator.add` reducers before a synthesizer node aggregates findings against the rubric. Each run also writes a Mermaid graph PNG (`graph.png`, `tc_graph.png`, or `hazard_graph.png`) into the run's log folder alongside `qaai.log`. The per-reviewer graph topologies are documented in the [design docs](docs/index.html) under `docs/design/`.

### Test Suite Reviewer (RTM coverage)

The RTM reviewer decomposes a requirement into atomic specs and summarizes its test cases in parallel, fans out one evaluation per spec via the `Send` API, then a synthesizer node reduces the per-spec coverage into the M1-M5 + R6 rubric.

### Hazard Risk Reviewer

The hazard pipeline reuses the test suite reviewer as an atomic subgraph: each requirement traced from a `HazardRecord` is reviewed in parallel by invoking the full RTM graph for that requirement. The hazard-level evaluators (H1, H2, H3, H7) run immediately in parallel with the requirement reviews, while H4 and H5 wait for requirement reviews to complete. H6 validates residual risk closure after H3, H4, and H5 complete. Finally, a deterministic aggregator assembles all seven findings into the H1-H7 rubric.

### Test Case Reviewer

A test case plus its traced requirements and a review-objectives checklist enter at `START`. The decomposer splits each requirement into atomic specs; a no-op `coverage_router` then fans out **three independent waves of Sends** — one per review axis (coverage / logical / prereqs) — to per-spec evaluators that run in parallel. The aggregator synthesizes the three accumulated `SpecAnalysis` lists into a single `TestCaseAssessment` with the review-objectives checklist populated.

### Test Suite Reviewer output fields

| Field                    | Description                                                                                                                                                                                                                                                                                                                             |
| ------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `decomposed_requirement` | Requirement broken into atomic, dimension-agnostic specs (`spec_id`, `description`, `acceptance_criteria`, `rationale`). Dimension classification happens later, per covering test case, rather than at decomposition time.                                                                                                             |
| `test_suite`             | Structured summary of each test case — objective, protocol, acceptance criteria                                                                                                                                                                                                                                                         |
| `coverage_analysis`      | Per-spec verdict: `covered_exists` (bool), `covered_by_test_cases` (list of `{test_case_id, dimensions[], rationale}` where each covering TC is labelled with the dimension(s) it exercises — any subset of `functional`, `negative`, `boundary` — and may cover multiple dimensions simultaneously), and a V&V `coverage_rationale`    |
| `synthesized_assessment` | SoP-gating rubric: `overall_verdict` (`Yes`/`No`), `mandatory_findings` (six items — M1-M5 plus the R6 advisory — with Yes/No/N-A verdicts, cited TC IDs, and uncovered spec IDs), short `comments` clarifying gaps, and a list of `clarification_questions` that the reviewer can answer to confirm whether identified gaps are real or N/A in context |

The `overall_verdict` aggregates deterministically: it is `Yes` only when every **M1-M5** mandatory finding is `Yes` or `N-A`; any single `No` flips it to `No`. **R6 (Design Alignment) is advisory and does not affect `overall_verdict`.** `N-A` is permitted only on M2 (Negative), M3 (Boundary), and R6 — M2/M3 when the requirement has no validation surface or no threshold/limit surface respectively, and R6 when no design documents exist.

### Hazard Risk Reviewer output fields

| Field                                                    | Description                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| -------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `requirement_reviews`                                    | One `RequirementReview` per requirement traced from the `HazardRecord`, each carrying the M1-M5 `synthesized_assessment` plus the RTM byproducts (`decomposed_requirement`, `test_suite`, `coverage_analysis`) — the full evidence chain that drove the hazard verdict                                                                                                                                                                                                                                                                                                                                                                                                               |
| `hazard_assessment.mandatory_findings`                   | Exactly seven items in order — H1 Hazard Record Completeness and Semantic Integrity, H2 Software Contribution and Cause Coverage, H3 Pre-Mitigation Risk and Exploitability Characterization, H4 Risk Control Identification, Allocation, and Coverage, H5 Verification Depth and Hazard-Path Effectiveness, H6 Residual Risk Closure and Acceptability Decision, H7 HSHA Update and Newly Identified Hazard / Hazardous Situation Capture — each with a `Yes` / `No` (or `N-A` on H5 only) verdict, cited `req_id`s and `test_id`s, and `unblocked_items` (sequence-of-events steps without controlling requirements on H4, controls without verifying tests on H5) |
| `hazard_assessment.overall_verdict`                      | `Yes` iff every finding is `Yes` or `N-A`; `No` otherwise — computed deterministically by the `final_assessor` node, never by the LLM                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| `hazard_assessment.comments` / `clarification_questions` | Same shape as the RTM reviewer — short prose plus closed-ended questions to drive reviewer follow-up                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |

### Test Case Reviewer output fields

| Field                                                                   | Description                                                                                                                                                                                                                   |
| ----------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `decomposed_requirements`                                               | Each traced requirement broken into atomic specs (same `DecomposedSpec` shape as the RTM reviewer)                                                                                                                            |
| `coverage_analysis` / `logical_structure_analysis` / `prereqs_analysis` | Three parallel `SpecAnalysis` lists — one per axis — each entry: `{spec_id, exists (bool), assessment}`                                                                                                                       |
| `aggregated_assessment.evaluated_checklist`                             | The `review_objectives` checklist (five items) populated with `verdict` (`Yes`/`No`), a `partial` flag (drives Yellow rendering when verdict is `Yes` but coverage is materially incomplete), and an `assessment` rationale per item |
| `aggregated_assessment.overall_verdict`                                 | `Yes` iff every **mandatory** objective is `Yes`; partial-Yes still counts as `Yes`, and the one advisory objective (`test_case_setup_clarity`) never affects the verdict                                                     |
| `aggregated_assessment.comments` / `clarification_questions`            | Same shape as the other reviewers                                                                                                                                                                                             |

The five review objectives default to `qaai/agents/test_case_reviewer/review_objectives.yaml`: `expected_result_support`, `expected_result_spec_align`, `test_case_achieves`, `test_case_logical_sequence` (all mandatory), and `test_case_setup_clarity` (advisory).

---

## Getting Started

### Prerequisites

- Python 3.12+
- [`uv`](https://docs.astral.sh/uv/) package manager
- An OpenAI-compatible API key (OpenAI / Ollama / vLLM / Bedrock via langchain-aws)
- (For RTM & Test Case baseline reviews) JAMA credentials

### Installation

```bash
git clone <repo-url>
cd qaai
uv sync --frozen   # installs deps, including pyjama pinned to the SHA in uv.lock
```

`pyjama` (the `pyjama-fastapi` package) is a git dependency. `uv sync` installs the
commit pinned in `uv.lock` — it does **not** auto-pull newer commits. To advance the
pin to the latest commit on `main` and reinstall:

```bash
uv sync --upgrade-package pyjama --native-tls   # re-pins uv.lock to latest pyjama-fastapi, reinstalls
# or use the helper: scripts/update_pyjama.sh  (PowerShell: scripts/update_pyjama.ps1)
```

> `--native-tls` uses the OS trust store; it's required behind corporate CAs (e.g. the
> Baxter network) where uv otherwise fails reaching pypi.org with an "invalid peer
> certificate" error. Drop it if you don't hit that error.

### Configuration

Create a `.env` file in the repo root with your LLM credentials (`API_KEY`, `API_BASE_URL`, `API_MODEL`) and, for the JAMA-sourced baseline reviews, your JAMA credentials. See the [Configuration Guide](docs/configuration.html) for the full environment-variable reference (rate limits, token costs, caching, prompt sets, CORS) and an example `.env`.

---

## Documentation

In-depth, self-contained HTML docs (generated from the codebase) live under `docs/` — open [`docs/index.html`](docs/index.html) as the entry point:

- [Configuration Guide](docs/configuration.html) — environment variables, enabling/disabling the cache, and creating & selecting prompt sets.
- [Caching design](docs/design/caching.html) — the shared review cache: disk layout, cache keys, per-run cache modes, prompt-set namespacing, and version-driven invalidation.
- [Test Guide](docs/test_guide.html) — running the unit, API, and integration suites; default fixtures and custom input files.
- [API Server & Frontend Guide](docs/api.html) — the async job model, every endpoint, request schemas, the single-page web frontend, and production notes.
- [Reviewer Agent design](docs/design/agents.html) — per-reviewer graph topologies, the shared node engine, output viewers, and design patterns.

---

## API Usage

### Starting the Server

```bash
uv run uvicorn qaai.api.main:app --reload
```

Interactive API documentation is available at `http://localhost:8000/docs`. At startup the lifespan handler builds a single shared `RTMReviewerRunnable` and reuses it inside the hazard pipeline's `RequirementReviewerNode`, so the RTM graph compiles and renders `graph.png` only once per process even though multiple endpoints exercise it. All three services share a single `ReviewCacheManager`, and a single in-memory `JobManager` (`qaai/api/jobs.py`) backs the asynchronous review jobs.

### Endpoint Reference

| Method | Path                          | Source                | Returns        | Description                                                                 |
| ------ | ----------------------------- | --------------------- | -------------- | --------------------------------------------------------------------------- |
| `GET`  | `/api/v1/health`              | —                     | JSON           | Health check for load balancers and monitoring                              |
| `POST` | `/api/v1/test-suite-review`   | JAMA baseline         | `202` + job_id | Submit the RTM coverage review (M1-M5 + R6) for every requirement in a baseline |
| `POST` | `/api/v1/test-case-review`    | JAMA baseline         | `202` + job_id | Submit the 5-objective test-case adequacy review for every test case in a baseline |
| `POST` | `/api/v1/hazard-risk-review`  | Uploaded SHA Excel    | `202` + job_id | Submit the H1-H7 hazard mitigation review for every row in an SHA table      |
| `GET`  | `/api/v1/jobs/{job_id}`       | —                     | JSON           | Poll a submitted job's status (`pending` / `running` / `completed` / `failed`) |
| `GET`  | `/api/v1/jobs/{job_id}/result`| —                     | HTML viewer    | Download a completed job's HTML report (`425 Too Early` while still running) |

#### Health Check

```bash
curl http://localhost:8000/api/v1/health
```

**Response (healthy):**

```json
{
  "status": "healthy",
  "version": "0.1.0",
  "services": {
    "rtm_service": "available",
    "hazard_service": "available",
    "test_case_service": "available"
  }
}
```

If any service is uninitialized the endpoint returns HTTP 503 with `"status": "degraded"`.

---

### Asynchronous job flow

The three review endpoints do **not** return the report on the `POST`. Each `POST` enqueues a background job and returns `202 Accepted` with a `job_id`; the report is fetched later:

```bash
# 1. Submit — returns 202 {"job_id": "...", "status": "pending"}
JOB=$(curl -s -X POST http://localhost:8000/api/v1/test-suite-review \
  -H "Content-Type: application/json" \
  -d '{"baseline_id": "BASE-84429", "use_cache": true}' | jq -r .job_id)

# 2. Poll — repeat until "status" is "completed" (or "failed")
curl -s http://localhost:8000/api/v1/jobs/$JOB
# {"job_id":"...","status":"running","filename":"qaai_rtm_review.html","error":null}

# 3. Download the HTML report once completed
curl -s http://localhost:8000/api/v1/jobs/$JOB/result --output qaai_rtm_review.html
```

- `GET /api/v1/jobs/{job_id}` returns `{job_id, status, filename, error}` where `status` ∈ `pending` / `running` / `completed` / `failed` (`error` is populated only on `failed`).
- `GET /api/v1/jobs/{job_id}/result` returns the HTML report (`200`) when the job is `completed`; `404` for an unknown id, `425 Too Early` while still pending/running, and the job's failure status (`400` for bad input such as an unknown baseline, `500` otherwise) when it failed.
- Jobs are held in an **in-memory** registry (most-recent 200) and run one at a time. This assumes a **single uvicorn worker** — see the Production Deployment section in `docs/api.md`.

The web frontend performs this submit → poll → download loop automatically.

---

### Test Suite Reviewer — `POST /api/v1/test-suite-review`

Submits an RTM review job for every requirement in a JAMA baseline; the completed job yields a downloadable `viewer.html`. Requires JAMA credentials in the server's `.env`. Follow the [Asynchronous job flow](#asynchronous-job-flow) to retrieve the report.

```bash
curl -X POST http://localhost:8000/api/v1/test-suite-review \
  -H "Content-Type: application/json" \
  -d '{"baseline_id": "BASE-84429", "use_cache": true}'
# → 202 {"job_id": "...", "status": "pending"}
```

| Body field   | Type   | Required | Default | Description                                                              |
| ------------ | ------ | -------- | ------- | ------------------------------------------------------------------------ |
| `baseline_id`| string | Yes      | —       | JAMA baseline ID, e.g. `BASE-84429`                                      |
| `use_cache`  | bool   | No       | `true`  | Reuse cached intermediate results (`partial`); set `false` to recompute (`off`) |
| `test_mode`  | bool   | No       | `null`  | Cache-only JAMA — fetch the baseline from the disk cache only, no live JAMA calls. `null` falls back to the server's `PYJAMA_TEST_MODE` |

Once the job completes, open the downloaded `viewer.html` in a browser to page through the M1-M5 + R6 rubric for every requirement.

---

### Test Case Reviewer — `POST /api/v1/test-case-review`

Same `BaselineRequest` body as the RTM endpoint (`baseline_id`, `use_cache`, `test_mode`); the completed job yields a `viewer_tc.html` with the 5-objective checklist for every test case in the baseline.

```bash
curl -X POST http://localhost:8000/api/v1/test-case-review \
  -H "Content-Type: application/json" \
  -d '{"baseline_id": "BASE-84429", "use_cache": true}'
# → 202 {"job_id": "...", "status": "pending"}  (poll + download per the async job flow)
```

---

### Hazard Risk Reviewer — `POST /api/v1/hazard-risk-review`

Accepts a **multipart upload** of an SHA Excel file and submits the H1-H7 review job for every hazard row. Runs with Excel-derived data only (no JAMA traceability). A sample SHA workbook lives at `tests/fixtures/external/software_hazard_analysis.xlsx`. Retrieve the report via the [Asynchronous job flow](#asynchronous-job-flow).

```bash
curl -X POST http://localhost:8000/api/v1/hazard-risk-review \
  -F "project_name=Infusion Pump" \
  -F "file=@tests/fixtures/external/software_hazard_analysis.xlsx" \
  -F "sheet_name=SHA Table" \
  -F "use_cache=true"
# → 202 {"job_id": "...", "status": "pending"}
```

| Form field     | Type   | Required | Default     | Description                                            |
| -------------- | ------ | -------- | ----------- | ------------------------------------------------------ |
| `project_name` | string | Yes      | —           | Project or product name                                |
| `file`         | file   | Yes      | —           | SHA Excel file (`.xlsx`/`.xls`) containing the hazard table |
| `sheet_name`   | string | No       | `SHA Table` | Worksheet holding the hazard table                     |
| `use_cache`    | bool   | No       | `true`      | Partial caching (`true`) vs recompute from scratch (`false`) |
| `test_mode`    | bool   | No       | `null`      | Cache-only JAMA (no live calls); `null` uses the server's `PYJAMA_TEST_MODE` |

H5 (Verification Depth and Hazard-Path Effectiveness) is the only finding that may be `N-A` — it applies when `software_related_causes` indicates no software cause. H1-H4, H6, and H7 always resolve to `Yes` or `No`.

> **Note:** The review endpoints are asynchronous — the `POST` returns `202` + a `job_id`, and the HTML report is downloaded from `GET /api/v1/jobs/{job_id}/result` once the job completes (see [Asynchronous job flow](#asynchronous-job-flow)). The underlying structured assessments (`SynthesizedAssessment`, `TestCaseAssessment`, `HazardAssessment`) are also serialized to `outputs.jsonl` in the run directory; see `docs/user_guide.md` for the full output data-model reference.
