"""
Metrics collection for Linguistic Stratigraphy.

Provides Prometheus-compatible metrics for monitoring application health,
performance, and usage patterns.

Usage:
    from src.utils.metrics import metrics

    # Record a counter
    metrics.increment("api_requests_total", labels={"endpoint": "/lsr", "method": "GET"})

    # Record a gauge
    metrics.set_gauge("active_connections", 42)

    # Record a histogram observation
    metrics.observe_histogram("request_duration_seconds", 0.125, labels={"endpoint": "/lsr"})

    # Get Prometheus-format output
    output = metrics.export_prometheus()
"""

import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any


@dataclass
class MetricValue:
    """A single metric value with optional labels."""

    value: float = 0.0
    labels: dict[str, str] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


@dataclass
class HistogramBuckets:
    """Histogram with configurable buckets."""

    buckets: list[float] = field(
        default_factory=lambda: [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]
    )
    counts: dict[float, int] = field(default_factory=dict)
    sum_value: float = 0.0
    count: int = 0

    def __post_init__(self) -> None:
        self.counts = {b: 0 for b in self.buckets}
        self.counts[float("inf")] = 0

    def observe(self, value: float) -> None:
        """Record an observation."""
        self.sum_value += value
        self.count += 1
        for bucket in self.buckets:
            if value <= bucket:
                self.counts[bucket] += 1
        self.counts[float("inf")] += 1


class MetricsCollector:
    """Thread-safe metrics collector with Prometheus-compatible export."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._counters: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        self._gauges: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        self._histograms: dict[str, dict[str, HistogramBuckets]] = defaultdict(dict)
        self._metadata: dict[str, dict[str, str]] = {}
        self._start_time = time.time()

        # Register default metrics
        self._register_default_metrics()

    def _register_default_metrics(self) -> None:
        """Register default application metrics."""
        self.register_metric("process_start_time_seconds", "gauge", "Start time of the process")
        self.set_gauge("process_start_time_seconds", self._start_time)

        self.register_metric(
            "api_requests_total", "counter", "Total API requests", ["endpoint", "method", "status"]
        )
        self.register_metric(
            "api_request_duration_seconds",
            "histogram",
            "API request duration in seconds",
            ["endpoint", "method"],
        )
        self.register_metric("api_active_requests", "gauge", "Currently active API requests")
        self.register_metric("lsr_operations_total", "counter", "Total LSR operations", ["operation"])
        self.register_metric(
            "lsr_operation_duration_seconds",
            "histogram",
            "LSR operation duration",
            ["operation"],
        )
        self.register_metric("entity_resolution_total", "counter", "Entity resolution operations")
        self.register_metric(
            "entity_resolution_matches", "counter", "Successful entity matches found"
        )
        self.register_metric("database_connections_active", "gauge", "Active database connections")
        self.register_metric(
            "database_query_duration_seconds",
            "histogram",
            "Database query duration",
            ["database", "operation"],
        )

    def register_metric(
        self,
        name: str,
        metric_type: str,
        description: str,
        labels: list[str] | None = None,
    ) -> None:
        """Register a metric with metadata."""
        with self._lock:
            self._metadata[name] = {
                "type": metric_type,
                "help": description,
                "labels": labels or [],
            }

    def _labels_key(self, labels: dict[str, str] | None) -> str:
        """Create a hashable key from labels."""
        if not labels:
            return ""
        return ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))

    def increment(
        self, name: str, value: float = 1.0, labels: dict[str, str] | None = None
    ) -> None:
        """Increment a counter."""
        with self._lock:
            key = self._labels_key(labels)
            self._counters[name][key] += value

    def set_gauge(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        """Set a gauge value."""
        with self._lock:
            key = self._labels_key(labels)
            self._gauges[name][key] = value

    def inc_gauge(self, name: str, value: float = 1.0, labels: dict[str, str] | None = None) -> None:
        """Increment a gauge."""
        with self._lock:
            key = self._labels_key(labels)
            self._gauges[name][key] += value

    def dec_gauge(self, name: str, value: float = 1.0, labels: dict[str, str] | None = None) -> None:
        """Decrement a gauge."""
        with self._lock:
            key = self._labels_key(labels)
            self._gauges[name][key] -= value

    def observe_histogram(
        self, name: str, value: float, labels: dict[str, str] | None = None
    ) -> None:
        """Record a histogram observation."""
        with self._lock:
            key = self._labels_key(labels)
            if key not in self._histograms[name]:
                self._histograms[name][key] = HistogramBuckets()
            self._histograms[name][key].observe(value)

    def get_counter(self, name: str, labels: dict[str, str] | None = None) -> float:
        """Get current counter value."""
        with self._lock:
            key = self._labels_key(labels)
            return self._counters[name].get(key, 0.0)

    def get_gauge(self, name: str, labels: dict[str, str] | None = None) -> float:
        """Get current gauge value."""
        with self._lock:
            key = self._labels_key(labels)
            return self._gauges[name].get(key, 0.0)

    def get_all_metrics(self) -> dict[str, Any]:
        """Get all metrics as a dictionary."""
        with self._lock:
            return {
                "counters": dict(self._counters),
                "gauges": dict(self._gauges),
                "histograms": {
                    name: {
                        key: {
                            "buckets": hist.counts,
                            "sum": hist.sum_value,
                            "count": hist.count,
                        }
                        for key, hist in buckets.items()
                    }
                    for name, buckets in self._histograms.items()
                },
                "metadata": self._metadata,
                "uptime_seconds": time.time() - self._start_time,
            }

    def export_prometheus(self) -> str:
        """Export metrics in Prometheus text format."""
        lines = []

        with self._lock:
            # Counters
            for name, values in self._counters.items():
                meta = self._metadata.get(name, {})
                if meta.get("help"):
                    lines.append(f"# HELP {name} {meta['help']}")
                lines.append(f"# TYPE {name} counter")
                for key, value in values.items():
                    label_str = f"{{{key}}}" if key else ""
                    lines.append(f"{name}{label_str} {value}")

            # Gauges
            for name, values in self._gauges.items():
                meta = self._metadata.get(name, {})
                if meta.get("help"):
                    lines.append(f"# HELP {name} {meta['help']}")
                lines.append(f"# TYPE {name} gauge")
                for key, value in values.items():
                    label_str = f"{{{key}}}" if key else ""
                    lines.append(f"{name}{label_str} {value}")

            # Histograms
            for name, buckets_dict in self._histograms.items():
                meta = self._metadata.get(name, {})
                if meta.get("help"):
                    lines.append(f"# HELP {name} {meta['help']}")
                lines.append(f"# TYPE {name} histogram")
                for key, hist in buckets_dict.items():
                    base_labels = key
                    for bucket, count in sorted(hist.counts.items()):
                        le = "+Inf" if bucket == float("inf") else str(bucket)
                        if base_labels:
                            lines.append(f'{name}_bucket{{{base_labels},le="{le}"}} {count}')
                        else:
                            lines.append(f'{name}_bucket{{le="{le}"}} {count}')
                    label_str = f"{{{base_labels}}}" if base_labels else ""
                    lines.append(f"{name}_sum{label_str} {hist.sum_value}")
                    lines.append(f"{name}_count{label_str} {hist.count}")

        return "\n".join(lines)

    def reset(self) -> None:
        """Reset all metrics (useful for testing)."""
        with self._lock:
            self._counters.clear()
            self._gauges.clear()
            self._histograms.clear()
            self._register_default_metrics()


# Global metrics instance
metrics = MetricsCollector()


class Timer:
    """Context manager for timing operations."""

    def __init__(
        self,
        metric_name: str,
        labels: dict[str, str] | None = None,
        collector: MetricsCollector | None = None,
    ) -> None:
        self.metric_name = metric_name
        self.labels = labels
        self.collector = collector or metrics
        self.start_time: float = 0

    def __enter__(self) -> "Timer":
        self.start_time = time.perf_counter()
        return self

    def __exit__(self, *args: Any) -> None:
        duration = time.perf_counter() - self.start_time
        self.collector.observe_histogram(self.metric_name, duration, self.labels)


def timed(metric_name: str, labels: dict[str, str] | None = None):
    """Decorator for timing function execution."""

    def decorator(func):
        def wrapper(*args, **kwargs):
            with Timer(metric_name, labels):
                return func(*args, **kwargs)

        return wrapper

    return decorator
