#!/usr/bin/env python3
"""Load initial reference data into the databases.

Loads:
- Language reference data (from data/languages.json when present, else a
  built-in seed of common languages) into the PostgreSQL `languages` table.
- The WOLD semantic-field taxonomy into data/semantic_fields.json as
  reference data for ingestion and analysis.

Postgres is reached through DatabaseManager (env-configured). When it is
unavailable the language rows are written to data/languages_pending.json
instead so the load can be replayed later.
"""

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

# Add project root to path so we can import src.*
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.adapters.clld import WOLD_SEMANTIC_FIELDS
from src.utils.db import DatabaseManager

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# Built-in seed used when data/languages.json is absent
SEED_LANGUAGES: list[dict[str, Any]] = [
    {"iso_code": "eng", "name": "English", "family": "Indo-European", "status": "living"},
    {"iso_code": "deu", "name": "German", "family": "Indo-European", "status": "living"},
    {"iso_code": "fra", "name": "French", "family": "Indo-European", "status": "living"},
    {"iso_code": "spa", "name": "Spanish", "family": "Indo-European", "status": "living"},
    {"iso_code": "ita", "name": "Italian", "family": "Indo-European", "status": "living"},
    {"iso_code": "por", "name": "Portuguese", "family": "Indo-European", "status": "living"},
    {"iso_code": "nld", "name": "Dutch", "family": "Indo-European", "status": "living"},
    {"iso_code": "rus", "name": "Russian", "family": "Indo-European", "status": "living"},
    {"iso_code": "lat", "name": "Latin", "family": "Indo-European", "status": "extinct"},
    {"iso_code": "grc", "name": "Ancient Greek", "family": "Indo-European", "status": "extinct"},
    {"iso_code": "ang", "name": "Old English", "family": "Indo-European", "status": "extinct"},
    {"iso_code": "fro", "name": "Old French", "family": "Indo-European", "status": "extinct"},
    {"iso_code": "non", "name": "Old Norse", "family": "Indo-European", "status": "extinct"},
    {"iso_code": "got", "name": "Gothic", "family": "Indo-European", "status": "extinct"},
    {
        "iso_code": "gem-pro",
        "name": "Proto-Germanic",
        "family": "Indo-European",
        "status": "reconstructed",
    },
    {
        "iso_code": "ine-pro",
        "name": "Proto-Indo-European",
        "family": "Indo-European",
        "status": "reconstructed",
    },
]


def load_languages() -> list[dict[str, Any]]:
    """Load language reference data from file or the built-in seed."""
    languages_file = DATA_DIR / "languages.json"
    if languages_file.is_file():
        data = json.loads(languages_file.read_text(encoding="utf-8"))
        print(f"Loaded {len(data)} languages from {languages_file}")
        return list(data)
    print(f"Loaded {len(SEED_LANGUAGES)} languages from built-in seed")
    return SEED_LANGUAGES


def write_semantic_fields() -> Path:
    """Write the WOLD semantic-field taxonomy as reference data."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out_path = DATA_DIR / "semantic_fields.json"
    fields = [
        {"field_id": field_id, "label": label}
        for field_id, label in sorted(WOLD_SEMANTIC_FIELDS.items(), key=lambda kv: int(kv[0]))
    ]
    out_path.write_text(json.dumps(fields, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(fields)} semantic fields to {out_path}")
    return out_path


async def insert_languages(languages: list[dict[str, Any]]) -> bool:
    """Insert language rows into the PostgreSQL languages table.

    Returns:
        True if rows were inserted, False if Postgres was unavailable.
    """
    db = DatabaseManager()
    if not await db.connect_postgres():
        return False

    try:
        async with db.postgres_connection() as conn:
            inserted = 0
            for lang in languages:
                await conn.execute(
                    """
                    INSERT INTO languages (id, code, name, family, status)
                    VALUES (gen_random_uuid(), $1, $2, $3, $4)
                    ON CONFLICT (code) DO UPDATE
                        SET name = EXCLUDED.name,
                            family = EXCLUDED.family,
                            status = EXCLUDED.status
                    """,
                    lang["iso_code"],
                    lang["name"],
                    lang.get("family"),
                    lang.get("status", "living"),
                )
                inserted += 1
            print(f"Inserted/updated {inserted} languages in PostgreSQL")
            return True
    finally:
        await db.close_all()


def write_pending_languages(languages: list[dict[str, Any]]) -> Path:
    """Persist language rows locally when Postgres is unreachable."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out_path = DATA_DIR / "languages_pending.json"
    out_path.write_text(json.dumps(languages, indent=2) + "\n", encoding="utf-8")
    return out_path


async def main() -> int:
    """Main entry point."""
    print("Starting initial data load...")

    languages = load_languages()
    write_semantic_fields()

    if await insert_languages(languages):
        print("Initial data load complete!")
        return 0

    pending = write_pending_languages(languages)
    print(
        "PostgreSQL is unavailable; language rows saved to "
        f"{pending} - start the database (make docker-up, make db-migrate) "
        "and re-run this script to load them."
    )
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
