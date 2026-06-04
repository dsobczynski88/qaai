"""
Centralized logging configuration for FastAPI application.

Sets up three separate log files in a timestamped run directory:
- api.log: FastAPI/Uvicorn logs (requests, middleware, health checks)
- autoqa.log: AutoQA application logs (services, reviews, cache, telemetry)
- pyjama.log: PyJama/JAMA integration logs
"""

import sys
import logging
import os
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

# US Central Time zone (handles both CST and CDT automatically)
US_CENTRAL = ZoneInfo("America/Chicago")


class CTFormatter(logging.Formatter):
    """Custom formatter that uses US Central Time for all log timestamps."""
    
    def formatTime(self, record, datefmt=None):
        """Override formatTime to use US Central Time instead of local/UTC."""
        dt = datetime.fromtimestamp(record.created, tz=US_CENTRAL)
        if datefmt:
            return dt.strftime(datefmt)
        else:
            # Default format: '2026-05-07 13:47:06,639' (CT)
            return dt.strftime('%Y-%m-%d %H:%M:%S,%f')[:-3]


def create_timestamped_run_directory(base_logs_dir: str = "./logs") -> Path:
    """Create a timestamped run directory for this session's logs.
    
    Args:
        base_logs_dir: Base directory for all logs (default: "./logs")
        
    Returns:
        Path object pointing to the run directory (e.g., "./logs/run-2024-01-15_14-30-45/")
    """
    # Get current time in Central Time
    now = datetime.now(tz=US_CENTRAL)
    timestamp = now.strftime("%Y-%m-%d_%H-%M-%S")
    run_dir = Path(base_logs_dir) / f"run-{timestamp}"
    
    # Create directory if it doesn't exist
    run_dir.mkdir(parents=True, exist_ok=True)
    
    return run_dir


def setup_logging(run_dir: Path) -> None:
    """Configure logging for the FastAPI application.
    
    Sets up three loggers with separate file handlers:
    - 'autoqa.api': Routes to api.log
    - 'autoqa': Routes to autoqa.log (and child loggers like autoqa.core, autoqa.components)
    - 'pyjama': Routes to pyjama.log
    
    Args:
        run_dir: Path to the timestamped run directory
    """
    # Ensure UTF-8 on Windows
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    
    # Create formatters
    file_format = CTFormatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    console_format = logging.Formatter('%(name)s - %(levelname)s - %(message)s')
    
    # =========================================================================
    # API Logger: autoqa.api.* (routes, middleware, services)
    # =========================================================================
    api_logger = logging.getLogger("autoqa.api")
    api_logger.setLevel(logging.DEBUG)
    api_logger.propagate = False  # Don't propagate to root
    
    api_file_handler = logging.FileHandler(
        run_dir / "api.log",
        encoding="utf-8"
    )
    api_file_handler.setLevel(logging.DEBUG)
    api_file_handler.setFormatter(file_format)
    api_logger.addHandler(api_file_handler)
    
    api_console_handler = logging.StreamHandler(sys.stdout)
    api_console_handler.setLevel(logging.DEBUG)
    api_console_handler.setFormatter(console_format)
    api_logger.addHandler(api_console_handler)
    
    # =========================================================================
    # AutoQA Logger: autoqa.* (except autoqa.api.*)
    # =========================================================================
    autoqa_logger = logging.getLogger("autoqa")
    autoqa_logger.setLevel(logging.DEBUG)
    autoqa_logger.propagate = False  # Don't propagate to root
    
    autoqa_file_handler = logging.FileHandler(
        run_dir / "autoqa.log",
        encoding="utf-8"
    )
    autoqa_file_handler.setLevel(logging.DEBUG)
    autoqa_file_handler.setFormatter(file_format)
    autoqa_logger.addHandler(autoqa_file_handler)
    
    autoqa_console_handler = logging.StreamHandler(sys.stdout)
    autoqa_console_handler.setLevel(logging.DEBUG)
    autoqa_console_handler.setFormatter(console_format)
    autoqa_logger.addHandler(autoqa_console_handler)
    
    # =========================================================================
    # PyJama Logger: pyjama.* (JAMA integration)
    # =========================================================================
    pyjama_logger = logging.getLogger("pyjama")
    pyjama_logger.setLevel(logging.DEBUG)
    pyjama_logger.propagate = False  # Don't propagate to root
    
    pyjama_file_handler = logging.FileHandler(
        run_dir / "pyjama.log",
        encoding="utf-8"
    )
    pyjama_file_handler.setLevel(logging.DEBUG)
    pyjama_file_handler.setFormatter(file_format)
    pyjama_logger.addHandler(pyjama_file_handler)
    
    pyjama_console_handler = logging.StreamHandler(sys.stdout)
    pyjama_console_handler.setLevel(logging.DEBUG)
    pyjama_console_handler.setFormatter(console_format)
    pyjama_logger.addHandler(pyjama_console_handler)
    
    # =========================================================================
    # Uvicorn Logger: uvicorn.* (FastAPI server logs)
    # =========================================================================
    uvicorn_logger = logging.getLogger("uvicorn")
    uvicorn_logger.setLevel(logging.DEBUG)
    uvicorn_logger.propagate = False
    
    uvicorn_file_handler = logging.FileHandler(
        run_dir / "api.log",
        encoding="utf-8"
    )
    uvicorn_file_handler.setLevel(logging.DEBUG)
    uvicorn_file_handler.setFormatter(file_format)
    uvicorn_logger.addHandler(uvicorn_file_handler)
    
    uvicorn_console_handler = logging.StreamHandler(sys.stdout)
    uvicorn_console_handler.setLevel(logging.DEBUG)
    uvicorn_console_handler.setFormatter(console_format)
    uvicorn_logger.addHandler(uvicorn_console_handler)
    
    # Also capture uvicorn.access logs to api.log
    uvicorn_access_logger = logging.getLogger("uvicorn.access")
    uvicorn_access_logger.setLevel(logging.DEBUG)
    uvicorn_access_logger.propagate = False
    
    uvicorn_access_file_handler = logging.FileHandler(
        run_dir / "api.log",
        encoding="utf-8"
    )
    uvicorn_access_file_handler.setLevel(logging.DEBUG)
    uvicorn_access_file_handler.setFormatter(file_format)
    uvicorn_access_logger.addHandler(uvicorn_access_file_handler)
    
    uvicorn_access_console_handler = logging.StreamHandler(sys.stdout)
    uvicorn_access_console_handler.setLevel(logging.DEBUG)
    uvicorn_access_console_handler.setFormatter(console_format)
    uvicorn_access_logger.addHandler(uvicorn_access_console_handler)
    
    # =========================================================================
    # Log startup info
    # =========================================================================
    startup_logger = logging.getLogger("autoqa.api.main")
    startup_logger.info("=" * 80)
    startup_logger.info("AutoQA API Startup - Logging Initialized")
    startup_logger.info("=" * 80)
    startup_logger.info("Run directory: %s", run_dir)
    startup_logger.info("Log files:")
    startup_logger.info("  - API logs: %s/api.log", run_dir)
    startup_logger.info("  - AutoQA logs: %s/autoqa.log", run_dir)
    startup_logger.info("  - PyJama logs: %s/pyjama.log", run_dir)
    startup_logger.info("=" * 80)
