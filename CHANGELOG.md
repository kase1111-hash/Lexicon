# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- GraphQL API mounted at `/graphql` with GraphiQL playground: `lsr`,
  `searchLsr`, `language(s)`, `etymology`, `semanticTrajectory`, `dateText`,
  `detectAnachronisms`, and `ancestors`/`descendants`/`cognates` fields on LSR
- Embedding pipeline: deterministic hashed n-gram encoder generating
  384-dimension semantic vectors at ingestion, persisted to Neo4j; PCA
  dimensionality reduction; semantic drift now computes real drift metrics
  and 2D trajectory coordinates
- CLICS adapter computing colexifications from CLDF wordlists
  (`--source clics`, `make ingest-clics`)
- Historical corpus adapter scanning dated local text documents into
  attested entries (`--source corpus`, `make ingest-corpus`)
- Phonetic matching: Soundex, Metaphone, feature-based IPA distance, IPA
  normalization, and named sound laws (Grimm, Verner, rhotacism, final
  devoicing, lenition); entity resolution phonetic candidate retrieval
- Async bulk-export jobs: `POST /graph/bulk/export` with `run_async`, live
  `GET /graph/bulk/status/{job_id}`, and `GET /graph/bulk/result/{job_id}`;
  sync exports return actual data (JSON items with relationships, or CSV)
- `lexicon` console script for the CLI; `ls-ingest` points at the packaged
  ingestion driver (`src/ingestion.py`)
- Alembic migrations wired to `make db-migrate` (plus downgrade/revision/
  history targets)
- `scripts/load_initial_data.py` seeds languages into PostgreSQL and writes
  the semantic-field taxonomy; `scripts/benchmark.py` measures real API
  latency, embedding throughput, entity-resolution speed, and Neo4j
  traversal
- LICENSE file (MIT)
- CHANGELOG.md following Keep a Changelog format

### Fixed
- Startup logs report actual per-database connection outcomes instead of
  unconditionally claiming success
- `sqlalchemy`, `alembic`, and `email-validator` declared in
  requirements.txt (previously imported but not installable)
- `make ingest-wiktionary` / `make ingest-clld` invoke the real ingestion
  pipeline (previously imported a module and exited without ingesting)

## [0.1.0] - 2024-01-01

### Added

#### Core Features
- Lexical State Record (LSR) data model for cross-linguistic lexical evolution
- Etymology tracing and borrowing path detection
- Text dating using diachronic vocabulary attestation patterns
- Semantic drift analysis for tracking meaning changes
- Language contact event detection
- Anachronism detection for historical text analysis

#### Data Adapters
- Wiktionary adapter for etymological data ingestion
- CLLD/WOLD adapter for loanword data with borrowing scores
- Entity resolution and deduplication pipeline
- Relationship extraction from etymology text
- Validation pipeline

#### API
- REST API with FastAPI framework
- OpenAPI/Swagger documentation at `/docs`
- ReDoc alternative documentation at `/redoc`
- Health check endpoint with database status
- Prometheus-compatible metrics endpoint
- Rate limiting with configurable thresholds
- API key authentication support

#### Storage Layer
- Neo4j integration for graph-based lexical relationships
- PostgreSQL for relational metadata
- Elasticsearch for full-text search capabilities
- Redis for caching and rate limiting

#### Infrastructure
- Docker Compose multi-service orchestration
- Environment-specific configurations (development, production)

#### Testing
- Unit, integration, regression, performance, and security test suites

#### Build & Deployment
- Makefile with comprehensive build targets
- Cross-platform build scripts (Unix shell, Windows batch)
- GitHub Actions CI/CD pipeline
- Docker image building
- Wheel and sdist package generation
- Dependabot configuration for dependency updates

#### Documentation
- README with quick start guide
- API reference documentation
- Architecture overview with Mermaid diagrams
- Data model reference
- FAQ and troubleshooting guides
- Contributing guidelines
- Code style guide

#### Monitoring & Observability
- Structured logging with configurable levels
- Optional Sentry error tracking
- Request/response timing metrics

#### Security
- Input validation and sanitization
- Environment-variable-based configuration with masked logging
- API key authentication
- Rate limiting protection

### Technical Details

#### Dependencies
- Python 3.11+
- FastAPI for REST API
- Strawberry GraphQL for the GraphQL endpoint
- Neo4j Python driver for graph database
- SQLAlchemy + Alembic for PostgreSQL models and migrations
- Elasticsearch-py for search
- Pydantic for data validation
- NumPy for numerical operations

#### Development Tools
- Ruff for linting and formatting
- Black for formatting
- Mypy for static type checking
- Bandit for security scanning
- Pytest for testing
- Coverage.py for code coverage

## Types of Changes

- `Added` for new features
- `Changed` for changes in existing functionality
- `Deprecated` for soon-to-be removed features
- `Removed` for now removed features
- `Fixed` for any bug fixes
- `Security` for vulnerability fixes

[Unreleased]: https://github.com/kase1111-hash/Lexicon/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/kase1111-hash/Lexicon/releases/tag/v0.1.0
