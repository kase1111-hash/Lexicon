# Coding Conventions & Style Guide

## Overview

This document defines the coding standards and conventions for Lexicon. All contributors must follow these guidelines to maintain code consistency and quality.

## Python Version

- **Required:** Python 3.11+
- Use modern Python features (type hints, pattern matching, dataclasses)

## Code Formatting

### Black (Formatter)
- Line length: 100 characters
- Use double quotes for strings
- Run before committing: `black src tests`

### Ruff (Linter)
- Enforces PEP 8 compliance
- Run before committing: `ruff check src tests`

## Type Hints

All code must include type hints:

```python
# Good
def process_entry(entry: RawLexicalEntry, language: str) -> LSR:
    ...

# Bad
def process_entry(entry, language):
    ...
```

### Type Checking
- Use mypy for static type analysis
- Run: `mypy src`
- Target: Zero type errors in CI

## Naming Conventions

### Variables and Functions
- Use `snake_case` for variables and functions
- Be descriptive: `calculate_similarity_score` not `calc_sim`

```python
# Good
def extract_etymology_chain(lsr_id: UUID) -> list[LSR]:
    ancestor_ids = []
    current_node = get_lsr(lsr_id)
    ...

# Bad
def get_etym(id):
    ids = []
    n = get(id)
    ...
```

### Classes
- Use `PascalCase` for class names
- Use descriptive names: `EntityResolver` not `ER`

```python
# Good
class RelationshipExtractor:
    ...

# Bad
class RelExtract:
    ...
```

### Constants
- Use `SCREAMING_SNAKE_CASE`
- Define at module level

```python
# Good
MAX_BATCH_SIZE = 1000
DEFAULT_CONFIDENCE_THRESHOLD = 0.85

# Bad
maxBatchSize = 1000
```

### Private Members
- Prefix with single underscore: `_internal_method`
- Never use double underscore for "privacy"

```python
# Good
class Processor:
    def __init__(self):
        self._cache = {}

    def _validate_input(self, data: dict) -> bool:
        ...

# Bad
class Processor:
    def __init__(self):
        self.__cache = {}  # Avoid name mangling
```

## Documentation

### Docstrings
- Use Google-style docstrings
- Required for all public functions, classes, and modules

```python
def calculate_similarity(entry1: LSR, entry2: LSR) -> float:
    """Calculate similarity score between two LSRs.

    Uses a weighted combination of form matching, semantic similarity,
    and temporal overlap to determine overall similarity.

    Args:
        entry1: First LSR to compare.
        entry2: Second LSR to compare.

    Returns:
        Similarity score between 0.0 and 1.0.

    Raises:
        ValueError: If either LSR has no language_code.

    Example:
        >>> lsr1 = LSR(form_orthographic="water", language_code="eng")
        >>> lsr2 = LSR(form_orthographic="Wasser", language_code="deu")
        >>> score = calculate_similarity(lsr1, lsr2)
        >>> print(f"Similarity: {score:.2f}")
    """
    ...
```

### Comments
- Explain "why", not "what"
- Keep comments up to date with code changes

```python
# Good: Explains reasoning
# Use Levenshtein distance < 2 to catch common OCR errors
if levenshtein_distance(form1, form2) < 2:
    ...

# Bad: States the obvious
# Check if distance is less than 2
if levenshtein_distance(form1, form2) < 2:
    ...
```

## Imports

### Order
1. Standard library imports
2. Third-party imports
3. Local application imports

Separate each group with a blank line.

```python
# Good
import asyncio
from datetime import datetime
from uuid import UUID

import httpx
from fastapi import APIRouter, HTTPException

from src.models.lsr import LSR
from src.utils.db import DatabaseManager
```

### Style
- Prefer absolute imports over relative imports
- Avoid wildcard imports (`from module import *`)
- Import specific names when possible

```python
# Good
from src.models.lsr import LSR, Attestation

# Acceptable for large modules
from src import models

# Bad
from src.models.lsr import *
```

## Error Handling

### Exceptions
- Use specific exception types
- Include helpful error messages
- Don't catch exceptions silently

```python
# Good: a LexiconError subclass from src/exceptions.py; the API maps its
# code and HTTP status (here LSR_NOT_FOUND, 404) into the error response
async def get_lsr(repo: LSRRepository, lsr_id: UUID) -> LSR:
    if not await repo.exists(lsr_id):
        raise LSRNotFoundError(lsr_id=str(lsr_id))
    return await repo.get_by_id(lsr_id)

# Bad
def get_lsr(lsr_id):
    try:
        return db.query(lsr_id)
    except:  # Never catch bare exceptions
        pass
```

### Logging
- Use the `logging` module, not `print()`
- Include context in log messages

```python
import logging

logger = logging.getLogger(__name__)

# Good
logger.info("Processing batch", extra={"batch_size": len(entries), "source": "wiktionary"})
logger.error("Failed to fetch entry", extra={"entry_id": entry_id}, exc_info=True)

# Bad
print(f"Processing {len(entries)} entries")
```

## Async/Await

- Use `async`/`await` for I/O-bound operations
- Prefer `asyncio.gather()` for concurrent operations
- Always use async context managers for connections

```python
# Good
async def fetch_all_entries(ids: list[UUID]) -> list[LSR]:
    async with DatabaseManager() as db:
        repo = LSRRepository(db)
        return await asyncio.gather(*(repo.get_by_id(lsr_id) for lsr_id in ids))

# Bad
def fetch_all_entries(ids):
    results = []
    for id in ids:
        results.append(db.get_lsr(id))  # Sequential, blocking
    return results
```

## Testing

### Test Naming
- Test files: `test_<module>.py`
- Test functions: `test_<description>`
- Test classes: `Test<ClassName>`

```python
# tests/unit/test_phonetics.py

class TestPhoneticUtils:
    def test_strip_diacritics_removes_accents(self):
        result = PhoneticUtils.strip_diacritics("café")
        assert result == "cafe"

    def test_levenshtein_distance_identical_strings(self):
        assert PhoneticUtils.levenshtein_distance("test", "test") == 0
```

### Test Structure
- Use Arrange-Act-Assert pattern
- One assertion per test when possible
- Use fixtures for common setup

```python
@pytest.fixture
def sample_lsr():
    return LSR(
        form_orthographic="water",
        language_code="eng",
        definition_primary="a colorless liquid",
    )

def test_lsr_normalize_form(sample_lsr):
    # Arrange
    sample_lsr.form_orthographic = "Water"

    # Act
    sample_lsr.normalize_form()

    # Assert
    assert sample_lsr.form_normalized == "water"
```

## Data Classes and Models

### Pydantic Models and Dataclasses
- Use Pydantic models for records that are validated or serialized: `LSR`
  (`src/models/lsr.py`) and the API request models (`src/utils/validation.py`)
- Use `@dataclass` for plain internal results, such as the analysis results
  in `src/analysis/`
- Use `field(default_factory=...)` for mutable defaults

```python
from dataclasses import dataclass, field

@dataclass
class AnachronismAnalysis:
    anachronisms: list[dict] = field(default_factory=list)
    verdict: str = "insufficient_data"
    confidence: float = 0.0
```

### Use Enums for Fixed Values
```python
from enum import StrEnum

class RelationshipType(StrEnum):
    DESCENDS_FROM = "DESCENDS_FROM"
    BORROWED_FROM = "BORROWED_FROM"
    COGNATE_OF = "COGNATE_OF"
```

## Configuration Files

This project uses the following configuration:

- `pyproject.toml` - Project metadata, dependencies, and the Black, Ruff, mypy, Bandit and coverage configuration
- `pytest.ini` - pytest configuration
- `.python-version` - Python version specification
- `.editorconfig` - Editor settings
- `.pre-commit-config.yaml` - Pre-commit hooks

## Pre-commit Hooks

All contributors should install pre-commit hooks:

```bash
pip install -r requirements-dev.txt
pre-commit install
```

Hooks run automatically on commit:
1. File checks (trailing whitespace, end of file, YAML/JSON/TOML syntax, large files, merge conflicts, private keys)
2. Black (formatting)
3. Ruff (linting, with `--fix`)
4. Bandit (security checks, everything except `tests/`)
5. mypy (type checking, `mypy src`)

The hooks do not run the tests; run `pytest` yourself before committing.

## Git Conventions

### Commit Messages
- Use imperative mood: "Add feature" not "Added feature"
- First line: 50 characters max, summary
- Body: Wrap at 72 characters, explain "why"

```
Add cognate detection based on phonetic similarity

Implements sound correspondence matching using feature-based
phonetic distance calculation. This enables automatic identification
of cognate relationships across related languages.

- Add PhoneticDistance class with IPA feature vectors
- Integrate with RelationshipExtractor pipeline
- Add unit tests for Germanic sound correspondences
```

### Branch Names
- Feature: `feature/description`
- Bug fix: `fix/description`
- Refactor: `refactor/description`

## Summary Checklist

Before submitting code:

- [ ] Code formatted with Black
- [ ] No Ruff linting errors
- [ ] No mypy type errors
- [ ] All tests pass
- [ ] Docstrings for public APIs
- [ ] Type hints on all functions
- [ ] Meaningful variable/function names
- [ ] No hardcoded secrets or credentials
