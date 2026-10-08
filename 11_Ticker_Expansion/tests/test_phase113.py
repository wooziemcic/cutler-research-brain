#!/usr/bin/env python3
"""Phase 11.3 - the repair that moved chunks instead of adding them.

WHAT HAPPENED
-------------
The Phase 11.2 repairs ran cleanly, every step exited 0, and then the "behind"
counts moved between tickers:

    after repairing MSFT KR MRK T VZ   -> 59 still missing
    after repairing T AMZN ABBV        -> MSFT 9, KR 14, MRK 16, T 8, VZ 59

Nothing was lost and no chunk was deleted. The chunks changed OWNER.

    chunk_id = f"{document_id}_c{chunk_index:04d}"      # no ticker in the id
    CREATE TABLE rag_chunks (chunk_id TEXT PRIMARY KEY, ...)
    INSERT OR REPLACE INTO rag_chunks ...

The ticker is a column, not part of the key. A document tagged with two tickers
has ONE set of chunk rows, so building the second ticker replaces the first
ticker's rows in place. Whichever ticker was built LAST owns the document, and
the other one reports it missing for ever. Repairing that one takes it back.

Measured on the live database: 36,007 chunk rows, 36,007 distinct chunk_ids,
and not one document whose chunks span two tickers - although 101 eligible
documents are claimed by more than one ticker. All 106 "missing" (document,
ticker) pairs were already chunked and embedded under a different ticker.

So these checks are about one property: **a repair adds, or it stops.**

Sections
  1. the collision is detected, named, and not offered as repairable
  2. the repair REFUSES while the chunk key cannot express two tickers
  3. --explain-repair-plan states the adds and proves zero removals
  4. the migration preserves every row and changes no chunk_id
  5. after migrating, a repair is append-only and cross-ticker safe
  6. a repair that DOES take something away is caught and exits 4
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path("/home/claude/CutlerResearchBrain")
SCRIPTS = ROOT / "scripts"
SCRATCH = Path("/tmp/claude-0/-home-claude/a9bffcda-c739-58a7-b47b-503dd7991d44/scratchpad")
P11 = SCRATCH / "p11"
FIXTURE = SCRATCH / "p113_test"

sys.path.insert(0, str(SCRIPTS))
FAILS = []


def check(label, condition, detail=""):
    print(("  PASS  " if condition else "  FAIL  ") + label + (f"   {detail}" if detail else ""))
    if not condition:
        FAILS.append(label)


def code_only(text: str) -> str:
    """Source with docstrings and comments removed - see test_phase112."""
    stripped = re.sub(r'"""(?:.|\n)*?"""', '""', text)
    stripped = re.sub(r"'''(?:.|\n)*?'''", "''", stripped)
    return "\n".join(line for line in stripped.splitlines()
                     if not line.lstrip().startswith("#"))


def run(command, cwd, offline=True):
    environment = dict(os.environ)
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8"
    environment.setdefault("OPENAI_API_KEY", "sk-fake-offline-test")
    if offline:
        # sitecustomize.py puts the fake openai module in every child process,
        # so no network call is possible anywhere in this suite.
        environment["PYTHONPATH"] = str(SCRATCH)
    return subprocess.run(command, cwd=str(cwd), env=environment, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", shell=False,
                          timeout=1800)


# ===========================================================================
# The fixture: documents deliberately shared between tickers.
# ===========================================================================
print("=== 0. FIXTURE ===")
if not P11.exists():
    print("  FAIL  p11 sandbox is missing")
    sys.exit(1)

SHARED = {
    "p113_t_vz_1": "T, VZ",
    "p113_t_vz_2": "T, VZ",
    "p113_t_amzn": "T, AMZN",
    "p113_msft_amzn": "MSFT, AMZN",
    "p113_solo_t": "T",
    "p113_solo_vz": "VZ",
    "p113_solo_amzn": "AMZN",
    "p113_triple": "T, VZ, AMZN",
}
TARGETS = ("T", "VZ", "AMZN", "MSFT")


def build_fixture(path: Path) -> Path:
    if path.exists():
        shutil.rmtree(path)
    shutil.copytree(P11, path)
    for script in ("ticker_selection.py", "inspect_ticker_indexes.py",
                   "reconcile_ticker_index.py", "build_rag_index.py",
                   "build_keyword_index.py", "plan_ticker_expansion.py",
                   "migrate_rag_chunk_key.py"):
        shutil.copy2(SCRIPTS / script, path / "scripts" / script)

    db = path / "data" / "document_metadata.sqlite"
    conn = sqlite3.connect(str(db))
    doc_columns = [row[1] for row in conn.execute("PRAGMA table_info(documents)")]
    ex_columns = [row[1] for row in conn.execute("PRAGMA table_info(pdf_extractions)")]
    for doc_id, tickers in SHARED.items():
        text_dir = path / "03_Extracted_Text" / "2026" / doc_id
        text_dir.mkdir(parents=True, exist_ok=True)
        body = (f"{tickers} joint industry note. Revenue, margins and guidance "
                f"discussed for each company named. ") * 60
        (text_dir / "document_text.txt").write_text(body, encoding="utf-8")
        values = {
            "document_id": doc_id, "file_name": f"{doc_id}.pdf",
            "relative_path": f"2026/09 September/{doc_id}.pdf", "archive_year": "2026",
            "folder_date_guess": "2026-09-01", "source_type_guess": "Industry Research",
            "document_type_guess": "Industry Note", "ticker_candidates_clean": tickers,
            "ticker_candidates_raw": tickers, "pdf_extraction_status": "extracted",
            "pdf_extraction_quality": "Good", "duplicate_status": "unique",
            "review_severity": "Low", "usable_for_rag": 1,
        }
        values = {k: v for k, v in values.items() if k in doc_columns}
        conn.execute(f"INSERT OR REPLACE INTO documents ({', '.join(values)}) "
                     f"VALUES ({', '.join('?' for _ in values)})", tuple(values.values()))
        ex = {"document_id": doc_id, "extraction_status": "extracted",
              "extraction_quality": "Good", "total_text_chars": len(body),
              "document_text_path": f"03_Extracted_Text/2026/{doc_id}/document_text.txt",
              "page_count": 5, "pages_extracted": 5}
        ex = {k: v for k, v in ex.items() if k in ex_columns}
        conn.execute(f"INSERT OR REPLACE INTO pdf_extractions ({', '.join(ex)}) "
                     f"VALUES ({', '.join('?' for _ in ex)})", tuple(ex.values()))
    conn.commit()
    conn.close()
    return db


DB = build_fixture(FIXTURE)
import ticker_selection as ts                                     # noqa: E402

check("fixture built", DB.exists())
conn = sqlite3.connect(str(DB))
conn.row_factory = sqlite3.Row
check("the fixture table is keyed by chunk_id alone, like the live one",
      not ts.chunk_key_is_ticker_scoped(conn))
conn.close()


def reconcile_all(db_path, tickers=TARGETS):
    connection = sqlite3.connect(str(db_path))
    connection.row_factory = sqlite3.Row
    try:
        return {t: ts.reconcile(connection, FIXTURE, t) for t in tickers}
    finally:
        connection.close()


def embedded_map(db_path, tickers=TARGETS):
    connection = sqlite3.connect(str(db_path))
    try:
        return {t: {row[0] for row in connection.execute(
            "SELECT DISTINCT document_id FROM rag_chunks "
            " WHERE UPPER(ticker) = ? AND embedding_status = 'embedded'", (t,))}
            for t in tickers}
    finally:
        connection.close()


RECONCILER = FIXTURE / "scripts" / "reconcile_ticker_index.py"
MIGRATE = FIXTURE / "scripts" / "migrate_rag_chunk_key.py"


def repair(tickers, db_path=None, extra=None):
    command = [sys.executable, str(RECONCILER), "--project-root", str(FIXTURE),
               "--db", str(db_path or DB), "--tickers", *tickers,
               "--repair-missing-chunks", "--max-total-cost", "5", "--confirm-cost",
               "--build-keyword"]
    return run(command + (extra or []), FIXTURE)


# Seed: build T, then VZ, then AMZN on the un-migrated fixture - the exact
# sequence that produced the live problem.
print("\n=== 1. THE COLLISION IS DETECTED AND NAMED ===")
for ticker in ("T", "VZ", "AMZN", "MSFT"):
    result = run([sys.executable, str(FIXTURE / "scripts" / "build_rag_index.py"),
                  "--project-root", str(FIXTURE), "--db", str(DB), "--ticker", ticker], FIXTURE)
    if result.returncode != 0:
        print(f"  (seed build {ticker} exited {result.returncode})")

recons = reconcile_all(DB)
check("a shared document is missing for the ticker that did not build last",
      recons["T"]["missing_count"] > 0 or recons["VZ"]["missing_count"] > 0,
      f"T={recons['T']['missing_count']} VZ={recons['VZ']['missing_count']} "
      f"AMZN={recons['AMZN']['missing_count']}")

claimed = [item for recon in recons.values() for item in recon["missing"]
           if item["reason"] == ts.CLAIMED]
check("the collision is reported as claimed_by_another_ticker, not never_chunked",
      bool(claimed), f"{len(claimed)} claimed")
check("each claimed document names the ticker that owns it",
      all(item["claimed_by"] for item in claimed),
      str(sorted({item["claimed_by"] for item in claimed})[:4]))
check("a claimed document is NOT offered as repairable while the key is unscoped",
      all(item["repairable"] is False for item in claimed))
check("claimed_count is reported per ticker",
      all("claimed_count" in recon for recon in recons.values()))
check("the chunks really do exist, under the other ticker",
      all(item["text_chars_on_disk"] > 0 for item in claimed))

connection = sqlite3.connect(str(DB))
spanning = connection.execute(
    "SELECT COUNT(*) FROM (SELECT document_id FROM rag_chunks "
    " GROUP BY document_id HAVING COUNT(DISTINCT ticker) > 1)").fetchone()[0]
connection.close()
check("no document's chunks span two tickers - the key cannot express it",
      spanning == 0, str(spanning))

# ===========================================================================
print("\n=== 2. THE REPAIR REFUSES WHILE THE KEY IS UNSAFE ===")
before = embedded_map(DB)
result = repair(["VZ"])
check("repairing a ticker with claimed documents exits 5 (unsafe schema)",
      result.returncode == 5, f"rc={result.returncode}")
check("it says what it refused and why", "THE CHUNK KEY DOES NOT NAME A TICKER" in result.stdout
      and "REFUSED" in result.stdout)
check("it names the migration script", "migrate_rag_chunk_key.py" in result.stdout)
check("nothing was embedded on the refusal", embedded_map(DB) == before)
check("the refusal is in the source, not only in this test",
      "EXIT_UNSAFE_SCHEMA" in code_only((SCRIPTS / "reconcile_ticker_index.py").read_text(
          encoding="utf-8")))

# ===========================================================================
print("\n=== 3. --explain-repair-plan ===")
result = run([sys.executable, str(RECONCILER), "--project-root", str(FIXTURE),
              "--db", str(DB), "--tickers", *TARGETS, "--explain-repair-plan"], FIXTURE)
plan_out = result.stdout
check("the plan exits 0", result.returncode == 0, f"rc={result.returncode}")
check("it announces itself as a dry run", "EXPLAIN REPAIR PLAN (dry run)" in plan_out)
check("it reports whether the chunk key names a ticker",
      "chunk key names a ticker: NO" in plan_out)
check("it states the number of removals, and it is zero",
      re.search(r"and 0 REMOVED", plan_out) is not None)
check("it lists blocked document_ids with their current owner",
      re.search(r"!\s+p113_\w+\s+owned by [A-Z]+", plan_out) is not None)
check("blocked documents are not counted as adds",
      re.search(r"\+ 0 to add", plan_out) is not None)
check("nothing was embedded by the plan", embedded_map(DB) == before)
check("the plan says it wrote nothing", "Nothing was embedded" in plan_out)

# ===========================================================================
print("\n=== 4. THE MIGRATION PRESERVES EVERYTHING ===")
connection = sqlite3.connect(str(DB))
rows_before = connection.execute("SELECT COUNT(*) FROM rag_chunks").fetchone()[0]
ids_before = {row[0] for row in connection.execute("SELECT chunk_id FROM rag_chunks")}
status_before = dict(connection.execute(
    "SELECT COALESCE(embedding_status,''), COUNT(*) FROM rag_chunks GROUP BY 1"))
connection.close()

result = run([sys.executable, str(MIGRATE), "--project-root", str(FIXTURE), "--db", str(DB)],
             FIXTURE)
check("the migration is a dry run by default", result.returncode == 0
      and "DRY RUN. Nothing was written." in result.stdout)
connection = sqlite3.connect(str(DB))
check("the dry run changed nothing",
      connection.execute("SELECT COUNT(*) FROM rag_chunks").fetchone()[0] == rows_before)
check("it is still keyed by chunk_id alone after a dry run",
      not ts.chunk_key_is_ticker_scoped(connection))
connection.close()

result = run([sys.executable, str(MIGRATE), "--project-root", str(FIXTURE), "--db", str(DB),
              "--apply"], FIXTURE)
check("the migration applies cleanly", result.returncode == 0, f"rc={result.returncode}")
connection = sqlite3.connect(str(DB))
rows_after = connection.execute("SELECT COUNT(*) FROM rag_chunks").fetchone()[0]
ids_after = {row[0] for row in connection.execute("SELECT chunk_id FROM rag_chunks")}
status_after = dict(connection.execute(
    "SELECT COALESCE(embedding_status,''), COUNT(*) FROM rag_chunks GROUP BY 1"))
backups = [row[0] for row in connection.execute(
    "SELECT name FROM sqlite_master WHERE name LIKE 'rag_chunks_backup_%'")]
check("every row survived", rows_after == rows_before, f"{rows_before} -> {rows_after}")
check("not one chunk_id changed", ids_after == ids_before)
check("no embedding status changed", status_after == status_before, str(status_after))
check("the table is now keyed by (chunk_id, ticker)",
      ts.chunk_key_is_ticker_scoped(connection))
check("a verified backup table was left behind", bool(backups), str(backups))
check("the backup holds the same number of rows",
      connection.execute(f"SELECT COUNT(*) FROM {backups[0]}").fetchone()[0] == rows_before)
connection.close()
check("the migration deletes no chunk",
      "DELETE FROM rag_chunks" not in code_only(
          (SCRIPTS / "migrate_rag_chunk_key.py").read_text(encoding="utf-8")))
check("the migration never opens Chroma",
      "chromadb" not in code_only((SCRIPTS / "migrate_rag_chunk_key.py").read_text(
          encoding="utf-8")))

result = run([sys.executable, str(MIGRATE), "--project-root", str(FIXTURE), "--db", str(DB),
              "--apply"], FIXTURE)
check("running the migration twice is a no-op", result.returncode == 0
      and "Already migrated" in result.stdout)

# ===========================================================================
print("\n=== 5. AFTER MIGRATING, THE REPAIR IS APPEND-ONLY ===")
recons = reconcile_all(DB)
check("claimed documents are now repairable",
      all(item["repairable"] for recon in recons.values() for item in recon["missing"]
          if item["reason"] == ts.CLAIMED))

# 5a. repair ONE ticker: it must gain, and nobody may lose.
before = embedded_map(DB)
result = repair(["VZ"])
after = embedded_map(DB)
check("repairing VZ exits 0", result.returncode == 0,
      f"rc={result.returncode} {result.stderr[-300:]}")
check("VZ did not lose a single embedded document",
      not (before["VZ"] - after["VZ"]), str(sorted(before["VZ"] - after["VZ"])[:5]))
check("VZ gained the documents it was missing", after["VZ"] > before["VZ"],
      f"{len(before['VZ'])} -> {len(after['VZ'])}")
check("repairing VZ took nothing from T",
      not (before["T"] - after["T"]), str(sorted(before["T"] - after["T"])[:5]))
check("repairing VZ took nothing from AMZN",
      not (before["AMZN"] - after["AMZN"]), str(sorted(before["AMZN"] - after["AMZN"])[:5]))
check("repairing VZ took nothing from MSFT", not (before["MSFT"] - after["MSFT"]))
check("no other ticker changed at all",
      all(before[t] == after[t] for t in ("T", "AMZN", "MSFT")))

# 5b. repair T: VZ must be untouched.
before = embedded_map(DB)
result = repair(["T"])
after = embedded_map(DB)
check("repairing T exits 0", result.returncode == 0, f"rc={result.returncode}")
check("repairing T does not change VZ", before["VZ"] == after["VZ"],
      f"lost {sorted(before['VZ'] - after['VZ'])[:5]} gained "
      f"{sorted(after['VZ'] - before['VZ'])[:5]}")
check("T gained rather than lost", after["T"] >= before["T"] and not (before["T"] - after["T"]))

# 5c. repair AMZN: nobody else may change.
before = embedded_map(DB)
result = repair(["AMZN"])
after = embedded_map(DB)
check("repairing AMZN exits 0", result.returncode == 0, f"rc={result.returncode}")
for other in ("MSFT", "T", "VZ"):
    check(f"repairing AMZN does not change {other}", before[other] == after[other],
          f"lost {sorted(before[other] - after[other])[:5]}")

# 5d. everything reaches zero and stays there.
before = embedded_map(DB)
result = repair(list(TARGETS))
recons = reconcile_all(DB)
check("every target ticker reaches Miss = 0",
      all(recon["missing_count"] == 0 for recon in recons.values()),
      str({t: r["missing_count"] for t, r in recons.items()}))
check("nobody lost a document in the full repair",
      all(not (before[t] - embedded_map(DB)[t]) for t in TARGETS))

# 5e. the second run is a no-op.
before = embedded_map(DB)
connection = sqlite3.connect(str(DB))
rows_before = connection.execute("SELECT COUNT(*) FROM rag_chunks").fetchone()[0]
connection.close()
result = repair(list(TARGETS))
after = embedded_map(DB)
connection = sqlite3.connect(str(DB))
rows_after = connection.execute("SELECT COUNT(*) FROM rag_chunks").fetchone()[0]
connection.close()
check("a second repair run is a no-op", before == after and rows_before == rows_after,
      f"rows {rows_before} -> {rows_after}")
check("it says there is nothing to repair",
      "Nothing to repair" in result.stdout, result.stdout[-200:].replace("\n", " "))

recons = reconcile_all(DB)
check("Miss stays 0 on a repeated reconcile",
      all(recon["missing_count"] == 0 for recon in recons.values()),
      str({t: r["missing_count"] for t, r in recons.items()}))

result = run([sys.executable, str(FIXTURE / "scripts" / "inspect_ticker_indexes.py"),
              "--project-root", str(FIXTURE), "--db", str(DB), "--tickers", *TARGETS], FIXTURE)
check("the inspector agrees that nothing is behind",
      result.returncode == 0
      and "No ticker has an eligible document missing" in result.stdout,
      result.stdout.split("BEHIND DETAIL")[-1][:160].replace("\n", " "))

connection = sqlite3.connect(str(DB))
spanning = connection.execute(
    "SELECT COUNT(*) FROM (SELECT document_id FROM rag_chunks "
    " GROUP BY document_id HAVING COUNT(DISTINCT ticker) > 1)").fetchone()[0]
connection.close()
check("shared documents now have chunks under BOTH tickers", spanning > 0, str(spanning))

# ===========================================================================
print("\n=== 6. A REPAIR THAT TAKES SOMETHING AWAY IS CAUGHT ===")
# The guard has to be proved against the real failure, not a mock. A fresh
# un-migrated fixture, repaired with --allow-unsafe-chunk-key, reproduces the
# Phase 11.2 behaviour exactly - and must now be stopped and reported.
UNSAFE = SCRATCH / "p113_unsafe"
DB2 = build_fixture(UNSAFE)
for ticker in ("T", "VZ", "AMZN"):
    run([sys.executable, str(UNSAFE / "scripts" / "build_rag_index.py"),
         "--project-root", str(UNSAFE), "--db", str(DB2), "--ticker", ticker], UNSAFE)


def embedded_map2(tickers=("T", "VZ", "AMZN")):
    connection = sqlite3.connect(str(DB2))
    try:
        return {t: {row[0] for row in connection.execute(
            "SELECT DISTINCT document_id FROM rag_chunks "
            " WHERE UPPER(ticker) = ? AND embedding_status = 'embedded'", (t,))}
            for t in tickers}
    finally:
        connection.close()


before2 = embedded_map2()
result = run([sys.executable, str(UNSAFE / "scripts" / "reconcile_ticker_index.py"),
              "--project-root", str(UNSAFE), "--db", str(DB2), "--tickers", "T", "VZ",
              "--repair-missing-chunks", "--max-total-cost", "5", "--confirm-cost",
              "--allow-unsafe-chunk-key"], UNSAFE)
after2 = embedded_map2()
losers = {t: sorted(before2[t] - after2[t]) for t in before2 if before2[t] - after2[t]}
check("the unsafe repair does take documents from another ticker (the real bug)",
      bool(losers), str({t: len(v) for t, v in losers.items()}))
check("and it is caught: the run stops and exits 4",
      result.returncode == 4, f"rc={result.returncode}")
check("it says which ticker lost what", "STOPPED" in result.stdout
      and "REDUCED what" in result.stdout)
check("it names the lost document_ids", "    lost: " in result.stdout)
check("the guard watches every known ticker, not only the ones named",
      "ALL_KNOWN_TICKERS" in code_only((SCRIPTS / "reconcile_ticker_index.py").read_text(
          encoding="utf-8")))

audits = sorted((UNSAFE / "11_Ticker_Expansion" / "reports").glob(
    "ticker_reconcile_repair_*.json"))
check("the regression is written to the audit record", bool(audits))
if audits:
    payload = json.loads(audits[-1].read_text(encoding="utf-8"))
    check("the audit names the regression", bool(payload.get("regression")))
    check("the audit records that the key was not ticker-scoped",
          payload.get("chunk_key_is_ticker_scoped") is False)

print("\n" + "=" * 70)
if FAILS:
    print(f"{len(FAILS)} FAILING CHECK(S):")
    for name in FAILS:
        print("  - " + name)
    sys.exit(1)
print("ALL CHECKS PASSED")
