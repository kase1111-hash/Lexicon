"""Repository for LSR persistence operations using Neo4j."""

import logging
from typing import Any
from uuid import UUID

from src.exceptions import DatabaseError, LSRNotFoundError
from src.models.lsr import LSR
from src.utils.db import DatabaseManager

logger = logging.getLogger(__name__)


class LSRRepository:
    """Repository for LSR CRUD operations in Neo4j."""

    def __init__(self, db: DatabaseManager):
        """Initialize the repository with a database manager."""
        self.db = db

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
        params = {
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

        try:
            async with self.db.neo4j_session() as session:
                result = await session.run(query, params)
                record = await result.single()
                if record:
                    logger.info(f"Created LSR: {lsr.id}")
                    return lsr
                raise DatabaseError(message="Failed to create LSR - no record returned")
        except RuntimeError as e:
            raise DatabaseError(message=f"Neo4j not connected: {e}")
        except Exception as e:
            logger.error(f"Failed to create LSR {lsr.id}: {e}")
            raise DatabaseError(message=f"Failed to create LSR: {e}")

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
            raise DatabaseError(message=f"Neo4j not connected: {e}")
        except Exception as e:
            logger.error(f"Failed to get LSR {lsr_id}: {e}")
            raise DatabaseError(message=f"Failed to get LSR: {e}")

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
        params = {
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

        try:
            async with self.db.neo4j_session() as session:
                result = await session.run(query, params)
                record = await result.single()
                if record is None:
                    raise LSRNotFoundError(lsr_id=str(lsr.id))
                logger.info(f"Updated LSR: {lsr.id}")
                return lsr
        except LSRNotFoundError:
            raise
        except RuntimeError as e:
            raise DatabaseError(message=f"Neo4j not connected: {e}")
        except Exception as e:
            logger.error(f"Failed to update LSR {lsr.id}: {e}")
            raise DatabaseError(message=f"Failed to update LSR: {e}")

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
                    return True
                raise LSRNotFoundError(lsr_id=str(lsr_id))
        except LSRNotFoundError:
            raise
        except RuntimeError as e:
            raise DatabaseError(message=f"Neo4j not connected: {e}")
        except Exception as e:
            logger.error(f"Failed to delete LSR {lsr_id}: {e}")
            raise DatabaseError(message=f"Failed to delete LSR: {e}")

    async def search(
        self,
        form: str | None = None,
        language: str | None = None,
        date_start: int | None = None,
        date_end: int | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[LSR], int]:
        """
        Search for LSRs matching the given criteria.

        Args:
            form: Optional form to search for (fuzzy match).
            language: Optional ISO 639-3 language code.
            date_start: Optional start year for date range.
            date_end: Optional end year for date range.
            limit: Maximum number of results to return.
            offset: Number of results to skip.

        Returns:
            Tuple of (list of matching LSRs, total count).

        Raises:
            DatabaseError: If the search fails.
        """
        # Build WHERE clauses
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

        where_clause = " AND ".join(where_clauses) if where_clauses else "TRUE"

        # Count query
        count_query = f"""
        MATCH (l:LSR)
        WHERE {where_clause}
        RETURN count(l) as total
        """

        # Search query
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
                # Get total count
                count_result = await session.run(count_query, params)
                count_record = await count_result.single()
                total = count_record["total"] if count_record else 0

                # Get results
                search_result = await session.run(search_query, params)
                records = await search_result.fetch(limit)
                lsrs = [self._node_to_lsr(record["l"]) for record in records]

                return lsrs, total
        except RuntimeError as e:
            raise DatabaseError(message=f"Neo4j not connected: {e}")
        except Exception as e:
            logger.error(f"Failed to search LSRs: {e}")
            raise DatabaseError(message=f"Failed to search LSRs: {e}")

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
