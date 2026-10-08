#!/usr/bin/env python3
# =============================================================================
# Cutler Research Brain - Phase 6
# build_keyword_index.py
#
# Builds a local keyword (BM25) index over the chunks Phase 5A already embedded,
# so Ask Brain can find text by the WORDS IN IT and not only by embedding
# similarity. Vector search is good at meaning and bad at exact strings: a
# filing line like "Azure and other cloud services revenue grew" can lose to a
# paraphrase that happens to sit closer in vector space. Keyword search is the
# opposite. Phase 6 uses both.
#
# WHAT THIS SCRIPT DOES NOT DO
#   - It does not open a source PDF. Text comes from rag_chunks, which Phase 5A
#     filled from 03_Extracted_Text.
#   - It does not embed or re-embed anything. No OpenAI call is made here at
#     all - this script has no network access of any kind.
#   - It does not touch the Chroma store.
#   - It does not modify rag_chunks. It only reads.
#   - It does not touch the V Drive.
#
# Everything it produces is a count of words. There is no interpretation, no
# summary, and no investment meaning anywhere in this file.
#
# Usage:
#   py -3.11 scripts\build_keyword_index.py ^
#       --db "data\document_metadata.sqlite" ^
#       --project-root "C:\Users\AI Admin\Documents\CutlerResearchBrain" ^
#       --ticker MSFT
# =============================================================================

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import re
import sqlite3
import sys
import time
from collections import Counter
from datetime import datetime, timezone
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


PIPELINE_VERSION = "phase6-1.0"

# BM25 parameters. Standard, conservative values.
BM25_K1 = 1.5
BM25_B = 0.75

# An exact phrase match is worth more than the sum of its words, but not so
# much that it drowns everything else out.
PHRASE_BOOST = 0.35

MIN_TOKEN_LENGTH = 2
MAX_TOKEN_LENGTH = 40

LOGGER = logging.getLogger("phase6_keyword_index")

# Ordinary English filler. Deliberately short: financial text is full of words
# that look like filler and are not ("net", "other", "total", "growth"), so
# only words with no discriminating power at all are removed.
STOPWORDS = frozenset("""
a about above after again against all am an and any are aren as at be because
been before being below between both but by can cannot could couldn did didn do
does doesn doing don down during each few for from further had hadn has hasn
have haven having he her here hers herself him himself his how i if in into is
isn it its itself just me more most must my myself no nor not now of off on once
only or other ought our ours ourselves out over own same shan she should
shouldn so some such than that the their theirs them themselves then there these
they this those through to too under until up ve very was wasn we were weren
what when where which while who whom why will with won would wouldn you your
yours yourself yourselves
""".split())

RE_TOKEN = re.compile(r"[a-z0-9][a-z0-9&./'\-]*")
RE_TRIM = re.compile(r"^[^a-z0-9]+|[^a-z0-9%]+$")
RE_QUOTED = re.compile(r'"([^"]{2,200})"')
SAFE_TICKER = re.compile(r"^[A-Za-z0-9._-]{1,15}$")


# ---------------------------------------------------------------------------
# Logging and path safety (same rules as every other phase)
# ---------------------------------------------------------------------------

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


def is_relative_to(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except (ValueError, OSError):
        return False


def validate_project_root(project_root: Path) -> None:
    if not project_root.exists():
        raise SystemExit(f"ERROR: --project-root does not exist: {project_root}")
    drive = os.path.splitdrive(str(project_root.resolve()))[0].upper().rstrip(":")
    if drive == "V":
        raise SystemExit(
            f"ERROR: --project-root is on the V Drive ({project_root}). "
            "The V Drive is read-only. Aborting."
        )


def assert_inside_project(path: Path, project_root: Path) -> None:
    if not is_relative_to(path, project_root):
        raise SystemExit(f"ERROR: refusing to write outside the project root: {path}")


def safe_ticker(value: str) -> str:
    ticker = (value or "").strip().upper()
    if not SAFE_TICKER.match(ticker):
        raise SystemExit(f"ERROR: --ticker must be a short alphanumeric symbol, got: {value!r}")
    return ticker


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(moment: datetime) -> str:
    return moment.replace(microsecond=0).isoformat()


# ---------------------------------------------------------------------------
# Tokenisation - the whole of the "language understanding" in this file
# ---------------------------------------------------------------------------

def tokenize(text: str) -> list:
    """Lowercase, split on non-word characters, trim punctuation, drop filler.

    Numbers and symbols that carry meaning in financial text are kept on
    purpose: '10-q', '8-k', 'fy26', 'q2', '39%'. Losing those would defeat the
    point of having a keyword index at all.
    """
    tokens = []
    for raw in RE_TOKEN.findall((text or "").lower()):
        token = RE_TRIM.sub("", raw)
        if not token or token in STOPWORDS:
            continue
        if len(token) < MIN_TOKEN_LENGTH and not token.isdigit():
            continue
        if len(token) > MAX_TOKEN_LENGTH:
            token = token[:MAX_TOKEN_LENGTH]
        tokens.append(token)
    return tokens


def normalise_phrase(text: str) -> str:
    """A phrase, flattened the same way on both sides of a comparison."""
    return " ".join(tokenize(text))


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def connect(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise SystemExit(
            f"ERROR: database not found: {db_path}\n"
            "  Phases 1 through 5A must have run before the keyword index can be built."
        )
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def create_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS keyword_index_runs (
            run_id            INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker            TEXT,
            chunks_indexed    INTEGER,
            documents_indexed INTEGER,
            vocabulary_size   INTEGER,
            created_at        TEXT,
            status            TEXT,
            error_message     TEXT,
            index_path        TEXT,
            pipeline_version  TEXT
        )
    """)
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(keyword_index_runs)")}
    for column in ("index_path", "pipeline_version"):
        if column not in existing:
            conn.execute(f"ALTER TABLE keyword_index_runs ADD COLUMN {column} TEXT")
    conn.commit()


def load_chunks(conn: sqlite3.Connection, ticker: str) -> list:
    """Every embedded chunk for this ticker. Read-only."""
    try:
        rows = conn.execute(
            "SELECT chunk_id, document_id, chunk_text, file_name, relative_path, "
            "       folder_date_guess, detected_year, source_type_guess, "
            "       document_type_guess, page_start_guess, page_end_guess, "
            "       chunk_char_count, embedding_status "
            "FROM rag_chunks WHERE ticker = ? ORDER BY chunk_id", (ticker,)).fetchall()
    except sqlite3.OperationalError as exc:
        raise SystemExit(
            f"ERROR: no rag_chunks table in the database ({exc}).\n"
            "  Phase 5A has not run. Build the vector index first:\n"
            f"  python scripts\\build_rag_index.py --ticker {ticker} ..."
        ) from exc

    usable = [r for r in rows if (r["chunk_text"] or "").strip()]
    if not usable:
        raise SystemExit(
            f"ERROR: no chunks with text for {ticker}.\n"
            f"  Build the vector index first: python scripts\\build_rag_index.py "
            f"--ticker {ticker} ..."
        )
    empty = len(rows) - len(usable)
    if empty:
        LOGGER.warning("%d chunk row(s) had no text and were skipped.", empty)
    return usable


# ---------------------------------------------------------------------------
# Index building
# ---------------------------------------------------------------------------

def build_index(rows: list, ticker: str) -> dict:
    """A plain inverted index plus the lengths BM25 needs. No cleverness."""
    chunk_ids = []
    documents = []
    lengths = []
    postings = {}

    for position, row in enumerate(rows):
        tokens = tokenize(row["chunk_text"])
        chunk_ids.append(row["chunk_id"])
        documents.append(row["document_id"])
        lengths.append(len(tokens))
        for term, count in Counter(tokens).items():
            postings.setdefault(term, []).append([position, count])

    total_tokens = sum(lengths)
    index = {
        "pipeline_version": PIPELINE_VERSION,
        "ticker": ticker,
        "created_at": iso(utc_now()),
        "scorer": "bm25",
        "k1": BM25_K1,
        "b": BM25_B,
        "phrase_boost": PHRASE_BOOST,
        "stopword_count": len(STOPWORDS),
        "chunk_count": len(chunk_ids),
        "document_count": len(set(documents)),
        "vocabulary_size": len(postings),
        "total_tokens": total_tokens,
        "avg_chunk_length": (total_tokens / len(lengths)) if lengths else 0.0,
        "chunk_ids": chunk_ids,
        "chunk_documents": documents,
        "chunk_lengths": lengths,
        "postings": postings,
        "note": ("Counts of words in text Phase 5A already extracted. No document was opened "
                 "to build this, nothing was embedded, and nothing here is interpreted."),
    }
    return index


def index_path_for(project_root: Path, ticker: str) -> Path:
    return project_root / "06_RAG_Index" / "keyword" / f"{ticker}_keyword_index.json"


def write_index(index: dict, path: Path, project_root: Path) -> None:
    assert_inside_project(path, project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    LOGGER.info("Wrote %s (%.1f MB)", path, path.stat().st_size / 1_048_576)


# ---------------------------------------------------------------------------
# Searching - imported by ask_brain.py, which is why it lives here
# ---------------------------------------------------------------------------

def load_index(project_root: Path, ticker: str) -> dict:
    """Load the index written above. Raises FileNotFoundError if it is absent."""
    path = index_path_for(project_root, ticker)
    if not path.exists():
        raise FileNotFoundError(str(path))
    with path.open(encoding="utf-8") as fh:
        index = json.load(fh)
    index["_path"] = str(path)
    return index


def index_is_current(index: dict, chunk_ids: list) -> tuple:
    """Does this index still describe the chunks in the database?"""
    indexed = set(index.get("chunk_ids") or [])
    current = set(chunk_ids)
    missing = current - indexed          # in the database, not in the index
    stale = indexed - current            # in the index, no longer in the database
    return (not missing and not stale), missing, stale


def search(index: dict, query: str, limit: int = 30, allowed_chunk_ids=None) -> list:
    """BM25 over the index, with a bonus for an exact phrase match.

    Returns [{chunk_id, document_id, keyword_score, matched_terms,
              phrase_hit, rank}], best first. Deterministic: ties break on
    chunk_id so the same query always produces the same order.

    `allowed_chunk_ids`, when given, restricts scoring to that set - this is how
    metadata filters are applied before ranking rather than after it.
    """
    postings = index.get("postings") or {}
    chunk_ids = index.get("chunk_ids") or []
    lengths = index.get("chunk_lengths") or []
    documents = index.get("chunk_documents") or []
    total = len(chunk_ids)
    if not total:
        return []

    avg_len = index.get("avg_chunk_length") or 1.0
    k1 = float(index.get("k1", BM25_K1))
    b = float(index.get("b", BM25_B))

    allowed_positions = None
    if allowed_chunk_ids is not None:
        allowed = set(allowed_chunk_ids)
        allowed_positions = {i for i, cid in enumerate(chunk_ids) if cid in allowed}
        if not allowed_positions:
            return []

    query_terms = tokenize(query)
    if not query_terms:
        return []

    scores = {}
    matched = {}
    for term in set(query_terms):
        entries = postings.get(term)
        if not entries:
            continue
        df = len(entries)
        idf = math.log(1.0 + (total - df + 0.5) / (df + 0.5))
        for position, tf in entries:
            if allowed_positions is not None and position not in allowed_positions:
                continue
            length = lengths[position] or 1
            denominator = tf + k1 * (1.0 - b + b * (length / avg_len))
            scores[position] = scores.get(position, 0.0) + idf * (tf * (k1 + 1.0)) / denominator
            matched.setdefault(position, set()).add(term)

    if not scores:
        return []

    # Exact phrase bonus: the whole query, and anything the caller quoted.
    phrases = [normalise_phrase(p) for p in RE_QUOTED.findall(query or "")]
    whole = " ".join(query_terms)
    if len(query_terms) > 1 and whole not in phrases:
        phrases.append(whole)
    phrases = [p for p in phrases if p and " " in p]

    results = []
    for position, score in scores.items():
        results.append({
            "position": position,
            "chunk_id": chunk_ids[position],
            "document_id": documents[position] if position < len(documents) else None,
            "keyword_score": score,
            "matched_terms": sorted(matched.get(position, ())),
            "phrase_hit": False,
            "phrases_to_check": phrases,
        })

    results.sort(key=lambda r: (-r["keyword_score"], r["chunk_id"]))
    return results[:max(1, limit)]


def apply_phrase_boost(results: list, texts: dict) -> list:
    """Boost results whose text actually contains a query phrase.

    Kept separate from search() because it needs the chunk text, which lives in
    the database rather than in the index file. Callers that do not care can
    skip it and lose nothing but the boost.
    """
    for result in results:
        phrases = result.pop("phrases_to_check", []) or []
        text = normalise_phrase(texts.get(result["chunk_id"], ""))
        if text and any(phrase in text for phrase in phrases):
            result["phrase_hit"] = True
            result["keyword_score"] *= (1.0 + PHRASE_BOOST)
    results.sort(key=lambda r: (-r["keyword_score"], r["chunk_id"]))
    for rank, result in enumerate(results, start=1):
        result["keyword_rank"] = rank
    return results


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------

def build_summary(index: dict, conn: sqlite3.Connection, ticker: str, rows: list,
                  elapsed: float, rebuilt: bool) -> str:
    doc_counts = Counter(r["document_id"] for r in rows)
    source_counts = Counter((r["source_type_guess"] or "(none)") for r in rows)
    type_counts = Counter((r["document_type_guess"] or "(none)") for r in rows)

    # Most common terms, purely as a sanity check that tokenisation worked.
    frequencies = sorted(
        ((term, sum(tf for _, tf in entries), len(entries))
         for term, entries in index["postings"].items()),
        key=lambda x: (-x[1], x[0]))[:25]

    singletons = sum(1 for entries in index["postings"].values() if len(entries) == 1)

    lines = [
        f"# Keyword index summary - {ticker}",
        "",
        f"_Generated {index['created_at']} by `build_keyword_index.py` ({PIPELINE_VERSION})._",
        "",
        "## What this is",
        "",
        "A word index over the chunks Phase 5A already embedded. It lets Ask Brain find text by "
        "the **words in it**, not only by embedding similarity.",
        "",
        "Vector search matches meaning and is weak on exact strings; a filing line like "
        "*Azure and other cloud services revenue* can lose to a loose paraphrase that happens to "
        "sit closer in vector space. Keyword search has the opposite failure mode. Phase 6 runs "
        "both and merges the results, which is the whole reason this file exists.",
        "",
        "Nothing here was interpreted. It is a count of words in text that was already on disk. "
        "No PDF was opened, nothing was embedded, and the Chroma store was not touched.",
        "",
        "## The index",
        "",
        f"- Chunks indexed: **{index['chunk_count']:,}**",
        f"- Documents represented: **{index['document_count']:,}**",
        f"- Vocabulary: **{index['vocabulary_size']:,}** distinct terms",
        f"- Total tokens after filtering: **{index['total_tokens']:,}**",
        f"- Average chunk length: **{index['avg_chunk_length']:.0f}** tokens",
        f"- Terms appearing in only one chunk: **{singletons:,}** "
        f"({singletons / max(1, index['vocabulary_size']):.0%} of the vocabulary)",
        f"- Scorer: BM25 (k1 = {index['k1']}, b = {index['b']}), exact-phrase boost "
        f"+{index['phrase_boost']:.0%}",
        f"- Stopwords removed: {index['stopword_count']} ordinary English filler words. "
        "Financial words that look like filler (*net, other, total, growth*) are deliberately "
        "kept.",
        f"- Build time: {elapsed:.1f} seconds ({'rebuilt' if rebuilt else 'unchanged'})",
        "",
        "## Coverage by source type",
        "",
        "| source_type_guess | chunks |",
        "|---|---|",
    ]
    for name, count in source_counts.most_common():
        lines.append(f"| {name} | {count:,} |")

    lines += ["", "## Coverage by document type", "", "| document_type_guess | chunks |", "|---|---|"]
    for name, count in type_counts.most_common():
        lines.append(f"| {name} | {count:,} |")

    lines += ["", "## Documents with the most chunks", "", "| document_id | chunks |", "|---|---|"]
    for document_id, count in doc_counts.most_common(10):
        lines.append(f"| `{document_id}` | {count} |")

    lines += ["", "## Most frequent terms", "",
              "A tokenisation sanity check, nothing more. If this list is full of punctuation or "
              "single letters, the tokeniser is broken.",
              "", "| term | occurrences | chunks |", "|---|---|---|"]
    for term, occurrences, chunks in frequencies:
        lines.append(f"| `{term}` | {occurrences:,} | {chunks:,} |")

    lines += ["", "## How it is used", "",
              "`ask_brain.py --retrieval-mode hybrid` (the default) retrieves candidates from "
              "both this index and the Chroma vector store, normalises the two scores separately, "
              "and merges them. `--retrieval-mode keyword` uses this index alone; "
              "`--retrieval-mode vector` ignores it entirely and reproduces Phase 5B behaviour.",
              "",
              "**Rebuild this index whenever the chunk set changes.** It is a snapshot of "
              "`rag_chunks` at the time above; if Phase 5A re-chunks or adds documents, Ask Brain "
              "will warn that the index is stale and fall back to vector-only retrieval.",
              "",
              "> **AI drafts. Humans verify. Cutler decides.**",
              ""]
    return "\n".join(lines)


def write_summaries(text: str, project_root: Path, ticker: str) -> list:
    written = []
    for path in (project_root / "06_RAG_Index" / "reports" / f"{ticker}_keyword_index_summary.md",
                 project_root / "02_Metadata_Index" / f"{ticker}_keyword_index_summary.md"):
        assert_inside_project(path, project_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        LOGGER.info("Wrote %s", path)
        written.append(path)
    return written


def record_run(conn: sqlite3.Connection, ticker: str, index: dict, status: str,
               error_message: str, index_path: str) -> int:
    cursor = conn.execute(
        "INSERT INTO keyword_index_runs (ticker, chunks_indexed, documents_indexed, "
        "vocabulary_size, created_at, status, error_message, index_path, pipeline_version) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (ticker, index.get("chunk_count", 0), index.get("document_count", 0),
         index.get("vocabulary_size", 0), iso(utc_now()), status, error_message,
         index_path, PIPELINE_VERSION))
    conn.commit()
    return int(cursor.lastrowid)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Cutler Research Brain Phase 6 - build a local BM25 keyword index over the "
                    "chunks Phase 5A embedded. No network, no PDFs, no embeddings.")
    parser.add_argument("--db", default="data/document_metadata.sqlite",
                        help="SQLite path; relative paths resolve against --project-root.")
    parser.add_argument("--project-root", required=True,
                        help=r'e.g. "C:\\Users\\AI Admin\\Documents\\CutlerResearchBrain"')
    parser.add_argument("--ticker", default="MSFT", help="Ticker to index (default MSFT).")
    parser.add_argument("--force", action="store_true",
                        help="Rebuild even when the existing index already matches the chunks.")
    parser.add_argument("--verbose", action="store_true", help="Chattier console output.")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    project_root = Path(args.project_root).expanduser().resolve()
    validate_project_root(project_root)          # before any file is created
    setup_logging(project_root / "logs" / "phase6_keyword_index.log", args.verbose)

    ticker = safe_ticker(args.ticker)
    db_path = Path(args.db).expanduser()
    if not db_path.is_absolute():
        db_path = (project_root / db_path).resolve()

    started = time.time()
    LOGGER.info("=" * 70)
    LOGGER.info("Keyword index %s | ticker %s | db %s", PIPELINE_VERSION, ticker, db_path)

    conn = connect(db_path)
    create_schema(conn)

    path = index_path_for(project_root, ticker)
    status = "completed"
    error_message = ""

    try:
        rows = load_chunks(conn, ticker)
        LOGGER.info("Loaded %d chunk(s) with text for %s.", len(rows), ticker)

        chunk_ids = [r["chunk_id"] for r in rows]
        rebuilt = True
        if path.exists() and not args.force:
            try:
                existing = load_index(project_root, ticker)
                current, missing, stale = index_is_current(existing, chunk_ids)
                if current:
                    LOGGER.info("Existing index already covers all %d chunk(s). Nothing to do "
                                "(use --force to rebuild anyway).", len(chunk_ids))
                    index = existing
                    rebuilt = False
                else:
                    LOGGER.info("Existing index is out of date (%d chunk(s) missing, %d stale). "
                                "Rebuilding.", len(missing), len(stale))
            except (json.JSONDecodeError, FileNotFoundError, KeyError) as exc:
                LOGGER.warning("Existing index could not be read (%s). Rebuilding.",
                               type(exc).__name__)

        if rebuilt:
            index = build_index(rows, ticker)
            write_index(index, path, project_root)

        elapsed = time.time() - started
        summary = build_summary(index, conn, ticker, rows, elapsed, rebuilt)
        write_summaries(summary, project_root, ticker)

    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - record the failure, do not hide it
        status = "failed"
        error_message = f"{type(exc).__name__}: {exc}"
        index = {}
        LOGGER.error("Keyword index build failed: %s", error_message)
        record_run(conn, ticker, index, status, error_message, str(path))
        conn.close()
        return 1

    run_id = record_run(conn, ticker, index, status, error_message, str(path))
    conn.close()

    LOGGER.info("-" * 70)
    LOGGER.info("Chunks indexed     : %d", index["chunk_count"])
    LOGGER.info("Documents          : %d", index["document_count"])
    LOGGER.info("Vocabulary         : %d terms", index["vocabulary_size"])
    LOGGER.info("Average chunk      : %.0f tokens", index["avg_chunk_length"])
    LOGGER.info("Index file         : %s", path)
    LOGGER.info("Run id             : %d (%s)", run_id, status)
    LOGGER.info("Runtime            : %.1f seconds", time.time() - started)
    LOGGER.info("-" * 70)
    LOGGER.info("This index counts words. It answers no question and interprets nothing.")
    LOGGER.info("Next: py -3.11 scripts\\inspect_retrieval_quality.py --ticker %s ...", ticker)
    return 0


if __name__ == "__main__":
    sys.exit(main())
