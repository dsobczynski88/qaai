"""Constants for Jama Connect API integration."""
import re

# ----------------------------
# Logger configuration
# ----------------------------
# Single logger name used across all contexts (pytest, langgraph, standalone)
# All logging goes to a unified pyjama.log file per run
PYJAMA_LOGGERNAME = "pyjama"

# ----------------------------
# Tier-3 (local disk) caching
# ----------------------------
CACHE_SOURCE_ROOT = "./cache/source"
CACHE_TIMESTAMP_FORMAT = "%Y_%m_%d_%H_%M_%S_%f"  # e.g. 2026_05_26_22_11_45_006721

CACHE_PROJECTS_SUBDIR = "projects"
CACHE_BASELINES_SUBDIR = "baselines"
CACHE_IDENTIFIERS_SUBDIR = "identifiers"

# Per-method cache filename prefixes
TEST_SUITE_CACHE_PREFIX = "test_suite_reviewer_structure"
TEST_CASE_CACHE_PREFIX = "test_case_reviewer_structure"
BIDIRECTIONAL_CACHE_PREFIX = "bidirectional_trace"
HIERARCHICAL_CACHE_PREFIX = "hierarchical_trace"
RTM_CACHE_PREFIX = "rtm"

# Cache filename fragments
IDS_FILE_FRAGMENT = "ids"
RESPONSE_FILE_FRAGMENT = "response"

# ids jsonl "type" values
IDS_TYPE_REQUIREMENT = "requirement"
IDS_TYPE_TEST_CASE = "test_case"

# Environment variable holding the Jama host address (replaces config.yaml)
JAMA_HOST_ADDRESS_ENV = "JAMA_HOST_ADDRESS"

# ----------------------------
# Project directory caching
# ----------------------------
# Project-directory cache files live under the shared cache root's "projects"
# subdir (CACHE_PROJECTS_SUBDIR) and reuse CACHE_TIMESTAMP_FORMAT, both resolved
# through DiskCacheManager. Only the filename prefix is project-specific.
PROJECT_DIR_PREFIX = "pyjamaapi_project_directory_"

# ----------------------------
# Regex patterns for cleaning retrieved text from JAMA API
# ----------------------------
TABLE_RE = re.compile(r"<table.*?>.*?</table>", re.DOTALL | re.IGNORECASE)
TAG_RE = re.compile(r"<[^>]+>")
WHITESPACE_RE = re.compile(r"\s+")

CLEANING_PATTERNS = (
    TABLE_RE,
    TAG_RE,
    WHITESPACE_RE,
)

REPLACE_TOKENS = [
    'UI&amp;quot;,', '-f"', '&amp;lt;', '&amp;gt;', '&amp;#39;',
    'ui&amp;quot;', '&amp;nbsp;', '&amp;amp;'
]
REPLACE_WITH = ' '

TOKEN_PATTERN = re.compile(
    '|'.join(map(re.escape, REPLACE_TOKENS))
)

# ----------------------------
# API SETTINGS
# ----------------------------
ALLOWED_RESULTS_PER_PAGE = 50

# ----------------------------
# Common API field / payload keys
# ----------------------------
GLOBAL_ID_KEY = "globalId"
FIELDS_KEY = "fields"
ID_KEY = "id"
NAME_KEY = "name"
DESCRIPTION_KEY = "description"
DOCUMENT_KEY = "documentKey"
TEST_CASE_STEPS_KEY = "testCaseSteps"
ACTION_KEY = "action"
EXPECTED_RESULT_KEY = "expectedResult"
SETUP_KEY = "setup$71"  # May vary by Jama instance
# ----------------------------
# Output payload keys
# ----------------------------
REQUIREMENT_KEY = "requirement"
REQUIREMENT_ID_KEY = "req_id"
TEXT_KEY = "text"
TEST_CASES_KEY = "test_cases"
DESIGN_DOCS_KEY = "design_docs"

TEST_ID_KEY = "test_id"
DOC_ID_KEY = "doc_id"
SETUP_OUTPUT_KEY = "setup"
STEPS_OUTPUT_KEY = "steps"
EXPECTED_RESULTS_OUTPUT_KEY = "expectedResults"
IN_REVIEW_BASELINE_KEY = "in_review_baseline"

# ----------------------------
# Default type keys
# ----------------------------
DEFAULT_DESIGN_TYPEKEYS = ("DES", "TDS")
DEFAULT_TESTCASE_TYPEKEY = "TEST"
DEFAULT_REQ_TYPEKEYS = ("REQ", "PRQ")
DEFAULT_USER_NEED_TYPEKEY = "UND"
DEFAULT_SYSTEM_REQ_TYPEKEY = "PRQ"
DEFAULT_MODULE_TYPEKEY = "MOD"

# ----------------------------
# Output keys for hierarchical structure
# ----------------------------
USER_NEEDS_KEY = "user_needs"
SYSTEM_REQUIREMENTS_KEY = "system_requirements"
REQUIREMENTS_KEY = "requirements"

# ----------------------------
# Pick list IDs
# ----------------------------
USER_NEED_ITEM_TYPE_ID = 62
REQUIREMENT_ITEM_TYPE_ID = 63
DESIGN_ITEM_TYPE_ID = 65
TEST_CASE_ITEM_TYPE_ID = 71
PRODUCT_REQUIREMENT_TYPE_PICK_LIST_NAME = "Product Requirement Type"
PRODUCT_REQUIREMENT_TYPE_PICK_LIST_ID = 271
REQUIREMENT_ITEM_TYPE_FIELD_NAME = "PRQ_type$63"
SYSTEM_REQUIREMENT_TYPE_ID = 1382

