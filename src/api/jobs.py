"""In-process async job registry for long-running API operations.

Jobs run as asyncio tasks inside the API process and their results are
held in memory. This intentionally trades durability for zero
infrastructure: a process restart loses job history, and in a
multi-worker deployment each worker only knows its own jobs. For
cross-worker durability, back this registry with Redis instead.
"""

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

logger = logging.getLogger(__name__)

# Completed/failed jobs are dropped after this many seconds
_JOB_TTL_SECONDS = 3600
# Hard cap on tracked jobs (oldest finished jobs evicted first)
_MAX_JOBS = 500


class JobStatus(StrEnum):
    """Lifecycle states of a background job."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class Job:
    """A tracked background job."""

    id: str
    kind: str
    status: JobStatus = JobStatus.PENDING
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    result: Any = None
    error: str | None = None
    params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Public job state (without the potentially large result payload)."""
        duration = None
        if self.started_at is not None:
            duration = round((self.finished_at or time.time()) - self.started_at, 3)
        return {
            "job_id": self.id,
            "kind": self.kind,
            "status": self.status.value,
            "created_at": self.created_at,
            "duration_seconds": duration,
            "error": self.error,
            "params": self.params,
        }


class JobRegistry:
    """Tracks asyncio background jobs by ID."""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._tasks: dict[str, asyncio.Task] = {}

    def submit(
        self,
        kind: str,
        runner: Callable[[], Awaitable[Any]],
        params: dict[str, Any] | None = None,
    ) -> Job:
        """Create a job and start running it in the background.

        Args:
            kind: Job type label (e.g. "bulk_export").
            runner: Async callable producing the job's result.
            params: Request parameters recorded on the job for status display.

        Returns:
            The created Job (status pending/running).
        """
        self._evict_stale()
        job = Job(id=uuid.uuid4().hex, kind=kind, params=params or {})
        self._jobs[job.id] = job

        async def _run() -> None:
            job.status = JobStatus.RUNNING
            job.started_at = time.time()
            try:
                job.result = await runner()
                job.status = JobStatus.COMPLETED
            except Exception as e:
                job.status = JobStatus.FAILED
                job.error = str(e)
                logger.warning(f"Job {job.id} ({kind}) failed: {e}")
            finally:
                job.finished_at = time.time()
                self._tasks.pop(job.id, None)

        self._tasks[job.id] = asyncio.get_running_loop().create_task(_run())
        return job

    def get(self, job_id: str) -> Job | None:
        """Look up a job by ID."""
        self._evict_stale()
        return self._jobs.get(job_id)

    def _evict_stale(self) -> None:
        """Drop finished jobs past their TTL and enforce the size cap."""
        now = time.time()
        finished = [
            (job.finished_at or 0.0, job_id)
            for job_id, job in self._jobs.items()
            if job.status in (JobStatus.COMPLETED, JobStatus.FAILED)
        ]
        for finished_at, job_id in finished:
            if now - finished_at > _JOB_TTL_SECONDS:
                self._jobs.pop(job_id, None)

        if len(self._jobs) > _MAX_JOBS:
            # Evict oldest finished jobs first; running jobs are never evicted
            evictable = sorted(
                (
                    (job.created_at, job_id)
                    for job_id, job in self._jobs.items()
                    if job.status in (JobStatus.COMPLETED, JobStatus.FAILED)
                ),
            )
            for _, job_id in evictable[: len(self._jobs) - _MAX_JOBS]:
                self._jobs.pop(job_id, None)


# Shared registry for the API process
job_registry = JobRegistry()
