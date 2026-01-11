"""Relationship/Edge models for the graph."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4


class RelationshipType(str, Enum):
    """Types of relationships between LSRs."""

    DESCENDS_FROM = "DESCENDS_FROM"
    BORROWED_FROM = "BORROWED_FROM"
    COGNATE_OF = "COGNATE_OF"
    SHIFTED_TO = "SHIFTED_TO"
    MERGED_WITH = "MERGED_WITH"


@dataclass
class Edge:
    """Represents a relationship between two LSRs in the graph."""

    id: UUID = field(default_factory=uuid4)
    source_id: UUID | None = None
    target_id: UUID | None = None
    relationship_type: RelationshipType = RelationshipType.DESCENDS_FROM
    confidence: float = 1.0
    date_of_change: int | None = None
    change_type: str | None = None  # For SHIFTED_TO: metaphor, metonymy, etc.
    evidence: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
