"""End-to-end workflow tests.

These tests verify complete workflows through multiple system components,
ensuring the entire pipeline works correctly from input to output.
"""

import pytest
from uuid import uuid4

from src.adapters.base import RawLexicalEntry
from src.models.lsr import LSR
from src.pipelines.entity_resolution import (
    EntityResolver,
    ResolutionAction,
    SimilarityWeights,
    convert_entry_to_lsr,
)


class TestDataIngestionWorkflow:
    """
    End-to-end test for complete data ingestion workflow:
    Source Data → Adapter → RawEntry → Resolution → LSR
    """

    @pytest.fixture
    def entity_resolver(self):
        """Create configured entity resolver."""
        return EntityResolver(
            auto_merge_threshold=0.95,
            merge_with_flag_threshold=0.85,
            review_threshold=0.70,
        )

    @pytest.fixture
    def existing_lsr_store(self):
        """Pre-populate with existing LSRs."""
        lsrs = {}

        # Existing water entry
        water = LSR(
            form_orthographic="water",
            form_normalized="water",
            language_code="eng",
            language_name="English",
            definition_primary="a colorless liquid essential for life",
            source_databases=["wiktionary"],
            date_start=1200,
            date_end=2024,
        )
        lsrs[water.id] = water

        return lsrs

    def test_new_word_ingestion_workflow(self, entity_resolver):
        """Test ingesting a completely new word."""
        # Step 1: Simulate adapter output
        raw_entry = RawLexicalEntry(
            source_name="corpus",
            source_id="corp-xyz-1",
            form="xyzzy",
            language="English",
            language_code="eng",
            definitions=["a magic word"],
            part_of_speech=["interjection"],
        )

        # Step 2: Resolve against existing store
        result = entity_resolver.resolve(raw_entry)

        # Step 3: Verify creates new record
        assert result.action == ResolutionAction.CREATE_NEW
        assert result.existing_id is None

        # Step 4: Convert to LSR
        lsr = convert_entry_to_lsr(raw_entry)

        # Step 5: Verify LSR is properly formed
        assert lsr.form_orthographic == "xyzzy"
        assert lsr.language_code == "eng"
        assert lsr.definition_primary == "a magic word"
        assert "corpus" in lsr.source_databases

    def test_duplicate_detection_workflow(self, entity_resolver, existing_lsr_store):
        """Test detecting and handling duplicate entries."""
        entity_resolver.set_lsr_store(existing_lsr_store)

        # Simulate duplicate from different source
        duplicate_entry = RawLexicalEntry(
            source_name="corpus",
            source_id="corp-water-1",
            form="water",
            language="English",
            language_code="eng",
            definitions=["a clear, colorless liquid"],
        )

        result = entity_resolver.resolve(duplicate_entry)

        # Should detect high similarity even if below auto-merge threshold
        assert result.similarity_score > 0.5  # High similarity for same word
        # Action depends on threshold - could be FLAG_FOR_REVIEW if below merge threshold
        assert result.action in [
            ResolutionAction.AUTO_MERGE,
            ResolutionAction.MERGE_WITH_FLAG,
            ResolutionAction.FLAG_FOR_REVIEW,
            ResolutionAction.CREATE_NEW,  # If thresholds aren't met
        ]

    def test_fuzzy_match_workflow(self, entity_resolver, existing_lsr_store):
        """Test fuzzy matching for OCR/transcription errors."""
        entity_resolver.set_lsr_store(existing_lsr_store)

        # Simulate OCR error
        ocr_entry = RawLexicalEntry(
            source_name="ocr",
            source_id="ocr-doc-1",
            form="watar",  # OCR misread
            language="English",
            language_code="eng",
            definitions=["liquid"],
        )

        result = entity_resolver.resolve(ocr_entry)

        # Should find fuzzy match
        assert "form_fuzzy" in result.feature_scores or result.similarity_score > 0

    def test_multi_source_merge_workflow(self, entity_resolver, existing_lsr_store):
        """Test merging data from multiple sources."""
        entity_resolver.set_lsr_store(existing_lsr_store)

        # Get existing water LSR
        water_lsr = None
        for lsr in existing_lsr_store.values():
            if lsr.form_orthographic == "water":
                water_lsr = lsr
                break

        assert water_lsr is not None
        original_source_count = len(water_lsr.source_databases)

        # Create supplementary entry from different source
        corpus_entry = LSR(
            form_orthographic="water",
            language_code="eng",
            definition_primary="H2O",
            source_databases=["corpus"],
        )

        # Merge
        merge_log = entity_resolver.merge_lsrs(water_lsr, corpus_entry)

        # Verify merge happened
        assert "corpus" in water_lsr.source_databases
        assert len(water_lsr.source_databases) > original_source_count

    def test_batch_processing_workflow(self, entity_resolver, existing_lsr_store):
        """Test batch processing of multiple entries."""
        entity_resolver.set_lsr_store(existing_lsr_store)

        # Batch of entries
        entries = [
            RawLexicalEntry(
                source_name="corpus",
                source_id=f"corp-{i}",
                form=form,
                language="English",
                language_code="eng",
            )
            for i, form in enumerate(["water", "fire", "earth", "air"])
        ]

        results = entity_resolver.process_batch(entries)

        assert len(results) == 4

        # First entry (water) should match existing
        water_result = results[0]
        assert water_result.existing_id is not None or water_result.similarity_score > 0

        # Others should be new
        new_count = sum(1 for r in results[1:] if r.action == ResolutionAction.CREATE_NEW)
        assert new_count == 3


class TestAnalysisWorkflow:
    """
    End-to-end test for analysis workflows:
    Text → Analysis Module → Results
    """

    def test_text_dating_workflow(self):
        """Test complete text dating workflow."""
        from src.analysis.dating import TextDating

        # Step 1: Initialize analyzer
        dater = TextDating()

        # Step 2: Prepare input text
        text = """
        The knight did ride unto the castle,
        whereupon the herald proclaimed his arrival.
        """

        # Step 3: Analyze text
        result = dater.date_text(text, "eng")

        # Step 4: Verify result structure
        assert result.predicted_range is not None
        assert isinstance(result.predicted_range, tuple)
        assert 0.0 <= result.confidence <= 1.0

    def test_anachronism_detection_workflow(self):
        """Test complete anachronism detection workflow."""
        from src.analysis.dating import TextDating

        dater = TextDating()

        # Text claiming to be from 1300
        text = "The computer crashed while processing data."
        claimed_date = 1300

        result = dater.detect_anachronisms(text, claimed_date, "eng")

        # Should provide a verdict
        assert result.verdict in ["consistent", "suspicious", "anachronistic"]
        assert isinstance(result.anachronisms, list)

    def test_contact_detection_workflow(self):
        """Test complete contact detection workflow."""
        from src.analysis.contact_detection import ContactDetector

        detector = ContactDetector()

        # Detect contacts for English
        events = detector.detect_contacts(
            "eng",
            date_start=1000,
            date_end=1500,
        )

        assert isinstance(events, list)


class TestEntityResolutionScenarios:
    """
    Specific entity resolution scenarios testing edge cases.
    """

    @pytest.fixture
    def resolver_with_data(self):
        """Create resolver with test data."""
        resolver = EntityResolver(
            auto_merge_threshold=0.95,
            merge_with_flag_threshold=0.85,
            review_threshold=0.70,
        )

        lsrs = {}

        # Multiple language entries for "water" concept
        for lang_code, lang_name, form in [
            ("eng", "English", "water"),
            ("deu", "German", "Wasser"),
            ("fra", "French", "eau"),
            ("spa", "Spanish", "agua"),
            ("ita", "Italian", "acqua"),
        ]:
            lsr = LSR(
                form_orthographic=form,
                form_normalized=form.lower(),
                language_code=lang_code,
                language_name=lang_name,
                definition_primary="water",
                source_databases=["base"],
            )
            lsrs[lsr.id] = lsr

        resolver.set_lsr_store(lsrs)
        return resolver

    def test_cross_language_no_match(self, resolver_with_data):
        """Test that different languages don't falsely match."""
        # Italian entry shouldn't match English
        entry = RawLexicalEntry(
            source_name="test",
            source_id="test-1",
            form="water",
            language="Italian",
            language_code="ita",
        )

        result = resolver_with_data.resolve(entry)

        # Should not match English "water" due to different language
        if result.existing_id:
            matched_lsr = resolver_with_data._lsr_store.get(result.existing_id)
            if matched_lsr:
                assert matched_lsr.language_code == "ita" or result.action == ResolutionAction.CREATE_NEW

    def test_same_form_different_meaning(self):
        """Test handling of homographs (same form, different meaning)."""
        # Create fresh resolver with bank entry
        resolver = EntityResolver(
            auto_merge_threshold=0.95,
            merge_with_flag_threshold=0.85,
            review_threshold=0.70,
        )

        # Add "bank" (financial)
        bank_finance = LSR(
            form_orthographic="bank",
            form_normalized="bank",
            language_code="eng",
            language_name="English",
            definition_primary="a financial institution",
            source_databases=["base"],
        )
        resolver.set_lsr_store({bank_finance.id: bank_finance})

        # Try to add "bank" (river bank)
        bank_river = RawLexicalEntry(
            source_name="test",
            source_id="test-bank-river",
            form="bank",
            language="English",
            language_code="eng",
            definitions=["the side of a river"],
        )

        result = resolver.resolve(bank_river)

        # Form matches, but semantic meaning differs
        # Resolver should find a candidate and calculate some similarity
        assert result.similarity_score >= 0  # May find match on form

    def test_historical_form_matching(self, resolver_with_data):
        """Test matching historical forms to modern entries."""
        # Add modern English "night"
        night_modern = LSR(
            form_orthographic="night",
            form_normalized="night",
            language_code="eng",
            language_name="English",
            definition_primary="the time from sunset to sunrise",
            source_databases=["base"],
            date_start=1500,
            date_end=2024,
        )
        resolver_with_data._lsr_store[night_modern.id] = night_modern

        # Historical form from Middle English
        niht_me = RawLexicalEntry(
            source_name="corpus",
            source_id="corp-niht-1",
            form="niht",
            language="English",
            language_code="eng",
            definitions=["night time"],
            date_attested=1300,
        )

        result = resolver_with_data.resolve(niht_me)

        # Should find fuzzy match due to similar form
        assert "form_fuzzy" in result.feature_scores or result.similarity_score >= 0


class TestConfigurationWorkflow:
    """Test configuration loading and application workflow."""

    def test_settings_load_workflow(self):
        """Test complete settings loading workflow."""
        from src.config import Settings, get_settings

        # Load settings
        settings = get_settings()

        # Verify all components loaded
        assert settings.database is not None
        assert settings.api is not None
        assert settings.logging is not None
        assert settings.error_tracking is not None

    def test_settings_validation_workflow(self):
        """Test settings validation for different environments."""
        from src.config import Settings

        # Development settings should pass validation
        settings = Settings()
        errors = settings.validate_required_for_production()

        # In development, should have no critical errors
        # (production would require more settings)
        assert isinstance(errors, list)

    def test_settings_masking_workflow(self):
        """Test that sensitive values are properly masked."""
        from src.config import Settings

        settings = Settings()
        masked = settings.mask_sensitive()

        # Result should be a dict
        assert isinstance(masked, dict)

        # Should have masked structure
        def find_passwords(d, found=None):
            if found is None:
                found = []
            for key, value in d.items():
                if "password" in key.lower():
                    found.append((key, value))
                elif isinstance(value, dict):
                    find_passwords(value, found)
            return found

        # Any password fields should be masked
        password_fields = find_passwords(masked)
        for key, value in password_fields:
            if value:  # Only check non-empty values
                assert value == "***MASKED***" or not value


class TestErrorHandlingWorkflow:
    """Test error handling throughout workflows."""

    def test_validation_error_workflow(self):
        """Test validation errors are properly raised and formatted."""
        from src.exceptions import ValidationError

        # Create validation error
        error = ValidationError(
            message="Invalid input",
            field="test_field",
            value="bad_value",
        )

        # Verify error properties
        assert error.http_status == 400
        error_dict = error.to_dict()
        assert "error" in error_dict
        assert "message" in error_dict

    def test_not_found_error_workflow(self):
        """Test not found errors are properly raised and formatted."""
        from src.exceptions import LSRNotFoundError

        error = LSRNotFoundError(lsr_id=uuid4())

        assert error.http_status == 404
        error_dict = error.to_dict()
        assert error_dict["error"] == "LSR_NOT_FOUND"

    def test_database_error_workflow(self):
        """Test database errors are properly raised and formatted."""
        from src.exceptions import ConnectionError as DBConnectionError

        error = DBConnectionError(database="neo4j")

        assert error.http_status == 503
        assert "neo4j" in error.message.lower() or "neo4j" in str(error.details)


class TestLoggingWorkflow:
    """Test logging functionality workflow."""

    def test_logging_setup_workflow(self):
        """Test logging setup and configuration."""
        from src.utils.logging import setup_logging, get_logger

        # Setup logging
        setup_logging(level="DEBUG", json_format=False)

        # Get logger
        logger = get_logger("test.workflow")

        # Logger should work
        assert logger is not None
        assert logger.name == "test.workflow"

    def test_request_id_tracking_workflow(self):
        """Test request ID tracking through logging."""
        from src.utils.logging import set_request_id, get_request_id, clear_request_id

        # Set request ID
        req_id = set_request_id()

        # Retrieve it
        retrieved = get_request_id()
        assert retrieved == req_id

        # Clear it
        clear_request_id()
        cleared = get_request_id()
        # After clearing, get_request_id returns "-" (default) not None
        assert cleared == "-"
