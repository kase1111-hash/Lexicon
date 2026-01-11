"""Relationship/Edge models for the linguistic graph."""

from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class RelationshipType(str, Enum):
    """Types of relationships between LSRs."""

    DESCENDS_FROM = "DESCENDS_FROM"  # Vertical inheritance within language lineage
    BORROWED_FROM = "BORROWED_FROM"  # Horizontal transfer (loanword)
    COGNATE_OF = "COGNATE_OF"  # Shared ancestry across languages (symmetric)
    SHIFTED_TO = "SHIFTED_TO"  # Semantic change within same language
    MERGED_WITH = "MERGED_WITH"  # Convergence of multiple forms


class ChangeType(str, Enum):
    """Types of semantic/phonological changes."""

    # Semantic changes
    METAPHOR = "METAPHOR"
    METONYMY = "METONYMY"
    GENERALIZATION = "GENERALIZATION"
    SPECIALIZATION = "SPECIALIZATION"
    AMELIORATION = "AMELIORATION"
    PEJORATION = "PEJORATION"

    # Phonological changes
    SOUND_CHANGE = "SOUND_CHANGE"
    ASSIMILATION = "ASSIMILATION"
    DISSIMILATION = "DISSIMILATION"
    LENITION = "LENITION"
    FORTITION = "FORTITION"

    # Other
    UNKNOWN = "UNKNOWN"


class ContactType(str, Enum):
    """Types of language contact situations."""

    CONQUEST = "CONQUEST"
    TRADE = "TRADE"
    RELIGIOUS = "RELIGIOUS"
    TECHNOLOGICAL = "TECHNOLOGICAL"
    CULTURAL = "CULTURAL"
    COLONIAL = "COLONIAL"
    MIGRATION = "MIGRATION"
    UNKNOWN = "UNKNOWN"


class Edge(BaseModel):
    """
    An edge in the linguistic graph representing a relationship between two LSRs.

    Edges encode the evolutionary and contact relationships that form the
    linguistic stratigraphy.
    """

    id: UUID = Field(default_factory=uuid4)
    source_id: UUID = Field(..., description="Source LSR ID")
    target_id: UUID = Field(..., description="Target LSR ID")
    relationship_type: RelationshipType
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    date_of_change: int | None = Field(default=None, description="When the relationship formed")
    change_type: ChangeType | None = Field(default=None, description="For SHIFTED_TO edges")
    contact_type: ContactType | None = Field(default=None, description="For BORROWED_FROM edges")
    evidence: list[str] = Field(default_factory=list, description="Supporting references")
    created_at: datetime = Field(default_factory=datetime.now)
    notes: str = ""

    model_config = {"frozen": False, "extra": "forbid"}

    def to_graph_edge(self) -> dict:
        """Convert to a dictionary for graph database insertion."""
        return {
            "id": str(self.id),
            "source_id": str(self.source_id),
            "target_id": str(self.target_id),
            "type": self.relationship_type.value,
            "confidence": self.confidence,
            "date": self.date_of_change,
            "change_type": self.change_type.value if self.change_type else None,
            "contact_type": self.contact_type.value if self.contact_type else None,
        }

    @classmethod
    def create_descent(
        cls,
        ancestor_id: UUID,
        descendant_id: UUID,
        confidence: float = 1.0,
        date: int | None = None,
    ) -> "Edge":
        """Create a DESCENDS_FROM edge."""
        return cls(
            source_id=descendant_id,
            target_id=ancestor_id,
            relationship_type=RelationshipType.DESCENDS_FROM,
            confidence=confidence,
            date_of_change=date,
        )

    @classmethod
    def create_borrowing(
        cls,
        borrower_id: UUID,
        donor_id: UUID,
        confidence: float = 1.0,
        date: int | None = None,
        contact_type: ContactType = ContactType.UNKNOWN,
    ) -> "Edge":
        """Create a BORROWED_FROM edge."""
        return cls(
            source_id=borrower_id,
            target_id=donor_id,
            relationship_type=RelationshipType.BORROWED_FROM,
            confidence=confidence,
            date_of_change=date,
            contact_type=contact_type,
        )

    @classmethod
    def create_cognate(
        cls,
        lsr1_id: UUID,
        lsr2_id: UUID,
        confidence: float = 1.0,
    ) -> "Edge":
        """Create a COGNATE_OF edge (symmetric)."""
        return cls(
            source_id=lsr1_id,
            target_id=lsr2_id,
            relationship_type=RelationshipType.COGNATE_OF,
            confidence=confidence,
        )

    @classmethod
    def create_semantic_shift(
        cls,
        earlier_id: UUID,
        later_id: UUID,
        change_type: ChangeType,
        confidence: float = 1.0,
        date: int | None = None,
    ) -> "Edge":
        """Create a SHIFTED_TO edge for semantic change."""
        return cls(
            source_id=earlier_id,
            target_id=later_id,
            relationship_type=RelationshipType.SHIFTED_TO,
            confidence=confidence,
            date_of_change=date,
            change_type=change_type,
        )


class ContactEvent(BaseModel):
    """
    A contact event between two languages, aggregating multiple borrowing relationships.

    Contact events represent historical situations where vocabulary transfer occurred
    between languages.
    """

    id: UUID = Field(default_factory=uuid4)
    donor_language_code: str
    recipient_language_code: str
    date_start: int | None = None
    date_end: int | None = None
    contact_type: ContactType = ContactType.UNKNOWN
    vocabulary_count: int = 0
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    detected_by: str = Field(default="automated", description="'automated' or 'manual'")
    validated: bool = False
    notes: str = ""
    sample_words: list[str] = Field(default_factory=list, description="Example borrowed words")
    edge_ids: list[UUID] = Field(default_factory=list, description="Related BORROWED_FROM edges")

    model_config = {"frozen": False, "extra": "forbid"}
