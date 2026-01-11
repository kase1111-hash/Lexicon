#!/bin/bash
# Linguistic Stratigraphy - Unix Build Script
# Usage: ./build.sh [command]
# Commands: install, install-dev, test, lint, build, clean, help

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

print_header() {
    echo ""
    echo -e "${GREEN}Linguistic Stratigraphy - Build Script${NC}"
    echo "======================================="
    echo ""
}

print_help() {
    print_header
    echo "Usage: ./build.sh [command]"
    echo ""
    echo "Commands:"
    echo "  install        Install production dependencies"
    echo "  install-dev    Install development dependencies"
    echo "  test           Run all tests"
    echo "  test-unit      Run unit tests only"
    echo "  test-cov       Run tests with coverage"
    echo "  lint           Run linter (ruff)"
    echo "  lint-fix       Run linter with auto-fix"
    echo "  format         Format code (black)"
    echo "  type-check     Run type checker (mypy)"
    echo "  security-check Run security scanner (bandit)"
    echo "  build          Build wheel package"
    echo "  dist           Create distribution packages"
    echo "  clean          Remove build artifacts"
    echo "  check          Run all checks (lint, type, security)"
    echo "  run-api        Start the API server"
    echo "  docker-up      Start Docker services"
    echo "  docker-down    Stop Docker services"
    echo "  help           Show this help message"
    echo ""
}

cmd_install() {
    echo -e "${YELLOW}Installing production dependencies...${NC}"
    pip install -r requirements.txt
    echo -e "${GREEN}Done!${NC}"
}

cmd_install_dev() {
    echo -e "${YELLOW}Installing development dependencies...${NC}"
    pip install -r requirements-dev.txt
    if [ -f .pre-commit-config.yaml ]; then
        pre-commit install
    fi
    echo -e "${GREEN}Done!${NC}"
}

cmd_test() {
    echo -e "${YELLOW}Running tests...${NC}"
    pytest tests/ -v
}

cmd_test_unit() {
    echo -e "${YELLOW}Running unit tests...${NC}"
    pytest tests/unit/ -v
}

cmd_test_cov() {
    echo -e "${YELLOW}Running tests with coverage...${NC}"
    pytest tests/ --cov=src --cov-report=html --cov-report=term-missing
}

cmd_lint() {
    echo -e "${YELLOW}Running linter...${NC}"
    ruff check src tests
}

cmd_lint_fix() {
    echo -e "${YELLOW}Running linter with auto-fix...${NC}"
    ruff check src tests --fix
}

cmd_format() {
    echo -e "${YELLOW}Formatting code...${NC}"
    black src tests
}

cmd_type_check() {
    echo -e "${YELLOW}Running type checker...${NC}"
    mypy src --ignore-missing-imports
}

cmd_security_check() {
    echo -e "${YELLOW}Running security scanner...${NC}"
    bandit -r src -c pyproject.toml
}

cmd_build() {
    echo -e "${YELLOW}Building wheel package...${NC}"
    pip install build
    python -m build --wheel
    echo -e "${GREEN}Build complete! Check dist/ directory.${NC}"
}

cmd_dist() {
    echo -e "${YELLOW}Creating distribution packages...${NC}"
    pip install build
    python -m build
    echo -e "${GREEN}Distribution packages created in dist/${NC}"
}

cmd_clean() {
    echo -e "${YELLOW}Cleaning build artifacts...${NC}"
    rm -rf build dist .eggs *.egg-info
    rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov
    find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
    find . -type f -name "*.pyc" -delete 2>/dev/null || true
    find . -type f -name ".coverage" -delete 2>/dev/null || true
    echo -e "${GREEN}Done!${NC}"
}

cmd_check() {
    echo -e "${YELLOW}Running all checks...${NC}"
    echo ""
    echo "=== Linting ==="
    ruff check src tests || true
    echo ""
    echo "=== Type Checking ==="
    mypy src --ignore-missing-imports || true
    echo ""
    echo "=== Security Scanning ==="
    bandit -r src -c pyproject.toml || true
    echo ""
    echo -e "${GREEN}All checks complete!${NC}"
}

cmd_run_api() {
    echo -e "${YELLOW}Starting API server...${NC}"
    uvicorn src.api.main:app --reload --host 0.0.0.0 --port 8000
}

cmd_docker_up() {
    echo -e "${YELLOW}Starting Docker services...${NC}"
    docker compose up -d
}

cmd_docker_down() {
    echo -e "${YELLOW}Stopping Docker services...${NC}"
    docker compose down
}

# Main command dispatcher
case "${1:-help}" in
    install)        cmd_install ;;
    install-dev)    cmd_install_dev ;;
    test)           cmd_test ;;
    test-unit)      cmd_test_unit ;;
    test-cov)       cmd_test_cov ;;
    lint)           cmd_lint ;;
    lint-fix)       cmd_lint_fix ;;
    format)         cmd_format ;;
    type-check)     cmd_type_check ;;
    security-check) cmd_security_check ;;
    build)          cmd_build ;;
    dist)           cmd_dist ;;
    clean)          cmd_clean ;;
    check)          cmd_check ;;
    run-api)        cmd_run_api ;;
    docker-up)      cmd_docker_up ;;
    docker-down)    cmd_docker_down ;;
    help)           print_help ;;
    *)
        echo -e "${RED}Unknown command: $1${NC}"
        print_help
        exit 1
        ;;
esac
