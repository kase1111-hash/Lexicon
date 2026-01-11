"""Unit tests for validation utilities."""

import pytest

from src.utils.validation import (
    AnachronismRequest,
    DateTextRequest,
    LSRCreateRequest,
    SearchRequest,
    is_safe_string,
    is_valid_confidence,
    is_valid_iso639_3,
    is_valid_uuid,
    is_valid_year_range,
    sanitize_identifier,
    sanitize_iso_code,
    sanitize_list,
    sanitize_string,
    sanitize_year,
)


class TestSanitizers:
    """Tests for sanitization functions."""

    def test_sanitize_string_basic(self):
        """Test basic string sanitization."""
        result = sanitize_string("  hello   world  ")
        assert result == "hello world"

    def test_sanitize_string_html_escape(self):
        """Test HTML escaping."""
        result = sanitize_string("<script>alert('xss')</script>")
        assert "<script>" not in result
        assert "&lt;script&gt;" in result

    def test_sanitize_string_max_length(self):
        """Test max length truncation."""
        result = sanitize_string("hello world", max_length=5)
        assert result == "hello"
        assert len(result) == 5

    def test_sanitize_string_empty(self):
        """Test empty string handling."""
        assert sanitize_string("") == ""
        assert sanitize_string(None) == ""

    def test_sanitize_identifier_valid(self):
        """Test valid identifier sanitization."""
        result = sanitize_identifier("valid_id_123")
        assert result == "valid_id_123"

    def test_sanitize_identifier_invalid_chars(self):
        """Test identifier with invalid characters."""
        result = sanitize_identifier("invalid-id!@#")
        assert result == "invalidid"

    def test_sanitize_identifier_spaces(self):
        """Test identifier with spaces."""
        result = sanitize_identifier("hello world")
        assert result == "hello_world"

    def test_sanitize_iso_code_valid(self):
        """Test valid ISO 639-3 code."""
        assert sanitize_iso_code("eng") == "eng"
        assert sanitize_iso_code("ENG") == "eng"
        assert sanitize_iso_code("  eng  ") == "eng"

    def test_sanitize_iso_code_invalid(self):
        """Test invalid ISO 639-3 code."""
        assert sanitize_iso_code("en") == ""  # Too short
        assert sanitize_iso_code("english") == ""  # Too long
        assert sanitize_iso_code("123") == ""  # Numbers not allowed

    def test_sanitize_year_valid(self):
        """Test valid year sanitization."""
        assert sanitize_year(2024) == 2024
        assert sanitize_year(-500) == -500  # BCE
        assert sanitize_year(0) == 0

    def test_sanitize_year_invalid(self):
        """Test invalid year sanitization."""
        assert sanitize_year(-20000) is None  # Too old
        assert sanitize_year(5000) is None  # Too far in future

    def test_sanitize_list(self):
        """Test list sanitization."""
        items = ["  hello  ", "<script>", "world"]
        result = sanitize_list(items, sanitize_string)
        assert result[0] == "hello"
        assert "&lt;script&gt;" in result[1]
        assert result[2] == "world"


class TestValidators:
    """Tests for validation functions."""

    def test_is_valid_iso639_3_valid(self):
        """Test valid ISO 639-3 codes."""
        assert is_valid_iso639_3("eng") is True
        assert is_valid_iso639_3("deu") is True
        assert is_valid_iso639_3("fra") is True

    def test_is_valid_iso639_3_invalid(self):
        """Test invalid ISO 639-3 codes."""
        assert is_valid_iso639_3("en") is False
        assert is_valid_iso639_3("english") is False
        assert is_valid_iso639_3("") is False
        assert is_valid_iso639_3("123") is False

    def test_is_valid_uuid_valid(self):
        """Test valid UUIDs."""
        assert is_valid_uuid("550e8400-e29b-41d4-a716-446655440000") is True
        assert is_valid_uuid("550E8400-E29B-41D4-A716-446655440000") is True

    def test_is_valid_uuid_invalid(self):
        """Test invalid UUIDs."""
        assert is_valid_uuid("not-a-uuid") is False
        assert is_valid_uuid("") is False
        assert is_valid_uuid("550e8400-e29b-41d4-a716") is False

    def test_is_valid_year_range_valid(self):
        """Test valid year ranges."""
        assert is_valid_year_range(-500, 200) is True
        assert is_valid_year_range(1500, 2024) is True
        assert is_valid_year_range(0, 0) is True

    def test_is_valid_year_range_invalid(self):
        """Test invalid year ranges."""
        assert is_valid_year_range(2024, 1500) is False  # End before start
        assert is_valid_year_range(-20000, 0) is False  # Too old

    def test_is_valid_confidence_valid(self):
        """Test valid confidence scores."""
        assert is_valid_confidence(0.0) is True
        assert is_valid_confidence(0.5) is True
        assert is_valid_confidence(1.0) is True

    def test_is_valid_confidence_invalid(self):
        """Test invalid confidence scores."""
        assert is_valid_confidence(-0.1) is False
        assert is_valid_confidence(1.1) is False

    def test_is_safe_string_safe(self):
        """Test safe strings."""
        assert is_safe_string("hello world") is True
        assert is_safe_string("café résumé") is True

    def test_is_safe_string_unsafe(self):
        """Test unsafe strings with injection patterns."""
        assert is_safe_string("<script>") is False
        assert is_safe_string("'; DROP TABLE") is False
        assert is_safe_string("javascript:") is False


class TestRequestModels:
    """Tests for Pydantic request models."""

    def test_search_request_valid(self):
        """Test valid search request."""
        req = SearchRequest(form="water", language="eng", limit=10)
        assert req.form == "water"
        assert req.language == "eng"
        assert req.limit == 10

    def test_search_request_defaults(self):
        """Test search request defaults."""
        req = SearchRequest()
        assert req.limit == 20
        assert req.offset == 0

    def test_date_text_request_valid(self):
        """Test valid date text request."""
        req = DateTextRequest(text="Sample text for dating", language="eng")
        assert req.text == "Sample text for dating"
        assert req.language == "eng"

    def test_anachronism_request_valid(self):
        """Test valid anachronism request."""
        req = AnachronismRequest(
            text="The knight used his smartphone",
            language="eng",
            claimed_date=1200,
        )
        assert req.claimed_date == 1200

    def test_lsr_create_request_valid(self):
        """Test valid LSR create request."""
        req = LSRCreateRequest(
            form_orthographic="water",
            language_code="eng",
            definition_primary="a liquid",
        )
        assert req.form_orthographic == "water"
        assert req.language_code == "eng"

    def test_lsr_create_request_validation(self):
        """Test LSR create request validation."""
        with pytest.raises(ValueError):
            LSRCreateRequest(
                form_orthographic="",  # Empty form should fail
                language_code="eng",
            )
