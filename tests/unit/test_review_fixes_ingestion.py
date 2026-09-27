"""Behaviour fixed after review: ingestion adapters, resolution and the ingest CLI."""

import csv
import json
import logging
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import httpx
import pytest
from pydantic import ValidationError

import src.ingestion as ingestion
from src.adapters.base import RawLexicalEntry
from src.adapters.clics import CLICSAdapter
from src.adapters.clld import CLLDAdapter
from src.adapters.corpus import CorpusAdapter
from src.adapters.wiktionary import WiktionaryAdapter
from src.models.lsr import LSR
from src.pipelines.entity_resolution import (
    EntityResolver,
    ResolutionAction,
    convert_entry_to_lsr,
)
from src.utils.languages import LANGUAGE_CODE_MAP, graph_language_code, language_name
from src.utils.phonetics import PhoneticUtils

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_wiktionary(monkeypatch: pytest.MonkeyPatch, pages: dict[str, str]) -> None:
    """Serve Wiktionary pages from a dict instead of the network."""
    monkeypatch.setattr(
        WiktionaryAdapter, "fetch_word", lambda self, word: self._parse_wikitext(word, pages[word])
    )


def _capture_results(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Capture what an ingestion run would write to the graph."""
    captured: dict[str, Any] = {}

    def fake_write(lsr_store, relationships, stats, dry_run):
        captured.update(store=dict(lsr_store), edges=relationships, stats=stats)

    monkeypatch.setattr(ingestion, "_write_results", fake_write)
    return captured


def _edges(captured: dict[str, Any]) -> set[tuple[str, str, str]]:
    """Edges as (source code:form, type, target code:form)."""
    store = captured["store"]

    def label(lsr_id: str) -> str:
        lsr = store[UUID(lsr_id)]
        return f"{lsr.language_code}:{lsr.form_orthographic}"

    return {(label(e["source_id"]), e["type"], label(e["target_id"])) for e in captured["edges"]}


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _run_cli(monkeypatch: pytest.MonkeyPatch, *argv: str) -> int:
    """Run the ingest CLI; return its exit status (0 when it returns normally)."""
    monkeypatch.setattr(sys, "argv", ["ingest", *argv])
    try:
        ingestion.main()
    except SystemExit as exit_info:
        return int(exit_info.code or 0)
    return 0


# ---------------------------------------------------------------------------
# Wiktionary etymology templates
# ---------------------------------------------------------------------------


class TestWiktionaryTemplatesWithoutTerm:
    """A template that names only a language yields no related word."""

    @pytest.mark.parametrize(
        "wikitext",
        [
            "{{bor|en|fr}}",
            "{{inh|en|enm}}",
            "{{der|en|ar}}",
            "{{der|en|la|-}}",
            "{{cal|en|de||gloss}}",
            "{{cog|fr|-}}",
            "{{m|ang|-}}",
            "{{cog|fr}}",
        ],
    )
    def test_no_term_no_template(self, wikitext):
        assert WiktionaryAdapter()._extract_etymology_templates(wikitext) == []

    def test_term_is_the_third_parameter(self):
        (template,) = WiktionaryAdapter()._extract_etymology_templates("{{bor|en|fr|café}}")
        assert (template["target_lang"], template["lang"], template["term"]) == (
            "en",
            "fr",
            "café",
        )

    def test_no_fake_words_or_self_loans(self, monkeypatch):
        page = (
            "==English==\n===Etymology===\n"
            "{{bor|en|fr}}, from {{der|en|ar}}, ultimately {{der|en|la|-}}. Cf. {{cog|fr|-}}.\n"
            "===Noun===\n# A drink.\n"
        )
        _fake_wiktionary(monkeypatch, {"coffee": page})
        captured = _capture_results(monkeypatch)

        stats = ingestion.run_ingestion(["coffee"], dry_run=True, rate_limit_ms=0)

        assert [lsr.form_orthographic for lsr in captured["store"].values()] == ["coffee"]
        assert captured["edges"] == []
        assert stats.donor_lsrs_created == 0

    def test_chain_skips_termless_links(self, monkeypatch):
        page = (
            "==English==\n===Etymology===\n"
            "{{bor|en|fr|café}}, from {{der|en|ar}}, from {{der|en|ota|قهوه}}.\n"
            "===Noun===\n# A drink.\n"
        )
        _fake_wiktionary(monkeypatch, {"coffee": page})
        captured = _capture_results(monkeypatch)

        ingestion.run_ingestion(["coffee"], dry_run=True, rate_limit_ms=0)

        assert _edges(captured) == {
            ("eng:coffee", "BORROWED_FROM", "fra:café"),
            ("fra:café", "DESCENDS_FROM", "ota:قهوه"),
        }


# ---------------------------------------------------------------------------
# WOLD donor language codes
# ---------------------------------------------------------------------------


@pytest.fixture()
def wold_contact_dir(tmp_path: Path) -> Path:
    """WOLD export: Hausa and Kanuri borrow from each other and from others."""
    _write_csv(
        tmp_path / "languages.csv",
        [
            {"ID": "Hausa", "Name": "Hausa", "Glottocode": "haus1257", "ISO639P3code": "hau"},
            {"ID": "Kanuri", "Name": "Kanuri", "Glottocode": "cent2050", "ISO639P3code": "knc"},
        ],
    )
    _write_csv(tmp_path / "parameters.csv", [{"ID": "1-1", "Name": "the world"}])
    forms = [
        # (form id, language, form)
        ("H-1", "Hausa", "àsháanàa"),
        ("K-1", "Kanuri", "àshánà"),
        ("K-2", "Kanuri", "yìnná"),
        ("H-2", "Hausa", "ínnàa"),
        ("K-3", "Kanuri", "kitâbu"),
        ("K-4", "Kanuri", "bùlgár"),
        ("K-5", "Kanuri", "dúnyà"),
        ("K-6", "Kanuri", "tìgì"),
    ]
    _write_csv(
        tmp_path / "forms.csv",
        [
            {
                "ID": form_id,
                "Language_ID": lang,
                "Parameter_ID": "1-1",
                "Form": form,
                "Borrowed": "1. clearly borrowed" if form_id != "H-1" else "5. no evidence",
                "Age": "",
            }
            for form_id, lang, form in forms
        ],
    )
    donors = [
        # (target form id, donor word, donor language, donor glottocode)
        ("K-1", "àsháanàa", "Hausa", "haus1257"),  # a WOLD language, by name
        ("H-2", "yìnná", "Central Kanuri", "cent2050"),  # a WOLD language, by glottocode
        ("K-3", "kitāb", "Arabic", "stan1318"),  # WOLD_DONOR_LANGUAGE_CODES
        ("K-4", "bŭlgar", "Bulgarian", "bulg1262"),  # LANGUAGE_CODE_MAP
        ("K-5", "dunya", "Late Latin", "late1252"),  # Latin variety
        ("K-6", "tigi", "Tamasheq", "tama1365"),  # nothing known: glottocode
    ]
    _write_csv(
        tmp_path / "borrowings.csv",
        [
            {
                "Target_Form_ID": target,
                "Source_word": word,
                "Source_relation": "immediate",
                "Source_certain": "yes",
                "Source_languoid": lang,
                "Source_languoid_glottocode": glottocode,
            }
            for target, word, lang, glottocode in donors
        ],
    )
    return tmp_path


class TestWoldDonorCodes:
    """Donors get the code WOLD itself uses for the language, not a glottocode."""

    def test_donor_codes(self, wold_contact_dir):
        adapter = CLLDAdapter(data_dir=wold_contact_dir)
        adapter.connect()
        donors = {
            e.form: e.related_forms[0]["language_code"]
            for e in adapter.fetch_all()
            if e.related_forms
        }
        adapter.disconnect()

        assert donors == {
            "àshánà": "hau",
            "ínnàa": "knc",
            "kitâbu": "ara",
            "bùlgár": "bul",
            "dúnyà": "la-lat",
            "tìgì": "tama1365",
        }

    def test_donor_word_links_to_the_real_lsr(self, wold_contact_dir, monkeypatch):
        captured = _capture_results(monkeypatch)

        stats = ingestion.run_wold_ingestion(data_dir=str(wold_contact_dir), dry_run=True)

        edges = _edges(captured)
        assert ("knc:àshánà", "BORROWED_FROM", "hau:àsháanàa") in edges
        assert ("hau:ínnàa", "BORROWED_FROM", "knc:yìnná") in edges
        # WOLD's own Hausa word is the target, not a placeholder copy of it
        assert sum(1 for e in edges if e[2] == "hau:àsháanàa") == 1
        assert "hau:àsháanàa" not in {
            f"{captured['store'][i].language_code}:{captured['store'][i].form_orthographic}"
            for i in stats.placeholder_ids
        }
        codes = {lsr.language_code for lsr in captured["store"].values()}
        assert not codes & {"haus1257", "cent2050", "stan1318", "bulg1262", "arb"}

    def test_blank_glottocode_is_not_a_key(self, wold_contact_dir):
        """A WOLD language without a glottocode does not claim donors that have none."""
        languages = wold_contact_dir / "languages.csv"
        languages.write_text(
            languages.read_text(encoding="utf-8").replace("cent2050", ""), encoding="utf-8"
        )
        adapter = CLLDAdapter(data_dir=wold_contact_dir)
        adapter.connect()
        code = adapter._donor_language_code(
            {"Source_languoid": "Unknown", "Source_languoid_glottocode": ""}
        )
        adapter.disconnect()
        assert code == ""


# ---------------------------------------------------------------------------
# --language for Wiktionary and CLICS
# ---------------------------------------------------------------------------

TWO_LANGUAGE_PAGE = """==English==
===Etymology===
{{inh|en|enm|water}}
===Noun===
# A liquid.

==French==
===Noun===
# toilet
"""


class TestLanguageFilter:
    """--language takes comma-separated names, ISO 639-3 and ISO 639-1 codes."""

    @pytest.mark.parametrize(
        ("languages", "expected"),
        [
            (["English"], ["English"]),
            (["eng"], ["English"]),
            (["en"], ["English"]),
            (["english"], ["English"]),
            (["English", " fra"], ["English", "French"]),
            (["fr"], ["French"]),
            (["Klingon"], []),
        ],
    )
    def test_wiktionary_sections(self, languages, expected):
        adapter = WiktionaryAdapter(languages_to_process=languages)
        entries = adapter._parse_wikitext("water", TWO_LANGUAGE_PAGE)
        assert [e.language for e in entries] == expected

    @pytest.mark.parametrize(
        ("value", "entries"),
        [("English", 1), ("eng", 1), ("en", 1), ("English,French", 2), ("en, fra", 2)],
    )
    def test_wiktionary_cli(self, monkeypatch, capsys, value, entries):
        _fake_wiktionary(monkeypatch, {"water": TWO_LANGUAGE_PAGE})

        status = _run_cli(
            monkeypatch, "--word", "water", "--language", value, "--dry-run", "--rate-limit", "0"
        )

        assert status == 0
        assert f"Entries fetched:     {entries}" in capsys.readouterr().out

    def test_documented_word_list_example(self, monkeypatch, capsys, tmp_path):
        """scripts/ingest.py documents `--words data/seed_words_eng.txt --language eng`."""
        words = tmp_path / "words.txt"
        words.write_text("# seed words\nwater\n", encoding="utf-8")
        _fake_wiktionary(monkeypatch, {"water": TWO_LANGUAGE_PAGE})

        status = _run_cli(
            monkeypatch,
            "--words",
            str(words),
            "--language",
            "eng",
            "--dry-run",
            "--rate-limit",
            "0",
        )

        assert status == 0
        assert "Entries fetched:     1" in capsys.readouterr().out

    @pytest.fixture()
    def cldf_dir(self, tmp_path: Path) -> Path:
        (tmp_path / "languages.csv").write_text(
            "ID,Name,ISO639P3code,Glottocode\n"
            "eng,English,eng,stan1293\n"
            "spa,Spanish,spa,stan1288\n"
            "deu,German,deu,stan1295\n",
            encoding="utf-8",
        )
        (tmp_path / "parameters.csv").write_text(
            "ID,Name,Concepticon_Gloss\nwater,water,WATER\n", encoding="utf-8"
        )
        (tmp_path / "forms.csv").write_text(
            "ID,Language_ID,Parameter_ID,Form\n"
            "1,eng,water,water\n"
            "2,spa,water,agua\n"
            "3,deu,water,Wasser\n",
            encoding="utf-8",
        )
        return tmp_path

    @pytest.mark.parametrize(
        ("languages", "expected"),
        [
            (["English"], ["English"]),
            (["eng"], ["English"]),
            (["en"], ["English"]),
            (["English", " Spanish"], ["English", "Spanish"]),
            (["stan1295", "es"], ["German", "Spanish"]),
        ],
    )
    def test_clics(self, cldf_dir, languages, expected):
        adapter = CLICSAdapter(data_dir=cldf_dir, languages_filter=languages)
        adapter.connect()
        assert sorted(e.language for e in adapter.fetch_all()) == expected
        adapter.disconnect()

    def test_clics_cli(self, cldf_dir, monkeypatch, capsys):
        status = _run_cli(
            monkeypatch,
            "--source",
            "clics",
            "--data-dir",
            str(cldf_dir),
            "--language",
            "en, spa",
            "--dry-run",
        )
        assert status == 0
        assert "Entries fetched:     2" in capsys.readouterr().out

    @pytest.mark.parametrize(
        ("languages", "expected"),
        [
            (["Old High German"], ["Old High German"]),
            # The code the graph stores for a WOLD language without an ISO code
            (["goh"], ["Old High German"]),
            (["oldh1241"], ["Old High German"]),
            (["yaku1245"], ["Sakha"]),
            (["en", "SAKHA"], ["English", "Sakha"]),
        ],
    )
    def test_wold_codes(self, tmp_path, languages, expected):
        _write_csv(
            tmp_path / "languages.csv",
            [
                {
                    "ID": "English",
                    "Name": "English",
                    "Glottocode": "stan1293",
                    "ISO639P3code": "eng",
                },
                {
                    "ID": "OHG",
                    "Name": "Old High German",
                    "Glottocode": "oldh1241",
                    "ISO639P3code": "",
                },
                {"ID": "Sakha", "Name": "Sakha", "Glottocode": "yaku1245", "ISO639P3code": ""},
            ],
        )
        _write_csv(tmp_path / "parameters.csv", [{"ID": "1-1", "Name": "the water"}])
        _write_csv(
            tmp_path / "forms.csv",
            [
                {"ID": f"F-{lang}", "Language_ID": lang, "Parameter_ID": "1-1", "Form": form}
                for lang, form in (("English", "water"), ("OHG", "wazzar"), ("Sakha", "uu"))
            ],
        )
        (tmp_path / "borrowings.csv").write_text("Target_Form_ID,Source_word\n", encoding="utf-8")
        adapter = CLLDAdapter(data_dir=tmp_path, languages_filter=languages)
        adapter.connect()
        entries = sorted(adapter.fetch_all(), key=lambda e: e.language)
        adapter.disconnect()
        assert [e.language for e in entries] == expected
        # The filter matches the code the entries are stored under
        assert {e.language_code for e in entries} <= {"eng", "goh", "yaku1245"}

    def test_help_says_what_each_source_accepts(self):
        import argparse

        parser = argparse.ArgumentParser()
        ingestion.add_arguments(parser)
        (action,) = [a for a in parser._actions if "--language" in a.option_strings]
        assert "ISO 639-1" in action.help
        assert "corpus" in action.help

    def test_corpus_language_accepts_iso_639_1(self, monkeypatch, tmp_path):
        (tmp_path / "doc.txt").write_text("water", encoding="utf-8")
        seen: dict[str, Any] = {}

        def fake_run(**kwargs: Any) -> ingestion.IngestionStats:
            seen.update(kwargs)
            stats = ingestion.IngestionStats("Corpus")
            stats.entries_fetched = 1
            stats.dry_run = True
            return stats

        monkeypatch.setattr(ingestion, "run_corpus_ingestion", fake_run)
        status = _run_cli(
            monkeypatch, "--source", "corpus", "--corpus-dir", str(tmp_path), "--language", "fr"
        )
        assert status == 0
        assert (seen["language"], seen["language_code"]) == ("French", "fra")


# ---------------------------------------------------------------------------
# Entity resolution
# ---------------------------------------------------------------------------


def _distinct_words(n: int) -> list[str]:
    """n distinct four-letter words (all the same length, so all fuzzy look-alikes)."""
    return ["".join(chr(97 + (i // 26**k) % 26) for k in range(4)) for i in range(n)]


class TestEntityResolutionScales:
    """Candidates come from the exact (form, language) index only."""

    def test_resolution_does_no_pairwise_work(self, monkeypatch):
        calls = {"levenshtein": 0}
        original = PhoneticUtils.levenshtein_distance

        def counting(a: str, b: str) -> int:
            calls["levenshtein"] += 1
            return original(a, b)

        monkeypatch.setattr(PhoneticUtils, "levenshtein_distance", staticmethod(counting))
        resolver = EntityResolver()
        resolver.set_lsr_store({})
        candidates = 0
        for i, word in enumerate(_distinct_words(3000)):
            entry = RawLexicalEntry(
                source_name="corpus",
                source_id=str(i),
                form=word,
                language="English",
                language_code="eng",
            )
            candidates += len(resolver._retrieve_candidates(entry))
            assert resolver.resolve(entry).action == ResolutionAction.CREATE_NEW
            resolver.add_lsr(convert_entry_to_lsr(entry))

        # Before, each word was compared with every stored word of its length:
        # about 4.5 million Levenshtein calls for these 3000 words
        assert candidates == 0
        assert calls["levenshtein"] == 0

    def test_near_miss_is_not_a_candidate(self):
        resolver = EntityResolver()
        resolver.set_lsr_store({})
        resolver.add_lsr(LSR(form_orthographic="water", language_code="eng"))
        near = RawLexicalEntry(
            source_name="t", source_id="1", form="watter", language="English", language_code="eng"
        )
        assert resolver._retrieve_candidates(near) == []

    def test_exact_matches_still_merge_and_flag(self):
        resolver = EntityResolver()
        store: dict[UUID, LSR] = {}
        resolver.set_lsr_store(store)
        first = RawLexicalEntry(
            source_name="wold",
            source_id="1",
            form="Wäter",
            language="English",
            language_code="eng",
            definitions=["clear liquid"],
        )
        lsr = convert_entry_to_lsr(first)
        store[lsr.id] = lsr
        resolver.add_lsr(lsr)

        same = first.model_copy(update={"source_id": "2", "form": "water"})
        other_sense = first.model_copy(update={"source_id": "3", "definitions": ["clear water"]})

        merged = resolver.resolve(same)
        assert (merged.action, merged.existing_id) == (ResolutionAction.AUTO_MERGE, lsr.id)
        flagged = resolver.resolve(other_sense)
        assert flagged.action == ResolutionAction.FLAG_FOR_REVIEW
        assert flagged.existing_id == lsr.id


# ---------------------------------------------------------------------------
# One code for Arabic
# ---------------------------------------------------------------------------


class TestArabicCode:
    def test_iso_639_1_and_name_agree(self):
        assert graph_language_code("ar") == LANGUAGE_CODE_MAP["Arabic"] == "ara"
        assert language_name("ara") == "Arabic"

    def test_wiktionary_loan_links_to_the_arabic_entry(self, monkeypatch):
        pages = {
            "algebra": "==English==\n===Etymology===\n{{bor|en|ar|الجبر}}\n===Noun===\n# maths\n",
            "الجبر": "==Arabic==\n===Noun===\n# algebra; restoration\n",
        }
        _fake_wiktionary(monkeypatch, pages)
        captured = _capture_results(monkeypatch)

        stats = ingestion.run_ingestion(["algebra", "الجبر"], dry_run=True, rate_limit_ms=0)

        assert _edges(captured) == {("eng:algebra", "BORROWED_FROM", "ara:الجبر")}
        assert stats.donor_lsrs_created == 0


# ---------------------------------------------------------------------------
# Graph write failures, placeholders and the search index line
# ---------------------------------------------------------------------------


def _write_result(**counts: Any) -> SimpleNamespace:
    """A graph write result (the fields of GraphWriteResult)."""
    fields: dict[str, Any] = {
        "lsrs_written": 0,
        "lsrs_failed": 0,
        "relationships_written": 0,
        "relationships_failed": 0,
        "search_index_available": False,
        "search_index_failed": 0,
        "api_cache_cleared": False,
        "errors": [],
    }
    fields.update(counts)
    return SimpleNamespace(**fields)


@pytest.fixture()
def wold_dir(tmp_path: Path) -> Path:
    """Two English words; 'sky' is borrowed from an Old Norse word not in the data."""
    _write_csv(
        tmp_path / "languages.csv",
        [{"ID": "English", "Name": "English", "Glottocode": "stan1293", "ISO639P3code": "eng"}],
    )
    _write_csv(tmp_path / "parameters.csv", [{"ID": "1-1", "Name": "the sky"}])
    _write_csv(
        tmp_path / "forms.csv",
        [
            {
                "ID": "E-1",
                "Language_ID": "English",
                "Parameter_ID": "1-1",
                "Form": "sky",
                "Borrowed": "1. clearly borrowed",
                "Age": "c. 1220",
            },
            {
                "ID": "E-2",
                "Language_ID": "English",
                "Parameter_ID": "1-1",
                "Form": "welkin",
                "Borrowed": "5. no evidence",
                "Age": "Pre 100 CE",
            },
        ],
    )
    _write_csv(
        tmp_path / "borrowings.csv",
        [
            {
                "Target_Form_ID": "E-1",
                "Source_word": "ský",
                "Source_meaning": "cloud",
                "Source_relation": "immediate",
                "Source_certain": "yes",
                "Source_languoid": "Old Norse",
                "Source_languoid_glottocode": "oldn1244",
            }
        ],
    )
    return tmp_path


class TestGraphWrite:
    @pytest.fixture()
    def graph(self, monkeypatch) -> SimpleNamespace:
        """Stand-in for write_to_graph: records calls, returns queued results."""
        graph = SimpleNamespace(calls=[], results=[])

        async def fake_write_to_graph(lsrs, relationships, db=None, placeholder_ids=None):
            graph.calls.append(
                {"lsrs": lsrs, "relationships": relationships, "placeholder_ids": placeholder_ids}
            )
            return graph.results.pop(0)

        monkeypatch.setattr(ingestion, "write_to_graph", fake_write_to_graph)
        return graph

    def test_placeholders_are_written_fill_only(self, wold_dir, graph):
        graph.results.append(_write_result(lsrs_written=3, relationships_written=1))

        stats = ingestion.run_wold_ingestion(data_dir=str(wold_dir))

        (call,) = graph.calls
        placeholders = [lsr for lsr in call["lsrs"] if str(lsr.id) in call["placeholder_ids"]]
        assert [(p.language_code, p.form_orthographic) for p in placeholders] == [("non", "ský")]
        assert {str(i) for i in stats.placeholder_ids} == set(call["placeholder_ids"])
        # The placeholder has no date of its own
        (placeholder,) = placeholders
        assert (placeholder.date_start, placeholder.date_confidence) == (None, 0.0)

    @pytest.mark.parametrize(
        "result",
        [
            _write_result(lsrs_written=1, lsrs_failed=2, errors=["Batch 1: ServiceUnavailable"]),
            _write_result(lsrs_written=3, relationships_failed=1, errors=["Edges: timeout"]),
            _write_result(lsrs_failed=3, relationships_failed=1, errors=["Batch 1: timeout"]),
        ],
    )
    def test_partial_write_failure_exits_non_zero(
        self, wold_dir, graph, monkeypatch, capsys, caplog, result
    ):
        graph.results.append(result)

        status = _run_cli(monkeypatch, "--source", "wold", "--data-dir", str(wold_dir))

        out = capsys.readouterr().out
        assert status == 1
        assert "INGESTION SUMMARY" in out  # the summary is printed first
        assert (
            f"Failed to write:     {result.lsrs_failed} LSRs, "
            f"{result.relationships_failed} relationships"
        ) in out
        assert "The graph write was incomplete" in caplog.text

    def test_complete_write_exits_zero(self, wold_dir, graph, monkeypatch, capsys):
        graph.results.append(
            _write_result(lsrs_written=3, relationships_written=1, search_index_available=True)
        )

        status = _run_cli(monkeypatch, "--source", "wold", "--data-dir", str(wold_dir))

        out = capsys.readouterr().out
        assert status == 0
        assert "Failed to write" not in out
        assert "Search index:        updated" in out

    @pytest.mark.parametrize(
        ("available", "failed", "line"),
        [
            (True, 0, "updated"),
            (False, 0, "not configured or unreachable"),
            (False, 7, "7 LSRs not indexed; run `lexicon reindex`"),
        ],
    )
    def test_search_index_line(self, available, failed, line):
        stats = ingestion.IngestionStats("WOLD")
        stats.search_index_updated = available
        stats.search_index_failed = failed
        assert f"  Search index:        {line}\n" in stats.summary()


# ---------------------------------------------------------------------------
# Dates: BCE, out-of-range years, confidence
# ---------------------------------------------------------------------------


class TestWiktionaryBCE:
    @pytest.mark.parametrize(
        ("wikitext", "year"),
        [
            ("# war {{defdate|from 8th c. BCE}}", -800),
            ("First attested in the 7th century BC.", -700),
            ("first attested in 600 BCE", -600),
            ("#* {{quote-book|grc|year=c. 800 BCE|author=Homer}}", -800),
            ("#* '''c. 400 B.C.''', Herodotus", -400),
            ("{{defdate|from 3rd c. B.C.E.}}", -300),
            ("{{defdate|c. 50 BC}}", -50),
            # CE dates are unchanged
            ("{{defdate|from 14th c.}}", 1300),
            ("#* {{quote-book|en|year=1596|page=12}}", 1596),
            ("{{defdate|from 14th c.|ref=p. 123}}", 1300),
            ("first attested in 1200 because", 1200),
        ],
    )
    def test_extract(self, wikitext, year):
        assert WiktionaryAdapter()._extract_attestation_date(wikitext) == year

    def test_bce_year_reaches_the_lsr(self):
        page = "==Ancient Greek==\n===Noun===\n# war {{defdate|from 8th c. BCE}}\n"
        (entry,) = WiktionaryAdapter()._parse_wikitext("πόλεμος", page)
        assert convert_entry_to_lsr(entry).date_start == -800


class TestOutOfRangeYears:
    @pytest.mark.parametrize("year", [13900, -20000])
    def test_conversion_drops_the_year(self, caplog, year):
        entry = RawLexicalEntry(
            source_name="corpus", source_id="x", form="aprille", language="enm", date_attested=year
        )
        with caplog.at_level(logging.WARNING):
            lsr = convert_entry_to_lsr(entry)
        assert (lsr.date_start, lsr.date_confidence) == (None, 0.0)
        assert str(year) in caplog.text

    def test_the_model_rejects_assignment(self):
        lsr = LSR(form_orthographic="aprille", language_code="enm")
        with pytest.raises(ValidationError):
            lsr.date_start = 13900
        with pytest.raises(ValidationError):
            LSR(form_orthographic="aprille", language_code="enm", date_start=-20000)
        assert lsr.date_start is None

    def test_corpus_typo_is_not_stored(self, tmp_path, monkeypatch, caplog):
        (tmp_path / "doc.txt").write_text("Whan that aprille", encoding="utf-8")
        (tmp_path / "doc.json").write_text(
            json.dumps({"date": 13900, "language": "Middle English", "language_code": "enm"}),
            encoding="utf-8",
        )
        captured = _capture_results(monkeypatch)

        with caplog.at_level(logging.WARNING):
            stats = ingestion.run_corpus_ingestion(corpus_dir=str(tmp_path), dry_run=True)

        assert "13900" in caplog.text
        assert stats.lsrs_created == 3  # whan, that, aprille
        assert all(lsr.date_start is None for lsr in captured["store"].values())


class TestDateConfidence:
    def test_corpus_confidence_reaches_the_lsr(self, tmp_path, monkeypatch):
        (tmp_path / "chaucer.txt").write_text("aprille shoures", encoding="utf-8")
        (tmp_path / "chaucer.json").write_text(
            json.dumps({"date": 1390, "date_confidence": 0.8, "language_code": "enm"}),
            encoding="utf-8",
        )
        (tmp_path / "later.txt").write_text("aprille", encoding="utf-8")
        (tmp_path / "later.json").write_text(
            json.dumps({"date": 1500, "date_confidence": 0.3, "language_code": "enm"}),
            encoding="utf-8",
        )
        captured = _capture_results(monkeypatch)

        ingestion.run_corpus_ingestion(corpus_dir=str(tmp_path), dry_run=True)

        dated = {
            lsr.form_orthographic: (lsr.date_start, lsr.date_confidence)
            for lsr in captured["store"].values()
        }
        # The earliest document dates the word, with its own confidence
        assert dated == {"aprille": (1390, 0.8), "shoures": (1390, 0.8)}

    @pytest.mark.parametrize("confidence", ["high", 1.5, -0.1])
    def test_invalid_corpus_confidence_is_ignored(self, tmp_path, confidence):
        (tmp_path / "doc.txt").write_text("aprille", encoding="utf-8")
        (tmp_path / "doc.json").write_text(
            json.dumps({"date": 1390, "date_confidence": confidence}), encoding="utf-8"
        )
        adapter = CorpusAdapter(corpus_dir=tmp_path)
        adapter.connect()
        (entry,) = list(adapter.fetch_all())
        assert convert_entry_to_lsr(entry).date_confidence == 1.0

    def test_undated_wold_word_keeps_its_age_label(self, wold_dir, monkeypatch):
        captured = _capture_results(monkeypatch)

        ingestion.run_wold_ingestion(data_dir=str(wold_dir), dry_run=True)

        by_form = {lsr.form_orthographic: lsr for lsr in captured["store"].values()}
        welkin, sky = by_form["welkin"], by_form["sky"]
        assert (welkin.date_start, welkin.date_confidence, welkin.period_label) == (
            None,
            0.0,
            "Pre 100 CE",
        )
        assert (sky.date_start, sky.date_confidence) == (1220, 0.9)

    def test_undated_wiktionary_word(self):
        (entry,) = WiktionaryAdapter()._parse_wikitext("water", "==English==\n# A liquid.\n")
        lsr = convert_entry_to_lsr(entry)
        assert (lsr.date_start, lsr.date_confidence) == (None, 0.0)

    def test_merge_takes_the_confidence_of_the_date_it_adopts(self):
        undated = LSR(form_orthographic="water", language_code="eng", date_confidence=0.0)
        dated = LSR(form_orthographic="water", language_code="eng", date_start=900)
        dated.date_confidence = 0.6
        undated.merge_with(dated)
        assert (undated.date_start, undated.date_confidence) == (900, 0.6)

    def test_merge_takes_the_label_of_the_date_it_adopts(self):
        """An undated record's age label does not end up describing another record's date."""
        undated = LSR(
            form_orthographic="sky",
            language_code="eng",
            date_confidence=0.0,
            period_label="Pre 100 CE",
        )
        dated = LSR(
            form_orthographic="sky",
            language_code="eng",
            date_start=1220,
            date_confidence=0.9,
            period_label="c. 1220",
        )
        undated.merge_with(dated)
        assert (undated.date_start, undated.period_label) == (1220, "c. 1220")

        later = LSR(
            form_orthographic="sky",
            language_code="eng",
            date_start=1400,
            date_confidence=1.0,
            period_label="1400",
        )
        undated.merge_with(later)
        assert (undated.date_start, undated.date_confidence, undated.period_label) == (
            1220,
            0.9,
            "c. 1220",
        )


# ---------------------------------------------------------------------------
# Downloads
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "error",
    [
        httpx.ProxyError("proxy refused"),
        httpx.ConnectTimeout("timed out"),
        httpx.RemoteProtocolError("server disconnected"),
        httpx.ReadError("connection reset"),
    ],
)
@pytest.mark.parametrize("adapter_class", [CLLDAdapter, CLICSAdapter])
def test_any_download_failure_is_a_connection_error(tmp_path, monkeypatch, adapter_class, error):
    def fail(request: httpx.Request) -> httpx.Response:
        raise error

    monkeypatch.setattr("time.sleep", lambda seconds: None)
    adapter = adapter_class(data_dir=tmp_path)
    adapter._client = httpx.Client(transport=httpx.MockTransport(fail))
    with pytest.raises(ConnectionError, match="Failed to download"):
        adapter._download_file("https://example.org/forms.csv", tmp_path / "forms.csv")
