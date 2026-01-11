"""Base classes for processing pipelines."""

import logging
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field


logger = logging.getLogger(__name__)

# Type variables for generic pipeline
InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")


class PipelineResult(BaseModel):
    """Base result model for all pipelines."""

    success: bool = True
    processed_count: int = 0
    failed_count: int = 0
    errors: list[str] = Field(default_factory=list)
    duration_seconds: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)


class PipelineStats(BaseModel):
    """Statistics for pipeline execution."""

    total_processed: int = 0
    total_succeeded: int = 0
    total_failed: int = 0
    total_skipped: int = 0
    start_time: datetime | None = None
    end_time: datetime | None = None
    items_per_second: float = 0.0

    def update(self, succeeded: int = 0, failed: int = 0, skipped: int = 0) -> None:
        """Update statistics."""
        self.total_processed += succeeded + failed + skipped
        self.total_succeeded += succeeded
        self.total_failed += failed
        self.total_skipped += skipped

    def finalize(self) -> None:
        """Finalize statistics and calculate rates."""
        if self.start_time and self.end_time:
            duration = (self.end_time - self.start_time).total_seconds()
            if duration > 0:
                self.items_per_second = self.total_processed / duration


class BasePipeline(ABC, Generic[InputT, OutputT]):
    """
    Abstract base class for all processing pipelines.

    Provides common functionality for:
    - Batch processing with progress tracking
    - Error handling and logging
    - Statistics collection
    - Configuration management
    """

    def __init__(self, name: str | None = None, batch_size: int = 100):
        """
        Initialize the pipeline.

        Args:
            name: Pipeline name for logging.
            batch_size: Default batch size for processing.
        """
        self.name = name or self.__class__.__name__
        self.batch_size = batch_size
        self.stats = PipelineStats()
        self._logger = logging.getLogger(f"pipeline.{self.name}")

    @abstractmethod
    def process_single(self, item: InputT) -> OutputT:
        """
        Process a single item.

        Args:
            item: The item to process.

        Returns:
            The processed result.
        """
        pass

    def process_batch(self, items: list[InputT]) -> list[OutputT]:
        """
        Process a batch of items.

        Args:
            items: List of items to process.

        Returns:
            List of processed results.
        """
        results: list[OutputT] = []
        for item in items:
            try:
                result = self.process_single(item)
                results.append(result)
                self.stats.update(succeeded=1)
            except Exception as e:
                self._logger.error(f"Error processing item: {e}")
                self.stats.update(failed=1)
        return results

    def run(self, items: list[InputT]) -> PipelineResult:
        """
        Run the pipeline on a list of items.

        Args:
            items: List of items to process.

        Returns:
            PipelineResult with statistics and any errors.
        """
        self.stats = PipelineStats()
        self.stats.start_time = datetime.now()
        errors: list[str] = []

        self._logger.info(f"Starting {self.name} pipeline with {len(items)} items")

        try:
            # Process in batches
            for i in range(0, len(items), self.batch_size):
                batch = items[i : i + self.batch_size]
                try:
                    self.process_batch(batch)
                except Exception as e:
                    error_msg = f"Batch {i // self.batch_size} failed: {e}"
                    self._logger.error(error_msg)
                    errors.append(error_msg)

        finally:
            self.stats.end_time = datetime.now()
            self.stats.finalize()

        self._logger.info(
            f"Completed {self.name} pipeline: "
            f"{self.stats.total_succeeded} succeeded, "
            f"{self.stats.total_failed} failed, "
            f"{self.stats.items_per_second:.2f} items/sec"
        )

        return PipelineResult(
            success=self.stats.total_failed == 0,
            processed_count=self.stats.total_succeeded,
            failed_count=self.stats.total_failed,
            errors=errors,
            duration_seconds=(self.stats.end_time - self.stats.start_time).total_seconds()
            if self.stats.start_time and self.stats.end_time
            else 0.0,
        )

    def validate_input(self, item: InputT) -> list[str]:
        """
        Validate an input item before processing.

        Override in subclasses to add validation logic.

        Args:
            item: The item to validate.

        Returns:
            List of validation error messages (empty if valid).
        """
        return []

    def pre_process(self) -> None:
        """Hook called before processing starts. Override to add setup logic."""
        pass

    def post_process(self) -> None:
        """Hook called after processing completes. Override to add cleanup logic."""
        pass
