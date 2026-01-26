"""Common utility functions used across the application."""

import hashlib
import re
from typing import Any, TypeVar


T = TypeVar("T")


def generate_content_hash(content: str) -> str:
    """Generate a SHA256 hash of content for deduplication."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def chunk_list(items: list[T], chunk_size: int) -> list[list[T]]:
    """Split a list into chunks of specified size."""
    return [items[i : i + chunk_size] for i in range(0, len(items), chunk_size)]


def flatten_list(nested: list[list[T]]) -> list[T]:
    """Flatten a nested list into a single list."""
    return [item for sublist in nested for item in sublist]


def deduplicate_preserve_order(items: list[T]) -> list[T]:
    """Remove duplicates from a list while preserving order."""
    seen: set[Any] = set()
    result: list[T] = []
    for item in items:
        # Use hash for hashable items, id for unhashable
        try:
            key = hash(item)
        except TypeError:
            key = id(item)

        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def safe_get(d: dict[str, Any], *keys: str, default: Any = None) -> Any:
    """Safely get a nested value from a dictionary."""
    result = d
    for key in keys:
        if isinstance(result, dict):
            result = result.get(key, default)
        else:
            return default
    return result


def normalize_whitespace(text: str) -> str:
    """Normalize whitespace in text (collapse multiple spaces, strip)."""
    return re.sub(r"\s+", " ", text).strip()


def truncate_string(text: str, max_length: int, suffix: str = "...") -> str:
    """Truncate a string to max_length, adding suffix if truncated."""
    if len(text) <= max_length:
        return text
    return text[: max_length - len(suffix)] + suffix


def parse_year(year_str: str) -> int | None:
    """
    Parse a year string, handling BCE notation.

    Examples:
        "1500" -> 1500
        "500 BCE" -> -500
        "200 BC" -> -200
    """
    if not year_str:
        return None

    year_str = year_str.strip().upper()

    # Check for BCE/BC notation
    bce_match = re.match(r"(\d+)\s*(BCE|BC)", year_str)
    if bce_match:
        return -int(bce_match.group(1))

    # Check for CE/AD notation
    ce_match = re.match(r"(\d+)\s*(CE|AD)?", year_str)
    if ce_match:
        return int(ce_match.group(1))

    return None


def year_to_string(year: int | None) -> str:
    """Convert a year integer to string, handling BCE."""
    if year is None:
        return "unknown"
    if year < 0:
        return f"{abs(year)} BCE"
    return str(year)


def calculate_overlap_ratio(
    start1: int | None,
    end1: int | None,
    start2: int | None,
    end2: int | None,
) -> float:
    """
    Calculate the overlap ratio between two date ranges.

    Returns a value between 0.0 (no overlap) and 1.0 (complete overlap).
    """
    if any(x is None for x in [start1, end1, start2, end2]):
        return 0.5  # Unknown, return neutral

    # Ensure proper ordering
    if start1 > end1:
        start1, end1 = end1, start1
    if start2 > end2:
        start2, end2 = end2, start2

    # Calculate overlap
    overlap_start = max(start1, start2)
    overlap_end = min(end1, end2)

    if overlap_start > overlap_end:
        return 0.0  # No overlap

    overlap_length = overlap_end - overlap_start
    min_range_length = min(end1 - start1, end2 - start2)

    if min_range_length == 0:
        return 1.0 if overlap_length == 0 else 0.0

    return overlap_length / min_range_length


def merge_dicts_deep(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """
    Deep merge two dictionaries.

    Values from override take precedence. Nested dicts are merged recursively.
    """
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = merge_dicts_deep(result[key], value)
        else:
            result[key] = value
    return result


class Singleton:
    """
    Singleton base class for creating singleton instances.

    Usage:
        class MyClass(Singleton):
            pass
    """

    _instances: dict[type, Any] | None = None

    @classmethod
    def _get_instances(cls) -> dict[type, Any]:
        """Get the instances dict, initializing if needed."""
        if Singleton._instances is None:
            Singleton._instances = {}
        return Singleton._instances

    def __new__(cls, *args: Any, **kwargs: Any) -> Any:
        instances = cls._get_instances()
        if cls not in instances:
            instances[cls] = super().__new__(cls)
        return instances[cls]
