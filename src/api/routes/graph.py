"""Graph query API routes."""

import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from src.api.jobs import JobStatus, job_registry
from src.exceptions import DatabaseError, NotFoundError, ValidationError
from src.utils.db import DatabaseManager, get_db
from src.utils.validation import GraphQueryRequest

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
        GraphQueryRequest(query=query_input.query)
    except ValueError as e:
        raise ValidationError(
            message=f"Invalid query: {e}",
            details={"query": query_input.query},
        ) from e

    logger.info(f"Executing graph query: {query_input.query[:100]}...")

    async def _run_read_query(tx: Any, query: str, parameters: dict[str, Any]) -> Any:
        result = await tx.run(query, parameters)
        records = await result.fetch(1000)  # Limit to 1000 results
        return records

    try:
        async with db.neo4j_session() as session:
            # Use explicit read transaction so Neo4j rejects any write operations
            records = await session.execute_read(
                _run_read_query, query_input.query, query_input.parameters
            )

            # Convert records to serializable format
            results = []
            for record in records:
                row = {}
                for key in record.keys():  # noqa: SIM118 - Record iteration yields values, not keys
                    value = record[key]
                    row[key] = _serialize_neo4j_value(value)
                results.append(row)

            return {
                "results": results,
                "count": len(results),
                "query": query_input.query,
                "query_type": "read",
            }

    except RuntimeError as e:
        raise DatabaseError(message=f"Neo4j not connected: {e}") from e
    except Exception as e:
        logger.error(f"Graph query failed: {e}")
        raise DatabaseError(message="Query execution failed") from e


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

    # Build relationship type filter (sanitize to prevent Cypher injection)
    valid_rel_types = {"DESCENDS_FROM", "BORROWED_FROM", "COGNATE_OF", "SHIFTED_TO", "MERGED_WITH"}
    rel_filter = ""
    if relationship_types:
        types = [t.strip().upper() for t in relationship_types.split(",")]
        types = [t for t in types if t in valid_rel_types]
        if types:
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
            result = await session.run(
                query,
                {
                    "from_id": str(from_lsr),
                    "to_id": str(to_lsr),
                },
            )
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
        raise DatabaseError(message=f"Neo4j not connected: {e}") from e
    except Exception as e:
        logger.error(f"Path finding failed: {e}")
        raise DatabaseError(message="Path finding failed") from e


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
        raise DatabaseError(message=f"Neo4j not connected: {e}") from e
    except Exception as e:
        logger.error(f"Etymology chain retrieval failed: {e}")
        raise DatabaseError(message="Etymology chain retrieval failed") from e


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
        raise DatabaseError(message=f"Neo4j not connected: {e}") from e
    except Exception as e:
        logger.error(f"Cognate retrieval failed: {e}")
        raise DatabaseError(message="Cognate retrieval failed") from e


class BulkExportRequest(BaseModel):
    """Request for bulk data export."""

    language: str = Field(..., description="ISO 639-3 language code")
    format: str = Field("json", pattern="^(json|csv)$", description="Export format: json or csv")
    include_relationships: bool = Field(True, description="Include relationship data")
    run_async: bool = Field(
        False,
        description="Run as a background job; poll /bulk/status/{job_id} and "
        "fetch the payload from /bulk/result/{job_id}",
    )


async def _run_bulk_export(db: DatabaseManager, request: BulkExportRequest) -> dict[str, Any]:
    """Execute the export and build the payload for the requested format."""
    query = """
    MATCH (l:LSR {language_code: $lang})
    RETURN l
    LIMIT 10000
    """
    async with db.neo4j_session() as session:
        result = await session.run(query, {"lang": request.language})
        records = await result.fetch(10000)
        lsrs = [_serialize_neo4j_value(r["l"]) for r in records]

    relationships: list[dict[str, Any]] = []
    if request.include_relationships:
        rel_query = """
        MATCH (a:LSR {language_code: $lang})-[r]->(b:LSR)
        RETURN a.id AS source, type(r) AS type, b.id AS target
        LIMIT 50000
        """
        async with db.neo4j_session() as session:
            result = await session.run(rel_query, {"lang": request.language})
            rel_records = await result.fetch(50000)
            relationships = [
                {"source": r["source"], "type": r["type"], "target": r["target"]}
                for r in rel_records
            ]

    payload: dict[str, Any] = {
        "format": request.format,
        "language": request.language,
        "count": len(lsrs),
        "relationship_count": len(relationships),
    }
    if request.format == "csv":
        payload["csv"] = _lsrs_to_csv(lsrs)
    else:
        payload["items"] = lsrs
        if request.include_relationships:
            payload["relationships"] = relationships
    return payload


def _lsrs_to_csv(lsrs: list[Any]) -> str:
    """Serialize exported LSR dicts to CSV (union of keys, sorted header)."""
    import csv
    import io

    dict_rows = [row for row in lsrs if isinstance(row, dict)]
    if not dict_rows:
        return ""
    fieldnames = sorted({key for row in dict_rows for key in row})
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for row in dict_rows:
        writer.writerow(row)
    return buffer.getvalue()


@router.post("/bulk/export")
async def create_bulk_export(
    request: BulkExportRequest,
    db: DatabaseManager = Depends(get_db_manager),
) -> dict[str, Any]:
    """
    Export LSRs (and optionally relationships) for a language.

    With run_async=false (default) the export runs inline and the payload
    is returned directly. With run_async=true a background job is created;
    poll /bulk/status/{job_id} and fetch the result from
    /bulk/result/{job_id} once completed.
    """
    logger.info(
        f"Bulk export for {request.language} in {request.format} format "
        f"(async={request.run_async})"
    )

    if request.run_async:
        job = job_registry.submit(
            "bulk_export",
            lambda: _run_bulk_export(db, request),
            params={"language": request.language, "format": request.format},
        )
        return {
            "status": "accepted",
            "job_id": job.id,
            "status_url": f"/api/v1/graph/bulk/status/{job.id}",
            "result_url": f"/api/v1/graph/bulk/result/{job.id}",
        }

    try:
        payload = await _run_bulk_export(db, request)
    except RuntimeError as e:
        raise DatabaseError(message=f"Neo4j not connected: {e}") from e
    except Exception as e:
        logger.error(f"Bulk export failed: {e}")
        raise DatabaseError(message="Bulk export failed") from e

    return {
        "status": "completed",
        "message": f"Exported {payload['count']} LSRs",
        **payload,
    }


@router.get("/bulk/status/{job_id}")
async def get_export_status(job_id: str) -> dict[str, Any]:
    """Get status of a bulk export job."""
    job = job_registry.get(job_id)
    if job is None:
        return {
            "job_id": job_id,
            "status": "not_found",
            "message": "No such job (jobs expire an hour after completion)",
            "download_url": None,
        }
    status = job.to_dict()
    status["download_url"] = (
        f"/api/v1/graph/bulk/result/{job_id}" if job.status == JobStatus.COMPLETED else None
    )
    return status


@router.get("/bulk/result/{job_id}")
async def get_export_result(job_id: str) -> dict[str, Any]:
    """Fetch the payload of a completed bulk export job."""
    job = job_registry.get(job_id)
    if job is None:
        raise NotFoundError(resource_type="Export job", resource_id=job_id)
    if job.status == JobStatus.FAILED:
        raise DatabaseError(message=f"Export job failed: {job.error}")
    if job.status != JobStatus.COMPLETED:
        return {"job_id": job_id, "status": job.status.value, "message": "Job still running"}
    return {"job_id": job_id, "status": "completed", **job.result}


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
            relationships.append(
                {
                    "type": rel.type if hasattr(rel, "type") else "UNKNOWN",
                    "properties": dict(rel.items()) if hasattr(rel, "items") else {},
                }
            )

    return {
        "nodes": nodes,
        "relationships": relationships,
        "length": len(relationships),
    }
