"""Relationship extraction pipeline for creating graph edges.

Parses etymology text to extract DESCENDS_FROM, BORROWED_FROM, and COGNATE_OF
relationships. Works with both clean text and raw Wiktionary template data.
"""

import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from Levenshtein import ratio as levenshtein_ratio

logger = logging.getLogger(__name__)


class RelationshipType(StrEnum):
    """Types of relationships between LSRs."""

    DESCENDS_FROM = "DESCENDS_FROM"
    BORROWED_FROM = "BORROWED_FROM"
    COGNATE_OF = "COGNATE_OF"
    SHIFTED_TO = "SHIFTED_TO"
    MERGED_WITH = "MERGED_WITH"


@dataclass
class ExtractedRelationship:
    """A relationship extracted from etymology text or cross-references."""

    source_id: UUID
    target_id: UUID
    relationship_type: RelationshipType
    confidence: float
    date_of_change: int | None
    change_type: str | None
    evidence: list[str]


@dataclass
class RawRelationship:
    """A relationship extracted from text, not yet resolved to UUIDs.

    This is the intermediate form before we can look up target LSR IDs.
    """

    target_form: str
    target_language: str
    target_language_code: str
    relationship_type: RelationshipType
    confidence: float
    evidence: str
    is_reconstructed: bool = False


# Language name -> ISO 639-3 code mapping (shared with wiktionary adapter)
LANGUAGE_CODE_MAP = {
    "English": "eng",
    "French": "fra",
    "German": "deu",
    "Spanish": "spa",
    "Italian": "ita",
    "Portuguese": "por",
    "Dutch": "nld",
    "Russian": "rus",
    "Polish": "pol",
    "Latin": "lat",
    "Ancient Greek": "grc",
    "Greek": "ell",
    "Old English": "ang",
    "Middle English": "enm",
    "Old French": "fro",
    "Middle French": "frm",
    "Old Norse": "non",
    "Old High German": "goh",
    "Middle High German": "gmh",
    "Middle Dutch": "dum",
    "Proto-Germanic": "gem-pro",
    "Proto-Indo-European": "ine-pro",
    "Proto-Slavic": "sla-pro",
    "Proto-Romance": "roa-pro",
    "Proto-Celtic": "cel-pro",
    "Sanskrit": "san",
    "Arabic": "ara",
    "Hebrew": "heb",
    "Japanese": "jpn",
    "Chinese": "zho",
    "Korean": "kor",
    "Norman": "nrf",
    "Anglo-Norman": "xno",
    "Vulgar Latin": "la-vul",
}

# Build reverse map for code -> name
CODE_TO_LANGUAGE = {v: k for k, v in LANGUAGE_CODE_MAP.items()}

# Patterns that indicate borrowing vs. inheritance
BORROWING_INDICATORS = {
    "borrowed",
    "loan",
    "loanword",
    "calque",
    "borrowed from",
    "taken from",
    "adopted from",
    "through",
    "via",
}

INHERITANCE_INDICATORS = {
    "from",
    "inherited from",
    "derives from",
    "derived from",
    "descended from",
    "descends from",
    "cognate with",
}

# Known language names for pattern matching (sorted by length desc to match longest first)
_KNOWN_LANGUAGES = sorted(LANGUAGE_CODE_MAP.keys(), key=len, reverse=True)
_LANG_ALT = "|".join(re.escape(lang) for lang in _KNOWN_LANGUAGES)

# Regex for common etymology patterns using known language names
# "From X word" or "from X word"
_FROM_PATTERN = re.compile(
    r"(?:(?:inherited|derived|descended?)\s+)?from\s+"
    rf"({_LANG_ALT})\s+"  # known language name
    r"(\*?\w[\w\s-]*)",  # form (optional * for reconstructed)
    re.IGNORECASE,
)

# "Borrowed from X word"
_BORROWED_PATTERN = re.compile(
    r"(?:borrowed|loan(?:ed)?|taken|adopted)\s+from\s+"
    rf"({_LANG_ALT})\s+"  # known language name
    r"(\*?\w[\w\s-]*)",  # form
    re.IGNORECASE,
)

# "Cognate with X word"
_COGNATE_PATTERN = re.compile(
    r"cognate\s+with\s+"
    rf"({_LANG_ALT})\s+"  # known language name
    r"(\*?\w[\w\s-]*)",  # form
    re.IGNORECASE,
)

_LANGUAGE_PATTERN = re.compile(r"\b(" + _LANG_ALT + r")\b")


class RelationshipExtractor:
    """Extract graph edges from etymology text and cross-references.

    Usage:
        extractor = RelationshipExtractor()
        raw_rels = extractor.extract_from_etymology_text(etymology_string)
        # raw_rels contains RawRelationship objects with target form+language
        # Resolve against LSR store to get full ExtractedRelationship objects
    """

    def __init__(self, lsr_store: Mapping[UUID, object] | None = None):
        """Initialize with optional LSR store for UUID resolution.

        Args:
            lsr_store: Dict mapping UUID -> LSR objects for resolving
                       target forms to UUIDs.
        """
        self._lsr_store = lsr_store or {}
        self._form_index: dict[str, list[UUID]] = {}
        if lsr_store:
            self._build_index()

    def set_lsr_store(self, store: Mapping[UUID, object]) -> None:
        """Set the LSR store and rebuild the form index."""
        self._lsr_store = store
        self._build_index()

    def _build_index(self) -> None:
        """Build a form+language -> UUID index for fast lookup."""
        self._form_index.clear()
        for lsr_id, lsr in self._lsr_store.items():
            form = getattr(lsr, "form_normalized", "") or ""
            lang = getattr(lsr, "language_code", "") or ""
            key = f"{form.lower()}:{lang}"
            self._form_index.setdefault(key, []).append(lsr_id)

    def extract_from_etymology_text(self, etymology_text: str) -> list[RawRelationship]:
        """Extract relationships from plain etymology text.

        Parses patterns like:
        - "From Old English wæter" -> DESCENDS_FROM
        - "Borrowed from French justice" -> BORROWED_FROM
        - "Cognate with German Wasser" -> COGNATE_OF

        Args:
            etymology_text: The etymology text to parse.

        Returns:
            List of RawRelationship objects (not yet resolved to UUIDs).
        """
        if not etymology_text:
            return []

        relationships: list[RawRelationship] = []

        # Extract borrowed-from relationships (check first, more specific)
        for match in _BORROWED_PATTERN.finditer(etymology_text):
            lang_name = match.group(1).strip()
            form = match.group(2).strip()
            rel = self._make_raw_relationship(
                form,
                lang_name,
                RelationshipType.BORROWED_FROM,
                confidence=0.8,
                evidence=match.group(0),
            )
            if rel:
                relationships.append(rel)

        # Extract cognate relationships
        for match in _COGNATE_PATTERN.finditer(etymology_text):
            lang_name = match.group(1).strip()
            form = match.group(2).strip()
            rel = self._make_raw_relationship(
                form,
                lang_name,
                RelationshipType.COGNATE_OF,
                confidence=0.7,
                evidence=match.group(0),
            )
            if rel:
                relationships.append(rel)

        # Extract inheritance (from X) relationships
        for match in _FROM_PATTERN.finditer(etymology_text):
            lang_name = match.group(1).strip()
            form = match.group(2).strip()

            # Skip if already matched as borrowing
            if any(
                r.target_form == form.lstrip("*") and r.target_language == lang_name
                for r in relationships
            ):
                continue

            # Check if the "from" context suggests borrowing
            context_start = max(0, match.start() - 30)
            context = etymology_text[context_start : match.start()].lower()
            is_borrowing = any(ind in context for ind in BORROWING_INDICATORS)

            rel_type = (
                RelationshipType.BORROWED_FROM if is_borrowing else RelationshipType.DESCENDS_FROM
            )
            rel = self._make_raw_relationship(
                form,
                lang_name,
                rel_type,
                confidence=0.7,
                evidence=match.group(0),
            )
            if rel:
                relationships.append(rel)

        return relationships

    def extract_from_templates(self, templates: list[dict]) -> list[RawRelationship]:
        """Extract relationships from parsed Wiktionary templates.

        Args:
            templates: List of template dicts with keys: name, lang, term, etc.

        Returns:
            List of RawRelationship objects.
        """
        relationships: list[RawRelationship] = []

        for tmpl in templates:
            name = tmpl.get("name", "").lower()
            lang_code = tmpl.get("lang", "")
            term = tmpl.get("term", "")

            if not lang_code or not term:
                continue

            lang_name = CODE_TO_LANGUAGE.get(lang_code, lang_code)

            if name in ("inh", "inherited"):
                rel = RawRelationship(
                    target_form=term.lstrip("*"),
                    target_language=lang_name,
                    target_language_code=lang_code,
                    relationship_type=RelationshipType.DESCENDS_FROM,
                    confidence=0.9,
                    evidence=f"{{{{inh|...|{lang_code}|{term}}}}}",
                    is_reconstructed=term.startswith("*"),
                )
                relationships.append(rel)

            elif name in ("bor", "borrowed"):
                rel = RawRelationship(
                    target_form=term.lstrip("*"),
                    target_language=lang_name,
                    target_language_code=lang_code,
                    relationship_type=RelationshipType.BORROWED_FROM,
                    confidence=0.9,
                    evidence=f"{{{{bor|...|{lang_code}|{term}}}}}",
                    is_reconstructed=term.startswith("*"),
                )
                relationships.append(rel)

            elif name in ("der", "derived"):
                # "derived" can be either inheritance or borrowing
                rel = RawRelationship(
                    target_form=term.lstrip("*"),
                    target_language=lang_name,
                    target_language_code=lang_code,
                    relationship_type=RelationshipType.DESCENDS_FROM,
                    confidence=0.7,
                    evidence=f"{{{{der|...|{lang_code}|{term}}}}}",
                    is_reconstructed=term.startswith("*"),
                )
                relationships.append(rel)

            elif name in ("cog", "cognate"):
                rel = RawRelationship(
                    target_form=term.lstrip("*"),
                    target_language=lang_name,
                    target_language_code=lang_code,
                    relationship_type=RelationshipType.COGNATE_OF,
                    confidence=0.8,
                    evidence=f"{{{{cog|{lang_code}|{term}}}}}",
                    is_reconstructed=term.startswith("*"),
                )
                relationships.append(rel)

        return relationships

    def extract_from_etymology(
        self, lsr_id: UUID, etymology_text: str
    ) -> list[ExtractedRelationship]:
        """Extract relationships from etymology text and resolve to UUIDs.

        Args:
            lsr_id: The source LSR's UUID.
            etymology_text: The etymology text to parse.

        Returns:
            List of ExtractedRelationship objects with resolved UUIDs.
        """
        raw_rels = self.extract_from_etymology_text(etymology_text)
        return self._resolve_relationships(lsr_id, raw_rels)

    def detect_cognates(
        self,
        lsr_id: UUID,
        candidates: list[UUID],
        min_similarity: float = 0.6,
    ) -> list[ExtractedRelationship]:
        """Detect cognate relationships using form similarity.

        Compares the source LSR's form against candidate LSRs in other
        languages, using edit distance on normalized forms.

        Args:
            lsr_id: The source LSR UUID.
            candidates: List of candidate LSR UUIDs to compare against.
            min_similarity: Minimum Levenshtein ratio to consider as cognate.

        Returns:
            List of ExtractedRelationship objects for detected cognates.
        """
        source = self._lsr_store.get(lsr_id)
        if not source:
            return []

        source_form = getattr(source, "form_normalized", "") or ""
        source_lang = getattr(source, "language_code", "") or ""

        if not source_form:
            return []

        cognates: list[ExtractedRelationship] = []

        for candidate_id in candidates:
            candidate = self._lsr_store.get(candidate_id)
            if not candidate:
                continue

            cand_form = getattr(candidate, "form_normalized", "") or ""
            cand_lang = getattr(candidate, "language_code", "") or ""

            # Skip same language
            if cand_lang == source_lang:
                continue

            if not cand_form:
                continue

            # Compare normalized forms
            similarity = levenshtein_ratio(source_form.lower(), cand_form.lower())

            if similarity >= min_similarity:
                cognates.append(
                    ExtractedRelationship(
                        source_id=lsr_id,
                        target_id=candidate_id,
                        relationship_type=RelationshipType.COGNATE_OF,
                        confidence=round(similarity * 0.8, 2),  # Scale down slightly
                        date_of_change=None,
                        change_type=None,
                        evidence=[
                            f"Form similarity: {source_form} ~ {cand_form} "
                            f"({source_lang} ~ {cand_lang}, ratio={similarity:.2f})"
                        ],
                    )
                )

        return cognates

    def classify_borrowing(self, relationship: ExtractedRelationship) -> ExtractedRelationship:
        """Classify whether a relationship is inheritance or borrowing.

        Uses heuristics based on language family relationships.
        """
        # Simple heuristic: if source and target are in different families,
        # it's more likely a borrowing than inheritance.
        source = self._lsr_store.get(relationship.source_id)
        target = self._lsr_store.get(relationship.target_id)

        if source and target:
            source_family = getattr(source, "language_family", "")
            target_family = getattr(target, "language_family", "")

            if source_family and target_family and source_family != target_family:
                relationship.relationship_type = RelationshipType.BORROWED_FROM
                relationship.confidence = min(relationship.confidence + 0.1, 1.0)

        return relationship

    def process_new_lsrs(self, lsr_ids: list[UUID]) -> list[ExtractedRelationship]:
        """Process newly created LSRs for relationship extraction.

        Args:
            lsr_ids: List of new LSR UUIDs to process.

        Returns:
            List of all extracted relationships.
        """
        all_relationships: list[ExtractedRelationship] = []

        for lsr_id in lsr_ids:
            lsr = self._lsr_store.get(lsr_id)
            if not lsr:
                continue

            # Extract from etymology text
            etymology = getattr(lsr, "etymology_text", "") or ""
            if etymology:
                rels = self.extract_from_etymology(lsr_id, etymology)
                all_relationships.extend(rels)

            # Detect cognates with all other LSRs
            other_ids = [uid for uid in self._lsr_store if uid != lsr_id]
            cognates = self.detect_cognates(lsr_id, other_ids)
            all_relationships.extend(cognates)

        return all_relationships

    def _make_raw_relationship(
        self,
        form: str,
        lang_name: str,
        rel_type: RelationshipType,
        confidence: float,
        evidence: str,
    ) -> RawRelationship | None:
        """Create a RawRelationship if the language is recognized."""
        # Clean the form
        form = form.strip().rstrip(".,;:")
        is_reconstructed = form.startswith("*")
        form = form.lstrip("*")

        if not form:
            return None

        # Try to resolve language name
        lang_code = LANGUAGE_CODE_MAP.get(lang_name, "")
        if not lang_code:
            # Try fuzzy match on language name
            for known_lang, code in LANGUAGE_CODE_MAP.items():
                if (
                    lang_name.lower() in known_lang.lower()
                    or known_lang.lower() in lang_name.lower()
                ):
                    lang_code = code
                    lang_name = known_lang
                    break

        if not lang_code:
            return None

        return RawRelationship(
            target_form=form,
            target_language=lang_name,
            target_language_code=lang_code,
            relationship_type=rel_type,
            confidence=confidence,
            evidence=evidence,
            is_reconstructed=is_reconstructed,
        )

    def _resolve_relationships(
        self, source_id: UUID, raw_rels: list[RawRelationship]
    ) -> list[ExtractedRelationship]:
        """Resolve RawRelationships to ExtractedRelationships with UUIDs."""
        resolved: list[ExtractedRelationship] = []

        for raw in raw_rels:
            target_ids = self._form_index.get(
                f"{raw.target_form.lower()}:{raw.target_language_code}", []
            )

            if not target_ids:
                # Try without language code
                for key, ids in self._form_index.items():
                    if key.startswith(f"{raw.target_form.lower()}:"):
                        target_ids = ids
                        break

            if target_ids:
                # Use first match (could be improved with better ranking)
                resolved.append(
                    ExtractedRelationship(
                        source_id=source_id,
                        target_id=target_ids[0],
                        relationship_type=raw.relationship_type,
                        confidence=raw.confidence,
                        date_of_change=None,
                        change_type=None,
                        evidence=[raw.evidence],
                    )
                )

        return resolved
