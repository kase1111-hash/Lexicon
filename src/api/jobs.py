"""Async job registry for long-running API operations.

Jobs run as asyncio tasks inside the API process that accepted them. Their
state is kept in that process's memory and, once `use_redis()` has connected
the registry to Redis (the API does this at startup when Redis is reachable),
also written to Redis with a TTL. Any worker of a multi-worker deployment can
then report a job's status and serve its result. Without Redis each worker
only knows its own jobs and a restart loses them, so run a single worker
(`make run-api-prod` falls back to one when Redis is unreachable).

Finished jobs expire after an hour. The results each process holds are also
limited in total size; past the limit its oldest finished jobs are dropped
early, so memory (or Redis) cannot fill up with results nobody fetched.
"""

import asyncio
import json
import logging
import re
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
# Results a process holds (in its memory, or written by it to Redis), as JSON;
# past this the oldest finished jobs are dropped, as if expired. A result
# held in memory takes about twice its JSON size.
_MAX_RESULT_BYTES = 100_000_000
# Jobs running at once per process; further jobs wait as pending
_MAX_RUNNING_JOBS = 4
_REDIS_KEY_PREFIX = "lexicon:job:"
# Redis calls made on the event loop (status lookups) give up after this many
# seconds, and after a failed lookup Redis is not asked again for
# _REDIS_RETRY_SECONDS, so an unresponsive Redis cannot stall the API
_REDIS_LOOP_TIMEOUT_SECONDS = 2.0
_REDIS_RETRY_SECONDS = 10.0
_JOB_ID = re.compile(r"^[0-9a-f]{32}$")


class JobStoreUnavailableError(Exception):
    """Raised when a job may exist in Redis but Redis cannot be asked.

    Reporting such a job as unknown (404) would tell a client polling a
    running export that it is gone; the API answers 503 instead.
    """


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

    def _state(self) -> dict[str, Any]:
        """Everything but the result, for storage."""
        return {
            "id": self.id,
            "kind": self.kind,
            "status": self.status.value,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "params": self.params,
        }


class _StoredJob(Job):
    """A job read back from Redis; its result is only fetched when accessed."""

    _load_result: Callable[[], Any] | None = None

    @property
    def result(self) -> Any:
        if self._load_result is not None:
            self._result = self._load_result()
            self._load_result = None
        return self._result

    @result.setter
    def result(self, value: Any) -> None:
        self._result = value


class JobRegistry:
    """Tracks asyncio background jobs by ID."""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._redis: Any = None
        # Writes results (possibly large) from worker threads; None: use _redis
        self._redis_writer: Any = None
        self._redis_retry_at = float("-inf")
        self._running = 0
        # Job id -> (finished_at, JSON size) of each result this process holds
        self._result_sizes: dict[str, tuple[float, int]] = {}

    def use_redis(self, redis_url: str | None) -> bool:
        """Share job state through Redis, or stop doing so (redis_url=None).

        Returns:
            True if Redis answered and will be used, False otherwise.
        """
        self._redis = self._redis_writer = None
        if not redis_url:
            return False
        try:
            import redis

            client = redis.Redis.from_url(
                redis_url,
                socket_connect_timeout=_REDIS_LOOP_TIMEOUT_SECONDS,
                socket_timeout=_REDIS_LOOP_TIMEOUT_SECONDS,
            )
            client.ping()
            writer = redis.Redis.from_url(
                redis_url, socket_connect_timeout=2.0, socket_timeout=10.0
            )
        except Exception as e:
            logger.warning(f"Job registry keeps jobs in process memory (Redis unavailable: {e})")
            return False
        self._redis, self._redis_writer = client, writer
        self._redis_retry_at = float("-inf")
        return True

    def submit(
        self,
        kind: str,
        runner: Callable[[], Awaitable[Any]],
        params: dict[str, Any] | None = None,
    ) -> Job:
        """Create a job and start running it in the background.

        At most _MAX_RUNNING_JOBS run at once per process; the rest wait as
        pending.

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
        # Record the job before its id is handed out, so any worker can find it
        self._store(job)

        async def _run() -> None:
            while self._running >= _MAX_RUNNING_JOBS:
                await asyncio.sleep(0.2)
            self._running += 1
            try:
                job.status = JobStatus.RUNNING
                job.started_at = time.time()
                await self._store_async(job)
                try:
                    job.result = await runner()
                    job.status = JobStatus.COMPLETED
                except Exception as e:
                    job.status = JobStatus.FAILED
                    job.error = str(e)
                    logger.warning(f"Job {job.id} ({kind}) failed: {e}")
            finally:
                self._running -= 1
                job.finished_at = time.time()
                self._tasks.pop(job.id, None)
            serialized = None
            if job.status == JobStatus.COMPLETED:
                # Serialized off the event loop: to measure it, and for Redis
                serialized = await asyncio.to_thread(_to_json, job.result)
                self._result_sizes[job.id] = (job.finished_at, len(serialized))
            if await self._store_async(job, with_result=True, serialized=serialized):
                # Redis now serves this job to every worker; free the memory
                self._jobs.pop(job.id, None)
            await self._drop_results_over_limit(keep=job.id)

        self._tasks[job.id] = asyncio.get_running_loop().create_task(_run())
        return job

    def get(self, job_id: str) -> Job | None:
        """Look up a job by ID (in this process, then in Redis).

        Returns:
            The job, or None if it is unknown, expired or cannot be read
            (use lookup() to tell the last case apart).
        """
        try:
            return self.lookup(job_id)
        except JobStoreUnavailableError:
            return None

    def lookup(self, job_id: str) -> Job | None:
        """Look up a job by ID (in this process, then in Redis).

        Returns:
            The job, or None if no worker knows it (or it expired).

        Raises:
            JobStoreUnavailableError: If the job is not in this process and
                the shared Redis store cannot be read.
        """
        self._evict_stale()
        job = self._jobs.get(job_id)
        if job is None and self._redis is not None and _JOB_ID.match(job_id):
            job = self._load(job_id)
        return job

    def _store(self, job: Job, with_result: bool = False, serialized: str | None = None) -> bool:
        """Write a job (and, once completed, its result) to Redis. True if stored.

        Args:
            job: The job.
            with_result: Also write the result of a completed job.
            serialized: The result already serialized with _to_json, if it is.
        """
        client = self._redis
        if with_result and self._redis_writer is not None:
            client = self._redis_writer
        if client is None:
            return False
        key = _REDIS_KEY_PREFIX + job.id
        try:
            pipe = client.pipeline()
            if with_result and job.status == JobStatus.COMPLETED:
                result = serialized if serialized is not None else _to_json(job.result)
                pipe.set(f"{key}:result", result, ex=_JOB_TTL_SECONDS)
            pipe.set(key, json.dumps(job._state()), ex=_JOB_TTL_SECONDS)
            pipe.execute()
            return True
        except Exception as e:
            logger.warning(f"Could not store job {job.id} in Redis: {e}")
            return False

    async def _store_async(
        self, job: Job, with_result: bool = False, serialized: str | None = None
    ) -> bool:
        """_store without blocking the event loop (results can be large)."""
        if self._redis is None:
            return False
        return await asyncio.to_thread(self._store, job, with_result, serialized)

    async def _drop_results_over_limit(self, keep: str) -> None:
        """Drop the oldest finished jobs until their results fit _MAX_RESULT_BYTES.

        Args:
            keep: The job that just finished, which is never dropped.
        """
        now = time.time()
        for job_id, (finished_at, _) in list(self._result_sizes.items()):
            if now - finished_at > _JOB_TTL_SECONDS:  # expired here and in Redis
                del self._result_sizes[job_id]
        held = sum(size for _, size in self._result_sizes.values())
        dropped = []
        for job_id in sorted(self._result_sizes, key=lambda i: self._result_sizes[i][0]):
            if held <= _MAX_RESULT_BYTES:
                break
            if job_id != keep:
                held -= self._result_sizes.pop(job_id)[1]
                self._jobs.pop(job_id, None)
                dropped.append(job_id)
        if not dropped:
            return
        logger.warning(
            f"Dropped {len(dropped)} finished job(s) to keep the results held "
            f"within {_MAX_RESULT_BYTES} bytes"
        )
        client = self._redis_writer or self._redis
        if client is not None:
            keys = [_REDIS_KEY_PREFIX + job_id for job_id in dropped]
            keys += [f"{key}:result" for key in keys]
            try:
                await asyncio.to_thread(client.delete, *keys)
            except Exception as e:
                logger.warning(f"Could not drop jobs from Redis: {e}")

    def _load(self, job_id: str) -> Job | None:
        """Read a job stored by any worker from Redis."""
        client = self._redis
        key = _REDIS_KEY_PREFIX + job_id
        if time.monotonic() < self._redis_retry_at:
            raise JobStoreUnavailableError("Redis did not answer recently")
        try:
            raw = client.get(key)
        except Exception as e:
            self._redis_retry_at = time.monotonic() + _REDIS_RETRY_SECONDS
            logger.warning(f"Could not read job {job_id} from Redis: {e}")
            raise JobStoreUnavailableError(f"Could not read job {job_id} from Redis") from e
        if raw is None:
            return None
        state = json.loads(raw)
        job = _StoredJob(
            id=state["id"],
            kind=state["kind"],
            status=JobStatus(state["status"]),
            created_at=state["created_at"],
            started_at=state["started_at"],
            finished_at=state["finished_at"],
            error=state["error"],
            params=state["params"],
        )
        if job.status == JobStatus.COMPLETED:

            def load_result() -> Any:
                try:
                    payload = client.get(f"{key}:result")
                except Exception as e:
                    logger.warning(f"Could not read the result of job {job_id} from Redis: {e}")
                    raise JobStoreUnavailableError(
                        f"Could not read the result of job {job_id} from Redis"
                    ) from e
                return json.loads(payload) if payload is not None else None

            job._load_result = load_result
        return job

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
                self._result_sizes.pop(job_id, None)

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
                self._result_sizes.pop(job_id, None)


def _to_json(result: Any) -> str:
    """Serialize a job result as stored in Redis."""
    return json.dumps(result, default=str)


# Shared registry for the API process
job_registry = JobRegistry()
