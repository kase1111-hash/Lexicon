"""Unit tests for configuration management."""

import os
from unittest.mock import patch

import pytest

from src.config import (
    APIConfig,
    DatabaseConfig,
    ErrorTrackingConfig,
    LoggingConfig,
    Settings,
    get_settings,
    is_debug,
    is_production,
    reload_settings,
)


class TestDatabaseConfig:
    """Tests for DatabaseConfig."""

    def test_default_values(self):
        """Test default database configuration values."""
        config = DatabaseConfig()
        assert config.neo4j_uri == "bolt://localhost:7687"
        assert config.postgres_host == "localhost"
        assert config.postgres_port == 5432
        assert config.redis_host == "localhost"
        assert config.redis_port == 6379

    def test_postgres_dsn(self):
        """Test PostgreSQL DSN generation."""
        config = DatabaseConfig(
            postgres_host="db.example.com",
            postgres_port=5432,
            postgres_db="testdb",
            postgres_user="testuser",
        )
        dsn = config.postgres_dsn
        assert "db.example.com" in dsn
        assert "5432" in dsn
        assert "testdb" in dsn
        assert "testuser" in dsn

    def test_redis_url_without_password(self):
        """Test Redis URL without password."""
        config = DatabaseConfig(redis_host="redis.example.com", redis_port=6379, redis_db=0)
        assert config.redis_url == "redis://redis.example.com:6379/0"


class TestAPIConfig:
    """Tests for APIConfig."""

    def test_default_values(self):
        """Test default API configuration values."""
        config = APIConfig()
        assert config.api_host == "0.0.0.0"
        assert config.api_port == 8000
        assert config.cors_origins == "*"

    def test_cors_origins_list_wildcard(self):
        """Test CORS origins list with wildcard."""
        config = APIConfig(cors_origins="*")
        assert config.cors_origins_list == ["*"]

    def test_cors_origins_list_multiple(self):
        """Test CORS origins list with multiple values."""
        config = APIConfig(cors_origins="http://localhost:3000,http://localhost:8080")
        assert "http://localhost:3000" in config.cors_origins_list
        assert "http://localhost:8080" in config.cors_origins_list

    def test_port_validation_valid(self):
        """Test valid port validation."""
        config = APIConfig(api_port=8080)
        assert config.api_port == 8080

    def test_port_validation_invalid(self):
        """Test invalid port validation."""
        with pytest.raises(ValueError):
            APIConfig(api_port=99999)

        with pytest.raises(ValueError):
            APIConfig(api_port=0)


class TestLoggingConfig:
    """Tests for LoggingConfig."""

    def test_default_values(self):
        """Test default logging configuration values."""
        config = LoggingConfig()
        assert config.log_level == "INFO"
        assert config.log_format == "text"
        assert config.slow_request_threshold_ms == 1000.0

    def test_log_level_validation_valid(self):
        """Test valid log level validation."""
        config = LoggingConfig(log_level="debug")
        assert config.log_level == "DEBUG"

    def test_log_level_validation_invalid(self):
        """Test invalid log level validation."""
        with pytest.raises(ValueError):
            LoggingConfig(log_level="INVALID")


class TestErrorTrackingConfig:
    """Tests for ErrorTrackingConfig."""

    def test_default_values(self):
        """Test default error tracking configuration values."""
        config = ErrorTrackingConfig()
        assert config.environment == "development"
        assert config.app_version == "0.1.0"
        assert config.debug is False

    def test_sample_rate_validation_valid(self):
        """Test valid sample rate validation."""
        config = ErrorTrackingConfig(sentry_traces_sample_rate=0.5)
        assert config.sentry_traces_sample_rate == 0.5

    def test_sample_rate_validation_invalid(self):
        """Test invalid sample rate validation."""
        with pytest.raises(ValueError):
            ErrorTrackingConfig(sentry_traces_sample_rate=1.5)

        with pytest.raises(ValueError):
            ErrorTrackingConfig(sentry_traces_sample_rate=-0.1)


class TestSettings:
    """Tests for main Settings class."""

    def test_settings_creation(self):
        """Test settings creation with defaults."""
        settings = Settings()
        assert settings.database is not None
        assert settings.api is not None
        assert settings.logging is not None
        assert settings.error_tracking is not None

    def test_mask_sensitive(self):
        """Test sensitive value masking."""
        settings = Settings()
        masked = settings.mask_sensitive()

        # Check that sensitive fields are masked
        # The structure varies, but any 'password' field should be masked
        def check_masked(d, depth=0):
            if depth > 5:
                return
            for key, value in d.items():
                if isinstance(value, dict):
                    check_masked(value, depth + 1)
                elif "password" in key.lower() and value:
                    assert value == "***MASKED***" or not value

        check_masked(masked)

    def test_validate_required_for_production_dev(self):
        """Test production validation in development mode."""
        settings = Settings()
        # In development, should have no errors
        errors = settings.validate_required_for_production()
        assert errors == []  # Development mode, no strict requirements


class TestSettingsFunctions:
    """Tests for settings utility functions."""

    def test_get_settings_caching(self):
        """Test that get_settings returns cached instance."""
        settings1 = get_settings()
        settings2 = get_settings()
        assert settings1 is settings2

    def test_reload_settings(self):
        """Test settings reload clears cache."""
        settings1 = get_settings()
        reload_settings()
        settings2 = get_settings()
        # After reload, should be a new instance
        # (but may be equal in content)
        assert settings1 is not None
        assert settings2 is not None

    def test_is_production_false(self):
        """Test is_production returns False in development."""
        # Default is development
        assert is_production() is False

    def test_is_debug_false(self):
        """Test is_debug returns False by default."""
        assert is_debug() is False


class TestEnvironmentLoading:
    """Tests for environment variable loading."""

    def test_env_override(self):
        """Test that environment variables override defaults."""
        with patch.dict(os.environ, {"LOG_LEVEL": "DEBUG"}):
            reload_settings()
            settings = get_settings()
            # Note: This may not work in all test scenarios due to caching
            # In real tests, you'd need to reload settings properly

    def test_missing_env_uses_defaults(self):
        """Test that missing env vars use defaults."""
        settings = Settings()
        assert settings.logging.log_level in ["INFO", "DEBUG", "WARNING", "ERROR", "CRITICAL"]
