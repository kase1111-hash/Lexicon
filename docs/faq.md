# Frequently Asked Questions (FAQ)

## General Questions

### What is Linguistic Stratigraphy?

Linguistic Stratigraphy is an automated system for building and querying a cross-linguistic lexical evolution graph. It ingests etymological data from multiple sources, constructs a unified knowledge graph, and provides analysis tools for historical linguistics research.

### What can I use it for?

- **Text Dating**: Estimate when a historical text was written based on vocabulary
- **Forgery Detection**: Identify anachronistic words in documents claiming historical origin
- **Etymology Tracing**: Track word origins across languages and time periods
- **Language Contact Analysis**: Detect borrowing patterns between languages
- **Semantic Drift**: Analyze how word meanings change over time

### What languages are supported?

The system supports any language with available etymological data. Primary coverage includes:
- Major Indo-European languages (English, French, German, Spanish, Latin, Greek, etc.)
- Languages with extensive Wiktionary coverage
- Languages in the CLLD/CLICS databases

### Is this free to use?

Yes, the project is open-source under the MIT license. You can use, modify, and distribute it freely.

---

## Installation Questions

### What are the system requirements?

- **Python**: 3.11 or higher
- **Memory**: 16GB RAM recommended (8GB minimum)
- **Storage**: 50GB+ for full database
- **Docker**: Latest version with Docker Compose

### Why is Docker required?

Docker simplifies running the multiple database services (Neo4j, PostgreSQL, Elasticsearch, Redis) needed by the system. You can run without Docker if you install and configure each service manually.

### Can I run this on Windows?

Yes, with either:
1. Docker Desktop for Windows
2. WSL2 (Windows Subsystem for Linux) - recommended
3. Native installation (more complex)

### Installation fails with "out of memory" error

The default Docker configuration allocates limited memory. Increase Docker's memory limit:
- Docker Desktop: Settings → Resources → Memory → Set to 8GB+
- Docker Engine: Edit `/etc/docker/daemon.json`

---

## API Questions

### How do I authenticate API requests?

By default, authentication is disabled for development. In production:

```bash
curl -H "X-API-Key: your-api-key" http://localhost:8000/api/v1/lsr/search?form=water
```

Configure the API key in your `.env` file:
```
API_KEY=your-secure-api-key
```

### What's the difference between REST and GraphQL endpoints?

- **REST** (`/api/v1/lsr/*`, `/api/v1/analyze/*`, `/api/v1/graph/*`): Traditional endpoints, simpler queries
- **GraphQL** (`/graphql`): Flexible queries, fetch exactly what you need

Use REST for simple operations, GraphQL for complex nested queries.

### What are the rate limits?

Default limits:
- 100 requests per minute per API key
- Configurable via `RATE_LIMIT_REQUESTS` and `RATE_LIMIT_WINDOW_SECONDS`

### How do I get all LSRs for a language?

```bash
# REST
curl "http://localhost:8000/api/v1/lsr/search?language=eng&limit=100"
```

---

## Data Questions

### What is an LSR (Lexical State Record)?

An LSR represents a word at a specific point in time and language. It captures:
- Form (written and phonetic)
- Language
- Date range of usage
- Definitions
- Relationships (ancestors, cognates, borrowings)

### How fresh is the data?

Data freshness depends on how often you run the ingestion pipeline. Use the CLI or Make targets to ingest data:

```bash
make ingest-wiktionary   # Ingest from Wiktionary
make ingest-clld         # Ingest from CLLD database
```

### Can I import my own data?

Yes, use the ingestion API or write a custom adapter:

```python
from src.adapters.base import BaseAdapter

class MyAdapter(BaseAdapter):
    async def fetch_entries(self):
        # Your data fetching logic
        yield entry
```

### How accurate is text dating?

Accuracy depends on:
- Text length (longer is better)
- Language coverage in the database
- Time period (well-documented periods are more accurate)

Typical accuracy: ±50-100 years for English texts 1500-1900.

---

## Development Questions

### How do I run tests?

```bash
# All tests
make test

# Specific test types
make test-unit         # Unit tests only
make test-cov          # With coverage report
pytest tests/security  # Security tests only
```

### How do I add a new analysis module?

1. Create a module in `src/analysis/`
2. Implement the analysis logic
3. Add API endpoint in `src/api/routes/analysis.py`
4. Write tests in `tests/unit/`

### How do I contribute?

1. Fork the repository
2. Create a feature branch
3. Make changes with tests
4. Run `make check` to verify
5. Submit a pull request

See [CONTRIBUTING.md](contributing.md) for detailed guidelines.

### Where are the logs?

- **Development**: stdout (colorized)
- **Production**: JSON format to stdout, optionally to file

Configure in `.env`:
```
LOG_LEVEL=DEBUG
LOG_FORMAT=text  # or "json"
LOG_FILE=/var/log/ls/app.log
```

---

## Performance Questions

### The API is slow, how can I improve it?

1. **Enable caching**: Ensure Redis is running
2. **Add indexes**: Check database indexes are created
3. **Limit results**: Use `limit` parameter in queries
4. **Use GraphQL**: Fetch only needed fields

### How much memory does it need?

Minimum requirements (per docker-compose.yml resource limits):
- Neo4j: 3GB (2GB heap)
- Elasticsearch: 2GB (1GB heap)
- PostgreSQL: 1GB
- Redis: 512MB
- API: 1GB

Total: ~8GB minimum, 16GB recommended

### Can I run on a smaller machine?

Yes, with reduced configuration:
1. Reduce Neo4j and Elasticsearch heap sizes in `docker-compose.yml`
2. Use external hosted database services
3. Run only the services you need (e.g., skip Elasticsearch if full-text search is not required)

---

## Troubleshooting

See [troubleshooting.md](troubleshooting.md) for common issues and solutions.

---

## Getting Help

- **Documentation**: [docs/](.)
- **Issues**: [GitHub Issues](https://github.com/kase1111-hash/Lexicon/issues)
- **Discussions**: [GitHub Discussions](https://github.com/kase1111-hash/Lexicon/discussions)
