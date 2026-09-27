"""Tests for the ingest -> graph path: WOLD dating/donors, borrowing edges, persistence.

The live-graph tests run only when a Neo4j reachable with the configured
credentials (NEO4J_URI / NEO4J_PASSWORD, or .env) is available; they write
records under a unique source name and delete them afterwards.
"""

import asyncio
import csv
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from src.adapters.base import RawLexicalEntry
from src.adapters.clld import CLLDAdapter, parse_wold_age, wold_meaning_definition
from src.ingestion import (
    IngestionStats,
    _build_source_relationships,
    _process_entry,
    run_wold_ingestion,
)
from src.models.lsr import LSR
from src.pipelines.entity_resolution import EntityResolver
from src.pipelines.graph_writer import write_to_graph
from src.utils.db import DatabaseManager


class TestParseWoldAge:
    """WOLD `Age` values -> earliest attestation year."""

    @pytest.mark.parametrize(
        ("age", "year"),
        [
            ("1835", 1835),
            ("c. 1300", 1300),
            ("before 1225", 1225),
            ("1432-1450", 1432),
            ("c. 1340-1370", 1340),
            ("14th century", 1300),
            ("c. 897", 897),
            ("Pre-1606", 1606),
            ("1940-present", 1940),
            ("mid-20th century", 1933),
            ("19th/20th century", 1800),
        ],
    )
    def test_dated_values(self, age, year):
        parsed, label, confidence = parse_wold_age(age, "English")
        assert parsed == year
        assert label == age
        assert 0 < confidence <= 1

    def test_before_is_less_certain_than_exact(self):
        assert parse_wold_age("before 1225")[2] < parse_wold_age("1225")[2]

    def test_english_inherited_periods(self):
        year, label, _ = parse_wold_age("Proto-Germanic", "English")
        assert year == 700
        assert "Old English" in label

    def test_period_labels_only_interpreted_for_known_languages(self):
        assert parse_wold_age("Proto-Germanic", "German")[0] is None
        assert parse_wold_age("Middle Japanese", "Japanese")[0] == 1100

    # "Pre 100 CE" is how WOLD dates the Latin etymon of inherited Romanian words
    @pytest.mark.parametrize(
        "age", ["", "Modern", "Pukui & Elbert 1986", "Kanuri only", "Pre 100 CE"]
    )
    def test_undatable_values(self, age):
        assert parse_wold_age(age, "English")[0] is None


class TestWoldMeaningDefinition:
    """Numbered WOLD meanings ("male(1)") become readable definitions."""

    @pytest.mark.parametrize(
        ("name", "gloss", "definition"),
        [
            ("the sky", "SKY", "the sky"),
            ("male(1)", "MALE (OF PERSON)", "male (of person)"),
            ("to burn(1)", "BURN (SOMETHING)", "to burn (something)"),
            ("the spring(2)", "SPRINGTIME", "the spring (springtime)"),
            ("right(2)", "CORRECT (RIGHT)", "right (correct)"),
            ("the knife(2)", "KNIFE", "the knife"),
            ("to grow(2)", "", "to grow"),
        ],
    )
    def test_definition(self, name, gloss, definition):
        meaning = {"Name": name, "Concepticon_Gloss": gloss}
        assert wold_meaning_definition(meaning) == definition


@pytest.fixture
def wold_dir(tmp_path: Path) -> Path:
    """Minimal WOLD CLDF export: two English words, one borrowed from Old Norse."""
    with open(tmp_path / "languages.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["ID", "Name", "ISO639P3code", "Family"])
        writer.writeheader()
        writer.writerow(
            {"ID": "English", "Name": "English", "ISO639P3code": "eng", "Family": "Indo-European"}
        )
    with open(tmp_path / "parameters.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["ID", "Name"])
        writer.writeheader()
        writer.writerow({"ID": "1-51", "Name": "the sky"})
        writer.writerow({"ID": "3-41", "Name": "the horse"})
    with open(tmp_path / "forms.csv", "w", newline="") as f:
        fields = ["ID", "Language_ID", "Parameter_ID", "Form", "Borrowed", "Borrowed_score", "Age"]
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerow(
            dict(
                zip(
                    fields,
                    [
                        "English-1-51-1",
                        "English",
                        "1-51",
                        "sky",
                        "1. clearly borrowed",
                        "1.0",
                        "c. 1220",
                    ],
                    strict=True,
                )
            )
        )
        writer.writerow(
            dict(
                zip(
                    fields,
                    [
                        "English-3-41-1",
                        "English",
                        "3-41",
                        "horse",
                        "5. no evidence for borrowing",
                        "0.0",
                        "Proto-Germanic",
                    ],
                    strict=True,
                )
            )
        )
    with open(tmp_path / "borrowings.csv", "w", newline="") as f:
        fields = [
            "Target_Form_ID",
            "Source_word",
            "Source_meaning",
            "Source_relation",
            "Source_certain",
            "Source_languoid",
            "Source_languoid_glottocode",
        ]
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerow(
            dict(
                zip(
                    fields,
                    ["English-1-51-1", "ský", "cloud", "immediate", "yes", "Old Norse", "oldn1244"],
                    strict=True,
                )
            )
        )
    return tmp_path


class TestWoldToGraphRecords:
    """WOLD rows become dated LSRs and BORROWED_FROM edges."""

    def test_adapter_dates_and_donors(self, wold_dir):
        adapter = CLLDAdapter(data_dir=wold_dir)
        adapter.connect()
        entries = {e.form: e for e in adapter.fetch_all()}
        adapter.disconnect()

        assert entries["sky"].date_attested == 1220
        assert entries["horse"].date_attested == 700
        donor = entries["sky"].related_forms[0]
        assert (donor["form"], donor["language"], donor["language_code"]) == (
            "ský",
            "Old Norse",
            "non",
        )
        assert entries["horse"].related_forms == []

    def test_dry_run_builds_donor_edges(self, wold_dir):
        stats = run_wold_ingestion(data_dir=str(wold_dir), dry_run=True)

        assert stats.lsrs_created == 2
        assert stats.lsrs_dated == 2
        assert stats.donor_lsrs_created == 1
        assert stats.relationships_extracted == 1
        assert stats.dry_run is True
        assert stats.lsrs_written == 0

    def test_borrowing_edge_reuses_existing_donor_lsr(self):
        """A donor that is already in the store is linked, not duplicated."""
        store: dict[UUID, LSR] = {}
        resolver = EntityResolver()
        resolver.set_lsr_store(store)
        stats = IngestionStats("test")

        norse = RawLexicalEntry(
            source_name="test", source_id="n1", form="ský", language="Old Norse"
        )
        sky = RawLexicalEntry(
            source_name="test",
            source_id="e1",
            form="sky",
            language="English",
            language_code="eng",
            related_forms=[
                {
                    "type": "borrowed_from",
                    "form": "ský",
                    "language": "Old Norse",
                    "language_code": "non",
                }
            ],
        )
        norse_id = _process_entry(norse, resolver, store, stats)
        sky_id = _process_entry(sky, resolver, store, stats)

        edges, linked = _build_source_relationships([(sky, sky_id)], store, stats)

        assert stats.donor_lsrs_created == 0
        assert linked == {sky_id}
        assert edges == [
            {
                "source_id": str(sky_id),
                "target_id": str(norse_id),
                "type": "BORROWED_FROM",
                "confidence": 0.5,
                "evidence": "test:e1",
            }
        ]


class TestResolverIncrementalIndex:
    """add_lsr keeps the indices equivalent to a full rebuild."""

    def test_add_lsr_matches_rebuild(self):
        lsrs = [
            LSR(form_orthographic=form, language_code="eng")
            for form in ("water", "waters", "wader", "fire")
        ]
        incremental = EntityResolver()
        incremental.set_lsr_store({})
        for lsr in lsrs:
            incremental.add_lsr(lsr)

        rebuilt = EntityResolver()
        rebuilt.set_lsr_store({lsr.id: lsr for lsr in lsrs})

        entry = RawLexicalEntry(
            source_name="t", source_id="1", form="Water", language="English", language_code="eng"
        )
        assert set(incremental._retrieve_candidates(entry)) == set(
            rebuilt._retrieve_candidates(entry)
        )
        assert incremental._retrieve_candidates(entry)


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


@pytest.mark.integration
@pytest.mark.skipif(not _neo4j_reachable(), reason="requires a reachable Neo4j")
class TestGraphWriteLive:
    """write_to_graph against a real Neo4j."""

    def test_write_is_idempotent_and_complete(self):
        source = f"pytest-{uuid4()}"
        recipient = LSR(
            form_orthographic="sky",
            language_code="eng",
            date_start=1220,
            definition_primary="the sky",
            definitions_alternate=["heaven"],
            semantic_vector=[0.1, 0.2],
            etymology_text="Borrowed from Old Norse ský",
            source_databases=[source],
        )
        donor = LSR(form_orthographic="ský", language_code="non", source_databases=[source])
        edges = [
            {
                "source_id": str(recipient.id),
                "target_id": str(donor.id),
                "type": "BORROWED_FROM",
                "confidence": 0.95,
                "evidence": "test",
            }
        ]

        async def run() -> tuple:
            first = await write_to_graph([recipient, donor], edges)
            second = await write_to_graph([recipient, donor], edges)
            db = DatabaseManager()
            await db.connect_neo4j()
            try:
                async with db.neo4j_session() as session:
                    res = await session.run(
                        "MATCH (l:LSR) WHERE $source IN l.source_databases "
                        "OPTIONAL MATCH (l)-[r:BORROWED_FROM]->() "
                        "RETURN count(DISTINCT l) AS nodes, count(r) AS edges",
                        {"source": source},
                    )
                    counts = await res.single()
                    res = await session.run(
                        "MATCH (l:LSR {id: $id}) RETURN l", {"id": str(recipient.id)}
                    )
                    node = (await res.single())["l"]
                    await session.run(
                        "MATCH (l:LSR) WHERE $source IN l.source_databases DETACH DELETE l",
                        {"source": source},
                    )
            finally:
                await db.close_all()
            return first, second, counts, node

        first, second, counts, node = asyncio.run(run())

        assert (first.lsrs_written, first.relationships_written) == (2, 1)
        assert (second.lsrs_written, second.relationships_written) == (2, 1)
        assert (counts["nodes"], counts["edges"]) == (2, 1)
        assert node["date_start"] == 1220
        assert node.get("date_end") is None
        assert list(node["definitions_alternate"]) == ["heaven"]
        assert list(node["semantic_vector"]) == [0.1, 0.2]
        assert node["etymology_text"] == "Borrowed from Old Norse ský"


class TestWiktionaryAttestationDates:
    """Dates come only from explicit evidence, never from arbitrary numbers."""

    @pytest.mark.parametrize(
        ("wikitext", "year"),
        [
            ("{{cite-book|page=1043|id=1234}} From Old English wæter", None),
            ("Some text 2004 and 1776 without context", None),
            ("# A liquid. {{defdate|from 14th c.}}", 1300),
            ("First attested in 1386 in Chaucer.", 1386),
            ("#* {{quote-book|en|year=1596|author=Shakespeare|page=12}}", 1596),
            ("#* '''1603''', William Shakespeare, ''Hamlet''", 1603),
            ("{{defdate|1500s}}\n#* {{quote-book|en|year=1450|page=3}}", 1450),
        ],
    )
    def test_extract(self, wikitext, year):
        from src.adapters.wiktionary import WiktionaryAdapter

        assert WiktionaryAdapter()._extract_attestation_date(wikitext) == year

    def test_unknown_language_gets_no_invented_code(self):
        from src.adapters.wiktionary import WiktionaryAdapter

        wikitext = "==Klingon==\n===Noun===\n# a word\n"
        entries = WiktionaryAdapter()._parse_wikitext("qapla", wikitext)
        assert [e.language_code for e in entries] == [""]


class TestWoldLanguageCodes:
    def test_glottocode_fallback_and_surface_form(self, tmp_path):
        with open(tmp_path / "languages.csv", "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["ID", "Name", "ISO639P3code", "Glottocode"])
            writer.writeheader()
            writer.writerow(
                {"ID": "X", "Name": "Oldish", "ISO639P3code": "", "Glottocode": "oldi1234"}
            )
        with open(tmp_path / "parameters.csv", "w", newline="") as f:
            csv.DictWriter(f, fieldnames=["ID", "Name"]).writeheader()
        with open(tmp_path / "borrowings.csv", "w", newline="") as f:
            csv.DictWriter(f, fieldnames=["Target_Form_ID"]).writeheader()
        with open(tmp_path / "forms.csv", "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["ID", "Language_ID", "Parameter_ID", "Form"])
            writer.writeheader()
            writer.writerow(
                {"ID": "X-1", "Language_ID": "X", "Parameter_ID": "1-1", "Form": "low_tide"}
            )

        adapter = CLLDAdapter(data_dir=tmp_path)
        adapter.connect()
        (entry,) = list(adapter.fetch_all())
        assert entry.language_code == "oldi1234"
        assert entry.form == "low tide"

    @pytest.mark.parametrize(
        ("value", "headword"),
        [("call (1)", "call"), ("(sea)gull", "gull"), ("wood(s)", "wood"), ("wake (up)", "wake")],
    )
    def test_parenthesized_parts_are_not_part_of_the_headword(self, tmp_path, value, headword):
        with open(tmp_path / "languages.csv", "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["ID", "Name", "ISO639P3code"])
            writer.writeheader()
            writer.writerow({"ID": "English", "Name": "English", "ISO639P3code": "eng"})
        with open(tmp_path / "parameters.csv", "w", newline="") as f:
            csv.DictWriter(f, fieldnames=["ID", "Name"]).writeheader()
        with open(tmp_path / "borrowings.csv", "w", newline="") as f:
            csv.DictWriter(f, fieldnames=["Target_Form_ID"]).writeheader()
        with open(tmp_path / "forms.csv", "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["ID", "Language_ID", "Parameter_ID", "Value"])
            writer.writeheader()
            writer.writerow(
                {"ID": "E-1", "Language_ID": "English", "Parameter_ID": "1-1", "Value": value}
            )

        adapter = CLLDAdapter(data_dir=tmp_path)
        adapter.connect()
        (entry,) = list(adapter.fetch_all())
        assert entry.form == headword
        assert entry.raw_data["value"] == value


WATER_WIKITEXT = """==English==
===Etymology===
From {{inh|en|enm|water}}, from {{inh+|en|ang|wæter}}, from {{inh|en|gem-pro|*watōr}}.
Compare {{cog|de|Wasser}}.
===Noun===
# A clear liquid. {{defdate|from 9th c.}}
==French==
===Etymology===
Borrowed from {{bor+|fr|en|water}}.
===Noun===
# toilet
"""


class TestWiktionaryEtymologyEdges:
    """Etymology templates become an ancestor chain, cognates and loans."""

    def test_chain_cognate_and_loan(self, monkeypatch):
        from src.adapters.wiktionary import WiktionaryAdapter
        from src.ingestion import run_ingestion

        monkeypatch.setattr(
            WiktionaryAdapter,
            "fetch_word",
            lambda self, word: self._parse_wikitext(word, WATER_WIKITEXT),
        )
        captured = {}

        def fake_write(lsr_store, relationships, stats, dry_run):
            captured["lsrs"] = {lsr.id: lsr for lsr in lsr_store.values()}
            captured["edges"] = relationships

        monkeypatch.setattr("src.ingestion._write_results", fake_write)

        stats = run_ingestion(["water"], dry_run=True)

        lsrs = captured["lsrs"]

        def label(lsr_id: str) -> str:
            lsr = lsrs[UUID(lsr_id)]
            return f"{lsr.language_code}:{lsr.form_orthographic}"

        edges = {
            (label(e["source_id"]), e["type"], label(e["target_id"])) for e in captured["edges"]
        }
        assert edges == {
            ("eng:water", "DESCENDS_FROM", "enm:water"),
            ("enm:water", "DESCENDS_FROM", "ang:wæter"),
            ("ang:wæter", "DESCENDS_FROM", "gem-pro:watōr"),
            ("eng:water", "COGNATE_OF", "deu:Wasser"),
            ("fra:water", "BORROWED_FROM", "eng:water"),
        }
        proto = next(lsr for lsr in lsrs.values() if lsr.language_code == "gem-pro")
        assert proto.reconstruction_flag is True
        assert stats.lsrs_dated == 1  # only the English entry states a date


def test_failed_wold_download_is_a_connection_error(tmp_path, monkeypatch):
    """The CLI turns ConnectionError into a one-line error and exit status 1."""
    monkeypatch.setattr(CLLDAdapter, "WOLD_BASE_URL", "http://127.0.0.1:1")
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    adapter = CLLDAdapter(data_dir=tmp_path)
    with pytest.raises(ConnectionError, match="Failed to download"):
        adapter.connect()


def test_cli_exits_cleanly_when_source_is_unreachable(tmp_path, monkeypatch, capsys):
    import sys

    from src.ingestion import main

    monkeypatch.setattr(CLLDAdapter, "WOLD_BASE_URL", "http://127.0.0.1:1")
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    monkeypatch.setattr(sys, "argv", ["ingest", "--source", "wold", "--data-dir", str(tmp_path)])
    with pytest.raises(SystemExit) as exit_info:
        main()
    assert exit_info.value.code == 1
