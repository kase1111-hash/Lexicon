"""Phase 3 integration tests: WOLD adapter, batch ops, ES/Redis, scaled ingestion.

Tests cover:
- CLLD/WOLD adapter: CSV loading, form conversion, borrowing filtering
- Repository batch operations: create_batch, create_relationships_batch
- Repository ES integration: index settings, search fallback
- Ingestion pipeline: WOLD ingestion, validation gating, relationship extraction
- Cache manager: get/set/delete operations
"""

import asyncio
import csv
import tempfile
from pathlib import Path
from unittest.mock import MagicMock
from uuid import UUID

import pytest

from src.adapters.base import RawLexicalEntry
from src.adapters.clld import (
    WOLD_BORROWING_CONFIDENCE,
    WOLD_LANGUAGE_CODES,
    WOLD_SEMANTIC_FIELDS,
    CLLDAdapter,
    WOLDData,
)
from src.models.lsr import LSR
from src.pipelines.entity_resolution import EntityResolver
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
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "ID",
                    "Name",
                    "ISO639P3code",
                    "Glottocode",
                    "Family",
                ],
            )
            writer.writeheader()
            writer.writerow(
                {
                    "ID": "eng",
                    "Name": "English",
                    "ISO639P3code": "eng",
                    "Glottocode": "stan1293",
                    "Family": "Indo-European",
                }
            )
            writer.writerow(
                {
                    "ID": "fra",
                    "Name": "French",
                    "ISO639P3code": "fra",
                    "Glottocode": "stan1290",
                    "Family": "Indo-European",
                }
            )
            writer.writerow(
                {
                    "ID": "swh",
                    "Name": "Swahili",
                    "ISO639P3code": "swh",
                    "Glottocode": "swah1253",
                    "Family": "Atlantic-Congo",
                }
            )

        # Create parameters.csv (meanings)
        params_path = Path(tmpdir) / "parameters.csv"
        with open(params_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["ID", "Name"])
            writer.writeheader()
            writer.writerow({"ID": "1-1", "Name": "the sky"})
            writer.writerow({"ID": "5-1", "Name": "to eat"})
            writer.writerow({"ID": "23-1", "Name": "computer"})

        # Create forms.csv (real WOLD CLDF columns: `Borrowed` category text,
        # `Borrowed_score` 1.0 = clearly borrowed ... 0.0 = no evidence, `Age`)
        forms_path = Path(tmpdir) / "forms.csv"
        with open(forms_path, "w", newline="") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "ID",
                    "Language_ID",
                    "Parameter_ID",
                    "Form",
                    "Borrowed",
                    "Borrowed_score",
                    "Age",
                ],
            )
            writer.writeheader()
            for row in [
                ("eng-sky-1", "eng", "1-1", "sky", "1. clearly borrowed", "1.0", "c. 1220"),
                (
                    "eng-eat-1",
                    "eng",
                    "5-1",
                    "eat",
                    "5. no evidence for borrowing",
                    "0.0",
                    "Proto-Germanic",
                ),
                (
                    "fra-manger-1",
                    "fra",
                    "5-1",
                    "manger",
                    "4. very little evidence for borrowing",
                    "0.25",
                    "",
                ),
                ("swh-kompyuta-1", "swh", "23-1", "kompyuta", "1. clearly borrowed", "1.0", ""),
                ("eng-empty-1", "eng", "23-1", "", "", "", ""),
            ]:
                writer.writerow(dict(zip(writer.fieldnames, row, strict=True)))

        # Create borrowings.csv (donor language/word per borrowed form)
        borrowings_path = Path(tmpdir) / "borrowings.csv"
        with open(borrowings_path, "w", newline="") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "ID",
                    "Target_Form_ID",
                    "Source_word",
                    "Source_meaning",
                    "Source_relation",
                    "Source_certain",
                    "Source_languoid",
                    "Source_languoid_glottocode",
                ],
            )
            writer.writeheader()
            writer.writerow(
                {
                    "ID": "1",
                    "Target_Form_ID": "eng-sky-1",
                    "Source_word": "ský",
                    "Source_meaning": "cloud",
                    "Source_relation": "immediate",
                    "Source_certain": "yes",
                    "Source_languoid": "Old Norse",
                    "Source_languoid_glottocode": "oldn1244",
                }
            )
            writer.writerow(
                {
                    "ID": "2",
                    "Target_Form_ID": "swh-kompyuta-1",
                    "Source_word": "computer",
                    "Source_meaning": "computer",
                    "Source_relation": "immediate",
                    "Source_certain": "yes",
                    "Source_languoid": "English",
                    "Source_languoid_glottocode": "stan1293",
                }
            )

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
        lsrs.append(
            LSR(
                form_orthographic=form,
                language_code=lang_code,
                language_name=lang_name,
                definition_primary=f"water ({lang_name})",
                source_databases=["test"],
            )
        )
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
        """Inherited forms (category 5) have no etymology or related_forms."""
        adapter = CLLDAdapter(data_dir=wold_csv_dir)
        adapter.connect()

        entries = list(adapter.fetch_batch(0, 10))
        eat = entries[1]  # "eat" - score 5, no borrowing

        assert eat.etymology is None
        assert len(eat.related_forms) == 0

        adapter.disconnect()

    def test_fetch_borrowings_only(self, wold_csv_dir):
        """fetch_borrowings keeps categories 1-3 (clearly/probably/perhaps borrowed)."""
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
        assert stats["little_evidence"] == 1  # manger
        assert stats["no_evidence"] == 1  # eat
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
        type(db).elasticsearch = property(
            lambda self: (_ for _ in ()).throw(RuntimeError("not connected"))
        )
        repo = LSRRepository(db)
        assert repo._has_elasticsearch() is False

    def test_has_elasticsearch_true(self):
        """_has_elasticsearch returns True when connected."""
        db = MagicMock()
        db.elasticsearch = MagicMock()
        repo = LSRRepository(db)
        assert repo._has_elasticsearch() is True

    def test_search_fallback_is_marked_degraded(self):
        """A form search that falls back to Neo4j says so, so it is not cached."""
        repo = LSRRepository(MagicMock())

        async def failing_es(**kwargs):
            raise RuntimeError("index unassigned")

        async def neo4j(**kwargs):
            return [], 0

        repo._has_elasticsearch = lambda: True  # type: ignore[method-assign]
        repo._search_elasticsearch = failing_es  # type: ignore[method-assign]
        repo._search_neo4j = neo4j  # type: ignore[method-assign]
        assert repo.search_degraded is False
        assert asyncio.run(repo.search(form="sky")) == ([], 0)
        assert repo.search_degraded is True


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

        # Only "sky" and "kompyuta" are borrowings (categories 1-3)
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

        _process_entry(entry, resolver, lsr_store, stats, validator)
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

        _process_entry(entry, resolver, lsr_store, stats, validator)
        assert stats.lsrs_rejected == 1
        assert stats.lsrs_created == 0

    def test_relationship_extraction_in_pipeline(self):
        """Relationship extraction runs after ingestion."""
        from scripts.ingest import _extract_relationships
        from src.pipelines.relationship_extraction import RelationshipExtractor

        lsr_store: dict[UUID, LSR] = {}

        # An English LSR whose etymology names an Old English ancestor that is
        # also in the store, plus a look-alike German word
        lsr1 = LSR(
            form_orthographic="water",
            language_code="eng",
            language_name="English",
            etymology_text="From Middle English water, from Old English wæter",
        )
        ancestor = LSR(form_orthographic="wæter", language_code="ang", language_name="Old English")
        lookalike = LSR(form_orthographic="Wasser", language_code="deu", language_name="German")
        for lsr in (lsr1, ancestor, lookalike):
            lsr_store[lsr.id] = lsr

        extractor = RelationshipExtractor()
        count = _extract_relationships(lsr_store, extractor)

        # One DESCENDS_FROM edge water -> wæter; no form-similarity "cognate"
        assert count == 1

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
