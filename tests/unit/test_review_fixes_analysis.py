"""Regression tests for the analysis review fixes.

Covers the read-only Cypher filter, the tokenizer and inflection fallbacks,
contact-event counting, text-dating bounds, semantic drift across dates, the
analysis request models and the CLI's behaviour when Neo4j fails.
"""

import asyncio
import json
import sys
import unicodedata
from contextlib import asynccontextmanager
from typing import Any

import pytest
from fastapi.testclient import TestClient
from neo4j.exceptions import ClientError, ServiceUnavailable

import src.cli as cli
import src.pipelines.graph_writer as graph_writer
from src.analysis.contact_detection import ContactDetector
from src.analysis.data_access import lookup_candidates, normalize, tokenize
from src.analysis.dating import TextDating
from src.analysis.semantic_drift import SemanticDriftAnalyzer, assess_trajectory
from src.api.graphql import resolvers
from src.api.main import app
from src.exceptions import DatabaseError
from src.models.lsr import LSR, YEAR_MAX, YEAR_MIN
from src.repositories.lsr_repository import BatchResult, LSRRepository
from src.utils.db import get_db
from src.utils.languages import ISO_639_1_TO_3
from src.utils.validation import (
    AnachronismRequest,
    DateTextRequest,
    LSRCreateRequest,
    normalize_language_code,
    validate_read_only_cypher,
)

BS = chr(92)  # a real backslash, so the queries hold escapes as Neo4j sees them


def _esc(text: str, us: str = "u") -> str:
    """Spell every character of text as a Java-style unicode escape."""
    return "".join(BS + us + format(ord(ch), "04x") for ch in text)


def _lookup(language: str = "eng", **years: int | tuple[int, int | None]) -> dict[str, dict]:
    """Build a vocabulary lookup: word=start or word=(start, end)."""
    lookup = {}
    for word, value in years.items():
        start, end = value if isinstance(value, tuple) else (value, None)
        lookup[normalize(word)] = {"date_start": start, "date_end": end, "language_code": language}
    return lookup


class FakeResult:
    def __init__(self, records: list[dict]):
        self._records = records

    async def fetch(self, n: int) -> list[dict]:
        return self._records[:n]


class FakeDB:
    """A DatabaseManager stand-in: canned records, or a failure on every query."""

    class config:
        neo4j_uri = "bolt://fake:7687"
        elasticsearch_configured = False
        redis_configured = False

    def __init__(self, records: list[dict] | None = None, error: Exception | None = None):
        self.records = records or []
        self.error = error
        self.closed = False

    async def connect_neo4j(self) -> bool:
        return True

    def get_connection_errors(self) -> dict:
        return {}

    async def close_all(self) -> None:
        self.closed = True

    @asynccontextmanager
    async def neo4j_session(self):
        db = self

        class Session:
            async def run(self, query: Any, params: dict | None = None) -> FakeResult:
                if db.error is not None:
                    raise db.error
                return FakeResult(db.records)

        yield Session()


def _sense(definition: str, date: int, vector: list[float]) -> dict:
    """A load_trajectory record for one dated sense."""
    return {
        "form": "male",
        "date_start": date,
        "date_end": None,
        "definition": definition,
        "semantic_vector": vector,
        "confidence": 1.0,
        "id": None,
    }


SAME_DATE_SENSES = [
    _sense("male (of person)", 1382, [1.0, 0.0, 0.0]),
    _sense("male (of animal)", 1382, [0.0, 1.0, 0.0]),
]


# =============================================================================
# Finding 11: unicode escapes cannot smuggle keywords past /graph/query
# =============================================================================

ESCAPE_BYPASSES = [
    # The reviewer's reproductions (executed by Neo4j 5.9 before the fix)
    f"WITH 1 AS x {_esc('C')}ALL {_esc('d')}bms.components() YIELD name RETURN name",
    f"WITH 1 AS x {_esc('C', 'uu')}ALL {_esc('d', 'uu')}bms.components() YIELD name RETURN name",
    f"WITH 1 AS x {_esc('L')}OAD CSV FROM 'http://localhost:7474/' AS r RETURN r[0] AS line",
    f"WITH 1 AS x {_esc('C')}ALL {_esc('d')}bms.listConfig() YIELD name, value RETURN name",
    f"MATCH (n) WITH count(n) AS c {_esc('C')}ALL {_esc('d')}bms.showCurrentUser() "
    "YIELD username RETURN username",
    f"WITH 1 AS x {_esc('CALL')} db.labels() YIELD label RETURN label",
    f"{_esc('M')}ATCH (n) RETURN count(n) AS c",
    # Every other forbidden clause, spelled with escapes
    f"WITH 1 AS x {_esc('USE')} system MATCH (n) RETURN n",
    f"WITH 1 AS x USING {_esc('PERIODIC')} COMMIT RETURN x",
    f"MATCH (n) RETURN {_esc('apoc')}.text.join(['a'], ',') AS s",
    f"MATCH (n) {_esc('DETACH')} {_esc('DELETE')} n",
    f"MATCH (n) {_esc('SET')} n.x = 1 RETURN n",
    f"MATCH (n) RETURN `{_esc('dbms')}`.x AS y",
    f"WITH 1 AS x {BS}U00000043ALL dbms.components() YIELD name RETURN name",
    # A decoded quote or newline would end a string or comment early
    f"MATCH (n) WHERE n.a = '{_esc(chr(39))} DETACH DELETE n //' RETURN n",
    f"MATCH (n) // {_esc(chr(10))} DETACH DELETE n\nRETURN n",
    # Backslashes mean nothing outside string literals
    f"MATCH (n) RETURN n {BS} LIMIT 1",
]


class TestCypherUnicodeEscapes:
    @pytest.mark.parametrize("query", ESCAPE_BYPASSES)
    def test_escaped_keywords_are_rejected(self, query: str) -> None:
        assert BS in query
        with pytest.raises(ValueError):
            validate_read_only_cypher(query)

    @pytest.mark.parametrize(
        "query",
        [
            f"MATCH (n:LSR) WHERE n.form_orthographic = 'it{BS}'s' RETURN n LIMIT 1",
            f"MATCH (n:LSR) RETURN 'a{BS}{BS}b' AS s, 'tab{BS}t' AS t LIMIT 1",
        ],
    )
    def test_string_escapes_still_allowed(self, query: str) -> None:
        assert validate_read_only_cypher(query) == query

    @pytest.mark.parametrize("query", ESCAPE_BYPASSES[:6])
    def test_endpoint_rejects_escape_bypass(self, query: str) -> None:
        response = TestClient(app).post("/api/v1/graph/query", json={"query": query})
        assert response.status_code == 400
        assert response.json()["error"] == "VALIDATION_ERROR"


# =============================================================================
# Finding 12: inflected forms are dated by their own lemma
# =============================================================================


class TestInflectionFallback:
    @pytest.mark.parametrize(
        ("token", "base", "other_word"),
        [
            ("fades", "fade", "fad"),
            ("faded", "fade", "fad"),
            ("fading", "fade", "fad"),
            ("modes", "mode", "mod"),
            ("grades", "grade", "grad"),
            ("graded", "grade", "grad"),
            ("pines", "pine", "pin"),
            ("stared", "stare", "star"),
            ("phoned", "phone", "phon"),
            ("cared", "care", "car"),
            ("hoped", "hope", "hop"),
            ("hoping", "hope", "hop"),
            ("used", "use", "us"),
            ("uses", "use", "us"),
            ("hopped", "hop", "hopp"),
            ("hopping", "hop", "hopp"),
            ("starred", "star", "starr"),
            # Bases that end in a double letter keep it
            ("called", "call", "cal"),
            ("passed", "pass", "pas"),
            ("added", "add", "ad"),
            # -sses is a base in -ss, not an accented word such as passé or massé
            ("passes", "pass", "passe"),
            ("masses", "mass", "masse"),
        ],
    )
    def test_base_form_is_tried_before_other_words(self, token, base, other_word):
        candidates = lookup_candidates(token, "eng")
        assert candidates[0] == token
        assert candidates.index(base) < candidates.index(other_word)

    @pytest.mark.parametrize(
        ("token", "base"),
        [
            ("boxes", "box"),
            ("hopping", "hop"),
            ("mousses", "mousse"),
            # A base ending in a double consonant and e
            ("gazetted", "gazette"),
            ("finessed", "finesse"),
            ("silhouetting", "silhouette"),
        ],
    )
    def test_other_bases_still_found(self, token, base):
        assert base in lookup_candidates(token, "eng")
        assert "hope" not in lookup_candidates("hopping", "eng")

    def test_accented_homograph_does_not_make_anachronism(self):
        """passé normalizes to "passe", so "passes" must reach pass first."""
        lookup = _lookup(**{"pass": 1300, "passé": 1775, "ship": 900, "harbour": 1100})
        result = TextDating(lookup).detect_anachronisms("The ship passes the harbour", 1600)
        assert result.verdict == "consistent"
        forms = {
            w["word"]: w["form"]
            for w in TextDating(lookup)
            .date_text("The ship passes the harbour")
            .diagnostic_vocabulary
        }
        assert forms["passes"] == "pass"

    def test_newer_homograph_stem_does_not_make_anachronism(self):
        """fades/modes/faded are fade and mode, not fad (1834) and mod (1960)."""
        lookup = _lookup(fade=1300, fad=1834, mode=1380, mod=1960, colour=1300, music=1250)
        result = TextDating(lookup).detect_anachronisms(
            "The colour fades; the music modes faded", 1700
        )
        assert result.verdict == "consistent"
        assert result.anachronisms == []

    def test_date_text_uses_lemma(self):
        lookup = _lookup(grade=1511, grad=1871, pupil=1390, teacher=1300)
        result = TextDating(lookup).date_text("The teacher graded the pupil grades")
        assert result.predicted_range is not None
        assert result.predicted_range[0] == 1511
        forms = {w["word"]: w["form"] for w in result.diagnostic_vocabulary}
        assert forms["grades"] == "grade"
        assert forms["graded"] == "grade"

    def test_hoping_and_hopping_are_different_words(self):
        lookup = _lookup(hope=900, hop=1000, rabbit=1400)
        result = TextDating(lookup).date_text("The rabbit was hopping, hoping, hopped")
        forms = {w["word"]: w["form"] for w in result.diagnostic_vocabulary}
        assert forms == {"hopping": "hop", "hoping": "hope", "hopped": "hop", "rabbit": "rabbit"}


# =============================================================================
# Finding 13: combining marks stay inside words
# =============================================================================


class TestCombiningMarks:
    @pytest.mark.parametrize(
        "word",
        [
            "किताब",  # Hindi: vowel signs
            "संस्कृत",  # Sanskrit: anusvara, virama
            "தமிழ்",  # Tamil: vowel sign, virama
            "বাংলা",  # Bengali
            "كِتَاب",  # vocalised Arabic
            "שָׁלוֹם",  # Hebrew with niqqud
            "می‌خواهم",  # Persian with a zero-width non-joiner
            "naïve",
        ],
    )
    def test_word_is_one_token_normalized_like_lsr(self, word: str) -> None:
        assert tokenize(word) == [LSR(form_orthographic=word).form_normalized]
        assert tokenize(f"{word} {word}") == [LSR(form_orthographic=word).form_normalized] * 2

    def test_decomposed_latin_is_not_split(self):
        text = unicodedata.normalize("NFD", "café naïve crème brûlée")
        assert tokenize(text) == ["cafe", "naive", "creme", "brulee"]

    def test_apostrophes_and_hyphens_unchanged(self):
        assert tokenize("The knight's self-evident 'tis x- knights'") == [
            "the",
            "knight's",
            "self-evident",
            "tis",
            "x",
            "knights",
        ]

    def test_hindi_text_is_analysed(self):
        lookup = _lookup("hin", **{"किताब": 1200, "कंप्यूटर": 1960})
        result = TextDating(lookup).detect_anachronisms("किताब और कंप्यूटर", 1300, "hin")
        assert result.content_tokens == 2  # "और" has two characters, like short stop words
        assert result.dated_tokens == 2
        assert [a["word"] for a in result.anachronisms] == [normalize("कंप्यूटर")]
        assert result.verdict in ("suspicious", "anachronistic")


# =============================================================================
# Finding 14 (and the same-date drift rule): `lexicon analyze drift --json`
# =============================================================================


def _run_cli(monkeypatch, capsys, argv: list[str], db: Any) -> tuple[int, str, str]:
    """Run `lexicon <argv>` against a fake database; return (exit code, stdout, stderr)."""
    monkeypatch.setattr(cli, "DatabaseManager", lambda: db)
    monkeypatch.setattr(sys, "argv", ["lexicon", *argv])
    code = 0
    try:
        cli.main()
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
    out, err = capsys.readouterr()
    return code, out, err


class TestDriftCli:
    def test_json_output(self, monkeypatch, capsys):
        senses = [
            _sense("foolish, silly", 1300, [1.0, 0.0, 0.0]),
            _sense("pleasant, agreeable", 1769, [0.0, 1.0, 0.0]),
        ]
        code, out, _ = _run_cli(
            monkeypatch, capsys, ["analyze", "drift", "--form", "male", "--json"], FakeDB(senses)
        )
        assert code == 0
        report = json.loads(out)
        assert report["status"] == "ok"
        assert [p["date"] for p in report["trajectory"]] == [1300, 1769]
        assert report["shift_events"][0]["date"] == 1769
        assert set(report) >= {"explanation", "total_drift", "stability_score"}

    def test_json_output_without_senses(self, monkeypatch, capsys):
        code, out, _ = _run_cli(
            monkeypatch, capsys, ["analyze", "drift", "--form", "male", "--json"], FakeDB([])
        )
        assert code == 0
        report = json.loads(out)
        assert report["status"] == "insufficient_data"
        assert report["trajectory"] == [] and report["total_drift"] is None

    def test_same_date_senses_are_insufficient(self, monkeypatch, capsys):
        db = FakeDB(SAME_DATE_SENSES)
        code, out, _ = _run_cli(monkeypatch, capsys, ["analyze", "drift", "--form", "male"], db)
        assert code == 0
        assert out.startswith("insufficient_data:")
        assert "1382" in out and "shift" not in out

        _, out, _ = _run_cli(
            monkeypatch, capsys, ["analyze", "drift", "--form", "male", "--json"], db
        )
        assert json.loads(out)["status"] == "insufficient_data"


# =============================================================================
# Findings 15-17: contact events count words, and domains need evidence
# =============================================================================


def _borrowing(form: str, date: int = 1300, fields: list[str] | None = None) -> dict:
    return {
        "form": form,
        "source_lang": "fra",
        "target_lang": "eng",
        "date": date,
        "definition": None,
        "semantic_fields": fields or [],
    }


class TestContactCounting:
    def test_senses_of_one_word_are_not_an_event(self):
        borrowings = [_borrowing("court", fields=["Law"]) for _ in range(5)]
        assert ContactDetector(borrowing_data=borrowings).detect_contacts("eng") == []

    def test_min_borrowings_counts_distinct_words(self):
        borrowings = [_borrowing(w) for w in ["court", "court", "court", "judge", "jury", "fee"]]
        detector = ContactDetector(borrowing_data=borrowings)
        assert detector.detect_contacts("eng") == []
        assert len(detector.detect_contacts("eng", min_borrowings=4)) == 1

    def test_counts_and_confidence_ignore_duplicate_senses(self):
        words = ["court", "judge", "jury", "bailiff", "verdict"]
        single = [_borrowing(w, fields=["Law"]) for w in words]
        repeated = single + [_borrowing("court", fields=["Law"]) for _ in range(4)]

        (plain,) = ContactDetector(borrowing_data=single).detect_contacts("eng")
        (event,) = ContactDetector(borrowing_data=repeated).detect_contacts("eng")
        assert event.vocabulary_count == 5
        assert event.sample_words == words
        assert event.intensity == plain.intensity == 0.1
        assert event.confidence == plain.confidence
        assert event.evidence["domain_distribution"] == {"Law": 5}

    def test_no_domain_credit_without_domain_evidence(self):
        undomained = [_borrowing(f"w{i}") for i in range(5)]
        distinct = [_borrowing(f"w{i}", fields=[f"F{i}"]) for i in range(5)]
        shared = [_borrowing(f"w{i}", fields=["Law"]) for i in range(5)]

        def confidence(borrowings: list[dict]) -> float:
            (event,) = ContactDetector(borrowing_data=borrowings).detect_contacts("eng")
            return event.confidence

        # 5 words (0.25 x 0.4) and identical dates (1.0 x 0.3), no domain credit
        assert confidence(undomained) == 0.4
        assert confidence(undomained) < confidence(distinct) < confidence(shared)

    def test_domain_credit_scales_with_words_that_have_domains(self):
        half = [_borrowing(f"w{i}", fields=["Law"] if i < 5 else []) for i in range(10)]
        (event,) = ContactDetector(borrowing_data=half).detect_contacts("eng")
        # 10 words (0.5 x 0.4) + concentrated domains for half the words (0.5 x 0.3) + 0.3
        assert event.confidence == 0.65

    def test_many_fields_give_no_negative_domain_score(self):
        borrowings = [_borrowing(f"w{i}", fields=[f"F{i}"]) for i in range(24)]
        (event,) = ContactDetector(borrowing_data=borrowings).detect_contacts("eng")
        assert event.confidence == 0.7  # 1.0 x 0.4 + 0 x 0.3 + 1.0 x 0.3

    def test_kinship_is_not_trade(self):
        borrowings = [_borrowing(f"w{i}", fields=["Kinship"]) for i in range(6)]
        (event,) = ContactDetector(borrowing_data=borrowings).detect_contacts("eng")
        assert event.contact_type != "trade"

    @pytest.mark.parametrize(
        ("field", "contact_type"),
        [
            ("Warfare and hunting", "conquest"),
            ("Law", "conquest"),
            ("Religion and belief", "religious"),
            ("Food and drink", "cultural"),
        ],
    )
    def test_wold_fields_classified_by_whole_words(self, field, contact_type):
        borrowings = [_borrowing(f"w{i}", fields=[field]) for i in range(6)]
        (event,) = ContactDetector(borrowing_data=borrowings).detect_contacts("eng")
        assert event.contact_type == contact_type


# =============================================================================
# Findings 18-19: Neo4j failures after connecting exit 2 with a message
# =============================================================================


class TestCliDatabaseFailures:
    @pytest.mark.parametrize(
        "argv",
        [
            ["analyze", "date-text", "--text", "The knight rode forth", "--json"],
            ["analyze", "anachronisms", "--text", "The knight rode", "--date", "1300"],
            ["analyze", "contact", "--language", "eng", "--json"],
            ["analyze", "drift", "--form", "nice", "--json"],
        ],
    )
    def test_dropped_connection_exits_2(self, monkeypatch, capsys, argv):
        db = FakeDB(error=ServiceUnavailable("connection dropped"))
        code, out, err = _run_cli(monkeypatch, capsys, argv, db)
        assert code == 2
        assert out == ""
        assert err.startswith("Error: ") and "Traceback" not in err
        assert db.closed

    def test_stats_json_on_database_error(self, monkeypatch, capsys):
        async def failing(self):
            raise DatabaseError(message="Statistics query timed out")

        monkeypatch.setattr(LSRRepository, "get_statistics", failing)
        code, out, err = _run_cli(monkeypatch, capsys, ["stats", "--json"], FakeDB())
        assert code == 2
        assert out == ""
        assert "Statistics query timed out" in err

    def test_stats_json_on_error_key(self, monkeypatch, capsys):
        async def partial(self):
            return {"total_lsrs": 10, "error": "timeout"}

        monkeypatch.setattr(LSRRepository, "get_statistics", partial)
        code, out, err = _run_cli(monkeypatch, capsys, ["stats", "--json"], FakeDB())
        assert code == 2
        assert out == ""
        assert "timeout" in err

    def test_unmapped_driver_error_exits_2(self, monkeypatch, capsys):
        async def failing(self, **kwargs):
            raise ClientError("Neo.ClientError.Statement.SyntaxError")

        monkeypatch.setattr(LSRRepository, "search", failing)
        code, out, err = _run_cli(monkeypatch, capsys, ["search", "--form", "sky"], FakeDB())
        assert code == 2
        assert out == ""
        assert "Neo4j query failed" in err


class TestReindexCli:
    def test_reindex_clears_api_cache(self, monkeypatch, capsys):
        calls: list[bool] = []

        async def reindex(self):
            return BatchResult(succeeded=3)

        async def clear(manager, owns_db):
            calls.append(owns_db)
            return True

        monkeypatch.setattr(LSRRepository, "reindex_all_to_elasticsearch", reindex)
        monkeypatch.setattr(graph_writer, "_clear_api_cache", clear)
        code, out, _ = _run_cli(monkeypatch, capsys, ["reindex"], FakeDB())
        assert code == 0
        assert "Indexed 3 LSRs" in out
        assert calls == [True]


# =============================================================================
# Finding 20: no upper bound from a word when the range runs to the present
# =============================================================================


class TestUpperBound:
    def test_conflicting_evidence_marks_no_upper_bound(self):
        lookup = _lookup(wight=(900, 1600), telephone=1835, horse=700, answer=700)
        result = TextDating(lookup).date_text("The wight answered the telephone on a horse")
        assert result.status == "conflicting_evidence"
        bounds = {w["word"]: w["sets_bound"] for w in result.diagnostic_vocabulary}
        assert bounds["telephone"] == "lower"
        assert "upper" not in bounds.values()

    def test_consistent_obsolete_word_sets_upper_bound(self):
        lookup = _lookup(knight=900, wight=(800, 1600), horse=700)
        result = TextDating(lookup).date_text("The knight met a wight on a horse")
        assert result.predicted_range == (900, 1600)
        bounds = {w["word"]: w["sets_bound"] for w in result.diagnostic_vocabulary}
        assert bounds["wight"] == "upper"


class TestAnachronismExplanation:
    def test_singular_postdates(self):
        lookup = _lookup(knight=900, castle=1000, horse=700, sword=800, lance=1320)
        result = TextDating(lookup).detect_anachronisms("knight castle horse sword lance", 1300)
        assert result.verdict == "consistent"
        assert "1 postdates it by 50 years or less" in result.explanation

    def test_plural_postdate(self):
        lookup = _lookup(knight=900, castle=1000, horse=700, sword=1310, lance=1320)
        result = TextDating(lookup).detect_anachronisms("knight castle horse sword lance", 1300)
        assert "2 postdate it by 50 years or less" in result.explanation


# =============================================================================
# Semantic drift: senses of the same date are not change over time
# =============================================================================


def _trajectory(senses: list[dict]):
    data = [
        {
            "date_start": s["date_start"],
            "definition_primary": s["definition"],
            "semantic_vector": s["semantic_vector"],
            "language_code": "eng",
        }
        for s in senses
    ]
    return SemanticDriftAnalyzer(lsr_data={"male:eng": data}).get_trajectory("male", "eng")


class TestDriftAcrossDates:
    def test_same_date_senses_are_no_shift(self):
        trajectory = _trajectory(SAME_DATE_SENSES)
        assert trajectory is not None
        assert trajectory.shift_events == []
        assert trajectory.total_drift == 0.0
        status, explanation = assess_trajectory(trajectory, "male", "eng")
        assert status == "insufficient_data"
        assert "all first attested in 1382" in explanation
        assert "two different years" in explanation

    def test_later_sense_compared_with_closest_earlier_sense(self):
        close_to_person = _sense("male person, man", 1500, [0.99, 0.1, 0.0])
        trajectory = _trajectory([*SAME_DATE_SENSES, close_to_person])
        assert trajectory is not None
        assert trajectory.shift_events == []  # close to the 1382 "of person" sense
        assert 0 < trajectory.total_drift < 0.2
        assert assess_trajectory(trajectory, "male", "eng")[0] == "ok"

    def test_new_meaning_at_a_later_date_is_a_shift(self):
        new = _sense("a type of plug", 1870, [0.0, 0.0, 1.0])
        trajectory = _trajectory([*SAME_DATE_SENSES, new])
        assert trajectory is not None
        assert [e.date for e in trajectory.shift_events] == [1870]

    def _client(self, db: FakeDB) -> TestClient:
        async def override():
            return db

        app.dependency_overrides[get_db] = override
        return TestClient(app)

    def teardown_method(self):
        app.dependency_overrides.clear()

    def test_rest_semantic_drift(self):
        response = self._client(FakeDB(SAME_DATE_SENSES)).get(
            "/api/v1/analyze/semantic-drift", params={"form": "male", "language": "eng"}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "insufficient_data"
        assert body["trajectory"] == [] and body["shift_events"] == []
        assert "1382" in body["explanation"]

    def test_rest_compare_concept(self):
        response = self._client(FakeDB(SAME_DATE_SENSES)).get(
            "/api/v1/analyze/compare-concept", params={"concept": "male", "languages": "eng"}
        )
        assert response.status_code == 200
        (result,) = response.json()["by_language"]
        assert result["status"] == "insufficient_data"
        assert result["trajectory"] is None
        assert "1382" in result["explanation"]

    def test_graphql_resolver(self):
        data = asyncio.run(
            resolvers.resolve_semantic_trajectory(FakeDB(SAME_DATE_SENSES), "male", "eng")
        )
        assert data["points"] == [] and data["shift_events"] == []
        assert data["status"] == "insufficient_data"

        later = [*SAME_DATE_SENSES, _sense("a type of plug", 1870, [0.0, 0.0, 1.0])]
        data = asyncio.run(resolvers.resolve_semantic_trajectory(FakeDB(later), "male", "eng"))
        assert [p["date"] for p in data["points"]] == [1382, 1382, 1870]
        assert [s["date"] for s in data["shift_events"]] == [1870]


# =============================================================================
# Request validation
# =============================================================================


class TestAnalysisRequests:
    def test_anachronism_text_length_after_collapsing_whitespace(self):
        with pytest.raises(ValueError, match="at least 10 characters"):
            AnachronismRequest(text="a    b    c    ", claimed_date=1300, language="eng")
        response = TestClient(app).post(
            "/api/v1/analyze/detect-anachronisms",
            json={"text": "a    b    c    ", "claimed_date": 1300, "language": "eng"},
        )
        assert response.status_code == 400

    def test_long_language_codes_accepted(self):
        assert DateTextRequest(text="The knight rode forth", language="ine-bsl-pro").language
        assert AnachronismRequest(
            text="The knight rode forth", claimed_date=900, language="ine-bsl-pro"
        ).language == ("ine-bsl-pro")
        assert LSRCreateRequest(form_orthographic="*x", language_code="ine-bsl-pro")

    @pytest.mark.parametrize(
        ("path", "params"),
        [
            ("/api/v1/analyze/contact-events", {"language": "ine-bsl-pro"}),
            ("/api/v1/analyze/semantic-drift", {"form": "x", "language": "ine-bsl-pro"}),
            (
                "/api/v1/analyze/compare-concept",
                {"concept": "x", "languages": ",".join(["ine-bsl-pro"] * 10)},
            ),
        ],
    )
    def test_long_language_codes_accepted_by_routes(self, path, params):
        async def override():
            return FakeDB([])

        app.dependency_overrides[get_db] = override
        try:
            assert TestClient(app).get(path, params=params).status_code == 200
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.parametrize(
        "dates", [{"date_start": 2101}, {"date_end": 2101}, {"date_start": -10001}]
    )
    def test_lsr_create_dates_within_model_range(self, dates):
        with pytest.raises(ValueError):
            LSRCreateRequest(form_orthographic="water", language_code="eng", **dates)
        assert LSRCreateRequest(
            form_orthographic="water", language_code="eng", date_start=-10000, date_end=2100
        )

    @pytest.mark.parametrize("field", ["date_start", "date_end"])
    def test_lsr_create_years_match_lsr_model(self, field):
        """The request accepts exactly the years an LSR can hold (YEAR_MIN..YEAR_MAX)."""
        base = {"form_orthographic": "water", "language_code": "eng"}
        for year in (YEAR_MIN, YEAR_MAX):
            assert getattr(LSR(**base, **{field: year}), field) == year
            assert getattr(LSRCreateRequest(**base, **{field: year}), field) == year
        for year in (YEAR_MIN - 1, YEAR_MAX + 1):
            with pytest.raises(ValueError):
                LSR(**base, **{field: year})
            with pytest.raises(ValueError):
                LSRCreateRequest(**base, **{field: year})

    def test_iso_639_1_codes_come_from_language_table(self):
        for short, code in ISO_639_1_TO_3.items():
            assert normalize_language_code(short) == code
