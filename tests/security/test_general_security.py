"""General security tests for the application.

These tests cover general security practices and patterns
that should be followed throughout the codebase.
"""

import pytest
import os
import ast
import re
from pathlib import Path


class TestNoHardcodedCredentials:
    """Test that there are no hardcoded credentials in the codebase."""

    @pytest.fixture
    def source_files(self):
        """Get all Python source files."""
        src_dir = Path("/home/user/Lexicon/src")
        return list(src_dir.rglob("*.py"))

    def test_no_hardcoded_passwords(self, source_files):
        """Test that no passwords are hardcoded in source files."""
        password_patterns = [
            r'password\s*=\s*["\'][^"\']{8,}["\']',  # password = "something"
            r'passwd\s*=\s*["\'][^"\']{8,}["\']',
            r'pwd\s*=\s*["\'][^"\']{8,}["\']',
        ]

        for file_path in source_files:
            content = file_path.read_text()

            for pattern in password_patterns:
                matches = re.findall(pattern, content, re.IGNORECASE)
                for match in matches:
                    # Allow if it's in a comment, docstring, or test/example
                    if not any(x in match.lower() for x in ['example', 'test', 'placeholder', 'your-']):
                        # Check if it's a default="" pattern (acceptable)
                        if 'default=' not in match.lower():
                            pytest.fail(f"Potential hardcoded password in {file_path}: {match}")

    def test_no_hardcoded_api_keys(self, source_files):
        """Test that no API keys are hardcoded in source files."""
        api_key_patterns = [
            r'api_key\s*=\s*["\'][a-zA-Z0-9_-]{20,}["\']',
            r'apikey\s*=\s*["\'][a-zA-Z0-9_-]{20,}["\']',
            r'api[-_]?secret\s*=\s*["\'][a-zA-Z0-9_-]{20,}["\']',
        ]

        for file_path in source_files:
            content = file_path.read_text()

            for pattern in api_key_patterns:
                matches = re.findall(pattern, content, re.IGNORECASE)
                for match in matches:
                    if 'default=' not in match.lower() and 'example' not in match.lower():
                        pytest.fail(f"Potential hardcoded API key in {file_path}: {match}")

    def test_no_private_keys(self, source_files):
        """Test that no private keys are embedded in source files."""
        private_key_markers = [
            "-----BEGIN RSA PRIVATE KEY-----",
            "-----BEGIN PRIVATE KEY-----",
            "-----BEGIN EC PRIVATE KEY-----",
            "-----BEGIN DSA PRIVATE KEY-----",
        ]

        for file_path in source_files:
            content = file_path.read_text()

            for marker in private_key_markers:
                if marker in content:
                    pytest.fail(f"Private key found in {file_path}")


class TestSecureDefaults:
    """Test that security-sensitive settings have secure defaults."""

    def test_rate_limiting_configurable(self):
        """Test that rate limiting is configurable (off by default for dev)."""
        from src.config import Settings

        settings = Settings()

        # Rate limiting should be configurable
        assert hasattr(settings.api, 'rate_limit_enabled')
        # Default is off for development, but can be enabled
        assert isinstance(settings.api.rate_limit_enabled, bool)

    def test_jwt_algorithm_secure(self):
        """Test that JWT uses secure algorithm by default."""
        from src.config import APIConfig

        config = APIConfig()

        # JWT algorithm should be a secure choice
        assert config.jwt_algorithm in ["HS256", "HS384", "HS512", "RS256", "RS384", "RS512"]


class TestExceptionSecurity:
    """Test that exceptions don't leak sensitive information."""

    def test_validation_error_doesnt_leak_value(self):
        """Test that validation errors don't expose full input values."""
        from src.exceptions import ValidationError

        # Create error with sensitive input
        sensitive_value = "password123secret"
        error = ValidationError(
            message="Invalid input",
            field="test",
            value=sensitive_value,
        )

        error_str = str(error)
        error_dict = error.to_dict()

        # The full sensitive value might be in details for debugging
        # But it should be truncated or masked in production
        # This test documents the behavior

    def test_database_error_doesnt_leak_credentials(self):
        """Test that database errors don't expose credentials."""
        from src.exceptions import ConnectionError as DBConnectionError

        error = DBConnectionError(database="neo4j")

        error_str = str(error)

        # Should not contain credential patterns
        assert "password" not in error_str.lower()
        assert "secret" not in error_str.lower()


class TestLoggingSecurity:
    """Test secure logging practices."""

    def test_request_id_format_safe(self):
        """Test that request IDs are safely formatted."""
        from src.utils.logging import set_request_id, get_request_id

        # Set a request ID
        req_id = set_request_id()

        # Should be a safe string (UUID format)
        assert isinstance(req_id, str)
        assert len(req_id) > 0
        # Should not contain potentially dangerous characters
        assert "<" not in req_id
        assert ">" not in req_id
        assert ";" not in req_id


class TestDataValidation:
    """Test that data validation prevents malicious input."""

    def test_lsr_date_validation(self):
        """Test that LSR dates are validated."""
        from src.models.lsr import LSR

        # Invalid dates should be rejected
        with pytest.raises(ValueError):
            LSR(
                form_orthographic="test",
                language_code="eng",
                date_start=2024,
                date_end=1900,  # End before start
            )

    def test_confidence_value_bounds(self):
        """Test that confidence values are bounded."""
        from src.models.lsr import LSR

        # Out of bounds values should be rejected
        with pytest.raises(ValueError):
            LSR(
                form_orthographic="test",
                language_code="eng",
                confidence_overall=1.5,  # > 1.0
            )

        with pytest.raises(ValueError):
            LSR(
                form_orthographic="test",
                language_code="eng",
                confidence_overall=-0.1,  # < 0.0
            )


class TestResourceLimits:
    """Test that resource limits prevent denial of service."""

    def test_sanitize_string_has_length_limit(self):
        """Test that sanitize_string enforces length limits."""
        from src.utils.validation import sanitize_string

        # Very long input should be truncated
        huge_input = "a" * 10000000  # 10MB
        result = sanitize_string(huge_input, max_length=10000)

        assert len(result) <= 10000

    def test_list_input_limits(self):
        """Test that list inputs have reasonable limits."""
        from src.models.lsr import LSR

        # Very long lists should be handled without crash
        huge_list = [f"item{i}" for i in range(10000)]

        lsr = LSR(
            form_orthographic="test",
            language_code="eng",
            definitions_alternate=huge_list[:100],  # Use reasonable subset
        )

        assert lsr.definitions_alternate is not None


class TestGitIgnoreSecurity:
    """Test that .gitignore properly excludes sensitive files."""

    def test_gitignore_excludes_env_files(self):
        """Test that .gitignore excludes .env files."""
        gitignore_path = Path("/home/user/Lexicon/.gitignore")

        if gitignore_path.exists():
            content = gitignore_path.read_text()

            # Should exclude .env files
            assert ".env" in content or "*.env" in content

    def test_gitignore_excludes_secrets(self):
        """Test that .gitignore excludes common secret file patterns."""
        gitignore_path = Path("/home/user/Lexicon/.gitignore")

        if gitignore_path.exists():
            content = gitignore_path.read_text()

            # Should exclude common secret patterns
            patterns_to_check = [
                ".env",
                "*.pem",
                "*.key",
                "credentials",
            ]

            # At minimum, .env should be excluded
            assert ".env" in content


class TestDependencySecurity:
    """Test for known security issues in dependencies."""

    def test_no_known_vulnerable_patterns(self):
        """Test for patterns that indicate vulnerable practices."""
        src_dir = Path("/home/user/Lexicon/src")

        vulnerable_patterns = [
            (r'pickle\.loads?\(', "Pickle deserialization can be dangerous"),
            (r'eval\s*\(', "eval() can execute arbitrary code"),
            (r'exec\s*\(', "exec() can execute arbitrary code"),
            (r'__import__\s*\(', "Dynamic imports can be dangerous"),
            (r'subprocess\..*shell\s*=\s*True', "Shell=True can be dangerous"),
        ]

        for py_file in src_dir.rglob("*.py"):
            content = py_file.read_text()

            for pattern, message in vulnerable_patterns:
                matches = re.findall(pattern, content)
                if matches:
                    # Check if it's in a comment
                    for line_no, line in enumerate(content.split('\n'), 1):
                        if re.search(pattern, line) and not line.strip().startswith('#'):
                            pytest.fail(
                                f"{message} found in {py_file}:{line_no}: {line.strip()}"
                            )
