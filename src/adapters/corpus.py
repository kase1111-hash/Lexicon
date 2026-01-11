"""Corpus adapter for historical text corpora."""

from collections.abc import Iterator
from datetime import datetime

from .base import RawLexicalEntry, SourceAdapter


class CorpusAdapter(SourceAdapter):
    """Adapter for historical text corpora (COHA, EEBO, etc.)."""

    def __init__(
        self,
        corpus_type: str = "gutenberg",
        metadata_source: str | None = None,
    ):
        self.corpus_type = corpus_type
        self.metadata_source = metadata_source
        self._connection = None

    def connect(self) -> None:
        """Establish connection to corpus data source."""
        # TODO: Implement connection logic
        pass

    def fetch_batch(self, offset: int, limit: int) -> Iterator[RawLexicalEntry]:
        """Fetch a batch of entries from corpus."""
        # TODO: Implement batch fetching
        return iter([])

    def get_total_count(self) -> int:
        """Return total available entries."""
        # TODO: Implement count logic
        return 0

    def get_last_modified(self) -> datetime:
        """Return last modification timestamp for incremental updates."""
        # TODO: Implement last modified check
        return datetime.now()

    def supports_incremental(self) -> bool:
        """Whether source supports delta updates."""
        return False
