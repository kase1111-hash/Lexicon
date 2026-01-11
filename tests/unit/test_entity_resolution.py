"""Unit tests for entity resolution pipeline."""

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


class TestResolutionAction:
    """Tests for ResolutionAction enum."""

    def test_action_values(self):
        """Test resolution action values."""
        assert ResolutionAction.AUTO_MERGE == "auto_merge"
        assert ResolutionAction.MERGE_WITH_FLAG == "merge_with_flag"
        assert ResolutionAction.FLAG_FOR_REVIEW == "flag_for_review"
        assert ResolutionAction.CREATE_NEW == "create_new"


class TestResolutionResult:
    """Tests for ResolutionResult model."""

    def test_result_creation(self):
        """Test result creation with defaults."""
        result = ResolutionResult(action=ResolutionAction.CREATE_NEW)
        assert result.action == ResolutionAction.CREATE_NEW
        assert result.existing_id is None
        assert result.similarity_score == 0.0
        assert result.feature_scores == {}
        assert result.issues == []

    def test_result_with_match(self):
        """Test result with a match."""
        match_id = uuid4()
        result = ResolutionResult(
            action=ResolutionAction.AUTO_MERGE,
            existing_id=match_id,
            similarity_score=0.98,
            feature_scores={"form_exact": 1.0, "semantic": 0.95},
        )
        assert result.existing_id == match_id
        assert result.similarity_score == 0.98


class TestSimilarityWeights:
    """Tests for SimilarityWeights model."""

    def test_default_weights(self):
        """Test default similarity weights."""
        weights = SimilarityWeights()
        assert weights.form_exact == 0.3
        assert weights.form_fuzzy == 0.2
        assert weights.semantic == 0.3
        assert weights.date_overlap == 0.1
        assert weights.source_agreement == 0.1

    def test_weights_sum(self):
        """Test that default weights sum to 1.0."""
        weights = SimilarityWeights()
        total = (
            weights.form_exact
            + weights.form_fuzzy
            + weights.semantic
            + weights.date_overlap
            + weights.source_agreement
        )
        assert abs(total - 1.0) < 0.001


class TestEntityResolver:
    """Tests for EntityResolver class."""

    @pytest.fixture
    def resolver(self):
        """Create a resolver instance."""
        return EntityResolver(
            auto_merge_threshold=0.95,
            merge_with_flag_threshold=0.85,
            review_threshold=0.70,
        )

    @pytest.fixture
    def sample_lsrs(self):
        """Create sample LSRs for testing."""
        lsr1 = LSR(
            form_orthographic="water",
            form_normalized="water",
            language_code="eng",
            definition_primary="a liquid",
            source_databases=["wiktionary"],
            date_start=1500,
            date_end=2024,
        )
        lsr2 = LSR(
            form_orthographic="wasser",
            form_normalized="wasser",
            language_code="deu",
            definition_primary="water in German",
            source_databases=["clld"],
            date_start=1500,
            date_end=2024,
        )
        return {lsr1.id: lsr1, lsr2.id: lsr2}

    def test_resolver_initialization(self, resolver):
        """Test resolver initialization."""
        assert resolver.auto_merge_threshold == 0.95
        assert resolver.merge_with_flag_threshold == 0.85
        assert resolver.review_threshold == 0.70

    def test_resolve_no_candidates(self, resolver):
        """Test resolving when no candidates exist."""
        entry = RawLexicalEntry(
            source_name="test",
            source_id="test-1",
            form="unique_word",
            language="English",
            language_code="eng",
        )

        result = resolver.resolve(entry)
        assert result.action == ResolutionAction.CREATE_NEW
        assert result.existing_id is None
        assert result.similarity_score == 0.0

    def test_resolve_exact_match(self, resolver, sample_lsrs):
        """Test resolving with an exact match."""
        resolver.set_lsr_store(sample_lsrs)

        entry = RawLexicalEntry(
            source_name="test",
            source_id="test-1",
            form="water",
            language="English",
            language_code="eng",
            definitions=["a liquid substance"],
        )

        result = resolver.resolve(entry)
        # Should find a match with high similarity
        assert result.action in [
            ResolutionAction.AUTO_MERGE,
            ResolutionAction.MERGE_WITH_FLAG,
            ResolutionAction.FLAG_FOR_REVIEW,
        ]
        assert result.similarity_score > 0

    def test_resolve_no_match_different_language(self, resolver, sample_lsrs):
        """Test that different language doesn't match."""
        resolver.set_lsr_store(sample_lsrs)

        entry = RawLexicalEntry(
            source_name="test",
            source_id="test-1",
            form="water",
            language="French",
            language_code="fra",  # Different language
        )

        result = resolver.resolve(entry)
        # No match in French
        assert result.action == ResolutionAction.CREATE_NEW

    def test_process_batch(self, resolver):
        """Test batch processing."""
        entries = [
            RawLexicalEntry(
                source_name="test",
                source_id=f"test-{i}",
                form=f"word{i}",
                language="English",
                language_code="eng",
            )
            for i in range(3)
        ]

        results = resolver.process_batch(entries)
        assert len(results) == 3
        assert all(isinstance(r, ResolutionResult) for r in results)

    def test_process_batch_with_error(self, resolver):
        """Test batch processing handles errors gracefully."""

        # Create an entry that might cause issues
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


class TestConvertEntryToLSR:
    """Tests for entry to LSR conversion."""

    def test_basic_conversion(self):
        """Test basic entry to LSR conversion."""
        entry = RawLexicalEntry(
            source_name="wiktionary",
            source_id="wikt-1",
            form="hello",
            language="English",
            language_code="eng",
            definitions=["a greeting", "an exclamation"],
            part_of_speech="interjection",
        )

        lsr = convert_entry_to_lsr(entry)

        assert lsr.form_orthographic == "hello"
        assert lsr.language_code == "eng"
        assert lsr.language_name == "English"
        assert lsr.definition_primary == "a greeting"
        assert lsr.definitions_alternate == ["an exclamation"]
        assert lsr.part_of_speech == "interjection"
        assert "wiktionary" in lsr.source_databases

    def test_conversion_with_date(self):
        """Test conversion with attestation date."""
        entry = RawLexicalEntry(
            source_name="corpus",
            source_id="corp-1",
            form="word",
            language="English",
            language_code="eng",
            date_attested=1500,
        )

        lsr = convert_entry_to_lsr(entry)

        assert lsr.date_start == 1500
        assert lsr.date_end == 1500

    def test_conversion_derives_language_code(self):
        """Test that language code is derived from language name if not provided."""
        entry = RawLexicalEntry(
            source_name="test",
            source_id="test-1",
            form="word",
            language="English",  # Full name
            language_code=None,  # No code provided
        )

        lsr = convert_entry_to_lsr(entry)
        # Should derive 3-letter code from language name
        assert len(lsr.language_code) == 3
