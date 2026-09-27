"""Pytest configuration and shared fixtures."""

import os
import sys
from pathlib import Path
from uuid import uuid4

import pytest

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Set test environment
os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("LOG_LEVEL", "WARNING")  # Reduce noise during tests
# The shared TestClient sends every request from one address; limiter tests
# build their own middleware instances
os.environ.setdefault("RATE_LIMIT_ENABLED", "false")

# Never read the developer's .env, and never touch their databases: DB-backed
# tests run only against stores named explicitly via TEST_* variables, e.g.
#   TEST_NEO4J_URI=bolt://localhost:7688 TEST_NEO4J_PASSWORD=... make test
os.environ["ENV_FILE"] = os.environ.get("TEST_ENV_FILE", "/nonexistent-lexicon-test-env")
_UNREACHABLE = {
    "NEO4J_URI": "bolt://127.0.0.1:1",
    "POSTGRES_URI": "postgresql://lexicon:lexicon@127.0.0.1:1/lexicon",
    "ELASTICSEARCH_URI": "http://127.0.0.1:1",
    "REDIS_URI": "redis://127.0.0.1:1",
}
for _key, _unreachable in _UNREACHABLE.items():
    os.environ[_key] = os.environ.get(f"TEST_{_key}", _unreachable)
for _key in ("NEO4J_USER", "NEO4J_PASSWORD"):
    if os.environ.get(f"TEST_{_key}"):
        os.environ[_key] = os.environ[f"TEST_{_key}"]


@pytest.fixture
def sample_uuid():
    """Generate a sample UUID."""
    return uuid4()


@pytest.fixture
def sample_lsr_data():
    """Sample LSR data for testing."""
    return {
        "form_orthographic": "water",
        "form_phonetic": "ˈwɔːtər",
        "language_code": "eng",
        "language_name": "English",
        "definition_primary": "a colorless, transparent liquid",
        "part_of_speech": ["noun"],
        "date_start": 1500,
        "date_end": 2024,
    }


@pytest.fixture
def sample_raw_entry_data():
    """Sample raw lexical entry data for testing."""
    return {
        "source_name": "wiktionary",
        "source_id": "wikt-water-eng",
        "form": "water",
        "language": "English",
        "language_code": "eng",
        "definitions": ["a colorless liquid", "a body of water"],
        "part_of_speech": ["noun"],
        "date_attested": 1200,
    }


@pytest.fixture
def temp_env_vars(monkeypatch):
    """Context manager for temporary environment variables."""

    def _set_env(**kwargs):
        for key, value in kwargs.items():
            monkeypatch.setenv(key, str(value))

    return _set_env


@pytest.fixture(autouse=True)
def reset_singletons():
    """Reset singleton instances between tests."""
    from src.utils.common import Singleton

    # Clear singleton instances
    Singleton._instances = {}
    yield


@pytest.fixture
def mock_db_config():
    """Mock database configuration for testing."""
    return {
        "neo4j_uri": "bolt://localhost:7687",
        "neo4j_user": "test",
        "neo4j_password": "test",
        "postgres_host": "localhost",
        "postgres_port": 5432,
        "postgres_db": "test_db",
        "postgres_user": "test",
        "postgres_password": "test",
    }
