"""Unit tests for the GraphQL schema and resolvers."""

import asyncio
from contextlib import asynccontextmanager

import pytest

from src.api.graphql.schema import schema


class FakeResult:
    """Mimics a Neo4j result cursor."""

    def __init__(self, records):
        self._records = records

    async def fetch(self, n):
        return self._records[:n]

    async def single(self):
        return self._records[0] if self._records else None


class FakeSession:
    """Mimics a Neo4j session with canned per-query records."""

    def __init__(self, records):
        self._records = records

    async def run(self, query, params=None):
        return FakeResult(self._records)


class FakeDB:
    """Mimics DatabaseManager for resolver tests."""

    def __init__(self, records=None, connected=True):
        self._records = records or []
        self._connected = connected

    @asynccontextmanager
    async def neo4j_session(self):
        if not self._connected:
            raise RuntimeError("Neo4j not connected")
        yield FakeSession(self._records)

    def _has_elasticsearch(self):
        return False


def _execute(query, db, variables=None):
    return asyncio.run(schema.execute(query, variable_values=variables, context_value={"db": db}))


class TestGraphQLQueries:
    """Execute GraphQL queries against fake data."""

    def test_languages_query(self):
        records = [
            {
                "iso_code": "eng",
                "name": "English",
                "family": "Indo-European",
                "reconstructed": False,
            }
        ]
        result = _execute("{ languages { isoCode name family isLiving } }", FakeDB(records))
        assert result.errors is None
        assert result.data["languages"] == [
            {"isoCode": "eng", "name": "English", "family": "Indo-European", "isLiving": True}
        ]

    def test_languages_query_db_down(self):
        """Database outage degrades to an empty list, not an error."""
        result = _execute("{ languages { isoCode } }", FakeDB(connected=False))
        assert result.errors is None
        assert result.data["languages"] == []

    def test_language_single_not_found(self):
        result = _execute('{ language(isoCode: "xxx") { isoCode } }', FakeDB([]))
        assert result.errors is None
        assert result.data["language"] is None

    def test_date_text_query(self):
        """dateText runs the real TextDating analyzer over graph data."""
        records = [
            {
                "form": "computer",
                "date_start": 1940,
                "date_end": 2020,
                "language_code": "eng",
                "definition": "an electronic device",
            }
        ]
        result = _execute(
            '{ dateText(text: "the computer works", language: "eng") '
            "{ predictedRange confidence diagnosticVocabulary { form } } }",
            FakeDB(records),
        )
        assert result.errors is None
        data = result.data["dateText"]
        assert data["predictedRange"] == [1940, 2020]
        assert data["confidence"] > 0
        assert data["diagnosticVocabulary"][0]["form"] == "computer"

    def test_detect_anachronisms_query(self):
        """detectAnachronisms flags vocabulary newer than the claimed date."""
        records = [
            {
                "form": "computer",
                "date_start": 1940,
                "date_end": 2020,
                "language_code": "eng",
                "definition": "an electronic device",
            }
        ]
        result = _execute(
            '{ detectAnachronisms(text: "the knight used a computer", '
            'claimedDate: 1300, language: "eng") '
            "{ verdict anachronisms { form earliestAttestation severity } } }",
            FakeDB(records),
        )
        assert result.errors is None
        data = result.data["detectAnachronisms"]
        assert data["anachronisms"][0]["form"] == "computer"
        assert data["anachronisms"][0]["earliestAttestation"] == 1940
        assert data["anachronisms"][0]["severity"] == "high"

    def test_lsr_query_invalid_id(self):
        """A malformed UUID resolves to null rather than erroring."""
        result = _execute('{ lsr(id: "not-a-uuid") { form } }', FakeDB([]))
        assert result.errors is None
        assert result.data["lsr"] is None

    def test_schema_has_documented_fields(self):
        """The README-advertised LSR fields exist on the schema."""
        sdl = schema.as_str()
        assert "ancestors" in sdl
        assert "cognates" in sdl
        assert "descendants" in sdl
        assert "searchLsr" in sdl
        assert "semanticTrajectory" in sdl


class TestGraphQLMounted:
    """The GraphQL endpoint is mounted in the FastAPI app."""

    @pytest.fixture()
    def client(self):
        from fastapi.testclient import TestClient

        from src.api.main import app

        return TestClient(app)

    def test_graphql_route_registered(self, client):
        routes = [getattr(r, "path", "") for r in client.app.routes]
        assert any(path.startswith("/graphql") for path in routes)

    def test_playground_served(self, client):
        response = client.get("/graphql", headers={"accept": "text/html"})
        assert response.status_code == 200
        assert "text/html" in response.headers.get("content-type", "")

    def test_introspection_query(self, client):
        response = client.post("/graphql", json={"query": "{ __schema { queryType { name } } }"})
        assert response.status_code == 200
        assert response.json()["data"]["__schema"]["queryType"]["name"] == "Query"
