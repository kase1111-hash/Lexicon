# REFOCUS PLAN

This plan reorganizes the Lexicon project around one goal: **get the core loop working end-to-end with real data before adding anything new.** It is structured in 4 phases. Each phase has a concrete exit milestone. Do not start the next phase until the current one's milestone is met.

---

## Phase 0: Cut Dead Weight

**Goal:** Remove everything that doesn't serve the core loop. Reduce cognitive overhead, install time, and maintenance surface.

**Duration target:** 1-2 days

### 0.1 Delete dead modules

| Path | Reason |
|------|--------|
| `src/training/` (entire directory) | 130 lines of TODO stubs. Zero functional code. Embeddings, phylogenetics, and classifiers are each separate research projects. |
| `src/adapters/ocr.py` | 48-line stub. OCR is a separate engineering domain. |
| `k8s/` (entire directory) | Kubernetes manifests for a project with no users and no data. |
| `dags/` (entire directory) | Airflow DAGs with commented-out imports. A cron job will suffice. |

### 0.2 Gut unused infrastructure from Docker Compose

Remove these services from `docker-compose.yml`:
- **milvus** -- no embeddings exist to search
- **etcd** -- milvus dependency
- **minio** -- milvus dependency
- **airflow** -- DAGs are deleted above

After cleanup, `docker-compose.yml` should contain only:
- `neo4j` (core graph)
- `postgres` (metadata, audit, ingestion tracking)
- `redis` (optional -- keep for caching but make it non-required)
- `api` (the FastAPI service)

Remove the associated volumes: `milvus_data`, `etcd_data`, `minio_data`, `airflow_logs`.

### 0.3 Trim `requirements.txt` to actual imports

**Remove** (none are imported in any functional code path):
- `torch>=2.0`
- `transformers>=4.30`
- `sentence-transformers>=2.2`
- `xgboost>=1.7`
- `scikit-learn>=1.3`
- `stanza>=1.5`
- `spacy>=3.6`
- `apache-airflow>=2.7`

**Keep** (actually imported or used in try/except fallbacks):
- `pydantic>=2.0`, `pydantic-settings>=2.0`
- `httpx>=0.24`, `aiohttp>=3.8`
- `neo4j>=5.0`, `asyncpg>=0.28`, `elasticsearch>=8.0`, `redis>=4.0`
- `pymilvus>=2.3` -- move to an `extras_require` or comment out (only in try/except)
- `fastapi>=0.100`, `uvicorn>=0.23`, `strawberry-graphql>=0.200`
- `sentry-sdk[fastapi]>=1.29`
- `pywikibot>=8.0`, `lxml>=4.9`, `tqdm>=4.65`, `python-Levenshtein>=0.21`
- `numpy>=1.24` (used by phonetics/embeddings utils)

This cuts install size from ~8GB+ (PyTorch alone is 2GB) to under 500MB.

### 0.4 Simplify `config.py`

Remove the `SecretsManagerConfig` class and all AWS/Vault/GCP secret-fetching logic. Replace with plain environment variable loading. The class can be re-added in a future PR when cloud deployment is actually happening.

### 0.5 Clean up dead references

After deletions, grep for broken imports and references:
- Remove any imports of `src.training.*`
- Remove any imports of `src.adapters.ocr`
- Remove any references to Airflow or DAGs in Makefile targets
- Update `src/adapters/__init__.py` if it exports the deleted adapter

### Exit Milestone
`make lint && make type-check` passes. `docker compose config` validates with only 4 services. `pip install -r requirements.txt` completes in under 2 minutes. Project has 0 references to deleted modules.

---

## Phase 1: Wire the Core Loop

**Goal:** Ingest real data from Wiktionary, resolve entities, store LSRs in Neo4j, and serve them via the API. One working pipeline, end to end.

**Duration target:** 1-2 weeks

### 1.1 Create a simple ingestion script

Replace the deleted Airflow DAGs with a single Python script at `scripts/ingest.py`:

```
WiktionaryAdapter  ->  EntityResolver  ->  LSRRepository.create()
     (fetch)            (deduplicate)         (persist to Neo4j)
```

The script should:
1. Accept a word list file or language parameter
2. Use `WiktionaryAdapter` to fetch entries
3. Run each through `EntityResolver` (with an initially empty store)
4. Convert resolved entries to LSR via `convert_entry_to_lsr()`
5. Persist via `LSRRepository.create()`
6. Log progress and error counts
7. Be runnable as: `python scripts/ingest.py --words wordlist.txt --language eng`

### 1.2 Create a seed word list

Create `data/seed_words_eng.txt` with ~1,000 English words:
- Swadesh 207-word list (core vocabulary)
- 200 words with known Norman French etymologies (to test borrowing detection)
- 200 words with known Latin/Greek etymologies
- 200 words with well-documented semantic drift (e.g., "nice", "awful", "silly")
- 200 high-frequency words from diverse domains

### 1.3 Wire analysis routes to real analysis modules

The analysis modules (`dating.py`, `contact_detection.py`, `semantic_drift.py`) are fully implemented but the API routes (`src/api/routes/analysis.py`) return mock data. Wire them:

- `POST /analyze/date-text` -> `TextDating.date_text()` with LSR data loaded from Neo4j
- `POST /analyze/detect-anachronisms` -> `TextDating.detect_anachronisms()` with LSR data from Neo4j
- `GET /analyze/contact-events` -> `ContactDetector.detect_contacts()` with borrowing data from Neo4j
- `GET /analyze/semantic-drift` -> `SemanticDriftAnalyzer.get_trajectory()` with LSR data from Neo4j

Each route needs a helper that queries Neo4j for the relevant LSR data and converts it to the format the analysis classes expect.

### 1.4 Wire LSR graph traversal routes

The stubs in `src/api/routes/lsr.py` for etymology, descendants, cognates, and borrowings should delegate to the already-implemented graph queries in `src/api/routes/graph.py`:

- `GET /lsr/{id}/etymology` -> Reuse the Cypher from `graph.py`'s `get_etymology_chain()`
- `GET /lsr/{id}/descendants` -> Reverse of etymology query
- `GET /lsr/{id}/cognates` -> Reuse `get_cognates()` from `graph.py`
- `GET /lsr/{id}/borrowings` -> Query BORROWED_FROM relationships

### 1.5 Run the first real ingestion

Execute `scripts/ingest.py` against the seed word list. Record:
- How many words were fetched successfully
- How many LSRs were created
- Entity resolution statistics (merges, new creations, flags)
- Errors and failure modes

Fix every bug this surfaces. This is the milestone that matters.

### 1.6 Write integration tests against real data

After ingestion, write tests that:
1. Verify LSR count in Neo4j matches expected
2. Query a known word (e.g., "water") and assert language_code, etymology data
3. Traverse an etymology chain (e.g., English "water" -> Old English "wæter" -> Proto-Germanic "*watōr")
4. Run text dating on a known Middle English passage and verify the predicted range includes 1200-1500
5. Run anachronism detection on a text with a planted modern word and verify it's flagged

### Exit Milestone
1,000+ LSRs in Neo4j from real Wiktionary data. All 4 analysis API endpoints return real results. All 4 LSR graph traversal endpoints work. Integration tests pass against a populated database.

---

## Phase 2: Harden the Core

**Goal:** Make the working system reliable, testable, and usable by someone other than the developer.

**Duration target:** 2-3 weeks

### 2.1 Add relationship extraction

The `src/pipelines/relationship_extraction.py` is a stub, but relationships are the entire point of a graph database. Implement at minimum:
- `extract_from_etymology()` -- Parse Wiktionary etymology sections to create DESCENDS_FROM and BORROWED_FROM edges
- `detect_cognates()` -- Use form similarity + shared ancestor to create COGNATE_OF edges

This is the highest-value code the project is missing. Without edges, Neo4j is just a document store.

### 2.2 Add validation pipeline

Implement `src/pipelines/validation.py`:
- `validate_schema()` -- Ensure all LSRs have required fields (form, language_code, at least one date)
- `validate_consistency()` -- Check for date range inversions, circular ancestry
- `detect_anomalies()` -- Flag LSRs with confidence < 0.3 or no attestations

Run validation after every ingestion batch.

### 2.3 Deepen test coverage

Replace the shallow test stubs with meaningful assertions:
- **Entity resolution:** Test with known duplicates (e.g., "colour"/"color"), near-misses, and false positives
- **Wiktionary parsing:** Test against saved HTML fixtures for 20+ words covering edge cases (multi-language entries, missing etymology, reconstructed forms)
- **Analysis modules:** Test dating with known historical texts, contact detection with known borrowing events
- **Repository:** Test all CRUD operations and graph queries against a test Neo4j instance

Target: 80% branch coverage on `src/models/`, `src/analysis/`, `src/pipelines/entity_resolution.py`, `src/repositories/`

### 2.4 Improve Wiktionary adapter robustness

Based on bugs found during Phase 1 ingestion:
- Handle multi-etymology entries (some words have Etymology 1, Etymology 2, etc.)
- Handle reconstructed proto-forms (starred forms like *watōr)
- Handle non-Latin scripts gracefully
- Add retry logic for transient API failures
- Save raw wikitext to PostgreSQL `ingestion_records` for debugging

### 2.5 Add a minimal CLI

Create `src/cli.py` using `argparse` (no new dependency):
- `lexicon ingest --source wiktionary --words file.txt`
- `lexicon search --form water --language eng`
- `lexicon analyze date-text --file passage.txt`
- `lexicon stats` (LSR count, relationship count, language distribution)

This makes the project usable without running the API server.

### Exit Milestone
Graph has edges (DESCENDS_FROM, BORROWED_FROM, COGNATE_OF) connecting LSRs. Validation pipeline catches bad data. Test coverage is 80%+ on core modules. A new developer can `make install && make docker-up && python scripts/ingest.py` and have a working system in 10 minutes.

---

## Phase 3: Scale and Extend

**Goal:** Add the second data source and handle real-world data volume.

**Duration target:** 3-4 weeks

### 3.1 Implement CLLD adapter

Now that the ingestion pipeline is proven with Wiktionary, implement `src/adapters/clld.py`:
- Start with WOLD (World Loanword Database) -- directly supports borrowing analysis
- Map CLLD data format to `RawLexicalEntry`
- Run through the same EntityResolver -> Repository pipeline

### 3.2 Scale ingestion

Expand from 1K to 100K+ LSRs:
- Ingest all English Wiktionary entries
- Ingest major European languages (French, German, Spanish, Latin, Ancient Greek)
- Ingest WOLD borrowing data
- Measure and optimize: Neo4j batch insert, entity resolution index performance

### 3.3 Add Elasticsearch (now justified)

With 100K+ nodes, Neo4j CONTAINS queries will slow down. Now integrate Elasticsearch:
- Index LSRs on create/update via the repository layer
- Replace `search()` in `lsr_repository.py` to use ES for fuzzy text search
- Keep Neo4j for graph traversal, ES for discovery

### 3.4 Add Redis caching (now justified)

With real traffic patterns visible:
- Cache frequent graph queries (etymology chains for popular words)
- Cache analysis results with TTL
- Measure cache hit rates before adding complexity

### 3.5 Implement corpus adapter (optional)

If dating analysis shows promise, implement `src/adapters/corpus.py`:
- Start with Project Gutenberg (free, well-structured)
- Extract frequency data per time period
- Use to enrich `frequency_score` on existing LSRs

### Exit Milestone
100K+ LSRs from 2+ data sources. Graph has dense relationship edges. Search works at scale via Elasticsearch. The analysis endpoints produce linguistically meaningful results validated against known historical facts.

---

## What This Plan Deliberately Excludes

These are all good ideas that should not be touched until Phase 3 is complete:

| Item | Why Not Now |
|------|-------------|
| ML training (embeddings, classifiers) | Requires 100K+ labeled examples. Build data first. |
| Phylogenetic inference | Research project. Separate repository. |
| OCR adapter | Separate engineering domain. Separate repository. |
| Kubernetes deployment | No users yet. Docker Compose on a single VM. |
| Airflow orchestration | Cron + script until data volume exceeds 1M records. |
| GraphQL endpoint | REST is sufficient until a frontend demands it. |
| Prometheus/Grafana monitoring | Meaningful only with real traffic. |
| Multi-cloud secrets management | Environment variables until cloud deployment. |

---

## Decision Log

Track deviations from this plan here. If you add a feature not in the plan, write down why.

| Date | Decision | Rationale |
|------|----------|-----------|
| | | |
