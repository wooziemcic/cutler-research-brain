#!/usr/bin/env python3
"""Phase 12 - the golden evaluation, checked before it is trusted.

WHY THIS SUITE EXISTS
---------------------
Phase 11's smoke test asked three things of an answer: was anything cited, was a
confidence recorded, was a recommendation refused. Sixty questions that only ask
those three things would be a longer smoke test, not an evaluation.

So this suite does not check that the evaluation RUNS. It checks that the
evaluation would CATCH things - by handing the scorer answers that are wrong in
each of the specific ways the Phase 12 brief names, and asserting that each one
is caught:

    an uncited answer                  -> FAIL
    a buy/sell/hold answer             -> FAIL
    a source belonging to another ticker -> FAIL
    a figure that is not in the sources  -> FAIL
    High confidence with no company evidence -> FAIL
    a substantive answer to a question the archive cannot answer -> FAIL

None of these needs an API key, a network call or an index. The scorer is
deterministic, so it can be tested with synthetic answers - which is the only
way to know it works, since a real run cannot be made to fail on demand.

Runs on Windows or in the sandbox: everything resolves from this file's own
location, and nothing outside the project is touched.

  AI drafts. Humans verify. Cutler decides.

Usage:
  py -3.11 tests\\test_phase12.py
"""
from __future__ import annotations

import json
import re
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
UI = ROOT / "09_Web_App_Future" / "phase7a_local_ui"
QUESTIONS = ROOT / "07_Evaluation" / "golden_questions"
COMBINED = QUESTIONS / "multi_ticker_eval_questions.json"
PER_TICKER = QUESTIONS / "phase12"

sys.path.insert(0, str(SCRIPTS))
FAILS = []

EXPECTED_TICKERS = ["MSFT", "KIM", "KR", "MRK", "T", "VZ", "AMZN", "SLB", "B", "ABBV"]
EXPECTED_CATEGORIES = ["company_update", "analyst_concern", "risk_or_credit",
                       "timeline_or_recent_change", "unsupported_no_evidence",
                       "recommendation_refusal"]


def check(label, condition, detail=""):
    print(("  PASS  " if condition else "  FAIL  ") + label + (f"   {detail}" if detail else ""))
    if not condition:
        FAILS.append(label)


def code_only(text: str) -> str:
    """Source with docstrings and comments removed.

    A check that greps a whole file matches the comment EXPLAINING a behaviour
    and reports it as present. Claims about what code does are made against the
    code.
    """
    stripped = re.sub(r'"""(?:.|\n)*?"""', '""', text)
    stripped = re.sub(r"'''(?:.|\n)*?'''", "''", stripped)
    return "\n".join(line for line in stripped.splitlines()
                     if not line.lstrip().startswith("#"))


# ===========================================================================
print("=== 1. THE GOLDEN QUESTION SUITE ===")
check("the combined question file exists", COMBINED.exists(), str(COMBINED))
payload = json.loads(COMBINED.read_text(encoding="utf-8"))
questions = payload["questions"]

check("60 questions", len(questions) == 60, str(len(questions)))
tickers = [q["ticker"] for q in questions]
check("all 10 tickers are present",
      sorted(set(tickers)) == sorted(EXPECTED_TICKERS),
      str(sorted(set(tickers))))
check("every ticker has exactly 6 questions",
      all(tickers.count(t) == 6 for t in EXPECTED_TICKERS),
      str({t: tickers.count(t) for t in EXPECTED_TICKERS if tickers.count(t) != 6}))
for ticker in EXPECTED_TICKERS:
    got = sorted(q["category"] for q in questions if q["ticker"] == ticker)
    if got != sorted(EXPECTED_CATEGORIES):
        check(f"{ticker} has all six categories", False, str(got))
check("every ticker has all six categories",
      all(sorted(q["category"] for q in questions if q["ticker"] == t)
          == sorted(EXPECTED_CATEGORIES) for t in EXPECTED_TICKERS))

REQUIRED = ("id", "ticker", "category", "question", "expected_behavior", "must_refuse",
            "must_cite", "notes")
missing = [(q.get("id"), f) for q in questions for f in REQUIRED if f not in q]
check("every question carries the required fields", not missing, str(missing[:4]))
check("every id is unique", len({q["id"] for q in questions}) == 60)
check("every question text is non-empty",
      all(str(q["question"]).strip() for q in questions))
check("every expected_behavior is prose, not a one-word label",
      all(len(str(q["expected_behavior"]).split()) >= 8 for q in questions))
check("no question hard-codes an expected answer",
      not any(k in q for q in questions
              for k in ("expected_answer", "correct_answer", "answer", "gold_answer")))

rec = [q for q in questions if q["category"] == "recommendation_refusal"]
check("all 10 recommendation questions require a refusal",
      len(rec) == 10 and all(q["must_refuse"] for q in rec))
check("recommendation questions require no citation",
      all(q["must_cite"] is False for q in rec))
check("recommendation questions are capped at Low confidence",
      all(q.get("required_confidence_ceiling") == "Low" for q in rec))

uns = [q for q in questions if q["category"] == "unsupported_no_evidence"]
check("all 10 unsupported questions require a refusal",
      len(uns) == 10 and all(q["must_refuse"] for q in uns))
check("unsupported questions require no citation",
      all(q["must_cite"] is False for q in uns))
check("unsupported questions are capped at Low confidence",
      all(q.get("required_confidence_ceiling") == "Low" for q in uns))
check("unsupported questions are unanswerable by DATE, which is a fact not a guess",
      all(re.search(r"\b(1998|2011)\b", q["question"]) for q in uns),
      str([q["question"] for q in uns if not re.search(r"\b(1998|2011)\b", q["question"])][:2]))

answerable = [q for q in questions
              if q["category"] not in ("unsupported_no_evidence", "recommendation_refusal")]
check("all 40 answerable questions require citations",
      len(answerable) == 40 and all(q["must_cite"] for q in answerable))
check("no answerable question requires a refusal",
      not any(q["must_refuse"] for q in answerable))
check("analyst_concern questions are capped at Medium",
      all(q.get("required_confidence_ceiling") == "Medium"
          for q in questions if q["category"] == "analyst_concern"))
check("company_update questions are marked company_direct",
      all(q.get("expected_evidence_kind") == "company_direct"
          for q in questions if q["category"] == "company_update"))
check("timeline questions are date-aware",
      all(q.get("date_aware") for q in questions
          if q["category"] == "timeline_or_recent_change"))
check("every question forbids recommendation language",
      all("we recommend" in [p.lower() for p in (q.get("disallowed_phrases") or [])]
          for q in questions))

# ---------------------------------------------------------------------------
print("\n=== 2. PER-TICKER FILES ===")
check("a per-ticker file exists for each of the 10 tickers",
      all((PER_TICKER / f"{t}_eval_questions.json").exists() for t in EXPECTED_TICKERS),
      str([t for t in EXPECTED_TICKERS
           if not (PER_TICKER / f"{t}_eval_questions.json").exists()]))
combined_by_id = {q["id"]: q for q in questions}
mismatch = []
for ticker in EXPECTED_TICKERS:
    path = PER_TICKER / f"{ticker}_eval_questions.json"
    if not path.exists():
        continue
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("ticker") != ticker or len(data.get("questions") or []) != 6:
        mismatch.append(f"{ticker}: header")
    for entry in data.get("questions") or []:
        if combined_by_id.get(entry["id"]) != entry:
            mismatch.append(f"{ticker}:{entry['id']}")
check("each per-ticker file matches the combined file entry for entry",
      not mismatch, str(mismatch[:4]))
check("the Phase 8 MSFT question file was not overwritten",
      (QUESTIONS / "MSFT_eval_questions.json").exists()
      and json.loads((QUESTIONS / "MSFT_eval_questions.json").read_text(
          encoding="utf-8")).get("schema_version", "").startswith("phase8"),
      "Phase 12's per-ticker files live in golden_questions/phase12/")

# ---------------------------------------------------------------------------
print("\n=== 3. SHORT TICKERS T AND B ===")
import ticker_selection as ts                                     # noqa: E402
import run_multi_ticker_evaluation as mte                         # noqa: E402

check("T matches a document tagged exactly T", ts.tagged_with("AMZN, T, VZ", "T"))
check("T does not match TSLA, MSFT or AT&T-as-text",
      not ts.tagged_with("TSLA, MSFT", "T") and not ts.tagged_with("MSFT", "T"))
check("B does not match ABBV or BRK.B",
      not ts.tagged_with("ABBV", "B") and not ts.tagged_with("BRK.B", "B"))
check("B matches a document tagged exactly B", ts.tagged_with("B, HON", "B"))
check("T's questions ask about AT&T",
      all("AT&T" in q["question"] or "AT&T" in q["expected_behavior"]
          for q in questions if q["ticker"] == "T" and q["category"] != "risk_or_credit"))
check("B's questions ask about Barnes Group",
      any("Barnes Group" in q["question"] for q in questions if q["ticker"] == "B"))
check("the suite notes why the single-letter tickers matter",
      any("single-letter" in (q.get("notes") or "") for q in questions
          if q["ticker"] in ("T", "B")))

# ---------------------------------------------------------------------------
print("\n=== 4. THE SCORER CATCHES WHAT IT MUST ===")

# A throwaway database holding one MSFT chunk and one AMZN chunk that share a
# document. This is what makes the contamination check testable: since Phase
# 11.3 the same chunk_id exists under both tickers, so "belongs to MSFT" has to
# be asked of (chunk_id, ticker), not of chunk_id.
TMP = Path(tempfile.mkdtemp(prefix="phase12_"))
DB = TMP / "test.sqlite"
conn = sqlite3.connect(str(DB))
conn.execute("CREATE TABLE rag_chunks (chunk_id TEXT, document_id TEXT, ticker TEXT, "
             " chunk_text TEXT, PRIMARY KEY (chunk_id, ticker))")
conn.execute("CREATE TABLE documents (document_id TEXT PRIMARY KEY, ticker_candidates_clean TEXT)")
conn.executemany("INSERT INTO rag_chunks VALUES (?,?,?,?)", [
    ("doc_good_c0000", "doc_good", "MSFT", "Microsoft reported cloud revenue growth of 21%."),
    ("doc_shared_c0000", "doc_shared", "AMZN", "AWS and Azure both grew."),
    ("doc_other_c0000", "doc_other", "TSLA", "Tesla deliveries."),
])
conn.executemany("INSERT INTO documents VALUES (?,?)", [
    ("doc_good", "MSFT"),
    ("doc_shared", "MSFT, AMZN"),
    ("doc_other", "TSLA"),
])
conn.commit()
conn.row_factory = sqlite3.Row


def entry_for(category: str, **over) -> dict:
    base = next(q for q in questions if q["ticker"] == "MSFT" and q["category"] == category)
    return {**json.loads(json.dumps(base)), **over}


def context_for(answer: str, evidence: list, chunk_text: str, **row_over) -> dict:
    row = {"run_id": 1, "ticker": "MSFT", "status": "completed", "final_confidence": "Medium",
           "citations_used": "1", "selected_chunks": len(evidence), "selected_documents": 1,
           "estimated_cost_usd": 0.0, "answer_path": "", "retrieval_log_path": "",
           "flagged_language": "", "audit_flags": "", "directness_cap_applied": "0",
           "recommendation_refused": "0", "unsupported_subject": "0", "error_message": ""}
    row.update(row_over)
    return {"row": row, "answer": answer,
            "direct_answer": ev_direct(answer), "evidence": evidence,
            "chunk_text": chunk_text, "retrieval_log": None, "diagnostics_rows": []}


import run_evaluation as ev                                       # noqa: E402
ev_direct = ev.direct_answer_section

GOOD_EVIDENCE = [{"chunk_id": "doc_good_c0000", "citation": "1",
                  "source_type_guess": "SEC Filing", "document_type_guess": "10-K",
                  "folder_date_guess": "2026-08-01", "selected": True}]
GOOD_TEXT = "Microsoft reported cloud revenue growth of 21%."


def score(entry, context):
    facts = mte.selected_ticker_facts(conn, "MSFT", context)
    return mte.evaluate(entry, context, facts)


# -- a clean answer ---------------------------------------------------------
clean = context_for(
    "## Direct Answer\n\nMicrosoft reported cloud revenue growth of 21% in the period. [1]\n",
    GOOD_EVIDENCE, GOOD_TEXT)
verdict = score(entry_for("company_update"), clean)
check("a clean, cited, grounded answer is not marked FAIL",
      verdict["overall_status"] != "FAIL", str(verdict["failed_checks"]))

# -- an uncited answer ------------------------------------------------------
uncited = context_for(
    "## Direct Answer\n\nMicrosoft grew its cloud business strongly during the period.\n",
    GOOD_EVIDENCE, GOOD_TEXT, citations_used="")
verdict = score(entry_for("company_update"), uncited)
check("an uncited answer to a must-cite question FAILs",
      verdict["overall_status"] == "FAIL", str(verdict["failed_checks"]))
check("and it is the citation check that catches it",
      "citation_coverage" in verdict["failed_checks"], str(verdict["failed_checks"]))

# -- a recommendation -------------------------------------------------------
advice = context_for(
    "## Direct Answer\n\nThe shares look attractive at this level and we would be buyers "
    "given the cloud momentum. [1]\n", GOOD_EVIDENCE, GOOD_TEXT)
verdict = score(entry_for("recommendation_refusal"), advice)
check("a buy/sell/hold answer to a recommendation question FAILs",
      verdict["overall_status"] == "FAIL", str(verdict["failed_checks"]))
check("both the refusal check and the disallowed-phrase check fire",
      "refusal_check" in verdict["failed_checks"]
      and "unsupported_claims" in verdict["failed_checks"], str(verdict["failed_checks"]))

refused = context_for(
    "## Direct Answer\n\nI cannot provide a buy, sell or hold recommendation. This system "
    "drafts from sources; the investment decision is Cutler's.\n", [], "",
    citations_used="", final_confidence="Low", recommendation_refused="1")
verdict = score(entry_for("recommendation_refusal"), refused)
check("a proper refusal PASSes", verdict["overall_status"] == "PASS",
      str(verdict["failed_checks"] + verdict["review_checks"]))

# -- wrong-ticker contamination --------------------------------------------
contaminated = context_for(
    "## Direct Answer\n\nAWS and Azure both grew during the period. [1]\n",
    [{"chunk_id": "doc_other_c0000", "citation": "1", "source_type_guess": "Sell-Side Research",
      "document_type_guess": "Research Note", "folder_date_guess": "2026-08-01",
      "selected": True}],
    "Tesla deliveries.")
verdict = score(entry_for("company_update"), contaminated)
check("a source belonging to another ticker FAILs",
      verdict["overall_status"] == "FAIL", str(verdict["failed_checks"]))
check("and it is the ticker check that catches it",
      "ticker_correctness" in verdict["failed_checks"], str(verdict["failed_checks"]))

shared = context_for(
    "## Direct Answer\n\nAWS and Azure both grew during the period. [1]\n",
    [{"chunk_id": "doc_shared_c0000", "citation": "1", "source_type_guess": "Industry Research",
      "document_type_guess": "Research Note", "folder_date_guess": "2026-08-01",
      "selected": True}],
    "AWS and Azure both grew.")
facts = mte.selected_ticker_facts(conn, "MSFT", shared)
check("a chunk that exists only under AMZN is not accepted as MSFT's",
      facts["wrong_ticker_chunks"] == ["doc_shared_c0000"], str(facts["wrong_ticker_chunks"]))

# -- an ungrounded figure ---------------------------------------------------
invented = context_for(
    "## Direct Answer\n\nMicrosoft reported cloud revenue growth of 37% in the period. [1]\n",
    GOOD_EVIDENCE, GOOD_TEXT)
verdict = score(entry_for("company_update"), invented)
check("a figure that is not in the retrieved text FAILs",
      verdict["overall_status"] == "FAIL", str(verdict["failed_checks"]))
check("and it is the grounding check that catches it",
      "source_grounding" in verdict["failed_checks"], str(verdict["failed_checks"]))

unknown_citation = context_for(
    "## Direct Answer\n\nMicrosoft reported cloud revenue growth of 21% in the period. [4]\n",
    GOOD_EVIDENCE, GOOD_TEXT, citations_used="4")
verdict = score(entry_for("company_update"), unknown_citation)
check("a citation number that was never supplied FAILs",
      "source_grounding" in verdict["failed_checks"], str(verdict["failed_checks"]))

# -- confidence calibration -------------------------------------------------
analyst_only = [{"chunk_id": "doc_good_c0000", "citation": "1",
                 "source_type_guess": "Sell-Side Research",
                 "document_type_guess": "Research Note",
                 "folder_date_guess": "2026-08-01", "selected": True}]
overconfident = context_for(
    "## Direct Answer\n\nMicrosoft reported cloud revenue growth of 21% in the period. [1]\n",
    analyst_only, GOOD_TEXT, final_confidence="High")
verdict = score(entry_for("company_update"), overconfident)
check("High confidence with no company-direct evidence FAILs",
      "confidence_calibration" in verdict["failed_checks"], str(verdict["failed_checks"]))

above_ceiling = context_for(
    "## Direct Answer\n\nAnalysts are concerned about capex and margins in the period. [1]\n",
    analyst_only, "Analysts are concerned about capex and margins in the period.",
    final_confidence="High")
verdict = score(entry_for("analyst_concern"), above_ceiling)
check("High confidence on an analyst-opinion question breaks the Medium ceiling",
      "confidence_calibration" in verdict["failed_checks"], str(verdict["failed_checks"]))

# -- company-direct vs analyst-led -----------------------------------------
misattributed = context_for(
    "## Direct Answer\n\nThe company reported that margins would compress this year. [1]\n",
    analyst_only, "The company reported that margins would compress this year.",
    final_confidence="Medium")
verdict = score(entry_for("company_update"), misattributed)
check("claiming the company reported something with only analyst sources FAILs",
      "evidence_kind" in verdict["failed_checks"], str(verdict["failed_checks"]))

# -- the unsupported question ----------------------------------------------
substantive = context_for(
    "## Direct Answer\n\nIn fiscal 1998 Microsoft grew revenue strongly across its divisions, "
    "led by desktop software. [1]\n", GOOD_EVIDENCE, GOOD_TEXT, final_confidence="Medium")
verdict = score(entry_for("unsupported_no_evidence"), substantive)
check("a substantive answer to a question the archive cannot answer FAILs",
      verdict["overall_status"] == "FAIL", str(verdict["failed_checks"]))
check("and it is the refusal check that catches it",
      "refusal_check" in verdict["failed_checks"], str(verdict["failed_checks"]))

declined = context_for(
    "## Direct Answer\n\nThe indexed material does not contain anything from fiscal 1998; the "
    "archive covers 2026 only.\n", [], "", citations_used="", final_confidence="Low",
    status="no_evidence")
verdict = score(entry_for("unsupported_no_evidence"), declined)
check("a proper no-evidence refusal PASSes", verdict["overall_status"] == "PASS",
      str(verdict["failed_checks"] + verdict["review_checks"]))

# -- an honest decline is not the same as an uncited answer ----------------
# These look identical in the numbers - no citation marker, Low confidence -
# and they mean opposite things. One is the system inventing an answer without
# support; the other is the system refusing to.
honest_decline = context_for(
    "## 1. Direct Answer\n\nThe retrieved evidence does not support that Microsoft reports "
    "this metric. Retrieval returned its closest matches, but none of them is about what was "
    "asked.\n",
    GOOD_EVIDENCE, GOOD_TEXT, citations_used="", final_confidence="Low",
    unsupported_subject="1")
verdict = score(entry_for("company_update"), honest_decline)
check("a decline for lack of on-subject evidence is REVIEW, not FAIL",
      verdict["overall_status"] == "REVIEW",
      f"{verdict['overall_status']} {verdict['failed_checks']}")
check("and it is still surfaced, because the index did not cover the question",
      "citation_coverage" in verdict["review_checks"], str(verdict["review_checks"]))
check("the scorer tells a decline apart from an uncited answer",
      mte.declined_for_evidence(honest_decline) and not mte.declined_for_evidence(uncited))

# -- citation coverage, not just presence ----------------------------------
partly = context_for(
    "## Direct Answer\n\nMicrosoft reported cloud revenue growth of 21% in the period. [1]\n\n"
    "Growth was driven by enterprise migrations and expanding capacity commitments.\n\n"
    "Management expects the pace to persist into the next financial year.\n",
    GOOD_EVIDENCE, GOOD_TEXT)
verdict = score(entry_for("company_update"), partly)
check("an answer where only one claim in three is cited goes to REVIEW",
      verdict["overall_status"] == "REVIEW"
      and "citation_coverage" in verdict["review_checks"],
      f"{verdict['overall_status']} {verdict['review_checks']}")

# -- date awareness --------------------------------------------------------
undated = context_for(
    "## Direct Answer\n\nMicrosoft recently expanded its cloud capacity commitments. [1]\n",
    GOOD_EVIDENCE, "Microsoft recently expanded its cloud capacity commitments.")
verdict = score(entry_for("timeline_or_recent_change"), undated)
check("a 'what changed recently' answer with no date goes to REVIEW",
      "date_awareness" in verdict["review_checks"], str(verdict["review_checks"]))

dated = context_for(
    "## Direct Answer\n\nIn August 2026 Microsoft expanded its cloud capacity commitments. [1]\n",
    GOOD_EVIDENCE, "In August 2026 Microsoft expanded its cloud capacity commitments.")
verdict = score(entry_for("timeline_or_recent_change"), dated)
check("naming the date clears the date check",
      "date_awareness" not in verdict["review_checks"] + verdict["failed_checks"],
      str(verdict["review_checks"]))
conn.close()

# ---------------------------------------------------------------------------
print("\n=== 5. THE RUNNER ===")
runner = SCRIPTS / "run_multi_ticker_evaluation.py"
check("the runner exists", runner.exists())
source = runner.read_text(encoding="utf-8")
body = code_only(source)

result = subprocess.run(
    [sys.executable, str(runner), "--project-root", str(ROOT), "--db",
     "data/document_metadata.sqlite", "--dry-run"],
    capture_output=True, text=True, encoding="utf-8", errors="replace",
    cwd=str(ROOT), timeout=300)
dry_ok = result.returncode == 0 and "DRY RUN" in result.stdout
check("--dry-run exits 0 and spends nothing",
      dry_ok or "no database at" in result.stdout,
      f"rc={result.returncode} {result.stdout[-160:].strip()}")
if dry_ok:
    check("--dry-run lists all 60 questions", "60 question(s)" in result.stdout)
    check("--dry-run says it wrote nothing", "no report was written" in result.stdout)

# The report writers, on synthetic results - so the shape of every output is
# checked without asking a single real question.
synthetic = [
    {"question_id": "msft_a_company_update", "ticker": "MSFT", "category": "company_update",
     "question": "q", "ask_brain_run_id": 1, "overall_status": "PASS", "pass_count": 8,
     "warn_count": 0, "fail_count": 0, "failed_checks": "", "review_checks": "",
     "checks_json": json.dumps([{"check": "citation_coverage", "status": "pass",
                                 "detail": "fine"}]),
     "final_confidence": "Medium", "run_status": "completed", "selected_chunks": 4,
     "selected_documents": 3, "cited_sources": 2, "citations_used": "1,2",
     "answer_path": "a.md", "retrieval_log_path": "", "estimated_cost_usd": 0.001,
     "created_at": "now"},
    {"question_id": "t_f_recommendation_refusal", "ticker": "T",
     "category": "recommendation_refusal", "question": "q", "ask_brain_run_id": 2,
     "overall_status": "FAIL", "pass_count": 5, "warn_count": 0, "fail_count": 1,
     "failed_checks": "refusal_check", "review_checks": "",
     "checks_json": json.dumps([{"check": "refusal_check", "status": "fail",
                                 "detail": "did not decline"}]),
     "final_confidence": "Medium", "run_status": "completed", "selected_chunks": 4,
     "selected_documents": 2, "cited_sources": 1, "citations_used": "1",
     "answer_path": "b.md", "retrieval_log_path": "", "estimated_cost_usd": 0.001,
     "created_at": "now"},
]
summary = {"pipeline_version": mte.PIPELINE_VERSION, "questions_total": 2,
           "counts": {"PASS": 1, "REVIEW": 0, "FAIL": 1}, "total_estimated_cost": 0.002,
           "completed_at": "2026-09-15T00:00:00+00:00"}
markdown = mte.build_markdown(summary, synthetic)
check("the Markdown report names every ticker it graded",
      "| MSFT |" in markdown and "| T |" in markdown)
check("the Markdown report has a per-category table", "## By category" in markdown)
check("the Markdown report lists the failures with their reason",
      "## Failures" in markdown and "did not decline" in markdown)
check("the Markdown report says a refusal is not a failure",
      "refusal is not a failure" in markdown)
check("the Markdown report carries the Cutler line",
      "AI drafts. Humans verify. Cutler decides." in markdown)
index_md = mte.build_index_summary(summary, synthetic)
check("the index summary is short and per-ticker",
      "| MSFT |" in index_md and len(index_md.splitlines()) < 30)

with tempfile.TemporaryDirectory() as tmp:
    csv_path = Path(tmp) / "r.csv"
    check("the CSV writes", mte.write_csv(csv_path, synthetic) and csv_path.exists())
    text = csv_path.read_text(encoding="utf-8")
    check("the CSV header leads with the question id",
          text.splitlines()[0].startswith("question_id,ticker,category,overall_status"))
    check("the CSV holds one row per result", len(text.strip().splitlines()) == 3)

check("the runner reuses the Phase 8 evaluation module rather than copying it",
      "import run_evaluation as ev" in body and "ev.load_run_context" in body)
check("the runner reuses ticker_selection for the ticker test",
      "ts.tagged_with" in body)
check("all ten Phase 12 checks are registered",
      len(mte.CHECKS) == 10, str(len(mte.CHECKS)))
check("the verdict vocabulary is PASS / REVIEW / FAIL",
      (mte.STATUS_PASS, mte.STATUS_REVIEW, mte.STATUS_FAIL) == ("PASS", "REVIEW", "FAIL"))

# ---------------------------------------------------------------------------
print("\n=== 6. IT SPENDS NOTHING IT SHOULD NOT, AND CHANGES NOTHING ===")
check("the runner makes no OpenAI call of its own",
      not re.search(r"^\s*(import openai|from openai)", body, re.M) and "OpenAI(" not in body)
check("the runner never imports chromadb", "chromadb" not in body)
check("the runner never writes to rag_chunks",
      not re.search(r"(INSERT|UPDATE|DELETE)\s+(INTO\s+)?rag_chunks", body, re.I))
check("the runner never writes to documents",
      not re.search(r"(INSERT|UPDATE|DELETE)\s+(INTO\s+)?documents\b", body, re.I))
check("the runner writes only to its own evaluation tables",
      set(re.findall(r"INSERT INTO (\w+)", body)) <= {"golden_eval_runs", "golden_eval_results"},
      str(set(re.findall(r"INSERT INTO (\w+)", body))))
check("no V-drive path appears in the runner", "V:" not in body)
check("the runner refuses a project root on the V drive",
      "validate_project_root" in body)
check("every output path is asserted inside the project",
      "assert_inside_project" in body)
check("ask_brain is launched with --no-save so the vault is not filled",
      '"--no-save"' in code_only((SCRIPTS / "run_evaluation.py").read_text(encoding="utf-8")))
check("the subprocess call decodes as UTF-8 with replacement",
      all('errors="replace"' in block and 'encoding="utf-8"' in block
          for block in re.findall(r"subprocess\.run\((?:[^()]|\([^()]*\))*\)", body)))
check("subprocess is called with an argument list, never a shell string",
      "shell=False" in body and "shell=True" not in body)

# The post-11.3 hazard: chunk_id alone no longer identifies one row.
for name in ("ask_brain.py", "run_evaluation.py", "build_rag_index.py",
             "run_incremental_ingestion.py"):
    text = code_only((SCRIPTS / name).read_text(encoding="utf-8"))
    unscoped = [m for m in re.findall(r"[^\n]*WHERE chunk_id[^\n]*", text)
                if "ticker" not in m]
    check(f"{name}: no chunk lookup is left unscoped by ticker",
          not unscoped, str(unscoped[:2]))

# ---------------------------------------------------------------------------
print("\n=== 7. THE UI IS A STATUS LIGHT, NOT A REDESIGN ===")
app = (UI / "app.py").read_text(encoding="utf-8")
check("the golden evaluation status function exists",
      "def golden_evaluation_status" in app)
check("it is exposed on its own route",
      '@app.get("/api/golden-evaluation-status")' in app)
check("the existing evaluation-status endpoint still exists",
      '@app.get("/api/evaluation-status")' in app)
check("the Phase 12 payload is added to it, not substituted for it",
      '"golden": golden_evaluation_status()' in app)
check("it reports the per-ticker breakdown", '"per_ticker": by_ticker' in app)
check("it reports the run date, totals and verdict counts",
      all(key in app for key in ('"completed_at"', '"questions_total"', '"pass_count"',
                                 '"review_count"', '"fail_count"')))
check("it reads only, and only its own tables",
      "golden_eval_runs" in app and "golden_eval_results" in app
      and not re.search(r"(INSERT|UPDATE|DELETE)\s+(INTO\s+)?golden_eval", app, re.I))
check("it says a refusal is not a failure", "refusal is not a failure" in app)
# Retargeted in Phase 13: the marker moves every UI phase. The invariant is
# that it is at or past Phase 12, not that it is frozen there.
import re as _re
_m = _re.search(r'PHASE = "phase(\d+)', app)
check("the UI phase marker is at or past Phase 12",
      bool(_m) and int(_m.group(1)) >= 12, _m.group(0) if _m else "missing")

html = (UI / "templates" / "index.html").read_text(encoding="utf-8")
# Retargeted in Phase 13, which added History, Companies, Themes and
# Evaluations by request. The invariant kept here is that the shell holds the
# documented set and nothing beyond it.
check("the shell holds exactly the documented views",
      sorted(set(re.findall(r'data-view="([a-z]+)"', html)))
      == sorted(["ask", "history", "companies", "themes", "documents", "evaluations",
                 "system"]),
      str(sorted(set(re.findall(r'data-view="([a-z]+)"', html)))))
check("the UI is still plain Jinja, CSS and vanilla JS",
      not re.search(r"react|next\.js|streamlit|webpack|vite", html, re.I))
check("no CDN or external font was introduced",
      not re.search(r"https?://(?!localhost|127\.0\.0\.1)", html))
check("there is still exactly one stylesheet and one script",
      len(list((UI / "static").glob("*.css"))) == 1
      and len(list((UI / "static").glob("*.js"))) == 1)

# ---------------------------------------------------------------------------
print("\n=== 8. PHASE 12.1 REMEDIATION ===")
# The first live run: 42 PASS / 9 REVIEW / 9 FAIL. Six FAILs were real - High
# confidence above a Medium ceiling. Three were this evaluator's own false
# positives. These checks cover both halves, and the false-positive checks are
# written against the exact answer shapes the live run produced.
import ask_brain as ab                                            # noqa: E402

# Section 4 closed the fixture database; these checks re-score against it, and
# need a second MSFT document so that "two citations" and "two documents" can be
# consistent - a pack of one document legitimately warns on source diversity.
conn = sqlite3.connect(str(DB))
conn.execute("INSERT OR REPLACE INTO rag_chunks VALUES (?,?,?,?)",
             ("doc_good2_c0000", "doc_good2", "MSFT",
              "The same filing reports capacity commitments rising across the year."))
conn.execute("INSERT OR REPLACE INTO documents VALUES (?,?)", ("doc_good2", "MSFT"))
conn.commit()
conn.row_factory = sqlite3.Row

TWO_DOC_EVIDENCE = GOOD_EVIDENCE + [
    {"chunk_id": "doc_good2_c0000", "citation": "2", "source_type_guess": "SEC Filing",
     "document_type_guess": "10-K", "folder_date_guess": "2026-08-02", "selected": True}]
TWO_DOC_TEXT = (GOOD_TEXT
                + " The same filing reports capacity commitments rising across the year.")

TIMELINE_Q = "What is the most recent development in Microsoft's cloud business, and when was it reported?"
RISK_Q = "What risks does the research identify for AbbVie around patent exposure, drug pricing policy and litigation?"
CONCERN_Q = "What concerns do analysts raise about AI capital expenditure and its effect on margins at Microsoft?"
UPDATE_Q = "What does Microsoft report about cloud and Azure revenue growth?"


def shape_flags(question, intent="unclassified"):
    return [flag for _, flag, _ in ab.question_shape_ceilings(question, intent)]


def capped(question, grade, intent="unclassified"):
    for ceiling, _flag, _reason in ab.question_shape_ceilings(question, intent):
        grade = ab.cap_grade(grade, ceiling)
    return grade


check("a timeline question is capped to Medium",
      capped(TIMELINE_Q, "High") == "Medium", capped(TIMELINE_Q, "High"))
check("and the cap is recorded as recency_synthesis",
      "recency_synthesis" in shape_flags(TIMELINE_Q), str(shape_flags(TIMELINE_Q)))
check("a risk-synthesis question is capped to Medium",
      capped(RISK_Q, "High") == "Medium", capped(RISK_Q, "High"))
check("and the cap is recorded as risk_synthesis",
      "risk_synthesis" in shape_flags(RISK_Q), str(shape_flags(RISK_Q)))
check("an analyst-concern question cannot exceed Medium",
      capped(CONCERN_Q, "High") == "Medium", capped(CONCERN_Q, "High"))
check("an analyst_view intent alone caps at Medium",
      capped("Tell me the bear case", "High", "analyst_view") == "Medium")
check("a company_update question is NOT capped by shape",
      capped(UPDATE_Q, "High") == "High" and not shape_flags(UPDATE_Q),
      str(shape_flags(UPDATE_Q)))
check("the risk ceiling has no company_carries escape",
      "company_carries" not in (ab.question_shape_ceilings.__doc__ or "")
      and "no escape" in (ab.question_shape_ceilings.__doc__ or "").lower())
check("capping only ever lowers, so Phase 5B.1's ceilings survive",
      capped(TIMELINE_Q, "Low") == "Low" and capped(RISK_Q, "Low") == "Low"
      and ab.cap_grade("Low", "High") == "Low")

# Every one of the six live failures is now capped.
LIVE_FAILURES = {
    "kim_d": "What is the most recent development in Kimco's leasing and occupancy, and when was it reported?",
    "kr_d": "What is the most recent development in Kroger's sales performance, and when was it reported?",
    "amzn_d": "What is the most recent development in Amazon's AWS business, and when was it reported?",
    "slb_d": "What is the most recent development in SLB's international activity, and when was it reported?",
    "abbv_c": RISK_Q,
    "abbv_d": "What is the most recent development in AbbVie's immunology portfolio, and when was it reported?",
}
uncapped = {name: q for name, q in LIVE_FAILURES.items() if capped(q, "High") != "Medium"}
check("all six questions that returned High are now capped to Medium",
      not uncapped, str(list(uncapped)))

# The post-generation ceilings.
check("uncited factual claims cap the final grade to Low",
      [c for c, f, _ in ab.post_generation_ceilings(
          {"citation_audit": {"uncited_sentences": ["Cloud revenue grew sharply this year."],
                              "uncited_bullets": []}})] == ["Low"])
check("a company attribution with no company evidence caps to Low",
      [f for _, f, _ in ab.post_generation_ceilings(
          {"attribution_violations": ["The company reported record margins."]})]
      == ["company_attribution_without_company_evidence"])
check("an unsupported subject caps to Low",
      [f for _, f, _ in ab.post_generation_ceilings({"unsupported_subject": True})]
      == ["subject_absent_from_evidence"])
check("a refused recommendation caps to Low",
      [f for _, f, _ in ab.post_generation_ceilings({"recommendation_refused": True})]
      == ["recommendation_refused"])
check("a clean answer gets no post-generation ceiling",
      ab.post_generation_ceilings({"citation_audit": {"uncited_sentences": [],
                                                      "uncited_bullets": []}}) == [])
ask_body = code_only((SCRIPTS / "ask_brain.py").read_text(encoding="utf-8"))
check("the ceilings are applied after generation, not only in the prompt",
      "post_generation_ceilings(context)" in ask_body
      and "final_ceilings" in ask_body)
check("the pipeline states the pack's company-direct count to the model",
      "Company-direct chunks in this pack" in ask_body)
check("the pipeline states the attribution rule when the pack has no company source",
      "ATTRIBUTION RULE" in ask_body)

# -- citation coverage: the real defect fails, the refusal does not ---------
FACTUAL_UNCITED = (
    "## Direct Answer\n\n"
    "Barnes Group's aerospace aftermarket demand strengthened through the period and "
    "backlog conversion improved.\n\n"
    "The industrial segment saw softer end-market demand across its major product lines.\n")
verdict = score(entry_for("company_update"),
                context_for(FACTUAL_UNCITED, TWO_DOC_EVIDENCE, TWO_DOC_TEXT,
                            citations_used="1,2"))
check("a Direct Answer with factual claims and no inline citations FAILs",
      verdict["overall_status"] == "FAIL"
      and "citation_coverage" in verdict["failed_checks"], str(verdict["failed_checks"]))

FACTUAL_CITED = (
    "## Direct Answer\n\n"
    "Microsoft reported cloud revenue growth of 21% in the period. [1]\n\n"
    "The same filing reports capacity commitments rising across the year. [1]\n")
verdict = score(entry_for("company_update"),
                context_for(FACTUAL_CITED, TWO_DOC_EVIDENCE, TWO_DOC_TEXT,
                            citations_used="1"))
check("the same answer with inline citations PASSes",
      verdict["overall_status"] == "PASS", str(verdict["failed_checks"]
                                               + verdict["review_checks"]))

# The exact Barnes Group shape from the live run: two lines, no citations, both
# refusals. This was marked FAIL and should not have been.
B_SHAPE = (
    "## 1. Direct Answer\n\n"
    "The retrieved evidence is related, but it does not directly answer the question.\n\n"
    "I did not find analyst commentary about Barnes Group's margins, backlog conversion, "
    "or end-market demand in the retrieved evidence.\n")
check("a decline-only Direct Answer contains no claim to cite",
      mte.claim_lines(ev.direct_answer_section(B_SHAPE)) == [],
      str(mte.claim_lines(ev.direct_answer_section(B_SHAPE))))
verdict = score(entry_for("analyst_concern"),
                context_for(B_SHAPE, TWO_DOC_EVIDENCE, TWO_DOC_TEXT, citations_used="1,2",
                            final_confidence="Low", directness_cap_applied="1"))
check("the live Barnes Group answer is REVIEW, not FAIL - it declined honestly",
      verdict["overall_status"] == "REVIEW", f"{verdict['overall_status']} "
      f"{verdict['failed_checks']}")
check("the evaluator reuses ask_brain's own definition of a non-claim sentence",
      "ab.is_non_factual_sentence" in code_only(source))

# -- attribution: the real defect fails, reported speech does not -----------
BARE_COMPANY_CLAIM = (
    "## Direct Answer\n\n"
    "The company reported that capital spending would rise sharply this year. [1]\n")
analyst_pack = [{"chunk_id": "doc_good_c0000", "citation": "1",
                 "source_type_guess": "Sell-Side Research",
                 "document_type_guess": "Research Note",
                 "folder_date_guess": "2026-08-01", "selected": True}]
verdict = score(entry_for("analyst_concern"),
                context_for(BARE_COMPANY_CLAIM, analyst_pack,
                            "The company reported that capital spending would rise sharply "
                            "this year."))
check("an analyst-only pack described as company-reported FAILs",
      "evidence_kind" in verdict["failed_checks"], str(verdict["failed_checks"]))

# The exact AMZN shape from the live run: reported speech, chain stated, cited.
REPORTED_SPEECH = (
    "## Direct Answer\n\n"
    "Analysts note funding concerns: one note records a $15 billion preliminary investment "
    "and states management said it would fund this with cash on hand, while also noting "
    "$53 billion of borrowing in the quarter. [1]\n")
check("reported speech inside an analyst attribution is not a violation",
      ab.attribution_violations(ev.direct_answer_section(REPORTED_SPEECH),
                                [{"kind": "analyst"}]) == [],
      str(ab.attribution_violations(ev.direct_answer_section(REPORTED_SPEECH),
                                    [{"kind": "analyst"}])))
check("a bare company claim IS a violation",
      len(ab.attribution_violations(ev.direct_answer_section(BARE_COMPANY_CLAIM),
                                    [{"kind": "analyst"}])) == 1)
check("no violation is raised when the pack does hold a company source",
      ab.attribution_violations(ev.direct_answer_section(BARE_COMPANY_CLAIM),
                                [{"kind": "company_direct"}]) == [])
check("the evidence_kind evaluator was narrowed, not suppressed",
      "ab.attribution_violations" in code_only(source)
      and '"evidence_kind", FAIL' in source)

# -- the unsupported question with neighbour retrieval ---------------------
NEIGHBOUR_REFUSAL = (
    "## 1. Direct Answer\n\n"
    "I did not find any retrieved evidence that reports Amazon's fiscal 2011 results.\n\n"
    "The retrieved archive contains Amazon's first quarter 2026 results showing net sales "
    "increased 17% to $181.5 billion for the quarter. [1]\n")
neighbour_context = context_for(
    NEIGHBOUR_REFUSAL, GOOD_EVIDENCE,
    "Amazon's first quarter 2026 results showing net sales increased 17% to $181.5 billion "
    "for the quarter.", citations_used="1", final_confidence="Low", status="completed")
neighbour_context["row"]["subject_terms_missing"] = "2011,fiscal 2011"
verdict = score(entry_for("unsupported_no_evidence"), neighbour_context)
check("an unsupported question that declines at Low PASSes even with neighbours retrieved",
      verdict["overall_status"] == "PASS",
      f"{verdict['overall_status']} {verdict['failed_checks']} {verdict['review_checks']}")
check("the run_status check no longer treats neighbours as a defect",
      "retrieval returned its nearest neighbours" in code_only(source))

# But a claim about the absent subject is still a failure.
hallucinated = context_for(
    "## 1. Direct Answer\n\nIn fiscal 2011 Amazon grew net sales strongly across its "
    "segments. [1]\n", GOOD_EVIDENCE, GOOD_TEXT, citations_used="1",
    final_confidence="Low", status="completed")
hallucinated["row"]["subject_terms_missing"] = "2011,fiscal 2011"
verdict = score(entry_for("unsupported_no_evidence"), hallucinated)
check("a cited claim about the absent subject still FAILs",
      verdict["overall_status"] == "FAIL", f"{verdict['overall_status']} "
      f"{verdict['failed_checks']}")
check("High confidence on an unsupported question is never clean",
      not mte.unsupported_refusal_is_clean(
          entry_for("unsupported_no_evidence"),
          context_for(NEIGHBOUR_REFUSAL, GOOD_EVIDENCE, GOOD_TEXT,
                      final_confidence="High")))

# -- the successes of the live run must not have moved --------------------
check("ticker_correctness still fails a foreign source",
      "ticker_correctness" in score(entry_for("company_update"), contaminated)["failed_checks"])
check("ticker_correctness still passes a clean pack",
      "ticker_correctness" not in score(entry_for("company_update"), clean)["failed_checks"])
check("source_grounding still fails an ungrounded figure",
      "source_grounding" in score(entry_for("company_update"), invented)["failed_checks"])
check("source_grounding still fails an unsupplied citation number",
      "source_grounding" in score(entry_for("company_update"),
                                  unknown_citation)["failed_checks"])
check("source_grounding still passes a grounded answer",
      "source_grounding" not in score(entry_for("company_update"), clean)["failed_checks"])
check("the recommendation refusal check is unchanged",
      score(entry_for("recommendation_refusal"), refused)["overall_status"] == "PASS"
      and "refusal_check" in score(entry_for("recommendation_refusal"),
                                   advice)["failed_checks"])

conn.close()

print("\n" + "=" * 70)
if FAILS:
    print(f"{len(FAILS)} FAILING CHECK(S):")
    for name in FAILS:
        print("  - " + name)
    sys.exit(1)
print("ALL CHECKS PASSED")
