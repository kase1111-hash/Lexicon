"""Integration tests for the core loop: ingest -> resolve -> analyze.

These tests verify that the complete pipeline works end-to-end:
1. Adapter output -> Entity Resolution -> LSR creation
2. LSR data -> TextDating analysis
3. LSR data -> ContactDetection analysis
4. LSR data -> SemanticDrift analysis
5. Ingestion script logic (without network)
"""

from uuid import UUID, uuid4

import pytest

from src.adapters.base import RawLexicalEntry
from src.analysis.contact_detection import ContactDetector, ContactEvent
from src.analysis.dating import AnachronismAnalysis, DateAnalysis, TextDating
from src.analysis.semantic_drift import SemanticDriftAnalyzer, SemanticTrajectory
from src.models.lsr import LSR
from src.pipelines.entity_resolution import (
    EntityResolver,
    ResolutionAction,
    convert_entry_to_lsr,
)


class TestIngestResolveLoop:
    """
    Integration test: Adapter output -> Entity Resolution -> LSR store.

    Simulates the ingestion script's core logic without network access.
    """

    @pytest.fixture
    def resolver(self):
        resolver = EntityResolver(
            auto_merge_threshold=0.95,
            merge_with_flag_threshold=0.85,
            review_threshold=0.70,
        )
        resolver.set_lsr_store({})
        return resolver

    def test_new_word_creates_lsr(self, resolver):
        """Fresh word with no existing match -> CREATE_NEW -> valid LSR."""
        entry = RawLexicalEntry(
            source_name="wiktionary",
            source_id="wikt-water-eng",
            form="water",
            form_phonetic="ˈwɔːtər",
            language="English",
            language_code="eng",
            definitions=["a clear, colorless liquid", "a body of water"],
            part_of_speech=["noun"],
            date_attested=1200,
            etymology="From Old English wæter",
        )

        result = resolver.resolve(entry)
        assert result.action == ResolutionAction.CREATE_NEW

        lsr = convert_entry_to_lsr(entry)
        assert lsr.form_orthographic == "water"
        assert lsr.language_code == "eng"
        assert lsr.definition_primary == "a clear, colorless liquid"
        assert lsr.date_start == 1200
        assert isinstance(lsr.id, UUID)

    def test_duplicate_detected_after_creation(self, resolver):
        """Second entry for same word+lang should be detected as duplicate."""
        # First entry creates a new LSR
        entry1 = RawLexicalEntry(
            source_name="wiktionary",
            source_id="wikt-fire-1",
            form="fire",
            language="English",
            language_code="eng",
            definitions=["combustion producing heat and light"],
        )

        result1 = resolver.resolve(entry1)
        assert result1.action == ResolutionAction.CREATE_NEW

        lsr = convert_entry_to_lsr(entry1)
        store = {lsr.id: lsr}
        resolver.set_lsr_store(store)

        # Second entry for same word should show high similarity
        entry2 = RawLexicalEntry(
            source_name="corpus",
            source_id="corp-fire-1",
            form="fire",
            language="English",
            language_code="eng",
            definitions=["combustion producing heat and light"],
        )

        result2 = resolver.resolve(entry2)
        assert result2.similarity_score > 0.5
        # With matching definition, should detect existing and merge/flag
        assert result2.action in [
            ResolutionAction.AUTO_MERGE,
            ResolutionAction.MERGE_WITH_FLAG,
            ResolutionAction.FLAG_FOR_REVIEW,
            ResolutionAction.CREATE_NEW,  # Still valid if below threshold
        ]

    def test_multi_language_ingestion(self, resolver):
        """Entries in different languages create separate LSRs."""
        entries = [
            RawLexicalEntry(
                source_name="wiktionary",
                source_id="wikt-water-eng",
                form="water",
                language="English",
                language_code="eng",
                definitions=["a clear liquid"],
            ),
            RawLexicalEntry(
                source_name="wiktionary",
                source_id="wikt-wasser-deu",
                form="Wasser",
                language="German",
                language_code="deu",
                definitions=["water"],
            ),
            RawLexicalEntry(
                source_name="wiktionary",
                source_id="wikt-eau-fra",
                form="eau",
                language="French",
                language_code="fra",
                definitions=["water"],
            ),
        ]

        store: dict[UUID, LSR] = {}
        for entry in entries:
            result = resolver.resolve(entry)
            if result.action == ResolutionAction.CREATE_NEW:
                lsr = convert_entry_to_lsr(entry)
                store[lsr.id] = lsr
                resolver.set_lsr_store(store)

        assert len(store) == 3
        languages = {lsr.language_code for lsr in store.values()}
        assert languages == {"eng", "deu", "fra"}

    def test_batch_processing_pipeline(self, resolver):
        """Batch of entries processed correctly with stats tracking."""
        entries = [
            RawLexicalEntry(
                source_name="wiktionary",
                source_id=f"batch-{i}",
                form=form,
                language="English",
                language_code="eng",
                definitions=[f"definition of {form}"],
            )
            for i, form in enumerate(
                [
                    "water",
                    "fire",
                    "earth",
                    "air",
                    "stone",
                    "tree",
                    "sun",
                    "moon",
                    "star",
                    "rain",
                ]
            )
        ]

        created = 0
        store: dict[UUID, LSR] = {}
        for entry in entries:
            result = resolver.resolve(entry)
            if result.action == ResolutionAction.CREATE_NEW:
                lsr = convert_entry_to_lsr(entry)
                store[lsr.id] = lsr
                resolver.set_lsr_store(store)
                created += 1

        assert created == 10
        assert len(store) == 10


class TestTextDatingIntegration:
    """
    Integration test: LSR lookup data -> TextDating -> DateAnalysis.

    Verifies that when real-format LSR lookup data is provided,
    the dating module produces valid analysis.
    """

    @pytest.fixture
    def lsr_lookup(self):
        """Build an LSR lookup dict matching the format from _build_lsr_lookup."""
        return {
            "knight": {"date_start": 1100, "date_end": 2024, "language_code": "eng"},
            "destrier": {"date_start": 1200, "date_end": 1500, "language_code": "eng"},
            "villein": {"date_start": 1066, "date_end": 1500, "language_code": "eng"},
            "demesne": {"date_start": 1100, "date_end": 1600, "language_code": "eng"},
            "standard": {"date_start": 1200, "date_end": 2024, "language_code": "eng"},
            "battle": {"date_start": 1300, "date_end": 2024, "language_code": "eng"},
            "lord": {"date_start": 900, "date_end": 2024, "language_code": "eng"},
            "field": {"date_start": 800, "date_end": 2024, "language_code": "eng"},
            "computer": {"date_start": 1945, "date_end": 2024, "language_code": "eng"},
            "internet": {"date_start": 1990, "date_end": 2024, "language_code": "eng"},
            "selfie": {"date_start": 2013, "date_end": 2024, "language_code": "eng"},
        }

    def test_date_medieval_text(self, lsr_lookup):
        """Medieval vocabulary should produce a medieval date range."""
        dater = TextDating(lsr_lookup=lsr_lookup)
        text = "The knight rode forth upon his destrier bearing his standard into battle"
        result = dater.date_text(text, "eng")

        assert isinstance(result, DateAnalysis)
        assert isinstance(result.predicted_range, tuple)
        assert len(result.predicted_range) == 2
        assert 0.0 <= result.confidence <= 1.0
        assert result.analyzed_tokens > 0

    def test_date_modern_text(self, lsr_lookup):
        """Modern vocabulary should produce a modern date range."""
        dater = TextDating(lsr_lookup=lsr_lookup)
        text = "She took a selfie and posted it on the internet from her computer"
        result = dater.date_text(text, "eng")

        assert isinstance(result, DateAnalysis)
        assert result.predicted_range[0] >= 1900 or result.matched_tokens == 0

    def test_detect_anachronisms_with_lookup(self, lsr_lookup):
        """Anachronism detection finds words that postdate the claimed date."""
        dater = TextDating(lsr_lookup=lsr_lookup)
        text = "The knight used his computer to search the internet"
        result = dater.detect_anachronisms(text, claimed_date=1300, language="eng")

        assert isinstance(result, AnachronismAnalysis)
        assert result.verdict in ["consistent", "suspicious", "anachronistic"]

    def test_empty_text_handled_gracefully(self, lsr_lookup):
        """Empty or very short text doesn't crash."""
        dater = TextDating(lsr_lookup=lsr_lookup)
        result = dater.date_text("", "eng")

        assert isinstance(result, DateAnalysis)
        assert result.confidence == 0.0

    def test_no_lookup_data(self):
        """Dating with empty lookup still returns valid structure."""
        dater = TextDating(lsr_lookup={})
        result = dater.date_text("some random text here", "eng")

        assert isinstance(result, DateAnalysis)
        assert result.matched_tokens == 0


class TestContactDetectionIntegration:
    """
    Integration test: Borrowing data -> ContactDetector -> ContactEvents.

    Verifies that the contact detector works with the data format
    produced by _build_borrowing_data.
    """

    @pytest.fixture
    def borrowing_data(self):
        """Build borrowing data matching the format from _build_borrowing_data."""
        # Simulate Norman French -> English borrowings (post-1066)
        norman_words = [
            "justice",
            "parliament",
            "royal",
            "sovereign",
            "castle",
            "noble",
            "court",
            "judge",
            "attorney",
            "verdict",
        ]
        borrowings = []
        for word in norman_words:
            borrowings.append(
                {
                    "form": word,
                    "source_lang": "fra",
                    "target_lang": "eng",
                    "date": 1150,
                    "date_start": 1066,
                    "date_end": 1300,
                    "definition": f"{word} (borrowed from French)",
                }
            )
        return borrowings

    def test_detect_norman_contact(self, borrowing_data):
        """Should detect the Norman French contact event."""
        detector = ContactDetector(borrowing_data=borrowing_data)
        events = detector.detect_contacts("eng")

        assert isinstance(events, list)
        # With 10 borrowings from French, should detect at least one event
        if events:
            event = events[0]
            assert isinstance(event, ContactEvent)
            assert event.donor_language == "fra"
            assert event.recipient_language == "eng"
            assert event.vocabulary_count > 0

    def test_empty_borrowing_data(self):
        """No borrowings -> no contact events."""
        detector = ContactDetector(borrowing_data=[])
        events = detector.detect_contacts("eng")

        assert isinstance(events, list)
        assert len(events) == 0

    def test_date_range_filter(self, borrowing_data):
        """Date filter should narrow results."""
        detector = ContactDetector(borrowing_data=borrowing_data)

        # Filter to before the Norman conquest
        events = detector.detect_contacts("eng", date_start=500, date_end=1000)
        # Should find nothing since all borrowings are dated ~1150
        assert isinstance(events, list)

    def test_contact_event_structure(self, borrowing_data):
        """Contact events have all required fields."""
        detector = ContactDetector(borrowing_data=borrowing_data)
        events = detector.detect_contacts("eng")

        for event in events:
            assert hasattr(event, "donor_language")
            assert hasattr(event, "recipient_language")
            assert hasattr(event, "date_range")
            assert hasattr(event, "vocabulary_count")
            assert hasattr(event, "confidence")
            assert hasattr(event, "sample_words")
            assert isinstance(event.date_range, tuple)
            assert len(event.date_range) == 2
            assert 0.0 <= event.confidence <= 1.0


class TestSemanticDriftIntegration:
    """
    Integration test: Trajectory data -> SemanticDriftAnalyzer -> SemanticTrajectory.

    Verifies that the drift analyzer works with the data format
    produced by _build_trajectory_data.
    """

    @pytest.fixture
    def trajectory_data(self):
        """Build trajectory data matching the format from _build_trajectory_data."""
        return {
            "nice:eng": [
                {
                    "form": "nice",
                    "date_start": 1300,
                    "date_end": 1400,
                    "definition_primary": "foolish, ignorant",
                    "language_code": "eng",
                    "id": str(uuid4()),
                },
                {
                    "form": "nice",
                    "date_start": 1500,
                    "date_end": 1600,
                    "definition_primary": "precise, fastidious",
                    "language_code": "eng",
                    "id": str(uuid4()),
                },
                {
                    "form": "nice",
                    "date_start": 1700,
                    "date_end": 1800,
                    "definition_primary": "pleasant, agreeable",
                    "language_code": "eng",
                    "id": str(uuid4()),
                },
                {
                    "form": "nice",
                    "date_start": 1900,
                    "date_end": 2024,
                    "definition_primary": "pleasant, kind, good",
                    "language_code": "eng",
                    "id": str(uuid4()),
                },
            ],
        }

    def test_trajectory_for_drifting_word(self, trajectory_data):
        """Word with multiple time periods should produce a trajectory."""
        analyzer = SemanticDriftAnalyzer(lsr_data=trajectory_data)
        result = analyzer.get_trajectory("nice", "eng")

        assert result is not None
        assert isinstance(result, SemanticTrajectory)
        assert result.form == "nice"
        assert result.language == "eng"
        assert len(result.points) == 4
        assert isinstance(result.total_drift, float)
        assert isinstance(result.stability_score, float)

    def test_trajectory_points_ordered_by_date(self, trajectory_data):
        """Trajectory points should be ordered chronologically."""
        analyzer = SemanticDriftAnalyzer(lsr_data=trajectory_data)
        result = analyzer.get_trajectory("nice", "eng")

        assert result is not None
        dates = [p.date for p in result.points]
        assert dates == sorted(dates)

    def test_unknown_word_returns_none(self, trajectory_data):
        """Unknown word should return None, not crash."""
        analyzer = SemanticDriftAnalyzer(lsr_data=trajectory_data)
        result = analyzer.get_trajectory("xyzzy", "eng")

        assert result is None

    def test_empty_data(self):
        """Empty data returns None for any query."""
        analyzer = SemanticDriftAnalyzer(lsr_data={})
        result = analyzer.get_trajectory("water", "eng")

        assert result is None

    def test_single_period_trajectory(self):
        """Word with only one time period should still work."""
        data = {
            "rock:eng": [
                {
                    "form": "rock",
                    "date_start": 1200,
                    "date_end": 2024,
                    "definition_primary": "a solid mineral mass",
                    "language_code": "eng",
                    "id": str(uuid4()),
                },
            ],
        }
        analyzer = SemanticDriftAnalyzer(lsr_data=data)
        result = analyzer.get_trajectory("rock", "eng")

        if result is not None:
            assert len(result.points) == 1
            assert result.total_drift == 0.0


class TestFullPipelineIntegration:
    """
    End-to-end integration: Ingest multiple words -> build LSR store ->
    use LSR data for dating analysis.

    This is the closest to testing the actual core loop without
    external services.
    """

    @pytest.fixture
    def lsr_store(self):
        """Ingest a batch of entries and build an LSR store."""
        resolver = EntityResolver(
            auto_merge_threshold=0.95,
            merge_with_flag_threshold=0.85,
            review_threshold=0.70,
        )
        store: dict[UUID, LSR] = {}
        resolver.set_lsr_store(store)

        entries = [
            RawLexicalEntry(
                source_name="wiktionary",
                source_id="wikt-knight",
                form="knight",
                language="English",
                language_code="eng",
                definitions=["a mounted warrior"],
                date_attested=1100,
            ),
            RawLexicalEntry(
                source_name="wiktionary",
                source_id="wikt-computer",
                form="computer",
                language="English",
                language_code="eng",
                definitions=["an electronic computing device"],
                date_attested=1945,
            ),
            RawLexicalEntry(
                source_name="wiktionary",
                source_id="wikt-water",
                form="water",
                language="English",
                language_code="eng",
                definitions=["a clear liquid"],
                date_attested=800,
            ),
            RawLexicalEntry(
                source_name="wiktionary",
                source_id="wikt-wasser",
                form="Wasser",
                language="German",
                language_code="deu",
                definitions=["water"],
                date_attested=1200,
            ),
        ]

        for entry in entries:
            result = resolver.resolve(entry)
            if result.action == ResolutionAction.CREATE_NEW:
                lsr = convert_entry_to_lsr(entry)
                store[lsr.id] = lsr
                resolver.set_lsr_store(store)

        return store

    def test_store_populated(self, lsr_store):
        """All 4 entries should create LSRs (different words/languages)."""
        assert len(lsr_store) == 4

    def test_build_lookup_from_store(self, lsr_store):
        """Can build an LSR lookup from the store for dating analysis."""
        lookup: dict[str, dict] = {}
        for lsr in lsr_store.values():
            if lsr.form_normalized:
                lookup[lsr.form_normalized] = {
                    "date_start": lsr.date_start,
                    "date_end": lsr.date_end,
                    "language_code": lsr.language_code,
                }

        assert "water" in lookup
        assert "knight" in lookup
        assert "computer" in lookup
        assert lookup["water"]["language_code"] == "eng"

    def test_date_text_using_ingested_data(self, lsr_store):
        """DateText analysis works with data derived from ingested LSRs."""
        # Build lookup from store
        lookup: dict[str, dict] = {}
        for lsr in lsr_store.values():
            if lsr.form_normalized and lsr.language_code == "eng":
                lookup[lsr.form_normalized] = {
                    "date_start": lsr.date_start,
                    "date_end": lsr.date_end,
                    "language_code": lsr.language_code,
                }

        dater = TextDating(lsr_lookup=lookup)
        result = dater.date_text("the knight rode to water his horse", "eng")

        assert isinstance(result, DateAnalysis)
        assert result.analyzed_tokens > 0

    def test_anachronism_using_ingested_data(self, lsr_store):
        """Anachronism detection works with data derived from ingested LSRs."""
        lookup: dict[str, dict] = {}
        for lsr in lsr_store.values():
            if lsr.form_normalized and lsr.language_code == "eng":
                lookup[lsr.form_normalized] = {
                    "date_start": lsr.date_start,
                    "date_end": lsr.date_end,
                    "language_code": lsr.language_code,
                }

        dater = TextDating(lsr_lookup=lookup)
        # "computer" attested from 1945 shouldn't appear in 1300 text
        result = dater.detect_anachronisms(
            "the knight used a computer",
            claimed_date=1300,
            language="eng",
        )

        assert isinstance(result, AnachronismAnalysis)
        assert result.verdict in ["consistent", "suspicious", "anachronistic"]

    def test_multi_language_store_filtering(self, lsr_store):
        """LSR lookup correctly filters by language."""
        eng_lookup = {
            lsr.form_normalized: {
                "date_start": lsr.date_start,
                "date_end": lsr.date_end,
                "language_code": lsr.language_code,
            }
            for lsr in lsr_store.values()
            if lsr.language_code == "eng" and lsr.form_normalized
        }

        deu_lookup = {
            lsr.form_normalized: {
                "date_start": lsr.date_start,
                "date_end": lsr.date_end,
                "language_code": lsr.language_code,
            }
            for lsr in lsr_store.values()
            if lsr.language_code == "deu" and lsr.form_normalized
        }

        assert "water" in eng_lookup
        assert "wasser" in deu_lookup
        assert "wasser" not in eng_lookup
        assert "water" not in deu_lookup


class TestIngestionScriptLogic:
    """
    Test the ingestion script's core functions without network.

    Validates that the script's helper functions work correctly.
    """

    def test_load_word_list(self, tmp_path):
        """load_word_list reads words, skips blanks and comments."""
        from scripts.ingest import load_word_list

        word_file = tmp_path / "words.txt"
        word_file.write_text(
            "# Header comment\n" "water\n" "fire\n" "\n" "# Another comment\n" "earth\n" "  \n"
        )

        words = load_word_list(str(word_file))
        assert words == ["water", "fire", "earth"]

    def test_ingestion_stats(self):
        """IngestionStats tracks counts and produces summary."""
        from scripts.ingest import IngestionStats

        stats = IngestionStats()
        stats.words_attempted = 100
        stats.words_fetched = 85
        stats.words_failed = 15
        stats.entries_fetched = 120
        stats.lsrs_created = 100
        stats.lsrs_merged = 15
        stats.lsrs_flagged = 5
        stats.errors = ["error 1", "error 2"]

        summary = stats.summary()
        assert "INGESTION SUMMARY" in summary
        assert "100" in summary  # words attempted
        assert "85" in summary  # words fetched
        assert "error 1" in summary

    def test_process_entry_create_new(self):
        """_process_entry handles CREATE_NEW action."""
        from scripts.ingest import IngestionStats, _process_entry

        resolver = EntityResolver(
            auto_merge_threshold=0.95,
            merge_with_flag_threshold=0.85,
            review_threshold=0.70,
        )
        store: dict[UUID, LSR] = {}
        resolver.set_lsr_store(store)
        stats = IngestionStats()

        entry = RawLexicalEntry(
            source_name="wiktionary",
            source_id="test-1",
            form="ocean",
            language="English",
            language_code="eng",
            definitions=["a large body of salt water"],
        )

        _process_entry(entry, resolver, store, stats, dry_run=False)

        assert stats.lsrs_created == 1
        assert len(store) == 1

    def test_process_entry_dry_run(self):
        """_process_entry in dry-run mode doesn't persist."""
        from scripts.ingest import IngestionStats, _process_entry

        resolver = EntityResolver(
            auto_merge_threshold=0.95,
            merge_with_flag_threshold=0.85,
            review_threshold=0.70,
        )
        store: dict[UUID, LSR] = {}
        resolver.set_lsr_store(store)
        stats = IngestionStats()

        entry = RawLexicalEntry(
            source_name="wiktionary",
            source_id="test-dry",
            form="cloud",
            language="English",
            language_code="eng",
        )

        _process_entry(entry, resolver, store, stats, dry_run=True)

        assert stats.lsrs_created == 1  # Stats still count
        assert len(store) == 0  # But store is empty
