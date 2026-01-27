"""Lexical State Record (LSR) model - core data unit for the linguistic graph."""

from datetime import datetime
from enum import Enum
from typing import Self
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator, model_validator

from src.utils.phonetics import PhoneticUtils


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


class Attestation(BaseModel):
    """A recorded usage of a word form in a historical text."""

    id: UUID = Field(default_factory=uuid4)
    lsr_id: UUID | None = None
    text_excerpt: str = ""
    text_source: str = ""
    text_date: int | None = Field(
        default=None,
        ge=-10000,
        le=2100,
        description="Attestation year (negative for BCE, must be between -10000 and 2100)",
    )
    text_date_confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    page_reference: str = ""
    url: str | None = None

    model_config = {"frozen": False, "extra": "forbid"}


class LSR(BaseModel):
    """
    Lexical State Record - the atomic unit representing a word at a specific point in time.

    This is the core data structure for the linguistic stratigraphy graph. Each LSR
    captures a snapshot of a form-meaning pairing within a specific language and time period.
    """

    # Identity
    id: UUID = Field(default_factory=uuid4)
    version: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)

    # Form
    form_orthographic: str = Field(default="", description="Written form")
    form_phonetic: str = Field(default="", description="IPA representation")
    form_normalized: str = Field(default="", description="Normalized for matching")

    # Language & Time
    language_code: str = Field(default="", description="ISO 639-3 code")
    language_name: str = Field(default="", description="Human-readable name")
    language_family: str = Field(default="", description="Top-level family")
    language_branch: list[str] = Field(default_factory=list, description="Full lineage path")
    period_label: str = Field(default="", description="e.g., 'Middle English'")
    date_start: int | None = Field(
        default=None,
        ge=-10000,
        le=2100,
        description="Start year (negative for BCE, must be between -10000 and 2100)",
    )
    date_end: int | None = Field(
        default=None,
        ge=-10000,
        le=2100,
        description="End year (must be between -10000 and 2100)",
    )
    date_confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    date_source: DateSource = Field(default=DateSource.ATTESTED)

    # Semantics
    semantic_vector: list[float] = Field(
        default_factory=list, description="Embedding (384 dimensions)"
    )
    semantic_fields: list[str] = Field(default_factory=list, description="WordNet synset IDs")
    definition_primary: str = Field(default="", description="Main gloss")
    definitions_alternate: list[str] = Field(default_factory=list)
    conceptual_domain: list[str] = Field(default_factory=list, description="High-level categories")

    # Usage
    register: Register | None = None
    frequency_score: float = Field(default=0.0, ge=0.0, le=1.0)
    frequency_source: str = ""
    part_of_speech: list[str] = Field(default_factory=list)

    # Evidence
    attestations: list[Attestation] = Field(default_factory=list)

    # Relationships (stored as graph edges, referenced here for queries)
    ancestor_ids: list[UUID] = Field(default_factory=list)
    descendant_ids: list[UUID] = Field(default_factory=list)
    cognate_ids: list[UUID] = Field(default_factory=list)
    loan_source_id: UUID | None = None
    loan_target_ids: list[UUID] = Field(default_factory=list)

    # Metadata
    reconstruction_flag: bool = Field(default=False, description="Is this a *starred proto-form?")
    confidence_overall: float = Field(default=1.0, ge=0.0, le=1.0)
    source_databases: list[str] = Field(default_factory=list, description="Data provenance")
    human_validated: bool = False
    validation_notes: str = ""

    model_config = {"frozen": False, "extra": "forbid"}

    @field_validator("date_end")
    @classmethod
    def validate_date_range(cls, v: int | None, info) -> int | None:
        """Ensure date_end >= date_start if both are set."""
        if v is not None and info.data.get("date_start") is not None:
            if v < info.data["date_start"]:
                raise ValueError("date_end must be >= date_start")
        return v

    @model_validator(mode="after")
    def auto_normalize_form(self) -> Self:
        """Automatically normalize the form if not already set."""
        if self.form_orthographic and not self.form_normalized:
            self.form_normalized = self._normalize(self.form_orthographic)
        return self

    @staticmethod
    def _normalize(text: str) -> str:
        """Normalize text for matching: lowercase and strip diacritics."""
        return PhoneticUtils.strip_diacritics(text.lower())

    def normalize_form(self) -> None:
        """Explicitly normalize the orthographic form."""
        self.form_normalized = self._normalize(self.form_orthographic)

    def add_attestation(self, attestation: Attestation) -> None:
        """Add an attestation and link it to this LSR."""
        attestation.lsr_id = self.id
        self.attestations.append(attestation)
        self._update_dates_from_attestations()

    def _update_dates_from_attestations(self) -> None:
        """Update date range based on attestations."""
        dated_attestations = [a for a in self.attestations if a.text_date is not None]
        if dated_attestations:
            dates = [a.text_date for a in dated_attestations if a.text_date is not None]
            if self.date_start is None or min(dates) < self.date_start:
                self.date_start = min(dates)
            if self.date_end is None or max(dates) > self.date_end:
                self.date_end = max(dates)

    def update_confidence(self) -> None:
        """Recalculate overall confidence based on various factors."""
        factors = []

        # Date confidence
        factors.append(self.date_confidence)

        # Attestation factor
        if self.attestations:
            factors.append(min(1.0, len(self.attestations) * 0.1 + 0.5))
        else:
            factors.append(0.3)

        # Reconstruction penalty
        if self.reconstruction_flag:
            factors.append(0.6)

        # Human validation bonus
        if self.human_validated:
            factors.append(1.0)

        self.confidence_overall = sum(factors) / len(factors)

    def merge_with(self, other: "LSR") -> None:
        """
        Merge another LSR into this one.

        Used during entity resolution when two records are determined to be the same.
        """
        # Combine attestations (union)
        existing_ids = {a.id for a in self.attestations}
        for att in other.attestations:
            if att.id not in existing_ids:
                self.attestations.append(att)

        # Expand date range
        if other.date_start is not None:
            if self.date_start is None or other.date_start < self.date_start:
                self.date_start = other.date_start
        if other.date_end is not None:
            if self.date_end is None or other.date_end > self.date_end:
                self.date_end = other.date_end

        # Combine source databases
        self.source_databases = list(set(self.source_databases + other.source_databases))

        # Combine alternate definitions
        existing_defs = set(self.definitions_alternate)
        for defn in other.definitions_alternate:
            if defn not in existing_defs and defn != self.definition_primary:
                self.definitions_alternate.append(defn)

        # Update version and timestamp
        self.version += 1
        self.updated_at = datetime.now()

        # Recalculate confidence
        self.update_confidence()

    def to_graph_node(self) -> dict:
        """Convert to a dictionary suitable for graph database insertion."""
        return {
            "id": str(self.id),
            "form": self.form_orthographic,
            "form_normalized": self.form_normalized,
            "language_code": self.language_code,
            "period_label": self.period_label,
            "date_start": self.date_start,
            "date_end": self.date_end,
            "definition": self.definition_primary,
            "confidence": self.confidence_overall,
            "reconstruction": self.reconstruction_flag,
        }

    def to_search_document(self) -> dict:
        """Convert to a dictionary suitable for Elasticsearch indexing."""
        return {
            "id": str(self.id),
            "form_orthographic": self.form_orthographic,
            "form_normalized": self.form_normalized,
            "form_phonetic": self.form_phonetic,
            "language_code": self.language_code,
            "language_name": self.language_name,
            "language_family": self.language_family,
            "period_label": self.period_label,
            "date_start": self.date_start,
            "date_end": self.date_end,
            "definition_primary": self.definition_primary,
            "definitions_alternate": self.definitions_alternate,
            "part_of_speech": self.part_of_speech,
            "semantic_fields": self.semantic_fields,
            "confidence": self.confidence_overall,
        }
