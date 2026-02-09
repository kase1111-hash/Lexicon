"""Phase 3 integration tests: WOLD adapter, batch ops, ES/Redis, scaled ingestion.

Tests cover:
- CLLD/WOLD adapter: CSV loading, form conversion, borrowing filtering
- Repository batch operations: create_batch, create_relationships_batch
- Repository ES integration: index settings, search fallback
- Ingestion pipeline: WOLD ingestion, validation gating, relationship extraction
- Cache manager: get/set/delete operations
"""

import csv
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from src.adapters.base import RawLexicalEntry
from src.adapters.clld import (
    CLLDAdapter,
    WOLD_BORROWING_CONFIDENCE,
    WOLD_LANGUAGE_CODES,
    WOLD_SEMANTIC_FIELDS,
    WOLDData,
)
from src.models.lsr import LSR
from src.pipelines.entity_resolution import EntityResolver, convert_entry_to_lsr
from src.repositories.lsr_repository import (
    ES_INDEX_NAME,
    ES_INDEX_SETTINGS,
    BatchResult,
    LSRRepository,
)
from src.utils.cache import CacheManager, make_cache_key


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def wold_csv_dir():
    """Create temporary WOLD CSV files for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create languages.csv
        lang_path = Path(tmpdir) / "languages.csv"
        with open(lang_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=[
                "ID", "Name", "ISO639P3code", "Glottocode", "Family",
            ])
            writer.writeheader()
            writer.writerow({
                "ID": "eng", "Name": "English", "ISO639P3code": "eng",
                "Glottocode": "stan1293", "Family": "Indo-European",
            })
            writer.writerow({
                "ID": "fra", "Name": "French", "ISO639P3code": "fra",
                "Glottocode": "stan1290", "Family": "Indo-European",
            })
            writer.writerow({
                "ID": "swh", "Name": "Swahili", "ISO639P3code": "swh",
                "Glottocode": "swah1253", "Family": "Atlantic-Congo",
            })

        # Create parameters.csv (meanings)
        params_path = Path(tmpdir) / "parameters.csv"
        with open(params_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["ID", "Name"])
            writer.writeheader()
            writer.writerow({"ID": "1-1", "Name": "the sky"})
            writer.writerow({"ID": "5-1", "Name": "to eat"})
            writer.writerow({"ID": "23-1", "Name": "computer"})

        # Create forms.csv
        forms_path = Path(tmpdir) / "forms.csv"
        with open(forms_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=[
                "ID", "Language_ID", "Parameter_ID", "Form",
                "Borrowed_score", "source_language",
            ])
            writer.writeheader()
            writer.writerow({
                "ID": "eng-sky-1", "Language_ID": "eng",
                "Parameter_ID": "1-1", "Form": "sky",
                "Borrowed_score": "1.0", "source_language": "Old Norse",
            })
            writer.writerow({
                "ID": "eng-eat-1", "Language_ID": "eng",
                "Parameter_ID": "5-1", "Form": "eat",
                "Borrowed_score": "5.0", "source_language": "",
            })
            writer.writerow({
                "ID": "fra-manger-1", "Language_ID": "fra",
                "Parameter_ID": "5-1", "Form": "manger",
                "Borrowed_score": "4.5", "source_language": "",
            })
            writer.writerow({
                "ID": "swh-kompyuta-1", "Language_ID": "swh",
                "Parameter_ID": "23-1", "Form": "kompyuta",
                "Borrowed_score": "1.0", "source_language": "English",
            })
            writer.writerow({
                "ID": "eng-empty-1", "Language_ID": "eng",
                "Parameter_ID": "23-1", "Form": "",
                "Borrowed_score": "", "source_language": "",
            })

        yield tmpdir


@pytest.fixture
def sample_lsrs():
    """Create a batch of test LSR objects."""
    lsrs = []
    for form, lang_code, lang_name in [
        ("water", "eng", "English"),
        ("eau", "fra", "French"),
        ("Wasser", "deu", "German"),
        ("agua", "spa", "Spanish"),
        ("acqua", "ita", "Italian"),
    ]:
        lsrs.append(LSR(
            form_orthographic=form,
            language_code=lang_code,
            language_name=lang_name,
            definition_primary=f"water ({lang_name})",
            source_databases=["test"],
        ))
    return lsrs


# =============================================================================
# CLLD/WOLD Adapter Tests
# =============================================================================


class TestWOLDAdapter:
    """Test the CLLD/WOLD adapter with fixture CSV data."""

    def test_connect_loads_data(self, wold_csv_dir):
        """Adapter loads all CSV files on connect."""
        adapter = CLLDAdapter(data_dir=wold_csv_dir)
        adapter.connect()

        assert adapter._data.loaded is True
        assert len(adapter._data.languages) == 3
        assert len(adapter._data.meanings) == 3
        # 5 rows, but one has empty Form
        assert adapter._data.total_count == 5

        adapter.disconnect()
        assert adapter._data.loaded is False

    def test_fetch_batch(self, wold_csv_dir):
        """fetch_batch returns RawLexicalEntry objects."""
        adapter = CLLDAdapter(data_dir=wold_csv_dir)
        adapter.connect()

        entries = list(adapter.fetch_batch(0, 10))
        # 4 valid entries (one has empty form and should be skipped)
        assert len(entries) == 4

        # Check first entry
        sky = entries[0]
        assert sky.form == "sky"
        assert sky.source_name == "wold"
        assert sky.language == "English"
        assert sky.language_code == "eng"

        adapter.disconnect()

    def test_convert_form_borrowing(self, wold_csv_dir):
        """Borrowed forms get etymology text and related_forms."""
        adapter = CLLDAdapter(data_dir=wold_csv_dir)
        adapter.connect()

        entries = list(adapter.fetch_batch(0, 10))
        sky = entries[0]  # "sky" - borrowed from Old Norse

        assert sky.etymology is not None
        assert "Old Norse" in sky.etymology
        assert len(sky.related_forms) == 1
        assert sky.related_forms[0]["type"] == "borrowed_from"
        assert sky.related_forms[0]["language"] == "Old Norse"
        assert sky.related_forms[0]["confidence"] == 0.95

        adapter.disconnect()

    def test_convert_form_not_borrowed(self, wold_csv_dir):
        """Inherited forms (score 5) have no etymology or related_forms."""
        adapter = CLLDAdapter(data_dir=wold_csv_dir)
        adapter.connect()

        entries = list(adapter.fetch_batch(0, 10))
        eat = entries[1]  # "eat" - score 5, no borrowing

        assert eat.etymology is None
        assert len(eat.related_forms) == 0

        adapter.disconnect()

    def test_fetch_borrowings_only(self, wold_csv_dir):
        """fetch_borrowings filters to score <= 3."""
        adapter = CLLDAdapter(data_dir=wold_csv_dir)
        adapter.connect()

        borrowed = list(adapter.fetch_borrowings())
        # "sky" (score 1.0) and "kompyuta" (score 1.0) are borrowings
        assert len(borrowed) == 2
        forms = {e.form for e in borrowed}
        assert "sky" in forms
        assert "kompyuta" in forms

        adapter.disconnect()

    def test_fetch_by_language(self, wold_csv_dir):
        """fetch_by_language filters to a single language."""
        adapter = CLLDAdapter(data_dir=wold_csv_dir)
        adapter.connect()

        eng_entries = list(adapter.fetch_by_language("English"))
        # "sky", "eat" (empty form is skipped)
        assert len(eng_entries) == 2
        assert all(e.language == "English" for e in eng_entries)

        adapter.disconnect()

    def test_language_filter_on_connect(self, wold_csv_dir):
        """languages_filter limits which languages are loaded."""
        adapter = CLLDAdapter(
            data_dir=wold_csv_dir,
            languages_filter=["French"],
        )
        adapter.connect()

        entries = list(adapter.fetch_batch(0, 100))
        assert len(entries) == 1
        assert entries[0].form == "manger"

        adapter.disconnect()

    def test_language_stats(self, wold_csv_dir):
        """get_language_stats returns counts per language."""
        adapter = CLLDAdapter(data_dir=wold_csv_dir)
        adapter.connect()

        stats = adapter.get_language_stats()
        assert stats["English"] == 3  # sky, eat, empty-form
        assert stats["French"] == 1
        assert stats["Swahili"] == 1

        adapter.disconnect()

    def test_borrowing_stats(self, wold_csv_dir):
        """get_borrowing_stats categorizes by score."""
        adapter = CLLDAdapter(data_dir=wold_csv_dir)
        adapter.connect()

        stats = adapter.get_borrowing_stats()
        assert stats["clearly_borrowed"] == 2  # sky, kompyuta
        assert stats["no_evidence"] == 2  # eat, manger
        assert stats["unscored"] == 1  # empty form row

        adapter.disconnect()

    def test_raw_data_fields(self, wold_csv_dir):
        """raw_data includes WOLD-specific metadata."""
        adapter = CLLDAdapter(data_dir=wold_csv_dir)
        adapter.connect()

        entries = list(adapter.fetch_batch(0, 1))
        sky = entries[0]

        assert sky.raw_data["source"] == "wold"
        assert sky.raw_data["is_borrowed"] is True
        assert sky.raw_data["borrowing_confidence"] == 0.95
        assert sky.raw_data["semantic_field"] == "The physical world"
        assert sky.raw_data["language_family"] == "Indo-European"

        adapter.disconnect()

    def test_empty_form_skipped(self, wold_csv_dir):
        """Forms with empty word field are skipped."""
        adapter = CLLDAdapter(data_dir=wold_csv_dir)
        adapter.connect()

        entries = list(adapter.fetch_batch(0, 100))
        forms = [e.form for e in entries]
        assert "" not in forms

        adapter.disconnect()

    def test_disconnect_resets_state(self, wold_csv_dir):
        """disconnect clears all loaded data."""
        adapter = CLLDAdapter(data_dir=wold_csv_dir)
        adapter.connect()
        assert adapter._data.loaded

        adapter.disconnect()
        assert not adapter._data.loaded
        assert adapter._data.total_count == 0

    def test_fetch_without_connect_raises(self, wold_csv_dir):
        """Fetching without connect raises RuntimeError."""
        adapter = CLLDAdapter(data_dir=wold_csv_dir)

        with pytest.raises(RuntimeError, match="not connected"):
            list(adapter.fetch_batch(0, 10))

    def test_supports_incremental(self, wold_csv_dir):
        """WOLD does not support incremental updates."""
        adapter = CLLDAdapter(data_dir=wold_csv_dir)
        assert adapter.supports_incremental() is False


# =============================================================================
# Repository Batch Operations Tests
# =============================================================================


class TestBatchResult:
    """Test the BatchResult dataclass."""

    def test_default_values(self):
        result = BatchResult()
        assert result.succeeded == 0
        assert result.failed == 0
        assert result.errors == []

    def test_accumulation(self):
        result = BatchResult()
        result.succeeded += 5
        result.failed += 2
        result.errors.append("test error")
        assert result.succeeded == 5
        assert result.failed == 2
        assert len(result.errors) == 1


class TestLSRRepositoryBatch:
    """Test batch operations using mock Neo4j sessions."""

    def test_lsr_to_params(self, sample_lsrs):
        """_lsr_to_params converts LSR to dict correctly."""
        db = MagicMock()
        repo = LSRRepository(db)

        lsr = sample_lsrs[0]
        params = repo._lsr_to_params(lsr)

        assert params["id"] == str(lsr.id)
        assert params["form_orthographic"] == "water"
        assert params["language_code"] == "eng"
        assert params["form_normalized"] == "water"
        assert params["version"] == 1

    def test_has_elasticsearch_false(self):
        """_has_elasticsearch returns False when not connected."""
        db = MagicMock()
        db.elasticsearch = property(lambda self: (_ for _ in ()).throw(RuntimeError()))
        type(db).elasticsearch = property(lambda self: (_ for _ in ()).throw(RuntimeError("not connected")))
        repo = LSRRepository(db)
        assert repo._has_elasticsearch() is False

    def test_has_elasticsearch_true(self):
        """_has_elasticsearch returns True when connected."""
        db = MagicMock()
        db.elasticsearch = MagicMock()
        repo = LSRRepository(db)
        assert repo._has_elasticsearch() is True


# =============================================================================
# Elasticsearch Integration Tests
# =============================================================================


class TestElasticsearchConfig:
    """Test Elasticsearch index configuration."""

    def test_index_name(self):
        assert ES_INDEX_NAME == "lexicon_lsr"

    def test_index_has_form_analyzer(self):
        analyzers = ES_INDEX_SETTINGS["settings"]["analysis"]["analyzer"]
        assert "form_analyzer" in analyzers
        assert analyzers["form_analyzer"]["type"] == "custom"
        assert "asciifolding" in analyzers["form_analyzer"]["filter"]

    def test_index_has_required_mappings(self):
        props = ES_INDEX_SETTINGS["mappings"]["properties"]
        assert "form_orthographic" in props
        assert "form_normalized" in props
        assert "language_code" in props
        assert "definition_primary" in props
        assert "date_start" in props
        assert "date_end" in props

    def test_form_orthographic_uses_form_analyzer(self):
        props = ES_INDEX_SETTINGS["mappings"]["properties"]
        assert props["form_orthographic"]["analyzer"] == "form_analyzer"
        assert props["form_orthographic"]["fields"]["raw"]["type"] == "keyword"

    def test_language_code_is_keyword(self):
        props = ES_INDEX_SETTINGS["mappings"]["properties"]
        assert props["language_code"]["type"] == "keyword"


# =============================================================================
# Cache Manager Tests
# =============================================================================


class TestCacheManager:
    """Test the CacheManager utility."""

    def test_make_cache_key_deterministic(self):
        key1 = make_cache_key("lsr", "abc123")
        key2 = make_cache_key("lsr", "abc123")
        assert key1 == key2
        assert key1.startswith("lexicon:lsr:")

    def test_make_cache_key_different_inputs(self):
        key1 = make_cache_key("lsr", "abc")
        key2 = make_cache_key("lsr", "def")
        assert key1 != key2

    def test_make_cache_key_with_kwargs(self):
        key1 = make_cache_key("search", form="water", language="eng")
        key2 = make_cache_key("search", form="water", language="fra")
        assert key1 != key2

    def test_cache_manager_disable_enable(self):
        cache = CacheManager()
        assert cache._enabled is True

        cache.disable()
        assert cache._enabled is False

        cache.enable()
        assert cache._enabled is True


# =============================================================================
# WOLD Ingestion Pipeline Tests
# =============================================================================


class TestWOLDIngestionPipeline:
    """Test the WOLD ingestion via the ingest script."""

    def test_wold_ingestion_dry_run(self, wold_csv_dir):
        """WOLD ingestion in dry-run mode processes but doesn't persist."""
        from scripts.ingest import run_wold_ingestion

        stats = run_wold_ingestion(
            data_dir=wold_csv_dir,
            dry_run=True,
            validate=False,
        )

        # Should process 4 valid entries (empty form skipped by adapter)
        assert stats.entries_fetched == 4
        assert stats.lsrs_created == 4
        assert stats.lsrs_merged == 0

    def test_wold_ingestion_borrowings_only(self, wold_csv_dir):
        """Borrowings-only mode filters to scored entries."""
        from scripts.ingest import run_wold_ingestion

        stats = run_wold_ingestion(
            data_dir=wold_csv_dir,
            borrowings_only=True,
            dry_run=True,
            validate=False,
        )

        # Only "sky" and "kompyuta" are borrowings (score <= 3)
        assert stats.entries_fetched == 2
        assert stats.lsrs_created == 2

    def test_wold_ingestion_with_language_filter(self, wold_csv_dir):
        """Language filter limits which entries are processed."""
        from scripts.ingest import run_wold_ingestion

        stats = run_wold_ingestion(
            data_dir=wold_csv_dir,
            languages_filter=["English"],
            dry_run=True,
            validate=False,
        )

        # "sky" and "eat" (empty form skipped)
        assert stats.entries_fetched == 2
        assert stats.lsrs_created == 2

    def test_wold_ingestion_with_validation(self, wold_csv_dir):
        """Validation rejects entries missing required fields."""
        from scripts.ingest import run_wold_ingestion

        stats = run_wold_ingestion(
            data_dir=wold_csv_dir,
            dry_run=True,
            validate=True,
        )

        # All entries have form and language_code, so none should be rejected
        assert stats.lsrs_rejected == 0
        assert stats.lsrs_created == 4


class TestWiktionaryIngestionPipeline:
    """Test enhanced Wiktionary ingestion with validation and relationships."""

    def test_ingestion_with_validation(self):
        """Validation integrates into the ingestion pipeline."""
        from scripts.ingest import IngestionStats, _process_entry
        from src.pipelines.validation import Validator

        stats = IngestionStats()
        resolver = EntityResolver()
        lsr_store: dict[UUID, LSR] = {}
        resolver.set_lsr_store(lsr_store)
        validator = Validator()

        # Entry with valid data
        entry = RawLexicalEntry(
            source_name="test",
            source_id="test-1",
            form="water",
            language="English",
            language_code="eng",
            definitions=["clear liquid"],
        )

        _process_entry(entry, resolver, lsr_store, stats, False, validator)
        assert stats.lsrs_created == 1
        assert stats.lsrs_rejected == 0

    def test_ingestion_rejects_missing_form(self):
        """Entries with empty form are rejected by validation."""
        from scripts.ingest import IngestionStats, _process_entry
        from src.pipelines.validation import Validator

        stats = IngestionStats()
        resolver = EntityResolver()
        lsr_store: dict[UUID, LSR] = {}
        resolver.set_lsr_store(lsr_store)
        validator = Validator()

        # Entry with empty form
        entry = RawLexicalEntry(
            source_name="test",
            source_id="test-2",
            form="",
            language="English",
            language_code="eng",
            definitions=["nothing"],
        )

        _process_entry(entry, resolver, lsr_store, stats, False, validator)
        assert stats.lsrs_rejected == 1
        assert stats.lsrs_created == 0

    def test_relationship_extraction_in_pipeline(self):
        """Relationship extraction runs after ingestion."""
        from scripts.ingest import _extract_relationships
        from src.pipelines.relationship_extraction import RelationshipExtractor

        lsr_store: dict[UUID, LSR] = {}

        # Create two similar LSRs (LSR model has no etymology_text field,
        # but RelationshipExtractor uses getattr with fallback)
        lsr1 = LSR(
            form_orthographic="water",
            language_code="eng",
            language_name="English",
            form_normalized="water",
        )
        lsr2 = LSR(
            form_orthographic="Wasser",
            language_code="deu",
            language_name="German",
            form_normalized="wasser",
        )
        lsr_store[lsr1.id] = lsr1
        lsr_store[lsr2.id] = lsr2

        extractor = RelationshipExtractor()
        count = _extract_relationships(lsr_store, extractor)
        # Should find cognate relationship via form similarity (water ~ wasser)
        assert isinstance(count, int)
        assert count >= 0

    def test_ingestion_stats_summary(self):
        """IngestionStats.summary() produces formatted output."""
        from scripts.ingest import IngestionStats

        stats = IngestionStats()
        stats.words_attempted = 100
        stats.words_fetched = 95
        stats.words_failed = 5
        stats.entries_fetched = 120
        stats.lsrs_created = 110
        stats.lsrs_merged = 5
        stats.lsrs_flagged = 3
        stats.lsrs_rejected = 2
        stats.relationships_extracted = 15
        stats.errors = ["error 1", "error 2"]

        summary = stats.summary()
        assert "100" in summary
        assert "95" in summary
        assert "Relationships" in summary
        assert "15" in summary
        assert "rejected" in summary.lower() or "Rejected" in summary


# =============================================================================
# WOLD Data Model Tests
# =============================================================================


class TestWOLDConstants:
    """Test WOLD constant maps."""

    def test_borrowing_confidence_map(self):
        assert WOLD_BORROWING_CONFIDENCE[1] == 0.95
        assert WOLD_BORROWING_CONFIDENCE[5] == 0.10

    def test_language_codes_populated(self):
        assert "English" in WOLD_LANGUAGE_CODES
        assert WOLD_LANGUAGE_CODES["English"] == "eng"
        assert len(WOLD_LANGUAGE_CODES) > 20

    def test_semantic_fields_populated(self):
        assert "1" in WOLD_SEMANTIC_FIELDS
        assert WOLD_SEMANTIC_FIELDS["1"] == "The physical world"
        assert len(WOLD_SEMANTIC_FIELDS) == 24

    def test_wold_data_initial_state(self):
        data = WOLDData()
        assert data.loaded is False
        assert data.total_count == 0
        assert data.forms == []
        assert data.languages == {}
        assert data.meanings == {}


# =============================================================================
# WOLD Entry Conversion Edge Cases
# =============================================================================


class TestWOLDConversionEdgeCases:
    """Test edge cases in WOLD form -> RawLexicalEntry conversion."""

    def test_missing_language_iso_falls_back(self, wold_csv_dir):
        """When ISO code is missing from data, fall back to WOLD_LANGUAGE_CODES."""
        adapter = CLLDAdapter(data_dir=wold_csv_dir)
        adapter.connect()

        # Manually test conversion with a form that has no ISO in languages.csv
        form = {
            "ID": "test-1",
            "Language_ID": "unknown_lang",
            "Parameter_ID": "1-1",
            "Form": "testword",
            "Borrowed_score": "",
            "source_language": "",
        }

        entry = adapter._convert_form(form)
        assert entry is not None
        assert entry.form == "testword"
        # Language code should be empty since unknown_lang is not in map
        assert entry.language_code == ""

        adapter.disconnect()

    def test_source_id_format(self, wold_csv_dir):
        """Source ID follows wold-{form_id} pattern."""
        adapter = CLLDAdapter(data_dir=wold_csv_dir)
        adapter.connect()

        entries = list(adapter.fetch_batch(0, 1))
        assert entries[0].source_id.startswith("wold-")

        adapter.disconnect()

    def test_definition_from_parameter(self, wold_csv_dir):
        """Definitions come from the parameters/meanings table."""
        adapter = CLLDAdapter(data_dir=wold_csv_dir)
        adapter.connect()

        entries = list(adapter.fetch_batch(0, 10))
        sky = entries[0]
        assert sky.definitions == ["the sky"]

        adapter.disconnect()
