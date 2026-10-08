#!/usr/bin/env python3
# =============================================================================
# Cutler Research Brain - Phase 7A / 7B
# app.py - a local, single-user demo UI for MSFT Ask Brain
#
# This is a WINDOW onto the pipeline, not a new pipeline. It does not retrieve,
# rank, embed, or answer anything itself. Every question is handed to
# scripts/ask_brain.py exactly as if it had been typed in PowerShell, and every
# answer, log and database row is produced by that script and nothing else.
#
# WHAT THIS APP DOES NOT DO
#   - no authentication (it is bound to 127.0.0.1 and meant for one person)
#   - no deployment, no production hardening, no external exposure
#   - no OpenAI call of its own; the only API calls are the ones ask_brain.py
#     already makes (one embeddings call, one responses call, per question)
#   - no writes anywhere except through ask_brain.py, which writes only inside
#     the project root
#   - no V Drive access of any kind
#   - no other ticker: MSFT only, enforced server-side
#
# HOW ASK BRAIN IS CALLED
#   As a subprocess, with an argument LIST and never a shell string, so nothing
#   a user types can become a command. The interpreter is this process's own
#   (sys.executable), so the UI runs Ask Brain with the same Python that is
#   running the UI.
#
#   AI drafts. Humans verify. Cutler decides.
#
# Run:
#   cd "C:\Users\AI Admin\Documents\CutlerResearchBrain\09_Web_App_Future\phase7a_local_ui"
#   py -3.11 -m uvicorn app:app --reload --host 127.0.0.1 --port 8000
# =============================================================================

from __future__ import annotations

import csv
import json
import os
import re
import sqlite3
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from starlette.requests import Request

PHASE = "phase14.3.7-1.0"

HERE = Path(__file__).resolve().parent
# phase7a_local_ui -> 09_Web_App_Future -> project root
PROJECT_ROOT = Path(os.environ.get("CUTLER_PROJECT_ROOT", HERE.parent.parent)).resolve()
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
ASK_BRAIN = SCRIPTS_DIR / "ask_brain.py"
DB_PATH = PROJECT_ROOT / "data" / "document_metadata.sqlite"

# --- Phase 10B: the one place an original file may be read from --------------
#
# The preview endpoint will open a file ONLY if its resolved path sits under
# this root. The root is configuration, never a request parameter, and the
# browser has no way to influence it. Everything outside it - including the
# project's own folders - is refused with 403 whatever the database says.
SOURCE_ARCHIVE_ROOT = Path(
    os.environ.get("CUTLER_SOURCE_ROOT", r"V:\Companies\A Current Month"))
PREVIEW_EXTENSIONS = (".pdf",)
PREVIEW_LOG = PROJECT_ROOT / "logs" / "phase10b_preview_access.log"

# Phase 11. The tickers this project knows the name of. Being listed here does
# NOT mean an index exists - `ticker_status()` reads the database and the disk
# for that, the dropdown shows it, and /api/ask refuses an unindexed ticker with
# the same sentence Ask Brain uses on the command line. There is deliberately no
# fallback: answering an AT&T question out of the MSFT index would be worse than
# not answering.
ALLOWED_TICKERS = ("MSFT", "KIM", "KR", "MRK", "T", "VZ", "AMZN", "SLB", "B", "ABBV")
NOT_INDEXED_MESSAGE = "This ticker has not been indexed yet."
RETRIEVAL_MODES = ("hybrid", "vector", "keyword")
SOURCE_PRIORITIES = ("balanced", "company", "analyst", "none")

# --- Phase 13: company names, so the UI can say "Microsoft" not "MSFT" -------
COMPANY_NAMES = {
    "MSFT": "Microsoft", "KIM": "Kimco Realty", "KR": "Kroger", "MRK": "Merck",
    "T": "AT&T", "VZ": "Verizon", "AMZN": "Amazon", "SLB": "SLB",
    "B": "Barnes Group", "ABBV": "AbbVie",
}

# --- Phase 13: Quick Answer / Deep Research ---------------------------------
#
# This is a UI-level routing over parameters ask_brain.py has accepted since
# Phase 6. There is no second retrieval system, no new ranking and no change to
# any weight: Deep Research widens the candidate pool and lets more of it into
# the evidence pack, which is exactly what --top-k and --max-chunks already do.
# Quick is the current default, unchanged, so an existing caller that sends no
# depth gets byte-identical behaviour.
# Phase 14. Depth now decides two independent things, and they are listed
# together here so the UI cannot promise one and the run do the other:
#   - how much evidence is retrieved (top_k / max_chunks / per document), and
#   - how the answer is WRITTEN UP (answer_style).
# The second is new. It changes no weight, no filter and no ranking rule: Quick
# and Deep read the same index the same way, and differ in how much of it is
# read and how the result is written.
DEPTH_PRESETS = {
    "quick": {"top_k": 30, "max_chunks": 8, "max_chunks_per_document": 2,
              "answer_style": "quick"},
    "deep": {"top_k": 60, "max_chunks": 16, "max_chunks_per_document": 3,
             "answer_style": "deep"},
}
ANSWER_STYLES = ("quick", "deep")
DEFAULT_ANSWER_STYLE = "quick"
DEFAULT_DEPTH = "quick"

# The source-type vocabulary, in the two words a PM needs rather than the nine
# the classifier uses. Kept identical to run_evaluation.py's sets on purpose.
COMPANY_SOURCE_TYPES = {"SEC Filing", "Company Press Release", "Company Material"}
ANALYST_SOURCE_TYPES = {
    "Sell-Side Research", "Industry Research", "Bank Research", "Credit Research",
    "Convertible Research", "REIT Research", "Green Street Research", "Market Monitor",
    "Short Sale Research",
}

# Phase 7B. The backend vocabulary does not change - these are only the words a
# non-technical reader sees. Value on the left, label on the right, and the
# template renders the pair so the two can never drift apart.
RETRIEVAL_MODE_LABELS = (
    ("hybrid", "Hybrid: vector + keyword"),
    ("vector", "Vector only"),
    ("keyword", "Keyword only"),
)
SOURCE_PRIORITY_LABELS = (
    ("balanced", "Balanced"),
    ("company", "Company / SEC first"),
    ("analyst", "Analyst research first"),
    ("none", "No source preference"),
)

# Phase 7B, Part F. What puts a run in front of a human again.
REVIEW_LIMIT = 20

MAX_QUESTION_CHARS = 1000
MAX_FILTER_CHARS = 200
TOP_K_RANGE = (1, 200)
MAX_CHUNKS_RANGE = (1, 50)
PER_DOCUMENT_RANGE = (1, 10)

ASK_TIMEOUT_SECONDS = float(os.environ.get("CUTLER_ASK_TIMEOUT_SECONDS", "600"))
STDOUT_SUMMARY_LINES = 40

RE_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
RE_RUN_ID = re.compile(r"^Run id\s*:\s*(\d+)", re.M)

# Only one Ask Brain at a time. Two concurrent runs would both rewrite the same
# report files and race on SQLite, so the second caller is told to wait rather
# than being allowed to corrupt the record.
ASK_LOCK = threading.Lock()

# Pull the pipeline version and the vocabulary straight from ask_brain.py so
# this file can never drift out of step with it.
try:
    sys.path.insert(0, str(SCRIPTS_DIR))
    import ask_brain as ask_brain_module  # noqa: E402
    ASK_BRAIN_VERSION = getattr(ask_brain_module, "PIPELINE_VERSION", "unknown")
except Exception:  # noqa: BLE001 - the UI must still start and say what is wrong
    ask_brain_module = None
    ASK_BRAIN_VERSION = "unavailable"

app = FastAPI(title="Cutler Research Brain - MSFT Ask Brain (Phase 7B)",
              version=PHASE, docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")
templates = Jinja2Templates(directory=str(HERE / "templates"))


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def connect() -> sqlite3.Connection:
    if not DB_PATH.exists():
        raise HTTPException(status_code=503,
                            detail=f"Database not found at {DB_PATH}. Phases 1-6 must have run.")
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                        (name,)).fetchone() is not None


def columns_of(conn: sqlite3.Connection, table: str) -> set:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def inside_project(path: Path) -> bool:
    """Nothing outside the project root is ever read or served."""
    try:
        path.resolve().relative_to(PROJECT_ROOT)
        return True
    except (ValueError, OSError):
        return False


def read_project_file(relative: str) -> Optional[str]:
    """Read a file recorded on a run row, refusing anything outside the root."""
    if not relative:
        return None
    candidate = (PROJECT_ROOT / relative).resolve()
    if not inside_project(candidate) or not candidate.is_file():
        return None
    try:
        return candidate.read_text(encoding="utf-8")
    except OSError:
        return None


def row_to_dict(row: sqlite3.Row) -> dict:
    return {key: row[key] for key in row.keys()}


# ---------------------------------------------------------------------------
# Phase 10A - the document library's path rules
#
# ONE RULE, AND IT IS NOT NEGOTIABLE: the browser never names a path.
#
# Every local file this phase serves is found by taking a validated document_id,
# looking it up in the database, and building the path from the project's own
# folder layout. There is no endpoint that accepts a path, a filename, or any
# fragment of one - so there is nothing for a traversal attempt to traverse.
#
# And the source archive is never opened. Phase 10A reports whether a source
# file exists, using stat() and nothing else. It does not read a byte of it, it
# does not copy it, and it does not serve it. The V drive stays exactly as
# read-only as it was before this UI existed.
# ---------------------------------------------------------------------------

EXTRACTED_TEXT_DIR = PROJECT_ROOT / "03_Extracted_Text"
DOCUMENT_CARDS_DIR = PROJECT_ROOT / "04_Document_Cards"

# doc_ + hex, which is what scan_source_archive.make_document_id produces. The
# synthetic fixture ids used in testing are also plain [a-z0-9_].
RE_DOCUMENT_ID = re.compile(r"^doc_[a-z0-9_]{4,64}$")

TEXT_PREVIEW_DEFAULT = 12_000
TEXT_PREVIEW_MAX = 50_000
SNIPPET_RADIUS = 240
MAX_SNIPPETS = 20
# Above this many pages, search the whole-document file instead of every page
# file. Per-page search is what lets a snippet say which page it came from;
# it is not worth opening a thousand files to say so.
PAGE_SEARCH_MAX_PAGES = 500
CHUNK_PREVIEW_CHARS = 600


def validate_document_id(document_id: str) -> str:
    """The only identifier this phase accepts from a browser.

    A strict pattern rather than an escape: `doc_` plus hex cannot contain a
    separator, a dot or a drive letter, so it cannot become a path fragment
    however it is concatenated downstream.
    """
    value = ("" if document_id is None else str(document_id)).strip()
    if not RE_DOCUMENT_ID.match(value):
        raise HTTPException(
            status_code=400,
            detail="Not a document id. Expected the form doc_<hex>, and nothing else is "
                   "accepted - this endpoint never takes a file path.")
    return value


def assert_inside_project(path: Path) -> Path:
    """Resolve, then prove the result is still inside the project root.

    Resolution happens FIRST so that any `..` or symlink has already been
    collapsed before the check - checking the unresolved string is the classic
    way to write a guard that does not guard.
    """
    try:
        resolved = path.resolve()
    except OSError as exc:
        raise HTTPException(status_code=404, detail="Path could not be resolved.") from exc
    try:
        resolved.relative_to(PROJECT_ROOT)
    except ValueError as exc:
        raise HTTPException(
            status_code=403,
            detail="Refusing to read outside the project root.") from exc
    return resolved


def get_document_by_id(document_id: str) -> dict:
    """One document row, or 404. Never a partial match, never a LIKE."""
    document_id = validate_document_id(document_id)
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM documents WHERE document_id = ?",
                           (document_id,)).fetchone()
    finally:
        conn.close()
    if row is None:
        raise HTTPException(status_code=404, detail=f"No document {document_id} in the index.")
    return row_to_dict(row)


def document_year(document: dict) -> str:
    """Which year folder this document's outputs live under."""
    for key in ("archive_year", "detected_year"):
        value = str(document.get(key) or "").strip()
        if value:
            return value
    return ""


def resolve_card_path(document_id: str, document: dict = None) -> Optional[Path]:
    """The document card for this id, under 04_Document_Cards and nowhere else.

    The stored `card_md_path` is preferred when it is there, but it is still
    resolved and checked against the project root like anything else - a value
    in the database is not a reason to trust a path.
    """
    document = document or get_document_by_id(document_id)
    conn = connect()
    try:
        row = conn.execute(
            "SELECT card_md_path, card_folder FROM document_cards WHERE document_id = ?",
            (document_id,)).fetchone() if table_exists(conn, "document_cards") else None
    finally:
        conn.close()

    candidates = []
    if row is not None:
        stored = str(row["card_md_path"] or "").strip()
        if stored:
            candidates.append(PROJECT_ROOT / stored)
        folder = str(row["card_folder"] or "").strip()
        if folder:
            candidates.append(PROJECT_ROOT / folder / "document_card.md")
    year = document_year(document)
    if year:
        candidates.append(DOCUMENT_CARDS_DIR / year / document_id / "document_card.md")
    candidates.append(DOCUMENT_CARDS_DIR / document_id / "document_card.md")

    for candidate in candidates:
        resolved = assert_inside_project(candidate)
        if DOCUMENT_CARDS_DIR.resolve() in resolved.parents and resolved.is_file():
            return resolved
    return None


def resolve_extracted_text_dir(document_id: str, document: dict = None) -> Optional[Path]:
    """The folder holding this document's extracted text."""
    document = document or get_document_by_id(document_id)
    conn = connect()
    try:
        row = conn.execute(
            "SELECT output_folder, document_text_path FROM pdf_extractions "
            "WHERE document_id = ?", (document_id,)).fetchone() \
            if table_exists(conn, "pdf_extractions") else None
    finally:
        conn.close()

    candidates = []
    if row is not None:
        folder = str(row["output_folder"] or "").strip()
        if folder:
            candidates.append(PROJECT_ROOT / folder)
        text_path = str(row["document_text_path"] or "").strip()
        if text_path:
            candidates.append((PROJECT_ROOT / text_path).parent)
    year = document_year(document)
    if year:
        candidates.append(EXTRACTED_TEXT_DIR / year / document_id)
    candidates.append(EXTRACTED_TEXT_DIR / document_id)

    for candidate in candidates:
        resolved = assert_inside_project(candidate)
        if EXTRACTED_TEXT_DIR.resolve() in resolved.parents and resolved.is_dir():
            return resolved
    return None


def resolve_extracted_text_path(document_id: str, document: dict = None) -> Optional[Path]:
    """The whole-document text file, if it was written."""
    folder = resolve_extracted_text_dir(document_id, document)
    if folder is None:
        return None
    candidate = folder / "document_text.txt"
    return candidate if candidate.is_file() else None


def resolve_page_path(document_id: str, page: int, document: dict = None) -> Optional[Path]:
    """One page file. The page NUMBER is an integer, so it cannot be a path."""
    folder = resolve_extracted_text_dir(document_id, document)
    if folder is None:
        return None
    candidate = folder / "pages" / f"page_{int(page):04d}.txt"
    resolved = assert_inside_project(candidate)
    return resolved if resolved.is_file() else None


def source_availability(document: dict) -> dict:
    """Is the original still where the index last saw it?

    stat() ONLY. Phase 10A never opens a source file, never copies it and never
    serves it. This answers one question - is it there - and nothing else.
    """
    source_path = str(document.get("file_path") or "")
    exists = False
    if source_path:
        try:
            exists = Path(source_path).exists()
        except OSError:
            exists = False
    marked_missing = str(document.get("file_change_status") or "") == "missing_file"
    return {
        "source_path": source_path,
        "relative_path": str(document.get("relative_path") or ""),
        "exists": bool(exists),
        "marked_missing": marked_missing,
        "warning": (
            "Original source file not found at last indexed path. Metadata and extracted text "
            "remain available. Confirm the V drive/share was fully mounted before treating this "
            "as deleted." if (marked_missing or not exists) and source_path else ""),
    }


# ---------------------------------------------------------------------------
# Phase 10B - the preview gate
#
# Every rule that decides whether an original file may be opened lives here, in
# one function, returning one verdict. The endpoint does not re-derive any of
# it: if this says no, nothing is opened. Keeping the decision in a single
# place is what makes it testable and what stops a later edit from adding a
# path that skips half the checks.
# ---------------------------------------------------------------------------

def normalised(path: Path) -> str:
    """A path in the form used for comparison. Case-folded on Windows."""
    return os.path.normcase(str(path))


def inside(path: Path, root: Path) -> bool:
    """Is `path` at or under `root`? Both must already be resolved."""
    path_text = normalised(path)
    root_text = normalised(root).rstrip("\\/")
    return path_text == root_text or path_text.startswith(root_text + os.sep) or \
        path_text.startswith(root_text + "/")


def safe_download_name(file_name: str) -> str:
    """A filename fit for a header: no quotes, no newlines, no path separators."""
    cleaned = re.sub(r"[^A-Za-z0-9._ ()-]", "_", (file_name or "document").strip())
    cleaned = cleaned.strip(". ") or "document"
    return cleaned[:120]


def preview_status(document: dict) -> dict:
    """May this document's original file be previewed, and if not, why not.

    The order of the checks is deliberate. Containment is tested before file
    type and before existence, because "that path is not in the archive" is a
    fact about what this server is allowed to do, and it should not be masked
    by a file simply being absent.
    """
    document_id = document.get("document_id", "")
    source_path = str(document.get("file_path") or "").strip()
    extension = Path(source_path).suffix.lower() if source_path else ""
    url = f"/api/document/{document_id}/preview"

    def verdict(enabled, reason, message, exists=False, status=None):
        return {"enabled": bool(enabled), "reason": reason, "message": message,
                "url": url if enabled else None, "extension": extension,
                "source_exists": bool(exists), "status": status,
                "archive_root": str(SOURCE_ARCHIVE_ROOT),
                "note": "The original is streamed from the archive for viewing only. "
                        "It is never copied into the project."}

    if not source_path:
        return verdict(False, "no_source_path_recorded",
                       "No source path was recorded for this document, so there is nothing "
                       "to preview.", status=404)

    try:
        resolved = Path(source_path).resolve()
    except OSError:
        return verdict(False, "source_path_outside_allowed_archive",
                       "The recorded source path could not be resolved.", status=403)

    if not inside(resolved, SOURCE_ARCHIVE_ROOT.resolve()) or inside(resolved, PROJECT_ROOT):
        return verdict(False, "source_path_outside_allowed_archive",
                       "The recorded source path is outside the archive this server is "
                       "allowed to read from. Nothing will be opened.", status=403)

    if extension not in PREVIEW_EXTENSIONS:
        return verdict(False, "preview_only_available_for_pdf",
                       "Original preview is only enabled for PDFs in Phase 10B. The extracted "
                       "text, card and chunks for this document are still available.",
                       exists=path_exists(resolved), status=415)

    if not path_exists(resolved):
        return verdict(False, "source_file_missing",
                       "The original file is not at its last indexed path. Confirm the V "
                       "drive/share was fully mounted before treating this as deleted.",
                       status=404)

    return verdict(True, "pdf_preview_available",
                   "Previewing the original indexed PDF. The source file stays on the V drive "
                   "and is not copied.", exists=True, status=200)


def path_exists(path: Path) -> bool:
    """exists(), with an unreachable share treated as absent rather than fatal."""
    try:
        return path.is_file()
    except OSError:
        return False


def log_preview_access(document_id: str, file_name: str, outcome: str, detail: str = "") -> None:
    """One line per attempt: when, which document, what happened.

    Never the file's contents, and never the full path - the point of the log is
    to show that previews are accounted for, not to become a second copy of the
    archive's structure.
    """
    line = "\t".join([datetime.now().isoformat(timespec="seconds"), document_id,
                      safe_download_name(file_name), outcome, detail])
    try:
        PREVIEW_LOG.parent.mkdir(parents=True, exist_ok=True)
        with PREVIEW_LOG.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        pass        # a log that cannot be written must never break a read


def read_text_file(path: Path, limit: int = None) -> str:
    """Read a project file that has already been resolved and checked."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise HTTPException(status_code=404, detail="File could not be read.") from exc
    return text if limit is None else text[:limit]


# ---------------------------------------------------------------------------
# Validation - every value is checked here before it reaches a command line
# ---------------------------------------------------------------------------

class AskRequest(BaseModel):
    question: str = ""
    ticker: str = "MSFT"
    retrieval_mode: str = "hybrid"
    source_priority: str = "balanced"
    # Phase 13: "quick" or "deep". Empty means "use whatever top_k/max_chunks
    # were sent", which is how every pre-Phase-13 caller behaves.
    depth: str = ""
    top_k: int = 30
    max_chunks: int = 8
    max_chunks_per_document: int = 2
    date_from: str = ""
    date_to: str = ""
    source_type: str = ""
    document_type: str = ""
    must_contain: str = ""
    save_to_obsidian: bool = True
    # Phase 13.1: follow-up context. The browser sends a flag and a run id and
    # nothing else - the previous question, its answer and its evidence are all
    # looked up server-side from that id, so a page cannot assert that an answer
    # cited a document it never cited. Both default off, which is how every
    # caller written before this phase behaves.
    follow_up: bool = False
    previous_run_id: int = 0


def clean_text(value, limit: int) -> str:
    text = ("" if value is None else str(value)).strip()
    if len(text) > limit:
        raise HTTPException(status_code=400, detail=f"Value is longer than {limit} characters.")
    # Control characters have no business on a command line or in a question.
    if any(ord(character) < 32 and character not in "\t\n" for character in text):
        raise HTTPException(status_code=400, detail="Value contains control characters.")
    return text


def clean_int(value, name: str, bounds: tuple) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail=f"{name} must be a whole number.") from None
    low, high = bounds
    if not low <= number <= high:
        raise HTTPException(status_code=400, detail=f"{name} must be between {low} and {high}.")
    return number


def clean_date(value, name: str) -> str:
    text = clean_text(value, 10)
    if not text:
        return ""
    if not RE_DATE.match(text):
        raise HTTPException(status_code=400, detail=f"{name} must be YYYY-MM-DD.")
    try:
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail=f"{name} is not a real date.") from None
    return text


def validate(payload: AskRequest) -> dict:
    question = clean_text(payload.question, MAX_QUESTION_CHARS).replace("\n", " ").strip()
    if not question:
        raise HTTPException(status_code=400, detail="Please type a question.")

    ticker = clean_text(payload.ticker, 15).upper()
    if ticker not in ALLOWED_TICKERS:
        raise HTTPException(
            status_code=400,
            detail=f"{ticker or 'That ticker'} is not one of the tickers this project knows: "
                   f"{', '.join(ALLOWED_TICKERS)}.")
    status = ticker_status(ticker)
    if not status["indexed"]:
        # Refuse before spending anything. Ask Brain would refuse too, but it
        # would embed the question first.
        raise HTTPException(
            status_code=400,
            detail=f"{NOT_INDEXED_MESSAGE} ({ticker}: {status['reason']}) "
                   f"Build it with scripts\\build_ticker_batch.py, then ask again.")

    mode = clean_text(payload.retrieval_mode, 20).lower() or "hybrid"
    if mode not in RETRIEVAL_MODES:
        raise HTTPException(status_code=400,
                            detail=f"retrieval_mode must be one of {', '.join(RETRIEVAL_MODES)}.")

    priority = clean_text(payload.source_priority, 20).lower() or "balanced"
    if priority not in SOURCE_PRIORITIES:
        raise HTTPException(
            status_code=400,
            detail=f"source_priority must be one of {', '.join(SOURCE_PRIORITIES)}.")

    date_from = clean_date(payload.date_from, "date_from")
    date_to = clean_date(payload.date_to, "date_to")
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=400, detail="date_from is after date_to.")

    # Phase 13. A named depth overrides the three numbers; no depth leaves them
    # exactly as sent, so nothing that called this endpoint before behaves
    # differently. The mapping lives in DEPTH_PRESETS, in one place, so the UI
    # cannot claim one thing and the run do another.
    depth = clean_text(payload.depth, 20).lower()
    if depth and depth not in DEPTH_PRESETS:
        raise HTTPException(status_code=400,
                            detail=f"depth must be one of {', '.join(DEPTH_PRESETS)}.")
    preset = DEPTH_PRESETS.get(depth, {})

    return {
        "question": question,
        "ticker": ticker,
        "retrieval_mode": mode,
        "source_priority": priority,
        "depth": depth,
        "top_k": clean_int(preset.get("top_k", payload.top_k), "top_k", TOP_K_RANGE),
        "max_chunks": clean_int(preset.get("max_chunks", payload.max_chunks),
                                "max_chunks", MAX_CHUNKS_RANGE),
        "max_chunks_per_document": clean_int(
            preset.get("max_chunks_per_document", payload.max_chunks_per_document),
            "max_chunks_per_document", PER_DOCUMENT_RANGE),
        # Phase 14. A request that named no depth keeps writing the way it
        # always did - the quick shape - so no caller written before this phase
        # changes behaviour.
        "answer_style": preset.get("answer_style", DEFAULT_ANSWER_STYLE),
        "date_from": date_from,
        "date_to": date_to,
        "source_type": clean_text(payload.source_type, MAX_FILTER_CHARS),
        "document_type": clean_text(payload.document_type, MAX_FILTER_CHARS),
        "must_contain": clean_text(payload.must_contain, MAX_FILTER_CHARS),
        "save_to_obsidian": bool(payload.save_to_obsidian),
        "follow_up": bool(payload.follow_up),
        "previous_run_id": max(0, int(payload.previous_run_id or 0)),
        # Filled in by ask() when this is a follow-up. Empty here so that
        # build_command() has one shape to read whatever the request was.
        "restrict_to_documents": [],
    }


def build_command(values: dict) -> list:
    """The exact argument list handed to ask_brain.py.

    A list, never a shell string: a question containing quotes, semicolons or
    ampersands is one argument, not a command. The subprocess call below runs
    with shell=False, so nothing a user types is ever interpreted.
    """
    command = [
        sys.executable, str(ASK_BRAIN),
        "--project-root", str(PROJECT_ROOT),
        "--db", str(DB_PATH),
        "--ticker", values["ticker"],
        "--question", values["question"],
        "--retrieval-mode", values["retrieval_mode"],
        "--source-priority", values["source_priority"],
        "--top-k", str(values["top_k"]),
        "--max-chunks", str(values["max_chunks"]),
        "--max-chunks-per-document", str(values["max_chunks_per_document"]),
        # Phase 14: how the answer is written up, not what is retrieved for it.
        "--answer-style", values.get("answer_style") or DEFAULT_ANSWER_STYLE,
    ]
    if values["date_from"]:
        command += ["--date-from", values["date_from"]]
    if values["date_to"]:
        command += ["--date-to", values["date_to"]]
    if values["source_type"]:
        command += ["--source-type", values["source_type"]]
    if values["document_type"]:
        command += ["--document-type", values["document_type"]]
    if values["must_contain"]:
        command += ["--must-contain", values["must_contain"]]
    # Phase 13.1. A referential follow-up looks in the documents the previous
    # answer came from. This is a filter of the same kind as the four above it -
    # it narrows the candidate pool before ranking - and it is only ever set when
    # the server itself decided this question follows on from a recorded run.
    for document_id in values.get("restrict_to_documents") or []:
        command += ["--restrict-to-documents", document_id]
    command.append("--save" if values["save_to_obsidian"] else "--no-save")
    return command


def child_environment() -> dict:
    """The environment Ask Brain runs in.

    Windows consoles default to a legacy code page - usually cp1252 - which
    cannot encode characters the answer model uses freely, such as the
    non-breaking hyphen in "non-GAAP". Asking the child for UTF-8 removes the
    problem at the source, and reading its output as UTF-8 with replacement
    means even a stray byte cannot break the reply to the browser.

    Everything else in the environment is inherited unchanged, so the child
    still finds its PATH, its .env and the same interpreter's site-packages.
    """
    environment = os.environ.copy()
    environment["PYTHONUTF8"] = "1"          # Python 3.7+ UTF-8 mode
    environment["PYTHONIOENCODING"] = "utf-8"
    return environment


# ---------------------------------------------------------------------------
# Phase 7B: reading what Ask Brain recorded
# ---------------------------------------------------------------------------

# Plain-English explanations of the audit flags. The wording matters: most of
# these are reading aids, not defects, and a demo that colours everything red
# teaches people to ignore the colour.
FLAG_GUIDE = (
    ("never cited",
     "unused evidence",
     "warn",
     "The model did not use every chunk it was given. Usually fine - the pack is "
     "deliberately wider than any one answer needs."),
    ("carry no citation",
     "uncited sentence",
     "warn",
     "A sentence in the Direct Answer states something without pointing at a chunk. "
     "Check that sentence against the sources yourself."),
    ("bullet",
     "uncited bullet",
     "warn",
     "A bullet in the Direct Answer has no citation. Check it against the sources."),
    ("directness_cap_applied",
     "directness cap",
     "warn",
     "The answer said its own evidence was not directly responsive, so confidence was "
     "lowered to Low. The answer is honest about its limits - read it as a near miss."),
    ("contradicts",
     "confidence contradiction",
     "warn",
     "The answer said the evidence was not direct but still claimed higher confidence. "
     "Trust the final confidence, not the model's own grade."),
    ("Source Citations but used nowhere",
     "listed but unused",
     "warn",
     "A citation appears only in the Source Citations list and is never used in the "
     "answer itself. Cosmetic, but it inflates the apparent evidence."),
    ("never supplied",
     "citation points at nothing",
     "stop",
     "The answer cited a chunk number that was never given to it. Do not rely on that "
     "claim until a person has checked it."),
    ("missing section",
     "missing section",
     "warn",
     "The answer left out one of the six required sections."),
)


def classify_audit(row: dict) -> dict:
    """Traffic-light the audit flags on a run, with an explanation for each.

    Green means every automated check passed. Amber means there is something to
    read carefully - which is the normal state of a good answer, not a problem.
    Red is reserved for the two things that would make an answer unsafe to use:
    recommendation or rating language, and a citation pointing at evidence that
    was never supplied.
    """
    flags = [f.strip() for f in str(row.get("audit_flags") or "").split(";") if f.strip()]
    language = [f.strip() for f in str(row.get("flagged_language") or "").split(";") if f.strip()]

    items = []
    level = "ok"
    for flag in flags:
        matched = None
        for needle, label, severity, explanation in FLAG_GUIDE:
            if needle.lower() in flag.lower():
                matched = {"flag": flag, "label": label, "severity": severity,
                           "explanation": explanation}
                break
        if matched is None:
            matched = {"flag": flag, "label": "other check", "severity": "warn",
                       "explanation": "An automated check flagged this. Read the answer with it "
                                      "in mind."}
        items.append(matched)
        if matched["severity"] == "stop":
            level = "stop"
        elif level != "stop":
            level = "warn"

    for flag in language:
        items.append({
            "flag": flag, "label": "recommendation or rating wording", "severity": "stop",
            "explanation": "The answer used wording this project does not allow - a buy/sell "
                           "call, a rating, or a prediction. Do not use it until a person has "
                           "reviewed it.",
        })
        level = "stop"

    if str(row.get("directness_cap_applied") or "0") == "1" \
            and not any("directness" in i["flag"].lower() for i in items):
        items.append({
            "flag": "directness cap applied", "label": "directness cap", "severity": "warn",
            "explanation": "The answer said its own evidence was not directly responsive, so "
                           "confidence was lowered to Low.",
        })
        level = "stop" if level == "stop" else "warn"

    headline = {
        "ok": "Every automated check passed.",
        "warn": "Nothing unsafe, but there are things to read carefully.",
        "stop": "Do not use this answer until a person has reviewed it.",
    }[level]
    return {"level": level, "headline": headline, "items": items,
            "flag_count": len(items)}


def review_reasons(row: dict) -> list:
    """Why a run is in the review queue. One short phrase per reason."""
    reasons = []
    if str(row.get("final_confidence") or "") == "Low":
        reasons.append("Low confidence")
    if str(row.get("directness_cap_applied") or "0") == "1":
        reasons.append("evidence not directly responsive")
    if str(row.get("flagged_language") or "").strip():
        reasons.append("recommendation or rating wording")
    if str(row.get("audit_flags") or "").strip():
        reasons.append("audit flags")
    status = str(row.get("status") or "")
    if status and status != "completed":
        reasons.append(f"status: {status}")
    return reasons


def read_diagnostics(relative: str, limit: int = 20) -> Optional[dict]:
    """Parse the per-run diagnostics CSV Ask Brain wrote.

    Read-only, and refuses anything outside the project root like every other
    file this app touches.
    """
    if not relative:
        return None
    path = (PROJECT_ROOT / relative).resolve()
    if not inside_project(path) or not path.is_file():
        return None

    try:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
    except (OSError, csv.Error):
        return None
    if not rows:
        return None

    def number(value):
        try:
            return round(float(value), 6)
        except (TypeError, ValueError):
            return None

    def integer(value):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None

    parsed = []
    for row in rows:
        parsed.append({
            "rank": integer(row.get("rank")),
            "selected": (row.get("selected") or "").lower() == "yes",
            "citation": integer(row.get("citation")),
            "kind": row.get("kind") or "",
            "file_name": row.get("file_name") or "",
            "chunk_id": row.get("chunk_id") or "",
            "document_id": row.get("document_id") or "",
            "source_type_guess": row.get("source_type_guess") or "",
            "document_type_guess": row.get("document_type_guess") or "",
            "folder_date_guess": row.get("folder_date_guess") or "",
            "vector_score": number(row.get("vector_score_norm")),
            "vector_score_raw": number(row.get("vector_score")),
            "keyword_score": number(row.get("keyword_score_norm")),
            "keyword_score_raw": number(row.get("keyword_score")),
            "hybrid_score": number(row.get("hybrid_score")),
            "selection_reason": row.get("selection_reason") or "",
        })
    parsed.sort(key=lambda r: (r["rank"] is None, r["rank"]))

    def mean(key):
        values = [r[key] for r in parsed if isinstance(r[key], (int, float))]
        return round(sum(values) / len(values), 4) if values else None

    selected = [r for r in parsed if r["selected"]]
    return {
        "path": relative,
        "candidates_considered": len(parsed),
        "selected_chunks": len(selected),
        "selected_documents": len({r["document_id"] for r in selected if r["document_id"]}),
        "avg_vector_score": mean("vector_score"),
        "avg_keyword_score": mean("keyword_score"),
        "avg_hybrid_score": mean("hybrid_score"),
        "shown": min(len(parsed), max(1, limit)),
        "rows": parsed[:max(1, limit)],
    }


def summarise_stdout(stdout: str, stderr: str) -> str:
    """The tail of the console output, for when something goes wrong."""
    lines = [line.rstrip() for line in (stdout or "").splitlines() if line.strip()]
    tail = lines[-STDOUT_SUMMARY_LINES:]
    if stderr and stderr.strip():
        tail += ["--- stderr ---"] + [
            line.rstrip() for line in stderr.splitlines() if line.strip()
        ][-STDOUT_SUMMARY_LINES:]
    return "\n".join(tail)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/")
def index(request: Request):
    context = {
        "request": request,
        "phase": PHASE,
        "ask_brain_version": ASK_BRAIN_VERSION,
        "project_root": str(PROJECT_ROOT),
        "tickers": ALLOWED_TICKERS,
        "retrieval_modes": RETRIEVAL_MODE_LABELS,
        "source_priorities": SOURCE_PRIORITY_LABELS,
    }
    try:
        # Starlette 0.29+ signature.
        return templates.TemplateResponse(request=request, name="index.html", context=context)
    except TypeError:
        # Older Starlette wants (name, context) with the request inside it.
        return templates.TemplateResponse("index.html", context)


@app.get("/api/health")
def health():
    keyword_index = PROJECT_ROOT / "06_RAG_Index" / "keyword" / "MSFT_keyword_index.json"
    chroma_store = PROJECT_ROOT / "06_RAG_Index" / "chroma" / "MSFT"

    payload = {
        "status": "ok",
        "phase": PHASE,
        "project_root": str(PROJECT_ROOT),
        "db_exists": DB_PATH.exists(),
        "chroma_exists": chroma_store.exists(),
        "keyword_index_exists": keyword_index.exists(),
        "ask_brain_exists": ASK_BRAIN.exists(),
        "msft_chunks": 0,
        "msft_documents": 0,
        "latest_ask_run": None,
        "pipeline_version": ASK_BRAIN_VERSION,
        "ui_version": PHASE,
        "total_runs": 0,
        "flagged_runs": 0,
        "source_types": [],
        "document_types": [],
        # --- Phase 9: ingestion status, read-only ---------------------------
        "last_ingestion_run": None,
        "last_ingestion_status": "",
        "last_ingestion_completed_at": "",
        "new_files_last_run": 0,
        "changed_files_last_run": 0,
        "missing_files_last_run": 0,
        "stale_rag_count": 0,
        "pending_chunks": 0,
        "smoke_eval_status": "",
        "warnings": [],
    }

    if not payload["db_exists"]:
        payload["status"] = "degraded"
        payload["warnings"].append(f"No database at {DB_PATH}.")
        return payload
    if not payload["ask_brain_exists"]:
        payload["status"] = "degraded"
        payload["warnings"].append(f"scripts/ask_brain.py not found at {ASK_BRAIN}.")

    conn = connect()
    try:
        if table_exists(conn, "rag_chunks"):
            row = conn.execute(
                "SELECT COUNT(*) AS chunks, COUNT(DISTINCT document_id) AS docs "
                "FROM rag_chunks WHERE ticker = 'MSFT' AND embedding_status = 'embedded'"
            ).fetchone()
            payload["msft_chunks"] = row["chunks"] or 0
            payload["msft_documents"] = row["docs"] or 0
            payload["source_types"] = [
                r[0] for r in conn.execute(
                    "SELECT DISTINCT source_type_guess FROM rag_chunks WHERE ticker='MSFT' "
                    "AND COALESCE(source_type_guess,'') <> '' ORDER BY 1")]
            payload["document_types"] = [
                r[0] for r in conn.execute(
                    "SELECT DISTINCT document_type_guess FROM rag_chunks WHERE ticker='MSFT' "
                    "AND COALESCE(document_type_guess,'') <> '' ORDER BY 1")]
        else:
            payload["status"] = "degraded"
            payload["warnings"].append("No rag_chunks table - Phase 5A has not run.")

        if table_exists(conn, "ask_brain_runs"):
            have = columns_of(conn, "ask_brain_runs")
            wanted = [c for c in ("run_id", "created_at", "question", "status",
                                  "final_confidence", "retrieval_mode_effective") if c in have]
            latest = conn.execute(
                f"SELECT {','.join(wanted)} FROM ask_brain_runs WHERE ticker = 'MSFT' "
                "ORDER BY run_id DESC LIMIT 1").fetchone()
            if latest:
                payload["latest_ask_run"] = row_to_dict(latest)
            payload["total_runs"] = conn.execute(
                "SELECT COUNT(*) FROM ask_brain_runs WHERE ticker = 'MSFT'").fetchone()[0]
            if "final_confidence" in have:
                payload["flagged_runs"] = conn.execute(
                    "SELECT COUNT(*) FROM ask_brain_runs WHERE ticker = 'MSFT' AND ("
                    "COALESCE(final_confidence,'') = 'Low' "
                    "OR COALESCE(audit_flags,'') <> '' "
                    "OR COALESCE(flagged_language,'') <> '' "
                    "OR COALESCE(directness_cap_applied,'0') = '1' "
                    "OR COALESCE(status,'') <> 'completed')").fetchone()[0]
        # --- Phase 9: what the last ingestion found, read-only --------------
        # The UI reports the state of ingestion. It cannot start one: an
        # ingestion walks a network share and may spend money on embeddings,
        # and both of those should be a decision someone makes at a terminal.
        if table_exists(conn, "ingestion_runs"):
            row = conn.execute(
                "SELECT * FROM ingestion_runs WHERE status IN ('completed','dry_run') "
                "ORDER BY ingestion_run_id DESC LIMIT 1").fetchone()
            if row is not None:
                run = row_to_dict(row)
                payload["last_ingestion_run"] = run.get("ingestion_run_id")
                payload["last_ingestion_status"] = run.get("status") or ""
                payload["last_ingestion_completed_at"] = run.get("completed_at") or ""
                payload["new_files_last_run"] = int(run.get("new_files") or 0)
                payload["changed_files_last_run"] = int(run.get("changed_files") or 0)
                payload["missing_files_last_run"] = int(run.get("missing_files") or 0)
                payload["smoke_eval_status"] = run.get("smoke_eval_status") or ""

        if "stale_rag_index" in columns_of(conn, "documents"):
            payload["stale_rag_count"] = conn.execute(
                "SELECT COUNT(*) FROM documents "
                "WHERE COALESCE(stale_rag_index,'0') = '1'").fetchone()[0]
        if table_exists(conn, "rag_chunks"):
            payload["pending_chunks"] = conn.execute(
                "SELECT COUNT(*) FROM rag_chunks "
                "WHERE embedding_status = 'pending'").fetchone()[0]
    finally:
        conn.close()

    if payload["stale_rag_count"] or payload["pending_chunks"]:
        payload["warnings"].append(
            f"The MSFT index is behind the archive: {payload['stale_rag_count']} document(s) "
            f"marked stale, {payload['pending_chunks']} chunk(s) chunked but not embedded. "
            "Answers will not see the newest material until an ingestion run embeds them.")
    if payload["missing_files_last_run"]:
        payload["warnings"].append(
            f"The last ingestion could not find {payload['missing_files_last_run']} indexed "
            "file(s) on the archive. Nothing was deleted - check the share was fully available.")

    if not payload["chroma_exists"]:
        payload["status"] = "degraded"
        payload["warnings"].append("No Chroma store for MSFT - run Phase 5A.")
    if not payload["keyword_index_exists"]:
        payload["warnings"].append(
            "No keyword index - hybrid retrieval will fall back to vector-only. "
            "Build it with scripts/build_keyword_index.py.")
    if not payload["msft_chunks"]:
        payload["status"] = "degraded"
        payload["warnings"].append("No embedded MSFT chunks. There is nothing to search.")
    if not payload["total_runs"]:
        payload["warnings"].append(
            "No questions have been asked yet. Try one of the presets under the question box.")
    return payload


@app.post("/api/ask")
def ask(payload: AskRequest):
    if not ASK_BRAIN.exists():
        raise HTTPException(status_code=503, detail=f"ask_brain.py not found at {ASK_BRAIN}.")
    values = validate(payload)

    # ---- Phase 13.1: follow-up context ----------------------------------
    #
    # Worked out before anything is spent, and reported back on every reply so
    # the reader is told which of the three things happened.
    plan = None
    if values["follow_up"] and values["previous_run_id"]:
        plan = follow_up_plan(values["question"], values["ticker"], values["previous_run_id"])

        if plan["kind"] == "evidence_reference":
            # "Which documents support this?" is answered by reading the previous
            # run's own record back. No search, no model call, nothing spent, and
            # no new run row - because nothing was asked of the pipeline.
            previous = plan["previous"]
            return {
                "status": "answered_from_previous_evidence",
                "process_warning": "",
                "return_code": 0,
                "answer_markdown": evidence_answer_markdown(plan),
                # Deliberately null: this is not a run. The panel keeps showing
                # the previous run's sources, which is what the reply is about.
                "run_id": None,
                "derived_from_run_id": previous["run_id"],
                "question": values["question"],
                "answer_path": "", "obsidian_path": "", "retrieval_log_path": "",
                "diagnostics_path": "",
                # Deliberately blank. Nothing was generated, so there is no
                # confidence in anything - carrying the previous run's grade
                # across would attach a judgement to a list of file names.
                "final_confidence": "",
                "confidence_stated": "", "confidence_rule": "",
                "confidence_rule_reason": "",
                "directness_cap_applied": False,
                "citations_used": ",".join(str(n) for n in previous.get("citations") or []),
                "audit_flags": "", "flagged_language": "",
                "retrieval_mode_effective": "",
                "trust": {
                    "confidence": "not applicable",
                    "level": "ok",
                    "headline": "Read back from the record",
                    "mix": "",
                    "points": [
                        f"The {len(previous.get('evidence') or [])} source(s) behind the previous "
                        "answer, listed from its retrieval log",
                        "No search was run and no model was asked anything",
                        "Nothing was spent, and no new run was recorded",
                    ],
                    "caution": "",
                    "sources_reviewed": len(previous.get("evidence") or []),
                    "documents_used": len(previous.get("document_ids") or []),
                    "company_sources": 0, "analyst_sources": 0, "other_sources": 0,
                    "oldest_source": "", "newest_source": "",
                    "technical": {"source": f"run {previous['run_id']} retrieval log"},
                },
                "filter_summary": "", "selected_chunks": len(previous.get("evidence") or []),
                "selected_documents": len(previous.get("document_ids") or []),
                "estimated_cost_usd": 0,
                "error_message": "",
                "audit": {"level": "ok",
                          "headline": "Nothing was generated, so there is nothing to check. "
                                      "This is the previous run's own record, read back.",
                          "items": [], "flag_count": 0},
                "evidence": previous.get("evidence") or [],
                "depth": values.get("depth") or "",
                "top_k": values["top_k"], "max_chunks": values["max_chunks"],
                "stdout_summary": "",
                "follow_up": {
                    "kind": plan["kind"], "explanation": plan["explanation"],
                    "reuses_evidence": True, "ran_retrieval": False,
                    "previous_run_id": previous["run_id"],
                    "previous_question": previous.get("question") or "",
                    "restrict_to_documents": [],
                    "question_sent": "",
                },
            }

        if plan["kind"] == "contextual":
            values["restrict_to_documents"] = plan["restrict_to_documents"]
            values["question"] = compose_follow_up_question(
                values["question"], plan["previous"].get("question") or "")
            if len(values["question"]) > MAX_QUESTION_CHARS:
                values["question"] = values["question"][:MAX_QUESTION_CHARS]

    if not ASK_LOCK.acquire(blocking=False):
        raise HTTPException(
            status_code=409,
            detail="Another question is still running. Ask Brain runs one at a time so two "
                   "answers cannot overwrite each other's reports. Try again in a moment.")
    try:
        command = build_command(values)
        try:
            completed = subprocess.run(          # noqa: S603 - list form, shell=False
                command,
                cwd=str(PROJECT_ROOT),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=child_environment(),
                timeout=ASK_TIMEOUT_SECONDS,
                shell=False,
            )
        except subprocess.TimeoutExpired:
            return JSONResponse(status_code=504, content={
                "status": "timeout",
                "error_message": (f"Ask Brain did not finish within {ASK_TIMEOUT_SECONDS:.0f} "
                                  "seconds and was stopped. Nothing was saved for this attempt."),
                "answer_markdown": "", "run_id": None, "answer_path": "",
                "obsidian_path": "", "retrieval_log_path": "", "final_confidence": "",
                "citations_used": "", "audit_flags": "", "stdout_summary": "",
            })
        except OSError as exc:
            raise HTTPException(status_code=500,
                                detail=f"Could not start Ask Brain: {exc}") from exc

        stdout_summary = summarise_stdout(completed.stdout, completed.stderr)

        # ask_brain.py is the source of truth for what happened, so read the row
        # it wrote rather than trying to parse the answer out of the console.
        run_id = None
        match = RE_RUN_ID.search(completed.stdout or "")
        if match:
            run_id = int(match.group(1))

        record = None
        conn = connect()
        try:
            if table_exists(conn, "ask_brain_runs"):
                if run_id is not None:
                    record = conn.execute("SELECT * FROM ask_brain_runs WHERE run_id = ?",
                                          (run_id,)).fetchone()
                if record is None:
                    record = conn.execute(
                        "SELECT * FROM ask_brain_runs WHERE ticker = ? AND question = ? "
                        "ORDER BY run_id DESC LIMIT 1",
                        (values["ticker"], values["question"])).fetchone()
        finally:
            conn.close()

        if record is None:
            return JSONResponse(status_code=500, content={
                "status": "failed",
                "error_message": ("Ask Brain exited with code "
                                  f"{completed.returncode} and recorded no run. "
                                  "See the console output below and "
                                  "logs\\phase5b_ask_brain.log."),
                "answer_markdown": "", "run_id": None, "answer_path": "",
                "obsidian_path": "", "retrieval_log_path": "", "final_confidence": "",
                "citations_used": "", "audit_flags": "", "stdout_summary": stdout_summary,
            })

        row = row_to_dict(record)
        answer_markdown = read_project_file(row.get("answer_path") or "") or ""

        # The database row is the truth about the run. If Ask Brain recorded a
        # completed run, that is what happened, even if the process then exited
        # non-zero on the way out - a console that could not display the answer
        # is a display problem, and the answer is on disk either way.
        recorded_status = row.get("status") or ""
        process_warning = ""
        if recorded_status == "completed" and completed.returncode != 0:
            process_warning = (
                f"Ask Brain recorded a completed run but its process exited with code "
                f"{completed.returncode}, usually because the console could not display part of "
                "the output. The answer, the retrieval log and the database row were all "
                "written. Nothing on disk is affected.")

        return {
            "status": recorded_status or ("completed" if completed.returncode == 0
                                          else "failed"),
            "process_warning": process_warning,
            "return_code": completed.returncode,
            "answer_markdown": answer_markdown,
            "run_id": row.get("run_id"),
            "answer_path": row.get("answer_path") or "",
            "obsidian_path": row.get("obsidian_answer_path") or "",
            "retrieval_log_path": row.get("retrieval_log_path") or "",
            "diagnostics_path": row.get("hybrid_diagnostics_path") or "",
            "final_confidence": row.get("final_confidence") or "",
            "confidence_stated": row.get("confidence_stated") or "",
            "confidence_rule": row.get("confidence_rule") or "",
            "confidence_rule_reason": row.get("confidence_rule_reason") or "",
            "directness_cap_applied": str(row.get("directness_cap_applied") or "0") == "1",
            "citations_used": row.get("citations_used") or "",
            "audit_flags": row.get("audit_flags") or "",
            "flagged_language": row.get("flagged_language") or "",
            "retrieval_mode_effective": row.get("retrieval_mode_effective") or "",
            "filter_summary": row.get("filter_summary") or "",
            "selected_chunks": row.get("selected_chunks") or 0,
            "selected_documents": row.get("selected_documents") or 0,
            "estimated_cost_usd": row.get("estimated_cost_usd") or 0,
            "error_message": row.get("error_message") or "",
            "audit": classify_audit(row),
            "depth": values.get("depth") or "",
            "answer_style": values.get("answer_style") or DEFAULT_ANSWER_STYLE,
            "top_k": values["top_k"],
            "max_chunks": values["max_chunks"],
            # Phase 13.1. The question the pipeline was actually asked, so the
            # answer heading is the question and not whatever is in the box now.
            "question": row.get("question") or values["question"],
            "evidence": run_evidence(row),
            # What the follow-up machinery decided, reported rather than hidden.
            # A reader who sees "searched inside the 4 documents the previous
            # answer came from" can disagree with it and ask again plainly.
            "follow_up": ({
                "kind": plan["kind"],
                "explanation": plan["explanation"],
                "reuses_evidence": plan["reuses_evidence"],
                "ran_retrieval": True,
                "previous_run_id": (plan.get("previous") or {}).get("run_id") or 0,
                "previous_question": (plan.get("previous") or {}).get("question") or "",
                "restrict_to_documents": plan["restrict_to_documents"],
                "names_other_company": plan.get("names_other_company") or "",
                "question_sent": values["question"],
            } if plan else None),
            "stdout_summary": stdout_summary,
        }
    finally:
        ASK_LOCK.release()


@app.get("/api/runs")
def runs(limit: int = 25, ticker: str = "MSFT"):
    limit = max(1, min(int(limit), 200))
    ticker = clean_text(ticker, 15).upper() or "MSFT"

    conn = connect()
    try:
        if not table_exists(conn, "ask_brain_runs"):
            return {"runs": [], "note": "No Ask Brain runs recorded yet."}
        have = columns_of(conn, "ask_brain_runs")
        wanted = [c for c in ("run_id", "created_at", "ticker", "question", "retrieval_mode",
                              "retrieval_mode_effective", "source_priority", "final_confidence",
                              "confidence_stated", "confidence_rule", "status", "answer_path",
                              "obsidian_answer_path", "audit_flags", "directness_cap_applied")
                  if c in have]
        rows = conn.execute(
            f"SELECT {','.join(wanted)} FROM ask_brain_runs WHERE ticker = ? "
            "ORDER BY run_id DESC LIMIT ?", (ticker, limit)).fetchall()
        return {"runs": [row_to_dict(r) for r in rows]}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Phase 13.1: the evidence record
#
# A citation in an answer is only worth something if it leads somewhere. Before
# this, the sources panel had the file name and the retrieval scores but not the
# passage, the publisher or a page to open - so verifying a claim meant leaving
# the answer, finding the document by name in the library, and reading it.
#
# One record per citation, with everything needed to check it: what the document
# is, who published it, when, which pages, and the exact text the answer was
# written from. The passage is read from rag_chunks by (chunk_id, ticker) - the
# Phase 11.3 key - and never by chunk_id alone, because chunk_id alone is
# ambiguous across tickers and a fallback is the bug with a default.
# ---------------------------------------------------------------------------

EVIDENCE_TEXT_CHARS = 1400


def page_label(start, end) -> str:
    """"p. 4", "pp. 4-6", or an honest admission that there is no page."""
    try:
        first = int(start) if start not in (None, "") else 0
        last = int(end) if end not in (None, "") else 0
    except (TypeError, ValueError):
        return ""
    # A page number is 1 or more. Chunk rows built before the page guesser
    # existed carry 0 or -1, and "p. -1" is worse than saying nothing.
    first = first if first > 0 else 0
    last = last if last > 0 else 0
    if not first and not last:
        return ""
    if not last or not first or first == last:
        return f"p. {first or last}"
    return f"pp. {first}-{last}"


def chunk_passages(ticker: str, chunk_ids: list) -> dict:
    """The exact retrieved text for these chunks, scoped to this ticker.

    Ticker-scoped with no fallback. If a chunk is not found under this ticker it
    is simply absent from the result, and the caller says "passage not found"
    rather than showing another company's text.
    """
    wanted = [str(value) for value in (chunk_ids or []) if str(value or "").strip()]
    if not wanted or not str(ticker or "").strip():
        return {}
    out = {}
    conn = connect()
    try:
        if not table_exists(conn, "rag_chunks"):
            return {}
        have = columns_of(conn, "rag_chunks")
        columns = ["chunk_id", "chunk_text"]
        for optional in ("source_vendor_candidates", "file_name", "strategy_theme_guess",
                         "chunk_index", "chunk_char_count"):
            if optional in have:
                columns.append(optional)
        for start in range(0, len(wanted), 200):
            batch = wanted[start:start + 200]
            marks = ",".join("?" * len(batch))
            rows = conn.execute(
                f"SELECT {','.join(columns)} FROM rag_chunks "
                f"WHERE ticker = ? AND chunk_id IN ({marks})",
                [str(ticker).upper()] + batch).fetchall()
            for record in rows:
                out[record["chunk_id"]] = row_to_dict(record)
    except sqlite3.Error:
        return out
    finally:
        conn.close()
    return out


def document_titles(document_ids: list) -> dict:
    """File name and publisher per document, read from the documents table."""
    wanted = [str(value) for value in (document_ids or []) if str(value or "").strip()]
    if not wanted:
        return {}
    out = {}
    conn = connect()
    try:
        if not table_exists(conn, "documents"):
            return {}
        have = columns_of(conn, "documents")
        columns = ["document_id"]
        for optional in ("file_name", "relative_path", "source_vendor_candidates",
                         "source_type_guess", "document_type_guess", "folder_date_guess",
                         "detected_year", "archive_year", "ticker_candidates_clean"):
            if optional in have:
                columns.append(optional)
        for start in range(0, len(wanted), 200):
            batch = wanted[start:start + 200]
            marks = ",".join("?" * len(batch))
            rows = conn.execute(
                f"SELECT {','.join(columns)} FROM documents WHERE document_id IN ({marks})",
                batch).fetchall()
            for record in rows:
                out[record["document_id"]] = row_to_dict(record)
    except sqlite3.Error:
        return out
    finally:
        conn.close()
    return out


def enrich_evidence(ticker: str, evidence: list, citations_used: str = "") -> list:
    """One structured record per citation, ready to verify.

    Additive: every field the Phase 7B / Phase 10A panel already read is left
    exactly where it was, and the new ones sit alongside. A page written against
    the old shape still works.
    """
    items = [item for item in (evidence or []) if isinstance(item, dict)]
    if not items:
        return items

    passages = chunk_passages(ticker, [item.get("chunk_id") for item in items])
    documents = document_titles([item.get("document_id") for item in items])
    cited = {int(n) for n in str(citations_used or "").split(",") if n.strip().isdigit()}

    for item in items:
        chunk = passages.get(str(item.get("chunk_id") or "")) or {}
        document = documents.get(str(item.get("document_id") or "")) or {}
        text = str(chunk.get("chunk_text") or "")
        truncated = len(text) > EVIDENCE_TEXT_CHARS

        item["ticker"] = str(ticker or "").upper()
        item["title"] = (item.get("file_name") or document.get("file_name")
                         or chunk.get("file_name") or "")
        item["publisher"] = (chunk.get("source_vendor_candidates")
                             or document.get("source_vendor_candidates") or "")
        item["source_type"] = (item.get("source_type_guess")
                               or document.get("source_type_guess") or "")
        item["document_type"] = (item.get("document_type_guess")
                                 or document.get("document_type_guess") or "")
        item["document_date"] = (item.get("folder_date_guess")
                                 or document.get("folder_date_guess")
                                 or document.get("detected_year") or "")
        item["page_start"] = item.get("page_start_guess")
        item["page_end"] = item.get("page_end_guess")
        item["page_label"] = page_label(item.get("page_start_guess"), item.get("page_end_guess"))
        item["supporting_text"] = text[:EVIDENCE_TEXT_CHARS]
        item["supporting_text_truncated"] = truncated
        item["supporting_text_found"] = bool(text)
        item["chunk_index"] = chunk.get("chunk_index")
        if cited:
            item["cited"] = item.get("citation") in cited
        elif "cited" not in item:
            item["cited"] = None
    return items


def retrieval_summary_of(row: dict) -> Optional[dict]:
    """The run's retrieval log, reduced to what the panel shows.

    Read from the log the run wrote at the time. Nothing is recomputed, so this
    is what happened, not what would happen if the question were asked now.
    """
    raw = read_project_file(row.get("retrieval_log_path") or "")
    if not raw:
        return None
    try:
        log = json.loads(raw)
        selected = [c for c in (log.get("candidates") or []) if c.get("selected")]
        summary = {
            "retrieval_mode_effective": log.get("retrieval_mode_effective"),
            "filters": log.get("filters"),
            "question_intent": (log.get("question_intent") or {}).get("intent"),
            "preference_applied": log.get("preference_applied"),
            "candidates_returned": log.get("candidates_returned"),
            "selected_chunks": len(selected),
            "selected_documents": log.get("selected_documents"),
            "confidence_rule": log.get("confidence_rule"),
            "confidence_rule_reason": log.get("confidence_rule_reason"),
            "final_confidence": log.get("final_confidence"),
            "directness_sections": log.get("directness_sections"),
            "warnings": log.get("warnings"),
            "evidence": [
                {
                    "citation": c.get("citation"),
                    "file_name": c.get("file_name"),
                    "folder_date_guess": c.get("folder_date_guess"),
                    "source_type_guess": c.get("source_type_guess"),
                    "document_type_guess": c.get("document_type_guess"),
                    "page_start_guess": c.get("page_start_guess"),
                    "page_end_guess": c.get("page_end_guess"),
                    "chunk_id": c.get("chunk_id"),
                    # Phase 10A. This is what lets a reader go from a citation
                    # to the document it came from, which is the whole point of
                    # the library existing.
                    "document_id": c.get("document_id"),
                    "relative_path": c.get("relative_path"),
                    "vector_score": c.get("vector_score"),
                    "keyword_score": c.get("keyword_score"),
                    "hybrid_score": c.get("hybrid_score"),
                }
                for c in sorted(selected, key=lambda c: c.get("citation") or 0)
            ],
        }
    except (json.JSONDecodeError, AttributeError, TypeError):
        return {"error": "The retrieval log could not be parsed."}

    summary["evidence"] = enrich_evidence(row.get("ticker") or "", summary["evidence"],
                                          row.get("citations_used") or "")
    return summary


def run_evidence(row: dict) -> list:
    """The structured evidence records for one run row, or an empty list."""
    summary = retrieval_summary_of(row) or {}
    evidence = summary.get("evidence")
    return evidence if isinstance(evidence, list) else []


@app.get("/api/run/{run_id}")
def run_detail(run_id: int):
    conn = connect()
    try:
        if not table_exists(conn, "ask_brain_runs"):
            raise HTTPException(status_code=404, detail="No Ask Brain runs recorded yet.")
        record = conn.execute("SELECT * FROM ask_brain_runs WHERE run_id = ?",
                              (int(run_id),)).fetchone()
    finally:
        conn.close()
    if record is None:
        raise HTTPException(status_code=404, detail=f"No run with id {run_id}.")

    row = row_to_dict(record)
    answer_markdown = read_project_file(row.get("answer_path") or "")
    retrieval_summary = retrieval_summary_of(row)

    # Phase 13.1: the parse and the "which of these did the answer cite"
    # marking both moved into retrieval_summary_of()/enrich_evidence(), because
    # the follow-up path needs the same records and two copies of this logic
    # would drift.

    diagnostics = row.get("hybrid_diagnostics_path") or ""
    return {
        "run": row,
        "answer_markdown": answer_markdown,
        "answer_exists": answer_markdown is not None,
        "retrieval_summary": retrieval_summary,
        # Phase 13.1: the same records, named at the top level. The panel reads
        # them from here; retrieval_summary keeps its own copy so every reader
        # written before this phase still finds them where they were.
        "evidence": (retrieval_summary or {}).get("evidence") or [],
        # Phase 13: the same facts in PM language, alongside the raw ones rather
        # than instead of them.
        "trust": build_trust(row, retrieval_summary),
        "audit": classify_audit(row),
        "review_reasons": review_reasons(row),
        "diagnostics_path": diagnostics,
        "diagnostics_exists": bool(diagnostics)
        and (PROJECT_ROOT / diagnostics).is_file(),
    }


@app.get("/api/diagnostics/{run_id}")
def diagnostics(run_id: int, limit: int = 20):
    """The per-candidate retrieval trace for one run.

    Everything here was written by Ask Brain at the time of the run. Nothing is
    recomputed, and no retrieval happens to serve this - it is a CSV read.
    """
    limit = max(1, min(int(limit), 200))
    conn = connect()
    try:
        if not table_exists(conn, "ask_brain_runs"):
            raise HTTPException(status_code=404, detail="No Ask Brain runs recorded yet.")
        record = conn.execute("SELECT * FROM ask_brain_runs WHERE run_id = ?",
                              (int(run_id),)).fetchone()
    finally:
        conn.close()
    if record is None:
        raise HTTPException(status_code=404, detail=f"No run with id {run_id}.")

    row = row_to_dict(record)
    parsed = read_diagnostics(row.get("hybrid_diagnostics_path") or "", limit)
    if parsed is None:
        return {
            "run_id": row.get("run_id"),
            "available": False,
            "note": "Diagnostics were not recorded for this run.",
            "candidates_considered": row.get("candidates_considered") or 0,
            "selected_chunks": row.get("selected_chunks") or 0,
            "selected_documents": row.get("selected_documents") or 0,
            "avg_vector_score": row.get("avg_vector_score"),
            "avg_keyword_score": row.get("avg_keyword_score"),
            "avg_hybrid_score": row.get("avg_hybrid_score"),
            "rows": [],
        }

    parsed.update({
        "run_id": row.get("run_id"),
        "available": True,
        "retrieval_mode_effective": row.get("retrieval_mode_effective") or "",
        "source_priority": row.get("source_priority") or "",
        "filter_summary": row.get("filter_summary") or "",
        # The run row's own averages are over the SELECTED evidence; the CSV
        # averages are over every candidate. Both are useful, so both are named.
        "avg_vector_score_selected": row.get("avg_vector_score"),
        "avg_keyword_score_selected": row.get("avg_keyword_score"),
        "avg_hybrid_score_selected": row.get("avg_hybrid_score"),
    })
    return parsed


@app.get("/api/review-queue")
def review_queue(limit: int = REVIEW_LIMIT, ticker: str = "MSFT"):
    """Runs a person should look at again.

    Low confidence, an audit flag, a directness cap, or a run that did not
    complete. This is the trust feature: the point of the system is not that
    every answer is good, it is that the weak ones are visible.
    """
    limit = max(1, min(int(limit), 100))
    ticker = clean_text(ticker, 15).upper() or "MSFT"

    conn = connect()
    try:
        if not table_exists(conn, "ask_brain_runs"):
            return {"runs": [], "note": "No Ask Brain runs recorded yet."}
        have = columns_of(conn, "ask_brain_runs")
        wanted = [c for c in ("run_id", "created_at", "question", "final_confidence",
                              "confidence_stated", "confidence_rule", "audit_flags",
                              "flagged_language", "directness_cap_applied", "status",
                              "retrieval_mode_effective", "answer_path", "obsidian_answer_path")
                  if c in have]

        conditions = ["COALESCE(status,'') <> 'completed'"]
        if "final_confidence" in have:
            conditions.append("COALESCE(final_confidence,'') = 'Low'")
        if "audit_flags" in have:
            conditions.append("COALESCE(audit_flags,'') <> ''")
        if "flagged_language" in have:
            conditions.append("COALESCE(flagged_language,'') <> ''")
        if "directness_cap_applied" in have:
            conditions.append("COALESCE(directness_cap_applied,'0') = '1'")

        rows = conn.execute(
            f"SELECT {','.join(wanted)} FROM ask_brain_runs WHERE ticker = ? "
            f"AND ({' OR '.join(conditions)}) ORDER BY run_id DESC LIMIT ?",
            (ticker, limit)).fetchall()

        total = conn.execute("SELECT COUNT(*) FROM ask_brain_runs WHERE ticker = ?",
                             (ticker,)).fetchone()[0]
    finally:
        conn.close()

    # Ordering matters more than it looks. "audit_flags is not empty" catches
    # almost every run, because "supplied chunks not cited" is the normal state
    # of a good answer - the evidence pack is deliberately wider than any one
    # answer needs. A queue where everything is flagged is a queue nobody reads,
    # so the genuinely serious runs are sorted to the top and counted
    # separately. Nothing is hidden: every run the criteria matched is still
    # returned.
    out = []
    for record in rows:
        row = row_to_dict(record)
        row["reasons"] = review_reasons(row)
        row["audit"] = classify_audit(row)

        if row["audit"]["level"] == "stop":
            severity = "serious"           # unsafe wording, or a citation pointing at nothing
        elif (str(row.get("final_confidence") or "") == "Low"
              or str(row.get("directness_cap_applied") or "0") == "1"
              or str(row.get("status") or "completed") != "completed"):
            severity = "weak"              # the answer itself is not standing up
        else:
            severity = "minor"             # reading flags only
        row["severity"] = severity
        out.append(row)

    order = {"serious": 0, "weak": 1, "minor": 2}
    out.sort(key=lambda r: (order[r["severity"]], -(r.get("run_id") or 0)))

    return {
        "runs": out,
        "total_runs": total,
        "flagged_runs": len(out),
        "serious_runs": sum(1 for r in out if r["severity"] == "serious"),
        "weak_runs": sum(1 for r in out if r["severity"] == "weak"),
        "minor_runs": sum(1 for r in out if r["severity"] == "minor"),
        "note": ("Sorted by severity. 'minor' means the only flags are reading aids - most good "
                 "answers have one, because the evidence pack is wider than the answer needs."),
    }


@app.get("/api/evaluation-status")
def evaluation_status(ticker: str = "MSFT"):
    """The last evaluation run, read-only.

    Phase 8 Part H, deliberately small. This is a status light, not an
    evaluation UI: it says when the system was last graded, how it did, and
    where the report is. Running an evaluation is a command-line act, because
    it costs money and should be a decision someone makes on purpose.
    """
    ticker = clean_text(ticker, 15).upper() or "MSFT"

    conn = connect()
    try:
        if not table_exists(conn, "evaluation_runs"):
            return {
                "available": False,
                "note": ("No evaluation has been run yet. Run "
                         "python scripts\\run_evaluation.py --ticker MSFT"),
            }
        row = conn.execute(
            "SELECT * FROM evaluation_runs WHERE ticker = ? AND status = 'completed' "
            "ORDER BY eval_run_id DESC LIMIT 1", (ticker,)).fetchone()
        if row is None:
            return {
                "available": False,
                "note": "No completed evaluation recorded for this ticker yet.",
            }
        run = row_to_dict(row)

        failing = []
        if table_exists(conn, "evaluation_results"):
            for record in conn.execute(
                    "SELECT question_id, question, overall_status, fail_count, warn_count "
                    "FROM evaluation_results WHERE eval_run_id = ? "
                    "AND overall_status IN ('FAIL','REVIEW') "
                    "ORDER BY CASE overall_status WHEN 'FAIL' THEN 0 ELSE 1 END, "
                    "eval_result_id", (run["eval_run_id"],)):
                failing.append(row_to_dict(record))
    finally:
        conn.close()

    total = run.get("questions_total") or 0
    fails = run.get("fail_count") or 0
    reviews = run.get("review_count") or 0
    if fails:
        level = "stop"
        headline = f"{fails} question(s) failed the last evaluation."
    elif reviews:
        level = "check"
        headline = f"{reviews} question(s) need a human read."
    elif total:
        level = "ok"
        headline = "Every golden question passed the last evaluation."
    else:
        level = "check"
        headline = "The last evaluation graded nothing."

    return {
        "available": True,
        "eval_run_id": run.get("eval_run_id"),
        "ticker": run.get("ticker"),
        "completed_at": run.get("completed_at") or "",
        "mode": run.get("mode") or "",
        "pipeline_version": run.get("pipeline_version") or "",
        "questions_total": total,
        "pass_count": run.get("pass_count") or 0,
        "review_count": reviews,
        "fail_count": fails,
        "pass_rate": run.get("pass_rate"),
        "total_estimated_cost": run.get("total_estimated_cost"),
        "report_path": run.get("report_path") or "",
        "json_path": run.get("json_path") or "",
        "level": level,
        "headline": headline,
        "attention": failing,
        # Phase 12: the golden evaluation across all ten tickers, alongside the
        # per-ticker Phase 8 run rather than instead of it. Additive on purpose:
        # an older UI build that does not read this key is unaffected.
        "golden": golden_evaluation_status(),
        # Said in the payload, not only in the template, so that anything else
        # reading this endpoint later inherits the caveat.
        "note": ("A PASS means the system behaved correctly on that question. It does not "
                 "mean the answer is true. Open the cited documents."),
    }


def golden_evaluation_status() -> dict:
    """Phase 12 - the latest multi-ticker golden evaluation, read-only.

    A status light, not an evaluation UI. Running the evaluation is a
    command-line act because it costs money; this only reports what the last one
    found, with a per-ticker breakdown so a single bad ticker is visible without
    opening the report.
    """
    conn = connect()
    try:
        if not table_exists(conn, "golden_eval_runs"):
            return {
                "available": False,
                "note": ("No multi-ticker golden evaluation has been run yet. Run "
                         "python scripts\\run_multi_ticker_evaluation.py"),
            }
        row = conn.execute(
            "SELECT * FROM golden_eval_runs WHERE status = 'completed' "
            "ORDER BY golden_run_id DESC LIMIT 1").fetchone()
        if row is None:
            return {"available": False,
                    "note": "No completed multi-ticker evaluation recorded yet."}
        run = row_to_dict(row)

        by_ticker = []
        if table_exists(conn, "golden_eval_results"):
            for record in conn.execute(
                    "SELECT ticker, "
                    "  COUNT(*) AS total, "
                    "  SUM(CASE WHEN overall_status = 'PASS' THEN 1 ELSE 0 END) AS pass_count, "
                    "  SUM(CASE WHEN overall_status = 'REVIEW' THEN 1 ELSE 0 END) AS review_count, "
                    "  SUM(CASE WHEN overall_status = 'FAIL' THEN 1 ELSE 0 END) AS fail_count "
                    " FROM golden_eval_results WHERE golden_run_id = ? "
                    " GROUP BY ticker ORDER BY fail_count DESC, review_count DESC, ticker",
                    (run["golden_run_id"],)):
                by_ticker.append(row_to_dict(record))
    finally:
        conn.close()

    fails = run.get("fail_count") or 0
    reviews = run.get("review_count") or 0
    total = run.get("questions_total") or 0
    if fails:
        level, headline = "stop", f"{fails} of {total} golden question(s) failed."
    elif reviews:
        level, headline = "check", f"{reviews} of {total} golden question(s) need a human read."
    elif total:
        level, headline = "ok", f"All {total} golden questions passed."
    else:
        level, headline = "check", "The last golden evaluation graded nothing."

    return {
        "available": True,
        "golden_run_id": run.get("golden_run_id"),
        "suite": run.get("suite") or "",
        "completed_at": run.get("completed_at") or "",
        "mode": run.get("mode") or "",
        "pipeline_version": run.get("pipeline_version") or "",
        "tickers": [t for t in str(run.get("tickers") or "").split(",") if t],
        "questions_total": total,
        "pass_count": run.get("pass_count") or 0,
        "review_count": reviews,
        "fail_count": fails,
        "pass_rate": run.get("pass_rate"),
        "total_estimated_cost": run.get("total_estimated_cost"),
        "report_path": run.get("report_path") or "",
        "csv_path": run.get("csv_path") or "",
        "json_path": run.get("json_path") or "",
        "per_ticker": by_ticker,
        "level": level,
        "headline": headline,
        "note": ("A refusal is not a failure: two of the six categories per ticker exist to be "
                 "declined. A PASS means the system behaved correctly, not that the answer is "
                 "true."),
    }


@app.get("/api/golden-evaluation-status")
def golden_evaluation_status_endpoint():
    """Phase 12's status on its own route, for anything that wants only this."""
    return golden_evaluation_status()


# ---------------------------------------------------------------------------
# Phase 10A - the document library
#
# Every endpoint below is READ-ONLY, takes no path from the browser, and never
# opens a source PDF. The most it will say about an original file is whether it
# is still where the index last saw it.
# ---------------------------------------------------------------------------

DOCUMENT_LIST_COLUMNS = [
    "document_id", "file_name", "relative_path", "archive_year", "detected_year",
    "detected_month", "folder_date_guess", "source_type_guess", "document_type_guess",
    "ticker_candidates_clean", "pdf_extraction_status", "document_card_status",
    "usable_for_rag", "review_severity", "file_change_status", "last_seen_at",
    "file_path", "duplicate_status",
]

DOCUMENT_SORTS = {
    "document_date": "COALESCE(NULLIF(d.folder_date_guess,''), NULLIF(d.detected_year,''), '')",
    "file_name": "LOWER(COALESCE(d.file_name,''))",
    "source_type": "LOWER(COALESCE(d.source_type_guess,''))",
    "document_type": "LOWER(COALESCE(d.document_type_guess,''))",
    "ticker": "LOWER(COALESCE(d.ticker_candidates_clean,''))",
}


def document_date_of(row: dict) -> str:
    """The date a person would file this document under."""
    for key in ("folder_date_guess", "detected_year"):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return str(row.get("archive_year") or "")


def build_trust(row: dict, retrieval_summary: Optional[dict]) -> dict:
    """Phase 13 - the run, in the words a PM needs.

    Everything here is derived from what the pipeline already recorded. Nothing
    is recomputed and no judgement is added: this is a translation layer, and
    the technical fields it reads stay available untouched under Deep Dive.

        confidence = Medium                ->  "Medium confidence"
        company_direct_count = 0           ->  "No direct company source used"
        source_kind = analyst              ->  "Mostly analyst research"
        selected_chunks = 8                ->  "8 sources reviewed"

    The point of the translation is that a reader should be able to decide
    whether to trust an answer without knowing what a chunk is.
    """
    evidence = []
    if retrieval_summary and isinstance(retrieval_summary.get("evidence"), list):
        evidence = retrieval_summary["evidence"]

    kinds = [str(item.get("source_type_guess") or "") for item in evidence]
    company = sum(1 for kind in kinds if kind in COMPANY_SOURCE_TYPES)
    analyst = sum(1 for kind in kinds if kind in ANALYST_SOURCE_TYPES)
    other = len(kinds) - company - analyst
    documents = len({item.get("document_id") for item in evidence if item.get("document_id")})
    dates = sorted(str(item.get("folder_date_guess") or "").strip() for item in evidence
                   if str(item.get("folder_date_guess") or "").strip())

    confidence = str(row.get("final_confidence") or "").strip()
    level = {"High": "ok", "Medium": "check", "Low": "stop"}.get(confidence, "check")

    # The mix, in one sentence.
    if not evidence:
        mix = "No sources were used"
    elif company and not analyst:
        mix = "Company filings and releases"
    elif analyst and not company:
        mix = "Mostly analyst research"
    elif company and analyst:
        mix = ("Company sources and analyst research" if company >= analyst
               else "Mostly analyst research, with some company sources")
    else:
        mix = "Unclassified sources"

    points = []
    if evidence:
        points.append(f"{len(evidence)} source{'s' if len(evidence) != 1 else ''} reviewed"
                      + (f" across {documents} documents" if documents > 1 else ""))
    points.append(mix)
    if evidence and not company:
        points.append("No direct company source used")
    elif company:
        points.append(f"{company} direct company source{'s' if company != 1 else ''} used")
    if dates:
        points.append(f"Newest source {dates[-1]}" if dates[-1] == dates[0]
                      else f"Sources from {dates[0]} to {dates[-1]}")
    if str(row.get("directness_cap_applied") or "0") == "1":
        points.append("The answer says its evidence is not directly on the question")
    if str(row.get("status") or "") == "no_evidence":
        points.append("Nothing relevant was found, so no answer was written")

    audit = classify_audit(row)
    caution = ""
    if audit.get("level") == "stop":
        caution = "Read the checks before using this answer."
    elif audit.get("level") == "warn":
        caution = "Some checks want a human read."

    return {
        "confidence": confidence or "unrecorded",
        "level": level,
        "headline": (f"{confidence} confidence" if confidence else "Confidence not recorded"),
        "mix": mix,
        "points": points,
        "caution": caution,
        "sources_reviewed": len(evidence),
        "documents_used": documents,
        "company_sources": company,
        "analyst_sources": analyst,
        "other_sources": other,
        "oldest_source": dates[0] if dates else "",
        "newest_source": dates[-1] if dates else "",
        # Kept so the Deep Dive tab can show the machine's own wording without a
        # second request. Not shown by default.
        "technical": {
            "confidence_rule": row.get("confidence_rule") or "",
            "confidence_stated": row.get("confidence_stated") or "",
            "confidence_rule_reason": row.get("confidence_rule_reason") or "",
            "retrieval_mode_effective": row.get("retrieval_mode_effective") or "",
            "selected_chunks": row.get("selected_chunks") or 0,
            "candidates_considered": row.get("candidates_considered") or 0,
            "filter_summary": row.get("filter_summary") or "",
            "audit_flags": row.get("audit_flags") or "",
        },
    }


# ===========================================================================
# Phase 13.1: research context
#
# Three problems, one cause. A PM asked a question, read the answer, and then
# had no way to (a) ask "why?" without retyping the whole subject, (b) get from
# a citation to the page it came from, or (c) change company without the screen
# still talking about the last one. All three are context that the browser was
# throwing away between one question and the next.
#
# Nothing below retrieves, ranks, embeds or writes. It reads what the pipeline
# already recorded and hands it back in a shape the page can act on.
# ===========================================================================

# The questions offered per company. NOT generated: they are the Phase 12
# golden questions, which were written by hand for each company and are already
# the questions this archive can answer. A suggestion produced by an API call
# would cost money, vary between page loads, and be unreviewable.
SUGGESTION_CATEGORIES = ("company_update", "analyst_concern", "risk_or_credit",
                         "timeline_or_recent_change")

# The two Phase 12 categories deliberately left out: both exist to prove the
# system refuses, so offering them as suggestions would invite a reader to
# treat a designed refusal as a broken answer.
SUGGESTION_CATEGORIES_EXCLUDED = ("unsupported_no_evidence", "recommendation_refusal")

QUESTION_BANK_PATH = (PROJECT_ROOT / "07_Evaluation" / "golden_questions"
                      / "multi_ticker_eval_questions.json")

# The fallback, used only when the golden question file is missing. Templates,
# filled with the company's own name - never another company's.
SUGGESTION_TEMPLATES = (
    ("company_update", "What does {company} report in its most recent results?"),
    ("analyst_concern", "What concerns do analysts raise about {company}?"),
    ("risk_or_credit", "What risks does the research identify for {company}?"),
    ("timeline_or_recent_change",
     "What is the most recent development at {company}, and when was it reported?"),
)

SUGGESTION_LABELS = {
    "company_update": "Company update",
    "analyst_concern": "Analyst concerns",
    "risk_or_credit": "Risks and credit",
    "timeline_or_recent_change": "Most recent change",
}

_QUESTION_BANK_CACHE: dict = {}


def company_name(ticker: str) -> str:
    """The name a person would say out loud. T is AT&T, B is Barnes Group."""
    symbol = str(ticker or "").strip().upper()
    return COMPANY_NAMES.get(symbol) or symbol or "this company"


def question_bank() -> list:
    """The Phase 12 golden questions, read once.

    Read-only, and a missing or unreadable file is not an error: the templates
    below cover every ticker without it.
    """
    stamp = ""
    try:
        stamp = str(QUESTION_BANK_PATH.stat().st_mtime)
    except OSError:
        return []
    if _QUESTION_BANK_CACHE.get("stamp") == stamp:
        return _QUESTION_BANK_CACHE.get("questions") or []
    try:
        payload = json.loads(QUESTION_BANK_PATH.read_text(encoding="utf-8"))
        questions = [q for q in (payload.get("questions") or []) if isinstance(q, dict)]
    except (OSError, json.JSONDecodeError, AttributeError, TypeError):
        questions = []
    _QUESTION_BANK_CACHE["stamp"] = stamp
    _QUESTION_BANK_CACHE["questions"] = questions
    return questions


def company_suggestions(ticker: str) -> list:
    """Four questions to offer for this company, in a fixed order.

    Deterministic: the same ticker gives the same four questions on every page
    load, in every browser, forever - because they were written down, not
    generated.
    """
    symbol = str(ticker or "").strip().upper()
    name = company_name(symbol)
    found = {}
    for row in question_bank():
        if str(row.get("ticker") or "").upper() != symbol:
            continue
        category = str(row.get("category") or "")
        if category in SUGGESTION_CATEGORIES_EXCLUDED:
            continue
        if category in SUGGESTION_CATEGORIES and category not in found:
            text = str(row.get("question") or "").strip()
            if text:
                found[category] = text

    out = []
    for category, template in SUGGESTION_TEMPLATES:
        text = found.get(category) or template.format(company=name)
        out.append({"category": category,
                    "label": SUGGESTION_LABELS.get(category, "Question"),
                    "question": text,
                    "source": "phase12_golden_questions" if category in found else "template"})
    return out


# --- follow-up classification ---------------------------------------------
#
# A follow-up is one of three things, and which one it is decides where the
# evidence comes from. The rules are regular expressions over the follow-up
# text, written down here, and they are the whole mechanism: no model is asked
# to classify anything, and no chat history is concatenated into a query.

# "Which documents support this?" - the answer is the evidence list itself.
# These need no retrieval and no generation: the previous run already recorded
# every source, and reading it back is free and exact.
RE_EVIDENCE_REFERENCE = re.compile(
    r"(which|what|where)\s+(documents?|sources?|filings?|notes?)\b"
    r"|documents?\s+support"
    r"|sources?\s+support"
    r"|support\s+(this|that|it)\b"
    r"|where\s+(did|does)\s+(this|that|it)\s+come\s+from"
    r"|where\s+is\s+(this|that|it)\s+from"
    r"|show\s+(me\s+)?(the\s+)?(source|sources|document|documents|evidence)"
    r"|which\s+analyst\s+(said|wrote|raised)"
    r"|who\s+(said|wrote)\s+(this|that|it)\b"
    r"|cite\s+(this|that|the\s+source)",
    re.I)

# "Why?", "Tell me more about the second point." - the subject is the answer on
# screen, so the documents that answer came from are the right place to look.
RE_CONTEXTUAL_FOLLOWUP = re.compile(
    r"^\s*why\b"
    r"|^\s*how\s+so\b"
    r"|\bwhat\s+changed\s+(from\s+before|since|compared)"
    r"|\btell\s+me\s+more\b"
    r"|\bmore\s+(detail|details|on\s+(this|that))\b"
    r"|\bexpand\s+on\b"
    r"|\bgo\s+deeper\b"
    r"|\b(first|second|third|fourth|last)\s+(point|bullet|item|one)\b"
    r"|\bsay\s+more\b"
    r"|\bexplain\s+(this|that|it)\b"
    r"|\bwhat\s+do(es)?\s+(this|that)\s+mean\b",
    re.I)

FOLLOW_UP_WORD_LIMIT = 14


def other_company_named(text: str, ticker: str) -> str:
    """A ticker other than the selected one, named in the follow-up.

    "What about Verizon?" while AT&T is selected is a new subject, not a
    follow-up about AT&T, and answering it out of AT&T's documents would be
    wrong in a way nobody would notice.
    """
    haystack = str(text or "")
    current = str(ticker or "").upper()
    for symbol, name in COMPANY_NAMES.items():
        if symbol == current:
            continue
        # A word boundary, not a space either side. "What about Verizon?" ends
        # the name with a question mark, and padding with spaces missed it - the
        # exact shape of question a person types when they change subject.
        if re.search(r"\b" + re.escape(name) + r"\b", haystack, re.I):
            return symbol
    return ""


def classify_follow_up(text: str, ticker: str = "") -> dict:
    """Which of the three kinds of follow-up this is, and why.

    Returned as a dict rather than a string so the page - and the tests - can
    show the reason. A classifier that cannot explain itself is a classifier
    nobody can check.
    """
    question = str(text or "").strip()
    words = len(question.split())
    switched = other_company_named(question, ticker)

    if switched:
        return {"kind": "new_subject", "reason": f"the question names {switched}, not {ticker}",
                "names_other_company": switched}
    if RE_EVIDENCE_REFERENCE.search(question):
        return {"kind": "evidence_reference",
                "reason": "the question asks which sources the last answer used"}
    if RE_CONTEXTUAL_FOLLOWUP.search(question) and words <= FOLLOW_UP_WORD_LIMIT:
        return {"kind": "contextual",
                "reason": "the question refers back to the last answer without naming a "
                          "new subject"}
    return {"kind": "new_subject",
            "reason": "the question stands on its own, so it gets a fresh search"}


def compose_follow_up_question(follow_up: str, previous_question: str) -> str:
    """One question, carrying exactly one piece of history.

    This is the whole of the "memory": the previous question, named once, in
    the question itself. There is no conversation buffer, no summary of the
    session and no hidden state - so what the pipeline was asked is always
    visible in the run row, which is what makes a run reproducible.
    """
    follow = str(follow_up or "").strip()
    previous = " ".join(str(previous_question or "").split())[:220]
    if not previous:
        return follow
    return f'{follow} (Follow-up to the earlier question: "{previous}")'


def previous_run_context(previous_run_id: int, ticker: str) -> Optional[dict]:
    """What the page is allowed to carry forward from one answer to the next.

    Named fields, read from the database and the run's own retrieval log - not
    from the browser. The browser sends a run id; everything else is looked up
    here, so a page cannot claim an answer cited a document it never cited.
    """
    try:
        run_id = int(previous_run_id or 0)
    except (TypeError, ValueError):
        return None
    if run_id <= 0:
        return None

    conn = connect()
    try:
        if not table_exists(conn, "ask_brain_runs"):
            return None
        record = conn.execute("SELECT * FROM ask_brain_runs WHERE run_id = ?",
                             (run_id,)).fetchone()
    finally:
        conn.close()
    if record is None:
        return None

    row = row_to_dict(record)
    previous_ticker = str(row.get("ticker") or "").upper()
    if ticker and previous_ticker and previous_ticker != str(ticker).upper():
        # A follow-up to an answer about another company is not a follow-up.
        return {"run_id": run_id, "ticker": previous_ticker, "usable": False,
                "reason": f"the previous answer was about {previous_ticker}, not {ticker}",
                "question": row.get("question") or "", "document_ids": [], "chunk_ids": [],
                "citations": []}

    evidence = run_evidence(row)
    document_ids, chunk_ids, citations = [], [], []
    for item in evidence:
        document_id = str(item.get("document_id") or "")
        if document_id and document_id not in document_ids:
            document_ids.append(document_id)
        chunk_id = str(item.get("chunk_id") or "")
        if chunk_id and chunk_id not in chunk_ids:
            chunk_ids.append(chunk_id)
        if item.get("citation") is not None:
            citations.append(item.get("citation"))

    return {
        "run_id": run_id,
        "ticker": previous_ticker,
        "usable": bool(document_ids),
        "reason": "" if document_ids else "the previous answer recorded no evidence to reuse",
        "question": row.get("question") or "",
        "final_confidence": row.get("final_confidence") or "",
        "status": row.get("status") or "",
        "answer_path": row.get("answer_path") or "",
        "document_ids": document_ids,
        "chunk_ids": chunk_ids,
        "citations": citations,
        "evidence": evidence,
    }


def follow_up_plan(question: str, ticker: str, previous_run_id: int) -> dict:
    """What asking this follow-up would do, worked out before anything is spent.

    The page shows this to the reader as a sentence, and the tests assert on it.
    Both need the decision to exist separately from the act of running it.
    """
    classification = classify_follow_up(question, ticker)
    previous = previous_run_context(previous_run_id, ticker)
    kind = classification["kind"]

    if previous is None:
        return {"kind": "new_subject", "reason": "there is no previous answer to follow up on",
                "classified_as": kind, "reuses_evidence": False, "runs_retrieval": True,
                "previous": None, "restrict_to_documents": [],
                "explanation": "This is a new question, so the whole index is searched."}
    if not previous.get("usable"):
        return {"kind": "new_subject", "reason": previous.get("reason") or "",
                "classified_as": kind, "reuses_evidence": False, "runs_retrieval": True,
                "previous": previous, "restrict_to_documents": [],
                "explanation": "This is answered as a new question: "
                               + (previous.get("reason") or "the previous answer cannot be "
                                                            "carried forward") + "."}

    if kind == "evidence_reference":
        return {
            "kind": "evidence_reference", "reason": classification["reason"],
            "classified_as": kind, "reuses_evidence": True, "runs_retrieval": False,
            "previous": previous, "restrict_to_documents": [],
            "explanation": (f"Answered from the {len(previous['evidence'])} source(s) behind the "
                            "previous answer. No new search and no model call, so nothing is "
                            "spent."),
        }
    if kind == "contextual":
        return {
            "kind": "contextual", "reason": classification["reason"],
            "classified_as": kind, "reuses_evidence": True, "runs_retrieval": True,
            "previous": previous,
            "restrict_to_documents": list(previous["document_ids"]),
            "explanation": (f"Searched inside the {len(previous['document_ids'])} document(s) the "
                            "previous answer came from, because the question refers back to it."),
        }
    # A follow-up that names another company is the one case where saying
    # nothing would be dangerous. The context is correctly dropped - answering a
    # Kroger question out of Microsoft's documents would be wrong - but the
    # question is still asked of the company in the box, and the reader has to
    # be told that, or they will read a refusal as "the archive has nothing".
    named = classification.get("names_other_company") or ""
    if named:
        return {
            "kind": "new_subject", "reason": classification["reason"], "classified_as": kind,
            "reuses_evidence": False, "runs_retrieval": True, "previous": previous,
            "restrict_to_documents": [], "names_other_company": named,
            "explanation": (f"This question names {company_name(named)}, but "
                            f"{company_name(ticker)} is the selected company, so it was searched "
                            f"in {company_name(ticker)}'s documents. Change the company above and "
                            "ask again to search " + company_name(named) + "."),
        }
    return {
        "kind": "new_subject", "reason": classification["reason"], "classified_as": kind,
        "reuses_evidence": False, "runs_retrieval": True, "previous": previous,
        "restrict_to_documents": [],
        "explanation": "This changes the subject, so the whole index is searched again.",
    }


def evidence_answer_markdown(plan: dict) -> str:
    """The answer to "which documents support this?", written from the record.

    Not generated. Every line is a field the previous run wrote down, and the
    heading says so, because an answer that looks model-written but is not is
    worse than either.
    """
    previous = plan.get("previous") or {}
    evidence = previous.get("evidence") or []
    question = " ".join(str(previous.get("question") or "").split())
    lines = [
        "## 1. Direct Answer",
        "",
        f"The previous answer — *{question}* — was written from the "
        f"{len(evidence)} passage(s) listed below. This list is read back from the run's own "
        "retrieval log; no new search was made and no model was asked anything.",
        "",
    ]
    cited = [item for item in evidence if item.get("cited") is not False]
    if cited and len(cited) != len(evidence):
        lines.append(f"{len(cited)} of the {len(evidence)} were actually cited in the answer. "
                     "The rest were read and not used.")
        lines.append("")
    for item in evidence:
        mark = "" if item.get("cited") is not False else "  *(read, not cited)*"
        pages = item.get("page_label") or "no page recorded"
        lines.append(
            f"- **[{item.get('citation')}]** {item.get('title') or 'untitled document'} — "
            f"{item.get('publisher') or 'no publisher recorded'}, "
            f"{item.get('document_date') or 'no date recorded'} "
            f"({item.get('source_type') or 'no source type'}, {pages}).{mark}")
    lines += [
        "",
        "Open any of them in the Sources panel to read the passage and the original document.",
        "",
        "## 2. Evidence",
        "",
        "The evidence for this reply is the previous run's retrieval log, "
        f"`{previous.get('run_id')}`. Nothing else was consulted.",
        "",
        "## 3. What this does not tell you",
        "",
        "That a document was used does not mean the claim drawn from it was read correctly. "
        "The passage behind each source is shown beside it so that can be checked.",
        "",
        "AI drafts. Humans verify. Cutler decides.",
    ]
    return "\n".join(lines)


@app.get("/api/follow-up-plan")
def follow_up_plan_endpoint(question: str = "", ticker: str = "", previous_run_id: int = 0):
    """What a follow-up would do, without doing it.

    A GET, so it is free to call on every keystroke if the page wants to. The
    page uses it to tell the reader, before they press the button, whether this
    will reuse the sources already on screen or start a new search.
    """
    text = clean_text(question, MAX_QUESTION_CHARS)
    symbol = clean_text(ticker, 15).upper()
    plan = follow_up_plan(text, symbol, previous_run_id)
    previous = plan.get("previous") or {}
    return {
        "kind": plan["kind"],
        "classified_as": plan["classified_as"],
        "reason": plan["reason"],
        "explanation": plan["explanation"],
        "reuses_evidence": plan["reuses_evidence"],
        "runs_retrieval": plan["runs_retrieval"],
        "restrict_to_documents": plan["restrict_to_documents"],
        "names_other_company": plan.get("names_other_company") or "",
        "previous_run_id": previous.get("run_id") or 0,
        "previous_question": previous.get("question") or "",
        "previous_documents": len(previous.get("document_ids") or []),
        "previous_sources": len(previous.get("evidence") or []),
    }


@app.get("/api/company-context/{ticker}")
def company_context(ticker: str):
    """Everything the page needs when the company changes.

    One request, so switching company cannot leave half the screen talking
    about the last one. Every field is either recorded metadata or a question
    written down in the Phase 12 suite - nothing here is generated.
    """
    symbol = clean_text(ticker, 15).upper()
    if symbol not in ALLOWED_TICKERS:
        raise HTTPException(status_code=400,
                            detail=f"{symbol or 'That ticker'} is not one of the tickers this "
                                   f"project knows: {', '.join(ALLOWED_TICKERS)}.")
    name = company_name(symbol)
    suggestions = company_suggestions(symbol)
    status = ticker_status(symbol)

    documents = newest = 0
    newest_document = ""
    questions_asked = 0
    conn = connect()
    try:
        if table_exists(conn, "documents"):
            row = conn.execute(
                "SELECT COUNT(*) AS n, MAX(COALESCE(NULLIF(folder_date_guess,''), "
                "NULLIF(detected_year,''), '')) AS newest FROM documents "
                "WHERE ',' || REPLACE(COALESCE(ticker_candidates_clean,''), ' ', '') || ',' "
                "LIKE ?", (f"%,{symbol},%",)).fetchone()
            if row:
                documents = row["n"] or 0
                newest_document = row["newest"] or ""
        if table_exists(conn, "ask_brain_runs"):
            questions_asked = conn.execute(
                "SELECT COUNT(*) FROM ask_brain_runs WHERE ticker = ?", (symbol,)).fetchone()[0]
    finally:
        conn.close()

    return {
        "ticker": symbol,
        "company": name,
        # The placeholder is the company's own first suggestion, so the empty
        # box never asks about Microsoft while Kroger is selected.
        "placeholder": suggestions[0]["question"] if suggestions else f"Ask about {name}…",
        "suggestions": suggestions,
        "indexed": bool(status.get("indexed")),
        "index_note": status.get("reason") or "",
        "documents": documents,
        "newest_document": newest_document,
        "questions_asked": questions_asked,
        "note": ("The suggested questions are the Phase 12 golden questions for this company. "
                 "They are read from a file, not generated, so they are the same every time."),
    }


@app.get("/api/history")
def history(limit: int = 40):
    """Phase 13 - every recent answer, across every ticker.

    /api/runs is per-ticker and is what the old right rail used. This is the
    History view: one list, newest first, with enough on each row to recognise
    a question without opening it.
    """
    limit = max(1, min(int(limit), 200))
    conn = connect()
    try:
        if not table_exists(conn, "ask_brain_runs"):
            return {"available": False, "runs": [], "note": "No questions have been asked yet."}
        rows = [row_to_dict(r) for r in conn.execute(
            "SELECT run_id, ticker, question, created_at, status, final_confidence, "
            "       selected_documents, selected_chunks, citations_used, audit_flags "
            "  FROM ask_brain_runs ORDER BY run_id DESC LIMIT ?", (limit,))]
    finally:
        conn.close()
    for row in rows:
        row["company"] = COMPANY_NAMES.get(str(row.get("ticker") or "").upper(), "")
        row["cited_sources"] = len([n for n in str(row.get("citations_used") or "").split(",")
                                    if n.strip()])
        row["needs_review"] = bool(review_reasons(row))
    return {"available": True, "runs": rows, "total": len(rows)}


@app.get("/api/companies")
def companies():
    """Phase 13 - the ten indexed companies, with what is behind each one.

    A light shell over data that already exists: the index state per ticker and
    the document counts. No new concept, and nothing here belongs to a later
    phase.
    """
    conn = connect()
    try:
        chunks = {}
        if table_exists(conn, "rag_chunks"):
            for row in conn.execute(
                    "SELECT UPPER(ticker) AS ticker, COUNT(*) AS chunks, "
                    "       COUNT(DISTINCT document_id) AS documents "
                    "  FROM rag_chunks WHERE embedding_status = 'embedded' GROUP BY 1"):
                chunks[row["ticker"]] = {"chunks": row["chunks"], "documents": row["documents"]}
        asked = {}
        if table_exists(conn, "ask_brain_runs"):
            for row in conn.execute(
                    "SELECT UPPER(ticker) AS ticker, COUNT(*) AS runs, MAX(created_at) AS last "
                    "  FROM ask_brain_runs GROUP BY 1"):
                asked[row["ticker"]] = {"questions": row["runs"], "last_asked": row["last"] or ""}
        dates = {}
        for row in conn.execute(
                "SELECT UPPER(COALESCE(ticker_candidates_clean,'')) AS tags, "
                "       MAX(COALESCE(folder_date_guess,'')) AS newest FROM documents "
                " WHERE COALESCE(folder_date_guess,'') <> '' GROUP BY 1"):
            for tag in str(row["tags"] or "").replace(";", ",").split(","):
                tag = tag.strip()
                if tag and row["newest"] > dates.get(tag, ""):
                    dates[tag] = row["newest"]
    finally:
        conn.close()

    out = []
    for ticker in ALLOWED_TICKERS:
        index = chunks.get(ticker, {})
        runs = asked.get(ticker, {})
        out.append({
            "ticker": ticker,
            "company": COMPANY_NAMES.get(ticker, ticker),
            "documents": index.get("documents", 0),
            "indexed": bool(index.get("documents")),
            "questions_asked": runs.get("questions", 0),
            "last_asked": runs.get("last_asked", ""),
            "newest_document": dates.get(ticker, ""),
        })
    return {"available": True, "companies": out, "total": len(out)}


@app.get("/api/themes")
def themes(limit: int = 24):
    """Phase 13 - the themes Phase 1 already guessed, counted.

    A light shell, deliberately. `strategy_theme_guess` is a Phase 1 guess and
    is labelled as one here; turning it into a real thematic workspace is a
    later phase, and inventing that now would be inventing depth this data does
    not have.
    """
    limit = max(1, min(int(limit), 100))
    conn = connect()
    try:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(documents)")}
        if "strategy_theme_guess" not in columns:
            return {"available": False, "themes": [],
                    "note": "This database has no strategy_theme_guess column."}
        rows = [{"theme": row["theme"], "documents": row["documents"],
                 "newest": row["newest"] or ""}
                for row in conn.execute(
                    "SELECT COALESCE(NULLIF(TRIM(strategy_theme_guess), ''), 'Unclassified') "
                    "         AS theme, "
                    "       COUNT(*) AS documents, MAX(COALESCE(folder_date_guess,'')) AS newest "
                    "  FROM documents GROUP BY 1 ORDER BY documents DESC LIMIT ?", (limit,))]
    finally:
        conn.close()
    return {"available": True, "themes": rows, "total": len(rows),
            "note": "Themes are a Phase 1 guess from the file name and folder, not a "
                    "classification anyone has confirmed."}


@app.get("/api/evaluation-dashboard")
def evaluation_dashboard():
    """Phase 13 - the golden evaluation, summarised for a reader.

    Reads the Phase 12 tables and nothing else. It aggregates; it does not
    re-score, and it does not return the per-question check rows by default -
    those are one request away, on /api/evaluation-questions.
    """
    golden = golden_evaluation_status()
    if not golden.get("available"):
        return {"available": False, "note": golden.get("note", "")}

    conn = connect()
    try:
        by_category, critical, questions = [], 0, 0
        if table_exists(conn, "golden_eval_results"):
            for row in conn.execute(
                    "SELECT category, COUNT(*) AS total, "
                    "  SUM(CASE WHEN overall_status='PASS' THEN 1 ELSE 0 END) AS pass_count, "
                    "  SUM(CASE WHEN overall_status='REVIEW' THEN 1 ELSE 0 END) AS review_count, "
                    "  SUM(CASE WHEN overall_status='FAIL' THEN 1 ELSE 0 END) AS fail_count "
                    " FROM golden_eval_results WHERE golden_run_id = ? "
                    " GROUP BY category ORDER BY fail_count DESC, category",
                    (golden["golden_run_id"],)):
                by_category.append(row_to_dict(row))
            questions = conn.execute(
                "SELECT COUNT(*) FROM golden_eval_results WHERE golden_run_id = ?",
                (golden["golden_run_id"],)).fetchone()[0]
            # A "critical" failure is one of the two that mean the system
            # misbehaved rather than that an index is thin: it gave advice, or
            # it used another company's material.
            critical = conn.execute(
                "SELECT COUNT(*) FROM golden_eval_results "
                " WHERE golden_run_id = ? AND overall_status = 'FAIL' "
                "   AND (failed_checks LIKE '%refusal_check%' "
                "        OR failed_checks LIKE '%ticker_correctness%' "
                "        OR failed_checks LIKE '%source_grounding%')",
                (golden["golden_run_id"],)).fetchone()[0]
    finally:
        conn.close()

    return {
        **golden,
        "questions_recorded": questions,
        "per_category": by_category,
        "critical_failures": critical,
        "review_means": (
            "REVIEW is not a failure. Two of the six categories per ticker exist to be "
            "declined, and an answerable question that the system declines - because that "
            "ticker's index does not cover it - is flagged for a human rather than marked "
            "wrong. A FAIL means the system misbehaved: it advised, it cited nothing, it "
            "stated a figure that is not in the sources, or it used another company's "
            "material."),
        "pass_means": (
            "A PASS means the system behaved correctly on that question. It does not mean "
            "the answer is true. Open the cited documents."),
    }


@app.get("/api/evaluation-questions")
def evaluation_questions(status: str = "", ticker: str = "", limit: int = 200):
    """Phase 13 - the per-question rows, on demand rather than by default."""
    limit = max(1, min(int(limit), 500))
    conn = connect()
    try:
        if not table_exists(conn, "golden_eval_runs"):
            return {"available": False, "questions": []}
        run = conn.execute("SELECT golden_run_id FROM golden_eval_runs "
                           " WHERE status = 'completed' ORDER BY golden_run_id DESC "
                           " LIMIT 1").fetchone()
        if run is None:
            return {"available": False, "questions": []}
        where, params = ["golden_run_id = ?"], [run["golden_run_id"]]
        wanted = clean_text(status, 10).upper()
        if wanted in ("PASS", "REVIEW", "FAIL"):
            where.append("overall_status = ?")
            params.append(wanted)
        symbol = clean_text(ticker, 15).upper()
        if symbol:
            where.append("UPPER(ticker) = ?")
            params.append(symbol)
        params.append(limit)
        rows = [row_to_dict(r) for r in conn.execute(
            "SELECT question_id, ticker, category, question, overall_status, final_confidence, "
            "       cited_sources, failed_checks, review_checks "
            f"  FROM golden_eval_results WHERE {' AND '.join(where)} "
            "  ORDER BY CASE overall_status WHEN 'FAIL' THEN 0 WHEN 'REVIEW' THEN 1 ELSE 2 END, "
            "           ticker, category LIMIT ?", params)]
    finally:
        conn.close()
    for row in rows:
        row["company"] = COMPANY_NAMES.get(str(row.get("ticker") or "").upper(), "")
    return {"available": True, "questions": rows, "total": len(rows)}


@app.get("/api/documents")
def documents(ticker: str = "", source_type: str = "", document_type: str = "",
              date_from: str = "", date_to: str = "", search: str = "",
              rag_ready: str = "", extraction_status: str = "", card_status: str = "",
              missing: str = "", limit: int = 100, offset: int = 0,
              sort: str = "document_date", direction: str = "desc"):
    """The document library.

    Every filter is a bound parameter - the only thing built by string
    concatenation is the sort column, and that comes from a fixed dictionary,
    never from the caller's text.

    The list NEVER returns extracted text. A library is an index of what exists;
    reading is a separate, deliberate act with its own endpoint and its own
    limits.
    """
    limit = max(1, min(int(limit or 100), 500))
    offset = max(0, int(offset or 0))
    sort_key = sort if sort in DOCUMENT_SORTS else "document_date"
    order = "ASC" if str(direction or "").lower() == "asc" else "DESC"

    where = []
    params = []

    def like(column: str, value: str):
        where.append(f"LOWER(COALESCE(d.{column},'')) LIKE ?")
        params.append(f"%{value.strip().lower()}%")

    if ticker.strip():
        like("ticker_candidates_clean", clean_text(ticker, MAX_FILTER_CHARS))
    if source_type.strip():
        where.append("COALESCE(d.source_type_guess,'') = ?")
        params.append(clean_text(source_type, MAX_FILTER_CHARS))
    if document_type.strip():
        where.append("COALESCE(d.document_type_guess,'') = ?")
        params.append(clean_text(document_type, MAX_FILTER_CHARS))
    if search.strip():
        term = f"%{clean_text(search, MAX_FILTER_CHARS).lower()}%"
        where.append("(LOWER(COALESCE(d.file_name,'')) LIKE ? "
                     "OR LOWER(COALESCE(d.relative_path,'')) LIKE ? "
                     "OR LOWER(COALESCE(d.ticker_candidates_clean,'')) LIKE ?)")
        params += [term, term, term]
    if date_from.strip():
        where.append("COALESCE(NULLIF(d.folder_date_guess,''), d.detected_year, '') >= ?")
        params.append(clean_date(date_from, "date_from"))
    if date_to.strip():
        where.append("COALESCE(NULLIF(d.folder_date_guess,''), d.detected_year, '') <= ?")
        params.append(clean_date(date_to, "date_to"))
    if str(rag_ready).lower() in ("true", "1", "yes"):
        where.append("COALESCE(d.usable_for_rag,0) = 1")
    elif str(rag_ready).lower() in ("false", "0", "no"):
        where.append("COALESCE(d.usable_for_rag,0) = 0")
    if extraction_status.strip():
        like("pdf_extraction_status", clean_text(extraction_status, MAX_FILTER_CHARS))
    if card_status.strip():
        like("document_card_status", clean_text(card_status, MAX_FILTER_CHARS))
    # The row SHAPE has been tolerant of a pre-Phase-9 database since Phase 10A,
    # but this filter named the column in SQL unconditionally and crashed with a
    # 500 on an index that never had the ingestion migrations. Phase 10C's
    # header status chip is what found it - the first thing to call this filter
    # on every page load.
    want_missing = str(missing).lower() in ("true", "1", "yes")
    missing_filter_available = True

    conn = connect()
    try:
        have = columns_of(conn, "documents")
        if want_missing:
            if "file_change_status" in have:
                where.append("COALESCE(d.file_change_status,'') = 'missing_file'")
            else:
                # Say so rather than crashing, and rather than quietly returning
                # every document as though nothing had been asked for.
                missing_filter_available = False
                where.append("1 = 0")
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        wanted = [c for c in DOCUMENT_LIST_COLUMNS if c in have]
        select = ", ".join(f"d.{c}" for c in wanted)
        total = conn.execute(f"SELECT COUNT(*) FROM documents d{clause}", params).fetchone()[0]
        rows = conn.execute(
            f"SELECT {select} FROM documents d{clause} "
            f"ORDER BY {DOCUMENT_SORTS[sort_key]} {order}, LOWER(COALESCE(d.file_name,'')) ASC "
            f"LIMIT ? OFFSET ?", params + [limit, offset]).fetchall()
    finally:
        conn.close()

    out = []
    for record in rows:
        row = row_to_dict(record)
        # A stable shape whatever phases have run against this database: a
        # column added in Phase 9 is absent on an older index, and a UI that
        # has to guess which keys exist is a UI that breaks quietly.
        for column in DOCUMENT_LIST_COLUMNS:
            row.setdefault(column, "")
        source_path = str(row.pop("file_path", "") or "")
        marked_missing = str(row.get("file_change_status") or "") == "missing_file"
        available = False
        if source_path:
            try:
                available = Path(source_path).exists()
            except OSError:
                available = False
        row["document_date"] = document_date_of(row)
        row["marked_missing"] = marked_missing
        row["source_path_available"] = bool(available)
        out.append(row)

    return {"documents": out, "total": int(total or 0), "limit": limit, "offset": offset,
            "sort": sort_key, "direction": order.lower(),
            "missing_filter_available": missing_filter_available,
            "note": ("The list never includes document text. Open a document to read it."
                     if missing_filter_available else
                     "This database predates the Phase 9 ingestion columns, so documents with a "
                     "missing source file cannot be listed. Run an incremental ingestion to "
                     "record that state.")}


# ---------------------------------------------------------------------------
# Phase 11 - which tickers can actually answer a question
# ---------------------------------------------------------------------------

def ticker_status(ticker: str) -> dict:
    """Is there a usable index for this ticker, and if not, why not.

    Three things have to be true: embedded chunk rows, a Chroma store to
    retrieve them from, and - for hybrid - a keyword index. Rows without a
    store cannot be retrieved and a store without rows has nothing to cite, so
    "indexed" means both.
    """
    ticker = (ticker or "").strip().upper()
    state = {"ticker": ticker, "indexed": False, "embedded": 0, "pending": 0, "failed": 0,
             "documents": 0, "chroma": False, "keyword": False, "reason": "not indexed yet",
             "status": "not indexed"}
    conn = connect()
    try:
        if not table_exists(conn, "rag_chunks"):
            state["reason"] = "no chunk table in this database"
            return state
        row = conn.execute(
            "SELECT COUNT(*) AS chunks, COUNT(DISTINCT document_id) AS documents, "
            "       SUM(CASE WHEN embedding_status = 'embedded' THEN 1 ELSE 0 END) AS embedded, "
            "       SUM(CASE WHEN embedding_status = 'failed' THEN 1 ELSE 0 END) AS failed, "
            "       SUM(CASE WHEN COALESCE(embedding_status,'') NOT IN "
            "                ('embedded','failed','superseded') THEN 1 ELSE 0 END) AS pending "
            "  FROM rag_chunks WHERE UPPER(ticker) = ?", (ticker,)).fetchone()
    finally:
        conn.close()

    state["embedded"] = int(row["embedded"] or 0)
    state["pending"] = int(row["pending"] or 0)
    state["failed"] = int(row["failed"] or 0)
    state["documents"] = int(row["documents"] or 0)
    state["chroma"] = (PROJECT_ROOT / "06_RAG_Index" / "chroma" / ticker).exists()
    state["keyword"] = (PROJECT_ROOT / "06_RAG_Index" / "keyword" /
                        f"{ticker}_keyword_index.json").exists()

    if state["embedded"] and state["chroma"]:
        state["indexed"] = True
        state["status"] = "indexed" if not state["pending"] else "partial"
        state["reason"] = (f"{state['embedded']:,} embedded chunk(s)" if not state["pending"]
                           else f"{state['embedded']:,} embedded, {state['pending']:,} pending")
    elif state["embedded"] and not state["chroma"]:
        state["status"] = "broken"
        state["reason"] = "chunk rows exist but there is no Chroma store to retrieve from"
    elif state["pending"]:
        state["status"] = "pending"
        state["reason"] = f"{state['pending']:,} chunk(s) chunked but not embedded"
    return state


@app.get("/api/tickers")
def tickers():
    """Every known ticker and whether it can answer a question."""
    entries = [ticker_status(symbol) for symbol in ALLOWED_TICKERS]
    return {"tickers": entries,
            "indexed": [e["ticker"] for e in entries if e["indexed"]],
            "not_indexed": [e["ticker"] for e in entries if not e["indexed"]],
            "default": next((e["ticker"] for e in entries if e["indexed"]), ALLOWED_TICKERS[0]),
            "note": ("A ticker is listed here because the project knows its name, not because "
                     "an index exists. Ask Brain refuses an unindexed ticker rather than "
                     "answering from another ticker's index.")}


@app.get("/api/document-filters")
def document_filters():
    """The values actually present in the index, for the filter dropdowns."""
    conn = connect()
    try:
        def distinct(column: str) -> list:
            return [r[0] for r in conn.execute(
                f"SELECT DISTINCT {column} FROM documents "
                f"WHERE COALESCE({column},'') <> '' ORDER BY 1")]
        payload = {
            "source_types": distinct("source_type_guess"),
            "document_types": distinct("document_type_guess"),
            "extraction_statuses": distinct("pdf_extraction_status"),
            "card_statuses": distinct("document_card_status"),
            "total_documents": conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0],
        }
        tickers = set()
        for value, in conn.execute(
                "SELECT DISTINCT ticker_candidates_clean FROM documents "
                "WHERE COALESCE(ticker_candidates_clean,'') <> ''"):
            for token in str(value).replace(";", ",").replace("|", ",").split(","):
                if token.strip():
                    tickers.add(token.strip().upper())
        payload["tickers"] = sorted(tickers)
    finally:
        conn.close()
    return payload


@app.get("/api/document/{document_id}")
def document_detail(document_id: str):
    """Everything the project knows about one document, and no file contents."""
    document = get_document_by_id(document_id)
    document_id = document["document_id"]

    conn = connect()
    try:
        extraction = None
        if table_exists(conn, "pdf_extractions"):
            row = conn.execute("SELECT * FROM pdf_extractions WHERE document_id = ?",
                               (document_id,)).fetchone()
            extraction = row_to_dict(row) if row is not None else None
        card = None
        if table_exists(conn, "document_cards"):
            row = conn.execute("SELECT * FROM document_cards WHERE document_id = ?",
                               (document_id,)).fetchone()
            card = row_to_dict(row) if row is not None else None
        chunks = {"total": 0, "embedded": 0, "pending": 0, "superseded": 0, "failed": 0}
        if table_exists(conn, "rag_chunks"):
            chunks["total"] = conn.execute(
                "SELECT COUNT(*) FROM rag_chunks WHERE document_id = ?",
                (document_id,)).fetchone()[0]
            for status, count in conn.execute(
                    "SELECT embedding_status, COUNT(*) FROM rag_chunks "
                    "WHERE document_id = ? GROUP BY 1", (document_id,)):
                chunks[str(status)] = count
    finally:
        conn.close()

    text_path = resolve_extracted_text_path(document_id, document)
    card_path = resolve_card_path(document_id, document)
    folder = resolve_extracted_text_dir(document_id, document)
    page_dir = (folder / "pages") if folder else None
    preview = preview_status(document)

    return {
        "document": {**{k: v for k, v in document.items() if k != "file_path"},
                     "document_date": document_date_of(document)},
        "extraction": extraction,
        "card": card,
        "chunks": chunks,
        "source": source_availability(document),
        "preview": preview,
        "available": {
            "extracted_text": text_path is not None,
            "document_card": card_path is not None,
            "pages": bool(page_dir and page_dir.is_dir()
                          and any(page_dir.glob("page_*.txt"))),
            "chunks": chunks["total"] > 0,
            "preview": preview["enabled"],
        },
        "note": "Phase 10B previews indexed PDFs in place. The original is never copied, "
                "renamed, moved or written to, and no other file type is served.",
    }


@app.get("/api/document/{document_id}/preview")
def document_preview(document_id: str, request: Request):
    """Stream one indexed PDF to the local browser for viewing.

    This is the only endpoint in the application that opens an original file,
    and it reaches that file by exactly one route: a validated document id ->
    the path the index recorded for it -> the gate in preview_status(). There
    is no path parameter here and there never may be. A query string is not
    read at all, so ?path=, ?file= and their relatives are inert rather than
    merely rejected.

    The file is read and streamed. It is not copied, moved, renamed, written
    to, or opened for anything but reading.
    """
    document = get_document_by_id(document_id)      # 400 malformed, 404 unknown
    document_id = document["document_id"]
    file_name = str(document.get("file_name") or "document.pdf")

    verdict = preview_status(document)
    if not verdict["enabled"]:
        log_preview_access(document_id, file_name, "refused", verdict["reason"])
        raise HTTPException(status_code=verdict["status"] or 404,
                            detail=verdict["message"])

    path = Path(str(document["file_path"])).resolve()
    # Belt and braces: the gate above already proved this, but the line that
    # actually opens a file should carry its own proof next to it.
    if not inside(path, SOURCE_ARCHIVE_ROOT.resolve()) or path.suffix.lower() not in \
            PREVIEW_EXTENSIONS or not path_exists(path):
        log_preview_access(document_id, file_name, "refused", "failed_second_check")
        raise HTTPException(status_code=403, detail="This file may not be previewed.")

    if request.query_params:
        # Nothing here reads them; say so plainly rather than letting a caller
        # believe a parameter did something.
        log_preview_access(document_id, file_name, "served", "ignored_query_params")
    else:
        log_preview_access(document_id, file_name, "served", "")

    download_name = safe_download_name(file_name)
    if not download_name.lower().endswith(".pdf"):
        download_name += ".pdf"

    return FileResponse(
        path, media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{download_name}"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
            "X-Cutler-Preview": "read-only; source not copied",
        })


@app.get("/api/document/{document_id}/card")
def document_card(document_id: str):
    """The document card markdown, from 04_Document_Cards and nowhere else."""
    document = get_document_by_id(document_id)
    path = resolve_card_path(document["document_id"], document)
    if path is None:
        raise HTTPException(status_code=404,
                            detail="No document card has been generated for this document.")
    markdown = read_text_file(path)
    return {"document_id": document["document_id"],
            "path": str(path.relative_to(PROJECT_ROOT)),
            "markdown": markdown,
            "chars": len(markdown),
            "note": "A document card is a draft of a guess. Open the source before relying on it."}


def count_matches(text: str, term: str) -> int:
    """How many times the term occurs. Counted even when snippets are capped."""
    if not term:
        return 0
    return text.lower().count(term.lower())


def build_snippets(text: str, term: str, page: int = None, limit: int = MAX_SNIPPETS) -> list:
    """Small windows around each match, never the whole file.

    Each snippet carries the match split out from its surroundings, so the page
    can highlight the term without the browser having to search the text again
    (and without any HTML ever being built on the server).
    """
    snippets = []
    if not term:
        return snippets
    lowered = text.lower()
    needle = term.lower()
    start = 0
    window_end = -1
    while len(snippets) < max(1, int(limit)):
        found = lowered.find(needle, start)
        if found < 0:
            break
        # Two matches a few words apart would otherwise produce two snippets
        # showing the same paragraph twice. Count the second one, do not print
        # the paragraph again.
        if snippets and found < window_end:
            snippets[-1]["matches_in_window"] += 1
            start = found + len(needle)
            continue
        left = max(0, found - SNIPPET_RADIUS)
        right = min(len(text), found + len(needle) + SNIPPET_RADIUS)
        window_end = right
        snippets.append({
            "page": page,
            "offset": found,
            "before": ("…" if left else "") + text[left:found],
            "match": text[found:found + len(needle)],
            "after": text[found + len(needle):right] + ("…" if right < len(text) else ""),
            "text": ("…" if left else "") + text[left:right] + ("…" if right < len(text) else ""),
            "match_at": found - left + (1 if left else 0),
            "match_length": len(needle),
            "matches_in_window": 1,
        })
        start = found + len(needle)
    return snippets


def page_files(document_id: str, document: dict = None) -> list:
    """The per-page text files, in page order, as (page_number, path)."""
    folder = resolve_extracted_text_dir(document_id, document)
    if folder is None or not (folder / "pages").is_dir():
        return []
    found = []
    for path in sorted((folder / "pages").glob("page_*.txt")):
        try:
            found.append((int(path.stem.split("_")[-1]), path))
        except ValueError:
            continue
    return sorted(found)


def search_extracted_text(document_id: str, document: dict, term: str) -> dict:
    """Find a term in this document's extracted text.

    Searches the per-page files when they exist, because then every snippet can
    say which page it came from. Falls back to the whole-document file. Either
    way the response is snippets, never the document.
    """
    pages = page_files(document_id, document)
    snippets = []
    match_count = 0
    total_chars = 0

    if pages and len(pages) <= PAGE_SEARCH_MAX_PAGES:
        for number, path in pages:
            text = read_text_file(path)
            total_chars += len(text)
            match_count += count_matches(text, term)
            if len(snippets) < MAX_SNIPPETS:
                snippets.extend(build_snippets(text, term, page=number,
                                               limit=MAX_SNIPPETS - len(snippets)))
        return {"snippets": snippets, "match_count": match_count,
                "total_chars": total_chars, "searched": "pages",
                "pages_searched": len(pages)}

    path = resolve_extracted_text_path(document_id, document)
    if path is None:
        return None
    whole = read_text_file(path)
    return {"snippets": build_snippets(whole, term), "match_count": count_matches(whole, term),
            "total_chars": len(whole), "searched": "document", "pages_searched": 0}


@app.get("/api/document/{document_id}/text")
def document_text(document_id: str, page: int = 0, q: str = "", search: str = "",
                  max_chars: int = TEXT_PREVIEW_DEFAULT):
    """Extracted text: a preview, one page, or snippets around a search term.

    Never the whole file when it is large. A preview that quietly returns eight
    megabytes is not a preview, and the response always says how much was
    returned and whether it was cut.

    The search term is taken from either `search=` or `q=`. Two spellings for
    one idea is a trap - a caller that guesses wrong gets the whole document
    back and never learns that its search was ignored - so both are accepted
    and the response always names the mode it is answering in.
    """
    document = get_document_by_id(document_id)
    document_id = document["document_id"]
    max_chars = max(500, min(int(max_chars or TEXT_PREVIEW_DEFAULT), TEXT_PREVIEW_MAX))
    term = clean_text(search or q, MAX_FILTER_CHARS)

    page_count = len(page_files(document_id, document))

    payload = {"document_id": document_id, "page_count": page_count, "truncated": False,
               "query": term,
               "note": "Extracted text only. The original PDF is not opened by this phase."}

    # --- one page -----------------------------------------------------------
    if page:
        page_number = max(1, min(int(page), 10_000))
        path = resolve_page_path(document_id, page_number, document)
        if path is None:
            raise HTTPException(status_code=404, detail=f"No extracted text for page {page_number}.")
        text = read_text_file(path)
        payload.update({"mode": "page", "page": page_number,
                        "text": text[:TEXT_PREVIEW_MAX],
                        "total_chars": len(text),
                        "chars_returned": min(len(text), TEXT_PREVIEW_MAX),
                        "truncated": len(text) > TEXT_PREVIEW_MAX,
                        "message": f"Page {page_number} of {page_count or '?'}, as extracted."})
        return payload

    # --- search -------------------------------------------------------------
    if term:
        result = search_extracted_text(document_id, document, term)
        if result is None:
            raise HTTPException(
                status_code=404,
                detail="No extracted text on disk for this document, so there is nothing to "
                       "search. It may never have been extracted, or the extraction may have "
                       "failed.")
        snippets = result["snippets"]
        payload.update({
            "mode": "search",
            "snippets": snippets,
            "match_count": result["match_count"],
            "matches": result["match_count"],      # older name, kept deliberately
            "snippet_count": len(snippets),
            "more_matches": result["match_count"] > len(snippets),
            "searched": result["searched"],
            "total_chars": result["total_chars"],
            "chars_returned": sum(len(s["text"]) for s in snippets),
            "truncated": True,
            "message": (
                f"No match for \"{term}\" in the extracted text of this document."
                if not result["match_count"] else
                f"{result['match_count']} match(es) for \"{term}\" in "
                f"{len(snippets)} passage(s)."),
        })
        return payload

    # --- preview ------------------------------------------------------------
    path = resolve_extracted_text_path(document_id, document)
    if path is None:
        raise HTTPException(
            status_code=404,
            detail="No extracted text on disk for this document. It may never have been "
                   "extracted, or the extraction may have failed.")
    whole = read_text_file(path)

    payload.update({
        "mode": "document_preview",
        "text": whole[:max_chars],
        "total_chars": len(whole),
        "chars_returned": min(len(whole), max_chars),
        "truncated": len(whole) > max_chars,
        "message": ("Showing the first {:,} of {:,} characters.".format(
            min(len(whole), max_chars), len(whole))
            if len(whole) > max_chars else "The whole extracted text."),
    })
    return payload


@app.get("/api/document/{document_id}/pages")
def document_pages(document_id: str, page: int = 0):
    """Which pages were extracted, and how much text each one holds.

    With `page=N` this returns that page's text instead, identical to
    `/text?page=N`. The page list is the primary answer here; the redirect
    exists so a caller that guesses the other spelling gets the text it asked
    for rather than a list it did not.
    """
    document = get_document_by_id(document_id)
    document_id = document["document_id"]

    if page:
        return document_text(document_id, page=page)

    pages = []
    for number, path in page_files(document_id, document):
        try:
            char_count = path.stat().st_size
        except OSError:
            char_count = 0
        pages.append({"page": number, "path_exists": True, "char_count": char_count,
                      "text_url": f"/api/document/{document_id}/text?page={number}"})

    if not pages:
        return {"document_id": document_id, "page_count": 0, "pages": [],
                "note": "No per-page text on disk for this document."}

    return {"document_id": document_id, "page_count": len(pages), "pages": pages,
            "note": "Character counts are file sizes on disk, close enough for a page list. "
                    "Open a page to read its extracted text."}


@app.get("/api/document/{document_id}/chunks")
def document_chunks(document_id: str, limit: int = 50):
    """The RAG chunks for this document - what Ask Brain can actually retrieve.

    Previews only. The full text of every chunk is the document again, and
    returning it here would make a chunk list a second way to dump a file.
    """
    document = get_document_by_id(document_id)
    document_id = document["document_id"]
    limit = max(1, min(int(limit or 50), 200))

    conn = connect()
    try:
        if not table_exists(conn, "rag_chunks"):
            return {"document_id": document_id, "chunks": [], "total": 0,
                    "note": "No rag_chunks table - Phase 5A has not run."}
        have = columns_of(conn, "rag_chunks")
        total = conn.execute("SELECT COUNT(*) FROM rag_chunks WHERE document_id = ?",
                             (document_id,)).fetchone()[0]
        wanted = [c for c in ("chunk_id", "chunk_index", "page_start_guess", "page_end_guess",
                              "chunk_token_count", "chunk_char_count", "embedding_status",
                              "embedded_at", "chunk_version", "chunk_text", "ticker")
                  if c in have]
        rows = conn.execute(
            f"SELECT {','.join(wanted)} FROM rag_chunks WHERE document_id = ? "
            f"ORDER BY chunk_index LIMIT ?", (document_id, limit)).fetchall()
    finally:
        conn.close()

    chunks = []
    for record in rows:
        row = row_to_dict(record)
        text = str(row.pop("chunk_text", "") or "")
        status = str(row.get("embedding_status") or "")
        chunks.append({
            **row,
            "is_superseded": status == "superseded",
            "retrievable": status == "embedded",
            "text_preview": text[:CHUNK_PREVIEW_CHARS],
            "preview_truncated": len(text) > CHUNK_PREVIEW_CHARS,
            "chars": len(text),
        })

    return {"document_id": document_id, "chunks": chunks, "total": int(total or 0),
            "shown": len(chunks), "limit": limit,
            "note": f"First {CHUNK_PREVIEW_CHARS} characters of each chunk. A superseded chunk "
                    "is kept for the record and cannot be retrieved."}


@app.get("/api/document/{document_id}/source-path")
def document_source_path(document_id: str):
    """The path to the original, as text to copy. Nothing is opened.

    Deliberately not a link. A file:// link that a browser may or may not honour,
    on a path that may or may not be mounted, is a worse experience than a path
    a person pastes into Explorer themselves - and it keeps this phase's promise
    that the archive is only ever read from, by a person, outside this app.
    """
    document = get_document_by_id(document_id)
    availability = source_availability(document)
    return {
        "document_id": document["document_id"],
        "source_path": availability["source_path"],
        "relative_path": availability["relative_path"],
        "exists": availability["exists"],
        "marked_missing": availability["marked_missing"],
        "warning": availability["warning"],
        "message": "Copy this path into File Explorer to open the source file.",
    }


@app.on_event("startup")
def announce() -> None:
    print("=" * 74)
    print(f"  Cutler Research Brain - MSFT Ask Brain demo UI ({PHASE})")
    print(f"  Project root : {PROJECT_ROOT}")
    print(f"  Ask Brain    : {ASK_BRAIN} ({ASK_BRAIN_VERSION})")
    print("  Local only, no authentication. Do not expose this beyond 127.0.0.1.")
    print("  Answers are drafts assembled from retrieved text. Not verified, not advice.")
    print("  AI drafts. Humans verify. Cutler decides.")
    print("=" * 74)
