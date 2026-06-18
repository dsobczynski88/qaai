"""Assembly classes for transforming Jama data into structured outputs.

This module provides specialized assemblers that transform raw Jama API data
into structured formats for different use cases:

- TestSuiteReviewerAssembler: Organizes data by requirements with downstream test cases
- TestCaseReviewerAssembler: Organizes data by test cases with upstream requirements
- FlatRTMAssembler: Creates flat Requirements Traceability Matrix structure
- HierarchicalTraceAssembler: Creates hierarchical trace (requirement → system reqs → user needs)
- UpstreamHierarchicalTraceAssembler: Handles upstream hierarchical structure (refactored from HierarchicalTraceAssembler)
- DownstreamHierarchicalTraceAssembler: Handles downstream test cases and design docs
- BidirectionalTraceAssembler: Combines upstream and downstream structures into bidirectional trace

Each assembler follows the Single Responsibility Principle and can be tested independently.
"""

from typing import Dict, List, Any, Set, Optional, Union
from pyjama.utils.jama_constants import (
    TEST_CASES_KEY,
    DESIGN_DOCS_KEY,
    REQUIREMENTS_KEY,
    USER_NEEDS_KEY,
    SYSTEM_REQUIREMENTS_KEY,
    IN_REVIEW_BASELINE_KEY,
    REQUIREMENT_KEY,
    REQUIREMENT_ID_KEY,
    ID_KEY,
    DEFAULT_DESIGN_TYPEKEYS,
    DEFAULT_TESTCASE_TYPEKEY,
    DEFAULT_REQ_TYPEKEYS,
)
from pyjama.utils.jama_utils import (
    get_doc_key,
    normalize_typekeys,
    build_requirement_data,
    build_simple_requirement_data,
    build_test_case_data,
    build_simple_test_case_data,
    build_design_doc_data,
)


class TestSuiteReviewerAssembler:
    """Assembles test suite reviewer structure (organized by requirements).
    
    This assembler creates a structure where each requirement is the primary entity,
    with its downstream test cases and design documents nested within it.
    
    Output structure:
        [
            {
                "requirement": {...},
                "test_cases": [{...}, ...],
                "design_docs": [{...}, ...]
            },
            ...
        ]
    """
    
    def __init__(self, logger=None):
        """Initialize assembler with optional logger.
        
        Args:
            logger: Optional logger instance for debug/info messages
        """
        self._logger = logger
    
    def assemble(
        self,
        requirements_dict: Dict[int, Dict[str, Any]],
        downstream_results: Dict[int, List[Dict[str, Any]]],
        review_test_keys: Set[str],
        testcase_typekey: str,
        design_typekey: str,
    ) -> List[Dict[str, Any]]:
        """Assemble requirements with downstream test cases and design docs.
        
        Args:
            requirements_dict: Dictionary of requirement items keyed by API ID
            downstream_results: Dictionary mapping requirement ID to downstream items
            review_test_keys: Set of test case document keys in the baseline
            testcase_typekey: Type key for test cases (e.g., 'TEST')
            design_typekey: Type key for design documents (e.g., 'DES')
            
        Returns:
            List of requirement dictionaries with nested test_cases and design_docs
        """
        if self._logger:
            self._logger.info("Assembling test suite reviewer structure")
            self._logger.debug(
                "Processing %d requirements with %d baseline test keys",
                len(requirements_dict),
                len(review_test_keys)
            )
        
        final_payload = []
        
        for req_id, req_item in requirements_dict.items():
            req_data = self._build_requirement_entry(
                req_id, req_item, downstream_results, 
                review_test_keys, testcase_typekey, design_typekey
            )
            final_payload.append(req_data)
        
        if self._logger:
            self._logger.info(
                "Assembly complete: %d requirements with test cases and design docs",
                len(final_payload)
            )
        
        return final_payload
    
    def _build_requirement_entry(
        self, 
        req_id: int,
        req_item: Dict[str, Any],
        downstream_results: Dict[int, List[Dict[str, Any]]],
        review_test_keys: Set[str],
        testcase_typekey: str,
        design_typekey: str
    ) -> Dict[str, Any]:
        """Build single requirement entry with downstream items.
        
        Args:
            req_id: Requirement API ID
            req_item: Requirement item dictionary
            downstream_results: Dictionary mapping requirement ID to downstream items
            review_test_keys: Set of test case document keys in the baseline
            testcase_typekey: Type key for test cases
            design_typekey: Type key for design documents
            
        Returns:
            Requirement dictionary with nested test_cases and design_docs
        """
        req_data = build_requirement_data(req_item)
        doc_key = get_doc_key(req_item)
        
        if self._logger:
            self._logger.debug("Building requirement data for: %s (ID: %d)", doc_key, req_id)
        
        downstream_items = downstream_results.get(req_id, [])
        
        test_cases, design_docs = self._process_downstream_items(
            downstream_items, testcase_typekey, design_typekey, review_test_keys
        )
        
        req_data[TEST_CASES_KEY] = test_cases
        req_data[DESIGN_DOCS_KEY] = design_docs
        
        if self._logger:
            self._logger.debug(
                "Requirement %s: %d test cases (%d in baseline), %d design docs",
                doc_key,
                len(test_cases),
                sum(1 for tc in test_cases if tc.get(IN_REVIEW_BASELINE_KEY, False)),
                len(design_docs)
            )
        
        return req_data
    
    def _process_downstream_items(
        self,
        downstream_items: List[Dict[str, Any]],
        testcase_typekey: str,
        design_typekey: Union[str, List[str]],
        review_test_keys: Set[str],
    ) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Separate downstream items into test cases and design docs.
        
        Args:
            downstream_items: List of downstream item dictionaries
            testcase_typekey: Type key for test cases
            design_typekey: Type key(s) for design documents (str or list of str)
            review_test_keys: Set of test case document keys in the baseline
            
        Returns:
            Tuple of (test_cases, design_docs)
        """
        test_cases = []
        design_docs = []

        # Accept either a single typekey or a list of typekeys for each kind.
        testcase_typekeys = normalize_typekeys(testcase_typekey, [DEFAULT_TESTCASE_TYPEKEY])
        design_typekeys = normalize_typekeys(design_typekey, DEFAULT_DESIGN_TYPEKEYS)

        for item in downstream_items:
            doc_key = get_doc_key(item)

            if any(tk in doc_key for tk in testcase_typekeys):
                test_cases.append(build_test_case_data(item, review_test_keys))
            elif any(tk in doc_key for tk in design_typekeys):
                design_docs.append(build_design_doc_data(item))
        
        if self._logger:
            self._logger.debug(
                "Processed %d test cases and %d design docs", 
                len(test_cases), 
                len(design_docs)
            )
        
        return test_cases, design_docs


class TestCaseReviewerAssembler:
    """Assembles test case reviewer structure (organized by test cases).
    
    This assembler creates a structure where each test case is the primary entity,
    with its upstream requirements and design documents nested within it.
    
    Output structure:
        [
            {
                "test_case": {...},
                "requirements": [{...}, ...],
                "design_docs": [{...}, ...]
            },
            ...
        ]
    """
    
    def __init__(self, logger=None):
        """Initialize assembler with optional logger.
        
        Args:
            logger: Optional logger instance for debug/info messages
        """
        self._logger = logger
    
    def assemble(
        self,
        test_cases_dict: Dict[int, Dict[str, Any]],
        upstream_results: Dict[int, List[Dict[str, Any]]],
        requirement_typekeys: List[str],
        design_typekey: str,
        api_id_key: str = "id",
    ) -> List[Dict[str, Any]]:
        """Assemble test cases with upstream requirements and design docs.
        
        Args:
            test_cases_dict: Dictionary of test case items keyed by API ID
            upstream_results: Dictionary mapping test case ID to upstream items
            requirement_typekeys: Type keys for requirements (e.g., ["REQ", "PRQ"])
            design_typekey: Type key for design documents (e.g., "DES")
            api_id_key: Key for item ID in API responses
            
        Returns:
            List of test case dictionaries with nested requirements and design_docs
        """
        if self._logger:
            self._logger.info("Assembling test case reviewer structure")
            self._logger.debug(
                "Processing %d test cases with upstream items",
                len(test_cases_dict)
            )
        
        final_payload = []
        
        for test_id, test_item in test_cases_dict.items():
            test_entry = self._build_test_case_entry(
                test_id, test_item, upstream_results,
                requirement_typekeys, design_typekey
            )
            final_payload.append(test_entry)
        
        if self._logger:
            self._logger.info(
                "Assembly complete: %d test cases with upstream dependencies",
                len(final_payload)
            )
        
        return final_payload
    
    def _build_test_case_entry(
        self, 
        test_id: int,
        test_item: Dict[str, Any],
        upstream_results: Dict[int, List[Dict[str, Any]]],
        requirement_typekeys: List[str],
        design_typekey: str
    ) -> Dict[str, Any]:
        """Build single test case entry with upstream items.
        
        Args:
            test_id: Test case API ID
            test_item: Test case item dictionary
            upstream_results: Dictionary mapping test case ID to upstream items
            requirement_typekeys: Type keys for requirements
            design_typekey: Type key for design documents
            
        Returns:
            Test case dictionary with nested requirements and design_docs
        """
        test_data = build_simple_test_case_data(test_item)
        doc_key = get_doc_key(test_item)
        
        if self._logger:
            self._logger.debug("Building test case data for: %s (ID: %d)", doc_key, test_id)
        
        upstream_items = upstream_results.get(test_id, [])
        
        requirements, design_docs = self._separate_upstream_items(
            upstream_items, requirement_typekeys, design_typekey
        )
        
        if self._logger:
            self._logger.debug(
                "Test case %s: %d requirements, %d design docs",
                doc_key,
                len(requirements),
                len(design_docs)
            )
        
        return {
            "test_case": test_data,
            REQUIREMENTS_KEY: requirements,
            DESIGN_DOCS_KEY: design_docs,
        }
    
    def _separate_upstream_items(
        self,
        upstream_items: List[Dict[str, Any]],
        requirement_typekeys: List[str],
        design_typekey: str
    ) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Separate upstream items by type.
        
        Args:
            upstream_items: List of upstream item dictionaries
            requirement_typekeys: Type keys for requirements
            design_typekey: Type key for design documents
            
        Returns:
            Tuple of (requirements, design_docs)
        """
        requirements = []
        design_docs = []

        # Accept either a single typekey or a list of typekeys for each kind.
        requirement_typekeys = normalize_typekeys(requirement_typekeys, DEFAULT_REQ_TYPEKEYS)
        design_typekeys = normalize_typekeys(design_typekey, DEFAULT_DESIGN_TYPEKEYS)

        for item in upstream_items:
            doc_key = get_doc_key(item)

            if any(typekey in doc_key for typekey in requirement_typekeys):
                requirements.append(build_simple_requirement_data(item))
                if self._logger:
                    self._logger.debug("Found requirement: %s", doc_key)
            elif any(typekey in doc_key for typekey in design_typekeys):
                design_docs.append(build_design_doc_data(item))
                if self._logger:
                    self._logger.debug("Found design doc: %s", doc_key)

        return requirements, design_docs


class FlatRTMAssembler:
    """Assembles flat RTM (Requirements Traceability Matrix) structure.
    
    This assembler creates a flat structure with five categories of items:
    user needs, system requirements, software requirements, test cases, and design docs.
    
    Output structure:
        {
            "user_needs": [{...}, ...],
            "system_requirements": [{...}, ...],
            "requirements": [{...}, ...],
            "test_cases": [{...}, ...],
            "design_docs": [{...}, ...]
        }
    """
    
    def __init__(self, logger=None):
        """Initialize assembler with optional logger.
        
        Args:
            logger: Optional logger instance for debug/info messages
        """
        self._logger = logger
    
    def assemble(
        self,
        user_needs_dict: Dict[int, Dict[str, Any]],
        connected_system_reqs_dict: Dict[int, Dict[str, Any]],
        input_software_reqs_dict: Dict[int, Dict[str, Any]],
        test_cases_dict: Dict[int, Dict[str, Any]],
        design_docs_dict: Dict[int, Dict[str, Any]],
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Assemble flat RTM from categorized items.
        
        Args:
            user_needs_dict: Dictionary of user need items keyed by item ID
            connected_system_reqs_dict: Dictionary of system requirement items keyed by item ID
            input_software_reqs_dict: Dictionary of software requirement items keyed by item ID
            test_cases_dict: Dictionary of test case items keyed by item ID
            design_docs_dict: Dictionary of design document items keyed by item ID
            
        Returns:
            Dictionary with keys: user_needs, system_requirements, requirements,
            test_cases, design_docs
        """
        if self._logger:
            self._logger.info("Assembling flat RTM output structure")
        
        result = {
            USER_NEEDS_KEY: self._build_simple_requirements(user_needs_dict),
            SYSTEM_REQUIREMENTS_KEY: self._build_simple_requirements(connected_system_reqs_dict),
            REQUIREMENTS_KEY: self._build_simple_requirements(input_software_reqs_dict),
            TEST_CASES_KEY: self._build_simple_test_cases(test_cases_dict),
            DESIGN_DOCS_KEY: self._build_design_docs(design_docs_dict),
        }
        
        if self._logger:
            self._logger.info("RTM assembly complete:")
            self._logger.info("  User needs: %d", len(result[USER_NEEDS_KEY]))
            self._logger.info("  System requirements: %d", len(result[SYSTEM_REQUIREMENTS_KEY]))
            self._logger.info("  Software requirements: %d", len(result[REQUIREMENTS_KEY]))
            self._logger.info("  Test cases: %d", len(result[TEST_CASES_KEY]))
            self._logger.info("  Design docs: %d", len(result[DESIGN_DOCS_KEY]))
        
        return result
    
    def _build_simple_requirements(
        self, 
        items_dict: Dict[int, Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Build simple requirement data from items dictionary.
        
        Args:
            items_dict: Dictionary of requirement items keyed by item ID
            
        Returns:
            List of simple requirement dictionaries
        """
        return [build_simple_requirement_data(item) for item in items_dict.values()]
    
    def _build_simple_test_cases(
        self, 
        items_dict: Dict[int, Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Build simple test case data from items dictionary.
        
        Args:
            items_dict: Dictionary of test case items keyed by item ID
            
        Returns:
            List of simple test case dictionaries
        """
        return [build_simple_test_case_data(item) for item in items_dict.values()]
    
    def _build_design_docs(
        self, 
        items_dict: Dict[int, Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Build design doc data from items dictionary.
        
        Args:
            items_dict: Dictionary of design doc items keyed by item ID
            
        Returns:
            List of design doc dictionaries
        """
        return [build_design_doc_data(item) for item in items_dict.values()]


class HierarchicalTraceAssembler:
    """Assembles hierarchical trace structure (requirement → system reqs → user needs).
    
    This assembler creates a hierarchical structure where each software requirement
    is the primary entity, with its upstream system requirements nested within it,
    and each system requirement has its upstream user needs nested within it.
    
    This maintains the full traceability chain and shows which specific system
    requirements and user needs are connected to each software requirement.
    
    Output structure (JSONL-compatible):
        [
            {
                "requirement": {"req_id": "GID-123", "text": "..."},
                "system_requirements": [
                    {
                        "req_id": "PRQ-456",
                        "text": "...",
                        "user_needs": [
                            {"req_id": "UND-789", "text": "..."},
                            ...
                        ]
                    },
                    ...
                ]
            },
            ...
        ]
    """
    
    def __init__(self, logger=None):
        """Initialize assembler with optional logger.
        
        Args:
            logger: Optional logger instance for debug/info messages
        """
        self._logger = logger
    
    def assemble(
        self,
        software_reqs_dict: Dict[int, Dict[str, Any]],
        software_upstream_results: Dict[int, List[Dict[str, Any]]],
        system_reqs_dict: Dict[int, Dict[str, Any]],
        system_upstream_results: Dict[int, List[Dict[str, Any]]],
        user_needs_dict: Dict[int, Dict[str, Any]],
        api_id_key: str = "id",
    ) -> List[Dict[str, Any]]:
        """Assemble hierarchical trace for each software requirement.
        
        Args:
            software_reqs_dict: Dictionary of software requirement items keyed by API ID
            software_upstream_results: Dictionary mapping software req ID to upstream items
            system_reqs_dict: Dictionary of system requirement items keyed by API ID
            system_upstream_results: Dictionary mapping system req ID to upstream items
            user_needs_dict: Dictionary of user need items keyed by API ID
            api_id_key: Key for item ID in API responses
            
        Returns:
            List of software requirement dictionaries with nested system_requirements
            (which contain nested user_needs)
        """
        if self._logger:
            self._logger.info("Assembling hierarchical trace structure")
            self._logger.debug(
                "Processing %d software requirements with upstream traces",
                len(software_reqs_dict)
            )
        
        final_payload = []
        
        for software_req_id, software_req_item in software_reqs_dict.items():
            req_entry = self._build_software_requirement_entry(
                software_req_id,
                software_req_item,
                software_upstream_results,
                system_reqs_dict,
                system_upstream_results,
                user_needs_dict,
                api_id_key,
            )
            final_payload.append(req_entry)
        
        if self._logger:
            self._logger.info(
                "Assembly complete: %d software requirements with hierarchical traces",
                len(final_payload)
            )
        
        return final_payload
    
    def _build_software_requirement_entry(
        self,
        software_req_id: int,
        software_req_item: Dict[str, Any],
        software_upstream_results: Dict[int, List[Dict[str, Any]]],
        system_reqs_dict: Dict[int, Dict[str, Any]],
        system_upstream_results: Dict[int, List[Dict[str, Any]]],
        user_needs_dict: Dict[int, Dict[str, Any]],
        api_id_key: str,
    ) -> Dict[str, Any]:
        """Build single software requirement entry with hierarchical upstream trace.
        
        Args:
            software_req_id: Software requirement API ID
            software_req_item: Software requirement item dictionary
            software_upstream_results: Dictionary mapping software req ID to upstream items
            system_reqs_dict: Dictionary of system requirement items keyed by API ID
            system_upstream_results: Dictionary mapping system req ID to upstream items
            user_needs_dict: Dictionary of user need items keyed by API ID
            api_id_key: Key for item ID in API responses
            
        Returns:
            Software requirement dictionary with nested system_requirements and user_needs
        """
        software_req_data = build_simple_requirement_data(software_req_item)
        doc_key = get_doc_key(software_req_item)
        
        if self._logger:
            self._logger.debug(
                "Building hierarchical trace for software requirement: %s (ID: %d)",
                doc_key,
                software_req_id
            )
        
        # Get upstream items for this software requirement
        upstream_items = software_upstream_results.get(software_req_id, [])
        
        # Filter to only system requirements
        connected_system_req_ids = [
            item[api_id_key]
            for item in upstream_items
            if item[api_id_key] in system_reqs_dict
        ]
        
        if self._logger:
            self._logger.debug(
                "Software requirement %s has %d connected system requirements",
                doc_key,
                len(connected_system_req_ids)
            )
        
        # Build system requirements with nested user needs
        system_requirements = []
        for system_req_id in connected_system_req_ids:
            system_req_entry = self._build_system_requirement_entry(
                system_req_id,
                system_reqs_dict[system_req_id],
                system_upstream_results,
                user_needs_dict,
                api_id_key,
            )
            system_requirements.append(system_req_entry)
        
        return {
            REQUIREMENT_KEY: software_req_data,
            SYSTEM_REQUIREMENTS_KEY: system_requirements,
        }
    
    def _build_system_requirement_entry(
        self,
        system_req_id: int,
        system_req_item: Dict[str, Any],
        system_upstream_results: Dict[int, List[Dict[str, Any]]],
        user_needs_dict: Dict[int, Dict[str, Any]],
        api_id_key: str,
    ) -> Dict[str, Any]:
        """Build single system requirement entry with nested user needs.
        
        Args:
            system_req_id: System requirement API ID
            system_req_item: System requirement item dictionary
            system_upstream_results: Dictionary mapping system req ID to upstream items
            user_needs_dict: Dictionary of user need items keyed by API ID
            api_id_key: Key for item ID in API responses
            
        Returns:
            System requirement dictionary with nested user_needs
        """
        system_req_data = build_simple_requirement_data(system_req_item)
        doc_key = get_doc_key(system_req_item)
        
        if self._logger:
            self._logger.debug(
                "Building user needs for system requirement: %s (ID: %d)",
                doc_key,
                system_req_id
            )
        
        # Get upstream items for this system requirement
        upstream_items = system_upstream_results.get(system_req_id, [])
        
        # Filter to only user needs
        connected_user_need_ids = [
            item[api_id_key]
            for item in upstream_items
            if item[api_id_key] in user_needs_dict
        ]
        
        if self._logger:
            self._logger.debug(
                "System requirement %s has %d connected user needs",
                doc_key,
                len(connected_user_need_ids)
            )
        
        # Build user needs list
        user_needs = [
            build_simple_requirement_data(user_needs_dict[user_need_id])
            for user_need_id in connected_user_need_ids
        ]
        
        # Add user_needs to system requirement data
        system_req_with_user_needs = system_req_data.copy()
        system_req_with_user_needs[USER_NEEDS_KEY] = user_needs
        
        return system_req_with_user_needs


class UpstreamHierarchicalTraceAssembler:
    """Handles upstream hierarchical trace assembly (requirement → system reqs → user needs).
    
    This is a refactored version of HierarchicalTraceAssembler logic, extracted
    for use in BidirectionalTraceAssembler. It focuses solely on upstream relationships.
    
    Used by BidirectionalTraceAssembler to build the upstream portion of bidirectional trace.
    """
    
    def __init__(self, logger=None):
        """Initialize assembler with optional logger."""
        self._logger = logger
    
    def build_system_requirements_with_user_needs(
        self,
        system_req_id: int,
        system_req_item: Dict[str, Any],
        system_upstream_results: Dict[int, List[Dict[str, Any]]],
        user_needs_dict: Dict[int, Dict[str, Any]],
        api_id_key: str = "id",
    ) -> Dict[str, Any]:
        """Build single system requirement with nested user needs.
        
        Args:
            system_req_id: System requirement API ID
            system_req_item: System requirement item dictionary
            system_upstream_results: Dictionary mapping system req ID to upstream items
            user_needs_dict: Dictionary of user need items keyed by API ID
            api_id_key: Key for item ID in API responses
            
        Returns:
            System requirement dict with nested user_needs
        """
        system_req_data = build_simple_requirement_data(system_req_item)
        
        # Get upstream items for this system requirement
        upstream_items = system_upstream_results.get(system_req_id, [])
        
        # Filter to only user needs
        connected_user_need_ids = [
            item[api_id_key]
            for item in upstream_items
            if item[api_id_key] in user_needs_dict
        ]
        
        # Build user needs list
        user_needs = [
            build_simple_requirement_data(user_needs_dict[user_need_id])
            for user_need_id in connected_user_need_ids
        ]
        
        # Add user_needs to system requirement data
        system_req_with_user_needs = system_req_data.copy()
        system_req_with_user_needs[USER_NEEDS_KEY] = user_needs
        
        return system_req_with_user_needs


class DownstreamHierarchicalTraceAssembler:
    """Handles downstream assembly (test cases and design docs).
    
    This assembler focuses solely on downstream relationships,
    extracting test cases and design docs from requirements.
    
    Used by BidirectionalTraceAssembler to build the downstream portion of bidirectional trace.
    """
    
    def __init__(self, logger=None):
        """Initialize assembler with optional logger."""
        self._logger = logger
    
    def build_test_cases_and_design_docs(
        self,
        test_cases_dict: Dict[int, Dict[str, Any]],
        design_docs_dict: Dict[int, Dict[str, Any]],
        testcase_typekey: str,
        design_typekey: str,
    ) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Build test cases and design docs lists.
        
        Args:
            test_cases_dict: Dictionary of test case items keyed by API ID
            design_docs_dict: Dictionary of design doc items keyed by API ID
            testcase_typekey: Type key for test cases (for logging)
            design_typekey: Type key for design documents (for logging)
            
        Returns:
            Tuple of (test_cases_list, design_docs_list)
        """
        test_cases = [
            build_simple_test_case_data(item)
            for item in test_cases_dict.values()
        ]
        
        design_docs = [
            build_design_doc_data(item)
            for item in design_docs_dict.values()
        ]
        
        if self._logger:
            self._logger.debug(
                "Built downstream items: %d test cases, %d design docs",
                len(test_cases),
                len(design_docs)
            )
        
        return test_cases, design_docs


class BidirectionalTraceAssembler:
    """Assembles bidirectional trace structure (upstream hierarchy + downstream artifacts).
    
    Combines UpstreamHierarchicalTraceAssembler and DownstreamHierarchicalTraceAssembler
    to create a complete bidirectional structure for each software requirement:
    
    - UPSTREAM: Hierarchical path (requirement → system requirements → user needs)
    - DOWNSTREAM: Test cases and design documents
    
    Output structure:
        [
            {
                "requirement": {"req_id": "REQ-PUMP-101", "text": "..."},
                "system_requirements": [
                    {
                        "req_id": "SYS-PUMP-015",
                        "text": "...",
                        "user_needs": [
                            {"req_id": "UN-PUMP-003", "text": "..."}
                        ]
                    }
                ],
                "test_cases": [
                    {
                        "test_id": "TC-PUMP-201",
                        "description": "...",
                        "setup": "...",
                        "steps": "...",
                        "expectedResults": "...",
                        "in_review_baseline": false
                    }
                ],
                "design_docs": [
                    {
                        "doc_id": "DD-PUMP-RC-001",
                        "name": "...",
                        "description": "..."
                    }
                ]
            },
            ...
        ]
    """
    
    def __init__(self, logger=None):
        """Initialize assembler with optional logger."""
        self._logger = logger
        self._upstream_assembler = UpstreamHierarchicalTraceAssembler(logger=logger)
        self._downstream_assembler = DownstreamHierarchicalTraceAssembler(logger=logger)
    
    def assemble(
        self,
        software_reqs_dict: Dict[int, Dict[str, Any]],
        software_upstream_results: Dict[int, List[Dict[str, Any]]],
        software_downstream_results: Dict[int, List[Dict[str, Any]]],
        system_reqs_dict: Dict[int, Dict[str, Any]],
        system_upstream_results: Dict[int, List[Dict[str, Any]]],
        user_needs_dict: Dict[int, Dict[str, Any]],
        test_cases_dict: Dict[int, Dict[str, Any]],
        design_docs_dict: Dict[int, Dict[str, Any]],
        api_id_key: str = "id",
        testcase_typekey: str = "TEST",
        design_typekey: str = "DES",
        api_id_to_identifier: Optional[Dict[int, str]] = None,
    ) -> List[Dict[str, Any]]:
        """Assemble bidirectional trace for each software requirement.
        
        Args:
            software_reqs_dict: Dictionary of software requirement items keyed by API ID
            software_upstream_results: Dictionary mapping software req ID to upstream items
            software_downstream_results: Dictionary mapping software req ID to downstream items
            system_reqs_dict: Dictionary of system requirement items keyed by API ID
            system_upstream_results: Dictionary mapping system req ID to upstream items
            user_needs_dict: Dictionary of user need items keyed by API ID
            test_cases_dict: Dictionary of test case items keyed by API ID
            design_docs_dict: Dictionary of design document items keyed by API ID
            api_id_key: Key for item ID in API responses
            testcase_typekey: Type key for test cases (for reference)
            design_typekey: Type key for design documents (for reference)
            
        Returns:
            List of software requirement dicts with nested upstream (system_requirements
            with user_needs) and downstream (test_cases, design_docs) structures
        """
        if self._logger:
            self._logger.info("Assembling bidirectional trace structure")
            self._logger.debug(
                "Processing %d software requirements with upstream/downstream traces",
                len(software_reqs_dict)
            )
        
        final_payload = []
        
        for software_req_id, software_req_item in software_reqs_dict.items():
            req_entry = self._build_bidirectional_requirement_entry(
                software_req_id,
                software_req_item,
                software_upstream_results,
                software_downstream_results,
                system_reqs_dict,
                system_upstream_results,
                user_needs_dict,
                test_cases_dict,
                design_docs_dict,
                api_id_key,
                testcase_typekey,
                design_typekey,
                api_id_to_identifier,
            )
            final_payload.append(req_entry)
        
        if self._logger:
            self._logger.info(
                "Assembly complete: %d software requirements with bidirectional traces",
                len(final_payload)
            )
        
        return final_payload
    
    def _build_bidirectional_requirement_entry(
        self,
        software_req_id: int,
        software_req_item: Dict[str, Any],
        software_upstream_results: Dict[int, List[Dict[str, Any]]],
        software_downstream_results: Dict[int, List[Dict[str, Any]]],
        system_reqs_dict: Dict[int, Dict[str, Any]],
        system_upstream_results: Dict[int, List[Dict[str, Any]]],
        user_needs_dict: Dict[int, Dict[str, Any]],
        test_cases_dict: Dict[int, Dict[str, Any]],
        design_docs_dict: Dict[int, Dict[str, Any]],
        api_id_key: str,
        testcase_typekey: str,
        design_typekey: str,
        api_id_to_identifier: Optional[Dict[int, str]] = None,
    ) -> Dict[str, Any]:
        """Build single software requirement with bidirectional trace.
        
        Args:
            software_req_id: Software requirement API ID
            software_req_item: Software requirement item dictionary
            software_upstream_results: Dictionary mapping software req ID to upstream items
            software_downstream_results: Dictionary mapping software req ID to downstream items
            system_reqs_dict: Dictionary of system requirement items keyed by API ID
            system_upstream_results: Dictionary mapping system req ID to upstream items
            user_needs_dict: Dictionary of user need items keyed by API ID
            test_cases_dict: Dictionary of test case items keyed by API ID
            design_docs_dict: Dictionary of design document items keyed by API ID
            api_id_key: Key for item ID in API responses
            testcase_typekey: Type key for test cases
            design_typekey: Type key for design documents
            
        Returns:
            Software requirement dict with nested upstream and downstream structures
        """
        software_req_data = build_simple_requirement_data(software_req_item)
        doc_key = get_doc_key(software_req_item)
        
        # Replace req_id with original identifier if available
        if api_id_to_identifier and software_req_id in api_id_to_identifier:
            software_req_data[REQUIREMENT_ID_KEY] = api_id_to_identifier[software_req_id]
            if self._logger:
                self._logger.debug(
                    "Replaced req_id with original identifier: %s (API ID: %d)",
                    api_id_to_identifier[software_req_id],
                    software_req_id
                )
        
        if self._logger:
            self._logger.debug(
                "Building bidirectional trace for software requirement: %s (ID: %d)",
                doc_key,
                software_req_id
            )
        
        # Build upstream hierarchical structure
        upstream_items = software_upstream_results.get(software_req_id, [])
        
        # Filter to only system requirements
        connected_system_req_ids = [
            item[api_id_key]
            for item in upstream_items
            if item[api_id_key] in system_reqs_dict
        ]
        
        # Build system requirements with nested user needs using upstream assembler
        system_requirements = []
        for system_req_id in connected_system_req_ids:
            system_req_entry = (
                self._upstream_assembler.build_system_requirements_with_user_needs(
                    system_req_id,
                    system_reqs_dict[system_req_id],
                    system_upstream_results,
                    user_needs_dict,
                    api_id_key,
                )
            )
            system_requirements.append(system_req_entry)
        
        if self._logger:
            self._logger.debug(
                "Software requirement %s has %d upstream system requirements",
                doc_key,
                len(system_requirements)
            )
        
        # Build downstream structures specific to this requirement
        downstream_items = software_downstream_results.get(software_req_id, [])
        
        test_cases_list = []
        design_docs_list = []

        # Accept either a single typekey or a list of typekeys for each kind.
        testcase_typekeys = normalize_typekeys(testcase_typekey, [DEFAULT_TESTCASE_TYPEKEY])
        design_typekeys = normalize_typekeys(design_typekey, DEFAULT_DESIGN_TYPEKEYS)

        for item in downstream_items:
            item_doc_key = get_doc_key(item)
            item_id = item[api_id_key]

            if any(tk in item_doc_key for tk in testcase_typekeys) and item_id in test_cases_dict:
                test_cases_list.append(
                    build_simple_test_case_data(test_cases_dict[item_id])
                )
            elif any(tk in item_doc_key for tk in design_typekeys) and item_id in design_docs_dict:
                design_docs_list.append(
                    build_design_doc_data(design_docs_dict[item_id])
                )
        
        if self._logger:
            self._logger.debug(
                "Software requirement %s has %d downstream test cases and %d design docs",
                doc_key,
                len(test_cases_list),
                len(design_docs_list)
            )
        
        # Assemble complete bidirectional entry
        return {
            REQUIREMENT_KEY: software_req_data,
            SYSTEM_REQUIREMENTS_KEY: system_requirements,
            TEST_CASES_KEY: test_cases_list,
            DESIGN_DOCS_KEY: design_docs_list,
        }