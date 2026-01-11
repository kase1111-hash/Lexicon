"""OCR adapter for digitized manuscripts and historical documents."""

from datetime import datetime
from typing import Iterator

from .base import RawLexicalEntry, SourceAdapter


class OCRAdapter(SourceAdapter):
    """Adapter for OCR processing of digitized manuscripts."""

    def __init__(
        self,
        ocr_engine: str = "tesseract",
        min_ocr_confidence: float = 0.7,
        min_word_confidence: float = 0.8,
        flag_for_review_below: float = 0.9,
    ):
        self.ocr_engine = ocr_engine
        self.min_ocr_confidence = min_ocr_confidence
        self.min_word_confidence = min_word_confidence
        self.flag_for_review_below = flag_for_review_below
        self._connection = None

    def connect(self) -> None:
        """Initialize OCR engine."""
        # TODO: Implement OCR engine initialization
        pass

    def fetch_batch(self, offset: int, limit: int) -> Iterator[RawLexicalEntry]:
        """Fetch a batch of OCR-processed entries."""
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
