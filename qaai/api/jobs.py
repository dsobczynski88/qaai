"""In-memory async job registry for long-running review pipelines.

Why this exists: a review can take several minutes. Returning the HTML report
synchronously means the upstream proxy (JupyterHub jupyter-server-proxy /
configurable-http-proxy, AWS ALB) sees an idle upstream for the whole run and
returns a 504 to the browser, even though uvicorn eventually completes with 200.

Instead, the POST endpoints submit a Job here and return 202 + job_id
immediately; the graph runs in the background via asyncio.create_task; the
frontend polls a fast status endpoint and downloads the report when ready. Every
HTTP request is now sub-second, so no proxy idle timeout can fire.

Scope / constraints:
- In-memory: assumes a SINGLE uvicorn worker (the current setup — qaai.api.run
  sets no --workers). If the app is ever run with multiple workers, this store
  must move to a shared backend (e.g. Redis, already a dependency) keyed by
  job_id, because each worker would otherwise hold its own dict.
- Reviews run CONCURRENTLY: there is no longer a global run-lock. It used to exist
  only to protect the per-run-folder logging invariant (start_new_run() re-pointed
  process-global logging handlers + settings.log_file_path, so two reviews would
  clobber each other's routing). That invariant is gone — per-run log/telemetry
  routing and cache-purge scoping are now carried in the ``current_run_dir``
  contextvar (qaai/core/logging_config.py), which asyncio copies per task, so each
  review is isolated without serialization. See start_new_run() for details.
"""

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Dict, Optional

logger = logging.getLogger("qaai.api.jobs")

# Terminal + non-terminal job states.
PENDING = "pending"
RUNNING = "running"
COMPLETED = "completed"
FAILED = "failed"
CANCELLED = "cancelled"

# Bound memory: keep at most this many jobs, evicting the oldest finished ones.
_MAX_JOBS = 200


@dataclass
class Job:
    """A single background review run."""

    job_id: str
    filename: str
    status: str = PENDING
    result_path: Optional[str] = None
    error: Optional[str] = None
    # HTTP status the result endpoint should surface on failure (400 for a
    # ValueError = bad input, 500 otherwise), mirroring the old inline handling.
    error_status: int = 500
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    # ── Per-run progress (driven by _run_batch_review via begin/record_item) ──
    # total stays 0 until the service has fetched/parsed its items and called
    # begin(); the frontend treats total==0 as "detecting items…".
    total: int = 0
    done: int = 0
    succeeded: int = 0
    failed: int = 0
    # Wall-clock start of actual item processing (after any queue wait), used for
    # the ETA. None until begin() is called.
    progress_started_at: Optional[float] = None
    # Problem-only log: one {item_id, level, text} dict per errored / incomplete /
    # missing-input item. Surfaced live in the UI and echoed in the report viewer.
    messages: list = field(default_factory=list)

    def begin(self, total: int) -> None:
        """Mark the start of item processing once the total count is known."""
        self.total = total
        self.progress_started_at = time.time()
        self.updated_at = time.time()

    def add_message(self, entry: dict) -> None:
        """Append a problem note (``{item_id, level, text}``) to the run log.
        Kept separate from :meth:`record_item` so one item can emit several notes
        (e.g. a missing-input warning plus an incomplete-output warning) while the
        progress bar still advances exactly one step."""
        self.messages.append(entry)
        self.updated_at = time.time()

    def record_item(self, *, ok: bool) -> None:
        """Advance the progress bar by one finished item and tally ok/failed."""
        self.done += 1
        if ok:
            self.succeeded += 1
        else:
            self.failed += 1
        self.updated_at = time.time()

    def _eta_seconds(self) -> Optional[float]:
        """Estimate remaining seconds from the mean time per finished item.
        None until at least one item is done (no basis to extrapolate)."""
        if not self.done or self.progress_started_at is None or self.done >= self.total:
            return None
        elapsed = time.time() - self.progress_started_at
        return (elapsed / self.done) * (self.total - self.done)

    def to_status_dict(self) -> Dict[str, object]:
        """Public status payload for GET /jobs/{id} (no internal paths)."""
        return {
            "job_id": self.job_id,
            "status": self.status,
            "filename": self.filename,
            "error": self.error,
            "total": self.total,
            "done": self.done,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "eta_seconds": self._eta_seconds(),
            "messages": self.messages,
        }


# A factory that, given the assigned Job, returns the awaitable doing the actual
# work. We take a factory (not a bare coroutine) so the coroutine is created
# inside the task, after the serialization lock is held. It receives the whole
# Job so the service can report progress onto it (and still read job.job_id,
# which the service methods use as their thread_id prefix).
CoroFactory = Callable[["Job"], Awaitable[str]]


class JobManager:
    """Registry of background review jobs (single-process, in-memory)."""

    def __init__(self) -> None:
        self._jobs: Dict[str, Job] = {}
        self._tasks: Dict[str, asyncio.Task] = {}

    def submit(self, coro_factory: CoroFactory, filename: str) -> Job:
        """Register a job and schedule it on the event loop. Returns immediately."""
        job_id = uuid.uuid4().hex
        job = Job(job_id=job_id, filename=filename)
        self._jobs[job_id] = job
        self._evict_if_needed()

        # Keep a reference to the task so it isn't garbage-collected mid-flight.
        task = asyncio.create_task(self._run(job, coro_factory))
        self._tasks[job_id] = task
        task.add_done_callback(lambda _t, jid=job_id: self._tasks.pop(jid, None))

        logger.info("Job %s submitted (%s)", job_id, filename)
        return job

    def get(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id)

    def cancel(self, job_id: str) -> bool:
        """Request cancellation of a running/pending job. Returns True if a live
        task was cancelled. The task surfaces asyncio.CancelledError in _run,
        which marks the job CANCELLED."""
        task = self._tasks.get(job_id)
        if task is None or task.done():
            return False
        task.cancel()
        logger.info("Job %s cancellation requested", job_id)
        return True

    async def _run(self, job: Job, coro_factory: CoroFactory) -> None:
        # No run-lock: reviews execute concurrently. Each review binds its own run
        # folder via the current_run_dir contextvar (start_new_run), so their
        # logs/telemetry/cache stay isolated without serialization.
        self._set(job, RUNNING)
        logger.info("Job %s running", job.job_id)
        try:
            result_path = await coro_factory(job)
        except asyncio.CancelledError:
            # User asked to stop the run (cancel & discard). Record a terminal
            # state and swallow — no partial report is kept.
            self._fail(job, "Run stopped by user.", 499)
            self._set(job, CANCELLED)
            logger.info("Job %s cancelled", job.job_id)
            return
        except ValueError as exc:
            # Bad input (e.g. unknown baseline) — surface the detail as a 400.
            self._fail(job, str(exc), 400)
            logger.warning("Job %s failed (bad request): %s", job.job_id, exc)
        except Exception as exc:  # noqa: BLE001 — log full, surface generic
            self._fail(
                job,
                f"An internal error occurred (job_id: {job.job_id})",
                500,
            )
            logger.error("Job %s failed: %s", job.job_id, exc, exc_info=True)
        else:
            job.result_path = result_path
            self._set(job, COMPLETED)
            logger.info("Job %s completed -> %s", job.job_id, result_path)

    def _set(self, job: Job, status: str) -> None:
        job.status = status
        job.updated_at = time.time()

    def _fail(self, job: Job, message: str, error_status: int) -> None:
        job.error = message
        job.error_status = error_status
        self._set(job, FAILED)

    def _evict_if_needed(self) -> None:
        """Drop the oldest finished jobs once over the cap (bounds memory)."""
        if len(self._jobs) <= _MAX_JOBS:
            return
        finished = sorted(
            (j for j in self._jobs.values() if j.status in (COMPLETED, FAILED)),
            key=lambda j: j.updated_at,
        )
        for job in finished:
            if len(self._jobs) <= _MAX_JOBS:
                break
            self._jobs.pop(job.job_id, None)
