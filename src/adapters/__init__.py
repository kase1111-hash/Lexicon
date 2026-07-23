"""Source adapters for ingesting etymological data from various sources."""

from .base import RawLexicalEntry, SourceAdapter
from .clics import CLICSAdapter
from .clld import CLLDAdapter
from .corpus import CorpusAdapter
from .wiktionary import WiktionaryAdapter

__all__ = [
    "CLICSAdapter",
    "CLLDAdapter",
    "CorpusAdapter",
    "RawLexicalEntry",
    "SourceAdapter",
    "WiktionaryAdapter",
]
