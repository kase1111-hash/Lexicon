# Lexicon API image
# Multi-stage build. Every dependency ships a manylinux wheel for Python 3.11,
# so no compiler or apt packages are needed.

# Build stage
FROM python:3.11-slim AS builder

WORKDIR /build

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# Production stage
FROM python:3.11-slim AS production

WORKDIR /app

# Non-root user; data/ is writable so ingestion can download source files
RUN useradd --create-home --shell /bin/bash appuser \
    && mkdir -p /app/data \
    && chown appuser:appuser /app/data

# Copy Python packages from builder
COPY --from=builder /root/.local /home/appuser/.local

# Copy application code
COPY src/ ./src/
COPY pyproject.toml ./

# Set environment variables
ENV PATH=/home/appuser/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

# Switch to non-root user
USER appuser

# Expose API port
EXPOSE 8000

# Health check (the slim image has no curl)
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health', timeout=5).status == 200 else 1)"]

# Run the application. Behind a reverse proxy the client address comes from
# X-Forwarded-For, which uvicorn trusts only from the addresses in
# FORWARDED_ALLOW_IPS (default 127.0.0.1; see docker-compose.production.yml),
# so do not pass --forwarded-allow-ips here
CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
