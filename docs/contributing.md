# Contributing Guide

## Development Setup

1. Clone the repository
2. Install dependencies: `pip install -e ".[dev]"`
3. Start services: `docker-compose up -d`
4. Run setup: `./scripts/setup_databases.sh`

## Code Style

- Python 3.11+
- Use type hints
- Format with Black
- Lint with Ruff
- Type check with mypy

Run all checks:
```bash
black src tests
ruff check src tests
mypy src
```

## Testing

Run tests with pytest:
```bash
pytest tests/unit
pytest tests/integration
pytest  # all tests
```

## Pull Request Process

1. Create a feature branch
2. Write tests for new functionality
3. Ensure all tests pass
4. Update documentation
5. Submit PR with clear description

## Architecture Decisions

When proposing significant changes, document the decision:

1. Context and problem statement
2. Considered options
3. Decision and rationale
4. Consequences

## Data Model Changes

Changes to the LSR schema or graph model require:

1. Migration script for existing data
2. Version bump
3. API compatibility consideration
4. Documentation update
