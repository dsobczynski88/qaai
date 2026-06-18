"""JobManager.cancel stops an in-flight job and marks it CANCELLED.

Pure unit test against the in-memory job registry — no LLM/graph involved; the
"work" is a coroutine that sleeps until cancelled.
"""
import asyncio

import pytest

from qaai.api.jobs import CANCELLED, COMPLETED, JobManager


async def _wait_until(predicate, timeout=2.0):
    """Poll predicate() each event-loop tick until true or timeout."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return False


async def test_cancel_running_job_marks_cancelled():
    mgr = JobManager()
    started = asyncio.Event()

    async def long_running(job):
        started.set()
        await asyncio.sleep(30)  # would never finish on its own
        return "never"

    job = mgr.submit(long_running, "x.html")
    assert await _wait_until(started.is_set), "job never started"

    assert mgr.cancel(job.job_id) is True
    assert await _wait_until(lambda: job.status == CANCELLED), "status not CANCELLED"
    assert job.error == "Run stopped by user."


async def test_cancel_unknown_job_returns_false():
    mgr = JobManager()
    assert mgr.cancel("does-not-exist") is False


async def test_cancel_completed_job_returns_false():
    mgr = JobManager()

    async def quick(job):
        return "done.html"

    job = mgr.submit(quick, "x.html")
    assert await _wait_until(lambda: job.status == COMPLETED), "job did not complete"
    # Task is already done -> nothing to cancel.
    assert mgr.cancel(job.job_id) is False
