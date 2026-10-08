#!/usr/bin/env python3
# =============================================================================
# Cutler Research Brain - Phase 2.1
# inspect_extractions.py
#
# Read-only console report over the pdf_extractions table.
#
# PHASE 2.1 FIX
# -------------
# extraction_status and extraction_quality are two different axes and this
# report no longer mixes them:
#
#   status  = what HAPPENED   (extracted / partial / failed / skipped_*)
#   quality = how much TEXT came out, per page (High / Medium / Low / Empty /
#             Failed / Not Applicable)
#
# Skipped rows were never attempted, so their quality is "Not Applicable" and
# they are counted separately. Only genuine failed attempts appear as failures.
#
# Prints:
#   - total PDF rows in the metadata index and total extraction records
#   - successfully extracted / partial / TRUE failed
#   - skipped duplicates / skipped high-review / skipped missing files
#   - extracted character and page totals
#   - quality counts EXCLUDING skipped rows
#   - skipped rows, separately, with reasons
#   - OCR candidates, separately
#   - table-heavy candidates, separately
#   - top largest extracted documents and top failures by error type
#
# Usage:
#   python scripts\inspect_extractions.py --db "data\document_metadata.sqlite"
#
# Reads only. Never writes to the database, the extracted text, or the V Drive.
# No document content is summarized or interpreted.
# Principle: AI drafts. Humans verify. Cutler decides.
# =============================================================================

from __future__ import annotations

import argparse
import re
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

STATUS_EXTRACTED = "extracted"
STATUS_PARTIAL = "partial"
STATUS_FAILED = "failed"
STATUS_SKIPPED_HIGH = "skipped_high_review"
STATUS_SKIPPED_DUPLICATE = "skipped_duplicate"
STATUS_SKIPPED_NON_PDF = "skipped_non_pdf"
STATUS_SKIPPED_MISSING = "skipped_missing_file"

SKIP_STATUSES = (STATUS_SKIPPED_HIGH, STATUS_SKIPPED_DUPLICATE,
                 STATUS_SKIPPED_NON_PDF, STATUS_SKIPPED_MISSING)
ATTEMPTED_STATUSES = (STATUS_EXTRACTED, STATUS_PARTIAL, STATUS_FAILED)

QUALITY_ORDER = ["High", "Medium", "Low", "Empty", "Failed", "Not Applicable"]

SKIP_LABELS = {
    STATUS_SKIPPED_DUPLICATE: "duplicate of another indexed file",
    STATUS_SKIPPED_HIGH: "High-review gate (possible client/portfolio material)",
    STATUS_SKIPPED_MISSING: "file not found at scan-time path",
    STATUS_SKIPPED_NON_PDF: "not a PDF",
}


def open_readonly(db_path: Path) -> sqlite3.Connection:
    try:
        conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    except sqlite3.OperationalError:
        conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def query(conn: sqlite3.Connection, sql: str, params=()) -> list[tuple]:
    return [tuple(r) for r in conn.execute(sql, params)]


def scalar(conn: sqlite3.Connection, sql: str, params=()) -> int:
    return int(conn.execute(sql, params).fetchone()[0] or 0)


def print_header(title: str) -> None:
    print()
    print(title)
    print("-" * max(len(title), 46))


def line(label: str, value, width: int = 42, suffix: str = "") -> None:
    print(f"  {label:<{width}} {value:>10,}{suffix}")


RE_ERROR_KIND = re.compile(r"^[a-zA-Z ]+?(?::|$)")


def error_kind(message: str) -> str:
    """Group failures by cause, not by full message text."""
    message = (message or "").strip()
    if not message:
        return "(no message)"
    for needle, label in [
        ("password", "encrypted PDF (password required)"),
        ("could not open", "could not open / corrupt file"),
        ("zero pages", "PDF reports zero pages"),
        ("no pages could be read", "no pages readable"),
        ("--max-pages", "truncated at --max-pages (partial by request)"),
        ("file not found", "file not found"),
    ]:
        if needle.lower() in message.lower():
            return label
    match = RE_ERROR_KIND.match(message)
    return (match.group(0).strip(": ") if match else message)[:60]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Cutler Research Brain Phase 2.1 - inspect the PDF extraction results.",
    )
    parser.add_argument("--db", required=True,
                        help=r'SQLite index, e.g. "data\document_metadata.sqlite"')
    parser.add_argument("--top", type=int, default=TOP_N,
                        help=f"How many rows in the top-N listings (default {TOP_N}).")
    args = parser.parse_args(argv)

    db_path = Path(args.db).expanduser()
    if not db_path.exists():
        print(f"ERROR: database not found: {db_path}", file=sys.stderr)
        return 1

    conn = open_readonly(db_path)
    try:
        tables = {r[0] for r in query(conn, "SELECT name FROM sqlite_master WHERE type='table'")}
        if "pdf_extractions" not in tables:
            print("ERROR: no 'pdf_extractions' table in this database.", file=sys.stderr)
            print("Run scripts/extract_pdf_text.py first.", file=sys.stderr)
            return 1

        skip_ph = ", ".join("?" for _ in SKIP_STATUSES)
        attempted_ph = ", ".join("?" for _ in ATTEMPTED_STATUSES)

        records = scalar(conn, "SELECT COUNT(*) FROM pdf_extractions")
        print("=" * 78)
        print("CUTLER RESEARCH BRAIN - PHASE 2 PDF EXTRACTION")
        print(f"Database: {db_path.resolve()}")
        print("=" * 78)

        if records == 0:
            print("\nNo extraction records yet. Run scripts/extract_pdf_text.py.")
            return 0

        pdfs = scalar(conn, "SELECT COUNT(*) FROM documents "
                            "WHERE LOWER(COALESCE(file_extension,'')) = '.pdf'") \
            if "documents" in tables else 0
        extracted = scalar(conn, "SELECT COUNT(*) FROM pdf_extractions WHERE extraction_status = ?",
                           (STATUS_EXTRACTED,))
        partial = scalar(conn, "SELECT COUNT(*) FROM pdf_extractions WHERE extraction_status = ?",
                         (STATUS_PARTIAL,))
        failed = scalar(conn, "SELECT COUNT(*) FROM pdf_extractions WHERE extraction_status = ?",
                        (STATUS_FAILED,))
        skipped_total = scalar(
            conn, f"SELECT COUNT(*) FROM pdf_extractions WHERE extraction_status IN ({skip_ph})",
            SKIP_STATUSES)
        attempted = extracted + partial + failed
        chars = scalar(conn, "SELECT COALESCE(SUM(total_text_chars),0) FROM pdf_extractions")
        pages = scalar(conn, "SELECT COALESCE(SUM(pages_extracted),0) FROM pdf_extractions")
        not_attempted = scalar(
            conn,
            "SELECT COUNT(*) FROM documents d WHERE LOWER(COALESCE(d.file_extension,'')) = '.pdf' "
            "AND NOT EXISTS (SELECT 1 FROM pdf_extractions e WHERE e.document_id = d.document_id)"
        ) if "documents" in tables else 0

        # ---- headline ------------------------------------------------------
        print_header("OVERVIEW")
        line("PDF rows in the metadata index", pdfs)
        line("Extraction records", records)
        print()
        line("Successfully extracted", extracted)
        line("Partially extracted", partial)
        line("TRUE FAILED extraction attempts", failed)
        line("Skipped (never attempted)", skipped_total)
        if not_attempted:
            line("Not yet attempted (no record)", not_attempted)
        print()
        if attempted:
            print(f"  Extraction succeeded on {(extracted + partial) / attempted * 100:.1f}% "
                  f"of the {attempted:,} PDFs actually attempted.")

        # ---- text produced --------------------------------------------------
        print_header("TEXT EXTRACTED")
        line("Total pages extracted", pages)
        line("Total characters extracted", chars)
        print(f"  {'':<42} {chars / (1024 * 1024):>10.1f} MB of plain text")
        if extracted + partial:
            line("Average characters per document", chars // (extracted + partial))

        # ---- quality, attempted rows only -----------------------------------
        print_header("EXTRACTION QUALITY (attempted extractions only)")
        quality_counts = {
            row[0]: row[1] for row in query(
                conn,
                f"SELECT COALESCE(extraction_quality,'(none)'), COUNT(*) FROM pdf_extractions "
                f"WHERE extraction_status IN ({attempted_ph}) GROUP BY 1", ATTEMPTED_STATUSES)
        }
        shown = set()
        for quality in QUALITY_ORDER:
            if quality == "Not Applicable":
                continue
            count = quality_counts.get(quality, 0)
            if count or quality in ("High", "Medium", "Low", "Empty", "Failed"):
                pct = count / attempted * 100 if attempted else 0.0
                print(f"  {quality:<20} {count:>10,}  {pct:>5.1f}%")
                shown.add(quality)
        for quality, count in quality_counts.items():
            if quality not in shown:
                print(f"  {quality:<20} {count:>10,}")
        print()
        print("  Quality measures characters per page only. It says nothing about content.")
        print("  Skipped rows are NOT counted here - they were never attempted.")

        # ---- skipped rows ----------------------------------------------------
        print_header("SKIPPED ROWS (not failures - never attempted)")
        if skipped_total:
            for status in SKIP_STATUSES:
                count = scalar(conn, "SELECT COUNT(*) FROM pdf_extractions WHERE extraction_status = ?",
                               (status,))
                if count:
                    print(f"  {status:<26} {count:>8,}   {SKIP_LABELS.get(status, '')}")
            line("TOTAL SKIPPED", skipped_total, width=26)
        else:
            print("  (none)")

        # ---- OCR candidates --------------------------------------------------
        scanned = scalar(conn, "SELECT COUNT(*) FROM pdf_extractions WHERE scanned_pdf_suspected = 1")
        empty = scalar(
            conn, f"SELECT COUNT(*) FROM pdf_extractions WHERE extraction_status IN ({attempted_ph}) "
                  f"AND extraction_quality = 'Empty'", ATTEMPTED_STATUSES)
        ocr = scalar(
            conn, f"SELECT COUNT(*) FROM pdf_extractions WHERE extraction_status IN ({attempted_ph}) "
                  f"AND (scanned_pdf_suspected = 1 OR extraction_quality = 'Empty')",
            ATTEMPTED_STATUSES)

        print_header("OCR CANDIDATES (no OCR was performed)")
        line("Suspected image-only / scanned PDFs", scanned)
        line("Documents with zero extracted text", empty)
        line("TOTAL OCR CANDIDATES", ocr)
        print("\n  Listed in 02_Metadata_Index\\ocr_candidates_report.csv for a later decision.")

        # ---- table-heavy -----------------------------------------------------
        tables_guess = scalar(conn, "SELECT COUNT(*) FROM pdf_extractions WHERE table_heavy_guess = 1")
        print_header("TABLE-HEAVY CANDIDATES (layout guess only)")
        line("Extracted text with a column-like shape", tables_guess)
        print("\n  Listed in 02_Metadata_Index\\table_heavy_candidates_report.csv. No table was parsed.")

        # ---- largest ---------------------------------------------------------
        join = "documents" in tables
        name_expr = "COALESCE(d.relative_path, e.document_id)" if join else "e.document_id"
        from_expr = ("pdf_extractions e LEFT JOIN documents d ON d.document_id = e.document_id"
                     if join else "pdf_extractions e")

        print_header(f"TOP {args.top} LARGEST EXTRACTED DOCUMENTS")
        rows = query(conn, f"""
            SELECT {name_expr}, e.total_text_chars, e.pages_extracted, e.extraction_quality
              FROM {from_expr}
             WHERE e.extraction_status IN ({attempted_ph})
               AND e.total_text_chars > 0
             ORDER BY e.total_text_chars DESC
             LIMIT ?
        """, (*ATTEMPTED_STATUSES, args.top))
        if rows:
            for name, chars_n, pages_n, quality in rows:
                print(f"  {int(chars_n or 0):>11,} chars  {int(pages_n or 0):>5} pages  "
                      f"{str(quality or ''):<7} {str(name)[:64]}")
        else:
            print("  (none)")

        # ---- failures ---------------------------------------------------------
        print_header("TRUE FAILURES BY ERROR TYPE")
        kinds: dict[str, int] = {}
        for (message,) in query(conn, "SELECT COALESCE(error_message,'') FROM pdf_extractions "
                                      "WHERE extraction_status = ?", (STATUS_FAILED,)):
            kind = error_kind(message)
            kinds[kind] = kinds.get(kind, 0) + 1
        if kinds:
            for kind, count in sorted(kinds.items(), key=lambda kv: (-kv[1], kv[0])):
                print(f"  {kind:<48} {count:>8,}")
        else:
            print("  (none - no extraction attempt failed)")

        print_header(f"TOP {args.top} FAILED DOCUMENTS")
        rows = query(conn, f"""
            SELECT {name_expr}, COALESCE(e.error_message,'')
              FROM {from_expr}
             WHERE e.extraction_status = ?
             ORDER BY {name_expr}
             LIMIT ?
        """, (STATUS_FAILED, args.top))
        if rows:
            for name, message in rows:
                print(f"  {str(name)[:56]:<56} {str(message)[:56]}")
        else:
            print("  (none)")

        if partial:
            print_header(f"PARTIAL EXTRACTIONS (text produced, but not every page)")
            rows = query(conn, f"""
                SELECT {name_expr}, e.pages_extracted, e.page_count, COALESCE(e.error_message,'')
                  FROM {from_expr}
                 WHERE e.extraction_status = ?
                 ORDER BY {name_expr}
                 LIMIT ?
            """, (STATUS_PARTIAL, args.top))
            for name, done, total_pages, message in rows:
                print(f"  {str(name)[:48]:<48} {int(done or 0):>4}/{int(total_pages or 0):<5} "
                      f"{str(message)[:40]}")

        print()
        print("=" * 78)
        print("Skipped rows are not failed extractions. Quality is a character count per page,")
        print("not a judgement about content. No OCR, no summarization, no interpretation, no")
        print("embeddings, no RAG, and no investment conclusions were produced in this phase.")
        print("Source PDFs were opened read-only and never modified.")
        print("AI drafts. Humans verify. Cutler decides.")
        print("=" * 78)
    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
