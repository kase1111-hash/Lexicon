#!/usr/bin/env python3
"""Load initial reference data into the databases."""

import asyncio
import json
from pathlib import Path


async def load_languages():
    """Load language reference data."""
    print("Loading language reference data...")
    # TODO: Load from Glottolog or similar source
    # For now, load a subset of common languages
    languages = [
        {"iso_code": "eng", "name": "English", "family": "Indo-European"},
        {"iso_code": "deu", "name": "German", "family": "Indo-European"},
        {"iso_code": "fra", "name": "French", "family": "Indo-European"},
        {"iso_code": "spa", "name": "Spanish", "family": "Indo-European"},
        {"iso_code": "ita", "name": "Italian", "family": "Indo-European"},
        {"iso_code": "lat", "name": "Latin", "family": "Indo-European"},
        {"iso_code": "grc", "name": "Ancient Greek", "family": "Indo-European"},
        {"iso_code": "ang", "name": "Old English", "family": "Indo-European"},
        {"iso_code": "fro", "name": "Old French", "family": "Indo-European"},
    ]
    print(f"Loaded {len(languages)} languages")
    return languages


async def load_semantic_fields():
    """Load semantic field reference data from WordNet."""
    print("Loading semantic field reference data...")
    # TODO: Load from WordNet
    print("Semantic field loading not yet implemented")
    return []


async def main():
    """Main entry point."""
    print("Starting initial data load...")

    languages = await load_languages()
    semantic_fields = await load_semantic_fields()

    # TODO: Insert into databases
    print("Database insertion not yet implemented")

    print("Initial data load complete!")


if __name__ == "__main__":
    asyncio.run(main())
