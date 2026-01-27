"""Text dating analysis using vocabulary attestation patterns."""

import logging
import re
from dataclasses import dataclass, field
from statistics import mean, median, stdev
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class DateAnalysis:
    """Result of text dating analysis."""

    predicted_range: tuple[int, int]
    confidence: float
    diagnostic_vocabulary: list[dict] = field(default_factory=list)
    analyzed_tokens: int = 0
    matched_tokens: int = 0
    method: str = "vocabulary_attestation"


@dataclass
class AnachronismAnalysis:
    """Result of anachronism detection."""

    anachronisms: list[dict] = field(default_factory=list)
    verdict: str = "consistent"  # "consistent", "suspicious", "anachronistic"
    confidence: float = 1.0
    explanation: str = ""


# Common words to skip during analysis (high-frequency words with little dating value)
STOP_WORDS = {
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "is", "are", "was", "were", "be", "been",
    "being", "have", "has", "had", "do", "does", "did", "will", "would",
    "could", "should", "may", "might", "must", "shall", "can", "need",
    "this", "that", "these", "those", "it", "its", "he", "she", "they",
    "we", "you", "i", "me", "him", "her", "us", "them", "my", "your",
    "his", "their", "our", "who", "what", "which", "when", "where", "how",
    "all", "each", "every", "both", "few", "more", "most", "other", "some",
    "such", "no", "not", "only", "same", "so", "than", "too", "very",
    "just", "also", "now", "here", "there", "then", "if", "as", "because",
}


class TextDating:
    """
    Analyze and date text based on vocabulary attestation patterns.

    This class provides methods to:
    1. Predict the approximate date range of a text based on vocabulary
    2. Detect anachronistic vocabulary that doesn't match claimed dates
    3. Identify diagnostic vocabulary that strongly indicates time periods
    """

    def __init__(self, lsr_lookup: dict[str, dict[str, Any]] | None = None):
        """
        Initialize the text dating analyzer.

        Args:
            lsr_lookup: Dictionary mapping normalized forms to LSR data
                       with keys: 'date_start', 'date_end', 'language_code'
        """
        self._lsr_lookup = lsr_lookup or {}
        self._classifier = None

    def set_lsr_lookup(self, lookup: dict[str, dict[str, Any]]) -> None:
        """
        Set the LSR lookup dictionary.

        Args:
            lookup: Dictionary mapping normalized word forms to their LSR data.
        """
        self._lsr_lookup = lookup

    def load_classifier(self, model_path: str | None = None) -> None:
        """
        Load a trained ML classifier for text dating.

        Args:
            model_path: Path to the trained model file.
        """
        # For now, we use the vocabulary-based approach
        # A trained classifier would improve accuracy
        self._classifier = None
        logger.info("Using vocabulary attestation method for dating")

    def date_text(self, text: str, language: str = "eng") -> DateAnalysis:
        """
        Predict the date range of a text based on vocabulary.

        The algorithm:
        1. Tokenizes the text and normalizes words
        2. Looks up attestation dates for each word
        3. Calculates a date range based on the overlap of word attestations
        4. Identifies diagnostic vocabulary (words with narrow date ranges)

        Args:
            text: The text to analyze.
            language: ISO 639-3 language code (default: 'eng' for English).

        Returns:
            DateAnalysis with predicted range and confidence.
        """
        # Tokenize and normalize
        tokens = self._tokenize(text)
        normalized_tokens = [self._normalize(t) for t in tokens]

        # Filter out stop words and short tokens
        content_tokens = [
            t for t in normalized_tokens
            if t not in STOP_WORDS and len(t) > 2
        ]

        if not content_tokens:
            return DateAnalysis(
                predicted_range=(0, 0),
                confidence=0.0,
                diagnostic_vocabulary=[],
                analyzed_tokens=len(tokens),
                matched_tokens=0,
            )

        # Look up dates for each token
        date_ranges: list[tuple[int, int]] = []
        diagnostic_words: list[dict] = []
        matched_count = 0

        for token in content_tokens:
            lsr_data = self._lsr_lookup.get(token)
            if lsr_data and lsr_data.get("language_code") == language:
                date_start = lsr_data.get("date_start")
                date_end = lsr_data.get("date_end")

                if date_start is not None and date_end is not None:
                    matched_count += 1
                    date_ranges.append((date_start, date_end))

                    # Check if this is a diagnostic word (narrow date range)
                    span = date_end - date_start
                    if span < 200:  # Less than 200 years span
                        diagnostic_words.append({
                            "word": token,
                            "date_start": date_start,
                            "date_end": date_end,
                            "span": span,
                            "diagnostic_value": max(0.0, 1.0 - span / 200),
                        })

        if not date_ranges:
            return DateAnalysis(
                predicted_range=(0, 0),
                confidence=0.0,
                diagnostic_vocabulary=[],
                analyzed_tokens=len(tokens),
                matched_tokens=0,
            )

        # Calculate the predicted date range
        predicted_range = self._calculate_date_range(date_ranges)

        # Calculate confidence based on coverage and agreement
        coverage = matched_count / len(content_tokens)
        agreement = self._calculate_agreement(date_ranges, predicted_range)
        confidence = min(1.0, coverage * 0.5 + agreement * 0.5)

        # Sort diagnostic words by diagnostic value
        diagnostic_words.sort(key=lambda x: x["diagnostic_value"], reverse=True)

        return DateAnalysis(
            predicted_range=predicted_range,
            confidence=round(confidence, 3),
            diagnostic_vocabulary=diagnostic_words[:20],  # Top 20
            analyzed_tokens=len(tokens),
            matched_tokens=matched_count,
        )

    def detect_anachronisms(
        self, text: str, claimed_date: int, language: str = "eng"
    ) -> AnachronismAnalysis:
        """
        Detect anachronistic vocabulary in a text.

        Checks if the vocabulary used in the text is consistent with
        the claimed date. Words that were coined after the claimed date
        are flagged as potential anachronisms.

        Args:
            text: The text to analyze.
            claimed_date: The claimed year of the text.
            language: ISO 639-3 language code.

        Returns:
            AnachronismAnalysis with detected anachronisms and verdict.
        """
        # Tokenize and normalize
        tokens = self._tokenize(text)
        normalized_tokens = [self._normalize(t) for t in tokens]

        # Filter out stop words
        content_tokens = [
            t for t in normalized_tokens
            if t not in STOP_WORDS and len(t) > 2
        ]

        anachronisms: list[dict] = []
        suspicious_count = 0
        total_checked = 0

        for token in content_tokens:
            lsr_data = self._lsr_lookup.get(token)
            if lsr_data and lsr_data.get("language_code") == language:
                total_checked += 1
                date_start = lsr_data.get("date_start")

                if date_start is not None and date_start > claimed_date:
                    # This word didn't exist at the claimed date
                    gap = date_start - claimed_date
                    severity = "high" if gap > 100 else "medium" if gap > 50 else "low"

                    anachronisms.append({
                        "word": token,
                        "earliest_attestation": date_start,
                        "claimed_date": claimed_date,
                        "gap_years": gap,
                        "severity": severity,
                    })

                    if severity in ("high", "medium"):
                        suspicious_count += 1

        # Determine verdict
        if not anachronisms:
            verdict = "consistent"
            confidence = 1.0
            explanation = "No anachronistic vocabulary detected."
        elif suspicious_count == 0:
            verdict = "consistent"
            confidence = 0.9
            explanation = f"Minor anachronisms detected ({len(anachronisms)} words), but within acceptable range."
        elif suspicious_count <= 2:
            verdict = "suspicious"
            confidence = 0.6
            explanation = f"Some suspicious vocabulary detected ({suspicious_count} significant anachronisms)."
        else:
            verdict = "anachronistic"
            confidence = 0.3
            explanation = f"Multiple anachronisms detected ({suspicious_count} significant). Text likely not from claimed date."

        # Sort by gap (most anachronistic first)
        anachronisms.sort(key=lambda x: x["gap_years"], reverse=True)

        return AnachronismAnalysis(
            anachronisms=anachronisms[:20],  # Top 20
            verdict=verdict,
            confidence=round(confidence, 3),
            explanation=explanation,
        )

    def _tokenize(self, text: str) -> list[str]:
        """
        Tokenize text into words.

        Args:
            text: The text to tokenize.

        Returns:
            List of word tokens.
        """
        # Simple tokenization: split on non-word characters
        tokens = re.findall(r"\b[a-zA-Z]+\b", text)
        return tokens

    def _normalize(self, token: str) -> str:
        """
        Normalize a token for lookup.

        Args:
            token: The token to normalize.

        Returns:
            Normalized token (lowercase).
        """
        return token.lower()

    def _calculate_date_range(
        self, date_ranges: list[tuple[int, int]]
    ) -> tuple[int, int]:
        """
        Calculate the most likely date range from multiple word attestations.

        Uses a weighted approach favoring the intersection of date ranges.

        Args:
            date_ranges: List of (start, end) tuples for each word.

        Returns:
            Predicted (start, end) date range.
        """
        if not date_ranges:
            return (0, 0)

        # Get all start and end dates
        starts = [r[0] for r in date_ranges]
        ends = [r[1] for r in date_ranges]

        # The text must be from when all words existed
        # So: after the latest word was coined, before any word fell out of use
        predicted_start = max(starts)  # All words must exist
        predicted_end = min(ends)  # None have fallen out of use yet

        # If ranges don't overlap, use the median approach
        if predicted_start > predicted_end:
            predicted_start = int(median(starts))
            predicted_end = int(median(ends))

            # Ensure valid range
            if predicted_start > predicted_end:
                mid = (predicted_start + predicted_end) // 2
                predicted_start = mid - 50
                predicted_end = mid + 50

        return (predicted_start, predicted_end)

    def _calculate_agreement(
        self, date_ranges: list[tuple[int, int]], predicted: tuple[int, int]
    ) -> float:
        """
        Calculate how well the word date ranges agree with the prediction.

        Args:
            date_ranges: List of word date ranges.
            predicted: The predicted date range.

        Returns:
            Agreement score between 0 and 1.
        """
        if not date_ranges:
            return 0.0

        pred_start, pred_end = predicted
        pred_mid = (pred_start + pred_end) // 2

        # Calculate how many words support the prediction
        supporting = 0
        for start, end in date_ranges:
            # Word supports prediction if prediction falls within word's range
            if start <= pred_mid <= end:
                supporting += 1

        return supporting / len(date_ranges)


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
