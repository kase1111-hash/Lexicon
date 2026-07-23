# API Reference

The Linguistic Stratigraphy API provides RESTful endpoints for lexical evolution analysis.

## Base URL

- **Development**: `http://localhost:8000`
- **API prefix**: `/api/v1`

## Interactive Documentation

- **Swagger UI**: `/docs` - Interactive API explorer
- **ReDoc**: `/redoc` - Alternative documentation format
- **OpenAPI Spec**: `/openapi.json` - Machine-readable specification

## Authentication

API key authentication via the `X-API-Key` header (when enabled via the `API_KEY` environment variable):

```bash
curl -H "X-API-Key: your-api-key" http://localhost:8000/api/v1/lsr/search?form=water
```

Authentication is disabled by default for development. Set `API_KEY` in your `.env` to enable it.

## Endpoints

### LSR (Lexical State Records)

#### Search LSRs

```http
GET /api/v1/lsr/search?form=water&language=eng&limit=20&offset=0
```

**Query Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `form` | string | No | Form to search (exact or fuzzy), max 200 chars |
| `language` | string | No | ISO 639-3 language code |
| `date_start` | integer | No | Start year (negative for BCE) |
| `date_end` | integer | No | End year |
| `semantic_field` | string | No | WordNet synset ID filter |
| `limit` | integer | No | Max results (default 20, max 100) |
| `offset` | integer | No | Pagination offset (default 0) |

**Response:**

```json
{
  "results": [...],
  "total": 42,
  "limit": 20,
  "offset": 0,
  "filters": {
    "form": "water",
    "language": "eng",
    "date_start": null,
    "date_end": null,
    "semantic_field": null
  }
}
```

#### Get LSR by ID

```http
GET /api/v1/lsr/{lsr_id}
```

#### Create LSR

```http
POST /api/v1/lsr/
Content-Type: application/json

{
  "form_orthographic": "water",
  "language_code": "eng",
  "form_phonetic": "ˈwɔːtər",
  "definition_primary": "a clear liquid...",
  "date_start": 1000,
  "date_end": 2024
}
```

Returns `201 Created` on success.

#### Delete LSR

```http
DELETE /api/v1/lsr/{lsr_id}
```

#### Get Etymology Chain

```http
GET /api/v1/lsr/{lsr_id}/etymology
```

Traces DESCENDS_FROM relationships back to the earliest ancestor (proto-form).

**Response:**

```json
{
  "lsr_id": "uuid",
  "chain": [
    {
      "id": "uuid",
      "form": "water",
      "language_code": "eng",
      "language_name": "English",
      "date_start": 1000,
      "date_end": 2024,
      "definition": "a clear liquid..."
    }
  ],
  "proto_form": {...},
  "depth": 3
}
```

#### Get Descendants

```http
GET /api/v1/lsr/{lsr_id}/descendants?depth=3
```

**Query Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `depth` | integer | 3 | Maximum depth to traverse (1-10) |

#### Get Cognates

```http
GET /api/v1/lsr/{lsr_id}/cognates
```

Returns words in other languages that share a common ancestor.

#### Get Borrowings

```http
GET /api/v1/lsr/{lsr_id}/borrowings
```

Returns both words this LSR borrowed from and words that borrowed from it.

### Analysis

#### Date Text

```http
POST /api/v1/analyze/date-text
Content-Type: application/json

{
  "text": "The quick brown fox...",
  "language": "eng"
}
```

**Response:**

```json
{
  "predicted_date_range": [1400, 1600],
  "confidence": 0.75,
  "diagnostic_vocabulary": [
    {"word": "knight", "date_start": 1100, "date_end": 1800, "span": 700, "diagnostic_value": 0.5}
  ],
  "analysis": {
    "language": "eng",
    "text_length": 120,
    "word_count": 25,
    "tokens_analyzed": 25,
    "tokens_matched": 18,
    "method": "vocabulary_attestation"
  }
}
```

#### Detect Anachronisms

```http
POST /api/v1/analyze/detect-anachronisms
Content-Type: application/json

{
  "text": "The knight used a computer",
  "claimed_date": 1300,
  "language": "eng"
}
```

**Response:**

```json
{
  "anachronisms": [
    {
      "word": "computer",
      "earliest_attestation": 1640,
      "claimed_date": 1300,
      "gap_years": 340,
      "severity": "high"
    }
  ],
  "verdict": "anachronistic",
  "confidence": 0.3,
  "explanation": "Multiple anachronisms detected...",
  "analysis": {
    "language": "eng",
    "claimed_date": 1300,
    "words_analyzed": 5
  }
}
```

#### Get Contact Events

```http
GET /api/v1/analyze/contact-events?language=eng&date_start=1000&date_end=1500
```

**Query Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `language` | string | Yes | ISO 639-3 language code |
| `date_start` | integer | No | Start year |
| `date_end` | integer | No | End year |

#### Get Semantic Drift

```http
GET /api/v1/analyze/semantic-drift?form=nice&language=eng
```

**Query Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `form` | string | Yes | Word form to analyze |
| `language` | string | Yes | ISO 639-3 language code |

#### Compare Concept Across Languages

```http
GET /api/v1/analyze/compare-concept?concept=freedom&languages=eng,deu,fra
```

### Graph

#### Execute Cypher Query

```http
POST /api/v1/graph/query
Content-Type: application/json

{
  "query": "MATCH (l:LSR {language_code: $lang}) RETURN l LIMIT 10",
  "parameters": {"lang": "eng"}
}
```

Queries are validated for safety (destructive operations are blocked).

#### Find Path Between LSRs

```http
GET /api/v1/graph/path?from_lsr={uuid}&to_lsr={uuid}&max_hops=5&relationship_types=DESCENDS_FROM,BORROWED_FROM
```

**Query Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `from_lsr` | UUID | Yes | Source LSR ID |
| `to_lsr` | UUID | Yes | Target LSR ID |
| `max_hops` | integer | No | Max path length (default 5, max 20) |
| `relationship_types` | string | No | Comma-separated types to traverse |

#### Get Etymology Chain (Graph)

```http
GET /api/v1/graph/etymology/{lsr_id}?max_depth=10
```

#### Get Cognates (Graph)

```http
GET /api/v1/graph/cognates/{lsr_id}
```

#### Bulk Export

```http
POST /api/v1/graph/bulk/export
Content-Type: application/json

{
  "language": "eng",
  "format": "json",
  "include_relationships": true,
  "run_async": false
}
```

`format` is `json` or `csv`. With `run_async: false` (default) the export
payload is returned directly. With `run_async: true` the response contains
a `job_id` plus `status_url`/`result_url` for the background job:

```http
GET /api/v1/graph/bulk/status/{job_id}
GET /api/v1/graph/bulk/result/{job_id}
```

Job status is one of `pending`, `running`, `completed`, or `failed`;
completed jobs expose a `download_url` and expire an hour after
completion.

### Monitoring

#### Health Check

```http
GET /health
```

Response:

```json
{
  "status": "healthy",
  "api": "up",
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

#### Traces

```http
GET /traces?limit=100
```

Returns recent completed spans for debugging.

## Error Handling

All errors follow a consistent format:

```json
{
  "error": "NOT_FOUND",
  "message": "LSR not found",
  "details": {"id": "uuid-123"}
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
| `INTERNAL_ERROR` | 500 | Unexpected server error |

## Rate Limiting

Default: 100 requests per minute (configurable via `RATE_LIMIT_REQUESTS` and `RATE_LIMIT_WINDOW_SECONDS` environment variables).

A `429 Too Many Requests` response is returned when exceeded, with a `Retry-After` header.

## Pagination

Search endpoints support pagination via `offset` and `limit` query parameters:

```http
GET /api/v1/lsr/search?form=water&offset=0&limit=50
```

Response includes pagination metadata:

```json
{
  "results": [...],
  "total": 1234,
  "limit": 50,
  "offset": 0
}
```

## Postman / OpenAPI

Import the OpenAPI spec directly into Postman or other API clients:

1. Open Postman
2. Click "Import"
3. Select "Link" tab
4. Enter: `http://localhost:8000/openapi.json`
5. Click "Import"
