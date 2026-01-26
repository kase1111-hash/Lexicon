"""
Telemetry and distributed tracing for Linguistic Stratigraphy.

Provides OpenTelemetry-compatible tracing for debugging and performance analysis.

Usage:
    from src.utils.telemetry import tracer, create_span

    # Using context manager
    with tracer.start_span("operation_name") as span:
        span.set_attribute("key", "value")
        # do work

    # Using decorator
    @traced("operation_name")
    def my_function():
        pass
"""

import contextvars
import functools
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, TypeVar

F = TypeVar("F", bound=Callable[..., Any])

# Context variable for current span
_current_span: contextvars.ContextVar["Span | None"] = contextvars.ContextVar(
    "current_span", default=None
)


@dataclass
class SpanContext:
    """Trace and span identifiers for distributed tracing."""

    trace_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    span_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    parent_span_id: str | None = None


@dataclass
class SpanEvent:
    """An event that occurred during a span."""

    name: str
    timestamp: float = field(default_factory=time.time)
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass
class Span:
    """A span representing a unit of work."""

    name: str
    context: SpanContext = field(default_factory=SpanContext)
    start_time: float = field(default_factory=time.time)
    end_time: float | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    events: list[SpanEvent] = field(default_factory=list)
    status: str = "OK"
    status_message: str = ""

    def set_attribute(self, key: str, value: Any) -> None:
        """Set a span attribute."""
        self.attributes[key] = value

    def set_attributes(self, attributes: dict[str, Any]) -> None:
        """Set multiple span attributes."""
        self.attributes.update(attributes)

    def add_event(self, name: str, attributes: dict[str, Any] | None = None) -> None:
        """Add an event to the span."""
        self.events.append(SpanEvent(name=name, attributes=attributes or {}))

    def set_status(self, status: str, message: str = "") -> None:
        """Set span status (OK, ERROR)."""
        self.status = status
        self.status_message = message

    def end(self) -> None:
        """End the span."""
        self.end_time = time.time()

    @property
    def duration_ms(self) -> float:
        """Get span duration in milliseconds."""
        end = self.end_time or time.time()
        return (end - self.start_time) * 1000

    def to_dict(self) -> dict[str, Any]:
        """Convert span to dictionary for export."""
        return {
            "name": self.name,
            "trace_id": self.context.trace_id,
            "span_id": self.context.span_id,
            "parent_span_id": self.context.parent_span_id,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_ms": self.duration_ms,
            "attributes": self.attributes,
            "events": [
                {"name": e.name, "timestamp": e.timestamp, "attributes": e.attributes}
                for e in self.events
            ],
            "status": self.status,
            "status_message": self.status_message,
        }


class SpanContextManager:
    """Context manager for spans."""

    def __init__(self, span: Span, tracer: "Tracer") -> None:
        self.span = span
        self.tracer = tracer
        self._token: contextvars.Token | None = None

    def __enter__(self) -> Span:
        self._token = _current_span.set(self.span)
        return self.span

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if exc_type is not None:
            self.span.set_status("ERROR", str(exc_val))
            self.span.add_event(
                "exception",
                {
                    "exception.type": exc_type.__name__,
                    "exception.message": str(exc_val),
                },
            )
        self.span.end()
        self.tracer._record_span(self.span)
        if self._token:
            _current_span.reset(self._token)


class Tracer:
    """Tracer for creating and managing spans."""

    def __init__(self, service_name: str = "linguistic-stratigraphy") -> None:
        self.service_name = service_name
        self._spans: list[Span] = []
        self._max_spans = 1000  # Keep last N spans in memory
        self._enabled = True

    def start_span(
        self,
        name: str,
        attributes: dict[str, Any] | None = None,
        parent: Span | None = None,
    ) -> SpanContextManager:
        """Start a new span."""
        # Get parent from context if not provided
        if parent is None:
            parent = _current_span.get()

        # Create span context
        if parent:
            context = SpanContext(
                trace_id=parent.context.trace_id,
                parent_span_id=parent.context.span_id,
            )
        else:
            context = SpanContext()

        span = Span(name=name, context=context)
        span.set_attribute("service.name", self.service_name)

        if attributes:
            span.set_attributes(attributes)

        return SpanContextManager(span, self)

    def _record_span(self, span: Span) -> None:
        """Record a completed span."""
        if not self._enabled:
            return

        self._spans.append(span)

        # Trim old spans
        if len(self._spans) > self._max_spans:
            self._spans = self._spans[-self._max_spans :]

    def get_current_span(self) -> Span | None:
        """Get the current active span."""
        return _current_span.get()

    def get_recent_spans(self, limit: int = 100) -> list[dict[str, Any]]:
        """Get recent spans for debugging."""
        return [span.to_dict() for span in self._spans[-limit:]]

    def get_trace(self, trace_id: str) -> list[dict[str, Any]]:
        """Get all spans for a trace."""
        return [span.to_dict() for span in self._spans if span.context.trace_id == trace_id]

    def clear(self) -> None:
        """Clear recorded spans."""
        self._spans.clear()

    def enable(self) -> None:
        """Enable tracing."""
        self._enabled = True

    def disable(self) -> None:
        """Disable tracing."""
        self._enabled = False


# Global tracer instance
tracer = Tracer()


def traced(
    name: str | None = None,
    attributes: dict[str, Any] | None = None,
) -> Callable[[F], F]:
    """Decorator to trace a function."""

    def decorator(func: F) -> F:
        span_name = name or f"{func.__module__}.{func.__name__}"

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            with tracer.start_span(span_name, attributes) as span:
                span.set_attribute("function.name", func.__name__)
                span.set_attribute("function.module", func.__module__)
                try:
                    result = func(*args, **kwargs)
                    return result
                except Exception as e:
                    span.set_status("ERROR", str(e))
                    raise

        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            with tracer.start_span(span_name, attributes) as span:
                span.set_attribute("function.name", func.__name__)
                span.set_attribute("function.module", func.__module__)
                try:
                    result = await func(*args, **kwargs)
                    return result
                except Exception as e:
                    span.set_status("ERROR", str(e))
                    raise

        import asyncio

        if asyncio.iscoroutinefunction(func):
            return async_wrapper  # type: ignore[return-value]
        return wrapper  # type: ignore[return-value]

    return decorator


def get_trace_context() -> dict[str, str]:
    """Get current trace context for propagation."""
    span = _current_span.get()
    if span:
        return {
            "trace_id": span.context.trace_id,
            "span_id": span.context.span_id,
        }
    return {}


def inject_trace_context(headers: dict[str, str]) -> dict[str, str]:
    """Inject trace context into headers for distributed tracing."""
    context = get_trace_context()
    if context:
        # W3C Trace Context format
        headers["traceparent"] = f"00-{context['trace_id']}-{context['span_id']}-01"
    return headers
