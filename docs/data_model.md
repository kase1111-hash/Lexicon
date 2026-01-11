# Data Model Documentation

## Lexical State Record (LSR)

The LSR is the core data unit representing a word at a specific point in time and space.

### Identity Fields
- `id` (UUID): Unique identifier
- `version` (int): Record version for tracking updates
- `created_at`, `updated_at` (timestamp): Audit timestamps

### Form Fields
- `form_orthographic` (string): Written form
- `form_phonetic` (string): IPA representation
- `form_normalized` (string): Lowercased, diacritic-stripped for matching

### Language & Time Fields
- `language_code` (string): ISO 639-3 code
- `language_name` (string): Human-readable name
- `language_family` (string): Top-level family
- `language_branch` (array): Full lineage path
- `period_label` (string): e.g., "Middle English"
- `date_start`, `date_end` (int): Year range (negative for BCE)
- `date_confidence` (float): 0.0-1.0
- `date_source` (enum): ATTESTED, INTERPOLATED, RECONSTRUCTED

### Semantic Fields
- `semantic_vector` (float[384]): Embedding
- `semantic_fields` (array): WordNet synset IDs
- `definition_primary` (string): Main gloss
- `definitions_alternate` (array): Other meanings
- `conceptual_domain` (array): High-level categories

### Usage Fields
- `register` (enum): FORMAL, COLLOQUIAL, TECHNICAL, SACRED, LITERARY, SLANG
- `frequency_score` (float): Normalized 0-1
- `part_of_speech` (array): Noun, verb, etc.

### Metadata
- `reconstruction_flag` (bool): Is this a *starred proto-form?
- `confidence_overall` (float): Aggregate confidence
- `source_databases` (array): Data provenance
- `human_validated` (bool): Expert reviewed?

## Relationships (Edges)

### Relationship Types
- `DESCENDS_FROM`: Direct inheritance within language lineage
- `BORROWED_FROM`: Loan from another language
- `COGNATE_OF`: Shared ancestor (symmetric)
- `SHIFTED_TO`: Semantic change within same form
- `MERGED_WITH`: Multiple forms converging

### Edge Properties
- `confidence` (float): 0.0-1.0
- `date_of_change` (int): When the relationship occurred
- `change_type` (string): For semantic shifts
- `evidence` (array): Supporting references

## Attestation

Evidence of a word's usage in a historical text.

- `text_excerpt` (string): The actual usage
- `text_source` (string): Document/corpus name
- `text_date` (int): Year of text
- `page_reference` (string): Location in source
- `url` (string): Online reference if available
