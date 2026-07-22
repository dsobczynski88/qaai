# syntax=docker/dockerfile:1
#
# QAAI production image. Two stages:
#   1. web   — build the Vue SPA (Vite) into qaai/web/dist
#   2. app   — install the Python app with uv and serve it via a SINGLE uvicorn
#              worker (the in-memory JobManager + shared rate limiter require one
#              process — see qaai/api/run.py and qaai/api/jobs.py).
#
# The SPA is served by FastAPI from qaai/web/dist (qaai/api/main.py). Persistent
# state (./shared cache + evidence, ./logs) must be mounted from a volume so it
# survives container restarts — see docs/deployment.md.

# ── Stage 1: build the SPA ──
FROM node:22-slim AS web
WORKDIR /web
COPY qaai/web/package.json qaai/web/package-lock.json* ./
RUN npm ci
COPY qaai/web/ ./
RUN npm run build   # → /web/dist

# ── Stage 2: Python app ──
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS app
WORKDIR /app

# Copy the project and the vendored pyjama path dependency, then install from the
# frozen lockfile. --extra aws pulls boto3 for Secrets Manager / SSM hydration.
COPY . .
COPY --from=web /web/dist ./qaai/web/dist
RUN uv sync --frozen --extra aws

# PROD triggers secret hydration (EnvVariableRetriever); one worker is mandatory.
ENV APP_ENV=PROD \
    QAAI_API_WORKERS=1 \
    QAAI_API_HOST=0.0.0.0 \
    QAAI_API_PORT=8000

EXPOSE 8000

# Long keep-alive so the ALB doesn't drop a slow review's connection. Single worker.
CMD ["uv", "run", "uvicorn", "qaai.api.main:app", \
     "--host", "0.0.0.0", "--port", "8000", "--timeout-keep-alive", "600"]
