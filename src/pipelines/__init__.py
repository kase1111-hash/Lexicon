"""Data processing pipelines for entity resolution, relationship extraction, and validation."""

from .base import BasePipeline, PipelineResult, PipelineStats
from .embedding import EmbeddingPipeline
from .entity_resolution import (
    EntityResolver,
    ResolutionAction,
    ResolutionResult,
    SimilarityWeights,
    convert_entry_to_lsr,
)
from .relationship_extraction import RelationshipExtractor
from .validation import Validator

__all__ = [
    # Base
    "BasePipeline",
    "PipelineResult",
    "PipelineStats",
    # Entity Resolution
    "EntityResolver",
    "ResolutionAction",
    "ResolutionResult",
    "SimilarityWeights",
    "convert_entry_to_lsr",
    # Other pipelines
    "RelationshipExtractor",
    "Validator",
    "EmbeddingPipeline",
]
