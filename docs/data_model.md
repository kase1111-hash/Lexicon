# Data Model

## Lexical State Record (LSR)

An LSR is one form–meaning pair in one language, with the window in which it
is attested. It is stored as a Neo4j node with the label `:LSR`; the Pydantic
model lives in `src/models/lsr.py`.

**Dates.** `date_start` is the earliest attestation. `date_end` is the last
attestation, or empty while the word is still in use; ingestion never
invents an end date. Years are integers from -10000 to 2100, negative for
BCE (`YEAR_MIN` / `YEAR_MAX` in `src/models/lsr.py`); the model checks them
on every assignment, and ingestion drops a source year outside that range
with a warning, leaving the record undated.

| Field | Type | Stored | Notes |
|---|---|---|---|
| `id` | UUID | yes | Derived from the source record (`uuid5`), so re-ingesting updates the same node; a placeholder's from its language and form (see below) |
| `version` | int | yes | |
| `created_at`, `updated_at` | datetime | yes | Set by Neo4j on write |
| `form_orthographic` | string | yes | Written form. WOLD values lose parenthesized sense numbers and optional parts (`call (1)` → `call`, `(sea)gull` → `gull`, `wood(s)` → `wood`) |
| `form_phonetic` | string | yes | IPA, when the source gives it |
| `form_normalized` | string | yes | Lowercased, diacritics stripped; used for all matching |
| `language_code` | string | yes | ISO 639-3 where one exists; Wiktionary codes for historical varieties and proto-languages (`enm`, `gem-pro`; Late, Vulgar, Medieval and Neo-Latin are `la-lat`, `la-vul`, `la-med`, `la-new`); Glottolog code when a source has no ISO code. Arabic is `ara`: Wiktionary's `Arabic` sections and `ar` code, WOLD's Arabic donors, and `ar` in queries. A WOLD donor gets the ISO code WOLD itself lists for that language before its Glottolog code. A linked language that has a name but no code at all (WOLD donors such as `Proto-Altaic` or `Saharan`) gets `und` (undetermined), kept apart per language name, and never forms a contact event; WOLD's `Unidentified` donors get no link |
| `language_name`, `language_family` | string | yes | |
| `language_branch` | string[] | yes | |
| `period_label` | string | yes | The date as the source states it: WOLD's `Age` as given (`c. 1220`, `before 1382`, `14th century`, `1835`), or, for English words WOLD dates by a period, Lexicon's label for it (`Proto-Germanic` → `Old English (inherited from Proto-Germanic)`); for corpus records `earliest in corpus: <document title>`; empty for other sources. An undated record keeps the source's label when it has one (WOLD ages Lexicon does not read as a year, such as `Modern` or `Pre 100 CE`). Analyses return it as `date_label` |
| `date_start`, `date_end` | int | yes | See above |
| `date_confidence` | float 0–1 | yes | 1.0 for an exact year, lower for `c.`, `before`, centuries and period labels; 0.0 when ingestion finds no date (records created through `POST /api/v1/lsr/` keep the default 1.0). Corpus records take the `date_confidence` of the earliest document they appear in (default 1.0). When records merge, the earliest date brings its confidence and label |
| `date_source` | enum | yes | `ATTESTED`, `INTERPOLATED`, `RECONSTRUCTED` (proto-forms) |
| `definition_primary`, `definitions_alternate` | string, string[] | yes | WOLD: the meaning's name. WOLD numbers meanings that share a name; the number is replaced by what the Concepticon gloss adds (`male(1)` → `male (of person)`, `the spring(2)` → `the spring (springtime)`) |
| `semantic_fields` | string[] | yes | Source semantic field, e.g. WOLD's `The physical world` |
| `conceptual_domain` | string[] | yes | |
| `semantic_vector` | float[384] | yes | Hashed n-gram encoding of the definitions (`src/pipelines/embedding.py`) |
| `etymology_text` | string | yes | Etymology as given by the source |
| `part_of_speech` | string[] | yes | |
| `register`, `frequency_score`, `frequency_source` | | yes | Not filled by any current source |
| `reconstruction_flag` | bool | yes | True for starred proto-forms |
| `confidence_overall` | float 0–1 | yes | |
| `source_databases` | string[] | yes | Provenance, e.g. `["wold"]` |
| `human_validated`, `validation_notes` | bool, string | yes | Ingestion notes possible duplicates here |
| `ancestor_ids`, `descendant_ids`, `cognate_ids`, `loan_source_id`, `loan_target_ids` | UUIDs | derived | Filled from edges when an LSR is read through the API |
| `attestations` | list | no | Model-only; the corpus adapter's per-document evidence is reduced to the earliest year and the title in `period_label` |

## Relationships

All relationships connect two `:LSR` nodes.

| Type | Direction | Produced by |
|---|---|---|
| `BORROWED_FROM` | borrowing word → donor word | WOLD donor data; Wiktionary `{{bor}}` / `{{cal}}` |
| `DESCENDS_FROM` | word → ancestor | Wiktionary `{{inh}}` / `{{der}}` chains; etymology text |
| `COGNATE_OF` | word → cognate | Wiktionary `{{cog}}` |
| `SHIFTED_TO`, `MERGED_WITH`, `RELATED_TO` | | Accepted by the repository; no current source produces them |

Relationship properties: `confidence` (0–1), `evidence` (the source template
or etymology text), `created_at`.

**Placeholder LSRs.** When a link points to a word that is not among the
records of the ingestion run (a WOLD donor word, a Wiktionary ancestor),
ingestion creates a minimal LSR for it: form, language, gloss if known,
`reconstruction_flag` for `*` forms, no dates (`date_confidence` 0.0). Its
id is derived from the language and normalized form, so every run that
links to the same word uses the same node; ingesting the word itself from a
source creates a separate record. Placeholders are written fill-only:

- a new node gets every property;
- an existing node keeps each property that is set and not empty, gains the
  missing ones, and its `source_databases` become the union of both;
- the dating (`date_start`, `date_end`, `date_confidence`, `date_source`,
  `period_label`) is taken only as a whole, and only onto a node with no
  dates; a dated node gains none of it.

A later run therefore never blanks an earlier run's gloss or provenance.

## Schema

Created idempotently by `LSRRepository.ensure_schema()` on every ingestion
run and at API startup:

- `CONSTRAINT lsr_id_unique` — `LSR.id` is unique
- `INDEX lsr_language_form` — `(language_code, form_normalized)`
- `INDEX lsr_form_normalized`, `INDEX lsr_language_code`

## Elasticsearch document

Index `lexicon_lsr` holds a subset of each LSR for fuzzy search: id, forms,
language code/name/family, primary definition, dates, period label,
confidence, reconstruction flag, sources and semantic fields.
