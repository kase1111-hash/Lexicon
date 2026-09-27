"""LSR (Lexical State Record) API routes."""

import logging
from typing import cast
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from src.exceptions import (
    DuplicateError,
    InvalidDateRangeError,
    InvalidLanguageCodeError,
    LSRNotFoundError,
)
from src.models import ErrorResponse
from src.models.lsr import LSR
from src.repositories.lsr_repository import (
    DEFAULT_ETYMOLOGY_DEPTH,
    MAX_LINEAGE_DEPTH,
    LSRRepository,
)
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
    normalize_language_code,
    sanitize_string,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# Deepest page a search may ask for; larger offsets are a 400, not a value the
# database cannot take (Neo4j's SKIP is a 64-bit integer)
MAX_SEARCH_OFFSET = 10_000_000

# Glottolog languoid codes (e.g. "nort3160"), which WOLD uses for donor
# languages that have no ISO 639-3 code.


async def get_lsr_repository() -> LSRRepository:
    """Dependency to get the LSR repository."""
    db = await get_db()
    return LSRRepository(db)


def _parse_language(value: str) -> str:
    """Normalize a language filter to a stored code, or raise a 400.

    Accepts ISO 639-3 codes and extensions ("eng", "gem-pro"), common
    ISO 639-1 codes ("en" -> "eng") and Glottolog codes ("nort3160").
    """
    try:
        return normalize_language_code(value)
    except ValueError as e:
        raise InvalidLanguageCodeError(language_code=value) from e


def _lsr_payload(repo: LSRRepository, lsr: LSR) -> dict:
    """An LSR read through `repo` as JSON, with its relationship counts.

    The relationship id lists hold at most MAX_LINKED_IDS ids each;
    relationship_counts has the full numbers and relationship_ids_truncated
    says whether any list was cut short.
    """
    return {**lsr.model_dump(mode="json"), **repo.relationship_summary(lsr.id)}


async def _require_lsr(repo: LSRRepository, lsr_id: UUID) -> None:
    """Raise LSRNotFoundError (404) unless the LSR exists."""
    if not await repo.exists(lsr_id):
        raise LSRNotFoundError(lsr_id=str(lsr_id))


@router.get("/search")
async def search_lsr(
    form: str | None = Query(
        None,
        description=(
            "Form to search: substring of the written form (case- and "
            "diacritic-insensitive). With Elasticsearch connected, near misses "
            "(typos) also match and results are ranked by relevance."
        ),
        max_length=200,
    ),
    language: str | None = Query(
        None,
        description="Language code: ISO 639-3 ('eng', 'gem-pro'), ISO 639-1 ('en') or Glottocode",
        max_length=20,
    ),
    date_start: int | None = Query(
        None,
        description=(
            "Start year (negative for BCE). With date_end, matches LSRs in use at any "
            "point in the range; undated LSRs are excluded."
        ),
        ge=-10000,
        le=2100,
    ),
    date_end: int | None = Query(None, description="End year", ge=-10000, le=2100),
    semantic_field: str | None = Query(
        None, description="Semantic field (exact match)", max_length=50
    ),
    limit: int = Query(20, ge=1, le=100, description="Maximum results to return"),
    offset: int = Query(0, ge=0, le=MAX_SEARCH_OFFSET, description="Number of results to skip"),
    repo: LSRRepository = Depends(get_lsr_repository),
) -> dict:
    """
    Search for LSRs matching criteria.

    Supports filtering by:
    - Form (substring; fuzzy as well when Elasticsearch is connected)
    - Language code (invalid codes are rejected with 400 INVALID_LANGUAGE_CODE)
    - Date range: an LSR matches when it was in use during the range (first
      attested no later than date_end, last attested no earlier than
      date_start or still in use); undated LSRs never match a date filter
    - Semantic field

    Returns paginated results in a stable order (offset at most 10,000,000).
    `filters` echoes the filters actually applied (e.g. language "en" is
    applied as "eng").
    """
    # Sanitize inputs
    if form:
        form = sanitize_string(form, max_length=200)
    if language:
        language = _parse_language(language)
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
        "results": [_lsr_payload(repo, lsr) for lsr in results],
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

    # Cache the result, unless it lacks the fuzzy matches of a working index
    if not repo.search_degraded:
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

    Returns the stored Lexical State Record. The relationship fields list
    the directly linked LSRs: ancestor_ids / descendant_ids (DESCENDS_FROM),
    cognate_ids (COGNATE_OF), loan_source_id (the most confident
    BORROWED_FROM donor) and loan_target_ids. Each list holds at most 100
    ids (the lowest); relationship_counts gives the full counts and
    relationship_ids_truncated says whether a list was cut short. The
    /etymology, /descendants, /cognates and /borrowings endpoints give the
    full traversals.
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
    result = {"data": _lsr_payload(repo, lsr)}

    # Cache the result
    await cache.set(cache_key, result, LSR_CACHE_TTL)
    return result


@router.post(
    "/",
    status_code=201,
    responses={400: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
async def create_lsr(
    request: LSRCreateRequest,
    repo: LSRRepository = Depends(get_lsr_repository),
) -> dict:
    """
    Create a new LSR record.

    The form_orthographic and language_code are required.
    Other fields are optional.

    Returns 409 DUPLICATE_ERROR if an LSR with the same normalized form,
    language and date_start already exists.
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

    existing_id = await repo.find_duplicate(lsr.form_normalized, lsr.language_code, lsr.date_start)
    if existing_id:
        raise DuplicateError(resource_type="LSR", identifier=existing_id)

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

    # Invalidate caches: this LSR, the searches, and every cached LSR, since
    # its neighbours' cached records list it among their relationship ids.
    await invalidate_lsr_cache(str(lsr_id))
    await (await get_cache()).delete_pattern("lexicon:lsr:*")

    return {"message": f"LSR {lsr_id} deleted successfully"}


@router.get(
    "/{lsr_id}/etymology",
    responses={404: {"model": ErrorResponse}},
)
async def get_etymology(
    lsr_id: UUID,
    max_depth: int = Query(
        DEFAULT_ETYMOLOGY_DEPTH,
        ge=1,
        le=MAX_LINEAGE_DEPTH,
        description="Maximum number of DESCENDS_FROM steps to follow",
    ),
    repo: LSRRepository = Depends(get_lsr_repository),
) -> dict:
    """
    Get the etymology chain to the proto-form.

    Follows DESCENDS_FROM relationships back to the deepest ancestor that
    has no further ancestor (the proto-form), along a shortest path. The
    chain starts with the LSR itself; an LSR without ancestors is its own
    proto-form (depth 0). If max_depth cuts off any line of ancestry
    (so a deeper proto-form may lie beyond it), `truncated` is true, the
    chain ends at the farthest ancestor found and proto_form is null.
    """
    logger.info(f"Fetching etymology for LSR: {lsr_id}")

    chain, complete = await repo.get_etymology_chain(lsr_id, max_depth=max_depth)
    if not chain:
        raise LSRNotFoundError(lsr_id=str(lsr_id))

    return {
        "lsr_id": str(lsr_id),
        "chain": chain,
        "proto_form": chain[-1] if complete else None,
        "depth": len(chain) - 1,
        "truncated": not complete,
    }


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

    await _require_lsr(repo, lsr_id)

    descendants = await repo.get_descendants(lsr_id, depth=depth, limit=500)
    return {
        "lsr_id": str(lsr_id),
        "descendants": descendants,
        "count": len(descendants),
        "depth": depth,
    }


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

    Returns words in other languages that share a common DESCENDS_FROM
    ancestor with this LSR, excluding its own ancestors and descendants,
    plus any word linked to it by a COGNATE_OF relationship.
    """
    logger.info(f"Fetching cognates for LSR: {lsr_id}")

    await _require_lsr(repo, lsr_id)

    cognates = await repo.get_cognates(lsr_id, limit=100)
    by_language: dict[str, list] = {}
    for entry in cognates:
        by_language.setdefault(entry.get("language_code") or "unknown", []).append(entry)

    return {
        "lsr_id": str(lsr_id),
        "cognates": cognates,
        "cognate_count": len(cognates),
        "languages": list(by_language.keys()),
        "by_language": by_language,
    }


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

    Returns both words this LSR borrowed from (borrowed_from) and words
    that borrowed from this LSR (borrowed_to), each with the confidence and
    evidence recorded on the BORROWED_FROM relationship.
    """
    logger.info(f"Fetching borrowings for LSR: {lsr_id}")

    await _require_lsr(repo, lsr_id)

    borrowed_from, borrowed_to = await repo.get_borrowings(lsr_id)
    return {
        "lsr_id": str(lsr_id),
        "borrowed_from": borrowed_from,
        "borrowed_to": borrowed_to,
    }
