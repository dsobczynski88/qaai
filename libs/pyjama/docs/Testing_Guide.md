# Integration Tests Guide

This guide covers running and extending integration tests for PyJama's Jama Connect API wrappers.

## Overview

Integration tests make **real API calls** to Jama Connect using the `py-jama-rest-client` library. Tests are parametrized from JSONL fixture files, meaning **each row in a fixture file becomes a separate test case**.

### Three Core API Methods Being Tested

1. **`get_test_suite_reviewer_structure(baseline_id)`** - Fetches requirements with associated test cases and design docs
2. **`get_test_case_reviewer_structure(baseline_id)`** - Fetches test cases with associated requirements and design docs
3. **`get_bidirectional_trace_from_gids(identifiers, project_name)`** - Fetches bidirectional trace matrix from item identifiers

---

## Quick Start

### Prerequisites

Set up `.env` in the project root:

```dotenv
JAMA_CLIENT_ID=your_client_id
JAMA_CLIENT_SECRET=your_client_secret
JAMA_HOST_ADDRESS=https://your-jama-instance.jamacloud.com/
```

### Run Default Tests

```bash
# Run all integration tests with default fixtures
pytest tests/integration/ -m integration -v

# Run a specific test (all fixtures for that test)
pytest tests/integration/tests.py::test_get_test_suite_reviewer_structure -v

# Run a specific fixture by name
pytest "tests/integration/tests.py::test_get_test_suite_reviewer_structure[basic_test_suite]" -v

# Run with print statements visible
pytest tests/integration/ -m integration -v -s
```

### Run Custom Fixture File

```bash
# Use a custom JSONL file from tests/fixtures/
pytest tests/integration/ --fixture-file custom_examples/edge_cases.jsonl -v

# Can also use --fixture-file with relative paths
pytest tests/integration/ --fixture-file custom_examples/test_suite_reviewer_edge_cases.jsonl -v
```

---

## How Parametrization Works

### The Flow

```
Command Line
├─ pytest tests/integration/ --fixture-file custom_examples/edge_cases.jsonl
│
├─ conftest.py: pytest_generate_tests() hook
│  └─ Reads --fixture-file option value
│
├─ conftest.py: get_fixture_file_path()
│  └─ Resolves path relative to tests/fixtures/
│
├─ conftest.py: load_fixture_file()
│  └─ Parses JSONL, returns list of JSON objects
│
└─ Each JSON object parametrizes a separate test instance
   └─ test_name field becomes test ID in pytest output
```

### Example

If `custom_examples/edge_cases.jsonl` contains:

```jsonl
{"test_name": "basic_suite", "baseline_id": "BASE-12345"}
{"test_name": "empty_baseline", "baseline_id": "BASE-99999"}
{"test_name": "large_suite", "baseline_id": "BASE-11111"}
```

Running:
```bash
pytest tests/integration/tests.py::test_get_test_suite_reviewer_structure \
  --fixture-file custom_examples/edge_cases.jsonl -v
```

Produces **3 separate test executions**:
```
test_get_test_suite_reviewer_structure[basic_suite] PASSED
test_get_test_suite_reviewer_structure[empty_baseline] PASSED
test_get_test_suite_reviewer_structure[large_suite] PASSED
```

---

## Fixture Files

### Location & Naming

Fixture files are stored in `tests/fixtures/`:

```
tests/fixtures/
├── test_suite_reviewer_inputs.jsonl          # Default for test_suite_reviewer
├── test_case_reviewer_inputs.jsonl           # Default for test_case_reviewer
├── bidirectional_trace_inputs.jsonl          # Default for bidirectional_trace
│
└── custom_examples/                          # Custom fixture sets (optional)
    ├── test_suite_reviewer_edge_cases.jsonl
    ├── test_case_reviewer_edge_cases.jsonl
    └── bidirectional_trace_edge_cases.jsonl
```

### Default Fixture Selection

If you don't specify `--fixture-file`, tests use **default files** based on test function:

- `test_get_test_suite_reviewer_structure()` → `test_suite_reviewer_inputs.jsonl`
- `test_get_test_case_reviewer_structure()` → `test_case_reviewer_inputs.jsonl`
- `test_get_bidirectional_trace_from_gids()` → `bidirectional_trace_inputs.jsonl`

---

## Example Fixture Files

### 1. Test Suite Reviewer Inputs

**File:** `tests/fixtures/test_suite_reviewer_inputs.jsonl`

```jsonl
{"test_name": "basic_test_suite", "baseline_id": "BASE-84465", "expected_min_requirements": 1}
{"test_name": "large_baseline", "baseline_id": "BASE-LARGE", "expected_min_requirements": 10}
{"test_name": "empty_baseline", "baseline_id": "BASE-EMPTY"}
```

**Field Reference:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `test_name` | string | ✅ Yes | Descriptive test scenario name (becomes test ID) |
| `baseline_id` | string | ✅ Yes | Baseline ID to fetch (format: `BASE-12345`) |
| `expected_min_requirements` | integer | ❌ No | Minimum expected requirement count; test fails if not met |
| `expected_keys` | array | ❌ No | Keys that must exist in response (default: `["requirement", "test_cases", "design_docs"]`) |
| `requirement_keys` | array | ❌ No | Keys that must exist in each requirement object |
| `test_case_keys` | array | ❌ No | Keys that must exist in each test case object |
| `design_doc_keys` | array | ❌ No | Keys that must exist in each design doc object |

**Example with validation:**

```jsonl
{
  "test_name": "strict_validation",
  "baseline_id": "BASE-84465",
  "expected_min_requirements": 5,
  "expected_keys": ["requirement", "test_cases", "design_docs"],
  "requirement_keys": ["req_id", "text"],
  "test_case_keys": ["test_id", "description", "setup", "steps", "expectedResults"],
  "design_doc_keys": ["doc_id", "name", "description"]
}
```

**Response Structure** (what your test receives):

```python
result = [
  {
    "requirement": {
      "req_id": "REQ-PUMP-001",
      "text": "The rate-control loop shall execute at 10 Hz..."
    },
    "test_cases": [
      {
        "test_id": "TC-PUMP-201",
        "description": "Verify rate-control loop execution frequency",
        "setup": "Pump in standard infusion mode...",
        "steps": "Step 1. Start measurement\nStep 2. Monitor...",
        "expectedResults": "ExpectedResult 1. Average frequency is 10 Hz...",
        "in_review_baseline": True
      }
    ],
    "design_docs": [
      {
        "doc_id": "DD-PUMP-RC-001",
        "name": "Rate Control Loop Design",
        "description": "Detailed design specification..."
      }
    ]
  }
]
```

---

### 2. Test Case Reviewer Inputs

**File:** `tests/fixtures/test_case_reviewer_inputs.jsonl`

```jsonl
{"test_name": "basic_test_cases", "baseline_id": "BASE-84465", "expected_min_test_cases": 1}
{"test_name": "large_test_cases", "baseline_id": "BASE-LARGE", "expected_min_test_cases": 10}
{"test_name": "empty_test_cases", "baseline_id": "BASE-EMPTY"}
```

**Field Reference:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `test_name` | string | ✅ Yes | Descriptive test scenario name (becomes test ID) |
| `baseline_id` | string | ✅ Yes | Baseline ID to fetch (format: `BASE-12345`) |
| `expected_min_test_cases` | integer | ❌ No | Minimum expected test case count; test fails if not met |
| `expected_keys` | array | ❌ No | Keys that must exist in response (default: `["test_case", "requirements", "design_docs"]`) |
| `test_case_keys` | array | ❌ No | Keys that must exist in test case object |
| `requirement_keys` | array | ❌ No | Keys that must exist in each requirement object |
| `design_doc_keys` | array | ❌ No | Keys that must exist in each design doc object |

**Example with validation:**

```jsonl
{
  "test_name": "strict_validation",
  "baseline_id": "BASE-84465",
  "expected_min_test_cases": 3,
  "expected_keys": ["test_case", "requirements", "design_docs"],
  "test_case_keys": ["test_id", "description", "setup", "steps", "expectedResults"],
  "requirement_keys": ["req_id", "text"],
  "design_doc_keys": ["doc_id", "name", "description"]
}
```

**Response Structure** (what your test receives):

```python
result = [
  {
    "test_case": {
      "test_id": "TC-PUMP-201",
      "description": "Verify rate-control loop execution frequency",
      "setup": "Pump in standard infusion mode...",
      "steps": "Step 1. Start measurement\nStep 2. Monitor...",
      "expectedResults": "ExpectedResult 1. Average frequency is 10 Hz..."
    },
    "requirements": [
      {
        "req_id": "REQ-PUMP-001",
        "text": "The rate-control loop shall execute at 10 Hz..."
      }
    ],
    "design_docs": [
      {
        "doc_id": "DD-PUMP-RC-001",
        "name": "Rate Control Loop Design",
        "description": "Detailed design specification..."
      }
    ]
  }
]
```

---

### 3. Bidirectional Trace Inputs

**File:** `tests/fixtures/bidirectional_trace_inputs.jsonl`

```jsonl
{"test_name": "gid_basic", "project_name": "Medical Device Platform", "identifiers": ["GID-2634456"], "identifier_type": "gid"}
{"test_name": "multiple_gids", "project_name": "Medical Device Platform", "identifiers": ["GID-2634456", "GID-2634457"], "identifier_type": "gid"}
{"test_name": "mixed_identifiers", "project_name": "Medical Device Platform", "identifiers": ["GID-2634456", "PRQ-001"]}
```

**Field Reference:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `test_name` | string | ✅ Yes | Descriptive test scenario name (becomes test ID) |
| `project_name` | string | ✅ Yes | Jama project name (must match exact project name in Jama) |
| `identifiers` | array | ✅ Yes | List of item identifiers (GIDs, document keys, etc.) |
| `identifier_type` | string | ❌ No | Type of identifiers ("gid", "prq", "req", etc.); used for documentation |
| `expected_keys` | array | ❌ No | Keys that must exist in response (default: `["user_needs", "system_requirements", "requirements", "test_cases", "design_docs"]`) |
| `expected_min_user_needs` | integer | ❌ No | Minimum expected user needs count |
| `expected_min_system_requirements` | integer | ❌ No | Minimum expected system requirements count |
| `expected_min_requirements` | integer | ❌ No | Minimum expected software requirements count |
| `expected_min_test_cases` | integer | ❌ No | Minimum expected test cases count |
| `expected_min_design_docs` | integer | ❌ No | Minimum expected design docs count |
| `requirement_keys` | array | ❌ No | Keys that must exist in requirement objects |

**Example with validation:**

```jsonl
{
  "test_name": "comprehensive_trace",
  "project_name": "Medical Device Platform",
  "identifiers": ["GID-2634456"],
  "identifier_type": "gid",
  "expected_min_user_needs": 1,
  "expected_min_system_requirements": 2,
  "expected_min_requirements": 3,
  "expected_min_test_cases": 5,
  "expected_min_design_docs": 1,
  "requirement_keys": ["req_id", "text"]
}
```

**Response Structure** (what your test receives):

```python
result = {
  "user_needs": [
    {
      "req_id": "UND-MDP-001",
      "text": "As a Hospital Administrator, I need to generate PDF reports..."
    }
  ],
  "system_requirements": [
    {
      "req_id": "PRQ-MDP-001",
      "text": "The system shall generate PDF reports..."
    }
  ],
  "requirements": [
    {
      "req_id": "GID-2634456",
      "text": "The reporting solution shall generate PDF reports..."
    }
  ],
  "test_cases": [
    {
      "test_id": "TC-REP-001",
      "description": "Verify PDF generation with valid data",
      "setup": "...",
      "steps": "...",
      "expectedResults": "..."
    }
  ],
  "design_docs": [
    {
      "doc_id": "DD-REP-001",
      "name": "PDF Reporting Design",
      "description": "Detailed design of PDF reporting feature..."
    }
  ]
}
```

---

## Working with Fixture Files

### Adding a New Test Scenario

1. **Identify the test type:**
   - Test suite → use `test_suite_reviewer_inputs.jsonl`
   - Test cases → use `test_case_reviewer_inputs.jsonl`
   - Bidirectional trace → use `bidirectional_trace_inputs.jsonl`

2. **Add a new line to the JSONL file:**

   ```jsonl
   {"test_name": "my_scenario", "baseline_id": "BASE-12345"}
   ```

3. **Run the tests:**

   ```bash
   pytest tests/integration/tests.py::test_get_test_suite_reviewer_structure -v
   ```

   Your new scenario will automatically be picked up and executed.

### Creating a Custom Fixture File

1. **Create the file** in `tests/fixtures/custom_examples/`:

   ```bash
   mkdir -p tests/fixtures/custom_examples
   touch tests/fixtures/custom_examples/my_edge_cases.jsonl
   ```

2. **Add scenarios** (copy from defaults and modify):

   ```jsonl
   {"test_name": "edge_case_1", "baseline_id": "BASE-EDGE1"}
   {"test_name": "edge_case_2", "baseline_id": "BASE-EDGE2"}
   ```

3. **Run with the custom file:**

   ```bash
   pytest tests/integration/ --fixture-file custom_examples/my_edge_cases.jsonl -v
   ```

### Validating Fixture Format

```bash
# Ensure JSONL is valid JSON
python -c "
import json
with open('tests/fixtures/test_suite_reviewer_inputs.jsonl') as f:
    for i, line in enumerate(f, 1):
        try:
            json.loads(line)
        except json.JSONDecodeError as e:
            print(f'Line {i}: {e}')
"
```

---

## Test Output and Logging

### Summary Output

Each test logs a summary with statistics. Example:

```
=== Test Suite Reviewer: basic_test_suite ===
Baseline ID: BASE-84465
Requirements: 42
Test cases (total): 156
Test cases (in baseline): 98
Design documents: 23
```

### Recording Input/Output

Tests automatically record:
- **Inputs:** What parameters were used
- **Outputs:** Response statistics and duration

These are saved to:
```
logs/run-{timestamp}/inputs.jsonl
logs/run-{timestamp}/outputs.jsonl
```

Each line is a JSON object containing test metadata.

---

## Optional: Response Files (Reference Only)

Response files are **optional documentation** and are **not required for tests to run**.

### When to Use Response Files

Response files are useful for:
- **Documenting** expected API response structure
- **Offline reference** when you don't have Jama access
- **Version control** to track response schema changes over time

### Available Response Files

- `tests/fixtures/test_suite_reviewer_response.jsonl` - Sample responses from test suite reviewer
- `tests/fixtures/test_case_reviewer_response.jsonl` - Sample responses from test case reviewer
- `tests/fixtures/bidirectional_trace_response.jsonl` - Sample responses from bidirectional trace

### Response File Format

Each line is a complete response object from the API. Example:

```jsonl
{"requirement": {"req_id": "REQ-001", "text": "..."}, "test_cases": [...], "design_docs": [...]}
{"requirement": {"req_id": "REQ-002", "text": "..."}, "test_cases": [...], "design_docs": [...]}
```

### Adding Real Responses to Response Files

If you want to document actual responses:

1. Run a test with `--capture=no` to see full output:
   ```bash
   pytest tests/integration/tests.py::test_get_test_suite_reviewer_structure[my_scenario] -v -s
   ```

2. Copy the response object output

3. Append to the appropriate response file:
   ```bash
   echo '{"requirement": {...}, ...}' >> tests/fixtures/test_suite_reviewer_response.jsonl
   ```

---

## Troubleshooting

### "No fixtures found" Error

**Problem:** Tests are being skipped because no fixtures are loaded.

**Solution:** 
- Check that fixture file exists in `tests/fixtures/`:
  ```bash
  ls -la tests/fixtures/test_suite_reviewer_inputs.jsonl
  ```
- If using `--fixture-file`, verify the path is relative to `tests/fixtures/`:
  ```bash
  # ✅ Correct
  pytest tests/integration/ --fixture-file custom_examples/my_file.jsonl
  
  # ❌ Incorrect (absolute path won't work)
  pytest tests/integration/ --fixture-file /absolute/path/my_file.jsonl
  ```

### JSONL Parse Error

**Problem:** `json.JSONDecodeError: Expecting value`

**Solution:** Validate JSONL file format (each line must be valid JSON):
```bash
# Using jq (if installed)
jq . tests/fixtures/test_suite_reviewer_inputs.jsonl

# Using Python
python -c "
import json
with open('tests/fixtures/test_suite_reviewer_inputs.jsonl') as f:
    for line in f:
        json.loads(line)
print('✓ Valid JSONL')
"
```

### Test Hangs or Times Out

**Problem:** Test takes too long to complete.

**Causes:**
- Large baseline with many requirements/test cases
- Jama API rate limiting (max 500 requests/min)
- Network latency

**Solution:**
- Reduce `expected_min_*` thresholds in fixtures
- Use smaller baselines for quick validation
- Check network connectivity to Jama instance

### Validation Fails Unexpectedly

**Problem:** Test fails with assertion error on expected counts/keys.

**Solution:**
- Remove optional validation fields and re-run:
  ```jsonl
  {"test_name": "my_test", "baseline_id": "BASE-12345"}
  ```
- If still failing, check that baseline/project/identifier exists in Jama
- Review the test failure message for missing keys

---

## Best Practices

### 1. Use Descriptive Test Names

✅ Good:
```jsonl
{"test_name": "pump_control_requirements", "baseline_id": "BASE-12345"}
```

❌ Poor:
```jsonl
{"test_name": "test1", "baseline_id": "BASE-12345"}
```

### 2. Organize Fixtures by Complexity

```jsonl
# File: test_suite_reviewer_inputs.jsonl
# Simple cases first, edge cases after

{"test_name": "single_requirement", "baseline_id": "BASE-SIMPLE"}
{"test_name": "medium_baseline", "baseline_id": "BASE-MEDIUM"}
{"test_name": "large_baseline", "baseline_id": "BASE-LARGE"}
{"test_name": "empty_baseline", "baseline_id": "BASE-EMPTY"}
```

### 3. Use Custom Fixtures for Regression Testing

```bash
# Create a regression suite for known issues
mkdir -p tests/fixtures/regression
touch tests/fixtures/regression/issue_123_regression.jsonl

# Run it specifically
pytest tests/integration/ --fixture-file regression/issue_123_regression.jsonl -v
```

### 4. Include Real Baseline/Project Data

Always use actual IDs from your Jama instance. Placeholder values like `"BASE-XXXXX"` will cause API errors.

### 5. Validate Before Committing

```bash
# Quick validation of fixture format
pytest tests/integration/ --collect-only -q

# Actually run against Jama
pytest tests/integration/ -v
```

---

## For More Information

- **Implementation Details:** See `tests/integration/tests.py`
- **Configuration & Hooks:** See `tests/conftest.py`
- **API Client:** See `pyjama.jama.pyjama.PyJamaTraceMatrix` class
- **Development Guide:** See `CLAUDE.md`
