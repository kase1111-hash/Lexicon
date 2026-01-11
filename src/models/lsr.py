"""Lexical State Record (LSR) model."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4


class DateSource(str, Enum):
    """Source of date information."""

    ATTESTED = "ATTESTED"
    INTERPOLATED = "INTERPOLATED"
    RECONSTRUCTED = "RECONSTRUCTED"


class Register(str, Enum):
    """Usage register of a word."""

    FORMAL = "FORMAL"
    COLLOQUIAL = "COLLOQUIAL"
    TECHNICAL = "TECHNICAL"
    SACRED = "SACRED"
    LITERARY = "LITERARY"
    SLANG = "SLANG"


@dataclass
class Attestation:
    """A recorded usage of a word form."""

    id: UUID = field(default_factory=uuid4)
    lsr_id: UUID | None = None
    text_excerpt: str = ""
    text_source: str = ""
    text_date: int | None = None
    text_date_confidence: float = 1.0
    page_reference: str = ""
    url: str | None = None


@dataclass
class LSR:
    """Lexical State Record - core data unit representing a word at a point in time."""

    # Identity
    id: UUID = field(default_factory=uuid4)
    version: int = 1
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    # Form
    form_orthographic: str = ""
    form_phonetic: str = ""
    form_normalized: str = ""

    # Language & Time
    language_code: str = ""  # ISO 639-3
    language_name: str = ""
    language_family: str = ""
    language_branch: list[str] = field(default_factory=list)
    period_label: str = ""
    date_start: int | None = None
    date_end: int | None = None
    date_confidence: float = 1.0
    date_source: DateSource = DateSource.ATTESTED

    # Semantics
    semantic_vector: list[float] = field(default_factory=list)
    semantic_fields: list[str] = field(default_factory=list)  # WordNet synset IDs
    definition_primary: str = ""
    definitions_alternate: list[str] = field(default_factory=list)
    conceptual_domain: list[str] = field(default_factory=list)

    # Usage
    register: Register | None = None
    frequency_score: float = 0.0
    frequency_source: str = ""
    part_of_speech: list[str] = field(default_factory=list)

    # Evidence
    attestations: list[Attestation] = field(default_factory=list)

    # Relationships (stored as graph edges, referenced here for completeness)
    ancestor_ids: list[UUID] = field(default_factory=list)
    descendant_ids: list[UUID] = field(default_factory=list)
    cognate_ids: list[UUID] = field(default_factory=list)
    loan_source_id: UUID | None = None
    loan_target_ids: list[UUID] = field(default_factory=list)

    # Metadata
    reconstruction_flag: bool = False
    confidence_overall: float = 1.0
    source_databases: list[str] = field(default_factory=list)
    human_validated: bool = False
    validation_notes: str = ""

    def normalize_form(self) -> None:
        """Normalize the orthographic form for matching."""
        # TODO: Implement normalization (lowercase, strip diacritics)
        self.form_normalized = self.form_orthographic.lower()
