"""Repository for LSR persistence operations using Neo4j.

Supports:
- Single and batch CRUD operations against Neo4j
- Elasticsearch indexing and full-text search (when connected)
- Redis cache integration (when connected)
- Relationship (edge) creation and batch insertion
"""

import asyncio
import logging
import weakref
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from neo4j import Query as Neo4jQuery
from neo4j.exceptions import ServiceUnavailable, SessionExpired

from src.exceptions import DatabaseError, LSRNotFoundError
from src.models.lsr import LSR
from src.utils.db import DatabaseManager

logger = logging.getLogger(__name__)

# Upper bound on DESCENDS_FROM hops followed by lineage traversals. Real
# etymologies are far shallower; the bound keeps malformed data (cycles,
# duplicated generations) from turning a lookup into an unbounded walk.
MAX_LINEAGE_DEPTH = 50
DEFAULT_ETYMOLOGY_DEPTH = 20

# Error message for a Neo4j that is down or was never reached. Driver
# messages (addresses, query internals) are logged, never returned.
GRAPH_UNAVAILABLE = "Graph database is not available"

# Server-side timeout of the repository's read queries. Neo4j only checks it
# between units of work, so the client stops waiting a little later.
READ_TIMEOUT_SECONDS = 15
_CLIENT_DEADLINE_GRACE_SECONDS = 2
# The statistics are several full counts, so they get longer than one read
STATISTICS_TIMEOUT_SECONDS = 60

# At most this many ids are listed per relationship field of an LSR read: a
# donor word or a proto-form can be linked to thousands of LSRs. The full
# counts are kept in LSRRepository.relationship_counts.
MAX_LINKED_IDS = 100

# Directly linked LSR ids (at most MAX_LINKED_IDS each, lowest ids first) and
# their counts, returned next to `l` so the relationship fields of the LSR
# model reflect the graph (see _record_to_lsr). loan_source_ids holds the
# most confident donor only.
_RELATIONSHIP_COLUMNS = f"""
    COLLECT {{ MATCH (l)-[:DESCENDS_FROM]->(x:LSR) RETURN DISTINCT x.id AS id
               ORDER BY id LIMIT {MAX_LINKED_IDS} }} AS ancestor_ids,
    COUNT {{ MATCH (l)-[:DESCENDS_FROM]->(x:LSR) RETURN DISTINCT x }} AS ancestor_count,
    COLLECT {{ MATCH (l)<-[:DESCENDS_FROM]-(x:LSR) RETURN DISTINCT x.id AS id
               ORDER BY id LIMIT {MAX_LINKED_IDS} }} AS descendant_ids,
    COUNT {{ MATCH (l)<-[:DESCENDS_FROM]-(x:LSR) RETURN DISTINCT x }} AS descendant_count,
    COLLECT {{ MATCH (l)-[:COGNATE_OF]-(x:LSR) WHERE x <> l RETURN DISTINCT x.id AS id
               ORDER BY id LIMIT {MAX_LINKED_IDS} }} AS cognate_ids,
    COUNT {{ MATCH (l)-[:COGNATE_OF]-(x:LSR) WHERE x <> l RETURN DISTINCT x }} AS cognate_count,
    COLLECT {{ MATCH (l)-[r:BORROWED_FROM]->(x:LSR) WHERE x.id IS NOT NULL
               RETURN x.id AS id ORDER BY coalesce(r.confidence, 0.0) DESC, id
               LIMIT 1 }} AS loan_source_ids,
    COUNT {{ MATCH (l)-[:BORROWED_FROM]->(x:LSR) RETURN DISTINCT x }} AS loan_source_count,
    COLLECT {{ MATCH (l)<-[:BORROWED_FROM]-(x:LSR) RETURN DISTINCT x.id AS id
               ORDER BY id LIMIT {MAX_LINKED_IDS} }} AS loan_target_ids,
    COUNT {{ MATCH (l)<-[:BORROWED_FROM]-(x:LSR) RETURN DISTINCT x }} AS loan_target_count
"""

# Elasticsearch clients whose index was already created or updated by this
# process, so single writes do not pay for a check each (see
# ensure_elasticsearch_index).
_es_index_ready: "weakref.WeakSet[Any]" = weakref.WeakSet()


def _bulk_error_reason(errors: Any) -> str:
    """The type and reason of the first item error reported by async_bulk."""
    try:
        item = next(iter(errors[0].values()))
        error = item.get("error", item)
    except (IndexError, KeyError, AttributeError, StopIteration, TypeError):
        return "no details"
    if isinstance(error, dict):
        return f"{error.get('type')}: {error.get('reason')}"
    return str(error)


def database_error(error: Exception, what: str) -> DatabaseError:
    """Map a Neo4j failure to a DatabaseError that leaks no driver internals.

    The details are logged. An unreachable Neo4j gives GRAPH_UNAVAILABLE,
    anything else "<what> failed" (or "timed out").
    """
    if isinstance(error, RuntimeError | ServiceUnavailable | SessionExpired):
        # RuntimeError: DatabaseManager has no driver (never connected)
        logger.warning(f"{what} failed, Neo4j unavailable: {error}")
        return DatabaseError(message=GRAPH_UNAVAILABLE)
    if isinstance(error, TimeoutError) or "TransactionTimedOut" in str(getattr(error, "code", "")):
        logger.warning(f"{what} timed out")
        return DatabaseError(message=f"{what} timed out")
    logger.error(f"{what} failed: {error}")
    return DatabaseError(message=f"{what} failed")


def node_summary(node: Any) -> dict[str, Any]:
    """Compact, JSON-safe view of an LSR node for traversal responses."""
    props = dict(node)
    return {
        "id": props.get("id"),
        "form": props.get("form_orthographic"),
        "language_code": props.get("language_code"),
        "language_name": props.get("language_name"),
        "date_start": props.get("date_start"),
        "date_end": props.get("date_end"),
        "definition": props.get("definition_primary"),
    }


def _to_datetime(value: Any) -> datetime | None:
    """Convert a stored Neo4j temporal value to a Python datetime."""
    if isinstance(value, datetime):
        return value
    if hasattr(value, "to_native"):
        native = value.to_native()
        if isinstance(native, datetime):
            return native
    return None


def _uuid_list(values: Any) -> list[UUID]:
    """Parse, de-duplicate and sort LSR ids read from the graph."""
    ids: set[UUID] = set()
    for value in values or []:
        try:
            ids.add(UUID(str(value)))
        except ValueError:
            logger.warning(f"Ignoring malformed LSR id in graph: {value!r}")
    return sorted(ids, key=str)


def _escape_wildcard(value: str) -> str:
    """Escape Elasticsearch wildcard metacharacters in user input."""
    return value.replace("\\", "\\\\").replace("*", "\\*").replace("?", "\\?")


# The properties that date an LSR. A fill-only write takes them as a unit, and
# only onto a node with no dates: a missing date_end on a dated LSR means it is
# still in use, a date_end must not precede its own LSR's date_start, and a
# date's confidence and period label belong to it (see LSR.merge_with).
_DATING_PROPERTIES = ("date_start", "date_end", "date_confidence", "date_source", "period_label")


def _fill_only_query(props: dict[str, Any]) -> str:
    """Cypher for a fill-only upsert of the property maps in $batch.

    A new node gets every property. An existing node keeps each property
    that is set and not empty ("" or []) and gains the others, except that
    the dating (_DATING_PROPERTIES) is taken as a unit when the node has no
    dates, and a dated node gains none of it; source_databases becomes the
    union of both. Returns the written nodes.

    Args:
        props: A property map from _lsr_to_params; only its keys are used.
    """
    assignments = []
    for key in props:
        if key == "id":
            continue
        stored, new = f"l.{key}", f"props.{key}"
        if key == "source_databases":
            value = (
                f"reduce(acc = coalesce({stored}, []), s IN coalesce({new}, []) | "
                "CASE WHEN s IN acc THEN acc ELSE acc + s END)"
            )
        else:
            fill = f"{stored} IS NULL OR {stored} = '' OR {stored} = []"
            if key in ("date_start", "date_end"):
                fill = "take_dating"
            elif key in _DATING_PROPERTIES:
                fill = f"take_dating OR (undated AND ({fill}))"
            value = f"CASE WHEN {fill} THEN {new} ELSE {stored} END"
        assignments.append(f"{stored} = {value}")
    return f"""
    UNWIND $batch AS props
    MERGE (l:LSR {{id: props.id}})
    ON CREATE SET l.created_at = datetime()
    WITH l, props, l.date_start IS NULL AND l.date_end IS NULL AS undated
    WITH l, props, undated,
         undated AND (props.date_start IS NOT NULL OR props.date_end IS NOT NULL) AS take_dating
    SET {", ".join(assignments)}, l.updated_at = datetime()
    RETURN l
    """


# Allowlist of valid relationship types for Cypher queries.
# Prevents Cypher injection via relationship type interpolation.
VALID_RELATIONSHIP_TYPES = frozenset(
    {
        "DESCENDS_FROM",
        "BORROWED_FROM",
        "COGNATE_OF",
        "SHIFTED_TO",
        "MERGED_WITH",
        "RELATED_TO",
    }
)

# Elasticsearch index configuration
ES_INDEX_NAME = "lexicon_lsr"
# LSRs read from Neo4j (and documents checked) per step of a full reindex
REINDEX_PAGE_SIZE = 1000
ES_INDEX_SETTINGS: dict[str, Any] = {
    "settings": {
        "number_of_shards": 1,
        "number_of_replicas": 0,
        "analysis": {
            "analyzer": {
                "form_analyzer": {
                    "type": "custom",
                    "tokenizer": "standard",
                    "filter": ["lowercase", "asciifolding"],
                },
            },
        },
    },
    "mappings": {
        "properties": {
            "id": {"type": "keyword"},
            "form_orthographic": {
                "type": "text",
                "analyzer": "form_analyzer",
                "fields": {"raw": {"type": "keyword"}},
            },
            "form_normalized": {"type": "keyword"},
            "form_phonetic": {"type": "text"},
            "language_code": {"type": "keyword"},
            "language_name": {"type": "keyword"},
            "language_family": {"type": "keyword"},
            "definition_primary": {"type": "text", "analyzer": "standard"},
            "date_start": {"type": "integer"},
            "date_end": {"type": "integer"},
            "period_label": {"type": "keyword"},
            "confidence_overall": {"type": "float"},
            "reconstruction_flag": {"type": "boolean"},
            "source_databases": {"type": "keyword"},
            "semantic_fields": {"type": "keyword"},
        },
    },
}


@dataclass
class BatchResult:
    """Result of a batch operation."""

    succeeded: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)
    # Records written to Neo4j that could not be written to the search index
    index_failed: int = 0


class LSRRepository:
    """Repository for LSR CRUD operations in Neo4j.

    Optionally integrates with Elasticsearch for full-text search and
    Redis for caching. Falls back to Neo4j-only when ES/Redis are
    not connected.
    """

    def __init__(self, db: DatabaseManager):
        """Initialize the repository with a database manager."""
        self.db = db
        # Full counts of the directly linked LSRs of every LSR read through
        # this repository (the id lists on the model are capped): LSR id ->
        # {"ancestors", "descendants", "cognates", "loan_sources", "loan_targets"}
        self.relationship_counts: dict[str, dict[str, int]] = {}
        # True once a form search fell back from Elasticsearch to Neo4j, or ran
        # on Neo4j because a configured Elasticsearch is not connected (no
        # fuzzy matches); such results should not be cached
        self.search_degraded = False

    # -------------------------------------------------------------------------
    # Single-record CRUD
    # -------------------------------------------------------------------------

    async def create(self, lsr: LSR) -> LSR:
        """
        Create a new LSR in the database.

        Args:
            lsr: The LSR to create.

        Returns:
            The created LSR as stored, including the database timestamps.

        Raises:
            DatabaseError: If the creation fails or times out (see _run).
        """
        query = """
        CREATE (l:LSR)
        SET l = $props, l.created_at = datetime(), l.updated_at = datetime()
        RETURN l
        """
        params = {"props": self._lsr_to_params(lsr)}

        try:
            rows = await self._run(query, params)
            if rows:
                logger.info(f"Created LSR: {lsr.id}")
                await self._index_to_elasticsearch(lsr)
                return self._node_to_lsr(rows[0]["l"])
            raise DatabaseError(message="LSR creation failed")
        except DatabaseError:
            raise
        except Exception as e:
            raise database_error(e, "LSR creation") from e

    async def get_by_id(self, lsr_id: UUID) -> LSR:
        """
        Get an LSR by its ID.

        Args:
            lsr_id: The UUID of the LSR to retrieve.

        Returns:
            The LSR if found, with ancestor_ids, descendant_ids, cognate_ids,
            loan_source_id and loan_target_ids filled from its direct edges.

        Raises:
            LSRNotFoundError: If no LSR with the given ID exists.
            DatabaseError: If the query fails or times out (see _read).
        """
        query = f"""
        MATCH (l:LSR {{id: $id}})
        RETURN l, {_RELATIONSHIP_COLUMNS}
        """

        rows = await self._read(query, {"id": str(lsr_id)}, "LSR retrieval")
        if not rows:
            raise LSRNotFoundError(lsr_id=str(lsr_id))
        try:
            return self._record_to_lsr(rows[0])
        except Exception as e:
            raise database_error(e, "LSR retrieval") from e

    async def update(self, lsr: LSR) -> LSR:
        """
        Update an existing LSR.

        Args:
            lsr: The LSR with updated fields.

        Returns:
            The updated LSR.

        Raises:
            LSRNotFoundError: If no LSR with the given ID exists.
            DatabaseError: If the update fails or times out (see _run).
        """
        query = """
        MATCH (l:LSR {id: $id})
        SET l += $props, l.updated_at = datetime()
        RETURN l
        """
        params = {"id": str(lsr.id), "props": self._lsr_to_params(lsr)}

        try:
            rows = await self._run(query, params)
            if not rows:
                raise LSRNotFoundError(lsr_id=str(lsr.id))
            logger.info(f"Updated LSR: {lsr.id}")
            await self._index_to_elasticsearch(lsr)
            return self._node_to_lsr(rows[0]["l"])
        except LSRNotFoundError:
            raise
        except Exception as e:
            raise database_error(e, "LSR update") from e

    async def delete(self, lsr_id: UUID) -> bool:
        """
        Delete an LSR by its ID.

        Args:
            lsr_id: The UUID of the LSR to delete.

        Returns:
            True if the LSR was deleted.

        Raises:
            LSRNotFoundError: If no LSR with the given ID exists.
            DatabaseError: If the deletion fails or times out (see _run).
        """
        query = """
        MATCH (l:LSR {id: $id})
        DETACH DELETE l
        RETURN count(l) as deleted
        """

        try:
            rows = await self._run(query, {"id": str(lsr_id)})
            if rows and rows[0]["deleted"] > 0:
                logger.info(f"Deleted LSR: {lsr_id}")
                await self._remove_from_elasticsearch(lsr_id)
                return True
            raise LSRNotFoundError(lsr_id=str(lsr_id))
        except LSRNotFoundError:
            raise
        except Exception as e:
            raise database_error(e, "LSR deletion") from e

    async def exists(self, lsr_id: UUID) -> bool:
        """Return True if an LSR with the given ID exists."""
        rows = await self._read(
            "MATCH (l:LSR {id: $id}) RETURN count(l) > 0 AS found",
            {"id": str(lsr_id)},
            "LSR lookup",
        )
        return bool(rows and rows[0]["found"])

    async def find_duplicate(
        self, form_normalized: str, language_code: str, date_start: int | None
    ) -> str | None:
        """Return the id of an LSR with the same normalized form, language and
        first attestation (both undated counts as the same), or None."""
        rows = await self._read(
            """
            MATCH (l:LSR {language_code: $language_code, form_normalized: $form})
            WHERE l.date_start = $date_start OR (l.date_start IS NULL AND $date_start IS NULL)
            RETURN l.id AS id
            ORDER BY l.id
            LIMIT 1
            """,
            {"form": form_normalized, "language_code": language_code, "date_start": date_start},
            "Duplicate check",
        )
        return rows[0]["id"] if rows else None

    # -------------------------------------------------------------------------
    # Lineage traversals
    # -------------------------------------------------------------------------

    async def _expand_lineage(
        self, start_ids: list[str], direction: str, depth: int, what: str
    ) -> tuple[dict[str, int], set[str], bool]:
        """Breadth-first DESCENDS_FROM expansion from start_ids, one generation per query.

        Each node is visited once, so the work is linear in the size of the
        lineage. A single variable-length Cypher pattern is not: depending
        on the rest of the query, Neo4j may plan it as an enumeration of
        every path, which is exponential when generations are duplicated.

        Args:
            start_ids: LSR ids to start from (distance 0).
            direction: "up" follows DESCENDS_FROM to ancestors, "down" to
                descendants.
            depth: Maximum number of hops.
            what: Operation name for errors.

        Returns:
            (distance, ends, cut): the shortest hop count of every node
            reached (start nodes included, at 0); the reached nodes with no
            further edge in that direction; whether any node lies beyond
            `depth` hops.
        """
        step = (
            "(n)-[:DESCENDS_FROM]->(m:LSR)"
            if direction == "up"
            else "(n)<-[:DESCENDS_FROM]-(m:LSR)"
        )
        # The subquery keeps the id lookup an index seek; written as one
        # pattern, Neo4j may scan the whole id index once per id instead.
        query = f"""
        UNWIND $ids AS id
        MATCH (n:LSR {{id: id}})
        CALL {{ WITH n MATCH {step} RETURN m }}
        RETURN id AS node, m.id AS next
        """
        distance = dict.fromkeys(start_ids, 0)
        ends: set[str] = set()
        frontier = sorted(distance)
        level = 0
        while frontier:
            rows = await self._read(query, {"ids": frontier}, what)
            ends.update(set(frontier) - {row["node"] for row in rows})
            reached = {row["next"] for row in rows} - distance.keys()
            if reached and level == depth:
                return distance, ends, True
            level += 1
            distance.update(dict.fromkeys(reached, level))
            frontier = sorted(reached)
        return distance, ends, False

    async def get_etymology_path(
        self, lsr_id: UUID, max_depth: int = DEFAULT_ETYMOLOGY_DEPTH
    ) -> tuple[Any, bool] | None:
        """Trace an LSR back along DESCENDS_FROM to its deepest proto-form.

        The distinct ancestors within max_depth hops are found first (see
        _expand_lineage), then one shortest path to the chosen ancestor is
        returned: the farthest root when the lineage is complete, otherwise
        the farthest ancestor found.

        Returns:
            None if the LSR does not exist, else (path, complete): a Neo4j
            Path from the LSR itself to the ancestor (of length 0 when it has
            no ancestors). complete is False when max_depth cut off any line
            of ancestry (a deeper root may exist even if a shallower one was
            found) or no root exists; the path then ends at the farthest
            ancestor found.
        """
        depth = max(1, min(int(max_depth), MAX_LINEAGE_DEPTH))
        start = str(lsr_id)
        if not await self.exists(lsr_id):
            return None
        what = "Etymology chain retrieval"
        distance, roots, cut = await self._expand_lineage([start], "up", depth, what)

        def rank(node: str) -> tuple[bool, int, str]:
            complete = not cut and node in roots
            return (not complete, -distance[node], node)

        target = min(distance, key=rank)
        rows = await self._read(
            f"""
            MATCH (start:LSR {{id: $id}}), (a:LSR {{id: $target}})
            MATCH path = shortestPath((start)-[:DESCENDS_FROM*0..{depth}]->(a))
            RETURN path
            """,
            {"id": start, "target": target},
            what,
        )
        if not rows:  # the lineage changed between the queries
            return None
        return rows[0]["path"], not cut and target in roots

    async def get_etymology_chain(
        self,
        lsr_id: UUID,
        max_depth: int = DEFAULT_ETYMOLOGY_DEPTH,
        summarize: Callable[[Any], dict[str, Any]] = node_summary,
    ) -> tuple[list[dict[str, Any]], bool]:
        """The nodes of get_etymology_path, converted with `summarize`.

        Returns:
            (chain, complete): chain runs from the LSR itself to the ancestor
            (empty if the LSR does not exist); complete as for
            get_etymology_path.
        """
        found = await self.get_etymology_path(lsr_id, max_depth)
        if found is None:
            return [], True
        path, complete = found
        return [summarize(node) for node in path.nodes], complete

    async def get_ancestors(
        self,
        lsr_id: UUID,
        max_depth: int = DEFAULT_ETYMOLOGY_DEPTH,
        limit: int = 100,
        summarize: Callable[[Any], dict[str, Any]] = node_summary,
    ) -> list[dict[str, Any]]:
        """The distinct ancestors of an LSR within max_depth DESCENDS_FROM hops.

        Nearest first (by shortest distance), then by language, form and id.
        Empty if the LSR does not exist.
        """
        depth = max(1, min(int(max_depth), MAX_LINEAGE_DEPTH))
        start = str(lsr_id)
        what = "Ancestor retrieval"
        distance, _, _ = await self._expand_lineage([start], "up", depth, what)
        found = [{"id": node, "distance": d} for node, d in distance.items() if node != start]
        if not found:
            return []
        rows = await self._read(
            """
            UNWIND $found AS row
            MATCH (a:LSR {id: row.id})
            RETURN a
            ORDER BY row.distance, a.language_code, a.form_orthographic, a.id
            LIMIT $limit
            """,
            {"found": found, "limit": limit},
            what,
        )
        return [summarize(row["a"]) for row in rows]

    async def get_descendants(
        self,
        lsr_id: UUID,
        depth: int = 3,
        limit: int = 500,
        summarize: Callable[[Any], dict[str, Any]] = node_summary,
    ) -> list[dict[str, Any]]:
        """The distinct LSRs descending from an LSR within `depth` hops.

        Ordered by first attestation, language, form and id. Empty if the
        LSR does not exist or has no descendants.
        """
        depth = max(1, min(int(depth), MAX_LINEAGE_DEPTH))
        start = str(lsr_id)
        what = "Descendant retrieval"
        distance, _, _ = await self._expand_lineage([start], "down", depth, what)
        found = [node for node in distance if node != start]
        if not found:
            return []
        rows = await self._read(
            """
            UNWIND $found AS id
            MATCH (d:LSR {id: id})
            RETURN d
            ORDER BY d.date_start, d.language_code, d.form_orthographic, d.id
            LIMIT $limit
            """,
            {"found": found, "limit": limit},
            what,
        )
        return [summarize(row["d"]) for row in rows]

    async def get_cognates(
        self,
        lsr_id: UUID,
        limit: int = 100,
        summarize: Callable[[Any], dict[str, Any]] = node_summary,
    ) -> list[dict[str, Any]]:
        """Find the cognates of an LSR.

        Cognates are LSRs that share a DESCENDS_FROM ancestor with it, in a
        different language, excluding its own lineage (its ancestors and
        descendants), plus any LSR linked to it by a COGNATE_OF edge.
        Ordered by language, form and id; each node is converted with
        `summarize`. Empty if the LSR does not exist.
        """
        start = str(lsr_id)
        what = "Cognate retrieval"
        depth = MAX_LINEAGE_DEPTH
        up, _, _ = await self._expand_lineage([start], "up", depth, what)
        ancestors = set(up) - {start}
        shared: set[str] = set()
        if ancestors:
            down, _, _ = await self._expand_lineage([start], "down", depth, what)
            relatives, _, _ = await self._expand_lineage(sorted(ancestors), "down", depth, what)
            shared = set(relatives) - ancestors - set(down)
        rows = await self._read(
            """
            MATCH (x:LSR {id: $id})
            OPTIONAL MATCH (x)-[:COGNATE_OF]-(d:LSR)
            WHERE d <> x
            WITH x, collect(DISTINCT d.id) AS direct
            UNWIND $shared + direct AS id
            MATCH (c:LSR {id: id})
            WHERE id IN direct OR c.language_code <> x.language_code
            WITH DISTINCT c
            RETURN c
            ORDER BY c.language_code, c.form_orthographic, c.id
            LIMIT $limit
            """,
            {"id": start, "shared": sorted(shared), "limit": limit},
            what,
        )
        return [summarize(row["c"]) for row in rows]

    async def get_borrowings(
        self, lsr_id: UUID
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """The BORROWED_FROM edges of an LSR, with their confidence and evidence.

        Returns:
            (borrowed_from, borrowed_to): the donors this LSR was borrowed
            from (at most 10, most confident first) and the LSRs borrowed
            from it (at most 100, earliest first), as node summaries.
        """

        def entry(row: Any) -> dict[str, Any]:
            return {
                **node_summary(row["lsr"]),
                "confidence": row["confidence"],
                "evidence": row["evidence"],
            }

        donors = await self._read(
            """
            MATCH (l:LSR {id: $id})-[r:BORROWED_FROM]->(donor:LSR)
            RETURN donor AS lsr, r.confidence AS confidence, r.evidence AS evidence
            ORDER BY r.confidence DESC, donor.id
            LIMIT 10
            """,
            {"id": str(lsr_id)},
            "Borrowing retrieval",
        )
        recipients = await self._read(
            """
            MATCH (l:LSR {id: $id})<-[r:BORROWED_FROM]-(recipient:LSR)
            RETURN recipient AS lsr, r.confidence AS confidence, r.evidence AS evidence
            ORDER BY recipient.date_start, recipient.language_code, recipient.id
            LIMIT 100
            """,
            {"id": str(lsr_id)},
            "Borrowing retrieval",
        )
        return [entry(row) for row in donors], [entry(row) for row in recipients]

    async def _run(self, query: str, params: dict[str, Any]) -> list[Any]:
        """Run a query and return all records; failures are raised unmapped.

        The query runs in an auto-commit transaction (an unreachable database
        fails fast instead of being retried) with a server-side timeout of
        READ_TIMEOUT_SECONDS, and the client stops waiting
        _CLIENT_DEADLINE_GRACE_SECONDS later (TimeoutError), so a Neo4j that
        stops answering cannot hold a request until the socket times out.
        Every repository query that serves a request goes through here.
        """

        async def _fetch_all() -> list[Any]:
            async with self.db.neo4j_session() as session:
                result = await session.run(Neo4jQuery(query, timeout=READ_TIMEOUT_SECONDS), params)
                return [record async for record in result]

        records: list[Any] = await asyncio.wait_for(
            _fetch_all(), READ_TIMEOUT_SECONDS + _CLIENT_DEADLINE_GRACE_SECONDS
        )
        return records

    async def _read(self, query: str, params: dict[str, Any], what: str) -> list[Any]:
        """Run a read query (see _run); failures are raised as DatabaseError."""
        try:
            return await self._run(query, params)
        except Exception as e:
            # The driver's message (addresses, query internals, memory limits)
            # is logged, not returned to API clients.
            raise database_error(e, what) from e

    # -------------------------------------------------------------------------
    # Batch operations
    # -------------------------------------------------------------------------

    async def create_batch(
        self, lsrs: list[LSR], batch_size: int = 500, fill_only: bool = False
    ) -> BatchResult:
        """Write multiple LSRs in batches using UNWIND + MERGE on id.

        The write is an upsert: an LSR whose id already exists is updated
        in place, so re-running an ingestion does not create duplicates.
        With Elasticsearch connected the LSRs are also indexed there (and
        visible to searches when this returns); documents it rejects are
        counted in index_failed and reported in errors.

        Args:
            lsrs: List of LSR objects to write.
            batch_size: Number of LSRs per Neo4j transaction.
            fill_only: Only fill the gaps of existing nodes (for placeholder
                LSRs, see _fill_only_query): a new node gets every property,
                an existing one keeps its non-empty properties, and
                source_databases becomes the union of both. Elasticsearch
                then indexes the nodes as merged.

        Returns:
            BatchResult with success/failure counts.
        """
        result = BatchResult()
        indexed_any = False

        query = """
        UNWIND $batch AS props
        MERGE (l:LSR {id: props.id})
        ON CREATE SET l.created_at = datetime()
        SET l += props, l.updated_at = datetime()
        RETURN count(l) AS written
        """

        for i in range(0, len(lsrs), batch_size):
            chunk = lsrs[i : i + batch_size]
            batch_params = [self._lsr_to_params(lsr) for lsr in chunk]

            try:
                if fill_only:
                    rows = await self._run(
                        _fill_only_query(batch_params[0]), {"batch": batch_params}
                    )
                    result.succeeded += len(rows)
                else:
                    rows = await self._run(query, {"batch": batch_params})
                    result.succeeded += rows[0]["written"] if rows else 0
            except Exception as e:
                error = database_error(e, "LSR batch write")
                result.failed += len(chunk)
                result.errors.append(f"Batch {i // batch_size}: {error.message}")
                continue

            if fill_only and self._has_elasticsearch():
                # Index the nodes as merged, not the placeholders' own values
                chunk = []
                for row in rows:
                    try:
                        chunk.append(self._node_to_lsr(row["l"]))
                    except Exception as e:
                        result.index_failed += 1
                        result.errors.append(f"LSR {row['l'].get('id')} is malformed: {e}")
            indexed = await self.index_batch_to_elasticsearch(chunk)
            indexed_any = indexed_any or indexed.succeeded > 0
            if indexed.failed:
                result.index_failed += indexed.failed
                result.errors.append(
                    f"Search index: {indexed.failed} of {len(chunk)} LSRs of batch "
                    f"{i // batch_size} not indexed ({'; '.join(indexed.errors[:1])}); "
                    "rebuild the index with `lexicon reindex`"
                )
                logger.warning(result.errors[-1])

        if indexed_any:
            # Make the batch visible to form searches now; a search made
            # before the scheduled refresh would miss it and be cached.
            await self._refresh_elasticsearch()

        logger.info(
            f"Batch write: {result.succeeded} succeeded, "
            f"{result.failed} failed out of {len(lsrs)}"
        )
        return result

    async def ensure_schema(self) -> None:
        """Create the Neo4j constraints and indexes the queries rely on.

        Idempotent. Without these, id lookups and per-language form
        lookups are full label scans.
        """
        statements = [
            "CREATE CONSTRAINT lsr_id_unique IF NOT EXISTS FOR (l:LSR) REQUIRE l.id IS UNIQUE",
            "CREATE INDEX lsr_language_form IF NOT EXISTS "
            "FOR (l:LSR) ON (l.language_code, l.form_normalized)",
            "CREATE INDEX lsr_form_normalized IF NOT EXISTS FOR (l:LSR) ON (l.form_normalized)",
            "CREATE INDEX lsr_language_code IF NOT EXISTS FOR (l:LSR) ON (l.language_code)",
        ]

        async def _create() -> None:
            async with self.db.neo4j_session() as session:
                for statement in statements:
                    result = await session.run(Neo4jQuery(statement, timeout=READ_TIMEOUT_SECONDS))
                    await result.consume()

        try:
            await asyncio.wait_for(
                _create(), len(statements) * READ_TIMEOUT_SECONDS + _CLIENT_DEADLINE_GRACE_SECONDS
            )
        except RuntimeError as e:
            raise DatabaseError(message=GRAPH_UNAVAILABLE) from e
        except Exception as e:
            raise database_error(e, "Schema creation") from e

    async def create_relationships_batch(
        self,
        relationships: list[dict[str, Any]],
        batch_size: int = 200,
    ) -> BatchResult:
        """Create relationship edges in batch using UNWIND.

        Each relationship dict must have:
        - source_id: str (UUID of source LSR)
        - target_id: str (UUID of target LSR)
        - type: str (DESCENDS_FROM, BORROWED_FROM, COGNATE_OF, etc.)
        - confidence: float (0-1)
        - evidence: str (optional)

        Edges are merged, so re-running a load updates them in place. An
        edge whose type is not allowed, or whose source or target LSR does
        not exist, is not written: it counts as failed, with an error naming
        its ids.

        Args:
            relationships: List of relationship dicts.
            batch_size: Number of relationships per transaction.

        Returns:
            BatchResult with success/failure counts.
        """
        result = BatchResult()

        # Group by relationship type for type-specific MERGE queries
        by_type: dict[str, list[dict[str, Any]]] = {}
        for rel in relationships:
            rel_type = rel.get("type", "RELATED_TO")
            if rel_type not in VALID_RELATIONSHIP_TYPES:
                logger.warning(f"Skipping invalid relationship type: {rel_type!r}")
                result.failed += 1
                result.errors.append(
                    f"{rel_type!r} {rel.get('source_id')} -> {rel.get('target_id')}: "
                    "invalid relationship type"
                )
                continue
            by_type.setdefault(rel_type, []).append(rel)

        for rel_type, rels in by_type.items():
            # Rows whose endpoints are missing come back (by index in the
            # batch) instead of silently matching nothing
            query = f"""
            UNWIND range(0, size($batch) - 1) AS i
            WITH i, $batch[i] AS rel
            OPTIONAL MATCH (source:LSR {{id: rel.source_id}})
            OPTIONAL MATCH (target:LSR {{id: rel.target_id}})
            FOREACH (_ IN CASE WHEN source IS NULL OR target IS NULL THEN [] ELSE [1] END |
                MERGE (source)-[r:{rel_type}]->(target)
                ON CREATE SET r.created_at = datetime()
                SET r.confidence = rel.confidence,
                    r.evidence = rel.evidence
            )
            WITH i, source IS NOT NULL AS has_source, target IS NOT NULL AS has_target
            WHERE NOT (has_source AND has_target)
            RETURN DISTINCT i, has_source, has_target
            """

            for i in range(0, len(rels), batch_size):
                chunk = rels[i : i + batch_size]
                batch_params = [
                    {
                        "source_id": str(r["source_id"]),
                        "target_id": str(r["target_id"]),
                        "confidence": r.get("confidence", 0.5),
                        "evidence": r.get("evidence", ""),
                    }
                    for r in chunk
                ]

                try:
                    missing = await self._run(query, {"batch": batch_params})
                except Exception as e:
                    error = database_error(e, "Relationship batch write")
                    result.failed += len(chunk)
                    result.errors.append(f"Batch {rel_type} at offset {i}: {error.message}")
                    continue

                result.succeeded += len(chunk) - len(missing)
                result.failed += len(missing)
                for row in missing:
                    rel = batch_params[row["i"]]
                    ends = [
                        end
                        for end, found in (
                            ("source", row["has_source"]),
                            ("target", row["has_target"]),
                        )
                        if not found
                    ]
                    result.errors.append(
                        f"{rel_type} {rel['source_id']} -> {rel['target_id']}: "
                        f"no LSR with the {' or '.join(ends)} id"
                    )

        logger.info(
            f"Batch relationships: {result.succeeded} created, "
            f"{result.failed} failed out of {len(relationships)}"
        )
        return result

    async def get_statistics(self) -> dict[str, Any]:
        """Get database statistics: node counts, relationship counts, etc.

        Returns:
            Dict with counts and distribution info.

        Raises:
            DatabaseError: If Neo4j is unavailable or a count fails, rather
                than returning partial counts.
        """
        stats: dict[str, Any] = {}

        async def _collect() -> None:
            async with self.db.neo4j_session() as session:
                # Total LSR count
                res = await session.run("MATCH (l:LSR) RETURN count(l) AS total")
                record = await res.single()
                stats["total_lsrs"] = record["total"] if record else 0

                # Count by language
                res = await session.run(
                    "MATCH (l:LSR) RETURN l.language_code AS lang, count(l) AS cnt "
                    "ORDER BY cnt DESC LIMIT 50"
                )
                records = await res.fetch(50)
                stats["by_language"] = {r["lang"]: r["cnt"] for r in records if r["lang"]}

                # Relationship counts
                for rel_type in [
                    "DESCENDS_FROM",
                    "BORROWED_FROM",
                    "COGNATE_OF",
                    "SHIFTED_TO",
                    "MERGED_WITH",
                ]:
                    res = await session.run(f"MATCH ()-[r:{rel_type}]->() RETURN count(r) AS cnt")
                    record = await res.single()
                    stats[f"rel_{rel_type.lower()}"] = record["cnt"] if record else 0

                stats["total_relationships"] = sum(
                    stats.get(f"rel_{t.lower()}", 0)
                    for t in [
                        "DESCENDS_FROM",
                        "BORROWED_FROM",
                        "COGNATE_OF",
                        "SHIFTED_TO",
                        "MERGED_WITH",
                    ]
                )

        try:
            # Several counts in one session: one deadline for all of them
            await asyncio.wait_for(
                _collect(), STATISTICS_TIMEOUT_SECONDS + _CLIENT_DEADLINE_GRACE_SECONDS
            )
        except Exception as e:
            raise database_error(e, "Statistics retrieval") from e

        return stats

    # -------------------------------------------------------------------------
    # Search (Neo4j + Elasticsearch for form queries)
    # -------------------------------------------------------------------------

    async def search(
        self,
        form: str | None = None,
        language: str | None = None,
        date_start: int | None = None,
        date_end: int | None = None,
        semantic_field: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[LSR], int]:
        """
        Search for LSRs matching the given criteria.

        Both backends apply the same filters:
        - form: substring of the written or normalized form (case- and
          diacritic-insensitive on the normalized form). With Elasticsearch
          connected, near misses (typos) also match and results are ranked
          by relevance.
        - language: exact language code.
        - date_start / date_end: the LSR was in use at some point within the
          range, i.e. first attested no later than date_end and, unless still
          in use (no date_end), last attested no earlier than date_start.
          Undated LSRs never match a date filter.
        - semantic_field: exact member of the LSR's semantic_fields.

        Results are ordered by relevance (form queries on Elasticsearch),
        then confidence, form and id, so pagination is stable.
        Elasticsearch is only used when a form is given: filter-only queries
        go to Neo4j, the source of truth.

        Args:
            form: Optional form to search for.
            language: Optional language code (as stored, e.g. "eng", "gem-pro").
            date_start: Optional start year of the range.
            date_end: Optional end year of the range.
            semantic_field: Optional semantic field to filter by.
            limit: Maximum number of results to return.
            offset: Number of results to skip.

        Returns:
            Tuple of (list of matching LSRs, total count).

        Raises:
            DatabaseError: If the search fails or Neo4j times out (see _run).
        """
        if form and self._has_elasticsearch():
            try:
                return await self._search_elasticsearch(
                    form=form,
                    language=language,
                    date_start=date_start,
                    date_end=date_end,
                    semantic_field=semantic_field,
                    limit=limit,
                    offset=offset,
                )
            except DatabaseError:
                # Neo4j failed to load the hits; a Neo4j search would only
                # wait for it again
                raise
            except Exception as e:
                logger.warning(f"Elasticsearch search failed, falling back to Neo4j: {e}")
                self.search_degraded = True
        elif form and getattr(getattr(self.db, "config", None), "elasticsearch_configured", False):
            # Elasticsearch is configured but not (yet) connected: this search
            # has no fuzzy matches either
            self.search_degraded = True

        return await self._search_neo4j(
            form=form,
            language=language,
            date_start=date_start,
            date_end=date_end,
            semantic_field=semantic_field,
            limit=limit,
            offset=offset,
        )

    async def _search_neo4j(
        self,
        form: str | None = None,
        language: str | None = None,
        date_start: int | None = None,
        date_end: int | None = None,
        semantic_field: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[LSR], int]:
        """Search using Neo4j CONTAINS queries (semantics documented on search())."""
        where_clauses = []
        params: dict[str, Any] = {"limit": limit, "offset": offset}

        if form:
            where_clauses.append(
                "(l.form_normalized CONTAINS $form_normalized OR l.form_orthographic CONTAINS $form)"
            )
            params["form"] = form
            params["form_normalized"] = LSR._normalize(form)

        if language:
            where_clauses.append("l.language_code = $language")
            params["language"] = language

        if date_start is not None or date_end is not None:
            where_clauses.append("l.date_start IS NOT NULL")

        if date_end is not None:
            where_clauses.append("l.date_start <= $date_end")
            params["date_end"] = date_end

        if date_start is not None:
            where_clauses.append("(l.date_end IS NULL OR l.date_end >= $date_start)")
            params["date_start"] = date_start

        if semantic_field:
            where_clauses.append("$semantic_field IN l.semantic_fields")
            params["semantic_field"] = semantic_field

        where_clause = " AND ".join(where_clauses) if where_clauses else "TRUE"

        count_query = f"""
        MATCH (l:LSR)
        WHERE {where_clause}
        RETURN count(l) as total
        """

        search_query = f"""
        MATCH (l:LSR)
        WHERE {where_clause}
        WITH l
        ORDER BY l.confidence_overall DESC, l.form_orthographic, l.id
        SKIP $offset
        LIMIT $limit
        RETURN l, {_RELATIONSHIP_COLUMNS}
        """

        count_rows = await self._read(count_query, params, "LSR search")
        records = await self._read(search_query, params, "LSR search")
        try:
            lsrs = [self._record_to_lsr(record) for record in records]
        except Exception as e:
            raise database_error(e, "LSR search") from e
        return lsrs, count_rows[0]["total"] if count_rows else 0

    @staticmethod
    def _es_filters(
        language: str | None,
        date_start: int | None,
        date_end: int | None,
        semantic_field: str | None,
    ) -> list[dict[str, Any]]:
        """Elasticsearch filters equivalent to the WHERE clauses of _search_neo4j."""
        filters: list[dict[str, Any]] = []
        if language:
            filters.append({"term": {"language_code": language}})
        if date_start is not None or date_end is not None:
            filters.append({"exists": {"field": "date_start"}})
        if date_end is not None:
            filters.append({"range": {"date_start": {"lte": date_end}}})
        if date_start is not None:
            filters.append(
                {
                    "bool": {
                        "should": [
                            {"range": {"date_end": {"gte": date_start}}},
                            {"bool": {"must_not": {"exists": {"field": "date_end"}}}},
                        ],
                        "minimum_should_match": 1,
                    }
                }
            )
        if semantic_field:
            filters.append({"term": {"semantic_fields": semantic_field}})
        return filters

    async def _search_elasticsearch(
        self,
        form: str | None = None,
        language: str | None = None,
        date_start: int | None = None,
        date_end: int | None = None,
        semantic_field: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[LSR], int]:
        """Search using Elasticsearch (semantics documented on search()).

        Every Neo4j match is also an Elasticsearch match (the same substring
        tests, as wildcard queries); fuzzy matching on the forms adds near
        misses. Results are then loaded from Neo4j in one query.
        """
        sort: list[dict[str, Any]] = [
            {"confidence_overall": {"order": "desc"}},
            {"form_orthographic.raw": {"order": "asc"}},
            {"id": {"order": "asc"}},
        ]
        query: dict[str, Any] = {
            "bool": {"filter": self._es_filters(language, date_start, date_end, semantic_field)}
        }
        if form:
            normalized = LSR._normalize(form)
            query["bool"]["should"] = [
                {"wildcard": {"form_normalized": {"value": f"*{_escape_wildcard(normalized)}*"}}},
                {"wildcard": {"form_orthographic.raw": {"value": f"*{_escape_wildcard(form)}*"}}},
                {
                    "multi_match": {
                        "query": form,
                        "fields": ["form_orthographic^3", "form_normalized^2"],
                        "operator": "and",
                        "fuzziness": "AUTO",
                        "prefix_length": 1,
                    }
                },
            ]
            query["bool"]["minimum_should_match"] = 1
            sort.insert(0, {"_score": {"order": "desc"}})

        es = self.db.elasticsearch
        response = await es.search(
            index=ES_INDEX_NAME,
            query=query,
            sort=sort,
            from_=offset,
            size=limit,
            track_total_hits=True,
            source=["id"],
        )

        total = response["hits"]["total"]["value"]
        lsr_ids = [hit["_source"]["id"] for hit in response["hits"]["hits"]]
        if not lsr_ids:
            return [], total

        # Load the full records from Neo4j (the source of truth) in ES order.
        records = await self._read(
            f"MATCH (l:LSR) WHERE l.id IN $ids RETURN l, {_RELATIONSHIP_COLUMNS}",
            {"ids": lsr_ids},
            "Search result lookup",
        )
        by_id = {record["l"]["id"]: self._record_to_lsr(record) for record in records}
        missing = [lsr_id for lsr_id in lsr_ids if lsr_id not in by_id]
        if missing:
            logger.warning(f"{len(missing)} LSRs in Elasticsearch but not in Neo4j: {missing[:5]}")
        return [by_id[lsr_id] for lsr_id in lsr_ids if lsr_id in by_id], total

    # -------------------------------------------------------------------------
    # Elasticsearch helpers
    # -------------------------------------------------------------------------

    def _has_elasticsearch(self) -> bool:
        """Check if Elasticsearch is connected."""
        try:
            _ = self.db.elasticsearch
            return True
        except RuntimeError:
            return False

    async def ensure_elasticsearch_index(self) -> bool:
        """Create the Elasticsearch index with its mapping, or bring an existing
        index's mapping up to date (adding fields such as semantic_fields).

        An index that Elasticsearch auto-created with dynamic mappings maps
        the codes as analyzed text, so keyword filters silently match nothing;
        such an index is rejected here and has to be rebuilt with
        reindex_all_to_elasticsearch (`lexicon reindex`).

        The check runs once per client and process; later calls return True
        without asking Elasticsearch (writes call this before every write).

        Returns:
            True if the index is usable, False otherwise.
        """
        if not self._has_elasticsearch():
            return False
        es = self.db.elasticsearch
        if es in _es_index_ready:
            return True

        try:
            if await es.indices.exists(index=ES_INDEX_NAME):
                await es.indices.put_mapping(
                    index=ES_INDEX_NAME, properties=ES_INDEX_SETTINGS["mappings"]["properties"]
                )
            else:
                await es.indices.create(
                    index=ES_INDEX_NAME,
                    settings=ES_INDEX_SETTINGS["settings"],
                    mappings=ES_INDEX_SETTINGS["mappings"],
                )
                logger.info(f"Created Elasticsearch index: {ES_INDEX_NAME}")
        except Exception as e:
            logger.warning(
                f"Elasticsearch index {ES_INDEX_NAME} is unusable ({e}); "
                "rebuild it with `lexicon reindex`"
            )
            return False
        _es_index_ready.add(es)
        return True

    @staticmethod
    def _es_document(lsr: LSR) -> dict[str, Any]:
        """Build the Elasticsearch document for an LSR."""
        return {
            "id": str(lsr.id),
            "form_orthographic": lsr.form_orthographic,
            "form_normalized": lsr.form_normalized,
            "form_phonetic": lsr.form_phonetic,
            "language_code": lsr.language_code,
            "language_name": lsr.language_name,
            "language_family": lsr.language_family,
            "definition_primary": lsr.definition_primary,
            "date_start": lsr.date_start,
            "date_end": lsr.date_end,
            "period_label": lsr.period_label,
            "confidence_overall": lsr.confidence_overall,
            "reconstruction_flag": lsr.reconstruction_flag,
            "source_databases": lsr.source_databases,
            "semantic_fields": lsr.semantic_fields,
        }

    async def _index_to_elasticsearch(self, lsr: LSR) -> None:
        """Index an LSR document in Elasticsearch.

        Refreshes the shard, so a form search made right after the write
        already sees it; otherwise that search would miss the record and the
        miss would be cached. Only single-record API writes do this (a cheap
        refresh); bulk writes go through index_batch_to_elasticsearch.
        Waiting for the scheduled refresh instead ("wait_for") would add up
        to a second to every create and delete.
        """
        # Never let the first write auto-create the index with dynamic mappings.
        if not await self.ensure_elasticsearch_index():
            return

        try:
            es = self.db.elasticsearch
            await es.index(
                index=ES_INDEX_NAME,
                id=str(lsr.id),
                document=self._es_document(lsr),
                refresh=True,
            )
        except Exception as e:
            # Don't fail the main operation if ES indexing fails
            logger.warning(
                f"Elasticsearch index failed for {lsr.id} ({e}); form searches miss it "
                "until the index is rebuilt with `lexicon reindex`"
            )

    async def index_batch_to_elasticsearch(self, lsrs: list[LSR]) -> BatchResult:
        """Bulk-index LSRs in Elasticsearch, creating the index if needed.

        Form searches use Elasticsearch whenever it is connected, so anything
        written to Neo4j must also land here or those searches miss it.

        Returns:
            BatchResult: documents indexed and rejected, with a sample of the
            errors (all zero when Elasticsearch is not connected). Failures
            are not logged here; callers report them.
        """
        result = BatchResult()
        if not lsrs or not self._has_elasticsearch():
            return result
        if not await self.ensure_elasticsearch_index():
            result.failed = len(lsrs)
            result.errors.append(f"Elasticsearch index {ES_INDEX_NAME} is unusable")
            return result

        from elasticsearch.helpers import async_bulk

        actions = [
            {"_index": ES_INDEX_NAME, "_id": str(lsr.id), "_source": self._es_document(lsr)}
            for lsr in lsrs
        ]
        try:
            indexed, errors = await async_bulk(self.db.elasticsearch, actions, raise_on_error=False)
        except Exception as e:
            result.failed = len(lsrs)
            result.errors.append(f"Elasticsearch bulk request failed: {e}")
            return result

        result.succeeded = int(indexed)
        result.failed = len(lsrs) - result.succeeded
        if result.failed:
            result.errors.append(
                f"Elasticsearch rejected {result.failed} of {len(lsrs)} documents "
                f"(e.g. {_bulk_error_reason(errors)})"
            )
        return result

    async def _refresh_elasticsearch(self) -> None:
        """Make every document indexed so far visible to searches."""
        try:
            await self.db.elasticsearch.indices.refresh(index=ES_INDEX_NAME)
        except Exception as e:
            logger.warning(f"Elasticsearch refresh failed: {e}")

    async def _remove_from_elasticsearch(self, lsr_id: UUID) -> None:
        """Remove an LSR document from Elasticsearch (visible to the next search)."""
        if not self._has_elasticsearch():
            return

        from elasticsearch import NotFoundError as DocumentNotFoundError

        try:
            es = self.db.elasticsearch
            await es.delete(index=ES_INDEX_NAME, id=str(lsr_id), refresh=True)
        except DocumentNotFoundError:
            return  # it was never indexed
        except Exception as e:
            logger.warning(
                f"Elasticsearch delete failed for {lsr_id} ({e}); searches may count it "
                "until the index is rebuilt with `lexicon reindex`"
            )

    async def reindex_all_to_elasticsearch(self) -> BatchResult:
        """Rebuild the Elasticsearch index from Neo4j, the source of truth.

        Every LSR is (re)indexed, reading Neo4j in pages ordered by id so the
        graph is never held in memory at once; then documents whose LSR no
        longer exists in Neo4j are deleted. Afterwards the index holds
        exactly the LSRs of the graph, unless failures are reported. An
        existing index whose mapping cannot be updated is dropped and
        recreated first.

        Returns:
            BatchResult: LSRs indexed and not indexed. errors starts with a
            summary whenever anything failed (followed by samples).
        """
        result = BatchResult()

        if not self._has_elasticsearch():
            result.errors.append("Elasticsearch not connected")
            return result

        if not await self.ensure_elasticsearch_index():
            try:
                await self.db.elasticsearch.indices.delete(
                    index=ES_INDEX_NAME, ignore_unavailable=True
                )
            except Exception as e:
                logger.warning(f"Could not drop Elasticsearch index {ES_INDEX_NAME}: {e}")
            if not await self.ensure_elasticsearch_index():
                result.errors.append(f"Could not create Elasticsearch index {ES_INDEX_NAME}")
                return result

        samples: list[str] = []
        removed = 0
        try:
            rows = await self._read(
                "MATCH (l:LSR) WHERE l.id IS NULL RETURN count(l) AS n", {}, "Reindex"
            )
            without_id = rows[0]["n"] if rows else 0
            if without_id:
                result.failed += without_id
                samples.append(f"{without_id} LSR nodes have no id")

            after = ""
            while True:
                rows = await self._read(
                    "MATCH (l:LSR) WHERE l.id > $after RETURN l ORDER BY l.id LIMIT $limit",
                    {"after": after, "limit": REINDEX_PAGE_SIZE},
                    "Reindex",
                )
                if not rows:
                    break
                after = rows[-1]["l"]["id"]
                lsrs = []
                for row in rows:
                    try:
                        lsrs.append(self._node_to_lsr(row["l"]))
                    except Exception as e:
                        result.failed += 1
                        samples.append(f"LSR {row['l'].get('id')} is malformed: {e}")
                page = await self.index_batch_to_elasticsearch(lsrs)
                result.succeeded += page.succeeded
                result.failed += page.failed
                samples.extend(page.errors)

            removed = await self._prune_elasticsearch()
        except Exception as e:
            result.errors.append(f"Reindex failed: {e}")

        if result.failed:
            result.errors.insert(
                0,
                f"{result.failed} of {result.succeeded + result.failed} LSRs not indexed",
            )
        result.errors.extend(list(dict.fromkeys(samples))[:5])
        logger.log(
            logging.WARNING if result.errors else logging.INFO,
            f"ES reindex: {result.succeeded} indexed, {result.failed} failed, "
            f"{removed} documents of deleted LSRs removed",
        )
        return result

    async def _prune_elasticsearch(self) -> int:
        """Delete the documents whose LSR is not in Neo4j; returns how many."""
        from elasticsearch.helpers import async_bulk

        es = self.db.elasticsearch
        await es.indices.refresh(index=ES_INDEX_NAME)
        removed = 0
        search_after: list[Any] | None = None
        while True:
            page = await es.search(
                index=ES_INDEX_NAME,
                size=REINDEX_PAGE_SIZE,
                sort=[{"id": "asc"}],
                source=False,
                **({"search_after": search_after} if search_after else {}),
            )
            hits = page["hits"]["hits"]
            if not hits:
                break
            search_after = hits[-1]["sort"]
            ids = [hit["_id"] for hit in hits]
            rows = await self._read(
                "MATCH (l:LSR) WHERE l.id IN $ids RETURN l.id AS id", {"ids": ids}, "Reindex"
            )
            present = {row["id"] for row in rows}
            stale = [lsr_id for lsr_id in ids if lsr_id not in present]
            if stale:
                deleted, _ = await async_bulk(
                    es,
                    [{"_op_type": "delete", "_index": ES_INDEX_NAME, "_id": i} for i in stale],
                    raise_on_error=False,
                )
                removed += int(deleted)
        await es.indices.refresh(index=ES_INDEX_NAME)
        return removed

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    def _lsr_to_params(self, lsr: LSR) -> dict[str, Any]:
        """Convert an LSR to the Neo4j property map stored on its node."""
        return {
            "id": str(lsr.id),
            "version": lsr.version,
            "form_orthographic": lsr.form_orthographic,
            "form_phonetic": lsr.form_phonetic,
            "form_normalized": lsr.form_normalized,
            "language_code": lsr.language_code,
            "language_name": lsr.language_name,
            "language_family": lsr.language_family,
            "language_branch": lsr.language_branch,
            "period_label": lsr.period_label,
            "date_start": lsr.date_start,
            "date_end": lsr.date_end,
            "date_confidence": lsr.date_confidence,
            "date_source": lsr.date_source.value,
            "definition_primary": lsr.definition_primary,
            "definitions_alternate": lsr.definitions_alternate,
            "semantic_vector": lsr.semantic_vector,
            "semantic_fields": lsr.semantic_fields,
            "conceptual_domain": lsr.conceptual_domain,
            "etymology_text": lsr.etymology_text,
            "register": lsr.register.value if lsr.register else None,
            "frequency_score": lsr.frequency_score,
            "frequency_source": lsr.frequency_source,
            "part_of_speech": lsr.part_of_speech,
            "reconstruction_flag": lsr.reconstruction_flag,
            "confidence_overall": lsr.confidence_overall,
            "source_databases": lsr.source_databases,
            "human_validated": lsr.human_validated,
            "validation_notes": lsr.validation_notes,
        }

    def _node_to_lsr(self, node: Any) -> LSR:
        """Convert a Neo4j node to an LSR object."""
        from src.models.lsr import DateSource, Register

        props = dict(node)

        # Handle enum conversions
        date_source = DateSource(props.get("date_source") or "ATTESTED")
        register = Register(props["register"]) if props.get("register") else None

        def _list(key: str) -> list[Any]:
            return list(props.get(key) or [])

        # Stored timestamps; a node written without them (e.g. by hand) keeps
        # the model defaults.
        timestamps = {
            key: value
            for key in ("created_at", "updated_at")
            if (value := _to_datetime(props.get(key))) is not None
        }

        return LSR(
            **timestamps,
            id=UUID(props["id"]),
            version=props.get("version") or 1,
            form_orthographic=props.get("form_orthographic") or "",
            form_phonetic=props.get("form_phonetic") or "",
            form_normalized=props.get("form_normalized") or "",
            language_code=props.get("language_code") or "",
            language_name=props.get("language_name") or "",
            language_family=props.get("language_family") or "",
            language_branch=_list("language_branch"),
            period_label=props.get("period_label") or "",
            date_start=props.get("date_start"),
            date_end=props.get("date_end"),
            date_confidence=props.get("date_confidence", 1.0),
            date_source=date_source,
            definition_primary=props.get("definition_primary") or "",
            definitions_alternate=_list("definitions_alternate"),
            semantic_vector=_list("semantic_vector"),
            semantic_fields=_list("semantic_fields"),
            conceptual_domain=_list("conceptual_domain"),
            etymology_text=props.get("etymology_text") or "",
            register=register,
            frequency_score=props.get("frequency_score", 0.0),
            frequency_source=props.get("frequency_source") or "",
            part_of_speech=_list("part_of_speech"),
            reconstruction_flag=props.get("reconstruction_flag", False),
            confidence_overall=props.get("confidence_overall", 1.0),
            source_databases=_list("source_databases"),
            human_validated=props.get("human_validated", False),
            validation_notes=props.get("validation_notes") or "",
        )

    def _record_to_lsr(self, record: Any) -> LSR:
        """Convert a record of `l` plus _RELATIONSHIP_COLUMNS to an LSR.

        The relationship fields hold the directly linked LSRs (one hop), at
        most MAX_LINKED_IDS each (the lowest ids); the full counts are kept
        in relationship_counts. With several donors, loan_source_id is the
        most confident one; GET /lsr/{id}/borrowings lists them all.
        """
        lsr = self._node_to_lsr(record["l"])
        lsr.ancestor_ids = _uuid_list(record["ancestor_ids"])
        lsr.descendant_ids = _uuid_list(record["descendant_ids"])
        lsr.cognate_ids = _uuid_list(record["cognate_ids"])
        lsr.loan_target_ids = _uuid_list(record["loan_target_ids"])
        source_ids = _uuid_list(record["loan_source_ids"])
        lsr.loan_source_id = source_ids[0] if source_ids else None
        self.relationship_counts[str(lsr.id)] = {
            "ancestors": record["ancestor_count"],
            "descendants": record["descendant_count"],
            "cognates": record["cognate_count"],
            "loan_sources": record["loan_source_count"],
            "loan_targets": record["loan_target_count"],
        }
        return lsr

    def relationship_summary(self, lsr_id: UUID) -> dict[str, Any]:
        """Counts of an LSR's direct relationships, for API responses.

        Returns:
            {"relationship_counts": {...}, "relationship_ids_truncated": bool}
            for an LSR read through this repository (get_by_id, search);
            relationship_ids_truncated says whether any id list was capped at
            MAX_LINKED_IDS. Empty for any other LSR.
        """
        counts = self.relationship_counts.get(str(lsr_id))
        if counts is None:
            return {}
        listed = ("ancestors", "descendants", "cognates", "loan_targets")
        return {
            "relationship_counts": dict(counts),
            "relationship_ids_truncated": any(counts[key] > MAX_LINKED_IDS for key in listed),
        }
