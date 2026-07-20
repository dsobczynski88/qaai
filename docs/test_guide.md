# Test Guide

<div class="meta">QAAI (qaai) · generated from the codebase 2026-07-06</div>

This guide covers how to set up, install, and run the test suites under `tests/unit`, `tests/api`, and `tests/integration`, the default fixture files each test uses, and how to run the tests against your own custom input files.

<div class="note"><strong>Recently restructured.</strong> The pytest architecture was reorganized
into three layers — a new <code>tests/unit/</code> suite (fast, no live LLM), the
<code>tests/api/</code> contract suite, and the <code>tests/integration/</code> pipeline suite. A
second pytest marker (<code>unit</code>) was added alongside <code>integration</code>, a
call-counting stub client and CLI knobs (<code>--cache-mode</code> / <code>--test-mode</code> /
<code>--include-edge-case-analysis</code>) were threaded through <code>conftest.py</code>, and the
API review endpoints became <strong>asynchronous background jobs</strong> (submit → poll →
result). The sections below reflect that layout.</div>

<h2 id="catalog">Interactive test catalog</h2>

Before working through the suites below, the fastest way to *see* what the test suite covers is the **test catalog** — a searchable, single-file HTML "book" of every test pytest collects. It is a pytest plugin registered via the `pytest11` entry point <span class="src">pyproject.toml:69-70</span>, so the `--test-catalog` flag is always available under `uv run pytest` with no `-p` needed <span class="src">plugins/qaai_testcatalog/plugin.py</span>. Because it is built from the tests pytest *actually collects*, it can never drift from the real suite.

Generate it with a **collection-only** run — no tests execute and no LLM calls are made <span class="src">plugins/qaai_testcatalog/README.md:14-25</span>:

```
uv run pytest --collect-only --test-catalog
#   -> logs/test-catalog/test_catalog.html   (open in a browser)
#   -> logs/test-catalog/test_catalog.json   (the underlying data)

# Scope it like any pytest run — the catalog tracks the selection:
uv run pytest -m unit --collect-only --test-catalog
uv run pytest tests/unit/eval --collect-only --test-catalog

# Change the output directory:
uv run pytest --collect-only --test-catalog --test-catalog-out docs/test-catalog
```

The page opens with a text search box (name / summary / fixtures / file), filter chips for **type** (unit / integration / api) and **component** (rtm / tc / hazard / eval / shared / api), sortable columns, a per-row **I/O** modal (each fixture, where it is defined, and an example input/output), a light/dark toggle, and **Copy as Markdown** / **Export JSON** buttons that respect the current filter <span class="src">plugins/qaai_testcatalog/README.md:28-31</span>. Each row's summary, type, component, fixtures, and example input are auto-derived from docstrings, markers, `item.fixturenames`, and parametrize params; the optional `@pytest.mark.catalog(summary=…, example_input=…, example_output=…)` marker curates any field (marker wins) <span class="src">plugins/qaai_testcatalog/README.md:40-69</span>.

Re-render offline from the saved JSON without re-collecting <span class="src">plugins/qaai_testcatalog/README.md:35-37</span>:

```
python -m qaai_testcatalog logs/test-catalog/test_catalog.json
```

<div class="note"><strong>Full reference.</strong> See the
<a href="test_catalog.html">Test catalog</a> page for the auto-derivation rules, every
<code>@pytest.mark.catalog</code> field, and the packaging details.</div>

<h2 id="setup">Setup &amp; install</h2>

QAAI uses **uv** and requires **Python ≥ 3.12** <span class="src">pyproject.toml:9</span>.

```
uv sync --frozen        # install locked dependencies (incl. pyjama, vendored locally at libs/pyjama)
```

pytest configuration <span class="src">pyproject.toml:48-55</span>: `asyncio_mode = "auto"`, test path `tests/`, and **two markers** — `integration` (*"marks tests that make real LLM API calls"*) and `unit` (*"marks fast unit tests with no live LLM calls"*).

### Environment variables for tests

`conftest.py` calls `load_dotenv()` at import time <span class="src">tests/conftest.py:10</span>. Integration tests read their **own** live-LLM credentials from `PYTEST_*` variables in a repo-root `.env` <span class="src">tests/conftest.py:201-222</span>:

```
PYTEST_API_KEY=<your-key>
PYTEST_BASE_URL=<your-api-url>
PYTEST_MODEL=gpt-4o-mini
```

<div class="note warn"><strong>Production guard.</strong> The <code>real_client</code> fixture
fails the test if <code>PYTEST_BASE_URL</code> contains the string <code>"prod"</code>, to prevent
accidental production charges <span class="src">tests/conftest.py:206-211</span>. If
<code>PYTEST_API_KEY</code> is unset, integration tests <em>skip</em> rather than fail
<span class="src">tests/conftest.py:203-204</span>.</div>

Test runs are isolated from server runs: all artifacts are written under `logs/tests/` (set via `settings.log_base_dir` **before** `qaai.api.main` is imported) <span class="src">tests/conftest.py:21</span>.

The unit suite needs no `.env` — it uses a call-counting stub client and mocks (boto3, the LLM client). `tests/unit/test_env_retriever.py` exercises the `APP_ENV`-driven secret hydration (DEV dotenv / PROD prefixed-mimic / AWS Secrets Manager) entirely with `monkeypatch` + a mocked boto3 client <span class="src">tests/unit/test_env_retriever.py:1-9</span>.

<h2 id="layout">Suite layout</h2>

<table>
<thead><tr><th>Path</th><th>Layer</th><th>Live LLM?</th></tr></thead>
<tbody>
<tr><td><code>tests/unit/</code></td><td>Fast unit tests — cache, transforms, input gates, viewer rendering, secrets</td><td>No (stub client / mocks)</td></tr>
<tr><td><code>tests/api/v1/</code></td><td>FastAPI contract + async-job happy paths</td><td>Only the <code>@integration</code> happy-path tests</td></tr>
<tr><td><code>tests/integration/</code></td><td>End-to-end compiled-graph pipeline runs + wiring/system checks</td><td>Pipeline tests yes; gating/wiring tests no</td></tr>
<tr><td><code>tests/conftest.py</code></td><td>Shared fixtures: clients, CLI options, recorders, input-gate data</td><td>—</td></tr>
<tr><td><code>tests/helpers.py</code></td><td>Fixture-path resolution, JSONL loading, state serialization, mock client</td><td>—</td></tr>
</tbody></table>

<h2 id="running">Running the suites</h2>

<table>
<thead><tr><th>Command</th><th>What runs</th></tr></thead>
<tbody>
<tr><td><code>uv run pytest -m "not integration"</code></td><td>Everything except live-LLM tests — the whole unit suite plus the API contract tests</td></tr>
<tr><td><code>uv run pytest tests/unit -v</code></td><td>The entire unit suite (recommended for the fast loop)</td></tr>
<tr><td><code>uv run pytest -m unit</code></td><td>Only the <strong>explicitly marked</strong> unit tests — the three input-gate files + the no-decomposition file (see note below)</td></tr>
<tr><td><code>uv run pytest -m integration -s</code></td><td>Integration + API happy-path tests (real LLM; needs <code>.env</code>)</td></tr>
<tr><td><code>uv run pytest tests/api -v</code></td><td>API contract + happy-path tests only</td></tr>
<tr><td><code>uv run pytest tests/integration -v</code></td><td>End-to-end pipeline + wiring tests only</td></tr>
<tr><td><code>uv run pytest path::test_name</code></td><td>A single test</td></tr>
</tbody></table>

<div class="note warn"><strong><code>-m unit</code> ≠ the whole <code>tests/unit</code> tree.</strong> Only four
files carry <code>pytestmark = pytest.mark.unit</code> (the three <code>test_input_gating.py</code>
files and <code>test_case_reviewer/test_no_decomposition.py</code>). The cache, transform,
viewer-log, and secrets tests are unmarked, so they are <em>not</em> selected by
<code>-m unit</code> — but they <em>are</em> picked up by <code>-m "not integration"</code> and by
<code>tests/unit</code>. Use <code>tests/unit</code> (a path) to run the full fast suite.</div>

<h2 id="unit">tests/unit — fast tests, no live LLM</h2>

These make **no network calls**. LLM clients are replaced by a call-counting stub (`stub_llm_client` / `_StubLLMClient` in conftest <span class="src">tests/conftest.py:482-510</span>) or by per-test mocks, so a test can assert exactly how many times inference would have run (e.g. `stub_llm_client.call_count == 0` proves a graph short-circuited before any LLM call). Reviewer-graph knobs come from the `review_settings` fixture (a `SimpleNamespace` of `cache_mode` / `test_mode` / `include_edge_case_analysis`, defaulted from CLI options) <span class="src">tests/conftest.py:462-479</span>.

### Input-gate &amp; topology tests <span class="pill">@pytest.mark.unit</span>

Each reviewer graph short-circuits on bad input — performing **zero inference calls** and returning `review_status == "skipped"` with the offending `missing_fields` listed — so the viewer can render an empty rubric with a warning instead of crashing. Bad-input data fixtures live in conftest <span class="src">tests/conftest.py:591-674</span>.

<table>
<thead><tr><th>File</th><th>What it asserts</th></tr></thead>
<tbody>
<tr><td><code>test_suite_reviewer/test_input_gating.py</code></td>
<td>RTM graph skips on no traced test cases or blank requirement text; missing design docs do <em>not</em> skip (<code>validate_rtm_inputs(...) == []</code>, graph proceeds) <span class="src">tests/unit/test_suite_reviewer/test_input_gating.py:24-68</span></td></tr>
<tr><td><code>test_case_reviewer/test_input_gating.py</code></td>
<td>TC graph skips on no upstream requirements (<code>missing_fields</code> ⊇ <code>requirements</code>) or empty step text (<code>test_case_steps</code>); <code>aggregated_assessment is None</code> <span class="src">tests/unit/test_case_reviewer/test_input_gating.py:20-45</span></td></tr>
<tr><td><code>hazard_risk_reviewer/test_input_gating.py</code></td>
<td>Hazard graph skips with no traced risk-control requirements, or when any field in <code>HAZARD_RISK_REVIEWER_REQUIRED_HAZARD_FIELDS</code> is blank (the missing names are reported) <span class="src">tests/unit/hazard_risk_reviewer/test_input_gating.py:24-51</span></td></tr>
<tr><td><code>test_case_reviewer/test_no_decomposition.py</code></td>
<td>"Include decomposition analysis" OFF drops the <code>decomposer</code> node and fans coverage out <em>per requirement</em> (cache key suffixed <code>_REQ-1</code>, no <code>decomposed_spec</code> in the payload); the aggregator stays None-safe when <code>decomposed_requirements</code> is absent <span class="src">tests/unit/test_case_reviewer/test_no_decomposition.py:38-113</span></td></tr>
</tbody></table>

### Cache &amp; `cache_mode` gating

`test_review_cache.py` is the largest unit file. A counting mock client lets each test assert hit/miss by inspecting `chat_completion.call_count` <span class="src">tests/unit/test_review_cache.py:34-46</span>. It covers, in five groups:

- **ReviewCacheManager** — one-folder-per-entity, append-only disk layout (`{cache_dir}/{entity_id}/{node}_{version}_{timestamp}.json`, newest wins), version-bump-is-a-miss, prompt-set namespacing (per-set subfolders that never alias), scoped/whole-entity `purge_entity`, entity/node-name sanitization, Redis-absent disk-only mode, a relative `CACHE_DIR` anchored to `PROJECT_ROOT` (the phantom-MISS regression guard), legacy meta-less files still reading as a HIT, and `extract_prompt_version` <span class="src">tests/unit/test_review_cache.py:93-257</span>
- **Base-node gating** — `on` caches interim nodes but always re-runs a `is_final_output` node; `test` reads the final node too and raises `CacheRequiredError` on a miss; `off` **writes a new timestamped file but never reads** (so it re-runs); missing `cache_mode` defaults to `on`; `cache_manager=None` disables caching <span class="src">tests/unit/test_review_cache.py:311-388</span>
- **Per-spec evaluators** — RTM/TC single-spec nodes write distinct files per `spec_id`, hit cache on rerun, and the RTM payload carries `summarized_designs` (or null) <span class="src">tests/unit/test_review_cache.py:354-436</span>
- **Send-dispatcher propagation** — `dispatch_coverage` copies `cache_mode` (and `summarized_designs`) into every fan-out payload <span class="src">tests/unit/test_review_cache.py:444-511</span>
- **Pipeline wiring** — the hazard reviewer shares the cache for its own nodes but its embedded RTM subgraph is `cache_manager is None` (cached instead as one blob per requirement) <span class="src">tests/unit/test_review_cache.py:519-537</span>

### Data transforms, viewer, secrets

<table>
<thead><tr><th>File</th><th>What it covers</th></tr></thead>
<tbody>
<tr><td><code>test_jama_transform_coercion.py</code></td>
<td>The pyjama → qaai model coercion in JAMA transforms: pyjama's <code>in_review_baseline</code> maps onto qaai's <code>in_baseline</code>, the transform wrappers return qaai-class instances, and a <code>TestSuite</code> builds without a Pydantic <code>model_type</code> error. <em>Skips if pyjama is not installed</em> <span class="src">tests/unit/test_jama_transform_coercion.py:20-22</span></td></tr>
<tr><td><code>test_hazard_bidirectional_transform.py</code></td>
<td>The hazard reviewer's <code>bidirectional_trace</code> data-integration transform: per-requirement entries aggregate + dedup into one <code>HazardTraceMatrix</code>, nested user-needs flatten, malformed entries are skipped (not raised), and <code>make_transform_node_bidirectional_trace</code> is a no-op in Excel/local mode <span class="src">tests/unit/test_hazard_bidirectional_transform.py:30-99</span></td></tr>
<tr><td><code>test_viewer_log.py</code></td>
<td>The "View log" feature: per-run problem notes embed into all three viewers (<code>build_viewer</code> / <code>_tc</code> / <code>_hz</code>) as a self-contained JSON block; an empty log renders <code>[]</code> with the button shipped hidden; a note containing <code>&lt;/script&gt;</code> is escaped so it can't break out of the embedded JSON <span class="src">tests/unit/test_viewer_log.py:15-54</span></td></tr>
<tr><td><code>test_env_retriever.py</code></td>
<td><code>EnvVariableRetriever</code> + <code>Settings.__init__</code> secret hydration: dotenv (plain + prefixed-mimic), <code>hydrate_environment</code> override semantics, the boto3-mocked Secrets Manager path, the <code>for_environment</code> factory, and end-to-end <code>APP_ENV=TEST</code> hydration from prefixed env vars <span class="src">tests/unit/test_env_retriever.py:23-144</span></td></tr>
</tbody></table>

<h2 id="api">tests/api — FastAPI contract &amp; happy-path tests</h2>

These spin up the FastAPI app via an in-process `AsyncClient` (wrapped in the app's `lifespan` so services initialize) <span class="src">tests/conftest.py:226-236</span>.

<div class="note"><strong>Reviews are asynchronous background jobs.</strong> A review
<code>POST</code> now returns <code>202</code> with a <code>job_id</code>; the result is fetched after
the job finishes. The <code>submit_and_wait</code> fixture drives
<strong>POST → 202 → poll <code>GET /api/v1/jobs/{id}</code> → <code>GET /api/v1/jobs/{id}/result</code></strong>,
returning the result response (or the original 4xx if submission was rejected)
<span class="src">tests/conftest.py:240-265</span>. So the "200 / <code>text/html</code>" assertion
describes the <em>result download</em>, not the initial <code>POST</code>.</div>

Files live under `tests/api/v1/`:

<table>
<thead><tr><th>File</th><th>Tests</th><th>Checks</th></tr></thead>
<tbody>
<tr><td><code>test_health.py</code></td><td><code>test_health_check</code></td><td><code>GET /health</code> → 200 with <code>status</code> ∈ {healthy, degraded}, <code>version</code>, and a <code>services</code> map (rtm / hazard / test_case)</td></tr>
<tr><td><code>test_general.py</code></td><td>invalid JSON, missing <code>baseline_id</code>, request-id header (+ 3 skipped placeholders for service-unavailable / rate-limit / auth)</td><td>422 validation; <code>X-Request-ID</code> present</td></tr>
<tr><td><code>test_test_suite_reviewer.py</code></td><td><code>test_test_suite_review_happy_path</code> <span class="pill">@integration</span> + 422 cases (missing / null / invalid JSON)</td><td>submit → poll → result is 200 <code>text/html</code></td></tr>
<tr><td><code>test_test_case_reviewer.py</code></td><td><code>test_tc_review_happy_path</code> <span class="pill">@integration</span> + 422 cases</td><td>same async flow for the TC endpoint</td></tr>
<tr><td><code>test_hazard_risk_reviewer.py</code></td><td>happy path <span class="pill">@integration, skipped</span>; missing file / missing project → 422; non-Excel upload → 400</td><td>multipart upload validation</td></tr>
<tr><td><code>test_cache_toggle.py</code></td><td><code>use_cache</code> / explicit <code>cache_mode</code> / <code>test_mode</code> / <code>include_edge_case_analysis</code> mapping (services stubbed)</td><td><code>use_cache=false</code>→<code>off</code>, default→<code>on</code>; explicit <code>cache_mode</code> forwarded verbatim (legacy <code>partial</code>/<code>full</code>→<code>on</code>/<code>test</code>) and wins over <code>use_cache</code>; edge-case toggle → <code>v4</code> else <code>v3</code>; <code>_select()</code> falls back to baseline for unknown sets</td></tr>
<tr><td><code>test_batch_review_gating.py</code></td><td>success-gating &amp; live-progress in <code>_run_batch_review</code> (stub graph + fake cache)</td><td>clean item kept; errored / incomplete entities purged (scoped to the run's prompt set); one bad item no longer aborts the batch (all-failing → <code>ValueError</code>); <code>Job</code> progress counters + problems-only run log shared with the viewer</td></tr>
</tbody></table>

```
uv run pytest tests/api/v1/test_cache_toggle.py -v
uv run pytest -m integration tests/api/v1/test_test_suite_reviewer.py::test_test_suite_review_happy_path
```

<h2 id="integration">tests/integration — pipeline &amp; wiring tests</h2>

This suite mixes **live-LLM pipeline runs** (marked `@integration`, need `.env`) with **stub/assertion wiring tests** that run offline.

### End-to-end pipeline runs <span class="pill">@integration</span>

These run the full compiled LangGraph pipeline against fixture files. For the RTM and test-case reviewers, every row of the selected JSONL fixture is expanded into its own parametrized item (id = `req_id` / `test_id`) by the `pytest_generate_tests` hook <span class="src">tests/conftest.py:140-154</span>.

<table>
<thead><tr><th>Test</th><th>Validates</th><th>Outputs</th></tr></thead>
<tbody>
<tr><td><code>test_suite_reviewer/pipeline.py::test_test_suite_reviewer</code></td>
<td>One <code>SynthesizedAssessment</code> per requirement; all M1–M5 present; partial-verdict invariants (<code>partial</code> implies <code>verdict=="Yes"</code>); <code>overall_verdict=Yes</code> iff M1–M5 ∈ {Yes, N-A}, with R6 excluded from the rollup <span class="src">tests/integration/test_suite_reviewer/pipeline.py:23-115</span></td>
<td><code>inputs.jsonl</code>, <code>outputs.jsonl</code>, <code>viewer.html</code></td></tr>
<tr><td><code>test_case_reviewer/pipeline.py::test_test_case_reviewer</code></td>
<td>One <code>TestCaseAssessment</code> per test case; 5-item <code>evaluated_checklist</code>; spec-align verdict derived from coverage count</td>
<td><code>inputs.jsonl</code>, <code>outputs.jsonl</code>, <code>viewer_tc.html</code></td></tr>
<tr><td><code>hazard_risk_reviewer/pipeline.py::test_hazard_risk_reviewer</code></td>
<td>One <code>RequirementReview</code> per traced requirement (each embeds a <code>SynthesizedAssessment</code>); <code>HazardAssessment</code> with 7 findings (H1–H6 mandatory + R7 recommended); only H5 may be <code>N-A</code>; R7 is excluded from the verdict</td>
<td>+ <code>hazard_pipeline_state.json</code></td></tr>
<tr><td><code>test_data_integration_backward_compat.py</code></td>
<td>RTM &amp; TC graphs still run on local data input (no <code>pyjama_request</code>): full state is produced and the JAMA fields stay <code>None</code> <span class="src">tests/integration/test_data_integration_backward_compat.py:14-107</span></td>
<td>—</td></tr>
</tbody></table>

### Wiring &amp; system tests <span class="pill">offline</span>

These are tagged `@integration` (or unmarked) but make no live LLM call — they use the stub client or plain assertions, so they run without `.env`.

<table>
<thead><tr><th>Test</th><th>What it asserts</th></tr></thead>
<tbody>
<tr><td><code>{test_suite,test_case,hazard_risk}_reviewer/test_input_gating_system.py</code></td>
<td>System-level skip path: drive a bad-input record through the real compiled graph, write <code>outputs.jsonl</code> as the service would, render the viewer, and assert the missing-fields warning banner (<code>id="missing-warning"</code>) is present and the rubric renders empty — with <code>call_count == 0</code> <span class="src">tests/integration/test_suite_reviewer/test_input_gating_system.py:19-43</span></td></tr>
<tr><td><code>test_integration_verification.py</code></td>
<td>Hazard <code>DataIntegrationNode</code> wiring: <code>HazardReviewState</code> exposes <code>pyjama_request</code>/<code>jama_data</code>/<code>jama_metadata</code>; the graph includes a <code>data_integration</code> node; local mode (no <code>pyjama_request</code>) returns an empty dict; <code>transform_hazard_record_to_state</code> signature; and the Excel/pyjama fixture files exist and parse <span class="src">tests/integration/test_integration_verification.py:19-198</span></td></tr>
</tbody></table>

```
uv run pytest tests/integration/test_suite_reviewer/pipeline.py::test_test_suite_reviewer
uv run pytest tests/integration/test_integration_verification.py -v   # offline wiring checks
```

<h2 id="fixtures">Default fixtures</h2>

Fixtures are resolved by `resolve_fixture_path` / `load_jsonl` <span class="src">tests/helpers.py:7-67</span> using the search order `mock/ → gold/ → local/ → external/ → root`, so a test's `--input-file` takes a **bare filename** and finds it wherever it lives under `tests/fixtures/`.

**Default fixture each parametrized integration test falls back to** when `--input-file` is omitted <span class="src">tests/conftest.py:53-56, 394-410</span>:

<table>
<thead><tr><th>Reviewer</th><th>Default fixture(s)</th></tr></thead>
<tbody>
<tr><td>Test Suite (RTM)</td><td><code>test_suite_review_all_fields.jsonl</code></td></tr>
<tr><td>Test Case</td><td><code>test_case_review_all_fields.jsonl</code></td></tr>
<tr><td>Hazard Risk</td><td><code>software_hazard_analysis.xlsx</code> + <code>pyjama_response_unified.jsonl</code> (<code>--pyjama-file</code>)</td></tr>
</tbody></table>

Files currently present under `tests/fixtures/`:

<table>
<thead><tr><th>Directory</th><th>Files</th></tr></thead>
<tbody>
<tr><td><code>mock/</code></td><td><code>decomposer_cases.jsonl</code>, <code>summarizer_cases.jsonl</code>, <code>generator_cases.jsonl</code>, <code>coverage_evaluator_cases.jsonl</code>, <code>synthesizer_cases.jsonl</code>, <code>tc_aggregator_cases.jsonl</code></td></tr>
<tr><td><code>gold/</code></td><td><code>gold_dataset.jsonl</code>, <code>gold_dataset_labeled.jsonl</code>, <code>gold_dataset-tc.jsonl</code>, <code>api_test_inputs.jsonl</code></td></tr>
<tr><td><code>local/</code></td><td><code>locating_device.jsonl</code></td></tr>
<tr><td><code>external/</code></td><td><code>test_suite_review_all_fields.jsonl</code>, <code>test_suite_review_min_fields.jsonl</code>, <code>test_case_review_all_fields.jsonl</code>, <code>test_case_review_min_fields.jsonl</code>, <code>software_hazard_analysis.xlsx</code>, <code>pyjama_response_unified.jsonl</code></td></tr>
</tbody></table>

JSONL fixtures are newline-delimited; each line is a self-contained graph-input object. The hazard test assembles its `HazardRowWithTraceMatrix` input **programmatically** from the Excel + PyJama fixtures (there is no single `hazard_full_traceability.jsonl`) <span class="src">tests/conftest.py:341-410</span>.

<h2 id="custom">Running with custom files</h2>

The reviewer tests accept these CLI options, all registered in `pytest_addoption` <span class="src">tests/conftest.py:59-113</span>:

<table>
<thead><tr><th>Option</th><th>Default</th><th>Meaning</th></tr></thead>
<tbody>
<tr><td><code>--input-file</code></td><td>per-reviewer (above)</td><td>Bare fixture filename — JSONL for RTM/TC, the <code>.xlsx</code> SHA workbook for hazard</td></tr>
<tr><td><code>--pyjama-file</code></td><td><code>pyjama_response_unified.jsonl</code></td><td>Hazard only: traceability JSONL</td></tr>
<tr><td><code>--cache-mode</code></td><td><code>off</code></td><td><code>cache_mode</code> threaded into graph state (<code>off</code>/<code>on</code>/<code>test</code>), surfaced by <code>review_settings</code></td></tr>
<tr><td><code>--test-mode</code></td><td><code>true</code></td><td>When true, tests use the call-counting stub client instead of a real LLM client</td></tr>
<tr><td><code>--include-edge-case-analysis</code></td><td><code>false</code></td><td>Selects the edge-case prompt set for the test-suite / hazard reviewers</td></tr>
</tbody></table>

```
# RTM / test-case: --input-file is the JSONL of input rows
uv run pytest tests/integration/test_suite_reviewer/pipeline.py::test_test_suite_reviewer \
  --input-file=locating_device.jsonl

# Preview which items a file expands to, without spending any LLM calls
uv run pytest tests/integration/test_suite_reviewer/pipeline.py::test_test_suite_reviewer \
  --input-file=locating_device.jsonl --collect-only

# Run a single row by its id
uv run pytest tests/integration/test_suite_reviewer/pipeline.py::test_test_suite_reviewer \
  --input-file=locating_device.jsonl -k REQ-PUMP-001

# Hazard: --input-file is the SHA .xlsx; --pyjama-file overrides the traceability JSONL
uv run pytest tests/integration/hazard_risk_reviewer/pipeline.py::test_hazard_risk_reviewer \
  --input-file=software_hazard_analysis.xlsx --pyjama-file=pyjama_response_unified.jsonl

# Override the graph knobs (e.g. run the unit input-gates with interim caching on)
uv run pytest -m unit --cache-mode on
```

### Adding your own fixture

1. Drop the file into a fixtures subdir — `tests/fixtures/local/` is recommended for project-specific data, e.g. `tests/fixtures/local/my_requirements.jsonl`.
2. Match the reviewer's graph-input schema per row:
  - RTM: `{"requirement": {...}, "test_cases": [...], "design_docs": [...]}`
  - TC: `{"test_case": {...}, "requirements": [...]}` (`design_docs` is accepted by the schema but unused by this reviewer — safe to omit)
3. Run by **bare filename**: `--input-file=my_requirements.jsonl` (found via the search order above, unless shadowed by a same-named file in `mock/` or `gold/`).

<h2 id="artifacts">Run artifacts</h2>

Session-scoped recorder fixtures (`jsonl_recorders` / `_tc` / `_hz`) clear and append each input/output, then generate the HTML viewer from `outputs.jsonl` at session teardown <span class="src">tests/conftest.py:413-454</span>. Per session, under `logs/tests/run-<timestamp>/` <span class="src">tests/conftest.py:157-170</span>:

- `inputs.jsonl` / `outputs.jsonl` — recorded records
- `viewer.html` / `viewer_tc.html` / `viewer_hz.html` — generated viewer
- `token_usage.jsonl` — per-call token/cost records + a session summary (`token_tracker` teardown) <span class="src">tests/conftest.py:173-191</span>
- `qaai.log` — node/app logs for the run
- `graph.png` — graph diagram (RTM pipeline test writes it via `write_graph_png`) <span class="src">tests/integration/test_suite_reviewer/pipeline.py:89</span>
- `hazard_pipeline_state.json` — full hazard graph state (hazard test only)
