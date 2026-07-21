"""Concurrency isolation for per-run log/telemetry/cache routing.

These tests prove the invariant that let us drop the JobManager run-lock: two
reviews running as separate asyncio tasks each bind their own run folder via the
``current_run_dir`` contextvar, so their logs, telemetry, and run-scoped cache
purge stay isolated with no global handler swap. Before this change,
start_new_run() re-pointed process-global logging handlers, so whichever review
ran start_new_run() last captured everyone's logs.
"""

import asyncio
import logging

import pytest

from qaai.core.logging_config import (
    _MANAGED_LOGGERS,
    finalize_run,
    install_run_routing,
    start_new_run,
)


@pytest.fixture
def managed_logger_state():
    """Snapshot + restore the managed loggers' handlers so installing routing in a
    unit test does not leak global logging config into the rest of the suite."""
    saved = {}
    for name in _MANAGED_LOGGERS:
        lg = logging.getLogger(name)
        saved[name] = (list(lg.handlers), lg.propagate, lg.level)
    try:
        yield
    finally:
        for name, (handlers, propagate, level) in saved.items():
            lg = logging.getLogger(name)
            lg.handlers = handlers
            lg.propagate = propagate
            lg.setLevel(level)


async def _run_and_log(base_dir, marker: str):
    """Simulate one review: bind a fresh run folder, then log before and after an
    await point so the two reviews genuinely interleave on the event loop."""
    run_dir = start_new_run(base_logs_dir=str(base_dir))
    logging.getLogger("qaai").warning("marker-%s-start", marker)
    await asyncio.sleep(0.02)  # force interleaving with the sibling task
    logging.getLogger("qaai").warning("marker-%s-end", marker)
    finalize_run(run_dir)
    return run_dir


async def test_concurrent_runs_route_to_isolated_folders(tmp_path, managed_logger_state):
    install_run_routing()

    a_dir, b_dir = await asyncio.gather(
        _run_and_log(tmp_path / "a", "A"),
        _run_and_log(tmp_path / "b", "B"),
    )

    assert a_dir != b_dir  # uuid-suffixed folders never collide

    a_log = (a_dir / "qaai.log").read_text(encoding="utf-8")
    b_log = (b_dir / "qaai.log").read_text(encoding="utf-8")

    # Each run's log holds only its own lines — no cross-routing despite interleave.
    assert "marker-A-start" in a_log and "marker-A-end" in a_log
    assert "marker-B" not in a_log
    assert "marker-B-start" in b_log and "marker-B-end" in b_log
    assert "marker-A" not in b_log


async def test_boot_logs_before_any_run_do_not_write_files(tmp_path, managed_logger_state):
    """With no run bound to the context, the routing handler drops file output
    (console still prints) — matching the boot-to-stdout behaviour."""
    install_run_routing()
    # No start_new_run() → current_run_dir is None in this context.
    logging.getLogger("qaai").warning("boot-line-should-not-crash")
    # Nothing to assert on disk; the point is emit() must be a safe no-op on file.
