"""LSRRepository storage behaviour with fake Neo4j and Elasticsearch clients.

Covers edge writes whose endpoints are missing (D4-19), the Elasticsearch
index check made once per client, honest bulk/reindex failure reporting and
paging (instead of loading the graph at once), capped relationship ids, and
error messages free of driver internals.
"""

import asyncio
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

import pytest
from neo4j.exceptions import ServiceUnavailable

from src.exceptions import DatabaseError
from src.models.lsr import LSR
from src.repositories import lsr_repository
from src.repositories.lsr_repository import (
    GRAPH_UNAVAILABLE,
    MAX_LINKED_IDS,
    LSRRepository,
    database_error,
)

SECRET = "secret-host-10.0.0.7"


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


class FakeSession:
    def __init__(self, db: "FakeDB") -> None:
        self.db = db

    async def run(self, query: Any, params: dict[str, Any] | None = None) -> FakeResult:
        text = getattr(query, "text", query)
        self.db.queries.append((text, params or {}))
        return FakeResult(self.db.answer(text, params or {}))


class FakeDB:
    """A DatabaseManager whose Neo4j answers through `answer(query, params)`."""

    def __init__(self, answer: Any = None, es: Any = None) -> None:
        self.answer = answer or (lambda query, params: [])
        self.queries: list[tuple[str, dict[str, Any]]] = []
        self._es = es

    @asynccontextmanager
    async def neo4j_session(self) -> Any:
        yield FakeSession(self)

    @property
    def elasticsearch(self) -> Any:
        if self._es is None:
            raise RuntimeError("Elasticsearch not connected")
        return self._es


class FakeIndices:
    def __init__(self, es: "FakeES") -> None:
        self.es = es

    async def exists(self, index: str) -> bool:
        self.es.calls.append("exists")
        return self.es.index_exists

    async def put_mapping(self, index: str, properties: dict[str, Any]) -> None:
        self.es.calls.append("put_mapping")

    async def create(self, index: str, settings: Any, mappings: Any) -> None:
        self.es.calls.append("create")
        self.es.index_exists = True

    async def refresh(self, index: str) -> None:
        self.es.calls.append("refresh")

    async def delete(self, index: str, ignore_unavailable: bool = False) -> None:
        self.es.calls.append("delete")


class FakeES:
    """The Elasticsearch calls the repository makes; documents held in memory."""

    def __init__(self, docs: set[str] | None = None) -> None:
        self.calls: list[str] = []
        self.index_exists = True
        self.docs = set(docs or ())
        self.indices = FakeIndices(self)

    async def index(self, index: str, id: str, document: Any, refresh: bool) -> None:
        self.calls.append("index")
        self.docs.add(id)

    async def search(self, index: str, size: int, sort: Any, source: bool, **kwargs: Any) -> Any:
        after = (kwargs.get("search_after") or [""])[0]
        ids = sorted(d for d in self.docs if d > after)[:size]
        return {"hits": {"hits": [{"_id": i, "sort": [i]} for i in ids]}}


def _fake_bulk(reject: set[str] | None = None) -> Any:
    """Stand-in for elasticsearch.helpers.async_bulk over a FakeES."""
    reject = reject or set()

    async def async_bulk(es: FakeES, actions: list[dict[str, Any]], **kwargs: Any) -> Any:
        done, errors = 0, []
        for action in actions:
            if action["_id"] in reject:
                errors.append(
                    {
                        "index": {
                            "_id": action["_id"],
                            "status": 403,
                            "error": {"type": "cluster_block_exception", "reason": "blocked"},
                        }
                    }
                )
                continue
            if action.get("_op_type") == "delete":
                es.docs.discard(action["_id"])
            else:
                es.docs.add(action["_id"])
            done += 1
        return done, errors

    return async_bulk


@pytest.fixture(autouse=True)
def _fresh_index_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lsr_repository, "_es_index_ready", lsr_repository.weakref.WeakSet())


# =============================================================================
# Relationship batches (D4-19)
# =============================================================================


class TestRelationshipBatch:
    def test_missing_endpoints_are_failures_with_ids(self) -> None:
        a, b, ghost = str(uuid4()), str(uuid4()), str(uuid4())

        def answer(query: str, params: dict[str, Any]) -> list[Any]:
            # Neo4j returns the batch rows whose source or target is missing
            existing = {a, b}
            return [
                {
                    "i": i,
                    "has_source": rel["source_id"] in existing,
                    "has_target": rel["target_id"] in existing,
                }
                for i, rel in enumerate(params["batch"])
                if not (rel["source_id"] in existing and rel["target_id"] in existing)
            ]

        repo = LSRRepository(FakeDB(answer))  # type: ignore[arg-type]
        result = asyncio.run(
            repo.create_relationships_batch(
                [
                    {"source_id": a, "target_id": b, "type": "BORROWED_FROM"},
                    {"source_id": a, "target_id": ghost, "type": "BORROWED_FROM"},
                    {"source_id": ghost, "target_id": ghost, "type": "DESCENDS_FROM"},
                    {"source_id": a, "target_id": b, "type": "BOGUS"},
                ]
            )
        )
        assert (result.succeeded, result.failed) == (1, 3)
        assert f"BORROWED_FROM {a} -> {ghost}: no LSR with the target id" in result.errors
        assert f"DESCENDS_FROM {ghost} -> {ghost}: no LSR with the source or target id" in (
            result.errors
        )
        assert any("BOGUS" in e and "invalid relationship type" in e for e in result.errors)

    def test_query_reports_unmatched_rows(self) -> None:
        db = FakeDB()
        repo = LSRRepository(db)  # type: ignore[arg-type]
        asyncio.run(
            repo.create_relationships_batch(
                [{"source_id": uuid4(), "target_id": uuid4(), "type": "COGNATE_OF"}]
            )
        )
        query = db.queries[0][0]
        assert "OPTIONAL MATCH (source:LSR" in query and "OPTIONAL MATCH (target:LSR" in query
        assert "WHERE NOT (has_source AND has_target)" in query


# =============================================================================
# Elasticsearch
# =============================================================================


class TestElasticsearchIndex:
    def test_index_is_checked_once_per_client(self) -> None:
        es = FakeES()
        repo = LSRRepository(FakeDB(es=es))  # type: ignore[arg-type]
        for _ in range(3):
            asyncio.run(
                repo._index_to_elasticsearch(LSR(form_orthographic="a", language_code="eng"))
            )
        assert es.calls.count("exists") == 1 and es.calls.count("put_mapping") == 1
        assert es.calls.count("index") == 3
        # Another repository on the same client reuses the check; a new client does not
        asyncio.run(LSRRepository(FakeDB(es=es)).ensure_elasticsearch_index())  # type: ignore[arg-type]
        assert es.calls.count("exists") == 1
        other = FakeES()
        asyncio.run(LSRRepository(FakeDB(es=other)).ensure_elasticsearch_index())  # type: ignore[arg-type]
        assert other.calls == ["exists", "put_mapping"]

    def test_failed_check_is_retried(self) -> None:
        es = FakeES()

        async def broken(index: str) -> bool:
            raise ConnectionError("down")

        es.indices.exists = broken  # type: ignore[method-assign]
        repo = LSRRepository(FakeDB(es=es))  # type: ignore[arg-type]
        assert asyncio.run(repo.ensure_elasticsearch_index()) is False
        del es.indices.exists
        assert asyncio.run(repo.ensure_elasticsearch_index()) is True

    def test_bulk_rejections_are_reported(self, monkeypatch: pytest.MonkeyPatch) -> None:
        lsrs = [LSR(form_orthographic=f"w{i}", language_code="eng") for i in range(3)]
        monkeypatch.setattr(
            "elasticsearch.helpers.async_bulk", _fake_bulk(reject={str(lsrs[1].id)})
        )
        repo = LSRRepository(FakeDB(es=FakeES()))  # type: ignore[arg-type]
        result = asyncio.run(repo.index_batch_to_elasticsearch(lsrs))
        assert (result.succeeded, result.failed) == (2, 1)
        assert "cluster_block_exception: blocked" in result.errors[0]

    def test_create_batch_reports_index_failures(self, monkeypatch: pytest.MonkeyPatch) -> None:
        lsrs = [LSR(form_orthographic=f"w{i}", language_code="eng") for i in range(3)]
        monkeypatch.setattr(
            "elasticsearch.helpers.async_bulk",
            _fake_bulk(reject={str(lsr.id) for lsr in lsrs[:2]}),
        )
        es = FakeES()
        db = FakeDB(lambda query, params: [{"written": len(params.get("batch", []))}], es=es)
        result = asyncio.run(LSRRepository(db).create_batch(lsrs))  # type: ignore[arg-type]
        assert (result.succeeded, result.failed, result.index_failed) == (3, 0, 2)
        assert "Search index: 2 of 3 LSRs" in result.errors[0]
        assert es.calls[-1] == "refresh"  # the indexed one is searchable at once


def _node(lsr_id: str) -> dict[str, Any]:
    return {"id": lsr_id, "form_orthographic": f"form-{lsr_id}", "language_code": "eng"}


class TestReindex:
    def _graph(self, ids: list[str]) -> Any:
        """Neo4j answers for the reindex queries over LSRs with these ids."""

        def answer(query: str, params: dict[str, Any]) -> list[Any]:
            if "l.id IS NULL" in query:
                return [{"n": 0}]
            if "l.id > $after" in query:
                page = sorted(i for i in ids if i > params["after"])[: params["limit"]]
                return [{"l": _node(i)} for i in page]
            if "l.id IN $ids" in query:
                return [{"id": i} for i in params["ids"] if i in ids]
            raise AssertionError(query)

        return answer

    def test_pages_through_the_graph_and_prunes_stale_documents(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(lsr_repository, "REINDEX_PAGE_SIZE", 2)
        monkeypatch.setattr("elasticsearch.helpers.async_bulk", _fake_bulk())
        ids = sorted(str(uuid4()) for _ in range(5))
        es = FakeES(docs={"stale-1", ids[0]})
        db = FakeDB(self._graph(ids), es=es)
        result = asyncio.run(LSRRepository(db).reindex_all_to_elasticsearch())  # type: ignore[arg-type]
        assert (result.succeeded, result.failed, result.errors) == (5, 0, [])
        assert es.docs == set(ids)
        pages = [params for query, params in db.queries if "l.id > $after" in query]
        assert [p["after"] for p in pages] == ["", ids[1], ids[3], ids[4]]
        assert all(p["limit"] == 2 for p in pages)

    def test_rejected_documents_are_failures(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Writes Elasticsearch refuses are not counted as indexed."""
        ids = sorted(str(uuid4()) for _ in range(3))
        monkeypatch.setattr("elasticsearch.helpers.async_bulk", _fake_bulk(reject=set(ids)))
        db = FakeDB(self._graph(ids), es=FakeES())
        result = asyncio.run(LSRRepository(db).reindex_all_to_elasticsearch())  # type: ignore[arg-type]
        assert (result.succeeded, result.failed) == (0, 3)
        assert result.errors[0] == "3 of 3 LSRs not indexed"
        assert "cluster_block_exception" in result.errors[1]

    def test_neo4j_failure_is_an_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def answer(query: str, params: dict[str, Any]) -> list[Any]:
            raise ServiceUnavailable(SECRET)

        db = FakeDB(answer, es=FakeES())
        result = asyncio.run(LSRRepository(db).reindex_all_to_elasticsearch())  # type: ignore[arg-type]
        assert result.errors == [f"Reindex failed: {GRAPH_UNAVAILABLE}"]


# =============================================================================
# Reads
# =============================================================================


def _record(lsr_id: str, **counts: int) -> dict[str, Any]:
    ids = {
        name: [str(uuid4()) for _ in range(min(n, MAX_LINKED_IDS))] for name, n in counts.items()
    }
    return {
        "l": _node(lsr_id),
        "ancestor_ids": ids.get("ancestors", []),
        "ancestor_count": counts.get("ancestors", 0),
        "descendant_ids": ids.get("descendants", []),
        "descendant_count": counts.get("descendants", 0),
        "cognate_ids": [],
        "cognate_count": 0,
        "loan_source_ids": ids.get("loan_sources", [])[:1],
        "loan_source_count": counts.get("loan_sources", 0),
        "loan_target_ids": ids.get("loan_targets", []),
        "loan_target_count": counts.get("loan_targets", 0),
    }


class TestRelationshipIds:
    def test_counts_and_truncation_are_reported(self) -> None:
        hub, leaf = str(uuid4()), str(uuid4())
        records = {hub: _record(hub, loan_targets=250, descendants=3), leaf: _record(leaf)}
        db = FakeDB(lambda query, params: [records[params["id"]]])
        repo = LSRRepository(db)  # type: ignore[arg-type]
        lsr = asyncio.run(repo.get_by_id(hub))  # type: ignore[arg-type]
        assert len(lsr.loan_target_ids) == MAX_LINKED_IDS and len(lsr.descendant_ids) == 3
        assert repo.relationship_summary(lsr.id) == {
            "relationship_counts": {
                "ancestors": 0,
                "descendants": 3,
                "cognates": 0,
                "loan_sources": 0,
                "loan_targets": 250,
            },
            "relationship_ids_truncated": True,
        }
        asyncio.run(repo.get_by_id(leaf))  # type: ignore[arg-type]
        assert repo.relationship_summary(leaf)["relationship_ids_truncated"] is False
        assert repo.relationship_summary(uuid4()) == {}

    def test_lists_are_capped_in_the_query(self) -> None:
        columns = lsr_repository._RELATIONSHIP_COLUMNS
        assert columns.count(f"LIMIT {MAX_LINKED_IDS}") == 4
        assert "loan_target_count" in columns and "COUNT {" in columns


class TestErrorMessages:
    """Errors returned to clients carry no driver internals."""

    @pytest.mark.parametrize(
        ("error", "message"),
        [
            (RuntimeError(f"Neo4j not connected {SECRET}"), GRAPH_UNAVAILABLE),
            (ServiceUnavailable(f"Couldn't connect to {SECRET}"), GRAPH_UNAVAILABLE),
            (TimeoutError(), "Cognate retrieval timed out"),
            (ValueError(f"Invalid input {SECRET}"), "Cognate retrieval failed"),
        ],
    )
    def test_mapping(self, error: Exception, message: str) -> None:
        mapped = database_error(error, "Cognate retrieval")
        assert isinstance(mapped, DatabaseError)
        assert mapped.message == message

    def test_read_failures_are_sanitized(self) -> None:
        def answer(query: str, params: dict[str, Any]) -> list[Any]:
            raise ValueError(f"MemoryPoolOutOfMemoryError {SECRET}")

        repo = LSRRepository(FakeDB(answer))  # type: ignore[arg-type]
        for call in (
            repo.get_by_id(uuid4()),
            repo.get_cognates(uuid4()),
            repo.get_ancestors(uuid4()),
            repo.get_descendants(uuid4()),
            repo.get_etymology_chain(uuid4()),
            repo.get_borrowings(uuid4()),
            repo.search(language="eng"),
        ):
            with pytest.raises(DatabaseError) as raised:
                asyncio.run(call)
            assert SECRET not in raised.value.message

    def test_lsr_read_does_not_hang_on_a_silent_neo4j(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A Neo4j that stops answering (paused, partitioned) fails GET /lsr/{id}
        and GraphQL lsr after the read deadline instead of hanging them."""

        class SilentDB(FakeDB):
            @asynccontextmanager
            async def neo4j_session(self) -> Any:
                await asyncio.sleep(3600)
                yield FakeSession(self)

        monkeypatch.setattr(lsr_repository, "READ_TIMEOUT_SECONDS", 0.05)
        monkeypatch.setattr(lsr_repository, "_CLIENT_DEADLINE_GRACE_SECONDS", 0.05)
        repo = LSRRepository(SilentDB())  # type: ignore[arg-type]
        with pytest.raises(DatabaseError) as raised:
            asyncio.run(repo.get_by_id(uuid4()))
        assert raised.value.message == "LSR retrieval timed out"
