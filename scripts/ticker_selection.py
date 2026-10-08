#!/usr/bin/env python3
"""Phase 11 - one definition of "the documents for this ticker".

Every script that counts, costs, chunks or embeds a ticker's documents imports
this module. That is the point of it: a planner that estimates one set of
documents and a builder that embeds a different set would produce a cost
estimate nobody can trust, and the first time they disagreed would be after the
money had been spent.

THE SHORT-TICKER PROBLEM
------------------------
Two of the Phase 11 tickers are a single letter: T (AT&T) and B (Barnes Group).
A substring match for them is not a near-miss, it is a catastrophe. Measured on
the Phase 10 fixture database:

    WHERE ticker_candidates_clean LIKE '%T%'  ->  11 of 59 documents
    WHERE ticker_candidates_clean LIKE '%B%'  ->  12 of 59 documents

On the real archive that is thousands of unrelated documents embedded at real
cost, and an index that answers questions about AT&T with research on somebody
else. So matching here is never a substring test. The stored value is split
into whole tokens and compared exactly, which is the same discipline Phase 9.3
adopted after the SQL LIKE missed 'AAPL, MSFT'.

WHAT IS NOT MATCHED
-------------------
`ticker_candidates_clean` is blank for documents whose raw candidates were
ambiguous - the Phase 1 cleaner drops 'UBS,RBC' rather than guess. Those
documents belong to no ticker and will not be indexed for any of them. That is
a deliberate exclusion, not an oversight, and the planner reports the count so
nobody discovers it by wondering where a document went.
"""
from __future__ import annotations

import re
import sqlite3

PIPELINE_VERSION = "phase11.3-1.0"

# The Phase 11 expansion set. MSFT is already indexed and is listed so the rest
# of the system has one place to ask "which tickers does this project know
# about" - never so that a batch quietly re-embeds it.
PHASE11_TICKERS = ("KIM", "KR", "MRK", "T", "VZ", "AMZN", "SLB", "B", "ABBV")
ALREADY_INDEXED = ("MSFT",)
ALL_KNOWN_TICKERS = ALREADY_INDEXED + PHASE11_TICKERS

# Anything that can separate two tickers in one stored value.
SEPARATORS = re.compile(r"[,\s;|/]+")
VALID_TICKER = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")


def safe_ticker(value: str) -> str:
    """Normalise and validate a ticker that came from a command line."""
    ticker = (value or "").strip().upper()
    if not VALID_TICKER.match(ticker):
        raise ValueError(
            f"{value!r} is not a ticker. Expected 1-10 characters, starting with a letter.")
    return ticker


def ticker_tokens(value: str) -> list:
    """The tickers recorded on one document, as whole tokens."""
    return [token for token in SEPARATORS.split((value or "").upper()) if token]


def tagged_with(value: str, ticker: str) -> bool:
    """Is this document tagged with exactly this ticker?

    Token equality, never `in`. 'T' must not match 'MSFT', 'TSLA' or 'AT&T
    Inc'; 'B' must not match 'ABBV' or 'BRK.B'.
    """
    return ticker.upper() in ticker_tokens(value)


# The eligibility rules, in one place, matching build_rag_index.select_documents.
# A document is RAG-eligible when every one of these is true.
ELIGIBILITY = (
    ("usable_for_rag", "COALESCE(d.usable_for_rag, 0) = 1"),
    ("extraction_status", "COALESCE(d.pdf_extraction_status, '') IN ('extracted', 'partial')"),
    ("extraction_quality", "COALESCE(d.pdf_extraction_quality, '') NOT IN ('Failed', 'Empty')"),
    ("not_duplicate", "COALESCE(d.duplicate_status, '') <> 'duplicate'"),
    ("not_high_review", "COALESCE(d.review_severity, '') <> 'High'"),
)

# The card exclusion test, as SQL, in ONE place - and NULLIF matters.
#
# Phase 11.2 found this divergence while checking that the builder and the
# inspector agree. The builder's clause was:
#
#     COALESCE(c.rag_exclusion_reason, 'none') = 'none'
#
# which treats a card whose reason is the EMPTY STRING as excluded, because
# COALESCE only substitutes for NULL: '' stays '' and fails the test. The
# inspector's Python test accepted both '' and 'none'. A card row with a blank
# reason would therefore be eligible to the inspector, invisible to the
# builder, and reported forever as a document that is behind and never catches
# up - one of the ways a phantom "behind" is manufactured.
#
# A blank reason is not a reason. NULLIF folds '' into NULL first, so blank and
# missing both mean "not excluded", and both sides now read the same clause.
CARD_EXCLUSION_SQL = "COALESCE(NULLIF(TRIM(c.rag_exclusion_reason), ''), 'none') = 'none'"

# The same test in Python. Kept next to the SQL so a change to one is an
# obvious omission in the other.
NO_EXCLUSION = ("", "none")


def card_excluded(value) -> bool:
    """Does this card exclude its document from RAG? Blank means no."""
    return str(value or "").strip().lower() not in NO_EXCLUSION


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
                        (name,)).fetchone() is not None


def eligibility_sql(conn: sqlite3.Connection) -> tuple:
    """(where_clause, join_clause) for RAG eligibility, minus the ticker test.

    The ticker test is deliberately absent: SQL cannot do the token comparison
    safely, so it is applied in Python by `tagged_with` after the rows come
    back. Filtering a few thousand candidate rows in Python is cheap; getting
    the ticker wrong is not.
    """
    joins = ["LEFT JOIN pdf_extractions e ON e.document_id = d.document_id"]
    where = [clause for _, clause in ELIGIBILITY]
    if table_exists(conn, "document_cards"):
        joins.append("LEFT JOIN document_cards c ON c.document_id = d.document_id")
        where.append(CARD_EXCLUSION_SQL)
    return " AND ".join(where), "\n          ".join(joins)


def candidate_rows(conn: sqlite3.Connection, ticker: str, eligible_only: bool = False) -> list:
    """Every document tagged with this ticker, eligible or not.

    One query for the whole archive slice, then a token filter in Python. The
    SQL narrows by a cheap substring so the scan is small, and the Python
    decides - so a substring false positive can never survive, but a true match
    can never be missed either.
    """
    has_cards = table_exists(conn, "document_cards")
    card_select = ("COALESCE(c.rag_exclusion_reason, '') AS rag_exclusion_reason, "
                   "COALESCE(c.card_md_path, '') AS card_md_path"
                   if has_cards else "'' AS rag_exclusion_reason, '' AS card_md_path")
    joins = "LEFT JOIN pdf_extractions e ON e.document_id = d.document_id"
    if has_cards:
        joins += "\n          LEFT JOIN document_cards c ON c.document_id = d.document_id"

    doc_columns = {row[1] for row in conn.execute("PRAGMA table_info(documents)")}
    optional = []
    for column in ("file_change_status", "stale_extraction", "stale_document_card",
                   "stale_rag_index", "file_size_bytes", "page_count", "rag_skip_reason"):
        optional.append(f"COALESCE(d.{column}, '') AS {column}" if column in doc_columns
                        else f"'' AS {column}")

    sql = f"""
        SELECT d.document_id,
               COALESCE(d.file_name, '')                   AS file_name,
               COALESCE(d.relative_path, '')               AS relative_path,
               COALESCE(d.file_path, '')                   AS file_path,
               COALESCE(d.archive_year, '')                AS archive_year,
               COALESCE(d.folder_date_guess, '')           AS folder_date_guess,
               COALESCE(d.detected_year, '')               AS detected_year,
               COALESCE(d.detected_month, '')              AS detected_month,
               COALESCE(d.source_type_guess, 'Unknown')    AS source_type_guess,
               COALESCE(d.document_type_guess, 'Unknown')  AS document_type_guess,
               COALESCE(d.ticker_candidates_clean, '')     AS ticker_candidates_clean,
               COALESCE(d.ticker_candidates_raw, '')       AS ticker_candidates_raw,
               COALESCE(d.pdf_extraction_status, '')       AS pdf_extraction_status,
               COALESCE(d.pdf_extraction_quality, '')      AS pdf_extraction_quality,
               COALESCE(d.duplicate_status, '')            AS duplicate_status,
               COALESCE(d.review_severity, '')             AS review_severity,
               COALESCE(d.usable_for_rag, 0)               AS usable_for_rag,
               COALESCE(e.document_text_path, '')          AS document_text_path,
               COALESCE(e.total_text_chars, 0)             AS total_text_chars,
               COALESCE(e.page_count, 0)                   AS page_count_extracted,
               COALESCE(e.scanned_pdf_suspected, 0)        AS scanned_pdf_suspected,
               {card_select},
               {', '.join(optional)}
          FROM documents d
          {joins}
         WHERE UPPER(COALESCE(d.ticker_candidates_clean, '')) LIKE ?
    """
    rows = [dict(row) for row in conn.execute(sql, (f"%{ticker.upper()}%",))]
    rows = [row for row in rows if tagged_with(row["ticker_candidates_clean"], ticker)]
    if eligible_only:
        rows = [row for row in rows if is_eligible(row)]
    return rows


def is_eligible(row: dict) -> bool:
    """The same five rules as ELIGIBILITY, applied to a row in Python."""
    return (int(row.get("usable_for_rag") or 0) == 1
            and str(row.get("pdf_extraction_status") or "") in ("extracted", "partial")
            and str(row.get("pdf_extraction_quality") or "") not in ("Failed", "Empty")
            and str(row.get("duplicate_status") or "") != "duplicate"
            and str(row.get("review_severity") or "") != "High"
            and not card_excluded(row.get("rag_exclusion_reason")))


def ineligibility_reasons(row: dict) -> list:
    """Why this document is not RAG-eligible. Empty list means it is."""
    reasons = []
    if int(row.get("usable_for_rag") or 0) != 1:
        reasons.append("not marked usable_for_rag")
    status = str(row.get("pdf_extraction_status") or "")
    if status not in ("extracted", "partial"):
        reasons.append(f"extraction status {status or 'none'}")
    quality = str(row.get("pdf_extraction_quality") or "")
    if quality in ("Failed", "Empty"):
        reasons.append(f"extraction quality {quality}")
    if str(row.get("duplicate_status") or "") == "duplicate":
        reasons.append("duplicate")
    if str(row.get("review_severity") or "") == "High":
        reasons.append("review severity High")
    if card_excluded(row.get("rag_exclusion_reason")):
        reasons.append(f"card exclusion: {str(row.get('rag_exclusion_reason')).strip()}")
    return reasons


def unassigned_documents(conn: sqlite3.Connection) -> list:
    """Documents with raw ticker candidates but no clean one.

    These belong to no ticker and will not be indexed for any of them. Usually
    the raw value named two companies and the cleaner refused to choose.
    """
    return [dict(row) for row in conn.execute(
        "SELECT document_id, COALESCE(file_name,'') AS file_name, "
        "       COALESCE(ticker_candidates_raw,'') AS ticker_candidates_raw "
        "  FROM documents "
        " WHERE COALESCE(ticker_candidates_raw, '') <> '' "
        "   AND COALESCE(ticker_candidates_clean, '') = ''")]


def collection_name_for(ticker: str) -> str:
    """The Chroma collection for one ticker. The same name every script uses."""
    return f"cutler_research_brain_{ticker.lower()}_2026"


def chroma_path_for(project_root, ticker: str):
    return project_root / "06_RAG_Index" / "chroma" / ticker.upper()


def keyword_index_path_for(project_root, ticker: str):
    return project_root / "06_RAG_Index" / "keyword" / f"{ticker.upper()}_keyword_index.json"


def index_state(conn: sqlite3.Connection, project_root, ticker: str) -> dict:
    """What exists for this ticker right now: chunks, vectors, keyword index."""
    ticker = ticker.upper()
    state = {"ticker": ticker, "chunks": 0, "embedded": 0, "pending": 0, "failed": 0,
             "superseded": 0, "documents": 0, "tokens": 0,
             "chroma_present": chroma_path_for(project_root, ticker).exists(),
             "collection": collection_name_for(ticker),
             "keyword_present": keyword_index_path_for(project_root, ticker).exists(),
             "last_run": None, "date_from": "", "date_to": ""}
    if not table_exists(conn, "rag_chunks"):
        return state

    row = conn.execute(
        "SELECT COUNT(*) AS chunks, COUNT(DISTINCT document_id) AS documents, "
        "       COALESCE(SUM(chunk_token_count), 0) AS tokens "
        "  FROM rag_chunks WHERE UPPER(ticker) = ?", (ticker,)).fetchone()
    state.update({"chunks": row["chunks"], "documents": row["documents"],
                  "tokens": row["tokens"]})
    for status, count in conn.execute(
            "SELECT COALESCE(embedding_status,'') , COUNT(*) FROM rag_chunks "
            " WHERE UPPER(ticker) = ? GROUP BY 1", (ticker,)):
        key = {"embedded": "embedded", "pending": "pending", "failed": "failed",
               "superseded": "superseded"}.get(str(status))
        if key:
            state[key] += count
        elif str(status) in ("", "new"):
            state["pending"] += count

    dates = conn.execute(
        "SELECT MIN(NULLIF(d.folder_date_guess,'')), MAX(NULLIF(d.folder_date_guess,'')) "
        "  FROM rag_chunks r JOIN documents d ON d.document_id = r.document_id "
        " WHERE UPPER(r.ticker) = ?", (ticker,)).fetchone()
    state["date_from"], state["date_to"] = (dates[0] or ""), (dates[1] or "")

    if table_exists(conn, "embedding_runs"):
        run = conn.execute(
            "SELECT run_id, status, completed_at, chunks_embedded, estimated_cost_usd "
            "  FROM embedding_runs WHERE UPPER(ticker) = ? "
            " ORDER BY run_id DESC LIMIT 1", (ticker,)).fetchone()
        if run is not None:
            state["last_run"] = dict(run)
    return state


def is_indexed(conn: sqlite3.Connection, project_root, ticker: str) -> bool:
    """Can Ask Brain answer for this ticker at all?

    Embedded chunks AND a Chroma store. Rows without vectors cannot be
    retrieved, and a store without rows has nothing to cite.
    """
    state = index_state(conn, project_root, ticker)
    return state["embedded"] > 0 and state["chroma_present"]


# ---------------------------------------------------------------------------
# Phase 11.2 - reconciliation: which documents are actually missing, and why
#
# The Phase 11 inspector decided "behind" by comparing two COUNTS:
#
#     eligible_count > distinct_document_ids_in_rag_chunks
#
# Those are different populations, so their sizes say almost nothing. A
# document can be:
#
#   * eligible now and indexed           - fine
#   * eligible now and NOT indexed       - genuinely behind
#   * indexed but no longer eligible     - was eligible when it was indexed and
#                                          something changed since (marked a
#                                          duplicate, review raised to High, a
#                                          card exclusion added). Its chunks
#                                          stay, because this project never
#                                          deletes them.
#
# A count difference of 11 could be 11 missing, or 30 missing and 19 no longer
# eligible. On the Phase 10 fixture MSFT reads 3 eligible against 10 indexed -
# a difference of MINUS seven - while the true number of missing documents is
# zero. The comparison was never capable of answering the question.
#
# So reconciliation works on SETS, and every missing document carries a reason,
# because several of the reasons cannot be fixed by embedding at all.
# ---------------------------------------------------------------------------

# Why an eligible document has no embedded chunks. The first four cannot be
# repaired by running the builder again; the last three can.
MISSING_REASONS = {
    "claimed_by_another_ticker": "its chunks exist and are embedded, but under another ticker - "
                                 "chunk_id does not name a ticker, so the last ticker built owns "
                                 "them",
    "no_extracted_text_path": "no extracted text path recorded - it was never extracted",
    "extracted_text_file_absent": "the extracted text file is not on disk",
    "extracted_text_empty": "the extracted text is empty (often a scanned PDF)",
    "chunks_failed": "chunks exist but embedding failed for all of them",
    "chunks_pending": "chunks exist but were never embedded",
    "only_superseded_chunks": "every chunk is superseded - the document changed and "
                              "needs re-chunking",
    "never_chunked": "eligible, with text, and never chunked",
}
REPAIRABLE = ("chunks_pending", "only_superseded_chunks", "never_chunked", "chunks_failed")

# ---------------------------------------------------------------------------
# Phase 11.3 - the chunk_id that does not name a ticker
#
# `build_rag_index.py` builds chunk ids as
#
#     chunk_id = f"{document_id}_c{chunk_index:04d}"
#
# and `rag_chunks` declares `chunk_id TEXT PRIMARY KEY`, while chunks are
# written with `INSERT OR REPLACE`. The ticker is a COLUMN, not part of the key.
#
# So a document tagged with two tickers has ONE set of chunk rows, and building
# the second ticker REPLACES the first ticker's rows in place - same ids, same
# text, new value in the `ticker` column. Nothing is deleted and the row count
# never drops, which is why this does not look like data loss; the chunks simply
# change owner. Whichever ticker was built LAST wins.
#
# Measured on the live database after the Phase 11.2 repairs: 36,007 chunk rows,
# 36,007 distinct chunk_ids, and not one document whose chunks span two tickers -
# although 101 eligible documents are claimed by more than one ticker. Every one
# of the 106 (document, ticker) pairs reported "missing" was in fact already
# chunked and embedded under a different ticker. Retrieval is genuinely affected:
# ask_brain restricts candidates with `WHERE ticker = ?`, so the losing ticker
# cannot cite those documents at all.
#
# A document in this state is NOT repairable by embedding while the key stays as
# it is - a repair run would only steal the chunks back, and the next ticker's
# run would steal them again. It is repairable once the key is (chunk_id,
# ticker); see scripts/migrate_rag_chunk_key.py.
# ---------------------------------------------------------------------------
CLAIMED = "claimed_by_another_ticker"


def chunk_key_is_ticker_scoped(conn: sqlite3.Connection) -> bool:
    """Can two tickers hold the same chunk_id at once?

    True when `rag_chunks` is keyed by (chunk_id, ticker). False for the
    original `chunk_id TEXT PRIMARY KEY`, under which one ticker's build
    silently takes another ticker's rows.
    """
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='rag_chunks'"
                       ).fetchone()
    if row is None or not row[0]:
        return False
    sql = re.sub(r"\s+", " ", row[0]).lower()
    return "primary key (chunk_id, ticker)" in sql


def foreign_chunk_owners(conn: sqlite3.Connection, ticker: str) -> dict:
    """{document_id: [other tickers that hold embedded chunks for it]}.

    Only documents with NO embedded chunk under `ticker` are of interest, so the
    caller can tell "never indexed" from "indexed, under someone else's name".
    """
    if not table_exists(conn, "rag_chunks"):
        return {}
    owners = {}
    for row in conn.execute(
            "SELECT document_id, UPPER(ticker) AS owner FROM rag_chunks "
            " WHERE embedding_status = 'embedded' AND UPPER(ticker) <> ? "
            " GROUP BY document_id, UPPER(ticker)", (ticker.upper(),)):
        owners.setdefault(row["document_id"], []).append(row["owner"])
    for document_id in owners:
        owners[document_id].sort()
    return owners


def chunk_state_by_document(conn: sqlite3.Connection, ticker: str) -> dict:
    """Per-document chunk counts for one ticker, by embedding status."""
    if not table_exists(conn, "rag_chunks"):
        return {}
    state = {}
    for row in conn.execute(
            "SELECT document_id, COALESCE(embedding_status,'') AS status, COUNT(*) AS n "
            "  FROM rag_chunks WHERE UPPER(ticker) = ? GROUP BY 1, 2", (ticker.upper(),)):
        entry = state.setdefault(row["document_id"],
                                 {"total": 0, "embedded": 0, "pending": 0,
                                  "failed": 0, "superseded": 0})
        entry["total"] += row["n"]
        status = str(row["status"])
        if status in ("embedded", "failed", "superseded"):
            entry[status] += row["n"]
        else:
            entry["pending"] += row["n"]
    return state


def resolve_text_file(project_root, row: dict):
    """The extracted text file for a document, if it is really there.

    Mirrors build_rag_index.resolve_text_path: the recorded path first, then
    the conventional location. A document whose text is missing produces no
    chunks however many times the builder is run, which is why this is checked
    here rather than guessed at.
    """
    from pathlib import Path as _Path
    recorded = str(row.get("document_text_path") or "").strip()
    if recorded:
        path = _Path(recorded)
        if not path.is_absolute():
            path = project_root / path
        if path.exists():
            return path
    year = str(row.get("archive_year") or "").strip() or "unknown_year"
    fallback = (project_root / "03_Extracted_Text" / year /
                str(row.get("document_id")) / "document_text.txt")
    return fallback if fallback.exists() else None


def text_bytes_on_disk(path) -> int:
    """How much text is really in the file the builder would read.

    Deliberately NOT pdf_extractions.total_text_chars. That column records what
    extraction measured at the time; the builder opens the file. When the two
    disagree - a re-extraction that never updated the row, or a row that was
    never written at all - the file is the one that decides whether chunking
    will produce anything, so the file is what is checked here.
    """
    try:
        return path.stat().st_size if path is not None else 0
    except OSError:
        return 0


def missing_reason(project_root, row: dict, chunks: dict, claimed_by=None) -> str:
    """Why this eligible document has no embedded chunks. One reason, ordered.

    Ordered deliberately: the causes that no amount of embedding can fix are
    reported first, so a repair command never promises to fix a document whose
    text does not exist.

    `claimed_by` comes first of all. A document whose chunks are embedded under
    another ticker is not a missing document in any ordinary sense - it is an
    ownership collision, and no extraction fix or embedding run addresses it.
    Reporting it as `never_chunked` is what made the Phase 11.2 repairs churn:
    the builder dutifully re-chunked it, took the rows back, and handed them to
    the next ticker on the next run.
    """
    if claimed_by:
        return CLAIMED
    text_file = resolve_text_file(project_root, row)
    if text_file is None:
        if not str(row.get("document_text_path") or "").strip():
            return "no_extracted_text_path"
        return "extracted_text_file_absent"
    if text_bytes_on_disk(text_file) <= 0:
        return "extracted_text_empty"
    if chunks:
        if chunks["failed"] and not chunks["pending"]:
            return "chunks_failed"
        if chunks["pending"]:
            return "chunks_pending"
        if chunks["superseded"] and not chunks["embedded"]:
            return "only_superseded_chunks"
    return "never_chunked"


def reconcile(conn: sqlite3.Connection, project_root, ticker: str) -> dict:
    """Compare what SHOULD be indexed with what IS, as sets.

    Read-only. Returns the three populations and a classified list of every
    eligible document with no embedded chunks.
    """
    ticker = ticker.upper()
    eligible = candidate_rows(conn, ticker, eligible_only=True)
    eligible_by_id = {row["document_id"]: row for row in eligible}
    chunks = chunk_state_by_document(conn, ticker)
    foreign = foreign_chunk_owners(conn, ticker)
    key_scoped = chunk_key_is_ticker_scoped(conn)

    with_any_chunk = {doc for doc, state in chunks.items() if state["total"]}
    with_embedded = {doc for doc, state in chunks.items() if state["embedded"]}

    missing = []
    for document_id, row in eligible_by_id.items():
        if document_id in with_embedded:
            continue
        claimed_by = foreign.get(document_id) or []
        reason = missing_reason(project_root, row, chunks.get(document_id, {}), claimed_by)
        state = chunks.get(document_id, {})
        text_file = resolve_text_file(project_root, row)
        missing.append({
            "text_path": str(text_file) if text_file is not None else "",
            "text_chars_on_disk": text_bytes_on_disk(text_file),
            "document_id": document_id,
            "file_name": row.get("file_name", ""),
            "document_date": row.get("folder_date_guess", ""),
            "source_type": row.get("source_type_guess", ""),
            "document_type": row.get("document_type_guess", ""),
            "usable_for_rag": int(row.get("usable_for_rag") or 0),
            "extraction_status": row.get("pdf_extraction_status", ""),
            "extraction_quality": row.get("pdf_extraction_quality", ""),
            "card_status": ("excluded: " + str(row.get("rag_exclusion_reason")).strip()
                            if card_excluded(row.get("rag_exclusion_reason"))
                            else ("card present" if row.get("card_md_path") else "no card")),
            "stale_rag_index": str(row.get("stale_rag_index") or ""),
            "has_chunks": bool(state.get("total")),
            "embedded_chunks": int(state.get("embedded", 0)),
            "pending_chunks": int(state.get("pending", 0)),
            "failed_chunks": int(state.get("failed", 0)),
            "superseded_chunks": int(state.get("superseded", 0)),
            "total_text_chars": int(row.get("total_text_chars") or 0),
            "claimed_by": ", ".join(claimed_by),
            "reason": reason,
            "reason_text": MISSING_REASONS.get(reason, reason),
            # A claimed document becomes repairable only once two tickers can
            # hold the same chunk_id. Until then, repairing it is a theft that
            # the next ticker's run reverses.
            "repairable": (reason in REPAIRABLE
                           or (reason == CLAIMED and key_scoped)),
        })
    missing.sort(key=lambda item: (item["reason"], item["document_date"], item["file_name"]))

    # Indexed but no longer eligible. NOT an error and NOT a deletion: the
    # chunks stay and remain retrievable. Reported so the numbers add up.
    no_longer_eligible = sorted(with_embedded - set(eligible_by_id))

    return {
        "ticker": ticker,
        "chunk_key_is_ticker_scoped": key_scoped,
        "claimed_count": sum(1 for item in missing if item["reason"] == CLAIMED),
        "eligible": len(eligible_by_id),
        "with_any_chunk": len(with_any_chunk),
        "with_embedded": len(with_embedded),
        "missing": missing,
        "missing_count": len(missing),
        "repairable_count": sum(1 for item in missing if item["repairable"]),
        "unrepairable_count": sum(1 for item in missing if not item["repairable"]),
        "no_longer_eligible": no_longer_eligible,
        "no_longer_eligible_count": len(no_longer_eligible),
        "reason_counts": {reason: sum(1 for item in missing if item["reason"] == reason)
                          for reason in MISSING_REASONS
                          if any(item["reason"] == reason for item in missing)},
    }


# ---------------------------------------------------------------------------
# Phase 11.3 - snapshots, so a repair can be proved not to have taken anything
# ---------------------------------------------------------------------------

def chroma_count(project_root, ticker: str):
    """How many vectors this ticker's Chroma collection holds, or None.

    None means "could not be read" - chromadb missing, no store, or a store
    that will not open. None is never treated as zero: a comparison against an
    unknown is not evidence, and pretending otherwise would let a real loss
    slip through as "0 -> 0".
    """
    path = chroma_path_for(project_root, ticker)
    if not path.exists():
        return None
    try:
        import chromadb                                    # noqa: PLC0415
        client = chromadb.PersistentClient(path=str(path))
        return client.get_collection(name=collection_name_for(ticker)).count()
    except Exception:                                      # noqa: BLE001
        return None


def snapshot(conn: sqlite3.Connection, project_root, ticker: str) -> dict:
    """Everything about one ticker that a repair must not reduce.

    Taken before and after every repair step. The id SETS matter more than the
    counts: Phase 11.2's whole failure was a count that stayed flat while the
    rows underneath it changed owner, so a snapshot that recorded only totals
    would have reported that run as clean.
    """
    ticker = ticker.upper()
    eligible = {row["document_id"] for row in candidate_rows(conn, ticker, eligible_only=True)}
    chunks = chunk_state_by_document(conn, ticker)
    embedded = {doc for doc, state in chunks.items() if state["embedded"]}
    rows = (0, 0)
    if table_exists(conn, "rag_chunks"):
        rows = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(CASE WHEN embedding_status='embedded' THEN 1 ELSE 0 END), 0) "
            "  FROM rag_chunks WHERE UPPER(ticker) = ?", (ticker,)).fetchone()
    return {
        "ticker": ticker,
        "eligible_ids": eligible,
        "embedded_document_ids": embedded,
        "missing_ids": eligible - embedded,
        "chunk_rows": int(rows[0]),
        "embedded_chunk_rows": int(rows[1]),
        "chroma_count": chroma_count(project_root, ticker),
    }


def snapshot_diff(before: dict, after: dict) -> dict:
    """What a repair actually did to one ticker, in documents and in chunks.

    `lost_document_ids` and `lost_chunk_rows` are the ones that matter: a repair
    is append-only or it is not, and this is the test.
    """
    lost = before["embedded_document_ids"] - after["embedded_document_ids"]
    gained = after["embedded_document_ids"] - before["embedded_document_ids"]
    chroma_lost = None
    if before["chroma_count"] is not None and after["chroma_count"] is not None:
        chroma_lost = max(0, before["chroma_count"] - after["chroma_count"])
    return {
        "ticker": after["ticker"],
        "lost_document_ids": sorted(lost),
        "gained_document_ids": sorted(gained),
        "lost_documents": len(lost),
        "gained_documents": len(gained),
        "chunk_rows_before": before["chunk_rows"],
        "chunk_rows_after": after["chunk_rows"],
        "lost_chunk_rows": max(0, before["chunk_rows"] - after["chunk_rows"]),
        "embedded_chunk_rows_before": before["embedded_chunk_rows"],
        "embedded_chunk_rows_after": after["embedded_chunk_rows"],
        "lost_embedded_chunk_rows": max(
            0, before["embedded_chunk_rows"] - after["embedded_chunk_rows"]),
        "chroma_before": before["chroma_count"],
        "chroma_after": after["chroma_count"],
        "chroma_lost": chroma_lost,
        "regressed": bool(lost) or after["chunk_rows"] < before["chunk_rows"]
                     or after["embedded_chunk_rows"] < before["embedded_chunk_rows"]
                     or bool(chroma_lost),
    }
