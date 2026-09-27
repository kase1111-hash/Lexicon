"""Text dating and anachronism detection from vocabulary attestation dates.

The evidence is each word's attestation window in the graph: ``date_start``
is its first attestation, ``date_end`` its last (None = still in use).

- A text can be no older than its most recently coined word (terminus
  post quem); obsolete words give a soft upper bound.
- A word first attested after a text's claimed date is an anachronism.

Every result reports coverage (how many content words had dates). When
there is too little dated vocabulary the verdict is ``insufficient_data``;
absence of evidence is never reported as a confident "consistent".
"""

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from src.analysis.data_access import lookup_candidates, tokenize

logger = logging.getLogger(__name__)

# Below this share of dated content words, "no anachronisms found" is not
# evidence that a text is consistent with its claimed date.
MIN_COVERAGE_FOR_CONSISTENT = 0.5

# Dated words needed before coverage alone drives confidence
FULL_EVIDENCE_WORDS = 5


@dataclass
class DateAnalysis:
    """Result of text dating analysis."""

    # (earliest, latest) plausible year, or None when there is no evidence
    predicted_range: tuple[int, int] | None
    confidence: float
    diagnostic_vocabulary: list[dict] = field(default_factory=list)
    analyzed_tokens: int = 0
    matched_tokens: int = 0
    method: str = "vocabulary_attestation"
    status: str = "ok"  # "ok", "conflicting_evidence", "insufficient_data"
    content_tokens: int = 0
    unknown_words: list[str] = field(default_factory=list)
    explanation: str = ""


@dataclass
class AnachronismAnalysis:
    """Result of anachronism detection."""

    anachronisms: list[dict] = field(default_factory=list)
    # "consistent", "suspicious", "anachronistic", "insufficient_data"
    verdict: str = "insufficient_data"
    confidence: float = 0.0
    explanation: str = ""
    content_tokens: int = 0
    dated_tokens: int = 0
    unknown_words: list[str] = field(default_factory=list)


@dataclass
class _WordEvidence:
    """Dating evidence for one content token."""

    word: str
    form: str
    date_start: int | None
    date_end: int | None
    # date_start as the source states it ("c. 1220", "earliest in corpus: …")
    date_label: str = ""


# Common words to skip during analysis (high-frequency words with little dating value)
STOP_WORDS = {
    "the",
    "a",
    "an",
    "and",
    "or",
    "but",
    "in",
    "on",
    "at",
    "to",
    "for",
    "of",
    "with",
    "by",
    "from",
    "is",
    "are",
    "was",
    "were",
    "be",
    "been",
    "being",
    "have",
    "has",
    "had",
    "do",
    "does",
    "did",
    "will",
    "would",
    "could",
    "should",
    "may",
    "might",
    "must",
    "shall",
    "can",
    "need",
    "this",
    "that",
    "these",
    "those",
    "it",
    "its",
    "he",
    "she",
    "they",
    "we",
    "you",
    "i",
    "me",
    "him",
    "her",
    "us",
    "them",
    "my",
    "your",
    "his",
    "their",
    "our",
    "who",
    "what",
    "which",
    "when",
    "where",
    "how",
    "all",
    "each",
    "every",
    "both",
    "few",
    "more",
    "most",
    "other",
    "some",
    "such",
    "no",
    "not",
    "only",
    "same",
    "so",
    "than",
    "too",
    "very",
    "just",
    "also",
    "now",
    "here",
    "there",
    "then",
    "if",
    "as",
    "because",
}


class TextDating:
    """
    Analyze and date text based on vocabulary attestation patterns.

    Provides:
    1. A plausible date range for a text (terminus post quem from its
       newest word, soft upper bound from obsolete words)
    2. Detection of anachronistic vocabulary relative to a claimed date
    """

    def __init__(self, lsr_lookup: dict[str, dict[str, Any]] | None = None):
        """
        Initialize the text dating analyzer.

        Args:
            lsr_lookup: Dictionary mapping normalized forms to LSR data
                       with keys: 'date_start', 'date_end', 'language_code'
                       (see src.analysis.data_access.load_vocabulary).
        """
        self._lsr_lookup = lsr_lookup or {}

    def set_lsr_lookup(self, lookup: dict[str, dict[str, Any]]) -> None:
        """
        Set the LSR lookup dictionary.

        Args:
            lookup: Dictionary mapping normalized word forms to their LSR data.
        """
        self._lsr_lookup = lookup

    def date_text(self, text: str, language: str = "eng") -> DateAnalysis:
        """
        Estimate when a text could have been written from its vocabulary.

        The earliest plausible year is the latest first attestation among
        the text's words. The latest plausible year is the earliest last
        attestation among words that fell out of use, or the present.

        Args:
            text: The text to analyze.
            language: ISO 639-3 language code (default: 'eng' for English).

        Returns:
            DateAnalysis with predicted range, confidence and coverage.
        """
        tokens = tokenize(text)
        content = self._content_tokens(tokens)
        evidence, unknown = self._gather_evidence(content, language)
        dated = [e for e in evidence if e.date_start is not None]

        if not dated:
            return DateAnalysis(
                predicted_range=None,
                confidence=0.0,
                status="insufficient_data",
                explanation=self._no_evidence_message(len(content), len(evidence), language),
                analyzed_tokens=len(tokens),
                matched_tokens=0,
                content_tokens=len(content),
                unknown_words=unknown[:50],
            )

        lower = max(e.date_start for e in dated if e.date_start is not None)
        ended = [e.date_end for e in dated if e.date_end is not None]
        current_year = date.today().year
        upper = min(ended) if ended else current_year

        coverage = len(dated) / len(content)
        confidence = coverage * min(1.0, len(dated) / FULL_EVIDENCE_WORDS)
        newest = sorted({e.word for e in dated if e.date_start == lower})

        if lower <= upper:
            status = "ok"
            predicted = (lower, upper)
            explanation = (
                f"Written no earlier than {lower} (first attestation of "
                f"{', '.join(newest)}); {len(dated)} of {len(content)} content words dated."
            )
        else:
            # A word fell out of use before another was coined. Coinage is
            # hard evidence; obsolete words may be deliberate archaisms.
            status = "conflicting_evidence"
            predicted = (lower, current_year)
            confidence *= 0.5
            obsolete = sorted(
                {e.word for e in dated if e.date_end is not None and e.date_end < lower}
            )
            explanation = (
                f"Written no earlier than {lower} ({', '.join(newest)}), but "
                f"{', '.join(obsolete)} fell out of use earlier; possible archaism."
            )

        # A word sets the upper bound only when the reported range ends at its
        # last attestation; with conflicting evidence the range runs to the present.
        diagnostic = [
            {
                "word": e.word,
                "form": e.form,
                "date_start": e.date_start,
                "date_label": e.date_label,
                "date_end": e.date_end,
                "sets_bound": (
                    "lower"
                    if e.date_start == lower
                    else (
                        "upper"
                        if status == "ok" and e.date_end is not None and e.date_end == upper
                        else None
                    )
                ),
            }
            for e in sorted(dated, key=lambda e: -(e.date_start or 0))
        ]

        return DateAnalysis(
            predicted_range=predicted,
            confidence=round(confidence, 3),
            diagnostic_vocabulary=diagnostic[:20],
            status=status,
            explanation=explanation,
            analyzed_tokens=len(tokens),
            matched_tokens=len(dated),
            content_tokens=len(content),
            unknown_words=unknown[:50],
        )

    def detect_anachronisms(
        self, text: str, claimed_date: int, language: str = "eng"
    ) -> AnachronismAnalysis:
        """
        Detect vocabulary that is anachronistic for a claimed date.

        Words first attested after the claimed date are anachronisms
        ("coined_after"). Words last attested before it are reported as
        possible archaisms ("obsolete_before") but do not drive the verdict.

        Args:
            text: The text to analyze.
            claimed_date: The claimed year of the text.
            language: ISO 639-3 language code.

        Returns:
            AnachronismAnalysis with detected anachronisms, verdict and coverage.
        """
        tokens = tokenize(text)
        content = self._content_tokens(tokens)
        evidence, unknown = self._gather_evidence(content, language)
        dated = [e for e in evidence if e.date_start is not None]

        anachronisms: list[dict] = []
        for e in dated:
            if e.date_start is not None and e.date_start > claimed_date:
                gap = e.date_start - claimed_date
                anachronisms.append(
                    {
                        "word": e.word,
                        "form": e.form,
                        "type": "coined_after",
                        "earliest_attestation": e.date_start,
                        "date_label": e.date_label,
                        "claimed_date": claimed_date,
                        "gap_years": gap,
                        "severity": "high" if gap > 100 else "medium" if gap > 50 else "low",
                    }
                )
            elif e.date_end is not None and e.date_end < claimed_date:
                anachronisms.append(
                    {
                        "word": e.word,
                        "form": e.form,
                        "type": "obsolete_before",
                        "last_attestation": e.date_end,
                        "claimed_date": claimed_date,
                        "gap_years": claimed_date - e.date_end,
                        "severity": "low",
                    }
                )
        anachronisms.sort(key=lambda a: a["gap_years"], reverse=True)

        coined = [a for a in anachronisms if a["type"] == "coined_after"]
        significant = [a for a in coined if a["severity"] in ("high", "medium")]
        coverage = len(dated) / len(content) if content else 0.0
        max_gap = max((a["gap_years"] for a in coined), default=0)

        if significant:
            verdict = "anachronistic" if len(significant) >= 3 or max_gap > 200 else "suspicious"
            confidence = min(0.95, 0.5 + 0.15 * len(significant) + (0.1 if max_gap > 200 else 0))
            words = ", ".join(f"{a['word']} ({a['earliest_attestation']})" for a in significant[:5])
            explanation = (
                f"{len(significant)} word(s) first attested well after {claimed_date}: {words}."
            )
        elif not dated or coverage < MIN_COVERAGE_FOR_CONSISTENT:
            verdict = "insufficient_data"
            confidence = 0.0
            explanation = (
                self._no_evidence_message(len(content), len(evidence), language)
                if not dated
                else f"Only {len(dated)} of {len(content)} content words have attestation dates; "
                "too few to judge the text consistent with its claimed date."
            )
        else:
            verdict = "consistent"
            confidence = coverage * min(1.0, len(dated) / FULL_EVIDENCE_WORDS)
            if coined:
                late = ", ".join(f"{a['word']} ({a['earliest_attestation']})" for a in coined[:5])
                verb = "postdates" if len(coined) == 1 else "postdate"
                explanation = (
                    f"No word among the {len(dated)} dated content words (of {len(content)}) "
                    f"is first attested more than 50 years after {claimed_date}; "
                    f"{len(coined)} {verb} it by 50 years or less: {late}."
                )
            else:
                explanation = (
                    f"No word among the {len(dated)} dated content words "
                    f"(of {len(content)}) is first attested after {claimed_date}."
                )

        return AnachronismAnalysis(
            anachronisms=anachronisms[:20],
            verdict=verdict,
            confidence=round(confidence, 3),
            explanation=explanation,
            content_tokens=len(content),
            dated_tokens=len(dated),
            unknown_words=unknown[:50],
        )

    @staticmethod
    def _content_tokens(tokens: list[str]) -> list[str]:
        """Distinct content words, in order: no stop words or very short tokens.

        Coverage is counted over distinct words, so a repeated word counts once.
        """
        return list(dict.fromkeys(t for t in tokens if t not in STOP_WORDS and len(t) > 2))

    def _gather_evidence(
        self, content: list[str], language: str
    ) -> tuple[list[_WordEvidence], list[str]]:
        """Look up each distinct content token (with inflection fallbacks).

        Returns:
            (evidence for tokens found in the graph, tokens not found)
        """
        evidence: list[_WordEvidence] = []
        unknown: list[str] = []
        for token in dict.fromkeys(content):
            for candidate in lookup_candidates(token, language):
                data = self._lsr_lookup.get(candidate)
                if data and data.get("language_code") == language:
                    evidence.append(
                        _WordEvidence(
                            word=token,
                            form=candidate,
                            date_start=data.get("date_start"),
                            date_end=data.get("date_end"),
                            date_label=data.get("date_label") or "",
                        )
                    )
                    break
            else:
                unknown.append(token)
        return evidence, unknown

    @staticmethod
    def _no_evidence_message(content: int, known: int, language: str) -> str:
        if content == 0:
            return "The text has no content words to analyze."
        if known == 0:
            return (
                f"None of the {content} content words are in the lexical graph for "
                f"'{language}'. Ingest data for this language first."
            )
        return (
            f"{known} of {content} content words are in the graph for '{language}', "
            "but none has an attestation date."
        )


# Convenience function for API use
def analyze_text_date(
    text: str,
    language: str = "eng",
    lsr_lookup: dict[str, dict[str, Any]] | None = None,
) -> DateAnalysis:
    """
    Analyze a text and predict its date.

    Args:
        text: The text to analyze.
        language: ISO 639-3 language code.
        lsr_lookup: Optional LSR lookup dictionary.

    Returns:
        DateAnalysis with predicted range and confidence.
    """
    analyzer = TextDating(lsr_lookup=lsr_lookup)
    return analyzer.date_text(text, language)


def check_anachronisms(
    text: str,
    claimed_date: int,
    language: str = "eng",
    lsr_lookup: dict[str, dict[str, Any]] | None = None,
) -> AnachronismAnalysis:
    """
    Check a text for anachronistic vocabulary.

    Args:
        text: The text to analyze.
        claimed_date: The claimed year of the text.
        language: ISO 639-3 language code.
        lsr_lookup: Optional LSR lookup dictionary.

    Returns:
        AnachronismAnalysis with detected anachronisms.
    """
    analyzer = TextDating(lsr_lookup=lsr_lookup)
    return analyzer.detect_anachronisms(text, claimed_date, language)
