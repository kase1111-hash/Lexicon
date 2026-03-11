# Agentic Security Audit — Linguistic Stratigraphy (Lexicon)

## AUDIT METADATA

```
Project:       Linguistic Stratigraphy (Lexicon)
Date:          2026-03-11
Auditor:       claude-opus-4-6
Commit:        864cacce169e5ba9e206ad47b8f99f839a7317ae
Strictness:    STANDARD
Context:       PROTOTYPE (v0.1.0 Alpha — no evidence of production traffic)
```

## PROVENANCE ASSESSMENT

```
Vibe-Code Confidence:   75%
Human Review Evidence:   MINIMAL
```

### Indicators Detected

**Vibe-code signals present:**

- **Rapid commit history**: 10+ substantial commits on 2026-01-27 alone (auth middleware, Redis caching, graph endpoints, analysis module, migrations, security config, integration tests, resource limits — all in a single day). A second burst on 2026-02-09 produced 4 "Phase" commits restructuring the entire codebase. This pattern is consistent with AI-assisted bulk generation, not iterative human development.
- **AI boilerplate**: Uniform docstring formatting across all modules, consistent comment style, tutorial-grade code structure. TODO comments in `resolvers.py` read like prompts (`# TODO: Implement ancestor resolution`).
- **Polished README, hollow internals**: Comprehensive `README.md`, `SPEC.md`, `CONTRIBUTING.md`, `EVALUATION.md`, API reference docs, Postman collection, and detailed architecture docs — but the GraphQL resolvers (`src/api/graphql/resolvers.py`) are entirely stub implementations returning empty lists.
- **Bloated infrastructure**: 4 production databases (Neo4j, PostgreSQL, Elasticsearch, Redis), Sentry integration, Prometheus metrics, OpenTelemetry tracing, structured logging with component-level config — for a v0.1.0 alpha with no real data or users.

**Partial mitigating factors:**

- `.gitignore` properly excludes `.env` and secret files
- `.env.example` exists with placeholder values (no real secrets)
- Pre-commit hooks configured with bandit security scanning
- CI/CD pipeline includes security scan job
- Security-specific test suite exists (`tests/security/`)
- `detect-private-key` pre-commit hook is present

---

## LAYER VERDICTS

```
L1 Provenance:       WARN   — Strong vibe-code signals; minimal human security review evidence
L2 Credentials:      WARN   — Good patterns in place but defaults are insecure
L3 Agent Boundaries: N/A    — No agentic features in the application itself
L4 Supply Chain:     WARN   — Unpinned dependencies, no lock file
L5 Infrastructure:   WARN   — Cypher injection vector, CORS wildcard default, exposed monitoring
```

---

## FINDINGS

### [HIGH] — Cypher Injection via `create_relationships_batch`

```
Layer:     5
Location:  src/repositories/lsr_repository.py:382
Evidence:  Relationship type is interpolated directly into Cypher query via f-string:
             query = f"""
             UNWIND $batch AS rel
             MATCH (source:LSR {{id: rel.source_id}})
             MATCH (target:LSR {{id: rel.target_id}})
             MERGE (source)-[r:{rel_type}]->(target)
             """
           `rel_type` comes from `rel.get("type", "RELATED_TO")` — user/caller-supplied data.
Risk:      An attacker passing a crafted relationship type like
           `RELATED_TO]->(target) DETACH DELETE target WITH source MERGE (source)-[r:X`
           could execute arbitrary Cypher. This bypasses the GraphQueryRequest blocklist
           because it enters through the repository layer, not the graph query endpoint.
Fix:       Validate `rel_type` against an allowlist of known relationship types
           (DESCENDS_FROM, BORROWED_FROM, COGNATE_OF, SHIFTED_TO, MERGED_WITH)
           before string interpolation — identical to the pattern used in
           `src/api/routes/graph.py:114` for path queries.
```

### [HIGH] — Open Cypher Query Endpoint with Weak Blocklist

```
Layer:     5
Location:  src/api/routes/graph.py:34-92, src/utils/validation.py:369-378
Evidence:  The POST /api/v1/graph/query endpoint accepts arbitrary Cypher queries.
           The blocklist only checks for: DETACH DELETE, DROP, CREATE INDEX, CREATE CONSTRAINT.
           Missing: DELETE (without DETACH), SET, REMOVE, MERGE (for data modification),
           CREATE (node creation), CALL (for procedure execution including apoc.load.json,
           apoc.cypher.run, dbms.security.*).
           APOC plugin is enabled in docker-compose.yml — APOC procedures can read the
           filesystem, make HTTP calls, and execute dynamic Cypher.
Risk:      Authenticated users can read all data, modify data, call APOC procedures
           to access the filesystem or make outbound HTTP requests from the Neo4j container.
Fix:       Either: (a) Remove the open query endpoint entirely and only expose
           purpose-built parameterized endpoints. Or (b) Use a proper Cypher parser/AST
           validation, enforce read-only transactions, and disable dangerous APOC procedures.
```

### [MEDIUM] — CORS Wildcard Default

```
Layer:     5
Location:  src/config.py:84
Evidence:  `cors_origins: str = "*"` — default allows all origins.
           Production validation in `validate_required_for_production()` catches this,
           but only when ENVIRONMENT=production. Staging and development deployments
           that handle real data are unprotected.
Risk:      Cross-origin requests from any website can interact with the API using
           credentials if `allow_credentials=True` (which it is, line 85).
           Note: browsers block `Access-Control-Allow-Credentials: true` with
           `Access-Control-Allow-Origin: *`, so the combination is contradictory —
           but FastAPI/Starlette CORS middleware resolves this by reflecting the
           requesting origin, effectively making it a per-origin wildcard.
Fix:       Default `cors_origins` to an empty string or `http://localhost:3000`.
           Enforce explicit CORS configuration for any non-local environment.
```

### [MEDIUM] — Authentication Disabled by Default

```
Layer:     2
Location:  src/config.py:77, src/api/main.py:189-194
Evidence:  `api_key: SecretStr | None = None` — when no API_KEY is set, the auth
           middleware is completely disabled (`enabled=api_key is not None`).
           The .env.example shows `API_KEY=` (empty), reinforcing the no-auth default.
Risk:      Every deployment that doesn't explicitly set API_KEY runs with zero
           authentication on all endpoints including data mutation and graph queries.
Fix:       Log a prominent warning at startup when auth is disabled.
           For production validation, require API_KEY to be set.
           Consider making auth required by default and requiring an explicit
           `AUTH_DISABLED=true` flag to bypass.
```

### [MEDIUM] — Unpinned Dependencies

```
Layer:     4
Location:  requirements.txt
Evidence:  All dependencies use minimum-version pins (>=) with no upper bounds:
           `fastapi>=0.100`, `neo4j>=5.0`, `redis>=4.0`, etc.
           No `requirements.lock`, `poetry.lock`, or `pip-compile` output file exists.
           No `pip-audit` or `safety` in CI pipeline.
Risk:      Builds are non-reproducible. A compromised or buggy new release of any
           dependency will silently be pulled into the next install.
           No automated vulnerability scanning of installed packages.
Fix:       Pin exact versions in a lock file (use `pip-compile` or switch to Poetry).
           Add `pip-audit` to the CI security job.
```

### [MEDIUM] — Monitoring Endpoints Publicly Accessible

```
Layer:     5
Location:  src/api/middleware.py:18-26, src/api/main.py:408-446
Evidence:  PUBLIC_PATHS includes `/metrics` — bypasses authentication.
           `/metrics/json` and `/traces` are not in PUBLIC_PATHS but the auth middleware
           checks exact path matches; since these routes exist under different paths,
           they DO require auth when auth is enabled. However, `/metrics` exposes
           Prometheus-format operational data without authentication.
           `/traces` endpoint exposes recent execution spans including query details.
Risk:      Operational metrics reveal request patterns, error rates, database status,
           and potentially sensitive query information to unauthenticated users.
Fix:       Remove `/metrics` from PUBLIC_PATHS. Require authentication for all
           monitoring endpoints, or restrict access by IP/network.
```

### [MEDIUM] — Database Ports Exposed to Host Network

```
Layer:     5
Location:  docker-compose.yml:13-14, 43-44, 76-77, 101-102
Evidence:  All database ports are mapped to the host:
           - Neo4j: 7474 (HTTP), 7687 (Bolt)
           - PostgreSQL: 5432
           - Elasticsearch: 9200, 9300
           - Redis: 6379
Risk:      If deployed on a server with a public IP, all databases are directly
           accessible from the internet. Neo4j's HTTP interface (7474) provides
           a browser-based query console.
Fix:       Bind database ports to localhost only (`127.0.0.1:7474:7474`) in
           docker-compose.yml. Use docker-compose.override.yml for development
           if broader access is needed locally.
```

### [LOW] — Error Messages Leak Internal Details

```
Layer:     5
Location:  src/api/main.py:349-356
Evidence:  The global exception handler returns `{"type": type(exc).__name__}` in
           the response. Database error handlers return `exc.to_dict()` which may
           include internal details. The `DatabaseError` messages include raw
           exception strings like `f"Failed to create LSR: {e}"`.
Risk:      Error responses may reveal internal class names, database driver errors,
           connection strings, or query fragments to API consumers.
Fix:       In non-debug mode, return generic error messages. Log the full details
           server-side only. Implement a consistent error sanitization layer.
```

### [LOW] — No Credential Rotation Mechanism

```
Layer:     2
Location:  src/config.py, docker-compose.yml
Evidence:  Database passwords and API keys are static environment variables.
           No rotation mechanism, no expiry, no per-user credential isolation.
           JWT configuration exists (`jwt_secret`, `jwt_algorithm`, `jwt_expire_minutes`)
           but JWT auth is not implemented — only static API key auth exists.
Risk:      Leaked credentials remain valid indefinitely. No audit trail of which
           API key performed which action (single shared key).
Fix:       Implement JWT-based auth for user-level access control.
           For infrastructure credentials, use a secrets manager (the production
           compose file references AWS Secrets Manager but it's not wired up).
```

### [LOW] — mypy Type Check Failures Ignored in CI

```
Layer:     1
Location:  .github/workflows/ci.yml:54
Evidence:  `continue-on-error: true  # Type coverage is improving`
Risk:      Type errors that could indicate logic bugs or security issues are not
           blocking the build. This reduces the value of static analysis.
Fix:       Fix existing type errors and remove `continue-on-error`. Use per-module
           mypy overrides for files that are still being improved.
```

### [LOW] — Elasticsearch SSL Disabled

```
Layer:     5
Location:  docker-compose.yml:71-72
Evidence:  `xpack.security.http.ssl.enabled=false` and
           `xpack.security.transport.ssl.enabled=false`
Risk:      Credentials and data transmitted to Elasticsearch are unencrypted.
           In a multi-host deployment, this allows network eavesdropping.
Fix:       Enable TLS for Elasticsearch in non-local environments.
           For local development, this is acceptable.
```

---

## SUMMARY

| Severity | Count | Key Issues |
|----------|-------|------------|
| CRITICAL | 0 | — |
| HIGH | 2 | Cypher injection in batch relationships; open Cypher query endpoint with weak blocklist + APOC |
| MEDIUM | 4 | CORS wildcard default; auth disabled by default; unpinned deps; exposed metrics/db ports |
| LOW | 4 | Error detail leakage; no credential rotation; mypy ignored; ES SSL disabled |

**Overall Assessment**: This codebase shows strong vibe-code characteristics — sophisticated infrastructure with several security-relevant gaps that a human security reviewer would typically catch. The two HIGH findings (Cypher injection vectors) are the most actionable. The project has good security *scaffolding* (bandit, pre-commit hooks, security tests, SecretStr usage, input validation) but the scaffolding has gaps where it matters most (the actual database query layer).

**Priority Remediation Order:**
1. Fix Cypher injection in `create_relationships_batch` (allowlist relationship types)
2. Replace or harden the open Cypher query endpoint
3. Set secure defaults for CORS and authentication
4. Pin dependencies and add `pip-audit` to CI
5. Restrict database and monitoring endpoint exposure
