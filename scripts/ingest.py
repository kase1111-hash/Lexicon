#!/usr/bin/env python3
"""Ingestion script: multi-source -> EntityResolver -> LSR store.

Supports Wiktionary and WOLD (World Loanword Database) as data sources.
Runs validation and relationship extraction on ingested entries.

Usage:
    python scripts/ingest.py --words data/seed_words_eng.txt --language eng
    python scripts/ingest.py --source wold --data-dir data/wold
    python scripts/ingest.py --source wold --borrowings-only
    python scripts/ingest.py --word water --language eng --dry-run
"""

import argparse
import logging
import sys
import time
from pathlib import Path
from uuid import UUID

# Add project root to path so we can import src.*
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.adapters.base import RawLexicalEntry
from src.adapters.wiktionary import WiktionaryAdapter
from src.models.lsr import LSR
from src.pipelines.entity_resolution import EntityResolver, ResolutionAction, convert_entry_to_lsr
from src.pipelines.relationship_extraction import RelationshipExtractor
from src.pipelines.validation import ValidationResult, Validator


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("ingest")


class IngestionStats:
    """Track ingestion statistics."""

    def __init__(self) -> None:
        self.words_attempted = 0
        self.words_fetched = 0
        self.words_failed = 0
        self.entries_fetched = 0
        self.lsrs_created = 0
        self.lsrs_merged = 0
        self.lsrs_flagged = 0
        self.lsrs_rejected = 0
        self.relationships_extracted = 0
        self.errors: list[str] = []
        self.start_time = time.time()

    @property
    def elapsed(self) -> float:
        return time.time() - self.start_time

    def summary(self) -> str:
        lines = [
            "",
            "=" * 60,
            "INGESTION SUMMARY",
            "=" * 60,
            f"  Words attempted:     {self.words_attempted}",
            f"  Words fetched:       {self.words_fetched}",
            f"  Words failed:        {self.words_failed}",
            f"  Entries fetched:     {self.entries_fetched}",
            f"  LSRs created:        {self.lsrs_created}",
            f"  LSRs merged:         {self.lsrs_merged}",
            f"  LSRs flagged:        {self.lsrs_flagged}",
            f"  LSRs rejected:       {self.lsrs_rejected}",
            f"  Relationships:       {self.relationships_extracted}",
            f"  Elapsed time:        {self.elapsed:.1f}s",
            f"  Rate:                {self.words_attempted / max(self.elapsed, 0.1):.1f} words/sec",
        ]
        if self.errors:
            lines.append(f"  Errors ({len(self.errors)}):")
            for err in self.errors[:20]:
                lines.append(f"    - {err}")
            if len(self.errors) > 20:
                lines.append(f"    ... and {len(self.errors) - 20} more")
        lines.append("=" * 60)
        return "\n".join(lines)


def load_word_list(path: str) -> list[str]:
    """Load words from a file, one per line. Strips comments and blanks."""
    words = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                words.append(line)
    return words


def run_ingestion(
    words: list[str],
    language: str | None = None,
    dry_run: bool = False,
    rate_limit_ms: int = 100,
    validate: bool = True,
    extract_relationships: bool = True,
) -> IngestionStats:
    """
    Run the Wiktionary ingestion pipeline.

    Args:
        words: List of words to ingest.
        language: If set, only ingest entries for this language (e.g. "English").
        dry_run: If True, fetch and resolve but don't persist.
        rate_limit_ms: Milliseconds between Wiktionary API requests.
        validate: If True, run validation on each entry before creating.
        extract_relationships: If True, run relationship extraction after.

    Returns:
        IngestionStats with counts and errors.
    """
    stats = IngestionStats()

    # Set up adapter
    languages = [language] if language else None
    adapter = WiktionaryAdapter(
        languages_to_process=languages,
        rate_limit_ms=rate_limit_ms,
    )

    # Set up entity resolver
    resolver = EntityResolver(
        auto_merge_threshold=0.95,
        merge_with_flag_threshold=0.85,
        review_threshold=0.70,
    )
    lsr_store: dict[UUID, LSR] = {}
    resolver.set_lsr_store(lsr_store)

    # Set up optional pipelines
    validator = Validator() if validate else None
    rel_extractor = RelationshipExtractor() if extract_relationships else None

    # Connect and fetch
    adapter.connect()
    try:
        for word in words:
            stats.words_attempted += 1
            try:
                entries = adapter.fetch_word(word)
                if entries:
                    stats.words_fetched += 1
                else:
                    stats.words_failed += 1
                    logger.debug(f"No entries found for '{word}'")
                    continue

                for entry in entries:
                    stats.entries_fetched += 1
                    _process_entry(
                        entry, resolver, lsr_store, stats,
                        dry_run, validator,
                    )

            except Exception as e:
                stats.words_failed += 1
                msg = f"Failed to fetch '{word}': {e}"
                stats.errors.append(msg)
                logger.warning(msg)

            # Progress logging every 50 words
            if stats.words_attempted % 50 == 0:
                logger.info(
                    f"Progress: {stats.words_attempted}/{len(words)} words, "
                    f"{stats.lsrs_created} created, {stats.lsrs_merged} merged"
                )

    finally:
        adapter.disconnect()

    # Post-ingestion: extract relationships
    if extract_relationships and rel_extractor and lsr_store:
        stats.relationships_extracted = _extract_relationships(
            lsr_store, rel_extractor
        )

    return stats


def run_wold_ingestion(
    data_dir: str | None = None,
    languages_filter: list[str] | None = None,
    borrowings_only: bool = False,
    dry_run: bool = False,
    validate: bool = True,
) -> IngestionStats:
    """Run the WOLD (World Loanword Database) ingestion pipeline.

    Args:
        data_dir: Directory containing WOLD CSV files.
        languages_filter: Optional list of language names to include.
        borrowings_only: If True, only ingest entries with borrowing evidence.
        dry_run: If True, resolve but don't persist.
        validate: If True, run validation.

    Returns:
        IngestionStats with counts and errors.
    """
    from src.adapters.clld import CLLDAdapter

    stats = IngestionStats()

    adapter = CLLDAdapter(
        data_dir=data_dir,
        languages_filter=languages_filter,
    )

    resolver = EntityResolver(
        auto_merge_threshold=0.95,
        merge_with_flag_threshold=0.85,
        review_threshold=0.70,
    )
    lsr_store: dict[UUID, LSR] = {}
    resolver.set_lsr_store(lsr_store)

    validator = Validator() if validate else None

    adapter.connect()
    try:
        if borrowings_only:
            entries_iter = adapter.fetch_borrowings()
        else:
            entries_iter = adapter.fetch_all(batch_size=500)

        for entry in entries_iter:
            stats.words_attempted += 1
            stats.entries_fetched += 1

            try:
                _process_entry(
                    entry, resolver, lsr_store, stats,
                    dry_run, validator,
                )
            except Exception as e:
                stats.words_failed += 1
                msg = f"Failed to process WOLD entry '{entry.form}': {e}"
                stats.errors.append(msg)
                logger.warning(msg)

            if stats.words_attempted % 500 == 0:
                logger.info(
                    f"WOLD progress: {stats.words_attempted} entries, "
                    f"{stats.lsrs_created} created, {stats.lsrs_merged} merged"
                )

    finally:
        adapter.disconnect()

    return stats


def _process_entry(
    entry: RawLexicalEntry,
    resolver: EntityResolver,
    lsr_store: dict[UUID, LSR],
    stats: IngestionStats,
    dry_run: bool,
    validator: Validator | None = None,
) -> None:
    """Process a single entry through validation and entity resolution."""
    # Validate before resolution
    if validator:
        lsr_dict = {
            "form_orthographic": entry.form,
            "language_code": entry.language_code,
            "definition_primary": entry.definitions[0] if entry.definitions else "",
            "source_databases": [entry.source_name],
        }
        report = validator.run_all(lsr_dict)
        if report.result == ValidationResult.FAIL:
            stats.lsrs_rejected += 1
            logger.debug(
                f"Rejected: {entry.form} ({entry.language_code}): "
                f"{[i['message'] for i in report.issues]}"
            )
            return

    result = resolver.resolve(entry)

    if result.action == ResolutionAction.CREATE_NEW:
        lsr = convert_entry_to_lsr(entry)
        if not dry_run:
            lsr_store[lsr.id] = lsr
            resolver.set_lsr_store(lsr_store)
        stats.lsrs_created += 1
        logger.debug(
            f"Created LSR: {lsr.form_orthographic} ({lsr.language_code}) "
            f"[{lsr.language_name}]"
        )

    elif result.action == ResolutionAction.AUTO_MERGE:
        if result.existing_id and not dry_run:
            existing = lsr_store.get(result.existing_id)
            if existing:
                new_lsr = convert_entry_to_lsr(entry)
                resolver.merge_lsrs(existing, new_lsr)
        stats.lsrs_merged += 1
        logger.debug(
            f"Merged: {entry.form} ({entry.language_code}) -> "
            f"existing {result.existing_id} (score={result.similarity_score:.2f})"
        )

    elif result.action in (ResolutionAction.MERGE_WITH_FLAG, ResolutionAction.FLAG_FOR_REVIEW):
        lsr = convert_entry_to_lsr(entry)
        if not dry_run:
            lsr_store[lsr.id] = lsr
            resolver.set_lsr_store(lsr_store)
        stats.lsrs_flagged += 1
        logger.debug(
            f"Flagged: {entry.form} ({entry.language_code}) "
            f"score={result.similarity_score:.2f}, action={result.action}"
        )


def _extract_relationships(
    lsr_store: dict[UUID, LSR],
    extractor: RelationshipExtractor,
) -> int:
    """Run relationship extraction on all LSRs in the store.

    Returns the number of relationships extracted.
    """
    extractor.set_lsr_store(lsr_store)
    lsr_ids = list(lsr_store.keys())
    relationships = extractor.process_new_lsrs(lsr_ids)

    logger.info(f"Extracted {len(relationships)} relationships from {len(lsr_ids)} LSRs")
    return len(relationships)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest lexical data from multiple sources into the LSR store.",
    )
    parser.add_argument(
        "--source",
        type=str,
        choices=["wiktionary", "wold"],
        default="wiktionary",
        help="Data source to ingest from (default: wiktionary)",
    )
    parser.add_argument(
        "--words",
        type=str,
        help="Path to word list file (Wiktionary source, one word per line)",
    )
    parser.add_argument(
        "--word",
        type=str,
        help="Single word to ingest (Wiktionary source)",
    )
    parser.add_argument(
        "--language",
        type=str,
        default=None,
        help="Language to filter (e.g. 'English'). If not set, all languages are ingested.",
    )
    parser.add_argument(
        "--data-dir",
        type=str,
        default=None,
        help="Directory for WOLD CSV data files (default: data/wold)",
    )
    parser.add_argument(
        "--borrowings-only",
        action="store_true",
        help="WOLD: only ingest entries with borrowing evidence",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and resolve but don't persist to store",
    )
    parser.add_argument(
        "--no-validate",
        action="store_true",
        help="Skip validation pipeline",
    )
    parser.add_argument(
        "--no-relationships",
        action="store_true",
        help="Skip relationship extraction",
    )
    parser.add_argument(
        "--rate-limit",
        type=int,
        default=100,
        help="Milliseconds between API requests (default: 100)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose (DEBUG) logging",
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    if args.source == "wold":
        # WOLD ingestion
        languages = args.language.split(",") if args.language else None
        mode = "DRY RUN" if args.dry_run else "LIVE"
        logger.info(f"Starting WOLD ingestion ({mode})")

        stats = run_wold_ingestion(
            data_dir=args.data_dir,
            languages_filter=languages,
            borrowings_only=args.borrowings_only,
            dry_run=args.dry_run,
            validate=not args.no_validate,
        )
    else:
        # Wiktionary ingestion
        if args.word:
            words = [args.word]
        elif args.words:
            path = Path(args.words)
            if not path.exists():
                logger.error(f"Word list file not found: {path}")
                sys.exit(1)
            words = load_word_list(str(path))
            logger.info(f"Loaded {len(words)} words from {path}")
        else:
            logger.error("Either --words or --word is required for Wiktionary source")
            parser.print_help()
            sys.exit(1)

        if not words:
            logger.error("No words to process")
            sys.exit(1)

        mode = "DRY RUN" if args.dry_run else "LIVE"
        lang_desc = args.language or "all languages"
        logger.info(
            f"Starting Wiktionary ingestion ({mode}): "
            f"{len(words)} words, language={lang_desc}"
        )

        stats = run_ingestion(
            words=words,
            language=args.language,
            dry_run=args.dry_run,
            rate_limit_ms=args.rate_limit,
            validate=not args.no_validate,
            extract_relationships=not args.no_relationships,
        )

    print(stats.summary())


if __name__ == "__main__":
    main()
