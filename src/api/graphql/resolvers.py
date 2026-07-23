"""GraphQL resolver logic - data access shared by the schema's fields.

These functions return plain dicts / domain models; src/api/graphql/schema.py
converts them to Strawberry types. Keeping data access here lets the schema
stay declarative and makes the resolvers testable without a GraphQL request.
"""

import logging
from typing import Any
from uuid import UUID

from src.exceptions import LSRNotFoundError
from src.models.lsr import LSR
from src.repositories.lsr_repository import LSRRepository
from src.utils.db import DatabaseManager

logger = logging.getLogger(__name__)


def _node_to_dict(node: Any) -> dict[str, Any]:
    """Convert a Neo4j LSR node to the dict shape the schema layer expects."""
    props = dict(node)
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
    """Fetch a single LSR by ID, or None if missing/invalid."""
    try:
        uuid = UUID(lsr_id)
    except (ValueError, AttributeError):
        return None
    repo = LSRRepository(db)
    try:
        lsr = await repo.get_by_id(uuid)
    except LSRNotFoundError:
        return None
    except Exception as e:
        logger.warning(f"GraphQL lsr({lsr_id}) failed: {e}")
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
    repo = LSRRepository(db)
    try:
        results, _total = await repo.search(
            form=form,
            language=language,
            date_start=date_start,
            date_end=date_end,
            limit=min(max(limit, 1), 100),
            offset=max(offset, 0),
        )
    except Exception as e:
        logger.warning(f"GraphQL searchLsr failed: {e}")
        return []
    return [lsr_model_to_dict(lsr) for lsr in results]


async def resolve_languages(
    db: DatabaseManager,
    family: str | None = None,
    iso_code: str | None = None,
) -> list[dict[str, Any]]:
    """List distinct languages present in the graph."""
    where = ["l.language_code IS NOT NULL"]
    params: dict[str, Any] = {}
    if family:
        where.append("l.language_family = $family")
        params["family"] = family
    if iso_code:
        where.append("l.language_code = $iso_code")
        params["iso_code"] = iso_code

    query = f"""
    MATCH (l:LSR)
    WHERE {" AND ".join(where)}
    RETURN l.language_code AS iso_code,
           l.language_name AS name,
           l.language_family AS family,
           max(l.reconstruction_flag) AS reconstructed
    ORDER BY iso_code
    """
    try:
        async with db.neo4j_session() as session:
            result = await session.run(query, params)
            records = await result.fetch(1000)
    except Exception as e:
        logger.warning(f"GraphQL languages query failed: {e}")
        return []

    languages = []
    for record in records:
        family_name = record["family"]
        languages.append(
            {
                "iso_code": record["iso_code"],
                "name": record["name"] or record["iso_code"],
                "family": family_name,
                "branch_path": [family_name] if family_name else [],
                # Reconstructed proto-languages are not living languages
                "is_living": not bool(record["reconstructed"]),
            }
        )
    return languages


async def resolve_lsr_ancestors(
    db: DatabaseManager, lsr_id: str, depth: int = 10
) -> list[dict[str, Any]]:
    """Resolve ancestor LSRs by following DESCENDS_FROM edges outward."""
    depth = min(max(depth, 1), 20)
    query = f"""
    MATCH (start:LSR {{id: $lsr_id}})-[:DESCENDS_FROM*1..{depth}]->(ancestor:LSR)
    RETURN DISTINCT ancestor
    LIMIT 100
    """
    return await _run_node_query(db, query, {"lsr_id": lsr_id}, "ancestor")


async def resolve_lsr_descendants(
    db: DatabaseManager, lsr_id: str, depth: int = 3
) -> list[dict[str, Any]]:
    """Resolve descendant LSRs by following DESCENDS_FROM edges inward."""
    depth = min(max(depth, 1), 10)
    query = f"""
    MATCH (start:LSR {{id: $lsr_id}})<-[:DESCENDS_FROM*1..{depth}]-(descendant:LSR)
    RETURN DISTINCT descendant
    LIMIT 500
    """
    return await _run_node_query(db, query, {"lsr_id": lsr_id}, "descendant")


async def resolve_lsr_cognates(db: DatabaseManager, lsr_id: str) -> list[dict[str, Any]]:
    """Resolve cognates: other descendants of this LSR's proto-ancestor."""
    query = """
    MATCH (start:LSR {id: $lsr_id})-[:DESCENDS_FROM*0..]->(proto:LSR)
    WHERE NOT (proto)-[:DESCENDS_FROM]->()
    WITH proto
    MATCH (proto)<-[:DESCENDS_FROM*1..]-(cognate:LSR)
    WHERE cognate.id <> $lsr_id
    RETURN DISTINCT cognate
    LIMIT 100
    """
    return await _run_node_query(db, query, {"lsr_id": lsr_id}, "cognate")


async def resolve_etymology_chain(db: DatabaseManager, lsr_id: str) -> dict[str, Any]:
    """Resolve the full etymology chain back to the proto-form."""
    query = """
    MATCH path = (start:LSR {id: $lsr_id})-[:DESCENDS_FROM*0..]->(ancestor:LSR)
    WHERE NOT (ancestor)-[:DESCENDS_FROM]->()
    RETURN path
    ORDER BY length(path) DESC
    LIMIT 1
    """
    try:
        async with db.neo4j_session() as session:
            result = await session.run(query, {"lsr_id": lsr_id})
            record = await result.single()
    except Exception as e:
        logger.warning(f"GraphQL etymology chain failed for {lsr_id}: {e}")
        return {"steps": [], "proto_form": None, "depth": 0}

    if not record:
        return {"steps": [], "proto_form": None, "depth": 0}

    steps = [_node_to_dict(node) for node in record["path"].nodes]
    return {
        "steps": steps,
        "proto_form": steps[-1] if steps else None,
        "depth": max(0, len(steps) - 1),
    }


async def resolve_semantic_trajectory(
    db: DatabaseManager, form: str, language: str
) -> dict[str, Any]:
    """Resolve the semantic trajectory for a word via the drift analyzer."""
    from src.analysis.semantic_drift import SemanticDriftAnalyzer
    from src.api.routes.analysis import _build_trajectory_data

    points = await _build_trajectory_data(db, form, language)
    analyzer = SemanticDriftAnalyzer(lsr_data={f"{form.lower()}:{language}": points})
    trajectory = analyzer.get_trajectory(form, language)

    if trajectory is None:
        return {"points": [], "shift_events": []}

    return {
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
    from src.analysis.dating import TextDating
    from src.api.routes.analysis import _build_lsr_lookup

    lookup = await _build_lsr_lookup(db, language)
    dater = TextDating(lsr_lookup=lookup)
    analysis = dater.date_text(text, language)
    return {
        "predicted_range": list(analysis.predicted_range),
        "confidence": analysis.confidence,
        "diagnostic_vocabulary": [
            {
                "form": w["word"],
                "date_contribution": w["diagnostic_value"],
                "earliest_attestation": w["date_start"],
            }
            for w in analysis.diagnostic_vocabulary
        ],
    }


async def resolve_detect_anachronisms(
    db: DatabaseManager, text: str, claimed_date: int, language: str
) -> dict[str, Any]:
    """Run anachronism detection for the GraphQL detectAnachronisms field."""
    from src.analysis.dating import TextDating
    from src.api.routes.analysis import _build_lsr_lookup

    lookup = await _build_lsr_lookup(db, language)
    dater = TextDating(lsr_lookup=lookup)
    analysis = dater.detect_anachronisms(text, claimed_date, language)
    return {
        "anachronisms": [
            {
                "form": a["word"],
                "earliest_attestation": a["earliest_attestation"],
                "severity": a["severity"],
            }
            for a in analysis.anachronisms
        ],
        "verdict": analysis.verdict,
    }


async def _run_node_query(
    db: DatabaseManager, query: str, params: dict[str, Any], alias: str
) -> list[dict[str, Any]]:
    """Run a Cypher query returning LSR nodes under `alias`."""
    try:
        async with db.neo4j_session() as session:
            result = await session.run(query, params)
            records = await result.fetch(500)
            return [_node_to_dict(record[alias]) for record in records]
    except Exception as e:
        logger.warning(f"GraphQL node query failed: {e}")
        return []
