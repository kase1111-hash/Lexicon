"""FastAPI application entry point."""

import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import ValidationError as PydanticValidationError

from src.exceptions import (
    AnalysisError,
    AuthenticationError,
    AuthorizationError,
    DatabaseError,
    DuplicateError,
    ExternalServiceError,
    LexiconError,
    NotFoundError,
    PipelineError,
    RateLimitError,
    ValidationError,
)
from src.utils.db import close_db, get_db
from src.utils.error_tracking import SentryIntegration, capture_error, init_error_tracking
from src.utils.logging import get_logger, setup_logging

from .middleware import PerformanceLoggingMiddleware, RequestLoggingMiddleware
from .routes import analysis, graph, lsr

# Configure logging with our custom setup
setup_logging(
    level=os.getenv("LOG_LEVEL", "INFO"),
    json_format=os.getenv("LOG_FORMAT", "text").lower() == "json",
    log_file=os.getenv("LOG_FILE"),
    component_levels={
        "src.api": os.getenv("API_LOG_LEVEL", "INFO"),
        "src.pipelines": os.getenv("PIPELINE_LOG_LEVEL", "INFO"),
        "src.utils.db": os.getenv("DB_LOG_LEVEL", "WARNING"),
    },
)
logger = get_logger(__name__)

# Initialize error tracking (Sentry, Elasticsearch)
init_error_tracking(environment=os.getenv("ENVIRONMENT", "development"))


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Manage application lifecycle - startup and shutdown."""
    # Startup
    logger.info("Starting Linguistic Stratigraphy API")
    try:
        db = await get_db()
        logger.info("Database connections established")
    except Exception as e:
        logger.warning(f"Could not connect to all databases: {e}")

    yield

    # Shutdown
    logger.info("Shutting down Linguistic Stratigraphy API")
    await close_db()
    logger.info("Database connections closed")


# Create FastAPI application
app = FastAPI(
    title="Linguistic Stratigraphy API",
    description="""
    API for cross-linguistic lexical evolution analysis.

    ## Features

    - **LSR Operations**: Search, retrieve, and analyze Lexical State Records
    - **Etymology Analysis**: Trace word origins and evolution
    - **Text Dating**: Date texts based on vocabulary analysis
    - **Contact Detection**: Identify language contact events
    - **Semantic Drift**: Track meaning changes over time

    ## Authentication

    API key authentication via `X-API-Key` header (when enabled).
    """,
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS configuration
cors_origins = os.getenv("CORS_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Request logging middleware
app.add_middleware(RequestLoggingMiddleware)

# Performance monitoring middleware (logs slow requests)
slow_request_threshold = float(os.getenv("SLOW_REQUEST_THRESHOLD_MS", "1000"))
app.add_middleware(PerformanceLoggingMiddleware, slow_request_threshold_ms=slow_request_threshold)


# =============================================================================
# Exception Handlers
# =============================================================================


@app.exception_handler(NotFoundError)
async def not_found_handler(request: Request, exc: NotFoundError) -> JSONResponse:
    """Handle resource not found errors."""
    logger.warning(f"Resource not found: {exc.message}")
    return JSONResponse(status_code=exc.http_status, content=exc.to_dict())


@app.exception_handler(ValidationError)
async def validation_error_handler(request: Request, exc: ValidationError) -> JSONResponse:
    """Handle validation errors."""
    logger.warning(f"Validation error: {exc.message}")
    return JSONResponse(status_code=exc.http_status, content=exc.to_dict())


@app.exception_handler(RequestValidationError)
async def request_validation_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Handle FastAPI request validation errors."""
    errors = []
    for error in exc.errors():
        field = ".".join(str(loc) for loc in error["loc"])
        errors.append({"field": field, "message": error["msg"], "type": error["type"]})
    logger.warning(f"Request validation failed: {errors}")
    return JSONResponse(
        status_code=400,
        content={
            "error": "VALIDATION_ERROR",
            "message": "Request validation failed",
            "details": {"errors": errors},
        },
    )


@app.exception_handler(PydanticValidationError)
async def pydantic_validation_handler(
    request: Request, exc: PydanticValidationError
) -> JSONResponse:
    """Handle Pydantic validation errors."""
    errors = []
    for error in exc.errors():
        field = ".".join(str(loc) for loc in error["loc"])
        errors.append({"field": field, "message": error["msg"], "type": error["type"]})
    logger.warning(f"Pydantic validation failed: {errors}")
    return JSONResponse(
        status_code=400,
        content={
            "error": "VALIDATION_ERROR",
            "message": "Data validation failed",
            "details": {"errors": errors},
        },
    )


@app.exception_handler(DuplicateError)
async def duplicate_handler(request: Request, exc: DuplicateError) -> JSONResponse:
    """Handle duplicate resource errors."""
    logger.warning(f"Duplicate resource: {exc.message}")
    return JSONResponse(status_code=exc.http_status, content=exc.to_dict())


@app.exception_handler(RateLimitError)
async def rate_limit_handler(request: Request, exc: RateLimitError) -> JSONResponse:
    """Handle rate limit exceeded errors."""
    logger.warning(f"Rate limit exceeded: {request.client.host if request.client else 'unknown'}")
    headers = {}
    if "retry_after_seconds" in exc.details:
        headers["Retry-After"] = str(exc.details["retry_after_seconds"])
    return JSONResponse(status_code=exc.http_status, content=exc.to_dict(), headers=headers)


@app.exception_handler(AuthenticationError)
async def authentication_handler(request: Request, exc: AuthenticationError) -> JSONResponse:
    """Handle authentication errors."""
    logger.warning(f"Authentication failed: {request.url.path}")
    return JSONResponse(
        status_code=exc.http_status,
        content=exc.to_dict(),
        headers={"WWW-Authenticate": "Bearer"},
    )


@app.exception_handler(AuthorizationError)
async def authorization_handler(request: Request, exc: AuthorizationError) -> JSONResponse:
    """Handle authorization errors."""
    logger.warning(f"Authorization denied: {request.url.path}")
    return JSONResponse(status_code=exc.http_status, content=exc.to_dict())


@app.exception_handler(DatabaseError)
async def database_error_handler(request: Request, exc: DatabaseError) -> JSONResponse:
    """Handle database errors."""
    logger.error(f"Database error: {exc.message}", exc_info=True)
    return JSONResponse(status_code=exc.http_status, content=exc.to_dict())


@app.exception_handler(PipelineError)
async def pipeline_error_handler(request: Request, exc: PipelineError) -> JSONResponse:
    """Handle pipeline processing errors."""
    logger.error(f"Pipeline error: {exc.message}", exc_info=True)
    return JSONResponse(status_code=exc.http_status, content=exc.to_dict())


@app.exception_handler(ExternalServiceError)
async def external_service_handler(request: Request, exc: ExternalServiceError) -> JSONResponse:
    """Handle external service errors."""
    logger.error(f"External service error: {exc.message}")
    return JSONResponse(status_code=exc.http_status, content=exc.to_dict())


@app.exception_handler(AnalysisError)
async def analysis_error_handler(request: Request, exc: AnalysisError) -> JSONResponse:
    """Handle analysis errors."""
    logger.error(f"Analysis error: {exc.message}")
    return JSONResponse(status_code=exc.http_status, content=exc.to_dict())


@app.exception_handler(LexiconError)
async def lexicon_error_handler(request: Request, exc: LexiconError) -> JSONResponse:
    """Handle any other Lexicon application errors."""
    logger.error(f"Application error: {exc.message}")
    return JSONResponse(status_code=exc.http_status, content=exc.to_dict())


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Handle uncaught exceptions."""
    logger.error(f"Unhandled exception: {exc}", exc_info=True)

    # Capture error with Sentry and other integrations
    capture_error(
        exc,
        path=str(request.url.path),
        method=request.method,
        client_ip=request.client.host if request.client else None,
    )

    return JSONResponse(
        status_code=500,
        content={
            "error": "INTERNAL_ERROR",
            "message": "An unexpected error occurred",
            "details": {"type": type(exc).__name__},
        },
    )


# Include routers
app.include_router(lsr.router, prefix="/api/v1/lsr", tags=["LSR"])
app.include_router(analysis.router, prefix="/api/v1/analyze", tags=["Analysis"])
app.include_router(graph.router, prefix="/api/v1/graph", tags=["Graph"])


@app.get("/", tags=["Root"])
async def root() -> dict:
    """Root endpoint with API information."""
    return {
        "name": "Linguistic Stratigraphy API",
        "version": "0.1.0",
        "description": "Cross-linguistic lexical evolution analysis",
        "docs": "/docs",
        "health": "/health",
    }


@app.get("/health", tags=["Health"])
async def health() -> dict:
    """
    Health check endpoint.

    Returns the health status of the API and its dependencies.
    """
    health_status = {
        "status": "healthy",
        "api": "up",
        "databases": {},
    }

    # Check database connections
    try:
        db = await get_db()
        health_status["databases"]["neo4j"] = "connected" if db._neo4j_driver else "disconnected"
        health_status["databases"]["postgres"] = (
            "connected" if db._postgres_pool else "disconnected"
        )
        health_status["databases"]["elasticsearch"] = (
            "connected" if db._elasticsearch_client else "disconnected"
        )
        health_status["databases"]["redis"] = "connected" if db._redis_client else "disconnected"
    except Exception as e:
        health_status["status"] = "degraded"
        health_status["error"] = str(e)

    return health_status


@app.get("/metrics", tags=["Monitoring"])
async def metrics() -> dict:
    """
    Basic metrics endpoint.

    Returns operational metrics for monitoring.
    """
    # In production, this would return Prometheus-style metrics
    return {
        "api_version": "0.1.0",
        "metrics": {
            "requests_total": "N/A - implement with prometheus_client",
            "lsr_count": "N/A - query from database",
            "edge_count": "N/A - query from database",
        },
    }


def run() -> None:
    """Run the API server (for development)."""
    import uvicorn

    uvicorn.run(
        "src.api.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )


if __name__ == "__main__":
    run()
