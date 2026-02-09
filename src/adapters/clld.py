"""CLLD adapter for Cross-Linguistic Linked Data repositories.

Focuses on WOLD (World Loanword Database) as the primary dataset.
WOLD provides structured loanword data with donor/recipient language pairs,
borrowing scores, and semantic fields - directly feeding the borrowing
analysis pipeline.

Data format: WOLD distributes data as CSV files downloadable from
https://wold.clld.org/. The key tables are:
- forms.csv: word forms with language, meaning, borrowing score
- languages.csv: language metadata (family, coordinates, etc.)
- meanings.csv: semantic fields and definitions
- counterparts.csv: cross-linguistic concept mappings
"""

import csv
import io
import logging
import re
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from .base import RawLexicalEntry, SourceAdapter

logger = logging.getLogger(__name__)

# WOLD borrowing scores map to confidence:
# 1 = clearly borrowed (high confidence)
# 2 = probably borrowed
# 3 = perhaps borrowed
# 4 = very little evidence for borrowing
# 5 = no evidence for borrowing (inherited)
WOLD_BORROWING_CONFIDENCE = {
    1: 0.95,
    2: 0.80,
    3: 0.60,
    4: 0.30,
    5: 0.10,
}

# WOLD language name -> ISO 639-3 code (subset, extended at runtime from data)
WOLD_LANGUAGE_CODES: dict[str, str] = {
    "English": "eng",
    "French": "fra",
    "German": "deu",
    "Spanish": "spa",
    "Dutch": "nld",
    "Portuguese": "por",
    "Italian": "ita",
    "Russian": "rus",
    "Turkish": "tur",
    "Japanese": "jpn",
    "Mandarin Chinese": "zho",
    "Indonesian": "ind",
    "Swahili": "swh",
    "Hawaiian": "haw",
    "Maori": "mri",
    "Romanian": "ron",
    "Hungarian": "hun",
    "Finnish": "fin",
    "Thai": "tha",
    "Vietnamese": "vie",
    "Hindi": "hin",
    "Arabic": "ara",
    "Hebrew": "heb",
    "Persian": "fas",
    "Hausa": "hau",
    "Yoruba": "yor",
    "Swahili": "swh",
    "Malagasy": "mlg",
    "Tagalog": "tgl",
    "Cebuano": "ceb",
    "Javanese": "jav",
    "Korean": "kor",
    "Quechua": "que",
    "Nahuatl": "nah",
    "Guarani": "grn",
}

# WOLD semantic fields (subset)
WOLD_SEMANTIC_FIELDS = {
    "1": "The physical world",
    "2": "Kinship",
    "3": "Animals",
    "4": "The body",
    "5": "Food and drink",
    "6": "Clothing and grooming",
    "7": "The house",
    "8": "Agriculture and vegetation",
    "9": "Basic actions and technology",
    "10": "Motion",
    "11": "Possession",
    "12": "Spatial relations",
    "13": "Quantity",
    "14": "Time",
    "15": "Sense perception",
    "16": "Emotions and values",
    "17": "Cognition",
    "18": "Speech and language",
    "19": "Social and political relations",
    "20": "Warfare and hunting",
    "21": "Law",
    "22": "Religion and belief",
    "23": "Modern world",
    "24": "Miscellaneous function words",
}


class WOLDData:
    """Parsed WOLD dataset held in memory."""

    def __init__(self) -> None:
        self.forms: list[dict[str, str]] = []
        self.languages: dict[str, dict[str, str]] = {}  # id -> language info
        self.meanings: dict[str, dict[str, str]] = {}  # id -> meaning info
        self.total_count: int = 0
        self.loaded: bool = False


class CLLDAdapter(SourceAdapter):
    """Adapter for CLLD (Cross-Linguistic Linked Data) repositories.

    Focuses on WOLD (World Loanword Database) which provides structured
    loanword data with borrowing scores, donor languages, and semantic fields.

    Usage:
        adapter = CLLDAdapter(data_dir="data/wold")
        adapter.connect()  # downloads/loads CSV files
        for entry in adapter.fetch_batch(0, 100):
            # process RawLexicalEntry
            pass
        adapter.disconnect()
    """

    # WOLD CSV download URLs (GitHub-hosted static files)
    WOLD_BASE_URL = "https://raw.githubusercontent.com/lexibank/wold/master/cldf"
    WOLD_FILES = {
        "forms": "forms.csv",
        "languages": "languages.csv",
        "parameters": "parameters.csv",  # meanings/concepts
    }

    def __init__(
        self,
        data_dir: str | Path | None = None,
        languages_filter: list[str] | None = None,
    ):
        """Initialize the CLLD/WOLD adapter.

        Args:
            data_dir: Directory containing WOLD CSV files. If files
                don't exist, they will be downloaded.
            languages_filter: Optional list of language names to include.
                If None, all languages are included.
        """
        self.data_dir = Path(data_dir) if data_dir else Path("data/wold")
        self.languages_filter = languages_filter
        self._data = WOLDData()
        self._client: httpx.Client | None = None
        self._connected = False

    @property
    def name(self) -> str:
        return "WOLD"

    def connect(self) -> None:
        """Load WOLD data from local CSV files, downloading if needed."""
        self._client = httpx.Client(timeout=60.0)
        self._ensure_data_files()
        self._load_data()
        self._connected = True
        logger.info(
            f"WOLD adapter connected: {self._data.total_count} forms "
            f"from {len(self._data.languages)} languages"
        )

    def disconnect(self) -> None:
        """Release resources."""
        if self._client:
            self._client.close()
            self._client = None
        self._data = WOLDData()
        self._connected = False

    def fetch_batch(self, offset: int, limit: int) -> Iterator[RawLexicalEntry]:
        """Fetch a batch of WOLD entries as RawLexicalEntry objects.

        Args:
            offset: Starting index into the forms list.
            limit: Maximum number of entries to return.

        Yields:
            RawLexicalEntry objects mapped from WOLD form data.
        """
        if not self._data.loaded:
            raise RuntimeError("Adapter not connected. Call connect() first.")

        end = min(offset + limit, len(self._data.forms))
        for i in range(offset, end):
            entry = self._convert_form(self._data.forms[i])
            if entry is not None:
                yield entry

    def get_total_count(self) -> int:
        """Return total number of WOLD forms loaded."""
        return self._data.total_count

    def get_last_modified(self) -> datetime:
        """Return modification time of the local data files."""
        forms_path = self.data_dir / self.WOLD_FILES["forms"]
        if forms_path.exists():
            return datetime.fromtimestamp(forms_path.stat().st_mtime)
        return datetime.now()

    def supports_incremental(self) -> bool:
        """WOLD is a static dataset; no incremental updates."""
        return False

    def fetch_by_language(self, language: str) -> Iterator[RawLexicalEntry]:
        """Fetch all entries for a specific language.

        Args:
            language: Language name (e.g. "English") or ISO code.

        Yields:
            RawLexicalEntry objects for the specified language.
        """
        if not self._data.loaded:
            raise RuntimeError("Adapter not connected. Call connect() first.")

        for form in self._data.forms:
            lang_id = form.get("Language_ID", "")
            lang_info = self._data.languages.get(lang_id, {})
            lang_name = lang_info.get("Name", "")
            lang_code = lang_info.get("ISO639P3code", "")

            if language in (lang_name, lang_code):
                entry = self._convert_form(form)
                if entry is not None:
                    yield entry

    def fetch_borrowings(self) -> Iterator[RawLexicalEntry]:
        """Fetch only entries that are identified as borrowings.

        Yields entries with WOLD borrowing scores 1-3 (clearly/probably/perhaps
        borrowed), which have the most linguistic value.

        Yields:
            RawLexicalEntry objects for borrowed forms.
        """
        if not self._data.loaded:
            raise RuntimeError("Adapter not connected. Call connect() first.")

        for form in self._data.forms:
            score_str = form.get("Borrowed_score", "")
            if not score_str:
                continue
            try:
                score = float(score_str)
            except (ValueError, TypeError):
                continue

            # Only yield forms with meaningful borrowing evidence (score <= 3)
            if score <= 3.0:
                entry = self._convert_form(form)
                if entry is not None:
                    yield entry

    def _ensure_data_files(self) -> None:
        """Download WOLD CSV files if they don't exist locally."""
        self.data_dir.mkdir(parents=True, exist_ok=True)

        for key, filename in self.WOLD_FILES.items():
            filepath = self.data_dir / filename
            if filepath.exists():
                logger.debug(f"WOLD {key} file exists: {filepath}")
                continue

            url = f"{self.WOLD_BASE_URL}/{filename}"
            logger.info(f"Downloading WOLD {key}: {url}")
            self._download_file(url, filepath)

    def _download_file(self, url: str, filepath: Path, max_retries: int = 3) -> None:
        """Download a file with retry logic.

        Args:
            url: URL to download.
            filepath: Local path to save to.
            max_retries: Maximum retry attempts.
        """
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
                import time
                time.sleep(wait)

        raise RuntimeError(
            f"Failed to download {url} after {max_retries} retries: {last_error}"
        )

    def _load_data(self) -> None:
        """Load and parse all WOLD CSV files into memory."""
        # Load languages
        lang_path = self.data_dir / self.WOLD_FILES["languages"]
        if lang_path.exists():
            self._data.languages = self._load_csv_indexed(lang_path, "ID")
            logger.info(f"Loaded {len(self._data.languages)} WOLD languages")

        # Load meanings/parameters
        params_path = self.data_dir / self.WOLD_FILES["parameters"]
        if params_path.exists():
            self._data.meanings = self._load_csv_indexed(params_path, "ID")
            logger.info(f"Loaded {len(self._data.meanings)} WOLD meanings")

        # Load forms (the main data)
        forms_path = self.data_dir / self.WOLD_FILES["forms"]
        if forms_path.exists():
            all_forms = self._load_csv_list(forms_path)
            # Apply language filter if set
            if self.languages_filter:
                filtered = []
                for form in all_forms:
                    lang_id = form.get("Language_ID", "")
                    lang_info = self._data.languages.get(lang_id, {})
                    lang_name = lang_info.get("Name", "")
                    if lang_name in self.languages_filter:
                        filtered.append(form)
                self._data.forms = filtered
            else:
                self._data.forms = all_forms

            self._data.total_count = len(self._data.forms)
            logger.info(f"Loaded {self._data.total_count} WOLD forms")

        self._data.loaded = True

    def _load_csv_indexed(self, path: Path, key_field: str) -> dict[str, dict[str, str]]:
        """Load a CSV file into a dict indexed by a key field.

        Args:
            path: Path to CSV file.
            key_field: Column name to use as dict key.

        Returns:
            Dict mapping key_field values to row dicts.
        """
        result: dict[str, dict[str, str]] = {}
        with open(path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                key = row.get(key_field, "")
                if key:
                    result[key] = dict(row)
        return result

    def _load_csv_list(self, path: Path) -> list[dict[str, str]]:
        """Load a CSV file into a list of row dicts.

        Args:
            path: Path to CSV file.

        Returns:
            List of row dicts.
        """
        rows: list[dict[str, str]] = []
        with open(path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                rows.append(dict(row))
        return rows

    def _convert_form(self, form: dict[str, str]) -> RawLexicalEntry | None:
        """Convert a WOLD form row to a RawLexicalEntry.

        Args:
            form: Dict from the WOLD forms.csv row.

        Returns:
            RawLexicalEntry or None if the form is invalid/empty.
        """
        word = form.get("Form", "").strip()
        if not word:
            return None

        # Resolve language
        lang_id = form.get("Language_ID", "")
        lang_info = self._data.languages.get(lang_id, {})
        lang_name = lang_info.get("Name", lang_id)
        lang_code = lang_info.get("ISO639P3code", "")

        # Fall back to our known codes if ISO not in the data
        if not lang_code:
            lang_code = WOLD_LANGUAGE_CODES.get(lang_name, "")

        # Resolve meaning/parameter
        param_id = form.get("Parameter_ID", "")
        meaning_info = self._data.meanings.get(param_id, {})
        definition = meaning_info.get("Name", "")
        semantic_field_id = param_id.split("-")[0] if "-" in param_id else param_id
        semantic_field = WOLD_SEMANTIC_FIELDS.get(semantic_field_id, "")

        # Borrowing metadata
        borrowed_score_str = form.get("Borrowed_score", "")
        donor_language = form.get("source_language", "") or form.get("Donor_language", "")

        # Parse borrowing score
        borrowing_confidence = 0.0
        is_borrowed = False
        if borrowed_score_str:
            try:
                score = float(borrowed_score_str)
                # Continuous score: lower = more likely borrowed
                # Map to our confidence scale
                if score <= 1.0:
                    borrowing_confidence = 0.95
                    is_borrowed = True
                elif score <= 2.0:
                    borrowing_confidence = 0.80
                    is_borrowed = True
                elif score <= 3.0:
                    borrowing_confidence = 0.60
                    is_borrowed = True
                elif score <= 4.0:
                    borrowing_confidence = 0.30
                else:
                    borrowing_confidence = 0.10
            except (ValueError, TypeError):
                pass

        # Build definitions list
        definitions = [definition] if definition else []

        # Build related_forms for borrowing relationships
        related_forms: list[dict[str, Any]] = []
        if is_borrowed and donor_language:
            related_forms.append({
                "type": "borrowed_from",
                "language": donor_language,
                "confidence": borrowing_confidence,
            })

        # Build etymology text from borrowing data
        etymology = None
        if is_borrowed and donor_language:
            etymology = f"Borrowed from {donor_language} (WOLD score: {borrowed_score_str})"

        # Generate source ID
        form_id = form.get("ID", f"{lang_id}-{word}")
        source_id = f"wold-{form_id}"

        return RawLexicalEntry(
            source_id=source_id,
            source_name="wold",
            form=word,
            language=lang_name,
            language_code=lang_code,
            etymology=etymology,
            definitions=definitions,
            related_forms=related_forms,
            raw_data={
                "source": "wold",
                "form_id": form_id,
                "language_id": lang_id,
                "parameter_id": param_id,
                "borrowed_score": borrowed_score_str,
                "donor_language": donor_language,
                "semantic_field": semantic_field,
                "semantic_field_id": semantic_field_id,
                "is_borrowed": is_borrowed,
                "borrowing_confidence": borrowing_confidence,
                "language_family": lang_info.get("Family", ""),
                "language_glottocode": lang_info.get("Glottocode", ""),
            },
        )

    def get_language_stats(self) -> dict[str, int]:
        """Get form counts per language.

        Returns:
            Dict mapping language name to form count.
        """
        if not self._data.loaded:
            return {}

        stats: dict[str, int] = {}
        for form in self._data.forms:
            lang_id = form.get("Language_ID", "")
            lang_info = self._data.languages.get(lang_id, {})
            lang_name = lang_info.get("Name", lang_id)
            stats[lang_name] = stats.get(lang_name, 0) + 1
        return dict(sorted(stats.items(), key=lambda x: -x[1]))

    def get_borrowing_stats(self) -> dict[str, int]:
        """Get borrowing counts by score category.

        Returns:
            Dict mapping score category to count.
        """
        if not self._data.loaded:
            return {}

        categories = {
            "clearly_borrowed": 0,
            "probably_borrowed": 0,
            "perhaps_borrowed": 0,
            "little_evidence": 0,
            "no_evidence": 0,
            "unscored": 0,
        }

        for form in self._data.forms:
            score_str = form.get("Borrowed_score", "")
            if not score_str:
                categories["unscored"] += 1
                continue
            try:
                score = float(score_str)
                if score <= 1.0:
                    categories["clearly_borrowed"] += 1
                elif score <= 2.0:
                    categories["probably_borrowed"] += 1
                elif score <= 3.0:
                    categories["perhaps_borrowed"] += 1
                elif score <= 4.0:
                    categories["little_evidence"] += 1
                else:
                    categories["no_evidence"] += 1
            except (ValueError, TypeError):
                categories["unscored"] += 1

        return categories
