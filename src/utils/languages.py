"""Language name -> code mapping shared by adapters and pipelines.

Codes are ISO 639-3 where one exists, otherwise the Wiktionary code for
historical varieties and proto-languages (e.g. "gem-pro", "la-vul").
Unknown names map to nothing; callers must not invent codes from the name.
Arabic is the macrolanguage code "ara" everywhere: Wiktionary's "ar" and
==Arabic== sections mean the macrolanguage, and WOLD's Arabic donors
(Arabic, Classical and Standard Arabic) map to it too, so one Arabic word
is one LSR.
"""

from collections.abc import Iterable

LANGUAGE_CODE_MAP: dict[str, str] = {
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
    "Middle French": "frm",
    "Old Norse": "non",
    "Old High German": "goh",
    "Middle High German": "gmh",
    "Middle Dutch": "dum",
    "Proto-Germanic": "gem-pro",
    "Proto-Indo-European": "ine-pro",
    "Proto-Slavic": "sla-pro",
    "Proto-Romance": "roa-pro",
    "Proto-Celtic": "cel-pro",
    "Sanskrit": "san",
    "Arabic": "ara",
    "Hebrew": "heb",
    "Japanese": "jpn",
    "Chinese": "zho",
    "Korean": "kor",
    "Norman": "nrf",
    "Anglo-Norman": "xno",
    "Vulgar Latin": "la-vul",
    "Middle Low German": "gml",
    "Old Saxon": "osx",
    "Old Frisian": "ofs",
    "Gothic": "got",
    "Scots": "sco",
    "Welsh": "cym",
    "Irish": "gle",
    "Old Irish": "sga",
    "Scottish Gaelic": "gla",
    "Swedish": "swe",
    "Danish": "dan",
    "Norwegian": "nor",
    "Norwegian Bokmål": "nob",
    "Icelandic": "isl",
    "Faroese": "fao",
    "Catalan": "cat",
    "Occitan": "oci",
    "Old Occitan": "pro",
    "Romanian": "ron",
    "Czech": "ces",
    "Ukrainian": "ukr",
    "Bulgarian": "bul",
    "Old Church Slavonic": "chu",
    "Hungarian": "hun",
    "Finnish": "fin",
    "Estonian": "est",
    "Turkish": "tur",
    "Persian": "fas",
    "Hindi": "hin",
    "Swahili": "swh",
    "Late Latin": "la-lat",
    "Medieval Latin": "la-med",
    "New Latin": "la-new",
    "Proto-West Germanic": "gmw-pro",
    "Proto-Italic": "itc-pro",
    "Old Dutch": "odt",
    "Middle Irish": "mga",
    "Old Spanish": "osp",
    "Old Portuguese": "roa-opt",
    "Old Italian": "roa-oit",
}

# Reverse map for code -> name
CODE_TO_LANGUAGE: dict[str, str] = {v: k for k, v in LANGUAGE_CODE_MAP.items()}

# ISO 639-3 code for a language a source names but does not identify
UNDETERMINED_LANGUAGE = "und"

# ISO 639-1 -> ISO 639-3, for Wiktionary codes and user input such as "en"
ISO_639_1_TO_3: dict[str, str] = {
    "en": "eng",
    "fr": "fra",
    "de": "deu",
    "es": "spa",
    "it": "ita",
    "pt": "por",
    "nl": "nld",
    "ru": "rus",
    "la": "lat",
    "el": "ell",
    "sv": "swe",
    "da": "dan",
    "no": "nor",
    "nb": "nob",
    "is": "isl",
    "fi": "fin",
    "pl": "pol",
    "cs": "ces",
    "hu": "hun",
    "tr": "tur",
    "ar": "ara",
    "fa": "fas",
    "hi": "hin",
    "sa": "san",
    "zh": "zho",
    "ja": "jpn",
    "ko": "kor",
    "sw": "swh",
    "he": "heb",
    "ga": "gle",
    "gd": "gla",
    "cy": "cym",
    "ca": "cat",
    "ro": "ron",
    "uk": "ukr",
    "bg": "bul",
}


def graph_language_code(code: str) -> str:
    """Map a source language code (Wiktionary or ISO 639-1) to the code used in the graph."""
    code = (code or "").strip()
    return ISO_639_1_TO_3.get(code.lower(), code)


def language_name(code: str) -> str:
    """Human-readable name for a graph language code (the code itself if unknown)."""
    return CODE_TO_LANGUAGE.get(code, code)


_NAME_TO_CODE_LOWER: dict[str, str] = {
    name.lower(): code for name, code in LANGUAGE_CODE_MAP.items()
}


def code_for_name(name: str) -> str:
    """The graph code of a language name (any case), or "" if it is not known."""
    return _NAME_TO_CODE_LOWER.get((name or "").strip().lower(), "")


def language_filter_keys(values: Iterable[str]) -> set[str]:
    """Lower-cased keys matched by a ``--language`` filter.

    Each value may be a language name, an ISO 639-3 code or an ISO 639-1
    code. The keys are every value as given plus the graph code it stands
    for, so "English", "eng" and "en" all select English. Adapters keep a
    language when any of its names or codes (lower-cased) is in the result.

    Args:
        values: Filter values, e.g. ``"English, fra".split(",")``.

    Returns:
        Lower-cased names and codes to match against.
    """
    keys: set[str] = set()
    for value in values:
        value = value.strip().lower()
        if not value:
            continue
        keys.add(value)
        keys.add(graph_language_code(value).lower())
        if value in _NAME_TO_CODE_LOWER:
            keys.add(_NAME_TO_CODE_LOWER[value])
    return keys
