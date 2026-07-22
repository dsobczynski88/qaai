# QAAI — AI-Powered DHF Reviewer for Medical Device Software

## Background

QAAI is a software quality tool designed to assist QA engineers and regulatory teams in reviewing Design History File artifacts for medical device software developed under FDA guidance and the IEC 62304 / ISO 14971 lifecycle standards. These reviews must demonstrate that every software requirement is adequately verified by a corresponding test case, that each test case is itself well-formed, and that hazards in the risk register are mitigated by traceable controls. In practice, they are labor-intensive processes prone to coverage gaps, inconsistent rationale, and missed edge cases.

QAAI exposes three complementary reviewers, each implemented as an independent LangGraph pipeline that emits a structured, SoP-gating rubric:

| Reviewer                                                          | What it scores                                                                       | Output rubric                                                                                                                                                                                                  |
| ---------------------------------------------------------------- | ------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Test Suite Reviewer (RTM)** — `POST /api/v1/test-suite-review` | One requirement against its associated test suite                                    | M1-M5 mandatory findings (Functional / Negative / Boundary / Spec Coverage / Terminology) + R6 advisory (Design Alignment) → binary Yes/No coverage verdict                                                    |
| **Test Case Reviewer** — `POST /api/v1/test-case-review`         | One test case against its requirements and a checklist of review objectives          | Five review objectives (4 mandatory + 1 advisory), each with a Yes/No verdict and a `partial` flag for material gaps → binary Yes/No overall verdict                                                            |
| **Hazard Risk Reviewer** — `POST /api/v1/hazard-risk-review`     | One hazard register entry against its traced requirements + test cases + design docs | H1-H6 mandatory findings (Hazard Record Completeness / Software Contribution / Pre-Mitigation Risk / Risk Control Adequacy / Verification Depth / Residual Risk Closure) + R7 recommended (HSHA Update, non-gating), each with a Yes/No verdict and a `partial` flag for material gaps → binary Yes/No verdict  |

All three reviewers cite the artifact IDs that support each finding, return short comments clarifying any gaps, and emit closed-ended clarification questions so reviewers can quickly confirm whether flagged gaps are real or N/A in context.

---

## Getting Started

A scannable lookup for setup and the everyday workflows. Each block is copy-pasteable; deep links point to the
full guide. See the [docs](docs/index.html) for detail.

### Prerequisites

- Python 3.12+
- [`uv`](https://docs.astral.sh/uv/) package manager
- An OpenAI-compatible API key (OpenAI / Ollama / vLLM / Bedrock via langchain-aws)
- (For RTM & Test Case baseline reviews) JAMA credentials

### Installation

```bash
git clone <repo-url>
cd qaai
uv sync --frozen   # installs deps, including pyjama (vendored locally at libs/pyjama)
```

`pyjama` (the `pyjama-fastapi` package) is **vendored into this repo** at `libs/pyjama`
as a git subtree and installed as an editable path dependency — its source lives in-tree,
so edits under `libs/pyjama` take effect immediately with no reinstall. To sync with the
standalone `pyjama-fastapi` repo:

```bash
scripts/pyjama_subtree.sh pull   # pull upstream changes into libs/pyjama  (PowerShell: scripts/pyjama_subtree.ps1 pull)
scripts/pyjama_subtree.sh push   # push local libs/pyjama edits back to pyjama-fastapi
```

> Behind corporate CAs (e.g. the Baxter network) where uv fails reaching pypi.org with an
> "invalid peer certificate" error, add `--native-tls` to `uv sync` (e.g.
> `uv sync --frozen --native-tls`); it uses the OS trust store. Drop it if you don't hit
> that error.

### Configuration

Create a `.env` file in the repo root with your LLM credentials (`API_KEY`, `API_BASE_URL`, `API_MODEL`) and, for the JAMA-sourced baseline reviews, your JAMA credentials. See the [Configuration Guide](docs/configuration.html) for the full environment-variable reference (rate limits, token costs, caching, prompt sets, CORS) and an example `.env`.

Any OpenAI-compatible endpoint works (OpenAI / Ollama / vLLM / Bedrock). Full env-var reference →
[Configuration Guide](docs/configuration.html).

### Start the server

```bash
uv run uvicorn qaai.api.main:app --reload   # dev: auto-reload;  app at http://localhost:8000/ , docs at /docs
uv run qaai-api                             # console script (no reload)
pwsh scripts/startup.ps1                     # Windows helper
bash scripts/startup.sh                      # JupyterHub-vs-local autodetect
```

The Vue SPA ships prebuilt in `qaai/web/dist`. To rebuild it: `cd qaai/web && npm run build`.

### Run a review — web UI

Start the server, open `http://localhost:8000/`, pick a reviewer, and submit. The page runs the
`202 → poll → download` job flow for you and renders the HTML report inline.

### Run a review — CLI (no browser)

The three endpoints are asynchronous: `POST` returns `202 + {job_id}`, then you poll and download
(full details in the [API Guide](docs/api.html)).

```bash
JOB=$(curl -s -X POST http://localhost:8000/api/v1/test-suite-review \
  -H "Content-Type: application/json" \
  -d '{"baseline_id": "BASE-84429", "use_cache": true}' | jq -r .job_id)
curl -s http://localhost:8000/api/v1/jobs/$JOB                       # poll until "completed"
curl -s http://localhost:8000/api/v1/jobs/$JOB/result -o report.html # download
```

The same submit→poll→download pattern applies to `test-case-review` (JSON body) and the multipart
`hazard-risk-review`.

### Run tests

```bash
uv run pytest -m "not integration"                        # unit + API — no LLM calls
uv run pytest -m integration -s                            # real LLM calls; needs .env
uv run pytest tests/unit -v                                 # unit suite only
uv run pytest tests/unit/test_cache.py::test_cache_write_read -v   # a single test, via the "::" notation
```

### View run results

```bash
# Each run writes logs/run-<ts>/ with qaai.log, graph png, inputs/outputs.jsonl, token_usage.jsonl,
# and a self-contained viewer.html / viewer_tc.html / viewer_hz.html — open it in a browser.
uv run python -m qaai.viewer logs/run-<ts>/outputs.jsonl --type rtm   # regenerate (rtm | tc | hz)
```

### Create a baseline WITHOUT JAMA

**The reviewer graphs never call JAMA — JAMA is only the data source.** Each graph consumes a plain
requirement + test-cases object, so a "baseline" is just a hand-authored inputs file and the whole
pipeline (decompose → evaluate per spec → synthesize → viewer) runs with zero JAMA connectivity.

**Recommended — drive the real graph via the eval harness (fully offline data path):**

```bash
# 1. Author a baseline: one JSON object per requirement, in graph-input shape. Copy the committed
#    pilot as a template:  eval/datasets/test_suite/actual/2026-07-17_12-01-00/actual_inputs.jsonl
#    Each line looks like:  {"requirement": {"req_id": "...", "text": "..."}, "test_cases": [ {...} ]}
DIR=$(uv run python -m qaai.dataset_studio new --type test_suite --quiet)
#    ...hand-write actual_inputs.jsonl in $DIR (one requirement per line).

# 2. Run the reviewer graph over it (needs LLM creds only — no JAMA):
uv run python scripts/evaluate_with_mlflow.py --spec eval/specs/test_suite_reviewer.yaml \
  --dataset-dir "$DIR" --mode run --limit 20

# 3. Render the same report the API produces, from the run's predictions:
uv run python -m qaai.viewer "$DIR"/predictions/<ts>/predicted_outputs.jsonl --type rtm
```

Analogous shapes exist for `--type test_case` and `--type hazard`; use their specs under
`eval/specs/`. To instead exercise the **API endpoint + `baseline_id`** offline, seed the pyjama disk
cache under `shared/source/baselines/<baseline_id>/` and POST with `test_mode=true` (cache-only; a
miss is a hard error). Those files are raw JAMA responses, so the practical route is to capture them
once from a real fetch and replay — see [Caching](docs/design/caching.html) and the
[API Guide](docs/api.html).

### Build a labeled dataset from old runs

Turn completed runs into one growing, human-labeled eval set — the answer key the harness scores.

```bash
uv run python -m qaai.dataset_studio ingest logs/run-<ts> --edit            # run -> reviewable set; patch labels in the browser
uv run python -m qaai.dataset_studio ingest logs/run-<ts2> \
  --append eval/datasets/test_suite/actual/<ts>                             # accumulate more runs into the SAME set
uv run python -m qaai.dataset_studio validate eval/datasets/test_suite/actual/<ts>   # must exit 0
```

Ingested labels start as **the model's own answers (UNREVIEWED)** — correct them in the editor. One
dataset row is emitted per *output* row; `--append` preserves the existing rows and their reviewed
labels and drops a timestamped `source.<ts>.json` provenance sidecar. Details →
[MLflow Evaluation](docs/mlflow.html).

### Edit an existing dataset

To open the browser editor for a dataset you already have (rather than one you're freshly ingesting),
point `edit` at its directory directly:

```bash
uv run python -m qaai.dataset_studio edit eval/datasets/test_suite/actual/<ts>   # browser editor + loopback save server
```

This serves the same editor used by `ingest --edit`, reading/writing `actual_inputs.jsonl` /
`actual_outputs.jsonl` / `actual_labels.jsonl` in place; saves are all-or-nothing and append an
`edits.log` line per row. Run `validate` afterwards to confirm the edited set still checks out.

### Author a prompt → score it with an MLflow experiment

```bash
# 1. New prompt version — copy a sibling version dir, bump `version`, set `parent_version`, edit both files:
#    qaai/prompts/<role>/<vX.Y.Z>/{template.jinja2, meta.yaml}
# 2. New prompt set pinning it — `name:` = filename stem; optional `parent_set` must name a surviving set:
#    qaai/prompts/sets/<new_set>.yaml
# 3a. Score one arm:
uv run python scripts/evaluate_with_mlflow.py --spec eval/specs/test_suite_reviewer.yaml \
  --dataset-dir eval/datasets/test_suite/actual/2026-07-17_12-01-00 \
  --mode run --prompt-set <new_set> --limit 20
# 3b. …or sweep it against the baseline (one MLflow run per model x prompt-set cell, ranked):
uv run python scripts/sweep.py --spec eval/specs/test_suite_reviewer.yaml \
  --dataset-dir eval/datasets/test_suite/actual/2026-07-17_12-01-00 \
  --models gpt-5-mini --prompt-sets <new_set>,test_suite_reviewer_v3 --experiment my-sweep --limit 20
uv run mlflow ui                                                            # browse runs + per-template SHA provenance
```

Prompt registry, sets, and versioning → [Configuration Guide](docs/configuration.html) and
[Prompt Design](docs/design/prompt_design.html).

---

## View test catalog

`plugins/qaai_testcatalog/` is a **pytest plugin** (registered via the `pytest11` entry point, so no `-p` is needed) that emits a searchable, self-contained HTML book of the collected suite — each test's type, component, summary, fixtures, and example input/output. Because it is built from the tests pytest actually collects, it cannot drift.

```bash
uv run pytest --collect-only --test-catalog   # no tests run, no LLM calls
#   -> logs/test-catalog/test_catalog.html    (open in a browser)
#   -> logs/test-catalog/test_catalog.json    (the underlying data)

uv run pytest -m unit --collect-only --test-catalog                  # scope it like any pytest run
uv run pytest --collect-only --test-catalog --test-catalog-out DIR   # change the output directory
python -m qaai_testcatalog logs/test-catalog/test_catalog.json       # re-render from saved JSON
```

Entries are auto-derived from docstrings, markers, fixtures, and parametrize params; the optional `@pytest.mark.catalog(summary=..., example_input=..., example_output=...)` marker overrides any field. See the [Test Guide](docs/test_guide.html) and [Test Catalog](docs/test_catalog.html) docs.

---

## Pipeline Architecture

Every reviewer is a LangGraph `StateGraph` that fans out via the `Send` API for maximum parallelism, then fans back in via `operator.add` reducers before a synthesizer node aggregates findings against the rubric. Each run also writes a Mermaid graph PNG (`graph.png`, `tc_graph.png`, or `hazard_graph.png`) into the run's log folder alongside `qaai.log`. The per-reviewer graph topologies and output data models are documented in the [design docs](docs/index.html) under `docs/design/`.

### Test Suite Reviewer (RTM coverage)

The RTM reviewer decomposes a requirement into atomic specs and summarizes its test cases in parallel, fans out one evaluation per spec via the `Send` API, then a synthesizer node reduces the per-spec coverage into the M1-M5 + R6 rubric.

### Hazard Risk Reviewer

The hazard pipeline reuses the test suite reviewer as an atomic subgraph: each requirement traced from a `HazardRecord` is reviewed in parallel by invoking the full RTM graph for that requirement. The hazard-level evaluators (H1, H2, H3, R7) run immediately in parallel with the requirement reviews, while H4 and H5 wait for requirement reviews to complete. H6 validates residual risk closure after H3, H4, and H5 complete. Finally, a deterministic aggregator assembles all seven findings into the H1-H6 (mandatory) + R7 (recommended) rubric. R7 is advisory only — an R7 = No never flips the overall verdict.

### Test Case Reviewer

A test case plus its traced requirements and a review-objectives checklist enter at `START`. The decomposer splits each requirement into atomic specs; a no-op `coverage_router` then fans out **three independent waves of Sends** — one per review axis (coverage / logical / prereqs) — to per-spec evaluators that run in parallel. The aggregator synthesizes the three accumulated `SpecAnalysis` lists into a single `TestCaseAssessment` with the review-objectives checklist populated.

---

## Documentation

In-depth, self-contained HTML docs (generated from the codebase) live under `docs/` — open [`docs/index.html`](docs/index.html) as the entry point.

**Guides**

- [Configuration Guide](docs/configuration.html) — environment variables, enabling/disabling the cache, and creating & selecting prompt sets.
- [API Server & Frontend Guide](docs/api.html) — the async job model, every endpoint, request schemas, the web frontend, and production notes.
- [Test Guide](docs/test_guide.html) — running the unit, API, and integration suites; default fixtures and custom input files.
- [MLflow Evaluation](docs/mlflow.html) — score the reviewers as classifiers: the spec-driven harness, dataset format, metrics, sample sizing, and the `qaai-mlflow-eval` plugin.
- [Test Catalog](docs/test_catalog.html) — the `--test-catalog` pytest plugin: a searchable HTML book of the collected suite.

**Design**

- [Reviewer Agents (overview)](docs/design/agents.html) — per-reviewer graph topologies, the shared node engine, output viewers, and design patterns.
- [Test Suite Reviewer (RTM)](docs/design/test_suite_reviewer.html) — node-by-node walkthrough, the M1–M5 + R6 rubric, and verdict logic.
- [Test Case Reviewer](docs/design/test_case_reviewer.html) — the three review axes and the 5-objective checklist.
- [Hazard Risk Reviewer](docs/design/hazard_risk_reviewer.html) — the staged H1–H6 + R7 graph and the embedded RTM subgraph.
- [Caching](docs/design/caching.html) — disk layout, cache keys, per-run cache modes, prompt-set namespacing, and version-driven invalidation.
- [Prompt Design](docs/design/prompt_design.html) — how the prompt suites encode ISO/IEC/IEEE 29148, 29119-3, and ISO 14971, with a full clause→prompt traceability table.
- [Frontend (Vue 3) & RBAC](docs/design/frontend_vue_rbac.html) — the SPA, the async job engine, the RBAC model, and the `/api/v1/me` identity seam.
