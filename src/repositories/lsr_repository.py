"""Repository for LSR persistence operations using Neo4j.

Supports:
- Single and batch CRUD operations against Neo4j
- Elasticsearch indexing and full-text search (when connected)
- Redis cache integration (when connected)
- Relationship (edge) creation and batch insertion
"""

import logging
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from src.exceptions import DatabaseError, LSRNotFoundError
from src.models.lsr import LSR
from src.utils.db import DatabaseManager

logger = logging.getLogger(__name__)

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
ES_INDEX_SETTINGS = {
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
        },
    },
}


@dataclass
class BatchResult:
    """Result of a batch operation."""

    succeeded: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)


class LSRRepository:
    """Repository for LSR CRUD operations in Neo4j.

    Optionally integrates with Elasticsearch for full-text search and
    Redis for caching. Falls back to Neo4j-only when ES/Redis are
    not connected.
    """

    def __init__(self, db: DatabaseManager):
        """Initialize the repository with a database manager."""
        self.db = db

    # -------------------------------------------------------------------------
    # Single-record CRUD
    # -------------------------------------------------------------------------

    async def create(self, lsr: LSR) -> LSR:
        """
        Create a new LSR in the database.

        Args:
            lsr: The LSR to create.

        Returns:
            The created LSR with any generated fields.

        Raises:
            DatabaseError: If the creation fails.
        """
        query = """
        CREATE (l:LSR {
            id: $id,
            version: $version,
            form_orthographic: $form_orthographic,
            form_phonetic: $form_phonetic,
            form_normalized: $form_normalized,
            language_code: $language_code,
            language_name: $language_name,
            language_family: $language_family,
            period_label: $period_label,
            date_start: $date_start,
            date_end: $date_end,
            date_confidence: $date_confidence,
            date_source: $date_source,
            definition_primary: $definition_primary,
            register: $register,
            frequency_score: $frequency_score,
            reconstruction_flag: $reconstruction_flag,
            confidence_overall: $confidence_overall,
            human_validated: $human_validated,
            validation_notes: $validation_notes,
            created_at: datetime(),
            updated_at: datetime()
        })
        RETURN l
        """
        params = self._lsr_to_params(lsr)

        try:
            async with self.db.neo4j_session() as session:
                result = await session.run(query, params)
                record = await result.single()
                if record:
                    logger.info(f"Created LSR: {lsr.id}")
                    await self._index_to_elasticsearch(lsr)
                    return lsr
                raise DatabaseError(message="Failed to create LSR - no record returned")
        except RuntimeError as e:
            raise DatabaseError(message=f"Neo4j not connected: {e}") from e
        except Exception as e:
            logger.error(f"Failed to create LSR {lsr.id}: {e}")
            raise DatabaseError(message=f"Failed to create LSR: {e}") from e

    async def get_by_id(self, lsr_id: UUID) -> LSR:
        """
        Get an LSR by its ID.

        Args:
            lsr_id: The UUID of the LSR to retrieve.

        Returns:
            The LSR if found.

        Raises:
            LSRNotFoundError: If no LSR with the given ID exists.
            DatabaseError: If the query fails.
        """
        query = """
        MATCH (l:LSR {id: $id})
        RETURN l
        """

        try:
            async with self.db.neo4j_session() as session:
                result = await session.run(query, {"id": str(lsr_id)})
                record = await result.single()
                if record is None:
                    raise LSRNotFoundError(lsr_id=str(lsr_id))
                return self._node_to_lsr(record["l"])
        except LSRNotFoundError:
            raise
        except RuntimeError as e:
            raise DatabaseError(message=f"Neo4j not connected: {e}") from e
        except Exception as e:
            logger.error(f"Failed to get LSR {lsr_id}: {e}")
            raise DatabaseError(message=f"Failed to get LSR: {e}") from e

    async def update(self, lsr: LSR) -> LSR:
        """
        Update an existing LSR.

        Args:
            lsr: The LSR with updated fields.

        Returns:
            The updated LSR.

        Raises:
            LSRNotFoundError: If no LSR with the given ID exists.
            DatabaseError: If the update fails.
        """
        query = """
        MATCH (l:LSR {id: $id})
        SET l.version = $version,
            l.form_orthographic = $form_orthographic,
            l.form_phonetic = $form_phonetic,
            l.form_normalized = $form_normalized,
            l.language_code = $language_code,
            l.language_name = $language_name,
            l.language_family = $language_family,
            l.period_label = $period_label,
            l.date_start = $date_start,
            l.date_end = $date_end,
            l.date_confidence = $date_confidence,
            l.date_source = $date_source,
            l.definition_primary = $definition_primary,
            l.register = $register,
            l.frequency_score = $frequency_score,
            l.reconstruction_flag = $reconstruction_flag,
            l.confidence_overall = $confidence_overall,
            l.human_validated = $human_validated,
            l.validation_notes = $validation_notes,
            l.updated_at = datetime()
        RETURN l
        """
        params = self._lsr_to_params(lsr)

        try:
            async with self.db.neo4j_session() as session:
                result = await session.run(query, params)
                record = await result.single()
                if record is None:
                    raise LSRNotFoundError(lsr_id=str(lsr.id))
                logger.info(f"Updated LSR: {lsr.id}")
                await self._index_to_elasticsearch(lsr)
                return lsr
        except LSRNotFoundError:
            raise
        except RuntimeError as e:
            raise DatabaseError(message=f"Neo4j not connected: {e}") from e
        except Exception as e:
            logger.error(f"Failed to update LSR {lsr.id}: {e}")
            raise DatabaseError(message=f"Failed to update LSR: {e}") from e

    async def delete(self, lsr_id: UUID) -> bool:
        """
        Delete an LSR by its ID.

        Args:
            lsr_id: The UUID of the LSR to delete.

        Returns:
            True if the LSR was deleted.

        Raises:
            LSRNotFoundError: If no LSR with the given ID exists.
            DatabaseError: If the deletion fails.
        """
        query = """
        MATCH (l:LSR {id: $id})
        DETACH DELETE l
        RETURN count(l) as deleted
        """

        try:
            async with self.db.neo4j_session() as session:
                result = await session.run(query, {"id": str(lsr_id)})
                record = await result.single()
                if record and record["deleted"] > 0:
                    logger.info(f"Deleted LSR: {lsr_id}")
                    await self._remove_from_elasticsearch(lsr_id)
                    return True
                raise LSRNotFoundError(lsr_id=str(lsr_id))
        except LSRNotFoundError:
            raise
        except RuntimeError as e:
            raise DatabaseError(message=f"Neo4j not connected: {e}") from e
        except Exception as e:
            logger.error(f"Failed to delete LSR {lsr_id}: {e}")
            raise DatabaseError(message=f"Failed to delete LSR: {e}") from e

    # -------------------------------------------------------------------------
    # Batch operations
    # -------------------------------------------------------------------------

    async def create_batch(self, lsrs: list[LSR], batch_size: int = 100) -> BatchResult:
        """Create multiple LSRs in batches using UNWIND.

        Uses Neo4j UNWIND for efficient batch insertion instead of
        individual CREATE statements.

        Args:
            lsrs: List of LSR objects to create.
            batch_size: Number of LSRs per Neo4j transaction.

        Returns:
            BatchResult with success/failure counts.
        """
        result = BatchResult()

        query = """
        UNWIND $batch AS props
        CREATE (l:LSR {
            id: props.id,
            version: props.version,
            form_orthographic: props.form_orthographic,
            form_phonetic: props.form_phonetic,
            form_normalized: props.form_normalized,
            language_code: props.language_code,
            language_name: props.language_name,
            language_family: props.language_family,
            period_label: props.period_label,
            date_start: props.date_start,
            date_end: props.date_end,
            date_confidence: props.date_confidence,
            date_source: props.date_source,
            definition_primary: props.definition_primary,
            register: props.register,
            frequency_score: props.frequency_score,
            reconstruction_flag: props.reconstruction_flag,
            confidence_overall: props.confidence_overall,
            human_validated: props.human_validated,
            validation_notes: props.validation_notes,
            created_at: datetime(),
            updated_at: datetime()
        })
        RETURN count(l) AS created
        """

        # Process in chunks
        for i in range(0, len(lsrs), batch_size):
            chunk = lsrs[i : i + batch_size]
            batch_params = [self._lsr_to_params(lsr) for lsr in chunk]

            try:
                async with self.db.neo4j_session() as session:
                    res = await session.run(query, {"batch": batch_params})
                    record = await res.single()
                    created = record["created"] if record else 0
                    result.succeeded += created

                # Index to ES
                for lsr in chunk:
                    await self._index_to_elasticsearch(lsr)

            except Exception as e:
                result.failed += len(chunk)
                result.errors.append(f"Batch {i // batch_size}: {e}")
                logger.error(f"Batch create failed at offset {i}: {e}")

        logger.info(
            f"Batch create: {result.succeeded} succeeded, "
            f"{result.failed} failed out of {len(lsrs)}"
        )
        return result

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
                continue
            by_type.setdefault(rel_type, []).append(rel)

        for rel_type, rels in by_type.items():
            query = f"""
            UNWIND $batch AS rel
            MATCH (source:LSR {{id: rel.source_id}})
            MATCH (target:LSR {{id: rel.target_id}})
            MERGE (source)-[r:{rel_type}]->(target)
            SET r.confidence = rel.confidence,
                r.evidence = rel.evidence,
                r.created_at = datetime()
            RETURN count(r) AS created
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
                    async with self.db.neo4j_session() as session:
                        res = await session.run(query, {"batch": batch_params})
                        record = await res.single()
                        created = record["created"] if record else 0
                        result.succeeded += created
                except Exception as e:
                    result.failed += len(chunk)
                    result.errors.append(f"Batch {rel_type} at offset {i}: {e}")
                    logger.error(f"Batch relationship create failed ({rel_type}, offset {i}): {e}")

        logger.info(
            f"Batch relationships: {result.succeeded} created, "
            f"{result.failed} failed out of {len(relationships)}"
        )
        return result

    async def get_statistics(self) -> dict[str, Any]:
        """Get database statistics: node counts, relationship counts, etc.

        Returns:
            Dict with counts and distribution info.
        """
        stats: dict[str, Any] = {}

        try:
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

        except RuntimeError as e:
            logger.warning(f"Could not get stats (Neo4j not connected): {e}")
            stats["error"] = str(e)
        except Exception as e:
            logger.error(f"Failed to get statistics: {e}")
            stats["error"] = str(e)

        return stats

    # -------------------------------------------------------------------------
    # Search (Neo4j fallback + Elasticsearch)
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

        Uses Elasticsearch when available for better full-text search;
        falls back to Neo4j CONTAINS queries otherwise.

        Args:
            form: Optional form to search for (fuzzy match).
            language: Optional ISO 639-3 language code.
            date_start: Optional start year for date range.
            date_end: Optional end year for date range.
            semantic_field: Optional WordNet synset ID to filter by.
            limit: Maximum number of results to return.
            offset: Number of results to skip.

        Returns:
            Tuple of (list of matching LSRs, total count).

        Raises:
            DatabaseError: If the search fails.
        """
        # Try Elasticsearch first
        if self._has_elasticsearch():
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
            except Exception as e:
                logger.warning(f"Elasticsearch search failed, falling back to Neo4j: {e}")

        # Fall back to Neo4j
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
        """Search using Neo4j CONTAINS queries."""
        where_clauses = []
        params: dict[str, Any] = {"limit": limit, "offset": offset}

        if form:
            where_clauses.append(
                "(l.form_normalized CONTAINS $form_lower OR l.form_orthographic CONTAINS $form)"
            )
            params["form"] = form
            params["form_lower"] = form.lower()

        if language:
            where_clauses.append("l.language_code = $language")
            params["language"] = language

        if date_start is not None:
            where_clauses.append("(l.date_start IS NULL OR l.date_start >= $date_start)")
            params["date_start"] = date_start

        if date_end is not None:
            where_clauses.append("(l.date_end IS NULL OR l.date_end <= $date_end)")
            params["date_end"] = date_end

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
        RETURN l
        ORDER BY l.confidence_overall DESC, l.form_orthographic
        SKIP $offset
        LIMIT $limit
        """

        try:
            async with self.db.neo4j_session() as session:
                count_result = await session.run(count_query, params)
                count_record = await count_result.single()
                total = count_record["total"] if count_record else 0

                search_result = await session.run(search_query, params)
                records = await search_result.fetch(limit)
                lsrs = [self._node_to_lsr(record["l"]) for record in records]

                return lsrs, total
        except RuntimeError as e:
            raise DatabaseError(message=f"Neo4j not connected: {e}") from e
        except Exception as e:
            logger.error(f"Failed to search LSRs: {e}")
            raise DatabaseError(message=f"Failed to search LSRs: {e}") from e

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
        """Search using Elasticsearch full-text queries."""
        must_clauses: list[dict] = []
        filter_clauses: list[dict] = []

        if form:
            must_clauses.append(
                {
                    "multi_match": {
                        "query": form,
                        "fields": [
                            "form_orthographic^3",
                            "form_normalized^2",
                            "definition_primary",
                        ],
                        "fuzziness": "AUTO",
                    },
                }
            )

        if language:
            filter_clauses.append({"term": {"language_code": language}})

        if date_start is not None:
            filter_clauses.append({"range": {"date_start": {"gte": date_start}}})

        if date_end is not None:
            filter_clauses.append({"range": {"date_end": {"lte": date_end}}})

        if semantic_field:
            filter_clauses.append({"term": {"semantic_fields": semantic_field}})

        body: dict[str, Any] = {
            "query": {
                "bool": {
                    "must": must_clauses or [{"match_all": {}}],
                    "filter": filter_clauses,
                },
            },
            "from": offset,
            "size": limit,
            "sort": [
                {"_score": {"order": "desc"}},
                {"confidence_overall": {"order": "desc"}},
            ],
        }

        es = self.db.elasticsearch
        response = await es.search(index=ES_INDEX_NAME, body=body)

        total = response["hits"]["total"]["value"]
        lsr_ids = [hit["_source"]["id"] for hit in response["hits"]["hits"]]

        # Fetch full LSRs from Neo4j by ID
        if not lsr_ids:
            return [], total

        lsrs = []
        for lsr_id in lsr_ids:
            try:
                lsr = await self.get_by_id(UUID(lsr_id))
                lsrs.append(lsr)
            except LSRNotFoundError:
                logger.warning(f"LSR {lsr_id} in ES but not in Neo4j")
                continue

        return lsrs, total

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
        """Create the Elasticsearch index if it doesn't exist.

        Returns:
            True if index exists or was created, False on error.
        """
        if not self._has_elasticsearch():
            return False

        try:
            es = self.db.elasticsearch
            exists = await es.indices.exists(index=ES_INDEX_NAME)
            if not exists:
                await es.indices.create(index=ES_INDEX_NAME, body=ES_INDEX_SETTINGS)
                logger.info(f"Created Elasticsearch index: {ES_INDEX_NAME}")
            return True
        except Exception as e:
            logger.warning(f"Failed to ensure ES index: {e}")
            return False

    async def _index_to_elasticsearch(self, lsr: LSR) -> None:
        """Index an LSR document in Elasticsearch."""
        if not self._has_elasticsearch():
            return

        try:
            doc = {
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
            }
            es = self.db.elasticsearch
            await es.index(index=ES_INDEX_NAME, id=str(lsr.id), document=doc)
        except Exception as e:
            # Don't fail the main operation if ES indexing fails
            logger.debug(f"ES index failed for {lsr.id}: {e}")

    async def _remove_from_elasticsearch(self, lsr_id: UUID) -> None:
        """Remove an LSR document from Elasticsearch."""
        if not self._has_elasticsearch():
            return

        try:
            es = self.db.elasticsearch
            await es.delete(index=ES_INDEX_NAME, id=str(lsr_id))
        except Exception as e:
            logger.debug(f"ES delete failed for {lsr_id}: {e}")

    async def reindex_all_to_elasticsearch(self) -> BatchResult:
        """Reindex all LSRs from Neo4j into Elasticsearch.

        Useful for initial population or recovery after ES data loss.

        Returns:
            BatchResult with indexing stats.
        """
        result = BatchResult()

        if not self._has_elasticsearch():
            result.errors.append("Elasticsearch not connected")
            return result

        await self.ensure_elasticsearch_index()

        try:
            async with self.db.neo4j_session() as session:
                res = await session.run("MATCH (l:LSR) RETURN l")
                records = await res.fetch(100000)

                for record in records:
                    try:
                        lsr = self._node_to_lsr(record["l"])
                        await self._index_to_elasticsearch(lsr)
                        result.succeeded += 1
                    except Exception as e:
                        result.failed += 1
                        result.errors.append(str(e))

        except Exception as e:
            result.errors.append(f"Reindex failed: {e}")

        logger.info(f"ES reindex: {result.succeeded} indexed, {result.failed} failed")
        return result

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    def _lsr_to_params(self, lsr: LSR) -> dict[str, Any]:
        """Convert an LSR to a dict of Neo4j query parameters."""
        return {
            "id": str(lsr.id),
            "version": lsr.version,
            "form_orthographic": lsr.form_orthographic,
            "form_phonetic": lsr.form_phonetic,
            "form_normalized": lsr.form_normalized,
            "language_code": lsr.language_code,
            "language_name": lsr.language_name,
            "language_family": lsr.language_family,
            "period_label": lsr.period_label,
            "date_start": lsr.date_start,
            "date_end": lsr.date_end,
            "date_confidence": lsr.date_confidence,
            "date_source": lsr.date_source.value,
            "definition_primary": lsr.definition_primary,
            "register": lsr.register.value if lsr.register else None,
            "frequency_score": lsr.frequency_score,
            "reconstruction_flag": lsr.reconstruction_flag,
            "confidence_overall": lsr.confidence_overall,
            "human_validated": lsr.human_validated,
            "validation_notes": lsr.validation_notes,
        }

    def _node_to_lsr(self, node: Any) -> LSR:
        """Convert a Neo4j node to an LSR object."""
        from src.models.lsr import DateSource, Register

        props = dict(node)

        # Handle enum conversions
        date_source = DateSource(props.get("date_source", "ATTESTED"))
        register = Register(props["register"]) if props.get("register") else None

        return LSR(
            id=UUID(props["id"]),
            version=props.get("version", 1),
            form_orthographic=props.get("form_orthographic", ""),
            form_phonetic=props.get("form_phonetic", ""),
            form_normalized=props.get("form_normalized", ""),
            language_code=props.get("language_code", ""),
            language_name=props.get("language_name", ""),
            language_family=props.get("language_family", ""),
            period_label=props.get("period_label", ""),
            date_start=props.get("date_start"),
            date_end=props.get("date_end"),
            date_confidence=props.get("date_confidence", 1.0),
            date_source=date_source,
            definition_primary=props.get("definition_primary", ""),
            register=register,
            frequency_score=props.get("frequency_score", 0.0),
            reconstruction_flag=props.get("reconstruction_flag", False),
            confidence_overall=props.get("confidence_overall", 1.0),
            human_validated=props.get("human_validated", False),
            validation_notes=props.get("validation_notes", ""),
        )
