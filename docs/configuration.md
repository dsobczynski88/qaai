# Configuration Guide

<div class="meta">QAAI (qaai) · generated from the codebase 2026-07-06</div>

All runtime configuration is centralized in the `Settings` singleton <span class="src">qaai/core/config.py:115-272</span>, loaded from a repo-root `.env` and the process environment. Import it via `from qaai.core.config import settings`.

<h2 id="env">Environment variables</h2>

<table>
<thead><tr><th>Variable</th><th>Field</th><th>Default</th><th>Purpose</th></tr></thead>
<tbody>
<tr><td><code>API_KEY</code></td><td><code>openai_api_key</code></td><td><em>required</em></td><td>OpenAI-compatible API key</td></tr>
<tr><td><code>API_BASE_URL</code></td><td><code>url</code></td><td><code>None</code></td><td>Base URL (None → official OpenAI)</td></tr>
<tr><td><code>API_MODEL</code></td><td><code>model</code></td><td><em>required</em></td><td>Model id (e.g. <code>gpt-4o-mini</code>)</td></tr>
<tr><td><code>MAX_REQUESTS_PER_MINUTE</code></td><td><code>max_requests_per_minute</code></td><td><code>5000</code></td><td>Client RPM ceiling</td></tr>
<tr><td><code>MAX_TOKENS_PER_MINUTE</code></td><td><code>max_tokens_per_minute</code></td><td><code>5000000</code></td><td>Client TPM ceiling</td></tr>
<tr><td><code>MAX_OUTPUT_TOKENS</code></td><td><code>max_output_tokens</code></td><td><code>16000</code></td><td>Max output tokens per request</td></tr>
<tr><td><code>TOKEN_COST_INPUT_PER_M</code></td><td><code>token_cost_input_per_m</code></td><td><code>1.00</code></td><td>USD / million input tokens (telemetry)</td></tr>
<tr><td><code>TOKEN_COST_OUTPUT_PER_M</code></td><td><code>token_cost_output_per_m</code></td><td><code>5.00</code></td><td>USD / million output tokens (telemetry)</td></tr>
<tr><td><code>JAMA_HOST_ADDRESS</code></td><td><code>jama_host_address</code></td><td><code>None</code></td><td>JAMA host URL</td></tr>
<tr><td><code>JAMA_CLIENT_ID</code></td><td><code>jama_client_id</code></td><td><code>None</code></td><td>JAMA OAuth client id</td></tr>
<tr><td><code>JAMA_CLIENT_SECRET</code></td><td><code>jama_client_secret</code></td><td><code>None</code></td><td>JAMA OAuth client secret</td></tr>
<tr><td><code>PYJAMA_TEST_MODE</code></td><td><code>pyjama_test_mode</code></td><td><code>false</code></td><td>Cache-only JAMA, no live calls</td></tr>
<tr><td><code>ENABLE_CACHE</code></td><td><code>enable_cache</code></td><td><code>true</code></td><td>Master review-cache switch</td></tr>
<tr><td><code>CACHE_DIR</code></td><td><code>cache_dir</code></td><td><code>./shared/runs</code></td><td>Disk cache directory (one folder per entity)</td></tr>
<tr><td><code>REDIS_URL</code></td><td><code>redis_url</code></td><td><code>None</code></td><td>Optional Tier-2 Redis cache accelerator (24h TTL); disk-only when unset</td></tr>
<tr><td><code>ENABLE_JSON_RESPONSE_FORMAT</code></td><td><code>enable_json_response_format</code></td><td><code>true</code></td><td>Request JSON-object response format from the model when supported</td></tr>
<tr><td><code>PROMPT_SET</code></td><td><code>prompt_set</code></td><td><code>None</code></td><td>Named prompt set to load</td></tr>
<tr><td><code>APP_ENV</code></td><td><code>app_env</code></td><td><code>DEV</code></td><td><code>TEST</code>/<code>PROD</code> pull secrets from the AWS secret store (see below)</td></tr>
<tr><td><code>QAAI_SECRET_ID</code></td><td>(read in <code>secrets.py</code>)</td><td><code>None</code></td><td>AWS Secrets Manager secret id used when <code>APP_ENV=TEST/PROD</code></td></tr>
<tr><td><code>ALLOWED_ORIGINS</code></td><td>(read in <code>main.py</code>)</td><td><code>*</code></td><td>CORS origins (comma-separated)</td></tr>
<tr><td><code>ENVIRONMENT</code></td><td>(read in <code>main.py</code>)</td><td><code>development</code></td><td><code>production</code> hides <code>/docs</code> &amp; <code>/redoc</code></td></tr>
<tr><td><code>QAAI_DEV_USER</code></td><td><code>dev_user_name</code></td><td><code>Local Dev</code></td><td>Identity name for the DEV fallback used by <code>GET /api/v1/me</code></td></tr>
<tr><td><code>QAAI_DEV_EMAIL</code></td><td><code>dev_user_email</code></td><td><code>dev@localhost</code></td><td>Identity email for the DEV fallback</td></tr>
<tr><td><code>QAAI_DEV_ROLES</code></td><td><code>dev_roles</code></td><td><code>admin</code></td><td>Comma-separated roles for the DEV fallback (validated against <code>admin</code>/<code>reviewer</code>/<code>viewer</code>)</td></tr>
<tr><td><code>QAAI_OIDC_ROLE_MAP</code></td><td><code>oidc_role_map_json</code></td><td><code>""</code></td><td>JSON map of SSO group → role, e.g. <code>{"qaai-admins":"admin"}</code></td></tr>
</tbody></table>

<div class="note">The RBAC/identity variables feed <code>GET /api/v1/me</code> and the SPA's role
gating — see the <a href="design/frontend_vue_rbac.html">Frontend &amp; RBAC design</a>. Real
per-route enforcement and OIDC signature verification are a documented follow-up; today the
roles are UX gating only.</div>

Example `.env`:

```
# AI model (required)
API_KEY=sk-...
API_BASE_URL=https://api.openai.com/v1
API_MODEL=gpt-4o-mini

# JAMA (only for baseline reviews)
JAMA_HOST_ADDRESS=https://your.jamacloud.com
JAMA_CLIENT_ID=...
JAMA_CLIENT_SECRET=...
PYJAMA_TEST_MODE=false

# Caching
ENABLE_CACHE=true
CACHE_DIR=./shared/runs
# REDIS_URL=redis://localhost:6379/0   # optional Tier-2 accelerator

# Prompts
# PROMPT_SET=test_suite_reviewer_v4

# Deployment environment (DEV reads this .env; TEST/PROD pull from AWS Secrets
# Manager via EnvVariableRetriever — set QAAI_SECRET_ID + AWS_REGION on the host).
# Locally you can mimic the AWS flow with prefixed vars, e.g. APP_ENV=PROD plus
# PROD_API_KEY=... PROD_API_MODEL=... (no QAAI_SECRET_ID set).
# APP_ENV=DEV
```

<h2 id="models">AI model settings</h2>

`API_KEY`, `API_BASE_URL`, and `API_MODEL` configure the `RateLimitOpenAIClient` built at app startup <span class="src">qaai/api/main.py:84-98</span>. Any OpenAI-compatible endpoint works (OpenAI, Ollama, vLLM, Bedrock via langchain-aws). The rate/token settings (`MAX_REQUESTS_PER_MINUTE`, `MAX_TOKENS_PER_MINUTE`, `MAX_OUTPUT_TOKENS`) feed the client's proactive RPM/TPM limiter; the `TOKEN_COST_*` rates drive the cost figures in `token_usage.jsonl` <span class="src">qaai/core/telemetry.py</span>.

<div class="note">Models listed in <code>models_using_max_completion_tokens</code> (default
<code>{"gpt-5.4-mini", "gpt-5-mini"}</code>) receive the output cap as
<code>max_completion_tokens</code> instead of <code>max_tokens</code>
<span class="src">qaai/core/config.py:149, qaai/api/main.py:95-98</span>.</div>

<h2 id="jama">JAMA credentials</h2>

The three `JAMA_*` variables enable live baseline fetching for the RTM and test-case endpoints. When unset, those baseline reviews fail at job time with *"PyJama is not installed — JAMA baseline fetching unavailable."*; the hazard Excel upload path needs no JAMA. Set `PYJAMA_TEST_MODE=true` (or per-request `test_mode`) to fetch baselines from the on-disk cache only, with no live API calls — useful for offline/repeatable runs.

## Caching

The review cache is a write-through store on disk, shared by all three reviewers <span class="src">qaai/core/cache.py</span>. Each per-node LLM result is persisted as an append-only, timestamped JSON file at `{CACHE_DIR}/{entity_id}/[{prompt_set}/]{node}_{prompt_version}_{timestamp}.json` (reads select the newest) — one folder per `REQ-*` (test suite), `TEST-*` (test case), or `HAZ-*` (hazard) entity — keyed `review:{entity_id}:{node_name}:{prompt_version}`. Invalidation is version-driven: bump a prompt's version and its key changes, leaving old entries on disk as evidence. Set `ENABLE_CACHE=false` to disable the cache entirely (no manager is created) <span class="src">qaai/api/main.py</span>.

Each run threads a `cache_mode` — `off`, `on` (default; caches interim nodes but always re-runs the final node), or `test` (reuses the final node too; used internally for the hazard reviewer's embedded RTM subgraph). Legacy aliases `partial`/`full` are still accepted and map to `on`/`test`. The API's **"Use cached results"** checkbox (`use_cache`) maps only to `on` (checked) or `off` (unchecked) <span class="src">qaai/api/routes.py</span>.

<div class="note">For the full design — disk layout and payload schema, cache keys, the cache
modes in detail, prompt-set namespacing, and version-driven invalidation — see the
<a href="design/caching.html">Caching design doc</a>.</div>

<h2 id="promptsets">Prompt sets</h2>

Each LLM node renders a versioned Jinja2 template resolved by `PromptConfig` <span class="src">qaai/core/config.py:44-113</span>. Templates live at `qaai/prompts/<role>/<version>/template.jinja2` with a sidecar `meta.yaml`, and are rendered by `render_prompt(path, **vars)` <span class="src">qaai/utils.py</span>. A **prompt set** is a named manifest that pins a version per role, letting you swap a whole bundle at once.

Manifests live in `qaai/prompts/sets/*.yaml`, e.g. the baseline test-suite set `test_suite_reviewer_v3.yaml`:

```
name: test_suite_reviewer_v3
component: test_suite_reviewer
description: |
  Baseline stack (no edge-case analysis).
prompts:
  decomposer: v5.0.0
  summarizer: v4.0.0
  coverage: v8.0.0
  synthesizer: v8.0.0
  design_summarizer: v1.0.0
status: experimental
authored: "2026-06-06"
```

Shipped sets are `test_suite_reviewer_v3`/`v4` (the RTM baseline and edge-case stacks the API toggles between) and `test_case_reviewer_v2`/`v3`; the hazard reviewer runs off `PromptConfig` defaults rather than a named set. List them programmatically with `list_sets(status=...)` <span class="src">qaai/prompts/_registry.py:108-126</span>.

<h3 id="create-set">Create a new prompt set</h3>

1. **Author the templates.** For each role you want to change, add a new version directory with both files: `qaai/prompts/<role>/v<MAJOR.MINOR.PATCH>/template.jinja2 qaai/prompts/<role>/v<MAJOR.MINOR.PATCH>/meta.yaml` Copy an existing `meta.yaml` for the expected fields (role, version, component, status, changelog, target_models). The role's on-disk directory is derived from `PromptConfig`'s default path for that field <span class="src">qaai/prompts/_registry.py:44-57</span> (e.g. role `coverage` → directory `coverage_evaluator`).
2. **Write the manifest.** Create `qaai/prompts/sets/<set_name>.yaml` with `name`, `component`, a `prompts` map of role → version, and `status` (e.g. `experimental` or `production`).
3. **Validate.** `load_set("<set_name>")` <span class="src">qaai/prompts/_registry.py:60-105</span> resolves every role/version and raises `FileNotFoundError` if a template or `meta.yaml` is missing.

```
uv run python -c "from qaai.prompts._registry import load_set; print(load_set('my_set_v1').prompts.keys())"
```

<h3 id="select-set">Select a prompt set at run time</h3>

Set the `PROMPT_SET` environment variable to the manifest name. The `Settings.prompt_config` property lazily resolves it via `PromptConfig.from_set(...)` and caches the result <span class="src">qaai/core/config.py:258-266</span>; when unset, the built-in default `PromptConfig()` versions are used.

```
# .env
PROMPT_SET=test_suite_reviewer_v4

# or per process
PROMPT_SET=test_case_reviewer_v2 uv run uvicorn qaai.api.main:app
```

<div class="note">Because the cache key includes each prompt's version, switching prompt
sets naturally produces fresh cache entries — old results for prior versions remain on
disk as evidence. See <a href="#caching">Caching</a>.</div>

<h3 id="edge-case-toggle">The "Include Edge Case Analysis" toggle</h3>

Beyond the server-wide `PROMPT_SET` env var, the test-suite and hazard endpoints select a prompt set **per request** via the `include_edge_case_analysis` input <span class="src">qaai/api/routes.py, qaai/api/services.py</span>:

<table>
<thead><tr><th>Toggle</th><th>Prompt set</th><th>Decomposer</th></tr></thead>
<tbody>
<tr><td>OFF <em>(default)</em></td><td><code>test_suite_reviewer_v3</code></td><td>v5.0.0 — baseline decomposition</td></tr>
<tr><td>ON</td><td><code>test_suite_reviewer_v4</code></td><td>v6.0.0 — edge-case decomposition (boundary, concurrency, state/mode, degenerate-input, …)</td></tr>
</tbody></table>

Both sets pin the same version for `summarizer`, `coverage`, `synthesizer` and `design_summarizer` — only the decomposer differs. Each reviewer service pre-compiles one graph per set at startup and selects by toggle; the selection also flows into the hazard reviewer's embedded RTM subgraph. Because the two sets would otherwise share cache keys for the unchanged nodes, the prompt-set name is folded into the cache key (see [Caching → Prompt-set namespacing](#caching)). The test-case reviewer does not use this toggle.
