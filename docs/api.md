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
Export LSR data (and optionally relationships) for a language.

**Request:**
```json
{
  "language": "eng",
  "format": "json",
  "include_relationships": true,
  "run_async": false
}
```

- `format`: `json` (items + relationships) or `csv` (CSV string in `csv`)
- `run_async: false` (default): the export runs inline and the payload is
  returned directly
- `run_async: true`: a background job is created; the response contains
  `job_id`, `status_url`, and `result_url`

#### GET /api/v1/graph/bulk/status/{job_id}
Get the status of a bulk export job (`pending`, `running`, `completed`,
`failed`), including duration and, once completed, a `download_url`.
Finished jobs expire an hour after completion.

#### GET /api/v1/graph/bulk/result/{job_id}
Fetch the payload of a completed bulk export job. Returns 404 for unknown
jobs and the job's error for failed ones.

## Rate Limits

Default: 100 requests per minute per API key (configurable via environment variables).

## GraphQL

The GraphQL endpoint is available at `/graphql`, with the GraphiQL
playground served in the browser at the same path.

Root query fields:

- `lsr(id)` — one record, including nested `ancestors`, `descendants`,
  and `cognates` graph traversals
- `searchLsr(form, language, dateStart, dateEnd, limit, offset)`
- `language(isoCode)` / `languages(family)`
- `etymology(lsrId)` — full chain back to the proto-form
- `semanticTrajectory(form, language)` — drift points and shift events
- `dateText(text, language)` — text dating analysis
- `detectAnachronisms(text, claimedDate, language)`

Example:

```graphql
query {
  searchLsr(form: "water", language: "eng") {
    form
    language { name }
    cognates { form language { name } }
  }
}
```

See `src/api/graphql/schema.py` for the full schema definition.
