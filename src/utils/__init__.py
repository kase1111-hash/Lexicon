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
from .validation import (
    AnachronismRequest,
    DateTextRequest,
    GraphQueryRequest,
    LSRCreateRequest,
    SearchRequest,
    TextAnalysisRequest,
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
    # Validation - sanitizers
    "sanitize_string",
    "sanitize_identifier",
    "sanitize_iso_code",
    "sanitize_year",
    "sanitize_list",
    # Validation - validators
    "is_valid_iso639_3",
    "is_valid_uuid",
    "is_valid_year_range",
    "is_valid_confidence",
    "is_safe_string",
    # Validation - request models
    "SearchRequest",
    "TextAnalysisRequest",
    "DateTextRequest",
    "AnachronismRequest",
    "LSRCreateRequest",
    "GraphQueryRequest",
]
