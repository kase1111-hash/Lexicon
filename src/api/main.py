"""FastAPI application entry point."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .routes import analysis, graph, lsr

app = FastAPI(
    title="Linguistic Stratigraphy API",
    description="API for cross-linguistic lexical evolution analysis",
    version="0.1.0",
)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure appropriately for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(lsr.router, prefix="/api/v1/lsr", tags=["LSR"])
app.include_router(analysis.router, prefix="/api/v1/analyze", tags=["Analysis"])
app.include_router(graph.router, prefix="/api/v1/graph", tags=["Graph"])


@app.get("/")
async def root():
    """Root endpoint."""
    return {"message": "Linguistic Stratigraphy API", "version": "0.1.0"}


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy"}
