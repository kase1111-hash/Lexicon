# FAQ

Lexicon dates a text by its words. The answers below assume you have read the
[README](../README.md); [architecture.md](architecture.md) and
[data_model.md](data_model.md) describe the pipeline and the records.

- [Why is my graph empty, or why do I get `insufficient_data`?](#why-is-my-graph-empty-or-why-do-i-get-insufficient_data)
- [Which sources give dates?](#which-sources-give-dates)
- [How accurate is the dating?](#how-accurate-is-the-dating)
- [What does `date_label` mean?](#what-does-date_label-mean)
- [What does `confidence` mean?](#what-does-confidence-mean)
- [Can I use my own texts?](#can-i-use-my-own-texts)
- [Why is a common word flagged as `earliest in corpus`?](#why-is-a-common-word-flagged-as-earliest-in-corpus)
- [Which languages work?](#which-languages-work)
- [Why are there two records for one word?](#why-are-there-two-records-for-one-word)
- [What does semantic drift measure?](#what-does-semantic-drift-measure)
- [Do I need Elasticsearch, Redis or PostgreSQL?](#do-i-need-elasticsearch-redis-or-postgresql)
- [How do I add a source?](#how-do-i-add-a-source)

## Why is my graph empty, or why do I get `insufficient_data`?

Lexicon ships no word records (only a four-document sample corpus, see
below). A fresh Neo4j is empty until you run an ingestion; until then
`date-text`, `detect-anachronisms` and `semantic-drift` answer
`insufficient_data`, and `contact-events` returns an empty list:

```console
$ lexicon stats
LSRs: 0
Relationships: 0
The graph is empty. Load data with: lexicon ingest --source wold --language English
```

```console
$ curl -s -X POST http://localhost:8000/api/v1/analyze/date-text -H 'Content-Type: application/json' \
    -d '{"text": "The knight rode forth to the castle with his sword", "language": "eng"}'
{
  "predicted_date_range": null,
  "confidence": 0.0,
  "status": "insufficient_data",
  "explanation": "None of the 5 content words are in the lexical graph for 'eng'. Ingest data for this language first.",
  "diagnostic_vocabulary": [],
  "analysis": {
    "language": "eng",
    "text_length": 50,
    "word_count": 10,
    "tokens_analyzed": 10,
    "content_words": 5,
    "dated_words": 0,
    "unknown_words": ["knight", "rode", "forth", "castle", "sword"],
    "method": "vocabulary_attestation"
  }
}
```

`lexicon ingest --source wold --language English` loads 1,516 English words
(1,511 of them dated), 630 donor words and 642 borrowing links. After that,
the `explanation` field says which case you are in:

| `explanation` | Cause | What to do |
|---|---|---|
| `None of the N content words are in the lexical graph for 'xxx'` | Nothing is loaded for that language code, or the words are not in the loaded data | Ingest a source for the language. WOLD's English list covers about 1,500 basic meanings, so words like *knight* are missing |
| `K of N content words are in the graph … but none has an attestation date` | The words came from an undated source (CLICS/IDS, most WOLD languages, many Wiktionary entries) | Load a dated source for the language (see below) |
| `Only K of N content words have attestation dates; too few to judge …` | Anachronism check only: under half of the content words are dated, so "no anachronisms found" would mean nothing | Load more data, or read the result as "not checked" |

Other causes:

- The ingestion ran with `--dry-run`, or it failed. The summary has a line
  `Written to graph: N LSRs, M relationships` (`Graph write: skipped
  (--dry-run)` on a dry run). A run that fetched or wrote nothing exits with
  status 1, one that cannot reach Neo4j with status 2. A run that wrote only
  part of its records also exits with status 1; its summary adds a line
  `Failed to write: N LSRs, M relationships` and the reason under `Errors`.
- The CLI or API is reading a different Neo4j from the one you loaded. Both
  read `NEO4J_URI` / `NEO4J_PASSWORD` from the environment, then from `./.env`
  in the directory you run them from (or the file named by `ENV_FILE`).
- `--language` on `lexicon ingest` did not match. For Wiktionary, WOLD and
  CLICS it takes one or more languages, comma-separated, each as a name
  (`English`), an ISO 639-3 code (`eng`) or an ISO 639-1 code (`en`):
  `--language "English, nld"` loads English and Dutch. WOLD and CLICS also
  take Glottolog codes (`stan1293`). A value that names no language of the
  source, such as `Englsh`, matches nothing and the run ends with `Nothing
  was ingested: the source returned no entries for these options`. For
  `--source corpus` it is the one language of documents whose metadata names
  none, as a name or code (`English`, `eng`, `en`). Unlike the other
  sources, names and ISO 639-3 codes are case-sensitive here: `english` and
  `ENG` are refused like `Englsh`, which stops with `Unknown corpus language
  'Englsh'; pass an ISO 639-3 code`.

## Which sources give dates?

| Source | Dates | Where the year comes from |
|---|---|---|
| WOLD (`--source wold`) | Yes, for 18 of its 41 languages | The `Age` column: years (`1835`, `c. 1297`, `before 1225`, `1432-1450`), centuries (`14th century` becomes 1300), English period labels (`Proto-Germanic` and `Old English` become 700, `Late Old English` 900) and Japanese periods. Other labels (`Modern`, `Hausa only`, `Pre 100 CE`, dynasty names) give no date |
| Wiktionary (`--source wiktionary`) | Only where an entry states one | `{{defdate}}` templates, "first attested in …" phrases and the years of dated quotations; the earliest year is used. Years and centuries marked BC or BCE become negative years (`8th c. BCE` is -800); unmarked years and centuries count only from 500 on, so a stray small number is not read as a year. Many entries have none |
| CLICS / IDS (`--source clics`) | No | Colexification data only |
| Your corpus (`--source corpus`) | Yes | The `date` of the earliest document in the corpus that contains the word, with that document's `date_confidence` |

WOLD languages with the most dated words: Japanese 2,071, Seychelles Creole
1,764, Dutch 1,587, English 1,511, Wichí 1,361, Mapudungun 1,092, Ket 870.
The dates are coarse where they come from labels: every English word
inherited from Proto-Germanic is dated 700 (the start of Old English).
WOLD labels 892 inherited Romanian words `Pre 100 CE`, the date of their
Latin etymon rather than of the Romanian word; they stay undated, so 492 of
Romanian's 2,270 words have a date. On each dated record, `period_label`
holds the date as the source states it (English period labels are expanded:
`Proto-Germanic` becomes `Old English (inherited from Proto-Germanic)`), and
`date_confidence` says how exact it is (for WOLD, 1.0 for a plain year down
to 0.5 for a period). The analyses return the label as
[`date_label`](#what-does-date_label-mean). Undated records keep the
source's label (the Romanian ones have `period_label` `Pre 100 CE`) and have
`date_confidence` 0.0.

## How accurate is the dating?

There are no accuracy figures: the dating has not been evaluated against texts
of known date. What the method gives is a bound, and how good it is depends on
the data behind it.

- **Terminus post quem.** A text cannot be older than its newest word. The
  lower end of `predicted_date_range` is the latest first-attestation year
  among the text's dated words, and `diagnostic_vocabulary` marks that word
  with `"sets_bound": "lower"`. A first attestation is the earliest evidence
  the source has, not the moment the word was coined.
- **Upper end.** It is the current year unless a dated word has a last
  attestation (`date_end`) before then. No current source records last
  attestations, so in practice the range always ends at the current year.
- **Coverage.** Only words in the graph count. Check `dated_words` against
  `content_words` and read `unknown_words`: a text whose newest word is not in
  the graph gets a bound that is too early.

```console
$ lexicon analyze date-text --text "The king rode to the castle and wrote a letter to the priest"
Status: ok
Estimated date range: 1225-2026
Confidence: 1.00
Dated words: 6 of 6 content words
Written no earlier than 1225 (first attestation of letter); 6 of 6 content words dated.
```

`status: conflicting_evidence` means one word was coined after another fell
out of use; the coinage bound is kept and the obsolete word may be a
deliberate archaism.

## What does `date_label` mean?

It is the first-attestation date as its source states it. The year next to it
(`date_start` in `diagnostic_vocabulary`, `earliest_attestation` in an
anachronism) is what the analyses compute with; the label says how exact that
year is and where it comes from:

| `date_label` | Source | Year used |
|---|---|---|
| `1913`, `c. 1297`, `before 1225`, `c. 1340-1370` | WOLD, a dated `Age` | The (first) year |
| `14th century` | WOLD, a century | 1300 |
| `Old English (inherited from Proto-Germanic)` | WOLD, an English period | 700 |
| `earliest in corpus: <document title>` | Your corpus | The `date` of that document |
| `""` | A source that states no label (Wiktionary) | The year the source gives |

The REST API puts it on each `diagnostic_vocabulary` entry of `date-text` and
on each `coined_after` entry of `detect-anachronisms`; GraphQL has `dateLabel`
on `DiagnosticWord` and `Anachronism`. `lexicon analyze anachronisms` prints
it when it says more than the year:

```console
$ lexicon analyze anachronisms --date 1100 --text "The river ran under the sky past the fortress"
Verdict: anachronistic (confidence 0.95)
Dated words: 5 of 6 content words
3 word(s) first attested well after 1100: fortress (1300), river (1297), sky (1220).
  - fortress: first attested 1300 (14th century; 200 years after 1100, high)
  - river: first attested 1297 (c. 1297; 197 years after 1100, high)
  - sky: first attested 1220 (c. 1220; 120 years after 1100, high)
Not in graph: past
```

When a form has several records, the label is that of the record with the
earliest date.

## What does `confidence` mean?

It measures how much of the text was checked, not the probability that the
answer is right. Coverage below is dated content words divided by content
words (`dated_words` / `content_words` in the response). Stop words and words
of one or two letters are not content words, and a word that occurs several
times counts once.

| Result | `confidence` |
|---|---|
| `date-text`, `status: ok` | `coverage × min(1, dated_words / 5)`: 6 of 6 dated words gives 1.0 |
| `date-text`, `conflicting_evidence` | Half of the above |
| `detect-anachronisms`, `anachronistic` / `suspicious` | `0.5 + 0.15` per word first attested more than 50 years after the claimed date, `+ 0.1` if one is over 200 years, at most 0.95 |
| `detect-anachronisms`, `consistent` | Same formula as `date-text` |
| any `insufficient_data` | 0 |

Here *castle* counts once, so 4 of 5 content words are dated: 0.8 × 4/5 = 0.64.

```console
$ lexicon analyze date-text --text "The knight saw the castle, the castle wall and the castle gate"
Status: ok
Estimated date range: 1075-2026
Confidence: 0.64
Dated words: 4 of 5 content words
Written no earlier than 1075 (first attestation of castle); 4 of 5 content words dated.
Not in graph: knight
```

A word first attested 50 years or less after the claimed date is listed with
severity `low` and does not change the verdict; over 50 years is `medium`,
over 100 is `high`. The explanation names such words:

```console
$ lexicon analyze anachronisms --date 1900 --text "The king listened to the radio in his castle"
Verdict: consistent (confidence 0.80)
Dated words: 4 of 4 content words
No word among the 4 dated content words (of 4) is first attested more than 50 years after 1900; 1 postdates it by 50 years or less: radio (1913).
  - radio: first attested 1913 (13 years after 1900, low)
```

## Can I use my own texts?

Texts you want to analyse need no ingestion: pass them with `--text` or
`--file`, or in the API request body. To use your own dated documents as
evidence, ingest them as a corpus: a directory of `.txt` files, each with an
optional `.json` sidecar of the same name.

```json
{
  "title": "Geoffrey Chaucer, The Canterbury Tales, General Prologue, lines 1-18",
  "date": 1390,
  "date_confidence": 0.8,
  "language": "Middle English",
  "language_code": "enm",
  "source": "corpus:chaucer_general_prologue.txt",
  "notes": "Written c. 1387-1400; text of W. W. Skeat's edition (1894). Public domain."
}
```

That is `data/corpus/chaucer_general_prologue.json`. The keys read are
`title`, `date`, `date_confidence`, `language`, `language_code`, `source` and
`url`, all optional (other keys such as `notes` are ignored); a
`metadata.json` keyed by file name works too. `date` is a year from -10000
to 2100 (negative for BCE); a document with a date outside that range is
treated as undated, with a warning. `date_confidence` is a number from 0 to
1. Every distinct word of three or more letters becomes one record per
language, dated by the earliest document it appears in, with that document's
`date_confidence` and the `date_label` `earliest in corpus: <title>`. Words
are split as the analyses split them, so a word keeps its combining marks in
any script.

```bash
lexicon ingest --source corpus --corpus-dir data/corpus --language English
```

`--language` sets the language of documents whose metadata does not name one,
as a name or code (`English`, `eng` or `en`).
[`data/corpus`](../data/corpus) holds four short public-domain excerpts
(Chaucer 1390, King James Bible 1611, Austen 1813, Wells 1898). They show the
format. Do not load them into a graph you use for dating: words the demo
first meets in 1611 get that date. With WOLD's English list and the excerpts
loaded:

```console
$ lexicon analyze anachronisms --date 1300 --text "The king looked upon the castle in the darkness"
Verdict: anachronistic (confidence 0.75)
Dated words: 5 of 5 content words
1 word(s) first attested well after 1300: upon (1611).
  - upon: first attested 1611 (earliest in corpus: King James Bible, Genesis 1:1-5; 311 years after 1300, high)
```

A corpus gives useful first attestations only when it is large and reaches
back before the texts you check. To remove corpus records again, see
[troubleshooting](troubleshooting.md#remove-a-source-from-the-graph).

## Why is a common word flagged as `earliest in corpus`?

Because a corpus dated it. `earliest in corpus: <title>` means the date is
that of the first corpus document containing the word, which is its first
attestation only as far as the corpus reaches. With WOLD's English list and
the sample corpus loaded:

```console
$ lexicon analyze anachronisms --date 1700 --text "She watched from her window and told him the truth"
Verdict: suspicious (confidence 0.80)
Dated words: 4 of 4 content words
2 word(s) first attested well after 1700: watched (1898), truth (1813).
  - watched: first attested 1898 (earliest in corpus: H. G. Wells, The War of the Worlds, book 1, chapter 1 (opening); 198 years after 1700, high)
  - truth: first attested 1813 (earliest in corpus: Jane Austen, Pride and Prejudice, chapter 1 (opening); 113 years after 1700, high)
```

This happens in two cases:

- No other source has the word. WOLD has neither *watched* nor *truth*.
  Without the sample corpus the same check lists both as `Not in graph` and
  answers `consistent` from 2 of 4 dated words.
- The corpus has the inflected form. A token is looked up as written, and as
  its base form (`waters` → `water`) only when the written form is not in the
  graph. A corpus stores every form it meets, so its `waters` (1611) is used
  instead of WOLD's `water` (700), and "The king saw the waters of the river"
  is `anachronistic` for 1400.

Records of the same form do not cause it: the analyses use the earliest date
among them. Remove the sample corpus
([troubleshooting](troubleshooting.md#remove-a-source-from-the-graph)), or
load a corpus large enough to date the words you check.

## Which languages work?

Any language whose words are in the graph with dates, looked up by the
language code of the records. The API and CLI accept:

- ISO 639-3 codes (`eng`, `nld`, `jpn`, `enm`, `ang`);
- 36 common ISO 639-1 codes, mapped to ISO 639-3 (`en` → `eng`, `ar` →
  `ara`; the list is in `src/utils/languages.py`);
- Wiktionary codes for historical varieties and proto-languages: a 2-3
  letter code and one or two parts of 3-4 letters (`gem-pro`, `la-vul`,
  `ine-bsl-pro`). A REST `language` field takes at most 20 characters;
- Glottolog codes, which records get where a source has no ISO code. WOLD's
  Sakha is `yaku1245`. A WOLD donor language gets a known ISO or Wiktionary
  code, or the ISO code WOLD gives that language, and its Glottolog code only
  when there is neither (`celt1248` for Celtic). A donor that WOLD names
  without any code, such as `Pre-Rangi` or `Saharan`, gets the ISO 639-3
  code `und` (undetermined), and borrowings from `und` never form a contact
  event. WOLD's `Unidentified` donors get no link at all.

Anything else is refused, for example `english`, `en-gb` or `12`:

```console
$ lexicon analyze date-text --language en-gb --text "The king rode to the castle"
Error: Invalid language code 'en-gb': use an ISO 639-3 code such as 'eng' (or 'gem-pro' for proto-languages, or a Glottolog code such as 'yaku1245')
```

English works best:

- WOLD dates 1,511 English words, and the Wiktionary seed list
  (`data/seed_words_eng.txt`) is English.
- Inflected tokens fall back to base forms (`computers` → `computer`,
  `rode` → `ride`, `wrote` → `write`) for English only. A base form is tried
  before a shorter word that the ending could also leave: `hoped` is looked
  up as `hope` before `hop`, `fades` as `fade` before `fad`. In other
  languages a token must match the stored form exactly, apart from case and
  diacritics.
- WOLD stores some languages in romanization (Japanese `sekai`, Mandarin
  `shi4jie4`), so a text in kana, kanji or hanzi matches nothing.

Tokenization handles any script. A word keeps its combining marks
(Devanagari and Tamil vowel signs, Arabic harakat, Hebrew niqqud), so they do
not split it. Matching ignores case and diacritics (`téléphone` matches
`telephone`).

## Why are there two records for one word?

Duplicate detection (entity resolution) only compares records with the same
normalized form and language within one ingestion run. The same word loaded
from two sources, or in two runs, stays as two records. Re-running the same
source does not duplicate: record ids are derived from the source record, so
the run updates them.

```console
$ lexicon search --form water --language eng
4 match(es) for 'water' in eng
  water [eng] 700-present 'the water'  id=03f24a96-212c-5f03-9ee9-024e6f7a7dde
  water [eng] 1898-present  id=c0d788bc-0ba7-5b5c-b34e-f04efc82a790
  waters [eng] 1611-present  id=8721e785-8d2f-5c62-9cdb-728e1f456f7f
  waterfall [eng] 992-present 'the waterfall'  id=541e7364-a06f-5747-b56f-4fc44dead563
```

The first `water` comes from WOLD, the second from the sample corpus. WOLD
also has several records for one form when it lists the form under several
meanings. Within a run, ingestion merges near-certain duplicates (`LSRs
merged` in the summary) and only flags likely ones (`LSRs flagged`, noted in
`validation_notes`). The analyses combine all records of a form and use the
earliest `date_start`, so duplicates do not change a dating result.

## What does semantic drift measure?

It is experimental. For one form in one language it compares the definitions
of its senses (records) that have a first-attestation date and a definition
vector (computed from the definition at ingestion; records created with
`POST /lsr/` have none). Records without either are left out. Each definition is turned into a
384-number vector by hashing its words and character trigrams
(`src/pipelines/embedding.py`), so the distance measures how differently two
senses are worded, not a model of meaning. The vector also carries a small
component for the sense's date, so identical definitions from different
centuries still differ slightly.

Drift is change over time, so senses first attested in the same year are not
compared with each other: each sense is compared with the closest sense of
the latest earlier date. Unless the senses have at least two different dates,
`status` is `insufficient_data` and `explanation` says why (in
`compare-concept`, per language; GraphQL `semanticTrajectory` returns no
points, with the same `status` and `explanation`):

```console
$ lexicon analyze drift --form day --language eng
insufficient_data: Found 2 dated senses with a definition for 'day' in 'eng', all first attested in 700; senses of the same date are not a change over time, and drift needs senses first attested in at least two different years.
```

The senses come from records that share a form, so a result is only as
meaningful as those records. After a WOLD English ingestion, 45 English forms
have two or more dated records with different definitions, one per WOLD
meaning. For 32 of them every sense has the same date, like the two senses of
*day* above (`the day (24 hours)` and `the day (not night)`, both 700), so
only 13 forms give a result. Many of those are homonyms rather than one word
changing meaning: for *post* the API reports a shift from `the post or pole`
(700) to `the post/mail` (1506), `"total_drift": 0.4595`, although these are
two words of different origin.

## Do I need Elasticsearch, Redis or PostgreSQL?

No. Only Neo4j is required.

| Store | Used for | Without it |
|---|---|---|
| Neo4j | The graph; every analysis, search and ingestion | The CLI exits with status 2 (except `validate` and `extract-rels`); `/health` and the `/api/v1` endpoints answer 503 ([troubleshooting](troubleshooting.md#503-database_error-from-the-api)) |
| Elasticsearch | Form search that also finds near misses (typos) and ranks by relevance (`lexicon search`, `/api/v1/lsr/search`) | Search runs in Neo4j as a substring match |
| Redis | Response cache, export job state and rate-limit counters shared between API workers | No cache; jobs and counters are kept per process, so run one worker |
| PostgreSQL | Nothing yet (compose profile `postgres`, migrations only) | No difference |

The API and CLI use an optional store only when it is configured:
Elasticsearch when `ELASTICSEARCH_URI` or `ELASTICSEARCH_PASSWORD` is set,
Redis when `REDIS_URI` or `REDIS_PASSWORD` is set, PostgreSQL when
`POSTGRES_URI` is set. The compose `.env` sets `ELASTICSEARCH_PASSWORD` and
`REDIS_PASSWORD`, so the compose stack uses both. `/health` reports a store
that is not configured as `not_configured`; it does not affect the status,
so a Neo4j-only setup is `healthy`:

```json
{"status":"healthy","api":"up","databases":{"neo4j":"connected","postgres":"not_configured","elasticsearch":"not_configured","redis":"not_configured"}}
```

A configured store that does not answer makes the status `degraded`
([troubleshooting](troubleshooting.md#what-health-says)).

## How do I add a source?

Write a subclass of `SourceAdapter` (`src/adapters/base.py`) that yields
`RawLexicalEntry` objects. Six methods are abstract: `connect`, `disconnect`,
`fetch_batch`, `get_total_count`, `get_last_modified` and
`supports_incremental`. This adapter reads a CSV with the columns
`word,year,gloss`:

```python
import csv
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from src.adapters.base import RawLexicalEntry, SourceAdapter


class GlossaryAdapter(SourceAdapter):
    """Reads a CSV with columns word,year,gloss."""

    def __init__(self, path: str, language: str = "English", language_code: str = "eng"):
        super().__init__()
        self.path = Path(path)
        self.language, self.language_code = language, language_code
        self._rows: list[dict[str, str]] = []

    def connect(self) -> None:
        if not self.path.is_file():
            raise ConnectionError(f"Glossary not found: {self.path}")  # clean CLI error
        with self.path.open(encoding="utf-8") as f:
            self._rows = list(csv.DictReader(f))

    def disconnect(self) -> None:
        self._rows = []

    def fetch_batch(self, offset: int, limit: int) -> Iterator[RawLexicalEntry]:
        for i, row in enumerate(self._rows[offset : offset + limit], start=offset):
            yield RawLexicalEntry(
                source_id=f"glossary-{i}",  # stable: re-runs update the same record
                source_name="glossary",
                form=row["word"],
                language=self.language,
                language_code=self.language_code,
                definitions=[row["gloss"]] if row.get("gloss") else [],
                date_attested=int(row["year"]) if row.get("year") else None,
            )

    def get_total_count(self) -> int:
        return len(self._rows)

    def get_last_modified(self) -> datetime:
        return datetime.fromtimestamp(self.path.stat().st_mtime)

    def supports_incremental(self) -> bool:
        return False
```

- `date_attested` is the first-attestation year and becomes `date_start`
  (negative for BCE; a year outside -10000 to 2100 is dropped with a
  warning). To report how the source states the date and how exact it is,
  add `raw_data={"period_label": "c. 1300", "date_confidence": 0.8}`; the
  analyses return the label as `date_label`. A dated record without
  `date_confidence` gets 1.0, an undated one 0.0.
- Links go in `related_forms`, for example
  `{"type": "borrowed_from", "form": "ciel", "language": "Old French", "language_code": "fro"}`.
  The types are `borrowed_from`, `bor` and `cal` (`BORROWED_FROM` edges),
  `inh` and `der` (`DESCENDS_FROM`), and `cog` (`COGNATE_OF`).
- `src/ingestion.py` runs any adapter through validation, entity resolution
  and the graph writer. To add it to `lexicon ingest`, write a
  `run_<name>_ingestion` function modelled on `run_corpus_ingestion`, add the
  name to the `--source` choices in `add_arguments`, and add a branch to
  `_run_from_args`.
- Raise `ConnectionError` when the data cannot be read, so the CLI prints the
  message instead of a traceback. Add tests under `tests/unit/`, and see
  [CONTRIBUTING.md](../CONTRIBUTING.md).
