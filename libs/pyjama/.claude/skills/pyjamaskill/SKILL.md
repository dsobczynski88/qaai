---
name: pyjama-trace-matrix
description: Fetch data from Jama Connect with the py-jama-rest-client library and shape it into a subset trace matrix — especially for FastAPI apps that expose Jama relationships via async endpoints. Use this skill any time the user mentions JamaClient, py-jama-rest-client, Jama relationships, a trace matrix, Jama baselines, item-type filtering, or exposing Jama data through FastAPI routes. Also use it for adjacent work like handling Jama pagination and rate limits, mapping Jama exceptions to HTTP status codes, streaming CSV/Excel exports of Jama data, or bridging the synchronous JamaClient into an async FastAPI route. If the user is touching any code that imports from py_jama_rest_client, lean toward using this skill.
---

# Jama Trace Matrix (py-jama-rest-client + FastAPI)

A skill for fetching Jama Connect relationships and shaping them into a trace matrix restricted to a subset of items, then exposing the result through FastAPI endpoints.

## What problem this solves

A trace matrix is a two-dimensional view of relationships between two groups of items in Jama (classically: Requirements × Test Cases, to see which requirements are verified). The "subset" part matters because real projects have tens of thousands of items and hundreds of thousands of relationships — you almost never want the full project cross-product. Typical subsets:

- **By baseline name** — the items captured in a named baseline (a snapshot of a project at a point in time).
- **By item type** — all items of a given type (e.g. all Requirements, all Test Cases).
- **Both combined** — items of type X that appeared in baseline Y.

The py-jama-rest-client is synchronous and paginates in the background. Wiring it into an async FastAPI route without blocking the event loop, and without refetching the same lookup tables on every request, is most of the non-obvious work.

## Mental model: three stages

Every trace matrix request boils down to the same three stages. Keep these separate in the code — one function per stage — so they stay testable and cacheable independently.

1. **Resolve the subset → two lists of item IDs** (rows and columns). "Resolve" means: turn a human-friendly input ("baseline named 'Release 2.4'", "item type 'Requirement'") into the integer item IDs that Jama's relationship endpoint speaks.
2. **Fetch raw data** — relationships for the project, plus lookup tables (item type names, relationship type names). Fetch lookups **once per process** if possible; they rarely change.
3. **Shape into a matrix** — index relationships by `(fromItem, toItem)`, then project onto the row × column axes.

Writing it as one big function that does all three is tempting and wrong: it couples Jama I/O to matrix logic, making it impossible to unit-test shaping without a live Jama instance.

## Setup

```python
from py_jama_rest_client.client import JamaClient

# Basic auth
client = JamaClient(
    host_domain="https://yourorg.jamacloud.com",
    credentials=("username", "password"),
    oauth=False,
)

# OAuth (preferred for production)
client = JamaClient(
    host_domain="https://yourorg.jamacloud.com",
    credentials=("client_id", "client_secret"),
    oauth=True,
)
```

Notes worth internalizing:

- `allowed_results_per_page` defaults to 20, max 50. Always set it to 50 for read-heavy work — the client paginates automatically, and a lower page size just means more HTTP round-trips. (Looking at the source: pass `allowed_results_per_page=50` on the constructor **and** on individual calls that accept it, because some methods re-declare the default internally.)
- `verify=False` exists but should only be used against staging / self-signed instances.
- The client emits logs to the logger named `py_jama_rest_client`. In FastAPI, wire that into your logging config explicitly; otherwise you'll get API errors swallowed silently.

For a one-line reference of every JamaClient method relevant to this skill (read-side endpoints only), see `references/jama-client-reference.md`.

## Stage 1: resolving the subset

The user wants to restrict the matrix to a subset. Two selectors are primary:

### By baseline name

The client has `get_baselines(project_id)` and `get_baselines_versioneditems(baseline_id)` but no "get baseline by name" endpoint. Look it up by listing and filtering:

```python
def resolve_baseline_by_name(client, project_id: int, baseline_name: str) -> int:
    baselines = client.get_baselines(project_id)
    matches = [b for b in baselines if b["name"] == baseline_name]
    if not matches:
        raise ValueError(
            f"No baseline named {baseline_name!r} in project {project_id}. "
            f"Available: {[b['name'] for b in baselines]}"
        )
    if len(matches) > 1:
        # Jama allows duplicate baseline names. Most recent wins; surface the ambiguity.
        matches.sort(key=lambda b: b.get("createdDate", ""), reverse=True)
    return matches[0]["id"]

def item_ids_in_baseline(client, baseline_id: int) -> list[int]:
    versioned = client.get_baselines_versioneditems(baseline_id)
    # Each entry has both an "id" (versioned-item id) and a "documentVersion" block;
    # the underlying item id lives in the "item" field, not "id".
    return [v["item"] for v in versioned]
```

### By item type

`get_item_types()` returns the full list across the instance; match on `typeKey` (short code like "REQ") or `display` (human name). Then fetch items of that type in the project using `get_abstract_items(project=..., item_type=[...])`:

```python
def resolve_item_type_by_key_or_name(client, key_or_name: str) -> int:
    types = client.get_item_types()
    for t in types:
        if t["typeKey"] == key_or_name or t["display"] == key_or_name:
            return t["id"]
    raise ValueError(f"No item type matches {key_or_name!r}")

def item_ids_of_type_in_project(client, project_id: int, type_id: int) -> list[int]:
    items = client.get_abstract_items(project=[project_id], item_type=[type_id])
    return [i["id"] for i in items]
```

### Combining selectors

For "items of type X in baseline Y", resolve both and take the intersection:

```python
row_ids = set(item_ids_in_baseline(client, baseline_id)) & set(item_ids_of_type_in_project(client, project_id, req_type_id))
```

The ready-to-import helpers live in `scripts/subset_resolvers.py`.

## Stage 2: fetching relationships

```python
relationships = client.get_relationships(project_id, allowed_results_per_page=50)
```

This returns every relationship in the project — potentially tens of thousands. The client paginates internally so it's one call from the caller's perspective, but it's not cheap. Two consequences:

- **Cache per process.** Wrap this in a TTL cache (e.g. `cachetools.TTLCache`) keyed by `project_id`. Relationships do change, but not every second, and matrix generation is a read-mostly workload.
- **Don't call `get_relationship(relationship_id)` in a loop** to enrich data. If you find yourself doing this, you've misused the API — everything you need is already in the bulk `get_relationships` response.

Each relationship dict looks like:

```python
{
    "id": 1234,
    "fromItem": 1001,          # source item id
    "toItem":   2001,          # target item id
    "relationshipType": 42,    # id — join against get_relationship_types() for the name
    "suspect": False,
    "createdDate": "...",
    "modifiedDate": "...",
    # ... more metadata
}
```

You'll also want name-lookup dictionaries, fetched once and reused:

```python
rel_type_names = {t["id"]: t["name"] for t in client.get_relationship_types()}
item_type_names = {t["id"]: t["display"] for t in client.get_item_types()}
```

## Stage 3: shaping the matrix

The canonical in-memory shape is a sparse dict — cells default to "no relationship" and only existing links occupy space:

```python
{
    "project_id": 42,
    "source_axis": {"selector": "item_type", "value": "REQ", "label": "Requirements"},
    "target_axis": {"selector": "item_type", "value": "TC",  "label": "Test Cases"},
    "rows":    [{"id": 1001, "documentKey": "REQ-1", "name": "..."}],
    "columns": [{"id": 2001, "documentKey": "TC-1",  "name": "..."}],
    "cells": {
        # cells[row_id][col_id] -> list of relationships linking them
        1001: {
            2001: [
                {"relationshipId": 555, "typeId": 42, "typeName": "verified by", "suspect": False}
            ]
        }
    },
    "summary": {
        "rows": 1, "columns": 1, "populated_cells": 1,
        "rows_with_coverage": 1, "rows_without_coverage": 0,
    }
}
```

Why sparse dicts and not a dense 2D list:

- Trace matrices are usually sparse (most cells empty). A dense list wastes memory and serializes clumsily to JSON.
- Looking up coverage for a specific row is O(1) with a dict, O(n) scanning a list.
- Conversion to dense form for export (CSV/Excel) is trivial when needed; the reverse is lossier.

The transformation itself is small and pure — see `scripts/build_trace_matrix.py` for a reference implementation. It accepts the three inputs (row ids, column ids, relationships) plus the lookup maps, and produces the dict above with no Jama calls of its own. This separation is deliberate: you can feed it fixtures in tests.

### Direction of relationships

Decide up front whether the matrix treats relationships as directional:

- **Directional** (default): cell (R, T) is populated only if a relationship exists with `fromItem=R, toItem=T`. This matches Jama's upstream/downstream model — Requirements are upstream of Test Cases, so the Req → Test relationship is `from=req, to=test`.
- **Undirected**: populate cells in both directions. Useful when the row/column axes don't have an obvious up/down ordering.

The reference script accepts a `directional: bool` flag. Default to `True` and document it in the endpoint.

## FastAPI integration

Three things make Jama + FastAPI tricky. All three are solved in `references/fastapi-patterns.md`; the summary:

1. **JamaClient is synchronous.** Calling it directly from an `async def` route will block the event loop. Use `await asyncio.to_thread(client.get_relationships, project_id)` (Python 3.9+) or `await loop.run_in_executor(None, ...)` on older versions. Wrap the blocking calls, never the matrix shaping (which is pure Python and fast).

2. **Share the JamaClient across requests.** Construct once at startup (in a `lifespan` context manager) and inject via `Depends`. Creating a fresh client per request triggers a fresh OAuth token fetch and defeats HTTP connection pooling.

3. **Map Jama exceptions to HTTP responses.** The client raises `UnauthorizedException` (→ 401), `ResourceNotFoundException` (→ 404), `TooManyRequestsException` (→ 429, with `Retry-After` if present), and a handful of others. A single global exception handler keeps routes clean:

   ```python
   from py_jama_rest_client.client import (
       APIException, UnauthorizedException, ResourceNotFoundException,
       TooManyRequestsException,
   )

   @app.exception_handler(APIException)
   async def jama_exception_handler(request, exc):
       status = {
           UnauthorizedException: 401,
           ResourceNotFoundException: 404,
           TooManyRequestsException: 429,
       }.get(type(exc), 502)   # 502 for upstream errors, not 500
       return JSONResponse(status_code=status, content={"detail": str(exc)})
   ```

See `references/fastapi-patterns.md` for the complete patterns: lifespan setup, dependency providers, Pydantic request/response models for matrix endpoints, and streaming CSV/Excel exports using `StreamingResponse`.

## Output formats

JSON is the primary output (the sparse dict above, returned from the FastAPI route). Two export paths are worth supporting:

- **CSV** — a dense pivot table, row headers = item document keys, column headers = item document keys, cells = comma-separated relationship type names (or empty). Easy to stream via `StreamingResponse` with `media_type="text/csv"`.
- **Excel (.xlsx)** — same pivot via `openpyxl` or `pandas.ExcelWriter`. Use a `StreamingResponse` with an `io.BytesIO` buffer; don't write to disk.

`references/fastapi-patterns.md` has complete examples for both. Don't add these export paths unless the user asks — they bring in `pandas` / `openpyxl` dependencies and many apps only need JSON.

## Common pitfalls

- **Calling `get_relationship` in a loop.** Everything is in `get_relationships(project_id)`. A loop means you wrote the code before looking at the payload.
- **Forgetting `allowed_results_per_page=50`.** The default is 20, and for projects with 50,000 relationships that's 2,500 HTTP requests instead of 1,000. Nearly 3× slower.
- **Constructing `JamaClient` per request.** OAuth token fetch on every request. Use a lifespan-managed singleton.
- **Treating baselines like reviews.** Baselines are snapshots; Review Center reviews are a different object. The Jama REST API does not expose Review Center as a queryable resource — no `/reviews` endpoint, no way to list review items, comments, or decisions. The one exception is `GET /baselines/{id}/reviewlink`, which returns the URL, name, and key of the review *attached to* a baseline (if any). `scripts/subset_resolvers.py::get_baseline_review_link` wraps this. If the user wants anything beyond that pointer — review status, participants, approval data — they're looking at web-scraping or waiting on an open feature request. Raise this early; don't let them discover it halfway through implementation.
- **Silent rate-limit retries.** If you catch `TooManyRequestsException` and retry, log it. Users hitting Jama's rate limit usually want to know their matrix is slow because they're throttled.
- **Assuming `fromItem` is always the "row"**. If the user's row axis is Test Cases and column axis is Requirements, and relationships go Req → TC, then the row item is actually `toItem`. The reference script's `directional` flag handles this, but be explicit in the endpoint about which axis is upstream.

## Files in this skill

- `scripts/subset_resolvers.py` — Resolve baseline-by-name and item-type-by-key into item ID lists. Also includes `get_baseline_review_link` as an escape-hatch for the one Review Center surface the REST API exposes (a pointer to the review attached to a baseline). Import and use directly.
- `scripts/build_trace_matrix.py` — Pure transformation from (row_ids, col_ids, relationships) to the canonical matrix dict. No Jama calls; fully testable.
- `references/jama-client-reference.md` — Cheatsheet of every JamaClient read-side method likely to come up, with signatures and notes. Load when you need to look up a specific endpoint.
- `references/fastapi-patterns.md` — FastAPI lifespan setup, async bridging, Pydantic models, dependency injection, exception handlers, and streaming export responses. Load when wiring the matrix into an HTTP route.
