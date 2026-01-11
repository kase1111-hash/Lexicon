"""Linguistic Stratigraphy - Automated Cross-Linguistic Lexical Evolution Graph."""

__version__ = "0.1.0"

from src.config import (
    Settings,
    get_api_config,
    get_database_config,
    get_error_tracking_config,
    get_logging_config,
    get_settings,
    is_debug,
    is_production,
    reload_settings,
)
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
    # Configuration
    "Settings",
    "get_settings",
    "reload_settings",
    "get_database_config",
    "get_api_config",
    "get_logging_config",
    "get_error_tracking_config",
    "is_production",
    "is_debug",
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
