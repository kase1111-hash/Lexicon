"""Storage review fixes that need a real Neo4j.

Fill-only placeholder writes (finding 8, storage half), and the repository's
bounded searches, writes and statistics against a working server (29, 31).

Run with a throwaway database, e.g.:
    TEST_NEO4J_URI=bolt://localhost:7688 TEST_NEO4J_PASSWORD=... \
        pytest tests/integration/test_review_fixes_storage.py
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar
from uuid import uuid4

import pytest

from src.exceptions import LSRNotFoundError
from src.models.lsr import LSR, DateSource
from src.pipelines.graph_writer import GraphWriteResult, write_to_graph
from src.repositories.lsr_repository import ES_INDEX_NAME, LSRRepository
from src.utils.db import DatabaseManager

T = TypeVar("T")


def _neo4j_reachable() -> bool:
    async def check() -> bool:
        db = DatabaseManager()
        ok = await db.connect_neo4j()
        await db.close_all()
        return ok

    try:
        return asyncio.run(check())
    except Exception:
        return False


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not _neo4j_reachable(),
        reason="requires a reachable Neo4j (set TEST_NEO4J_URI / TEST_NEO4J_PASSWORD)",
    ),
]


def _with_graph(fn: Callable[[DatabaseManager], Awaitable[T]], tag: str) -> T:
    """Run fn against a connected Neo4j, then delete the LSRs whose form contains tag."""

    async def run() -> T:
        db = DatabaseManager()
        assert await db.connect_neo4j()
        try:
            return await fn(db)
        finally:
            async with db.neo4j_session() as session:
                await session.run(
                    "MATCH (l:LSR) WHERE l.form_normalized CONTAINS $tag DETACH DELETE l",
                    {"tag": tag},
                )
            await db.close_all()

    return asyncio.run(run())


async def _node(db: DatabaseManager, lsr_id: Any) -> dict[str, Any]:
    async with db.neo4j_session() as session:
        result = await session.run("MATCH (l:LSR {id: $id}) RETURN l", {"id": str(lsr_id)})
        record = await result.single()
        return dict(record["l"])


def _placeholder(lsr_id: Any, form: str, **fields: Any) -> LSR:
    """A donor LSR as ingestion makes it: undated, known only by name."""
    defaults: dict[str, Any] = {
        "language_code": "fro",
        "date_confidence": 0.0,
        "source_databases": ["wiktionary"],
    }
    return LSR(id=lsr_id, form_orthographic=form, **{**defaults, **fields})


class TestFillOnlyPlaceholders:
    def test_placeholder_written_twice_keeps_the_first_gloss_and_both_sources(self) -> None:
        tag = uuid4().hex[:10]
        form, lsr_id = f"delfin{tag}", uuid4()
        wold = _placeholder(lsr_id, form, definition_primary="dolphin", source_databases=["wold"])
        wiktionary = _placeholder(
            lsr_id, form, language_name="Old French", source_databases=["wiktionary"]
        )

        async def run(db: DatabaseManager) -> tuple[list[GraphWriteResult], dict[str, Any]]:
            results = [
                await write_to_graph([lsr], [], db=db, placeholder_ids=[str(lsr_id)])
                for lsr in (wold, wiktionary)
            ]
            return results, await _node(db, lsr_id)

        results, node = _with_graph(run, tag)

        assert [(r.lsrs_written, r.lsrs_failed) for r in results] == [(1, 0), (1, 0)]
        assert node["definition_primary"] == "dolphin"
        assert sorted(node["source_databases"]) == ["wiktionary", "wold"]
        assert node["language_name"] == "Old French"  # missing before, so gained
        assert node["date_confidence"] == 0.0
        assert node.get("date_start") is None and node.get("date_end") is None
        assert node["created_at"] < node["updated_at"]

    def test_the_search_index_gets_the_merged_placeholder(self) -> None:
        tag = uuid4().hex[:10]
        form, lsr_id = f"delfin{tag}", uuid4()
        wold = _placeholder(lsr_id, form, definition_primary="dolphin", source_databases=["wold"])
        wiktionary = _placeholder(lsr_id, form, source_databases=["wiktionary"])

        async def run(db: DatabaseManager) -> dict[str, Any]:
            if not await db.connect_elasticsearch(quiet=True):
                pytest.skip("Elasticsearch is not reachable (set TEST_ELASTICSEARCH_URI)")
            try:
                for lsr in (wold, wiktionary):
                    result = await write_to_graph([lsr], [], db=db, placeholder_ids=[str(lsr_id)])
                    assert (result.search_index_failed, result.search_index_available) == (0, True)
                document = await db.elasticsearch.get(index=ES_INDEX_NAME, id=str(lsr_id))
                return dict(document["_source"])
            finally:
                await LSRRepository(db)._remove_from_elasticsearch(lsr_id)

        document = _with_graph(run, tag)

        assert document["definition_primary"] == "dolphin"
        assert sorted(document["source_databases"]) == ["wiktionary", "wold"]

    def test_a_real_record_is_never_blanked_by_a_placeholder(self) -> None:
        tag = uuid4().hex[:10]
        form = f"sky{tag}"
        real = LSR(
            form_orthographic=form,
            language_code="eng",
            language_name="English",
            date_start=1220,  # still in use: no date_end
            date_confidence=0.8,
            definition_primary="the sky",
            definitions_alternate=["heaven"],
            semantic_fields=["sky.n.01"],
            etymology_text="Borrowed from Old Norse",
            part_of_speech=["noun"],
            confidence_overall=0.9,
            source_databases=["wold"],
        )
        # Same id, emptier, with a different (and complete) dating of its own
        placeholder = LSR(
            id=real.id,
            form_orthographic=form,
            language_code="eng",
            date_start=1300,
            date_end=1400,
            date_confidence=0.2,
            date_source=DateSource.INTERPOLATED,
            reconstruction_flag=True,
            confidence_overall=0.1,
            source_databases=["wiktionary"],
        )

        async def run(db: DatabaseManager) -> tuple[dict[str, Any], dict[str, Any]]:
            await write_to_graph([real], [], db=db)
            before = await _node(db, real.id)
            result = await write_to_graph([placeholder], [], db=db, placeholder_ids={str(real.id)})
            assert (result.lsrs_written, result.lsrs_failed) == (1, 0)
            return before, await _node(db, real.id)

        before, after = _with_graph(run, tag)

        unchanged = set(before) - {"source_databases", "updated_at"}
        assert {key: after.get(key) for key in unchanged} == {key: before[key] for key in unchanged}
        assert after.get("date_end") is None  # still in use, not "last seen 1400"
        assert sorted(after["source_databases"]) == ["wiktionary", "wold"]

    def test_an_undated_placeholder_takes_a_later_dating_as_a_whole(self) -> None:
        tag = uuid4().hex[:10]
        form, lsr_id = f"hwaet{tag}", uuid4()
        undated = _placeholder(lsr_id, form, language_code="ang")
        dated = _placeholder(
            lsr_id,
            form,
            language_code="ang",
            date_start=900,
            date_end=1100,
            date_confidence=0.7,
            date_source=DateSource.INTERPOLATED,
        )

        async def run(db: DatabaseManager) -> dict[str, Any]:
            for lsr in (undated, dated):
                await write_to_graph([lsr], [], db=db, placeholder_ids=[str(lsr_id)])
            return await _node(db, lsr_id)

        node = _with_graph(run, tag)

        assert (node["date_start"], node["date_end"]) == (900, 1100)
        assert node["date_confidence"] == 0.7
        assert node["date_source"] == "INTERPOLATED"

    def test_a_period_label_stays_with_its_own_dating(self) -> None:
        """Analyses report period_label as the label of date_start."""
        tag = uuid4().hex[:10]
        # Dated, no label; the placeholder is undated but labelled
        dated = LSR(form_orthographic=f"sky{tag}", language_code="eng", date_start=1220)
        labelled = _placeholder(
            dated.id, f"sky{tag}", language_code="eng", period_label="Old English"
        )
        # Undated, with its source's own age label; the placeholder brings a dating
        undated = LSR(
            form_orthographic=f"hwaet{tag}",
            language_code="ang",
            period_label="Pre 100 CE",
            date_confidence=0.0,
        )
        dating = _placeholder(
            undated.id, f"hwaet{tag}", language_code="ang", date_start=900, date_confidence=0.6
        )

        async def run(db: DatabaseManager) -> list[dict[str, Any]]:
            await write_to_graph([dated, undated], [], db=db)
            await write_to_graph(
                [labelled, dating], [], db=db, placeholder_ids=[str(dated.id), str(undated.id)]
            )
            return [await _node(db, dated.id), await _node(db, undated.id)]

        after_labelled, after_dating = _with_graph(run, tag)

        assert (after_labelled["date_start"], after_labelled["period_label"]) == (1220, "")
        assert after_labelled["date_confidence"] == 1.0
        assert (after_dating["date_start"], after_dating["period_label"]) == (900, "")
        assert after_dating["date_confidence"] == 0.6

    def test_other_lsrs_of_the_write_are_still_full_upserts(self) -> None:
        tag = uuid4().hex[:10]
        recipient = LSR(
            form_orthographic=f"dolphin{tag}",
            language_code="eng",
            definition_primary="old gloss",
        )
        donor = _placeholder(uuid4(), f"delfin{tag}", definition_primary="dolphin")
        edge = {
            "source_id": str(recipient.id),
            "target_id": str(donor.id),
            "type": "BORROWED_FROM",
            "confidence": 0.9,
            "evidence": "test",
        }

        async def run(db: DatabaseManager) -> tuple[GraphWriteResult, dict[str, Any], list[Any]]:
            await write_to_graph([recipient], [], db=db)
            recipient.definition_primary = "new gloss"
            result = await write_to_graph(
                [recipient, donor], [edge], db=db, placeholder_ids=[str(donor.id)]
            )
            donors, _ = await LSRRepository(db).get_borrowings(recipient.id)
            return result, await _node(db, recipient.id), donors

        result, node, donors = _with_graph(run, tag)

        assert (result.lsrs_written, result.relationships_written) == (2, 1)
        assert result.search_index_failed == 0
        assert node["definition_primary"] == "new gloss"
        assert [(d["id"], d["definition"]) for d in donors] == [(str(donor.id), "dolphin")]


class TestBoundedQueries:
    """The repository's searches, writes and statistics on a working Neo4j."""

    def test_create_search_delete_and_statistics(self) -> None:
        tag = uuid4().hex[:10]
        lsr = LSR(form_orthographic=f"water{tag}", language_code="eng", date_start=1000)

        async def run(db: DatabaseManager) -> None:
            repo = LSRRepository(db)
            created = await repo.create(lsr)
            assert created.id == lsr.id
            found, total = await repo.search(form=f"water{tag}")
            assert (total, [f.id for f in found]) == (1, [lsr.id])
            stats = await repo.get_statistics()
            assert stats["total_lsrs"] >= 1 and "error" not in stats
            assert await repo.delete(lsr.id) is True
            with pytest.raises(LSRNotFoundError):
                await repo.delete(lsr.id)
            assert await repo.search(form=f"water{tag}") == ([], 0)

        _with_graph(run, tag)
