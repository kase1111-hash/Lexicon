"""Storage review fixes, with fake Neo4j and Elasticsearch clients.

Covers the deadline on searches and writes (finding 29), uncached Neo4j form
searches while a configured Elasticsearch is not connected (30), statistics
failures raised as DatabaseError (31), and fill-only placeholder writes
(8, storage half; the Neo4j side is in tests/integration).
"""

import asyncio
import sys
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from neo4j.exceptions import ServiceUnavailable

import src.api.routes.lsr as lsr_routes
from src import cli
from src.exceptions import DatabaseError
from src.models.lsr import LSR
from src.pipelines import graph_writer
from src.repositories import lsr_repository
from src.repositories.lsr_repository import GRAPH_UNAVAILABLE, BatchResult, LSRRepository

SECRET = "secret-host-10.0.0.7"
# Short stand-ins for READ_TIMEOUT_SECONDS and the client grace period
TIMEOUT, GRACE = 0.05, 0.05
# How long a silent Neo4j stand-in takes to fail (the driver's socket timeout,
# about 120 s in reality); far past the deadline, so an unbounded query fails
# the timing assertions instead of hanging the test run
SILENT_SECONDS = 3


class FakeResult:
    def __init__(self, records: list[Any]) -> None:
        self._records = records

    def __aiter__(self) -> Any:
        return self._iterate()

    async def _iterate(self) -> Any:
        for record in self._records:
            yield record

    async def single(self) -> Any:
        return self._records[0] if self._records else None

    async def fetch(self, n: int) -> list[Any]:
        return self._records[:n]


class FakeDB:
    """A DatabaseManager stand-in.

    Neo4j answers through `answer(query, params)`, which may raise; with
    hang=True it is silent (a paused or partitioned server) until the
    connection fails after SILENT_SECONDS.
    """

    def __init__(
        self,
        answer: Any = None,
        hang: bool = False,
        es: Any = None,
        es_configured: bool = False,
    ) -> None:
        self.answer = answer or (lambda query, params: [])
        self.hang = hang
        self._es = es
        self.config = SimpleNamespace(
            neo4j_uri="bolt://fake:7687",
            elasticsearch_configured=es_configured,
            redis_configured=False,
        )
        self._redis_client = None
        # (query text, server-side timeout) of every query sent
        self.queries: list[tuple[str, Any]] = []
        self.closed = False

    @asynccontextmanager
    async def neo4j_session(self) -> Any:
        db = self

        class Session:
            async def run(self, query: Any, params: dict[str, Any] | None = None) -> FakeResult:
                db.queries.append((getattr(query, "text", query), getattr(query, "timeout", None)))
                if db.hang:
                    await asyncio.sleep(SILENT_SECONDS)
                    raise ServiceUnavailable("Failed to read from defunct connection")
                return FakeResult(db.answer(getattr(query, "text", query), params or {}))

        yield Session()

    @property
    def elasticsearch(self) -> Any:
        if self._es is None:
            raise RuntimeError("Elasticsearch not connected")
        return self._es

    async def connect_neo4j(self) -> bool:
        return True

    def get_connection_errors(self) -> dict[str, str]:
        return {}

    async def close_all(self) -> None:
        self.closed = True


@pytest.fixture
def short_deadline(monkeypatch: pytest.MonkeyPatch) -> float:
    """Shrink the repository deadline; returns the time a hung query may take."""
    monkeypatch.setattr(lsr_repository, "READ_TIMEOUT_SECONDS", TIMEOUT)
    monkeypatch.setattr(lsr_repository, "_CLIENT_DEADLINE_GRACE_SECONDS", GRACE)
    return TIMEOUT + GRACE


def _timed(coro: Any) -> tuple[Any, float]:
    """Run a coroutine; return (its result or the exception it raised, seconds)."""
    start = time.monotonic()
    try:
        outcome = asyncio.run(coro)
    except Exception as e:
        outcome = e
    return outcome, time.monotonic() - start


def _search_answer(query: str, params: dict[str, Any]) -> list[Any]:
    """Neo4j answers for _search_neo4j: no matches."""
    return [{"total": 0}] if "count(l) as total" in query else []


# =============================================================================
# Finding 29: searches and writes get the repository deadline
# =============================================================================


class TestDeadline:
    def test_neo4j_search_of_a_silent_neo4j_fails_at_the_deadline(
        self, short_deadline: float
    ) -> None:
        db = FakeDB(hang=True)
        error, seconds = _timed(LSRRepository(db).search(form="wat"))  # type: ignore[arg-type]
        assert isinstance(error, DatabaseError)
        assert error.message == "LSR search timed out"
        assert seconds < short_deadline + 1

    def test_search_queries_carry_the_server_side_timeout(self, short_deadline: float) -> None:
        db = FakeDB(_search_answer)
        assert asyncio.run(LSRRepository(db).search(form="wat")) == ([], 0)  # type: ignore[arg-type]
        assert len(db.queries) == 2
        assert all(timeout == TIMEOUT for _, timeout in db.queries)

    def test_elasticsearch_hits_of_a_silent_neo4j_fail_without_a_second_wait(
        self, short_deadline: float
    ) -> None:
        """The hits' Neo4j lookup timing out is not an Elasticsearch failure."""

        class ES:
            async def search(self, **kwargs: Any) -> Any:
                return {"hits": {"total": {"value": 1}, "hits": [{"_source": {"id": "x"}}]}}

        db = FakeDB(hang=True, es=ES(), es_configured=True)
        repo = LSRRepository(db)  # type: ignore[arg-type]
        error, seconds = _timed(repo.search(form="wat"))
        assert isinstance(error, DatabaseError)
        assert error.message == "Search result lookup timed out"
        assert len(db.queries) == 1  # no Neo4j substring search after it
        assert seconds < short_deadline + 1
        assert repo.search_degraded is False

    @pytest.mark.parametrize(
        ("operation", "message"),
        [
            (lambda repo: repo.create(LSR(form_orthographic="w", language_code="eng")), "creation"),
            (lambda repo: repo.update(LSR(form_orthographic="w", language_code="eng")), "update"),
            (lambda repo: repo.delete(uuid4()), "deletion"),
        ],
    )
    def test_single_writes_to_a_silent_neo4j_fail_at_the_deadline(
        self, short_deadline: float, operation: Any, message: str
    ) -> None:
        db = FakeDB(hang=True)
        error, seconds = _timed(operation(LSRRepository(db)))  # type: ignore[arg-type]
        assert isinstance(error, DatabaseError)
        assert error.message == f"LSR {message} timed out"
        assert seconds < short_deadline + 1
        assert db.queries[0][1] == TIMEOUT

    def test_batch_writes_to_a_silent_neo4j_fail_at_the_deadline(
        self, short_deadline: float
    ) -> None:
        lsrs = [LSR(form_orthographic=f"w{i}", language_code="eng") for i in range(3)]
        edge = {"source_id": lsrs[0].id, "target_id": lsrs[1].id, "type": "BORROWED_FROM"}
        repo = LSRRepository(FakeDB(hang=True))  # type: ignore[arg-type]

        nodes, seconds = _timed(repo.create_batch(lsrs, batch_size=2))
        assert (nodes.succeeded, nodes.failed) == (0, 3)
        assert nodes.errors == [
            "Batch 0: LSR batch write timed out",
            "Batch 1: LSR batch write timed out",
        ]
        assert seconds < 2 * short_deadline + 1

        edges, seconds = _timed(repo.create_relationships_batch([edge]))
        assert (edges.succeeded, edges.failed) == (0, 1)
        assert edges.errors == [
            "Batch BORROWED_FROM at offset 0: Relationship batch write timed out"
        ]
        assert seconds < short_deadline + 1

    def test_batch_write_errors_leave_out_driver_internals(self) -> None:
        def unavailable(query: str, params: dict[str, Any]) -> list[Any]:
            raise ServiceUnavailable(f"Failed to read from defunct connection {SECRET}")

        lsrs = [LSR(form_orthographic="w", language_code="eng")]
        result = asyncio.run(
            LSRRepository(FakeDB(unavailable)).create_batch(lsrs)  # type: ignore[arg-type]
        )
        assert result.failed == 1
        assert result.errors == [f"Batch 0: {GRAPH_UNAVAILABLE}"]


# =============================================================================
# Finding 30: Neo4j form searches while Elasticsearch is not yet connected
# =============================================================================


class FakeCache:
    def __init__(self) -> None:
        self.sets: list[str] = []

    async def get(self, key: str) -> Any:
        return None

    async def set(self, key: str, value: Any, ttl: int) -> bool:
        self.sets.append(key)
        return True


def _route_search(
    monkeypatch: pytest.MonkeyPatch, repo: LSRRepository, form: str | None
) -> FakeCache:
    """GET /lsr/search through the route function; returns the cache it used."""
    cache = FakeCache()

    async def get_cache() -> FakeCache:
        return cache

    monkeypatch.setattr(lsr_routes, "get_cache", get_cache)
    asyncio.run(
        lsr_routes.search_lsr(
            form=form,
            language="eng",
            date_start=None,
            date_end=None,
            semantic_field=None,
            limit=20,
            offset=0,
            repo=repo,
        )
    )
    return cache


class TestSearchDegraded:
    def test_form_search_without_the_configured_index_is_not_cached(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = LSRRepository(FakeDB(_search_answer, es_configured=True))  # type: ignore[arg-type]
        cache = _route_search(monkeypatch, repo, form="wter")
        assert repo.search_degraded is True
        assert cache.sets == []

    @pytest.mark.parametrize(
        ("es_configured", "form"),
        [
            (False, "wter"),  # no Elasticsearch in this deployment: nothing is missing
            (True, None),  # filter-only searches always run on Neo4j
        ],
    )
    def test_complete_neo4j_searches_are_cached(
        self, monkeypatch: pytest.MonkeyPatch, es_configured: bool, form: str | None
    ) -> None:
        db = FakeDB(_search_answer, es_configured=es_configured)
        repo = LSRRepository(db)  # type: ignore[arg-type]
        cache = _route_search(monkeypatch, repo, form=form)
        assert repo.search_degraded is False
        assert len(cache.sets) == 1


# =============================================================================
# Finding 31: statistics failures raise DatabaseError
# =============================================================================


def _stats_answer(fail_on: str, error: Exception) -> Any:
    def answer(query: str, params: dict[str, Any]) -> list[Any]:
        if fail_on in query:
            raise error
        return [{"total": 7, "cnt": 1, "lang": "eng"}]

    return answer


class TestStatistics:
    @pytest.mark.parametrize(
        ("error", "message"),
        [
            (ServiceUnavailable(f"defunct connection {SECRET}"), GRAPH_UNAVAILABLE),
            (RuntimeError("Neo4j not connected"), GRAPH_UNAVAILABLE),
            (Exception(f"Neo.TransientError {SECRET}"), "Statistics retrieval failed"),
        ],
    )
    def test_a_failed_count_raises_instead_of_returning_partial_counts(
        self, error: Exception, message: str
    ) -> None:
        # The total succeeds, the per-language count fails
        db = FakeDB(_stats_answer("l.language_code AS lang", error))
        with pytest.raises(DatabaseError) as raised:
            asyncio.run(LSRRepository(db).get_statistics())  # type: ignore[arg-type]
        assert raised.value.message == message
        assert SECRET not in raised.value.message

    def test_counts_when_neo4j_answers(self) -> None:
        db = FakeDB(lambda query, params: [{"total": 7, "cnt": 2, "lang": "eng"}])
        stats = asyncio.run(LSRRepository(db).get_statistics())  # type: ignore[arg-type]
        assert stats["total_lsrs"] == 7
        assert stats["by_language"] == {"eng": 2}
        assert stats["total_relationships"] == 10  # five edge types, 2 each
        assert "error" not in stats

    def test_stats_json_exits_nonzero_and_prints_no_result(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        db = FakeDB(_stats_answer("DESCENDS_FROM", ServiceUnavailable(SECRET)))
        monkeypatch.setattr(cli, "DatabaseManager", lambda: db)
        monkeypatch.setattr(sys, "argv", ["lexicon", "stats", "--json"])
        with pytest.raises(SystemExit) as exited:
            cli.main()
        out, err = capsys.readouterr()
        assert exited.value.code == 2
        assert out == ""
        assert GRAPH_UNAVAILABLE in err
        assert db.closed


# =============================================================================
# Finding 8 (storage half): placeholders are written fill-only
# =============================================================================


class TestFillOnlyWrites:
    def test_write_to_graph_writes_only_placeholders_fill_only(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        real = [LSR(form_orthographic=f"real{i}", language_code="eng") for i in range(2)]
        placeholder = LSR(form_orthographic="delfin", language_code="fro")
        calls: list[tuple[list[str], bool]] = []

        async def create_batch(
            self: LSRRepository, lsrs: list[LSR], batch_size: int = 500, fill_only: bool = False
        ) -> BatchResult:
            calls.append(([lsr.form_orthographic for lsr in lsrs], fill_only))
            return BatchResult(succeeded=len(lsrs), index_failed=1)

        async def ensure_schema(self: LSRRepository) -> None:
            return None

        monkeypatch.setattr(LSRRepository, "create_batch", create_batch)
        monkeypatch.setattr(LSRRepository, "ensure_schema", ensure_schema)
        monkeypatch.setattr(LSRRepository, "_has_elasticsearch", lambda self: True)

        result = asyncio.run(
            graph_writer.write_to_graph(
                [real[0], placeholder, real[1]],
                [],
                db=FakeDB(),  # type: ignore[arg-type]
                placeholder_ids=[str(placeholder.id)],
            )
        )

        assert calls == [(["real0", "real1"], False), (["delfin"], True)]
        assert (result.lsrs_written, result.lsrs_failed) == (3, 0)
        assert result.search_index_failed == 2
        assert result.search_index_available is False

    @pytest.fixture
    def indexed(self, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, list[dict[str, Any]]]:
        """A connected Elasticsearch stand-in and the documents bulk-indexed into it."""
        documents: list[dict[str, Any]] = []

        async def async_bulk(es: Any, actions: list[dict[str, Any]], **kwargs: Any) -> Any:
            documents.extend(action["_source"] for action in actions)
            return len(actions), []

        class Indices:
            async def refresh(self, index: str) -> None:
                return None

        async def index_ready(self: LSRRepository) -> bool:
            return True

        monkeypatch.setattr("elasticsearch.helpers.async_bulk", async_bulk)
        monkeypatch.setattr(LSRRepository, "ensure_elasticsearch_index", index_ready)
        return SimpleNamespace(indices=Indices()), documents

    def test_search_index_gets_the_merged_node_not_the_placeholder(
        self, indexed: tuple[Any, list[dict[str, Any]]]
    ) -> None:
        """A placeholder with no gloss must not blank the gloss in Elasticsearch."""
        es, documents = indexed
        placeholder = LSR(
            form_orthographic="delfin", language_code="fro", source_databases=["wiktionary"]
        )
        stored = {
            "id": str(placeholder.id),
            "form_orthographic": "delfin",
            "language_code": "fro",
            "definition_primary": "dolphin",
            "source_databases": ["wold", "wiktionary"],
        }
        db = FakeDB(lambda query, params: [{"l": stored}], es=es)

        result = asyncio.run(
            LSRRepository(db).create_batch([placeholder], fill_only=True)  # type: ignore[arg-type]
        )

        assert (result.succeeded, result.failed, result.index_failed) == (1, 0, 0)
        (document,) = documents
        assert document["definition_primary"] == "dolphin"
        assert document["source_databases"] == ["wold", "wiktionary"]

    def test_a_malformed_stored_node_is_written_but_reported_unindexed(
        self, indexed: tuple[Any, list[dict[str, Any]]]
    ) -> None:
        es, documents = indexed
        placeholder = LSR(form_orthographic="delfin", language_code="fro")
        stored = {"id": str(placeholder.id), "date_source": "GUESSED"}
        db = FakeDB(lambda query, params: [{"l": stored}], es=es)

        result = asyncio.run(
            LSRRepository(db).create_batch([placeholder], fill_only=True)  # type: ignore[arg-type]
        )

        assert (result.succeeded, result.failed, result.index_failed) == (1, 0, 1)
        assert result.errors[0].startswith(f"LSR {placeholder.id} is malformed")
        assert documents == []
