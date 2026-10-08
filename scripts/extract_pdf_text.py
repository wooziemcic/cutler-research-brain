#!/usr/bin/env python3
# =============================================================================
# Cutler Research Brain - Phase 2
# extract_pdf_text.py
#
# Page-level PDF text extraction with PyMuPDF, driven by the Phase 1 metadata
# index. Reads PDFs from the V Drive READ-ONLY and writes plain text only into
# 03_Extracted_Text inside the project root.
#
# What it does:
#   1. Selects eligible PDF rows from the documents table.
#   2. Opens each PDF read-only with PyMuPDF and pulls the text of every page.
#   3. Writes page_0001.txt ... , a combined document_text.txt, and an
#      extraction_metadata.json per document, under <document_id> folders.
#   4. Measures extraction QUALITY only - characters per page, empty pages,
#      a scanned-PDF suspicion flag and a crude table-heavy guess.
#   5. Records everything in the pdf_extractions table and the reports built by
#      refresh_extraction_reports.py.
#
# What it deliberately does NOT do:
#   - summarize, paraphrase or interpret any document
#   - classify investment meaning or draw any conclusion
#   - call OpenAI, Claude or any external API
#   - create embeddings, chunks or a vector index
#   - copy source PDFs anywhere; only extracted TEXT is written locally
#   - write anything at all to the V Drive
#
# Rows graded High review in Phase 1 (client/portfolio-looking material and
# read errors) are excluded by default via exclude_from_phase2_extraction.
#
# PHASE 2.1: extraction_status and extraction_quality are separate axes.
# A skipped row is written with quality "Not Applicable" - it was never
# attempted. "Failed" means a genuine failed extraction attempt, and only that.
#
# Principle: AI drafts. Humans verify. Cutler decides.
#
# Usage:
#   python scripts\extract_pdf_text.py ^
#       --db "data\document_metadata.sqlite" ^
#       --project-root "C:\Users\AI Admin\Documents\CutlerResearchBrain"
# =============================================================================

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

EXTRACTOR_NAME = "PyMuPDF"
PIPELINE_VERSION = "phase2-1.1"

# ---- extraction_status values ----------------------------------------------
STATUS_EXTRACTED = "extracted"
STATUS_SKIPPED_HIGH = "skipped_high_review"
STATUS_SKIPPED_DUPLICATE = "skipped_duplicate"
STATUS_SKIPPED_NON_PDF = "skipped_non_pdf"
STATUS_SKIPPED_MISSING = "skipped_missing_file"
STATUS_FAILED = "failed"
STATUS_PARTIAL = "partial"

SKIP_STATUSES = {
    STATUS_SKIPPED_HIGH, STATUS_SKIPPED_DUPLICATE,
    STATUS_SKIPPED_NON_PDF, STATUS_SKIPPED_MISSING,
}

# ---- extraction_quality values ---------------------------------------------
QUALITY_HIGH = "High"
QUALITY_MEDIUM = "Medium"
QUALITY_LOW = "Low"
QUALITY_EMPTY = "Empty"
QUALITY_FAILED = "Failed"
# Phase 2.1: a skipped row was never attempted, so it has no quality to report.
# "Failed" is reserved for a genuine failed extraction attempt.
QUALITY_NOT_APPLICABLE = "Not Applicable"

# Quality thresholds, in characters per extracted page.
QUALITY_HIGH_MIN = 1200
QUALITY_MEDIUM_MIN = 300

# A page with fewer than this many characters counts as empty.
EMPTY_PAGE_CHARS = 10

# Scanned-PDF suspicion.
SCANNED_CHARS_PER_PAGE = 50
SCANNED_EMPTY_RATIO = 0.5

# Table-heavy guess.
TABLE_LINE_RATIO = 0.25
TABLE_MIN_LINES = 10

PAGE_SEPARATOR = "\n\n===== [ page {page} ] =====\n\n"

PDF_EXTRACTION_COLUMNS = [
    "document_id",
    "file_hash_sha256",
    "extraction_status",
    "extraction_quality",
    "page_count",
    "pages_extracted",
    "total_text_chars",
    "avg_text_chars_per_page",
    "scanned_pdf_suspected",
    "empty_pages",
    "failed_pages",
    "table_heavy_guess",
    "output_folder",
    "document_text_path",
    "metadata_json_path",
    "error_message",
    "extracted_at",
]

INTEGER_EXTRACTION_COLUMNS = {
    "page_count", "pages_extracted", "total_text_chars",
    "scanned_pdf_suspected", "empty_pages", "failed_pages", "table_heavy_guess",
}

LOGGER = logging.getLogger("phase2_pdf_extraction")


# --------------------------------------------------------------------------
# PyMuPDF import
# --------------------------------------------------------------------------

def load_pymupdf():
    """Import PyMuPDF under either module name, with a clear error if missing."""
    try:
        import pymupdf  # noqa: PLC0415 - modern module name (>= 1.24.3)
        return pymupdf
    except ImportError:
        pass
    try:
        import fitz  # noqa: PLC0415 - legacy module name
        return fitz
    except ImportError as exc:
        raise SystemExit(
            "ERROR: PyMuPDF is not installed.\n"
            "  pip install -r requirements.txt\n"
            f"  ({exc})"
        ) from exc


# --------------------------------------------------------------------------
# Logging
# --------------------------------------------------------------------------

def setup_logging(log_path: Path, verbose: bool = False) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)

    LOGGER.setLevel(logging.DEBUG)
    LOGGER.handlers.clear()

    file_handler = logging.FileHandler(log_path, mode="a", encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s  %(levelname)-8s  %(message)s", "%Y-%m-%d %H:%M:%S")
    )
    LOGGER.addHandler(file_handler)

    console = logging.StreamHandler(stream=sys.stdout)
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    console.setFormatter(logging.Formatter("%(levelname)-8s %(message)s"))
    LOGGER.addHandler(console)


# --------------------------------------------------------------------------
# Path safety (same guarantees as Phase 1)
# --------------------------------------------------------------------------

def os_path(path: Path) -> str:
    """Long-path-safe string for Windows."""
    raw = str(path)
    if os.name != "nt" or raw.startswith("\\\\?\\") or len(raw) < 240:
        return raw
    if raw.startswith("\\\\"):
        return "\\\\?\\UNC\\" + raw[2:]
    return "\\\\?\\" + raw


def is_relative_to(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except (ValueError, OSError):
        return False


def validate_output_root(project_root: Path, source_roots: list[str]) -> None:
    """Refuse to run if anything could be written to the research archive."""
    drive = os.path.splitdrive(str(project_root.resolve()))[0].upper().rstrip(":")
    if drive == "V":
        raise SystemExit(
            f"ERROR: --project-root is on the V Drive ({project_root}). "
            "The V Drive is read-only. Aborting."
        )

    for root in source_roots:
        if not root:
            continue
        source = Path(root)
        if is_relative_to(project_root, source):
            raise SystemExit(
                "ERROR: --project-root is inside the scanned source archive "
                f"({source}). Extracted text would land on the archive. Aborting."
            )


def assert_inside_project(path: Path, project_root: Path) -> None:
    if not is_relative_to(path, project_root):
        raise SystemExit(f"ERROR: refusing to write outside the project root: {path}")


SAFE_ID = re.compile(r"^[A-Za-z0-9_.-]+$")


def safe_component(value: str, fallback: str) -> str:
    """Folder names come from document_id / archive_year only - never file names.

    File names in the archive are long, non-ASCII and sometimes hostile to
    Windows paths, so they are never used to build a path.
    """
    value = (value or "").strip()
    return value if value and SAFE_ID.match(value) else fallback


# --------------------------------------------------------------------------
# Database
# --------------------------------------------------------------------------

def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    # Phase 9.1. This script is launched as a subprocess by the incremental
    # ingestion runner, so another process may be mid-write when it starts.
    # Without a busy timeout SQLite gives up instantly and the run dies at its
    # first CREATE TABLE - which reads like a schema fault and is not one.
    # Sixty seconds is far longer than any write this pipeline makes.
    conn.execute("PRAGMA busy_timeout = 60000")
    return conn


def table_columns(conn: sqlite3.Connection, table: str) -> set:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def create_extraction_schema(conn: sqlite3.Connection) -> None:
    """pdf_extractions plus the pdf_* columns on documents (added if missing)."""
    defs = []
    for col in PDF_EXTRACTION_COLUMNS:
        if col == "document_id":
            defs.append("document_id TEXT PRIMARY KEY")
        elif col == "avg_text_chars_per_page":
            defs.append("avg_text_chars_per_page REAL")
        elif col in INTEGER_EXTRACTION_COLUMNS:
            defs.append(f"{col} INTEGER")
        else:
            defs.append(f"{col} TEXT")

    conn.execute(f"CREATE TABLE IF NOT EXISTS pdf_extractions ({', '.join(defs)})")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_extractions_status "
                 "ON pdf_extractions(extraction_status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_extractions_quality "
                 "ON pdf_extractions(extraction_quality)")

    # Phase 2 columns on documents, for anyone reading only that table.
    existing = table_columns(conn, "documents")
    wanted = [
        ("exclude_from_phase2_extraction", "INTEGER"),
        ("pdf_extraction_status", "TEXT"),
        ("pdf_extraction_quality", "TEXT"),
        ("pdf_page_count", "INTEGER"),
        ("pdf_total_text_chars", "INTEGER"),
        ("pdf_extracted_at", "TEXT"),
    ]
    added = []
    for col, col_type in wanted:
        if col not in existing:
            conn.execute(f"ALTER TABLE documents ADD COLUMN {col} {col_type}")
            added.append(col)
    if added:
        LOGGER.info("Added Phase 2 column(s) to documents: %s", ", ".join(added))

    # If the gate was never computed (documents table built before Phase 2),
    # derive it now so the eligibility rules below have something to read.
    conn.execute(
        """
        UPDATE documents
           SET exclude_from_phase2_extraction =
               CASE WHEN review_severity = 'High' THEN 1 ELSE 0 END
         WHERE exclude_from_phase2_extraction IS NULL
        """
    )
    conn.commit()


def select_candidates(conn: sqlite3.Connection, only_document_id: str = "") -> list[sqlite3.Row]:
    """Every PDF row in the index. Eligibility is decided per row afterwards."""
    columns = table_columns(conn, "documents")
    wanted = ["document_id", "file_name", "file_extension", "file_path", "relative_path",
              "file_hash_sha256", "duplicate_status", "review_severity", "review_category",
              "sensitive_context_guess", "source_type_guess", "document_type_guess",
              "archive_year", "detected_year", "ticker_candidates_clean",
              "source_vendor_candidates", "exclude_from_phase2_extraction"]
    select = ", ".join(c for c in wanted if c in columns)

    if only_document_id:
        sql = f"SELECT {select} FROM documents WHERE document_id = ?"
        return list(conn.execute(sql, (only_document_id,)))

    sql = (f"SELECT {select} FROM documents WHERE LOWER(COALESCE(file_extension,'')) = '.pdf' "
           f"ORDER BY relative_path")
    return list(conn.execute(sql))


def upsert_extraction(conn: sqlite3.Connection, record: dict) -> None:
    placeholders = ", ".join("?" for _ in PDF_EXTRACTION_COLUMNS)
    conn.execute(
        f"INSERT OR REPLACE INTO pdf_extractions ({', '.join(PDF_EXTRACTION_COLUMNS)}) "
        f"VALUES ({placeholders})",
        tuple(record.get(col) for col in PDF_EXTRACTION_COLUMNS),
    )
    conn.execute(
        """
        UPDATE documents
           SET pdf_extraction_status = ?, pdf_extraction_quality = ?,
               pdf_page_count = ?, pdf_total_text_chars = ?, pdf_extracted_at = ?
         WHERE document_id = ?
        """,
        (record.get("extraction_status"), record.get("extraction_quality"),
         record.get("page_count"), record.get("total_text_chars"),
         record.get("extracted_at"), record.get("document_id")),
    )


# --------------------------------------------------------------------------
# Quality heuristics - measurement only, never interpretation
# --------------------------------------------------------------------------

def grade_quality(total_chars: int, pages_extracted: int, empty_pages: int,
                  failed: bool) -> tuple[str, float]:
    """(extraction_quality, avg_text_chars_per_page). Purely mechanical."""
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


def suspect_scanned(page_count: int, total_chars: int, empty_pages: int,
                    pages_extracted: int) -> bool:
    """A PDF with pages but almost no extractable text is probably an image scan.

    This is a flag for a human, not a conclusion: it says "text extraction found
    very little here", which usually means the pages are images and would need
    OCR (not part of Phase 2).
    """
    if page_count <= 0:
        return False
    if total_chars < SCANNED_CHARS_PER_PAGE * page_count:
        return True
    if pages_extracted and (empty_pages / pages_extracted) >= SCANNED_EMPTY_RATIO:
        return True
    return False


RE_MULTI_SPACE = re.compile(r"\S {2,}\S")
RE_NUMERIC_TOKEN = re.compile(r"[-+(]?\$?\d[\d,.]*%?\)?")


def guess_table_heavy(text: str) -> bool:
    """Crude 'this page looks like a table' guess. Deliberately simple.

    Counts lines that either contain pipes, or are short with several numeric
    tokens, or hold column-like runs of whitespace. No parsing, no meaning.
    """
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) < TABLE_MIN_LINES:
        return False

    table_like = 0
    for line in lines:
        stripped = line.strip()
        if "|" in stripped or "\t" in line:
            table_like += 1
            continue
        numbers = len(RE_NUMERIC_TOKEN.findall(stripped))
        if numbers >= 2 and len(stripped) < 120:
            table_like += 1
            continue
        if RE_MULTI_SPACE.search(line) and numbers >= 1:
            table_like += 1

    return (table_like / len(lines)) >= TABLE_LINE_RATIO


# --------------------------------------------------------------------------
# Extraction
# --------------------------------------------------------------------------

def extract_one(pdf_module, source_path: Path, out_dir: Path, max_pages: int) -> dict:
    """Extract page text from one PDF. Never raises - failures come back as data."""
    pages_dir = out_dir / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)

    result = {
        "page_count": 0,
        "pages_extracted": 0,
        "total_text_chars": 0,
        "empty_pages": 0,
        "failed_pages": 0,
        "truncated": False,
        "error_message": "",
        "status": STATUS_EXTRACTED,
        "text_sample": "",
    }

    doc = None
    try:
        doc = pdf_module.open(os_path(source_path))
    except Exception as exc:  # noqa: BLE001 - corrupt / unreadable file
        result["status"] = STATUS_FAILED
        result["error_message"] = f"could not open PDF: {type(exc).__name__}: {exc}"
        return result

    try:
        # Password-protected files: try the empty password, then give up cleanly.
        if getattr(doc, "needs_pass", False):
            if not doc.authenticate(""):
                result["status"] = STATUS_FAILED
                result["error_message"] = "encrypted PDF: password required"
                return result

        result["page_count"] = int(getattr(doc, "page_count", 0) or 0)
        if result["page_count"] == 0:
            result["status"] = STATUS_FAILED
            result["error_message"] = "PDF reports zero pages"
            return result

        limit = min(result["page_count"], max_pages) if max_pages else result["page_count"]
        result["truncated"] = limit < result["page_count"]

        combined = []
        sample_chars = 0
        for index in range(limit):
            page_no = index + 1
            try:
                text = doc[index].get_text("text") or ""
            except Exception as exc:  # noqa: BLE001 - one bad page, keep going
                result["failed_pages"] += 1
                LOGGER.warning("  page %d failed in %s: %s", page_no, source_path.name, exc)
                continue

            page_path = pages_dir / f"page_{page_no:04d}.txt"
            page_path.write_text(text, encoding="utf-8", errors="replace")

            result["pages_extracted"] += 1
            result["total_text_chars"] += len(text)
            if len(text.strip()) < EMPTY_PAGE_CHARS:
                result["empty_pages"] += 1

            combined.append(PAGE_SEPARATOR.format(page=page_no))
            combined.append(text)

            if sample_chars < 60000:  # cap the text used for the table guess
                result["text_sample"] += text
                sample_chars += len(text)

        (out_dir / "document_text.txt").write_text(
            "".join(combined), encoding="utf-8", errors="replace"
        )

        if result["pages_extracted"] == 0:
            result["status"] = STATUS_FAILED
            if not result["error_message"]:
                result["error_message"] = "no pages could be read"
        elif result["failed_pages"] or result["truncated"]:
            result["status"] = STATUS_PARTIAL
            if result["truncated"] and not result["error_message"]:
                result["error_message"] = f"stopped at --max-pages ({limit} of {result['page_count']})"
    except Exception as exc:  # noqa: BLE001 - anything unexpected mid-document
        result["status"] = STATUS_FAILED
        result["error_message"] = f"{type(exc).__name__}: {exc}"
    finally:
        if doc is not None:
            try:
                doc.close()
            except Exception:  # noqa: BLE001
                pass

    # Nothing came out: leave no empty pages folder behind. The
    # extraction_metadata.json still gets written, so the failure is auditable.
    if result["pages_extracted"] == 0:
        try:
            pages_dir.rmdir()
        except OSError:
            pass

    return result


def build_record(row: sqlite3.Row, status: str, quality: str, out_dir: Path | None,
                 extracted: dict | None, project_root: Path, error: str = "") -> dict:
    """One pdf_extractions row - for extractions AND for skips."""
    extracted = extracted or {}
    now = datetime.now().replace(microsecond=0).isoformat()

    def rel(path: Path | None) -> str:
        if path is None:
            return ""
        try:
            return str(path.relative_to(project_root))
        except ValueError:
            return str(path)

    pages_extracted = int(extracted.get("pages_extracted", 0) or 0)
    total_chars = int(extracted.get("total_text_chars", 0) or 0)
    page_count = int(extracted.get("page_count", 0) or 0)
    empty_pages = int(extracted.get("empty_pages", 0) or 0)
    avg = total_chars / pages_extracted if pages_extracted else 0.0

    return {
        "document_id": row["document_id"],
        "file_hash_sha256": row["file_hash_sha256"] if "file_hash_sha256" in row.keys() else "",
        "extraction_status": status,
        "extraction_quality": quality,
        "page_count": page_count,
        "pages_extracted": pages_extracted,
        "total_text_chars": total_chars,
        "avg_text_chars_per_page": round(avg, 1),
        "scanned_pdf_suspected": 1 if extracted.get("scanned_pdf_suspected") else 0,
        "empty_pages": empty_pages,
        "failed_pages": int(extracted.get("failed_pages", 0) or 0),
        "table_heavy_guess": 1 if extracted.get("table_heavy_guess") else 0,
        "output_folder": rel(out_dir),
        "document_text_path": rel(out_dir / "document_text.txt") if out_dir else "",
        "metadata_json_path": rel(out_dir / "extraction_metadata.json") if out_dir else "",
        "error_message": error or extracted.get("error_message", ""),
        "extracted_at": now,
    }


def write_metadata_json(path: Path, row: sqlite3.Row, record: dict) -> None:
    """extraction_metadata.json next to the extracted text."""
    keys = row.keys()
    payload = {
        "document_id": record["document_id"],
        "file_name": row["file_name"] if "file_name" in keys else "",
        "relative_path": row["relative_path"] if "relative_path" in keys else "",
        "source_file_path": row["file_path"] if "file_path" in keys else "",
        "file_hash_sha256": record["file_hash_sha256"],
        "page_count": record["page_count"],
        "pages_extracted": record["pages_extracted"],
        "total_text_chars": record["total_text_chars"],
        "avg_text_chars_per_page": record["avg_text_chars_per_page"],
        "extraction_status": record["extraction_status"],
        "extraction_quality": record["extraction_quality"],
        "scanned_pdf_suspected": bool(record["scanned_pdf_suspected"]),
        "empty_pages": record["empty_pages"],
        "failed_pages": record["failed_pages"],
        "table_heavy_guess": bool(record["table_heavy_guess"]),
        "extracted_at": record["extracted_at"],
        "extractor": EXTRACTOR_NAME,
        "extractor_version": record.get("extractor_version", ""),
        "pipeline_version": PIPELINE_VERSION,
        "error_message": record["error_message"],
        "note": ("Text extraction only. No summarization, interpretation or investment "
                 "conclusion was performed. Source PDF was opened read-only and never modified."),
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


# Reports (Phase 2.1: one implementation, shared with the repair command)
# --------------------------------------------------------------------------
# All report writing lives in refresh_extraction_reports.py so that an
# extraction run and `python scripts\refresh_extraction_reports.py` always
# produce byte-identical reports.


def write_reports(conn: sqlite3.Connection, project_root: Path, run_stats: dict) -> None:
    """Normalize quality values, then rebuild every Phase 2 report."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        import refresh_extraction_reports  # noqa: PLC0415 - intentional late import
    except ImportError as exc:
        LOGGER.error("Could not import refresh_extraction_reports.py (%s). "
                     "Extraction results are safe in the database; run "
                     "scripts/refresh_extraction_reports.py to build the reports.", exc)
        return

    refresh_extraction_reports.normalize_quality(conn, LOGGER)
    refresh_extraction_reports.write_all_reports(conn, project_root, run_stats, LOGGER)


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Cutler Research Brain Phase 2 - page-level PDF text extraction (PyMuPDF).",
    )
    parser.add_argument("--db", required=True,
                        help=r'SQLite index, e.g. "data\document_metadata.sqlite"')
    parser.add_argument("--project-root", required=True,
                        help=r'Project root, e.g. "C:\Users\AI Admin\Documents\CutlerResearchBrain"')
    parser.add_argument("--limit", type=int, default=0,
                        help="Stop after N documents are processed (0 = no limit).")
    parser.add_argument("--force", action="store_true",
                        help="Re-extract documents that already have output, and allow duplicates.")
    parser.add_argument("--include-high-review", action="store_true",
                        help="Also extract rows graded High review / excluded from Phase 2. "
                             "These may contain client or portfolio material - use deliberately.")
    parser.add_argument("--include-duplicates", action="store_true",
                        help="Extract duplicate rows too (implied by --force).")
    parser.add_argument("--only-document-id", default="",
                        help="Extract a single document_id (debugging).")
    parser.add_argument("--max-pages", type=int, default=0,
                        help="Extract at most N pages per PDF (0 = all). Marks the row partial.")
    parser.add_argument("--verbose", action="store_true", help="Chattier console output.")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    project_root = Path(args.project_root).expanduser().resolve()
    db_path = Path(args.db).expanduser()
    if not db_path.is_absolute():
        db_path = (project_root / db_path).resolve()

    if not db_path.exists():
        raise SystemExit(f"ERROR: database not found: {db_path}\nRun the Phase 1 scan first.")

    log_path = project_root / "logs" / "phase2_pdf_extraction.log"
    setup_logging(log_path, verbose=args.verbose)

    pdf_module = load_pymupdf()
    extractor_version = getattr(pdf_module, "__version__", "") or ""

    started = time.time()
    started_at = datetime.now().replace(microsecond=0).isoformat()

    LOGGER.info("=" * 78)
    LOGGER.info("Cutler Research Brain - Phase 2 PDF text extraction")
    LOGGER.info("Database    : %s", db_path)
    LOGGER.info("Project root: %s", project_root)
    LOGGER.info("Extractor   : %s %s (pipeline %s)", EXTRACTOR_NAME, extractor_version, PIPELINE_VERSION)
    LOGGER.info("Text output : %s", project_root / "03_Extracted_Text")
    LOGGER.info("=" * 78)

    conn = connect(db_path)
    try:
        create_extraction_schema(conn)

        # ---- safety gates -------------------------------------------------
        source_roots = [
            r[0] for r in conn.execute(
                "SELECT DISTINCT scan_source_root FROM documents WHERE COALESCE(scan_source_root,'') <> ''"
            )
        ] if "scan_source_root" in table_columns(conn, "documents") else []
        validate_output_root(project_root, source_roots)

        text_root = project_root / "03_Extracted_Text"
        assert_inside_project(text_root, project_root)
        text_root.mkdir(parents=True, exist_ok=True)

        rows = select_candidates(conn, args.only_document_id)
        LOGGER.info("PDF rows in the index: %d", len(rows))
        if args.only_document_id and not rows:
            LOGGER.error("No document with document_id = %s", args.only_document_id)
            return 1

        allow_duplicates = args.force or args.include_duplicates
        considered = extracted_now = reused = failures = 0

        for row in rows:
            keys = row.keys()
            document_id = row["document_id"]
            extension = str(row["file_extension"] or "").lower() if "file_extension" in keys else ""
            duplicate_status = str(row["duplicate_status"] or "") if "duplicate_status" in keys else ""
            severity = str(row["review_severity"] or "") if "review_severity" in keys else ""
            excluded = int(row["exclude_from_phase2_extraction"] or 0) \
                if "exclude_from_phase2_extraction" in keys else 0
            source_file = Path(str(row["file_path"] or "")) if "file_path" in keys else Path("")

            # ---- eligibility ----------------------------------------------
            if extension != ".pdf":
                upsert_extraction(conn, build_record(
                    row, STATUS_SKIPPED_NON_PDF, QUALITY_NOT_APPLICABLE, None, None, project_root,
                    error=f"file_extension is {extension or '(none)'}"))
                continue

            if duplicate_status == "duplicate" and not allow_duplicates:
                upsert_extraction(conn, build_record(
                    row, STATUS_SKIPPED_DUPLICATE, QUALITY_NOT_APPLICABLE, None, None, project_root,
                    error="duplicate of another indexed file"))
                continue

            if (severity == "High" or excluded) and not args.include_high_review:
                upsert_extraction(conn, build_record(
                    row, STATUS_SKIPPED_HIGH, QUALITY_NOT_APPLICABLE, None, None, project_root,
                    error="excluded by the Phase 1 High-review gate"))
                continue

            if not source_file or not source_file.exists():
                upsert_extraction(conn, build_record(
                    row, STATUS_SKIPPED_MISSING, QUALITY_NOT_APPLICABLE, None, None, project_root,
                    error=f"file not found: {source_file}"))
                LOGGER.warning("Missing file: %s", source_file)
                continue

            # ---- output folder --------------------------------------------
            year = safe_component(
                str(row["archive_year"] or "") if "archive_year" in keys else "", "unknown_year")
            out_dir = text_root / year / safe_component(document_id, "unknown_document")
            assert_inside_project(out_dir, project_root)

            text_file = out_dir / "document_text.txt"
            meta_file = out_dir / "extraction_metadata.json"

            # ---- resumable ---------------------------------------------------
            if text_file.exists() and meta_file.exists() and not args.force:
                try:
                    previous = json.loads(meta_file.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    previous = {}
                record = build_record(
                    row, previous.get("extraction_status", STATUS_EXTRACTED),
                    previous.get("extraction_quality", QUALITY_MEDIUM),
                    out_dir,
                    {
                        "page_count": previous.get("page_count", 0),
                        "pages_extracted": previous.get("pages_extracted", 0),
                        "total_text_chars": previous.get("total_text_chars", 0),
                        "empty_pages": previous.get("empty_pages", 0),
                        "failed_pages": previous.get("failed_pages", 0),
                        "scanned_pdf_suspected": previous.get("scanned_pdf_suspected", False),
                        "table_heavy_guess": previous.get("table_heavy_guess", False),
                    },
                    project_root,
                    error=previous.get("error_message", ""),
                )
                record["extracted_at"] = previous.get("extracted_at", record["extracted_at"])
                upsert_extraction(conn, record)
                reused += 1
                LOGGER.debug("Already extracted, skipping: %s", document_id)
                continue

            # ---- extract ------------------------------------------------------
            considered += 1
            LOGGER.info("[%d] %s", considered,
                        row["relative_path"] if "relative_path" in keys else document_id)

            outcome = extract_one(pdf_module, source_file, out_dir, args.max_pages)

            failed = outcome["status"] == STATUS_FAILED
            quality, _avg = grade_quality(
                outcome["total_text_chars"], outcome["pages_extracted"],
                outcome["empty_pages"], failed,
            )
            outcome["scanned_pdf_suspected"] = suspect_scanned(
                outcome["page_count"], outcome["total_text_chars"],
                outcome["empty_pages"], outcome["pages_extracted"],
            )
            outcome["table_heavy_guess"] = guess_table_heavy(outcome.pop("text_sample", ""))

            record = build_record(row, outcome["status"], quality, out_dir, outcome, project_root)
            record["extractor_version"] = extractor_version

            if failed:
                failures += 1
                LOGGER.error("  FAILED: %s", record["error_message"])
            else:
                extracted_now += 1
                LOGGER.info("  %s | %s | %d/%d pages | %d chars%s",
                            outcome["status"], quality, outcome["pages_extracted"],
                            outcome["page_count"], outcome["total_text_chars"],
                            " | scanned?" if outcome["scanned_pdf_suspected"] else "")

            try:
                write_metadata_json(meta_file, row, record)
            except OSError as exc:
                LOGGER.error("  could not write extraction_metadata.json: %s", exc)

            record.pop("extractor_version", None)
            upsert_extraction(conn, record)
            conn.commit()

            if args.limit and considered >= args.limit:
                LOGGER.info("Reached --limit of %d documents.", args.limit)
                break

        conn.commit()

        runtime = time.time() - started
        run_stats = {
            "started_at": started_at,
            "runtime_seconds": runtime,
            "considered": considered,
            "extracted_now": extracted_now,
            "reused": reused,
            "limit": args.limit,
            "max_pages": args.max_pages,
            "force": args.force,
            "include_high_review": args.include_high_review,
            "extractor_version": extractor_version,
        }
        write_reports(conn, project_root, run_stats)

        LOGGER.info("-" * 78)
        LOGGER.info("Extraction run complete in %.1f seconds", runtime)
        LOGGER.info("  documents processed this run : %d", considered)
        LOGGER.info("  extracted / partial          : %d", extracted_now)
        LOGGER.info("  failed                       : %d", failures)
        LOGGER.info("  reused (already extracted)   : %d", reused)
        LOGGER.info("  text output                  : %s", text_root)
        LOGGER.info("  reports                      : %s", project_root / "02_Metadata_Index")
        LOGGER.info("-" * 78)
        LOGGER.info("Text extraction only - nothing was summarized or interpreted.")
    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
