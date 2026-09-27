"""Load the graph data the analyses need.

Shared by the REST routes, GraphQL resolvers and the CLI so every entry
point analyses the same data the same way. Database failures propagate
as DatabaseError (HTTP 503) instead of being reported as "no data".
"""

import asyncio
import logging
from typing import Any

from neo4j import Query
from neo4j.exceptions import DriverError, Neo4jError

from src.models.lsr import LSR
from src.repositories.lsr_repository import database_error
from src.utils.db import DatabaseManager
from src.utils.text import word_spans

logger = logging.getLogger(__name__)

# Server-side timeout for analysis reads, so a stalled Neo4j fails the request
QUERY_TIMEOUT_SECONDS = 15
# Neo4j checks the timeout only between units of work, and a stalled server
# never answers at all: the client stops waiting this much later
_CLIENT_DEADLINE_GRACE_SECONDS = 2

# Common irregular English past forms -> base form
_ENGLISH_IRREGULAR = {
    "rode": "ride",
    "ridden": "ride",
    "spoke": "speak",
    "spoken": "speak",
    "wrote": "write",
    "written": "write",
    "went": "go",
    "gone": "go",
    "came": "come",
    "saw": "see",
    "seen": "see",
    "took": "take",
    "taken": "take",
    "gave": "give",
    "given": "give",
    "ate": "eat",
    "eaten": "eat",
    "drank": "drink",
    "sang": "sing",
    "sung": "sing",
    "ran": "run",
    "made": "make",
    "said": "say",
    "told": "tell",
    "thought": "think",
    "brought": "bring",
    "bought": "buy",
    "fought": "fight",
    "taught": "teach",
    "caught": "catch",
    "sent": "send",
    "built": "build",
    "felt": "feel",
    "kept": "keep",
    "left": "leave",
    "met": "meet",
    "held": "hold",
    "stood": "stand",
    "found": "find",
    "knew": "know",
    "known": "know",
    "grew": "grow",
    "threw": "throw",
    "flew": "fly",
    "drew": "draw",
    "broke": "break",
    "chose": "choose",
    "froze": "freeze",
    "slew": "slay",
    "slain": "slay",
    "men": "man",
    "women": "woman",
    "children": "child",
    "feet": "foot",
    "teeth": "tooth",
    "mice": "mouse",
    "geese": "goose",
}


def normalize(token: str) -> str:
    """Normalize a token exactly as LSR.form_normalized is normalized."""
    return LSR._normalize(token)


def _words(text: str) -> list[str]:
    """Split text into words: letters with their combining marks, any script."""
    return [text[start:end] for start, end in word_spans(text)]


def tokenize(text: str) -> list[str]:
    """Split text into normalized word tokens (Unicode letters, any script).

    Tokens are normalized like LSR.form_normalized, so they match stored forms.
    """
    return [normalize(t) for t in _words(text)]


def lookup_candidates(token: str, language: str) -> list[str]:
    """Forms to try for a token, most specific first.

    Graph entries are dictionary forms, so for English an inflected token
    ("computers", "rode", "knights'") also tries its likely base forms.
    """
    candidates = [token]
    if language != "eng":
        return candidates

    word = token.replace("’", "'")
    if word.endswith("'s") or word.endswith("s'"):
        word = word[:-2]
        candidates.append(word)
    if word in _ENGLISH_IRREGULAR:
        candidates.append(_ENGLISH_IRREGULAR[word])

    # The first candidate found in the graph dates the token, so each base
    # form comes before the shorter strings that are other words ("fades" is
    # fade before fad, "hoped" hope before hop, "hopped" hop before hopp).
    if len(word) > 4 and word.endswith("ies"):
        candidates.append(word[:-3] + "y")
    if word.endswith("sses"):
        candidates.append(word[:-2])  # "passes" is pass, not passé (normalized "passe")
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        candidates.append(word[:-1])
    if len(word) > 3 and word.endswith("es"):
        candidates.append(word[:-2])
    if len(word) > 4 and word.endswith("ied"):
        candidates.append(word[:-3] + "y")
    if len(word) > 3 and word.endswith("ed"):
        candidates += _verb_bases(word[:-2])
    if len(word) > 5 and word.endswith("ing"):
        candidates += _verb_bases(word[:-3])

    return list(dict.fromkeys(c for c in candidates if c))


def _verb_bases(stem: str) -> list[str]:
    """Base forms for what is left of a verb after removing -ed or -ing, likeliest first.

    A doubled final consonant is usually the spelling rule ("hopped" -> hop),
    except for bases that end in ll/ss/ff/zz ("called", "passed") or would be
    left with two letters ("added" is add, not ad); a base ending in a double
    consonant and e ("gazetted", "finessed") is tried last. Otherwise the base
    usually lost a silent e ("hoped" -> hope, "faded" -> fade), and the bare
    stem ("walked" -> walk) is tried after it.
    """
    if len(stem) > 2 and stem[-1] == stem[-2] and stem[-1] not in "aeiouy":
        undoubled = stem[:-1]
        if stem[-1] in "lsfz" or len(undoubled) < 3:
            return [stem, undoubled, stem + "e"]
        return [undoubled, stem, stem + "e"]
    return [stem + "e", stem]


async def _fetch(
    db: DatabaseManager, query: str, params: dict[str, Any], limit: int, what: str
) -> list[Any]:
    """Run an analysis read and return up to `limit` records.

    The query has a server-side timeout of QUERY_TIMEOUT_SECONDS and the
    client gives up shortly after it, so a stalled Neo4j becomes a
    DatabaseError instead of holding the request for minutes.
    """

    async def run() -> list[Any]:
        async with db.neo4j_session() as session:
            result = await session.run(Query(query, timeout=QUERY_TIMEOUT_SECONDS), params)
            return list(await result.fetch(limit))

    try:
        return await asyncio.wait_for(run(), QUERY_TIMEOUT_SECONDS + _CLIENT_DEADLINE_GRACE_SECONDS)
    except (RuntimeError, DriverError, Neo4jError, TimeoutError) as e:
        raise database_error(e, what) from e


async def load_vocabulary(
    db: DatabaseManager, language: str, forms: list[str]
) -> dict[str, dict[str, Any]]:
    """Load dating data for the given normalized forms in one language.

    All senses of a form are aggregated: ``date_start`` is the earliest
    first attestation, ``date_end`` is the latest last attestation, or
    None when any sense is still in use (or has no recorded end).

    Returns:
        {form: {"date_start", "date_label", "date_end", "language_code",
        "senses", "definition"}} for every form that exists in the graph
        (including undated ones, whose date_start is None). date_label is
        the earliest date as its source states it ("c. 1220", "earliest in
        corpus: Pride and Prejudice"), or "".
    """
    if not forms:
        return {}
    query = """
    MATCH (l:LSR)
    WHERE l.language_code = $lang AND l.form_normalized IN $forms
    WITH l ORDER BY l.date_start, l.id
    RETURN l.form_normalized AS form,
           min(l.date_start) AS date_start,
           head(collect(CASE WHEN l.date_start IS NOT NULL
                             THEN coalesce(l.period_label, '') END)) AS date_label,
           CASE WHEN count(l.date_end) < count(l) THEN null ELSE max(l.date_end) END AS date_end,
           count(l) AS senses,
           head(collect(l.definition_primary)) AS definition
    """
    params = {"lang": language, "forms": sorted(set(forms))}
    records = await _fetch(db, query, params, len(forms) + 1, "Vocabulary lookup")
    return {
        record["form"]: {
            "date_start": record["date_start"],
            "date_label": record.get("date_label") or "",
            "date_end": record["date_end"],
            "language_code": language,
            "senses": record.get("senses", 1),
            "definition": record.get("definition") or "",
        }
        for record in records
    }


async def load_vocabulary_for_text(
    db: DatabaseManager, language: str, text: str
) -> dict[str, dict[str, Any]]:
    """Load dating data for every token of a text (with inflection fallbacks)."""
    forms: set[str] = set()
    for token in tokenize(text):
        forms.update(lookup_candidates(token, language))
    return await load_vocabulary(db, language, sorted(forms))


async def load_borrowings(db: DatabaseManager, language: str) -> list[dict[str, Any]]:
    """Load BORROWED_FROM edges where the language is recipient or donor.

    The borrowing date is the recipient form's first attestation.
    """
    query = """
    MATCH (recipient:LSR)-[b:BORROWED_FROM]->(donor:LSR)
    WHERE recipient.language_code = $lang OR donor.language_code = $lang
    RETURN recipient.form_orthographic AS target_form,
           donor.form_orthographic AS source_form,
           donor.language_code AS source_lang,
           donor.language_name AS source_lang_name,
           recipient.language_code AS target_lang,
           recipient.date_start AS date,
           coalesce(recipient.definition_primary, '') AS definition,
           coalesce(recipient.semantic_fields, []) AS semantic_fields,
           b.confidence AS confidence
    """
    records = await _fetch(db, query, {"lang": language}, 100000, "Borrowing lookup")
    return [
        {
            "form": record["target_form"],
            "target_form": record["target_form"],
            "source_form": record["source_form"],
            "source_lang": record["source_lang"],
            "source_lang_name": record["source_lang_name"],
            "target_lang": record["target_lang"],
            "date": record["date"],
            "definition": record["definition"],
            "semantic_fields": list(record["semantic_fields"]),
            "confidence": record["confidence"],
        }
        for record in records
        if record["source_lang"] and record["target_lang"]
    ]


async def load_trajectory(db: DatabaseManager, form: str, language: str) -> list[dict[str, Any]]:
    """Load the senses of a form that drift analysis can compare.

    Only senses with a first-attestation date and a definition vector are
    returned; an undated or undefined record says nothing about change in
    meaning.
    """
    query = """
    MATCH (l:LSR {form_normalized: $form, language_code: $lang})
    RETURN l.form_normalized AS form,
           l.date_start AS date_start,
           l.date_end AS date_end,
           l.definition_primary AS definition,
           l.semantic_vector AS semantic_vector,
           coalesce(l.confidence_overall, 1.0) AS confidence,
           l.id AS id
    ORDER BY l.date_start
    """
    params = {"form": normalize(form), "lang": language}
    records = await _fetch(db, query, params, 1000, "Trajectory lookup")
    return [
        {
            "form": record["form"],
            "date_start": record["date_start"],
            "date_end": record["date_end"],
            "definition_primary": record["definition"],
            "semantic_vector": list(record["semantic_vector"] or []),
            "confidence_overall": record["confidence"],
            "language_code": language,
            "id": record["id"],
        }
        for record in records
        if record["date_start"] is not None and record["semantic_vector"]
    ]
