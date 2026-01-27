"""Semantic drift analysis over time.

Analyzes how word meanings change over time using:
1. Definition comparison across time periods
2. Embedding-based semantic similarity
3. Shift event detection (metaphor, metonymy, etc.)
"""

import logging
import math
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

logger = logging.getLogger(__name__)


@dataclass
class TrajectoryPoint:
    """A point in a semantic trajectory."""

    date: int
    embedding_2d: tuple[float, float] = (0.0, 0.0)
    embedding_full: list[float] = field(default_factory=list)
    definition: str | None = None
    attestation_count: int = 0
    confidence: float = 1.0


@dataclass
class ShiftEvent:
    """A detected semantic shift event."""

    date: int
    change_type: str  # metaphor, metonymy, generalization, specialization, amelioration, pejoration
    confidence: float
    magnitude: float  # 0-1 scale of how significant the shift was
    before_meaning: str | None = None
    after_meaning: str | None = None
    evidence: str = ""


@dataclass
class SemanticTrajectory:
    """The semantic evolution of a word over time."""

    lsr_id: UUID | None
    form: str
    language: str
    points: list[TrajectoryPoint] = field(default_factory=list)
    shift_events: list[ShiftEvent] = field(default_factory=list)
    total_drift: float = 0.0  # Cumulative semantic distance traveled
    stability_score: float = 1.0  # 1 = very stable, 0 = highly drifting


# Keywords that suggest specific types of semantic shifts
SHIFT_INDICATORS = {
    "generalization": [
        "general", "broad", "any", "various", "multiple", "diverse",
        "extended", "expanded", "wider", "universal",
    ],
    "specialization": [
        "specific", "particular", "narrow", "limited", "restricted",
        "technical", "specialized", "precise", "exact",
    ],
    "metaphor": [
        "figurative", "metaphor", "like", "as if", "symbolic",
        "represents", "stands for", "imagery",
    ],
    "metonymy": [
        "associated", "related", "connected", "part of", "aspect",
        "attribute", "characteristic",
    ],
    "amelioration": [
        "positive", "improved", "elevated", "noble", "prestigious",
        "respected", "favorable", "better",
    ],
    "pejoration": [
        "negative", "degraded", "lowered", "vulgar", "derogatory",
        "offensive", "unfavorable", "worse",
    ],
}


class SemanticDriftAnalyzer:
    """
    Analyze semantic drift of words over time.

    This class provides methods to:
    1. Track how word meanings change across time periods
    2. Detect significant semantic shift events
    3. Compare semantic trajectories across languages
    4. Calculate drift metrics and stability scores
    """

    def __init__(
        self,
        lsr_data: dict[str, list[dict[str, Any]]] | None = None,
        embedding_dim: int = 384,
    ):
        """
        Initialize the semantic drift analyzer.

        Args:
            lsr_data: Dictionary mapping forms to lists of LSR data by time period.
                     Each LSR entry should have: date_start, date_end, definition,
                     semantic_vector, language_code.
            embedding_dim: Dimension of semantic embeddings.
        """
        self._lsr_data = lsr_data or {}
        self._embedding_dim = embedding_dim

    def set_lsr_data(self, data: dict[str, list[dict[str, Any]]]) -> None:
        """
        Set the LSR data for analysis.

        Args:
            data: Dictionary mapping forms to LSR time series data.
        """
        self._lsr_data = data

    def get_trajectory(
        self,
        form: str,
        language: str,
        time_slices: int = 10,
    ) -> SemanticTrajectory | None:
        """
        Get the semantic trajectory of a word over time.

        Args:
            form: The word form to analyze.
            language: ISO 639-3 language code.
            time_slices: Number of time periods to divide the range into.

        Returns:
            SemanticTrajectory or None if no data found.
        """
        # Get LSR data for this form
        form_key = f"{form.lower()}:{language}"
        lsr_entries = self._lsr_data.get(form_key, [])

        if not lsr_entries:
            # Try without language suffix
            lsr_entries = self._lsr_data.get(form.lower(), [])
            if not lsr_entries:
                logger.debug(f"No LSR data found for {form} in {language}")
                return None

        # Filter by language and sort by date
        entries = [
            e for e in lsr_entries
            if e.get("language_code") == language
        ]

        if not entries:
            return None

        # Sort by date
        entries.sort(key=lambda x: x.get("date_start") or 0)

        # Build trajectory points
        points: list[TrajectoryPoint] = []
        embeddings: list[list[float]] = []

        for entry in entries:
            date_start = entry.get("date_start")
            date_end = entry.get("date_end")
            if date_start is None:
                continue

            mid_date = (date_start + (date_end or date_start)) // 2
            embedding = entry.get("semantic_vector", [])

            point = TrajectoryPoint(
                date=mid_date,
                embedding_full=embedding,
                embedding_2d=self._reduce_to_2d(embedding) if embedding else (0.0, 0.0),
                definition=entry.get("definition_primary"),
                attestation_count=len(entry.get("attestations", [])),
                confidence=entry.get("confidence_overall", 1.0),
            )
            points.append(point)

            if embedding:
                embeddings.append(embedding)

        if not points:
            return None

        # Detect shift events
        shift_events = self._detect_shifts_from_points(points)

        # Calculate total drift
        total_drift = self._calculate_total_drift(embeddings)

        # Calculate stability score
        stability_score = self._calculate_stability(points, shift_events)

        # Get LSR ID from first entry if available
        lsr_id = None
        if entries and entries[0].get("id"):
            try:
                lsr_id = UUID(entries[0]["id"])
            except (ValueError, TypeError):
                pass

        return SemanticTrajectory(
            lsr_id=lsr_id,
            form=form,
            language=language,
            points=points,
            shift_events=shift_events,
            total_drift=round(total_drift, 4),
            stability_score=round(stability_score, 4),
        )

    def detect_shifts(
        self,
        form: str,
        language: str,
        threshold: float = 0.3,
    ) -> list[ShiftEvent]:
        """
        Detect semantic shift events for a word.

        Args:
            form: The word form to analyze.
            language: ISO 639-3 language code.
            threshold: Minimum magnitude to consider a shift significant.

        Returns:
            List of detected ShiftEvent objects.
        """
        trajectory = self.get_trajectory(form, language)
        if not trajectory:
            return []

        # Filter by threshold
        return [
            event for event in trajectory.shift_events
            if event.magnitude >= threshold
        ]

    def compare_trajectories(
        self,
        form: str,
        languages: list[str],
    ) -> dict[str, Any]:
        """
        Compare semantic trajectories of cognates across languages.

        Args:
            form: The word form (or cognate set) to analyze.
            languages: List of ISO 639-3 language codes.

        Returns:
            Dictionary with comparison results.
        """
        trajectories: dict[str, SemanticTrajectory | None] = {}
        for lang in languages:
            trajectories[lang] = self.get_trajectory(form, lang)

        # Remove None values
        valid_trajectories = {
            lang: traj for lang, traj in trajectories.items()
            if traj is not None
        }

        if not valid_trajectories:
            return {
                "form": form,
                "languages": languages,
                "trajectories_found": 0,
                "comparison": None,
            }

        # Calculate divergence metrics
        divergence_matrix: dict[str, dict[str, float]] = {}

        lang_list = list(valid_trajectories.keys())
        for i, lang1 in enumerate(lang_list):
            divergence_matrix[lang1] = {}
            for lang2 in lang_list[i:]:
                if lang1 == lang2:
                    divergence_matrix[lang1][lang2] = 0.0
                else:
                    div = self._calculate_trajectory_divergence(
                        valid_trajectories[lang1],
                        valid_trajectories[lang2],
                    )
                    divergence_matrix[lang1][lang2] = round(div, 4)
                    if lang2 not in divergence_matrix:
                        divergence_matrix[lang2] = {}
                    divergence_matrix[lang2][lang1] = round(div, 4)

        # Find most/least divergent pairs
        pairs = []
        for i, lang1 in enumerate(lang_list):
            for lang2 in lang_list[i + 1:]:
                pairs.append((lang1, lang2, divergence_matrix[lang1][lang2]))

        pairs.sort(key=lambda x: x[2])

        return {
            "form": form,
            "languages": languages,
            "trajectories_found": len(valid_trajectories),
            "divergence_matrix": divergence_matrix,
            "most_similar": pairs[0] if pairs else None,
            "most_divergent": pairs[-1] if pairs else None,
            "trajectories": {
                lang: {
                    "total_drift": traj.total_drift,
                    "stability_score": traj.stability_score,
                    "shift_count": len(traj.shift_events),
                }
                for lang, traj in valid_trajectories.items()
            },
        }

    def _reduce_to_2d(self, embedding: list[float]) -> tuple[float, float]:
        """
        Reduce a high-dimensional embedding to 2D for visualization.

        Uses a simple projection (first two principal components approximation).
        In production, would use proper PCA or t-SNE.

        Args:
            embedding: High-dimensional embedding vector.

        Returns:
            Tuple of (x, y) coordinates.
        """
        if not embedding or len(embedding) < 2:
            return (0.0, 0.0)

        # Simple approach: use first two dimensions scaled by later dimensions
        # This is a placeholder - real implementation would use trained PCA
        x = sum(embedding[::2]) / len(embedding) * 2
        y = sum(embedding[1::2]) / len(embedding) * 2

        return (round(x, 4), round(y, 4))

    def _detect_shifts_from_points(
        self,
        points: list[TrajectoryPoint],
    ) -> list[ShiftEvent]:
        """
        Detect semantic shifts from trajectory points.

        Args:
            points: List of trajectory points over time.

        Returns:
            List of detected shift events.
        """
        shifts: list[ShiftEvent] = []

        if len(points) < 2:
            return shifts

        for i in range(1, len(points)):
            prev = points[i - 1]
            curr = points[i]

            # Calculate semantic distance
            distance = self._embedding_distance(
                prev.embedding_full,
                curr.embedding_full,
            )

            # If significant distance, analyze the shift
            if distance > 0.2:  # Threshold for "significant" change
                shift_type = self._classify_shift(
                    prev.definition,
                    curr.definition,
                )

                shifts.append(ShiftEvent(
                    date=curr.date,
                    change_type=shift_type,
                    confidence=min(prev.confidence, curr.confidence),
                    magnitude=min(1.0, distance),
                    before_meaning=prev.definition,
                    after_meaning=curr.definition,
                    evidence=f"Semantic distance: {distance:.3f}",
                ))

        return shifts

    def _classify_shift(
        self,
        before_def: str | None,
        after_def: str | None,
    ) -> str:
        """
        Classify the type of semantic shift based on definitions.

        Args:
            before_def: Definition before the shift.
            after_def: Definition after the shift.

        Returns:
            String identifying the shift type.
        """
        if not before_def or not after_def:
            return "unknown"

        before_lower = before_def.lower()
        after_lower = after_def.lower()

        # Check for each shift type
        scores: dict[str, int] = {}
        for shift_type, indicators in SHIFT_INDICATORS.items():
            before_count = sum(1 for ind in indicators if ind in before_lower)
            after_count = sum(1 for ind in indicators if ind in after_lower)
            scores[shift_type] = after_count - before_count

        # Find the most likely shift type
        if not scores:
            return "general"

        best_type = max(scores, key=scores.get)
        if scores[best_type] > 0:
            return best_type

        # Default based on definition length change (rough heuristic)
        if len(after_def) > len(before_def) * 1.5:
            return "generalization"
        elif len(after_def) < len(before_def) * 0.7:
            return "specialization"

        return "general"

    def _embedding_distance(
        self,
        emb1: list[float],
        emb2: list[float],
    ) -> float:
        """
        Calculate cosine distance between two embeddings.

        Args:
            emb1: First embedding.
            emb2: Second embedding.

        Returns:
            Cosine distance (0 = identical, 1 = orthogonal).
        """
        if not emb1 or not emb2:
            return 0.0

        # Pad to same length
        min_len = min(len(emb1), len(emb2))
        emb1 = emb1[:min_len]
        emb2 = emb2[:min_len]

        # Calculate cosine similarity
        dot_product = sum(a * b for a, b in zip(emb1, emb2))
        norm1 = math.sqrt(sum(a * a for a in emb1))
        norm2 = math.sqrt(sum(b * b for b in emb2))

        if norm1 == 0 or norm2 == 0:
            return 0.0

        similarity = dot_product / (norm1 * norm2)

        # Convert to distance (0-1 range)
        return max(0.0, min(1.0, 1.0 - similarity))

    def _calculate_total_drift(
        self,
        embeddings: list[list[float]],
    ) -> float:
        """
        Calculate total semantic drift (cumulative distance traveled).

        Args:
            embeddings: List of embeddings over time.

        Returns:
            Total drift value.
        """
        if len(embeddings) < 2:
            return 0.0

        total = 0.0
        for i in range(1, len(embeddings)):
            total += self._embedding_distance(embeddings[i - 1], embeddings[i])

        return total

    def _calculate_stability(
        self,
        points: list[TrajectoryPoint],
        shifts: list[ShiftEvent],
    ) -> float:
        """
        Calculate stability score for a trajectory.

        Args:
            points: Trajectory points.
            shifts: Detected shift events.

        Returns:
            Stability score (1 = very stable, 0 = highly volatile).
        """
        if len(points) < 2:
            return 1.0

        # Factors affecting stability:
        # 1. Number of significant shifts
        # 2. Magnitude of shifts
        # 3. Time span covered

        shift_penalty = sum(s.magnitude for s in shifts) / len(points)
        stability = max(0.0, 1.0 - shift_penalty)

        return stability

    def _calculate_trajectory_divergence(
        self,
        traj1: SemanticTrajectory,
        traj2: SemanticTrajectory,
    ) -> float:
        """
        Calculate divergence between two trajectories.

        Args:
            traj1: First trajectory.
            traj2: Second trajectory.

        Returns:
            Divergence score (0 = identical, 1 = completely different).
        """
        if not traj1.points or not traj2.points:
            return 1.0

        # Compare at overlapping time points
        # Get the final embeddings for each
        emb1 = traj1.points[-1].embedding_full if traj1.points else []
        emb2 = traj2.points[-1].embedding_full if traj2.points else []

        current_divergence = self._embedding_distance(emb1, emb2)

        # Also consider drift patterns
        drift_diff = abs(traj1.total_drift - traj2.total_drift)

        # Combined score
        return (current_divergence * 0.7 + min(1.0, drift_diff) * 0.3)


# Convenience functions for API use
def get_semantic_trajectory(
    form: str,
    language: str,
    lsr_data: dict[str, list[dict[str, Any]]] | None = None,
) -> SemanticTrajectory | None:
    """
    Get the semantic trajectory for a word.

    Args:
        form: The word form.
        language: ISO 639-3 language code.
        lsr_data: Optional LSR data dictionary.

    Returns:
        SemanticTrajectory or None.
    """
    analyzer = SemanticDriftAnalyzer(lsr_data=lsr_data)
    return analyzer.get_trajectory(form, language)


def detect_semantic_shifts(
    form: str,
    language: str,
    threshold: float = 0.3,
    lsr_data: dict[str, list[dict[str, Any]]] | None = None,
) -> list[ShiftEvent]:
    """
    Detect semantic shifts for a word.

    Args:
        form: The word form.
        language: ISO 639-3 language code.
        threshold: Minimum magnitude threshold.
        lsr_data: Optional LSR data dictionary.

    Returns:
        List of ShiftEvent objects.
    """
    analyzer = SemanticDriftAnalyzer(lsr_data=lsr_data)
    return analyzer.detect_shifts(form, language, threshold)
