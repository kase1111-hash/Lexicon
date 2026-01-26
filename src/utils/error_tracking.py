"""Error tracking and monitoring integrations.

Provides integrations with:
- Sentry for error tracking and alerting
- Elasticsearch for centralized log aggregation (ELK stack)
- Custom error handlers for notifications
"""

import atexit
import logging
import os
import queue
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from src.utils.logging import get_logger, get_request_id


logger = get_logger(__name__)


# =============================================================================
# Sentry Integration
# =============================================================================


class SentryIntegration:
    """Sentry error tracking integration."""

    _initialized: bool = False
    _sdk_available: bool = False

    @classmethod
    def init(
        cls,
        dsn: str | None = None,
        environment: str | None = None,
        release: str | None = None,
        traces_sample_rate: float = 0.1,
        profiles_sample_rate: float = 0.1,
        enable_tracing: bool = True,
    ) -> bool:
        """
        Initialize Sentry SDK.

        Args:
            dsn: Sentry DSN (Data Source Name). If None, reads from SENTRY_DSN env var.
            environment: Environment name (e.g., 'production', 'staging').
            release: Application release/version.
            traces_sample_rate: Sample rate for performance traces (0.0 to 1.0).
            profiles_sample_rate: Sample rate for profiling (0.0 to 1.0).
            enable_tracing: Whether to enable performance tracing.

        Returns:
            True if Sentry was initialized successfully, False otherwise.
        """
        if cls._initialized:
            return True

        dsn = dsn or os.getenv("SENTRY_DSN")
        if not dsn:
            logger.info("Sentry DSN not configured, skipping initialization")
            return False

        try:
            import sentry_sdk
            from sentry_sdk.integrations.fastapi import FastApiIntegration
            from sentry_sdk.integrations.logging import LoggingIntegration
            from sentry_sdk.integrations.starlette import StarletteIntegration

            cls._sdk_available = True

            # Configure logging integration to capture errors and above
            logging_integration = LoggingIntegration(
                level=logging.INFO,  # Capture INFO and above as breadcrumbs
                event_level=logging.ERROR,  # Send ERROR and above as events
            )

            sentry_sdk.init(
                dsn=dsn,
                environment=environment or os.getenv("ENVIRONMENT", "development"),
                release=release or os.getenv("APP_VERSION", "0.1.0"),
                traces_sample_rate=traces_sample_rate if enable_tracing else 0.0,
                profiles_sample_rate=profiles_sample_rate if enable_tracing else 0.0,
                integrations=[
                    StarletteIntegration(),
                    FastApiIntegration(),
                    logging_integration,
                ],
                # Don't send PII by default
                send_default_pii=False,
                # Attach stack traces to messages
                attach_stacktrace=True,
                # Before send hook for filtering
                before_send=cls._before_send,
            )

            cls._initialized = True
            logger.info(f"Sentry initialized for environment: {environment or 'development'}")
            return True

        except ImportError:
            logger.warning("sentry-sdk not installed, Sentry integration disabled")
            return False
        except Exception as e:
            logger.error(f"Failed to initialize Sentry: {e}")
            return False

    @classmethod
    def _before_send(cls, event: dict, hint: dict) -> dict | None:
        """
        Filter events before sending to Sentry.

        Use this to scrub sensitive data or filter out unwanted events.
        """
        # Add request ID if available
        request_id = get_request_id()
        if request_id and request_id != "-":
            event.setdefault("tags", {})["request_id"] = request_id

        # Example: Filter out specific exceptions
        if "exc_info" in hint:
            exc_type, exc_value, _ = hint["exc_info"]
            # Don't send 404 errors to Sentry
            if exc_type.__name__ in ("NotFoundError", "LSRNotFoundError"):
                return None

        return event

    @classmethod
    def capture_exception(cls, error: Exception, **context: Any) -> str | None:
        """
        Capture an exception and send to Sentry.

        Args:
            error: The exception to capture.
            **context: Additional context to attach.

        Returns:
            Event ID if captured, None otherwise.
        """
        if not cls._sdk_available:
            return None

        try:
            import sentry_sdk

            with sentry_sdk.push_scope() as scope:
                for key, value in context.items():
                    scope.set_extra(key, value)

                request_id = get_request_id()
                if request_id and request_id != "-":
                    scope.set_tag("request_id", request_id)

                return sentry_sdk.capture_exception(error)
        except Exception as e:
            logger.error(f"Failed to capture exception in Sentry: {e}")
            return None

    @classmethod
    def capture_message(
        cls, message: str, level: str = "info", **context: Any
    ) -> str | None:
        """
        Capture a message and send to Sentry.

        Args:
            message: The message to capture.
            level: Log level (debug, info, warning, error, fatal).
            **context: Additional context to attach.

        Returns:
            Event ID if captured, None otherwise.
        """
        if not cls._sdk_available:
            return None

        try:
            import sentry_sdk

            with sentry_sdk.push_scope() as scope:
                for key, value in context.items():
                    scope.set_extra(key, value)

                return sentry_sdk.capture_message(message, level=level)
        except Exception as e:
            logger.error(f"Failed to capture message in Sentry: {e}")
            return None

    @classmethod
    def set_user(cls, user_id: str, email: str | None = None, **extra: Any) -> None:
        """Set the current user context for Sentry."""
        if not cls._sdk_available:
            return

        try:
            import sentry_sdk

            sentry_sdk.set_user({"id": user_id, "email": email, **extra})
        except Exception as e:
            logger.debug(f"Failed to set Sentry user context: {e}")

    @classmethod
    def add_breadcrumb(
        cls,
        message: str,
        category: str = "custom",
        level: str = "info",
        **data: Any,
    ) -> None:
        """Add a breadcrumb for debugging."""
        if not cls._sdk_available:
            return

        try:
            import sentry_sdk

            sentry_sdk.add_breadcrumb(
                message=message, category=category, level=level, data=data
            )
        except Exception as e:
            logger.debug(f"Failed to add Sentry breadcrumb: {e}")


# =============================================================================
# Elasticsearch/ELK Integration
# =============================================================================


class ElasticsearchHandler(logging.Handler):
    """
    Logging handler that sends logs to Elasticsearch.

    Buffers logs and sends them in batches for efficiency.
    Compatible with ELK (Elasticsearch, Logstash, Kibana) stack.
    """

    def __init__(
        self,
        hosts: list[str] | None = None,
        index_prefix: str = "lexicon-logs",
        buffer_size: int = 100,
        flush_interval: float = 5.0,
        api_key: str | None = None,
        cloud_id: str | None = None,
    ):
        """
        Initialize Elasticsearch handler.

        Args:
            hosts: List of Elasticsearch hosts (e.g., ['http://localhost:9200']).
            index_prefix: Prefix for index names (date will be appended).
            buffer_size: Number of logs to buffer before flushing.
            flush_interval: Seconds between automatic flushes.
            api_key: API key for authentication.
            cloud_id: Elastic Cloud ID for cloud deployments.
        """
        super().__init__()

        self.index_prefix = index_prefix
        self.buffer_size = buffer_size
        self.flush_interval = flush_interval

        self._buffer: queue.Queue = queue.Queue()
        self._client = None
        self._flush_timer: threading.Timer | None = None
        self._lock = threading.Lock()
        self._shutdown = False

        # Initialize client
        self._init_client(hosts, api_key, cloud_id)

        # Start flush timer
        if self._client:
            self._schedule_flush()

        # Register shutdown handler
        atexit.register(self.close)

    def _init_client(
        self,
        hosts: list[str] | None,
        api_key: str | None,
        cloud_id: str | None,
    ) -> None:
        """Initialize Elasticsearch client."""
        hosts = hosts or os.getenv("ELASTICSEARCH_HOSTS", "").split(",")
        hosts = [h.strip() for h in hosts if h.strip()]

        if not hosts and not cloud_id:
            logger.debug("Elasticsearch hosts not configured")
            return

        try:
            from elasticsearch import Elasticsearch

            kwargs: dict[str, Any] = {}

            if cloud_id or os.getenv("ELASTICSEARCH_CLOUD_ID"):
                kwargs["cloud_id"] = cloud_id or os.getenv("ELASTICSEARCH_CLOUD_ID")
            else:
                kwargs["hosts"] = hosts

            if api_key or os.getenv("ELASTICSEARCH_API_KEY"):
                kwargs["api_key"] = api_key or os.getenv("ELASTICSEARCH_API_KEY")

            self._client = Elasticsearch(**kwargs)

            # Test connection
            if self._client.ping():
                logger.info(f"Connected to Elasticsearch: {hosts or cloud_id}")
            else:
                logger.warning("Elasticsearch ping failed")
                self._client = None

        except ImportError:
            logger.warning("elasticsearch package not installed")
        except Exception as e:
            logger.error(f"Failed to connect to Elasticsearch: {e}")
            self._client = None

    def emit(self, record: logging.LogRecord) -> None:
        """Emit a log record."""
        if self._shutdown or not self._client:
            return

        try:
            doc = self._format_record(record)
            self._buffer.put(doc)

            if self._buffer.qsize() >= self.buffer_size:
                self._flush()
        except Exception:
            self.handleError(record)

    def _format_record(self, record: logging.LogRecord) -> dict:
        """Format log record as Elasticsearch document."""
        doc = {
            "@timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": self.format(record),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        # Add request ID if available
        if hasattr(record, "request_id"):
            doc["request_id"] = record.request_id

        # Add extra fields
        for key in ["duration_ms", "operation", "component", "user_id", "resource_id"]:
            if hasattr(record, key):
                doc[key] = getattr(record, key)

        # Add exception info
        if record.exc_info:
            doc["exception"] = {
                "type": record.exc_info[0].__name__ if record.exc_info[0] else None,
                "message": str(record.exc_info[1]) if record.exc_info[1] else None,
                "stacktrace": self.formatter.formatException(record.exc_info)
                if self.formatter
                else None,
            }

        return doc

    def _schedule_flush(self) -> None:
        """Schedule the next flush."""
        if self._shutdown:
            return

        self._flush_timer = threading.Timer(self.flush_interval, self._timed_flush)
        self._flush_timer.daemon = True
        self._flush_timer.start()

    def _timed_flush(self) -> None:
        """Flush on timer."""
        self._flush()
        self._schedule_flush()

    def _flush(self) -> None:
        """Flush buffered logs to Elasticsearch."""
        if not self._client or self._buffer.empty():
            return

        with self._lock:
            docs = []
            while not self._buffer.empty() and len(docs) < self.buffer_size * 2:
                try:
                    docs.append(self._buffer.get_nowait())
                except queue.Empty:
                    break

            if not docs:
                return

            try:
                # Build bulk request
                index_name = f"{self.index_prefix}-{datetime.now(UTC).strftime('%Y.%m.%d')}"
                actions = []
                for doc in docs:
                    actions.append({"index": {"_index": index_name}})
                    actions.append(doc)

                self._client.bulk(body=actions, refresh=False)
            except Exception as e:
                logger.error(f"Failed to flush logs to Elasticsearch: {e}")
                # Re-queue failed docs
                for doc in docs:
                    self._buffer.put(doc)

    def close(self) -> None:
        """Close the handler."""
        self._shutdown = True

        if self._flush_timer:
            self._flush_timer.cancel()

        self._flush()

        if self._client:
            self._client.close()

        super().close()


# =============================================================================
# Error Notification System
# =============================================================================


class ErrorNotifier:
    """
    Centralized error notification system.

    Supports multiple notification channels and provides
    rate limiting to prevent notification storms.
    """

    _handlers: list[Callable[[Exception, dict], None]] | None = None
    _rate_limit_window: float = 60.0  # seconds
    _rate_limit_count: int = 10  # max notifications per window
    _recent_errors: list[float] | None = None
    _lock = threading.Lock()

    @classmethod
    def _get_handlers(cls) -> list[Callable[[Exception, dict], None]]:
        """Get the handlers list, initializing if needed."""
        if cls._handlers is None:
            cls._handlers = []
        return cls._handlers

    @classmethod
    def _get_recent_errors(cls) -> list[float]:
        """Get the recent errors list, initializing if needed."""
        if cls._recent_errors is None:
            cls._recent_errors = []
        return cls._recent_errors

    @classmethod
    def register_handler(cls, handler: Callable[[Exception, dict], None]) -> None:
        """
        Register an error notification handler.

        Args:
            handler: Callable that takes (exception, context_dict).
        """
        cls._get_handlers().append(handler)

    @classmethod
    def notify(cls, error: Exception, **context: Any) -> None:
        """
        Notify all registered handlers of an error.

        Applies rate limiting to prevent notification storms.

        Args:
            error: The exception that occurred.
            **context: Additional context about the error.
        """
        if not cls._should_notify():
            return

        # Add common context
        context.setdefault("timestamp", datetime.now(UTC).isoformat())
        context.setdefault("request_id", get_request_id())
        context.setdefault("error_type", type(error).__name__)
        context.setdefault("error_message", str(error))

        for handler in cls._get_handlers():
            try:
                handler(error, context)
            except Exception as e:
                logger.error(f"Error notification handler failed: {e}")

    @classmethod
    def _should_notify(cls) -> bool:
        """Check if we should send a notification (rate limiting)."""
        now = datetime.now(UTC).timestamp()

        with cls._lock:
            recent_errors = cls._get_recent_errors()
            # Remove old entries
            cls._recent_errors = [
                t for t in recent_errors if now - t < cls._rate_limit_window
            ]

            if len(cls._recent_errors) >= cls._rate_limit_count:
                return False

            cls._recent_errors.append(now)
            return True


# =============================================================================
# Convenience Functions
# =============================================================================


def init_error_tracking(
    sentry_dsn: str | None = None,
    elasticsearch_hosts: list[str] | None = None,
    environment: str | None = None,
) -> None:
    """
    Initialize all error tracking integrations.

    Args:
        sentry_dsn: Sentry DSN (or set SENTRY_DSN env var).
        elasticsearch_hosts: Elasticsearch hosts (or set ELASTICSEARCH_HOSTS env var).
        environment: Environment name.
    """
    # Initialize Sentry
    SentryIntegration.init(dsn=sentry_dsn, environment=environment)

    # Add Elasticsearch handler to root logger
    if elasticsearch_hosts or os.getenv("ELASTICSEARCH_HOSTS"):
        es_handler = ElasticsearchHandler(hosts=elasticsearch_hosts)
        es_handler.setLevel(logging.WARNING)  # Only send warnings and above
        logging.getLogger().addHandler(es_handler)
        logger.info("Elasticsearch logging handler added")


def capture_error(error: Exception, **context: Any) -> None:
    """
    Capture an error with all configured integrations.

    Args:
        error: The exception to capture.
        **context: Additional context.
    """
    # Log the error
    logger.error(f"Error captured: {error}", exc_info=error, extra=context)

    # Send to Sentry
    SentryIntegration.capture_exception(error, **context)

    # Notify handlers
    ErrorNotifier.notify(error, **context)
