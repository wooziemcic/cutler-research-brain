# Ticker index reconciliation

_phase11.3-1.0 · 2026-09-15T09:16:51_

> **What "behind" means here.** A document is behind when it is RAG-eligible now
> and has zero embedded chunks. That is a set difference over `document_id`, not
> the gap between two totals. A document that is indexed but no longer eligible is
> counted separately and is **not** a problem: its chunks are intact and remain
> retrievable, because this project never deletes chunks.

> AI drafts. Humans verify. Cutler decides.

| Ticker | Eligible | With chunks | With embedded | Missing | Repairable | Not fixable | No longer eligible | Repair est. |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| MSFT | 128 | 128 | 128 | **0** | 0 | 0 | 0 | $0.0000 |
| KR | 154 | 154 | 154 | **0** | 0 | 0 | 0 | $0.0000 |
| MRK | 286 | 286 | 286 | **0** | 0 | 0 | 0 | $0.0000 |
| T | 200 | 200 | 200 | **0** | 0 | 0 | 0 | $0.0000 |
| VZ | 175 | 175 | 175 | **0** | 0 | 0 | 0 | $0.0000 |
| ABBV | 192 | 192 | 192 | **0** | 0 | 0 | 0 | $0.0000 |

**Total estimated repair cost: $0.0000** at $0.0200 per 1M tokens, 900-token chunks. An estimate, not a bill.

## Repair plan (dry run)

| Ticker | Would ADD | Blocked (owned elsewhere) | Unfixable (no text) | Would REMOVE |
|---|---:|---:|---:|---:|
| MSFT | 0 | 0 | 0 | 0 |
| KR | 0 | 0 | 0 | 0 |
| MRK | 0 | 0 | 0 | 0 |
| T | 0 | 0 | 0 | 0 |
| VZ | 0 | 0 | 0 | 0 |
| ABBV | 0 | 0 | 0 | 0 |

**Removals: 0.** A repair adds documents to an index and never takes one
away. If a repair ever does reduce a ticker's embedded documents, it stops and
exits 4 rather than continuing.

## Before and after, per ticker

| Ticker | Embedded docs before | after | gained | **lost** | Chunk rows before | after | Chroma before | after |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| ABBV | 176 | 192 | 0 | **0** | 3,725 | 3,725 | 3725 | 3725 |
| AMZN | 293 | 293 | 0 | **0** | 6,365 | 6,365 | 6365 | 6365 |
| B | 76 | 76 | 0 | **0** | 3,169 | 3,169 | 3169 | 3169 |
| KIM | 148 | 148 | 0 | **0** | 4,282 | 4,282 | 4282 | 4282 |
| KR | 140 | 154 | 0 | **0** | 3,746 | 3,746 | 3746 | 3746 |
| MRK | 270 | 286 | 0 | **0** | 5,675 | 5,675 | 5675 | 5675 |
| MSFT | 119 | 128 | 9 | **0** | 2,221 | 2,221 | 2221 | 2221 |
| SLB | 59 | 59 | 0 | **0** | 1,348 | 1,348 | 1348 | 1348 |
| T | 192 | 200 | 0 | **0** | 4,525 | 4,851 | 4851 | 4851 |
| VZ | 116 | 175 | 0 | **0** | 2,125 | 4,296 | 4296 | 4296 |

## What to run

The repair has already been run in this session; re-run this script to confirm the new state.

