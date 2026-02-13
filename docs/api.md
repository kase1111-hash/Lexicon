# API Documentation

## Overview

The Linguistic Stratigraphy API provides REST and GraphQL interfaces for querying and analyzing cross-linguistic lexical evolution data.

**Base URL:** `/api/v1`

**Authentication:** API key via `X-API-Key` header (disabled by default; set `API_KEY` in `.env` to enable)

For detailed endpoint documentation with request/response examples, see [api-reference.md](api-reference.md).

## REST Endpoints

### LSR Operations

#### GET /api/v1/lsr/{id}
Get a full Lexical State Record by ID.

#### GET /api/v1/lsr/search
Search for LSRs matching criteria.

**Parameters:**
- `form` (string): Form to search (exact or fuzzy)
- `language` (string): ISO 639-3 language code
- `date_start` (integer): Start of date range
- `date_end` (integer): End of date range
- `semantic_field` (string): Semantic field filter
- `limit` (integer): Max results (default 20, max 100)
- `offset` (integer): Pagination offset

#### POST /api/v1/lsr/
Create a new LSR record.

#### DELETE /api/v1/lsr/{id}
Delete an LSR record.

#### GET /api/v1/lsr/{id}/etymology
Get the full ancestor chain to proto-form.

#### GET /api/v1/lsr/{id}/descendants
Get the descendant tree.

**Parameters:**
- `depth` (integer): Max depth (default 3, max 10)

#### GET /api/v1/lsr/{id}/cognates
Get all cognate LSRs across languages.

#### GET /api/v1/lsr/{id}/borrowings
Get borrowing relationships (both incoming and outgoing).

### Analysis Endpoints

#### POST /api/v1/analyze/date-text
Predict the date range of a text based on vocabulary.

**Request:**
```json
{
  "text": "string",
  "language": "string"
}
```

#### POST /api/v1/analyze/detect-anachronisms
Detect anachronistic vocabulary in a text.

**Request:**
```json
{
  "text": "string",
  "claimed_date": 0,
  "language": "string"
}
```

#### GET /api/v1/analyze/contact-events
Get detected language contact events.

**Parameters:**
- `language` (string, required): ISO 639-3 language code
- `date_start` (integer): Start year
- `date_end` (integer): End year

#### GET /api/v1/analyze/semantic-drift
Get semantic drift trajectory for a word.

**Parameters:**
- `form` (string, required): Word form to analyze
- `language` (string, required): ISO 639-3 language code

#### GET /api/v1/analyze/compare-concept
Compare how a concept is expressed across languages.

**Parameters:**
- `concept` (string, required): Concept to compare
- `languages` (string, required): Comma-separated ISO 639-3 codes

### Graph Endpoints

#### POST /api/v1/graph/query
Execute a Cypher graph query (validated for safety).

#### GET /api/v1/graph/path
Find all shortest paths between two LSRs.

#### GET /api/v1/graph/etymology/{lsr_id}
Get etymology chain via graph traversal.

#### GET /api/v1/graph/cognates/{lsr_id}
Get cognates via graph traversal.

#### POST /api/v1/graph/bulk/export
Export LSR data for a language.

## Rate Limits

Default: 100 requests per minute per API key (configurable via environment variables).

## GraphQL

The GraphQL endpoint is available at `/graphql`.

See `src/api/graphql/schema.py` for the full schema definition. You can use the GraphQL playground at `/graphql` in the browser for interactive exploration.
