# Security policy

## Supported versions

Lexicon is alpha software. Security fixes go to the `main` branch and the
next release; older releases are not patched.

| Version | Supported |
|---|---|
| `main`, 0.1.x | Yes |
| Anything older | No |

## Reporting a vulnerability

Report privately through a GitHub security advisory: the **Report a
vulnerability** button on the repository's
[Security tab](https://github.com/kase1111-hash/Lexicon/security), which
opens <https://github.com/kase1111-hash/Lexicon/security/advisories/new>. If
the button is not there, open an [issue](https://github.com/kase1111-hash/Lexicon/issues)
that asks for a private contact and contains no details of the problem.

Include the version or commit, how to reproduce the problem, and what an
attacker gains. The report, the fix and the coordinated disclosure are
handled in the advisory thread. Lexicon is maintained by volunteers, so there
is no guaranteed response time.

## What the API does to protect itself

| Control | What it does | Limits |
|---|---|---|
| API key | With `API_KEY` set, every path except `/`, `/health`, `/docs`, `/redoc` and `/openapi.json` requires it in the `X-API-Key` header (`/graphql` and `/metrics` included), compared in constant time | Off when `API_KEY` is empty, which is the development default; the API logs a warning at startup. One shared key, no users or roles: whoever holds it can also create and delete records (`POST /api/v1/lsr`, `DELETE /api/v1/lsr/{id}`) |
| Production settings | `docker-compose.production.yml` refuses to start without `API_KEY`. With `ENVIRONMENT=production` the API refuses to start without `API_KEY` or `NEO4J_PASSWORD`, with `DEBUG=true`, with a `*` entry anywhere in `CORS_ORIGINS` (it allows every origin) or with rate limiting off | Only when you use the overlay or set `ENVIRONMENT=production` |
| Rate limiting | Per client IP, fixed window, default 100 requests per 60 s (`RATE_LIMIT_*`); 429 with `Retry-After`. Every path that can require the key is limited, `/metrics` included, and requests rejected for a missing or wrong key count too, so keys cannot be guessed without limit. Counters are shared through Redis, otherwise kept per process | `/health` and the documentation pages (`/docs`, `/redoc`, `/openapi.json`) are not limited; `/` is. The client is the address uvicorn reports, which it takes from `X-Forwarded-For` only for connections from `FORWARDED_ALLOW_IPS` (default `127.0.0.1`). Behind a reverse proxy, set `FORWARDED_ALLOW_IPS` to the proxy's address as the API sees it (for the compose stack, the `ls-network` gateway; see `docker-compose.production.yml`), or all clients share one budget |
| Read-only Cypher (`POST /api/v1/graph/query`) | The query must start with a read clause (`MATCH`, `OPTIONAL MATCH`, `RETURN`, `WITH`, `UNWIND`, `EXPLAIN`, `PROFILE`). `LOAD CSV`, `CALL`, `USE`, `apoc.*`, `dbms.*` and administration commands are rejected wherever they appear, even in strings and comments; write clauses are rejected outside strings and comments. Unicode escapes (`\u0043`) are rejected anywhere, because Neo4j decodes them before parsing and they could spell a keyword the checks do not see; a backslash outside string literals and comments is rejected too. It runs in a read transaction, so Neo4j itself rejects writes. At most 5,000 characters in; at most 1,000 rows or about 5 MB returned; server-side timeout `timeout_seconds` (default 10, at most 30). `GRAPH_QUERY_ENABLED=false` turns the endpoint off (`403 QUERY_DISABLED`); `config/.env.production` sets it | The caps limit what is returned, not what the API reads. See the residual risks below |
| GraphQL limits | Query depth 5, at most 15 aliases, at most 1,000 estimated graph traversals per query | |
| Error messages | Database and driver error text is not returned to clients; unexpected errors answer a generic 500 | With `DEBUG=true` (the development compose override) a 500 also names the exception type |
| Network exposure | Every compose service, the API included, is published on `127.0.0.1` only; `make run-api` binds `127.0.0.1`, and so does `ls-api` unless `API_HOST` says otherwise. Neo4j, Elasticsearch and Redis require passwords: compose refuses to start without them | Elasticsearch and Redis run without TLS on the internal network. `make run-api-prod` binds `0.0.0.0` |
| Container | The API image runs as the non-root user `appuser` | |
| Dependencies | CI runs bandit on `src/` and pip-audit on `requirements.txt`; Dependabot proposes updates weekly | |

### Residual risks of `POST /api/v1/graph/query`

The endpoint runs Cypher that API clients write. The checks above keep it
read-only, but they cannot bound what a read costs.

**Queries Neo4j cannot interrupt.** Neo4j checks transaction timeouts only
between units of work. A query that only computes passes every check above,
for example:

```cypher
UNWIND range(1, 100000) AS i UNWIND range(1, 100000) AS j WITH sum((i * j) % 7) AS s RETURN s
```

The API stops waiting and answers `QUERY_TIMEOUT` after `timeout_seconds`
plus two seconds, but Neo4j keeps running the query on a full CPU core.
Neo4j marks it as timed out, yet neither that, nor `TERMINATE TRANSACTION`,
nor the compose server-wide limits
(`db.transaction.timeout=120s`, `db.memory.transaction.max=256m`; the query
uses almost no memory) stop it. It ends only when it finishes or Neo4j
restarts.

**Large values.** The row and size caps apply to what the API returns, not
to what it reads. A query that returns one huge value, such as
`RETURN range(1, 3000000)`, is read whole into the API process before it is
measured and dropped; the client gets an empty, truncated result. A few such
requests can use a lot of the API's memory.

Anyone who can call the endpoint can therefore tie up the database or the
API with a few requests, and anyone holding the key can read the whole
graph. That is why the development setup publishes the API on `127.0.0.1`
only and why the production overlay requires an API key. Treat the key as a
database credential. Where API keys go to clients you do not fully trust,
set `GRAPH_QUERY_ENABLED=false` (the production template
`config/.env.production` does): the endpoint then answers
`403 QUERY_DISABLED`, and the fixed REST routes and GraphQL keep working.
[docs/troubleshooting.md](docs/troubleshooting.md#a-graphquery-request-keeps-neo4j-busy-after-it-timed-out)
shows how to find and stop a runaway query.

## Deploying

- Use the production overlay and set `API_KEY` to a long random value, for
  example the output of
  `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
- Give Neo4j, Elasticsearch and Redis distinct strong passwords, and keep
  filled-in env files out of git (`.env` is ignored).
- Keep the API on `127.0.0.1` and put a TLS-terminating reverse proxy in
  front of it; expose it only to the people who need it. Set
  `FORWARDED_ALLOW_IPS` to the proxy's address as the API sees it, so that
  each client gets its own rate limit.
- Keep `GRAPH_QUERY_ENABLED=false`, as `config/.env.production` sets it,
  unless every key holder is trusted.
- Set `CORS_ORIGINS` to the origins of your front ends.
- See the production checklist in [config/README.md](config/README.md).
