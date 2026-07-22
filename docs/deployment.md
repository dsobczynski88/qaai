# QAAI Production Deployment

<div class="meta">QAAI (qaai) · production deployment · generated from the codebase 2026-07-21</div>

This is the reproducible path to run QAAI on a single AWS instance behind an
Application Load Balancer (ALB) that authenticates users at the edge against an
Active Directory (AD) security group via OIDC. For how the roles are enforced in the
API and SPA, see the [Frontend &amp; RBAC design](design/frontend_vue_rbac.html); for
the endpoint contract, the [API guide](api.html).

## Architecture in one picture

```
Browser ──HTTPS──▶ ALB (OIDC listener, AD group)
                     │  injects signed JWT: x-amzn-oidc-data
                     ▼
              QAAI container (single uvicorn worker, port 8000)
                     │  verifies the JWT signature, maps AD groups → roles,
                     │  enforces per-route permissions
                     ▼
              EBS volume: /app/shared  (cache + regulatory evidence)
                          /app/logs    (run logs, telemetry)
```

Authentication is handled by the ALB — QAAI stores **no passwords and has no user
database**. It reads the identity the ALB proves and enforces what each role may do.

## Roles and permissions

Two roles (see `VALID_ROLES` in `qaai/core/config.py`, `PERMISSIONS_BY_ROLE` in
`qaai/api/authz.py`, and `ROLE_PERMISSIONS` in `qaai/web/src/constants.ts` — keep
the three in sync):

| Role  | Permissions | Can do |
|-------|-------------|--------|
| admin | run_review, upload_feedback, manage | Everything, incl. `GET /usage` monitoring |
| user  | run_review, upload_feedback | Run reviews, poll/download jobs, upload feedback |

Endpoint gating (enforced server-side via `Depends(...)` in `qaai/api/routes.py`):

| Endpoint | admin | user | unauthenticated |
|---|---|---|---|
| `GET /health`, `GET /me` | ✓ | ✓ | ✓ (public) |
| `POST /test-suite-review` / `test-case-review` / `hazard-risk-review` | ✓ | ✓ | 401 |
| `GET/POST /jobs/*` | ✓ | ✓ | 401 |
| `POST /feedback-upload` | ✓ | ✓ | 401 |
| `GET /usage` | ✓ | 403 | 401 |

## Environment variables (PROD)

Set `APP_ENV=PROD` so `Settings.__init__` hydrates secrets from the AWS store
(`EnvVariableRetriever`, `qaai/core/secrets.py`) before validation.

| Variable | Purpose |
|---|---|
| `APP_ENV=PROD` | Enables secret hydration + OIDC signature verification |
| `API_KEY`, `API_BASE_URL`, `API_MODEL` | LLM endpoint (one endpoint, served model) |
| `QAAI_OIDC_ROLE_MAP` | JSON: AD group → role, e.g. `{"qaai-admins":"admin","qaai-users":"user"}` |
| `ALB_OIDC_REGION` | AWS region hosting the ALB OIDC signing keys (for signature verification) |
| `QAAI_VERIFY_OIDC_SIGNATURE` | Leave unset/`true`. **Never set false in PROD** |
| `QAAI_API_WORKERS=1` | Mandatory — see below |
| `REDIS_URL` | Optional Tier-2 cache; disk is authoritative |
| `JAMA_*` | JAMA credentials if fetching live baselines |

Deliver `API_KEY`, `QAAI_OIDC_ROLE_MAP`, and `JAMA_*` via Secrets Manager / SSM,
not the image.

## Single worker — mandatory

Keep `QAAI_API_WORKERS=1`. The in-memory `JobManager` (`qaai/api/jobs.py`) and the
one shared RPM/TPM rate limiter both assume a single process; multiple workers
fragment job tracking (a poll can hit a worker that never saw the job) and multiply
outbound LLM load. Concurrency across ~10 users is already handled **within** the one
worker via the `current_run_dir` contextvar (isolated run folders, no lock) — adding
workers is the wrong lever. `qaai/api/run.py` logs a loud warning if workers > 1.

## Build and run

```bash
docker build -t qaai:latest .
docker run -d --name qaai -p 8000:8000 \
  --env-file prod.env \
  -v /mnt/qaai-shared:/app/shared \
  -v /mnt/qaai-logs:/app/logs \
  qaai:latest
```

The image (`Dockerfile`) builds the Vue SPA in a Node stage, installs the Python app
with `uv sync --frozen --extra aws`, and serves a single uvicorn worker with
`--timeout-keep-alive 600`.

### Persistent storage

Mount an EBS volume for **both**:
- `/app/shared` — the reviewer cache **and regulatory evidence trail** (immutable,
  timestamped node results + uploaded feedback). This must survive restarts.
- `/app/logs` — per-run logs and telemetry.

## ALB configuration (infra, not code)

1. HTTPS listener with an **authenticate-oidc** action bound to your IdP and the AD
   security group; forward to the QAAI target group on port 8000.
2. Ensure the ALB passes the `x-amzn-oidc-data` header through to the target.
3. Set the target group / idle timeout **≥ the longest review runtime** so a slow
   review isn't dropped (pair with the container's 600s keep-alive).
4. Confirm `ALB_OIDC_REGION` matches the ALB's region so signature verification can
   fetch the signing public key.

## Smoke test after deploy

```bash
curl -f https://<host>/api/v1/health           # 200 healthy
# Through the ALB (authenticated), GET /api/v1/me should return your roles.
# A direct request WITHOUT the ALB header must fail closed (no dev identity in PROD).
```
