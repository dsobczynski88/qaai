# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

A Python library (`pyjama`) that pulls traceability data from a Jama Connect REST API and assembles it into structured dicts for downstream use (AI review pipelines, reports, JSONL exports). It is a **library only** — FastAPI is a listed dependency but no HTTP routes exist yet.

## Commands

```bash
# Install
uv sync

# Run all tests
uv run pytest tests/ -v

# Unit tests only — no Jama credentials needed
uv run pytest tests/langgraph/ -v

# Run a single test
uv run pytest tests/langgraph/test_nodes.py::test_pyjama_request_test_suite_review_valid -v

# Integration tests — require .env with JAMA_CLIENT_ID, JAMA_CLIENT_SECRET, JAMA_HOST_ADDRESS
uv run pytest tests/integration/ -v

# Run integration tests for a specific baseline
uv run pytest tests/integration/tests.py::test_get_test_suite_reviewer_structure -v
```

No build step. No linter configured.

## Architecture

### Fetch → Assemble pipeline

Every public method on `PyJamaTraceMatrix` (`pyjama/jama/pyjama.py`) follows the same two-phase pattern:

1. **Fetch phase** — hit the Jama API concurrently using inline `ThreadPoolExecutor` blocks (each method builds its own). `_fetch_concurrent()` exists as a fault-tolerant helper but is not called by the public methods; each method instead calls `future.result()` directly and lets any failure propagate.

2. **Assemble phase** — pass the raw dicts to one of the assembler classes in `pyjama/assemblers/jama_assemblers.py`. Assemblers own all output-shaping logic and have no API calls. They are the right place to change output structure.

### Item classification

Items are classified by type in two ways:
- **By `itemType` integer** (e.g., `TEST_CASE_ITEM_TYPE_ID = 71`) — used in the baseline-based methods for test case filtering. Fast O(n) comparison.
- **By `typekey` substring** (e.g., `"TEST" in doc_key`) — used in GID-based methods and assemblers to separate test cases from design docs. Searches are substring, not prefix — `"REQ" in "PREQ-001"` would match. Also used for system req classification via the `PRQ_type$63` pick-list field.

### Identifier mapping

`map_identifiers_to_api_ids()` in `jama_utils.py` auto-detects whether inputs are GIDs (`GID-*`) or document keys (`PRQ-*`, `REQ-*`, etc.) and routes to the appropriate mapping strategy. All GID-based public methods go through this.

### Tier-3 disk caching

`DiskCacheManager` (`pyjama/utils/cache_manager.py`) writes timestamped artifacts under `./cache/source/`
so repeat runs skip the Jama API. A `CacheMode` (`OFF`/`USE`/`REFRESH`) is passed to the
`PyJamaTraceMatrix` constructor (`cache_mode=`, default `USE`) and through to `JamaProjectCache`.
Each public method wraps its fetch+assemble body with a top-of-method cache CHECK and a pre-return
WRITE; `OFF` reproduces the original (no-cache) behavior exactly. Layout:
- `cache/source/projects/` — project name→ID directory (`JamaProjectCache`)
- `cache/source/baselines/<baseline_id>/` — paired `_ids_` + `_response_` jsonl for the two reviewer methods
- `cache/source/identifiers/` — one `_response_<identifier>_` jsonl per input identifier (bidirectional,
  hierarchical) or one aggregated `rtm_response_<hash>_` file (RTM)

Per-identifier methods correlate each result entry back to its **input identifier** positionally
(`zip(software_reqs_dict.keys(), result)` + the `api_id→identifier` reverse map), since a result's
`req_id` is a document key, not the input identifier.

`JamaProjectCache` (`pyjama/utils/jama_project_cache.py`) resolves project name → ID: `USE` reads the
newest cached directory and refreshes only on miss; `REFRESH` refreshes once per session; `OFF` always
hits the API.

### LangGraph integration

`pyjama/langgraph/nodes.py` wraps `PyJamaTraceMatrix` as an async LangGraph node. The sync API methods run in `asyncio.get_event_loop().run_in_executor()`. `pyjama/langgraph/transforms.py` converts the raw list-of-dicts output into typed Pydantic models (`Requirement`, `TestCase`, `DesignDoc`, `SystemRequirement`) for use as LangGraph state.

### Instance-specific constants

`pyjama/utils/jama_constants.py` contains pick-list IDs that are specific to the Baxter Jama instance:
- `REQUIREMENT_ITEM_TYPE_ID = 63`, `TEST_CASE_ITEM_TYPE_ID = 71`, `SYSTEM_REQUIREMENT_TYPE_ID = 1382`
- `SETUP_KEY = "setup$71"`, `REQUIREMENT_ITEM_TYPE_FIELD_NAME = "PRQ_type$63"`

These must be verified if targeting a different Jama organisation.

## Test structure

- `tests/langgraph/test_nodes.py` — unit tests, fully mocked, no credentials needed
- `tests/integration/tests.py` — hits the real API; parametrized via JSONL fixtures in `tests/fixtures/`
- `tests/conftest.py` — provides `pyjama_instance` and `jsonl_recorders` fixtures; `jsonl_recorders` writes real API responses to fixtures for future replay
- Assembler classes and `jama_utils.py` utility functions have no unit tests currently
