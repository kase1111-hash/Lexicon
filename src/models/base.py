"""Base models and mixins for reusable components."""

from datetime import datetime
from typing import Any, Self
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, model_validator


class TimestampMixin(BaseModel):
    """Mixin for models that need created/updated timestamps."""

    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)

    def touch(self) -> None:
        """Update the updated_at timestamp."""
        self.updated_at = datetime.now()


class VersionedMixin(BaseModel):
    """Mixin for models that need version tracking."""

    version: int = Field(default=1, ge=1)

    def increment_version(self) -> None:
        """Increment the version number."""
        self.version += 1


class IdentifiableMixin(BaseModel):
    """Mixin for models with UUID identifiers."""

    id: UUID = Field(default_factory=uuid4)


class ConfidenceMixin(BaseModel):
    """Mixin for models with confidence scores."""

    confidence: float = Field(default=1.0, ge=0.0, le=1.0)

    def adjust_confidence(self, factor: float) -> None:
        """Adjust confidence by a factor, clamping to [0, 1]."""
        self.confidence = max(0.0, min(1.0, self.confidence * factor))


class BaseEntity(IdentifiableMixin, TimestampMixin, VersionedMixin):
    """Base class for all entity models with common fields."""

    model_config = {"frozen": False, "extra": "forbid"}

    def update(self, **kwargs: Any) -> None:
        """Update fields and touch timestamp."""
        for key, value in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, value)
        self.touch()
        self.increment_version()


class DateRangeMixin(BaseModel):
    """Mixin for models with date ranges."""

    date_start: int | None = Field(default=None, description="Start year (negative for BCE)")
    date_end: int | None = Field(default=None, description="End year")

    @model_validator(mode="after")
    def validate_date_range(self) -> Self:
        """Ensure date_end >= date_start if both are set."""
        if self.date_start is not None and self.date_end is not None:
            if self.date_end < self.date_start:
                raise ValueError("date_end must be >= date_start")
        return self

    def expand_date_range(self, start: int | None, end: int | None) -> None:
        """Expand the date range to include new dates."""
        if start is not None:
            if self.date_start is None or start < self.date_start:
                self.date_start = start
        if end is not None:
            if self.date_end is None or end > self.date_end:
                self.date_end = end

    def dates_overlap(self, other_start: int | None, other_end: int | None) -> bool:
        """Check if this date range overlaps with another."""
        if self.date_start is None or self.date_end is None:
            return True  # Unknown dates are considered overlapping
        if other_start is None or other_end is None:
            return True

        return self.date_start <= other_end and other_start <= self.date_end


class SourceTrackingMixin(BaseModel):
    """Mixin for models that track their data sources."""

    source_databases: list[str] = Field(default_factory=list, description="Data provenance")

    def add_source(self, source: str) -> None:
        """Add a source database if not already present."""
        if source not in self.source_databases:
            self.source_databases.append(source)

    def merge_sources(self, other_sources: list[str]) -> None:
        """Merge sources from another record."""
        for source in other_sources:
            self.add_source(source)


class ValidationMixin(BaseModel):
    """Mixin for models that can be validated by humans."""

    human_validated: bool = False
    validation_notes: str = ""

    def mark_validated(self, notes: str = "") -> None:
        """Mark as validated with optional notes."""
        self.human_validated = True
        if notes:
            self.validation_notes = notes


# Common response models for API
class PaginatedResponse(BaseModel):
    """Standard paginated response wrapper."""

    items: list[Any]
    total: int
    page: int
    page_size: int
    has_next: bool
    has_prev: bool

    @classmethod
    def create(
        cls, items: list[Any], total: int, page: int, page_size: int
    ) -> "PaginatedResponse":
        """Create a paginated response."""
        return cls(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
            has_next=(page * page_size) < total,
            has_prev=page > 1,
        )


class ErrorResponse(BaseModel):
    """Standard error response."""

    error: str
    detail: str | None = None
    code: str | None = None


class SuccessResponse(BaseModel):
    """Standard success response."""

    success: bool = True
    message: str = "Operation completed successfully"
    data: dict[str, Any] | None = None
