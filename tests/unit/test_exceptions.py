"""Unit tests for custom exceptions."""

import pytest

from src.exceptions import (
    AnalysisError,
    AuthenticationError,
    AuthorizationError,
    ConfigurationError,
    DatabaseError,
    DuplicateError,
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
    QueryError,
    RateLimitError,
    ValidationError,
)


class TestLexiconError:
    """Tests for base LexiconError."""

    def test_default_message(self):
        """Test default error message."""
        err = LexiconError()
        assert err.message == "An unexpected error occurred"
        assert err.code == "LEXICON_ERROR"
        assert err.http_status == 500

    def test_custom_message(self):
        """Test custom error message."""
        err = LexiconError(message="Custom error", code="CUSTOM")
        assert err.message == "Custom error"
        assert err.code == "CUSTOM"

    def test_to_dict(self):
        """Test error serialization to dict."""
        err = LexiconError(message="Test error", details={"key": "value"})
        result = err.to_dict()
        assert result["error"] == "LEXICON_ERROR"
        assert result["message"] == "Test error"
        assert result["details"]["key"] == "value"

    def test_str_representation(self):
        """Test string representation."""
        err = LexiconError(message="Test error")
        assert str(err) == "Test error"


class TestNotFoundErrors:
    """Tests for NotFoundError and subclasses."""

    def test_not_found_error(self):
        """Test generic NotFoundError."""
        err = NotFoundError(resource_type="Widget", resource_id="123")
        assert err.http_status == 404
        assert "Widget not found: 123" in err.message
        assert err.details["resource_type"] == "Widget"
        assert err.details["resource_id"] == "123"

    def test_lsr_not_found(self):
        """Test LSRNotFoundError."""
        err = LSRNotFoundError(lsr_id="uuid-123")
        assert err.http_status == 404
        assert err.code == "LSR_NOT_FOUND"
        assert "LSR not found: uuid-123" in err.message

    def test_language_not_found(self):
        """Test LanguageNotFoundError."""
        err = LanguageNotFoundError(language_code="xyz")
        assert err.code == "LANGUAGE_NOT_FOUND"
        assert "Language not found: xyz" in err.message


class TestValidationErrors:
    """Tests for ValidationError and subclasses."""

    def test_validation_error(self):
        """Test generic ValidationError."""
        err = ValidationError(message="Invalid input", field="email")
        assert err.http_status == 400
        assert err.code == "VALIDATION_ERROR"
        assert err.details["field"] == "email"

    def test_invalid_date_range(self):
        """Test InvalidDateRangeError."""
        err = InvalidDateRangeError(start_date=2024, end_date=1500)
        assert err.code == "INVALID_DATE_RANGE"
        assert err.details["start_date"] == 2024
        assert err.details["end_date"] == 1500

    def test_invalid_language_code(self):
        """Test InvalidLanguageCodeError."""
        err = InvalidLanguageCodeError(language_code="invalid")
        assert err.code == "INVALID_LANGUAGE_CODE"
        assert "invalid" in err.message


class TestDatabaseErrors:
    """Tests for DatabaseError and subclasses."""

    def test_database_error(self):
        """Test generic DatabaseError."""
        err = DatabaseError(message="Connection failed")
        assert err.http_status == 503
        assert err.code == "DATABASE_ERROR"

    def test_query_error(self):
        """Test QueryError."""
        err = QueryError(query_type="SELECT")
        assert err.code == "QUERY_ERROR"
        assert "SELECT" in err.message


class TestPipelineErrors:
    """Tests for PipelineError and subclasses."""

    def test_pipeline_error(self):
        """Test generic PipelineError."""
        err = PipelineError(message="Processing failed")
        assert err.http_status == 500
        assert err.code == "PIPELINE_ERROR"

    def test_ingestion_error(self):
        """Test IngestionError."""
        err = IngestionError(source="Wiktionary", record_id="word123")
        assert err.code == "INGESTION_ERROR"
        assert err.details["source"] == "Wiktionary"
        assert err.details["record_id"] == "word123"

    def test_entity_resolution_error(self):
        """Test EntityResolutionError."""
        err = EntityResolutionError(message="Failed to resolve entity")
        assert err.code == "ENTITY_RESOLUTION_ERROR"


class TestAnalysisErrors:
    """Tests for AnalysisError and subclasses."""

    def test_analysis_error(self):
        """Test generic AnalysisError."""
        err = AnalysisError(message="Analysis failed")
        assert err.http_status == 500
        assert err.code == "ANALYSIS_ERROR"

    def test_insufficient_data_error(self):
        """Test InsufficientDataError."""
        err = InsufficientDataError(
            analysis_type="dating",
            minimum_required=10,
            actual=3,
        )
        assert err.http_status == 422
        assert err.code == "INSUFFICIENT_DATA"
        assert err.details["analysis_type"] == "dating"
        assert err.details["minimum_required"] == 10
        assert err.details["actual"] == 3


class TestOtherErrors:
    """Tests for other error types."""

    def test_duplicate_error(self):
        """Test DuplicateError."""
        err = DuplicateError(resource_type="LSR", identifier="word-eng")
        assert err.http_status == 409
        assert err.code == "DUPLICATE_ERROR"
        assert "already exists" in err.message

    def test_rate_limit_error(self):
        """Test RateLimitError."""
        err = RateLimitError(retry_after=60)
        assert err.http_status == 429
        assert err.code == "RATE_LIMIT_EXCEEDED"
        assert err.details["retry_after_seconds"] == 60

    def test_authentication_error(self):
        """Test AuthenticationError."""
        err = AuthenticationError()
        assert err.http_status == 401
        assert err.code == "AUTHENTICATION_ERROR"

    def test_authorization_error(self):
        """Test AuthorizationError."""
        err = AuthorizationError()
        assert err.http_status == 403
        assert err.code == "AUTHORIZATION_ERROR"

    def test_external_service_error(self):
        """Test ExternalServiceError."""
        err = ExternalServiceError(message="Wiktionary API unavailable")
        assert err.http_status == 502
        assert err.code == "EXTERNAL_SERVICE_ERROR"

    def test_configuration_error(self):
        """Test ConfigurationError."""
        err = ConfigurationError(setting="DATABASE_URL")
        assert err.http_status == 500
        assert err.code == "CONFIGURATION_ERROR"
        assert err.details["setting"] == "DATABASE_URL"
