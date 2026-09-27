"""App-level API behaviour: middleware, health, configuration, jobs and monitoring."""

import asyncio
import contextlib
import json
import os
import subprocess
import sys
import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import main as api_main
from src.api import middleware as middleware_module
from src.api.jobs import JobRegistry, JobStatus
from src.api.main import app, configure_middleware
from src.api.middleware import RateLimitMiddleware
from src.config import APIConfig, Settings, get_settings
from src.exceptions import ConfigurationError, LexiconError, NotFoundError
from src.utils import db as db_module
from src.utils.db import DatabaseConfig, DatabaseManager
from src.utils.metrics import metrics

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _neo4j_available() -> bool:
    """Whether the configured Neo4j (NEO4J_URI / NEO4J_PASSWORD) accepts us."""
    from neo4j import GraphDatabase

    config = DatabaseConfig()
    try:
        driver = GraphDatabase.driver(
            config.neo4j_uri, auth=(config.neo4j_user, config.neo4j_password)
        )
        try:
            driver.verify_connectivity()
        finally:
            driver.close()
        return True
    except Exception:
        return False


requires_db = pytest.mark.skipif(
    not _neo4j_available(),
    reason="requires a reachable Neo4j (start with `docker compose up -d neo4j`)",
)


def _stack_client(client: tuple[str, int] = ("testclient", 50000), **api: Any) -> TestClient:
    """A small app behind the real middleware stack, configured by `api`."""
    test_app = FastAPI()

    @test_app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    @test_app.get("/items/{item_id}")
    async def item(item_id: str) -> dict:
        return {"id": item_id}

    api.setdefault("rate_limit_enabled", True)  # the test suite may disable it globally
    settings = Settings(_env_file=None, api=APIConfig(_env_file=None, **api))
    configure_middleware(test_app, settings)
    return TestClient(test_app, client=client)


def _subprocess_env(**overrides: str) -> dict[str, str]:
    """Environment for a fresh interpreter, without secrets from this one."""
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in {"API_KEY", "NEO4J_PASSWORD", "POSTGRES_PASSWORD", "ENVIRONMENT"}
    }
    env.update({"LOG_LEVEL": "WARNING", "PYTHONPATH": str(PROJECT_ROOT), **overrides})
    return env


# =============================================================================
# Rate limiting (D3-04)
# =============================================================================


class FakeAsyncPipeline:
    def __init__(self, redis: "FakeAsyncRedis"):
        self.redis = redis
        self.ops: list[tuple[str, str, int]] = []

    def incr(self, key: str) -> "FakeAsyncPipeline":
        self.ops.append(("incr", key, 0))
        return self

    def expire(self, key: str, seconds: int) -> "FakeAsyncPipeline":
        self.ops.append(("expire", key, seconds))
        return self

    async def execute(self) -> list[Any]:
        if self.redis.broken:
            raise ConnectionError("redis down")
        results: list[Any] = []
        for op, key, seconds in self.ops:
            if op == "incr":
                self.redis.data[key] = self.redis.data.get(key, 0) + 1
                results.append(self.redis.data[key])
            else:
                self.redis.expiry[key] = seconds
                results.append(True)
        return results


class FakeAsyncRedis:
    def __init__(self) -> None:
        self.data: dict[str, int] = {}
        self.expiry: dict[str, int] = {}
        self.broken = False

    def pipeline(self, transaction: bool = True) -> FakeAsyncPipeline:
        return FakeAsyncPipeline(self)


class FakeRedisManager:
    """What RateLimitMiddleware needs from the global DatabaseManager."""

    def __init__(self, redis: FakeAsyncRedis):
        self.redis = redis

    def get_connection_status(self) -> dict[str, dict[str, Any]]:
        return {"redis": {"connected": True, "error": None}}


class _MidWindowClock:
    """The `time` module as the rate limiter sees it, with the wall clock
    fixed 30 s into a minute: a real window boundary inside a burst would
    reset the counters and change the counts the tests expect."""

    def time(self) -> float:
        return 1_800_000_030.0

    def __getattr__(self, name: str) -> Any:
        return getattr(time, name)


class TestRateLimiting:
    """Requests over RATE_LIMIT_REQUESTS per window are rejected with 429."""

    @pytest.fixture(autouse=True)
    def _fixed_window(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(middleware_module, "time", _MidWindowClock())

    def test_over_limit_returns_429_with_retry_after(self) -> None:
        client = _stack_client(rate_limit_requests=3, rate_limit_window_seconds=60)
        remaining = []
        for i in range(3):
            response = client.get(f"/items/{i}")
            assert response.status_code == 200
            assert response.headers["X-RateLimit-Limit"] == "3"
            remaining.append(response.headers["X-RateLimit-Remaining"])
        assert remaining == ["2", "1", "0"]

        response = client.get("/items/4")
        assert response.status_code == 429
        body = response.json()
        assert body["error"] == "RATE_LIMIT_EXCEEDED"
        retry_after = int(response.headers["Retry-After"])
        assert 1 <= retry_after <= 60
        assert body["details"]["retry_after_seconds"] == retry_after
        # Rejections still pass the outer middleware
        assert response.headers.get("X-Request-ID")

    def test_health_is_exempt(self) -> None:
        client = _stack_client(rate_limit_requests=1)
        assert client.get("/items/1").status_code == 200
        assert client.get("/items/2").status_code == 429
        for _ in range(5):
            assert client.get("/health").status_code == 200

    def test_disabled(self) -> None:
        client = _stack_client(rate_limit_requests=1, rate_limit_enabled=False)
        for i in range(5):
            assert client.get(f"/items/{i}").status_code == 200

    def test_clients_are_counted_per_ip_without_auth(self) -> None:
        first = _stack_client(client=("10.0.0.1", 1000), rate_limit_requests=1)
        # Same middleware instance, another client address
        second = TestClient(first.app, client=("10.0.0.2", 1000))
        assert first.get("/items/1").status_code == 200
        assert first.get("/items/1").status_code == 429
        assert second.get("/items/1").status_code == 200

    def test_clients_sharing_the_api_key_are_counted_per_ip(self) -> None:
        first = _stack_client(client=("10.0.0.1", 1000), api_key="secret", rate_limit_requests=1)
        second = TestClient(first.app, client=("10.0.0.2", 1000))
        key = {"X-API-Key": "secret"}
        assert first.get("/items/1", headers=key).status_code == 200
        assert first.get("/items/1", headers=key).status_code == 429
        # The one API key is shared by all clients; another address has its own budget
        assert second.get("/items/1", headers=key).status_code == 200

    def test_rejected_api_keys_count(self) -> None:
        client = _stack_client(api_key="secret", rate_limit_requests=3)
        statuses = [
            client.get("/items/1", headers={"X-API-Key": f"guess-{i}"}).status_code
            for i in range(5)
        ]
        assert statuses == [401, 401, 401, 429, 429]
        # Once limited, even the right key waits for the next window
        assert client.get("/items/1", headers={"X-API-Key": "secret"}).status_code == 429

    def test_window_resets(self) -> None:
        async def scenario() -> list[int]:
            limiter = RateLimitMiddleware(app=FastAPI(), requests=2, window_seconds=60)
            counts = [await limiter._count("ip:x", 100) for _ in range(3)]
            counts.append(await limiter._count("ip:x", 101))
            return counts

        assert asyncio.run(scenario()) == [1, 2, 3, 1]

    def test_redis_counters_are_shared_between_workers(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        redis = FakeAsyncRedis()
        monkeypatch.setattr(db_module, "_db_manager", FakeRedisManager(redis))

        async def scenario() -> list[int]:
            # Two middleware instances stand in for two worker processes
            worker_a = RateLimitMiddleware(app=FastAPI(), requests=5, window_seconds=60)
            worker_b = RateLimitMiddleware(app=FastAPI(), requests=5, window_seconds=60)
            return [
                await worker_a._count("ip:x", 7),
                await worker_b._count("ip:x", 7),
                await worker_a._count("ip:x", 7),
            ]

        assert asyncio.run(scenario()) == [1, 2, 3]
        assert redis.expiry == {"lexicon:ratelimit:ip:x:7": 61}

    def test_redis_failure_falls_back_to_process_counters(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        redis = FakeAsyncRedis()
        redis.broken = True
        monkeypatch.setattr(db_module, "_db_manager", FakeRedisManager(redis))

        async def scenario() -> list[int]:
            limiter = RateLimitMiddleware(app=FastAPI(), requests=5, window_seconds=60)
            return [await limiter._count("ip:x", 7) for _ in range(2)]

        assert asyncio.run(scenario()) == [1, 2]

    def test_redis_is_not_retried_right_after_a_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An unresponsive Redis must not delay every request by its timeout."""
        redis = FakeAsyncRedis()
        redis.broken = True
        monkeypatch.setattr(db_module, "_db_manager", FakeRedisManager(redis))
        pipelines: list[int] = []
        original_pipeline = redis.pipeline

        def counting_pipeline(transaction: bool = True) -> FakeAsyncPipeline:
            pipelines.append(1)
            return original_pipeline(transaction)

        redis.pipeline = counting_pipeline  # type: ignore[method-assign]

        async def scenario() -> list[int]:
            limiter = RateLimitMiddleware(app=FastAPI(), requests=5, window_seconds=60)
            counts = [await limiter._count("ip:x", 7) for _ in range(3)]
            assert len(pipelines) == 1  # the failure is remembered
            redis.broken = False
            limiter._redis_retry_at = float("-inf")  # retry interval elapsed
            counts.append(await limiter._count("ip:x", 7))
            return counts

        # In process 1, 2, 3; then Redis again, which has not seen this client
        assert asyncio.run(scenario()) == [1, 2, 3, 1]
        assert len(pipelines) == 2


# =============================================================================
# CORS and API key authentication (D3-06, D3-20)
# =============================================================================


class TestCORSWithAuth:
    """Browsers can call the API when an API key is configured."""

    ORIGIN = "http://localhost:3000"

    def test_preflight_is_not_rejected_by_auth(self) -> None:
        client = _stack_client(api_key="secret")
        response = client.options(
            "/items/1",
            headers={
                "Origin": self.ORIGIN,
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "x-api-key",
            },
        )
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == self.ORIGIN
        assert "x-api-key" in response.headers["access-control-allow-headers"].lower()

    def test_auth_errors_carry_cors_headers(self) -> None:
        client = _stack_client(api_key="secret")
        response = client.get("/items/1", headers={"Origin": self.ORIGIN})
        assert response.status_code == 401
        assert response.headers["access-control-allow-origin"] == self.ORIGIN
        assert "X-Request-ID" in response.headers["access-control-expose-headers"]

    def test_valid_key_accepted(self) -> None:
        client = _stack_client(api_key="secret")
        response = client.get("/items/1", headers={"X-API-Key": "secret"})
        assert response.status_code == 200


class TestAPIKeyEncoding:
    """Header bytes outside ASCII are compared, never crash (D3-20)."""

    def test_non_ascii_key_is_rejected_with_401(self) -> None:
        client = _stack_client(api_key="secret")
        response = client.get("/items/1", headers={"X-API-Key": "sécret".encode()})
        assert response.status_code == 401
        assert response.json()["error"] == "AUTHENTICATION_ERROR"

    def test_non_ascii_configured_key_matches_utf8_header(self) -> None:
        client = _stack_client(api_key="sécret")
        assert client.get("/items/1", headers={"X-API-Key": "sécret".encode()}).status_code == 200
        assert client.get("/items/1", headers={"X-API-Key": "secret"}).status_code == 401


# =============================================================================
# Settings from the .env file (D3-07) and production validation (D3-22)
# =============================================================================


@pytest.fixture()
def env_file(tmp_path: Path) -> Iterator[Path]:
    """A temporary .env selected via ENV_FILE; restores the environment afterwards."""
    path = tmp_path / "test.env"
    # load_dotenv writes os.environ directly; patch.dict undoes every change
    with patch.dict(os.environ):
        for key in ("API_KEY", "CORS_ORIGINS", "RATE_LIMIT_REQUESTS", "LOG_LEVEL", "LOG_FILE"):
            os.environ.pop(key, None)
        os.environ["ENV_FILE"] = str(path)
        get_settings.cache_clear()
        try:
            yield path
        finally:
            get_settings.cache_clear()


class TestSettingsFromEnvFile:
    """Flat keys in .env reach every settings section."""

    def test_flat_keys_apply(self, env_file: Path) -> None:
        env_file.write_text(
            "API_KEY=from-dotenv\n"
            "CORS_ORIGINS=http://example.org\n"
            "RATE_LIMIT_REQUESTS=7\n"
            "LOG_LEVEL=DEBUG\n"
        )
        settings = get_settings()
        assert settings.api.api_key is not None
        assert settings.api.api_key.get_secret_value() == "from-dotenv"
        assert settings.api.cors_origins_list == ["http://example.org"]
        assert settings.api.rate_limit_requests == 7
        assert settings.logging.log_level == "DEBUG"

    def test_environment_wins_over_env_file(
        self, env_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env_file.write_text("API_KEY=from-dotenv\n")
        monkeypatch.setenv("API_KEY", "from-environment")
        settings = get_settings()
        assert settings.api.api_key is not None
        assert settings.api.api_key.get_secret_value() == "from-environment"

    def test_empty_values_mean_default(self, env_file: Path) -> None:
        # As in .env.example: "API_KEY=" leaves authentication disabled
        env_file.write_text("API_KEY=\nLOG_FILE=\n")
        settings = get_settings()
        assert settings.api.api_key is None
        assert settings.logging.log_file is None

    def test_api_key_in_env_file_enables_auth(self, tmp_path: Path) -> None:
        env_path = tmp_path / "auth.env"
        env_path.write_text("API_KEY=from-dotenv\n")
        script = (
            "from fastapi.testclient import TestClient\n"
            "from src.api.main import app\n"
            "client = TestClient(app)\n"
            "print(client.get('/metrics/json').status_code,"
            " client.get('/metrics/json', headers={'X-API-Key': 'from-dotenv'}).status_code)\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=PROJECT_ROOT,
            env=_subprocess_env(ENV_FILE=str(env_path)),
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, result.stderr[-2000:]
        assert result.stdout.split()[-2:] == ["401", "200"]


class TestProductionValidation:
    """Invalid production configuration names what is missing (D3-22)."""

    def test_configuration_error_accepts_message_and_details(self) -> None:
        err = ConfigurationError(message="Invalid production configuration", details={"x": 1})
        assert err.message == "Invalid production configuration"
        assert err.details == {"x": 1}
        assert ConfigurationError(setting="API_KEY").details == {"setting": "API_KEY"}

    def test_subclasses_accept_message_overrides(self) -> None:
        err = NotFoundError(resource_type="LSR", resource_id="x", message="gone")
        assert err.message == "gone"
        assert err.details == {"resource_type": "LSR", "resource_id": "x"}

    def test_production_import_lists_missing_settings(self) -> None:
        result = subprocess.run(
            [sys.executable, "-c", "import src.api.main"],
            cwd=PROJECT_ROOT,
            env=_subprocess_env(ENV_FILE="/nonexistent", ENVIRONMENT="production"),
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode != 0
        assert "TypeError" not in result.stderr
        assert "ConfigurationError" in result.stderr
        assert "API_KEY is required in production" in result.stderr


# =============================================================================
# Health checks and Neo4j reconnection (D3-05)
# =============================================================================


class FakePingDB:
    def __init__(self, reachable: dict[str, bool]):
        self.reachable = reachable

    async def ping(self) -> dict[str, bool]:
        return dict(self.reachable)


ALL_UP = {"neo4j": True, "postgres": True, "elasticsearch": True, "redis": True}


class TestHealth:
    """/health reflects what the backends actually answer."""

    @pytest.mark.parametrize(
        ("down", "status_code", "status"),
        [
            ((), 200, "healthy"),
            (("redis",), 200, "degraded"),
            (("postgres", "elasticsearch"), 200, "degraded"),
            (("neo4j",), 503, "unhealthy"),
        ],
    )
    def test_status(
        self,
        monkeypatch: pytest.MonkeyPatch,
        down: tuple[str, ...],
        status_code: int,
        status: str,
    ) -> None:
        reachable = {name: name not in down for name in ALL_UP}

        async def fake_get_db() -> FakePingDB:
            return FakePingDB(reachable)

        monkeypatch.setattr(api_main, "get_db", fake_get_db)
        response = TestClient(app).get("/health")
        assert response.status_code == status_code
        body = response.json()
        assert body["status"] == status
        assert body["databases"] == {
            name: "disconnected" if name in down else "connected" for name in ALL_UP
        }

    @requires_db
    def test_live_health(self) -> None:
        db_module._db_manager = None  # never reuse a manager bound to another loop
        try:
            with TestClient(app) as client:
                response = client.get("/health")
        finally:
            db_module._db_manager = None
        assert response.status_code == 200
        assert response.json()["databases"]["neo4j"] == "connected"


class _FakeNeo4j:
    async def verify_connectivity(self) -> None:
        return None


class _FakeES:
    async def ping(self) -> bool:
        return False


class _HangingRedis:
    async def ping(self) -> None:
        await asyncio.sleep(10)


class TestDatabaseManagerProbes:
    """DatabaseManager.ping() round-trips; neo4j_session() reconnects lazily."""

    def test_ping_reports_what_answers(self) -> None:
        async def scenario() -> dict[str, bool]:
            manager = DatabaseManager()
            manager._neo4j_driver = _FakeNeo4j()
            manager._elasticsearch_client = _FakeES()
            manager._redis_client = _HangingRedis()
            return await manager.ping(timeout=0.2)

        assert asyncio.run(scenario()) == {
            "neo4j": True,
            "postgres": False,
            "elasticsearch": False,
            "redis": False,
        }

    def test_reconnect_is_attempted_and_throttled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def scenario() -> list[int]:
            manager = DatabaseManager()
            attempts: list[int] = []

            async def failing_connect() -> bool:
                manager._neo4j_last_attempt = time.monotonic()
                attempts.append(1)
                return False

            monkeypatch.setattr(manager, "connect_neo4j", failing_connect)
            counts = []
            for _ in range(2):
                with pytest.raises(RuntimeError):
                    async with manager.neo4j_session():
                        pass
                counts.append(len(attempts))
            manager._neo4j_last_attempt = float("-inf")  # interval elapsed
            with pytest.raises(RuntimeError):
                async with manager.neo4j_session():
                    pass
            counts.append(len(attempts))
            return counts

        assert asyncio.run(scenario()) == [1, 1, 2]

    def test_rejected_credentials_are_not_retried(self) -> None:
        class AuthFailure(Exception):
            code = "Neo.ClientError.Security.Unauthorized"

        class RejectingDriver:
            async def verify_connectivity(self) -> None:
                raise AuthFailure("unauthorized")

            async def close(self) -> None:
                return None

        async def scenario() -> bool:
            import neo4j

            original = neo4j.AsyncGraphDatabase.driver
            neo4j.AsyncGraphDatabase.driver = lambda *a, **k: RejectingDriver()  # type: ignore[method-assign,assignment]
            try:
                manager = DatabaseManager()
                assert not await manager.connect_neo4j()
                manager._neo4j_last_attempt = float("-inf")
                calls: list[int] = []

                async def counting_connect() -> bool:
                    calls.append(1)
                    return False

                manager.connect_neo4j = counting_connect  # type: ignore[method-assign]
                with contextlib.suppress(RuntimeError):
                    async with manager.neo4j_session():
                        pass
                return not calls
            finally:
                neo4j.AsyncGraphDatabase.driver = original  # type: ignore[method-assign]

        assert asyncio.run(scenario())

    @requires_db
    def test_live_reconnect_after_startup_failure(self) -> None:
        async def scenario() -> Any:
            manager = DatabaseManager()
            real_uri = manager.config.neo4j_uri
            manager.config.neo4j_uri = "bolt://127.0.0.1:1"
            try:
                assert not await manager.connect_neo4j()
                assert not manager.get_connection_status()["neo4j"]["connected"]
                # Neo4j "comes up": no explicit connect, the session reconnects
                manager.config.neo4j_uri = real_uri
                manager.reconnect_interval = 0
                async with manager.neo4j_session() as session:
                    record = await (await session.run("RETURN 1 AS one")).single()
                return record["one"], (await manager.ping())["neo4j"]
            finally:
                await manager.close_all()

        assert asyncio.run(scenario()) == (1, True)


# =============================================================================
# Error responses (D3-19)
# =============================================================================


@pytest.fixture()
def boom_route() -> Iterator[str]:
    """A temporary route on the real app that raises an unhandled error."""
    path = "/__c_app_test_boom"

    async def boom() -> dict:
        raise RuntimeError("kaboom")

    app.add_api_route(path, boom)
    try:
        yield path
    finally:
        app.router.routes[:] = [r for r in app.router.routes if getattr(r, "path", None) != path]


class TestErrorResponses:
    """Every error uses the standard body and carries X-Request-ID."""

    def test_unknown_route(self) -> None:
        response = TestClient(app).get("/api/v1/nonexistent", headers={"X-Request-ID": "c-app-404"})
        assert response.status_code == 404
        assert response.json() == {
            "error": "NOT_FOUND",
            "message": "Not Found",
            "details": {"path": "/api/v1/nonexistent"},
        }
        assert response.headers["X-Request-ID"] == "c-app-404"

    def test_method_not_allowed(self) -> None:
        response = TestClient(app).post("/")
        assert response.status_code == 405
        assert response.json()["error"] == "METHOD_NOT_ALLOWED"
        assert response.json()["details"] == {}
        assert response.headers["Allow"] == "GET"

    def test_unhandled_error_carries_request_id(self, boom_route: str) -> None:
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get(boom_route, headers={"X-Request-ID": "c-app-500"})
        assert response.status_code == 500
        assert response.json()["error"] == "INTERNAL_ERROR"
        assert "kaboom" not in response.text
        assert response.headers["X-Request-ID"] == "c-app-500"

        generated = client.get(boom_route)
        assert generated.status_code == 500
        assert generated.headers.get("X-Request-ID")

    def test_error_body_always_has_details(self) -> None:
        assert LexiconError(message="x").to_dict() == {
            "error": "LEXICON_ERROR",
            "message": "x",
            "details": {},
        }


# =============================================================================
# Monitoring (D3-14)
# =============================================================================


class TestMonitoring:
    """/metrics and /traces report the requests the API served."""

    def test_requests_are_counted_by_route_template(self) -> None:
        client = TestClient(app)
        labels = {
            "endpoint": "/api/v1/graph/bulk/status/{job_id}",
            "method": "GET",
            "status": "404",
        }
        before = metrics.get_counter("api_requests_total", labels)
        active_before = metrics.get_gauge("api_active_requests")
        for job_id in ("a", "b"):
            assert client.get(f"/api/v1/graph/bulk/status/{job_id}").status_code == 404
        assert metrics.get_counter("api_requests_total", labels) == before + 2
        assert metrics.get_gauge("api_active_requests") == active_before

        text = client.get("/metrics").text
        assert (
            'api_requests_total{endpoint="/api/v1/graph/bulk/status/{job_id}",'
            'method="GET",status="404"}' in text
        )
        assert (
            'api_request_duration_seconds_count{endpoint="/api/v1/graph/bulk/status/{job_id}",'
            'method="GET"}' in text
        )
        assert text.endswith("\n")

    def test_unmatched_paths_share_one_series(self) -> None:
        client = TestClient(app)
        labels = {"endpoint": "<unmatched>", "method": "GET", "status": "404"}
        before = metrics.get_counter("api_requests_total", labels)
        client.get("/no/such/path/1")
        client.get("/no/such/path/2")
        assert metrics.get_counter("api_requests_total", labels) == before + 2

    def test_unknown_methods_share_one_series(self) -> None:
        # The server accepts any token as a method; each must not add a series
        client = TestClient(app)
        labels = {"endpoint": "/", "method": "OTHER", "status": "405"}
        before = metrics.get_counter("api_requests_total", labels)
        for method in ("FOO1", "FOO2", "FOO3"):
            assert client.request(method, "/").status_code == 405
        assert metrics.get_counter("api_requests_total", labels) == before + 3
        assert "FOO1" not in client.get("/metrics").text

    def test_metrics_json_serializes_histograms(self) -> None:
        client = TestClient(app)
        client.get("/")
        response = client.get("/metrics/json")
        assert response.status_code == 200
        histograms = response.json()["histograms"]["api_request_duration_seconds"]
        root = histograms['endpoint="/",method="GET"']
        assert root["count"] >= 1
        assert "+Inf" in root["buckets"]

    def test_traces_record_requests(self) -> None:
        client = TestClient(app)
        client.get("/", headers={"X-Request-ID": "c-app-trace"})
        spans = client.get("/traces", params={"limit": 50}).json()
        span = next(s for s in spans if s["attributes"].get("request_id") == "c-app-trace")
        assert span["name"] == "GET /"
        assert span["attributes"]["http.status_code"] == 200
        assert span["end_time"] is not None

    def test_traces_limit_is_validated(self) -> None:
        assert TestClient(app).get("/traces", params={"limit": 0}).status_code == 400


# =============================================================================
# Async jobs shared through Redis (D3-12)
# =============================================================================


class FakeSyncPipeline:
    def __init__(self, redis: "FakeSyncRedis"):
        self.redis = redis
        self.ops: list[tuple[str, str, int | None]] = []

    def set(self, key: str, value: str, ex: int | None = None) -> "FakeSyncPipeline":
        self.ops.append((key, value, ex))
        return self

    def execute(self) -> list[bool]:
        if self.redis.broken:
            raise ConnectionError("redis down")
        return [self.redis.set(key, value, ex=ex) for key, value, ex in self.ops]


class FakeSyncRedis:
    """The subset of redis.Redis the job registry uses."""

    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self.ttl: dict[str, int | None] = {}
        self.gets: list[str] = []
        self.broken = False

    def ping(self) -> bool:
        return True

    def get(self, key: str) -> str | None:
        self.gets.append(key)
        return self.data.get(key)

    def set(self, key: str, value: str, ex: int | None = None) -> bool:
        self.data[key] = value
        self.ttl[key] = ex
        return True

    def pipeline(self) -> FakeSyncPipeline:
        return FakeSyncPipeline(self)


def _shared_registries(redis: Any) -> tuple[JobRegistry, JobRegistry]:
    """Two registries on one Redis, as in two worker processes."""
    worker_a, worker_b = JobRegistry(), JobRegistry()
    worker_a._redis = redis
    worker_b._redis = redis
    return worker_a, worker_b


async def _wait_finished(registry: JobRegistry, job_id: str) -> Any:
    for _ in range(200):
        job = registry.get(job_id)
        if job is not None and job.status in (JobStatus.COMPLETED, JobStatus.FAILED):
            return job
        await asyncio.sleep(0.01)
    raise AssertionError("job did not finish")


class TestJobsAcrossWorkers:
    """Any worker can report and serve a job submitted to another."""

    def test_status_and_result_visible_from_other_worker(self) -> None:
        redis = FakeSyncRedis()
        worker_a, worker_b = _shared_registries(redis)

        async def scenario() -> Any:
            async def work() -> dict[str, Any]:
                return {"count": 2, "items": [{"form": "night"}, {"form": "nacht"}]}

            job = worker_a.submit("bulk_export", work, params={"language": "eng"})
            # Visible elsewhere before it even starts
            pending = worker_b.get(job.id)
            assert pending is not None and pending.status == JobStatus.PENDING
            await _wait_finished(worker_a, job.id)
            return job.id

        job_id = asyncio.run(scenario())
        job = worker_b.get(job_id)
        assert job is not None
        assert job.to_dict()["status"] == "completed"
        assert job.to_dict()["params"] == {"language": "eng"}
        assert job.result == {"count": 2, "items": [{"form": "night"}, {"form": "nacht"}]}
        assert redis.ttl[f"lexicon:job:{job_id}"] == 3600
        assert redis.ttl[f"lexicon:job:{job_id}:result"] == 3600
        # The submitting worker no longer holds the result in memory
        assert job_id not in worker_a._jobs

    def test_status_polls_do_not_load_the_result(self) -> None:
        redis = FakeSyncRedis()
        worker_a, worker_b = _shared_registries(redis)

        async def scenario() -> str:
            async def work() -> list[int]:
                return list(range(1000))

            job = worker_a.submit("bulk_export", work)
            await _wait_finished(worker_a, job.id)
            return job.id

        job_id = asyncio.run(scenario())
        redis.gets.clear()
        job = worker_b.get(job_id)
        assert job is not None
        job.to_dict()
        assert redis.gets == [f"lexicon:job:{job_id}"]
        assert job.result == list(range(1000))

    def test_failed_job_visible_from_other_worker(self) -> None:
        worker_a, worker_b = _shared_registries(FakeSyncRedis())

        async def scenario() -> str:
            async def work() -> None:
                raise ValueError("boom")

            job = worker_a.submit("bulk_export", work)
            await _wait_finished(worker_a, job.id)
            return job.id

        job = worker_b.get(asyncio.run(scenario()))
        assert job is not None
        assert job.status == JobStatus.FAILED
        assert job.error == "boom"

    def test_unknown_and_malformed_ids(self) -> None:
        redis = FakeSyncRedis()
        _, worker_b = _shared_registries(redis)
        assert worker_b.get("0" * 32) is None
        assert worker_b.get("abc:result") is None
        assert redis.gets == [f"lexicon:job:{'0' * 32}"]

    def test_redis_failure_keeps_job_in_memory(self) -> None:
        redis = FakeSyncRedis()
        redis.broken = True
        worker_a, _ = _shared_registries(redis)

        async def scenario() -> Any:
            async def work() -> int:
                return 42

            job = worker_a.submit("bulk_export", work)
            await _wait_finished(worker_a, job.id)
            return job

        job = asyncio.run(scenario())
        assert worker_a.get(job.id) is job
        assert job.result == 42

    def test_running_jobs_are_capped(self) -> None:
        async def scenario() -> tuple[int, list[JobStatus]]:
            registry = JobRegistry()
            release = asyncio.Event()
            running = 0
            peak = 0

            async def work() -> None:
                nonlocal running, peak
                running += 1
                peak = max(peak, running)
                await release.wait()
                running -= 1

            jobs = [registry.submit("bulk_export", work) for _ in range(6)]
            await asyncio.sleep(0.1)
            assert sum(job.status == JobStatus.PENDING for job in jobs) == 2
            release.set()
            for _ in range(100):
                if all(job.status == JobStatus.COMPLETED for job in jobs):
                    break
                await asyncio.sleep(0.05)
            return peak, [job.status for job in jobs]

        peak, statuses = asyncio.run(scenario())
        assert peak == 4
        assert statuses == [JobStatus.COMPLETED] * 6

    def test_lookups_back_off_after_a_redis_failure(self) -> None:
        """Lookups block the event loop, so a failing Redis is not asked every time."""

        class FailingRedis(FakeSyncRedis):
            def get(self, key: str) -> str | None:
                self.gets.append(key)
                raise TimeoutError("redis timed out")

        redis = FailingRedis()
        _, worker_b = _shared_registries(redis)
        job_id = "0" * 32
        assert worker_b.get(job_id) is None
        assert worker_b.get(job_id) is None
        assert len(redis.gets) == 1
        worker_b._redis_retry_at = float("-inf")  # retry interval elapsed
        assert worker_b.get(job_id) is None
        assert len(redis.gets) == 2

    def test_use_redis_unreachable(self) -> None:
        registry = JobRegistry()
        assert registry.use_redis("redis://127.0.0.1:1") is False
        assert registry._redis is None

    @pytest.mark.skipif(
        not os.getenv("TEST_REDIS_URI"), reason="set TEST_REDIS_URI to test against Redis"
    )
    def test_real_redis(self) -> None:
        worker_a, worker_b = JobRegistry(), JobRegistry()
        assert worker_a.use_redis(os.environ["TEST_REDIS_URI"])
        assert worker_b.use_redis(os.environ["TEST_REDIS_URI"])

        async def scenario() -> str:
            async def work() -> dict[str, int]:
                return {"count": 1}

            job = worker_a.submit("bulk_export", work)
            await _wait_finished(worker_a, job.id)
            return job.id

        job = worker_b.get(asyncio.run(scenario()))
        assert job is not None
        assert job.status == JobStatus.COMPLETED
        assert job.result == {"count": 1}


# =============================================================================
# Search index preparation at startup (D3-13)
# =============================================================================


class _FakeResult:
    def __init__(self, n: int):
        self.n = n

    async def single(self) -> dict[str, int]:
        return {"n": self.n}


class _FakeSession:
    def __init__(self, n: int):
        self.n = n

    async def run(self, query: str) -> _FakeResult:
        assert "count(l)" in query
        return _FakeResult(self.n)


class _FakeCountES:
    def __init__(self, count: int):
        self._count = count

    async def count(self, index: str) -> dict[str, int]:
        return {"count": self._count}


class _FakeLockRedis:
    def __init__(self, held: bool):
        self.held = held
        self.deleted: list[str] = []

    async def set(self, key: str, value: str, nx: bool, ex: int) -> bool | None:
        if self.held:
            return None
        self.held = True
        return True

    async def delete(self, key: str) -> None:
        self.deleted.append(key)
        self.held = False


class _FakeSearchDB:
    def __init__(
        self,
        es_count: int,
        neo4j_count: int,
        es_connected: bool = True,
        redis: _FakeLockRedis | None = None,
    ):
        self.elasticsearch = _FakeCountES(es_count)
        self.neo4j_count = neo4j_count
        self.es_connected = es_connected
        self.redis = redis

    def get_connection_status(self) -> dict[str, dict[str, Any]]:
        return {
            "neo4j": {"connected": True},
            "elasticsearch": {"connected": self.es_connected},
            "redis": {"connected": self.redis is not None},
        }

    @contextlib.asynccontextmanager
    async def neo4j_session(self) -> AsyncIterator[_FakeSession]:
        yield _FakeSession(self.neo4j_count)


class TestSearchIndexStartup:
    """Startup creates the index mapping and backfills a short index."""

    def _run(
        self, monkeypatch: pytest.MonkeyPatch, db: _FakeSearchDB, index_ok: bool = True
    ) -> list[str]:
        calls: list[str] = []

        class FakeRepository:
            def __init__(self, _db: Any):
                pass

            async def ensure_elasticsearch_index(self) -> bool:
                calls.append("ensure")
                return index_ok

            async def reindex_all_to_elasticsearch(self) -> Any:
                calls.append("reindex")
                return type("Result", (), {"errors": [], "succeeded": db.neo4j_count})()

        monkeypatch.setattr(api_main, "LSRRepository", FakeRepository)

        async def scenario() -> None:
            await api_main._prepare_search_index(db)  # type: ignore[arg-type]
            await asyncio.gather(*api_main._background_tasks)

        asyncio.run(scenario())
        return calls

    def test_short_index_is_backfilled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assert self._run(monkeypatch, _FakeSearchDB(es_count=3, neo4j_count=2146)) == [
            "ensure",
            "reindex",
        ]

    def test_complete_index_is_left_alone(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assert self._run(monkeypatch, _FakeSearchDB(es_count=2146, neo4j_count=2146)) == ["ensure"]

    def test_unusable_index_is_rebuilt(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = self._run(monkeypatch, _FakeSearchDB(es_count=0, neo4j_count=5), index_ok=False)
        assert calls == ["ensure", "reindex"]

    def test_nothing_without_elasticsearch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        db = _FakeSearchDB(es_count=0, neo4j_count=5, es_connected=False)
        assert self._run(monkeypatch, db) == []

    def test_one_worker_reindexes_at_a_time(self, monkeypatch: pytest.MonkeyPatch) -> None:
        busy = _FakeLockRedis(held=True)
        db = _FakeSearchDB(es_count=0, neo4j_count=5, redis=busy)
        assert self._run(monkeypatch, db) == ["ensure"]
        # The other worker's lock is left alone
        assert busy.held and busy.deleted == []

        free = _FakeLockRedis(held=False)
        db = _FakeSearchDB(es_count=0, neo4j_count=5, redis=free)
        assert self._run(monkeypatch, db) == ["ensure", "reindex"]
        assert not free.held and free.deleted == ["lexicon:es-reindex-lock"]


def test_json_roundtrip_of_job_state() -> None:
    """Stored job state is plain JSON (no custom types leak into Redis)."""
    redis = FakeSyncRedis()
    registry = JobRegistry()
    registry._redis = redis

    async def scenario() -> str:
        async def work() -> dict[str, Any]:
            return {"when": "1066"}

        job = registry.submit("bulk_export", work, params={"offset": 0})
        await _wait_finished(registry, job.id)
        return job.id

    job_id = asyncio.run(scenario())
    state = json.loads(redis.data[f"lexicon:job:{job_id}"])
    assert state["status"] == "completed"
    assert state["params"] == {"offset": 0}
