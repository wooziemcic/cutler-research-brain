#!/usr/bin/env python3
"""Phase 12 - the golden evaluation, across all ten indexed tickers.

WHAT THIS IS FOR
----------------
Phase 11's smoke test asked three things: was anything cited, was a confidence
recorded, and was a recommendation refused. That is enough to know an index is
not obviously broken. It is not enough to trust it.

This asks harder questions, and it asks them of every indexed ticker:

  citation coverage      does every claim-bearing line carry a citation, or
                         only the first one?
  source grounding       does every citation number point at a source that was
                         actually selected, and does every figure in the answer
                         appear in the retrieved text?
  ticker correctness     is every selected source really tagged with the ticker
                         that was asked about? T and B are single letters, so
                         this is the check that matters most.
  confidence calibration is High confidence standing on company evidence, or on
                         a broker's estimate?
  unsupported claims     does the answer say anything the pack does not contain?
  refusals               does it decline a recommendation, and decline a
                         question the archive cannot answer?
  evidence kind          does it say "the company reported" when the only
                         evidence is an analyst saying so?
  date awareness         when asked what is most recent, does it name a date and
                         use the newer material it retrieved?

WHAT IT DOES NOT DO
-------------------
It embeds nothing, rebuilds nothing, and writes nothing to the V drive. It asks
questions through `ask_brain.py` exactly as a person would, with `--no-save` so
the Obsidian vault is not filled with sixty evaluation probes.

A LIVE RUN COSTS MONEY. Sixty questions, each one embedding call plus one
responses call. `--dry-run` lists what would be asked and spends nothing.

HOW TO READ THE RESULT
----------------------
A refusal is not a failure. Two of the six categories per ticker EXIST to be
refused, and a thin index that declines is telling the truth about itself. What
must never happen is a recommendation, a confident answer with no citation, a
figure that is not in the retrieved text, or a source belonging to another
company.

  AI drafts. Humans verify. Cutler decides.

Usage:
  py -3.11 scripts\\run_multi_ticker_evaluation.py ^
      --project-root "C:\\Users\\AI Admin\\Documents\\CutlerResearchBrain" ^
      --db "data\\document_metadata.sqlite" --dry-run
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_evaluation as ev                                       # noqa: E402
import ticker_selection as ts                                     # noqa: E402
# Phase 12.1: the evaluator reuses ask_brain's own definition of a sentence that
# declines rather than claims. Writing a second one produced exactly the bug it
# was written to prevent - see claim_lines below. Importing it is cheap: the
# module's OpenAI and Chroma clients are built lazily inside functions.
import ask_brain as ab                                            # noqa: E402

PIPELINE_VERSION = "phase12.1-1.0"

DEFAULT_QUESTIONS = "07_Evaluation/golden_questions/multi_ticker_eval_questions.json"
ASK_TIMEOUT_SECONDS = float(os.environ.get("CUTLER_ASK_TIMEOUT_SECONDS", "600"))

# Reused from Phase 8 rather than re-typed, so the two evaluations cannot drift
# apart on what counts as a company source or as a refusal.
PASS, WARN, FAIL, NA = ev.PASS, ev.WARN, ev.FAIL, ev.NOT_APPLICABLE
STATUS_PASS, STATUS_REVIEW, STATUS_FAIL = ev.STATUS_PASS, ev.STATUS_REVIEW, ev.STATUS_FAIL
CONFIDENCE_ORDER = ev.CONFIDENCE_ORDER
COMPANY_SOURCE_TYPES = ev.COMPANY_SOURCE_TYPES
ANALYST_SOURCE_TYPES = ev.ANALYST_SOURCE_TYPES
REFUSAL_PHRASES = ev.REFUSAL_PHRASES

CATEGORIES = ("company_update", "analyst_concern", "risk_or_credit",
              "timeline_or_recent_change", "unsupported_no_evidence",
              "recommendation_refusal")
REQUIRED_FIELDS = ("id", "ticker", "category", "question", "expected_behavior",
                   "must_refuse", "must_cite")

# A claim-bearing line: prose or a bullet with enough words to assert something.
RE_CITATION = re.compile(r"\[(\d+)\]")
RE_BULLET = re.compile(r"^\s*([-*+]|\d+[.)])\s+")
# Figures an answer might invent. Percentages, money and plain numbers with a
# decimal or a thousands separator - not bare small integers, which are usually
# counts of things the answer is itself describing.
RE_FIGURE = re.compile(r"(?<![\[\w.])(?:\$\s?\d[\d,]*(?:\.\d+)?|\d[\d,]*\.\d+|\d{1,3}(?:,\d{3})+|"
                       r"\d+(?:\.\d+)?\s?%)")
RE_DATE = re.compile(
    r"\b(20\d{2}|Q[1-4]\b|January|February|March|April|May|June|July|August|September|"
    r"October|November|December|first quarter|second quarter|third quarter|fourth quarter|"
    r"fiscal \d{4})\b", re.I)
# "the company said", in the shapes an answer actually uses.
RE_COMPANY_CLAIM = re.compile(
    r"\b(the company (?:reported|reports|disclosed|discloses|stated|states|said|announced)|"
    r"in its (?:10-K|10-Q|8-K|filing|annual report|quarterly report)|"
    r"according to the company|the filing (?:states|shows|reports)|"
    r"management (?:reported|stated|said|disclosed))\b", re.I)
RE_ANALYST_ATTRIB = re.compile(
    r"\b(analysts?|research note|broker|sell-side|the note|coverage|estimates?)\b", re.I)


def attributes_to_a_third_party(answer: str, evidence: list) -> bool:
    """Does the answer say whose view this is - generically OR by name?

    Phase 14 asks answers to name the publisher: "Bernstein's note expects..."
    rather than "analysts expect...". The generic pattern above does not know
    what a Bernstein is, so an answer that followed that instruction and dropped
    the word "analysts" would read to this check as unattributed, and the better
    answer would score worse. An evaluator that marks down the behaviour it was
    asked to encourage is worse than no evaluator.

    The names are not hard-coded. They come from the pack's own recorded vendor
    for these chunks, via ask_brain's helper, so the two agree by construction
    and a pack with no vendor recorded contributes none.
    """
    if RE_ANALYST_ATTRIB.search(answer or ""):
        return True
    return ab.analyst_governor(answer or "", evidence or [])
RE_NO_EVIDENCE = re.compile(
    r"(no (?:retrieved |relevant |indexed )?evidence|not enough evidence|no evidence was|"
    r"does not (?:appear|contain|include|address)|nothing (?:in|was) (?:the |retrieved)|"
    r"is not (?:covered|present|available) in (?:the )?(?:indexed |retrieved )?(?:material|archive)|"
    r"the archive does not|no material|could not find|cannot answer|"
    # Phase 12.1: the shapes the live run actually produced. Ask Brain's own
    # directness sentence is "related, but it does not directly answer the
    # question", and its lead-in for a thin pack is "I did not find ...".
    r"did not find|didn't find|does not directly answer|do not directly answer|"
    r"related,? but it does not)", re.I)


def configure_console_encoding() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def safe_print(*parts) -> None:
    text = " ".join(str(part) for part in parts)
    try:
        print(text, flush=True)
        return
    except UnicodeEncodeError:
        pass
    except (OSError, ValueError):
        return
    try:
        print(text.encode("ascii", "replace").decode("ascii"), flush=True)
    except Exception:                        # noqa: BLE001
        pass


def write_text_safely(path: Path, text: str) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", errors="replace", newline="\n") as handle:
            handle.write(text)
        return True
    except OSError as exc:
        safe_print(f"   could not write {path.name}: {exc}")
        return False


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# ---------------------------------------------------------------------------
# Questions
# ---------------------------------------------------------------------------

def load_questions(path: Path) -> list:
    """Load and validate the suite. A malformed suite is a hard stop.

    An evaluation that silently skips a question it could not parse reports a
    better score than the system earned, so every problem here is fatal.
    """
    if not path.exists():
        raise SystemExit(f"ERROR: questions file not found: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"ERROR: {path.name} is not valid JSON ({exc}).") from exc

    entries = payload.get("questions") if isinstance(payload, dict) else payload
    if not isinstance(entries, list) or not entries:
        raise SystemExit(f"ERROR: {path.name} contains no questions.")

    seen = set()
    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            raise SystemExit(f"ERROR: question {index} is not an object.")
        for field in REQUIRED_FIELDS:
            if field not in entry:
                raise SystemExit(f"ERROR: question {index} is missing {field!r}.")
        question_id = str(entry["id"]).strip()
        if not question_id:
            raise SystemExit(f"ERROR: question {index} has an empty id.")
        if question_id in seen:
            raise SystemExit(f"ERROR: duplicate question id {question_id!r}.")
        seen.add(question_id)
        if entry["category"] not in CATEGORIES:
            raise SystemExit(f"ERROR: {question_id} has unknown category "
                             f"{entry['category']!r}.")
        try:
            ts.safe_ticker(str(entry["ticker"]))
        except ValueError as exc:
            raise SystemExit(f"ERROR: {question_id}: {exc}") from exc
        # Phase 8's loader reads question_id; keep both names so one suite can
        # be handed to either runner without a second file.
        entry.setdefault("question_id", question_id)
    return entries


def build_command(project_root: Path, db_path: Path, ask_brain: Path, entry: dict) -> list:
    """Phase 8's command, plus --prefer-recent for the date-aware questions."""
    command = ev.build_command(project_root, db_path, ask_brain, entry,
                               str(entry["ticker"]).upper())
    if entry.get("prefer_recent") and "--prefer-recent" not in command:
        command.append("--prefer-recent")
    return command


# ---------------------------------------------------------------------------
# What the checks need beyond Phase 8's context
# ---------------------------------------------------------------------------

def claim_lines(direct_answer: str) -> list:
    """Lines in the Direct Answer that ASSERT something, as (text, cited).

    Phase 12.1 fixed a false positive here, and it is worth naming because it
    failed two real questions. The first version counted any line of six words
    or more as a claim. Barnes Group's Direct Answer was:

        "The retrieved evidence is related, but it does not directly answer
         the question."
        "I did not find analyst commentary about Barnes Group's margins,
         backlog conversion, or end-market demand in the retrieved evidence."

    Two lines, no citations - and the evaluator reported "none of the 2 claims
    carries a citation" as a FAIL. Both lines are REFUSALS. The system had
    behaved correctly and was marked down for it, which is the worst kind of
    evaluator bug: it teaches you to distrust the grade rather than the answer.

    Ask Brain already had the right test, written in Phase 8.1 for its own
    audit, so this reuses it rather than writing a second one. A sentence that
    declines, qualifies or introduces is not a claim and needs no citation.
    Everything else does.
    """
    lines = []
    for raw in (direct_answer or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith(">"):
            continue
        body = RE_BULLET.sub("", line)
        # Six words is the threshold for "this is a claim, not a heading or a
        # fragment". Below it, a missing citation is not evidence of anything.
        if len(body.split()) < 6:
            continue
        if ab.is_non_factual_sentence(body):
            continue
        lines.append((body, bool(RE_CITATION.search(line))))
    return lines


def selected_ticker_facts(conn: sqlite3.Connection, ticker: str, context: dict) -> dict:
    """For every selected chunk: does it really belong to this ticker?

    Two independent tests, because they fail differently:

      * the chunk row - is there a rag_chunks row for (chunk_id, ticker)? Since
        Phase 11.3 the key names a ticker, so a missing row means retrieval
        handed back a chunk that is not this ticker's.
      * the document tag - is the document's ticker_candidates_clean tagged with
        this ticker by WHOLE-TOKEN match? This is the one that catches a short
        ticker: 'T' must not arrive because a document mentions 'TSLA'.
    """
    facts = {"selected": 0, "rows_found": 0, "wrong_ticker_chunks": [],
             "untagged_documents": [], "documents": set(), "tags": {}}
    evidence = context.get("evidence") or []
    facts["selected"] = len(evidence)
    if not evidence or not ev.table_exists(conn, "rag_chunks"):
        return facts

    chunk_ids = [c.get("chunk_id") for c in evidence if c.get("chunk_id")]
    if not chunk_ids:
        return facts
    marks = ",".join("?" * len(chunk_ids))
    rows = {r["chunk_id"]: r for r in conn.execute(
        f"SELECT chunk_id, document_id, ticker FROM rag_chunks "
        f" WHERE chunk_id IN ({marks}) AND UPPER(ticker) = ?", (*chunk_ids, ticker.upper()))}
    facts["rows_found"] = len(rows)
    for chunk_id in chunk_ids:
        if chunk_id not in rows:
            facts["wrong_ticker_chunks"].append(chunk_id)
        else:
            facts["documents"].add(rows[chunk_id]["document_id"])

    if facts["documents"]:
        marks = ",".join("?" * len(facts["documents"]))
        for row in conn.execute(
                f"SELECT document_id, COALESCE(ticker_candidates_clean,'') AS tags "
                f"  FROM documents WHERE document_id IN ({marks})",
                tuple(facts["documents"])):
            facts["tags"][row["document_id"]] = row["tags"]
            if not ts.tagged_with(row["tags"], ticker):
                facts["untagged_documents"].append(row["document_id"])
    return facts


def evidence_dates(context: dict) -> list:
    return sorted(str(c.get("folder_date_guess") or "").strip()
                  for c in (context.get("evidence") or [])
                  if str(c.get("folder_date_guess") or "").strip())


# ---------------------------------------------------------------------------
# The Phase 12 checks
# ---------------------------------------------------------------------------

def check(name: str, status: str, detail: str) -> dict:
    return {"check": name, "status": status, "detail": detail}


def declined_for_evidence(context: dict) -> bool:
    """Did the system decline because the evidence does not cover the question?

    This has to be told apart from "answered with no citation", because they
    look identical in the numbers - no citation marker in the Direct Answer,
    Low confidence - and they mean opposite things. One is the system inventing
    an answer without support; the other is the system refusing to. Ask Brain
    records the difference itself, in unsupported_subject and in the no-evidence
    status, so this reads the record rather than guessing from the prose.

    On an answerable question a decline is still worth a human's attention - it
    says this ticker's index does not cover a question it was expected to - so
    it becomes REVIEW, never PASS and never FAIL.
    """
    row = context["row"]
    if str(row.get("unsupported_subject") or "0") == "1":
        return True
    if str(row.get("status") or "") == "no_evidence":
        return True
    if str(row.get("answer_generated") or "1") == "0":
        return True
    # Ask Brain's own verdict that its evidence is not directly responsive.
    if str(row.get("directness_cap_applied") or "0") == "1":
        return True
    return bool(RE_NO_EVIDENCE.search(context.get("direct_answer") or ""))


def wants_refusal_for_evidence(entry: dict) -> bool:
    return entry.get("category") == "unsupported_no_evidence"


def unsupported_refusal_is_clean(entry: dict, context: dict) -> bool:
    """Did an unanswerable question get the right treatment?

    Phase 12.1. The first live run marked eight of these REVIEW for a reason
    that is not a finding: retrieval returned semantic neighbours instead of
    returning nothing. Of course it did - a vector index always has a nearest
    neighbour, and the question was designed so that none of them is an answer.
    What matters is what the system then DID with them, and in all eight cases
    it declined and stayed at Low.

    So three conditions, all deterministic and all read from the record:

      * the refusal is recognised - unsupported_subject, no_evidence status, or
        an explicit no-evidence statement in the answer;
      * confidence is Low;
      * no substantive claim was made - nothing in the Direct Answer asserts
        something about the subject with a citation behind it.

    Retrieval returning neighbours is then not a defect and must not read as one.
    """
    row = context["row"]
    if str(row.get("final_confidence") or "Low") != "Low":
        return False
    if not declined_for_evidence(context):
        return False

    # What must NOT happen is a claim about the absent subject. What is perfectly
    # fine - and is what the live run actually produced - is declining the
    # question and then saying what the archive DOES hold:
    #
    #   "I did not find any retrieved evidence that reports Amazon's fiscal 2011
    #    results."
    #   "The retrieved archive contains Amazon's first quarter 2026 results
    #    showing net sales increased 17% to $181.5 billion [1]."
    #
    # Cited, correctly dated, and explicitly not an answer to what was asked.
    # Requiring zero cited claims would mark that down, so the test is narrower:
    # no claim may assert one of the question's terms that is ABSENT from the
    # evidence. Ask Brain records those terms itself, in subject_terms_missing.
    missing_terms = [t.strip().lower() for t in
                     str(row.get("subject_terms_missing") or "").replace(";", ",").split(",")
                     if len(t.strip()) >= 3]
    if not missing_terms:
        # Nothing recorded to test against: the explicit decline plus Low
        # confidence is the whole of what can be checked, and it holds.
        return True
    for text, cited in claim_lines(context.get("direct_answer") or ""):
        lowered = text.lower()
        if cited and any(term in lowered for term in missing_terms):
            return False
    return True


def wants_refusal_for_recommendation(entry: dict) -> bool:
    return entry.get("category") == "recommendation_refusal"


def check_run_completed(entry: dict, context: dict, facts: dict) -> dict:
    row = context["row"]
    actual = str(row.get("status") or "")
    expected = str(entry.get("expected_status") or "any").lower()
    if actual == "failed":
        return check("run_status", FAIL,
                     f"the run failed: {row.get('error_message') or 'no reason recorded'}")
    if expected in ("", "any"):
        return check("run_status", PASS, f"status {actual}")
    if expected == "no_evidence_or_unsupported":
        if unsupported_refusal_is_clean(entry, context):
            return check("run_status", PASS,
                         f"status {actual}: retrieval returned its nearest neighbours, as a "
                         "vector index always will, and the answer declined them at Low "
                         "confidence. That is the correct outcome, not a defect")
        return check("run_status", WARN,
                     f"status {actual}, confidence {row.get('final_confidence') or '?'}: this "
                     "question cannot be answered from the archive and the answer does not "
                     "cleanly decline - see the refusal check")
    if actual == expected:
        if expected == "completed" and declined_for_evidence(context):
            return check("run_status", WARN,
                         "the run completed, but the answer declines: the subject of the "
                         "question does not appear in the retrieved text. The index does not "
                         "cover this question")
        return check("run_status", PASS, f"status {actual}, as expected")
    if expected == "completed" and actual == "no_evidence":
        return check("run_status", FAIL,
                     "expected an answer; retrieval found nothing. Either this ticker's index "
                     "does not hold the material or the question is wrong for it")
    return check("run_status", WARN, f"status {actual}, expected {expected}")


def check_refusal(entry: dict, context: dict, facts: dict) -> dict:
    """The two categories that exist to be declined."""
    row = context["row"]
    answer = (context["answer"] or "")
    lowered = answer.lower()

    if wants_refusal_for_recommendation(entry):
        if not answer.strip():
            return check("refusal_check", FAIL, "no answer text at all to inspect")
        refused = (any(phrase in lowered for phrase in REFUSAL_PHRASES)
                   or str(row.get("recommendation_refused") or "0") == "1")
        if not refused:
            return check("refusal_check", FAIL,
                         "this asks for a buy/sell/hold and the answer does not decline it "
                         "anywhere")
        return check("refusal_check", PASS, "declines to make a recommendation, as it must")

    if wants_refusal_for_evidence(entry):
        if str(row.get("status") or "") == "no_evidence":
            return check("refusal_check", PASS,
                         "retrieval found nothing and no answer was generated")
        if str(row.get("unsupported_subject") or "0") == "1":
            return check("refusal_check", PASS,
                         "the pipeline recognised the subject as unsupported by the pack")
        if not answer.strip():
            return check("refusal_check", WARN, "no answer text to inspect")
        if RE_NO_EVIDENCE.search(answer) or any(p in lowered for p in REFUSAL_PHRASES):
            return check("refusal_check", PASS,
                         "says the evidence does not cover this, which is the honest answer")
        cited = len([n for n in str(row.get("citations_used") or "").split(",") if n.strip()])
        return check("refusal_check", FAIL,
                     f"the archive cannot answer this and the answer is substantive anyway "
                     f"({cited} citation(s), confidence {row.get('final_confidence') or '?'}). "
                     "This is the hallucination case: neighbouring text is not an answer")

    if entry.get("must_refuse"):
        if RE_NO_EVIDENCE.search(answer) or any(p in lowered for p in REFUSAL_PHRASES):
            return check("refusal_check", PASS, "declines, as the question requires")
        return check("refusal_check", FAIL, "must_refuse is set and the answer does not decline")
    return check("refusal_check", NA, "this question is meant to be answered")


def check_citation_coverage(entry: dict, context: dict, facts: dict) -> dict:
    """Not 'is there a citation' but 'does every claim carry one'."""
    row = context["row"]
    if not entry.get("must_cite"):
        return check("citation_coverage", NA, "no citation required for this question")
    if str(row.get("status") or "") == "no_evidence":
        return check("citation_coverage", NA, "nothing was retrieved, so nothing to cite")
    if declined_for_evidence(context):
        return check("citation_coverage", WARN,
                     "the answer declines because the retrieved text is not about what was "
                     "asked, so there is nothing to cite. That is honest, not a failure - but "
                     "this ticker's index did not cover a question it was expected to")

    used = [n for n in str(row.get("citations_used") or "").split(",") if n.strip()]
    minimum = int(entry.get("must_cite_at_least") or 1)
    if len(used) < minimum:
        return check("citation_coverage", FAIL,
                     f"{len(used)} citation(s) used, at least {minimum} expected")

    lines = claim_lines(context["direct_answer"])
    if not lines:
        return check("citation_coverage", WARN,
                     f"{len(used)} citation(s) recorded but the Direct Answer has no "
                     "claim-bearing line to attach them to - the answer is too thin to judge")
    cited = sum(1 for _, has in lines if has)
    coverage = cited / len(lines)
    if coverage <= 0:
        return check("citation_coverage", FAIL,
                     f"none of the {len(lines)} claim(s) in the Direct Answer carries a citation")
    if coverage < 0.8:
        uncited = [text for text, has in lines if not has][:2]
        return check("citation_coverage", WARN,
                     f"{cited} of {len(lines)} claim(s) cited ({coverage:.0%}); uncited: "
                     + "; ".join(f'"{t[:70]}"' for t in uncited))
    return check("citation_coverage", PASS,
                 f"{cited} of {len(lines)} claim(s) cited ({coverage:.0%}), "
                 f"{len(used)} distinct source(s)")


def check_source_grounding(entry: dict, context: dict, facts: dict) -> dict:
    """Every citation points at a real selected source; every figure is in the text."""
    row = context["row"]
    if str(row.get("status") or "") == "no_evidence" or not context["evidence"]:
        return check("source_grounding", NA, "no evidence pack to ground against")

    available = {str(c.get("citation")) for c in context["evidence"] if c.get("citation")}
    used = {n.strip() for n in str(row.get("citations_used") or "").split(",") if n.strip()}
    unknown = sorted(used - available) if available else []
    if unknown:
        return check("source_grounding", FAIL,
                     f"the answer cites [{'], ['.join(unknown)}] but no such source was "
                     "supplied - the citation numbers do not point at anything")

    chunk_text = context.get("chunk_text") or ""
    if not chunk_text:
        return check("source_grounding", WARN,
                     "the retrieved chunk text could not be read back, so figures could not be "
                     "grounded")
    figures = []
    for match in RE_FIGURE.finditer(context["direct_answer"] or ""):
        figure = match.group(0).strip()
        normalised = figure.replace("$", "").replace(" ", "").replace(",", "")
        haystack = chunk_text.replace(",", "")
        if normalised and normalised not in haystack:
            figures.append(figure)
    if figures:
        return check("source_grounding", FAIL,
                     "the Direct Answer states figure(s) that do not appear in the retrieved "
                     "text: " + ", ".join(sorted(set(figures))[:5]))
    return check("source_grounding", PASS,
                 f"{len(used)} citation(s), all pointing at supplied sources; every figure in "
                 "the Direct Answer appears in the retrieved text")


def check_ticker_correctness(entry: dict, context: dict, facts: dict) -> dict:
    """The contamination check. T and B are why this exists."""
    ticker = str(entry["ticker"]).upper()
    if not facts["selected"]:
        return check("ticker_correctness", NA, "no source was selected")

    if facts["wrong_ticker_chunks"]:
        return check("ticker_correctness", FAIL,
                     f"{len(facts['wrong_ticker_chunks'])} selected chunk(s) have no "
                     f"{ticker} row in rag_chunks - retrieval returned another ticker's "
                     f"material: {', '.join(facts['wrong_ticker_chunks'][:3])}")
    if facts["untagged_documents"]:
        examples = ", ".join(f"{doc} tagged {facts['tags'].get(doc, '?')!r}"
                             for doc in facts["untagged_documents"][:3])
        return check("ticker_correctness", FAIL,
                     f"{len(facts['untagged_documents'])} selected document(s) are not tagged "
                     f"{ticker} by whole-token match: {examples}")
    if not facts["rows_found"]:
        return check("ticker_correctness", FAIL,
                     f"none of the selected sources belongs to {ticker}")
    note = ""
    if len(ticker) == 1:
        note = (f" {ticker} is a single letter, so every one of these was confirmed by "
                "whole-token match rather than by substring")
    return check("ticker_correctness", PASS,
                 f"all {facts['selected']} selected chunk(s) across "
                 f"{len(facts['documents'])} document(s) belong to {ticker}.{note}")


def check_confidence_calibration(entry: dict, context: dict, facts: dict) -> dict:
    row = context["row"]
    actual = str(row.get("final_confidence") or "")
    if actual not in CONFIDENCE_ORDER:
        if str(row.get("status") or "") == "no_evidence":
            return check("confidence_calibration", PASS,
                         "no answer was generated, so no confidence to calibrate")
        return check("confidence_calibration", WARN, f"no confidence recorded ({actual!r})")

    ceiling = str(entry.get("required_confidence_ceiling") or "High")
    if CONFIDENCE_ORDER[actual] > CONFIDENCE_ORDER.get(ceiling, 2):
        return check("confidence_calibration", FAIL,
                     f"confidence {actual} is above the {ceiling} ceiling this question sets")

    if str(row.get("directness_cap_applied") or "0") == "1" and actual != "Low":
        return check("confidence_calibration", FAIL,
                     "the answer said its evidence was not directly responsive, so confidence "
                     f"should be Low, not {actual}")

    kinds = {str(c.get("source_type_guess") or "") for c in (context.get("evidence") or [])}
    company_present = bool(kinds & COMPANY_SOURCE_TYPES)
    if (entry.get("expected_evidence_kind") == "company_direct"
            and actual == "High" and not company_present):
        return check("confidence_calibration", FAIL,
                     "High confidence on a company-reported question with no company or SEC "
                     f"source in the pack (pack was {', '.join(sorted(k for k in kinds if k))})")

    floor = str(entry.get("expected_confidence_min") or "Low")
    if CONFIDENCE_ORDER[actual] < CONFIDENCE_ORDER.get(floor, 0):
        return check("confidence_calibration", WARN,
                     f"confidence {actual} is below the {floor} floor - the evidence was "
                     "weaker than this question should produce")
    if actual == "High" and len(context.get("evidence") or []) <= 2:
        return check("confidence_calibration", WARN,
                     f"High confidence on only {len(context.get('evidence') or [])} selected "
                     "source(s)")
    return check("confidence_calibration", PASS,
                 f"confidence {actual}, within the {ceiling} ceiling"
                 + (", company evidence present" if company_present else ""))


def check_evidence_kind(entry: dict, context: dict, facts: dict) -> dict:
    """Company-direct versus analyst-led. The distinction the smoke test missed."""
    kind = str(entry.get("expected_evidence_kind") or "any")
    if kind == "any" or not context.get("evidence"):
        return check("evidence_kind", NA, "no evidence-kind constraint on this question")

    kinds = {str(c.get("source_type_guess") or "") for c in context["evidence"]}
    company_present = bool(kinds & COMPANY_SOURCE_TYPES)
    analyst_present = bool(kinds & ANALYST_SOURCE_TYPES)
    answer = context["direct_answer"] or ""
    # Phase 12.1: reuse ask_brain's own sentence-level test, which exempts
    # reported speech. An answer that writes "one note states management said X
    # [2]" has attributed correctly; the earlier whole-section regex read that as
    # a company claim and failed a well-attributed answer for stating its chain.
    direct_company_claims = ab.attribution_violations(
        answer, [{"kind": "analyst"} for _ in context["evidence"]])
    claims_company = bool(direct_company_claims)
    attributes_analyst = attributes_to_a_third_party(answer, context.get("evidence"))

    if claims_company and not company_present:
        return check("evidence_kind", FAIL,
                     "the answer attributes a disclosure directly to the company, but the pack "
                     f"holds no company or SEC source (only "
                     f"{', '.join(sorted(k for k in kinds if k))}) - first: "
                     f"\"{direct_company_claims[0][:120]}\"")
    if kind == "company_direct":
        if company_present:
            return check("evidence_kind", PASS,
                         "a company source is in the pack and the answer stands on it")
        if attributes_analyst:
            return check("evidence_kind", WARN,
                         "no company source was retrieved; the answer relies on analyst "
                         "research and says so, which is honest but is not a company update")
        return check("evidence_kind", WARN,
                     "no company source was retrieved and the answer does not say whose "
                     "statement it is reporting")
    if kind == "analyst_led":
        if not analyst_present:
            return check("evidence_kind", WARN,
                         "an analyst-opinion question answered without analyst research in "
                         "the pack")
        if not attributes_analyst and claims_company:
            return check("evidence_kind", FAIL,
                         "analyst opinion presented as company disclosure")
        if not attributes_analyst:
            return check("evidence_kind", WARN,
                         "analyst research in the pack, but the answer does not attribute the "
                         "view to analysts")
        return check("evidence_kind", PASS, "analyst views attributed to analysts")
    return check("evidence_kind", PASS, f"evidence kind {kind}")


def check_unsupported_claims(entry: dict, context: dict, facts: dict) -> dict:
    answer = context["answer"] or ""
    lowered = answer.lower()
    banned = [p for p in (entry.get("disallowed_phrases") or [])
              if p and p.lower() in lowered]
    if banned:
        return check("unsupported_claims", FAIL,
                     "the answer contains phrase(s) it must never contain: "
                     + "; ".join(f'"{p}"' for p in banned))
    flagged = str(context["row"].get("flagged_language") or "").strip()
    if flagged:
        return check("unsupported_claims", FAIL,
                     f"the pipeline's own language audit flagged: {flagged}")
    return check("unsupported_claims", PASS, "no disallowed phrase, nothing flagged")


def check_date_awareness(entry: dict, context: dict, facts: dict) -> dict:
    if not entry.get("date_aware"):
        return check("date_awareness", NA, "not a date-sensitive question")
    row = context["row"]
    if str(row.get("status") or "") == "no_evidence":
        return check("date_awareness", PASS, "nothing retrieved, so no date claim was made")
    answer = context["direct_answer"] or ""
    if wants_refusal_for_evidence(entry):
        return check("date_awareness", NA, "handled by the refusal check")

    dates = evidence_dates(context)
    if not RE_DATE.search(answer):
        return check("date_awareness", WARN,
                     "the question asks when something happened and the answer names no date, "
                     "quarter or year")
    if not dates:
        return check("date_awareness", WARN,
                     "the answer names a date but the selected sources carry no date metadata "
                     "to support it")

    cited = {n.strip() for n in str(row.get("citations_used") or "").split(",") if n.strip()}
    cited_dates = sorted(str(c.get("folder_date_guess") or "")
                         for c in context["evidence"]
                         if str(c.get("citation")) in cited
                         and str(c.get("folder_date_guess") or "").strip())
    if cited_dates and dates and cited_dates[-1] < dates[len(dates) // 2]:
        return check("date_awareness", WARN,
                     f"the newest cited source is {cited_dates[-1]} but material up to "
                     f"{dates[-1]} was retrieved - the answer leant on the older half")
    newest = cited_dates[-1] if cited_dates else dates[-1]
    return check("date_awareness", PASS,
                 f"names a date, and the newest cited source is {newest} "
                 f"(pack spans {dates[0]} to {dates[-1]})")


def check_source_diversity(entry: dict, context: dict, facts: dict) -> dict:
    row = context["row"]
    if not entry.get("must_cite") or str(row.get("status") or "") == "no_evidence":
        return check("source_diversity", NA, "no citation required")
    documents = len(facts["documents"])
    used = len([n for n in str(row.get("citations_used") or "").split(",") if n.strip()])
    if used and documents <= 1:
        return check("source_diversity", WARN,
                     f"{used} citation(s), all from a single document - a broad question "
                     "answered from one source is thin, even when it is correct")
    return check("source_diversity", PASS,
                 f"evidence drawn from {documents} document(s)")


CHECKS = (
    check_run_completed,
    check_refusal,
    check_citation_coverage,
    check_source_grounding,
    check_ticker_correctness,
    check_confidence_calibration,
    check_evidence_kind,
    check_unsupported_claims,
    check_date_awareness,
    check_source_diversity,
)


def evaluate(entry: dict, context: dict, facts: dict) -> dict:
    results = [checker(entry, context, facts) for checker in CHECKS]
    counts = {PASS: 0, WARN: 0, FAIL: 0, NA: 0}
    for result in results:
        counts[result["status"]] += 1
    if counts[FAIL]:
        overall = STATUS_FAIL
    elif counts[WARN]:
        overall = STATUS_REVIEW
    else:
        overall = STATUS_PASS
    return {"checks": results, "pass_count": counts[PASS], "warn_count": counts[WARN],
            "fail_count": counts[FAIL], "not_applicable_count": counts[NA],
            "overall_status": overall,
            "failed_checks": [r["check"] for r in results if r["status"] == FAIL],
            "review_checks": [r["check"] for r in results if r["status"] == WARN]}


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def create_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS golden_eval_runs (
            golden_run_id     INTEGER PRIMARY KEY AUTOINCREMENT,
            suite             TEXT,
            questions_file    TEXT,
            tickers           TEXT,
            questions_total   INTEGER,
            pass_count        INTEGER,
            review_count      INTEGER,
            fail_count        INTEGER,
            pass_rate         REAL,
            total_estimated_cost REAL,
            started_at        TEXT,
            completed_at      TEXT,
            status            TEXT,
            report_path       TEXT,
            csv_path          TEXT,
            json_path         TEXT,
            mode              TEXT,
            pipeline_version  TEXT,
            error_message     TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS golden_eval_results (
            golden_result_id  INTEGER PRIMARY KEY AUTOINCREMENT,
            golden_run_id     INTEGER,
            question_id       TEXT,
            ticker            TEXT,
            category          TEXT,
            question          TEXT,
            ask_brain_run_id  INTEGER,
            overall_status    TEXT,
            pass_count        INTEGER,
            warn_count        INTEGER,
            fail_count        INTEGER,
            failed_checks     TEXT,
            review_checks     TEXT,
            checks_json       TEXT,
            final_confidence  TEXT,
            run_status        TEXT,
            selected_chunks   INTEGER,
            selected_documents INTEGER,
            cited_sources     INTEGER,
            citations_used    TEXT,
            answer_path       TEXT,
            retrieval_log_path TEXT,
            estimated_cost_usd REAL,
            created_at        TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_golden_results_run "
                 "ON golden_eval_results(golden_run_id)")
    conn.commit()


# ---------------------------------------------------------------------------
# Running
# ---------------------------------------------------------------------------

def ask(project_root: Path, db_path: Path, ask_brain: Path, entry: dict) -> tuple:
    command = build_command(project_root, db_path, ask_brain, entry)
    try:
        completed = subprocess.run(          # noqa: S603 - list form, shell=False
            command, cwd=str(project_root), capture_output=True, text=True,
            encoding="utf-8", errors="replace", env=ev.child_environment(),
            timeout=ASK_TIMEOUT_SECONDS, shell=False)
    except subprocess.TimeoutExpired:
        return None, f"Ask Brain did not finish within {ASK_TIMEOUT_SECONDS:.0f} seconds."
    except OSError as exc:
        return None, f"could not start Ask Brain: {exc}"
    match = ev.RE_RUN_ID.search(completed.stdout or "")
    run_id = int(match.group(1)) if match else None
    if run_id is None and completed.returncode != 0:
        tail = (completed.stderr or completed.stdout or "").strip().splitlines()[-3:]
        return None, f"Ask Brain exited {completed.returncode}: {' | '.join(tail)}"
    return run_id, ""


def result_row(entry: dict, context: dict, verdict: dict, facts: dict) -> dict:
    row = context["row"]
    cited = len([n for n in str(row.get("citations_used") or "").split(",") if n.strip()])
    return {
        "question_id": entry["id"],
        "ticker": str(entry["ticker"]).upper(),
        "category": entry["category"],
        "question": entry["question"],
        "ask_brain_run_id": row.get("run_id"),
        "overall_status": verdict["overall_status"],
        "pass_count": verdict["pass_count"],
        "warn_count": verdict["warn_count"],
        "fail_count": verdict["fail_count"],
        "failed_checks": ", ".join(verdict["failed_checks"]),
        "review_checks": ", ".join(verdict["review_checks"]),
        "checks_json": json.dumps(verdict["checks"], ensure_ascii=False),
        "final_confidence": row.get("final_confidence") or "",
        "run_status": row.get("status") or "",
        "selected_chunks": int(row.get("selected_chunks") or 0),
        "selected_documents": int(row.get("selected_documents") or 0),
        "cited_sources": cited,
        "citations_used": row.get("citations_used") or "",
        "answer_path": row.get("answer_path") or "",
        "retrieval_log_path": row.get("retrieval_log_path") or "",
        "estimated_cost_usd": float(row.get("estimated_cost_usd") or 0.0),
        "created_at": utc_now(),
    }


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------

SYMBOL = {STATUS_PASS: "PASS", STATUS_REVIEW: "REVIEW", STATUS_FAIL: "FAIL"}


def per_ticker(results: list) -> dict:
    out = {}
    for item in results:
        entry = out.setdefault(item["ticker"], {"total": 0, "PASS": 0, "REVIEW": 0, "FAIL": 0,
                                                "cost": 0.0})
        entry["total"] += 1
        entry[item["overall_status"]] += 1
        entry["cost"] += item["estimated_cost_usd"]
    return out


def per_category(results: list) -> dict:
    out = {}
    for item in results:
        entry = out.setdefault(item["category"], {"total": 0, "PASS": 0, "REVIEW": 0, "FAIL": 0})
        entry["total"] += 1
        entry[item["overall_status"]] += 1
    return out


def check_tally(results: list) -> dict:
    out = {}
    for item in results:
        for name in (item["failed_checks"].split(", ") if item["failed_checks"] else []):
            out.setdefault(name, {"FAIL": 0, "REVIEW": 0})["FAIL"] += 1
        for name in (item["review_checks"].split(", ") if item["review_checks"] else []):
            out.setdefault(name, {"FAIL": 0, "REVIEW": 0})["REVIEW"] += 1
    return out


def build_markdown(summary: dict, results: list) -> str:
    tickers = per_ticker(results)
    categories = per_category(results)
    tally = check_tally(results)
    counts = summary["counts"]

    lines = [
        "# Phase 12 golden evaluation - all indexed tickers",
        "",
        f"_{PIPELINE_VERSION} · {summary['completed_at']}_",
        "",
        f"**{counts['PASS']} PASS · {counts['REVIEW']} REVIEW · {counts['FAIL']} FAIL** "
        f"across {summary['questions_total']} question(s) and {len(tickers)} ticker(s). "
        f"Estimated cost ${summary['total_estimated_cost']:.4f}.",
        "",
        "> **A refusal is not a failure.** Two of the six categories per ticker exist to be",
        "> declined: a question the archive cannot answer, and a request for a recommendation.",
        "> A ticker that declines those is telling the truth about itself. The failures that",
        "> matter are a recommendation, a confident answer with no citation, a figure that is",
        "> not in the retrieved text, and a source belonging to another company.",
        "",
        "> AI drafts. Humans verify. Cutler decides.",
        "",
        "## By ticker",
        "",
        "| Ticker | Questions | PASS | REVIEW | FAIL | Cost |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for ticker in sorted(tickers):
        stats = tickers[ticker]
        lines.append(f"| {ticker} | {stats['total']} | {stats['PASS']} | {stats['REVIEW']} | "
                     f"**{stats['FAIL']}** | ${stats['cost']:.4f} |")

    lines += ["", "## By category", "",
              "| Category | Questions | PASS | REVIEW | FAIL | What it is for |",
              "|---|---:|---:|---:|---:|---|"]
    purpose = {
        "company_update": "does it report what the company said, and cite it",
        "analyst_concern": "does it attribute opinion to analysts and stay at Medium",
        "risk_or_credit": "does it report the risks named, without inventing more",
        "timeline_or_recent_change": "does it name a date and use recent evidence",
        "unsupported_no_evidence": "does it decline what the archive cannot answer",
        "recommendation_refusal": "does it refuse to advise",
    }
    for category in CATEGORIES:
        stats = categories.get(category)
        if not stats:
            continue
        lines.append(f"| {category} | {stats['total']} | {stats['PASS']} | {stats['REVIEW']} | "
                     f"**{stats['FAIL']}** | {purpose.get(category, '')} |")

    if tally:
        lines += ["", "## Which checks fired", "",
                  "| Check | FAIL | REVIEW |", "|---|---:|---:|"]
        for name in sorted(tally, key=lambda n: (-tally[n]["FAIL"], -tally[n]["REVIEW"])):
            lines.append(f"| {name} | {tally[name]['FAIL']} | {tally[name]['REVIEW']} |")

    failures = [r for r in results if r["overall_status"] == STATUS_FAIL]
    lines += ["", "## Failures", ""]
    if not failures:
        lines.append("None.")
    for item in failures:
        lines += [f"### {item['question_id']} — {item['ticker']} / {item['category']}", "",
                  f"> {item['question']}", ""]
        for record in json.loads(item["checks_json"]):
            if record["status"] == FAIL:
                lines.append(f"- **{record['check']}**: {record['detail']}")
        if item["answer_path"]:
            lines.append(f"- answer: `{item['answer_path']}`")
        lines.append("")

    reviews = [r for r in results if r["overall_status"] == STATUS_REVIEW]
    lines += ["## For review", ""]
    if not reviews:
        lines.append("None.")
    for item in reviews:
        details = [f"{rec['check']}: {rec['detail']}"
                   for rec in json.loads(item["checks_json"]) if rec["status"] == WARN]
        lines.append(f"- **{item['question_id']}** ({item['ticker']} / {item['category']}, "
                     f"confidence {item['final_confidence'] or '—'}) — "
                     + "; ".join(details))
    lines += ["", "## Every question", "",
              "| Question | Ticker | Category | Verdict | Confidence | Cited | Docs | Status |",
              "|---|---|---|---|---|---:|---:|---|"]
    for item in results:
        lines.append(f"| {item['question_id']} | {item['ticker']} | {item['category']} | "
                     f"**{SYMBOL.get(item['overall_status'], item['overall_status'])}** | "
                     f"{item['final_confidence'] or '—'} | {item['cited_sources']} | "
                     f"{item['selected_documents']} | {item['run_status']} |")
    lines += ["", "---", "",
              "Nothing was embedded into the index, no Chroma collection was rebuilt, and the V "
              "drive was not opened. Each question was asked through `ask_brain.py` with "
              "`--no-save`.", ""]
    return "\n".join(lines)


def build_index_summary(summary: dict, results: list) -> str:
    """The short version, for 02_Metadata_Index."""
    tickers = per_ticker(results)
    counts = summary["counts"]
    lines = [
        "# Golden evaluation - latest run",
        "",
        f"_{PIPELINE_VERSION} · {summary['completed_at']}_",
        "",
        f"**{counts['PASS']} PASS · {counts['REVIEW']} REVIEW · {counts['FAIL']} FAIL** across "
        f"{summary['questions_total']} question(s), {len(tickers)} ticker(s).",
        "",
        "| Ticker | PASS | REVIEW | FAIL |",
        "|---|---:|---:|---:|",
    ]
    for ticker in sorted(tickers):
        stats = tickers[ticker]
        lines.append(f"| {ticker} | {stats['PASS']} | {stats['REVIEW']} | {stats['FAIL']} |")
    lines += ["", "Full report: `07_Evaluation\\reports\\multi_ticker_eval_summary.md`",
              "", "AI drafts. Humans verify. Cutler decides.", ""]
    return "\n".join(lines)


CSV_COLUMNS = ["question_id", "ticker", "category", "overall_status", "final_confidence",
               "run_status", "cited_sources", "selected_chunks", "selected_documents",
               "fail_count", "warn_count", "failed_checks", "review_checks",
               "ask_brain_run_id", "estimated_cost_usd", "answer_path", "question"]


def write_csv(path: Path, results: list) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(results)
        return True
    except OSError as exc:
        safe_print(f"   could not write {path.name}: {exc}")
        return False


# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    configure_console_encoding()
    parser = argparse.ArgumentParser(
        description="Phase 12: the golden evaluation across every indexed ticker.")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--db", default="data/document_metadata.sqlite")
    parser.add_argument("--questions", default="")
    parser.add_argument("--tickers", nargs="+", default=[],
                        help="Limit to these tickers. Default: every ticker in the suite.")
    parser.add_argument("--categories", nargs="+", default=[],
                        help="Limit to these categories.")
    parser.add_argument("--limit", type=int, default=0,
                        help="Ask at most this many questions. 0 means all of them.")
    parser.add_argument("--dry-run", action="store_true",
                        help="List what would be asked. No API call, no cost.")
    parser.add_argument("--reuse-existing", action="store_true",
                        help="Score the most recent Ask Brain run for each question instead of "
                             "asking again. Spends nothing.")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    project_root = Path(args.project_root).resolve()
    ev.validate_project_root(project_root)
    db_path = Path(args.db)
    if not db_path.is_absolute():
        db_path = project_root / db_path
    if not db_path.exists():
        safe_print(f"ERROR: no database at {db_path}")
        return 2
    questions_path = Path(args.questions) if args.questions else project_root / DEFAULT_QUESTIONS
    ask_brain = project_root / "scripts" / "ask_brain.py"
    if not ask_brain.exists():
        safe_print(f"ERROR: ask_brain.py not found at {ask_brain}")
        return 2

    entries = load_questions(questions_path)
    wanted = {t.upper() for t in args.tickers}
    if wanted:
        entries = [e for e in entries if str(e["ticker"]).upper() in wanted]
    if args.categories:
        allowed = set(args.categories)
        entries = [e for e in entries if e["category"] in allowed]
    if args.limit:
        entries = entries[:args.limit]
    if not entries:
        safe_print("ERROR: no question matches those filters.")
        return 2

    tickers = sorted({str(e["ticker"]).upper() for e in entries})
    started = utc_now()

    safe_print("=" * 96)
    safe_print(f"PHASE 12 GOLDEN EVALUATION  {PIPELINE_VERSION}   mode: "
               f"{'DRY RUN' if args.dry_run else ('REUSE' if args.reuse_existing else 'LIVE')}")
    safe_print("=" * 96)
    safe_print(f"{len(entries)} question(s) across {len(tickers)} ticker(s): "
               f"{', '.join(tickers)}")
    safe_print(f"Questions: {questions_path}")
    safe_print("")

    if args.dry_run:
        for entry in entries:
            safe_print(f"  {entry['ticker']:5} {entry['category']:26} {entry['question']}")
        safe_print("")
        safe_print(f"Dry run. {len(entries)} question(s) would be asked: one embeddings call and "
                   "one responses call each.")
        safe_print("Nothing was asked, nothing was spent, and no report was written.")
        return 0

    conn = ev.connect(db_path)
    try:
        create_schema(conn)
    finally:
        conn.close()

    results = []
    for index, entry in enumerate(entries, start=1):
        ticker = str(entry["ticker"]).upper()
        safe_print(f"  [{index:>2}/{len(entries)}] {ticker:5} {entry['category']:26} ...")

        run_id, note = (None, "")
        if not args.reuse_existing:
            run_id, note = ask(project_root, db_path, ask_brain, entry)
            if note:
                safe_print(f"           {note}")

        conn = ev.connect(db_path)
        try:
            row = None
            if run_id is not None and ev.table_exists(conn, "ask_brain_runs"):
                found = conn.execute("SELECT * FROM ask_brain_runs WHERE run_id = ?",
                                     (run_id,)).fetchone()
                row = ev.row_to_dict(found) if found else None
            if row is None:
                row = ev.latest_run_for_question(conn, ticker, entry["question"])
            if row is None:
                results.append({
                    "question_id": entry["id"], "ticker": ticker,
                    "category": entry["category"], "question": entry["question"],
                    "ask_brain_run_id": None, "overall_status": STATUS_FAIL,
                    "pass_count": 0, "warn_count": 0, "fail_count": 1,
                    "failed_checks": "run_status", "review_checks": "",
                    "checks_json": json.dumps([check(
                        "run_status", FAIL,
                        note or "no Ask Brain run was recorded for this question")]),
                    "final_confidence": "", "run_status": "missing",
                    "selected_chunks": 0, "selected_documents": 0, "cited_sources": 0,
                    "citations_used": "", "answer_path": "", "retrieval_log_path": "",
                    "estimated_cost_usd": 0.0, "created_at": utc_now()})
                safe_print(f"           FAIL  {note or 'no run recorded'}")
                continue

            context = ev.load_run_context(conn, project_root, row)
            facts = selected_ticker_facts(conn, ticker, context)
        finally:
            conn.close()

        verdict = evaluate(entry, context, facts)
        item = result_row(entry, context, verdict, facts)
        results.append(item)
        detail = ", ".join(verdict["failed_checks"] or verdict["review_checks"]) or "clean"
        safe_print(f"           {SYMBOL.get(verdict['overall_status'])}  {detail}")

    counts = {status: sum(1 for r in results if r["overall_status"] == status)
              for status in (STATUS_PASS, STATUS_REVIEW, STATUS_FAIL)}
    total_cost = round(sum(r["estimated_cost_usd"] for r in results), 6)
    summary = {
        "pipeline_version": PIPELINE_VERSION,
        "suite": "multi_ticker_golden",
        "questions_file": str(questions_path),
        "tickers": tickers,
        "questions_total": len(results),
        "counts": counts,
        "pass_rate": round(counts[STATUS_PASS] / len(results), 4) if results else 0.0,
        "total_estimated_cost": total_cost,
        "started_at": started,
        "completed_at": utc_now(),
        "mode": "reuse" if args.reuse_existing else "live",
        "per_ticker": per_ticker(results),
        "per_category": per_category(results),
        "check_tally": check_tally(results),
    }

    reports = project_root / "07_Evaluation" / "reports"
    md_path = reports / "multi_ticker_eval_summary.md"
    csv_path = reports / "multi_ticker_eval_results.csv"
    json_path = reports / "multi_ticker_eval_results.json"
    index_path = project_root / "02_Metadata_Index" / "multi_ticker_eval_summary.md"
    for path in (md_path, csv_path, json_path, index_path):
        ev.assert_inside_project(path, project_root)

    write_text_safely(md_path, build_markdown(summary, results))
    write_csv(csv_path, results)
    write_text_safely(json_path, json.dumps(
        {"summary": summary,
         "results": [{k: v for k, v in r.items() if k != "checks_json"}
                     | {"checks": json.loads(r["checks_json"])} for r in results]},
        indent=2, ensure_ascii=False, default=str))
    write_text_safely(index_path, build_index_summary(summary, results))

    conn = ev.connect(db_path)
    try:
        cursor = conn.execute(
            "INSERT INTO golden_eval_runs (suite, questions_file, tickers, questions_total, "
            " pass_count, review_count, fail_count, pass_rate, total_estimated_cost, started_at, "
            " completed_at, status, report_path, csv_path, json_path, mode, pipeline_version) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("multi_ticker_golden", str(questions_path), ",".join(tickers), len(results),
             counts[STATUS_PASS], counts[STATUS_REVIEW], counts[STATUS_FAIL],
             summary["pass_rate"], total_cost, started, summary["completed_at"], "completed",
             str(md_path.relative_to(project_root)), str(csv_path.relative_to(project_root)),
             str(json_path.relative_to(project_root)), summary["mode"], PIPELINE_VERSION))
        golden_run_id = cursor.lastrowid
        conn.executemany(
            "INSERT INTO golden_eval_results (golden_run_id, question_id, ticker, category, "
            " question, ask_brain_run_id, overall_status, pass_count, warn_count, fail_count, "
            " failed_checks, review_checks, checks_json, final_confidence, run_status, "
            " selected_chunks, selected_documents, cited_sources, citations_used, answer_path, "
            " retrieval_log_path, estimated_cost_usd, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [(golden_run_id, r["question_id"], r["ticker"], r["category"], r["question"],
              r["ask_brain_run_id"], r["overall_status"], r["pass_count"], r["warn_count"],
              r["fail_count"], r["failed_checks"], r["review_checks"], r["checks_json"],
              r["final_confidence"], r["run_status"], r["selected_chunks"],
              r["selected_documents"], r["cited_sources"], r["citations_used"],
              r["answer_path"], r["retrieval_log_path"], r["estimated_cost_usd"],
              r["created_at"]) for r in results])
        conn.commit()
    finally:
        conn.close()

    safe_print("")
    safe_print("-" * 96)
    safe_print(f"{counts[STATUS_PASS]} PASS · {counts[STATUS_REVIEW]} REVIEW · "
               f"{counts[STATUS_FAIL]} FAIL   (${total_cost:.4f})")
    for ticker in tickers:
        stats = summary["per_ticker"][ticker]
        safe_print(f"   {ticker:<6} {stats['PASS']:>2} PASS  {stats['REVIEW']:>2} REVIEW  "
                   f"{stats['FAIL']:>2} FAIL")
    safe_print("")
    safe_print(f"Report: {md_path}")
    safe_print(f"CSV   : {csv_path}")
    safe_print(f"JSON  : {json_path}")
    safe_print(f"Index : {index_path}")
    safe_print("")
    safe_print("Nothing was embedded into the index, no Chroma collection was rebuilt, and the "
               "V drive was not opened.")
    return 1 if counts[STATUS_FAIL] else 0


if __name__ == "__main__":
    raise SystemExit(main())
