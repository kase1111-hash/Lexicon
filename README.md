Computational Linguistic Stratigraphy: A Temporal-Semantic Model for Cross-Language Lexical Evolution
Version 2.0 — Revised Framework

Abstract
We propose a methodology for constructing a unified, version-controlled representation of human lexical evolution across all documented languages. By treating languages as branches in a directed acyclic graph and individual semantic-phonological changes as discrete events, we aim to enable temporal dating of texts through vocabulary analysis, detection of language contact events, reconstruction of semantic drift patterns, and identification of forgeries or anachronisms. This framework synthesizes comparative historical linguistics, distributional semantics, and computational phylogenetics into a queryable infrastructure for diachronic lexical research.

1. Problem Statement
Historical linguistics has established robust methods for tracing language relationships and etymological descent. However, these methods remain:

Labor-intensive — requiring expert analysis of individual cognate sets
Fragmentary — siloed by language family, time period, or institution
Qualitative — producing relationship claims without confidence intervals
Static — captured in publications rather than living, queryable databases

No unified computational infrastructure exists for querying questions like: "When did the concept of 'privacy' emerge across languages, and through what contact pathways did it spread?"

2. Research Questions
RQ1 (Temporal): Can vocabulary usage patterns date a text's composition more precisely than traditional paleographic or stylistic methods?
RQ2 (Relational): Can automated analysis recover known language family relationships and identify previously unrecognized contact events?
RQ3 (Semantic): Can we map the emergence and drift of abstract concepts across languages to identify cultural transmission pathways?
RQ4 (Forensic): Can anachronistic vocabulary usage reliably identify forged, misdated, or interpolated texts?

3. Theoretical Foundations
3.1 Linguistic Grounding
This framework synthesizes three theoretical traditions:
Saussurean Diachrony — Language as a system evolving through time, where synchronic states can be compared across temporal cross-sections. We operationalize this through discrete Lexical State Records representing snapshots of form-meaning pairings.
Distributional Semantics — "You shall know a word by the company it keeps" (Firth, 1957). Meaning is derivable from usage context, enabling computational extraction of semantic content from corpora without manual annotation.
Evolutionary Linguistics — Following Croft's (2000) Evolutionary Model of Language Change, we treat linguistic change as a population-level phenomenon where variants compete, propagate, and undergo selection. Lexical innovations are "mutations"; borrowings are "horizontal gene transfer."
3.2 Model of Change: Punctuated Gradualism
We adopt a hybrid model:

Gradual drift — Semantic fields shift incrementally through metonymy, metaphor, and generalization/specialization
Punctuated events — Contact situations (conquest, trade, technological transmission) produce rapid, identifiable vocabulary influx

The model must capture both. Gradual drift appears as continuous vector movement in semantic space; punctuated events appear as clustering anomalies in the borrowing graph.
3.3 The "Commit" Metaphor Formalized
A lexical "commit" is an observable state change in the form-meaning-usage bundle:
Change TypeLinguistic TermCommit AnalogySound changePhonological shiftRefactorMeaning shiftSemantic driftFeature changeNew word (internal)NeologismNew fileNew word (external)LoanwordDependency importWord deathObsolescenceDeprecationRegister changeSociolinguistic shiftAccess modifier change

4. Background & Prior Work
4.1 Comparative Historical Linguistics

Grimm's Law, Verner's Law — systematic sound correspondences enabling reconstruction
Swadesh lists — core vocabulary for measuring divergence time (glottochronology)
Established family trees: Indo-European, Sino-Tibetan, Austronesian, Afroasiatic, Niger-Congo

4.2 Corpus Linguistics & Diachronic Semantics

Google Ngram Viewer — word frequency over 500 years (Michel et al., 2011)
COHA/COCA — structured historical corpora of American English
Hamilton et al. (2016) — Diachronic word embeddings reveal statistical laws of semantic change; demonstrated that semantic change rate correlates with word frequency

4.3 Computational Phylogenetics

Gray & Atkinson (2003) — Bayesian phylogenetic methods date Indo-European origin to Anatolia ~8,000 BP
Bouckaert et al. (2012) — Phylogeographic analysis of IE expansion
List et al. (2018) — CLICS database of cross-linguistic colexifications

4.4 Distributional Semantics

Lau et al. (2012) — Historical corpus word sense induction
Kutuzov et al. (2018) — Survey of diachronic word embedding methods
Tahmasebi et al. (2021) — Survey on computational approaches to semantic change

4.5 Identified Gaps

No cross-linguistic unified schema bridging language families
Semantic dimension underrepresented relative to phonological
No queryable infrastructure for multi-language temporal analysis
Existing tools optimize for single-language, not comparative work


5. Proposed Methodology
5.1 Data Model
The atomic unit is a Lexical State Record (LSR):
LSR {
  id:             UUID
  form:           String (orthographic) + IPA (phonetic)
  language:       ISO 639-3 + temporal tag (e.g., "eng-1400")
  date_range:     [Int, Int] — attested or estimated
  date_confidence: Float [0,1] — certainty of dating
  
  semantic_vector: Float[300] — embedding representation
  semantic_field:  String[] — categorical labels (WordNet synsets)
  definition:      String — human-readable gloss
  
  register:       Enum {formal, colloquial, technical, sacred, literary}
  frequency:      Float — normalized usage frequency in period
  
  attestations:   Source[] — textual evidence
  
  ancestors:      LSR[] — inheritance pointers
  cognates:       LSR[] — sibling language parallels
  loan_source:    LSR | null — if borrowed, donor record
  descendants:    LSR[] — forward pointers
  
  reconstruction: Boolean — is this a starred proto-form?
  confidence:     Float [0,1] — overall certainty weight
}
5.2 Graph Structure

Nodes — Lexical State Records
Edges — Typed, weighted relationships:

DESCENDS_FROM (vertical inheritance)
BORROWED_FROM (horizontal transfer)
COGNATE_OF (shared ancestry)
SHIFTED_TO (same language, semantic change)
MERGED_WITH (convergence events)


Supernodes — Language-state containers (e.g., "Middle English 1200-1400")
Hyperedges — Contact events linking multiple languages simultaneously

5.3 Quantitative Methods
Temporal Fingerprinting
For a given text T, extract vocabulary V. For each word w ∈ V, retrieve date distribution D(w) from the graph. Compute composite date probability:
P(date | T) = ∏ P(date | w) for w ∈ V
Refine using Bayesian updating with priors from paleographic/stylistic evidence.
Semantic Drift Measurement
Using diachronic embeddings (Hamilton et al., 2016), compute cosine distance between time-sliced vectors:
drift(w, t1, t2) = 1 - cos(vec(w, t1), vec(w, t2))
Aggregate drift rates reveal "stable core" vs. "volatile periphery" vocabulary.
Contact Detection
Model expected vocabulary distribution for language L at time T. Identify statistically anomalous clusters:
anomaly_score(w) = -log P(w | L, T, no_contact)
Cluster high-anomaly words by donor language to identify contact events.
Phylogenetic Inference
Apply Bayesian phylogenetic methods (MrBayes, BEAST) to cognate matrices. Output: posterior distribution over tree topologies with branch-length estimates (divergence times).
5.4 Uncertainty Quantification
All outputs carry confidence intervals:

Attested data — High confidence (0.9+), bounded by attestation date
Interpolated data — Medium confidence (0.5-0.9), inferred from surrounding attestations
Reconstructed data — Variable confidence (0.2-0.8), derived from comparative method
Speculative data — Low confidence (<0.5), flagged for review

Confidence propagates through graph edges: descendants of uncertain nodes inherit uncertainty.

6. System Architecture & Scalability
6.1 Storage Layer
ComponentTechnologyRationaleGraph storeNeo4j or ArangoDBNative graph queries, scales to billions of edgesVector storePinecone / MilvusSemantic similarity search at scaleDocument storeElasticsearchFull-text search over attestationsRelational metadataPostgreSQLStructured queries, ACID compliance
6.2 Embedding Pipeline

Base model: multilingual-BERT or XLM-RoBERTa for cross-linguistic representations
Diachronic adaptation: Train time-slice-specific layers following Hamilton et al. methodology
Fallback: fastText for low-resource languages with limited BERT coverage

6.3 Scalability Analysis
Estimated graph size at full scale:

~7,000 living languages × ~50,000 lexemes average = 350M base nodes
Historical depth (10 time slices average) = 3.5B LSR nodes
Edge density ~5 edges/node = 17.5B edges

This is large but tractable with modern graph databases. Partitioning strategy:

Shard by language family (primary)
Sub-shard by time period (secondary)
Cross-shard queries for contact events (federated)

6.4 Inference Optimization

Precompute common queries (date fingerprints for frequent vocabulary)
Cache phylogenetic subtrees
Approximate nearest-neighbor for semantic similarity (HNSW algorithm)


7. Data Sources & Ingestion
7.1 Structured Sources (Phase 1)
SourceTypeCoverageAccessWiktionaryEtymological6M+ entries, multilingualOpen, APICLLD/CLICSComparative wordlists3,000+ languagesOpenGlottologLanguage metadata8,000+ languagesOpenIELexIndo-European lexicon~200 IE languagesOpenSTEDTSino-Tibetan400+ languagesOpenAustronesian Basic VocabularyAustronesian1,000+ languagesOpen
7.2 Dated Corpora (Phase 2)
SourceCoverageDate RangeCOHAAmerican English1820-2010EEBOEarly English1475-1700Google NgramEN, FR, DE, ES, IT, RU, ZH, HE1500-2019Project GutenbergWestern languagesVariableInternet ArchiveGlobalVariable
7.3 Mass Ingestion (Phase 3)

OCR'd manuscripts via Transkribus / Google Vision
LLM-assisted entity extraction and normalization
Crowdsourced validation layer

7.4 Licensing Constraints

OED: Proprietary, requires institutional license or partnership
Some historical corpora restricted to academic use
Strategy: Build open-source core, document pathways for licensed enhancement


8. Validation & Evaluation
8.1 Temporal Dating Validation
Ground Truth: Texts with independently verified composition dates (dated manuscripts, letters with postmarks, inscriptions with regnal years).
Protocol:

Hold out 20% of dated texts from training
Predict date range from vocabulary alone
Compare predicted vs. actual date
Benchmark against: stylometry, paleography, human expert

Metrics:

Mean absolute error (years)
Percentage within 50-year window
Percentage within 100-year window

Target: Outperform stylometry alone on held-out set.
8.2 Contact Event Detection Validation
Ground Truth: Known historical contact events with documented vocabulary transfer:

Norman Conquest (1066) — French → English
Arabic → Spanish (711-1492)
Sanskrit → Southeast Asian languages (1st millennium CE)
English → Japanese (Meiji era, 1868+)

Protocol:

Mask contact event knowledge from model
Run contact detection algorithm
Evaluate: Does model recover known events? False positive rate?

Metrics:

Precision/recall on known contact events
Date accuracy of detected events
Donor language identification accuracy

8.3 Semantic Drift Validation
Ground Truth: Well-documented semantic shifts:

"nice" (foolish → pleasant)
"awful" (awe-inspiring → terrible)
"computer" (human → machine)
"gay" (happy → homosexual)

Protocol:

Compute semantic trajectory from model
Compare against scholarly consensus on shift timing and direction

8.4 Phylogenetic Reconstruction Validation
Ground Truth: Established language family trees (IE, Austronesian) with scholarly consensus.
Protocol:

Reconstruct tree from lexical data alone
Compare topology to consensus tree
Compare divergence time estimates to archaeological/genetic dating

Metrics:

Robinson-Foulds distance (tree topology similarity)
Branch length correlation with independent dating


9. Pilot Study Design
9.1 Scope: Romance Lexical Divergence
Rationale: Well-documented family, abundant dated texts, established scholarship for validation.
Languages: Latin (Classical, Vulgar), Spanish, Portuguese, French, Italian, Romanian, Catalan
Time span: 200 BCE — 2000 CE
Target outputs:

Semantic drift map for 500 core concepts
Contact event detection: Arabic→Spanish, Germanic→French
Text dating tool for medieval Romance texts

9.2 Data Sources for Pilot

Latin: Perseus Digital Library, Packard Humanities Institute
Medieval Romance: CORDE (Spanish), BFM (French), OVI (Italian)
Modern: Europarl parallel corpus, Wikipedia dumps

9.3 Success Criteria

Recover known Romance family topology
Date held-out medieval texts within 100-year window at 80% accuracy
Detect Arabisms in Spanish with >90% precision

9.4 Timeline
PhaseDurationDeliverableSchema finalization2 weeksLSR spec, graph schemaData ingestion (structured)4 weeksLatin + 3 Romance languages loadedEmbedding training2 weeksDiachronic vectors for pilot languagesValidation pipeline2 weeksAutomated evaluation harnessAnalysis & writeup2 weeksPilot study report
Total: 12 weeks to proof-of-concept

10. Ethical Considerations
10.1 Corpus Bias
Historical corpora systematically overrepresent:

Elite registers (literary, administrative, religious)
Colonial languages and perspectives
Male authors
Written traditions (excluding oral cultures entirely)

Mitigation:

Explicitly document coverage gaps
Weight confidence scores by corpus representativeness
Prioritize digitization partnerships with underrepresented language communities
Flag "data desert" regions in visualizations

10.2 Colonial Language Dynamics
Language contact was often coercive. The model must not:

Frame colonial language imposition as neutral "contact"
Treat language death as mere "deprecation"
Obscure power dynamics in borrowing relationships

Mitigation:

Include metadata on contact type (trade, conquest, missionary, etc.)
Visualize asymmetric borrowing relationships
Consult with indigenous language communities on representation

10.3 Reconstruction Uncertainty
Proto-language reconstructions (PIE, Proto-Bantu, etc.) are scholarly hypotheses, not attested data. Risk: computational systems may present reconstructions with false certainty.
Mitigation:

Visually distinguish attested vs. reconstructed data (e.g., starred forms, uncertainty halos)
Propagate uncertainty through all downstream inferences
Never present reconstruction as "fact" without confidence intervals

10.4 Potential Misuse

Forgery: Dating tools could help forgers craft more convincing fakes
Linguistic nationalism: Data could be weaponized for ethnic/political claims

Mitigation:

Publish detection methods alongside dating methods
Academic use licensing for sensitive tools
Explicit disclaimers on political interpretation of linguistic data


11. Visualization & Interface
11.1 Temporal-Semantic Explorer
Interactive visualization allowing users to:

Select a concept (e.g., "freedom," "money," "love")
View semantic trajectory through time as vector path in reduced-dimension space
Compare trajectories across languages
Identify convergence/divergence points

Technical approach: UMAP/t-SNE projection of diachronic embeddings, animated timeline scrubber.
11.2 Language Contact Network
Graph visualization showing:

Languages as nodes (sized by vocabulary count)
Borrowing relationships as directed edges (weighted by volume)
Temporal filtering (show contact network at specific dates)
Cluster highlighting (trade networks, colonial systems, religious transmission)

Technical approach: Force-directed graph layout, temporal animation, community detection coloring.
11.3 Text Dating Interface
User inputs text → system outputs:

Predicted date range with confidence interval
Highlighted "dating-diagnostic" vocabulary
Flagged anachronisms or anomalies
Comparison to known dated texts from same period

11.4 Cognate Explorer
Select a word → view:

Full cognate tree across language family
Sound correspondence patterns
Semantic drift in each branch
Reconstructed proto-form with confidence

11.5 Example Use Case: Tracing "Freedom"
Latin: libertas (political status, civic freedom)
  ↓
Old French: liberté (feudal context, privileges)
  ↓ 
Middle English: libertee (borrowed from French post-1066)
  ↓
Early Modern English: liberty (religious freedom, 1600s)
  ↓
Modern English: liberty / freedom (freedom is Germanic competitor)

Germanic parallel track:
Proto-Germanic: *frijaz (beloved, not in bondage)
  ↓
Old English: frēodōm (state of being free)
  ↓
Modern English: freedom

Visualization shows: Two competing words, different etymologies, 
semantic convergence in Modern English, subtle register differences remain.

12. Expected Contributions

Open schema — A reusable, extensible data model for diachronic lexical data
Unified graph — First cross-linguistic lexical knowledge graph spanning multiple language families
Temporal classifier — Text dating tool with quantified uncertainty, benchmarked against traditional methods
Contact event detector — Automated identification of borrowing patterns with historical validation
Semantic drift visualizer — Explorable maps of meaning change across languages and centuries
Reproducible methodology — Open-source pipeline enabling extension to new languages


13. Challenges & Limitations

Pre-literate reconstruction — High uncertainty before written records; proto-forms are hypotheses
Semantic annotation at scale — Meaning is harder to formalize than form; embeddings are proxies
Corpus bias — Historical texts skew toward elite, religious, administrative registers
Polysemy — Same form, multiple meanings, different trajectories; requires sense disambiguation
Data licensing — OED and other key sources are proprietary; full system requires partnerships
Computational cost — Full-scale graph requires significant infrastructure investment


14. Future Directions

Phonological integration — Add sound change rules to predict unattested cognates
Syntactic extension — Track grammatical constructions, not just lexemes
Speech community modeling — Model sociolinguistic variation within languages
Real-time monitoring — Extend to contemporary language change (social media, neologism tracking)
Endangered language priority — Partner with documentation projects to preserve lexical knowledge before language death
