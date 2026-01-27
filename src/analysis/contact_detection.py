"""Language contact event detection.

Analyzes lexical borrowing patterns to detect historical language contact events.
Uses:
1. Loanword clustering by time period
2. Semantic field analysis (what domains were borrowed)
3. Phonological adaptation patterns
4. Directional borrowing analysis
"""

import logging
import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ContactEvent:
    """A detected language contact event."""

    donor_language: str
    recipient_language: str
    date_range: tuple[int, int]
    vocabulary_count: int
    confidence: float
    sample_words: list[str] = field(default_factory=list)
    contact_type: str | None = None  # trade, conquest, cultural, religious, etc.
    semantic_domains: list[str] = field(default_factory=list)
    intensity: float = 0.0  # 0-1 scale of contact intensity
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass
class BorrowingPattern:
    """Analysis of borrowing patterns between two languages."""

    donor: str
    recipient: str
    total_borrowings: int
    by_period: dict[str, int] = field(default_factory=dict)
    by_domain: dict[str, int] = field(default_factory=dict)
    phonological_adaptations: list[str] = field(default_factory=list)
    peak_period: tuple[int, int] | None = None
    contact_events: list[ContactEvent] = field(default_factory=list)


# Semantic domain indicators for contact type classification
CONTACT_TYPE_INDICATORS = {
    "trade": [
        "commerce", "money", "goods", "merchant", "market", "price",
        "trade", "exchange", "cargo", "ship", "port",
    ],
    "conquest": [
        "military", "war", "weapon", "soldier", "army", "battle",
        "king", "ruler", "law", "court", "tax", "tribute",
    ],
    "religious": [
        "religion", "god", "temple", "priest", "sacred", "ritual",
        "prayer", "worship", "heaven", "soul", "sin",
    ],
    "cultural": [
        "art", "music", "literature", "education", "philosophy",
        "science", "fashion", "food", "architecture",
    ],
    "technological": [
        "technology", "tool", "machine", "invention", "science",
        "medicine", "agriculture", "craft", "manufacture",
    ],
}

# Semantic field keywords for domain classification
SEMANTIC_DOMAINS = {
    "administration": ["law", "government", "court", "official", "tax", "rule"],
    "military": ["war", "army", "weapon", "soldier", "battle", "fight"],
    "religion": ["god", "temple", "priest", "sacred", "ritual", "prayer"],
    "commerce": ["trade", "merchant", "money", "market", "goods", "price"],
    "food": ["food", "eat", "drink", "cook", "meal", "fruit", "vegetable"],
    "nature": ["animal", "plant", "tree", "flower", "bird", "fish"],
    "technology": ["tool", "machine", "build", "craft", "make", "work"],
    "culture": ["art", "music", "dance", "literature", "story", "tradition"],
    "kinship": ["family", "father", "mother", "brother", "sister", "child"],
    "body": ["body", "head", "hand", "foot", "eye", "heart"],
}


class ContactDetector:
    """
    Detect historical language contact events.

    Analyzes lexical borrowing patterns to identify when and how
    languages came into contact, what domains were affected, and
    the nature of the contact (trade, conquest, cultural exchange, etc.).
    """

    def __init__(
        self,
        lsr_data: dict[str, list[dict[str, Any]]] | None = None,
        borrowing_data: list[dict[str, Any]] | None = None,
    ):
        """
        Initialize contact detector.

        Args:
            lsr_data: Dictionary of LSR data by form.
            borrowing_data: List of borrowing relationship data.
                Each entry should have: source_id, target_id, source_lang,
                target_lang, date, semantic_fields, definition.
        """
        self._lsr_data = lsr_data or {}
        self._borrowing_data = borrowing_data or []
        self._language_pairs: dict[tuple[str, str], list[dict]] = defaultdict(list)

        # Index borrowings by language pair
        for borrowing in self._borrowing_data:
            source_lang = borrowing.get("source_lang", "")
            target_lang = borrowing.get("target_lang", "")
            if source_lang and target_lang:
                self._language_pairs[(source_lang, target_lang)].append(borrowing)

    def set_borrowing_data(self, data: list[dict[str, Any]]) -> None:
        """Set borrowing relationship data."""
        self._borrowing_data = data
        self._language_pairs.clear()
        for borrowing in data:
            source_lang = borrowing.get("source_lang", "")
            target_lang = borrowing.get("target_lang", "")
            if source_lang and target_lang:
                self._language_pairs[(source_lang, target_lang)].append(borrowing)

    def detect_contacts(
        self,
        language: str,
        date_start: int | None = None,
        date_end: int | None = None,
        min_borrowings: int = 5,
        min_confidence: float = 0.3,
    ) -> list[ContactEvent]:
        """
        Detect contact events for a language in a time period.

        Args:
            language: ISO 639-3 language code to analyze.
            date_start: Start of time period (year, negative for BCE).
            date_end: End of time period (year).
            min_borrowings: Minimum borrowings to consider as a contact event.
            min_confidence: Minimum confidence threshold.

        Returns:
            List of detected ContactEvent objects.
        """
        events: list[ContactEvent] = []

        # Find all borrowings into this language
        incoming = self._get_borrowings_to_language(language, date_start, date_end)

        # Find all borrowings from this language
        outgoing = self._get_borrowings_from_language(language, date_start, date_end)

        # Cluster borrowings by donor language and time period
        donor_clusters = self._cluster_by_language_and_period(incoming)
        recipient_clusters = self._cluster_by_language_and_period(outgoing)

        # Detect events from incoming borrowings (language was recipient)
        for (donor, period), borrowings in donor_clusters.items():
            if len(borrowings) >= min_borrowings:
                event = self._create_contact_event(
                    donor=donor,
                    recipient=language,
                    borrowings=borrowings,
                    period=period,
                )
                if event.confidence >= min_confidence:
                    events.append(event)

        # Detect events from outgoing borrowings (language was donor)
        for (recipient, period), borrowings in recipient_clusters.items():
            if len(borrowings) >= min_borrowings:
                event = self._create_contact_event(
                    donor=language,
                    recipient=recipient,
                    borrowings=borrowings,
                    period=period,
                )
                if event.confidence >= min_confidence:
                    events.append(event)

        # Sort by confidence and date
        events.sort(key=lambda e: (-e.confidence, e.date_range[0]))

        return events

    def analyze_borrowing_patterns(
        self,
        donor: str,
        recipient: str,
        date_start: int | None = None,
        date_end: int | None = None,
    ) -> BorrowingPattern:
        """
        Analyze borrowing patterns between two languages.

        Args:
            donor: ISO 639-3 code of donor language.
            recipient: ISO 639-3 code of recipient language.
            date_start: Start of time period.
            date_end: End of time period.

        Returns:
            BorrowingPattern analysis object.
        """
        # Get borrowings for this language pair
        borrowings = self._language_pairs.get((donor, recipient), [])

        # Filter by date range
        if date_start is not None or date_end is not None:
            borrowings = [
                b for b in borrowings
                if self._in_date_range(
                    b.get("date"),
                    date_start,
                    date_end,
                )
            ]

        # Group by period (centuries)
        by_period: dict[str, int] = defaultdict(int)
        for b in borrowings:
            date = b.get("date")
            if date is not None:
                century = self._date_to_century_label(date)
                by_period[century] += 1

        # Group by semantic domain
        by_domain = self._group_by_domain(borrowings)

        # Identify phonological adaptations
        adaptations = self._identify_adaptations(borrowings)

        # Find peak period
        peak_period = None
        if by_period:
            peak_century = max(by_period, key=by_period.get)
            peak_period = self._century_label_to_range(peak_century)

        # Detect individual contact events
        clusters = self._cluster_by_time_period(borrowings)
        contact_events = []
        for period, period_borrowings in clusters.items():
            if len(period_borrowings) >= 3:
                event = self._create_contact_event(
                    donor=donor,
                    recipient=recipient,
                    borrowings=period_borrowings,
                    period=period,
                )
                contact_events.append(event)

        return BorrowingPattern(
            donor=donor,
            recipient=recipient,
            total_borrowings=len(borrowings),
            by_period=dict(by_period),
            by_domain=by_domain,
            phonological_adaptations=adaptations,
            peak_period=peak_period,
            contact_events=contact_events,
        )

    def get_contact_intensity(
        self,
        lang1: str,
        lang2: str,
        date_start: int | None = None,
        date_end: int | None = None,
    ) -> dict[str, Any]:
        """
        Calculate the intensity of contact between two languages.

        Args:
            lang1: First language ISO code.
            lang2: Second language ISO code.
            date_start: Start of time period.
            date_end: End of time period.

        Returns:
            Dictionary with contact intensity metrics.
        """
        # Get bidirectional borrowings
        lang1_to_lang2 = self._get_borrowings_between(lang1, lang2, date_start, date_end)
        lang2_to_lang1 = self._get_borrowings_between(lang2, lang1, date_start, date_end)

        total = len(lang1_to_lang2) + len(lang2_to_lang1)

        # Calculate asymmetry (which direction dominates)
        asymmetry = 0.0
        dominant_donor = None
        if total > 0:
            ratio1 = len(lang1_to_lang2) / total
            ratio2 = len(lang2_to_lang1) / total
            asymmetry = abs(ratio1 - ratio2)
            dominant_donor = lang1 if ratio1 > ratio2 else lang2

        # Calculate domain diversity (how many domains affected)
        all_borrowings = lang1_to_lang2 + lang2_to_lang1
        domains = self._group_by_domain(all_borrowings)
        domain_diversity = len(domains) / len(SEMANTIC_DOMAINS) if domains else 0

        # Calculate intensity score
        # Higher borrowing count, higher diversity = higher intensity
        intensity = min(1.0, (total / 100) * (0.5 + 0.5 * domain_diversity))

        return {
            "lang1": lang1,
            "lang2": lang2,
            "total_borrowings": total,
            "lang1_to_lang2": len(lang1_to_lang2),
            "lang2_to_lang1": len(lang2_to_lang1),
            "asymmetry": round(asymmetry, 3),
            "dominant_donor": dominant_donor,
            "domain_diversity": round(domain_diversity, 3),
            "domains_affected": list(domains.keys()),
            "intensity_score": round(intensity, 3),
            "date_range": (date_start, date_end),
        }

    def _get_borrowings_to_language(
        self,
        language: str,
        date_start: int | None,
        date_end: int | None,
    ) -> list[dict]:
        """Get all borrowings into a language."""
        result = []
        for (donor, recipient), borrowings in self._language_pairs.items():
            if recipient == language:
                for b in borrowings:
                    if self._in_date_range(b.get("date"), date_start, date_end):
                        result.append({**b, "donor": donor})
        return result

    def _get_borrowings_from_language(
        self,
        language: str,
        date_start: int | None,
        date_end: int | None,
    ) -> list[dict]:
        """Get all borrowings from a language."""
        result = []
        for (donor, recipient), borrowings in self._language_pairs.items():
            if donor == language:
                for b in borrowings:
                    if self._in_date_range(b.get("date"), date_start, date_end):
                        result.append({**b, "recipient": recipient})
        return result

    def _get_borrowings_between(
        self,
        donor: str,
        recipient: str,
        date_start: int | None,
        date_end: int | None,
    ) -> list[dict]:
        """Get borrowings from donor to recipient within date range."""
        borrowings = self._language_pairs.get((donor, recipient), [])
        if date_start is not None or date_end is not None:
            borrowings = [
                b for b in borrowings
                if self._in_date_range(b.get("date"), date_start, date_end)
            ]
        return borrowings

    def _in_date_range(
        self,
        date: int | None,
        date_start: int | None,
        date_end: int | None,
    ) -> bool:
        """Check if a date falls within the given range."""
        if date is None:
            return True  # Include undated borrowings
        if date_start is not None and date < date_start:
            return False
        if date_end is not None and date > date_end:
            return False
        return True

    def _cluster_by_language_and_period(
        self,
        borrowings: list[dict],
    ) -> dict[tuple[str, tuple[int, int]], list[dict]]:
        """Cluster borrowings by source/target language and time period."""
        clusters: dict[tuple[str, tuple[int, int]], list[dict]] = defaultdict(list)

        for b in borrowings:
            # Get the "other" language (donor or recipient depending on direction)
            other_lang = b.get("donor") or b.get("recipient") or b.get("source_lang")
            if not other_lang:
                continue

            date = b.get("date")
            if date is None:
                continue

            # Cluster by century
            century_start = (date // 100) * 100
            period = (century_start, century_start + 100)
            clusters[(other_lang, period)].append(b)

        return clusters

    def _cluster_by_time_period(
        self,
        borrowings: list[dict],
    ) -> dict[tuple[int, int], list[dict]]:
        """Cluster borrowings by time period (century)."""
        clusters: dict[tuple[int, int], list[dict]] = defaultdict(list)

        for b in borrowings:
            date = b.get("date")
            if date is None:
                continue

            century_start = (date // 100) * 100
            period = (century_start, century_start + 100)
            clusters[period].append(b)

        return clusters

    def _create_contact_event(
        self,
        donor: str,
        recipient: str,
        borrowings: list[dict],
        period: tuple[int, int],
    ) -> ContactEvent:
        """Create a ContactEvent from a cluster of borrowings."""
        # Get sample words
        sample_words = []
        for b in borrowings[:10]:  # Limit to 10 samples
            form = b.get("form") or b.get("target_form") or b.get("source_form")
            if form:
                sample_words.append(form)

        # Determine semantic domains
        domains = self._group_by_domain(borrowings)
        semantic_domains = list(domains.keys())

        # Classify contact type
        contact_type = self._classify_contact_type(domains)

        # Calculate confidence
        confidence = self._calculate_event_confidence(borrowings, domains)

        # Calculate intensity
        intensity = min(1.0, len(borrowings) / 50)  # 50+ borrowings = max intensity

        return ContactEvent(
            donor_language=donor,
            recipient_language=recipient,
            date_range=period,
            vocabulary_count=len(borrowings),
            confidence=round(confidence, 3),
            sample_words=sample_words,
            contact_type=contact_type,
            semantic_domains=semantic_domains,
            intensity=round(intensity, 3),
            evidence={
                "borrowing_count": len(borrowings),
                "domain_distribution": domains,
            },
        )

    def _group_by_domain(self, borrowings: list[dict]) -> dict[str, int]:
        """Group borrowings by semantic domain."""
        domain_counts: dict[str, int] = defaultdict(int)

        for b in borrowings:
            # Check semantic fields from data
            fields = b.get("semantic_fields", [])
            if fields:
                for f in fields:
                    domain_counts[f] += 1
                continue

            # Otherwise, try to classify from definition
            definition = b.get("definition", "").lower()
            if not definition:
                continue

            for domain, keywords in SEMANTIC_DOMAINS.items():
                if any(kw in definition for kw in keywords):
                    domain_counts[domain] += 1
                    break

        return dict(domain_counts)

    def _classify_contact_type(self, domains: dict[str, int]) -> str | None:
        """Classify contact type based on semantic domains."""
        if not domains:
            return None

        # Score each contact type
        scores: dict[str, float] = defaultdict(float)

        for domain, count in domains.items():
            domain_lower = domain.lower()
            for contact_type, indicators in CONTACT_TYPE_INDICATORS.items():
                if any(ind in domain_lower for ind in indicators):
                    scores[contact_type] += count

        if not scores:
            return None

        # Return highest-scoring type
        best_type = max(scores, key=scores.get)
        return best_type if scores[best_type] > 0 else None

    def _calculate_event_confidence(
        self,
        borrowings: list[dict],
        domains: dict[str, int],
    ) -> float:
        """Calculate confidence score for a contact event."""
        # Factors:
        # 1. Number of borrowings (more = higher confidence)
        # 2. Domain coherence (concentrated domains = higher)
        # 3. Date clustering (tight clustering = higher)

        count_score = min(1.0, len(borrowings) / 20)  # 20+ = max score

        # Domain coherence: entropy-based
        total = sum(domains.values()) if domains else 1
        entropy = 0.0
        for count in domains.values():
            p = count / total
            if p > 0:
                entropy -= p * math.log2(p)
        max_entropy = math.log2(len(SEMANTIC_DOMAINS))
        domain_score = 1.0 - (entropy / max_entropy) if max_entropy > 0 else 0.5

        # Date clustering: standard deviation based
        dates = [b.get("date") for b in borrowings if b.get("date") is not None]
        if len(dates) >= 2:
            mean_date = sum(dates) / len(dates)
            variance = sum((d - mean_date) ** 2 for d in dates) / len(dates)
            std_dev = math.sqrt(variance)
            # Lower std dev = higher score (50 years std dev = 0.5 score)
            date_score = max(0.0, 1.0 - std_dev / 100)
        else:
            date_score = 0.5

        # Weighted average
        return (count_score * 0.4 + domain_score * 0.3 + date_score * 0.3)

    def _identify_adaptations(self, borrowings: list[dict]) -> list[str]:
        """Identify common phonological adaptations in borrowings."""
        adaptations = []

        # This would need actual phonetic data to work properly
        # For now, return common adaptation patterns based on forms
        source_forms = [b.get("source_form", "") for b in borrowings]
        target_forms = [b.get("target_form", "") for b in borrowings]

        # Count common suffix changes
        suffix_changes: dict[str, int] = defaultdict(int)
        for src, tgt in zip(source_forms, target_forms):
            if src and tgt and len(src) > 2 and len(tgt) > 2:
                if src[-2:] != tgt[-2:]:
                    change = f"-{src[-2:]} > -{tgt[-2:]}"
                    suffix_changes[change] += 1

        # Return most common adaptations
        for change, count in sorted(suffix_changes.items(), key=lambda x: -x[1])[:5]:
            if count >= 2:
                adaptations.append(f"{change} ({count} instances)")

        return adaptations

    def _date_to_century_label(self, date: int) -> str:
        """Convert a year to a century label."""
        if date >= 0:
            century = (date // 100) + 1
            return f"{century}th century CE"
        else:
            century = (abs(date) // 100) + 1
            return f"{century}th century BCE"

    def _century_label_to_range(self, label: str) -> tuple[int, int]:
        """Convert a century label back to a year range."""
        # Parse "Nth century CE/BCE" format
        parts = label.split()
        if len(parts) < 3:
            return (0, 100)

        century = int(parts[0].rstrip("thstndrd"))
        is_bce = "BCE" in label

        if is_bce:
            end = -((century - 1) * 100)
            start = end - 100
        else:
            start = (century - 1) * 100
            end = century * 100

        return (start, end)


# Convenience functions for API use
def detect_language_contacts(
    language: str,
    borrowing_data: list[dict[str, Any]] | None = None,
    date_start: int | None = None,
    date_end: int | None = None,
) -> list[ContactEvent]:
    """
    Detect contact events for a language.

    Args:
        language: ISO 639-3 language code.
        borrowing_data: List of borrowing relationship data.
        date_start: Start of time period.
        date_end: End of time period.

    Returns:
        List of ContactEvent objects.
    """
    detector = ContactDetector(borrowing_data=borrowing_data)
    return detector.detect_contacts(language, date_start, date_end)


def analyze_language_pair(
    donor: str,
    recipient: str,
    borrowing_data: list[dict[str, Any]] | None = None,
) -> BorrowingPattern:
    """
    Analyze borrowing patterns between two languages.

    Args:
        donor: Donor language ISO code.
        recipient: Recipient language ISO code.
        borrowing_data: List of borrowing relationship data.

    Returns:
        BorrowingPattern analysis object.
    """
    detector = ContactDetector(borrowing_data=borrowing_data)
    return detector.analyze_borrowing_patterns(donor, recipient)
