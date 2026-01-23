# Contributing to Computational Linguistic Stratigraphy

Thank you for your interest in contributing to this project! We welcome contributions from the community and are grateful for any help you can provide.

## Table of Contents

- [Getting Started](#getting-started)
- [Development Setup](#development-setup)
- [Code Style](#code-style)
- [Testing](#testing)
- [Submitting Changes](#submitting-changes)
- [Pull Request Process](#pull-request-process)
- [Reporting Bugs](#reporting-bugs)
- [Requesting Features](#requesting-features)
- [Architecture Decisions](#architecture-decisions)

## Getting Started

1. Fork the repository on GitHub
2. Clone your fork locally:
   ```bash
   git clone https://github.com/YOUR_USERNAME/Lexicon.git
   cd Lexicon
   ```
3. Add the upstream repository as a remote:
   ```bash
   git remote add upstream https://github.com/kase1111-hash/Lexicon.git
   ```
4. Create a branch for your changes:
   ```bash
   git checkout -b feature/your-feature-name
   ```

## Development Setup

### Prerequisites

- Python 3.11 or higher
- Docker and Docker Compose
- 16GB+ RAM recommended

### Installation

1. Copy the environment template and configure:
   ```bash
   cp .env.example .env
   # Edit .env with your settings
   ```

2. Start the required services:
   ```bash
   docker compose up -d
   ```

3. Create a virtual environment and install dependencies:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   make install-dev
   ```

4. Initialize the databases:
   ```bash
   ./scripts/setup_databases.sh
   ```

5. Install pre-commit hooks:
   ```bash
   pre-commit install
   ```

## Code Style

We maintain consistent code style through automated tooling:

- **Python Version**: 3.11+
- **Type Hints**: Required for all public functions and methods
- **Formatter**: [Black](https://github.com/psf/black) with 100 character line length
- **Linter**: [Ruff](https://github.com/astral-sh/ruff) for fast Python linting
- **Type Checker**: [mypy](https://mypy.readthedocs.io/) in strict mode

### Running Code Quality Checks

```bash
# Format code
make format

# Run linter
make lint

# Run type checker
make type-check

# Run security scan
make security-check

# Run all checks
make pre-commit
```

See [docs/style-guide.md](docs/style-guide.md) for detailed style guidelines.

## Testing

We use [pytest](https://pytest.org/) for testing with a minimum coverage requirement of 80%.

### Running Tests

```bash
# Run all tests
make test

# Run unit tests only
make test-unit

# Run integration tests
make test-integration

# Run with coverage report
make test-cov

# Run specific test file
pytest tests/unit/test_specific.py

# Run tests with specific marker
pytest -m "not slow"
```

### Test Categories

- **Unit Tests** (`tests/unit/`): Test individual components in isolation
- **Integration Tests** (`tests/integration/`): Test component interactions
- **Acceptance Tests** (`tests/acceptance/`): Test user workflows
- **Performance Tests** (`tests/performance/`): Load and stress testing
- **Security Tests** (`tests/security/`): Security-focused testing

### Writing Tests

- Write tests for all new functionality
- Follow the Arrange-Act-Assert pattern
- Use descriptive test names that explain what is being tested
- Use fixtures for common setup (see `tests/conftest.py`)

## Submitting Changes

### Commit Messages

Write clear, concise commit messages:

- Use the present tense ("Add feature" not "Added feature")
- Use the imperative mood ("Move cursor to..." not "Moves cursor to...")
- Limit the first line to 72 characters
- Reference issues and pull requests when relevant

Example:
```
Add semantic drift analysis for Indo-European languages

- Implement DriftAnalyzer class with configurable time windows
- Add support for cognate-based drift detection
- Include unit tests for drift calculation methods

Fixes #123
```

### Before Submitting

1. Ensure all tests pass: `make test`
2. Run code quality checks: `make lint && make type-check`
3. Update documentation if needed
4. Add or update tests for your changes

## Pull Request Process

1. Update the README.md or relevant documentation with details of changes if applicable
2. Add any new dependencies to `requirements.txt` or `pyproject.toml`
3. Ensure your PR description clearly describes the problem and solution
4. Link any related issues in your PR description
5. Request review from maintainers

### PR Requirements

- All CI checks must pass
- Code review approval required
- Documentation updated (if applicable)
- Tests added/updated (if applicable)

## Reporting Bugs

When reporting bugs, please include:

1. **Description**: A clear and concise description of the bug
2. **Steps to Reproduce**: Detailed steps to reproduce the behavior
3. **Expected Behavior**: What you expected to happen
4. **Actual Behavior**: What actually happened
5. **Environment**:
   - Python version
   - Operating system
   - Docker version (if applicable)
   - Relevant dependency versions
6. **Logs/Screenshots**: Any relevant logs or screenshots

Use the [bug report template](.github/ISSUE_TEMPLATE/bug_report.md) when creating issues.

## Requesting Features

Feature requests are welcome! Please:

1. Check existing issues to avoid duplicates
2. Clearly describe the use case and motivation
3. Explain how the feature fits the project's goals
4. Consider offering to implement the feature yourself

Use the [feature request template](.github/ISSUE_TEMPLATE/feature_request.md) when creating issues.

## Architecture Decisions

For significant changes that affect the project architecture:

1. Open an issue to discuss the change before implementing
2. Document the decision using the following template:
   - **Context**: What is the issue or need?
   - **Options Considered**: What alternatives were evaluated?
   - **Decision**: What was decided and why?
   - **Consequences**: What are the trade-offs?

### Data Model Changes

Changes to the LSR schema or graph model require:

1. Migration script for existing data
2. Version bump (following semantic versioning)
3. API compatibility consideration
4. Documentation update

## Questions?

If you have questions about contributing:

- Check the [FAQ](docs/faq.md)
- Review the [Troubleshooting Guide](docs/troubleshooting.md)
- Open a discussion on GitHub

Thank you for contributing!
