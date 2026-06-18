# Integration Tests for PyJama API

This directory contains integration tests that make real API calls to Jama Connect using the `py-jama-rest-client` library.

## Overview

The integration tests use **fixture-based parametrization** to run multiple test scenarios from JSONL files. Each line in a JSONL fixture file represents a separate test case that will be executed.

## Fixture Files

Fixture files are located in `tests/fixtures/` and use JSONL (JSON Lines) format:

- **`test_suite_reviewer_fixtures.jsonl`** - Fixtures for `get_test_suite_reviewer_structure()` tests
- **`test_case_reviewer_fixtures.jsonl`** - Fixtures for `get_test_case_reviewer_structure()` tests  
- **`rtm_from_gids_fixtures.jsonl`** - Fixtures for `get_rtm_from_gids()` tests

## How It Works

### Dynamic Parametrization

The `pytest_generate_tests()` hook in `tests/conftest.py` automatically:

1. Reads all fixtures from the JSONL file
2. Creates a separate test case for each fixture
3. Uses the `test_name` field as the test ID for better output

This means **all fixtures in the JSONL file will be tested**, not just the first one.

### Example

If `test_suite_reviewer_fixtures.jsonl` contains:

```jsonl
{"test_name": "basic_test_suite", "baseline_id": "BASE-12345", ...}
{"test_name": "empty_baseline", "baseline_id": "BASE-67890", ...}
{"test_name": "large_baseline", "baseline_id": "BASE-11111", ...}
```

Running `pytest tests/integration/tests.py::test_get_test_suite_reviewer_structure` will execute **3 separate tests**:

```
tests/integration/tests.py::test_get_test_suite_reviewer_structure[basic_test_suite] PASSED
tests/integration/tests.py::test_get_test_suite_reviewer_structure[empty_baseline] PASSED
tests/integration/tests.py::test_get_test_suite_reviewer_structure[large_baseline] PASSED
```

## Running Tests

### Run all integration tests:
```bash
pytest tests/integration/ -m integration -v
```

### Run specific test function (all fixtures):
```bash
pytest tests/integration/tests.py::test_get_test_suite_reviewer_structure -v
```

### Run specific fixture by test name:
```bash
pytest tests/integration/tests.py::test_get_test_suite_reviewer_structure[basic_test_suite] -v
```

### Run with output capture disabled (see print statements):
```bash
pytest tests/integration/ -m integration -v -s
```

## Fixture File Format

### Test Suite Reviewer Fixtures

```jsonl
{
  "test_name": "basic_test_suite",
  "baseline_id": "BASE-XXXXX",
  "project_name": "PROJECT_NAME_PLACEHOLDER",
  "expected_min_requirements": 1,
  "expected_keys": ["requirement", "test_cases", "design_docs"],
  "requirement_keys": ["req_id", "text"],
  "test_case_keys": ["test_id", "description", "setup", "steps", "expectedResults", "in_review_baseline"],
  "design_doc_keys": ["doc_id", "name", "description"]
}
```

### Test Case Reviewer Fixtures

```jsonl
{
  "test_name": "basic_test_case_reviewer",
  "baseline_id": "BASE-XXXXX",
  "project_name": "PROJECT_NAME_PLACEHOLDER",
  "expected_min_test_cases": 1,
  "expected_keys": ["test_case", "requirements", "design_docs"],
  "test_case_keys": ["test_id", "description", "setup", "steps", "expectedResults"],
  "requirement_keys": ["req_id", "text"],
  "design_doc_keys": ["doc_id", "name", "description"]
}
```

### RTM from GIDs Fixtures

```jsonl
{
  "test_name": "rtm_with_gids",
  "project_name": "PROJECT_NAME_PLACEHOLDER",
  "identifiers": ["GID-PLACEHOLDER1", "GID-PLACEHOLDER2"],
  "identifier_type": "gids",
  "expected_keys": ["user_needs", "system_requirements", "requirements", "test_cases", "design_docs"],
  "expected_min_requirements": 2,
  "requirement_keys": ["req_id", "text"],
  "test_case_keys": ["test_id", "description"],
  "design_doc_keys": ["doc_id", "name", "description"]
}
```

## Configuration

Tests require a `.env` file in the project root with:

```
JAMA_CLIENT_ID = <your_client_id>
JAMA_CLIENT_SECRET = <your_client_secret>
JAMA_HOST_ADDRESS = https://your-jama-instance.jamacloud.com/
```

`JAMA_HOST_ADDRESS` is read by the `host_address` fixture (integration tests skip if it is unset);
`data_path`/`log_path`/`max_concurrent` use fixture defaults (`./data`, `./logs`, `100`). Credentials
are loaded via `pyjama.utils.jama_utils.get_jama_credentials()`.

## Adding New Test Scenarios

To add a new test scenario:

1. Add a new line to the appropriate JSONL fixture file
2. Fill in the required fields (`test_name`, `baseline_id` or `identifiers`, etc.)
3. Replace placeholder values with real data from your Jama instance
4. Run the tests - the new scenario will automatically be included

## Test Output

Each test logs a summary with statistics:

```
=== Test Suite Reviewer: basic_test_suite ===
Baseline ID: BASE-84398
Requirements: 42
Test cases (total): 156
Test cases (in baseline): 98
Design documents: 23
```

## Notes

- Tests make **real API calls** to Jama Connect
- Ensure you have valid credentials and network access
- Tests may take time depending on data size and API rate limits
- The `max_concurrent` setting controls parallel API requests (1-500)
