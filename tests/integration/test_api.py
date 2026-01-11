"""Integration tests for the API."""

import pytest
from fastapi.testclient import TestClient

from src.api.main import app

client = TestClient(app)


class TestRootEndpoints:
    """Tests for root API endpoints."""

    def test_root(self):
        """Test root endpoint."""
        response = client.get("/")
        assert response.status_code == 200
        data = response.json()
        assert data["message"] == "Linguistic Stratigraphy API"
        assert "version" in data

    def test_health(self):
        """Test health check endpoint."""
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "healthy"


class TestLSREndpoints:
    """Tests for LSR API endpoints."""

    def test_search_empty(self):
        """Test search with no results."""
        response = client.get("/api/v1/lsr/search")
        assert response.status_code == 200
        data = response.json()
        assert data["results"] == []
        assert data["total"] == 0


class TestAnalysisEndpoints:
    """Tests for analysis API endpoints."""

    def test_date_text(self):
        """Test text dating endpoint."""
        response = client.post(
            "/api/v1/analyze/date-text",
            json={"text": "test text", "language": "eng"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "predicted_date_range" in data
        assert "confidence" in data

    def test_detect_anachronisms(self):
        """Test anachronism detection endpoint."""
        response = client.post(
            "/api/v1/analyze/detect-anachronisms",
            json={"text": "test text", "claimed_date": 1500, "language": "eng"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "verdict" in data
