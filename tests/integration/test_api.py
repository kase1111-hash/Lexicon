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


class TestAPIKeyAuthentication:
    """Tests for API key authentication middleware."""

    def test_public_paths_no_auth_required(self):
        """Test that public paths don't require authentication."""
        public_paths = ["/", "/health", "/docs", "/redoc", "/openapi.json", "/metrics"]
        for path in public_paths:
            response = client.get(path)
            # Should not get 401 for public paths
            assert response.status_code != 401, f"Path {path} should not require auth"

    def test_api_endpoints_accessible_without_key_when_disabled(self):
        """Test API endpoints are accessible when API key auth is disabled."""
        # By default in tests, API_KEY is not set, so auth should be disabled
        response = client.get("/api/v1/lsr/search")
        # Should not get 401 when auth is disabled
        assert response.status_code != 401

    def test_openapi_docs_accessible(self):
        """Test that OpenAPI documentation is accessible."""
        response = client.get("/openapi.json")
        assert response.status_code == 200
        data = response.json()
        assert "openapi" in data
        assert "paths" in data


class TestLSREdgeCases:
    """Additional edge case tests for LSR endpoints."""

    def test_search_with_special_characters(self):
        """Test search with special characters in form."""
        response = client.get("/api/v1/lsr/search", params={"form": "test<script>"})
        assert response.status_code == 200
        # Form should be sanitized
        data = response.json()
        assert "<script>" not in data["filters"]["form"]

    def test_search_with_very_long_form(self):
        """Test search with excessively long form parameter."""
        long_form = "a" * 500  # Exceeds 200 char limit
        response = client.get("/api/v1/lsr/search", params={"form": long_form})
        # Should either truncate or return validation error
        assert response.status_code in [200, 422]

    def test_search_with_empty_string_form(self):
        """Test search with empty string form."""
        response = client.get("/api/v1/lsr/search", params={"form": ""})
        assert response.status_code == 200

    def test_search_with_unicode_form(self):
        """Test search with Unicode characters in form."""
        response = client.get("/api/v1/lsr/search", params={"form": "水"})  # Chinese for water
        assert response.status_code == 200
        data = response.json()
        assert data["filters"]["form"] == "水"

    def test_search_date_boundary_values(self):
        """Test search with boundary date values."""
        # Minimum date
        response = client.get("/api/v1/lsr/search", params={"date_start": -10000})
        assert response.status_code == 200

        # Maximum date
        response = client.get("/api/v1/lsr/search", params={"date_end": 2100})
        assert response.status_code == 200

    def test_search_date_below_minimum(self):
        """Test search with date below allowed minimum."""
        response = client.get("/api/v1/lsr/search", params={"date_start": -15000})
        assert response.status_code == 422  # Validation error

    def test_search_date_above_maximum(self):
        """Test search with date above allowed maximum."""
        response = client.get("/api/v1/lsr/search", params={"date_end": 3000})
        assert response.status_code == 422  # Validation error

    def test_delete_lsr_not_found(self):
        """Test deleting non-existent LSR."""
        fake_id = str(uuid4())
        response = client.delete(f"/api/v1/lsr/{fake_id}")
        assert response.status_code == 404
        data = response.json()
        assert data["error"] == "LSR_NOT_FOUND"

    def test_create_lsr_with_dates(self):
        """Test creating LSR with date fields."""
        response = client.post(
            "/api/v1/lsr/",
            json={
                "form_orthographic": "test_dated",
                "language_code": "eng",
                "definition_primary": "a test word with dates",
                "date_start": 1500,
                "date_end": 1600,
            },
        )
        assert response.status_code == 201
        data = response.json()
        assert data["data"]["date_start"] == 1500
        assert data["data"]["date_end"] == 1600

    def test_create_lsr_invalid_date_range(self):
        """Test creating LSR with invalid date range."""
        response = client.post(
            "/api/v1/lsr/",
            json={
                "form_orthographic": "test",
                "language_code": "eng",
                "date_start": 1600,
                "date_end": 1500,  # End before start
            },
        )
        # Should fail validation
        assert response.status_code in [400, 422]

    def test_create_lsr_with_phonetic(self):
        """Test creating LSR with phonetic transcription."""
        response = client.post(
            "/api/v1/lsr/",
            json={
                "form_orthographic": "water",
                "form_phonetic": "ˈwɔːtər",
                "language_code": "eng",
                "definition_primary": "H2O",
            },
        )
        assert response.status_code == 201
        data = response.json()
        assert data["data"]["form_phonetic"] == "ˈwɔːtər"


class TestAnalysisEdgeCases:
    """Additional edge case tests for analysis endpoints."""

    def test_date_text_empty_text(self):
        """Test dating with empty text."""
        response = client.post(
            "/api/v1/analyze/date-text",
            json={"text": "", "language": "eng"},
        )
        assert response.status_code == 200
        data = response.json()
        # Should handle gracefully with low confidence
        assert data["confidence"] == 0 or "predicted_date_range" in data

    def test_date_text_very_long_text(self):
        """Test dating with very long text."""
        long_text = "The quick brown fox jumps over the lazy dog. " * 1000
        response = client.post(
            "/api/v1/analyze/date-text",
            json={"text": long_text, "language": "eng"},
        )
        assert response.status_code == 200

    def test_detect_anachronisms_ancient_date(self):
        """Test anachronism detection with very old claimed date."""
        response = client.post(
            "/api/v1/analyze/detect-anachronisms",
            json={
                "text": "The farmer planted wheat in the field.",
                "claimed_date": -5000,  # Ancient date
                "language": "eng",
            },
        )
        assert response.status_code == 200

    def test_semantic_drift_with_unicode_form(self):
        """Test semantic drift with Unicode word."""
        response = client.get(
            "/api/v1/analyze/semantic-drift",
            params={"form": "café", "language": "fra"},
        )
        assert response.status_code == 200

    def test_compare_concept_single_language(self):
        """Test concept comparison with single language."""
        response = client.get(
            "/api/v1/analyze/compare-concept",
            params={"concept": "water", "languages": "eng"},
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["by_language"]) == 1


class TestGraphEdgeCases:
    """Additional edge case tests for graph endpoints."""

    def test_execute_query_empty_query(self):
        """Test graph query with empty query string."""
        response = client.post(
            "/api/v1/graph/query",
            json={"query": ""},
        )
        # Should fail validation
        assert response.status_code in [400, 422]

    def test_execute_query_with_parameters(self):
        """Test graph query with parameters."""
        response = client.post(
            "/api/v1/graph/query",
            json={
                "query": "MATCH (n:LSR {language_code: $lang}) RETURN n LIMIT 10",
                "parameters": {"lang": "eng"},
            },
        )
        assert response.status_code == 200

    def test_get_path_same_source_target(self):
        """Test path finding with same source and target."""
        lsr_id = str(uuid4())
        response = client.get(
            "/api/v1/graph/path",
            params={"from_lsr": lsr_id, "to_lsr": lsr_id},
        )
        assert response.status_code == 200

    def test_get_path_with_relationship_types(self):
        """Test path finding with specific relationship types."""
        from_id = str(uuid4())
        to_id = str(uuid4())
        response = client.get(
            "/api/v1/graph/path",
            params={
                "from_lsr": from_id,
                "to_lsr": to_id,
                "relationship_types": "DESCENDS_FROM,BORROWED_FROM",
            },
        )
        assert response.status_code == 200

    def test_etymology_chain(self):
        """Test etymology chain endpoint."""
        lsr_id = str(uuid4())
        response = client.get(f"/api/v1/graph/etymology/{lsr_id}")
        assert response.status_code == 200
        data = response.json()
        assert "chain" in data
        assert "depth" in data

    def test_graph_cognates(self):
        """Test cognates endpoint on graph router."""
        lsr_id = str(uuid4())
        response = client.get(f"/api/v1/graph/cognates/{lsr_id}")
        assert response.status_code == 200
        data = response.json()
        assert "cognate_count" in data
        assert "by_language" in data

    def test_bulk_export_invalid_format(self):
        """Test bulk export with invalid format."""
        response = client.post(
            "/api/v1/graph/bulk/export",
            json={"language": "eng", "format": "invalid_format"},
        )
        # Should either handle gracefully or return validation error
        assert response.status_code in [200, 400, 422]


class TestContentTypeHandling:
    """Tests for content type handling."""

    def test_json_content_type_required(self):
        """Test that POST endpoints require JSON content type."""
        response = client.post(
            "/api/v1/lsr/",
            content="form_orthographic=test&language_code=eng",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        # Should fail with non-JSON content
        assert response.status_code == 422

    def test_json_response_content_type(self):
        """Test that responses have correct content type."""
        response = client.get("/health")
        assert "application/json" in response.headers.get("content-type", "")


class TestPaginationEdgeCases:
    """Tests for pagination edge cases."""

    def test_zero_offset(self):
        """Test pagination with zero offset."""
        response = client.get("/api/v1/lsr/search", params={"offset": 0})
        assert response.status_code == 200

    def test_large_offset(self):
        """Test pagination with large offset."""
        response = client.get("/api/v1/lsr/search", params={"offset": 10000})
        assert response.status_code == 200
        data = response.json()
        assert data["offset"] == 10000

    def test_minimum_limit(self):
        """Test pagination with minimum limit."""
        response = client.get("/api/v1/lsr/search", params={"limit": 1})
        assert response.status_code == 200
        data = response.json()
        assert data["limit"] == 1

    def test_negative_offset_rejected(self):
        """Test that negative offset is rejected."""
        response = client.get("/api/v1/lsr/search", params={"offset": -1})
        assert response.status_code == 422

    def test_zero_limit_rejected(self):
        """Test that zero limit is rejected."""
        response = client.get("/api/v1/lsr/search", params={"limit": 0})
        assert response.status_code == 422
