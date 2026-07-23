"""Embedding pipeline for generating semantic vectors.

Embeddings are produced by a deterministic hashed n-gram encoder: each
token and character trigram of the input text is hashed (BLAKE2b) into a
stable pseudo-random unit vector, and the vectors are summed and
L2-normalized. This gives real similarity structure (texts sharing
vocabulary are close in cosine space) with no model download, and the
encoder is deliberately isolated behind generate_embedding() so it can be
swapped for a transformer model without touching callers.
"""

import hashlib
import logging
import math
import re
from collections.abc import Mapping
from uuid import UUID

from src.models.lsr import LSR

logger = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Weight given to the time-slice component of a time-aligned embedding.
# Small enough that textual similarity dominates, large enough that the
# same definition in distant time slices is measurably apart.
_TIME_SLICE_WEIGHT = 0.15


class EmbeddingPipeline:
    """Generate time-aware semantic vectors for all LSRs."""

    def __init__(
        self,
        base_model: str = "hashed-ngram-v1",
        dimension: int = 384,
        time_slice_years: int = 50,
        overlap_years: int = 10,
    ):
        self.base_model = base_model
        self.dimension = dimension
        self.time_slice_years = time_slice_years
        self.overlap_years = overlap_years
        self._loaded = False
        self._lsr_store: Mapping[UUID, LSR] = {}
        # Previous vectors, kept so calculate_drift can compare after updates
        self._previous_vectors: dict[UUID, list[float]] = {}
        self._feature_cache: dict[str, list[float]] = {}

    def set_lsr_store(self, store: Mapping[UUID, LSR]) -> None:
        """Set the LSR store that update/retrain operations read and write."""
        self._lsr_store = store

    def load_model(self) -> None:
        """Initialize the encoder (idempotent)."""
        self._loaded = True
        logger.debug(f"Embedding encoder '{self.base_model}' ready (dim={self.dimension})")

    def _feature_vector(self, feature: str) -> list[float]:
        """Derive a stable pseudo-random vector for a feature string.

        Expands a BLAKE2b hash of the feature into `dimension` values in
        [-1, 1]. The same feature always maps to the same vector.
        """
        cached = self._feature_cache.get(feature)
        if cached is not None:
            return cached
        values: list[float] = []
        counter = 0
        while len(values) < self.dimension:
            digest = hashlib.blake2b(
                f"{self.base_model}:{feature}:{counter}".encode(), digest_size=64
            ).digest()
            # Two bytes per value -> uniform in [-1, 1]
            for i in range(0, len(digest) - 1, 2):
                if len(values) >= self.dimension:
                    break
                raw = int.from_bytes(digest[i : i + 2], "big")
                values.append(raw / 32767.5 - 1.0)
            counter += 1
        if len(self._feature_cache) < 100_000:
            self._feature_cache[feature] = values
        return values

    def _features(self, text: str) -> list[str]:
        """Extract token and character-trigram features from text."""
        tokens = _TOKEN_RE.findall(text.lower())
        features = [f"tok:{t}" for t in tokens]
        for token in tokens:
            padded = f"^{token}$"
            features.extend(f"tri:{padded[i : i + 3]}" for i in range(len(padded) - 2))
        return features

    def generate_embedding(self, text: str, time_slice: int | None = None) -> list[float]:
        """Generate an embedding for text, optionally aligned to a time slice.

        Args:
            text: Input text (typically a definition or gloss).
            time_slice: Optional year; embeddings of the same text in
                different time slices differ slightly, so diachronic
                comparisons are meaningful.

        Returns:
            L2-normalized vector of length `self.dimension` (zero vector
            for empty input).
        """
        if not self._loaded:
            self.load_model()

        features = self._features(text)
        if not features:
            return [0.0] * self.dimension

        accumulated = [0.0] * self.dimension
        for feature in features:
            vec = self._feature_vector(feature)
            for i in range(self.dimension):
                accumulated[i] += vec[i]

        if time_slice is not None:
            # Snap the year to its slice so nearby years share a component
            slice_index = time_slice // self.time_slice_years
            slice_vec = self._feature_vector(f"slice:{slice_index}")
            slice_norm = math.sqrt(sum(v * v for v in slice_vec)) or 1.0
            text_norm = math.sqrt(sum(v * v for v in accumulated)) or 1.0
            scale = text_norm * _TIME_SLICE_WEIGHT / slice_norm
            for i in range(self.dimension):
                accumulated[i] += slice_vec[i] * scale

        norm = math.sqrt(sum(v * v for v in accumulated))
        if norm == 0:
            return [0.0] * self.dimension
        return [v / norm for v in accumulated]

    def _lsr_text(self, lsr: LSR) -> str:
        """Assemble the text an LSR's embedding is generated from."""
        parts = [lsr.definition_primary, *lsr.definitions_alternate]
        return " ".join(p for p in parts if p)

    def _lsr_time_slice(self, lsr: LSR) -> int | None:
        """Midpoint year of the LSR's attested range, if dated."""
        if lsr.date_start is None:
            return None
        return (lsr.date_start + (lsr.date_end or lsr.date_start)) // 2

    def embed_lsr(self, lsr: LSR) -> bool:
        """Generate and set semantic_vector on one LSR.

        Returns:
            True if a vector was set, False if the LSR has no text to embed.
        """
        text = self._lsr_text(lsr)
        if not text:
            return False
        if lsr.semantic_vector:
            self._previous_vectors[lsr.id] = list(lsr.semantic_vector)
        lsr.semantic_vector = self.generate_embedding(text, self._lsr_time_slice(lsr))
        return True

    def update_modified(self, lsr_ids: list[UUID]) -> dict:
        """Update embeddings for the given LSRs in the store.

        Returns:
            Dict with 'updated', 'skipped' (no text), and 'failed' counts.
        """
        updated = skipped = failed = 0
        for lsr_id in lsr_ids:
            lsr = self._lsr_store.get(lsr_id)
            if lsr is None:
                failed += 1
                continue
            try:
                if self.embed_lsr(lsr):
                    updated += 1
                else:
                    skipped += 1
            except Exception as e:
                logger.warning(f"Embedding failed for LSR {lsr_id}: {e}")
                failed += 1
        return {"updated": updated, "skipped": skipped, "failed": failed}

    def full_retrain(self) -> dict:
        """Regenerate embeddings for every LSR in the store.

        Returns:
            Dict with 'processed', 'skipped', and 'failed' counts.
        """
        result = self.update_modified(list(self._lsr_store.keys()))
        return {
            "processed": result["updated"],
            "skipped": result["skipped"],
            "failed": result["failed"],
        }

    def calculate_drift(self, lsr_id: UUID) -> float:
        """Calculate semantic drift (cosine distance) from the previous embedding.

        Returns:
            Distance in [0, 1]; 0.0 when there is no previous vector to
            compare against or either vector is empty.
        """
        lsr = self._lsr_store.get(lsr_id)
        previous = self._previous_vectors.get(lsr_id)
        if lsr is None or not previous or not lsr.semantic_vector:
            return 0.0

        current = lsr.semantic_vector
        dot = sum(a * b for a, b in zip(previous, current, strict=False))
        norm_prev = math.sqrt(sum(a * a for a in previous))
        norm_curr = math.sqrt(sum(b * b for b in current))
        if norm_prev == 0 or norm_curr == 0:
            return 0.0
        similarity = dot / (norm_prev * norm_curr)
        return max(0.0, min(1.0, 1.0 - similarity))
