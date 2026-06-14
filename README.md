# AutoQA — AI-Powered DHF Reviewer for Medical Device Software

## Background

AutoQA is a software quality tool designed to assist QA engineers and regulatory teams in reviewing Design History File artifacts for medical device software developed under FDA guidance and the IEC 62304 / ISO 14971 lifecycle standards. These reviews must demonstrate that every software requirement is adequately verified by a corresponding test case, that each test case is itself well-formed, and that hazards in the risk register are mitigated by traceable controls. In practice, they are labor-intensive processes prone to coverage gaps, inconsistent rationale, and missed edge cases.

AutoQA exposes three complementary reviewers, each implemented as an independent LangGraph pipeline that emits a structured, SoP-gating rubric:

| Reviewer                                                          | What it scores                                                                       | Output rubric                                                                                                                                                                                                  |
| ---------------------------------------------------------------- | ------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Test Suite Reviewer (RTM)** — `POST /api/v1/test-suite-review` | One requirement against its associated test suite                                    | M1-M5 mandatory findings (Functional / Negative / Boundary / Spec Coverage / Terminology) + R6 advisory (Design Alignment) → binary Yes/No coverage verdict                                                    |
| **Test Case Reviewer** — `POST /api/v1/test-case-review`         | One test case against its requirements and a checklist of review objectives          | Five review objectives (4 mandatory + 1 advisory), each with a Yes/No verdict and a `partial` flag for material gaps → binary Yes/No overall verdict                                                            |
| **Hazard Risk Reviewer** — `POST /api/v1/hazard-risk-review`     | One hazard register entry against its traced requirements + test cases + design docs | H1-H7 mandatory findings (Hazard Record Completeness / Software Contribution / Pre-Mitigation Risk / Risk Control Adequacy / Verification Depth / Residual Risk Closure / HSHA Update) → binary Yes/No verdict  |

All three reviewers cite the artifact IDs that support each finding, return short comments clarifying any gaps, and emit closed-ended clarification questions so reviewers can quickly confirm whether flagged gaps are real or N/A in context.

### Data sourcing

The reviewers do **not** accept inline review payloads over HTTP. Each endpoint sources its records and runs a batch:

- **Test Suite Reviewer** and **Test Case Reviewer** fetch a **JAMA baseline** by `baseline_id` (via [PyJama](https://github.com/dsobczynski88/pyjama-fastapi.git), installed as the `pyjama` git dependency). They require JAMA credentials in the server's `.env`.
- **Hazard Risk Reviewer** parses an **uploaded SHA Excel file** (`.xlsx`) — no JAMA required.

A review can take several minutes, so the three review endpoints run **asynchronously**: a `POST` submits a background job and returns `202 Accepted` with a `job_id` immediately, the client polls `GET /api/v1/jobs/{job_id}` until the status is `completed`, then downloads the report from `GET /api/v1/jobs/{job_id}/result`. The result is a self-contained **HTML viewer** (`FileResponse`, `text/html`) rather than a JSON body. This keeps every HTTP request sub-second so an upstream proxy (JupyterHub, AWS ALB) never sees a multi-minute idle request and can't return a 504. See [Asynchronous job flow](#asynchronous-job-flow) for details. The underlying structured assessments are also serialized to `outputs.jsonl` in the run directory.

---

## Pipeline Architecture

Every reviewer is a LangGraph `StateGraph` that fans out via the `Send` API for maximum parallelism, then fans back in via `operator.add` reducers before a synthesizer node aggregates findings against the rubric. Each run also writes a Mermaid graph PNG (`graph.png`, `tc_graph.png`, or `hazard_graph.png`) into the run's log folder alongside `qaai.log`.

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

### Hazard Risk Reviewer

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

### Test Case Reviewer

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

The five review objectives default to `qaai/components/test_case_reviewer/review_objectives.yaml`: `expected_result_support`, `expected_result_spec_align`, `test_case_achieves`, `test_case_logical_sequence` (all mandatory), and `test_case_setup_clarity` (advisory).

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

### Environment Setup

Create a `.env` file in the repo root:

```env
# Required — LLM credentials
API_KEY=<your api key>
API_BASE_URL=<your api url>
API_MODEL=<your model name>

# JAMA / PyJama (required for /test-suite-review and /test-case-review)
JAMA_HOST_ADDRESS=<your jama host>
JAMA_CLIENT_ID=<your client id>
JAMA_CLIENT_SECRET=<your client secret>
PYJAMA_TEST_MODE=false   # true = fetch baselines from disk cache only, no live JAMA calls

# Caching (optional — defaults shown)
ENABLE_CACHE=true
CACHE_DIR=./cache
REDIS_URL=               # leave unset to skip the optional Redis tier

# Rate / cost / output limits (optional — defaults shown)
MAX_REQUESTS_PER_MINUTE=490
MAX_TOKENS_PER_MINUTE=200000
MAX_OUTPUT_TOKENS=16000
TOKEN_COST_INPUT_PER_M=1.00
TOKEN_COST_OUTPUT_PER_M=5.00

# Server / prompts (optional)
ENVIRONMENT=development  # set to 'production' to disable /docs and /redoc
ALLOWED_ORIGINS=*        # comma-separated list for CORS (e.g., https://app.example.com)
PROMPT_SET=              # named prompt-set manifest, e.g. test_case_reviewer_v2
```

**Environment Variable Reference** (see `qaai/core/config.py` for the authoritative list):

| Variable                  | Required | Default       | Description                                                          |
| ------------------------- | -------- | ------------- | -------------------------------------------------------------------- |
| `API_KEY`                 | Yes      | —             | API key for the LLM service                                         |
| `API_BASE_URL`            | No       | —             | Base URL for the API endpoint                                       |
| `API_MODEL`               | Yes      | —             | Model identifier (e.g., `gpt-4o`, `gpt-4o-mini`)                    |
| `JAMA_HOST_ADDRESS`       | No¹      | —             | JAMA instance hostname (for baseline fetching)                     |
| `JAMA_CLIENT_ID`          | No¹      | —             | JAMA OAuth client ID                                                |
| `JAMA_CLIENT_SECRET`      | No¹      | —             | JAMA OAuth client secret                                            |
| `PYJAMA_TEST_MODE`        | No       | `false`       | Cache-only JAMA: fetch baselines from the disk cache only, make no live JAMA calls (per-request `test_mode` overrides this) |
| `ENABLE_CACHE`            | No       | `true`        | Master switch for the shared reviewer cache                        |
| `CACHE_DIR`               | No       | `./cache`     | Disk cache root (one folder per entity id)                         |
| `REDIS_URL`               | No       | unset         | Optional Redis (Tier 2) connection string                          |
| `MAX_REQUESTS_PER_MINUTE` | No       | 490           | Rate limit for API requests (buffer under 500 RPM)                 |
| `MAX_TOKENS_PER_MINUTE`   | No       | 200000        | Token rate limit (adjust based on your account tier)               |
| `MAX_OUTPUT_TOKENS`       | No       | 16000         | Maximum output tokens per request                                  |
| `TOKEN_COST_INPUT_PER_M`  | No       | 1.00          | USD per million input tokens (telemetry cost estimate)             |
| `TOKEN_COST_OUTPUT_PER_M` | No       | 5.00          | USD per million output tokens (telemetry cost estimate)            |
| `ENVIRONMENT`             | No       | `development` | Set to `production` to disable interactive API docs                |
| `ALLOWED_ORIGINS`         | No       | `*`           | CORS allowed origins (comma-separated, use `*` for development only)|
| `PROMPT_SET`              | No       | unset         | Named prompt-set manifest to override the default `PromptConfig`   |

¹ Required only for the JAMA-sourced endpoints (`/test-suite-review`, `/test-case-review`). The hazard endpoint runs from an uploaded Excel file and needs no JAMA credentials.

---

## Caching

A shared, write-through **`ReviewCacheManager`** (`qaai/core/cache.py`) backs all three reviewers so re-running a review reuses prior per-node LLM results and only pays for what changed. It is a three-tier cache:

- **Tier 2 — Redis** (optional, 24h TTL): a hot in-memory tier, enabled only when `REDIS_URL` is set. Degrades gracefully — if Redis is unreachable, reviews still run off disk.
- **Tier 3 — Disk** (`{CACHE_DIR}/{entity_id}/{node}_{prompt_version}.json`): one folder per entity (`REQ-*`, `TEST-*`, `HAZ-*`) holding the regulatory-evidence JSON for each node. The Redis key is `review:{entity_id}:{node}:{prompt_version}`.

The cache is keyed in part by **prompt version**, so bumping a template's version (under `qaai/prompts/`) automatically invalidates the affected entries — no manual purge needed.

**Per-run cache mode** is threaded through graph state as `cache_mode ∈ {off, partial, full}`:

- `partial` (default) — caches every interim node but **always re-runs the graph's final node** (synthesizer / aggregator / final_assessor, flagged `is_final_output=True`) to produce a fresh top-level verdict.
- `full` — caches the final node too; used internally for the hazard reviewer's embedded RTM subgraph (cached as one `req_id`-keyed blob).
- `off` — disables caching for the run.

Each endpoint exposes a **`use_cache`** toggle (default `true`) that the API maps to `partial` (enabled) or `off` (disabled). Set `ENABLE_CACHE=false` to disable the cache globally regardless of the toggle. See `docs/cache-implementation.md` for the full design.

---

## Web Frontend

When the server is running, a single-page UI is served at the root (`http://localhost:8000/`) from `qaai/api/static/index.html`. It presents three reviewer cards — **Requirement Coverage** (RTM), **Test Case Adequacy** (TC), and **Software Hazard Analysis** (hazard) — each fading in an input form (baseline ID or Excel upload) plus a "Use cached results" checkbox that drives the `use_cache` flag. On submit the page kicks off a background job and **polls for status every few seconds** (showing elapsed time) until the report is ready, then offers it as a download. Interactive API docs are at `http://localhost:8000/docs`.

---

## Running Tests

This project uses **pytest** with `asyncio_mode = "auto"`. The only marker is `integration` (real LLM calls); everything else mocks the LLM and JAMA.

```bash
# Unit tests (no API key required — all LLM calls are mocked)
uv run pytest tests/unit -v

# API happy-path / contract tests (mocked services; no live LLM)
uv run pytest -m "not integration" tests/api/v1 -v

# Everything except integration
uv run pytest -m "not integration"

# Integration tests (require a live LLM in .env via PYTEST_* vars)
uv run pytest -m integration -s

# Per-reviewer integration pipelines
uv run pytest tests/integration/test_suite_reviewer/pipeline.py::test_test_suite_reviewer -m integration -s
uv run pytest tests/integration/test_case_reviewer/pipeline.py::test_test_case_reviewer -m integration -s
uv run pytest tests/integration/hazard_risk_reviewer/pipeline.py::test_hazard_risk_reviewer -m integration -s
```

Integration tests read `PYTEST_API_KEY` / `PYTEST_BASE_URL` / `PYTEST_MODEL` from `.env`; a safety check in `tests/conftest.py` rejects any base URL containing `prod`.

#### Selecting the input fixture

Each of the three per-reviewer integration tests accepts a `--input-file` CLI option to choose which fixture file it runs against, resolved across `tests/fixtures/` in the order `mock/ → gold/ → local/ → external/ → root`. Pass a bare filename (no path):

```bash
# RTM / Test Case reviewers: --input-file is the JSONL of input rows
uv run pytest tests/integration/test_suite_reviewer/pipeline.py::test_test_suite_reviewer \
  --input-file=locating_device.jsonl -m integration -s

# Hazard reviewer: --input-file is the SHA .xlsx workbook;
# --pyjama-file (optional) overrides the traceability JSONL
uv run pytest tests/integration/hazard_risk_reviewer/pipeline.py::test_hazard_risk_reviewer \
  --input-file=software_hazard_analysis.xlsx --pyjama-file=pyjama_response_unified.jsonl -m integration -s
```

When omitted, each test falls back to its standard fixture (`test_suite_review_all_fields.jsonl`, `test_case_review_all_fields.jsonl`, and `software_hazard_analysis.xlsx` + `pyjama_response_unified.jsonl` respectively). For the RTM and Test Case tests, every row in the selected JSONL becomes its own parametrized pytest item (id = `req_id` / `test_id`); use `--collect-only` to preview the items a given file expands to without spending any LLM calls.

Fixture inputs live under `tests/fixtures/external/` (`test_suite_review_all_fields.jsonl`, `test_case_review_all_fields.jsonl`, `software_hazard_analysis.xlsx`, `pyjama_response_unified.jsonl`, plus `*_min_fields.jsonl` variants) and `tests/fixtures/local/` (project-specific files such as `locating_device.jsonl`), with labelled gold datasets under `tests/fixtures/gold/` and per-node mocks under `tests/fixtures/mock/`. Append a line to the relevant JSONL file to add a scenario, or drop a new file into `local/` (or `external/`) and point `--input-file` at it.

The integration suite includes session-scoped `jsonl_recorders` / `jsonl_recorders_tc` / `jsonl_recorders_hz` fixtures that write `inputs.jsonl` and `outputs.jsonl` to the active `logs/run-.../` folder and, on teardown, invoke the matching `qaai.viewer` `write_viewer*` function whenever `outputs.jsonl` has records — no manual step required.

All run artifacts are written to a timestamped `logs/run-<datetime>/` directory:

| File                                              | Contents                                                                         |
| ------------------------------------------------- | -------------------------------------------------------------------------------- |
| `qaai.log`                                      | Structured application logs                                                      |
| `graph.png` / `tc_graph.png` / `hazard_graph.png` | Mermaid diagrams of the compiled LangGraph (one per reviewer)                    |
| `inputs.jsonl`                                    | Input records fed to the run                                                     |
| `outputs.jsonl`                                   | Serialized pipeline state for each record                                        |
| `token_usage.jsonl`                               | Per-call token / cost telemetry and cache hit/miss events                       |
| `viewer.html` / `viewer_tc.html` / `viewer_hz.html` | Single-file HTML reviewer UIs built from `outputs.jsonl`                        |

---

## HTML Reviewer Viewer

Each batch run emits an HTML viewer alongside `outputs.jsonl` (`viewer.html` for RTM, `viewer_tc.html` for test case, `viewer_hz.html` for hazard). Each is a single static file with inlined JSON and vanilla JavaScript — no server, CDN, or build step. Open it directly in a browser to page through the batch.

**Left panel (information):**

- `req_id` chip + full requirement text
- Clickable test-case list — opens a modal with the raw TC and its AI-parsed summary (objective, protocol, acceptance criteria)
- Coverage Assessment: overall Yes/No verdict badge (green/orange) plus a bulleted findings table with Yes/No/N-A chips, cited IDs, and uncovered spec IDs
- A "Decomposed specs & coverage analysis →" link opens a dialog showing every decomposed spec color-coded light green (covered) or light orange (uncovered), with per-spec covering TCs and dimension chips (`functional` / `negative` / `boundary`)
- Synthesizer `comments` and `clarification_questions` (rendered only when non-empty)

**Right panel (feedback capture):**

- 1–5 reviewer rating radios
- Free-text notes
- Prev / Save & Next navigation with a progress counter
- Ratings + notes persist to browser `localStorage` (keyed by `req_id`) and are exportable as a JSON blob via the header's **Export feedback JSON** button

### Regenerating a viewer manually

```bash
# module form (RTM viewer)
uv run python -m qaai.viewer logs/run-<ts>/outputs.jsonl
```

The viewer is also importable — the package exposes one writer per reviewer:

```python
from qaai.viewer import write_viewer, write_viewer_tc, write_viewer_hz

write_viewer("logs/run-2026-04-22-09-00-00/outputs.jsonl")       # RTM → viewer.html
write_viewer_tc("logs/run-2026-04-22-09-00-00/outputs.jsonl")    # test case → viewer_tc.html
write_viewer_hz("logs/run-2026-04-22-09-00-00/outputs.jsonl")    # hazard → viewer_hz.html
```

### Package layout

```
qaai/viewer/
├── __init__.py                 # public API: build_viewer(_tc/_hz), write_viewer(_tc/_hz), *_HTML_TEMPLATE
├── __main__.py                 # enables `python -m qaai.viewer`
├── generator.py                # build_/write_ functions + CLI main()
├── template.py                 # HTML_TEMPLATE (RTM)
├── template_test_case.py       # TC_HTML_TEMPLATE
├── template_hazard_review.py   # HZ_HTML_TEMPLATE
└── common/ test_suite_reviewer/ test_case_reviewer/ hazard_reviewer/   # shared + per-reviewer assets
```

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
