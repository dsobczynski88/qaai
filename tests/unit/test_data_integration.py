"""
Unit tests for data integration layer.

Tests the DataIntegrationNode and transform nodes in isolation,
verifying both local passthrough and JAMA fetch modes.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from autoqa.components.shared.data_integration import (
    DataIntegrationNode,
    PYJAMA_AVAILABLE,
)
from autoqa.components.shared.nodes import (
    make_data_integration_node,
    make_transform_node_test_suite_review,
    make_transform_node_test_case_review,
)


class TestDataIntegrationNode:
    """Test DataIntegrationNode behavior in local and JAMA modes."""
    
    @pytest.mark.asyncio
    async def test_local_mode_passthrough(self):
        """Local mode: no pyjama_request → node returns empty dict (no-op)."""
        node = DataIntegrationNode(pyjama_config=None)
        
        state = {
            "requirement": {"req_id": "REQ-101", "text": "Test requirement"},
            "test_cases": [],
        }
        
        result = await node(state)
        
        # Should return empty dict (passthrough)
        assert result == {}
    
    @pytest.mark.asyncio
    async def test_jama_mode_missing_pyjama_raises(self):
        """JAMA mode without PyJama installed raises RuntimeError."""
        if not PYJAMA_AVAILABLE:
            node = DataIntegrationNode(pyjama_config=None)
            
            state = {
                "pyjama_request": {
                    "request_type": "test_suite_review",
                    "baseline_id": "BASE-123",
                }
            }
            
            with pytest.raises(RuntimeError, match="PyJama is not installed"):
                await node(state)
        else:
            pytest.skip("PyJama is available, cannot test missing-pyjama path")
    
    @pytest.mark.asyncio
    @pytest.mark.skipif(not PYJAMA_AVAILABLE, reason="PyJama not installed")
    async def test_jama_mode_fetch_success(self):
        """JAMA mode with valid request fetches data successfully."""
        # Mock PyJamaDataSourceNode
        with patch("autoqa.components.shared.data_integration.PyJamaDataSourceNode") as MockNode:
            mock_instance = AsyncMock()
            mock_instance.return_value = {
                "jama_data": [{"requirement": {"req_id": "REQ-101", "text": "..."}}],
                "jama_metadata": {"count": 1, "baseline_id": "BASE-123"},
            }
            MockNode.return_value = mock_instance
            
            # Create node with mock config
            from autoqa.components.shared.data_integration import PyJamaNodeConfig
            config = PyJamaNodeConfig(
                host_address="https://test.jamacloud.com",
                client_id="test_id",
                client_secret="test_secret",
            )
            node = DataIntegrationNode(pyjama_config=config)
            
            state = {
                "pyjama_request": {
                    "request_type": "test_suite_review",
                    "baseline_id": "BASE-123",
                }
            }
            
            result = await node(state)
            
            # Should return jama_data and jama_metadata
            assert "jama_data" in result
            assert "jama_metadata" in result
            assert len(result["jama_data"]) == 1
            assert result["jama_metadata"]["baseline_id"] == "BASE-123"


class TestTransformNodes:
    """Test transform node factories."""
    
    def test_transform_test_suite_review_local_mode(self):
        """Transform node in local mode returns empty dict."""
        transform = make_transform_node_test_suite_review()
        
        state = {
            "requirement": {"req_id": "REQ-101", "text": "Test"},
            "test_cases": [],
        }
        
        result = transform(state)
        
        # Local mode: no jama_data → no-op
        assert result == {}
    
    @pytest.mark.skipif(not PYJAMA_AVAILABLE, reason="PyJama not installed")
    def test_transform_test_suite_review_jama_mode(self):
        """Transform node in JAMA mode converts data to state format."""
        transform = make_transform_node_test_suite_review()
        
        state = {
            "jama_data": [
                {
                    "requirement": {"req_id": "REQ-101", "text": "Test requirement"},
                    "test_cases": [
                        {
                            "test_id": "TC-201",
                            "description": "Test case",
                            "setup": "",
                            "steps": "Step 1",
                            "expectedResults": "Pass",
                        }
                    ],
                    "design_docs": [],
                }
            ]
        }
        
        result = transform(state)
        
        # Should transform to state format
        assert "requirement" in result
        assert "test_cases" in result
        assert result["requirement"].req_id == "REQ-101"
        assert len(result["test_cases"]) == 1
        assert result["test_cases"][0].test_id == "TC-201"
    
    def test_transform_test_case_review_local_mode(self):
        """Transform node in local mode returns empty dict."""
        transform = make_transform_node_test_case_review()
        
        state = {
            "test_case": {"test_id": "TC-201", "description": "Test"},
            "requirements": [],
        }
        
        result = transform(state)
        
        # Local mode: no jama_data → no-op
        assert result == {}
    
    @pytest.mark.skipif(not PYJAMA_AVAILABLE, reason="PyJama not installed")
    def test_transform_test_case_review_jama_mode(self):
        """Transform node in JAMA mode converts data to state format."""
        transform = make_transform_node_test_case_review()
        
        state = {
            "jama_data": [
                {
                    "test_case": {
                        "test_id": "TC-201",
                        "description": "Test case",
                        "setup": "",
                        "steps": "Step 1",
                        "expectedResults": "Pass",
                    },
                    "requirements": [
                        {"req_id": "REQ-101", "text": "Test requirement"}
                    ],
                    "design_docs": [],
                }
            ]
        }
        
        result = transform(state)
        
        # Should transform to state format
        assert "test_case" in result
        assert "requirements" in result
        assert result["test_case"].test_id == "TC-201"
        assert len(result["requirements"]) == 1
        assert result["requirements"][0].req_id == "REQ-101"


class TestFactoryFunctions:
    """Test factory function behavior."""
    
    def test_make_data_integration_node_no_config(self):
        """Factory creates node without config (lazy init from env)."""
        node = make_data_integration_node(pyjama_config=None)
        
        assert isinstance(node, DataIntegrationNode)
        assert node.pyjama_config is None
    
    @pytest.mark.skipif(not PYJAMA_AVAILABLE, reason="PyJama not installed")
    def test_make_data_integration_node_with_config(self):
        """Factory creates node with explicit config."""
        from autoqa.components.shared.data_integration import PyJamaNodeConfig
        
        config = PyJamaNodeConfig(
            host_address="https://test.jamacloud.com",
            client_id="test_id",
            client_secret="test_secret",
        )
        node = make_data_integration_node(pyjama_config=config)
        
        assert isinstance(node, DataIntegrationNode)
        assert node.pyjama_config == config
