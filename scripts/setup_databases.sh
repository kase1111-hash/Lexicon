#!/usr/bin/env bash
# Start the databases with docker compose and create their schemas (make db-init).
#
#   scripts/setup_databases.sh                   # Neo4j, Elasticsearch, Redis
#   WITH_POSTGRES=1 scripts/setup_databases.sh   # also the optional PostgreSQL
#
# Credentials come from .env (cp .env.example .env), the same file docker
# compose reads; the schema steps run on the host through the application
# code, so they use exactly the settings the API and ingestion use. Needs the
# Python dependencies (pip install -r requirements.txt). Safe to re-run.
set -euo pipefail

cd "$(dirname "$0")/.."
PYTHON="${PYTHON:-python}"

if ! docker info >/dev/null 2>&1; then
    echo "Error: Docker is not running. Please start Docker first." >&2
    exit 1
fi
if [ ! -f .env ]; then
    echo "Error: .env not found. Run: cp .env.example .env  (then set the passwords)" >&2
    exit 1
fi

compose=(docker compose)
services=(neo4j elasticsearch redis)
if [ "${WITH_POSTGRES:-0}" = "1" ]; then
    # PostgreSQL is optional and reserved for future use
    compose+=(--profile postgres)
    services+=(postgres)
fi

echo "Starting ${services[*]} and waiting until they are healthy..."
"${compose[@]}" up -d --wait "${services[@]}"

echo "Creating the Neo4j constraints and indexes and the Elasticsearch index..."
"$PYTHON" - <<'PY'
import asyncio
import sys

from src.repositories.lsr_repository import LSRRepository
from src.utils.db import DatabaseManager


async def main() -> int:
    db = DatabaseManager()
    if not await db.connect_neo4j():
        error = db.get_connection_errors().get("neo4j", "unknown error")
        print(f"Cannot reach Neo4j at {db.config.neo4j_uri}: {error}", file=sys.stderr)
        return 1
    try:
        repo = LSRRepository(db)
        await repo.ensure_schema()
        print("Neo4j schema ready")
        if db.config.elasticsearch_configured and await db.connect_elasticsearch():
            if await repo.ensure_elasticsearch_index():
                print("Elasticsearch index ready")
    finally:
        await db.close_all()
    return 0


sys.exit(asyncio.run(main()))
PY

if [ "${WITH_POSTGRES:-0}" = "1" ]; then
    echo "Creating the PostgreSQL schema (alembic) and loading reference data..."
    "$PYTHON" -m alembic upgrade head
    "$PYTHON" scripts/load_initial_data.py
fi

echo "Database setup complete. Load data with e.g.: make ingest-clld"
