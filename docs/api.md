# API Server & Frontend Guide

## Starting the Development Server

### Quick Start

```bash
uv run uvicorn autoqa.api.main:app --reload
```

**Expected Output:**
```
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
```

Look for these log lines confirming services initialized:
```
AutoQA services initialized successfully
AutoQA API created (environment: development)
```

The `--reload` flag automatically restarts the server when files change (development only).

---

## Opening the Frontend

Navigate to **http://localhost:8000** in your browser.

The `index.html` is served from `autoqa/api/static/` via FastAPI's StaticFiles mount.

### What to Verify

- Dark grid-background page loads correctly
- Fonts (Syne display headers, JetBrains Mono for inputs) render properly
- Three animated reviewer cards appear:
  1. **Requirement Coverage** (RTM review)
  2. **Test Case Adequacy** (Test case review)
  3. **Software Hazard Analysis** (Hazard review)
- Clicking a card highlights it and fades in the corresponding input form
- API documentation accessible at http://localhost:8000/docs

---

## Testing Endpoints

### Health Check

```bash
curl http://localhost:8000/api/v1/health
```

**Expected Response:**
```json
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

---

## The Asynchronous Job Model

A review can take several minutes. If the report were returned synchronously, an upstream
proxy (JupyterHub's jupyter-server-proxy, AWS ALB, etc.) would see an idle upstream for the
whole run and return a **504** to the browser. To avoid this, the three review endpoints run
the work as a **background job**:

1. **Submit** — `POST` a review endpoint. It returns `202 Accepted` with `{"job_id": "...", "status": "pending"}` in well under a second.
2. **Poll** — `GET /api/v1/jobs/{job_id}` returns `{job_id, status, filename, error}`. `status` is one of `pending`, `running`, `completed`, `failed`. Repeat until terminal (`completed` / `failed`).
3. **Download** — `GET /api/v1/jobs/{job_id}/result` returns the HTML report (`200`) when the job is `completed`. It returns `404` for an unknown id, `425 Too Early` while still pending/running, and the job's failure status (`400` for bad input, `500` otherwise) when it failed.

Jobs live in an **in-memory** registry (`autoqa/api/jobs.py`, most-recent 200 retained) and run
one at a time, which assumes a **single uvicorn worker** (see [Production Deployment](#production-deployment)).
The frontend performs the submit → poll → download loop automatically (polling every ~4s and
showing elapsed time).

---

## Testing the Hazard Upload Endpoint

The hazard reviewer works with just an Excel file—no JAMA credentials required.

### Via curl

```bash
# 1. Submit the job → 202 {"job_id": "...", "status": "pending"}
JOB=$(curl -s -X POST http://localhost:8000/api/v1/hazard-risk-review \
  -F "project_name=Test Project" \
  -F "file=@tests/fixtures/external/software_hazard_analysis.xlsx" \
  -F "sheet_name=SHA Table" \
  -F "use_cache=true" | jq -r .job_id)

# 2. Poll until "status" is "completed"
curl -s http://localhost:8000/api/v1/jobs/$JOB

# 3. Download the completed report
curl -s http://localhost:8000/api/v1/jobs/$JOB/result --output autoqa_hazard_review.html
```

The multipart form fields are `project_name` (required), `file` (required, `.xlsx`/`.xls`),
`sheet_name` (default `SHA Table`), `use_cache` (default `true` — partial caching; set `false`
to recompute from scratch), and `test_mode` (optional — cache-only JAMA; omit to use the
server's `PYJAMA_TEST_MODE` default). The per-row thread ID is derived from the job ID server-side.

Then open the downloaded `autoqa_hazard_review.html` in a browser to see the viewer.

### Via the Frontend UI

1. Click the **"Software Hazard Analysis"** card
2. Enter a project name (e.g., "Test Project")
3. Drag and drop `tests/fixtures/external/software_hazard_analysis.xlsx` onto the upload zone
4. Leave **"Use cached results"** checked (or uncheck to recompute from scratch)
5. Click **"Run Review"** → the page submits the job and polls for status, showing elapsed time
6. Once the job completes, click the download link to save the HTML viewer

---

## Testing Baseline Reviews (RTM & Test Case)

RTM baseline and test case baseline reviews require JAMA credentials to be configured
in your `.env` file:

```env
JAMA_HOST_ADDRESS=<your_jama_host>
JAMA_CLIENT_ID=<your_client_id>
JAMA_CLIENT_SECRET=<your_client_secret>
```

If JAMA is not configured, the `POST` still returns `202` but the **job fails**: `GET /api/v1/jobs/{job_id}`
reports `"status": "failed"`, and `GET /api/v1/jobs/{job_id}/result` returns the error, e.g.:
```json
{
  "detail": "PyJama is not installed — JAMA baseline fetching unavailable."
}
```

### RTM Review from Baseline

```bash
# Submit (→ 202 + job_id), then poll + download per the async job model above
JOB=$(curl -s -X POST http://localhost:8000/api/v1/test-suite-review \
  -H "Content-Type: application/json" \
  -d '{"baseline_id": "BASE-12345", "use_cache": true}' | jq -r .job_id)
curl -s http://localhost:8000/api/v1/jobs/$JOB                       # poll until completed
curl -s http://localhost:8000/api/v1/jobs/$JOB/result --output autoqa_rtm_review.html
```

### Test Case Review from Baseline

```bash
JOB=$(curl -s -X POST http://localhost:8000/api/v1/test-case-review \
  -H "Content-Type: application/json" \
  -d '{"baseline_id": "BASE-67890", "use_cache": true}' | jq -r .job_id)
curl -s http://localhost:8000/api/v1/jobs/$JOB/result --output autoqa_tc_review.html
```

Replace `BASE-12345` and `BASE-67890` with actual JAMA baseline IDs. Both endpoints accept a
`BaselineRequest` body of `{baseline_id, use_cache, test_mode}` — `use_cache` (default `true`)
selects partial caching vs a full recompute, and `test_mode` (optional) forces cache-only JAMA
(no live calls), falling back to the server's `PYJAMA_TEST_MODE` when omitted. The per-record
thread ID is derived from the job ID server-side.

---

## Understanding the Response

The review endpoints return `202 Accepted` + a `job_id` (see [The Asynchronous Job Model](#the-asynchronous-job-model)).
The HTML report is downloaded from `GET /api/v1/jobs/{job_id}/result` once the job is `completed`.
The downloaded viewer file can be opened directly in a browser and contains:
- A summary of all processed items (requirements, test cases, or hazards)
- Per-item review rubric results
- Links to trace matrices and supporting artifacts

Viewer files are also written to the run directory:
- RTM viewer: `logs/run-<timestamp>/viewer.html`
- Test Case viewer: `logs/run-<timestamp>/viewer_tc.html`
- Hazard viewer: `logs/run-<timestamp>/viewer_hz.html`

---

## Configuration

### Environment Variables

Key settings can be configured via `.env`:

| Variable | Required | Purpose |
|----------|----------|---------|
| `API_KEY` | Yes | OpenAI API key (or compatible endpoint) |
| `API_BASE_URL` | No | OpenAI base URL (defaults to official OpenAI) |
| `API_MODEL` | Yes | Model name (e.g., `gpt-4o`, `gpt-4-turbo`) |
| `JAMA_HOST_ADDRESS` | No | JAMA instance hostname |
| `JAMA_CLIENT_ID` | No | JAMA OAuth client ID |
| `JAMA_CLIENT_SECRET` | No | JAMA OAuth client secret |
| `PYJAMA_TEST_MODE` | No | Cache-only JAMA: fetch baselines from disk cache only, no live calls (default `false`; per-request `test_mode` overrides) |
| `ALLOWED_ORIGINS` | No | CORS allowed origins (default: `*`) |
| `ENVIRONMENT` | No | `development` or `production` (hides API docs in prod) |

See `autoqa/core/config.py` for the complete settings list.

---

## Debugging Tips

### Check Logs

Real-time logs appear in the console and are also written to `logs/run-<timestamp>/autoqa.log`.

Log entries include request IDs for tracing concurrent requests. Because reviews run as
background jobs, the `POST` completes almost immediately with `202`, and the actual review
progress is logged separately under the job's `logs/run-<timestamp>/` folder:
```
Request started: POST /api/v1/hazard-risk-review
Request completed: POST /api/v1/hazard-risk-review - 202
```

### Inspect Network Activity

Use your browser's Developer Tools (F12) to monitor requests:
- **Network** tab shows uploads, response size, timing
- **Console** shows any JavaScript errors from the frontend
- **Application** tab lets you inspect cookies and local storage

### Performance Profiling

Each run writes `token_usage.jsonl` with per-call token and cost metrics:
```json
{"thread_id": "...", "node": "...", "input_tokens": 500, "output_tokens": 200, "cost": 0.025}
```

---

## Production Deployment

For production:
1. Remove `--reload` flag (Uvicorn with auto-reload is dev-only)
2. Use a production ASGI server (Gunicorn with a Uvicorn worker, or similar)
3. Set `ENVIRONMENT=production` to hide API documentation endpoints
4. Set `ALLOWED_ORIGINS` to your domain(s) instead of `*`
5. Ensure all required environment variables are set and secrets are not in `.env`

> **Run a single worker.** The async job registry (`autoqa/api/jobs.py`) is in-memory, so a
> `job_id` created on one worker would be invisible to another — polling `GET /jobs/{id}` could
> hit a worker that never saw the job. Keep `--workers 1` until the registry is moved to a shared
> backend (Redis is already a dependency). Jobs are also serialized one-at-a-time to preserve the
> per-run logging invariant.

Example production command:
```bash
gunicorn autoqa.api.main:app \
  --workers 1 \
  --worker-class uvicorn.workers.UvicornWorker \
  --bind 0.0.0.0:8000
```
