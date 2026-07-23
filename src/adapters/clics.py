"""CLICS adapter for cross-linguistic colexification data.

CLICS (the Database of Cross-Linguistic Colexifications) is computed from
CLDF wordlists: whenever one language uses the same form for two concepts,
those concepts are "colexified" (e.g. many languages colexify HAND and ARM).

This adapter consumes any CLDF wordlist (forms.csv / languages.csv /
parameters.csv - the same layout the WOLD adapter uses), groups forms by
(language, normalized form), and emits one RawLexicalEntry per group with
all colexified concepts as definitions. Colexification data feeds semantic
field assignment and semantic drift analysis.

Data can be supplied locally via data_dir, or downloaded from a
CLDF-on-GitHub base URL (defaults to the lexibank IDS dataset, one of the
wordlists CLICS itself aggregates).
"""

import csv
import logging
import time
import unicodedata
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from .base import RawLexicalEntry, SourceAdapter

logger = logging.getLogger(__name__)


class CLICSAdapter(SourceAdapter):
    """Adapter producing colexification entries from CLDF wordlist data.

    Usage:
        adapter = CLICSAdapter(data_dir="data/clics")
        adapter.connect()  # loads (or downloads) CLDF CSV files
        for entry in adapter.fetch_batch(0, 100):
            ...  # entry.raw_data["colexified_concepts"] lists shared senses
        adapter.disconnect()
    """

    DEFAULT_BASE_URL = (
        "https://raw.githubusercontent.com/intercontinental-dictionary-series/ids/master/cldf"
    )
    CLDF_FILES = {
        "forms": "forms.csv",
        "languages": "languages.csv",
        "parameters": "parameters.csv",
    }

    def __init__(
        self,
        data_dir: str | Path | None = None,
        base_url: str | None = None,
        languages_filter: list[str] | None = None,
        min_colexifications: int = 1,
    ):
        """Initialize the CLICS adapter.

        Args:
            data_dir: Directory containing CLDF CSV files; downloaded from
                base_url when missing.
            base_url: Base URL of a CLDF dataset's raw files.
            languages_filter: Optional list of language names to include.
            min_colexifications: Minimum number of distinct concepts a form
                must express to be emitted (1 = every form, 2 = only true
                colexifications).
        """
        super().__init__()
        self.data_dir = Path(data_dir) if data_dir else Path("data/clics")
        self.base_url = base_url or self.DEFAULT_BASE_URL
        self.languages_filter = languages_filter
        self.min_colexifications = max(1, min_colexifications)
        self._languages: dict[str, dict[str, str]] = {}
        self._parameters: dict[str, dict[str, str]] = {}
        self._entries: list[RawLexicalEntry] = []
        self._client: httpx.Client | None = None

    @property
    def name(self) -> str:
        return "CLICS"

    def connect(self) -> None:
        """Load CLDF data (downloading missing files) and compute colexifications."""
        self._client = httpx.Client(timeout=60.0)
        self._ensure_data_files()
        self._load_data()
        self._connected = True
        logger.info(
            f"CLICS adapter connected: {len(self._entries)} colexification "
            f"entries from {len(self._languages)} languages"
        )

    def disconnect(self) -> None:
        """Release resources."""
        if self._client:
            self._client.close()
            self._client = None
        self._languages = {}
        self._parameters = {}
        self._entries = []
        self._connected = False

    def fetch_batch(self, offset: int, limit: int) -> Iterator[RawLexicalEntry]:
        """Fetch a batch of colexification entries."""
        if not self._connected:
            raise RuntimeError("Adapter not connected. Call connect() first.")
        yield from self._entries[offset : offset + limit]

    def get_total_count(self) -> int:
        """Return the number of colexification entries."""
        return len(self._entries)

    def get_last_modified(self) -> datetime:
        """Return modification time of the local forms file."""
        forms_path = self.data_dir / self.CLDF_FILES["forms"]
        if forms_path.exists():
            return datetime.fromtimestamp(forms_path.stat().st_mtime)
        return datetime.now()

    def supports_incremental(self) -> bool:
        """CLDF releases are static snapshots; no incremental updates."""
        return False

    def get_colexification_pairs(self) -> dict[tuple[str, str], int]:
        """Count how many languages colexify each concept pair.

        Returns:
            Dict mapping sorted (concept_a, concept_b) tuples to the number
            of languages in which one form expresses both concepts.
        """
        if not self._connected:
            raise RuntimeError("Adapter not connected. Call connect() first.")

        pair_languages: dict[tuple[str, str], set[str]] = {}
        for entry in self._entries:
            concepts = entry.raw_data.get("colexified_concepts", [])
            language = entry.language
            for i, concept_a in enumerate(concepts):
                for concept_b in concepts[i + 1 :]:
                    pair = tuple(sorted((concept_a, concept_b)))
                    pair_languages.setdefault(pair, set()).add(language)
        return {pair: len(langs) for pair, langs in sorted(pair_languages.items())}

    def _ensure_data_files(self) -> None:
        """Download CLDF CSV files if they don't exist locally."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        for key, filename in self.CLDF_FILES.items():
            filepath = self.data_dir / filename
            if filepath.exists():
                logger.debug(f"CLICS {key} file exists: {filepath}")
                continue
            url = f"{self.base_url}/{filename}"
            logger.info(f"Downloading CLDF {key}: {url}")
            self._download_file(url, filepath)

    def _download_file(self, url: str, filepath: Path, max_retries: int = 3) -> None:
        """Download a file with retry logic."""
        if not self._client:
            raise RuntimeError("HTTP client not initialized")

        last_error: Exception | None = None
        for attempt in range(max_retries):
            try:
                response = self._client.get(url)
                response.raise_for_status()
                filepath.write_bytes(response.content)
                logger.info(f"Downloaded {filepath.name} ({len(response.content)} bytes)")
                return
            except (httpx.ConnectError, httpx.ReadTimeout, httpx.HTTPStatusError) as e:
                last_error = e
                wait = (attempt + 1) * 2
                logger.warning(
                    f"Download attempt {attempt + 1}/{max_retries} failed for "
                    f"{filepath.name}: {e}. Retrying in {wait}s..."
                )
                time.sleep(wait)

        raise ConnectionError(f"Failed to download {url} after {max_retries} retries: {last_error}")

    def _load_data(self) -> None:
        """Load CLDF CSVs and group forms into colexification entries."""
        self._languages = self._load_csv_indexed(self.data_dir / self.CLDF_FILES["languages"])
        self._parameters = self._load_csv_indexed(self.data_dir / self.CLDF_FILES["parameters"])

        forms_path = self.data_dir / self.CLDF_FILES["forms"]
        if not forms_path.exists():
            raise ConnectionError(f"CLDF forms file missing: {forms_path}")

        # Group parameter IDs by (language, normalized form)
        groups: dict[tuple[str, str], dict[str, Any]] = {}
        with open(forms_path, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                language_id = row.get("Language_ID", "")
                form = (row.get("Form") or "").strip()
                parameter_id = row.get("Parameter_ID", "")
                if not form or not language_id or not parameter_id:
                    continue

                lang_info = self._languages.get(language_id, {})
                lang_name = lang_info.get("Name", language_id)
                if self.languages_filter and lang_name not in self.languages_filter:
                    continue

                key = (language_id, unicodedata.normalize("NFC", form).lower())
                group = groups.setdefault(key, {"form": form, "parameter_ids": [], "row_ids": []})
                if parameter_id not in group["parameter_ids"]:
                    group["parameter_ids"].append(parameter_id)
                group["row_ids"].append(row.get("ID", ""))

        self._entries = []
        for (language_id, _normalized), group in sorted(groups.items()):
            entry = self._build_entry(language_id, group)
            if entry is not None:
                self._entries.append(entry)

    def _load_csv_indexed(self, path: Path) -> dict[str, dict[str, str]]:
        """Load a CSV file into a dict indexed by its ID column."""
        result: dict[str, dict[str, str]] = {}
        if not path.exists():
            return result
        with open(path, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                key = row.get("ID", "")
                if key:
                    result[key] = dict(row)
        return result

    def _build_entry(self, language_id: str, group: dict[str, Any]) -> RawLexicalEntry | None:
        """Build a RawLexicalEntry for one (language, form) group."""
        if len(group["parameter_ids"]) < self.min_colexifications:
            return None

        lang_info = self._languages.get(language_id, {})
        lang_name = lang_info.get("Name", language_id)
        lang_code = lang_info.get("ISO639P3code", "")

        concepts = []
        for parameter_id in group["parameter_ids"]:
            param = self._parameters.get(parameter_id, {})
            name = param.get("Concepticon_Gloss") or param.get("Name") or parameter_id
            if name not in concepts:
                concepts.append(name)

        return RawLexicalEntry(
            source_id=f"clics-{language_id}-{group['row_ids'][0]}",
            source_name="clics",
            form=group["form"],
            language=lang_name,
            language_code=lang_code,
            definitions=concepts,
            raw_data={
                "source": "clics",
                "language_id": language_id,
                "parameter_ids": group["parameter_ids"],
                "colexified_concepts": concepts,
                "colexification_degree": len(concepts),
                "language_family": lang_info.get("Family", ""),
                "language_glottocode": lang_info.get("Glottocode", ""),
            },
        )
