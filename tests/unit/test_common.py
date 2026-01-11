"""Unit tests for common utility functions."""

import pytest

from src.utils.common import (
    Singleton,
    calculate_overlap_ratio,
    chunk_list,
    deduplicate_preserve_order,
    flatten_list,
    generate_content_hash,
    merge_dicts_deep,
    normalize_whitespace,
    parse_year,
    safe_get,
    truncate_string,
    year_to_string,
)


class TestHashingUtils:
    """Tests for hashing utilities."""

    def test_generate_content_hash(self):
        """Test content hash generation."""
        hash1 = generate_content_hash("test content")
        hash2 = generate_content_hash("test content")
        hash3 = generate_content_hash("different content")

        assert hash1 == hash2  # Same content, same hash
        assert hash1 != hash3  # Different content, different hash
        assert len(hash1) == 64  # SHA-256 hex length

    def test_generate_content_hash_empty(self):
        """Test hash generation for empty content."""
        hash_result = generate_content_hash("")
        assert len(hash_result) == 64


class TestListUtils:
    """Tests for list manipulation utilities."""

    def test_chunk_list_even(self):
        """Test chunking a list with even division."""
        items = [1, 2, 3, 4, 5, 6]
        chunks = list(chunk_list(items, 2))
        assert chunks == [[1, 2], [3, 4], [5, 6]]

    def test_chunk_list_uneven(self):
        """Test chunking a list with uneven division."""
        items = [1, 2, 3, 4, 5]
        chunks = list(chunk_list(items, 2))
        assert chunks == [[1, 2], [3, 4], [5]]

    def test_chunk_list_larger_chunk(self):
        """Test chunking when chunk size is larger than list."""
        items = [1, 2, 3]
        chunks = list(chunk_list(items, 10))
        assert chunks == [[1, 2, 3]]

    def test_chunk_list_empty(self):
        """Test chunking an empty list."""
        chunks = list(chunk_list([], 2))
        assert chunks == []

    def test_flatten_list(self):
        """Test flattening nested lists."""
        nested = [[1, 2], [3, 4], [5]]
        assert flatten_list(nested) == [1, 2, 3, 4, 5]

    def test_flatten_list_empty(self):
        """Test flattening empty nested lists."""
        assert flatten_list([]) == []
        assert flatten_list([[]]) == []

    def test_deduplicate_preserve_order(self):
        """Test deduplication while preserving order."""
        items = [1, 2, 3, 2, 4, 1, 5]
        result = deduplicate_preserve_order(items)
        assert result == [1, 2, 3, 4, 5]

    def test_deduplicate_preserve_order_strings(self):
        """Test deduplication with strings."""
        items = ["a", "b", "a", "c", "b"]
        result = deduplicate_preserve_order(items)
        assert result == ["a", "b", "c"]


class TestDictUtils:
    """Tests for dictionary utilities."""

    def test_safe_get_existing(self):
        """Test safe_get with existing key."""
        d = {"a": {"b": {"c": 1}}}
        assert safe_get(d, "a", "b", "c") == 1

    def test_safe_get_missing(self):
        """Test safe_get with missing key."""
        d = {"a": {"b": 1}}
        assert safe_get(d, "a", "c", "d") is None
        assert safe_get(d, "a", "c", "d", default="default") == "default"

    def test_safe_get_empty_path(self):
        """Test safe_get with empty path."""
        d = {"a": 1}
        assert safe_get(d) == d

    def test_merge_dicts_deep(self):
        """Test deep dictionary merging."""
        base = {"a": 1, "b": {"c": 2, "d": 3}}
        override = {"b": {"c": 10, "e": 5}, "f": 6}
        result = merge_dicts_deep(base, override)

        assert result["a"] == 1
        assert result["b"]["c"] == 10  # Overridden
        assert result["b"]["d"] == 3  # Preserved
        assert result["b"]["e"] == 5  # Added
        assert result["f"] == 6  # Added

    def test_merge_dicts_deep_no_mutation(self):
        """Test that merge doesn't mutate originals."""
        base = {"a": {"b": 1}}
        override = {"a": {"c": 2}}
        result = merge_dicts_deep(base, override)

        assert "c" not in base["a"]
        assert result["a"]["c"] == 2


class TestStringUtils:
    """Tests for string utilities."""

    def test_normalize_whitespace(self):
        """Test whitespace normalization."""
        assert normalize_whitespace("  hello   world  ") == "hello world"
        assert normalize_whitespace("\t\ntest\r\n") == "test"

    def test_normalize_whitespace_empty(self):
        """Test whitespace normalization with empty string."""
        assert normalize_whitespace("") == ""
        assert normalize_whitespace("   ") == ""

    def test_truncate_string(self):
        """Test string truncation."""
        assert truncate_string("hello world", 5) == "he..."
        assert truncate_string("hi", 10) == "hi"
        assert truncate_string("hello", 5) == "hello"

    def test_truncate_string_custom_suffix(self):
        """Test truncation with custom suffix."""
        assert truncate_string("hello world", 8, suffix="[...]") == "hel[...]"


class TestDateUtils:
    """Tests for date utilities."""

    def test_parse_year_valid(self):
        """Test parsing valid year strings."""
        assert parse_year("2024") == 2024
        assert parse_year("-500") == -500
        assert parse_year("500 BCE") == -500
        assert parse_year("500 BC") == -500
        assert parse_year("500 CE") == 500
        assert parse_year("500 AD") == 500

    def test_parse_year_invalid(self):
        """Test parsing invalid year strings."""
        assert parse_year("not a year") is None
        assert parse_year("") is None
        assert parse_year(None) is None

    def test_year_to_string_positive(self):
        """Test year to string conversion for CE years."""
        assert year_to_string(2024) == "2024 CE"
        assert year_to_string(500) == "500 CE"

    def test_year_to_string_negative(self):
        """Test year to string conversion for BCE years."""
        assert year_to_string(-500) == "500 BCE"
        assert year_to_string(-1) == "1 BCE"

    def test_year_to_string_zero(self):
        """Test year to string conversion for year 0."""
        assert year_to_string(0) == "0"


class TestOverlapUtils:
    """Tests for overlap calculation utilities."""

    def test_calculate_overlap_ratio_full(self):
        """Test full overlap."""
        assert calculate_overlap_ratio(100, 200, 100, 200) == 1.0

    def test_calculate_overlap_ratio_partial(self):
        """Test partial overlap."""
        ratio = calculate_overlap_ratio(100, 200, 150, 250)
        assert 0.4 < ratio < 0.6  # 50 overlap out of ~150 span

    def test_calculate_overlap_ratio_none(self):
        """Test no overlap."""
        assert calculate_overlap_ratio(100, 200, 300, 400) == 0.0

    def test_calculate_overlap_ratio_contained(self):
        """Test when one range is contained in another."""
        ratio = calculate_overlap_ratio(100, 400, 200, 300)
        assert ratio > 0


class TestSingleton:
    """Tests for Singleton metaclass."""

    def test_singleton_same_instance(self):
        """Test that Singleton always returns same instance."""

        class TestClass(metaclass=Singleton):
            def __init__(self, value):
                self.value = value

        instance1 = TestClass(1)
        instance2 = TestClass(2)

        assert instance1 is instance2
        assert instance1.value == 1  # First initialization wins

    def test_singleton_different_classes(self):
        """Test that different Singleton classes have different instances."""

        class ClassA(metaclass=Singleton):
            pass

        class ClassB(metaclass=Singleton):
            pass

        a = ClassA()
        b = ClassB()

        assert a is not b
