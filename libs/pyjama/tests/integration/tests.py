"""
Integration tests for PyJama API with real Jama Client

These tests use dynamic parametrization to run against all fixtures in JSONL files.
Each fixture in the JSONL file will generate a separate test case.
"""

import time
from datetime import datetime
import pytest
from py_jama_rest_client.client import JamaClient
from pyjama.jama.pyjama import PyJamaTraceMatrix
from pyjama.utils.jama_constants import (
    TEST_CASES_KEY,
    REQUIREMENTS_KEY,
    DESIGN_DOCS_KEY,
    USER_NEEDS_KEY,
    SYSTEM_REQUIREMENTS_KEY,
    IN_REVIEW_BASELINE_KEY,
)


@pytest.mark.integration
def test_setup_jama_client(host_address, credentials):
    """Set up and return authenticated Jama client."""
    result = JamaClient(host_address, credentials, oauth=True, verify=True)
    assert isinstance(result, JamaClient)


@pytest.mark.integration
def test_instantiate_pyjamatracematrix(host_address, credentials):
    """Instantiate the PyJamaTraceMatrix class"""
    jama_client = JamaClient(host_address, credentials, oauth=True, verify=True)

    result = PyJamaTraceMatrix(
        jama_client,
        data_path="./data",
        log_path="./logs",
        max_concurrent=100,
    )
    assert isinstance(result, PyJamaTraceMatrix)


@pytest.mark.integration
def test_get_test_suite_reviewer_structure(pyjama_instance, test_suite_input, jsonl_recorders):
    """
    Integration test for get_test_suite_reviewer_structure using fixture inputs.
    
    This test runs for EACH input fixture in test_suite_reviewer_inputs.jsonl.
    The pytest_generate_tests hook in conftest.py automatically parametrizes
    this test to run once per fixture.
    
    Fixture Structure (from test_suite_reviewer_inputs.jsonl):
    - test_name (required): Descriptive name for the scenario
    - baseline_id (required): Baseline ID to test (e.g., "BASE-123")
    - expected_min_requirements (optional): Minimum expected requirement count
    - expected_keys (optional): Required keys in response
    - requirement_keys (optional): Required keys in requirement object
    - test_case_keys (optional): Required keys in test case objects
    - design_doc_keys (optional): Required keys in design doc objects
    
    Response files (test_suite_reviewer_response.jsonl) are optional, for reference only.
    Tests always fetch from real Jama API; response files are used only for documentation.
    
    Tests the complete workflow with real API calls:
    1. Parse baseline ID
    2. Fetch baseline versioned items
    3. Filter test cases by itemType
    4. Fetch upstream relationships
    5. Extract requirement IDs
    6. Fetch requirements and downstream items
    7. Assemble final structure
    """
    if test_suite_input is None:
        pytest.skip("No fixture provided")
    
    record_input, record_output = jsonl_recorders
    
    baseline_id = test_suite_input["baseline_id"]
    test_name = test_suite_input.get("test_name", "unnamed")
    
    # Record input
    input_data = {
        "test_name": "test_get_test_suite_reviewer_structure",
        "fixture_id": test_name,
        "timestamp": datetime.now().isoformat(),
        "baseline_id": baseline_id,
    }
    record_input(input_data)
    
    # Execute
    start_time = time.perf_counter()
    result = pyjama_instance.get_test_suite_reviewer_structure(baseline_id)
    duration = time.perf_counter() - start_time

    print(result)
    
    # Record output
    output_data = {
        "test_name": "test_get_test_suite_reviewer_structure",
        "fixture_id": test_name,
        "timestamp": datetime.now().isoformat(),
        "duration_seconds": round(duration, 3),
        "baseline_id": baseline_id,
        "requirements_count": len(result),
    }
    
    if len(result) > 0:
        output_data.update({
            "test_cases_count": sum(len(req[TEST_CASES_KEY]) for req in result),
            "test_cases_in_baseline": sum(
                sum(1 for tc in req[TEST_CASES_KEY] if tc.get(IN_REVIEW_BASELINE_KEY, False))
                for req in result
            ),
            "design_docs_count": sum(len(req[DESIGN_DOCS_KEY]) for req in result),
        })
    
    record_output(output_data)
    
    # Verify structure
    assert isinstance(result, list), "Result should be a list"
    
    # Check minimum requirements if specified
    expected_min = test_suite_input.get("expected_min_requirements", 0)
    assert len(result) >= expected_min, f"Expected at least {expected_min} requirements"
    
    if len(result) > 0:
        # Verify expected keys
        first_req = result[0]
        for key in test_suite_input.get("expected_keys", []):
            assert key in first_req, f"Should have '{key}' key"
        
        # Verify requirement structure
        req = first_req["requirement"]
        for key in test_suite_input.get("requirement_keys", []):
            assert key in req, f"Requirement should have '{key}' key"
        
        # Verify test cases structure
        test_cases = first_req[TEST_CASES_KEY]
        assert isinstance(test_cases, list), "Test cases should be a list"
        
        if len(test_cases) > 0 and "test_case_keys" in test_suite_input:
            first_tc = test_cases[0]
            for key in test_suite_input["test_case_keys"]:
                assert key in first_tc, f"Test case should have '{key}' key"
        
        # Verify design docs structure
        design_docs = first_req[DESIGN_DOCS_KEY]
        assert isinstance(design_docs, list), "Design docs should be a list"
        
        # Log summary
        total_reqs = len(result)
        total_tests = sum(len(req[TEST_CASES_KEY]) for req in result)
        baseline_tests = sum(
            sum(1 for tc in req[TEST_CASES_KEY] if tc.get(IN_REVIEW_BASELINE_KEY, False))
            for req in result
        )
        total_design = sum(len(req[DESIGN_DOCS_KEY]) for req in result)
        
        print(f"\n=== Test Suite Reviewer: {test_suite_input.get('test_name', 'unnamed')} ===")
        print(f"Baseline ID: {baseline_id}")
        print(f"Requirements: {total_reqs}")
        print(f"Test cases (total): {total_tests}")
        print(f"Test cases (in baseline): {baseline_tests}")
        print(f"Design documents: {total_design}")
    elif test_suite_input.get("expected_result") == []:
        # Handle empty baseline case
        print(f"\n=== Test Suite Reviewer: {test_suite_input.get('test_name', 'unnamed')} ===")
        print(f"Baseline ID: {baseline_id}")
        print(f"Result: Empty list (as expected)")


@pytest.mark.integration
def test_get_test_case_reviewer_structure(pyjama_instance, test_case_input, jsonl_recorders):
    """
    Integration test for get_test_case_reviewer_structure using fixture inputs.
    
    This test runs for EACH input fixture in test_case_reviewer_inputs.jsonl.
    
    Fixture Structure (from test_case_reviewer_inputs.jsonl):
    - test_name (required): Descriptive name for the scenario
    - baseline_id (required): Baseline ID to test
    - expected_min_test_cases (optional): Minimum expected test case count
    - expected_keys (optional): Required keys in response
    - test_case_keys (optional): Required keys in test case object
    - requirement_keys (optional): Required keys in requirement objects
    - expected_requirements_count (optional): Expected requirements per test case
    - design_doc_keys (optional): Required keys in design doc objects
    
    Response files (test_case_reviewer_response.jsonl) are optional, for reference only.
    Tests always fetch from real Jama API; response files are used only for documentation.
    
    Tests the complete workflow with real API calls:
    1. Parse baseline ID
    2. Fetch baseline versioned items
    3. Filter test cases by itemType
    4. Fetch upstream items for all test cases
    5. Separate upstream by type
    6. Assemble final structure
    """
    if test_case_input is None:
        pytest.skip("No fixture provided")
    
    record_input, record_output = jsonl_recorders
    
    baseline_id = test_case_input["baseline_id"]
    test_name = test_case_input.get("test_name", "unnamed")
    
    # Record input
    input_data = {
        "test_name": "test_get_test_case_reviewer_structure",
        "fixture_id": test_name,
        "timestamp": datetime.now().isoformat(),
        "baseline_id": baseline_id,
    }
    record_input(input_data)
    
    # Execute
    start_time = time.perf_counter()
    result = pyjama_instance.get_test_case_reviewer_structure(baseline_id)
    duration = time.perf_counter() - start_time

    print(result)
    
    # Record output
    output_data = {
        "test_name": "test_get_test_case_reviewer_structure",
        "fixture_id": test_name,
        "timestamp": datetime.now().isoformat(),
        "duration_seconds": round(duration, 3),
        "baseline_id": baseline_id,
        "test_cases_count": len(result),
    }
    
    if len(result) > 0:
        output_data.update({
            "requirements_count": sum(len(tc[REQUIREMENTS_KEY]) for tc in result),
            "design_docs_count": sum(len(tc[DESIGN_DOCS_KEY]) for tc in result),
            "test_cases_with_requirements": sum(1 for tc in result if len(tc[REQUIREMENTS_KEY]) > 0),
        })
    
    record_output(output_data)
    
    # Verify structure
    assert isinstance(result, list), "Result should be a list"
    
    # Check minimum test cases if specified
    expected_min = test_case_input.get("expected_min_test_cases", 0)
    assert len(result) >= expected_min, f"Expected at least {expected_min} test cases"
    
    if len(result) > 0:
        # Verify expected keys
        first_tc = result[0]
        for key in test_case_input.get("expected_keys", []):
            assert key in first_tc, f"Should have '{key}' key"
        
        # Verify test case structure
        test_case = first_tc["test_case"]
        for key in test_case_input.get("test_case_keys", []):
            assert key in test_case, f"Test case should have '{key}' key"
        
        # Verify requirements structure
        requirements = first_tc[REQUIREMENTS_KEY]
        assert isinstance(requirements, list), "Requirements should be a list"
        
        if len(requirements) > 0 and "requirement_keys" in test_case_input:
            first_req = requirements[0]
            for key in test_case_input["requirement_keys"]:
                assert key in first_req, f"Requirement should have '{key}' key"
        
        # Check expected requirements count if specified
        if "expected_requirements_count" in test_case_input:
            assert len(requirements) == test_case_input["expected_requirements_count"]
        
        # Verify design docs structure
        design_docs = first_tc[DESIGN_DOCS_KEY]
        assert isinstance(design_docs, list), "Design docs should be a list"
        
        # Log summary
        total_tcs = len(result)
        total_reqs = sum(len(tc[REQUIREMENTS_KEY]) for tc in result)
        total_design = sum(len(tc[DESIGN_DOCS_KEY]) for tc in result)
        tcs_with_reqs = sum(1 for tc in result if len(tc[REQUIREMENTS_KEY]) > 0)
        
        print(f"\n=== Test Case Reviewer: {test_case_input.get('test_name', 'unnamed')} ===")
        print(f"Baseline ID: {baseline_id}")
        print(f"Test cases: {total_tcs}")
        print(f"Test cases with requirements: {tcs_with_reqs}")
        print(f"Total requirements: {total_reqs}")
        print(f"Total design documents: {total_design}")


@pytest.mark.integration
def test_get_bidirectional_trace_from_gids(pyjama_instance, bidirectional_input, jsonl_recorders):
    """
    Integration test for get_bidirectional_trace_from_gids using fixture inputs.
    
    This test runs for EACH input fixture in bidirectional_trace_inputs.jsonl.
    The pytest_generate_tests hook in conftest.py automatically parametrizes
    this test to run once per fixture.
    
    Fixture Structure (from bidirectional_trace_inputs.jsonl):
    - test_name (required): Descriptive name for the scenario
    - project_name (required): Jama project name
    - identifiers (required): List of GIDs or document keys (e.g., ["GID-001"])
    - identifier_type (optional): Type of identifiers ("gid", "prq", "req", etc.)
    - expected_keys (optional): Required keys in response
    - expected_min_user_needs (optional): Minimum user needs count
    - expected_min_system_requirements (optional): Minimum system requirements count
    - expected_min_requirements (optional): Minimum software requirements count
    - expected_min_test_cases (optional): Minimum test cases count
    - expected_min_design_docs (optional): Minimum design docs count
    - requirement_keys (optional): Required keys in requirement objects
    
    Response files (bidirectional_trace_response.jsonl) are optional, for reference only.
    Tests always fetch from real Jama API; response files are used only for documentation.
    
    Tests the complete workflow with real API calls:
    1. Resolve project name to ID
    2. Fetch items from identifiers
    3. Fetch upstream and downstream relationships
    4. Partition by requirement type
    5. Assemble bidirectional trace
    """
    if bidirectional_input is None:
        pytest.skip("No fixture provided")
    
    record_input, record_output = jsonl_recorders
    
    project_name = bidirectional_input["project_name"]
    identifiers = bidirectional_input["identifiers"]
    test_name = bidirectional_input.get("test_name", "unnamed")
    
    # Record input
    input_data = {
        "test_name": "test_get_bidirectional_trace_from_gids",
        "fixture_id": test_name,
        "timestamp": datetime.now().isoformat(),
        "project_name": project_name,
        "identifiers": identifiers,
        "identifier_count": len(identifiers),
        "identifier_type": bidirectional_input.get("identifier_type", "unknown"),
    }
    record_input(input_data)
    
    # Execute
    start_time = time.perf_counter()
    result = pyjama_instance.get_bidirectional_trace_from_gids(identifiers, project_name)
    duration = time.perf_counter() - start_time
    
    print(result)
    
    # Record output
    output_data = {
        "test_name": "test_get_bidirectional_trace_from_gids",
        "fixture_id": test_name,
        "timestamp": datetime.now().isoformat(),
        "duration_seconds": round(duration, 3),
        "project_name": project_name,
        "identifier_count": len(identifiers),
        "user_needs_count": len(result.get(USER_NEEDS_KEY, [])),
        "system_requirements_count": len(result.get(SYSTEM_REQUIREMENTS_KEY, [])),
        "requirements_count": len(result.get(REQUIREMENTS_KEY, [])),
        "test_cases_count": len(result.get(TEST_CASES_KEY, [])),
        "design_docs_count": len(result.get(DESIGN_DOCS_KEY, [])),
    }
    record_output(output_data)
    
    # Verify structure
    assert isinstance(result, dict), "Result should be a dictionary"
    
    # Verify all expected keys
    for key in bidirectional_input.get("expected_keys", []):
        assert key in result, f"Result should have '{key}' key"
        assert isinstance(result[key], list), f"'{key}' should be a list"
    
    # Check minimum user needs if specified
    if "expected_min_user_needs" in bidirectional_input:
        assert len(result[USER_NEEDS_KEY]) >= bidirectional_input["expected_min_user_needs"]
    
    # Check minimum system requirements if specified
    if "expected_min_system_requirements" in bidirectional_input:
        assert len(result[SYSTEM_REQUIREMENTS_KEY]) >= bidirectional_input["expected_min_system_requirements"]
    
    # Check minimum requirements if specified
    if "expected_min_requirements" in bidirectional_input:
        assert len(result[REQUIREMENTS_KEY]) >= bidirectional_input["expected_min_requirements"]
    
    # Check minimum test cases if specified
    if "expected_min_test_cases" in bidirectional_input:
        assert len(result[TEST_CASES_KEY]) >= bidirectional_input["expected_min_test_cases"]
    
    # Check minimum design docs if specified
    if "expected_min_design_docs" in bidirectional_input:
        assert len(result[DESIGN_DOCS_KEY]) >= bidirectional_input["expected_min_design_docs"]
    
    # Verify requirement structure if present
    if len(result[REQUIREMENTS_KEY]) > 0 and "requirement_keys" in bidirectional_input:
        first_req = result[REQUIREMENTS_KEY][0]
        for key in bidirectional_input["requirement_keys"]:
            assert key in first_req, f"Requirement should have '{key}' key"
    
    # Log summary
    print(f"\n=== Bidirectional Trace: {bidirectional_input.get('test_name', 'unnamed')} ===")
    print(f"Project: {project_name}")
    print(f"Input identifiers ({bidirectional_input.get('identifier_type', 'unknown')}): {len(identifiers)}")
    print(f"User needs: {len(result[USER_NEEDS_KEY])}")
    print(f"System requirements: {len(result[SYSTEM_REQUIREMENTS_KEY])}")
    print(f"Software requirements: {len(result[REQUIREMENTS_KEY])}")
    print(f"Test cases: {len(result[TEST_CASES_KEY])}")
    print(f"Design documents: {len(result[DESIGN_DOCS_KEY])}")


@pytest.mark.integration
def test_invalid_baseline_id_format(pyjama_instance):
    """Test that invalid baseline ID format raises ValueError."""
    invalid_ids = ["INVALID-123", "base-123", "123", "BASE123", ""]
    
    for invalid_id in invalid_ids:
        with pytest.raises(ValueError, match="Invalid baseline_id format"):
            pyjama_instance.get_test_suite_reviewer_structure(invalid_id)
        
        with pytest.raises(ValueError, match="Invalid baseline_id format"):
            pyjama_instance.get_test_case_reviewer_structure(invalid_id)
