# Computational Linguistic Stratigraphy
## Automated Implementation Specification v1.0

---

## Executive Summary

This specification defines the automated infrastructure for building and maintaining a cross-linguistic lexical evolution graph. The system ingests etymological data from multiple sources, constructs a unified knowledge graph, trains diachronic embeddings, and exposes analysis tools via API.

**Target State:** A self-updating linguistic knowledge graph that requires minimal human intervention after initial configuration.

---

## 1. System Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           ORCHESTRATION LAYER                               │
│                    (Airflow / Prefect / Temporal)                          │
└─────────────────────────────────────────────────────────────────────────────┘
        │                    │                    │                    │
        ▼                    ▼                    ▼                    ▼
┌───────────────┐  ┌───────────────┐  ┌───────────────┐  ┌───────────────┐
│   INGESTION   │  │  PROCESSING   │  │   TRAINING    │  │   SERVING     │
│   PIPELINE    │  │   PIPELINE    │  │   PIPELINE    │  │   LAYER       │
├───────────────┤  ├───────────────┤  ├───────────────┤  ├───────────────┤
│ • Wiktionary  │  │ • Entity Res. │  │ • Embeddings  │  │ • REST API    │
│ • CLLD/CLICS  │  │ • Dedup       │  │ • Clustering  │  │ • GraphQL     │
│ • Corpora     │  │ • Linking     │  │ • Phylogeny   │  │ • WebSocket   │
│ • OCR Texts   │  │ • Validation  │  │ • Classifiers │  │ • Batch Jobs  │
└───────────────┘  └───────────────┘  └───────────────┘  └───────────────┘
        │                    │                    │                    │
        ▼                    ▼                    ▼                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                            STORAGE LAYER                                    │
├─────────────────┬─────────────────┬─────────────────┬───────────────────────┤
│   Neo4j/Arango  │    Pinecone     │  Elasticsearch  │     PostgreSQL        │
│   (Graph)       │    (Vectors)    │  (Full-text)    │     (Metadata)        │
└─────────────────┴─────────────────┴─────────────────┴───────────────────────┘
```

---

## 2. Data Model Specification

### 2.1 Lexical State Record (LSR) Schema

```yaml
LSR:
  # Identity
  id: UUID
  version: Integer                    # For tracking updates
  created_at: Timestamp
  updated_at: Timestamp
  
  # Form
  form_orthographic: String           # Written form
  form_phonetic: String               # IPA representation
  form_normalized: String             # Lowercased, diacritic-stripped for matching
  
  # Language & Time
  language_code: String               # ISO 639-3
  language_name: String               # Human readable
  language_family: String             # Top-level family
  language_branch: String[]           # Full lineage path
  period_label: String                # e.g., "Middle English", "Classical Latin"
  date_start: Integer                 # Year (negative for BCE)
  date_end: Integer                   # Year
  date_confidence: Float              # 0.0 - 1.0
  date_source: Enum                   # ATTESTED, INTERPOLATED, RECONSTRUCTED
  
  # Semantics
  semantic_vector: Float[384]         # Embedding (sentence-transformers dimension)
  semantic_fields: String[]           # WordNet synset IDs
  definition_primary: String          # Main gloss
  definitions_alternate: String[]     # Other meanings
  conceptual_domain: String[]         # High-level categories
  
  # Usage
  register: Enum                      # FORMAL, COLLOQUIAL, TECHNICAL, SACRED, LITERARY, SLANG
  frequency_score: Float              # Normalized 0-1
  frequency_source: String            # Corpus reference
  part_of_speech: String[]            # Noun, verb, etc.
  
  # Evidence
  attestations: Attestation[]         # See sub-schema
  
  # Relationships (stored as graph edges, referenced here for completeness)
  ancestor_ids: UUID[]
  descendant_ids: UUID[]
  cognate_ids: UUID[]
  loan_source_id: UUID | null
  loan_target_ids: UUID[]
  
  # Metadata
  reconstruction_flag: Boolean        # Is this a *starred proto-form?
  confidence_overall: Float           # Aggregate confidence
  source_databases: String[]          # Where this data came from
  human_validated: Boolean            # Has expert reviewed?
  validation_notes: String
  
Attestation:
  id: UUID
  lsr_id: UUID
  text_excerpt: String                # The actual usage
  text_source: String                 # Document/corpus name
  text_date: Integer                  # Year of text
  text_date_confidence: Float
  page_reference: String
  url: String | null
  
Edge:
  id: UUID
  source_id: UUID
  target_id: UUID
  relationship_type: Enum             # DESCENDS_FROM, BORROWED_FROM, COGNATE_OF, SHIFTED_TO, MERGED_WITH
  confidence: Float
  date_of_change: Integer | null      # When did this transition occur?
  change_type: String | null          # For SHIFTED_TO: metaphor, metonymy, generalization, etc.
  evidence: String[]                  # Supporting references
  created_at: Timestamp
```

### 2.2 Database Schema (PostgreSQL - Metadata)

```sql
-- Core tables for relational queries and metadata

CREATE TABLE languages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    iso_code VARCHAR(3) UNIQUE NOT NULL,
    name VARCHAR(255) NOT NULL,
    family VARCHAR(255),
    branch_path TEXT[],
    is_living BOOLEAN DEFAULT true,
    is_reconstructed BOOLEAN DEFAULT false,
    speaker_count INTEGER,
    geographic_region TEXT[],
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE corpora (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL,
    description TEXT,
    language_codes TEXT[],
    date_range_start INTEGER,
    date_range_end INTEGER,
    source_url TEXT,
    license VARCHAR(100),
    ingestion_status VARCHAR(50) DEFAULT 'pending',
    last_ingested TIMESTAMP,
    record_count INTEGER DEFAULT 0
);

CREATE TABLE ingestion_jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_type VARCHAR(100) NOT NULL,
    source_identifier TEXT NOT NULL,
    status VARCHAR(50) DEFAULT 'queued',
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    records_processed INTEGER DEFAULT 0,
    records_created INTEGER DEFAULT 0,
    records_updated INTEGER DEFAULT 0,
    records_failed INTEGER DEFAULT 0,
    error_log TEXT,
    config JSONB
);

CREATE TABLE validation_queue (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    lsr_id UUID NOT NULL,
    validation_type VARCHAR(100),
    priority INTEGER DEFAULT 5,
    assigned_to VARCHAR(255),
    status VARCHAR(50) DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT NOW(),
    completed_at TIMESTAMP
);

CREATE TABLE contact_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    donor_language UUID REFERENCES languages(id),
    recipient_language UUID REFERENCES languages(id),
    date_start INTEGER,
    date_end INTEGER,
    contact_type VARCHAR(100),  -- conquest, trade, religious, etc.
    vocabulary_count INTEGER,
    confidence Float,
    detected_by VARCHAR(50),    -- 'automated' or 'manual'
    validated BOOLEAN DEFAULT false,
    notes TEXT
);

-- Indexes
CREATE INDEX idx_languages_family ON languages(family);
CREATE INDEX idx_ingestion_status ON ingestion_jobs(status);
CREATE INDEX idx_validation_priority ON validation_queue(priority DESC, created_at ASC);
```

### 2.3 Graph Schema (Neo4j / ArangoDB)

```cypher
// Node Labels
(:LSR {
    id: String,
    form: String,
    form_normalized: String,
    language_code: String,
    period_label: String,
    date_start: Integer,
    date_end: Integer,
    definition: String,
    confidence: Float,
    reconstruction: Boolean
})

(:Language {
    iso_code: String,
    name: String,
    family: String
})

(:SemanticField {
    synset_id: String,
    label: String,
    domain: String
})

(:Corpus {
    id: String,
    name: String
})

// Relationship Types
(lsr1:LSR)-[:DESCENDS_FROM {confidence: Float, date: Integer}]->(lsr2:LSR)
(lsr1:LSR)-[:BORROWED_FROM {confidence: Float, date: Integer, contact_type: String}]->(lsr2:LSR)
(lsr1:LSR)-[:COGNATE_OF {confidence: Float}]->(lsr2:LSR)
(lsr1:LSR)-[:SHIFTED_TO {change_type: String, date: Integer}]->(lsr2:LSR)
(lsr:LSR)-[:IN_LANGUAGE]->(lang:Language)
(lsr:LSR)-[:HAS_MEANING]->(sf:SemanticField)
(lsr:LSR)-[:ATTESTED_IN]->(corpus:Corpus)
(lang1:Language)-[:DESCENDS_FROM]->(lang2:Language)
(lang1:Language)-[:CONTACT_WITH {type: String, period: String}]->(lang2:Language)

// Indexes
CREATE INDEX lsr_form FOR (n:LSR) ON (n.form_normalized);
CREATE INDEX lsr_language FOR (n:LSR) ON (n.language_code);
CREATE INDEX lsr_date FOR (n:LSR) ON (n.date_start);
CREATE INDEX language_code FOR (n:Language) ON (n.iso_code);
```

---

## 3. Ingestion Pipeline Specifications

### 3.1 Source Adapters

Each source requires a dedicated adapter implementing a common interface:

```python
# Abstract interface for all source adapters

from abc import ABC, abstractmethod
from typing import Iterator, Dict, Any
from dataclasses import dataclass

@dataclass
class RawLexicalEntry:
    """Intermediate format between source and LSR"""
    source_id: str
    source_name: str
    form: str
    language: str
    etymology: str | None
    definitions: list[str]
    attestations: list[dict]
    related_forms: list[dict]
    raw_data: dict  # Original source data for debugging

class SourceAdapter(ABC):
    
    @abstractmethod
    def connect(self) -> None:
        """Establish connection to data source"""
        pass
    
    @abstractmethod
    def fetch_batch(self, offset: int, limit: int) -> Iterator[RawLexicalEntry]:
        """Fetch a batch of entries"""
        pass
    
    @abstractmethod
    def get_total_count(self) -> int:
        """Return total available entries"""
        pass
    
    @abstractmethod
    def get_last_modified(self) -> datetime:
        """Return last modification timestamp for incremental updates"""
        pass
    
    @abstractmethod
    def supports_incremental(self) -> bool:
        """Whether source supports delta updates"""
        pass
```

### 3.2 Wiktionary Adapter

```yaml
Adapter: WiktionaryAdapter
Source: Wiktionary XML Dumps + API
URL: https://dumps.wikimedia.org/enwiktionary/
Update Frequency: Weekly dumps, real-time API for deltas

Configuration:
  dump_url: "https://dumps.wikimedia.org/enwiktionary/latest/enwiktionary-latest-pages-articles.xml.bz2"
  api_endpoint: "https://en.wiktionary.org/w/api.php"
  languages_to_process: ["all"]  # Or specific ISO codes
  batch_size: 1000
  rate_limit_ms: 100  # For API calls
  
Extraction Rules:
  form: 
    source: Page title
    normalization: lowercase, strip diacritics for matching
    
  language:
    source: ==Language== header sections
    mapping: Use ISO 639-3 lookup table
    
  etymology:
    source: ===Etymology=== section
    parsing: 
      - Extract {{inh|...}} templates → DESCENDS_FROM
      - Extract {{bor|...}} templates → BORROWED_FROM  
      - Extract {{cog|...}} templates → COGNATE_OF
      - Extract {{der|...}} templates → DERIVED (needs classification)
      
  definitions:
    source: # Numbered definition lines
    parsing: Strip wiki markup, preserve sense numbers
    
  pronunciation:
    source: ===Pronunciation=== section
    parsing: Extract IPA from {{IPA|...}} templates
    
  attestations:
    source: ===Quotations=== and {{quote-...}} templates
    parsing: Extract date, source text, quote

Output Mapping:
  RawLexicalEntry.source_id: "wikt:{language}:{form}"
  RawLexicalEntry.etymology → parsed into relationship candidates
  
Incremental Strategy:
  - Weekly: Full dump reprocessing
  - Daily: API recent changes stream for modified pages
  - Dedup: Compare source_id + content hash
```

### 3.3 CLLD/CLICS Adapter

```yaml
Adapter: CLLDAdapter
Source: Cross-Linguistic Linked Data repositories
URLs:
  - https://clics.clld.org/
  - https://wold.clld.org/
  - https://asjp.clld.org/
Update Frequency: Quarterly (static datasets)

Configuration:
  repositories:
    - name: "CLICS"
      type: "colexification"
      url: "https://github.com/clics/clics/raw/main/clics.sqlite"
    - name: "WOLD"
      type: "loanwords"
      url: "https://wold.clld.org/download"
    - name: "ASJP"
      type: "wordlists"
      url: "https://asjp.clld.org/download"
      
Extraction Rules:
  CLICS:
    - Maps concepts to forms across languages
    - Colexification = same form covers multiple concepts
    - Use for: Semantic field clustering, cognate detection
    
  WOLD:
    - World Loanword Database
    - Explicit borrowing relationships with confidence scores
    - Use for: BORROWED_FROM edges with expert confidence
    
  ASJP:
    - Automated Similarity Judgment Program
    - Phonetic transcriptions of Swadesh lists
    - Use for: Cognate candidate generation, phylogenetic distance

Output Mapping:
  - Create LSR for each form-language-concept triple
  - Create COGNATE_OF edges from ASJP similarity scores > threshold
  - Create BORROWED_FROM edges from WOLD with source confidence
  - Create HAS_MEANING edges from CLICS concept mappings
```

### 3.4 Historical Corpora Adapter

```yaml
Adapter: CorpusAdapter
Source: Dated text corpora
Targets:
  - COHA (Corpus of Historical American English)
  - EEBO (Early English Books Online)
  - Google Ngram exports
  - Project Gutenberg with metadata

Configuration:
  coha:
    access: Licensed API
    date_range: [1820, 2019]
    granularity: decade
    
  eebo:
    access: Licensed/Partnership
    date_range: [1475, 1700]
    granularity: individual texts with dates
    
  gutenberg:
    access: Open
    metadata_source: "gutenberg_metadata.json"
    date_extraction: regex patterns on publication info
    
Processing Pipeline:
  1. Tokenization (language-specific)
  2. Lemmatization (where available)
  3. Frequency counting per time slice
  4. Collocation extraction (for semantic context)
  5. Date binning

Output:
  - Frequency time series per lemma
  - Attestation records with dated quotes
  - Collocation networks per period
```

### 3.5 OCR/Manuscript Adapter

```yaml
Adapter: OCRAdapter
Source: Digitized manuscripts, historical documents
Pipeline: Image → OCR → NLP → Extraction

Configuration:
  ocr_engine: "tesseract"  # or "google_vision", "transkribus"
  
  preprocessing:
    - deskew
    - contrast_normalization
    - binarization
    
  postprocessing:
    - spell_correction (period-appropriate dictionaries)
    - abbreviation_expansion
    - unicode_normalization
    
  nlp_pipeline:
    tokenizer: "language_specific"
    lemmatizer: "stanza"  # supports historical variants
    ner: "flair"  # for date/place extraction
    
Quality Thresholds:
  min_ocr_confidence: 0.7
  min_word_confidence: 0.8
  flag_for_review_below: 0.9

Output:
  - Raw OCR text with confidence scores
  - Tokenized/lemmatized forms
  - Extracted dates and locations
  - Attestation candidates flagged for validation
```

---

## 4. Processing Pipeline Specifications

### 4.1 Entity Resolution & Deduplication

```yaml
Pipeline: EntityResolution
Purpose: Match incoming entries to existing LSRs or create new ones
Trigger: After each ingestion batch

Steps:
  1. Candidate Retrieval:
     - Query by form_normalized + language_code
     - Fuzzy match using Levenshtein distance < 2
     - Phonetic matching using Soundex/Metaphone
     
  2. Similarity Scoring:
     features:
       - form_exact_match: 0.3 weight
       - form_fuzzy_score: 0.2 weight
       - semantic_similarity: 0.3 weight (embedding cosine)
       - date_overlap: 0.1 weight
       - source_agreement: 0.1 weight
     threshold: 0.85 for auto-merge
     
  3. Resolution Actions:
     score >= 0.95: Auto-merge, update existing LSR
     score 0.85-0.95: Auto-merge with flag for review
     score 0.70-0.85: Create as candidate duplicate, queue for review
     score < 0.70: Create new LSR
     
  4. Merge Logic:
     - Combine attestations (union)
     - Update date_range (expand to encompass all)
     - Recalculate confidence (weighted by source reliability)
     - Preserve all source_database references
     - Log merge event for audit trail
```

### 4.2 Relationship Extraction

```yaml
Pipeline: RelationshipExtraction
Purpose: Create graph edges from etymology text and cross-references
Trigger: After entity resolution

Extractors:
  1. Etymology Parser:
     input: Raw etymology text
     method: 
       - Template extraction (Wiktionary patterns)
       - Dependency parsing for "from X", "derived from Y"
       - Named entity recognition for language names
     output: Candidate edges with relationship type
     
  2. Cognate Detector:
     input: LSR pairs in related languages
     method:
       - Sound correspondence rules (Grimm's Law, etc.)
       - Edit distance on phonetic forms
       - Semantic similarity threshold
     output: COGNATE_OF edges with confidence
     
  3. Borrowing Classifier:
     input: Candidate DERIVED relationships
     method:
       - Check for sound change consistency (inherited vs borrowed)
       - Geographic/temporal plausibility
       - Register analysis (borrowed words often technical/prestige)
     output: Classified as DESCENDS_FROM or BORROWED_FROM
     
  4. Semantic Shift Detector:
     input: Same-form LSRs across time periods
     method:
       - Embedding distance between periods
       - Definition clustering
       - Usage context analysis
     output: SHIFTED_TO edges with change_type classification

Confidence Calibration:
  - Source agreement bonus: +0.1 if multiple sources confirm
  - Expert validation bonus: +0.2 if human-reviewed
  - Reconstruction penalty: -0.2 if either endpoint is reconstructed
```

### 4.3 Validation Pipeline

```yaml
Pipeline: AutomatedValidation
Purpose: Quality control before data enters production graph
Trigger: Continuous on new/updated LSRs

Validators:
  1. Schema Validator:
     - Required fields present
     - Date ranges valid (start <= end)
     - Language code exists in reference table
     - Confidence scores in [0, 1]
     
  2. Consistency Validator:
     - Circular reference detection (A descends from B descends from A)
     - Temporal consistency (ancestor date < descendant date)
     - Language family consistency (edges within plausible families)
     
  3. Anomaly Detector:
     - Statistical outliers in date ranges
     - Unusually high/low confidence clusters
     - Orphan nodes (no edges after N days)
     
  4. Cross-Reference Validator:
     - Compare against Glottolog for language metadata
     - Compare against established etymologies (OED sample)
     - Flag contradictions for review

Output Actions:
  pass: Move to production
  warn: Move to production with flag
  fail: Move to validation queue
  reject: Log and discard with reason
```

---

## 5. Training Pipeline Specifications

### 5.1 Diachronic Embedding Training

```yaml
Pipeline: EmbeddingTraining
Purpose: Generate time-aware semantic vectors for all LSRs
Schedule: Weekly full retrain, daily incremental

Architecture:
  base_model: "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
  dimension: 384
  
Training Approach:
  method: "temporal_alignment"  # Hamilton et al. methodology
  
  steps:
    1. Train base embeddings on full corpus (all time periods)
    2. For each time slice T:
       - Fine-tune on texts from period T
       - Align to base space using Procrustes rotation
       - Store as separate vector set
    3. For each LSR:
       - Generate embedding using appropriate time-slice model
       - Store in vector database with temporal metadata

Time Slices:
  granularity: 50 years (adjustable per language based on data density)
  overlap: 10 years (for continuity)
  
Incremental Update:
  - New attestations trigger re-embedding for affected LSRs
  - Batch updates weekly
  - Full retrain monthly

Output:
  - Vector per LSR stored in Pinecone/Milvus
  - Metadata: lsr_id, language, time_slice, model_version
  - Drift metrics: distance from previous embedding
```

### 5.2 Classifier Training

```yaml
Pipeline: ClassifierTraining
Purpose: Train models for automated analysis tasks
Schedule: Monthly retrain with new validated data

Classifiers:
  1. Text Dating Classifier:
     architecture: Gradient boosted ensemble
     features:
       - Vocabulary frequency vector (TF-IDF weighted)
       - Character n-gram frequencies
       - Embedding centroid
       - Syntactic feature ratios (if parsed)
     labels: Date ranges from training corpus
     output: Probability distribution over time periods
     
  2. Contact Event Detector:
     architecture: Anomaly detection (Isolation Forest)
     features:
       - Vocabulary distribution per language per period
       - Borrowing rate acceleration
       - Donor language signature (phonological/semantic patterns)
     output: Anomaly scores, candidate contact events
     
  3. Borrowing Direction Classifier:
     architecture: Binary classifier (XGBoost)
     features:
       - Phonological adaptation patterns
       - Semantic domain (technical, religious, everyday)
       - Geographic proximity
       - Political/cultural relationship
     labels: Expert-annotated borrowing directions
     output: P(A→B) vs P(B→A)
     
  4. Semantic Shift Classifier:
     architecture: Multi-class (Neural net)
     features:
       - Embedding trajectory
       - Collocation change
       - Register change
     labels: Change types (metaphor, metonymy, generalization, specialization, amelioration, pejoration)
     output: Change type with confidence

Validation:
  - 80/10/10 train/val/test split
  - Stratified by language family and time period
  - Human evaluation on 100 random samples per classifier
```

### 5.3 Phylogenetic Inference

```yaml
Pipeline: PhylogeneticInference
Purpose: Reconstruct language family trees with divergence dating
Schedule: Quarterly (computationally intensive)

Method:
  tool: BEAST2 or MrBayes
  data: Cognate matrices from graph
  
Preparation:
  1. Extract cognate sets for Swadesh-100 concepts
  2. Encode as binary presence/absence matrix
  3. Add calibration points (known dates from historical record)
  
Inference Settings:
  model: "covarion"  # Allows rate variation
  clock: "relaxed_lognormal"  # Allows branch-specific rates
  prior: "birth_death"  # Tree topology prior
  chains: 4
  generations: 10_000_000
  sampling: every 1000
  burnin: 25%
  
Output:
  - Maximum clade credibility tree
  - 95% HPD intervals for divergence dates
  - Posterior probability for each clade
  - Comparison to established trees (Robinson-Foulds distance)

Automation:
  - Trigger on significant graph updates to relevant language families
  - Compare new tree to previous; flag significant topology changes
  - Generate visual diff for expert review
```

---

## 6. Serving Layer Specifications

### 6.1 REST API

```yaml
API: LinguisticStratigraphyAPI
Base URL: /api/v1
Authentication: API key (header) or OAuth2

Endpoints:

  # LSR Operations
  GET /lsr/{id}
    Returns: Full LSR record
    
  GET /lsr/search
    Params:
      form: String (exact or fuzzy)
      language: ISO code
      date_start: Integer
      date_end: Integer
      semantic_field: String
    Returns: Paginated LSR list
    
  GET /lsr/{id}/etymology
    Returns: Full ancestor chain to proto-form
    
  GET /lsr/{id}/descendants
    Params: depth (default 3)
    Returns: Descendant tree
    
  GET /lsr/{id}/cognates
    Returns: All cognate LSRs across languages
    
  # Analysis Endpoints
  POST /analyze/date-text
    Body: { text: String, language: String }
    Returns: { 
      predicted_date_range: [Int, Int],
      confidence: Float,
      diagnostic_vocabulary: [{form, date_contribution}]
    }
    
  POST /analyze/detect-anachronisms
    Body: { text: String, claimed_date: Int, language: String }
    Returns: {
      anachronisms: [{form, earliest_attestation, severity}],
      verdict: "consistent" | "suspicious" | "anachronistic"
    }
    
  GET /analyze/contact-events
    Params:
      language: ISO code
      date_start: Integer
      date_end: Integer
    Returns: [{
      donor_language: String,
      date_range: [Int, Int],
      vocabulary_count: Int,
      confidence: Float,
      sample_words: [String]
    }]
    
  GET /analyze/semantic-drift
    Params:
      form: String
      language: ISO code
    Returns: {
      trajectory: [{date, embedding_2d, definition}],
      shift_events: [{date, change_type, confidence}]
    }
    
  # Graph Queries
  POST /graph/query
    Body: Cypher or GraphQL query
    Returns: Query results
    
  GET /graph/path
    Params:
      from_lsr: UUID
      to_lsr: UUID
      max_hops: Int
    Returns: All paths between LSRs
    
  # Bulk Operations
  POST /bulk/export
    Body: { language: String, format: "json" | "csv" | "rdf" }
    Returns: Job ID (async)
    
  GET /bulk/status/{job_id}
    Returns: Job status and download URL when complete

Rate Limits:
  - Anonymous: 100 requests/hour
  - Authenticated: 10,000 requests/hour
  - Bulk exports: 10/day
```

### 6.2 GraphQL Schema

```graphql
type LSR {
  id: ID!
  form: String!
  formPhonetic: String
  language: Language!
  dateStart: Int
  dateEnd: Int
  definitions: [String!]!
  semanticFields: [SemanticField!]!
  confidence: Float!
  isReconstructed: Boolean!
  
  # Relationships
  ancestors(depth: Int = 1): [LSR!]!
  descendants(depth: Int = 1): [LSR!]!
  cognates: [LSR!]!
  loanSource: LSR
  loanTargets: [LSR!]!
  attestations: [Attestation!]!
  
  # Computed
  etymology: EtymologyChain!
  semanticTrajectory: SemanticTrajectory
}

type Language {
  isoCode: String!
  name: String!
  family: String
  branchPath: [String!]!
  isLiving: Boolean!
  
  lexicon(limit: Int = 100, offset: Int = 0): [LSR!]!
  contactEvents: [ContactEvent!]!
}

type SemanticField {
  synsetId: String!
  label: String!
  domain: String
  words: [LSR!]!
}

type Attestation {
  text: String!
  source: String!
  date: Int
  url: String
}

type EtymologyChain {
  steps: [EtymologyStep!]!
  protoForm: LSR
  depth: Int!
}

type EtymologyStep {
  from: LSR!
  to: LSR!
  relationship: RelationshipType!
  confidence: Float!
  date: Int
}

type ContactEvent {
  donorLanguage: Language!
  recipientLanguage: Language!
  dateRange: [Int!]!
  contactType: String
  vocabularyCount: Int!
  sampleWords: [LSR!]!
  confidence: Float!
}

type SemanticTrajectory {
  points: [TrajectoryPoint!]!
  shiftEvents: [ShiftEvent!]!
}

type TrajectoryPoint {
  date: Int!
  embedding2D: [Float!]!
  definition: String
  attestationCount: Int
}

type ShiftEvent {
  date: Int!
  changeType: String!
  confidence: Float!
  beforeMeaning: String
  afterMeaning: String
}

enum RelationshipType {
  DESCENDS_FROM
  BORROWED_FROM
  COGNATE_OF
  SHIFTED_TO
  MERGED_WITH
}

type Query {
  lsr(id: ID!): LSR
  searchLSR(
    form: String
    language: String
    dateStart: Int
    dateEnd: Int
    semanticField: String
    limit: Int = 20
    offset: Int = 0
  ): [LSR!]!
  
  language(isoCode: String!): Language
  languages(family: String): [Language!]!
  
  dateText(text: String!, language: String!): DateAnalysis!
  detectAnachronisms(text: String!, claimedDate: Int!, language: String!): AnachronismAnalysis!
  
  semanticFieldEvolution(synsetId: String!, language: String!): SemanticTrajectory!
  compareConcept(concept: String!, languages: [String!]!): ConceptComparison!
}

type DateAnalysis {
  predictedRange: [Int!]!
  confidence: Float!
  diagnosticVocabulary: [DiagnosticWord!]!
}

type DiagnosticWord {
  form: String!
  dateContribution: Float!
  earliestAttestation: Int
}

type AnachronismAnalysis {
  anachronisms: [Anachronism!]!
  verdict: String!
}

type Anachronism {
  form: String!
  earliestAttestation: Int!
  severity: String!
}

type ConceptComparison {
  concept: String!
  byLanguage: [LanguageConceptData!]!
}

type LanguageConceptData {
  language: Language!
  forms: [LSR!]!
  trajectory: SemanticTrajectory
}
```

---

## 7. Orchestration & Scheduling

### 7.1 Workflow Definitions (Airflow DAGs)

```python
# dag_definitions.py

from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.sensors.external_task import ExternalTaskSensor
from datetime import datetime, timedelta

default_args = {
    'owner': 'linguistic-stratigraphy',
    'depends_on_past': False,
    'email_on_failure': True,
    'email_on_retry': False,
    'retries': 3,
    'retry_delay': timedelta(minutes=5),
}

# Daily ingestion DAG
with DAG(
    'daily_ingestion',
    default_args=default_args,
    description='Daily incremental data ingestion',
    schedule_interval='0 2 * * *',  # 2 AM daily
    start_date=datetime(2024, 1, 1),
    catchup=False,
    tags=['ingestion'],
) as daily_dag:
    
    ingest_wiktionary_delta = PythonOperator(
        task_id='ingest_wiktionary_delta',
        python_callable=wiktionary_adapter.fetch_recent_changes,
        op_kwargs={'hours_back': 26},  # Overlap for safety
    )
    
    resolve_entities = PythonOperator(
        task_id='resolve_entities',
        python_callable=entity_resolution.process_batch,
    )
    
    extract_relationships = PythonOperator(
        task_id='extract_relationships',
        python_callable=relationship_extractor.process_new_lsrs,
    )
    
    validate = PythonOperator(
        task_id='validate',
        python_callable=validator.run_all,
    )
    
    update_embeddings = PythonOperator(
        task_id='update_embeddings',
        python_callable=embedding_pipeline.update_modified,
    )
    
    ingest_wiktionary_delta >> resolve_entities >> extract_relationships >> validate >> update_embeddings


# Weekly full processing DAG
with DAG(
    'weekly_full_process',
    default_args=default_args,
    description='Weekly full dump processing and retraining',
    schedule_interval='0 0 * * 0',  # Sunday midnight
    start_date=datetime(2024, 1, 1),
    catchup=False,
    tags=['ingestion', 'training'],
) as weekly_dag:
    
    download_dumps = PythonOperator(
        task_id='download_dumps',
        python_callable=dump_downloader.fetch_all,
    )
    
    process_wiktionary_full = PythonOperator(
        task_id='process_wiktionary_full',
        python_callable=wiktionary_adapter.process_full_dump,
        execution_timeout=timedelta(hours=12),
    )
    
    process_clld = PythonOperator(
        task_id='process_clld',
        python_callable=clld_adapter.sync_all,
    )
    
    rebuild_embeddings = PythonOperator(
        task_id='rebuild_embeddings',
        python_callable=embedding_pipeline.full_retrain,
        execution_timeout=timedelta(hours=8),
    )
    
    retrain_classifiers = PythonOperator(
        task_id='retrain_classifiers',
        python_callable=classifier_training.train_all,
        execution_timeout=timedelta(hours=4),
    )
    
    generate_reports = PythonOperator(
        task_id='generate_reports',
        python_callable=reporting.weekly_summary,
    )
    
    download_dumps >> [process_wiktionary_full, process_clld]
    [process_wiktionary_full, process_clld] >> rebuild_embeddings >> retrain_classifiers >> generate_reports


# Monthly phylogenetic DAG
with DAG(
    'monthly_phylogenetics',
    default_args=default_args,
    description='Monthly phylogenetic tree reconstruction',
    schedule_interval='0 0 1 * *',  # 1st of month
    start_date=datetime(2024, 1, 1),
    catchup=False,
    tags=['analysis'],
) as monthly_dag:
    
    extract_cognate_matrices = PythonOperator(
        task_id='extract_cognate_matrices',
        python_callable=phylogenetics.prepare_matrices,
    )
    
    run_beast = PythonOperator(
        task_id='run_beast',
        python_callable=phylogenetics.run_inference,
        execution_timeout=timedelta(hours=48),
    )
    
    compare_trees = PythonOperator(
        task_id='compare_trees',
        python_callable=phylogenetics.compare_to_baseline,
    )
    
    extract_cognate_matrices >> run_beast >> compare_trees
```

### 7.2 Event-Driven Triggers

```yaml
Event Triggers:

  on_new_lsr:
    source: Message queue (LSR creation events)
    actions:
      - Queue for embedding generation
      - Queue for relationship extraction
      - Update search index
      
  on_lsr_update:
    source: Message queue (LSR modification events)
    actions:
      - Recalculate confidence scores
      - Re-embed if semantic content changed
      - Propagate confidence changes to dependents
      
  on_validation_complete:
    source: Validation pipeline output
    actions:
      - Move to production if passed
      - Notify if flagged
      - Update metrics
      
  on_expert_validation:
    source: Human validation interface
    actions:
      - Update confidence scores
      - Mark as validated
      - Add to classifier training set
      
  on_api_feedback:
    source: User feedback on API responses
    actions:
      - Log for analysis
      - Queue for review if negative
      - Update quality metrics
```

---

## 8. Monitoring & Observability

### 8.1 Metrics

```yaml
Metrics Collection: Prometheus + Grafana

System Metrics:
  - ingestion_records_processed_total{source, status}
  - ingestion_duration_seconds{source}
  - entity_resolution_matches{resolution_type}
  - validation_results{validator, result}
  - embedding_generation_duration_seconds
  - api_requests_total{endpoint, status}
  - api_latency_seconds{endpoint}
  
Data Quality Metrics:
  - lsr_count_total{language, period, confidence_bucket}
  - edge_count_total{relationship_type, confidence_bucket}
  - orphan_node_count
  - validation_queue_size
  - average_confidence_score{language}
  
Model Metrics:
  - classifier_accuracy{classifier}
  - embedding_coverage_ratio
  - phylogeny_rf_distance{family}
  
Business Metrics:
  - languages_covered
  - time_periods_covered
  - attestation_density{language, period}
  - user_queries_total{query_type}
```

### 8.2 Alerting Rules

```yaml
Alerts:

  - name: IngestionFailure
    condition: ingestion_records_processed_total increase = 0 for 24h
    severity: critical
    action: Page on-call
    
  - name: HighValidationFailureRate
    condition: validation_results{result="fail"} / validation_results > 0.2
    severity: warning
    action: Slack notification
    
  - name: EmbeddingBacklogGrowing
    condition: embedding_queue_size > 100000 for 1h
    severity: warning
    action: Slack notification
    
  - name: APIHighLatency
    condition: api_latency_seconds{quantile="0.99"} > 5
    severity: warning
    action: Slack notification
    
  - name: DatabaseConnectionFailure
    condition: database_connections_active = 0
    severity: critical
    action: Page on-call
    
  - name: ConfidenceDrift
    condition: |
      abs(average_confidence_score - average_confidence_score offset 7d) > 0.1
    severity: warning
    action: Slack notification (possible data quality issue)
```

### 8.3 Logging

```yaml
Logging Configuration:

  format: JSON structured logging
  
  fields:
    - timestamp
    - level
    - service
    - trace_id
    - span_id
    - message
    - context (varies by event type)
    
  levels:
    DEBUG: Development only
    INFO: Normal operations
    WARNING: Recoverable issues
    ERROR: Failures requiring attention
    CRITICAL: System-level failures
    
  retention:
    hot: 7 days (Elasticsearch)
    warm: 30 days (compressed)
    cold: 1 year (S3/archival)
    
  key_events_to_log:
    - LSR creation/update/delete
    - Relationship creation
    - Validation decisions
    - API queries (sampled 10%)
    - Pipeline stage transitions
    - Errors and exceptions
```

---

## 9. Deployment Configuration

### 9.1 Infrastructure (Docker Compose - Development)

```yaml
# docker-compose.yml
version: '3.8'

services:
  neo4j:
    image: neo4j:5.9
    environment:
      - NEO4J_AUTH=neo4j/password
      - NEO4J_PLUGINS=["apoc", "graph-data-science"]
    ports:
      - "7474:7474"
      - "7687:7687"
    volumes:
      - neo4j_data:/data
      
  postgres:
    image: postgres:15
    environment:
      - POSTGRES_DB=linguistic_stratigraphy
      - POSTGRES_USER=ls_user
      - POSTGRES_PASSWORD=password
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data
      
  elasticsearch:
    image: elasticsearch:8.9.0
    environment:
      - discovery.type=single-node
      - xpack.security.enabled=false
    ports:
      - "9200:9200"
    volumes:
      - es_data:/usr/share/elasticsearch/data
      
  redis:
    image: redis:7
    ports:
      - "6379:6379"
      
  milvus:
    image: milvusdb/milvus:v2.3.0
    ports:
      - "19530:19530"
    volumes:
      - milvus_data:/var/lib/milvus
      
  airflow:
    image: apache/airflow:2.7.0
    environment:
      - AIRFLOW__CORE__EXECUTOR=LocalExecutor
      - AIRFLOW__DATABASE__SQL_ALCHEMY_CONN=postgresql+psycopg2://ls_user:password@postgres/airflow
    ports:
      - "8080:8080"
    volumes:
      - ./dags:/opt/airflow/dags
      - ./plugins:/opt/airflow/plugins
      
  api:
    build: ./api
    ports:
      - "8000:8000"
    environment:
      - NEO4J_URI=bolt://neo4j:7687
      - POSTGRES_URI=postgresql://ls_user:password@postgres/linguistic_stratigraphy
      - ELASTICSEARCH_URI=http://elasticsearch:9200
      - REDIS_URI=redis://redis:6379
      - MILVUS_URI=milvus:19530
    depends_on:
      - neo4j
      - postgres
      - elasticsearch
      - redis
      - milvus

volumes:
  neo4j_data:
  postgres_data:
  es_data:
  milvus_data:
```

### 9.2 Infrastructure (Kubernetes - Production)

```yaml
# High-level Kubernetes architecture

Namespaces:
  - ls-data: Database services
  - ls-compute: Processing pipelines
  - ls-serving: API and web services
  - ls-monitoring: Observability stack

Key Deployments:

  ls-data/neo4j:
    type: StatefulSet
    replicas: 3 (cluster mode)
    storage: 500Gi SSD per replica
    resources:
      requests: { cpu: 4, memory: 32Gi }
      limits: { cpu: 8, memory: 64Gi }
      
  ls-data/postgres:
    type: StatefulSet
    replicas: 2 (primary + replica)
    storage: 100Gi SSD
    
  ls-compute/embedding-workers:
    type: Deployment
    replicas: 2-10 (HPA)
    resources:
      requests: { cpu: 4, memory: 16Gi, nvidia.com/gpu: 1 }
      
  ls-compute/ingestion-workers:
    type: Deployment
    replicas: 3-20 (HPA based on queue depth)
    
  ls-serving/api:
    type: Deployment
    replicas: 3-10 (HPA based on CPU)
    resources:
      requests: { cpu: 2, memory: 4Gi }

Autoscaling:
  - API: Scale on CPU > 70%
  - Ingestion workers: Scale on queue depth > 10000
  - Embedding workers: Scale on queue depth > 1000

Storage Classes:
  - fast: SSD for databases
  - standard: HDD for bulk storage
  - archive: S3-compatible for backups
```

---

## 10. Security Considerations

```yaml
Security Measures:

  Authentication:
    - API keys for programmatic access
    - OAuth2 for interactive users
    - Service accounts for internal communication
    
  Authorization:
    - Role-based access control (RBAC)
    - Roles: reader, contributor, validator, admin
    - Resource-level permissions for sensitive data
    
  Data Protection:
    - Encryption at rest (AES-256)
    - Encryption in transit (TLS 1.3)
    - PII handling: None expected, but audit for any
    
  Network Security:
    - Internal services not exposed externally
    - API gateway with rate limiting
    - WAF for web endpoints
    
  Audit:
    - All data modifications logged with user/service identity
    - API access logs retained 1 year
    - Validation decisions traceable
    
  Backup:
    - Daily database snapshots
    - Point-in-time recovery enabled
    - Cross-region replication for disaster recovery
    - Monthly backup restoration tests
```

---

## 11. Cost Estimation (Cloud - Monthly)

```yaml
Estimated Monthly Costs (AWS, production scale):

Compute:
  - EKS cluster (control plane): $73
  - API nodes (3x m5.large): $210
  - Ingestion workers (5x m5.xlarge avg): $350
  - Embedding workers (2x p3.2xlarge avg): $2,200
  - Subtotal: ~$2,833

Storage:
  - Neo4j EBS (1.5TB SSD): $150
  - PostgreSQL RDS (200GB): $50
  - Elasticsearch EBS (500GB): $50
  - Milvus EBS (200GB): $20
  - S3 backups (1TB): $23
  - Subtotal: ~$293

Networking:
  - Load balancer: $20
  - Data transfer (500GB): $45
  - Subtotal: ~$65

Managed Services (optional):
  - If using managed Neo4j Aura: +$1,000
  - If using Pinecone instead of Milvus: +$70
  
Total Estimated: $3,200 - $4,300/month

Cost Optimization Options:
  - Spot instances for batch processing: -40% on compute
  - Reserved instances (1yr): -30% on steady-state
  - Self-managed databases: -$1,000 but +ops burden
```

---

## 12. Implementation Roadmap

```yaml
Phase 1 - Foundation (Weeks 1-4):
  Week 1:
    - Set up development environment (Docker Compose)
    - Initialize databases with schemas
    - Deploy basic API skeleton
  Week 2:
    - Implement Wiktionary adapter
    - Build entity resolution pipeline
    - Set up Airflow with basic DAGs
  Week 3:
    - Implement relationship extraction
    - Build validation pipeline
    - First data load (English only)
  Week 4:
    - Basic API endpoints (search, retrieve)
    - Integration tests
    - Documentation

Phase 2 - Core Analysis (Weeks 5-8):
  Week 5:
    - Embedding pipeline setup
    - Train initial diachronic embeddings (English)
  Week 6:
    - Text dating classifier
    - Anachronism detection
  Week 7:
    - CLLD/CLICS adapter
    - Expand to Romance languages
  Week 8:
    - Cognate detection
    - Contact event detection
    - API endpoints for analysis features

Phase 3 - Scale & Validation (Weeks 9-12):
  Week 9:
    - Phylogenetic inference pipeline
    - Validation against known etymologies
  Week 10:
    - Visualization endpoints
    - GraphQL implementation
  Week 11:
    - Performance optimization
    - Kubernetes deployment
  Week 12:
    - Documentation finalization
    - Pilot study execution
    - Public release preparation

Milestones:
  M1 (Week 4): First 100K LSRs loaded, basic search working
  M2 (Week 8): Text dating achieves 80% accuracy on held-out set
  M3 (Week 12): Romance pilot complete, public beta launch
```

---

## Appendix A: Directory Structure

```
linguistic-stratigraphy/
├── README.md
├── docker-compose.yml
├── pyproject.toml
├── 
├── src/
│   ├── adapters/
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── wiktionary.py
│   │   ├── clld.py
│   │   ├── corpus.py
│   │   └── ocr.py
│   │
│   ├── pipelines/
│   │   ├── __init__.py
│   │   ├── entity_resolution.py
│   │   ├── relationship_extraction.py
│   │   ├── validation.py
│   │   └── embedding.py
│   │
│   ├── models/
│   │   ├── __init__.py
│   │   ├── lsr.py
│   │   ├── language.py
│   │   └── relationships.py
│   │
│   ├── training/
│   │   ├── __init__.py
│   │   ├── embeddings.py
│   │   ├── classifiers.py
│   │   └── phylogenetics.py
│   │
│   ├── analysis/
│   │   ├── __init__.py
│   │   ├── dating.py
│   │   ├── contact_detection.py
│   │   └── semantic_drift.py
│   │
│   ├── api/
│   │   ├── __init__.py
│   │   ├── main.py
│   │   ├── routes/
│   │   │   ├── lsr.py
│   │   │   ├── analysis.py
│   │   │   └── graph.py
│   │   └── graphql/
│   │       ├── schema.py
│   │       └── resolvers.py
│   │
│   └── utils/
│       ├── __init__.py
│       ├── db.py
│       ├── embeddings.py
│       └── phonetics.py
│
├── dags/
│   ├── daily_ingestion.py
│   ├── weekly_processing.py
│   └── monthly_phylogenetics.py
│
├── tests/
│   ├── unit/
│   ├── integration/
│   └── fixtures/
│
├── scripts/
│   ├── setup_databases.sh
│   ├── load_initial_data.py
│   └── benchmark.py
│
├── k8s/
│   ├── base/
│   ├── overlays/
│   │   ├── dev/
│   │   └── prod/
│   └── kustomization.yaml
│
└── docs/
    ├── api.md
    ├── architecture.md
    ├── contributing.md
    └── data_model.md
```

---

## Appendix B: Key Dependencies

```toml
# pyproject.toml [dependencies]

[project]
name = "linguistic-stratigraphy"
version = "0.1.0"
requires-python = ">=3.11"

dependencies = [
    # Core
    "pydantic>=2.0",
    "httpx>=0.24",
    "aiohttp>=3.8",
    
    # Databases
    "neo4j>=5.0",
    "asyncpg>=0.28",
    "elasticsearch>=8.0",
    "redis>=4.0",
    "pymilvus>=2.3",
    
    # NLP
    "spacy>=3.6",
    "stanza>=1.5",
    "sentence-transformers>=2.2",
    "transformers>=4.30",
    
    # ML
    "scikit-learn>=1.3",
    "xgboost>=1.7",
    "torch>=2.0",
    
    # API
    "fastapi>=0.100",
    "uvicorn>=0.23",
    "strawberry-graphql>=0.200",
    
    # Orchestration
    "apache-airflow>=2.7",
    
    # Utils
    "pywikibot>=8.0",
    "lxml>=4.9",
    "tqdm>=4.65",
    "python-Levenshtein>=0.21",
]

[project.optional-dependencies]
dev = [
    "pytest>=7.4",
    "pytest-asyncio>=0.21",
    "black>=23.0",
    "ruff>=0.0.280",
    "mypy>=1.4",
]
```

---

*End of Specification*
