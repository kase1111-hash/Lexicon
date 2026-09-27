"""GraphQL against a live Neo4j, compared with the REST answers.

The fixture graph is written through LSRRepository, as ingestion does, and
deleted afterwards. Runs only against a Neo4j named explicitly (see
tests/conftest.py), e.g.

    TEST_NEO4J_URI=bolt://localhost:7688 TEST_NEO4J_PASSWORD=... pytest tests/integration/test_graphql_live.py
"""

import random
import string
import time
from collections.abc import Iterator
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

import src.utils.db as db_module
from src.api.main import app
from src.repositories.lsr_repository import MAX_LINKED_IDS


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
    reason="requires a reachable Neo4j (set TEST_NEO4J_URI / TEST_NEO4J_PASSWORD)",
)

LSR_API = "/api/v1/lsr"
GRAPH_API = "/api/v1/graph"

# ISO 639-3 reserves qaa-qtz for local use; the random suffix keeps leftovers
# of an interrupted run out of this run's answers
_SUFFIX = "".join(random.choices(string.ascii_lowercase, k=4))
_TEST_LANGUAGE = f"qaa-{_SUFFIX}"

# key: (form, language, language name, reconstructed)
_LINEAGE = {
    "pie": ("*wódr̥", "ine-pro", "Proto-Indo-European", True),
    "pgmc": ("*watōr", "gem-pro", "Proto-Germanic", True),
    "oe": ("wæter", "ang", "Old English", False),
    "en": ("water", "eng", "English", False),
    "de": ("Wasser", "deu", "German", False),
    "sv": ("vatten", "swe", "Swedish", False),
    "grc": ("ὕδωρ", "grc", "Ancient Greek", False),
    "hit": ("watar", "hit", "Hittite", False),
}
# DESCENDS_FROM child -> parent
_DESCENT = [("pgmc", "pie"), ("oe", "pgmc"), ("en", "oe"), ("de", "pgmc"), ("sv", "pgmc")]
_DESCENT += [("grc", "pie")]
_DAG_GENERATIONS = 20
_HUB_LOANS = MAX_LINKED_IDS + 20


async def _seed(ids: dict[str, Any]) -> None:
    from src.models.lsr import LSR
    from src.repositories.lsr_repository import LSRRepository

    repo = LSRRepository(await db_module.get_db())
    lsrs = []
    for key, (form, code, name, reconstructed) in _LINEAGE.items():
        lsr = LSR(
            form_orthographic=form,
            language_code=code,
            language_name=name,
            reconstruction_flag=reconstructed,
            semantic_fields=[f"gql-{_SUFFIX}"],
        )
        ids[key] = str(lsr.id)
        lsrs.append(lsr)
    # One language, named inconsistently by its LSRs (D4-15)
    for i, name in enumerate(["Testish", "Testish", "", "Other Testish"]):
        lsrs.append(
            LSR(form_orthographic=f"tword{i}", language_code=_TEST_LANGUAGE, language_name=name)
        )
    # A hub word borrowed by more LSRs than an id list holds
    hub = LSR(form_orthographic="hubword", language_code="lat", semantic_fields=[f"hub-{_SUFFIX}"])
    loans = [LSR(form_orthographic=f"hubloan{i}", language_code="eng") for i in range(_HUB_LOANS)]
    ids["hub"] = str(hub.id)
    lsrs += [hub, *loans]
    # Duplicated generations: every node descends from both nodes of the
    # previous one, so the number of paths doubles per generation
    root = LSR(form_orthographic="dagroot", language_code="ine-pro")
    dag = [root]
    rels: list[dict[str, Any]] = []
    previous = [root]
    for generation in range(_DAG_GENERATIONS):
        current = [
            LSR(form_orthographic=f"dag{generation}x{k}", language_code="gem-pro") for k in range(2)
        ]
        rels += [
            {"source_id": str(c.id), "target_id": str(p.id), "type": "DESCENDS_FROM"}
            for c in current
            for p in previous
        ]
        dag += current
        previous = current
    ids["dag_root"], ids["dag_leaf"] = str(root.id), str(previous[0].id)
    lsrs += dag
    ids["all"] = [str(lsr.id) for lsr in lsrs]
    assert (await repo.create_batch(lsrs)).failed == 0

    rels += [
        {"source_id": ids[a], "target_id": ids[b], "type": "DESCENDS_FROM", "confidence": 0.9}
        for a, b in _DESCENT
    ]
    rels.append({"source_id": ids["en"], "target_id": ids["hit"], "type": "COGNATE_OF"})
    rels += [
        {"source_id": str(loan.id), "target_id": ids["hub"], "type": "BORROWED_FROM"}
        for loan in loans
    ]
    result = await repo.create_relationships_batch(rels)
    assert result.failed == 0, result.errors


async def _cleanup(ids: dict[str, Any]) -> None:
    from src.repositories.lsr_repository import ES_INDEX_NAME, LSRRepository

    db = await db_module.get_db()
    async with db.neo4j_session() as session:
        await session.run(
            "MATCH (l:LSR) WHERE l.id IN $ids DETACH DELETE l", {"ids": ids.get("all", [])}
        )
    # create_batch also indexed the fixture when Elasticsearch is configured
    if LSRRepository(db)._has_elasticsearch():
        await db.elasticsearch.delete_by_query(
            index=ES_INDEX_NAME,
            query={"terms": {"id": ids.get("all", [])}},
            refresh=True,
            ignore_unavailable=True,
        )


@pytest.fixture(scope="module")
def live() -> Iterator[TestClient]:
    """One app lifespan (and event loop) for the module."""
    db_module._db_manager = None  # never reuse a manager bound to another loop
    with TestClient(app) as test_client:
        yield test_client
    db_module._db_manager = None


@pytest.fixture(scope="module")
def seeded(live: TestClient) -> Iterator[dict[str, Any]]:
    ids: dict[str, Any] = {}
    assert live.portal is not None
    try:
        live.portal.call(_seed, ids)
        yield ids
    finally:
        live.portal.call(_cleanup, ids)


def _gql(client: TestClient, query: str) -> dict[str, Any]:
    response = client.post("/graphql", json={"query": query})
    assert response.status_code == 200
    body: dict[str, Any] = response.json()
    return body


def _data(client: TestClient, query: str) -> dict[str, Any]:
    body = _gql(client, query)
    assert "errors" not in body, body["errors"]
    data: dict[str, Any] = body["data"]
    return data


def _keys(ids: dict[str, Any], nodes: list[dict[str, Any]]) -> list[str]:
    names = {v: k for k, v in ids.items() if isinstance(v, str)}
    return [names.get(node["id"], node["id"]) for node in nodes]


class TestSameRulesAsRest:
    """GraphQL traversals use LSRRepository, like REST (D4-03)."""

    @pytest.mark.parametrize("key", ["en", "de", "pgmc", "pie"])
    def test_cognates(self, live, seeded, key) -> None:
        rest = live.get(f"{LSR_API}/{seeded[key]}/cognates").json()["cognates"]
        graph_route = live.get(f"{GRAPH_API}/cognates/{seeded[key]}").json()["by_language"]
        query = f'{{ lsr(id: "{seeded[key]}") {{ cognates {{ id }} }} }}'
        cognates = _data(live, query)["lsr"]["cognates"]
        assert _keys(seeded, cognates) == _keys(seeded, rest)
        assert sorted(_keys(seeded, cognates)) == sorted(
            _keys(seeded, [c for group in graph_route.values() for c in group])
        )

    def test_cognates_exclude_own_lineage(self, live, seeded) -> None:
        """The README example: water's cognates are not its own ancestors."""
        query = f'{{ lsr(id: "{seeded["en"]}") {{ cognates {{ id }} }} }}'
        cognates = _keys(seeded, _data(live, query)["lsr"]["cognates"])
        assert sorted(cognates) == ["de", "grc", "hit", "sv"]

    @pytest.mark.parametrize("key", ["en", "de", "pie"])
    def test_etymology(self, live, seeded, key) -> None:
        rest = live.get(f"{LSR_API}/{seeded[key]}/etymology").json()
        query = (
            f'{{ etymology(lsrId: "{seeded[key]}") '
            "{ depth truncated protoForm { id } steps { depth lsr { id } } } }"
        )
        chain = _data(live, query)["etymology"]
        assert _keys(seeded, [s["lsr"] for s in chain["steps"]]) == _keys(seeded, rest["chain"])
        assert [s["depth"] for s in chain["steps"]] == list(range(len(rest["chain"])))
        assert chain["depth"] == rest["depth"] and chain["truncated"] is rest["truncated"] is False
        assert chain["protoForm"]["id"] == rest["proto_form"]["id"] == seeded["pie"]

    def test_etymology_cut_off_by_max_depth(self, live, seeded) -> None:
        rest = live.get(f"{LSR_API}/{seeded['en']}/etymology", params={"max_depth": 2}).json()
        query = f'{{ etymology(lsrId: "{seeded["en"]}", maxDepth: 2) {{ depth truncated protoForm {{ id }} }} }}'
        chain = _data(live, query)["etymology"]
        assert chain == {"depth": 2, "truncated": True, "protoForm": None}
        assert rest["truncated"] is True and rest["proto_form"] is None

    def test_missing_lsr_is_null(self, live, seeded) -> None:
        missing = uuid4()
        data = _data(
            live, f'{{ lsr(id: "{missing}") {{ form }} etymology(lsrId: "{missing}") {{ depth }} }}'
        )
        assert data == {"lsr": None, "etymology": None}

    def test_ancestors_nearest_first(self, live, seeded) -> None:
        query = f'{{ lsr(id: "{seeded["en"]}") {{ ancestors {{ id }} }} }}'
        assert _keys(seeded, _data(live, query)["lsr"]["ancestors"]) == ["oe", "pgmc", "pie"]

    def test_descendants(self, live, seeded) -> None:
        rest = live.get(f"{LSR_API}/{seeded['pie']}/descendants", params={"depth": 5}).json()
        query = f'{{ lsr(id: "{seeded["pie"]}") {{ descendants(depth: 5) {{ id }} }} }}'
        descendants = _data(live, query)["lsr"]["descendants"]
        assert _keys(seeded, descendants) == _keys(seeded, rest["descendants"])
        assert sorted(_keys(seeded, descendants)) == ["de", "en", "grc", "oe", "pgmc", "sv"]

    def test_lineage_is_not_exponential(self, live, seeded) -> None:
        """Duplicated generations must not blow up ancestors/etymology (D3-24)."""
        started = time.monotonic()
        query = (
            f'{{ lsr(id: "{seeded["dag_leaf"]}") {{ ancestors(depth: 50) {{ id }} }} '
            f'etymology(lsrId: "{seeded["dag_leaf"]}", maxDepth: 50) {{ depth protoForm {{ id }} }} }}'
        )
        data = _data(live, query)
        assert time.monotonic() - started < 10
        assert len(data["lsr"]["ancestors"]) == 2 * _DAG_GENERATIONS - 1
        assert data["lsr"]["ancestors"][-1]["id"] == seeded["dag_root"]
        assert data["etymology"]["depth"] == _DAG_GENERATIONS
        assert data["etymology"]["protoForm"]["id"] == seeded["dag_root"]


class TestLanguages:
    """One entry per language code, with an honest isLiving (D4-15)."""

    def test_grouped_by_code_with_most_frequent_name(self, live, seeded) -> None:
        languages = _data(live, "{ languages { isoCode name } }")["languages"]
        codes = [lang["isoCode"] for lang in languages]
        assert len(codes) == len(set(codes))
        assert {"isoCode": _TEST_LANGUAGE, "name": "Testish"} in languages

    def test_single_language(self, live, seeded) -> None:
        query = f'{{ language(isoCode: "{_TEST_LANGUAGE}") {{ isoCode name isLiving }} }}'
        assert _data(live, query)["language"] == {
            "isoCode": _TEST_LANGUAGE,
            "name": "Testish",
            "isLiving": None,
        }

    def test_is_living_not_claimed(self, live, seeded) -> None:
        data = _data(
            live,
            '{ grc: language(isoCode: "grc") { isLiving } '
            'pgmc: language(isoCode: "gem-pro") { isLiving } }',
        )
        assert data == {"grc": {"isLiving": None}, "pgmc": {"isLiving": False}}


class TestRelationshipIdCaps:
    """Hub words list at most MAX_LINKED_IDS ids per relationship."""

    def test_get_lsr(self, live, seeded) -> None:
        data = live.get(f"{LSR_API}/{seeded['hub']}").json()["data"]
        assert len(data["loan_target_ids"]) == MAX_LINKED_IDS
        assert data["loan_target_ids"] == sorted(data["loan_target_ids"])
        assert data["relationship_counts"]["loan_targets"] == _HUB_LOANS
        assert data["relationship_ids_truncated"] is True

    def test_search(self, live, seeded) -> None:
        body = live.get(f"{LSR_API}/search", params={"semantic_field": f"hub-{_SUFFIX}"}).json()
        [hub] = body["results"]
        assert len(hub["loan_target_ids"]) == MAX_LINKED_IDS
        assert hub["relationship_counts"]["loan_targets"] == _HUB_LOANS

    def test_small_record_is_complete(self, live, seeded) -> None:
        data = live.get(f"{LSR_API}/{seeded['pgmc']}").json()["data"]
        assert data["relationship_ids_truncated"] is False
        assert data["relationship_counts"]["descendants"] == len(data["descendant_ids"]) == 3


class TestRelationshipBatchLive:
    """Edges to LSRs that do not exist are failures, not silent drops (D4-19)."""

    def test_missing_endpoints(self, live, seeded) -> None:
        from src.repositories.lsr_repository import LSRRepository

        ghost = str(uuid4())

        async def write() -> Any:
            repo = LSRRepository(await db_module.get_db())
            return await repo.create_relationships_batch(
                [
                    {"source_id": seeded["sv"], "target_id": ghost, "type": "BORROWED_FROM"},
                    {"source_id": seeded["sv"], "target_id": seeded["de"], "type": "COGNATE_OF"},
                ]
            )

        assert live.portal is not None
        result = live.portal.call(write)
        assert (result.succeeded, result.failed) == (1, 1)
        assert result.errors == [
            f"BORROWED_FROM {seeded['sv']} -> {ghost}: no LSR with the target id"
        ]


class TestOutage:
    """A Neo4j that goes away is a GraphQL error, not null or [] (D4-05)."""

    def test_errors_while_neo4j_is_unreachable(self, live, seeded) -> None:
        from neo4j import AsyncGraphDatabase

        async def swap(driver: Any) -> Any:
            db = await db_module.get_db()
            previous, db._neo4j_driver = db._neo4j_driver, driver
            return previous

        async def unreachable() -> Any:
            return AsyncGraphDatabase.driver("bolt://127.0.0.1:1", auth=("neo4j", "x"))

        assert live.portal is not None
        broken = live.portal.call(unreachable)
        working = live.portal.call(swap, broken)
        try:
            # Nullable root fields each fail on their own
            nullable = _gql(
                live,
                f'{{ lsr(id: "{seeded["en"]}") {{ form }} '
                f'etymology(lsrId: "{seeded["en"]}") {{ depth }} '
                'language(isoCode: "eng") { name } }',
            )
            # A failing non-null root field nulls `data`; graphql-core then
            # drops the errors of sibling fields still running, so only the
            # first failure is certain to be listed
            non_null = _gql(live, '{ languages { isoCode } searchLsr(language: "eng") { form } }')
        finally:
            live.portal.call(swap, working)
            live.portal.call(broken.close)
        assert nullable["data"] == {"lsr": None, "etymology": None, "language": None}
        assert {tuple(e["path"]) for e in nullable["errors"]} == {
            ("lsr",),
            ("etymology",),
            ("language",),
        }
        assert non_null["data"] is None and non_null["errors"]
        assert {tuple(e["path"]) for e in non_null["errors"]} <= {("languages",), ("searchLsr",)}
        for body in (nullable, non_null):
            assert all(e["extensions"]["code"] == "DATABASE_ERROR" for e in body["errors"])
            assert all(e["message"] == "Graph database is not available" for e in body["errors"])
            assert "127.0.0.1" not in str(body)
        # and the graph answers again once Neo4j is back
        query = f'{{ lsr(id: "{seeded["en"]}") {{ form }} }}'
        assert _data(live, query) == {"lsr": {"form": "water"}}
