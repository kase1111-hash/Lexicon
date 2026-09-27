# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

The core loop now works end to end: ingested data reaches the graph, and the
analyses give answers that hold up on real data.

### Added
- Ingestion writes to Neo4j: idempotent upserts keyed on ids derived from the
  source record, uniqueness constraint and indexes, relationship edges, and
  Elasticsearch bulk indexing when configured. When Elasticsearch is
  configured but unreachable, ingestion logs one warning and writes to Neo4j
  only. `--dry-run` runs the whole pipeline except the write
- WOLD: first-attestation years from the `Age` column (12,219 dated forms,
  1,511 of them English), donor words and languages from `borrowings.csv` as
  `BORROWED_FROM` edges, Glottolog codes for languages without an ISO code;
  numbered meanings become readable definitions (`male(1)` → `male (of
  person)`, `the spring(2)` → `the spring (springtime)`)
- Wiktionary: etymology templates (`{{inh}}`, `{{bor}}`, `{{der}}`, `{{cal}}`,
  `{{cog}}`, including `+` forms) become ancestor chains, loans and cognates;
  missing ancestors become placeholder records. Dates marked BC/BCE become
  negative years (`8th c. BCE` → -800); unmarked years count only from 500
  on
- The ingestion summary reports failed writes (`Failed to write: N LSRs, M
  relationships`) and LSRs that Elasticsearch did not index
  (`` N LSRs not indexed; run `lexicon reindex` ``)
- Analysis results report coverage (distinct content words, dated words,
  unknown words) and a status; `insufficient_data` replaces confident
  verdicts when evidence is missing
- `date_label`: each dated word's first attestation as its source states it
  (`c. 1220`, `before 1382`, `1835`, `Old English (inherited from
  Proto-Germanic)`, or `earliest in corpus: <title>`). REST returns it on each
  `coined_after` anachronism and `diagnostic_vocabulary` entry, GraphQL as
  `dateLabel` on `Anachronism` and `DiagnosticWord`; the CLI prints it when it
  says more than the year. Corpus LSRs store `earliest in corpus: <title>` as
  their `period_label`
- `lexicon` CLI commands run against the graph: `ingest` (all four sources),
  `search`, `analyze`, `stats`, `reindex`; `--json` output
- Rate limiting per client IP (only `/health` and the documentation pages
  `/docs`, `/docs/oauth2-redirect`, `/redoc` and `/openapi.json` are exempt),
  request metrics feeding `/metrics`, `/health` that probes each configured
  store (503 without Neo4j)
- `GRAPH_QUERY_ENABLED` (default `true`; `false` in `config/.env.production`)
  turns off `POST /api/v1/graph/query`, which then answers 403
  `QUERY_DISABLED`
- When the API starts, or connects within three minutes to an Elasticsearch
  that came up after it, and the index holds fewer documents than Neo4j has
  LSRs (for example after ingesting while Elasticsearch was down), it
  rebuilds the index from Neo4j in the background and then clears cached
  searches
- GraphQL depth, alias and traversal limits; GraphQL errors instead of
  empty results on database failures
- Sample corpus in `data/corpus/` (a format demo: four excerpts are too few
  to date words by, and loading them skews dating); `make test-db` for
  DB-backed tests on a throwaway Neo4j; CI jobs for the Docker build and
  DB-backed tests
- CITATION.cff
- Earlier in this cycle: GraphQL API at `/graphql`; hashed n-gram embedding
  pipeline (384 dimensions); CLICS/IDS and corpus adapters; phonetic
  matching; async bulk-export jobs; Alembic migrations

### Changed
- Dating: the estimate is a terminus post quem (the newest word's first
  attestation) with an open upper bound, instead of a median of ranges;
  `date_end` means last attestation or still in use, never a copy of the
  first attestation
- Anachronism verdicts depend on coverage; `obsolete_before` archaisms are
  reported separately. The explanation of a `consistent` verdict names the
  words that postdate the claim by 50 years or less (`…; 1 postdates it by
  50 years or less: mountain (1275).`)
- Contact events are dated by the borrowed word's first attestation and
  include both directions; their sample words are distinct. Borrowings from
  an undetermined language (`und`) form no event
- Semantic drift (experimental) compares only senses that have a date and a
  definition vector and were first attested in at least two different
  years, each with the closest sense of the latest earlier date; senses of
  one date are not a change over time. Otherwise it says why (`Found 2
  dated senses with a definition for 'male' in 'eng', all first attested
  in 1382; senses of the same date are not a change over time, and drift
  needs senses first attested in at least two different years.`).
  `compare-concept` gives each language a `status` and an `explanation`, and
  GraphQL `semanticTrajectory` gains `status` and `explanation` and returns
  no points in that case
- Text tokens are normalized exactly like stored forms (Unicode, diacritics),
  with English inflection fallbacks
- Language codes: REST, GraphQL and the CLI accept ISO 639-3, ISO 639-1
  (`en` → `eng`, the 36 codes in `src/utils/languages.py`), Wiktionary codes
  for historical varieties and proto-languages (`gem-pro`, `la-vul`,
  `roa-opt`) and Glottocodes (`yaku1245`). Anything else (`english`,
  `en-gb`, `12`) is refused with `INVALID_LANGUAGE_CODE` (HTTP 400 in REST, a
  GraphQL error, exit status 1 in the CLI) instead of being dropped. REST
  takes codes of up to 20 characters (`ine-bsl-pro`), and GraphQL
  `language(isoCode:)` normalizes its code too (`en` finds English).
  Ingestion maps language names to real ISO 639-3 codes
- Years are -10000 to 2100, the range an LSR can hold, in REST
  (`claimed_date`, search and contact-event dates, `POST /lsr/`) and GraphQL
- GraphQL validates its arguments like REST: language codes, text length
  (10 to 100,000 characters), years and, in `searchLsr`, `dateStart` not
  after `dateEnd`
- LSRs ingested without a date have `date_confidence` 0.0 (was 1.0) and
  keep the source's own label as `period_label` (WOLD `Modern`). When
  records merge, the earliest date brings its confidence and label
- Lineage traversals (etymology, ancestors, descendants, cognates) expand the
  lineage breadth first, one generation per query: the work is linear in the
  size of the lineage, and the results are unchanged
- Elasticsearch and Redis are used only when configured (`ELASTICSEARCH_URI`
  or `ELASTICSEARCH_PASSWORD`; `REDIS_URI` or `REDIS_PASSWORD`). `/health`
  reports an unconfigured store as `not_configured`, which does not make the
  status `degraded`, so a Neo4j-only deployment is `healthy`. An API without
  Redis configured logs `Redis is not configured: …` at INFO, not as a
  warning
- `lexicon reindex` clears the API's cached searches after rebuilding the
  index, as the API's own rebuild does
- Logs go to stderr, so `lexicon … --json` prints valid JSON on stdout. The
  Neo4j driver no longer reports unknown labels, properties or relationship
  types, and in the API its logger follows `DB_LOG_LEVEL`. The Elasticsearch
  client's per-request and retry logging is silenced
- OpenAPI: title "Lexicon API" and a description of what the API does;
  `GET /` reports the name "Lexicon API"; the `lexicon --help` description
  begins "Lexicon - date a text by its words"
- PostgreSQL is optional (compose profile `postgres`); no code uses it yet
- Services are published on 127.0.0.1 only, and `ls-api` binds 127.0.0.1
  unless `API_HOST` says otherwise; the production overlay requires
  `API_KEY`
- Docker image built on Python 3.11 without apt packages
- `.env` settings (including `API_KEY`) are applied to the running app
- Dependencies bumped to versions without known vulnerabilities (FastAPI,
  Starlette, Strawberry, aiohttp)
- Documentation rewritten around what the system does; historical plans,
  audits and the original design spec moved to `docs/archive/`

### Fixed
- Ingestion discarded everything it resolved; nothing reached the graph
- WOLD borrowing scores were read on the wrong scale, so every form counted
  as borrowed; donor columns that do not exist were read
- WOLD headwords kept sense numbers and optional parts in parentheses
  (`call (1)`, `(sea)gull`, `wood(s)`), so those words never matched a text
- A failed WOLD or CLICS download (host unreachable, read timeout, any other
  transport error such as a proxy failure, HTTP error status) ends with a
  one-line error and exit code 1 instead of a traceback
- WOLD donor languages got Glottolog codes even where WOLD lists an ISO
  639-3 code, which split one language into two codes; they now get WOLD's
  own ISO code first. Late, Vulgar, Medieval and Neo-Latin donors are
  `la-lat`, `la-vul`, `la-med` and `la-new` instead of `lat`, and donor
  languages with no code at all (`Proto-Altaic`, `Saharan`) are `und`
  instead of an empty code
- Arabic had two codes (`ar` mapped to `arb`, the name `Arabic` to `ara`),
  so one word could become two LSRs in one run; it is `ara` everywhere
- `--language` selected Wiktionary and CLICS languages only by an exact
  name, so ISO codes ingested nothing, and a comma-separated list matched
  nothing (Wiktionary) or lost each name written after `, ` (CLICS).
  Wiktionary, WOLD and CLICS now take a comma-separated list of names or ISO
  639-3/639-1 codes (WOLD and CLICS also Glottocodes); the corpus takes one
  name or code
- Wiktionary templates without a term (`{{bor|en|fr}}`, `{{der|en|la|-}}`)
  made placeholder words out of the language code or `-`
  (`eng:coffee -BORROWED_FROM-> eng:fr`); they now give no link
- Wiktionary dates marked BC/BCE were read as CE years
- Entity resolution rebuilt its index after every insert, and compared every
  entry with every stored form for fuzzy and phonetic candidates that could
  never match (both quadratic); a dry run of all of WOLD now takes about
  30 seconds
- A placeholder's id is shared by every run that links to its word, so a
  later run overwrote an earlier run's gloss and provenance. Placeholders
  now have `date_confidence` 0.0 and are written fill-only: an existing node
  keeps every property that is set, gains the missing ones and merges its
  sources, and takes dating only as a whole onto an undated node
- Attestation years outside -10000..2100 bypassed LSR validation and were
  stored as `date_start`; ingestion now drops them with a warning
- The corpus `date_confidence` metadata key was ignored and every word got
  1.0; a word now takes the confidence of the earliest document it appears
  in
- `lexicon ingest` exited 0 when part of the graph write failed; it exits 1
- Wiktionary dates were taken from any number on the page (page numbers,
  ids); templates were stripped from etymology text
- Graph endpoints returned 500 for any stored node (unserialized Neo4j
  datetimes)
- `/graph/query` keyword filter could be bypassed (e.g. `LOAD CSV` via extra
  whitespace, or `\u0043ALL`, which Neo4j decodes to `CALL` before parsing);
  queries now run read-only with timeouts and caps on the rows and bytes
  returned, and unicode escapes anywhere, or backslashes outside string
  literals, are refused
- `/graph/query` kept retrying while Neo4j was unreachable; it answers 503
  once Neo4j has not begun the transaction within about 2 s, and does not
  retry a query whose connection dropped
- Async bulk-export results were kept whole for an hour with no bound on
  their size. A page now also ends at about 10 MB of JSON (`size_limited`),
  and each process holds at most about 100 MB of results; past that the
  oldest finished jobs are dropped and answer 404
- `/metrics` was exempt from rate limiting although it checks the API key,
  so keys could be guessed through it without limit; `/` is limited too
- Behind a reverse proxy every client shared one rate-limit bucket, because
  `X-Forwarded-For` was never trusted. The image and the dev override run
  uvicorn with `--proxy-headers`, and compose passes `FORWARDED_ALLOW_IPS`
  (default `127.0.0.1`), the proxy addresses to trust
- The production check accepted `*` in `CORS_ORIGINS` when it was combined
  with other origins or padded
- `GET /lsr/search` passed any `offset` to Neo4j, so a huge one gave 503;
  it now stops at 10,000,000 (400 beyond)
- `/graph/etymology/{id}` gave the last node of a truncated chain as
  `proto_form`; it is now `null` when `truncated` is true
- API errors hid database outages as empty or "consistent" results;
  `/analyze/*` returned 500 instead of 503 `DATABASE_ERROR` when Neo4j became
  unreachable after startup
- Searches, `POST`/`DELETE /lsr`, the analyses, GraphQL's `language`
  lookups and `lexicon stats` ran without a client deadline and could hang
  for one to two minutes when Neo4j stopped answering; they now answer 503
  `DATABASE_ERROR "… timed out"` after about 17 s (statistics: about a
  minute). `/graph/path` and bulk export said "… failed" on a timeout; they
  say "… timed out" like the other routes. A Neo4j failure while loading
  Elasticsearch hits is a 503, not a fallback search
- Searches answered by the Neo4j fallback while Elasticsearch failed, or
  before the API had connected to a configured Elasticsearch, were cached,
  so their fuzzy matches stayed missing for up to three minutes; they are no
  longer cached
- A reindex lock left by a worker that died stopped the API from rebuilding
  the search index until it restarted; a worker that finds the lock taken
  checks the index again once the lock is released or expires
- The CLI printed a traceback (exit 1) when Neo4j failed after connecting;
  it prints `Error: … no result was produced.` and exits 2. `lexicon stats`
  (with `--json` too) no longer prints partial counts when a count fails,
  and exits 2
- `lexicon analyze drift --json` printed plain text
- CLI output piped into a reader that stops early (`| head`) ended with a
  `BrokenPipeError` traceback; it now exits quietly with status 1
- A corpus document whose sidecar names its `language` but no
  `language_code` got the code of `--language`; it gets that language's
  code (or `und`, with a warning, for a name Lexicon does not know)
- Donor words of different languages that a source names without a code
  (WOLD's *Saharan*, *Pre-Rangi*) shared one `und` record when spelled
  alike; each language keeps its own
- English inflections tried the bare stem before stem + e, so `faded` was
  dated as `fad` and could be flagged anachronistic; the base form comes
  first (`fades` → `fade`, `passes` → `pass`)
- The tokenizer split words at combining marks, so Hindi, Sanskrit and Tamil
  (and vocalized Arabic or Hebrew) texts had no content words, and the
  corpus adapter split such words too; both now take a word as letters with
  their combining marks (`src/utils/text.py`)
- Contact events counted sense records as words (one borrowed word with five
  senses was a five-word event; `vocabulary_count`, thresholds, intensity
  and date spread now count each word once), gave full domain-coherence
  confidence without any domain evidence (the domain score is now 0 without
  domains and scales with the share of words that have one), and classified
  WOLD's `Kinship` field as `trade` (matching `ship`); contact types now
  match whole words
- With `conflicting_evidence`, an obsolete word was marked as setting the
  upper bound (`sets_bound: "upper"`) although the range runs to the
  present
- JSON log files and Elasticsearch log shipping got ANSI color codes in
  `level` when stderr was a terminal
- Sentry never initialized with the pinned Strawberry and Starlette
  (sentry-sdk 2.19.2); sentry-sdk is now 2.70.0 (at least 2.56 in
  `pyproject.toml`), with its Strawberry integration
- `make run-api-prod` checked Redis differently from the API and could start
  4 workers that shared nothing; it starts one unless Redis is configured
  and answers
- The compose default `APP_VERSION=0.1.0` overrode the image's own version;
  it is empty unless set (the env templates no longer pin it), so the API
  and Sentry report the version of the code
- Any `NEO4J_USER` other than `neo4j` stopped the Neo4j container, and so
  the whole stack; the bundled Neo4j and the api's login to it always use
  `neo4j`, and `NEO4J_USER` applies only to a Neo4j outside compose
- The production overlay said managed databases could be used by setting
  `NEO4J_URI` / `ELASTICSEARCH_URI` / `REDIS_URI`, which never reach the api
  container; it now shows the overlay that does
- Elasticsearch's percentage disk watermarks left the search index
  unassigned on a disk about 90% full, even with gigabytes free, so every
  search fell back to Neo4j; `docker-compose.yml` sets absolute watermarks
  (low 2gb, high 1gb, flood stage 512mb)
- First `docker compose up` failed: Postgres init script, override file
  replacing `.env` passwords, a Neo4j healthcheck using a missing `curl`,
  unused Neo4j plugins, and a Python 3.14 base image the pinned dependencies
  cannot build on
- Cognate queries returned a word's own ancestors; search pagination skipped
  and repeated records; the `semantic_field` filter never matched
- CI: security tests hard-coded a local path; coverage gate set to the
  measured floor; release workflow no longer hides type-check failures;
  rate-limit tests failed when a minute boundary fell inside them; random
  parametrize ids broke `pytest -n`; version tests pinned `0.1.0`, so a
  version bump failed them

### Removed
- `license.md` / `license-summary.md` (PolyForm); the project is MIT
- `scripts/generate_postman.py`; import `/openapi.json` into Postman instead
- Unused dependencies: pywikibot, lxml, tqdm, email-validator
- Nonexistent secrets-manager and JWT settings

## [0.1.0] - 2024-01-01

### Added

#### Core Features
- Lexical State Record (LSR) data model for cross-linguistic lexical evolution
- Etymology tracing and borrowing path detection
- Text dating using diachronic vocabulary attestation patterns
- Semantic drift analysis for tracking meaning changes
- Language contact event detection
- Anachronism detection for historical text analysis

#### Data Adapters
- Wiktionary adapter for etymological data ingestion
- CLLD/WOLD adapter for loanword data with borrowing scores
- Entity resolution and deduplication pipeline
- Relationship extraction from etymology text
- Validation pipeline

#### API
- REST API with FastAPI framework
- OpenAPI/Swagger documentation at `/docs`
- ReDoc alternative documentation at `/redoc`
- Health check endpoint with database status
- Prometheus-compatible metrics endpoint
- Rate limiting with configurable thresholds
- API key authentication support

#### Storage Layer
- Neo4j integration for graph-based lexical relationships
- PostgreSQL for relational metadata
- Elasticsearch for full-text search capabilities
- Redis for caching and rate limiting

#### Infrastructure
- Docker Compose multi-service orchestration
- Environment-specific configurations (development, production)

#### Testing
- Unit, integration, regression, performance, and security test suites

#### Build & Deployment
- Makefile with comprehensive build targets
- Cross-platform build scripts (Unix shell, Windows batch)
- GitHub Actions CI/CD pipeline
- Docker image building
- Wheel and sdist package generation
- Dependabot configuration for dependency updates

#### Documentation
- README with quick start guide
- API reference documentation
- Architecture overview with Mermaid diagrams
- Data model reference
- FAQ and troubleshooting guides
- Contributing guidelines
- Code style guide

#### Monitoring & Observability
- Structured logging with configurable levels
- Optional Sentry error tracking
- Request/response timing metrics

#### Security
- Input validation and sanitization
- Environment-variable-based configuration with masked logging
- API key authentication
- Rate limiting protection

### Technical Details

#### Dependencies
- Python 3.11+
- FastAPI for REST API
- Strawberry GraphQL for the GraphQL endpoint
- Neo4j Python driver for graph database
- SQLAlchemy + Alembic for PostgreSQL models and migrations
- Elasticsearch-py for search
- Pydantic for data validation
- NumPy for numerical operations

#### Development Tools
- Ruff for linting and formatting
- Black for formatting
- Mypy for static type checking
- Bandit for security scanning
- Pytest for testing
- Coverage.py for code coverage

## Types of Changes

- `Added` for new features
- `Changed` for changes in existing functionality
- `Deprecated` for soon-to-be removed features
- `Removed` for now removed features
- `Fixed` for any bug fixes
- `Security` for vulnerability fixes

[Unreleased]: https://github.com/kase1111-hash/Lexicon/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/kase1111-hash/Lexicon/releases/tag/v0.1.0
