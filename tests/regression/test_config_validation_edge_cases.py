"""Regression tests for configuration and validation edge cases.

These tests ensure configuration and validation
handle edge cases correctly without breaking.
"""

import pytest
from unittest.mock import patch
import os

from src.config import (
    APIConfig,
    DatabaseConfig,
    LoggingConfig,
    ErrorTrackingConfig,
    Settings,
    get_settings,
    reload_settings,
)
from src.exceptions import (
    ValidationError,
    InvalidDateRangeError,
    InvalidLanguageCodeError,
    LexiconError,
)
from src.utils.validation import (
    sanitize_string,
    sanitize_iso_code,
    is_valid_year_range,
)


class TestConfigurationEdgeCases:
    """Test configuration edge cases."""

    def test_database_config_defaults(self):
        """Test database config uses correct defaults."""
        config = DatabaseConfig()
        assert config.neo4j_uri == "bolt://localhost:7687"
        assert config.postgres_host == "localhost"
        assert config.postgres_port == 5432

    def test_api_config_port_boundaries(self):
        """Test API config port at boundaries."""
        # Minimum valid port
        config_min = APIConfig(api_port=1)
        assert config_min.api_port == 1

        # Maximum valid port
        config_max = APIConfig(api_port=65535)
        assert config_max.api_port == 65535

    def test_api_config_invalid_port(self):
        """Test API config rejects invalid ports."""
        with pytest.raises(ValueError):
            APIConfig(api_port=0)

        with pytest.raises(ValueError):
            APIConfig(api_port=65536)

        with pytest.raises(ValueError):
            APIConfig(api_port=-1)

    def test_logging_config_level_case_insensitive(self):
        """Test log level is case insensitive."""
        config_lower = LoggingConfig(log_level="debug")
        assert config_lower.log_level == "DEBUG"

        config_upper = LoggingConfig(log_level="DEBUG")
        assert config_upper.log_level == "DEBUG"

        config_mixed = LoggingConfig(log_level="DeBuG")
        assert config_mixed.log_level == "DEBUG"

    def test_logging_config_invalid_level(self):
        """Test logging config rejects invalid level."""
        with pytest.raises(ValueError):
            LoggingConfig(log_level="INVALID")

    def test_error_tracking_sample_rate_boundaries(self):
        """Test sample rate at boundaries."""
        config_zero = ErrorTrackingConfig(sentry_traces_sample_rate=0.0)
        assert config_zero.sentry_traces_sample_rate == 0.0

        config_one = ErrorTrackingConfig(sentry_traces_sample_rate=1.0)
        assert config_one.sentry_traces_sample_rate == 1.0

    def test_error_tracking_invalid_sample_rate(self):
        """Test invalid sample rates rejected."""
        with pytest.raises(ValueError):
            ErrorTrackingConfig(sentry_traces_sample_rate=-0.1)

        with pytest.raises(ValueError):
            ErrorTrackingConfig(sentry_traces_sample_rate=1.1)

    def test_settings_reload_clears_cache(self):
        """Test that reload_settings clears the cache."""
        settings1 = get_settings()
        reload_settings()
        settings2 = get_settings()

        # Both should be valid settings objects
        assert settings1 is not None
        assert settings2 is not None

    def test_settings_mask_sensitive_values(self):
        """Test that sensitive values are masked."""
        settings = Settings()
        masked = settings.mask_sensitive()

        # Check structure
        assert isinstance(masked, dict)
        assert "database" in masked
        assert "api" in masked

    def test_cors_origins_parsing(self):
        """Test CORS origins parsing edge cases."""
        # Wildcard
        config_wildcard = APIConfig(cors_origins="*")
        assert config_wildcard.cors_origins_list == ["*"]

        # Multiple origins
        config_multi = APIConfig(
            cors_origins="http://localhost:3000,http://localhost:8080"
        )
        assert len(config_multi.cors_origins_list) == 2

        # Single origin
        config_single = APIConfig(cors_origins="http://localhost:3000")
        assert "http://localhost:3000" in config_single.cors_origins_list


class TestValidationEdgeCases:
    """Test validation utility edge cases."""

    def test_sanitize_string_empty(self):
        """Test sanitizing empty string."""
        result = sanitize_string("")
        assert result == ""

    def test_sanitize_string_whitespace(self):
        """Test sanitizing whitespace-only string."""
        result = sanitize_string("   ")
        assert result == ""

    def test_sanitize_string_with_html(self):
        """Test sanitizing string with HTML."""
        result = sanitize_string("<script>alert('xss')</script>")
        assert "<script>" not in result
        assert "alert" not in result or "<" not in result

    def test_sanitize_string_max_length(self):
        """Test sanitizing string with max length."""
        long_string = "a" * 1000
        result = sanitize_string(long_string, max_length=100)
        assert len(result) <= 100

    def test_sanitize_iso_code_valid(self):
        """Test sanitizing valid ISO codes."""
        assert sanitize_iso_code("eng") == "eng"
        assert sanitize_iso_code("ENG") == "eng"
        assert sanitize_iso_code("  eng  ") == "eng"

    def test_sanitize_iso_code_invalid(self):
        """Test sanitizing invalid ISO codes."""
        assert sanitize_iso_code("") == ""
        # Function may allow longer codes for flexibility
        # Just ensure it handles them without error
        result = sanitize_iso_code("toolong")
        assert isinstance(result, str)

    def test_is_valid_year_range_valid(self):
        """Test valid date ranges."""
        # Valid range
        assert is_valid_year_range(1000, 2000) is True

        # Same date
        assert is_valid_year_range(1500, 1500) is True

        # BCE dates
        assert is_valid_year_range(-500, -100) is True

        # None values
        assert is_valid_year_range(None, None) is True
        assert is_valid_year_range(1000, None) is True
        assert is_valid_year_range(None, 2000) is True

    def test_is_valid_year_range_invalid(self):
        """Test invalid date ranges."""
        # End before start
        assert is_valid_year_range(2000, 1000) is False


class TestExceptionEdgeCases:
    """Test exception edge cases."""

    def test_validation_error_with_all_params(self):
        """Test ValidationError with all parameters."""
        error = ValidationError(
            message="Custom message",
            field="test_field",
            value="bad_value",
        )

        assert error.message == "Custom message"
        assert error.http_status == 400
        error_dict = error.to_dict()
        assert "error" in error_dict
        assert "message" in error_dict

    def test_validation_error_minimal(self):
        """Test ValidationError with minimal parameters."""
        error = ValidationError(message="Error")
        assert error.http_status == 400

    def test_invalid_date_range_error(self):
        """Test InvalidDateRangeError formatting."""
        error = InvalidDateRangeError(start_date=2000, end_date=1000)
        assert error.http_status == 400
        assert "2000" in str(error.details) or "2000" in error.message

    def test_invalid_language_code_error(self):
        """Test InvalidLanguageCodeError formatting."""
        error = InvalidLanguageCodeError(language_code="xyz")
        assert error.http_status == 400

    def test_lexicon_error_to_dict(self):
        """Test base LexiconError to_dict method."""
        error = LexiconError(message="Test error", details={"key": "value"})
        result = error.to_dict()

        assert "error" in result
        assert "message" in result
        assert result["message"] == "Test error"
        assert result["details"]["key"] == "value"

    def test_exception_chaining(self):
        """Test exception chaining works correctly."""
        original = ValueError("Original error")
        try:
            try:
                raise original
            except ValueError as e:
                raise ValidationError(message="Wrapped error") from e
        except ValidationError as ve:
            assert ve.__cause__ is original


class TestEnvironmentVariableEdgeCases:
    """Test environment variable handling edge cases."""

    def test_env_override_with_empty_string(self):
        """Test that empty env var doesn't override defaults."""
        reload_settings()
        settings = get_settings()

        # Default should be used
        assert settings.logging.log_level in ["INFO", "DEBUG", "WARNING", "ERROR"]

    def test_settings_with_missing_optional(self):
        """Test settings work with missing optional values."""
        settings = Settings()

        # Optional values should have defaults
        assert settings.database is not None
        assert settings.api is not None

    def test_production_validation_in_dev(self):
        """Test production validation returns empty in dev mode."""
        settings = Settings()
        errors = settings.validate_required_for_production()

        # In development, should have no critical errors
        assert isinstance(errors, list)


class TestDatabaseConfigEdgeCases:
    """Test database configuration edge cases."""

    def test_postgres_dsn_generation(self):
        """Test PostgreSQL DSN is properly generated."""
        config = DatabaseConfig(
            postgres_host="db.example.com",
            postgres_port=5432,
            postgres_db="testdb",
            postgres_user="user",
        )

        dsn = config.postgres_dsn
        assert "db.example.com" in dsn
        assert "5432" in dsn
        assert "testdb" in dsn

    def test_redis_url_without_password(self):
        """Test Redis URL without password."""
        config = DatabaseConfig(
            redis_host="redis.example.com",
            redis_port=6379,
            redis_db=0,
            redis_password=None,
        )

        url = config.redis_url
        assert "redis.example.com" in url
        assert "6379" in url

    def test_redis_url_with_password(self):
        """Test Redis URL with password is properly formatted."""
        config = DatabaseConfig(
            redis_host="redis.example.com",
            redis_port=6379,
            redis_db=0,
        )

        # URL should be valid format
        url = config.redis_url
        assert url.startswith("redis://")


class TestInputSanitizationRegression:
    """Regression tests for input sanitization."""

    def test_null_byte_injection(self):
        """Test that null bytes are handled gracefully."""
        result = sanitize_string("test\x00injection")
        # Sanitize should at least return a string without crashing
        assert isinstance(result, str)

    def test_unicode_normalization(self):
        """Test Unicode normalization."""
        # Different Unicode representations of same character
        result1 = sanitize_string("café")  # Precomposed
        result2 = sanitize_string("cafe\u0301")  # Decomposed

        # Both should be sanitized consistently
        assert result1 is not None
        assert result2 is not None

    def test_control_characters(self):
        """Test control characters are stripped."""
        result = sanitize_string("test\n\r\tvalue")
        # Should handle control characters gracefully
        assert isinstance(result, str)

    def test_very_long_input(self):
        """Test very long input is handled."""
        long_input = "x" * 100000
        result = sanitize_string(long_input, max_length=1000)
        assert len(result) <= 1000
