"""Input validation and sanitization utilities."""

import re
import unicodedata
from collections.abc import Callable, Iterator
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.utils.languages import ISO_639_1_TO_3

# The LSR model's year bounds (src/models/lsr.py YEAR_MIN/YEAR_MAX; importing
# them here would be circular)
YEAR_MIN = -10000
YEAR_MAX = 2100

# =============================================================================
# Sanitization Functions
# =============================================================================


def sanitize_string(value: str, max_length: int | None = None) -> str:
    """
    Sanitize a string input.

    - Strips leading/trailing whitespace
    - Normalizes internal whitespace
    - Removes control characters
    - Optionally truncates to max_length

    Note: Does NOT HTML-escape because this is used for linguistic data
    stored in the database, not for direct HTML rendering. Characters like
    '>' and '&' are legitimate in linguistic notation (e.g. "k > tʃ").
    HTML escaping should be applied at the presentation layer instead.
    """
    if not value:
        return ""

    # Strip and normalize whitespace
    result = " ".join(value.split())

    # Remove control characters (but preserve legitimate Unicode)
    result = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", result)

    # Truncate if needed
    if max_length and len(result) > max_length:
        result = result[:max_length]

    return result


def sanitize_identifier(value: str) -> str:
    """
    Sanitize an identifier (e.g., language code, ID).

    - Replaces spaces with underscores
    - Only allows alphanumeric and underscore
    - Converts to lowercase
    - Max 50 characters
    """
    if not value:
        return ""

    # Replace spaces with underscores before stripping
    result = value.replace(" ", "_")

    # Only allow safe characters (alphanumeric and underscore)
    result = re.sub(r"[^a-zA-Z0-9_]", "", result)

    return result.lower()[:50]


def sanitize_iso_code(value: str) -> str:
    """
    Sanitize an ISO language code.

    - Only allows letters and hyphens
    - Lowercase
    - Must be at least 3 characters (ISO 639-3 minimum)
    - Max 20 characters (e.g., "gem-pro" for Proto-Germanic, "ine-bsl-pro")
    - Returns empty string for invalid codes
    """
    if not value:
        return ""

    value = value.strip()
    result = re.sub(r"[^a-zA-Z-]", "", value)
    result = result.lower()[:20]

    # ISO 639-3 codes must be at least 3 characters
    if len(result) < 3:
        return ""

    return result


def normalize_language_code(value: str) -> str:
    """Normalize a user-supplied language code to the codes stored in the graph.

    Accepts ISO 639-3 codes, Wiktionary-style extensions for historical
    varieties and proto-languages ("gem-pro", "la-vul"), Glottolog codes
    (stored for languages without an ISO code, e.g. "yaku1245") and common
    ISO 639-1 codes ("en" -> "eng", from src/utils/languages.py).

    Raises:
        ValueError: If the value is not a usable language code.
    """
    raw = (value or "").strip().lower()
    if raw in ISO_639_1_TO_3:
        return ISO_639_1_TO_3[raw]
    if not is_valid_language_code(raw):
        raise ValueError(
            f"Invalid language code {value!r}: use an ISO 639-3 code such as 'eng' "
            "(or 'gem-pro' for proto-languages, or a Glottolog code such as 'yaku1245')"
        )
    return raw


def sanitize_year(value: Any) -> int | None:
    """
    Sanitize a year value.

    - Accepts int or string
    - Handles BCE notation
    - Returns None for out-of-range or invalid input
    - Valid range: YEAR_MIN to YEAR_MAX (-10000 to 2100)
    """
    if value is None:
        return None

    if isinstance(value, int):
        if not YEAR_MIN <= value <= YEAR_MAX:
            return None
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
    values: list[Any], sanitizer: Callable[[str], str] = sanitize_string, max_items: int = 100
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


# ISO 639-3 ("eng"), Wiktionary extensions ("gem-pro", "la-vul", "roa-opt"),
# or a Glottolog code ("yaku1245"). Wiktionary extensions have 3-4 letters,
# so BCP 47 region tags ("en-gb"), which the graph never stores, are refused.
_LANGUAGE_CODE_RE = re.compile(r"^(?:[a-z]{2,3}(?:-[a-z]{3,4}){1,2}|[a-z]{3}|[a-z]{4}\d{4})$")


def is_valid_language_code(code: str) -> bool:
    """Check if a string is a language code the graph can store."""
    return bool(code) and bool(_LANGUAGE_CODE_RE.match(code))


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
    """Check if a year range is valid (within YEAR_MIN to YEAR_MAX)."""
    if start is None or end is None:
        return True  # Partial ranges are allowed
    if start < YEAR_MIN or end > YEAR_MAX:
        return False  # Out of reasonable bounds
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
        r";\s*drop\s",  # SQL injection (DROP)
        r";\s*delete\s",  # SQL injection (DELETE)
        r";\s*update\s",  # SQL injection (UPDATE)
        r"'\s*or\s+.*=",  # SQL injection (OR-based)
        r"\$\{",  # Template injection
        r"\{\{",  # Template injection
    ]

    value_lower = value.lower()
    return all(not re.search(pattern, value_lower) for pattern in dangerous_patterns)


# =============================================================================
# Pydantic Validators (reusable)
# =============================================================================


class SanitizedString(str):
    """A string type that is automatically sanitized."""

    @classmethod
    def __get_validators__(cls) -> Iterator[Callable[[Any], str]]:
        yield cls.validate

    @classmethod
    def validate(cls, v: Any) -> str:
        if not isinstance(v, str):
            v = str(v)
        return sanitize_string(v)


class ISOLanguageCode(str):
    """A validated ISO 639-3 language code."""

    @classmethod
    def __get_validators__(cls) -> Iterator[Callable[[Any], str]]:
        yield cls.validate

    @classmethod
    def validate(cls, v: Any) -> str:
        if not isinstance(v, str):
            raise ValueError("Language code must be a string")

        code = sanitize_iso_code(v)
        if not is_valid_iso639_3(code):
            raise ValueError(f"Invalid ISO 639-3 language code: {code}")

        return code


# =============================================================================
# API Request Validation Models
# =============================================================================


class SearchRequest(BaseModel):
    """Validated search request parameters."""

    form: str | None = Field(default=None, max_length=200)
    language: str | None = Field(default=None, max_length=20)
    date_start: int | None = Field(default=None, ge=YEAR_MIN, le=YEAR_MAX)
    date_end: int | None = Field(default=None, ge=YEAR_MIN, le=YEAR_MAX)
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)

    @field_validator("form")
    @classmethod
    def sanitize_form(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return sanitize_string(v, max_length=200)

    @field_validator("language")
    @classmethod
    def sanitize_language(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return sanitize_iso_code(v)


class TextAnalysisRequest(BaseModel):
    """Validated text analysis request."""

    text: str = Field(..., min_length=1, max_length=50000)
    language: str = Field(..., min_length=2, max_length=20)
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
    language: str = Field(..., min_length=2, max_length=20)

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
        return normalize_language_code(v)


class AnachronismRequest(BaseModel):
    """Validated request for anachronism detection."""

    text: str = Field(..., min_length=10, max_length=100000)
    claimed_date: int = Field(..., ge=YEAR_MIN, le=YEAR_MAX)
    language: str = Field(..., min_length=2, max_length=20)

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
        return normalize_language_code(v)


class LSRCreateRequest(BaseModel):
    """Validated request for creating an LSR.

    Text fields are sanitized first and validated afterwards, so input that
    sanitizes to nothing (whitespace or control characters only) is rejected
    rather than stored as an empty form. Unknown fields are rejected instead
    of being silently dropped.
    """

    model_config = ConfigDict(extra="forbid")

    form_orthographic: str = Field(..., min_length=1, max_length=200)
    form_phonetic: str = Field(default="", max_length=200)
    language_code: str = Field(..., min_length=2, max_length=20)
    definition_primary: str = Field(default="", max_length=2000)
    # The LSR model's year range (YEAR_MIN/YEAR_MAX in src/models/lsr.py, which
    # cannot be imported here: src.models.lsr imports src.utils)
    date_start: int | None = Field(default=None, ge=-10000, le=2100)
    date_end: int | None = Field(default=None, ge=-10000, le=2100)

    @field_validator("form_orthographic")
    @classmethod
    def sanitize_form(cls, v: str) -> str:
        v = sanitize_string(v, max_length=200)
        if not v:
            raise ValueError("form_orthographic must not be empty")
        if "\ufffd" in v:
            raise ValueError("form_orthographic contains U+FFFD (garbled text encoding)")
        if not any(unicodedata.category(ch).startswith("L") for ch in v):
            raise ValueError("form_orthographic must contain at least one letter")
        return v

    @field_validator("form_phonetic")
    @classmethod
    def sanitize_phonetic(cls, v: str) -> str:
        return sanitize_string(v, max_length=200)

    @field_validator("language_code")
    @classmethod
    def validate_language(cls, v: str) -> str:
        # normalize_language_code rejects codes that would change under
        # sanitization (e.g. "e1n2g"), instead of silently coercing them
        return normalize_language_code(v)

    @field_validator("definition_primary")
    @classmethod
    def sanitize_definition(cls, v: str) -> str:
        return sanitize_string(v, max_length=2000)

    @model_validator(mode="after")
    def check_date_order(self) -> "LSRCreateRequest":
        if not is_valid_year_range(self.date_start, self.date_end):
            raise ValueError("date_end must be >= date_start")
        return self


# =============================================================================
# Read-only Cypher validation (POST /graph/query)
# =============================================================================

# Default and maximum server-side transaction timeout for user queries.
CYPHER_QUERY_DEFAULT_TIMEOUT_SECONDS = 10
CYPHER_QUERY_MAX_TIMEOUT_SECONDS = 30

# Keywords and namespaces that are never allowed in a user query, wherever
# they appear: file/URL access (LOAD CSV), procedures and subqueries (CALL,
# apoc.*, dbms.*), switching databases (USE), batched writes (PERIODIC,
# FOREACH) and administration commands.
_CYPHER_FORBIDDEN_WORDS = frozenset(
    {
        "LOAD",
        "CALL",
        "USE",
        "PERIODIC",
        "FOREACH",
        "APOC",
        "DBMS",
        "SHOW",
        "TERMINATE",
        "GRANT",
        "DENY",
        "REVOKE",
        "ALTER",
    }
)

# Write clauses. The query also runs in a read transaction, so the database
# rejects writes regardless; this check just fails fast with a clear message.
_CYPHER_WRITE_WORDS = frozenset({"CREATE", "MERGE", "SET", "DELETE", "DETACH", "REMOVE", "DROP"})

_CYPHER_READ_STARTS = frozenset(
    {"MATCH", "OPTIONAL", "RETURN", "WITH", "UNWIND", "EXPLAIN", "PROFILE"}
)

# Neo4j decodes Java-style unicode escapes (\u0043, \uu0043) anywhere in the
# query text before it tokenizes, so "\u0043ALL" is CALL and an escaped quote
# or newline ends a string or comment early. The keyword checks read the text
# as written, so such escapes are refused wherever they appear.
_CYPHER_UNICODE_ESCAPE_RE = re.compile(r"\\+[uU]")


def _cypher_words(text: str) -> set[str]:
    """Return every run of letters in ``text``, upper-cased.

    Anything that is not a letter (whitespace, newlines, comments, digits,
    underscores, backticks, quotes, punctuation) separates words, so a
    keyword the Cypher parser recognises always shows up here as a whole
    word. The text is also checked after NFKC normalisation, and words are
    case-folded, so look-alike letters (fullwidth forms, the long s, dotless
    i) cannot smuggle a keyword past the check.
    """
    words: set[str] = set()
    for variant in {text, unicodedata.normalize("NFKC", text)}:
        current: list[str] = []
        for ch in variant + " ":
            if unicodedata.category(ch).startswith("L"):
                current.append(ch)
            elif current:
                words.add("".join(current).casefold().upper())
                current = []
    return words


def strip_cypher_comments_and_strings(query: str) -> str:
    """Return the code of a Cypher query with comments and string literals removed.

    ``//`` and ``/* */`` comments become a space and string literals become
    ``''``; the content of backtick-quoted names is kept (unquoted), since
    those are identifiers such as ``dbms`` in ``CALL `dbms`.listConfig()``.

    Raises:
        ValueError: If a string, comment or quoted name is not terminated.
    """
    out: list[str] = []
    i, n = 0, len(query)
    while i < n:
        ch = query[i]
        pair = query[i : i + 2]
        if pair == "//":
            end = re.search(r"[\r\n]", query[i:])
            i = n if end is None else i + end.start()
            out.append(" ")
        elif pair == "/*":
            end_idx = query.find("*/", i + 2)
            if end_idx == -1:
                raise ValueError("Unterminated /* comment")
            i = end_idx + 2
            out.append(" ")
        elif ch in "'\"":
            j = i + 1
            while j < n and query[j] != ch:
                j += 2 if query[j] == "\\" else 1
            if j >= n:
                raise ValueError("Unterminated string literal")
            i = j + 1
            out.append(" '' ")
        elif ch == "`":
            name: list[str] = []
            j = i + 1
            while True:
                k = query.find("`", j)
                if k == -1:
                    raise ValueError("Unterminated backtick-quoted name")
                if query[k + 1 : k + 2] == "`":  # escaped backtick
                    name.append(query[j : k + 1])
                    j = k + 2
                    continue
                name.append(query[j:k])
                break
            i = k + 1
            out.append(" " + "".join(name) + " ")
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def validate_read_only_cypher(query: str) -> str:
    """Check that a user-supplied Cypher query is a plain read query.

    This is a defence-in-depth filter: callers must still execute the query
    in a read transaction with a timeout. Forbidden keywords are matched as
    whole words on the raw text, so neither whitespace, newlines, comments
    nor backticks can hide them; string literals are not exempt, so pass
    literal values as ``$parameters``.

    Unicode escapes (``\\u0043``) are refused anywhere, and backslashes
    outside string literals, so no keyword can be spelled in a form the
    parser decodes but these checks do not see.

    Returns:
        The query, unchanged.

    Raises:
        ValueError: With a message that is safe to return to the client.
    """
    if _CYPHER_UNICODE_ESCAPE_RE.search(query):
        raise ValueError(
            "Query contains a unicode escape (\\u...); Neo4j decodes these before "
            "parsing, so they are not allowed. Pass literal values as $parameters"
        )
    code = strip_cypher_comments_and_strings(query)
    if "\\" in code:
        raise ValueError("Backslashes are only allowed inside string literals and comments")
    leading = re.match(r"\s*([A-Za-z]*)", code)
    first_word = leading.group(1).upper() if leading else ""
    if first_word not in _CYPHER_READ_STARTS:
        raise ValueError(
            "Query must start with a read-only clause (MATCH, OPTIONAL MATCH, RETURN, "
            "WITH, UNWIND, EXPLAIN or PROFILE)"
        )

    forbidden = sorted((_cypher_words(query) | _cypher_words(code)) & _CYPHER_FORBIDDEN_WORDS)
    if forbidden:
        raise ValueError(
            f"Query contains disallowed keyword(s): {', '.join(forbidden)}. "
            "LOAD CSV, CALL, USE, procedures (dbms.*, apoc.*) and administration "
            "commands are not allowed, even inside string literals or comments; "
            "pass literal values as $parameters"
        )

    writes = sorted(_cypher_words(code) & _CYPHER_WRITE_WORDS)
    if writes:
        raise ValueError(
            f"Query contains disallowed write operation(s): {', '.join(writes)}. "
            "Only read-only queries are allowed"
        )
    return query


class GraphQueryRequest(BaseModel):
    """Validated request for graph queries."""

    query: str = Field(..., min_length=1, max_length=5000)
    parameters: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: int = Field(
        default=CYPHER_QUERY_DEFAULT_TIMEOUT_SECONDS,
        ge=1,
        le=CYPHER_QUERY_MAX_TIMEOUT_SECONDS,
    )

    @field_validator("query")
    @classmethod
    def validate_query(cls, v: str) -> str:
        return validate_read_only_cypher(v)
