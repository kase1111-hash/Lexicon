"""Unit tests for the async bulk-export job API."""

import asyncio

import pytest

from src.api.jobs import JobRegistry, JobStatus


class TestJobRegistry:
    """Tests for the in-process job registry."""

    def test_submit_and_complete(self):
        async def scenario():
            registry = JobRegistry()

            async def work():
                return {"answer": 42}

            job = registry.submit("test", work)
            assert registry.get(job.id) is job
            await asyncio.sleep(0.05)
            assert job.status == JobStatus.COMPLETED
            assert job.result == {"answer": 42}
            assert job.to_dict()["status"] == "completed"
            assert job.to_dict()["duration_seconds"] is not None

        asyncio.run(scenario())

    def test_failure_recorded(self):
        async def scenario():
            registry = JobRegistry()

            async def work():
                raise ValueError("boom")

            job = registry.submit("test", work)
            await asyncio.sleep(0.05)
            assert job.status == JobStatus.FAILED
            assert job.error == "boom"

        asyncio.run(scenario())

    def test_unknown_job(self):
        registry = JobRegistry()
        assert registry.get("nope") is None


class FakeResult:
    def __init__(self, records):
        self._records = records

    async def fetch(self, n):
        return self._records[:n]


class FakeSession:
    def __init__(self, records_by_marker):
        self._records_by_marker = records_by_marker

    async def run(self, query, params=None):
        for marker, records in self._records_by_marker.items():
            if marker in query:
                return FakeResult(records)
        return FakeResult([])


class FakeDB:
    """DatabaseManager stand-in with canned Neo4j responses."""

    def __init__(self, records_by_marker):
        self._records_by_marker = records_by_marker

    def neo4j_session(self):
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def _session():
            yield FakeSession(self._records_by_marker)

        return _session()


@pytest.fixture()
def client(monkeypatch):
    from fastapi.testclient import TestClient

    from src.api.main import app
    from src.api.routes import graph

    fake_db = FakeDB(
        {
            "MATCH (l:LSR {language_code: $lang})": [
                {"l": {"id": "1", "form_orthographic": "water", "language_code": "eng"}},
                {"l": {"id": "2", "form_orthographic": "fire", "language_code": "eng"}},
            ],
            "type(r) AS type": [
                {"source": "1", "type": "COGNATE_OF", "target": "2"},
            ],
        }
    )

    async def fake_get_db_manager():
        return fake_db

    app.dependency_overrides[graph.get_db_manager] = fake_get_db_manager
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


class TestBulkExportRoutes:
    """End-to-end tests for sync and async export."""

    def test_sync_export_returns_items(self, client):
        response = client.post(
            "/api/v1/graph/bulk/export",
            json={"language": "eng", "format": "json"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "completed"
        assert data["count"] == 2
        assert [item["form_orthographic"] for item in data["items"]] == ["water", "fire"]
        assert data["relationships"] == [{"source": "1", "type": "COGNATE_OF", "target": "2"}]

    def test_sync_export_csv(self, client):
        response = client.post(
            "/api/v1/graph/bulk/export",
            json={"language": "eng", "format": "csv", "include_relationships": False},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 2
        assert "form_orthographic" in data["csv"]
        assert "water" in data["csv"]

    def test_invalid_format_rejected(self, client):
        response = client.post(
            "/api/v1/graph/bulk/export",
            json={"language": "eng", "format": "invalid_format"},
        )
        # The app's RequestValidationError handler maps schema errors to 400
        assert response.status_code == 400

    def test_async_export_full_lifecycle(self, client):
        create = client.post(
            "/api/v1/graph/bulk/export",
            json={"language": "eng", "format": "json", "run_async": True},
        )
        assert create.status_code == 200
        body = create.json()
        assert body["status"] == "accepted"
        job_id = body["job_id"]

        status = client.get(f"/api/v1/graph/bulk/status/{job_id}").json()
        assert status["job_id"] == job_id
        assert status["status"] in ("pending", "running", "completed")

        # TestClient runs the event loop per request; by the next request
        # the background task has been scheduled and completed
        for _ in range(50):
            status = client.get(f"/api/v1/graph/bulk/status/{job_id}").json()
            if status["status"] == "completed":
                break
        assert status["status"] == "completed"
        assert status["download_url"] == f"/api/v1/graph/bulk/result/{job_id}"

        result = client.get(f"/api/v1/graph/bulk/result/{job_id}")
        assert result.status_code == 200
        payload = result.json()
        assert payload["count"] == 2
        assert len(payload["items"]) == 2

    def test_status_unknown_job(self, client):
        response = client.get("/api/v1/graph/bulk/status/does-not-exist")
        assert response.status_code == 200
        assert response.json()["status"] == "not_found"

    def test_result_unknown_job_404(self, client):
        response = client.get("/api/v1/graph/bulk/result/does-not-exist")
        assert response.status_code == 404
