# Configuration

Lexicon is configured only through environment variables. This page lists every
variable the code and the compose files read.

## Where values come from

1. **Real environment variables** take precedence.
2. **An env file**: `./.env` in the directory you run from, or the file named by
   `ENV_FILE`. The Python code (API, `lexicon` CLI, ingestion, migrations) loads it
   and never overrides variables that are already set.
3. **Defaults** in `src/config.py` and `src/utils/db.py`.

docker compose reads `./.env` (or `--env-file <file>`) to fill in its
`${...}` placeholders. The containers get only the variables listed in their
`environment:` sections, so the API container never reads a `.env` file. For
the api service these are the database passwords, `API_KEY`, `CORS_ORIGINS`,
`LOG_LEVEL`, `RATE_LIMIT_*`, `SLOW_REQUEST_THRESHOLD_MS`, `SENTRY_DSN`,
`GRAPH_QUERY_ENABLED`, `APP_VERSION` (empty unless set, so the image reports
the version of its code) and `FORWARDED_ALLOW_IPS`. The database hosts and
the Neo4j user are fixed to the bundled services; `API_HOST` / `API_PORT` /
`API_WORKERS` apply only to `ls-api` on the host (the container runs one
uvicorn worker on port 8000).

## Files in this directory

| File | Use it for |
|------|------------|
| `.env.development` | API/CLI on the host against the compose databases: `ENV_FILE=config/.env.development ls-api --reload` |
| `.env.staging` | Production behaviour with a smaller footprint (compose `--env-file` or `ENV_FILE`) |
| `.env.production` | Template for a production env file; copy it somewhere outside git and fill it in |

Start from the repository root `.env.example` for the default docker compose setup:
`cp .env.example .env`.

## Variables

### Databases (`src/utils/db.py`)

| Variable | Default | Notes |
|----------|---------|-------|
| `NEO4J_URI` | `bolt://localhost:7687` | Required store. Compose sets `bolt://neo4j:7687` for the api container |
| `NEO4J_USER` / `NEO4J_PASSWORD` | `neo4j` / `password` | Compose requires `NEO4J_PASSWORD`. The bundled Neo4j container's user is always `neo4j` (the image refuses to start with another), so compose ignores `NEO4J_USER`; set it only for a Neo4j server outside compose |
| `ELASTICSEARCH_URI` | derived | Optional. Derived as `http://elastic:<ELASTICSEARCH_PASSWORD>@<ELASTICSEARCH_HOST>:9200` |
| `ELASTICSEARCH_PASSWORD` / `ELASTICSEARCH_HOST` | none / `localhost` | The API, CLI and ingestion use Elasticsearch only when `ELASTICSEARCH_URI` or `ELASTICSEARCH_PASSWORD` is set. Compose requires `ELASTICSEARCH_PASSWORD` |
| `REDIS_URI` | derived | Optional. Derived as `redis://:<REDIS_PASSWORD>@<REDIS_HOST>:6379` |
| `REDIS_PASSWORD` / `REDIS_HOST` | none / `localhost` | The API and ingestion use Redis only when `REDIS_URI` or `REDIS_PASSWORD` is set. Compose requires `REDIS_PASSWORD` |
| `POSTGRES_URI` | unset | Optional, reserved for future use. The API connects to PostgreSQL only when this is set |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` / `POSTGRES_HOST` / `POSTGRES_PORT` | `ls_user` / `password` / `linguistic_stratigraphy` / `localhost` / `5432` | Used by migrations and `scripts/load_initial_data.py` when `POSTGRES_URI` is unset, and by the compose `postgres` service |

The API and CLI never contact a store that is not configured, and `/health`
reports it as `not_configured` without marking the API `degraded`: with only
the Neo4j variables set, the API is `healthy`. Docker Compose still needs
all three passwords (`NEO4J_PASSWORD`, `ELASTICSEARCH_PASSWORD`,
`REDIS_PASSWORD`) to render the file, even to start only `neo4j`.

Derived URIs URL-encode the passwords, so URL-special characters are fine.
The neo4j image rejects a `NEO4J_PASSWORD` containing `/` or shorter than 8
characters, and docker compose treats `$` in `.env` values as interpolation.

### API (`src/config.py`)

| Variable | Default | Notes |
|----------|---------|-------|
| `API_KEY` | unset | Enables authentication of `/api/v1` and `/graphql`. Required in production |
| `API_KEY_HEADER` | `X-API-Key` | |
| `CORS_ORIGINS` | `http://localhost:3000,http://localhost:8080` | Comma-separated. A `*` entry anywhere in the list (which allows every origin) is rejected in production |
| `CORS_ALLOW_CREDENTIALS` | `false` | |
| `GRAPH_QUERY_ENABLED` | `true` | `false` turns off `POST /api/v1/graph/query`, which runs read-only Cypher written by API clients; it then answers 403 `QUERY_DISABLED`. The query's caps limit the rows returned, not what the API must receive, so one query can still use a lot of memory: turn it off where API keys go to clients you do not trust. `config/.env.production` sets `false` |
| `RATE_LIMIT_ENABLED` | `true` | Required in production |
| `RATE_LIMIT_REQUESTS` / `RATE_LIMIT_WINDOW_SECONDS` | `100` / `60` | Per client IP; shared across workers through Redis. Only `/health`, `/docs`, `/docs/oauth2-redirect`, `/redoc` and `/openapi.json` are not limited |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` in compose | Read by uvicorn, not by `src/config.py`. When a connection comes from one of these addresses or networks (comma-separated), the client address is taken from its `X-Forwarded-For` header. Compose passes it to the api container. Behind a reverse proxy, set it to the proxy's address as the container sees it, or all clients share one rate-limit budget (see [Production checklist](#production-checklist)) |
| `API_HOST` / `API_PORT` / `API_WORKERS` | `127.0.0.1` / `8000` / `1` | Used by `ls-api` only. More than one worker needs Redis: `make run-api-prod` (whose own `API_WORKERS` default is 4) starts one worker unless Redis is configured and answers. The api container runs uvicorn with `--host 0.0.0.0` and is published on `127.0.0.1:8000` |

### Logging and environment (`src/config.py`, `src/utils/error_tracking.py`)

| Variable | Default | Notes |
|----------|---------|-------|
| `ENVIRONMENT` | `development` | `development`, `staging` or `production`. `production` validates the settings at startup |
| `DEBUG` | `false` | Must be `false` in production |
| `APP_VERSION` | the code's version | Overrides the version reported by `GET /`, the OpenAPI document and Sentry (as the release). Normally left unset; compose passes it empty, so the image reports the version of its code |
| `LOG_LEVEL` / `LOG_FORMAT` / `LOG_FILE` | `INFO` / `text` / none | Logs go to stderr (and to `LOG_FILE` when set). `LOG_FORMAT=json` for log collectors |
| `API_LOG_LEVEL` / `PIPELINE_LOG_LEVEL` / `DB_LOG_LEVEL` | `INFO` / `INFO` / `WARNING` | API only: levels for `src.api`, `src.pipelines`, and `src.utils.db` with the `neo4j` driver (which logs every Bolt message at `DEBUG`). A level below `LOG_LEVEL` has no effect, because the handler filters at `LOG_LEVEL`: to see driver messages, set both `LOG_LEVEL=DEBUG` and `DB_LOG_LEVEL=DEBUG`. The CLI and ingestion ignore these three variables |
| `SLOW_REQUEST_THRESHOLD_MS` | `1000` | Slower requests are logged as warnings |
| `SENTRY_DSN` | unset | Enables Sentry. Needs sentry-sdk 2.56 or later (`requirements.txt` pins 2.70.0); older releases fail to initialize with the pinned Strawberry and Starlette, which leaves Sentry off |
| `ELASTICSEARCH_HOSTS` / `ELASTICSEARCH_CLOUD_ID` / `ELASTICSEARCH_API_KEY` | unset | Optional shipping of WARNING+ logs to an Elasticsearch cluster (separate from search) |

### Tooling

| Variable | Read by | Notes |
|----------|---------|-------|
| `ENV_FILE` | Python code | Env file to load instead of `./.env` |
| `VERSION` | `docker-compose.production.yml` | Image tag to run (default `latest`) |
| `TEST_NEO4J_URI` / `TEST_NEO4J_PASSWORD` (and `TEST_ELASTICSEARCH_URI`, `TEST_REDIS_URI`, `TEST_POSTGRES_URI`) | `tests/conftest.py` | The only way tests reach a database; see `make test-db` |

## Elasticsearch disk watermarks

Elasticsearch stops allocating shards to a disk that is fuller than its
watermarks. The defaults are relative to the disk size (85%, 90% and 95%
used), so a disk around 90% full leaves the search index unassigned even with
many gigabytes free, and every search falls back to Neo4j.
`docker-compose.yml` sets absolute watermarks on the `elasticsearch` service
instead, as free space:

| Setting | Value | Below this much free space |
|---------|-------|----------------------------|
| `cluster.routing.allocation.disk.watermark.low` | `2gb` | No shards are allocated to the node except the primaries of new indexes |
| `cluster.routing.allocation.disk.watermark.high` | `1gb` | No shards are allocated, including a new index; existing ones are moved away where possible |
| `cluster.routing.allocation.disk.watermark.flood_stage` | `512mb` | Indexes on the node become read-only |

They are compose `environment:` entries, not `.env` variables; edit
`docker-compose.yml` to change them. The production overlay keeps them.

## Production checklist

With `ENVIRONMENT=production` the API refuses to start unless `NEO4J_PASSWORD`
and `API_KEY` are set, `DEBUG` is false, `CORS_ORIGINS` has no `*` entry, rate
limiting is on, and, if `POSTGRES_URI` is set, it carries a password. Check a
configuration without starting the server:

```python
from src.config import get_settings

print(get_settings().validate_required_for_production() or "OK")
```

The production compose overlay does not enable TLS on Elasticsearch. All
services except the API are reachable only on the internal Docker network and
on `127.0.0.1`, and the API is published on `127.0.0.1:8000` too. Terminate TLS
at a reverse proxy (nginx, Caddy, Traefik) on the host that forwards to
`127.0.0.1:8000`.

The rate limit counts per client address. Even a proxy on the same host
reaches the api container from the gateway of the compose network
(`ls-network`), not from `127.0.0.1`, so with the default
`FORWARDED_ALLOW_IPS` every client shares the proxy's budget. Set
`FORWARDED_ALLOW_IPS` in the env file to that gateway:

```bash
docker network inspect <project>_ls-network -f '{{(index .IPAM.Config 0).Gateway}}'
```

or to `172.16.0.0/12`, Docker's default bridge range, if the network may be
recreated with another subnet. The proxy must set or append
`X-Forwarded-For`; uvicorn takes the rightmost address it does not trust as
the client.

`config/.env.production` also sets `GRAPH_QUERY_ENABLED=false`; turn it on
only if every API key holder is trusted.

To use managed databases instead of the bundled ones, add one more overlay
that replaces the api's connection settings (`NEO4J_URI`,
`ELASTICSEARCH_URI`, `REDIS_URI`) and its wait for Neo4j, and start only
the services you keep. The comments in `docker-compose.production.yml` show
it; setting these variables in the env file alone does not reach the api
container.

Keep filled-in env files out of git (`.env` and `*.env.local` are ignored).
