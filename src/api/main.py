"""FastAPI application entry point."""

import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src.utils.db import close_db, get_db

from .routes import analysis, graph, lsr

# Configure logging
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


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


# Exception handlers
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Handle uncaught exceptions."""
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error", "type": type(exc).__name__},
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
