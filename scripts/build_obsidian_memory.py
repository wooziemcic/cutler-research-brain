#!/usr/bin/env python3
# =============================================================================
# Cutler Research Brain - Phase 4
# build_obsidian_memory.py
#
# Builds a human-browsable OBSIDIAN VAULT from metadata that already exists in
# the database and from the Phase 3 document cards. Every note is a listing,
# a count or a link. Nothing is summarized, paraphrased or interpreted.
#
# WHAT THIS PHASE DOES:
#   - creates the 05_Obsidian_Brain folder structure
#   - writes a dashboard, daily notes, company/ticker pages, source type pages,
#     document type pages, strategy theme pages, a RAG-ready index, a needs
#     review page, an OCR candidates page, a document card index and templates
#   - writes one lightweight STUB note per document card inside the vault, and
#     links every document list to those stubs with ordinary wikilinks
#   - records every note it generates in the obsidian_notes table
#
# PHASE 4.1: document lists used to link OUT to 04_Document_Cards, which sits
# outside the vault. Obsidian cannot resolve such a link and threw
# "Cannot read properties of null reading 'getParentPrefix'", followed by
# "folder already exists". Every document link is now an internal wikilink to a
# stub note under 09_Document_Card_Index/Card_Stubs; the real filesystem paths
# appear as plain text inside the stub only, never as clickable links.
#
# PHASE 4.2: a wikilink is only written when the target note actually exists.
# Ticker candidates that never earned a company page (vendor/noise tokens, or
# tokens below --min-ticker-docs) are rendered as inline code instead of a link,
# so the vault has no unresolved links to chase.
#
# WHAT THIS PHASE DOES NOT DO - deliberately:
#   - no summaries, no paraphrase, no interpretation of any document
#   - no thesis, sentiment, rating, recommendation, catalyst or investment view
#   - nothing inferred from a file being present in the archive
#   - no OpenAI, no Claude API, no external API of any kind
#   - no embeddings, no chunking, no vector index, no retrieval (RAG)
#   - no OCR
#   - no source PDFs copied, no extracted text copied into the vault
#   - nothing written to the V Drive
#
# It also never deletes anything it did not create. Notes generated here carry
# `generated_by: build_obsidian_memory.py` in their front matter and are tracked
# in obsidian_notes; any other file in the vault is left completely alone.
#
# Principle: AI drafts. Humans verify. Cutler decides.
#
# Usage:
#   python scripts\build_obsidian_memory.py ^
#       --db "data\document_metadata.sqlite" ^
#       --project-root "C:\Users\AI Admin\Documents\CutlerResearchBrain" ^
#       --limit 250
# =============================================================================

from __future__ import annotations

import argparse
import csv
import hashlib
import logging
import os
import re
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

PIPELINE_VERSION = "phase4-1.2"
GENERATOR = "build_obsidian_memory.py"

VAULT_ROOT_NAME = "05_Obsidian_Brain"

# The vault's fixed folder structure.
FOLDERS = {
    "dashboard": "00_Dashboard",
    "daily": "01_Daily_Research_Notes",
    "company": "02_Company_Pages",
    "source_type": "03_Source_Type_Pages",
    "document_type": "04_Document_Type_Pages",
    "theme": "05_Strategy_Themes",
    "rag": "06_RAG_Ready_Index",
    "review": "07_Needs_Review",
    "ocr": "08_OCR_Candidates",
    "card_index": "09_Document_Card_Index",
    # Phase 4.1: stub notes live INSIDE the vault so Obsidian can resolve every
    # document link. Linking out to 04_Document_Cards made Obsidian throw
    # "Cannot read properties of null reading 'getParentPrefix'".
    "card_stubs": "09_Document_Card_Index/Card_Stubs",
    "templates": "10_Templates",
}

DASHBOARD_TITLE = "Cutler Research Brain Dashboard"
RAG_INDEX_TITLE = "RAG Ready Documents"
REVIEW_TITLE = "Needs Review"
OCR_TITLE = "OCR Candidates"
CARD_INDEX_TITLE = "Document Card Index"
UNKNOWN_DATE_TITLE = "Unknown_Date"

# Listing caps, so a single note never becomes unusable in Obsidian.
MAX_LINKS_PER_NOTE = 250
MAX_SAMPLE_LINKS = 50
TOP_TICKERS_PER_NOTE = 25

SAFETY_NOTE = ("This vault is deterministic metadata memory only. "
               "No document was summarized or interpreted.")

GUESS_NOTE = ("Every `*_guess` value is an automated classification from file paths and "
              "extraction metadata. Ticker candidates are unverified. Nothing here is an "
              "investment view, recommendation or conclusion.")

LOGGER = logging.getLogger("phase4_obsidian_memory")

OBSIDIAN_NOTE_COLUMNS = [
    "note_id", "note_type", "note_title", "note_path", "related_key",
    "related_value", "source_row_count", "generated_at", "pipeline_version",
]

# Fallback noise tokens, used only if configs/ticker_rules.yaml cannot be read.
FALLBACK_NOISE_TOKENS = {
    "PDF", "DOC", "DOCX", "XLS", "XLSX", "PPT", "PPTX", "CSV", "TXT", "MD",
    "LLC", "INC", "CORP", "THE", "AND", "FOR", "NEW", "NOTE", "REPORT",
    "RESEARCH", "CREDIT", "BANK", "BANKS", "REIT", "REITS", "US", "USA", "SEC",
}


# --------------------------------------------------------------------------
# Logging
# --------------------------------------------------------------------------

def setup_logging(log_path: Path, verbose: bool = False) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    LOGGER.setLevel(logging.DEBUG)
    LOGGER.handlers.clear()

    file_handler = logging.FileHandler(log_path, mode="a", encoding="utf-8")
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s  %(levelname)-8s  %(message)s", "%Y-%m-%d %H:%M:%S"))
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


RE_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
RE_SPACES = re.compile(r"\s+")

WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def safe_note_name(value: str, fallback: str = "Untitled", max_len: int = 80) -> str:
    """A file name that is legal on Windows and still readable in Obsidian.

    Illegal characters become spaces, runs of whitespace collapse, trailing
    dots and spaces are stripped (Windows silently drops them), and reserved
    device names get a suffix.
    """
    name = RE_ILLEGAL.sub(" ", str(value or ""))
    name = name.replace("[", "(").replace("]", ")")  # keep wikilinks unambiguous
    name = RE_SPACES.sub(" ", name).strip().strip(".")
    if not name:
        return fallback
    if name.upper() in WINDOWS_RESERVED or name.upper().split(".")[0] in WINDOWS_RESERVED:
        name = f"{name}_page"
    if len(name) > max_len:
        name = name[:max_len].rstrip().rstrip(".")
    return name or fallback


def note_id_for(relative_path: str) -> str:
    return "note_" + hashlib.sha256(relative_path.replace("\\", "/").lower()
                                    .encode("utf-8")).hexdigest()[:16]


def relative_link(target: Path, note_path: Path) -> str:
    """A markdown-safe relative link from one note to a file in the project."""
    try:
        rel = os.path.relpath(target, note_path.parent)
    except ValueError:
        rel = str(target)
    rel = rel.replace("\\", "/")
    return rel.replace(" ", "%20")


def wikilink(title: str) -> str:
    return f"[[{title}]]"


def vault_link(folder_key: str, title: str) -> str:
    """A folder-qualified wikilink, so same-named notes cannot be confused.

    "Company Material" can be both a source type and a document type; a bare
    [[Company Material]] would leave Obsidian to guess which one is meant.
    """
    return f"[[{FOLDERS[folder_key]}/{title}|{title}]]"


def ticker_link(token: str, company_pages: set) -> str:
    """Link a ticker candidate ONLY when its company page exists (Phase 4.2).

    Company pages are not created for vendor/noise tokens or for tokens below
    --min-ticker-docs, so linking every candidate produced hundreds of
    unresolved links. A candidate without a page is now shown as inline code:
    still visible and searchable, but not a link that goes nowhere.
    """
    title = safe_note_name(token)
    if title in company_pages:
        return f"[[{FOLDERS['company']}/{title}|{title}]]"
    return f"`{title}`"


def render_ticker_list(tokens, company_pages: set, limit: int = 0) -> str:
    """A comma-separated list of ticker candidates, linked only where possible."""
    items = list(tokens)[:limit] if limit else list(tokens)
    if not items:
        return "_(none)_"
    return ", ".join(ticker_link(t, company_pages) for t in items)


def eligible_company_pages(rows: list, noise_tokens: set, min_docs: int) -> set:
    """Which company pages WILL exist - computed before any note is rendered.

    Every builder consults this set, so a link is never written before it is
    known whether the target page is going to be there.
    """
    counts = Counter()
    for row in rows:
        for token in split_tokens(row["ticker_candidates_clean"]):
            counts[token.upper()] += 1

    pages = set()
    for token, count in counts.items():
        if not token or token in noise_tokens or count < min_docs:
            continue
        pages.add(safe_note_name(token))
    return pages


# --------------------------------------------------------------------------
# Database
# --------------------------------------------------------------------------

def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def create_note_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS obsidian_notes (
            note_id          TEXT PRIMARY KEY,
            note_type        TEXT,
            note_title       TEXT,
            note_path        TEXT,
            related_key      TEXT,
            related_value    TEXT,
            source_row_count INTEGER,
            generated_at     TEXT,
            pipeline_version TEXT
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_notes_type ON obsidian_notes(note_type)")
    conn.commit()


def load_rows(conn: sqlite3.Connection, args: argparse.Namespace) -> list[sqlite3.Row]:
    """Every document card row, with the metadata each note needs."""
    where = ["c.card_status = 'created'"]
    params: list = []

    if args.year:
        where.append("COALESCE(d.archive_year,'') = ?")
        params.append(args.year)
    if args.only_date:
        where.append("COALESCE(d.folder_date_guess,'') = ?")
        params.append(args.only_date)
    if args.only_ticker:
        where.append("(',' || UPPER(COALESCE(d.ticker_candidates_clean,'')) || ',') LIKE ?")
        params.append(f"%,{args.only_ticker.upper()},%")

    sql = f"""
        SELECT c.document_id, c.card_status, c.card_quality, c.usable_for_rag,
               c.rag_exclusion_reason, c.card_md_path, c.card_folder,
               COALESCE(d.file_name, '')                AS file_name,
               COALESCE(d.relative_path, '')            AS relative_path,
               COALESCE(d.archive_year, '')             AS archive_year,
               COALESCE(d.detected_year, '')            AS detected_year,
               COALESCE(d.detected_month, '')           AS detected_month,
               COALESCE(d.folder_date_guess, '')        AS folder_date_guess,
               COALESCE(d.ticker_candidates_clean, '')  AS ticker_candidates_clean,
               COALESCE(d.source_vendor_candidates, '') AS source_vendor_candidates,
               COALESCE(d.source_type_guess, 'Unknown') AS source_type_guess,
               COALESCE(d.document_type_guess, 'Unknown') AS document_type_guess,
               COALESCE(d.strategy_theme_guess, 'Unknown') AS strategy_theme_guess,
               COALESCE(d.review_severity, 'None')      AS review_severity,
               COALESCE(d.review_category, '')          AS review_category,
               COALESCE(d.sensitive_context_guess, '')  AS sensitive_context_guess,
               COALESCE(d.duplicate_status, '')         AS duplicate_status,
               COALESCE(d.pdf_extraction_status, '')    AS pdf_extraction_status,
               COALESCE(d.pdf_extraction_quality, '')   AS pdf_extraction_quality,
               COALESCE(d.pdf_page_count, 0)            AS pdf_page_count,
               COALESCE(e.scanned_pdf_suspected, 0)     AS scanned_pdf_suspected
          FROM document_cards c
          LEFT JOIN documents d       ON d.document_id = c.document_id
          LEFT JOIN pdf_extractions e ON e.document_id = c.document_id
         WHERE {' AND '.join(where)}
         ORDER BY d.folder_date_guess DESC, d.relative_path
    """
    if args.limit:
        sql += f" LIMIT {int(args.limit)}"
    return list(conn.execute(sql, params))


def load_noise_tokens(project_root: Path) -> set:
    """Tokens that must never become a company page: vendors, exclusions, themes."""
    tokens = set(FALLBACK_NOISE_TOKENS)
    try:
        import yaml  # noqa: PLC0415
        path = project_root / "configs" / "ticker_rules.yaml"
        if path.exists():
            with path.open("r", encoding="utf-8") as fh:
                rules = yaml.safe_load(fh) or {}
            for key in ("exclusions", "vendor_tokens", "strong_vendor_tokens",
                        "never_ticker_tokens"):
                tokens.update(str(t).upper() for t in rules.get(key, []) or [])
            tokens.update(str(t).upper() for t in (rules.get("theme_tokens", {}) or {}))
            protected = {str(t).upper() for t in rules.get("protected_tickers", []) or []}
            tokens -= protected  # a known Cutler name always gets its page
            LOGGER.info("Loaded %d noise/vendor tokens from configs/ticker_rules.yaml", len(tokens))
    except ImportError:
        LOGGER.warning("PyYAML not installed - using the built-in noise token list.")
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("Could not read ticker_rules.yaml (%s) - using built-in list.", exc)
    return tokens


# --------------------------------------------------------------------------
# Row helpers - counting only, never reading document content
# --------------------------------------------------------------------------

def split_tokens(value: str) -> list[str]:
    return [t.strip() for t in str(value or "").split(",") if t.strip()]


def date_key(row: sqlite3.Row) -> str:
    return row["folder_date_guess"] or UNKNOWN_DATE_TITLE


def counter_table(counter: Counter, header: tuple[str, str], limit: int = 0) -> str:
    rows = counter.most_common(limit) if limit else counter.most_common()
    lines = [f"| {header[0]} | {header[1]} |", "|---|---|"]
    for key, count in rows:
        lines.append(f"| {key} | {count:,} |")
    if not rows:
        lines.append("| (none) | 0 |")
    return "\n".join(lines)


def link_label(value: str, fallback: str = "") -> str:
    """Alias text that is safe inside a wikilink: no pipes, no brackets."""
    label = str(value or fallback or "").strip()
    label = label.replace("|", "-").replace("[", "(").replace("]", ")")
    label = RE_SPACES.sub(" ", label)
    return label or fallback or "document"


def card_link(row: sqlite3.Row, note_path: Path = None, project_root: Path = None) -> str:
    """An INTERNAL wikilink to the document's stub note (Phase 4.1).

    Obsidian can only follow links to notes inside the vault, so every document
    link points at 09_Document_Card_Index/Card_Stubs/<document_id>.md. The stub
    carries the real paths as plain text. note_path and project_root are kept
    in the signature for compatibility with earlier callers; they are unused.
    """
    document_id = str(row["document_id"] or "").strip()
    label = link_label(row["file_name"], document_id)
    if not document_id:
        return f"- {label} _(no document id)_"
    return f"- [[{document_id}|{label}]]"


def card_link_block(rows: list, note_path: Path = None, project_root: Path = None,
                    cap: int = MAX_LINKS_PER_NOTE) -> str:
    if not rows:
        return "_(no documents)_"
    lines = [card_link(row) for row in rows[:cap]]
    if len(rows) > cap:
        lines.append(f"- _...and {len(rows) - cap:,} more; see the manifest CSVs in "
                     f"`02_Metadata_Index`._")
    return "\n".join(lines)


def front_matter(note_type: str, title: str, extra: dict | None = None) -> list[str]:
    lines = ["---", f"type: {note_type}", f"title: {title}"]
    for key, value in (extra or {}).items():
        lines.append(f"{key}: {value}")
    lines += [
        f"generated_by: {GENERATOR}",
        f"pipeline_version: {PIPELINE_VERSION}",
        f"generated_at: {datetime.now().replace(microsecond=0).isoformat()}",
        "deterministic: true",
        "---",
        "",
    ]
    return lines


# --------------------------------------------------------------------------
# Note writing
# --------------------------------------------------------------------------

class VaultWriter:
    """Writes notes, tracks them, and never touches files it did not create."""

    def __init__(self, project_root: Path, vault_root: Path, conn: sqlite3.Connection,
                 force: bool):
        self.project_root = project_root
        self.vault_root = vault_root
        self.conn = conn
        self.force = force
        self.written = 0
        self.unchanged = 0
        self.failed = 0
        self.records: list[dict] = []

    def write(self, folder_key: str, title: str, note_type: str, body: str,
              related_key: str = "", related_value: str = "", row_count: int = 0) -> Path:
        folder = self.vault_root / FOLDERS[folder_key]
        assert_inside_project(folder, self.project_root)
        folder.mkdir(parents=True, exist_ok=True)

        path = folder / f"{safe_note_name(title)}.md"
        assert_inside_project(path, self.project_root)

        try:
            existing = path.read_text(encoding="utf-8") if path.exists() else None
            # Notes are deterministic, so an identical body means no rewrite is
            # needed. The timestamp line is ignored for that comparison.
            if existing is not None and not self.force and _strip_timestamp(existing) == _strip_timestamp(body):
                self.unchanged += 1
            else:
                path.write_text(body, encoding="utf-8", errors="replace")
                self.written += 1
        except OSError as exc:
            self.failed += 1
            LOGGER.error("Could not write note %s: %s", path, exc)
            return path

        relative = str(path.relative_to(self.project_root)).replace("\\", "/")
        record = {
            "note_id": note_id_for(relative),
            "note_type": note_type,
            "note_title": title,
            "note_path": relative,
            "related_key": related_key,
            "related_value": related_value,
            "source_row_count": int(row_count),
            "generated_at": datetime.now().replace(microsecond=0).isoformat(),
            "pipeline_version": PIPELINE_VERSION,
        }
        self.records.append(record)
        self.conn.execute(
            f"INSERT OR REPLACE INTO obsidian_notes ({', '.join(OBSIDIAN_NOTE_COLUMNS)}) "
            f"VALUES ({', '.join('?' for _ in OBSIDIAN_NOTE_COLUMNS)})",
            tuple(record[c] for c in OBSIDIAN_NOTE_COLUMNS),
        )
        return path


RE_TIMESTAMP_LINE = re.compile(r"^generated_at: .*$", re.MULTILINE)


def _strip_timestamp(text: str) -> str:
    return RE_TIMESTAMP_LINE.sub("generated_at: -", text)


# --------------------------------------------------------------------------
# Note builders
# --------------------------------------------------------------------------

def build_card_stubs(writer: VaultWriter, rows: list, project_root: Path,
                     company_pages: set | None = None) -> int:
    """One stub note per document card, INSIDE the vault (Phase 4.1).

    A stub is metadata plus plain-text paths. It never embeds card content or
    extracted text, and no source PDF is opened to build it. Its whole job is to
    give Obsidian a real note to navigate to, so document links resolve instead
    of throwing.
    """
    for row in rows:
        document_id = str(row["document_id"] or "").strip()
        if not document_id:
            continue

        title = safe_note_name(document_id, "unknown_document")
        file_name = row["file_name"] or document_id
        usable = "true" if row["usable_for_rag"] else "false"

        card_path = row["card_md_path"] or ""
        text_path = ""
        if card_path:
            # The extracted text sits under 03_Extracted_Text with the same
            # <archive_year>/<document_id> shape as the card folder.
            text_path = (f"03_Extracted_Text/{row['archive_year'] or 'unknown_year'}/"
                         f"{document_id}/document_text.txt")

        pages = company_pages if company_pages is not None else set()
        date = date_key(row)
        tickers = render_ticker_list(split_tokens(row["ticker_candidates_clean"]), pages)
        vendors = ", ".join(f"`{v}`" for v in split_tokens(row["source_vendor_candidates"])) \
            or "(none)"

        body = "\n".join(
            front_matter("document_card_stub", title,
                         {"document_id": document_id,
                          "archive_year": row["archive_year"] or "(none)",
                          "source_type": row["source_type_guess"],
                          "document_type": row["document_type_guess"],
                          "review_severity": row["review_severity"],
                          "usable_for_rag": usable})
            + [
                f"# {file_name}",
                "",
                "## Basic Metadata",
                "",
                f"- Document ID: `{document_id}`",
                f"- File name: {file_name}",
                f"- Relative path: `{row['relative_path'] or '(none)'}`",
                f"- Archive year: {row['archive_year'] or '(none)'}",
                f"- Folder date: {date if date != UNKNOWN_DATE_TITLE else '(none detected)'}",
                f"- Ticker candidates: {tickers}",
                f"- Source/vendor candidates: {vendors}",
                f"- Source type guess: {row['source_type_guess']}",
                f"- Document type guess: {row['document_type_guess']}",
                f"- Strategy theme guess: {row['strategy_theme_guess']}",
                "",
                "## RAG Readiness",
                "",
                f"- Usable for RAG: {usable}",
                f"- RAG exclusion reason: {row['rag_exclusion_reason'] or 'none'}",
                f"- PDF extraction quality: {row['pdf_extraction_quality'] or '(none)'}",
                f"- Card quality: {row['card_quality'] or '(none)'}",
                "",
                "## Links and Paths",
                "",
                "_Plain text, deliberately not clickable: these paths live outside the vault "
                "and Obsidian cannot follow them. Open them from Windows Explorer._",
                "",
                f"- Document card path: `{card_path or '(none)'}`",
                f"- Extracted text path: `{text_path or '(none)'}`",
                f"- Original source path (V Drive, read-only): `{row['relative_path'] or '(none)'}`",
                "",
                "## Context",
                "",
                f"- Archive date note: {vault_link('daily', safe_note_name(date, UNKNOWN_DATE_TITLE))}",
                f"- Source type page: {vault_link('source_type', safe_note_name(row['source_type_guess']))}",
                f"- Document type page: "
                f"{vault_link('document_type', safe_note_name(row['document_type_guess']))}",
                "",
                "---",
                "",
                f"{SAFETY_NOTE} This stub holds metadata and paths only - no card content and "
                "no extracted text is copied here.",
                "",
                f"Back to {wikilink(CARD_INDEX_TITLE)} or {wikilink(DASHBOARD_TITLE)}.",
                "",
            ]
        )
        writer.write("card_stubs", title, "document_card_stub", body,
                     "document_id", document_id, 1)

    return len(rows)


def build_dashboard(writer: VaultWriter, rows: list, totals: dict, project_root: Path) -> None:
    years = Counter(r["archive_year"] or "(none)" for r in rows)
    dates = {date_key(r) for r in rows}
    note_path = writer.vault_root / FOLDERS["dashboard"] / f"{DASHBOARD_TITLE}.md"

    body = "\n".join(
        front_matter("dashboard", DASHBOARD_TITLE)
        + [
            f"# {DASHBOARD_TITLE}",
            "",
            f"> {SAFETY_NOTE}",
            "",
            "## Project status",
            "",
            "| Phase | Status |",
            "|---|---|",
            "| 1 - Metadata index | complete |",
            "| 2 - PDF text extraction | complete |",
            "| 3 - Document cards | complete |",
            "| 4 - Obsidian memory (this vault) | current |",
            "| 5+ - Embeddings / retrieval / evaluation | **not started** |",
            "",
            "## Archive coverage",
            "",
            counter_table(years, ("Archive year", "Documents in this vault")),
            "",
            f"- Daily research notes in this vault: **{len(dates):,}**",
            "",
            "## Headline counts",
            "",
            "| Measure | Count |",
            "|---|---|",
            f"| PDF rows in the metadata index | {totals['pdf_rows']:,} |",
            f"| Document cards created | {totals['cards_created']:,} |",
            f"| **RAG-ready documents** | **{totals['rag_ready']:,}** |",
            f"| Needs Review | {totals['needs_review']:,} |",
            f"| OCR candidates (scanned / empty text) | {totals['ocr_candidates']:,} |",
            f"| Duplicates gated | {totals['duplicates']:,} |",
            f"| High-review gated | {totals['high_review']:,} |",
            f"| Documents represented in this vault | {len(rows):,} |",
            "",
            "## Sections",
            "",
            f"- {wikilink(RAG_INDEX_TITLE)} - documents eligible for a future retrieval phase",
            f"- {wikilink(REVIEW_TITLE)} - everything a human should look at",
            f"- {wikilink(OCR_TITLE)} - suspected image-only scans",
            f"- {wikilink(CARD_INDEX_TITLE)} - the full document card index",
            "- `01_Daily_Research_Notes` - one note per archive folder date",
            "- `02_Company_Pages` - one page per ticker candidate (unverified)",
            "- `03_Source_Type_Pages`, `04_Document_Type_Pages`, `05_Strategy_Themes`",
            "- `10_Templates` - blank templates for human-written notes",
            "",
            "## What this vault is",
            "",
            "A browsable index of metadata. Each note lists counts and links to the document",
            "cards in `04_Document_Cards`. No document text is copied here, no source PDF is",
            "copied anywhere, and the V Drive is never modified.",
            "",
            f"{GUESS_NOTE}",
            "",
            "**AI drafts. Humans verify. Cutler decides.**",
            "",
        ]
    )
    writer.write("dashboard", DASHBOARD_TITLE, "dashboard", body,
                 "dashboard", "", len(rows))
    del note_path


def build_daily_notes(writer: VaultWriter, rows: list, project_root: Path,
                      company_pages: set) -> int:
    by_date = defaultdict(list)
    for row in rows:
        by_date[date_key(row)].append(row)

    for date, date_rows in sorted(by_date.items()):
        title = safe_note_name(date, UNKNOWN_DATE_TITLE)
        note_path = writer.vault_root / FOLDERS["daily"] / f"{title}.md"

        rag_ready = [r for r in date_rows if r["usable_for_rag"]]
        review_rows = [r for r in date_rows
                       if r["review_severity"] in ("High", "Medium")
                       or r["card_quality"] == "Needs Review"]
        ocr_rows = [r for r in date_rows if r["rag_exclusion_reason"] == "scanned_pdf_no_text"]

        tickers = Counter()
        for row in date_rows:
            tickers.update(split_tokens(row["ticker_candidates_clean"]))

        ticker_links = render_ticker_list(
            [t for t, _ in tickers.most_common(TOP_TICKERS_PER_NOTE)], company_pages)

        body = "\n".join(
            front_matter("daily_research_note", title,
                         {"date": date, "document_count": len(date_rows),
                          "rag_ready_count": len(rag_ready)})
            + [
                f"# {title}",
                "",
                f"Archive folder date: **{date}**" if date != UNKNOWN_DATE_TITLE
                else "Documents with **no folder date** detected in the archive path.",
                "",
                f"- Documents: **{len(date_rows):,}**",
                f"- RAG-ready: **{len(rag_ready):,}**",
                f"- Needs review: {len(review_rows):,}",
                f"- OCR candidates: {len(ocr_rows):,}",
                "",
                "## Source types",
                "",
                counter_table(Counter(r["source_type_guess"] for r in date_rows),
                              ("Source type guess", "Documents")),
                "",
                "## Document types",
                "",
                counter_table(Counter(r["document_type_guess"] for r in date_rows),
                              ("Document type guess", "Documents")),
                "",
                "## Ticker candidates seen on this date",
                "",
                ticker_links,
                "",
                "_Linked candidates have a company page; the rest are shown as code because no "
                "page was created for them (vendor/noise token, or too few documents)._",
                "",
                "## Documents",
                "",
                card_link_block(date_rows, note_path, project_root),
                "",
                "## Needs review on this date",
                "",
                card_link_block(review_rows, note_path, project_root, MAX_SAMPLE_LINKS)
                if review_rows else "_(none)_",
                "",
                "## OCR candidates on this date",
                "",
                card_link_block(ocr_rows, note_path, project_root, MAX_SAMPLE_LINKS)
                if ocr_rows else "_(none)_",
                "",
                "---",
                "",
                f"{SAFETY_NOTE} {GUESS_NOTE}",
                "",
                f"Back to {wikilink(DASHBOARD_TITLE)}.",
                "",
            ]
        )
        writer.write("daily", title, "daily_research_note", body, "date", date, len(date_rows))

    return len(by_date)


def build_company_pages(writer: VaultWriter, rows: list, project_root: Path,
                        noise_tokens: set, min_docs: int) -> tuple[int, int]:
    """One page per ticker CANDIDATE. Vendors and noise tokens are excluded."""
    by_ticker = defaultdict(list)
    vendor_only = Counter()

    for row in rows:
        for token in split_tokens(row["ticker_candidates_clean"]):
            by_ticker[token.upper()].append(row)
        for token in split_tokens(row["source_vendor_candidates"]):
            vendor_only[token.upper()] += 1

    skipped = 0
    created = 0

    for ticker, ticker_rows in sorted(by_ticker.items()):
        if not ticker or ticker in noise_tokens or len(ticker_rows) < min_docs:
            skipped += 1
            LOGGER.debug("Skipping company page for %s (noise/vendor or too few documents)", ticker)
            continue

        title = safe_note_name(ticker)
        note_path = writer.vault_root / FOLDERS["company"] / f"{title}.md"

        rag_ready = [r for r in ticker_rows if r["usable_for_rag"]]
        dates = sorted({date_key(r) for r in ticker_rows})
        vendors = Counter()
        for row in ticker_rows:
            vendors.update(split_tokens(row["source_vendor_candidates"]))

        date_links = ", ".join(vault_link("daily", safe_note_name(d, UNKNOWN_DATE_TITLE))
                               for d in dates[:60]) or "_(none)_"
        if len(dates) > 60:
            date_links += f", _...and {len(dates) - 60:,} more dates_"

        body = "\n".join(
            front_matter("company_page", title,
                         {"ticker_candidate": ticker, "verified": "false",
                          "document_count": len(ticker_rows),
                          "rag_ready_count": len(rag_ready)})
            + [
                f"# {title}",
                "",
                f"> **{ticker} is an unverified ticker CANDIDATE** detected from file names by "
                "the Phase 1 rules. It is not a confirmed security mapping, and no security "
                "master has been consulted. A human must verify it before it is relied on.",
                "",
                f"- Documents mentioning this candidate: **{len(ticker_rows):,}**",
                f"- RAG-ready: **{len(rag_ready):,}**",
                f"- Date coverage: {len(dates):,} archive dates "
                f"({dates[0] if dates else 'n/a'} to {dates[-1] if dates else 'n/a'})",
                "",
                "## Source types",
                "",
                counter_table(Counter(r["source_type_guess"] for r in ticker_rows),
                              ("Source type guess", "Documents")),
                "",
                "## Document types",
                "",
                counter_table(Counter(r["document_type_guess"] for r in ticker_rows),
                              ("Document type guess", "Documents")),
                "",
                "## Research providers seen alongside this candidate",
                "",
                counter_table(vendors, ("Source/vendor candidate", "Documents"), 20),
                "",
                "_Vendors are the research providers named in the file name. They are not "
                "company tickers and say nothing about the document's content._",
                "",
                "## Dates",
                "",
                date_links,
                "",
                "## Documents",
                "",
                card_link_block(ticker_rows, note_path, project_root),
                "",
                "---",
                "",
                f"{SAFETY_NOTE} No thesis, sentiment, rating or investment view is expressed "
                "or implied by this page, and nothing is inferred from these files existing.",
                "",
                f"Back to {wikilink(DASHBOARD_TITLE)}.",
                "",
            ]
        )
        writer.write("company", title, "company_page", body, "ticker", ticker, len(ticker_rows))
        created += 1

    LOGGER.info("Company pages: %d created, %d candidate tokens skipped as vendor/noise/too-few.",
                created, skipped)
    return created, skipped


def build_grouped_pages(writer: VaultWriter, rows: list, project_root: Path,
                        field: str, folder_key: str, note_type: str,
                        label: str, company_pages: set, theme_warning: bool = False) -> int:
    grouped = defaultdict(list)
    for row in rows:
        grouped[row[field] or "Unknown"].append(row)

    for value, group_rows in sorted(grouped.items()):
        title = safe_note_name(value)
        note_path = writer.vault_root / FOLDERS[folder_key] / f"{title}.md"

        rag_ready = [r for r in group_rows if r["usable_for_rag"]]
        dates = sorted({date_key(r) for r in group_rows})
        tickers = Counter()
        for row in group_rows:
            tickers.update(split_tokens(row["ticker_candidates_clean"]))

        ticker_links = render_ticker_list(
            [t for t, _ in tickers.most_common(TOP_TICKERS_PER_NOTE)], company_pages)

        lines = front_matter(note_type, title,
                             {"group_field": field, "group_value": value,
                              "document_count": len(group_rows),
                              "rag_ready_count": len(rag_ready)})
        lines += [
            f"# {title}",
            "",
            f"{label}: **{value}** (automated guess)",
            "",
        ]
        if theme_warning:
            lines += [
                "> **Themes are metadata guesses only, not confirmed investment themes.** The "
                "theme was inferred from words in the file path by the Phase 1 keyword rules. "
                "It is not a view, a mandate, or a statement about what any document argues.",
                "",
            ]
        lines += [
            f"- Documents: **{len(group_rows):,}**",
            f"- RAG-ready: **{len(rag_ready):,}**",
            f"- Date coverage: {len(dates):,} archive dates "
            f"({dates[0] if dates else 'n/a'} to {dates[-1] if dates else 'n/a'})",
            "",
            "## Top ticker candidates (unverified)",
            "",
            ticker_links,
            "",
            "## Documents",
            "",
            card_link_block(group_rows, note_path, project_root),
            "",
            "---",
            "",
            f"{SAFETY_NOTE} {GUESS_NOTE}",
            "",
            f"Back to {wikilink(DASHBOARD_TITLE)}.",
            "",
        ]
        writer.write(folder_key, title, note_type, "\n".join(lines), field, value, len(group_rows))

    return len(grouped)


def build_rag_index(writer: VaultWriter, rows: list, project_root: Path,
                    company_pages: set) -> None:
    ready = [r for r in rows if r["usable_for_rag"]]
    note_path = writer.vault_root / FOLDERS["rag"] / f"{RAG_INDEX_TITLE}.md"

    tickers = Counter()
    for row in ready:
        tickers.update(split_tokens(row["ticker_candidates_clean"]))

    body = "\n".join(
        front_matter("rag_ready_index", RAG_INDEX_TITLE,
                     {"rag_ready_count": len(ready)})
        + [
            f"# {RAG_INDEX_TITLE}",
            "",
            "> **Nothing has been embedded, chunked, indexed or retrieved.** This page marks "
            "which documents are ELIGIBLE for a future retrieval phase - it is a plumbing "
            "flag about text availability, not a judgement about research value.",
            "",
            f"- RAG-ready documents in this vault: **{len(ready):,}**",
            "",
            "## By source type",
            "",
            counter_table(Counter(r["source_type_guess"] for r in ready),
                          ("Source type guess", "Documents")),
            "",
            "## By document type",
            "",
            counter_table(Counter(r["document_type_guess"] for r in ready),
                          ("Document type guess", "Documents")),
            "",
            "## By archive year",
            "",
            counter_table(Counter(r["archive_year"] or "(none)" for r in ready),
                          ("Archive year", "Documents")),
            "",
            "## Top ticker candidates (unverified)",
            "",
            render_ticker_list([t for t, _ in tickers.most_common(40)], company_pages),
            "",
            "## Documents",
            "",
            card_link_block(ready, note_path, project_root),
            "",
            "---",
            "",
            f"{SAFETY_NOTE}",
            "",
            f"Back to {wikilink(DASHBOARD_TITLE)}.",
            "",
        ]
    )
    writer.write("rag", RAG_INDEX_TITLE, "rag_ready_index", body, "index", "rag_ready", len(ready))


def build_needs_review(writer: VaultWriter, rows: list, totals: dict, project_root: Path) -> None:
    note_path = writer.vault_root / FOLDERS["review"] / f"{REVIEW_TITLE}.md"

    review_rows = [r for r in rows
                   if r["review_severity"] in ("High", "Medium")
                   or r["card_quality"] == "Needs Review"]
    high_rows = [r for r in rows if r["review_severity"] == "High"]
    failed_rows = [r for r in rows if r["rag_exclusion_reason"] == "failed_extraction"]
    missing_rows = [r for r in rows if r["rag_exclusion_reason"] == "missing_text_file"]
    empty_rows = [r for r in rows if r["rag_exclusion_reason"] == "empty_text"]
    scanned_rows = [r for r in rows if r["rag_exclusion_reason"] == "scanned_pdf_no_text"]

    categories = Counter()
    for row in review_rows:
        for category in str(row["review_category"] or "").split(";"):
            if category.strip():
                categories[category.strip()] += 1

    body = "\n".join(
        front_matter("needs_review", REVIEW_TITLE, {"needs_review_count": len(review_rows)})
        + [
            f"# {REVIEW_TITLE}",
            "",
            "> This is a work queue, not a verdict on any document. Work High severity first, "
            "then Medium. Low severity is usually a classification gap fixed by editing the "
            "rules in `configs/`, not by opening files.",
            "",
            f"- Documents needing review in this vault: **{len(review_rows):,}**",
            f"- High review (gated, possible client/portfolio material): **{len(high_rows):,}**",
            f"- Failed extraction: {len(failed_rows):,}",
            f"- Missing text file: {len(missing_rows):,}",
            f"- Empty text: {len(empty_rows):,}",
            f"- Suspected scans with no text: {len(scanned_rows):,} (see {wikilink(OCR_TITLE)})",
            "",
            "## By review severity",
            "",
            counter_table(Counter(r["review_severity"] for r in review_rows),
                          ("Review severity", "Documents")),
            "",
            "## By review category",
            "",
            counter_table(categories, ("Review category", "Documents")),
            "",
            "## High review rows",
            "",
            "_These are gated out of extraction and retrieval until a human clears them. "
            "They may contain client or portfolio material._",
            "",
            card_link_block(high_rows, note_path, project_root, MAX_SAMPLE_LINKS)
            if high_rows else "_(none in this vault)_",
            "",
            "## Failed extraction",
            "",
            card_link_block(failed_rows, note_path, project_root, MAX_SAMPLE_LINKS)
            if failed_rows else "_(none in this vault)_",
            "",
            "## Missing or empty text",
            "",
            card_link_block(missing_rows + empty_rows, note_path, project_root, MAX_SAMPLE_LINKS)
            if (missing_rows or empty_rows) else "_(none in this vault)_",
            "",
            "## Full review queue",
            "",
            card_link_block(review_rows, note_path, project_root),
            "",
            "## Supporting reports",
            "",
            "The complete lists live in `02_Metadata_Index`:",
            "",
            "- `high_review_report.csv`",
            "- `sensitive_data_review_report.csv`",
            "- `document_card_needs_review_report.csv`",
            "- `true_failed_extractions.csv`",
            "- `missing_text_file_card_report.csv`",
            "- `empty_text_card_report.csv`",
            "",
            "---",
            "",
            f"{SAFETY_NOTE}",
            "",
            f"Back to {wikilink(DASHBOARD_TITLE)}.",
            "",
        ]
    )
    writer.write("review", REVIEW_TITLE, "needs_review", body, "index", "needs_review",
                 len(review_rows))


def build_ocr_candidates(writer: VaultWriter, rows: list, project_root: Path) -> None:
    note_path = writer.vault_root / FOLDERS["ocr"] / f"{OCR_TITLE}.md"

    scanned_rows = [r for r in rows if r["rag_exclusion_reason"] == "scanned_pdf_no_text"]
    empty_rows = [r for r in rows if r["rag_exclusion_reason"] == "empty_text"]

    body = "\n".join(
        front_matter("ocr_candidates", OCR_TITLE,
                     {"scanned_pdf_no_text": len(scanned_rows), "empty_text": len(empty_rows)})
        + [
            f"# {OCR_TITLE}",
            "",
            "> **OCR has not been performed.** These are PDFs where text extraction found "
            "little or nothing on pages that exist - normally an image-only scan. They are "
            "listed here so a decision about OCR can be made later. Nothing has been read "
            "from these files.",
            "",
            f"- `scanned_pdf_no_text` (suspected image-only scans): **{len(scanned_rows):,}**",
            f"- `empty_text` (extraction ran, no usable text layer): **{len(empty_rows):,}**",
            f"- Total OCR candidates in this vault: **{len(scanned_rows) + len(empty_rows):,}**",
            "",
            "## By source type",
            "",
            counter_table(Counter(r["source_type_guess"] for r in scanned_rows + empty_rows),
                          ("Source type guess", "Documents")),
            "",
            "## By archive year",
            "",
            counter_table(Counter((r["archive_year"] or "(none)") for r in scanned_rows + empty_rows),
                          ("Archive year", "Documents")),
            "",
            "## Suspected scans",
            "",
            card_link_block(scanned_rows, note_path, project_root),
            "",
            "## Empty text",
            "",
            card_link_block(empty_rows, note_path, project_root, MAX_SAMPLE_LINKS)
            if empty_rows else "_(none in this vault)_",
            "",
            "## Supporting reports",
            "",
            "- `02_Metadata_Index/scanned_no_text_card_report.csv`",
            "- `02_Metadata_Index/empty_text_card_report.csv`",
            "- `02_Metadata_Index/scanned_pdf_suspected_report.csv`",
            "",
            "---",
            "",
            f"{SAFETY_NOTE}",
            "",
            f"Back to {wikilink(DASHBOARD_TITLE)}.",
            "",
        ]
    )
    writer.write("ocr", OCR_TITLE, "ocr_candidates", body, "index", "ocr_candidates",
                 len(scanned_rows) + len(empty_rows))


def build_card_index(writer: VaultWriter, rows: list, project_root: Path) -> None:
    note_path = writer.vault_root / FOLDERS["card_index"] / f"{CARD_INDEX_TITLE}.md"
    by_year = Counter(r["archive_year"] or "(none)" for r in rows)

    year_lines = []
    for year in sorted(by_year):
        # Plain text on purpose: 04_Document_Cards is outside the vault, so a
        # clickable link there is exactly what broke navigation in Phase 4.0.
        year_lines.append(f"- **{year}** - {by_year[year]:,} cards - "
                          f"`04_Document_Cards/{year}/`")

    body = "\n".join(
        front_matter("document_card_index", CARD_INDEX_TITLE, {"card_count": len(rows)})
        + [
            f"# {CARD_INDEX_TITLE}",
            "",
            f"- Document cards represented in this vault: **{len(rows):,}**",
            "",
            "Cards live in `04_Document_Cards/<archive_year>/<document_id>/document_card.md`, "
            "outside this vault. Inside the vault each document has a **stub note** under "
            "`09_Document_Card_Index/Card_Stubs/<document_id>.md` holding its metadata and the "
            "real paths as plain text. Every document link in this vault points at a stub, "
            "which is what keeps Obsidian navigation working.",
            "",
            "## By archive year",
            "",
            "\n".join(year_lines) if year_lines else "_(none)_",
            "",
            "## By source type",
            "",
            counter_table(Counter(r["source_type_guess"] for r in rows),
                          ("Source type guess", "Cards")),
            "",
            "## By document type",
            "",
            counter_table(Counter(r["document_type_guess"] for r in rows),
                          ("Document type guess", "Cards")),
            "",
            "## By card quality",
            "",
            counter_table(Counter(r["card_quality"] for r in rows), ("Card quality", "Cards")),
            "",
            "_Card quality measures how complete the card is - text volume plus how much "
            "metadata is filled in. It is not a judgement about the document._",
            "",
            f"## Sample of {MAX_SAMPLE_LINKS} cards",
            "",
            card_link_block(rows, note_path, project_root, MAX_SAMPLE_LINKS),
            "",
            "The complete list is in `02_Metadata_Index/document_card_manifest.csv`.",
            "",
            "---",
            "",
            f"{SAFETY_NOTE}",
            "",
            f"Back to {wikilink(DASHBOARD_TITLE)}.",
            "",
        ]
    )
    writer.write("card_index", CARD_INDEX_TITLE, "document_card_index", body,
                 "index", "document_cards", len(rows))


def build_templates(writer: VaultWriter) -> int:
    """Blank templates for HUMAN-written notes. No AI conclusions anywhere."""
    templates = {
        "Daily Research Note Template": [
            "# {{date}}",
            "",
            "_Human notes for this date. The generated daily note in "
            "`01_Daily_Research_Notes` holds the automated counts and links._",
            "",
            "## What was reviewed",
            "",
            "- ",
            "",
            "## Questions raised",
            "",
            "- ",
            "",
            "## Follow-ups",
            "",
            "- [ ] ",
            "",
            "## Documents referenced",
            "",
            "- ",
            "",
        ],
        "Company Page Template": [
            "# {{TICKER}}",
            "",
            "> Ticker candidates in this vault are unverified. Confirm the security mapping "
            "before relying on it.",
            "",
            "## Verified identity",
            "",
            "- Company name: ",
            "- Confirmed ticker / exchange: ",
            "- Verified by: ",
            "- Date verified: ",
            "",
            "## Human notes",
            "",
            "- ",
            "",
            "## Open questions",
            "",
            "- ",
            "",
        ],
        "Document Card Link Template": [
            "# Document reference",
            "",
            "- Document ID: ",
            "- File name: ",
            "- Archive date: ",
            "- Card stub (inside the vault): paste a wikilink to the document id, which "
            "lives in `09_Document_Card_Index/Card_Stubs`",
            "- Card path (outside the vault, plain text): "
            "`04_Document_Cards/<year>/<document_id>/document_card.md`",
            "- Source type guess: ",
            "- Document type guess: ",
            "",
            "## Why this document matters (human input)",
            "",
            "- ",
            "",
        ],
        "Needs Review Template": [
            "# Review item",
            "",
            "- Document ID: ",
            "- Review severity: ",
            "- Review category: ",
            "- Card: ",
            "",
            "## What needs checking",
            "",
            "- ",
            "",
            "## Decision",
            "",
            "- Reviewed by: ",
            "- Date: ",
            "- Outcome: ",
            "",
        ],
    }

    for title, lines in templates.items():
        body = "\n".join(
            front_matter("template", title, {"fill_in_by": "human"})
            + lines
            + [
                "---",
                "",
                "_Blank template. It contains no automated analysis, summary or investment "
                "view, and none should be added by any automated process._",
                "",
            ]
        )
        writer.write("templates", title, "template", body, "template", title, 0)

    return len(templates)


# --------------------------------------------------------------------------
# Reports
# --------------------------------------------------------------------------

def write_csv(path: Path, header: list[str], rows) -> int:
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


def write_reports(conn: sqlite3.Connection, project_root: Path, rows: list,
                  totals: dict, run_stats: dict) -> None:
    data_dir = project_root / "data"
    index_dir = project_root / "02_Metadata_Index"
    for directory in (data_dir, index_dir):
        assert_inside_project(directory, project_root)
        directory.mkdir(parents=True, exist_ok=True)

    manifest = [tuple(r) for r in conn.execute(
        f"SELECT {', '.join(OBSIDIAN_NOTE_COLUMNS)} FROM obsidian_notes "
        f"ORDER BY note_type, note_title")]
    write_csv(data_dir / "obsidian_note_manifest.csv", OBSIDIAN_NOTE_COLUMNS, manifest)
    write_csv(index_dir / "obsidian_note_manifest.csv", OBSIDIAN_NOTE_COLUMNS, manifest)

    # ---- daily note report ------------------------------------------------
    by_date = defaultdict(list)
    for row in rows:
        by_date[date_key(row)].append(row)
    daily_rows = []
    for date in sorted(by_date):
        date_rows = by_date[date]
        daily_rows.append((
            date, len(date_rows),
            sum(1 for r in date_rows if r["usable_for_rag"]),
            sum(1 for r in date_rows if r["review_severity"] in ("High", "Medium")
                or r["card_quality"] == "Needs Review"),
            sum(1 for r in date_rows if r["rag_exclusion_reason"] == "scanned_pdf_no_text"),
            len({t for r in date_rows for t in split_tokens(r["ticker_candidates_clean"])}),
            f"{FOLDERS['daily']}/{safe_note_name(date, UNKNOWN_DATE_TITLE)}.md",
        ))
    write_csv(index_dir / "obsidian_daily_note_report.csv",
              ["date", "document_count", "rag_ready_count", "needs_review_count",
               "ocr_candidate_count", "distinct_ticker_candidates", "note_path"],
              daily_rows)

    # ---- company page report ----------------------------------------------
    company_rows = []
    for record in conn.execute(
        "SELECT note_title, related_value, source_row_count, note_path FROM obsidian_notes "
        "WHERE note_type = 'company_page' ORDER BY source_row_count DESC, note_title"
    ):
        ticker = record["related_value"]
        matching = [r for r in rows
                    if ticker in [t.upper() for t in split_tokens(r["ticker_candidates_clean"])]]
        dates = sorted({date_key(r) for r in matching})
        company_rows.append((
            record["note_title"], ticker, record["source_row_count"],
            sum(1 for r in matching if r["usable_for_rag"]),
            len(dates), dates[0] if dates else "", dates[-1] if dates else "",
            record["note_path"],
        ))
    write_csv(index_dir / "obsidian_company_page_report.csv",
              ["note_title", "ticker_candidate", "document_count", "rag_ready_count",
               "date_count", "first_date", "last_date", "note_path"],
              company_rows)

    # ---- needs review report ----------------------------------------------
    review_rows = [
        (r["document_id"], r["file_name"], r["relative_path"], r["review_severity"],
         r["review_category"], r["card_quality"], r["rag_exclusion_reason"],
         date_key(r), r["card_md_path"])
        for r in rows
        if r["review_severity"] in ("High", "Medium") or r["card_quality"] == "Needs Review"
    ]
    write_csv(index_dir / "obsidian_needs_review_report.csv",
              ["document_id", "file_name", "relative_path", "review_severity",
               "review_category", "card_quality", "rag_exclusion_reason",
               "archive_date", "card_md_path"],
              review_rows)

    # ---- Phase 4.1 reports ------------------------------------------------
    write_card_stub_report(conn, index_dir / "obsidian_card_stub_report.csv", rows)
    write_link_health_report(project_root, index_dir / "obsidian_link_health_report.csv")

    write_summary(conn, index_dir / "obsidian_memory_summary.md", rows, totals, run_stats)


# --------------------------------------------------------------------------
# Phase 4.1: stub inventory and link health
# --------------------------------------------------------------------------

RE_WIKILINK = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]*))?\]\]")
RE_MD_LINK = re.compile(r"\]\((?!https?:)([^)]+)\)")
RE_GENERATED_BY = re.compile(r"^generated_by:\s*(.+)$", re.MULTILINE)

RE_FENCED = re.compile(r"```.*?```", re.DOTALL)
RE_INLINE_CODE = re.compile(r"`[^`\n]*`")


def strip_code(text: str) -> str:
    """Remove fenced blocks and inline code before auditing links.

    Obsidian does not resolve a wikilink written inside code, so neither should
    the audit - otherwise every documented example looks like a broken link.
    """
    return RE_INLINE_CODE.sub(" ", RE_FENCED.sub(" ", text))




def write_card_stub_report(conn: sqlite3.Connection, path: Path, rows: list) -> None:
    """One row per stub note, so the stub inventory can be reconciled."""
    stub_paths = {
        r["related_value"]: r["note_path"]
        for r in conn.execute(
            "SELECT related_value, note_path FROM obsidian_notes "
            "WHERE note_type = 'document_card_stub'")
    }
    report_rows = []
    for row in rows:
        document_id = str(row["document_id"] or "")
        report_rows.append((
            document_id, row["file_name"], row["archive_year"], date_key(row),
            row["source_type_guess"], row["document_type_guess"],
            "yes" if row["usable_for_rag"] else "no",
            row["rag_exclusion_reason"] or "none",
            row["card_quality"] or "",
            stub_paths.get(document_id, ""),
            "yes" if document_id in stub_paths else "NO STUB",
            row["card_md_path"] or "",
        ))
    write_csv(path,
              ["document_id", "file_name", "archive_year", "archive_date", "source_type_guess",
               "document_type_guess", "usable_for_rag", "rag_exclusion_reason", "card_quality",
               "stub_note_path", "stub_exists", "card_md_path"],
              report_rows)


KNOWN_SECTION_TITLES = {
    DASHBOARD_TITLE, RAG_INDEX_TITLE, REVIEW_TITLE, OCR_TITLE, CARD_INDEX_TITLE,
}

# Broken-link categories (Phase 4.2)
CATEGORY_STUB = "broken_document_stub_link"
CATEGORY_TICKER = "broken_company_ticker_link"
CATEGORY_SECTION = "broken_section_link"
CATEGORY_OTHER = "broken_other_link"

RE_TICKER_LIKE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")


def resolvable_targets(vault_root: Path) -> set:
    """Every string Obsidian could resolve: bare note names and folder paths."""
    targets = set()
    for note in vault_root.rglob("*.md"):
        targets.add(note.stem)
        targets.add(str(note.relative_to(vault_root).with_suffix("")).replace("\\", "/"))
    return targets


def classify_broken(target: str) -> str:
    """What KIND of link is broken - so the fix is obvious from the report."""
    target = target.strip()
    tail = target.rsplit("/", 1)[-1]
    if tail.startswith("doc_"):
        return CATEGORY_STUB
    if tail in KNOWN_SECTION_TITLES:
        return CATEGORY_SECTION
    if RE_TICKER_LIKE.match(tail):
        return CATEGORY_TICKER
    return CATEGORY_OTHER


def audit_vault_links(vault_root: Path) -> tuple[list[tuple], list[tuple], dict]:
    """Audit every generated note's links. Read-only; nothing is modified.

    Returns (per_note_rows, broken_rows, totals). Human-written notes are
    counted and then skipped entirely - they are never analysed or touched.
    """
    empty_totals = {
        "notes": 0, "human_notes": 0, "wikilinks": 0, "internal_stub": 0, "external": 0,
        "broken": 0, CATEGORY_STUB: 0, CATEGORY_TICKER: 0, CATEGORY_SECTION: 0,
        CATEGORY_OTHER: 0,
    }
    if not vault_root.exists():
        return [], [], empty_totals

    targets = resolvable_targets(vault_root)
    per_note: list[tuple] = []
    broken_rows: list[tuple] = []
    totals = dict(empty_totals)

    for note in sorted(vault_root.rglob("*.md")):
        try:
            text = note.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        generated = RE_GENERATED_BY.search(text[:600])
        if not generated or GENERATOR not in generated.group(1):
            totals["human_notes"] += 1
            continue

        totals["notes"] += 1
        scannable = strip_code(text)
        links = RE_WIKILINK.findall(scannable)
        relative_note = str(note.relative_to(vault_root)).replace("\\", "/")

        note_type = "unknown"
        match = re.search(r"^type:\s*(.+)$", text[:400], re.MULTILINE)
        if match:
            note_type = match.group(1).strip()

        stub_links = 0
        note_broken = []
        for target, alias in links:
            target = target.strip()
            totals["wikilinks"] += 1
            if target.rsplit("/", 1)[-1].startswith("doc_"):
                stub_links += 1
            if target in targets:
                continue
            category = classify_broken(target)
            note_broken.append((target, category))
            totals["broken"] += 1
            totals[category] += 1
            broken_rows.append((relative_note, note_type, target,
                                (alias or "").strip(), category))

        external = [m for m in RE_MD_LINK.findall(scannable) if "04_Document_Cards" in m]
        totals["internal_stub"] += stub_links
        totals["external"] += len(external)

        status = "ok"
        if external:
            status = "EXTERNAL CARD LINKS - run refresh_obsidian_links.py --rewrite-links"
        elif note_broken:
            status = "BROKEN WIKILINKS - run repair_broken_wikilinks.py --rewrite-links"

        per_note.append((
            relative_note, note_type, len(links), stub_links, len(external),
            len(note_broken),
            sum(1 for _, c in note_broken if c == CATEGORY_STUB),
            sum(1 for _, c in note_broken if c == CATEGORY_TICKER),
            sum(1 for _, c in note_broken if c == CATEGORY_SECTION),
            "; ".join(sorted({t for t, _ in note_broken})[:5]),
            status,
        ))

    return per_note, broken_rows, totals


def write_link_health_report(project_root: Path, path: Path) -> dict:
    """Writes obsidian_link_health_report.csv and obsidian_broken_wikilink_report.csv."""
    vault_root = project_root / VAULT_ROOT_NAME
    per_note, broken_rows, totals = audit_vault_links(vault_root)

    write_csv(path,
              ["note_path", "note_type", "wikilinks_total", "internal_stub_links",
               "external_card_links", "broken_wikilinks", "broken_document_stub_links",
               "broken_company_ticker_links", "broken_section_links",
               "broken_examples", "status"],
              per_note)

    write_csv(path.parent / "obsidian_broken_wikilink_report.csv",
              ["note_path", "note_type", "broken_target", "link_alias", "category"],
              broken_rows)

    LOGGER.info("Link health: %d generated note(s), %d wikilink(s), %d internal stub link(s), "
                "%d external card link(s), %d broken (stub %d / ticker %d / section %d / "
                "other %d), %d human note(s) untouched.",
                totals["notes"], totals["wikilinks"], totals["internal_stub"],
                totals["external"], totals["broken"], totals[CATEGORY_STUB],
                totals[CATEGORY_TICKER], totals[CATEGORY_SECTION], totals[CATEGORY_OTHER],
                totals["human_notes"])
    return totals


def write_summary(conn: sqlite3.Connection, path: Path, rows: list,
                  totals: dict, run_stats: dict) -> None:
    generated = datetime.now().replace(microsecond=0).isoformat()
    by_type = [tuple(r) for r in conn.execute(
        "SELECT note_type, COUNT(*) FROM obsidian_notes GROUP BY 1 ORDER BY COUNT(*) DESC")]
    note_total = sum(count for _, count in by_type)

    lines = [
        "# Phase 4 - Obsidian Memory Summary",
        "",
        f"_Generated: {generated}_",
        "",
        "## In one line",
        "",
        f"**{note_total:,} Obsidian notes** now index **{len(rows):,} document cards** in "
        f"`05_Obsidian_Brain`. Every note is counts and links - "
        "**no document was summarized or interpreted.**",
        "",
        "## What this vault is",
        "",
        "- A browsable index over metadata that already existed in the database.",
        "- Notes link to `04_Document_Cards/.../document_card.md`; card text is never copied.",
        "- No source PDF was opened or copied. The V Drive was not touched.",
        "- No embeddings, no retrieval, no OCR, no external APIs, no model calls.",
        "- No thesis, sentiment, rating, recommendation or catalyst was inferred, and nothing",
        "  was concluded from a file simply being present in the archive.",
        "",
        "## Notes by type",
        "",
        counter_table(Counter({t: c for t, c in by_type}), ("Note type", "Notes")),
        "",
        "## Coverage",
        "",
        "| Measure | Count |",
        "|---|---|",
        f"| Document cards indexed in the vault | {len(rows):,} |",
        f"| RAG-ready documents | {sum(1 for r in rows if r['usable_for_rag']):,} |",
        f"| Daily research notes | {len({date_key(r) for r in rows}):,} |",
        f"| Company pages | {sum(c for t, c in by_type if t == 'company_page'):,} |",
        f"| Source type pages | {sum(c for t, c in by_type if t == 'source_type_page'):,} |",
        f"| Document type pages | {sum(c for t, c in by_type if t == 'document_type_page'):,} |",
        f"| Strategy theme pages | {sum(c for t, c in by_type if t == 'strategy_theme_page'):,} |",
        f"| Templates | {sum(c for t, c in by_type if t == 'template'):,} |",
        "",
        "## Build run",
        "",
        f"- Runtime: {run_stats.get('runtime_seconds', 0):,.1f} seconds",
        f"- Notes written: {run_stats.get('written', 0):,}",
        f"- Notes already current (unchanged): {run_stats.get('unchanged', 0):,}",
        f"- Note failures: {run_stats.get('failed', 0):,}",
        f"- Company pages skipped as vendor/noise tokens: {run_stats.get('tickers_skipped', 0):,}",
        f"- Filters: limit={run_stats.get('limit') or 'none'}, "
        f"year={run_stats.get('year') or 'all'}, "
        f"date={run_stats.get('only_date') or 'all'}, "
        f"ticker={run_stats.get('only_ticker') or 'all'}, "
        f"force={run_stats.get('force', False)}",
        "",
        "## Safety",
        "",
        f"{SAFETY_NOTE}",
        "",
        "Only notes generated by this script are managed. Anything else in the vault - notes a",
        "human writes, folders they add - is never modified or deleted.",
        "",
        "**AI drafts. Humans verify. Cutler decides.**",
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    LOGGER.info("Wrote %s", path)


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def collect_totals(conn: sqlite3.Connection) -> dict:
    def scalar(sql: str, params: tuple = ()) -> int:
        try:
            return int(conn.execute(sql, params).fetchone()[0] or 0)
        except sqlite3.OperationalError:
            return 0

    return {
        "pdf_rows": scalar("SELECT COUNT(*) FROM documents "
                           "WHERE LOWER(COALESCE(file_extension,'')) = '.pdf'"),
        "cards_created": scalar("SELECT COUNT(*) FROM document_cards WHERE card_status = 'created'"),
        "rag_ready": scalar("SELECT COUNT(*) FROM document_cards WHERE usable_for_rag = 1"),
        "needs_review": scalar("SELECT COUNT(*) FROM document_cards "
                               "WHERE card_quality = 'Needs Review'"),
        "ocr_candidates": scalar("SELECT COUNT(*) FROM document_cards WHERE rag_exclusion_reason "
                                 "IN ('scanned_pdf_no_text','empty_text')"),
        "duplicates": scalar("SELECT COUNT(*) FROM document_cards "
                             "WHERE rag_exclusion_reason = 'duplicate'"),
        "high_review": scalar("SELECT COUNT(*) FROM document_cards "
                              "WHERE rag_exclusion_reason = 'high_review'"),
    }


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Cutler Research Brain Phase 4 - build a deterministic Obsidian vault.",
    )
    parser.add_argument("--db", required=True,
                        help=r'SQLite index, e.g. "data\document_metadata.sqlite"')
    parser.add_argument("--project-root", required=True,
                        help=r'Project root, e.g. "C:\Users\AI Admin\Documents\CutlerResearchBrain"')
    parser.add_argument("--limit", type=int, default=0,
                        help="Only index the first N document cards (0 = all).")
    parser.add_argument("--force", action="store_true",
                        help="Rewrite every generated note even if it is already current.")
    parser.add_argument("--year", default="", help="Only documents from this archive year.")
    parser.add_argument("--only-date", default="",
                        help="Only documents with this folder date, e.g. 2026-06-22.")
    parser.add_argument("--only-ticker", default="",
                        help="Only documents carrying this ticker candidate.")
    parser.add_argument("--min-ticker-docs", type=int, default=1,
                        help="Minimum documents before a company page is created (default 1).")
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
        raise SystemExit(f"ERROR: database not found: {db_path}\nRun Phases 1-3 first.")

    setup_logging(project_root / "logs" / "phase4_obsidian_memory.log", args.verbose)
    started = time.time()

    vault_root = project_root / VAULT_ROOT_NAME
    assert_inside_project(vault_root, project_root)

    LOGGER.info("=" * 78)
    LOGGER.info("Cutler Research Brain - Phase 4 Obsidian memory")
    LOGGER.info("Database    : %s", db_path)
    LOGGER.info("Project root: %s", project_root)
    LOGGER.info("Vault       : %s", vault_root)
    LOGGER.info("Deterministic metadata only - no summaries, no interpretation, no APIs.")
    LOGGER.info("=" * 78)

    conn = connect(db_path)
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "document_cards" not in tables:
            raise SystemExit("ERROR: no 'document_cards' table. Run Phase 3 first.")

        create_note_schema(conn)

        for folder in FOLDERS.values():
            path = vault_root / folder
            assert_inside_project(path, project_root)
            path.mkdir(parents=True, exist_ok=True)

        rows = load_rows(conn, args)
        LOGGER.info("Document cards selected for this build: %d", len(rows))
        if not rows:
            LOGGER.warning("No document cards matched the filters - nothing to build.")

        totals = collect_totals(conn)
        noise_tokens = load_noise_tokens(project_root)

        writer = VaultWriter(project_root, vault_root, conn, args.force)

        # Phase 4.2: work out which company pages will exist BEFORE anything is
        # rendered, so no note links to a page that never gets created.
        company_pages = eligible_company_pages(rows, noise_tokens, args.min_ticker_docs)
        LOGGER.info("Company pages that will exist: %d (ticker candidates without a page are "
                    "rendered as plain code, never as links)", len(company_pages))

        builders = [
            # Stubs first: every other note links to them.
            ("card stubs", lambda: build_card_stubs(writer, rows, project_root, company_pages)),
            ("dashboard", lambda: build_dashboard(writer, rows, totals, project_root)),
            ("daily notes", lambda: build_daily_notes(writer, rows, project_root, company_pages)),
            ("company pages", lambda: build_company_pages(writer, rows, project_root,
                                                          noise_tokens, args.min_ticker_docs)),
            ("source type pages", lambda: build_grouped_pages(
                writer, rows, project_root, "source_type_guess", "source_type",
                "source_type_page", "Source type", company_pages)),
            ("document type pages", lambda: build_grouped_pages(
                writer, rows, project_root, "document_type_guess", "document_type",
                "document_type_page", "Document type", company_pages)),
            ("strategy theme pages", lambda: build_grouped_pages(
                writer, rows, project_root, "strategy_theme_guess", "theme",
                "strategy_theme_page", "Strategy theme", company_pages, theme_warning=True)),
            ("RAG-ready index", lambda: build_rag_index(writer, rows, project_root, company_pages)),
            ("needs review", lambda: build_needs_review(writer, rows, totals, project_root)),
            ("OCR candidates", lambda: build_ocr_candidates(writer, rows, project_root)),
            ("document card index", lambda: build_card_index(writer, rows, project_root)),
            ("templates", lambda: build_templates(writer)),
        ]

        tickers_skipped = 0
        for label, builder in builders:
            try:
                result = builder()
                if label == "company pages" and isinstance(result, tuple):
                    tickers_skipped = result[1]
                LOGGER.info("Built %s.", label)
            except Exception as exc:  # noqa: BLE001 - one section must not stop the build
                LOGGER.error("Section FAILED (%s): %s", label, exc)
            conn.commit()

        conn.commit()

        runtime = time.time() - started
        run_stats = {
            "runtime_seconds": runtime,
            "written": writer.written,
            "unchanged": writer.unchanged,
            "failed": writer.failed,
            "tickers_skipped": tickers_skipped,
            "limit": args.limit,
            "year": args.year,
            "only_date": args.only_date,
            "only_ticker": args.only_ticker,
            "force": args.force,
        }
        write_reports(conn, project_root, rows, totals, run_stats)

        note_count = int(conn.execute("SELECT COUNT(*) FROM obsidian_notes").fetchone()[0] or 0)

        LOGGER.info("-" * 78)
        LOGGER.info("Obsidian build complete in %.1f seconds", runtime)
        LOGGER.info("  document cards indexed : %d", len(rows))
        LOGGER.info("  notes written          : %d", writer.written)
        LOGGER.info("  notes already current  : %d", writer.unchanged)
        LOGGER.info("  note failures          : %d", writer.failed)
        LOGGER.info("  notes tracked in total : %d", note_count)
        LOGGER.info("  vault                  : %s", vault_root)
        LOGGER.info("-" * 78)
        LOGGER.info("%s", SAFETY_NOTE)
    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
