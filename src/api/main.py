"""FastAPI application entry point."""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from http import HTTPStatus
from typing import Any, cast
from urllib.parse import urlsplit

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import ValidationError as PydanticValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from strawberry.fastapi import GraphQLRouter

from src.api.graphql import schema as graphql_schema
from src.config import Settings, get_settings, is_production
from src.exceptions import (
    AnalysisError,
    AuthenticationError,
    AuthorizationError,
    ConfigurationError,
    DatabaseError,
    DuplicateError,
    ExternalServiceError,
    LexiconError,
    NotFoundError,
    PipelineError,
    RateLimitError,
    ValidationError,
)
from src.repositories.lsr_repository import ES_INDEX_NAME, LSRRepository
from src.utils.cache import invalidate_search_cache
from src.utils.db import DatabaseManager, close_db, get_db
from src.utils.error_tracking import capture_error, init_error_tracking
from src.utils.logging import get_logger, setup_logging
from src.utils.metrics import metrics
from src.utils.telemetry import tracer

from .jobs import job_registry
from .middleware import (
    APIKeyAuthMiddleware,
    PerformanceLoggingMiddleware,
    RateLimitMiddleware,
    RequestLoggingMiddleware,
    RequestMetricsMiddleware,
)
from .routes import analysis, graph, lsr

# Load configuration
settings = get_settings()

# Validate production configuration
if is_production():
    config_errors = settings.validate_required_for_production()
    if config_errors:
        raise ConfigurationError(
            message="Invalid production configuration: " + "; ".join(config_errors),
            details={"errors": config_errors},
        )

# Configure logging with settings
setup_logging(
    level=settings.logging.log_level,
    json_format=settings.logging.log_format == "json",
    log_file=settings.logging.log_file,
    component_levels={
        "src.api": settings.logging.api_log_level,
        "src.pipelines": settings.logging.pipeline_log_level,
        "src.utils.db": settings.logging.db_log_level,
        "neo4j": settings.logging.db_log_level,  # the driver logs every Bolt message at DEBUG
    },
)
logger = get_logger(__name__)

# Initialize error tracking
init_error_tracking(environment=settings.error_tracking.environment)

# Log configuration (with sensitive values masked)
logger.debug(f"Configuration loaded: {settings.mask_sensitive()}")


# Background tasks started at startup (kept referenced until they finish)
_background_tasks: set[asyncio.Task] = set()

_REINDEX_LOCK_KEY = "lexicon:es-reindex-lock"
# A worker killed while reindexing leaves its lock behind until it expires
_REINDEX_LOCK_SECONDS = 900
_REINDEX_LOCK_POLL_SECONDS = 30.0


async def _reindex_search(db: DatabaseManager, wait_for_lock: bool = True) -> None:
    """Rebuild the Elasticsearch index from Neo4j (one worker at a time).

    Args:
        db: The database manager.
        wait_for_lock: When another worker holds the reindex lock, check the
            index again once the lock is released or expires, in case its
            holder died before finishing.
    """
    redis = db.redis if db.get_connection_status()["redis"]["connected"] else None
    locked = False
    try:
        if redis is not None:
            locked = bool(
                await redis.set(_REINDEX_LOCK_KEY, "1", nx=True, ex=_REINDEX_LOCK_SECONDS)
            )
            if not locked:
                if wait_for_lock:
                    logger.info(
                        "Another worker is reindexing Elasticsearch; checking the index "
                        "again once its lock is released"
                    )
                    _start_background(_recheck_search_index_after_lock(db))
                else:
                    logger.info("Another worker is already reindexing Elasticsearch")
                return
        result = await LSRRepository(db).reindex_all_to_elasticsearch()
        if result.errors:
            logger.warning(f"Elasticsearch reindex incomplete: {'; '.join(result.errors)}")
        else:
            logger.info(f"Elasticsearch reindexed {result.succeeded} LSRs from Neo4j")
        # Searches cached while the index was incomplete missed its matches
        await invalidate_search_cache()
    except Exception as e:
        logger.warning(f"Elasticsearch reindex failed: {e}")
    finally:
        if locked and redis is not None:
            try:
                await redis.delete(_REINDEX_LOCK_KEY)
            except Exception as e:
                logger.debug(f"Could not release reindex lock: {e}")


async def _recheck_search_index_after_lock(db: DatabaseManager) -> None:
    """Check the index again once the reindex lock is gone (at most once).

    The lock's holder may have died mid-reindex (it is only released when
    the reindex ends), leaving the index short until the lock expires.
    """
    deadline = asyncio.get_running_loop().time() + _REINDEX_LOCK_SECONDS
    while asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(_REINDEX_LOCK_POLL_SECONDS)
        try:
            if not await db.redis.exists(_REINDEX_LOCK_KEY):
                break
        except Exception as e:
            logger.warning(f"Could not check the Elasticsearch reindex lock: {e}")
            break
    await _prepare_search_index(db, wait_for_lock=False)


async def _prepare_search_index(db: DatabaseManager, wait_for_lock: bool = True) -> None:
    """Make Elasticsearch able to answer for every LSR in Neo4j.

    Form searches go to Elasticsearch whenever it is connected, so the index
    needs its explicit mapping (keyword language codes) and every LSR
    written while Elasticsearch was unavailable. Creates or updates the
    index, then reindexes in the background when it holds fewer documents
    than Neo4j has LSRs, or when its mapping is unusable.

    Args:
        db: The database manager.
        wait_for_lock: Passed to _reindex_search; False when this is already
            the check made after another worker's lock was released.
    """
    status = db.get_connection_status()
    if not (status["elasticsearch"]["connected"] and status["neo4j"]["connected"]):
        return
    try:
        if await LSRRepository(db).ensure_elasticsearch_index():
            es_count = (await db.elasticsearch.count(index=ES_INDEX_NAME))["count"]
            async with db.neo4j_session() as session:
                record = await (await session.run("MATCH (l:LSR) RETURN count(l) AS n")).single()
            neo4j_count = record["n"] if record else 0
            if es_count >= neo4j_count:
                logger.info(f"Elasticsearch index holds {es_count} of {neo4j_count} LSRs")
                return
            logger.warning(
                f"Elasticsearch index holds {es_count} of {neo4j_count} LSRs; "
                "reindexing from Neo4j in the background"
            )
        else:
            logger.warning("Elasticsearch index is unusable; rebuilding it in the background")
    except Exception as e:
        logger.warning(f"Could not check the Elasticsearch index: {e}")
        return

    _start_background(_reindex_search(db, wait_for_lock))


def _start_background(coro: Any) -> None:
    """Run a coroutine in the background, cancelled at shutdown."""
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


# Elasticsearch and Redis are optional, so the API does not wait for them at
# startup; ones that are still booting are retried for this long.
_LATE_CONNECT_INTERVAL_SECONDS = 5.0
_LATE_CONNECT_ATTEMPTS = 36


async def _port_open(uri: str, default_port: int) -> bool:
    """Whether something accepts TCP connections at the URI's host and port."""
    parts = urlsplit(uri)
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(parts.hostname or "localhost", parts.port or default_port),
            timeout=2.0,
        )
    except (TimeoutError, OSError):
        return False
    writer.close()
    return True


async def _wait_for_search_cluster(db: DatabaseManager) -> None:
    """Give a just-started Elasticsearch time to reach status yellow.

    It answers requests before it can create indexes; creating the index
    earlier times out.
    """
    try:
        await db.elasticsearch.cluster.health(
            wait_for_status="yellow", timeout="60s", request_timeout=70
        )
    except Exception as e:
        logger.warning(f"Elasticsearch is not ready yet: {str(e) or type(e).__name__}")


async def _connect_late_stores(db: DatabaseManager) -> None:
    """Connect Elasticsearch and Redis once they come up after the API.

    Under docker compose the API starts as soon as Neo4j is healthy, often
    before Elasticsearch has finished booting. Without this the API would
    serve without them until restarted. Elasticsearch is retried only when it
    is configured, and only once its port accepts connections (a refused
    client connection makes the Elasticsearch client log warnings).
    """
    for _ in range(_LATE_CONNECT_ATTEMPTS):
        status = db.get_connection_status()
        es_missing = db.config.elasticsearch_configured and not status["elasticsearch"]["connected"]
        redis_missing = db.config.redis_configured and not status["redis"]["connected"]
        if not (es_missing or redis_missing):
            return
        await asyncio.sleep(_LATE_CONNECT_INTERVAL_SECONDS)
        if redis_missing and await db.connect_redis(quiet=True):
            job_registry.use_redis(db.config.redis_uri)
        if (
            es_missing
            and await _port_open(db.config.elasticsearch_uri, 9200)
            and await db.connect_elasticsearch(quiet=True)
        ):
            await _wait_for_search_cluster(db)
            await _prepare_search_index(db)
    logger.info("Stopped retrying unavailable optional stores (Elasticsearch/Redis)")


async def _ensure_graph_schema(db: DatabaseManager) -> None:
    """Create the Neo4j constraints and indexes (idempotent) if Neo4j is up."""
    if not db.get_connection_status()["neo4j"]["connected"]:
        return
    try:
        await LSRRepository(db).ensure_schema()
    except Exception as e:
        logger.warning(f"Could not create the Neo4j constraints and indexes: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Manage application lifecycle - startup and shutdown."""
    # Startup
    logger.info("Starting Lexicon API")
    try:
        db = await get_db()
        status = db.get_connection_status()
        unavailable = sorted(
            name for name, info in status.items() if info["configured"] and not info["connected"]
        )
        if unavailable:
            logger.warning(
                f"API starting with degraded database availability; "
                f"unavailable: {', '.join(unavailable)}"
            )
        else:
            logger.info("Database connections established")

        # Async job state and rate-limit counters are shared across workers
        # only through Redis
        if not status["redis"]["configured"]:
            logger.info(
                "Redis is not configured: async job state and rate-limit counters are "
                "kept per process, so run a single worker"
            )
        elif not (status["redis"]["connected"] and job_registry.use_redis(db.config.redis_uri)):
            logger.warning(
                "Redis unavailable: async job state and rate-limit counters are "
                "kept per process, so run a single worker"
            )

        await _ensure_graph_schema(db)
        await _prepare_search_index(db)
        if not (status["elasticsearch"]["connected"] and status["redis"]["connected"]):
            _start_background(_connect_late_stores(db))
    except Exception as e:
        logger.warning(f"Could not connect to all databases: {e}")

    yield

    # Shutdown
    logger.info("Shutting down Lexicon API")
    for task in list(_background_tasks):
        task.cancel()
    job_registry.use_redis(None)
    await close_db()
    logger.info("Database connections closed")


# OpenAPI Tags for documentation grouping
tags_metadata = [
    {
        "name": "LSR",
        "description": (
            "Lexical State Records (word forms with their first and last attestation): "
            "search, fetch, create and delete, and each record's etymology, descendants, "
            "cognates and borrowings"
        ),
    },
    {
        "name": "Analysis",
        "description": (
            "Text dating, anachronism detection, language contact events (borrowings by "
            "donor language and century) and experimental semantic drift (one word over "
            "time, or one spelling across languages)"
        ),
    },
    {
        "name": "Graph",
        "description": (
            "Graph traversal (paths, etymology chains, cognates), read-only Cypher "
            "queries and paged bulk export"
        ),
    },
    {
        "name": "Monitoring",
        "description": "Prometheus and JSON metrics, and recent request traces",
    },
]

# Create FastAPI application
app = FastAPI(
    title="Lexicon API",
    description="""
Date a text by its words.

Lexicon keeps a Neo4j graph of word records (LSRs), each with its first
attestation year and links to the words it descends from or was borrowed
from. This API serves that graph and the analyses built on it:

- **Dating** (`/api/v1/analyze/date-text`): a text is no older than its newest
  word; the result reports how many of its words have dates.
- **Anachronisms** (`/api/v1/analyze/detect-anachronisms`): words first
  attested after a claimed date.
- **Contact events** (`/api/v1/analyze/contact-events`): borrowings grouped by
  donor language and century.
- **Semantic drift** (experimental), **records and search** (`/api/v1/lsr`),
  **graph traversal and read-only Cypher** (`/api/v1/graph`), and **GraphQL**
  (`/graphql`).

When too little of a text is dated, analyses answer `insufficient_data`
rather than a verdict. Errors use `{"error", "message", "details"}` with an
error code; an unreachable graph is `503`.

**Authentication:** `X-API-Key` header when the server sets `API_KEY`.
**Rate limiting:** per client IP (default 100 requests/minute; `429` with
`Retry-After`); only `/health` and the documentation pages are not limited.

Documentation: https://github.com/kase1111-hash/Lexicon/blob/main/docs/api-reference.md
    """,
    version=settings.error_tracking.app_version,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    openapi_tags=tags_metadata,
    contact={
        "name": "Lexicon on GitHub",
        "url": "https://github.com/kase1111-hash/Lexicon/issues",
    },
    license_info={
        "name": "MIT",
        "url": "https://opensource.org/licenses/MIT",
    },
    servers=[
        {"url": "/", "description": "Current server"},
        {"url": "http://localhost:8000", "description": "Local development"},
    ],
)


def configure_middleware(app: FastAPI, settings: Settings) -> None:
    """Install the middleware stack configured by `settings` on `app`.

    Each add_middleware call wraps the ones added before it, so a request
    passes them bottom-up: CORS -> request ID/logging -> metrics ->
    slow-request logging -> rate limiting -> API key auth -> routes.
    """
    api_key = (settings.api.api_key.get_secret_value() if settings.api.api_key else "") or None
    if not api_key:
        logger.warning(
            "API key authentication is DISABLED. "
            "Set API_KEY environment variable to enable authentication."
        )

    # API key authentication middleware
    app.add_middleware(
        APIKeyAuthMiddleware,
        api_key=api_key,
        header_name=settings.api.api_key_header,
        enabled=api_key is not None,
    )

    # Rate limiting per client IP, outside auth so that rejected API keys
    # count too
    app.add_middleware(
        RateLimitMiddleware,
        requests=settings.api.rate_limit_requests,
        window_seconds=settings.api.rate_limit_window_seconds,
        enabled=settings.api.rate_limit_enabled,
    )

    # Performance monitoring middleware (logs slow requests)
    app.add_middleware(
        PerformanceLoggingMiddleware,
        slow_request_threshold_ms=settings.logging.slow_request_threshold_ms,
    )

    # Request metrics and traces (served by /metrics and /traces)
    app.add_middleware(RequestMetricsMiddleware)

    # Request logging middleware (assigns X-Request-ID)
    app.add_middleware(RequestLoggingMiddleware)

    # CORS configuration. Outermost, so preflights are answered before auth
    # and every response (including 401/429) carries the CORS headers.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.api.cors_origins_list,
        allow_credentials=settings.api.cors_allow_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=[
            "X-Request-ID",
            "Retry-After",
            "X-RateLimit-Limit",
            "X-RateLimit-Remaining",
            "X-RateLimit-Reset",
        ],
    )


configure_middleware(app, settings)


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
async def request_validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
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


# Error codes for HTTP errors raised by the framework (unknown routes, wrong
# methods) or by routes via HTTPException
_HTTP_ERROR_CODES = {
    400: "VALIDATION_ERROR",
    401: "AUTHENTICATION_ERROR",
    403: "AUTHORIZATION_ERROR",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "DUPLICATE_ERROR",
    429: "RATE_LIMIT_EXCEEDED",
}


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """Render HTTP errors in the standard error format."""
    if isinstance(exc.detail, str):
        message, details = exc.detail, {}
    else:
        message, details = HTTPStatus(exc.status_code).phrase, {"detail": exc.detail}
    if exc.status_code == 404 and message == "Not Found":
        details = {"path": request.url.path}
    code = _HTTP_ERROR_CODES.get(
        exc.status_code, "INTERNAL_ERROR" if exc.status_code >= 500 else "HTTP_ERROR"
    )
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": code, "message": message, "details": details},
        headers=exc.headers,
    )


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

    details = {}
    if settings.error_tracking.debug:
        details["type"] = type(exc).__name__

    # This handler runs outside all middleware, so echo the request ID here
    request_id = getattr(request.state, "request_id", None) or request.headers.get("X-Request-ID")
    return JSONResponse(
        status_code=500,
        content={
            "error": "INTERNAL_ERROR",
            "message": "An unexpected error occurred",
            "details": details,
        },
        headers={"X-Request-ID": request_id} if request_id else None,
    )


# Include routers
app.include_router(lsr.router, prefix="/api/v1/lsr", tags=["LSR"])
app.include_router(analysis.router, prefix="/api/v1/analyze", tags=["Analysis"])
app.include_router(graph.router, prefix="/api/v1/graph", tags=["Graph"])


# GraphQL endpoint (with GraphiQL playground on GET)
async def _graphql_context() -> dict[str, Any]:
    return {"db": await get_db()}


graphql_router: GraphQLRouter[dict[str, Any], None] = GraphQLRouter(
    graphql_schema,
    # Strawberry awaits async context getters at runtime; the stubs of older
    # releases only admit sync callables
    context_getter=cast(Any, _graphql_context),
    graphql_ide="graphiql",
)
app.include_router(graphql_router, prefix="/graphql", tags=["GraphQL"])


@app.get("/", tags=["Root"])
async def root() -> dict:
    """Root endpoint with API information."""
    return {
        "name": "Lexicon API",
        "version": settings.error_tracking.app_version,
        "description": "Date a text by its words: dating, anachronisms, language contact",
        "docs": "/docs",
        "health": "/health",
    }


@app.get("/health", tags=["Health"])
async def health() -> JSONResponse:
    """
    Health check endpoint.

    Actively checks every configured backend. Neo4j holds the lexical graph,
    so without it the API is "unhealthy" (HTTP 503); when a configured
    optional store (Elasticsearch, Redis, PostgreSQL) is down it is "degraded"
    (HTTP 200). A Neo4j that was unavailable is reconnected here. Optional
    stores that are not configured are reported as "not_configured".
    """
    try:
        db = await get_db()
        reachable = await db.ping()
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        reachable = dict.fromkeys(("neo4j", "postgres", "elasticsearch", "redis"), False)

    if not reachable["neo4j"]:
        status = "unhealthy"
    elif all(reachable.values()):
        status = "healthy"
    else:
        status = "degraded"
    return JSONResponse(
        status_code=503 if status == "unhealthy" else 200,
        content={
            "status": status,
            "api": "up",
            "databases": {
                name: (
                    ("connected" if reachable[name] else "disconnected")
                    if name in reachable
                    else "not_configured"
                )
                for name in ("neo4j", "postgres", "elasticsearch", "redis")
            },
        },
    )


@app.get("/metrics", tags=["Monitoring"], response_class=PlainTextResponse)
async def get_metrics() -> PlainTextResponse:
    """
    Prometheus-compatible metrics endpoint.

    Returns operational metrics in Prometheus text format: request counts
    (api_requests_total), latencies (api_request_duration_seconds) and
    in-flight requests (api_active_requests) by route, method and status.
    Metrics are per process; with several workers each reports its own.
    """
    return PlainTextResponse(
        content=metrics.export_prometheus(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


@app.get("/metrics/json", tags=["Monitoring"])
async def get_metrics_json() -> dict:
    """
    JSON metrics endpoint.

    Returns all metrics as JSON for debugging.
    """
    return metrics.get_all_metrics()


@app.get("/traces", tags=["Monitoring"])
async def get_traces(limit: int = Query(100, ge=1, le=1000)) -> list:
    """
    Get recent traces for debugging.

    Returns the last N completed spans (one per request, oldest first), kept
    in this process's memory.
    """
    return tracer.get_recent_spans(limit)


def run() -> None:
    """Run the API server (the `ls-api` command).

    Host, port and worker count default to API_HOST / API_PORT / API_WORKERS;
    command-line flags override them. Auto-reload is off unless --reload.
    """
    import argparse

    import uvicorn

    parser = argparse.ArgumentParser(prog="ls-api", description="Run the Lexicon API server.")
    parser.add_argument("--host", default=settings.api.api_host, help="Bind address (API_HOST)")
    parser.add_argument("--port", type=int, default=settings.api.api_port, help="Port (API_PORT)")
    parser.add_argument(
        "--workers",
        type=int,
        default=settings.api.api_workers,
        help="Worker processes (API_WORKERS); more than one needs Redis",
    )
    parser.add_argument(
        "--reload", action="store_true", help="Restart on code changes (development, one worker)"
    )
    args = parser.parse_args()

    uvicorn.run(
        "src.api.main:app",
        host=args.host,
        port=args.port,
        workers=None if args.reload else args.workers,
        reload=args.reload,
    )


if __name__ == "__main__":
    run()
