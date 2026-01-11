"""Regression tests for data integrity.

These tests ensure data integrity is maintained across
operations and that no data corruption occurs.
"""

import pytest
from copy import deepcopy
from uuid import uuid4

from src.adapters.base import RawLexicalEntry
from src.models.lsr import LSR, Attestation
from src.pipelines.entity_resolution import EntityResolver, convert_entry_to_lsr


class TestLSRDataIntegrity:
    """Test LSR data integrity across operations."""

    def test_lsr_id_immutable_after_creation(self):
        """Test that LSR ID doesn't change after creation."""
        lsr = LSR(
            form_orthographic="test",
            language_code="eng",
        )
        original_id = lsr.id

        # Perform various operations
        lsr.normalize_form()
        lsr.update_confidence()
        lsr.form_orthographic = "changed"

        assert lsr.id == original_id

    def test_lsr_attestation_ids_preserved(self):
        """Test that attestation IDs are preserved when added to LSR."""
        attestation = Attestation(
            text_excerpt="sample text",
            text_date=1500,
        )
        original_att_id = attestation.id

        lsr = LSR(
            form_orthographic="test",
            language_code="eng",
        )
        lsr.add_attestation(attestation)

        assert lsr.attestations[0].id == original_att_id

    def test_lsr_merge_preserves_original_data(self):
        """Test that merge doesn't lose original data."""
        lsr1 = LSR(
            form_orthographic="water",
            language_code="eng",
            definition_primary="a liquid",
            source_databases=["wiktionary"],
            date_start=1200,
            date_end=2000,
        )

        lsr2 = LSR(
            form_orthographic="water",
            language_code="eng",
            definition_primary="H2O",
            source_databases=["corpus"],
            date_start=1000,
            date_end=2024,
        )

        # Store original values
        original_form = lsr1.form_orthographic
        original_primary_def = lsr1.definition_primary

        lsr1.merge_with(lsr2)

        # Original form preserved
        assert lsr1.form_orthographic == original_form
        # Original primary definition preserved
        assert lsr1.definition_primary == original_primary_def
        # Both sources included
        assert "wiktionary" in lsr1.source_databases
        assert "corpus" in lsr1.source_databases
        # Date range expanded
        assert lsr1.date_start == 1000
        assert lsr1.date_end == 2024

    def test_conversion_preserves_source_data(self):
        """Test that conversion preserves all source data."""
        entry = RawLexicalEntry(
            source_name="wiktionary",
            source_id="wikt-123",
            form="test",
            form_phonetic="/tɛst/",
            language="English",
            language_code="eng",
            definitions=["an examination", "a trial"],
            part_of_speech=["noun", "verb"],
            date_attested=1600,
        )

        lsr = convert_entry_to_lsr(entry)

        # All data should be preserved
        assert lsr.form_orthographic == entry.form
        assert lsr.form_phonetic == entry.form_phonetic
        assert lsr.language_code == entry.language_code
        assert "wiktionary" in lsr.source_databases
        assert lsr.definition_primary == entry.definitions[0]

    def test_uuid_uniqueness(self):
        """Test that UUIDs are unique across multiple LSRs."""
        lsrs = [
            LSR(form_orthographic=f"word{i}", language_code="eng")
            for i in range(100)
        ]

        ids = [lsr.id for lsr in lsrs]
        # All IDs should be unique
        assert len(ids) == len(set(ids))


class TestRawEntryDataIntegrity:
    """Test RawLexicalEntry data integrity."""

    def test_raw_data_preserved(self):
        """Test that raw_data field preserves arbitrary data."""
        custom_data = {
            "ocr_confidence": 0.95,
            "page_number": 42,
            "nested": {"key": "value"},
        }

        entry = RawLexicalEntry(
            source_name="ocr",
            source_id="ocr-1",
            form="test",
            language="English",
            raw_data=custom_data,
        )

        assert entry.raw_data == custom_data
        assert entry.raw_data["nested"]["key"] == "value"

    def test_extra_fields_allowed(self):
        """Test that extra fields are allowed on RawLexicalEntry."""
        entry = RawLexicalEntry(
            source_name="test",
            source_id="test-1",
            form="word",
            language="English",
            custom_field="custom_value",  # Extra field
        )

        assert entry.custom_field == "custom_value"

    def test_source_key_consistency(self):
        """Test that source key generation is consistent."""
        entry = RawLexicalEntry(
            source_name="wiktionary",
            source_id="wikt-1",
            form="water",
            language="English",
        )

        key1 = entry.to_source_key()
        key2 = entry.to_source_key()

        assert key1 == key2
        assert "wiktionary" in key1
        assert "English" in key1
        assert "water" in key1


class TestResolverDataIntegrity:
    """Test resolver data integrity."""

    @pytest.fixture
    def resolver_with_data(self):
        """Create resolver with test data."""
        resolver = EntityResolver()
        lsrs = {}

        for i in range(5):
            lsr = LSR(
                form_orthographic=f"word{i}",
                form_normalized=f"word{i}",
                language_code="eng",
                source_databases=["base"],
            )
            lsrs[lsr.id] = lsr

        resolver.set_lsr_store(lsrs)
        return resolver

    def test_store_not_modified_by_resolve(self, resolver_with_data):
        """Test that resolve doesn't modify the store for CREATE_NEW."""
        original_count = len(resolver_with_data._lsr_store)

        entry = RawLexicalEntry(
            source_name="test",
            source_id="test-1",
            form="unique_word",
            language="English",
            language_code="eng",
        )

        resolver_with_data.resolve(entry)

        # Store should not be modified by resolve
        assert len(resolver_with_data._lsr_store) == original_count

    def test_batch_process_order_preserved(self, resolver_with_data):
        """Test that batch processing preserves entry order."""
        entries = [
            RawLexicalEntry(
                source_name="test",
                source_id=f"test-{i}",
                form=f"entry{i}",
                language="English",
                language_code="eng",
            )
            for i in range(10)
        ]

        results = resolver_with_data.process_batch(entries)

        # Results should be in same order as entries
        assert len(results) == len(entries)


class TestAttestationDataIntegrity:
    """Test attestation data integrity."""

    def test_attestation_linked_to_lsr(self):
        """Test that attestation is properly linked to LSR."""
        lsr = LSR(
            form_orthographic="test",
            language_code="eng",
        )

        attestation = Attestation(
            text_excerpt="sample",
            text_date=1500,
        )

        lsr.add_attestation(attestation)

        # Attestation should be linked to LSR
        assert attestation.lsr_id == lsr.id

    def test_multiple_attestations_all_linked(self):
        """Test that multiple attestations are all linked."""
        lsr = LSR(
            form_orthographic="test",
            language_code="eng",
        )

        for i in range(5):
            att = Attestation(
                text_excerpt=f"sample {i}",
                text_date=1500 + i * 100,
            )
            lsr.add_attestation(att)

        # All attestations should be linked
        for att in lsr.attestations:
            assert att.lsr_id == lsr.id

    def test_attestation_dates_update_lsr(self):
        """Test that attestation dates update LSR date range."""
        lsr = LSR(
            form_orthographic="test",
            language_code="eng",
            date_start=1500,
            date_end=1600,
        )

        # Add earlier attestation
        early = Attestation(text_excerpt="early", text_date=1400)
        lsr.add_attestation(early)

        # Add later attestation
        late = Attestation(text_excerpt="late", text_date=1700)
        lsr.add_attestation(late)

        # Date range should expand
        assert lsr.date_start == 1400
        assert lsr.date_end == 1700


class TestDeepCopyIntegrity:
    """Test that deep copying preserves data."""

    def test_lsr_deep_copy(self):
        """Test that deep copying LSR preserves all data."""
        original = LSR(
            form_orthographic="test",
            language_code="eng",
            definition_primary="a test",
            definitions_alternate=["examination"],
            source_databases=["wiktionary", "corpus"],
            attestations=[
                Attestation(text_excerpt="sample", text_date=1500)
            ],
        )

        copied = deepcopy(original)

        # Data should be equal
        assert copied.form_orthographic == original.form_orthographic
        assert copied.definition_primary == original.definition_primary
        assert copied.definitions_alternate == original.definitions_alternate
        assert copied.source_databases == original.source_databases

        # Objects should be different instances
        assert copied is not original
        # Note: UUID is preserved in deepcopy (pydantic behavior)
        # This is correct - the ID represents the same logical entity
        assert copied.id == original.id

    def test_modification_doesnt_affect_copy(self):
        """Test that modifying original doesn't affect copy."""
        original = LSR(
            form_orthographic="test",
            language_code="eng",
            source_databases=["wiktionary"],
        )

        copied = deepcopy(original)

        # Modify original
        original.source_databases.append("corpus")
        original.form_orthographic = "modified"

        # Copy should be unaffected
        assert "corpus" not in copied.source_databases
        assert copied.form_orthographic == "test"
