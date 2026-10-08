#!/usr/bin/env python3
# =============================================================================
# Cutler Research Brain - Phase 3
# build_document_cards.py
#
# Builds one structured DOCUMENT CARD per eligible document from data that
# already exists: the Phase 1 metadata index, the Phase 2 extraction results,
# and the extracted text on local disk.
#
# A document card is a metadata profile, not a summary. Everything on it is
# either copied from the database or produced by a fixed, deterministic rule
# (first N characters, first N non-empty lines). Run the script twice on the
# same input and you get byte-identical cards.
#
# WHAT THIS PHASE DOES NOT DO - deliberately:
#   - no AI summaries, paraphrase or abstraction of any kind
#   - no interpretation of what a document means
#   - no thesis, sentiment, rating, view or investment conclusion
#   - no inference from the mere presence of a file
#   - no OpenAI, no Claude API, no external API of any kind
#   - no embeddings, no chunking, no vector index, no retrieval
#   - no OCR
#   - no Obsidian memory (Phase 4+)
#   - nothing is written to the V Drive; source PDFs are never opened here
#
# The one judgement the card does make is mechanical: whether the document is
# READY to be used by a later retrieval phase (`usable_for_rag`), based on
# extraction status, review severity, duplicates and whether there is actually
# any text. That is a plumbing decision, not a research one.
#
# PHASE 3.1: the old catch-all `missing_text` reason is gone. A card that is not
# RAG-ready now says WHY precisely - missing_text_file (the text file is not on
# disk), empty_text (extraction ran, produced nothing usable) or
# scanned_pdf_no_text (the same, and Phase 2 flagged an image-only scan, so it
# is an OCR candidate for a later phase). All three are decided from extraction
# metadata and character counts; no document content is examined.
#
# Principle: AI drafts. Humans verify. Cutler decides.
#
# Usage:
#   python scripts\build_document_cards.py ^
#       --db "data\document_metadata.sqlite" ^
#       --project-root "C:\Users\AI Admin\Documents\CutlerResearchBrain" ^
#       --limit 100
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

PIPELINE_VERSION = "phase3-1.1"

# ---- card_status values -----------------------------------------------------
CARD_CREATED = "created"
CARD_SKIPPED_DUPLICATE = "skipped_duplicate"
CARD_SKIPPED_HIGH = "skipped_high_review"
CARD_SKIPPED_MISSING_TEXT = "skipped_missing_text"
CARD_SKIPPED_FAILED_EXTRACTION = "skipped_failed_extraction"
CARD_FAILED = "failed"

SKIP_CARD_STATUSES = (
    CARD_SKIPPED_DUPLICATE, CARD_SKIPPED_HIGH,
    CARD_SKIPPED_MISSING_TEXT, CARD_SKIPPED_FAILED_EXTRACTION,
)

# ---- card_quality values ----------------------------------------------------
QUALITY_HIGH = "High"
QUALITY_MEDIUM = "Medium"
QUALITY_LOW = "Low"
QUALITY_NEEDS_REVIEW = "Needs Review"

# ---- extraction quality ordering, for --min-quality -------------------------
EXTRACTION_QUALITY_RANK = {"High": 4, "Medium": 3, "Low": 2, "Empty": 1}

# ---- rag_exclusion_reason values (Phase 3.1) --------------------------------
# Phase 3.0 used one broad "missing_text" bucket, which mixed three very
# different situations. They are now separate so a later phase can tell an OCR
# candidate from a genuinely absent file without re-deriving anything:
#
#   missing_text_file    the expected document_text.txt is not on disk
#   empty_text           extraction ran but produced no meaningful text
#   scanned_pdf_no_text  same, and Phase 2 flagged the PDF as an image scan
#
# All three come from extraction metadata and character counts only. Nothing
# about the CONTENT of a document is examined or inferred.
REASON_NONE = "none"
REASON_HIGH_REVIEW = "high_review"
REASON_DUPLICATE = "duplicate"
REASON_FAILED_EXTRACTION = "failed_extraction"
REASON_MISSING_TEXT_FILE = "missing_text_file"
REASON_EMPTY_TEXT = "empty_text"
REASON_SCANNED_NO_TEXT = "scanned_pdf_no_text"
REASON_NOT_PDF = "not_pdf"

# Legacy value from Phase 3.0, kept so old rows can still be recognised and
# repaired by scripts/refresh_document_card_reports.py --fix-reasons.
REASON_LEGACY_MISSING_TEXT = "missing_text"

RAG_EXCLUSION_REASONS = [
    REASON_NONE,
    REASON_DUPLICATE,
    REASON_HIGH_REVIEW,
    REASON_FAILED_EXTRACTION,
    REASON_MISSING_TEXT_FILE,
    REASON_EMPTY_TEXT,
    REASON_SCANNED_NO_TEXT,
    REASON_NOT_PDF,
]

REASON_MEANINGS = {
    REASON_NONE: "usable - text is available for a later retrieval phase",
    REASON_DUPLICATE: "duplicate of another indexed file",
    REASON_HIGH_REVIEW: "High review - possible client/portfolio material, gated",
    REASON_FAILED_EXTRACTION: "extraction failed, was skipped, or never ran",
    REASON_MISSING_TEXT_FILE: "expected extracted-text file is missing on disk",
    REASON_EMPTY_TEXT: "extraction ran but no meaningful text was produced",
    REASON_SCANNED_NO_TEXT: "likely image-only / scanned PDF - OCR candidate for a later phase",
    REASON_NOT_PDF: "not a PDF",
}

# Extraction statuses that mean Phase 2 produced no text through failure.
FAILED_EXTRACTION_STATUSES = ("failed", "skipped_failed_extraction", "skipped_non_pdf", "")

# ---- deterministic preview settings -----------------------------------------
PREVIEW_CHARS = 1500
MIN_MEANINGFUL_CHARS = 200      # below this, the text is not worth retrieving
HEADING_MAX_LEN = 90
HEADING_MIN_LEN = 3
FIRST_LINES = 10                # "first 10 non-empty lines" per the spec
MAX_HEADING_CANDIDATES = 15
TITLE_MAX_LEN = 160

DOCUMENT_CARD_COLUMNS = [
    "document_id",
    "card_status",
    "card_quality",
    "usable_for_rag",
    "rag_exclusion_reason",
    "card_folder",
    "card_md_path",
    "card_json_path",
    "deterministic_preview_chars",
    "heading_candidate_count",
    "generated_at",
    "error_message",
]

INTEGER_CARD_COLUMNS = {"usable_for_rag", "deterministic_preview_chars", "heading_candidate_count"}

LOGGER = logging.getLogger("phase3_document_cards")


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
# Path safety
# --------------------------------------------------------------------------

def is_relative_to(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except (ValueError, OSError):
        return False


def validate_project_root(project_root: Path) -> None:
    drive = os.path.splitdrive(str(project_root.resolve()))[0].upper().rstrip(":")
    if drive == "V":
        raise SystemExit(
            f"ERROR: --project-root is on the V Drive ({project_root}). "
            "The V Drive is read-only. Aborting."
        )


def assert_inside_project(path: Path, project_root: Path) -> None:
    if not is_relative_to(path, project_root):
        raise SystemExit(f"ERROR: refusing to write outside the project root: {path}")


SAFE_ID = re.compile(r"^[A-Za-z0-9_.-]+$")


def safe_component(value: str, fallback: str) -> str:
    """Folder names come from document_id / archive_year only, never file names."""
    value = (value or "").strip()
    return value if value and SAFE_ID.match(value) else fallback


# --------------------------------------------------------------------------
# Deterministic text handling - no interpretation anywhere in here
# --------------------------------------------------------------------------

RE_PAGE_MARKER = re.compile(r"^=====\s*\[\s*page\s+\d+\s*\]\s*=====$", re.IGNORECASE)
RE_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
RE_SPACES = re.compile(r"[ \t\u00a0]+")
RE_BLANK_RUN = re.compile(r"\n{3,}")
RE_HAS_LETTER = re.compile(r"[A-Za-z]")
RE_MOSTLY_PUNCT = re.compile(r"^[^A-Za-z0-9]+$")


def clean_text(raw: str) -> str:
    """Normalize extracted text. Whitespace only - no words are changed.

    Removes the page markers this pipeline wrote in Phase 2, strips control
    characters, collapses runs of spaces and blank lines. Nothing is reordered,
    rephrased or dropped beyond that.
    """
    lines = []
    for line in raw.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if RE_PAGE_MARKER.match(line.strip()):
            continue
        line = RE_CONTROL.sub(" ", line)
        line = RE_SPACES.sub(" ", line).strip()
        lines.append(line)
    return RE_BLANK_RUN.sub("\n\n", "\n".join(lines)).strip()


def deterministic_preview(cleaned: str, limit: int = PREVIEW_CHARS) -> str:
    """The first N characters of the cleaned text. Verbatim, never a summary."""
    if not cleaned:
        return ""
    preview = cleaned[:limit]
    if len(cleaned) > limit:
        # Cut at the last whitespace so the preview does not end mid-word.
        cut = preview.rfind(" ")
        if cut > limit * 0.6:
            preview = preview[:cut]
        preview = preview.rstrip() + " ..."
    return preview.strip()


def looks_like_heading(line: str) -> bool:
    """Fixed shape test. Says nothing about what the line means."""
    if not (HEADING_MIN_LEN <= len(line) <= HEADING_MAX_LEN):
        return False
    if not RE_HAS_LETTER.search(line):
        return False
    if RE_MOSTLY_PUNCT.match(line):
        return False
    if line.endswith((".", ";", ",")):
        return False
    words = line.split()
    return 1 <= len(words) <= 14


def detect_headings(cleaned: str) -> list[str]:
    """First N non-empty lines, plus later lines with a title-like SHAPE.

    Pure pattern matching: length, word count, punctuation. No meaning is
    inferred and nothing is ranked by importance.
    """
    non_empty = [line.strip() for line in cleaned.split("\n") if line.strip()]
    if not non_empty:
        return []

    candidates: list[str] = []
    for line in non_empty[:FIRST_LINES]:
        if line not in candidates:
            candidates.append(line)

    for line in non_empty[FIRST_LINES:]:
        if len(candidates) >= MAX_HEADING_CANDIDATES:
            break
        if not looks_like_heading(line):
            continue
        upper_ratio = sum(1 for c in line if c.isupper()) / max(len(line), 1)
        title_like = line.istitle() or line.isupper() or upper_ratio > 0.3
        if title_like and line not in candidates:
            candidates.append(line)

    return candidates[:MAX_HEADING_CANDIDATES]


def detect_title(cleaned: str, headings: list[str], file_name: str) -> str:
    """First line that has a title's SHAPE, else the file name. A guess, labelled."""
    for line in headings:
        if HEADING_MIN_LEN <= len(line) <= TITLE_MAX_LEN and RE_HAS_LETTER.search(line):
            return line
    stem = Path(file_name or "").stem
    return stem[:TITLE_MAX_LEN] if stem else ""


# --------------------------------------------------------------------------
# Card grading - mechanical, from metadata completeness only
# --------------------------------------------------------------------------

def grade_card(row: sqlite3.Row, preview_chars: int, extraction_quality: str,
               scanned: bool, review_severity: str) -> str:
    """card_quality: how COMPLETE this card is, not how good the document is.

    Points for text volume and for metadata that is actually filled in. A card
    that a human clearly needs to look at is marked "Needs Review" outright.
    """
    if review_severity == "High":
        return QUALITY_NEEDS_REVIEW
    if preview_chars == 0 or extraction_quality == "Empty":
        return QUALITY_NEEDS_REVIEW
    if scanned and preview_chars < MIN_MEANINGFUL_CHARS:
        return QUALITY_NEEDS_REVIEW

    score = 0
    score += {"High": 2, "Medium": 1, "Low": 0}.get(extraction_quality, 0)
    if value_of(row, "source_type_guess") not in ("", "Unknown"):
        score += 1
    if value_of(row, "document_type_guess") not in ("", "Unknown"):
        score += 1
    if value_of(row, "ticker_candidates_clean"):
        score += 1
    if value_of(row, "detected_year"):
        score += 1
    if preview_chars >= 400:
        score += 1

    if score >= 5:
        return QUALITY_HIGH
    if score >= 3:
        return QUALITY_MEDIUM
    return QUALITY_LOW


def assess_rag_readiness(extraction_status: str, extraction_quality: str,
                         review_severity: str, duplicate_status: str,
                         scanned: bool, meaningful_chars: int,
                         text_file_exists: bool = True,
                         file_extension: str = ".pdf") -> tuple[bool, str]:
    """(usable_for_rag, rag_exclusion_reason). Plumbing readiness only.

    Says whether a later retrieval phase COULD use this document's text. It
    never says whether the document should be trusted, what it is about, or
    what it means. Every input is either a database field or a character count.

    Rule order (Phase 3.1):
      1. duplicate
      2. High review
      3. extraction failed / skipped / never ran
      4. expected text file missing on disk   -> missing_text_file
      5. Empty extraction quality + scanned    -> scanned_pdf_no_text
      6. Empty extraction quality              -> empty_text
      7. under the meaningful-character floor + scanned -> scanned_pdf_no_text
      8. under the meaningful-character floor  -> empty_text
      9. otherwise usable
    """
    if str(file_extension or "").lower() not in ("", ".pdf"):
        return False, REASON_NOT_PDF
    if duplicate_status == "duplicate":
        return False, REASON_DUPLICATE
    if review_severity == "High":
        return False, REASON_HIGH_REVIEW
    if extraction_status in FAILED_EXTRACTION_STATUSES or \
            extraction_status not in ("extracted", "partial"):
        return False, REASON_FAILED_EXTRACTION
    if not text_file_exists:
        return False, REASON_MISSING_TEXT_FILE
    if extraction_quality == "Empty":
        return False, REASON_SCANNED_NO_TEXT if scanned else REASON_EMPTY_TEXT
    if meaningful_chars < MIN_MEANINGFUL_CHARS:
        return False, REASON_SCANNED_NO_TEXT if scanned else REASON_EMPTY_TEXT
    return True, REASON_NONE


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


def create_card_schema(conn: sqlite3.Connection) -> None:
    defs = []
    for col in DOCUMENT_CARD_COLUMNS:
        if col == "document_id":
            defs.append("document_id TEXT PRIMARY KEY")
        elif col in INTEGER_CARD_COLUMNS:
            defs.append(f"{col} INTEGER")
        else:
            defs.append(f"{col} TEXT")
    conn.execute(f"CREATE TABLE IF NOT EXISTS document_cards ({', '.join(defs)})")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cards_status ON document_cards(card_status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cards_rag ON document_cards(usable_for_rag)")

    existing = table_columns(conn, "documents")
    for col, col_type in [("document_card_status", "TEXT"),
                          ("usable_for_rag", "INTEGER"),
                          ("document_card_generated_at", "TEXT")]:
        if col not in existing:
            conn.execute(f"ALTER TABLE documents ADD COLUMN {col} {col_type}")
            LOGGER.info("Added Phase 3 column to documents: %s", col)
    conn.commit()


def value_of(row: sqlite3.Row, column: str, default: str = "") -> str:
    try:
        if column in row.keys():
            value = row[column]
            return "" if value is None else str(value)
    except (AttributeError, IndexError):
        pass
    return default


def select_candidates(conn: sqlite3.Connection, only_document_id: str = "") -> list[sqlite3.Row]:
    """Every PDF row with its Phase 2 extraction result. Eligibility comes later."""
    doc_cols = table_columns(conn, "documents")
    wanted = [
        "document_id", "file_name", "file_extension", "file_path", "relative_path",
        "archive_year", "detected_year", "detected_month", "folder_date_guess",
        "ticker_candidates_clean", "source_vendor_candidates", "source_type_guess",
        "document_type_guess", "strategy_theme_guess", "review_severity",
        "review_category", "sensitive_context_guess", "duplicate_status",
        "file_hash_sha256",
    ]
    select = ", ".join(f"d.{c}" for c in wanted if c in doc_cols)

    sql = f"""
        SELECT {select},
               COALESCE(e.extraction_status, '')   AS pdf_extraction_status,
               COALESCE(e.extraction_quality, '')  AS pdf_extraction_quality,
               COALESCE(e.page_count, 0)           AS pdf_page_count,
               COALESCE(e.total_text_chars, 0)     AS pdf_total_text_chars,
               COALESCE(e.scanned_pdf_suspected, 0) AS scanned_pdf_suspected,
               COALESCE(e.table_heavy_guess, 0)    AS table_heavy_guess,
               COALESCE(e.output_folder, '')       AS extraction_output_folder,
               COALESCE(e.document_text_path, '')  AS document_text_path,
               COALESCE(e.metadata_json_path, '')  AS metadata_json_path
          FROM documents d
          LEFT JOIN pdf_extractions e ON e.document_id = d.document_id
    """
    if only_document_id:
        sql += " WHERE d.document_id = ?"
        return list(conn.execute(sql, (only_document_id,)))

    sql += " WHERE LOWER(COALESCE(d.file_extension,'')) = '.pdf' ORDER BY d.relative_path"
    return list(conn.execute(sql))


def upsert_card(conn: sqlite3.Connection, record: dict) -> None:
    placeholders = ", ".join("?" for _ in DOCUMENT_CARD_COLUMNS)
    conn.execute(
        f"INSERT OR REPLACE INTO document_cards ({', '.join(DOCUMENT_CARD_COLUMNS)}) "
        f"VALUES ({placeholders})",
        tuple(record.get(col) for col in DOCUMENT_CARD_COLUMNS),
    )
    conn.execute(
        """
        UPDATE documents
           SET document_card_status = ?, usable_for_rag = ?, document_card_generated_at = ?
         WHERE document_id = ?
        """,
        (record.get("card_status"), record.get("usable_for_rag"),
         record.get("generated_at"), record.get("document_id")),
    )


# --------------------------------------------------------------------------
# Card writing
# --------------------------------------------------------------------------

def yes_no(value) -> str:
    return "yes" if value else "no"


def build_card_json(row: sqlite3.Row, card_status: str, card_quality: str,
                    usable: bool, reason: str, preview: str, title: str,
                    headings: list[str], project_root: Path) -> dict:
    return {
        "document_id": value_of(row, "document_id"),
        "file_name": value_of(row, "file_name"),
        "relative_path": value_of(row, "relative_path"),
        "source_file_path": value_of(row, "file_path"),
        "archive_year": value_of(row, "archive_year"),
        "detected_year": value_of(row, "detected_year"),
        "detected_month": value_of(row, "detected_month"),
        "folder_date_guess": value_of(row, "folder_date_guess"),
        "ticker_candidates_clean": value_of(row, "ticker_candidates_clean"),
        "source_vendor_candidates": value_of(row, "source_vendor_candidates"),
        "source_type_guess": value_of(row, "source_type_guess"),
        "document_type_guess": value_of(row, "document_type_guess"),
        "strategy_theme_guess": value_of(row, "strategy_theme_guess"),
        "review_severity": value_of(row, "review_severity"),
        "review_category": value_of(row, "review_category"),
        "sensitive_context_guess": value_of(row, "sensitive_context_guess"),
        "duplicate_status": value_of(row, "duplicate_status"),
        "pdf_extraction_status": value_of(row, "pdf_extraction_status"),
        "pdf_extraction_quality": value_of(row, "pdf_extraction_quality"),
        "pdf_page_count": int(value_of(row, "pdf_page_count", "0") or 0),
        "pdf_total_text_chars": int(value_of(row, "pdf_total_text_chars", "0") or 0),
        "extraction_output_folder": value_of(row, "extraction_output_folder"),
        "document_text_path": value_of(row, "document_text_path"),
        "card_status": card_status,
        "card_quality": card_quality,
        "usable_for_rag": bool(usable),
        "rag_exclusion_reason": reason,
        "deterministic_preview": preview,
        "detected_title_guess": title,
        "detected_heading_candidates": headings,
        "generated_at": datetime.now().replace(microsecond=0).isoformat(),
        # ---- provenance, so a reader knows exactly how this card was made ----
        "scanned_pdf_suspected": bool(int(value_of(row, "scanned_pdf_suspected", "0") or 0)),
        "table_heavy_guess": bool(int(value_of(row, "table_heavy_guess", "0") or 0)),
        "extraction_metadata_path": value_of(row, "metadata_json_path"),
        "generator": "build_document_cards.py",
        "pipeline_version": PIPELINE_VERSION,
        "generation_method": "deterministic metadata + fixed-rule text slice; no AI summary",
        "note": ("Metadata profile only. The preview is a verbatim slice of extracted text. "
                 "Nothing on this card was summarized, paraphrased or interpreted, and no "
                 "investment view is expressed or implied."),
    }


def render_card_md(card: dict) -> str:
    """The Markdown card. Front matter first so later phases can parse it."""
    def field(value) -> str:
        return str(value) if str(value).strip() else "(none)"

    headings = card["detected_heading_candidates"]
    heading_block = "\n".join(f"- {h}" for h in headings) if headings else "- (none detected)"

    preview = card["deterministic_preview"]
    preview_block = (f"```text\n{preview}\n```" if preview
                     else "_(no extracted text available for a preview)_")

    lines = [
        "---",
        "type: document_card",
        f"document_id: {card['document_id']}",
        f"archive_year: {field(card['archive_year'])}",
        f"detected_year: {field(card['detected_year'])}",
        f"source_type: {field(card['source_type_guess'])}",
        f"document_type: {field(card['document_type_guess'])}",
        f"strategy_theme: {field(card['strategy_theme_guess'])}",
        f"review_severity: {field(card['review_severity'])}",
        f"pdf_extraction_quality: {field(card['pdf_extraction_quality'])}",
        f"usable_for_rag: {'true' if card['usable_for_rag'] else 'false'}",
        "---",
        "",
        f"# Document Card: {card['file_name'] or card['document_id']}",
        "",
        f"_Detected title guess (shape-based, unverified): {field(card['detected_title_guess'])}_",
        "",
        "## Basic Metadata",
        "",
        f"- Document ID: {card['document_id']}",
        f"- File name: {field(card['file_name'])}",
        f"- Relative path: {field(card['relative_path'])}",
        f"- Archive year: {field(card['archive_year'])}",
        f"- Detected year / month: {field(card['detected_year'])} / {field(card['detected_month'])}",
        f"- Folder date: {field(card['folder_date_guess'])}",
        f"- Source type guess: {field(card['source_type_guess'])}",
        f"- Document type guess: {field(card['document_type_guess'])}",
        f"- Strategy theme guess: {field(card['strategy_theme_guess'])}",
        f"- Ticker candidates (unverified): {field(card['ticker_candidates_clean'])}",
        f"- Source/vendor candidates (not tickers): {field(card['source_vendor_candidates'])}",
        "",
        "## Extraction Status",
        "",
        f"- PDF extraction status: {field(card['pdf_extraction_status'])}",
        f"- PDF extraction quality: {field(card['pdf_extraction_quality'])}",
        f"- Page count: {card['pdf_page_count']:,}",
        f"- Total extracted characters: {card['pdf_total_text_chars']:,}",
        f"- Scanned / image-only suspected: {yes_no(card['scanned_pdf_suspected'])}",
        f"- Table-heavy guess: {yes_no(card['table_heavy_guess'])}",
        "",
        "## Review Status",
        "",
        f"- Review severity: {field(card['review_severity'])}",
        f"- Review category: {field(card['review_category'])}",
        f"- Sensitive context guess: {field(card['sensitive_context_guess'])}",
        f"- Duplicate status: {field(card['duplicate_status'])}",
        f"- Card quality: {field(card['card_quality'])}",
        "",
        "## RAG Readiness",
        "",
        f"- Usable for RAG: {'true' if card['usable_for_rag'] else 'false'}",
        f"- Exclusion reason if not usable: {field(card['rag_exclusion_reason'])}",
        "",
        "## Deterministic Preview",
        "",
        f"_Verbatim slice of the cleaned extracted text: the first {PREVIEW_CHARS:,} characters, "
        f"cut at a word boundary ({len(preview):,} characters here). Not a summary; nothing was "
        "rewritten, reordered, selected for importance or interpreted._",
        "",
        preview_block,
        "",
        "## Detected Heading Candidates",
        "",
        "_First lines of the document plus later lines with a title-like shape. "
        "Pattern matching only - no meaning was inferred._",
        "",
        heading_block,
        "",
        "## Source Links",
        "",
        f"- Original source file path: `{field(card['source_file_path'])}`",
        f"- Extracted text path: `{field(card['document_text_path'])}`",
        f"- Extraction metadata path: `{field(card['extraction_metadata_path'])}`",
        "",
        "## Safety Note",
        "",
        "This card is generated from metadata and deterministic extraction only. It is not an "
        "investment summary, recommendation, or verified interpretation. Classifications ending "
        "in `_guess` are automated guesses, ticker candidates are unverified, and no view is "
        "expressed about the document's content.",
        "",
        "**AI drafts. Humans verify. Cutler decides.**",
        "",
        f"_Generated {card['generated_at']} by {card['generator']} ({card['pipeline_version']})._",
        "",
    ]
    return "\n".join(lines)


def make_record(document_id: str, card_status: str, card_quality: str, usable: bool,
                reason: str, card_dir: Path | None, project_root: Path,
                preview_chars: int = 0, heading_count: int = 0, error: str = "") -> dict:
    def rel(path: Path | None) -> str:
        if path is None:
            return ""
        try:
            return str(path.relative_to(project_root))
        except ValueError:
            return str(path)

    return {
        "document_id": document_id,
        "card_status": card_status,
        "card_quality": card_quality,
        "usable_for_rag": 1 if usable else 0,
        "rag_exclusion_reason": reason,
        "card_folder": rel(card_dir),
        "card_md_path": rel(card_dir / "document_card.md") if card_dir else "",
        "card_json_path": rel(card_dir / "document_card.json") if card_dir else "",
        "deterministic_preview_chars": preview_chars,
        "heading_candidate_count": heading_count,
        "generated_at": datetime.now().replace(microsecond=0).isoformat(),
        "error_message": error,
    }


# --------------------------------------------------------------------------
# Reports
# --------------------------------------------------------------------------

def write_csv(path: Path, header: list[str], rows) -> int:
    import csv  # noqa: PLC0415
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow(header)
        count = 0
        for row in rows:
            writer.writerow(row)
            count += 1
    LOGGER.info("Wrote %s (%d data rows)", path, count)
    return count


MANIFEST_COLUMNS = [
    "document_id", "file_name", "relative_path", "card_status", "card_quality",
    "usable_for_rag", "rag_exclusion_reason", "archive_year", "detected_year",
    "source_type_guess", "document_type_guess", "strategy_theme_guess",
    "ticker_candidates_clean", "source_vendor_candidates", "review_severity",
    "pdf_extraction_status", "pdf_extraction_quality", "pdf_page_count",
    "pdf_total_text_chars", "deterministic_preview_chars", "heading_candidate_count",
    "card_folder", "card_md_path", "error_message", "generated_at",
]


def manifest_rows(conn: sqlite3.Connection, where: str = "", params: tuple = ()) -> list[tuple]:
    sql = """
        SELECT c.document_id, d.file_name, d.relative_path, c.card_status, c.card_quality,
               c.usable_for_rag, c.rag_exclusion_reason, d.archive_year, d.detected_year,
               d.source_type_guess, d.document_type_guess, d.strategy_theme_guess,
               d.ticker_candidates_clean, d.source_vendor_candidates, d.review_severity,
               d.pdf_extraction_status, d.pdf_extraction_quality, d.pdf_page_count,
               d.pdf_total_text_chars, c.deterministic_preview_chars, c.heading_candidate_count,
               c.card_folder, c.card_md_path, c.error_message, c.generated_at
          FROM document_cards c
          LEFT JOIN documents d ON d.document_id = c.document_id
    """
    if where:
        sql += f" WHERE {where}"
    sql += " ORDER BY c.card_status, d.relative_path"
    return [tuple(r) for r in conn.execute(sql, params)]


def scalar(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> int:
    return int(conn.execute(sql, params).fetchone()[0] or 0)


def _md_table(header: list[str], rows: list[tuple]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join(["---"] * len(header)) + "|"]
    for row in rows:
        lines.append("| " + " | ".join("" if v is None else str(v) for v in row) + " |")
    if not rows:
        lines.append("| " + " | ".join(["(none)"] * len(header)) + " |")
    return "\n".join(lines)


def write_reports(conn: sqlite3.Connection, project_root: Path, run_stats: dict) -> dict:
    data_dir = project_root / "data"
    index_dir = project_root / "02_Metadata_Index"
    for directory in (data_dir, index_dir):
        assert_inside_project(directory, project_root)
        directory.mkdir(parents=True, exist_ok=True)

    rows = manifest_rows(conn)
    write_csv(data_dir / "document_card_manifest.csv", MANIFEST_COLUMNS, rows)
    write_csv(index_dir / "document_card_manifest.csv", MANIFEST_COLUMNS, rows)

    quality_rows = [
        tuple(r) for r in conn.execute(
            """
            SELECT COALESCE(c.card_quality, '(none)'), COALESCE(c.card_status, '(none)'),
                   COUNT(*), SUM(c.usable_for_rag),
                   COALESCE(SUM(c.deterministic_preview_chars), 0),
                   COALESCE(AVG(c.heading_candidate_count), 0)
              FROM document_cards c
             GROUP BY 1, 2
             ORDER BY COUNT(*) DESC
            """
        )
    ]
    write_csv(index_dir / "document_card_quality_report.csv",
              ["card_quality", "card_status", "card_count", "usable_for_rag_count",
               "total_preview_chars", "avg_heading_candidates"],
              [(q, s, n, u or 0, p, round(float(h or 0), 1)) for q, s, n, u, p, h in quality_rows])

    write_csv(index_dir / "document_card_needs_review_report.csv", MANIFEST_COLUMNS,
              manifest_rows(conn,
                            "c.card_quality = 'Needs Review' OR c.card_status = 'failed' "
                            "OR d.review_severity = 'High'"))

    write_csv(index_dir / "rag_readiness_report.csv",
              ["rag_exclusion_reason", "usable_for_rag", "card_count", "share_of_cards"],
              rag_readiness_rows(conn))

    # ---- Phase 3.1 reports ------------------------------------------------
    write_csv(index_dir / "rag_exclusion_reason_clean_report.csv",
              ["rag_exclusion_reason", "usable_for_rag", "meaning", "card_count",
               "share_of_cards", "is_legacy_value"],
              reason_clean_rows(conn))

    for reason, filename in (
        (REASON_SCANNED_NO_TEXT, "scanned_no_text_card_report.csv"),
        (REASON_EMPTY_TEXT, "empty_text_card_report.csv"),
        (REASON_MISSING_TEXT_FILE, "missing_text_file_card_report.csv"),
    ):
        write_csv(index_dir / filename, MANIFEST_COLUMNS,
                  manifest_rows(conn, "c.rag_exclusion_reason = ?", (reason,)))

    totals = collect_totals(conn)
    write_summary(conn, index_dir / "document_card_summary.md", totals, run_stats)
    return totals


def reason_clean_rows(conn: sqlite3.Connection) -> list[tuple]:
    """Every canonical Phase 3.1 reason, in a fixed order, zero rows included.

    A stable row set means a later phase can diff two runs. Any value that is
    not canonical (an old Phase 3.0 "missing_text", say) is listed at the end
    and flagged, rather than silently folded into another bucket.
    """
    counts = {
        (row[0] or REASON_NONE): row[1]
        for row in conn.execute(
            "SELECT COALESCE(rag_exclusion_reason, 'none'), COUNT(*) FROM document_cards GROUP BY 1"
        )
    }
    total = sum(counts.values()) or 1

    rows = []
    for reason in RAG_EXCLUSION_REASONS:
        count = counts.pop(reason, 0)
        rows.append((reason, "yes" if reason == REASON_NONE else "no",
                     REASON_MEANINGS.get(reason, ""), count,
                     f"{count / total * 100:.1f}%", "no"))
    for reason, count in sorted(counts.items(), key=lambda kv: -kv[1]):
        rows.append((reason, "no",
                     "LEGACY value - run refresh_document_card_reports.py --fix-reasons",
                     count, f"{count / total * 100:.1f}%", "yes"))
    return rows


def rag_readiness_rows(conn: sqlite3.Connection) -> list[tuple]:
    total = scalar(conn, "SELECT COUNT(*) FROM document_cards") or 1
    rows = []
    for reason, usable, count in conn.execute(
        """
        SELECT COALESCE(rag_exclusion_reason, 'none'), usable_for_rag, COUNT(*)
          FROM document_cards
         GROUP BY 1, 2
         ORDER BY COUNT(*) DESC
        """
    ):
        rows.append((reason, "yes" if usable else "no", count, f"{count / total * 100:.1f}%"))
    return rows


def collect_totals(conn: sqlite3.Connection) -> dict:
    return {
        "cards": scalar(conn, "SELECT COUNT(*) FROM document_cards"),
        "created": scalar(conn, "SELECT COUNT(*) FROM document_cards WHERE card_status = ?",
                          (CARD_CREATED,)),
        "failed": scalar(conn, "SELECT COUNT(*) FROM document_cards WHERE card_status = ?",
                         (CARD_FAILED,)),
        "skipped": scalar(conn, "SELECT COUNT(*) FROM document_cards WHERE card_status LIKE 'skipped%'"),
        "usable": scalar(conn, "SELECT COUNT(*) FROM document_cards WHERE usable_for_rag = 1"),
        "not_usable": scalar(conn, "SELECT COUNT(*) FROM document_cards WHERE usable_for_rag = 0"),
        "needs_review": scalar(conn, "SELECT COUNT(*) FROM document_cards WHERE card_quality = ?",
                               (QUALITY_NEEDS_REVIEW,)),
        "preview_chars": scalar(conn, "SELECT COALESCE(SUM(deterministic_preview_chars),0) "
                                      "FROM document_cards"),
        "pdfs": scalar(conn, "SELECT COUNT(*) FROM documents "
                             "WHERE LOWER(COALESCE(file_extension,'')) = '.pdf'"),
        "extracted": scalar(conn, "SELECT COUNT(*) FROM pdf_extractions "
                                  "WHERE extraction_status IN ('extracted','partial')"),
    }


def write_summary(conn: sqlite3.Connection, path: Path, totals: dict, run_stats: dict) -> None:
    generated = datetime.now().replace(microsecond=0).isoformat()

    status_rows = [tuple(r) for r in conn.execute(
        "SELECT COALESCE(card_status,'(none)'), COUNT(*) FROM document_cards "
        "GROUP BY 1 ORDER BY COUNT(*) DESC")]
    quality_rows = [tuple(r) for r in conn.execute(
        "SELECT COALESCE(card_quality,'(none)'), COUNT(*) FROM document_cards "
        "WHERE card_status = ? GROUP BY 1 ORDER BY COUNT(*) DESC", (CARD_CREATED,))]
    reason_rows = [tuple(r) for r in conn.execute(
        "SELECT COALESCE(rag_exclusion_reason,'none'), COUNT(*) FROM document_cards "
        "WHERE usable_for_rag = 0 GROUP BY 1 ORDER BY COUNT(*) DESC")]
    source_rows = [tuple(r) for r in conn.execute(
        "SELECT COALESCE(d.source_type_guess,'(none)'), COUNT(*) FROM document_cards c "
        "LEFT JOIN documents d ON d.document_id = c.document_id "
        "WHERE c.card_status = ? GROUP BY 1 ORDER BY COUNT(*) DESC LIMIT 15", (CARD_CREATED,))]
    doctype_rows = [tuple(r) for r in conn.execute(
        "SELECT COALESCE(d.document_type_guess,'(none)'), COUNT(*) FROM document_cards c "
        "LEFT JOIN documents d ON d.document_id = c.document_id "
        "WHERE c.card_status = ? GROUP BY 1 ORDER BY COUNT(*) DESC LIMIT 15", (CARD_CREATED,))]

    created = totals["created"] or 1
    lines = [
        "# Phase 3 - Document Card Summary",
        "",
        f"_Generated: {generated}_",
        "",
        "## In one line",
        "",
        f"**{totals['created']:,} document cards** now exist, "
        f"**{totals['usable']:,}** of them marked ready for a future retrieval phase. "
        f"Cards are metadata profiles with a verbatim text preview - "
        f"**no summaries, no interpretation, no investment views.**",
        "",
        "## What a document card is",
        "",
        "- Metadata copied from the Phase 1 index and the Phase 2 extraction results.",
        "- A **verbatim** slice of the extracted text (first "
        f"{PREVIEW_CHARS:,} characters, cut at a word boundary).",
        "- Heading candidates found by shape alone - length, word count, punctuation.",
        "- A mechanical `usable_for_rag` flag: can a later phase use this text at all.",
        "",
        "Nothing on a card was summarized, paraphrased, ranked by importance, or interpreted. "
        "No thesis, sentiment, rating or investment view is expressed or implied, and nothing "
        "is inferred from a file merely existing.",
        "",
        "## Headline numbers",
        "",
        "| Measure | Count |",
        "|---|---|",
        f"| PDFs in the metadata index | {totals['pdfs']:,} |",
        f"| PDFs with extracted text (Phase 2) | {totals['extracted']:,} |",
        f"| **Document cards created** | **{totals['created']:,}** |",
        f"| Card rows skipped | {totals['skipped']:,} |",
        f"| Card build failures | {totals['failed']:,} |",
        f"| **Usable for RAG** | **{totals['usable']:,}** |",
        f"| Not usable for RAG | {totals['not_usable']:,} |",
        f"| Cards marked Needs Review | {totals['needs_review']:,} |",
        f"| Total preview characters written | {totals['preview_chars']:,} |",
        "",
        f"{totals['usable'] / created * 100:.1f}% of created cards are RAG-ready.",
        "",
        "## Card status",
        "",
        _md_table(["Status", "Cards"], status_rows),
        "",
        "## Card quality (created cards only)",
        "",
        _md_table(["Card quality", "Cards"], quality_rows),
        "",
        "Card quality measures how COMPLETE the card is - text volume plus how much metadata is "
        "filled in. It is not a judgement about the document.",
        "",
        "## Why cards are not RAG-usable",
        "",
        _md_table(["Exclusion reason", "Cards", "What it means"],
                  [(r, c, REASON_MEANINGS.get(r, "")) for r, c in reason_rows]),
        "",
        "Phase 3.1 split the old catch-all `missing_text` bucket into three distinct reasons, so a",
        "later phase can tell an OCR candidate from a genuinely absent file:",
        "",
        "- **`missing_text_file`** - the expected `document_text.txt` is not on disk. Something is",
        "  missing from the pipeline output; re-run Phase 2 extraction for these documents.",
        "- **`empty_text`** - extraction ran and the text file exists, but it holds no meaningful",
        f"  text (under {MIN_MEANINGFUL_CHARS} characters). The PDF had nothing to give.",
        "- **`scanned_pdf_no_text`** - the same, and Phase 2 flagged the PDF as an image-only scan.",
        "  These are the **OCR candidates** for a later phase. No OCR was performed here.",
        "",
        "The remaining reasons are deliberate gates rather than problems: `duplicate` (another copy",
        "is already indexed), `high_review` (possible client or portfolio material, held back until",
        "a human clears it) and `failed_extraction` (Phase 2 could not read the file, or skipped",
        "it). A skipped or gated row is **not** a failure and is not RAG-ready either way.",
        "",
        "Every one of these labels comes from extraction metadata and character counts only. None",
        "of them involved looking at what a document says.",
        "",
        "## Cards by source type guess",
        "",
        _md_table(["Source type guess", "Cards"], source_rows),
        "",
        "## Cards by document type guess",
        "",
        _md_table(["Document type guess", "Cards"], doctype_rows),
        "",
        "## What was and was not done",
        "",
        "- No AI summary of any document was produced.",
        "- No thesis, sentiment, rating change or investment view was inferred.",
        "- Nothing was concluded from the presence of a file.",
        "- No OpenAI, Claude or other external API was called.",
        "- No embeddings, no chunking, no vector index, no retrieval.",
        "- No OCR.",
        "- No Obsidian memory was written.",
        "- Source PDFs were never opened in this phase; cards are built from the database and "
        "the already-extracted text. The V Drive was not touched.",
        "",
        "**AI drafts. Humans verify. Cutler decides.**",
        "",
    ]

    if run_stats:
        lines.extend([
            "---",
            "",
            "## Last card build run",
            "",
            f"- Runtime: {run_stats.get('runtime_seconds', 0):,.1f} seconds",
            f"- Documents processed this run: {run_stats.get('considered', 0):,}",
            f"- Cards written this run: {run_stats.get('written', 0):,}",
            f"- Cards reused (already existed): {run_stats.get('reused', 0):,}",
            f"- Options: limit={run_stats.get('limit') or 'none'}, "
            f"force={run_stats.get('force', False)}, "
            f"min_quality={run_stats.get('min_quality') or 'none'}, "
            f"include_high_review={run_stats.get('include_high_review', False)}, "
            f"include_duplicates={run_stats.get('include_duplicates', False)}",
            "",
        ])

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    LOGGER.info("Wrote %s", path)


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Cutler Research Brain Phase 3 - build deterministic document cards.",
    )
    parser.add_argument("--db", required=True,
                        help=r'SQLite index, e.g. "data\document_metadata.sqlite"')
    parser.add_argument("--project-root", required=True,
                        help=r'Project root, e.g. "C:\Users\AI Admin\Documents\CutlerResearchBrain"')
    parser.add_argument("--limit", type=int, default=0,
                        help="Stop after N documents are processed (0 = no limit).")
    parser.add_argument("--force", action="store_true",
                        help="Rebuild cards that already exist.")
    parser.add_argument("--only-document-id", default="",
                        help="Build one card by document_id (debugging).")
    parser.add_argument("--include-high-review", action="store_true",
                        help="Also card rows graded High review. May contain client or portfolio "
                             "material - use deliberately.")
    parser.add_argument("--include-duplicates", action="store_true",
                        help="Also card duplicate rows.")
    parser.add_argument("--min-quality", default="",
                        choices=["", "High", "Medium", "Low", "Empty"],
                        help="Only card documents at this extraction quality or better.")
    parser.add_argument("--verbose", action="store_true", help="Chattier console output.")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    project_root = Path(args.project_root).expanduser().resolve()
    validate_project_root(project_root)

    db_path = Path(args.db).expanduser()
    if not db_path.is_absolute():
        db_path = (project_root / db_path).resolve()
    if not db_path.exists():
        raise SystemExit(f"ERROR: database not found: {db_path}\nRun Phase 1 and Phase 2 first.")

    log_path = project_root / "logs" / "phase3_document_cards.log"
    setup_logging(log_path, verbose=args.verbose)

    started = time.time()
    cards_root = project_root / "04_Document_Cards"
    assert_inside_project(cards_root, project_root)
    cards_root.mkdir(parents=True, exist_ok=True)

    LOGGER.info("=" * 78)
    LOGGER.info("Cutler Research Brain - Phase 3 document cards")
    LOGGER.info("Database    : %s", db_path)
    LOGGER.info("Project root: %s", project_root)
    LOGGER.info("Cards output: %s", cards_root)
    LOGGER.info("Deterministic only - no summaries, no interpretation, no external APIs.")
    LOGGER.info("=" * 78)

    min_rank = EXTRACTION_QUALITY_RANK.get(args.min_quality, 0) if args.min_quality else 0

    conn = connect(db_path)
    try:
        if "pdf_extractions" not in {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}:
            raise SystemExit("ERROR: no 'pdf_extractions' table. Run Phase 2 extraction first.")

        create_card_schema(conn)

        rows = select_candidates(conn, args.only_document_id)
        LOGGER.info("Candidate PDF rows: %d", len(rows))
        if args.only_document_id and not rows:
            LOGGER.error("No document with document_id = %s", args.only_document_id)
            return 1

        considered = written = reused = failures = 0

        for row in rows:
            document_id = value_of(row, "document_id")
            extension = value_of(row, "file_extension").lower()
            duplicate_status = value_of(row, "duplicate_status")
            severity = value_of(row, "review_severity")
            status = value_of(row, "pdf_extraction_status")
            quality = value_of(row, "pdf_extraction_quality")
            scanned = bool(int(value_of(row, "scanned_pdf_suspected", "0") or 0))

            # ---- eligibility --------------------------------------------
            if extension != ".pdf":
                upsert_card(conn, make_record(document_id, CARD_SKIPPED_FAILED_EXTRACTION,
                                              QUALITY_NEEDS_REVIEW, False, REASON_NOT_PDF,
                                              None, project_root,
                                              error=f"file_extension is {extension or '(none)'}"))
                continue

            if duplicate_status == "duplicate" and not args.include_duplicates:
                upsert_card(conn, make_record(document_id, CARD_SKIPPED_DUPLICATE,
                                              QUALITY_NEEDS_REVIEW, False, REASON_DUPLICATE,
                                              None, project_root,
                                              error="duplicate of another indexed file"))
                continue

            if severity == "High" and not args.include_high_review:
                upsert_card(conn, make_record(document_id, CARD_SKIPPED_HIGH,
                                              QUALITY_NEEDS_REVIEW, False, REASON_HIGH_REVIEW,
                                              None, project_root,
                                              error="excluded by the Phase 1 High-review gate"))
                continue

            if status not in ("extracted", "partial") or quality == "Failed":
                # Carry over WHY Phase 2 produced no text, rather than calling a
                # deliberately skipped extraction a failure.
                card_status, reason, note = {
                    "skipped_high_review": (CARD_SKIPPED_HIGH, REASON_HIGH_REVIEW,
                                            "extraction skipped by the High-review gate"),
                    "skipped_duplicate": (CARD_SKIPPED_DUPLICATE, REASON_DUPLICATE,
                                          "extraction skipped as a duplicate"),
                    "skipped_missing_file": (CARD_SKIPPED_MISSING_TEXT, REASON_MISSING_TEXT_FILE,
                                             "source file was not found at extraction time"),
                    "skipped_non_pdf": (CARD_SKIPPED_FAILED_EXTRACTION, REASON_NOT_PDF,
                                        "not a PDF"),
                    "failed": (CARD_SKIPPED_FAILED_EXTRACTION, REASON_FAILED_EXTRACTION,
                               "extraction attempt failed"),
                }.get(status, (CARD_SKIPPED_FAILED_EXTRACTION, REASON_FAILED_EXTRACTION,
                               "no extraction record for this document"))
                upsert_card(conn, make_record(document_id, card_status, QUALITY_NEEDS_REVIEW,
                                              False, reason, None, project_root,
                                              error=f"{note} (extraction status: "
                                                    f"{status or 'none'})"))
                continue

            if min_rank and EXTRACTION_QUALITY_RANK.get(quality, 0) < min_rank:
                LOGGER.debug("Below --min-quality (%s < %s): %s", quality, args.min_quality, document_id)
                continue

            # ---- locate the extracted text ------------------------------
            text_path = Path(value_of(row, "document_text_path"))
            if text_path and not text_path.is_absolute():
                text_path = project_root / text_path
            if not text_path or not text_path.exists():
                year = safe_component(value_of(row, "archive_year"), "unknown_year")
                text_path = (project_root / "03_Extracted_Text" / year /
                             safe_component(document_id, "unknown_document") / "document_text.txt")

            if not text_path.exists():
                upsert_card(conn, make_record(document_id, CARD_SKIPPED_MISSING_TEXT,
                                              QUALITY_NEEDS_REVIEW, False, REASON_MISSING_TEXT_FILE,
                                              None, project_root,
                                              error=f"extracted text not found: {text_path}"))
                LOGGER.warning("Missing extracted text: %s", text_path)
                continue

            # ---- card folder ---------------------------------------------
            year = safe_component(value_of(row, "archive_year"), "unknown_year")
            card_dir = cards_root / year / safe_component(document_id, "unknown_document")
            assert_inside_project(card_dir, project_root)

            json_path = card_dir / "document_card.json"
            md_path = card_dir / "document_card.md"

            # ---- resumable -------------------------------------------------
            if json_path.exists() and md_path.exists() and not args.force:
                try:
                    previous = json.loads(json_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    previous = {}
                record = make_record(
                    document_id, previous.get("card_status", CARD_CREATED),
                    previous.get("card_quality", QUALITY_MEDIUM),
                    bool(previous.get("usable_for_rag", False)),
                    previous.get("rag_exclusion_reason", REASON_NONE),
                    card_dir, project_root,
                    preview_chars=len(previous.get("deterministic_preview", "") or ""),
                    heading_count=len(previous.get("detected_heading_candidates", []) or []),
                )
                record["generated_at"] = previous.get("generated_at", record["generated_at"])
                upsert_card(conn, record)
                reused += 1
                LOGGER.debug("Card already exists, skipping: %s", document_id)
                continue

            # ---- build the card ---------------------------------------------
            considered += 1
            try:
                raw = text_path.read_text(encoding="utf-8", errors="replace")
                cleaned = clean_text(raw)
                preview = deterministic_preview(cleaned)
                headings = detect_headings(cleaned)
                title = detect_title(cleaned, headings, value_of(row, "file_name"))

                usable, reason = assess_rag_readiness(
                    status, quality, severity, duplicate_status, scanned,
                    meaningful_chars=len(cleaned), text_file_exists=True,
                    file_extension=extension,
                )
                card_quality = grade_card(row, len(preview), quality, scanned, severity)

                card = build_card_json(row, CARD_CREATED, card_quality, usable, reason,
                                       preview, title, headings, project_root)

                card_dir.mkdir(parents=True, exist_ok=True)
                json_path.write_text(json.dumps(card, indent=2, ensure_ascii=False),
                                     encoding="utf-8")
                md_path.write_text(render_card_md(card), encoding="utf-8", errors="replace")

                record = make_record(document_id, CARD_CREATED, card_quality, usable, reason,
                                     card_dir, project_root, len(preview), len(headings))
                upsert_card(conn, record)
                written += 1

                LOGGER.info("[%d] %s | %s | rag=%s | %d preview chars | %d headings",
                            considered, value_of(row, "relative_path") or document_id,
                            card_quality, "yes" if usable else f"no ({reason})",
                            len(preview), len(headings))
            except Exception as exc:  # noqa: BLE001 - one bad card must not stop the run
                failures += 1
                LOGGER.error("Card FAILED for %s: %s", document_id, exc)
                upsert_card(conn, make_record(document_id, CARD_FAILED, QUALITY_NEEDS_REVIEW,
                                              False, REASON_FAILED_EXTRACTION, card_dir, project_root,
                                              error=f"{type(exc).__name__}: {exc}"))

            conn.commit()

            if args.limit and considered >= args.limit:
                LOGGER.info("Reached --limit of %d documents.", args.limit)
                break

        conn.commit()

        runtime = time.time() - started
        run_stats = {
            "runtime_seconds": runtime,
            "considered": considered,
            "written": written,
            "reused": reused,
            "limit": args.limit,
            "force": args.force,
            "min_quality": args.min_quality,
            "include_high_review": args.include_high_review,
            "include_duplicates": args.include_duplicates,
        }
        totals = write_reports(conn, project_root, run_stats)

        LOGGER.info("-" * 78)
        LOGGER.info("Card build complete in %.1f seconds", runtime)
        LOGGER.info("  processed this run   : %d", considered)
        LOGGER.info("  cards written        : %d", written)
        LOGGER.info("  cards reused         : %d", reused)
        LOGGER.info("  card failures        : %d", failures)
        LOGGER.info("  cards total          : %d  (usable for RAG: %d)",
                    totals["created"], totals["usable"])
        LOGGER.info("  cards output         : %s", cards_root)
        LOGGER.info("-" * 78)
        LOGGER.info("Deterministic cards only - nothing was summarized or interpreted.")
    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
