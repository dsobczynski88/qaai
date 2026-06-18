"""
LangGraph integration for PyJama.

This module provides LangGraph-compatible nodes for fetching Jama data in real-time,
enabling external LangGraph applications to replace static JSONL file inputs with
live Jama API calls.
"""

from .nodes import (
    PyJamaDataSourceNode,
    PyJamaRequest,
    PyJamaNodeConfig,
)
from .transforms import (
    Requirement,
    TestCase,
    DesignDoc,
    transform_test_suite_review_to_state,
    transform_test_case_review_to_state,
    transform_hierarchical_trace_to_state,
)

__all__ = [
    # Node classes
    "PyJamaDataSourceNode",
    "PyJamaRequest",
    "PyJamaNodeConfig",
    # Transform utilities
    "Requirement",
    "TestCase",
    "DesignDoc",
    "transform_test_suite_review_to_state",
    "transform_test_case_review_to_state",
    "transform_hierarchical_trace_to_state",
]
