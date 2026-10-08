#!/usr/bin/env python3
# =============================================================================
# Cutler Research Brain - Phase 5A
# build_rag_index.py
#
# Chunks the already-extracted text of ONE ticker's documents and embeds those
# chunks into a local Chroma index. MSFT first, deliberately small.
#
# WHAT THIS PHASE DOES:
#   1. Selects RAG-eligible documents for one ticker from the Phase 1-3 metadata
#   2. Reads their document_text.txt from 03_Extracted_Text (never a PDF)
#   3. Splits the text into overlapping token windows, keeping page numbers
#      where Phase 2's page markers make that possible
#   4. Stores every chunk in SQLite (rag_chunks) and as JSONL
#   5. Embeds the chunks with the OpenAI EMBEDDINGS endpoint, in batches
#   6. Writes the vectors into a persistent Chroma collection with metadata
#   7. Records the run in embedding_runs and writes four reports
#
# WHAT THIS PHASE DOES NOT DO - deliberately:
#   - no chat, completions or responses calls; EMBEDDINGS is the only endpoint
#     this project touches, and no answer or summary is ever generated
#   - no summarizing, paraphrasing or interpreting of any document
#   - no thesis, sentiment, rating, recommendation, catalyst or investment view
#   - no Claude API, no other external service
#   - no OCR, no source PDFs opened, nothing written to the V Drive
#   - no full-archive index; one ticker at a time until you say otherwise
#
# The API key is read from .env with python-dotenv. It is never printed, never
# logged, and never written to a report.
#
# Principle: AI drafts. Humans verify. Cutler decides.
#
# Usage:
#   python scripts\build_rag_index.py --db "data\document_metadata.sqlite" ^
#       --project-root "C:\Users\AI Admin\Documents\CutlerResearchBrain" ^
#       --ticker MSFT --limit-docs 10 --dry-run
# =============================================================================

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import re
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ticker_selection                        # noqa: E402 - sibling module

PIPELINE_VERSION = "phase5a-1.0"

DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"
DEFAULT_COST_PER_1M_TOKENS = 0.02      # USD, conservative; override in .env
DEFAULT_CHUNK_SIZE = 900               # tokens
DEFAULT_CHUNK_OVERLAP = 150            # tokens
DEFAULT_BATCH_SIZE = 64
MAX_RETRIES = 4
RETRY_BASE_SECONDS = 2.0
FALLBACK_CHARS_PER_TOKEN = 4           # only used when tiktoken is unavailable

RAG_CHUNK_COLUMNS = [
    "chunk_id", "document_id", "ticker", "chunk_index", "chunk_text",
    "chunk_char_count", "chunk_token_count", "page_start_guess", "page_end_guess",
    "file_name", "relative_path", "archive_year", "detected_year", "detected_month",
    "folder_date_guess", "source_type_guess", "document_type_guess",
    "strategy_theme_guess", "source_vendor_candidates", "embedding_model",
    "embedding_status", "embedded_at", "chroma_collection", "created_at",
]

INTEGER_CHUNK_COLUMNS = {"chunk_index", "chunk_char_count", "chunk_token_count",
                         "page_start_guess", "page_end_guess"}

STATUS_PENDING = "pending"
STATUS_EMBEDDED = "embedded"
STATUS_FAILED = "failed"
STATUS_DRY_RUN = "dry_run"

LOGGER = logging.getLogger("phase5a_rag_index")

RE_PAGE_MARKER = re.compile(r"^=====\s*\[\s*page\s+(\d+)\s*\]\s*=====\s*$", re.IGNORECASE)
RE_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
RE_SPACES = re.compile(r"[ \t\u00a0]+")
RE_BLANK_RUN = re.compile(r"\n{3,}")


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


SAFE_TICKER = re.compile(r"^[A-Za-z0-9._-]{1,15}$")


def safe_ticker(value: str) -> str:
    ticker = (value or "").strip().upper()
    if not SAFE_TICKER.match(ticker):
        raise SystemExit(f"ERROR: --ticker must be a short alphanumeric symbol, got: {value!r}")
    return ticker


# --------------------------------------------------------------------------
# Environment / OpenAI client
# --------------------------------------------------------------------------

def load_environment(project_root: Path) -> dict:
    """Read .env. The API key is returned but NEVER logged or printed."""
    try:
        from dotenv import load_dotenv  # noqa: PLC0415
        env_path = project_root / ".env"
        if env_path.exists():
            load_dotenv(env_path)
            LOGGER.info("Loaded environment from %s", env_path.name)
        else:
            load_dotenv()
            LOGGER.warning("No .env found in %s - falling back to the process environment.",
                           project_root)
    except ImportError:
        LOGGER.warning("python-dotenv is not installed; using the process environment only.")

    settings = {
        "api_key": os.environ.get("OPENAI_API_KEY", "").strip(),
        "model": os.environ.get("OPENAI_EMBEDDING_MODEL", "").strip() or DEFAULT_EMBEDDING_MODEL,
        "base_url": os.environ.get("OPENAI_BASE_URL", "").strip(),
        "timeout": float(os.environ.get("OPENAI_TIMEOUT_SECONDS", "60") or 60),
    }
    try:
        settings["cost_per_1m"] = float(
            os.environ.get("OPENAI_EMBEDDING_COST_PER_1M", "") or DEFAULT_COST_PER_1M_TOKENS)
    except ValueError:
        settings["cost_per_1m"] = DEFAULT_COST_PER_1M_TOKENS

    # Only ever report the SHAPE of the key, never any part of its value.
    LOGGER.info("Embedding model: %s", settings["model"])
    LOGGER.info("OPENAI_API_KEY: %s",
                "present" if settings["api_key"] else "MISSING")
    return settings


def make_openai_client(settings: dict):
    """Create the OpenAI client. Used for embeddings only."""
    if not settings["api_key"]:
        raise SystemExit(
            "ERROR: OPENAI_API_KEY is not set.\n"
            "  1. Copy .env.example to .env in the project root\n"
            "  2. Put your key in OPENAI_API_KEY\n"
            "  (the key is never printed or logged by these scripts)"
        )
    try:
        from openai import OpenAI  # noqa: PLC0415
    except ImportError as exc:
        raise SystemExit(
            "ERROR: the openai package is not installed.\n"
            "  pip install -r requirements.txt\n"
            f"  ({exc})"
        ) from exc

    kwargs = {"api_key": settings["api_key"], "timeout": settings["timeout"]}
    if settings["base_url"]:
        kwargs["base_url"] = settings["base_url"]
    return OpenAI(**kwargs)


# --------------------------------------------------------------------------
# Tokenisation
# --------------------------------------------------------------------------

class Tokenizer:
    """tiktoken when it is available, a character estimate when it is not.

    The fallback never pretends to be exact: token counts are marked as
    estimates in the reports so nobody reads them as billing truth.
    """

    def __init__(self, model: str):
        self.encoder = None
        self.exact = False
        try:
            import tiktoken  # noqa: PLC0415
            try:
                self.encoder = tiktoken.encoding_for_model(model)
            except Exception:  # noqa: BLE001 - unknown model name
                self.encoder = tiktoken.get_encoding("cl100k_base")
            self.exact = True
            LOGGER.info("Token counting: tiktoken (exact)")
        except Exception as exc:  # noqa: BLE001 - not installed, or no cached encoding
            LOGGER.warning("Token counting: character estimate (~%d chars/token). tiktoken "
                           "unavailable (%s)", FALLBACK_CHARS_PER_TOKEN, type(exc).__name__)

    def encode(self, text: str) -> list:
        if self.encoder is not None:
            return self.encoder.encode(text)
        # Fallback: treat whitespace-separated words as pseudo tokens.
        return text.split(" ")

    def decode(self, tokens: list) -> str:
        if self.encoder is not None:
            return self.encoder.decode(tokens)
        return " ".join(tokens)

    def count(self, text: str) -> int:
        if self.encoder is not None:
            return len(self.encoder.encode(text))
        return max(1, len(text) // FALLBACK_CHARS_PER_TOKEN)


# --------------------------------------------------------------------------
# Text handling - mechanical only, nothing is interpreted
# --------------------------------------------------------------------------

def parse_pages(raw: str) -> list[tuple]:
    """Split extracted text into (page_number, text) using Phase 2's markers.

    If the file has no markers, one pseudo page with page_number None is
    returned and page fields stay empty - page numbers are never invented.
    """
    lines = raw.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    pages: list[tuple] = []
    current_page = None
    buffer: list[str] = []

    for line in lines:
        match = RE_PAGE_MARKER.match(line.strip())
        if match:
            if buffer:
                pages.append((current_page, "\n".join(buffer)))
                buffer = []
            current_page = int(match.group(1))
            continue
        buffer.append(line)

    if buffer:
        pages.append((current_page, "\n".join(buffer)))

    if not pages:
        return [(None, "")]
    return pages


def clean_text(text: str) -> str:
    """Whitespace normalisation only. No words are changed, added or removed."""
    out = []
    for line in text.split("\n"):
        line = RE_CONTROL.sub(" ", line)
        out.append(RE_SPACES.sub(" ", line).strip())
    return RE_BLANK_RUN.sub("\n\n", "\n".join(out)).strip()


def chunk_document(raw_text: str, tokenizer: Tokenizer, chunk_size: int,
                   overlap: int) -> list[dict]:
    """Split one document into overlapping token windows.

    Page numbers ride along: each token knows which page it came from, so a
    chunk can report the first and last page it spans. When the source has no
    page markers the page fields are left empty rather than guessed.
    """
    pages = parse_pages(raw_text)
    have_pages = any(page_no is not None for page_no, _ in pages)

    tokens: list = []
    token_pages: list = []
    for page_no, page_text in pages:
        cleaned = clean_text(page_text)
        if not cleaned:
            continue
        page_tokens = tokenizer.encode(cleaned + "\n\n")
        tokens.extend(page_tokens)
        token_pages.extend([page_no] * len(page_tokens))

    if not tokens:
        return []

    step = max(1, chunk_size - overlap)
    chunks: list[dict] = []
    start = 0
    index = 0

    while start < len(tokens):
        end = min(start + chunk_size, len(tokens))
        window = tokens[start:end]
        text = tokenizer.decode(window).strip()
        if text:
            window_pages = [p for p in token_pages[start:end] if p is not None]
            chunks.append({
                "chunk_index": index,
                "chunk_text": text,
                "chunk_char_count": len(text),
                "chunk_token_count": len(window),
                "page_start_guess": min(window_pages) if (have_pages and window_pages) else None,
                "page_end_guess": max(window_pages) if (have_pages and window_pages) else None,
            })
            index += 1
        if end >= len(tokens):
            break
        start += step

    return chunks


# --------------------------------------------------------------------------
# Database
# --------------------------------------------------------------------------

def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def create_schema(conn: sqlite3.Connection) -> None:
    defs = []
    for col in RAG_CHUNK_COLUMNS:
        if col in INTEGER_CHUNK_COLUMNS:
            defs.append(f"{col} INTEGER")
        else:
            defs.append(f"{col} TEXT")
    # Phase 11.3: the key is (chunk_id, ticker), not chunk_id alone.
    #
    # chunk_id is "{document_id}_c{index:04d}" - it does not name a ticker. With
    # chunk_id as the sole primary key, a document tagged with two tickers has
    # ONE set of chunk rows, and this builder's INSERT OR REPLACE hands them to
    # whichever ticker was built last. Nothing is deleted and the row count
    # never drops, so it does not look like loss; the losing ticker simply stops
    # being able to cite the document, because ask_brain filters on `ticker`.
    #
    # This CREATE runs only for a database that has no rag_chunks table yet. An
    # existing single-key table is migrated by scripts/migrate_rag_chunk_key.py,
    # which copies every row and deletes none.
    conn.execute(f"CREATE TABLE IF NOT EXISTS rag_chunks "
                 f"({', '.join(defs)}, PRIMARY KEY (chunk_id, ticker))")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_ticker ON rag_chunks(ticker)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_document ON rag_chunks(document_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_status ON rag_chunks(embedding_status)")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS embedding_runs (
            run_id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker                      TEXT,
            embedding_model             TEXT,
            documents_selected          INTEGER,
            chunks_created              INTEGER,
            chunks_embedded             INTEGER,
            chunks_skipped              INTEGER,
            total_input_tokens_estimate INTEGER,
            openai_calls                INTEGER,
            estimated_cost_usd          REAL,
            started_at                  TEXT,
            completed_at                TEXT,
            status                      TEXT,
            error_message               TEXT
        )
        """
    )
    conn.commit()


def select_documents(conn: sqlite3.Connection, ticker: str, limit: int = 0) -> list[sqlite3.Row]:
    """RAG-eligible documents for one ticker. Every exclusion is explicit."""
    doc_cols = {row[1] for row in conn.execute("PRAGMA table_info(documents)")}
    has_cards = "document_cards" in {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}

    reason_clause = ""
    join_clause = ""
    if has_cards:
        join_clause = "LEFT JOIN document_cards c ON c.document_id = d.document_id"
        # Phase 11.2: the clause now comes from ticker_selection, so the builder
        # and the inspector cannot drift apart on it. It used to be written out
        # here as COALESCE(c.rag_exclusion_reason, 'none') = 'none', which
        # treats a BLANK reason as an exclusion while the inspector's Python
        # test treated it as no exclusion - a document that is eligible to the
        # inspector and invisible to the builder, reported as behind forever.
        reason_clause = f"AND {ticker_selection.CARD_EXCLUSION_SQL}"

    extraction_join = "LEFT JOIN pdf_extractions e ON e.document_id = d.document_id"

    text_path_expr = "COALESCE(e.document_text_path, '')"
    card_path_expr = "COALESCE(c.card_md_path, '')" if has_cards else "''"

    sql = f"""
        SELECT d.document_id,
               COALESCE(d.file_name, '')                  AS file_name,
               COALESCE(d.relative_path, '')              AS relative_path,
               COALESCE(d.archive_year, '')               AS archive_year,
               COALESCE(d.detected_year, '')              AS detected_year,
               COALESCE(d.detected_month, '')             AS detected_month,
               COALESCE(d.folder_date_guess, '')          AS folder_date_guess,
               COALESCE(d.source_type_guess, 'Unknown')   AS source_type_guess,
               COALESCE(d.document_type_guess, 'Unknown') AS document_type_guess,
               COALESCE(d.strategy_theme_guess, 'Unknown') AS strategy_theme_guess,
               COALESCE(d.source_vendor_candidates, '')   AS source_vendor_candidates,
               COALESCE(d.ticker_candidates_clean, '')    AS ticker_candidates_clean,
               COALESCE(d.pdf_extraction_status, '')      AS pdf_extraction_status,
               COALESCE(d.pdf_extraction_quality, '')     AS pdf_extraction_quality,
               COALESCE(d.duplicate_status, '')           AS duplicate_status,
               COALESCE(d.review_severity, '')            AS review_severity,
               {text_path_expr}                            AS document_text_path,
               {card_path_expr}                            AS card_md_path
          FROM documents d
          {extraction_join}
          {join_clause}
         WHERE COALESCE(d.usable_for_rag, 0) = 1
           AND UPPER(COALESCE(d.ticker_candidates_clean, '')) LIKE ?
           AND COALESCE(d.pdf_extraction_status, '') IN ('extracted', 'partial')
           AND COALESCE(d.pdf_extraction_quality, '') NOT IN ('Failed', 'Empty')
           AND COALESCE(d.duplicate_status, '') <> 'duplicate'
           AND COALESCE(d.review_severity, '') <> 'High'
           {reason_clause}
         ORDER BY d.folder_date_guess DESC, d.relative_path
    """
    if limit:
        sql += f" LIMIT {int(limit)}"

    if "usable_for_rag" not in doc_cols:
        raise SystemExit("ERROR: documents.usable_for_rag is missing. Run Phase 3 first.")

    # Phase 11. The SQL narrows by a cheap substring; the TICKER DECISION is made
    # in Python by whole-token comparison, in the one module the planner, the
    # batch runner and this builder all share.
    #
    # The old clause was (',' || clean || ',') LIKE '%,MSFT,%'. It is safe
    # against substrings but misses 'AAPL, MSFT' - the same fault Phase 9.3
    # found in the ingestion worklist. With single-letter tickers (T, B) now in
    # scope, a matching rule that is "nearly right" is not good enough: on the
    # Phase 10 fixture a substring match for T selected 11 documents of 59, and
    # for B, 12.
    rows = list(conn.execute(sql, (f"%{ticker}%",)))
    rows = [row for row in rows
            if ticker_selection.tagged_with(row["ticker_candidates_clean"], ticker)]
    return rows


def resolve_text_path(row: sqlite3.Row, project_root: Path) -> Path | None:
    recorded = (row["document_text_path"] or "").strip()
    if recorded:
        path = Path(recorded)
        if not path.is_absolute():
            path = project_root / path
        if path.exists():
            return path

    year = (row["archive_year"] or "unknown_year").strip() or "unknown_year"
    fallback = (project_root / "03_Extracted_Text" / year /
                row["document_id"] / "document_text.txt")
    return fallback if fallback.exists() else None


def upsert_chunks(conn: sqlite3.Connection, records: list[dict]) -> None:
    placeholders = ", ".join("?" for _ in RAG_CHUNK_COLUMNS)
    conn.executemany(
        f"INSERT OR REPLACE INTO rag_chunks ({', '.join(RAG_CHUNK_COLUMNS)}) "
        f"VALUES ({placeholders})",
        [tuple(rec.get(col) for col in RAG_CHUNK_COLUMNS) for rec in records],
    )
    conn.commit()


# --------------------------------------------------------------------------
# Chroma
# --------------------------------------------------------------------------

def collection_name_for(ticker: str) -> str:
    return f"cutler_research_brain_{ticker.lower()}_2026"


def open_chroma(project_root: Path, ticker: str, collection: str):
    """Persistent local Chroma client. Nothing leaves the project folder."""
    try:
        import chromadb  # noqa: PLC0415
    except ImportError as exc:
        raise SystemExit(
            "ERROR: chromadb is not installed.\n"
            "  pip install -r requirements.txt\n"
            f"  ({exc})"
        ) from exc

    path = project_root / "06_RAG_Index" / "chroma" / ticker
    assert_inside_project(path, project_root)
    path.mkdir(parents=True, exist_ok=True)

    client = chromadb.PersistentClient(path=str(path))
    return client.get_or_create_collection(
        name=collection, metadata={"hnsw:space": "cosine"})


# --------------------------------------------------------------------------
# Embedding
# --------------------------------------------------------------------------

def embed_batch(client, model: str, texts: list[str]) -> list[list]:
    """One embeddings call, with retries. EMBEDDINGS ONLY - never chat."""
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = client.embeddings.create(model=model, input=texts)
            return [item.embedding for item in response.data]
        except Exception as exc:  # noqa: BLE001 - rate limit, network, server error
            last_error = exc
            wait = RETRY_BASE_SECONDS * (2 ** (attempt - 1))
            name = type(exc).__name__
            if attempt < MAX_RETRIES:
                LOGGER.warning("Embedding attempt %d/%d failed (%s). Retrying in %.0fs.",
                               attempt, MAX_RETRIES, name, wait)
                time.sleep(wait)
            else:
                LOGGER.error("Embedding failed after %d attempts (%s).", MAX_RETRIES, name)
    raise RuntimeError(f"embedding batch failed: {type(last_error).__name__}: {last_error}")


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


def _md_table(header: list[str], rows: list[tuple]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join(["---"] * len(header)) + "|"]
    for row in rows:
        lines.append("| " + " | ".join("" if v is None else str(v) for v in row) + " |")
    if not rows:
        lines.append("| " + " | ".join(["(none)"] * len(header)) + " |")
    return "\n".join(lines)


def write_reports(conn: sqlite3.Connection, project_root: Path, ticker: str,
                  stats: dict, settings: dict, collection: str) -> None:
    reports_dir = project_root / "06_RAG_Index" / "reports"
    index_dir = project_root / "02_Metadata_Index"
    for directory in (reports_dir, index_dir):
        assert_inside_project(directory, project_root)
        directory.mkdir(parents=True, exist_ok=True)

    manifest_columns = [
        "chunk_id", "document_id", "chunk_index", "chunk_token_count", "chunk_char_count",
        "page_start_guess", "page_end_guess", "embedding_status", "embedded_at",
        "file_name", "relative_path", "folder_date_guess", "archive_year",
        "source_type_guess", "document_type_guess", "strategy_theme_guess",
        "source_vendor_candidates", "embedding_model", "chroma_collection",
    ]
    rows = [tuple(r) for r in conn.execute(
        f"SELECT {', '.join(manifest_columns)} FROM rag_chunks WHERE ticker = ? "
        f"ORDER BY document_id, chunk_index", (ticker,))]
    write_csv(reports_dir / f"{ticker}_chunk_manifest.csv", manifest_columns, rows)

    run_columns = ["run_id", "ticker", "embedding_model", "documents_selected", "chunks_created",
                   "chunks_embedded", "chunks_skipped", "total_input_tokens_estimate",
                   "openai_calls", "estimated_cost_usd", "started_at", "completed_at",
                   "status", "error_message"]
    write_csv(reports_dir / f"{ticker}_embedding_run_report.csv", run_columns,
              [tuple(r) for r in conn.execute(
                  f"SELECT {', '.join(run_columns)} FROM embedding_runs WHERE ticker = ? "
                  f"ORDER BY run_id DESC", (ticker,))])

    failure_columns = ["chunk_id", "document_id", "chunk_index", "file_name",
                       "chunk_token_count", "embedding_status", "embedding_model"]
    write_csv(reports_dir / f"{ticker}_embedding_failures.csv", failure_columns,
              [tuple(r) for r in conn.execute(
                  f"SELECT {', '.join(failure_columns)} FROM rag_chunks "
                  f"WHERE ticker = ? AND embedding_status = ? ORDER BY document_id, chunk_index",
                  (ticker, STATUS_FAILED))])

    summary = build_summary(conn, ticker, stats, settings, collection)
    for path in (reports_dir / f"{ticker}_rag_index_summary.md",
                 index_dir / f"{ticker}_rag_index_summary.md"):
        path.write_text(summary, encoding="utf-8")
        LOGGER.info("Wrote %s", path)


def build_summary(conn: sqlite3.Connection, ticker: str, stats: dict,
                  settings: dict, collection: str) -> str:
    def scalar(sql: str, params: tuple = ()) -> int:
        return int(conn.execute(sql, params).fetchone()[0] or 0)

    total_chunks = scalar("SELECT COUNT(*) FROM rag_chunks WHERE ticker = ?", (ticker,))
    embedded = scalar("SELECT COUNT(*) FROM rag_chunks WHERE ticker = ? AND embedding_status = ?",
                      (ticker, STATUS_EMBEDDED))
    failed = scalar("SELECT COUNT(*) FROM rag_chunks WHERE ticker = ? AND embedding_status = ?",
                    (ticker, STATUS_FAILED))
    pending = scalar("SELECT COUNT(*) FROM rag_chunks WHERE ticker = ? AND embedding_status IN (?, ?)",
                     (ticker, STATUS_PENDING, STATUS_DRY_RUN))
    documents = scalar("SELECT COUNT(DISTINCT document_id) FROM rag_chunks WHERE ticker = ?",
                       (ticker,))
    tokens = scalar("SELECT COALESCE(SUM(chunk_token_count), 0) FROM rag_chunks WHERE ticker = ?",
                    (ticker,))

    by_source = [tuple(r) for r in conn.execute(
        "SELECT source_type_guess, COUNT(*) FROM rag_chunks WHERE ticker = ? "
        "GROUP BY 1 ORDER BY COUNT(*) DESC", (ticker,))]
    by_doc_type = [tuple(r) for r in conn.execute(
        "SELECT document_type_guess, COUNT(*) FROM rag_chunks WHERE ticker = ? "
        "GROUP BY 1 ORDER BY COUNT(*) DESC", (ticker,))]
    by_date = [tuple(r) for r in conn.execute(
        "SELECT CASE WHEN COALESCE(folder_date_guess,'') = '' THEN '(no date)' "
        "ELSE folder_date_guess END, COUNT(DISTINCT document_id), COUNT(*) "
        "FROM rag_chunks WHERE ticker = ? GROUP BY 1 ORDER BY 1 DESC LIMIT 25", (ticker,))]
    largest = [tuple(r) for r in conn.execute(
        "SELECT file_name, COUNT(*), SUM(chunk_token_count) FROM rag_chunks WHERE ticker = ? "
        "GROUP BY document_id ORDER BY COUNT(*) DESC LIMIT 15", (ticker,))]

    generated = datetime.now().replace(microsecond=0).isoformat()
    mode = "DRY RUN - no embeddings were created" if stats.get("dry_run") else "embedding run"

    lines = [
        f"# Phase 5A - {ticker} RAG Index Summary",
        "",
        f"_Generated: {generated} ({mode})_",
        "",
        "## In one line",
        "",
        f"**{documents:,} {ticker} documents** produced **{total_chunks:,} chunks**, "
        f"**{embedded:,}** of them embedded into the local Chroma collection "
        f"`{collection}`. Retrieval is prepared; **no question has been answered and no "
        "document has been summarized.**",
        "",
        "## What this phase did and did not do",
        "",
        "- Read only the already-extracted text in `03_Extracted_Text`. No PDF was opened.",
        "- Called the OpenAI **embeddings** endpoint and nothing else. No chat, completions or",
        "  responses call was made, so no text was generated, summarized or interpreted.",
        "- Wrote vectors to a **local** Chroma store inside the project folder.",
        "- No thesis, sentiment, rating, recommendation, catalyst or investment view was",
        "  produced or inferred.",
        "- The API key was read from `.env` and never printed, logged or written to a report.",
        "- The V Drive was not touched.",
        "",
        "## Run",
        "",
        "| Measure | Value |",
        "|---|---|",
        f"| Ticker | {ticker} |",
        f"| Embedding model | `{settings['model']}` |",
        f"| Chroma collection | `{collection}` |",
        f"| Documents selected | {stats.get('documents_selected', 0):,} |",
        f"| Documents chunked | {documents:,} |",
        f"| Chunks created | {total_chunks:,} |",
        f"| Chunks embedded | {embedded:,} |",
        f"| Chunks skipped (already embedded) | {stats.get('chunks_skipped', 0):,} |",
        f"| Chunks pending / dry run | {pending:,} |",
        f"| Chunks failed | {failed:,} |",
        f"| Token count ({'exact' if stats.get('exact_tokens') else 'estimated'}) | {tokens:,} |",
        f"| OpenAI embedding calls | {stats.get('openai_calls', 0):,} |",
        f"| Estimated cost (USD) | ${stats.get('estimated_cost_usd', 0.0):.4f} |",
        f"| Chunk size / overlap (tokens) | {stats.get('chunk_size')} / {stats.get('chunk_overlap')} |",
        "",
        f"_Cost is an estimate at ${settings['cost_per_1m']:.4f} per 1M input tokens "
        "(override with `OPENAI_EMBEDDING_COST_PER_1M`). It is not a bill._",
        "",
        "## Chunks by source type",
        "",
        _md_table(["Source type guess", "Chunks"], by_source),
        "",
        "## Chunks by document type",
        "",
        _md_table(["Document type guess", "Chunks"], by_doc_type),
        "",
        "## Date coverage",
        "",
        _md_table(["Archive date", "Documents", "Chunks"], by_date),
        "",
        "## Largest documents by chunk count",
        "",
        _md_table(["File name", "Chunks", "Tokens"], largest),
        "",
        "## What a human should check next",
        "",
        f"1. `{ticker}_embedding_failures.csv` - any chunk that could not be embedded.",
        "2. Run a retrieval-only search and read what comes back:",
        "",
        "```bat",
        f"python scripts\\search_rag_index.py --project-root \".\" --ticker {ticker} "
        "--query \"your question here\" --top-k 5",
        "```",
        "",
        "3. Confirm the retrieved chunks actually belong to the documents you expect before",
        "   anything is built on top of this index.",
        "",
        "**AI drafts. Humans verify. Cutler decides.**",
        "",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Cutler Research Brain Phase 5A - chunk and embed one ticker's documents. "
                    "Embeddings only; no answers are generated.",
    )
    parser.add_argument("--db", required=True,
                        help=r'SQLite index, e.g. "data\document_metadata.sqlite"')
    parser.add_argument("--project-root", required=True,
                        help=r'Project root, e.g. "C:\Users\AI Admin\Documents\CutlerResearchBrain"')
    parser.add_argument("--ticker", default="MSFT", help="Ticker to index (default MSFT).")
    parser.add_argument("--limit-docs", type=int, default=0,
                        help="Only process the first N documents (0 = all).")
    parser.add_argument("--limit-chunks", type=int, default=0,
                        help="Stop after N chunks have been prepared (0 = no limit).")
    parser.add_argument("--force", action="store_true",
                        help="Re-chunk and re-embed even if chunks are already embedded.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Select, chunk and estimate cost, but make NO OpenAI call and "
                             "write no vectors.")
    parser.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE,
                        help=f"Chunk size in tokens (default {DEFAULT_CHUNK_SIZE}).")
    parser.add_argument("--chunk-overlap", type=int, default=DEFAULT_CHUNK_OVERLAP,
                        help=f"Chunk overlap in tokens (default {DEFAULT_CHUNK_OVERLAP}).")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE,
                        help=f"Embedding batch size (default {DEFAULT_BATCH_SIZE}).")
    parser.add_argument("--collection", default="",
                        help="Chroma collection name (default cutler_research_brain_<ticker>_2026).")
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

    ticker = safe_ticker(args.ticker)
    if args.chunk_overlap >= args.chunk_size:
        raise SystemExit("ERROR: --chunk-overlap must be smaller than --chunk-size.")

    setup_logging(project_root / "logs" / "phase5a_rag_index.log", args.verbose)
    started_at = datetime.now().replace(microsecond=0).isoformat()
    started = time.time()

    collection_name = args.collection.strip() or collection_name_for(ticker)

    LOGGER.info("=" * 78)
    LOGGER.info("Cutler Research Brain - Phase 5A RAG index (%s)", ticker)
    LOGGER.info("Database    : %s", db_path)
    LOGGER.info("Project root: %s", project_root)
    LOGGER.info("Mode        : %s", "DRY RUN (no OpenAI calls)" if args.dry_run else "embedding run")
    LOGGER.info("Embeddings only - no chat/completions/responses call is ever made here.")
    LOGGER.info("=" * 78)

    settings = load_environment(project_root)
    tokenizer = Tokenizer(settings["model"])

    conn = connect(db_path)
    stats = {
        "documents_selected": 0, "chunks_created": 0, "chunks_embedded": 0,
        "chunks_skipped": 0, "openai_calls": 0, "estimated_cost_usd": 0.0,
        "total_tokens": 0, "dry_run": args.dry_run, "exact_tokens": tokenizer.exact,
        "chunk_size": args.chunk_size, "chunk_overlap": args.chunk_overlap,
    }
    run_status = "running"
    error_message = ""

    try:
        create_schema(conn)

        documents = select_documents(conn, ticker, args.limit_docs)
        stats["documents_selected"] = len(documents)
        LOGGER.info("Documents selected for %s: %d", ticker, len(documents))
        if not documents:
            LOGGER.warning("No RAG-eligible %s documents found. Nothing to do.", ticker)

        # --force means a clean rebuild for this ticker: chunk ids are derived
        # from the chunk index, so changing --chunk-size would otherwise leave
        # stale chunks from the previous sizing behind in the table and index.
        if args.force:
            removed = conn.execute("DELETE FROM rag_chunks WHERE ticker = ?", (ticker,)).rowcount
            conn.commit()
            if removed:
                LOGGER.info("--force: cleared %d existing %s chunk row(s).", removed, ticker)
            if not args.dry_run:
                try:
                    import chromadb  # noqa: PLC0415
                    store = project_root / "06_RAG_Index" / "chroma" / ticker
                    if store.exists():
                        client = chromadb.PersistentClient(path=str(store))
                        client.delete_collection(name=collection_name)
                        LOGGER.info("--force: dropped Chroma collection %s.", collection_name)
                except Exception as exc:  # noqa: BLE001 - nothing to drop is fine
                    LOGGER.debug("No existing Chroma collection to drop (%s).",
                                 type(exc).__name__)

        already_embedded = {
            r[0] for r in conn.execute(
                "SELECT chunk_id FROM rag_chunks WHERE ticker = ? AND embedding_status = ?",
                (ticker, STATUS_EMBEDDED))
        } if not args.force else set()

        # ---- chunk ---------------------------------------------------------
        pending: list[dict] = []
        chunks_dir = project_root / "06_RAG_Index" / "chunks"
        assert_inside_project(chunks_dir, project_root)
        chunks_dir.mkdir(parents=True, exist_ok=True)
        jsonl_path = chunks_dir / f"{ticker}_chunks.jsonl"

        now = datetime.now().replace(microsecond=0).isoformat()
        with jsonl_path.open("w", encoding="utf-8") as jsonl:
            for row in documents:
                text_path = resolve_text_path(row, project_root)
                if text_path is None:
                    LOGGER.warning("No extracted text on disk, skipping: %s",
                                   row["relative_path"] or row["document_id"])
                    continue
                try:
                    raw = text_path.read_text(encoding="utf-8", errors="replace")
                    doc_chunks = chunk_document(raw, tokenizer, args.chunk_size,
                                                args.chunk_overlap)
                except Exception as exc:  # noqa: BLE001 - one bad file must not stop the run
                    LOGGER.error("Chunking FAILED for %s: %s", row["document_id"], exc)
                    continue

                if not doc_chunks:
                    LOGGER.warning("No usable text after cleaning: %s", row["document_id"])
                    continue

                for chunk in doc_chunks:
                    chunk_id = f"{row['document_id']}_c{chunk['chunk_index']:04d}"
                    record = {
                        "chunk_id": chunk_id,
                        "document_id": row["document_id"],
                        "ticker": ticker,
                        "chunk_index": chunk["chunk_index"],
                        "chunk_text": chunk["chunk_text"],
                        "chunk_char_count": chunk["chunk_char_count"],
                        "chunk_token_count": chunk["chunk_token_count"],
                        "page_start_guess": chunk["page_start_guess"],
                        "page_end_guess": chunk["page_end_guess"],
                        "file_name": row["file_name"],
                        "relative_path": row["relative_path"],
                        "archive_year": row["archive_year"],
                        "detected_year": row["detected_year"],
                        "detected_month": row["detected_month"],
                        "folder_date_guess": row["folder_date_guess"],
                        "source_type_guess": row["source_type_guess"],
                        "document_type_guess": row["document_type_guess"],
                        "strategy_theme_guess": row["strategy_theme_guess"],
                        "source_vendor_candidates": row["source_vendor_candidates"],
                        "embedding_model": settings["model"],
                        "embedding_status": STATUS_DRY_RUN if args.dry_run else STATUS_PENDING,
                        "embedded_at": "",
                        "chroma_collection": collection_name,
                        "created_at": now,
                    }
                    jsonl.write(json.dumps(record, ensure_ascii=False) + "\n")

                    stats["chunks_created"] += 1
                    stats["total_tokens"] += chunk["chunk_token_count"]

                    if chunk_id in already_embedded:
                        stats["chunks_skipped"] += 1
                        continue
                    pending.append(record)

                    if args.limit_chunks and stats["chunks_created"] >= args.limit_chunks:
                        break
                if args.limit_chunks and stats["chunks_created"] >= args.limit_chunks:
                    LOGGER.info("Reached --limit-chunks of %d.", args.limit_chunks)
                    break

        LOGGER.info("Chunks created: %d (%d already embedded and skipped)",
                    stats["chunks_created"], stats["chunks_skipped"])

        # Warn about rows left from a previous run with different chunk settings.
        stored = int(conn.execute("SELECT COUNT(*) FROM rag_chunks WHERE ticker = ?",
                                  (ticker,)).fetchone()[0] or 0)
        if stored > stats["chunks_created"] and not args.force:
            LOGGER.warning("The table holds %d %s chunk(s) but this run produced %d. Rows from an "
                           "earlier run with different --chunk-size settings are still there; "
                           "re-run with --force for a clean rebuild.",
                           stored, ticker, stats["chunks_created"])
        LOGGER.info("Chunk JSONL: %s", jsonl_path)

        # ---- cost estimate, printed BEFORE anything is sent -----------------
        estimate = stats["total_tokens"] / 1_000_000 * settings["cost_per_1m"]
        stats["estimated_cost_usd"] = round(estimate, 6)
        LOGGER.info("-" * 78)
        LOGGER.info("Token count %s : %s",
                    "(exact, tiktoken)" if tokenizer.exact else "(ESTIMATE, no tiktoken)",
                    f"{stats['total_tokens']:,}")
        LOGGER.info("Chunks to embed     : %s", f"{len(pending):,}")
        LOGGER.info("Batches at size %-3d : %s", args.batch_size,
                    f"{(len(pending) + args.batch_size - 1) // args.batch_size:,}")
        LOGGER.info("ESTIMATED COST      : $%.4f  (at $%.4f per 1M tokens)",
                    estimate, settings["cost_per_1m"])
        LOGGER.info("-" * 78)

        if pending:
            upsert_chunks(conn, pending)

        if args.dry_run:
            LOGGER.info("DRY RUN: no OpenAI call was made and no vector was written.")
            LOGGER.info("Re-run without --dry-run to embed these chunks.")
            run_status = "dry_run"
        elif not pending:
            LOGGER.info("Nothing to embed - every chunk is already embedded.")
            run_status = "completed"
        else:
            client = make_openai_client(settings)
            collection = open_chroma(project_root, ticker, collection_name)

            for start in range(0, len(pending), args.batch_size):
                batch = pending[start:start + args.batch_size]
                ids = [rec["chunk_id"] for rec in batch]
                texts = [rec["chunk_text"] for rec in batch]
                number = start // args.batch_size + 1

                try:
                    vectors = embed_batch(client, settings["model"], texts)
                    stats["openai_calls"] += 1
                except Exception as exc:  # noqa: BLE001 - one bad batch must not stop the run
                    LOGGER.error("Batch %d FAILED (%d chunk(s)): %s", number, len(batch), exc)
                    LOGGER.error("  failed chunk ids: %s", ", ".join(ids[:10])
                                 + (" ..." if len(ids) > 10 else ""))
                    # Phase 12: scoped by ticker. Since Phase 11.3 the same
                    # chunk_id can exist under two tickers, so an unscoped
                    # UPDATE would mark ANOTHER ticker's chunk failed.
                    conn.executemany(
                        "UPDATE rag_chunks SET embedding_status = ? "
                        " WHERE chunk_id = ? AND ticker = ?",
                        [(STATUS_FAILED, chunk_id, ticker) for chunk_id in ids])
                    conn.commit()
                    continue

                try:
                    collection.upsert(
                        ids=ids,
                        embeddings=vectors,
                        documents=texts,
                        metadatas=[{
                            "ticker": ticker,
                            "document_id": rec["document_id"],
                            "chunk_index": rec["chunk_index"],
                            "file_name": rec["file_name"],
                            "relative_path": rec["relative_path"],
                            "folder_date_guess": rec["folder_date_guess"],
                            "archive_year": rec["archive_year"],
                            "source_type_guess": rec["source_type_guess"],
                            "document_type_guess": rec["document_type_guess"],
                            "strategy_theme_guess": rec["strategy_theme_guess"],
                            "page_start_guess": rec["page_start_guess"] or -1,
                            "page_end_guess": rec["page_end_guess"] or -1,
                            "embedding_model": settings["model"],
                        } for rec in batch],
                    )
                except Exception as exc:  # noqa: BLE001
                    LOGGER.error("Chroma write FAILED for batch %d: %s", number, exc)
                    # Phase 12: scoped by ticker. Since Phase 11.3 the same
                    # chunk_id can exist under two tickers, so an unscoped
                    # UPDATE would mark ANOTHER ticker's chunk failed.
                    conn.executemany(
                        "UPDATE rag_chunks SET embedding_status = ? "
                        " WHERE chunk_id = ? AND ticker = ?",
                        [(STATUS_FAILED, chunk_id, ticker) for chunk_id in ids])
                    conn.commit()
                    continue

                embedded_at = datetime.now().replace(microsecond=0).isoformat()
                conn.executemany(
                    "UPDATE rag_chunks SET embedding_status = ?, embedded_at = ? "
                    " WHERE chunk_id = ? AND ticker = ?",
                    [(STATUS_EMBEDDED, embedded_at, chunk_id, ticker) for chunk_id in ids])
                conn.commit()
                stats["chunks_embedded"] += len(batch)
                LOGGER.info("Batch %d: embedded %d chunk(s)  [%d/%d]",
                            number, len(batch), stats["chunks_embedded"], len(pending))

            # Be honest about partial success: a run where batches failed is
            # not "completed", and one where nothing embedded is a failure.
            failed_now = int(conn.execute(
                "SELECT COUNT(*) FROM rag_chunks WHERE ticker = ? AND embedding_status = ?",
                (ticker, STATUS_FAILED)).fetchone()[0] or 0)
            if failed_now and stats["chunks_embedded"]:
                run_status = "completed_with_failures"
                error_message = f"{failed_now} chunk(s) failed to embed"
            elif failed_now:
                run_status = "failed"
                error_message = f"all {failed_now} chunk(s) failed to embed"
            else:
                run_status = "completed"

        conn.execute(
            """
            INSERT INTO embedding_runs
                (ticker, embedding_model, documents_selected, chunks_created, chunks_embedded,
                 chunks_skipped, total_input_tokens_estimate, openai_calls, estimated_cost_usd,
                 started_at, completed_at, status, error_message)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (ticker, settings["model"], stats["documents_selected"], stats["chunks_created"],
             stats["chunks_embedded"], stats["chunks_skipped"], stats["total_tokens"],
             stats["openai_calls"], stats["estimated_cost_usd"], started_at,
             datetime.now().replace(microsecond=0).isoformat(), run_status, error_message),
        )
        conn.commit()

        write_reports(conn, project_root, ticker, stats, settings, collection_name)

        LOGGER.info("-" * 78)
        LOGGER.info("Documents selected : %d", stats["documents_selected"])
        LOGGER.info("Chunks created     : %d", stats["chunks_created"])
        LOGGER.info("Chunks embedded    : %d", stats["chunks_embedded"])
        LOGGER.info("Chunks skipped     : %d", stats["chunks_skipped"])
        LOGGER.info("OpenAI calls       : %d  (embeddings endpoint only)", stats["openai_calls"])
        LOGGER.info("Estimated cost     : $%.4f", stats["estimated_cost_usd"])
        LOGGER.info("Chroma collection  : %s", collection_name)
        LOGGER.info("Runtime            : %.1f seconds", time.time() - started)
        LOGGER.info("-" * 78)
        LOGGER.info("Retrieval is prepared. No question was answered and nothing was summarized.")
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - record the failure, then re-raise
        error_message = f"{type(exc).__name__}: {exc}"
        LOGGER.error("Run FAILED: %s", error_message)
        try:
            conn.execute(
                "INSERT INTO embedding_runs (ticker, embedding_model, documents_selected, "
                "chunks_created, chunks_embedded, chunks_skipped, total_input_tokens_estimate, "
                "openai_calls, estimated_cost_usd, started_at, completed_at, status, "
                "error_message) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (ticker, settings["model"], stats["documents_selected"], stats["chunks_created"],
                 stats["chunks_embedded"], stats["chunks_skipped"], stats["total_tokens"],
                 stats["openai_calls"], stats["estimated_cost_usd"], started_at,
                 datetime.now().replace(microsecond=0).isoformat(), "failed", error_message))
            conn.commit()
        except sqlite3.Error:
            pass
        raise
    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
