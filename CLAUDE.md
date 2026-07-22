# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

QAAI is an AI-assisted Design History File (DHF) reviewer for medical-device software (FDA / IEC 62304 / ISO 14971). It exposes three independent **LangGraph reviewer pipelines**, each emitting a structured, SoP-gating rubric with a binary Yes/No verdict:

| Reviewer | Package | Endpoint | Rubric |
|----------|---------|----------|--------|
| Test Suite Reviewer (RTM) | `qaai.agents.test_suite_reviewer` | `POST /api/v1/test-suite-review` | M1–M5 (Functional / Negative / Boundary / Spec Coverage / Terminology) |
| Hazard Coverage Reviewer | `qaai.agents.hazard_risk_reviewer` | `POST /api/v1/hazard-risk-review` | H1–H6 mandatory + R7 recommended (per ISO 14971 / IEC 62304) |
| Single Test Case Reviewer | `qaai.agents.test_case_reviewer` | `POST /api/v1/test-case-review` | 5 review objectives, 4 mandatory + `test_case_setup_clarity` advisory (3 axes: coverage, logical, prereqs) |

The hazard reviewer **embeds the full RTM reviewer as a subgraph** — each requirement traced from a `HazardRecord` is reviewed by invoking the RTM graph for that requirement.

All endpoints return a self-contained **HTML FileResponse** (not JSON). The `qaai.viewer` package renders it from `outputs.jsonl` at the end of each run.

## Common commands

This project uses **uv** (Python >=3.12). There is no lint/format/typecheck config (no ruff/black/mypy).

```bash
uv sync --frozen                                   # install deps (pyjama vendored locally at libs/pyjama, editable)
scripts/pyjama_subtree.ps1 pull                     # sync vendored pyjama w/ upstream pyjama-fastapi (push: send local edits back)

# Tests — pytest with asyncio_mode=auto. Markers: `integration` and `unit`.
uv run pytest -m "not integration"                 # unit/fast tests, no live LLM calls
uv run pytest -m integration -s                     # integration tests (real LLM calls; needs .env)
uv run pytest tests/path/to/test_file.py -v         # single file
uv run pytest tests/path/to/test_file.py::test_name # single test
uv run pytest tests/unit -v                         # unit suite only

# API server (FastAPI app object is qaai.api.main:app)
uv run uvicorn qaai.api.main:app --reload         # docs at http://localhost:8000/docs
bash scripts/startup.sh                             # detects JupyterHub vs local
```

```bash
# Evaluation — spec-driven MLflow harness (qaai/eval/). See "Evaluation" below.
uv run python scripts/evaluate_with_mlflow.py --spec eval/specs/test_suite_reviewer.yaml \
  --dataset-dir eval/datasets/test_suite/actual/2026-07-17_12-01-00 --mode run --limit 40 \
  --model gpt-5-mini                                              # --model overrides settings.model (run mode only)
uv run python scripts/sample_size.py ci --confidence 0.95 --margin 0.05 --p 0.5
uv run mlflow ui                                                  # file:./mlruns

# Hyperparameter sweep — one MLflow run per model × prompt-set cell, ranked at the end.
# All arms hit ONE endpoint (settings.url / API_BASE_URL); every model id must be served by it
# (no per-model provider routing). A preflight pings each model and aborts on any it can't reach.
uv run python scripts/sweep.py --spec eval/specs/test_suite_reviewer.yaml \
  --dataset-dir eval/datasets/test_suite/actual/2026-07-17_12-01-00 \
  --models gpt-5.4-mini,gpt-5-mini --prompt-sets test_suite_reviewer_v3,test_suite_reviewer_v4 \
  --experiment rtm-sweep --limit 20 --max-parallel-arms 4 [--dry-run] [--skip-unavailable-models]
uv run python -m qaai.eval.compare <dataset-dir>/predictions/<experiment>/<arm>/   # drill into one arm
```

```bash
# Dataset authoring — see "Dataset Studio" below. Types: test_suite | test_case | hazard.
DIR=$(uv run python -m qaai.dataset_studio new --type test_suite --quiet)  # timestamped folder
uv run python -m qaai.dataset_studio ingest logs/run-<ts> --edit        # a run -> reviewable dataset
uv run python -m qaai.dataset_studio sync-outputs "$DIR"   # derive actual_outputs from labels
uv run python -m qaai.dataset_studio validate "$DIR"       # check vs live models; must exit 0
uv run python -m qaai.dataset_studio edit "$DIR"           # browser editor + loopback save server
```

Integration tests require a `.env` in the repo root. Required: `API_KEY`, `API_BASE_URL`, `API_MODEL`. Tests also read `PYTEST_API_KEY` / `PYTEST_BASE_URL` / `PYTEST_MODEL` (a safety check in `tests/conftest.py` rejects a base URL containing "prod"). See `qaai/core/config.py` for the full settings list (rate limits, token-cost rates, cache config).

### Test catalog

`plugins/qaai_testcatalog/` is a pytest plugin (registered via the `pytest11` entry point in `pyproject.toml` as `qaai_testcatalog.plugin`; it lives under `plugins/` but still ships inside the qaai wheel) that emits a searchable, self-contained HTML lookup of the collected tests. Run `uv run pytest --collect-only --test-catalog` to write `logs/test-catalog/test_catalog.{json,html}` **without running any tests** (no LLM calls); scope it with the usual selectors (`-m unit`, a path) and it tracks that selection. Each row's summary/type/component/fixtures/example-input are auto-derived from docstrings, markers, `item.fixturenames`, and parametrize `callspec`; the optional `@pytest.mark.catalog(summary=..., example_input=..., example_output=...)` marker curates any field (marker wins). Re-render from saved JSON with `python -m qaai_testcatalog <json>`. The HTML reuses the `qaai/viewer` theme (placeholder `str.replace()` render + inlined assets, no Jinja2). See `plugins/qaai_testcatalog/README.md`.

## Architecture: the reviewer pattern

Every reviewer follows a **canonical four-file layout** under `qaai/agents/<reviewer>/`:

- **`core.py`** — Pydantic output models (one per node) **and** the `TypedDict` graph state. Fan-in accumulators use `Annotated[List[X], operator.add]` so parallel `Send` results concatenate automatically (e.g. `coverage_analysis: Annotated[List[EvaluatedSpec], operator.add]`).
- **`nodes.py`** — node classes (subclasses of `StandardLLMNode` / `BatchedLLMNode` from `qaai/agents/shared/nodes.py`), paired `make_*_node(...)` factory functions that render the Jinja2 prompt and inject config, and `dispatch_*(state) -> List[Send]` fan-out functions. **Dispatchers return `[]` on missing/invalid state** (safe no-op).
- **`pipeline.py`** — a `*Runnable` class whose `build()` constructs the `StateGraph`, wires edges (`add_edge` / `add_conditional_edges`), compiles with an optional checkpointer, and writes `graph.png` into the run directory. The graph is built in `__init__`.
- **`__init__.py`** — exports the Runnable. (`hazard_risk_reviewer` also has `loader.py` for parsing `HazardRecord`s from Excel.)

### Shared base node classes (`qaai/agents/shared/nodes.py`)

**`StandardLLMNode`** (Template Method): `__call__` runs `_validate_state` → cache-check → `_build_payload` → LLM call → `_parse_llm_response` (JSON extraction + Pydantic validation) → cache-write → `_format_response`. Subclasses implement `_validate_state`, `_build_payload`, `_format_response`, and optionally `_get_cache_entity_id` to opt into caching. **`_validate_state` returning `False` is the standard reason a node silently skips** (it returns `_get_skip_response()`, usually `{}`).

**`BatchedLLMNode`** (multi-batch variant): fans multiple items in parallel via `asyncio.gather`. Hooks to implement: `_get_items()`, `_build_batch_payload()`, `_unwrap_batch_result()`, `_build_result()`. Supports the same optional cache interface as `StandardLLMNode`.

**`DecomposerNode`** (shared/nodes.py): reused by all three reviewers — decomposes a requirement into specs with acceptance criteria.

**`DataIntegrationNode`** (shared/data_integration.py): conditional JAMA fetch vs. local data pass-through. Checks for `pyjama_request` in state — if absent, returns `{}` (no-op; data is already in state, as in tests). If present, calls PyJamaDataSourceNode and returns `{jama_data, jama_metadata}`. PyJama import degrades gracefully if not installed.

**Shared Pydantic models** (`qaai/agents/shared/core.py`): `Requirement`, `TestCase`, `DecomposedSpec`, `DecomposedRequirement`, `DesignDocument` — used as input types across all three reviewers.

The LLM client is `RateLimitOpenAIClient` (`qaai/agents/clients.py`) — an `AsyncOpenAI` wrapper with proactive RPM/TPM limiting, reactive backoff retries, and telemetry hooks. Any OpenAI-compatible endpoint works (OpenAI / Ollama / vLLM / Bedrock via langchain-aws).

### Graph topologies

**Test Suite Reviewer (RTM):**
```
START → data_integration → transform → validation_gate  (skip → END if inputs missing)
  → [decomposer | summarizer | design_summarizer]  (parallel)
  → coverage_router (join)
  → dispatch_coverage → Send × N → spec_evaluator  (parallel per spec)
  → synthesizer → END
```
State: `RTMReviewState`. Fan-in field: `coverage_analysis: Annotated[List[EvaluatedSpec], operator.add]`. Output: `SynthesizedAssessment` with 6 `MandatoryFinding` items (M1–M5 + R6 advisory).

**Hazard Risk Reviewer (staged by data dependency):**
```
START → data_integration → transform → validation_gate  (skip → END if required SHA fields missing)
  → work_router
     → dispatch_hazard_evaluators_early → [h1, r7]                     (need only hazard fields)
     → dispatch_requirement_reviews → Send × N → requirement_reviewer  (each calls RTM subgraph)
     → design_summarizer → dispatch_hazard_evaluators_design → [h2, h3]  (consume summarized_designs)
     → needs_summarizer
  → late_evaluator_router (joins requirement_reviewer + design_summarizer + needs_summarizer)
     → dispatch_hazard_evaluators_late → [h4, h5]
  → h6                         (joins h4/h5; h3 finding already reduced)
  → final_assessment (deterministic) → END
```
State: `HazardReviewState`. Fan-in field: `hazard_findings: Annotated[List[HazardFinding], operator.add]`. Output: `HazardAssessment` with 7 `HazardFinding` items (H1–H6 mandatory + R7 recommended). `final_assessor` computes `overall_verdict` deterministically: Yes iff every **mandatory** finding (H1–H6) is Yes/N-A. **R7 is recommended only and is excluded from the verdict** — an R7 = No never flips it (mirrors the RTM reviewer's R6 advisory criterion). Each `HazardFinding` also carries a `partial: bool` flag (mirroring the test_case_reviewer's `EvaluatedReviewObjective.partial`): a partial-Yes (`verdict="Yes"`, `partial=True`) marks a met-but-materially-incomplete criterion, renders **Yellow** in the viewer, still passes `overall_verdict`, and is intentionally **unscored** by the eval harness. An LLM `"Partial"` verdict string is coerced to `verdict="Yes"` + `partial=True` by a `mode="before"` validator via `coerce_partial_verdict`.

**Test Case Reviewer (3-axis):**
```
START → data_integration → transform → validation_gate (skip → END if inputs missing)
  → coverage_router (join)
  → dispatch_coverage → Send × N → coverage_evaluator  (parallel per spec)
  → logical_evaluator                                   (direct edge, test-level)
  → prereqs_evaluator                                   (direct edge, test-level)
  → aggregator → END
```
State: `TCReviewState`. Three fan-in fields with `operator.add`: `coverage_analysis`, `logical_analysis`, `prereqs_analysis`. Output: `TestCaseAssessment` with 5 `EvaluatedReviewObjective` items. The objective list is **embedded in the aggregator prompt** (`single_test_aggregator` v8+); there is no `review_objectives.yaml` and no `review_objectives` graph input. The fifth, `test_case_setup_clarity`, is **advisory** — `mandatory: false`, excluded from `overall_verdict`, mirroring RTM's R6 and hazard's R7; `eval/specs/test_case_reviewer.yaml` lists it in `advisory_codes` to match.

## API layer (`qaai/api/`)

- **`main.py`** — `create_app()` factory with CORS, GZip, request logging middleware; `lifespan()` context manager initializes `RTMReviewService`, `HazardReviewService`, `TestCaseReviewService` at startup. Mounts the built Vue SPA (`qaai/web/dist`) at `/` via `NoCacheStaticFiles`, falling back to the legacy `qaai/api/static/` UI when `dist/` is absent.
- **`services.py`** — one service class per reviewer. Each service: fetches JAMA or Excel data, batches records, invokes the graph, writes `inputs.jsonl` / `outputs.jsonl`, and calls `qaai.viewer` to generate the HTML report.
- **`routes.py`** — the three review endpoints run **asynchronously**: each returns `202 + {job_id}`, then the client polls and downloads via the jobs endpoints. Endpoints: `GET /api/v1/health`; `POST /api/v1/test-suite-review` (baseline_id; `include_edge_case_analysis` selects the prompt set); `POST /api/v1/test-case-review` (baseline_id; `include_decomposition_analysis` toggles decomposition); `POST /api/v1/hazard-risk-review` (multipart: file + project_name + sheet_name + identifier_pattern + `include_edge_case_analysis`); `GET /api/v1/jobs/{job_id}` (status); `POST /api/v1/jobs/{job_id}/cancel` (Stop Run); `GET /api/v1/jobs/{job_id}/result` (download the HTML report — 425 while still running); `POST /api/v1/feedback-upload` (store exported reviewer feedback JSON under `./shared/feedback/`); `GET /api/v1/me` (caller identity + roles for the SPA's RBAC layer — resolved from the ALB/OIDC header or a DEV fallback via `qaai/api/identity.py`; identity-read only, does **not** gate the review endpoints). All accept the `cache_mode` radio (`off`/`partial`/`full`) with the legacy `use_cache` bool as fallback.
- **`middleware.py`** — UUID request logging with timing; rejects POST bodies >10MB.
- **`jobs.py`** — `JobManager` runs review jobs as background asyncio tasks, tracks status (pending/running/completed/failed), supports cancellation, and stores each job's result HTML path.
- **`schemas.py`** — `BaselineRequest(baseline_id: str, cache_mode, use_cache, test_mode, include_edge_case_analysis, include_decomposition_analysis)` for the JAMA-sourced endpoints.
- **`identity.py`** — `resolve_identity(request)` for `GET /api/v1/me`: decodes the ALB `x-amzn-oidc-data` OIDC header (payload only — signature verification is the RBAC follow-up) and maps SSO groups → roles, or returns a DEV-only fallback identity. `VALID_ROLES` + the group→role map live in `qaai/core/config.py`.

## Frontend (Vue SPA — `qaai/web/`)

The interactive UI is a **Vue 3 + Vite** SPA (Vue Router hash mode + Pinia), built to `qaai/web/dist/` and served by FastAPI (see `main.py` above). It replaced the vanilla `qaai/api/static/` page (kept as a build-less fallback). No JS toolchain runs in CI yet — build with `npm run build` in `qaai/web` (see `qaai/web/README.md`). Key points:

- **Structure:** `stores/auth.ts` (identity + roles), `stores/job.ts` (the async 202→poll→download engine, ported from the old `runJob` with an `AbortController`), `api/client.ts` (the `detectRootPath()` proxy base-path logic ported verbatim), `router/` (hash mode + role guard), `components/` + `views/`. The backend endpoint contract and request field names are preserved exactly.
- **RBAC (scaffolding):** roles **admin / reviewer / viewer**; `ROLE_PERMISSIONS` in `src/constants.ts` mirrors `VALID_ROLES` in `qaai/core/config.py`. Route guards + role-gated components (`SubmitButton`, `FeedbackUpload`) are **UX gating only** — real per-route enforcement (and OIDC signature verification) is the documented follow-up phase. `QAAI_DEV_ROLES` flips the local dev role to exercise gating.
- **Proxy safety:** `vite.config.ts` uses `base: './'` (relative asset URLs) so the SPA works under any JupyterHub/ALB proxy prefix; runtime `/api` calls are prefixed via `detectRootPath()`.

## Cross-cutting subsystems

- **Prompts** (`qaai/prompts/`) — a versioned registry, not flat files. Templates live at `<role>/<version>/template.jinja2` with a `meta.yaml` sidecar (content SHA256, status, target models). `PromptConfig` (`qaai/core/config.py`) maps 19 node roles to template paths; `PromptConfig.from_set("<name>")` resolves a named bundle manifest from `qaai/prompts/sets/`. The registry (`_registry.py`) validates content SHA on load and exposes `list_sets(status)` for discovery. Bumping a prompt version is the cache-invalidation mechanism (version is part of the cache key). Render via `render_prompt(path, **vars)` in `qaai/utils.py`.
- **Caching** (`qaai/core/cache.py`) — `ReviewCacheManager` is a 3-tier write-through cache shared by **all three reviewers**: Tier 2 = Redis (optional, degrades gracefully, 24h TTL, holds the latest write under a timestamp-free key), Tier 3 = **immutable, timestamped** disk JSON at `{cache_dir}/{entity_id}/[{prompt_set}/]{node}_{prompt_version}_{timestamp}.json` (regulatory evidence). Files are **append-only** (every write creates a new timestamped file; reads select the **newest** — mirroring the pyjama JAMA source cache convention) so a full history is preserved. One folder per entity under `./shared/runs/` (the `CACHE_DIR` default; a sibling of the pyjama JAMA source cache at `./shared/source`) — `REQ-*` (test suite), `TEST-*` (test case), `HAZ-*` (hazard), and `DD-*` (design-doc summaries). Redis key = `review:{entity_id}:{node}:{prompt_version}`. **Per-item (doc-keyed) design summaries**: the RTM `design_summarizer` and hazard `design_summarizer` opt into `BatchedLLMNode.PER_ITEM_CACHE`, caching each design-doc summary under its own `doc_id` (a `DD-*` entity) rather than the requirement/hazard — so a summary is computed once and reused across every entity that cites the doc (the summarizer payload deliberately omits the requirement/hazard to keep the entry doc-intrinsic; `_item_cache_prompt_set()` returns None so it is shared across prompt sets). The hazard reviewer's embedded RTM subgraph shares the same `cache_manager`, so its `design_summarizer` participates in this shared `DD-*` cache too (the per-requirement whole-RTM-result blob keyed by `req_id` still exists alongside it). Controlled by `ENABLE_CACHE` / `CACHE_DIR` / `REDIS_URL`. **Per-run mode** is threaded through graph state as `cache_mode` ∈ `{off, on, test}` (default `on`): `off` never reads but always re-runs every node and writes a new timestamped result (reuses nothing, keeps history); `on` (UI default) reuses the newest cached interim results and always re-runs the graph's final node (synthesizer / aggregator / final_assessment) for a fresh result, writing through; `test` reads the newest cached result for **all** nodes (incl. final) and makes **no LLM calls** — a cache miss raises `CacheRequiredError` (`qaai/agents/shared/nodes.py`), surfaced as the 400 "Test mode requires all node results to pre-exist in cache"; `test` also forces the pyjama JAMA fetch cache-only so report regeneration is fully offline. Final-output nodes are flagged `is_final_output=True`; gating lives in `BaseLLMNode._cache_read_allowed/_cache_write_allowed` with a chokepoint that raises before any `chat_completion` in test mode. The API takes an explicit `cache_mode` from the UI's per-endpoint **cache-mode radio** (`on` "reuse cached, fresh final", default / `test` "recreate from cache, no LLM" / `off` "re-run all, save timestamped"), resolved by `_resolve_cache_mode()` in `qaai/api/routes.py`; the legacy `use_cache` bool (`true`→`on`, `false`→`off`) and legacy radio values (`partial`→`on`, `full`→`test`) are still accepted. **Success-gated keeping**: because writes are per-node/write-through, `_run_batch_review` (`qaai/api/services.py`) gates *reuse* after the fact — an item whose graph run raises, or whose final state fails the reviewer's `is_complete_fn` (final assessment present + full rubric: 6 / 5 / 7 cells), has **only this run's** files purged via `ReviewCacheManager.purge_run(entity_id, since, prompt_set)` (run-scoped by timestamp, so earlier good runs survive) so a failed/incomplete run is never reused; a hard error no longer aborts the batch (the item is skipped, the job only fails if nothing produces output). **Concurrency**: `_run_batch_review` fans the per-item graph invocations out with `asyncio.gather` under an `asyncio.Semaphore` sized per-reviewer (`TEST_SUITE_MAX_CONCURRENT_REVIEWS`/`TEST_CASE_MAX_CONCURRENT_REVIEWS`, default 8; `HAZARD_MAX_CONCURRENT_REVIEWS`, default **1**) rather than awaiting them one at a time — the hazard reviewer runs records **sequentially** so the first record warms the shared `DD-*`/`REQ-*` cache before the next (write-after-completion cache, no in-flight dedup), while each record still fans its summarizers + requirement reviews out concurrently, and the embedded RTM subgraph fan-out (`RequirementReviewerNode`) is bounded by `TEST_SUITE_MAX_CONCURRENT_REVIEWS` (cache hits bypass that semaphore); outputs are written in **input order** after the gather, a per-item failure never cancels its siblings (`return_exceptions=True`), and a test-mode cache miss still hard-fails the whole job. The soft semaphore sits over the client's hard RPM/TPM limiter (defaults 10000 RPM / 10M TPM). Note: `Send` dispatchers must copy `cache_mode` into each fan-out payload. **Prompt-set namespacing**: when a run uses a named prompt set (see the edge-case toggle below), the set name is folded into the key (`review:{entity_id}:{prompt_set}:{node}:{prompt_version}`) and the disk path gains a per-set subfolder (`{cache_dir}/{entity_id}/{prompt_set}/{node}_{prompt_version}_{timestamp}.json`). It is threaded `PromptConfig.set_name` → node (`prompt_set`) → `ReviewCacheManager.get/set` and is optional/defaulted, so default-config runs (e.g. the test-case reviewer) keep the un-namespaced layout.
- **Edge-case prompt-set toggle** — the API input `include_edge_case_analysis` (`BaselineRequest` field for test-suite; multipart form field for hazard) selects the prompt set per request: ON → `test_suite_reviewer_v4` (edge-case decomposer v6), OFF → `test_suite_reviewer_v3` (baseline decomposer v5). It applies to the **test-suite reviewer and the hazard reviewer's embedded RTM**; the test-case reviewer is unaffected. Routes resolve it via `resolve_prompt_set()` (`qaai/api/services.py`); `RTMReviewService` / `HazardReviewService` pre-build one compiled graph per set at startup (`self.graphs[prompt_set]`) and select per request. v3 and v4 share every node version except the decomposer, which is exactly why the prompt-set name must be in the cache key.
- **Telemetry** (`qaai/core/telemetry.py`) — `TokenUsageTracker` appends per-call token/cost records (and cache hit/miss events) to `token_usage.jsonl`; injected into the client and the cache manager. Writes a summary record at teardown.
- **Run artifacts** — each run writes to `logs/run-<timestamp>/` (created by `make_output_directory` in `qaai/utils.py`): `qaai.log`, `graph.png` / `tc_graph.png`, `inputs.jsonl`, `outputs.jsonl`, `token_usage.jsonl`, `viewer.html` / `viewer_tc.html` / `viewer_hz.html`. The `tests/conftest.py` `jsonl_recorders` fixtures generate HTML viewers from `outputs.jsonl` at session teardown.
- **Evaluation fixtures** — labelled gold datasets live under `tests/fixtures/gold/` (`gold_dataset*.jsonl`); per-node mocks under `tests/fixtures/mock/`. The pipeline is evaluated as a binary classifier on `overall_verdict`.
- **Evaluation** (`qaai/eval/`) — a committed, **spec-driven** MLflow harness, wrapped by the repo-local `plugins/qaai-mlflow-eval` plugin (five skills). An `EvalSpec` (`eval/specs/<name>.yaml`) declares where the verdict/rubric live and how to read labels, so swapping reviewers or projects needs **no code change**; specs ship for all three reviewers and are the source of truth for each rubric (RTM = M1–M5 + R6 advisory). Datasets are row-aligned three-file sets under `eval/datasets/<type>/actual/<ts>/`: `actual_inputs.jsonl`, plus `actual_outputs.jsonl` / `actual_labels.jsonl` — the **answer key** in graph-output and flat shape (the "actual" values; `synthesize_outputs()` renders one from labels, so scoring it directly returns 1.000 and self-tags `oracle_selftest`). There is one committed dataset — the grounded 20-row RTM pilot at `eval/datasets/test_suite/actual/2026-07-17_12-01-00/` (answer-key `actual_*` files + `source_gold.jsonl` + `edits.log`; `description.md` there is the reference for the layout and the revision model). **Predictions only come from `--mode run`**, which invokes the graph and writes a timestamped set to `<dataset-dir>/predictions/<ts>/` (`predicted_inputs.jsonl` + `predicted_outputs.jsonl` + its flat projection `predicted_labels.jsonl` = the *predicted* values, + `run_metadata.json` carrying the `mlflow_run_id`); `outputs_to_labels()` is the exact inverse of `synthesize_outputs()` and flattens both sides, which is what makes them comparable. Metrics: overall accuracy/precision/recall/F1 + macro-F1, balanced accuracy, Cohen's kappa, prevalence; per-rubric accuracy/macro-F1/balanced accuracy/kappa/per-class support; `exact_match_rate` (mandatory cells only), `helper_invariant_pass_rate`, `skip_rate`, latency, cost. Sizing lives in `qaai/eval/sample_size.py` (Wald + Wilson + FPC, stdlib only): 95%/±0.05 needs **385** at p=0.5, **196** at p=0.85 — size off `n_scored`, not `n_records`. **`--model`** (run mode only) overrides `settings.model` on the returned client string (`runners.build_client(model_override=...)`); `base_url`/`api_key` stay from settings (one endpoint, many models), so it just changes the logged `params.model`. **Hyperparameter sweeps** — `scripts/sweep.py` fans out one `evaluate_with_mlflow.py --mode run` process per `--models` × `--prompt-sets` grid cell (arm slug `<model>__<prompt_set>`), then ranks arms via `mlflow.search_runs`. **All arms hit the one settings-derived endpoint** (`settings.url` / `API_BASE_URL`, defaulting to OpenAI when unset) — there is **no per-model provider routing**, so every model id must be served by that endpoint (an Anthropic id on an OpenAI endpoint 404s). A **preflight** (`preflight_models`) pings each unique model once and **aborts before launching** if any is unserved (or drops them with `--skip-unavailable-models`) — this is what stops a bad id from silently producing an all-`null` arm. It structurally handles the three concurrent-run hazards: a per-arm `--predictions-dir <dataset-dir>/predictions/<sweep_ts>/<arm>` under **one fresh timestamped folder per sweep run** (dodging `new_predictions_dir`'s 1-second-resolution `exist_ok=True` collision), a child-env `MAX_REQUESTS_PER_MINUTE` divided by `--max-parallel-arms` (so N arms don't N× the endpoint ceiling), and a serial parent-side `set_experiment` before any child launches (so `file:./mlruns` never races); `API_MODEL` is also set per child so telemetry cost lookups match the arm. The ranking flags any arm whose every record errored/skipped as **FAILED** (via the `all_records_failed` tag / `error_rate` metric the harness now logs). `--dry-run` prints the plan without launching. **Per-record failure visibility**: `run_and_collect` logs each record's exception at WARNING, the harness logs `error_rate` + the `all_records_failed` tag, and the CLI prints `errors=N/M` plus a loud all-failed warning — and `evaluate_with_mlflow.py` calls `bootstrap_console_logging()` so a run's node logs are actually visible (previously the eval process attached no handlers, so failures were silent). ⚠ Two science caveats (documented in the script header): at n=20 the CI on `overall_f1` (~±0.20) is wider than any plausible arm gap, so treat sweeps as **plumbing/smoke, not selection**, until the dataset grows; and sweeping against an answer key whose labels don't match content optimizes for agreement with bad labels — fix label quality first.
- **Dataset Studio** (`qaai/dataset_studio/`) — the authoring side of the same datasets, wrapped by the repo-local `plugins/qaai-dataset-gen` plugin (three `generate-*-dataset` skills + `dataset-ingest` + `dataset-review`). CLI: `python -m qaai.dataset_studio <new|ingest|sync-outputs|validate|edit>`. Two ways in, both landing in `eval/datasets/<type>/actual/<YYYY-MM-DD_HH-MM-SS>/` — one folder per answer-key revision, never edited in place, with `predictions/<ts>/` hanging off the revision it scored (`registry.infer_dataset_type` walks ancestors and `server._datasets_root` finds the type-named ancestor rather than counting levels, so older flat/`<type>/<ts>` layouts still resolve). `new` scaffolds a five-file skeleton (**never** reuses an existing directory, unlike `new_predictions_dir`'s `exist_ok=True`); **`ingest`** (`ingest.py`) converts a completed run — `logs/run-<ts>/{inputs,outputs}.jsonl`, a `predictions/<ts>/predicted_*` set, or another dataset — into the three-file answer key, detecting the reviewer type from the assessment key present in the output state and deriving labels via the scorer's own `outputs_to_labels()` (so `V050` passes by construction), writing `source.json` provenance + an `ingest` log line. ⚠ Its rows are **the model's own answers, not ground truth** (`description.md` is stamped UNREVIEWED), and it emits **one row per *output* row**, projecting each input back out of the output state via `spec.input` — because `_run_batch_review` appends an output only for items that didn't raise, so `outputs.jsonl` can be shorter than `inputs.jsonl` and zipping them positionally would pair one item's requirement with another's verdict; unmatched inputs are reported, never dropped. `sync-outputs` derives `actual_outputs.jsonl` from `actual_labels.jsonl` via `synthesize_outputs()` (hand-writing it is the main source of answer-key self-disagreement); `validate` runs ~16 checks (`--list-checks`) with exit codes 0/1/2/3/4; `edit` serves a browser editor on loopback. **Nothing type-specific is restated here**: `registry.py` builds per-row Pydantic models by projecting the reviewer state TypedDicts' own annotations onto the keys `spec.input` names, and every rubric fact (codes, advisory, N-A) is read from `eval/specs/*.yaml`. Two legitimate `actual_outputs` shapes exist and `registry.output_row_shape()` distinguishes them by whether the row supplies fields the model *requires*: **minimal** (the oracle projection — what `synthesize_outputs` emits and what the committed pilot uses; skips full-model validation because it deliberately omits required fields) and **full** (a real run's state). Verdict derivation lives in `rules.py`, driven by `spec.mandatory_codes` so the advisory exclusion can't drift from the scorer; agreement with the live `SynthesizedAssessment._derive_overall_verdict` is pinned by a parametrized test rather than by parsing through the model. The editor (`qaai/viewer/dataset_editor/`, layout `common/layout_editor.html`, runtime `common/editor.js`) renders the **input** pane from `input_row_model(...).model_json_schema(by_alias=False)` — `by_alias=False` is load-bearing, since `HazardRowFromExcel` aliases every field to its Excel column header — and the **output** pane from a `CONFIG` blob derived from the spec — verdict cells, plus (for full-shape rows, detected by key presence) `partial`, rationale, any array-of-string field such as `cited_test_case_ids`, and the assessment-level fields. Saving also emits one untruncated `feedback` line per row carrying a `reviewer_note` (`editlog.MAX_NOTE_CHARS`), so the log answers who reviewed a record and why, not just what changed. Saves go through `server.py` (stdlib `http.server`, loopback-only, per-process `X-QAAI-Token` header + JSON content-type + same-origin checks), which validates in memory, writes all three JSONL all-or-nothing (`writer.py`), and only then appends `edits.log` (`editlog.py` — tab-separated, 7 fields, append-only), so the log can never claim a write that did not land.

## Conventions worth knowing

- Reviewer graphs maximize parallelism via the `Send` API and reduce with `operator.add`. A node added to the graph that should run in parallel must have its fan-in field declared with an `operator.add` reducer in the state TypedDict, or results will overwrite instead of accumulate.
- Nodes fail **soft**: validation failure or parse failure returns an empty/skip response rather than raising, so a missing upstream field shows up as a node that "skips" in the logs (`logger... DEBUG - <Node>: skipping — validation failed`) rather than a crash.
- The hazard graph orders evaluators by data dependency: H1/R7 run immediately; H2/H3 dispatch off `design_summarizer` (they consume `summarized_designs`); H4/H5 run after the per-requirement RTM subgraph reviews and the summarizers complete; H6 runs after H4/H5 (H3's finding is already reduced by then); a deterministic `final_assessment` aggregates all seven (R7 is recommended and excluded from the verdict).
- Settings are a module-level singleton: `from qaai.core.config import settings`.
- JSON extraction in `StandardLLMNode` handles markdown fences, bracket balancing, and Llama-3.3-style missing closing delimiters. Do not manually parse LLM JSON — let the base class do it.
- The `conftest.py` `real_client` fixture reads `PYTEST_API_KEY` / `PYTEST_BASE_URL` / `PYTEST_MODEL` and will reject any `PYTEST_BASE_URL` containing "prod" to prevent accidental production charges.
