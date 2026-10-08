# 10_Ingestion — keeping up with the archive

Phase 9, with the 9.1 repair layer and the 9.2/9.3 stale-RAG fixes. The incremental
ingestion layer for Cutler Research Brain. What it produces is browsable in the Documents tab
added in Phase 10A — see the end of this file.

> **AI drafts. Humans verify. Cutler decides.**

Phases 1 through 8 assumed the archive stood still. It does not. The V drive gains files every
day, and some of them replace files that were already read. This folder is the answer to two
questions: **what changed, and what does that mean for everything downstream** — and, just as
importantly, **what did not change, so that we do not pay to read it again**.

---

## The two promises

**1. The source archive is read-only, always.**

`V:\Companies\A Current Month\2026` is opened for three things only: listing a directory,
`stat()`-ing a file, and reading bytes to hash. There is no code path in `run_incremental_ingestion.py`
that opens a source file for writing. Every output path passes two independent checks before it is
written — one that it is inside the project root, and one that it is *not* inside the source
archive.

**2. Nothing is ever deleted.**

A file that has vanished from the archive is counted, listed in the report, and marked
`missing_file` on its row. The row stays. A network share that is briefly unmounted must not be
able to erase the memory of everything it held, and this script cannot tell an unmounted share
from a deletion.

---

## Folder layout

```
10_Ingestion\
  README.md                                    this file
  runs\
    ingestion_<timestamp>.json                 full machine-readable result of one run
  reports\
    latest_ingestion_summary.md                the most recent run, in full
    ingestion_<timestamp>.md                   the same report, kept by date
  manifests\
    ingestion_<timestamp>_file_changes.csv     one row per file the run had an opinion about
```

A copy of the latest summary is also written to `02_Metadata_Index\latest_ingestion_summary.md`.

---

## The daily command

```bat
cd "C:\Users\AI Admin\Documents\CutlerResearchBrain"

REM 1. What changed? Reads the archive, changes nothing, costs nothing.
py -3.11 scripts\run_incremental_ingestion.py ^
    --project-root "C:\Users\AI Admin\Documents\CutlerResearchBrain" ^
    --db "data\document_metadata.sqlite" ^
    --source-root "V:\Companies\A Current Month\2026" ^
    --dry-run --verbose

REM 2. Read the result.
py -3.11 scripts\inspect_ingestion.py ^
    --project-root "C:\Users\AI Admin\Documents\CutlerResearchBrain" ^
    --db "data\document_metadata.sqlite"

REM 3. Process the new and changed files. No embeddings unless you ask for them.
py -3.11 scripts\run_incremental_ingestion.py ^
    --project-root "C:\Users\AI Admin\Documents\CutlerResearchBrain" ^
    --db "data\document_metadata.sqlite" ^
    --source-root "V:\Companies\A Current Month\2026" ^
    --process-new --process-changed --extract-text --build-cards --update-obsidian --verbose

REM 4. Only if new or changed MSFT documents were found. THIS ONE SPENDS MONEY.
py -3.11 scripts\run_incremental_ingestion.py ^
    --project-root "C:\Users\AI Admin\Documents\CutlerResearchBrain" ^
    --db "data\document_metadata.sqlite" ^
    --source-root "V:\Companies\A Current Month\2026" ^
    --ticker MSFT --chunk-new --embed-new --update-keyword --run-smoke-eval --verbose
```

Steps 1 and 2 are free. Step 3 costs nothing but time. Only step 4 calls OpenAI, and only for
chunks that are genuinely new.

---

## How a file is classified

| Status | Meaning |
|---|---|
| `new_file` | The path is not in the metadata index |
| `changed_file` | Same path, different contents |
| `unchanged_file` | Same path, same contents — counted and left alone |
| `missing_file` | In the index, not found on the drive. **Reported, never deleted** |
| `skipped_file` | An unsupported extension, or a folder Phase 1 excludes |

**Cheap comparison first.** Size and modified time settle the overwhelming majority of files at
the cost of one `stat()` each. Hashing 11,697 PDFs over a network share to learn that none of them
moved is not a daily job. The SHA-256 is computed only when the cheap comparison is inconclusive,
or when `--hash-all` says to trust nothing.

**A touched file is not a changed file.** If size or timestamp moved but the hash is identical —
a copy, a restore from backup, a `touch` — the file is `unchanged` and nothing downstream is
redone.

---

## What a change makes stale

A changed file invalidates everything derived from it. Five columns on `documents` record that:

| Column | Meaning |
|---|---|
| `last_seen_at` | The archive still had this file when we last looked |
| `last_ingested_at` | We last processed it at this time |
| `file_change_status` | What the last walk decided about it |
| `content_hash_previous` / `content_hash_current` | What it was, what it is |
| `stale_extraction` / `stale_document_card` / `stale_rag_index` | What is now behind |

The flags are **set**, not automatically acted on. A run that only refreshes metadata leaves an
honest trail saying the extraction and the chunks are now behind, and the inspector will tell you
the command that catches them up. That is deliberate: embedding costs money, and deferring it to a
decision someone makes on purpose is the whole point of separating the flags from the work.

---

## Superseded chunks

When a changed MSFT document is re-chunked:

- the old `rag_chunks` **rows** are marked `superseded` and **kept**. They are the audit record of
  what an older answer was built on;
- the old **vectors** are removed from the Chroma collection.

Those are different decisions on purpose. A vector is a derived artifact, and a superseded one
describes text that is no longer in the file — an answer citing it would send a person to a PDF
that does not say what the citation claims. **A citation that does not check out is worse than no
answer at all.** The row survives for the audit; the vector goes so it cannot be retrieved.

New chunks from a re-chunked document carry a `_v2`, `_v3` … suffix, so they can never overwrite
the rows they just superseded.

---

## What is MSFT-only, and why

Metadata, extraction, document cards and the Obsidian vault run for **every** new file, whatever
ticker it belongs to. The RAG stages — chunking, embedding, the keyword index — run for **MSFT
only**, exactly as in Phases 5A through 8. `--chunk-new` or `--embed-new` with another ticker is
refused with an explanation rather than quietly doing nothing.

This is not a limitation to route around. The MSFT evaluation gate passed 10/10; no other ticker
has been through it, and indexing 10,675 documents that nothing has evaluated would produce a
system nobody can vouch for.

---

## The smoke evaluation

`--run-smoke-eval` runs **three** golden questions, not ten:

- `msft_azure_growth_sec` — one that must be answered
- `msft_no_evidence_test` — one that must be refused for lack of evidence
- `msft_safety_test` — one that must be refused outright

It is a quick "did ingestion break anything obvious", not a substitute for the full evaluation.
A `REVIEW` or `FAIL` here is a reason to run `scripts\run_evaluation.py` properly before trusting
new answers.

---

## In the UI

The demo page's health panel shows the last ingestion run, its status, new and changed file counts,
anything still stale, and the smoke-eval result. It is **read-only**: there is no button that runs
an ingestion. Walking a network share and spending money on embeddings should be a decision someone
makes at a terminal.

---

## When a run only half works

The first live run on the real archive updated metadata for 73 files and then failed to extract
70 of them. That is a **partial success**, and Phase 9.1 makes the system say so rather than
report `completed`.

### What went wrong, and what it looked like

The traceback pointed at `create_extraction_schema(conn)` in `extract_pdf_text.py`, which looks
like a schema-migration fault. It was not. That function is idempotent and correctly guarded, and
always has been.

The real error underneath was **`sqlite3.OperationalError: database is locked`**. Python's
`sqlite3` opens a write transaction on the first `UPDATE` and holds it until commit; the Phase 9
extract loop updated a document row per file and committed only after the whole loop. So from the
first successful document onward the parent process held the database, and every subsequent child
died at its first write — which is the `CREATE TABLE IF NOT EXISTS` inside that schema function.

The signature was **one file extracted, then seventy failures**. A genuine schema fault would have
failed the first file too. When every failure carries the same message, it is one problem, not
many — which is why the failure log now says exactly that at the bottom.

The fix: the database is released before every subprocess launch, every per-document update
commits immediately, and both the runner and the child scripts set a 60-second busy timeout so
brief contention waits instead of failing.

### Partial success

A run with any extraction or card failure is recorded as `partial_success`, never `completed`.
The report, the console summary and the inspector all say the same thing in plain words: *live
ingestion ran, but downstream processing failed for some files*. A broken daily job that reports
success goes unnoticed for a week.

### Seeing why, before retrying anything

```bat
py -3.11 scripts\run_incremental_ingestion.py ^
    --project-root "C:\Users\AI Admin\Documents\CutlerResearchBrain" ^
    --db "data\document_metadata.sqlite" ^
    --source-root "V:\Companies\A Current Month\2026" ^
    --diagnose-failed
```

Prints the first few failures in full and **retries nothing**. Seventy failures with one cause are
one problem; this is how you find out which it is without spending an hour to learn it seventy
times.

Every failed subprocess is also written in full to
`10_Ingestion\runs\ingestion_<timestamp>_failures.log` — the exact command, the complete stderr
and the complete stdout, nothing truncated. The database column keeps the one line that names the
cause, found by searching for the error rather than taking whatever came last.

### Repairing

```bat
py -3.11 scripts\run_incremental_ingestion.py ^
    --project-root "C:\Users\AI Admin\Documents\CutlerResearchBrain" ^
    --db "data\document_metadata.sqlite" ^
    --source-root "V:\Companies\A Current Month\2026" ^
    --retry-failed-extraction --retry-failed-cards --verbose
```

Repair mode **does not walk the archive and writes no metadata**. It works entirely from what the
previous run recorded, and reuses the existing document rows — a repaired document keeps its
`document_id`, so no duplicate row can appear.

- `--retry-failed-extraction` re-extracts documents whose *latest* extraction event was a failure.
- `--retry-failed-cards` rebuilds cards for documents whose card failed, **plus** any document
  that now has text and no usable card — which is every document whose card failed only because
  its extraction had failed first.

The vault is refreshed after the repairs, so it describes the cards that now exist rather than the
ones that did not.

### Order of operations after a partial run

1. `inspect_ingestion.py` — read the **RECOVERY NEEDED** section
2. `--diagnose-failed` — find out why, if it is not already obvious
3. `--retry-failed-extraction --retry-failed-cards` — repair only what failed
4. `inspect_ingestion.py` again — confirm the failures are gone
5. **Only then** consider `--chunk-new --embed-new`

Step 5 is last for a reason. Chunking while extraction failures stand would index an incomplete
set, and the embedding cost would be spent on it.

---

## Why a document is flagged stale, and when that clears

Phase 9 set `stale_rag_index` on every changed document and cleared it only for documents that
were successfully embedded. A document that RAG will *never* index — a duplicate, a spreadsheet,
one its card excludes — was therefore marked stale forever. Every run rediscovered it, reported
"10 documents are marked stale_rag_index", and recommended the chunk command, which then said "no
affected MSFT document is RAG-eligible, nothing to chunk". **A flag nobody can clear and nobody
can explain is worse than no flag, because it trains a reader to ignore the one that matters.**

Phase 9.2 puts every stale document into exactly one bucket, with a reason recorded in
`rag_skip_reason`:

| Bucket | What happens | Flag |
|---|---|---|
| **Eligible** | Chunked and embedded as normal | cleared once embedded, with nothing left pending |
| **Not eligible** | Nothing. RAG will never index it, however many times you run | **cleared**, with the specific rule that excludes it |
| **Not ready** | Nothing. Its extraction or card has to be repaired first | **kept** |
| **Missing source** | Nothing at all. Chunks untouched, nothing deleted | **kept**, marked `missing_source_needs_review` |

The reasons are specific on purpose. "Not eligible" is not something a person can act on; *"its
card excludes it: client_data"* or *"marked a duplicate of …"* is.

The order matters too: missing source is checked first, then not-ready, then eligibility. A
missing file cannot be re-read and an unextracted file cannot be chunked, so those facts come
before any question of whether the rules would have allowed it.

### Cleared, but still retrievable

If a document is no longer eligible *and* still has embedded chunks in the index, the flag is
cleared — but the run reports it separately, under **"Cleared, but still retrievable — worth a
look"**. Nothing is removed automatically. Whether an answer may still stand on chunks from a
document the rules now exclude is a judgement, not a cleanup.

### Clearing without chunking

```bat
py -3.11 scripts\run_incremental_ingestion.py ^
    --project-root "C:\Users\AI Admin\Documents\CutlerResearchBrain" ^
    --db "data\document_metadata.sqlite" ^
    --source-root "V:\Companies\A Current Month\2026" ^
    --ticker MSFT --clear-noneligible-stale-rag --verbose
```

Classifies every stale document and clears the ones RAG will never index. Chunks nothing, embeds
nothing, costs nothing. The normal `--chunk-new` path does the same classification, so this flag
is only needed when you want the state tidied without touching the index.

### What the tools will no longer do

Neither the report nor the inspector recommends the chunk command unless a document is genuinely
**eligible and waiting**. When the only remaining flags are missing sources or repairs, they say
that instead — and the inspector breaks the count down by reason so you can see at a glance
whether anything is actually yours to do.

---

## The RAG worklist is both halves

`--chunk-new` builds its worklist from **two** sources, and it has to:

- **A.** documents this walk touched — new and changed files;
- **B.** documents any *earlier* run left flagged `stale_rag_index` for this ticker.

Phase 9.2 only ever looked at half A in practice. Run 5 processed four new files, found none of
them RAG-eligible, and then reported ten stale documents it had never looked at — a number that
could not fall however many times the command ran. Embedding is often deferred to a later day, so
half B is the normal case, not an edge case.

### Documents of other tickers

A document tagged `JPM` with `stale_rag_index` set was matched by no ticker's worklist and cleared
by nothing. It was counted in every report forever.

A stale flag means *the RAG index is behind for this document*. For a ticker with no index at all,
there is nothing to be behind — so `--chunk-new` now clears those flags with exactly that recorded
in `rag_skip_reason`. Nothing is lost: when a ticker is first indexed, the full build covers every
eligible document regardless of any flag.

### Ticker matching

The old rule was SQL: `(',' || ticker_candidates_clean || ',') LIKE '%,MSFT,%'`. It matches
`MSFT` and `AAPL,MSFT` and **misses `AAPL, MSFT`** — a comma followed by a space. A multi-ticker
document written that way was invisible to the worklist, and nothing about the output would tell
you it was being skipped.

Matching now splits on the separator and compares whole tokens, which cannot have that class of
bug. It also accepts `;` and `|` as separators and ignores case.

---

## Missing source files

The inspector lists them by name, with when each was last seen, and then says the thing that
matters:

> Nothing was deleted, and nothing will be. Confirm the V drive/share was fully available before
> treating these as deleted. A share that was briefly unmounted looks exactly like this.

---

## What Phase 9 does not do

No scheduling or automation, no full-archive RAG expansion, no OCR, no authentication or
deployment, no Chroma rebuild, no re-embedding of unchanged chunks, and no write of any kind to
the V drive.

---

## Where you see the results

Phase 10A added a **Documents** tab to the local UI (`09_Web_App_Future\phase7a_local_ui`) that
reads what this pipeline produced: the extraction status and RAG state of every document, the
document card, the extracted text, the per-page text, and the chunks. It also surfaces the two
states this folder cares most about — **documents whose source file is missing**, with the same
"confirm the share was mounted" warning as the inspector, and **documents that are indexed for Ask
Brain versus not**.

That tab reads only what is inside the project, with one exception added in Phase 10B: an indexed
**PDF** can be previewed in the page, streamed straight from the archive through an endpoint that
takes a document id, refuses any path that does not resolve under the configured archive root, and
never copies the file. Nothing is written to the archive there either, and the UI still writes
nothing to the database.

The command line remains the place where the ingestion is *run* and where the full picture lives:

```bat
py -3.11 scripts\inspect_ingestion.py
```

---

_Internal tool. Cutler Capital Management LLC. Not investment advice, and not a source of
investment conclusions._
