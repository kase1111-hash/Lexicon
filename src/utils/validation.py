"""Input validation and sanitization utilities."""

import html
import re
from typing import Any

from pydantic import BaseModel, Field, field_validator


# =============================================================================
# Sanitization Functions
# =============================================================================


def sanitize_string(value: str, max_length: int | None = None) -> str:
    """
    Sanitize a string input.

    - Strips leading/trailing whitespace
    - Normalizes internal whitespace
    - Optionally truncates to max_length
    - Escapes HTML entities
    """
    if not value:
        return ""

    # Strip and normalize whitespace
    result = " ".join(value.split())

    # Escape HTML entities to prevent XSS
    result = html.escape(result)

    # Truncate if needed
    if max_length and len(result) > max_length:
        result = result[:max_length]

    return result


def sanitize_identifier(value: str) -> str:
    """
    Sanitize an identifier (e.g., language code, ID).

    - Only allows alphanumeric, hyphen, underscore
    - Converts to lowercase
    - Max 50 characters
    """
    if not value:
        return ""

    # Only allow safe characters
    result = re.sub(r"[^a-zA-Z0-9_-]", "", value)

    return result.lower()[:50]


def sanitize_iso_code(value: str) -> str:
    """
    Sanitize an ISO language code.

    - Only allows letters and hyphens
    - Lowercase
    - Max 10 characters (e.g., "gem-pro" for Proto-Germanic)
    """
    if not value:
        return ""

    result = re.sub(r"[^a-zA-Z-]", "", value)
    return result.lower()[:10]


def sanitize_year(value: Any) -> int | None:
    """
    Sanitize a year value.

    - Accepts int or string
    - Handles BCE notation
    - Returns None for invalid input
    """
    if value is None:
        return None

    if isinstance(value, int):
        return value

    if isinstance(value, str):
        value = value.strip().upper()

        # Handle BCE/BC notation
        bce_match = re.match(r"(\d+)\s*(BCE|BC)", value)
        if bce_match:
            return -int(bce_match.group(1))

        # Handle plain numbers or CE/AD
        ce_match = re.match(r"(\d+)\s*(CE|AD)?$", value)
        if ce_match:
            return int(ce_match.group(1))

    return None


def sanitize_list(
    values: list[Any], sanitizer: callable = sanitize_string, max_items: int = 100
) -> list[Any]:
    """
    Sanitize a list of values.

    - Applies sanitizer to each item
    - Removes empty/None values
    - Limits to max_items
    """
    if not values:
        return []

    result = []
    for item in values[:max_items]:
        if item is not None:
            sanitized = sanitizer(item) if isinstance(item, str) else item
            if sanitized:
                result.append(sanitized)

    return result


# =============================================================================
# Validation Functions
# =============================================================================


def is_valid_iso639_3(code: str) -> bool:
    """Check if a string is a valid ISO 639-3 language code format."""
    if not code:
        return False
    # ISO 639-3 codes are exactly 3 lowercase letters, or extended like "gem-pro"
    return bool(re.match(r"^[a-z]{3}(-[a-z]{2,4})?$", code.lower()))


def is_valid_uuid(value: str) -> bool:
    """Check if a string is a valid UUID format."""
    uuid_pattern = re.compile(
        r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
    )
    return bool(uuid_pattern.match(value))


def is_valid_year_range(start: int | None, end: int | None) -> bool:
    """Check if a year range is valid."""
    if start is None or end is None:
        return True  # Partial ranges are allowed
    return start <= end


def is_valid_confidence(value: float) -> bool:
    """Check if a confidence value is in valid range [0, 1]."""
    return 0.0 <= value <= 1.0


def is_safe_string(value: str) -> bool:
    """Check if a string is safe (no potential injection patterns)."""
    # Check for common injection patterns
    dangerous_patterns = [
        r"<script",  # XSS
        r"javascript:",  # XSS
        r"on\w+=",  # Event handlers
        r"--",  # SQL comment
        r";.*--",  # SQL injection
        r"\$\{",  # Template injection
        r"\{\{",  # Template injection
    ]

    value_lower = value.lower()
    for pattern in dangerous_patterns:
        if re.search(pattern, value_lower):
            return False

    return True


# =============================================================================
# Pydantic Validators (reusable)
# =============================================================================


class SanitizedString(str):
    """A string type that is automatically sanitized."""

    @classmethod
    def __get_validators__(cls):
        yield cls.validate

    @classmethod
    def validate(cls, v: Any) -> str:
        if not isinstance(v, str):
            v = str(v)
        return sanitize_string(v)


class ISOLanguageCode(str):
    """A validated ISO 639-3 language code."""

    @classmethod
    def __get_validators__(cls):
        yield cls.validate

    @classmethod
    def validate(cls, v: Any) -> str:
        if not isinstance(v, str):
            raise ValueError("Language code must be a string")

        v = sanitize_iso_code(v)
        if not is_valid_iso639_3(v):
            raise ValueError(f"Invalid ISO 639-3 language code: {v}")

        return v


# =============================================================================
# API Request Validation Models
# =============================================================================


class SearchRequest(BaseModel):
    """Validated search request parameters."""

    query: str = Field(..., min_length=1, max_length=500)
    language: str | None = Field(default=None, max_length=10)
    date_start: int | None = Field(default=None, ge=-10000, le=3000)
    date_end: int | None = Field(default=None, ge=-10000, le=3000)
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)

    @field_validator("query")
    @classmethod
    def sanitize_query(cls, v: str) -> str:
        return sanitize_string(v, max_length=500)

    @field_validator("language")
    @classmethod
    def sanitize_language(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return sanitize_iso_code(v)


class TextAnalysisRequest(BaseModel):
    """Validated text analysis request."""

    text: str = Field(..., min_length=1, max_length=50000)
    language: str = Field(..., min_length=2, max_length=10)
    options: dict[str, Any] = Field(default_factory=dict)

    @field_validator("text")
    @classmethod
    def sanitize_text(cls, v: str) -> str:
        # Don't escape HTML in text to analyze, but do normalize whitespace
        return " ".join(v.split())

    @field_validator("language")
    @classmethod
    def validate_language(cls, v: str) -> str:
        v = sanitize_iso_code(v)
        if not v:
            raise ValueError("Language code is required")
        return v


class DateTextRequest(BaseModel):
    """Validated request for text dating analysis."""

    text: str = Field(..., min_length=10, max_length=100000)
    language: str = Field(..., min_length=2, max_length=10)

    @field_validator("text")
    @classmethod
    def validate_text(cls, v: str) -> str:
        v = " ".join(v.split())
        if len(v) < 10:
            raise ValueError("Text must be at least 10 characters")
        return v

    @field_validator("language")
    @classmethod
    def validate_language(cls, v: str) -> str:
        return sanitize_iso_code(v)


class AnachronismRequest(BaseModel):
    """Validated request for anachronism detection."""

    text: str = Field(..., min_length=10, max_length=100000)
    claimed_date: int = Field(..., ge=-10000, le=3000)
    language: str = Field(..., min_length=2, max_length=10)

    @field_validator("text")
    @classmethod
    def validate_text(cls, v: str) -> str:
        return " ".join(v.split())

    @field_validator("language")
    @classmethod
    def validate_language(cls, v: str) -> str:
        return sanitize_iso_code(v)


class LSRCreateRequest(BaseModel):
    """Validated request for creating an LSR."""

    form_orthographic: str = Field(..., min_length=1, max_length=200)
    form_phonetic: str = Field(default="", max_length=200)
    language_code: str = Field(..., min_length=2, max_length=10)
    definition_primary: str = Field(default="", max_length=2000)
    date_start: int | None = Field(default=None, ge=-10000, le=3000)
    date_end: int | None = Field(default=None, ge=-10000, le=3000)

    @field_validator("form_orthographic")
    @classmethod
    def sanitize_form(cls, v: str) -> str:
        return sanitize_string(v, max_length=200)

    @field_validator("language_code")
    @classmethod
    def validate_language(cls, v: str) -> str:
        v = sanitize_iso_code(v)
        if not is_valid_iso639_3(v):
            raise ValueError(f"Invalid language code: {v}")
        return v

    @field_validator("definition_primary")
    @classmethod
    def sanitize_definition(cls, v: str) -> str:
        return sanitize_string(v, max_length=2000)


class GraphQueryRequest(BaseModel):
    """Validated request for graph queries."""

    query: str = Field(..., min_length=1, max_length=5000)
    parameters: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: int = Field(default=30, ge=1, le=300)

    @field_validator("query")
    @classmethod
    def validate_query(cls, v: str) -> str:
        # Basic injection protection for Cypher queries
        dangerous = ["DETACH DELETE", "DROP", "CREATE INDEX", "CREATE CONSTRAINT"]
        v_upper = v.upper()
        for pattern in dangerous:
            if pattern in v_upper:
                raise ValueError(f"Query contains disallowed operation: {pattern}")
        return v
