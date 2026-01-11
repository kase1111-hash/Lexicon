"""Semantic drift analysis over time."""

from dataclasses import dataclass
from uuid import UUID


@dataclass
class TrajectoryPoint:
    """A point in a semantic trajectory."""

    date: int
    embedding_2d: tuple[float, float]
    definition: str | None
    attestation_count: int


@dataclass
class ShiftEvent:
    """A detected semantic shift event."""

    date: int
    change_type: str  # metaphor, metonymy, generalization, specialization, etc.
    confidence: float
    before_meaning: str | None
    after_meaning: str | None


@dataclass
class SemanticTrajectory:
    """The semantic evolution of a word over time."""

    lsr_id: UUID
    form: str
    language: str
    points: list[TrajectoryPoint]
    shift_events: list[ShiftEvent]


class SemanticDriftAnalyzer:
    """Analyze semantic drift of words over time."""

    def __init__(self):
        pass

    def get_trajectory(self, form: str, language: str) -> SemanticTrajectory | None:
        """Get the semantic trajectory of a word."""
        # TODO: Implement trajectory retrieval
        return None

    def detect_shifts(self, lsr_id: UUID) -> list[ShiftEvent]:
        """Detect semantic shift events for an LSR."""
        # TODO: Implement shift detection
        return []

    def compare_trajectories(
        self, form: str, languages: list[str]
    ) -> dict:
        """Compare semantic trajectories across languages."""
        # TODO: Implement cross-lingual trajectory comparison
        return {}
