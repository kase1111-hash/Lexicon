"""Unit tests for utility modules."""

import pytest

from src.utils.phonetics import PhoneticUtils
from src.utils.embeddings import EmbeddingUtils


class TestPhoneticUtils:
    """Tests for PhoneticUtils."""

    def test_strip_diacritics(self):
        """Test diacritic stripping."""
        result = PhoneticUtils.strip_diacritics("café")
        assert result == "cafe"

    def test_strip_diacritics_complex(self):
        """Test diacritic stripping with complex characters."""
        result = PhoneticUtils.strip_diacritics("naïve résumé")
        assert result == "naive resume"

    def test_levenshtein_distance_equal(self):
        """Test Levenshtein distance for equal strings."""
        result = PhoneticUtils.levenshtein_distance("test", "test")
        assert result == 0

    def test_levenshtein_distance_different(self):
        """Test Levenshtein distance for different strings."""
        result = PhoneticUtils.levenshtein_distance("kitten", "sitting")
        assert result == 3

    def test_levenshtein_distance_empty(self):
        """Test Levenshtein distance with empty string."""
        result = PhoneticUtils.levenshtein_distance("test", "")
        assert result == 4


class TestEmbeddingUtils:
    """Tests for EmbeddingUtils."""

    def test_cosine_similarity_identical(self):
        """Test cosine similarity for identical vectors."""
        vec = [1.0, 0.0, 0.0]
        result = EmbeddingUtils.cosine_similarity(vec, vec)
        assert abs(result - 1.0) < 0.0001

    def test_cosine_similarity_orthogonal(self):
        """Test cosine similarity for orthogonal vectors."""
        vec1 = [1.0, 0.0, 0.0]
        vec2 = [0.0, 1.0, 0.0]
        result = EmbeddingUtils.cosine_similarity(vec1, vec2)
        assert abs(result) < 0.0001

    def test_normalize(self):
        """Test vector normalization."""
        vec = [3.0, 4.0]
        result = EmbeddingUtils.normalize(vec)
        assert abs(result[0] - 0.6) < 0.0001
        assert abs(result[1] - 0.8) < 0.0001

    def test_average_embeddings(self):
        """Test embedding averaging."""
        embeddings = [[1.0, 2.0], [3.0, 4.0]]
        result = EmbeddingUtils.average_embeddings(embeddings)
        assert result == [2.0, 3.0]

    def test_average_embeddings_empty(self):
        """Test embedding averaging with empty input."""
        result = EmbeddingUtils.average_embeddings([])
        assert result == []
