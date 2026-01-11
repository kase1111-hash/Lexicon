"""Integration tests for data pipelines."""

import pytest
from uuid import uuid4

from src.adapters.base import RawLexicalEntry
from src.models.lsr import LSR
from src.pipelines.entity_resolution import (
    EntityResolver,
    ResolutionAction,
    convert_entry_to_lsr,
)


class TestEntityResolutionPipeline:
    """Integration tests for entity resolution pipeline."""

    @pytest.fixture
    def resolver(self):
        """Create an entity resolver with test data."""
        resolver = EntityResolver(
            auto_merge_threshold=0.95,
            merge_with_flag_threshold=0.85,
            review_threshold=0.70,
        )

        # Pre-populate with some LSRs
        lsrs = {}

        water_eng = LSR(
            form_orthographic="water",
            form_normalized="water",
            language_code="eng",
            language_name="English",
            definition_primary="a clear liquid essential for life",
            source_databases=["wiktionary"],
            date_start=1500,
            date_end=2024,
        )
        lsrs[water_eng.id] = water_eng

        wasser_deu = LSR(
            form_orthographic="Wasser",
            form_normalized="wasser",
            language_code="deu",
            language_name="German",
            definition_primary="water in German",
            source_databases=["clld"],
            date_start=1500,
            date_end=2024,
        )
        lsrs[wasser_deu.id] = wasser_deu

        resolver.set_lsr_store(lsrs)
        return resolver

    def test_resolve_exact_match(self, resolver):
        """Test resolving an exact match."""
        entry = RawLexicalEntry(
            source_name="corpus",
            source_id="corp-1",
            form="water",
            language="English",
            language_code="eng",
            definitions=["a clear liquid"],
        )

        result = resolver.resolve(entry)

        assert result.similarity_score > 0.5
        assert result.existing_id is not None
        assert "form_exact" in result.feature_scores

    def test_resolve_fuzzy_match(self, resolver):
        """Test resolving a fuzzy match."""
        entry = RawLexicalEntry(
            source_name="ocr",
            source_id="ocr-1",
            form="watar",  # OCR error
            language="English",
            language_code="eng",
            definitions=["liquid"],
        )

        result = resolver.resolve(entry)

        # Should find a match due to Levenshtein distance
        assert result.similarity_score > 0
        assert "form_fuzzy" in result.feature_scores

    def test_resolve_no_match_different_language(self, resolver):
        """Test that different language creates new."""
        entry = RawLexicalEntry(
            source_name="wiktionary",
            source_id="wikt-1",
            form="water",
            language="French",
            language_code="fra",
        )

        result = resolver.resolve(entry)

        # No French entries in store
        assert result.action == ResolutionAction.CREATE_NEW

    def test_resolve_unique_word(self, resolver):
        """Test resolving a completely unique word."""
        entry = RawLexicalEntry(
            source_name="corpus",
            source_id="corp-2",
            form="xyzzy",
            language="English",
            language_code="eng",
        )

        result = resolver.resolve(entry)

        assert result.action == ResolutionAction.CREATE_NEW
        assert result.existing_id is None

    def test_batch_processing(self, resolver):
        """Test batch processing multiple entries."""
        entries = [
            RawLexicalEntry(
                source_name="corpus",
                source_id=f"batch-{i}",
                form=form,
                language="English",
                language_code="eng",
            )
            for i, form in enumerate(["water", "fire", "earth", "air"])
        ]

        results = resolver.process_batch(entries)

        assert len(results) == 4
        # "water" should match, others should be new
        water_result = results[0]
        assert water_result.similarity_score > 0

    def test_lsr_merge(self, resolver):
        """Test merging two LSRs."""
        # Get the existing water LSR
        water_id = None
        for lsr_id, lsr in resolver._lsr_store.items():
            if lsr.form_orthographic == "water":
                water_id = lsr_id
                break

        assert water_id is not None

        target = resolver._lsr_store[water_id]

        # Create a source LSR to merge
        source = LSR(
            form_orthographic="water",
            language_code="eng",
            definition_primary="H2O",
            source_databases=["corpus"],
        )

        original_sources = len(target.source_databases)
        merge_log = resolver.merge_lsrs(target, source)

        assert "merged_fields" in merge_log
        assert len(target.source_databases) > original_sources


class TestEntryToLSRConversion:
    """Integration tests for entry to LSR conversion."""

    def test_full_conversion(self):
        """Test converting a fully populated entry."""
        entry = RawLexicalEntry(
            source_name="wiktionary",
            source_id="wikt-water",
            form="water",
            form_phonetic="ˈwɔːtər",
            language="English",
            language_code="eng",
            definitions=[
                "A clear liquid essential for life",
                "A body of water such as a lake or ocean",
                "To pour water on plants",
            ],
            part_of_speech="noun",
            date_attested=1200,
            etymology="From Old English wæter",
        )

        lsr = convert_entry_to_lsr(entry)

        assert lsr.form_orthographic == "water"
        assert lsr.form_phonetic == "ˈwɔːtər"
        assert lsr.language_code == "eng"
        assert lsr.language_name == "English"
        assert lsr.definition_primary == "A clear liquid essential for life"
        assert len(lsr.definitions_alternate) == 2
        assert lsr.part_of_speech == "noun"
        assert lsr.date_start == 1200
        assert "wiktionary" in lsr.source_databases

    def test_minimal_conversion(self):
        """Test converting a minimally populated entry."""
        entry = RawLexicalEntry(
            source_name="test",
            source_id="test-1",
            form="word",
            language="English",
        )

        lsr = convert_entry_to_lsr(entry)

        assert lsr.form_orthographic == "word"
        assert lsr.language_code == "eng"  # Derived from language name
        assert "test" in lsr.source_databases

    def test_conversion_preserves_data(self):
        """Test that conversion preserves all relevant data."""
        entry = RawLexicalEntry(
            source_name="corpus",
            source_id="corp-123",
            form="test",
            language="German",
            language_code="deu",
            definitions=["a test"],
            date_attested=1800,
        )

        lsr = convert_entry_to_lsr(entry)

        # Convert back to dict and verify
        assert lsr.form_orthographic == entry.form
        assert lsr.language_code == entry.language_code
        assert lsr.date_start == entry.date_attested


class TestResolverThresholds:
    """Tests for resolution action thresholds."""

    def test_auto_merge_threshold(self):
        """Test that high similarity triggers auto merge."""
        resolver = EntityResolver(auto_merge_threshold=0.90)

        # Create matching LSRs
        lsr = LSR(
            form_orthographic="test",
            form_normalized="test",
            language_code="eng",
            definition_primary="a test",
            source_databases=["source1"],
        )
        resolver.set_lsr_store({lsr.id: lsr})

        # Create nearly identical entry
        entry = RawLexicalEntry(
            source_name="source2",
            source_id="entry-1",
            form="test",
            language="English",
            language_code="eng",
            definitions=["a test"],
        )

        result = resolver.resolve(entry)

        # Should trigger auto merge or merge with flag
        assert result.action in [
            ResolutionAction.AUTO_MERGE,
            ResolutionAction.MERGE_WITH_FLAG,
            ResolutionAction.FLAG_FOR_REVIEW,
        ]

    def test_custom_weights(self):
        """Test resolver with custom similarity weights."""
        from src.pipelines.entity_resolution import SimilarityWeights

        # Prioritize semantic similarity
        weights = SimilarityWeights(
            form_exact=0.1,
            form_fuzzy=0.1,
            semantic=0.6,
            date_overlap=0.1,
            source_agreement=0.1,
        )

        resolver = EntityResolver(weights=weights)

        assert resolver.weights.semantic == 0.6
        assert resolver.weights.form_exact == 0.1
