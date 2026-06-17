"""
FastAPI application entry point with optimized timeout configuration.

This script starts the Uvicorn server with increased timeout values to prevent
504 Gateway Timeout errors during long-running batch processing operations.

Timeout Configuration:
  - timeout_keep_alive: 600s (10 min) - Keep-alive timeout for persistent connections
  - timeout_notify: 600s (10 min) - Grace period for graceful shutdown
  - timeout_handle: 600s (10 min) - Handler timeout for processing requests

These values support batch processing with 50+ requirements/test cases that may
take 5-10 minutes to complete.

IMPORTANT: This script uses subprocess to invoke uvicorn as a CLI command rather
than importing it directly. This avoids asyncio conflicts when running in Jupyter
environments where nest-asyncio patches the asyncio module.

Usage:
    python qaai/api/run.py
    # OR
    uvicorn qaai.api.main:app --host 0.0.0.0 --port 8000
"""

import logging
import os
import sys
import subprocess
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

# Setup logging for the launcher script
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("qaai.api.run")


def main():
    """Main entry point for the QAAI API server.
    
    Launches uvicorn as a subprocess to avoid asyncio conflicts with Jupyter's
    nest-asyncio patches.
    """
    logger.info("=" * 80)
    logger.info("QAAI API Server Launcher")
    logger.info("=" * 80)
    logger.info("Timeout Settings:")
    logger.info("  - timeout_keep_alive: 600s (10 minutes)")
    logger.info("=" * 80)
    
    # Get configuration from environment variables with sensible defaults
    host = os.getenv("QAAI_API_HOST", "0.0.0.0")
    port = int(os.getenv("QAAI_API_PORT", "8000"))
    reload = os.getenv("QAAI_API_RELOAD", "false").lower() == "true"
    workers = int(os.getenv("QAAI_API_WORKERS", "1"))
    
    logger.info("Server Configuration:")
    logger.info("  - Host: %s", host)
    logger.info("  - Port: %d", port)
    logger.info("  - Reload: %s", reload)
    logger.info("  - Workers: %d", workers)
    logger.info("=" * 80)
    
    # Build uvicorn command with CLI arguments
    # Using uvicorn as a subprocess ensures a clean asyncio event loop
    # not affected by Jupyter's nest-asyncio patches
    uvicorn_cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "qaai.api.main:app",
        "--host",
        host,
        "--port",
        str(port),
        "--timeout-keep-alive",
        "600",  # 10 minutes
    ]
    
    # Add reload flag if enabled
    if reload:
        uvicorn_cmd.append("--reload")
    
    # Add workers if > 1
    if workers > 1:
        uvicorn_cmd.extend(["--workers", str(workers)])
    
    logger.info("Launching uvicorn with command:")
    logger.info(" ".join(uvicorn_cmd))
    logger.info("=" * 80)
    logger.info("")
    
    try:
        # Run uvicorn in a subprocess - this provides a clean environment
        # unaffected by Jupyter's asyncio patches
        process = subprocess.Popen(uvicorn_cmd)
        
        # Wait for the process to complete
        # This will block until uvicorn is stopped (e.g., Ctrl+C)
        process.wait()
    except KeyboardInterrupt:
        logger.info("Received interrupt signal, shutting down...")
        process.terminate()
        process.wait(timeout=5)
    except Exception as e:
        logger.error("Failed to start uvicorn: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
