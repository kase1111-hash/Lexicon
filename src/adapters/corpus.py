"""Corpus adapter for historical text corpora.

Reads dated plain-text documents from a local corpus directory and emits
one RawLexicalEntry per distinct word, carrying attestations (source
document, date, excerpt) for the dating and anachronism pipelines.

Corpus layout:
    corpus_dir/
        some_text.txt            # document body
        some_text.json           # optional per-document metadata
        metadata.json            # optional corpus-wide metadata (by filename)

Per-document metadata keys (all optional): title, date (year, int),
date_confidence, language, language_code, source, url.
"""

import json
import logging
import re
import unicodedata
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

from .base import RawLexicalEntry, SourceAdapter

logger = logging.getLogger(__name__)

_WORD_RE = re.compile(r"[^\W\d_]+(?:['-][^\W\d_]+)*", re.UNICODE)

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

        words: dict[str, dict[str, Any]] = {}
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

        self._entries = [self._build_entry(word, info) for word, info in sorted(words.items())]
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
        meta.setdefault("language", self.language)
        meta.setdefault("language_code", self.language_code)
        date = meta.get("date")
        if date is not None:
            try:
                meta["date"] = int(date)
            except (ValueError, TypeError):
                logger.warning(f"Ignoring non-numeric date {date!r} in {doc_path.name}")
                meta["date"] = None
        return meta

    def _collect_words(
        self, text: str, meta: dict[str, Any], words: dict[str, dict[str, Any]]
    ) -> None:
        """Accumulate word occurrences from one document into `words`."""
        normalized_text = unicodedata.normalize("NFC", text)
        seen_in_doc: set[str] = set()
        for match in _WORD_RE.finditer(normalized_text):
            word = match.group().lower()
            if len(word) < self.min_word_length:
                continue

            info = words.setdefault(
                word,
                {"count": 0, "attestations": [], "languages": set()},
            )
            info["count"] += 1
            info["languages"].add((meta["language"], meta["language_code"]))

            # One attestation per document per word (the first occurrence)
            if word not in seen_in_doc:
                seen_in_doc.add(word)
                start = max(0, match.start() - _EXCERPT_RADIUS)
                end = min(len(normalized_text), match.end() + _EXCERPT_RADIUS)
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
        dates = [a["text_date"] for a in info["attestations"] if a["text_date"] is not None]
        return RawLexicalEntry(
            source_id=f"corpus-{language_code}-{word}",
            source_name="corpus",
            form=word,
            language=language,
            language_code=language_code,
            attestations=info["attestations"],
            date_attested=min(dates) if dates else None,
            raw_data={
                "corpus_type": self.corpus_type,
                "occurrence_count": info["count"],
                "document_count": len(info["attestations"]),
            },
        )
