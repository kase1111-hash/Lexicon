"""GraphQL schema definition using Strawberry."""

from typing import Optional

import strawberry


@strawberry.type
class Attestation:
    """A recorded usage of a word form."""

    text: str
    source: str
    date: Optional[int]
    url: Optional[str]


@strawberry.type
class Language:
    """A language in the system."""

    iso_code: str
    name: str
    family: Optional[str]
    branch_path: list[str]
    is_living: bool


@strawberry.type
class SemanticField:
    """A semantic field/domain."""

    synset_id: str
    label: str
    domain: Optional[str]


@strawberry.type
class TrajectoryPoint:
    """A point in a semantic trajectory."""

    date: int
    embedding_2d: list[float]
    definition: Optional[str]
    attestation_count: int


@strawberry.type
class ShiftEvent:
    """A semantic shift event."""

    date: int
    change_type: str
    confidence: float
    before_meaning: Optional[str]
    after_meaning: Optional[str]


@strawberry.type
class SemanticTrajectory:
    """Semantic evolution trajectory."""

    points: list[TrajectoryPoint]
    shift_events: list[ShiftEvent]


@strawberry.type
class LSR:
    """Lexical State Record."""

    id: strawberry.ID
    form: str
    form_phonetic: Optional[str]
    language: Language
    date_start: Optional[int]
    date_end: Optional[int]
    definitions: list[str]
    confidence: float
    is_reconstructed: bool
    attestations: list[Attestation]


@strawberry.type
class DiagnosticWord:
    """A word that contributed to date prediction."""

    form: str
    date_contribution: float
    earliest_attestation: Optional[int]


@strawberry.type
class DateAnalysis:
    """Result of text dating analysis."""

    predicted_range: list[int]
    confidence: float
    diagnostic_vocabulary: list[DiagnosticWord]


@strawberry.type
class Anachronism:
    """A detected anachronism."""

    form: str
    earliest_attestation: int
    severity: str


@strawberry.type
class AnachronismAnalysis:
    """Result of anachronism analysis."""

    anachronisms: list[Anachronism]
    verdict: str


@strawberry.type
class Query:
    """Root query type."""

    @strawberry.field
    def lsr(self, id: strawberry.ID) -> Optional[LSR]:
        """Get an LSR by ID."""
        # TODO: Implement resolver
        return None

    @strawberry.field
    def search_lsr(
        self,
        form: Optional[str] = None,
        language: Optional[str] = None,
        date_start: Optional[int] = None,
        date_end: Optional[int] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[LSR]:
        """Search for LSRs."""
        # TODO: Implement resolver
        return []

    @strawberry.field
    def language(self, iso_code: str) -> Optional[Language]:
        """Get a language by ISO code."""
        # TODO: Implement resolver
        return None

    @strawberry.field
    def languages(self, family: Optional[str] = None) -> list[Language]:
        """Get all languages, optionally filtered by family."""
        # TODO: Implement resolver
        return []

    @strawberry.field
    def date_text(self, text: str, language: str) -> DateAnalysis:
        """Analyze text to predict its date."""
        # TODO: Implement resolver
        return DateAnalysis(
            predicted_range=[0, 0],
            confidence=0.0,
            diagnostic_vocabulary=[],
        )

    @strawberry.field
    def detect_anachronisms(
        self, text: str, claimed_date: int, language: str
    ) -> AnachronismAnalysis:
        """Detect anachronisms in text."""
        # TODO: Implement resolver
        return AnachronismAnalysis(anachronisms=[], verdict="consistent")


schema = strawberry.Schema(query=Query)
