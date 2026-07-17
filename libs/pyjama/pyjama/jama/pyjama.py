"""PyJamaTraceMatrix - Clean interface for Jama traceability extraction."""
import concurrent.futures
import logging
import os
import re
import warnings
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Set, Callable, Union
from pyjama.utils.proj_log import ProjectLogger, timing
from pyjama.utils.gen_utils import make_output_directory
from pyjama.utils.jama_constants import *
from pyjama.utils.jama_utils import (
    get_doc_key,
    normalize_typekeys,
    extract_version_number,
    map_identifiers_to_api_ids,
)
from pyjama.utils.jama_project_cache import JamaProjectCache
from pyjama.utils.cache_manager import DiskCacheManager, CacheMode, CacheMissError
from pyjama.assemblers.jama_assemblers import (
    TestSuiteReviewerAssembler,
    TestCaseReviewerAssembler,
    FlatRTMAssembler,
    HierarchicalTraceAssembler,
    BidirectionalTraceAssembler,
)


class PyJamaTraceMatrix:
    """A clean, single-purpose class to handle Jama traceability extraction."""

    def __init__(
        self,
        jama_client: Any,
        data_path: str,
        log_path: str = "logs",
        max_concurrent: int = 100,
        project_cache_folder: Optional[str] = None,
        cache_mode: CacheMode = CacheMode.USE,
        test_mode: bool = False,
        log_dir: Optional[str] = None,
        logger: Optional[logging.Logger] = None,
        enable_cache: bool = True,
        inputs_file_name: str = "pyjama_inputs.jsonl",
        outputs_file_name: str = "pyjama_outputs.jsonl",
    ):
        """
        Initialize PyJamaTraceMatrix with Jama client and logging configuration.

        Args:
            jama_client: Authenticated JamaClient instance. May be ``None`` when
                ``test_mode`` is True (no live API calls are ever made).
            data_path: Path for data output
            log_path: Base path for log files
            max_concurrent: Maximum concurrent API requests (1-500)
            project_cache_folder: Override default project cache location
            cache_mode: Tier-3 disk cache behavior (OFF / USE / REFRESH).
                Accepts a CacheMode or its string value ("off"/"use"/"refresh").
            test_mode: When True, the instance is strictly cache-only. Every
                public method returns the seeded tier-3 cache artifact keyed by
                its baseline_id / identifiers and never contacts the Jama API;
                a cache miss raises :class:`CacheMissError`. Intended for running
                downstream workflows with mock/absent Jama credentials.
            log_dir: Optional pre-resolved log directory. When provided, it is used
                as-is (created if missing) instead of generating a new timestamped
                run directory under ``log_path``. Lets a caller (e.g. the LangGraph
                node) share a single run directory across components.
            logger: Optional pre-configured logger. When provided, it is used
                directly instead of building a new :class:`ProjectLogger`. Must use
                the name :data:`PYJAMA_LOGGERNAME` so the ``@timing`` decorators
                resolve to the same logger.
            enable_cache: When False, disables tier-3 disk caching (overrides cache_mode).
                Default True.
            inputs_file_name: Customizable name for input JSONL file. Default "pyjama_inputs.jsonl".
            outputs_file_name: Customizable name for output JSONL file. Default "pyjama_outputs.jsonl".

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
        self._test_mode = test_mode
        self._enable_cache = enable_cache
        self.inputs_file_name = inputs_file_name
        self.outputs_file_name = outputs_file_name
        
        # If caching is disabled, override cache_mode to OFF
        effective_cache_mode = CacheMode.OFF if not enable_cache else cache_mode
        self._cache_mode = effective_cache_mode if isinstance(effective_cache_mode, CacheMode) else CacheMode(effective_cache_mode)

        # Set up logging. A caller may supply an already-resolved run directory
        # and/or a pre-configured logger (so node + matrix + cache share one log
        # file); otherwise check if pytest logging is initialized for test mode.
        if log_dir is not None:
            self._log_dir = log_dir
            Path(self._log_dir).mkdir(parents=True, exist_ok=True)
        else:
            self._log_dir = make_output_directory(log_path)

        if logger is not None:
            # Use provided logger directly
            self._logger = logger
        else:
            # Check if pytest session logger is available (test mode)
            try:
                from pyjama.utils.pytest_log_config import get_pytest_logger, get_pytest_log_dir
                self._logger = get_pytest_logger()
                self._log_dir = str(get_pytest_log_dir())
            except RuntimeError:
                # Pytest logging not initialized, create standalone logger
                log_file = os.path.join(self._log_dir, "pyjama.log")
                self._logger = ProjectLogger(
                    name=PYJAMA_LOGGERNAME,
                    log_file=log_file
                ).config().get_logger()

        self._logger.info("=" * 60)
        self._logger.info("Initialized PyJamaTraceMatrix")
        self._logger.info("Data path: %s", self.data_path)
        self._logger.info("Log directory: %s", self._log_dir)
        self._logger.info("Max concurrent workers: %d", self.max_workers)
        self._logger.info("Cache mode: %s", self._cache_mode.value)
        self._logger.info("Caching enabled: %s", self._enable_cache)
        self._logger.info("Input file name: %s", self.inputs_file_name)
        self._logger.info("Output file name: %s", self.outputs_file_name)
        self._logger.info("Test mode (cache-only): %s", self._test_mode)

        # Set up Tier-3 disk cache manager
        self._cache = DiskCacheManager(mode=self._cache_mode, logger=self._logger)

        # Set up project cache (shares the tier-3 disk manager + cache root)
        self._project_cache = JamaProjectCache(
            jama_client=jama_client,
            cache_manager=self._cache,
            cache_folder=project_cache_folder,
            logger=self._logger,
        )
        
        # Initialize assemblers
        self._test_suite_assembler = TestSuiteReviewerAssembler(logger=self._logger)
        self._test_case_assembler = TestCaseReviewerAssembler(logger=self._logger)
        self._rtm_assembler = FlatRTMAssembler(logger=self._logger)
        self._hierarchical_trace_assembler = HierarchicalTraceAssembler(logger=self._logger)
        self._bidirectional_trace_assembler = BidirectionalTraceAssembler(logger=self._logger)
        
        self._logger.info("=" * 60)
  
    @timing(PYJAMA_LOGGERNAME)
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
        """Fetch upstream relationships for a given item ID.
        
        Gracefully handles missing or renamed items by returning empty list instead of raising.
        This allows workflows to continue even if an item has been deleted from Jama.
        """
        self._logger.debug("Fetching upstream relationships for item_id: %d", item_id)
        try:
            relationships = self.client.get_items_upstream_relationships(item_id)
            self._logger.debug("Retrieved %d upstream relationships for item_id: %d", len(relationships), item_id)
            return item_id, relationships
        except Exception as e:
            self._logger.warning(
                "Failed to fetch upstream relationships for item_id %d (item may be missing or renamed): %s",
                item_id,
                str(e)
            )
            # Return empty list to allow workflow to continue
            return item_id, []

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

    @timing(PYJAMA_LOGGERNAME)
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
    
    @timing(PYJAMA_LOGGERNAME)
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

    @timing(PYJAMA_LOGGERNAME)
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

    @timing(PYJAMA_LOGGERNAME)
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

    @timing(PYJAMA_LOGGERNAME)
    def _collect_requirements_from_baseline(
        self,
        baseline_versioned_items: List[Dict[str, Any]],
        api_id_key: Optional[str] = None
    ) -> Tuple[Set[str], List[int]]:
        """Collect requirement ids directly from baseline versioned items by itemType.

        Mirror of :meth:`_collect_test_cases_from_baseline` for the requirement-review
        workflow, where the baseline's items are requirements (not test cases). Keeps
        items whose integer ``itemType`` is in ``REQUIREMENT_PRIMARY_ITEM_TYPE_IDS``
        (software + system requirements), excluding modules/folders and other types.

        Args:
            baseline_versioned_items: List of baseline versioned item dictionaries.
            api_id_key: Key for item ID in API responses (default: ``"id"``).

        Returns:
            Tuple of (requirement_doc_keys_set, requirement_api_ids_list).
        """
        api_id_key = api_id_key or ID_KEY

        self._logger.debug(
            "Collecting requirements from %d baseline items (itemType in %s)",
            len(baseline_versioned_items),
            REQUIREMENT_PRIMARY_ITEM_TYPE_IDS,
        )

        req_keys: Set[str] = set()
        req_ids: List[int] = []

        for item in baseline_versioned_items:
            if item.get("itemType") in REQUIREMENT_PRIMARY_ITEM_TYPE_IDS:
                doc_key = get_doc_key(item)
                req_keys.add(doc_key)
                req_ids.append(item[api_id_key])
                self._logger.debug("Found requirement in baseline: %s (ID: %d)", doc_key, item[api_id_key])

        self._logger.info(
            "Collected %d requirements from baseline (itemType filtering)",
            len(req_ids),
        )

        return req_keys, req_ids

    @timing(PYJAMA_LOGGERNAME)
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
                req_id = from_item
                
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


    @timing(PYJAMA_LOGGERNAME)
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






    # ------------------------------------------------------------------
    # Tier-3 disk cache helpers
    # ------------------------------------------------------------------
    def _require_cached(self, cached, description: str, folder: str):
        """Return ``cached`` in test_mode, or raise a clear CacheMissError.

        Used by the test_mode short-circuit at the top of each public method:
        the instance never calls the Jama API, so a missing cache entry is fatal.
        """
        if cached is not None:
            return cached
        raise CacheMissError(
            f"test_mode: no cached result for {description}. "
            f"Searched: {folder}. Seed the cache for this request or disable test_mode."
        )

    @staticmethod
    def _get_item_type_from_typekey(doc_key: str) -> str:
        """Determine cache item type from document key typeKey.
        
        Classifies items based on their typeKey prefixes:
        - DEFAULT_REQ_TYPEKEYS ("REQ", "PRQ") → "requirement"
        - DEFAULT_DESIGN_TYPEKEYSS ("DES", "TDS") → "design_doc"
        - DEFAULT_MODULE_TYPEKEY ("MOD") → "module"
        - Otherwise → "other"
        
        Args:
            doc_key: Document key string (e.g., "REQ-123", "TDS-456", "MOD-789")
            
        Returns:
            Type string: "requirement", "design_doc", "module", or "other"
        """
        if any(typekey in doc_key for typekey in DEFAULT_REQ_TYPEKEYS):
            return "requirement"
        elif any(typekey in doc_key for typekey in DEFAULT_DESIGN_TYPEKEYS):
            return "design_doc"
        elif DEFAULT_MODULE_TYPEKEY in doc_key:
            return "module"
        elif DEFAULT_TESTCASE_TYPEKEY in doc_key:
            return "test_case"
        else:
            return "other"

    @staticmethod
    def _filter_items_by_itemtype(
        items_dict: Dict[int, Dict[str, Any]],
        allowed_item_types: Tuple[int, ...],
        expected_type: str = "requirement",
    ) -> Dict[int, Dict[str, Any]]:
        """Keep only items whose Jama ``itemType`` is in the allowlist.

        Used to drop non-graphable items (e.g. modules/folders that a test case
        traces up to) before assembly, so each reviewer graphs only its intended
        item type. Items missing an ``itemType`` fall back to the doc-key typekey
        classifier (matched against ``expected_type``) so nothing is silently
        dropped when the API omits the field.

        Args:
            items_dict: Items keyed by API ID.
            allowed_item_types: Accepted Jama itemType integer IDs.
            expected_type: Type name (:meth:`_get_item_type_from_typekey` output)
                used for the doc-key fallback when ``itemType`` is absent.

        Returns:
            A new dict containing only the retained items.
        """
        kept: Dict[int, Dict[str, Any]] = {}
        for item_id, item in items_dict.items():
            item_type = item.get("itemType")
            if item_type in allowed_item_types:
                kept[item_id] = item
            elif item_type is None:
                # itemType absent: fall back to document-key classification.
                if PyJamaTraceMatrix._get_item_type_from_typekey(get_doc_key(item)) == expected_type:
                    kept[item_id] = item
        return kept

    def _baseline_cache_folder(self, baseline_id: str) -> str:
        """Folder for a baseline's cache artifacts."""
        return self._cache.resolve_folder(CACHE_BASELINES_SUBDIR, baseline_id)

    def _identifiers_cache_folder(self) -> str:
        """Folder for identifier-keyed cache artifacts."""
        return self._cache.resolve_folder(CACHE_IDENTIFIERS_SUBDIR)

    def _load_baseline_response(
        self, baseline_id: str, method_prefix: str
    ) -> Optional[List[Dict[str, Any]]]:
        """Load the newest cached response list for a baseline, or None on miss."""
        folder = self._baseline_cache_folder(baseline_id)
        prefix = f"{method_prefix}_{RESPONSE_FILE_FRAGMENT}_"
        path = self._cache.newest_file(folder, prefix)
        if not path:
            return None
        self._logger.info("Cache HIT (%s) for baseline %s: %s", method_prefix, baseline_id, path)
        return self._cache.read_jsonl(path)

    def _write_baseline_cache(
        self,
        baseline_id: str,
        method_prefix: str,
        payload: List[Dict[str, Any]],
        ids_rows: List[Dict[str, Any]],
    ) -> None:
        """Write paired response + ids jsonl files (sharing one timestamp)."""
        folder = self._baseline_cache_folder(baseline_id)
        ts = self._cache.timestamp()
        self._cache.write_jsonl(folder, f"{method_prefix}_{RESPONSE_FILE_FRAGMENT}_", ts, payload)
        self._cache.write_jsonl(folder, f"{method_prefix}_{IDS_FILE_FRAGMENT}_", ts, ids_rows)

    @staticmethod
    def _test_suite_ids_rows(payload: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Derive ids rows from a test-suite payload (requirements + their test cases).
        
        Classifies requirements by their typeKey rather than hardcoding as "requirement".
        Test cases remain classified as "test_case" (based on itemType, not typeKey).
        """
        rows = []
        seen = set()
        for entry in payload:
            req_data = entry.get(REQUIREMENT_KEY, {})
            rid = req_data.get(REQUIREMENT_ID_KEY)
            if rid and (rid, "req") not in seen:  # Use "req" as dedup key
                seen.add((rid, "req"))
                # Determine type based on requirement ID (which is the document key)
                item_type = PyJamaTraceMatrix._get_item_type_from_typekey(rid)
                rows.append({"id": rid, "type": item_type})
            for tc in entry.get(TEST_CASES_KEY, []):
                tid = tc.get(TEST_ID_KEY)
                if tid and (tid, "test") not in seen:  # Use "test" as dedup key
                    seen.add((tid, "test"))
                    rows.append({"id": tid, "type": IDS_TYPE_TEST_CASE})
        return rows

    @staticmethod
    def _test_case_ids_rows(payload: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Derive ids rows from a test-case payload (test cases + their requirements).
        
        Classifies requirements by their typeKey rather than hardcoding as "requirement".
        Test cases remain classified as "test_case" (based on itemType, not typeKey).
        """
        rows = []
        seen = set()
        for entry in payload:
            tid = entry.get("test_case", {}).get(TEST_ID_KEY)
            if tid and (tid, "test") not in seen:  # Use "test" as dedup key
                seen.add((tid, "test"))
                rows.append({"id": tid, "type": IDS_TYPE_TEST_CASE})
            for req in entry.get(REQUIREMENTS_KEY, []):
                rid = req.get(REQUIREMENT_ID_KEY)
                if rid and (rid, "req") not in seen:  # Use "req" as dedup key
                    seen.add((rid, "req"))
                    # Determine type based on requirement ID (which is the document key)
                    item_type = PyJamaTraceMatrix._get_item_type_from_typekey(rid)
                    rows.append({"id": rid, "type": item_type})
        return rows

    def _load_identifier_responses(
        self, method_prefix: str, identifiers: List[str]
    ) -> Optional[List[Dict[str, Any]]]:
        """All-or-nothing per-identifier load.

        Returns the concatenated entries in input order, or None if ANY requested
        identifier has no cached file (a present-but-empty file is a valid [] hit).
        """
        folder = self._identifiers_cache_folder()
        per_ident: Dict[str, List[Dict[str, Any]]] = {}
        for ident in identifiers:
            prefix = f"{method_prefix}_{RESPONSE_FILE_FRAGMENT}_{ident}_"
            path = self._cache.newest_file(folder, prefix)
            if not path:
                return None
            per_ident[ident] = self._cache.read_jsonl(path)
        self._logger.info("Cache HIT (%s) for %d identifiers", method_prefix, len(identifiers))
        result: List[Dict[str, Any]] = []
        for ident in identifiers:
            result.extend(per_ident[ident])
        return result

    def _write_identifier_responses(
        self,
        method_prefix: str,
        identifiers: List[str],
        ordered_api_ids: List[int],
        result: List[Dict[str, Any]],
        api_id_to_identifier: Dict[int, str],
    ) -> None:
        """Write one response file per input identifier (empty file if no entry).

        Correlates each result entry back to its INPUT identifier positionally:
        the assembler emits entries in ``software_reqs_dict`` insertion order, so
        ``ordered_api_ids`` (its keys) zips 1:1 with ``result``.
        """
        if len(ordered_api_ids) != len(result):
            self._logger.error(
                "Cache skip (%s): result length %d != ordered ids length %d",
                method_prefix, len(result), len(ordered_api_ids)
            )
            return

        folder = self._identifiers_cache_folder()
        ts = self._cache.timestamp()
        written = set()
        for api_id, entry in zip(ordered_api_ids, result):
            ident = api_id_to_identifier.get(api_id)
            if ident is None:
                continue
            self._cache.write_jsonl(
                folder, f"{method_prefix}_{RESPONSE_FILE_FRAGMENT}_{ident}_", ts, [entry]
            )
            written.add(ident)
        # Empty file for any requested identifier that produced no entry
        for ident in identifiers:
            if ident not in written:
                self._cache.write_jsonl(
                    folder, f"{method_prefix}_{RESPONSE_FILE_FRAGMENT}_{ident}_", ts, []
                )

    @staticmethod
    def _rtm_identifiers_hash(identifiers: List[str]) -> str:
        """Short stable digest of the (order-independent) identifier set."""
        import hashlib
        joined = ",".join(sorted(identifiers))
        return hashlib.sha1(joined.encode("utf-8")).hexdigest()[:12]

    def _load_rtm_response(self, identifiers: List[str]) -> Optional[Dict[str, Any]]:
        """Load the newest aggregated RTM dict for an identifier set, or None."""
        folder = self._identifiers_cache_folder()
        prefix = f"{RTM_CACHE_PREFIX}_{RESPONSE_FILE_FRAGMENT}_{self._rtm_identifiers_hash(identifiers)}_"
        path = self._cache.newest_file(folder, prefix)
        if not path:
            return None
        rows = self._cache.read_jsonl(path)
        if not rows:
            return None
        self._logger.info("Cache HIT (rtm): %s", path)
        return rows[0]

    def _write_rtm_response(self, identifiers: List[str], result: Dict[str, Any]) -> None:
        """
        Write the aggregated RTM dict as a single-line jsonl file.
        """
        folder = self._identifiers_cache_folder()
        ts = self._cache.timestamp()
        prefix = f"{RTM_CACHE_PREFIX}_{RESPONSE_FILE_FRAGMENT}_{self._rtm_identifiers_hash(identifiers)}_"
        self._cache.write_jsonl(folder, prefix, ts, [result])

    @timing(PYJAMA_LOGGERNAME)
    def _assemble_requirement_structure(
        self,
        requirement_ids: Set[int],
        review_test_keys: Set[str],
        api_id_key: str,
        design_typekey: Union[str, List[str]],
        testcase_typekey: Union[str, List[str]],
    ) -> List[Dict[str, Any]]:
        """Fetch requirement items + their downstream items and assemble the payload.

        Shared by :meth:`get_test_suite_reviewer_structure` (requirements discovered
        via test-case relationships) and :meth:`get_requirement_reviewer_structure`
        (requirements read straight from the baseline) so both emit an identical
        per-requirement structure ``{requirement, test_cases, design_docs}``.

        Args:
            requirement_ids: Requirement API ids to fetch and assemble.
            review_test_keys: Test-case doc keys present in the baseline (used to set
                ``in_review_baseline`` flags on downstream test cases).
            api_id_key: Key for item ID in API responses.
            design_typekey / testcase_typekey: Typekeys forwarded to the assembler.

        Returns:
            The assembled per-requirement payload list.
        """
        requirements_dict: Dict[int, Dict[str, Any]] = {}
        downstream_results_dict: Dict[int, List[Dict[str, Any]]] = {}
        failed_req_ids: List[int] = []

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

            for future in concurrent.futures.as_completed(req_item_futures):
                req_id = req_item_futures[future]
                try:
                    requirements_dict[req_id] = future.result()
                except Exception as e:
                    self._logger.error("Failed to fetch requirement item ID %d: %s", req_id, str(e))
                    failed_req_ids.append(req_id)

            for future in concurrent.futures.as_completed(downstream_futures):
                req_id, downstream_items = future.result()
                downstream_results_dict[req_id] = downstream_items

        self._logger.info("Retrieved %d requirement items and downstream data", len(requirements_dict))

        # Drop non-requirement primaries (e.g. modules/folders a test case traces up
        # to) so only genuine requirements are assembled. Both the response payload
        # and the ids projection derive from this dict, keeping the paired cache
        # files aligned. Harmless (no-op) when items were already collected by
        # requirement itemType, and preserves the doc-key fallback path.
        before_filter = len(requirements_dict)
        requirements_dict = self._filter_items_by_itemtype(
            requirements_dict, REQUIREMENT_PRIMARY_ITEM_TYPE_IDS,
            expected_type="requirement",
        )
        self._logger.info(
            "Filtered requirement primaries by itemType: kept %d/%d "
            "(dropped %d non-requirement items, e.g. modules)",
            len(requirements_dict), before_filter, before_filter - len(requirements_dict),
        )

        return self._test_suite_assembler.assemble(
            requirements_dict=requirements_dict,
            downstream_results=downstream_results_dict,
            review_test_keys=review_test_keys,
            testcase_typekey=testcase_typekey,
            design_typekey=design_typekey,
        )

    @timing(PYJAMA_LOGGERNAME)
    def get_test_suite_reviewer_structure(
        self,
        baseline_id: str,
        api_id_key: Optional[str] = None,
        design_typekey: Optional[Union[str, List[str]]] = None,
        testcase_typekey: Optional[Union[str, List[str]]] = None,
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
            >>> api = PyJamaTraceMatrix(client, data_path="./data")
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

        # Test mode: serve strictly from cache, never touch the Jama API.
        if self._test_mode:
            cached = self._load_baseline_response(baseline_id, TEST_SUITE_CACHE_PREFIX)
            return self._require_cached(
                cached, f"baseline '{baseline_id}' (test_suite)",
                self._baseline_cache_folder(baseline_id),
            )

        # Tier-3 cache check
        cache_key = f"baselines:{baseline_id}:test_suite"
        if not self._cache.should_recompute(cache_key):
            cached = self._load_baseline_response(baseline_id, TEST_SUITE_CACHE_PREFIX)
            if cached is not None:
                return cached

        # Set defaults
        api_id_key = api_id_key or ID_KEY
        design_typekey = normalize_typekeys(design_typekey, DEFAULT_DESIGN_TYPEKEYS)
        testcase_typekey = normalize_typekeys(testcase_typekey, [DEFAULT_TESTCASE_TYPEKEY])

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
                if self._cache.writes_enabled():
                    self._write_baseline_cache(baseline_id, TEST_SUITE_CACHE_PREFIX, [], [])
                    self._cache.mark_refreshed(cache_key)
                return []

            # Step 4: Fetch upstream relationships in parallel (lighter than full items)
            self._logger.info(
                "Step 4: Fetching upstream relationships for %d test cases",
                len(review_test_ids)
            )
            
            relationship_results = []
            failed_test_ids = []
            with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                relationship_futures = {
                    executor.submit(self._get_upstream_relationships, test_id): test_id
                    for test_id in review_test_ids
                }
                
                for future in concurrent.futures.as_completed(relationship_futures):
                    test_id = relationship_futures[future]
                    try:
                        test_id, relationships = future.result()
                        relationship_results.append((test_id, relationships))
                    except Exception as e:
                        self._logger.error(
                            "Failed to fetch upstream relationships for test case ID %d: %s",
                            test_id,
                            str(e)
                        )
                        failed_test_ids.append(test_id)
            
            if failed_test_ids:
                self._logger.warning(
                    "Failed to fetch relationships for %d/%d test cases: %s",
                    len(failed_test_ids),
                    len(review_test_ids),
                    failed_test_ids[:10]  # Show first 10
                )
            
            self._logger.info(
                "Completed upstream relationship fetch for %d/%d test cases (%d succeeded, %d failed)",
                len(relationship_results),
                len(review_test_ids),
                len(relationship_results),
                len(failed_test_ids)
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
                if self._cache.writes_enabled():
                    self._write_baseline_cache(baseline_id, TEST_SUITE_CACHE_PREFIX, [], [])
                    self._cache.mark_refreshed(cache_key)
                return []

            # Steps 6-7: Fetch requirement items + downstream and assemble (shared
            # with the requirement-baseline workflow so both emit identical structure).
            self._logger.info(
                "Steps 6-7: Fetching %d requirement items + downstream and assembling",
                len(requirement_ids)
            )
            final_payload = self._assemble_requirement_structure(
                requirement_ids, review_test_keys, api_id_key, design_typekey, testcase_typekey
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

            # Tier-3 cache write
            if self._cache.writes_enabled():
                self._write_baseline_cache(
                    baseline_id, TEST_SUITE_CACHE_PREFIX,
                    final_payload, self._test_suite_ids_rows(final_payload)
                )
                self._cache.mark_refreshed(cache_key)

            return final_payload

        except Exception as e:
            self._logger.error(
                "Fatal error in get_test_suite_reviewer_structure: %s",
                str(e),
                exc_info=True
            )
            raise

    @timing(PYJAMA_LOGGERNAME)
    def get_requirement_reviewer_structure(
        self,
        baseline_id: str,
        api_id_key: Optional[str] = None,
        design_typekey: Optional[Union[str, List[str]]] = None,
        testcase_typekey: Optional[Union[str, List[str]]] = None,
    ) -> List[Dict[str, Any]]:
        """Extract reviewer structure from a **requirement-review** baseline.

        Alternative to :meth:`get_test_suite_reviewer_structure` for the case where
        the baseline's items are requirement ids **directly** (rather than test cases
        traced upstream to requirements). Workflow:

        1. Parse baseline ID from 'BASE-12345' format.
        2. Fetch baseline versioned items.
        3. Collect requirement ids directly from the baseline (itemType filtering).
        4. Fetch each requirement's items + downstream test cases/design docs and
           assemble — shared with the test-suite workflow via
           :meth:`_assemble_requirement_structure`.

        Produces the **identical** per-requirement response structure
        ``{requirement, test_cases, design_docs}`` so every downstream consumer works
        unchanged. Cached under its own prefix (:data:`REQUIREMENT_REVIEW_CACHE_PREFIX`).

        Args:
            baseline_id: Baseline identifier (e.g. 'BASE-84398').
            api_id_key: Key for item ID in API responses (default: 'id').
            design_typekey: Type key(s) for design documents (default: 'DES').
            testcase_typekey: Type key(s) for test cases (default: 'TEST').

        Returns:
            List of requirement dictionaries (see :meth:`get_test_suite_reviewer_structure`).

        Raises:
            ValueError: If baseline_id format is invalid.
        """
        self._logger.info("=" * 60)
        self._logger.info("Starting requirement reviewer structure extraction")
        self._logger.info("Baseline ID: %s", baseline_id)
        self._logger.info("=" * 60)

        # Test mode: serve strictly from cache, never touch the Jama API.
        if self._test_mode:
            cached = self._load_baseline_response(baseline_id, REQUIREMENT_REVIEW_CACHE_PREFIX)
            return self._require_cached(
                cached, f"baseline '{baseline_id}' (requirement_review)",
                self._baseline_cache_folder(baseline_id),
            )

        # Tier-3 cache check
        cache_key = f"baselines:{baseline_id}:requirement_review"
        if not self._cache.should_recompute(cache_key):
            cached = self._load_baseline_response(baseline_id, REQUIREMENT_REVIEW_CACHE_PREFIX)
            if cached is not None:
                return cached

        # Set defaults
        api_id_key = api_id_key or ID_KEY
        design_typekey = normalize_typekeys(design_typekey, DEFAULT_DESIGN_TYPEKEYS)
        testcase_typekey = normalize_typekeys(testcase_typekey, [DEFAULT_TESTCASE_TYPEKEY])

        try:
            # Step 1: Parse baseline ID from 'BASE-12345' format
            self._logger.info("Step 1: Parsing baseline ID")
            baseline_id_int = self._parse_baseline_id(baseline_id)

            # Step 2: Fetch baseline versioned items
            self._logger.info("Step 2: Fetching baseline versioned items for baseline_id: %d", baseline_id_int)
            raw_review_items = self.client.get_baselines_versioneditems(baseline_id_int)
            self._logger.info("Retrieved %d baseline versioned items", len(raw_review_items))

            # Step 3: Collect requirement ids directly from the baseline (itemType filtering)
            self._logger.info("Step 3: Collecting requirements from baseline (itemType filtering)")
            _, requirement_ids = self._collect_requirements_from_baseline(
                raw_review_items, api_id_key=api_id_key
            )

            if not requirement_ids:
                self._logger.warning("No requirements found in baseline. Returning empty result.")
                if self._cache.writes_enabled():
                    self._write_baseline_cache(baseline_id, REQUIREMENT_REVIEW_CACHE_PREFIX, [], [])
                    self._cache.mark_refreshed(cache_key)
                return []

            # Any test cases that ARE in this baseline set in_review_baseline flags on
            # downstream test cases (usually empty for a pure requirement baseline;
            # populated for a mixed requirement+test baseline).
            review_test_keys, _ = self._collect_test_cases_from_baseline(
                raw_review_items, api_id_key=api_id_key
            )

            # Step 4: Fetch requirement items + downstream and assemble (shared helper)
            self._logger.info(
                "Step 4: Fetching %d requirement items + downstream and assembling",
                len(requirement_ids)
            )
            final_payload = self._assemble_requirement_structure(
                set(requirement_ids), review_test_keys, api_id_key, design_typekey, testcase_typekey
            )

            self._logger.info("=" * 60)
            self._logger.info("Completed requirement reviewer structure extraction")
            self._logger.info("Total requirements processed: %d", len(final_payload))

            total_tests = sum(len(req[TEST_CASES_KEY]) for req in final_payload)
            total_design_docs = sum(len(req[DESIGN_DOCS_KEY]) for req in final_payload)
            self._logger.info("Summary:")
            self._logger.info("  Requirements: %d", len(final_payload))
            self._logger.info("  Test cases (total): %d", total_tests)
            self._logger.info("  Design documents: %d", total_design_docs)
            self._logger.info("=" * 60)

            # Tier-3 cache write
            if self._cache.writes_enabled():
                self._write_baseline_cache(
                    baseline_id, REQUIREMENT_REVIEW_CACHE_PREFIX,
                    final_payload, self._test_suite_ids_rows(final_payload)
                )
                self._cache.mark_refreshed(cache_key)

            return final_payload

        except Exception as e:
            self._logger.error(
                "Fatal error in get_requirement_reviewer_structure: %s",
                str(e),
                exc_info=True
            )
            raise

    @timing(PYJAMA_LOGGERNAME)
    def get_rtm_from_gids(
        self,
        identifiers: List[str],
        project_name: str,
        api_id_key: Optional[str] = None,
        design_typekey: Optional[Union[str, List[str]]] = None,
        testcase_typekey: Optional[Union[str, List[str]]] = None,
        user_need_typekey: Optional[Union[str, List[str]]] = None,
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
            user_need_typekey: Type key(s) for user needs — a single typekey or a list of typekeys
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

        # Test mode: serve strictly from cache, never touch the Jama API.
        if self._test_mode:
            cached = self._load_rtm_response(identifiers)
            return self._require_cached(
                cached, f"rtm identifiers {identifiers}",
                self._identifiers_cache_folder(),
            )

        # Tier-3 cache check
        cache_key = f"identifiers:rtm:{tuple(sorted(identifiers))}"
        if not self._cache.should_recompute(cache_key):
            cached = self._load_rtm_response(identifiers)
            if cached is not None:
                return cached

        # Set defaults
        api_id_key = api_id_key or ID_KEY
        design_typekey = normalize_typekeys(design_typekey, DEFAULT_DESIGN_TYPEKEYS)
        testcase_typekey = normalize_typekeys(testcase_typekey, [DEFAULT_TESTCASE_TYPEKEY])
        user_need_typekey = normalize_typekeys(user_need_typekey, [DEFAULT_USER_NEED_TYPEKEY])
        
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
                user_need_typekey,
                api_id_key=api_id_key
            )
            
            # Step 7: Collect test cases and design docs from downstream results
            self._logger.info("Step 7: Collecting test cases and design docs from downstream results")
            
            test_cases_dict = self._collect_unique_items_by_typekeys(
                all_downstream_items,
                testcase_typekey,
                api_id_key=api_id_key
            )

            design_docs_dict = self._collect_unique_items_by_typekeys(
                all_downstream_items,
                design_typekey,
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

            # Tier-3 cache write
            if self._cache.writes_enabled():
                self._write_rtm_response(identifiers, result)
                self._cache.mark_refreshed(cache_key)

            return result

        except Exception as e:
            self._logger.error("Fatal error in get_rtm_from_gids: %s",
                             str(e), exc_info=True)
            raise

    @timing(PYJAMA_LOGGERNAME)
    def get_test_case_reviewer_structure(
        self,
        baseline_id: str,
        api_id_key: Optional[str] = None,
        design_typekey: Optional[Union[str, List[str]]] = None,
        requirement_typekeys: Optional[Union[str, List[str]]] = None,
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
            design_typekey: Type key(s) for design documents — a single typekey or a
                list of typekeys (default: 'DES')
            requirement_typekeys: Type key(s) for requirements — a single typekey or a
                list of typekeys (default: ['REQ', 'PRQ'])
            
        Returns:
            List of test case dictionaries, each containing:
            - test_case: {test_id, description, setup, steps, expectedResults}
            - requirements: [{req_id, text}, ...]
            - design_docs: [{doc_id, name, description}, ...]
            
        Raises:
            ValueError: If baseline_id format is invalid or no test cases found
            
        Example:
            >>> api = PyJamaTraceMatrix(client, data_path="./data")
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

        # Test mode: serve strictly from cache, never touch the Jama API.
        if self._test_mode:
            cached = self._load_baseline_response(baseline_id, TEST_CASE_CACHE_PREFIX)
            return self._require_cached(
                cached, f"baseline '{baseline_id}' (test_case)",
                self._baseline_cache_folder(baseline_id),
            )

        # Tier-3 cache check
        cache_key = f"baselines:{baseline_id}:test_case"
        if not self._cache.should_recompute(cache_key):
            cached = self._load_baseline_response(baseline_id, TEST_CASE_CACHE_PREFIX)
            if cached is not None:
                return cached

        # Set defaults
        api_id_key = api_id_key or ID_KEY
        design_typekey = normalize_typekeys(design_typekey, DEFAULT_DESIGN_TYPEKEYS)
        requirement_typekeys = normalize_typekeys(requirement_typekeys, DEFAULT_REQ_TYPEKEYS)
        
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
                if self._cache.writes_enabled():
                    self._write_baseline_cache(baseline_id, TEST_CASE_CACHE_PREFIX, [], [])
                    self._cache.mark_refreshed(cache_key)
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
            failed_test_ids = []
            
            with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                upstream_futures = {
                    executor.submit(self._get_upstream_with_id, test_id): test_id
                    for test_id in test_case_ids
                }
                
                for future in concurrent.futures.as_completed(upstream_futures):
                    test_id = upstream_futures[future]
                    try:
                        test_id, upstream_items = future.result()
                        upstream_results_dict[test_id] = upstream_items
                        self._logger.debug(
                            "Collected %d upstream items for test case ID: %d",
                            len(upstream_items),
                            test_id
                        )
                    except Exception as e:
                        self._logger.error(
                            "Failed to fetch upstream items for test case ID %d: %s",
                            test_id,
                            str(e)
                        )
                        failed_test_ids.append(test_id)
            
            if failed_test_ids:
                self._logger.warning(
                    "Failed to fetch upstream for %d/%d test cases: %s",
                    len(failed_test_ids),
                    len(test_case_ids),
                    failed_test_ids[:10]  # Show first 10
                )
            
            total_upstream = sum(len(items) for items in upstream_results_dict.values())
            self._logger.info(
                "Completed upstream fetch for %d/%d test cases (%d total upstream items, %d failed)",
                len(upstream_results_dict),
                len(test_case_ids),
                total_upstream,
                len(failed_test_ids)
            )
            
            # Ensure only genuine test cases are graphed. test_cases_dict is
            # already itemType-filtered above; this makes the invariant explicit
            # and central, guarding against future changes to the collection step.
            test_cases_dict = self._filter_items_by_itemtype(
                test_cases_dict, (TEST_CASE_ITEM_TYPE_ID,), expected_type="test_case",
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

            # Tier-3 cache write
            if self._cache.writes_enabled():
                self._write_baseline_cache(
                    baseline_id, TEST_CASE_CACHE_PREFIX,
                    final_payload, self._test_case_ids_rows(final_payload)
                )
                self._cache.mark_refreshed(cache_key)

            return final_payload

        except Exception as e:
            self._logger.error(
                "Fatal error in get_test_case_reviewer_structure: %s",
                str(e),
                exc_info=True
            )
            raise

    @timing(PYJAMA_LOGGERNAME)
    def get_hierarchical_trace_from_gids(
        self,
        identifiers: List[str],
        project_name: str,
        api_id_key: Optional[str] = None,
        user_need_typekey: Optional[Union[str, List[str]]] = None,
        prq_type_field: str = "PRQ_type$63",
    ) -> List[Dict[str, Any]]:
        """
        Build hierarchical traceability structure for each software requirement.
        
        For each input GID (software requirement), creates a hierarchical structure showing:
        - The requirement itself
        - Its upstream system requirements
        - Each system requirement's upstream user needs
        
        This maintains the full traceability chain and shows which specific system
        requirements and user needs are connected to each software requirement.
        
        Unlike get_rtm_from_gids which returns flat bidirectional traceability,
        this method preserves the hierarchical relationships:
        Software Requirement → System Requirements → User Needs
        
        Optimized workflow:
        1. Resolve project name to ID (using cache)
        2. Fetch abstract items filtered by REQUIREMENT_ITEM_TYPE_ID (single API call)
        3. Map input identifiers to API IDs
        4. Classify ALL abstract items into software reqs, system reqs, and user needs
           (using PRQ_type field for efficient in-memory filtering)
        5. Batch fetch upstream items for ALL software requirements concurrently
        6. Identify connected system requirements from upstream results
        7. Batch fetch upstream items for ALL connected system requirements concurrently
        8. Assemble hierarchical structure with preserved relationships
        
        Performance optimizations:
        - Single abstract items fetch (reused for all classifications)
        - Concurrent batch fetching of upstream items (minimizes API calls)
        - In-memory filtering using PRQ_type field (no additional API calls)
        - Efficient set operations for identifying connected items
        
        Args:
            identifiers: List of JAMA identifier strings (GIDs or document keys)
                        Examples: ["GID-2788627", "PRQ-123", "REQ-456"]
            project_name: Name of the Jama project (required)
            api_id_key: Key for item ID in API responses (default: 'id')
            user_need_typekey: Type key(s) for user needs — a single typekey or a
                list of typekeys (default: 'UND')
            prq_type_field: Field name for requirement type pick list (default: "PRQ_type$63")
            
        Returns:
            JSONL-compatible list where each element represents one software requirement
            with nested system requirements (which contain nested user needs):
            [
                {
                    "requirement": {
                        "req_id": "GID-2634456",
                        "text": "The reporting solution shall..."
                    },
                    "system_requirements": [
                        {
                            "req_id": "P1545-PRQ-216",
                            "text": "The system shall be able to generate...",
                            "user_needs": [
                                {
                                    "req_id": "P1545-UND-7",
                                    "text": "As a Hospital Administrator..."
                                },
                                ...
                            ]
                        },
                        ...
                    ]
                },
                ...
            ]
            
        Raises:
            ValueError: If no identifiers resolve or invalid project name
            
        Example:
            >>> api = PyJamaTraceMatrix(client, data_path="./data")
            >>> result = api.get_hierarchical_trace_from_gids(
            ...     identifiers=["GID-2634456", "GID-2634457"],
            ...     project_name="Patient Safety Platform"
            ... )
            >>> print(f"Found {len(result)} software requirements")
            Found 2 software requirements
            >>> 
            >>> # Write to JSONL file
            >>> import json
            >>> with open("hierarchical_trace.jsonl", "w") as f:
            ...     for req in result:
            ...         f.write(json.dumps(req) + "\n")
        """
        self._logger.info("=" * 60)
        self._logger.info("Starting hierarchical trace extraction from identifiers")
        self._logger.info("Input identifiers: %d", len(identifiers))
        self._logger.info("Project: %s", project_name)
        self._logger.info("=" * 60)

        # Test mode: serve strictly from cache, never touch the Jama API.
        if self._test_mode:
            cached = self._load_identifier_responses(HIERARCHICAL_CACHE_PREFIX, identifiers)
            return self._require_cached(
                cached, f"hierarchical identifiers {identifiers}",
                self._identifiers_cache_folder(),
            )

        # Tier-3 cache check
        cache_key = f"identifiers:hierarchical:{tuple(sorted(identifiers))}"
        if not self._cache.should_recompute(cache_key):
            cached = self._load_identifier_responses(HIERARCHICAL_CACHE_PREFIX, identifiers)
            if cached is not None:
                return cached

        # Set defaults
        api_id_key = api_id_key or ID_KEY
        user_need_typekey = normalize_typekeys(user_need_typekey, [DEFAULT_USER_NEED_TYPEKEY])
        
        try:
            # Step 1: Resolve project name to ID using cache
            self._logger.info("Step 1: Resolving project name to ID")
            project_id = self._project_cache.resolve_project_id(project_name, api_id_key=api_id_key)
            
            # Step 2: Fetch abstract items filtered by REQUIREMENT_ITEM_TYPE_ID and USER_NEED_ITEM_TYPE_ID
            # This single API call gets ALL requirements (software, system) and user needs
            self._logger.info("Step 2: Fetching abstract items for project_id: %d", project_id)
            abstract_items = self.client.get_abstract_items(
                project=project_id,
                item_type=[REQUIREMENT_ITEM_TYPE_ID, USER_NEED_ITEM_TYPE_ID]
            )
            self._logger.info("Retrieved %d abstract items", len(abstract_items))
            
            # Step 3: Map identifiers to API IDs
            self._logger.info("Step 3: Mapping identifiers to API IDs")
            id_to_api_id, unresolved_ids = map_identifiers_to_api_ids(
                items=abstract_items,
                identifiers=identifiers,
                api_id_key=api_id_key,
                raise_on_empty=True,
                logger=self._logger,
            )
            
            software_req_ids = list(id_to_api_id.values())
            self._logger.info(
                "Successfully resolved %d/%d identifiers to API IDs",
                len(software_req_ids),
                len(identifiers)
            )

            # Reverse map (api_id -> original input identifier) for per-identifier caching
            api_id_to_identifier = {v: k for k, v in id_to_api_id.items()}

            # Step 4: Classify ALL abstract items into software reqs, system reqs, and user needs
            # This is done in-memory using the PRQ_type field - no additional API calls
            self._logger.info(
                "Step 4: Classifying abstract items (software reqs, system reqs, user needs)"
            )
            
            software_reqs_dict = {}
            system_reqs_dict = {}
            user_needs_dict = {}
            
            for item in abstract_items:
                item_id = item[api_id_key]
                doc_key = get_doc_key(item)
                fields = item.get(FIELDS_KEY, {})
                prq_type_id = fields.get(prq_type_field)
                
                # Classify by type
                if any(tk in doc_key for tk in user_need_typekey):
                    user_needs_dict[item_id] = item
                    self._logger.debug("Classified %s as user need", doc_key)
                elif prq_type_id == SYSTEM_REQUIREMENT_TYPE_ID:
                    system_reqs_dict[item_id] = item
                    self._logger.debug("Classified %s as system requirement", doc_key)
                elif item_id in software_req_ids:
                    # Only include input software requirements
                    software_reqs_dict[item_id] = item
                    self._logger.debug("Classified %s as input software requirement", doc_key)
            
            self._logger.info(
                "Classification complete: %d software reqs, %d system reqs, %d user needs",
                len(software_reqs_dict),
                len(system_reqs_dict),
                len(user_needs_dict)
            )
            
            if not software_reqs_dict:
                self._logger.warning(
                    "No software requirements found in input identifiers. Returning empty result."
                )
                if self._cache.writes_enabled():
                    self._write_identifier_responses(
                        HIERARCHICAL_CACHE_PREFIX, identifiers, [], [], api_id_to_identifier
                    )
                    self._cache.mark_refreshed(cache_key)
                return []

            # Step 5: Batch fetch upstream items for ALL software requirements concurrently
            # This minimizes API calls and latency
            self._logger.info(
                "Step 5: Batch fetching upstream items for %d software requirements",
                len(software_reqs_dict)
            )
            
            software_upstream_results = {}
            
            with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                upstream_futures = {
                    executor.submit(self._get_upstream_with_id, req_id): req_id
                    for req_id in software_reqs_dict.keys()
                }
                
                for future in concurrent.futures.as_completed(upstream_futures):
                    req_id, upstream_items = future.result()
                    software_upstream_results[req_id] = upstream_items
                    self._logger.debug(
                        "Collected %d upstream items for software req ID: %d",
                        len(upstream_items),
                        req_id
                    )
            
            total_software_upstream = sum(len(items) for items in software_upstream_results.values())
            self._logger.info(
                "Completed upstream fetch for software requirements (%d total upstream items)",
                total_software_upstream
            )
            
            # Step 6: Identify connected system requirements from upstream results
            # Use set operations for efficient filtering
            self._logger.info("Step 6: Identifying connected system requirements")
            
            connected_system_req_ids = set()
            for upstream_items in software_upstream_results.values():
                for item in upstream_items:
                    item_id = item[api_id_key]
                    if item_id in system_reqs_dict:
                        connected_system_req_ids.add(item_id)
                        self._logger.debug(
                            "Found connected system requirement: %s (ID: %d)",
                            get_doc_key(item),
                            item_id
                        )
            
            self._logger.info(
                "Identified %d connected system requirements",
                len(connected_system_req_ids)
            )
            
            # Step 7: Batch fetch upstream items for ALL connected system requirements concurrently
            # This is the second and final batch of upstream fetches
            self._logger.info(
                "Step 7: Batch fetching upstream items for %d connected system requirements",
                len(connected_system_req_ids)
            )
            
            system_upstream_results = {}
            
            if connected_system_req_ids:
                with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                    upstream_futures = {
                        executor.submit(self._get_upstream_with_id, req_id): req_id
                        for req_id in connected_system_req_ids
                    }
                    
                    for future in concurrent.futures.as_completed(upstream_futures):
                        req_id, upstream_items = future.result()
                        system_upstream_results[req_id] = upstream_items
                        self._logger.debug(
                            "Collected %d upstream items for system req ID: %d",
                            len(upstream_items),
                            req_id
                        )
                
                total_system_upstream = sum(len(items) for items in system_upstream_results.values())
                self._logger.info(
                    "Completed upstream fetch for system requirements (%d total upstream items)",
                    total_system_upstream
                )
            else:
                self._logger.warning("No connected system requirements found")
            
            # Step 8: Assemble hierarchical structure using assembler
            self._logger.info("Step 8: Assembling hierarchical trace structure")
            
            result = self._hierarchical_trace_assembler.assemble(
                software_reqs_dict=software_reqs_dict,
                software_upstream_results=software_upstream_results,
                system_reqs_dict=system_reqs_dict,
                system_upstream_results=system_upstream_results,
                user_needs_dict=user_needs_dict,
                api_id_key=api_id_key,
            )
            
            self._logger.info("=" * 60)
            self._logger.info("Completed hierarchical trace extraction")
            self._logger.info("Total software requirements: %d", len(result))
            
            # Summary statistics
            total_system_reqs = sum(
                len(req[SYSTEM_REQUIREMENTS_KEY]) for req in result
            )
            total_user_needs = sum(
                sum(len(sys_req[USER_NEEDS_KEY]) for sys_req in req[SYSTEM_REQUIREMENTS_KEY])
                for req in result
            )
            
            # Calculate requirements with/without traces
            reqs_with_system = sum(
                1 for req in result if len(req[SYSTEM_REQUIREMENTS_KEY]) > 0
            )
            reqs_without_system = len(result) - reqs_with_system
            
            self._logger.info("Summary:")
            self._logger.info("  Software requirements: %d", len(result))
            self._logger.info("  Software reqs with system reqs: %d", reqs_with_system)
            self._logger.info("  Software reqs without system reqs: %d", reqs_without_system)
            self._logger.info("  Total system requirements: %d", total_system_reqs)
            self._logger.info("  Total user needs: %d", total_user_needs)
            self._logger.info("=" * 60)

            # Tier-3 cache write (one response file per input identifier)
            if self._cache.writes_enabled():
                self._write_identifier_responses(
                    HIERARCHICAL_CACHE_PREFIX,
                    identifiers,
                    list(software_reqs_dict.keys()),
                    result,
                    api_id_to_identifier,
                )
                self._cache.mark_refreshed(cache_key)

            return result

        except Exception as e:
            self._logger.error(
                "Fatal error in get_hierarchical_trace_from_gids: %s",
                str(e),
                exc_info=True
            )
            raise

    @timing(PYJAMA_LOGGERNAME)
    def get_bidirectional_trace_from_gids(
        self,
        identifiers: List[str],
        project_name: str,
        api_id_key: Optional[str] = None,
        user_need_typekey: Optional[Union[str, List[str]]] = None,
        design_typekey: Optional[Union[str, List[str]]] = None,
        testcase_typekey: Optional[Union[str, List[str]]] = None,
        prq_type_field: str = "PRQ_type$63",
    ) -> List[Dict[str, Any]]:
        """
        Build bidirectional traceability structure for each software requirement.
        
        For each input GID (software requirement), creates a complete bidirectional structure showing:
        - UPSTREAM: Hierarchical path (requirement → system requirements → user needs)
        - DOWNSTREAM: Connected test cases and design documents
        
        This method preserves hierarchical upstream relationships while also including
        downstream verification and design artifacts.
        
        Optimized workflow:
        1. Resolve project name to ID (using cache)
        2. Fetch abstract items filtered by REQUIREMENT_ITEM_TYPE_ID (single API call)
        3. Map input identifiers to API IDs
        4. Classify ALL abstract items into software reqs, system reqs, and user needs
        5. PARALLEL: Batch fetch BOTH upstream and downstream for ALL software requirements concurrently
        6. Identify connected system requirements from upstream results
        7. Batch fetch upstream items for ALL connected system requirements concurrently
        8. Extract and classify downstream test cases and design docs
        9. Assemble complete bidirectional structure with upstream hierarchy and downstream items
        
        Performance optimizations:
        - Single abstract items fetch (reused for all classifications)
        - Concurrent upstream/downstream fetches in parallel (Step 5)
        - In-memory filtering using PRQ_type field (no additional API calls)
        - Efficient set operations for identifying connected items
        
        Args:
            identifiers: List of JAMA identifier strings (GIDs or document keys)
            project_name: Name of the Jama project (required)
            api_id_key: Key for item ID in API responses (default: 'id')
            user_need_typekey: Type key(s) for user needs — a single typekey or a
                list of typekeys (default: 'UND')
            design_typekey: Type key for design documents (default: 'DES')
            testcase_typekey: Type key for test cases (default: 'TEST')
            prq_type_field: Field name for requirement type pick list (default: "PRQ_type$63")
            
        Returns:
            List of requirement dicts with nested system_requirements (with user_needs),
            test_cases, and design_docs
            
        Raises:
            ValueError: If no identifiers resolve or invalid project name
        """
        self._logger.info("=" * 60)
        self._logger.info("Starting bidirectional trace extraction from identifiers")
        self._logger.info("Input identifiers: %d", len(identifiers))
        self._logger.info("Project: %s", project_name)
        self._logger.info("=" * 60)

        # Test mode: serve strictly from cache, never touch the Jama API.
        if self._test_mode:
            cached = self._load_identifier_responses(BIDIRECTIONAL_CACHE_PREFIX, identifiers)
            return self._require_cached(
                cached, f"bidirectional identifiers {identifiers}",
                self._identifiers_cache_folder(),
            )

        # Tier-3 cache check
        cache_key = f"identifiers:bidirectional:{tuple(sorted(identifiers))}"
        if not self._cache.should_recompute(cache_key):
            cached = self._load_identifier_responses(BIDIRECTIONAL_CACHE_PREFIX, identifiers)
            if cached is not None:
                return cached

        # Set defaults
        api_id_key = api_id_key or ID_KEY
        user_need_typekey = normalize_typekeys(user_need_typekey, [DEFAULT_USER_NEED_TYPEKEY])
        design_typekey = normalize_typekeys(design_typekey, DEFAULT_DESIGN_TYPEKEYS)
        testcase_typekey = normalize_typekeys(testcase_typekey, [DEFAULT_TESTCASE_TYPEKEY])
        
        try:
            # Step 1: Resolve project name to ID using cache
            self._logger.info("Step 1: Resolving project name to ID")
            project_id = self._project_cache.resolve_project_id(project_name, api_id_key=api_id_key)
            
            # Step 2: Fetch abstract items
            self._logger.info("Step 2: Fetching abstract items for project_id: %d", project_id)
            abstract_items = self.client.get_abstract_items(
                project=project_id,
                item_type=[REQUIREMENT_ITEM_TYPE_ID, USER_NEED_ITEM_TYPE_ID]
            )
            self._logger.info("Retrieved %d abstract items", len(abstract_items))
            
            # Step 3: Map identifiers to API IDs
            self._logger.info("Step 3: Mapping identifiers to API IDs")
            id_to_api_id, unresolved_ids = map_identifiers_to_api_ids(
                items=abstract_items,
                identifiers=identifiers,
                api_id_key=api_id_key,
                raise_on_empty=True,
                logger=self._logger,
            )
            
            software_req_ids = list(id_to_api_id.values())
            self._logger.info(
                "Successfully resolved %d/%d identifiers to API IDs",
                len(software_req_ids),
                len(identifiers)
            )
            
            # Create reverse mapping: api_id -> original_identifier (e.g., GID format)
            api_id_to_identifier = {v: k for k, v in id_to_api_id.items()}
            self._logger.debug("Created reverse mapping for %d identifiers", len(api_id_to_identifier))
            
            # Step 4: Classify abstract items
            self._logger.info(
                "Step 4: Classifying abstract items (software reqs, system reqs, user needs)"
            )
            
            software_reqs_dict = {}
            system_reqs_dict = {}
            user_needs_dict = {}
            
            for item in abstract_items:
                item_id = item[api_id_key]
                doc_key = get_doc_key(item, dockey_key=DOCUMENT_KEY)
                fields = item.get(FIELDS_KEY, {})
                prq_type_id = fields.get(REQUIREMENT_ITEM_TYPE_FIELD_NAME)
                
                if any(tk in doc_key for tk in user_need_typekey):
                    user_needs_dict[item_id] = item
                elif prq_type_id == SYSTEM_REQUIREMENT_TYPE_ID:
                    system_reqs_dict[item_id] = item
                elif item_id in software_req_ids:
                    software_reqs_dict[item_id] = item
            
            self._logger.info(
                "Classification complete: %d software reqs, %d system reqs, %d user needs",
                len(software_reqs_dict),
                len(system_reqs_dict),
                len(user_needs_dict)
            )
            
            if not software_reqs_dict:
                self._logger.warning(
                    "No software requirements found in input identifiers. Returning empty result."
                )
                if self._cache.writes_enabled():
                    self._write_identifier_responses(
                        BIDIRECTIONAL_CACHE_PREFIX, identifiers, [], [], api_id_to_identifier
                    )
                    self._cache.mark_refreshed(cache_key)
                return []

            # Step 5: PARALLEL - Fetch upstream AND downstream for software requirements
            self._logger.info(
                "Step 5: PARALLEL batch fetching upstream AND downstream items for %d software requirements",
                len(software_reqs_dict)
            )
            
            software_upstream_results = {}
            software_downstream_results = {}
            
            with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                upstream_futures = {
                    executor.submit(self._get_upstream_with_id, req_id): req_id
                    for req_id in software_reqs_dict.keys()
                }
                downstream_futures = {
                    executor.submit(self._get_downstream, req_id): req_id
                    for req_id in software_reqs_dict.keys()
                }
                
                for future in concurrent.futures.as_completed(upstream_futures):
                    req_id, upstream_items = future.result()
                    software_upstream_results[req_id] = upstream_items
                    self._logger.debug(
                        "Collected %d upstream items for software req ID: %d",
                        len(upstream_items),
                        req_id
                    )
                
                for future in concurrent.futures.as_completed(downstream_futures):
                    req_id, downstream_items = future.result()
                    software_downstream_results[req_id] = downstream_items
                    self._logger.debug(
                        "Collected %d downstream items for software req ID: %d",
                        len(downstream_items),
                        req_id
                    )
            
            total_software_upstream = sum(len(items) for items in software_upstream_results.values())
            total_software_downstream = sum(len(items) for items in software_downstream_results.values())
            self._logger.info(
                "Completed upstream/downstream fetch: %d upstream, %d downstream",
                total_software_upstream,
                total_software_downstream
            )
            
            # Step 6: Identify connected system requirements
            self._logger.info("Step 6: Identifying connected system requirements")
            
            connected_system_req_ids = set()
            for upstream_items in software_upstream_results.values():
                for item in upstream_items:
                    item_id = item[api_id_key]
                    if item_id in system_reqs_dict:
                        connected_system_req_ids.add(item_id)
            
            self._logger.info(
                "Identified %d connected system requirements",
                len(connected_system_req_ids)
            )
            
            # Step 7: Fetch upstream for system requirements
            self._logger.info(
                "Step 7: Batch fetching upstream items for %d connected system requirements",
                len(connected_system_req_ids)
            )
            
            system_upstream_results = {}
            
            if connected_system_req_ids:
                with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                    upstream_futures = {
                        executor.submit(self._get_upstream_with_id, req_id): req_id
                        for req_id in connected_system_req_ids
                    }
                    
                    for future in concurrent.futures.as_completed(upstream_futures):
                        req_id, upstream_items = future.result()
                        system_upstream_results[req_id] = upstream_items
                
                total_system_upstream = sum(len(items) for items in system_upstream_results.values())
                self._logger.info(
                    "Completed upstream fetch for system requirements (%d total items)",
                    total_system_upstream
                )
            
            # Step 8: Extract test cases and design docs
            self._logger.info("Step 8: Extracting test cases and design docs from downstream items")
            
            test_cases_dict = {}
            design_docs_dict = {}
            
            for req_id, downstream_items in software_downstream_results.items():
                for item in downstream_items:
                    doc_key = get_doc_key(item, dockey_key=DOCUMENT_KEY)
                    item_id = item[api_id_key]

                    if any(tk in doc_key for tk in testcase_typekey):
                        if item_id not in test_cases_dict:
                            test_cases_dict[item_id] = item
                    elif any(tk in doc_key for tk in design_typekey):
                        if item_id not in design_docs_dict:
                            design_docs_dict[item_id] = item
            
            self._logger.info(
                "Extracted downstream items: %d test cases, %d design docs",
                len(test_cases_dict),
                len(design_docs_dict)
            )
            
            # Step 9: Assemble bidirectional structure
            self._logger.info("Step 9: Assembling bidirectional trace structure")
            
            result = self._bidirectional_trace_assembler.assemble(
                software_reqs_dict=software_reqs_dict,
                software_upstream_results=software_upstream_results,
                software_downstream_results=software_downstream_results,
                system_reqs_dict=system_reqs_dict,
                system_upstream_results=system_upstream_results,
                user_needs_dict=user_needs_dict,
                test_cases_dict=test_cases_dict,
                design_docs_dict=design_docs_dict,
                api_id_key=api_id_key,
                testcase_typekey=testcase_typekey,
                design_typekey=design_typekey,
                api_id_to_identifier=api_id_to_identifier,
            )
            
            self._logger.info("=" * 60)
            self._logger.info("Completed bidirectional trace extraction")
            self._logger.info("Total software requirements: %d", len(result))
            
            # Summary statistics
            total_system_reqs = sum(
                len(req[SYSTEM_REQUIREMENTS_KEY]) for req in result
            )
            total_user_needs = sum(
                sum(len(sys_req[USER_NEEDS_KEY]) for sys_req in req[SYSTEM_REQUIREMENTS_KEY])
                for req in result
            )
            total_test_cases = sum(
                len(req[TEST_CASES_KEY]) for req in result
            )
            total_design_docs = sum(
                len(req[DESIGN_DOCS_KEY]) for req in result
            )
            
            reqs_with_system = sum(
                1 for req in result if len(req[SYSTEM_REQUIREMENTS_KEY]) > 0
            )
            reqs_without_system = len(result) - reqs_with_system
            
            reqs_with_tests = sum(
                1 for req in result if len(req[TEST_CASES_KEY]) > 0
            )
            reqs_without_tests = len(result) - reqs_with_tests
            
            self._logger.info("Summary:")
            self._logger.info("  Software requirements: %d", len(result))
            self._logger.info("  Software reqs with system reqs: %d", reqs_with_system)
            self._logger.info("  Software reqs without system reqs: %d", reqs_without_system)
            self._logger.info("  Total system requirements: %d", total_system_reqs)
            self._logger.info("  Total user needs: %d", total_user_needs)
            self._logger.info("  Software reqs with test cases: %d", reqs_with_tests)
            self._logger.info("  Software reqs without test cases: %d", reqs_without_tests)
            self._logger.info("  Total test cases: %d", total_test_cases)
            self._logger.info("  Total design documents: %d", total_design_docs)
            self._logger.info("=" * 60)

            # Tier-3 cache write (one response file per input identifier)
            if self._cache.writes_enabled():
                self._write_identifier_responses(
                    BIDIRECTIONAL_CACHE_PREFIX,
                    identifiers,
                    list(software_reqs_dict.keys()),
                    result,
                    api_id_to_identifier,
                )
                self._cache.mark_refreshed(cache_key)

            return result

        except Exception as e:
            self._logger.error(
                "Fatal error in get_bidirectional_trace_from_gids: %s",
                str(e),
                exc_info=True
            )
            raise

    
            self._logger.info("  Software reqs without system reqs: %d", reqs_without_system)
            self._logger.info("  Total system requirements: %d", total_system_reqs)
            self._logger.info("  Total user needs: %d", total_user_needs)
            self._logger.info("  Software reqs with test cases: %d", reqs_with_tests)
            self._logger.info("  Software reqs without test cases: %d", reqs_without_tests)
            self._logger.info("  Total test cases: %d", total_test_cases)
            self._logger.info("  Total design documents: %d", total_design_docs)
            self._logger.info("=" * 60)

            # Tier-3 cache write (one response file per input identifier)
            if self._cache.writes_enabled():
                self._write_identifier_responses(
                    BIDIRECTIONAL_CACHE_PREFIX,
                    identifiers,
                    list(software_reqs_dict.keys()),
                    result,
                    api_id_to_identifier,
                )
                self._cache.mark_refreshed(cache_key)

            return result

        except Exception as e:
            self._logger.error(
                "Fatal error in get_bidirectional_trace_from_gids: %s",
                str(e),
                exc_info=True
            )
            raise

    