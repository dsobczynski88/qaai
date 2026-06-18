"""
Pytest-specific logging configuration for pyjama.

This module provides utilities to initialize and manage logging for pytest sessions,
ensuring a single pyjama.log file is created per test run in a customizable location.
All downstream components (PyJamaTraceMatrix, etc.) will reuse this shared logger.

Usage:
    # In conftest.py or pytest hook
    from pyjama.utils.pytest_log_config import init_pytest_logging
    init_pytest_logging()  # Uses default or PYJAMA_TEST_LOG_PATH env var

    # In tests or downstream packages
    from pyjama.utils.pytest_log_config import get_pytest_logger, get_pytest_log_dir
    logger = get_pytest_logger()
    log_dir = get_pytest_log_dir()
    
    # Pass to PyJamaTraceMatrix to share the same log file
    from pyjama.utils.pytest_log_config import get_pytest_logger, get_pytest_log_dir
    api = PyJamaTraceMatrix(
        jama_client,
        data_path="./data",
        log_dir=str(get_pytest_log_dir()),
        logger=get_pytest_logger(),
    )
"""

import os
import logging
from pathlib import Path
from typing import Optional

from pyjama.utils.proj_log import ProjectLogger
from pyjama.utils import gen_utils


# Module-level state for the pytest session logger
_pytest_session_project_logger: Optional[ProjectLogger] = None
_pytest_session_logger: Optional[logging.Logger] = None
_pytest_log_dir: Optional[Path] = None

# Module-level state for cache and file settings
_pytest_cache_mode: Optional[str] = None
_pytest_inputs_file_name: str = "pyjama_inputs.jsonl"
_pytest_outputs_file_name: str = "pyjama_outputs.jsonl"


def init_pytest_logging(log_base_path: Optional[str] = None) -> ProjectLogger:
    """
    Initialize pytest session logging.

    Creates a single pyjama.log file in a run-specific directory under the base path.
    This function should be called once per pytest session (typically from pytest_configure hook).
    All downstream components (PyJamaTraceMatrix, etc.) will reuse this shared logger.

    Args:
        log_base_path: Base path for test logs. If None, checks PYJAMA_TEST_LOG_PATH
                      environment variable or defaults to "logs/tests".
                      Example: "/path/to/logs/tests" → creates
                      "/path/to/logs/tests/run-2024-01-15-14-30-45/pyjama.log"

    Returns:
        ProjectLogger: The configured session logger instance.

    Raises:
        IOError: If unable to create the log directory or file.

    Examples:
        >>> # Default behavior
        >>> logger = init_pytest_logging()
        >>> # Logs to: logs/tests/run-{timestamp}/pyjama.log

        >>> # Custom path
        >>> logger = init_pytest_logging("/custom/test/logs")
        >>> # Logs to: /custom/test/logs/run-{timestamp}/pyjama.log

        >>> # Via environment variable
        >>> os.environ["PYJAMA_TEST_LOG_PATH"] = "/env/log/path"
        >>> logger = init_pytest_logging()
        >>> # Logs to: /env/log/path/run-{timestamp}/pyjama.log
    """
    global _pytest_session_project_logger, _pytest_session_logger, _pytest_log_dir

    # If already initialized, return existing logger
    if _pytest_session_project_logger is not None:
        return _pytest_session_project_logger

    # Determine base log path
    if log_base_path is None:
        log_base_path = os.getenv("PYJAMA_TEST_LOG_PATH", "logs/tests")

    # Normalize and create run directory
    log_base_path = str(Path(log_base_path).resolve())
    run_dir = gen_utils.make_output_directory(log_base_path)
    _pytest_log_dir = Path(run_dir)

    # Create the pyjama.log file path
    log_file_path = str(_pytest_log_dir / "pyjama.log")

    # Initialize and configure the session logger with name "pyjama"
    # This same name will be used by PyJamaTraceMatrix, so all logs go to one file
    _pytest_session_project_logger = ProjectLogger("pyjama", log_file_path)
    _pytest_session_project_logger.config()
    _pytest_session_logger = _pytest_session_project_logger.get_logger()

    # Log initialization info
    _pytest_session_logger.info(f"Pytest session logging initialized")
    _pytest_session_logger.info(f"Log directory: {run_dir}")
    _pytest_session_logger.info(f"Log file: {log_file_path}")

    return _pytest_session_project_logger


def get_pytest_logger() -> logging.Logger:
    """
    Get the configured pytest session logger.

    Must be called after init_pytest_logging() has been invoked.

    Returns:
        logging.Logger: The configured logger instance.

    Raises:
        RuntimeError: If logging has not been initialized yet.

    Examples:
        >>> logger = get_pytest_logger()
        >>> logger.info("Test message")
    """
    if _pytest_session_logger is None:
        raise RuntimeError(
            "Pytest logging not initialized. Call init_pytest_logging() first."
        )
    return _pytest_session_logger


def get_pytest_project_logger() -> ProjectLogger:
    """
    Get the configured pytest session ProjectLogger instance.

    Must be called after init_pytest_logging() has been invoked.
    This gives direct access to the ProjectLogger wrapper, useful for passing
    to downstream components like PyJamaTraceMatrix that need to reuse the same logger.

    Returns:
        ProjectLogger: The configured ProjectLogger instance.

    Raises:
        RuntimeError: If logging has not been initialized yet.

    Examples:
        >>> from pyjama.utils.pytest_log_config import get_pytest_project_logger
        >>> project_logger = get_pytest_project_logger()
        >>> # Pass to PyJamaTraceMatrix:
        >>> api = PyJamaTraceMatrix(
        ...     jama_client,
        ...     data_path="./data",
        ...     logger=project_logger.get_logger()
        ... )
    """
    if _pytest_session_project_logger is None:
        raise RuntimeError(
            "Pytest logging not initialized. Call init_pytest_logging() first."
        )
    return _pytest_session_project_logger


def get_pytest_log_dir() -> Path:
    """
    Get the pytest session log directory.

    Must be called after init_pytest_logging() has been invoked.

    Returns:
        Path: The run-specific log directory (e.g., logs/tests/run-2024-01-15-14-30-45/).

    Raises:
        RuntimeError: If logging has not been initialized yet.

    Examples:
        >>> log_dir = get_pytest_log_dir()
        >>> print(log_dir)
        PosixPath('/absolute/path/to/logs/tests/run-2024-01-15-14-30-45')

        >>> # Write custom data to the log directory
        >>> (log_dir / "custom_data.json").write_text("{}")
    """
    if _pytest_log_dir is None:
        raise RuntimeError(
            "Pytest logging not initialized. Call init_pytest_logging() first."
        )
    return _pytest_log_dir


def reset_pytest_logging() -> None:
    """
    Reset pytest logging state.

    Useful for testing or resetting between multiple pytest runs in the same process.
    After calling this, init_pytest_logging() can be called again to create a new session.

    Note:
        This does NOT close handlers or clean up resources. For proper cleanup,
        use pytest's built-in session teardown or call get_pytest_logger().handlers
        directly if needed.
    """
    global _pytest_session_project_logger, _pytest_session_logger, _pytest_log_dir
    _pytest_session_project_logger = None
    _pytest_session_logger = None
    _pytest_log_dir = None


# ============================================================================
# Cache and File Settings for PyJamaTraceMatrix
# ============================================================================

def set_pytest_cache_mode(cache_mode: str) -> None:
    """
    Set the cache mode for pytest session (before PyJamaTraceMatrix instantiation).
    
    Args:
        cache_mode: Cache mode as string ("off", "use", "refresh")
    """
    global _pytest_cache_mode
    _pytest_cache_mode = cache_mode


def get_pytest_cache_mode() -> Optional[str]:
    """
    Get the cache mode configured for pytest session.
    
    Returns:
        Cache mode string or None if not set
    """
    return _pytest_cache_mode


def set_pytest_input_file_name(file_name: str) -> None:
    """
    Set the input JSONL file name for pytest session.
    
    Args:
        file_name: Name of input file (default: "pyjama_inputs.jsonl")
    """
    global _pytest_inputs_file_name
    _pytest_inputs_file_name = file_name


def get_pytest_input_file_name() -> str:
    """
    Get the input JSONL file name for pytest session.
    
    Returns:
        File name string
    """
    return _pytest_inputs_file_name


def set_pytest_output_file_name(file_name: str) -> None:
    """
    Set the output JSONL file name for pytest session.
    
    Args:
        file_name: Name of output file (default: "pyjama_outputs.jsonl")
    """
    global _pytest_outputs_file_name
    _pytest_outputs_file_name = file_name


def get_pytest_output_file_name() -> str:
    """
    Get the output JSONL file name for pytest session.
    
    Returns:
        File name string
    """
    return _pytest_outputs_file_name
