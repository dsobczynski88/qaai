# FastAPI patterns for Jama trace matrix routes

Load this when wiring the matrix into HTTP endpoints. Covers the five things that are either non-obvious or easy to get wrong:

1. Lifespan setup so `JamaClient` is constructed once and reused.
2. Dependency injection to get the shared client into routes.
3. Async bridging (the client is synchronous; routes should not be).
4. Pydantic models for the request/response shape.
5. Exception handling — mapping Jama errors to HTTP status codes.
6. Streaming CSV and Excel exports.

## 1. Lifespan setup

Construct the `JamaClient` once per process, during startup. Don't build it in a module-level global — that runs at import time, which breaks testability and hides config errors behind deferred failures.

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from py_jama_rest_client.client import JamaClient

from .config import settings   # your Pydantic Settings or similar


@asynccontextmanager
async def lifespan(app: FastAPI):
    client = JamaClient(
        host_domain=settings.jama_host,
        credentials=(settings.jama_client_id, settings.jama_client_secret),
        oauth=True,
        allowed_results_per_page=50,   # not 20, ever
    )
    # Sanity-check credentials at startup rather than on first request.
    # A failing boot is a clearer signal than a cryptic 401 from a later call.
    client.get_current_user()

    app.state.jama_client = client
    yield
    # JamaClient holds a requests.Session internally but has no explicit
    # close method. Letting the process tear down is fine.


app = FastAPI(lifespan=lifespan)
```

## 2. Dependency injection

```python
from fastapi import Depends, Request


def get_jama_client(request: Request) -> JamaClient:
    return request.app.state.jama_client


# Usage in a route:
@app.get("/matrix")
async def get_matrix(client: JamaClient = Depends(get_jama_client)):
    ...
```

Keeping the dependency a plain function (not a class) makes it easy to override in tests with `app.dependency_overrides[get_jama_client] = lambda: fake_client`.

## 3. Async bridging

`JamaClient` is built on the synchronous `requests` library. Calling any of its methods from an `async def` route without a thread offload **blocks the event loop** — which in practice means one slow Jama call pauses every other request on the process.

Use `asyncio.to_thread` (Python 3.9+):

```python
import asyncio

async def fetch_relationships(client: JamaClient, project_id: int) -> list[dict]:
    return await asyncio.to_thread(client.get_relationships, project_id)
```

Or, for a larger bundle of work that should happen together, offload the whole thing:

```python
from functools import partial

def _fetch_matrix_inputs(client, project_id, row_selector, col_selector):
    # All sync calls happen here, off the event loop.
    row_ids = resolve_subset(client, project_id, **row_selector)
    col_ids = resolve_subset(client, project_id, **col_selector)
    relationships = client.get_relationships(project_id, allowed_results_per_page=50)
    rel_type_names = {t["id"]: t["name"] for t in client.get_relationship_types()}
    row_meta = fetch_item_metadata(client, row_ids)
    col_meta = fetch_item_metadata(client, col_ids)
    return row_ids, col_ids, relationships, rel_type_names, row_meta, col_meta


async def _fetch_matrix_inputs_async(client, project_id, row_selector, col_selector):
    return await asyncio.to_thread(
        _fetch_matrix_inputs, client, project_id, row_selector, col_selector,
    )
```

Bundling minimizes thread hops. Don't wrap *each* individual call in `to_thread` — that's N thread transitions for no benefit, and the per-hop latency adds up.

## 4. Pydantic models

The selector model accepts either a baseline name, an item type, or both (intersected). Using `model_validator` to enforce "at least one" keeps validation close to the data.

```python
from typing import Literal
from pydantic import BaseModel, Field, model_validator


class AxisSelector(BaseModel):
    baseline_name: str | None = Field(default=None, description="Exact name of a Jama baseline in this project.")
    item_type: str | None = Field(default=None, description="typeKey (e.g. 'REQ') or display name of an item type.")

    @model_validator(mode="after")
    def at_least_one(self):
        if self.baseline_name is None and self.item_type is None:
            raise ValueError("Specify at least one of baseline_name or item_type.")
        return self


class MatrixRequest(BaseModel):
    project_id: int
    rows: AxisSelector
    columns: AxisSelector
    directional: bool = True


class MatrixHeader(BaseModel):
    id: int
    documentKey: str | None
    name: str | None


class MatrixCell(BaseModel):
    relationshipId: int
    typeId: int | None
    typeName: str | None
    suspect: bool


class MatrixResponse(BaseModel):
    project_id: int
    source_axis: dict | None
    target_axis: dict | None
    directional: bool
    rows: list[MatrixHeader]
    columns: list[MatrixHeader]
    cells: dict[int, dict[int, list[MatrixCell]]]
    summary: dict
```

FastAPI's automatic OpenAPI doc generation uses these, so the interactive `/docs` page becomes a decent internal tool for QA.

## 5. Exception handling

Register one handler at the app level rather than wrapping each route in try/except. Subclass checks are ordered: `TooManyRequestsException` is an `APIException`, so list the specific ones first or use an explicit type dispatch.

```python
from fastapi import Request
from fastapi.responses import JSONResponse
from py_jama_rest_client.client import (
    APIException,
    UnauthorizedException,
    ResourceNotFoundException,
    TooManyRequestsException,
    APIServerException,
)


_JAMA_STATUS_MAP = {
    UnauthorizedException: 401,
    ResourceNotFoundException: 404,
    TooManyRequestsException: 429,
    APIServerException: 502,   # upstream server error -> 502 Bad Gateway
}


@app.exception_handler(APIException)
async def jama_exception_handler(request: Request, exc: APIException):
    status = _JAMA_STATUS_MAP.get(type(exc), 502)
    headers = {}
    if isinstance(exc, TooManyRequestsException):
        # Jama doesn't always send Retry-After; 30s is a reasonable default.
        headers["Retry-After"] = "30"
    return JSONResponse(
        status_code=status,
        content={"detail": str(exc), "jamaStatus": exc.status_code},
        headers=headers,
    )
```

Two things worth knowing:

- Return **502** (Bad Gateway), not 500, for unknown upstream errors. 500 implies *your* app broke; 502 says the upstream service did. This is what clients hitting your API will care about when debugging.
- Don't leak Jama's raw error messages to end users if your API is public. The message from Jama can include internal IDs, URLs, or stack trace fragments. For internal tools it's fine; for public APIs, log the full exception and return a generic message.

## 6. Streaming CSV / Excel exports

The matrix can get large. Returning the full payload as a JSON blob is fine up to a few MB; for exports, stream through `StreamingResponse` so memory doesn't spike.

### CSV

```python
import csv
import io
from fastapi.responses import StreamingResponse

from .scripts.build_trace_matrix import matrix_to_dense_rows


def _matrix_csv_bytes(matrix: dict):
    buf = io.StringIO()
    writer = csv.writer(buf)
    for row in matrix_to_dense_rows(matrix):
        writer.writerow(row)
        # yield in chunks — emit after every N rows for very large matrices
    buf.seek(0)
    yield buf.getvalue().encode("utf-8")


@app.post("/matrix.csv")
async def matrix_csv(req: MatrixRequest, client: JamaClient = Depends(get_jama_client)):
    matrix = await _build_matrix_async(client, req)  # your orchestration fn
    filename = f"trace_matrix_project{req.project_id}.csv"
    return StreamingResponse(
        _matrix_csv_bytes(matrix),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
```

For truly large matrices, yield per-row inside the generator instead of materializing the whole CSV in a `StringIO`. The `matrix_to_dense_rows` helper returns a list — if that list itself is the memory problem, write a streaming variant that yields one row at a time.

### Excel (.xlsx)

`openpyxl` is the standard. Stream it through `BytesIO`:

```python
import io
from openpyxl import Workbook
from fastapi.responses import StreamingResponse


def _matrix_xlsx_bytes(matrix: dict) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Trace Matrix"
    for row in matrix_to_dense_rows(matrix):
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.getvalue()


@app.post("/matrix.xlsx")
async def matrix_xlsx(req: MatrixRequest, client: JamaClient = Depends(get_jama_client)):
    matrix = await _build_matrix_async(client, req)
    xlsx_bytes = await asyncio.to_thread(_matrix_xlsx_bytes, matrix)
    filename = f"trace_matrix_project{req.project_id}.xlsx"
    return StreamingResponse(
        io.BytesIO(xlsx_bytes),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
```

The `asyncio.to_thread` on the workbook build matters — `openpyxl`'s `save` is CPU-bound and will freeze the event loop on a large matrix otherwise.

## Putting it together: a full route

```python
from fastapi import APIRouter, Depends

from .deps import get_jama_client
from .models import MatrixRequest, MatrixResponse
from .scripts.subset_resolvers import resolve_subset, fetch_item_metadata
from .scripts.build_trace_matrix import build_trace_matrix

router = APIRouter()


@router.post("/matrix", response_model=MatrixResponse)
async def create_matrix(
    req: MatrixRequest,
    client: JamaClient = Depends(get_jama_client),
) -> MatrixResponse:
    def _work():
        row_ids = resolve_subset(client, req.project_id, **req.rows.model_dump(exclude_none=True))
        col_ids = resolve_subset(client, req.project_id, **req.columns.model_dump(exclude_none=True))
        relationships = client.get_relationships(req.project_id, allowed_results_per_page=50)
        rel_type_names = {t["id"]: t["name"] for t in client.get_relationship_types()}
        row_meta = fetch_item_metadata(client, row_ids)
        col_meta = fetch_item_metadata(client, col_ids)

        return build_trace_matrix(
            project_id=req.project_id,
            row_ids=row_ids,
            col_ids=col_ids,
            row_metadata=row_meta,
            col_metadata=col_meta,
            relationships=relationships,
            rel_type_names=rel_type_names,
            source_axis={"selector": "composite", "value": req.rows.model_dump(exclude_none=True)},
            target_axis={"selector": "composite", "value": req.columns.model_dump(exclude_none=True)},
            directional=req.directional,
        )

    return await asyncio.to_thread(_work)
```

Note the single `asyncio.to_thread` call around the whole orchestration — one thread transition, not one per client call.

## Caching

Add caching once the routes work end-to-end, not before. Two candidates:

- `get_relationships(project_id)` — the expensive bulk fetch. TTL cache of 60s is usually fine; users building matrices iteratively want fresh-ish data, not same-millisecond data.
- `get_relationship_types()` and `get_item_types()` — change rarely. Cache for the lifetime of the process, invalidate via an admin endpoint if needed.

`cachetools.TTLCache` + a module-level lock is enough. Don't pull in Redis for this unless you already have it.

```python
from cachetools import TTLCache
from threading import Lock

_rel_cache: TTLCache = TTLCache(maxsize=16, ttl=60)
_rel_lock = Lock()


def get_relationships_cached(client: JamaClient, project_id: int) -> list[dict]:
    with _rel_lock:
        if project_id in _rel_cache:
            return _rel_cache[project_id]
    # Release lock during HTTP call; two racing requests might both fetch, which is fine.
    data = client.get_relationships(project_id, allowed_results_per_page=50)
    with _rel_lock:
        _rel_cache[project_id] = data
    return data
```
