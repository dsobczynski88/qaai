# LangGraph Integration

This module provides LangGraph-compatible nodes for fetching Jama Connect data in real-time, enabling external LangGraph applications to replace static JSONL file inputs with live API calls.

## Overview

The PyJama LangGraph integration consists of three main components:

1. **PyJamaDataSourceNode**: A LangGraph-compatible node that fetches data from Jama
2. **PyJamaRequest**: Configuration model for specifying what data to fetch
3. **Transform utilities**: Functions to convert raw Jama data into LangGraph state format

## Installation

### In Your External LangGraph Project

```bash
# Install from git repository
pip install git+https://github.com/your-org/pyjama-fastapi.git

# Or if using uv
uv add git+https://github.com/your-org/pyjama-fastapi.git
```

### Environment Setup

Create a `.env` file with your Jama credentials:

```env
JAMA_HOST_ADDRESS=https://your-org.jamacloud.com/
JAMA_CLIENT_ID=your_client_id
JAMA_CLIENT_SECRET=your_client_secret
```

## Quick Start

### Basic Usage

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

## Request Types

### 1. Test Suite Review

Organizes data by requirements, showing all downstream test cases and design documents.

```python
request = PyJamaRequest(
    request_type="test_suite_review",
    baseline_id="BASE-84398"
)

result = await jama_node({"pyjama_request": request})
states = transform_test_suite_review_to_state(result["jama_data"])

# Each state contains:
# - requirement: Requirement model
# - test_cases: List[TestCase]
# - design_docs: List[DesignDoc]
```

### 2. Test Case Review

Organizes data by test cases, showing all upstream requirements and design documents.

```python
request = PyJamaRequest(
    request_type="test_case_review",
    baseline_id="BASE-84398"
)

result = await jama_node({"pyjama_request": request})
states = transform_test_case_review_to_state(result["jama_data"])

# Each state contains:
# - test_case: TestCase model
# - requirements: List[Requirement]
# - design_docs: List[DesignDoc]
```

### 3. Hierarchical Trace

Builds hierarchical traceability: Software Requirements → System Requirements → User Needs.

```python
request = PyJamaRequest(
    request_type="hierarchical_trace",
    project_name="Patient Safety Platform",
    identifiers=["GID-2788627", "GID-2788628"]
)

result = await jama_node({"pyjama_request": request})
states = transform_hierarchical_trace_to_state(result["jama_data"])

# Each state contains:
# - requirement: Requirement model (software requirement)
# - system_requirements: List[SystemRequirement] (with nested user_needs)
```

### 4. Bidirectional Trace

Per-requirement upstream hierarchy (system requirements → user needs) **and** downstream
test cases + design docs. Returns the raw structure in `jama_data` (no transform helper).

```python
request = PyJamaRequest(
    request_type="bidirectional_trace",
    project_name="Patient Safety Platform",
    identifiers=["GID-2788627", "GID-2788628"]
)

result = await jama_node({"pyjama_request": request})
# result["jama_data"]: list of
#   {requirement, system_requirements:[{...user_needs}], test_cases:[...], design_docs:[...]}
```

### 5. RTM

Flat traceability matrix across all five item categories. Returns a **dict** (not a list).

```python
request = PyJamaRequest(
    request_type="rtm",
    project_name="Patient Safety Platform",
    identifiers=["GID-2788627", "GID-2788628"]
)

result = await jama_node({"pyjama_request": request})
# result["jama_data"]: {user_needs, system_requirements, requirements, test_cases, design_docs}
```

## Caching

`PyJamaNodeConfig` accepts `cache_mode` (`CacheMode.OFF` / `USE` / `REFRESH`, default `USE`),
passed through to `PyJamaTraceMatrix`. With `USE`, the first request writes Tier-3 artifacts
under `./cache/source/` and subsequent identical requests are served from disk without hitting
Jama. Use `OFF` to always fetch, or `REFRESH` to force one fresh fetch per session.

```python
from pyjama.utils.cache_manager import CacheMode

config = PyJamaNodeConfig(
    host_address=os.getenv("JAMA_HOST_ADDRESS"),
    client_id=os.getenv("JAMA_CLIENT_ID"),
    client_secret=os.getenv("JAMA_CLIENT_SECRET"),
    cache_mode=CacheMode.USE,
)
```

## Integration with LangGraph

### Pattern 1: PyJama Node at Graph Start

Add the PyJama node at the beginning of your graph to fetch data dynamically:

```python
from langgraph.graph import StateGraph, START, END
from pyjama.langgraph.nodes import PyJamaDataSourceNode, PyJamaNodeConfig
from pyjama.langgraph.transforms import transform_test_suite_review_to_state

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
    
    # Transform and return first requirement
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

# Compile
graph = sg.compile()

# Run with PyJama data source
request = PyJamaRequest(
    request_type="test_suite_review",
    baseline_id="BASE-84398"
)

result = await graph.ainvoke({"pyjama_request": request})
```

### Pattern 2: Batch Processing

Fetch data once and process multiple items through your graph:

```python
# Fetch data
jama_node = PyJamaDataSourceNode(config)
result = await jama_node({
    "pyjama_request": PyJamaRequest(
        request_type="test_suite_review",
        baseline_id="BASE-84398"
    )
})

# Transform to states
states = transform_test_suite_review_to_state(result["jama_data"])

# Process each requirement
results = []
for state in states:
    graph_result = await your_graph.ainvoke(state)
    results.append(graph_result)
```

### Pattern 3: Conditional PyJama Source

Enable/disable PyJama source via configuration:

```python
class RTMReviewerRunnable:
    def __init__(
        self,
        client: RateLimitOpenAIClient,
        model: str,
        pyjama_config: Optional[PyJamaNodeConfig] = None,  # Optional
    ):
        self.pyjama_config = pyjama_config
        self.graph = self.build()

    def build(self) -> Runnable:
        sg = StateGraph(RTMReviewState)

        # Conditionally add PyJama nodes
        if self.pyjama_config:
            pyjama_node = PyJamaDataSourceNode(self.pyjama_config)
            sg.add_node("pyjama_source", pyjama_node)
            sg.add_node("transform", transform_node)
            
            sg.add_edge(START, "pyjama_source")
            sg.add_edge("pyjama_source", "transform")
            
            decomposer_source = "transform"
        else:
            # Original behavior: START directly to decomposer
            decomposer_source = START

        # Add existing nodes
        sg.add_node("decomposer", decomposer)
        sg.add_node("summarizer", summarizer)
        # ... etc

        # Connect from source
        sg.add_edge(decomposer_source, "decomposer")
        sg.add_edge(decomposer_source, "summarizer")
        # ... etc

        return sg.compile()
```

## State Models

### Requirement

```python
class Requirement(BaseModel):
    req_id: str  # e.g., "REQ-PUMP-101", "GID-2788627"
    text: str    # Cleaned requirement text
```

### TestCase

```python
class TestCase(BaseModel):
    test_id: str
    description: str
    setup: str
    steps: str
    expectedResults: str
    in_review_baseline: bool
```

### DesignDoc

```python
class DesignDoc(BaseModel):
    doc_id: str
    name: str
    description: str
```

### SystemRequirement

```python
class SystemRequirement(BaseModel):
    req_id: str
    text: str
    user_needs: List[Requirement]  # Nested user needs
```

## Configuration Options

### PyJamaNodeConfig

```python
config = PyJamaNodeConfig(
    host_address="https://your-org.jamacloud.com/",  # Required
    client_id="your_client_id",                      # Required
    client_secret="your_client_secret",              # Required
    data_path="./data",                              # Optional, default: "./data"
    log_path="logs",                                 # Optional, default: "logs"
    max_concurrent=100,                              # Optional, default: 100, range: 1-500
    oauth=True                                       # Optional, default: True
)
```

### PyJamaRequest

```python
# Test Suite Review
request = PyJamaRequest(
    request_type="test_suite_review",
    baseline_id="BASE-84398",
    api_id_key=None,           # Optional
    design_typekey=None,       # Optional, default: "DES"
    testcase_typekey=None      # Optional, default: "TEST"
)

# Test Case Review
request = PyJamaRequest(
    request_type="test_case_review",
    baseline_id="BASE-84398",
    api_id_key=None,           # Optional
    design_typekey=None,       # Optional, default: "DES"
    requirement_typekeys=None  # Optional, default: ["REQ", "PRQ"]
)

# Hierarchical Trace
request = PyJamaRequest(
    request_type="hierarchical_trace",
    project_name="Patient Safety Platform",
    identifiers=["GID-2788627", "GID-2788628"],
    api_id_key=None,           # Optional
    user_need_typekey=None,    # Optional, default: "UND"
    prq_type_field=None        # Optional, default: "PRQ_type$63"
)
```

## Error Handling

The PyJama node includes comprehensive error handling:

```python
try:
    result = await jama_node({"pyjama_request": request})
except ValueError as e:
    # Invalid request configuration
    print(f"Configuration error: {e}")
except Exception as e:
    # Jama API error
    print(f"API error: {e}")
```

## Performance Considerations

### Concurrent API Requests

The `max_concurrent` parameter controls how many parallel API requests are made:

```python
config = PyJamaNodeConfig(
    # ... other config
    max_concurrent=100  # Tune based on your Jama instance limits
)
```

### Caching

For repeated requests, consider caching the results:

```python
from functools import lru_cache

@lru_cache(maxsize=128)
def get_cached_jama_data(baseline_id: str):
    # Cache results by baseline_id
    pass
```

### Batch Processing

Process multiple requirements in batches to optimize throughput:

```python
# Fetch once
result = await jama_node({"pyjama_request": request})
states = transform_test_suite_review_to_state(result["jama_data"])

# Process in batches
batch_size = 10
for i in range(0, len(states), batch_size):
    batch = states[i:i+batch_size]
    # Process batch concurrently
    tasks = [your_graph.ainvoke(state) for state in batch]
    results = await asyncio.gather(*tasks)
```

## Examples

See the runnable node-based scripts in `examples/` for complete working examples:

- `examples/test_suite_reviewer_example.py` — Test Suite Review (see `README_test_suite_reviewer.md`)
- `examples/test_case_reviewer_example.py` — Test Case Review (see `README_test_case_reviewer.md`)
- `examples/bidirectional_trace_from_gids.py` — Bidirectional Trace (see `README_bidirectional_trace.md`)

## Troubleshooting

### "State must contain 'pyjama_request' key"

Make sure you're passing the request in the initial state:

```python
result = await graph.ainvoke({"pyjama_request": request})
```

### "baseline_id is required for request_type='test_suite_review'"

Ensure you're providing the required parameters for each request type:

```python
# Test suite/case review requires baseline_id
request = PyJamaRequest(
    request_type="test_suite_review",
    baseline_id="BASE-84398"  # Required
)

# Hierarchical trace requires project_name and identifiers
request = PyJamaRequest(
    request_type="hierarchical_trace",
    project_name="Your Project",  # Required
    identifiers=["GID-123"]       # Required
)
```

### Jama API Connection Issues

Check your credentials and host address:

```python
# Verify credentials are loaded
print(os.getenv("JAMA_CLIENT_ID"))
print(os.getenv("JAMA_CLIENT_SECRET"))
print(os.getenv("JAMA_HOST_ADDRESS"))

# Test connection
config = PyJamaNodeConfig(
    host_address=os.getenv("JAMA_HOST_ADDRESS"),
    client_id=os.getenv("JAMA_CLIENT_ID"),
    client_secret=os.getenv("JAMA_CLIENT_SECRET")
)
node = PyJamaDataSourceNode(config)
# If initialization succeeds, credentials are valid
```

## API Reference

### PyJamaDataSourceNode

```python
class PyJamaDataSourceNode:
    """LangGraph-compatible node for fetching Jama data."""
    
    def __init__(self, config: PyJamaNodeConfig):
        """Initialize with configuration."""
        
    async def __call__(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute node: fetch data from Jama.
        
        Args:
            state: Must contain 'pyjama_request' key
            
        Returns:
            Dict with 'jama_data' and 'jama_metadata' keys
        """
```

### Transform Functions

```python
def transform_test_suite_review_to_state(
    jama_data: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Transform test suite review data to LangGraph state format."""

def transform_test_case_review_to_state(
    jama_data: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Transform test case review data to LangGraph state format."""

def transform_hierarchical_trace_to_state(
    jama_data: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Transform hierarchical trace data to LangGraph state format."""
```

## Contributing

When adding new request types or transform functions:

1. Add the request type to `PyJamaRequest.request_type` Literal
2. Implement the fetch method in `PyJamaDataSourceNode`
3. Create a corresponding transform function in `transforms.py`
4. Add a runnable script under `examples/`
5. Update this README

## License

See main project LICENSE file.
