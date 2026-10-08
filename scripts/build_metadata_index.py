#!/usr/bin/env python3
# =============================================================================
# Cutler Research Brain - Phase 1.2
# build_metadata_index.py
#
# Turns the raw scan records (data/raw_scan.jsonl) into:
#
#   data/document_metadata.sqlite               - the documents table (source of truth)
#   data/document_manifest.csv                  - full manifest
#   02_Metadata_Index/document_manifest.csv
#   02_Metadata_Index/scan_summary.md           - human-readable summary
#   02_Metadata_Index/duplicate_report.csv
#   02_Metadata_Index/source_type_report.csv
#   02_Metadata_Index/ticker_candidate_report.csv
#
# Phase 1.1 also writes:
#   02_Metadata_Index/sensitive_data_review_report.csv
#   02_Metadata_Index/high_review_report.csv
#   02_Metadata_Index/ticker_noise_report.csv
#   02_Metadata_Index/year_detection_report.csv
#   02_Metadata_Index/sample_unknown_source_files.csv
#   02_Metadata_Index/sample_unknown_document_type_files.csv
#
# Phase 1.2 adds:
#   02_Metadata_Index/public_language_sensitive_false_positive_report.csv
#   02_Metadata_Index/date_confidence_report.csv
#   02_Metadata_Index/remaining_future_detected_years_report.csv
#
# It resolves duplicates across the WHOLE index (not just the last scan) by
# comparing SHA-256 hashes, and re-grades review_severity / review_category
# accordingly. Existing Phase 1.0 databases are migrated in place - the new
# columns are added with ALTER TABLE, no data is dropped.
#
# Normally this runs automatically at the end of scan_source_archive.py.
# You can also run it on its own to rebuild the index and reports:
#
#   python scripts\build_metadata_index.py ^
#       --project-root "C:\Users\AI Admin\Documents\CutlerResearchBrain"
#
# Phase 1 only. No document contents are read here - just the metadata rows.
# Principle: AI drafts. Humans verify. Cutler decides.
# =============================================================================

from __future__ import annotations

import argparse
import csv
import json
import logging
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

# Original Phase 1.0 columns. Order preserved for backward compatibility -
# anything already reading document_manifest.csv keeps working.
BASE_COLUMNS = [
    "document_id",
    "file_name",
    "file_extension",
    "file_path",
    "relative_path",
    "parent_folder",
    "source_folder_guess",
    "file_size_bytes",
    "created_time",
    "modified_time",
    "file_hash_sha256",
    "duplicate_status",
    "duplicate_of",
    "folder_date_guess",
    "detected_year",
    "detected_month",
    "ticker_candidates",
    "source_type_guess",
    "document_type_guess",
    "strategy_theme_guess",
    "needs_review",
    "review_reason",
    "processed_at",
]

# Phase 1.1 additions, appended after the originals.
PHASE_11_COLUMNS = [
    "archive_year",
    "archive_month",
    "folder_year",
    "folder_month",
    "filename_year_mentions",
    "possible_maturity_years",
    "source_vendor_candidates",
    "ticker_candidates_raw",
    "ticker_candidates_clean",
    "ticker_confidence",
    "review_severity",
    "review_category",
]

# Phase 1.2 additions.
PHASE_12_COLUMNS = [
    "date_confidence",
    "detected_year_source",
    "sensitive_keyword_hits",
    "sensitive_context_guess",
]

# Phase 2 additions. exclude_from_phase2_extraction is the gate the PDF
# extractor honours: it is derived, never hand-edited here (High review = do not
# extract until a human has cleared the row). The pdf_* columns are filled in by
# scripts/extract_pdf_text.py and stay empty until that runs.
PHASE_2_COLUMNS = [
    "exclude_from_phase2_extraction",
    "pdf_extraction_status",
    "pdf_extraction_quality",
    "pdf_page_count",
    "pdf_total_text_chars",
    "pdf_extracted_at",
]

COLUMNS = BASE_COLUMNS + PHASE_11_COLUMNS + PHASE_12_COLUMNS + PHASE_2_COLUMNS

# Extra bookkeeping columns kept in SQLite but not part of the required set.
EXTRA_COLUMNS = ["scan_source_root"]

INTEGER_COLUMNS = {
    "file_size_bytes", "needs_review", "exclude_from_phase2_extraction",
    "pdf_page_count", "pdf_total_text_chars",
}

# Columns the Phase 2 extractor owns. build_metadata_index must never blank them
# when a Phase 1 re-scan re-writes a row.
EXTRACTION_OWNED_COLUMNS = [
    "pdf_extraction_status", "pdf_extraction_quality", "pdf_page_count",
    "pdf_total_text_chars", "pdf_extracted_at",
]

DB_FILENAME = "document_metadata.sqlite"

SEVERITY_ORDER = ["High", "Medium", "Low", "None"]

DEFAULT_SEVERITY_MAP = {
    "sensitive_data": "High",
    "scan_error": "High",
    "possible_sensitive_data": "Medium",
    "duplicate": "Medium",
    "no_ticker_on_research": "Medium",
    "public_language_sensitive": "Low",
    "unknown_source_type": "Low",
    "unknown_document_type": "Low",
}

DEFAULT_CATEGORY_ORDER = [
    "sensitive_data", "scan_error", "possible_sensitive_data", "duplicate",
    "no_ticker_on_research", "public_language_sensitive",
    "unknown_source_type", "unknown_document_type",
]

SAMPLE_ROWS = 500  # cap on the "sample_unknown_*" reports

DEFAULT_LOGGER = logging.getLogger("phase1_index")


# --------------------------------------------------------------------------
# Review grading helpers
# --------------------------------------------------------------------------

def severity_for(categories: list[str], severity_map: dict) -> str:
    """Worst severity across the given categories."""
    severity = "None"
    for category in categories:
        candidate = str(severity_map.get(category, "Low"))
        if candidate not in SEVERITY_ORDER:
            candidate = "Low"
        if SEVERITY_ORDER.index(candidate) < SEVERITY_ORDER.index(severity):
            severity = candidate
    return severity


def order_categories(categories: list[str], order: list[str]) -> list[str]:
    ranked = [c for c in order if c in categories]
    ranked += [c for c in categories if c not in ranked]
    return ranked


# --------------------------------------------------------------------------
# SQLite helpers
# --------------------------------------------------------------------------

def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def create_schema(conn: sqlite3.Connection, reset: bool = False,
                  logger: logging.Logger | None = None) -> None:
    """Create the documents table, or migrate an existing one in place."""
    log = logger or DEFAULT_LOGGER
    cur = conn.cursor()

    if reset:
        cur.execute("DROP TABLE IF EXISTS documents")

    column_defs = []
    for col in COLUMNS + EXTRA_COLUMNS:
        if col == "document_id":
            column_defs.append("document_id TEXT PRIMARY KEY")
        elif col in INTEGER_COLUMNS:
            column_defs.append(f"{col} INTEGER")
        else:
            column_defs.append(f"{col} TEXT")

    cur.execute(f"CREATE TABLE IF NOT EXISTS documents ({', '.join(column_defs)})")

    # ---- migrate a Phase 1.0 table -------------------------------------
    existing = {row["name"] for row in cur.execute("PRAGMA table_info(documents)")}
    missing = [c for c in COLUMNS + EXTRA_COLUMNS if c not in existing]
    for col in missing:
        col_type = "INTEGER" if col in INTEGER_COLUMNS else "TEXT"
        cur.execute(f"ALTER TABLE documents ADD COLUMN {col} {col_type}")
    if missing:
        log.info("Migrated documents table: added %d new column(s): %s",
                 len(missing), ", ".join(missing))

    # Indexes that make the inspect/report queries fast on a large archive.
    for name, expr in [
        ("idx_documents_hash", "file_hash_sha256"),
        ("idx_documents_year", "detected_year"),
        ("idx_documents_archive_year", "archive_year"),
        ("idx_documents_source_type", "source_type_guess"),
        ("idx_documents_needs_review", "needs_review"),
        ("idx_documents_severity", "review_severity"),
    ]:
        cur.execute(f"CREATE INDEX IF NOT EXISTS {name} ON documents({expr})")

    # Audit trail of scans, so a human can see what produced what.
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS scan_runs (
            run_id            INTEGER PRIMARY KEY AUTOINCREMENT,
            processed_at      TEXT,
            source_root       TEXT,
            project_root      TEXT,
            files_indexed     INTEGER,
            files_with_errors INTEGER,
            scan_seconds      REAL,
            details_json      TEXT
        )
        """
    )
    conn.commit()


def upsert_records(conn: sqlite3.Connection, records: list[dict], logger: logging.Logger) -> int:
    """Insert or replace rows keyed on document_id (a stable hash of the path).

    Phase 2 results already stored on a row are carried over, so re-running a
    Phase 1 scan never wipes what the PDF extractor wrote.
    """
    all_cols = COLUMNS + EXTRA_COLUMNS
    placeholders = ", ".join("?" for _ in all_cols)
    sql = f"INSERT OR REPLACE INTO documents ({', '.join(all_cols)}) VALUES ({placeholders})"

    existing: dict[str, dict] = {}
    try:
        cur = conn.execute(
            f"SELECT document_id, {', '.join(EXTRACTION_OWNED_COLUMNS)} FROM documents"
        )
        for row in cur.fetchall():
            existing[row["document_id"]] = {c: row[c] for c in EXTRACTION_OWNED_COLUMNS}
    except sqlite3.OperationalError:
        pass  # first run, or a table that predates the Phase 2 columns

    carried = 0
    rows = []
    for rec in records:
        prior = existing.get(rec.get("document_id", ""))
        if prior and any(prior.get(c) not in (None, "") for c in EXTRACTION_OWNED_COLUMNS):
            for col in EXTRACTION_OWNED_COLUMNS:
                if not rec.get(col):
                    rec[col] = prior.get(col)
            carried += 1
        row = []
        for col in all_cols:
            value = rec.get(col, "")
            if col in INTEGER_COLUMNS:
                try:
                    value = int(value or 0)
                except (TypeError, ValueError):
                    value = 0
            elif value is None:
                value = ""
            row.append(value)
        rows.append(tuple(row))

    conn.executemany(sql, rows)
    conn.commit()
    logger.info("Wrote %d rows into the documents table.", len(rows))
    if carried:
        logger.info("Carried existing Phase 2 extraction results forward on %d row(s).", carried)
    return len(rows)


def refresh_extraction_gate(conn: sqlite3.Connection, logger: logging.Logger) -> int:
    """Recompute exclude_from_phase2_extraction across the whole index.

    Rule: True when review_severity is High, False otherwise. It runs AFTER
    duplicate resolution so the flag always reflects the final severity, and it
    is recomputed every build so a severity change can never leave a stale gate
    behind. scripts/extract_pdf_text.py refuses to touch a flagged row unless
    --include-high-review is passed.
    """
    conn.execute(
        """
        UPDATE documents
           SET exclude_from_phase2_extraction =
               CASE WHEN review_severity = 'High' THEN 1 ELSE 0 END
        """
    )
    conn.commit()
    excluded = conn.execute(
        "SELECT COUNT(*) FROM documents WHERE exclude_from_phase2_extraction = 1"
    ).fetchone()[0]
    logger.info("Phase 2 extraction gate: %d row(s) excluded (review_severity = High).", excluded)
    return excluded


# --------------------------------------------------------------------------
# Duplicate resolution (and duplicate-driven review grading)
# --------------------------------------------------------------------------

def resolve_duplicates(conn: sqlite3.Connection, logger: logging.Logger,
                       severity_map: dict, category_order: list[str]) -> dict:
    """Assign duplicate_status / duplicate_of across the entire index.

    Files that share a SHA-256 hash are one duplicate group. The oldest
    modified_time (ties broken by relative_path) is treated as the "original";
    the rest are "duplicate" and point at the original's document_id.

    Duplicates are NEVER dropped - they are kept and flagged, so the human
    review step can decide which copy is canonical. The "duplicate" review
    category (Medium severity) is added here and removed on a re-run, so the
    grading stays correct no matter how often you re-index.
    """
    cur = conn.cursor()

    # ---- reset so re-running is idempotent -------------------------------
    cur.execute(
        """
        UPDATE documents
           SET duplicate_status = 'unique', duplicate_of = ''
         WHERE duplicate_status <> 'error'
        """
    )

    # Strip any stale "duplicate" category / reason from a previous run.
    stale = cur.execute(
        "SELECT document_id, review_category, review_reason FROM documents "
        "WHERE review_category LIKE '%duplicate%' OR review_reason LIKE '%duplicate of %'"
    ).fetchall()
    for row in stale:
        categories = [c for c in (row["review_category"] or "").split(";") if c and c != "duplicate"]
        reasons = [r for r in (row["review_reason"] or "").split("; ")
                   if r and not r.startswith("duplicate of ") and r != "duplicate of another file"]
        conn.execute(
            """
            UPDATE documents
               SET review_category = ?, review_reason = ?,
                   review_severity = ?, needs_review = ?
             WHERE document_id = ?
            """,
            (";".join(categories), "; ".join(reasons),
             severity_for(categories, severity_map), 1 if categories else 0,
             row["document_id"]),
        )

    # ---- find duplicate groups -------------------------------------------
    cur.execute(
        """
        SELECT file_hash_sha256
          FROM documents
         WHERE file_hash_sha256 <> ''
           AND file_hash_sha256 IS NOT NULL
           AND duplicate_status <> 'error'
         GROUP BY file_hash_sha256
        HAVING COUNT(*) > 1
        """
    )
    dup_hashes = [r["file_hash_sha256"] for r in cur.fetchall()]

    groups = 0
    duplicate_rows = 0

    for file_hash in dup_hashes:
        cur.execute(
            """
            SELECT document_id, modified_time, relative_path,
                   review_category, review_reason
              FROM documents
             WHERE file_hash_sha256 = ?
               AND duplicate_status <> 'error'
             ORDER BY (modified_time = '') ASC, modified_time ASC, relative_path ASC
            """,
            (file_hash,),
        )
        members = cur.fetchall()
        if len(members) < 2:
            continue

        groups += 1
        original = members[0]
        cur.execute(
            "UPDATE documents SET duplicate_status = 'original', duplicate_of = '' WHERE document_id = ?",
            (original["document_id"],),
        )

        for member in members[1:]:
            duplicate_rows += 1
            categories = [c for c in (member["review_category"] or "").split(";") if c]
            if "duplicate" not in categories:
                categories.append("duplicate")
            categories = order_categories(categories, category_order)

            reasons = [r for r in (member["review_reason"] or "").split("; ") if r]
            note = f"duplicate of {original['document_id']} ({original['relative_path']})"
            if not any(r.startswith("duplicate of ") for r in reasons):
                reasons.append(note)

            cur.execute(
                """
                UPDATE documents
                   SET duplicate_status = 'duplicate',
                       duplicate_of = ?,
                       review_category = ?,
                       review_severity = ?,
                       review_reason = ?,
                       needs_review = 1
                 WHERE document_id = ?
                """,
                (original["document_id"], ";".join(categories),
                 severity_for(categories, severity_map), "; ".join(reasons),
                 member["document_id"]),
            )

    conn.commit()
    logger.info("Duplicate groups: %d | duplicate files flagged: %d", groups, duplicate_rows)
    return {"duplicate_groups": groups, "duplicate_files": duplicate_rows}


# --------------------------------------------------------------------------
# Export helpers
# --------------------------------------------------------------------------

def write_csv(path: Path, header: list[str], rows, logger: logging.Logger) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    # utf-8-sig keeps Excel happy with accented characters.
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow(header)
        count = 0
        for row in rows:
            writer.writerow(row)
            count += 1
    logger.info("Wrote %s (%d data rows)", path, count)
    return count


def export_manifest(conn: sqlite3.Connection, paths: list[Path], logger: logging.Logger) -> int:
    cur = conn.cursor()
    cur.execute(f"SELECT {', '.join(COLUMNS)} FROM documents ORDER BY relative_path")
    rows = [tuple(r[c] for c in COLUMNS) for r in cur.fetchall()]
    for path in paths:
        write_csv(path, COLUMNS, rows, logger)
    return len(rows)


def export_duplicate_report(conn: sqlite3.Connection, path: Path, logger: logging.Logger) -> None:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT file_hash_sha256, duplicate_status, document_id, duplicate_of,
               file_name, relative_path, file_size_bytes, modified_time, file_path
          FROM documents
         WHERE duplicate_status IN ('original', 'duplicate')
         ORDER BY file_hash_sha256, duplicate_status DESC, relative_path
        """
    )
    header = ["file_hash_sha256", "duplicate_status", "document_id", "duplicate_of",
              "file_name", "relative_path", "file_size_bytes", "modified_time", "file_path"]
    write_csv(path, header, (tuple(r) for r in cur.fetchall()), logger)


def export_source_type_report(conn: sqlite3.Connection, path: Path, logger: logging.Logger) -> None:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT source_type_guess,
               COUNT(*) AS file_count,
               SUM(needs_review) AS needs_review_count,
               SUM(CASE WHEN review_severity = 'High' THEN 1 ELSE 0 END) AS high_severity_count,
               SUM(CASE WHEN COALESCE(ticker_candidates_clean, '') = '' THEN 1 ELSE 0 END) AS no_clean_ticker_count,
               SUM(file_size_bytes) AS total_bytes
          FROM documents
         GROUP BY source_type_guess
         ORDER BY file_count DESC
        """
    )
    header = ["source_type_guess", "file_count", "needs_review_count",
              "high_severity_count", "no_clean_ticker_count", "total_bytes"]
    write_csv(path, header, (tuple(r) for r in cur.fetchall()), logger)


def _token_counts(conn: sqlite3.Connection, column: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    cur = conn.cursor()
    cur.execute(f"SELECT {column} FROM documents WHERE COALESCE({column}, '') <> ''")
    for (value,) in cur.fetchall():
        for token in str(value).split(","):
            token = token.strip()
            if token:
                counts[token] = counts.get(token, 0) + 1
    return counts


def export_ticker_report(conn: sqlite3.Connection, path: Path, logger: logging.Logger) -> None:
    """One row per CLEAN ticker candidate. Candidates, never verified."""
    cur = conn.cursor()
    cur.execute(
        "SELECT ticker_candidates_clean, detected_year, source_type_guess, ticker_confidence "
        "FROM documents WHERE COALESCE(ticker_candidates_clean, '') <> ''"
    )

    counts: dict[str, dict] = {}
    for row in cur.fetchall():
        for token in str(row["ticker_candidates_clean"]).split(","):
            token = token.strip()
            if not token:
                continue
            entry = counts.setdefault(
                token, {"count": 0, "years": set(), "source_types": set(), "high": 0}
            )
            entry["count"] += 1
            if row["detected_year"]:
                entry["years"].add(str(row["detected_year"]))
            if row["source_type_guess"]:
                entry["source_types"].add(str(row["source_type_guess"]))
            if row["ticker_confidence"] == "High":
                entry["high"] += 1

    ordered = sorted(counts.items(), key=lambda kv: (-kv[1]["count"], kv[0]))
    rows = [
        (token, data["count"], data["high"], ",".join(sorted(data["years"])),
         ",".join(sorted(data["source_types"])))
        for token, data in ordered
    ]
    write_csv(path, ["ticker_candidate_clean", "file_count", "high_confidence_files",
                     "years_seen", "source_types_seen"], rows, logger)


def export_ticker_noise_report(conn: sqlite3.Connection, path: Path, ticker_rules: dict,
                               logger: logging.Logger) -> None:
    """Every raw uppercase token, and what happened to it.

    This is the report to work through when the ticker list looks wrong: it
    shows which tokens survived as clean candidates, which were parked as
    research vendors, and which were dropped by the exclusion list. Anything
    that should not be a ticker goes into configs/ticker_rules.yaml.
    """
    exclusions = {str(x).upper() for x in ticker_rules.get("exclusions", [])}
    vendor_tokens = {str(x).upper() for x in ticker_rules.get("vendor_tokens", [])}

    raw_counts = _token_counts(conn, "ticker_candidates_raw")
    clean_counts = _token_counts(conn, "ticker_candidates_clean")
    vendor_counts = _token_counts(conn, "source_vendor_candidates")

    rows = []
    for token in sorted(set(raw_counts) | set(clean_counts) | set(vendor_counts)):
        raw_n = raw_counts.get(token, 0)
        clean_n = clean_counts.get(token, 0)
        vendor_n = vendor_counts.get(token, 0)

        if token in exclusions:
            classification = "excluded_false_positive"
        elif token in vendor_tokens and vendor_n and not clean_n:
            classification = "source_vendor"
        elif token in vendor_tokens and clean_n:
            classification = "ambiguous_vendor_or_ticker"
        elif clean_n:
            classification = "ticker_candidate"
        else:
            classification = "dropped"

        dropped = max(raw_n - clean_n - vendor_n, 0)
        rows.append((token, classification, raw_n, clean_n, vendor_n, dropped,
                     "yes" if token in vendor_tokens else "no",
                     "yes" if token in exclusions else "no"))

    rows.sort(key=lambda r: (-r[2], r[0]))
    write_csv(path, ["token", "classification", "raw_count", "clean_count",
                     "vendor_count", "dropped_count", "in_vendor_list", "in_exclusion_list"],
              rows, logger)


def export_year_detection_report(conn: sqlite3.Connection, path: Path,
                                 logger: logging.Logger) -> None:
    """How every year value was arrived at, so year quality can be audited.

    row_type = combination            one row per (archive/folder/detected) combo
    row_type = filename_year_mention  how often each year appears in file names
    row_type = possible_maturity_year how often each year was parked as a maturity
    """
    cur = conn.cursor()
    rows = []

    cur.execute(
        """
        SELECT COALESCE(archive_year, '') AS ay,
               COALESCE(folder_year, '') AS fy,
               COALESCE(detected_year, '') AS dy,
               COALESCE(detected_month, '') AS dm,
               COUNT(*) AS n
          FROM documents
         GROUP BY ay, fy, dy, dm
         ORDER BY n DESC
        """
    )
    for r in cur.fetchall():
        rows.append(("combination", r["ay"] or "(none)", r["fy"] or "(none)",
                     r["dy"] or "(none)", r["dm"] or "(none)", "", r["n"]))

    for year, count in sorted(_token_counts(conn, "filename_year_mentions").items(),
                              key=lambda kv: (-kv[1], kv[0])):
        rows.append(("filename_year_mention", "", "", "", "", year, count))

    for year, count in sorted(_token_counts(conn, "possible_maturity_years").items(),
                              key=lambda kv: (-kv[1], kv[0])):
        rows.append(("possible_maturity_year", "", "", "", "", year, count))

    write_csv(path, ["row_type", "archive_year", "folder_year", "detected_year",
                     "detected_month", "year_value", "file_count"], rows, logger)


REVIEW_REPORT_COLUMNS = [
    "document_id", "review_severity", "review_category", "review_reason",
    "sensitive_context_guess", "sensitive_keyword_hits",
    "file_name", "relative_path", "archive_year", "detected_year",
    "date_confidence", "ticker_candidates_clean", "source_vendor_candidates",
    "source_type_guess", "document_type_guess", "duplicate_status", "file_path",
]


def export_review_report(conn: sqlite3.Connection, path: Path, where: str,
                         params: tuple, logger: logging.Logger, limit: int = 0) -> int:
    cur = conn.cursor()
    sql = (f"SELECT {', '.join(REVIEW_REPORT_COLUMNS)} FROM documents WHERE {where} "
           f"ORDER BY review_severity, relative_path")
    if limit:
        sql += f" LIMIT {int(limit)}"
    cur.execute(sql, params)
    return write_csv(path, REVIEW_REPORT_COLUMNS, (tuple(r) for r in cur.fetchall()), logger)


def export_date_confidence_report(conn: sqlite3.Connection, path: Path,
                                  logger: logging.Logger) -> None:
    """How confident we are in each row's year, and where the year came from.

    date_confidence:
      High    a daily folder date or the archive path year settled it
      Medium  only a full date inside the file name
      Low     only a loose year mention in the file name
      None    no year could be established
    """
    cur = conn.cursor()
    cur.execute(
        """
        SELECT COALESCE(date_confidence, 'None')      AS conf,
               COALESCE(detected_year_source, 'none') AS src,
               COALESCE(archive_year, '')             AS ay,
               COALESCE(detected_year, '')            AS dy,
               COUNT(*)                               AS n
          FROM documents
         GROUP BY conf, src, ay, dy
         ORDER BY n DESC
        """
    )
    rows = [(r["conf"], r["src"], r["ay"] or "(none)", r["dy"] or "(none)", r["n"])
            for r in cur.fetchall()]
    write_csv(path, ["date_confidence", "detected_year_source", "archive_year",
                     "detected_year", "file_count"], rows, logger)


def export_future_year_report(conn: sqlite3.Connection, path: Path, max_year: int,
                              logger: logging.Logger) -> int:
    """Rows whose detected_year is still in the future. This should be EMPTY.

    If anything appears here, a folder genuinely named for a future year got
    through, or a rule needs tightening. The file is written either way so the
    check is visible rather than assumed.
    """
    cur = conn.cursor()
    cur.execute(
        f"""
        SELECT {', '.join(REVIEW_REPORT_COLUMNS)},
               filename_year_mentions, possible_maturity_years, folder_date_guess, folder_year
          FROM documents
         WHERE COALESCE(detected_year, '') <> ''
           AND CAST(detected_year AS INTEGER) > ?
         ORDER BY detected_year DESC, relative_path
        """,
        (max_year,),
    )
    header = REVIEW_REPORT_COLUMNS + ["filename_year_mentions", "possible_maturity_years",
                                      "folder_date_guess", "folder_year"]
    return write_csv(path, header, (tuple(r) for r in cur.fetchall()), logger)


# --------------------------------------------------------------------------
# scan_summary.md
# --------------------------------------------------------------------------

def _count_query(conn: sqlite3.Connection, sql: str, params=()) -> list[tuple]:
    cur = conn.cursor()
    cur.execute(sql, params)
    return [tuple(r) for r in cur.fetchall()]


def _md_table(header: list[str], rows: list[tuple]) -> str:
    lines = ["| " + " | ".join(header) + " |",
             "|" + "|".join(["---"] * len(header)) + "|"]
    for row in rows:
        lines.append("| " + " | ".join("" if v is None else str(v) for v in row) + " |")
    if not rows:
        lines.append("| " + " | ".join(["(none)"] * len(header)) + " |")
    return "\n".join(lines)


def export_scan_summary(conn: sqlite3.Connection, path: Path, scan_context: dict,
                        dup_stats: dict, logger: logging.Logger) -> None:
    cur = conn.cursor()

    total = cur.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    needs_review = cur.execute("SELECT COUNT(*) FROM documents WHERE needs_review = 1").fetchone()[0]
    errors = cur.execute("SELECT COUNT(*) FROM documents WHERE duplicate_status = 'error'").fetchone()[0]
    total_bytes = cur.execute("SELECT COALESCE(SUM(file_size_bytes), 0) FROM documents").fetchone()[0]
    sensitive = cur.execute(
        "SELECT COUNT(*) FROM documents WHERE COALESCE(sensitive_keyword_hits,'') <> ''"
    ).fetchone()[0]
    restricted = cur.execute(
        "SELECT COUNT(*) FROM documents WHERE sensitive_context_guess = 'possible_restricted_data'"
    ).fetchone()[0]
    public_language = cur.execute(
        "SELECT COUNT(*) FROM documents WHERE sensitive_context_guess IN "
        "('public_company_language', 'research_business_language')"
    ).fetchone()[0]
    future_years = cur.execute(
        "SELECT COUNT(*) FROM documents WHERE COALESCE(detected_year,'') <> '' "
        "AND CAST(detected_year AS INTEGER) > ?", (datetime.now().year + 1,)
    ).fetchone()[0]
    high = cur.execute("SELECT COUNT(*) FROM documents WHERE review_severity = 'High'").fetchone()[0]
    medium = cur.execute("SELECT COUNT(*) FROM documents WHERE review_severity = 'Medium'").fetchone()[0]
    low = cur.execute("SELECT COUNT(*) FROM documents WHERE review_severity = 'Low'").fetchone()[0]

    by_ext = _count_query(conn, "SELECT file_extension, COUNT(*) FROM documents "
                                "GROUP BY file_extension ORDER BY COUNT(*) DESC")
    by_archive_year = _count_query(conn, "SELECT CASE WHEN COALESCE(archive_year,'') = '' THEN '(none)' "
                                         "ELSE archive_year END, COUNT(*) FROM documents GROUP BY 1 ORDER BY 1")
    by_year = _count_query(conn, "SELECT CASE WHEN COALESCE(detected_year,'') = '' THEN '(none)' "
                                 "ELSE detected_year END, COUNT(*) FROM documents GROUP BY 1 ORDER BY 1")
    by_month = _count_query(conn, "SELECT CASE WHEN COALESCE(detected_month,'') = '' THEN '(none)' "
                                  "ELSE detected_month END, COUNT(*) FROM documents GROUP BY 1 ORDER BY 1")
    by_source = _count_query(conn, "SELECT source_type_guess, COUNT(*) FROM documents "
                                   "GROUP BY source_type_guess ORDER BY COUNT(*) DESC")
    by_doc = _count_query(conn, "SELECT document_type_guess, COUNT(*) FROM documents "
                                "GROUP BY document_type_guess ORDER BY COUNT(*) DESC")
    by_theme = _count_query(conn, "SELECT strategy_theme_guess, COUNT(*) FROM documents "
                                  "GROUP BY strategy_theme_guess ORDER BY COUNT(*) DESC")
    by_conf = _count_query(conn, "SELECT COALESCE(ticker_confidence,'None'), COUNT(*) FROM documents "
                                 "GROUP BY 1 ORDER BY COUNT(*) DESC")
    by_date_conf = _count_query(conn, "SELECT COALESCE(date_confidence,'None'), COUNT(*) FROM documents "
                                      "GROUP BY 1 ORDER BY COUNT(*) DESC")
    by_year_source = _count_query(conn, "SELECT COALESCE(detected_year_source,'none'), COUNT(*) "
                                        "FROM documents GROUP BY 1 ORDER BY COUNT(*) DESC")
    by_context = _count_query(conn, "SELECT COALESCE(sensitive_context_guess,'none'), COUNT(*) "
                                    "FROM documents GROUP BY 1 ORDER BY COUNT(*) DESC")
    by_sev = _count_query(conn, "SELECT COALESCE(review_severity,'None'), COUNT(*) FROM documents "
                                "GROUP BY 1 ORDER BY COUNT(*) DESC")
    by_cat = _count_query(conn, "SELECT CASE WHEN COALESCE(review_category,'') = '' THEN '(none)' "
                                "ELSE review_category END, COUNT(*) FROM documents "
                                "GROUP BY 1 ORDER BY COUNT(*) DESC LIMIT 20")

    maturity = sorted(_token_counts(conn, "possible_maturity_years").items(),
                      key=lambda kv: (-kv[1], kv[0]))[:15]
    vendors = sorted(_token_counts(conn, "source_vendor_candidates").items(),
                     key=lambda kv: (-kv[1], kv[0]))[:20]
    clean_tickers = sorted(_token_counts(conn, "ticker_candidates_clean").items(),
                           key=lambda kv: (-kv[1], kv[0]))[:25]

    generated = datetime.now().replace(microsecond=0).isoformat()
    pct_review = (needs_review / total * 100) if total else 0.0

    lines = [
        "# Cutler Research Brain - Phase 1.2 Scan Summary",
        "",
        f"_Generated: {generated}_",
        "",
        "> Every classification below is an automated **guess** derived from file paths and",
        "> file names only. No document contents were opened, extracted or summarized.",
        "> The source archive is opened **read-only** and is never modified, moved, renamed or",
        "> written to. **AI drafts. Humans verify. Cutler decides.**",
        "",
        "## The four things to know before reading any number below",
        "",
        "1. **`detected_year` is controlled by the folder and archive path, not by file names.**",
        "   It is resolved folder date -> archive path year -> folder name -> file name, and the",
        "   result is clamped to a plausible window. For a scan of",
        "   `V:\\Companies\\A Current Month\\2026` that means 2026 for essentially every row.",
        "   `date_confidence` records how solid that year is and `detected_year_source` records",
        "   where it came from.",
        "2. **Future maturity years are separated, not used.** A year like 2028, 2029, 2030, 2032",
        "   or 2036 - whether it sits in a file name (\"Notes due 2029\") or a folder name",
        "   (\"XYZ Converts 2032\") - is recorded in `possible_maturity_years` and can never become",
        "   `detected_year`. Every year seen in a file name is still kept in",
        "   `filename_year_mentions`, so nothing is lost.",
        "3. **A sensitive keyword hit is not restricted data.** Words like portfolio, client,",
        "   account, performance, position and statement appear constantly in ordinary",
        "   public-company research titles (\"Expands Portfolio\", \"Financial Statements\",",
        "   \"performance-based equity award\", \"market position\"). Only strong phrases such as",
        "   \"client account\", \"portfolio holdings\", \"account statement\" or \"model portfolio\" are",
        "   graded High. Everything else is Low or Medium and is explained by",
        "   `sensitive_context_guess`. All hits stay visible in",
        "   `sensitive_data_review_report.csv` - severity is downgraded, rows are never hidden.",
        "4. **Everything ending in `_guess` is an automated guess** from paths and file names.",
        "   Ticker candidates are unverified, vendors are not tickers, and nothing here is an",
        "   investment conclusion.",
        "",
        "## How to read this index (Phase 1.1 columns)",
        "",
        "- **`detected_year` is the folder / archive year, not every year mentioned in a file name.**",
        "  It is resolved in strict order: the daily folder date (`6.22.2026`), then the archive path",
        "  year (the `2026` folder), then a full date inside the file name, and only then a plausible",
        "  loose year from the file name. A year that looks like a security maturity can never win.",
        "- **`filename_year_mentions`** keeps every year found in the file name, so nothing is lost.",
        "- **`possible_maturity_years`** captures future / security maturity years such as 2028, 2029,",
        "  2030, 2032 and 2036 - typically bond and convertible maturities like \"Notes due 2029\".",
        "  These are recorded for reference and are blocked from becoming `detected_year`.",
        "- **`archive_year` / `archive_month`** come from the scan path itself.",
        "  **`folder_year` / `folder_month`** come from the folders below it.",
        "- **Ticker candidates are still unverified.** `ticker_candidates_raw` is the audit trail,",
        "  `ticker_candidates_clean` is the reviewed-by-a-human-next column, and `ticker_confidence`",
        "  is High / Medium / Low / None based on where the token sat in the file name.",
        "- **`source_vendor_candidates` are research providers, not company tickers by default**",
        "  (JPM, GS, UBS, RBC, KBW, Bloomberg Intelligence and so on). Several are also real listed",
        "  tickers, so they are parked in their own column rather than deleted.",
        "- **`review_severity`** turns the review queue into actual work: High first, then Medium.",
        "  Low is mostly classification gaps that get fixed by editing `configs\\source_type_rules.yaml`.",
        "- Every `*_guess` column is an automated guess only. Nothing here is an investment conclusion.",
        "",
        "## Run details",
        "",
        f"- Source scanned (read-only): `{scan_context.get('source_root', 'n/a')}`",
        f"- Archive year from path: {scan_context.get('archive_year') or '(none)'}",
        f"- Project root: `{scan_context.get('project_root', 'n/a')}`",
        f"- Scan finished: {scan_context.get('processed_at', 'n/a')}",
        f"- Scan duration: {scan_context.get('scan_seconds', 'n/a')} seconds",
        f"- Files indexed this run: {scan_context.get('files_indexed_this_run', 'n/a')}",
        f"- Files with errors this run: {scan_context.get('files_with_errors_this_run', 'n/a')}",
        f"- Folders visited: {scan_context.get('folders_visited', 'n/a')} "
        f"(skipped: {scan_context.get('folders_skipped', 'n/a')}, "
        f"errors: {scan_context.get('folder_errors', 'n/a')})",
        f"- Files seen: {scan_context.get('files_seen', 'n/a')} "
        f"(unsupported types skipped: {scan_context.get('files_unsupported', 'n/a')})",
        f"- Classification rules: `{scan_context.get('classification_rules', 'n/a')}`",
        f"- Ticker rules: `{scan_context.get('ticker_rules', 'n/a')}`",
        "",
        "## Index totals (whole database, all scans)",
        "",
        f"- **Total documents indexed: {total:,}**",
        f"- Total bytes catalogued: {total_bytes:,}",
        f"- Needs review (any severity): {needs_review:,} ({pct_review:.1f}%)",
        f"  - High: {high:,}  |  Medium: {medium:,}  |  Low: {low:,}",
        f"- Duplicate groups: {dup_stats.get('duplicate_groups', 0):,}",
        f"- Duplicate files (copies beyond the original): {dup_stats.get('duplicate_files', 0):,}",
        f"- Rows with read/hash errors: {errors:,}",
        f"- Rows containing any sensitive keyword: {sensitive:,}",
        f"  - graded restricted (High): {restricted:,}",
        f"  - ordinary public-company / research language (Low): {public_language:,}",
        f"- Rows with a future `detected_year` (should be 0): **{future_years:,}**",
        f"- Excluded from Phase 2 PDF extraction (`review_severity` High): "
        f"{cur.execute('SELECT COUNT(*) FROM documents WHERE exclude_from_phase2_extraction = 1').fetchone()[0]:,}",
        "",
        "## Review severity",
        "",
        _md_table(["Severity", "Files"], by_sev),
        "",
        "## Date confidence",
        "",
        _md_table(["Date confidence", "Files"], by_date_conf),
        "",
        "## Where detected_year came from",
        "",
        _md_table(["detected_year_source", "Files"], by_year_source),
        "",
        "## Sensitive context",
        "",
        _md_table(["sensitive_context_guess", "Files"], by_context),
        "",
        "## Review categories",
        "",
        _md_table(["Category", "Files"], by_cat),
        "",
        "## Files by archive year (from the scan path)",
        "",
        _md_table(["Archive year", "Files"], by_archive_year),
        "",
        "## Files by detected year (folder / archive year)",
        "",
        _md_table(["Detected year", "Files"], by_year),
        "",
        "## Files by detected month",
        "",
        _md_table(["Detected month", "Files"], by_month),
        "",
        "## Possible maturity years found in file names (NOT document years)",
        "",
        _md_table(["Year", "Files"], maturity),
        "",
        "## Files by extension",
        "",
        _md_table(["Extension", "Files"], by_ext),
        "",
        "## Files by source type guess",
        "",
        _md_table(["Source type guess", "Files"], by_source),
        "",
        "## Files by document type guess",
        "",
        _md_table(["Document type guess", "Files"], by_doc),
        "",
        "## Files by strategy theme guess",
        "",
        _md_table(["Strategy theme guess", "Files"], by_theme),
        "",
        "## Ticker confidence",
        "",
        _md_table(["Confidence", "Files"], by_conf),
        "",
        "## Top clean ticker candidates (unverified)",
        "",
        _md_table(["Ticker candidate", "Files"], clean_tickers),
        "",
        "## Top source / vendor candidates (research providers, not tickers)",
        "",
        _md_table(["Vendor candidate", "Files"], vendors),
        "",
        "## What a human should check next",
        "",
        "1. `high_review_report.csv` - everything graded High. Start here; it should now be a",
        "   short, genuinely restricted-looking list.",
        "2. `sensitive_data_review_report.csv` - every sensitive keyword hit with its",
        "   `sensitive_context_guess`. Confirm the `possible_restricted_data` rows and spot-check",
        "   `needs_manual_check`.",
        "3. `public_language_sensitive_false_positive_report.csv` - the rows Phase 1.2 downgraded",
        "   as ordinary company language. Skim it to confirm nothing real was downgraded; anything",
        "   that should be High goes into `sensitive_strong_phrases`.",
        "4. `remaining_future_detected_years_report.csv` - should be empty. If it is not, a folder",
        "   is genuinely named for a future year.",
        "5. `date_confidence_report.csv` - anything below High is worth a glance.",
        "6. `duplicate_report.csv` - decide which copy of each duplicate is canonical.",
        "7. `ticker_noise_report.csv` - move false positives into `exclusions`, research providers",
        "   into `vendor_tokens` / `strong_vendor_tokens`, and real names into `protected_tickers`",
        "   in `configs\\ticker_rules.yaml`.",
        "8. `sample_unknown_source_files.csv` and `sample_unknown_document_type_files.csv` - add the",
        "   missing keywords or folder rules to `configs\\source_type_rules.yaml`, then re-run.",
        "",
        "_Nothing in this report is an investment conclusion._",
        "",
    ]

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    logger.info("Wrote %s", path)


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------

def load_records(records_path: Path, logger: logging.Logger) -> list[dict]:
    """Read data/raw_scan.jsonl. A malformed line is skipped, never fatal."""
    if not records_path.exists():
        raise SystemExit(f"ERROR: raw scan records not found: {records_path}\n"
                         "Run scripts/scan_source_archive.py first.")

    records: list[dict] = []
    bad = 0
    with records_path.open("r", encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                bad += 1
                logger.warning("Skipping malformed record on line %d of %s (%s)",
                               line_no, records_path.name, exc)
    if bad:
        logger.warning("%d malformed record line(s) skipped.", bad)
    logger.info("Loaded %d scan records from %s", len(records), records_path)
    return records


def load_rules(project_root: Path, logger: logging.Logger) -> tuple[dict, dict]:
    """Load both config files (best effort) for grading and the noise report."""
    source_rules: dict = {}
    ticker_rules: dict = {}
    try:
        import yaml  # type: ignore
        for name, target in (("source_type_rules.yaml", "source"), ("ticker_rules.yaml", "ticker")):
            path = project_root / "configs" / name
            if not path.exists():
                continue
            with path.open("r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
            if target == "source":
                source_rules = data
            else:
                ticker_rules = data
    except ImportError:
        logger.warning("PyYAML not installed - using built-in severity and vendor defaults.")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not read config files (%s) - using built-in defaults.", exc)
    return source_rules, ticker_rules


def build_index(project_root: Path, records_path: Path, scan_context: dict | None = None,
                reset: bool = False, logger: logging.Logger | None = None,
                source_rules: dict | None = None) -> dict:
    """Build the SQLite index and every CSV / Markdown report."""
    log = logger or DEFAULT_LOGGER
    scan_context = scan_context or {}

    project_root = Path(project_root).resolve()
    data_dir = project_root / "data"
    index_dir = project_root / "02_Metadata_Index"
    data_dir.mkdir(parents=True, exist_ok=True)
    index_dir.mkdir(parents=True, exist_ok=True)

    db_path = data_dir / DB_FILENAME

    file_source_rules, ticker_rules = load_rules(project_root, log)
    rules = source_rules or file_source_rules or {}
    severity_map = rules.get("review_severity_map") or DEFAULT_SEVERITY_MAP
    category_order = rules.get("review_category_order") or DEFAULT_CATEGORY_ORDER

    records = load_records(Path(records_path), log)

    conn = connect(db_path)
    try:
        create_schema(conn, reset=reset, logger=log)
        if records:
            upsert_records(conn, records, log)
        dup_stats = resolve_duplicates(conn, log, severity_map, category_order)
        excluded_from_extraction = refresh_extraction_gate(conn, log)

        total = export_manifest(
            conn,
            [data_dir / "document_manifest.csv", index_dir / "document_manifest.csv"],
            log,
        )
        export_duplicate_report(conn, index_dir / "duplicate_report.csv", log)
        export_source_type_report(conn, index_dir / "source_type_report.csv", log)
        export_ticker_report(conn, index_dir / "ticker_candidate_report.csv", log)

        # ---- Phase 1.1 / 1.2 review reports -------------------------------
        # Every row with ANY sensitive word stays visible here, whatever its
        # severity - the context guess is what a human triages on.
        export_review_report(
            conn, index_dir / "sensitive_data_review_report.csv",
            "COALESCE(sensitive_keyword_hits, '') <> ''", (), log,
        )
        export_review_report(
            conn, index_dir / "high_review_report.csv",
            "review_severity = 'High'", (), log,
        )
        # Rows that Phase 1.1 would have called High and Phase 1.2 downgraded.
        export_review_report(
            conn, index_dir / "public_language_sensitive_false_positive_report.csv",
            "sensitive_context_guess IN ('public_company_language', 'research_business_language')",
            (), log,
        )
        export_date_confidence_report(conn, index_dir / "date_confidence_report.csv", log)
        future_rows = export_future_year_report(
            conn, index_dir / "remaining_future_detected_years_report.csv",
            datetime.now().year + 1, log,
        )
        if future_rows:
            log.warning("%d row(s) still have a future detected_year - see "
                        "remaining_future_detected_years_report.csv", future_rows)
        export_ticker_noise_report(conn, index_dir / "ticker_noise_report.csv", ticker_rules, log)
        export_year_detection_report(conn, index_dir / "year_detection_report.csv", log)
        export_review_report(
            conn, index_dir / "sample_unknown_source_files.csv",
            "source_type_guess = 'Unknown'", (), log, limit=SAMPLE_ROWS,
        )
        export_review_report(
            conn, index_dir / "sample_unknown_document_type_files.csv",
            "document_type_guess = 'Unknown'", (), log, limit=SAMPLE_ROWS,
        )

        export_scan_summary(conn, index_dir / "scan_summary.md", scan_context, dup_stats, log)

        # Audit row for this run.
        conn.execute(
            """
            INSERT INTO scan_runs
                (processed_at, source_root, project_root, files_indexed,
                 files_with_errors, scan_seconds, details_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                scan_context.get("processed_at", datetime.now().isoformat(timespec="seconds")),
                scan_context.get("source_root", ""),
                str(project_root),
                scan_context.get("files_indexed_this_run", len(records)),
                scan_context.get("files_with_errors_this_run", 0),
                scan_context.get("scan_seconds", 0.0),
                json.dumps(scan_context, ensure_ascii=False),
            ),
        )
        conn.commit()

        counts = {
            row[0] or "None": row[1]
            for row in conn.execute(
                "SELECT COALESCE(review_severity,'None'), COUNT(*) FROM documents GROUP BY 1"
            )
        }
        needs_review = conn.execute(
            "SELECT COUNT(*) FROM documents WHERE needs_review = 1"
        ).fetchone()[0]
    finally:
        conn.close()

    log.info("=" * 78)
    log.info("METADATA INDEX BUILT")
    log.info("  database        : %s", db_path)
    log.info("  documents rows  : %d", total)
    log.info("  needs review    : %d  (High %d | Medium %d | Low %d)",
             needs_review, counts.get("High", 0), counts.get("Medium", 0), counts.get("Low", 0))
    log.info("  duplicate files : %d", dup_stats.get("duplicate_files", 0))
    log.info("  reports         : %s", index_dir)
    log.info("=" * 78)

    return {
        "db_path": str(db_path),
        "total_rows": total,
        "needs_review": needs_review,
        "severity_counts": counts,
        "excluded_from_phase2_extraction": excluded_from_extraction,
        **dup_stats,
    }


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Cutler Research Brain Phase 1.2 - build the SQLite metadata index and reports.",
    )
    parser.add_argument(
        "--project-root", required=True,
        help=r'Project root, e.g. "C:\Users\AI Admin\Documents\CutlerResearchBrain"',
    )
    parser.add_argument(
        "--records", default=None,
        help="Path to raw scan records (default: <project-root>/data/raw_scan.jsonl)",
    )
    parser.add_argument(
        "--reset", action="store_true",
        help="Drop and rebuild the documents table instead of updating it in place.",
    )
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)-8s %(message)s",
        stream=sys.stdout,
    )

    project_root = Path(args.project_root).expanduser().resolve()
    records_path = Path(args.records) if args.records else project_root / "data" / "raw_scan.jsonl"

    build_index(project_root, records_path, scan_context={"project_root": str(project_root)},
                reset=args.reset, logger=DEFAULT_LOGGER)
    return 0


if __name__ == "__main__":
    sys.exit(main())
