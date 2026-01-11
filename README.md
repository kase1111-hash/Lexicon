# Computational Linguistic Stratigraphy

Automated infrastructure for building and maintaining a cross-linguistic lexical evolution graph.

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)

## Overview

Linguistic Stratigraphy is a system that ingests etymological data from multiple sources, constructs a unified knowledge graph, trains diachronic embeddings, and exposes analysis tools via API. It enables:

- **Temporal Dating**: Date texts by analyzing vocabulary usage patterns
- **Contact Detection**: Identify language contact events and borrowing patterns
- **Semantic Drift Analysis**: Track how word meanings evolve over time
- **Forgery Detection**: Identify anachronistic vocabulary in historical texts
- **Phylogenetic Inference**: Reconstruct language family relationships

## Features

- 🔗 **Unified Knowledge Graph**: Cross-linguistic lexical data across language families
- 🕐 **Diachronic Embeddings**: Time-aware semantic vectors for historical analysis
- 🔍 **Multi-Source Ingestion**: Wiktionary, CLLD/CLICS, historical corpora, OCR
- 🚀 **REST & GraphQL APIs**: Query the graph programmatically
- 📊 **Analysis Pipelines**: Text dating, contact detection, semantic drift
- 🔄 **Automated Updates**: Scheduled ingestion and model retraining

## Quick Start

### Prerequisites

- Python 3.11+
- Docker and Docker Compose
- 16GB+ RAM recommended

### Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/linguistic-stratigraphy/linguistic-stratigraphy.git
   cd linguistic-stratigraphy
   ```

2. **Set up environment**
   ```bash
   # Copy environment template
   cp .env.example .env

   # Edit .env with your settings
   nano .env
   ```

3. **Start services with Docker**
   ```bash
   # Start all services
   make docker-up

   # Or manually
   docker compose up -d
   ```

4. **Install Python dependencies (for development)**
   ```bash
   # Create virtual environment
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate

   # Install dependencies
   make install-dev
   ```

### Verify Installation

```bash
# Check service health
docker compose ps

# Test API
curl http://localhost:8000/health
```

## Usage

### API Endpoints

The REST API is available at `http://localhost:8000/api/v1`:

```bash
# Search for a word
curl "http://localhost:8000/api/v1/lsr/search?form=water&language=eng"

# Get etymology chain
curl "http://localhost:8000/api/v1/lsr/{id}/etymology"

# Analyze text date
curl -X POST "http://localhost:8000/api/v1/analyze/date-text" \
  -H "Content-Type: application/json" \
  -d '{"text": "Your historical text here", "language": "eng"}'

# Detect anachronisms
curl -X POST "http://localhost:8000/api/v1/analyze/detect-anachronisms" \
  -H "Content-Type: application/json" \
  -d '{"text": "Text to analyze", "claimed_date": 1400, "language": "eng"}'
```

### GraphQL

GraphQL endpoint available at `http://localhost:8000/graphql`:

```graphql
query {
  lsr(id: "...") {
    form
    language { name }
    ancestors { form }
    cognates { form language { name } }
  }
}
```

### Python Client

```python
from src.models.lsr import LSR
from src.analysis.dating import TextDatingClassifier

# Initialize classifier
classifier = TextDatingClassifier()

# Date a text
result = classifier.predict_date(
    text="Þis is an olde text with middel englisch wordes",
    language="eng"
)
print(f"Predicted date: {result.date_range}")
print(f"Confidence: {result.confidence}")
```

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           ORCHESTRATION LAYER                               │
│                         (Apache Airflow)                                    │
└─────────────────────────────────────────────────────────────────────────────┘
        │                    │                    │                    │
        ▼                    ▼                    ▼                    ▼
┌───────────────┐  ┌───────────────┐  ┌───────────────┐  ┌───────────────┐
│   INGESTION   │  │  PROCESSING   │  │   TRAINING    │  │   SERVING     │
│   PIPELINE    │  │   PIPELINE    │  │   PIPELINE    │  │   LAYER       │
├───────────────┤  ├───────────────┤  ├───────────────┤  ├───────────────┤
│ • Wiktionary  │  │ • Entity Res. │  │ • Embeddings  │  │ • REST API    │
│ • CLLD/CLICS  │  │ • Dedup       │  │ • Clustering  │  │ • GraphQL     │
│ • Corpora     │  │ • Linking     │  │ • Phylogeny   │  │ • WebSocket   │
│ • OCR Texts   │  │ • Validation  │  │ • Classifiers │  │ • Batch Jobs  │
└───────────────┘  └───────────────┘  └───────────────┘  └───────────────┘
        │                    │                    │                    │
        ▼                    ▼                    ▼                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                            STORAGE LAYER                                    │
├─────────────────┬─────────────────┬─────────────────┬───────────────────────┤
│   Neo4j         │    Milvus       │  Elasticsearch  │     PostgreSQL        │
│   (Graph)       │    (Vectors)    │  (Full-text)    │     (Metadata)        │
└─────────────────┴─────────────────┴─────────────────┴───────────────────────┘
```

## Project Structure

```
linguistic-stratigraphy/
├── src/
│   ├── adapters/        # Data source adapters (Wiktionary, CLLD, etc.)
│   ├── pipelines/       # Processing pipelines
│   ├── models/          # Data models (LSR, Language, Edge)
│   ├── training/        # ML training pipelines
│   ├── analysis/        # Analysis modules
│   ├── api/             # REST & GraphQL API
│   └── utils/           # Utilities
├── tests/               # Unit and integration tests
├── dags/                # Airflow DAG definitions
├── scripts/             # Setup and utility scripts
├── k8s/                 # Kubernetes configurations
└── docs/                # Documentation
```

## Development

### Setup Development Environment

```bash
# Install dev dependencies
make install-dev

# Run pre-commit hooks
make pre-commit
```

### Running Tests

```bash
# All tests
make test

# Unit tests only
make test-unit

# With coverage
make test-cov
```

### Code Quality

```bash
# Format code
make format

# Lint
make lint

# Type check
make type-check

# Security scan
make security-check
```

### Running Locally

```bash
# Start API in development mode
make run-api

# Or with Docker
make docker-up
```

## Data Model

The core data unit is the **Lexical State Record (LSR)**, representing a word at a specific point in time:

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID | Unique identifier |
| `form_orthographic` | String | Written form |
| `form_phonetic` | String | IPA representation |
| `language_code` | String | ISO 639-3 code |
| `date_start/end` | Integer | Attested date range |
| `semantic_vector` | Float[384] | Embedding representation |
| `definition_primary` | String | Main gloss |
| `ancestors` | UUID[] | Inheritance pointers |
| `cognates` | UUID[] | Related forms in other languages |

See [docs/data_model.md](docs/data_model.md) for complete schema.

## Configuration

Key environment variables (see `.env.example`):

| Variable | Description | Default |
|----------|-------------|---------|
| `NEO4J_URI` | Neo4j connection URI | `bolt://localhost:7687` |
| `POSTGRES_URI` | PostgreSQL connection URI | `postgresql://...` |
| `ELASTICSEARCH_URI` | Elasticsearch URI | `http://localhost:9200` |
| `MILVUS_HOST` | Milvus vector DB host | `localhost` |
| `LOG_LEVEL` | Logging level | `INFO` |

## Documentation

- [API Documentation](docs/api.md)
- [Architecture Overview](docs/architecture.md)
- [Data Model Reference](docs/data_model.md)
- [Contributing Guide](docs/contributing.md)
- [Style Guide](docs/style-guide.md)
- [Research Proposal](docs/research-proposal.md)

## Contributing

We welcome contributions! Please see [CONTRIBUTING.md](docs/contributing.md) for guidelines.

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Make your changes
4. Run tests (`make test`)
5. Commit (`git commit -m 'Add amazing feature'`)
6. Push (`git push origin feature/amazing-feature`)
7. Open a Pull Request

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Acknowledgments

- [Wiktionary](https://www.wiktionary.org/) for etymological data
- [CLLD/CLICS](https://clics.clld.org/) for cross-linguistic data
- [Hamilton et al. (2016)](https://arxiv.org/abs/1605.09096) for diachronic embedding methodology

## Citation

If you use this project in your research, please cite:

```bibtex
@software{linguistic_stratigraphy,
  title = {Computational Linguistic Stratigraphy},
  author = {Linguistic Stratigraphy Team},
  year = {2024},
  url = {https://github.com/linguistic-stratigraphy/linguistic-stratigraphy}
}
```
