# API Reference

The Linguistic Stratigraphy API provides RESTful endpoints for lexical evolution analysis.

## Base URL

- **Development**: `http://localhost:8000`
- **Production**: `https://api.linguistic-stratigraphy.com`

## Interactive Documentation

- **Swagger UI**: `/docs` - Interactive API explorer
- **ReDoc**: `/redoc` - Alternative documentation format
- **OpenAPI Spec**: `/openapi.json` - Machine-readable specification

## Authentication

API key authentication (when enabled):

```bash
curl -H "X-API-Key: your-api-key" https://api.example.com/lsr/search
```

## Endpoints

### LSR (Lexical State Records)

#### Search LSRs

```http
POST /lsr/search
Content-Type: application/json

{
  "query": "water",
  "language_codes": ["eng", "deu"],
  "date_range": {"start": 1500, "end": 1800},
  "limit": 50
}
```

#### Get LSR by ID

```http
GET /lsr/{id}
```

#### Create LSR

```http
POST /lsr/
Content-Type: application/json

{
  "form": "water",
  "form_normalized": "water",
  "language_code": "eng",
  "date_start": 1000,
  "date_end": 1500,
  "register": "common",
  "definitions": ["clear liquid..."],
  "source": "OED"
}
```

### Analysis

#### Trace Etymology

```http
POST /analysis/etymology
Content-Type: application/json

{
  "form": "computer",
  "language_code": "eng",
  "max_depth": 5
}
```

Response:

```json
{
  "word": "computer",
  "etymology_chain": [
    {"form": "computer", "language": "eng", "date": "1640"},
    {"form": "computare", "language": "lat", "date": "-100"}
  ],
  "borrowing_path": ["lat -> eng"]
}
```

#### Date Text

```http
POST /analysis/date-text
Content-Type: application/json

{
  "text": "The quick brown fox...",
  "language_code": "eng"
}
```

#### Detect Language Contact

```http
POST /analysis/contact-events
Content-Type: application/json

{
  "language_codes": ["eng", "fra"],
  "date_range": {"start": 1000, "end": 1500}
}
```

#### Analyze Semantic Drift

```http
POST /analysis/semantic-drift
Content-Type: application/json

{
  "form": "nice",
  "language_code": "eng"
}
```

### Graph

#### Get Neighbors

```http
GET /graph/neighbors/{lsr_id}?depth=2&relationship_types=BORROWED_FROM,COGNATE_OF
```

#### Find Path

```http
POST /graph/path
Content-Type: application/json

{
  "source_id": "uuid-1",
  "target_id": "uuid-2",
  "max_depth": 10
}
```

### Monitoring

#### Health Check

```http
GET /health
```

Response:

```json
{
  "status": "healthy",
  "version": "0.1.0",
  "databases": {
    "neo4j": "connected",
    "postgres": "connected",
    "elasticsearch": "connected",
    "redis": "connected"
  }
}
```

#### Metrics (Prometheus)

```http
GET /metrics
```

Returns Prometheus-format metrics.

#### Metrics (JSON)

```http
GET /metrics/json
```

Returns metrics as JSON for debugging.

## Error Handling

All errors follow a consistent format:

```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "LSR not found",
    "details": {"id": "uuid-123"},
    "request_id": "req-456"
  }
}
```

### Error Codes

| Code | HTTP Status | Description |
|------|-------------|-------------|
| `VALIDATION_ERROR` | 400 | Invalid request parameters |
| `AUTHENTICATION_ERROR` | 401 | Missing or invalid API key |
| `AUTHORIZATION_ERROR` | 403 | Insufficient permissions |
| `NOT_FOUND` | 404 | Resource not found |
| `DUPLICATE_ERROR` | 409 | Resource already exists |
| `RATE_LIMIT_ERROR` | 429 | Too many requests |
| `DATABASE_ERROR` | 500 | Database operation failed |
| `ANALYSIS_ERROR` | 500 | Analysis operation failed |

## Rate Limiting

Default limits:
- 100 requests per minute per API key
- 429 response when exceeded

Headers returned:
- `X-RateLimit-Limit`: Request limit
- `X-RateLimit-Remaining`: Requests remaining
- `X-RateLimit-Reset`: Reset timestamp

## Pagination

List endpoints support pagination:

```http
GET /lsr/list?offset=0&limit=50
```

Response includes pagination metadata:

```json
{
  "data": [...],
  "pagination": {
    "offset": 0,
    "limit": 50,
    "total": 1234,
    "has_more": true
  }
}
```

## Versioning

API version is included in responses and can be checked at `/health`.

Current version: `0.1.0`

## SDKs and Libraries

### Python

```bash
pip install linguistic-stratigraphy
```

```python
from linguistic_stratigraphy import Client

client = Client(api_key="your-key")
results = client.search_lsr(query="water", language_codes=["eng"])
```

## Postman Collection

Import the OpenAPI spec directly into Postman:

1. Open Postman
2. Click "Import"
3. Select "Link" tab
4. Enter: `http://localhost:8000/openapi.json`
5. Click "Import"
