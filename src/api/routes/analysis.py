"""Analysis API routes for text dating, anachronism detection, and semantic analysis.

Every response carries enough context to judge it: a ``status`` or verdict
of ``insufficient_data`` when the graph has too little dated vocabulary,
plus coverage counts. Database outages surface as HTTP 503.
"""

import logging

from fastapi import APIRouter, Depends, Query

from src.analysis.contact_detection import ContactDetector
from src.analysis.data_access import (
    load_borrowings,
    load_trajectory,
    load_vocabulary_for_text,
)
from src.analysis.dating import TextDating
from src.analysis.semantic_drift import SemanticDriftAnalyzer, assess_trajectory, drift_report
from src.exceptions import InvalidDateRangeError, InvalidLanguageCodeError, ValidationError
from src.models import ErrorResponse
from src.utils.db import DatabaseManager, get_db
from src.utils.validation import (
    YEAR_MAX,
    YEAR_MIN,
    AnachronismRequest,
    DateTextRequest,
    normalize_language_code,
    sanitize_string,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def _language_param(value: str) -> str:
    """Validate a language query parameter (ISO 639-3, or common 639-1)."""
    try:
        return normalize_language_code(value)
    except ValueError as e:
        raise InvalidLanguageCodeError(language_code=value) from e


@router.post(
    "/date-text",
    responses={400: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
async def date_text(
    request: DateTextRequest,
    db: DatabaseManager = Depends(get_db),
) -> dict:
    """
    Estimate when a text was written from its vocabulary.

    ``predicted_date_range`` is [earliest, latest]: the earliest year is the
    first attestation of the text's most recent word; the latest is the
    present unless some words fell out of use. It is null when none of the
    text's words have attestation dates in the graph (``status`` is then
    ``insufficient_data``).
    """
    logger.info(f"Dating text in {request.language}, length={len(request.text)}")

    lookup = await load_vocabulary_for_text(db, request.language, request.text)
    result = TextDating(lsr_lookup=lookup).date_text(request.text, request.language)

    return {
        "predicted_date_range": list(result.predicted_range) if result.predicted_range else None,
        "confidence": result.confidence,
        "status": result.status,
        "explanation": result.explanation,
        "diagnostic_vocabulary": result.diagnostic_vocabulary,
        "analysis": {
            "language": request.language,
            "text_length": len(request.text),
            "word_count": len(request.text.split()),
            "tokens_analyzed": result.analyzed_tokens,
            "content_words": result.content_tokens,
            "dated_words": result.matched_tokens,
            "unknown_words": result.unknown_words,
            "method": result.method,
        },
    }


@router.post(
    "/detect-anachronisms",
    responses={400: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
async def detect_anachronisms(
    request: AnachronismRequest,
    db: DatabaseManager = Depends(get_db),
) -> dict:
    """
    Detect vocabulary that is anachronistic for a claimed date.

    ``verdict`` is ``anachronistic`` / ``suspicious`` when words are first
    attested after the claimed date, ``consistent`` when enough of the text
    is dated and nothing postdates it, and ``insufficient_data`` otherwise.
    """
    logger.info(
        f"Checking anachronisms for {request.language}, "
        f"claimed_date={request.claimed_date}, length={len(request.text)}"
    )

    lookup = await load_vocabulary_for_text(db, request.language, request.text)
    result = TextDating(lsr_lookup=lookup).detect_anachronisms(
        request.text, request.claimed_date, request.language
    )

    return {
        "anachronisms": result.anachronisms,
        "verdict": result.verdict,
        "confidence": result.confidence,
        "explanation": result.explanation,
        "analysis": {
            "language": request.language,
            "claimed_date": request.claimed_date,
            "words_analyzed": len(request.text.split()),
            "content_words": result.content_tokens,
            "dated_words": result.dated_tokens,
            "unknown_words": result.unknown_words,
        },
    }


@router.get(
    "/contact-events",
    responses={400: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
async def get_contact_events(
    language: str = Query(..., description="ISO 639-3 language code", max_length=20),
    date_start: int | None = Query(None, description="Start year", ge=YEAR_MIN, le=YEAR_MAX),
    date_end: int | None = Query(None, description="End year", ge=YEAR_MIN, le=YEAR_MAX),
    db: DatabaseManager = Depends(get_db),
) -> list[dict]:
    """
    Get detected language contact events.

    Clusters BORROWED_FROM edges by donor/recipient language and century of
    the borrowing (the borrowed form's first attestation). Returns events
    where the language was either donor or recipient.
    """
    language = _language_param(language)

    if date_start is not None and date_end is not None and date_end < date_start:
        raise InvalidDateRangeError(start_date=date_start, end_date=date_end)

    logger.info(f"Fetching contact events for {language}, dates={date_start}-{date_end}")

    borrowings = await load_borrowings(db, language)
    language_names = {b["source_lang"]: b["source_lang_name"] for b in borrowings}

    detector = ContactDetector(borrowing_data=borrowings)
    events = detector.detect_contacts(language, date_start=date_start, date_end=date_end)

    return [
        {
            "donor_language": e.donor_language,
            "donor_language_name": language_names.get(e.donor_language) or e.donor_language,
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
    responses={400: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
async def get_semantic_drift(
    form: str = Query(..., description="Word form to analyze", max_length=200),
    language: str = Query(..., description="ISO 639-3 language code", max_length=20),
    db: DatabaseManager = Depends(get_db),
) -> dict:
    """
    Get the semantic drift trajectory for a word.

    Needs dated senses of the form with definitions from at least two
    different years (senses of the same date are not a change over time);
    otherwise ``status`` is ``insufficient_data`` and ``explanation`` says
    why. Distances compare definition text with a lightweight hashed n-gram
    encoder (see src/pipelines/embedding.py), so they measure change in how
    the sense is described, not a trained model of meaning.
    """
    form = sanitize_string(form, max_length=200)
    if not form:
        raise ValidationError(message="Form is required", field="form")
    language = _language_param(language)

    logger.info(f"Fetching semantic drift for '{form}' in {language}")

    return drift_report(form, language, await load_trajectory(db, form, language))


@router.get(
    "/compare-concept",
    responses={400: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
async def compare_concept(
    concept: str = Query(
        ..., description="Written form to look up in each language (no translation)", max_length=100
    ),
    languages: str = Query(
        ..., description="Comma-separated ISO 639-3 codes (at most 10)", max_length=250
    ),
    db: DatabaseManager = Depends(get_db),
) -> dict:
    """
    Compare the trajectories of one written form across languages.

    The same spelling is looked up in each language; there is no
    translation step, so this is useful for shared loanwords and
    identically spelled cognates (e.g. "taxi", "chocolate").
    """
    concept = sanitize_string(concept, max_length=100)

    language_list = [_language_param(lang) for lang in languages.split(",") if lang.strip()]

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
        trajectory_data = await load_trajectory(db, concept, lang)
        lsr_data_dict = {f"{concept.lower()}:{lang}": trajectory_data} if trajectory_data else {}
        analyzer = SemanticDriftAnalyzer(lsr_data=lsr_data_dict)
        trajectory = analyzer.get_trajectory(concept, lang)

        # Senses of a single date have nothing to drift from
        status, explanation = assess_trajectory(trajectory, concept, lang)
        lang_result: dict = {
            "language": lang,
            "forms": [d["form"] for d in trajectory_data] if trajectory_data else [],
            "status": status,
            "explanation": explanation,
            "trajectory": None,
        }

        if trajectory and status == "ok":
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
