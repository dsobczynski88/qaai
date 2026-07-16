# QAAI Web (Vue 3 SPA)

The interactive reviewer UI, a **Vue 3 + Vite** single-page app. It replaces the old
vanilla `qaai/api/static/` page and is **RBAC-ready** (auth store, route guards,
role-gated components) for the AWS deployment. Built output (`dist/`) is served by
FastAPI at `/`.

## Prerequisites

- Node 20.19+ / 22.12+ (Node 24 works), npm 9+.

## Develop

Two servers side by side — FastAPI for the API, Vite for the UI with hot reload:

```bash
# terminal 1 — backend (from repo root)
uv run uvicorn qaai.api.main:app --reload      # http://localhost:8000

# terminal 2 — frontend (from qaai/web)
npm install
npm run dev                                    # http://localhost:5173
```

`vite.config.ts` proxies `/api`, `/guide`, `/docs`, `/redoc` from :5173 → :8000, so the
dev UI talks to the real backend.

## Build (what production serves)

```bash
cd qaai/web
npm run build          # → qaai/web/dist
```

FastAPI (`qaai/api/main.py`) mounts `qaai/web/dist` at `/`. If `dist/` is absent it
falls back to the legacy `qaai/api/static/` UI and logs a warning — so the backend
never breaks if the SPA hasn't been built yet.

`npm run typecheck` runs `vue-tsc` (not part of `build`, which uses esbuild).

## Running on JupyterHub

**Do not use `npm run dev` / port 5173 on the Hub.** The Vite dev server binds to
`127.0.0.1`, so the JupyterHub proxy (which reaches services at
`/user/<you>/proxy/<port>/`) can't connect to it — you'll get
`connect ECONNREFUSED 0.0.0.0:5173`.

Instead, serve the **built** SPA from FastAPI on port 8000 (already proxied by the
Hub with the correct `--root-path`). `scripts/startup.sh` builds `dist/` for you if
it's missing, then launches the server:

```bash
bash scripts/startup.sh                 # builds dist/ if absent, then starts uvicorn
```

Open the `…/user/<you>/proxy/8000/` URL the script prints (not 5173). After pulling
UI changes, force a fresh build:

```bash
bash scripts/startup.sh --rebuild-web   # rebuild dist/ even if it already exists
```

The SPA handles the proxy prefix automatically (`base: "./"` + `detectRootPath()`),
so no per-deployment path config is needed.

## Architecture

```
src/
  main.ts              app bootstrap (pinia + router + global styles)
  App.vue              shell: AppHeader / <router-view> / AppFooter
  router/index.ts      hash-mode router + role guard (beforeEach)
  api/
    client.ts          ROOT_PATH (detectRootPath — proxy base), fetch wrapper, auth-header seam
    reviews.ts         submit / poll / result / cancel / feedback-upload
  stores/
    auth.ts            identity + roles (GET /api/v1/me); hasRole() / can()
    job.ts             async review engine: 202 → poll → download-blob; AbortController cancel
  constants.ts         POLL_INTERVAL_MS, MAX_POLL_MS, ROLE_PERMISSIONS, tooltip copy
  types.ts             Role, Permission, JobStatus, BaselineReviewRequest, …
  views/               ReviewHome, Unauthorized
  components/          ReviewerSelector, ReviewerCard, forms/*, controls/*, status/*
  styles/              tokens.css (theme vars) · base.css (reset/layout/keyframes) · forms.css (form primitives)
```

The backend API contract is preserved exactly — same endpoints, same request payload
field names. Two things ported verbatim: `detectRootPath()` (proxy base-path detection)
and the poll engine (`POLL_INTERVAL_MS`/`MAX_POLL_MS`).

## RBAC

Roles: **admin** (everything) · **reviewer** (run reviews + download + upload feedback)
· **viewer** (view/download only — cannot start runs). The role → action map lives in
`src/constants.ts` (`ROLE_PERMISSIONS`) and is mirrored server-side in
`qaai/core/config.py` (`VALID_ROLES`).

Identity comes from `GET /api/v1/me` (`qaai/api/identity.py`):

- **Production (ALB + OIDC):** the ALB authenticates at the edge and injects a signed
  JWT in `x-amzn-oidc-data`; the endpoint reads the claims and maps SSO groups → roles
  (`QAAI_OIDC_ROLE_MAP`, or a name-substring fallback).
- **Local dev:** with `APP_ENV=DEV` and no header, a configurable dev identity is
  returned. Set `QAAI_DEV_ROLES=viewer` (or `reviewer` / `admin`) to exercise gating.
  Outside DEV, a missing header fails closed (unauthenticated → "Access denied").

> **⚠ Frontend RBAC is UX only, not security.** Route guards and disabled buttons stop
> honest users, not the API. Real enforcement — verifying the ALB OIDC JWT **signature**
> and adding per-route role dependencies on the review endpoints — is the RBAC
> **follow-up phase**. Do not treat the client checks as an access boundary.

### Relevant env vars

| Var | Purpose |
|-----|---------|
| `QAAI_DEV_ROLES` | DEV-only fallback roles (comma-separated), default `admin` |
| `QAAI_DEV_USER` / `QAAI_DEV_EMAIL` | DEV fallback display identity |
| `QAAI_OIDC_ROLE_MAP` | JSON map of SSO group → role, e.g. `{"qaai-admins":"admin"}` |
| `APP_ENV` | `DEV` enables the dev fallback; `TEST`/`PROD` require the edge header |
