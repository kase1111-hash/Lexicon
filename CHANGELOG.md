# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- LICENSE file (MIT)
- CHANGELOG.md following Keep a Changelog format

## [0.1.0] - 2024-01-01

### Added

#### Core Features
- Lexical State Record (LSR) data model for cross-linguistic lexical evolution
- Etymology tracing and borrowing path detection
- Text dating classifier using diachronic vocabulary patterns
- Semantic drift analysis for tracking meaning changes
- Language contact event detection
- Anachronism detection for historical text analysis

#### Data Adapters
- Wiktionary adapter for etymological data ingestion
- CLLD/CLICS adapter for cross-linguistic database integration
- Historical corpora adapter with OCR text support
- Entity resolution and deduplication pipeline

#### API
- REST API with FastAPI framework
- GraphQL endpoint for flexible queries
- OpenAPI/Swagger documentation at `/docs`
- ReDoc alternative documentation at `/redoc`
- Health check endpoint with database status
- Prometheus-compatible metrics endpoint
- Rate limiting with configurable thresholds
- API key authentication support

#### Storage Layer
- Neo4j integration for graph-based lexical relationships
- PostgreSQL for metadata and user data
- Elasticsearch for full-text search capabilities
- Milvus for semantic vector similarity search
- Redis for caching and rate limiting

#### Infrastructure
- Docker Compose multi-service orchestration
- Environment-specific configurations (development, staging, production)
- Kubernetes deployment manifests
- Apache Airflow DAG definitions for scheduled pipelines

#### Testing
- Comprehensive unit test suite
- Integration tests for all adapters and services
- System/acceptance tests for user workflows
- Regression test suite
- Performance tests (load and stress testing)
- Security test suite (input validation, secrets handling)

#### Build & Deployment
- Makefile with comprehensive build targets
- Cross-platform build scripts (Unix shell, Windows batch)
- GitHub Actions CI/CD pipeline
- Automated release workflow with PyPI publishing
- Docker image building and registry push
- Wheel and sdist package generation
- Dependabot configuration for dependency updates

#### Documentation
- Comprehensive README with quick start guide
- API reference documentation
- Architecture overview with Mermaid diagrams
- Data model reference
- FAQ and troubleshooting guides
- Contributing guidelines
- Code style guide
- Static analysis report

#### Monitoring & Observability
- Structured logging with configurable levels
- Error logging integration (Sentry, ELK stack)
- Prometheus metrics collection
- OpenTelemetry-compatible distributed tracing
- Request/response timing metrics

#### Security
- Input validation and sanitization
- Secure configuration management with secrets
- Environment variable protection
- API key authentication
- Rate limiting protection

### Technical Details

#### Dependencies
- Python 3.11+
- FastAPI for REST API
- Strawberry GraphQL for GraphQL endpoint
- Neo4j Python driver for graph database
- SQLAlchemy for PostgreSQL ORM
- Elasticsearch-py for search
- Pydantic for data validation
- NumPy for numerical operations

#### Development Tools
- Ruff for linting and formatting
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

[Unreleased]: https://github.com/linguistic-stratigraphy/linguistic-stratigraphy/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/linguistic-stratigraphy/linguistic-stratigraphy/releases/tag/v0.1.0
