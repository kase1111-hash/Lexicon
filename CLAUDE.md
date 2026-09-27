# CLAUDE.md - Lexicon Project Guide

## What this project is

**Lexicon** dates texts by their words. It ingests open lexical datasets into
a Neo4j graph of word records (LSRs) with first-attestation dates and
borrowing/ancestry links, then answers: when could this text have been
written (terminus post quem from its newest word), which words are
anachronistic for a claimed date, and which languages did a language borrow
from and when (contact events). CLI (`lexicon`), REST (`/api/v1`) and
GraphQL (`/graphql`) expose the same analyses.

Version 0.1.0 (alpha), MIT license.

## Tech stack

Python 3.11, FastAPI, Strawberry GraphQL, Pydantic 2. Neo4j 5 is the only
required store. Elasticsearch (fuzzy search), Redis (cache, job state, rate
limits) and PostgreSQL (reserved, unused; opt-in compose profile) are
optional, and the API and CLI connect to them only when configured
(`ELASTICSEARCH_URI`/`ELASTICSEARCH_PASSWORD`, `REDIS_URI`/`REDIS_PASSWORD`,
`POSTGRES_URI`). Docker Compose for local services.

## Essential commands

```bash
make install-dev        # dependencies + pre-commit hooks
docker compose up -d neo4j
lexicon ingest --source wold --language English   # real, dated data (~15 s)
lexicon stats
lexicon analyze anachronisms --date 1300 --text "The knight spoke on the telephone"
make run-api            # API with auto-reload on :8000

make test               # no databases needed; DB tests skip
make test-db            # DB-backed tests against TEST_NEO4J_URI (never your own graph)
make lint type-check format-check
```

## Project structure

```
src/
├── adapters/        # Sources: wold (clld.py), wiktionary, clics (IDS/CLDF), corpus
├── ingestion.py     # Ingestion driver: adapters -> validation -> resolution -> links -> graph
├── pipelines/       # entity_resolution, validation, relationship_extraction,
│                    # embedding (hashed n-gram vectors), graph_writer (Neo4j/ES upserts)
├── analysis/        # data_access (the only Neo4j access for analyses), dating,
│                    # contact_detection, semantic_drift
├── repositories/    # LSRRepository: Neo4j CRUD, search, schema, ES indexing
├── api/             # FastAPI app, middleware, routes/, graphql/, jobs.py
├── cli.py           # `lexicon` command
├── models/          # LSR and relationship models
├── utils/           # db (connections, .env-aware), cache, languages, text (word
│                    # boundaries), validation, logging
├── config.py        # Settings (reads .env via ENV_FILE)
└── exceptions.py    # LexiconError hierarchy with HTTP status codes
```

## Conventions

- Line length 100; type hints on public functions; Google-style docstrings.
- Graph access goes through `LSRRepository` or `src/analysis/data_access.py`;
  don't scatter Cypher in routes.
- `date_start` = first attestation; `date_end` = last attestation or null
  (still in use). Never copy one into the other.
- Analyses must report coverage (over distinct content words) and return
  `insufficient_data` rather than a confident verdict when evidence is
  missing. Database failures surface as `DatabaseError` (HTTP 503), never as
  empty results or 500s.
- Dated evidence carries `date_label`, the LSR's `period_label` (the date as
  the source states it: `c. 1220`, `earliest in corpus: <title>`). Compute
  with the year; show the label.
- Language codes: every user-supplied code (REST, GraphQL, CLI) must go
  through `normalize_language_code` in `src/utils/validation.py`: ISO 639-3,
  ISO 639-1 mapped (`en` -> `eng`, from `ISO_639_1_TO_3` in
  `src/utils/languages.py`), Wiktionary extensions (`gem-pro`, `la-vul`) and
  Glottocodes (`yaku1245`); names and region tags are rejected. REST
  `language` parameters take up to 20 characters. `lexicon ingest
  --language` is different: adapters match it with `language_filter_keys`,
  so it takes names (`English`) as well as ISO 639-3/639-1 codes (WOLD and
  CLICS also Glottocodes). `src/utils/languages.py` maps source language
  names to codes for ingestion; never derive a code from a name prefix.
  Arabic is `ara` everywhere; a linked language with a name but no code
  (WOLD donors such as `Proto-Altaic`) is `und`, which never forms a contact
  event. WOLD's `Unidentified` donors get no link at all.
- Years are integers from -10000 to 2100 (`YEAR_MIN`/`YEAR_MAX` in
  `src/models/lsr.py`; `src/utils/validation.py` repeats them). REST, GraphQL
  and the LSR model use the same bounds; ingestion drops a year outside them
  with a warning (`convert_entry_to_lsr` in
  `src/pipelines/entity_resolution.py`). An LSR ingested without a date has
  `date_confidence` 0.0.
- Word boundaries come from `word_spans` in `src/utils/text.py` (letters plus
  their combining marks, any script), shared by the analyses and the corpus
  adapter; don't add another word regex.
- Placeholder LSRs (donors and ancestors known only by a link) are written
  fill-only (`create_batch(..., fill_only=True)`): they never blank a
  property another run stored.
- Repository queries that serve requests (reads, searches, writes) go
  through `LSRRepository._run` / `_read` (analyses: `data_access._fetch`):
  a 15 s server-side timeout and a client deadline 2 s later, so a stalled
  Neo4j gives 503 `DATABASE_ERROR` instead of hanging.
- Logs go to stderr; stdout is for command output (`lexicon ... --json`).
  `search`, `analyze`, `stats` and `reindex` exit 2 with `Error: …` on
  stderr, and print no partial result, when Neo4j is unreachable or fails;
  `ingest` exits 2 when Neo4j is unreachable and 1 when part of the write
  fails.
- Tests: `tests/conftest.py` isolates tests from your `.env` and databases;
  DB-backed tests need `TEST_NEO4J_URI`/`TEST_NEO4J_PASSWORD`. Assert
  behaviour (verdicts, dates, edges), not just response shape.

## Key entry points

| File | Purpose |
|---|---|
| `src/ingestion.py` | Ingestion runs for every source; `--dry-run` skips only the graph write |
| `src/pipelines/graph_writer.py` | Idempotent Neo4j (and Elasticsearch) writes |
| `src/adapters/clld.py` | WOLD: dates from `Age`, donors from `borrowings.csv` |
| `src/analysis/data_access.py` | Tokenization, inflection fallback, graph loaders for analyses |
| `src/analysis/dating.py` | Date estimation and anachronism detection |
| `src/repositories/lsr_repository.py` | LSR CRUD, search (Elasticsearch, Neo4j fallback), breadth-first lineage traversals |
| `src/api/main.py` | FastAPI app, lifespan, `/health` |
| `src/cli.py` | `lexicon` command |

## Known gaps

- Entity resolution works within one ingestion run and only merges entries
  with the same normalized form and language; the same word from two
  sources, or a placeholder donor and the same word ingested later, can
  become two LSRs.
- Dating coverage depends on dated vocabulary: WOLD dates ~1,500 core
  English words; other languages mostly lack dates.
- Semantic drift (experimental) needs senses of a word with a date and a
  definition, first attested in at least two different years, which current
  sources rarely provide; vectors are lexical (hashed n-grams), not a
  trained model.
- Analysis reads in `src/analysis/data_access.py` set the 15 s server-side
  timeout but, unlike repository queries, no client deadline;
  `LSRRepository.ensure_schema` and `get_statistics` (`lexicon stats`) set
  neither.
- `POST /graph/query` caps the rows it returns, not what it must receive: a
  single huge value is read whole. `GRAPH_QUERY_ENABLED=false` turns the
  endpoint off (403 `QUERY_DISABLED`) where untrusted clients hold API keys.
- Attestations are not persisted (only the earliest year and its label
  survive).
- The top-level import package is named `src`; rename before publishing.

## Documentation

README.md (start here), docs/architecture.md, docs/data_model.md,
docs/api-reference.md, docs/faq.md, docs/troubleshooting.md, CONTRIBUTING.md.
docs/archive/ holds historical plans and the original (much larger) design
spec; do not treat it as a description of the current system.
