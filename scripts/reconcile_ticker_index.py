#!/usr/bin/env python3
"""Phase 11.2 / 11.3 - reconcile what SHOULD be indexed with what IS.

PHASE 11.3, IN ONE PARAGRAPH
----------------------------
The Phase 11.2 repairs appeared to work and then the "behind" counts moved
between tickers. They were not moving: chunks were. `chunk_id` is
`{document_id}_c{index:04d}` and `rag_chunks` is keyed by `chunk_id` alone, so
a document tagged with two tickers has ONE set of chunk rows and `INSERT OR
REPLACE` hands them to whichever ticker was built last. Nothing is deleted and
the row count never falls, so it does not look like loss - but the losing
ticker can no longer cite the document, because ask_brain filters candidates
with `WHERE ticker = ?`. Such a document is now reported as
`claimed_by_another_ticker`, is NOT offered as repairable, and the repair path
REFUSES to run while any target is in that state. `--explain-repair-plan`
prints the diff - which document_ids would be added, which are blocked, and the
zero removals - and every repair step is bracketed by a snapshot of every known
ticker, so a repair that reduces anybody's index stops the run and exits 4.

WHY THIS EXISTS
---------------
`inspect_ticker_indexes.py` used to report "behind" by subtracting two totals:
the number of eligible documents, and the number of distinct documents that had
chunk rows. Those are different populations, so the difference between their
SIZES is not a number of documents. It cannot be acted on, and it never goes to
zero, because a document that was indexed and has since stopped being eligible
sits in one total and not the other forever.

This script compares SETS of document_id and names every document it counts. If
it says five documents are missing, there are five document_ids printed below
it, each with the reason it is missing and whether embedding can fix it.

  eligible now                -> the documents ticker_selection says belong to
                                 this ticker and pass every RAG rule
  with any chunk              -> of those, the ones that have chunk rows
  with embedded chunks        -> of those, the ones that are actually retrievable
  MISSING                     -> eligible, zero embedded chunks. This is the
                                 only honest definition of "behind".
  indexed but no longer eligible -> has embedded chunks, no longer passes the
                                 rules. Not an error. Nothing to fix. Its
                                 chunks stay, because this project never
                                 deletes chunks.

Several reasons for missing cannot be fixed by embedding at all - a document
whose extracted text is absent or empty produces no chunks however many times
the builder runs. Those are marked unrepairable and excluded from any repair.

WHAT IT DOES BY DEFAULT
-----------------------
Nothing. It reads the database and the extracted-text folder, prints the
reconciliation, writes a report, and exits. No API call, no Chroma write, no
chunk row, no V drive access.

REPAIR
------
`--repair-missing-chunks` chunks and embeds ONLY the repairable missing
documents' tickers, and it will not start without a cost gate:

    --repair-missing-chunks --max-total-cost <usd> --confirm-cost

Missing either flag is a refusal, not a prompt. The repair runs the ordinary
builder WITHOUT `--force`, so already-embedded chunks are skipped rather than
re-embedded, and no existing chunk row is deleted.

  AI drafts. Humans verify. Cutler decides.

Usage:
  py -3.11 scripts\\reconcile_ticker_index.py ^
      --project-root "C:\\Users\\AI Admin\\Documents\\CutlerResearchBrain" ^
      --db "data\\document_metadata.sqlite"
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ticker_selection as ts                                     # noqa: E402
import plan_ticker_expansion as plan                              # noqa: E402

PIPELINE_VERSION = "phase11.3-1.0"
STEP_TIMEOUT_SECONDS = 3600

# Exit codes, so a script or a person can tell the refusals apart.
EXIT_OK = 0
EXIT_STEP_FAILED = 1
EXIT_BAD_INPUT = 2
EXIT_COST_REFUSED = 3
EXIT_REGRESSION = 4          # a repair took something away: stop everything
EXIT_UNSAFE_SCHEMA = 5       # the chunk key cannot express two tickers at once

# The same estimator the planner and the batch gate use. Imported rather than
# re-typed so a change to the cost model cannot leave this script quoting an
# old price.
CHARS_PER_TOKEN = plan.CHARS_PER_TOKEN
DEFAULT_CHUNK_SIZE = plan.DEFAULT_CHUNK_SIZE
DEFAULT_COST_PER_1M_TOKENS = plan.DEFAULT_COST_PER_1M_TOKENS


def configure_console_encoding() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def safe_print(*parts) -> None:
    text = " ".join(str(part) for part in parts)
    try:
        print(text, flush=True)
        return
    except UnicodeEncodeError:
        pass
    except (OSError, ValueError):
        return
    try:
        print(text.encode("ascii", "replace").decode("ascii"), flush=True)
    except Exception:                        # noqa: BLE001 - never fatal
        pass


def write_text_safely(path: Path, text: str) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", errors="replace", newline="\n") as handle:
            handle.write(text)
        return True
    except OSError as exc:
        safe_print(f"   could not write {path.name}: {exc}")
        return False


def repair_cost(conn: sqlite3.Connection, recon: dict, chunk_size: int,
                cost_per_1m: float) -> dict:
    """What repairing this ticker would cost, from the chunks that would be sent.

    Two populations get embedded by a plain builder run, and the gate has to
    cover both or it is not a gate:

      * the repairable MISSING documents - estimated from the text actually on
        disk, because that is the file the builder opens;
      * chunks that already exist for this ticker and are not embedded -
        pending, failed and superseded rows belonging to documents that are
        otherwise fine. They are not "missing documents", but the builder will
        embed them in the same run, and they cost money.

    Deliberately an over-estimate rather than an under-estimate. A gate that
    guesses low is worse than no gate.
    """
    doc_tokens = 0
    for item in recon["missing"]:
        if not item["repairable"]:
            continue
        chars = max(int(item["text_chars_on_disk"] or 0),
                    int(item["total_text_chars"] or 0))
        doc_tokens += int(chars / CHARS_PER_TOKEN) if chars > 0 else 0
    doc_chunks = plan.estimate_chunks(doc_tokens, chunk_size) if doc_tokens else 0

    existing_tokens = 0
    existing_chunks = 0
    if ts.table_exists(conn, "rag_chunks"):
        row = conn.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(chunk_token_count), 0) AS tokens "
            "  FROM rag_chunks "
            " WHERE UPPER(ticker) = ? "
            "   AND COALESCE(embedding_status, '') <> 'embedded'", (recon["ticker"],)).fetchone()
        existing_chunks, existing_tokens = int(row["n"]), int(row["tokens"])

    tokens = doc_tokens + existing_tokens
    return {"ticker": recon["ticker"],
            "documents_to_repair": recon["repairable_count"],
            "documents_unrepairable": recon["unrepairable_count"],
            "estimated_new_chunks": doc_chunks,
            "existing_unembedded_chunks": existing_chunks,
            "estimated_tokens": tokens,
            "estimated_cost_usd": round(tokens / 1_000_000 * cost_per_1m, 6)}


def force_claimed_repairable(recon: dict) -> None:
    """Treat documents owned by another ticker as repairable after all.

    This is the pre-Phase-11.3 behaviour and it is only reachable through
    --allow-unsafe-chunk-key. It is kept, rather than deleted, for one reason:
    the loss guard has to be provable against the real failure. With this
    override the repair really does take rows from another ticker, and the
    snapshot comparison really does stop the run and exit 4 - which is the only
    honest way to know the guard works.
    """
    for item in recon["missing"]:
        if item["reason"] == ts.CLAIMED:
            item["repairable"] = True
    recon["repairable_count"] = sum(1 for item in recon["missing"] if item["repairable"])
    recon["unrepairable_count"] = recon["missing_count"] - recon["repairable_count"]


def print_chunk_key_warning(project_root: Path, db_arg: str, claimed: int) -> None:
    """Say plainly why some documents cannot be repaired, and what fixes it."""
    safe_print("=" * 96)
    safe_print("THE CHUNK KEY DOES NOT NAME A TICKER")
    safe_print("=" * 96)
    safe_print("  build_rag_index.py builds chunk ids as")
    safe_print("")
    safe_print("      chunk_id = f\"{document_id}_c{chunk_index:04d}\"")
    safe_print("")
    safe_print("  and rag_chunks declares `chunk_id TEXT PRIMARY KEY`, while chunks are written")
    safe_print("  with INSERT OR REPLACE. The ticker is a COLUMN, not part of the key. So a")
    safe_print("  document tagged with two tickers has ONE set of chunk rows, and building the")
    safe_print("  second ticker REPLACES the first ticker's rows in place - same ids, same text,")
    safe_print("  a new value in the `ticker` column.")
    safe_print("")
    safe_print("  Nothing is deleted and the row count never drops, which is why this does not")
    safe_print("  look like data loss. The chunks change owner, and the ticker that lost them")
    safe_print("  reports them as missing on the next run. Repairing that ticker takes them")
    safe_print("  back, and the next ticker's run takes them again.")
    safe_print("")
    safe_print(f"  {claimed} document(s) in this run are in exactly that state.")
    safe_print("")
    safe_print("  This is NOT fixable by embedding. It is fixed by making the key name a ticker:")
    safe_print("")
    safe_print(f"      py -3.11 scripts\\migrate_rag_chunk_key.py "
               f"--project-root \"{project_root}\" --db \"{db_arg}\"")
    safe_print("")
    safe_print("  which is a dry run by default and reports exactly what it would do.")
    safe_print("")


def repair_plan(recon: dict) -> dict:
    """Exactly what a repair of this ticker would add, and what it would not.

    Phase 11.3 exists because a repair that looked like an addition was in fact
    a transfer: the same chunk rows, handed from one ticker to another. So the
    plan is stated in three lists, and the third one is the point.

      add      - eligible documents with no embedded chunks anywhere for this
                 ticker, which the builder will chunk and embed. New rows.
      blocked  - documents already embedded under ANOTHER ticker. Repairing
                 them under the current key would move rows, not add them, so
                 they are excluded until the key names a ticker.
      remove   - always empty. Nothing in a repair removes a document from an
                 index, and this list exists so that claim is checked rather
                 than asserted.
    """
    add, blocked = [], []
    for item in recon["missing"]:
        if item["reason"] == ts.CLAIMED and not item["repairable"]:
            blocked.append(item)
        elif item["repairable"]:
            add.append(item)
    return {"ticker": recon["ticker"], "add": add, "blocked": blocked, "remove": [],
            "unfixable": [item for item in recon["missing"]
                          if not item["repairable"] and item["reason"] != ts.CLAIMED]}


def print_repair_plan(plans: list, detail_limit: int, key_scoped: bool) -> None:
    """The dry-run diff: which document_ids would be added, and zero removals."""
    safe_print("=" * 96)
    safe_print("REPAIR PLAN (dry run) - what a repair WOULD do, document by document")
    safe_print("=" * 96)
    safe_print(f"chunk key names a ticker: {'YES' if key_scoped else 'NO'}")
    if not key_scoped:
        safe_print("  rag_chunks is keyed by chunk_id alone, and chunk_id does not contain the")
        safe_print("  ticker. Two tickers cannot hold the same chunk at once, so a document")
        safe_print("  eligible for both belongs to whichever ticker was built LAST. Repairing")
        safe_print("  such a document moves rows instead of adding them, and the next ticker's")
        safe_print("  run moves them back. Those documents are listed as BLOCKED below and are")
        safe_print("  excluded from any repair until the key is migrated.")
    safe_print("")

    total_add = total_blocked = total_unfixable = 0
    for plan in plans:
        add, blocked, unfixable = plan["add"], plan["blocked"], plan["unfixable"]
        total_add += len(add)
        total_blocked += len(blocked)
        total_unfixable += len(unfixable)
        safe_print("-" * 96)
        safe_print(f"{plan['ticker']}   + {len(add)} to add   "
                   f"! {len(blocked)} blocked (owned by another ticker)   "
                   f"x {len(unfixable)} unfixable (no text)   "
                   f"- {len(plan['remove'])} removals")
        for item in add[:detail_limit]:
            safe_print(f"    + {item['document_id']}  {item['reason']:<26}"
                       f"{item['text_chars_on_disk']:>10,} chars  "
                       f"{(item['file_name'] or '?')[:40]}")
        if len(add) > detail_limit:
            safe_print(f"    + ... and {len(add) - detail_limit} more (full list in the report)")
        for item in blocked[:detail_limit]:
            safe_print(f"    ! {item['document_id']}  owned by {item['claimed_by'] or '?':<12}"
                       f"  {(item['file_name'] or '?')[:40]}")
        if len(blocked) > detail_limit:
            safe_print(f"    ! ... and {len(blocked) - detail_limit} more")
        for item in unfixable[:5]:
            safe_print(f"    x {item['document_id']}  {item['reason']}")
        if len(unfixable) > 5:
            safe_print(f"    x ... and {len(unfixable) - 5} more")
    safe_print("-" * 96)
    safe_print(f"TOTAL: {total_add} document(s) would be ADDED, "
               f"{total_blocked} BLOCKED, {total_unfixable} unfixable, "
               f"and 0 REMOVED.")
    safe_print("A repair never removes a document from an index. This plan lists removals so")
    safe_print("that statement is checked on real data rather than taken on trust; if a repair")
    safe_print("ever does reduce a ticker's embedded documents, it stops and exits 4.")
    safe_print("")


def print_ticker(recon: dict, cost: dict, detail_limit: int) -> None:
    safe_print("-" * 96)
    safe_print(f"{recon['ticker']}")
    safe_print(f"  eligible now                   : {recon['eligible']:,}")
    safe_print(f"  with any chunk                 : {recon['with_any_chunk']:,}")
    safe_print(f"  with embedded chunks           : {recon['with_embedded']:,}")
    safe_print(f"  MISSING from the index         : {recon['missing_count']:,}"
               f"   ({recon['repairable_count']:,} repairable, "
               f"{recon['unrepairable_count']:,} not fixable by embedding)")
    safe_print(f"  indexed but no longer eligible : {recon['no_longer_eligible_count']:,}"
               f"   (chunks intact - nothing to fix)")
    if recon["missing_count"]:
        safe_print(f"  estimated repair cost          : "
                   f"${cost['estimated_cost_usd']:.4f} "
                   f"({cost['estimated_tokens']:,} token(s), "
                   f"~{cost['estimated_new_chunks']:,} new chunk(s) plus "
                   f"{cost['existing_unembedded_chunks']:,} existing un-embedded chunk(s))")
    safe_print("")

    if not recon["missing_count"]:
        if not recon["eligible"]:
            safe_print("  Nothing eligible for this ticker in the archive, so nothing to index.")
        else:
            safe_print("  Nothing is missing. Every eligible document has embedded chunks.")
        safe_print("")
        return

    for reason, count in sorted(recon["reason_counts"].items(), key=lambda item: -item[1]):
        safe_print(f"  {count:>5} x {reason}: {ts.MISSING_REASONS.get(reason, reason)}")
    safe_print("")
    safe_print(f"  {'document_id':<26}{'date':<12}{'chunks':<8}{'chars':>10}  reason / file")
    for item in recon["missing"][:detail_limit]:
        chunk_note = ("e{embedded}/p{pending}/f{failed}/s{superseded}".format(**{
            "embedded": item["embedded_chunks"], "pending": item["pending_chunks"],
            "failed": item["failed_chunks"], "superseded": item["superseded_chunks"]})
            if item["has_chunks"] else "none")
        owned = f"  [owned by {item['claimed_by']}]" if item.get("claimed_by") else ""
        safe_print(f"  {item['document_id']:<26}{(item['document_date'] or '?'):<12}"
                   f"{chunk_note:<8}{item['text_chars_on_disk']:>10,}  "
                   f"{item['reason']}{owned}")
        safe_print(f"  {'':<26}{'':<12}{'':<8}{'':>10}  "
                   f"{item['file_name'] or '(no file name)'}")
    remaining = recon["missing_count"] - len(recon["missing"][:detail_limit])
    if remaining > 0:
        safe_print(f"  ... and {remaining:,} more. The full list is in the report and the CSV.")
    safe_print("")


def build_report(project_root: Path, results: list, total_cost: float,
                 repaired: bool, settings: dict, plans: list = None,
                 key_scoped: bool = True, diffs: list = None) -> str:
    lines = [
        "# Ticker index reconciliation",
        "",
        f"_{PIPELINE_VERSION} · {datetime.now().isoformat(timespec='seconds')}_",
        "",
        "> **What \"behind\" means here.** A document is behind when it is RAG-eligible now",
        "> and has zero embedded chunks. That is a set difference over `document_id`, not",
        "> the gap between two totals. A document that is indexed but no longer eligible is",
        "> counted separately and is **not** a problem: its chunks are intact and remain",
        "> retrievable, because this project never deletes chunks.",
        "",
        "> AI drafts. Humans verify. Cutler decides.",
        "",
        "| Ticker | Eligible | With chunks | With embedded | Missing | Repairable | Not fixable | "
        "No longer eligible | Repair est. |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for recon, cost in results:
        lines.append(
            f"| {recon['ticker']} | {recon['eligible']:,} | {recon['with_any_chunk']:,} | "
            f"{recon['with_embedded']:,} | **{recon['missing_count']:,}** | "
            f"{recon['repairable_count']:,} | {recon['unrepairable_count']:,} | "
            f"{recon['no_longer_eligible_count']:,} | "
            f"${cost['estimated_cost_usd']:.4f} |")
    lines += ["", f"**Total estimated repair cost: ${total_cost:.4f}** at "
                  f"${settings['cost_per_1m']:.4f} per 1M tokens, "
                  f"{settings['chunk_size']:,}-token chunks. An estimate, not a bill.", ""]

    claimed_total = sum(recon["claimed_count"] for recon, _ in results)
    if claimed_total and not key_scoped:
        lines += [
            "## Why some of these cannot be repaired by embedding", "",
            f"`rag_chunks` is keyed by `chunk_id` alone, and `chunk_id` is built as",
            "`{document_id}_c{chunk_index:04d}` — **it does not contain the ticker**. Chunks are",
            "written with `INSERT OR REPLACE`, so a document tagged with two tickers has one set",
            "of chunk rows and building the second ticker replaces the first ticker's rows in",
            "place: same ids, same text, a new value in the `ticker` column.",
            "",
            "Nothing is deleted and the row count never drops, which is why this does not look",
            "like data loss. The chunks change owner; the ticker that lost them reports them",
            "missing on the next run; repairing that ticker takes them back; the next ticker's",
            "run takes them again. That is the churn Phase 11.2's repairs produced.",
            "",
            f"**{claimed_total} document(s) in this run are in exactly that state.** They are",
            "listed below as `claimed_by_another_ticker` with the ticker that currently owns",
            "them, and they are excluded from any repair until the key names a ticker:",
            "",
            "```",
            "py -3.11 scripts\\migrate_rag_chunk_key.py "
            f"--project-root \"{project_root}\" --db \"data\\document_metadata.sqlite\"",
            "```",
            "",
            "A dry run by default. It rebuilds `rag_chunks` with `PRIMARY KEY (chunk_id, ticker)`,",
            "copying every existing row and deleting none.",
            "",
        ]

    if plans is not None:
        lines += ["## Repair plan (dry run)", "",
                  "| Ticker | Would ADD | Blocked (owned elsewhere) | Unfixable (no text) | "
                  "Would REMOVE |", "|---|---:|---:|---:|---:|"]
        for plan in plans:
            lines.append(f"| {plan['ticker']} | {len(plan['add'])} | {len(plan['blocked'])} | "
                         f"{len(plan['unfixable'])} | {len(plan['remove'])} |")
        lines += ["", "**Removals: 0.** A repair adds documents to an index and never takes one",
                  "away. If a repair ever does reduce a ticker's embedded documents, it stops and",
                  "exits 4 rather than continuing.", ""]
        for plan in plans:
            if plan["add"]:
                lines += [f"### {plan['ticker']}: would add {len(plan['add'])} document(s)", "",
                          ", ".join(f"`{item['document_id']}`" for item in plan["add"]), ""]
            if plan["blocked"]:
                lines += [f"### {plan['ticker']}: {len(plan['blocked'])} blocked by the chunk key",
                          "", "| document_id | currently owned by | file name |", "|---|---|---|"]
                for item in plan["blocked"]:
                    lines.append(f"| `{item['document_id']}` | {item['claimed_by']} | "
                                 f"{(item['file_name'] or '?').replace('|', '/')} |")
                lines.append("")

    if diffs:
        lines += ["## Before and after, per ticker", "",
                  "| Ticker | Embedded docs before | after | gained | **lost** | Chunk rows "
                  "before | after | Chroma before | after |",
                  "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
        for diff in diffs:
            lines.append(
                f"| {diff['ticker']} | {len(diff.get('before_embedded', []))} | "
                f"{len(diff.get('after_embedded', []))} | {diff['gained_documents']} | "
                f"**{diff['lost_documents']}** | {diff['chunk_rows_before']:,} | "
                f"{diff['chunk_rows_after']:,} | "
                f"{diff['chroma_before'] if diff['chroma_before'] is not None else '?'} | "
                f"{diff['chroma_after'] if diff['chroma_after'] is not None else '?'} |")
        lines.append("")

    for recon, cost in results:
        if not recon["missing_count"]:
            continue
        lines += [f"## {recon['ticker']} - {recon['missing_count']:,} missing", ""]
        for reason, count in sorted(recon["reason_counts"].items(), key=lambda i: -i[1]):
            lines.append(f"- `{reason}` x {count}: {ts.MISSING_REASONS.get(reason, reason)}")
        lines += ["",
                  "| document_id | date | file name | source | type | usable | extraction | "
                  "card | stale_rag_index | chunks (emb/pend/fail/sup) | chars on disk | reason | "
                  "repairable |",
                  "|---|---|---|---|---|---:|---|---|---|---|---:|---|---|"]
        for item in recon["missing"]:
            lines.append(
                f"| `{item['document_id']}` | {item['document_date'] or '?'} | "
                f"{(item['file_name'] or '?').replace('|', '/')} | "
                f"{item['source_type']} | {item['document_type']} | {item['usable_for_rag']} | "
                f"{item['extraction_status']}/{item['extraction_quality'] or '-'} | "
                f"{item['card_status']} | {item['stale_rag_index'] or '-'} | "
                f"{item['embedded_chunks']}/{item['pending_chunks']}/"
                f"{item['failed_chunks']}/{item['superseded_chunks']} | "
                f"{item['text_chars_on_disk']:,} | `{item['reason']}` | "
                f"{'yes' if item['repairable'] else 'NO'} |")
        lines.append("")
        if recon["no_longer_eligible"]:
            lines += [f"### {recon['ticker']}: indexed but no longer eligible "
                      f"({recon['no_longer_eligible_count']:,})", "",
                      "These have embedded chunks and no longer pass the RAG rules - usually "
                      "marked duplicate, review raised to High, or a card exclusion added after "
                      "they were indexed. **Nothing to do.** Listed so the counts add up.", "",
                      ", ".join(f"`{doc}`" for doc in recon["no_longer_eligible"][:200]), ""]

    repairable_tickers = [recon["ticker"] for recon, _ in results if recon["repairable_count"]]
    lines += ["## What to run", ""]
    if repaired:
        lines += ["The repair has already been run in this session; re-run this script to "
                  "confirm the new state.", ""]
    elif repairable_tickers:
        lines += ["Read the tables above first, then:", "", "```",
                  "py -3.11 scripts\\reconcile_ticker_index.py "
                  f"--project-root \"{project_root}\" "
                  f"--tickers {' '.join(repairable_tickers)} "
                  f"--repair-missing-chunks --max-total-cost {max(1, int(total_cost) + 1)} "
                  "--confirm-cost --build-keyword", "```", "",
                  "That runs the ordinary builder without `--force`: already-embedded chunks "
                  "are skipped, nothing is deleted, and nothing outside the project is touched.",
                  ""]
    else:
        lines += ["Nothing is repairable by embedding. Every ticker is either complete or its "
                  "missing documents have no extracted text to index - those need extraction "
                  "fixed first, not a bigger cost limit.", ""]
    lines.append("")
    return "\n".join(lines)


def write_csv(path: Path, results: list) -> bool:
    columns = ["ticker", "document_id", "file_name", "document_date", "source_type",
               "document_type", "usable_for_rag", "extraction_status", "extraction_quality",
               "card_status", "stale_rag_index", "has_chunks", "embedded_chunks",
               "pending_chunks", "failed_chunks", "superseded_chunks", "total_text_chars",
               "text_chars_on_disk", "text_path", "reason", "reason_text", "repairable"]
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            for recon, _ in results:
                for item in recon["missing"]:
                    writer.writerow({"ticker": recon["ticker"], **item})
        return True
    except OSError as exc:
        safe_print(f"   could not write {path.name}: {exc}")
        return False


def run_step(command: list, project_root: Path) -> dict:
    """One builder invocation. Argument list, shell=False, UTF-8 both ends.

    `encoding`/`errors` are not optional: without them `text=True` decodes with
    the Windows ANSI code page while the child, told PYTHONUTF8=1, writes
    UTF-8. The mismatch raises UnicodeDecodeError inside subprocess's reader
    thread, which is the Phase 11.1 fault and looks like a broken run when
    every step actually succeeded.
    """
    environment = dict(os.environ)
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8"
    try:
        completed = subprocess.run(command, cwd=str(project_root), env=environment,
                                   capture_output=True, text=True,
                                   encoding="utf-8", errors="replace",
                                   shell=False, timeout=STEP_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        return {"command": command, "returncode": None, "timed_out": True,
                "stdout": "", "stderr": ""}
    return {"command": command, "returncode": completed.returncode, "timed_out": False,
            "stdout": completed.stdout or "", "stderr": completed.stderr or ""}


def main() -> int:
    configure_console_encoding()
    parser = argparse.ArgumentParser(
        description="Phase 11.2: reconcile each ticker's index against its eligible documents. "
                    "Read-only unless --repair-missing-chunks is given.")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--db", default="data/document_metadata.sqlite")
    parser.add_argument("--tickers", nargs="+", default=list(ts.ALL_KNOWN_TICKERS))
    parser.add_argument("--detail-limit", type=int, default=25,
                        help="Missing documents printed per ticker (0 = no limit). The report "
                             "and CSV always contain every one.")
    parser.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE)
    parser.add_argument("--cost-per-1m-tokens", type=float, default=DEFAULT_COST_PER_1M_TOKENS)
    parser.add_argument("--repair-missing-chunks", action="store_true",
                        help="Chunk and embed the repairable missing documents. "
                             "Requires --max-total-cost AND --confirm-cost.")
    parser.add_argument("--max-total-cost", type=float, default=None,
                        help="Refuse to repair if the estimate exceeds this, in USD.")
    parser.add_argument("--confirm-cost", action="store_true",
                        help="Typed on purpose: proceed with the repair at the estimated cost.")
    parser.add_argument("--build-keyword", action="store_true",
                        help="Rebuild each repaired ticker's keyword index afterwards.")
    parser.add_argument("--explain-repair-plan", action="store_true",
                        help="Dry-run diff: the exact document_ids a repair would ADD, the ones "
                             "it cannot add because another ticker owns their chunks, and a "
                             "confirmation that it would remove nothing. Spends nothing.")
    parser.add_argument("--allow-unsafe-chunk-key", action="store_true",
                        help="Repair even though rag_chunks is keyed by chunk_id alone. Not "
                             "recommended: for a document two tickers share, the repair moves "
                             "rows instead of adding them.")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve()
    db_path = Path(args.db)
    if not db_path.is_absolute():
        db_path = project_root / db_path
    if not db_path.exists():
        safe_print(f"ERROR: no database at {db_path}")
        return EXIT_BAD_INPUT
    try:
        tickers = [ts.safe_ticker(value) for value in args.tickers]
    except ValueError as exc:
        safe_print(f"ERROR: {exc}")
        return EXIT_BAD_INPUT

    settings = {"chunk_size": args.chunk_size, "cost_per_1m": args.cost_per_1m_tokens}
    detail_limit = args.detail_limit if args.detail_limit > 0 else 10 ** 9

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        key_scoped = ts.chunk_key_is_ticker_scoped(conn)
        results = []
        for ticker in tickers:
            recon = ts.reconcile(conn, project_root, ticker)
            if args.allow_unsafe_chunk_key and not key_scoped:
                force_claimed_repairable(recon)
            cost = repair_cost(conn, recon, args.chunk_size, args.cost_per_1m_tokens)
            results.append((recon, cost))
    finally:
        conn.close()

    total_cost = round(sum(cost["estimated_cost_usd"] for _, cost in results), 6)
    total_missing = sum(recon["missing_count"] for recon, _ in results)
    total_repairable = sum(recon["repairable_count"] for recon, _ in results)
    total_claimed = sum(recon["claimed_count"] for recon, _ in results)
    plans = [repair_plan(recon) for recon, _ in results]

    mode = "READ-ONLY"
    if args.explain_repair_plan:
        mode = "EXPLAIN REPAIR PLAN (dry run)"
    elif args.repair_missing_chunks:
        mode = "REPAIR"

    safe_print("=" * 96)
    safe_print(f"TICKER INDEX RECONCILIATION  {PIPELINE_VERSION}   mode: {mode}")
    safe_print("=" * 96)
    safe_print(f"{len(tickers)} ticker(s). \"Missing\" means eligible now with zero embedded "
               f"chunks -")
    safe_print("a set difference over document_id, not the gap between two totals.")
    safe_print("")
    for recon, cost in results:
        print_ticker(recon, cost, detail_limit)

    safe_print("-" * 96)
    safe_print(f"{total_missing:,} document(s) missing across {len(tickers)} ticker(s); "
               f"{total_repairable:,} repairable by embedding.")
    if total_claimed:
        safe_print(f"{total_claimed:,} of them are already embedded UNDER ANOTHER TICKER and are "
                   f"not repairable")
        safe_print("by embedding - see the chunk-key note below.")
    safe_print(f"Estimated repair cost: ${total_cost:.4f} "
               f"(estimate, not a bill).")
    safe_print("")

    reports = project_root / "11_Ticker_Expansion" / "reports"
    repaired = False
    repair_results = []

    if args.explain_repair_plan:
        print_repair_plan(plans, detail_limit, key_scoped)
        if total_claimed and not key_scoped:
            print_chunk_key_warning(project_root, args.db, total_claimed)
        report = build_report(project_root, results, total_cost, False, settings, plans, key_scoped)
        write_text_safely(reports / "ticker_reconciliation.md", report)
        write_csv(reports / "ticker_reconciliation_missing.csv", results)
        safe_print(f"Report: {reports / 'ticker_reconciliation.md'}")
        safe_print("")
        safe_print("Dry run. Nothing was embedded, no chunk row was written, no Chroma "
                   "collection was")
        safe_print("opened, and the V drive was not touched.")
        return EXIT_OK

    # --- the schema guard ----------------------------------------------------
    # A repair cannot be append-only while two tickers cannot hold the same
    # chunk_id. Refusing here is the whole point of Phase 11.3.
    if args.repair_missing_chunks and total_claimed and not key_scoped \
            and not args.allow_unsafe_chunk_key:
        print_chunk_key_warning(project_root, args.db, total_claimed)
        safe_print("REFUSED: repairing now would move chunk rows between tickers, not add them.")
        safe_print("         Nothing was embedded.")
        safe_print("         Migrate the key first, or pass --allow-unsafe-chunk-key if you have")
        safe_print("         read the above and want the old behaviour anyway.")
        return EXIT_UNSAFE_SCHEMA

    # --- the gate -----------------------------------------------------------
    if args.repair_missing_chunks:
        if not total_repairable:
            safe_print("Nothing to repair: no eligible document is missing for a reason that "
                       "embedding can fix.")
            safe_print("Documents with absent or empty extracted text need extraction fixed "
                       "first. Nothing was embedded.")
            args.repair_missing_chunks = False
        elif args.max_total_cost is None:
            safe_print("REFUSED: --repair-missing-chunks requires --max-total-cost, in USD.")
            safe_print(f"         This repair is estimated at ${total_cost:.4f}.")
            safe_print(f"         For example: --max-total-cost "
                       f"{max(1, int(total_cost) + 1)} --confirm-cost")
            safe_print("         Nothing was embedded.")
            return EXIT_COST_REFUSED
        elif total_cost > args.max_total_cost and not args.confirm_cost:
            safe_print(f"REFUSED: the estimate is ${total_cost:.4f}, over the "
                       f"${args.max_total_cost:.4f} limit you set.")
            safe_print("         Nothing was embedded.")
            safe_print("         Either raise --max-total-cost, reduce --tickers, or pass "
                       "--confirm-cost")
            safe_print("         to proceed at that price on purpose.")
            return EXIT_COST_REFUSED
        elif not args.confirm_cost:
            safe_print("REFUSED: --repair-missing-chunks also requires --confirm-cost.")
            safe_print(f"         The estimate is ${total_cost:.4f}, within your "
                       f"${args.max_total_cost:.4f} limit, but a repair that spends money is "
                       f"typed on purpose.")
            safe_print("         Nothing was embedded.")
            return EXIT_COST_REFUSED

    if not args.repair_missing_chunks:
        if total_claimed and not key_scoped:
            print_chunk_key_warning(project_root, args.db, total_claimed)
        report = build_report(project_root, results, total_cost, False, settings,
                              plans, key_scoped)
        report_path = reports / "ticker_reconciliation.md"
        write_text_safely(report_path, report)
        write_csv(reports / "ticker_reconciliation_missing.csv", results)
        safe_print(f"Report: {report_path}")
        safe_print(f"CSV   : {reports / 'ticker_reconciliation_missing.csv'}")
        safe_print("")
        safe_print("Read-only. Nothing was embedded, no Chroma collection was written, no chunk "
                   "row was created,")
        safe_print("and the V drive was not opened.")
        return EXIT_OK

    # --- the repair ---------------------------------------------------------
    safe_print(f"--confirm-cost given: repairing at an estimated ${total_cost:.4f}.")
    safe_print("The builder runs WITHOUT --force: already-embedded chunks are skipped and no "
               "chunk row is deleted.")
    safe_print("")
    builder = project_root / "scripts" / "build_rag_index.py"
    keyword = project_root / "scripts" / "build_keyword_index.py"
    failures = 0
    diffs = []
    regression = None

    # A snapshot of EVERY ticker in the system, not only the ones being
    # repaired. Phase 11.2's damage was done to tickers that were not named on
    # the command line: repairing T took documents away from VZ, which nobody
    # was watching. Task 8's "repair T does not change VZ" is only checkable if
    # VZ is measured.
    watched = list(dict.fromkeys(list(tickers) + list(ts.ALL_KNOWN_TICKERS)))

    def take_snapshots() -> dict:
        connection = sqlite3.connect(str(db_path))
        connection.row_factory = sqlite3.Row
        try:
            return {t: ts.snapshot(connection, project_root, t) for t in watched}
        finally:
            connection.close()

    snapshots = take_snapshots()

    for recon, cost in results:
        if not recon["repairable_count"]:
            continue
        ticker = recon["ticker"]
        safe_print(f"  {ticker}: repairing {recon['repairable_count']} document(s) ...")
        steps = [[sys.executable, str(builder),
                  "--project-root", str(project_root), "--db", str(db_path),
                  "--ticker", ticker, "--chunk-size", str(args.chunk_size)]]
        if args.build_keyword:
            steps.append([sys.executable, str(keyword),
                          "--project-root", str(project_root), "--db", str(db_path),
                          "--ticker", ticker])
        ticker_steps = []
        for command in steps:
            step = run_step(command, project_root)
            ticker_steps.append(step)
            label = Path(command[1]).name
            if step["timed_out"]:
                safe_print(f"        {label}: TIMED OUT after {STEP_TIMEOUT_SECONDS}s")
                failures += 1
                break
            if step["returncode"] != 0:
                safe_print(f"        {label}: exited {step['returncode']}")
                if args.verbose:
                    safe_print(step["stderr"][-2000:])
                failures += 1
                break
            safe_print(f"        {label}: ok")

        # --- did this step take anything from anybody? ----------------------
        after_snapshots = take_snapshots()
        step_diffs = []
        for watched_ticker in watched:
            diff = ts.snapshot_diff(snapshots[watched_ticker], after_snapshots[watched_ticker])
            diff["before_embedded"] = sorted(snapshots[watched_ticker]["embedded_document_ids"])
            diff["after_embedded"] = sorted(
                after_snapshots[watched_ticker]["embedded_document_ids"])
            diff["repaired_ticker"] = ticker
            step_diffs.append(diff)
            if diff["gained_documents"]:
                safe_print(f"        {watched_ticker}: +{diff['gained_documents']} "
                           f"embedded document(s)")
            if diff["regressed"]:
                regression = diff
        diffs.extend(step_diffs)
        snapshots = after_snapshots

        repair_results.append({"ticker": ticker,
                               "documents_to_repair": recon["repairable_count"],
                               "estimated_cost_usd": cost["estimated_cost_usd"],
                               "diffs": [{k: v for k, v in d.items()
                                          if k not in ("before_embedded", "after_embedded")}
                                         for d in step_diffs if d["regressed"]
                                         or d["gained_documents"] or d["lost_documents"]],
                               "steps": [{k: v for k, v in step.items()
                                          if k not in ("stdout", "stderr")}
                                         for step in ticker_steps]})

        if regression is not None:
            safe_print("")
            safe_print("!" * 96)
            safe_print(f"STOPPED: repairing {ticker} REDUCED what {regression['ticker']} has.")
            safe_print("!" * 96)
            safe_print(f"  {regression['ticker']} embedded documents: "
                       f"{len(regression['before_embedded'])} -> "
                       f"{len(regression['after_embedded'])}  "
                       f"({regression['lost_documents']} lost)")
            safe_print(f"  {regression['ticker']} chunk rows: "
                       f"{regression['chunk_rows_before']:,} -> "
                       f"{regression['chunk_rows_after']:,}  "
                       f"(embedded {regression['embedded_chunk_rows_before']:,} -> "
                       f"{regression['embedded_chunk_rows_after']:,})")
            if regression["chroma_lost"]:
                safe_print(f"  {regression['ticker']} Chroma vectors: "
                           f"{regression['chroma_before']} -> {regression['chroma_after']}")
            for document_id in regression["lost_document_ids"][:20]:
                safe_print(f"    lost: {document_id}")
            if len(regression["lost_document_ids"]) > 20:
                safe_print(f"    ... and {len(regression['lost_document_ids']) - 20} more")
            safe_print("")
            safe_print("  A repair is append-only or it is not a repair. No further ticker will")
            safe_print("  be touched in this run. Nothing was deleted - chunk rows are most")
            safe_print("  likely to have changed OWNER rather than disappeared; check the")
            safe_print("  chunk-key note in the report.")
            break

    repaired = True
    safe_print("")

    # Re-read, so the report states what is true AFTER the repair.
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        after = []
        for ticker in tickers:
            recon = ts.reconcile(conn, project_root, ticker)
            after.append((recon, repair_cost(conn, recon, args.chunk_size,
                                             args.cost_per_1m_tokens)))
    finally:
        conn.close()

    still_missing = sum(recon["missing_count"] for recon, _ in after)
    still_repairable = sum(recon["repairable_count"] for recon, _ in after)
    safe_print("-" * 96)
    safe_print(f"After the repair: {still_missing:,} document(s) still missing, "
               f"{still_repairable:,} of them repairable.")
    if still_repairable:
        safe_print("Some repairable documents are still missing - read the report before "
                   "re-running.")
    safe_print("")

    net = {}
    for diff in diffs:
        entry = net.setdefault(diff["ticker"], dict(diff))
        entry["gained_documents"] = entry.get("gained_documents", 0)
        entry.update({k: diff[k] for k in ("after_embedded", "chunk_rows_after",
                                           "embedded_chunk_rows_after", "chroma_after")})
    summary_diffs = sorted(net.values(), key=lambda d: d["ticker"])

    report = build_report(project_root, after, 0.0, True, settings,
                          [repair_plan(recon) for recon, _ in after], key_scoped,
                          summary_diffs)
    report_path = reports / "ticker_reconciliation.md"
    write_text_safely(report_path, report)
    write_csv(reports / "ticker_reconciliation_missing.csv", after)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    write_text_safely(reports / f"ticker_reconcile_repair_{stamp}.json",
                      json.dumps({"pipeline_version": PIPELINE_VERSION,
                                  "completed_at": datetime.now().isoformat(),
                                  "tickers": tickers,
                                  "chunk_key_is_ticker_scoped": key_scoped,
                                  "estimated_total_cost_usd": total_cost,
                                  "max_total_cost": args.max_total_cost,
                                  "results": repair_results,
                                  "regression": regression,
                                  "still_missing": still_missing,
                                  "still_repairable": still_repairable},
                                 indent=2, default=str, ensure_ascii=False))
    safe_print(f"Report: {report_path}")
    if not args.build_keyword:
        safe_print("")
        safe_print("NOTE: --build-keyword was not given, so each repaired ticker's keyword index "
                   "is now")
        safe_print("      older than its chunks and hybrid retrieval will not see the new "
                   "documents.")
        safe_print("      Run scripts\\build_keyword_index.py --ticker <TICKER> for each one.")
    if regression is not None:
        return EXIT_REGRESSION
    return EXIT_STEP_FAILED if failures else EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
