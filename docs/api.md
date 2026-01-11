# API Documentation

## Overview

The Linguistic Stratigraphy API provides REST and GraphQL interfaces for querying and analyzing cross-linguistic lexical evolution data.

**Base URL:** `/api/v1`

**Authentication:** API key (header) or OAuth2

## REST Endpoints

### LSR Operations

#### GET /lsr/{id}
Get a full Lexical State Record by ID.

**Response:**
```json
{
  "id": "uuid",
  "form_orthographic": "string",
  "form_phonetic": "string",
  "language_code": "string",
  "date_start": 0,
  "date_end": 0,
  "definitions": ["string"],
  "confidence": 0.0
}
```

#### GET /lsr/search
Search for LSRs matching criteria.

**Parameters:**
- `form` (string): Form to search (exact or fuzzy)
- `language` (string): ISO 639-3 language code
- `date_start` (integer): Start of date range
- `date_end` (integer): End of date range
- `semantic_field` (string): Semantic field filter
- `limit` (integer): Max results (default 20, max 100)
- `offset` (integer): Pagination offset

#### GET /lsr/{id}/etymology
Get the full ancestor chain to proto-form.

#### GET /lsr/{id}/descendants
Get the descendant tree.

**Parameters:**
- `depth` (integer): Max depth (default 3)

#### GET /lsr/{id}/cognates
Get all cognate LSRs across languages.

### Analysis Endpoints

#### POST /analyze/date-text
Predict the date range of a text based on vocabulary.

**Request:**
```json
{
  "text": "string",
  "language": "string"
}
```

**Response:**
```json
{
  "predicted_date_range": [0, 0],
  "confidence": 0.0,
  "diagnostic_vocabulary": [
    {"form": "string", "date_contribution": 0.0}
  ]
}
```

#### POST /analyze/detect-anachronisms
Detect anachronistic vocabulary in a text.

**Request:**
```json
{
  "text": "string",
  "claimed_date": 0,
  "language": "string"
}
```

#### GET /analyze/contact-events
Get detected language contact events.

#### GET /analyze/semantic-drift
Get semantic drift trajectory for a word.

### Graph Endpoints

#### POST /graph/query
Execute a graph query (Cypher or GraphQL).

#### GET /graph/path
Find all paths between two LSRs.

## Rate Limits

- Anonymous: 100 requests/hour
- Authenticated: 10,000 requests/hour
- Bulk exports: 10/day

## GraphQL

The GraphQL endpoint is available at `/graphql`.

See `schema.py` for the full schema definition.
