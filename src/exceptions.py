"""Custom exception classes for Linguistic Stratigraphy system.

This module defines a hierarchy of exceptions for handling various error
conditions throughout the application. All custom exceptions inherit from
LexiconError for easy catching of application-specific errors.
"""

from typing import Any


class LexiconError(Exception):
    """Base exception for all Lexicon application errors.

    Attributes:
        message: Human-readable error description.
        code: Machine-readable error code.
        details: Additional context about the error.
    """

    default_message = "An unexpected error occurred"
    error_code = "LEXICON_ERROR"
    http_status = 500

    def __init__(
        self,
        message: str | None = None,
        code: str | None = None,
        details: dict[str, Any] | None = None,
    ):
        self.message = message or self.default_message
        self.code = code or self.error_code
        self.details = details or {}
        super().__init__(self.message)

    def to_dict(self) -> dict[str, Any]:
        """Convert exception to dictionary for API responses."""
        result = {
            "error": self.code,
            "message": self.message,
        }
        if self.details:
            result["details"] = self.details
        return result


# =============================================================================
# Resource Errors (4xx - Client Errors)
# =============================================================================


class NotFoundError(LexiconError):
    """Raised when a requested resource does not exist."""

    default_message = "Resource not found"
    error_code = "NOT_FOUND"
    http_status = 404

    def __init__(
        self,
        resource_type: str = "Resource",
        resource_id: str | None = None,
        **kwargs: Any,
    ):
        message = f"{resource_type} not found"
        if resource_id:
            message = f"{resource_type} not found: {resource_id}"
        details = {"resource_type": resource_type}
        if resource_id:
            details["resource_id"] = resource_id
        super().__init__(message=message, details=details, **kwargs)


class LSRNotFoundError(NotFoundError):
    """Raised when an LSR record is not found."""

    error_code = "LSR_NOT_FOUND"

    def __init__(self, lsr_id: str | None = None, **kwargs: Any):
        super().__init__(resource_type="LSR", resource_id=lsr_id, **kwargs)


class LanguageNotFoundError(NotFoundError):
    """Raised when a language code is not recognized."""

    error_code = "LANGUAGE_NOT_FOUND"

    def __init__(self, language_code: str | None = None, **kwargs: Any):
        super().__init__(resource_type="Language", resource_id=language_code, **kwargs)


class ValidationError(LexiconError):
    """Raised when input validation fails."""

    default_message = "Validation error"
    error_code = "VALIDATION_ERROR"
    http_status = 400

    def __init__(
        self,
        message: str | None = None,
        field: str | None = None,
        value: Any = None,
        **kwargs: Any,
    ):
        details = kwargs.pop("details", {})
        if field:
            details["field"] = field
        if value is not None:
            details["value"] = str(value)[:100]  # Truncate long values
        super().__init__(message=message, details=details, **kwargs)


class InvalidDateRangeError(ValidationError):
    """Raised when a date range is invalid."""

    error_code = "INVALID_DATE_RANGE"

    def __init__(
        self,
        start_date: int | None = None,
        end_date: int | None = None,
        **kwargs: Any,
    ):
        message = "Invalid date range"
        if start_date is not None and end_date is not None:
            message = f"Invalid date range: {start_date} to {end_date}"
        details = {}
        if start_date is not None:
            details["start_date"] = start_date
        if end_date is not None:
            details["end_date"] = end_date
        super().__init__(message=message, details=details, **kwargs)


class InvalidLanguageCodeError(ValidationError):
    """Raised when a language code format is invalid."""

    error_code = "INVALID_LANGUAGE_CODE"

    def __init__(self, language_code: str | None = None, **kwargs: Any):
        message = "Invalid language code format"
        if language_code:
            message = f"Invalid language code format: {language_code}"
        super().__init__(message=message, field="language_code", value=language_code, **kwargs)


class DuplicateError(LexiconError):
    """Raised when attempting to create a duplicate resource."""

    default_message = "Resource already exists"
    error_code = "DUPLICATE_ERROR"
    http_status = 409

    def __init__(
        self,
        resource_type: str = "Resource",
        identifier: str | None = None,
        **kwargs: Any,
    ):
        message = f"{resource_type} already exists"
        if identifier:
            message = f"{resource_type} already exists: {identifier}"
        details = {"resource_type": resource_type}
        if identifier:
            details["identifier"] = identifier
        super().__init__(message=message, details=details, **kwargs)


class RateLimitError(LexiconError):
    """Raised when rate limit is exceeded."""

    default_message = "Rate limit exceeded"
    error_code = "RATE_LIMIT_EXCEEDED"
    http_status = 429

    def __init__(
        self,
        retry_after: int | None = None,
        **kwargs: Any,
    ):
        details = kwargs.pop("details", {})
        if retry_after:
            details["retry_after_seconds"] = retry_after
        super().__init__(details=details, **kwargs)


class AuthenticationError(LexiconError):
    """Raised when authentication fails."""

    default_message = "Authentication required"
    error_code = "AUTHENTICATION_ERROR"
    http_status = 401


class AuthorizationError(LexiconError):
    """Raised when user lacks permission for an action."""

    default_message = "Permission denied"
    error_code = "AUTHORIZATION_ERROR"
    http_status = 403


# =============================================================================
# Database Errors (5xx - Server Errors)
# =============================================================================


class DatabaseError(LexiconError):
    """Base class for database-related errors."""

    default_message = "Database error"
    error_code = "DATABASE_ERROR"
    http_status = 503


class ConnectionError(DatabaseError):
    """Raised when database connection fails."""

    error_code = "CONNECTION_ERROR"

    def __init__(
        self,
        database: str | None = None,
        **kwargs: Any,
    ):
        message = "Database connection failed"
        if database:
            message = f"Failed to connect to {database}"
        details = {}
        if database:
            details["database"] = database
        super().__init__(message=message, details=details, **kwargs)


class QueryError(DatabaseError):
    """Raised when a database query fails."""

    error_code = "QUERY_ERROR"

    def __init__(
        self,
        query_type: str | None = None,
        **kwargs: Any,
    ):
        message = "Database query failed"
        if query_type:
            message = f"Database query failed: {query_type}"
        details = {}
        if query_type:
            details["query_type"] = query_type
        super().__init__(message=message, details=details, **kwargs)


class TransactionError(DatabaseError):
    """Raised when a database transaction fails."""

    error_code = "TRANSACTION_ERROR"


# =============================================================================
# Pipeline Errors
# =============================================================================


class PipelineError(LexiconError):
    """Base class for pipeline processing errors."""

    default_message = "Pipeline processing error"
    error_code = "PIPELINE_ERROR"
    http_status = 500


class IngestionError(PipelineError):
    """Raised when data ingestion fails."""

    error_code = "INGESTION_ERROR"

    def __init__(
        self,
        source: str | None = None,
        record_id: str | None = None,
        **kwargs: Any,
    ):
        message = "Data ingestion failed"
        if source:
            message = f"Data ingestion failed from {source}"
        details = {}
        if source:
            details["source"] = source
        if record_id:
            details["record_id"] = record_id
        super().__init__(message=message, details=details, **kwargs)


class EntityResolutionError(PipelineError):
    """Raised when entity resolution fails."""

    error_code = "ENTITY_RESOLUTION_ERROR"


class EmbeddingError(PipelineError):
    """Raised when embedding generation fails."""

    error_code = "EMBEDDING_ERROR"


# =============================================================================
# External Service Errors
# =============================================================================


class ExternalServiceError(LexiconError):
    """Base class for external service errors."""

    default_message = "External service error"
    error_code = "EXTERNAL_SERVICE_ERROR"
    http_status = 502


class WiktionaryError(ExternalServiceError):
    """Raised when Wiktionary API fails."""

    error_code = "WIKTIONARY_ERROR"


class CLLDError(ExternalServiceError):
    """Raised when CLLD/CLICS service fails."""

    error_code = "CLLD_ERROR"


class OCRError(ExternalServiceError):
    """Raised when OCR processing fails."""

    error_code = "OCR_ERROR"


# =============================================================================
# Analysis Errors
# =============================================================================


class AnalysisError(LexiconError):
    """Base class for analysis-related errors."""

    default_message = "Analysis error"
    error_code = "ANALYSIS_ERROR"
    http_status = 500


class InsufficientDataError(AnalysisError):
    """Raised when there's insufficient data for analysis."""

    error_code = "INSUFFICIENT_DATA"
    http_status = 422

    def __init__(
        self,
        analysis_type: str | None = None,
        minimum_required: int | None = None,
        actual: int | None = None,
        **kwargs: Any,
    ):
        message = "Insufficient data for analysis"
        if analysis_type:
            message = f"Insufficient data for {analysis_type} analysis"
        details = {}
        if analysis_type:
            details["analysis_type"] = analysis_type
        if minimum_required is not None:
            details["minimum_required"] = minimum_required
        if actual is not None:
            details["actual"] = actual
        super().__init__(message=message, details=details, **kwargs)


class AmbiguousResultError(AnalysisError):
    """Raised when analysis produces ambiguous results."""

    error_code = "AMBIGUOUS_RESULT"
    http_status = 422


# =============================================================================
# Configuration Errors
# =============================================================================


class ConfigurationError(LexiconError):
    """Raised when there's a configuration problem."""

    default_message = "Configuration error"
    error_code = "CONFIGURATION_ERROR"
    http_status = 500

    def __init__(
        self,
        setting: str | None = None,
        **kwargs: Any,
    ):
        message = "Configuration error"
        if setting:
            message = f"Configuration error: {setting}"
        details = {}
        if setting:
            details["setting"] = setting
        super().__init__(message=message, details=details, **kwargs)
