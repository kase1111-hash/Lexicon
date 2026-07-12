#!/usr/bin/env python3
"""Lexicon CLI - command-line interface for the Linguistic Stratigraphy system.

Usage:
    python -m src.cli ingest --words data/seed_words_eng.txt --language eng
    python -m src.cli search --form water --language eng
    python -m src.cli analyze date-text --text "The knight rode forth"
    python -m src.cli analyze anachronisms --text "The knight used a computer" --date 1300
    python -m src.cli validate --form water --language eng
    python -m src.cli stats
"""

import argparse
import logging
import sys
from pathlib import Path

from src.analysis.contact_detection import ContactDetector
from src.analysis.dating import TextDating
from src.analysis.semantic_drift import SemanticDriftAnalyzer
from src.pipelines.relationship_extraction import RelationshipExtractor
from src.pipelines.validation import Validator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("lexicon")


def cmd_ingest(args: argparse.Namespace) -> None:
    """Run the ingestion pipeline."""
    # Import here to avoid circular deps and heavy imports at startup
    from scripts.ingest import load_word_list, run_ingestion, run_wold_ingestion

    if args.source == "wold":
        languages = args.language.split(",") if args.language else None
        stats = run_wold_ingestion(
            data_dir=args.data_dir,
            languages_filter=languages,
            borrowings_only=args.borrowings_only,
            dry_run=args.dry_run,
        )
        print(stats.summary())
        return

    if args.word:
        words = [args.word]
    elif args.words:
        path = Path(args.words)
        if not path.exists():
            print(f"Error: Word list file not found: {path}")
            sys.exit(1)
        words = load_word_list(str(path))
        print(f"Loaded {len(words)} words from {path}")
    else:
        print("Error: Either --words or --word is required")
        sys.exit(1)

    stats = run_ingestion(
        words=words,
        language=args.language,
        dry_run=args.dry_run,
        rate_limit_ms=args.rate_limit,
    )
    print(stats.summary())


def cmd_search(args: argparse.Namespace) -> None:
    """Search the local LSR store (in-memory demo mode)."""
    print(f"Searching for form='{args.form}', language='{args.language or 'any'}'")
    print()
    print("Note: This is a local demo. For full search, use the API server:")
    print(f"  curl 'http://localhost:8000/api/v1/lsr/search?form={args.form}'")


def cmd_analyze(args: argparse.Namespace) -> None:
    """Run analysis commands."""
    if args.analysis_type == "date-text":
        _analyze_date_text(args)
    elif args.analysis_type == "anachronisms":
        _analyze_anachronisms(args)
    elif args.analysis_type == "contact":
        _analyze_contact(args)
    elif args.analysis_type == "drift":
        _analyze_drift(args)
    else:
        print(f"Unknown analysis type: {args.analysis_type}")
        sys.exit(1)


def _analyze_date_text(args: argparse.Namespace) -> None:
    """Date a text based on its vocabulary."""
    text = _get_text(args)
    language = args.language or "eng"

    dater = TextDating()
    result = dater.date_text(text, language)

    print(f"Language: {language}")
    print(f"Text length: {len(text)} chars, {len(text.split())} words")
    print(f"Predicted date range: {result.predicted_range[0]}-{result.predicted_range[1]}")
    print(f"Confidence: {result.confidence:.2f}")
    print(f"Tokens analyzed: {result.analyzed_tokens}")
    print(f"Tokens matched: {result.matched_tokens}")
    print(f"Method: {result.method}")
    if result.diagnostic_vocabulary:
        words = ", ".join(w["word"] for w in result.diagnostic_vocabulary[:10])
        print(f"Diagnostic vocabulary: {words}")


def _analyze_anachronisms(args: argparse.Namespace) -> None:
    """Detect anachronistic vocabulary."""
    text = _get_text(args)
    language = args.language or "eng"
    claimed_date = args.date

    if claimed_date is None:
        print("Error: --date is required for anachronism detection")
        sys.exit(1)

    dater = TextDating()
    result = dater.detect_anachronisms(text, claimed_date, language)

    print(f"Language: {language}")
    print(f"Claimed date: {claimed_date}")
    print(f"Verdict: {result.verdict}")
    print(f"Confidence: {result.confidence:.2f}")
    print(f"Explanation: {result.explanation}")
    if result.anachronisms:
        print(f"Anachronisms found: {len(result.anachronisms)}")
        for a in result.anachronisms[:5]:
            print(f"  - {a}")


def _analyze_contact(args: argparse.Namespace) -> None:
    """Detect language contact events."""
    language = args.language or "eng"

    detector = ContactDetector()
    events = detector.detect_contacts(language)

    print(f"Language: {language}")
    print(f"Contact events detected: {len(events)}")
    for event in events[:5]:
        print(
            f"  {event.donor_language} -> {event.recipient_language}: "
            f"{event.vocabulary_count} words, confidence={event.confidence:.2f}"
        )


def _analyze_drift(args: argparse.Namespace) -> None:
    """Analyze semantic drift for a word."""
    form = args.form
    language = args.language or "eng"

    if not form:
        print("Error: --form is required for drift analysis")
        sys.exit(1)

    analyzer = SemanticDriftAnalyzer()
    result = analyzer.get_trajectory(form, language)

    print(f"Form: {form}")
    print(f"Language: {language}")
    if result is None:
        print("No trajectory data found (load data via ingestion first)")
    else:
        print(f"Trajectory points: {len(result.points)}")
        print(f"Total drift: {result.total_drift:.2f}")
        print(f"Stability score: {result.stability_score:.2f}")
        for point in result.points[:5]:
            print(f"  {point.date}: {point.definition}")


def _get_text(args: argparse.Namespace) -> str:
    """Get text from --text or --file argument."""
    if args.text:
        return str(args.text)
    elif hasattr(args, "file") and args.file:
        path = Path(args.file)
        if not path.exists():
            print(f"Error: File not found: {path}")
            sys.exit(1)
        return path.read_text()
    else:
        print("Error: Either --text or --file is required")
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
    text = args.text
    if not text:
        print("Error: --text is required")
        sys.exit(1)

    extractor = RelationshipExtractor()
    raw_rels = extractor.extract_from_etymology_text(text)

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
    """Show system statistics."""
    print("Lexicon System Statistics")
    print("=" * 40)
    print()
    print("Note: Full stats require a running database.")
    print("Use the API for live statistics:")
    print("  curl http://localhost:8000/health")
    print()
    print("Available components:")
    print("  - Wiktionary adapter: ready")
    print("  - Entity resolution: ready")
    print("  - Relationship extraction: ready")
    print("  - Validation pipeline: ready")
    print("  - Text dating: ready")
    print("  - Contact detection: ready")
    print("  - Semantic drift analysis: ready")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="lexicon",
        description="Lexicon - Computational Linguistic Stratigraphy CLI",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # ingest
    p_ingest = subparsers.add_parser("ingest", help="Ingest from Wiktionary or WOLD")
    p_ingest.add_argument(
        "--source",
        type=str,
        choices=["wiktionary", "wold"],
        default="wiktionary",
        help="Data source",
    )
    p_ingest.add_argument("--words", type=str, help="Path to word list file")
    p_ingest.add_argument("--word", type=str, help="Single word to ingest")
    p_ingest.add_argument("--language", type=str, help="Language filter (e.g. 'English')")
    p_ingest.add_argument("--data-dir", type=str, help="WOLD data directory")
    p_ingest.add_argument("--borrowings-only", action="store_true", help="WOLD: borrowings only")
    p_ingest.add_argument("--dry-run", action="store_true", help="Don't persist")
    p_ingest.add_argument("--rate-limit", type=int, default=100, help="Rate limit (ms)")

    # search
    p_search = subparsers.add_parser("search", help="Search for a word")
    p_search.add_argument("--form", type=str, required=True, help="Word form")
    p_search.add_argument("--language", type=str, help="Language code")

    # analyze
    p_analyze = subparsers.add_parser("analyze", help="Run analysis")
    p_analyze.add_argument(
        "analysis_type", choices=["date-text", "anachronisms", "contact", "drift"]
    )
    p_analyze.add_argument("--text", type=str, help="Text to analyze")
    p_analyze.add_argument("--file", type=str, help="File containing text")
    p_analyze.add_argument("--language", type=str, help="Language code")
    p_analyze.add_argument("--date", type=int, help="Claimed date (for anachronisms)")
    p_analyze.add_argument("--form", type=str, help="Word form (for drift)")

    # validate
    p_validate = subparsers.add_parser("validate", help="Validate an LSR record")
    p_validate.add_argument("--form", type=str, help="Word form")
    p_validate.add_argument("--language", type=str, help="Language code")
    p_validate.add_argument("--date-start", type=int, help="Date start")
    p_validate.add_argument("--date-end", type=int, help="Date end")
    p_validate.add_argument("--definition", type=str, help="Definition")
    p_validate.add_argument("--strict", action="store_true", help="Treat warnings as failures")

    # extract-relationships
    p_extract = subparsers.add_parser("extract-rels", help="Extract relationships from etymology")
    p_extract.add_argument("--text", type=str, required=True, help="Etymology text")

    # stats
    subparsers.add_parser("stats", help="Show system statistics")

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    if args.command == "ingest":
        cmd_ingest(args)
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
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
