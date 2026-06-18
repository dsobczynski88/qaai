"""
Transform PyJama outputs into LangGraph state formats.

These utilities convert the raw JSON structures returned by PyJamaTraceMatrix
into Pydantic models that can be used by LangGraph nodes. This provides a clean
separation between the Jama API layer and the LangGraph workflow layer.
"""

from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field
import logging


# Configure module logger
logger = logging.getLogger(__name__)


class Requirement(BaseModel):
    """
    Requirement model compatible with LangGraph state.
    
    Attributes:
        req_id: Requirement document key (e.g., "REQ-PUMP-101", "GID-2788627")
        text: Cleaned requirement text (HTML stripped)
    """
    req_id: str = Field(..., description="Requirement document key")
    text: str = Field(..., description="Requirement text")


class TestCase(BaseModel):
    """
    Test case model compatible with LangGraph state.
    
    Attributes:
        test_id: Test case document key (e.g., "TC-PUMP-202")
        description: Test case name/description
        setup: Test setup instructions
        steps: Formatted test steps
        expectedResults: Formatted expected results
        in_review_baseline: Whether test case is in the review baseline (optional)
    """
    test_id: str = Field(..., description="Test case document key")
    description: str = Field(default="", description="Test case description")
    setup: str = Field(default="", description="Test setup instructions")
    steps: str = Field(default="", description="Test steps")
    expectedResults: str = Field(default="", description="Expected results")
    in_review_baseline: bool = Field(default=True, description="In review baseline flag")


class DesignDoc(BaseModel):
    """
    Design document model compatible with LangGraph state.
    
    Attributes:
        doc_id: Design document key (e.g., "DD-PUMP-RC-001")
        name: Design document name
        description: Design document description
    """
    doc_id: str = Field(..., description="Design document key")
    name: str = Field(default="", description="Design document name")
    description: str = Field(default="", description="Design document description")


class SystemRequirement(BaseModel):
    """
    System requirement model with nested user needs.
    
    Attributes:
        req_id: System requirement document key
        text: System requirement text
        user_needs: List of upstream user needs
    """
    req_id: str = Field(..., description="System requirement document key")
    text: str = Field(..., description="System requirement text")
    user_needs: List[Requirement] = Field(
        default_factory=list,
        description="Upstream user needs"
    )


def transform_test_suite_review_to_state(
    jama_data: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """
    Transform test_suite_review data into format for LangGraph state.
    
    Converts the raw output from PyJamaTraceMatrix.get_test_suite_reviewer_structure()
    into a list of state dictionaries, one per requirement, ready for graph processing.
    
    Each output dict contains Pydantic models that can be directly used by LangGraph
    nodes without additional parsing.
    
    Args:
        jama_data: List of requirement entries from get_test_suite_reviewer_structure()
        
    Returns:
        List of state dicts, one per requirement, each containing:
            - requirement: Requirement model
            - test_cases: List[TestCase]
            - design_docs: List[DesignDoc]
    
    Example:
        >>> from pyjama.langgraph.nodes import PyJamaDataSourceNode, PyJamaRequest
        >>> from pyjama.langgraph.transforms import transform_test_suite_review_to_state
        >>> 
        >>> # Fetch data
        >>> node = PyJamaDataSourceNode(config)
        >>> result = await node({
        ...     "pyjama_request": PyJamaRequest(
        ...         request_type="test_suite_review",
        ...         baseline_id="BASE-84398"
        ...     )
        ... })
        >>> 
        >>> # Transform to state format
        >>> states = transform_test_suite_review_to_state(result["jama_data"])
        >>> 
        >>> # Process each requirement through graph
        >>> for state in states:
        ...     graph_result = await graph.ainvoke(state)
    """
    logger.info("Transforming %d test suite review entries to state format", len(jama_data))
    
    transformed = []
    
    for entry in jama_data:
        req_data = entry.get("requirement", {})
        test_cases_data = entry.get("test_cases", [])
        design_docs_data = entry.get("design_docs", [])
        
        # Validate required fields
        if not req_data.get("req_id") or not req_data.get("text"):
            logger.warning(
                "Skipping entry with missing requirement data: %s",
                req_data
            )
            continue
        
        try:
            state_entry = {
                "requirement": Requirement(
                    req_id=req_data.get("req_id"),
                    text=req_data.get("text")
                ),
                "test_cases": [
                    TestCase(
                        test_id=tc.get("test_id", ""),
                        description=tc.get("description", ""),
                        setup=tc.get("setup", ""),
                        steps=tc.get("steps", ""),
                        expectedResults=tc.get("expectedResults", ""),
                        in_review_baseline=tc.get("in_review_baseline", True)
                    )
                    for tc in test_cases_data
                ],
                "design_docs": [
                    DesignDoc(
                        doc_id=dd.get("doc_id", ""),
                        name=dd.get("name", ""),
                        description=dd.get("description", "")
                    )
                    for dd in design_docs_data
                ]
            }
            
            transformed.append(state_entry)
            
            logger.debug(
                "Transformed requirement %s: %d test cases, %d design docs",
                req_data.get("req_id"),
                len(test_cases_data),
                len(design_docs_data)
            )
            
        except Exception as e:
            logger.error(
                "Failed to transform entry for requirement %s: %s",
                req_data.get("req_id"),
                str(e)
            )
            continue
    
    logger.info(
        "Successfully transformed %d/%d entries",
        len(transformed),
        len(jama_data)
    )
    
    return transformed


def transform_test_case_review_to_state(
    jama_data: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """
    Transform test_case_review data into format for LangGraph state.
    
    Converts the raw output from PyJamaTraceMatrix.get_test_case_reviewer_structure()
    into a list of state dictionaries, one per test case, ready for graph processing.
    
    This is the inverse of test_suite_review: organized by test case instead of
    requirement, showing upstream requirements and design docs for each test.
    
    Args:
        jama_data: List of test case entries from get_test_case_reviewer_structure()
        
    Returns:
        List of state dicts, one per test case, each containing:
            - test_case: TestCase model
            - requirements: List[Requirement]
            - design_docs: List[DesignDoc]
    
    Example:
        >>> from pyjama.langgraph.transforms import transform_test_case_review_to_state
        >>> 
        >>> # Fetch data
        >>> result = await node({
        ...     "pyjama_request": PyJamaRequest(
        ...         request_type="test_case_review",
        ...         baseline_id="BASE-84398"
        ...     )
        ... })
        >>> 
        >>> # Transform to state format
        >>> states = transform_test_case_review_to_state(result["jama_data"])
        >>> 
        >>> # Process each test case through graph
        >>> for state in states:
        ...     graph_result = await graph.ainvoke(state)
    """
    logger.info("Transforming %d test case review entries to state format", len(jama_data))
    
    transformed = []
    
    for entry in jama_data:
        test_case_data = entry.get("test_case", {})
        requirements_data = entry.get("requirements", [])
        design_docs_data = entry.get("design_docs", [])
        
        # Validate required fields
        if not test_case_data.get("test_id"):
            logger.warning(
                "Skipping entry with missing test case data: %s",
                test_case_data
            )
            continue
        
        try:
            state_entry = {
                "test_case": TestCase(
                    test_id=test_case_data.get("test_id", ""),
                    description=test_case_data.get("description", ""),
                    setup=test_case_data.get("setup", ""),
                    steps=test_case_data.get("steps", ""),
                    expectedResults=test_case_data.get("expectedResults", ""),
                    in_review_baseline=test_case_data.get("in_review_baseline", True)
                ),
                "requirements": [
                    Requirement(
                        req_id=req.get("req_id", ""),
                        text=req.get("text", "")
                    )
                    for req in requirements_data
                ],
                "design_docs": [
                    DesignDoc(
                        doc_id=dd.get("doc_id", ""),
                        name=dd.get("name", ""),
                        description=dd.get("description", "")
                    )
                    for dd in design_docs_data
                ]
            }
            
            transformed.append(state_entry)
            
            logger.debug(
                "Transformed test case %s: %d requirements, %d design docs",
                test_case_data.get("test_id"),
                len(requirements_data),
                len(design_docs_data)
            )
            
        except Exception as e:
            logger.error(
                "Failed to transform entry for test case %s: %s",
                test_case_data.get("test_id"),
                str(e)
            )
            continue
    
    logger.info(
        "Successfully transformed %d/%d entries",
        len(transformed),
        len(jama_data)
    )
    
    return transformed


def transform_hierarchical_trace_to_state(
    jama_data: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """
    Transform hierarchical_trace data into format for LangGraph state.
    
    Converts the raw output from PyJamaTraceMatrix.get_hierarchical_trace_from_gids()
    into a list of state dictionaries, one per software requirement, with nested
    system requirements and user needs.
    
    This preserves the hierarchical structure:
    Software Requirement → System Requirements → User Needs
    
    Args:
        jama_data: List of software requirement entries from get_hierarchical_trace_from_gids()
        
    Returns:
        List of state dicts, one per software requirement, each containing:
            - requirement: Requirement model (software requirement)
            - system_requirements: List[SystemRequirement] (with nested user_needs)
    
    Example:
        >>> from pyjama.langgraph.transforms import transform_hierarchical_trace_to_state
        >>> 
        >>> # Fetch data
        >>> result = await node({
        ...     "pyjama_request": PyJamaRequest(
        ...         request_type="hierarchical_trace",
        ...         project_name="Patient Safety Platform",
        ...         identifiers=["GID-2788627", "GID-2788628"]
        ...     )
        ... })
        >>> 
        >>> # Transform to state format
        >>> states = transform_hierarchical_trace_to_state(result["jama_data"])
        >>> 
        >>> # Process each software requirement through graph
        >>> for state in states:
        ...     graph_result = await graph.ainvoke(state)
        >>> 
        >>> # Or write to JSONL
        >>> import json
        >>> with open("hierarchical_trace.jsonl", "w") as f:
        ...     for state in states:
        ...         # Convert Pydantic models to dicts for JSON serialization
        ...         json_entry = {
        ...             "requirement": state["requirement"].model_dump(),
        ...             "system_requirements": [
        ...                 sr.model_dump() for sr in state["system_requirements"]
        ...             ]
        ...         }
        ...         f.write(json.dumps(json_entry) + "\n")
    """
    logger.info("Transforming %d hierarchical trace entries to state format", len(jama_data))
    
    transformed = []
    
    for entry in jama_data:
        req_data = entry.get("requirement", {})
        system_reqs_data = entry.get("system_requirements", [])
        
        # Validate required fields
        if not req_data.get("req_id") or not req_data.get("text"):
            logger.warning(
                "Skipping entry with missing requirement data: %s",
                req_data
            )
            continue
        
        try:
            # Transform system requirements with nested user needs
            system_requirements = []
            for sys_req in system_reqs_data:
                user_needs_data = sys_req.get("user_needs", [])
                
                system_requirement = SystemRequirement(
                    req_id=sys_req.get("req_id", ""),
                    text=sys_req.get("text", ""),
                    user_needs=[
                        Requirement(
                            req_id=un.get("req_id", ""),
                            text=un.get("text", "")
                        )
                        for un in user_needs_data
                    ]
                )
                
                system_requirements.append(system_requirement)
            
            state_entry = {
                "requirement": Requirement(
                    req_id=req_data.get("req_id"),
                    text=req_data.get("text")
                ),
                "system_requirements": system_requirements
            }
            
            transformed.append(state_entry)
            
            # Calculate total user needs across all system requirements
            total_user_needs = sum(
                len(sr.user_needs) for sr in system_requirements
            )
            
            logger.debug(
                "Transformed requirement %s: %d system reqs, %d total user needs",
                req_data.get("req_id"),
                len(system_requirements),
                total_user_needs
            )
            
        except Exception as e:
            logger.error(
                "Failed to transform entry for requirement %s: %s",
                req_data.get("req_id"),
                str(e)
            )
            continue
    
    logger.info(
        "Successfully transformed %d/%d entries",
        len(transformed),
        len(jama_data)
    )
    
    return transformed
