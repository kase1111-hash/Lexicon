# Sample corpus

Four short, dated excerpts of public-domain English texts, so that
`make ingest-corpus` (or `lexicon ingest --source corpus --corpus-dir data/corpus`)
works out of the box. Every distinct word of three or more letters becomes a
dated attestation: a word's first attestation is the earliest document it
appears in.

| File | Text | Date |
|------|------|------|
| `chaucer_general_prologue.txt` | Chaucer, *The Canterbury Tales*, General Prologue, lines 1-18 (Middle English, `enm`) | c. 1390 |
| `kjv_genesis.txt` | King James Bible, Genesis 1:1-5 (1769 standard spelling) | 1611 |
| `austen_pride_and_prejudice.txt` | Austen, *Pride and Prejudice*, opening | 1813 |
| `wells_war_of_the_worlds.txt` | Wells, *The War of the Worlds*, opening | 1898 |

All four texts are in the public domain.

## Format

Each `name.txt` holds a document; the optional `name.json` sidecar gives its
metadata (all keys optional): `title`, `date`, `date_confidence`, `language`,
`language_code` (ISO 639-3), `source`, `url`. A `language` name without a
`language_code` gets that language's code (or `und`, with a warning, for a
name Lexicon does not know); a document with neither is in the language of
`--language`.

- `date` is a year from -10000 to 2100, negative for BCE. A document with a
  date outside that range, or with none, is treated as undated (with a
  warning for a bad date).
- `date_confidence` is a number from 0 to 1 that says how sure the date is
  (the Chaucer excerpt has 0.8). A word dated by this document gets it; a
  value outside 0-1 is ignored with a warning.

Add your own dated documents the same way, or point `--corpus-dir` at another
directory. Words are split as the analyses split them, so texts in any
script work.

Four excerpts are only a demonstration: dates derived from them say when a word
appears in this corpus, not when it entered the language. Don't load them into
a graph you use for dating: *truth* would look first attested in 1813 and
*watched* in 1898, and texts using them would be flagged as anachronistic.
Analyses label such dates `earliest in corpus: <title>`.
