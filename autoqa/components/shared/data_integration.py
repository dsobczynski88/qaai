"""
Data integration layer for LangGraph pipelines.

Provides a unified interface for fetching data from multiple sources:
- Option 1: Local/Direct (JSON objects, JSONL files) - used by tests
- Option 2: JAMA Baseline (live API fetch via PyJama) - used by production

The DataIntegrationNode is a conditional node that:
1. Checks if state contains a pyjama_request
2. If yes: fetches from JAMA and returns {jama_data, jama_metadata}
3. If no: returns {} (no-op, data already in state)

Transform utilities convert raw JAMA responses into pipeline state formats.
"""

import logging
import os
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

# Re-export PyJama classes for convenience
try:
    from pyjama.langgraph.nodes import (
        PyJamaDataSourceNode,
        PyJamaNodeConfig,
        PyJamaRequest,
    )
    from pyjama.langgraph.transforms import (
        transform_test_suite_review_to_state as _pyjama_transform_test_suite,
        transform_test_case_review_to_state as _pyjama_transform_test_case,
    )
    PYJAMA_AVAILABLE = True
except ImportError:
    PYJAMA_AVAILABLE = False
    PyJamaDataSourceNode = None
    PyJamaNodeConfig = None
    PyJamaRequest = None
    _pyjama_transform_test_suite = None
    _pyjama_transform_test_case = None


logger = logging.getLogger(__name__)


__all__ = [
    "PyJamaRequest",
    "PyJamaNodeConfig",
    "PyJamaDataSourceNode",
    "DataIntegrationNode",
    "transform_test_suite_review_to_state",
    "transform_test_case_review_to_state",
    "PYJAMA_AVAILABLE",
]


class DataIntegrationNode:
    """
    LangGraph-compatible node that conditionally fetches data from JAMA.
    
    This node serves as the entry point for all pipelines, supporting two modes:
    
    Mode 1 (Local/Direct):
        - State contains requirement/test_case + test_cases/requirements directly
        - pyjama_request is None or absent
        - Node returns {} (no-op, data already in state)
        - Used by: pytest fixtures, batch JSONL processing
    
    Mode 2 (JAMA Baseline):
        - State contains pyjama_request with baseline_id
        - Node fetches from JAMA via PyJamaDataSourceNode
        - Returns {jama_data: [...], jama_metadata: {...}}
        - Used by: API endpoints, live integration workflows
    
    Usage:
        # In pipeline.py
        from autoqa.components.shared.data_integration import DataIntegrationNode
        
        data_integration = DataIntegrationNode(pyjama_config)
        sg.add_node("data_integration", data_integration)
        sg.add_edge(START, "data_integration")
    
    State Requirements:
        Input state may contain:
            - pyjama_request: Optional[PyJamaRequest] - triggers JAMA fetch
        
        Output state (JAMA mode only):
            - jama_data: List[Dict] - raw JAMA response
            - jama_metadata: Dict - fetch metadata (count, baseline_id, etc.)
    """
    
    def __init__(self, pyjama_config: Optional["PyJamaNodeConfig"] = None):
        """
        Initialize the data integration node.
        
        Args:
            pyjama_config: Optional PyJama configuration. If None, JAMA mode
                          will attempt lazy initialization from environment
                          variables (JAMA_HOST_ADDRESS, JAMA_CLIENT_ID,
                          JAMA_CLIENT_SECRET).
        """
        self.pyjama_config = pyjama_config
        self._pyjama_node = None
        
        if not PYJAMA_AVAILABLE:
            logger.warning(
                "PyJama not available. JAMA baseline fetching will be disabled. "
                "Install pyjama to enable: pip install pyjama"
            )
    
    def _initialize_pyjama_node(self):
        """Lazy initialization of PyJama node from config or environment."""
        if not PYJAMA_AVAILABLE:
            raise RuntimeError(
                "PyJama is not installed. Cannot fetch from JAMA baseline. "
                "Install pyjama: pip install pyjama"
            )
        
        if self._pyjama_node is not None:
            return  # Already initialized
        
        config = self.pyjama_config
        
        # Fallback to environment variables if no config provided
        if config is None:
            host = os.getenv("JAMA_HOST_ADDRESS")
            client_id = os.getenv("JAMA_CLIENT_ID")
            client_secret = os.getenv("JAMA_CLIENT_SECRET")
            
            if not all([host, client_id, client_secret]):
                raise ValueError(
                    "PyJama config not provided and environment variables not set. "
                    "Either pass pyjama_config to DataIntegrationNode or set: "
                    "JAMA_HOST_ADDRESS, JAMA_CLIENT_ID, JAMA_CLIENT_SECRET"
                )
            
            config = PyJamaNodeConfig(
                host_address=host,
                client_id=client_id,
                client_secret=client_secret,
            )
            logger.info("Initialized PyJama config from environment variables")
        
        self._pyjama_node = PyJamaDataSourceNode(config)
        logger.info("PyJama node initialized successfully")
    
    async def __call__(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute the node: conditionally fetch from JAMA or pass through.
        
        Args:
            state: LangGraph state dict, may contain 'pyjama_request'
        
        Returns:
            Empty dict (local mode) or {jama_data, jama_metadata} (JAMA mode)
        
        Raises:
            RuntimeError: If JAMA mode requested but PyJama not available
            ValueError: If JAMA config missing
            Exception: If JAMA API call fails
        """
        pyjama_request = state.get("pyjama_request")
        
        if pyjama_request is None:
            # Local mode: data already in state, no-op
            logger.debug("Local mode: pyjama_request not present, skipping JAMA fetch")
            return {}
        
        # JAMA mode: fetch from baseline
        logger.info(
            "JAMA mode: fetching data for request_type=%s",
            pyjama_request.request_type if hasattr(pyjama_request, 'request_type') else 'unknown'
        )
        
        # Lazy init PyJama node
        self._initialize_pyjama_node()
        
        # Delegate to PyJama node
        try:
            result = await self._pyjama_node(state)
            logger.info(
                "JAMA fetch successful: %d items retrieved",
                len(result.get("jama_data", []))
            )
            return result
        except Exception as e:
            logger.error("JAMA fetch failed: %s", str(e), exc_info=True)
            raise


def transform_test_suite_review_to_state(
    jama_data: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """
    Transform JAMA test_suite_review data into RTMReviewState format.
    
    Wraps pyjama.langgraph.transforms.transform_test_suite_review_to_state()
    with additional validation and error handling.
    
    Args:
        jama_data: Raw JAMA response from get_test_suite_reviewer_structure()
    
    Returns:
        List of state dicts, one per requirement, each containing:
            - requirement: Requirement model
            - test_cases: List[TestCase]
            - design_docs: List[DesignDoc] (if present)
    
    Raises:
        RuntimeError: If PyJama not available
        ValueError: If transformation fails
    
    Example:
        >>> jama_data = [
        ...     {
        ...         "requirement": {"req_id": "REQ-101", "text": "..."},
        ...         "test_cases": [{"test_id": "TC-201", ...}],
        ...         "design_docs": []
        ...     }
        ... ]
        >>> states = transform_test_suite_review_to_state(jama_data)
        >>> len(states)
        1
        >>> states[0]["requirement"].req_id
        'REQ-101'
    """
    if not PYJAMA_AVAILABLE or _pyjama_transform_test_suite is None:
        raise RuntimeError(
            "PyJama is not installed. Cannot transform JAMA data. "
            "Install pyjama: pip install pyjama"
        )
    
    logger.info("Transforming %d test_suite_review entries to state format", len(jama_data))
    
    try:
        transformed = _pyjama_transform_test_suite(jama_data)
        logger.info("Successfully transformed %d entries", len(transformed))
        return transformed
    except Exception as e:
        logger.error("Transform failed: %s", str(e), exc_info=True)
        raise ValueError(f"Failed to transform JAMA data: {e}") from e


def transform_test_case_review_to_state(
    jama_data: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """
    Transform JAMA test_case_review data into TCReviewState format.
    
    Wraps pyjama.langgraph.transforms.transform_test_case_review_to_state()
    with additional validation and error handling.
    
    Args:
        jama_data: Raw JAMA response from get_test_case_reviewer_structure()
    
    Returns:
        List of state dicts, one per test case, each containing:
            - test_case: TestCase model
            - requirements: List[Requirement]
            - design_docs: List[DesignDoc] (if present)
    
    Raises:
        RuntimeError: If PyJama not available
        ValueError: If transformation fails
    
    Example:
        >>> jama_data = [
        ...     {
        ...         "test_case": {"test_id": "TC-201", ...},
        ...         "requirements": [{"req_id": "REQ-101", "text": "..."}],
        ...         "design_docs": []
        ...     }
        ... ]
        >>> states = transform_test_case_review_to_state(jama_data)
        >>> len(states)
        1
        >>> states[0]["test_case"].test_id
        'TC-201'
    """
    if not PYJAMA_AVAILABLE or _pyjama_transform_test_case is None:
        raise RuntimeError(
            "PyJama is not installed. Cannot transform JAMA data. "
            "Install pyjama: pip install pyjama"
        )
    
    logger.info("Transforming %d test_case_review entries to state format", len(jama_data))
    
    try:
        transformed = _pyjama_transform_test_case(jama_data)
        logger.info("Successfully transformed %d entries", len(transformed))
        return transformed
    except Exception as e:
        logger.error("Transform failed: %s", str(e), exc_info=True)
        raise ValueError(f"Failed to transform JAMA data: {e}") from e
