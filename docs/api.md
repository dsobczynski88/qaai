# API Server & Frontend Guide

<div class="meta">QAAI (qaai) · generated from the codebase 2026-07-20</div>

QAAI exposes three LangGraph reviewers behind a FastAPI app (`qaai.api.main:app`). Every review runs as an **asynchronous background job** and returns a self-contained HTML viewer once complete.

<h2 id="start">Starting the development server</h2>

```
uv run uvicorn qaai.api.main:app --reload
```

Expected console output:

```
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
```

Look for these log lines confirming services initialized in the `lifespan` context manager <span class="src">qaai/api/main.py:71-161</span>:

```
QAAI services initialized successfully
QAAI API created (environment: development)
```

The `--reload` flag is development-only. The factory is `create_app()` <span class="src">qaai/api/main.py:164-231</span>, which adds request-logging + body-size-limit middleware, conditional CORS, and GZip.

<h2 id="frontend">Opening the frontend</h2>

Navigate to **http://localhost:8000**. The interactive UI is a **Vue 3 + Vite SPA** built to `qaai/web/dist/` and mounted at `/` via `NoCacheStaticFiles`; if that build is absent the server falls back to the build-less legacy page under `qaai/api/static/` <span class="src">qaai/api/main.py:216-228</span>. The page shows three reviewer cards (Requirement Coverage, Test Case Adequacy, Software Hazard Analysis); selecting one reveals its input form. These documentation pages are also served at `/guide` <span class="src">qaai/api/main.py:207-209</span>. Interactive API docs are at `/docs` and `/redoc` — both are **hidden when `ENVIRONMENT=production`** <span class="src">qaai/api/main.py:179-185</span>. See the [Frontend &amp; RBAC design](design/frontend_vue_rbac.html) for the SPA, the async job engine, and the `/api/v1/me` identity/roles seam.

<div class="note warn"><strong><code>qaai/api/static/</code> is deprecated.</strong> The Vue SPA
under <code>qaai/web/</code> is the supported frontend and the only one that receives new
features; the legacy vanilla page survives purely as the build-less fallback taken when
<code>qaai/web/dist/</code> is missing <span class="src">qaai/api/main.py:216-228</span>. It is
~1,600 lines of separately-maintained HTML/CSS/JS that does <strong>not</strong> track the SPA —
notably it has none of the RBAC gating. Treat it as a safety net for an unbuilt checkout, not a
second UI to keep in sync: build the SPA (<code>npm run build</code> in <code>qaai/web</code>)
rather than adding features to <code>static/</code>.</div>

## Endpoints

All routes are mounted under `/api/v1` <span class="src">qaai/api/routes.py</span>.

<table>
<thead><tr><th>Method &amp; path</th><th>Body / form</th><th>Success</th><th>Permission</th><th>Source</th></tr></thead>
<tbody>
<tr><td><span class="pill get">GET</span> <code>/api/v1/me</code></td><td>—</td><td>200 <code>{user, roles}</code></td><td>public</td><td><span class="src">routes.py:53-63</span></td></tr>
<tr><td><span class="pill get">GET</span> <code>/api/v1/health</code></td><td>—</td><td>200 (or 503 degraded)</td><td>public</td><td><span class="src">routes.py:84-102</span></td></tr>
<tr><td><span class="pill get">GET</span> <code>/api/v1/usage</code></td><td>—</td><td>200 <code>{rate_limits, totals}</code></td><td><code>manage</code> (admin)</td><td><span class="src">routes.py:66-81</span></td></tr>
<tr><td><span class="pill post">POST</span> <code>/api/v1/test-suite-review</code></td><td><code>BaselineRequest</code> JSON</td><td>202 + <code>job_id</code></td><td><code>run_review</code></td><td><span class="src">routes.py:105-133</span></td></tr>
<tr><td><span class="pill post">POST</span> <code>/api/v1/test-case-review</code></td><td><code>BaselineRequest</code> JSON</td><td>202 + <code>job_id</code></td><td><code>run_review</code></td><td><span class="src">routes.py:136-161</span></td></tr>
<tr><td><span class="pill post">POST</span> <code>/api/v1/hazard-risk-review</code></td><td><code>multipart/form-data</code></td><td>202 + <code>job_id</code></td><td><code>run_review</code></td><td><span class="src">routes.py:164-214</span></td></tr>
<tr><td><span class="pill get">GET</span> <code>/api/v1/jobs/{job_id}</code></td><td>—</td><td>200 status (404 unknown)</td><td><code>run_review</code></td><td><span class="src">routes.py:229-239</span></td></tr>
<tr><td><span class="pill post">POST</span> <code>/api/v1/jobs/{job_id}/cancel</code></td><td>—</td><td>200 <code>{status, cancelled}</code> (Stop Run)</td><td><code>run_review</code></td><td><span class="src">routes.py:242-257</span></td></tr>
<tr><td><span class="pill get">GET</span> <code>/api/v1/jobs/{job_id}/result</code></td><td>—</td><td>200 HTML (see codes below)</td><td><code>run_review</code></td><td><span class="src">routes.py:260-278</span></td></tr>
<tr><td><span class="pill post">POST</span> <code>/api/v1/feedback-upload</code></td><td><code>multipart</code> (<code>.json</code> file)</td><td>200 <code>{saved, status}</code></td><td><code>upload_feedback</code></td><td><span class="src">routes.py:308-337</span></td></tr>
</tbody></table>

<div class="note"><strong>Authorization is enforced server-side.</strong> Every non-public
route above carries a <code>Depends(...)</code> guard from <code>qaai/api/authz.py</code>
that fails closed: <strong>401</strong> when unauthenticated, <strong>403</strong> when
authenticated but lacking the listed permission. Roles are <code>admin</code> (holds
<code>run_review</code>, <code>upload_feedback</code>, <code>manage</code>) and
<code>user</code> (<code>run_review</code>, <code>upload_feedback</code>). The jobs endpoints
require <code>run_review</code> too, because the SPA auto-polls and downloads on every run.
See <a href="design/frontend_vue_rbac.html#rbac">RBAC model</a> and the
<a href="deployment.html">Deployment guide</a>. <span class="src">qaai/api/authz.py:27-66</span></div>

`GET /api/v1/me` returns the caller's identity and roles for the SPA's RBAC layer (resolved from the ALB/OIDC header or a DEV fallback); it is public and identity-read only. `GET /api/v1/usage` exposes the shared limiter's RPM/TPM utilization + rolling token/cost totals for monitoring (admin-only). `POST /api/v1/feedback-upload` validates the exported-feedback JSON shape (422 on mismatch) and stores it under `./shared/feedback/`.

### Health check

```
curl http://localhost:8000/api/v1/health
```

Returns 200 with each service marked `available` when initialized; if any service is missing it returns **503** with `"status": "degraded"` <span class="src">routes.py:84-102</span>:

```
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

<h2 id="jobs">The asynchronous job model</h2>

A review can take several minutes. If the report were returned synchronously, an upstream proxy (JupyterHub's jupyter-server-proxy, an AWS ALB, etc.) would see an idle upstream and return a **504**. So the three review endpoints run the work as a background job <span class="src">qaai/api/jobs.py</span>:

1. **Submit** — `POST` a review endpoint → `202 Accepted` with `{"job_id": "...", "status": "pending"}` in well under a second.
2. **Poll** — `GET /api/v1/jobs/{job_id}` returns `Job.to_status_dict()`: `{job_id, status, filename, error, total, done, succeeded, failed, eta_seconds, messages}`. `status` ∈ `pending`, `running`, `completed`, `failed`, `cancelled` <span class="src">jobs.py:34-38</span>.
3. **Cancel (optional)** — `POST /api/v1/jobs/{job_id}/cancel` stops an in-flight run (the UI's "Stop Run"); the job then reports `cancelled`.
4. **Download** — `GET /api/v1/jobs/{job_id}/result` returns the HTML report (`200`) when `completed`. It returns `404` for an unknown id, `425 Too Early` while pending/running, and the job's failure status when it failed — `400` for bad input, `499` for a user-cancelled run, `500` otherwise <span class="src">routes.py:260-278, jobs.py:166-195</span>.

<div class="note warn"><strong>Single worker.</strong> Jobs live in an in-memory registry
(most-recent <strong>200</strong> retained). Reviews now run <strong>concurrently</strong> — the
old <code>asyncio.Lock</code> that serialized them is gone; each review binds its own run folder
via the <code>current_run_dir</code> contextvar so logs/telemetry/cache stay isolated with no lock
<span class="src">jobs.py, qaai/core/logging_config.py</span>. The in-memory registry still assumes a
single uvicorn worker — see <a href="#prod">Production</a>. The frontend performs submit → poll →
download automatically (polling every ~4 s, showing elapsed time and per-item progress).</div>

<div class="note"><strong>Reviews and their items both run concurrently.</strong> Multiple
submitted jobs execute in parallel (no job-level lock); within each job <code>_run_batch_review</code>
<span class="src">qaai/api/services.py:233-298</span> fans a job's items out with
<code>asyncio.gather</code> under an <code>asyncio.Semaphore</code> sized by that reviewer's per-job
knob — <code>TEST_SUITE_MAX_CONCURRENT_REVIEWS</code> (8), <code>TEST_CASE_MAX_CONCURRENT_REVIEWS</code>
(8), or <code>HAZARD_MAX_CONCURRENT_REVIEWS</code> (<strong>1</strong>) — rather than awaiting them
one at a time. The hazard reviewer defaults to <strong>1</strong> so records run sequentially and
the first warms the shared <code>DD-*</code>/<code>REQ-*</code> cache before the next; its embedded
RTM subgraph fan-out is separately bounded by <code>TEST_SUITE_MAX_CONCURRENT_REVIEWS</code>. A single
item's exception (<code>return_exceptions=True</code>) never cancels its siblings — the item is
skipped, its run-scoped cache entries are purged via <code>purge_run</code> so the failed attempt is
never reused (see <a href="configuration.html#caching">Configuration &amp; Caching</a>), and the job
only fails outright if <em>nothing</em> produced output or if a <code>test</code>-mode cache miss
occurs on any item. Regardless of completion order, <code>outputs.jsonl</code> is written back in
the original input order.</div>

<h2 id="hazard">Testing the hazard upload endpoint</h2>

The hazard endpoint takes an uploaded SHA Excel workbook instead of a `baseline_id`. The example below runs offline against **cached JAMA results** by passing `test_mode=true`:

```
# 1. Submit the job -> 202 {"job_id": "...", "status": "pending"}
JOB=$(curl -s -X POST http://localhost:8000/api/v1/hazard-risk-review \
  -F "project_name=Test Project" \
  -F "file=@tests/fixtures/external/software_hazard_analysis.xlsx" \
  -F "sheet_name=SHA Table" \
  -F "test_mode=true" | jq -r .job_id)   # cached JAMA only; omit for a live JAMA fetch

# 2. Poll until "status" is "completed"
curl -s http://localhost:8000/api/v1/jobs/$JOB

# 3. Download the completed report
curl -s http://localhost:8000/api/v1/jobs/$JOB/result --output qaai_hazard_review.html
```

<div class="note warn"><strong>The Excel alone is not enough — the hazard reviewer needs JAMA.</strong>
The uploaded workbook supplies only the hazard-register fields and the requirement <em>ID
references</em> (GIDs scraped from the Risk Control Measures column); it does <strong>not</strong>
contain the requirement text, test cases, or design documents the review evaluates. Those are
fetched from JAMA via a <code>bidirectional_trace</code> request keyed on the extracted IDs
<span class="src">qaai/api/services.py:503-515,586-594</span>. So the reviewer needs either
<strong>live JAMA credentials</strong> (see <a href="#baseline">Baseline reviews</a>) or
<strong>cached JAMA results</strong> (<code>test_mode=true</code> / <code>cache_mode=test</code>) to
run correctly. Only the scalar hazard-register dimensions (H1, H3, H6, R7, and H2) come from the
Excel alone; the per-requirement RTM sub-review and the H4/H5 coverage/verification dimensions
depend on the JAMA-traced data and evaluate empty inputs without it
<span class="src">qaai/agents/hazard_risk_reviewer/loader.py:91-115</span>.</div>

Multipart form fields <span class="src">routes.py:167-175</span>:

<table>
<thead><tr><th>Field</th><th>Required</th><th>Default</th><th>Notes</th></tr></thead>
<tbody>
<tr><td><code>project_name</code></td><td>Yes</td><td>—</td><td>Project / product name</td></tr>
<tr><td><code>file</code></td><td>Yes</td><td>—</td><td>SHA Excel (<code>.xlsx</code>/<code>.xls</code>); other types → <strong>400</strong></td></tr>
<tr><td><code>sheet_name</code></td><td>No</td><td><code>SHA Table</code></td><td>Worksheet holding the hazard table</td></tr>
<tr><td><code>identifier_pattern</code></td><td>No</td><td><code>GID-\d+</code></td><td>Regex for hazard/global identifiers; passed to the loader as <code>extract_gids_format</code></td></tr>
<tr><td><code>cache_mode</code></td><td>No</td><td>—</td><td><code>off</code>/<code>on</code>/<code>test</code> (legacy <code>partial</code>/<code>full</code> accepted); overrides <code>use_cache</code></td></tr>
<tr><td><code>use_cache</code></td><td>No</td><td><code>true</code></td><td>Legacy toggle: <code>true</code> → <code>on</code>, <code>false</code> → <code>off</code></td></tr>
<tr><td><code>test_mode</code></td><td>No</td><td>server default</td><td>Cache-only JAMA; omit to use <code>PYJAMA_TEST_MODE</code></td></tr>
<tr><td><code>include_edge_case_analysis</code></td><td>No</td><td><code>false</code></td><td><code>true</code> → embedded RTM uses <code>test_suite_reviewer_v4</code> (edge-case); <code>false</code> → <code>v3</code> baseline</td></tr>
<tr><td><code>include_design_summaries</code></td><td>No</td><td><code>false</code></td><td><code>true</code> → embedded RTM runs its <code>design_summarizer</code> (design context feeds coverage &amp; R6); does not affect the hazard rubric's own H2/H3</td></tr>
</tbody></table>

The per-row thread ID is derived server-side from the hazard's `hazard_id` (falling back to the job-derived index) <span class="src">qaai/api/services.py:293-298</span>.

<h2 id="baseline">Testing baseline reviews (RTM &amp; test case)</h2>

RTM and test-case baseline reviews require JAMA credentials in `.env`:

```
JAMA_HOST_ADDRESS=<your_jama_host>
JAMA_CLIENT_ID=<your_client_id>
JAMA_CLIENT_SECRET=<your_client_secret>
```

If JAMA is not configured the `POST` still returns `202`, but the **job fails**: polling reports `"status": "failed"` and the result endpoint returns the error, e.g. `{"detail": "PyJama is not installed — JAMA baseline fetching unavailable."}`.

```
# RTM review from baseline
JOB=$(curl -s -X POST http://localhost:8000/api/v1/test-suite-review \
  -H "Content-Type: application/json" \
  -d '{"baseline_id": "BASE-12345", "use_cache": true}' | jq -r .job_id)
curl -s http://localhost:8000/api/v1/jobs/$JOB                       # poll until completed
curl -s http://localhost:8000/api/v1/jobs/$JOB/result --output qaai_rtm_review.html

# Test case review from baseline
JOB=$(curl -s -X POST http://localhost:8000/api/v1/test-case-review \
  -H "Content-Type: application/json" \
  -d '{"baseline_id": "BASE-67890", "use_cache": true}' | jq -r .job_id)
curl -s http://localhost:8000/api/v1/jobs/$JOB/result --output qaai_tc_review.html
```

Both endpoints accept a `BaselineRequest` body <span class="src">qaai/api/schemas.py:6-64</span>:

<table>
<thead><tr><th>Field</th><th>Type</th><th>Default</th><th>Meaning</th></tr></thead>
<tbody>
<tr><td><code>baseline_id</code></td><td>string</td><td>required</td><td>JAMA baseline to fetch and review</td></tr>
<tr><td><code>cache_mode</code></td><td><code>"off"|"on"|"test"</code>? (also accepts legacy <code>"partial"|"full"</code>)</td><td><code>null</code></td><td>Explicit per-run cache mode; when set it overrides <code>use_cache</code> <span class="src">routes.py:41-50</span></td></tr>
<tr><td><code>use_cache</code></td><td>bool (deprecated)</td><td><code>true</code></td><td><code>true</code> → <code>cache_mode="on"</code>; <code>false</code> → <code>"off"</code> (used only when <code>cache_mode</code> is unset) <span class="src">routes.py:120,150</span></td></tr>
<tr><td><code>test_mode</code></td><td>bool?</td><td><code>null</code></td><td>Cache-only JAMA (no live calls); <code>null</code> → <code>PYJAMA_TEST_MODE</code></td></tr>
<tr><td><code>include_edge_case_analysis</code></td><td>bool</td><td><code>false</code></td><td><code>true</code> → prompt set <code>test_suite_reviewer_v4</code> (edge-case decomposer v6); <code>false</code> → <code>test_suite_reviewer_v3</code> (baseline v5). Applies to the test-suite review only. <span class="src">routes.py:122</span></td></tr>
<tr><td><code>include_decomposition_analysis</code></td><td>bool</td><td><code>true</code></td><td>Test-case review only: <code>true</code> → <code>test_case_reviewer_v2</code> (decomposition); <code>false</code> → <code>test_case_reviewer_v3</code> (no-decomposition) <span class="src">routes.py:157</span></td></tr>
<tr><td><code>include_design_summaries</code></td><td>bool</td><td><code>false</code></td><td>Test-suite review only: <code>true</code> → run the <code>design_summarizer</code> so design context feeds per-spec coverage and the R6 criterion; <code>false</code> skips that branch <span class="src">qaai/api/schemas.py:55-64</span></td></tr>
<tr><td><code>baseline_review_type</code></td><td><code>"requirements"|"tests"</code></td><td><code>"tests"</code></td><td>Test-suite review only: <code>tests</code> fetches baseline items as test cases traced up to their requirements (<code>request_type=test_suite_review</code>, the original behavior); <code>requirements</code> fetches requirement ids directly (<code>request_type=requirement_review</code>). Both produce the same per-requirement output shape <span class="src">qaai/api/schemas.py:65-75</span></td></tr>
</tbody></table>

<div class="note"><strong>Reviewing a queue of baselines without the API/job layer.</strong>
<code>scripts/run_baselines.py</code> drives the RTM graph directly over one or more
baseline ids, sequentially, wiring the client/model/cache_manager the same way
<code>qaai.api.main:lifespan</code> does so behavior matches a real
<code>/api/v1/test-suite-review</code> request — useful for a scripted queue without going
through jobs/polling. Runs land under <code>logs/tests/</code> by default (kept separate
from API-server runs under <code>logs/</code>); one bad baseline doesn't stop the queue.
<pre><code>uv run python scripts/run_baselines.py BASE-1 BASE-2 BASE-3
uv run python scripts/run_baselines.py --file baselines.txt --cache-mode off --edge-case</code></pre></div>

<div class="note"><strong>Every run option is documented in one place.</strong> The cache-mode radio
(<code>off</code> / <code>on</code> / <code>test</code>) and the three analysis toggles
(<code>include_edge_case_analysis</code>, <code>include_decomposition_analysis</code>,
<code>include_design_summaries</code>) apply whether you call the API directly or use the SPA —
what each does, its values, default, and which reviewer it affects are all in
<strong><a href="review_options.html">Review options &amp; toggles</a></strong>. For the caching
mechanics behind the cache modes, see <a href="configuration.html#caching">Configuration →
Caching</a>.</div>

<h2 id="outputs">Understanding the response &amp; viewers</h2>

The review endpoints return `202` + a `job_id`; the HTML report is downloaded from `GET /api/v1/jobs/{job_id}/result` once `completed`. The downloaded viewer opens directly in a browser and contains a per-item rubric summary with drill-downs. Viewer files are also written to the run directory <span class="src">qaai/viewer/generator.py</span>:

- RTM viewer: `logs/run-<timestamp>/viewer.html`
- Test case viewer: `logs/run-<timestamp>/viewer_tc.html`
- Hazard viewer: `logs/run-<timestamp>/viewer_hz.html`

The structured per-record state is also written one-per-line to `outputs.jsonl` (inputs to `inputs.jsonl`) in the same run directory by the shared `_run_batch_review` helper <span class="src">qaai/api/services.py:29-72</span>.

<h2 id="config">Configuration</h2>

Key environment variables (full list in [configuration.html](configuration.html) and `qaai/core/config.py:115-272`):

<table>
<thead><tr><th>Variable</th><th>Required</th><th>Purpose</th></tr></thead>
<tbody>
<tr><td><code>API_KEY</code></td><td>Yes</td><td>OpenAI-compatible API key</td></tr>
<tr><td><code>API_BASE_URL</code></td><td>No</td><td>Base URL (defaults to official OpenAI)</td></tr>
<tr><td><code>API_MODEL</code></td><td>Yes</td><td>Model name (e.g. <code>gpt-4o-mini</code>)</td></tr>
<tr><td><code>JAMA_HOST_ADDRESS</code> / <code>JAMA_CLIENT_ID</code> / <code>JAMA_CLIENT_SECRET</code></td><td>No</td><td>JAMA OAuth (needed only for baseline reviews)</td></tr>
<tr><td><code>PYJAMA_TEST_MODE</code></td><td>No</td><td>Cache-only JAMA (default <code>false</code>)</td></tr>
<tr><td><code>ENABLE_CACHE</code> / <code>CACHE_DIR</code></td><td>No</td><td>Review cache (see configuration.html)</td></tr>
<tr><td><code>PROMPT_SET</code></td><td>No</td><td>Named prompt set to load</td></tr>
<tr><td><code>ALLOWED_ORIGINS</code></td><td>No</td><td>CORS origins (default <code>*</code>)</td></tr>
<tr><td><code>ENVIRONMENT</code></td><td>No</td><td><code>development</code>/<code>production</code> (prod hides API docs)</td></tr>
</tbody></table>

<h2 id="debug">Debugging tips</h2>

Real-time logs appear in the console and at `logs/run-<timestamp>/qaai.log`. Because reviews run as background jobs, the `POST` completes immediately with `202` and the review progress is logged under the job's run folder:

```
Request started: POST /api/v1/hazard-risk-review
Request completed: POST /api/v1/hazard-risk-review - 202
```

Each run also writes `token_usage.jsonl` with per-call token and cost metrics <span class="src">qaai/core/telemetry.py</span>.

<h2 id="prod">Production deployment</h2>

<div class="note"><strong>Full guide.</strong> The reproducible AWS path — the multi-stage
<code>Dockerfile</code>, single-worker run, EBS volumes, ALB OIDC listener config, PROD env
vars, and a post-deploy smoke test — is in the <a href="deployment.html">Deployment
guide</a>. The essentials:</div>

1. Remove `--reload` (dev-only); run a **single** uvicorn worker (see below).
2. Set `APP_ENV=PROD` so secrets hydrate from the AWS store and OIDC signature verification turns on.
3. Set `ENVIRONMENT=production` to hide API docs.
4. Put an ALB with an OIDC listener (AD security group) in front; it injects `x-amzn-oidc-data`, which QAAI verifies. Keep `ALB_OIDC_REGION` matching the ALB region.
5. Set `ALLOWED_ORIGINS` to your domain(s) instead of `*`; keep secrets out of the image (Secrets Manager / SSM).
6. Mount an EBS volume for `./shared` (cache + regulatory evidence) and `./logs` so they survive restarts.

<div class="note warn"><strong>Run a single worker.</strong> The async job registry is
in-memory <span class="src">qaai/api/jobs.py</span>, so a <code>job_id</code> created on
one worker is invisible to another; the shared RPM/TPM rate limiter is likewise per-process, so
N workers would each assume the full quota and multiply outbound LLM load. Keep <code>--workers 1</code>
until the registry moves to a shared backend — <code>qaai/api/run.py</code> logs a loud warning if
<code>QAAI_API_WORKERS &gt; 1</code>. Reviews still run concurrently <em>within</em> the one worker,
which comfortably covers the ~10-concurrent-user target. Monitor aggregate LLM usage across all
users via <code>GET /api/v1/usage</code> (current RPM/TPM window utilization vs. caps + rolling
token/cost totals).</div>

```
gunicorn qaai.api.main:app \
  --workers 1 \
  --worker-class uvicorn.workers.UvicornWorker \
  --bind 0.0.0.0:8000
```
