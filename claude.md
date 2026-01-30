# Claude.md - Lexicon Project Guide

## Project Overview

**Lexicon** is a computational linguistic stratigraphy system for building and maintaining a cross-linguistic lexical evolution graph. It ingests etymological data from multiple sources, constructs a unified knowledge graph, trains diachronic embeddings, and exposes analysis tools via REST/GraphQL APIs.

**Core Capabilities:**
- Temporal dating of historical texts by vocabulary analysis
- Language contact detection and borrowing pattern identification
- Semantic drift analysis tracking word meaning evolution
- Forgery detection via anachronistic vocabulary identification
- Phylogenetic inference for language family reconstruction
- Etymology tracing across languages and time periods

**Version:** 0.1.0 (Alpha) | **License:** MIT

## Tech Stack

**Core:** Python 3.11+, FastAPI, Strawberry GraphQL, Pydantic 2.0+

**Databases:**
- Neo4j 5.9 - Graph database for lexical relationships
- PostgreSQL 15 - Relational metadata storage
- Elasticsearch 8.9 - Full-text search
- Milvus v2.3 - Vector database for embeddings
- Redis 7 - Caching layer

**ML/NLP:** PyTorch 2.0+, Sentence Transformers, Transformers, spaCy, scikit-learn, XGBoost

**Infrastructure:** Docker Compose, Kubernetes, Apache Airflow

## Essential Commands

```bash
# Setup
make install-dev      # Install all dependencies
make docker-up        # Start all services

# Development
make run-api          # Start API server with auto-reload (port 8000)
make lint-fix         # Auto-fix linting issues
make format           # Format code with Black
make type-check       # Run mypy

# Testing (80% coverage required)
make test             # Run all tests
make test-unit        # Unit tests only
make test-cov         # With coverage report

# Quality checks
make pre-commit       # Run all pre-commit hooks
make security-check   # Bandit security scan
```

## Project Structure

```
src/
├── adapters/         # Data source ingestion (Wiktionary, CLLD, corpus, OCR)
├── analysis/         # Analysis modules (dating, contact_detection, semantic_drift)
├── api/              # REST & GraphQL API
│   ├── main.py       # FastAPI entry point
│   ├── middleware.py # Auth & rate limiting
│   ├── routes/       # REST endpoints (lsr, graph, analysis)
│   └── graphql/      # GraphQL schema & resolvers
├── models/           # Data models (LSR, Language, Relationships)
├── pipelines/        # Data processing (entity_resolution, embedding, validation)
├── training/         # ML training (embeddings, classifiers, phylogenetics)
├── repositories/     # Data access layer
├── utils/            # Utilities (db, cache, logging, validation)
├── config.py         # Configuration management
└── exceptions.py     # Custom exception hierarchy

tests/
├── unit/             # Unit tests
├── integration/      # Integration tests
├── performance/      # Performance tests
├── security/         # Security tests
└── fixtures/         # Shared test data
```

## Code Conventions

**Style:**
- Line length: 100 characters
- Type hints required for all public functions
- Google-style docstrings
- `snake_case` for functions/variables, `PascalCase` for classes

**Patterns:**
- Dependency Injection via FastAPI `Depends()`
- Repository pattern for data access (`LSRRepository`)
- Pipeline pattern for data processing
- Custom exceptions inherit from `LexiconError`
- Async-first design for I/O operations

**Testing:**
- pytest markers: `@pytest.mark.unit`, `.integration`, `.performance`, `.security`
- Fixtures in `tests/fixtures/`
- Async testing with `pytest-asyncio`
- Minimum 80% coverage required

## Key Entry Points

| File | Purpose |
|------|---------|
| `src/api/main.py` | FastAPI application entry point |
| `src/config.py` | Configuration management |
| `src/models/lsr.py` | Core Lexical State Record model |
| `src/analysis/dating.py` | Text dating classifier |
| `src/analysis/contact_detection.py` | Language contact analysis |
| `src/repositories/lsr_repository.py` | Database operations |

## API Endpoints

**REST (v1):**
- `GET/POST/PUT/DELETE /lsr/{id}` - LSR CRUD operations
- `POST /analysis/etymology` - Trace etymology chain
- `POST /analysis/date-text` - Estimate text date
- `POST /analysis/detect-anachronisms` - Find anachronistic vocabulary
- `POST /analysis/contact-detection` - Detect borrowing patterns
- `GET /graph/{id}/ancestors|descendants|cognates` - Graph traversal

**GraphQL:** `/graphql`

**Docs:** `/docs` (Swagger) | `/redoc` (ReDoc)

## Core Data Model

**Lexical State Record (LSR):**
- Represents a word at a specific point in time
- Fields: form (orthographic, phonetic, normalized), language (ISO 639-3), date range, semantic vector (384D), definitions, confidence, attestations
- Relationships: `DESCENDS_FROM`, `BORROWED_FROM`, `COGNATE_OF`, `SHIFTED_TO`, `MERGED_WITH`

## Docker Services

All services have resource limits configured:
- Neo4j: 7687 (Bolt), 7474 (HTTP)
- PostgreSQL: 5432
- Elasticsearch: 9200
- Redis: 6379
- Milvus: 19530
- Airflow: 8080
- API: 8000

Start with `docker compose up -d` (requires 16GB+ RAM recommended)

## Configuration

- Environment variables override `.env` file
- See `.env.example` for all options
- Production validation enabled on startup
- Secrets support: AWS Secrets Manager, HashiCorp Vault, GCP

## Documentation

- `SPEC.md` - Comprehensive implementation specification
- `docs/api-reference.md` - API documentation
- `docs/architecture.md` - System architecture
- `docs/data_model.md` - Data model details
- `CONTRIBUTING.md` - Contribution guidelines
