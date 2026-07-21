"""
Centralized logging configuration for FastAPI application.

Sets up three separate log files in a timestamped run directory:
- api.log: FastAPI/Uvicorn logs (requests, middleware, health checks)
- qaai.log: QAAI application logs (services, reviews, cache, telemetry)
- pyjama.log: PyJama/JAMA integration logs

Concurrency model: reviews run concurrently (JobManager no longer serializes
them), so per-run log routing MUST NOT depend on mutating a single shared set of
FileHandlers. Instead the destination run folder is carried in the
``current_run_dir`` ContextVar; a ``RunRoutingHandler`` installed once at boot
resolves it at emit time and writes to a per-run FileHandler. asyncio copies the
context into every task/Send a review spawns, so each review's logs land in its
own folder with no lock and no cross-routing.
"""

import sys
import logging
import os
import threading
import uuid
from collections import OrderedDict
from contextvars import ContextVar
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

# US Central Time zone (handles both CST and CDT automatically)
US_CENTRAL = ZoneInfo("America/Chicago")

# The active review's run directory, carried in the async context. Set by
# start_new_run() at the top of each review; read by RunRoutingHandler (logging),
# TokenUsageTracker (telemetry), and ReviewCacheManager (run-scoped cache purge).
# Default None ⇒ boot/CLI/test contexts with no active run.
current_run_dir: ContextVar[Optional[Path]] = ContextVar(
    "qaai_current_run_dir", default=None
)


def get_current_run_dir() -> Optional[Path]:
    """Return the run directory bound to the current async context, or None."""
    return current_run_dir.get()


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

    The folder name carries a short uuid suffix in addition to the second-
    resolution timestamp: with reviews now running concurrently, two starting in
    the same second would otherwise resolve to the same ``run-<ts>`` folder
    (``mkdir(exist_ok=True)``) and clobber each other's inputs/outputs/viewer.
    The resulting ``run_dir.name`` (e.g. ``run-2024-01-15_14-30-45-1a2b3c4d``)
    doubles as the unique run id used to scope cache-purge to a single run.

    Args:
        base_logs_dir: Base directory for all logs (default: "./logs")

    Returns:
        Path object pointing to the run directory
        (e.g., "./logs/run-2024-01-15_14-30-45-1a2b3c4d/")
    """
    # Get current time in Central Time
    now = datetime.now(tz=US_CENTRAL)
    timestamp = now.strftime("%Y-%m-%d_%H-%M-%S")
    run_dir = Path(base_logs_dir) / f"run-{timestamp}-{uuid.uuid4().hex[:8]}"

    # Create directory if it doesn't exist
    run_dir.mkdir(parents=True, exist_ok=True)

    return run_dir


# Loggers this module owns. setup_logging() detaches and re-attaches handlers
# for exactly these names so it is safe to call repeatedly (once at startup and
# again at the start of every review request) without duplicating handlers.
# "projectlog.pyjama_api" is the pyjama package's real logger name — QAAI owns
# its single pyjama.log FileHandler (the data_integration boundary shim stops
# pyjama from attaching its own / creating a second run folder).
_MANAGED_LOGGERS = ("qaai.api", "qaai", "projectlog.pyjama_api", "uvicorn", "uvicorn.access")


def _reset_managed_handlers() -> None:
    """Detach and close existing handlers on the managed loggers.

    Called at the top of setup_logging() so re-pointing the file handlers at a
    new run directory does not duplicate handlers (which would double-write every
    line) and properly closes the previous run's open file handles.
    """
    for name in _MANAGED_LOGGERS:
        logger = logging.getLogger(name)
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass


# Managed-logger → log-file basename mapping. Uvicorn/access lines join api.log,
# matching the original setup_logging() layout.
_LOGGER_FILE_MAP: Dict[str, str] = {
    "qaai.api": "api.log",
    "qaai": "qaai.log",
    "projectlog.pyjama_api": "pyjama.log",
    "uvicorn": "api.log",
    "uvicorn.access": "api.log",
}

# The RunRoutingHandler instances installed at boot, so finalize_run() can close a
# finished run's per-run FileHandlers across every managed logger.
_ROUTING_HANDLERS: List["RunRoutingHandler"] = []


class RunRoutingHandler(logging.Handler):
    """Route each record to a per-run FileHandler chosen at emit time.

    One instance is attached to each managed logger by ``install_run_routing()``
    (once, at boot). On ``emit`` it resolves the destination run directory from
    the ``current_run_dir`` contextvar and lazily opens+caches a ``FileHandler``
    for that directory's ``<filename>``. This replaces the old per-review global
    handler swap: because the destination comes from the async context, concurrent
    reviews write to isolated log files with no lock and no cross-routing.

    When no run directory can be resolved (boot/lifespan logs before any review)
    the record is dropped on the file side — the sibling console handler still
    prints it, matching the previous boot-to-stdout behaviour.

    Open file descriptors are bounded two ways: ``close_run(run_dir)`` (called via
    ``finalize_run`` at the end of a review) evicts a finished run eagerly, and an
    LRU cap self-evicts the least-recently-used run's FileHandler so a crashed or
    cancelled review that never calls finalize can't leak fds without limit.
    """

    # Max concurrently-open per-run FileHandlers before the least-recently-used is
    # closed. Comfortably above the ~10 concurrent-review target so active runs are
    # never evicted mid-flight; a re-emit after eviction simply reopens the file
    # (append mode), so eviction is safe, only slightly less efficient.
    _MAX_OPEN_HANDLERS = 64

    def __init__(self, filename: str, level: int = logging.DEBUG) -> None:
        super().__init__(level)
        self._filename = filename
        self._handlers: "OrderedDict[Path, logging.FileHandler]" = OrderedDict()
        self._lock = threading.Lock()
        self.setFormatter(
            CTFormatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        )

    def _resolve_run_dir(self) -> Optional[Path]:
        run_dir = current_run_dir.get()
        if run_dir is not None:
            return run_dir
        # Fallback for contexts the contextvar can't reach (integration tests and
        # CLI scripts that call start_new_run() outside the request task). It is a
        # best-effort shared pointer only: concurrent reviews each read the
        # contextvar, so a racing overwrite of this value can never cross-route a
        # live review's logs. Restricted to a real ``run-*`` folder so boot logs
        # (default ./logs/qaai.log) still go to stdout only.
        try:
            from qaai.core.config import settings

            parent = Path(settings.log_file_path).parent
            if parent.name.startswith("run-") and parent.exists():
                return parent
        except Exception:
            pass
        return None

    def _handler_for(self, run_dir: Path) -> logging.FileHandler:
        with self._lock:
            handler = self._handlers.get(run_dir)
            if handler is not None:
                self._handlers.move_to_end(run_dir)  # mark most-recently-used
                return handler
            handler = logging.FileHandler(run_dir / self._filename, encoding="utf-8")
            handler.setFormatter(self.formatter)
            self._handlers[run_dir] = handler
            # LRU-evict the oldest open handler if over the cap (bounds fds even
            # when a run never calls finalize_run, e.g. after a crash/cancel).
            while len(self._handlers) > self._MAX_OPEN_HANDLERS:
                _old_dir, old_handler = self._handlers.popitem(last=False)
                try:
                    old_handler.close()
                except Exception:
                    pass
            return handler

    def emit(self, record: logging.LogRecord) -> None:
        run_dir = self._resolve_run_dir()
        if run_dir is None:
            return
        try:
            self._handler_for(run_dir).emit(record)
        except Exception:
            self.handleError(record)

    def close_run(self, run_dir: Path) -> None:
        """Close and drop the cached FileHandler for ``run_dir`` (fd hygiene)."""
        with self._lock:
            handler = self._handlers.pop(run_dir, None)
        if handler is not None:
            try:
                handler.close()
            except Exception:
                pass

    def close(self) -> None:
        with self._lock:
            handlers = list(self._handlers.values())
            self._handlers.clear()
        for handler in handlers:
            try:
                handler.close()
            except Exception:
                pass
        super().close()


def install_run_routing() -> None:
    """Attach console + per-run routing handlers to the managed loggers, ONCE.

    Called at process boot (create_app). Replaces the old model where every review
    called ``setup_logging()`` to swap a single shared set of FileHandlers to a new
    folder — unsafe once reviews run concurrently. Here each managed logger gets a
    stdout ``StreamHandler`` plus one ``RunRoutingHandler`` that resolves the run
    folder per-record from the contextvar, so no global mutation happens per review.

    We deliberately do NOT create a run folder at boot: the first ``logs/run-<ts>/``
    is created by the first review (``start_new_run``); until then boot/lifespan
    logs go to stdout (captured by the process manager / container runtime).

    Idempotent: existing handlers on the managed loggers are detached first.
    """
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    _reset_managed_handlers()
    _ROUTING_HANDLERS.clear()
    console_format = logging.Formatter('%(name)s - %(levelname)s - %(message)s')
    for name in _MANAGED_LOGGERS:
        logger = logging.getLogger(name)
        logger.setLevel(logging.DEBUG)
        logger.propagate = False

        console = logging.StreamHandler(sys.stdout)
        console.setLevel(logging.DEBUG)
        console.setFormatter(console_format)
        logger.addHandler(console)

        routing = RunRoutingHandler(_LOGGER_FILE_MAP[name])
        routing.setLevel(logging.DEBUG)
        logger.addHandler(routing)
        _ROUTING_HANDLERS.append(routing)


def bootstrap_console_logging() -> None:
    """Backwards-compatible boot hook — installs console + run-routing handlers.

    Retained so existing callers (``main.create_app``, the MLflow eval CLI) keep
    working; delegates to ``install_run_routing()``. The name is kept because
    those callers import it by this name.
    """
    install_run_routing()


def finalize_run(run_dir: Path) -> None:
    """Close the per-run FileHandlers opened for ``run_dir`` on every managed
    logger, bounding open file descriptors as runs accumulate. Correctness of
    routing does not depend on this (nor on resetting the contextvar — each review
    runs in its own task/context); it is purely fd hygiene at the end of a run.
    """
    for handler in _ROUTING_HANDLERS:
        handler.close_run(run_dir)


def start_new_run(base_logs_dir: str | None = None) -> Path:
    """Begin a fresh run: create a unique run directory and bind it to this context.

    Called at the start of every review (and once per integration-test session),
    so each run gets its own ``logs/run-<ts>-<uuid>/`` holding that run's
    ``api.log`` / ``qaai.log`` / ``pyjama.log`` alongside its ``inputs.jsonl``,
    ``outputs.jsonl``, viewer, graph png and ``token_usage.jsonl``.

    Routing is per-async-context: this sets the ``current_run_dir`` contextvar,
    which asyncio copies into every task/Send the review spawns, so the routing
    log handler and the telemetry tracker land in this folder with NO global lock.
    ``settings.log_file_path`` is still updated as a best-effort shared fallback
    for contexts the contextvar can't reach (tests / CLI); concurrent reviews never
    read it, so a racing overwrite cannot cross-route a live review.

    Args:
        base_logs_dir: Base directory for all run folders. Defaults to
            ``settings.log_base_dir`` (``./logs`` in production, ``./logs/tests``
            under the test harness).

    Returns:
        Path to the new run directory.
    """
    from qaai.core.config import settings

    if base_logs_dir is None:
        base_logs_dir = settings.log_base_dir

    run_dir = create_timestamped_run_directory(base_logs_dir)

    # Bind the run folder to this async context (primary routing mechanism).
    current_run_dir.set(run_dir)

    # Best-effort shared fallback (see docstring) + truncate this run's telemetry
    # file so it starts empty. telemetry_file_path derives from log_file_path.
    settings.log_file_path = str(run_dir / settings.log_file_name)
    Path(settings.telemetry_file_path).write_text("", encoding="utf-8")

    logger = logging.getLogger("qaai.api.main")
    logger.info("Run directory: %s", run_dir)
    return run_dir
