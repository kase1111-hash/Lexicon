"""Abstract base class for all source adapters."""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Iterator

from pydantic import BaseModel, Field


class RawLexicalEntry(BaseModel):
    """
    Intermediate format between source data and LSR.

    This serves as a normalized representation that all adapters produce,
    which is then processed by the ingestion pipeline to create LSRs.
    """

    source_id: str = Field(..., description="Unique ID within the source")
    source_name: str = Field(..., description="Name of the data source")
    form: str = Field(..., description="Word form (orthographic)")
    form_phonetic: str = Field(default="", description="IPA pronunciation")
    language: str = Field(..., description="Language name or ISO code")
    language_code: str = Field(default="", description="ISO 639-3 code")
    etymology: str | None = Field(default=None, description="Etymology text")
    definitions: list[str] = Field(default_factory=list)
    part_of_speech: list[str] = Field(default_factory=list)
    attestations: list[dict[str, Any]] = Field(default_factory=list)
    related_forms: list[dict[str, Any]] = Field(default_factory=list)
    date_attested: int | None = Field(default=None, description="Earliest attestation year")
    raw_data: dict[str, Any] = Field(default_factory=dict, description="Original source data")

    model_config = {"extra": "allow"}

    def to_source_key(self) -> str:
        """Generate a unique key for this entry within its source."""
        return f"{self.source_name}:{self.language}:{self.form}"


class SourceAdapter(ABC):
    """
    Abstract interface for all data source adapters.

    Each source (Wiktionary, CLLD, historical corpora, etc.) implements this
    interface to provide a uniform way to ingest lexical data.
    """

    def __init__(self, config: dict[str, Any] | None = None):
        """Initialize the adapter with optional configuration."""
        self.config = config or {}
        self._connected = False

    @property
    def name(self) -> str:
        """Return the adapter/source name."""
        return self.__class__.__name__.replace("Adapter", "")

    @abstractmethod
    def connect(self) -> None:
        """
        Establish connection to data source.

        Raises:
            ConnectionError: If connection cannot be established.
        """
        pass

    @abstractmethod
    def disconnect(self) -> None:
        """Close connection to data source."""
        pass

    @abstractmethod
    def fetch_batch(self, offset: int, limit: int) -> Iterator[RawLexicalEntry]:
        """
        Fetch a batch of entries from the source.

        Args:
            offset: Number of entries to skip.
            limit: Maximum number of entries to return.

        Yields:
            RawLexicalEntry objects.
        """
        pass

    @abstractmethod
    def get_total_count(self) -> int:
        """Return total number of available entries."""
        pass

    @abstractmethod
    def get_last_modified(self) -> datetime:
        """Return last modification timestamp for incremental updates."""
        pass

    @abstractmethod
    def supports_incremental(self) -> bool:
        """Whether source supports delta/incremental updates."""
        pass

    def fetch_all(self, batch_size: int = 1000) -> Iterator[RawLexicalEntry]:
        """
        Fetch all entries from the source in batches.

        Args:
            batch_size: Number of entries per batch.

        Yields:
            RawLexicalEntry objects.
        """
        offset = 0
        total = self.get_total_count()

        while offset < total:
            for entry in self.fetch_batch(offset, batch_size):
                yield entry
            offset += batch_size

    def fetch_incremental(self, since: datetime) -> Iterator[RawLexicalEntry]:
        """
        Fetch entries modified since a given timestamp.

        Args:
            since: Fetch entries modified after this timestamp.

        Yields:
            RawLexicalEntry objects.

        Raises:
            NotImplementedError: If source doesn't support incremental updates.
        """
        if not self.supports_incremental():
            raise NotImplementedError(f"{self.name} does not support incremental updates")
        # Subclasses should override this method
        raise NotImplementedError

    def validate_entry(self, entry: RawLexicalEntry) -> list[str]:
        """
        Validate an entry and return list of issues.

        Args:
            entry: The entry to validate.

        Returns:
            List of validation error messages (empty if valid).
        """
        issues = []

        if not entry.form:
            issues.append("Missing form")
        if not entry.language:
            issues.append("Missing language")
        if not entry.definitions:
            issues.append("No definitions provided")

        return issues

    def __enter__(self) -> "SourceAdapter":
        """Context manager entry."""
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit."""
        self.disconnect()
