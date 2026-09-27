"""Write ingestion output to the Neo4j graph.

Ingestion builds LSRs and relationships in memory; this module persists
them so the API and analyses can see them. Writes are idempotent upserts
keyed on LSR id (ids are derived from source records), so re-running an
ingestion updates the graph instead of duplicating it. LSRs the caller
marks as placeholders (donors and ancestors an ingestion only knows by name)
are written fill-only, so they never blank what another run stored under
their id. When Elasticsearch is configured and reachable, the written LSRs
are also indexed there, because API search prefers Elasticsearch when
connected. Afterwards the API's cached search results and LSR records are
dropped from Redis (when reachable), so the API does not serve
pre-ingestion responses until they expire.
"""

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from src.models.lsr import LSR
from src.repositories.lsr_repository import LSRRepository
from src.utils.cache import LSR_CACHE_TTL, invalidate_graph_caches
from src.utils.db import DatabaseManager

logger = logging.getLogger(__name__)


class GraphUnavailableError(RuntimeError):
    """Raised when Neo4j cannot be reached for a live (non dry-run) ingestion."""


@dataclass
class GraphWriteResult:
    """Counts from one graph write."""

    lsrs_written: int = 0
    lsrs_failed: int = 0
    relationships_written: int = 0
    relationships_failed: int = 0
    # Elasticsearch was reachable and accepted every written LSR
    search_index_available: bool = False
    # LSRs written to Neo4j that Elasticsearch did not index
    search_index_failed: int = 0
    # The API's response cache in Redis was cleared after the write
    api_cache_cleared: bool = False
    errors: list[str] = field(default_factory=list)


async def write_to_graph(
    lsrs: list[LSR],
    relationships: list[dict[str, Any]],
    db: DatabaseManager | None = None,
    placeholder_ids: Iterable[str] | None = None,
) -> GraphWriteResult:
    """Persist LSRs and relationship edges.

    Args:
        lsrs: LSRs to upsert.
        relationships: Edge dicts with source_id, target_id, type,
            confidence and evidence (see LSRRepository.create_relationships_batch).
        db: Connected DatabaseManager to use. When omitted, one is created
            from the environment/.env and closed afterwards.
        placeholder_ids: Ids (as strings) of the LSRs in `lsrs` to write
            fill-only: a new node gets all their properties, an existing one
            keeps every non-empty property and only gains the missing ones
            (the dating only as a whole, onto an undated node), and its
            source_databases become the union of both.

    Returns:
        GraphWriteResult with counts.

    Raises:
        GraphUnavailableError: If Neo4j is not reachable.
    """
    owns_db = db is None
    manager = db or DatabaseManager()
    try:
        if owns_db:
            if not await manager.connect_neo4j():
                error = manager.get_connection_errors().get("neo4j", "unknown error")
                raise GraphUnavailableError(
                    f"Cannot reach Neo4j at {manager.config.neo4j_uri}: {error}. "
                    "Start it with `docker compose up -d neo4j` and check NEO4J_URI / "
                    "NEO4J_PASSWORD in .env, or use --dry-run."
                )
            # Optional: keep the search index in step with the graph
            if manager.config.elasticsearch_configured and not (
                await manager.connect_elasticsearch(quiet=True)
            ):
                logger.warning(
                    "Elasticsearch is configured but not reachable; writing to Neo4j "
                    "only. Run `lexicon reindex` once it is up."
                )

        repo = LSRRepository(manager)
        result = GraphWriteResult(search_index_available=repo._has_elasticsearch())
        await repo.ensure_schema()

        fill_only = {str(lsr_id) for lsr_id in placeholder_ids or ()}
        for batch, placeholders in (
            ([lsr for lsr in lsrs if str(lsr.id) not in fill_only], False),
            ([lsr for lsr in lsrs if str(lsr.id) in fill_only], True),
        ):
            if not batch:
                continue
            node_result = await repo.create_batch(batch, fill_only=placeholders)
            result.lsrs_written += node_result.succeeded
            result.lsrs_failed += node_result.failed
            result.search_index_failed += node_result.index_failed
            result.errors.extend(node_result.errors)
        result.search_index_available = (
            result.search_index_available and result.search_index_failed == 0
        )

        if relationships:
            edge_result = await repo.create_relationships_batch(relationships)
            result.relationships_written = edge_result.succeeded
            result.relationships_failed = edge_result.failed
            result.errors.extend(edge_result.errors)

        result.api_cache_cleared = await _clear_api_cache(manager, owns_db)
        return result
    finally:
        if owns_db:
            await manager.close_all()


async def _clear_api_cache(manager: DatabaseManager, owns_db: bool) -> bool:
    """Drop the API's cached search results and LSR records from Redis.

    Uses the manager's Redis client when it has one; a manager created here
    gets a short-lived client for the configured REDIS_URI. Redis is
    optional: when it cannot be reached, nothing is cleared.

    Returns:
        True if the cache was cleared.
    """
    client: Any = manager._redis_client
    if client is None and not manager.config.redis_configured:
        return False  # no Redis, so no API response cache to clear
    own_client = client is None and owns_db
    if own_client:
        try:
            import redis.asyncio as redis

            client = redis.from_url(
                manager.config.redis_uri, socket_connect_timeout=2, socket_timeout=5
            )
            await client.ping()
        except Exception as e:
            logger.info(
                f"API response cache not cleared (Redis unreachable: {e}); an API using "
                f"that cache may serve pre-ingestion results for up to {LSR_CACHE_TTL}s"
            )
            if client is not None:
                await client.aclose()
            return False
    if client is None:
        return False
    try:
        deleted = await invalidate_graph_caches(client)
        logger.info(f"Cleared {deleted} cached API responses")
        return True
    except Exception as e:
        logger.warning(f"Could not clear the API response cache in Redis: {e}")
        return False
    finally:
        if own_client:
            await client.aclose()
