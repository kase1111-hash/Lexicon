# Lexicon

**Date a text by its words.**

Lexicon builds a graph of words, each with the year it was first attested and
the language it was borrowed from, out of open lexical datasets. It then uses
that graph to answer three questions:

1. **When could this text have been written?** A text is no older than its
   newest word: *"written no earlier than 1835 (first attestation of
   telephone)"*.
2. **Does this text use words that didn't exist yet?** Words first attested
   after a text's claimed date are flagged as anachronisms. This is a first
   screen for forgeries and misdated documents.
3. **Which languages did this one borrow from, and when?** Dated loanwords are
   clustered into language-contact events, e.g. *Old Norse → English,
   1200–1300: sky, they, skin, skull, leg*.

It is built for historical linguists, philologists and digital-humanities
researchers who want those answers from open data, reproducibly, through a
command line, a REST API or GraphQL.

```console
$ lexicon analyze anachronisms --date 1300 \
    --text "The knight spoke on the telephone and watched the television"
Verdict: anachronistic (confidence 0.90)
Dated words: 3 of 5 content words
2 word(s) first attested well after 1300: television (1907), telephone (1835).
  - television: first attested 1907 (607 years after 1300, high)
  - telephone: first attested 1835 (535 years after 1300, high)
Not in graph: knight, watched
```

Every answer says how much of the text it could actually check. When too few
of a text's words are in the graph, the verdict is `insufficient_data`, never a
confident "consistent".

## Quickstart

Requirements: Python 3.11 and Docker. Takes about five minutes.

```bash
git clone https://github.com/kase1111-hash/Lexicon.git
cd Lexicon
cp .env.example .env          # then set NEO4J_PASSWORD, ELASTICSEARCH_PASSWORD, REDIS_PASSWORD

docker compose up -d neo4j    # the graph database (all the CLI needs)
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e .              # installs the `lexicon` command

# Load the World Loanword Database (downloads ~18 MB from GitHub once):
# 2,146 English words and donor words, 642 borrowing links
lexicon ingest --source wold --language English

lexicon stats
lexicon analyze anachronisms --date 1300 --text "The knight spoke on the telephone"
lexicon analyze date-text --text "They saw the sky over the mountain and ate an egg"
lexicon analyze contact --language eng
lexicon search --form sky --language eng
```

The CLI reads the same `.env` as Docker Compose, so it finds Neo4j without
extra configuration. Elasticsearch is not running yet, so the ingestion warns
`Elasticsearch is configured but not reachable; writing to Neo4j only` and
carries on; the graph is complete without it. When Neo4j cannot be reached,
or fails during a search, an analysis, `stats` or `reindex`, the command
reports the error on stderr and exits with status 2; it never prints a
partial result, not even with `lexicon stats --json`.

To run the REST and GraphQL API as well (with Elasticsearch for fuzzy search
and Redis for caching):

```bash
docker compose up -d
curl http://localhost:8000/health      # interactive docs at http://localhost:8000/docs
```

The API reports `healthy` about 20 seconds after the containers start. It
starts as soon as Neo4j is healthy, usually before Elasticsearch, and keeps
trying to connect to Elasticsearch for three minutes. When it starts or
connects, and the search index holds fewer records than Neo4j, as it does
after the ingestion above, it rebuilds the index from Neo4j in the background
(`docker compose logs api` shows `Elasticsearch index holds 0 of 2146 LSRs;
reindexing from Neo4j in the background`, then `Elasticsearch reindexed 2146
LSRs from Neo4j`). An Elasticsearch that comes up later is not used, or
rebuilt, until the API restarts. Searches made before the rebuild ends can
miss records; their cached results are dropped when it ends. `lexicon
reindex` does the same rebuild by hand, then clears the API's cached
searches as well.

Every service listens on 127.0.0.1 only, and authentication is off until you
set `API_KEY` in `.env`. PostgreSQL is not part of the default stack; it is
reserved for future metadata (`docker compose --profile postgres up -d`).

## Data sources

| Source | Command | What it contributes | Network |
|---|---|---|---|
| [WOLD](https://wold.clld.org/) (World Loanword Database) | `lexicon ingest --source wold` | 64k words in 41 languages with borrowing status and donor word/language; first-attestation years for ~1,500 English words | CSVs downloaded once from GitHub |
| [Wiktionary](https://en.wiktionary.org/) | `lexicon ingest --source wiktionary --words FILE` | definitions and etymologies (DESCENDS_FROM / BORROWED_FROM links); dates only where an entry states them (`{{defdate}}`, "first attested…", dated quotations) | Wikimedia API, rate-limited |
| [IDS](https://ids.clld.org/) via CLDF (CLICS colexifications) | `lexicon ingest --source clics` | forms and colexified concepts in 319 languages; no dates | CSVs downloaded once from GitHub |
| Your own corpus | `lexicon ingest --source corpus --corpus-dir DIR` | every word of your dated documents, dated by the earliest document it appears in | local |

A corpus is a directory of `.txt` files, each with an optional `.json`
sidecar such as `{"title": "Canterbury Tales", "date": 1390, "language_code": "enm"}`.
A corpus dates a word by the first document it appears in, so its dates are
only as good as its coverage of earlier periods. A sidecar may also give
`date_confidence` (0 to 1), which the words that document dates inherit.
`data/corpus/` shows the format with four short public-domain excerpts
(Chaucer, King James Bible, Austen, Wells); they are far too few to date
words by. Loaded into your graph, they make *watched* look first attested
in 1898 (Wells). Analyses name
the evidence behind each date in `date_label`, e.g. `earliest in corpus: …`
for corpus dates or `c. 1220` for WOLD's.

Ingestion is idempotent: re-running a source updates records in place. Add
`--dry-run` to see what would be written. If part of the graph write fails,
the summary adds a `Failed to write:` line and the command exits with
status 1.

## How it works

```
 source adapters ─► validation ─► entity resolution ─► Neo4j graph ─► analyses ─► CLI / REST / GraphQL
 (WOLD, Wiktionary,                (merge duplicates)   LSR nodes +     (dating,
  CLICS, corpus)                                        DESCENDS_FROM,   anachronisms,
                                                        BORROWED_FROM    contact, drift)
```

The unit of data is the **Lexical State Record (LSR)**: one form–meaning pair
in one language, with its attestation window. `date_start` is the first
attestation; `date_end` is the last, or empty while the word is still in use.
See [docs/data_model.md](docs/data_model.md).

## Reading the results

| Analysis | Result | Meaning |
|---|---|---|
| `date-text` | `status: ok`, range `[1835, 2026]` | No earlier than the newest word's first attestation; no later than the present unless some word fell out of use |
| | `status: conflicting_evidence` | A word was coined after another fell out of use; the coinage bound is kept, the obsolete word may be a deliberate archaism |
| | `status: insufficient_data`, range `null` | None of the text's words have dates in the graph |
| `detect-anachronisms` | `anachronistic` / `suspicious` | Words first attested well after (>50 years) the claimed date |
| | `consistent` | At least half of the content words are dated and none postdates the claim by more than 50 years; the explanation names up to five that postdate it by 50 years or less |
| | `insufficient_data` | Too few dated words to vouch for the text |
| `contact-events` | events per donor language and century | Clusters of BORROWED_FROM links, dated by the borrowed word's first attestation |
| `semantic-drift` (experimental) | trajectory of senses | Needs senses of the word that have both a date and a definition, first attested in at least two different years; otherwise `insufficient_data` |

Coverage counts distinct content words: a word repeated in the text counts
once. `confidence` rises with the share of those words that have dates and
with the number of dated words. It is a coverage measure, not a calibrated
probability.

Each word in `diagnostic_vocabulary` and each `coined_after` anachronism
carries `date_label`, its first attestation as the source states it, e.g.
`c. 1220`, `before 1382`, `14th century`, `Old English (inherited from
Proto-Germanic)` or `earliest in corpus: <title>` (empty when the source
gives no label, as with Wiktionary). The year next to it is what the
analysis computes with. The CLI prints the label when
it says more than the year:

```console
$ lexicon analyze anachronisms --date 1100 \
    --text "They saw the sky over the mountain and ate an egg"
Verdict: suspicious (confidence 0.80)
Dated words: 5 of 6 content words
2 word(s) first attested well after 1100: mountain (1275), sky (1220).
  - mountain: first attested 1275 (c. 1275; 175 years after 1100, high)
  - sky: first attested 1220 (c. 1220; 120 years after 1100, high)
Not in graph: over
```

## API

REST endpoints live under `/api/v1` (OpenAPI docs at `/docs`):

```bash
curl -X POST http://localhost:8000/api/v1/analyze/detect-anachronisms \
  -H "Content-Type: application/json" \
  -d '{"text": "The knight spoke on the telephone", "claimed_date": 1300, "language": "eng"}'

curl -X POST http://localhost:8000/api/v1/analyze/date-text \
  -H "Content-Type: application/json" \
  -d '{"text": "They saw the sky over the mountain", "language": "eng"}'

curl "http://localhost:8000/api/v1/analyze/contact-events?language=eng&date_start=1150&date_end=1350"
curl "http://localhost:8000/api/v1/lsr/search?form=sky&language=eng"
```

GraphQL is at `/graphql` (with the GraphiQL explorer in a browser):

```graphql
{
  detectAnachronisms(text: "the telephone rang", claimedDate: 1300, language: "eng") {
    verdict
    confidence
    anachronisms { form earliestAttestation dateLabel }
  }
}
```

Language codes for analyses and search are ISO 639-3 (`eng`, `enm`), ISO
639-1 (`en`, read as `eng`), Wiktionary codes for proto-languages and
historical varieties (`gem-pro`, `la-vul`) and Glottocodes, which WOLD uses
for languages without an ISO code (`tupi1276`). Names and region tags
(`english`, `en-gb`) are refused: REST answers 400 `INVALID_LANGUAGE_CODE`,
GraphQL returns an error with that code, and the CLI exits with status 1.
`lexicon ingest --language` is different: it takes a comma-separated list of
language names (`English`) or ISO 639-3 or ISO 639-1 codes (`eng,fra`,
`en`), and for WOLD and CLICS also Glottolog codes. For the corpus it names
the one language (name or code) of documents whose sidecar gives none.

See [docs/api-reference.md](docs/api-reference.md) for every endpoint.

## Limitations

- Dates are first attestations recorded by the sources, not proof of first
  use. WOLD's "before 1225" becomes 1225; inherited English words date from
  the Old English period (700).
- Coverage decides quality. WOLD dates about 1,500 core English words, so
  ordinary texts contain many words the graph doesn't know; those are listed
  as unknown. Add Wiktionary or your own dated corpus to extend coverage.
- English inflections are matched to dictionary forms with simple rules and an
  irregular-verb list, trying the base form before shorter words (*fades* is
  *fade*, not *fad*); other languages need exact dictionary forms.
- Semantic drift is experimental. It compares definition text with a small
  hashed n-gram encoder, not a trained language model, and needs senses of a
  word with a date and a definition, first attested in at least two
  different years; few words have them. *male*, whose two WOLD senses are
  both dated 1382, gives `insufficient_data`; *calf* ("the calf" 700, "the
  calf of the leg" 1325) gives a trajectory.
- Entity resolution merges duplicates within one ingestion run. The same word
  loaded from two sources may exist as two records.

## Configuration

Everything is configured through `.env` (copied from `.env.example`), which
both Docker Compose and the Python code read. The main settings:

| Variable | Purpose |
|---|---|
| `NEO4J_PASSWORD` (`NEO4J_URI`, `NEO4J_USER`) | Graph database; `NEO4J_URI` defaults to `bolt://localhost:7687`. `NEO4J_USER` (default `neo4j`) applies only to a Neo4j outside compose: the bundled container's user is always `neo4j` |
| `ELASTICSEARCH_PASSWORD`, `REDIS_PASSWORD` (or `ELASTICSEARCH_URI`, `REDIS_URI`) | Optional for the Python code, which uses a store only when one of these is set. Without Elasticsearch, search matches substrings in Neo4j; without Redis, responses are not cached and rate-limit counters are kept per process. `/health` reports an unset store as `not_configured`. Docker Compose requires both passwords, even to start only `neo4j` |
| `API_KEY` | When set, requests must send `X-API-Key` (required by the production overlay) |
| `RATE_LIMIT_ENABLED`, `RATE_LIMIT_REQUESTS`, `RATE_LIMIT_WINDOW_SECONDS` | Per-client-IP rate limiting. Behind a reverse proxy, also set `FORWARDED_ALLOW_IPS`, or all clients share one budget |
| `GRAPH_QUERY_ENABLED` | `false` turns off `POST /api/v1/graph/query` (read-only Cypher from API clients); `config/.env.production` sets it to `false` |
| `POSTGRES_URI` | Only if you enable the reserved PostgreSQL profile |

See [config/README.md](config/README.md) for all variables and the
production overlay (`docker-compose.production.yml`).

## Development

```bash
make install-dev   # dependencies + pre-commit hooks
make test          # no databases needed; DB-backed tests are skipped
make test-db-start && make test-db && make test-db-stop   # DB tests on a throwaway Neo4j
make lint type-check format-check
```

Tests never touch your own `.env` or databases: DB-backed tests run only
against the instance named by `TEST_NEO4J_URI`.

Project layout: `src/adapters` (sources), `src/ingestion.py` (the ingestion
driver), `src/pipelines` (validation, resolution, graph writes),
`src/analysis` (dating, contact, drift), `src/api` (REST and GraphQL),
`src/cli.py`, `tests/`.

Further documentation: [architecture](docs/architecture.md),
[data model](docs/data_model.md), [API reference](docs/api-reference.md),
[FAQ](docs/faq.md), [troubleshooting](docs/troubleshooting.md),
[contributing](CONTRIBUTING.md), [security](SECURITY.md). Older plans and
the original design specification are in [docs/archive](docs/archive/README.md).

## License and citation

MIT. See [LICENSE](LICENSE). The WOLD and IDS datasets are published under
CC BY 4.0: cite them when you publish results based on them. To cite
Lexicon itself, see [CITATION.cff](CITATION.cff).
