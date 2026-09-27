"""Bulk export job lookups when the shared Redis job store fails.

A job that may exist in Redis must not be reported as unknown (404) just
because Redis cannot be asked: the export endpoints answer 503 instead.
"""

import json
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from src.api import jobs as jobs_module
from src.api.jobs import JobRegistry, JobStatus, JobStoreUnavailableError
from src.api.main import app

GRAPH = "/api/v1/graph"
JOB_ID = "a" * 32


class FakeRedis:
    """The subset of redis.Redis the registry reads; fails on demand."""

    def __init__(self, data: dict[str, str] | None = None) -> None:
        self.data = data or {}
        self.failing: set[str] = set()
        self.gets = 0

    def get(self, key: str) -> str | None:
        self.gets += 1
        if key in self.failing or "*" in self.failing:
            raise TimeoutError("Timeout reading from socket")
        return self.data.get(key)


def _stored_job(status: JobStatus = JobStatus.COMPLETED) -> dict[str, str]:
    key = jobs_module._REDIS_KEY_PREFIX + JOB_ID
    state = {
        "id": JOB_ID,
        "kind": "bulk_export",
        "status": status.value,
        "created_at": time.time(),
        "started_at": time.time(),
        "finished_at": time.time(),
        "error": None,
        "params": {},
    }
    return {key: json.dumps(state), f"{key}:result": json.dumps({"count": 1, "items": []})}


def _registry(redis: FakeRedis) -> JobRegistry:
    registry = JobRegistry()
    registry._redis = redis
    return registry


class TestLookup:
    def test_lookup_reads_jobs_of_other_workers(self) -> None:
        job = _registry(FakeRedis(_stored_job())).lookup(JOB_ID)
        assert job is not None and job.status == JobStatus.COMPLETED
        assert job.result == {"count": 1, "items": []}

    def test_unknown_job_is_none(self) -> None:
        assert _registry(FakeRedis()).lookup(JOB_ID) is None

    def test_redis_failure_raises_and_backs_off(self) -> None:
        redis = FakeRedis(_stored_job())
        redis.failing.add("*")
        registry = _registry(redis)
        with pytest.raises(JobStoreUnavailableError):
            registry.lookup(JOB_ID)
        # Within the retry interval Redis is not asked again, and the job is
        # still not reported as unknown
        with pytest.raises(JobStoreUnavailableError):
            registry.lookup(JOB_ID)
        assert redis.gets == 1
        # get() keeps its lenient contract
        assert registry.get(JOB_ID) is None

    def test_result_read_failure_raises(self) -> None:
        redis = FakeRedis(_stored_job())
        job = _registry(redis).lookup(JOB_ID)
        redis.failing.add(jobs_module._REDIS_KEY_PREFIX + JOB_ID + ":result")
        assert job is not None
        with pytest.raises(JobStoreUnavailableError):
            _ = job.result


@pytest.fixture
def registry(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Point the API's shared job registry at a fake Redis."""
    redis = FakeRedis(_stored_job())
    monkeypatch.setattr(jobs_module.job_registry, "_redis", redis)
    monkeypatch.setattr(jobs_module.job_registry, "_redis_retry_at", float("-inf"))
    return redis


class TestExportEndpoints:
    def test_status_and_result_from_redis(self, registry: FakeRedis) -> None:
        client = TestClient(app)
        assert client.get(f"{GRAPH}/bulk/status/{JOB_ID}").json()["status"] == "completed"
        assert client.get(f"{GRAPH}/bulk/result/{JOB_ID}").json()["count"] == 1

    def test_status_is_503_when_redis_fails(self, registry: FakeRedis) -> None:
        registry.failing.add("*")
        client = TestClient(app)
        for _ in range(2):  # the lookup itself, then during the back-off
            response = client.get(f"{GRAPH}/bulk/status/{JOB_ID}")
            assert response.status_code == 503
            assert response.json()["error"] == "DATABASE_ERROR"
            assert "Timeout" not in response.text

    def test_result_is_503_when_redis_fails(self, registry: FakeRedis) -> None:
        registry.failing.add(jobs_module._REDIS_KEY_PREFIX + JOB_ID + ":result")
        response = TestClient(app).get(f"{GRAPH}/bulk/result/{JOB_ID}")
        assert response.status_code == 503

    def test_expired_result_is_404(self, registry: FakeRedis) -> None:
        del registry.data[jobs_module._REDIS_KEY_PREFIX + JOB_ID + ":result"]
        response = TestClient(app).get(f"{GRAPH}/bulk/result/{JOB_ID}")
        assert response.status_code == 404

    def test_unknown_or_malformed_ids_are_still_404(self, registry: FakeRedis) -> None:
        client = TestClient(app)
        assert client.get(f"{GRAPH}/bulk/status/{'b' * 32}").status_code == 404
        registry.failing.add("*")
        # A malformed id cannot be a job, whatever Redis does
        assert client.get(f"{GRAPH}/bulk/status/not-a-job").status_code == 404
