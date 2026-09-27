"""Corpus adapter for historical text corpora.

Reads dated plain-text documents from a local corpus directory and emits
one RawLexicalEntry per distinct word, carrying attestations (source
document, date, excerpt) for the dating and anachronism pipelines.

Corpus layout:
    corpus_dir/
        some_text.txt            # document body
        some_text.json           # optional per-document metadata
        metadata.json            # optional corpus-wide metadata (by filename)

Per-document metadata keys (all optional): title, date (year, int,
-10000..2100; negative for BCE), date_confidence (0-1; a word dated by
this document gets it), language, language_code, source, url.
"""

import json
import logging
import unicodedata
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

from src.models.lsr import YEAR_MAX, YEAR_MIN
from src.utils.languages import UNDETERMINED_LANGUAGE, code_for_name
from src.utils.text import word_spans

from .base import RawLexicalEntry, SourceAdapter

logger = logging.getLogger(__name__)

# Number of characters of context kept around a word's first occurrence
_EXCERPT_RADIUS = 60


class CorpusAdapter(SourceAdapter):
    """Adapter for historical text corpora stored as local dated documents."""

    def __init__(
        self,
        corpus_dir: str | Path = "data/corpus",
        language: str = "English",
        language_code: str = "eng",
        min_word_length: int = 3,
        corpus_type: str = "generic",
        metadata_source: str | None = None,
    ):
        """Initialize the corpus adapter.

        Args:
            corpus_dir: Directory containing .txt documents (and optional
                .json metadata sidecars).
            language: Default language name for documents that don't
                specify one in metadata.
            language_code: Default ISO 639-3 code.
            min_word_length: Words shorter than this are skipped.
            corpus_type: Free-form corpus label recorded on entries.
            metadata_source: Optional path to a corpus-wide metadata JSON
                file (defaults to corpus_dir/metadata.json).
        """
        super().__init__()
        self.corpus_dir = Path(corpus_dir)
        self.language = language
        self.language_code = language_code
        self.min_word_length = min_word_length
        self.corpus_type = corpus_type
        self.metadata_source = metadata_source
        self._entries: list[RawLexicalEntry] = []
        self._last_modified: datetime | None = None

    @property
    def name(self) -> str:
        return "Corpus"

    def connect(self) -> None:
        """Scan the corpus directory and build word entries.

        Raises:
            ConnectionError: If the corpus directory does not exist.
        """
        if not self.corpus_dir.is_dir():
            raise ConnectionError(f"Corpus directory not found: {self.corpus_dir}")

        corpus_meta = self._load_corpus_metadata()
        documents = sorted(self.corpus_dir.glob("*.txt"))
        if not documents:
            logger.warning(f"No .txt documents found in {self.corpus_dir}")

        # Keyed by (language_code, word): the same spelling in two languages
        # (e.g. Middle and Modern English) is two different words
        words: dict[tuple[str, str], dict[str, Any]] = {}
        latest_mtime = 0.0
        for doc_path in documents:
            latest_mtime = max(latest_mtime, doc_path.stat().st_mtime)
            meta = self._document_metadata(doc_path, corpus_meta)
            try:
                text = doc_path.read_text(encoding="utf-8", errors="replace")
            except OSError as e:
                logger.warning(f"Could not read corpus document {doc_path}: {e}")
                continue
            self._collect_words(text, meta, words)

        self._entries = [self._build_entry(word, info) for (_, word), info in sorted(words.items())]
        self._last_modified = (
            datetime.fromtimestamp(latest_mtime) if latest_mtime else datetime.now()
        )
        self._connected = True
        logger.info(
            f"Corpus adapter connected: {len(self._entries)} distinct words "
            f"from {len(documents)} documents in {self.corpus_dir}"
        )

    def disconnect(self) -> None:
        """Release loaded corpus data."""
        self._entries = []
        self._connected = False

    def fetch_batch(self, offset: int, limit: int) -> Iterator[RawLexicalEntry]:
        """Fetch a batch of corpus word entries."""
        if not self._connected:
            raise RuntimeError("Adapter not connected. Call connect() first.")
        yield from self._entries[offset : offset + limit]

    def get_total_count(self) -> int:
        """Return the number of distinct words found in the corpus."""
        return len(self._entries)

    def get_last_modified(self) -> datetime:
        """Return the newest document modification time."""
        return self._last_modified or datetime.now()

    def supports_incremental(self) -> bool:
        """Local corpora are re-scanned in full; no incremental updates."""
        return False

    def _load_corpus_metadata(self) -> dict[str, dict[str, Any]]:
        """Load corpus-wide metadata (filename -> metadata dict)."""
        path = (
            Path(self.metadata_source)
            if self.metadata_source
            else self.corpus_dir / "metadata.json"
        )
        if not path.is_file():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            logger.warning(f"Could not parse corpus metadata {path}: {e}")
            return {}
        return data if isinstance(data, dict) else {}

    def _document_metadata(
        self, doc_path: Path, corpus_meta: dict[str, dict[str, Any]]
    ) -> dict[str, Any]:
        """Merge per-document sidecar metadata over corpus-wide metadata."""
        meta: dict[str, Any] = dict(corpus_meta.get(doc_path.name, {}))
        sidecar = doc_path.with_suffix(".json")
        if sidecar.is_file():
            try:
                sidecar_data = json.loads(sidecar.read_text(encoding="utf-8"))
                if isinstance(sidecar_data, dict):
                    meta.update(sidecar_data)
            except (OSError, json.JSONDecodeError) as e:
                logger.warning(f"Could not parse metadata sidecar {sidecar}: {e}")

        meta.setdefault("title", doc_path.stem)
        meta.setdefault("source", f"{self.corpus_type}:{doc_path.name}")
        if meta.get("language") and not meta.get("language_code"):
            # A document in another language than --language: its words must
            # not get the default code
            meta["language_code"] = code_for_name(meta["language"]) or UNDETERMINED_LANGUAGE
            if meta["language_code"] == UNDETERMINED_LANGUAGE:
                logger.warning(
                    f"{doc_path.name}: unknown language {meta['language']!r} and no "
                    "language_code; its words get 'und' (set language_code in the sidecar)"
                )
        meta.setdefault("language", self.language)
        meta.setdefault("language_code", self.language_code)
        date = meta.get("date")
        if date is not None:
            try:
                meta["date"] = int(date)
            except (ValueError, TypeError):
                logger.warning(f"Ignoring non-numeric date {date!r} in {doc_path.name}")
                meta["date"] = None
            else:
                if not YEAR_MIN <= meta["date"] <= YEAR_MAX:
                    logger.warning(
                        f"Ignoring date {date!r} in {doc_path.name}: outside "
                        f"{YEAR_MIN}..{YEAR_MAX}; the document is treated as undated"
                    )
                    meta["date"] = None
        confidence = meta.get("date_confidence")
        if confidence is not None:
            try:
                valid = 0.0 <= float(confidence) <= 1.0
            except (ValueError, TypeError):
                valid = False
            if valid:
                meta["date_confidence"] = float(confidence)
            else:
                logger.warning(
                    f"Ignoring date_confidence {confidence!r} in {doc_path.name}: "
                    "expected a number from 0 to 1"
                )
                del meta["date_confidence"]
        return meta

    def _collect_words(
        self, text: str, meta: dict[str, Any], words: dict[tuple[str, str], dict[str, Any]]
    ) -> None:
        """Accumulate word occurrences from one document into `words`."""
        normalized_text = unicodedata.normalize("NFC", text)
        seen_in_doc: set[str] = set()
        # The analyses' word boundaries, so combining marks stay in the word
        for match_start, match_end in word_spans(normalized_text):
            word = normalized_text[match_start:match_end].lower()
            if len(word) < self.min_word_length:
                continue

            info = words.setdefault(
                (meta["language_code"], word),
                {"count": 0, "attestations": [], "languages": set()},
            )
            info["count"] += 1
            info["languages"].add((meta["language"], meta["language_code"]))

            # One attestation per document per word (the first occurrence)
            if word not in seen_in_doc:
                seen_in_doc.add(word)
                start = max(0, match_start - _EXCERPT_RADIUS)
                end = min(len(normalized_text), match_end + _EXCERPT_RADIUS)
                excerpt = " ".join(normalized_text[start:end].split())
                info["attestations"].append(
                    {
                        "text_excerpt": excerpt,
                        "text_source": meta["source"],
                        "text_date": meta.get("date"),
                        "text_date_confidence": meta.get("date_confidence", 1.0),
                        "url": meta.get("url"),
                        "title": meta.get("title"),
                    }
                )

    def _build_entry(self, word: str, info: dict[str, Any]) -> RawLexicalEntry:
        """Build a RawLexicalEntry from accumulated word data."""
        language, language_code = sorted(info["languages"])[0]
        dated = [a for a in info["attestations"] if a["text_date"] is not None]
        earliest = min(dated, key=lambda a: a["text_date"]) if dated else None
        dates = [a["text_date"] for a in dated]
        # The date says when the word appears in this corpus, which is its
        # first attestation only as far as the corpus reaches
        label = f"earliest in corpus: {earliest['title']}" if earliest else ""
        raw_data: dict[str, Any] = {
            "corpus_type": self.corpus_type,
            "occurrence_count": info["count"],
            "document_count": len(info["attestations"]),
            "period_label": label,
        }
        if earliest:
            # How sure the earliest document's own date is
            raw_data["date_confidence"] = earliest["text_date_confidence"]
        return RawLexicalEntry(
            source_id=f"corpus-{language_code}-{word}",
            source_name="corpus",
            form=word,
            language=language,
            language_code=language_code,
            attestations=info["attestations"],
            date_attested=min(dates) if dates else None,
            raw_data=raw_data,
        )
