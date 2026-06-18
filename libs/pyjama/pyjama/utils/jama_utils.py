"""Utility functions for Jama Connect API operations."""
import os
import re
import html
from typing import Any, Dict, List, Optional, Set, Tuple
import pandas as pd

from .jama_constants import (
    CLEANING_PATTERNS,
    TOKEN_PATTERN,
    REPLACE_WITH,
    WHITESPACE_RE,
    FIELDS_KEY,
    DOCUMENT_KEY,
    GLOBAL_ID_KEY,
    ID_KEY,
    ACTION_KEY,
    EXPECTED_RESULT_KEY,
    REQUIREMENT_KEY,
    REQUIREMENT_ID_KEY,
    TEXT_KEY,
    DESCRIPTION_KEY,
    TEST_CASES_KEY,
    DESIGN_DOCS_KEY,
    TEST_ID_KEY,
    DOC_ID_KEY,
    NAME_KEY,
    SETUP_KEY,
    SETUP_OUTPUT_KEY,
    STEPS_OUTPUT_KEY,
    EXPECTED_RESULTS_OUTPUT_KEY,
    IN_REVIEW_BASELINE_KEY,
    TEST_CASE_STEPS_KEY,
)

def get_jama_credentials(id_name="JAMA_CLIENT_ID", secret_name="JAMA_CLIENT_SECRET"):
    """Get Jama credentials from environment variables."""
    jama_client_id = os.getenv(id_name)
    jama_client_secret = os.getenv(secret_name)
    credentials = (jama_client_id, jama_client_secret)
    return credentials


def clean_html(text: str) -> str:
    """
    Strip HTML tags and decode entities for clean text output.
    
    Args:
        text: Raw HTML text from Jama API
        
    Returns:
        Cleaned plain text
    """
    if not text:
        return ""
    
    # Remove HTML patterns
    for pattern in CLEANING_PATTERNS:
        text = pattern.sub(" ", text)
    
    # Decode HTML entities (e.g., &nbsp; → space, &amp; → &, &lt; → <)
    text = html.unescape(text)
    
    # Replace specific tokens
    text = TOKEN_PATTERN.sub(REPLACE_WITH, text)
    
    # Final whitespace cleanup
    text = WHITESPACE_RE.sub(" ", text)
    
    return text.strip()


def get_doc_key(item: Dict[str, Any], dockey_key: Optional[str] = None) -> str:
    """
    Safely extract the documentKey from a Jama item payload.
    
    Args:
        item: Jama item dictionary
        dockey_key: Optional override for document key field name
        
    Returns:
        Document key string (e.g., "GID-12345")
    """
    dockey_key = dockey_key or DOCUMENT_KEY
    fields = item.get(FIELDS_KEY, {})
    value = fields.get(dockey_key, item.get(dockey_key, ""))
    # Defensive: a malformed payload may carry a non-string documentKey. Coerce
    # so downstream substring matching (``typekey in doc_key``) can never raise.
    if value is None:
        return ""
    return value if isinstance(value, str) else str(value)


def normalize_typekeys(value: Any, default: Optional[Any] = None) -> List[str]:
    """
    Coerce a typekey argument into a list of string matchers.

    Accepts a single typekey (``"DES"``), a sequence of typekeys
    (``["DES", "DESIGN"]``), or ``None``/empty (falls back to ``default``).
    Empty/falsy entries are dropped. This lets every public method treat
    ``design_typekey`` / ``testcase_typekey`` / ``requirement_typekeys``
    uniformly as "one or more substrings to match against a document key",
    so passing a list where a string was historically expected no longer
    raises ``TypeError`` in ``typekey in doc_key`` checks.

    Args:
        value: A single typekey, a sequence of typekeys, or None.
        default: Fallback typekey(s) used when ``value`` is None/empty.

    Returns:
        List of non-empty typekey strings.
    """
    if value is None or value == "":
        value = default if default is not None else []
    if isinstance(value, str):
        value = [value]
    return [str(tk) for tk in value if tk]


def format_test_steps(steps: List[Dict[str, Any]]) -> Tuple[str, str]:
    """
    Format Jama testCaseSteps into readable text with step numbering.
    
    Args:
        steps: List of test step dictionaries from Jama
        
    Returns:
        Tuple of (formatted_actions, formatted_expected_results)
    """
    actions = []
    expected = []

    for i, step in enumerate(steps, 1):
        action = clean_html(step.get(ACTION_KEY, ""))
        result = clean_html(step.get(EXPECTED_RESULT_KEY, ""))

        if action:
            actions.append(f"Step {i}. {action}")
        if result:
            expected.append(f"ExpectedResult {i}. {result}")

    return "\n".join(actions), "\n".join(expected)


def parse_gid(gid: str) -> Optional[int]:
    """
    Parse a JAMA GID string to extract the numeric ID.
    
    Args:
        gid: GID string (e.g., 'GID-12345')
        
    Returns:
        Numeric ID if valid format, None otherwise
        
    Examples:
        >>> parse_gid("GID-12345")
        12345
        >>> parse_gid("invalid")
        None
    """
    match = re.match(r"GID-(\d+)", gid, re.IGNORECASE)
    return int(match.group(1)) if match else None


def extract_version_number(name: str) -> int:
    """
    Extract version number from baseline name.
    
    Args:
        name: Baseline name (e.g., 'baseline_v3')
        
    Returns:
        Version number, or -1 if not found
        
    Examples:
        >>> extract_version_number("baseline_v3")
        3
        >>> extract_version_number("no_version")
        -1
    """
    match = re.search(r"v(\d+)$", name)
    return int(match.group(1)) if match else -1

def get_items_map(jama_client):
    items_map = jama_client.get_item_types()
    items_map_dict = {}
    for item_type in items_map:
        items_map_dict[item_type['typeKey']] = item_type['id']
    return items_map_dict


def map_item_identifiers(
    items: List[Dict[str, Any]],
    requested_ids: Optional[List[str]],
    source_key: str,
    target_key: str = "id",
    context_name: str = "identifiers",
    raise_on_empty: bool = True,
    logger: Optional[Any] = None,
) -> Tuple[Dict[str, Any], List[str]]:
    """
    Build a mapping from source identifiers to target identifiers for requested items.
    
    Generic utility for mapping between different identifier types in Jama items.
    Supports both top-level and nested field paths using dot notation.
    
    Args:
        items: List of Jama item dictionaries to search
        requested_ids: List of source identifiers to map (e.g., ["GID-123", "PRQ-456"]).
                      If None, maps ALL items to their target identifiers.
        source_key: Dot-notation path to source identifier
                   Examples: "globalId", "fields.documentKey", "typeKey"
        target_key: Dot-notation path to target identifier (default: "id")
        context_name: Human-readable name for error messages (e.g., "GIDs", "document keys")
        raise_on_empty: Whether to raise ValueError if no mappings found
        logger: Optional logger instance for debug/warning messages
        
    Returns:
        Tuple of (mapping_dict, unresolved_list)
        - mapping_dict: {source_id: target_value} for successfully mapped items
        - unresolved_list: List of requested_ids that couldn't be resolved
                          (empty list when requested_ids=None)
        
    Raises:
        ValueError: If raise_on_empty=True and no mappings found
        
    Examples:
        >>> # Map GIDs to API IDs (top-level field)
        >>> items = [{"globalId": "GID-123", "id": 456}]
        >>> mapping, _ = map_item_identifiers(
        ...     items, ["GID-123"], source_key="globalId"
        ... )
        >>> mapping
        {"GID-123": 456}
        
        >>> # Map document keys to API IDs (nested field)
        >>> items = [{"fields": {"documentKey": "PRQ-001"}, "id": 123}]
        >>> mapping, _ = map_item_identifiers(
        ...     items, ["PRQ-001"], source_key="fields.documentKey"
        ... )
        >>> mapping
        {"PRQ-001": 123}
        
        >>> # Map type keys to type IDs
        >>> items = [{"typeKey": "REQ", "id": 63}]
        >>> mapping, _ = map_item_identifiers(
        ...     items, ["REQ"], source_key="typeKey"
        ... )
        >>> mapping
        {"REQ": 63}
        
        >>> # Map ALL items (requested_ids=None)
        >>> items = [
        ...     {"globalId": "GID-123", "id": 456},
        ...     {"globalId": "GID-789", "id": 999}
        ... ]
        >>> mapping, _ = map_item_identifiers(
        ...     items, None, source_key="globalId"
        ... )
        >>> mapping
        {"GID-123": 456, "GID-789": 999}
    """
    # Helper to extract nested keys using dot notation
    def get_nested_value(item: Dict[str, Any], key_path: str) -> Any:
        """Extract value from nested dict using dot notation (e.g., 'fields.documentKey')."""        
        keys = key_path.split(".")
        value = item
        for key in keys:
            if not isinstance(value, dict):
                return None
            value = value.get(key)
            if value is None:
                return None
        return value
    
    # Determine if we're mapping all items or filtering to requested IDs
    map_all = requested_ids is None
    requested_set = None if map_all else set(requested_ids)
    mapping = {}
    
    # Build mapping
    for item in items:
        source_id = get_nested_value(item, source_key)
        if not source_id:
            continue
            
        # Include item if mapping all OR if it's in the requested set
        if map_all or source_id in requested_set:
            target_id = get_nested_value(item, target_key)
            if target_id is not None:
                mapping[source_id] = target_id
                if logger:
                    logger.debug("Mapped %s '%s' -> %s", context_name, source_id, target_id)
    
    # Check for unresolved IDs (only applicable when filtering)
    if map_all:
        unresolved = []
    else:
        unresolved = sorted(requested_set - set(mapping.keys()))
        
        if logger and unresolved:
            logger.warning(
                "Could not resolve %d %s: %s",
                len(unresolved),
                context_name,
                unresolved[:10]  # Limit output for large lists
            )
    
    # Validate if required
    if raise_on_empty and not mapping:
        if map_all:
            raise ValueError(
                f"No items found with valid {context_name} in source_key '{source_key}'."
            )
        else:
            raise ValueError(
                f"None of the provided {context_name} could be resolved. "
                f"Requested: {requested_ids[:10]}{'...' if len(requested_ids) > 10 else ''}"
            )
    
    return mapping, unresolved


def map_gids_to_api_ids(
    items: List[Dict[str, Any]],
    gids: Optional[List[str]],
    api_id_key: str = "id",
    raise_on_empty: bool = True,
    logger: Optional[Any] = None,
) -> Tuple[Dict[str, int], List[str]]:
    """
    Map Jama Global IDs (GIDs) to their numeric API IDs.
    
    Convenience wrapper for mapping GID identifiers (e.g., "GID-2788627").
    
    Args:
        items: List of Jama item dictionaries (must contain globalId field)
        gids: List of GID strings (e.g., ["GID-2788627", "GID-2788628"]).
              If None, maps ALL items to their API IDs.
        api_id_key: Key for API ID in item dictionaries (default: "id")
        raise_on_empty: Whether to raise ValueError if no GIDs resolved
        logger: Optional logger for debug/warning messages
        
    Returns:
        Tuple of (gid_to_api_id_dict, unresolved_gids_list)
        
    Examples:
        >>> items = [{"globalId": "GID-123", "id": 456}]
        >>> mapping, unresolved = map_gids_to_api_ids(items, ["GID-123"])
        >>> mapping
        {"GID-123": 456}
        
        >>> # Map all GIDs
        >>> mapping, _ = map_gids_to_api_ids(items, None)
        >>> mapping
        {"GID-123": 456}
    """
    return map_item_identifiers(
        items=items,
        requested_ids=gids,
        source_key=GLOBAL_ID_KEY,
        target_key=api_id_key,
        context_name="GIDs",
        raise_on_empty=raise_on_empty,
        logger=logger,
    )


def map_document_keys_to_api_ids(
    items: List[Dict[str, Any]],
    doc_keys: Optional[List[str]],
    api_id_key: str = "id",
    raise_on_empty: bool = True,
    logger: Optional[Any] = None,
) -> Tuple[Dict[str, int], List[str]]:
    """
    Map Jama document keys to their numeric API IDs.
    
    Convenience wrapper for mapping document key identifiers (e.g., "PRQ-123", "REQ-456").
    Document keys are typically stored in fields.documentKey.
    
    Args:
        items: List of Jama item dictionaries (must contain fields.documentKey)
        doc_keys: List of document key strings (e.g., ["PRQ-123", "REQ-456"]).
                 If None, maps ALL items to their API IDs.
        api_id_key: Key for API ID in item dictionaries (default: "id")
        raise_on_empty: Whether to raise ValueError if no document keys resolved
        logger: Optional logger for debug/warning messages
        
    Returns:
        Tuple of (doc_key_to_api_id_dict, unresolved_doc_keys_list)
        
    Examples:
        >>> items = [{"fields": {"documentKey": "PRQ-123"}, "id": 456}]
        >>> mapping, unresolved = map_document_keys_to_api_ids(items, ["PRQ-123"])
        >>> mapping
        {"PRQ-123": 456}
        
        >>> # Map all document keys
        >>> mapping, _ = map_document_keys_to_api_ids(items, None)
        >>> mapping
        {"PRQ-123": 456}
    """
    return map_item_identifiers(
        items=items,
        requested_ids=doc_keys,
        source_key=f"{FIELDS_KEY}.{DOCUMENT_KEY}",
        target_key=api_id_key,
        context_name="document keys",
        raise_on_empty=raise_on_empty,
        logger=logger,
    )


def map_identifiers_to_api_ids(
    items: List[Dict[str, Any]],
    identifiers: Optional[List[str]],
    api_id_key: str = "id",
    raise_on_empty: bool = True,
    logger: Optional[Any] = None,
) -> Tuple[Dict[str, int], List[str]]:
    """
    Smart wrapper that auto-detects identifier format and maps to API IDs.
    
    Automatically detects whether identifiers are GIDs or document keys based on format.
    - GIDs start with "GID-" (case-insensitive)
    - Document keys are anything else (e.g., "PRQ-123", "REQ-456")
    
    Args:
        items: List of Jama item dictionaries
        identifiers: List of identifier strings (mixed GIDs and doc keys allowed).
                    If None, maps ALL items (both GIDs and document keys) to their API IDs.
        api_id_key: Key for API ID in item dictionaries (default: "id")
        raise_on_empty: Whether to raise ValueError if no identifiers resolved
        logger: Optional logger for debug/warning messages
        
    Returns:
        Tuple of (identifier_to_api_id_dict, unresolved_identifiers_list)
        
    Examples:
        >>> items = [
        ...     {"globalId": "GID-123", "id": 456},
        ...     {"fields": {"documentKey": "PRQ-789"}, "id": 999}
        ... ]
        >>> mapping, _ = map_identifiers_to_api_ids(
        ...     items, ["GID-123", "PRQ-789"]
        ... )
        >>> mapping
        {"GID-123": 456, "PRQ-789": 999}
        
        >>> # Map all identifiers
        >>> mapping, _ = map_identifiers_to_api_ids(items, None)
        >>> mapping
        {"GID-123": 456, "PRQ-789": 999}
    """
    # Handle None case - map all items
    if identifiers is None:
        gids = None
        doc_keys = None
    else:
        # Separate GIDs from document keys
        gids = [id for id in identifiers if id.upper().startswith("GID-")]
        doc_keys = [id for id in identifiers if not id.upper().startswith("GID-")]
    
    mapping = {}
    unresolved = []
    
    # Map GIDs (if None is passed, map all GIDs; if empty list, skip)
    if gids is None or gids:
        gid_mapping, gid_unresolved = map_gids_to_api_ids(
            items=items,
            gids=gids,
            api_id_key=api_id_key,
            raise_on_empty=False,
            logger=logger,
        )
        mapping.update(gid_mapping)
        unresolved.extend(gid_unresolved)
    
    # Map document keys (if None is passed, map all doc keys; if empty list, skip)
    if doc_keys is None or doc_keys:
        doc_mapping, doc_unresolved = map_document_keys_to_api_ids(
            items=items,
            doc_keys=doc_keys,
            api_id_key=api_id_key,
            raise_on_empty=False,
            logger=logger,
        )
        mapping.update(doc_mapping)
        unresolved.extend(doc_unresolved)
    
    # Validate if required
    if raise_on_empty and not mapping:
        if identifiers is None:
            raise ValueError(
                "No items found with valid identifiers (GIDs or document keys)."
            )
        else:
            raise ValueError(
                f"None of the provided identifiers could be resolved. "
                f"Requested: {identifiers[:10]}{'...' if len(identifiers) > 10 else ''}"
            )
    
    return mapping, sorted(unresolved)


def build_simple_requirement_data(req_item: Dict[str, Any]) -> Dict[str, Any]:
    """
    Build a simplified requirement data structure (just req_id and text).
    Used for user_needs, system_requirements, and requirements lists.
    
    Args:
        req_item: Jama requirement item dictionary
        
    Returns:
        Dictionary with req_id and text fields
    """
    req_fields = req_item.get(FIELDS_KEY, {})
    req_id = get_doc_key(req_item)
    
    return {
        REQUIREMENT_ID_KEY: req_id,
        TEXT_KEY: clean_html(
            req_fields.get(DESCRIPTION_KEY, "")
        ),
    }


def build_requirement_data(req_item: Dict[str, Any]) -> Dict[str, Any]:
    """
    Build the base requirement data structure from a Jama requirement item.
    
    Creates a nested structure with requirement object and empty arrays for
    test_cases and design_docs to be populated later.
    
    Args:
        req_item: Jama requirement item dictionary
        
    Returns:
        Dictionary with requirement, test_cases, and design_docs fields
    """
    req_fields = req_item.get(FIELDS_KEY, {})
    req_id = get_doc_key(req_item)
    
    return {
        REQUIREMENT_KEY: {
            REQUIREMENT_ID_KEY: req_id,
            TEXT_KEY: clean_html(
                req_fields.get(DESCRIPTION_KEY, "")
            ),
        },
        TEST_CASES_KEY: [],
        DESIGN_DOCS_KEY: [],
    }


def build_simple_test_case_data(test_item: Dict[str, Any]) -> Dict[str, Any]:
    """
    Build a simplified test case data structure without in_review_baseline flag.
    
    Args:
        test_item: Jama test case item dictionary
        
    Returns:
        Dictionary with test_id, description, setup, steps, and expectedResults
    """
    d_key = get_doc_key(test_item)
    fields = test_item.get(FIELDS_KEY, {})
    
    steps_text, expected_text = format_test_steps(
        fields.get(TEST_CASE_STEPS_KEY, []) or []
    )
    
    return {
        TEST_ID_KEY: d_key,
        DESCRIPTION_KEY: clean_html(fields.get(NAME_KEY, "")),
        SETUP_OUTPUT_KEY: clean_html(fields.get(SETUP_KEY, "")),
        STEPS_OUTPUT_KEY: steps_text,
        EXPECTED_RESULTS_OUTPUT_KEY: expected_text,
    }


def build_test_case_data(
    test_item: Dict[str, Any], 
    review_test_keys: Set[str]
) -> Dict[str, Any]:
    """
    Build test case data structure with in_review_baseline flag.
    
    Args:
        test_item: Jama test case item dictionary
        review_test_keys: Set of test case document keys that are in the review baseline
        
    Returns:
        Dictionary with test_id, description, setup, steps, expectedResults,
        and in_review_baseline flag
    """
    d_key = get_doc_key(test_item)
    fields = test_item.get(FIELDS_KEY, {})
    
    steps_text, expected_text = format_test_steps(
        fields.get(TEST_CASE_STEPS_KEY, []) or []
    )
    
    return {
        TEST_ID_KEY: d_key,
        DESCRIPTION_KEY: clean_html(fields.get(NAME_KEY, "")),
        SETUP_OUTPUT_KEY: clean_html(fields.get(SETUP_KEY, "")),
        STEPS_OUTPUT_KEY: steps_text,
        EXPECTED_RESULTS_OUTPUT_KEY: expected_text,
        IN_REVIEW_BASELINE_KEY: d_key in review_test_keys,
    }


def build_design_doc_data(design_item: Dict[str, Any]) -> Dict[str, Any]:
    """
    Build design document data structure.
    
    Args:
        design_item: Jama design document item dictionary
        
    Returns:
        Dictionary with doc_id, name, and description fields
    """
    d_key = get_doc_key(design_item)
    fields = design_item.get(FIELDS_KEY, {})
    
    return {
        DOC_ID_KEY: d_key,
        NAME_KEY: clean_html(fields.get(NAME_KEY, "")),
        DESCRIPTION_KEY: clean_html(
            fields.get(DESCRIPTION_KEY, "")
        ),
    }