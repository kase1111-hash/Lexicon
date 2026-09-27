"""Tests for the graph API router (/api/v1/graph) and its request validation.

The first sections need no database: the read-only Cypher validator, the
Neo4j value serializer, LSR create validation, and error mapping with a fake
database. The last section runs against the configured Neo4j and seeds its
fixtures through LSRRepository, so nodes and edges carry real
neo4j.time.DateTime timestamps exactly as the API writes them.
"""

import asyncio
import csv
import io
import json
import random
import string
import time
from collections.abc import Iterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from neo4j.exceptions import Neo4jError, ServiceUnavailable
from neo4j.graph import Node, Relationship
from neo4j.spatial import CartesianPoint
from neo4j.time import Date, DateTime, Duration

import src.utils.db as db_module
from src.api.jobs import JobStatus, job_registry
from src.api.main import app
from src.api.routes import graph as graph_routes
from src.utils.validation import (
    GraphQueryRequest,
    LSRCreateRequest,
    validate_read_only_cypher,
)

client = TestClient(app)

GRAPH = "/api/v1/graph"
SECRET = "secret-host-10.0.0.7"


def _neo4j_available() -> bool:
    """Check whether the configured Neo4j accepts connections."""
    from neo4j import GraphDatabase

    from src.utils.db import DatabaseConfig

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


# =============================================================================
# Read-only Cypher validation (D3-02)
# =============================================================================

# Every bypass from the audit evidence, plus variants of each technique
BLOCKED_QUERIES = [
    # LOAD CSV (SSRF): single space, double space, newline, tab, comments
    "WITH 1 AS x LOAD CSV FROM 'http://example.com' AS r RETURN r",
    'WITH 1 AS x LOAD  CSV FROM "http://172.17.0.1:18103/internal.csv" AS row RETURN row',
    "WITH 1 AS x LOAD\nCSV FROM 'http://172.17.0.1:18103/x.csv' AS row RETURN row",
    "WITH 1 AS x LOAD\tCSV FROM 'http://172.17.0.1:18103/x.csv' AS row RETURN row",
    "WITH 1 AS x LOAD/**/CSV FROM 'http://172.17.0.1:18103/x.csv' AS row RETURN row",
    "WITH 1 AS x LOAD// comment\nCSV FROM 'http://172.17.0.1:18103/x.csv' AS row RETURN row",
    "WITH 1 AS x load csv from 'http://172.17.0.1:18103/x.csv' AS row RETURN row",
    "MATCH (n) WITH n ORDER BY 1e5LOAD CSV FROM 'http://x/y.csv' AS row RETURN row",
    "WITH 1 AS x ＬＯＡＤ CSV FROM 'http://x/y.csv' AS r RETURN r",
    # dbms.* procedures, however CALL and the namespace are separated
    "WITH 1 AS x CALL\ndbms.listConfig() YIELD name, value RETURN name, value",
    "WITH 1 AS x CALL  dbms.components() YIELD name RETURN name",
    "WITH 1 AS x CALL\ndbms.showCurrentUser() YIELD username RETURN username",
    "WITH 1 AS x CALL `dbms`.components() YIELD name RETURN name",
    "WITH 1 AS x CALL /* c */ dbms.components() YIELD name RETURN name",
    "WITH 1 AS x CALL dbmſ.components() YIELD name RETURN name",
    # A '//' inside a string literal must not hide the rest of the line
    "MATCH (n) WHERE n.a = '//' CALL dbms.components() YIELD name RETURN name",
    # Other procedures, subqueries, database switching, apoc
    "WITH 1 AS x CALL db.labels() YIELD label RETURN label",
    "MATCH (n) WITH n LIMIT 1 CALL { RETURN 1 AS y } RETURN y",
    "WITH 1 AS x USE system MATCH (n) RETURN n",
    "MATCH (n) RETURN apoc.text.join(['a'], ',') AS s",
    "WITH 1 AS x USING PERIODIC COMMIT RETURN x",
    "EXPLAIN SHOW TRANSACTIONS",
    # Writes
    "MATCH (n) FOREACH (x IN [1] | SET n.a = 1)",
    "MATCH (n) DETACH DELETE n",
    "MATCH (n)\nSET\tn.x = 1 RETURN n",
    "MATCH (n) WITH n MERGE (m:X) RETURN m",
    "MATCH (n) REMOVE n.x RETURN n",
    "CREATE (n:LSR {id: 'x'}) RETURN n",
    "/* comment */ CREATE (n) RETURN n",
    "SHOW DATABASES",
    "DROP INDEX lsr_id_unique",
    # Malformed input
    "",
    "   ",
    "MATCH (n) WHERE n.a = 'unterminated RETURN n",
    "MATCH (n) /* unterminated RETURN n",
]

ALLOWED_QUERIES = [
    "MATCH (l:LSR {language_code: $lang}) RETURN l LIMIT 10",
    "match (l:LSR) return count(l) AS c",
    "MATCH(l:LSR)RETURN l LIMIT 1",
    "OPTIONAL MATCH (l:LSR) RETURN l LIMIT 1",
    "UNWIND [1, 2] AS x RETURN x",
    "RETURN 1 AS x",
    "// leading comment\nMATCH (n:LSR) RETURN count(n) AS c",
    "/* leading comment */ MATCH (n:LSR) RETURN count(n) AS c",
    "MATCH (n:LSR) RETURN n.form_orthographic AS f, 'http://x//y' AS s LIMIT 1",
    "MATCH (n:LSR) WHERE n.date_start < 1200 AND n.created_at IS NOT NULL RETURN n LIMIT 5",
    "MATCH (a:LSR)-[r:BORROWED_FROM]->(b:LSR) RETURN a, r, b LIMIT 5",
]


class TestReadOnlyCypherValidation:
    """validate_read_only_cypher and GraphQueryRequest."""

    @pytest.mark.parametrize("query", BLOCKED_QUERIES)
    def test_blocked(self, query: str) -> None:
        with pytest.raises(ValueError):
            validate_read_only_cypher(query)

    @pytest.mark.parametrize("query", ALLOWED_QUERIES)
    def test_allowed(self, query: str) -> None:
        assert validate_read_only_cypher(query) == query

    def test_string_literals_are_not_exempt(self) -> None:
        """Keywords are matched on the raw text; literal values go in parameters."""
        with pytest.raises(ValueError, match="parameters"):
            validate_read_only_cypher("MATCH (l:LSR {form_orthographic: 'call'}) RETURN l")

    def test_request_model_bounds_timeout(self) -> None:
        assert GraphQueryRequest(query="RETURN 1 AS x").timeout_seconds == 10
        with pytest.raises(ValueError):
            GraphQueryRequest(query="RETURN 1 AS x", timeout_seconds=301)
        with pytest.raises(ValueError):
            GraphQueryRequest(query="WITH 1 AS x LOAD  CSV FROM 'http://x' AS r RETURN r")

    @pytest.mark.parametrize("query", BLOCKED_QUERIES[:22])
    def test_endpoint_rejects_bypass(self, query: str) -> None:
        response = client.post(f"{GRAPH}/query", json={"query": query})
        assert response.status_code == 400
        body = response.json()
        assert body["error"] == "VALIDATION_ERROR"
        assert body["message"].startswith("Invalid query:")

    def test_endpoint_rejects_timeout_above_cap(self) -> None:
        response = client.post(
            f"{GRAPH}/query", json={"query": "RETURN 1 AS x", "timeout_seconds": 31}
        )
        assert response.status_code == 400


# =============================================================================
# Neo4j value serialization (D3-01)
# =============================================================================


def _graph_fixture() -> tuple[Node, Node, Relationship]:
    """Build nodes and an edge the way the driver hydrates them from Bolt."""
    from neo4j._codec.hydration.v1.hydration_handler import _GraphHydrator

    hydrator = _GraphHydrator()
    created = DateTime(2026, 9, 27, 2, 16, 17, 600000000)
    a = hydrator.hydrate_node(1, {"LSR"}, {"id": "a", "created_at": created}, "e:1")
    b = hydrator.hydrate_node(2, {"LSR"}, {"id": "b", "created_at": created}, "e:2")
    rel = hydrator.hydrate_relationship(
        10, 1, 2, "BORROWED_FROM", {"confidence": 0.9, "created_at": created}, "r:10", "e:1", "e:2"
    )
    return a, b, rel


class TestSerializer:
    """_serialize_neo4j_value turns every Neo4j type into plain JSON."""

    def test_temporal_values(self) -> None:
        ser = graph_routes._serialize_neo4j_value
        assert ser(DateTime(2026, 1, 2, 3, 4, 5)) == "2026-01-02T03:04:05.000000000"
        assert ser(Date(1066, 10, 14)) == "1066-10-14"
        assert ser(Duration(days=1, hours=2)) == "P1DT2H"

    def test_node_and_relationship_properties_are_converted(self) -> None:
        a, _b, rel = _graph_fixture()
        node = graph_routes._serialize_neo4j_value(a)
        assert node["labels"] == ["LSR"]
        assert node["created_at"].startswith("2026-09-27T02:16:17")
        edge = graph_routes._serialize_neo4j_value(rel)
        assert edge == {
            "type": "BORROWED_FROM",
            "source": "a",
            "target": "b",
            "properties": {"confidence": 0.9, "created_at": edge["properties"]["created_at"]},
        }
        assert isinstance(edge["properties"]["created_at"], str)
        json.dumps([node, edge], allow_nan=False)

    def test_other_values_are_json_safe(self) -> None:
        ser = graph_routes._serialize_neo4j_value
        value = {
            "nan": float("nan"),
            "inf": float("inf"),
            "point": CartesianPoint((1.0, 2.0)),
            "bytes": b"\x00\x01",
            "nested": [{"when": Date(2020, 1, 1)}, (1, 2)],
        }
        out = ser(value)
        assert out["point"] == {"srid": 7203, "coordinates": [1.0, 2.0]}
        assert out["nested"] == [{"when": "2020-01-01"}, [1, 2]]
        json.dumps(out, allow_nan=False)


# =============================================================================
# LSR create validation (D3-23)
# =============================================================================


class TestLSRCreateRequestValidation:
    """LSRCreateRequest validates after sanitizing and rejects garbage."""

    @pytest.mark.parametrize("form", ["   ", "\u0001\u0002", "\t\n", "123", "!!!", "ab�c"])
    def test_rejects_empty_or_garbled_forms(self, form: str) -> None:
        with pytest.raises(ValueError):
            LSRCreateRequest(form_orthographic=form, language_code="eng")

    @pytest.mark.parametrize("code", ["e1n2g", "en-", "x", "abcdefghijk", "e n g", "---"])
    def test_rejects_codes_changed_by_sanitizing(self, code: str) -> None:
        with pytest.raises(ValueError):
            LSRCreateRequest(form_orthographic="water", language_code=code)

    def test_normalizes_valid_input(self) -> None:
        req = LSRCreateRequest(
            form_orthographic="  water  ",
            language_code="EN",
            form_phonetic="\u0000ˈwɔː  tər",
        )
        assert req.form_orthographic == "water"
        assert req.language_code == "eng"
        assert req.form_phonetic == "ˈwɔː tər"
        assert LSRCreateRequest(form_orthographic="*nahts", language_code="gem-pro")

    def test_rejects_unknown_fields(self) -> None:
        with pytest.raises(ValueError):
            LSRCreateRequest(form_orthographic="water", language_code="eng", semantic_fields=["x"])

    def test_rejects_reversed_dates(self) -> None:
        with pytest.raises(ValueError):
            LSRCreateRequest(
                form_orthographic="water", language_code="eng", date_start=1500, date_end=1200
            )

    def test_endpoint_returns_400(self) -> None:
        response = client.post(
            "/api/v1/lsr/", json={"form_orthographic": "   ", "language_code": "eng"}
        )
        assert response.status_code == 400
        response = client.post(
            "/api/v1/lsr/", json={"form_orthographic": "digits", "language_code": "e1n2g"}
        )
        assert response.status_code == 400


# =============================================================================
# Error mapping with a fake database (D3-02, D3-03, D3-18, D3-19)
# =============================================================================


def _neo4j_error(code: str, message: str) -> Neo4jError:
    return Neo4jError._hydrate_neo4j(code=code, message=message)  # noqa: SLF001


class _EmptyResult:
    def __aiter__(self) -> "_EmptyResult":
        return self

    async def __anext__(self) -> Any:
        raise StopAsyncIteration


class _FakeSession:
    """A session (and transaction) whose queries fail with `exc` after `delay` s.

    With `unreachable` set, transaction functions are never started, as when
    the driver keeps retrying to reach a database that is down.
    """

    def __init__(self, exc: BaseException | None, delay: float, unreachable: bool) -> None:
        self.exc = exc
        self.delay = delay
        self.unreachable = unreachable
        self.attempts = 0

    async def run(self, *args: Any, **kwargs: Any) -> Any:
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.exc is not None:
            raise self.exc
        return _EmptyResult()

    async def execute_read(self, work: Any, *args: Any, **kwargs: Any) -> Any:
        if self.unreachable:
            await asyncio.sleep(3600)
        # Like the driver, retry a transaction function that raised a
        # retryable Neo4j error (here at most 3 attempts, without delay)
        for attempt in range(3):
            self.attempts += 1
            try:
                return await work(self, *args, **kwargs)
            except Neo4jError as e:
                if not e.is_retryable() or attempt == 2:
                    raise


class _FakeDB:
    """Stands in for DatabaseManager; every read fails with the given exception."""

    def __init__(self, exc: BaseException | None, delay: float, unreachable: bool) -> None:
        self.exc = exc
        self.delay = delay
        self.unreachable = unreachable
        self.sessions: list[_FakeSession] = []

    @asynccontextmanager
    async def neo4j_session(self) -> Any:
        if isinstance(self.exc, RuntimeError):
            raise self.exc
        session = _FakeSession(self.exc, self.delay, self.unreachable)
        self.sessions.append(session)
        yield session


@pytest.fixture
def fake_db():
    """Install a failing fake database for the graph router."""

    def install(
        exc: BaseException | None, delay: float = 0.0, unreachable: bool = False
    ) -> _FakeDB:
        db = _FakeDB(exc, delay, unreachable)
        app.dependency_overrides[graph_routes.get_db_manager] = lambda: db
        return db

    yield install
    app.dependency_overrides.pop(graph_routes.get_db_manager, None)


class TestGraphErrorMapping:
    """Database failures map to sanitized 400/503 responses."""

    def _query(self, query: str = "MATCH (n:LSR) RETURN n LIMIT 1", **extra: Any) -> Any:
        return client.post(f"{GRAPH}/query", json={"query": query, **extra})

    def test_syntax_error_is_400_with_position(self, fake_db) -> None:
        fake_db(
            _neo4j_error(
                "Neo.ClientError.Statement.SyntaxError",
                f"Invalid input 'R' {SECRET} (line 1, column 10 (offset: 9))",
            )
        )
        response = self._query("MATCH (n RETURN n")
        assert response.status_code == 400
        body = response.json()
        assert body["message"] == "Invalid Cypher syntax"
        assert body["details"]["line"] == 1 and body["details"]["column"] == 10
        assert SECRET not in response.text

    def test_write_rejected_by_read_transaction_is_400(self, fake_db) -> None:
        fake_db(_neo4j_error("Neo.ClientError.Statement.AccessMode", SECRET))
        response = self._query()
        assert response.status_code == 400
        assert response.json()["message"] == "Only read-only queries are allowed"
        assert SECRET not in response.text

    def test_missing_parameter_is_400(self, fake_db) -> None:
        fake_db(
            _neo4j_error(
                "Neo.ClientError.Statement.ParameterMissing", "Expected parameter(s): lang"
            )
        )
        response = self._query("MATCH (n:LSR {language_code: $lang}) RETURN n")
        assert response.status_code == 400
        assert response.json()["details"]["missing_parameters"] == ["lang"]

    def test_server_timeout_is_400_query_timeout(self, fake_db) -> None:
        fake_db(
            _neo4j_error(
                "Neo.ClientError.Transaction.TransactionTimedOutClientConfiguration", SECRET
            )
        )
        response = self._query(timeout_seconds=3)
        assert response.status_code == 400
        assert response.json()["error"] == "QUERY_TIMEOUT"
        assert "3s" in response.json()["message"]
        assert SECRET not in response.text

    def test_out_of_memory_is_400_and_not_retried(self, fake_db) -> None:
        """Retrying a query that ran out of memory would only repeat the load."""
        db = fake_db(_neo4j_error("Neo.TransientError.General.MemoryPoolOutOfMemoryError", SECRET))
        response = self._query("RETURN range(1, 20000000) AS r")
        assert response.status_code == 400
        assert response.json()["error"] == "QUERY_TOO_LARGE"
        assert SECRET not in response.text
        assert [session.attempts for session in db.sessions] == [1]

    def test_other_transient_error_is_503_and_not_retried(self, fake_db) -> None:
        db = fake_db(_neo4j_error("Neo.TransientError.General.DatabaseUnavailable", SECRET))
        response = self._query()
        assert response.status_code == 503
        assert SECRET not in response.text
        assert [session.attempts for session in db.sessions] == [1]

    def test_client_deadline_stops_waiting(self, fake_db, monkeypatch) -> None:
        """Neo4j does not interrupt every query at its timeout; the API gives up anyway."""
        monkeypatch.setattr(graph_routes, "_CLIENT_DEADLINE_GRACE_SECONDS", 0)
        fake_db(None, delay=30)
        started = time.monotonic()
        response = self._query(timeout_seconds=1)
        assert response.status_code == 400
        assert response.json()["error"] == "QUERY_TIMEOUT"
        assert time.monotonic() - started < 10

    def test_unreachable_database_during_query_is_503(self, fake_db, monkeypatch) -> None:
        """Hitting the deadline before Neo4j accepted the query is not a query timeout."""
        monkeypatch.setattr(graph_routes, "_CLIENT_DEADLINE_GRACE_SECONDS", 0)
        fake_db(None, unreachable=True)
        response = self._query(timeout_seconds=1)
        assert response.status_code == 503
        assert response.json()["message"] == "Graph database is not available"

    def test_non_query_client_error_is_503(self, fake_db) -> None:
        """An API-to-Neo4j auth failure is not the caller's fault."""
        fake_db(_neo4j_error("Neo.ClientError.Security.Unauthorized", SECRET))
        response = self._query()
        assert response.status_code == 503
        assert SECRET not in response.text

    def test_database_failure_is_503_without_internals(self, fake_db) -> None:
        fake_db(ServiceUnavailable(f"defunct connection IPv4Address(('{SECRET}', 7687))"))
        for response in (
            self._query(),
            client.get(f"{GRAPH}/etymology/{uuid4()}"),
            client.get(f"{GRAPH}/cognates/{uuid4()}"),
            client.get(f"{GRAPH}/path", params={"from_lsr": uuid4(), "to_lsr": uuid4()}),
            client.post(f"{GRAPH}/bulk/export", json={"language": "eng"}),
        ):
            assert response.status_code == 503
            assert SECRET not in response.text

    def test_not_connected_is_503(self, fake_db) -> None:
        fake_db(RuntimeError(f"Neo4j not connected {SECRET}"))
        response = client.get(f"{GRAPH}/etymology/{uuid4()}")
        assert response.status_code == 503
        assert response.json()["message"] == "Graph database is not available"

    def test_path_rejects_unknown_relationship_types(self, fake_db) -> None:
        fake_db(None)
        response = client.get(
            f"{GRAPH}/path",
            params={"from_lsr": uuid4(), "to_lsr": uuid4(), "relationship_types": "BOGUS"},
        )
        assert response.status_code == 400
        assert "BOGUS" in response.json()["message"]

    def test_path_rejects_same_endpoints(self, fake_db) -> None:
        fake_db(None)
        lsr_id = str(uuid4())
        response = client.get(f"{GRAPH}/path", params={"from_lsr": lsr_id, "to_lsr": lsr_id})
        assert response.status_code == 400

    @pytest.mark.parametrize(
        "body",
        [
            {"language": ""},
            {"language": "e1n2g"},
            {"language": "eng", "offset": 99999999999999999999},
            {"language": "eng", "limit": 10001},
            {"language": "eng", "format": "xml"},
        ],
    )
    def test_bulk_export_validates_request(self, fake_db, body: dict[str, Any]) -> None:
        fake_db(None)
        response = client.post(f"{GRAPH}/bulk/export", json=body)
        assert response.status_code == 400

    def test_unknown_export_job_is_404(self) -> None:
        for path in ("status", "result"):
            response = client.get(f"{GRAPH}/bulk/{path}/doesnotexist")
            assert response.status_code == 404
            assert response.json()["error"] == "NOT_FOUND"

    def test_failed_export_job_hides_db_error(self) -> None:
        async def scenario() -> tuple[Any, Any]:
            db: Any = _FakeDB(ServiceUnavailable(f"defunct connection {SECRET}"), 0.0, False)
            request = graph_routes.BulkExportRequest(language="eng", run_async=True)
            accepted = await graph_routes.create_bulk_export(request, db=db)
            for _ in range(100):
                job = job_registry.get(accepted["job_id"])
                if job is not None and job.status in (JobStatus.COMPLETED, JobStatus.FAILED):
                    break
                await asyncio.sleep(0.01)
            status = await graph_routes.get_export_status(accepted["job_id"])
            try:
                await graph_routes.get_export_result(accepted["job_id"])
            except Exception as e:  # noqa: BLE001 - asserting on the raised error
                return status, e
            return status, None

        status, error = asyncio.run(scenario())
        assert status["status"] == "failed"
        assert SECRET not in json.dumps(status)
        assert error is not None and SECRET not in str(error)


# =============================================================================
# Live graph tests (seeded through LSRRepository)
# =============================================================================

# key: (form, language); lineage edges below are DESCENDS_FROM child -> parent
_LINEAGE = {
    "pie": ("*nókʷts", "ine"),
    "pgmc": ("*nahts", "gem-pro"),
    "oe": ("niht", "ang"),
    "me": ("night", "enm"),
    "en": ("night", "eng"),
    "ohg": ("naht", "goh"),
    "de": ("Nacht", "deu"),
    "la": ("nox", "lat"),
    "fr": ("nuit", "fra"),
    "en2": ("nyctal", "eng"),
    "sa": ("nákt", "san"),
}
_DESCENT = [
    ("pgmc", "pie"),
    ("oe", "pgmc"),
    ("me", "oe"),
    ("en", "me"),
    ("ohg", "pgmc"),
    ("de", "ohg"),
    ("la", "pie"),
    ("fr", "la"),
    ("en2", "la"),
]
# ISO 639-3 reserves qaa-qtz for local use, so no real data shares it; the
# random suffix keeps leftovers of an interrupted run out of this run's counts
_EXPORT_LANGUAGE = "qaa-" + "".join(random.choices(string.ascii_lowercase, k=4))
_DAG_GENERATIONS = 22


async def _seed(ids: dict[str, Any]) -> None:
    """Write the fixture graph through LSRRepository, as ingestion does."""
    from src.models.lsr import LSR
    from src.repositories.lsr_repository import LSRRepository

    repo = LSRRepository(await db_module.get_db())
    for key, (form, lang) in _LINEAGE.items():
        created = await repo.create(LSR(form_orthographic=form, language_code=lang))
        ids[key] = str(created.id)
    rels = [
        {"source_id": ids[a], "target_id": ids[b], "type": "DESCENDS_FROM", "confidence": 0.9}
        for a, b in _DESCENT
    ]
    rels.append(
        {
            "source_id": ids["en"],
            "target_id": ids["sa"],
            "type": "COGNATE_OF",
            "confidence": 0.8,
        }
    )

    export_ids = []
    for i in range(5):
        created = await repo.create(
            LSR(form_orthographic=f"exportword{i}", language_code=_EXPORT_LANGUAGE)
        )
        export_ids.append(str(created.id))
    ids["export"] = sorted(export_ids)
    rels.append(
        {
            "source_id": ids["export"][0],
            "target_id": ids["la"],
            "type": "BORROWED_FROM",
            "confidence": 0.7,
        }
    )

    # Duplicated generations: every node descends from both nodes of the
    # previous generation, so the number of paths doubles per generation
    root = await repo.create(LSR(form_orthographic="dagroot", language_code="ine"))
    dag_ids = [str(root.id)]
    previous = [str(root.id)]
    for generation in range(_DAG_GENERATIONS):
        current = []
        for k in range(2):
            node = await repo.create(
                LSR(form_orthographic=f"dag{generation}x{k}", language_code="gem-pro")
            )
            current.append(str(node.id))
            rels.extend(
                {"source_id": str(node.id), "target_id": parent, "type": "DESCENDS_FROM"}
                for parent in previous
            )
        dag_ids.extend(current)
        previous = current
    ids["dag_root"], ids["dag_leaf"], ids["dag"] = str(root.id), previous[0], dag_ids

    result = await repo.create_relationships_batch(rels)
    assert result.failed == 0


async def _cleanup(ids: dict[str, Any]) -> None:
    flat = [v for v in ids.values() if isinstance(v, str)]
    flat += ids.get("export", []) + ids.get("dag", [])
    await _delete_lsrs(flat)


async def _delete_lsrs(lsr_ids: list[str]) -> None:
    db = await db_module.get_db()
    async with db.neo4j_session() as session:
        await session.run("MATCH (l:LSR) WHERE l.id IN $ids DETACH DELETE l", {"ids": lsr_ids})


_WIDE_FANOUT = 4000


async def _seed_wide_lineage(ids: dict[str, Any]) -> None:
    """A word with thousands of descendants whose parent has thousands of other children."""
    from src.models.lsr import LSR
    from src.repositories.lsr_repository import LSRRepository

    repo = LSRRepository(await db_module.get_db())
    root = LSR(form_orthographic="wideroot", language_code="ine")
    word = LSR(form_orthographic="wideword", language_code="gem-pro")
    descendants = [
        LSR(form_orthographic=f"widedesc{i}", language_code="ang") for i in range(_WIDE_FANOUT)
    ]
    siblings = [
        LSR(form_orthographic=f"widesib{i}", language_code="lat") for i in range(_WIDE_FANOUT)
    ]
    lsrs = [root, word, *descendants, *siblings]
    ids["all"] = [str(lsr.id) for lsr in lsrs]
    ids["word"] = str(word.id)
    assert (await repo.create_batch(lsrs)).failed == 0
    rels = [{"source_id": str(word.id), "target_id": str(root.id), "type": "DESCENDS_FROM"}]
    rels += [
        {"source_id": str(d.id), "target_id": str(word.id), "type": "DESCENDS_FROM"}
        for d in descendants
    ]
    rels += [
        {"source_id": str(s.id), "target_id": str(root.id), "type": "DESCENDS_FROM"}
        for s in siblings
    ]
    assert (await repo.create_relationships_batch(rels, batch_size=1000)).failed == 0


@pytest.fixture(scope="module")
def live() -> Iterator[TestClient]:
    """One app lifespan (and event loop) for the live tests.

    The app's Neo4j driver is bound to the event loop it was created on, and
    a TestClient used without `with` runs every request on a new loop.
    """
    db_module._db_manager = None  # never reuse a manager bound to another loop
    with TestClient(app) as test_client:
        yield test_client
    db_module._db_manager = None


@pytest.fixture(scope="module")
def seeded(live: TestClient) -> Iterator[dict[str, Any]]:
    ids: dict[str, Any] = {}
    assert live.portal is not None
    try:
        live.portal.call(_seed, ids)
        yield ids
    finally:
        live.portal.call(_cleanup, ids)


@pytest.fixture(scope="module")
def wide_lineage(live: TestClient) -> Iterator[dict[str, Any]]:
    ids: dict[str, Any] = {}
    assert live.portal is not None
    try:
        live.portal.call(_seed_wide_lineage, ids)
        yield ids
    finally:
        live.portal.call(_delete_lsrs, ids.get("all", []))


def _ids(nodes: list[dict[str, Any]]) -> list[str]:
    return [node["id"] for node in nodes]


@requires_db
class TestGraphLive:
    """Graph endpoints against a real Neo4j."""

    def test_query_serializes_repository_nodes(self, live, seeded) -> None:
        """The documented example returns nodes whose created_at is a DateTime (D3-01)."""
        response = live.post(
            f"{GRAPH}/query",
            json={
                "query": "MATCH (l:LSR {language_code: $lang}) RETURN l LIMIT 10",
                "parameters": {"lang": _EXPORT_LANGUAGE},
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["count"] == 5 and body["truncated"] is False
        assert all(isinstance(row["l"]["created_at"], str) for row in body["results"])

    def test_query_serializes_relationships(self, live, seeded) -> None:
        response = live.post(
            f"{GRAPH}/query",
            json={
                "query": "MATCH (a:LSR {id: $id})-[r]->(b:LSR) RETURN a, r, b",
                "parameters": {"id": seeded["en"]},
            },
        )
        assert response.status_code == 200
        rows = response.json()["results"]
        assert {row["r"]["type"] for row in rows} == {"DESCENDS_FROM", "COGNATE_OF"}
        assert all(isinstance(row["r"]["properties"]["created_at"], str) for row in rows)

    def test_query_caps_rows(self, live, seeded) -> None:
        response = live.post(
            f"{GRAPH}/query", json={"query": "UNWIND range(1, 5000) AS x RETURN x"}
        )
        body = response.json()
        assert body["count"] == 1000 and body["truncated"] is True

    def test_query_caps_bytes(self, live, seeded) -> None:
        response = live.post(f"{GRAPH}/query", json={"query": "RETURN range(1, 2000000) AS r"})
        assert response.status_code == 200
        body = response.json()
        assert body["truncated"] is True and "size" in body["truncated_reason"]
        assert len(response.content) < 6_000_000

    def test_query_syntax_error_is_400(self, live, seeded) -> None:
        response = live.post(f"{GRAPH}/query", json={"query": "MATCH (n RETURN n"})
        assert response.status_code == 400
        assert response.json()["details"]["neo4j_code"].endswith("SyntaxError")

    def test_query_timeout(self, live, seeded) -> None:
        query = (
            "MATCH (a:LSR), (b:LSR), (c:LSR) "
            "WHERE a.form_orthographic + b.form_orthographic + c.form_orthographic = 'zz' "
            "RETURN count(*) AS n"
        )
        started = time.monotonic()
        response = live.post(f"{GRAPH}/query", json={"query": query, "timeout_seconds": 1})
        # Small graphs can finish within the limit; big ones must stop at it
        if response.status_code == 400:
            assert response.json()["error"] == "QUERY_TIMEOUT"
        else:
            assert response.status_code == 200
        assert time.monotonic() - started < 10

    def test_etymology_chain(self, live, seeded) -> None:
        response = live.get(f"{GRAPH}/etymology/{seeded['en']}")
        assert response.status_code == 200
        body = response.json()
        expected = [seeded[k] for k in ("en", "me", "oe", "pgmc", "pie")]
        assert _ids(body["chain"]) == expected
        assert body["depth"] == 4 and body["truncated"] is False
        assert body["proto_form"]["id"] == seeded["pie"]
        assert [(r["source"], r["target"]) for r in body["relationships"]] == list(
            zip(expected, expected[1:], strict=False)
        )
        assert isinstance(body["chain"][0]["created_at"], str)

    def test_etymology_respects_max_depth(self, live, seeded) -> None:
        """max_depth bounds the traversal (D3-17)."""
        response = live.get(f"{GRAPH}/etymology/{seeded['en']}", params={"max_depth": 2})
        body = response.json()
        assert _ids(body["chain"]) == [seeded["en"], seeded["me"], seeded["oe"]]
        assert body["depth"] == 2 and body["truncated"] is True

    def test_etymology_of_root_is_itself(self, live, seeded) -> None:
        body = live.get(f"{GRAPH}/etymology/{seeded['pie']}").json()
        assert _ids(body["chain"]) == [seeded["pie"]] and body["depth"] == 0

    def test_etymology_is_not_exponential(self, live, seeded) -> None:
        """Duplicated generations must not blow up the traversal (D3-24)."""
        started = time.monotonic()
        response = live.get(f"{GRAPH}/etymology/{seeded['dag_leaf']}", params={"max_depth": 50})
        assert response.status_code == 200
        assert time.monotonic() - started < 5
        body = response.json()
        assert body["depth"] == _DAG_GENERATIONS and body["proto_form"]["id"] == seeded["dag_root"]

    def test_cognates_exclude_lineage_and_language(self, live, seeded) -> None:
        """Ancestors and same-language forms are not cognates (D3-09)."""
        response = live.get(f"{GRAPH}/cognates/{seeded['en']}")
        assert response.status_code == 200
        body = response.json()
        found = {c["id"] for group in body["by_language"].values() for c in group}
        assert found == {seeded[k] for k in ("ohg", "de", "la", "fr", "sa")}
        assert body["cognate_count"] == 5
        assert sorted(body["languages"]) == ["deu", "fra", "goh", "lat", "san"]

    def test_cognates_exclude_descendants(self, live, seeded) -> None:
        body = live.get(f"{GRAPH}/cognates/{seeded['oe']}").json()
        found = {c["id"] for group in body["by_language"].values() for c in group}
        assert found == {seeded[k] for k in ("ohg", "de", "la", "fr", "en2")}

    def test_cognates_of_word_with_many_descendants(self, live, wide_lineage) -> None:
        """Thousands of descendants and relatives must not exhaust transaction memory."""
        response = live.get(f"{GRAPH}/cognates/{wide_lineage['word']}")
        assert response.status_code == 200
        body = response.json()
        assert body["cognate_count"] == 100 and body["truncated"] is True
        assert body["languages"] == ["lat"]

    def test_path_has_direction(self, live, seeded) -> None:
        response = live.get(
            f"{GRAPH}/path", params={"from_lsr": seeded["en"], "to_lsr": seeded["de"]}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["paths_found"] == 1
        path = body["paths"][0]
        assert _ids(path["nodes"]) == [seeded[k] for k in ("en", "me", "oe", "pgmc", "ohg", "de")]
        edges = [(r["source"], r["type"], r["target"]) for r in path["relationships"]]
        assert (seeded["ohg"], "DESCENDS_FROM", seeded["pgmc"]) in edges
        assert all(isinstance(r["properties"]["created_at"], str) for r in path["relationships"])

    def test_path_type_filter(self, live, seeded) -> None:
        params = {"from_lsr": seeded["en"], "to_lsr": seeded["de"]}
        body = live.get(
            f"{GRAPH}/path", params={**params, "relationship_types": "BORROWED_FROM"}
        ).json()
        assert body["paths_found"] == 0
        body = live.get(
            f"{GRAPH}/path", params={**params, "relationship_types": "related_to,DESCENDS_FROM"}
        ).json()
        assert body["paths_found"] == 1

    def test_unknown_ids_are_404(self, live, seeded) -> None:
        missing = str(uuid4())
        for response in (
            live.get(f"{GRAPH}/etymology/{missing}"),
            live.get(f"{GRAPH}/cognates/{missing}"),
            live.get(f"{GRAPH}/path", params={"from_lsr": seeded["en"], "to_lsr": missing}),
        ):
            assert response.status_code == 404
            assert response.json()["error"] == "LSR_NOT_FOUND"

    def test_bulk_export_pages(self, live, seeded) -> None:
        """Exports report total/truncated and page by offset (D3-21)."""
        body = live.post(
            f"{GRAPH}/bulk/export", json={"language": _EXPORT_LANGUAGE, "limit": 2}
        ).json()
        assert body["count"] == 2 and body["total"] == 5
        assert body["truncated"] is True and body["next_offset"] == 2
        assert _ids(body["items"]) == seeded["export"][:2]
        body = live.post(
            f"{GRAPH}/bulk/export", json={"language": _EXPORT_LANGUAGE, "offset": 4}
        ).json()
        assert _ids(body["items"]) == seeded["export"][4:]
        assert body["truncated"] is False and body["next_offset"] is None

    def test_bulk_export_csv_includes_relationships(self, live, seeded) -> None:
        response = live.post(
            f"{GRAPH}/bulk/export", json={"language": _EXPORT_LANGUAGE, "format": "csv"}
        )
        assert response.status_code == 200
        body = response.json()
        rows = list(csv.DictReader(io.StringIO(body["csv"])))
        assert sorted(row["id"] for row in rows) == seeded["export"]
        assert json.loads(rows[0]["labels"]) == ["LSR"]
        assert "['" not in body["csv"]
        rels = list(csv.DictReader(io.StringIO(body["relationships_csv"])))
        assert body["relationship_count"] == 1
        assert (rels[0]["source"], rels[0]["type"], rels[0]["target"]) == (
            seeded["export"][0],
            "BORROWED_FROM",
            seeded["la"],
        )
        assert json.loads(rels[0]["properties"])["confidence"] == 0.7

    def test_bulk_export_maps_iso_639_1(self, live, seeded) -> None:
        body = live.post(f"{GRAPH}/bulk/export", json={"language": "en", "limit": 1}).json()
        assert body["language"] == "eng"

    def test_async_export_result_serializes(self, live, seeded) -> None:
        accepted = live.post(
            f"{GRAPH}/bulk/export",
            json={"language": _EXPORT_LANGUAGE, "run_async": True},
        ).json()
        for _ in range(100):
            status = live.get(f"{GRAPH}/bulk/status/{accepted['job_id']}").json()
            if status["status"] in ("completed", "failed"):
                break
            time.sleep(0.05)
        assert status["status"] == "completed"
        response = live.get(f"{GRAPH}/bulk/result/{accepted['job_id']}")
        assert response.status_code == 200
        assert response.json()["count"] == 5
        assert isinstance(response.json()["items"][0]["created_at"], str)
