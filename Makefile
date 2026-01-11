# Linguistic Stratigraphy - Makefile
# Common development and deployment commands

.PHONY: help install install-dev test lint format type-check security-check \
        docker-up docker-down docker-build docker-logs clean pre-commit

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
