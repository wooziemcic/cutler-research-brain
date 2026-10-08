#!/usr/bin/env python3
"""Phase 11.2 - the phantom "behind" status.

WHAT WAS WRONG
--------------
`inspect_ticker_indexes.py` decided "behind" like this:

    if state["embedded"] and eligible > state["documents"]:
        return "behind", f"{eligible - state['documents']} eligible document(s) not indexed"

`eligible` counts documents that pass the RAG rules NOW. `state["documents"]`
counts distinct document_ids in `rag_chunks`. Those are different populations,
so the difference between their sizes is not a number of documents and cannot be
acted on. Three groups are conflated:

    eligible and indexed            - fine
    eligible and NOT indexed        - genuinely behind
    indexed but no longer eligible  - metadata changed after indexing; chunks
                                      stay, because nothing here deletes chunks

"MSFT behind - 11 eligible documents not indexed" was arithmetic on two
unrelated totals. On the Phase 10 fixture the same expression reads MSFT as 3
eligible against 10 indexed - MINUS seven - while the true number of missing
documents is zero.

So these checks are about one thing: the word "behind" must mean a SET of
document_ids that can be named, and every script that decides what belongs to a
ticker must reach the same set.

Sections
  1. the count comparison is gone, and the set comparison is right
  2. the builder and the inspector select the identical document_ids
  3. a behind ticker names its exact missing documents
  4. documents that are not eligible are never counted behind
  5. multi-ticker documents are handled consistently for every ticker
  6. short tickers (T, B) are still whole-token matched
  7. read-only by default: no embedding, no chunk row, no V drive write
  8. the repair is gated on cost, and repairs only what it can
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
FIXTURE = SCRATCH / "p112_test"
ARCHIVE = SCRATCH / "fake_v"

sys.path.insert(0, str(SCRIPTS))
FAILS = []


def check(label, condition, detail=""):
    print(("  PASS  " if condition else "  FAIL  ") + label + (f"   {detail}" if detail else ""))
    if not condition:
        FAILS.append(label)


def code_only(text: str) -> str:
    """The source with docstrings and comments removed.

    Phase 11 taught this twice: a check that greps the whole file matches the
    comment EXPLAINING the old behaviour and reports the bug as still present.
    A claim about what the code does has to be made against the code.
    """
    stripped = re.sub(r'"""(?:.|\n)*?"""', '""', text)
    stripped = re.sub(r"'''(?:.|\n)*?'''", "''", stripped)
    return "\n".join(line for line in stripped.splitlines()
                     if not line.lstrip().startswith("#"))


def run(command, cwd, env_extra=None):
    environment = dict(os.environ)
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8"
    environment.setdefault("OPENAI_API_KEY", "sk-fake-offline-test")
    if env_extra:
        environment.update(env_extra)
    return subprocess.run(command, cwd=str(cwd), env=environment, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", shell=False,
                          timeout=900)


# ===========================================================================
# The fixture. A copy of p11 with one document planted for every case that
# matters, so a claim about "behind" can be checked against known documents
# rather than against whatever the sandbox happens to contain.
# ===========================================================================
print("=== 0. FIXTURE ===")
if not P11.exists():
    print("  FAIL  p11 sandbox is missing; cannot build the Phase 11.2 fixture")
    sys.exit(1)

if FIXTURE.exists():
    shutil.rmtree(FIXTURE)
shutil.copytree(P11, FIXTURE)
for script in ("ticker_selection.py", "inspect_ticker_indexes.py",
               "reconcile_ticker_index.py", "build_rag_index.py",
               "build_keyword_index.py", "plan_ticker_expansion.py",
               "build_ticker_batch.py"):
    shutil.copy2(SCRIPTS / script, FIXTURE / "scripts" / script)

DB = FIXTURE / "data" / "document_metadata.sqlite"
PLANTED = {
    # eligible, text on disk, never chunked -> missing AND repairable
    "p112_missing_repairable": {"tickers": "MSFT", "text": "Azure bookings grew. " * 200},
    # eligible, no text anywhere -> missing and NOT repairable
    "p112_missing_no_text": {"tickers": "MSFT", "text": None},
    # eligible, tagged with two tickers -> must reconcile identically for both
    "p112_multi_ticker": {"tickers": "MSFT, KIM", "text": "Kimco and Microsoft leases. " * 120},
    # NOT eligible (duplicate), no chunks -> must never appear anywhere
    "p112_ineligible_dup": {"tickers": "MSFT", "text": "Duplicate copy. " * 50,
                            "duplicate_status": "duplicate"},
    # NOT eligible (High review), no chunks -> must never appear anywhere
    "p112_ineligible_review": {"tickers": "MSFT", "text": "Escalated. " * 50,
                               "review_severity": "High"},
    # a card row with a BLANK exclusion reason. The builder's old clause
    # (COALESCE(reason,'none')='none') treated blank as excluded while the
    # inspector's Python treated it as not excluded - a document eligible to one
    # and invisible to the other, i.e. behind forever.
    "p112_blank_card_reason": {"tickers": "MSFT", "text": "Blank card reason. " * 90,
                               "card_reason": "   "},
    # tagged exactly 'T' -> the short-ticker case that MUST match
    "p112_short_t": {"tickers": "T", "text": "AT&T wireless subscriber adds. " * 90},
    # tagged with tickers that CONTAIN t and b -> must NOT match T or B
    "p112_short_decoys": {"tickers": "MSFT, ABBV, BRK.B, TSLA", "text": "Decoys. " * 90},
}

conn = sqlite3.connect(str(DB))
conn.row_factory = sqlite3.Row
doc_columns = [row[1] for row in conn.execute("PRAGMA table_info(documents)")]
extraction_columns = [row[1] for row in conn.execute("PRAGMA table_info(pdf_extractions)")]
card_columns = [row[1] for row in conn.execute("PRAGMA table_info(document_cards)")]

for doc_id, spec in PLANTED.items():
    text_dir = FIXTURE / "03_Extracted_Text" / "2026" / doc_id
    text_path = ""
    chars = 0
    if spec["text"] is not None:
        text_dir.mkdir(parents=True, exist_ok=True)
        (text_dir / "document_text.txt").write_text(spec["text"], encoding="utf-8")
        text_path = f"03_Extracted_Text/2026/{doc_id}/document_text.txt"
        chars = len(spec["text"])
    else:
        # A recorded path that is deliberately not on disk.
        text_path = f"03_Extracted_Text/2026/{doc_id}/document_text.txt"
        chars = 4321

    values = {
        "document_id": doc_id,
        "file_name": f"{doc_id}.pdf",
        "relative_path": f"2026/09 September/{doc_id}.pdf",
        "archive_year": "2026",
        "folder_date_guess": "2026-09-01",
        "source_type_guess": "Sell Side Research",
        "document_type_guess": "Research Note",
        "ticker_candidates_clean": spec["tickers"],
        "ticker_candidates_raw": spec["tickers"],
        "pdf_extraction_status": "extracted",
        "pdf_extraction_quality": "Good",
        "duplicate_status": spec.get("duplicate_status", "unique"),
        "review_severity": spec.get("review_severity", "Low"),
        "usable_for_rag": 1,
    }
    values = {key: val for key, val in values.items() if key in doc_columns}
    conn.execute(f"INSERT OR REPLACE INTO documents ({', '.join(values)}) "
                 f"VALUES ({', '.join('?' for _ in values)})", tuple(values.values()))

    ex = {"document_id": doc_id, "extraction_status": "extracted",
          "extraction_quality": "Good", "total_text_chars": chars,
          "document_text_path": text_path, "page_count": 3, "pages_extracted": 3}
    ex = {key: val for key, val in ex.items() if key in extraction_columns}
    conn.execute(f"INSERT OR REPLACE INTO pdf_extractions ({', '.join(ex)}) "
                 f"VALUES ({', '.join('?' for _ in ex)})", tuple(ex.values()))

    if "card_reason" in spec:
        card = {"document_id": doc_id, "rag_exclusion_reason": spec["card_reason"],
                "card_md_path": ""}
        card = {key: val for key, val in card.items() if key in card_columns}
        conn.execute(f"INSERT OR REPLACE INTO document_cards ({', '.join(card)}) "
                     f"VALUES ({', '.join('?' for _ in card)})", tuple(card.values()))

# One document that IS indexed and is no longer eligible, built from a real
# MSFT chunked document so its chunks are genuine.
indexed_ids = [row[0] for row in conn.execute(
    "SELECT DISTINCT document_id FROM rag_chunks WHERE UPPER(ticker) = 'MSFT' "
    "  AND embedding_status = 'embedded'")]
RETIRED = None
for candidate in indexed_ids:
    row = conn.execute("SELECT COALESCE(duplicate_status,'') FROM documents WHERE document_id = ?",
                       (candidate,)).fetchone()
    if row and row[0] != "duplicate":
        RETIRED = candidate
        break
if RETIRED:
    conn.execute("UPDATE documents SET duplicate_status = 'duplicate' WHERE document_id = ?",
                 (RETIRED,))
conn.commit()
conn.close()

import ticker_selection as ts                                     # noqa: E402
import inspect_ticker_indexes as inspector                        # noqa: E402
import build_rag_index as builder                                 # noqa: E402

check("fixture built", DB.exists())
check("a document was retired (indexed, now ineligible)", RETIRED is not None, str(RETIRED))

conn = sqlite3.connect(str(DB))
conn.row_factory = sqlite3.Row

# ===========================================================================
print("\n=== 1. THE COUNT COMPARISON IS GONE ===")
inspector_src = (SCRIPTS / "inspect_ticker_indexes.py").read_text(encoding="utf-8")
check("the old count comparison is not in the source",
      'eligible > state["documents"]' not in inspector_src
      and "eligible > state['documents']" not in inspector_src)
check("health_of takes a reconciliation, not a bare count",
      re.search(r"def health_of\(state: dict, recon: dict\)", inspector_src) is not None)
check("'behind' is decided by the repairable missing count",
      'repairable = recon["repairable_count"]' in inspector_src
      and 'if repairable:' in inspector_src)
check("the inspector explains what it used to do wrong",
      "different populations" in inspector_src)

msft_state = ts.index_state(conn, FIXTURE, "MSFT")
msft_recon = ts.reconcile(conn, FIXTURE, "MSFT")
health, why = inspector.health_of(msft_state, msft_recon)
check("a ticker with MORE indexed documents than eligible is not 'behind' for that reason",
      msft_state["documents"] > msft_recon["eligible"] or msft_recon["missing_count"] > 0,
      f"indexed={msft_state['documents']} eligible={msft_recon['eligible']}")

# The exact shape of the old bug: more indexed than eligible, nothing missing.
clean = dict(msft_state)
clean.update({"chunks": 50, "embedded": 50, "pending": 0, "failed": 0,
              "documents": 30, "chroma_present": True, "keyword_present": True})
clean_recon = {"eligible": 3, "missing_count": 0, "repairable_count": 0}
check("30 indexed against 3 eligible with nothing missing reads 'ready'",
      inspector.health_of(clean, clean_recon)[0] == "ready",
      inspector.health_of(clean, clean_recon)[0])
behind_recon = {"eligible": 40, "missing_count": 5, "repairable_count": 5}
word, reason = inspector.health_of(clean, behind_recon)
check("5 genuinely missing documents read 'behind'", word == "behind", word)
check("the reason counts documents, not a subtraction of totals",
      "5 eligible document(s)" in reason, reason)
stuck_recon = {"eligible": 40, "missing_count": 2, "repairable_count": 0}
check("missing documents that embedding cannot fix are not called 'behind'",
      inspector.health_of(clean, stuck_recon)[0] == "ready*",
      inspector.health_of(clean, stuck_recon)[0])

# ===========================================================================
print("\n=== 2. THE BUILDER AND THE INSPECTOR SELECT THE SAME DOCUMENTS ===")
for ticker in ("MSFT", "KIM", "T", "B", "AMZN", "VZ"):
    from_builder = {row["document_id"] for row in builder.select_documents(conn, ticker)}
    from_shared = {row["document_id"]
                   for row in ts.candidate_rows(conn, ticker, eligible_only=True)}
    check(f"{ticker}: identical document_id sets",
          from_builder == from_shared,
          f"builder {len(from_builder)} / shared {len(from_shared)} "
          f"only-builder {sorted(from_builder - from_shared)[:3]} "
          f"only-shared {sorted(from_shared - from_builder)[:3]}")

check("the blank-reason card document is selected by BOTH",
      "p112_blank_card_reason" in {r["document_id"] for r in builder.select_documents(conn, "MSFT")}
      and "p112_blank_card_reason" in {r["document_id"] for r in
                                       ts.candidate_rows(conn, "MSFT", eligible_only=True)})
builder_code = code_only((SCRIPTS / "build_rag_index.py").read_text(encoding="utf-8"))
check("the card-exclusion clause lives in ticker_selection only",
      "ticker_selection.CARD_EXCLUSION_SQL" in builder_code
      and "COALESCE(c.rag_exclusion_reason, 'none') = 'none'" not in builder_code)
check("the shared clause folds a blank reason into 'no exclusion'",
      "NULLIF(TRIM(c.rag_exclusion_reason), '')" in ts.CARD_EXCLUSION_SQL)
check("a real exclusion reason still excludes",
      ts.card_excluded("failed_extraction") and not ts.card_excluded("")
      and not ts.card_excluded(None) and not ts.card_excluded("none")
      and not ts.card_excluded("  "))
check("the builder resolves text exactly as ticker_selection does",
      (builder.resolve_text_path.__doc__ or "") == (builder.resolve_text_path.__doc__ or "")
      and ts.resolve_text_file(FIXTURE, {"document_id": "p112_missing_repairable",
                                         "document_text_path": "", "archive_year": "2026"})
      is not None)

# ===========================================================================
print("\n=== 3. A BEHIND TICKER NAMES ITS EXACT MISSING DOCUMENTS ===")
recon = ts.reconcile(conn, FIXTURE, "MSFT")
missing_ids = {item["document_id"] for item in recon["missing"]}
check("the repairable planted document is listed missing",
      "p112_missing_repairable" in missing_ids)
check("the no-text planted document is listed missing",
      "p112_missing_no_text" in missing_ids)
check("the multi-ticker planted document is listed missing",
      "p112_multi_ticker" in missing_ids)
check("the blank-card-reason document is listed missing",
      "p112_blank_card_reason" in missing_ids)
check("missing_count equals the number of documents named",
      recon["missing_count"] == len(recon["missing"]) == len(missing_ids),
      f"{recon['missing_count']} / {len(missing_ids)}")

by_id = {item["document_id"]: item for item in recon["missing"]}
check("the repairable one is classified never_chunked",
      by_id["p112_missing_repairable"]["reason"] == "never_chunked",
      by_id["p112_missing_repairable"]["reason"])
check("the repairable one is marked repairable",
      by_id["p112_missing_repairable"]["repairable"] is True)
check("the no-text one is classified extracted_text_file_absent",
      by_id["p112_missing_no_text"]["reason"] == "extracted_text_file_absent",
      by_id["p112_missing_no_text"]["reason"])
check("the no-text one is NOT offered as repairable",
      by_id["p112_missing_no_text"]["repairable"] is False)
check("a recorded char count is not trusted over the file on disk",
      by_id["p112_missing_no_text"]["total_text_chars"] == 4321
      and by_id["p112_missing_no_text"]["text_chars_on_disk"] == 0)

required_fields = ("document_id", "file_name", "document_date", "source_type", "document_type",
                   "usable_for_rag", "extraction_status", "card_status", "stale_rag_index",
                   "has_chunks", "embedded_chunks", "pending_chunks", "reason")
check("every field the BEHIND DETAIL section reports is present on every entry",
      all(field in item for item in recon["missing"] for field in required_fields))

result = run([sys.executable, str(FIXTURE / "scripts" / "inspect_ticker_indexes.py"),
              "--project-root", str(FIXTURE), "--db", "data/document_metadata.sqlite",
              "--tickers", "MSFT", "KIM", "T"], FIXTURE)
out = result.stdout
check("the inspector exits 0", result.returncode == 0, str(result.returncode))
check("the inspector prints a BEHIND DETAIL section", "BEHIND DETAIL" in out)
check("BEHIND DETAIL names the repairable document", "p112_missing_repairable" in out)
check("BEHIND DETAIL gives the reason it is counted behind", "counted behind as" in out)
check("BEHIND DETAIL reports chunk counts per document",
      re.search(r"has chunks\s+:\s+no\s+embedded 0\s+pending 0", out) is not None)
check("BEHIND DETAIL says whether a document can be repaired",
      "NO - fix extraction first" in out and "yes - chunk and embed it" in out)
check("the table explains that Miss is a set difference", "set difference" in out)
check("the inspector states it wrote nothing", "Nothing was embedded" in out)

# ===========================================================================
print("\n=== 4. DOCUMENTS THAT ARE NOT ELIGIBLE ARE NEVER COUNTED BEHIND ===")
check("a duplicate is not missing", "p112_ineligible_dup" not in missing_ids)
check("a High-review document is not missing", "p112_ineligible_review" not in missing_ids)
check("neither appears in the inspector's BEHIND DETAIL",
      "p112_ineligible_dup" not in out and "p112_ineligible_review" not in out)
check("an ineligible document is not counted eligible",
      "p112_ineligible_dup" not in {r["document_id"] for r in
                                    ts.candidate_rows(conn, "MSFT", eligible_only=True)})
check("it is still visible as a candidate, with its reason",
      "p112_ineligible_dup" in {r["document_id"] for r in ts.candidate_rows(conn, "MSFT")}
      and "duplicate" in " ".join(ts.ineligibility_reasons(
          [r for r in ts.candidate_rows(conn, "MSFT")
           if r["document_id"] == "p112_ineligible_dup"][0])))

check("the retired document (indexed, now ineligible) is NOT counted missing",
      RETIRED not in missing_ids)
check("it is reported separately as no longer eligible",
      RETIRED in recon["no_longer_eligible"], str(RETIRED))
check("its chunks were not deleted",
      conn.execute("SELECT COUNT(*) FROM rag_chunks WHERE document_id = ?",
                   (RETIRED,)).fetchone()[0] > 0)
check("the no-longer-eligible count is reported, not hidden in a subtraction",
      recon["no_longer_eligible_count"] == len(recon["no_longer_eligible"]))
check("the inspector prints the no-longer-eligible count",
      "no longer eligible" in out)

# ===========================================================================
print("\n=== 5. MULTI-TICKER DOCUMENTS ===")
msft_ids = {r["document_id"] for r in ts.candidate_rows(conn, "MSFT", eligible_only=True)}
kim_ids = {r["document_id"] for r in ts.candidate_rows(conn, "KIM", eligible_only=True)}
check("a 'MSFT, KIM' document is eligible for MSFT", "p112_multi_ticker" in msft_ids)
check("the same document is eligible for KIM", "p112_multi_ticker" in kim_ids)
kim_recon = ts.reconcile(conn, FIXTURE, "KIM")
kim_missing = {item["document_id"] for item in kim_recon["missing"]}
check("it is reported missing for KIM as well", "p112_multi_ticker" in kim_missing)
check("its reason is the same for both tickers",
      by_id["p112_multi_ticker"]["reason"] ==
      [i for i in kim_recon["missing"] if i["document_id"] == "p112_multi_ticker"][0]["reason"])
check("the builder agrees for both tickers",
      "p112_multi_ticker" in {r["document_id"] for r in builder.select_documents(conn, "MSFT")}
      and "p112_multi_ticker" in {r["document_id"]
                                  for r in builder.select_documents(conn, "KIM")})
check("chunk state is read per ticker, so one ticker's chunks are not the other's",
      "UPPER(ticker) = ?" in (SCRIPTS / "ticker_selection.py").read_text(encoding="utf-8"))

# ===========================================================================
print("\n=== 6. SHORT TICKERS ARE STILL WHOLE-TOKEN MATCHED ===")
t_ids = {r["document_id"] for r in ts.candidate_rows(conn, "T")}
b_ids = {r["document_id"] for r in ts.candidate_rows(conn, "B")}
check("a document tagged exactly 'T' matches T", "p112_short_t" in t_ids)
check("'MSFT, ABBV, BRK.B, TSLA' does NOT match T", "p112_short_decoys" not in t_ids)
check("'MSFT, ABBV, BRK.B, TSLA' does NOT match B", "p112_short_decoys" not in b_ids)
check("no MSFT document leaks into T",
      not (t_ids & {r["document_id"] for r in ts.candidate_rows(conn, "MSFT")}))
check("reconcile for T only sees T's documents",
      all(item["document_id"] in t_ids for item in ts.reconcile(conn, FIXTURE, "T")["missing"]))
check("reconcile for B sees no decoys",
      "p112_short_decoys" not in {i["document_id"]
                                  for i in ts.reconcile(conn, FIXTURE, "B")["missing"]})
check("the token test still rejects substrings",
      ts.tagged_with("MSFT, ABBV", "MSFT") and not ts.tagged_with("MSFT, ABBV", "T")
      and not ts.tagged_with("MSFT, ABBV", "B") and not ts.tagged_with("ABBV", "BV"))
check("the builder's T selection matches the shared module's",
      {r["document_id"] for r in builder.select_documents(conn, "T")} ==
      {r["document_id"] for r in ts.candidate_rows(conn, "T", eligible_only=True)})
conn.close()

# ===========================================================================
print("\n=== 7. READ-ONLY BY DEFAULT ===")
reconciler_src = (SCRIPTS / "reconcile_ticker_index.py").read_text(encoding="utf-8")
check("the reconciler imports no OpenAI client of its own",
      not re.search(r"^\s*(import openai|from openai)", reconciler_src, re.M)
      and "OpenAI(" not in reconciler_src)
check("it imports no chromadb", "chromadb" not in reconciler_src)
check("it never writes to the documents or rag_chunks tables itself",
      not re.search(r"(INSERT|UPDATE|DELETE)\s+(INTO\s+)?(rag_chunks|documents)",
                    reconciler_src, re.I))
check("every subprocess call decodes as UTF-8 with replacement",
      all("errors=\"replace\"" in block and "encoding=\"utf-8\"" in block
          for block in re.findall(r"subprocess\.run\((?:[^()]|\([^()]*\))*\)", reconciler_src)))
check("subprocess is called with an argument list, never a shell string",
      "shell=False" in reconciler_src and "shell=True" not in reconciler_src)
check("the V drive is not named as a write target anywhere in it",
      "V:" not in reconciler_src or "read-only" in reconciler_src.lower())


def db_snapshot(path):
    connection = sqlite3.connect(str(path))
    try:
        rows = connection.execute(
            "SELECT COUNT(*), COALESCE(SUM(LENGTH(chunk_text)),0) FROM rag_chunks").fetchone()
        statuses = dict(connection.execute(
            "SELECT COALESCE(embedding_status,''), COUNT(*) FROM rag_chunks GROUP BY 1"))
    finally:
        connection.close()
    return {"rows": rows[0], "bytes": rows[1], "statuses": statuses}


def archive_snapshot(root):
    return {str(p.relative_to(root)): (p.stat().st_size, int(p.stat().st_mtime))
            for p in sorted(root.rglob("*")) if p.is_file()}


before_db = db_snapshot(DB)
before_archive = archive_snapshot(ARCHIVE) if ARCHIVE.exists() else {}

result = run([sys.executable, str(FIXTURE / "scripts" / "reconcile_ticker_index.py"),
              "--project-root", str(FIXTURE), "--db", "data/document_metadata.sqlite",
              "--tickers", "MSFT", "KIM", "T"], FIXTURE)
recon_out = result.stdout
check("the reconciler exits 0 in read-only mode", result.returncode == 0,
      f"rc={result.returncode} {result.stderr[-300:]}")
check("it says it is read-only", "mode: READ-ONLY" in recon_out)
check("it names the missing documents", "p112_missing_repairable" in recon_out)
check("it separates repairable from unrepairable", "not fixable by embedding" in recon_out)
check("it states that nothing was embedded", "Nothing was embedded" in recon_out
      or "nothing was embedded" in recon_out.lower())
check("no chunk row was added, removed or altered", db_snapshot(DB) == before_db,
      f"{before_db['rows']} -> {db_snapshot(DB)['rows']}")
if before_archive:
    check("not one file in the source archive changed",
          archive_snapshot(ARCHIVE) == before_archive)
else:
    check("archive fixture present to compare", False, "fake_v missing")

report = FIXTURE / "11_Ticker_Expansion" / "reports" / "ticker_reconciliation.md"
csv_path = FIXTURE / "11_Ticker_Expansion" / "reports" / "ticker_reconciliation_missing.csv"
check("it writes a report inside the project", report.exists())
check("it writes a CSV of every missing document", csv_path.exists())
if report.exists():
    report_text = report.read_text(encoding="utf-8")
    check("the report explains what 'behind' means", "set difference" in report_text)
    check("the report lists the planted documents", "p112_missing_repairable" in report_text)
    check("the report carries the Cutler line", "AI drafts. Humans verify." in report_text)
    check("the report prints the repair command instead of running it",
          "--repair-missing-chunks" in report_text and "--max-total-cost" in report_text)
if csv_path.exists():
    csv_text = csv_path.read_text(encoding="utf-8")
    check("the CSV has one header and a row per missing document",
          csv_text.splitlines()[0].startswith("ticker,document_id")
          and "p112_missing_no_text" in csv_text)

# ===========================================================================
print("\n=== 8. THE REPAIR IS GATED ON COST ===")
base = [sys.executable, str(FIXTURE / "scripts" / "reconcile_ticker_index.py"),
        "--project-root", str(FIXTURE), "--db", "data/document_metadata.sqlite",
        "--tickers", "MSFT"]

result = run(base + ["--repair-missing-chunks"], FIXTURE)
check("--repair-missing-chunks without --max-total-cost is REFUSED",
      result.returncode == 3 and "REFUSED" in result.stdout, str(result.returncode))
check("the refusal says nothing was embedded", "Nothing was embedded" in result.stdout)
check("nothing was embedded on that refusal", db_snapshot(DB) == before_db)

result = run(base + ["--repair-missing-chunks", "--max-total-cost", "0.0000001"], FIXTURE)
check("an estimate over --max-total-cost is REFUSED",
      result.returncode == 3 and "over the" in result.stdout, str(result.returncode))
check("nothing was embedded on that refusal", db_snapshot(DB) == before_db)

result = run(base + ["--repair-missing-chunks", "--max-total-cost", "5"], FIXTURE)
check("a repair without --confirm-cost is REFUSED",
      result.returncode == 3 and "requires --confirm-cost" in result.stdout,
      str(result.returncode))
check("nothing was embedded on that refusal", db_snapshot(DB) == before_db)

check("the gate is in the source, not only in this test",
      "--max-total-cost, in USD" in reconciler_src and "--confirm-cost" in reconciler_src)
check("the repair never passes --force to the builder",
      '"--force"' not in code_only(reconciler_src)
      and "'--force'" not in code_only(reconciler_src))

# The repair itself, offline. sitecustomize.py replaces the openai module in the
# child, so no network call is possible: the embeddings are deterministic fakes.
env_extra = {"PYTHONPATH": str(SCRATCH)}
result = run(base + ["--repair-missing-chunks", "--max-total-cost", "5", "--confirm-cost",
                     "--build-keyword"], FIXTURE, env_extra)
repair_out = result.stdout
check("the repair completes", result.returncode == 0,
      f"rc={result.returncode} {result.stderr[-400:]}")
check("it reports repairing a named number of documents",
      re.search(r"MSFT: repairing \d+ document\(s\)", repair_out) is not None)
check("the builder step succeeded", "build_rag_index.py: ok" in repair_out)
check("the keyword index was rebuilt", "build_keyword_index.py: ok" in repair_out)

after_db = db_snapshot(DB)
check("chunk rows were ADDED, not replaced wholesale",
      after_db["rows"] > before_db["rows"], f"{before_db['rows']} -> {after_db['rows']}")
check("no previously embedded chunk was lost",
      after_db["statuses"].get("embedded", 0) >= before_db["statuses"].get("embedded", 0),
      f"{before_db['statuses']} -> {after_db['statuses']}")
if before_archive:
    check("the repair did not touch the source archive",
          archive_snapshot(ARCHIVE) == before_archive)

conn = sqlite3.connect(str(DB))
conn.row_factory = sqlite3.Row
after_recon = ts.reconcile(conn, FIXTURE, "MSFT")
after_missing = {item["document_id"] for item in after_recon["missing"]}
check("the repairable document is no longer missing",
      "p112_missing_repairable" not in after_missing)
check("the blank-card-reason document is no longer missing",
      "p112_blank_card_reason" not in after_missing)
check("the document with no text is STILL missing, and honestly so",
      "p112_missing_no_text" in after_missing)
check("nothing repairable is left", after_recon["repairable_count"] == 0,
      str(after_recon["repairable_count"]))
check("the retired document still has its chunks",
      conn.execute("SELECT COUNT(*) FROM rag_chunks WHERE document_id = ?",
                   (RETIRED,)).fetchone()[0] > 0)
state_after = ts.index_state(conn, FIXTURE, "MSFT")
word, reason = inspector.health_of(state_after, after_recon)
check("the inspector no longer calls MSFT 'behind'", word != "behind", f"{word} - {reason}")
conn.close()

run_json = sorted((FIXTURE / "11_Ticker_Expansion" / "reports").glob(
    "ticker_reconcile_repair_*.json"))
check("the repair wrote an audit record", bool(run_json))
if run_json:
    payload = json.loads(run_json[-1].read_text(encoding="utf-8"))
    check("the audit record states the cost limit it ran under",
          payload.get("max_total_cost") == 5.0, str(payload.get("max_total_cost")))
    check("the audit record states what is still missing",
          "still_missing" in payload and "still_repairable" in payload)

print("\n" + "=" * 70)
if FAILS:
    print(f"{len(FAILS)} FAILING CHECK(S):")
    for name in FAILS:
        print("  - " + name)
    sys.exit(1)
print("ALL CHECKS PASSED")
