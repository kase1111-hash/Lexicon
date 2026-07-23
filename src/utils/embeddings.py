"""Embedding utilities."""

import numpy as np


class EmbeddingUtils:
    """Utilities for working with embeddings."""

    @staticmethod
    def cosine_similarity(vec1: list[float], vec2: list[float]) -> float:
        """Calculate cosine similarity between two vectors."""
        a = np.array(vec1)
        b = np.array(vec2)
        return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))

    @staticmethod
    def euclidean_distance(vec1: list[float], vec2: list[float]) -> float:
        """Calculate Euclidean distance between two vectors."""
        a = np.array(vec1)
        b = np.array(vec2)
        return float(np.linalg.norm(a - b))

    @staticmethod
    def reduce_dimensions(
        vectors: list[list[float]], target_dim: int = 2, method: str = "pca"
    ) -> list[list[float]]:
        """Reduce embedding dimensions for visualization.

        Uses principal component analysis (via SVD on the mean-centered
        matrix): each output row is the input vector's coordinates along
        the top `target_dim` principal components. When the data has fewer
        meaningful components than target_dim (e.g. a single vector), the
        remaining coordinates are zero.

        Raises:
            ValueError: If method is not 'pca', or vectors have unequal
                lengths.
        """
        if method != "pca":
            raise ValueError(f"Unsupported reduction method '{method}'; supported: pca")
        if not vectors:
            return []
        if len({len(v) for v in vectors}) > 1:
            raise ValueError("All vectors must have the same dimension")

        matrix = np.array(vectors, dtype=float)
        if matrix.shape[1] <= target_dim:
            # Already at or below target dimensionality; pad with zeros
            padded = np.zeros((matrix.shape[0], target_dim))
            padded[:, : matrix.shape[1]] = matrix
            return [row.tolist() for row in padded]

        centered = matrix - matrix.mean(axis=0)
        # SVD of the centered matrix: principal axes are the rows of vt
        _, _, vt = np.linalg.svd(centered, full_matrices=False)
        components = vt[:target_dim]
        projected = centered @ components.T

        # Pad if there were fewer samples than target_dim components
        if projected.shape[1] < target_dim:
            padded = np.zeros((projected.shape[0], target_dim))
            padded[:, : projected.shape[1]] = projected
            projected = padded

        return [row.tolist() for row in projected]

    @staticmethod
    def normalize(vector: list[float]) -> list[float]:
        """Normalize a vector to unit length."""
        arr = np.array(vector)
        norm = np.linalg.norm(arr)
        if norm == 0:
            return vector
        normalized: list[float] = (arr / norm).tolist()
        return normalized

    @staticmethod
    def average_embeddings(embeddings: list[list[float]]) -> list[float]:
        """Calculate average of multiple embeddings."""
        if not embeddings:
            return []
        arr = np.array(embeddings)
        averaged: list[float] = np.mean(arr, axis=0).tolist()
        return averaged
