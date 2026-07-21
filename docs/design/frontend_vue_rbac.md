# Frontend (Vue 3) & Role-Based Access

<div class="meta">QAAI (qaai) · Vue 3 frontend &amp; RBAC · generated from the codebase 2026-07-20</div>

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
<tr><td>Roles</td><td>admin / reviewer / viewer</td><td><span class="src">qaai/web/src/constants.ts:14</span> · <span class="src">qaai/core/config.py:22</span></td></tr>
</tbody></table>

<div class="note"><strong>Scope.</strong> This work is the Vue conversion plus RBAC
<em>scaffolding</em> — the identity read seam and client-side gating. Server-side
enforcement of roles on the review endpoints is a documented follow-up (see
<a href="#followup">Backend &amp; AWS follow-up</a>).</div>

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

Three roles gate UI actions. The role → action map lives in the SPA and is mirrored on the backend so both sides agree on the role vocabulary. <span class="src">qaai/web/src/constants.ts:14</span> <span class="src">qaai/core/config.py:23</span>

<table>
<thead><tr><th>Role</th><th><code>run_review</code></th><th><code>upload_feedback</code></th><th><code>manage</code></th></tr></thead>
<tbody>
<tr><td><code>admin</code></td><td>✓</td><td>✓</td><td>✓</td></tr>
<tr><td><code>reviewer</code></td><td>✓</td><td>✓</td><td>—</td></tr>
<tr><td><code>viewer</code></td><td>—</td><td>—</td><td>—</td></tr>
</tbody></table>

The `auth` store loads identity once via `GET /api/v1/me` and exposes `hasRole()` / `can(permission)`, the latter resolving against `ROLE_PERMISSIONS`. <span class="src">qaai/web/src/stores/auth.ts:33</span> <span class="src">qaai/web/src/stores/auth.ts:28</span>

Gating is applied in two places:

- **Route guard** — `router.beforeEach` ensures identity is loaded, then redirects to the `unauthorized` route when a route `requiresAuth` and the user is unauthenticated, or lacks a required role. <span class="src">qaai/web/src/router/index.ts:32-41</span>
- **Role-gated components** — `SubmitButton` disables (with an explanatory note) unless `can("run_review")`; `FeedbackUpload` is hidden unless `can("upload_feedback")`. <span class="src">qaai/web/src/components/controls/SubmitButton.vue</span>

<div class="note warn"><strong>Frontend RBAC is UX only, not security.</strong> Route
guards and disabled buttons stop honest users, not the API — a determined client can
still call the review endpoints directly. Real enforcement (per-route role dependencies
and OIDC signature verification) is the follow-up phase below. This matters in the
regulated (IEC 62304) context.</div>

<h2 id="identity">Identity endpoint</h2>

<table>
<thead><tr><th>Method</th><th>Path</th><th>Returns</th></tr></thead>
<tbody>
<tr><td><span class="pill get">GET</span></td><td><code>/api/v1/me</code></td><td><code>{ "user": {id,name,email} | null, "roles": [...] }</code></td></tr>
</tbody></table>

The route delegates to `resolve_identity(request)`. It is **identity-read only** and does not gate the review endpoints. <span class="src">qaai/api/routes.py:53-62</span> <span class="src">qaai/api/identity.py:78</span>

Resolution order:

1. **ALB / OIDC header** — in the target AWS deployment an ALB with an OIDC listener authenticates at the edge and injects a signed JWT in `x-amzn-oidc-data`. The resolver decodes the JWT *payload* and maps the caller's SSO groups to QAAI roles. <span class="src">qaai/api/identity.py:30</span> <span class="src">qaai/api/identity.py:38</span> <span class="src">qaai/api/identity.py:54</span>
2. **Dev fallback** — when no header is present *and* `APP_ENV=DEV`, a configurable dev identity is returned; otherwise it fails closed to `{user: null, roles: []}` so a misconfigured production deploy shows "Access denied" rather than silently granting access. <span class="src">qaai/api/identity.py:94</span>

<div class="note warn"><strong>Signature not verified (yet).</strong> The resolver decodes
the OIDC payload but does <em>not</em> verify its signature against the ALB public key —
that verification, and enforcing roles on the review routes, is the follow-up phase.
<span class="src">qaai/api/identity.py:38-52</span></div>

Group→role mapping uses an explicit configured map, with a name-substring fallback (a group whose name contains `admin`/`reviewer`/`viewer`). Configuration:

<table>
<thead><tr><th>Env var</th><th>Purpose</th><th>Source</th></tr></thead>
<tbody>
<tr><td><code>QAAI_DEV_ROLES</code></td><td>DEV-only fallback roles (comma-separated), default <code>admin</code></td><td><span class="src">qaai/core/config.py:208</span></td></tr>
<tr><td><code>QAAI_DEV_USER</code> / <code>QAAI_DEV_EMAIL</code></td><td>DEV fallback display identity</td><td><span class="src">qaai/core/config.py:206</span></td></tr>
<tr><td><code>QAAI_OIDC_ROLE_MAP</code></td><td>JSON map of SSO group → role, e.g. <code>{"qaai-admins":"admin"}</code></td><td><span class="src">qaai/core/config.py:211</span></td></tr>
<tr><td><code>APP_ENV</code></td><td><code>DEV</code> enables the dev fallback; <code>TEST</code>/<code>PROD</code> require the edge header</td><td><span class="src">qaai/core/config.py:130</span></td></tr>
</tbody></table>

The parsed helpers `settings.dev_roles_list` and `settings.oidc_role_map` validate values against `VALID_ROLES`. <span class="src">qaai/core/config.py:239</span> <span class="src">qaai/core/config.py:248</span>

To exercise gating locally, set `QAAI_DEV_ROLES=viewer` (run buttons disable) or `reviewer` / `admin`.

<h2 id="serving">FastAPI serving</h2>

`create_app()` mounts the built SPA at `/` after the API router (so `/api/v1/*` wins), using `NoCacheStaticFiles`. If `qaai/web/dist` is absent it falls back to the legacy `qaai/api/static/` UI and logs a warning, so Python-only workflows and existing deploys keep working mid-migration. <span class="src">qaai/api/main.py:216-228</span>

```
web_dist = Path(__file__).resolve().parents[1] / "web" / "dist"
legacy_static = Path(__file__).resolve().parent / "static"
ui_dir = str(web_dist) if web_dist.is_dir() else str(legacy_static)
app.mount("/", NoCacheStaticFiles(directory=ui_dir, html=True), name="static")
```

Hash-mode routing keeps every route in the URL fragment, so unknown paths never reach the server — **no SPA fallback route is required**, and deep links work under any proxy prefix. <span class="src">qaai/web/src/router/index.ts:26</span>

<h2 id="followup">Backend &amp; AWS follow-up</h2>

<div class="note warn"><strong>Planned — not yet implemented.</strong> The items below are
the documented next phase (per <code>qaai/web/README.md</code> and the plan); no code for
them exists in the repo yet. <span class="src">qaai/web/README.md</span></div>

- **Backend enforcement.** A FastAPI dependency that *verifies* the ALB OIDC JWT signature against the ALB public key (by `kid`), extracts SSO groups, and a `require_role(...)` dependency on the review / feedback routes.
- **Credentialed CORS revisit.** Today CORS middleware is only added when `ALLOWED_ORIGINS` is set to explicit origins (with `allow_credentials=True`); the default `"*"` adds no CORS middleware. Credentialed CORS cannot use `"*"`, so this must be revisited when cookie/token auth is enforced. <span class="src">qaai/api/main.py:191-199</span>
- **AWS infrastructure.** ALB + OIDC listener (Cognito or corporate IdP), an ECS/Fargate task, and a Dockerfile that runs `npm run build` then serves via uvicorn/gunicorn. Reuse the existing secret loader (`qaai/core/secrets.py`, `APP_ENV`). <span class="src">qaai/core/secrets.py</span>

<div class="note warn"><strong>Run a single worker.</strong> The job registry is an
in-memory <code>JobManager</code>, so the API must run with a single worker; horizontal
scaling on ECS would need a shared job store. <span class="src">qaai/api/jobs.py</span></div>
