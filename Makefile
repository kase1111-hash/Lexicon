# Linguistic Stratigraphy - Makefile
# Common development and deployment commands

.PHONY: help install install-dev test lint format type-check security-check \
        docker-up docker-down docker-build docker-logs clean pre-commit \
        build dist publish version-check release-check package-all docker-image \
        version-bump-patch version-bump-minor version-bump-major

# Default target
help:
	@echo "Linguistic Stratigraphy - Available Commands"
	@echo "============================================="
	@echo ""
	@echo "Development:"
	@echo "  make install       Install production dependencies"
	@echo "  make install-dev   Install development dependencies"
	@echo "  make test          Run all tests"
	@echo "  make test-unit     Run unit tests only"
	@echo "  make test-cov      Run tests with coverage report"
	@echo "  make lint          Run linter (ruff)"
	@echo "  make format        Format code (black)"
	@echo "  make type-check    Run type checker (mypy)"
	@echo "  make security-check Run security scanner (bandit)"
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
	@echo "  make db-init       Initialize databases"
	@echo "  make db-migrate    Run database migrations"
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
	pip install -r requirements.txt

install-dev:
	pip install -r requirements-dev.txt
	pre-commit install

test:
	pytest tests/ -v

test-unit:
	pytest tests/unit/ -v

test-integration:
	pytest tests/integration/ -v -m integration

test-cov:
	pytest tests/ --cov=src --cov-report=html --cov-report=term-missing

lint:
	ruff check src tests

lint-fix:
	ruff check src tests --fix

format:
	black src tests

format-check:
	black src tests --check

type-check:
	mypy src

security-check:
	bandit -r src -c pyproject.toml

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

db-init:
	./scripts/setup_databases.sh

db-migrate:
	@echo "Database migrations not yet implemented"

# =============================================================================
# API Commands
# =============================================================================

run-api:
	uvicorn src.api.main:app --reload --host 0.0.0.0 --port 8000

run-api-prod:
	uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --workers 4

# =============================================================================
# Ingestion Commands
# =============================================================================

ingest-wiktionary:
	python -m src.adapters.wiktionary

ingest-clld:
	python -m src.adapters.clld

# =============================================================================
# Build & Release Commands
# =============================================================================

build:
	pip install build
	python -m build --wheel

dist:
	pip install build
	python -m build

version-check:
	@echo "VERSION file: $$(cat VERSION)"
	@echo "pyproject.toml: $$(grep '^version' pyproject.toml | head -1)"
	@echo "src/__init__.py: $$(python -c "from src import __version__; print(__version__)")"

version-bump-patch:
	python scripts/bump_version.py patch

version-bump-minor:
	python scripts/bump_version.py minor

version-bump-major:
	python scripts/bump_version.py major

release-check: lint type-check security-check test
	@echo ""
	@echo "✓ All release checks passed!"
	@echo ""
	$(MAKE) version-check

publish: dist
	pip install twine
	twine upload dist/*

publish-test: dist
	pip install twine
	twine upload --repository testpypi dist/*

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

docker-image:
	docker build -t linguistic-stratigraphy:$$(python -c "from src import __version__; print(__version__)") .
	docker tag linguistic-stratigraphy:$$(python -c "from src import __version__; print(__version__)") linguistic-stratigraphy:latest
	@echo "Docker image built successfully"

package-zip:
	@mkdir -p dist
	zip -r dist/linguistic-stratigraphy-$$(python -c "from src import __version__; print(__version__)").zip \
		src/ requirements.txt requirements-dev.txt pyproject.toml README.md LICENSE \
		Makefile Dockerfile docker-compose.yml config/ scripts/ \
		-x "*.pyc" -x "*/__pycache__/*" -x "*.egg-info/*"

package-all: clean dist docker-image package-zip
	@echo ""
	@echo "All packages built:"
	@ls -la dist/
	@echo ""
	@docker images linguistic-stratigraphy --format "table {{.Repository}}\t{{.Tag}}\t{{.Size}}"
