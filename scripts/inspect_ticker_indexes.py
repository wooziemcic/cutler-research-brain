#!/usr/bin/env python3
"""Phase 11 Part D - what is actually indexed, per ticker.

Read-only. It opens the database and looks on disk for each ticker's Chroma
store and keyword index; it does not open a collection, embed anything, or
touch the V drive.

The question it answers is the one to ask before trusting an answer: for this
ticker, is there an index, is it complete, and when was it last built?

  AI drafts. Humans verify. Cutler decides.

WHAT PHASE 11.2 CHANGED, AND WHY
--------------------------------
The Phase 11 version of this script decided "behind" by comparing two counts:

    eligible_documents > COUNT(DISTINCT document_id) in rag_chunks

Those are two different populations, so the difference between their SIZES says
almost nothing. A document can be eligible-and-indexed, eligible-and-missing,
or indexed-but-no-longer-eligible (it was eligible when it was embedded and its
metadata changed afterwards - marked duplicate, review raised to High, a card
exclusion added; its chunks stay, because this project never deletes chunks).

So "MSFT behind - 11 eligible documents not indexed" was not a statement about
eleven documents. It was the arithmetic of two unrelated totals. On the Phase 10
fixture the same comparison reads MSFT as 3 eligible against 10 indexed - minus
seven - while the true number of missing documents is zero.

This version compares SETS of document_id, via ticker_selection.reconcile(), and
prints the document_ids it is talking about. If it says five documents are
missing, it names five documents.

Usage:
  py -3.11 scripts\\inspect_ticker_indexes.py ^
      --project-root "C:\\Users\\AI Admin\\Documents\\CutlerResearchBrain" ^
      --db "data\\document_metadata.sqlite"
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ticker_selection as ts                                     # noqa: E402

PIPELINE_VERSION = "phase11.2-1.0"
DEFAULT_DETAIL_LIMIT = 25


def configure_console_encoding() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def safe_print(*parts) -> None:
    text = " ".join(str(part) for part in parts)
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode("ascii", "replace").decode("ascii"))
    except (OSError, ValueError):
        pass


def health_of(state: dict, recon: dict) -> tuple:
    """One word for the state of this ticker's index, and why.

    `recon` is a ticker_selection.reconcile() result, so "behind" is decided by
    the number of eligible documents with no embedded chunks - a set
    difference - and never by subtracting one total from another.
    """
    eligible = recon["eligible"]
    missing = recon["missing_count"]
    repairable = recon["repairable_count"]

    if not state["chunks"] and not eligible:
        return "not started", "nothing eligible in the archive"
    if not state["chunks"]:
        return "not started", f"{eligible} eligible document(s) waiting"
    if not state["chroma_present"]:
        return "broken", "chunk rows exist but there is no Chroma store to retrieve from"
    if state["failed"]:
        return "degraded", f"{state['failed']} chunk(s) failed to embed"
    if state["pending"]:
        return "incomplete", f"{state['pending']} chunk(s) not embedded yet"
    if not state["keyword_present"]:
        return "vector only", "no keyword index, so hybrid retrieval falls back to vector"
    if repairable:
        return "behind", (f"{repairable} eligible document(s) have no embedded chunks "
                          f"and can be repaired")
    if missing:
        # Missing, but embedding cannot fix it: no extracted text to chunk.
        return "ready*", (f"retrievable, but {missing} eligible document(s) have no text "
                          f"to index (see BEHIND DETAIL)")
    return "ready", "embedded, retrievable, and hybrid-capable"


def print_behind_detail(entries: list, detail_limit: int) -> None:
    """Name every document counted as missing, with the evidence for it.

    This is the section the count comparison could never produce. One block per
    document, because thirteen fields do not fit on a line anybody can read.
    """
    safe_print("=" * 96)
    safe_print("BEHIND DETAIL - the eligible documents that have no embedded chunks")
    safe_print("=" * 96)
    if not entries:
        safe_print("No ticker has an eligible document missing from its index.")
        safe_print("")
        return

    safe_print("Every document below is eligible NOW and has zero embedded chunks. A document")
    safe_print("that is indexed but no longer eligible is NOT listed here - it is counted")
    safe_print("separately, its chunks are intact, and it is not a problem to fix.")
    safe_print("")

    for ticker, recon in entries:
        safe_print("-" * 96)
        safe_print(f"{ticker}  -  {recon['missing_count']} missing "
                   f"({recon['repairable_count']} repairable, "
                   f"{recon['unrepairable_count']} cannot be fixed by embedding)")
        safe_print(f"         eligible now: {recon['eligible']}    "
                   f"with any chunk: {recon['with_any_chunk']}    "
                   f"with embedded chunks: {recon['with_embedded']}    "
                   f"indexed but no longer eligible: {recon['no_longer_eligible_count']}")
        for reason, count in sorted(recon["reason_counts"].items(),
                                    key=lambda item: -item[1]):
            safe_print(f"         {count:>4} x {reason}: {ts.MISSING_REASONS.get(reason, reason)}")
        safe_print("")

        shown = recon["missing"][:detail_limit]
        for item in shown:
            safe_print(f"  {item['document_id']}")
            safe_print(f"      file name         : {item['file_name'] or '(none recorded)'}")
            safe_print(f"      document date     : {item['document_date'] or '(unknown)'}")
            safe_print(f"      source type       : {item['source_type'] or '(unknown)'}")
            safe_print(f"      document type     : {item['document_type'] or '(unknown)'}")
            safe_print(f"      usable_for_rag    : {item['usable_for_rag']}")
            safe_print(f"      extraction status : {item['extraction_status'] or '(none)'}"
                       f"  quality {item['extraction_quality'] or '(none)'}"
                       f"  {item['total_text_chars']:,} char(s) recorded, "
                       f"{item['text_chars_on_disk']:,} on disk")
            safe_print(f"      card status       : {item['card_status']}")
            safe_print(f"      stale_rag_index   : {item['stale_rag_index'] or '(not tracked)'}")
            safe_print(f"      has chunks        : "
                       f"{'yes' if item['has_chunks'] else 'no'}"
                       f"   embedded {item['embedded_chunks']}"
                       f"   pending {item['pending_chunks']}"
                       f"   failed {item['failed_chunks']}"
                       f"   superseded {item['superseded_chunks']}")
            safe_print(f"      counted behind as : {item['reason']} - {item['reason_text']}")
            safe_print(f"      repairable        : "
                       f"{'yes - chunk and embed it' if item['repairable'] else 'NO - fix extraction first'}")
            safe_print("")
        remaining = recon["missing_count"] - len(shown)
        if remaining > 0:
            safe_print(f"  ... and {remaining} more. The full list, with no limit, is in the")
            safe_print(f"  reconcile report: scripts\\reconcile_ticker_index.py --tickers {ticker}")
            safe_print("")


def main() -> int:
    configure_console_encoding()
    parser = argparse.ArgumentParser(
        description="Phase 11: report every ticker index. Read-only.")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--db", default="data/document_metadata.sqlite")
    parser.add_argument("--tickers", nargs="+", default=list(ts.ALL_KNOWN_TICKERS))
    parser.add_argument("--all", action="store_true",
                        help="Report every ticker that has chunk rows, not just the known set.")
    parser.add_argument("--detail-limit", type=int, default=DEFAULT_DETAIL_LIMIT,
                        help="Documents listed per ticker in BEHIND DETAIL (0 = no limit).")
    parser.add_argument("--no-detail", action="store_true",
                        help="Table and summary only; skip the BEHIND DETAIL section.")
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve()
    db_path = Path(args.db)
    if not db_path.is_absolute():
        db_path = project_root / db_path
    if not db_path.exists():
        safe_print(f"ERROR: no database at {db_path}")
        return 2

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        tickers = [ts.safe_ticker(value) for value in args.tickers]
        if args.all and ts.table_exists(conn, "rag_chunks"):
            for row in conn.execute("SELECT DISTINCT UPPER(ticker) FROM rag_chunks "
                                    "WHERE COALESCE(ticker,'') <> ''"):
                if row[0] not in tickers:
                    tickers.append(row[0])

        safe_print("=" * 96)
        safe_print(f"TICKER INDEXES  {PIPELINE_VERSION}")
        safe_print("=" * 96)
        safe_print(f"{'Ticker':<7}{'Elig':>6}{'Docs':>6}{'Chunks':>8}{'Embed':>7}"
                   f"{'Pend':>6}{'Fail':>6}{'Miss':>6}{'Retd':>6}"
                   f"{'Chroma':>8}{'Keyword':>8}  {'Health':<13}Date range")
        safe_print("-" * 96)

        total_ready = 0
        details = []
        for ticker in tickers:
            state = ts.index_state(conn, project_root, ticker)
            recon = ts.reconcile(conn, project_root, ticker)
            health, why = health_of(state, recon)
            if health.startswith("ready"):
                total_ready += 1
            dates = (f"{state['date_from'] or '?'} to {state['date_to'] or '?'}"
                     if state["chunks"] else "")
            safe_print(f"{ticker:<7}{recon['eligible']:>6,}{state['documents']:>6,}"
                       f"{state['chunks']:>8,}{state['embedded']:>7,}"
                       f"{state['pending']:>6,}{state['failed']:>6,}"
                       f"{recon['missing_count']:>6,}{recon['no_longer_eligible_count']:>6,}"
                       f"{'yes' if state['chroma_present'] else 'no':>8}"
                       f"{'yes' if state['keyword_present'] else 'no':>8}  {health:<13}{dates}")
            details.append((ticker, state, recon, health, why))

        safe_print("-" * 96)
        safe_print(f"{total_ready} of {len(tickers)} ticker(s) can answer questions.")
        safe_print("")
        safe_print("Elig  eligible documents for this ticker RIGHT NOW (whole-token match).")
        safe_print("Docs  distinct documents that have chunk rows, eligible or not.")
        safe_print("Miss  eligible documents with NO embedded chunk. This, and only this,")
        safe_print("      is what 'behind' means. It is a set difference, not Elig - Docs.")
        safe_print("Retd  indexed but no longer eligible. Chunks intact, nothing to fix.")
        safe_print("")

        # What to run next, per ticker that is not simply ready.
        for ticker, state, recon, health, why in details:
            if health == "ready":
                continue
            safe_print(f"{ticker}: {health} - {why}")
            if recon["eligible"] and not state["embedded"]:
                safe_print(f"   build it:   py -3.11 scripts\\build_ticker_batch.py "
                           f"--project-root \"{project_root}\" --db \"{args.db}\" "
                           f"--tickers {ticker} --build-keyword --max-total-cost 1")
            elif state["pending"] or state["failed"]:
                safe_print(f"   finish it:  py -3.11 scripts\\build_rag_index.py "
                           f"--project-root \"{project_root}\" --ticker {ticker}")
            elif state["chunks"] and not state["keyword_present"]:
                safe_print(f"   add hybrid: py -3.11 scripts\\build_keyword_index.py "
                           f"--project-root \"{project_root}\" --ticker {ticker}")
            if recon["repairable_count"]:
                safe_print(f"   reconcile:  py -3.11 scripts\\reconcile_ticker_index.py "
                           f"--project-root \"{project_root}\" --db \"{args.db}\" "
                           f"--tickers {ticker}")
                safe_print( "               (read-only; it prints the repair command it would run)")

        safe_print("")
        for ticker, state, recon, health, why in details:
            if state["last_run"]:
                run = state["last_run"]
                safe_print(f"{ticker:<6} last embedding run #{run['run_id']} "
                           f"{run['status']} {str(run['completed_at'] or '')[:19]} "
                           f"({run['chunks_embedded']:,} chunk(s), "
                           f"${float(run['estimated_cost_usd'] or 0):.4f})")
        safe_print("")

        if not args.no_detail:
            behind = [(ticker, recon) for ticker, _, recon, _, _ in details
                      if recon["missing_count"]]
            limit = args.detail_limit if args.detail_limit > 0 else 10 ** 9
            print_behind_detail(behind, limit)

        safe_print("Read-only. Nothing was embedded and the V drive was not opened.")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
