"""Application-wide constants for AutoQA.

This module centralizes magic numbers and configuration defaults to improve
maintainability and make tuning easier. All constants are documented with
their purpose and typical use cases.
"""

# ============================================================================
# Rate Limiting Defaults
# ============================================================================

DEFAULT_MAX_REQUESTS_PER_MINUTE = 490
"""Default maximum API requests per minute.

Set to 490 to provide a buffer under typical 500 RPM limits. Adjust based on
your API tier and account limits.
"""

DEFAULT_MAX_TOKENS_PER_MINUTE = 200_000
"""Default maximum tokens per minute across all requests.

Typical for standard OpenAI accounts. Check your account's TPM limit and
adjust accordingly. Premium accounts may support higher values.
"""

DEFAULT_MAX_OUTPUT_TOKENS = 16_000
"""Default maximum output tokens per single request.

Haiku and similar models support up to 16K output tokens. This ensures the
summarizer can process 100+ test cases without truncation.
"""

# ============================================================================
# Batch Processing Configuration
# ============================================================================

DEFAULT_BATCH_SIZE = 25
"""Default batch size for test case summarization.

Tuned for models like Claude Haiku. Larger batches reduce API calls but may
hit token limits. Smaller batches increase latency but improve reliability.
"""

MAX_TEST_CASES_PER_REQUIREMENT = 1000
"""Maximum test cases allowed per requirement.

Safety limit to prevent memory exhaustion and API timeouts. Adjust based on
available memory and expected workload.
"""

MAX_REQUIREMENTS_PER_HAZARD = 100
"""Maximum requirements allowed per hazard record.

Safety limit for hazard review pipeline to prevent excessive fan-out and
memory consumption.
"""

# ============================================================================
# Retry Configuration
# ============================================================================

DEFAULT_INITIAL_RETRY_DELAY = 60.0
"""Initial delay in seconds before first retry attempt.

Used by async_retry_with_backoff for rate limit errors. Conservative default
to respect API rate limits.
"""

DEFAULT_MAX_RETRIES = 5
"""Maximum number of retry attempts for failed API calls.

Applies to rate limit errors and transient failures. Does not apply to
client errors (4xx status codes).
"""

DEFAULT_BACKOFF_FACTOR = 2.0
"""Exponential backoff multiplier for retry delays.

Each retry waits (delay * factor^attempt) seconds. Factor of 2.0 provides
reasonable spacing: 60s, 120s, 240s, 480s, 960s.
"""

# ============================================================================
# Timeout Configuration
# ============================================================================

DEFAULT_API_TIMEOUT = 120
"""Default timeout in seconds for single API requests.

Applies to individual LLM calls. Increase for complex prompts or slower models.
"""

DEFAULT_GRAPH_TIMEOUT = 300
"""Default timeout in seconds for full graph execution.

Applies to complete pipeline runs (RTM, hazard, or test case review). Increase
for large batches or complex requirements.
"""

# ============================================================================
# Validation Limits
# ============================================================================

MAX_THREAD_ID_LENGTH = 100
"""Maximum length for thread_id in API requests.

Prevents excessively long identifiers that could cause storage or logging issues.
"""

MAX_REQUIREMENT_TEXT_LENGTH = 10_000
"""Maximum length for requirement text fields.

Safety limit to prevent processing of malformed or excessively verbose requirements.
Typical requirements are 100-500 characters.
"""

MAX_REQUEST_BODY_SIZE = 10_000_000
"""Maximum request body size in bytes (10MB).

Prevents denial-of-service via large payloads. Adjust based on expected
requirement and test case volumes.
"""

# ============================================================================
# Logging Configuration
# ============================================================================

LOG_LEVEL_DEFAULT = "INFO"
"""Default logging level for application logs."""

LOG_LEVEL_INTEGRATION_TESTS = "DEBUG"
"""Logging level for integration test runs."""

LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
"""Standard log format for file handlers."""

# ============================================================================
# Error Codes
# ============================================================================

class ErrorCode:
    """Standardized error codes for API responses and internal errors."""
    
    # Client errors (4xx equivalent)
    INVALID_REQUEST = "INVALID_REQUEST"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    THREAD_NOT_FOUND = "THREAD_NOT_FOUND"
    
    # Server errors (5xx equivalent)
    INTERNAL_ERROR = "INTERNAL_ERROR"
    LLM_ERROR = "LLM_ERROR"
    RATE_LIMIT_ERROR = "RATE_LIMIT_ERROR"
    TIMEOUT_ERROR = "TIMEOUT_ERROR"
    
    # Processing errors
    PROMPT_FAILED = "PROMPT_FAILED"
    JSON_PARSE_ERROR = "JSON_PARSE_ERROR"
    PROCESSING_ERROR = "PROCESSING_ERROR"
    GRAPH_EXECUTION_ERROR = "GRAPH_EXECUTION_ERROR"
