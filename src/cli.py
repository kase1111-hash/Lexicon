#!/usr/bin/env python3
"""Lexicon CLI - command-line interface for the Linguistic Stratigraphy system.

Every command except `validate` and `extract-rels` works against the Neo4j
graph configured by NEO4J_URI / NEO4J_PASSWORD (environment or .env).

Usage:
    lexicon ingest --source wold --language English
    lexicon search --form sky --language eng
    lexicon analyze anachronisms --text "The knight spoke on the telephone" --date 1300
    lexicon analyze date-text --text "The knight rode forth under the sky"
    lexicon analyze contact --language eng
    lexicon analyze drift --form nice --language eng
    lexicon stats
    lexicon reindex
    lexicon validate --form water --language eng
    lexicon extract-rels --text "From Old English wæter"
"""

import argparse
import asyncio
import json
import logging
import os
import sys
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any, NoReturn, TypeVar

from neo4j.exceptions import DriverError, Neo4jError

from src.exceptions import DatabaseError
from src.pipelines.relationship_extraction import RelationshipExtractor
from src.pipelines.validation import Validator
from src.utils.db import DatabaseManager

logger = logging.getLogger("lexicon")

T = TypeVar("T")


def _graph_failed(db: DatabaseManager, message: str) -> NoReturn:
    """Report a Neo4j failure after connecting and exit with status 2."""
    print(
        f"Error: {message} (Neo4j at {db.config.neo4j_uri}); no result was produced.",
        file=sys.stderr,
    )
    sys.exit(2)


def _run_with_graph(fn: Callable[[DatabaseManager], Awaitable[T]], search: bool = False) -> T:
    """Connect to Neo4j (and optionally Elasticsearch), run fn, and close.

    Exits with status 2 and a clear message when Neo4j is unreachable, or
    fails, times out or drops the connection while fn runs.
    """

    async def runner() -> T:
        db = DatabaseManager()
        if not await db.connect_neo4j():
            error = db.get_connection_errors().get("neo4j", "unknown error")
            print(
                f"Error: cannot reach Neo4j at {db.config.neo4j_uri}: {error}\n"
                "Start it with `docker compose up -d neo4j` and check NEO4J_URI / "
                "NEO4J_PASSWORD in .env.",
                file=sys.stderr,
            )
            sys.exit(2)
        if search and db.config.elasticsearch_configured:
            # Unreachable: search falls back to Neo4j substring matching
            await db.connect_elasticsearch(quiet=True)
        try:
            return await fn(db)
        except DatabaseError as e:
            _graph_failed(db, e.message)
        except (DriverError, Neo4jError) as e:
            _graph_failed(db, f"Neo4j query failed: {e}")
        finally:
            await db.close_all()

    return asyncio.run(runner())


def _print_json(data: Any) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False, default=str))


def _language(value: str | None) -> str:
    """Normalize a --language value (ISO 639-3, or common 639-1)."""
    from src.utils.validation import normalize_language_code

    try:
        return normalize_language_code(value or "eng")
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_ingest(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    """Run the ingestion pipeline (same options as `python -m src.ingestion`)."""
    from src.ingestion import run_cli

    run_cli(args, parser)


def cmd_search(args: argparse.Namespace) -> None:
    """Search the graph for LSRs by form."""
    from src.repositories.lsr_repository import LSRRepository

    language = _language(args.language) if args.language else None

    async def search(db: DatabaseManager) -> tuple[list, int]:
        return await LSRRepository(db).search(form=args.form, language=language, limit=args.limit)

    results, total = _run_with_graph(search, search=True)
    if args.json:
        _print_json({"total": total, "results": [lsr.model_dump(mode="json") for lsr in results]})
        return

    print(f"{total} match(es) for '{args.form}'" + (f" in {language}" if language else ""))
    for lsr in results:
        dates = f"{lsr.date_start if lsr.date_start is not None else '?'}-"
        dates += str(lsr.date_end) if lsr.date_end is not None else "present"
        gloss = f" '{lsr.definition_primary}'" if lsr.definition_primary else ""
        print(f"  {lsr.form_orthographic} [{lsr.language_code}] {dates}{gloss}  id={lsr.id}")


def cmd_analyze(args: argparse.Namespace) -> None:
    """Run an analysis against the graph."""
    handlers = {
        "date-text": _analyze_date_text,
        "anachronisms": _analyze_anachronisms,
        "contact": _analyze_contact,
        "drift": _analyze_drift,
    }
    handlers[args.analysis_type](args)


def _analyze_date_text(args: argparse.Namespace) -> None:
    """Date a text based on its vocabulary."""
    from src.analysis.data_access import load_vocabulary_for_text
    from src.analysis.dating import TextDating

    text = _get_text(args)
    language = _language(args.language)

    async def analyze(db: DatabaseManager) -> Any:
        lookup = await load_vocabulary_for_text(db, language, text)
        return TextDating(lsr_lookup=lookup).date_text(text, language)

    result = _run_with_graph(analyze)
    if args.json:
        _print_json(asdict(result))
        return

    print(f"Status: {result.status}")
    if result.predicted_range:
        print(f"Estimated date range: {result.predicted_range[0]}-{result.predicted_range[1]}")
    print(f"Confidence: {result.confidence:.2f}")
    print(f"Dated words: {result.matched_tokens} of {result.content_tokens} content words")
    print(result.explanation)
    if result.unknown_words:
        print(f"Not in graph: {', '.join(result.unknown_words[:15])}")


def _analyze_anachronisms(args: argparse.Namespace) -> None:
    """Detect anachronistic vocabulary."""
    from src.analysis.data_access import load_vocabulary_for_text
    from src.analysis.dating import TextDating

    if args.date is None:
        print("Error: --date is required for anachronism detection", file=sys.stderr)
        sys.exit(1)
    text = _get_text(args)
    language = _language(args.language)

    async def analyze(db: DatabaseManager) -> Any:
        lookup = await load_vocabulary_for_text(db, language, text)
        return TextDating(lsr_lookup=lookup).detect_anachronisms(text, args.date, language)

    result = _run_with_graph(analyze)
    if args.json:
        _print_json(asdict(result))
        return

    print(f"Verdict: {result.verdict} (confidence {result.confidence:.2f})")
    print(f"Dated words: {result.dated_tokens} of {result.content_tokens} content words")
    print(result.explanation)
    for a in result.anachronisms[:10]:
        if a["type"] == "coined_after":
            year = a["earliest_attestation"]
            label = a.get("date_label") or ""
            source = f"{label}; " if label and label != str(year) else ""
            print(
                f"  - {a['word']}: first attested {year} "
                f"({source}{a['gap_years']} years after {args.date}, {a['severity']})"
            )
        else:
            print(f"  - {a['word']}: last attested {a['last_attestation']} (possible archaism)")
    if result.unknown_words:
        print(f"Not in graph: {', '.join(result.unknown_words[:15])}")


def _analyze_contact(args: argparse.Namespace) -> None:
    """Detect language contact events from borrowing edges."""
    from src.analysis.contact_detection import ContactDetector
    from src.analysis.data_access import load_borrowings

    language = _language(args.language)

    async def load(db: DatabaseManager) -> list[dict[str, Any]]:
        return await load_borrowings(db, language)

    borrowings = _run_with_graph(load)
    names = {b["source_lang"]: b["source_lang_name"] for b in borrowings}
    events = ContactDetector(borrowing_data=borrowings).detect_contacts(language)
    if args.json:
        _print_json([asdict(e) for e in events])
        return

    dated = sum(1 for b in borrowings if b.get("date") is not None)
    print(f"{len(borrowings)} borrowing edges involving '{language}', {dated} of them dated")
    if not borrowings:
        print("No BORROWED_FROM edges in the graph; ingest WOLD data first.")
    elif not dated:
        print("Contact events are dated by the borrowed words' first attestations; none are dated.")
    print(f"Contact events: {len(events)}")
    for e in events[:15]:
        donor = names.get(e.donor_language) or e.donor_language
        print(
            f"  {donor} -> {e.recipient_language} {e.date_range[0]}-{e.date_range[1]}: "
            f"{e.vocabulary_count} words (e.g. {', '.join(e.sample_words[:5])}), "
            f"confidence {e.confidence:.2f}"
        )


def _analyze_drift(args: argparse.Namespace) -> None:
    """Analyze semantic drift for a word."""
    from src.analysis.data_access import load_trajectory
    from src.analysis.semantic_drift import drift_report

    if not args.form:
        print("Error: --form is required for drift analysis", file=sys.stderr)
        sys.exit(1)
    language = _language(args.language)

    async def load(db: DatabaseManager) -> list[dict[str, Any]]:
        return await load_trajectory(db, args.form, language)

    report = drift_report(args.form, language, _run_with_graph(load))
    if args.json:
        _print_json(report)
        return

    if report["status"] != "ok":
        print(f"{report['status']}: {report['explanation']}")
        return

    print(f"Senses compared: {len(report['trajectory'])}")
    print(f"Total drift: {report['total_drift']:.2f}  Stability: {report['stability_score']:.2f}")
    for point in report["trajectory"]:
        print(f"  {point['date']}: {point['definition']}")
    for event in report["shift_events"]:
        print(
            f"  shift at {event['date']}: {event['before_meaning']!r} -> "
            f"{event['after_meaning']!r}"
        )


def _get_text(args: argparse.Namespace) -> str:
    """Get text from --text or --file argument."""
    if args.text:
        return str(args.text)
    if getattr(args, "file", None):
        path = Path(args.file)
        if not path.exists():
            print(f"Error: File not found: {path}", file=sys.stderr)
            sys.exit(1)
        return path.read_text()
    print("Error: Either --text or --file is required", file=sys.stderr)
    sys.exit(1)


def cmd_validate(args: argparse.Namespace) -> None:
    """Validate an LSR record."""
    lsr_data = {
        "form_orthographic": args.form or "",
        "language_code": args.language or "",
        "date_start": args.date_start,
        "date_end": args.date_end,
        "definition_primary": args.definition or "",
        "source_databases": ["cli-test"],
    }

    validator = Validator(strict=args.strict)
    report = validator.run_all(lsr_data)

    print(f"Validation result: {report.result.value}")
    print(f"Validators run: {', '.join(report.validators_run)}")
    if report.issues:
        print(f"Issues ({len(report.issues)}):")
        for issue in report.issues:
            print(f"  [{issue['severity']}] {issue['field']}: {issue['message']}")
    if report.recommendations:
        print("Recommendations:")
        for rec in report.recommendations:
            print(f"  - {rec}")


def cmd_extract_relationships(args: argparse.Namespace) -> None:
    """Extract relationships from etymology text."""
    extractor = RelationshipExtractor()
    raw_rels = extractor.extract_from_etymology_text(args.text)

    print(f"Extracted {len(raw_rels)} relationships:")
    for rel in raw_rels:
        marker = "*" if rel.is_reconstructed else ""
        print(
            f"  {rel.relationship_type.value}: -> {marker}{rel.target_form} "
            f"({rel.target_language}/{rel.target_language_code}) "
            f"confidence={rel.confidence:.1f}"
        )
        print(f"    Evidence: {rel.evidence}")


def cmd_stats(args: argparse.Namespace) -> None:
    """Show what is in the graph."""
    from src.repositories.lsr_repository import LSRRepository

    async def stats(db: DatabaseManager) -> dict[str, Any]:
        return await LSRRepository(db).get_statistics()

    # A failed query raises DatabaseError (exit 2 in _run_with_graph); an
    # "error" key is the older way of reporting it, never printed as a result
    data = _run_with_graph(stats)
    if "error" in data:
        print(f"Error: {data['error']}", file=sys.stderr)
        sys.exit(2)
    if args.json:
        _print_json(data)
        return

    print(f"LSRs: {data.get('total_lsrs', 0)}")
    print(f"Relationships: {data.get('total_relationships', 0)}")
    for key, value in data.items():
        if key.startswith("rel_") and value:
            print(f"  {key[4:].upper()}: {value}")
    by_language = data.get("by_language", {})
    if by_language:
        top = ", ".join(f"{lang}={n}" for lang, n in list(by_language.items())[:10])
        print(f"Top languages: {top}")
    if not data.get("total_lsrs"):
        print("The graph is empty. Load data with: lexicon ingest --source wold --language English")


def cmd_reindex(args: argparse.Namespace) -> None:
    """Rebuild the Elasticsearch search index from the graph.

    Afterwards the API's cached searches are cleared (as after the API's own
    reindex), since they were answered from the index as it was before.
    """
    from src.pipelines.graph_writer import _clear_api_cache
    from src.repositories.lsr_repository import LSRRepository

    async def reindex(db: DatabaseManager) -> Any:
        result = await LSRRepository(db).reindex_all_to_elasticsearch()
        await _clear_api_cache(db, owns_db=True)
        return result

    result = _run_with_graph(reindex, search=True)
    if result.errors:
        print(f"Error: {'; '.join(result.errors[:3])}", file=sys.stderr)
        sys.exit(1)
    print(f"Indexed {result.succeeded} LSRs ({result.failed} failed)")


def main() -> None:
    """Entry point of the `lexicon` command."""
    try:
        _main()
    except BrokenPipeError:
        # The output went to a reader that stopped early (`lexicon ... | head`):
        # silence the flush at exit instead of printing a traceback
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
        sys.exit(1)


def _main() -> None:
    from src.ingestion import add_arguments as add_ingest_arguments

    parser = argparse.ArgumentParser(
        prog="lexicon",
        description="Lexicon - date a text by its words: ingest dated lexical data into a "
        "graph, then date texts, flag anachronisms and find language-contact events.",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    p_ingest = subparsers.add_parser("ingest", help="Load a data source into the graph")
    add_ingest_arguments(p_ingest)

    p_search = subparsers.add_parser("search", help="Search the graph for a word")
    p_search.add_argument("--form", type=str, required=True, help="Word form")
    p_search.add_argument("--language", type=str, help="Language code (e.g. eng)")
    p_search.add_argument("--limit", type=int, default=20, help="Maximum results")
    p_search.add_argument("--json", action="store_true", help="Print JSON")

    p_analyze = subparsers.add_parser("analyze", help="Run an analysis against the graph")
    p_analyze.add_argument(
        "analysis_type", choices=["date-text", "anachronisms", "contact", "drift"]
    )
    p_analyze.add_argument("--text", type=str, help="Text to analyze")
    p_analyze.add_argument("--file", type=str, help="File containing text")
    p_analyze.add_argument("--language", type=str, help="Language code (default: eng)")
    p_analyze.add_argument("--date", type=int, help="Claimed date (for anachronisms)")
    p_analyze.add_argument("--form", type=str, help="Word form (for drift)")
    p_analyze.add_argument("--json", action="store_true", help="Print JSON")

    p_validate = subparsers.add_parser("validate", help="Validate an LSR record")
    p_validate.add_argument("--form", type=str, help="Word form")
    p_validate.add_argument("--language", type=str, help="Language code")
    p_validate.add_argument("--date-start", type=int, help="Date start")
    p_validate.add_argument("--date-end", type=int, help="Date end")
    p_validate.add_argument("--definition", type=str, help="Definition")
    p_validate.add_argument("--strict", action="store_true", help="Treat warnings as failures")

    p_extract = subparsers.add_parser("extract-rels", help="Extract relationships from etymology")
    p_extract.add_argument("--text", type=str, required=True, help="Etymology text")

    p_stats = subparsers.add_parser("stats", help="Show what is in the graph")
    p_stats.add_argument("--json", action="store_true", help="Print JSON")

    subparsers.add_parser("reindex", help="Rebuild the Elasticsearch index from the graph")

    args = parser.parse_args()

    # Importing src.utils configures logging at INFO; keep query commands
    # quiet and show progress for ingestion.
    logging.getLogger().setLevel(logging.INFO if args.command == "ingest" else logging.WARNING)

    if args.command == "ingest":
        cmd_ingest(args, p_ingest)
    elif args.command == "search":
        cmd_search(args)
    elif args.command == "analyze":
        cmd_analyze(args)
    elif args.command == "validate":
        cmd_validate(args)
    elif args.command == "extract-rels":
        cmd_extract_relationships(args)
    elif args.command == "stats":
        cmd_stats(args)
    elif args.command == "reindex":
        cmd_reindex(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
