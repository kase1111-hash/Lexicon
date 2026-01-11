"""Language model."""

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID, uuid4


@dataclass
class Language:
    """Represents a language in the system."""

    id: UUID = field(default_factory=uuid4)
    iso_code: str = ""  # ISO 639-3
    name: str = ""
    family: str = ""
    branch_path: list[str] = field(default_factory=list)
    is_living: bool = True
    is_reconstructed: bool = False
    speaker_count: int | None = None
    geographic_region: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)


@dataclass
class ContactEvent:
    """Represents a historical contact event between languages."""

    id: UUID = field(default_factory=uuid4)
    donor_language_id: UUID | None = None
    recipient_language_id: UUID | None = None
    date_start: int | None = None
    date_end: int | None = None
    contact_type: str = ""  # conquest, trade, religious, etc.
    vocabulary_count: int = 0
    confidence: float = 1.0
    detected_by: str = "automated"  # 'automated' or 'manual'
    validated: bool = False
    notes: str = ""
