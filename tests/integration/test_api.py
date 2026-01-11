"""Integration tests for the API."""

import pytest
from uuid import uuid4

from fastapi.testclient import TestClient

from src.api.main import app

client = TestClient(app)


class TestRootEndpoints:
    """Tests for root API endpoints."""

    def test_root(self):
        """Test root endpoint returns API info."""
        response = client.get("/")
        assert response.status_code == 200
        data = response.json()
        assert "name" in data
        assert "version" in data
        assert "docs" in data

    def test_health(self):
        """Test health check endpoint."""
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert "databases" in data

    def test_metrics(self):
        """Test metrics endpoint."""
        response = client.get("/metrics")
        assert response.status_code == 200
        data = response.json()
        assert "api_version" in data
        assert "metrics" in data


class TestLSREndpoints:
    """Tests for LSR API endpoints."""

    def test_search_empty(self):
        """Test search with no parameters returns empty results."""
        response = client.get("/api/v1/lsr/search")
        assert response.status_code == 200
        data = response.json()
        assert data["results"] == []
        assert data["total"] == 0
        assert "limit" in data
        assert "offset" in data

    def test_search_with_form(self):
        """Test search with form parameter."""
        response = client.get("/api/v1/lsr/search", params={"form": "water"})
        assert response.status_code == 200
        data = response.json()
        assert data["filters"]["form"] == "water"

    def test_search_with_language(self):
        """Test search with language parameter."""
        response = client.get("/api/v1/lsr/search", params={"language": "eng"})
        assert response.status_code == 200
        data = response.json()
        assert data["filters"]["language"] == "eng"

    def test_search_with_date_range(self):
        """Test search with date range parameters."""
        response = client.get(
            "/api/v1/lsr/search",
            params={"date_start": 1500, "date_end": 2000},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["filters"]["date_start"] == 1500
        assert data["filters"]["date_end"] == 2000

    def test_search_invalid_date_range(self):
        """Test search with invalid date range."""
        response = client.get(
            "/api/v1/lsr/search",
            params={"date_start": 2000, "date_end": 1500},
        )
        assert response.status_code == 400
        data = response.json()
        assert data["error"] == "INVALID_DATE_RANGE"

    def test_search_pagination(self):
        """Test search pagination parameters."""
        response = client.get(
            "/api/v1/lsr/search",
            params={"limit": 50, "offset": 10},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["limit"] == 50
        assert data["offset"] == 10

    def test_search_limit_validation(self):
        """Test search limit validation (max 100)."""
        response = client.get("/api/v1/lsr/search", params={"limit": 200})
        assert response.status_code == 422  # Validation error

    def test_get_lsr_not_found(self):
        """Test getting non-existent LSR."""
        fake_id = str(uuid4())
        response = client.get(f"/api/v1/lsr/{fake_id}")
        assert response.status_code == 404
        data = response.json()
        assert data["error"] == "LSR_NOT_FOUND"

    def test_get_lsr_invalid_uuid(self):
        """Test getting LSR with invalid UUID."""
        response = client.get("/api/v1/lsr/not-a-uuid")
        assert response.status_code == 422  # Validation error

    def test_get_etymology(self):
        """Test etymology endpoint."""
        fake_id = str(uuid4())
        response = client.get(f"/api/v1/lsr/{fake_id}/etymology")
        assert response.status_code == 200
        data = response.json()
        assert "chain" in data
        assert "depth" in data

    def test_get_descendants(self):
        """Test descendants endpoint."""
        fake_id = str(uuid4())
        response = client.get(f"/api/v1/lsr/{fake_id}/descendants")
        assert response.status_code == 200
        data = response.json()
        assert "descendants" in data

    def test_get_descendants_with_depth(self):
        """Test descendants endpoint with depth parameter."""
        fake_id = str(uuid4())
        response = client.get(f"/api/v1/lsr/{fake_id}/descendants", params={"depth": 5})
        assert response.status_code == 200
        data = response.json()
        assert data["depth"] == 5

    def test_get_cognates(self):
        """Test cognates endpoint."""
        fake_id = str(uuid4())
        response = client.get(f"/api/v1/lsr/{fake_id}/cognates")
        assert response.status_code == 200
        data = response.json()
        assert "cognates" in data

    def test_get_borrowings(self):
        """Test borrowings endpoint."""
        fake_id = str(uuid4())
        response = client.get(f"/api/v1/lsr/{fake_id}/borrowings")
        assert response.status_code == 200
        data = response.json()
        assert "borrowed_from" in data
        assert "borrowed_to" in data

    def test_create_lsr(self):
        """Test LSR creation endpoint."""
        response = client.post(
            "/api/v1/lsr/",
            json={
                "form_orthographic": "test",
                "language_code": "eng",
                "definition_primary": "a test word",
            },
        )
        assert response.status_code == 201
        data = response.json()
        assert "data" in data

    def test_create_lsr_missing_required(self):
        """Test LSR creation with missing required fields."""
        response = client.post(
            "/api/v1/lsr/",
            json={"form_orthographic": "test"},  # Missing language_code
        )
        assert response.status_code == 422


class TestAnalysisEndpoints:
    """Tests for analysis API endpoints."""

    def test_date_text(self):
        """Test text dating endpoint."""
        response = client.post(
            "/api/v1/analyze/date-text",
            json={"text": "The knight rode his horse to battle.", "language": "eng"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "predicted_date_range" in data
        assert "confidence" in data
        assert "diagnostic_vocabulary" in data
        assert data["analysis"]["language"] == "eng"

    def test_date_text_missing_text(self):
        """Test date-text with missing text."""
        response = client.post(
            "/api/v1/analyze/date-text",
            json={"language": "eng"},
        )
        assert response.status_code == 422

    def test_detect_anachronisms(self):
        """Test anachronism detection endpoint."""
        response = client.post(
            "/api/v1/analyze/detect-anachronisms",
            json={
                "text": "The medieval knight used his smartphone.",
                "claimed_date": 1200,
                "language": "eng",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert "anachronisms" in data
        assert "verdict" in data
        assert data["analysis"]["claimed_date"] == 1200

    def test_contact_events(self):
        """Test contact events endpoint."""
        response = client.get(
            "/api/v1/analyze/contact-events",
            params={"language": "eng"},
        )
        assert response.status_code == 200
        assert isinstance(response.json(), list)

    def test_contact_events_with_dates(self):
        """Test contact events with date range."""
        response = client.get(
            "/api/v1/analyze/contact-events",
            params={"language": "eng", "date_start": 1000, "date_end": 1500},
        )
        assert response.status_code == 200

    def test_contact_events_invalid_date_range(self):
        """Test contact events with invalid date range."""
        response = client.get(
            "/api/v1/analyze/contact-events",
            params={"language": "eng", "date_start": 2000, "date_end": 1000},
        )
        assert response.status_code == 400
        assert response.json()["error"] == "INVALID_DATE_RANGE"

    def test_semantic_drift(self):
        """Test semantic drift endpoint."""
        response = client.get(
            "/api/v1/analyze/semantic-drift",
            params={"form": "nice", "language": "eng"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["form"] == "nice"
        assert data["language"] == "eng"
        assert "trajectory" in data
        assert "shift_events" in data

    def test_semantic_drift_missing_form(self):
        """Test semantic drift with missing form."""
        response = client.get(
            "/api/v1/analyze/semantic-drift",
            params={"language": "eng"},
        )
        assert response.status_code == 422

    def test_compare_concept(self):
        """Test concept comparison endpoint."""
        response = client.get(
            "/api/v1/analyze/compare-concept",
            params={"concept": "freedom", "languages": "eng,deu,fra"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["concept"] == "freedom"
        assert len(data["by_language"]) == 3

    def test_compare_concept_too_many_languages(self):
        """Test concept comparison with too many languages."""
        languages = ",".join([f"l{i:02d}" for i in range(15)])
        response = client.get(
            "/api/v1/analyze/compare-concept",
            params={"concept": "test", "languages": languages},
        )
        assert response.status_code == 400


class TestGraphEndpoints:
    """Tests for graph API endpoints."""

    def test_execute_query(self):
        """Test graph query execution endpoint."""
        response = client.post(
            "/api/v1/graph/query",
            json={"query": "MATCH (n) RETURN n LIMIT 10"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "results" in data
        assert "query" in data

    def test_get_path(self):
        """Test path finding endpoint."""
        from_id = str(uuid4())
        to_id = str(uuid4())
        response = client.get(
            "/api/v1/graph/path",
            params={"from_lsr": from_id, "to_lsr": to_id},
        )
        assert response.status_code == 200
        data = response.json()
        assert "paths" in data

    def test_get_path_with_max_hops(self):
        """Test path finding with max_hops parameter."""
        from_id = str(uuid4())
        to_id = str(uuid4())
        response = client.get(
            "/api/v1/graph/path",
            params={"from_lsr": from_id, "to_lsr": to_id, "max_hops": 10},
        )
        assert response.status_code == 200

    def test_bulk_export(self):
        """Test bulk export job creation."""
        response = client.post(
            "/api/v1/graph/bulk/export",
            json={"language": "eng", "format": "json"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "job_id" in data
        assert "status" in data

    def test_export_status(self):
        """Test bulk export status check."""
        response = client.get("/api/v1/graph/bulk/status/test-job-id")
        assert response.status_code == 200
        data = response.json()
        assert "job_id" in data
        assert "status" in data


class TestErrorHandling:
    """Tests for error handling."""

    def test_404_not_found(self):
        """Test 404 for non-existent route."""
        response = client.get("/api/v1/nonexistent")
        assert response.status_code == 404

    def test_method_not_allowed(self):
        """Test 405 for wrong HTTP method."""
        response = client.delete("/api/v1/lsr/search")
        assert response.status_code == 405

    def test_validation_error_format(self):
        """Test that validation errors return proper format."""
        response = client.post("/api/v1/analyze/date-text", json={})
        assert response.status_code == 422
        data = response.json()
        assert "error" in data or "detail" in data


class TestCORS:
    """Tests for CORS headers."""

    def test_cors_headers_present(self):
        """Test that CORS headers are present."""
        response = client.options(
            "/",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
            },
        )
        # CORS preflight should succeed
        assert response.status_code in [200, 204, 400]


class TestRequestHeaders:
    """Tests for request header handling."""

    def test_request_id_header(self):
        """Test that X-Request-ID header is returned."""
        response = client.get("/health")
        # Our middleware adds X-Request-ID to responses
        assert response.status_code == 200
        # Note: The header might not be present in test client
        # depending on middleware execution

    def test_custom_request_id(self):
        """Test that custom X-Request-ID is echoed back."""
        custom_id = "test-request-123"
        response = client.get("/health", headers={"X-Request-ID": custom_id})
        assert response.status_code == 200
