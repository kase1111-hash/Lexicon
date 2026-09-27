"""Ingestion pipeline: source adapter -> validation -> entity resolution -> Neo4j.

Supports Wiktionary, WOLD (World Loanword Database), CLICS/CLDF wordlists
and local dated corpora. Entries are validated, resolved against each
other, turned into LSRs, linked (WOLD donor words become BORROWED_FROM
edges) and written to the Neo4j graph. ``--dry-run`` does everything
except the graph write.

Usage:
    python -m src.ingestion --source wold --language English
    python -m src.ingestion --source wold --borrowings-only
    python -m src.ingestion --words data/seed_words_eng.txt --language eng
    python -m src.ingestion --word water --language English,French --dry-run
"""

import argparse
import asyncio
import logging
import sys
import time
from pathlib import Path
from typing import Any
from uuid import UUID, uuid5

from src.adapters.base import RawLexicalEntry
from src.adapters.wiktionary import WiktionaryAdapter
from src.models.lsr import LSR, DateSource
from src.pipelines.embedding import EmbeddingPipeline
from src.pipelines.entity_resolution import (
    LSR_ID_NAMESPACE,
    EntityResolver,
    ResolutionAction,
    convert_entry_to_lsr,
    resolve_language_code,
)
from src.pipelines.graph_writer import GraphUnavailableError, write_to_graph
from src.pipelines.relationship_extraction import ExtractedRelationship, RelationshipExtractor
from src.pipelines.validation import ValidationResult, Validator
from src.utils.languages import (
    CODE_TO_LANGUAGE,
    LANGUAGE_CODE_MAP,
    UNDETERMINED_LANGUAGE,
    graph_language_code,
    language_name,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("ingest")

# Shared encoder for semantic vectors generated during ingestion
_embedder = EmbeddingPipeline()


class IngestionStats:
    """Track ingestion statistics."""

    def __init__(self, source: str = "wiktionary") -> None:
        self.source = source
        self.words_attempted = 0
        self.words_fetched = 0
        self.words_failed = 0
        self.entries_fetched = 0
        self.lsrs_created = 0
        self.lsrs_merged = 0
        self.lsrs_flagged = 0
        self.lsrs_rejected = 0
        self.lsrs_dated = 0
        # Placeholder LSRs made for donors/ancestors that are not in this
        # run; they are written fill-only so they never blank a real record
        self.placeholder_ids: set[UUID] = set()
        self.relationships_extracted = 0
        self.dry_run = False
        self.lsrs_written = 0
        self.lsrs_failed = 0
        self.relationships_written = 0
        self.relationships_failed = 0
        self.search_index_updated = False
        self.search_index_failed = 0
        self.errors: list[str] = []
        self.start_time = time.time()

    @property
    def elapsed(self) -> float:
        return time.time() - self.start_time

    @property
    def donor_lsrs_created(self) -> int:
        """Number of placeholder LSRs made for linked donors and ancestors."""
        return len(self.placeholder_ids)

    @property
    def write_failed(self) -> bool:
        """True when part of a live graph write failed."""
        return bool(self.lsrs_failed or self.relationships_failed)

    def summary(self) -> str:
        lines = [
            "",
            "=" * 60,
            f"INGESTION SUMMARY ({self.source})",
            "=" * 60,
        ]
        if self.source == "wiktionary":
            lines += [
                f"  Words attempted:     {self.words_attempted}",
                f"  Words fetched:       {self.words_fetched}",
                f"  Words failed:        {self.words_failed}",
            ]
        lines += [
            f"  Entries fetched:     {self.entries_fetched}",
            f"  LSRs created:        {self.lsrs_created}",
            f"  LSRs merged:         {self.lsrs_merged}",
            f"  LSRs flagged:        {self.lsrs_flagged}",
            f"  LSRs rejected:       {self.lsrs_rejected}",
            f"  LSRs with dates:     {self.lsrs_dated}",
            f"  Donor LSRs added:    {self.donor_lsrs_created}",
            f"  Relationships:       {self.relationships_extracted}",
        ]
        if self.dry_run:
            lines.append("  Graph write:         skipped (--dry-run)")
        else:
            lines.append(
                f"  Written to graph:    {self.lsrs_written} LSRs, "
                f"{self.relationships_written} relationships"
            )
            if self.write_failed:
                lines.append(
                    f"  Failed to write:     {self.lsrs_failed} LSRs, "
                    f"{self.relationships_failed} relationships"
                )
            if self.search_index_failed:
                search_index = f"{self.search_index_failed} LSRs not indexed; run `lexicon reindex`"
            elif self.search_index_updated:
                search_index = "updated"
            else:
                search_index = "not configured or unreachable"
            lines.append(f"  Search index:        {search_index}")
        lines += [
            f"  Elapsed time:        {self.elapsed:.1f}s",
            f"  Rate:                {self.entries_fetched / max(self.elapsed, 0.1):.1f} entries/sec",
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


def _new_resolver(lsr_store: dict[UUID, LSR]) -> EntityResolver:
    """Create the entity resolver used by every ingestion run."""
    resolver = EntityResolver(
        auto_merge_threshold=0.95,
        merge_with_flag_threshold=0.85,
        review_threshold=0.70,
    )
    resolver.set_lsr_store(lsr_store)
    return resolver


def run_ingestion(
    words: list[str],
    language: str | list[str] | None = None,
    dry_run: bool = False,
    rate_limit_ms: int = 100,
    validate: bool = True,
    extract_relationships: bool = True,
) -> IngestionStats:
    """
    Run the Wiktionary ingestion pipeline.

    Args:
        words: List of words to ingest.
        language: If set, only ingest entries for these languages: names,
            ISO 639-3 or ISO 639-1 codes, as a list or a comma-separated
            string (e.g. "English", "eng", ["en", "French"]).
        dry_run: If True, fetch and resolve but don't write to the graph.
        rate_limit_ms: Milliseconds between Wiktionary API requests.
        validate: If True, run validation on each entry before creating.
        extract_relationships: If True, run relationship extraction after.

    Returns:
        IngestionStats with counts and errors.

    Raises:
        GraphUnavailableError: If not a dry run and Neo4j is unreachable.
    """
    stats = IngestionStats("wiktionary")

    if isinstance(language, str):
        language = language.split(",")
    adapter = WiktionaryAdapter(
        languages_to_process=language or None,
        rate_limit_ms=rate_limit_ms,
    )
    lsr_store: dict[UUID, LSR] = {}
    resolver = _new_resolver(lsr_store)
    validator = Validator() if validate else None
    resolved: list[tuple[RawLexicalEntry, UUID]] = []

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
                    lsr_id = _process_entry(entry, resolver, lsr_store, stats, validator)
                    if lsr_id is not None:
                        resolved.append((entry, lsr_id))

            except Exception as e:
                stats.words_failed += 1
                msg = f"Failed to fetch '{word}': {e}"
                stats.errors.append(msg)
                logger.warning(msg)

            if stats.words_attempted % 50 == 0:
                logger.info(
                    f"Progress: {stats.words_attempted}/{len(words)} words, "
                    f"{stats.lsrs_created} created, {stats.lsrs_merged} merged"
                )

    finally:
        adapter.disconnect()

    relationships, linked = _build_source_relationships(resolved, lsr_store, stats)
    if extract_relationships and lsr_store:
        # Etymology text is only parsed for entries without structured links,
        # so the two sources don't produce conflicting edges
        unlinked = [lsr_id for lsr_id in lsr_store if lsr_id not in linked]
        relationships.extend(
            _extract_relationship_records(lsr_store, RelationshipExtractor(), unlinked)
        )
    stats.relationships_extracted = len(relationships)

    _write_results(lsr_store, relationships, stats, dry_run)
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
        languages_filter: Optional list of language names, ISO 639-3 or
            ISO 639-1 codes, or Glottolog codes to include.
        borrowings_only: If True, only ingest entries with borrowing evidence.
        dry_run: If True, resolve but don't write to the graph.
        validate: If True, run validation.

    Returns:
        IngestionStats with counts and errors.

    Raises:
        GraphUnavailableError: If not a dry run and Neo4j is unreachable.
    """
    from src.adapters.clld import CLLDAdapter

    adapter = CLLDAdapter(data_dir=data_dir, languages_filter=languages_filter)
    adapter.connect()
    entries = adapter.fetch_borrowings() if borrowings_only else adapter.fetch_all(batch_size=500)
    return _run_entries(adapter, entries, "WOLD", dry_run=dry_run, validate=validate)


def _run_adapter_ingestion(
    adapter: "object",
    source_label: str,
    dry_run: bool = False,
    validate: bool = True,
    batch_size: int = 500,
) -> IngestionStats:
    """Run the standard ingestion loop over any SourceAdapter."""
    from src.adapters.base import SourceAdapter

    assert isinstance(adapter, SourceAdapter)
    adapter.connect()
    entries = adapter.fetch_all(batch_size=batch_size)
    return _run_entries(adapter, entries, source_label, dry_run=dry_run, validate=validate)


def _run_entries(
    adapter: "object",
    entries: Any,
    source_label: str,
    dry_run: bool,
    validate: bool,
) -> IngestionStats:
    """Resolve an iterator of entries from a connected adapter and write the result."""
    stats = IngestionStats(source_label)
    lsr_store: dict[UUID, LSR] = {}
    resolver = _new_resolver(lsr_store)
    validator = Validator() if validate else None
    resolved: list[tuple[RawLexicalEntry, UUID]] = []

    try:
        for entry in entries:
            stats.entries_fetched += 1
            try:
                lsr_id = _process_entry(entry, resolver, lsr_store, stats, validator)
                if lsr_id is not None:
                    resolved.append((entry, lsr_id))
            except Exception as e:
                msg = f"Failed to process {source_label} entry '{entry.form}': {e}"
                stats.errors.append(msg)
                logger.warning(msg)

            if stats.entries_fetched % 5000 == 0:
                logger.info(
                    f"{source_label} progress: {stats.entries_fetched} entries, "
                    f"{stats.lsrs_created} created, {stats.lsrs_merged} merged"
                )
    finally:
        adapter.disconnect()  # type: ignore[attr-defined]

    relationships, _ = _build_source_relationships(resolved, lsr_store, stats)
    stats.relationships_extracted = len(relationships)
    _write_results(lsr_store, relationships, stats, dry_run)
    return stats


def run_clics_ingestion(
    data_dir: str | None = None,
    languages_filter: list[str] | None = None,
    colexified_only: bool = False,
    dry_run: bool = False,
    validate: bool = True,
) -> IngestionStats:
    """Run the CLICS colexification ingestion pipeline.

    Args:
        data_dir: Directory containing CLDF wordlist CSV files.
        languages_filter: Optional list of language names, ISO 639-3 or
            ISO 639-1 codes, or Glottolog codes to include.
        colexified_only: If True, only ingest forms expressing 2+ concepts.
        dry_run: If True, resolve but don't write to the graph.
        validate: If True, run validation.

    Returns:
        IngestionStats with counts and errors.
    """
    from src.adapters.clics import CLICSAdapter

    adapter = CLICSAdapter(
        data_dir=data_dir,
        languages_filter=languages_filter,
        min_colexifications=2 if colexified_only else 1,
    )
    return _run_adapter_ingestion(adapter, "CLICS", dry_run=dry_run, validate=validate)


def run_corpus_ingestion(
    corpus_dir: str | None = None,
    language: str = "English",
    language_code: str = "eng",
    dry_run: bool = False,
    validate: bool = True,
) -> IngestionStats:
    """Run the historical corpus ingestion pipeline.

    Args:
        corpus_dir: Directory of dated .txt documents (see CorpusAdapter).
        language: Default language name for undated documents.
        language_code: Default ISO 639-3 code.
        dry_run: If True, resolve but don't write to the graph.
        validate: If True, run validation.

    Returns:
        IngestionStats with counts and errors.
    """
    from src.adapters.corpus import CorpusAdapter

    adapter = CorpusAdapter(
        corpus_dir=corpus_dir or "data/corpus",
        language=language,
        language_code=language_code,
    )
    return _run_adapter_ingestion(adapter, "Corpus", dry_run=dry_run, validate=validate)


def _process_entry(
    entry: RawLexicalEntry,
    resolver: EntityResolver,
    lsr_store: dict[UUID, LSR],
    stats: IngestionStats,
    validator: Validator | None = None,
) -> UUID | None:
    """Validate and resolve one entry into the in-memory LSR store.

    Returns:
        The id of the LSR the entry now belongs to (new or merged into),
        or None if validation rejected it.
    """
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
            return None

    result = resolver.resolve(entry)

    if result.action == ResolutionAction.AUTO_MERGE and result.existing_id:
        existing = lsr_store.get(result.existing_id)
        if existing:
            had_date = existing.date_start is not None
            resolver.merge_lsrs(existing, convert_entry_to_lsr(entry))
            # Merged definitions may have changed the semantics
            _embedder.embed_lsr(existing)
            if not had_date and existing.date_start is not None:
                stats.lsrs_dated += 1
            stats.lsrs_merged += 1
            logger.debug(
                f"Merged: {entry.form} ({entry.language_code}) -> "
                f"existing {result.existing_id} (score={result.similarity_score:.2f})"
            )
            return existing.id

    lsr = convert_entry_to_lsr(entry)
    _embedder.embed_lsr(lsr)
    lsr_store[lsr.id] = lsr
    resolver.add_lsr(lsr)
    if lsr.date_start is not None:
        stats.lsrs_dated += 1

    if result.action in (ResolutionAction.MERGE_WITH_FLAG, ResolutionAction.FLAG_FOR_REVIEW):
        stats.lsrs_flagged += 1
        lsr.validation_notes = (
            f"Possible duplicate of {result.existing_id} "
            f"(similarity {result.similarity_score:.2f}, {result.action})"
        )
        logger.debug(
            f"Flagged: {entry.form} ({entry.language_code}) "
            f"score={result.similarity_score:.2f}, action={result.action}"
        )
    else:
        stats.lsrs_created += 1
        logger.debug(
            f"Created LSR: {lsr.form_orthographic} ({lsr.language_code}) [{lsr.language_name}]"
        )
    return lsr.id


# Source link kinds -> (graph relationship type, default confidence).
# "borrowed_from" comes from WOLD donor rows; the others are Wiktionary
# etymology templates ({{inh}}, {{bor}}, {{der}}, {{cal}}, {{cog}}).
_LINK_TYPES: dict[str, tuple[str, float]] = {
    "borrowed_from": ("BORROWED_FROM", 0.5),
    "inh": ("DESCENDS_FROM", 0.9),
    "der": ("DESCENDS_FROM", 0.6),
    "bor": ("BORROWED_FROM", 0.9),
    "cal": ("BORROWED_FROM", 0.6),
    "cog": ("COGNATE_OF", 0.7),
}


def _build_source_relationships(
    resolved: list[tuple[RawLexicalEntry, UUID]],
    lsr_store: dict[UUID, LSR],
    stats: IngestionStats,
) -> tuple[list[dict[str, Any]], set[UUID]]:
    """Turn the links adapters report (``related_forms``) into graph edges.

    - WOLD ``borrowed_from`` items link the borrowing word to its donor.
    - Wiktionary etymology templates form a chain: in "From A, from B,
      from C" each item is the ancestor of the previous one, so edges run
      word -> A -> B -> C. ``{{cog}}`` links the word itself and does not
      advance the chain.

    Each target is an LSR of this run with the same language and normalized
    form when there is one, otherwise an undated placeholder LSR (proto-forms
    marked as reconstructions). A placeholder's id depends only on its
    language and form, so every run and source that links to that word
    shares it; ingesting the word itself later still creates its own record
    (entity resolution works within one run). Placeholder ids are recorded
    in ``stats.placeholder_ids`` so the graph write only fills their gaps.

    Returns:
        (edges ready for the graph, ids of LSRs that got source links)
    """
    by_form: dict[tuple[str, str], UUID] = {
        (lsr.language_code, lsr.form_normalized): lsr.id for lsr in lsr_store.values()
    }
    relationships: list[dict[str, Any]] = []
    seen: set[tuple[UUID, UUID, str]] = set()
    linked: set[UUID] = set()

    def target_for(related: dict[str, Any], entry: RawLexicalEntry) -> UUID | None:
        raw_form = (related.get("form") or "").strip()
        reconstructed = raw_form.startswith("*")
        form = raw_form.lstrip("*").strip()
        code = graph_language_code(related.get("language_code") or "")
        name = (related.get("language") or "").strip() or language_name(code)
        if not form or not (code or name):
            return None
        # A source language named without a code (a WOLD donor such as
        # "Saharan") is ISO 639-3 "und" (undetermined), never an empty code;
        # its name keeps words of different such languages apart
        code = code or LANGUAGE_CODE_MAP.get(name, "")
        language_key = code or f"{UNDETERMINED_LANGUAGE}:{name}"
        target = LSR(
            id=uuid5(LSR_ID_NAMESPACE, f"linked:{language_key}:{LSR._normalize(form)}"),
            form_orthographic=form,
            language_code=code or UNDETERMINED_LANGUAGE,
            language_name=name,
            definition_primary=related.get("meaning") or "",
            reconstruction_flag=reconstructed,
            date_source=DateSource.RECONSTRUCTED if reconstructed else DateSource.ATTESTED,
            date_confidence=0.0,  # undated
            source_databases=[entry.source_name],
        )
        key = (language_key, target.form_normalized)
        if key not in by_form:
            lsr_store[target.id] = target
            by_form[key] = target.id
            stats.placeholder_ids.add(target.id)
        return by_form[key]

    for entry, lsr_id in resolved:
        previous = lsr_id
        for related in entry.related_forms:
            kind = related.get("type")
            if kind not in _LINK_TYPES:
                continue
            rel_type, default_confidence = _LINK_TYPES[kind]
            target_id = target_for(related, entry)
            if target_id is None:
                continue

            source_id = lsr_id if kind in ("cog", "borrowed_from") else previous
            if kind not in ("cog", "borrowed_from"):
                previous = target_id
            if source_id == target_id or (source_id, target_id, rel_type) in seen:
                continue
            seen.add((source_id, target_id, rel_type))
            linked.add(lsr_id)
            relationships.append(
                {
                    "source_id": str(source_id),
                    "target_id": str(target_id),
                    "type": rel_type,
                    "confidence": float(related.get("confidence", default_confidence)),
                    "evidence": related.get("raw_template")
                    or entry.etymology
                    or f"{entry.source_name}:{entry.source_id}",
                }
            )
    return relationships, linked


def _extract_relationships(
    lsr_store: dict[UUID, LSR],
    extractor: RelationshipExtractor,
) -> int:
    """Run relationship extraction on all LSRs in the store.

    Returns the number of relationships extracted.
    """
    return len(_extract_relationship_records(lsr_store, extractor))


def _extract_relationship_records(
    lsr_store: dict[UUID, LSR],
    extractor: RelationshipExtractor,
    lsr_ids: list[UUID] | None = None,
) -> list[dict[str, Any]]:
    """Parse etymology text and return edges ready for the graph."""
    extractor.set_lsr_store(lsr_store)
    lsr_ids = list(lsr_store.keys()) if lsr_ids is None else lsr_ids
    relationships: list[ExtractedRelationship] = extractor.process_new_lsrs(lsr_ids)
    logger.info(f"Extracted {len(relationships)} relationships from {len(lsr_ids)} LSRs")
    return [
        {
            "source_id": str(rel.source_id),
            "target_id": str(rel.target_id),
            "type": str(rel.relationship_type),
            "confidence": rel.confidence,
            "evidence": "; ".join(rel.evidence),
        }
        for rel in relationships
    ]


def _write_results(
    lsr_store: dict[UUID, LSR],
    relationships: list[dict[str, Any]],
    stats: IngestionStats,
    dry_run: bool,
) -> None:
    """Write the resolved LSRs and edges to Neo4j unless this is a dry run.

    Placeholder LSRs (``stats.placeholder_ids``) are written fill-only: a
    node another run already wrote keeps its gloss and provenance.
    """
    stats.dry_run = dry_run
    if dry_run or not lsr_store:
        return

    logger.info(
        f"Writing {len(lsr_store)} LSRs and {len(relationships)} relationships to the graph"
    )
    result = asyncio.run(
        write_to_graph(
            list(lsr_store.values()),
            relationships,
            placeholder_ids=[str(lsr_id) for lsr_id in stats.placeholder_ids],
        )
    )
    stats.lsrs_written = result.lsrs_written
    stats.lsrs_failed = result.lsrs_failed
    stats.relationships_written = result.relationships_written
    stats.relationships_failed = result.relationships_failed
    stats.search_index_updated = result.search_index_available
    stats.search_index_failed = result.search_index_failed
    stats.errors.extend(result.errors)


def _run_from_args(args: argparse.Namespace, words: list[str]) -> IngestionStats:
    """Dispatch a parsed command line to the matching ingestion runner."""
    mode = "DRY RUN" if args.dry_run else "LIVE"
    languages = [lang.strip() for lang in (args.language or "").split(",") if lang.strip()] or None

    if args.source == "wold":
        logger.info(f"Starting WOLD ingestion ({mode})")
        return run_wold_ingestion(
            data_dir=args.data_dir,
            languages_filter=languages,
            borrowings_only=args.borrowings_only,
            dry_run=args.dry_run,
            validate=not args.no_validate,
        )
    if args.source == "clics":
        logger.info(f"Starting CLICS ingestion ({mode})")
        return run_clics_ingestion(
            data_dir=args.data_dir,
            languages_filter=languages,
            colexified_only=args.colexified_only,
            dry_run=args.dry_run,
            validate=not args.no_validate,
        )
    if args.source == "corpus":
        language = (args.language or "English").strip()
        language_code = resolve_language_code(graph_language_code(language))
        if not language_code:
            raise ValueError(f"Unknown corpus language {language!r}; pass an ISO 639-3 code")
        logger.info(f"Starting corpus ingestion ({mode}), language={language_code}")
        return run_corpus_ingestion(
            corpus_dir=args.corpus_dir,
            language=CODE_TO_LANGUAGE.get(language_code, language),
            language_code=language_code,
            dry_run=args.dry_run,
            validate=not args.no_validate,
        )

    lang_desc = ", ".join(languages) if languages else "all languages"
    logger.info(f"Starting Wiktionary ingestion ({mode}): {len(words)} words, language={lang_desc}")
    return run_ingestion(
        words=words,
        language=languages,
        dry_run=args.dry_run,
        rate_limit_ms=args.rate_limit,
        validate=not args.no_validate,
        extract_relationships=not args.no_relationships,
    )


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Add the ingestion options to a parser (shared with `lexicon ingest`)."""
    parser.add_argument(
        "--source",
        type=str,
        choices=["wiktionary", "wold", "clics", "corpus"],
        default="wiktionary",
        help="Data source to ingest from (default: wiktionary; wold needs no API access)",
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
        help="wiktionary, wold, clics: language(s) to include, comma-separated names, "
        "ISO 639-3 or ISO 639-1 codes (e.g. 'English', 'eng,fra' or 'en'; wold and clics "
        "also take Glottolog codes); all languages when not set. corpus: the one language "
        "(name or code) of documents whose metadata names none (default: English).",
    )
    parser.add_argument(
        "--data-dir",
        type=str,
        default=None,
        help="Directory for WOLD/CLICS CSV data files (default: data/<source>)",
    )
    parser.add_argument(
        "--borrowings-only",
        action="store_true",
        help="WOLD: only ingest entries with borrowing evidence",
    )
    parser.add_argument(
        "--colexified-only",
        action="store_true",
        help="CLICS: only ingest forms that express two or more concepts",
    )
    parser.add_argument(
        "--corpus-dir",
        type=str,
        default=None,
        help="Corpus: directory of dated .txt documents (default: data/corpus)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and resolve everything but don't write to the graph",
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
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose (DEBUG) logging",
    )


def run_cli(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    """Run an ingestion from parsed arguments, print the summary, exit non-zero on failure."""
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    words: list[str] = []
    if args.source == "wiktionary":
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

    try:
        stats = _run_from_args(args, words)
    except GraphUnavailableError as e:
        logger.error(str(e))
        sys.exit(2)
    except (ValueError, ConnectionError) as e:
        # Bad options, or a source that cannot be read (missing corpus dir,
        # failed download)
        logger.error(str(e))
        sys.exit(1)

    print(stats.summary())
    if stats.entries_fetched == 0:
        logger.error("Nothing was ingested: the source returned no entries for these options")
        sys.exit(1)
    if not stats.dry_run and stats.write_failed:
        logger.error(
            f"The graph write was incomplete: {stats.lsrs_failed} LSRs and "
            f"{stats.relationships_failed} relationships failed (see Errors above)"
        )
        sys.exit(1)
    if not stats.dry_run and stats.lsrs_written == 0 and stats.lsrs_created + stats.lsrs_flagged:
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest lexical data from a source into the Neo4j graph.",
    )
    add_arguments(parser)
    run_cli(parser.parse_args(), parser)


if __name__ == "__main__":
    main()
