"""Graph query API routes."""

import asyncio
import base64
import csv
import io
import json
import logging
import math
import re
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from neo4j import Query as Neo4jQuery
from neo4j import unit_of_work
from neo4j.exceptions import ClientError, ServiceUnavailable, SessionExpired, TransientError
from neo4j.graph import Node, Path, Relationship
from neo4j.spatial import Point
from pydantic import BaseModel, Field, field_validator

from src.api.jobs import JobStatus, JobStoreUnavailableError, job_registry
from src.config import get_settings
from src.exceptions import (
    AuthorizationError,
    DatabaseError,
    LexiconError,
    LSRNotFoundError,
    NotFoundError,
    ValidationError,
)
from src.repositories.lsr_repository import VALID_RELATIONSHIP_TYPES, LSRRepository, database_error
from src.utils.db import DatabaseManager, get_db
from src.utils.validation import (
    CYPHER_QUERY_DEFAULT_TIMEOUT_SECONDS,
    CYPHER_QUERY_MAX_TIMEOUT_SECONDS,
    normalize_language_code,
    validate_read_only_cypher,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# Caps for POST /graph/query responses
_QUERY_MAX_ROWS = 1000
_QUERY_MAX_BYTES = 5_000_000

# Upper bound for DESCENDS_FROM traversals (etymology, cognates)
_MAX_TRAVERSAL_DEPTH = 50
_MAX_COGNATES = 100
# Server-side timeouts for the fixed traversal queries and for exports
_TRAVERSAL_TIMEOUT_SECONDS = 15
_EXPORT_TIMEOUT_SECONDS = 60
# Neo4j only checks its transaction timeout between units of work, so some
# queries (e.g. a huge UNWIND) overrun it; the API stops waiting after this
# much extra time.
_CLIENT_DEADLINE_GRACE_SECONDS = 2
# execute_read keeps retrying a database it cannot reach; a user query whose
# transaction Neo4j has not begun within this many seconds is given up (503)
_QUERY_START_DEADLINE_SECONDS = 2.0

_EXPORT_MAX_LIMIT = 10_000
_EXPORT_MAX_OFFSET = 10_000_000
_EXPORT_MAX_RELATIONSHIPS = 50_000
# A page also ends once its LSRs reach about this much JSON (with their
# 384-float semantic vectors, some 1,200 LSRs), so neither an export response
# nor an async result held for an hour can grow without bound
_EXPORT_MAX_BYTES = 10_000_000

_DB_UNAVAILABLE = "Graph database is not available"


class GraphQuery(BaseModel):
    """Input for graph queries."""

    query: str = Field(..., description="Cypher query to execute", max_length=5000)
    parameters: dict[str, Any] = Field(
        default_factory=dict,
        description="Query parameters (for parameterized queries)",
    )
    timeout_seconds: int = Field(
        CYPHER_QUERY_DEFAULT_TIMEOUT_SECONDS,
        ge=1,
        le=CYPHER_QUERY_MAX_TIMEOUT_SECONDS,
        description="Server-side transaction timeout in seconds",
    )


async def get_db_manager() -> DatabaseManager:
    """Dependency to get the database manager."""
    return await get_db()


async def _read_records(
    db: DatabaseManager,
    query: str,
    parameters: dict[str, Any],
    timeout: float = _TRAVERSAL_TIMEOUT_SECONDS,
) -> list[Any]:
    """Run one of this module's fixed read queries with a server-side timeout.

    An auto-commit transaction is used so that an unreachable database fails
    fast instead of being retried the way transaction functions are.
    """

    async def _fetch_all() -> list[Any]:
        async with db.neo4j_session() as session:
            result = await session.run(Neo4jQuery(query, timeout=timeout), parameters)
            return [record async for record in result]

    records: list[Any] = await asyncio.wait_for(
        _fetch_all(), timeout + _CLIENT_DEADLINE_GRACE_SECONDS
    )
    return records


async def _guarded(action: str, operation: Callable[[], Awaitable[Any]]) -> Any:
    """Await a graph operation, mapping failures to errors that leak no internals."""
    try:
        return await operation()
    except LexiconError:
        raise
    except Exception as e:
        # Unreachable -> "not available", a deadline -> "<action> timed out"
        raise database_error(e, action) from e


async def _require_lsrs(db: DatabaseManager, *lsr_ids: UUID) -> None:
    """Raise LSRNotFoundError for the first ID with no LSR node in the graph."""
    wanted = [str(lsr_id) for lsr_id in lsr_ids]
    records = await _read_records(
        db, "MATCH (l:LSR) WHERE l.id IN $ids RETURN l.id AS id", {"ids": wanted}
    )
    found = {record["id"] for record in records}
    for lsr_id in wanted:
        if lsr_id not in found:
            raise LSRNotFoundError(lsr_id)


def _query_timeout_error(timeout_seconds: int, details: dict[str, Any]) -> ValidationError:
    """400 for a user query that ran past its time limit."""
    return ValidationError(
        message=(
            f"Query exceeded the {timeout_seconds}s time limit; "
            "add a LIMIT or make the MATCH more selective"
        ),
        code="QUERY_TIMEOUT",
        details=details,
    )


def _cypher_client_error(e: ClientError, timeout_seconds: int) -> LexiconError:
    """Map a Neo4j client error to an API error that never echoes the DB text.

    Problems with the query itself (syntax, types, missing parameters, writes,
    timeouts) are 400s; anything else, such as an authentication failure
    between the API and Neo4j, is not the caller's fault and stays a 503.
    """
    code = e.code or "Neo.ClientError"
    details: dict[str, Any] = {"neo4j_code": code}
    if "TransactionTimedOut" in code:
        return _query_timeout_error(timeout_seconds, details)
    if code.endswith((".AccessMode", ".Security.Forbidden")):
        return ValidationError(message="Only read-only queries are allowed", details=details)
    if not code.startswith("Neo.ClientError.Statement."):
        logger.error(f"Graph query failed with {code}")
        return DatabaseError(message="Query execution failed")
    if code.endswith(".SyntaxError"):
        position = re.search(r"line (\d+), column (\d+)", e.message or "")
        if position:
            details["line"] = int(position.group(1))
            details["column"] = int(position.group(2))
        return ValidationError(message="Invalid Cypher syntax", details=details)
    if code.endswith(".ParameterMissing"):
        missing = re.search(r"Expected parameter\(s\): ([\w, ]+)", e.message or "")
        if missing:
            details["missing_parameters"] = [p.strip() for p in missing.group(1).split(",")]
        return ValidationError(
            message="Query references a parameter that was not supplied", details=details
        )
    return ValidationError(message=f"Query rejected by the database ({code})", details=details)


class _QueryTransientError(Exception):
    """A retryable Neo4j error raised while a user query ran.

    Raised from the transaction function in place of the driver's error so
    that execute_read does not retry the query: retrying a query that ran
    out of memory only repeats the load on the database, and one that lost
    its connection would keep waiting for a database that is down.
    """

    def __init__(self, error: TransientError | ServiceUnavailable | SessionExpired) -> None:
        super().__init__(getattr(error, "code", None) or type(error).__name__)
        self.error = error


def _cypher_transient_error(
    e: TransientError | ServiceUnavailable | SessionExpired,
) -> LexiconError:
    """Map a retryable error of a user query (typically out of memory)."""
    code = (e.code if isinstance(e, TransientError) else None) or type(e).__name__
    if "OutOfMemory" in code:
        return ValidationError(
            message=(
                "Query needs more memory than the database allows; "
                "add a LIMIT or return less data"
            ),
            code="QUERY_TOO_LARGE",
            details={"neo4j_code": code},
        )
    logger.error(f"Graph query failed with {code}")
    return DatabaseError(message=_DB_UNAVAILABLE)


@router.post("/query")
async def execute_query(
    query_input: GraphQuery,
    db: DatabaseManager = Depends(get_db_manager),
) -> dict[str, Any]:
    """
    Execute a read-only Cypher graph query.

    The query must start with a read clause and may not use LOAD CSV, CALL,
    USE, procedures (dbms.*, apoc.*), administration commands or write
    clauses. Those keywords are rejected anywhere in the text, even inside
    string literals, so pass literal values as parameters. The query runs in
    a read transaction with a server-side timeout (timeout_seconds, default
    10, max 30). At most 1000 rows and about 5 MB are returned; `truncated`
    says whether the result was cut short.

    These caps apply to what is returned, not to what the API must receive:
    a single row holding a huge value (e.g. `RETURN range(1, 10000000)`) is
    read whole first. The endpoint is therefore meant for trusted clients;
    with GRAPH_QUERY_ENABLED=false it answers 403 QUERY_DISABLED.

    Example:
    ```json
    {
        "query": "MATCH (l:LSR {language_code: $lang}) RETURN l LIMIT 10",
        "parameters": {"lang": "eng"}
    }
    ```
    """
    if not get_settings().api.graph_query_enabled:
        raise AuthorizationError(
            message="Cypher queries are disabled on this server (GRAPH_QUERY_ENABLED=false)",
            code="QUERY_DISABLED",
        )
    try:
        validate_read_only_cypher(query_input.query)
    except ValueError as e:
        raise ValidationError(message=f"Invalid query: {e}", field="query") from e

    logger.info(f"Executing graph query: {query_input.query[:100]!r}")
    timeout = query_input.timeout_seconds
    started = False  # set once Neo4j has accepted the transaction
    loop = asyncio.get_running_loop()

    @unit_of_work(timeout=timeout)
    async def _run_read_query(tx: Any) -> tuple[list[dict[str, Any]], str | None]:
        nonlocal started
        if not started:
            started = True
            # Neo4j has begun the transaction: the query's own time limit applies
            deadline.reschedule(loop.time() + timeout + _CLIENT_DEADLINE_GRACE_SECONDS)
        rows: list[dict[str, Any]] = []
        size = 0
        try:
            result = await tx.run(query_input.query, query_input.parameters)
            async for record in result:
                if len(rows) >= _QUERY_MAX_ROWS:
                    return rows, f"row limit of {_QUERY_MAX_ROWS} reached"
                row = {key: _serialize_neo4j_value(value) for key, value in record.items()}
                size += len(json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode())
                if size > _QUERY_MAX_BYTES:
                    return rows, f"response size limit of {_QUERY_MAX_BYTES} bytes reached"
                rows.append(row)
        except (TransientError, ServiceUnavailable, SessionExpired) as e:
            raise _QueryTransientError(e) from e
        return rows, None

    try:
        async with db.neo4j_session() as session:
            async with asyncio.timeout(_QUERY_START_DEADLINE_SECONDS) as deadline:
                # Read transaction: Neo4j itself rejects any write operation
                results, truncated_reason = await session.execute_read(_run_read_query)
    except TimeoutError as e:
        if not started:
            # Still retrying to reach Neo4j when the deadline passed
            raise DatabaseError(message=_DB_UNAVAILABLE) from e
        logger.warning(f"Graph query overran its {timeout}s timeout")
        raise _query_timeout_error(timeout, {}) from e
    except ClientError as e:
        logger.warning(f"Graph query rejected by Neo4j: {e.code}")
        raise _cypher_client_error(e, timeout) from e
    except _QueryTransientError as e:
        logger.warning(f"Graph query failed transiently: {e}")
        raise _cypher_transient_error(e.error) from e
    except (RuntimeError, ServiceUnavailable, SessionExpired) as e:
        raise DatabaseError(message=_DB_UNAVAILABLE) from e
    except Exception as e:
        logger.error(f"Graph query failed: {e}")
        raise DatabaseError(message="Query execution failed") from e

    return {
        "results": results,
        "count": len(results),
        "truncated": truncated_reason is not None,
        "truncated_reason": truncated_reason,
        "query": query_input.query,
        "query_type": "read",
    }


def _parse_relationship_types(relationship_types: str | None) -> list[str]:
    """Parse a comma-separated relationship type filter, rejecting unknown types."""
    if not relationship_types:
        return []
    types = {t.strip().upper() for t in relationship_types.split(",") if t.strip()}
    unknown = sorted(types - VALID_RELATIONSHIP_TYPES)
    if unknown:
        raise ValidationError(
            message=(
                f"Unknown relationship type(s): {', '.join(unknown)}. "
                f"Valid types: {', '.join(sorted(VALID_RELATIONSHIP_TYPES))}"
            ),
            field="relationship_types",
        )
    return sorted(types)


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
    Find the shortest paths (at most 10) between two different LSRs.

    Relationships are traversed in either direction; each returned
    relationship carries its `source` and `target` LSR IDs, so the direction
    of, for example, a borrowing can be read off the path. Unknown LSR IDs
    give 404 and unknown relationship types give 400.
    """
    types = _parse_relationship_types(relationship_types)
    if from_lsr == to_lsr:
        raise ValidationError(message="from_lsr and to_lsr must be different LSRs", field="to_lsr")
    logger.info(f"Finding paths from {from_lsr} to {to_lsr} (max {max_hops} hops)")

    # The types come from the allowlist and max_hops is a validated int, so
    # interpolating them cannot inject Cypher
    rel_filter = ":" + "|".join(types) if types else ""
    query = f"""
    MATCH (source:LSR {{id: $from_id}}), (target:LSR {{id: $to_id}})
    MATCH path = allShortestPaths((source)-[{rel_filter}*1..{max_hops}]-(target))
    RETURN path
    LIMIT 10
    """

    async def _find() -> list[Any]:
        await _require_lsrs(db, from_lsr, to_lsr)
        return await _read_records(db, query, {"from_id": str(from_lsr), "to_id": str(to_lsr)})

    records = await _guarded("Path finding", _find)
    paths = [_serialize_path(record["path"]) for record in records]
    return {
        "from_lsr": str(from_lsr),
        "to_lsr": str(to_lsr),
        "max_hops": max_hops,
        "relationship_types": types or sorted(VALID_RELATIONSHIP_TYPES),
        "paths_found": len(paths),
        "paths": paths,
    }


@router.get("/etymology/{lsr_id}")
async def get_etymology_chain(
    lsr_id: UUID,
    max_depth: int = Query(10, ge=1, le=_MAX_TRAVERSAL_DEPTH, description="Maximum ancestry depth"),
    db: DatabaseManager = Depends(get_db_manager),
) -> dict[str, Any]:
    """
    Get the etymology chain for an LSR.

    Follows DESCENDS_FROM relationships back towards a proto-form, at most
    max_depth hops, along a shortest path. The chain ends at the farthest
    root ancestor found. If max_depth cuts off any line of ancestry (a
    deeper root may lie beyond it), `truncated` is true and the chain ends
    at the farthest ancestor found.
    """
    logger.info(f"Getting etymology chain for {lsr_id} (max depth {max_depth})")

    # Same rules as GET /lsr/{id}/etymology (LSRRepository.get_etymology_path)
    repo = LSRRepository(db)
    found = await _guarded(
        "Etymology chain retrieval", lambda: repo.get_etymology_path(lsr_id, max_depth)
    )
    if found is None:
        raise LSRNotFoundError(str(lsr_id))

    path, complete = found
    chain = _serialize_path(path)  # an LSR without ancestors: the LSR itself
    return {
        "lsr_id": str(lsr_id),
        "chain": chain["nodes"],
        "relationships": chain["relationships"],
        "depth": len(chain["nodes"]) - 1,
        "max_depth": max_depth,
        "truncated": not complete,
        # A cut-off chain's last node is not known to be the oldest form
        "proto_form": chain["nodes"][-1] if complete else None,
    }


@router.get("/cognates/{lsr_id}")
async def get_cognates(
    lsr_id: UUID,
    db: DatabaseManager = Depends(get_db_manager),
) -> dict[str, Any]:
    """
    Get the cognates of an LSR across languages (at most 100).

    Cognates are LSRs in other languages that share a DESCENDS_FROM ancestor
    with it, excluding its own lineage (its ancestors and descendants), plus
    any LSR linked to it directly by a COGNATE_OF relationship.
    """
    logger.info(f"Getting cognates for {lsr_id}")

    # Same rules as GET /lsr/{id}/cognates (LSRRepository.get_cognates)
    repo = LSRRepository(db)

    async def _find() -> list[dict[str, Any]]:
        if not await repo.exists(lsr_id):
            raise LSRNotFoundError(str(lsr_id))
        return await repo.get_cognates(
            lsr_id, limit=_MAX_COGNATES + 1, summarize=_serialize_neo4j_value
        )

    records = await _guarded("Cognate retrieval", _find)
    cognates = records[:_MAX_COGNATES]

    by_language: dict[str, list[Any]] = {}
    for cognate in cognates:
        by_language.setdefault(cognate.get("language_code") or "unknown", []).append(cognate)

    return {
        "lsr_id": str(lsr_id),
        "cognate_count": len(cognates),
        "truncated": len(records) > _MAX_COGNATES,
        "languages": list(by_language.keys()),
        "by_language": by_language,
    }


class BulkExportRequest(BaseModel):
    """Request for bulk data export."""

    language: str = Field(
        ..., max_length=20, description="ISO 639-3 language code (639-1 codes are mapped)"
    )
    format: str = Field("json", pattern="^(json|csv)$", description="Export format: json or csv")
    include_relationships: bool = Field(
        True, description="Include the outgoing relationships of the exported LSRs"
    )
    offset: int = Field(
        0, ge=0, le=_EXPORT_MAX_OFFSET, description="Number of LSRs to skip (ordered by id)"
    )
    limit: int = Field(
        _EXPORT_MAX_LIMIT, ge=1, le=_EXPORT_MAX_LIMIT, description="Maximum LSRs to export"
    )
    run_async: bool = Field(
        False,
        description="Run as a background job; poll /bulk/status/{job_id} and "
        "fetch the payload from /bulk/result/{job_id}",
    )

    @field_validator("language")
    @classmethod
    def validate_language(cls, v: str) -> str:
        return normalize_language_code(v)


async def _read_export_page(
    db: DatabaseManager, params: dict[str, Any]
) -> tuple[list[dict[str, Any]], bool]:
    """Read one page of LSRs, ending it early at about _EXPORT_MAX_BYTES of JSON.

    Records are consumed as they arrive, so a page cut short never holds
    more than the byte limit (plus one fetch batch) in memory.

    Returns:
        The serialized LSRs, and whether the byte limit ended the page.
    """
    query = """
        MATCH (l:LSR {language_code: $lang})
        RETURN l
        ORDER BY l.id
        SKIP $offset
        LIMIT $limit
        """

    async def _fetch_page() -> tuple[list[dict[str, Any]], bool]:
        lsrs: list[dict[str, Any]] = []
        size = 0
        async with db.neo4j_session() as session:
            result = await session.run(Neo4jQuery(query, timeout=_EXPORT_TIMEOUT_SECONDS), params)
            async for record in result:
                lsr = _serialize_neo4j_value(record["l"])
                size += len(json.dumps(lsr, ensure_ascii=False, separators=(",", ":")).encode())
                if lsrs and size > _EXPORT_MAX_BYTES:
                    return lsrs, True
                lsrs.append(lsr)
        return lsrs, False

    page: tuple[list[dict[str, Any]], bool] = await asyncio.wait_for(
        _fetch_page(), _EXPORT_TIMEOUT_SECONDS + _CLIENT_DEADLINE_GRACE_SECONDS
    )
    return page


async def _run_bulk_export(db: DatabaseManager, request: BulkExportRequest) -> dict[str, Any]:
    """Export one page of LSRs and build the payload for the requested format."""
    params = {"lang": request.language, "offset": request.offset, "limit": request.limit}
    total_records = await _read_records(
        db,
        "MATCH (l:LSR {language_code: $lang}) RETURN count(l) AS total",
        params,
        timeout=_EXPORT_TIMEOUT_SECONDS,
    )
    total = total_records[0]["total"] if total_records else 0
    lsrs, size_limited = await _read_export_page(db, params)

    relationships: list[dict[str, Any]] = []
    relationships_truncated = False
    if request.include_relationships and lsrs:
        rel_records = await _read_records(
            db,
            """
            MATCH (a:LSR)-[r]->(b:LSR)
            WHERE a.id IN $ids
            RETURN a.id AS source, type(r) AS type, b.id AS target,
                   properties(r) AS properties
            ORDER BY source, type, target
            LIMIT $rel_limit
            """,
            {
                "ids": [lsr.get("id") for lsr in lsrs],
                "rel_limit": _EXPORT_MAX_RELATIONSHIPS + 1,
            },
            timeout=_EXPORT_TIMEOUT_SECONDS,
        )
        relationships_truncated = len(rel_records) > _EXPORT_MAX_RELATIONSHIPS
        relationships = [
            {
                "source": r["source"],
                "type": r["type"],
                "target": r["target"],
                "properties": _serialize_neo4j_value(r["properties"]),
            }
            for r in rel_records[:_EXPORT_MAX_RELATIONSHIPS]
        ]

    next_offset = request.offset + len(lsrs)
    truncated = next_offset < total
    payload: dict[str, Any] = {
        "format": request.format,
        "language": request.language,
        "offset": request.offset,
        "limit": request.limit,
        "count": len(lsrs),
        "total": total,
        "truncated": truncated,
        "next_offset": next_offset if truncated else None,
        "size_limited": size_limited,
        "relationship_count": len(relationships),
        "relationships_truncated": relationships_truncated,
    }
    if request.format == "csv":
        payload["csv"] = _rows_to_csv(lsrs)
        if request.include_relationships:
            payload["relationships_csv"] = _rows_to_csv(
                relationships, fieldnames=["source", "type", "target", "properties"]
            )
    else:
        payload["items"] = lsrs
        if request.include_relationships:
            payload["relationships"] = relationships
    return payload


def _csv_cell(value: Any) -> Any:
    """Render a JSON-safe value as a CSV cell (lists, dicts and booleans as JSON)."""
    if value is None:
        return ""
    if isinstance(value, bool | list | dict):
        return json.dumps(value, ensure_ascii=False)
    return value


def _rows_to_csv(rows: list[Any], fieldnames: list[str] | None = None) -> str:
    """Serialize exported dicts to CSV (header: the sorted union of keys by default)."""
    dict_rows = [row for row in rows if isinstance(row, dict)]
    if fieldnames is None:
        if not dict_rows:
            return ""
        fieldnames = sorted({key for row in dict_rows for key in row})
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for row in dict_rows:
        writer.writerow({key: _csv_cell(row.get(key)) for key in fieldnames})
    return buffer.getvalue()


@router.post("/bulk/export")
async def create_bulk_export(
    request: BulkExportRequest,
    db: DatabaseManager = Depends(get_db_manager),
) -> dict[str, Any]:
    """
    Export LSRs (and optionally their outgoing relationships) for a language.

    Exports are paged: at most `limit` (max 10,000) LSRs ordered by id,
    starting at `offset`. A page also ends once its LSRs reach about 10 MB
    of JSON (`size_limited` is then true); with their semantic vectors that
    is some 1,200 LSRs. The payload reports `total`, `truncated` and
    `next_offset` for fetching the next page. In CSV format the LSRs are in
    `csv` and the relationships in `relationships_csv`; list values are
    JSON-encoded.

    With run_async=false (default) the export runs inline and the payload
    is returned directly. With run_async=true a background job is created;
    poll /bulk/status/{job_id} and fetch the result from
    /bulk/result/{job_id} once completed. Results are kept for an hour, but
    the API holds only about 100 MB of them: past that the oldest finished
    jobs are dropped and answer 404 like expired ones, so fetch results soon.
    """
    logger.info(
        f"Bulk export for {request.language} in {request.format} format "
        f"(offset={request.offset}, limit={request.limit}, async={request.run_async})"
    )

    if request.run_async:

        async def _export_job() -> Any:
            # A failed job's error is shown by /bulk/status, so keep DB text out of it
            return await _guarded("Bulk export", lambda: _run_bulk_export(db, request))

        job = job_registry.submit(
            "bulk_export",
            _export_job,
            params={
                "language": request.language,
                "format": request.format,
                "offset": request.offset,
                "limit": request.limit,
            },
        )
        return {
            "status": "accepted",
            "job_id": job.id,
            "status_url": f"/api/v1/graph/bulk/status/{job.id}",
            "result_url": f"/api/v1/graph/bulk/result/{job.id}",
        }

    payload = await _guarded("Bulk export", lambda: _run_bulk_export(db, request))
    return {
        "status": "completed",
        "message": f"Exported {payload['count']} of {payload['total']} LSRs",
        **payload,
    }


_JOB_STORE_UNAVAILABLE = "Export job store (Redis) is not available"


def _get_export_job(job_id: str) -> Any:
    """Look up a bulk export job, raising NotFoundError if there is none.

    DatabaseError (503) when the job may exist but the shared job store
    cannot be read: a 404 would tell the client its job is gone.
    """
    try:
        job = job_registry.lookup(job_id)
    except JobStoreUnavailableError as e:
        raise DatabaseError(message=_JOB_STORE_UNAVAILABLE) from e
    if job is None or getattr(job, "kind", "bulk_export") != "bulk_export":
        raise NotFoundError(resource_type="Export job", resource_id=job_id)
    return job


@router.get("/bulk/status/{job_id}")
async def get_export_status(job_id: str) -> dict[str, Any]:
    """Get the status of a bulk export job (404 if unknown, expired or dropped
    to keep the results held within their limit; 503 if the job store cannot
    be reached)."""
    job = _get_export_job(job_id)
    status: dict[str, Any] = job.to_dict()
    status["download_url"] = (
        f"/api/v1/graph/bulk/result/{job_id}" if job.status == JobStatus.COMPLETED else None
    )
    return status


@router.get("/bulk/result/{job_id}")
async def get_export_result(job_id: str) -> dict[str, Any]:
    """Fetch the payload of a completed bulk export job."""
    job = _get_export_job(job_id)
    if job.status == JobStatus.FAILED:
        raise DatabaseError(message="Export job failed")
    if job.status != JobStatus.COMPLETED:
        return {"job_id": job_id, "status": job.status.value, "message": "Job still running"}
    try:
        result = job.result
    except JobStoreUnavailableError as e:
        raise DatabaseError(message=_JOB_STORE_UNAVAILABLE) from e
    if result is None:  # expired from the store after its status was read
        raise NotFoundError(resource_type="Export job result", resource_id=job_id)
    return {"job_id": job_id, "status": "completed", **result}


def _entity_ref(node: Any) -> Any:
    """Identify a relationship endpoint: its LSR id, else its Neo4j element id."""
    lsr_id = node.get("id") if isinstance(node, Node) else None
    return lsr_id if lsr_id is not None else getattr(node, "element_id", None)


def _serialize_relationship(rel: Relationship) -> dict[str, Any]:
    """Serialize a relationship with its type, direction and properties."""
    return {
        "type": rel.type,
        "source": _entity_ref(rel.start_node),
        "target": _entity_ref(rel.end_node),
        "properties": {k: _serialize_neo4j_value(v) for k, v in rel.items()},
    }


def _serialize_neo4j_value(value: Any) -> Any:
    """Convert a Neo4j value to plain JSON types, recursively."""
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        # JSON has no NaN or Infinity
        return value if math.isfinite(value) else str(value)
    if isinstance(value, Node):
        return {
            "labels": sorted(value.labels),
            **{k: _serialize_neo4j_value(v) for k, v in value.items()},
        }
    if isinstance(value, Relationship):
        return _serialize_relationship(value)
    if isinstance(value, Path):
        return _serialize_path(value)
    if isinstance(value, Point):
        return {"srid": value.srid, "coordinates": [float(c) for c in value]}
    if isinstance(value, dict):
        return {str(k): _serialize_neo4j_value(v) for k, v in value.items()}
    if hasattr(value, "iso_format"):
        # neo4j.time.DateTime, Date, Time and Duration (a tuple subclass)
        return value.iso_format()
    if isinstance(value, list | tuple):
        return [_serialize_neo4j_value(v) for v in value]
    if isinstance(value, bytes | bytearray):
        return base64.b64encode(bytes(value)).decode("ascii")
    if hasattr(value, "isoformat"):
        # Python datetime, date and time
        return value.isoformat()
    return str(value)


def _serialize_path(path: Path) -> dict[str, Any]:
    """Serialize a Neo4j path to a dictionary."""
    relationships = [_serialize_relationship(rel) for rel in path.relationships]
    return {
        "nodes": [_serialize_neo4j_value(node) for node in path.nodes],
        "relationships": relationships,
        "length": len(relationships),
    }
