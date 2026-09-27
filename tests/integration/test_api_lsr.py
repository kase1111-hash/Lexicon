"""API tests for the LSR routes against a live Neo4j.

Fixtures are written through LSRRepository (create, create_batch,
create_relationships_batch), as ingestion does, so nodes and edges carry
Neo4j DateTime properties like real data. Every fixture row is tagged with a
unique semantic field, which isolates it in searches from whatever else the
configured graph holds, and is deleted afterwards.

Run with a reachable Neo4j (NEO4J_URI / NEO4J_PASSWORD); the Elasticsearch
parity tests also need ELASTICSEARCH_URI to point at a reachable cluster.
"""

import contextlib
import time
from collections.abc import Awaitable, Callable, Iterator
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

import src.utils.db as db_module
from src.api.main import app
from src.models.lsr import LSR
from src.repositories.lsr_repository import ES_INDEX_NAME, LSRRepository

SEARCH = "/api/v1/lsr/search"


def _neo4j_available() -> bool:
    """Check whether the configured Neo4j accepts us."""
    from neo4j import GraphDatabase

    config = db_module.DatabaseConfig()
    try:
        driver = GraphDatabase.driver(
            config.neo4j_uri, auth=(config.neo4j_user, config.neo4j_password)
        )
        try:
            driver.verify_connectivity()
        finally:
            driver.close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _neo4j_available(),
    reason="requires a reachable Neo4j (start with `docker compose up -d neo4j`)",
)


@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    """One app lifespan (and event loop) for the whole module."""
    db_module._db_manager = None  # never reuse a manager bound to another test's loop
    with TestClient(app) as test_client:
        yield test_client


def _run(client: TestClient, fn: Callable[[LSRRepository], Awaitable[Any]]) -> Any:
    """Run fn(repository) on the app's event loop, with the app's connections."""

    async def call() -> Any:
        return await fn(LSRRepository(await db_module.get_db()))

    assert client.portal is not None
    return client.portal.call(call)


def _delete_all(client: TestClient, ids: list[str]) -> None:
    async def delete(repo: LSRRepository) -> None:
        for lsr_id in ids:
            with contextlib.suppress(Exception):
                await repo.delete(UUID(lsr_id))

    _run(client, delete)


@contextlib.contextmanager
def _lineage(
    client: TestClient, nodes: list[LSR], edges: list[tuple[LSR, LSR, str]]
) -> Iterator[None]:
    """Write nodes and (source, target, type) edges as ingestion does; delete
    them afterwards in bulk (one repo.delete per node is slow for big lineages)."""
    ids = [str(lsr.id) for lsr in nodes]

    async def seed(repo: LSRRepository) -> None:
        assert (await repo.create_batch(nodes)).failed == 0
        rels = [{"source_id": str(s.id), "target_id": str(t.id), "type": r} for s, t, r in edges]
        assert (await repo.create_relationships_batch(rels)).failed == 0

    async def drop(repo: LSRRepository) -> None:
        async with repo.db.neo4j_session() as session:
            await session.run(
                "UNWIND $ids AS id MATCH (l:LSR {id: id}) DETACH DELETE l", {"ids": ids}
            )
        if repo._has_elasticsearch():
            with contextlib.suppress(Exception):
                await repo.db.elasticsearch.delete_by_query(
                    index=ES_INDEX_NAME, query={"ids": {"values": ids}}, refresh=True
                )

    try:
        _run(client, seed)
        yield
    finally:
        _run(client, drop)


# name: (form, language, date_start, date_end)
FAMILY = {
    "pie": ("nókʷts", "ine-pro", -4000, -2500),
    "pgm": ("nahts", "gem-pro", -500, 200),
    "ang": ("niht", "ang", 700, 1150),
    "enm": ("night", "enm", 1150, 1500),
    "eng": ("night", "eng", 1500, None),
    "deu": ("Nacht", "deu", 1500, None),
    "lat": ("nox", "lat", -200, 600),
    "fra": ("nuit", "fra", 1100, None),
    "noct": ("nocturnal", "eng", 1485, None),
    "nld": ("nacht", "nld", 1200, None),
    "undated": ("nightjar", "eng", None, None),
}

EDGES = [
    ("pgm", "pie", "DESCENDS_FROM", 1.0),
    ("ang", "pgm", "DESCENDS_FROM", 1.0),
    ("enm", "ang", "DESCENDS_FROM", 1.0),
    ("eng", "enm", "DESCENDS_FROM", 1.0),
    ("deu", "pgm", "DESCENDS_FROM", 1.0),
    ("lat", "pie", "DESCENDS_FROM", 1.0),
    ("fra", "lat", "DESCENDS_FROM", 1.0),
    ("noct", "lat", "BORROWED_FROM", 0.9),
    ("noct", "fra", "BORROWED_FROM", 0.5),
    ("eng", "nld", "COGNATE_OF", 0.8),
]


@pytest.fixture(scope="module")
def graph(client: TestClient) -> Iterator[SimpleNamespace]:
    """The 'night' family: two branches of PIE *nókʷts, a loan and a COGNATE_OF edge."""
    tag = f"test-field-{uuid4().hex[:12]}"
    lsrs = {
        name: LSR(
            form_orthographic=form,
            language_code=lang,
            date_start=start,
            date_end=end,
            semantic_fields=[tag],
            source_databases=["test"],
        )
        for name, (form, lang, start, end) in FAMILY.items()
    }
    ids = {name: str(lsr.id) for name, lsr in lsrs.items()}

    async def seed(repo: LSRRepository) -> None:
        for lsr in lsrs.values():
            await repo.create(lsr)
        result = await repo.create_relationships_batch(
            [
                {"source_id": ids[s], "target_id": ids[t], "type": rel, "confidence": conf}
                for s, t, rel, conf in EDGES
            ]
        )
        assert (result.succeeded, result.failed) == (len(EDGES), 0)
        # Form searches go to Elasticsearch when it is connected; make the
        # new documents visible now rather than after the refresh interval.
        if repo._has_elasticsearch():
            await repo.db.elasticsearch.indices.refresh(index=ES_INDEX_NAME)

    _run(client, seed)
    yield SimpleNamespace(ids=ids, tag=tag, name={v: k for k, v in ids.items()})
    _delete_all(client, list(ids.values()))


def _search(client: TestClient, graph: SimpleNamespace, **params: Any) -> dict:
    response = client.get(SEARCH, params={"semantic_field": graph.tag, "limit": 100, **params})
    assert response.status_code == 200, response.text
    return response.json()


def _names(graph: SimpleNamespace, rows: list[dict]) -> set[str]:
    return {graph.name[row["id"]] for row in rows}


# =============================================================================
# Search
# =============================================================================


class TestSearchFilters:
    """D3-08, D3-11, D3-25: filters apply exactly as documented."""

    def test_semantic_field_filter_matches(self, client, graph):
        data = _search(client, graph)
        assert data["total"] == len(FAMILY)
        assert _names(graph, data["results"]) == set(FAMILY)
        assert all(graph.tag in row["semantic_fields"] for row in data["results"])

    def test_iso639_1_language_is_applied(self, client, graph):
        data = _search(client, graph, language="en")
        assert data["filters"]["language"] == "eng"
        assert _names(graph, data["results"]) == {"eng", "noct", "undated"}

    def test_hyphenated_language_code(self, client, graph):
        data = _search(client, graph, language="gem-pro")
        assert _names(graph, data["results"]) == {"pgm"}

    @pytest.mark.parametrize("code", ["e1n2g3", "english", "en-gb", "12"])
    def test_invalid_language_is_rejected(self, client, code):
        response = client.get(SEARCH, params={"language": code})
        assert response.status_code == 400
        assert response.json()["error"] == "INVALID_LANGUAGE_CODE"

    @pytest.mark.parametrize(
        "dates, expected",
        [
            # in use at some point in the range (overlap), not contained in it
            ({"date_start": 1400, "date_end": 1600}, {"enm", "eng", "deu", "fra", "noct", "nld"}),
            ({"date_start": 1000, "date_end": 1100}, {"ang", "fra"}),
            ({"date_end": 0}, {"pie", "pgm", "lat"}),
            # a null date_end means still in use
            ({"date_start": 1550}, {"eng", "deu", "fra", "noct", "nld"}),
            ({"date_start": 1500, "date_end": 1500}, {"enm", "eng", "deu", "fra", "noct", "nld"}),
        ],
    )
    def test_date_range_overlap(self, client, graph, dates, expected):
        data = _search(client, graph, **dates)
        assert _names(graph, data["results"]) == expected
        assert data["total"] == len(expected)
        assert "undated" not in _names(graph, data["results"])

    def test_form_search_is_case_and_diacritic_insensitive(self, client, graph):
        assert _names(graph, _search(client, graph, form="NACHT")["results"]) == {"deu", "nld"}
        assert _names(graph, _search(client, graph, form="nokʷ")["results"]) == {"pie"}

    def test_results_carry_relationship_ids(self, client, graph):
        data = _search(client, graph, form="night", language="eng")
        (row,) = [r for r in data["results"] if r["id"] == graph.ids["eng"]]
        assert row["ancestor_ids"] == [graph.ids["enm"]]
        assert row["cognate_ids"] == [graph.ids["nld"]]


class TestSearchPagination:
    """D3-10: paging never repeats or skips records, even on ties."""

    def test_pages_are_disjoint_and_complete(self, client, graph):
        # All rows tie on confidence; eng/enm "night" also tie on form.
        pages = [
            _search(client, graph, limit=1, offset=offset)["results"]
            for offset in range(len(FAMILY) + 1)
        ]
        ids = [row["id"] for page in pages for row in page]
        assert pages[-1] == []
        assert len(ids) == len(set(ids)) == len(FAMILY)
        assert set(ids) == set(graph.ids.values())

    def test_order_is_stable(self, client, graph):
        first = [r["id"] for r in _search(client, graph, limit=4, offset=2)["results"]]
        second = [r["id"] for r in _search(client, graph, limit=4, offset=2)["results"]]
        assert first == second


# =============================================================================
# GET /lsr/{id}
# =============================================================================


class TestGetLSR:
    """D3-16: stored timestamps and relationship ids."""

    def _get(self, client, lsr_id: str) -> dict:
        response = client.get(f"/api/v1/lsr/{lsr_id}")
        assert response.status_code == 200, response.text
        return response.json()["data"]

    def test_timestamps_are_the_stored_ones(self, client, graph):
        first = self._get(client, graph.ids["eng"])
        time.sleep(0.01)
        second = self._get(client, graph.ids["eng"])
        assert first["created_at"] == second["created_at"]
        assert first["updated_at"] == second["updated_at"]
        created = datetime.fromisoformat(first["created_at"].replace("Z", "+00:00"))
        assert created.tzinfo is not None
        assert created <= datetime.now(UTC)

    def test_lineage_ids(self, client, graph):
        ids = graph.ids
        eng = self._get(client, ids["eng"])
        assert eng["ancestor_ids"] == [ids["enm"]]
        assert eng["descendant_ids"] == []
        assert eng["cognate_ids"] == [ids["nld"]]
        assert eng["loan_source_id"] is None

        lat = self._get(client, ids["lat"])
        assert lat["ancestor_ids"] == [ids["pie"]]
        assert lat["descendant_ids"] == [ids["fra"]]
        assert lat["loan_target_ids"] == [ids["noct"]]

        pie = self._get(client, ids["pie"])
        assert pie["descendant_ids"] == sorted([ids["pgm"], ids["lat"]])

    def test_loan_source_is_most_confident_donor(self, client, graph):
        assert self._get(client, graph.ids["noct"])["loan_source_id"] == graph.ids["lat"]


# =============================================================================
# Lineage traversals
# =============================================================================


class TestCognates:
    """D3-09: cognates share an ancestor but are not in the word's own lineage."""

    def _cognates(self, client, graph, name: str) -> dict:
        response = client.get(f"/api/v1/lsr/{graph.ids[name]}/cognates")
        assert response.status_code == 200, response.text
        return response.json()

    def test_excludes_ancestors_and_same_language(self, client, graph):
        data = self._cognates(client, graph, "eng")
        # deu/lat/fra share *nókʷts; nld via COGNATE_OF. Not enm/ang/pgm/pie
        # (its own ancestors) and not the loanword nocturnal (same language).
        assert _names(graph, data["cognates"]) == {"deu", "lat", "fra", "nld"}
        assert data["cognate_count"] == 4
        assert sorted(data["languages"]) == ["deu", "fra", "lat", "nld"]
        assert sum(len(v) for v in data["by_language"].values()) == 4

    def test_excludes_descendants(self, client, graph):
        data = self._cognates(client, graph, "ang")
        assert _names(graph, data["cognates"]) == {"deu", "lat", "fra"}

    def test_other_branch(self, client, graph):
        data = self._cognates(client, graph, "fra")
        assert _names(graph, data["cognates"]) == {"pgm", "ang", "enm", "eng", "deu"}

    def test_cognate_of_edge_without_ancestors(self, client, graph):
        data = self._cognates(client, graph, "nld")
        assert _names(graph, data["cognates"]) == {"eng"}

    def test_word_with_many_descendants(self, client):
        """The ancestor's other lines are cognates; the word's own many
        descendants (which sort first) are not. The original query carried
        the lineage lists through a DISTINCT and ran out of transaction
        memory on lineages like this one, only larger."""
        root = LSR(form_orthographic="wideroot", language_code="qla")
        word = LSR(form_orthographic="wideword", language_code="qlb")
        own = [LSR(form_orthographic=f"wideown{i}", language_code="qlc") for i in range(500)]
        other = [LSR(form_orthographic=f"wideother{i}", language_code="qld") for i in range(1500)]
        edges = [(word, root, "DESCENDS_FROM")]
        edges += [(lsr, word, "DESCENDS_FROM") for lsr in own]
        edges += [(lsr, root, "DESCENDS_FROM") for lsr in other]

        with _lineage(client, [root, word, *own, *other], edges):
            response = client.get(f"/api/v1/lsr/{word.id}/cognates")
            assert response.status_code == 200, response.text
            data = response.json()
            assert data["cognate_count"] == 100  # the endpoint's cap
            assert data["languages"] == ["qld"]


class TestEtymology:
    """D3-24: bounded, polynomial etymology chains."""

    def test_chain_to_proto_form(self, client, graph):
        response = client.get(f"/api/v1/lsr/{graph.ids['eng']}/etymology")
        assert response.status_code == 200
        data = response.json()
        assert [graph.name[link["id"]] for link in data["chain"]] == [
            "eng",
            "enm",
            "ang",
            "pgm",
            "pie",
        ]
        assert data["proto_form"]["id"] == graph.ids["pie"]
        assert data["proto_form"]["form"] == "nókʷts"
        assert data["depth"] == 4
        assert data["truncated"] is False

    def test_max_depth_truncates(self, client, graph):
        response = client.get(f"/api/v1/lsr/{graph.ids['eng']}/etymology", params={"max_depth": 2})
        assert response.status_code == 200
        data = response.json()
        assert [graph.name[link["id"]] for link in data["chain"]] == ["eng", "enm", "ang"]
        assert data["truncated"] is True
        assert data["proto_form"] is None

    @pytest.mark.parametrize(
        "max_depth, chain, truncated",
        [(1, None, True), (2, "abc", True), (3, "abcd", False), (4, "abcd", False)],
    )
    def test_shallow_root_does_not_hide_a_cut_line(self, client, max_depth, chain, truncated):
        """a descends from b (whose line continues to c and the root d) and
        from the root s. While max_depth cuts the line through b, finding
        the root s must not make the chain look complete."""
        lsrs = {k: LSR(form_orthographic=f"cut{k}", language_code="qla") for k in "abcds"}
        edges = [(lsrs[s], lsrs[t], "DESCENDS_FROM") for s, t in ("ab", "bc", "cd", "as")]
        name = {str(lsr.id): k for k, lsr in lsrs.items()}

        with _lineage(client, list(lsrs.values()), edges):
            response = client.get(
                f"/api/v1/lsr/{lsrs['a'].id}/etymology", params={"max_depth": max_depth}
            )
            assert response.status_code == 200
            data = response.json()
            assert data["truncated"] is truncated
            if chain is not None:
                assert "".join(name[link["id"]] for link in data["chain"]) == chain
            if truncated:
                assert data["proto_form"] is None
            else:
                assert data["proto_form"]["id"] == str(lsrs["d"].id)

    @pytest.mark.parametrize("max_depth", [0, 51])
    def test_max_depth_bounds(self, client, graph, max_depth):
        response = client.get(
            f"/api/v1/lsr/{graph.ids['eng']}/etymology", params={"max_depth": max_depth}
        )
        assert response.status_code == 400

    def test_duplicated_generations_stay_fast(self, client):
        """A root plus 25 generations of two nodes, each descending from both
        nodes of the previous generation: 2**25 root paths from a leaf."""
        generations = 25
        tag = f"test-dag-{uuid4().hex[:12]}"
        root = LSR(form_orthographic="dagroot", language_code="qla", semantic_fields=[tag])
        levels = [[root]] + [
            [
                LSR(form_orthographic=f"dag{g}n{k}", language_code=code, semantic_fields=[tag])
                for k, code in enumerate(("qla", "qlb"))
            ]
            for g in range(1, generations + 1)
        ]
        nodes = [lsr for level in levels for lsr in level]
        edges = [
            {"source_id": str(child.id), "target_id": str(parent.id), "type": "DESCENDS_FROM"}
            for g in range(1, generations + 1)
            for child in levels[g]
            for parent in levels[g - 1]
        ]

        async def seed(repo: LSRRepository) -> None:
            assert (await repo.create_batch(nodes)).failed == 0
            assert (await repo.create_relationships_batch(edges)).failed == 0

        _run(client, seed)
        leaf = levels[-1][0]
        try:
            started = time.monotonic()
            response = client.get(f"/api/v1/lsr/{leaf.id}/etymology", params={"max_depth": 50})
            cognates = client.get(f"/api/v1/lsr/{leaf.id}/cognates")
            elapsed = time.monotonic() - started

            assert response.status_code == 200
            data = response.json()
            assert data["depth"] == generations
            assert data["proto_form"]["id"] == str(root.id)
            assert data["truncated"] is False
            # the leaf's sibling shares its ancestors and is in another language
            assert cognates.status_code == 200
            assert [c["id"] for c in cognates.json()["cognates"]] == [str(levels[-1][1].id)]
            assert elapsed < 10, f"lineage queries took {elapsed:.1f}s"
        finally:
            _delete_all(client, [str(lsr.id) for lsr in nodes])


class TestDescendantsAndBorrowings:
    """Traversals over repository-written nodes and edges serialise cleanly."""

    def test_descendants(self, client, graph):
        pie = graph.ids["pie"]
        data = client.get(f"/api/v1/lsr/{pie}/descendants", params={"depth": 10}).json()
        assert _names(graph, data["descendants"]) == {
            "pgm",
            "ang",
            "enm",
            "eng",
            "deu",
            "lat",
            "fra",
        }
        assert data["count"] == 7
        data = client.get(f"/api/v1/lsr/{pie}/descendants", params={"depth": 1}).json()
        assert _names(graph, data["descendants"]) == {"pgm", "lat"}

    def test_borrowings(self, client, graph):
        data = client.get(f"/api/v1/lsr/{graph.ids['noct']}/borrowings").json()
        assert [graph.name[d["id"]] for d in data["borrowed_from"]] == ["lat", "fra"]
        assert [d["confidence"] for d in data["borrowed_from"]] == [0.9, 0.5]
        assert data["borrowed_to"] == []
        data = client.get(f"/api/v1/lsr/{graph.ids['lat']}/borrowings").json()
        assert [graph.name[d["id"]] for d in data["borrowed_to"]] == ["noct"]


# =============================================================================
# Create
# =============================================================================


class TestCreateDuplicates:
    """D3-23: the same form, language and date_start cannot be created twice."""

    @pytest.fixture
    def created(self, client) -> Iterator[list[str]]:
        ids: list[str] = []
        yield ids
        _delete_all(client, ids)

    def _post(self, client, created: list[str], **body: Any):
        response = client.post("/api/v1/lsr/", json=body)
        if response.status_code == 201:
            created.append(response.json()["data"]["id"])
        return response

    def test_duplicate_is_rejected(self, client, created):
        form = f"dupform{uuid4().hex[:10]}"
        first = self._post(
            client, created, form_orthographic=form, language_code="eng", date_start=1700
        )
        assert first.status_code == 201
        again = self._post(
            client, created, form_orthographic=form.upper(), language_code="eng", date_start=1700
        )
        assert again.status_code == 409
        body = again.json()
        assert body["error"] == "DUPLICATE_ERROR"
        assert body["details"]["identifier"] == first.json()["data"]["id"]

    def test_other_period_or_language_is_not_a_duplicate(self, client, created):
        form = f"dupform{uuid4().hex[:10]}"
        for body in (
            {"language_code": "eng", "date_start": 1700},
            {"language_code": "eng", "date_start": 1701},
            {"language_code": "eng"},
            {"language_code": "enm", "date_start": 1700},
        ):
            response = self._post(client, created, form_orthographic=form, **body)
            assert response.status_code == 201, (body, response.text)
        undated_again = self._post(client, created, form_orthographic=form, language_code="eng")
        assert undated_again.status_code == 409

    def test_created_record_matches_stored_record(self, client, created):
        response = self._post(
            client, created, form_orthographic=f"dupform{uuid4().hex[:10]}", language_code="eng"
        )
        assert response.status_code == 201
        posted = response.json()["data"]
        stored = client.get(f"/api/v1/lsr/{posted['id']}").json()["data"]
        assert stored["created_at"] == posted["created_at"]


class TestWriteVisibility:
    """A write is visible to the very next request. Meaningful with
    Elasticsearch (index refresh) and Redis (cached records) connected."""

    def test_form_search_right_after_create_and_delete(self, client):
        form = f"freshform{uuid4().hex[:10]}"
        response = client.post(
            "/api/v1/lsr/", json={"form_orthographic": form, "language_code": "eng"}
        )
        assert response.status_code == 201
        lsr_id = response.json()["data"]["id"]
        try:
            found = client.get(SEARCH, params={"form": form}).json()
            assert [row["id"] for row in found["results"]] == [lsr_id]
        finally:
            assert client.delete(f"/api/v1/lsr/{lsr_id}").status_code == 200
        gone = client.get(SEARCH, params={"form": form}).json()
        assert (gone["total"], gone["results"]) == (0, [])

    def test_deleting_a_neighbour_updates_cached_records(self, client):
        parent = LSR(form_orthographic="cacheparent", language_code="qla")
        child = LSR(form_orthographic="cachechild", language_code="qla")
        with _lineage(client, [parent, child], [(child, parent, "DESCENDS_FROM")]):
            record = client.get(f"/api/v1/lsr/{child.id}").json()["data"]
            assert record["ancestor_ids"] == [str(parent.id)]
            assert client.delete(f"/api/v1/lsr/{parent.id}").status_code == 200
            record = client.get(f"/api/v1/lsr/{child.id}").json()["data"]
            assert record["ancestor_ids"] == []


# =============================================================================
# Elasticsearch parity (D3-11, D3-13): same filters, same results
# =============================================================================


class TestElasticsearchParity:
    """Run each search on both backends; skipped unless Elasticsearch is reachable."""

    @pytest.fixture
    def repo_call(self, client, graph):
        def has_es(repo: LSRRepository) -> Awaitable[bool]:
            async def check() -> bool:
                if not repo._has_elasticsearch():
                    return False
                await repo.db.elasticsearch.indices.refresh(index=ES_INDEX_NAME)
                return True

            return check()

        if not _run(client, has_es):
            pytest.skip("Elasticsearch is not reachable")
        return lambda fn: _run(client, fn)

    @pytest.mark.parametrize(
        "filters",
        [
            {},
            {"language": "eng"},
            {"language": "gem-pro"},
            {"date_start": 1400, "date_end": 1600},
            {"date_end": 0},
            {"date_start": 1550},
        ],
    )
    def test_filters_match_neo4j(self, repo_call, graph, filters):
        params = {"semantic_field": graph.tag, "limit": 100, **filters}

        async def both(repo: LSRRepository) -> tuple:
            return (
                await repo._search_neo4j(**params),
                await repo._search_elasticsearch(**params),
            )

        (neo4j_rows, neo4j_total), (es_rows, es_total) = repo_call(both)
        assert es_total == neo4j_total
        assert [str(r.id) for r in es_rows] == [str(r.id) for r in neo4j_rows]

    @pytest.mark.parametrize("form", ["night", "NACHT", "nokʷ", "ni"])
    def test_form_matches_include_neo4j_matches(self, repo_call, graph, form):
        params = {"form": form, "semantic_field": graph.tag, "limit": 100}

        async def both(repo: LSRRepository) -> tuple:
            return (
                await repo._search_neo4j(**params),
                await repo._search_elasticsearch(**params),
            )

        (neo4j_rows, _), (es_rows, _) = repo_call(both)
        assert neo4j_rows
        assert {r.id for r in neo4j_rows} <= {r.id for r in es_rows}

    def test_fuzzy_form(self, repo_call, graph):
        async def search(repo: LSRRepository) -> tuple:
            return await repo.search(form="nigth", semantic_field=graph.tag)

        rows, _ = repo_call(search)
        assert {graph.ids["enm"], graph.ids["eng"]} <= {str(r.id) for r in rows}
