"""LSR (Lexical State Record) API routes."""

import logging
from typing import cast
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from src.exceptions import DatabaseError, InvalidDateRangeError
from src.models import ErrorResponse
from src.models.lsr import LSR
from src.repositories.lsr_repository import LSRRepository
from src.utils.cache import (
    LSR_CACHE_TTL,
    SEARCH_CACHE_TTL,
    get_cache,
    invalidate_lsr_cache,
    invalidate_search_cache,
    make_cache_key,
)
from src.utils.db import get_db
from src.utils.validation import (
    LSRCreateRequest,
    sanitize_iso_code,
    sanitize_string,
)

logger = logging.getLogger(__name__)

router = APIRouter()


async def get_lsr_repository() -> LSRRepository:
    """Dependency to get the LSR repository."""
    db = await get_db()
    return LSRRepository(db)


@router.get("/search")
async def search_lsr(
    form: str | None = Query(None, description="Form to search (exact or fuzzy)", max_length=200),
    language: str | None = Query(None, description="ISO 639-3 language code", max_length=10),
    date_start: int | None = Query(
        None, description="Start year (negative for BCE)", ge=-10000, le=2100
    ),
    date_end: int | None = Query(None, description="End year", ge=-10000, le=2100),
    semantic_field: str | None = Query(None, description="WordNet synset ID filter", max_length=50),
    limit: int = Query(20, ge=1, le=100, description="Maximum results to return"),
    offset: int = Query(0, ge=0, description="Number of results to skip"),
    repo: LSRRepository = Depends(get_lsr_repository),
) -> dict:
    """
    Search for LSRs matching criteria.

    Supports filtering by:
    - Form (orthographic, supports fuzzy matching)
    - Language code (ISO 639-3)
    - Date range
    - Semantic field (WordNet synset)

    Returns paginated results.
    """
    # Sanitize inputs
    if form:
        form = sanitize_string(form, max_length=200)
    if language:
        language = sanitize_iso_code(language)
    if semantic_field:
        semantic_field = sanitize_string(semantic_field, max_length=50)

    # Validate date range
    if date_start is not None and date_end is not None:
        if date_end < date_start:
            raise InvalidDateRangeError(start_date=date_start, end_date=date_end)

    logger.info(f"Searching LSRs: form={form}, language={language}, dates={date_start}-{date_end}")

    # Check cache first
    cache = await get_cache()
    cache_key = make_cache_key(
        "search",
        form=form,
        language=language,
        date_start=date_start,
        date_end=date_end,
        semantic_field=semantic_field,
        limit=limit,
        offset=offset,
    )
    cached = await cache.get(cache_key)
    if cached:
        logger.debug(f"Cache hit for search: {cache_key}")
        return cast(dict, cached)

    # Perform search
    results, total = await repo.search(
        form=form,
        language=language,
        date_start=date_start,
        date_end=date_end,
        semantic_field=semantic_field,
        limit=limit,
        offset=offset,
    )

    response = {
        "results": [lsr.model_dump(mode="json") for lsr in results],
        "total": total,
        "limit": limit,
        "offset": offset,
        "filters": {
            "form": form,
            "language": language,
            "date_start": date_start,
            "date_end": date_end,
            "semantic_field": semantic_field,
        },
    }

    # Cache the result
    await cache.set(cache_key, response, SEARCH_CACHE_TTL)
    return response


@router.get(
    "/{lsr_id}",
    responses={404: {"model": ErrorResponse}},
)
async def get_lsr(
    lsr_id: UUID,
    repo: LSRRepository = Depends(get_lsr_repository),
) -> dict:
    """
    Get a full LSR record by ID.

    Returns the complete Lexical State Record including all fields,
    attestations, and relationship IDs.
    """
    # Check cache first
    cache = await get_cache()
    cache_key = make_cache_key("lsr", str(lsr_id))
    cached = await cache.get(cache_key)
    if cached:
        logger.debug(f"Cache hit for LSR: {lsr_id}")
        return cast(dict, cached)

    logger.info(f"Fetching LSR: {lsr_id}")
    lsr = await repo.get_by_id(lsr_id)
    result = {"data": lsr.model_dump(mode="json")}

    # Cache the result
    await cache.set(cache_key, result, LSR_CACHE_TTL)
    return result


@router.post(
    "/",
    status_code=201,
    responses={400: {"model": ErrorResponse}},
)
async def create_lsr(
    request: LSRCreateRequest,
    repo: LSRRepository = Depends(get_lsr_repository),
) -> dict:
    """
    Create a new LSR record.

    The form_orthographic and language_code are required.
    Other fields are optional.
    """
    logger.info(f"Creating LSR: {request.form_orthographic} ({request.language_code})")

    # Create LSR from request
    lsr = LSR(
        form_orthographic=request.form_orthographic,
        form_phonetic=request.form_phonetic,
        language_code=request.language_code,
        definition_primary=request.definition_primary,
        date_start=request.date_start,
        date_end=request.date_end,
    )

    # Persist to database
    created_lsr = await repo.create(lsr)

    # Invalidate search cache since results may have changed
    await invalidate_search_cache()

    return {
        "message": "LSR created successfully",
        "data": created_lsr.model_dump(mode="json"),
    }


@router.delete(
    "/{lsr_id}",
    responses={404: {"model": ErrorResponse}},
)
async def delete_lsr(
    lsr_id: UUID,
    repo: LSRRepository = Depends(get_lsr_repository),
) -> dict:
    """
    Delete an LSR record by ID.

    This will also remove all relationships to/from this LSR.
    """
    logger.info(f"Deleting LSR: {lsr_id}")
    await repo.delete(lsr_id)

    # Invalidate caches
    await invalidate_lsr_cache(str(lsr_id))

    return {"message": f"LSR {lsr_id} deleted successfully"}


@router.get(
    "/{lsr_id}/etymology",
    responses={404: {"model": ErrorResponse}},
)
async def get_etymology(
    lsr_id: UUID,
    repo: LSRRepository = Depends(get_lsr_repository),
) -> dict:
    """
    Get the full etymology chain to proto-form.

    Traces the DESCENDS_FROM relationships back to the earliest
    reconstructed or attested ancestor.
    """
    logger.info(f"Fetching etymology for LSR: {lsr_id}")

    # Verify LSR exists
    await repo.get_by_id(lsr_id)

    query = """
    MATCH path = (start:LSR {id: $lsr_id})-[:DESCENDS_FROM*0..]->(ancestor:LSR)
    WHERE NOT (ancestor)-[:DESCENDS_FROM]->()
    RETURN path
    ORDER BY length(path) DESC
    LIMIT 1
    """

    try:
        async with repo.db.neo4j_session() as session:
            result = await session.run(query, {"lsr_id": str(lsr_id)})
            record = await result.single()

            if not record:
                return {
                    "lsr_id": str(lsr_id),
                    "chain": [],
                    "proto_form": None,
                    "depth": 0,
                }

            path = record["path"]
            chain = []
            for node in path.nodes:
                chain.append(
                    {
                        "id": dict(node).get("id"),
                        "form": dict(node).get("form_orthographic"),
                        "language_code": dict(node).get("language_code"),
                        "language_name": dict(node).get("language_name"),
                        "date_start": dict(node).get("date_start"),
                        "date_end": dict(node).get("date_end"),
                        "definition": dict(node).get("definition_primary"),
                    }
                )

            return {
                "lsr_id": str(lsr_id),
                "chain": chain,
                "proto_form": chain[-1] if chain else None,
                "depth": len(chain) - 1,
            }

    except RuntimeError as e:
        raise DatabaseError(message=f"Neo4j not connected: {e}") from e
    except Exception as e:
        logger.error(f"Etymology chain retrieval failed: {e}")
        raise DatabaseError(message=f"Etymology chain retrieval failed: {e}") from e


@router.get(
    "/{lsr_id}/descendants",
    responses={404: {"model": ErrorResponse}},
)
async def get_descendants(
    lsr_id: UUID,
    depth: int = Query(3, ge=1, le=10, description="Maximum depth to traverse"),
    repo: LSRRepository = Depends(get_lsr_repository),
) -> dict:
    """
    Get descendant tree.

    Returns all LSRs that descend from this one, up to the specified depth.
    """
    logger.info(f"Fetching descendants for LSR: {lsr_id}, depth={depth}")

    # Verify LSR exists
    await repo.get_by_id(lsr_id)

    query = f"""
    MATCH (start:LSR {{id: $lsr_id}})<-[:DESCENDS_FROM*1..{depth}]-(descendant:LSR)
    RETURN DISTINCT descendant
    LIMIT 500
    """

    try:
        async with repo.db.neo4j_session() as session:
            result = await session.run(query, {"lsr_id": str(lsr_id)})
            records = await result.fetch(500)

            descendants = []
            for record in records:
                node = record["descendant"]
                props = dict(node)
                descendants.append(
                    {
                        "id": props.get("id"),
                        "form": props.get("form_orthographic"),
                        "language_code": props.get("language_code"),
                        "language_name": props.get("language_name"),
                        "date_start": props.get("date_start"),
                        "date_end": props.get("date_end"),
                        "definition": props.get("definition_primary"),
                    }
                )

            return {
                "lsr_id": str(lsr_id),
                "descendants": descendants,
                "count": len(descendants),
                "depth": depth,
            }

    except RuntimeError as e:
        raise DatabaseError(message=f"Neo4j not connected: {e}") from e
    except Exception as e:
        logger.error(f"Descendant retrieval failed: {e}")
        raise DatabaseError(message=f"Descendant retrieval failed: {e}") from e


@router.get(
    "/{lsr_id}/cognates",
    responses={404: {"model": ErrorResponse}},
)
async def get_cognates(
    lsr_id: UUID,
    repo: LSRRepository = Depends(get_lsr_repository),
) -> dict:
    """
    Get all cognate LSRs across languages.

    Returns words in other languages that share a common ancestor
    with this LSR.
    """
    logger.info(f"Fetching cognates for LSR: {lsr_id}")

    # Verify LSR exists
    await repo.get_by_id(lsr_id)

    # Find the proto-ancestor, then find all its descendants in other languages
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
        async with repo.db.neo4j_session() as session:
            result = await session.run(query, {"lsr_id": str(lsr_id)})
            records = await result.fetch(100)

            cognates = []
            by_language: dict[str, list] = {}
            for record in records:
                node = record["cognate"]
                props = dict(node)
                entry = {
                    "id": props.get("id"),
                    "form": props.get("form_orthographic"),
                    "language_code": props.get("language_code"),
                    "language_name": props.get("language_name"),
                    "date_start": props.get("date_start"),
                    "date_end": props.get("date_end"),
                    "definition": props.get("definition_primary"),
                }
                cognates.append(entry)
                lang = props.get("language_code", "unknown")
                by_language.setdefault(lang, []).append(entry)

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
        raise DatabaseError(message=f"Cognate retrieval failed: {e}") from e


@router.get(
    "/{lsr_id}/borrowings",
    responses={404: {"model": ErrorResponse}},
)
async def get_borrowings(
    lsr_id: UUID,
    repo: LSRRepository = Depends(get_lsr_repository),
) -> dict:
    """
    Get borrowing relationships for an LSR.

    Returns both words this LSR borrowed from (loan_source)
    and words that borrowed from this LSR (loan_targets).
    """
    logger.info(f"Fetching borrowings for LSR: {lsr_id}")

    # Verify LSR exists
    await repo.get_by_id(lsr_id)

    # Find what this word borrowed from
    source_query = """
    MATCH (l:LSR {id: $lsr_id})-[:BORROWED_FROM]->(donor:LSR)
    RETURN donor
    LIMIT 10
    """

    # Find what borrowed from this word
    target_query = """
    MATCH (l:LSR {id: $lsr_id})<-[:BORROWED_FROM]-(recipient:LSR)
    RETURN recipient
    LIMIT 100
    """

    try:
        async with repo.db.neo4j_session() as session:
            # Get loan sources (words this LSR borrowed from)
            source_result = await session.run(source_query, {"lsr_id": str(lsr_id)})
            source_records = await source_result.fetch(10)

            borrowed_from = []
            for record in source_records:
                props = dict(record["donor"])
                borrowed_from.append(
                    {
                        "id": props.get("id"),
                        "form": props.get("form_orthographic"),
                        "language_code": props.get("language_code"),
                        "language_name": props.get("language_name"),
                        "definition": props.get("definition_primary"),
                    }
                )

            # Get loan targets (words that borrowed from this LSR)
            target_result = await session.run(target_query, {"lsr_id": str(lsr_id)})
            target_records = await target_result.fetch(100)

            borrowed_to = []
            for record in target_records:
                props = dict(record["recipient"])
                borrowed_to.append(
                    {
                        "id": props.get("id"),
                        "form": props.get("form_orthographic"),
                        "language_code": props.get("language_code"),
                        "language_name": props.get("language_name"),
                        "definition": props.get("definition_primary"),
                    }
                )

            return {
                "lsr_id": str(lsr_id),
                "borrowed_from": borrowed_from,
                "borrowed_to": borrowed_to,
            }

    except RuntimeError as e:
        raise DatabaseError(message=f"Neo4j not connected: {e}") from e
    except Exception as e:
        logger.error(f"Borrowing retrieval failed: {e}")
        raise DatabaseError(message=f"Borrowing retrieval failed: {e}") from e
