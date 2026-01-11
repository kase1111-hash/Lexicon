"""Abstract base class for all source adapters."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Iterator


@dataclass
class RawLexicalEntry:
    """Intermediate format between source and LSR."""

    source_id: str
    source_name: str
    form: str
    language: str
    etymology: str | None
    definitions: list[str]
    attestations: list[dict]
    related_forms: list[dict]
    raw_data: dict  # Original source data for debugging


class SourceAdapter(ABC):
    """Abstract interface for all source adapters."""

    @abstractmethod
    def connect(self) -> None:
        """Establish connection to data source."""
        pass

    @abstractmethod
    def fetch_batch(self, offset: int, limit: int) -> Iterator[RawLexicalEntry]:
        """Fetch a batch of entries."""
        pass

    @abstractmethod
    def get_total_count(self) -> int:
        """Return total available entries."""
        pass

    @abstractmethod
    def get_last_modified(self) -> datetime:
        """Return last modification timestamp for incremental updates."""
        pass

    @abstractmethod
    def supports_incremental(self) -> bool:
        """Whether source supports delta updates."""
        pass
