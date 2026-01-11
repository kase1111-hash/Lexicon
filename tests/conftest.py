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
        "part_of_speech": "noun",
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
