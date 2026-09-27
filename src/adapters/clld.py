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
import logging
import re
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from src.utils.languages import LANGUAGE_CODE_MAP, language_filter_keys

from .base import RawLexicalEntry, SourceAdapter

logger = logging.getLogger(__name__)

# WOLD borrowing categories map to confidence. forms.csv carries the category
# as text in `Borrowed` ("1. clearly borrowed") and as a continuous
# `Borrowed_score` where HIGHER means more likely borrowed:
# 1 = clearly borrowed          (Borrowed_score 1.0)
# 2 = probably borrowed         (0.75)
# 3 = perhaps borrowed          (0.5)
# 4 = very little evidence      (0.25)
# 5 = no evidence (inherited)   (0.0)
WOLD_BORROWING_CONFIDENCE = {
    1: 0.95,
    2: 0.80,
    3: 0.60,
    4: 0.30,
    5: 0.10,
}

# Categories 1-3 count as borrowings
WOLD_BORROWED_MAX_CATEGORY = 3

# English `Age` labels for inherited vocabulary. The English word itself is
# attested from the Old English period; the label names how far back the
# etymon goes. Values are (earliest year for the English form, period label).
_ENGLISH_PERIOD_AGES: dict[str, tuple[int, str]] = {
    "proto-indo-european": (700, "Old English (inherited from Proto-Indo-European)"),
    "proto-germanic": (700, "Old English (inherited from Proto-Germanic)"),
    "proto-west germanic": (700, "Old English (inherited from Proto-West Germanic)"),
    "germanic": (700, "Old English (inherited from Germanic)"),
    "frisian-old english": (700, "Old English"),
    "early old english": (700, "Early Old English"),
    "old english": (700, "Old English"),
    "late old english": (900, "Late Old English"),
}

# Japanese periods used by WOLD (standard periodization, start years)
_JAPANESE_PERIOD_AGES: dict[str, int] = {
    "old japanese": 700,
    "late old japanese": 800,
    "middle japanese": 1100,
    "early modern japanese": 1600,
    "modern japanese": 1868,
}

_AGE_YEAR_RE = re.compile(
    r"^(?P<prefix>c\.|ca\.|circa|before|pre-?)?\s*(?P<start>\d{3,4})"
    r"(?:\s*[-–]\s*(?P<end>\d{3,4}|present))?$",
    re.IGNORECASE,
)
_PARENTHESIZED_RE = re.compile(r"\([^)]*\)")
_AGE_CENTURY_RE = re.compile(
    r"^(?:(?P<part>early|mid|late)[-\s])?(?P<n>\d{1,2})(?:st|nd|rd|th)"
    r"(?:\s*/\s*\d{1,2}(?:st|nd|rd|th))? century$",
    re.IGNORECASE,
)
_CENTURY_PART_OFFSET = {"early": 0, "mid": 33, "late": 66}

# WOLD donor language names -> graph codes, consulted before WOLD's own
# language list and LANGUAGE_CODE_MAP (see CLLDAdapter._donor_language_code).
# Latin varieties use the Wiktionary codes of src.utils.languages.
WOLD_DONOR_LANGUAGE_CODES: dict[str, str] = {
    "Latin": "lat",
    "Late Latin": "la-lat",
    "Vulgar Latin": "la-vul",
    "Medieval Latin": "la-med",
    "Neo-Latin": "la-new",
    "French": "fra",
    "Old French": "fro",
    "Middle French": "frm",
    "French (Anglo-Norman)": "xno",
    "Anglo-Norman": "xno",
    "Old Norse": "non",
    "English": "eng",
    "Old English": "ang",
    "Middle English": "enm",
    "Dutch": "nld",
    "Middle Dutch": "dum",
    "Middle Low German": "gml",
    "German": "deu",
    "New High German": "deu",
    "Spanish": "spa",
    "Portuguese": "por",
    "Italian": "ita",
    "Greek": "ell",
    "Ancient Greek": "grc",
    "Arabic": "ara",
    "Classical Arabic": "ara",
    "Standard Arabic": "ara",
    "Persian": "fas",
    "Sanskrit": "san",
    "Russian": "rus",
    "Chinese": "zho",
    "Turkish": "tur",
    "Hungarian": "hun",
    "Malay": "msa",
    "Welsh": "cym",
    "Gaelic (Scottish)": "gla",
    "Irish": "gle",
    "Swahili": "swh",
}


def parse_wold_age(age: str, language_name: str = "") -> tuple[int | None, str, float]:
    """Parse a WOLD `Age` value into an earliest-attestation year.

    Handles the formats WOLD uses for dated words ("1835", "c. 1300",
    "before 1225", "Pre-1606", "1432-1450", "1940-present", "14th century",
    "mid-20th century"), the English period labels used for inherited
    vocabulary ("Proto-Germanic", "Old English") and the Japanese periods
    ("Middle Japanese"). Other free-text labels ("Modern", language-specific
    strata, dictionary citations) are not guessed at.

    Args:
        age: Raw `Age` cell from forms.csv.
        language_name: Recipient language name (period labels are
            only interpreted for English).

    Returns:
        (year or None, period label, date confidence 0-1).
    """
    text = (age or "").strip()
    if not text:
        return None, "", 0.0

    match = _AGE_YEAR_RE.match(text)
    if match:
        year = int(match.group("start"))
        prefix = (match.group("prefix") or "").lower()
        if prefix in ("before", "pre", "pre-"):
            confidence = 0.7
        elif prefix or match.group("end"):
            confidence = 0.9
        else:
            confidence = 1.0
        return year, text, confidence

    match = _AGE_CENTURY_RE.match(text)
    if match:
        century = int(match.group("n"))
        offset = _CENTURY_PART_OFFSET.get((match.group("part") or "").lower(), 0)
        return (century - 1) * 100 + offset, text, 0.6

    if language_name == "English":
        period = _ENGLISH_PERIOD_AGES.get(text.lower())
        if period:
            return period[0], period[1], 0.5
    if language_name == "Japanese" and text.lower() in _JAPANESE_PERIOD_AGES:
        return _JAPANESE_PERIOD_AGES[text.lower()], text, 0.5

    return None, text, 0.0


def _borrowing_category(form: dict[str, str]) -> int | None:
    """Return the WOLD borrowing category (1-5) for a forms.csv row."""
    label = form.get("Borrowed", "").strip()
    if label[:1].isdigit():
        return int(label[0])

    score_str = form.get("Borrowed_score", "").strip()
    if not score_str:
        return None
    try:
        score = float(score_str)
    except ValueError:
        return None
    # Borrowed_score: 1.0 clearly ... 0.0 no evidence
    return max(1, min(5, round(5 - score * 4)))


_NUMBERED_MEANING_RE = re.compile(r"\s*\(\d+\)$")
_QUALIFIED_GLOSS_RE = re.compile(r"^(?P<head>.*?)\s*\((?P<qualifier>[^)]*)\)\s*$")


def wold_meaning_definition(meaning: dict[str, str]) -> str:
    """A WOLD meaning's name, as a definition.

    WOLD numbers meanings that share a name ("male(1)", "male(2)"). The
    number is replaced by what the Concepticon gloss says about the sense:
    its qualifier when it qualifies the same word ("MALE (OF PERSON)" ->
    "male (of person)"), otherwise the gloss itself ("SPRINGTIME" ->
    "the spring (springtime)", "CORRECT (RIGHT)" -> "right (correct)").
    """
    name = (meaning.get("Name") or "").strip()
    base = _NUMBERED_MEANING_RE.sub("", name)
    if base == name:
        return name
    word = base.removeprefix("the ").removeprefix("to ").lower()
    gloss = (meaning.get("Concepticon_Gloss") or "").strip().lower()
    qualified = _QUALIFIED_GLOSS_RE.match(gloss)
    if qualified:
        head, qualifier = qualified.group("head"), qualified.group("qualifier")
        sense = qualifier if head == word else head
    else:
        sense = gloss if gloss != word else ""
    return f"{base} ({sense})" if sense else base


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
        # form id -> donor rows from borrowings.csv
        self.donors: dict[str, list[dict[str, str]]] = {}
        # WOLD language name / glottocode -> its ISO 639-3 code
        self.iso_by_name: dict[str, str] = {}
        self.iso_by_glottocode: dict[str, str] = {}
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
        "borrowings": "borrowings.csv",  # donor language/word per borrowed form
    }
    # Files the adapter can run without (older local copies may lack them)
    OPTIONAL_FILES = frozenset({"borrowings"})

    def __init__(
        self,
        data_dir: str | Path | None = None,
        languages_filter: list[str] | None = None,
    ):
        """Initialize the CLLD/WOLD adapter.

        Args:
            data_dir: Directory containing WOLD CSV files. If files
                don't exist, they will be downloaded.
            languages_filter: Optional list of languages to include, as names,
                ISO 639-3 or ISO 639-1 codes, or Glottolog codes. If None, all
                languages are included.
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

        Yields entries in WOLD borrowing categories 1-3 (clearly/probably/
        perhaps borrowed), which have the most linguistic value.

        Yields:
            RawLexicalEntry objects for borrowed forms.
        """
        if not self._data.loaded:
            raise RuntimeError("Adapter not connected. Call connect() first.")

        for form in self._data.forms:
            category = _borrowing_category(form)
            if category is not None and category <= WOLD_BORROWED_MAX_CATEGORY:
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
            try:
                self._download_file(url, filepath)
            except ConnectionError as e:
                if key not in self.OPTIONAL_FILES:
                    raise
                logger.warning(f"Continuing without WOLD {key}: {e}")

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
            except httpx.HTTPError as e:
                # Any transport failure (proxy, timeout, protocol) or HTTP error status
                last_error = e
                wait = (attempt + 1) * 2
                logger.warning(
                    f"Download attempt {attempt + 1}/{max_retries} failed for "
                    f"{filepath.name}: {e}. Retrying in {wait}s..."
                )
                import time

                time.sleep(wait)

        raise ConnectionError(
            f"Failed to download {url} after {max_retries} attempts: {last_error}"
        )

    def _load_data(self) -> None:
        """Load and parse all WOLD CSV files into memory."""
        # Load languages
        lang_path = self.data_dir / self.WOLD_FILES["languages"]
        if lang_path.exists():
            self._data.languages = self._load_csv_indexed(lang_path, "ID")
            logger.info(f"Loaded {len(self._data.languages)} WOLD languages")
        for info in self._data.languages.values():
            iso = info.get("ISO639P3code", "")
            name, glottocode = info.get("Name", ""), info.get("Glottocode", "")
            # A blank name or glottocode must not become a key that a donor
            # row with the same blank would match
            if iso and name:
                self._data.iso_by_name[name] = iso
            if iso and glottocode:
                self._data.iso_by_glottocode[glottocode] = iso

        # Load meanings/parameters
        params_path = self.data_dir / self.WOLD_FILES["parameters"]
        if params_path.exists():
            self._data.meanings = self._load_csv_indexed(params_path, "ID")
            logger.info(f"Loaded {len(self._data.meanings)} WOLD meanings")

        # Load donor information for borrowed forms
        borrowings_path = self.data_dir / self.WOLD_FILES["borrowings"]
        if borrowings_path.exists():
            for row in self._load_csv_list(borrowings_path):
                target = row.get("Target_Form_ID", "")
                if target:
                    self._data.donors.setdefault(target, []).append(row)
            logger.info(f"Loaded donor data for {len(self._data.donors)} WOLD forms")

        # Load forms (the main data)
        forms_path = self.data_dir / self.WOLD_FILES["forms"]
        if forms_path.exists():
            all_forms = self._load_csv_list(forms_path)
            # Apply language filter if set
            if self.languages_filter:
                # Names or codes (the graph code, e.g. "goh" for Old High
                # German, or a glottocode); ISO 639-1 codes ("en") map to 639-3
                wanted = language_filter_keys(self.languages_filter)
                filtered = []
                for form in all_forms:
                    lang_id = form.get("Language_ID", "")
                    lang_info = self._data.languages.get(lang_id, {})
                    lang_name = lang_info.get("Name", lang_id)
                    names = {
                        lang_name.lower(),
                        lang_info.get("Glottocode", "").lower(),
                        self._language_code(lang_info, lang_name).lower(),
                        WOLD_LANGUAGE_CODES.get(lang_name, ""),
                    }
                    if wanted & names:
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
        # `Value` is the surface form; CLDF `Form` uses underscores for spaces.
        # Parenthesized parts are sense numbers or optional material
        # ("call (1)", "(sea)gull", "wood(s)"); the headword is the rest.
        value = (form.get("Value") or form.get("Form", "").replace("_", " ")).strip()
        word = " ".join(_PARENTHESIZED_RE.sub(" ", value).split()) or value
        if not word:
            return None

        # Resolve language
        lang_id = form.get("Language_ID", "")
        lang_info = self._data.languages.get(lang_id, {})
        lang_name = lang_info.get("Name", lang_id)
        lang_code = self._language_code(lang_info, lang_name)

        # Resolve meaning/parameter
        param_id = form.get("Parameter_ID", "")
        meaning_info = self._data.meanings.get(param_id, {})
        definition = wold_meaning_definition(meaning_info)
        semantic_field_id = param_id.split("-")[0] if "-" in param_id else param_id
        semantic_field = WOLD_SEMANTIC_FIELDS.get(semantic_field_id, "")

        # Borrowing metadata
        borrowed_score_str = form.get("Borrowed_score", "")
        category = _borrowing_category(form)
        is_borrowed = category is not None and category <= WOLD_BORROWED_MAX_CATEGORY
        borrowing_confidence = WOLD_BORROWING_CONFIDENCE.get(category, 0.0) if category else 0.0

        # Earliest attestation from the `Age` column
        date_attested, period_label, date_confidence = parse_wold_age(
            form.get("Age", ""), lang_name
        )

        # Build definitions list
        definitions = [definition] if definition else []

        # Build related_forms from borrowings.csv donor rows. Only the
        # immediate donor becomes a BORROWED_FROM edge; earlier stages of
        # the loan history are kept in the etymology text.
        form_id = form.get("ID", f"{lang_id}-{word}")
        related_forms: list[dict[str, Any]] = []
        donor_notes: list[str] = []
        if is_borrowed:
            for donor in self._data.donors.get(form_id, []):
                donor_lang = donor.get("Source_languoid", "").strip()
                donor_word = donor.get("Source_word", "").strip()
                if not donor_lang or donor_lang == "Unidentified":
                    continue
                relation = donor.get("Source_relation", "immediate") or "immediate"
                certain = donor.get("Source_certain", "yes") != "no"
                note = f"{donor_lang} {donor_word}".strip()
                donor_notes.append(note if relation == "immediate" else f"earlier {note}")
                if relation != "immediate" or not donor_word:
                    continue
                related_forms.append(
                    {
                        "type": "borrowed_from",
                        "form": donor_word,
                        "language": donor_lang,
                        "language_code": self._donor_language_code(donor),
                        "meaning": donor.get("Source_meaning", ""),
                        "certain": certain,
                        "confidence": round(borrowing_confidence * (1.0 if certain else 0.7), 3),
                    }
                )
        donor_language = related_forms[0]["language"] if related_forms else ""

        # Build etymology text from borrowing data
        etymology = None
        if is_borrowed:
            label = form.get("Borrowed", "").split(". ", 1)[-1] or "borrowed"
            source = f" from {'; '.join(donor_notes)}" if donor_notes else ""
            etymology = f"Borrowed{source} (WOLD: {label})"

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
            date_attested=date_attested,
            raw_data={
                "source": "wold",
                "form_id": form_id,
                "value": value,
                "language_id": lang_id,
                "parameter_id": param_id,
                "borrowed_score": borrowed_score_str,
                "borrowed_category": category,
                "donor_language": donor_language,
                "semantic_field": semantic_field,
                "semantic_field_id": semantic_field_id,
                "is_borrowed": is_borrowed,
                "borrowing_confidence": borrowing_confidence,
                "age": form.get("Age", ""),
                "period_label": period_label,
                "date_confidence": date_confidence,
                "language_family": lang_info.get("Family", ""),
                "language_glottocode": lang_info.get("Glottocode", ""),
            },
        )

    @staticmethod
    def _language_code(lang_info: dict[str, str], lang_name: str) -> str:
        """Graph language code for a WOLD (recipient) language.

        Its ISO 639-3 code; otherwise a known code for its name, then its
        Glottolog code, rather than dropping languages (e.g. Old High
        German) that have no ISO code here.
        """
        return (
            lang_info.get("ISO639P3code", "")
            or WOLD_LANGUAGE_CODES.get(lang_name)
            or LANGUAGE_CODE_MAP.get(lang_name)
            or lang_info.get("Glottocode", "")
        )

    def _donor_language_code(self, donor: dict[str, str]) -> str:
        """Graph language code for a borrowings.csv donor row.

        Tried in order: WOLD_DONOR_LANGUAGE_CODES, the ISO code of the WOLD
        language with the donor's name or glottocode (so a Hausa donor links
        to the Hausa words WOLD itself lists), LANGUAGE_CODE_MAP, and only
        then the donor's Glottolog code.
        """
        name = donor.get("Source_languoid", "").strip()
        glottocode = donor.get("Source_languoid_glottocode", "").strip()
        return (
            WOLD_DONOR_LANGUAGE_CODES.get(name)
            or self._data.iso_by_name.get(name)
            or self._data.iso_by_glottocode.get(glottocode)
            or LANGUAGE_CODE_MAP.get(name)
            or glottocode
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

        names = {
            1: "clearly_borrowed",
            2: "probably_borrowed",
            3: "perhaps_borrowed",
            4: "little_evidence",
            5: "no_evidence",
        }
        for form in self._data.forms:
            category = _borrowing_category(form)
            categories[names.get(category, "unscored") if category else "unscored"] += 1

        return categories
