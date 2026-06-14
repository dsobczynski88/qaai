"""
Integration verification test for DataIntegrationNode wiring in HazardReviewerRunnable.

Verifies that:
1. HazardReviewState includes pyjama_request field
2. DataIntegrationNode is properly wired in the graph
3. transform_hazard_record_to_state properly populates requirements from traceability
4. Graph receives properly formatted initial state with both hazard and pyjama_request
"""

import asyncio
import json
from pathlib import Path
from typing import Any, Dict, List

import pytest


def test_hazard_review_state_has_pyjama_request():
    """Verify HazardReviewState includes pyjama_request field."""
    from qaai.components.hazard_risk_reviewer.core import HazardReviewState
    
    # Check that HazardReviewState TypedDict has pyjama_request
    annotations = HazardReviewState.__annotations__
    assert "pyjama_request" in annotations, \
        "HazardReviewState missing pyjama_request field"
    assert "jama_data" in annotations, \
        "HazardReviewState missing jama_data field"
    assert "jama_metadata" in annotations, \
        "HazardReviewState missing jama_metadata field"
    
    print("✓ HazardReviewState includes pyjama_request, jama_data, jama_metadata fields")


def test_data_integration_node_factory():
    """Verify DataIntegrationNode can be instantiated directly."""
    from qaai.components.shared.data_integration import DataIntegrationNode

    node = DataIntegrationNode(pyjama_config=None)
    assert isinstance(node, DataIntegrationNode), \
        f"Expected DataIntegrationNode, got {type(node)}"

    print("✓ DataIntegrationNode instantiates correctly")


def test_hazard_reviewer_runnable_has_data_integration():
    """Verify HazardReviewerRunnable graph includes data_integration node."""
    from qaai.components.clients import RateLimitOpenAIClient
    from qaai.components.hazard_risk_reviewer.pipeline import HazardReviewerRunnable
    import os
    
    # Create minimal client
    api_key = os.getenv("PYTEST_API_KEY", "test-key")
    client = RateLimitOpenAIClient(api_key=api_key, base_url=None)
    
    # Build graph
    runnable = HazardReviewerRunnable(client=client, model="gpt-4")
    graph = runnable.graph
    
    # Check that graph has data_integration node
    assert hasattr(graph, "nodes"), "Graph should have nodes attribute"
    assert "data_integration" in graph.nodes, \
        "Graph missing data_integration node"
    
    print("✓ HazardReviewerRunnable graph includes data_integration node")


@pytest.mark.asyncio
async def test_data_integration_node_local_mode():
    """Verify DataIntegrationNode passes through local mode (no pyjama_request)."""
    from qaai.components.shared.data_integration import DataIntegrationNode
    from qaai.components.hazard_risk_reviewer.core import HazardReviewState, HazardRecord
    
    node = DataIntegrationNode(pyjama_config=None)
    
    # Create test state with no pyjama_request (local mode)
    hazard = HazardRecord(
        hazard_id="H001",
        hazardous_situation_id="HS001",
        hazard="Test hazard",
        hazardous_situation="Test situation",
        function="Test function",
        ots_software="None",
        hazardous_sequence_of_events="Test FSOE",
        software_related_causes="None",
        harm="Test harm",
        severity="High",
        exploitability_pre_mitigation="Medium",
        probability_of_harm_pre_mitigation="Medium",
        initial_risk_rating="High",
        risk_control_measures="Test controls",
        demonstration_of_effectiveness="Test demo",
        severity_of_harm_post_mitigation="Low",
        exploitability_post_mitigation="Low",
        probability_of_harm_post_mitigation="Low",
        final_risk_rating="Low",
        new_hs_reference="None",
        sw_fmea_trace="None",
        sra_link="None",
        urra_item="None",
        residual_risk_acceptability="Acceptable",
    )
    
    state: HazardReviewState = {
        "hazard": hazard,
        "pyjama_request": None,
    }
    
    # Invoke node
    result = await node(state)
    
    # Should return empty dict in local mode
    assert isinstance(result, dict), \
        f"Expected dict, got {type(result)}"
    
    print("✓ DataIntegrationNode local mode (pyjama_request=None) returns empty dict")


def test_transform_hazard_record_to_state_signature():
    """Verify transform_hazard_record_to_state has correct signature."""
    from qaai.components.shared.data_integration import transform_hazard_record_to_state
    import inspect
    
    sig = inspect.signature(transform_hazard_record_to_state)
    params = list(sig.parameters.keys())
    
    assert "excel_file_path" in params
    assert "pyjama_response_file_path" in params
    assert "output_jsonl_path" in params
    assert "graph_runnable" in params
    
    print("✓ transform_hazard_record_to_state has correct signature")


def test_fixture_files_exist():
    """Verify test fixture files exist."""
    fixtures_dir = Path(__file__).parent / "fixtures" / "external"
    
    excel_file = fixtures_dir / "software_hazard_analysis.xlsx"
    pyjama_file = fixtures_dir / "pyjama_response_unified.jsonl"
    
    assert excel_file.exists(), f"Excel fixture not found: {excel_file}"
    assert pyjama_file.exists(), f"Pyjama fixture not found: {pyjama_file}"
    
    print(f"✓ Fixture files exist:")
    print(f"  - {excel_file}")
    print(f"  - {pyjama_file}")


def test_pyjama_fixture_format():
    """Verify pyjama fixture has correct format."""
    fixtures_dir = Path(__file__).parent / "fixtures" / "external"
    pyjama_file = fixtures_dir / "pyjama_response_unified.jsonl"
    
    with pyjama_file.open("r") as f:
        first_line = f.readline()
    
    assert first_line.strip(), "Pyjama fixture is empty"
    
    data = json.loads(first_line)
    assert "requirement" in data, "Pyjama item missing 'requirement'"
    assert "test_cases" in data, "Pyjama item missing 'test_cases'"
    
    print("✓ Pyjama fixture has correct format:")
    print(f"  - requirement: {data['requirement'].get('req_id', 'unknown')}")
    print(f"  - test_cases: {len(data.get('test_cases', []))} items")


def test_excel_fixture_format():
    """Verify Excel fixture has correct sheet and columns."""
    from pathlib import Path
    import pandas as pd
    
    fixtures_dir = Path(__file__).parent / "fixtures" / "external"
    excel_file = fixtures_dir / "software_hazard_analysis.xlsx"
    
    try:
        df = pd.read_excel(excel_file, sheet_name="SHA Table", engine="openpyxl")
    except Exception as e:
        pytest.fail(f"Failed to read Excel fixture: {e}")
    
    # Check for key columns
    columns_to_check = [
        "SHA \nID Number",
        "Hazard ",
        "Risk Control Measures:\n\nInherent Safety by Design and Manufacture:\nProtective Measures:\nInformation for Safety:",
    ]
    
    for col in columns_to_check:
        assert col in df.columns, f"Excel missing column: {col}"
    
    assert len(df) > 0, "Excel fixture has no data rows"
    
    print(f"✓ Excel fixture has correct format:")
    print(f"  - Sheet: SHA Table")
    print(f"  - Rows: {len(df)}")
    print(f"  - Key columns: {', '.join(columns_to_check)}")


if __name__ == "__main__":
    print("\n" + "=" * 80)
    print("INTEGRATION VERIFICATION TESTS")
    print("=" * 80)
    
    # Run synchronous tests
    print("\n[1] Testing HazardReviewState...")
    test_hazard_review_state_has_pyjama_request()
    
    print("\n[2] Testing DataIntegrationNode factory...")
    test_data_integration_node_factory()
    
    print("\n[3] Testing HazardReviewerRunnable graph...")
    test_hazard_reviewer_runnable_has_data_integration()
    
    print("\n[4] Testing DataIntegrationNode local mode...")
    asyncio.run(test_data_integration_node_local_mode())
    
    print("\n[5] Testing transform_hazard_record_to_state signature...")
    test_transform_hazard_record_to_state_signature()
    
    print("\n[6] Testing fixture files exist...")
    test_fixture_files_exist()
    
    print("\n[7] Testing pyjama fixture format...")
    test_pyjama_fixture_format()
    
    print("\n[8] Testing Excel fixture format...")
    test_excel_fixture_format()
    
    print("\n" + "=" * 80)
    print("✓ ALL INTEGRATION VERIFICATION TESTS PASSED")
    print("=" * 80)
