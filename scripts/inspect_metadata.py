#!/usr/bin/env python3
# =============================================================================
# Cutler Research Brain - Phase 1.2
# inspect_metadata.py
#
# Read-only console report over data/document_metadata.sqlite so a human can
# sanity-check what the scan actually found before anything downstream is built.
#
# Prints:
#   - total files indexed
#   - count by extension
#   - count by archive year          (Phase 1.1)
#   - count by detected year
#   - count by detected month
#   - filename year mention counts   (Phase 1.1)
#   - possible maturity year counts  (Phase 1.1)
#   - count by source type guess
#   - count by document type guess
#   - ticker confidence counts       (Phase 1.1)
#   - date confidence counts         (Phase 1.2)
#   - where detected_year came from  (Phase 1.2)
#   - remaining future detected years, which should be zero (Phase 1.2)
#   - sensitive context counts       (Phase 1.2)
#   - top excluded / downgraded ticker noise tokens (Phase 1.2)
#   - top 25 clean ticker candidates
#   - top source/vendor candidates   (Phase 1.1)
#   - duplicate count
#   - needs review count, review severity counts and top review categories
#
# Usage:
#   python scripts\inspect_metadata.py --db "data\document_metadata.sqlite"
#
# This script only reads. It never writes to the database or the V Drive.
# Principle: AI drafts. Humans verify. Cutler decides.
# =============================================================================

from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime
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


TOP_TICKERS = 25
TOP_VENDORS = 20


def open_readonly(db_path: Path) -> sqlite3.Connection:
    """Open the SQLite file in read-only mode (falls back if the URI form fails)."""
    try:
        conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    except sqlite3.OperationalError:
        conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def print_header(title: str) -> None:
    print()
    print(title)
    print("-" * max(len(title), 40))


def print_counts(rows, label_width: int = 34, empty_label: str = "(none)") -> None:
    if not rows:
        print("  (no rows)")
        return
    total = sum(int(r[1] or 0) for r in rows)
    for label, count in rows:
        text = str(label) if str(label).strip() else empty_label
        pct = (int(count) / total * 100) if total else 0.0
        print(f"  {text:<{label_width}} {int(count):>8,}  {pct:>5.1f}%")


def query(conn: sqlite3.Connection, sql: str, params=()) -> list[tuple]:
    cur = conn.cursor()
    cur.execute(sql, params)
    return [tuple(r) for r in cur.fetchall()]


def token_counts(conn: sqlite3.Connection, column: str) -> dict[str, int]:
    """Count comma-separated tokens stored in one column."""
    counts: dict[str, int] = {}
    for (value,) in query(conn, f"SELECT {column} FROM documents WHERE COALESCE({column}, '') <> ''"):
        for token in str(value).split(","):
            token = token.strip()
            if token:
                counts[token] = counts.get(token, 0) + 1
    return counts


def print_token_counts(counts: dict[str, int], limit: int, width: int = 14) -> None:
    if not counts:
        print("  (none)")
        return
    for token, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]:
        print(f"  {token:<{width}} {count:>8,}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Cutler Research Brain Phase 1.2 - inspect the metadata index.",
    )
    parser.add_argument(
        "--db", required=True,
        help=r'Path to the SQLite database, e.g. "data\document_metadata.sqlite"',
    )
    parser.add_argument(
        "--top-tickers", type=int, default=TOP_TICKERS,
        help=f"How many ticker candidates to show (default {TOP_TICKERS}).",
    )
    args = parser.parse_args(argv)

    db_path = Path(args.db).expanduser()
    if not db_path.exists():
        print(f"ERROR: database not found: {db_path}", file=sys.stderr)
        print("Run scripts/scan_source_archive.py first.", file=sys.stderr)
        return 1

    conn = open_readonly(db_path)
    try:
        tables = {r[0] for r in query(conn, "SELECT name FROM sqlite_master WHERE type='table'")}
        if "documents" not in tables:
            print(f"ERROR: no 'documents' table in {db_path}", file=sys.stderr)
            return 1

        columns = {r[1] for r in query(conn, "PRAGMA table_info(documents)")}
        phase_11 = "review_severity" in columns and "ticker_candidates_clean" in columns
        phase_12 = phase_11 and "date_confidence" in columns and "sensitive_context_guess" in columns
        clean_col = "ticker_candidates_clean" if phase_11 else "ticker_candidates"

        total = query(conn, "SELECT COUNT(*) FROM documents")[0][0]

        print("=" * 78)
        print("CUTLER RESEARCH BRAIN - PHASE 1.2 METADATA INDEX")
        print(f"Database: {db_path.resolve()}")
        print("=" * 78)
        print()
        print(f"TOTAL FILES INDEXED: {total:,}")

        if total == 0:
            print("\nThe index is empty. Nothing else to report.")
            return 0

        if not phase_11:
            print("\nNOTE: this database predates Phase 1.1. Re-run scan_source_archive.py to")
            print("      populate the new year / ticker / review columns.")

        total_bytes = query(conn, "SELECT COALESCE(SUM(file_size_bytes), 0) FROM documents")[0][0]
        print(f"TOTAL BYTES CATALOGUED: {int(total_bytes):,} "
              f"({int(total_bytes) / (1024 ** 3):.2f} GB)")

        # ---- most recent scans -------------------------------------------
        if "scan_runs" in tables:
            runs = query(
                conn,
                "SELECT processed_at, source_root, files_indexed FROM scan_runs "
                "ORDER BY run_id DESC LIMIT 3",
            )
            if runs:
                print_header("MOST RECENT SCANS")
                for processed_at, source_root, files_indexed in runs:
                    print(f"  {processed_at}  {int(files_indexed or 0):>7,} files  {source_root}")

        # ---- extension ----------------------------------------------------
        print_header("COUNT BY FILE EXTENSION")
        print_counts(query(conn, "SELECT file_extension, COUNT(*) FROM documents "
                                 "GROUP BY file_extension ORDER BY COUNT(*) DESC"))

        # ---- archive year (Phase 1.1) --------------------------------------
        if phase_11:
            print_header("COUNT BY ARCHIVE YEAR (from the scan path)")
            print_counts(query(conn, "SELECT COALESCE(archive_year,''), COUNT(*) FROM documents "
                                     "GROUP BY 1 ORDER BY 1"),
                         empty_label="(no archive year in path)")

        # ---- detected year -------------------------------------------------
        print_header("COUNT BY DETECTED YEAR (folder / archive year, not filename noise)")
        print_counts(query(conn, "SELECT COALESCE(detected_year,''), COUNT(*) FROM documents "
                                 "GROUP BY 1 ORDER BY 1"),
                     empty_label="(no year detected)")

        # ---- date confidence + future-year check (Phase 1.2) ---------------
        if phase_12:
            print_header("DATE CONFIDENCE")
            print_counts(
                query(conn, "SELECT COALESCE(date_confidence,'None'), COUNT(*) FROM documents "
                            "GROUP BY 1 ORDER BY CASE COALESCE(date_confidence,'None') "
                            "WHEN 'High' THEN 1 WHEN 'Medium' THEN 2 WHEN 'Low' THEN 3 ELSE 4 END"),
                label_width=12,
            )

            print_header("WHERE DETECTED_YEAR CAME FROM")
            print_counts(query(conn, "SELECT COALESCE(detected_year_source,'none'), COUNT(*) "
                                     "FROM documents GROUP BY 1 ORDER BY COUNT(*) DESC"),
                         label_width=20)

            cutoff = datetime.now().year + 1
            future = query(
                conn,
                "SELECT COUNT(*) FROM documents WHERE COALESCE(detected_year,'') <> '' "
                "AND CAST(detected_year AS INTEGER) > ?", (cutoff,),
            )[0][0]
            print_header("REMAINING FUTURE DETECTED YEARS (should be 0)")
            print(f"  rows with detected_year > {cutoff}      {int(future):>8,}")
            if future:
                rows = query(
                    conn,
                    "SELECT detected_year, COUNT(*) FROM documents "
                    "WHERE COALESCE(detected_year,'') <> '' AND CAST(detected_year AS INTEGER) > ? "
                    "GROUP BY 1 ORDER BY 1", (cutoff,),
                )
                print()
                print_counts(rows, label_width=12)
                print("\n  See 02_Metadata_Index\\remaining_future_detected_years_report.csv")

        # ---- detected month ------------------------------------------------
        print_header("COUNT BY DETECTED MONTH")
        print_counts(query(conn, "SELECT COALESCE(detected_month,''), COUNT(*) FROM documents "
                                 "GROUP BY 1 ORDER BY 1"),
                     empty_label="(no month detected)")

        # ---- filename year mentions / maturities (Phase 1.1) ---------------
        if phase_11:
            mentions = token_counts(conn, "filename_year_mentions")
            files_with_mentions = query(
                conn, "SELECT COUNT(*) FROM documents WHERE COALESCE(filename_year_mentions,'') <> ''"
            )[0][0]
            print_header("FILENAME YEAR MENTIONS (every year seen in a file name)")
            print_token_counts(mentions, 20, width=8)
            print(f"\n  files with at least one year in the name: {int(files_with_mentions):,}")

            maturities = token_counts(conn, "possible_maturity_years")
            files_with_maturity = query(
                conn, "SELECT COUNT(*) FROM documents WHERE COALESCE(possible_maturity_years,'') <> ''"
            )[0][0]
            print_header("POSSIBLE MATURITY YEARS (blocked from becoming detected_year)")
            print_token_counts(maturities, 20, width=8)
            print(f"\n  files carrying a possible maturity year: {int(files_with_maturity):,}")

        # ---- source type --------------------------------------------------
        print_header("COUNT BY SOURCE TYPE GUESS")
        print_counts(query(conn, "SELECT source_type_guess, COUNT(*) FROM documents "
                                 "GROUP BY source_type_guess ORDER BY COUNT(*) DESC"))

        # ---- document type ------------------------------------------------
        print_header("COUNT BY DOCUMENT TYPE GUESS")
        print_counts(query(conn, "SELECT document_type_guess, COUNT(*) FROM documents "
                                 "GROUP BY document_type_guess ORDER BY COUNT(*) DESC"))

        # ---- strategy theme ------------------------------------------------
        print_header("COUNT BY STRATEGY THEME GUESS")
        print_counts(query(conn, "SELECT strategy_theme_guess, COUNT(*) FROM documents "
                                 "GROUP BY strategy_theme_guess ORDER BY COUNT(*) DESC"))

        # ---- ticker confidence ----------------------------------------------
        if phase_11:
            print_header("TICKER CONFIDENCE")
            print_counts(query(conn, "SELECT COALESCE(ticker_confidence,'None'), COUNT(*) "
                                     "FROM documents GROUP BY 1 ORDER BY COUNT(*) DESC"))

        # ---- ticker candidates ----------------------------------------------
        counts = token_counts(conn, clean_col)
        files_with_tickers = query(
            conn, f"SELECT COUNT(*) FROM documents WHERE COALESCE({clean_col},'') <> ''"
        )[0][0]

        print_header(f"TOP {args.top_tickers} CLEAN TICKER CANDIDATES (candidates only - not verified)")
        print_token_counts(counts, args.top_tickers)
        print(f"\n  distinct candidates: {len(counts):,}")
        print(f"  files with at least one candidate: {int(files_with_tickers):,} "
              f"({files_with_tickers / total * 100:.1f}% of index)")

        # ---- downgraded / excluded ticker noise (Phase 1.2) -------------------
        if phase_12:
            raw_counts = token_counts(conn, "ticker_candidates_raw")
            clean_counts = token_counts(conn, clean_col)
            vendor_counts = token_counts(conn, "source_vendor_candidates")
            noise = {
                token: count
                for token, count in raw_counts.items()
                if count > clean_counts.get(token, 0) + vendor_counts.get(token, 0)
            }
            print_header("TOP EXCLUDED / DOWNGRADED TICKER NOISE TOKENS")
            print("  (seen in file names, never promoted to a clean candidate)")
            print()
            print_token_counts(noise, 15)

        # ---- source / vendor candidates ---------------------------------------
        if phase_11:
            vendors = token_counts(conn, "source_vendor_candidates")
            files_with_vendors = query(
                conn, "SELECT COUNT(*) FROM documents WHERE COALESCE(source_vendor_candidates,'') <> ''"
            )[0][0]
            print_header(f"TOP {TOP_VENDORS} SOURCE / VENDOR CANDIDATES (research providers, not tickers)")
            print_token_counts(vendors, TOP_VENDORS)
            print(f"\n  distinct vendors: {len(vendors):,}")
            print(f"  files attributed to a research provider: {int(files_with_vendors):,} "
                  f"({files_with_vendors / total * 100:.1f}% of index)")

        # ---- duplicates -----------------------------------------------------
        dup_files = query(conn, "SELECT COUNT(*) FROM documents WHERE duplicate_status = 'duplicate'")[0][0]
        dup_originals = query(conn, "SELECT COUNT(*) FROM documents WHERE duplicate_status = 'original'")[0][0]
        dup_groups = query(
            conn,
            "SELECT COUNT(*) FROM (SELECT file_hash_sha256 FROM documents "
            "WHERE COALESCE(file_hash_sha256,'') <> '' GROUP BY file_hash_sha256 HAVING COUNT(*) > 1)",
        )[0][0]
        dup_bytes = query(
            conn, "SELECT COALESCE(SUM(file_size_bytes), 0) FROM documents "
                  "WHERE duplicate_status = 'duplicate'"
        )[0][0]

        print_header("DUPLICATES")
        print(f"  duplicate groups (same SHA-256)      {int(dup_groups):>8,}")
        print(f"  originals kept                       {int(dup_originals):>8,}")
        print(f"  DUPLICATE FILES                      {int(dup_files):>8,}")
        print(f"  bytes held in duplicate copies       {int(dup_bytes):>8,}")

        # ---- needs review ----------------------------------------------------
        needs_review = query(conn, "SELECT COUNT(*) FROM documents WHERE needs_review = 1")[0][0]
        errors = query(conn, "SELECT COUNT(*) FROM documents WHERE duplicate_status = 'error'")[0][0]

        print_header("NEEDS REVIEW")
        print(f"  NEEDS REVIEW (any severity)          {int(needs_review):>8,} "
              f"({needs_review / total * 100:.1f}% of index)")
        print(f"  rows with read/hash errors           {int(errors):>8,}")

        if phase_11:
            hits_col = "sensitive_keyword_hits" if phase_12 else "review_category"
            sensitive = query(
                conn, f"SELECT COUNT(*) FROM documents WHERE COALESCE({hits_col},'') <> ''"
                      if phase_12 else
                      "SELECT COUNT(*) FROM documents WHERE review_category LIKE '%sensitive_data%'"
            )[0][0]
            print(f"  rows with any sensitive keyword      {int(sensitive):>8,}")

            print_header("REVIEW SEVERITY")
            severity_rows = query(
                conn,
                "SELECT COALESCE(review_severity,'None'), COUNT(*) FROM documents GROUP BY 1 "
                "ORDER BY CASE COALESCE(review_severity,'None') WHEN 'High' THEN 1 "
                "WHEN 'Medium' THEN 2 WHEN 'Low' THEN 3 ELSE 4 END",
            )
            print_counts(severity_rows, label_width=12)

            print_header("TOP REVIEW CATEGORIES (combinations)")
            print_counts(
                query(conn, "SELECT review_category, COUNT(*) FROM documents "
                            "WHERE COALESCE(review_category,'') <> '' "
                            "GROUP BY review_category ORDER BY COUNT(*) DESC LIMIT 12"),
                label_width=60,
            )

            print_header("REVIEW CATEGORIES (individual)")
            category_totals = token_counts_semicolon(conn)
            print_token_counts(category_totals, 12, width=28)

            if phase_12:
                print_header("SENSITIVE CONTEXT GUESS")
                print_counts(
                    query(conn, "SELECT COALESCE(sensitive_context_guess,'none'), COUNT(*) "
                                "FROM documents GROUP BY 1 ORDER BY COUNT(*) DESC"),
                    label_width=28,
                )
                print("\n  possible_restricted_data  -> graded High, treat as restricted")
                print("  needs_manual_check        -> Medium, a human decides")
                print("  public_company_language / research_business_language -> Low,")
                print("                               ordinary research wording, still listed")
        else:
            print_header("TOP REVIEW REASONS")
            print_counts(
                query(conn, "SELECT review_reason, COUNT(*) FROM documents WHERE needs_review = 1 "
                            "GROUP BY review_reason ORDER BY COUNT(*) DESC LIMIT 10"),
                label_width=60,
            )

        print()
        print("=" * 78)
        print("detected_year = folder / archive year. Years found only in file names are kept in")
        print("filename_year_mentions, and security maturities in possible_maturity_years.")
        print("Ticker candidates are unverified. source_vendor_candidates are research providers,")
        print("not company tickers. All classifications are automated GUESSES from file paths only.")
        print("AI drafts. Humans verify. Cutler decides.")
        print("=" * 78)
    finally:
        conn.close()

    return 0


def token_counts_semicolon(conn: sqlite3.Connection) -> dict[str, int]:
    """review_category is semicolon-separated; count the individual categories."""
    counts: dict[str, int] = {}
    for (value,) in query(conn, "SELECT review_category FROM documents "
                                "WHERE COALESCE(review_category,'') <> ''"):
        for token in str(value).split(";"):
            token = token.strip()
            if token:
                counts[token] = counts.get(token, 0) + 1
    return counts


if __name__ == "__main__":
    sys.exit(main())
