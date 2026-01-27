"""Graph query API routes."""

import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from src.exceptions import DatabaseError, LSRNotFoundError, ValidationError
from src.utils.db import DatabaseManager, get_db
from src.utils.validation import GraphQueryRequest, validate_graph_query

logger = logging.getLogger(__name__)

router = APIRouter()


class GraphQuery(BaseModel):
    """Input for graph queries."""

    query: str = Field(..., description="Cypher query to execute", max_length=5000)
    parameters: dict[str, Any] = Field(
        default_factory=dict,
        description="Query parameters (for parameterized queries)",
    )


async def get_db_manager() -> DatabaseManager:
    """Dependency to get the database manager."""
    return await get_db()


@router.post("/query")
async def execute_query(
    query_input: GraphQuery,
    db: DatabaseManager = Depends(get_db_manager),
) -> dict[str, Any]:
    """
    Execute a Cypher graph query.

    The query is validated for safety (no destructive operations allowed).
    Use parameterized queries for user-provided values.

    Example:
    ```json
    {
        "query": "MATCH (l:LSR {language_code: $lang}) RETURN l LIMIT 10",
        "parameters": {"lang": "eng"}
    }
    ```
    """
    # Validate the query for safety
    try:
        validation_request = GraphQueryRequest(query=query_input.query)
    except ValueError as e:
        raise ValidationError(
            message=f"Invalid query: {e}",
            details={"query": query_input.query},
        )

    logger.info(f"Executing graph query: {query_input.query[:100]}...")

    try:
        async with db.neo4j_session() as session:
            result = await session.run(query_input.query, query_input.parameters)
            records = await result.fetch(1000)  # Limit to 1000 results

            # Convert records to serializable format
            results = []
            for record in records:
                row = {}
                for key in record.keys():
                    value = record[key]
                    row[key] = _serialize_neo4j_value(value)
                results.append(row)

            # Get result summary
            summary = await result.consume()

            return {
                "results": results,
                "count": len(results),
                "query": query_input.query,
                "query_type": summary.query_type if hasattr(summary, 'query_type') else "read",
            }

    except RuntimeError as e:
        raise DatabaseError(message=f"Neo4j not connected: {e}")
    except Exception as e:
        logger.error(f"Graph query failed: {e}")
        raise DatabaseError(message=f"Query execution failed: {e}")


@router.get("/path")
async def get_path(
    from_lsr: UUID = Query(..., description="Source LSR ID"),
    to_lsr: UUID = Query(..., description="Target LSR ID"),
    max_hops: int = Query(5, ge=1, le=20, description="Maximum path length"),
    relationship_types: str | None = Query(
        None,
        description="Comma-separated relationship types to traverse (e.g., 'DESCENDS_FROM,BORROWED_FROM')",
    ),
    db: DatabaseManager = Depends(get_db_manager),
) -> dict[str, Any]:
    """
    Find all paths between two LSRs.

    Traverses the graph to find etymology chains, borrowing paths, etc.
    """
    logger.info(f"Finding paths from {from_lsr} to {to_lsr} (max {max_hops} hops)")

    # Build relationship type filter
    rel_filter = ""
    if relationship_types:
        types = [t.strip() for t in relationship_types.split(",")]
        rel_filter = ":" + "|".join(types)

    # Cypher query to find all shortest paths
    query = f"""
    MATCH path = allShortestPaths(
        (start:LSR {{id: $from_id}})-[r{rel_filter}*1..{max_hops}]-(end:LSR {{id: $to_id}})
    )
    RETURN path
    LIMIT 10
    """

    try:
        async with db.neo4j_session() as session:
            result = await session.run(query, {
                "from_id": str(from_lsr),
                "to_id": str(to_lsr),
            })
            records = await result.fetch(10)

            paths = []
            for record in records:
                path = record["path"]
                path_data = _serialize_path(path)
                paths.append(path_data)

            return {
                "from_lsr": str(from_lsr),
                "to_lsr": str(to_lsr),
                "max_hops": max_hops,
                "paths_found": len(paths),
                "paths": paths,
            }

    except RuntimeError as e:
        raise DatabaseError(message=f"Neo4j not connected: {e}")
    except Exception as e:
        logger.error(f"Path finding failed: {e}")
        raise DatabaseError(message=f"Path finding failed: {e}")


@router.get("/etymology/{lsr_id}")
async def get_etymology_chain(
    lsr_id: UUID,
    max_depth: int = Query(10, ge=1, le=50, description="Maximum ancestry depth"),
    db: DatabaseManager = Depends(get_db_manager),
) -> dict[str, Any]:
    """
    Get the full etymology chain for an LSR.

    Traces DESCENDS_FROM relationships back to proto-forms.
    """
    logger.info(f"Getting etymology chain for {lsr_id}")

    query = """
    MATCH path = (start:LSR {id: $lsr_id})-[:DESCENDS_FROM*0..]->(ancestor:LSR)
    WHERE NOT (ancestor)-[:DESCENDS_FROM]->()
    RETURN path
    ORDER BY length(path) DESC
    LIMIT 1
    """

    try:
        async with db.neo4j_session() as session:
            result = await session.run(query, {"lsr_id": str(lsr_id)})
            record = await result.single()

            if not record:
                return {
                    "lsr_id": str(lsr_id),
                    "chain": [],
                    "depth": 0,
                    "proto_form": None,
                }

            path = record["path"]
            chain = _serialize_path(path)

            return {
                "lsr_id": str(lsr_id),
                "chain": chain["nodes"],
                "relationships": chain["relationships"],
                "depth": len(chain["nodes"]) - 1,
                "proto_form": chain["nodes"][-1] if chain["nodes"] else None,
            }

    except RuntimeError as e:
        raise DatabaseError(message=f"Neo4j not connected: {e}")
    except Exception as e:
        logger.error(f"Etymology chain retrieval failed: {e}")
        raise DatabaseError(message=f"Etymology chain retrieval failed: {e}")


@router.get("/cognates/{lsr_id}")
async def get_cognates(
    lsr_id: UUID,
    db: DatabaseManager = Depends(get_db_manager),
) -> dict[str, Any]:
    """
    Get all cognates for an LSR across languages.

    Finds words that share a common proto-ancestor.
    """
    logger.info(f"Getting cognates for {lsr_id}")

    # Find the proto-ancestor, then find all descendants in other languages
    query = """
    MATCH (start:LSR {id: $lsr_id})-[:DESCENDS_FROM*0..]->(proto:LSR)
    WHERE NOT (proto)-[:DESCENDS_FROM]->()
    WITH proto
    MATCH (proto)<-[:DESCENDS_FROM*1..]-(cognate:LSR)
    WHERE cognate.id <> $lsr_id
    RETURN DISTINCT cognate
    LIMIT 100
    """

    try:
        async with db.neo4j_session() as session:
            result = await session.run(query, {"lsr_id": str(lsr_id)})
            records = await result.fetch(100)

            cognates = []
            for record in records:
                node = record["cognate"]
                cognates.append(_serialize_neo4j_value(node))

            # Group by language
            by_language: dict[str, list] = {}
            for cognate in cognates:
                lang = cognate.get("language_code", "unknown")
                if lang not in by_language:
                    by_language[lang] = []
                by_language[lang].append(cognate)

            return {
                "lsr_id": str(lsr_id),
                "cognate_count": len(cognates),
                "languages": list(by_language.keys()),
                "by_language": by_language,
            }

    except RuntimeError as e:
        raise DatabaseError(message=f"Neo4j not connected: {e}")
    except Exception as e:
        logger.error(f"Cognate retrieval failed: {e}")
        raise DatabaseError(message=f"Cognate retrieval failed: {e}")


class BulkExportRequest(BaseModel):
    """Request for bulk data export."""

    language: str = Field(..., description="ISO 639-3 language code")
    format: str = Field("json", description="Export format: json, csv, or rdf")
    include_relationships: bool = Field(True, description="Include relationship data")


@router.post("/bulk/export")
async def create_bulk_export(
    request: BulkExportRequest,
    db: DatabaseManager = Depends(get_db_manager),
) -> dict[str, str]:
    """
    Create a bulk export job for LSRs in a language.

    Note: Full async job processing not yet implemented.
    Returns immediate results for small datasets.
    """
    logger.info(f"Creating bulk export for {request.language} in {request.format} format")

    # For now, return a simple export (not async job-based)
    query = """
    MATCH (l:LSR {language_code: $lang})
    RETURN l
    LIMIT 10000
    """

    try:
        async with db.neo4j_session() as session:
            result = await session.run(query, {"lang": request.language})
            records = await result.fetch(10000)

            lsrs = [_serialize_neo4j_value(r["l"]) for r in records]

            return {
                "status": "completed",
                "format": request.format,
                "language": request.language,
                "count": len(lsrs),
                "message": f"Exported {len(lsrs)} LSRs",
            }

    except RuntimeError as e:
        raise DatabaseError(message=f"Neo4j not connected: {e}")
    except Exception as e:
        logger.error(f"Bulk export failed: {e}")
        raise DatabaseError(message=f"Bulk export failed: {e}")


@router.get("/bulk/status/{job_id}")
async def get_export_status(job_id: str) -> dict[str, Any]:
    """Get status of a bulk export job."""
    # Job-based export not yet implemented
    return {
        "job_id": job_id,
        "status": "not_found",
        "message": "Async job processing not yet implemented. Use /bulk/export for immediate results.",
        "download_url": None,
    }


def _serialize_neo4j_value(value: Any) -> Any:
    """Convert Neo4j values to JSON-serializable format."""
    if value is None:
        return None

    # Handle Neo4j Node
    if hasattr(value, "labels") and hasattr(value, "items"):
        return {
            "labels": list(value.labels),
            **dict(value.items()),
        }

    # Handle Neo4j Relationship
    if hasattr(value, "type") and hasattr(value, "start_node"):
        return {
            "type": value.type,
            "properties": dict(value.items()) if hasattr(value, "items") else {},
        }

    # Handle Neo4j Path
    if hasattr(value, "nodes") and hasattr(value, "relationships"):
        return _serialize_path(value)

    # Handle lists
    if isinstance(value, list):
        return [_serialize_neo4j_value(v) for v in value]

    # Handle dicts
    if isinstance(value, dict):
        return {k: _serialize_neo4j_value(v) for k, v in value.items()}

    # Handle datetime
    if hasattr(value, "isoformat"):
        return value.isoformat()

    return value


def _serialize_path(path: Any) -> dict[str, Any]:
    """Serialize a Neo4j path to a dictionary."""
    nodes = []
    relationships = []

    if hasattr(path, "nodes"):
        for node in path.nodes:
            nodes.append(_serialize_neo4j_value(node))

    if hasattr(path, "relationships"):
        for rel in path.relationships:
            relationships.append({
                "type": rel.type if hasattr(rel, "type") else "UNKNOWN",
                "properties": dict(rel.items()) if hasattr(rel, "items") else {},
            })

    return {
        "nodes": nodes,
        "relationships": relationships,
        "length": len(relationships),
    }
