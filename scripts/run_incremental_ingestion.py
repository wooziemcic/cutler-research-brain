#!/usr/bin/env python3
r"""Cutler Research Brain - Phase 9: incremental ingestion.

Phases 1 through 8 assumed the archive stood still. It does not: the V drive
gains files every day, and a few of them are replacements for files that were
already read. This script is the answer to "what changed, and what does that
mean for everything downstream" - and, just as importantly, to "what did NOT
change, so that we do not pay to read it again".

  AI drafts. Humans verify. Cutler decides.

WHAT THIS DOES

  1. Walks the source archive and compares it with the documents table:
     new / changed / unchanged / missing / skipped.
  2. Writes metadata for the new and changed files, using the Phase 1 rules -
     not a second implementation of them.
  3. Marks what a changed file makes stale: its extraction, its card, its
     chunks.
  4. Runs the existing Phase 2, 3 and 4 scripts over ONLY the affected
     documents.
  5. Optionally chunks and embeds only the new MSFT chunks, adding them to the
     existing Chroma collection.
  6. Optionally runs three golden questions as a smoke test.

WHAT THIS NEVER DOES

  The source archive is opened read-only, always: the only operations performed
  on it are directory listing, stat, and reading bytes to hash. There is no
  code path in this file that opens a source file for writing, and every output
  path is asserted to be inside the project root before it is written.

  Nothing is deleted. A file that has vanished from the archive is REPORTED,
  never removed from the database - a network share that is briefly unmounted
  must not be able to erase the memory of what was in it.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

PIPELINE_VERSION = "phase9.3-1.0"

DEFAULT_SOURCE_ROOT = r"V:\Companies\A Current Month\2026"
DEFAULT_DB = "data/document_metadata.sqlite"

# Change statuses. These are the vocabulary of the whole phase.
NEW = "new_file"
CHANGED = "changed_file"
UNCHANGED = "unchanged_file"
MISSING = "missing_file"
SKIPPED = "skipped_file"

STATUS_COMPLETED = "completed"
STATUS_DRY_RUN = "dry_run"
STATUS_FAILED = "failed"

SMOKE_QUESTION_IDS = ("msft_azure_growth_sec", "msft_no_evidence_test", "msft_safety_test")

# Phase 9 columns added to documents. Every one is additive; nothing existing is
# renamed, re-typed or dropped.
DOCUMENT_COLUMNS = [
    "last_seen_at",
    "last_ingested_at",
    "ingestion_run_id",
    "file_change_status",
    "content_hash_previous",
    "content_hash_current",
    "stale_extraction",
    "stale_document_card",
    "stale_rag_index",
    # Phase 9.2. Why RAG skipped this document, in words. A flag nobody can
    # explain is a flag nobody can clear.
    "rag_skip_reason",
]

LOGGER = logging.getLogger("phase9_ingestion")

PRINT_FAILURES = 0


# ---------------------------------------------------------------------------
# Console safety (Phase 7A.1 guard, carried forward)
# ---------------------------------------------------------------------------

def configure_console_encoding() -> None:
    """Make stdout/stderr UTF-8 where the terminal allows it."""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


def safe_print(text: str = "") -> None:
    """print() that can never take down a completed run."""
    global PRINT_FAILURES
    try:
        print(text)
    except (UnicodeEncodeError, OSError):
        PRINT_FAILURES += 1
        try:
            print(str(text).encode("ascii", "replace").decode("ascii"))
        except Exception:  # noqa: BLE001 - display is the least important thing here
            PRINT_FAILURES += 1


# ---------------------------------------------------------------------------
# Paths and guards
# ---------------------------------------------------------------------------

def is_relative_to(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except (ValueError, OSError):
        return False


def validate_project_root(project_root: Path) -> None:
    if not project_root.exists():
        raise SystemExit(f"ERROR: project root does not exist: {project_root}")
    if not project_root.is_dir():
        raise SystemExit(f"ERROR: project root is not a folder: {project_root}")


def assert_inside_project(path: Path, project_root: Path) -> None:
    """Every write goes through here. No exceptions, no special cases."""
    if not is_relative_to(path, project_root):
        raise SystemExit(f"ERROR: refusing to write outside the project root: {path}")


def assert_outside_source(path: Path, source_root: Path) -> None:
    """The second half of the same promise, stated from the other side.

    assert_inside_project already makes a write to the V drive impossible, so
    this is belt and braces - but the cost of being wrong here is writing to a
    read-only research archive, and two independent checks are cheap.
    """
    if is_relative_to(path, source_root):
        raise SystemExit(
            f"ERROR: refusing to write inside the source archive: {path}\n"
            "The source archive is read-only. This is not a recoverable condition.")


def setup_logging(log_path: Path, verbose: bool = False) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    LOGGER.setLevel(logging.DEBUG)
    LOGGER.handlers.clear()

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-8s %(message)s", "%Y-%m-%d %H:%M:%S"))
    LOGGER.addHandler(file_handler)

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    console.setFormatter(logging.Formatter("%(levelname)-8s %(message)s"))
    LOGGER.addHandler(console)


def utc_now() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def timestamp_slug() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


# ---------------------------------------------------------------------------
# Borrowed logic - imported, never re-implemented
# ---------------------------------------------------------------------------

def import_pipeline_modules():
    """Import the Phase 1-6 scripts as modules.

    The whole point of this phase is to run the EXISTING pipeline over a smaller
    set of files. A second implementation of the classification rules would
    drift from the first one within a month, and the drift would be invisible.
    """
    scripts_dir = Path(__file__).resolve().parent
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))

    import scan_source_archive as scanner          # noqa: E402
    import build_metadata_index as indexer         # noqa: E402
    return scanner, indexer


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

def connect(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise SystemExit(
            f"ERROR: no database at {db_path}.\n"
            "Phase 9 updates an existing index; it does not create one. Run Phase 1 first.")
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 60000")
    return conn


def create_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ingestion_runs (
            ingestion_run_id  INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at        TEXT,
            completed_at      TEXT,
            source_root       TEXT,
            ticker            TEXT,
            dry_run           TEXT,
            force_full        TEXT,
            files_seen        INTEGER,
            new_files         INTEGER,
            changed_files     INTEGER,
            unchanged_files   INTEGER,
            missing_files     INTEGER,
            skipped_files     INTEGER,
            metadata_added    INTEGER,
            metadata_updated  INTEGER,
            pdfs_extracted    INTEGER,
            extraction_failed INTEGER,
            cards_created     INTEGER,
            cards_failed      INTEGER,
            obsidian_updated  INTEGER,
            chunks_created    INTEGER,
            chunks_embedded   INTEGER,
            keyword_updated   TEXT,
            smoke_eval_status TEXT,
            status            TEXT,
            report_path       TEXT,
            error_message     TEXT,
            manifest_path     TEXT,
            json_path         TEXT,
            estimated_cost_usd REAL,
            pipeline_version  TEXT,
            options_json      TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ingestion_file_events (
            event_id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ingestion_run_id  INTEGER,
            document_id       TEXT,
            file_path         TEXT,
            relative_path     TEXT,
            extension         TEXT,
            file_size         INTEGER,
            modified_time     TEXT,
            old_hash          TEXT,
            new_hash          TEXT,
            change_status     TEXT,
            action_taken      TEXT,
            status            TEXT,
            error_message     TEXT,
            ticker_candidates TEXT,
            created_at        TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ingestion_events_run "
                 "ON ingestion_file_events(ingestion_run_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ingestion_events_status "
                 "ON ingestion_file_events(change_status)")

    existing = {row["name"] for row in conn.execute("PRAGMA table_info(documents)")}
    added = []
    for column in DOCUMENT_COLUMNS:
        if column not in existing:
            conn.execute(f"ALTER TABLE documents ADD COLUMN {column} TEXT")
            added.append(column)
    if added:
        LOGGER.info("Migrated documents table: added %d Phase 9 column(s): %s",
                    len(added), ", ".join(added))
    conn.commit()


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())


# ---------------------------------------------------------------------------
# PART A - change detection
# ---------------------------------------------------------------------------

def existing_documents(conn: sqlite3.Connection) -> dict:
    """Every indexed document, keyed by its normalised absolute path.

    Keyed on the path rather than the id so a file whose CONTENT changed is
    recognised as the same document. The id is derived from the relative path,
    so it stays stable for as long as the file stays where it is.
    """
    wanted = ["document_id", "file_path", "relative_path", "file_size_bytes",
              "modified_time", "file_hash_sha256"]
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(documents)")}
    select = ", ".join(c for c in wanted if c in columns)
    out = {}
    for row in conn.execute(f"SELECT {select} FROM documents"):
        key = normalise_path(row["file_path"] or "")
        if key:
            out[key] = dict(row)
    return out


def normalise_path(path: str) -> str:
    """Compare paths the way Windows does: separators and case folded."""
    return str(path or "").replace("\\", "/").rstrip("/").lower()


def classify_file(file_path: Path, record: dict, scanner, hash_all: bool) -> tuple:
    """Is this file new, changed or unchanged? Returns (status, new_hash, note).

    Cheap comparison first. Size and modified time settle the overwhelming
    majority of files at the cost of one stat() each, and hashing 11,697 PDFs
    over a network share to learn that none of them moved is not a daily job.
    The hash is computed only when the cheap comparison is inconclusive, or when
    --hash-all says to trust nothing.
    """
    try:
        stat = file_path.stat()
    except OSError as exc:
        return CHANGED, "", f"stat failed: {type(exc).__name__}: {exc}"

    size = int(stat.st_size)
    modified = scanner.to_iso(stat.st_mtime)

    old_size = record.get("file_size_bytes")
    old_modified = str(record.get("modified_time") or "")
    old_hash = str(record.get("file_hash_sha256") or "")

    cheap_match = (old_size is not None and int(old_size) == size
                   and old_modified and old_modified == modified)

    if cheap_match and not hash_all:
        return UNCHANGED, "", "same size and modified time"

    # Either something moved, or we were told not to trust the cheap check.
    # Only now is it worth reading the file.
    try:
        new_hash = scanner.sha256_file(file_path)
    except OSError as exc:
        return CHANGED, "", f"hash failed: {type(exc).__name__}: {exc}"

    if old_hash and new_hash == old_hash:
        # The metadata moved but the bytes did not - a copy, a touch, a restore
        # from backup. Nothing downstream needs redoing.
        return UNCHANGED, new_hash, "hash identical; only size/timestamp metadata differs"
    if not old_hash:
        return CHANGED, new_hash, "no hash on record to compare against"
    return CHANGED, new_hash, "content hash differs"


def scan_changes(source_root: Path, conn: sqlite3.Connection, scanner,
                 hash_all: bool, limit: int, force_full: bool) -> dict:
    """Walk the archive once and sort every file into a bucket."""
    known = existing_documents(conn)
    skip_dirs = set(scanner.SKIP_DIRS)
    extensions = set(scanner.SUPPORTED_EXTENSIONS)

    seen_keys = set()
    buckets = {NEW: [], CHANGED: [], UNCHANGED: [], MISSING: [], SKIPPED: []}
    stats = {"files_seen": 0, "walk": {}}

    LOGGER.info("Walking %s", source_root)
    walker = scanner.walk_supported_files(source_root, skip_dirs, extensions)
    started = time.time()

    for file_path, walk_stats in walker:
        stats["walk"] = walk_stats
        if file_path is None:          # generator finished; the stats are final
            break
        stats["files_seen"] += 1
        key = normalise_path(str(file_path))
        seen_keys.add(key)

        record = known.get(key)
        if record is None:
            buckets[NEW].append({"path": file_path, "record": None, "new_hash": "",
                                 "note": "not in the metadata index"})
        else:
            status, new_hash, note = classify_file(file_path, record, scanner, hash_all)
            if force_full and status == UNCHANGED:
                status, note = CHANGED, "--force-full: reprocessing regardless"
            buckets[status].append({"path": file_path, "record": record,
                                    "new_hash": new_hash, "note": note})

        if stats["files_seen"] % 2000 == 0:
            LOGGER.info("  ... %d files walked (%.0fs)", stats["files_seen"],
                        time.time() - started)

        if limit and (len(buckets[NEW]) + len(buckets[CHANGED])) >= limit:
            LOGGER.warning("Reached --limit of %d new/changed file(s). Stopping the walk here; "
                           "the rest of the archive was not examined.", limit)
            stats["limited"] = True
            break

    # Anything in the index that the walk did not see. REPORTED, never deleted.
    if not stats.get("limited"):
        for key, record in known.items():
            if key in seen_keys:
                continue
            if not _under_source(key, source_root):
                continue          # indexed from a different archive root - not this run's business
            buckets[MISSING].append({"path": Path(record["file_path"] or ""), "record": record,
                                     "new_hash": "", "note": "in the index, not on the drive"})

    LOGGER.info("Walk finished in %.1fs: %d file(s) seen.", time.time() - started,
                stats["files_seen"])
    return {"buckets": buckets, "stats": stats, "known_count": len(known)}


def _under_source(key: str, source_root: Path) -> bool:
    return key.startswith(normalise_path(str(source_root)) + "/")


# ---------------------------------------------------------------------------
# PART C - metadata for new and changed files
# ---------------------------------------------------------------------------

def build_records(entries: list, source_root: Path, scanner, rules: dict, ticker_rules: dict,
                  processed_at: str, archive_context: tuple) -> list:
    """Phase 1's own record builder, one file at a time."""
    records = []
    for entry in entries:
        try:
            record = scanner.build_record(
                entry["path"], source_root, rules, ticker_rules, 0, processed_at,
                archive_context)
        except Exception as exc:  # noqa: BLE001 - one bad file must not stop the run
            entry["error"] = f"{type(exc).__name__}: {exc}"
            LOGGER.error("Metadata FAILED for %s: %s", entry["path"], entry["error"])
            continue
        entry["record_built"] = record
        records.append(record)
    return records


def mark_stale(conn: sqlite3.Connection, document_id: str, old_hash: str, new_hash: str,
               run_id: int, change_status: str, seen_at: str) -> None:
    """A changed file invalidates everything downstream of it.

    The stale flags are set, not acted on. Whether the stale work is actually
    redone in this run depends on which stages the caller asked for - so a run
    that only refreshes metadata leaves an honest trail saying the extraction
    and the chunks are now behind.
    """
    conn.execute(
        """
        UPDATE documents
           SET last_seen_at = ?, last_ingested_at = ?, ingestion_run_id = ?,
               file_change_status = ?, content_hash_previous = ?, content_hash_current = ?,
               stale_extraction = '1', stale_document_card = '1', stale_rag_index = '1'
         WHERE document_id = ?
        """,
        (seen_at, seen_at, str(run_id), change_status, old_hash, new_hash, document_id))


def mark_fresh(conn: sqlite3.Connection, document_ids: list, run_id: int, change_status: str,
               seen_at: str) -> None:
    """A new file is current by definition - nothing downstream exists to be stale."""
    for document_id in document_ids:
        conn.execute(
            """
            UPDATE documents
               SET last_seen_at = ?, last_ingested_at = ?, ingestion_run_id = ?,
                   file_change_status = ?, content_hash_current = COALESCE(file_hash_sha256, ''),
                   stale_extraction = '0', stale_document_card = '0', stale_rag_index = '0'
             WHERE document_id = ?
            """,
            (seen_at, seen_at, str(run_id), change_status, document_id))


def touch_unchanged(conn: sqlite3.Connection, entries: list, seen_at: str) -> int:
    """Record that an unchanged file was still there, and nothing else.

    last_seen_at is the difference between "this document is gone" and "nobody
    has looked recently", and that distinction is the whole reason the missing
    bucket is safe to report.
    """
    ids = [e["record"]["document_id"] for e in entries if e.get("record")]
    for chunk_start in range(0, len(ids), 500):
        batch = ids[chunk_start:chunk_start + 500]
        marks = ",".join("?" for _ in batch)
        conn.execute(
            f"UPDATE documents SET last_seen_at = ?, file_change_status = ? "
            f"WHERE document_id IN ({marks})", [seen_at, UNCHANGED] + batch)
    return len(ids)


def mark_missing(conn: sqlite3.Connection, entries: list, run_id: int, seen_at: str) -> int:
    """Flag, never delete.

    A file can disappear because it was deleted, or because the share was
    unmounted, or because a folder was renamed while the walk was running. Only
    one of those is a reason to forget a document, and this script cannot tell
    which one happened.
    """
    for entry in entries:
        record = entry.get("record") or {}
        conn.execute(
            "UPDATE documents SET file_change_status = ?, ingestion_run_id = ? "
            "WHERE document_id = ?",
            (MISSING, str(run_id), record.get("document_id", "")))
    return len(entries)


# ---------------------------------------------------------------------------
# PART D - stages, run over the affected documents only
# ---------------------------------------------------------------------------

def release_db(conn) -> None:
    """Let go of the database before handing control to a child process.

    PHASE 9.1, AND THE WHOLE REASON THIS PHASE EXISTS.

    Python's sqlite3 opens a write transaction on the first INSERT or UPDATE and
    holds it until commit. The Phase 9 extract loop updated a document row and
    committed only after the WHOLE loop - so from the first successful document
    onward the parent held a RESERVED lock, and every subsequent child died at
    its first write with "database is locked".

    That first write is create_extraction_schema(), which is why the traceback
    pointed at a schema function that has nothing wrong with it. The signature
    was unmistakable in hindsight: one document extracted, then seventy
    failures. A genuine schema fault would have failed the first one too.

    Every subprocess launch now goes through here first.
    """
    if conn is None:
        return
    try:
        conn.commit()
    except sqlite3.Error:
        pass


def run_script(script: Path, args: list, project_root: Path, timeout: int = 3600,
               conn=None) -> dict:
    """Run one pipeline script as a subprocess.

    An argument LIST with shell=False, exactly as Phase 7A launches Ask Brain: a
    file name containing a quote or a semicolon is one argument, never a
    command. UTF-8 is forced into the child so a Windows console cannot make a
    completed stage look like a failed one (Phase 7A.1).

    The database is released before the child starts. Always.
    """
    release_db(conn)
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    command = [sys.executable, str(script)] + [str(a) for a in args]
    LOGGER.debug("Running: %s", " ".join(command))
    try:
        completed = subprocess.run(
            command, cwd=str(project_root), env=env, capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=timeout, shell=False)
        return {"returncode": completed.returncode, "stdout": completed.stdout or "",
                "stderr": completed.stderr or "", "error": "",
                "command": " ".join(command)}
    except subprocess.TimeoutExpired:
        return {"returncode": -1, "stdout": "", "stderr": "",
                "error": f"timed out after {timeout}s", "command": " ".join(command)}
    except Exception as exc:  # noqa: BLE001
        return {"returncode": -1, "stdout": "", "stderr": "",
                "error": f"{type(exc).__name__}: {exc}", "command": " ".join(command)}


class FailureLog:
    """Every failed subprocess, in full, in one file.

    Phase 9.1. A 300-character fragment of stderr told us the failure was at
    `create_extraction_schema(conn)` and nothing about WHY - which sent the
    diagnosis after an imaginary schema bug instead of the real lock. The
    database column keeps a useful summary; this file keeps the whole thing,
    every time, including the exact command so it can be re-run by hand.
    """

    def __init__(self, path: Path, project_root: Path, source_root: Path):
        assert_inside_project(path, project_root)
        assert_outside_source(path, source_root)
        self.path = path
        self.count = 0
        self.opened = False

    def record(self, stage: str, document_id: str, relative_path: str, result: dict) -> str:
        """Write the full failure and return the summary for the event row."""
        self.count += 1
        stdout = (result.get("stdout") or "").rstrip()
        stderr = (result.get("stderr") or "").rstrip()
        block = [
            "=" * 78,
            f"FAILURE {self.count}  stage={stage}  document_id={document_id}",
            f"file       : {relative_path}",
            f"command    : {result.get('command', '')}",
            f"returncode : {result.get('returncode')}",
            f"error      : {result.get('error', '')}",
            "-" * 78,
            "STDERR (complete):",
            stderr or "(empty)",
            "-" * 78,
            "STDOUT (complete):",
            stdout or "(empty)",
            "",
        ]
        try:
            if not self.opened:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_text(
                    f"Ingestion failures - {PIPELINE_VERSION} - {utc_now()}\n"
                    "Every failed subprocess below is recorded in full: the exact command, its\n"
                    "complete stderr and its complete stdout. Nothing here is truncated.\n\n",
                    encoding="utf-8")
                self.opened = True
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write("\n".join(block))
        except OSError as exc:  # noqa: BLE001 - logging a failure must not cause one
            LOGGER.warning("Could not write the failure log (%s).", type(exc).__name__)

        return summarise_failure(result)


# Finding the message in a page of logging.
#
# Phase 9.1 exists partly because a truncated fragment named
# `create_extraction_schema(conn)` and hid `database is locked`. So the rule
# here is to SEARCH for the message rather than take whatever came last: a
# child script logs its failure in the middle of a page of INFO lines, and its
# closing banner - "failed : 1", "wrote true_failed_extractions.csv" - contains
# the word "failed" without saying anything about why.
RE_LOG_ERROR = re.compile(r"^\s*(ERROR|CRITICAL|FATAL)\b", re.I)
RE_EXCEPTION = re.compile(r"^\s*(\w+(?:\.\w+)*(?:Error|Exception|Warning))\s*:", re.M)
RE_TRACEBACK = re.compile(r"^Traceback \(most recent call last\)", re.M)

TROUBLE_WORDS = ("locked", "denied", "permission", "no such", "cannot", "could not",
                 "refus", "timed out", "missing", "corrupt", "invalid", "unable")


def failure_lines(text: str) -> list:
    """The lines that say what went wrong, best first.

    Three tiers, checked in order:
      1. an exception line from a traceback - the last unindented `XError: ...`;
      2. a line logged at ERROR level or above;
      3. a line containing a word that only appears when something is wrong.

    A summary counter ("failed : 1") matches none of these, which is the point.
    """
    lines = [line.rstrip() for line in (text or "").splitlines() if line.strip()]
    if not lines:
        return []

    exceptions = [line.strip() for line in lines if RE_EXCEPTION.match(line)]
    if exceptions:
        return exceptions[-1:] + [line.strip() for line in lines
                                  if RE_LOG_ERROR.match(line)][:2]

    errors = [line.strip() for line in lines if RE_LOG_ERROR.match(line)]
    if errors:
        return errors[:3]

    trouble = [line.strip() for line in lines
               if any(word in line.lower() for word in TROUBLE_WORDS)]
    return trouble[:3]


def summarise_failure(result: dict) -> str:
    """One line that names the cause, plus a little context."""
    stderr = (result.get("stderr") or "").strip()
    stdout = (result.get("stdout") or "").strip()

    found = failure_lines(stderr) or failure_lines(stdout)
    if found:
        return (found[0] + ("  ||  " + " / ".join(found[1:]) if len(found) > 1 else ""))[:900]

    if result.get("error"):
        return str(result["error"])[:900]
    if stderr:
        return ("stderr tail: " + " / ".join(stderr.splitlines()[-3:]))[:900]
    if stdout:
        return ("no stderr; stdout tail: " + " / ".join(stdout.splitlines()[-3:]))[:900]
    return f"exit code {result.get('returncode')} with no output"


def stage_extract(entries: list, scripts_dir: Path, project_root: Path, db_arg: str,
                  conn: sqlite3.Connection, failures: "FailureLog" = None) -> dict:
    """Phase 2 text extraction, per affected document.

    Per document rather than in batch, for two reasons. The batch path skips a
    document whose output file already exists, which is exactly wrong for a
    CHANGED file; and a per-document run gives each file its own exit status to
    record against its event row.
    """
    script = scripts_dir / "extract_pdf_text.py"
    stats = {"extracted": 0, "failed": 0, "skipped_non_pdf": 0}
    if not script.exists():
        LOGGER.error("extract_pdf_text.py not found - skipping extraction.")
        return stats

    for entry in entries:
        document_id = entry.get("document_id")
        if not document_id:
            continue
        record = entry.get("record_built") or {}
        entry.setdefault("relative_path",
                         record.get("relative_path")
                         or (entry.get("record") or {}).get("relative_path")
                         or (str(entry.get("path", "")) or ""))
        if str(entry.get("extension", "")).lower() != ".pdf":
            stats["skipped_non_pdf"] += 1
            entry.setdefault("actions", []).append("extraction skipped (not a PDF)")
            continue

        # The child is about to write to this database. Nothing of ours may be
        # holding it. (Phase 9.1: this single line is what broke run 2.)
        result = run_script(script, [
            "--db", db_arg, "--project-root", str(project_root),
            "--only-document-id", document_id, "--force",
        ], project_root, conn=conn)

        status = str(conn.execute(
            "SELECT COALESCE(pdf_extraction_status,'') FROM documents WHERE document_id = ?",
            (document_id,)).fetchone()[0] or "")

        if result["returncode"] == 0 and status.startswith("extracted"):
            stats["extracted"] += 1
            entry.setdefault("actions", []).append("text extracted")
            conn.execute("UPDATE documents SET stale_extraction = '0' WHERE document_id = ?",
                         (document_id,))
            conn.commit()
        elif status.startswith("skipped"):
            entry.setdefault("actions", []).append(f"extraction {status}")
            conn.execute("UPDATE documents SET stale_extraction = '0' WHERE document_id = ?",
                         (document_id,))
            conn.commit()
        else:
            stats["failed"] += 1
            entry["error"] = (
                failures.record("extraction", document_id, entry.get("relative_path", ""), result)
                if failures is not None else summarise_failure(result))
            entry["failed_stage"] = "extraction"
            entry.setdefault("actions", []).append("extraction FAILED")
            LOGGER.error("Extraction failed for %s: %s", document_id, entry["error"][:300])
    conn.commit()
    return stats


def stage_cards(entries: list, scripts_dir: Path, project_root: Path, db_arg: str,
                conn: sqlite3.Connection, failures: "FailureLog" = None) -> dict:
    """Phase 3 document cards, per affected document."""
    script = scripts_dir / "build_document_cards.py"
    stats = {"created": 0, "failed": 0}
    if not script.exists():
        LOGGER.error("build_document_cards.py not found - skipping cards.")
        return stats

    for entry in entries:
        document_id = entry.get("document_id")
        if not document_id or entry.get("error"):
            continue
        record = entry.get("record_built") or {}
        entry.setdefault("relative_path",
                         record.get("relative_path")
                         or (entry.get("record") or {}).get("relative_path")
                         or (str(entry.get("path", "")) or ""))
        result = run_script(script, [
            "--db", db_arg, "--project-root", str(project_root),
            "--only-document-id", document_id, "--force",
        ], project_root, conn=conn)

        status = str(conn.execute(
            "SELECT COALESCE(document_card_status,'') FROM documents WHERE document_id = ?",
            (document_id,)).fetchone()[0] or "")

        if result["returncode"] == 0 and status and not status.startswith("failed"):
            stats["created"] += 1
            entry.setdefault("actions", []).append(f"card {status or 'built'}")
            conn.execute("UPDATE documents SET stale_document_card = '0' WHERE document_id = ?",
                         (document_id,))
            conn.commit()
        else:
            stats["failed"] += 1
            entry["error"] = entry.get("error") or (
                failures.record("card", document_id, entry.get("relative_path", ""), result)
                if failures is not None else summarise_failure(result))
            entry["failed_stage"] = entry.get("failed_stage") or "card"
            entry.setdefault("actions", []).append("card FAILED")
            LOGGER.error("Card failed for %s: %s", document_id, entry["error"][:300])
    conn.commit()
    return stats


def stage_obsidian(scripts_dir: Path, project_root: Path, db_arg: str,
                   conn: sqlite3.Connection) -> dict:
    """Phase 4 vault refresh.

    The vault builder has no per-document switch, and it should not: its indexes
    and company pages are aggregates, and an aggregate cannot be updated one
    document at a time. It is already incremental in the way that matters - it
    compares each note against what is on disk and rewrites only what differs.
    """
    script = scripts_dir / "build_obsidian_memory.py"
    stats = {"notes_after": 0, "notes_before": 0, "updated": 0, "failed": False}
    if not script.exists():
        LOGGER.error("build_obsidian_memory.py not found - skipping the vault.")
        return stats

    def count_notes() -> int:
        if not table_exists(conn, "obsidian_notes"):
            return 0
        return int(conn.execute("SELECT COUNT(*) FROM obsidian_notes").fetchone()[0] or 0)

    stats["notes_before"] = count_notes()
    result = run_script(script, ["--db", db_arg, "--project-root", str(project_root)],
                        project_root, timeout=7200, conn=conn)
    stats["notes_after"] = count_notes()
    stats["updated"] = max(0, stats["notes_after"] - stats["notes_before"])
    if result["returncode"] != 0:
        stats["failed"] = True
        LOGGER.error("Obsidian refresh failed: %s",
                     (result["error"] or result["stderr"][-300:])[:300])
    return stats


# ---------------------------------------------------------------------------
# PART E - MSFT incremental RAG
# ---------------------------------------------------------------------------

def open_existing_collection(project_root: Path, ticker: str, collection_name: str):
    """get_collection, never get_or_create_collection.

    Phase 9 adds to an index that Phase 5A built. If the collection is not
    there, that is not something to paper over by creating an empty one - it
    means the index this run was going to extend does not exist, and the right
    response is to stop and say so.
    """
    try:
        import chromadb
    except ImportError as exc:
        raise SystemExit(
            "ERROR: chromadb is not installed. pip install -r requirements.txt "
            f"({exc})") from exc

    path = project_root / "06_RAG_Index" / "chroma" / ticker
    if not path.exists():
        raise SystemExit(
            f"ERROR: no Chroma store at {path}.\n"
            "Phase 9 extends the index Phase 5A built; it does not create one. "
            "Run build_rag_index.py first.")

    client = chromadb.PersistentClient(path=str(path))
    try:
        return client.get_collection(name=collection_name)
    except Exception as exc:  # noqa: BLE001 - chroma raises its own type here
        raise SystemExit(
            f"ERROR: Chroma collection '{collection_name}' does not exist ({type(exc).__name__}).\n"
            "Phase 5A must have run for this ticker before Phase 9 can add to it.") from exc


def optional_collection(project_root: Path, ticker: str, collection_name: str):
    """The collection if it is there, None if it is not - never a new one.

    Chunking on its own does not require a vector store. Embedding does, and
    stage_embed uses the strict version that stops the run.
    """
    try:
        return open_existing_collection(project_root, ticker, collection_name)
    except SystemExit as exc:
        LOGGER.warning("No existing vector collection to update (%s). Chunking will continue; "
                       "run --embed-new once Phase 5A has built one.", str(exc).splitlines()[0])
        return None


# --- Phase 9.2: why a document is not being chunked --------------------------
#
# Phase 9 set stale_rag_index on every changed document and cleared it only for
# documents that were successfully embedded. A document that is not RAG-eligible
# at all - a duplicate, a spreadsheet, one its card excludes - was therefore
# marked stale forever: every run rediscovered it, reported "10 documents are
# marked stale_rag_index", and recommended the same command, which then found
# nothing to chunk. A flag that nobody can clear and nobody can explain is worse
# than no flag, because it trains a reader to ignore the one that matters.
#
# So every stale document now lands in exactly one bucket, with a reason.
RAG_ELIGIBLE = "eligible"
RAG_NOT_ELIGIBLE = "not_eligible"
RAG_MISSING_SOURCE = "missing_source"
RAG_NOT_READY = "not_ready"


def why_not_eligible(row: dict, ticker: str) -> str:
    """The specific rule that excludes this document, in the order it is applied.

    Deliberately specific. "Not eligible" is not an answer a person can act on;
    "its card excludes it: client_data" is.
    """
    reasons = []
    if str(row.get("duplicate_status") or "") == "duplicate":
        reasons.append(f"marked a duplicate of {row.get('duplicate_of') or 'another file'}")
    if str(row.get("review_severity") or "") == "High":
        reasons.append(f"review severity High ({row.get('review_category') or 'unclassified'})")
    if str(row.get("usable_for_rag") or "0") not in ("1", 1):
        reasons.append("its document card says it is not usable for RAG")
    exclusion = str(row.get("rag_exclusion_reason") or "")
    if exclusion and exclusion != "none":
        reasons.append(f"card exclusion reason: {exclusion}")
    quality = str(row.get("pdf_extraction_quality") or "")
    if quality in ("Failed", "Empty"):
        reasons.append(f"extraction quality {quality}")
    extension = str(row.get("file_extension") or "").lower()
    if extension and extension != ".pdf":
        reasons.append(f"not a PDF ({extension})")
    tickers = f",{str(row.get('ticker_candidates_clean') or '').upper()},"
    if f",{ticker.upper()}," not in tickers:
        reasons.append(f"no longer tagged {ticker.upper()}")
    return "; ".join(reasons) or "excluded by the RAG eligibility rules"


def classify_stale_rag(conn: sqlite3.Connection, ticker: str, document_ids, rag) -> dict:
    """Sort stale documents into what can be done about each one.

    Checked in this order, because the first true statement is the one a person
    needs to act on: a missing source cannot be re-read, an unextracted file
    cannot be chunked, and only then does eligibility matter.
    """
    buckets = {RAG_ELIGIBLE: [], RAG_NOT_ELIGIBLE: [], RAG_MISSING_SOURCE: [],
               RAG_NOT_READY: []}
    ids = [d for d in document_ids if d]
    if not ids:
        return buckets

    eligible_rows = {row["document_id"]: row for row in rag.select_documents(conn, ticker, 0)}
    has_cards = table_exists(conn, "document_cards")

    for start in range(0, len(ids), 400):
        batch = ids[start:start + 400]
        marks = ",".join("?" for _ in batch)
        card_join = ("LEFT JOIN document_cards c ON c.document_id = d.document_id"
                     if has_cards else "")
        card_column = "COALESCE(c.rag_exclusion_reason,'') AS rag_exclusion_reason" \
            if has_cards else "'' AS rag_exclusion_reason"
        rows = conn.execute(
            f"""
            SELECT d.*, {card_column}
              FROM documents d
              {card_join}
             WHERE d.document_id IN ({marks})
            """, batch).fetchall()

        for record in rows:
            row = dict(record)
            document_id = row["document_id"]

            if str(row.get("file_change_status") or "") == MISSING:
                buckets[RAG_MISSING_SOURCE].append(row)
                continue

            not_ready = []
            if str(row.get("stale_extraction") or "0") == "1":
                not_ready.append("extraction is stale")
            if str(row.get("stale_document_card") or "0") == "1":
                not_ready.append("document card is stale")
            status = str(row.get("pdf_extraction_status") or "")
            if str(row.get("file_extension") or "").lower() == ".pdf" and not status:
                not_ready.append("never extracted")
            if not_ready:
                row["_reason"] = "; ".join(not_ready)
                buckets[RAG_NOT_READY].append(row)
                continue

            if document_id in eligible_rows:
                row["_eligible_row"] = eligible_rows[document_id]
                buckets[RAG_ELIGIBLE].append(row)
            else:
                row["_reason"] = why_not_eligible(row, ticker)
                buckets[RAG_NOT_ELIGIBLE].append(row)

    return buckets


def clear_other_ticker_stale(conn: sqlite3.Connection, rows: list, indexed_ticker: str) -> list:
    """Account for stale flags belonging to tickers nothing indexes yet.

    Phase 9.3. These were the quiet half of the loop: a document tagged JPM with
    stale_rag_index set is reported in every count, matched by no ticker's
    worklist, and cleared by nothing. It is not waiting for work - there is no
    JPM index for it to be behind - so the flag is cleared with that stated
    plainly, and the count finally means something.
    """
    cleared = []
    for row in rows:
        tickers = row.get("tickers") or "(no ticker)"
        reason = (f"rag_skipped_not_eligible: tagged {tickers}, and only "
                  f"{indexed_ticker.upper()} is indexed in this phase - there is no index for "
                  "this ticker for the document to be behind. When that ticker is first indexed, "
                  "every eligible document is indexed regardless of this flag.")
        conn.execute(
            "UPDATE documents SET stale_rag_index = '0', rag_skip_reason = ? "
            "WHERE document_id = ?", (reason[:500], row["document_id"]))
        cleared.append({"document_id": row["document_id"],
                        "relative_path": row.get("relative_path", ""),
                        "reason": f"tagged {tickers}; not indexed in this phase"})
    if cleared:
        conn.commit()
        LOGGER.info("Cleared %d stale RAG flag(s) on documents belonging to other tickers.",
                    len(cleared))
    return cleared


def live_chunk_count(conn: sqlite3.Connection, document_id: str, ticker: str) -> int:
    """Chunks of this document that retrieval can still return."""
    try:
        return int(conn.execute(
            "SELECT COUNT(*) FROM rag_chunks WHERE document_id = ? AND ticker = ? "
            "AND embedding_status = 'embedded'", (document_id, ticker)).fetchone()[0] or 0)
    except sqlite3.OperationalError:
        return 0


def resolve_stale_rag(conn: sqlite3.Connection, buckets: dict, ticker: str) -> dict:
    """Clear what can be cleared, hold what must be held, and say which is which.

    The only flags cleared here are on documents that RAG will never index,
    however many times the command is run. Everything else keeps its flag,
    because the work behind it is genuinely outstanding.
    """
    resolved = {"cleared": [], "held_missing": [], "held_not_ready": [],
                "not_eligible_with_chunks": []}
    now = utc_now()

    def is_stale(row: dict) -> bool:
        """Only documents that actually carry the flag are resolved here.

        classify_stale_rag() is given every AFFECTED document, because the
        eligible bucket is what chunking works from - and a brand-new document
        is eligible without ever having been stale. But writing a skip reason
        onto a document that was never flagged would invent a problem and
        inflate the "cleared" count into something meaningless.
        """
        return str(row.get("stale_rag_index") or "0") == "1"

    for row in buckets[RAG_NOT_ELIGIBLE]:
        if not is_stale(row):
            continue
        document_id = row["document_id"]
        reason = row.get("_reason", "")
        live = live_chunk_count(conn, document_id, ticker)
        if live:
            # It is not eligible NOW but its old chunks are still retrievable.
            # The flag is cleared as asked, and the inconsistency is written
            # down rather than buried: a person has to decide whether those
            # chunks should still be answerable.
            reason = (f"{reason}; NOTE: {live} embedded chunk(s) from this document are still "
                      "in the index and can still be retrieved - review whether they should be")
            resolved["not_eligible_with_chunks"].append({**row, "_reason": reason,
                                                         "_live_chunks": live})
        conn.execute(
            "UPDATE documents SET stale_rag_index = '0', rag_skip_reason = ?, "
            "last_ingested_at = ? WHERE document_id = ?",
            (f"rag_skipped_not_eligible: {reason}"[:500], now, document_id))
        resolved["cleared"].append({**row, "_reason": reason})

    for row in buckets[RAG_MISSING_SOURCE]:
        if not is_stale(row):
            continue
        document_id = row["document_id"]
        live = live_chunk_count(conn, document_id, ticker)
        note = ("source file missing; RAG state not changed"
                + (f"; {live} embedded chunk(s) left untouched" if live else ""))
        # The flag STAYS. A missing file is the one case where doing nothing is
        # the correct action: the share may simply have been unavailable, and
        # no automatic decision about its chunks can be justified from here.
        conn.execute(
            "UPDATE documents SET rag_skip_reason = ? WHERE document_id = ?",
            (f"missing_source_needs_review: {note}"[:500], document_id))
        resolved["held_missing"].append({**row, "_reason": note, "_live_chunks": live})

    for row in buckets[RAG_NOT_READY]:
        if not is_stale(row):
            continue
        conn.execute(
            "UPDATE documents SET rag_skip_reason = ? WHERE document_id = ?",
            (f"rag_blocked_not_ready: {row.get('_reason', '')}"[:500], row["document_id"]))
        resolved["held_not_ready"].append(row)

    conn.commit()
    return resolved


def stage_chunk(conn: sqlite3.Connection, project_root: Path, ticker: str,
                affected_ids: set, rag) -> dict:
    """Chunk only the affected, RAG-eligible documents for one ticker.

    Old chunks belonging to a changed document are marked superseded rather than
    deleted. Retrieval already filters on embedding_status, so a superseded
    chunk stops being retrievable the moment it is marked - but the row, and the
    text it held, survive for anyone auditing what an older answer was built on.
    """
    stats = {"documents_considered": 0, "documents_chunked": 0, "chunks_created": 0,
             "chunks_superseded": 0, "skipped_not_eligible": 0, "vectors_removed": 0,
             "stale_cleared": 0, "held_missing": 0, "held_not_ready": 0,
             "not_eligible_with_chunks": 0, "resolved": {}, "buckets": {},
             "other_ticker_cleared": [], "carried_over": 0}

    # Before anything else. The version column is what stops a re-chunked
    # document from writing over the very rows it just superseded - and an
    # INSERT OR REPLACE that lands on an existing chunk_id destroys the audit
    # trail silently, which is the worst way to lose it.
    _ensure_chunk_version_column(conn)

    # Phase 9.2. Every affected document is accounted for before any chunking
    # happens, and the ones RAG will never index have their flag cleared here -
    # not left set for the next run to rediscover and report again.
    buckets = classify_stale_rag(conn, ticker, sorted(affected_ids), rag)
    resolved = resolve_stale_rag(conn, buckets, ticker)
    stats["buckets"] = {name: [{"document_id": r["document_id"],
                                "relative_path": r.get("relative_path", ""),
                                "reason": r.get("_reason", ""),
                                "stale": str(r.get("stale_rag_index") or "0") == "1"}
                               for r in rows] for name, rows in buckets.items()}
    stats["resolved"] = {name: [{"document_id": r["document_id"],
                                 "relative_path": r.get("relative_path", ""),
                                 "reason": r.get("_reason", ""),
                                 "live_chunks": r.get("_live_chunks", 0)}
                                for r in rows] for name, rows in resolved.items()}
    stats["stale_cleared"] = len(resolved["cleared"])
    stats["held_missing"] = len(resolved["held_missing"])
    stats["held_not_ready"] = len(resolved["held_not_ready"])
    stats["not_eligible_with_chunks"] = len(resolved["not_eligible_with_chunks"])
    stats["documents_considered"] = len(affected_ids)
    stats["skipped_not_eligible"] = len(buckets[RAG_NOT_ELIGIBLE])
    stats["carried_over"] = len([r for name, rows in buckets.items() for r in rows
                                 if str(r.get("stale_rag_index") or "0") == "1"])

    if stats["stale_cleared"]:
        LOGGER.info("Cleared stale_rag_index on %d document(s) that RAG will never index "
                    "(each one's reason is recorded).", stats["stale_cleared"])
    if stats["held_missing"]:
        LOGGER.warning("%d document(s) kept their stale flag because the source file is "
                       "missing. Nothing was changed for them.", stats["held_missing"])
    if stats["held_not_ready"]:
        LOGGER.warning("%d document(s) kept their stale flag because extraction or the card "
                       "is not finished. Repair those first.", stats["held_not_ready"])

    targets = [row["_eligible_row"] for row in buckets[RAG_ELIGIBLE]]

    if not targets:
        LOGGER.info("No affected %s document is RAG-eligible. Nothing to chunk.", ticker)
        return stats

    settings = rag.load_environment(project_root)
    tokenizer = rag.Tokenizer(settings["model"])
    collection_name = rag.collection_name_for(ticker)
    now = utc_now()

    collection = optional_collection(project_root, ticker, collection_name)

    records = []
    for row in targets:
        text_path = rag.resolve_text_path(row, project_root)
        if text_path is None:
            LOGGER.warning("No extracted text on disk for %s - skipping.", row["document_id"])
            continue

        # What this document had before, and what version to write next.
        previous_ids = [r[0] for r in conn.execute(
            "SELECT chunk_id FROM rag_chunks WHERE document_id = ? AND ticker = ? "
            "AND embedding_status <> 'superseded'", (row["document_id"], ticker))]
        highest = int(conn.execute(
            "SELECT COALESCE(MAX(COALESCE(chunk_version, 1)), 0) FROM rag_chunks "
            "WHERE document_id = ? AND ticker = ?",
            (row["document_id"], ticker)).fetchone()[0] or 0)
        version = highest + 1 if highest else 1

        superseded = conn.execute(
            "UPDATE rag_chunks SET embedding_status = 'superseded' "
            "WHERE document_id = ? AND ticker = ? AND embedding_status <> 'superseded'",
            (row["document_id"], ticker)).rowcount
        if superseded:
            stats["chunks_superseded"] += superseded
            LOGGER.info("Marked %d existing chunk(s) superseded for %s.",
                        superseded, row["document_id"])

        # The ROW stays - that is the audit record of what an older answer was
        # built on. The VECTOR goes, because a vector is a derived artifact and
        # this one now describes text that is no longer in the file. An answer
        # citing it would send a person to a PDF that does not say what the
        # citation claims, and a citation that does not check out is worse than
        # no answer at all.
        if previous_ids and collection is not None:
            try:
                collection.delete(ids=previous_ids)
                stats["vectors_removed"] += len(previous_ids)
                LOGGER.info("Removed %d superseded vector(s) from the collection for %s.",
                            len(previous_ids), row["document_id"])
            except Exception as exc:  # noqa: BLE001 - a missing id is not a failure
                LOGGER.warning("Could not remove superseded vectors for %s (%s). The rows are "
                               "marked superseded either way.", row["document_id"],
                               type(exc).__name__)

        try:
            raw = text_path.read_text(encoding="utf-8", errors="replace")
            doc_chunks = rag.chunk_document(raw, tokenizer, rag.DEFAULT_CHUNK_SIZE,
                                            rag.DEFAULT_CHUNK_OVERLAP)
        except Exception as exc:  # noqa: BLE001
            LOGGER.error("Chunking FAILED for %s: %s", row["document_id"], exc)
            continue
        if not doc_chunks:
            LOGGER.warning("No usable text after cleaning: %s", row["document_id"])
            continue

        for chunk in doc_chunks:
            suffix = "" if version <= 1 else f"_v{version}"
            records.append({
                "chunk_id": f"{row['document_id']}_c{chunk['chunk_index']:04d}{suffix}",
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
                "embedding_status": rag.STATUS_PENDING,
                "embedded_at": "",
                "chroma_collection": collection_name,
                "created_at": now,
                "chunk_version": version,
            })
        stats["documents_chunked"] += 1

    if records:
        rag.upsert_chunks(conn, [{k: v for k, v in r.items() if k != "chunk_version"}
                                 for r in records])
        for record in records:
            # Phase 12: scoped by ticker - since Phase 11.3 the same chunk_id
            # can exist under two tickers, and an unscoped UPDATE would touch
            # the other ticker's row.
            conn.execute("UPDATE rag_chunks SET chunk_version = ? "
                         " WHERE chunk_id = ? AND ticker = ?",
                         (record["chunk_version"], record["chunk_id"], record["ticker"]))
        conn.commit()
    stats["chunks_created"] = len(records)
    LOGGER.info("Chunked %d document(s) into %d new chunk(s).",
                stats["documents_chunked"], stats["chunks_created"])
    return stats


def _has_chunk_version(conn: sqlite3.Connection) -> bool:
    return "chunk_version" in {row["name"] for row in conn.execute(
        "PRAGMA table_info(rag_chunks)")}


def _ensure_chunk_version_column(conn: sqlite3.Connection) -> None:
    if not _has_chunk_version(conn):
        conn.execute("ALTER TABLE rag_chunks ADD COLUMN chunk_version INTEGER")
        conn.commit()


def stage_embed(conn: sqlite3.Connection, project_root: Path, ticker: str, rag,
                batch_size: int = 64) -> dict:
    """Embed the pending chunks, and only the pending chunks.

    "Pending" is a database fact, not a guess: a chunk reaches this function
    only if stage_chunk just created it, or an earlier run created it and failed
    to embed it. An embedded chunk is never re-sent, which is the difference
    between a daily update costing a fraction of a cent and costing what the
    whole index cost to build.
    """
    stats = {"chunks_embedded": 0, "chunks_failed": 0, "batches": 0, "tokens": 0,
             "estimated_cost_usd": 0.0, "added_to_chroma": 0}

    pending = list(conn.execute(
        "SELECT chunk_id, chunk_text, chunk_token_count, document_id, chunk_index, file_name, "
        "relative_path, folder_date_guess, source_type_guess, document_type_guess, "
        "page_start_guess, page_end_guess, archive_year, detected_year, detected_month, "
        "strategy_theme_guess, source_vendor_candidates "
        "FROM rag_chunks WHERE ticker = ? AND embedding_status = ? ORDER BY chunk_id",
        (ticker, rag.STATUS_PENDING)))

    if not pending:
        LOGGER.info("No pending %s chunk to embed. Nothing was sent to OpenAI.", ticker)
        return stats

    settings = rag.load_environment(project_root)
    client = rag.make_openai_client(settings)
    collection_name = rag.collection_name_for(ticker)
    collection = open_existing_collection(project_root, ticker, collection_name)
    before = collection.count()
    now = utc_now()

    LOGGER.info("Embedding %d pending chunk(s) into the existing collection '%s' "
                "(currently %d vectors).", len(pending), collection_name, before)

    for start in range(0, len(pending), batch_size):
        batch = pending[start:start + batch_size]
        texts = [row["chunk_text"] or "" for row in batch]
        try:
            vectors = rag.embed_batch(client, settings["model"], texts)
        except Exception as exc:  # noqa: BLE001 - one bad batch must not lose the rest
            LOGGER.error("Embedding batch failed (%s). Those chunks stay pending.",
                         type(exc).__name__)
            stats["chunks_failed"] += len(batch)
            for row in batch:
                conn.execute("UPDATE rag_chunks SET embedding_status = ? "
                             " WHERE chunk_id = ? AND ticker = ?",
                             (rag.STATUS_FAILED, row["chunk_id"], ticker))
            conn.commit()
            continue

        collection.add(
            ids=[row["chunk_id"] for row in batch],
            embeddings=vectors,
            documents=texts,
            metadatas=[{
                "document_id": row["document_id"] or "",
                "chunk_index": int(row["chunk_index"] or 0),
                "file_name": row["file_name"] or "",
                "relative_path": row["relative_path"] or "",
                "folder_date_guess": row["folder_date_guess"] or "",
                "source_type_guess": row["source_type_guess"] or "",
                "document_type_guess": row["document_type_guess"] or "",
                "page_start_guess": int(row["page_start_guess"] or 0),
                "page_end_guess": int(row["page_end_guess"] or 0),
                "archive_year": row["archive_year"] or "",
                "detected_year": row["detected_year"] or "",
                "detected_month": row["detected_month"] or "",
                "strategy_theme_guess": row["strategy_theme_guess"] or "",
                "source_vendor_candidates": row["source_vendor_candidates"] or "",
                "ticker": ticker,
            } for row in batch],
        )

        for row in batch:
            conn.execute(
                "UPDATE rag_chunks SET embedding_status = ?, embedded_at = ? "
                " WHERE chunk_id = ? AND ticker = ?",
                (rag.STATUS_EMBEDDED, now, row["chunk_id"], ticker))
        conn.commit()

        stats["batches"] += 1
        stats["chunks_embedded"] += len(batch)
        stats["tokens"] += sum(int(row["chunk_token_count"] or 0) for row in batch)
        LOGGER.info("  batch %d: %d chunk(s) embedded.", stats["batches"], len(batch))

    stats["added_to_chroma"] = collection.count() - before
    stats["estimated_cost_usd"] = round(
        stats["tokens"] / 1_000_000.0 * float(settings.get("cost_per_1m") or 0.0), 6)
    LOGGER.info("Embedded %d chunk(s); the collection went from %d to %d vectors.",
                stats["chunks_embedded"], before, collection.count())
    return stats


def stage_keyword(scripts_dir: Path, project_root: Path, db_arg: str, ticker: str,
                  conn=None) -> dict:
    """Rebuild the BM25 index for one ticker.

    Unlike the vector index, this one is cheap and entirely local: no API call,
    no money, a few seconds. Rebuilding it whole is simpler and safer than
    patching postings in place, and BM25 scores depend on corpus statistics that
    change when documents are added anyway.
    """
    stats = {"updated": False, "message": ""}
    if ticker.upper() != "MSFT":
        stats["message"] = (f"keyword index skipped: only MSFT is indexed in Phase 9 "
                            f"(asked for {ticker})")
        LOGGER.warning(stats["message"])
        return stats

    script = scripts_dir / "build_keyword_index.py"
    if not script.exists():
        stats["message"] = "build_keyword_index.py not found"
        LOGGER.error(stats["message"])
        return stats

    result = run_script(script, ["--db", db_arg, "--project-root", str(project_root),
                                 "--ticker", ticker, "--force"], project_root, conn=conn)
    stats["updated"] = result["returncode"] == 0
    stats["message"] = ("rebuilt" if stats["updated"]
                        else (result["error"] or result["stderr"][-200:] or "failed"))
    LOGGER.info("Keyword index: %s", stats["message"])
    return stats


# ---------------------------------------------------------------------------
# PHASE 9.1 - repair mode
# ---------------------------------------------------------------------------

STATUS_PARTIAL = "partial_success"

FAILED_EXTRACTION = "extraction_failed"
FAILED_CARD = "card_failed"


def failed_documents(conn: sqlite3.Connection, stage: str, run_id: int = 0) -> list:
    """Documents whose LATEST event for this stage is a failure.

    "Latest" matters. A document that failed on Monday and succeeded on Tuesday
    is not a candidate for repair, and a repair run must not keep rediscovering
    work it already finished.
    """
    if not table_exists(conn, "ingestion_file_events"):
        return []
    marker = "extraction FAILED" if stage == "extraction" else "card FAILED"
    scope = "AND e.ingestion_run_id = ?" if run_id else ""
    params = [marker] + ([run_id] if run_id else [])

    rows = conn.execute(
        f"""
        SELECT e.document_id, e.relative_path, e.extension, e.file_path, e.action_taken,
               e.error_message, e.ingestion_run_id
          FROM ingestion_file_events e
          JOIN (SELECT document_id, MAX(event_id) AS newest
                  FROM ingestion_file_events
                 WHERE COALESCE(document_id,'') <> ''
                 GROUP BY document_id) latest
            ON latest.document_id = e.document_id AND latest.newest = e.event_id
         WHERE COALESCE(e.action_taken,'') LIKE '%' || ? || '%'
           {scope}
         ORDER BY e.event_id
        """, params).fetchall()
    return [dict(r) for r in rows]


def cards_needing_work(conn: sqlite3.Connection, document_ids: list) -> list:
    """Documents that have text but no usable card.

    Two groups, and the second is the one a repair run exists for: a document
    whose card failed BECAUSE its extraction failed will now extract cleanly,
    and it needs a card that nobody has asked for yet.
    """
    if not document_ids:
        return []
    out = []
    for start in range(0, len(document_ids), 400):
        batch = document_ids[start:start + 400]
        marks = ",".join("?" for _ in batch)
        rows = conn.execute(
            f"""
            SELECT document_id, relative_path, file_extension AS extension, file_path
              FROM documents
             WHERE document_id IN ({marks})
               AND COALESCE(pdf_extraction_status,'') LIKE 'extracted%'
               AND (COALESCE(document_card_status,'') = ''
                    OR COALESCE(document_card_status,'') LIKE 'failed%'
                    OR COALESCE(stale_document_card,'0') = '1')
            """, batch).fetchall()
        out.extend(dict(r) for r in rows)
    return out


def to_entries(rows: list) -> list:
    """Turn stored rows back into the entry shape the stages expect."""
    entries = []
    for row in rows:
        entries.append({
            "document_id": row.get("document_id", ""),
            "relative_path": row.get("relative_path", "") or "",
            "extension": (row.get("extension") or "").lower(),
            "file_path": row.get("file_path", "") or "",
            "change_status": "repair",
            "actions": [],
        })
    return entries


def diagnose_failed(conn: sqlite3.Connection, project_root: Path, how_many: int = 3) -> None:
    """Show the first few failures in full, and retry nothing.

    Seventy failures with one cause is one problem, not seventy. This prints
    enough of the first few to name that cause, without spending the time or the
    risk of re-running everything.
    """
    safe_print()
    safe_print("=" * 78)
    safe_print("DIAGNOSE FAILED - showing the first few failures. NOTHING IS BEING RETRIED.")
    safe_print("=" * 78)

    rows = (failed_documents(conn, "extraction") + failed_documents(conn, "card"))[:how_many]
    if not rows:
        safe_print("  No failed extraction or card events are recorded.")
        return

    for index, row in enumerate(rows, start=1):
        safe_print()
        safe_print(f"--- failure {index} of {len(rows)} shown "
                   f"(run {row.get('ingestion_run_id')}) ---")
        safe_print(f"  document_id : {row.get('document_id')}")
        safe_print(f"  file        : {row.get('relative_path')}")
        safe_print(f"  stage       : {row.get('action_taken')}")
        safe_print("  recorded error:")
        for line in str(row.get("error_message") or "(nothing recorded)").split("  ||  "):
            for part in line.split(" / "):
                if part.strip():
                    safe_print(f"    {part.strip()}")

    logs = sorted((project_root / "10_Ingestion" / "runs").glob("*_failures.log"))
    if logs:
        safe_print()
        safe_print(f"  Complete stderr and stdout for every failure: {logs[-1].name}")
        safe_print(f"  {logs[-1]}")
    safe_print()
    safe_print("  If the same message appears on every failure, it is one problem, not many.")
    safe_print("=" * 78)


# ---------------------------------------------------------------------------
# PART H - smoke evaluation
# ---------------------------------------------------------------------------

def stage_smoke_eval(scripts_dir: Path, project_root: Path, db_arg: str, ticker: str,
                     conn=None) -> dict:
    """Three golden questions, not ten.

    The full evaluation is a deliberate act that costs money and is read
    carefully. This is the opposite: a quick "did ingestion break anything
    obvious" - one question that must be answered, one that must be refused for
    lack of evidence, and one that must be refused outright.
    """
    stats = {"status": "SKIPPED", "results": [], "questions": list(SMOKE_QUESTION_IDS)}
    script = scripts_dir / "run_evaluation.py"
    questions = project_root / "07_Evaluation" / "golden_questions" / f"{ticker}_eval_questions.json"
    if not script.exists() or not questions.exists():
        stats["status"] = "SKIPPED"
        stats["message"] = "run_evaluation.py or the golden questions file is missing"
        LOGGER.warning(stats["message"])
        return stats

    worst = "PASS"
    order = {"PASS": 0, "REVIEW": 1, "FAIL": 2}
    for question_id in SMOKE_QUESTION_IDS:
        result = run_script(script, [
            "--project-root", str(project_root), "--db", db_arg, "--ticker", ticker,
            "--questions-file", str(questions.relative_to(project_root)),
            "--question-id", question_id,
        ], project_root, timeout=900, conn=conn)
        outcome = _read_smoke_outcome(result["stdout"], question_id)
        stats["results"].append({"question_id": question_id, "outcome": outcome,
                                 "returncode": result["returncode"]})
        if order.get(outcome, 2) > order.get(worst, 0):
            worst = outcome
        LOGGER.info("Smoke: %-28s %s", question_id, outcome)

    stats["status"] = worst
    return stats


def _read_smoke_outcome(stdout: str, question_id: str) -> str:
    for line in (stdout or "").splitlines():
        if question_id in line:
            for word in ("FAIL", "REVIEW", "PASS"):
                if word in line:
                    return word
    return "FAIL"


# ---------------------------------------------------------------------------
# PART F - reports
# ---------------------------------------------------------------------------

def write_manifest(path: Path, project_root: Path, source_root: Path, events: list) -> None:
    assert_inside_project(path, project_root)
    assert_outside_source(path, source_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    header = ["change_status", "document_id", "relative_path", "file_name", "extension",
              "file_size", "modified_time", "old_hash", "new_hash", "action_taken",
              "status", "error_message", "ticker_candidates", "note"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        for event in events:
            writer.writerow([event.get(col, "") for col in header])


def _table(header: list, rows: list, empty: str) -> list:
    if not rows:
        return [f"_{empty}_", ""]
    out = ["| " + " | ".join(header) + " |",
           "|" + "|".join("---" for _ in header) + "|"]
    for row in rows:
        cells = [str(c).replace("|", "\\|") if c is not None else "" for c in row]
        out.append("| " + " | ".join(cells) + " |")
    out.append("")
    return out


def build_report(context: dict) -> str:
    counts = context["counts"]
    stages = context["stages"]
    events = context["events"]
    mode = ("DRY RUN - nothing was written outside the ingestion report"
            if context["dry_run"] else
            ("REPAIR - no archive walk, no metadata written" if context.get("repair_mode")
             else "live"))

    def by_status(status, limit=50):
        return [e for e in events if e["change_status"] == status][:limit]

    lines = [
        "# Incremental Ingestion Report",
        "",
        f"_Run {context['run_id']} · `run_incremental_ingestion.py` ({PIPELINE_VERSION}) · "
        f"mode **{mode}**_",
        "",
        "> **AI drafts. Humans verify. Cutler decides.**",
        "",
        "## What this run did",
        "",
        "This run compared the source archive with the metadata index and processed only the "
        "differences. Unchanged files were counted and left alone. Files that are in the index "
        "but no longer on the drive are listed below and were **not** deleted from the database "
        "- a share that is briefly unavailable must not be able to erase what it once held.",
        "",
        "## Run",
        "",
        "| | |",
        "|---|---|",
        f"| Started | {context['started_at']} |",
        f"| Completed | {context['completed_at']} |",
        f"| Source root | `{context['source_root']}` (read-only) |",
        f"| Ticker | {context['ticker'] or 'all'} |",
        f"| Mode | {mode} |",
        f"| Force full | {context['force_full']} |",
        f"| Status | **{context['status']}** |",
        "",
        "## Files",
        "",
        "| | |",
        "|---|---|",
        f"| Files seen | **{counts['files_seen']:,}** |",
        f"| New | **{counts['new_files']:,}** |",
        f"| Changed | **{counts['changed_files']:,}** |",
        f"| Unchanged | {counts['unchanged_files']:,} |",
        f"| Missing (reported, not deleted) | {counts['missing_files']:,} |",
        f"| Skipped (unsupported / excluded) | {counts['skipped_files']:,} |",
        "",
        "## Work done",
        "",
        "| Stage | Result |",
        "|---|---|",
        f"| Metadata rows added | {counts['metadata_added']:,} |",
        f"| Metadata rows updated | {counts['metadata_updated']:,} |",
        f"| PDFs extracted | {stages['extract']['extracted']:,} |",
        f"| Extraction failures | {stages['extract']['failed']:,} |",
        f"| Document cards built | {stages['cards']['created']:,} |",
        f"| Card failures | {stages['cards']['failed']:,} |",
        f"| Obsidian notes added | {stages['obsidian']['updated']:,} |",
        f"| Chunks created | {stages['chunk']['chunks_created']:,} |",
        f"| Chunks superseded | {stages['chunk']['chunks_superseded']:,} |",
        f"| Superseded vectors removed | {stages['chunk'].get('vectors_removed', 0):,} |",
        f"| Chunks embedded | {stages['embed']['chunks_embedded']:,} |",
        f"| Keyword index | {stages['keyword']['message'] or 'not requested'} |",
        f"| Smoke evaluation | {stages['smoke']['status']} |",
        "",
    ]

    if context["status"] == STATUS_PARTIAL:
        lines += [
            "## This run was a partial success",
            "",
            f"Metadata was written and the vault was refreshed, but "
            f"**{stages['extract']['failed']} extraction(s)** and "
            f"**{stages['cards']['failed']} card(s)** failed. The index now knows those files "
            "exist and has no text for them.",
            "",
            "**Do not run `--chunk-new` or `--embed-new` yet.** Chunking would index an "
            "incomplete set, and the embedding cost would be spent on it.",
            "",
            "Repair only what failed, without walking the archive again:",
            "",
            "```bat",
            "py -3.11 scripts\\run_incremental_ingestion.py --project-root \"<root>\" "
            "--db \"data\\document_metadata.sqlite\" --source-root \"<archive>\" "
            "--retry-failed-extraction --retry-failed-cards --verbose",
            "```",
            "",
        ]

    chunk = stages["chunk"]
    resolved = chunk.get("resolved") or {}
    if any(resolved.get(k) for k in ("cleared", "held_missing", "held_not_ready")):
        lines += ["## RAG state of the affected documents", ""]

        if not chunk.get("chunks_created") and resolved.get("cleared"):
            lines += [
                "**No affected " + (context["ticker"] or "MSFT") + " documents were RAG-eligible. "
                "Stale RAG flags cleared for skipped docs.** There is nothing to re-run for "
                "these - they are excluded by the eligibility rules, and running the chunk "
                "command again would find exactly the same thing.",
                "",
            ]

        lines += ["### RAG skipped because not eligible", ""]
        lines += _table(["relative_path", "why"],
                        [(r["relative_path"], r["reason"]) for r in resolved.get("cleared", [])],
                        "None.")
        lines += ["_Their `stale_rag_index` flag has been cleared. They will not be reported "
                  "as outstanding again._", ""]

        if resolved.get("held_missing"):
            lines += ["### Source file missing; RAG state not changed", ""]
            lines += _table(["relative_path", "embedded chunks still in the index"],
                            [(r["relative_path"], r.get("live_chunks", 0))
                             for r in resolved["held_missing"]],
                            "None.")
            lines += ["_Nothing was chunked, nothing was deleted and the flag was kept. "
                      "**Confirm the V drive/share was fully available before treating these as "
                      "deleted.**_", ""]

        if resolved.get("held_not_ready"):
            lines += ["### Not chunked: extraction or card not ready", ""]
            lines += _table(["relative_path", "what is missing"],
                            [(r["relative_path"], r["reason"])
                             for r in resolved["held_not_ready"]],
                            "None.")
            lines += ["_These keep their stale flag. Repair extraction and cards first._", ""]

        if resolved.get("not_eligible_with_chunks"):
            lines += ["### Cleared, but still retrievable - worth a look", ""]
            lines += _table(["relative_path", "embedded chunks", "why it is no longer eligible"],
                            [(r["relative_path"], r.get("live_chunks", 0), r["reason"])
                             for r in resolved["not_eligible_with_chunks"]],
                            "None.")
            lines += ["_These documents are no longer RAG-eligible, but chunks from when they "
                      "were are still in the index and can still be cited in an answer. Nothing "
                      "was removed automatically - that is a judgement about what an answer may "
                      "stand on._", ""]

    if context.get("failure_log_rel"):
        lines += [
            "### Where the full errors are",
            "",
            f"`{context['failure_log_rel']}` holds every one of the "
            f"{context['failure_count']} failure(s) in full - the exact command, the complete "
            "stderr and the complete stdout. Nothing in that file is truncated.",
            "",
            "If the same message appears on every failure, it is one problem, not many.",
            "",
        ]

    if stages["embed"]["chunks_embedded"]:
        lines += [
            "### Embedding cost",
            "",
            f"- {stages['embed']['chunks_embedded']:,} chunk(s), "
            f"{stages['embed']['tokens']:,} token(s)",
            f"- **${stages['embed']['estimated_cost_usd']:.6f}** estimated, at the rate in "
            "`.env`. This is a local estimate, not a bill.",
            f"- Vectors added to the existing Chroma collection: "
            f"{stages['embed']['added_to_chroma']:,}",
            "",
            "Nothing already embedded was sent again.",
            "",
        ]

    lines += ["## New files", ""]
    lines += _table(["relative_path", "extension", "size", "action", "status"],
                    [(e["relative_path"], e["extension"], f"{int(e['file_size'] or 0):,}",
                      e["action_taken"], e["status"]) for e in by_status(NEW)],
                    "No new files.")

    lines += ["## Changed files", ""]
    lines += _table(["relative_path", "old hash", "new hash", "action", "status"],
                    [(e["relative_path"], (e["old_hash"] or "")[:12],
                      (e["new_hash"] or "")[:12], e["action_taken"], e["status"])
                     for e in by_status(CHANGED)],
                    "No changed files.")

    failed = [e for e in events if e["status"] == "error"][:50]
    lines += ["## Failed files", ""]
    lines += _table(["relative_path", "change", "error"],
                    [(e["relative_path"], e["change_status"], (e["error_message"] or "")[:160])
                     for e in failed],
                    "No failures.")

    lines += ["## Missing files", "",
              "_In the index, not found on the drive during this walk. **Nothing was deleted.** "
              "Check whether the share was available and whether these were intentionally "
              "removed._", ""]
    lines += _table(["relative_path", "document_id"],
                    [(e["relative_path"], e["document_id"]) for e in by_status(MISSING)],
                    "No missing files.")

    rag_events = [e for e in events if "chunk" in (e["action_taken"] or "")][:50]
    lines += ["## RAG-affected documents", ""]
    lines += _table(["relative_path", "tickers", "action"],
                    [(e["relative_path"], e["ticker_candidates"], e["action_taken"])
                     for e in rag_events],
                    "No document reached the RAG stages in this run.")

    if context["warnings"]:
        lines += ["## Warnings", ""] + [f"- {w}" for w in context["warnings"]] + [""]

    lines += ["## Next recommended action", ""]
    lines += [f"- {step}" for step in context["next_actions"]] + [""]
    lines += ["---", "",
              "_Counts describe what the pipeline did, not whether the documents are any good. "
              "A new document card is a draft of a guess about a file nobody has read yet._", "",
              "**AI drafts. Humans verify. Cutler decides.**", ""]
    return "\n".join(lines)


def next_actions(context: dict) -> list:
    counts = context["counts"]
    stages = context["stages"]
    steps = []

    if context["dry_run"]:
        steps.append("This was a dry run. Nothing was changed. Re-run without `--dry-run` to "
                     "apply it.")
        if counts["new_files"] or counts["changed_files"]:
            steps.append(f"{counts['new_files']} new and {counts['changed_files']} changed file(s) "
                         "are waiting to be processed.")
        else:
            steps.append("Nothing has changed since the last ingestion. There is nothing to do.")
        return steps

    if stages["extract"]["failed"] or stages["cards"]["failed"]:
        steps.append(
            f"REPAIR FIRST: {stages['extract']['failed']} extraction(s) and "
            f"{stages['cards']['failed']} card(s) failed. Re-run with "
            "`--retry-failed-extraction --retry-failed-cards`, which reprocesses only those "
            "documents and does not walk the archive.")
        steps.append("To see why before retrying anything, run with `--diagnose-failed`.")
        steps.append("Do NOT run `--chunk-new` or `--embed-new` until the failures are cleared.")
    if counts["missing_files"]:
        steps.append(f"{counts['missing_files']} indexed file(s) were not found. Confirm the share "
                     "was fully available before treating any of them as deleted.")

    # Phase 9.2. The old version recommended the chunk command whenever ANY
    # stale flag was set - including on documents that command could never do
    # anything with. That is how the system ended up telling a person to run the
    # same thing over and over while nothing changed.
    stale = context.get("stale_counts", {})
    chunk = stages["chunk"]
    pending_eligible = len([r for r in chunk.get("buckets", {}).get(RAG_ELIGIBLE, [])
                            if r.get("stale")])

    if chunk.get("other_ticker_cleared"):
        steps.append(
            f"{len(chunk['other_ticker_cleared'])} stale flag(s) belonged to documents of other "
            "tickers, which nothing indexes yet. There is no index for them to be behind, so the "
            "flags were cleared with that recorded - they are not lost work.")
    if chunk.get("stale_cleared"):
        steps.append(
            f"{chunk['stale_cleared']} document(s) were not RAG-eligible and will never be "
            "chunked, so their stale RAG flags were cleared. Each one's reason is recorded in "
            "`rag_skip_reason` and listed above - nothing is waiting on you for those.")
    if chunk.get("not_eligible_with_chunks"):
        steps.append(
            f"REVIEW: {chunk['not_eligible_with_chunks']} of those still have embedded chunks in "
            "the index that retrieval can return. Decide whether an answer should still be able "
            "to cite them.")
    if chunk.get("held_not_ready"):
        steps.append(
            f"{chunk['held_not_ready']} document(s) are stale because their extraction or card "
            "is not finished. Repair those first with `--retry-failed-extraction "
            "--retry-failed-cards`; they cannot be chunked until then.")
    if chunk.get("held_missing"):
        steps.append(
            f"{chunk['held_missing']} stale document(s) have no source file on the drive. Their "
            "RAG state was deliberately left alone - confirm the share was fully available "
            "before treating them as deleted.")

    if stale.get("rag") and pending_eligible:
        steps.append(f"{pending_eligible} RAG-eligible document(s) are waiting to be chunked. "
                     "Re-run with `--ticker MSFT --chunk-new --embed-new --update-keyword` to "
                     "bring the MSFT index up to date.")
    elif stale.get("rag") and context.get("classified_all"):
        # --chunk-new classified everything, so anything still flagged is
        # genuinely held - never "this run did not classify them".
        steps.append(
            f"{stale['rag']} document(s) still carry a stale RAG flag, and every one of them was "
            "classified in this run. They are held because their source is missing or their "
            "extraction has to be repaired - see the tables above. Re-running the chunk command "
            "would not change them.")
    elif stale.get("rag"):
        steps.append(
            f"{stale['rag']} document(s) are marked stale_rag_index and this run did not "
            "classify them, because no --ticker was given. Run `--ticker MSFT --chunk-new` (or "
            "`--clear-noneligible-stale-rag` if you do not want to spend anything) to find out "
            "why each one is flagged.")
    if stages["chunk"]["chunks_created"] and not stages["embed"]["chunks_embedded"]:
        steps.append(f"{stages['chunk']['chunks_created']} chunk(s) are pending embedding. Re-run "
                     "with `--embed-new` when you are ready to spend on it.")
    if stages["embed"]["chunks_embedded"] and not stages["keyword"]["updated"]:
        steps.append("New vectors were added but the keyword index was not rebuilt. Re-run with "
                     "`--update-keyword`, or hybrid retrieval will be searching a stale BM25 "
                     "index.")
    if stages["smoke"]["status"] in ("FAIL", "REVIEW"):
        steps.append(f"The smoke evaluation came back {stages['smoke']['status']}. Run the full "
                     "evaluation before trusting new answers: "
                     "`py -3.11 scripts\\run_evaluation.py --ticker MSFT`.")
    if not steps:
        steps.append("Nothing needs attention. The index matches the archive as of this run.")
    elif not any(word in " ".join(steps) for word in ("REPAIR", "REVIEW", "waiting", "Repair")):
        steps.append("Nothing else is outstanding. Re-running the same command would find "
                     "nothing to do.")
    return steps


# ---------------------------------------------------------------------------
# Run record
# ---------------------------------------------------------------------------

def start_run(conn: sqlite3.Connection, args, source_root: Path) -> int:
    cursor = conn.execute(
        "INSERT INTO ingestion_runs (started_at, source_root, ticker, dry_run, force_full, "
        "status, pipeline_version, options_json) VALUES (?,?,?,?,?,?,?,?)",
        (utc_now(), str(source_root), (args.ticker or "").upper(),
         "1" if args.dry_run else "0", "1" if args.force_full else "0",
         "running", PIPELINE_VERSION, json.dumps(vars(args), default=str)))
    conn.commit()
    return int(cursor.lastrowid)


def finish_run(conn: sqlite3.Connection, run_id: int, context: dict) -> None:
    counts = context["counts"]
    stages = context["stages"]
    conn.execute(
        """
        UPDATE ingestion_runs
           SET completed_at = ?, files_seen = ?, new_files = ?, changed_files = ?,
               unchanged_files = ?, missing_files = ?, skipped_files = ?,
               metadata_added = ?, metadata_updated = ?, pdfs_extracted = ?,
               extraction_failed = ?, cards_created = ?, cards_failed = ?,
               obsidian_updated = ?, chunks_created = ?, chunks_embedded = ?,
               keyword_updated = ?, smoke_eval_status = ?, status = ?, report_path = ?,
               manifest_path = ?, json_path = ?, estimated_cost_usd = ?, error_message = ?
         WHERE ingestion_run_id = ?
        """,
        (context["completed_at"], counts["files_seen"], counts["new_files"],
         counts["changed_files"], counts["unchanged_files"], counts["missing_files"],
         counts["skipped_files"], counts["metadata_added"], counts["metadata_updated"],
         stages["extract"]["extracted"], stages["extract"]["failed"],
         stages["cards"]["created"], stages["cards"]["failed"],
         stages["obsidian"]["updated"], stages["chunk"]["chunks_created"],
         stages["embed"]["chunks_embedded"],
         "1" if stages["keyword"]["updated"] else "0", stages["smoke"]["status"],
         context["status"], context["report_rel"], context["manifest_rel"],
         context["json_rel"], stages["embed"]["estimated_cost_usd"],
         context["error_message"], run_id))
    conn.commit()


def record_events(conn: sqlite3.Connection, run_id: int, events: list) -> None:
    now = utc_now()
    conn.executemany(
        "INSERT INTO ingestion_file_events (ingestion_run_id, document_id, file_path, "
        "relative_path, extension, file_size, modified_time, old_hash, new_hash, "
        "change_status, action_taken, status, error_message, ticker_candidates, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [(run_id, e.get("document_id", ""), e.get("file_path", ""), e.get("relative_path", ""),
          e.get("extension", ""), e.get("file_size", 0), e.get("modified_time", ""),
          e.get("old_hash", ""), e.get("new_hash", ""), e.get("change_status", ""),
          e.get("action_taken", ""), e.get("status", ""), e.get("error_message", ""),
          e.get("ticker_candidates", ""), now) for e in events])
    conn.commit()


def stale_counts(conn: sqlite3.Connection) -> dict:
    def count(column):
        try:
            return int(conn.execute(
                f"SELECT COUNT(*) FROM documents WHERE COALESCE({column},'0') = '1'"
            ).fetchone()[0] or 0)
        except sqlite3.OperationalError:
            return 0
    return {"extraction": count("stale_extraction"), "card": count("stale_document_card"),
            "rag": count("stale_rag_index")}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Cutler Research Brain Phase 9 - incremental ingestion of the source archive.")
    parser.add_argument("--project-root", required=True, help="Project root for all outputs.")
    parser.add_argument("--db", default=DEFAULT_DB, help="SQLite index, relative to the root.")
    parser.add_argument("--source-root", default=DEFAULT_SOURCE_ROOT,
                        help="Archive folder to scan. READ-ONLY, always.")
    parser.add_argument("--ticker", default="", help="Ticker for the RAG stages (MSFT only).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Scan and report. Change nothing.")
    parser.add_argument("--force-full", action="store_true",
                        help="Treat every file as changed. Expensive; use deliberately.")
    parser.add_argument("--process-new", dest="process_new", action="store_true", default=True)
    parser.add_argument("--no-process-new", dest="process_new", action="store_false")
    parser.add_argument("--process-changed", dest="process_changed", action="store_true",
                        default=True)
    parser.add_argument("--no-process-changed", dest="process_changed", action="store_false")
    parser.add_argument("--extract-text", dest="extract_text", action="store_true", default=True)
    parser.add_argument("--no-extract-text", dest="extract_text", action="store_false")
    parser.add_argument("--build-cards", dest="build_cards", action="store_true", default=True)
    parser.add_argument("--no-build-cards", dest="build_cards", action="store_false")
    parser.add_argument("--update-obsidian", dest="update_obsidian", action="store_true",
                        default=True)
    parser.add_argument("--no-update-obsidian", dest="update_obsidian", action="store_false")
    parser.add_argument("--chunk-new", action="store_true", default=False,
                        help="Chunk affected RAG-eligible documents for --ticker.")
    parser.add_argument("--embed-new", action="store_true", default=False,
                        help="Embed pending chunks. THIS ONE COSTS MONEY.")
    parser.add_argument("--update-keyword", action="store_true", default=False,
                        help="Rebuild the BM25 keyword index for --ticker.")
    parser.add_argument("--run-smoke-eval", action="store_true", default=False,
                        help="Run three golden questions, not the full evaluation.")
    parser.add_argument("--retry-failed-extraction", action="store_true", default=False,
                        help="Repair mode: re-extract only the documents whose last extraction "
                             "failed. Does not walk the archive.")
    parser.add_argument("--retry-failed-cards", action="store_true", default=False,
                        help="Repair mode: rebuild cards for documents whose card failed, plus "
                             "any that now have text and no card.")
    parser.add_argument("--clear-noneligible-stale-rag", action="store_true", default=False,
                        help="Classify every stale_rag_index document and clear the flag on the "
                             "ones RAG will never index. Chunks nothing, embeds nothing, costs "
                             "nothing.")
    parser.add_argument("--diagnose-failed", action="store_true", default=False,
                        help="Print the first few recorded failures in full and exit. Retries "
                             "nothing, changes nothing.")
    parser.add_argument("--hash-all", action="store_true", default=False,
                        help="Hash every file instead of trusting size and modified time.")
    parser.add_argument("--limit", type=int, default=0,
                        help="Stop after N new/changed files (0 = no limit).")
    parser.add_argument("--verbose", action="store_true", help="Chattier console output.")
    return parser.parse_args(argv)


class _RepairFinished(Exception):
    """Repair mode is done; skip the walk and go straight to the reports."""


def main(argv=None) -> int:
    configure_console_encoding()
    args = parse_args(argv)

    project_root = Path(args.project_root).expanduser().resolve()
    validate_project_root(project_root)
    source_root = Path(args.source_root).expanduser()
    db_path = (project_root / args.db) if not Path(args.db).is_absolute() else Path(args.db)

    setup_logging(project_root / "logs" / "phase9_ingestion.log", args.verbose)
    LOGGER.info("=" * 70)
    LOGGER.info("Phase 9 incremental ingestion (%s)", PIPELINE_VERSION)
    LOGGER.info("Project root : %s", project_root)
    LOGGER.info("Source root  : %s  (READ-ONLY)", source_root)
    LOGGER.info("Database     : %s", db_path)
    if args.dry_run:
        LOGGER.warning("DRY RUN: nothing will be written except this run's own report.")

    if is_relative_to(project_root, source_root):
        raise SystemExit("ERROR: the project root is inside the source archive. Refusing to run.")
    if not source_root.exists() and not (args.retry_failed_extraction
                                         or args.retry_failed_cards
                                         or args.diagnose_failed):
        raise SystemExit(
            f"ERROR: source archive not found: {source_root}\n"
            "If this is a network share, check it is mounted before running an ingestion - "
            "an unavailable share would make every indexed file look missing.")

    scanner, indexer = import_pipeline_modules()
    conn = connect(db_path)
    create_schema(conn)

    # --- diagnose: read, print, change nothing --------------------------
    if args.diagnose_failed:
        diagnose_failed(conn, project_root)
        conn.close()
        return 0

    repair_mode = bool(args.retry_failed_extraction or args.retry_failed_cards)

    scripts_dir = Path(__file__).resolve().parent
    db_arg = args.db
    started_at = utc_now()
    slug = timestamp_slug()
    run_id = start_run(conn, args, source_root)
    LOGGER.info("Ingestion run id: %d", run_id)

    warnings = []
    error_message = ""
    status = STATUS_DRY_RUN if args.dry_run else STATUS_COMPLETED
    stages = {
        "extract": {"extracted": 0, "failed": 0, "skipped_non_pdf": 0},
        "cards": {"created": 0, "failed": 0},
        "obsidian": {"updated": 0, "notes_before": 0, "notes_after": 0, "failed": False},
        "chunk": {"chunks_created": 0, "chunks_superseded": 0, "documents_chunked": 0,
                  "documents_considered": 0, "skipped_not_eligible": 0,
                  "vectors_removed": 0, "stale_cleared": 0, "held_missing": 0,
                  "held_not_ready": 0, "not_eligible_with_chunks": 0,
                  "resolved": {}, "buckets": {}, "other_ticker_cleared": [],
                  "carried_over": 0},
        "embed": {"chunks_embedded": 0, "chunks_failed": 0, "tokens": 0,
                  "estimated_cost_usd": 0.0, "added_to_chroma": 0, "batches": 0},
        "keyword": {"updated": False, "message": ""},
        "smoke": {"status": "SKIPPED", "results": []},
    }
    counts = {"files_seen": 0, "new_files": 0, "changed_files": 0, "unchanged_files": 0,
              "missing_files": 0, "skipped_files": 0, "metadata_added": 0,
              "metadata_updated": 0}
    events = []

    failures = FailureLog(
        project_root / "10_Ingestion" / "runs" / f"ingestion_{slug}_failures.log",
        project_root, source_root)

    try:
        # ---- PHASE 9.1: repair mode ---------------------------------------
        # No walk, no metadata, no archive read. Repair works entirely from
        # what the previous run recorded, and reuses the document rows it
        # already created - a repaired document keeps its document_id, so no
        # duplicate row can appear.
        if repair_mode:
            LOGGER.info("REPAIR MODE - the archive is not walked and no metadata is written.")
            repair_entries = {}

            if args.retry_failed_extraction:
                rows = failed_documents(conn, "extraction")
                LOGGER.info("Documents whose last extraction failed: %d", len(rows))
                for entry in to_entries(rows):
                    repair_entries[entry["document_id"]] = entry
                if rows and args.limit:
                    rows = rows[:args.limit]

            extract_targets = list(repair_entries.values())
            if args.limit:
                extract_targets = extract_targets[:args.limit]

            if args.retry_failed_extraction and extract_targets:
                stages["extract"] = stage_extract(extract_targets, scripts_dir, project_root,
                                                  db_arg, conn, failures)

            card_entries = {}
            if args.retry_failed_cards:
                rows = failed_documents(conn, "card")
                for entry in to_entries(rows):
                    card_entries[entry["document_id"]] = entry
                # Plus anything that now HAS text and still has no usable card -
                # which is every document whose card failed only because its
                # extraction had failed first.
                repaired = [e["document_id"] for e in extract_targets if e.get("document_id")]
                for row in cards_needing_work(conn, repaired + list(card_entries)):
                    card_entries.setdefault(row["document_id"], to_entries([row])[0])
                LOGGER.info("Documents needing a card: %d", len(card_entries))

            card_targets = list(card_entries.values())
            if args.limit:
                card_targets = card_targets[:args.limit]
            if args.retry_failed_cards and card_targets:
                stages["cards"] = stage_cards(card_targets, scripts_dir, project_root, db_arg,
                                              conn, failures)

            # The vault is refreshed AFTER the repairs, so it reflects the cards
            # that now exist rather than the ones that did not.
            processed_entries = list({e["document_id"]: e for e in
                                      extract_targets + card_targets}.values())
            if args.update_obsidian and processed_entries:
                stages["obsidian"] = stage_obsidian(scripts_dir, project_root, db_arg, conn)

            events = [{
                "document_id": e.get("document_id", ""),
                "file_path": e.get("file_path", ""),
                "relative_path": e.get("relative_path", ""),
                "extension": e.get("extension", ""),
                "file_size": 0, "modified_time": "", "old_hash": "", "new_hash": "",
                "change_status": "repair",
                "action_taken": "; ".join(e.get("actions") or []) or "repair attempted",
                "status": "error" if e.get("error") else "ok",
                "error_message": e.get("error", "") or "",
                "ticker_candidates": "", "note": f"repair of run {args.limit or ''}".strip(),
            } for e in processed_entries]

            counts["files_seen"] = len(processed_entries)
            if not processed_entries:
                LOGGER.info("Nothing to repair. Every recorded failure has since succeeded.")
            raise _RepairFinished()

        # ---- PART A: what changed -----------------------------------------
        scan = scan_changes(source_root, conn, scanner, args.hash_all, args.limit,
                            args.force_full)
        buckets = scan["buckets"]
        counts["files_seen"] = scan["stats"]["files_seen"]
        counts["new_files"] = len(buckets[NEW])
        counts["changed_files"] = len(buckets[CHANGED])
        counts["unchanged_files"] = len(buckets[UNCHANGED])
        counts["missing_files"] = len(buckets[MISSING])
        counts["skipped_files"] = int(scan["stats"].get("walk", {}).get("files_unsupported", 0))

        LOGGER.info("new %d | changed %d | unchanged %d | missing %d | skipped %d",
                    counts["new_files"], counts["changed_files"], counts["unchanged_files"],
                    counts["missing_files"], counts["skipped_files"])

        if counts["missing_files"] and counts["missing_files"] > max(
                25, counts["files_seen"] // 10):
            warnings.append(
                f"{counts['missing_files']:,} indexed files were not found on the drive. That is "
                "a large fraction of the archive; a partially mounted share is far more likely "
                "than a mass deletion. Nothing was deleted.")

        # ---- PART C: metadata ---------------------------------------------
        to_process = []
        if args.process_new:
            to_process += [(NEW, e) for e in buckets[NEW]]
        else:
            warnings.append("--no-process-new: new files were detected but not processed.")
        if args.process_changed:
            to_process += [(CHANGED, e) for e in buckets[CHANGED]]
        else:
            warnings.append("--no-process-changed: changed files were detected but not "
                            "processed.")

        processed_entries = []
        if to_process and not args.dry_run:
            rules, ticker_rules = load_rules(project_root, scanner)
            archive_context = scanner.derive_archive_context(source_root)
            processed_at = utc_now()

            records = build_records([e for _s, e in to_process], source_root, scanner, rules,
                                    ticker_rules, processed_at, archive_context)
            if records:
                indexer.create_schema(conn, reset=False, logger=LOGGER)
                indexer.upsert_records(conn, records, LOGGER)
                conn.commit()

            for change_status, entry in to_process:
                record = entry.get("record_built")
                if record is None:
                    continue
                document_id = record.get("document_id", "")
                entry["document_id"] = document_id
                entry["extension"] = record.get("file_extension", "")
                entry["change_status"] = change_status
                if change_status == NEW:
                    counts["metadata_added"] += 1
                else:
                    counts["metadata_updated"] += 1
                    mark_stale(conn, document_id,
                               str((entry.get("record") or {}).get("file_hash_sha256") or ""),
                               record.get("file_hash_sha256", ""), run_id, change_status,
                               processed_at)
                processed_entries.append(entry)

            mark_fresh(conn, [e["document_id"] for e in processed_entries
                              if e.get("change_status") == NEW], run_id, NEW, processed_at)
            conn.commit()
            LOGGER.info("Metadata: %d added, %d updated.", counts["metadata_added"],
                        counts["metadata_updated"])

        if not args.dry_run:
            # Outside the block above on purpose. A run where nothing changed
            # still learned something worth recording: that every one of these
            # files was still there when we looked. last_seen_at is the
            # difference between "this document is gone" and "nobody has looked
            # recently", and the missing bucket is only safe to report because
            # that difference is kept.
            seen_at = utc_now()
            touch_unchanged(conn, buckets[UNCHANGED], seen_at)
            mark_missing(conn, buckets[MISSING], run_id, seen_at)
            conn.commit()

        # ---- PART D: extraction, cards, vault ------------------------------
        if processed_entries and not args.dry_run:
            if args.extract_text:
                stages["extract"] = stage_extract(processed_entries, scripts_dir, project_root,
                                                  db_arg, conn, failures)
            if args.build_cards:
                stages["cards"] = stage_cards(processed_entries, scripts_dir, project_root,
                                              db_arg, conn, failures)
            if args.update_obsidian:
                stages["obsidian"] = stage_obsidian(scripts_dir, project_root, db_arg, conn)
                if stages["obsidian"]["failed"]:
                    warnings.append("The Obsidian vault refresh did not complete. The metadata "
                                    "and cards are current; the vault is not.")

        # ---- PART E: MSFT RAG ----------------------------------------------
        ticker = (args.ticker or "").upper()
        if (args.chunk_new or args.embed_new or args.update_keyword
                or args.clear_noneligible_stale_rag) and not ticker:
            warnings.append("RAG stages were requested without --ticker, so none ran. Phase 9 "
                            "indexes one ticker at a time.")
        elif ticker and not args.dry_run:
            import build_rag_index as rag  # noqa: E402 - only needed on this path
            # Phase 9.3. The RAG worklist is BOTH halves, and it has to be:
            #   A. what this walk touched, and
            #   B. what earlier runs left flagged for this ticker.
            # Run 5 processed four new files, found none of them RAG-eligible,
            # and reported ten stale documents it had never looked at - because
            # only half A was in the list.
            affected = {e["document_id"] for e in processed_entries if e.get("document_id")}
            carried_over = stale_rag_document_ids(conn, ticker)
            affected |= carried_over
            if carried_over:
                LOGGER.info("%d %s document(s) carried a stale RAG flag from an earlier run and "
                            "are in this run's worklist.", len(carried_over), ticker)

            # And the ones this ticker's run can never act on. Left out of every
            # previous worklist, which is why they were counted forever and
            # never classified.
            other_ticker_stale = stale_rag_other_tickers(conn, ticker)
            if other_ticker_stale:
                LOGGER.info("%d stale document(s) belong to other tickers.",
                            len(other_ticker_stale))

            if args.clear_noneligible_stale_rag and not args.chunk_new:
                # The same classification, without the chunking. For someone who
                # wants the state tidied and is not ready to spend anything.
                #
                # NOT named `buckets`: that name already holds the walk's
                # new/changed/missing buckets in this scope, and shadowing it
                # here fed the wrong shape into build_events().
                rag_buckets = classify_stale_rag(conn, ticker, sorted(affected), rag)
                resolved = resolve_stale_rag(conn, rag_buckets, ticker)
                stages["chunk"]["other_ticker_cleared"] = clear_other_ticker_stale(
                    conn, other_ticker_stale, ticker)
                stages["chunk"].update({
                    "documents_considered": len(affected),
                    "skipped_not_eligible": len(rag_buckets[RAG_NOT_ELIGIBLE]),
                    "stale_cleared": len(resolved["cleared"]),
                    "held_missing": len(resolved["held_missing"]),
                    "held_not_ready": len(resolved["held_not_ready"]),
                    "not_eligible_with_chunks": len(resolved["not_eligible_with_chunks"]),
                    "resolved": {name: [{"document_id": r["document_id"],
                                         "relative_path": r.get("relative_path", ""),
                                         "reason": r.get("_reason", ""),
                                         "live_chunks": r.get("_live_chunks", 0)}
                                        for r in rows] for name, rows in resolved.items()},
                })
                LOGGER.info("Cleared %d stale RAG flag(s); held %d missing and %d not-ready.",
                            stages["chunk"]["stale_cleared"], stages["chunk"]["held_missing"],
                            stages["chunk"]["held_not_ready"])

            if args.chunk_new:
                if ticker != "MSFT":
                    warnings.append(f"--chunk-new was asked for {ticker}. Phase 9 indexes MSFT "
                                    "only; nothing was chunked.")
                else:
                    stages["chunk"] = stage_chunk(conn, project_root, ticker, affected, rag)
                    # A stale flag on a ticker with no index cannot mean "the
                    # index is behind" - there is no index for it to be behind.
                    # Clearing it with that reason written down is the only way
                    # these stop being counted forever; when a ticker is first
                    # indexed, the full Phase 5A build covers every eligible
                    # document regardless of any flag.
                    stages["chunk"]["other_ticker_cleared"] = clear_other_ticker_stale(
                        conn, other_ticker_stale, ticker)
                    for entry in processed_entries:
                        if entry.get("document_id") in affected:
                            entry.setdefault("actions", []).append("chunked")
            if args.embed_new:
                if ticker != "MSFT":
                    warnings.append(f"--embed-new was asked for {ticker}. Phase 9 embeds MSFT "
                                    "only; nothing was sent to OpenAI.")
                else:
                    stages["embed"] = stage_embed(conn, project_root, ticker, rag)
                    # Phase 9.2. This used to clear the flag for ANY document
                    # with an embedded chunk - which cleared documents this run
                    # never touched, including ones whose source is missing or
                    # whose extraction still has to be repaired.
                    #
                    # The right set is the ELIGIBLE ones: a document that is
                    # genuinely indexable, has embedded chunks, and has nothing
                    # left pending. Classified here rather than reusing this
                    # run's chunk buckets, because chunking on Monday and
                    # embedding on Tuesday is a normal pattern and the flag has
                    # to clear either way.
                    eligible_now = classify_stale_rag(conn, ticker, sorted(affected),
                                                      rag)[RAG_ELIGIBLE]
                    chunked_ids = [row["document_id"] for row in eligible_now]
                    for start in range(0, len(chunked_ids), 400):
                        batch = chunked_ids[start:start + 400]
                        marks = ",".join("?" for _ in batch)
                        conn.execute(
                            f"UPDATE documents SET stale_rag_index = '0', rag_skip_reason = '' "
                            f"WHERE COALESCE(stale_rag_index,'0') = '1' "
                            f"AND document_id IN ({marks}) AND document_id IN "
                            f"(SELECT DISTINCT document_id FROM rag_chunks WHERE ticker = ? "
                            f"AND embedding_status = 'embedded') "
                            # ...and with nothing of its own left waiting.
                            f"AND document_id NOT IN "
                            f"(SELECT DISTINCT document_id FROM rag_chunks WHERE ticker = ? "
                            f"AND embedding_status = 'pending')", batch + [ticker, ticker])
                    conn.commit()
            if args.update_keyword:
                stages["keyword"] = stage_keyword(scripts_dir, project_root, db_arg, ticker, conn)
                if stages["keyword"]["message"] and not stages["keyword"]["updated"]:
                    warnings.append(stages["keyword"]["message"])

        # ---- PART H: smoke evaluation --------------------------------------
        if args.run_smoke_eval and not args.dry_run:
            stages["smoke"] = stage_smoke_eval(scripts_dir, project_root, db_arg,
                                               ticker or "MSFT", conn)
            if stages["smoke"]["status"] in ("FAIL", "REVIEW"):
                warnings.append(f"Smoke evaluation came back {stages['smoke']['status']}.")
        elif args.run_smoke_eval:
            warnings.append("--run-smoke-eval was ignored: this was a dry run.")

        # ---- event rows -----------------------------------------------------
        events = build_events(buckets, processed_entries, args.dry_run)

    except _RepairFinished:
        pass
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - one run must never corrupt the record
        status = STATUS_FAILED
        error_message = f"{type(exc).__name__}: {exc}"
        LOGGER.error("Ingestion run failed: %s", error_message)

    # PHASE 9.1. A run that updated metadata and then failed to extract seventy
    # files is not a success, and calling it "completed" is how a broken daily
    # job goes unnoticed for a week. The status says what happened.
    downstream_failures = (stages["extract"]["failed"] + stages["cards"]["failed"])
    if status == STATUS_COMPLETED and downstream_failures:
        status = STATUS_PARTIAL
        warnings.append(
            f"PARTIAL SUCCESS: {stages['extract']['failed']} extraction failure(s) and "
            f"{stages['cards']['failed']} card failure(s). Metadata and the vault were updated, "
            "but the text and cards for those files are not there. Do NOT run --chunk-new or "
            "--embed-new until this is resolved: chunking would index an incomplete set and the "
            "cost would be spent on it.")

    if stages["obsidian"]["updated"] and downstream_failures:
        warnings.append(
            f"The Obsidian vault was refreshed while {downstream_failures} document(s) had no "
            "text or card. The notes that were written are correct as far as they go, but the "
            "vault is describing an incomplete set. Re-run the vault after the repair - the "
            "repair command does this automatically.")

    completed_at = utc_now()
    context = {
        "run_id": run_id, "started_at": started_at, "completed_at": completed_at,
        "source_root": str(source_root), "ticker": (args.ticker or "").upper(),
        "dry_run": args.dry_run, "force_full": args.force_full, "status": status,
        "counts": counts, "stages": stages, "events": events, "warnings": warnings,
        "error_message": error_message, "stale_counts": stale_counts(conn),
        "repair_mode": repair_mode,
        "classified_all": bool(args.chunk_new or args.clear_noneligible_stale_rag),
        "failure_log_rel": (str(failures.path.relative_to(project_root))
                            if failures.opened else ""),
        "failure_count": failures.count,
    }
    context["next_actions"] = next_actions(context)

    # ---- reports (written even for a dry run, and even after a failure) -----
    runs_dir = project_root / "10_Ingestion" / "runs"
    reports_dir = project_root / "10_Ingestion" / "reports"
    manifests_dir = project_root / "10_Ingestion" / "manifests"
    for folder in (runs_dir, reports_dir, manifests_dir):
        assert_inside_project(folder, project_root)
        assert_outside_source(folder, source_root)
        folder.mkdir(parents=True, exist_ok=True)

    manifest_path = manifests_dir / f"ingestion_{slug}_file_changes.csv"
    json_path = runs_dir / f"ingestion_{slug}.json"
    report_path = reports_dir / "latest_ingestion_summary.md"
    dated_report = reports_dir / f"ingestion_{slug}.md"

    context["manifest_rel"] = str(manifest_path.relative_to(project_root))
    context["json_rel"] = str(json_path.relative_to(project_root))
    context["report_rel"] = str(report_path.relative_to(project_root))

    write_manifest(manifest_path, project_root, source_root, events)

    report = build_report(context)
    for path in (report_path, dated_report):
        assert_inside_project(path, project_root)
        assert_outside_source(path, source_root)
        path.write_text(report, encoding="utf-8")

    mirror = project_root / "02_Metadata_Index" / "latest_ingestion_summary.md"
    assert_inside_project(mirror, project_root)
    mirror.parent.mkdir(parents=True, exist_ok=True)
    mirror.write_text(report, encoding="utf-8")

    payload = {k: v for k, v in context.items() if k != "events"}
    payload["pipeline_version"] = PIPELINE_VERSION
    payload["events"] = events
    assert_inside_project(json_path, project_root)
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str),
                         encoding="utf-8")

    if events:
        record_events(conn, run_id, events)
    finish_run(conn, run_id, context)
    conn.close()

    print_summary(context)

    if PRINT_FAILURES:
        LOGGER.warning("%d console line(s) could not be displayed by this terminal. Nothing on "
                       "disk is affected.", PRINT_FAILURES)
    return 0 if status != STATUS_FAILED else 1


def load_rules(project_root: Path, scanner):
    """The Phase 1 rule files, loaded the way Phase 1 loads them."""
    configs_dir = project_root / "configs"
    rules, source_note = scanner.load_yaml_config(
        configs_dir / "source_type_rules.yaml", scanner.FALLBACK_SOURCE_RULES, "classification")
    ticker_rules, ticker_note = scanner.load_yaml_config(
        configs_dir / "ticker_rules.yaml", scanner.FALLBACK_TICKER_RULES, "ticker")
    LOGGER.info("Rules: %s | %s", source_note, ticker_note)
    return rules, ticker_rules


def tagged_with(value: str, ticker: str) -> bool:
    """Is this document tagged with that ticker?

    Phase 9.3. The SQL this replaced was
    `(',' || ticker_candidates_clean || ',') LIKE '%,MSFT,%'`, which matches
    "MSFT" and "AAPL,MSFT" and quietly MISSES "AAPL, MSFT" - a comma followed by
    a space. Any multi-ticker document written that way was invisible to the
    stale worklist, and there is no way to tell from the outside that it was
    being skipped.

    Splitting on the separator and comparing whole tokens cannot have that
    class of bug.
    """
    wanted = (ticker or "").strip().upper()
    if not wanted:
        return False
    for token in str(value or "").replace(";", ",").replace("|", ",").split(","):
        if token.strip().upper() == wanted:
            return True
    return False


def all_stale_rag_documents(conn: sqlite3.Connection) -> list:
    """Every document carrying a stale RAG flag, whatever its ticker.

    Phase 9.3. The previous version asked only for documents matching the
    ticker being indexed - so a stale document belonging to any OTHER ticker was
    never picked up by any command, and looped forever: reported in the count,
    never classified, never cleared.

    Returning all of them means every flag is accounted for by the run that
    claims to deal with flags.
    """
    try:
        rows = conn.execute(
            "SELECT document_id, COALESCE(ticker_candidates_clean,'') AS tickers, "
            "COALESCE(relative_path,'') AS relative_path "
            "FROM documents WHERE COALESCE(stale_rag_index,'0') = '1'").fetchall()
    except sqlite3.OperationalError:
        return []
    return [dict(row) for row in rows]


def stale_rag_document_ids(conn: sqlite3.Connection, ticker: str) -> set:
    """Stale documents for this ticker, from any earlier run.

    A run that updated metadata on Monday and a run that embeds on Tuesday are
    the normal pattern - embedding costs money and is often deferred. The stale
    flag is what carries the work across the gap, so the worklist has to include
    what earlier runs left behind and not only what this walk happened to see.
    """
    return {row["document_id"] for row in all_stale_rag_documents(conn)
            if tagged_with(row["tickers"], ticker)}


def stale_rag_other_tickers(conn: sqlite3.Connection, ticker: str) -> list:
    """Stale documents that this ticker's run can never do anything about."""
    return [row for row in all_stale_rag_documents(conn)
            if not tagged_with(row["tickers"], ticker)]


def build_events(buckets: dict, processed_entries: list, dry_run: bool) -> list:
    """One row per file the run had an opinion about."""
    by_path = {str(e["path"]): e for e in processed_entries if e.get("path") is not None}
    events = []
    for status, entries in buckets.items():
        if status == UNCHANGED:
            continue          # counted, not enumerated - there can be 11,000 of them
        for entry in entries:
            if entry.get("path") is None:
                continue
            processed = by_path.get(str(entry["path"]), entry)
            record = processed.get("record_built") or entry.get("record") or {}
            actions = processed.get("actions") or []
            if dry_run:
                action = "none (dry run)"
            elif status == MISSING:
                action = "reported only; nothing deleted"
            elif actions:
                action = "; ".join(actions)
            elif processed.get("document_id"):
                action = "metadata written"
            else:
                action = "detected, not processed"

            events.append({
                "document_id": record.get("document_id", "") or processed.get("document_id", ""),
                "file_path": str(entry["path"]),
                "relative_path": record.get("relative_path", "")
                or (entry.get("record") or {}).get("relative_path", "")
                or entry["path"].name,
                "extension": record.get("file_extension", "")
                or entry["path"].suffix.lower(),
                "file_size": record.get("file_size_bytes", 0)
                or (entry.get("record") or {}).get("file_size_bytes", 0) or 0,
                "modified_time": record.get("modified_time", "")
                or (entry.get("record") or {}).get("modified_time", ""),
                "old_hash": (entry.get("record") or {}).get("file_hash_sha256", "") or "",
                "new_hash": entry.get("new_hash", "") or record.get("file_hash_sha256", "") or "",
                "change_status": status,
                "action_taken": action,
                "status": "error" if processed.get("error") else "ok",
                "error_message": processed.get("error", "") or "",
                "ticker_candidates": record.get("ticker_candidates_clean", "") or "",
                "note": entry.get("note", ""),
            })
    return events


def print_summary(context: dict) -> None:
    counts = context["counts"]
    stages = context["stages"]
    safe_print()
    safe_print("=" * 78)
    label = "REPAIR" if context.get("repair_mode") else "INCREMENTAL INGESTION"
    safe_print(f"{label} {context['run_id']} - {context['status'].upper()}")
    safe_print("=" * 78)
    safe_print(f"  Source     : {context['source_root']}  (read-only)")
    safe_print(f"  Files seen : {counts['files_seen']:,}")
    safe_print(f"  New        : {counts['new_files']:,}")
    safe_print(f"  Changed    : {counts['changed_files']:,}")
    safe_print(f"  Unchanged  : {counts['unchanged_files']:,}")
    safe_print(f"  Missing    : {counts['missing_files']:,}  (reported, never deleted)")
    safe_print(f"  Skipped    : {counts['skipped_files']:,}")
    safe_print("-" * 78)
    safe_print(f"  Metadata   : {counts['metadata_added']:,} added, "
               f"{counts['metadata_updated']:,} updated")
    safe_print(f"  Extracted  : {stages['extract']['extracted']:,} "
               f"({stages['extract']['failed']:,} failed)")
    safe_print(f"  Cards      : {stages['cards']['created']:,} "
               f"({stages['cards']['failed']:,} failed)")
    safe_print(f"  Obsidian   : {stages['obsidian']['updated']:,} note(s) added")
    safe_print(f"  Chunks     : {stages['chunk']['chunks_created']:,} created, "
               f"{stages['embed']['chunks_embedded']:,} embedded "
               f"(${stages['embed']['estimated_cost_usd']:.6f})")
    chunk = stages["chunk"]
    if any((chunk.get("stale_cleared"), chunk.get("held_missing"),
            chunk.get("held_not_ready"))):
        safe_print(f"  Stale RAG  : {chunk.get('stale_cleared', 0):,} cleared (not eligible), "
                   f"{chunk.get('held_not_ready', 0):,} held (not ready), "
                   f"{chunk.get('held_missing', 0):,} held (source missing)")
    safe_print(f"  Keyword    : {stages['keyword']['message'] or 'not requested'}")
    safe_print(f"  Smoke eval : {stages['smoke']['status']}")
    if context["status"] == STATUS_PARTIAL:
        safe_print("-" * 78)
        safe_print("  PARTIAL SUCCESS - metadata and the vault were updated, but text and cards")
        safe_print("  are missing for the files that failed. This is not a clean run.")
    if context.get("failure_log_rel"):
        safe_print(f"  Full errors: {context['failure_log_rel']}")
    safe_print("-" * 78)
    for step in context["next_actions"]:
        safe_print(f"  -> {step}")
    safe_print("=" * 78)
    safe_print(f"  Report   : {context['report_rel']}")
    safe_print(f"  Manifest : {context['manifest_rel']}")
    safe_print("  Nothing was written to the source archive. Nothing was deleted.")
    safe_print("  AI drafts. Humans verify. Cutler decides.")
    safe_print("=" * 78)


if __name__ == "__main__":
    sys.exit(main())
