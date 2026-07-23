"""Phonetic utilities for linguistic analysis."""

import re
import unicodedata

# IPA symbols that carry no segmental information: stress marks, syllable
# breaks, tie bars, and length markers are stripped during normalization
# of comparisons (length is kept by normalize_ipa itself).
_IPA_SUPRASEGMENTALS = "ˈˌ.|‖‿͜͡"

# Articulatory feature vectors for common IPA consonants:
# (place 0-10, manner 0-7, voiced 0/1)
_CONSONANT_FEATURES: dict[str, tuple[float, float, float]] = {
    "p": (0, 0, 0),
    "b": (0, 0, 1),
    "m": (0, 1, 1),
    "ɸ": (0, 3, 0),
    "β": (0, 3, 1),
    "f": (1, 3, 0),
    "v": (1, 3, 1),
    "ʋ": (1, 5, 1),
    "θ": (2, 3, 0),
    "ð": (2, 3, 1),
    "t": (3, 0, 0),
    "d": (3, 0, 1),
    "n": (3, 1, 1),
    "s": (3, 3, 0),
    "z": (3, 3, 1),
    "r": (3, 2, 1),
    "ɾ": (3, 2, 1),
    "l": (3, 6, 1),
    "ɬ": (3, 6, 0),
    "ʃ": (4, 3, 0),
    "ʒ": (4, 3, 1),
    "tʃ": (4, 4, 0),
    "dʒ": (4, 4, 1),
    "ʈ": (5, 0, 0),
    "ɖ": (5, 0, 1),
    "ɳ": (5, 1, 1),
    "ʂ": (5, 3, 0),
    "ʐ": (5, 3, 1),
    "c": (6, 0, 0),
    "ɟ": (6, 0, 1),
    "ɲ": (6, 1, 1),
    "ç": (6, 3, 0),
    "ʝ": (6, 3, 1),
    "j": (6, 5, 1),
    "k": (7, 0, 0),
    "g": (7, 0, 1),
    "ɡ": (7, 0, 1),
    "ŋ": (7, 1, 1),
    "x": (7, 3, 0),
    "ɣ": (7, 3, 1),
    "w": (7, 5, 1),
    "q": (8, 0, 0),
    "ɢ": (8, 0, 1),
    "χ": (8, 3, 0),
    "ʁ": (8, 3, 1),
    "ħ": (9, 3, 0),
    "ʕ": (9, 3, 1),
    "ʔ": (10, 0, 0),
    "h": (10, 3, 0),
    "ɦ": (10, 3, 1),
}

# Vowel feature vectors: (height 0-3, backness 0-2, rounded 0/1)
_VOWEL_FEATURES: dict[str, tuple[float, float, float]] = {
    "i": (0, 0, 0),
    "y": (0, 0, 1),
    "ɨ": (0, 1, 0),
    "ʉ": (0, 1, 1),
    "ɯ": (0, 2, 0),
    "u": (0, 2, 1),
    "ɪ": (0.5, 0, 0),
    "ʏ": (0.5, 0, 1),
    "ʊ": (0.5, 2, 1),
    "e": (1, 0, 0),
    "ø": (1, 0, 1),
    "ɘ": (1, 1, 0),
    "ɵ": (1, 1, 1),
    "ɤ": (1, 2, 0),
    "o": (1, 2, 1),
    "ə": (1.5, 1, 0),
    "ɛ": (2, 0, 0),
    "œ": (2, 0, 1),
    "ɜ": (2, 1, 0),
    "ɞ": (2, 1, 1),
    "ʌ": (2, 2, 0),
    "ɔ": (2, 2, 1),
    "æ": (2.5, 0, 0),
    "ɐ": (2.5, 1, 0),
    "a": (3, 0, 0),
    "ɶ": (3, 0, 1),
    "ɑ": (3, 2, 0),
    "ɒ": (3, 2, 1),
}

# Named historical sound laws as ordered (pattern, replacement) regex rules.
# Rules within a law apply sequentially, so orderings encode bleeding/feeding.
_SOUND_LAWS: dict[str, list[tuple[str, str]]] = {
    # Grimm's Law (Proto-Indo-European > Proto-Germanic), three chain shifts
    # applied in chronological order so each shift's output escapes the next:
    # voiceless stops fricativize, then voiced stops devoice, then aspirates
    # deaspirate.
    "grimm": [
        (r"(?<!s)kʷ(?!ʰ)", "xʷ"),
        (r"(?<!s)p(?!ʰ)", "ɸ"),
        (r"(?<!s)t(?!ʰ)", "θ"),
        (r"(?<!s)k(?!ʰ)", "x"),
        (r"gʷ(?!ʰ)", "kʷ"),
        (r"b(?!ʰ)", "p"),
        (r"d(?!ʰ)", "t"),
        (r"g(?!ʰ)", "k"),
        (r"gʷʰ", "gʷ"),
        (r"bʰ", "b"),
        (r"dʰ", "d"),
        (r"gʰ", "g"),
    ],
    # Verner's Law: voiceless fricatives voice after an unstressed syllable.
    # Applied unconditionally to the fricative inventory (stress marking is
    # rarely present in cited proto-forms).
    "verner": [
        (r"ɸ", "β"),
        (r"θ", "ð"),
        (r"x", "ɣ"),
        (r"xʷ", "ɣʷ"),
        (r"s", "z"),
    ],
    # Rhotacism (e.g. Latin, West Germanic): intervocalic s/z > r.
    "rhotacism": [
        (r"(?<=[aeiouɑɛɪɔʊəyø])[sz](?=[aeiouɑɛɪɔʊəyø])", "r"),
    ],
    # Final-obstruent devoicing (German, Dutch, Russian, ...).
    "final_devoicing": [
        (r"b$", "p"),
        (r"d$", "t"),
        (r"g$", "k"),
        (r"v$", "f"),
        (r"z$", "s"),
        (r"ʒ$", "ʃ"),
        (r"ɣ$", "x"),
    ],
    # Intervocalic lenition (broadly Romance): voiceless stops voice.
    "lenition": [
        (r"(?<=[aeiouɑɛɪɔʊə])p(?=[aeiouɑɛɪɔʊə])", "b"),
        (r"(?<=[aeiouɑɛɪɔʊə])t(?=[aeiouɑɛɪɔʊə])", "d"),
        (r"(?<=[aeiouɑɛɪɔʊə])k(?=[aeiouɑɛɪɔʊə])", "g"),
    ],
}


class PhoneticUtils:
    """Utilities for phonetic processing and comparison."""

    @staticmethod
    def normalize_ipa(ipa_string: str) -> str:
        """Normalize an IPA transcription for comparison.

        Applies NFC normalization, strips enclosing brackets/slashes,
        converts ASCII substitutes to proper IPA (':' -> 'ː', 'g' -> 'ɡ'),
        and removes stress marks, syllable breaks, and tie bars.
        """
        s = unicodedata.normalize("NFC", ipa_string.strip())
        s = s.strip("[]/")
        s = s.replace(":", "ː").replace("g", "ɡ")
        s = "".join(c for c in s if c not in _IPA_SUPRASEGMENTALS)
        return s.strip()

    @staticmethod
    def strip_diacritics(text: str) -> str:
        """Remove diacritics from text for matching."""
        nfkd = unicodedata.normalize("NFKD", text)
        return "".join(c for c in nfkd if not unicodedata.combining(c))

    @staticmethod
    def _segment_features(segment: str) -> tuple[float, float, float, bool] | None:
        """Look up the feature vector for one IPA segment.

        Returns (f1, f2, f3, is_vowel), or None for unknown segments.
        """
        if segment in _CONSONANT_FEATURES:
            return (*_CONSONANT_FEATURES[segment], False)
        if segment in _VOWEL_FEATURES:
            return (*_VOWEL_FEATURES[segment], True)
        return None

    @staticmethod
    def _segment_cost(seg1: str, seg2: str) -> float:
        """Substitution cost between two IPA segments, in [0, 1]."""
        if seg1 == seg2:
            return 0.0
        f1 = PhoneticUtils._segment_features(seg1)
        f2 = PhoneticUtils._segment_features(seg2)
        if f1 is None or f2 is None:
            return 1.0
        if f1[3] != f2[3]:  # vowel vs consonant
            return 1.0
        if f1[3]:  # both vowels: height 0-3, backness 0-2, rounding 0-1
            return (
                abs(f1[0] - f2[0]) / 3 * 0.5
                + abs(f1[1] - f2[1]) / 2 * 0.3
                + abs(f1[2] - f2[2]) * 0.2
            )
        # both consonants: place 0-10, manner 0-7, voicing 0-1
        return (
            abs(f1[0] - f2[0]) / 10 * 0.4 + abs(f1[1] - f2[1]) / 7 * 0.4 + abs(f1[2] - f2[2]) * 0.2
        )

    @staticmethod
    def _tokenize_ipa(ipa: str) -> list[str]:
        """Split an IPA string into segments, keeping affricate digraphs."""
        ipa = PhoneticUtils.normalize_ipa(ipa)
        # Strip length marks and combining diacritics for distance purposes
        ipa = ipa.replace("ː", "")
        ipa = "".join(c for c in unicodedata.normalize("NFD", ipa) if not unicodedata.combining(c))
        segments = []
        i = 0
        while i < len(ipa):
            if ipa[i : i + 2] in ("tʃ", "dʒ", "kʷ", "gʷ", "xʷ", "ɣʷ"):
                segments.append(ipa[i : i + 2])
                i += 2
            else:
                segments.append(ipa[i])
                i += 1
        return segments

    @staticmethod
    def phonetic_distance(ipa1: str, ipa2: str) -> float:
        """Calculate phonetic distance between two IPA strings.

        Uses a feature-weighted edit distance: substitution cost depends on
        articulatory feature difference (place/manner/voicing for consonants,
        height/backness/rounding for vowels). Result is normalized to [0, 1],
        where 0 means identical and 1 means maximally different.
        """
        seg1 = PhoneticUtils._tokenize_ipa(ipa1)
        seg2 = PhoneticUtils._tokenize_ipa(ipa2)

        if not seg1 and not seg2:
            return 0.0
        if not seg1 or not seg2:
            return 1.0

        # Weighted Levenshtein over segments
        rows, cols = len(seg1) + 1, len(seg2) + 1
        dist = [[0.0] * cols for _ in range(rows)]
        for i in range(1, rows):
            dist[i][0] = float(i)
        for j in range(1, cols):
            dist[0][j] = float(j)

        for i in range(1, rows):
            for j in range(1, cols):
                sub_cost = PhoneticUtils._segment_cost(seg1[i - 1], seg2[j - 1])
                dist[i][j] = min(
                    dist[i - 1][j] + 1.0,  # deletion
                    dist[i][j - 1] + 1.0,  # insertion
                    dist[i - 1][j - 1] + sub_cost,  # substitution
                )

        return dist[-1][-1] / max(len(seg1), len(seg2))

    @staticmethod
    def soundex(word: str) -> str:
        """Generate the American Soundex code (letter + 3 digits) for a word.

        Non-ASCII input is transliterated by stripping diacritics first;
        words with no codable letters return an empty string.
        """
        word = PhoneticUtils.strip_diacritics(word).upper()
        word = re.sub(r"[^A-Z]", "", word)
        if not word:
            return ""

        codes = {
            **dict.fromkeys("BFPV", "1"),
            **dict.fromkeys("CGJKQSXZ", "2"),
            **dict.fromkeys("DT", "3"),
            "L": "4",
            **dict.fromkeys("MN", "5"),
            "R": "6",
        }

        first_letter = word[0]
        # Encode all letters; H and W are transparent (adjacent same-coded
        # letters separated by H/W collapse), vowels break runs.
        encoded = []
        prev_code = codes.get(first_letter, "")
        for char in word[1:]:
            code = codes.get(char, "")
            if char in "HW":
                continue
            if code and code != prev_code:
                encoded.append(code)
            prev_code = code  # vowels reset prev_code to ""

        return (first_letter + "".join(encoded) + "000")[:4]

    @staticmethod
    def metaphone(word: str) -> str:
        """Generate a Metaphone code for a word.

        Implements the classic Metaphone transformations (Philips 1990),
        covering silent letters, digraph handling (TH, SH, CH, PH, GH, ...),
        and context-dependent C/G/S/T rules.
        """
        word = PhoneticUtils.strip_diacritics(word).upper()
        word = re.sub(r"[^A-Z]", "", word)
        if not word:
            return ""

        # Initial-letter exceptions
        for prefix, replacement in (
            ("AE", "E"),
            ("GN", "N"),
            ("KN", "N"),
            ("PN", "N"),
            ("WR", "R"),
            ("X", "S"),
        ):
            if word.startswith(prefix):
                word = replacement + word[len(prefix) :]
                break

        vowels = "AEIOU"
        result = []
        i = 0
        n = len(word)

        def peek(offset: int) -> str:
            # "\0" sentinel (never matches letter comparisons or `in` checks)
            # instead of "", because `"" in "IEY"` is True in Python
            pos = i + offset
            return word[pos] if 0 <= pos < n else "\0"

        while i < n:
            c = word[i]
            # Skip doubled letters (except C, which has its own rules)
            if c != "C" and c == peek(-1):
                i += 1
                continue

            if c in vowels:
                if i == 0:
                    result.append(c)
            elif c == "B":
                if not (i == n - 1 and peek(-1) == "M"):
                    result.append("B")
            elif c == "C":
                if peek(1) == "I" and peek(2) == "A":
                    result.append("X")
                elif peek(1) == "H":
                    result.append("X")
                    i += 1
                elif peek(1) in "IEY":
                    result.append("S")
                else:
                    result.append("K")
            elif c == "D":
                if peek(1) == "G" and peek(2) in "EIY":
                    result.append("J")
                    i += 2
                else:
                    result.append("T")
            elif c == "G":
                if peek(1) == "H":
                    if peek(2) and peek(2) not in vowels:
                        pass  # silent GH (e.g. "night")
                    else:
                        result.append("K")
                        i += 1
                elif peek(1) == "N":
                    pass  # silent G in GN/GNED
                elif peek(1) in "IEY":
                    result.append("J")
                else:
                    result.append("K")
            elif c == "H":
                if peek(-1) in vowels and peek(1) not in vowels:
                    pass  # silent H after vowel with no following vowel
                elif peek(-1) in "CSPTG":
                    pass  # part of a digraph already handled
                else:
                    result.append("H")
            elif c == "K":
                if peek(-1) != "C":
                    result.append("K")
            elif c == "P":
                if peek(1) == "H":
                    result.append("F")
                    i += 1
                else:
                    result.append("P")
            elif c == "Q":
                result.append("K")
            elif c == "S":
                if peek(1) == "H":
                    result.append("X")
                    i += 1
                elif peek(1) == "I" and peek(2) in "OA":
                    result.append("X")
                else:
                    result.append("S")
            elif c == "T":
                if peek(1) == "H":
                    result.append("0")  # theta
                    i += 1
                elif peek(1) == "I" and peek(2) in "OA":
                    result.append("X")
                else:
                    result.append("T")
            elif c == "V":
                result.append("F")
            elif c == "W":
                if peek(1) in vowels:
                    result.append("W")
            elif c == "X":
                result.append("KS")
            elif c == "Y":
                if peek(1) in vowels:
                    result.append("Y")
            elif c == "Z":
                result.append("S")
            elif c in "FJLMNR":
                result.append(c)
            i += 1

        return "".join(result)

    @staticmethod
    def levenshtein_distance(s1: str, s2: str) -> int:
        """Calculate Levenshtein edit distance between two strings."""
        if len(s1) < len(s2):
            return PhoneticUtils.levenshtein_distance(s2, s1)

        if len(s2) == 0:
            return len(s1)

        previous_row = list(range(len(s2) + 1))
        for i, c1 in enumerate(s1):
            current_row = [i + 1]
            for j, c2 in enumerate(s2):
                insertions = previous_row[j + 1] + 1
                deletions = current_row[j] + 1
                substitutions = previous_row[j] + (c1 != c2)
                current_row.append(min(insertions, deletions, substitutions))
            previous_row = current_row

        return previous_row[-1]

    @staticmethod
    def known_sound_laws() -> list[str]:
        """Return the names of available sound-law transformations."""
        return sorted(_SOUND_LAWS)

    @staticmethod
    def apply_sound_law(form: str, law_name: str) -> str:
        """Apply a named historical sound-law transformation to a form.

        Supported laws: grimm, verner, rhotacism, final_devoicing, lenition.

        Raises:
            ValueError: If law_name is not a known sound law.
        """
        law = _SOUND_LAWS.get(law_name.lower())
        if law is None:
            raise ValueError(
                f"Unknown sound law '{law_name}'. Known laws: {', '.join(sorted(_SOUND_LAWS))}"
            )
        result = unicodedata.normalize("NFC", form)
        for pattern, replacement in law:
            result = re.sub(pattern, replacement, result)
        return result
