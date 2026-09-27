"""Ingestion review fixes that need a real Neo4j.

Run with a throwaway database, e.g.:
    TEST_NEO4J_URI=bolt://localhost:7688 TEST_NEO4J_PASSWORD=... \
        pytest tests/integration/test_review_fixes_ingestion.py
"""

import asyncio
import csv
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from src.adapters.wiktionary import WiktionaryAdapter
from src.ingestion import run_ingestion, run_wold_ingestion
from src.utils.db import DatabaseManager


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


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


async def _nodes(forms: list[str]) -> dict[tuple[str, str], dict[str, Any]]:
    """LSR nodes with these normalized forms, by (language_code, form)."""
    db = DatabaseManager()
    await db.connect_neo4j()
    try:
        async with db.neo4j_session() as session:
            result = await session.run(
                "MATCH (l:LSR) WHERE l.form_normalized IN $forms RETURN l", {"forms": forms}
            )
            return {
                (record["l"]["language_code"], record["l"]["form_normalized"]): dict(record["l"])
                async for record in result
            }
    finally:
        await db.close_all()


async def _delete(forms: list[str]) -> None:
    db = DatabaseManager()
    await db.connect_neo4j()
    try:
        async with db.neo4j_session() as session:
            await session.run(
                "MATCH (l:LSR) WHERE l.form_normalized IN $forms DETACH DELETE l", {"forms": forms}
            )
    finally:
        await db.close_all()


def test_a_later_run_does_not_blank_a_placeholder(tmp_path, monkeypatch):
    """A WOLD donor gloss survives a Wiktionary run that links the same donor word."""
    tag = uuid4().hex[:8]
    donor, recipient, other = f"sky{tag}", f"skie{tag}", f"skyey{tag}"
    _write_csv(
        tmp_path / "languages.csv",
        [{"ID": "English", "Name": "English", "Glottocode": "stan1293", "ISO639P3code": "eng"}],
    )
    _write_csv(tmp_path / "parameters.csv", [{"ID": "1-1", "Name": "the sky"}])
    _write_csv(
        tmp_path / "forms.csv",
        [
            {
                "ID": f"E-{tag}",
                "Language_ID": "English",
                "Parameter_ID": "1-1",
                "Form": recipient,
                "Borrowed": "1. clearly borrowed",
                "Age": "c. 1220",
            }
        ],
    )
    _write_csv(
        tmp_path / "borrowings.csv",
        [
            {
                "Target_Form_ID": f"E-{tag}",
                "Source_word": donor,
                "Source_meaning": "cloud",
                "Source_relation": "immediate",
                "Source_certain": "yes",
                "Source_languoid": "Old Norse",
                "Source_languoid_glottocode": "oldn1244",
            }
        ],
    )
    page = f"==English==\n===Etymology===\n{{{{bor|en|non|{donor}}}}}\n===Noun===\n# cloudy\n"
    monkeypatch.setattr(
        WiktionaryAdapter, "fetch_word", lambda self, word: self._parse_wikitext(word, page)
    )
    forms = [donor, recipient, other]
    try:
        wold = run_wold_ingestion(data_dir=str(tmp_path))
        wiktionary = run_ingestion([other], rate_limit_ms=0)
        nodes = asyncio.run(_nodes(forms))
    finally:
        asyncio.run(_delete(forms))

    assert (wold.lsrs_failed, wold.relationships_failed) == (0, 0)
    assert (wiktionary.lsrs_failed, wiktionary.relationships_failed) == (0, 0)
    assert wold.donor_lsrs_created == wiktionary.donor_lsrs_created == 1
    placeholder = nodes[("non", donor)]
    assert placeholder["definition_primary"] == "cloud"
    assert sorted(placeholder["source_databases"]) == ["wiktionary", "wold"]
    assert placeholder.get("date_start") is None
    assert placeholder["date_confidence"] == 0.0
    assert nodes[("eng", recipient)]["date_start"] == 1220
    assert nodes[("eng", other)]["definition_primary"] == "cloudy"
