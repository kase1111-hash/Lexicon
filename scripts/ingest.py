#!/usr/bin/env python3
"""Thin wrapper for the packaged ingestion pipeline.

The implementation lives in src/ingestion.py so it ships in the wheel and
backs the ls-ingest console script. This shim keeps the documented
`python scripts/ingest.py ...` invocation (and existing imports) working.

Usage:
    python scripts/ingest.py --words data/seed_words_eng.txt --language eng
    python scripts/ingest.py --source wold --data-dir data/wold
    python scripts/ingest.py --source wold --borrowings-only
    python scripts/ingest.py --word water --language eng --dry-run
"""

import sys
from pathlib import Path

# Add project root to path so we can import src.*
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.ingestion import (  # noqa: E402,F401
    IngestionStats,
    _extract_relationships,
    _process_entry,
    load_word_list,
    main,
    run_ingestion,
    run_wold_ingestion,
)

if __name__ == "__main__":
    main()
