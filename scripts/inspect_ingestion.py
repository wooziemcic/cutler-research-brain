#!/usr/bin/env python3
r"""Cutler Research Brain - Phase 9: read the ingestion record.

Read-only. This script answers one question in several ways: **is the index
still a fair description of the archive, and if not, what is the next command?**

It never writes anything, never calls an API, and never touches the source
archive. Run it as often as you like.

  py -3.11 scripts\inspect_ingestion.py --project-root "C:\...\CutlerResearchBrain"
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

PIPELINE_VERSION = "phase9.3-1.0"
PRINT_FAILURES = 0


def configure_console_encoding() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


def safe_print(text: str = "") -> None:
    global PRINT_FAILURES
    try:
        print(text)
    except (UnicodeEncodeError, OSError):
        PRINT_FAILURES += 1
        try:
            print(str(text).encode("ascii", "replace").decode("ascii"))
        except Exception:  # noqa: BLE001
            PRINT_FAILURES += 1


def rule(char: str = "-") -> None:
    safe_print(char * 78)


def heading(title: str) -> None:
    safe_print()
    rule("=")
    safe_print(title)
    rule("=")


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())


def column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    try:
        return column in {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    except sqlite3.OperationalError:
        return False


def scalar(conn: sqlite3.Connection, sql: str, params=()) -> int:
    try:
        return int(conn.execute(sql, params).fetchone()[0] or 0)
    except (sqlite3.OperationalError, TypeError):
        return 0


def age_phrase(when: str) -> str:
    """How long ago, in words a person uses."""
    if not when:
        return "never"
    try:
        moment = datetime.fromisoformat(when)
    except ValueError:
        return when
    delta = datetime.now() - moment
    if delta < timedelta(minutes=2):
        return "just now"
    if delta < timedelta(hours=1):
        return f"{int(delta.total_seconds() // 60)} minutes ago"
    if delta < timedelta(days=1):
        return f"{int(delta.total_seconds() // 3600)} hours ago"
    return f"{delta.days} day(s) ago"


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Cutler Research Brain Phase 9 - inspect the ingestion record (read-only).")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--db", default="data/document_metadata.sqlite")
    parser.add_argument("--top", type=int, default=10, help="How many rows per section.")
    parser.add_argument("--latest", action="store_true",
                        help="Only the latest run, in brief.")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    configure_console_encoding()
    args = parse_args(argv)

    project_root = Path(args.project_root).expanduser().resolve()
    db_path = (project_root / args.db) if not Path(args.db).is_absolute() else Path(args.db)
    if not db_path.exists():
        safe_print(f"ERROR: no database at {db_path}")
        return 1

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    if not table_exists(conn, "ingestion_runs"):
        heading("INGESTION")
        safe_print("No ingestion has ever run on this database.")
        safe_print()
        safe_print("Start with a dry run, which reads the archive and changes nothing:")
        safe_print(r'  py -3.11 scripts\run_incremental_ingestion.py --project-root "'
                   f'{project_root}" --db "{args.db}" --dry-run')
        conn.close()
        return 0

    runs = [dict(r) for r in conn.execute(
        "SELECT * FROM ingestion_runs ORDER BY ingestion_run_id DESC LIMIT ?",
        (max(1, args.top),))]
    total_runs = scalar(conn, "SELECT COUNT(*) FROM ingestion_runs")
    latest = runs[0] if runs else {}

    # ---- history ---------------------------------------------------------
    if not args.latest:
        heading(f"INGESTION HISTORY  ({total_runs} run(s) recorded)")
        safe_print(f"{'run':>4}  {'completed':<20} {'mode':<9} {'seen':>7} {'new':>5} "
                   f"{'chg':>5} {'miss':>5} {'emb':>5}  status")
        rule()
        for run in runs:
            mode = "dry run" if str(run.get("dry_run")) == "1" else "live"
            if str(run.get("force_full")) == "1":
                mode = "FORCE FULL"
            safe_print(
                f"{run['ingestion_run_id']:>4}  {(run.get('completed_at') or '')[:19]:<20} "
                f"{mode:<9} {int(run.get('files_seen') or 0):>7,} "
                f"{int(run.get('new_files') or 0):>5,} {int(run.get('changed_files') or 0):>5,} "
                f"{int(run.get('missing_files') or 0):>5,} "
                f"{int(run.get('chunks_embedded') or 0):>5,}  {run.get('status') or ''}")

    if not latest:
        conn.close()
        return 0

    # ---- latest run ------------------------------------------------------
    heading(f"LATEST RUN  ({latest['ingestion_run_id']})")
    mode = "DRY RUN" if str(latest.get("dry_run")) == "1" else "live"
    safe_print(f"  Status        : {latest.get('status')}  ({mode})")
    if str(latest.get("status")) == "partial_success":
        safe_print("  >> Live ingestion ran, but downstream processing failed for some files.")
        safe_print("  >> Metadata and the vault were updated. The text and cards were not.")
    safe_print(f"  Started       : {latest.get('started_at')}")
    safe_print(f"  Completed     : {latest.get('completed_at')}  "
               f"({age_phrase(latest.get('completed_at') or '')})")
    safe_print(f"  Source root   : {latest.get('source_root')}")
    safe_print(f"  Ticker        : {latest.get('ticker') or '(none)'}")
    if latest.get("error_message"):
        safe_print(f"  Error         : {latest.get('error_message')}")

    safe_print()
    safe_print("FILES")
    rule()
    safe_print(f"  Seen          : {int(latest.get('files_seen') or 0):,}")
    safe_print(f"  New           : {int(latest.get('new_files') or 0):,}")
    safe_print(f"  Changed       : {int(latest.get('changed_files') or 0):,}")
    safe_print(f"  Unchanged     : {int(latest.get('unchanged_files') or 0):,}")
    safe_print(f"  Missing       : {int(latest.get('missing_files') or 0):,}   "
               "(reported only - a missing file is never deleted from the index)")
    safe_print(f"  Skipped       : {int(latest.get('skipped_files') or 0):,}")

    safe_print()
    safe_print("WHAT WAS DONE")
    rule()
    safe_print(f"  Metadata      : {int(latest.get('metadata_added') or 0):,} added, "
               f"{int(latest.get('metadata_updated') or 0):,} updated")
    safe_print(f"  Extracted     : {int(latest.get('pdfs_extracted') or 0):,} "
               f"({int(latest.get('extraction_failed') or 0):,} failed)")
    safe_print(f"  Cards         : {int(latest.get('cards_created') or 0):,} "
               f"({int(latest.get('cards_failed') or 0):,} failed)")
    safe_print(f"  Obsidian      : {int(latest.get('obsidian_updated') or 0):,} note(s) added")
    safe_print(f"  Chunks        : {int(latest.get('chunks_created') or 0):,} created, "
               f"{int(latest.get('chunks_embedded') or 0):,} embedded")
    safe_print(f"  Embedding cost: ${float(latest.get('estimated_cost_usd') or 0):.6f}  "
               "(local estimate, not a bill)")
    safe_print(f"  Keyword index : {'rebuilt' if str(latest.get('keyword_updated')) == '1' else 'not updated'}")
    safe_print(f"  Smoke eval    : {latest.get('smoke_eval_status') or 'SKIPPED'}")
    if latest.get("report_path"):
        safe_print(f"  Report        : {latest.get('report_path')}")

    if args.latest:
        conn.close()
        return 0

    # ---- recovery -----------------------------------------------------
    extraction_failed = []
    card_failed = []
    if table_exists(conn, "ingestion_file_events"):
        rows = conn.execute(
            """
            SELECT e.document_id, e.relative_path, e.action_taken, e.error_message,
                   e.ingestion_run_id
              FROM ingestion_file_events e
              JOIN (SELECT document_id, MAX(event_id) AS newest
                      FROM ingestion_file_events
                     WHERE COALESCE(document_id,'') <> ''
                     GROUP BY document_id) latest
                ON latest.document_id = e.document_id AND latest.newest = e.event_id
             WHERE COALESCE(e.status,'') = 'error'
             ORDER BY e.event_id DESC
            """).fetchall()
        for row in rows:
            action = str(row["action_taken"] or "")
            if "extraction FAILED" in action:
                extraction_failed.append(dict(row))
            elif "card FAILED" in action:
                card_failed.append(dict(row))

    if extraction_failed or card_failed:
        heading("RECOVERY NEEDED")
        safe_print("Some files reached the index but not the pipeline behind it. Until this is")
        safe_print("cleared, the archive and the searchable text describe different things.")
        safe_print()
        safe_print(f"  Extraction failed : {len(extraction_failed):,}")
        safe_print(f"  Card failed       : {len(card_failed):,}")
        latest_stage = ("extraction" if extraction_failed else "card")
        safe_print(f"  Latest failed stage: {latest_stage}")
        safe_print()

        if extraction_failed:
            safe_print("  Affected documents (extraction):")
            for row in extraction_failed[:max(1, args.top)]:
                safe_print(f"    {row['document_id']}  {row['relative_path']}")
                safe_print(f"      {(row['error_message'] or '')[:120]}")
            if len(extraction_failed) > args.top:
                safe_print(f"    ... and {len(extraction_failed) - args.top} more.")
        if card_failed:
            safe_print("  Affected documents (card):")
            for row in card_failed[:max(1, args.top)]:
                safe_print(f"    {row['document_id']}  {row['relative_path']}")
            if len(card_failed) > args.top:
                safe_print(f"    ... and {len(card_failed) - args.top} more.")

        safe_print()
        safe_print("  See why, without retrying anything:")
        safe_print(f'    py -3.11 scripts\\run_incremental_ingestion.py --project-root "'
                   f'{project_root}" --db "{args.db}" --source-root "'
                   f'{latest.get("source_root") or "<archive>"}" --diagnose-failed')
        safe_print()
        safe_print("  Retry ONLY what failed (no archive walk, no metadata rewrite):")
        safe_print(f'    py -3.11 scripts\\run_incremental_ingestion.py --project-root "'
                   f'{project_root}" --db "{args.db}" --source-root "'
                   f'{latest.get("source_root") or "<archive>"}" '
                   "--retry-failed-extraction --retry-failed-cards --verbose")
        safe_print()
        safe_print("  DO NOT run --chunk-new or --embed-new until extraction and card failures")
        safe_print("  are resolved. Chunking now would index an incomplete set, and the")
        safe_print("  embedding cost would be spent on it.")

        logs = sorted((project_root / "10_Ingestion" / "runs").glob("*_failures.log"))
        if logs:
            safe_print()
            safe_print(f"  Complete stderr/stdout for every failure: {logs[-1].name}")

    # ---- recent failures --------------------------------------------------
    heading("RECENT FAILURES")
    failures = [dict(r) for r in conn.execute(
        "SELECT * FROM ingestion_file_events WHERE status = 'error' "
        "ORDER BY event_id DESC LIMIT ?", (max(1, args.top),))] \
        if table_exists(conn, "ingestion_file_events") else []
    if not failures:
        safe_print("  None. Every file the ingestion touched was processed without error.")
    else:
        for event in failures:
            safe_print(f"  run {event['ingestion_run_id']}  {event['change_status']}")
            safe_print(f"    {event['relative_path']}")
            safe_print(f"    {(event['error_message'] or '')[:150]}")
    total_failures = scalar(conn, "SELECT COUNT(*) FROM ingestion_file_events "
                                  "WHERE status = 'error'")
    if total_failures > len(failures):
        safe_print(f"  ... and {total_failures - len(failures)} more across earlier runs.")

    # ---- stale work -------------------------------------------------------
    heading("STALE WORK")
    safe_print("A file whose contents changed makes everything derived from it out of date.")
    safe_print("These counts are what is still waiting.")
    safe_print()
    stale = {
        "extraction": scalar(conn, "SELECT COUNT(*) FROM documents "
                                   "WHERE COALESCE(stale_extraction,'0') = '1'"),
        "card": scalar(conn, "SELECT COUNT(*) FROM documents "
                             "WHERE COALESCE(stale_document_card,'0') = '1'"),
        "rag": scalar(conn, "SELECT COUNT(*) FROM documents "
                            "WHERE COALESCE(stale_rag_index,'0') = '1'"),
    }
    safe_print(f"  Stale extraction    : {stale['extraction']:,}")
    safe_print(f"  Stale document card : {stale['card']:,}")
    safe_print(f"  Stale RAG index     : {stale['rag']:,}")

    missing_now = scalar(conn, "SELECT COUNT(*) FROM documents "
                               "WHERE COALESCE(file_change_status,'') = 'missing_file'") \
        if column_exists(conn, "documents", "file_change_status") else 0
    safe_print(f"  Marked missing      : {missing_now:,}   "
               "(rows kept on purpose - confirm the share was available)")

    # ---- Phase 9.2: WHY a stale RAG flag is set --------------------------
    # A count on its own is what created the loop: "10 documents are stale"
    # says nothing about whether anyone can do anything about them.
    INDEXED_TICKER = "MSFT"          # Phase 9 indexes one ticker. When that changes, so does this.

    def tagged_with(value: str, ticker: str) -> bool:
        """Whole-token ticker match.

        Phase 9.3. The SQL this replaced compared `',' || tickers || ','` to
        `'%,MSFT,%'`, which misses "AAPL, MSFT" - a comma followed by a space.
        """
        wanted = (ticker or "").strip().upper()
        return any(token.strip().upper() == wanted
                   for token in str(value or "").replace(";", ",").replace("|", ",").split(","))

    eligible_waiting = 0
    if stale["rag"]:
        safe_print()
        safe_print("  Why those RAG flags are set:")
        # Phase 9.3. This breakdown used to have NO TICKER FILTER: any stale
        # document with usable_for_rag = 1 was reported as "eligible, waiting to
        # be chunked", whatever ticker it belonged to. That is what kept telling
        # a reader to run the MSFT chunk command for documents the MSFT chunk
        # command can never touch.
        rows = [dict(r) for r in conn.execute(
            "SELECT document_id, COALESCE(relative_path,'') AS relative_path, "
            "COALESCE(ticker_candidates_clean,'') AS tickers, "
            "COALESCE(usable_for_rag,0) AS usable_for_rag, "
            "COALESCE(file_change_status,'') AS file_change_status, "
            "COALESCE(stale_extraction,'0') AS stale_extraction, "
            "COALESCE(stale_document_card,'0') AS stale_document_card, "
            "COALESCE(rag_skip_reason,'') AS rag_skip_reason "
            "FROM documents WHERE COALESCE(stale_rag_index,'0') = '1'")]

        tally = {}
        for row in rows:
            if row["file_change_status"] == "missing_file" \
                    or row["rag_skip_reason"].startswith("missing_source"):
                reason = "source file missing"
            elif row["stale_extraction"] == "1" or row["stale_document_card"] == "1" \
                    or row["rag_skip_reason"].startswith("rag_blocked_not_ready"):
                reason = "extraction or card not ready"
            elif not tagged_with(row["tickers"], INDEXED_TICKER):
                reason = (f"tagged {row['tickers'] or '(no ticker)'} - not indexed in this phase "
                          f"({INDEXED_TICKER} only)")
            elif int(row["usable_for_rag"] or 0) == 1:
                reason = "eligible, waiting to be chunked"
            else:
                reason = "not eligible - run --chunk-new to classify and clear"
            tally[reason] = tally.get(reason, 0) + 1

        for reason, how_many in sorted(tally.items(), key=lambda kv: -kv[1]):
            safe_print(f"    {how_many:>4}  {reason}")
        eligible_waiting = tally.get("eligible, waiting to be chunked", 0)

        other_ticker = sum(count for reason, count in tally.items()
                           if "not indexed in this phase" in reason)
        if other_ticker:
            safe_print()
            safe_print(f"  {other_ticker} of these belong to tickers nothing indexes yet. There "
                       "is no index")
            safe_print("  for them to be behind. Running --chunk-new clears them with that "
                       "recorded.")
    else:
        safe_print("  No document is waiting on the RAG index.")

    # ---- Phase 9.2: the missing files, by name ---------------------------
    if missing_now and column_exists(conn, "documents", "file_change_status"):
        safe_print()
        safe_print("MISSING SOURCE FILES")
        rule()
        rows = conn.execute(
            "SELECT document_id, relative_path, file_path, last_seen_at "
            "FROM documents WHERE COALESCE(file_change_status,'') = 'missing_file' "
            "ORDER BY relative_path LIMIT ?", (max(1, args.top),)).fetchall()
        for row in rows:
            safe_print(f"  {row['relative_path'] or row['file_path']}")
            safe_print(f"    {row['document_id']}   last seen {row['last_seen_at'] or 'unknown'}")
        if missing_now > len(rows):
            safe_print(f"  ... and {missing_now - len(rows)} more.")
        safe_print()
        safe_print("  Nothing was deleted, and nothing will be.")
        safe_print("  Confirm the V drive/share was fully available before treating these as")
        safe_print("  deleted. A share that was briefly unmounted looks exactly like this.")

    pending_chunks = scalar(conn, "SELECT COUNT(*) FROM rag_chunks "
                                  "WHERE embedding_status = 'pending'") \
        if table_exists(conn, "rag_chunks") else 0
    superseded = scalar(conn, "SELECT COUNT(*) FROM rag_chunks "
                              "WHERE embedding_status = 'superseded'") \
        if table_exists(conn, "rag_chunks") else 0
    embedded = scalar(conn, "SELECT COUNT(*) FROM rag_chunks "
                            "WHERE embedding_status = 'embedded'") \
        if table_exists(conn, "rag_chunks") else 0
    safe_print(f"  Chunks pending      : {pending_chunks:,}   (waiting for --embed-new)")
    safe_print(f"  Chunks superseded   : {superseded:,}   (kept for audit, not retrievable)")
    safe_print(f"  Chunks embedded     : {embedded:,}")

    # ---- is the system current -------------------------------------------
    heading("IS THE SYSTEM CURRENT?")
    last_live = None
    for run in conn.execute(
            "SELECT * FROM ingestion_runs WHERE COALESCE(dry_run,'0') = '0' "
            "AND status = 'completed' ORDER BY ingestion_run_id DESC LIMIT 1"):
        last_live = dict(run)

    if last_live is None:
        safe_print("  NO. No live ingestion has completed - only dry runs, or none at all.")
        safe_print("  The index describes the archive as it was when Phase 1 last scanned it.")
    else:
        safe_print(f"  Last completed live ingestion: run {last_live['ingestion_run_id']}, "
                   f"{age_phrase(last_live.get('completed_at') or '')}")
        actionable = (stale["extraction"] or stale["card"] or eligible_waiting
                      or pending_chunks)
        if not actionable and stale["rag"]:
            safe_print("  YES, as far as anything can be done. Some documents still carry a "
                       "stale RAG flag, but none of them is work anyone can complete:")
            safe_print("  a missing source file, or an extraction that has to be repaired first.")
            safe_print("  See the breakdown above. Re-running the chunk command would change "
                       "nothing.")
        elif not actionable:
            safe_print("  YES. Nothing is marked stale and no chunk is waiting to be embedded.")
        else:
            safe_print("  NOT QUITE. Something changed and the work downstream of it is not "
                       "finished:")
            if stale["extraction"]:
                safe_print(f"    - {stale['extraction']:,} document(s) need re-extraction")
            if stale["card"]:
                safe_print(f"    - {stale['card']:,} document(s) need a rebuilt card")
            if eligible_waiting:
                safe_print(f"    - {eligible_waiting:,} RAG-eligible document(s) need chunking")
            if pending_chunks:
                safe_print(f"    - {pending_chunks:,} chunk(s) are chunked but not embedded")

    safe_print()
    safe_print("MSFT RETRIEVAL")
    rule()
    keyword_index = project_root / "06_RAG_Index" / "keyword" / "MSFT_keyword_index.json"
    chroma_store = project_root / "06_RAG_Index" / "chroma" / "MSFT"
    msft_embedded = scalar(conn, "SELECT COUNT(*) FROM rag_chunks WHERE ticker = 'MSFT' "
                                 "AND embedding_status = 'embedded'") \
        if table_exists(conn, "rag_chunks") else 0
    msft_pending = scalar(conn, "SELECT COUNT(*) FROM rag_chunks WHERE ticker = 'MSFT' "
                                "AND embedding_status = 'pending'") \
        if table_exists(conn, "rag_chunks") else 0
    safe_print(f"  Vector store   : {'present' if chroma_store.exists() else 'MISSING'}")
    safe_print(f"  Keyword index  : {'present' if keyword_index.exists() else 'MISSING'}")
    safe_print(f"  Embedded chunks: {msft_embedded:,}")
    if msft_pending:
        safe_print(f"  PENDING chunks : {msft_pending:,} - the vector index is behind the text.")
    if stale["rag"] and not eligible_waiting:
        safe_print(f"  Stale flags    : {stale['rag']:,}, none of them chunkable - see the "
                   "breakdown above.")
    keyword_age = ""
    if keyword_index.exists():
        keyword_age = datetime.fromtimestamp(
            keyword_index.stat().st_mtime).replace(microsecond=0).isoformat()
        safe_print(f"  Keyword built  : {keyword_age} ({age_phrase(keyword_age)})")
    if msft_pending:
        safe_print("  The BM25 index and the vector index must be rebuilt together, or hybrid "
                   "retrieval searches two different corpora.")
    if extraction_failed or card_failed:
        safe_print("  Do not run --chunk-new/--embed-new until extraction/card failures are "
                   "resolved.")

    # ---- next command -----------------------------------------------------
    heading("NEXT RECOMMENDED COMMAND")
    root = f'"{project_root}"'
    db = f'"{args.db}"'
    base = (f'py -3.11 scripts\\run_incremental_ingestion.py --project-root {root} --db {db}')

    if extraction_failed or card_failed:
        safe_print("  Repair first. Nothing else should run until this is clear:")
        safe_print(f"    {base} --source-root \"{latest.get('source_root') or '<archive>'}\" "
                   "--retry-failed-extraction --retry-failed-cards --verbose")
    elif last_live is None:
        safe_print("  See what has changed, without changing anything:")
        safe_print(f"    {base} --dry-run --verbose")
    elif stale["extraction"] or stale["card"]:
        safe_print("  Finish the text and card work that a changed file made stale:")
        safe_print(f"    {base} --process-changed --extract-text --build-cards "
                   "--update-obsidian --verbose")
    elif eligible_waiting or msft_pending:
        safe_print("  Bring the MSFT index up to date. This one spends money on embeddings:")
        safe_print(f"    {base} --ticker MSFT --chunk-new --embed-new --update-keyword "
                   "--run-smoke-eval --verbose")
    elif stale["rag"]:
        # Phase 9.2/9.3. Flags are set, but nothing the chunk command can act on
        # - either they are held for repair, or they belong to a ticker with no
        # index. Recommending the chunk command here is what produced the loop.
        safe_print("  Nothing can be chunked: every remaining stale flag is a missing source,")
        safe_print("  work that has to be repaired first, or a ticker nothing indexes yet.")
        safe_print("  To classify them all and clear the ones RAG will never index:")
        safe_print(f"    {base} --source-root \"{latest.get('source_root') or '<archive>'}\" "
                   "--ticker MSFT --clear-noneligible-stale-rag --verbose")
    else:
        safe_print("  Nothing is outstanding. The usual daily check:")
        safe_print(f"    {base} --dry-run")

    safe_print()
    safe_print("  Read-only inspectors, any time:")
    safe_print(f"    py -3.11 scripts\\inspect_rag_index.py --project-root {root} --db {db} "
               "--ticker MSFT")
    safe_print(f"    py -3.11 scripts\\inspect_evaluation.py --project-root {root} --db {db} "
               "--ticker MSFT")

    rule("=")
    safe_print("Counts describe what the pipeline did, not whether the documents are any good.")
    safe_print("AI drafts. Humans verify. Cutler decides.")
    rule("=")

    conn.close()
    if PRINT_FAILURES:
        safe_print(f"({PRINT_FAILURES} line(s) could not be displayed by this terminal.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
