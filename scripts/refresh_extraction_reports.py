#!/usr/bin/env python3
# =============================================================================
# Cutler Research Brain - Phase 2.1
# refresh_extraction_reports.py
#
# Rebuilds every Phase 2 extraction report from what is ALREADY in the
# pdf_extractions table. It never opens a source PDF, never touches the V
# Drive, and never changes a single character of extracted text.
#
# It also repairs one reporting bug from Phase 2.0: skipped rows were written
# with extraction_quality = "Failed", which made 126 rows look like failures
# when only 2 extractions had actually failed. Skipped rows are normalized to
# "Not Applicable" here.
#
# THE TWO AXES (the whole point of this phase)
#   extraction_status   what HAPPENED to the row
#                       extracted / partial / failed / skipped_duplicate /
#                       skipped_high_review / skipped_missing_file /
#                       skipped_non_pdf
#   extraction_quality  how much TEXT came out, per page
#                       High / Medium / Low / Empty / Failed / Not Applicable
#
# Rules:
#   - "Failed" quality is used ONLY for a genuine failed extraction attempt.
#   - Every skipped_* row gets "Not Applicable" - it was never attempted, so
#     there is no quality to report.
#   - An extracted PDF with no text stays "Empty" (an image-only PDF), never
#     "Failed" - the extractor worked, the PDF simply had no text layer.
#   - A partial row keeps its MEASURED quality (High/Medium/Low/Empty) and
#     carries its partialness in extraction_status. Quality answers "how much
#     text per page", status answers "what happened"; conflating them is what
#     caused the original confusion, so the two axes are kept separate.
#
# This module is also imported by scripts/extract_pdf_text.py, so extraction
# runs and this repair command always produce identical reports.
#
# Usage:
#   python scripts\refresh_extraction_reports.py ^
#       --db "data\document_metadata.sqlite" ^
#       --project-root "C:\Users\AI Admin\Documents\CutlerResearchBrain"
#
# No summarization, no interpretation, no OCR, no embeddings, no RAG, no
# investment conclusions. Principle: AI drafts. Humans verify. Cutler decides.
# =============================================================================

from __future__ import annotations

import argparse
import csv
import logging
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

# ---- extraction_status values ----------------------------------------------
STATUS_EXTRACTED = "extracted"
STATUS_PARTIAL = "partial"
STATUS_FAILED = "failed"
STATUS_SKIPPED_HIGH = "skipped_high_review"
STATUS_SKIPPED_DUPLICATE = "skipped_duplicate"
STATUS_SKIPPED_NON_PDF = "skipped_non_pdf"
STATUS_SKIPPED_MISSING = "skipped_missing_file"

SKIP_STATUSES = (
    STATUS_SKIPPED_HIGH, STATUS_SKIPPED_DUPLICATE,
    STATUS_SKIPPED_NON_PDF, STATUS_SKIPPED_MISSING,
)
ATTEMPTED_STATUSES = (STATUS_EXTRACTED, STATUS_PARTIAL, STATUS_FAILED)

# ---- extraction_quality values ---------------------------------------------
QUALITY_HIGH = "High"
QUALITY_MEDIUM = "Medium"
QUALITY_LOW = "Low"
QUALITY_EMPTY = "Empty"
QUALITY_FAILED = "Failed"
QUALITY_NOT_APPLICABLE = "Not Applicable"

QUALITY_ORDER = [QUALITY_HIGH, QUALITY_MEDIUM, QUALITY_LOW, QUALITY_EMPTY,
                 QUALITY_FAILED, QUALITY_NOT_APPLICABLE]

QUALITY_HIGH_MIN = 1200
QUALITY_MEDIUM_MIN = 300

STATUS_LABELS = {
    STATUS_EXTRACTED: "Successfully extracted",
    STATUS_PARTIAL: "Partially extracted",
    STATUS_FAILED: "Failed extraction attempt",
    STATUS_SKIPPED_DUPLICATE: "Skipped - duplicate of another file",
    STATUS_SKIPPED_HIGH: "Skipped - High-review gate",
    STATUS_SKIPPED_MISSING: "Skipped - file not found",
    STATUS_SKIPPED_NON_PDF: "Skipped - not a PDF",
}

STATUS_CATEGORIES = {
    STATUS_EXTRACTED: "extracted",
    STATUS_PARTIAL: "extracted",
    STATUS_FAILED: "failed",
    STATUS_SKIPPED_DUPLICATE: "skipped",
    STATUS_SKIPPED_HIGH: "skipped",
    STATUS_SKIPPED_MISSING: "skipped",
    STATUS_SKIPPED_NON_PDF: "skipped",
}

DEFAULT_LOGGER = logging.getLogger("phase2_reports")

# Row shape shared by every per-document report.
MANIFEST_COLUMNS = [
    "document_id", "file_name", "relative_path", "extraction_status",
    "extraction_quality", "page_count", "pages_extracted", "total_text_chars",
    "avg_text_chars_per_page", "empty_pages", "failed_pages",
    "scanned_pdf_suspected", "table_heavy_guess", "source_type_guess",
    "document_type_guess", "detected_year", "review_severity",
    "output_folder", "document_text_path", "error_message", "extracted_at",
]


# --------------------------------------------------------------------------
# Path safety
# --------------------------------------------------------------------------

def is_relative_to(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except (ValueError, OSError):
        return False


def validate_project_root(project_root: Path) -> None:
    """Reports may only ever be written inside the project root, never on V:."""
    drive = os.path.splitdrive(str(project_root.resolve()))[0].upper().rstrip(":")
    if drive == "V":
        raise SystemExit(
            f"ERROR: --project-root is on the V Drive ({project_root}). "
            "The V Drive is read-only. Aborting."
        )


def assert_inside_project(path: Path, project_root: Path) -> None:
    if not is_relative_to(path, project_root):
        raise SystemExit(f"ERROR: refusing to write outside the project root: {path}")


# --------------------------------------------------------------------------
# Quality normalization (the Phase 2.1 repair)
# --------------------------------------------------------------------------

def normalize_quality(conn: sqlite3.Connection, logger: logging.Logger | None = None) -> dict:
    """Make extraction_quality mean one thing only, across the whole table.

    - skipped_* rows            -> Not Applicable (never attempted)
    - failed rows               -> Failed
    - extracted/partial rows    -> a measured quality; a row that somehow still
                                   says Failed is re-derived from its own
                                   characters-per-page, so no attempted row is
                                   left mislabelled.

    Extracted TEXT is never touched - this only rewrites two report columns.
    """
    log = logger or DEFAULT_LOGGER
    stats = {"skipped_normalized": 0, "failed_normalized": 0, "attempted_regraded": 0}

    cur = conn.cursor()

    placeholders = ", ".join("?" for _ in SKIP_STATUSES)
    cur.execute(
        f"""
        UPDATE pdf_extractions
           SET extraction_quality = ?
         WHERE extraction_status IN ({placeholders})
           AND COALESCE(extraction_quality, '') <> ?
        """,
        (QUALITY_NOT_APPLICABLE, *SKIP_STATUSES, QUALITY_NOT_APPLICABLE),
    )
    stats["skipped_normalized"] = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0

    cur.execute(
        """
        UPDATE pdf_extractions
           SET extraction_quality = ?
         WHERE extraction_status = ?
           AND COALESCE(extraction_quality, '') <> ?
        """,
        (QUALITY_FAILED, STATUS_FAILED, QUALITY_FAILED),
    )
    stats["failed_normalized"] = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0

    # An extracted/partial row must never carry "Failed" quality. Re-derive it
    # from the characters that were actually written.
    cur.execute(
        """
        SELECT document_id, total_text_chars, pages_extracted, empty_pages
          FROM pdf_extractions
         WHERE extraction_status IN (?, ?)
           AND COALESCE(extraction_quality, '') IN ('', ?, ?)
        """,
        (STATUS_EXTRACTED, STATUS_PARTIAL, QUALITY_FAILED, QUALITY_NOT_APPLICABLE),
    )
    for row in cur.fetchall():
        quality = grade_quality(
            int(row["total_text_chars"] or 0),
            int(row["pages_extracted"] or 0),
            int(row["empty_pages"] or 0),
            failed=False,
        )[0]
        conn.execute(
            "UPDATE pdf_extractions SET extraction_quality = ? WHERE document_id = ?",
            (quality, row["document_id"]),
        )
        stats["attempted_regraded"] += 1

    conn.commit()

    if any(stats.values()):
        log.info(
            "Normalized extraction_quality: %d skipped row(s) -> Not Applicable, "
            "%d failed row(s) -> Failed, %d attempted row(s) re-graded.",
            stats["skipped_normalized"], stats["failed_normalized"], stats["attempted_regraded"],
        )
    else:
        log.info("extraction_quality already consistent; nothing to normalize.")

    # Mirror the corrected quality onto the documents table.
    try:
        conn.execute(
            """
            UPDATE documents
               SET pdf_extraction_quality = (
                       SELECT e.extraction_quality FROM pdf_extractions e
                        WHERE e.document_id = documents.document_id)
             WHERE document_id IN (SELECT document_id FROM pdf_extractions)
            """
        )
        conn.commit()
    except sqlite3.OperationalError:
        pass  # documents table has no pdf_extraction_quality column yet

    return stats


def grade_quality(total_chars: int, pages_extracted: int, empty_pages: int,
                  failed: bool) -> tuple[str, float]:
    """(extraction_quality, avg_text_chars_per_page). Purely mechanical.

    Shared by the extractor and by this repair path so both agree exactly.
    """
    if failed:
        return QUALITY_FAILED, 0.0
    if total_chars <= 0:
        return QUALITY_EMPTY, 0.0

    avg = total_chars / pages_extracted if pages_extracted else 0.0
    mostly_empty = pages_extracted > 0 and (empty_pages / pages_extracted) >= 0.5

    if avg < QUALITY_MEDIUM_MIN or mostly_empty:
        return QUALITY_LOW, avg
    if avg <= QUALITY_HIGH_MIN:
        return QUALITY_MEDIUM, avg
    return QUALITY_HIGH, avg


# --------------------------------------------------------------------------
# Query helpers
# --------------------------------------------------------------------------

def write_csv(path: Path, header: list[str], rows, logger: logging.Logger) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow(header)
        count = 0
        for row in rows:
            writer.writerow(row)
            count += 1
    logger.info("Wrote %s (%d data rows)", path, count)
    return count


def manifest_rows(conn: sqlite3.Connection, where: str = "", params: tuple = ()) -> list[tuple]:
    sql = """
        SELECT e.document_id, d.file_name, d.relative_path, e.extraction_status,
               e.extraction_quality, e.page_count, e.pages_extracted, e.total_text_chars,
               e.avg_text_chars_per_page, e.empty_pages, e.failed_pages,
               e.scanned_pdf_suspected, e.table_heavy_guess, d.source_type_guess,
               d.document_type_guess, d.detected_year, d.review_severity,
               e.output_folder, e.document_text_path, e.error_message, e.extracted_at
          FROM pdf_extractions e
          LEFT JOIN documents d ON d.document_id = e.document_id
    """
    if where:
        sql += f" WHERE {where}"
    sql += " ORDER BY e.total_text_chars DESC, d.relative_path"
    return [tuple(r) for r in conn.execute(sql, params)]


def scalar(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> int:
    return int(conn.execute(sql, params).fetchone()[0] or 0)


def collect_totals(conn: sqlite3.Connection) -> dict:
    """Every headline number, computed once and reused by all outputs."""
    skip_placeholders = ", ".join("?" for _ in SKIP_STATUSES)
    attempted_placeholders = ", ".join("?" for _ in ATTEMPTED_STATUSES)

    totals = {
        "pdfs_in_index": scalar(
            conn, "SELECT COUNT(*) FROM documents WHERE LOWER(COALESCE(file_extension,'')) = '.pdf'"
        ),
        "extraction_records": scalar(conn, "SELECT COUNT(*) FROM pdf_extractions"),
        "extracted": scalar(conn, "SELECT COUNT(*) FROM pdf_extractions WHERE extraction_status = ?",
                            (STATUS_EXTRACTED,)),
        "partial": scalar(conn, "SELECT COUNT(*) FROM pdf_extractions WHERE extraction_status = ?",
                          (STATUS_PARTIAL,)),
        "failed": scalar(conn, "SELECT COUNT(*) FROM pdf_extractions WHERE extraction_status = ?",
                         (STATUS_FAILED,)),
        "skipped_duplicate": scalar(conn, "SELECT COUNT(*) FROM pdf_extractions WHERE extraction_status = ?",
                                    (STATUS_SKIPPED_DUPLICATE,)),
        "skipped_high_review": scalar(conn, "SELECT COUNT(*) FROM pdf_extractions WHERE extraction_status = ?",
                                      (STATUS_SKIPPED_HIGH,)),
        "skipped_missing": scalar(conn, "SELECT COUNT(*) FROM pdf_extractions WHERE extraction_status = ?",
                                  (STATUS_SKIPPED_MISSING,)),
        "skipped_non_pdf": scalar(conn, "SELECT COUNT(*) FROM pdf_extractions WHERE extraction_status = ?",
                                  (STATUS_SKIPPED_NON_PDF,)),
        "skipped_total": scalar(
            conn, f"SELECT COUNT(*) FROM pdf_extractions WHERE extraction_status IN ({skip_placeholders})",
            SKIP_STATUSES),
        "attempted": scalar(
            conn, f"SELECT COUNT(*) FROM pdf_extractions WHERE extraction_status IN ({attempted_placeholders})",
            ATTEMPTED_STATUSES),
        "scanned_suspected": scalar(conn, "SELECT COUNT(*) FROM pdf_extractions WHERE scanned_pdf_suspected = 1"),
        "table_heavy": scalar(conn, "SELECT COUNT(*) FROM pdf_extractions WHERE table_heavy_guess = 1"),
        "empty_quality": scalar(conn, "SELECT COUNT(*) FROM pdf_extractions WHERE extraction_quality = ?",
                                (QUALITY_EMPTY,)),
        "total_text_chars": scalar(conn, "SELECT COALESCE(SUM(total_text_chars),0) FROM pdf_extractions"),
        "total_pages": scalar(conn, "SELECT COALESCE(SUM(pages_extracted),0) FROM pdf_extractions"),
    }
    totals["with_text"] = totals["extracted"] + totals["partial"]
    totals["not_yet_attempted"] = scalar(
        conn,
        "SELECT COUNT(*) FROM documents d WHERE LOWER(COALESCE(d.file_extension,'')) = '.pdf' "
        "AND NOT EXISTS (SELECT 1 FROM pdf_extractions e WHERE e.document_id = d.document_id)",
    )
    totals["ocr_candidates"] = scalar(
        conn,
        f"SELECT COUNT(*) FROM pdf_extractions WHERE extraction_status IN ({attempted_placeholders}) "
        f"AND (scanned_pdf_suspected = 1 OR extraction_quality = ?)",
        (*ATTEMPTED_STATUSES, QUALITY_EMPTY),
    )
    return totals


# --------------------------------------------------------------------------
# Reports
# --------------------------------------------------------------------------

def write_status_report(conn: sqlite3.Connection, path: Path, logger: logging.Logger) -> None:
    """One row per extraction_status, with a plain-English label and category."""
    rows = []
    for status, count, chars, pages in conn.execute(
        """
        SELECT COALESCE(extraction_status, '(none)'), COUNT(*),
               COALESCE(SUM(total_text_chars), 0), COALESCE(SUM(pages_extracted), 0)
          FROM pdf_extractions
         GROUP BY 1
        """
    ):
        rows.append((
            status,
            STATUS_CATEGORIES.get(status, "other"),
            STATUS_LABELS.get(status, status),
            "no" if status in SKIP_STATUSES else "yes",
            count, chars, pages,
        ))
    rows.sort(key=lambda r: -r[4])
    write_csv(path, ["extraction_status", "category", "meaning", "extraction_attempted",
                     "file_count", "total_text_chars", "pages_extracted"], rows, logger)


def write_quality_clean_report(conn: sqlite3.Connection, path: Path, logger: logging.Logger) -> None:
    """Quality counts for ATTEMPTED rows only - skipped rows are excluded."""
    placeholders = ", ".join("?" for _ in ATTEMPTED_STATUSES)
    counts = {
        row[0]: row
        for row in conn.execute(
            f"""
            SELECT COALESCE(extraction_quality, '(none)'), COUNT(*),
                   COALESCE(SUM(total_text_chars), 0),
                   ROUND(AVG(avg_text_chars_per_page), 1),
                   COALESCE(SUM(scanned_pdf_suspected), 0),
                   COALESCE(SUM(table_heavy_guess), 0)
              FROM pdf_extractions
             WHERE extraction_status IN ({placeholders})
             GROUP BY 1
            """,
            ATTEMPTED_STATUSES,
        )
    }
    total = sum(row[1] for row in counts.values()) or 1

    rows = []
    for quality in QUALITY_ORDER:
        if quality == QUALITY_NOT_APPLICABLE or quality not in counts:
            continue
        _, count, chars, avg, scanned, tables = counts[quality]
        rows.append((quality, count, f"{count / total * 100:.1f}%", chars, avg, scanned, tables))
    for quality, row in counts.items():
        if quality not in QUALITY_ORDER:
            rows.append((quality, row[1], f"{row[1] / total * 100:.1f}%", row[2], row[3], row[4], row[5]))

    write_csv(path, ["extraction_quality", "file_count", "share_of_attempted",
                     "total_text_chars", "avg_text_chars_per_page",
                     "scanned_pdf_suspected", "table_heavy_guess"], rows, logger)


def write_legacy_quality_report(conn: sqlite3.Connection, path: Path, logger: logging.Logger) -> None:
    """Quality x status grid over every row (kept from Phase 2.0)."""
    rows = [
        tuple(r) for r in conn.execute(
            """
            SELECT COALESCE(extraction_quality, '(none)'), COALESCE(extraction_status, '(none)'),
                   COUNT(*), COALESCE(SUM(total_text_chars), 0),
                   ROUND(AVG(avg_text_chars_per_page), 1),
                   COALESCE(SUM(scanned_pdf_suspected), 0), COALESCE(SUM(table_heavy_guess), 0)
              FROM pdf_extractions
             GROUP BY 1, 2
             ORDER BY COUNT(*) DESC
            """
        )
    ]
    write_csv(path, ["extraction_quality", "extraction_status", "file_count", "total_text_chars",
                     "avg_text_chars_per_page", "scanned_pdf_suspected", "table_heavy_guess"],
              rows, logger)


def write_all_reports(conn: sqlite3.Connection, project_root: Path,
                      run_stats: dict | None = None,
                      logger: logging.Logger | None = None) -> dict:
    """Every Phase 2 report, from the database only. No PDF is opened."""
    log = logger or DEFAULT_LOGGER
    project_root = Path(project_root).resolve()
    validate_project_root(project_root)

    data_dir = project_root / "data"
    index_dir = project_root / "02_Metadata_Index"
    for directory in (data_dir, index_dir):
        assert_inside_project(directory, project_root)
        directory.mkdir(parents=True, exist_ok=True)

    skip_placeholders = ", ".join("?" for _ in SKIP_STATUSES)
    attempted_placeholders = ", ".join("?" for _ in ATTEMPTED_STATUSES)

    # ---- full manifest ---------------------------------------------------
    rows = manifest_rows(conn)
    write_csv(data_dir / "pdf_extraction_manifest.csv", MANIFEST_COLUMNS, rows, log)
    write_csv(index_dir / "pdf_extraction_manifest.csv", MANIFEST_COLUMNS, rows, log)

    # ---- Phase 2.1 reports ------------------------------------------------
    write_csv(index_dir / "true_failed_extractions.csv", MANIFEST_COLUMNS,
              manifest_rows(conn, "e.extraction_status = ?", (STATUS_FAILED,)), log)

    write_csv(index_dir / "skipped_extractions_report.csv", MANIFEST_COLUMNS,
              manifest_rows(conn, f"e.extraction_status IN ({skip_placeholders})", SKIP_STATUSES), log)

    write_csv(index_dir / "ocr_candidates_report.csv", MANIFEST_COLUMNS,
              manifest_rows(
                  conn,
                  f"e.extraction_status IN ({attempted_placeholders}) "
                  f"AND (e.scanned_pdf_suspected = 1 OR e.extraction_quality = ?)",
                  (*ATTEMPTED_STATUSES, QUALITY_EMPTY)), log)

    write_csv(index_dir / "table_heavy_candidates_report.csv", MANIFEST_COLUMNS,
              manifest_rows(conn, "e.table_heavy_guess = 1"), log)

    write_status_report(conn, index_dir / "extraction_status_report.csv", log)
    write_quality_clean_report(conn, index_dir / "extraction_quality_clean_report.csv", log)

    # ---- Phase 2.0 reports, kept ------------------------------------------
    write_csv(index_dir / "pdf_extraction_failed_report.csv", MANIFEST_COLUMNS,
              manifest_rows(conn, "e.extraction_status IN (?, ?)",
                            (STATUS_FAILED, STATUS_PARTIAL)), log)
    write_csv(index_dir / "scanned_pdf_suspected_report.csv", MANIFEST_COLUMNS,
              manifest_rows(conn, "e.scanned_pdf_suspected = 1"), log)
    write_legacy_quality_report(conn, index_dir / "pdf_extraction_quality_report.csv", log)

    totals = collect_totals(conn)
    write_summary(conn, index_dir / "pdf_extraction_summary.md", totals, run_stats or {}, log)
    return totals


# --------------------------------------------------------------------------
# Manager-readable summary
# --------------------------------------------------------------------------

def _md_table(header: list[str], rows: list[tuple]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join(["---"] * len(header)) + "|"]
    for row in rows:
        lines.append("| " + " | ".join("" if v is None else str(v) for v in row) + " |")
    if not rows:
        lines.append("| " + " | ".join(["(none)"] * len(header)) + " |")
    return "\n".join(lines)


def write_summary(conn: sqlite3.Connection, path: Path, totals: dict,
                  run_stats: dict, logger: logging.Logger) -> None:
    generated = datetime.now().replace(microsecond=0).isoformat()
    attempted = totals["attempted"] or 1
    success_rate = (totals["extracted"] + totals["partial"]) / attempted * 100

    status_rows = []
    for status in (STATUS_EXTRACTED, STATUS_PARTIAL, STATUS_FAILED,
                   STATUS_SKIPPED_DUPLICATE, STATUS_SKIPPED_HIGH,
                   STATUS_SKIPPED_MISSING, STATUS_SKIPPED_NON_PDF):
        count = scalar(conn, "SELECT COUNT(*) FROM pdf_extractions WHERE extraction_status = ?",
                       (status,))
        if count or status in (STATUS_EXTRACTED, STATUS_PARTIAL, STATUS_FAILED):
            status_rows.append((status, STATUS_LABELS.get(status, status), f"{count:,}"))

    placeholders = ", ".join("?" for _ in ATTEMPTED_STATUSES)
    quality_rows = []
    quality_counts = {
        row[0]: row[1] for row in conn.execute(
            f"SELECT COALESCE(extraction_quality,'(none)'), COUNT(*) FROM pdf_extractions "
            f"WHERE extraction_status IN ({placeholders}) GROUP BY 1", ATTEMPTED_STATUSES
        )
    }
    quality_meaning = {
        QUALITY_HIGH: f"more than {QUALITY_HIGH_MIN:,} characters per page",
        QUALITY_MEDIUM: f"{QUALITY_MEDIUM_MIN:,} to {QUALITY_HIGH_MIN:,} characters per page",
        QUALITY_LOW: f"under {QUALITY_MEDIUM_MIN:,} characters per page, or half the pages empty",
        QUALITY_EMPTY: "no text at all - almost always an image-only PDF (OCR candidate)",
        QUALITY_FAILED: "the extraction attempt itself failed (corrupt or encrypted file)",
    }
    for quality in QUALITY_ORDER:
        if quality == QUALITY_NOT_APPLICABLE:
            continue
        count = quality_counts.get(quality, 0)
        quality_rows.append((quality, f"{count:,}",
                             f"{count / attempted * 100:.1f}%", quality_meaning.get(quality, "")))

    lines = [
        "# Phase 2 - PDF Text Extraction Summary",
        "",
        f"_Generated: {generated}_",
        "",
        "## In one line",
        "",
        f"**{totals['with_text']:,} of {totals['pdfs_in_index']:,} PDFs in the research archive now have "
        f"machine-readable text** - {totals['total_pages']:,} pages and "
        f"{totals['total_text_chars']:,} characters "
        f"({totals['total_text_chars'] / (1024 * 1024):.0f} MB of plain text). "
        f"Only **{totals['failed']:,}** extraction attempts actually failed.",
        "",
        "## How to read the two columns",
        "",
        "Every row has a **status** (what happened) and a **quality** (how much text came out).",
        "They are separate on purpose:",
        "",
        "- A **skipped** row was never attempted, so its quality is **Not Applicable**. "
        "Skipped rows are *not* failures.",
        "- **Failed** quality is used only for a genuine failed extraction attempt.",
        "- A PDF that extracted cleanly but holds no text layer is **Empty**, not Failed - "
        "the extractor worked, the file is a picture of a document.",
        "- A **partial** row keeps its measured quality; the partialness lives in the status.",
        "",
        "## Headline numbers",
        "",
        f"| Measure | Count |",
        f"|---|---|",
        f"| PDFs in the metadata index | {totals['pdfs_in_index']:,} |",
        f"| Extraction records | {totals['extraction_records']:,} |",
        f"| **Successfully extracted** | **{totals['extracted']:,}** |",
        f"| Partially extracted | {totals['partial']:,} |",
        f"| **True failed extractions** | **{totals['failed']:,}** |",
        f"| Skipped - duplicate | {totals['skipped_duplicate']:,} |",
        f"| Skipped - High review | {totals['skipped_high_review']:,} |",
        f"| Skipped - file not found | {totals['skipped_missing']:,} |",
        f"| Skipped - not a PDF | {totals['skipped_non_pdf']:,} |",
        f"| Not yet attempted | {totals['not_yet_attempted']:,} |",
        "",
        f"Extraction succeeded on **{success_rate:.1f}%** of the {totals['attempted']:,} PDFs "
        f"that were actually attempted.",
        "",
        "## Text produced",
        "",
        f"- Total pages extracted: **{totals['total_pages']:,}**",
        f"- Total characters extracted: **{totals['total_text_chars']:,}** "
        f"({totals['total_text_chars'] / (1024 * 1024):.1f} MB of plain text)",
        f"- Average per extracted document: "
        f"{(totals['total_text_chars'] // totals['with_text']) if totals['with_text'] else 0:,} characters",
        "",
        "## Follow-up candidates",
        "",
        f"- **OCR candidates: {totals['ocr_candidates']:,}** - pages exist but little or no text came out "
        f"(suspected image-only scans: {totals['scanned_suspected']:,}; "
        f"zero-text documents: {totals['empty_quality']:,}). "
        "**OCR was NOT performed in this phase.**",
        f"- **Table-heavy candidates: {totals['table_heavy']:,}** - the extracted text has a column-like "
        "shape. This is a crude layout guess only; no table was parsed.",
        "",
        "## Status breakdown",
        "",
        _md_table(["Status", "Meaning", "Files"], status_rows),
        "",
        "## Quality breakdown (attempted extractions only)",
        "",
        _md_table(["Quality", "Files", "Share", "What it means"], quality_rows),
        "",
        f"Skipped rows ({totals['skipped_total']:,}) are excluded from the quality table above, "
        "and are listed in `skipped_extractions_report.csv`.",
        "",
        "## Reports in this folder",
        "",
        "| File | What it holds |",
        "|---|---|",
        "| `pdf_extraction_manifest.csv` | Every PDF considered, one row each |",
        "| `true_failed_extractions.csv` | Only genuine failed attempts |",
        "| `skipped_extractions_report.csv` | Rows never attempted, with the reason |",
        "| `ocr_candidates_report.csv` | Little or no text extracted - possible scans |",
        "| `table_heavy_candidates_report.csv` | Table-shaped extracted text |",
        "| `extraction_status_report.csv` | Counts by status, with plain-English meanings |",
        "| `extraction_quality_clean_report.csv` | Quality counts, skipped rows excluded |",
        "| `pdf_extraction_failed_report.csv` | Failed and partial rows together |",
        "| `pdf_extraction_quality_report.csv` | Quality x status grid, all rows |",
        "| `scanned_pdf_suspected_report.csv` | Suspected image-only PDFs |",
        "",
        "## What was and was not done",
        "",
        "This phase extracted text and measured how much text came out. That is all.",
        "",
        "- No OCR was performed. Suspected scans are listed, not processed.",
        "- Skipped rows are not failed extractions; they were deliberately not attempted.",
        "- No document was summarized, paraphrased or interpreted.",
        "- No embeddings, no vector index, no retrieval, no RAG.",
        "- No document cards were created.",
        "- No investment conclusions of any kind were drawn.",
        "- Source PDFs were opened **read-only**. Nothing on the V Drive was modified, moved, "
        "renamed or deleted, and no source PDF was copied out of the archive - only extracted text.",
        "",
        "**AI drafts. Humans verify. Cutler decides.**",
        "",
    ]

    if run_stats:
        lines.extend([
            "---",
            "",
            "## Last extraction run",
            "",
            f"- Started: {run_stats.get('started_at', 'n/a')}",
            f"- Runtime: {run_stats.get('runtime_seconds', 0):,.1f} seconds",
            f"- Documents processed this run: {run_stats.get('considered', 0):,}",
            f"- Extracted this run: {run_stats.get('extracted_now', 0):,}",
            f"- Reused (already extracted, resumable skip): {run_stats.get('reused', 0):,}",
            f"- Options: limit={run_stats.get('limit') or 'none'}, "
            f"max_pages={run_stats.get('max_pages') or 'all'}, "
            f"force={run_stats.get('force', False)}, "
            f"include_high_review={run_stats.get('include_high_review', False)}",
            f"- Extractor: PyMuPDF {run_stats.get('extractor_version', '')}",
            "",
        ])

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    logger.info("Wrote %s", path)


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def refresh(db_path: Path, project_root: Path, run_stats: dict | None = None,
            logger: logging.Logger | None = None) -> dict:
    """Normalize quality values and rebuild every report. Read-only on PDFs."""
    log = logger or DEFAULT_LOGGER
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "pdf_extractions" not in tables:
            raise SystemExit(
                f"ERROR: no 'pdf_extractions' table in {db_path}\n"
                "Run scripts/extract_pdf_text.py first."
            )
        normalize_quality(conn, log)
        totals = write_all_reports(conn, project_root, run_stats, log)
    finally:
        conn.close()
    return totals


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Cutler Research Brain Phase 2.1 - rebuild the PDF extraction reports "
                    "from the database. Never opens a source PDF.",
    )
    parser.add_argument("--db", required=True,
                        help=r'SQLite index, e.g. "data\document_metadata.sqlite"')
    parser.add_argument("--project-root", required=True,
                        help=r'Project root, e.g. "C:\Users\AI Admin\Documents\CutlerResearchBrain"')
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(message)s", stream=sys.stdout)

    project_root = Path(args.project_root).expanduser().resolve()
    db_path = Path(args.db).expanduser()
    if not db_path.is_absolute():
        db_path = (project_root / db_path).resolve()

    if not db_path.exists():
        raise SystemExit(f"ERROR: database not found: {db_path}")

    # Log to the Phase 2 log as well, so the repair is on the record.
    log_path = project_root / "logs" / "phase2_pdf_extraction.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(log_path, mode="a", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-8s  %(message)s",
                                           "%Y-%m-%d %H:%M:%S"))
    DEFAULT_LOGGER.addHandler(handler)
    DEFAULT_LOGGER.setLevel(logging.INFO)
    DEFAULT_LOGGER.propagate = True

    DEFAULT_LOGGER.info("=" * 78)
    DEFAULT_LOGGER.info("Phase 2.1 - refreshing PDF extraction reports (no PDF is opened)")
    DEFAULT_LOGGER.info("Database    : %s", db_path)
    DEFAULT_LOGGER.info("Project root: %s", project_root)
    DEFAULT_LOGGER.info("=" * 78)

    totals = refresh(db_path, project_root, logger=DEFAULT_LOGGER)

    DEFAULT_LOGGER.info("-" * 78)
    DEFAULT_LOGGER.info("PDFs in index      : %s", f"{totals['pdfs_in_index']:,}")
    DEFAULT_LOGGER.info("Extracted          : %s", f"{totals['extracted']:,}")
    DEFAULT_LOGGER.info("Partial            : %s", f"{totals['partial']:,}")
    DEFAULT_LOGGER.info("TRUE failed        : %s", f"{totals['failed']:,}")
    DEFAULT_LOGGER.info("Skipped (all)      : %s", f"{totals['skipped_total']:,}")
    DEFAULT_LOGGER.info("OCR candidates     : %s  (no OCR was performed)", f"{totals['ocr_candidates']:,}")
    DEFAULT_LOGGER.info("Table-heavy guess  : %s", f"{totals['table_heavy']:,}")
    DEFAULT_LOGGER.info("-" * 78)
    DEFAULT_LOGGER.info("Reports refreshed. No PDF was opened and no extracted text was changed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
