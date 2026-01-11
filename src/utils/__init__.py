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
from .error_tracking import (
    ElasticsearchHandler,
    ErrorNotifier,
    SentryIntegration,
    capture_error,
    init_error_tracking,
)
from .logging import (
    LogContext,
    Timer,
    clear_request_id,
    get_logger,
    get_request_id,
    log_call,
    log_timing,
    set_request_id,
    setup_logging,
)
from .metrics import MetricsCollector, Timer as MetricsTimer, metrics, timed
from .phonetics import PhoneticUtils
from .telemetry import Span, SpanContext, Tracer, get_trace_context, traced, tracer
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
    # Logging
    "setup_logging",
    "get_logger",
    "set_request_id",
    "get_request_id",
    "clear_request_id",
    "LogContext",
    "Timer",
    "log_timing",
    "log_call",
    # Error tracking
    "init_error_tracking",
    "capture_error",
    "SentryIntegration",
    "ElasticsearchHandler",
    "ErrorNotifier",
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
    # Metrics
    "metrics",
    "MetricsCollector",
    "MetricsTimer",
    "timed",
    # Telemetry
    "tracer",
    "Tracer",
    "Span",
    "SpanContext",
    "traced",
    "get_trace_context",
]
