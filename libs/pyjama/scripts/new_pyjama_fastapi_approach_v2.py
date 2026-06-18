"""PyJama API - Clean interface for Jama traceability extraction."""
import concurrent.futures
import os
import re
import warnings
from typing import Any, Dict, List, Optional, Tuple, Set, Callable
from src.utils.proj_log import ProjectLogger
from src.utils.gen_utils import make_output_directory
from src.utils.jama_constants import *
from src.utils.jama_utils import (
    get_doc_key,
    extract_version_number,
    map_identifiers_to_api_ids,
)
from src.utils.jama_project_cache import JamaProjectCache
from src.assemblers.jama_assemblers import (
    TestSuiteReviewerAssembler,
    TestCaseReviewerAssembler,
    FlatRTMAssembler,
)


class PyJamaAPI:
    """A clean, single-purpose class to handle Jama traceability extraction."""

    def __init__(
        self, 
        jama_client: Any, 
        data_path: str,
        log_path: str = "logs",
        max_concurrent: int = 100,
        project_cache_folder: Optional[str] = None
    ):
        """
        Initialize PyJamaAPI with Jama client and logging configuration.
        
        Args:
            jama_client: Authenticated JamaClient instance
            data_path: Path for data output
            log_path: Base path for log files
            max_concurrent: Maximum concurrent API requests (1-500)
            project_cache_folder: Override default project cache location
            
        Raises:
            ValueError: If max_concurrent is out of valid range
        """
        # Validate max_concurrent
        if not 1 <= max_concurrent <= 500:
            raise ValueError(
                f"max_concurrent must be between 1 and 500, got {max_concurrent}"
            )
        
        self.client = jama_client
        self.data_path = data_path
        self.max_workers = max_concurrent

        # Set up logging
        self._log_dir = make_output_directory(log_path)
        log_file = os.path.join(self._log_dir, "pyjamaapi.log")
        self._logger = ProjectLogger(
            name=PYJAMA_LOGGERNAME, 
            log_file=log_file
        ).config().get_logger()

        self._logger.info("=" * 60)
        self._logger.info("Initialized PyJamaAPI")
        self._logger.info("Data path: %s", self.data_path)
        self._logger.info("Log directory: %s", self._log_dir)
        self._logger.info("Max concurrent workers: %d", self.max_workers)

        # Set up project cache
        self._project_cache = JamaProjectCache(
            jama_client=jama_client,
            cache_folder=project_cache_folder,
            logger=self._logger
        )       
        
        # Initialize assemblers
        self._test_suite_assembler = TestSuiteReviewerAssembler(logger=self._logger)
        self._test_case_assembler = TestCaseReviewerAssembler(logger=self._logger)
        self._rtm_assembler = FlatRTMAssembler(logger=self._logger)
        
        self._logger.info("=" * 60)
  
    def _fetch_concurrent(
        self,
        fetch_func: Callable,
        item_ids: List[int],
        description: str
    ) -> Dict[int, Any]:
        """Generic concurrent fetch with error handling.
        
        Args:
            fetch_func: Function to call for each item_id
            item_ids: List of item IDs to fetch
            description: Description for logging
            
        Returns:
            Dictionary mapping item_id to result
        """
        results = {}
        failed_ids = []
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {
                executor.submit(fetch_func, item_id): item_id
                for item_id in item_ids
            }
            
            for future in concurrent.futures.as_completed(futures):
                item_id = futures[future]
                try:
                    results[item_id] = future.result()
                except Exception as e:
                    self._logger.error(
                        "Failed %s for ID %d: %s",
                        description,
                        item_id,
                        str(e)
                    )
                    failed_ids.append(item_id)
        
        if failed_ids:
            self._logger.warning(
                "Failed to fetch %d/%d items for %s: %s",
                len(failed_ids),
                len(item_ids),
                description,
                failed_ids[:10]  # Show first 10
            )
        
        return results
    
    def _get_item_by_id(self, item_id: int) -> Dict[str, Any]:
        """Fetch a single Jama item by its numeric ID."""
        self._logger.debug("Fetching item with ID: %d", item_id)
        try:
            item = self.client.get_item(item_id)
            self._logger.debug("Retrieved item ID: %d", item_id)
            return item
        except Exception as e:
            self._logger.error("Failed to fetch item ID %d: %s", item_id, str(e))
            raise

    def _get_upstream(self, _id: int) -> List[Dict[str, Any]]:
        """Fetch upstream related items for a given Jama item ID."""
        self._logger.debug("Fetching upstream items for ID: %d", _id)
        try:
            items = self.client.get_items_upstream_related(_id)
            self._logger.debug("Retrieved %d upstream items for ID: %d", len(items), _id)
            return items
        except Exception as e:
            self._logger.error("Failed to fetch upstream items for ID %d: %s", _id, str(e))
            raise

    def _get_downstream(self, req_id: int) -> Tuple[int, List[Dict[str, Any]]]:
        """Fetch downstream related items for a given requirement ID."""
        self._logger.debug("Fetching downstream items for req_id: %d", req_id)
        try:
            items = self.client.get_items_downstream_related(req_id)
            self._logger.debug("Retrieved %d downstream items for req_id: %d", len(items), req_id)
            return req_id, items
        except Exception as e:
            self._logger.error("Failed to fetch downstream items for req_id %d: %s", req_id, str(e))
            raise

    def _get_upstream_relationships(self, item_id: int) -> Tuple[int, List[Dict[str, Any]]]:
        """Fetch upstream relationships for a given item ID."""
        self._logger.debug("Fetching upstream relationships for item_id: %d", item_id)
        try:
            relationships = self.client.get_items_upstream_relationships(item_id)
            self._logger.debug("Retrieved %d upstream relationships for item_id: %d", len(relationships), item_id)
            return item_id, relationships
        except Exception as e:
            self._logger.error("Failed to fetch upstream relationships for item_id %d: %s", item_id, str(e))
            raise

    def _get_upstream_with_id(self, item_id: int) -> Tuple[int, List[Dict[str, Any]]]:
        """Fetch upstream related items and return with the original item_id."""
        self._logger.debug("Fetching upstream items for item_id: %d", item_id)
        try:
            items = self.client.get_items_upstream_related(item_id)
            self._logger.debug("Retrieved %d upstream items for item_id: %d", len(items), item_id)
            return item_id, items
        except Exception as e:
            self._logger.error("Failed to fetch upstream items for item_id %d: %s", item_id, str(e))
            raise

    def _resolve_baseline_id(
        self,
        project_id: int,
        review_id: str,
        api_id_key: Optional[str] = None,
    ) -> int:
        """
        Resolve baseline ID from project + review_id.
        Assumes baseline naming contains review_id.
        Adjust logic if your Jama instance differs.
        """
        api_id_key = api_id_key or ID_KEY
        
        self._logger.info("Resolving baseline ID for project_id: %d, review_id: '%s'", 
                         project_id, review_id)
        
        try:
            baselines = self.client.get_baselines(project_id)
            self._logger.debug("Retrieved %d baselines for project_id: %d", 
                             len(baselines), project_id)

            matches = [
                baseline
                for baseline in baselines
                if str(review_id) in baseline.get(NAME_KEY, "")
            ]

            if not matches:
                self._logger.error("No baseline found matching review_id '%s'", review_id)
                raise ValueError(f"No baseline found matching review_id '{review_id}'.")

            self._logger.debug("Found %d matching baselines", len(matches))
            
            latest = max(matches, key=lambda baseline: extract_version_number(baseline.get(NAME_KEY, "")))
            baseline_id = latest[api_id_key]
            baseline_name = latest.get(NAME_KEY, "")
            
            self._logger.info("Selected baseline '%s' (ID: %d)", baseline_name, baseline_id)
            return baseline_id
            
        except Exception as e:
            self._logger.error("Error resolving baseline ID: %s", str(e))
            raise
    
    def _resolve_item_type_ids(
        self,
        typekeys: List[str],
        project_id: int,
        api_id_key: Optional[str] = None
    ) -> List[int]:
        """
        Resolve type keys (e.g., 'REQ', 'PRQ') to their integer item type IDs.
        
        Args:
            typekeys: List of type key strings (e.g., ["REQ", "PRQ"])
            project_id: Jama project ID
            api_id_key: Key for item type ID in API responses
            
        Returns:
            List of integer item type IDs
        """
        api_id_key = api_id_key or ID_KEY
        
        self._logger.info("Resolving item type IDs for typekeys: %s", typekeys)
        
        try:
            # Fetch all item types for the project
            item_types = self.client.get_item_types(allowed_results_per_page=ALLOWED_RESULTS_PER_PAGE)
            self._logger.debug("Retrieved %d item types from project", len(item_types))
            
            # Map typekeys to item type IDs
            type_id_map = {}
            for item_type in item_types:
                type_key = item_type.get("typeKey", "")
                if type_key in typekeys:
                    type_id = item_type[api_id_key]
                    type_id_map[type_key] = type_id
                    self._logger.debug("Mapped typekey '%s' -> item_type_id %d", type_key, type_id)
            
            # Check for unresolved typekeys
            unresolved = set(typekeys) - set(type_id_map.keys())
            if unresolved:
                self._logger.warning("Could not resolve typekeys: %s", unresolved)
            
            item_type_ids = list(type_id_map.values())
            self._logger.info("Resolved %d/%d typekeys to item type IDs: %s", 
                             len(item_type_ids), len(typekeys), item_type_ids)
            
            return item_type_ids
            
        except Exception as e:
            self._logger.error("Failed to resolve item type IDs: %s", str(e))
            raise

    def _parse_baseline_id(self, baseline_id: str) -> int:
        """
        Parse baseline ID from 'BASE-12345' format to integer.
        
        Args:
            baseline_id: Baseline identifier string (e.g., 'BASE-84398')
            
        Returns:
            Integer baseline ID
            
        Raises:
            ValueError: If baseline_id format is invalid
            
        Examples:
            >>> _parse_baseline_id("BASE-84398")
            84398
        """
        self._logger.debug("Parsing baseline ID from: '%s'", baseline_id)
        
        # Strict matching (case-sensitive for data quality)
        match = re.match(r"BASE-(\d+)", baseline_id)
        
        if not match:
            self._logger.error("Invalid baseline_id format: '%s'", baseline_id)
            raise ValueError(
                f"Invalid baseline_id format: '{baseline_id}'. "
                f"Expected format: 'BASE-<number>' (e.g., 'BASE-84398')"
            )
        
        baseline_id_int = int(match.group(1))
        self._logger.info("Parsed baseline ID: %d", baseline_id_int)
        
        return baseline_id_int

    def _collect_test_cases_from_baseline(
        self,
        baseline_versioned_items: List[Dict[str, Any]],
        api_id_key: Optional[str] = None
    ) -> Tuple[Set[str], List[int]]:
        """
        Efficiently collect test cases from baseline versioned items by itemType ID.
        
        Uses integer comparison (itemType == TEST_CASE_ITEM_TYPE_ID) instead of
        string matching on typeKeys for better performance: O(n) vs O(n*m).
        
        Args:
            baseline_versioned_items: List of baseline versioned item dictionaries
            api_id_key: Key for item ID in API responses
            
        Returns:
            Tuple of (test_case_doc_keys_set, test_case_api_ids_list)
            
        Performance:
            - Time complexity: O(n) with integer comparison
            - Previous approach: O(n*m) with string matching where m = typeKey length
        """
        api_id_key = api_id_key or ID_KEY
        
        self._logger.debug(
            "Collecting test cases from %d baseline items (itemType == %d)",
            len(baseline_versioned_items),
            TEST_CASE_ITEM_TYPE_ID
        )
        
        test_case_keys = set()
        test_case_ids = []
        
        for item in baseline_versioned_items:
            # Efficient integer comparison instead of string matching
            if item.get("itemType") == TEST_CASE_ITEM_TYPE_ID:
                doc_key = get_doc_key(item)
                test_case_keys.add(doc_key)
                test_case_ids.append(item[api_id_key])
                self._logger.debug("Found test case in baseline: %s (ID: %d)", doc_key, item[api_id_key])
        
        self._logger.info(
            "Collected %d test cases from baseline (itemType filtering)",
            len(test_case_ids)
        )
        
        return test_case_keys, test_case_ids

    def _extract_requirement_ids_from_relationships(
        self,
        relationship_results: List[Tuple[int, List[Dict[str, Any]]]],
        api_id_key: Optional[str] = None
    ) -> Set[int]:
        """
        Extract unique requirement IDs from upstream relationship objects.
        
        Relationship objects contain 'fromItem' and 'toItem' fields with item IDs.
        For upstream relationships, the 'fromItem' is the upstream requirement.
        
        Args:
            relationship_results: List of (test_case_id, relationships_list) tuples
            api_id_key: Key for item ID in relationship objects
            
        Returns:
            Set of unique requirement API IDs
        """
        api_id_key = api_id_key or ID_KEY
        
        self._logger.debug("Extracting requirement IDs from relationship objects")
        
        requirement_ids = set()
        
        for test_case_id, relationships in relationship_results:
            for relationship in relationships:
                # Upstream relationships: fromItem is the upstream requirement
                from_item = relationship.get("fromItem", {})
                req_id = from_item.get(api_id_key)
                
                if req_id:
                    requirement_ids.add(req_id)
                    self._logger.debug(
                        "Found upstream requirement ID %d for test case %d",
                        req_id,
                        test_case_id
                    )
        
        self._logger.info(
            "Extracted %d unique requirement IDs from relationships",
            len(requirement_ids)
        )
        
        return requirement_ids


    def _collect_unique_items_by_typekeys(
        self,
        items: List[Dict[str, Any]],
        retained_typekeys: List[str],
        api_id_key: Optional[str] = None
    ) -> Dict[int, Dict[str, Any]]:
        """
        Collect unique items from a list, filtered by type keys.
        Works with both flat lists and nested lists.
        
        Args:
            items: List of item dictionaries (can be nested)
            retained_typekeys: Type keys to filter by
            api_id_key: Key for item ID in API responses
            
        Returns:
            Dictionary of unique items keyed by item ID
        """
        api_id_key = api_id_key or ID_KEY
        
        self._logger.debug("Collecting unique items by typekeys: %s", retained_typekeys)
        
        unique_items: Dict[int, Dict[str, Any]] = {}
        
        # Flatten if nested
        flat_items = []
        for item in items:
            if isinstance(item, list):
                flat_items.extend(item)
            else:
                flat_items.append(item)
        
        for item in flat_items:
            doc_key = get_doc_key(item)
            if any(key in doc_key for key in retained_typekeys):
                item_id = item[api_id_key]
                unique_items[item_id] = item
        
        self._logger.info("Collected %d unique items", len(unique_items))
        
        return unique_items






    def get_test_suite_reviewer_structure(
        self,
        baseline_id: str,
        api_id_key: Optional[str] = None,
        design_typekey: Optional[str] = None,
        testcase_typekey: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Extract test suite reviewer structure from a Jama baseline.
        
        Optimized workflow:
        1. Parse baseline ID from 'BASE-12345' format
        2. Fetch baseline versioned items
        3. Filter test cases by itemType (O(n) integer comparison)
        4. Fetch upstream relationships (lighter API calls)
        5. Extract unique requirement IDs from relationships
        6. Fetch requirement items and downstream items concurrently
        7. Assemble final structure with in_baseline flags
        
        Performance improvements over previous version:
        - No baseline resolution API calls (direct ID input)
        - Efficient test case filtering: O(n) integer comparison vs O(n*m) string matching
        - Uses relationships API instead of full items (lighter payload)
        - Separate assembly logic for better maintainability
        
        Args:
            baseline_id: Baseline identifier (e.g., 'BASE-84398')
            api_id_key: Key for item ID in API responses (default: 'id')
            design_typekey: Type key for design documents (default: 'DES')
            testcase_typekey: Type key for test cases (default: 'TEST')
            
        Returns:
            List of requirement dictionaries, each containing:
            - requirement: {req_id, text}
            - test_cases: [{test_id, description, setup, steps, expectedResults, in_review_baseline}]
            - design_docs: [{doc_id, name, description}]
            
        Raises:
            ValueError: If baseline_id format is invalid or no test cases found
            
        Example:
            >>> api = PyJamaAPI(client, data_path="./data")
            >>> result = api.get_test_suite_reviewer_structure(
            ...     baseline_id="BASE-84398"
            ... )
            >>> print(f"Found {len(result)} requirements")
            Found 42 requirements
        """
        self._logger.info("=" * 60)
        self._logger.info("Starting test suite reviewer structure extraction")
        self._logger.info("Baseline ID: %s", baseline_id)
        self._logger.info("=" * 60)
        
        # Set defaults
        api_id_key = api_id_key or ID_KEY
        design_typekey = design_typekey or DEFAULT_DESIGN_TYPEKEY
        testcase_typekey = testcase_typekey or DEFAULT_TESTCASE_TYPEKEY

        try:
            # Step 1: Parse baseline ID from 'BASE-12345' format
            self._logger.info("Step 1: Parsing baseline ID")
            baseline_id_int = self._parse_baseline_id(baseline_id)

            # Step 2: Fetch baseline versioned items
            self._logger.info("Step 2: Fetching baseline versioned items for baseline_id: %d", baseline_id_int)
            raw_review_items = self.client.get_baselines_versioneditems(baseline_id_int)
            self._logger.info("Retrieved %d baseline versioned items", len(raw_review_items))

            # Step 3: Collect test cases efficiently by itemType
            self._logger.info("Step 3: Collecting test cases from baseline (itemType filtering)")
            review_test_keys, review_test_ids = self._collect_test_cases_from_baseline(
                raw_review_items,
                api_id_key=api_id_key
            )
            
            if not review_test_ids:
                self._logger.warning("No test cases found in baseline. Returning empty result.")
                return []

            # Step 4: Fetch upstream relationships in parallel (lighter than full items)
            self._logger.info(
                "Step 4: Fetching upstream relationships for %d test cases",
                len(review_test_ids)
            )
            
            relationship_results = []
            with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                relationship_futures = {
                    executor.submit(self._get_upstream_relationships, test_id): test_id
                    for test_id in review_test_ids
                }
                
                for future in concurrent.futures.as_completed(relationship_futures):
                    test_id, relationships = future.result()
                    relationship_results.append((test_id, relationships))
            
            self._logger.info(
                "Completed upstream relationship fetch for %d test cases",
                len(review_test_ids)
            )

            # Step 5: Extract unique requirement IDs from relationships
            self._logger.info("Step 5: Extracting requirement IDs from relationships")
            requirement_ids = self._extract_requirement_ids_from_relationships(
                relationship_results,
                api_id_key=api_id_key
            )

            if not requirement_ids:
                self._logger.warning(
                    "No requirements found upstream from test cases. Returning empty result."
                )
                return []

            # Step 6: Fetch requirement items and downstream items concurrently
            self._logger.info(
                "Step 6: Fetching %d requirement items and their downstream items",
                len(requirement_ids)
            )
            
            requirements_dict = {}
            downstream_results_dict = {}
            
            with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                # Fetch requirement items
                req_item_futures = {
                    executor.submit(self._get_item_by_id, req_id): req_id
                    for req_id in requirement_ids
                }
                
                # Fetch downstream items
                downstream_futures = {
                    executor.submit(self._get_downstream, req_id): req_id
                    for req_id in requirement_ids
                }
                
                # Collect requirement items
                for future in concurrent.futures.as_completed(req_item_futures):
                    req_id = req_item_futures[future]
                    req_item = future.result()
                    requirements_dict[req_id] = req_item
                
                # Collect downstream items
                for future in concurrent.futures.as_completed(downstream_futures):
                    req_id, downstream_items = future.result()
                    downstream_results_dict[req_id] = downstream_items
            
            self._logger.info(
                "Retrieved %d requirement items and downstream data",
                len(requirements_dict)
            )

            # Step 7: Assemble final structure using assembler
            self._logger.info("Step 7: Assembling test suite reviewer structure")
            final_payload = self._test_suite_assembler.assemble(
                requirements_dict=requirements_dict,
                downstream_results=downstream_results_dict,
                review_test_keys=review_test_keys,
                testcase_typekey=testcase_typekey,
                design_typekey=design_typekey,
            )

            self._logger.info("=" * 60)
            self._logger.info("Completed test suite reviewer structure extraction")
            self._logger.info("Total requirements processed: %d", len(final_payload))
            
            # Summary statistics
            total_tests = sum(len(req[TEST_CASES_KEY]) for req in final_payload)
            baseline_tests = sum(
                sum(1 for tc in req[TEST_CASES_KEY] if tc.get(IN_REVIEW_BASELINE_KEY, False))
                for req in final_payload
            )
            total_design_docs = sum(len(req[DESIGN_DOCS_KEY]) for req in final_payload)
            
            self._logger.info("Summary:")
            self._logger.info("  Requirements: %d", len(final_payload))
            self._logger.info("  Test cases (total): %d", total_tests)
            self._logger.info("  Test cases (in baseline): %d", baseline_tests)
            self._logger.info("  Test cases (not in baseline): %d", total_tests - baseline_tests)
            self._logger.info("  Design documents: %d", total_design_docs)
            self._logger.info("=" * 60)
            
            return final_payload
            
        except Exception as e:
            self._logger.error(
                "Fatal error in get_test_suite_reviewer_structure: %s",
                str(e),
                exc_info=True
            )
            raise

    def get_rtm_from_gids(
        self,
        identifiers: List[str],
        project_name: str,
        api_id_key: Optional[str] = None,
        design_typekey: Optional[str] = None,
        testcase_typekey: Optional[str] = None,
        user_need_typekey: Optional[str] = None,
        prq_type_field: str = "PRQ_type$63",
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        Build a flat RTM (Requirements Traceability Matrix) from JAMA identifiers.
        
        Accepts both GIDs (e.g., "GID-2788627") and document keys (e.g., "PRQ-123", "REQ-456").
        The function automatically detects the identifier format and maps accordingly.
        
        Workflow:
        1. Fetch all abstract items filtered by REQUIREMENT_ITEM_TYPE_ID
        2. Map input identifiers to API IDs (item_ids)
        3. Create subset of abstract items matching item_ids (input_items_dict)
        4. From ALL abstract items, create system_requirements_dict based on PRQ_type$63
        5. Fetch upstream and downstream for ALL input identifiers (item_ids)
        6. Identify system requirements in upstream results (connected_system_reqs)
        7. Fetch upstream for connected system requirements to get user needs
        8. Return complete trace: User Needs → System Reqs → Software Reqs → Tests/Design
        
        Args:
            identifiers: List of JAMA identifier strings (GIDs or document keys)
                        Examples: ["GID-2788627", "PRQ-123", "REQ-456"]
            project_name: Name of the Jama project (required)
            api_id_key: Key for item ID in API responses
            design_typekey: Type key for design documents
            testcase_typekey: Type key for test cases
            user_need_typekey: Type key for user needs
            prq_type_field: Field name for requirement type pick list (default: "PRQ_type$63")
            
        Returns:
            Dictionary with keys: user_needs, system_requirements, requirements,
            test_cases, design_docs
        """
        self._logger.info("=" * 60)
        self._logger.info("Starting RTM extraction from identifiers")
        self._logger.info("Input identifiers: %d", len(identifiers))
        self._logger.info("Project: %s", project_name)
        self._logger.info("=" * 60)
        
        # Set defaults
        api_id_key = api_id_key or ID_KEY
        design_typekey = design_typekey or DEFAULT_DESIGN_TYPEKEY
        testcase_typekey = testcase_typekey or DEFAULT_TESTCASE_TYPEKEY
        user_need_typekey = user_need_typekey or DEFAULT_USER_NEED_TYPEKEY
        
        try:
            # Step 1: Resolve project name to ID using cache
            self._logger.info("Step 1: Resolving project name to ID")
            project_id = self._project_cache.resolve_project_id(project_name, api_id_key=api_id_key)
            
            # Step 2a: Fetch abstract items filtered by REQUIREMENT_ITEM_TYPE_ID
            self._logger.info("Step 2a: Fetching abstract items for project_id: %d", project_id)
            abstract_items = self.client.get_abstract_items(
                project=project_id,
                item_type=[REQUIREMENT_ITEM_TYPE_ID]
            )
            self._logger.info("Retrieved %d abstract items", len(abstract_items))
            
            # Step 2b: Map identifiers to API IDs
            self._logger.info("Step 2b: Mapping identifiers to API IDs")
            id_to_api_id, unresolved_ids = map_identifiers_to_api_ids(
                items=abstract_items,
                identifiers=identifiers,
                api_id_key=api_id_key,
                raise_on_empty=True,
                logger=self._logger,
            )
            
            item_ids = list(id_to_api_id.values())
            self._logger.info(
                "Successfully resolved %d/%d identifiers to API IDs",
                len(item_ids),
                len(identifiers)
            )

            # Step 2c: Create subset of abstract items matching input identifiers
            self._logger.info("Step 2c: Creating subset of input items")
            input_items_dict = {}
            for item in abstract_items:
                item_id = item[api_id_key]
                if item_id in item_ids:
                    input_items_dict[item_id] = item
            
            self._logger.info("Created input_items_dict with %d items", len(input_items_dict))

            # Step 3: Create system requirements dict from ALL abstract items
            self._logger.info("Step 3: Creating system requirements dict from ALL abstract items")
            system_requirements_dict = {}
            
            for item in abstract_items:
                item_id = item[api_id_key]
                fields = item.get(FIELDS_KEY, {})
                prq_type_id = fields.get(prq_type_field)
                
                if prq_type_id == SYSTEM_REQUIREMENT_TYPE_ID:
                    system_requirements_dict[item_id] = item
                    self._logger.debug("Classified %s as system requirement", get_doc_key(item))
            
            self._logger.info("Created system_requirements_dict with %d items", len(system_requirements_dict))
            
            # Step 3b: Partition input items into software vs system requirements
            self._logger.info("Step 3b: Partitioning input items into software vs system requirements")
            input_software_reqs_dict = {}
            input_system_reqs_dict = {}
            
            for item_id, item in input_items_dict.items():
                fields = item.get(FIELDS_KEY, {})
                prq_type_id = fields.get(prq_type_field)
                
                if prq_type_id == SYSTEM_REQUIREMENT_TYPE_ID:
                    input_system_reqs_dict[item_id] = item
                    self._logger.debug("Input item %s is system requirement", get_doc_key(item))
                else:
                    input_software_reqs_dict[item_id] = item
                    self._logger.debug("Input item %s is software requirement", get_doc_key(item))
            
            self._logger.info(
                "Partitioned input items: %d software requirements, %d system requirements",
                len(input_software_reqs_dict),
                len(input_system_reqs_dict)
            )
            
            # Step 4: Fetch upstream and downstream for ALL input identifiers
            self._logger.info("Step 4: Fetching upstream and downstream for %d input identifiers", len(item_ids))
            
            all_upstream_items = []
            all_downstream_items = []
            
            with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                # Fetch upstream for all input identifiers
                self._logger.info("Step 4a: Fetching upstream items for all input identifiers")
                upstream_futures = {
                    executor.submit(self._get_upstream_with_id, item_id): item_id
                    for item_id in item_ids
                }
                
                # Fetch downstream for all input identifiers
                self._logger.info("Step 4b: Fetching downstream items for all input identifiers")
                downstream_futures = {
                    executor.submit(self._get_downstream, item_id): item_id
                    for item_id in item_ids
                }
                
                # Collect upstream results
                for future in concurrent.futures.as_completed(upstream_futures):
                    item_id, upstream_items = future.result()
                    all_upstream_items.extend(upstream_items)
                    self._logger.debug("Collected %d upstream items for item_id: %d", len(upstream_items), item_id)
                
                # Collect downstream results
                for future in concurrent.futures.as_completed(downstream_futures):
                    item_id, downstream_items = future.result()
                    all_downstream_items.extend(downstream_items)
                    self._logger.debug("Collected %d downstream items for item_id: %d", len(downstream_items), item_id)
            
            self._logger.info("Retrieved %d total upstream items", len(all_upstream_items))
            self._logger.info("Retrieved %d total downstream items", len(all_downstream_items))
            
            # Step 5: Identify connected system requirements from upstream results
            self._logger.info("Step 5: Identifying connected system requirements from upstream results")
            connected_system_reqs_dict = {}
            
            for item in all_upstream_items:
                item_id = item[api_id_key]
                if item_id in system_requirements_dict:
                    connected_system_reqs_dict[item_id] = system_requirements_dict[item_id]
                    self._logger.debug(
                        "Found connected system requirement: %s (ID: %d)",
                        get_doc_key(item),
                        item_id
                    )
            
            self._logger.info(
                "Identified %d connected system requirements from upstream results",
                len(connected_system_reqs_dict)
            )
            
            # Step 6: Fetch user needs from connected system requirements
            self._logger.info(
                "Step 6: Fetching user needs from %d connected system requirements",
                len(connected_system_reqs_dict)
            )
            
            user_needs_upstream_items = []
            
            if connected_system_reqs_dict:
                with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                    user_needs_futures = list(executor.map(
                        self._get_upstream,
                        connected_system_reqs_dict.keys()
                    ))
                    
                    # Flatten results
                    for upstream_list in user_needs_futures:
                        user_needs_upstream_items.extend(upstream_list)
                
                self._logger.info(
                    "Retrieved %d total upstream items from connected system requirements",
                    len(user_needs_upstream_items)
                )
            
            # Collect unique user needs
            user_needs_dict = self._collect_unique_items_by_typekeys(
                user_needs_upstream_items,
                [user_need_typekey],
                api_id_key=api_id_key
            )
            
            # Step 7: Collect test cases and design docs from downstream results
            self._logger.info("Step 7: Collecting test cases and design docs from downstream results")
            
            test_cases_dict = self._collect_unique_items_by_typekeys(
                all_downstream_items,
                [testcase_typekey],
                api_id_key=api_id_key
            )
            
            design_docs_dict = self._collect_unique_items_by_typekeys(
                all_downstream_items,
                [design_typekey],
                api_id_key=api_id_key
            )
            
            # Step 8: Assemble flat RTM output structure using assembler
            self._logger.info("Step 8: Assembling flat RTM output structure")
            
            result = self._rtm_assembler.assemble(
                user_needs_dict=user_needs_dict,
                connected_system_reqs_dict=connected_system_reqs_dict,
                input_software_reqs_dict=input_software_reqs_dict,
                test_cases_dict=test_cases_dict,
                design_docs_dict=design_docs_dict,
            )
            
            self._logger.info("=" * 60)
            self._logger.info("Completed RTM extraction from identifiers")
            self._logger.info("=" * 60)
            
            return result
            
        except Exception as e:
            self._logger.error("Fatal error in get_rtm_from_gids: %s", 
                             str(e), exc_info=True)
            raise

    def get_test_case_reviewer_structure(
        self,
        baseline_id: str,
        api_id_key: Optional[str] = None,
        design_typekey: Optional[str] = None,
        requirement_typekeys: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Extract test case reviewer structure from a Jama baseline.
        
        Organizes data by test case (not requirement) with upstream requirements
        and design docs. This is the inverse of get_test_suite_reviewer_structure.
        
        Optimized workflow:
        1. Parse baseline ID from 'BASE-12345' format
        2. Fetch baseline versioned items
        3. Filter test cases by itemType (O(n) integer comparison)
        4. Fetch upstream items for all test cases concurrently
        5. Separate upstream items by type (requirements vs design docs)
        6. Assemble final structure organized by test case
        
        Performance optimizations:
        - Efficient test case filtering: O(n) integer comparison on itemType
        - Concurrent upstream fetches using ThreadPoolExecutor
        - Direct upstream item fetching (no relationship objects needed)
        - Type filtering using document key matching
        
        Args:
            baseline_id: Baseline identifier (e.g., 'BASE-84398')
            api_id_key: Key for item ID in API responses (default: 'id')
            design_typekey: Type key for design documents (default: 'DES')
            requirement_typekeys: Type keys for requirements (default: ['REQ', 'PRQ'])
            
        Returns:
            List of test case dictionaries, each containing:
            - test_case: {test_id, description, setup, steps, expectedResults}
            - requirements: [{req_id, text}, ...]
            - design_docs: [{doc_id, name, description}, ...]
            
        Raises:
            ValueError: If baseline_id format is invalid or no test cases found
            
        Example:
            >>> api = PyJamaAPI(client, data_path="./data")
            >>> result = api.get_test_case_reviewer_structure(
            ...     baseline_id="BASE-84398"
            ... )
            >>> print(f"Found {len(result)} test cases")
            Found 42 test cases
            >>> 
            >>> # Example output structure:
            >>> result[0]
            {
                "test_case": {
                    "test_id": "TC-PUMP-202",
                    "description": "Fault injection — simulate scheduler stall...",
                    "setup": "Pump in standard infusion mode...",
                    "steps": "Step 1. Start an infusion at 5 mL/hr...",
                    "expectedResults": "ExpectedResult 1. Watchdog counter stalls..."
                },
                "requirements": [
                    {"req_id": "REQ-PUMP-101", "text": "The rate-control loop..."},
                    {"req_id": "REQ-PUMP-102", "text": "The UI thread shall..."}
                ],
                "design_docs": [
                    {"doc_id": "DD-PUMP-RC-001", "name": "Rate Control Loop...", "description": "..."}
                ]
            }
        """
        self._logger.info("=" * 60)
        self._logger.info("Starting test case reviewer structure extraction")
        self._logger.info("Baseline ID: %s", baseline_id)
        self._logger.info("=" * 60)
        
        # Set defaults
        api_id_key = api_id_key or ID_KEY
        design_typekey = design_typekey or DEFAULT_DESIGN_TYPEKEY
        requirement_typekeys = requirement_typekeys or list(DEFAULT_REQ_TYPEKEYS)
        
        try:
            # Step 1: Parse baseline ID from 'BASE-12345' format
            self._logger.info("Step 1: Parsing baseline ID")
            baseline_id_int = self._parse_baseline_id(baseline_id)
            
            # Step 2: Fetch baseline versioned items
            self._logger.info(
                "Step 2: Fetching baseline versioned items for baseline_id: %d",
                baseline_id_int
            )
            raw_baseline_items = self.client.get_baselines_versioneditems(baseline_id_int)
            self._logger.info("Retrieved %d baseline versioned items", len(raw_baseline_items))
            
            # Step 3: Collect test cases efficiently by itemType
            self._logger.info("Step 3: Collecting test cases from baseline (itemType filtering)")
            _, test_case_ids = self._collect_test_cases_from_baseline(
                raw_baseline_items,
                api_id_key=api_id_key
            )
            
            if not test_case_ids:
                self._logger.warning("No test cases found in baseline. Returning empty result.")
                return []
            
            # Create test cases dict from baseline items
            test_cases_dict = {}
            for item in raw_baseline_items:
                if item.get("itemType") == TEST_CASE_ITEM_TYPE_ID:
                    item_id = item[api_id_key]
                    test_cases_dict[item_id] = item
            
            self._logger.info("Created test_cases_dict with %d items", len(test_cases_dict))
            
            # Step 4: Fetch upstream items for all test cases concurrently
            self._logger.info(
                "Step 4: Fetching upstream items for %d test cases",
                len(test_case_ids)
            )
            
            upstream_results_dict = {}
            
            with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                upstream_futures = {
                    executor.submit(self._get_upstream_with_id, test_id): test_id
                    for test_id in test_case_ids
                }
                
                for future in concurrent.futures.as_completed(upstream_futures):
                    test_id, upstream_items = future.result()
                    upstream_results_dict[test_id] = upstream_items
                    self._logger.debug(
                        "Collected %d upstream items for test case ID: %d",
                        len(upstream_items),
                        test_id
                    )
            
            total_upstream = sum(len(items) for items in upstream_results_dict.values())
            self._logger.info(
                "Completed upstream fetch for %d test cases (%d total upstream items)",
                len(test_case_ids),
                total_upstream
            )
            
            # Step 5: Assemble final structure using assembler
            self._logger.info("Step 5: Assembling test case reviewer structure")
            final_payload = self._test_case_assembler.assemble(
                test_cases_dict=test_cases_dict,
                upstream_results=upstream_results_dict,
                requirement_typekeys=requirement_typekeys,
                design_typekey=design_typekey,
                api_id_key=api_id_key,
            )
            
            self._logger.info("=" * 60)
            self._logger.info("Completed test case reviewer structure extraction")
            self._logger.info("Total test cases processed: %d", len(final_payload))
            
            # Summary statistics
            total_requirements = sum(len(tc[REQUIREMENTS_KEY]) for tc in final_payload)
            total_design_docs = sum(len(tc[DESIGN_DOCS_KEY]) for tc in final_payload)
            
            # Calculate test cases with/without requirements
            test_cases_with_reqs = sum(
                1 for tc in final_payload if len(tc[REQUIREMENTS_KEY]) > 0
            )
            test_cases_without_reqs = len(final_payload) - test_cases_with_reqs
            
            self._logger.info("Summary:")
            self._logger.info("  Test cases: %d", len(final_payload))
            self._logger.info("  Test cases with requirements: %d", test_cases_with_reqs)
            self._logger.info("  Test cases without requirements: %d", test_cases_without_reqs)
            self._logger.info("  Total requirements: %d", total_requirements)
            self._logger.info("  Total design documents: %d", total_design_docs)
            self._logger.info("=" * 60)
            
            return final_payload
            
        except Exception as e:
            self._logger.error(
                "Fatal error in get_test_case_reviewer_structure: %s",
                str(e),
                exc_info=True
            )
            raise

    # Backward compatibility aliases
    def get_hierarchical_traceability_from_identifiers(
        self,
        identifiers: List[str],
        project_name: str,
        api_id_key: Optional[str] = None,
        design_typekey: Optional[str] = None,
        testcase_typekey: Optional[str] = None,
        user_need_typekey: Optional[str] = None,
        prq_type_field: str = "PRQ_type$63",
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        Backward-compatible alias for get_rtm_from_gids.
        
        DEPRECATED: Use get_rtm_from_gids instead. This method will be removed
        in a future version.
        
        Args:
            identifiers: List of JAMA identifier strings (GIDs or document keys)
            project_name: Name of the Jama project (required)
            api_id_key: Key for item ID in API responses
            design_typekey: Type key for design documents
            testcase_typekey: Type key for test cases
            user_need_typekey: Type key for user needs
            prq_type_field: Field name for requirement type pick list
            
        Returns:
            Dictionary with keys: user_needs, system_requirements, requirements,
            test_cases, design_docs
        """
        warnings.warn(
            "get_hierarchical_traceability_from_identifiers is deprecated, "
            "use get_rtm_from_gids instead",
            DeprecationWarning,
            stacklevel=2
        )
        return self.get_rtm_from_gids(
            identifiers=identifiers,
            project_name=project_name,
            api_id_key=api_id_key,
            design_typekey=design_typekey,
            testcase_typekey=testcase_typekey,
            user_need_typekey=user_need_typekey,
            prq_type_field=prq_type_field,
        )

    def get_hierarchical_traceability_from_gids(
        self,
        gids: List[str],
        project_name: str,
        api_id_key: Optional[str] = None,
        design_typekey: Optional[str] = None,
        testcase_typekey: Optional[str] = None,
        user_need_typekey: Optional[str] = None,
        prq_type_field: str = "PRQ_type$63",
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        Backward-compatible alias for get_rtm_from_gids.
        
        DEPRECATED: Use get_rtm_from_gids instead. This method will be removed
        in a future version.
        
        Args:
            gids: List of JAMA GID strings (e.g., ["GID-2788627", "GID-2788628"])
            project_name: Name of the Jama project (required)
            api_id_key: Key for item ID in API responses
            design_typekey: Type key for design documents
            testcase_typekey: Type key for test cases
            user_need_typekey: Type key for user needs
            prq_type_field: Field name for requirement type pick list
            
        Returns:
            Dictionary with keys: user_needs, system_requirements, requirements,
            test_cases, design_docs
        """
        warnings.warn(
            "get_hierarchical_traceability_from_gids is deprecated, "
            "use get_rtm_from_gids instead",
            DeprecationWarning,
            stacklevel=2
        )
        return self.get_rtm_from_gids(
            identifiers=gids,
            project_name=project_name,
            api_id_key=api_id_key,
            design_typekey=design_typekey,
            testcase_typekey=testcase_typekey,
            user_need_typekey=user_need_typekey,
            prq_type_field=prq_type_field,
        )
