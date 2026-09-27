"""FastAPI middleware for logging, authentication, rate limiting and metrics."""

import math
import secrets
import time
from typing import Any

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse
from starlette.types import ASGIApp

from src.exceptions import RateLimitError
from src.utils.db import peek_db
from src.utils.logging import clear_request_id, get_logger, set_request_id
from src.utils.metrics import metrics
from src.utils.telemetry import tracer

logger = get_logger(__name__)


# Paths that don't require authentication
PUBLIC_PATHS = {
    "/",
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
}


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Middleware for logging HTTP requests and responses."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """Process request with logging."""
        # Get or generate request ID
        request_id = request.headers.get("X-Request-ID") or set_request_id()
        set_request_id(request_id)
        # Shared with the app's exception handlers, which also set the header
        request.state.request_id = request_id

        # Start timing
        start_time = time.perf_counter()

        # Log request
        logger.info(
            f"Request started: {request.method} {request.url.path}",
            extra={
                "method": request.method,
                "path": request.url.path,
                "query": str(request.query_params) if request.query_params else None,
                "client_ip": request.client.host if request.client else None,
            },
        )

        # Process request
        try:
            response = await call_next(request)
        except Exception as e:
            # Log error
            duration_ms = (time.perf_counter() - start_time) * 1000
            logger.error(
                f"Request failed: {request.method} {request.url.path}",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "duration_ms": round(duration_ms, 2),
                    "error": str(e),
                },
                exc_info=True,
            )
            clear_request_id()
            raise

        # Calculate duration
        duration_ms = (time.perf_counter() - start_time) * 1000

        # Log response
        log_level = "info" if response.status_code < 400 else "warning"
        if response.status_code >= 500:
            log_level = "error"

        getattr(logger, log_level)(
            f"Request completed: {request.method} {request.url.path} -> {response.status_code}",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": round(duration_ms, 2),
            },
        )

        # Add request ID to response headers
        response.headers["X-Request-ID"] = request_id

        # Clear request ID context
        clear_request_id()

        return response


class PerformanceLoggingMiddleware(BaseHTTPMiddleware):
    """Middleware for logging slow requests."""

    def __init__(self, app: ASGIApp, slow_request_threshold_ms: float = 1000.0):
        """
        Initialize middleware.

        Args:
            app: FastAPI application
            slow_request_threshold_ms: Threshold in ms for logging slow requests
        """
        super().__init__(app)
        self.slow_request_threshold_ms = slow_request_threshold_ms

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """Process request with performance monitoring."""
        start_time = time.perf_counter()

        response = await call_next(request)

        duration_ms = (time.perf_counter() - start_time) * 1000

        if duration_ms > self.slow_request_threshold_ms:
            logger.warning(
                f"Slow request detected: {request.method} {request.url.path}",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "duration_ms": round(duration_ms, 2),
                    "threshold_ms": self.slow_request_threshold_ms,
                },
            )

        return response


class APIKeyAuthMiddleware(BaseHTTPMiddleware):
    """
    Middleware for API key authentication.

    Validates the X-API-Key header against configured API keys.
    Requests to public paths (health, docs, etc.) are allowed without authentication.
    """

    def __init__(
        self,
        app: ASGIApp,
        api_key: str | None = None,
        header_name: str = "X-API-Key",
        enabled: bool = True,
    ):
        """
        Initialize API key authentication middleware.

        Args:
            app: FastAPI application
            api_key: The valid API key (if None, authentication is disabled)
            header_name: Header name to check for API key
            enabled: Whether authentication is enabled
        """
        super().__init__(app)
        self.api_key = api_key
        self.header_name = header_name
        self.enabled = enabled and api_key is not None

    def _is_public_path(self, path: str) -> bool:
        """Check if the path is public (doesn't require authentication)."""
        # Exact match
        if path in PUBLIC_PATHS:
            return True

        # Check path prefixes for static files and docs
        public_prefixes = ("/docs", "/redoc", "/openapi")
        return path.startswith(public_prefixes)

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """Process request with API key authentication."""
        # Skip authentication if disabled
        if not self.enabled:
            return await call_next(request)

        # Skip authentication for public paths and CORS preflights (browsers
        # never send credentials on a preflight)
        if request.method == "OPTIONS" or self._is_public_path(request.url.path):
            return await call_next(request)

        # Get API key from header
        provided_key = request.headers.get(self.header_name)

        # Check if API key is provided
        if not provided_key:
            logger.warning(
                f"Missing API key for {request.method} {request.url.path}",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "client_ip": request.client.host if request.client else None,
                },
            )
            return JSONResponse(
                status_code=401,
                content={
                    "error": "AUTHENTICATION_ERROR",
                    "message": "API key required",
                    "details": {"header": self.header_name},
                },
                headers={"WWW-Authenticate": f'ApiKey header="{self.header_name}"'},
            )

        # Validate API key using constant-time comparison to prevent timing
        # attacks. Compare bytes: header values are latin-1 decoded and may hold
        # any byte, which compare_digest rejects in non-ASCII str.
        if not self.api_key or not secrets.compare_digest(
            provided_key.encode("latin-1", "replace"), self.api_key.encode("utf-8")
        ):
            logger.warning(
                f"Invalid API key for {request.method} {request.url.path}",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "client_ip": request.client.host if request.client else None,
                },
            )
            return JSONResponse(
                status_code=401,
                content={
                    "error": "AUTHENTICATION_ERROR",
                    "message": "Invalid API key",
                    "details": {},
                },
                headers={"WWW-Authenticate": f'ApiKey header="{self.header_name}"'},
            )

        # API key is valid, proceed with request
        return await call_next(request)


def _route_template(request: Request) -> str:
    """The matched route's path template (e.g. /api/v1/lsr/{lsr_id})."""
    path = getattr(request.scope.get("route"), "path", None)
    return path if isinstance(path, str) else "<unmatched>"


# Methods recorded by name in metrics; the server accepts any token as a
# method, so anything else is recorded as OTHER (one series, not one per name)
_METRIC_METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"})


class RequestMetricsMiddleware(BaseHTTPMiddleware):
    """Record every request in the metrics collector and the tracer.

    Feeds api_requests_total{endpoint,method,status},
    api_request_duration_seconds{endpoint,method} and api_active_requests
    (served by /metrics) and one span per request (served by /traces).
    Endpoints are route templates, so ids in paths do not create new series.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """Process request, recording its outcome."""
        method = request.method if request.method in _METRIC_METHODS else "OTHER"
        status = 500  # unless a response comes back
        metrics.inc_gauge("api_active_requests")
        start = time.perf_counter()
        with tracer.start_span(f"{method} {request.url.path}") as span:
            try:
                response = await call_next(request)
                status = response.status_code
                return response
            finally:
                duration = time.perf_counter() - start
                endpoint = _route_template(request)
                metrics.dec_gauge("api_active_requests")
                metrics.increment(
                    "api_requests_total",
                    labels={"endpoint": endpoint, "method": method, "status": str(status)},
                )
                metrics.observe_histogram(
                    "api_request_duration_seconds",
                    duration,
                    labels={"endpoint": endpoint, "method": method},
                )
                span.name = f"{method} {endpoint}"
                span.set_attributes(
                    {
                        "http.method": method,
                        "http.route": endpoint,
                        "http.target": request.url.path,
                        "http.status_code": status,
                        "request_id": getattr(request.state, "request_id", None),
                    }
                )
                if status >= 500:
                    span.set_status("ERROR", f"HTTP {status}")


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Fixed-window rate limiting: at most `requests` per client per window.

    A client is its IP address. There is a single API key, shared by every
    client, so counting per key would give all clients one budget that any
    of them could use up. This middleware runs outside APIKeyAuthMiddleware,
    so requests rejected for a missing or wrong key count too, which limits
    API key guessing. Counters live in Redis while the API is connected to
    it, so all workers share them; otherwise (or while Redis fails) each
    process counts on its own. Over the limit, a RATE_LIMIT_EXCEEDED error is
    returned with status 429 and a Retry-After header. Health checks, the API
    documentation pages and CORS preflights are never limited; every path
    that can require the API key (including /metrics) is, so no path lets a
    client guess keys without limit.
    """

    # Health checks and the static API documentation, all public (see
    # PUBLIC_PATHS): an exempt path that checked the API key would let
    # clients guess keys without limit
    EXEMPT_PATHS = frozenset(
        {"/health", "/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json"}
    )
    REDIS_PREFIX = "lexicon:ratelimit:"
    # After a Redis error, count in process for this many seconds instead of
    # making every request wait for an unresponsive Redis
    REDIS_RETRY_SECONDS = 10.0

    def __init__(
        self,
        app: ASGIApp,
        requests: int = 100,
        window_seconds: int = 60,
        enabled: bool = True,
    ):
        """
        Initialize rate limiting middleware.

        Args:
            app: FastAPI application
            requests: Requests allowed per client per window
            window_seconds: Window length in seconds
            enabled: Whether rate limiting is enabled
        """
        super().__init__(app)
        self.requests = requests
        self.window_seconds = window_seconds
        self.enabled = enabled
        # In-process counters for the current window: client -> count
        self._window = -1
        self._counts: dict[str, int] = {}
        self._redis_retry_at = float("-inf")

    def _client_id(self, request: Request) -> str:
        """Identify the client by its IP address."""
        return f"ip:{request.client.host if request.client else 'unknown'}"

    async def _count(self, client_id: str, window: int) -> int:
        """Count this request in the client's window and return the total."""
        db = peek_db()
        redis: Any = None
        if (
            db is not None
            and db.get_connection_status()["redis"]["connected"]
            and time.monotonic() >= self._redis_retry_at
        ):
            redis = db.redis
        if redis is not None:
            key = f"{self.REDIS_PREFIX}{client_id}:{window}"
            try:
                pipe = redis.pipeline(transaction=True)
                pipe.incr(key)
                pipe.expire(key, self.window_seconds + 1)
                count, _ = await pipe.execute()
                return int(count)
            except Exception as e:
                self._redis_retry_at = time.monotonic() + self.REDIS_RETRY_SECONDS
                logger.warning(
                    f"Redis rate limiting failed, counting in process for "
                    f"{self.REDIS_RETRY_SECONDS:g}s: {str(e) or type(e).__name__}"
                )

        if window != self._window:
            self._window = window
            self._counts.clear()
        self._counts[client_id] = self._counts.get(client_id, 0) + 1
        return self._counts[client_id]

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """Process request, rejecting it once the client is over its limit."""
        if not self.enabled or request.method == "OPTIONS" or request.url.path in self.EXEMPT_PATHS:
            return await call_next(request)

        now = time.time()
        window = int(now // self.window_seconds)
        count = await self._count(self._client_id(request), window)
        reset = max(1, math.ceil((window + 1) * self.window_seconds - now))
        headers = {
            "X-RateLimit-Limit": str(self.requests),
            "X-RateLimit-Remaining": str(max(0, self.requests - count)),
            "X-RateLimit-Reset": str(reset),
        }

        if count > self.requests:
            logger.warning(
                f"Rate limit exceeded for {request.method} {request.url.path}",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "client_ip": request.client.host if request.client else None,
                },
            )
            error = RateLimitError(
                retry_after=reset,
                message=(
                    f"Rate limit exceeded: {self.requests} requests "
                    f"per {self.window_seconds} seconds"
                ),
            )
            return JSONResponse(
                status_code=error.http_status,
                content=error.to_dict(),
                headers={**headers, "Retry-After": str(reset)},
            )

        response = await call_next(request)
        response.headers.update(headers)
        return response
