"""Unit tests for the GraphQL schema and resolvers."""

import asyncio
import json
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from neo4j.exceptions import ServiceUnavailable

from src.api.graphql import resolvers
from src.api.graphql.schema import MAX_ALIASES, MAX_QUERY_DEPTH, MAX_TRAVERSALS, schema

SECRET = "secret-host-10.0.0.7"
# A fixed id for parametrized queries: the parameters are part of the test ids,
# which must be the same in every collection (pytest -n, re-runs by node id)
LSR_ID = "5b7e0c1a-3f2d-4c6b-9a8e-1d2f3a4b5c6d"


class FakeResult:
    """Mimics a Neo4j result cursor."""

    def __init__(self, records):
        self._records = records

    async def fetch(self, n):
        return self._records[:n]

    async def single(self):
        return self._records[0] if self._records else None

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for record in self._records:
            yield record


class FakeSession:
    """Mimics a Neo4j session with canned per-query records."""

    def __init__(self, records, error=None):
        self._records = records
        self._error = error

    async def run(self, query, params=None):
        if self._error is not None:
            raise self._error
        return FakeResult(self._records)


class FakeDB:
    """Mimics DatabaseManager for resolver tests.

    connected=False: no driver (neo4j_session raises RuntimeError);
    error: every query fails with it, as when Neo4j goes away mid-request.
    """

    def __init__(self, records=None, connected=True, error=None):
        self._records = records or []
        self._connected = connected
        self._error = error

    @asynccontextmanager
    async def neo4j_session(self):
        if not self._connected:
            raise RuntimeError(f"Neo4j not connected {SECRET}")
        yield FakeSession(self._records, self._error)

    def _has_elasticsearch(self):
        return False


def _execute(query, db, variables=None):
    return asyncio.run(schema.execute(query, variable_values=variables, context_value={"db": db}))


def _language_row(code, name, family=None, lsrs=1, reconstructed=0):
    return {
        "iso_code": code,
        "name": name,
        "family": family,
        "lsrs": lsrs,
        "reconstructed": reconstructed,
    }


class TestGraphQLQueries:
    """Execute GraphQL queries against fake data."""

    def test_languages_query(self):
        records = [
            _language_row("eng", "English", "Indo-European", lsrs=5),
            _language_row("eng", None, None, lsrs=7),
            _language_row("eng", "eng", None, lsrs=9),
            _language_row("grc", "Ancient Greek", "Indo-European", lsrs=2),
        ]
        result = _execute("{ languages { isoCode name family isLiving } }", FakeDB(records))
        assert result.errors is None
        assert result.data["languages"] == [
            {"isoCode": "eng", "name": "English", "family": "Indo-European", "isLiving": None},
            {
                "isoCode": "grc",
                "name": "Ancient Greek",
                "family": "Indo-European",
                "isLiving": None,
            },
        ]

    def test_languages_one_entry_per_code_with_most_frequent_name(self):
        """LSRs of one code with different names give one language (D4-15)."""
        records = [
            _language_row("deu", "German", "Germanic", lsrs=10),
            _language_row("deu", "Standard German", "Indo-European", lsrs=3),
            _language_row("deu", "", None, lsrs=50),
            # REST-created LSRs have no name: the code table names the language
            _language_row("nld", "", None, lsrs=4),
            _language_row("zzq", None, None, lsrs=1),
        ]
        result = _execute("{ languages { isoCode name family } }", FakeDB(records))
        assert result.errors is None
        assert result.data["languages"] == [
            {"isoCode": "deu", "name": "German", "family": "Germanic"},
            {"isoCode": "nld", "name": "Dutch", "family": None},
            {"isoCode": "zzq", "name": "zzq", "family": None},
        ]

    def test_is_living_is_only_known_for_proto_languages(self):
        records = [
            _language_row("gem-pro", "Proto-Germanic", lsrs=3, reconstructed=3),
            _language_row("ine", "Proto-Indo-European", lsrs=2, reconstructed=2),
            _language_row("lat", "Latin", lsrs=4, reconstructed=1),
        ]
        result = _execute("{ languages { isoCode isLiving } }", FakeDB(records))
        assert result.errors is None
        assert result.data["languages"] == [
            {"isoCode": "gem-pro", "isLiving": False},
            {"isoCode": "ine", "isLiving": False},
            {"isoCode": "lat", "isLiving": None},
        ]

    def test_languages_family_filter_uses_the_chosen_family(self):
        records = [
            _language_row("eng", "English", "Indo-European", lsrs=5),
            _language_row("eng", "English", "Germanic", lsrs=1),
            _language_row("jpn", "Japanese", "Japonic", lsrs=5),
        ]
        result = _execute('{ languages(family: "Indo-European") { isoCode } }', FakeDB(records))
        assert result.errors is None
        assert result.data["languages"] == [{"isoCode": "eng"}]

    def test_language_single(self):
        records = [
            _language_row("deu", "deu", None, lsrs=2),
            _language_row("deu", "German", "Indo-European", lsrs=1),
        ]
        result = _execute('{ language(isoCode: "deu") { isoCode name family } }', FakeDB(records))
        assert result.errors is None
        assert result.data["language"] == {
            "isoCode": "deu",
            "name": "German",
            "family": "Indo-European",
        }

    def test_language_single_not_found(self):
        result = _execute('{ language(isoCode: "xxx") { isoCode } }', FakeDB([]))
        assert result.errors is None
        assert result.data["language"] is None

    def test_language_with_empty_code_is_an_error(self):
        """An empty code must not fall through to the unfiltered language list."""
        records = [_language_row("afro1255", "Afro-Asiatic")]
        result = _execute('{ language(isoCode: "") { isoCode } }', FakeDB(records))
        assert result.errors[0].extensions["code"] == "INVALID_LANGUAGE_CODE"
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

    def test_lsr_not_found_is_null_without_error(self):
        result = _execute(f'{{ lsr(id: "{uuid4()}") {{ form }} }}', FakeDB([]))
        assert result.errors is None
        assert result.data["lsr"] is None

    @pytest.mark.parametrize(("code", "living"), [("hun", None), ("gem-pro", False)])
    def test_reconstructed_form_does_not_make_its_language_extinct(self, monkeypatch, code, living):
        """LSR.language agrees with Query.language: one reconstructed form of
        a language (WOLD has some in Hungarian) says nothing about the language."""

        async def get_by_id(self, lsr_id):
            from src.models.lsr import LSR

            return LSR(
                id=lsr_id, form_orthographic="csiboka", language_code=code, reconstruction_flag=True
            )

        monkeypatch.setattr(resolvers.LSRRepository, "get_by_id", get_by_id)
        query = f'{{ lsr(id: "{uuid4()}") {{ isReconstructed language {{ isLiving }} }} }}'
        result = _execute(query, FakeDB([]))
        assert result.errors is None
        assert result.data["lsr"] == {"isReconstructed": True, "language": {"isLiving": living}}

    def test_etymology_not_found_is_null(self):
        for lsr_id in (uuid4(), "not-a-uuid"):
            result = _execute(f'{{ etymology(lsrId: "{lsr_id}") {{ depth }} }}', FakeDB([]))
            assert result.errors is None
            assert result.data["etymology"] is None

    def test_schema_has_documented_fields(self):
        """The README-advertised LSR fields exist on the schema."""
        sdl = schema.as_str()
        assert "ancestors" in sdl
        assert "cognates" in sdl
        assert "descendants" in sdl
        assert "searchLsr" in sdl
        assert "semanticTrajectory" in sdl


_OUTAGE_QUERIES = {
    "lsr": f'{{ lsr(id: "{uuid4()}") {{ form }} }}',
    "searchLsr": '{ searchLsr(language: "eng") { form } }',
    "languages": "{ languages { isoCode } }",
    "language": '{ language(isoCode: "eng") { name } }',
    "etymology": f'{{ etymology(lsrId: "{uuid4()}") {{ depth }} }}',
    "dateText": '{ dateText(text: "the computer", language: "eng") { status } }',
    "detectAnachronisms": (
        '{ detectAnachronisms(text: "the knight used a computer", claimedDate: 1300, '
        'language: "eng") { verdict } }'
    ),
    "semanticTrajectory": '{ semanticTrajectory(form: "gay", language: "eng") { points { date } } }',
}


class TestGraphQLDatabaseErrors:
    """A Neo4j outage is a GraphQL error, never null/[]/"consistent" (D4-05)."""

    @pytest.mark.parametrize(
        "db",
        [
            FakeDB(connected=False),
            FakeDB(error=ServiceUnavailable(f"defunct connection to {SECRET}:7687")),
        ],
        ids=["never-connected", "went-away"],
    )
    @pytest.mark.parametrize("field", sorted(_OUTAGE_QUERIES))
    def test_outage_is_reported_as_error(self, db, field):
        result = _execute(_OUTAGE_QUERIES[field], db)
        assert result.errors, f"{field} hid the outage: {result.data}"
        error = result.errors[0]
        assert error.extensions == {"code": "DATABASE_ERROR"}
        assert error.path == [field]
        assert SECRET not in json.dumps([e.formatted for e in result.errors])
        # The failed field is null (the root, for non-nullable fields)
        assert result.data is None or result.data[field] is None

    def test_outage_in_nested_field_nulls_its_parent(self, monkeypatch):
        async def get_by_id(self, lsr_id):
            from src.models.lsr import LSR

            return LSR(id=lsr_id, form_orthographic="water", language_code="eng")

        monkeypatch.setattr(resolvers.LSRRepository, "get_by_id", get_by_id)
        db = FakeDB(error=ServiceUnavailable(SECRET))
        result = _execute(f'{{ lsr(id: "{uuid4()}") {{ form cognates {{ form }} }} }}', db)
        assert result.data == {"lsr": None}
        assert [e.path for e in result.errors] == [["lsr", "cognates"]]
        assert result.errors[0].extensions == {"code": "DATABASE_ERROR"}

    def test_unexpected_error_is_masked(self, monkeypatch):
        async def boom(db, lsr_id):
            raise ValueError(f"bug with {SECRET}")

        monkeypatch.setattr(resolvers, "resolve_lsr", boom)
        result = _execute(f'{{ lsr(id: "{uuid4()}") {{ form }} }}', FakeDB([]))
        assert result.errors[0].message == "Internal server error"
        assert result.errors[0].extensions == {"code": "INTERNAL_ERROR"}
        assert SECRET not in json.dumps([e.formatted for e in result.errors])


def _etymology_query(*fields):
    """An etymology query nesting the given LSR fields under protoForm."""
    inner = "language { name }"
    for name in reversed(fields):
        inner = f"{name} {{ {inner} }}"
    return f'{{ etymology(lsrId: "{uuid4()}") {{ protoForm {{ {inner} }} }} }}'


class TestGraphQLLimits:
    """Depth, alias and traversal limits (D4-18)."""

    def _errors(self, query):
        result = _execute(query, FakeDB([]))
        return [e.message for e in result.errors or []]

    def test_depth_limit(self):
        assert MAX_QUERY_DEPTH == 5
        assert self._errors(_etymology_query("ancestors", "descendants")) == []
        errors = self._errors(_etymology_query("ancestors", "descendants", "ancestors"))
        assert any("exceeds maximum operation depth of 5" in e for e in errors)

    def test_readme_query_is_allowed(self):
        query = (
            f'{{ lsr(id: "{uuid4()}") {{ form language {{ name }} '
            "ancestors { form } cognates { form language { name } } } }"
        )
        assert self._errors(query) == []

    def test_introspection_is_not_depth_limited(self):
        from graphql import get_introspection_query

        assert self._errors(get_introspection_query()) == []

    def test_alias_limit(self):
        def aliased(n):
            return (
                "{ "
                + " ".join(f'a{i}: language(isoCode: "eng") {{ name }}' for i in range(n))
                + " }"
            )

        assert self._errors(aliased(MAX_ALIASES)) == []
        errors = self._errors(aliased(MAX_ALIASES + 1))
        assert errors and "aliases found" in errors[0]

    @pytest.mark.parametrize(
        "query",
        [
            "{ searchLsr(limit: 100) { descendants { cognates { form } } } }",
            f'{{ lsr(id: "{LSR_ID}") {{ cognates {{ cognates {{ cognates {{ form }} }} }} }} }}',
            # The same through fragments and a variable limit
            "query($n: Int!) { searchLsr(limit: $n) { ...D } } "
            "fragment D on LSR { descendants { ... on LSR { cognates { form } } } }",
        ],
    )
    def test_traversal_limit_rejects_fan_out(self, query):
        result = _execute(query, FakeDB([]), variables={"n": 100})
        assert result.errors and result.data is None
        assert result.errors[0].extensions["code"] == "QUERY_TOO_COMPLEX"
        assert f"limit {MAX_TRAVERSALS}" in result.errors[0].message

    @pytest.mark.parametrize(
        "query",
        [
            "{ searchLsr(limit: 100) { descendants { form } cognates { form } } }",
            f'{{ lsr(id: "{LSR_ID}") {{ descendants {{ cognates {{ form }} }} }} }}',
            f'{{ etymology(lsrId: "{LSR_ID}") {{ steps {{ lsr {{ cognates {{ form }} }} }} }} }}',
        ],
    )
    def test_traversal_limit_allows_bounded_queries(self, query):
        assert self._errors(query) == []


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


class TestGraphQLInputValidation:
    """GraphQL applies the same input rules as REST."""

    def _records_for(self, query):
        db = FakeDB([])
        return _execute(query, db)

    def test_iso_639_1_language_is_mapped(self):
        records = [
            {"form": "computer", "date_start": 1646, "date_end": None, "language_code": "eng"}
        ]
        result = _execute(
            '{ detectAnachronisms(text: "the knight used a computer", claimedDate: 1300, '
            'language: "en") { verdict } }',
            FakeDB(records),
        )
        assert result.errors is None
        assert result.data["detectAnachronisms"]["verdict"] in ("suspicious", "anachronistic")

    @pytest.mark.parametrize(
        ("query", "code"),
        [
            (
                '{ dateText(text: "the knight rode forth", language: "english") { status } }',
                "INVALID_LANGUAGE_CODE",
            ),
            ('{ dateText(text: "short", language: "eng") { status } }', "VALIDATION_ERROR"),
            (
                '{ detectAnachronisms(text: "the knight rode forth", claimedDate: 99999, '
                'language: "eng") { verdict } }',
                "VALIDATION_ERROR",
            ),
            ("{ searchLsr(dateStart: 1500, dateEnd: 1400) { form } }", "INVALID_DATE_RANGE"),
        ],
    )
    def test_invalid_input_is_an_error(self, query, code):
        result = self._records_for(query)
        assert result.errors
        assert result.errors[0].extensions["code"] == code
