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
  "version": "0.2.0",
  "services": {
    "rtm_service": "available",
    "hazard_service": "available",
    "test_case_service": "available"
  }
}
```

---

## Testing the Hazard Upload Endpoint

The hazard reviewer works with just an Excel file—no JAMA credentials required.

### Via curl

```bash
curl -X POST http://localhost:8000/api/v1/hazard-risk-review \
  -F "project_name=Test Project" \
  -F "file=@tests/fixtures/external/software_hazard_analysis.xlsx" \
  -F "sheet_name=SHA Table" \
  -F "use_cache=true" \
  --output autoqa_hazard_review.html
```

The multipart form fields are `project_name` (required), `file` (required, `.xlsx`/`.xls`),
`sheet_name` (default `SHA Table`), and `use_cache` (default `true` — partial caching; set
`false` to recompute from scratch). The per-row thread ID is derived from the request ID server-side.

Then open the downloaded `autoqa_hazard_review.html` in a browser to see the viewer.

### Via the Frontend UI

1. Click the **"Software Hazard Analysis"** card
2. Enter a project name (e.g., "Test Project")
3. Drag and drop `tests/fixtures/external/software_hazard_analysis.xlsx` onto the upload zone
4. Leave **"Use cached results"** checked (or uncheck to recompute from scratch)
5. Click **"Run Review"** → wait for the spinner
6. Once complete, click the download link to save the HTML viewer

---

## Testing Baseline Reviews (RTM & Test Case)

RTM baseline and test case baseline reviews require JAMA credentials to be configured
in your `.env` file:

```env
JAMA_HOST_ADDRESS=<your_jama_host>
JAMA_CLIENT_ID=<your_client_id>
JAMA_CLIENT_SECRET=<your_client_secret>
```

If JAMA is not configured, the endpoints return:
```json
{
  "detail": "PyJama is not installed — JAMA baseline fetching unavailable."
}
```

### RTM Review from Baseline

```bash
curl -X POST http://localhost:8000/api/v1/test-suite-review \
  -H "Content-Type: application/json" \
  -d '{
    "baseline_id": "BASE-12345",
    "use_cache": true
  }' \
  --output autoqa_rtm_review.html
```

### Test Case Review from Baseline

```bash
curl -X POST http://localhost:8000/api/v1/test-case-review \
  -H "Content-Type: application/json" \
  -d '{
    "baseline_id": "BASE-67890",
    "use_cache": true
  }' \
  --output autoqa_tc_review.html
```

Replace `BASE-12345` and `BASE-67890` with actual JAMA baseline IDs. Both endpoints accept a
`BaselineRequest` body of `{baseline_id, use_cache}` — `use_cache` (default `true`) selects partial
caching vs a full recompute. The per-record thread ID is derived from the request ID server-side.

---

## Understanding the Response

All batch review endpoints return HTML viewer files that can be opened directly in a browser.
These viewers contain:
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
| `ALLOWED_ORIGINS` | No | CORS allowed origins (default: `*`) |
| `ENVIRONMENT` | No | `development` or `production` (hides API docs in prod) |

See `autoqa/core/config.py` for the complete settings list.

---

## Debugging Tips

### Check Logs

Real-time logs appear in the console and are also written to `logs/run-<timestamp>/autoqa.log`.

Log entries include request IDs for tracing concurrent requests:
```
Request started: POST /api/v1/hazard-risk-review
...
Request completed: POST /api/v1/hazard-risk-review - 200
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
2. Use a production ASGI server (Gunicorn with Uvicorn workers, or similar)
3. Set `ENVIRONMENT=production` to hide API documentation endpoints
4. Set `ALLOWED_ORIGINS` to your domain(s) instead of `*`
5. Ensure all required environment variables are set and secrets are not in `.env`

Example production command:
```bash
gunicorn autoqa.api.main:app \
  --workers 4 \
  --worker-class uvicorn.workers.UvicornWorker \
  --bind 0.0.0.0:8000
```
