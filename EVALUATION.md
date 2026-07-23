# PROJECT EVALUATION REPORT

> **HISTORICAL SNAPSHOT — pre-refocus.** This evaluation describes the
> codebase as it stood before the refocus (see `REFOCUS_PLAN.md`) was
> executed. Much of what it critiques no longer exists: `src/training/`,
> `src/adapters/ocr.py`, `k8s/`, `dags/`, Milvus, Airflow, and the
> cloud-secrets-manager config were all deleted, and the stub modules it
> flags (CLLD adapter, corpus adapter, GraphQL, embeddings, phonetics)
> have since been implemented. See "Resolution Status" at the end of this
> document for the current state. Kept for historical context.

**Project:** Lexicon (Computational Linguistic Stratigraphy)
**Primary Classification (at time of evaluation):** Underdeveloped
**Secondary Tags:** Good Concept, Over-Engineered Infrastructure

---

## CONCEPT ASSESSMENT

**Problem solved:** Researchers in historical linguistics lack a unified, queryable graph connecting lexical evolution data (etymologies, borrowings, cognates, semantic drift) across languages and time periods. Current resources (Wiktionary, CLLD databases, historical corpora) exist in silos with no programmatic cross-referencing.

**User:** Historical linguists, computational linguists, and digital humanities researchers who need to trace word evolution, detect language contact events, date texts by vocabulary, and flag anachronistic word usage in historical documents.

**Is the pain real?** Yes. This is a genuine gap. Wiktionary is unstructured; CLLD datasets are isolated; no tool unifies these into a traversable temporal graph. But the user base is extremely niche -- a few thousand researchers worldwide at most.

**Competition:** Partially overlapping tools exist (Etymonline, Wiktionary, CLLD/CLICS, HistWords embeddings, LinguistList), but nothing integrates all of these into a single queryable API with graph traversal, text dating, and contact detection. The concept has novelty.

**Value prop in one sentence:** A unified knowledge graph and API for tracing how words evolve across languages and centuries, enabling automated text dating, forgery detection, and language contact analysis.

**Verdict:** Sound -- the concept addresses a real gap in computational historical linguistics. The risk is not concept viability but market size and execution ambition mismatch. This is a legitimate research-infrastructure project, not a consumer product.

---

## EXECUTION ASSESSMENT

### Architecture

The architecture is **dramatically over-scoped** for the current state of implementation. The project prescribes 5 databases (Neo4j, PostgreSQL, Elasticsearch, Milvus, Redis), Apache Airflow orchestration, Kubernetes manifests, and a multi-service Docker Compose with 9 containers -- all for a codebase where most of the core logic is still TODO stubs.

**Specific findings:**

- **`src/training/embeddings.py` (38 lines):** 100% TODO stubs. The "diachronic embedding trainer" has zero implementation -- every method returns `pass` or `{"status": "not_implemented"}`.
- **`src/training/phylogenetics.py` (49 lines):** Same pattern. BEAST2/MrBayes integration is pure scaffolding.
- **`src/training/classifiers.py` (43 lines):** Four classifiers described in comments, none implemented. All return `{"status": "not_implemented"}`.
- **`src/adapters/clld.py`, `corpus.py`, `ocr.py`:** All stub adapters. `fetch_batch()` returns `iter([])`. Only the `wiktionary.py` adapter has real implementation.
- **`dags/daily_ingestion.py`:** Airflow imports are commented out. DAGs are templates, not runnable.

### What IS Implemented (and is decent):

- **`src/models/lsr.py`** (260 lines): The core LSR model is well-designed -- proper Pydantic v2 usage, field validators, date range validation, auto-normalization, merge logic. This is the strongest code in the project.
- **`src/analysis/dating.py`** (398 lines): Text dating and anachronism detection are fully implemented with reasonable algorithms (vocabulary attestation overlap, confidence scoring). Not production-grade ML, but functional.
- **`src/analysis/contact_detection.py`** (652 lines): Contact detection with clustering, domain classification, confidence scoring. Complete and testable.
- **`src/analysis/semantic_drift.py`** (595 lines): Semantic trajectory tracking, shift detection, cross-language comparison. Complete with proper math (cosine distance, entropy).
- **`src/pipelines/entity_resolution.py`** (327 lines): Multi-strategy candidate retrieval (exact, fuzzy, phonetic), weighted similarity scoring, configurable thresholds. Functional.
- **`src/repositories/lsr_repository.py`** (364 lines): Clean Neo4j Cypher queries, parameterized (no injection), proper error handling.
- **`src/api/main.py`** (463 lines): Well-structured FastAPI app with comprehensive exception handlers, middleware stack, health checks, metrics.
- **`src/adapters/wiktionary.py`** (436 lines): Working Wiktionary API client with rate limiting, wikitext parsing, language section extraction. This is the only adapter that actually works.

### Code Quality

- **Type safety:** Strict mypy config (excluding tests), Pydantic v2 models with validators. Good.
- **Error handling:** Custom exception hierarchy with HTTP status mapping (`src/exceptions.py`, 11KB). Thorough but verbose -- 11 specific exception types for a v0.1.0 is premature.
- **Configuration:** `src/config.py` (14KB) supports AWS Secrets Manager, HashiCorp Vault, and GCP Secret Manager for secret injection. For a project that can't yet train an embedding or ingest from CLLD, this is pure over-engineering.
- **Tests:** 31 test files across 7 categories (unit, integration, acceptance, regression, performance, security). The tests themselves are shallow -- `test_models.py` has 4 trivial creation tests. The volume of test infrastructure significantly outweighs the depth of actual assertions.
- **Documentation:** 25+ markdown files including a 1729-line SPEC.md. The spec describes a system that is maybe 25% built.

### Tech Stack Appropriateness

- **Neo4j** for the etymology graph: Appropriate. Graph queries (etymology chains, cognate networks) are the natural use case.
- **PostgreSQL** for metadata/jobs: Reasonable, but adds operational burden. Could use Neo4j or SQLite for the current scope.
- **Elasticsearch** for fuzzy text search: Premature. The `search()` method in `lsr_repository.py` uses Neo4j CONTAINS, not Elasticsearch. ES is declared but unused.
- **Milvus** for vector similarity: Premature. No embeddings are being generated (training module is stubs). Vector search is aspirational.
- **Redis** for caching: Premature at current data scale.
- **Apache Airflow** for orchestration: Dramatically premature. DAGs are commented-out templates.
- **Kubernetes manifests:** For a project where 3 of 4 adapters return empty iterators, K8s is pure resume-driven development.
- **45+ dependencies:** Including PyTorch, Transformers, XGBoost, spaCy, Stanza, sentence-transformers -- none of which are actually used in any functional code path.

**Verdict:** Good Concept, execution ambition far exceeds actual implementation. The project has built the infrastructure for a 50-person team but implemented the logic of a single-developer prototype. The ratio of scaffolding to working code is roughly 3:1. The parts that ARE implemented (analysis modules, LSR model, Wiktionary adapter, entity resolution) are competently written. But they're buried under mountains of TODO stubs, unused dependencies, and infrastructure for a scale the project hasn't earned.

---

## SCOPE ANALYSIS

**Core Feature:** The cross-linguistic lexical evolution graph -- ingesting etymological data, building a temporal knowledge graph, and querying relationships between words across languages and time.

**Supporting:**
- Wiktionary adapter (implemented) -- primary data source
- LSR data model (implemented) -- the atomic unit
- Entity resolution pipeline (implemented) -- deduplication
- Neo4j repository layer (implemented) -- graph persistence
- REST API for CRUD and search (implemented)

**Nice-to-Have:**
- Text dating analysis (implemented, but secondary to core graph)
- Contact detection analysis (implemented)
- Semantic drift tracking (implemented)
- GraphQL endpoint (partially implemented)
- Redis caching layer

**Distractions:**
- Kubernetes manifests (`k8s/`) -- not needed until there's data to serve
- Production Docker Compose with 9 services -- development should use 2-3
- Apache Airflow DAGs -- a cron job would suffice at current scale
- AWS/Vault/GCP secrets manager integration -- environment variables are fine for now
- Prometheus metrics and Sentry integration -- meaningful only with real traffic
- 31 test files across 7 categories for a v0.1 -- test depth doesn't match test breadth
- Pre-commit hooks with 5 tools -- appropriate for a mature project, overkill here

**Wrong Product:**
- **ML training pipelines** (`src/training/`): Diachronic embedding training, phylogenetic inference, and classifier training are each research projects unto themselves. They should be separate repositories with their own data pipelines, experiment tracking, and evaluation benchmarks. Bundling them as stub modules in the API service conflates inference serving with model training.
- **OCR adapter** (`src/adapters/ocr.py`): OCR text extraction from historical manuscripts is a separate engineering challenge (layout analysis, historical font recognition, confidence calibration) that should not live in a data ingestion adapter with 48 lines of stubs.

**Scope Verdict:** Feature Creep + Multiple Products. The SPEC.md describes 5+ distinct systems (data ingestion platform, graph database service, ML training framework, analysis toolkit, OCR pipeline) masquerading as one project. The implemented portions are focused and coherent, but the scaffolding reveals plans to build everything at once.

---

## RECOMMENDATIONS

### CUT

- **`k8s/` directory** -- Delete entirely. Deploy on a single VM or use Docker Compose when the project actually has users.
- **`dags/` directory** -- Delete the Airflow DAGs. Replace with a simple Python script invoked by cron. When data volume justifies orchestration, reintroduce it.
- **`src/training/` module** -- Delete the entire directory. These are 130 lines of TODO stubs adding zero value. When you're ready to train models, create a separate `lexicon-ml` repository.
- **`src/adapters/ocr.py`** -- Delete. OCR is a separate project.
- **Milvus, etcd, minio** from `docker-compose.yml` -- Remove vector DB infrastructure until embeddings actually exist.
- **Airflow** from `docker-compose.yml` -- Remove until workflow orchestration is needed.
- **Unused dependencies** from `requirements.txt` -- Remove `torch`, `transformers`, `sentence-transformers`, `xgboost`, `scikit-learn`, `stanza`, `spacy`, `apache-airflow`, `pymilvus`. None are imported in functional code paths. This cuts install size by gigabytes.
- **Secrets manager integration** in `config.py` -- Simplify to environment variables only. Reintroduce when deploying to a cloud provider.

### DEFER

- **CLLD adapter** (`src/adapters/clld.py`) -- Good data source, but implement after Wiktionary pipeline is proven end-to-end.
- **Corpus adapter** (`src/adapters/corpus.py`) -- Defer until graph has sufficient vocabulary coverage to make corpus analysis meaningful.
- **GraphQL endpoint** -- REST API is sufficient for v1. Add GraphQL when you have frontend consumers requesting it.
- **Elasticsearch integration** -- Neo4j full-text search is adequate for the current scale. Integrate ES when query latency on the graph becomes a bottleneck.
- **Performance and stress tests** (`tests/performance/`) -- Meaningful only with real data. Defer until the graph has >100K nodes.

### DOUBLE DOWN

- **Wiktionary ingestion pipeline** -- This is the critical path. Make `WiktionaryAdapter` -> `EntityResolver` -> `LSRRepository` work end-to-end with real data. Run it on 10,000 English words and validate the graph.
- **Core graph queries** -- Build and test the etymology chain traversal, cognate discovery, and borrowing path queries on real data. The repository layer (`lsr_repository.py`) needs these graph traversal methods tested against a populated Neo4j instance.
- **Analysis modules with real data** -- The dating, contact detection, and semantic drift code is implemented but untested against real LSR data. Wire them to the graph and validate with known historical linguistics examples (e.g., Norman French borrowings into English, Latin borrowings into Germanic languages).
- **Integration testing** -- Write tests that run the full pipeline: fetch from Wiktionary -> resolve entities -> store in Neo4j -> query via API. This is the gap between "modules that compile" and "a system that works."

### FINAL VERDICT: Refocus

This project has a sound concept and competent code in the parts that are actually implemented. But it's drowning in aspirational infrastructure. The SPEC.md describes a cathedral; the codebase has built the scaffolding for a cathedral but only laid the foundation of a chapel.

**The path forward is subtraction, not addition.** Strip out everything that doesn't serve the core loop: ingest data -> build graph -> query graph -> analyze. Get that loop working end-to-end with real data before adding a single new feature, dependency, or infrastructure component.

**Next Step:** Delete `src/training/`, `k8s/`, `dags/`, and the unused adapters. Trim `requirements.txt` to actual imports. Then run the Wiktionary adapter against 1,000 English words and store the results in Neo4j. That single milestone will teach you more about what this project actually needs than 1,729 lines of specification ever could.

---

## RESOLUTION STATUS (2026-07)

The refocus recommended above was executed (`REFOCUS_PLAN.md`), and the
gaps this evaluation identified have since been closed:

**CUT (done):** `src/training/`, `src/adapters/ocr.py`, `k8s/`, `dags/`,
Milvus/etcd/minio, Airflow, cloud secrets-manager config, and the unused
ML dependencies (torch, transformers, xgboost, spaCy, Stanza, etc.) were
all removed. The stack is now four stores (Neo4j, PostgreSQL,
Elasticsearch, Redis) behind one FastAPI service.

**DOUBLE DOWN (done):** The core loop works end-to-end — Wiktionary and
WOLD ingestion drive `EntityResolver` → validation → relationship
extraction → `LSRRepository`, exercised by integration tests. The
Elasticsearch path in `search()` is implemented with Neo4j fallback.

**DEFERRED items (since implemented):** CLLD/WOLD adapter, CLICS
colexification adapter, historical corpus adapter, GraphQL endpoint
(mounted at `/graphql` with real resolvers), embedding generation
(deterministic hashed n-gram encoder feeding semantic drift), and
phonetic matching (Soundex/Metaphone/IPA distance) in entity resolution.

The test suite currently stands at 650+ passing tests with ruff, black,
mypy, and bandit clean.
