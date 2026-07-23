"""Analysis API routes for text dating, anachronism detection, and semantic analysis."""

import logging

from fastapi import APIRouter, Depends, Query

from src.analysis.contact_detection import ContactDetector
from src.analysis.dating import TextDating
from src.analysis.semantic_drift import SemanticDriftAnalyzer
from src.exceptions import InvalidDateRangeError, InvalidLanguageCodeError, ValidationError
from src.models import ErrorResponse
from src.utils.db import DatabaseManager, get_db
from src.utils.validation import (
    AnachronismRequest,
    DateTextRequest,
    sanitize_iso_code,
    sanitize_string,
)

logger = logging.getLogger(__name__)

router = APIRouter()


async def _build_lsr_lookup(db: DatabaseManager, language: str) -> dict[str, dict]:
    """Build a word-form -> LSR data lookup from Neo4j for the analysis modules.

    Returns a dict mapping normalized forms to their attestation data:
        {"water": {"date_start": 1200, "date_end": 2024, "language_code": "eng"}, ...}
    """
    lookup: dict[str, dict] = {}
    try:
        async with db.neo4j_session() as session:
            result = await session.run(
                """
                MATCH (l:LSR {language_code: $lang})
                RETURN l.form_normalized AS form,
                       l.date_start AS date_start,
                       l.date_end AS date_end,
                       l.language_code AS language_code,
                       l.definition_primary AS definition
                """,
                {"lang": language},
            )
            records = await result.fetch(50000)
            for record in records:
                form = record["form"]
                if form:
                    lookup[form] = {
                        "date_start": record["date_start"],
                        "date_end": record["date_end"],
                        "language_code": record["language_code"],
                        "definition": record["definition"],
                    }
    except Exception as e:
        logger.warning(f"Could not load LSR lookup from Neo4j: {e}")
    return lookup


async def _build_borrowing_data(db: DatabaseManager, language: str) -> list[dict]:
    """Load borrowing relationship data from Neo4j for contact detection."""
    borrowings: list[dict] = []
    try:
        async with db.neo4j_session() as session:
            result = await session.run(
                """
                MATCH (recipient:LSR {language_code: $lang})-[:BORROWED_FROM]->(donor:LSR)
                RETURN recipient.form_normalized AS form,
                       donor.language_code AS donor_language,
                       recipient.language_code AS recipient_language,
                       recipient.date_start AS date_start,
                       recipient.date_end AS date_end,
                       recipient.definition_primary AS definition
                """,
                {"lang": language},
            )
            records = await result.fetch(50000)
            for record in records:
                date_start = record["date_start"]
                date_end = record["date_end"]
                borrowings.append(
                    {
                        "form": record["form"],
                        "source_lang": record["donor_language"],
                        "target_lang": record["recipient_language"],
                        "date": (
                            (date_start + date_end) // 2 if date_start and date_end else date_start
                        ),
                        "date_start": date_start,
                        "date_end": date_end,
                        "definition": record["definition"],
                    }
                )
    except Exception as e:
        logger.warning(f"Could not load borrowing data from Neo4j: {e}")
    return borrowings


async def _build_trajectory_data(db: DatabaseManager, form: str, language: str) -> list[dict]:
    """Load LSR data for a word across time periods for semantic drift analysis."""
    points: list[dict] = []
    try:
        async with db.neo4j_session() as session:
            result = await session.run(
                """
                MATCH (l:LSR {form_normalized: $form, language_code: $lang})
                RETURN l.form_normalized AS form,
                       l.date_start AS date_start,
                       l.date_end AS date_end,
                       l.definition_primary AS definition,
                       l.semantic_vector AS semantic_vector,
                       l.id AS id
                ORDER BY l.date_start
                """,
                {"form": form.lower(), "lang": language},
            )
            records = await result.fetch(1000)
            for record in records:
                points.append(
                    {
                        "form": record["form"],
                        "date_start": record["date_start"],
                        "date_end": record["date_end"],
                        "definition_primary": record["definition"],
                        "semantic_vector": list(record["semantic_vector"] or []),
                        "language_code": language,
                        "id": record["id"],
                    }
                )
    except Exception as e:
        logger.warning(f"Could not load trajectory data from Neo4j: {e}")
    return points


@router.post(
    "/date-text",
    responses={400: {"model": ErrorResponse}},
)
async def date_text(
    request: DateTextRequest,
    db: DatabaseManager = Depends(get_db),
) -> dict:
    """
    Predict the date range of a text based on vocabulary.

    Analyzes the vocabulary in the text and compares it against
    historical attestation data to estimate when the text was composed.
    """
    logger.info(f"Dating text in {request.language}, length={len(request.text)}")

    lookup = await _build_lsr_lookup(db, request.language)

    dater = TextDating(lsr_lookup=lookup)
    result = dater.date_text(request.text, request.language)

    return {
        "predicted_date_range": list(result.predicted_range),
        "confidence": result.confidence,
        "diagnostic_vocabulary": result.diagnostic_vocabulary,
        "analysis": {
            "language": request.language,
            "text_length": len(request.text),
            "word_count": len(request.text.split()),
            "tokens_analyzed": result.analyzed_tokens,
            "tokens_matched": result.matched_tokens,
            "method": result.method,
        },
    }


@router.post(
    "/detect-anachronisms",
    responses={400: {"model": ErrorResponse}},
)
async def detect_anachronisms(
    request: AnachronismRequest,
    db: DatabaseManager = Depends(get_db),
) -> dict:
    """
    Detect anachronistic vocabulary in a text.

    Compares the vocabulary in the text against the claimed date
    to identify words that were not yet in use at that time.
    """
    logger.info(
        f"Checking anachronisms for {request.language}, "
        f"claimed_date={request.claimed_date}, length={len(request.text)}"
    )

    lookup = await _build_lsr_lookup(db, request.language)

    dater = TextDating(lsr_lookup=lookup)
    result = dater.detect_anachronisms(request.text, request.claimed_date, request.language)

    return {
        "anachronisms": result.anachronisms,
        "verdict": result.verdict,
        "confidence": result.confidence,
        "explanation": result.explanation,
        "analysis": {
            "language": request.language,
            "claimed_date": request.claimed_date,
            "words_analyzed": len(request.text.split()),
        },
    }


@router.get(
    "/contact-events",
    responses={400: {"model": ErrorResponse}},
)
async def get_contact_events(
    language: str = Query(..., description="ISO 639-3 language code", max_length=10),
    date_start: int | None = Query(None, description="Start year", ge=-10000, le=3000),
    date_end: int | None = Query(None, description="End year", ge=-10000, le=3000),
    db: DatabaseManager = Depends(get_db),
) -> list[dict]:
    """
    Get detected language contact events.

    Returns contact events where the specified language was either
    a donor or recipient of vocabulary.
    """
    language = sanitize_iso_code(language)
    if not language:
        raise InvalidLanguageCodeError(language_code=language)

    if date_start is not None and date_end is not None and date_end < date_start:
        raise InvalidDateRangeError(start_date=date_start, end_date=date_end)

    logger.info(f"Fetching contact events for {language}, dates={date_start}-{date_end}")

    borrowings = await _build_borrowing_data(db, language)

    detector = ContactDetector(borrowing_data=borrowings)
    events = detector.detect_contacts(language, date_start=date_start, date_end=date_end)

    return [
        {
            "donor_language": e.donor_language,
            "recipient_language": e.recipient_language,
            "date_range": list(e.date_range),
            "vocabulary_count": e.vocabulary_count,
            "confidence": e.confidence,
            "sample_words": e.sample_words,
            "contact_type": e.contact_type,
            "semantic_domains": e.semantic_domains,
            "intensity": e.intensity,
        }
        for e in events
    ]


@router.get(
    "/semantic-drift",
    responses={400: {"model": ErrorResponse}},
)
async def get_semantic_drift(
    form: str = Query(..., description="Word form to analyze", max_length=200),
    language: str = Query(..., description="ISO 639-3 language code", max_length=10),
    db: DatabaseManager = Depends(get_db),
) -> dict:
    """
    Get semantic drift trajectory for a word.

    Tracks how a word's meaning has changed over time by analyzing
    its definitions and attestations across different time periods.
    """
    form = sanitize_string(form, max_length=200)
    language = sanitize_iso_code(language)

    if not form:
        raise ValidationError(message="Form is required", field="form")
    if not language:
        raise InvalidLanguageCodeError(language_code=language)

    logger.info(f"Fetching semantic drift for '{form}' in {language}")

    trajectory_data = await _build_trajectory_data(db, form, language)

    lsr_data_dict = {f"{form.lower()}:{language}": trajectory_data} if trajectory_data else {}
    analyzer = SemanticDriftAnalyzer(lsr_data=lsr_data_dict)
    result = analyzer.get_trajectory(form, language)

    if result is None:
        return {
            "form": form,
            "language": language,
            "trajectory": [],
            "shift_events": [],
            "total_drift": 0.0,
            "stability_score": 1.0,
        }

    return {
        "form": form,
        "language": language,
        "trajectory": [
            {
                "date": p.date,
                "embedding_2d": list(p.embedding_2d),
                "definition": p.definition,
                "attestation_count": p.attestation_count,
                "confidence": p.confidence,
            }
            for p in result.points
        ],
        "shift_events": [
            {
                "date": e.date,
                "change_type": e.change_type,
                "confidence": e.confidence,
                "magnitude": e.magnitude,
                "before_meaning": e.before_meaning,
                "after_meaning": e.after_meaning,
                "evidence": e.evidence,
            }
            for e in result.shift_events
        ],
        "total_drift": result.total_drift,
        "stability_score": result.stability_score,
    }


@router.get(
    "/compare-concept",
    responses={400: {"model": ErrorResponse}},
)
async def compare_concept(
    concept: str = Query(..., description="Concept to compare (e.g., 'freedom')", max_length=100),
    languages: str = Query(..., description="Comma-separated ISO 639-3 codes", max_length=100),
    db: DatabaseManager = Depends(get_db),
) -> dict:
    """
    Compare how a concept is expressed across languages.

    Analyzes the semantic trajectories of translations/equivalents
    of a concept across multiple languages.
    """
    concept = sanitize_string(concept, max_length=100)

    language_list = [sanitize_iso_code(lang.strip()) for lang in languages.split(",")]
    language_list = [lang for lang in language_list if lang]

    if not concept:
        raise ValidationError(message="Concept is required", field="concept")
    if not language_list:
        raise ValidationError(
            message="At least one valid language code is required", field="languages"
        )
    if len(language_list) > 10:
        raise ValidationError(message="Maximum 10 languages allowed", field="languages")

    logger.info(f"Comparing concept '{concept}' across {language_list}")

    results_by_lang = []
    for lang in language_list:
        trajectory_data = await _build_trajectory_data(db, concept, lang)
        lsr_data_dict = {f"{concept.lower()}:{lang}": trajectory_data} if trajectory_data else {}
        analyzer = SemanticDriftAnalyzer(lsr_data=lsr_data_dict)
        trajectory = analyzer.get_trajectory(concept, lang)

        lang_result: dict = {
            "language": lang,
            "forms": [d["form"] for d in trajectory_data] if trajectory_data else [],
            "trajectory": None,
        }

        if trajectory:
            lang_result["trajectory"] = {
                "points": [{"date": p.date, "definition": p.definition} for p in trajectory.points],
                "total_drift": trajectory.total_drift,
                "stability_score": trajectory.stability_score,
            }

        results_by_lang.append(lang_result)

    return {
        "concept": concept,
        "by_language": results_by_lang,
    }
