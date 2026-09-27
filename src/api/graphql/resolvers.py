"""GraphQL resolver logic - data access shared by the schema's fields.

These functions return plain dicts / domain models; src/api/graphql/schema.py
converts them to Strawberry types. Keeping data access here lets the schema
stay declarative and makes the resolvers testable without a GraphQL request.

Graph traversals go through LSRRepository, so GraphQL and REST apply the same
rules (e.g. what counts as a cognate). A database failure is raised as
DatabaseError, which the schema reports as a GraphQL error; it is never
turned into null or an empty list, which would read as "not found".
"""

import asyncio
import logging
from collections import Counter
from collections.abc import Awaitable
from typing import Any, TypeVar
from uuid import UUID

from neo4j import Query as Neo4jQuery
from neo4j.exceptions import DriverError, Neo4jError

from src.exceptions import LSRNotFoundError
from src.models.lsr import LSR
from src.repositories.lsr_repository import (
    _CLIENT_DEADLINE_GRACE_SECONDS,
    DEFAULT_ETYMOLOGY_DEPTH,
    MAX_LINEAGE_DEPTH,
    READ_TIMEOUT_SECONDS,
    LSRRepository,
    database_error,
)
from src.utils.db import DatabaseManager
from src.utils.languages import language_name

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Most LSRs returned by one nested traversal field
MAX_ANCESTORS = 100
MAX_DESCENDANTS = 500
MAX_COGNATES = 100
MAX_DESCENDANT_DEPTH = 10


async def _guarded(what: str, operation: Awaitable[T]) -> T:
    """Await a data access call, mapping driver failures to DatabaseError.

    The repository already does this; the analysis loaders only map an
    absent driver, so a Neo4j that goes away mid-request would otherwise
    surface with the driver's message (addresses, internals).
    """
    try:
        return await operation
    except (RuntimeError, Neo4jError, DriverError, OSError, TimeoutError) as e:
        raise database_error(e, what) from e


def _parse_id(lsr_id: str) -> UUID | None:
    """The UUID of an LSR id argument, or None if it is not one (no such LSR)."""
    try:
        return UUID(str(lsr_id))
    except (ValueError, AttributeError):
        return None


def is_living(language_code: str, reconstructed: bool) -> bool | None:
    """Whether a language is living, as far as the graph can tell.

    The graph records no living/extinct status, so this is only False for a
    proto-language (a "-pro" code, or only reconstructed forms) and None
    (unknown) otherwise: Latin or Old English LSRs look like any other.
    """
    if reconstructed or language_code.endswith("-pro"):
        return False
    return None


def _node_to_dict(node: Any) -> dict[str, Any]:
    """Convert a Neo4j LSR node to the dict shape the schema layer expects."""
    props = dict(node)
    definitions = [props.get("definition_primary"), *(props.get("definitions_alternate") or [])]
    return {
        "id": props.get("id"),
        "form": props.get("form_orthographic", ""),
        "form_phonetic": props.get("form_phonetic"),
        "language_code": props.get("language_code", ""),
        "language_name": props.get("language_name", ""),
        "language_family": props.get("language_family"),
        "date_start": props.get("date_start"),
        "date_end": props.get("date_end"),
        "definition": props.get("definition_primary"),
        "definitions": [d for d in definitions if d],
        "confidence": props.get("confidence_overall", 1.0),
        "reconstruction_flag": props.get("reconstruction_flag", False),
    }


def lsr_model_to_dict(lsr: LSR) -> dict[str, Any]:
    """Convert a domain LSR model to the dict shape the schema layer expects."""
    return {
        "id": str(lsr.id),
        "form": lsr.form_orthographic,
        "form_phonetic": lsr.form_phonetic or None,
        "language_code": lsr.language_code,
        "language_name": lsr.language_name,
        "language_family": lsr.language_family or None,
        "date_start": lsr.date_start,
        "date_end": lsr.date_end,
        "definition": lsr.definition_primary or None,
        "confidence": lsr.confidence_overall,
        "reconstruction_flag": lsr.reconstruction_flag,
        "attestations": [
            {
                "text": a.text_excerpt,
                "source": a.text_source,
                "date": a.text_date,
                "url": a.url,
            }
            for a in lsr.attestations
        ],
        "definitions": [d for d in [lsr.definition_primary, *lsr.definitions_alternate] if d],
    }


async def resolve_lsr(db: DatabaseManager, lsr_id: str) -> dict[str, Any] | None:
    """Fetch a single LSR by ID, or None if there is no such LSR.

    Raises:
        DatabaseError: If the graph cannot be queried.
    """
    uuid = _parse_id(lsr_id)
    if uuid is None:
        return None
    try:
        lsr = await LSRRepository(db).get_by_id(uuid)
    except LSRNotFoundError:
        return None
    return lsr_model_to_dict(lsr)


async def resolve_search_lsr(
    db: DatabaseManager,
    form: str | None = None,
    language: str | None = None,
    date_start: int | None = None,
    date_end: int | None = None,
    limit: int = 20,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Search LSRs via the repository (Elasticsearch with Neo4j fallback)."""
    results, _total = await LSRRepository(db).search(
        form=form,
        language=language,
        date_start=date_start,
        date_end=date_end,
        limit=min(max(limit, 1), 100),
        offset=max(offset, 0),
    )
    return [lsr_model_to_dict(lsr) for lsr in results]


async def resolve_languages(
    db: DatabaseManager,
    family: str | None = None,
    iso_code: str | None = None,
) -> list[dict[str, Any]]:
    """List the languages present in the graph, one entry per language code.

    A language's name and family are the most frequent non-empty ones among
    its LSRs (REST-created LSRs carry none; the name then comes from the
    code table, else it is the code). `family` filters on that family.
    """
    params: dict[str, Any] = {}
    where = "l.language_code IS NOT NULL AND l.language_code <> ''"
    if iso_code:
        where = "l.language_code = $iso_code"
        params["iso_code"] = iso_code

    query = f"""
    MATCH (l:LSR)
    WHERE {where}
    RETURN l.language_code AS iso_code,
           l.language_name AS name,
           l.language_family AS family,
           count(l) AS lsrs,
           count(CASE WHEN l.reconstruction_flag THEN 1 END) AS reconstructed
    """

    async def _fetch() -> list[Any]:
        async with db.neo4j_session() as session:
            result = await session.run(Neo4jQuery(query, timeout=READ_TIMEOUT_SECONDS), params)
            return [record async for record in result]

    # The client deadline also covers a stalled Neo4j, which never times out
    records = await _guarded(
        "Language lookup",
        asyncio.wait_for(_fetch(), READ_TIMEOUT_SECONDS + _CLIENT_DEADLINE_GRACE_SECONDS),
    )

    grouped: dict[str, dict[str, Any]] = {}
    for record in records:
        code = record["iso_code"]
        entry = grouped.setdefault(
            code, {"names": Counter(), "families": Counter(), "lsrs": 0, "reconstructed": 0}
        )
        if record["name"] and record["name"] != code:
            entry["names"][record["name"]] += record["lsrs"]
        if record["family"]:
            entry["families"][record["family"]] += record["lsrs"]
        entry["lsrs"] += record["lsrs"]
        entry["reconstructed"] += record["reconstructed"]

    def most_frequent(counter: Counter) -> str | None:
        ranked = sorted(counter.items(), key=lambda item: (-item[1], item[0]))
        return ranked[0][0] if ranked else None

    languages = []
    for code in sorted(grouped):
        entry = grouped[code]
        family_name = most_frequent(entry["families"])
        if family and family_name != family:
            continue
        languages.append(
            {
                "iso_code": code,
                "name": most_frequent(entry["names"]) or language_name(code),
                "family": family_name,
                "branch_path": [family_name] if family_name else [],
                "is_living": is_living(code, entry["reconstructed"] == entry["lsrs"]),
            }
        )
    return languages


async def resolve_lsr_ancestors(
    db: DatabaseManager, lsr_id: str, depth: int = 10
) -> list[dict[str, Any]]:
    """Resolve the distinct ancestor LSRs within `depth` DESCENDS_FROM hops, nearest first."""
    uuid = _parse_id(lsr_id)
    if uuid is None:
        return []
    return await LSRRepository(db).get_ancestors(
        uuid,
        max_depth=min(max(depth, 1), MAX_LINEAGE_DEPTH),
        limit=MAX_ANCESTORS,
        summarize=_node_to_dict,
    )


async def resolve_lsr_descendants(
    db: DatabaseManager, lsr_id: str, depth: int = 3
) -> list[dict[str, Any]]:
    """Resolve descendant LSRs by following DESCENDS_FROM edges inward."""
    uuid = _parse_id(lsr_id)
    if uuid is None:
        return []
    return await LSRRepository(db).get_descendants(
        uuid,
        depth=min(max(depth, 1), MAX_DESCENDANT_DEPTH),
        limit=MAX_DESCENDANTS,
        summarize=_node_to_dict,
    )


async def resolve_lsr_cognates(db: DatabaseManager, lsr_id: str) -> list[dict[str, Any]]:
    """Resolve cognates with the REST rules (LSRRepository.get_cognates)."""
    uuid = _parse_id(lsr_id)
    if uuid is None:
        return []
    return await LSRRepository(db).get_cognates(uuid, limit=MAX_COGNATES, summarize=_node_to_dict)


async def resolve_etymology_chain(
    db: DatabaseManager, lsr_id: str, max_depth: int = DEFAULT_ETYMOLOGY_DEPTH
) -> dict[str, Any] | None:
    """Resolve the etymology chain back to the proto-form, as REST does.

    Returns:
        None if there is no such LSR. Otherwise steps run from the LSR to
        the farthest ancestor found; proto_form is that ancestor only when
        the chain is complete (not cut off by max_depth), as in
        GET /lsr/{id}/etymology.
    """
    uuid = _parse_id(lsr_id)
    if uuid is None:
        return None
    steps, complete = await LSRRepository(db).get_etymology_chain(
        uuid, max_depth=min(max(max_depth, 1), MAX_LINEAGE_DEPTH), summarize=_node_to_dict
    )
    if not steps:
        return None
    return {
        "steps": steps,
        "proto_form": steps[-1] if complete else None,
        "depth": len(steps) - 1,
        "truncated": not complete,
    }


async def resolve_semantic_trajectory(
    db: DatabaseManager, form: str, language: str
) -> dict[str, Any]:
    """Resolve the semantic trajectory for a word via the drift analyzer.

    Points and shift events are empty unless senses from two different years
    can be compared; "status" and "explanation" say why, as in REST.
    """
    from src.analysis.data_access import load_trajectory
    from src.analysis.semantic_drift import SemanticDriftAnalyzer, assess_trajectory

    points = await _guarded("Semantic trajectory", load_trajectory(db, form, language))
    analyzer = SemanticDriftAnalyzer(lsr_data={f"{form.lower()}:{language}": points})
    trajectory = analyzer.get_trajectory(form, language)

    # Drift needs dated senses with definitions from two different years
    status, explanation = assess_trajectory(trajectory, form, language)
    if trajectory is None or status != "ok":
        return {"points": [], "shift_events": [], "status": status, "explanation": explanation}

    return {
        "status": status,
        "explanation": explanation,
        "points": [
            {
                "date": p.date,
                "embedding_2d": list(p.embedding_2d),
                "definition": p.definition,
                "attestation_count": p.attestation_count,
            }
            for p in trajectory.points
        ],
        "shift_events": [
            {
                "date": s.date,
                "change_type": s.change_type,
                "confidence": s.confidence,
                "before_meaning": s.before_meaning,
                "after_meaning": s.after_meaning,
            }
            for s in trajectory.shift_events
        ],
    }


async def resolve_date_text(db: DatabaseManager, text: str, language: str) -> dict[str, Any]:
    """Run text-dating analysis for the GraphQL dateText field."""
    from src.analysis.data_access import load_vocabulary_for_text
    from src.analysis.dating import TextDating

    lookup = await _guarded("Text dating", load_vocabulary_for_text(db, language, text))
    analysis = TextDating(lsr_lookup=lookup).date_text(text, language)
    return {
        "predicted_range": list(analysis.predicted_range) if analysis.predicted_range else None,
        "confidence": analysis.confidence,
        "status": analysis.status,
        "explanation": analysis.explanation,
        "content_words": analysis.content_tokens,
        "dated_words": analysis.matched_tokens,
        "diagnostic_vocabulary": [
            {
                "form": w["word"],
                "earliest_attestation": w["date_start"],
                "date_label": w.get("date_label") or "",
                "last_attestation": w["date_end"],
                "sets_bound": w["sets_bound"],
            }
            for w in analysis.diagnostic_vocabulary
        ],
    }


async def resolve_detect_anachronisms(
    db: DatabaseManager, text: str, claimed_date: int, language: str
) -> dict[str, Any]:
    """Run anachronism detection for the GraphQL detectAnachronisms field."""
    from src.analysis.data_access import load_vocabulary_for_text
    from src.analysis.dating import TextDating

    lookup = await _guarded("Anachronism detection", load_vocabulary_for_text(db, language, text))
    analysis = TextDating(lsr_lookup=lookup).detect_anachronisms(text, claimed_date, language)
    return {
        "anachronisms": [
            {
                "form": a["word"],
                "type": a["type"],
                "earliest_attestation": a.get("earliest_attestation"),
                "date_label": a.get("date_label") or "",
                "last_attestation": a.get("last_attestation"),
                "gap_years": a["gap_years"],
                "severity": a["severity"],
            }
            for a in analysis.anachronisms
        ],
        "verdict": analysis.verdict,
        "confidence": analysis.confidence,
        "explanation": analysis.explanation,
        "content_words": analysis.content_tokens,
        "dated_words": analysis.dated_tokens,
    }
