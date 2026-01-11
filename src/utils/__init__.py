"""Utility modules for database connections, embeddings, phonetics, and common helpers."""

from .common import (
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
from .db import DatabaseConfig, DatabaseManager, close_db, get_db
from .embeddings import EmbeddingUtils
from .phonetics import PhoneticUtils

__all__ = [
    # Database
    "DatabaseManager",
    "DatabaseConfig",
    "get_db",
    "close_db",
    # Embeddings
    "EmbeddingUtils",
    # Phonetics
    "PhoneticUtils",
    # Common utilities
    "generate_content_hash",
    "chunk_list",
    "flatten_list",
    "deduplicate_preserve_order",
    "safe_get",
    "normalize_whitespace",
    "truncate_string",
    "parse_year",
    "year_to_string",
    "calculate_overlap_ratio",
    "merge_dicts_deep",
    "Singleton",
]
