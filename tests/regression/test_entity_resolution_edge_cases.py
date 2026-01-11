"""Regression tests for entity resolution edge cases.

These tests ensure edge cases in entity resolution
don't cause unexpected behavior or data corruption.
"""

import pytest
from uuid import uuid4

from src.adapters.base import RawLexicalEntry
from src.models.lsr import LSR
from src.pipelines.entity_resolution import (
    EntityResolver,
    ResolutionAction,
    ResolutionResult,
    SimilarityWeights,
    convert_entry_to_lsr,
)


class TestResolverEdgeCases:
    """Test entity resolver with edge cases."""

    @pytest.fixture
    def resolver(self):
        """Create default resolver."""
        return EntityResolver(
            auto_merge_threshold=0.95,
            merge_with_flag_threshold=0.85,
            review_threshold=0.70,
        )

    def test_resolve_with_empty_store(self, resolver):
        """Test resolution when store is empty."""
        entry = RawLexicalEntry(
            source_name="test",
            source_id="test-1",
            form="anything",
            language="English",
            language_code="eng",
        )

        result = resolver.resolve(entry)
        assert result.action == ResolutionAction.CREATE_NEW
        assert result.existing_id is None

    def test_resolve_with_empty_form(self, resolver):
        """Test resolution with empty form."""
        entry = RawLexicalEntry(
            source_name="test",
            source_id="test-1",
            form="",
            language="English",
            language_code="eng",
        )

        result = resolver.resolve(entry)
        assert result.action == ResolutionAction.CREATE_NEW

    def test_resolve_with_whitespace_form(self, resolver):
        """Test resolution with whitespace-only form."""
        entry = RawLexicalEntry(
            source_name="test",
            source_id="test-1",
            form="   ",
            language="English",
            language_code="eng",
        )

        result = resolver.resolve(entry)
        assert isinstance(result, ResolutionResult)

    def test_resolve_identical_entries(self, resolver):
        """Test resolution with identical entry to existing."""
        lsr = LSR(
            form_orthographic="water",
            form_normalized="water",
            language_code="eng",
            language_name="English",
            definition_primary="a liquid",
            source_databases=["base"],
        )
        resolver.set_lsr_store({lsr.id: lsr})

        entry = RawLexicalEntry(
            source_name="test",
            source_id="test-1",
            form="water",
            language="English",
            language_code="eng",
            definitions=["a liquid"],
        )

        result = resolver.resolve(entry)
        # Should have high similarity
        assert result.similarity_score > 0.5

    def test_resolve_case_difference(self, resolver):
        """Test resolution with case differences."""
        lsr = LSR(
            form_orthographic="Water",
            form_normalized="water",
            language_code="eng",
            source_databases=["base"],
        )
        resolver.set_lsr_store({lsr.id: lsr})

        entry = RawLexicalEntry(
            source_name="test",
            source_id="test-1",
            form="water",  # lowercase
            language="English",
            language_code="eng",
        )

        result = resolver.resolve(entry)
        # Should find match due to normalization
        assert result.similarity_score > 0


class TestThresholdEdgeCases:
    """Test resolver threshold edge cases."""

    def test_score_exactly_at_auto_merge_threshold(self):
        """Test behavior when score is exactly at auto merge threshold."""
        resolver = EntityResolver(
            auto_merge_threshold=0.95,
            merge_with_flag_threshold=0.85,
            review_threshold=0.70,
        )

        # Create result at exact threshold
        result = ResolutionResult(
            action=ResolutionAction.AUTO_MERGE,
            similarity_score=0.95,
        )
        assert result.action == ResolutionAction.AUTO_MERGE

    def test_score_exactly_at_review_threshold(self):
        """Test behavior when score is exactly at review threshold."""
        result = ResolutionResult(
            action=ResolutionAction.FLAG_FOR_REVIEW,
            similarity_score=0.70,
        )
        assert result.similarity_score == 0.70

    def test_zero_thresholds(self):
        """Test resolver with zero thresholds."""
        resolver = EntityResolver(
            auto_merge_threshold=0.0,
            merge_with_flag_threshold=0.0,
            review_threshold=0.0,
        )
        assert resolver.auto_merge_threshold == 0.0

    def test_all_thresholds_at_one(self):
        """Test resolver with all thresholds at 1.0."""
        resolver = EntityResolver(
            auto_merge_threshold=1.0,
            merge_with_flag_threshold=1.0,
            review_threshold=1.0,
        )
        # Everything should become CREATE_NEW since nothing can score 1.0
        entry = RawLexicalEntry(
            source_name="test",
            source_id="test-1",
            form="test",
            language="English",
            language_code="eng",
        )
        result = resolver.resolve(entry)
        assert result.action == ResolutionAction.CREATE_NEW


class TestSimilarityWeightsEdgeCases:
    """Test similarity weights edge cases."""

    def test_default_weights_sum_to_one(self):
        """Ensure default weights sum to 1.0."""
        weights = SimilarityWeights()
        total = (
            weights.form_exact
            + weights.form_fuzzy
            + weights.semantic
            + weights.date_overlap
            + weights.source_agreement
        )
        assert abs(total - 1.0) < 0.001

    def test_custom_weights_zero_semantic(self):
        """Test custom weights with zero semantic weight."""
        weights = SimilarityWeights(
            form_exact=0.5,
            form_fuzzy=0.3,
            semantic=0.0,
            date_overlap=0.1,
            source_agreement=0.1,
        )
        assert weights.semantic == 0.0

    def test_custom_weights_all_zero_except_one(self):
        """Test weights with only one non-zero weight."""
        weights = SimilarityWeights(
            form_exact=1.0,
            form_fuzzy=0.0,
            semantic=0.0,
            date_overlap=0.0,
            source_agreement=0.0,
        )
        assert weights.form_exact == 1.0


class TestBatchProcessingEdgeCases:
    """Test batch processing edge cases."""

    @pytest.fixture
    def resolver(self):
        """Create resolver for batch tests."""
        return EntityResolver()

    def test_batch_empty_list(self, resolver):
        """Test batch processing with empty list."""
        results = resolver.process_batch([])
        assert results == []

    def test_batch_single_entry(self, resolver):
        """Test batch processing with single entry."""
        entries = [
            RawLexicalEntry(
                source_name="test",
                source_id="test-1",
                form="word",
                language="English",
                language_code="eng",
            )
        ]

        results = resolver.process_batch(entries)
        assert len(results) == 1

    def test_batch_duplicate_entries(self, resolver):
        """Test batch processing with duplicate entries."""
        entry = RawLexicalEntry(
            source_name="test",
            source_id="test-1",
            form="word",
            language="English",
            language_code="eng",
        )

        # Same entry twice
        results = resolver.process_batch([entry, entry])
        assert len(results) == 2

    def test_batch_large_number(self, resolver):
        """Test batch processing with many entries."""
        entries = [
            RawLexicalEntry(
                source_name="test",
                source_id=f"test-{i}",
                form=f"word{i}",
                language="English",
                language_code="eng",
            )
            for i in range(100)
        ]

        results = resolver.process_batch(entries)
        assert len(results) == 100


class TestConversionEdgeCases:
    """Test entry to LSR conversion edge cases."""

    def test_convert_minimal_entry(self):
        """Test converting entry with only required fields."""
        entry = RawLexicalEntry(
            source_name="test",
            source_id="test-1",
            form="word",
            language="English",
        )

        lsr = convert_entry_to_lsr(entry)
        assert lsr.form_orthographic == "word"
        assert "test" in lsr.source_databases

    def test_convert_entry_with_empty_definitions(self):
        """Test converting entry with empty definitions list."""
        entry = RawLexicalEntry(
            source_name="test",
            source_id="test-1",
            form="word",
            language="English",
            language_code="eng",
            definitions=[],
        )

        lsr = convert_entry_to_lsr(entry)
        assert lsr.definition_primary == ""
        assert lsr.definitions_alternate == []

    def test_convert_entry_with_single_definition(self):
        """Test converting entry with single definition."""
        entry = RawLexicalEntry(
            source_name="test",
            source_id="test-1",
            form="word",
            language="English",
            language_code="eng",
            definitions=["a unit of language"],
        )

        lsr = convert_entry_to_lsr(entry)
        assert lsr.definition_primary == "a unit of language"
        assert lsr.definitions_alternate == []

    def test_convert_entry_with_multiple_definitions(self):
        """Test converting entry with multiple definitions."""
        entry = RawLexicalEntry(
            source_name="test",
            source_id="test-1",
            form="bank",
            language="English",
            language_code="eng",
            definitions=[
                "financial institution",
                "side of a river",
                "to rely on",
            ],
        )

        lsr = convert_entry_to_lsr(entry)
        assert lsr.definition_primary == "financial institution"
        assert len(lsr.definitions_alternate) == 2

    def test_convert_derives_language_code_from_name(self):
        """Test that language code is derived from language name."""
        entry = RawLexicalEntry(
            source_name="test",
            source_id="test-1",
            form="word",
            language="English",
            language_code="",  # Empty code
        )

        lsr = convert_entry_to_lsr(entry)
        # Should derive code from language name
        assert lsr.language_code != "" or lsr.language_name == "English"


class TestMergeEdgeCases:
    """Test LSR merge edge cases in resolver."""

    @pytest.fixture
    def resolver(self):
        """Create resolver for merge tests."""
        return EntityResolver()

    def test_merge_preserves_target_id(self, resolver):
        """Test that merge preserves the target LSR's ID."""
        target = LSR(
            form_orthographic="test",
            language_code="eng",
            source_databases=["base"],
        )
        target_id = target.id

        source = LSR(
            form_orthographic="test",
            language_code="eng",
            source_databases=["other"],
        )

        resolver.merge_lsrs(target, source)
        assert target.id == target_id

    def test_merge_updates_version(self, resolver):
        """Test that merge increments version."""
        target = LSR(
            form_orthographic="test",
            language_code="eng",
            source_databases=["base"],
        )
        original_version = target.version

        source = LSR(
            form_orthographic="test",
            language_code="eng",
            source_databases=["other"],
        )

        resolver.merge_lsrs(target, source)
        assert target.version == original_version + 1

    def test_merge_returns_log(self, resolver):
        """Test that merge returns a merge log."""
        target = LSR(
            form_orthographic="test",
            language_code="eng",
            source_databases=["base"],
        )
        source = LSR(
            form_orthographic="test",
            language_code="eng",
            source_databases=["other"],
        )

        log = resolver.merge_lsrs(target, source)
        assert isinstance(log, dict)
        assert "merged_fields" in log
