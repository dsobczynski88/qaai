# Frontend (Vue 3) & Role-Based Access

<div class="meta">QAAI (qaai) · Vue 3 frontend &amp; RBAC · generated from the codebase 2026-07-21</div>

## Overview

The interactive reviewer UI is a **Vue 3 + Vite single-page app** under `qaai/web/`, built to `qaai/web/dist/` and served by FastAPI at `/`. It replaces the former vanilla `qaai/api/static/` page (kept as a build-less fallback) and adds a **role-based access-control** layer. <span class="src">qaai/api/main.py:216-228</span>

The backend endpoint contract and request payload field names are preserved exactly — only the UI and the static mount changed. Two behaviours were ported verbatim: the proxy base-path detection and the async job engine (below).

<div class="note warn"><strong><code>qaai/api/static/</code> is deprecated — do not add features
to it.</strong> The 1,644-line legacy page is separately maintained and does not track this SPA;
in particular it has <strong>no RBAC layer at all</strong> — it never calls
<code>/api/v1/me</code> and its only <code>role</code> attributes are ARIA
(<code>role="button"</code>). It exists solely so an unbuilt checkout still serves a working UI.
The fix for a missing UI is <code>npm run build</code> in <code>qaai/web</code>, not an edit to
<code>static/</code>.</div>

<table>
<thead><tr><th>Concern</th><th>Choice</th><th>Where</th></tr></thead>
<tbody>
<tr><td>Framework</td><td>Vue 3 (SFC, <code>&lt;script setup&gt;</code>)</td><td><code>qaai/web/src/*.vue</code></td></tr>
<tr><td>Build</td><td>Vite (<code>base: "./"</code>)</td><td><span class="src">qaai/web/vite.config.ts:10</span></td></tr>
<tr><td>Routing</td><td>Vue Router, <strong>hash mode</strong></td><td><span class="src">qaai/web/src/router/index.ts:26</span></td></tr>
<tr><td>State</td><td>Pinia stores (<code>auth</code>, <code>job</code>)</td><td><code>qaai/web/src/stores/</code></td></tr>
<tr><td>Roles</td><td>admin / user</td><td><span class="src">qaai/web/src/constants.ts:15</span> · <span class="src">qaai/core/config.py:28</span></td></tr>
</tbody></table>

<div class="note"><strong>Scope.</strong> The client-side gating below is a UX
convenience. Roles are now <strong>enforced server-side</strong> too — a FastAPI
dependency on every review/jobs/feedback/usage route (<code>qaai/api/authz.py</code>),
and the ALB OIDC JWT signature is verified before any claim is trusted
(<code>qaai/api/identity.py</code>). See <a href="#followup">Server-side enforcement &amp;
AWS deployment</a> and the <a href="../deployment.html">Deployment guide</a>.</div>

<h2 id="layout">SPA layout &amp; build</h2>

Source lives under `qaai/web/src/`; a production build lands in `qaai/web/dist/`, which is what FastAPI mounts.

```
qaai/web/
  package.json         vite · vue · vue-router · pinia
  vite.config.ts       base:'./' · dev proxy → :8000
  index.html           mounts #app
  src/
    main.ts            createApp + pinia + router + global styles
    App.vue            shell: AppHeader / <router-view> / AppFooter
    router/index.ts    hash router + role guard
    api/{client,reviews}.ts
    stores/{auth,job}.ts
    constants.ts  types.ts
    views/{ReviewHome,Unauthorized}.vue
    components/  ReviewerSelector, ReviewerCard, forms/*, controls/*, status/*
    styles/{tokens,base,forms}.css
  dist/                built output → served by FastAPI
```

`base: "./"` makes every built asset URL **relative** to `index.html`, so the SPA works under any proxy prefix (local `/`, JupyterHub `/user/x/proxy/8000/`, or an AWS ALB path) without hardcoding it. <span class="src">qaai/web/vite.config.ts:5-10</span>

Build once, then start the API:

```
cd qaai/web
npm install
npm run build          # → qaai/web/dist
uv run uvicorn qaai.api.main:app --reload
```

For UI development with hot reload, run the Vite dev server alongside the API; it proxies backend paths to port 8000:

```
npm run dev            # http://localhost:5173  (proxies /api → :8000)
```

<span class="src">qaai/web/vite.config.ts:22-27</span> · `npm run typecheck` runs `vue-tsc` (the `build` script transpiles via esbuild and does not type-check).

<h2 id="job-flow">Async job engine</h2>

Reviews run as background jobs so every HTTP request stays sub-second and the upstream proxy never idles out. The client submits, then polls a fast status endpoint and downloads the report when done — ported into the `job` Pinia store. <span class="src">qaai/web/src/stores/job.ts:101</span>

<pre class="diagram"><code>POST /api/v1/{test-suite|test-case|hazard-risk}-review   → 202 { job_id }
      │
      ▼   every POLL_INTERVAL_MS (4000 ms), until MAX_POLL_MS (4 hr)
GET  /api/v1/jobs/{job_id}          → { status, total, done, succeeded, failed,
      │                                  eta_seconds, messages[] }
      │  status == "completed"
      ▼
GET  /api/v1/jobs/{job_id}/result   → HTML report (Blob)  → Download Report
      ▲
      └── POST /api/v1/jobs/{job_id}/cancel                (Stop Run)</code></pre>

<span class="src">qaai/web/src/constants.ts:7-8</span> defines `POLL_INTERVAL_MS = 4000` and `MAX_POLL_MS = 4 * 60 * 60 * 1000` (4 hours — long enough for a large baseline batch). The store exposes reactive `phase` (`idle | loading | done | error`), progress counts, the result object URL, and the error string; the status components bind to these instead of mutating the DOM.

The old global `pollToken` is replaced by a monotonic **generation counter plus an `AbortController`**: `bumpGeneration()` increments the token and aborts the in-flight fetch, so a superseded poll loop returns quietly. Switching reviewers calls `cancelSilently()`; **Stop Run** calls `stop()`, which cancels the job server-side. <span class="src">qaai/web/src/stores/job.ts:86</span> <span class="src">qaai/web/src/stores/job.ts:95</span> <span class="src">qaai/web/src/stores/job.ts:166</span>

<h2 id="controls">Run controls &amp; options</h2>

Each reviewer form is a thin Vue component that binds a few reusable controls to reactive refs and posts them under the exact request field names — the payload contract is unchanged from the old vanilla UI. The controls live in `qaai/web/src/components/controls/` and the forms in `qaai/web/src/components/forms/`.

<table>
<thead><tr><th>Control</th><th>Renders</th><th>Source</th></tr></thead>
<tbody>
<tr><td><code>TextField</code></td><td>A labelled text input (baseline id, project name, sheet, prefix)</td><td><span class="src">qaai/web/src/components/controls/TextField.vue</span></td></tr>
<tr><td><code>CacheModeRadio</code></td><td>The three-way <code>on</code>/<code>test</code>/<code>off</code> cache-mode radio</td><td><span class="src">qaai/web/src/components/controls/CacheModeRadio.vue:8-12</span></td></tr>
<tr><td><code>LabeledCheckbox</code></td><td>A checkbox + label for each boolean toggle</td><td><span class="src">qaai/web/src/components/controls/LabeledCheckbox.vue</span></td></tr>
<tr><td><code>FileDropzone</code></td><td>The SHA Excel upload (hazard only)</td><td><span class="src">qaai/web/src/components/controls/FileDropzone.vue</span></td></tr>
<tr><td><code>BaselineReviewTypeRadio</code></td><td>The <code>requirements</code>/<code>tests</code> radio backing <code>baseline_review_type</code> (test-suite only)</td><td><span class="src">qaai/web/src/components/controls/BaselineReviewTypeRadio.vue</span></td></tr>
<tr><td><code>InfoTooltip</code></td><td>The per-option help copy (from <code>TOOLTIPS</code>)</td><td><span class="src">qaai/web/src/constants.ts:23-42</span></td></tr>
<tr><td><code>SubmitButton</code></td><td>The role-gated <strong>Run</strong> button (see <a href="#rbac">RBAC</a>)</td><td><span class="src">qaai/web/src/components/controls/SubmitButton.vue</span></td></tr>
</tbody></table>

Which controls each form shows, and the request field each one sets:

<table>
<thead><tr><th>Form</th><th>Controls → request field</th><th>Source</th></tr></thead>
<tbody>
<tr><td><code>RtmForm</code><br>(test-suite)</td><td><code>baseline_id</code>, <code>cache_mode</code>, <code>test_mode</code>, <code>include_edge_case_analysis</code>, <code>include_design_summaries</code> (<code>include_decomposition_analysis</code> is sent hard-coded <code>true</code>)</td><td><span class="src">qaai/web/src/components/forms/RtmForm.vue:26-73</span></td></tr>
<tr><td><code>TcForm</code><br>(test-case)</td><td><code>baseline_id</code>, <code>cache_mode</code>, <code>test_mode</code>, <code>include_decomposition_analysis</code> (edge-case &amp; design-summaries sent hard-coded <code>false</code>)</td><td><span class="src">qaai/web/src/components/forms/TcForm.vue:27-38</span></td></tr>
<tr><td><code>HazardForm</code><br>(hazard)</td><td><code>project_name</code>, <code>file</code>, <code>sheet_name</code>, <code>identifier_pattern</code>, <code>cache_mode</code>, <code>test_mode</code>, <code>include_edge_case_analysis</code>, <code>include_design_summaries</code></td><td><span class="src">qaai/web/src/components/forms/HazardForm.vue:36-44</span></td></tr>
</tbody></table>

<div class="note"><strong>What each option means lives in one place.</strong> These forms only
<em>bind</em> the controls; for what every option does, its values, default, and which reviewer it
affects — identical whether set here or via <code>curl</code> — see
<a href="../review_options.html">Review options &amp; toggles</a>. The
<a href="../api.html#baseline">API guide</a> documents the request bodies.</div>

<h2 id="client">API client &amp; proxy base</h2>

All requests flow through one client module. `detectRootPath()` is ported verbatim from the original static UI: it derives the JupyterHub/VSCode proxy prefix from `window.location.pathname` (or `""` for local/direct access), and every request is prefixed with the resulting `ROOT_PATH`. <span class="src">qaai/web/src/api/client.ts:12</span> <span class="src">qaai/web/src/api/client.ts:24</span>

<table>
<thead><tr><th>Export</th><th>Role</th><th>Source</th></tr></thead>
<tbody>
<tr><td><code>apiFetch(path, opts)</code></td><td><code>fetch()</code> that prefixes <code>ROOT_PATH</code> and injects auth headers</td><td><span class="src">qaai/web/src/api/client.ts:46</span></td></tr>
<tr><td><code>authHeaders()</code></td><td>auth-header seam — empty for ALB/OIDC (cookie is sent same-origin); the single place to add a Bearer token if swapped to Cognito</td><td><span class="src">qaai/web/src/api/client.ts:41</span></td></tr>
<tr><td><code>parseErr(resp)</code></td><td>uniform <code>"status: detail"</code> error extraction</td><td><span class="src">qaai/web/src/api/client.ts:52</span></td></tr>
</tbody></table>

<div class="note"><strong>Runtime base vs asset base.</strong> Built <em>assets</em> load
via Vite's relative <code>base: "./"</code>; runtime <em>API calls</em> are prefixed
separately by <code>detectRootPath()</code>. The two mechanisms together keep the SPA
portable across proxy prefixes.</div>

<h2 id="rbac">RBAC model</h2>

Two roles gate UI actions. The role → action map lives in the SPA and is **mirrored on the backend** (`PERMISSIONS_BY_ROLE`) so both sides agree on the vocabulary — and the backend map is the real gate. <span class="src">qaai/web/src/constants.ts:15</span> <span class="src">qaai/api/authz.py:27</span> <span class="src">qaai/core/config.py:28</span>

<table>
<thead><tr><th>Role</th><th><code>run_review</code></th><th><code>upload_feedback</code></th><th><code>manage</code></th></tr></thead>
<tbody>
<tr><td><code>admin</code></td><td>✓</td><td>✓</td><td>✓</td></tr>
<tr><td><code>user</code></td><td>✓</td><td>✓</td><td>—</td></tr>
</tbody></table>

<div class="note"><strong>Collapsed from three roles to two.</strong> The earlier
<code>admin</code>/<code>reviewer</code>/<code>viewer</code> model became
<code>admin</code>/<code>user</code> for the AWS deployment: a <code>user</code> runs
reviews, polls/downloads jobs, and uploads feedback; <code>admin</code> additionally holds
<code>manage</code> (the <code>GET /usage</code> monitoring endpoint). Old AD groups mapping
to <code>reviewer</code> become <code>user</code>; <code>viewer</code> maps to no role
(no access).</div>

The `auth` store loads identity once via `GET /api/v1/me` and exposes `hasRole()` / `can(permission)`, the latter resolving against `ROLE_PERMISSIONS`. <span class="src">qaai/web/src/stores/auth.ts:33</span> <span class="src">qaai/web/src/stores/auth.ts:28</span>

Gating is applied in two places:

- **Route guard** — `router.beforeEach` ensures identity is loaded, then redirects to the `unauthorized` route when a route `requiresAuth` and the user is unauthenticated, or lacks a required role. <span class="src">qaai/web/src/router/index.ts:32-41</span>
- **Role-gated components** — `SubmitButton` disables (with an explanatory note) unless `can("run_review")`; `FeedbackUpload` is hidden unless `can("upload_feedback")`. <span class="src">qaai/web/src/components/controls/SubmitButton.vue</span>

<div class="note"><strong>Frontend RBAC is UX only — but the API is now enforced too.</strong>
Route guards and disabled buttons stop honest users; a determined client that calls the
review endpoints directly is stopped by the server-side dependency
(<code>qaai/api/authz.py</code>): <strong>401</strong> when unauthenticated,
<strong>403</strong> when authenticated but lacking the permission. The frontend and
backend permission maps are kept in sync deliberately. <span class="src">qaai/api/authz.py:48-58</span></div>

<h2 id="identity">Identity endpoint</h2>

<table>
<thead><tr><th>Method</th><th>Path</th><th>Returns</th></tr></thead>
<tbody>
<tr><td><span class="pill get">GET</span></td><td><code>/api/v1/me</code></td><td><code>{ "user": {id,name,email} | null, "roles": [...] }</code></td></tr>
</tbody></table>

The route delegates to `resolve_identity(request)`. `GET /me` itself is **identity-read only** and public; the *other* review/jobs/feedback/usage routes are gated by the `qaai/api/authz.py` dependency built on the same resolver. <span class="src">qaai/api/routes.py:53-63</span> <span class="src">qaai/api/identity.py:141</span>

Resolution order:

1. **ALB / OIDC header** — in the AWS deployment an ALB with an OIDC listener authenticates at the edge and injects a signed JWT in `x-amzn-oidc-data`. Outside DEV the resolver **verifies the JWT signature** (see below) before reading the claims and mapping the caller's SSO/AD groups to QAAI roles. <span class="src">qaai/api/identity.py:33</span> <span class="src">qaai/api/identity.py:141-157</span> <span class="src">qaai/api/identity.py:117</span>
2. **Dev fallback** — when no header is present *and* `APP_ENV=DEV`, a configurable dev identity is returned; otherwise it fails closed to `{user: null, roles: []}` so a misconfigured production deploy shows "Access denied" rather than silently granting access. <span class="src">qaai/api/identity.py:161-173</span>

<div class="note"><strong>Signature verification (ES256).</strong> In PROD/TEST the resolver
calls <code>_verify_oidc_claims</code>: it reads the JWT's unverified header for the
<code>kid</code>, fetches the matching ALB public key from
<code>https://public-keys.auth.elb.&lt;region&gt;.amazonaws.com/&lt;kid&gt;</code> (cached
per-process), and <code>jwt.decode(..., algorithms=["ES256"])</code> — so a forged
<code>x-amzn-oidc-data</code> header is rejected. Any failure (bad signature, expired token,
unknown key, missing <code>ALB_OIDC_REGION</code>) returns no claims → no roles (fails
closed). Verification is skipped only in DEV, where there is typically no ALB in front.
<span class="src">qaai/api/identity.py:78-114</span></div>

Group→role mapping uses an explicit configured map, with a name-substring fallback (a group whose name contains `admin`/`user`). Configuration:

<table>
<thead><tr><th>Env var</th><th>Purpose</th><th>Source</th></tr></thead>
<tbody>
<tr><td><code>QAAI_DEV_ROLES</code></td><td>DEV-only fallback roles (comma-separated), default <code>admin</code></td><td><span class="src">qaai/core/config.py:232</span></td></tr>
<tr><td><code>QAAI_DEV_USER</code> / <code>QAAI_DEV_EMAIL</code></td><td>DEV fallback display identity</td><td><span class="src">qaai/core/config.py:230-231</span></td></tr>
<tr><td><code>QAAI_OIDC_ROLE_MAP</code></td><td>JSON map of SSO/AD group → role, e.g. <code>{"qaai-admins":"admin","qaai-users":"user"}</code></td><td><span class="src">qaai/core/config.py:235</span></td></tr>
<tr><td><code>ALB_OIDC_REGION</code></td><td>AWS region hosting the ALB OIDC signing keys (builds the public-key URL for verification)</td><td><span class="src">qaai/core/config.py:240</span></td></tr>
<tr><td><code>QAAI_VERIFY_OIDC_SIGNATURE</code></td><td>Verify the JWT signature (default <code>true</code>); skipped in DEV. Never set false in PROD</td><td><span class="src">qaai/core/config.py:244</span></td></tr>
<tr><td><code>APP_ENV</code></td><td><code>DEV</code> enables the dev fallback + skips verification; <code>TEST</code>/<code>PROD</code> require the edge header and verify it</td><td><span class="src">qaai/core/config.py:142</span></td></tr>
</tbody></table>

The parsed helpers `settings.dev_roles_list` and `settings.oidc_role_map` validate values against `VALID_ROLES` (so stale `reviewer`/`viewer` map entries self-drop). <span class="src">qaai/core/config.py:266</span> <span class="src">qaai/core/config.py:275</span>

To exercise gating locally, set `QAAI_DEV_ROLES=user` (feedback upload hides, `GET /usage` returns 403) or `admin`.

<h2 id="serving">FastAPI serving</h2>

`create_app()` mounts the built SPA at `/` after the API router (so `/api/v1/*` wins), using `NoCacheStaticFiles`. If `qaai/web/dist` is absent it falls back to the legacy `qaai/api/static/` UI and logs a warning, so Python-only workflows and existing deploys keep working mid-migration. <span class="src">qaai/api/main.py:216-228</span>

```
web_dist = Path(__file__).resolve().parents[1] / "web" / "dist"
legacy_static = Path(__file__).resolve().parent / "static"
ui_dir = str(web_dist) if web_dist.is_dir() else str(legacy_static)
app.mount("/", NoCacheStaticFiles(directory=ui_dir, html=True), name="static")
```

Hash-mode routing keeps every route in the URL fragment, so unknown paths never reach the server — **no SPA fallback route is required**, and deep links work under any proxy prefix. <span class="src">qaai/web/src/router/index.ts:26</span>

<h2 id="followup">Server-side enforcement &amp; AWS deployment</h2>

<div class="note"><strong>Implemented.</strong> The backend enforcement and signature
verification that were previously scaffolding are now shipped. The full reproducible
deployment path (container, secrets, ALB config, smoke test) lives in the
<a href="../deployment.html">Deployment guide</a>.</div>

- **Backend enforcement.** `qaai/api/authz.py` provides `require_permission(...)` (and the pre-built `require_run_review` / `require_upload_feedback` / `require_manage` guards) mounted via `Depends(...)` on every review, jobs, feedback, and usage route in `qaai/api/routes.py`. It fails closed: **401** unauthenticated, **403** authenticated-but-lacking. <span class="src">qaai/api/authz.py:41-66</span> <span class="src">qaai/api/routes.py:9</span>
- **OIDC signature verification.** `resolve_identity` verifies the ALB JWT's ES256 signature against the `kid`-matched ALB public key before trusting any claim (see [Identity endpoint](#identity)). `PyJWT[crypto]` is a runtime dependency. <span class="src">qaai/api/identity.py:78-114</span>
- **Feedback-upload validation.** Because a `user` may upload, `POST /feedback-upload` validates the parsed body against the exported-feedback shape (`{record_key: {rating, notes, saved_at}}`) and returns **422** on a mismatch rather than storing an arbitrary file. <span class="src">qaai/api/routes.py:285-305</span>
- **Deployment artifacts.** A multi-stage `Dockerfile` (Node stage builds the SPA, Python stage runs a single uvicorn worker) and `docs/deployment.md` cover the AWS run. Secrets hydrate via `qaai/core/secrets.py` when `APP_ENV=PROD`. <span class="src">Dockerfile</span> <span class="src">qaai/core/secrets.py</span>
- **Credentialed CORS (still a revisit).** CORS middleware is only added when `ALLOWED_ORIGINS` is set to explicit origins (with `allow_credentials=True`); the default `"*"` adds none. Not needed under same-origin ALB serving, but revisit if a cross-origin credentialed client is ever added. <span class="src">qaai/api/main.py:191-199</span>

<div class="note warn"><strong>Run a single worker.</strong> The job registry is an
in-memory <code>JobManager</code> and the RPM/TPM rate limiter is per-process, so the API
must run with a single uvicorn worker; horizontal scaling would need a shared job store.
Concurrency across ~10 users is handled <em>within</em> the one worker via the
<code>current_run_dir</code> contextvar. <span class="src">qaai/api/jobs.py</span> <span class="src">qaai/api/run.py</span></div>
