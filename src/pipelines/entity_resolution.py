"""Entity resolution and deduplication pipeline."""

import logging
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid5

from pydantic import BaseModel, Field

from src.adapters.base import RawLexicalEntry
from src.models.lsr import LSR, YEAR_MAX, YEAR_MIN
from src.utils.languages import LANGUAGE_CODE_MAP
from src.utils.phonetics import PhoneticUtils

logger = logging.getLogger(__name__)

# Namespace for deterministic LSR ids derived from source records
LSR_ID_NAMESPACE = UUID("8d3f7c5e-2b1a-5f4e-9c6d-4a7b8e9f0a1b")


class ResolutionAction(StrEnum):
    """Actions that can be taken during entity resolution."""

    AUTO_MERGE = "auto_merge"  # High confidence match, merge automatically
    MERGE_WITH_FLAG = "merge_with_flag"  # Merge but flag for review
    FLAG_FOR_REVIEW = "flag_for_review"  # Create as candidate duplicate
    CREATE_NEW = "create_new"  # No match found, create new LSR


class ResolutionResult(BaseModel):
    """Result of entity resolution for a single entry."""

    action: ResolutionAction
    existing_id: UUID | None = None
    similarity_score: float = 0.0
    feature_scores: dict[str, float] = Field(default_factory=dict)
    merge_log: dict[str, Any] | None = None
    issues: list[str] = Field(default_factory=list)


class SimilarityWeights(BaseModel):
    """Configurable weights for similarity scoring."""

    form_exact: float = 0.3
    form_fuzzy: float = 0.2
    semantic: float = 0.3
    date_overlap: float = 0.1
    source_agreement: float = 0.1


class EntityResolver:
    """
    Match incoming entries to existing LSRs or create new ones.

    This pipeline implements the entity resolution logic from docs/archive/SPEC.md Section 4.1:
    1. Candidate Retrieval
    2. Similarity Scoring
    3. Resolution Actions
    4. Merge Logic
    """

    # Separator for form:language index keys
    # Using "||" because it won't appear in normalized word forms
    INDEX_KEY_SEPARATOR = "||"

    def __init__(
        self,
        auto_merge_threshold: float = 0.95,
        merge_with_flag_threshold: float = 0.85,
        review_threshold: float = 0.70,
        weights: SimilarityWeights | None = None,
    ):
        """
        Initialize the entity resolver.

        Args:
            auto_merge_threshold: Score >= this triggers automatic merge.
            merge_with_flag_threshold: Score >= this triggers merge with review flag.
            review_threshold: Score >= this creates candidate duplicate.
            weights: Configurable weights for similarity features.
        """
        self.auto_merge_threshold = auto_merge_threshold
        self.merge_with_flag_threshold = merge_with_flag_threshold
        self.review_threshold = review_threshold
        self.weights = weights or SimilarityWeights()

        # These would be injected in production
        self._lsr_store: dict[UUID, LSR] = {}
        # "form_normalized||language_code" -> LSR ids
        self._form_index: dict[str, list[UUID]] = {}

    def set_lsr_store(self, store: dict[UUID, LSR]) -> None:
        """Set the LSR store for resolution lookups."""
        self._lsr_store = store
        self._rebuild_index()

    def add_lsr(self, lsr: LSR) -> None:
        """Add one LSR to the store and indices without a full rebuild."""
        self._lsr_store[lsr.id] = lsr
        self._index_lsr(lsr.id, lsr)

    def _rebuild_index(self) -> None:
        """Rebuild the form index from the LSR store."""
        self._form_index.clear()
        for lsr_id, lsr in self._lsr_store.items():
            self._index_lsr(lsr_id, lsr)

    def _index_lsr(self, lsr_id: UUID, lsr: LSR) -> None:
        """Add one LSR to the form index."""
        key = f"{lsr.form_normalized}{self.INDEX_KEY_SEPARATOR}{lsr.language_code}"
        self._form_index.setdefault(key, []).append(lsr_id)

    def resolve(self, entry: RawLexicalEntry) -> ResolutionResult:
        """
        Resolve a single entry against existing LSRs.

        Args:
            entry: The raw lexical entry to resolve.

        Returns:
            ResolutionResult with action and details.
        """
        # Step 1: Candidate Retrieval
        candidates = self._retrieve_candidates(entry)

        if not candidates:
            return ResolutionResult(
                action=ResolutionAction.CREATE_NEW,
                similarity_score=0.0,
            )

        # Step 2: Similarity Scoring
        best_match: tuple[UUID | None, float, dict[str, float]] = (None, 0.0, {})

        for candidate_id in candidates:
            candidate = self._lsr_store.get(candidate_id)
            if candidate is None:
                continue

            score, features = self._calculate_similarity(entry, candidate)
            if score > best_match[1]:
                best_match = (candidate_id, score, features)

        match_id, score, feature_scores = best_match

        # Step 3: Resolution Actions
        if score >= self.auto_merge_threshold:
            action = ResolutionAction.AUTO_MERGE
        elif score >= self.merge_with_flag_threshold:
            action = ResolutionAction.MERGE_WITH_FLAG
        elif score >= self.review_threshold:
            action = ResolutionAction.FLAG_FOR_REVIEW
        else:
            action = ResolutionAction.CREATE_NEW
            match_id = None

        return ResolutionResult(
            action=action,
            existing_id=match_id,
            similarity_score=score,
            feature_scores=feature_scores,
        )

    def _retrieve_candidates(self, entry: RawLexicalEntry) -> list[UUID]:
        """
        Retrieve candidate LSRs that might match the entry.

        Only LSRs with the same normalized form and language are candidates.
        A candidate whose form differs gets no form_exact credit, so with
        the default weights it scores below 0.2 (fuzzy) + 0.3 + 0.1 + 0.1
        = 0.70, under the review threshold: fuzzy or phonetic look-alikes
        could never change the outcome, and comparing every entry with
        every stored form made resolution quadratic in vocabulary size.
        """
        form_normalized = PhoneticUtils.strip_diacritics(entry.form.lower())
        language_code = resolve_language_code(entry.language, entry.language_code)
        exact_key = f"{form_normalized}{self.INDEX_KEY_SEPARATOR}{language_code}"
        return list(dict.fromkeys(self._form_index.get(exact_key, [])))

    def _calculate_similarity(
        self, entry: RawLexicalEntry, candidate: LSR
    ) -> tuple[float, dict[str, float]]:
        """
        Calculate weighted similarity score between entry and candidate.

        Returns:
            Tuple of (total_score, feature_scores_dict)
        """
        features: dict[str, float] = {}

        # Form exact match
        entry_normalized = PhoneticUtils.strip_diacritics(entry.form.lower())
        features["form_exact"] = 1.0 if entry_normalized == candidate.form_normalized else 0.0

        # Form fuzzy score
        distance = PhoneticUtils.levenshtein_distance(entry_normalized, candidate.form_normalized)
        max_len = max(len(entry_normalized), len(candidate.form_normalized), 1)
        features["form_fuzzy"] = max(0.0, 1.0 - (distance / max_len))

        # Semantic similarity (placeholder - would use embeddings)
        # Compare definitions for now
        if entry.definitions and candidate.definition_primary:
            entry_def = " ".join(entry.definitions).lower()
            candidate_def = candidate.definition_primary.lower()
            # Simple word overlap metric
            entry_words = set(entry_def.split())
            candidate_words = set(candidate_def.split())
            if entry_words and candidate_words:
                overlap = len(entry_words & candidate_words)
                union_size = len(entry_words | candidate_words)
                features["semantic"] = overlap / union_size if union_size > 0 else 0.0
            else:
                features["semantic"] = 0.0
        else:
            features["semantic"] = 0.5  # Neutral if no definition available

        # Date overlap
        if entry.date_attested and candidate.date_start and candidate.date_end:
            if candidate.date_start <= entry.date_attested <= candidate.date_end:
                features["date_overlap"] = 1.0
            else:
                # Partial credit for close dates
                distance_to_range = min(
                    abs(entry.date_attested - candidate.date_start),
                    abs(entry.date_attested - candidate.date_end),
                )
                features["date_overlap"] = max(0.0, 1.0 - (distance_to_range / 100))
        else:
            features["date_overlap"] = 0.5  # Neutral if no date available

        # Source agreement
        if entry.source_name in candidate.source_databases:
            features["source_agreement"] = 1.0
        else:
            features["source_agreement"] = 0.0

        # Calculate weighted total
        total = (
            features["form_exact"] * self.weights.form_exact
            + features["form_fuzzy"] * self.weights.form_fuzzy
            + features["semantic"] * self.weights.semantic
            + features["date_overlap"] * self.weights.date_overlap
            + features["source_agreement"] * self.weights.source_agreement
        )

        return total, features

    def process_batch(self, entries: list[RawLexicalEntry]) -> list[ResolutionResult]:
        """
        Process a batch of entries for resolution.

        Args:
            entries: List of raw lexical entries.

        Returns:
            List of resolution results.
        """
        results = []
        for entry in entries:
            try:
                result = self.resolve(entry)
                results.append(result)
            except Exception as e:
                logger.error(f"Error resolving entry {entry.source_id}: {e}")
                results.append(
                    ResolutionResult(
                        action=ResolutionAction.CREATE_NEW,
                        issues=[str(e)],
                    )
                )
        return results

    def merge_lsrs(self, target: LSR, source: LSR) -> dict[str, Any]:
        """
        Merge source LSR into target LSR.

        Args:
            target: The LSR to merge into.
            source: The LSR to merge from.

        Returns:
            Merge log with details of what was merged.
        """
        merge_log = {
            "target_id": str(target.id),
            "source_id": str(source.id),
            "merged_fields": [],
        }

        target.merge_with(source)
        merge_log["merged_fields"] = ["attestations", "source_databases", "definitions"]

        logger.info(f"Merged LSR {source.id} into {target.id}")
        return merge_log


def lsr_id_for_entry(entry: RawLexicalEntry) -> UUID:
    """Deterministic LSR id for a source entry.

    Re-ingesting the same source record yields the same id, so writes to
    the graph are idempotent upserts rather than duplicates.
    """
    return uuid5(LSR_ID_NAMESPACE, f"{entry.source_name}:{entry.source_id}")


def resolve_language_code(language: str, language_code: str = "") -> str:
    """Return an ISO 639-3 code for a language name, or "" if unknown."""
    if language_code:
        return language_code
    if language in LANGUAGE_CODE_MAP:
        return LANGUAGE_CODE_MAP[language]
    # Already a code (e.g. "eng")
    if len(language) == 3 and language.isalpha() and language.islower():
        return language
    return ""


def convert_entry_to_lsr(entry: RawLexicalEntry) -> LSR:
    """
    Convert a RawLexicalEntry to an LSR.

    ``date_attested`` is the earliest attestation, so it becomes
    ``date_start``; ``date_end`` stays unset, meaning the form is not
    known to have fallen out of use. A year outside the range an LSR can
    hold is dropped with a warning. An undated entry gets date_confidence
    0.0 and keeps the source's own age label (e.g. WOLD "Pre 100 CE") as
    its period_label.

    Args:
        entry: The raw entry to convert.

    Returns:
        A new LSR instance.
    """
    raw = entry.raw_data or {}
    semantic_field = raw.get("semantic_field")
    date_start = entry.date_attested
    if date_start is not None and not YEAR_MIN <= date_start <= YEAR_MAX:
        logger.warning(
            f"Dropping attestation year {date_start} of {entry.source_name} entry "
            f"'{entry.form}' ({entry.source_id}): outside {YEAR_MIN}..{YEAR_MAX}"
        )
        date_start = None
    if date_start is None:
        date_confidence = 0.0
    elif raw.get("date_confidence") is not None:
        date_confidence = float(raw["date_confidence"])
    else:
        date_confidence = 1.0

    return LSR(
        id=lsr_id_for_entry(entry),
        form_orthographic=entry.form,
        form_phonetic=entry.form_phonetic,
        language_code=resolve_language_code(entry.language, entry.language_code),
        language_name=entry.language,
        language_family=raw.get("language_family") or "",
        definition_primary=entry.definitions[0] if entry.definitions else "",
        definitions_alternate=entry.definitions[1:] if len(entry.definitions) > 1 else [],
        part_of_speech=entry.part_of_speech,
        semantic_fields=[semantic_field] if semantic_field else [],
        etymology_text=entry.etymology or "",
        source_databases=[entry.source_name],
        date_start=date_start,
        date_confidence=date_confidence,
        period_label=raw.get("period_label") or "",
    )
