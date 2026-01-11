"""Regression tests for LSR model edge cases.

These tests ensure edge cases and boundary conditions
don't cause unexpected behavior in the LSR model.
"""

import pytest
from datetime import datetime
from uuid import uuid4

from src.models.lsr import LSR, Attestation, DateSource, Register


class TestLSRBoundaryConditions:
    """Test LSR model with boundary values."""

    def test_empty_form_orthographic(self):
        """Test LSR with empty orthographic form."""
        lsr = LSR(
            form_orthographic="",
            language_code="eng",
        )
        assert lsr.form_orthographic == ""
        assert lsr.form_normalized == ""

    def test_very_long_form(self):
        """Test LSR with very long word form."""
        long_form = "a" * 1000
        lsr = LSR(
            form_orthographic=long_form,
            language_code="eng",
        )
        assert len(lsr.form_orthographic) == 1000
        assert len(lsr.form_normalized) == 1000

    def test_unicode_form(self):
        """Test LSR with Unicode characters."""
        lsr = LSR(
            form_orthographic="水",  # Chinese for water
            language_code="zho",
        )
        assert lsr.form_orthographic == "水"

    def test_diacritics_in_form(self):
        """Test LSR with diacritical marks."""
        lsr = LSR(
            form_orthographic="café",
            language_code="fra",
        )
        assert lsr.form_orthographic == "café"
        # Normalized form should strip diacritics
        assert "e" in lsr.form_normalized or "é" in lsr.form_normalized

    def test_whitespace_in_form(self):
        """Test LSR with whitespace in form."""
        lsr = LSR(
            form_orthographic="ice cream",
            language_code="eng",
        )
        assert lsr.form_orthographic == "ice cream"

    def test_special_characters_in_form(self):
        """Test LSR with special characters."""
        lsr = LSR(
            form_orthographic="don't",
            language_code="eng",
        )
        assert "'" in lsr.form_orthographic

    def test_negative_date_bce(self):
        """Test LSR with BCE dates (negative years)."""
        lsr = LSR(
            form_orthographic="aqua",
            language_code="lat",
            date_start=-500,  # 500 BCE
            date_end=-100,    # 100 BCE
        )
        assert lsr.date_start == -500
        assert lsr.date_end == -100

    def test_same_start_end_date(self):
        """Test LSR where start and end date are the same."""
        lsr = LSR(
            form_orthographic="test",
            language_code="eng",
            date_start=2000,
            date_end=2000,
        )
        assert lsr.date_start == lsr.date_end

    def test_null_dates(self):
        """Test LSR with null dates."""
        lsr = LSR(
            form_orthographic="test",
            language_code="eng",
            date_start=None,
            date_end=None,
        )
        assert lsr.date_start is None
        assert lsr.date_end is None

    def test_date_end_before_start_raises_error(self):
        """Test that date_end before date_start raises validation error."""
        with pytest.raises(ValueError):
            LSR(
                form_orthographic="test",
                language_code="eng",
                date_start=2000,
                date_end=1900,
            )


class TestLSRSpecialValues:
    """Test LSR with special and edge case values."""

    def test_empty_lists(self):
        """Test LSR with empty lists."""
        lsr = LSR(
            form_orthographic="test",
            language_code="eng",
            definitions_alternate=[],
            semantic_fields=[],
            part_of_speech=[],
            attestations=[],
            source_databases=[],
        )
        assert lsr.definitions_alternate == []
        assert lsr.semantic_fields == []

    def test_confidence_boundaries(self):
        """Test confidence values at boundaries."""
        # Minimum confidence
        lsr_min = LSR(
            form_orthographic="test",
            language_code="eng",
            confidence_overall=0.0,
        )
        assert lsr_min.confidence_overall == 0.0

        # Maximum confidence
        lsr_max = LSR(
            form_orthographic="test",
            language_code="eng",
            confidence_overall=1.0,
        )
        assert lsr_max.confidence_overall == 1.0

    def test_confidence_out_of_bounds_raises_error(self):
        """Test that confidence outside 0-1 raises error."""
        with pytest.raises(ValueError):
            LSR(
                form_orthographic="test",
                language_code="eng",
                confidence_overall=1.5,
            )

        with pytest.raises(ValueError):
            LSR(
                form_orthographic="test",
                language_code="eng",
                confidence_overall=-0.1,
            )

    def test_frequency_score_boundaries(self):
        """Test frequency score at boundaries."""
        lsr_zero = LSR(
            form_orthographic="test",
            language_code="eng",
            frequency_score=0.0,
        )
        assert lsr_zero.frequency_score == 0.0

        lsr_one = LSR(
            form_orthographic="test",
            language_code="eng",
            frequency_score=1.0,
        )
        assert lsr_one.frequency_score == 1.0

    def test_all_date_sources(self):
        """Test all DateSource enum values."""
        for source in DateSource:
            lsr = LSR(
                form_orthographic="test",
                language_code="eng",
                date_source=source,
            )
            assert lsr.date_source == source

    def test_all_register_values(self):
        """Test all Register enum values."""
        for reg in Register:
            lsr = LSR(
                form_orthographic="test",
                language_code="eng",
                register=reg,
            )
            assert lsr.register == reg

    def test_reconstruction_flag(self):
        """Test reconstruction flag for proto-forms."""
        lsr = LSR(
            form_orthographic="*wódr̥",  # Proto-Indo-European water
            language_code="ine",
            reconstruction_flag=True,
        )
        assert lsr.reconstruction_flag is True


class TestLSRMergeEdgeCases:
    """Test LSR merge operations edge cases."""

    def test_merge_with_empty_attestations(self):
        """Test merging when both have empty attestations."""
        lsr1 = LSR(
            form_orthographic="test",
            language_code="eng",
            attestations=[],
        )
        lsr2 = LSR(
            form_orthographic="test",
            language_code="eng",
            attestations=[],
        )

        original_version = lsr1.version
        lsr1.merge_with(lsr2)
        assert lsr1.version == original_version + 1
        assert lsr1.attestations == []

    def test_merge_with_duplicate_attestations(self):
        """Test that duplicate attestations are not added twice."""
        att_id = uuid4()
        attestation = Attestation(
            id=att_id,
            text_excerpt="sample text",
            text_date=1500,
        )

        lsr1 = LSR(
            form_orthographic="test",
            language_code="eng",
            attestations=[attestation],
        )
        lsr2 = LSR(
            form_orthographic="test",
            language_code="eng",
            attestations=[attestation],  # Same attestation
        )

        lsr1.merge_with(lsr2)
        # Should not duplicate
        assert len(lsr1.attestations) == 1

    def test_merge_expands_date_range(self):
        """Test that merge expands date range appropriately."""
        lsr1 = LSR(
            form_orthographic="test",
            language_code="eng",
            date_start=1500,
            date_end=1700,
        )
        lsr2 = LSR(
            form_orthographic="test",
            language_code="eng",
            date_start=1400,  # Earlier
            date_end=1800,    # Later
        )

        lsr1.merge_with(lsr2)
        assert lsr1.date_start == 1400
        assert lsr1.date_end == 1800

    def test_merge_combines_source_databases(self):
        """Test that source databases are combined without duplicates."""
        lsr1 = LSR(
            form_orthographic="test",
            language_code="eng",
            source_databases=["wiktionary", "corpus"],
        )
        lsr2 = LSR(
            form_orthographic="test",
            language_code="eng",
            source_databases=["corpus", "clld"],  # corpus is duplicate
        )

        lsr1.merge_with(lsr2)
        assert "wiktionary" in lsr1.source_databases
        assert "corpus" in lsr1.source_databases
        assert "clld" in lsr1.source_databases
        # No duplicates
        assert lsr1.source_databases.count("corpus") == 1


class TestAttestationEdgeCases:
    """Test Attestation model edge cases."""

    def test_empty_attestation(self):
        """Test creating attestation with minimal data."""
        att = Attestation()
        assert att.id is not None
        assert att.text_excerpt == ""

    def test_attestation_date_confidence_boundaries(self):
        """Test date confidence at boundaries."""
        att_min = Attestation(text_date_confidence=0.0)
        assert att_min.text_date_confidence == 0.0

        att_max = Attestation(text_date_confidence=1.0)
        assert att_max.text_date_confidence == 1.0

    def test_add_attestation_updates_dates(self):
        """Test that adding attestation updates LSR dates."""
        lsr = LSR(
            form_orthographic="test",
            language_code="eng",
            date_start=None,
            date_end=None,
        )

        attestation = Attestation(
            text_excerpt="first attestation",
            text_date=1400,
        )
        lsr.add_attestation(attestation)

        assert lsr.date_start == 1400
        assert lsr.date_end == 1400


class TestLSRConversion:
    """Test LSR conversion methods."""

    def test_to_graph_node_with_minimal_data(self):
        """Test conversion to graph node with minimal data."""
        lsr = LSR(
            form_orthographic="test",
            language_code="eng",
        )

        node = lsr.to_graph_node()
        assert "id" in node
        assert node["form"] == "test"
        assert node["language_code"] == "eng"

    def test_to_search_document_with_full_data(self):
        """Test conversion to search document with full data."""
        lsr = LSR(
            form_orthographic="water",
            form_phonetic="ˈwɔːtər",
            language_code="eng",
            language_name="English",
            definition_primary="a liquid",
            definitions_alternate=["H2O"],
            part_of_speech=["noun"],
            date_start=1200,
            date_end=2024,
        )

        doc = lsr.to_search_document()
        assert doc["form_orthographic"] == "water"
        assert doc["form_phonetic"] == "ˈwɔːtər"
        assert doc["language_name"] == "English"
        assert "H2O" in doc["definitions_alternate"]
