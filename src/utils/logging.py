"""Comprehensive logging utilities for Linguistic Stratigraphy.

Provides structured logging with:
- Request correlation IDs for tracing
- JSON formatting for production environments
- Performance timing utilities
- Context-aware logging
- Configurable log levels per component
"""

import contextvars
import functools
import json
import logging
import os
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, TypeVar
from uuid import uuid4


# Context variable for request correlation ID
request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="")

# Type variable for decorators
F = TypeVar("F", bound=Callable[..., Any])


class RequestIdFilter(logging.Filter):
    """Logging filter that adds request_id to log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Add request_id to the log record."""
        record.request_id = request_id_var.get() or "-"
        return True


class JsonFormatter(logging.Formatter):
    """JSON formatter for structured logging in production."""

    def __init__(
        self,
        include_timestamp: bool = True,
        include_level: bool = True,
        include_logger: bool = True,
        include_request_id: bool = True,
        extra_fields: dict[str, Any] | None = None,
    ):
        super().__init__()
        self.include_timestamp = include_timestamp
        self.include_level = include_level
        self.include_logger = include_logger
        self.include_request_id = include_request_id
        self.extra_fields = extra_fields or {}

    def format(self, record: logging.LogRecord) -> str:
        """Format the log record as JSON."""
        log_data: dict[str, Any] = {}

        if self.include_timestamp:
            log_data["timestamp"] = datetime.now(UTC).isoformat()

        if self.include_level:
            log_data["level"] = record.levelname

        if self.include_logger:
            log_data["logger"] = record.name

        if self.include_request_id:
            log_data["request_id"] = getattr(record, "request_id", "-")

        log_data["message"] = record.getMessage()

        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        # Add extra fields from record
        for key in ["duration_ms", "operation", "component", "user_id", "resource_id"]:
            if hasattr(record, key):
                log_data[key] = getattr(record, key)

        # Add configured extra fields
        log_data.update(self.extra_fields)

        return json.dumps(log_data)


class ColoredFormatter(logging.Formatter):
    """Colored formatter for console output in development."""

    COLORS = {
        "DEBUG": "\033[36m",  # Cyan
        "INFO": "\033[32m",  # Green
        "WARNING": "\033[33m",  # Yellow
        "ERROR": "\033[31m",  # Red
        "CRITICAL": "\033[35m",  # Magenta
    }
    RESET = "\033[0m"

    def __init__(self, fmt: str | None = None, datefmt: str | None = None):
        super().__init__(fmt, datefmt)

    def format(self, record: logging.LogRecord) -> str:
        """Format with colors for terminal output."""
        color = self.COLORS.get(record.levelname, "")
        record.levelname = f"{color}{record.levelname}{self.RESET}"
        return super().format(record)


def setup_logging(
    level: str | int = "INFO",
    json_format: bool = False,
    log_file: str | None = None,
    component_levels: dict[str, str] | None = None,
) -> None:
    """
    Configure logging for the application.

    Args:
        level: Default log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        json_format: Use JSON formatting (for production)
        log_file: Optional file path for file logging
        component_levels: Dict of logger names to their specific levels
    """
    # Convert string level to int if needed
    if isinstance(level, str):
        level = getattr(logging, level.upper(), logging.INFO)

    # Get root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Clear existing handlers
    root_logger.handlers.clear()

    # Create request ID filter
    request_filter = RequestIdFilter()

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.addFilter(request_filter)

    if json_format:
        console_handler.setFormatter(JsonFormatter())
    else:
        # Development format with colors
        fmt = "%(asctime)s | %(levelname)-8s | %(name)s | [%(request_id)s] %(message)s"
        datefmt = "%Y-%m-%d %H:%M:%S"
        if sys.stdout.isatty():
            console_handler.setFormatter(ColoredFormatter(fmt, datefmt))
        else:
            console_handler.setFormatter(logging.Formatter(fmt, datefmt))

    root_logger.addHandler(console_handler)

    # File handler if specified
    if log_file:
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(level)
        file_handler.addFilter(request_filter)
        file_handler.setFormatter(JsonFormatter())
        root_logger.addHandler(file_handler)

    # Set component-specific levels
    if component_levels:
        for logger_name, logger_level in component_levels.items():
            component_logger = logging.getLogger(logger_name)
            component_logger.setLevel(getattr(logging, logger_level.upper(), logging.INFO))

    # Suppress noisy third-party loggers
    for noisy_logger in ["urllib3", "asyncio", "aiohttp", "httpx"]:
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger with the application's configuration.

    Args:
        name: Logger name (typically __name__)

    Returns:
        Configured logger instance
    """
    return logging.getLogger(name)


def set_request_id(request_id: str | None = None) -> str:
    """
    Set the request ID for the current context.

    Args:
        request_id: Optional request ID. If None, generates a new UUID.

    Returns:
        The request ID that was set.
    """
    rid = request_id or str(uuid4())[:8]
    request_id_var.set(rid)
    return rid


def get_request_id() -> str:
    """Get the current request ID."""
    return request_id_var.get() or "-"


def clear_request_id() -> None:
    """Clear the current request ID."""
    request_id_var.set("")


class LogContext:
    """Context manager for adding temporary context to logs."""

    def __init__(self, logger: logging.Logger, **context: Any):
        """
        Initialize log context.

        Args:
            logger: Logger to use
            **context: Key-value pairs to add to log messages
        """
        self.logger = logger
        self.context = context

    def __enter__(self) -> "LogContext":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        pass

    def _log(self, level: int, msg: str, *args: Any, **kwargs: Any) -> None:
        """Log with context."""
        extra = kwargs.pop("extra", {})
        extra.update(self.context)
        self.logger.log(level, msg, *args, extra=extra, **kwargs)

    def debug(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self._log(logging.DEBUG, msg, *args, **kwargs)

    def info(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self._log(logging.INFO, msg, *args, **kwargs)

    def warning(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self._log(logging.WARNING, msg, *args, **kwargs)

    def error(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self._log(logging.ERROR, msg, *args, **kwargs)

    def critical(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self._log(logging.CRITICAL, msg, *args, **kwargs)


class Timer:
    """Context manager for timing operations."""

    def __init__(
        self,
        logger: logging.Logger | None = None,
        operation: str = "operation",
        level: int = logging.DEBUG,
        log_start: bool = False,
    ):
        """
        Initialize timer.

        Args:
            logger: Logger to use for output
            operation: Name of the operation being timed
            level: Log level for timing messages
            log_start: Whether to log when timing starts
        """
        self.logger = logger
        self.operation = operation
        self.level = level
        self.log_start = log_start
        self.start_time: float = 0
        self.end_time: float = 0
        self.duration_ms: float = 0

    def __enter__(self) -> "Timer":
        self.start_time = time.perf_counter()
        if self.log_start and self.logger:
            self.logger.log(self.level, f"Starting {self.operation}")
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.end_time = time.perf_counter()
        self.duration_ms = (self.end_time - self.start_time) * 1000

        if self.logger:
            status = "failed" if exc_type else "completed"
            self.logger.log(
                self.level,
                f"{self.operation} {status}",
                extra={"duration_ms": round(self.duration_ms, 2), "operation": self.operation},
            )


def log_timing(
    logger: logging.Logger | None = None,
    operation: str | None = None,
    level: int = logging.DEBUG,
) -> Callable[[F], F]:
    """
    Decorator to log function execution time.

    Args:
        logger: Logger to use. If None, creates one from function module.
        operation: Operation name. If None, uses function name.
        level: Log level for timing messages.

    Returns:
        Decorated function.
    """

    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            nonlocal logger, operation
            _logger = logger or logging.getLogger(func.__module__)
            _operation = operation or func.__name__

            with Timer(_logger, _operation, level):
                return func(*args, **kwargs)

        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            nonlocal logger, operation
            _logger = logger or logging.getLogger(func.__module__)
            _operation = operation or func.__name__

            with Timer(_logger, _operation, level):
                return await func(*args, **kwargs)

        import asyncio

        if asyncio.iscoroutinefunction(func):
            return async_wrapper  # type: ignore
        return wrapper  # type: ignore

    return decorator


def log_call(
    logger: logging.Logger | None = None,
    level: int = logging.DEBUG,
    log_args: bool = True,
    log_result: bool = False,
    max_arg_length: int = 100,
) -> Callable[[F], F]:
    """
    Decorator to log function calls with arguments.

    Args:
        logger: Logger to use. If None, creates one from function module.
        level: Log level for call messages.
        log_args: Whether to log function arguments.
        log_result: Whether to log function result.
        max_arg_length: Maximum length for argument string representation.

    Returns:
        Decorated function.
    """

    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            nonlocal logger
            _logger = logger or logging.getLogger(func.__module__)

            # Build argument string
            if log_args:
                arg_strs = [_truncate(repr(a), max_arg_length) for a in args]
                kwarg_strs = [f"{k}={_truncate(repr(v), max_arg_length)}" for k, v in kwargs.items()]
                all_args = ", ".join(arg_strs + kwarg_strs)
                _logger.log(level, f"Calling {func.__name__}({all_args})")
            else:
                _logger.log(level, f"Calling {func.__name__}")

            result = func(*args, **kwargs)

            if log_result:
                _logger.log(level, f"{func.__name__} returned: {_truncate(repr(result), max_arg_length)}")

            return result

        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            nonlocal logger
            _logger = logger or logging.getLogger(func.__module__)

            if log_args:
                arg_strs = [_truncate(repr(a), max_arg_length) for a in args]
                kwarg_strs = [f"{k}={_truncate(repr(v), max_arg_length)}" for k, v in kwargs.items()]
                all_args = ", ".join(arg_strs + kwarg_strs)
                _logger.log(level, f"Calling {func.__name__}({all_args})")
            else:
                _logger.log(level, f"Calling {func.__name__}")

            result = await func(*args, **kwargs)

            if log_result:
                _logger.log(level, f"{func.__name__} returned: {_truncate(repr(result), max_arg_length)}")

            return result

        import asyncio

        if asyncio.iscoroutinefunction(func):
            return async_wrapper  # type: ignore
        return wrapper  # type: ignore

    return decorator


def _truncate(s: str, max_length: int) -> str:
    """Truncate string to max length with ellipsis."""
    if len(s) <= max_length:
        return s
    return s[: max_length - 3] + "..."


# Initialize logging from environment on module import
_log_level = os.getenv("LOG_LEVEL", "INFO")
_log_format = os.getenv("LOG_FORMAT", "text")  # "text" or "json"
_log_file = os.getenv("LOG_FILE")

# Auto-setup if not already configured
if not logging.getLogger().handlers:
    setup_logging(
        level=_log_level,
        json_format=_log_format.lower() == "json",
        log_file=_log_file,
    )
