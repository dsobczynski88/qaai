# Bidirectional Trace — via the LangGraph node

For each input requirement, fetch the **full bidirectional trace**: the **upstream**
hierarchy (system requirements → user needs) **and** the **downstream** verification
artifacts (test cases + design documents). Keyed by a project name + a list of
identifiers (GIDs or document keys). Driven through `PyJamaDataSourceNode`
(`pyjama/langgraph/nodes.py`).

## Setup

`.env` in the project root:

```
JAMA_CLIENT_ID = <your_client_id>
JAMA_CLIENT_SECRET = <your_client_secret>
JAMA_HOST_ADDRESS = https://<your-org>.jamacloud.com/
```

## Usage

```python
import os
import asyncio
from dotenv import load_dotenv
from pyjama.langgraph.nodes import PyJamaDataSourceNode, PyJamaNodeConfig, PyJamaRequest
from pyjama.utils.cache_manager import CacheMode

load_dotenv()

config = PyJamaNodeConfig(
    host_address=os.getenv("JAMA_HOST_ADDRESS"),
    client_id=os.getenv("JAMA_CLIENT_ID"),
    client_secret=os.getenv("JAMA_CLIENT_SECRET"),
    max_concurrent=100,
    cache_mode=CacheMode.USE,        # OFF / USE / REFRESH
)

node = PyJamaDataSourceNode(config)

request = PyJamaRequest(
    request_type="bidirectional_trace",
    project_name="Patient Safety Platform",          # required for this request type
    identifiers=["GID-2788627", "GID-2788628", "PRQ-123"],  # GIDs or document keys
)

async def main():
    result = await node({"pyjama_request": request})
    jama_data = result["jama_data"]          # list, one entry per input requirement
    meta = result["jama_metadata"]           # {request_type, count, project_name, identifiers}
    print(f"{meta['count']} software requirements")
    for req in jama_data:
        print(req["requirement"]["req_id"],
              "| upstream sys reqs:", len(req["system_requirements"]),
              "| downstream tests:", len(req["test_cases"]),
              "| design docs:", len(req["design_docs"]))

asyncio.run(main())
```

> The node returns the raw structure in `jama_data`; there is no dedicated transform
> helper for bidirectional traces (unlike the reviewer workflows).

## Output shape (`jama_data`)

```json
[
  {
    "requirement": { "req_id": "REQ-PUMP-101", "text": "..." },
    "system_requirements": [
      { "req_id": "SYS-PUMP-015", "text": "...",
        "user_needs": [ { "req_id": "UN-PUMP-003", "text": "..." } ] }
    ],
    "test_cases": [
      { "test_id": "TC-PUMP-201", "description": "...", "setup": "...",
        "steps": "...", "expectedResults": "...", "in_review_baseline": false }
    ],
    "design_docs": [ { "doc_id": "DD-PUMP-RC-001", "name": "...", "description": "..." } ]
  }
]
```

## Caching

Backed by the Tier-3 disk cache. On the first call, **one response file per input
identifier** is written under:

```
./cache/source/identifiers/bidirectional_trace_response_GID-2788627_{ts}.jsonl
./cache/source/identifiers/bidirectional_trace_response_GID-2788628_{ts}.jsonl
./cache/source/identifiers/bidirectional_trace_response_PRQ-123_{ts}.jsonl
```

`cache_mode` controls reads (the cache HIT short-circuits **before** project resolution
and any Jama API call):

| Mode | Behavior |
|------|----------|
| `USE` (default) | Return the cached responses if every requested identifier has a file; otherwise fetch + write. |
| `REFRESH` | Ignore the cache on the first call this session (fetch + write new `{ts}` files), reuse them afterward. |
| `OFF` | Never read or write the cache; always call Jama. |
