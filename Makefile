# Lexicon - Makefile
# Common development and deployment commands

.PHONY: help install install-dev test test-unit test-integration test-db test-db-start \
        test-db-stop test-cov lint format type-check security-check audit \
        docker-up docker-down docker-build docker-logs clean pre-commit \
        db-init db-migrate run-api ingest-wiktionary ingest-clld ingest-clics ingest-corpus \
        build dist publish version-check release-check package-all docker-image \
        version-bump-patch version-bump-minor version-bump-major

# Tools run as `python -m <tool>` so they use the interpreter that has the
# project's dependencies, not whatever same-named command is first on PATH
PYTHON ?= python

# Default target
help:
	@echo "Lexicon - Available Commands"
	@echo "============================"
	@echo ""
	@echo "Development:"
	@echo "  make install       Install dependencies and the lexicon/ls-api/ls-ingest commands"
	@echo "  make install-dev   Install development dependencies and the pre-commit hook"
	@echo "  make test          Run all tests (no database needed; DB tests are skipped)"
	@echo "  make test-unit     Run unit tests only"
	@echo "  make test-integration Run integration tests (DB tests skipped without TEST_NEO4J_URI)"
	@echo "  make test-db       Run all tests against a throwaway Neo4j (TEST_NEO4J_URI)"
	@echo "  make test-db-start Start that throwaway Neo4j (bolt://localhost:7688)"
	@echo "  make test-cov      Run tests with coverage report"
	@echo "  make lint          Run linter (ruff)"
	@echo "  make format        Format code (black)"
	@echo "  make type-check    Run type checker (mypy)"
	@echo "  make security-check Run security scanner (bandit)"
	@echo "  make audit         Check pinned dependencies for known vulnerabilities"
	@echo "  make pre-commit    Run all pre-commit hooks"
	@echo "  make clean         Remove build artifacts"
	@echo ""
	@echo "Docker:"
	@echo "  make docker-up     Start all services"
	@echo "  make docker-down   Stop all services"
	@echo "  make docker-build  Build Docker images"
	@echo "  make docker-logs   View service logs"
	@echo "  make docker-ps     Show running containers"
	@echo ""
	@echo "Database:"
	@echo "  make db-init       Start the databases and create the Neo4j schema"
	@echo "                     (WITH_POSTGRES=1 also the optional PostgreSQL)"
	@echo "  make db-migrate    Apply PostgreSQL migrations (optional profile)"
	@echo ""
	@echo "Data:"
	@echo "  make ingest-clld   Ingest WOLD loanwords (downloads the CLDF data)"
	@echo "  make ingest-corpus Ingest the 4-excerpt sample corpus (format demo; skews dating)"
	@echo ""
	@echo "Build & Release:"
	@echo "  make build         Build wheel package"
	@echo "  make dist          Create wheel and source distribution"
	@echo "  make docker-image  Build Docker image"
	@echo "  make package-zip   Create zip archive"
	@echo "  make package-all   Build all distributable packages"
	@echo "  make release-check Run all checks before release"
	@echo "  make publish       Publish to PyPI (requires credentials)"
	@echo ""
	@echo "Versioning:"
	@echo "  make version-check      Show current version"
	@echo "  make version-bump-patch Bump patch version (0.1.0 -> 0.1.1)"
	@echo "  make version-bump-minor Bump minor version (0.1.0 -> 0.2.0)"
	@echo "  make version-bump-major Bump major version (0.1.0 -> 1.0.0)"
	@echo ""

# =============================================================================
# Development Commands
# =============================================================================

install:
	$(PYTHON) -m pip install -r requirements.txt
	$(PYTHON) -m pip install --no-deps -e .

install-dev:
	$(PYTHON) -m pip install -r requirements-dev.txt
	$(PYTHON) -m pip install --no-deps -e .
	pre-commit install

# Tests never touch your databases: tests/conftest.py ignores .env and points
# every store at an unreachable address unless a TEST_* variable names one.
test:
	$(PYTHON) -m pytest tests/

test-unit:
	$(PYTHON) -m pytest tests/unit/

test-integration:
	$(PYTHON) -m pytest tests/integration/

# DB-backed tests create and delete nodes, so give them a throwaway Neo4j:
#   make test-db-start && make test-db && make test-db-stop
# or point TEST_NEO4J_URI / TEST_NEO4J_PASSWORD at another disposable instance.
TEST_NEO4J_PORT ?= 7688
TEST_NEO4J_URI ?= bolt://localhost:$(TEST_NEO4J_PORT)
TEST_NEO4J_PASSWORD ?= testpassword123
TEST_NEO4J_CONTAINER ?= lexicon-test-neo4j

test-db:
	TEST_NEO4J_URI=$(TEST_NEO4J_URI) TEST_NEO4J_PASSWORD=$(TEST_NEO4J_PASSWORD) $(PYTHON) -m pytest tests/

test-db-start:
	docker run -d --rm --name $(TEST_NEO4J_CONTAINER) -p 127.0.0.1:$(TEST_NEO4J_PORT):7687 \
		-e NEO4J_AUTH=neo4j/$(TEST_NEO4J_PASSWORD) \
		-e NEO4J_server_memory_heap_max__size=512m neo4j:5.9
	@echo "Waiting for Neo4j on bolt://localhost:$(TEST_NEO4J_PORT) ..."
	@for i in $$(seq 1 90); do \
		docker exec $(TEST_NEO4J_CONTAINER) cypher-shell -u neo4j -p '$(TEST_NEO4J_PASSWORD)' \
			'RETURN 1' >/dev/null 2>&1 && echo "Neo4j is up" && exit 0; \
		sleep 1; \
	done; echo "Neo4j did not start" >&2; exit 1

test-db-stop:
	docker rm -f $(TEST_NEO4J_CONTAINER)

# Performance tests assert timings, which coverage tracing distorts
test-cov:
	$(PYTHON) -m pytest tests/ --ignore=tests/performance --cov=src --cov-report=html --cov-report=term-missing

lint:
	$(PYTHON) -m ruff check src tests

lint-fix:
	$(PYTHON) -m ruff check src tests --fix

format:
	$(PYTHON) -m black src tests

format-check:
	$(PYTHON) -m black src tests --check

type-check:
	$(PYTHON) -m mypy src

security-check:
	$(PYTHON) -m bandit -r src -c pyproject.toml

audit:
	$(PYTHON) -m pip_audit -r requirements.txt

pre-commit:
	pre-commit run --all-files

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".mypy_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "htmlcov" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	find . -type f -name ".coverage" -delete 2>/dev/null || true
	rm -rf build dist .eggs

# =============================================================================
# Docker Commands
# =============================================================================

docker-up:
	docker compose up -d

docker-down:
	docker compose down

docker-build:
	docker compose build

docker-rebuild:
	docker compose build --no-cache

docker-logs:
	docker compose logs -f

docker-ps:
	docker compose ps

docker-clean:
	docker compose down -v --remove-orphans

# =============================================================================
# Database Commands
# =============================================================================

# Starts neo4j/elasticsearch/redis (plus postgres with WITH_POSTGRES=1) and
# creates the schemas with the credentials in .env
db-init:
	PYTHON=$(PYTHON) bash scripts/setup_databases.sh

# PostgreSQL is optional (reserved for future use); start it first with
#   docker compose --profile postgres up -d postgres
db-migrate:
	$(PYTHON) -m alembic upgrade head

db-migrate-down:
	$(PYTHON) -m alembic downgrade -1

db-revision:
	$(PYTHON) -m alembic revision --autogenerate -m "$(MSG)"

db-migrate-history:
	$(PYTHON) -m alembic history --verbose

# =============================================================================
# API Commands
# =============================================================================

run-api:
	$(PYTHON) -m uvicorn src.api.main:app --reload --host 127.0.0.1 --port 8000

# Workers share async export jobs and rate-limit counters only through Redis,
# which the API uses only when REDIS_URI or REDIS_PASSWORD is set; unless that
# Redis answers, this runs a single worker.
API_WORKERS ?= 4

run-api-prod:
	@workers=$(API_WORKERS); \
	if [ "$$workers" -gt 1 ] && ! $(PYTHON) -c "import sys, redis; from src.utils.db import DatabaseConfig; c = DatabaseConfig(); sys.exit(0 if c.redis_configured and redis.Redis.from_url(c.redis_uri, socket_connect_timeout=2).ping() else 1)" >/dev/null 2>&1; then \
		echo "Redis is not configured or not reachable: starting 1 worker instead of $$workers"; \
		workers=1; \
	fi; \
	exec $(PYTHON) -m uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --workers $$workers

# =============================================================================
# Ingestion Commands
# =============================================================================

ingest-wiktionary:
	$(PYTHON) -m src.ingestion --source wiktionary --words data/seed_words_eng.txt

ingest-clld:
	$(PYTHON) -m src.ingestion --source wold

ingest-clics:
	$(PYTHON) -m src.ingestion --source clics

# data/corpus ships a few short dated public-domain excerpts (see its README).
# They show the format; their dates only say which of four texts a word is in.
ingest-corpus:
	$(PYTHON) -m src.ingestion --source corpus --corpus-dir data/corpus --language English

# =============================================================================
# Build & Release Commands
# =============================================================================

build:
	$(PYTHON) -m pip install build
	$(PYTHON) -m build --wheel

dist:
	$(PYTHON) -m pip install build
	$(PYTHON) -m build

version-check:
	@echo "VERSION file: $$(cat VERSION)"
	@echo "pyproject.toml: $$(grep '^version' pyproject.toml | head -1)"
	@echo "src/__init__.py: $$($(PYTHON) -c "from src import __version__; print(__version__)")"

version-bump-patch:
	$(PYTHON) scripts/bump_version.py patch

version-bump-minor:
	$(PYTHON) scripts/bump_version.py minor

version-bump-major:
	$(PYTHON) scripts/bump_version.py major

release-check: lint type-check security-check test
	@echo ""
	@echo "✓ All release checks passed!"
	@echo ""
	$(MAKE) version-check

publish: dist
	$(PYTHON) -m pip install twine
	$(PYTHON) -m twine upload dist/*

publish-test: dist
	$(PYTHON) -m pip install twine
	$(PYTHON) -m twine upload --repository testpypi dist/*

# =============================================================================
# Convenience Aliases
# =============================================================================

.PHONY: dev check all

dev: install-dev
	@echo "Development environment ready!"

check: lint type-check security-check
	@echo "All checks complete!"

all: clean install-dev check test build
	@echo "Full build complete!"

# =============================================================================
# Package Distribution
# =============================================================================

# Same name as the release workflow publishes and the production overlay runs
IMAGE ?= ghcr.io/kase1111-hash/lexicon

docker-image:
	docker build -t $(IMAGE):$$($(PYTHON) -c "from src import __version__; print(__version__)") .
	docker tag $(IMAGE):$$($(PYTHON) -c "from src import __version__; print(__version__)") $(IMAGE):latest
	@echo "Docker image built successfully"

package-zip:
	@mkdir -p dist
	zip -r dist/linguistic-stratigraphy-$$($(PYTHON) -c "from src import __version__; print(__version__)").zip \
		src/ requirements.txt requirements-dev.txt pyproject.toml README.md LICENSE \
		Makefile Dockerfile docker-compose.yml config/ scripts/ \
		-x "*.pyc" -x "*/__pycache__/*" -x "*.egg-info/*"

package-all: clean dist docker-image package-zip
	@echo ""
	@echo "All packages built:"
	@ls -la dist/
	@echo ""
	@docker images $(IMAGE) --format "table {{.Repository}}\t{{.Tag}}\t{{.Size}}"

# =============================================================================
# Release Commands
# =============================================================================

release: release-check
	./scripts/release.sh $$(cat VERSION)

release-tag:
	@VERSION=$$(cat VERSION); \
	git tag -a "v$$VERSION" -m "Release version $$VERSION"; \
	echo "Created tag v$$VERSION"

release-archive:
	@mkdir -p releases
	@VERSION=$$(cat VERSION); \
	ARCHIVE="releases/linguistic-stratigraphy-$$VERSION.tar.gz"; \
	tar -czf "$$ARCHIVE" \
		--exclude='.git' \
		--exclude='__pycache__' \
		--exclude='*.pyc' \
		--exclude='.pytest_cache' \
		--exclude='.mypy_cache' \
		--exclude='.ruff_cache' \
		--exclude='venv' \
		--exclude='.venv' \
		--exclude='.env' \
		--exclude='*.log' \
		src/ tests/ docs/ scripts/ dist/ \
		pyproject.toml requirements.txt requirements-dev.txt \
		README.md LICENSE CHANGELOG.md VERSION Makefile \
		docker-compose.yml Dockerfile; \
	sha256sum "$$ARCHIVE" > "$$ARCHIVE.sha256"; \
	echo "Archive created: $$ARCHIVE"

release-push:
	@VERSION=$$(cat VERSION); \
	git push origin "v$$VERSION"; \
	echo "Pushed tag v$$VERSION to origin"
