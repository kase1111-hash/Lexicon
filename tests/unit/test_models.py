"""Unit tests for data models."""

import pytest
from uuid import UUID

from src.models.lsr import LSR, Attestation, DateSource, Register
from src.models.language import Language, ContactEvent
from src.models.relationships import Edge, RelationshipType


class TestLSR:
    """Tests for LSR model."""

    def test_lsr_creation(self):
        """Test basic LSR creation."""
        lsr = LSR(
            form_orthographic="test",
            language_code="eng",
            definition_primary="a test word",
        )
        assert lsr.form_orthographic == "test"
        assert lsr.language_code == "eng"
        assert isinstance(lsr.id, UUID)

    def test_lsr_normalize_form(self):
        """Test form normalization."""
        lsr = LSR(form_orthographic="Test")
        lsr.normalize_form()
        assert lsr.form_normalized == "test"

    def test_lsr_default_values(self):
        """Test LSR default values."""
        lsr = LSR()
        assert lsr.version == 1
        assert lsr.reconstruction_flag is False
        assert lsr.confidence_overall == 1.0
        assert lsr.attestations == []


class TestAttestation:
    """Tests for Attestation model."""

    def test_attestation_creation(self):
        """Test basic attestation creation."""
        att = Attestation(
            text_excerpt="Example usage",
            text_source="Test Corpus",
            text_date=1500,
        )
        assert att.text_excerpt == "Example usage"
        assert att.text_date == 1500


class TestLanguage:
    """Tests for Language model."""

    def test_language_creation(self):
        """Test basic language creation."""
        lang = Language(
            iso_code="eng",
            name="English",
            family="Indo-European",
        )
        assert lang.iso_code == "eng"
        assert lang.is_living is True


class TestEdge:
    """Tests for Edge model."""

    def test_edge_creation(self):
        """Test basic edge creation."""
        edge = Edge(
            relationship_type=RelationshipType.DESCENDS_FROM,
            confidence=0.95,
        )
        assert edge.relationship_type == RelationshipType.DESCENDS_FROM
        assert edge.confidence == 0.95
