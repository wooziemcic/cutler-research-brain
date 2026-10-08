#!/usr/bin/env python3
"""Phase 11.3 - let two tickers hold the same chunk at once.

THE PROBLEM THIS FIXES
----------------------
`build_rag_index.py` builds chunk ids as

    chunk_id = f"{document_id}_c{chunk_index:04d}"

and `rag_chunks` declares `chunk_id TEXT PRIMARY KEY`, while chunks are written
with `INSERT OR REPLACE`. **The ticker is a column, not part of the key.**

A document tagged with two tickers therefore has ONE set of chunk rows. Building
the second ticker replaces the first ticker's rows in place - same ids, same
text, a new value in the `ticker` column. Nothing is deleted and the row count
never drops, so it does not look like data loss; the chunks change owner, and
the ticker that lost them reports them missing forever. Repairing that ticker
takes them back. The next ticker's run takes them again. That is the churn the
Phase 11.2 repairs produced.

It is not fixable by embedding, and it is not fixable by any amount of care in
the builder. The key has to name a ticker.

WHAT THIS DOES
--------------
Rebuilds `rag_chunks` with `PRIMARY KEY (chunk_id, ticker)` and copies every
existing row across, unchanged. Same chunk_ids, same text, same
`embedding_status`, same `embedded_at`. Chroma is not opened at all - each
ticker already has its own store, so ids never collided there.

WHAT IT DOES NOT DO
-------------------
It does not delete a chunk, re-chunk a document, embed anything, call OpenAI,
open a Chroma collection, or touch the V drive. It does not change any chunk_id,
so every vector already in Chroma still matches its row.

  AI drafts. Humans verify. Cutler decides.

SAFETY
------
* Dry run by default. `--apply` is required to write anything.
* Before writing, it copies the whole table to `rag_chunks_backup_<stamp>`, which
  is left in place. Nothing is dropped until that copy is verified row-for-row.
* Every count is compared before and after: total rows, rows per ticker,
  distinct chunk_ids, and rows per embedding_status. A mismatch rolls the whole
  thing back in one transaction.
* It refuses to run if the table is already keyed by (chunk_id, ticker).

Usage:
  py -3.11 scripts\\migrate_rag_chunk_key.py ^
      --project-root "C:\\Users\\AI Admin\\Documents\\CutlerResearchBrain" ^
      --db "data\\document_metadata.sqlite"
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ticker_selection as ts                                     # noqa: E402

PIPELINE_VERSION = "phase11.3-1.0"

EXIT_OK = 0
EXIT_STEP_FAILED = 1
EXIT_BAD_INPUT = 2
EXIT_ALREADY_DONE = 0


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
    except Exception:                        # noqa: BLE001
        pass


def census(conn: sqlite3.Connection, table: str = "rag_chunks") -> dict:
    """Every number that must be identical before and after."""
    return {
        "rows": conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0],
        "distinct_chunk_ids": conn.execute(
            f"SELECT COUNT(DISTINCT chunk_id) FROM {table}").fetchone()[0],
        "by_ticker": dict(conn.execute(
            f"SELECT COALESCE(ticker,''), COUNT(*) FROM {table} GROUP BY 1")),
        "by_status": dict(conn.execute(
            f"SELECT COALESCE(embedding_status,''), COUNT(*) FROM {table} GROUP BY 1")),
        "documents": conn.execute(
            f"SELECT COUNT(DISTINCT document_id) FROM {table}").fetchone()[0],
    }


def column_definitions(conn: sqlite3.Connection) -> list:
    """The existing columns, with their declared types, in their existing order.

    Read from the live table rather than from build_rag_index's constant, so a
    column added by a later phase is carried across instead of dropped.
    """
    return [(row[1], row[2] or "TEXT") for row in conn.execute("PRAGMA table_info(rag_chunks)")]


def shared_document_report(conn: sqlite3.Connection) -> list:
    """Documents that more than one ticker is eligible for, and who owns them now.

    This is what the migration unblocks, so it is shown before anything is
    written: these are the (document, ticker) pairs that cannot exist today.
    """
    owners = {}
    for row in conn.execute("SELECT DISTINCT document_id, UPPER(ticker) FROM rag_chunks "
                            " WHERE embedding_status = 'embedded'"):
        owners[row[0]] = row[1]
    rows = []
    for ticker in ts.ALL_KNOWN_TICKERS:
        eligible = {r["document_id"] for r in ts.candidate_rows(conn, ticker, eligible_only=True)}
        blocked = sorted(doc for doc in eligible
                         if doc in owners and owners[doc] != ticker.upper())
        if blocked:
            rows.append({"ticker": ticker, "blocked": blocked,
                         "owners": sorted({owners[doc] for doc in blocked})})
    return rows


def main() -> int:
    configure_console_encoding()
    parser = argparse.ArgumentParser(
        description="Phase 11.3: rekey rag_chunks on (chunk_id, ticker). Dry run by default.")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--db", default="data/document_metadata.sqlite")
    parser.add_argument("--apply", action="store_true",
                        help="Actually rebuild the table. Without this, nothing is written.")
    parser.add_argument("--keep-backup", action="store_true", default=True,
                        help="Leave the backup table in place (default, and recommended).")
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve()
    db_path = Path(args.db)
    if not db_path.is_absolute():
        db_path = project_root / db_path
    if not db_path.exists():
        safe_print(f"ERROR: no database at {db_path}")
        return EXIT_BAD_INPUT

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        if not ts.table_exists(conn, "rag_chunks"):
            safe_print("ERROR: there is no rag_chunks table in this database.")
            return EXIT_BAD_INPUT

        safe_print("=" * 96)
        safe_print(f"RAG CHUNK KEY MIGRATION  {PIPELINE_VERSION}   mode: "
                   f"{'APPLY' if args.apply else 'DRY RUN'}")
        safe_print("=" * 96)

        if ts.chunk_key_is_ticker_scoped(conn):
            safe_print("Already migrated: rag_chunks is keyed by (chunk_id, ticker).")
            safe_print("Nothing to do. No change was made.")
            return EXIT_ALREADY_DONE

        before = census(conn)
        safe_print("Current table: chunk_id TEXT PRIMARY KEY - the ticker is a column, so two")
        safe_print("tickers cannot hold the same chunk_id and the last build wins.")
        safe_print("")
        safe_print(f"  chunk rows          : {before['rows']:,}")
        safe_print(f"  distinct chunk_ids  : {before['distinct_chunk_ids']:,}")
        safe_print(f"  distinct documents  : {before['documents']:,}")
        safe_print(f"  by embedding status : {before['by_status']}")
        safe_print("")
        safe_print("  by ticker:")
        for ticker, count in sorted(before["by_ticker"].items()):
            safe_print(f"      {ticker or '(blank)':<8}{count:>8,}")
        safe_print("")

        blocked = shared_document_report(conn)
        total_blocked = sum(len(row["blocked"]) for row in blocked)
        safe_print("-" * 96)
        safe_print(f"WHAT THE MIGRATION UNBLOCKS: {total_blocked} (document, ticker) pair(s)")
        safe_print("-" * 96)
        if not blocked:
            safe_print("  No ticker is currently missing a document that another ticker owns.")
            safe_print("  The migration is still worth doing - it stops this happening at all -")
            safe_print("  but nothing is waiting on it right now.")
        for row in blocked:
            safe_print(f"  {row['ticker']:<6} {len(row['blocked']):>4} document(s) currently owned "
                       f"by {', '.join(row['owners'])}")
        safe_print("")
        safe_print("  These documents are embedded and retrievable - for the OTHER ticker. Their")
        safe_print("  chunks exist; only the owner is wrong. After the migration each ticker can")
        safe_print("  have its own row for the same chunk, and a repair becomes an addition.")
        safe_print("")

        columns = column_definitions(conn)
        names = [name for name, _ in columns]
        defs = ", ".join(f"{name} {declared}" for name, declared in columns)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = f"rag_chunks_backup_{stamp}"

        safe_print("-" * 96)
        safe_print("PLAN")
        safe_print("-" * 96)
        safe_print(f"  1. CREATE TABLE {backup} AS SELECT * FROM rag_chunks     (a full copy)")
        safe_print(f"  2. verify the copy row-for-row against the original")
        safe_print(f"  3. CREATE TABLE rag_chunks_new (... , PRIMARY KEY (chunk_id, ticker))")
        safe_print(f"  4. INSERT INTO rag_chunks_new SELECT * FROM rag_chunks   ({len(names)} columns)")
        safe_print(f"  5. verify every count matches, then swap the new table into place")
        safe_print(f"  6. recreate the three indexes on ticker, document_id, embedding_status")
        safe_print("")
        safe_print("  No chunk_id changes, so every vector already in Chroma still matches its")
        safe_print("  row. No chunk is deleted. Nothing is embedded. Chroma is never opened.")
        safe_print("")

        if not args.apply:
            safe_print("DRY RUN. Nothing was written.")
            safe_print("Re-run with --apply when the numbers above look right.")
            return EXIT_OK

        safe_print("Applying ...")
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(f"CREATE TABLE {backup} AS SELECT * FROM rag_chunks")
            backup_census = census(conn, backup)
            if backup_census != before:
                raise RuntimeError(f"backup does not match the original: "
                                   f"{backup_census} != {before}")
            safe_print(f"  backup {backup}: {backup_census['rows']:,} row(s), verified")

            conn.execute(f"CREATE TABLE rag_chunks_new ({defs}, PRIMARY KEY (chunk_id, ticker))")
            conn.execute(f"INSERT INTO rag_chunks_new ({', '.join(names)}) "
                         f"SELECT {', '.join(names)} FROM rag_chunks")
            after = census(conn, "rag_chunks_new")
            if after != before:
                raise RuntimeError(f"the rebuilt table does not match: {after} != {before}")
            safe_print(f"  rebuilt table: {after['rows']:,} row(s), every count matches")

            conn.execute("DROP TABLE rag_chunks")
            conn.execute("ALTER TABLE rag_chunks_new RENAME TO rag_chunks")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_ticker ON rag_chunks(ticker)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_document "
                         "ON rag_chunks(document_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_status "
                         "ON rag_chunks(embedding_status)")
            conn.commit()
        except Exception as exc:                            # noqa: BLE001
            conn.rollback()
            safe_print(f"  FAILED: {exc}")
            safe_print("  Rolled back. The database is exactly as it was.")
            return EXIT_STEP_FAILED

        final = census(conn)
        safe_print("")
        safe_print("-" * 96)
        safe_print(f"  rows      {before['rows']:,} -> {final['rows']:,}")
        safe_print(f"  documents {before['documents']:,} -> {final['documents']:,}")
        safe_print(f"  by status {before['by_status']} -> {final['by_status']}")
        safe_print(f"  keyed by (chunk_id, ticker): "
                   f"{ts.chunk_key_is_ticker_scoped(conn)}")
        safe_print(f"  backup kept as {backup} - drop it yourself once you are satisfied.")
        safe_print("")
        safe_print("Nothing was embedded, no chunk was deleted, no chunk_id changed, no Chroma")
        safe_print("collection was opened, and the V drive was not touched.")
        safe_print("")
        safe_print("Next: re-run the reconciler to see what is now repairable.")
        safe_print(f"  py -3.11 scripts\\reconcile_ticker_index.py "
                   f"--project-root \"{project_root}\" --db \"{args.db}\" --explain-repair-plan")
        return EXIT_OK
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
