
# PyJama Trace Matrix

Tools to transform py-jama-rest-client outputs into pandas dataframes and requirement-centric trace matrices.

## Getting Started

### Prerequisites

- Install uv: https://docs.astral.sh/uv/getting-started/installation/

### Installation

#### Option 1: Local Development (Editable Install)

For local development and testing:

```bash
git clone <repo_url>
cd pyjama-fastapi
uv sync
```

#### Option 2: Install as Dependency (Recommended for External Projects)

Install directly from git as an editable package in your external project:

```bash
# Using pip
pip install -e git+https://github.com/<your-org>/pyjama-fastapi.git#egg=pyjama

# Using uv (recommended)
uv add --editable git+https://github.com/<your-org>/pyjama-fastapi.git

# Or add to pyproject.toml
# [project.dependencies]
# pyjama = { git = "https://github.com/<your-org>/pyjama-fastapi.git", editable = true }
```

#### Option 3: Install Specific Branch or Tag

```bash
# Install from specific branch
uv add --editable git+https://github.com/<your-org>/pyjama-fastapi.git@feature-branch

# Install from specific tag/release
uv add --editable git+https://github.com/<your-org>/pyjama-fastapi.git@v1.0.0

# Install from specific commit
uv add --editable git+https://github.com/<your-org>/pyjama-fastapi.git@abc123
```

### Create JAMA API Credentials

Set up your JAMA API credentials by following the instructions here: https://help.jamasoftware.com/ah/en/get-started/setting-up-your-work-environment/set-api-credentials.html

### Create environment file

Create a `.env` file in the project root with your credentials and Jama host. This keeps them out of source control.

```
JAMA_CLIENT_ID = <your_client_id>
JAMA_CLIENT_SECRET = <your_client_secret>
JAMA_HOST_ADDRESS = https://<your-org>.jamacloud.com/
```

All other settings (`data_path`, `log_path`, `max_concurrent`, `cache_mode`) are passed directly to the
`PyJamaTraceMatrix` constructor — there is no config file.

### Caching

`PyJamaTraceMatrix` writes Tier-3 (local disk) cache artifacts under `./cache/source/` so repeat runs
skip the Jama API. Control it with the `cache_mode` constructor argument:

| Mode | Behavior |
|------|----------|
| `CacheMode.USE` (default) | Load the newest cached file if present, else fetch and write one. |
| `CacheMode.REFRESH` | Ignore existing cache on the first call per key this session, fetch fresh and write a new timestamped file, then reuse it for the rest of the session. |
| `CacheMode.OFF` | Never read or write cache; always call the API. |

```python
from pyjama.utils.cache_manager import CacheMode

api = PyJamaTraceMatrix(jama_client, data_path="./data", cache_mode=CacheMode.USE)
```

Cache artifacts are organized under `./cache/source/`:

| Subdirectory | Written by |
|--------------|-----------|
| `cache/source/projects/` | Project name→ID directory, created on the first request that resolves a `project_name` (bidirectional / hierarchical / rtm). |
| `cache/source/baselines/<baseline_id>/` | Paired `_ids_` + `_response_` files for the two baseline reviewer methods. |
| `cache/source/identifiers/` | One response file per input identifier (bidirectional, hierarchical) or one aggregated RTM file. |

Note: the baseline reviewer requests (`test_suite_review` / `test_case_review`) key off a `baseline_id`
and never resolve a project name, so they do **not** create `cache/source/projects/`. Only the
`project_name`-based requests do.

## Usage Examples

The main interface is the `PyJamaTraceMatrix` class, which provides three primary methods for extracting traceability data:

### Example 1: Test Suite Reviewer Structure

Organizes data by requirements, showing all downstream test cases and design documents for each requirement.

```python
from pyjama.jama import PyJamaTraceMatrix
from py_jama_rest_client.client import JamaClient
from dotenv import load_dotenv
import os

# Load credentials
load_dotenv()
client_id = os.getenv("JAMA_CLIENT_ID")
client_secret = os.getenv("JAMA_CLIENT_SECRET")
host_address = "https://your-org.jamacloud.com/"

# Initialize Jama client
jama_client = JamaClient(
    host_domain=host_address,
    credentials=(client_id, client_secret),
    oauth=True
)

# Create PyJamaTraceMatrix instance
api = PyJamaTraceMatrix(
    jama_client=jama_client,
    data_path="./data",
    log_path="logs",
    max_concurrent=100
)

# Extract test suite reviewer structure from a baseline
result = api.get_test_suite_reviewer_structure(
    baseline_id="BASE-84398"
)

print(f"Found {len(result)} requirements")
for req in result:
    req_id = req['requirement']['req_id']
    test_count = len(req['test_cases'])
    design_count = len(req['design_docs'])
    print(f"  {req_id}: {test_count} tests, {design_count} design docs")
```

**Output Structure:**
```json
[
  {
    "requirement": {
      "req_id": "REQ-PUMP-101",
      "text": "The rate-control loop shall execute at 10 Hz..."
    },
    "test_cases": [
      {
        "test_id": "TC-PUMP-202",
        "description": "Fault injection — simulate scheduler stall...",
        "setup": "Pump in standard infusion mode...",
        "steps": "Step 1. Start an infusion at 5 mL/hr...\nStep 2. ...",
        "expectedResults": "ExpectedResult 1. Watchdog counter stalls...\nExpectedResult 2. ...",
        "in_review_baseline": true
      }
    ],
    "design_docs": [
      {
        "doc_id": "DD-PUMP-RC-001",
        "name": "Rate Control Loop Design",
        "description": "Detailed design of the rate control loop..."
      }
    ]
  }
]
```

### Example 2: Test Case Reviewer Structure

Organizes data by test cases, showing all upstream requirements and design documents for each test case.

```python
# Extract test case reviewer structure from a baseline
result = api.get_test_case_reviewer_structure(
    baseline_id="BASE-84398"
)

print(f"Found {len(result)} test cases")
for tc in result:
    test_id = tc['test_case']['test_id']
    req_count = len(tc['requirements'])
    design_count = len(tc['design_docs'])
    print(f"  {test_id}: {req_count} requirements, {design_count} design docs")
```

**Output Structure:**
```json
[
  {
    "test_case": {
      "test_id": "TC-PUMP-202",
      "description": "Fault injection — simulate scheduler stall...",
      "setup": "Pump in standard infusion mode...",
      "steps": "Step 1. Start an infusion at 5 mL/hr...\nStep 2. ...",
      "expectedResults": "ExpectedResult 1. Watchdog counter stalls...\nExpectedResult 2. ..."
    },
    "requirements": [
      {
        "req_id": "REQ-PUMP-101",
        "text": "The rate-control loop shall execute at 10 Hz..."
      },
      {
        "req_id": "REQ-PUMP-102",
        "text": "The UI thread shall monitor the watchdog..."
      }
    ],
    "design_docs": [
      {
        "doc_id": "DD-PUMP-RC-001",
        "name": "Rate Control Loop Design",
        "description": "Detailed design of the rate control loop..."
      }
    ]
  }
]
```

### Example 3: Hierarchical Trace from GIDs

Builds hierarchical traceability structure showing Software Requirements → System Requirements → User Needs.

```python
import json

# Extract hierarchical trace for specific requirements
result = api.get_hierarchical_trace_from_gids(
    identifiers=["GID-2788627", "GID-2788628", "PRQ-123"],
    project_name="Patient Safety Platform"
)

print(f"Found {len(result)} software requirements with hierarchical traces")

# Write to JSONL file (one requirement per line)
with open("hierarchical_trace.jsonl", "w", encoding="utf-8") as f:
    for req_entry in result:
        f.write(json.dumps(req_entry, ensure_ascii=False) + "\n")

# Print summary
for req in result:
    req_id = req['requirement']['req_id']
    sys_req_count = len(req['system_requirements'])
    user_need_count = sum(
        len(sys_req['user_needs']) 
        for sys_req in req['system_requirements']
    )
    print(f"  {req_id}: {sys_req_count} system reqs, {user_need_count} user needs")
```

**Output Structure:**
```json
[
  {
    "requirement": {
      "req_id": "GID-2634456",
      "text": "The reporting solution shall generate PDF reports..."
    },
    "system_requirements": [
      {
        "req_id": "P1545-PRQ-216",
        "text": "The system shall be able to generate reports...",
        "user_needs": [
          {
            "req_id": "P1545-UND-7",
            "text": "As a Hospital Administrator, I need to generate reports..."
          },
          {
            "req_id": "P1545-UND-12",
            "text": "As a Compliance Officer, I need audit trails..."
          }
        ]
      },
      {
        "req_id": "P1545-PRQ-217",
        "text": "The system shall support multiple report formats...",
        "user_needs": [
          {
            "req_id": "P1545-UND-7",
            "text": "As a Hospital Administrator, I need to generate reports..."
          }
        ]
      }
    ]
  }
]
```

## API Reference

### Method Input/Output Summary

| Method | Input Parameters | Output Structure |
|--------|-----------------|------------------|
| `get_test_suite_reviewer_structure()` | `baseline_id` (str): Baseline identifier (e.g., "BASE-84398")<br>`api_id_key` (str, optional): API ID key<br>`design_typekey` (str, optional): Design doc type key<br>`testcase_typekey` (str, optional): Test case type key | List of requirements, each containing:<br>• `requirement`: {req_id, text}<br>• `test_cases`: [{test_id, description, setup, steps, expectedResults, in_review_baseline}]<br>• `design_docs`: [{doc_id, name, description}] |
| `get_test_case_reviewer_structure()` | `baseline_id` (str): Baseline identifier (e.g., "BASE-84398")<br>`api_id_key` (str, optional): API ID key<br>`design_typekey` (str, optional): Design doc type key<br>`requirement_typekeys` (list, optional): Requirement type keys | List of test cases, each containing:<br>• `test_case`: {test_id, description, setup, steps, expectedResults}<br>• `requirements`: [{req_id, text}]<br>• `design_docs`: [{doc_id, name, description}] |
| `get_hierarchical_trace_from_gids()` | `identifiers` (list): GIDs or document keys (e.g., ["GID-2788627", "PRQ-123"])<br>`project_name` (str): Jama project name<br>`api_id_key` (str, optional): API ID key<br>`user_need_typekey` (str, optional): User need type key<br>`prq_type_field` (str, optional): PRQ type field name | List of software requirements, each containing:<br>• `requirement`: {req_id, text}<br>• `system_requirements`: [{req_id, text, user_needs: [{req_id, text}]}] |
| `get_rtm_from_gids()` | `identifiers` (list): GIDs or document keys<br>`project_name` (str): Jama project name<br>`api_id_key` (str, optional): API ID key | Dict with five keys, each a list of items:<br>• `user_needs`, `system_requirements`, `software_requirements`, `test_cases`, `design_docs` |
| `get_bidirectional_trace_from_gids()` | `identifiers` (list): GIDs or document keys<br>`project_name` (str): Jama project name<br>`api_id_key` (str, optional): API ID key | List of software requirements, each containing:<br>• `requirement`: {req_id, text}<br>• `system_requirements`: [{req_id, text, user_needs: [{req_id, text}]}]<br>• `test_cases`: [{test_id, description, ...}]<br>• `design_docs`: [{doc_id, name, description}] |

### Data Structure Details

#### Requirement Object
```python
{
    "req_id": str,      # Document key (e.g., "REQ-PUMP-101", "GID-2788627")
    "text": str         # Cleaned requirement text (HTML stripped)
}
```

#### Test Case Object (Full)
```python
{
    "test_id": str,              # Document key (e.g., "TC-PUMP-202")
    "description": str,          # Test case name/description
    "setup": str,                # Test setup instructions
    "steps": str,                # Formatted test steps ("Step 1. ...\nStep 2. ...")
    "expectedResults": str,      # Formatted expected results ("ExpectedResult 1. ...")
    "in_review_baseline": bool   # Only in get_test_suite_reviewer_structure()
}
```

#### Design Document Object
```python
{
    "doc_id": str,        # Document key (e.g., "DD-PUMP-RC-001")
    "name": str,          # Design document name
    "description": str    # Design document description
}
```

See `examples/` directory for more detailed usage patterns.

## LangGraph Integration

PyJama provides LangGraph-compatible nodes for real-time Jama data fetching, enabling external LangGraph applications to replace static JSONL file inputs with live API calls.

### Installation in External Projects

Install PyJama directly from git as an editable dependency:

```bash
# Using uv (recommended)
uv add --editable git+https://github.com/<your-org>/pyjama-fastapi.git

# Using pip
pip install -e git+https://github.com/<your-org>/pyjama-fastapi.git#egg=pyjama
```

Or add to your `pyproject.toml`:

```toml
[project.dependencies]
pyjama = { git = "https://github.com/<your-org>/pyjama-fastapi.git", editable = true }
```

### Quick Start

```python
import os
from dotenv import load_dotenv
from pyjama.langgraph.nodes import (
    PyJamaDataSourceNode,
    PyJamaNodeConfig,
    PyJamaRequest,
)
from pyjama.langgraph.transforms import transform_test_suite_review_to_state

# Load credentials
load_dotenv()

# Configure PyJama node
config = PyJamaNodeConfig(
    host_address=os.getenv("JAMA_HOST_ADDRESS"),
    client_id=os.getenv("JAMA_CLIENT_ID"),
    client_secret=os.getenv("JAMA_CLIENT_SECRET"),
    max_concurrent=100
)

# Create node
jama_node = PyJamaDataSourceNode(config)

# Create request
request = PyJamaRequest(
    request_type="test_suite_review",
    baseline_id="BASE-84398"
)

# Fetch data
result = await jama_node({"pyjama_request": request})

# Transform to LangGraph state format
states = transform_test_suite_review_to_state(result["jama_data"])

# Process each requirement through your graph
for state in states:
    graph_result = await your_graph.ainvoke(state)
```

### Integration with LangGraph StateGraph

```python
from langgraph.graph import StateGraph, START, END
from pyjama.langgraph.nodes import PyJamaDataSourceNode, PyJamaNodeConfig

# Configure PyJama
pyjama_config = PyJamaNodeConfig(
    host_address=os.getenv("JAMA_HOST_ADDRESS"),
    client_id=os.getenv("JAMA_CLIENT_ID"),
    client_secret=os.getenv("JAMA_CLIENT_SECRET")
)

# Build graph
sg = StateGraph(YourState)

# Add PyJama source node
jama_node = PyJamaDataSourceNode(pyjama_config)
sg.add_node("jama_source", jama_node)

# Add transformation node
def transform_node(state):
    jama_data = state.get("jama_data")
    if not jama_data:
        return {}
    transformed = transform_test_suite_review_to_state(jama_data)
    if transformed:
        return transformed[0]
    return {}

sg.add_node("transform", transform_node)

# Add your existing nodes
sg.add_node("decomposer", decomposer)
sg.add_node("summarizer", summarizer)
# ... etc

# Wire up the graph
sg.add_edge(START, "jama_source")
sg.add_edge("jama_source", "transform")
sg.add_edge("transform", "decomposer")
sg.add_edge("transform", "summarizer")
# ... etc

# Compile and run
graph = sg.compile()
result = await graph.ainvoke({"pyjama_request": request})
```

### Supported Request Types

1. **test_suite_review**: Organizes data by requirements with downstream test cases and design docs (baseline_id)
2. **test_case_review**: Organizes data by test cases with upstream requirements and design docs (baseline_id)
3. **hierarchical_trace**: Shows Software Req → System Req → User Needs hierarchy (project_name + identifiers)
4. **bidirectional_trace**: Per-requirement upstream hierarchy **and** downstream test cases + design docs (project_name + identifiers)
5. **rtm**: Flat traceability matrix across all five item categories (project_name + identifiers)

`PyJamaNodeConfig` accepts a `cache_mode` (`CacheMode.OFF` / `USE` / `REFRESH`, default `USE`) that is
passed through to `PyJamaTraceMatrix`, so node-routed requests read from `./cache/source/` first.

See `pyjama/langgraph/README.md` for detailed documentation, the per-workflow guides
`README_test_suite_reviewer.md`, `README_test_case_reviewer.md`, and `README_bidirectional_trace.md`,
and the runnable scripts in `examples/`.

## Project Structure

```
pyjama/                   # Core library
  jama/                   # Jama-specific modules
    pyjama.py             # PyJamaTraceMatrix class (main interface)
  langgraph/              # LangGraph integration
    __init__.py           # Public API exports
    nodes.py              # PyJamaDataSourceNode, PyJamaNodeConfig, PyJamaRequest
    transforms.py         # Pydantic models + transform_*_to_state() functions
    README.md             # LangGraph integration docs
  assemblers/             # Data assemblers
    jama_assemblers.py    # Test suite, test case, RTM, hierarchical, and bidirectional assemblers
  utils/                  # Shared utilities
    jama_constants.py     # Constants and type IDs
    jama_utils.py         # Helper functions
    jama_project_cache.py # Disk-backed project-name→ID cache
    gen_utils.py          # General utilities
    pd_utils.py           # DataFrame flatten helpers, Excel export
    text_utils.py         # CleanFrame — HTML/text preprocessing
    risk_table.py         # Risk table utilities
    proj_log.py           # Logging utilities
    proj_exception.py     # Exception logging decorator
scripts/                  # Standalone scripts
examples/                 # Runnable node-based workflow scripts
  test_suite_reviewer_example.py      # test_suite_review workflow
  test_case_reviewer_example.py       # test_case_review workflow
  bidirectional_trace_from_gids.py    # bidirectional_trace workflow
cache/                    # Tier-3 disk cache (gitignored)
  source/
    projects/             # Project name→ID directory
    baselines/            # Per-baseline reviewer artifacts
    identifiers/          # Per-identifier / RTM responses
tests/                    # Test suite
  integration/            # Integration tests (require Jama credentials)
  langgraph/              # LangGraph unit tests (mock-based)
  fixtures/               # Recorded JSONL test fixtures
```

## Testing

Run tests with pytest:

```bash
# All tests
uv run pytest tests/ -v

# Unit tests only (no Jama credentials needed)
uv run pytest tests/langgraph/ -v

# Integration tests (requires .env with JAMA_CLIENT_ID and JAMA_CLIENT_SECRET)
uv run pytest tests/integration/ -v
```

