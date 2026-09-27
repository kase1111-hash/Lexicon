"""Word boundaries shared by the analyses and the corpus adapter."""

import unicodedata

# Characters that join two parts of one word when a letter follows them:
# apostrophes and hyphens ("knight's", "self-evident"), and the zero-width
# (non-)joiners written inside Persian and Indic words
WORD_JOINERS = frozenset("'’-‌‍")


def word_spans(text: str) -> list[tuple[int, int]]:
    """(start, end) offsets of the words of a text, in any script.

    A word starts with a letter and runs through letters and combining marks
    (Devanagari and Tamil vowel signs and viramas, Arabic harakat, Hebrew
    niqqud, decomposed accents), so marks never split a word in two.
    """
    spans: list[tuple[int, int]] = []
    start: int | None = None  # where the current word began
    for i, ch in enumerate(text):
        kind = unicodedata.category(ch)[0]
        if kind == "L" or (kind == "M" and start is not None):
            if start is None:
                start = i
        elif start is not None and not (
            ch in WORD_JOINERS and i + 1 < len(text) and unicodedata.category(text[i + 1])[0] == "L"
        ):
            spans.append((start, i))
            start = None
    if start is not None:
        spans.append((start, len(text)))
    return spans
