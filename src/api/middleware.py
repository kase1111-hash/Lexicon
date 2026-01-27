"""FastAPI middleware for logging, authentication, and request tracking."""

import secrets
import time
from collections.abc import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from src.utils.logging import clear_request_id, get_logger, set_request_id


logger = get_logger(__name__)


# Paths that don't require authentication
PUBLIC_PATHS = {
    "/",
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/metrics",
    "/metrics/json",
}


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Middleware for logging HTTP requests and responses."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Process request with logging."""
        # Get or generate request ID
        request_id = request.headers.get("X-Request-ID") or set_request_id()
        set_request_id(request_id)

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

    def __init__(self, app, slow_request_threshold_ms: float = 1000.0):
        """
        Initialize middleware.

        Args:
            app: FastAPI application
            slow_request_threshold_ms: Threshold in ms for logging slow requests
        """
        super().__init__(app)
        self.slow_request_threshold_ms = slow_request_threshold_ms

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
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
        app,
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

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Process request with API key authentication."""
        # Skip authentication if disabled
        if not self.enabled:
            return await call_next(request)

        # Skip authentication for public paths
        if self._is_public_path(request.url.path):
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

        # Validate API key using constant-time comparison to prevent timing attacks
        if not secrets.compare_digest(provided_key, self.api_key):
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
