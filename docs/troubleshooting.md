# Troubleshooting

Commands below run from the repository root, against the services of
`docker-compose.yml`. The ones that talk to a database need the passwords from
`.env` in your shell:

```bash
for v in NEO4J_PASSWORD REDIS_PASSWORD ELASTICSEARCH_PASSWORD; do
  export "$v=$(grep "^$v=" .env | cut -d= -f2-)"
done
```

For "the graph is empty" and `insufficient_data`, see the [FAQ](faq.md).

- [First checks](#first-checks)
- [What `/health` says](#what-health-says)
- [`Error: cannot reach Neo4j` (CLI exit status 2)](#error-cannot-reach-neo4j-cli-exit-status-2)
- [`503 DATABASE_ERROR` from the API](#503-database_error-from-the-api)
- [Neo4j password rules](#neo4j-password-rules)
- [Neo4j does not start: `Neo4j is already running (pid:7)`](#neo4j-does-not-start-neo4j-is-already-running-pid7)
- [Port already allocated](#port-already-allocated)
- [Search finds no typos](#search-finds-no-typos)
- [Search misses words that are in the graph](#search-misses-words-that-are-in-the-graph)
- [Elasticsearch is red or read-only: disk watermark](#elasticsearch-is-red-or-read-only-disk-watermark)
- [Ingestion cannot download its data](#ingestion-cannot-download-its-data)
- [Wiktionary rate limits](#wiktionary-rate-limits)
- [A `/graph/query` request keeps Neo4j busy after it timed out](#a-graphquery-request-keeps-neo4j-busy-after-it-timed-out)
- [401 and 429 from the API](#401-and-429-from-the-api)
- [Results do not change after an ingestion](#results-do-not-change-after-an-ingestion)
- [Remove a source from the graph](#remove-a-source-from-the-graph)

## First checks

```bash
docker compose ps                        # every service "Up (healthy)"?
docker compose logs --tail 50 neo4j      # or elasticsearch, redis, api
curl -s http://localhost:8000/health     # API and store status
lexicon stats                            # what is in the graph
docker compose exec neo4j cypher-shell -u neo4j -p "$NEO4J_PASSWORD" "MATCH (l:LSR) RETURN count(l) AS lsrs"
docker compose exec redis redis-cli -a "$REDIS_PASSWORD" --no-auth-warning ping
curl -s -u "elastic:$ELASTICSEARCH_PASSWORD" "http://localhost:9200/_cluster/health?pretty"
curl -s -u "elastic:$ELASTICSEARCH_PASSWORD" "http://localhost:9200/_cat/indices?v"   # lexicon_lsr: health, docs.count
```

Inside the redis container `redis-cli ping` works without `-a`, because
compose sets `REDISCLI_AUTH`. Elasticsearch answers 401 to requests without
`-u elastic:…`. Logs go to standard error, so `lexicon … --json` prints only
JSON on standard output. Output piped into a reader that stops early
(`lexicon search … | head -1`) ends the command without a traceback, with
status 1 when part of the output could not be written.

## What `/health` says

| `status` | HTTP | Meaning |
|---|---|---|
| `healthy` | 200 | Neo4j and every configured optional store answered |
| `degraded` | 200 | Neo4j answered; a configured Elasticsearch, Redis or PostgreSQL did not |
| `unhealthy` | 503 | Neo4j did not answer. The API retries it at most every 5 seconds, except after an authentication failure (see below) |

An optional store is configured when its settings are present:
Elasticsearch with `ELASTICSEARCH_URI` or `ELASTICSEARCH_PASSWORD`, Redis with
`REDIS_URI` or `REDIS_PASSWORD`, PostgreSQL with `POSTGRES_URI`. The others
are reported as `not_configured` and do not affect the status. The compose
stack:

```json
{"status":"healthy","api":"up","databases":{"neo4j":"connected","postgres":"not_configured","elasticsearch":"connected","redis":"connected"}}
```

An API with Elasticsearch configured but not running, and no Redis settings:

```json
{"status":"degraded","api":"up","databases":{"neo4j":"connected","postgres":"not_configured","elasticsearch":"disconnected","redis":"not_configured"}}
```

Without Redis settings the API logs at startup, at INFO level, `Redis is not
configured: async job state and rate-limit counters are kept per process, so
run a single worker`. With Redis configured but not answering, the same
advice is a WARNING that starts `Redis unavailable:`.

`postgres: not_configured` is normal: nothing uses PostgreSQL. `/health` does
not say whether the graph holds any data (`lexicon stats` does) or whether the
search index is complete (`_cat/indices` above; see
[Search misses words](#search-misses-words-that-are-in-the-graph)).

## `Error: cannot reach Neo4j` (CLI exit status 2)

`lexicon search`, `analyze`, `stats` and `reindex` connect to Neo4j first
and exit with status 2 when they cannot. `lexicon ingest` reads the source
first, then exits with status 2 at the write, logging `Cannot reach Neo4j at
…`. The CLI reads `NEO4J_URI` / `NEO4J_PASSWORD` from the environment, then
from `./.env` in the current directory. With nothing listening at the
configured address (here `bolt://localhost:1`), a log line `Failed to connect
to Neo4j: …` is followed by:

```text
Error: cannot reach Neo4j at bolt://localhost:1: Couldn't connect to localhost:1 (resolved to ('127.0.0.1:1',)):
Failed to establish connection to ResolvedIPv4Address(('127.0.0.1', 1)) (reason [Errno 111] Connect call failed ('127.0.0.1', 1))
Start it with `docker compose up -d neo4j` and check NEO4J_URI / NEO4J_PASSWORD in .env.
```

Neo4j is not running, is still starting (`docker compose ps` shows
`health: starting` for up to a minute), or listens elsewhere. Start it with
`docker compose up -d neo4j`, or fix `NEO4J_URI`.

```text
Error: cannot reach Neo4j at bolt://…: {neo4j_code: Neo.ClientError.Security.Unauthorized} {message: The client is unauthorized due to authentication failure.} {gql_status: 50N42} …
```

The password is wrong. The usual cause: `NEO4J_PASSWORD` was changed in `.env`
after the first `docker compose up`. The neo4j image sets the password only
when the database is created, and keeps the old one in the `neo4j_data`
volume. Either put the old password back in `.env`, or change the stored one
to match `.env`:

```bash
docker compose exec neo4j cypher-shell -u neo4j -p 'OLD_PASSWORD' -d system \
  "ALTER CURRENT USER SET PASSWORD FROM 'OLD_PASSWORD' TO '$NEO4J_PASSWORD'"
```

`docker compose down -v` also works, but deletes the graph and every other
volume.

An API that failed to log in does not retry, so that it does not trip
Neo4j's failed-login lockout: it stays `unhealthy` until you restart it
(`docker compose restart api`, or restart `ls-api` / `make run-api`).

Neo4j can also fail after a CLI command has connected: it stops, drops the
connection or does not answer in time. `search`, `analyze` and `stats` then
exit with status 2 and print no partial result (`stats --json` prints
nothing on standard output). A warning names the operation that failed:

```text
… WARNING  | src.repositories.lsr_repository | [-] Statistics retrieval failed, Neo4j unavailable: Failed to read from defunct connection …
Error: Graph database is not available (Neo4j at bolt://…); no result was produced.
```

`lexicon reindex` exits with status 1 instead, with `Error: Reindex failed:
Graph database is not available`. `lexicon ingest` counts the records it
could not write and exits with status 1:

```text
  Written to graph:    1646 LSRs, 642 relationships
  Failed to write:     500 LSRs, 0 relationships
  …
  Errors (1):
    - Batch 1: Graph database is not available
============================================================
… ERROR    | ingest | [-] The graph write was incomplete: 500 LSRs and 0 relationships failed (see Errors above)
```

Run the command again once Neo4j answers; an ingestion updates the records
it wrote before and does not duplicate them.

## `503 DATABASE_ERROR` from the API

While Neo4j cannot be reached, the `/api/v1` endpoints that read the graph
(analyses, records, search, graph traversal and `/graph/query`) answer 503,
whether Neo4j was down when the API started or went away later:

```json
{"error":"DATABASE_ERROR","message":"Graph database is not available","details":{}}
```

GraphQL answers 200 with the error in `errors`
(`"extensions":{"code":"DATABASE_ERROR"}`), and `/health` answers 503
`unhealthy`. The API log shows a warning that names the operation, then
`Database error: Graph database is not available` with a traceback. The
warning reads `Vocabulary lookup failed, Neo4j unavailable: Neo4j not
connected` when Neo4j was down at startup, and `… Neo4j unavailable:
Couldn't connect to …` when it went away later. The first request after it
went away also logs `ERROR | neo4j.io | … Failed to read from defunct
connection …`.

These requests fail at once, except `POST /api/v1/graph/query` after Neo4j
went away: the driver retries it (`WARNING | neo4j.session | Transaction
failed and will be retried in …`), and the API gives up with the same 503
when Neo4j has not begun the query within about 2 seconds. The 2 seconds
also apply while Neo4j is up: a first connection that is slow to open (TLS
to a remote server) or a connection pool with no free connection gives the
same 503, and so does a query that loses its cluster member
(`SessionExpired`), which is not retried. Send the request again.

A Neo4j that stops answering without closing its connections (a paused
container, a network partition) is slower to detect. Record reads,
searches, graph traversals, analyses, GraphQL fields and
`POST`/`DELETE /api/v1/lsr` answer after about 17 seconds, bulk export after
about a minute, with a message that says what timed out:

```json
{"error":"DATABASE_ERROR","message":"LSR search timed out","details":{}}
```

Against a paused Neo4j, `date-text` answered `Vocabulary lookup timed out`,
`/graph/path` `Path finding timed out` and GraphQL `language` `Language
lookup timed out`, each after 17 seconds. `/graph/query` gives up after
about 2 seconds.

Start Neo4j (see above); the API reconnects by itself, and requests succeed
again once Neo4j accepts connections. After an authentication failure it does
not reconnect until restarted.

## Neo4j password rules

| Rule | What happens otherwise |
|---|---|
| At least 8 characters | The neo4j container exits: `InvalidPasswordException: A password must be at least 8 characters.` |
| No `/` | The container exits: `Invalid value for NEO4J_AUTH: 'neo4j/abc/defghijk'` (the image splits `NEO4J_AUTH` at `/`) |
| No `$` | docker compose treats `$` in `.env` as interpolation: `x$yz12345` becomes `x`, with the warning `The "yz12345" variable is not set` |

Other URL-special characters are fine: the code URL-encodes passwords when
it builds connection URIs.

## Neo4j does not start: `Neo4j is already running (pid:7)`

```text
Neo4j is already running (pid:7).
Run with '--verbose' for a more detailed error message.
```

The neo4j container does not start, and `docker logs ls-neo4j` ends with the
lines above. Neo4j did not shut down cleanly: the machine crashed, the Docker
daemon was killed, or the container was stopped while a long query ran
(`docker compose restart`), and left a stale PID file in the container.
Recreate the container; the graph lives in the `neo4j_data` volume and is
kept:

```bash
docker compose up -d --force-recreate neo4j
```

## Port already allocated

```text
Bind for 127.0.0.1:<port> failed: port is already allocated
```

Compose publishes 7474 and 7687 (Neo4j), 9200 and 9300 (Elasticsearch), 6379
(Redis), 8000 (API) and, with the `postgres` profile, 5432, all on 127.0.0.1.
Find what holds the port:

```bash
docker ps --filter publish=7687
lsof -nP -iTCP:7687 -sTCP:LISTEN
```

Stop that container or process. A host-side `make run-api` also uses port
8000, so it cannot run next to the compose `api` service. To publish a service
on another port, replace its `ports` in a compose override file (compose merges
port lists, so use `ports: !override`), then point the host tools at it, for
example `NEO4J_URI=bolt://localhost:17687` in `.env`.

## Search finds no typos

With Elasticsearch, form search (`lexicon search`, `/api/v1/lsr/search`) also
finds near misses:

```console
$ lexicon search --form watr --language eng
2 match(es) for 'watr' in eng
  water [eng] 700-present 'the water'  id=03f24a96-212c-5f03-9ee9-024e6f7a7dde
  war [eng] 1154-present 'the war or battle'  id=3062df0c-7a71-5bba-8f93-913b58b51318
```

If the same search answers `0 match(es) for 'watr' in eng` while `--form
water` works, the search ran in Neo4j, which matches substrings only. That
happens when Elasticsearch is:

- **Not configured**: neither `ELASTICSEARCH_URI` nor
  `ELASTICSEARCH_PASSWORD` is set. `/health` shows
  `"elasticsearch":"not_configured"`.
- **Not reachable.** The CLI falls back without a message. The API logs
  `Failed to connect to Elasticsearch: …` at startup, shows
  `"elasticsearch":"disconnected"`, and keeps trying for three minutes; when
  it connects, it checks the index as below. Until then it does not cache
  the Neo4j results of form searches, so searches find typos as soon as it
  connects. Start Elasticsearch (`docker compose up -d elasticsearch`), and
  restart the API if it came up later than that.
- **Failing**, usually because the index is red or missing (see
  [Elasticsearch is red or read-only](#elasticsearch-is-red-or-read-only-disk-watermark)). Every search
  logs, for example:

  ```text
  … WARNING  | src.repositories.lsr_repository | [-] Elasticsearch search failed, falling back to Neo4j: ApiError(503, 'search_phase_execution_exception', None)
  ```

  The API does not cache these fallback results, so searches find typos
  again as soon as the index works. There is no fallback when Neo4j fails
  while loading the records Elasticsearch found: the search answers
  `503 DATABASE_ERROR`, since a Neo4j search would fail the same way.

## Search misses words that are in the graph

When Elasticsearch is connected and answers, form search asks it only.
Records written while it was down, or while its index was red or read-only,
are in Neo4j but not in the index:

```console
$ lexicon search --form water --language eng
0 match(es) for 'water' in eng
$ lexicon reindex
Indexed 2146 LSRs (0 failed)
$ lexicon search --form water --language eng
2 match(es) for 'water' in eng
  water [eng] 700-present 'the water'  id=03f24a96-212c-5f03-9ee9-024e6f7a7dde
  waterfall [eng] 992-present 'the waterfall'  id=541e7364-a06f-5747-b56f-4fc44dead563
```

`lexicon reindex` rebuilds the index from Neo4j (creating it if needed),
removes documents whose records no longer exist, and then clears the API's
cached searches and records in Redis. An ingestion that could not index says so in its
summary. With Elasticsearch configured but unreachable, the summary has
`Search index: not configured or unreachable` and the log:

```text
… WARNING  | src.pipelines.graph_writer | [-] Elasticsearch is configured but not reachable; writing to Neo4j only. Run `lexicon reindex` once it is up.
```

When Elasticsearch refused some of the records, the summary counts them and
the reason is listed under `Errors` (see
[Read-only index](#elasticsearch-is-red-or-read-only-disk-watermark)):

```text
  Search index:        188 LSRs not indexed; run `lexicon reindex`
```

Such an ingestion still exits with status 0: the records are in Neo4j.

The API reindexes by itself. When it starts, or connects to an
Elasticsearch that came up after it, and the index holds fewer documents than
Neo4j has records, it reindexes in the background and then clears cached
searches:

```text
… WARNING  | src.api.main | [-] Elasticsearch index holds 0 of 2146 LSRs; reindexing from Neo4j in the background
… INFO     | src.api.main | [-] Elasticsearch reindexed 2146 LSRs from Neo4j
```

With several workers sharing Redis, one reindexes and the others wait. A
worker that finds the reindex lock held logs:

```text
… INFO     | src.api.main | [-] Another worker is reindexing Elasticsearch; checking the index again once its lock is released
```

It checks the lock every 30 seconds, and once the lock is released, or has
expired after 15 minutes because its holder died, it checks the index again
and reindexes if the index is still short.

Without Elasticsearch configured, or when it is unreachable, `lexicon
reindex` prints `Error: Elasticsearch not connected` and exits with status 1;
search then runs in Neo4j and needs no index.

## Elasticsearch is red or read-only: disk watermark

Elasticsearch stops placing and writing indexes when the disk holding its
data runs low. The compose file sets the limits as free space
(`cluster.routing.allocation.disk.watermark.*`): with less than 1 GB free a
new index cannot be placed and stays `red`; with less than 512 MB, the flood
stage, every index becomes read-only. Elasticsearch's own defaults are
percentages (90 and 95 % of the disk used), which a large disk reaches with
many gigabytes still free; they apply to an Elasticsearch you run without
this compose file.

**Red index.** `_cluster/health` says `"status" : "red"` and `_cat/indices`
shows `lexicon_lsr` as `red`. Searches fall back to Neo4j (see
[Search finds no typos](#search-finds-no-typos)), ingestion summaries list
the records as not indexed, and `lexicon reindex` fails with status 1 after
about 45 seconds:

```text
… WARNING  | src.repositories.lsr_repository | [-] ES reindex: 0 indexed, 2146 failed, 0 documents of deleted LSRs removed
Error: 2146 of 2146 LSRs not indexed; Reindex failed: Connection timed out; Elasticsearch bulk request failed: Connection timed out
```

With no `lexicon_lsr` index at all, `lexicon reindex` creates one that stays
red, and fails with status 1 after about 20 seconds:

```text
… WARNING  | src.repositories.lsr_repository | [-] Elasticsearch index lexicon_lsr is unusable (Connection timed out); rebuild it with `lexicon reindex`
… WARNING  | src.repositories.lsr_repository | [-] Elasticsearch index lexicon_lsr is unusable (Connection timed out); rebuild it with `lexicon reindex`
Error: Could not create Elasticsearch index lexicon_lsr
```

Ask Elasticsearch why the index is not placed:

```bash
curl -s -u "elastic:$ELASTICSEARCH_PASSWORD" "http://localhost:9200/_cluster/allocation/explain?pretty"
```

A full disk shows as `the node is above the high watermark cluster setting
[cluster.routing.allocation.disk.watermark.high=…], having less than the
minimum required […] free space`.

**Read-only index.** The index stays `green` and searches work, but new
records are not indexed ([Search misses words](#search-misses-words-that-are-in-the-graph)).
The Elasticsearch log says `flood stage disk watermark […] exceeded on […],
all indices on this node will be marked read-only`, and the ingestion summary
reports:

```text
  Search index:        188 LSRs not indexed; run `lexicon reindex`
  …
  Errors (1):
    - Search index: 188 of 188 LSRs of batch 0 not indexed (Elasticsearch rejected 188 of 188 documents (e.g. cluster_block_exception: index [lexicon_lsr] blocked by: [TOO_MANY_REQUESTS/12/disk usage exceeded flood-stage watermark, index has read-only-allow-delete block];)); rebuild the index with `lexicon reindex`
```

`lexicon reindex` fails with status 1 on the same `cluster_block_exception`.

Free disk space (`docker system df` shows what Docker uses). Elasticsearch
places a red index and lifts the read-only block by itself once there is
room; the log then says `releasing read-only block on indices [lexicon_lsr]
since they are now allocated to nodes with sufficient disk space`. Then run
`lexicon reindex`, or restart the API, which reindexes when the index holds
fewer documents than Neo4j has records. On a development machine you can
instead switch the check off (the block goes within a minute):

```bash
curl -s -u "elastic:$ELASTICSEARCH_PASSWORD" -X PUT "http://localhost:9200/_cluster/settings" \
  -H 'Content-Type: application/json' \
  -d '{"persistent":{"cluster.routing.allocation.disk.threshold_enabled":false}}'
lexicon reindex
```

## Ingestion cannot download its data

WOLD and CLICS download CSV files from `raw.githubusercontent.com` into
`data/wold` and `data/clics` on first use; Wiktionary calls
`en.wiktionary.org`. Behind a proxy, set `HTTPS_PROXY`, which the HTTP client
honours. A WOLD or CLICS file is tried three times, then the run stops with
exit status 1, about 12 seconds after the first attempt:

```text
… WARNING  | src.adapters.clld | [-] Download attempt 3/3 failed for forms.csv: [Errno 111] Connection refused. Retrying in 6s...
… ERROR    | ingest | [-] Failed to download https://raw.githubusercontent.com/lexibank/wold/master/cldf/forms.csv after 3 attempts: [Errno 111] Connection refused
```

A proxy that refuses the connection gives the same output. Any other HTTP
failure ends the same way with its own message, for example a proxy that
answers `403 Forbidden` for a host it does not allow:

```text
… ERROR    | ingest | [-] Failed to download https://raw.githubusercontent.com/lexibank/wold/master/cldf/forms.csv after 3 attempts: 403 Forbidden
```

Fetch the files some other way and point `--data-dir` at them (the CLICS
source needs `forms.csv`, `languages.csv` and `parameters.csv` from
`https://raw.githubusercontent.com/intercontinental-dictionary-series/ids/master/cldf`):

```bash
mkdir -p data/wold
for f in forms languages parameters borrowings; do
  curl -fsSL -o data/wold/$f.csv https://raw.githubusercontent.com/lexibank/wold/master/cldf/$f.csv
done
lexicon ingest --source wold --language English --data-dir data/wold
```

When the Wiktionary API is unreachable, each word fails and is listed in the
summary; a run that fetched nothing exits with status 1. Here the proxy
answered `403 Forbidden`:

```text
  Errors (1):
    - Failed to fetch 'water': 403 Forbidden
============================================================
… ERROR    | ingest | [-] Nothing was ingested: the source returned no entries for these options
```

## Wiktionary rate limits

Wiktionary ingestion makes one API request per word, 100 ms apart by default,
and identifies itself with a `User-Agent` naming this project. After a
refused connection or a read or write timeout a word is tried up to three
times. Other failures, including an HTTP error such as `429 Too Many
Requests`, are not retried: the word is skipped and listed under `Errors` in
the summary. Slow down and re-run the list (re-running is safe;
records are updated, not duplicated):

```bash
lexicon ingest --words data/seed_words_eng.txt --language English --rate-limit 1000
```

## A `/graph/query` request keeps Neo4j busy after it timed out

`POST /api/v1/graph/query` gives a query `timeout_seconds` (default 10, at
most 30) and stops waiting two seconds after that, answering
`"error":"QUERY_TIMEOUT"`. Neo4j checks its timeout only between units of
work, so a query that only computes, such as
`UNWIND range(1, 100000) AS i UNWIND range(1, 100000) AS j WITH sum((i * j) % 7) AS s RETURN s`,
keeps running on the server and uses a full CPU core, past the compose
setting `db.transaction.timeout=120s`. `SHOW TRANSACTIONS` lists it as
`Terminated with reason: …TransactionTimedOutClientConfiguration` while it
goes on, and `TERMINATE TRANSACTION` does not stop it either. Find it, then
recreate the Neo4j container (the graph is kept in its volume; a plain
`docker compose restart` can leave it unable to start, see
[`Neo4j is already running`](#neo4j-does-not-start-neo4j-is-already-running-pid7)):

```bash
docker compose exec neo4j cypher-shell -u neo4j -p "$NEO4J_PASSWORD" \
  "SHOW TRANSACTIONS YIELD transactionId, elapsedTime, status, currentQuery RETURN transactionId, elapsedTime, status, left(currentQuery, 60) AS query"
docker compose up -d --force-recreate neo4j
```

This is why [SECURITY.md](../SECURITY.md) asks for an API key wherever the API
is reachable by others. Where clients you do not trust hold a key, set
`GRAPH_QUERY_ENABLED=false`: the endpoint then answers `403` with
`"error":"QUERY_DISABLED"`.

## 401 and 429 from the API

With `API_KEY` set, everything except `/`, `/health` and the documentation
pages (`/docs`, `/redoc`, `/openapi.json`) needs the key in the `X-API-Key`
header (`/graphql` and `/metrics` included):

```json
{"error":"AUTHENTICATION_ERROR","message":"API key required","details":{"header":"X-API-Key"}}
{"error":"AUTHENTICATION_ERROR","message":"Invalid API key","details":{}}
```

Each client IP may make `RATE_LIMIT_REQUESTS` requests (default 100) per
`RATE_LIMIT_WINDOW_SECONDS` (default 60). Only `/health` and the
documentation pages (`/docs`, `/docs/oauth2-redirect`, `/redoc`,
`/openapi.json`) are not counted; `/`, `/metrics` and requests rejected for
a missing or wrong key are. Over the limit (here with
`RATE_LIMIT_REQUESTS=3`):

```text
HTTP/1.1 429 Too Many Requests
x-ratelimit-limit: 3
x-ratelimit-remaining: 0
x-ratelimit-reset: 56
retry-after: 56
{"error":"RATE_LIMIT_EXCEEDED","message":"Rate limit exceeded: 3 requests per 60 seconds","details":{"retry_after_seconds":56}}
```

Wait `Retry-After` seconds, or raise the limit. The API container gets
`API_KEY` and `RATE_LIMIT_*` from `.env` when compose creates it, so run
`docker compose up -d api` after changing them.

Behind a reverse proxy, every client arrives from the proxy's address and
shares one budget, unless the API trusts the `X-Forwarded-For` header the
proxy sets. Set `FORWARDED_ALLOW_IPS` (compose default `127.0.0.1`) to the
proxy's address. A proxy on the same host reaches the container from the
Docker network's gateway, so use that address or `172.16.0.0/12`
([docker-compose.production.yml](../docker-compose.production.yml)
explains it).

## Results do not change after an ingestion

The analyses read Neo4j directly and see new data at once. Form search and
single-record reads (`/api/v1/lsr/search`, `/api/v1/lsr/{id}`) are cached in
Redis for up to 3 and 10 minutes. Ingestion clears that cache when it can
reach the API's Redis (`REDIS_URI`, or `REDIS_PASSWORD` with `REDIS_HOST`).
When Redis is configured but the ingestion cannot reach it, cached answers
expire on their own, and its log says:

```text
… INFO     | src.pipelines.graph_writer | [-] API response cache not cleared (Redis unreachable: …); an API using that cache may serve pre-ingestion results for up to 600s
```

Without Redis nothing is cached. When Elasticsearch is configured, form
searches that Neo4j answered in its place (because Elasticsearch was not
connected or failed to answer) are not cached. Cached searches are cleared
after a reindex, by the API's own or by `lexicon reindex`.

## Remove a source from the graph

To take out, for example, the sample corpus (records whose only source is
`corpus`), delete them in Neo4j, then bring the search index in line:

```bash
docker compose exec neo4j cypher-shell -u neo4j -p "$NEO4J_PASSWORD" \
  "MATCH (l:LSR) WHERE l.source_databases = ['corpus'] DETACH DELETE l RETURN count(*) AS deleted"
lexicon reindex    # only if Elasticsearch is configured
```

Replace `corpus` with `wold`, `wiktionary`, `clics` or your adapter's
`source_name`. Placeholder records created for link targets (donor words,
ancestors and cognates) carry the sources of every ingestion that linked to them; one
that two sources linked to lists both, so the query above keeps it.

Still stuck? Open an issue at
<https://github.com/kase1111-hash/Lexicon/issues> with the command, its full
output, `docker compose ps` and the `/health` response.
