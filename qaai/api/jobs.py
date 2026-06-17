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
- Jobs are serialized by an asyncio.Lock so only one review runs at a time. This
  preserves the global per-run-folder logging invariant (start_new_run() mutates
  process-global logging + settings.log_file_path), matching today's effective
  one-review-at-a-time behaviour.
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

    def to_status_dict(self) -> Dict[str, object]:
        """Public status payload for GET /jobs/{id} (no internal paths)."""
        return {
            "job_id": self.job_id,
            "status": self.status,
            "filename": self.filename,
            "error": self.error,
        }


# A factory that, given the assigned job_id, returns the awaitable doing the
# actual work. We take a factory (not a bare coroutine) so the coroutine is
# created inside the task, after the serialization lock is held; it receives the
# job_id because the service methods use it as their thread_id prefix.
CoroFactory = Callable[[str], Awaitable[str]]


class JobManager:
    """Registry of background review jobs (single-process, in-memory)."""

    def __init__(self) -> None:
        self._jobs: Dict[str, Job] = {}
        self._tasks: Dict[str, asyncio.Task] = {}
        # Serialize review execution; submission/status reads are not gated.
        self._run_lock = asyncio.Lock()

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

    async def _run(self, job: Job, coro_factory: CoroFactory) -> None:
        # The lock makes reviews run one at a time, preserving the per-run-folder
        # logging invariant. While queued, the job stays in PENDING.
        async with self._run_lock:
            self._set(job, RUNNING)
            logger.info("Job %s running", job.job_id)
            try:
                result_path = await coro_factory(job.job_id)
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
