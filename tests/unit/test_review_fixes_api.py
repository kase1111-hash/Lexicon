"""Tests for the API fixes from the final review (findings 21-28 and follow-ups).

Covers GRAPH_QUERY_ENABLED and the fail-fast POST /graph/query, bounded bulk
export results, GraphQL language codes and year bounds, rate limiting of
/metrics, the production CORS check, the /lsr/search offset bound, colored
console logs next to a JSON log file, the Elasticsearch reindex retry after
a stale lock, REST language code length, the startup Redis message and the
OpenAPI tag descriptions. One test needs a Neo4j (TEST_NEO4J_URI); the rest
use fakes or an unreachable address.
"""

import asyncio
import io
import json
import logging
import sys
import time
from collections.abc import Iterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from neo4j import AsyncGraphDatabase
from neo4j.exceptions import DriverError, Neo4jError, ServiceUnavailable, SessionExpired

import src.utils.db as db_module
from src.api import jobs as jobs_module
from src.api import main as main_module
from src.api.graphql.schema import schema
from src.api.jobs import JobRegistry, JobStatus
from src.api.main import app, configure_middleware
from src.api.middleware import APIKeyAuthMiddleware, RateLimitMiddleware
from src.api.routes import graph as graph_routes
from src.api.routes import lsr as lsr_routes
from src.config import APIConfig, DatabaseConfig, ErrorTrackingConfig, Settings, get_settings
from src.utils.db import DatabaseConfig as DBManagerConfig
from src.utils.logging import setup_logging

client = TestClient(app)

GRAPH = "/api/v1/graph"


# =============================================================================
# Fakes
# =============================================================================


class _Cursor:
    """An async Neo4j result over canned records; counts the records pulled."""

    def __init__(self, records: list[Any]) -> None:
        self._records = records
        self.pulled = 0

    def __aiter__(self) -> Any:
        return self._iterate()

    async def _iterate(self) -> Any:
        for record in self._records:
            self.pulled += 1
            yield record

    async def single(self) -> Any:
        return self._records[0] if self._records else None


class _Session:
    """A Neo4j session (and transaction) with the driver's retry behaviour.

    execute_read retries retryable errors like the driver does (here after
    `retry_delay` s, for up to 30 s). `run` answers with the records of the
    first marker found in the query, after `run_delay` s, or raises
    `run_error`.
    """

    def __init__(
        self,
        records_by_marker: dict[str, list[Any]] | None = None,
        run_error: BaseException | None = None,
        run_delay: float = 0.0,
        retry_delay: float = 1.0,
    ) -> None:
        self.records_by_marker = records_by_marker or {}
        self.run_error = run_error
        self.run_delay = run_delay
        self.retry_delay = retry_delay
        self.attempts = 0
        self.cursors: list[_Cursor] = []

    async def run(self, query: Any, parameters: dict[str, Any] | None = None) -> _Cursor:
        if self.run_delay:
            await asyncio.sleep(self.run_delay)
        if self.run_error is not None:
            raise self.run_error
        text = getattr(query, "text", query)
        records = next(
            (rows for marker, rows in self.records_by_marker.items() if marker in text), []
        )
        cursor = _Cursor(records)
        self.cursors.append(cursor)
        return cursor

    async def execute_read(self, work: Any, *args: Any, **kwargs: Any) -> Any:
        give_up = time.monotonic() + 30
        while True:
            self.attempts += 1
            try:
                return await work(self, *args, **kwargs)
            except (DriverError, Neo4jError) as e:
                if not e.is_retryable() or time.monotonic() > give_up:
                    raise
            await asyncio.sleep(self.retry_delay)


class _DB:
    """DatabaseManager stand-in handing out one _Session."""

    def __init__(self, session: _Session) -> None:
        self.session = session
        self.sessions_opened = 0

    @asynccontextmanager
    async def neo4j_session(self) -> Any:
        self.sessions_opened += 1
        yield self.session


class _UnreachableNeo4j:
    """A DatabaseManager whose (real) driver can no longer reach Neo4j."""

    @asynccontextmanager
    async def neo4j_session(self) -> Any:
        driver = AsyncGraphDatabase.driver("bolt://127.0.0.1:1", auth=("neo4j", "x"))
        try:
            async with driver.session() as session:
                yield session
        finally:
            await driver.close()


@pytest.fixture
def use_graph_db() -> Iterator[Any]:
    """Serve the graph router from the given database stand-in."""

    def install(db: Any) -> Any:
        app.dependency_overrides[graph_routes.get_db_manager] = lambda: db
        return db

    yield install
    app.dependency_overrides.pop(graph_routes.get_db_manager, None)


# =============================================================================
# POST /graph/query: GRAPH_QUERY_ENABLED (21) and failing fast
# =============================================================================


class TestGraphQueryEndpoint:
    def test_disabled_endpoint_is_403_and_never_queries(self, use_graph_db, monkeypatch) -> None:
        monkeypatch.setattr(get_settings().api, "graph_query_enabled", False)
        db = use_graph_db(_DB(_Session()))
        response = client.post(f"{GRAPH}/query", json={"query": "RETURN range(1, 15000000) AS r"})
        assert response.status_code == 403
        assert response.json()["error"] == "QUERY_DISABLED"
        assert db.sessions_opened == 0

    def test_setting_is_read_from_the_environment(self, monkeypatch) -> None:
        assert APIConfig(_env_file=None).graph_query_enabled is True
        monkeypatch.setenv("GRAPH_QUERY_ENABLED", "false")
        assert APIConfig(_env_file=None).graph_query_enabled is False

    def test_unreachable_database_fails_fast(self, use_graph_db) -> None:
        """The driver retries an unreachable Neo4j; the API stops waiting within seconds."""
        use_graph_db(_UnreachableNeo4j())
        started = time.monotonic()
        response = client.post(
            f"{GRAPH}/query", json={"query": "MATCH (n) RETURN n", "timeout_seconds": 30}
        )
        elapsed = time.monotonic() - started
        assert response.status_code == 503
        assert response.json()["message"] == "Graph database is not available"
        # Previously the full timeout_seconds + 2 (here 32 s)
        assert elapsed < graph_routes._QUERY_START_DEADLINE_SECONDS + 3

    def test_started_query_keeps_its_own_time_limit(self, use_graph_db, monkeypatch) -> None:
        monkeypatch.setattr(graph_routes, "_QUERY_START_DEADLINE_SECONDS", 0.1)
        use_graph_db(_DB(_Session({"RETURN": [{"n": 1}]}, run_delay=0.5)))
        response = client.post(f"{GRAPH}/query", json={"query": "RETURN 1 AS n"})
        assert response.status_code == 200
        assert response.json()["results"] == [{"n": 1}]

    @pytest.mark.parametrize(
        "error", [ServiceUnavailable("connection lost"), SessionExpired("session expired")]
    )
    def test_connection_lost_mid_query_is_503_and_not_retried(
        self, use_graph_db, error: DriverError
    ) -> None:
        db = use_graph_db(_DB(_Session(run_error=error)))
        started = time.monotonic()
        response = client.post(f"{GRAPH}/query", json={"query": "MATCH (n) RETURN n"})
        assert response.status_code == 503
        assert response.json()["message"] == "Graph database is not available"
        assert db.session.attempts == 1
        assert time.monotonic() - started < 1


def _neo4j_available() -> bool:
    from neo4j import GraphDatabase

    config = DBManagerConfig()
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


@pytest.mark.skipif(not _neo4j_available(), reason="set TEST_NEO4J_URI to test against Neo4j")
def test_query_still_runs_in_a_read_transaction(monkeypatch) -> None:
    """Neo4j itself refuses a write that got past the keyword check."""
    monkeypatch.setattr(graph_routes, "validate_read_only_cypher", lambda query: query)
    # The app's driver is bound to the event loop it was created on: use a
    # fresh one inside a single app lifespan
    monkeypatch.setattr(db_module, "_db_manager", None)
    with TestClient(app) as live:
        response = live.post(f"{GRAPH}/query", json={"query": "CREATE (n:ReviewProbe) RETURN n"})
    assert response.status_code == 400
    assert response.json()["message"] == "Only read-only queries are allowed"


# =============================================================================
# Bulk export results are bounded (22)
# =============================================================================


def _export_db(lsr_count: int, vector_size: int = 40) -> _DB:
    lsrs = [
        {
            "l": {
                "id": f"{i:04d}",
                "form_orthographic": f"w{i}",
                "semantic_vector": [0.5] * vector_size,
            }
        }
        for i in range(lsr_count)
    ]
    return _DB(
        _Session(
            {
                "count(l) AS total": [{"total": lsr_count}],
                "ORDER BY l.id": lsrs,
            }
        )
    )


def _lsr_json_size(vector_size: int = 40) -> int:
    lsr = {"id": "0000", "form_orthographic": "w0", "semantic_vector": [0.5] * vector_size}
    return len(json.dumps(lsr, separators=(",", ":")))


class TestBulkExportBounds:
    def test_page_ends_at_the_byte_limit(self, use_graph_db, monkeypatch) -> None:
        monkeypatch.setattr(graph_routes, "_EXPORT_MAX_BYTES", int(_lsr_json_size() * 3.5))
        db = use_graph_db(_export_db(10))
        response = client.post(
            f"{GRAPH}/bulk/export", json={"language": "eng", "include_relationships": False}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["count"] == 3
        assert body["size_limited"] is True
        assert body["truncated"] is True and body["next_offset"] == 3
        # Records past the limit are not read into memory
        page_cursor = db.session.cursors[-1]
        assert page_cursor.pulled == 4

    def test_first_lsr_is_exported_even_if_over_the_limit(self, use_graph_db, monkeypatch) -> None:
        monkeypatch.setattr(graph_routes, "_EXPORT_MAX_BYTES", 10)
        use_graph_db(_export_db(5))
        body = client.post(f"{GRAPH}/bulk/export", json={"language": "eng"}).json()
        assert body["count"] == 1 and body["next_offset"] == 1

    def test_small_page_is_not_size_limited(self, use_graph_db) -> None:
        use_graph_db(_export_db(5))
        body = client.post(f"{GRAPH}/bulk/export", json={"language": "eng"}).json()
        assert body["count"] == 5
        assert body["size_limited"] is False and body["truncated"] is False


async def _run_job(registry: JobRegistry, result: Any) -> str:
    """Submit a job returning `result` and wait until the registry is done with it."""

    async def work() -> Any:
        return result

    job = registry.submit("bulk_export", work)
    await registry._tasks[job.id]
    return job.id


class _SyncRedis:
    """The subset of redis.Redis the registry writes with."""

    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self.deleted: list[str] = []

    def pipeline(self) -> "_SyncRedis":
        return self

    def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.data[key] = value

    def execute(self) -> None:
        return None

    def get(self, key: str) -> str | None:
        return self.data.get(key)

    def delete(self, *keys: str) -> int:
        self.deleted.extend(keys)
        return sum(self.data.pop(key, None) is not None for key in keys)


class TestHeldResultsAreBounded:
    RESULT = {"items": ["x" * 80]}

    def test_oldest_results_in_memory_are_dropped(self, monkeypatch) -> None:
        size = len(json.dumps(self.RESULT))
        monkeypatch.setattr(jobs_module, "_MAX_RESULT_BYTES", int(size * 2.5))

        async def scenario() -> tuple[list[str], JobRegistry]:
            registry = JobRegistry()
            return [await _run_job(registry, self.RESULT) for _ in range(4)], registry

        ids, registry = asyncio.run(scenario())
        assert [registry.get(job_id) is not None for job_id in ids] == [False, False, True, True]
        assert registry.get(ids[-1]).status == JobStatus.COMPLETED
        assert registry.get(ids[-1]).result == self.RESULT
        assert sum(size for _, size in registry._result_sizes.values()) <= size * 2.5

    def test_newest_result_is_kept_even_if_over_the_limit(self, monkeypatch) -> None:
        monkeypatch.setattr(jobs_module, "_MAX_RESULT_BYTES", 10)

        async def scenario() -> tuple[list[str], JobRegistry]:
            registry = JobRegistry()
            return [await _run_job(registry, self.RESULT) for _ in range(2)], registry

        ids, registry = asyncio.run(scenario())
        assert registry.get(ids[0]) is None
        assert registry.get(ids[1]).result == self.RESULT

    def test_results_dropped_are_deleted_from_redis(self, monkeypatch) -> None:
        size = len(json.dumps(self.RESULT))
        monkeypatch.setattr(jobs_module, "_MAX_RESULT_BYTES", int(size * 1.5))
        redis = _SyncRedis()

        async def scenario() -> list[str]:
            registry = JobRegistry()
            registry._redis = registry._redis_writer = redis
            return [await _run_job(registry, self.RESULT) for _ in range(3)]

        ids = asyncio.run(scenario())
        prefix = jobs_module._REDIS_KEY_PREFIX
        for dropped in ids[:2]:
            assert prefix + dropped not in redis.data
            assert f"{prefix}{dropped}:result" not in redis.data
        assert json.loads(redis.data[f"{prefix}{ids[2]}:result"]) == self.RESULT


# =============================================================================
# GraphQL language codes (23) and year bounds
# =============================================================================


class _LanguageSession:
    """Answers the language lookup for the codes stored in the graph."""

    def __init__(self, stored: dict[str, str], params_seen: list[dict[str, Any]]) -> None:
        self.stored = stored
        self.params_seen = params_seen

    async def run(self, query: Any, params: dict[str, Any] | None = None) -> _Cursor:
        if "AS iso_code" not in getattr(query, "text", query):
            return _Cursor([])  # an LSR search: nothing stored
        params = params or {}
        self.params_seen.append(params)
        code = params.get("iso_code")
        rows = [
            {"iso_code": c, "name": n, "family": None, "lsrs": 1, "reconstructed": 0}
            for c, n in self.stored.items()
            if code is None or c == code
        ]
        return _Cursor(rows)


class _LanguageDB:
    def __init__(self, stored: dict[str, str]) -> None:
        self.stored = stored
        self.params_seen: list[dict[str, Any]] = []

    @asynccontextmanager
    async def neo4j_session(self) -> Any:
        yield _LanguageSession(self.stored, self.params_seen)


def _graphql(query: str, db: Any) -> Any:
    return asyncio.run(schema.execute(query, context_value={"db": db}))


class TestGraphQLLanguageCodes:
    def test_iso_639_1_code_finds_the_language(self) -> None:
        db = _LanguageDB({"eng": "English"})
        result = _graphql('{ language(isoCode: "en") { isoCode name } }', db)
        assert result.errors is None
        assert result.data["language"] == {"isoCode": "eng", "name": "English"}
        assert db.params_seen == [{"iso_code": "eng"}]

    def test_invalid_code_is_the_same_error_as_elsewhere(self) -> None:
        db = _LanguageDB({"eng": "English"})
        language = _graphql('{ language(isoCode: "e1") { isoCode } }', db)
        search = _graphql('{ searchLsr(language: "e1") { form } }', db)
        assert language.errors[0].extensions["code"] == "INVALID_LANGUAGE_CODE"
        assert language.errors[0].message == search.errors[0].message
        assert language.data["language"] is None
        assert db.params_seen == []


class TestGraphQLYearBounds:
    @pytest.mark.parametrize(
        "query",
        [
            "{ searchLsr(dateStart: 2101) { form } }",
            "{ searchLsr(dateEnd: 3000) { form } }",
            "{ searchLsr(dateStart: -10001) { form } }",
            '{ detectAnachronisms(text: "the knight rode forth", claimedDate: 2500, '
            'language: "eng") { verdict } }',
        ],
    )
    def test_years_outside_the_lsr_range_are_rejected(self, query: str) -> None:
        result = _graphql(query, _LanguageDB({}))
        assert result.errors[0].extensions["code"] == "VALIDATION_ERROR"
        assert "between -10000 and 2100" in result.errors[0].message

    def test_years_at_the_bounds_are_accepted(self) -> None:
        result = _graphql(
            "{ searchLsr(dateStart: -10000, dateEnd: 2100) { form } }", _LanguageDB({})
        )
        assert result.errors is None
        assert result.data["searchLsr"] == []


# =============================================================================
# /metrics cannot be used to guess API keys without limit (24)
# =============================================================================


def _app_with_metrics(**api: Any) -> TestClient:
    test_app = FastAPI()

    @test_app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    @test_app.get("/metrics")
    async def metrics() -> str:
        return "api_requests_total 1"

    settings = Settings(
        _env_file=None, api=APIConfig(_env_file=None, rate_limit_enabled=True, **api)
    )
    configure_middleware(test_app, settings)
    return TestClient(test_app)


class _MidWindowClock:
    """The rate limiter's `time` module with the wall clock mid-window, so a
    window boundary cannot fall inside the test."""

    def time(self) -> float:
        return 1_800_000_030.0

    def __getattr__(self, name: str) -> Any:
        return getattr(time, name)


class TestMetricsRateLimited:
    def test_guessing_keys_on_metrics_is_rate_limited(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from src.api import middleware as middleware_module

        monkeypatch.setattr(middleware_module, "time", _MidWindowClock())
        monkeypatch.setattr(middleware_module, "peek_db", lambda: None)  # in-process counts
        metrics_client = _app_with_metrics(api_key="s3cret", rate_limit_requests=2)
        statuses = [
            metrics_client.get("/metrics", headers={"X-API-Key": f"guess-{i}"}).status_code
            for i in range(5)
        ]
        assert statuses == [401, 401, 429, 429, 429]
        # The right key cannot be told apart until the window ends
        assert metrics_client.get("/metrics", headers={"X-API-Key": "s3cret"}).status_code == 429

    def test_health_and_docs_stay_exempt(self) -> None:
        exempt_client = _app_with_metrics(api_key="s3cret", rate_limit_requests=1)
        for _ in range(4):
            assert exempt_client.get("/health").status_code == 200
            assert exempt_client.get("/openapi.json").status_code == 200

    @pytest.mark.parametrize("path", sorted(RateLimitMiddleware.EXEMPT_PATHS))
    def test_every_exempt_path_is_public(self, path: str) -> None:
        auth = APIKeyAuthMiddleware(FastAPI(), api_key="s3cret")
        assert auth._is_public_path(path)


# =============================================================================
# Production CORS check (25)
# =============================================================================


def _production_errors(cors_origins: str) -> list[str]:
    settings = Settings(
        _env_file=None,
        database=DatabaseConfig(_env_file=None, neo4j_password="pw"),
        api=APIConfig(
            _env_file=None, cors_origins=cors_origins, api_key="key", rate_limit_enabled=True
        ),
        error_tracking=ErrorTrackingConfig(_env_file=None, environment="production"),
    )
    return settings.validate_required_for_production()


class TestProductionCors:
    @pytest.mark.parametrize(
        "origins", ["*", " *", "https://app.example.com,*", "https://app.example.com , * "]
    )
    def test_wildcard_anywhere_is_an_error(self, origins: str) -> None:
        assert "CORS_ORIGINS should not contain '*' in production" in _production_errors(origins)

    def test_explicit_origins_are_accepted(self) -> None:
        assert _production_errors("https://app.example.com,https://admin.example.com") == []


# =============================================================================
# /lsr/search offset (26) and REST language code length
# =============================================================================


class _SearchRepo:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.search_degraded = True  # keep results out of the cache

    async def search(self, **kwargs: Any) -> tuple[list[Any], int]:
        self.calls.append(kwargs)
        return [], 0


@pytest.fixture
def search_repo() -> Iterator[_SearchRepo]:
    repo = _SearchRepo()
    app.dependency_overrides[lsr_routes.get_lsr_repository] = lambda: repo
    yield repo
    app.dependency_overrides.pop(lsr_routes.get_lsr_repository, None)


class TestSearchBounds:
    @pytest.mark.parametrize("offset", [lsr_routes.MAX_SEARCH_OFFSET + 1, 2**63 - 1, 2**63, 10**19])
    def test_oversized_offset_is_a_400(self, search_repo: _SearchRepo, offset: int) -> None:
        response = client.get("/api/v1/lsr/search", params={"offset": offset})
        assert response.status_code == 400
        assert response.json()["error"] == "VALIDATION_ERROR"
        assert search_repo.calls == []

    def test_largest_offset_is_searched(self, search_repo: _SearchRepo) -> None:
        offset = lsr_routes.MAX_SEARCH_OFFSET
        response = client.get("/api/v1/lsr/search", params={"offset": offset})
        assert response.status_code == 200
        assert search_repo.calls[0]["offset"] == offset

    def test_long_language_codes_are_accepted(self, search_repo: _SearchRepo) -> None:
        response = client.get("/api/v1/lsr/search", params={"language": "ine-bsl-pro"})
        assert response.status_code == 200
        assert search_repo.calls[0]["language"] == "ine-bsl-pro"
        too_long = client.get("/api/v1/lsr/search", params={"language": "a" * 21})
        assert too_long.status_code == 400

    def test_bulk_export_accepts_long_language_codes(self) -> None:
        request = graph_routes.BulkExportRequest(language="ine-bsl-pro")
        assert request.language == "ine-bsl-pro"


# =============================================================================
# Colored console logs do not leak into the JSON log file (27)
# =============================================================================


class _TTY(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_json_log_file_has_plain_levels_when_console_is_colored(tmp_path, monkeypatch) -> None:
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    console = _TTY()
    monkeypatch.setattr(sys, "stderr", console)
    log_file = tmp_path / "app.jsonl"
    try:
        setup_logging(level="INFO", log_file=str(log_file))
        logging.getLogger("review.test").warning("disk almost full")
        logging.getLogger("review.test").error("disk full")
        for handler in root.handlers:
            handler.flush()
        lines = [json.loads(line) for line in log_file.read_text().splitlines()]
    finally:
        for handler in root.handlers:
            handler.close()
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)

    assert [line["level"] for line in lines] == ["WARNING", "ERROR"]
    assert "\033[33mWARNING\033[0m" in console.getvalue()


# =============================================================================
# A stale Elasticsearch reindex lock is retried (28)
# =============================================================================


class _AsyncRedis:
    """The async Redis calls the reindex lock uses."""

    def __init__(self, locked: bool) -> None:
        self.keys: set[str] = {main_module._REINDEX_LOCK_KEY} if locked else set()

    async def set(self, key: str, value: str, nx: bool = False, ex: int | None = None) -> bool:
        if nx and key in self.keys:
            return False
        self.keys.add(key)
        return True

    async def exists(self, key: str) -> int:
        return int(key in self.keys)

    async def delete(self, key: str) -> int:
        present = key in self.keys
        self.keys.discard(key)
        return int(present)


class _SearchIndexDB:
    """Neo4j holds 1000 LSRs, the Elasticsearch index 400."""

    def __init__(self, redis: _AsyncRedis) -> None:
        self.redis = redis
        self.elasticsearch = self

    def get_connection_status(self) -> dict[str, dict[str, Any]]:
        return {
            name: {"configured": True, "connected": True}
            for name in ("neo4j", "elasticsearch", "redis")
        }

    async def count(self, index: str) -> dict[str, int]:
        return {"count": 400}

    @asynccontextmanager
    async def neo4j_session(self) -> Any:
        yield _Session({"count(l)": [{"n": 1000}]})


class _Reindexer:
    reindexed = 0

    def __init__(self, db: Any) -> None:
        pass

    async def ensure_elasticsearch_index(self) -> bool:
        return True

    async def reindex_all_to_elasticsearch(self) -> Any:
        type(self).reindexed += 1

        class Result:
            errors: list[str] = []
            succeeded = 1000

        return Result()


@pytest.fixture
def reindexer(monkeypatch) -> type[_Reindexer]:
    async def no_cache() -> None:
        return None

    _Reindexer.reindexed = 0
    monkeypatch.setattr(main_module, "_background_tasks", set())
    monkeypatch.setattr(main_module, "LSRRepository", _Reindexer)
    monkeypatch.setattr(main_module, "invalidate_search_cache", no_cache)
    monkeypatch.setattr(main_module, "_REINDEX_LOCK_POLL_SECONDS", 0.01)
    return _Reindexer


async def _drain_background_tasks() -> None:
    while main_module._background_tasks:
        await asyncio.gather(*list(main_module._background_tasks))


class TestReindexLock:
    def test_reindex_runs_once_a_stale_lock_expires(self, reindexer) -> None:
        async def scenario() -> tuple[int, int, set[str]]:
            redis = _AsyncRedis(locked=True)  # left by a worker killed mid-reindex
            await main_module._prepare_search_index(_SearchIndexDB(redis))
            await asyncio.sleep(0.05)
            before = reindexer.reindexed
            redis.keys.clear()  # the lock expires
            await _drain_background_tasks()
            return before, reindexer.reindexed, redis.keys

        before, after, keys_left = asyncio.run(scenario())
        assert before == 0
        assert after == 1
        assert keys_left == set()  # the new reindex released its lock

    def test_retry_is_bounded_while_a_live_worker_holds_the_lock(
        self, reindexer, monkeypatch
    ) -> None:
        monkeypatch.setattr(main_module, "_REINDEX_LOCK_SECONDS", 0.05)

        async def scenario() -> int:
            redis = _AsyncRedis(locked=True)  # held throughout
            await main_module._prepare_search_index(_SearchIndexDB(redis))
            await asyncio.wait_for(_drain_background_tasks(), 5)
            return reindexer.reindexed

        assert asyncio.run(scenario()) == 0


# =============================================================================
# Startup message when Redis is not configured
# =============================================================================


class _StartupDB:
    def __init__(self, redis_configured: bool) -> None:
        self.redis_configured = redis_configured
        self.config = type("Config", (), {"redis_uri": "redis://127.0.0.1:1"})()

    def get_connection_status(self) -> dict[str, dict[str, Any]]:
        status = {name: {"configured": True, "connected": True} for name in ("neo4j",)}
        status["postgres"] = {"configured": False, "connected": False}
        status["elasticsearch"] = {"configured": False, "connected": False}
        status["redis"] = {"configured": self.redis_configured, "connected": False}
        return status


@pytest.fixture
def start_api(monkeypatch, caplog) -> Any:
    async def nothing(*args: Any) -> None:
        return None

    for name in ("_ensure_graph_schema", "_prepare_search_index", "_connect_late_stores"):
        monkeypatch.setattr(main_module, name, nothing)
    monkeypatch.setattr(main_module, "close_db", nothing)
    caplog.set_level(logging.INFO, logger="src.api.main")

    def start(db: _StartupDB) -> list[logging.LogRecord]:
        async def get_db() -> _StartupDB:
            return db

        monkeypatch.setattr(main_module, "get_db", get_db)

        async def run() -> None:
            async with main_module.lifespan(app):
                pass

        asyncio.run(run())
        return [record for record in caplog.records if "Redis" in record.getMessage()]

    return start


class TestStartupRedisMessage:
    def test_redis_not_configured_is_said_once_at_info(self, start_api) -> None:
        records = start_api(_StartupDB(redis_configured=False))
        assert [(r.levelno, r.getMessage()) for r in records] == [
            (
                logging.INFO,
                "Redis is not configured: async job state and rate-limit counters are "
                "kept per process, so run a single worker",
            )
        ]

    def test_configured_but_unreachable_redis_is_a_warning(self, start_api) -> None:
        records = start_api(_StartupDB(redis_configured=True))
        assert [r.levelno for r in records] == [logging.WARNING]
        assert records[0].getMessage().startswith("Redis unavailable")


# =============================================================================
# OpenAPI tag descriptions
# =============================================================================


def test_openapi_tags_describe_the_current_routes() -> None:
    tags = {tag["name"]: tag["description"] for tag in client.get("/openapi.json").json()["tags"]}
    analysis = tags["Analysis"].lower()
    for topic in ("dating", "anachronism", "contact", "experimental semantic drift", "across"):
        assert topic in analysis
    assert "etymology" not in analysis
    assert "crud" not in tags["LSR"].lower()
