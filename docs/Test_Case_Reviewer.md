# Test Case Reviewer — via the LangGraph node

Fetch a **test-case-centric** structure from a Jama baseline: every test case in the
baseline with its **upstream** requirements and design documents (the inverse of the
test suite reviewer). Driven through `PyJamaDataSourceNode` (`pyjama/langgraph/nodes.py`).

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
    request_type="test_case_review",
    baseline_id="BASE-12345",        # required for this request type
)

async def main():
    result = await node({"pyjama_request": request})
    jama_data = result["jama_data"]          # list, one entry per test case
    meta = result["jama_metadata"]           # {request_type, count, baseline_id, ...}
    print(f"{meta['count']} test cases")
    for tc in jama_data:
        print(tc["test_case"]["test_id"],
              len(tc["requirements"]), "requirements,",
              len(tc["design_docs"]), "design docs")

asyncio.run(main())
```

### Optional: transform to typed LangGraph state

```python
from pyjama.langgraph.transforms import transform_test_case_review_to_state
states = transform_test_case_review_to_state(result["jama_data"])
# each state: {"test_case": TestCase, "requirements": [Requirement], "design_docs": [DesignDoc]}
```

## Output shape (`jama_data`)

```json
[
  {
    "test_case": { "test_id": "TC-PUMP-202", "description": "...", "setup": "...",
                   "steps": "Step 1. ...", "expectedResults": "ExpectedResult 1. ..." },
    "requirements": [
      { "req_id": "REQ-PUMP-101", "text": "..." },
      { "req_id": "REQ-PUMP-102", "text": "..." }
    ],
    "design_docs": [ { "doc_id": "DD-PUMP-RC-001", "name": "...", "description": "..." } ]
  }
]
```

## Caching

Backed by the Tier-3 disk cache. On the first call the result is written to:

```
./cache/source/baselines/BASE-12345/test_case_reviewer_structure_ids_{ts}.jsonl
./cache/source/baselines/BASE-12345/test_case_reviewer_structure_response_{ts}.jsonl
```

`cache_mode` controls reads:

| Mode | Behavior |
|------|----------|
| `USE` (default) | Return the cached response if the folder/files exist; otherwise fetch + write. |
| `REFRESH` | Ignore the cache on the first call this session (fetch + write a new `{ts}`), reuse it afterward. |
| `OFF` | Never read or write the cache; always call Jama. |
