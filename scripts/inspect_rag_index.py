#!/usr/bin/env python3
# =============================================================================
# Cutler Research Brain - Phase 5A
# inspect_rag_index.py
#
# Read-only console report over the rag_chunks and embedding_runs tables, so a
# human can see what was chunked and embedded before anything is built on top.
#
# Prints: ticker, documents selected, chunks created / embedded / skipped, the
# embedding model, token and cost estimates, the Chroma collection name, source
# and document type counts, date coverage, the largest documents by chunk count,
# failed embeddings and sample chunks.
#
# Reads only. It makes no API call of any kind, writes nothing, and neither
# summarizes nor interprets any document.
#
# Usage:
#   python scripts\inspect_rag_index.py --db "data\document_metadata.sqlite" ^
#       --project-root "C:\Users\AI Admin\Documents\CutlerResearchBrain" ^
#       --ticker MSFT
# =============================================================================

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Console output (Phase 7A.1)
# A Windows console defaults to a legacy code page - usually cp1252 - which
# cannot encode characters that appear in archive text and in model answers
# (the non-breaking hyphen U+2011, curly quotes, dashes). Without this, a
# report could die halfway through printing. Ask for UTF-8 up front; anything
# still unprintable is replaced rather than raised.
# ---------------------------------------------------------------------------

def configure_console_encoding() -> None:
    for _name in ("stdout", "stderr"):
        _stream = getattr(sys, _name, None)
        if _stream is None or not hasattr(_stream, "reconfigure"):
            continue
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError, AttributeError):
            pass


configure_console_encoding()


TOP_N = 20
PREVIEW_CHARS = 180


def open_readonly(db_path: Path) -> sqlite3.Connection:
    try:
        conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    except sqlite3.OperationalError:
        conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def query(conn: sqlite3.Connection, sql: str, params=()) -> list[tuple]:
    return [tuple(r) for r in conn.execute(sql, params)]


def scalar(conn: sqlite3.Connection, sql: str, params=()):
    row = conn.execute(sql, params).fetchone()
    return row[0] if row and row[0] is not None else 0


def print_header(title: str) -> None:
    print()
    print(title)
    print("-" * max(len(title), 46))


def line(label: str, value, width: int = 34) -> None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        print(f"  {label:<{width}} {value:>12,}")
    else:
        print(f"  {label:<{width}} {str(value):>12}")


def print_counts(rows, label_width: int = 30) -> None:
    if not rows:
        print("  (none)")
        return
    total = sum(int(r[1] or 0) for r in rows) or 1
    for label, count in rows:
        text = str(label) if str(label).strip() else "(none)"
        print(f"  {text:<{label_width}} {int(count):>8,}  {int(count) / total * 100:>5.1f}%")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Cutler Research Brain Phase 5A - inspect the RAG chunk/embedding index.",
    )
    parser.add_argument("--db", required=True,
                        help=r'SQLite index, e.g. "data\document_metadata.sqlite"')
    parser.add_argument("--project-root", default="",
                        help=r'Project root, e.g. "C:\Users\AI Admin\Documents\CutlerResearchBrain"')
    parser.add_argument("--ticker", default="MSFT", help="Ticker to inspect (default MSFT).")
    parser.add_argument("--top", type=int, default=TOP_N,
                        help=f"How many rows in the sample listings (default {TOP_N}).")
    args = parser.parse_args(argv)

    ticker = args.ticker.strip().upper()
    project_root = Path(args.project_root).expanduser().resolve() if args.project_root else None

    db_path = Path(args.db).expanduser()
    if not db_path.is_absolute() and project_root:
        db_path = (project_root / db_path).resolve()
    if not db_path.exists():
        print(f"ERROR: database not found: {db_path}", file=sys.stderr)
        return 1

    conn = open_readonly(db_path)
    try:
        tables = {r[0] for r in query(conn, "SELECT name FROM sqlite_master WHERE type='table'")}
        if "rag_chunks" not in tables:
            print("ERROR: no 'rag_chunks' table in this database.", file=sys.stderr)
            print("Run scripts/build_rag_index.py first.", file=sys.stderr)
            return 1

        chunks = scalar(conn, "SELECT COUNT(*) FROM rag_chunks WHERE ticker = ?", (ticker,))

        print("=" * 78)
        print(f"CUTLER RESEARCH BRAIN - PHASE 5A RAG INDEX ({ticker})")
        print(f"Database: {db_path.resolve()}")
        print("=" * 78)

        if chunks == 0:
            print(f"\nNo chunks for {ticker} yet. Run scripts/build_rag_index.py --ticker {ticker}.")
            return 0

        embedded = scalar(conn, "SELECT COUNT(*) FROM rag_chunks WHERE ticker = ? "
                                "AND embedding_status = 'embedded'", (ticker,))
        failed = scalar(conn, "SELECT COUNT(*) FROM rag_chunks WHERE ticker = ? "
                              "AND embedding_status = 'failed'", (ticker,))
        pending = scalar(conn, "SELECT COUNT(*) FROM rag_chunks WHERE ticker = ? "
                               "AND embedding_status IN ('pending','dry_run')", (ticker,))
        documents = scalar(conn, "SELECT COUNT(DISTINCT document_id) FROM rag_chunks "
                                 "WHERE ticker = ?", (ticker,))
        tokens = scalar(conn, "SELECT COALESCE(SUM(chunk_token_count),0) FROM rag_chunks "
                              "WHERE ticker = ?", (ticker,))
        chars = scalar(conn, "SELECT COALESCE(SUM(chunk_char_count),0) FROM rag_chunks "
                             "WHERE ticker = ?", (ticker,))
        model = scalar(conn, "SELECT embedding_model FROM rag_chunks WHERE ticker = ? "
                             "ORDER BY created_at DESC LIMIT 1", (ticker,)) or "(none)"
        collection = scalar(conn, "SELECT chroma_collection FROM rag_chunks WHERE ticker = ? "
                                  "ORDER BY created_at DESC LIMIT 1", (ticker,)) or "(none)"

        print_header("OVERVIEW")
        line("Ticker", ticker)
        line("Documents in the index", documents)
        line("Chunks created", chunks)
        line("Chunks EMBEDDED", embedded)
        line("Chunks pending / dry run", pending)
        line("Chunks failed", failed)
        line("Total tokens (chunked)", tokens)
        line("Total characters (chunked)", chars)
        print()
        line("Embedding model", model)
        line("Chroma collection", collection)
        if project_root:
            store = project_root / "06_RAG_Index" / "chroma" / ticker
            line("Chroma store on disk", "yes" if store.exists() else "NOT FOUND")

        # ---- runs -------------------------------------------------------
        if "embedding_runs" in tables:
            runs = query(conn, """
                SELECT run_id, started_at, status, documents_selected, chunks_created,
                       chunks_embedded, chunks_skipped, total_input_tokens_estimate,
                       openai_calls, estimated_cost_usd
                  FROM embedding_runs WHERE ticker = ? ORDER BY run_id DESC LIMIT 5
            """, (ticker,))
            if runs:
                print_header("RECENT EMBEDDING RUNS")
                for (run_id, started, status, docs, created, emb, skipped,
                     tok, calls, cost) in runs:
                    print(f"  #{run_id:<4} {started}  {str(status):<10} "
                          f"docs {int(docs or 0):>4}  chunks {int(created or 0):>6}  "
                          f"embedded {int(emb or 0):>6}  skipped {int(skipped or 0):>5}")
                    print(f"        tokens {int(tok or 0):>10,}  calls {int(calls or 0):>4}  "
                          f"estimated cost ${float(cost or 0):.4f}")
                total_cost = scalar(conn, "SELECT COALESCE(SUM(estimated_cost_usd),0) "
                                          "FROM embedding_runs WHERE ticker = ?", (ticker,))
                total_calls = scalar(conn, "SELECT COALESCE(SUM(openai_calls),0) "
                                           "FROM embedding_runs WHERE ticker = ?", (ticker,))
                print(f"\n  Cumulative: {int(total_calls):,} embedding call(s), "
                      f"estimated ${float(total_cost):.4f} total.")
                print("  Cost is an estimate from token counts, not a bill.")

        # ---- breakdowns ---------------------------------------------------
        print_header("CHUNKS BY SOURCE TYPE GUESS")
        print_counts(query(conn, "SELECT source_type_guess, COUNT(*) FROM rag_chunks "
                                 "WHERE ticker = ? GROUP BY 1 ORDER BY COUNT(*) DESC", (ticker,)))

        print_header("CHUNKS BY DOCUMENT TYPE GUESS")
        print_counts(query(conn, "SELECT document_type_guess, COUNT(*) FROM rag_chunks "
                                 "WHERE ticker = ? GROUP BY 1 ORDER BY COUNT(*) DESC", (ticker,)))

        print_header("CHUNKS BY STRATEGY THEME GUESS")
        print_counts(query(conn, "SELECT strategy_theme_guess, COUNT(*) FROM rag_chunks "
                                 "WHERE ticker = ? GROUP BY 1 ORDER BY COUNT(*) DESC", (ticker,)))

        print_header("DATE COVERAGE")
        rows = query(conn, """
            SELECT CASE WHEN COALESCE(folder_date_guess,'') = '' THEN '(no date)'
                        ELSE folder_date_guess END AS d,
                   COUNT(DISTINCT document_id), COUNT(*)
              FROM rag_chunks WHERE ticker = ? GROUP BY d ORDER BY d DESC LIMIT ?
        """, (ticker, args.top))
        if rows:
            for date, docs, chunk_count in rows:
                print(f"  {str(date):<14} {int(docs):>4} documents   {int(chunk_count):>6} chunks")
            first = scalar(conn, "SELECT MIN(folder_date_guess) FROM rag_chunks "
                                 "WHERE ticker = ? AND COALESCE(folder_date_guess,'') <> ''",
                           (ticker,))
            last = scalar(conn, "SELECT MAX(folder_date_guess) FROM rag_chunks "
                                "WHERE ticker = ? AND COALESCE(folder_date_guess,'') <> ''",
                          (ticker,))
            if first:
                print(f"\n  Date range: {first} to {last}")
        else:
            print("  (none)")

        print_header(f"TOP {args.top} DOCUMENTS BY CHUNK COUNT")
        rows = query(conn, """
            SELECT file_name, COUNT(*), SUM(chunk_token_count), folder_date_guess
              FROM rag_chunks WHERE ticker = ?
             GROUP BY document_id ORDER BY COUNT(*) DESC LIMIT ?
        """, (ticker, args.top))
        for file_name, count, tok, date in rows or []:
            print(f"  {int(count):>4} chunks  {int(tok or 0):>8,} tokens  "
                  f"{str(date or ''):<12} {str(file_name)[:44]}")
        if not rows:
            print("  (none)")

        # ---- failures ------------------------------------------------------
        print_header("FAILED EMBEDDINGS")
        if failed:
            rows = query(conn, """
                SELECT chunk_id, file_name, chunk_index, chunk_token_count
                  FROM rag_chunks WHERE ticker = ? AND embedding_status = 'failed'
                 ORDER BY document_id, chunk_index LIMIT ?
            """, (ticker, args.top))
            for chunk_id, file_name, index, tok in rows:
                print(f"  {chunk_id:<26} #{int(index):<4} {int(tok or 0):>6} tok  "
                      f"{str(file_name)[:36]}")
            print(f"\n  {failed:,} failed chunk(s). Re-run build_rag_index.py to retry them,")
            print("  and see 06_RAG_Index/reports/ for the full failure list.")
        else:
            print("  (none)")

        # ---- samples --------------------------------------------------------
        print_header(f"SAMPLE OF {min(args.top, 5)} CHUNKS")
        rows = query(conn, """
            SELECT chunk_id, file_name, folder_date_guess, page_start_guess, page_end_guess,
                   chunk_token_count, embedding_status, chunk_text
              FROM rag_chunks WHERE ticker = ? ORDER BY document_id, chunk_index LIMIT ?
        """, (ticker, min(args.top, 5)))
        for (chunk_id, file_name, date, page_start, page_end, tok, status, text) in rows or []:
            pages = f"p{page_start}-{page_end}" if page_start is not None else "no page info"
            preview = " ".join(str(text or "").split())[:PREVIEW_CHARS]
            print(f"\n  {chunk_id}  [{status}]  {int(tok or 0)} tokens  {pages}")
            print(f"    {str(file_name)[:64]}   {str(date or '')}")
            print(f"    \"{preview}...\"")
        if not rows:
            print("  (none)")

        print()
        print("=" * 78)
        print("Chunks are verbatim slices of already-extracted text. Nothing here was")
        print("summarized or interpreted, no question was answered, and the only API this")
        print("phase uses is OpenAI embeddings. Vectors are stored locally in the project")
        print("folder. AI drafts. Humans verify. Cutler decides.")
        print("=" * 78)
    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
