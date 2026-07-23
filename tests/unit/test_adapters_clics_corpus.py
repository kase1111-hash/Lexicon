"""Unit tests for the CLICS and historical corpus adapters."""

import json

import pytest

from src.adapters.clics import CLICSAdapter
from src.adapters.corpus import CorpusAdapter


@pytest.fixture()
def cldf_dir(tmp_path):
    """A minimal CLDF wordlist where Spanish colexifies DRINK/SMOKE."""
    (tmp_path / "languages.csv").write_text(
        "ID,Name,ISO639P3code,Glottocode,Family\n"
        "spa,Spanish,spa,stan1288,Indo-European\n"
        "eng,English,eng,stan1293,Indo-European\n",
        encoding="utf-8",
    )
    (tmp_path / "parameters.csv").write_text(
        "ID,Name,Concepticon_Gloss\n"
        "drink,to drink,DRINK\n"
        "smoke,to smoke,SMOKE\n"
        "water,water,WATER\n",
        encoding="utf-8",
    )
    (tmp_path / "forms.csv").write_text(
        "ID,Language_ID,Parameter_ID,Form\n"
        "1,spa,drink,tomar\n"
        "2,spa,smoke,tomar\n"
        "3,spa,water,agua\n"
        "4,eng,drink,drink\n"
        "5,eng,smoke,smoke\n",
        encoding="utf-8",
    )
    return tmp_path


class TestCLICSAdapter:
    """Tests for colexification extraction from CLDF data."""

    def test_connect_and_counts(self, cldf_dir):
        adapter = CLICSAdapter(data_dir=cldf_dir)
        adapter.connect()
        try:
            # 4 distinct (language, form) groups: tomar, agua, drink, smoke
            assert adapter.get_total_count() == 4
        finally:
            adapter.disconnect()

    def test_colexification_grouping(self, cldf_dir):
        adapter = CLICSAdapter(data_dir=cldf_dir)
        adapter.connect()
        try:
            entries = {e.form: e for e in adapter.fetch_batch(0, 100)}
            tomar = entries["tomar"]
            assert tomar.language == "Spanish"
            assert tomar.language_code == "spa"
            assert tomar.raw_data["colexified_concepts"] == ["DRINK", "SMOKE"]
            assert tomar.raw_data["colexification_degree"] == 2
            assert tomar.definitions == ["DRINK", "SMOKE"]
            # Non-colexified form has degree 1
            assert entries["agua"].raw_data["colexification_degree"] == 1
        finally:
            adapter.disconnect()

    def test_min_colexifications_filter(self, cldf_dir):
        adapter = CLICSAdapter(data_dir=cldf_dir, min_colexifications=2)
        adapter.connect()
        try:
            forms = [e.form for e in adapter.fetch_batch(0, 100)]
            assert forms == ["tomar"]
        finally:
            adapter.disconnect()

    def test_languages_filter(self, cldf_dir):
        adapter = CLICSAdapter(data_dir=cldf_dir, languages_filter=["English"])
        adapter.connect()
        try:
            languages = {e.language for e in adapter.fetch_batch(0, 100)}
            assert languages == {"English"}
        finally:
            adapter.disconnect()

    def test_colexification_pairs(self, cldf_dir):
        adapter = CLICSAdapter(data_dir=cldf_dir)
        adapter.connect()
        try:
            pairs = adapter.get_colexification_pairs()
            assert pairs == {("DRINK", "SMOKE"): 1}
        finally:
            adapter.disconnect()

    def test_fetch_before_connect_raises(self, cldf_dir):
        adapter = CLICSAdapter(data_dir=cldf_dir)
        with pytest.raises(RuntimeError, match="not connected"):
            list(adapter.fetch_batch(0, 10))

    def test_missing_forms_file_raises(self, tmp_path, monkeypatch):
        import src.adapters.clics as clics_module

        monkeypatch.setattr(clics_module.time, "sleep", lambda _s: None)
        adapter = CLICSAdapter(data_dir=tmp_path, base_url="http://127.0.0.1:1/nope")
        with pytest.raises(ConnectionError):
            adapter.connect()


@pytest.fixture()
def corpus_dir(tmp_path):
    """A two-document corpus with sidecar and corpus-wide metadata."""
    (tmp_path / "beowulf.txt").write_text(
        "The dragon guarded the treasure hoard.", encoding="utf-8"
    )
    (tmp_path / "beowulf.json").write_text(
        json.dumps({"date": 1000, "title": "Beowulf", "source": "manuscript"}),
        encoding="utf-8",
    )
    (tmp_path / "modern.txt").write_text("The computer guarded the network.", encoding="utf-8")
    (tmp_path / "metadata.json").write_text(
        json.dumps({"modern.txt": {"date": 1990}}), encoding="utf-8"
    )
    return tmp_path


class TestCorpusAdapter:
    """Tests for local corpus scanning."""

    def test_connect_builds_entries(self, corpus_dir):
        adapter = CorpusAdapter(corpus_dir=corpus_dir)
        adapter.connect()
        try:
            assert adapter.get_total_count() > 0
            entries = {e.form: e for e in adapter.fetch_batch(0, 100)}
            assert "dragon" in entries
            assert "computer" in entries
            # Stop-length words are excluded (default min length 3)
            assert "the" in entries  # 'the' has length 3, so included
        finally:
            adapter.disconnect()

    def test_attestations_carry_dates_and_sources(self, corpus_dir):
        adapter = CorpusAdapter(corpus_dir=corpus_dir)
        adapter.connect()
        try:
            entries = {e.form: e for e in adapter.fetch_batch(0, 100)}
            dragon = entries["dragon"]
            assert dragon.date_attested == 1000
            assert dragon.attestations[0]["text_source"] == "manuscript"
            assert "dragon" in dragon.attestations[0]["text_excerpt"]

            computer = entries["computer"]
            assert computer.date_attested == 1990
        finally:
            adapter.disconnect()

    def test_word_in_both_documents_gets_earliest_date(self, corpus_dir):
        adapter = CorpusAdapter(corpus_dir=corpus_dir)
        adapter.connect()
        try:
            entries = {e.form: e for e in adapter.fetch_batch(0, 100)}
            guarded = entries["guarded"]
            assert guarded.date_attested == 1000
            assert len(guarded.attestations) == 2
            assert guarded.raw_data["document_count"] == 2
        finally:
            adapter.disconnect()

    def test_missing_directory_raises(self, tmp_path):
        adapter = CorpusAdapter(corpus_dir=tmp_path / "nope")
        with pytest.raises(ConnectionError):
            adapter.connect()

    def test_min_word_length(self, corpus_dir):
        adapter = CorpusAdapter(corpus_dir=corpus_dir, min_word_length=6)
        adapter.connect()
        try:
            forms = {e.form for e in adapter.fetch_batch(0, 100)}
            assert "dragon" in forms
            assert "the" not in forms
        finally:
            adapter.disconnect()

    def test_fetch_before_connect_raises(self, corpus_dir):
        adapter = CorpusAdapter(corpus_dir=corpus_dir)
        with pytest.raises(RuntimeError, match="not connected"):
            list(adapter.fetch_batch(0, 10))


class TestCorpusIngestionEndToEnd:
    """Corpus entries run through the full ingestion pipeline."""

    def test_dry_run_ingestion(self, corpus_dir):
        from src.ingestion import run_corpus_ingestion

        stats = run_corpus_ingestion(corpus_dir=str(corpus_dir), dry_run=True)
        assert stats.lsrs_created > 0
        assert not stats.errors


class TestCLICSIngestionEndToEnd:
    """CLICS entries run through the full ingestion pipeline."""

    def test_dry_run_ingestion(self, cldf_dir):
        from src.ingestion import run_clics_ingestion

        stats = run_clics_ingestion(data_dir=str(cldf_dir), dry_run=True)
        assert stats.lsrs_created > 0
        assert not stats.errors
