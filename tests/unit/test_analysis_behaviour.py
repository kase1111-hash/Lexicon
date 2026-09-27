"""Behavioural tests for the analyses: they must give right answers, not just right shapes."""

import asyncio
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient

from src.analysis.contact_detection import ContactDetector
from src.analysis.data_access import (
    load_borrowings,
    load_vocabulary,
    lookup_candidates,
    tokenize,
)
from src.analysis.dating import TextDating
from src.api.main import app
from src.exceptions import DatabaseError
from src.models.lsr import LSR
from src.utils.db import get_db
from src.utils.validation import normalize_language_code


def _dated(**years: int | tuple[int, int | None]) -> dict[str, dict]:
    """Build a lookup: word=start or word=(start, end)."""
    lookup = {}
    for word, value in years.items():
        start, end = value if isinstance(value, tuple) else (value, None)
        lookup[word] = {"date_start": start, "date_end": end, "language_code": "eng"}
    return lookup


class TestTokenization:
    def test_tokens_match_lsr_normalization(self):
        """Text tokens normalize exactly like LSR.form_normalized."""
        for word in ("Þis", "café", "Straße", "ský", "naïve"):
            assert tokenize(word) == [LSR(form_orthographic=word).form_normalized]

    def test_non_ascii_words_are_kept(self):
        assert tokenize("Þe knyȝt rood") == ["þe", "knyȝt", "rood"]

    @pytest.mark.parametrize(
        ("token", "base"),
        [
            ("computers", "computer"),
            ("rode", "ride"),
            ("knights'", "knight"),
            ("surveyed", "survey"),
            ("carried", "carry"),
            ("stopped", "stop"),
            ("making", "make"),
            ("berries", "berry"),
            ("children", "child"),
        ],
    )
    def test_english_inflections_try_base_form(self, token, base):
        assert base in lookup_candidates(token, "eng")

    def test_other_languages_use_exact_form(self):
        assert lookup_candidates("chevaux", "fra") == ["chevaux"]


class TestDating:
    def test_newest_word_sets_lower_bound(self):
        result = TextDating(_dated(knight=900, telephone=1835, ride=800)).date_text(
            "The knight rode to the telephone", "eng"
        )
        assert result.status == "ok"
        assert result.predicted_range[0] == 1835
        assert result.diagnostic_vocabulary[0]["sets_bound"] == "lower"

    def test_obsolete_words_set_upper_bound(self):
        result = TextDating(_dated(knight=900, wight=(900, 1600))).date_text(
            "The knight met a wight", "eng"
        )
        assert result.predicted_range == (900, 1600)

    def test_conflict_prefers_coinage_evidence(self):
        """A word coined after another fell out of use: report, don't average."""
        result = TextDating(_dated(wight=(900, 1600), telephone=1835)).date_text(
            "The wight answered the telephone", "eng"
        )
        assert result.status == "conflicting_evidence"
        assert result.predicted_range[0] == 1835
        assert "wight" in result.explanation

    def test_unknown_words_are_reported(self):
        result = TextDating(_dated(knight=900)).date_text("The knight saw a zeppelin", "eng")
        assert "zeppelin" in result.unknown_words

    def test_confidence_grows_with_evidence(self):
        lookup = _dated(knight=900, horse=700, river=1300, castle=1075, sword=700, king=700)
        few = TextDating(lookup).date_text("knight and dragon", "eng")
        many = TextDating(lookup).date_text("knight horse river castle sword king", "eng")
        assert many.confidence > few.confidence


class TestAnachronisms:
    def test_inflected_anachronism_is_found(self):
        result = TextDating(_dated(computer=1646, knight=900)).detect_anachronisms(
            "The knights used computers", 1300
        )
        assert result.verdict in ("suspicious", "anachronistic")
        assert result.anachronisms[0]["form"] == "computer"

    def test_low_coverage_without_anachronisms_is_insufficient(self):
        result = TextDating(_dated(knight=900)).detect_anachronisms(
            "The knight spoke with sprockets gizmos widgets", 1300
        )
        assert result.verdict == "insufficient_data"
        assert result.confidence == 0.0

    def test_well_covered_text_is_consistent_but_not_certain(self):
        result = TextDating(_dated(knight=900, horse=700, river=1300)).detect_anachronisms(
            "The knight led his horse to the river", 1350
        )
        assert result.verdict == "consistent"
        assert 0.0 < result.confidence < 1.0

    def test_anachronism_names_the_evidence_for_its_date(self):
        """A corpus-only date is visibly a corpus date, not a known coinage."""
        lookup = _dated(truth=1813)
        lookup["truth"]["date_label"] = "earliest in corpus: Pride and Prejudice"
        result = TextDating(lookup).detect_anachronisms("the truth", 1600)
        assert result.anachronisms[0]["date_label"] == "earliest in corpus: Pride and Prejudice"
        dated = TextDating(lookup).date_text("the truth", "eng")
        assert dated.diagnostic_vocabulary[0]["date_label"].startswith("earliest in corpus")

    def test_repeated_words_count_once_for_coverage(self):
        result = TextDating(_dated(king=700)).detect_anachronisms(
            "king king king king king king zorblax", 1300
        )
        assert result.content_tokens == 2
        assert result.dated_tokens == 1

    def test_minor_postdating_is_described_accurately(self):
        result = TextDating(_dated(radio=1913, horse=700, river=1300)).detect_anachronisms(
            "The radio by the horse near the river", 1880
        )
        assert result.verdict == "consistent"
        assert "more than 50 years after 1880" in result.explanation
        assert "radio (1913)" in result.explanation

    def test_archaism_is_reported_but_not_decisive(self):
        result = TextDating(_dated(wight=(900, 1600), horse=700, river=1300)).detect_anachronisms(
            "The wight rode a horse along the river", 1900
        )
        assert result.verdict == "consistent"
        assert result.anachronisms[0]["type"] == "obsolete_before"

    def test_other_language_entries_are_ignored(self):
        lookup = {"computer": {"date_start": 1646, "date_end": None, "language_code": "fra"}}
        result = TextDating(lookup).detect_anachronisms("the computer works", 1300, "eng")
        assert result.verdict == "insufficient_data"


class FakeResult:
    def __init__(self, records):
        self._records = records

    async def fetch(self, n):
        return self._records[:n]


class FakeDB:
    """Records the last query and returns canned records."""

    def __init__(self, records=None, connected=True):
        self.records = records or []
        self.connected = connected
        self.queries: list[tuple[str, dict]] = []
        self.timeouts: list[float | None] = []

    @asynccontextmanager
    async def neo4j_session(self):
        if not self.connected:
            raise RuntimeError("Neo4j not connected")
        db = self

        class Session:
            async def run(self, query, params=None):
                db.timeouts.append(getattr(query, "timeout", None))
                db.queries.append((getattr(query, "text", query), params or {}))
                return FakeResult(db.records)

        yield Session()


class TestDataAccess:
    def test_vocabulary_queries_only_requested_forms(self):
        db = FakeDB([{"form": "sky", "date_start": 1220, "date_end": None, "senses": 2}])
        vocab = asyncio.run(load_vocabulary(db, "eng", ["sky", "egg"]))
        query, params = db.queries[0]
        assert "form_normalized IN $forms" in query
        assert params == {"lang": "eng", "forms": ["egg", "sky"]}
        assert vocab["sky"]["date_start"] == 1220
        assert vocab["sky"]["senses"] == 2
        assert db.timeouts == [15]  # a stalled Neo4j fails the request

    def test_database_outage_is_an_error_not_empty_data(self):
        with pytest.raises(DatabaseError):
            asyncio.run(load_vocabulary(FakeDB(connected=False), "eng", ["sky"]))

    def test_neo4j_dropping_mid_run_is_503_not_500(self):
        """A driver error after startup (not just 'never connected') is a DatabaseError."""
        from neo4j.exceptions import ServiceUnavailable

        class DroppedDB(FakeDB):
            @asynccontextmanager
            async def neo4j_session(self):
                class Session:
                    async def run(self, query, params=None):
                        raise ServiceUnavailable("Couldn't connect to localhost:7687")

                yield Session()

        with pytest.raises(DatabaseError) as error:
            asyncio.run(load_vocabulary(DroppedDB(), "eng", ["sky"]))
        assert "localhost" not in error.value.message  # no driver internals

    def test_borrowings_load_both_directions(self):
        db = FakeDB()
        asyncio.run(load_borrowings(db, "eng"))
        query, _ = db.queries[0]
        assert "recipient.language_code = $lang OR donor.language_code = $lang" in query


class TestContactDetection:
    def _borrowing(self, form, donor, date, definition=None):
        return {
            "form": form,
            "source_lang": donor,
            "target_lang": "eng",
            "date": date,
            "definition": definition,
            "semantic_fields": [],
        }

    def test_contact_dated_by_borrowing_period(self):
        borrowings = [
            self._borrowing(w, "non", 1200 + i)
            for i, w in enumerate(["sky", "egg", "skin", "skull", "leg"])
        ]
        events = ContactDetector(borrowing_data=borrowings).detect_contacts("eng")
        assert len(events) == 1
        assert events[0].donor_language == "non"
        assert events[0].date_range == (1200, 1300)

    def test_null_definitions_do_not_crash(self):
        borrowings = [self._borrowing(f"w{i}", "fra", 1300, None) for i in range(5)]
        assert ContactDetector(borrowing_data=borrowings).detect_contacts("eng")

    def test_domains_match_whole_words(self):
        """'war' must not match inside 'warm'."""
        detector = ContactDetector()
        domains = detector._group_by_domain([{"definition": "warm weather"}])
        assert "military" not in domains


class TestLanguageCodes:
    @pytest.mark.parametrize(
        ("value", "code"),
        [
            ("en", "eng"),
            ("ENG", "eng"),
            ("gem-pro", "gem-pro"),
            ("la-vul", "la-vul"),
            ("yaku1245", "yaku1245"),
        ],
    )
    def test_accepted(self, value, code):
        assert normalize_language_code(value) == code

    @pytest.mark.parametrize("value", ["", "x", "e1", "english!", "english", "en-gb", "en-us"])
    def test_rejected(self, value):
        with pytest.raises(ValueError):
            normalize_language_code(value)


class TestAnalysisRoutes:
    """The REST layer passes coverage through and surfaces outages."""

    def _client(self, db):
        async def override():
            return db

        app.dependency_overrides[get_db] = override
        return TestClient(app)

    def teardown_method(self):
        app.dependency_overrides.clear()

    def test_empty_graph_is_insufficient_data(self):
        response = self._client(FakeDB([])).post(
            "/api/v1/analyze/detect-anachronisms",
            json={"text": "The knight used a computer", "claimed_date": 1300, "language": "eng"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["verdict"] == "insufficient_data"
        assert body["confidence"] == 0.0
        assert body["analysis"]["dated_words"] == 0

    def test_date_text_null_range_without_data(self):
        response = self._client(FakeDB([])).post(
            "/api/v1/analyze/date-text", json={"text": "The knight rode forth", "language": "en"}
        )
        assert response.status_code == 200
        assert response.json()["predicted_date_range"] is None
        assert response.json()["status"] == "insufficient_data"

    def test_neo4j_down_is_503(self):
        response = self._client(FakeDB(connected=False)).post(
            "/api/v1/analyze/date-text", json={"text": "The knight rode forth", "language": "eng"}
        )
        assert response.status_code == 503

    def test_invalid_language_is_rejected(self):
        response = self._client(FakeDB([])).get(
            "/api/v1/analyze/contact-events", params={"language": "e1"}
        )
        assert response.status_code == 400
