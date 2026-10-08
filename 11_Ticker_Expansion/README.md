# 11_Ticker_Expansion — beyond MSFT, on purpose and on a budget

Phase 11. Nine more tickers: **KIM, KR, MRK, T, VZ, AMZN, SLB, B, ABBV**.

> **AI drafts. Humans verify. Cutler decides.**

Phases 5A through 10 built one index, for MSFT, and proved it with an evaluation
that passed 10/10. Phase 11 does not open that up to the whole archive. It
indexes **only the nine named tickers**, one at a time, with the cost shown
before anything is spent.

---

## The two things that can go wrong

**1. A short ticker matching everything.** Two of the nine are a single letter:
`T` (AT&T) and `B` (Barnes Group). A substring match for them is not a
near-miss, it is a catastrophe. Measured on the Phase 10 fixture:

```
WHERE ticker_candidates_clean LIKE '%T%'   ->  11 of 59 documents
WHERE ticker_candidates_clean LIKE '%B%'   ->  12 of 59 documents
```

On the real archive that is thousands of unrelated documents embedded at real
cost, in an index that would then answer AT&T questions with somebody else's
research. So **matching is never a substring test**. `scripts/ticker_selection.py`
splits the stored value into whole tokens and compares exactly, and every script
in this phase imports that one module — the planner, the builder and the batch
runner cannot disagree about what a ticker means.

**2. Spending nine times as much as intended.** Embedding is the only step in
this project that costs money. So the batch runner refuses to embed without
`--max-total-cost`, refuses again if the estimate exceeds it, and only proceeds
past that with `--confirm-cost` typed on purpose.

---

## The scripts

| Script | What it does | Spends money |
|---|---|---|
| `plan_ticker_expansion.py` | Inventory and cost estimate per ticker. Reads metadata only | **No** |
| `build_ticker_batch.py` | Runs the Phase 5A builder per ticker, behind the cost gate | Only with `--embed` |
| `inspect_ticker_indexes.py` | What is indexed, what is pending, what is broken | **No** |
| `run_ticker_smoke.py` | Three smoke questions per indexed ticker | Yes, unless `--dry-run` |
| `reconcile_ticker_index.py` | Names every eligible document missing from an index, and why | Only with `--repair-missing-chunks` |
| `ticker_selection.py` | The one definition of "this ticker's documents" | **No** |

---

## The run sequence

```bat
cd "C:\Users\AI Admin\Documents\CutlerResearchBrain"

REM 1. What would be indexed, and what would it cost? Spends nothing.
py -3.11 scripts\plan_ticker_expansion.py ^
    --project-root "C:\Users\AI Admin\Documents\CutlerResearchBrain" ^
    --db "data\document_metadata.sqlite" ^
    --tickers KIM KR MRK T VZ AMZN SLB B ABBV --export

REM 2. Read it. Especially the warnings for T and B.
REM    11_Ticker_Expansion\reports\ticker_expansion_plan.md

REM 3. Dry-run the batch. Still spends nothing.
py -3.11 scripts\build_ticker_batch.py ^
    --project-root "C:\Users\AI Admin\Documents\CutlerResearchBrain" ^
    --db "data\document_metadata.sqlite" ^
    --tickers KIM KR MRK T VZ AMZN SLB B ABBV --dry-run --build-keyword --verbose

REM 4. Only if the cost looks right. THIS ONE SPENDS MONEY.
py -3.11 scripts\build_ticker_batch.py ^
    --project-root "C:\Users\AI Admin\Documents\CutlerResearchBrain" ^
    --db "data\document_metadata.sqlite" ^
    --tickers KIM KR MRK T VZ AMZN SLB B ABBV ^
    --embed --build-keyword --max-total-cost 5 --verbose

REM 5. What did we get?
py -3.11 scripts\inspect_ticker_indexes.py ^
    --project-root "C:\Users\AI Admin\Documents\CutlerResearchBrain" ^
    --db "data\document_metadata.sqlite"

REM 6. Anything reported behind? Name it. Spends nothing.
py -3.11 scripts\reconcile_ticker_index.py ^
    --project-root "C:\Users\AI Admin\Documents\CutlerResearchBrain" ^
    --db "data\document_metadata.sqlite"

REM 7. Three smoke questions per indexed ticker. Costs a few cents.
py -3.11 scripts\run_ticker_smoke.py ^
    --project-root "C:\Users\AI Admin\Documents\CutlerResearchBrain" ^
    --db "data\document_metadata.sqlite"
```

Steps 1, 2, 3, 5 and 6 are free. Only 4 and 7 call OpenAI.

---

## One index per ticker

```
06_RAG_Index\chroma\<TICKER>\                       Chroma store
  collection: cutler_research_brain_<ticker>_2026
06_RAG_Index\keyword\<TICKER>_keyword_index.json    BM25 index
06_RAG_Index\reports\<TICKER>_rag_index_summary.md  what was built
```

Nothing is shared between tickers, so building KR cannot disturb MSFT. The
batch runner never passes `--force` unless you do, and without it a ticker that
already has an index gets **only its pending chunks** embedded — never a rebuild.

---

## Documents that belong to no ticker

`ticker_candidates_clean` is blank when the raw candidates were ambiguous: the
Phase 1 cleaner drops `UBS,RBC` rather than guess which company a document is
about. **Those documents will not be indexed for any ticker.** That is a
deliberate exclusion, not an oversight, and the plan report counts them so
nobody discovers it by wondering where a document went.

---

## Smoke questions, and how to read them

`smoke_questions\phase11_smoke_questions.json` holds three questions per ticker:
a company-update question, an analyst-concern question, and a safety question
that must be refused.

**A refusal is not a failure.** An index with thin evidence *should* decline,
and a ticker whose smoke test declines is telling the truth about its index. The
failures that matter are a confident answer with no citation, a citation
belonging to another ticker, and any recommendation at all.

This is **not** the golden evaluation. That is Phase 12, and no ticker should be
trusted the way MSFT is until it has been through one.

---

## Phase 11.1 — the Windows subprocess decoding trap

The first real Phase 11 run worked: nine tickers indexed, 30/30 smoke questions
passed. The console also filled with

```
UnicodeDecodeError: 'charmap' codec can't decode byte 0x9d
```

thrown from inside subprocess's **reader threads**. Every answer was fine; the
parent could not decode what the child wrote.

`subprocess.run(..., text=True)` with no `encoding=` decodes using the machine's
ANSI code page — cp1252 on Windows. The child, told `PYTHONUTF8=1`, wrote UTF-8.
Byte `0x9d` is the second byte of a smart quote in UTF-8 and undefined in
cp1252. Because `capture_output` plus `timeout` drains the pipes on threads, the
traceback arrived with no obvious owner and the run looked broken when it was
not.

**This was a regression, not a discovery.** `run_evaluation.py` (Phase 8),
`run_incremental_ingestion.py` (Phase 9.1) and the UI's `app.py` (Phase 7A) all
already passed `encoding="utf-8", errors="replace"`. The two scripts written in
Phase 11 did not.

Both now do. `build_ticker_batch.py` had the identical latent bug and would have
hit it mid-batch — **after the embedding spend** — the first time a builder
logged a file name containing a smart quote or an en dash.

The durable fix is in the test suite: `test_phase111.py` scans **every**
`subprocess.run` and `subprocess.Popen` in the project and fails if a text-mode
call omits `encoding` and `errors`. The fifth regression fails in the sandbox
rather than on a Windows console.

Also hardened in `run_ticker_smoke.py`: the console is reconfigured to UTF-8
before anything is printed, `safe_print` survives both a bad character and a
broken stream, and reports are written with `errors="replace"` on the write side
— because text that came back through a replacing decoder can still carry a lone
surrogate, and `str.encode` would raise on it after the questions had been asked
and paid for.

---

## Phase 11.2 — the phantom "behind" status

After the real run, `inspect_ticker_indexes.py` reported:

```
MSFT  behind - 11 eligible document(s) not indexed
KR    behind - 14
MRK   behind - 16
T     behind - 62
VZ    behind - 5
```

None of those numbers was a number of documents. The inspector decided "behind"
like this:

```python
if state["embedded"] and eligible > state["documents"]:
    return "behind", f"{eligible - state['documents']} eligible document(s) not indexed"
```

`eligible` counts documents that pass the RAG rules **now**.
`state["documents"]` counts distinct `document_id`s in `rag_chunks`. They are
different populations, so subtracting one from the other conflates three groups:

| A document can be | What it means |
|---|---|
| eligible now **and** indexed | fine |
| eligible now **and not** indexed | genuinely behind |
| indexed but **no longer** eligible | it was eligible when it was embedded and something changed after — marked duplicate, review raised to High, a card exclusion added. Its chunks stay, because nothing in this project deletes chunks. **Nothing to fix.** |

A difference of 11 could be 11 missing documents, or 30 missing and 19 retired.
On the Phase 10 fixture the same expression reads MSFT as 3 eligible against 10
indexed — *minus seven* — while the true number of missing documents is **zero**.
The comparison could never answer the question, and it never reached zero.

**The fix.** `ticker_selection.reconcile()` compares **sets of `document_id`** and
classifies every eligible document that has no embedded chunk:

| Reason | Repairable by embedding? |
|---|---|
| `no_extracted_text_path` — never extracted | **No** — fix extraction |
| `extracted_text_file_absent` — the text file is not on disk | **No** — fix extraction |
| `extracted_text_empty` — nothing in the file, usually a scanned PDF | **No** — needs OCR, a later phase |
| `chunks_failed` — chunks exist, every embedding failed | Yes |
| `chunks_pending` — chunks exist, never embedded | Yes |
| `only_superseded_chunks` — the document changed and needs re-chunking | Yes |
| `never_chunked` — eligible, has text, never chunked | Yes |

"Behind" now means *repairable missing documents*, and the inspector prints a
**BEHIND DETAIL** section that names each one with its document_id, file name,
date, source and document type, `usable_for_rag`, extraction status and quality,
characters recorded versus characters actually on disk, card status,
`stale_rag_index`, its chunk counts by embedding status, the reason it is
counted, and whether embedding can fix it.

A ticker whose only missing documents have no text reads `ready*`, not `behind` —
because no cost limit will ever fix them and calling that "behind" invites
someone to keep paying for nothing.

**A second, quieter bug found on the way.** Task 2 was to confirm the builder and
the inspector select the identical documents. They did not, in one case. The
builder's card clause was

```sql
COALESCE(c.rag_exclusion_reason, 'none') = 'none'
```

`COALESCE` only substitutes for NULL, so a card whose reason is the **empty
string** failed that test and the document was invisible to the builder — while
the inspector's Python test accepted blank as "not excluded" and counted it
eligible. Such a document would be reported behind forever and never catch up.
A blank reason is not a reason: the clause now lives in
`ticker_selection.CARD_EXCLUSION_SQL`, uses `NULLIF(TRIM(...), '')`, and is read
by both sides, so they cannot drift apart again.

### The reconciler

```bat
REM Read-only. Names every missing document and prints the repair command.
py -3.11 scripts\reconcile_ticker_index.py ^
    --project-root "C:\Users\AI Admin\Documents\CutlerResearchBrain" ^
    --db "data\document_metadata.sqlite"
```

Reports land in `11_Ticker_Expansion\reports\ticker_reconciliation.md` and
`ticker_reconciliation_missing.csv` (one row per missing document, every field).

Repair is opt-in and gated twice — a missing flag is a refusal, not a prompt:

```bat
py -3.11 scripts\reconcile_ticker_index.py ^
    --project-root "C:\Users\AI Admin\Documents\CutlerResearchBrain" ^
    --db "data\document_metadata.sqlite" ^
    --tickers MRK ^
    --repair-missing-chunks --max-total-cost 1 --confirm-cost --build-keyword
```

* `--repair-missing-chunks` without `--max-total-cost` → refused, exit 3.
* an estimate over the limit without `--confirm-cost` → refused, exit 3.
* within the limit but without `--confirm-cost` → still refused, exit 3. Spending
  money is typed on purpose.
* The estimate covers both the repairable documents' text **and** every existing
  un-embedded chunk for that ticker, because the builder embeds those too. A gate
  that guesses low is not a gate.
* The repair runs the ordinary builder **without `--force`**: already-embedded
  chunks are skipped, no chunk row is deleted, and documents with no text are
  excluded from the repair rather than paid for.
* Without `--build-keyword` the keyword index is left older than the chunks, and
  the script says so at the end.

---

## Phase 11.3 — the repair that moved chunks instead of adding them

The Phase 11.2 repairs ran cleanly — every step exited 0 — and then the "behind"
counts moved between tickers:

```
repair MSFT KR MRK T VZ   ->  59 still missing
repair T AMZN ABBV        ->  MSFT 9, KR 14, MRK 16, T 8, VZ 59
```

Nothing was lost. No chunk was deleted. No Chroma collection was dropped. The
chunks changed **owner**.

### The root cause, in three lines

```python
chunk_id = f"{document_id}_c{chunk_index:04d}"          # no ticker in the id
CREATE TABLE rag_chunks (chunk_id TEXT PRIMARY KEY, ...)
INSERT OR REPLACE INTO rag_chunks ...
```

The ticker is a **column**, not part of the key. A document tagged with two
tickers has ONE set of chunk rows, so building the second ticker replaces the
first ticker's rows in place — same ids, same text, a new value in `ticker`.
Whichever ticker was built **last** owns the document; the other reports it
missing for ever. Repairing that one takes it back; the next run takes it again.

Measured on the live database after the 11.2 repairs:

| | |
|---|---:|
| chunk rows | 36,007 |
| distinct `chunk_id`s | 36,007 |
| documents whose chunks span more than one ticker | **0** |
| eligible documents claimed by more than one ticker | **101** |
| "missing" (document, ticker) pairs that were already embedded elsewhere | **106 of 106** |

Not one of the reported-missing documents was un-indexed. Every one of them was
embedded under another ticker — and it is a real fault, not just a reporting
artefact: `ask_brain.py` restricts candidates with `WHERE ticker = ?`, so the
losing ticker genuinely cannot cite those documents.

| Ticker | Missing | Currently owned by |
|---|---:|---|
| MSFT | 9 | AMZN (8), ABBV (1) |
| KR | 14 | AMZN (14) |
| MRK | 16 | ABBV (16) |
| T | 8 | AMZN (8) |
| VZ | 59 | T (54), AMZN (5) |

The last batch ran `T, AMZN, ABBV` in that order, and AMZN and ABBV own
everything shared. The order of the command line decided the result.

### What changed

**The collision has a name.** `reconcile()` reports it as
`claimed_by_another_ticker`, with the ticker that currently owns the document,
and it is **not offered as repairable** while the key is unscoped — because
repairing it is a theft, not a repair.

**The repair refuses.** `--repair-missing-chunks` exits **5** if any target
document is in that state, explains why, and names the migration.

**`--explain-repair-plan`** is a dry-run diff: per ticker, the exact
`document_id`s that would be ADDED, the ones BLOCKED with their current owner,
the ones unfixable for lack of text, and the removals — which are always zero,
printed so the claim is checked rather than asserted.

**Every repair step is bracketed by a snapshot** of every known ticker — not
only the ones named on the command line, because 11.2's damage was done to
tickers nobody was watching. Each snapshot holds the eligible ids, the embedded
document ids, the missing ids, the chunk-row counts and the Chroma count. If a
step reduces **anybody's** embedded documents, the run stops, names the ticker
and the lost ids, and exits **4**.

### The migration

```bat
REM Dry run. Reports what it would do and writes nothing.
py -3.11 scripts\migrate_rag_chunk_key.py ^
    --project-root "C:\Users\AI Admin\Documents\CutlerResearchBrain" ^
    --db "data\document_metadata.sqlite"

REM Then, when the numbers look right:
py -3.11 scripts\migrate_rag_chunk_key.py ... --apply
```

It rebuilds `rag_chunks` with `PRIMARY KEY (chunk_id, ticker)` and copies every
row across unchanged — same ids, same text, same `embedding_status`. Columns are
read from `PRAGMA table_info` rather than from a constant, so a column added by
a later phase is carried over rather than dropped (the live table has 25; the
Phase 5A constant lists 24).

* It copies the table to `rag_chunks_backup_<stamp>` **first** and verifies it
  row-for-row before anything else happens. The backup is left in place.
* Every count — rows, distinct ids, per ticker, per status — is compared before
  and after. A mismatch rolls back the whole thing in one transaction.
* **No `chunk_id` changes**, so every vector already in Chroma still matches its
  row. Chroma is never opened. Nothing is embedded. Nothing is deleted.

Verified on a copy of the live database: 36,007 rows in, 36,007 rows out, all
`embedded`, and the plan then reads **106 to add, 0 blocked, 0 removed**.

`build_rag_index.create_schema` now creates the composite key for a **new**
database, so this is a one-time migration, not a recurring one.

### What Phase 11.3 did not do

No re-embedding. No chunk deleted. No Chroma collection dropped or rebuilt. No
change to prompts or retrieval. No write to `V:\Companies\A Current Month`. No
live repair — that waits for approval.

---

_Internal tool. Cutler Capital Management LLC. Not investment advice, and not a
source of investment conclusions._
