"""Follow-ups to the review fixes that span groups: shared bounds, word
boundaries, GraphQL drift status, Sentry release and compose settings."""

import asyncio
import sys
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
import yaml
from fastapi.testclient import TestClient

import src
from src.adapters.corpus import CorpusAdapter
from src.analysis.data_access import tokenize
from src.api.graphql import resolvers
from src.api.graphql.schema import schema
from src.api.main import app
from src.models.lsr import LSR, YEAR_MAX, YEAR_MIN
from src.utils import validation
from src.utils.error_tracking import SentryIntegration

REPO_ROOT = Path(__file__).resolve().parents[2]


class TestYearBounds:
    def test_request_bounds_are_the_models(self) -> None:
        assert (validation.YEAR_MIN, validation.YEAR_MAX) == (YEAR_MIN, YEAR_MAX)

    @pytest.mark.parametrize("year", [YEAR_MAX + 1, 3000])
    def test_rest_refuses_years_the_model_cannot_store(self, year: int) -> None:
        # Request validation runs before any database access: no lifespan needed
        client = TestClient(app)
        response = client.post(
            "/api/v1/analyze/detect-anachronisms",
            json={"text": "The knight rode home", "claimed_date": year, "language": "eng"},
        )
        assert response.status_code == 400
        response = client.get(
            "/api/v1/analyze/contact-events", params={"language": "eng", "date_end": year}
        )
        assert response.status_code == 400


class TestCorpusWordBoundaries:
    def test_combining_marks_stay_in_corpus_words(self, tmp_path: Path) -> None:
        """The corpus splits words like the analyses, so a Hindi corpus word
        matches the tokens of a Hindi text."""
        text = "नमस्ते दुनिया"
        (tmp_path / "doc.txt").write_text(text, encoding="utf-8")
        (tmp_path / "doc.json").write_text(
            '{"date": 1950, "language": "Hindi", "language_code": "hin"}', encoding="utf-8"
        )
        adapter = CorpusAdapter(corpus_dir=tmp_path, language="Hindi")
        adapter.connect()
        try:
            forms = {e.form for e in adapter.fetch_batch(0, 10)}
        finally:
            adapter.disconnect()
        assert forms == {"नमस्ते", "दुनिया"}
        normalized = {LSR(form_orthographic=f, language_code="hin").form_normalized for f in forms}
        assert normalized == set(tokenize(text))


class TestSemanticTrajectoryStatus:
    def test_graphql_says_why_there_are_no_points(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def resolve(db: Any, form: str, language: str) -> dict[str, Any]:
            return {
                "points": [],
                "shift_events": [],
                "status": "insufficient_data",
                "explanation": "all first attested in 1382",
            }

        monkeypatch.setattr(resolvers, "resolve_semantic_trajectory", resolve)
        query = (
            '{ semanticTrajectory(form: "male", language: "eng") '
            "{ status explanation points { date } } }"
        )
        result = asyncio.run(schema.execute(query, context_value={"db": None}))
        assert result.errors is None
        assert result.data == {
            "semanticTrajectory": {
                "status": "insufficient_data",
                "explanation": "all first attested in 1382",
                "points": [],
            }
        }


class TestSentryRelease:
    def test_release_defaults_to_the_code_version(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """compose passes APP_VERSION empty unless it is set."""
        sentry_sdk = pytest.importorskip("sentry_sdk")
        seen: dict[str, Any] = {}
        monkeypatch.setattr(sentry_sdk, "init", lambda **kwargs: seen.update(kwargs))
        monkeypatch.setenv("APP_VERSION", "")
        monkeypatch.setattr(SentryIntegration, "_initialized", False)
        assert SentryIntegration.init(dsn="https://key@example.invalid/1") is True
        assert seen["release"] == src.__version__

    def test_missing_sdk_is_reported_as_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setitem(sys.modules, "sentry_sdk", None)
        monkeypatch.setattr(SentryIntegration, "_initialized", False)
        assert SentryIntegration.init(dsn="https://key@example.invalid/1") is False


class TestComposeSettings:
    def test_graph_query_switch_reaches_the_api(self) -> None:
        compose = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text())
        env = compose["services"]["api"]["environment"]
        assert "GRAPH_QUERY_ENABLED=${GRAPH_QUERY_ENABLED:-}" in env

    @pytest.mark.parametrize("name", [".env.production", ".env.staging"])
    def test_env_templates_do_not_pin_the_version(self, name: str) -> None:
        lines = (REPO_ROOT / "config" / name).read_text().splitlines()
        assert not any(line.startswith("APP_VERSION=") for line in lines)


class TestUndeterminedDonors:
    def test_unidentified_donor_gets_und(self) -> None:
        from src.adapters.base import RawLexicalEntry
        from src.ingestion import IngestionStats, _build_source_relationships

        entry = RawLexicalEntry(
            source_id="wold-1",
            source_name="wold",
            form="tabu",
            language="English",
            language_code="eng",
            related_forms=[{"type": "borrowed_from", "form": "tapu", "language": "Unidentified"}],
        )
        word = LSR(form_orthographic="tabu", language_code="eng")
        store = {word.id: word}
        edges, _ = _build_source_relationships([(entry, word.id)], store, IngestionStats("wold"))
        (edge,) = edges
        donor = store[UUID(edge["target_id"])]
        assert (donor.language_code, donor.language_name) == ("und", "Unidentified")

    def test_undetermined_donors_make_no_contact_event(self) -> None:
        from src.analysis.contact_detection import ContactDetector

        borrowings = [
            {"form": f"w{i}", "source_lang": "und", "target_lang": "eng", "date": 1300}
            for i in range(5)
        ]
        assert ContactDetector(borrowing_data=borrowings).detect_contacts("eng") == []


def test_cli_output_into_a_closed_pipe_is_quiet() -> None:
    """`lexicon ... | head` must not end in a BrokenPipeError traceback."""
    import subprocess

    script = (
        "import sys\n"
        "import src.cli as cli\n"
        "def flood(args):\n"
        "    for _ in range(100000):\n"
        "        print('x' * 80)\n"
        "cli.cmd_stats = flood\n"
        "sys.argv = ['lexicon', 'stats']\n"
        "cli.main()\n"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert proc.stdout is not None and proc.stderr is not None
    proc.stdout.readline()
    proc.stdout.close()
    stderr = proc.stderr.read().decode()
    proc.wait(timeout=60)
    assert "Traceback" not in stderr


class _StalledSession:
    """A Neo4j session whose server never answers."""

    async def run(self, *args: Any, **kwargs: Any) -> Any:
        await asyncio.sleep(3600)

    async def __aenter__(self) -> "_StalledSession":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None


class _StalledDB:
    def neo4j_session(self) -> _StalledSession:
        return _StalledSession()


class TestStalledNeo4j:
    """Reads give up instead of waiting for the driver's socket timeout."""

    def test_analysis_reads_have_a_deadline(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from src.analysis import data_access
        from src.exceptions import DatabaseError

        monkeypatch.setattr(data_access, "QUERY_TIMEOUT_SECONDS", 0.05)
        monkeypatch.setattr(data_access, "_CLIENT_DEADLINE_GRACE_SECONDS", 0.05)
        db: Any = _StalledDB()
        for call in (
            data_access.load_vocabulary(db, "eng", ["sky"]),
            data_access.load_borrowings(db, "eng"),
            data_access.load_trajectory(db, "sky", "eng"),
        ):
            with pytest.raises(DatabaseError, match="timed out"):
                asyncio.run(asyncio.wait_for(call, 5))

    def test_statistics_have_a_deadline(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from src.exceptions import DatabaseError
        from src.repositories import lsr_repository
        from src.repositories.lsr_repository import LSRRepository

        monkeypatch.setattr(lsr_repository, "STATISTICS_TIMEOUT_SECONDS", 0.05)
        monkeypatch.setattr(lsr_repository, "_CLIENT_DEADLINE_GRACE_SECONDS", 0.05)
        repo = LSRRepository(_StalledDB())  # type: ignore[arg-type]
        with pytest.raises(DatabaseError, match="timed out"):
            asyncio.run(asyncio.wait_for(repo.get_statistics(), 5))

    def test_graph_routes_say_timed_out(self) -> None:
        from src.api.routes.graph import _guarded
        from src.exceptions import DatabaseError

        async def times_out() -> None:
            raise TimeoutError

        with pytest.raises(DatabaseError, match="Path finding timed out"):
            asyncio.run(_guarded("Path finding", times_out))


class TestUncodedDonorLanguages:
    def test_same_form_in_two_uncoded_languages_stays_apart(self) -> None:
        from src.adapters.base import RawLexicalEntry
        from src.ingestion import IngestionStats, _build_source_relationships

        entries = []
        store: dict[UUID, LSR] = {}
        for form, donor_language in (("rumi", "Saharan"), ("ruma", "Pre-Rangi")):
            word = LSR(form_orthographic=form, language_code="hau")
            store[word.id] = word
            entry = RawLexicalEntry(
                source_id=f"wold-{form}",
                source_name="wold",
                form=form,
                language="Hausa",
                language_code="hau",
                related_forms=[
                    {"type": "borrowed_from", "form": "kara", "language": donor_language}
                ],
            )
            entries.append((entry, word.id))
        edges, _ = _build_source_relationships(entries, store, IngestionStats("wold"))
        donors = [store[UUID(edge["target_id"])] for edge in edges]
        assert len({donor.id for donor in donors}) == 2
        assert {(d.language_code, d.language_name) for d in donors} == {
            ("und", "Saharan"),
            ("und", "Pre-Rangi"),
        }


class TestMessages:
    def test_one_sense_drift_explanation(self) -> None:
        from src.analysis.semantic_drift import (
            SemanticTrajectory,
            TrajectoryPoint,
            assess_trajectory,
        )

        point = TrajectoryPoint(date=1913, embedding_2d=(0.0, 0.0), embedding_full=[0.1] * 4)
        status, explanation = assess_trajectory(
            SemanticTrajectory(lsr_id=None, form="radio", language="eng", points=[point]),
            "radio",
            "eng",
        )
        assert status == "insufficient_data"
        assert explanation.startswith("Found 1 dated sense with a definition for 'radio'")
        assert "same date" not in explanation

    def test_corpus_sidecar_language_name_sets_its_code(self, tmp_path: Path) -> None:
        (tmp_path / "doc.txt").write_text("le chevalier", encoding="utf-8")
        (tmp_path / "doc.json").write_text('{"date": 1300, "language": "French"}')
        adapter = CorpusAdapter(corpus_dir=tmp_path, language="English")
        adapter.connect()
        try:
            codes = {e.language_code for e in adapter.fetch_batch(0, 10)}
        finally:
            adapter.disconnect()
        assert codes == {"fra"}
