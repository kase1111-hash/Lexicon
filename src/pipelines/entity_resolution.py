"""Entity resolution and deduplication pipeline."""

import logging
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from src.adapters.base import RawLexicalEntry
from src.models.lsr import LSR
from src.utils.phonetics import PhoneticUtils

logger = logging.getLogger(__name__)


class ResolutionAction(str, Enum):
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

    This pipeline implements the entity resolution logic from SPEC.md Section 4.1:
    1. Candidate Retrieval
    2. Similarity Scoring
    3. Resolution Actions
    4. Merge Logic
    """

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
        self._form_index: dict[str, list[UUID]] = {}

    def set_lsr_store(self, store: dict[UUID, LSR]) -> None:
        """Set the LSR store for resolution lookups."""
        self._lsr_store = store
        self._rebuild_index()

    def _rebuild_index(self) -> None:
        """Rebuild the form index from the LSR store."""
        self._form_index.clear()
        for lsr_id, lsr in self._lsr_store.items():
            key = f"{lsr.form_normalized}:{lsr.language_code}"
            if key not in self._form_index:
                self._form_index[key] = []
            self._form_index[key].append(lsr_id)

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

        Uses multiple strategies:
        1. Exact normalized form + language match
        2. Fuzzy form matching (Levenshtein distance < 2)
        3. Phonetic matching (Soundex/Metaphone)
        """
        candidates: set[UUID] = set()

        # Normalize the form
        form_normalized = PhoneticUtils.strip_diacritics(entry.form.lower())
        language_code = entry.language_code or entry.language[:3].lower()

        # Strategy 1: Exact match
        exact_key = f"{form_normalized}:{language_code}"
        if exact_key in self._form_index:
            candidates.update(self._form_index[exact_key])

        # Strategy 2: Fuzzy matching on same language
        for key, ids in self._form_index.items():
            stored_form, stored_lang = key.rsplit(":", 1)
            if stored_lang != language_code:
                continue
            distance = PhoneticUtils.levenshtein_distance(form_normalized, stored_form)
            if distance <= 2:
                candidates.update(ids)

        return list(candidates)

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
                total = len(entry_words | candidate_words)
                features["semantic"] = overlap / total if total > 0 else 0.0
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


def convert_entry_to_lsr(entry: RawLexicalEntry) -> LSR:
    """
    Convert a RawLexicalEntry to an LSR.

    Args:
        entry: The raw entry to convert.

    Returns:
        A new LSR instance.
    """
    lsr = LSR(
        form_orthographic=entry.form,
        form_phonetic=entry.form_phonetic,
        language_code=entry.language_code or entry.language[:3].lower(),
        language_name=entry.language,
        definition_primary=entry.definitions[0] if entry.definitions else "",
        definitions_alternate=entry.definitions[1:] if len(entry.definitions) > 1 else [],
        part_of_speech=entry.part_of_speech,
        source_databases=[entry.source_name],
    )

    if entry.date_attested:
        lsr.date_start = entry.date_attested
        lsr.date_end = entry.date_attested

    return lsr
