"""Sample data for testing."""

from uuid import uuid4

# Sample LSR data
SAMPLE_LSR_ENGLISH = {
    "id": str(uuid4()),
    "form_orthographic": "water",
    "form_phonetic": "ˈwɔːtər",
    "form_normalized": "water",
    "language_code": "eng",
    "language_name": "English",
    "language_family": "Indo-European",
    "language_branch": ["Germanic", "West Germanic", "Anglo-Frisian", "Anglic"],
    "period_label": "Modern English",
    "date_start": 1500,
    "date_end": 2024,
    "definition_primary": "a colorless, transparent, odorless liquid",
    "part_of_speech": ["noun"],
}

SAMPLE_LSR_OLD_ENGLISH = {
    "id": str(uuid4()),
    "form_orthographic": "wæter",
    "form_phonetic": "ˈwæter",
    "form_normalized": "waeter",
    "language_code": "ang",
    "language_name": "Old English",
    "language_family": "Indo-European",
    "language_branch": ["Germanic", "West Germanic", "Anglo-Frisian", "Anglic"],
    "period_label": "Old English",
    "date_start": 450,
    "date_end": 1100,
    "definition_primary": "water",
    "part_of_speech": ["noun"],
}

SAMPLE_LSR_PROTO_GERMANIC = {
    "id": str(uuid4()),
    "form_orthographic": "*watōr",
    "form_normalized": "wator",
    "language_code": "gem-pro",
    "language_name": "Proto-Germanic",
    "language_family": "Indo-European",
    "language_branch": ["Germanic"],
    "period_label": "Proto-Germanic",
    "date_start": -500,
    "date_end": 200,
    "definition_primary": "water",
    "reconstruction_flag": True,
}

# Sample language data
SAMPLE_LANGUAGES = [
    {
        "iso_code": "eng",
        "name": "English",
        "family": "Indo-European",
        "branch_path": ["Germanic", "West Germanic", "Anglo-Frisian", "Anglic"],
        "is_living": True,
    },
    {
        "iso_code": "deu",
        "name": "German",
        "family": "Indo-European",
        "branch_path": ["Germanic", "West Germanic", "High German"],
        "is_living": True,
    },
    {
        "iso_code": "lat",
        "name": "Latin",
        "family": "Indo-European",
        "branch_path": ["Italic", "Latino-Faliscan"],
        "is_living": False,
    },
]

# Sample etymology chain
SAMPLE_ETYMOLOGY_CHAIN = [
    {"from": "water (English)", "to": "wæter (Old English)", "type": "DESCENDS_FROM"},
    {"from": "wæter (Old English)", "to": "*watōr (Proto-Germanic)", "type": "DESCENDS_FROM"},
]
