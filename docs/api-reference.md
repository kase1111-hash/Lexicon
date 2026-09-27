# API reference

The Lexicon API serves the word graph and the analyses built on it: dating a
text by its newest word, finding words that postdate a claimed date, listing
the languages a language borrowed from and when, and (experimental) tracing
how a word's senses drift. It has a REST interface under `/api/v1` and a
GraphQL endpoint at `/graphql`.

The running server publishes the authoritative schema:

| Path | What |
|---|---|
| `/docs` | Swagger UI: every route, parameter and body schema |
| `/redoc` | The same schema rendered by ReDoc |
| `/openapi.json` | Machine-readable OpenAPI 3 schema. Import this into Postman or Insomnia |
| `/graphql` (in a browser) | GraphiQL, with the GraphQL schema browsable |

The route and parameter schemas at `/docs` are generated from the code. The
introduction at the top of that page is a short summary of this one.

This page documents the behaviour the schema cannot show: authentication,
rate limits, errors, limits, and how to read analysis results. Every example
response on this page was captured from a running API whose graph held only
the WOLD English data: 2146 records (1516 English forms and their donor
words) and 642 `BORROWED_FROM` edges. The sample corpus in `data/corpus` was
not loaded. WOLD has no `DESCENDS_FROM` or `COGNATE_OF` edges, so the `water`
etymology examples use six records and six edges that were added by hand for
the capture and removed afterwards: Middle English *water*, Old English
*wæter*, Proto-Germanic *\*watōr*, Middle High German *wazzer*, German
*Wasser* and Dutch *water*. Their ids do not exist in your graph. Long arrays
are cut with `…`. For what an LSR is and what its fields mean, see
[data_model.md](data_model.md). For how the pieces fit together, see
[architecture.md](architecture.md).

## Contents

- [Conventions](#conventions)
- [Authentication](#authentication)
- [Rate limiting](#rate-limiting)
- [Errors](#errors)
- [Health and monitoring](#health-and-monitoring)
- [Reading analysis results](#reading-analysis-results)
- [REST endpoints](#rest-endpoints): [Analysis](#analysis), [LSR](#lsr), [Graph](#graph)
- [GraphQL](#graphql)
- [Known limitations](#known-limitations)

## Conventions

The examples use a shell variable for the server:

```bash
export LEXICON=http://localhost:8000
```

`make run-api`, `ls-api` and docker compose all serve on `localhost:8000` by
default.

- **Bodies** are JSON, in requests and responses.
- **Years** are integers; negative years are BCE.
- **Ids** are LSR UUIDs. Ingested records have stable ids derived from the source record.
- **Language codes** are the codes stored on LSRs: ISO 639-3 (`eng`);
  Wiktionary-style extensions for historical languages and proto-languages
  (`gem-pro`, `la-vul`, `roa-opt`: a 2–3 letter code followed by one or two
  segments of 3–4 letters); and Glottocodes (`celt1248`, Celtic: four letters
  and four digits), which WOLD uses for donor languages that have no ISO
  code. Every REST parameter or body field and every GraphQL argument that
  takes a language accepts these, ignores case and surrounding spaces, and
  maps 36 ISO 639-1 codes to ISO 639-3 (`en` → `eng`, `fr` → `fra`,
  `de` → `deu`, `ar` → `ara`, …). The ISO 639-1 table is the one ingestion
  uses (`src/utils/languages.py`). Anything else, such as `english`, `en-gb`
  or `12`, is refused (see [Errors](#errors)). REST `language` and
  `language_code` fields take at most 20 characters, more than any valid
  code needs.
- **Request ids.** Every REST response carries an `X-Request-ID` header. It echoes
  the one you send, or is generated. The server logs use the same id.
- **Caching.** When Redis is connected, `GET /lsr/search` responses are cached
  for 3 minutes and `GET /lsr/{id}` responses for 10 minutes. API writes clear
  the affected entries. Ingestion and `lexicon reindex` clear the cache when
  they can reach Redis; otherwise the entries expire. A form search that
  Neo4j answered in place of Elasticsearch is not cached, because it lacks
  the near misses. That happens while a configured Elasticsearch is not
  connected (for example just after startup, before Elasticsearch is up) and
  when an Elasticsearch request fails. The API also clears cached searches
  after its own background reindex (see [GET /lsr/search](#get-lsrsearch)).
  Without Redis, nothing is cached.

## Authentication

Authentication is off unless `API_KEY` is set, either in the environment or in
the `.env` file (or the file named by `ENV_FILE`). The server logs a warning at
startup while it is off. When it is on, send the key in the `X-API-Key` header.
`API_KEY_HEADER` renames the header.

```bash
curl -s -H "X-API-Key: $LEXICON_API_KEY" "$LEXICON/api/v1/lsr/search?form=castle&limit=1"
```

| Needs the key (when `API_KEY` is set) | No key needed |
|---|---|
| everything under `/api/v1`, `/graphql`, `/metrics`, `/metrics/json`, `/traces` | `/`, `/health`, the documentation pages (`/docs`, `/docs/oauth2-redirect`, `/redoc`, `/openapi.json`), CORS preflight (`OPTIONS`) requests |

A Prometheus scraper therefore has to send the key, and its scrapes count
against the rate limit.

There is one shared key and no roles: a valid key grants every operation,
including `POST` and `DELETE` on `/api/v1/lsr/`. A missing or wrong key gets
`401`:

```http
HTTP/1.1 401 Unauthorized
www-authenticate: ApiKey header="X-API-Key"
x-ratelimit-limit: 3
x-ratelimit-remaining: 2
x-ratelimit-reset: 59

{"error":"AUTHENTICATION_ERROR","message":"API key required","details":{"header":"X-API-Key"}}
```

A wrong key gets `"message":"Invalid API key"` and empty `details`. (These
responses were captured with `RATE_LIMIT_REQUESTS=3`.)

## Rate limiting

Each client IP address may make `RATE_LIMIT_REQUESTS` requests (default 100)
per fixed window of `RATE_LIMIT_WINDOW_SECONDS` (default 60). Windows start at
multiples of the window length, not at a client's first request.
`RATE_LIMIT_ENABLED=false` turns the limit off; with `ENVIRONMENT=production`
the API refuses to start that way.

- **What counts.** Every request except `OPTIONS` requests and requests for
  `/health` and the documentation pages (`/docs`, `/docs/oauth2-redirect`,
  `/redoc`, `/openapi.json`). `/`, `/metrics`, `/metrics/json` and `/traces`
  count. Every path that can require the API key counts, and the limiter
  runs before authentication, so requests rejected for a bad key count too:
  no path lets a client guess keys without limit. A GraphQL request counts
  as one request, however many fields it has.
- **Counters.** Counters are kept in Redis while the API is connected to it, so
  every worker shares them. Without Redis, each worker process counts on its
  own, so with N workers a client gets up to N times the limit.
- **Behind a proxy.** The client address is the one uvicorn reports. uvicorn
  takes it from `X-Forwarded-For` only when the connection comes from an
  address in `FORWARDED_ALLOW_IPS` (comma-separated addresses or networks;
  default `127.0.0.1`, which docker compose passes to the api container).
  Otherwise every client behind the proxy shares the proxy's budget. This
  also applies to a proxy on the same host as the compose stack: its
  connections reach the container from the `ls-network` gateway, not from
  `127.0.0.1`. Set `FORWARDED_ALLOW_IPS` to that gateway
  (`docker network inspect <project>_ls-network -f '{{(index .IPAM.Config 0).Gateway}}'`)
  or to `172.16.0.0/12`, Docker's default bridge range; see
  `docker-compose.production.yml`. The proxy must set or append
  `X-Forwarded-For`. uvicorn takes the rightmost address in it that is not
  trusted.

Every counted response carries these headers:

| Header | Meaning |
|---|---|
| `X-RateLimit-Limit` | Requests allowed per window |
| `X-RateLimit-Remaining` | Requests left in the current window |
| `X-RateLimit-Reset` | Seconds until the window resets |

Over the limit, the API answers `429` with a `Retry-After` header:

```http
HTTP/1.1 429 Too Many Requests
x-ratelimit-limit: 3
x-ratelimit-remaining: 0
x-ratelimit-reset: 59
retry-after: 59

{"error":"RATE_LIMIT_EXCEEDED","message":"Rate limit exceeded: 3 requests per 60 seconds","details":{"retry_after_seconds":59}}
```

## Errors

Every REST error has the same body:

```json
{"error": "LSR_NOT_FOUND", "message": "LSR not found: 00000000-0000-0000-0000-000000000000", "details": {"resource_type": "LSR", "resource_id": "00000000-0000-0000-0000-000000000000"}}
```

`error` is a stable code. `message` is for people. `details` varies by code.
Request validation failures list every bad field:

```json
{"error":"VALIDATION_ERROR","message":"Request validation failed","details":{"errors":[{"field":"body.text","message":"String should have at least 10 characters","type":"string_too_short"}]}}
```

Database error messages never include driver text, hostnames or query
internals. **Too little data is not an error.** The analyses answer `200` with
`status` or `verdict` set to `insufficient_data` (see
[Reading analysis results](#reading-analysis-results)).

Error codes the API returns:

| Code | HTTP | When |
|---|---|---|
| `VALIDATION_ERROR` | 400 | Bad body, query or path parameter (including a non-UUID id, a bad language code in a request body, a language code over 20 characters, and a year outside −10000 to 2100); a `/graph/query` query rejected by the read-only rules, with a syntax error, with a missing `$parameter`, or otherwise refused by Neo4j |
| `INVALID_LANGUAGE_CODE` | 400 | A `language` or `languages` query parameter that is not a usable code (`/lsr/search` and the `GET /analyze/*` routes) |
| `INVALID_DATE_RANGE` | 400 | `date_end` before `date_start` (`/lsr/search`, `/analyze/contact-events`) |
| `QUERY_TIMEOUT` | 400 | A `/graph/query` query ran past its `timeout_seconds` |
| `QUERY_TOO_LARGE` | 400 | A `/graph/query` query needed more memory than Neo4j allows |
| `AUTHENTICATION_ERROR` | 401 | Missing or wrong API key |
| `QUERY_DISABLED` | 403 | `/graph/query` on a server with `GRAPH_QUERY_ENABLED=false` |
| `NOT_FOUND` | 404 | Unknown path; unknown, expired or dropped export job |
| `LSR_NOT_FOUND` | 404 | No LSR with that id |
| `METHOD_NOT_ALLOWED` | 405 | Wrong HTTP method for the path (there are no `PUT`/`PATCH` routes) |
| `DUPLICATE_ERROR` | 409 | `POST /lsr/` for a record that already exists |
| `RATE_LIMIT_EXCEEDED` | 429 | Over the rate limit |
| `HTTP_ERROR` | other 4xx | Any other client error raised by the framework |
| `INTERNAL_ERROR` | 500 | Unexpected failure. `details.type` names the exception only when `DEBUG=true` |
| `DATABASE_ERROR` | 503 | Neo4j unreachable, failing or not answering in time; a failed export job; the Redis job store unreachable while polling an export job |

A bad language code gets `INVALID_LANGUAGE_CODE` in a query parameter and
`VALIDATION_ERROR` in a request body:

```json
{"error":"INVALID_LANGUAGE_CODE","message":"Invalid language code format: english","details":{"field":"language_code","value":"english"}}
{"error":"VALIDATION_ERROR","message":"Request validation failed","details":{"errors":[{"field":"body.language","message":"Value error, Invalid language code 'english': use an ISO 639-3 code such as 'eng' (or 'gem-pro' for proto-languages, or a Glottolog code such as 'yaku1245')","type":"value_error"}]}}
```

`src/exceptions.py` defines more codes, which no API route returns today:
`AUTHORIZATION_ERROR` 403 (its class is raised only with the code
`QUERY_DISABLED`), `INSUFFICIENT_DATA` 422, `AMBIGUOUS_RESULT` 422,
`LANGUAGE_NOT_FOUND` 404, `CONNECTION_ERROR`, `QUERY_ERROR`,
`TRANSACTION_ERROR` 503, `PIPELINE_ERROR` and its subclasses 500,
`EXTERNAL_SERVICE_ERROR` and its subclasses 502, and
`CONFIGURATION_ERROR` 500.

**Outages.** When Neo4j is unreachable, whether it was down when the API
started or went away later, every route under `/api/v1` that reads the
graph answers `503 DATABASE_ERROR`, usually with `"message":"Graph database is not
available"`, and `/health` answers `503`. GraphQL reports `DATABASE_ERROR`
in `errors` (see [GraphQL errors](#errors-1)). No analysis returns a verdict
while the graph is unreachable. Requests use Neo4j again once it is back;
the API needs no restart. (After an authentication failure the API does not
retry; restart it. See
[troubleshooting](troubleshooting.md#503-database_error-from-the-api).)

A Neo4j that keeps the connection open but stops answering (paused, or cut
off by the network) is handled by time limits:

- The search, record, traversal and analysis routes (`/lsr/…`, `POST` and
  `DELETE` included, `/analyze/…`), the fixed `/graph` routes and the
  GraphQL fields run each query with a server-side timeout (15 s; 60 s for
  bulk export) and stop waiting 2 s later. They answer
  `503 DATABASE_ERROR` after about 17 s (about a minute for bulk export),
  saying what timed out (`"LSR search timed out"`,
  `"Vocabulary lookup timed out"`, `"Path finding timed out"`; GraphQL
  reports the same message with code `DATABASE_ERROR`). A write that timed
  out is normally rolled back, but check with a `GET` before retrying it.
- `POST /graph/query` gives up after about 2 s with
  `503 DATABASE_ERROR "Graph database is not available"` when Neo4j has not
  begun the transaction.

## Health and monitoring

`GET /health` checks each configured store on every call (with a 2-second
timeout each), after first trying to reconnect a Neo4j that was down.

| `status` | HTTP | Meaning |
|---|---|---|
| `healthy` | 200 | Neo4j and every configured optional store answered |
| `degraded` | 200 | Neo4j answered; a configured Elasticsearch, Redis or PostgreSQL did not. Every endpoint works. Without Elasticsearch, search is not fuzzy; without Redis, nothing is cached and rate-limit counters and export jobs are per process |
| `unhealthy` | 503 | Neo4j did not answer. Nothing but `/health` and the docs is useful |

Each entry in `databases` is `connected`, `disconnected` or `not_configured`.
An optional store is configured when one of these settings is set, and the
API connects only to configured stores:

| Store | Settings |
|---|---|
| Elasticsearch | `ELASTICSEARCH_URI` or `ELASTICSEARCH_PASSWORD` |
| Redis | `REDIS_URI` or `REDIS_PASSWORD` |
| PostgreSQL | `POSTGRES_URI` (no code uses it yet) |

A store that is `not_configured` does not affect `status`. A configured
Elasticsearch or Redis that is not up when the API starts is retried for
three minutes; one that comes up after that needs an API restart.

The docker compose stack (all passwords set in `.env`):

```json
{"status":"healthy","api":"up","databases":{"neo4j":"connected","postgres":"not_configured","elasticsearch":"connected","redis":"connected"}}
```

A deployment that runs only Neo4j (no Elasticsearch or Redis settings) is
also `healthy`:

```json
{"status":"healthy","api":"up","databases":{"neo4j":"connected","postgres":"not_configured","elasticsearch":"not_configured","redis":"not_configured"}}
```

The same deployment with Neo4j unreachable (HTTP 503):

```json
{"status":"unhealthy","api":"up","databases":{"neo4j":"disconnected","postgres":"not_configured","elasticsearch":"not_configured","redis":"not_configured"}}
```

The other monitoring routes hold per-process data only. With several workers,
each worker reports its own.

| Route | Returns |
|---|---|
| `GET /` | `{"name":"Lexicon API","version":"0.1.0","description":"Date a text by its words: dating, anachronisms, language contact","docs":"/docs","health":"/health"}` |
| `GET /metrics` | Prometheus text: `api_requests_total{endpoint,method,status}`, `api_request_duration_seconds{endpoint,method}`, `api_active_requests`, `process_start_time_seconds`. `endpoint` is the route template, e.g. `/api/v1/lsr/{lsr_id}`; requests rejected before routing (`401`, `429`, unknown paths) are counted as `<unmatched>` |
| `GET /metrics/json` | The same metrics as JSON |
| `GET /traces?limit=100` | The last `limit` (1–1000) request spans, oldest first |

## Reading analysis results

An analysis answer is only as good as the graph's coverage of the text's
language. Text dating and anachronism detection report how many of the
text's words the graph could date. When the evidence is too thin, date-text,
detect-anachronisms and semantic-drift say `insufficient_data` instead of
giving a verdict, and contact-events returns an empty list.

**Coverage fields** (date-text and detect-anachronisms):

| Field | Meaning |
|---|---|
| `content_words` | **Distinct** tokens left after dropping English stop words and tokens of 1–2 letters |
| `dated_words` | Distinct content words found in the graph with a first-attestation year |
| `unknown_words` | Distinct content words not found in the graph (up to 50). Found-but-undated words are counted in neither this list nor `dated_words` |

Coverage is `dated_words / content_words`. Both count a repeated word once:
"king king king king king king zorblax" has 2 content words, 1 of them
dated, so coverage is 0.5.

Tokens are matched to `form_normalized` (lowercase, diacritics stripped), in
any script. A token starts with a letter and runs through letters and
combining marks, so vowel signs, viramas, harakat and decomposed accents do
not split a word. An apostrophe or hyphen between two letters joins them
(*knight's*, *self-evident*). For English, a token not found as written
also tries base forms, likeliest first, and the first one in the graph
dates it: *listened* → *listen*, *rode* → *ride*, *knights* → *knight*.
The base form comes before a shorter word, so *fades* is *fade*, not *fad*,
and *passes* is *pass*. A word's dates are
aggregated over all its LSRs in the language: the earliest `date_start`, and
the latest `date_end`, but only if every sense has an end date.

**Date labels.** Each dated word in `diagnostic_vocabulary` and each
`coined_after` anachronism has a `date_label`: the first attestation as its
source states it, taken from the `period_label` of the word's earliest-dated
LSR. WOLD gives labels such as `"c. 1275"`, `"before 1075"`, `"1913"` or
`"Old English (inherited from Proto-Germanic)"`; corpus data gives
`"earliest in corpus: <document title>"`. The label is `""` when the record
has none. Read it with the year. The WOLD ingestion dates Old English words,
including inherited ones, to 700 (900 for WOLD's "Late Old English"), so
*king* at 700 means "Old English", not a text of the year 700.

**Text dating** (`status`):

| `status` | Meaning |
|---|---|
| `ok` | `predicted_date_range` is `[earliest, latest]`. `earliest` is the terminus post quem: the first attestation of the text's newest word. `latest` is the earliest `date_end` among words that fell out of use, or the current year |
| `conflicting_evidence` | A word fell out of use before another was first attested (possible archaism). The range is `[newest word's first attestation, current year]` and confidence is halved |
| `insufficient_data` | No content word has a date. `predicted_date_range` is null and confidence is 0 |

`ok` needs only one dated word. Low coverage shows in `confidence`, not in
`status`, so read both. The upper bound is weak: ingested words carry no end
date unless a source gives one, so `latest` is usually the current year.

**Anachronism detection** (`verdict`). A word first attested after the claimed
date is an anachronism of type `coined_after`, with severity `high` (gap over
100 years), `medium` (51–100) or `low` (50 or less). Only high and medium
anachronisms count as significant.

| `verdict` | When |
|---|---|
| `anachronistic` | 3 or more significant anachronisms, or one with a gap over 200 years |
| `suspicious` | 1–2 significant anachronisms, all with gaps of 200 years or less |
| `consistent` | No significant anachronism, and coverage is at least 0.5 |
| `insufficient_data` | No significant anachronism, and coverage is below 0.5 |

Low-severity `coined_after` words and `obsolete_before` words (last attested
before the claimed date, a possible archaism) are listed in `anachronisms` but
do not change the verdict. A text can therefore be `consistent` while
`anachronisms` lists a word first attested up to 50 years after the claimed
date. The explanation then names those words. With a claimed date of 1900,
"The king listened to the radio in his castle" is `consistent`, with
confidence 0.8, and lists *radio* (1913, gap 13, severity `low`):

```json
"explanation": "No word among the 4 dated content words (of 4) is first attested more than 50 years after 1900; 1 postdates it by 50 years or less: radio (1913)."
```

**Confidence** is a number in 0–1. How it is computed:

- **date-text and `consistent`:** coverage × min(1, dated words / 5). So a text
  with fewer than five dated words never reaches 1.
- **`suspicious` / `anachronistic`:** min(0.95, 0.5 + 0.15 × significant
  anachronisms, + 0.1 if a gap exceeds 200 years).
- **`insufficient_data`:** 0.

These are heuristics, not calibrated probabilities. No accuracy evaluation
exists.

**What the dates mean.** A date is the first attestation recorded by the
source: WOLD's age column, a dated corpus document, or a year in a Wiktionary
etymology. It is not a coinage date. A word missing from the graph is not
evidence either way. A corpus date only says which loaded document a word
first appears in. The sample corpus in `data/corpus` shows the input format
and is not dating evidence: loaded into a graph, it makes *watched* look
first attested in 1898 (see [data/corpus/README.md](../data/corpus/README.md)).

## REST endpoints

All routes below are under `/api/v1`.

| Method | Path | Purpose |
|---|---|---|
| POST | `/analyze/date-text` | Date a text by its words |
| POST | `/analyze/detect-anachronisms` | Words that postdate a claimed date |
| GET | `/analyze/contact-events` | Borrowing clusters by donor language and century |
| GET | `/analyze/semantic-drift` | Sense trajectory of one word (experimental) |
| GET | `/analyze/compare-concept` | One spelling's trajectory in several languages (experimental) |
| GET | `/lsr/search` | Search records |
| GET | `/lsr/{id}` | One record |
| POST | `/lsr/` | Create a record |
| DELETE | `/lsr/{id}` | Delete a record and its relationships |
| GET | `/lsr/{id}/etymology` | Ancestor chain to the proto-form |
| GET | `/lsr/{id}/descendants` | Descendants |
| GET | `/lsr/{id}/cognates` | Cognates |
| GET | `/lsr/{id}/borrowings` | Donors and borrowers |
| POST | `/graph/query` | Read-only Cypher |
| GET | `/graph/path` | Shortest paths between two records |
| GET | `/graph/etymology/{id}` | Ancestor chain, with full nodes and edges |
| GET | `/graph/cognates/{id}` | Cognates, with full nodes |
| POST | `/graph/bulk/export` | Export one language's records, a page at a time |
| GET | `/graph/bulk/status/{job_id}` | Status of an async export |
| GET | `/graph/bulk/result/{job_id}` | Result of an async export |

The analysis routes read Neo4j with a 15-second server-side timeout per
query and stop waiting 2 seconds later, so a Neo4j that stops answering gives
503 `DATABASE_ERROR` after about 17 seconds (see [Outages](#errors)).

### Analysis

#### POST /analyze/date-text

| Field | Type | Rules |
|---|---|---|
| `text` | string | 10–100,000 characters, and at least 10 once runs of whitespace are collapsed |
| `language` | string | Language of the text (see [Conventions](#conventions)) |

```bash
curl -s -X POST "$LEXICON/api/v1/analyze/date-text" -H 'Content-Type: application/json' \
  -d '{"text": "The king listened to the radio in his castle", "language": "eng"}'
```

```json
{
  "predicted_date_range": [1913, 2026],
  "confidence": 0.8,
  "status": "ok",
  "explanation": "Written no earlier than 1913 (first attestation of radio); 4 of 4 content words dated.",
  "diagnostic_vocabulary": [
    {"word": "radio", "form": "radio", "date_start": 1913, "date_label": "1913", "date_end": null, "sets_bound": "lower"},
    {"word": "castle", "form": "castle", "date_start": 1075, "date_label": "before 1075", "date_end": null, "sets_bound": null},
    {"word": "king", "form": "king", "date_start": 700, "date_label": "Old English (inherited from Proto-Germanic)", "date_end": null, "sets_bound": null},
    {"word": "listened", "form": "listen", "date_start": 700, "date_label": "Old English (inherited from Proto-Germanic)", "date_end": null, "sets_bound": null}
  ],
  "analysis": {
    "language": "eng",
    "text_length": 44,
    "word_count": 9,
    "tokens_analyzed": 9,
    "content_words": 4,
    "dated_words": 4,
    "unknown_words": [],
    "method": "vocabulary_attestation"
  }
}
```

`diagnostic_vocabulary` lists up to 20 dated words, newest first. `word` is
the token in the text, lowercased with diacritics stripped (like
`form_normalized`), and `form` is the graph form it matched. `date_label`
is the first attestation as the source states it (see
[Reading analysis results](#reading-analysis-results)). `sets_bound` says
which end of the range a word fixes (`lower`, `upper`, or null). It is
never `upper` when the status is `conflicting_evidence`, because that range
runs to the current year.

When no content word has a date, for example a French text against a graph
with no dated French records, the response is `insufficient_data`:

```bash
curl -s -X POST "$LEXICON/api/v1/analyze/date-text" -H 'Content-Type: application/json' \
  -d '{"text": "Le roi envoya un télégraphe au château", "language": "fr"}'
```

```json
{
  "predicted_date_range": null,
  "confidence": 0.0,
  "status": "insufficient_data",
  "explanation": "None of the 4 content words are in the lexical graph for 'fra'. Ingest data for this language first.",
  "diagnostic_vocabulary": [],
  "analysis": {
    "language": "fra",
    "text_length": 38,
    "word_count": 7,
    "tokens_analyzed": 7,
    "content_words": 4,
    "dated_words": 0,
    "unknown_words": ["roi", "envoya", "telegraphe", "chateau"],
    "method": "vocabulary_attestation"
  }
}
```

#### POST /analyze/detect-anachronisms

| Field | Type | Rules |
|---|---|---|
| `text` | string | 10–100,000 characters, and at least 10 once runs of whitespace are collapsed |
| `claimed_date` | integer | −10000 to 2100 |
| `language` | string | Language of the text (see [Conventions](#conventions)) |

```bash
curl -s -X POST "$LEXICON/api/v1/analyze/detect-anachronisms" -H 'Content-Type: application/json' \
  -d '{"text": "The knight listened to the radio in his castle", "claimed_date": 1300, "language": "eng"}'
```

```json
{
  "anachronisms": [
    {
      "word": "radio",
      "form": "radio",
      "type": "coined_after",
      "earliest_attestation": 1913,
      "date_label": "1913",
      "claimed_date": 1300,
      "gap_years": 613,
      "severity": "high"
    }
  ],
  "verdict": "anachronistic",
  "confidence": 0.75,
  "explanation": "1 word(s) first attested well after 1300: radio (1913).",
  "analysis": {
    "language": "eng",
    "claimed_date": 1300,
    "words_analyzed": 9,
    "content_words": 4,
    "dated_words": 3,
    "unknown_words": ["knight"]
  }
}
```

`anachronisms` holds up to 20 entries, largest gap first. `obsolete_before`
entries carry `last_attestation` instead of `earliest_attestation` and
`date_label`.

When most of the words are unknown, the API does not call the text consistent:

```bash
curl -s -X POST "$LEXICON/api/v1/analyze/detect-anachronisms" -H 'Content-Type: application/json' \
  -d '{"text": "The knight bore a halberd and a gonfalon", "claimed_date": 1300, "language": "eng"}'
```

```json
{
  "anachronisms": [],
  "verdict": "insufficient_data",
  "confidence": 0.0,
  "explanation": "Only 1 of 4 content words have attestation dates; too few to judge the text consistent with its claimed date.",
  "analysis": {
    "language": "eng",
    "claimed_date": 1300,
    "words_analyzed": 8,
    "content_words": 4,
    "dated_words": 1,
    "unknown_words": ["knight", "halberd", "gonfalon"]
  }
}
```

#### GET /analyze/contact-events

| Parameter | Type | Rules |
|---|---|---|
| `language` | string, required | Returns events where this language is the recipient or the donor. Any code from [Conventions](#conventions), including Glottocodes such as `celt1248` |
| `date_start`, `date_end` | integer, optional | −10000 to 2100. Only borrowings dated inside the range are counted |

A contact event is a cluster of `BORROWED_FROM` edges between one pair of
languages within one century. A borrowing is dated by the borrowing word's
first attestation, and undated borrowings are ignored. The cluster's words
are counted once each: WOLD gives every sense of a word its own record and
edge, and a word's senses count as one word. A cluster becomes an event only
if it has **at least 5 distinct borrowed words** and a confidence of at
least 0.3, so sparse borrowing data returns `[]`. Borrowings from a donor
with the code `und` (donor languages a source names without any code,
such as WOLD's *Saharan*) never form an event. Events are sorted by confidence.

```bash
curl -s "$LEXICON/api/v1/analyze/contact-events?language=eng&date_start=1000&date_end=1099"
```

```json
[
  {
    "donor_language": "lat",
    "donor_language_name": "Latin",
    "recipient_language": "eng",
    "date_range": [1000, 1100],
    "vocabulary_count": 12,
    "confidence": 0.567,
    "sample_words": ["earlobe", "cup", "pepper", "butter", "silk", "cap", "fan", "turn", "circle", "school"],
    "contact_type": "cultural",
    "semantic_domains": ["The body", "Food and drink", "Clothing and grooming", "Basic actions and technology", "Motion", "Spatial relations", "Cognition", "Religion and belief", "The house"],
    "intensity": 0.24
  },
  {
    "donor_language": "non",
    "donor_language_name": "Old Norse",
    "recipient_language": "eng",
    "date_range": [1000, 1100],
    "vocabulary_count": 6,
    "confidence": 0.487,
    "sample_words": ["husband", "egg", "give", "Thursday", "call", "law"],
    "contact_type": "cultural",
    "semantic_domains": ["Kinship", "Food and drink", "Possession", "Time", "Speech and language", "Law"],
    "intensity": 0.12
  }
]
```

| Field | Meaning |
|---|---|
| `date_range` | The century bucket `[start, start + 100]` |
| `vocabulary_count` | Distinct borrowed words in the cluster |
| `confidence` | 0.4 × size score + 0.3 × domain score + 0.3 × date score. The size score is words / 20, capped at 1. The domain score is how concentrated the words' semantic domains are, times the share of words that have a domain; it is 0 when no word has one. The date score is 1 − (standard deviation of the words' first attestations / 100), at least 0 |
| `intensity` | vocabulary_count / 50, capped at 1 |
| `semantic_domains` | The borrowed words' semantic fields (WOLD) or, failing that, domains guessed from whole words of the definitions. A word counts once per domain |
| `contact_type` | `trade`, `conquest`, `religious`, `cultural`, `technological` or null: whole words of the domain labels matched against keyword lists, so *Kinship* does not count as *ship* (trade). Treat it as a hint only |
| `sample_words` | Up to 10 distinct borrowed words, as the source spells them |

Without a date range, the WOLD English data gives 29 events, from Latin
(700–800) to French (1800–1900).

#### GET /analyze/semantic-drift

Experimental. Parameters: `form` (required, up to 200 characters) and
`language` (required).

The LSRs of the form in the language that have a first-attestation year and
a stored `semantic_vector` are treated as senses of one word, ordered by
`date_start`. Other LSRs of the form are ignored. Ingestion computes the
vector from a record's definition, so records without a definition (such as
corpus records) and records created with `POST /lsr/` have none. Distances
compare these vectors, a hashed character n-gram encoding of the definition
([architecture.md](architecture.md#analyses-and-the-data-they-need)). They
measure how differently the senses are described, not a trained model of
meaning.

Drift is change over time, so senses first attested in the same year are
not compared with each other. Each sense is compared with the closest sense
of the latest earlier date. A shift event is recorded when that distance is
more than 0.2. The status is `ok` only when the senses have at least two
different dates.

Homographs and different parts of speech count as senses. WOLD's *calf* (the
animal) and *calf* (of the leg) form one trajectory:

```bash
curl -s "$LEXICON/api/v1/analyze/semantic-drift?form=calf&language=eng"
```

```json
{
  "form": "calf",
  "language": "eng",
  "status": "ok",
  "explanation": "2 dated senses compared across 2 dates.",
  "trajectory": [
    {"date": 700, "embedding_2d": [-0.3148, -0.0], "definition": "the calf", "attestation_count": 0, "confidence": 1.0},
    {"date": 1325, "embedding_2d": [0.3148, 0.0], "definition": "the calf of the leg", "attestation_count": 0, "confidence": 1.0}
  ],
  "shift_events": [],
  "total_drift": 0.1982,
  "stability_score": 1.0
}
```

`total_drift` is the sum of those distances. `stability_score` is 1 minus
the summed shift magnitudes divided by the number of senses (floor 0). Each
shift event has `date`, `change_type`, `confidence`, `magnitude`,
`before_meaning`, `after_meaning` and `evidence`.

When the usable senses have fewer than two different dates, the response is
`insufficient_data`, `trajectory` is empty and the numbers are null. The
explanation counts only the usable senses. WOLD's two senses of *male* are
both dated 1382:

```bash
curl -s "$LEXICON/api/v1/analyze/semantic-drift?form=male&language=eng"
```

```json
{
  "form": "male",
  "language": "eng",
  "status": "insufficient_data",
  "explanation": "Found 2 dated senses with a definition for 'male' in 'eng', all first attested in 1382; senses of the same date are not a change over time, and drift needs senses first attested in at least two different years.",
  "trajectory": [],
  "shift_events": [],
  "total_drift": null,
  "stability_score": null
}
```

A word with one usable sense, such as *radio*, gets "Found 1 dated sense
with a definition for 'radio' in 'eng' (first attested in 1913); drift needs
senses first attested in at least two different years." A word with none
gets "Found no dated sense with a definition for 'zorblax' in 'eng'; drift
needs senses first attested in at least two different years."

#### GET /analyze/compare-concept

Experimental. Parameters: `concept` (required, up to 100 characters) and
`languages` (required, comma-separated, at most 10).

Despite the name, there is no translation step. The same spelling is looked
up in each language, so this only finds shared loanwords and identically
spelled cognates. Each language gets the same treatment as semantic-drift:
`status` is `ok` when its usable senses have at least two different dates,
and `insufficient_data` otherwise, with `trajectory` null. `explanation`
says why, in the same words as semantic-drift. `forms` lists the usable
senses' forms.

```bash
curl -s "$LEXICON/api/v1/analyze/compare-concept?concept=calf&languages=eng,fra"
```

```json
{
  "concept": "calf",
  "by_language": [
    {
      "language": "eng",
      "forms": ["calf", "calf"],
      "status": "ok",
      "explanation": "2 dated senses compared across 2 dates.",
      "trajectory": {
        "points": [{"date": 700, "definition": "the calf"}, {"date": 1325, "definition": "the calf of the leg"}],
        "total_drift": 0.1982,
        "stability_score": 1.0
      }
    },
    {
      "language": "fra",
      "forms": [],
      "status": "insufficient_data",
      "explanation": "Found no dated sense with a definition for 'calf' in 'fra'; drift needs senses first attested in at least two different years.",
      "trajectory": null
    }
  ]
}
```

Senses of a single date give `insufficient_data` too:
`?concept=male&languages=eng` returns `"forms": ["male", "male"]`,
`"status": "insufficient_data"` and the explanation shown for *male* under
semantic-drift.

### LSR

The traversal routes (`/etymology`, `/descendants`, `/cognates`,
`/borrowings`) return compact summaries of the linked records:
`id, form, language_code, language_name, date_start, date_end, definition`.
The `/graph` routes return full nodes instead.

#### GET /lsr/search

| Parameter | Type | Rules |
|---|---|---|
| `form` | string | Up to 200 characters. Substring of the written form, case- and diacritic-insensitive. With Elasticsearch connected, near misses (typos) also match and results are ranked by relevance |
| `language` | string | A language code (see [Conventions](#conventions)). A value over 20 characters is `400 VALIDATION_ERROR`; any other invalid value is `400 INVALID_LANGUAGE_CODE` |
| `date_start`, `date_end` | integer | −10000 to 2100. Matches records in use at any point in the range (first attested by `date_end`, and not last attested before `date_start`). Undated records never match a date filter |
| `semantic_field` | string | Exact match on a source semantic field, e.g. `Warfare and hunting` (WOLD) |
| `limit` | integer | 1–100, default 20 |
| `offset` | integer | 0–10,000,000, default 0 |

```bash
curl -s "$LEXICON/api/v1/lsr/search?form=castle&language=en"
```

```json
{
  "results": [
    {
      "id": "43c29b18-1722-59c5-9220-9b35adf06a85",
      "version": 1,
      "created_at": "2026-09-27T15:35:58.886000Z",
      "updated_at": "2026-09-27T15:35:58.886000Z",
      "form_orthographic": "castle",
      "form_phonetic": "",
      "form_normalized": "castle",
      "language_code": "eng",
      "language_name": "English",
      "language_family": "Indo-European",
      "language_branch": [],
      "period_label": "before 1075",
      "date_start": 1075,
      "date_end": null,
      "date_confidence": 0.7,
      "date_source": "ATTESTED",
      "semantic_vector": [0.057571975164184455, 0.007290769082169919, …],
      "semantic_fields": ["Warfare and hunting"],
      "definition_primary": "the fortress",
      "definitions_alternate": [],
      "conceptual_domain": [],
      "etymology_text": "Borrowed from French (Anglo-Norman) castel; earlier Latin castellum (WOLD: clearly borrowed)",
      "register": null,
      "frequency_score": 0.0,
      "frequency_source": "",
      "part_of_speech": [],
      "attestations": [],
      "ancestor_ids": [],
      "descendant_ids": [],
      "cognate_ids": [],
      "loan_source_id": "fb42da85-71e9-5492-8a38-ecdf2e36d23e",
      "loan_target_ids": [],
      "reconstruction_flag": false,
      "confidence_overall": 1.0,
      "source_databases": ["wold"],
      "human_validated": false,
      "validation_notes": "",
      "relationship_counts": {"ancestors": 0, "descendants": 0, "cognates": 0, "loan_sources": 1, "loan_targets": 0},
      "relationship_ids_truncated": false
    },
    {"id": "a634b827-b801-537a-bb13-874da922f53a", "form_orthographic": "cattle", "period_label": "c. 1275", "date_start": 1275, …},
    {"id": "2f17f28b-b974-5363-a613-9031725bd86a", "form_orthographic": "candle", "period_label": "before 700", "date_start": 700, …},
    {"id": "744b5c8c-7ec4-52d0-8229-873293dcd14d", "form_orthographic": "cast", "period_label": "c. 1300", "date_start": 1300, …}
  ],
  "total": 4,
  "limit": 20,
  "offset": 0,
  "filters": {"form": "castle", "language": "eng", "date_start": null, "date_end": null, "semantic_field": null}
}
```

Results are full records in a stable order. Ingested records include the
384-number `semantic_vector`, so pages are large. `filters` echoes the filters
as applied (`en` became `eng`). Page with `offset` until `offset + limit >=
total`.

Elasticsearch was connected for this capture, so the near misses *cattle*,
*candle* and *cast* matched too, ranked below *castle*. Without
Elasticsearch, form searches go to Neo4j, and the same request returns only
*castle* (`"total": 1`). Searches without `form` always go to Neo4j.

Elasticsearch holds a copy of the records for fuzzy search. When the API
starts, or connects to Elasticsearch late, and the index holds fewer
documents than Neo4j has LSRs (for example after an ingestion run while
Elasticsearch was down), it rebuilds the index in the background and then
clears cached searches. `lexicon reindex` does the same rebuild by hand and
then clears the API's cache, when it can reach Redis. While a configured
Elasticsearch is not connected, or when an Elasticsearch request fails, form
searches go to Neo4j, and those results are not cached. When Neo4j fails
while the API loads the records of the Elasticsearch hits, the search
answers `503 DATABASE_ERROR`; it does not fall back to a Neo4j search.

Each Neo4j query of a search has a 15-second server-side timeout, and the
API stops waiting 2 seconds later. A Neo4j that stops answering therefore
gives `503 DATABASE_ERROR "LSR search timed out"` after about 17 s
(`"Search result lookup timed out"` when Elasticsearch found the matches).
The same limits apply to `GET`, `POST` and `DELETE /lsr` and to the
traversal routes below.

#### GET /lsr/{id}

Returns `{"data": <record>}`. The record has the same fields as a search
result.

The relationship fields list directly linked records:

| Field | Links |
|---|---|
| `ancestor_ids` / `descendant_ids` | `DESCENDS_FROM` |
| `cognate_ids` | `COGNATE_OF` |
| `loan_source_id` | The most confident `BORROWED_FROM` donor |
| `loan_target_ids` | Records that borrowed from this one |

Each list holds at most 100 ids (the lowest). `relationship_counts` has the
full counts, and `relationship_ids_truncated` says whether any list was cut.
`attestations` is always empty because attestations are not stored (see
[data_model.md](data_model.md)).

```bash
curl -s "$LEXICON/api/v1/lsr/00000000-0000-0000-0000-000000000000"
```

```json
{"error":"LSR_NOT_FOUND","message":"LSR not found: 00000000-0000-0000-0000-000000000000","details":{"resource_type":"LSR","resource_id":"00000000-0000-0000-0000-000000000000"}}
```

#### POST /lsr/

| Field | Type | Rules |
|---|---|---|
| `form_orthographic` | string, required | 1–200 characters; must contain a letter |
| `language_code` | string, required | A language code (see [Conventions](#conventions)) |
| `form_phonetic` | string | Up to 200 characters |
| `definition_primary` | string | Up to 2000 characters |
| `date_start`, `date_end` | integer | −10000 to 2100, `date_end` ≥ `date_start`. Leave `date_end` out for a word still in use |

Unknown fields are rejected (`400`, `extra_forbidden`). A year after 2100 is
refused like any other bad field:

```json
{"error":"VALIDATION_ERROR","message":"Request validation failed","details":{"errors":[{"field":"body.date_start","message":"Input should be less than or equal to 2100","type":"less_than_equal"}]}}
```

The response is `201` with
`{"message": "LSR created successfully", "data": <record>}`. If Neo4j stops
answering, the request fails with `503 DATABASE_ERROR` after about 17 s:
`"Duplicate check timed out"` before the write, `"LSR creation timed out"`
during it. A write that timed out is normally rolled back, but check with a
search before posting it again.

The API stores only these fields. It computes no `semantic_vector` and fills
no `language_name`, and it has no route for relationships. A record with the
same normalized form, language and `date_start` as an existing one is
refused. Posting `{"form_orthographic": "castle", "language_code": "en",
"date_start": 1075}` gives:

```json
{"error":"DUPLICATE_ERROR","message":"LSR already exists: 43c29b18-1722-59c5-9220-9b35adf06a85","details":{"resource_type":"LSR","identifier":"43c29b18-1722-59c5-9220-9b35adf06a85"}}
```

There is no update route. To change a record, delete it and create it again
(the id changes), or re-ingest its source.

#### DELETE /lsr/{id}

Deletes the record and every relationship touching it. Deleting the
hand-added Dutch *water* record gave
`{"message":"LSR 57bd7a26-e594-4454-92d3-ff9365902a02 deleted successfully"}`;
deleting it again gave `404 LSR_NOT_FOUND`.

#### GET /lsr/{id}/etymology

Follows `DESCENDS_FROM` edges to the deepest ancestor, along a shortest path.

| Parameter | Rules |
|---|---|
| `max_depth` | 1–50, default 20 |

```bash
curl -s "$LEXICON/api/v1/lsr/03f24a96-212c-5f03-9ee9-024e6f7a7dde/etymology"
```

```json
{
  "lsr_id": "03f24a96-212c-5f03-9ee9-024e6f7a7dde",
  "chain": [
    {"id": "03f24a96-212c-5f03-9ee9-024e6f7a7dde", "form": "water", "language_code": "eng", "language_name": "English", "date_start": 700, "date_end": null, "definition": "the water"},
    {"id": "a59428b5-e98f-4289-bb93-b557d8e67ea3", "form": "water", "language_code": "enm", "language_name": "Middle English", "date_start": 1150, "date_end": 1500, "definition": "water"},
    {"id": "57c4ddc0-d7ab-482c-be98-eba12c2907ab", "form": "wæter", "language_code": "ang", "language_name": "Old English", "date_start": 700, "date_end": 1150, "definition": "water"},
    {"id": "d6818940-2e4b-465e-b8d7-c8e92c096df6", "form": "*watōr", "language_code": "gem-pro", "language_name": "Proto-Germanic", "date_start": null, "date_end": null, "definition": "water"}
  ],
  "proto_form": {"id": "d6818940-2e4b-465e-b8d7-c8e92c096df6", "form": "*watōr", "language_code": "gem-pro", "language_name": "Proto-Germanic", "date_start": null, "date_end": null, "definition": "water"},
  "depth": 3,
  "truncated": false
}
```

The chain starts with the record itself, so a record without ancestors is its
own proto-form at depth 0. If `max_depth` cuts off a line of ancestry,
`truncated` is true and `proto_form` is null; with `max_depth=2` this chain
stops at *wæter*.

(The English *water* record is dated 700 because WOLD dates the word from its
Old English attestation. That is earlier than the Middle English record in its
own chain.)

#### GET /lsr/{id}/descendants

| Parameter | Rules |
|---|---|
| `depth` | 1–10, default 3 |

Returns `{lsr_id, descendants, count, depth}`. It lists at most 500
descendants, ordered by `date_start`, language and form, without saying at
which depth each one sits.

```bash
curl -s "$LEXICON/api/v1/lsr/d6818940-2e4b-465e-b8d7-c8e92c096df6/descendants"
```

```json
{
  "lsr_id": "d6818940-2e4b-465e-b8d7-c8e92c096df6",
  "descendants": [
    {"id": "57c4ddc0-d7ab-482c-be98-eba12c2907ab", "form": "wæter", "language_code": "ang", "language_name": "Old English", "date_start": 700, "date_end": 1150, "definition": "water"},
    {"id": "03f24a96-212c-5f03-9ee9-024e6f7a7dde", "form": "water", "language_code": "eng", "language_name": "English", "date_start": 700, "date_end": null, "definition": "the water"},
    {"id": "5a9278d7-eb07-417c-9b27-03b1176e1085", "form": "wazzer", "language_code": "gmh", "language_name": "Middle High German", "date_start": 1050, "date_end": 1350, "definition": "water"},
    {"id": "a59428b5-e98f-4289-bb93-b557d8e67ea3", "form": "water", "language_code": "enm", "language_name": "Middle English", "date_start": 1150, "date_end": 1500, "definition": "water"},
    {"id": "886ad379-0907-486b-87cd-d0dd3376b937", "form": "Wasser", "language_code": "deu", "language_name": "German", "date_start": 1500, "date_end": null, "definition": "water"}
  ],
  "count": 5,
  "depth": 3
}
```

#### GET /lsr/{id}/cognates

Cognates are records in another language that share a `DESCENDS_FROM`
ancestor with this one, excluding its own ancestors and descendants, plus any
record linked to it by `COGNATE_OF`. At most 100 are returned, ordered by
language and form.

```bash
curl -s "$LEXICON/api/v1/lsr/03f24a96-212c-5f03-9ee9-024e6f7a7dde/cognates"
```

```json
{
  "lsr_id": "03f24a96-212c-5f03-9ee9-024e6f7a7dde",
  "cognates": [
    {"id": "886ad379-0907-486b-87cd-d0dd3376b937", "form": "Wasser", "language_code": "deu", "language_name": "German", "date_start": 1500, "date_end": null, "definition": "water"},
    {"id": "5a9278d7-eb07-417c-9b27-03b1176e1085", "form": "wazzer", "language_code": "gmh", "language_name": "Middle High German", "date_start": 1050, "date_end": 1350, "definition": "water"},
    {"id": "57bd7a26-e594-4454-92d3-ff9365902a02", "form": "water", "language_code": "nld", "language_name": "Dutch", "date_start": 1500, "date_end": null, "definition": "water"}
  ],
  "cognate_count": 3,
  "languages": ["deu", "gmh", "nld"],
  "by_language": {"deu": […], "gmh": […], "nld": […]}
}
```

*Wasser* and *wazzer* share the ancestor *\*watōr* with English *water*. The
Dutch *water* record has no ancestor here; it is linked by a `COGNATE_OF`
edge. `by_language` groups the same entries by language code.

#### GET /lsr/{id}/borrowings

```bash
curl -s "$LEXICON/api/v1/lsr/43c29b18-1722-59c5-9220-9b35adf06a85/borrowings"
```

```json
{
  "lsr_id": "43c29b18-1722-59c5-9220-9b35adf06a85",
  "borrowed_from": [
    {
      "id": "fb42da85-71e9-5492-8a38-ecdf2e36d23e",
      "form": "castel",
      "language_code": "xno",
      "language_name": "French (Anglo-Norman)",
      "date_start": null,
      "date_end": null,
      "definition": "castle",
      "confidence": 0.95,
      "evidence": "Borrowed from French (Anglo-Norman) castel; earlier Latin castellum (WOLD: clearly borrowed)"
    }
  ],
  "borrowed_to": []
}
```

`borrowed_from` lists the records this word was borrowed from (at most 10,
most confident first), and `borrowed_to` the records that borrowed from it
(at most 100, earliest first). `confidence` and `evidence`
come from the `BORROWED_FROM` edge. Donor records created from WOLD donor
data are placeholders with no dates.

### Graph

The `/graph` routes return full Neo4j nodes: every stored property plus
`labels`, including `semantic_vector`. A property that is null is not stored,
so it is absent from the node. Relationships are
`{type, source, target, properties}`, where `source` and `target` are LSR ids.
Each query of a fixed traversal has a 15-second server-side timeout, and the
API stops waiting 2 seconds later (`503 DATABASE_ERROR`, see
[Outages](#errors)). Lineage
traversals (`/graph/etymology` and `/graph/cognates`; `/lsr/{id}/etymology`,
`/descendants` and `/cognates`; and GraphQL's `etymology`, `ancestors`,
`descendants` and `cognates`) expand one generation per query, so their cost
grows linearly with the size of the lineage.

#### POST /graph/query

Runs a read-only Cypher query. The endpoint is meant for trusted clients.
A server started with `GRAPH_QUERY_ENABLED=false` refuses every query
(`config/.env.production` sets it; docker compose passes it to the api
container):

```http
HTTP/1.1 403 Forbidden

{"error":"QUERY_DISABLED","message":"Cypher queries are disabled on this server (GRAPH_QUERY_ENABLED=false)","details":{}}
```

| Field | Type | Rules |
|---|---|---|
| `query` | string | Up to 5000 characters |
| `parameters` | object | Values for `$name` placeholders |
| `timeout_seconds` | integer | 1–30, default 10. Server-side transaction timeout |

Rules, checked before the query reaches Neo4j:

- The first clause must be `MATCH`, `OPTIONAL MATCH`, `RETURN`, `WITH`,
  `UNWIND`, `EXPLAIN` or `PROFILE`.
- These words are rejected anywhere in the text, in any case, including
  inside string literals, comments, backticks and as identifiers:
  `LOAD`, `CALL`, `USE`, `PERIODIC`, `FOREACH`, `APOC`, `DBMS`, `SHOW`,
  `TERMINATE`, `GRANT`, `DENY`, `REVOKE`, `ALTER`. Underscores and digits
  split words, so a property or alias named `show` or `use_count` is rejected
  too. Pass literal values as `$parameters`.
- Write clauses outside string literals and comments are rejected: `CREATE`,
  `MERGE`, `SET`, `DELETE`, `DETACH`, `REMOVE`, `DROP`.
- Unicode escapes (a backslash followed by `u` or `U`, as in `\u0043`) are
  rejected anywhere, strings and comments included. Neo4j decodes them
  before it parses the query, so `\u0043ALL` would be `CALL`. A backslash
  outside string literals and comments is rejected too. Other escapes
  inside strings, such as `\\` and `\"`, are allowed.
- The query then runs in a read transaction, so Neo4j refuses any write the
  filter missed.

Results are capped at **1000 rows** and **about 5 MB** of JSON. `truncated`
and `truncated_reason` say when a cap was hit. The caps limit what is
returned, not what the API receives: a row is read whole before it is
measured. `RETURN range(1, 3000000)` is one row of well over 5 MB, so the
API reads it and then answers with no rows and
`"truncated_reason": "response size limit of 5000000 bytes reached"`.

```bash
curl -s -X POST "$LEXICON/api/v1/graph/query" -H 'Content-Type: application/json' -d '{
  "query": "MATCH (r:LSR)-[:BORROWED_FROM]->(d:LSR) WHERE r.language_code = $lang AND r.date_start < $before RETURN r.form_orthographic AS word, r.date_start AS year, d.language_name AS donor ORDER BY year LIMIT 3",
  "parameters": {"lang": "eng", "before": 1000}}'
```

```json
{
  "results": [
    {"word": "lion", "year": 700, "donor": "Latin"},
    {"word": "cooked", "year": 700, "donor": "Latin"},
    {"word": "cook", "year": 700, "donor": "Latin"}
  ],
  "count": 3,
  "truncated": false,
  "truncated_reason": null,
  "query": "MATCH (r:LSR)-[:BORROWED_FROM]->(d:LSR) WHERE r.language_code = $lang AND r.date_start < $before RETURN r.form_orthographic AS word, r.date_start AS year, d.language_name AS donor ORDER BY year LIMIT 3",
  "query_type": "read"
}
```

Nodes come back as `{labels, …properties}`, relationships as
`{type, source, target, properties}`, and paths as
`{nodes, relationships, length}`. Temporal values come back as ISO strings,
NaN and Infinity as strings, and bytes as base64. A query that hits the row
cap reports `"truncated": true, "truncated_reason": "row limit of 1000 reached"`.

Rejections (all `400`):

```json
{"error":"VALIDATION_ERROR","message":"Invalid query: Query contains disallowed write operation(s): SET. Only read-only queries are allowed","details":{"field":"query"}}
{"error":"VALIDATION_ERROR","message":"Invalid query: Query contains disallowed keyword(s): LOAD. LOAD CSV, CALL, USE, procedures (dbms.*, apoc.*) and administration commands are not allowed, even inside string literals or comments; pass literal values as $parameters","details":{"field":"query"}}
{"error":"VALIDATION_ERROR","message":"Invalid query: Query contains a unicode escape (\\u...); Neo4j decodes these before parsing, so they are not allowed. Pass literal values as $parameters","details":{"field":"query"}}
{"error":"VALIDATION_ERROR","message":"Invalid query: Backslashes are only allowed inside string literals and comments","details":{"field":"query"}}
{"error":"VALIDATION_ERROR","message":"Invalid Cypher syntax","details":{"neo4j_code":"Neo.ClientError.Statement.SyntaxError","line":1,"column":14}}
{"error":"VALIDATION_ERROR","message":"Query references a parameter that was not supplied","details":{"neo4j_code":"Neo.ClientError.Statement.ParameterMissing","missing_parameters":["lang"]}}
{"error":"QUERY_TIMEOUT","message":"Query exceeded the 1s time limit; add a LIMIT or make the MATCH more selective","details":{"neo4j_code":"Neo.ClientError.Transaction.TransactionTimedOutClientConfiguration"}}
```

(The second came from `{form_orthographic: "load"}` inside a string literal,
the third from `{form_orthographic: "\u0063astle"}`.)
A `QUERY_TIMEOUT` has `neo4j_code` in `details` when Neo4j stopped the query,
and empty `details` when the API stopped waiting for it (2 seconds after the
limit). When Neo4j has not begun the transaction within about 2 seconds
(it is down or has stopped answering), the API gives up with
`503 DATABASE_ERROR "Graph database is not available"`.

#### GET /graph/path

| Parameter | Rules |
|---|---|
| `from_lsr`, `to_lsr` | UUIDs, required, different. An unknown id is `404` |
| `max_hops` | 1–20, default 5 |
| `relationship_types` | Comma-separated, from `BORROWED_FROM`, `COGNATE_OF`, `DESCENDS_FROM`, `MERGED_WITH`, `RELATED_TO`, `SHIFTED_TO`; default all. Others are `400` |

Returns up to 10 shortest paths, following edges in either direction:
`{from_lsr, to_lsr, max_hops, relationship_types, paths_found, paths}`. Each
path is `{nodes, relationships, length}`, and each relationship's `source`
and `target` show its real direction. English *water* to German *Wasser* is
one 5-edge path through *\*watōr*: three edges go up from *water*, two come
down to *Wasser*.

```bash
curl -s "$LEXICON/api/v1/graph/path?from_lsr=03f24a96-212c-5f03-9ee9-024e6f7a7dde&to_lsr=886ad379-0907-486b-87cd-d0dd3376b937&relationship_types=DESCENDS_FROM"
```

```json
{
  "from_lsr": "03f24a96-212c-5f03-9ee9-024e6f7a7dde",
  "to_lsr": "886ad379-0907-486b-87cd-d0dd3376b937",
  "max_hops": 5,
  "relationship_types": ["DESCENDS_FROM"],
  "paths_found": 1,
  "paths": [
    {
      "nodes": [
        {"labels": ["LSR"], "id": "03f24a96-212c-5f03-9ee9-024e6f7a7dde", "form_orthographic": "water", "language_code": "eng", "date_start": 700, …},
        {"labels": ["LSR"], "id": "a59428b5-e98f-4289-bb93-b557d8e67ea3", "form_orthographic": "water", "language_code": "enm", "date_start": 1150, "date_end": 1500, …},
        {"labels": ["LSR"], "id": "57c4ddc0-d7ab-482c-be98-eba12c2907ab", "form_orthographic": "wæter", "language_code": "ang", "date_start": 700, "date_end": 1150, …},
        {"labels": ["LSR"], "id": "d6818940-2e4b-465e-b8d7-c8e92c096df6", "form_orthographic": "*watōr", "language_code": "gem-pro", …},
        {"labels": ["LSR"], "id": "5a9278d7-eb07-417c-9b27-03b1176e1085", "form_orthographic": "wazzer", "language_code": "gmh", "date_start": 1050, "date_end": 1350, …},
        {"labels": ["LSR"], "id": "886ad379-0907-486b-87cd-d0dd3376b937", "form_orthographic": "Wasser", "language_code": "deu", "date_start": 1500, …}
      ],
      "relationships": [
        {"type": "DESCENDS_FROM", "source": "03f24a96-212c-5f03-9ee9-024e6f7a7dde", "target": "a59428b5-e98f-4289-bb93-b557d8e67ea3", "properties": {"evidence": "docs example, added by hand", "confidence": 0.9, "created_at": "2026-09-27T07:34:59.878000000+00:00"}},
        …,
        {"type": "DESCENDS_FROM", "source": "886ad379-0907-486b-87cd-d0dd3376b937", "target": "5a9278d7-eb07-417c-9b27-03b1176e1085", "properties": {"evidence": "docs example, added by hand", "confidence": 0.9, "created_at": "2026-09-27T07:34:59.878000000+00:00"}}
      ],
      "length": 5
    }
  ]
}
```

#### GET /graph/etymology/{id}

Same traversal as `/lsr/{id}/etymology`, with `max_depth` 1–50 (default 10).
Returns `{lsr_id, chain, relationships, depth, max_depth, truncated,
proto_form}`, where `chain` holds full nodes and `relationships` the
`DESCENDS_FROM` edges with their `evidence` and `confidence`. As in the
`/lsr` route, `proto_form` is the last node of the chain, or null when
`truncated` is true. With `max_depth=2`:

```json
{
  "lsr_id": "03f24a96-212c-5f03-9ee9-024e6f7a7dde",
  "chain": [
    {"labels": ["LSR"], "id": "03f24a96-212c-5f03-9ee9-024e6f7a7dde", "form_orthographic": "water", "language_code": "eng", "date_start": 700, …},
    {"labels": ["LSR"], "id": "a59428b5-e98f-4289-bb93-b557d8e67ea3", "form_orthographic": "water", "language_code": "enm", "date_start": 1150, "date_end": 1500, …},
    {"labels": ["LSR"], "id": "57c4ddc0-d7ab-482c-be98-eba12c2907ab", "form_orthographic": "wæter", "language_code": "ang", "date_start": 700, "date_end": 1150, …}
  ],
  "relationships": [
    {"type": "DESCENDS_FROM", "source": "03f24a96-212c-5f03-9ee9-024e6f7a7dde", "target": "a59428b5-e98f-4289-bb93-b557d8e67ea3", "properties": {"evidence": "docs example, added by hand", "confidence": 0.9, "created_at": "2026-09-27T07:34:59.878000000+00:00"}},
    {"type": "DESCENDS_FROM", "source": "a59428b5-e98f-4289-bb93-b557d8e67ea3", "target": "57c4ddc0-d7ab-482c-be98-eba12c2907ab", "properties": {"evidence": "docs example, added by hand", "confidence": 0.9, "created_at": "2026-09-27T07:34:59.878000000+00:00"}}
  ],
  "depth": 2,
  "max_depth": 2,
  "truncated": true,
  "proto_form": null
}
```

#### GET /graph/cognates/{id}

Same rules as `/lsr/{id}/cognates`. Returns
`{lsr_id, cognate_count, truncated, languages, by_language}`: full nodes
grouped by language, at most 100, with `truncated` set when there were more.

#### POST /graph/bulk/export

Exports one language's records, one page per request.

| Field | Type | Rules |
|---|---|---|
| `language` | string, required | A language code (see [Conventions](#conventions)) |
| `format` | string | `json` (default) or `csv` |
| `include_relationships` | boolean | Default true: the outgoing relationships of the exported records, up to 50,000 |
| `offset` | integer | 0–10,000,000, default 0. Records are ordered by id |
| `limit` | integer | 1–10,000, default 10,000 |
| `run_async` | boolean | Default false |

A page ends after `limit` records, or earlier once its records reach about
10 MB of JSON; `size_limited` is then true. With their 384-number semantic
vectors, WOLD records fill 10 MB at about 1,100 records: an `eng` export
with the default limit returned 1125 of 1516 records, with
`"size_limited": true` and `"next_offset": 1125`. Each export query has a
60-second server-side timeout, and the API stops waiting 2 seconds later.

```bash
curl -s -X POST "$LEXICON/api/v1/graph/bulk/export" -H 'Content-Type: application/json' \
  -d '{"language": "eng", "limit": 2, "offset": 0}'
```

```json
{
  "status": "completed",
  "message": "Exported 2 of 1516 LSRs",
  "format": "json",
  "language": "eng",
  "offset": 0,
  "limit": 2,
  "count": 2,
  "total": 1516,
  "truncated": true,
  "next_offset": 2,
  "size_limited": false,
  "relationship_count": 0,
  "relationships_truncated": false,
  "items": [
    {"labels": ["LSR"], "id": "00426095-9635-513b-b5ed-8ed4e72c045e", "form_orthographic": "room", "language_code": "eng", "date_start": 700, "period_label": "Old English (inherited from Proto-Germanic)", "source_databases": ["wold"], …},
    {"labels": ["LSR"], "id": "0042686d-23f6-5e60-a392-c599e2e99f17", "form_orthographic": "fall", "language_code": "eng", "date_start": 700, "period_label": "Old English (inherited from Proto-Indo-European)", "source_databases": ["wold"], …}
  ],
  "relationships": []
}
```

To fetch the next page, repeat the request with `offset` set to
`next_offset` until `next_offset` is null. With `"format": "csv"`, `items`
and `relationships` are replaced by `csv` and `relationships_csv` strings.
Their header row is the sorted union of property names, and list values are
JSON-encoded.

With `"run_async": true`, the export runs as a background job:

```bash
curl -s -X POST "$LEXICON/api/v1/graph/bulk/export" -H 'Content-Type: application/json' \
  -d '{"language": "non", "format": "csv", "run_async": true}'
```

```json
{
  "status": "accepted",
  "job_id": "a811f983c824449ca71be5c21c8fb73c",
  "status_url": "/api/v1/graph/bulk/status/a811f983c824449ca71be5c21c8fb73c",
  "result_url": "/api/v1/graph/bulk/result/a811f983c824449ca71be5c21c8fb73c"
}
```

`GET /graph/bulk/status/{job_id}`:

```json
{
  "job_id": "a811f983c824449ca71be5c21c8fb73c",
  "kind": "bulk_export",
  "status": "completed",
  "created_at": 1790523730.641452,
  "duration_seconds": 0.085,
  "error": null,
  "params": {"language": "non", "format": "csv", "offset": 0, "limit": 10000},
  "download_url": "/api/v1/graph/bulk/result/a811f983c824449ca71be5c21c8fb73c"
}
```

`status` is `pending`, `running`, `completed` or `failed`.
`GET /graph/bulk/result/{job_id}` returns `job_id`, `"status": "completed"`
and the same fields as a synchronous export, without `message`. While the job is still pending or running it returns
`{"job_id", "status", "message": "Job still running"}`, and for a failed job
it returns `503 DATABASE_ERROR`.

Job lifetime:

- Jobs run inside the worker that accepted them, at most 4 at a time per
  process; further jobs wait as `pending`.
- A finished job and its result are kept for one hour, then both routes
  return `404 NOT_FOUND`.
- Each process holds at most about 100 MB of results (measured as JSON, in
  its memory or written by it to Redis). Past that, its oldest finished jobs
  are dropped early and answer `404` like expired ones, so fetch results
  soon.
- With Redis connected, any worker can answer for any job. If Redis cannot be
  reached while polling, the routes return `503` rather than a misleading `404`.
- Without Redis, only the accepting worker knows the job, and a restart loses
  it.

## GraphQL

`POST /graphql` with `{"query": "...", "variables": {...}}`. `GET /graphql`
also accepts `?query=`, and serves GraphiQL to a browser. Authentication and
rate limiting are the same as for REST. Field and argument names are
camelCase.

```bash
curl -s -X POST "$LEXICON/graphql" -H 'Content-Type: application/json' \
  -d '{"query": "{ lsr(id: \"03f24a96-212c-5f03-9ee9-024e6f7a7dde\") { form dateStart language { isoCode name isLiving } ancestors { form language { isoCode } } cognates { form language { isoCode } } } }"}'
```

```json
{
  "data": {
    "lsr": {
      "form": "water",
      "dateStart": 700,
      "language": {"isoCode": "eng", "name": "English", "isLiving": null},
      "ancestors": [
        {"form": "water", "language": {"isoCode": "enm"}},
        {"form": "wæter", "language": {"isoCode": "ang"}},
        {"form": "*watōr", "language": {"isoCode": "gem-pro"}}
      ],
      "cognates": [
        {"form": "Wasser", "language": {"isoCode": "deu"}},
        {"form": "wazzer", "language": {"isoCode": "gmh"}},
        {"form": "water", "language": {"isoCode": "nld"}}
      ]
    }
  }
}
```

### Schema overview

| Root field | Returns | Notes |
|---|---|---|
| `lsr(id)` | `LSR` or null | Null for an unknown or malformed id, without an error |
| `searchLsr(form, language, dateStart, dateEnd, limit = 20, offset = 0)` | `[LSR]` | Same search as REST, without the semantic-field filter. `limit` is clamped to 1–100. No total count |
| `language(isoCode)` / `languages(family)` | `Language` or null / `[Language]` | Languages present in the graph. Name and family are the most frequent values on their LSRs |
| `etymology(lsrId, maxDepth = 20)` | `EtymologyChain` or null | `steps { depth lsr }`, `protoForm` (null when truncated), `depth`, `truncated`. `maxDepth` is clamped to 1–50 |
| `semanticTrajectory(form, language)` | `SemanticTrajectory` | `status`, `explanation`, `points { date embedding2d definition attestationCount }`, `shiftEvents { date changeType confidence beforeMeaning afterMeaning }`. `status` and `explanation` are REST's; `points` and `shiftEvents` are empty unless `status` is `ok` |
| `dateText(text, language)` | `DateAnalysis` | `predictedRange status confidence explanation contentWords datedWords diagnosticVocabulary { form earliestAttestation dateLabel lastAttestation setsBound }` |
| `detectAnachronisms(text, claimedDate, language)` | `AnachronismAnalysis` | `verdict confidence explanation contentWords datedWords anachronisms { form type earliestAttestation dateLabel lastAttestation gapYears severity }` |

`LSR` has these fields: `id`, `form`, `formPhonetic`, `language`, `dateStart`,
`dateEnd`, `definitions`, `confidence`, `isReconstructed` and `attestations`
(always empty). It also has three traversal fields:

- `ancestors(depth = 10)`: at most 100, depth clamped to 50
- `descendants(depth = 3)`: at most 500, depth clamped to 10
- `cognates`: at most 100, same rules as REST

`Language` has `isoCode`, `name`, `family`, `branchPath` and `isLiving`.
`isLiving` is false for proto-languages and otherwise null, because the graph
records no living/extinct status.

Arguments are checked as in REST:

- **Language codes** are normalized and validated like REST's (see
  [Conventions](#conventions)), `language(isoCode)` included:
  `language: "en"` means `eng`, `language(isoCode: "EN")` finds English, and
  `language: "english"` or `language(isoCode: "")` is an
  `INVALID_LANGUAGE_CODE` error.
- **Texts** in `dateText` and `detectAnachronisms` must have 10–100,000
  characters, and at least 10 once runs of whitespace are collapsed.
- **Years** (`dateStart`, `dateEnd`, `claimedDate`) must be in −10000 to
  2100, as in REST: `dateStart: 2500` gives
  `"dateStart must be between -10000 and 2100"` (`VALIDATION_ERROR`).
  `searchLsr` refuses a `dateEnd` before `dateStart` with
  `INVALID_DATE_RANGE`.

### Limits

Queries are checked before they run. A query over a limit is rejected
without running.

| Limit | Value | Error |
|---|---|---|
| Nesting depth | 5 | `'anonymous' exceeds maximum operation depth of 5` |
| Aliases per document | 15 | `16 aliases found. Allowed: 15` |
| Estimated graph traversals | 1000 | `extensions.code: QUERY_TOO_COMPLEX` |

Each `ancestors`, `descendants` or `cognates` field runs one traversal per
parent record. The estimate assumes every list is full: 100 ancestors, 500
descendants, 100 cognates, 51 etymology steps, and `searchLsr`'s literal
`limit` (20 by default, 100 when `limit` is a variable). So
`lsr { descendants { cognates { form } } }` is allowed (1 + 500 = 501), but
`searchLsr(limit: 100) { descendants { cognates { form } } }` is not:

```json
{
  "data": null,
  "errors": [
    {
      "message": "Query may run up to 50100 graph traversals (limit 1000); nest fewer ancestors/descendants/cognates fields or lower searchLsr's limit",
      "locations": [{"line": 1, "column": 1}],
      "extensions": {"code": "QUERY_TOO_COMPLEX"}
    }
  ]
}
```

### Errors

GraphQL answers HTTP 200 even when `errors` is present. The exceptions are
`401` and `429` from authentication and rate limiting, and `400` with a
plain-text message when the body is not JSON or holds no query. Errors raised
while resolving a field carry `extensions.code`, taken from the REST codes:
`INVALID_LANGUAGE_CODE`, `INVALID_DATE_RANGE` and `VALIDATION_ERROR` for bad
arguments, `DATABASE_ERROR` when the graph is unavailable, and
`INTERNAL_ERROR`, with a generic message, for anything unexpected. The failed
field is null, and so is `data` when that field cannot be null (`searchLsr`,
`languages`, `semanticTrajectory`, `dateText`, `detectAnachronisms`):

```json
{"data": null, "errors": [{"message": "Invalid language code format: english", "locations": [{"line": 1, "column": 3}], "path": ["searchLsr"], "extensions": {"code": "INVALID_LANGUAGE_CODE"}}]}
```

Here Neo4j was unreachable:

```json
{"data": {"lsr": null}, "errors": [{"message": "Graph database is not available", "locations": [{"line": 1, "column": 3}], "path": ["lsr"], "extensions": {"code": "DATABASE_ERROR"}}]}
```

Syntax, schema, depth and alias errors have no `extensions.code`. The
traversal limit error carries `QUERY_TOO_COMPLEX`. A missing record is null,
not an error.

### Examples

Search (Elasticsearch connected, so near misses match too):

```graphql
{ searchLsr(form: "castle", language: "eng", limit: 5) { id form dateStart dateEnd definitions language { name family } } }
```

```json
{"data": {"searchLsr": [
  {"id": "43c29b18-1722-59c5-9220-9b35adf06a85", "form": "castle", "dateStart": 1075, "dateEnd": null, "definitions": ["the fortress"], "language": {"name": "English", "family": "Indo-European"}},
  {"id": "a634b827-b801-537a-bb13-874da922f53a", "form": "cattle", "dateStart": 1275, "dateEnd": null, "definitions": ["the cattle"], "language": {"name": "English", "family": "Indo-European"}},
  {"id": "2f17f28b-b974-5363-a613-9031725bd86a", "form": "candle", "dateStart": 700, "dateEnd": null, "definitions": ["the candle"], "language": {"name": "English", "family": "Indo-European"}},
  {"id": "744b5c8c-7ec4-52d0-8229-873293dcd14d", "form": "cast", "dateStart": 1300, "dateEnd": null, "definitions": ["to cast"], "language": {"name": "English", "family": "Indo-European"}}]}}
```

Languages:

```graphql
{ language(isoCode: "EN") { isoCode name family isLiving } languages(family: "Indo-European") { isoCode name } }
```

```json
{"data": {"language": {"isoCode": "eng", "name": "English", "family": "Indo-European", "isLiving": null}, "languages": [{"isoCode": "eng", "name": "English"}]}}
```

(Only `eng` is listed under Indo-European because only the WOLD English
records carry a family. The WOLD donor placeholders have none:
`language(isoCode: "la-vul")` gives `{"isoCode": "la-vul", "name":
"Vulgar Latin", "family": null, "isLiving": null}`.)

Etymology:

```graphql
{ etymology(lsrId: "03f24a96-212c-5f03-9ee9-024e6f7a7dde") { depth truncated steps { depth lsr { form language { isoCode } } } protoForm { form isReconstructed } } }
```

```json
{"data": {"etymology": {"depth": 3, "truncated": false, "steps": [
  {"depth": 0, "lsr": {"form": "water", "language": {"isoCode": "eng"}}},
  {"depth": 1, "lsr": {"form": "water", "language": {"isoCode": "enm"}}},
  {"depth": 2, "lsr": {"form": "wæter", "language": {"isoCode": "ang"}}},
  {"depth": 3, "lsr": {"form": "*watōr", "language": {"isoCode": "gem-pro"}}}],
  "protoForm": {"form": "*watōr", "isReconstructed": true}}}}
```

With `maxDepth: 2` the answer is `{"depth": 2, "truncated": true, "protoForm": null}`.

Dating, with the text passed as a variable:

```graphql
query ($t: String!) { dateText(text: $t, language: "eng") { predictedRange confidence status explanation contentWords datedWords diagnosticVocabulary { form earliestAttestation dateLabel lastAttestation setsBound } } }
```

Variables `{"t": "The king listened to the radio in his castle"}` give:

```json
{"data": {"dateText": {"predictedRange": [1913, 2026], "confidence": 0.8, "status": "ok",
  "explanation": "Written no earlier than 1913 (first attestation of radio); 4 of 4 content words dated.",
  "contentWords": 4, "datedWords": 4, "diagnosticVocabulary": [
    {"form": "radio", "earliestAttestation": 1913, "dateLabel": "1913", "lastAttestation": null, "setsBound": "lower"},
    {"form": "castle", "earliestAttestation": 1075, "dateLabel": "before 1075", "lastAttestation": null, "setsBound": null},
    {"form": "king", "earliestAttestation": 700, "dateLabel": "Old English (inherited from Proto-Germanic)", "lastAttestation": null, "setsBound": null},
    {"form": "listened", "earliestAttestation": 700, "dateLabel": "Old English (inherited from Proto-Germanic)", "lastAttestation": null, "setsBound": null}]}}}
```

In GraphQL, `diagnosticVocabulary.form` is the text's token, lowercased with
diacritics stripped (REST calls it `word`).

Anachronisms:

```graphql
{ detectAnachronisms(text: "The knight listened to the radio in his castle", claimedDate: 1300, language: "eng") { verdict confidence explanation contentWords datedWords anachronisms { form type earliestAttestation dateLabel gapYears severity } } }
```

```json
{"data": {"detectAnachronisms": {"verdict": "anachronistic", "confidence": 0.75,
  "explanation": "1 word(s) first attested well after 1300: radio (1913).", "contentWords": 4, "datedWords": 3,
  "anachronisms": [{"form": "radio", "type": "coined_after", "earliestAttestation": 1913, "dateLabel": "1913", "gapYears": 613, "severity": "high"}]}}}
```

`dateLabel` is `""` on `obsolete_before` entries.

Semantic trajectory (experimental; see the REST section for what the numbers mean):

```graphql
{ semanticTrajectory(form: "calf", language: "eng") { status explanation points { date definition embedding2d attestationCount } shiftEvents { date changeType confidence } } }
```

```json
{"data": {"semanticTrajectory": {"status": "ok", "explanation": "2 dated senses compared across 2 dates.", "points": [
  {"date": 700, "definition": "the calf", "embedding2d": [-0.3148, -0.0], "attestationCount": 0},
  {"date": 1325, "definition": "the calf of the leg", "embedding2d": [0.3148, 0.0], "attestationCount": 0}],
  "shiftEvents": []}}}
```

`semanticTrajectory(form: "male", language: "eng")`, whose two senses are
both dated 1382, gives `"status": "insufficient_data"`, the explanation
shown for *male* in the REST section, and empty `points` and `shiftEvents`.

## Known limitations

These are current behaviours of the code, listed so that results are not
misread.

| Area | Behaviour |
|---|---|
| Semantic drift | Homographs and different parts of speech count as senses of one word. Records created with `POST /lsr/` have no semantic vector, so drift ignores them |
| `POST /lsr/` | Stores only the six request fields. No relationships, vectors or language names; no update route |
| `POST /graph/query` | The row and size caps limit what is returned, not what the API reads: a query that returns one huge value is read whole first. Turn the endpoint off with `GRAPH_QUERY_ENABLED=false` where API keys go to clients you do not trust (see [SECURITY.md](../SECURITY.md)) |
