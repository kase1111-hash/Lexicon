# Contributing to Lexicon

Bug reports, fixes, new data sources and documentation corrections are
welcome. Open an issue before large changes (a new analysis, a change to the
data model) so the approach can be agreed first.

## Setup

You need Python 3.11 or 3.12 (CI tests both) and Docker for the database
tests.

```bash
git clone https://github.com/kase1111-hash/Lexicon.git
cd Lexicon
python -m venv .venv
. .venv/bin/activate
make install-dev
```

`make install-dev` installs `requirements-dev.txt` (the pinned runtime
dependencies plus the test and lint tools), installs the package in editable
mode, which provides the `lexicon`, `ls-api` and `ls-ingest` commands, and
installs the pre-commit hook. Only the PostgreSQL migrations need more
(`pip install -e '.[postgres]'`).

To run Lexicon itself against your changes, follow the quickstart in the
[README](README.md). [docs/architecture.md](docs/architecture.md) explains
where things live.

## Tests

```bash
make test
```

runs the whole suite without any database. `tests/conftest.py` ignores your
`.env` and points Neo4j, Elasticsearch, Redis and PostgreSQL at unreachable
addresses, so tests that need a database skip themselves; the rest take
about half a minute.

The database tests create and delete nodes. Give them a throwaway Neo4j,
never the one holding your graph:

```bash
make test-db-start   # neo4j:5.9 in container lexicon-test-neo4j on localhost:7688
make test-db         # the whole suite with TEST_NEO4J_URI=bolt://localhost:7688
make test-db-stop
```

`TEST_NEO4J_PORT`, `TEST_NEO4J_PASSWORD` and `TEST_NEO4J_CONTAINER` change the
defaults, for example `make test-db-start TEST_NEO4J_PORT=7782` followed by
`make test-db TEST_NEO4J_PORT=7782`. The `TEST_*` variables are the only way
tests reach a database (see [config/README.md](config/README.md)).

| Directory | Contents |
|---|---|
| `tests/unit/` | Single modules |
| `tests/integration/` | API, pipeline and ingestion tests; the live ones need `TEST_NEO4J_URI` |
| `tests/acceptance/` | User workflows end to end |
| `tests/regression/` | Edge cases and fixed bugs |
| `tests/security/` | Input validation, secrets handling, unsafe code patterns |
| `tests/performance/` | Timing assertions; CI runs them but does not fail on them |

`make test-cov` writes a coverage report to `htmlcov/`.

## Checks CI runs

Every push and pull request to `main` or `develop` runs
`.github/workflows/ci.yml`. Run the same checks locally before opening a pull
request:

| CI job | Checks | Locally |
|---|---|---|
| Lint | `ruff check src tests`, `black src tests --check` | `make lint`, `make format-check` |
| Type Check | `mypy src` (strict settings in `pyproject.toml`) | `make type-check` |
| Security Scan | `bandit -r src -c pyproject.toml`, `pip-audit -r requirements.txt` | `make security-check`, `make audit` |
| Test (3.11, 3.12) | `pytest tests/ --ignore=tests/performance` without databases | `make test` |
| Test with Neo4j | The same against a Neo4j 5.9 service | `make test-db` |
| Test Coverage | Fails below `fail_under` in `pyproject.toml`, currently 69% | `make test-cov` |
| Docker Image | Builds the image and imports the API, CLI and ingestion modules in it | |
| Build Package | `python -m build` | |

`make pre-commit` runs the hooks from `.pre-commit-config.yaml` (whitespace,
YAML/JSON/TOML syntax, black, ruff, bandit, mypy) on every file; the installed
hook runs them on the files you commit. The black, ruff, mypy and bandit
versions are pinned in `requirements-dev.txt` and match CI and the hooks.

## Code

- Format with black (line length 100) and keep ruff clean. mypy requires type
  hints on every function.
- Follow [docs/style-guide.md](docs/style-guide.md).
- Database failures must reach the caller as `DatabaseError` (HTTP 503), not
  as an empty result. Analyses must report coverage and return
  `insufficient_data` rather than a verdict the data cannot support.
- New sources are `SourceAdapter` subclasses; the
  [FAQ](docs/faq.md#how-do-i-add-a-source) shows one.
- Changes to the LSR fields or relationship types go in
  [docs/data_model.md](docs/data_model.md). The Neo4j constraints and indexes
  are created by `LSRRepository.ensure_schema()`, which must stay idempotent;
  there is no migration tool for the graph (the alembic migrations are for
  the optional PostgreSQL only).
- Add tests with every change, and never point them at a database that holds
  data you want to keep.

## Documentation

Keep documentation to what the code does now. Command examples and API
responses in the docs must come from a real run; shorten long output with
`…`, but do not invent fields or values. Say plainly what does not work.

## Pull requests

1. Branch from `main`, keep the change focused, and write commit messages in
   the imperative ("Add corpus sidecar validation").
2. Run the checks above and update the documentation your change affects,
   including [CHANGELOG.md](CHANGELOG.md) under `Unreleased`.
3. Open the pull request with the template and link the issue it addresses.

Report bugs and request features with the
[issue templates](https://github.com/kase1111-hash/Lexicon/issues/new/choose).
Security problems go through [SECURITY.md](SECURITY.md), not public issues.
