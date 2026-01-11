"""Linguistic Stratigraphy - Automated Cross-Linguistic Lexical Evolution Graph."""

__version__ = "0.1.0"

from src.exceptions import (
    AnalysisError,
    AuthenticationError,
    AuthorizationError,
    ConfigurationError,
    DatabaseError,
    DuplicateError,
    EmbeddingError,
    EntityResolutionError,
    ExternalServiceError,
    IngestionError,
    InsufficientDataError,
    InvalidDateRangeError,
    InvalidLanguageCodeError,
    LanguageNotFoundError,
    LexiconError,
    LSRNotFoundError,
    NotFoundError,
    PipelineError,
    RateLimitError,
    ValidationError,
)

__all__ = [
    "__version__",
    # Base exception
    "LexiconError",
    # Client errors (4xx)
    "NotFoundError",
    "LSRNotFoundError",
    "LanguageNotFoundError",
    "ValidationError",
    "InvalidDateRangeError",
    "InvalidLanguageCodeError",
    "DuplicateError",
    "RateLimitError",
    "AuthenticationError",
    "AuthorizationError",
    # Database errors (5xx)
    "DatabaseError",
    # Pipeline errors
    "PipelineError",
    "IngestionError",
    "EntityResolutionError",
    "EmbeddingError",
    # External service errors
    "ExternalServiceError",
    # Analysis errors
    "AnalysisError",
    "InsufficientDataError",
    # Configuration errors
    "ConfigurationError",
]
