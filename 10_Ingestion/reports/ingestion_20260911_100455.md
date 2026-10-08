# Incremental Ingestion Report

_Run 6 · `run_incremental_ingestion.py` (phase9.3-1.0) · mode **live**_

> **AI drafts. Humans verify. Cutler decides.**

## What this run did

This run compared the source archive with the metadata index and processed only the differences. Unchanged files were counted and left alone. Files that are in the index but no longer on the drive are listed below and were **not** deleted from the database - a share that is briefly unavailable must not be able to erase what it once held.

## Run

| | |
|---|---|
| Started | 2026-09-11T10:04:55 |
| Completed | 2026-09-11T10:06:46 |
| Source root | `V:\Companies\A Current Month\2026` (read-only) |
| Ticker | MSFT |
| Mode | live |
| Force full | False |
| Status | **completed** |

## Files

| | |
|---|---|
| Files seen | **12,165** |
| New | **4** |
| Changed | **0** |
| Unchanged | 12,161 |
| Missing (reported, not deleted) | 2 |
| Skipped (unsupported / excluded) | 276 |

## Work done

| Stage | Result |
|---|---|
| Metadata rows added | 4 |
| Metadata rows updated | 0 |
| PDFs extracted | 4 |
| Extraction failures | 0 |
| Document cards built | 4 |
| Card failures | 0 |
| Obsidian notes added | 4 |
| Chunks created | 0 |
| Chunks superseded | 0 |
| Superseded vectors removed | 0 |
| Chunks embedded | 0 |
| Keyword index | rebuilt |
| Smoke evaluation | PASS |

## New files

| relative_path | extension | size | action | status |
|---|---|---|---|---|
| SEPT 2026\9.11.26\CNN Fear & Greed Index 9.11.26.pdf | .pdf | 1,669,732 | text extracted; card created; chunked | ok |
| SEPT 2026\9.11.26\Insider Trading 9.11.26.pdf | .pdf | 310,025 | text extracted; card created; chunked | ok |
| SEPT 2026\9.11.26\Green Street\LXP Green Street 9.10.26.pdf | .pdf | 958,893 | text extracted; card created; chunked | ok |
| SEPT 2026\9.11.26\Green Street\STWD Green Street 9.10.26.pdf | .pdf | 768,266 | text extracted; card created; chunked | ok |

## Changed files

_No changed files._

## Failed files

_No failures._

## Missing files

_In the index, not found on the drive during this walk. **Nothing was deleted.** Check whether the share was available and whether these were intentionally removed._

| relative_path | document_id |
|---|---|
| SEPT 2026\9.11.26\Companies\BANC Form 144 9.10.26.pdf | doc_e03c949e42afeec5 |
| SEPT 2026\9.11.26\Companies\SFL Arctic 9.11.26.pdf | doc_8c0b561644cf9a8a |

## RAG-affected documents

| relative_path | tickers | action |
|---|---|---|
| SEPT 2026\9.11.26\CNN Fear & Greed Index 9.11.26.pdf |  | text extracted; card created; chunked |
| SEPT 2026\9.11.26\Insider Trading 9.11.26.pdf |  | text extracted; card created; chunked |
| SEPT 2026\9.11.26\Green Street\LXP Green Street 9.10.26.pdf | LXP | text extracted; card created; chunked |
| SEPT 2026\9.11.26\Green Street\STWD Green Street 9.10.26.pdf | STWD | text extracted; card created; chunked |

## Next recommended action

- 2 indexed file(s) were not found. Confirm the share was fully available before treating any of them as deleted.
- 10 stale flag(s) belonged to documents of other tickers, which nothing indexes yet. There is no index for them to be behind, so the flags were cleared with that recorded - they are not lost work.
- Nothing else is outstanding. Re-running the same command would find nothing to do.

---

_Counts describe what the pipeline did, not whether the documents are any good. A new document card is a draft of a guess about a file nobody has read yet._

**AI drafts. Humans verify. Cutler decides.**
