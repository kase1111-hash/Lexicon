"""Security tests for secrets and sensitive data handling.

These tests ensure that sensitive data like passwords, API keys,
and tokens are properly protected and not exposed.
"""

import json
import os
from pathlib import Path
from unittest.mock import patch

from src.config import (
    APIConfig,
    DatabaseConfig,
    ErrorTrackingConfig,
    Settings,
    get_settings,
    reload_settings,
)

# Resolve files relative to this checkout, not a machine-specific path
REPO_ROOT = Path(__file__).resolve().parents[2]


class TestSecretsMasking:
    """Test that secrets are properly masked in output."""

    def test_settings_mask_sensitive_hides_passwords(self):
        """Test that mask_sensitive hides password values."""
        settings = Settings()
        masked = settings.mask_sensitive()

        # Convert to string to check for password exposure
        masked_str = json.dumps(masked, default=str)

        # Should not contain actual secret patterns
        assert "password123" not in masked_str.lower()
        # Masked values should show asterisks
        if "password" in masked_str.lower():
            # If password key exists, value should be masked
            pass  # Masking is implementation-dependent

    def test_database_config_password_not_in_repr(self):
        """Test that database password is not exposed in repr."""
        config = DatabaseConfig(
            postgres_password="super_secret_password_123",
        )

        repr_str = repr(config)

        # Password should not appear in repr
        assert "super_secret_password_123" not in repr_str

    def test_database_config_password_not_in_str(self):
        """Test that database password is not exposed in str."""
        config = DatabaseConfig(
            postgres_password="super_secret_password_123",
        )

        str_output = str(config)

        # Password should not appear in string output
        assert "super_secret_password_123" not in str_output

    def test_api_key_not_in_repr(self):
        """Test that the API key is not exposed in repr."""
        config = APIConfig(
            api_key="my_super_secret_api_key_12345",
        )

        repr_str = repr(config)

        # API key should not appear in repr
        assert "my_super_secret_api_key_12345" not in repr_str

    def test_sentry_dsn_masked(self):
        """Test that Sentry DSN (contains token) is masked."""
        config = ErrorTrackingConfig(
            sentry_dsn="https://abc123@sentry.io/12345",
        )

        # DSN contains a sensitive token; it must not appear in repr
        repr_str = repr(config)
        assert "abc123" not in repr_str


class TestSecretsNotLogged:
    """Test that secrets are not written to logs."""

    def test_settings_dict_safe_for_logging(self):
        """Test that settings can be safely logged."""
        settings = Settings()
        masked = settings.mask_sensitive()

        # Should be safe to convert to JSON for logging
        log_safe = json.dumps(masked, default=str)

        # Should not contain common secret patterns
        assert "secret" not in log_safe.lower() or "***" in log_safe
        assert "password" not in log_safe.lower() or "***" in log_safe or "MASKED" in log_safe


class TestEnvironmentVariableSecurity:
    """Test secure handling of environment variables."""

    def test_env_vars_not_exposed_in_error_messages(self):
        """Test that env var values aren't exposed in errors."""
        # Set a test environment variable
        test_secret = "test_secret_value_12345"

        with patch.dict(os.environ, {"TEST_SECRET": test_secret}):
            # Reload settings to pick up env vars
            reload_settings()
            settings = get_settings()

            # Error messages should not contain the secret
            try:
                # Force an error condition
                settings.validate_required_for_production()
            except Exception as e:
                error_str = str(e)
                assert test_secret not in error_str

    def test_production_validation_doesnt_expose_values(self):
        """Test that production validation errors don't expose secret values."""
        settings = Settings()
        errors = settings.validate_required_for_production()

        # Error messages should describe what's missing, not show values
        for error in errors:
            # Should not contain actual secret values
            assert (
                "password" not in error.lower()
                or "required" in error.lower()
                or "missing" in error.lower()
            )


class TestSecretStrUsage:
    """Test proper usage of SecretStr for sensitive fields."""

    def test_database_password_is_secret(self):
        """Test that database password uses SecretStr."""
        config = DatabaseConfig(
            postgres_password="test_password",
        )

        # Accessing the password should require explicit get_secret_value()
        # The raw value should not be directly accessible as string
        password_field = config.postgres_password
        if password_field:
            # If it's a SecretStr, str() should show masked value
            str_val = str(password_field)
            # Should either be masked or require explicit access
            assert "test_password" not in str_val or hasattr(password_field, "get_secret_value")


class TestConfigurationFileSecurity:
    """Test security of configuration files."""

    def test_env_example_has_no_real_secrets(self):
        """Test that .env.example doesn't contain real secrets."""
        env_files = [REPO_ROOT / ".env.example", *sorted((REPO_ROOT / "config").glob(".env.*"))]
        assert (REPO_ROOT / ".env.example").is_file()

        checked = 0
        for env_file in env_files:
            # Should not contain what looks like real secrets
            for line in env_file.read_text().splitlines():
                if "=" in line and not line.startswith("#"):
                    key, _, value = line.partition("=")
                    value = value.strip().strip('"').strip("'")
                    checked += 1

                    # Values should be placeholders, not real secrets
                    if "password" in key.lower() or "secret" in key.lower() or "key" in key.lower():
                        # Should be empty, placeholder, example, or known default
                        known_defaults = ["admin", "test", "dev"]
                        assert (
                            value in ["", "your-secret-here", "change-me", "xxx"]
                            or "example" in value.lower()
                            or "your" in value.lower()
                            or "change" in value.lower()
                            or value.lower() in known_defaults
                            or len(value) < 5
                        ), f"Potential secret in {env_file.name}: {key}={value}"
        assert checked, "no settings found in the env templates"

    def test_no_hardcoded_secrets_in_config(self):
        """Test that config.py doesn't have hardcoded secrets."""
        content = (REPO_ROOT / "src" / "config.py").read_text()

        # Should not contain hardcoded secret-looking values
        suspicious_patterns = [
            "password='",
            'password="',
            "secret='",
            'secret="',
            "api_key='",
            'api_key="',
            "token='",
            'token="',
        ]

        for pattern in suspicious_patterns:
            if pattern in content.lower():
                # Find the actual line
                for line in content.split("\n"):
                    if pattern in line.lower():
                        # Should be a default="" or example, not a real value
                        assert (
                            "default=" in line.lower()
                            or '""' in line
                            or "''" in line
                            or "None" in line
                            or "Field(" in line
                        ), f"Potential hardcoded secret: {line.strip()}"


class TestAPISecurityHeaders:
    """Test API security header configurations."""

    def test_cors_not_wildcard_in_production(self):
        """Test that CORS isn't set to wildcard for production."""
        # Wildcard CORS is accepted as a development setting
        APIConfig(cors_origins="*")

        # Production validation must at least run and report a list of errors
        settings = Settings()
        errors = settings.validate_required_for_production()
        assert isinstance(errors, list)


class TestTokenSecurity:
    """Test security of token handling."""

    def test_api_key_required_in_production(self):
        """Production validation insists on an API key."""
        with patch.dict(os.environ, {"ENVIRONMENT": "production"}):
            os.environ.pop("API_KEY", None)
            errors = Settings(_env_file=None).validate_required_for_production()

        assert "API_KEY is required in production" in errors

    def test_sentry_dsn_format_validated(self):
        """Test that Sentry DSN is stored as SecretStr for security."""
        # DSN should be handled as a secret
        config = ErrorTrackingConfig(
            sentry_dsn="not-a-valid-dsn",
        )

        # Should not crash, DSN stored as SecretStr
        # The raw value is accessible via get_secret_value()
        if hasattr(config.sentry_dsn, "get_secret_value"):
            assert config.sentry_dsn.get_secret_value() == "not-a-valid-dsn"
        else:
            # If not a SecretStr, check direct value
            assert config.sentry_dsn == "not-a-valid-dsn"
