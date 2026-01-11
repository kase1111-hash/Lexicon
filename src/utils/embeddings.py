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
        """Reduce embedding dimensions for visualization."""
        # TODO: Implement dimensionality reduction (PCA, t-SNE, UMAP)
        return [[0.0] * target_dim for _ in vectors]

    @staticmethod
    def normalize(vector: list[float]) -> list[float]:
        """Normalize a vector to unit length."""
        arr = np.array(vector)
        norm = np.linalg.norm(arr)
        if norm == 0:
            return vector
        return (arr / norm).tolist()

    @staticmethod
    def average_embeddings(embeddings: list[list[float]]) -> list[float]:
        """Calculate average of multiple embeddings."""
        if not embeddings:
            return []
        arr = np.array(embeddings)
        return np.mean(arr, axis=0).tolist()
