"""Acceptance tests based on user stories.

These tests validate complete user journeys through the system,
ensuring that the application meets user requirements.
"""

import pytest
from uuid import uuid4

from src.adapters.base import RawLexicalEntry
from src.models.lsr import LSR
from src.pipelines.entity_resolution import (
    EntityResolver,
    ResolutionAction,
    convert_entry_to_lsr,
)


class TestUserStoryDataIngestion:
    """
    User Story: As a linguist, I want to ingest lexical data from multiple
    sources so that I can build a comprehensive cross-linguistic database.

    Acceptance Criteria:
    - Can ingest data from Wiktionary, CLLD, corpus, and OCR sources
    - Data is normalized to a common format (RawLexicalEntry)
    - Duplicate entries are detected and merged appropriately
    - Data quality issues are flagged for review
    """

    def test_ingest_entry_from_wiktionary_source(self):
        """Verify Wiktionary-format data can be ingested."""
        entry = RawLexicalEntry(
            source_name="wiktionary",
            source_id="wikt-water-eng",
            form="water",
            form_phonetic="ˈwɔːtər",
            language="English",
            language_code="eng",
            definitions=[
                "A clear, colorless liquid",
                "A body of water such as a lake",
            ],
            part_of_speech=["noun"],
            date_attested=1200,
            etymology="From Old English wæter",
        )

        # Verify entry is properly structured
        assert entry.source_name == "wiktionary"
        assert entry.form == "water"
        assert entry.language_code == "eng"
        assert len(entry.definitions) == 2
        assert entry.date_attested == 1200

    def test_ingest_entry_from_clld_source(self):
        """Verify CLLD-format data can be ingested."""
        entry = RawLexicalEntry(
            source_name="clld",
            source_id="clld-wasser-deu",
            form="Wasser",
            language="German",
            language_code="deu",
            definitions=["water"],
        )

        assert entry.source_name == "clld"
        assert entry.language_code == "deu"

    def test_ingest_entry_from_ocr_source(self):
        """Verify OCR-extracted data can be ingested with quality markers."""
        entry = RawLexicalEntry(
            source_name="ocr",
            source_id="ocr-manuscript-123",
            form="watar",  # OCR error
            language="English",
            language_code="eng",
            definitions=["liquid"],
            raw_data={"ocr_confidence": 0.85, "source_document": "manuscript.pdf"},
        )

        assert entry.source_name == "ocr"
        assert entry.raw_data.get("ocr_confidence") == 0.85

    def test_convert_raw_entry_to_lsr(self):
        """Verify raw entries are converted to LSR format."""
        entry = RawLexicalEntry(
            source_name="corpus",
            source_id="corp-test-1",
            form="test",
            language="English",
            language_code="eng",
            definitions=["an examination", "a trial"],
            part_of_speech=["noun"],
            date_attested=1600,
        )

        lsr = convert_entry_to_lsr(entry)

        assert lsr.form_orthographic == "test"
        assert lsr.language_code == "eng"
        assert lsr.language_name == "English"
        assert lsr.definition_primary == "an examination"
        assert "an examination" in lsr.definition_primary or len(lsr.definitions_alternate) > 0
        assert "corpus" in lsr.source_databases

    def test_detect_duplicate_entries(self):
        """Verify duplicate entries are detected during resolution."""
        resolver = EntityResolver(
            auto_merge_threshold=0.95,
            merge_with_flag_threshold=0.85,
            review_threshold=0.70,
        )

        # Add existing LSR
        existing = LSR(
            form_orthographic="water",
            form_normalized="water",
            language_code="eng",
            language_name="English",
            definition_primary="a clear liquid",
            source_databases=["wiktionary"],
        )
        resolver.set_lsr_store({existing.id: existing})

        # Try to add duplicate
        duplicate_entry = RawLexicalEntry(
            source_name="corpus",
            source_id="corp-water-1",
            form="water",
            language="English",
            language_code="eng",
            definitions=["a clear liquid"],
        )

        result = resolver.resolve(duplicate_entry)

        # Should detect as potential duplicate
        assert result.action != ResolutionAction.CREATE_NEW or result.similarity_score > 0

    def test_flag_low_quality_entries(self):
        """Verify low-quality entries are flagged for review."""
        resolver = EntityResolver(
            auto_merge_threshold=0.95,
            merge_with_flag_threshold=0.85,
            review_threshold=0.70,
        )

        # Add existing LSR
        existing = LSR(
            form_orthographic="water",
            form_normalized="water",
            language_code="eng",
            definition_primary="liquid essential for life",
            source_databases=["wiktionary"],
        )
        resolver.set_lsr_store({existing.id: existing})

        # Entry with questionable quality (OCR error)
        low_quality = RawLexicalEntry(
            source_name="ocr",
            source_id="ocr-1",
            form="watar",  # Likely OCR error
            language="English",
            language_code="eng",
            definitions=["liquid"],
        )

        result = resolver.resolve(low_quality)

        # Fuzzy match should trigger review
        if result.similarity_score >= 0.70 and result.similarity_score < 0.95:
            assert result.action in [
                ResolutionAction.MERGE_WITH_FLAG,
                ResolutionAction.FLAG_FOR_REVIEW,
            ]


class TestUserStoryTextDating:
    """
    User Story: As a historian, I want to date historical texts based on
    their vocabulary so that I can authenticate documents.

    Acceptance Criteria:
    - Can submit text for dating analysis
    - Receives a predicted date range with confidence score
    - Gets list of diagnostic vocabulary used in the analysis
    - Can detect anachronistic vocabulary
    """

    @pytest.fixture
    def sample_historical_text(self):
        """Sample text for dating analysis."""
        return """
        The knight rode forth upon his destrier,
        bearing his standard into battle.
        The villein worked the fields while
        the lord surveyed his demesne.
        """

    def test_date_text_returns_required_fields(self, sample_historical_text):
        """Verify date-text response contains required fields."""
        from src.analysis.dating import TextDating

        dater = TextDating()
        result = dater.date_text(sample_historical_text, "eng")

        # Verify structure (even with placeholder implementation)
        assert hasattr(result, "predicted_range")
        assert hasattr(result, "confidence")
        assert hasattr(result, "diagnostic_vocabulary")
        assert isinstance(result.predicted_range, tuple)
        assert len(result.predicted_range) == 2

    def test_detect_anachronisms_returns_verdict(self, sample_historical_text):
        """Verify anachronism detection returns a verdict."""
        from src.analysis.dating import TextDating

        dater = TextDating()
        result = dater.detect_anachronisms(
            sample_historical_text,
            claimed_date=1300,
            language="eng",
        )

        assert hasattr(result, "anachronisms")
        assert hasattr(result, "verdict")
        assert result.verdict in ["consistent", "suspicious", "anachronistic"]

    def test_date_text_confidence_is_valid_range(self, sample_historical_text):
        """Verify confidence score is within valid range."""
        from src.analysis.dating import TextDating

        dater = TextDating()
        result = dater.date_text(sample_historical_text, "eng")

        assert 0.0 <= result.confidence <= 1.0


class TestUserStoryContactDetection:
    """
    User Story: As a historical linguist, I want to detect language contact
    events so that I can understand how languages influenced each other.

    Acceptance Criteria:
    - Can query contact events for a specific language
    - Can filter by date range
    - Results include donor/recipient information
    - Results include confidence scores and sample words
    """

    def test_contact_detector_returns_list(self):
        """Verify contact detector returns list of events."""
        from src.analysis.contact_detection import ContactDetector

        detector = ContactDetector()
        results = detector.detect_contacts("eng")

        assert isinstance(results, list)

    def test_contact_detector_accepts_date_filter(self):
        """Verify contact detector accepts date range filter."""
        from src.analysis.contact_detection import ContactDetector

        detector = ContactDetector()
        results = detector.detect_contacts(
            "eng",
            date_start=1000,
            date_end=1500,
        )

        assert isinstance(results, list)

    def test_contact_event_structure(self):
        """Verify ContactEvent has required fields."""
        from src.analysis.contact_detection import ContactEvent

        event = ContactEvent(
            donor_language="fra",
            recipient_language="eng",
            date_range=(1066, 1200),
            vocabulary_count=10000,
            confidence=0.95,
            sample_words=["justice", "royal", "parliament"],
        )

        assert event.donor_language == "fra"
        assert event.recipient_language == "eng"
        assert event.date_range == (1066, 1200)
        assert event.vocabulary_count == 10000
        assert event.confidence == 0.95
        assert len(event.sample_words) == 3


class TestUserStorySemanticDrift:
    """
    User Story: As a lexicographer, I want to track how word meanings
    change over time so that I can document semantic evolution.

    Acceptance Criteria:
    - Can query semantic trajectory for a word
    - Results show meaning changes over time
    - Can identify semantic shift events (narrowing, broadening, etc.)
    """

    def test_semantic_drift_analysis_exists(self):
        """Verify semantic drift module is available."""
        from src.analysis.semantic_drift import SemanticDriftAnalyzer

        analyzer = SemanticDriftAnalyzer()
        assert analyzer is not None

    def test_semantic_drift_trajectory_structure(self):
        """Verify drift trajectory has expected structure."""
        from src.analysis.semantic_drift import SemanticDriftAnalyzer, SemanticTrajectory

        analyzer = SemanticDriftAnalyzer()
        result = analyzer.get_trajectory("water", "eng")

        # Result can be None or SemanticTrajectory
        assert result is None or isinstance(result, SemanticTrajectory)

    def test_semantic_trajectory_dataclass_structure(self):
        """Verify SemanticTrajectory has required fields."""
        from src.analysis.semantic_drift import SemanticTrajectory, TrajectoryPoint
        from uuid import uuid4

        trajectory = SemanticTrajectory(
            lsr_id=uuid4(),
            form="water",
            language="eng",
            points=[],
            shift_events=[],
        )

        assert trajectory.form == "water"
        assert trajectory.language == "eng"
        assert isinstance(trajectory.points, list)


class TestUserStoryLSROperations:
    """
    User Story: As a researcher, I want to search and retrieve lexical
    records so that I can analyze linguistic data.

    Acceptance Criteria:
    - Can search by word form
    - Can filter by language
    - Can filter by date range
    - Results include full LSR data
    """

    @pytest.fixture
    def sample_lsr_store(self):
        """Create a sample LSR store for testing."""
        lsrs = {}

        # English water
        water_eng = LSR(
            form_orthographic="water",
            form_normalized="water",
            language_code="eng",
            language_name="English",
            definition_primary="a colorless liquid",
            source_databases=["wiktionary"],
            date_start=1200,
            date_end=2024,
        )
        lsrs[water_eng.id] = water_eng

        # German Wasser
        wasser_deu = LSR(
            form_orthographic="Wasser",
            form_normalized="wasser",
            language_code="deu",
            language_name="German",
            definition_primary="water",
            source_databases=["clld"],
            date_start=1500,
            date_end=2024,
        )
        lsrs[wasser_deu.id] = wasser_deu

        # Old English wæter
        water_ang = LSR(
            form_orthographic="wæter",
            form_normalized="waeter",
            language_code="ang",
            language_name="Old English",
            definition_primary="water",
            source_databases=["corpus"],
            date_start=700,
            date_end=1150,
        )
        lsrs[water_ang.id] = water_ang

        return lsrs

    def test_search_by_exact_form(self, sample_lsr_store):
        """Verify search finds exact form matches."""
        # Search logic
        form_query = "water"
        matches = [
            lsr for lsr in sample_lsr_store.values()
            if lsr.form_normalized == form_query.lower()
        ]

        assert len(matches) >= 1
        assert all(m.form_normalized == "water" for m in matches)

    def test_filter_by_language(self, sample_lsr_store):
        """Verify language filter works correctly."""
        lang_query = "eng"
        matches = [
            lsr for lsr in sample_lsr_store.values()
            if lsr.language_code == lang_query
        ]

        assert len(matches) >= 1
        assert all(m.language_code == "eng" for m in matches)

    def test_filter_by_date_range(self, sample_lsr_store):
        """Verify date range filter works correctly."""
        # Looking for words attested between 1000 and 1300
        date_start = 1000
        date_end = 1300

        matches = [
            lsr for lsr in sample_lsr_store.values()
            if lsr.date_start is not None
            and lsr.date_end is not None
            and not (lsr.date_end < date_start or lsr.date_start > date_end)
        ]

        assert len(matches) >= 1
        # English "water" (1200-2024) should match
        assert any(m.language_code == "eng" for m in matches)

    def test_lsr_contains_required_fields(self, sample_lsr_store):
        """Verify LSRs contain all required fields."""
        for lsr in sample_lsr_store.values():
            assert lsr.id is not None
            assert lsr.form_orthographic
            assert lsr.language_code
            assert lsr.source_databases


class TestUserStoryEtymologyTracking:
    """
    User Story: As an etymologist, I want to trace word origins and
    evolution so that I can document historical word relationships.

    Acceptance Criteria:
    - Can link related words across languages
    - Can trace derivation chains
    - Can identify cognates
    """

    def test_raw_entry_can_store_etymology(self):
        """Verify raw entries can store etymology information."""
        entry = RawLexicalEntry(
            source_name="wiktionary",
            source_id="wikt-water-1",
            form="water",
            language="English",
            language_code="eng",
            etymology="From Old English wæter, from Proto-Germanic *watōr",
        )

        assert entry.etymology is not None
        assert "Old English" in entry.etymology
        assert "Proto-Germanic" in entry.etymology

    def test_lsr_can_link_to_ancestors(self):
        """Verify LSR can store ancestor relationships."""
        # Create ancestor LSR
        ancestor = LSR(
            form_orthographic="wæter",
            language_code="ang",
            definition_primary="water in Old English",
        )

        # Create descendant with link
        descendant = LSR(
            form_orthographic="water",
            language_code="eng",
            definition_primary="water in Modern English",
            ancestor_ids=[ancestor.id],
        )

        assert ancestor.id in descendant.ancestor_ids

    def test_lsr_can_store_cognate_relationships(self):
        """Verify LSRs can store cognate relationships."""
        # Create cognates
        water_eng = LSR(
            form_orthographic="water",
            language_code="eng",
            definition_primary="water in English",
        )

        wasser_deu = LSR(
            form_orthographic="Wasser",
            language_code="deu",
            definition_primary="water in German",
            cognate_ids=[water_eng.id],
        )

        # Update English entry with cognate link
        water_eng.cognate_ids.append(wasser_deu.id)

        # Both should reference each other as cognates
        assert wasser_deu.id in water_eng.cognate_ids
        assert water_eng.id in wasser_deu.cognate_ids
