"""Wiktionary adapter for ingesting etymological data from Wiktionary API."""

import asyncio
import logging
import re
import time
from collections.abc import Iterator
from datetime import datetime
from typing import Any

import httpx

from .base import RawLexicalEntry, SourceAdapter

logger = logging.getLogger(__name__)


# Common ISO 639-3 language code mappings for Wiktionary language names
LANGUAGE_CODE_MAP = {
    "English": "eng",
    "French": "fra",
    "German": "deu",
    "Spanish": "spa",
    "Italian": "ita",
    "Portuguese": "por",
    "Dutch": "nld",
    "Russian": "rus",
    "Polish": "pol",
    "Latin": "lat",
    "Ancient Greek": "grc",
    "Greek": "ell",
    "Old English": "ang",
    "Middle English": "enm",
    "Old French": "fro",
    "Old Norse": "non",
    "Proto-Germanic": "gem-pro",
    "Proto-Indo-European": "ine-pro",
    "Sanskrit": "san",
    "Arabic": "ara",
    "Hebrew": "heb",
    "Japanese": "jpn",
    "Chinese": "zho",
    "Korean": "kor",
}


class WiktionaryAdapter(SourceAdapter):
    """
    Adapter for Wiktionary API.

    This adapter fetches lexical entries from Wiktionary using the MediaWiki API.
    It can fetch individual words or process lists of words in batches.
    """

    def __init__(
        self,
        api_endpoint: str = "https://en.wiktionary.org/w/api.php",
        languages_to_process: list[str] | None = None,
        batch_size: int = 50,
        rate_limit_ms: int = 100,
        timeout_seconds: float = 30.0,
    ):
        """
        Initialize the Wiktionary adapter.

        Args:
            api_endpoint: The Wiktionary API endpoint URL.
            languages_to_process: List of languages to extract (None for all).
            batch_size: Number of entries to fetch per API call.
            rate_limit_ms: Minimum milliseconds between API requests.
            timeout_seconds: HTTP request timeout.
        """
        super().__init__()
        self.api_endpoint = api_endpoint
        self.languages_to_process = languages_to_process
        self.batch_size = batch_size
        self.rate_limit_ms = rate_limit_ms
        self.timeout_seconds = timeout_seconds

        self._client: httpx.Client | None = None
        self._last_request_time: float = 0
        self._word_list: list[str] = []
        self._total_count: int = 0

    def connect(self) -> None:
        """Establish connection to Wiktionary API."""
        self._client = httpx.Client(timeout=self.timeout_seconds)
        self._connected = True
        logger.info(f"Connected to Wiktionary API at {self.api_endpoint}")

    def disconnect(self) -> None:
        """Close connection to Wiktionary API."""
        if self._client:
            self._client.close()
            self._client = None
        self._connected = False
        logger.info("Disconnected from Wiktionary API")

    def set_word_list(self, words: list[str]) -> None:
        """
        Set the list of words to process.

        Args:
            words: List of words to fetch from Wiktionary.
        """
        self._word_list = words
        self._total_count = len(words)

    def get_total_count(self) -> int:
        """Return total number of words in the word list."""
        return self._total_count

    def get_last_modified(self) -> datetime:
        """Return current time (API data is always current)."""
        return datetime.now()

    def supports_incremental(self) -> bool:
        """Whether source supports delta updates."""
        return True

    def fetch_batch(self, offset: int, limit: int) -> Iterator[RawLexicalEntry]:
        """
        Fetch a batch of entries from Wiktionary.

        Args:
            offset: Index to start from in the word list.
            limit: Maximum number of entries to fetch.

        Yields:
            RawLexicalEntry objects for each word.
        """
        if not self._client:
            raise RuntimeError("Not connected. Call connect() first.")

        words_to_fetch = self._word_list[offset : offset + limit]

        for word in words_to_fetch:
            self._rate_limit()
            try:
                entries = self._fetch_word(word)
                yield from entries
            except Exception as e:
                logger.warning(f"Failed to fetch word '{word}': {e}")
                continue

    def fetch_word(self, word: str) -> list[RawLexicalEntry]:
        """
        Fetch a single word from Wiktionary.

        Args:
            word: The word to look up.

        Returns:
            List of RawLexicalEntry objects (one per language section).
        """
        if not self._client:
            raise RuntimeError("Not connected. Call connect() first.")

        self._rate_limit()
        return self._fetch_word(word)

    def _rate_limit(self) -> None:
        """Enforce rate limiting between requests."""
        elapsed = (time.time() - self._last_request_time) * 1000
        if elapsed < self.rate_limit_ms:
            sleep_time = (self.rate_limit_ms - elapsed) / 1000
            time.sleep(sleep_time)
        self._last_request_time = time.time()

    def _fetch_word(self, word: str) -> list[RawLexicalEntry]:
        """
        Fetch and parse a word from Wiktionary API.

        Args:
            word: The word to fetch.

        Returns:
            List of RawLexicalEntry objects.
        """
        # Fetch the page content
        params = {
            "action": "query",
            "titles": word,
            "prop": "revisions",
            "rvprop": "content",
            "rvslots": "main",
            "format": "json",
            "formatversion": "2",
        }

        response = self._client.get(self.api_endpoint, params=params)
        response.raise_for_status()
        data = response.json()

        # Extract the wikitext content
        pages = data.get("query", {}).get("pages", [])
        if not pages:
            return []

        page = pages[0]
        if "missing" in page:
            logger.debug(f"Word '{word}' not found in Wiktionary")
            return []

        revisions = page.get("revisions", [])
        if not revisions:
            return []

        content = revisions[0].get("slots", {}).get("main", {}).get("content", "")
        if not content:
            return []

        # Parse the wikitext to extract entries
        return self._parse_wikitext(word, content)

    def _parse_wikitext(self, word: str, content: str) -> list[RawLexicalEntry]:
        """
        Parse Wiktionary wikitext to extract lexical entries.

        Args:
            word: The word being parsed.
            content: The wikitext content.

        Returns:
            List of RawLexicalEntry objects.
        """
        entries = []

        # Split by language sections (==Language==)
        language_pattern = re.compile(r"^==\s*([^=]+?)\s*==$", re.MULTILINE)
        language_sections = language_pattern.split(content)

        # Process pairs of (language_name, section_content)
        for i in range(1, len(language_sections), 2):
            if i + 1 >= len(language_sections):
                break

            language_name = language_sections[i].strip()
            section_content = language_sections[i + 1]

            # Filter by languages if specified
            if self.languages_to_process and language_name not in self.languages_to_process:
                continue

            # Get language code
            language_code = LANGUAGE_CODE_MAP.get(language_name, language_name[:3].lower())

            # Parse the language section
            entry = self._parse_language_section(word, language_name, language_code, section_content)
            if entry:
                entries.append(entry)

        return entries

    def _parse_language_section(
        self, word: str, language_name: str, language_code: str, content: str
    ) -> RawLexicalEntry | None:
        """
        Parse a single language section from Wiktionary.

        Args:
            word: The word.
            language_name: The language name.
            language_code: The ISO 639-3 code.
            content: The section content.

        Returns:
            RawLexicalEntry or None if parsing fails.
        """
        # Extract pronunciation (IPA)
        ipa_pattern = re.compile(r"\{\{IPA\|[^|]*\|/([^/]+)/")
        ipa_match = ipa_pattern.search(content)
        phonetic = ipa_match.group(1) if ipa_match else ""

        # Extract etymology
        etymology = self._extract_section(content, "Etymology")

        # Extract definitions
        definitions = self._extract_definitions(content)

        # Extract part of speech
        pos = self._extract_parts_of_speech(content)

        # Extract earliest attestation date if available
        date_attested = self._extract_attestation_date(content)

        if not definitions:
            # If no definitions found, still create entry with etymology
            definitions = ["(definition not extracted)"]

        return RawLexicalEntry(
            source_id=f"wikt-{word}-{language_code}",
            source_name="wiktionary",
            form=word,
            form_phonetic=phonetic,
            language=language_name,
            language_code=language_code,
            etymology=etymology,
            definitions=definitions,
            part_of_speech=pos,
            date_attested=date_attested,
            raw_data={
                "source_url": f"https://en.wiktionary.org/wiki/{word}",
                "language_section": language_name,
            },
        )

    def _extract_section(self, content: str, section_name: str) -> str | None:
        """Extract content from a named section."""
        pattern = re.compile(
            rf"^===+\s*{section_name}[^=]*===+\s*\n(.*?)(?=^===|\Z)",
            re.MULTILINE | re.DOTALL,
        )
        match = pattern.search(content)
        if match:
            text = match.group(1).strip()
            # Clean up wikitext markup
            text = re.sub(r"\{\{[^}]+\}\}", "", text)  # Remove templates
            text = re.sub(r"\[\[([^|\]]+\|)?([^\]]+)\]\]", r"\2", text)  # Clean links
            text = re.sub(r"'''?", "", text)  # Remove bold/italic
            text = text.strip()
            return text if text else None
        return None

    def _extract_definitions(self, content: str) -> list[str]:
        """Extract definitions from numbered list items."""
        definitions = []

        # Look for definition lines (# Definition text)
        def_pattern = re.compile(r"^#\s+([^#\n*:][^\n]*)", re.MULTILINE)
        matches = def_pattern.findall(content)

        for match in matches[:10]:  # Limit to 10 definitions
            # Clean up wikitext
            definition = match.strip()
            definition = re.sub(r"\{\{[^}]+\}\}", "", definition)
            definition = re.sub(r"\[\[([^|\]]+\|)?([^\]]+)\]\]", r"\2", definition)
            definition = re.sub(r"'''?", "", definition)
            definition = definition.strip()

            if definition and len(definition) > 2:
                definitions.append(definition)

        return definitions

    def _extract_parts_of_speech(self, content: str) -> list[str]:
        """Extract parts of speech from section headers."""
        pos_list = []

        # Common POS headers in Wiktionary
        pos_keywords = [
            "Noun",
            "Verb",
            "Adjective",
            "Adverb",
            "Pronoun",
            "Preposition",
            "Conjunction",
            "Interjection",
            "Article",
            "Determiner",
            "Particle",
            "Numeral",
            "Proper noun",
        ]

        for pos in pos_keywords:
            pattern = re.compile(rf"^===+\s*{pos}\s*===+", re.MULTILINE | re.IGNORECASE)
            if pattern.search(content):
                pos_list.append(pos.lower())

        return pos_list

    def _extract_attestation_date(self, content: str) -> int | None:
        """Try to extract earliest attestation date from etymology or quotes."""
        # Look for century patterns like "14th century" or "c. 1400"
        century_pattern = re.compile(r"(\d{1,2})(?:st|nd|rd|th)\s+century", re.IGNORECASE)
        match = century_pattern.search(content)
        if match:
            century = int(match.group(1))
            # Return approximate start of century
            return (century - 1) * 100 + 1

        # Look for year patterns like "1400" or "c. 1400"
        year_pattern = re.compile(r"(?:c\.\s*)?(\d{4})")
        matches = year_pattern.findall(content)
        if matches:
            years = [int(y) for y in matches if 800 <= int(y) <= 2100]
            if years:
                return min(years)

        return None

    def fetch_recent_changes(self, hours_back: int = 24) -> Iterator[RawLexicalEntry]:
        """
        Fetch recently changed entries from Wiktionary.

        Args:
            hours_back: Number of hours to look back.

        Yields:
            RawLexicalEntry objects for changed words.
        """
        if not self._client:
            raise RuntimeError("Not connected. Call connect() first.")

        # Use the RecentChanges API
        params = {
            "action": "query",
            "list": "recentchanges",
            "rcnamespace": "0",  # Main namespace only
            "rclimit": "500",
            "rctype": "edit|new",
            "format": "json",
            "formatversion": "2",
        }

        self._rate_limit()
        response = self._client.get(self.api_endpoint, params=params)
        response.raise_for_status()
        data = response.json()

        changes = data.get("query", {}).get("recentchanges", [])

        seen_titles = set()
        for change in changes:
            title = change.get("title", "")
            if title and title not in seen_titles and ":" not in title:
                seen_titles.add(title)
                try:
                    entries = self.fetch_word(title)
                    yield from entries
                except Exception as e:
                    logger.debug(f"Failed to fetch recent change '{title}': {e}")
                    continue
