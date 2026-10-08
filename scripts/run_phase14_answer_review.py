#!/usr/bin/env python3
# =============================================================================
# Cutler Research Brain - Phase 14
# run_phase14_answer_review.py - the before/after, on real answers
#
# Phase 14 changed how answers are WRITTEN. The only honest evidence that it
# worked is the same questions, asked again against the same index, with both
# answers side by side and the differences counted.
#
# BEFORE comes from answers already on disk - the Phase 12 golden evaluation
# wrote 60 of them, and this reads the ones for the review set rather than
# re-running the old prompt. Those files are the record of what the system used
# to say; regenerating them would cost money to reproduce something already
# written down.
#
# AFTER is a fresh run of each question through ask_brain.py, exactly as the UI
# would ask it. One embedding call and one answer call per question, the same
# as any other question.
#
# WHAT THIS DOES NOT DO
#   - it does not rebuild an index, re-embed anything, or touch Chroma
#   - it does not change a retrieval weight; both sides ran the same retrieval
#   - it does not use a model to grade a model. Every number in the report is
#     a count over text, from scripts/answer_quality.py
#   - it does not write to the V drive, and never opens it
#
# The source set is reported for both sides precisely because retrieval is NOT
# what this phase changed: if the documents move, that is nondeterminism in
# retrieval, and the report says so rather than taking credit for it.
#
#   AI drafts. Humans verify. Cutler decides.
#
# Run (from the project root):
#   py -3.11 scripts\run_phase14_answer_review.py --dry-run
#   py -3.11 scripts\run_phase14_answer_review.py --confirm-cost
# =============================================================================

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import answer_quality as aq                                        # noqa: E402
import ask_brain as ab                                             # noqa: E402

PHASE = "phase14.3.3-1.0"

# The review set the Phase 14 brief asks for: five tickers, and every category
# that exercises a different behaviour - a company update, an analyst-opinion
# question, a risk synthesis, a recent change, an unsupported question and a
# recommendation request. The contextual follow-up is checked separately, by
# tests/test_phase14.py, because it needs a previous run to follow.
REVIEW_TICKERS = ("MSFT", "KIM", "B", "T", "AMZN")
REVIEW_CATEGORIES = ("company_update", "analyst_concern", "risk_or_credit",
                     "timeline_or_recent_change", "unsupported_no_evidence",
                     "recommendation_refusal")

# The four that ask a real research question. A refusal is not judged against
# the quick band: it is correct for being short.
SUBSTANTIVE_CATEGORIES = ("company_update", "analyst_concern", "risk_or_credit",
                          "timeline_or_recent_change")

# Phase 14.3. A reply that the archive cannot answer something should be read in
# about fifteen seconds. Measured on the WHOLE answer, because the live run
# showed sections 2 to 6 carrying the weight, not section 1.
UNSUPPORTED_WORD_CAP = 80

RE_RUN_ID = re.compile(r"^Run id\s*:\s*(\d+)", re.M)

# Phase 14.3.3. What an answer file looks like: front matter, then the
# automated-checks block when there is one, then the six sections the model
# wrote, then a horizontal rule and the appendices (source table, retrieval log
# path). ANSWER_BODY_START finds where the answer proper begins; everything
# after the rule that follows section 6 is apparatus this report already
# summarises in its own tables.
ANSWER_BODY_MARKERS = ("## Automated checks flagged this answer", "## 1. Direct Answer")

# A guard, not a budget. Measured over the 60 answers of the 14.3.2 before/after
# run: the six sections run 1,100 to 8,040 characters, median about 2,500, and
# the longest are the pre-Phase-14 answers this work exists to shorten. The
# guard sits above all of them, so nothing in a real report is trimmed - it is
# there only so that a pathological answer cannot make the report unopenable,
# and when it does fire it cuts at a paragraph break and says how much it left
# out. A before/after report that silently trims one side understates it.
ANSWER_EXCERPT_GUARD = 12000
ASK_TIMEOUT_SECONDS = 600.0

# What a question costs, from the Phase 12 run: $0.4012 for 60 questions.
ESTIMATED_COST_PER_QUESTION = 0.0067


def safe_print(text: str = "") -> None:
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode("ascii", "replace").decode("ascii"))


def child_environment() -> dict:
    """UTF-8 for the child, so a non-breaking hyphen cannot break the capture."""
    environment = os.environ.copy()
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8"
    return environment


def project_file(project_root: Path, recorded: str) -> Path:
    """A path the pipeline recorded, resolved on whatever platform is reading.

    Ask Brain writes these on Windows, so they carry backslashes. Joining that
    string on a POSIX machine produces one filename containing backslashes,
    which exists nowhere - and the caller sees "no before answer" rather than "I
    could not read the path". Splitting on both separators costs nothing and
    makes the record readable from either side.
    """
    parts = [p for p in re.split(r"[\\/]+", str(recorded or "")) if p not in ("", ".")]
    return project_root.joinpath(*parts) if parts else project_root


def load_questions(project_root: Path) -> list:
    path = (project_root / "07_Evaluation" / "golden_questions"
            / "multi_ticker_eval_questions.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = [q for q in payload["questions"]
            if q.get("ticker") in REVIEW_TICKERS and q.get("category") in REVIEW_CATEGORIES]
    rows.sort(key=lambda q: (REVIEW_TICKERS.index(q["ticker"]),
                             REVIEW_CATEGORIES.index(q["category"])))
    return rows


def load_before(project_root: Path, source: str = "") -> dict:
    """The Phase 12 answers, keyed by question text.

    Keyed by the question rather than by id so a renumbered suite still lines
    up, and so a missing before-answer is visible as a gap rather than as a
    silent mismatch.
    """
    # Phase 14.1. Either side of the arc can be the baseline. With no --before,
    # it is the Phase 12 golden run - the system as it was before any of this.
    # Pointing --before at an earlier review's JSON compares against THAT run
    # instead, which is how a remediation shows its own delta rather than the
    # delta of the phase it is remediating.
    path = (project_file(project_root, source) if source
            else project_root / "07_Evaluation" / "reports" / "multi_ticker_eval_results.json")
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if "rows" in payload:
        return {row["question"]: {
            "text": row.get("after_text") or "",
            "path": "(from a previous review)",
            "run_id": row.get("after_run_id"),
            "confidence": row.get("after_confidence") or "",
            "citations": str(row.get("after_citations") or ""),
            "status": "", "retrieval_log_path": "",
            "sources": row.get("after_sources") or {},
        } for row in payload["rows"] if row.get("after_text")}
    out = {}
    for row in payload.get("results") or []:
        answer_path = project_file(project_root, row.get("answer_path") or "")
        if not str(row.get("answer_path") or "") or not answer_path.is_file():
            continue
        out[row["question"]] = {
            "text": answer_path.read_text(encoding="utf-8", errors="replace"),
            "path": str(row.get("answer_path")),
            "run_id": row.get("ask_brain_run_id"),
            "confidence": row.get("final_confidence") or "",
            "citations": str(row.get("citations_used") or ""),
            "status": row.get("overall_status") or "",
            "retrieval_log_path": row.get("retrieval_log_path") or "",
        }
    return out


def sources_of(project_root: Path, retrieval_log_path: str) -> dict:
    """The documents and publishers a run actually selected."""
    if not retrieval_log_path:
        return {"documents": [], "vendors": [], "chunks": []}
    path = project_file(project_root, retrieval_log_path)
    if not path.is_file():
        return {"documents": [], "vendors": [], "chunks": []}
    try:
        log = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"documents": [], "vendors": [], "chunks": []}
    selected = [c for c in (log.get("candidates") or []) if c.get("selected")]
    documents = sorted({str(c.get("document_id") or "") for c in selected if c.get("document_id")})
    chunks = sorted({str(c.get("chunk_id") or "") for c in selected if c.get("chunk_id")})
    files = sorted({str(c.get("file_name") or "") for c in selected if c.get("file_name")})
    return {"documents": documents, "chunks": chunks, "files": files,
            "vendors": vendors_for(project_root, chunks)}


def vendors_for(project_root: Path, chunk_ids: list) -> list:
    """The publishers recorded for these chunks, read from the database."""
    if not chunk_ids:
        return []
    import sqlite3  # noqa: PLC0415 - only needed on this path
    db = project_root / "data" / "document_metadata.sqlite"
    if not db.is_file():
        return []
    names = []
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        marks = ",".join("?" * len(chunk_ids))
        rows = conn.execute(
            f"SELECT DISTINCT source_vendor_candidates FROM rag_chunks "
            f"WHERE chunk_id IN ({marks})", list(chunk_ids)).fetchall()
        conn.close()
    except sqlite3.Error:
        return []
    for row in rows:
        for part in re.split(r"[;,|]", str(row[0] or "")):
            name = part.strip()
            if len(name) >= 2 and name.lower() not in {"none", "unknown", "n/a"}:
                if name not in names:
                    names.append(name)
    return names


def ask(project_root: Path, db_path: Path, entry: dict, style: str) -> dict:
    """One question, through ask_brain.py, exactly as the UI would ask it."""
    ask_brain = project_root / "scripts" / "ask_brain.py"
    command = [
        sys.executable, str(ask_brain),
        "--project-root", str(project_root),
        "--db", str(db_path),
        "--ticker", str(entry["ticker"]).upper(),
        "--question", entry["question"],
        "--retrieval-mode", entry.get("retrieval_mode") or "hybrid",
        "--source-priority", entry.get("source_priority") or "balanced",
        "--top-k", str(entry.get("top_k") or 30),
        "--max-chunks", str(entry.get("max_chunks") or 8),
        "--max-chunks-per-document", str(entry.get("max_chunks_per_document") or 2),
        "--answer-style", style,
        "--no-save",
    ]
    if entry.get("prefer_recent"):
        command.append("--prefer-recent")

    completed = subprocess.run(            # noqa: S603 - list form, shell=False
        command, cwd=str(project_root), capture_output=True, text=True,
        encoding="utf-8", errors="replace", env=child_environment(),
        timeout=ASK_TIMEOUT_SECONDS, shell=False)

    match = RE_RUN_ID.search(completed.stdout or "")
    return {"run_id": int(match.group(1)) if match else None,
            "returncode": completed.returncode,
            "stderr": (completed.stderr or "")[-600:]}


def run_row(project_root: Path, db_path: Path, run_id: int) -> dict:
    import sqlite3  # noqa: PLC0415
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM ask_brain_runs WHERE run_id = ?", (run_id,)).fetchone()
    conn.close()
    return dict(row) if row else {}


# Phase 14.3. What the review is allowed to call an improvement.
#
# Each entry is (verdict, note). IMPROVED is reserved for the six deterministic
# rules the Phase 14.3 brief lists; everything else is CHANGED, which is an
# honest word for "this is different and nobody has shown it is better".
#
# "Longer" is never an improvement here. Neither is "shorter" on its own - a
# shorter answer that dropped a citation or a source has lost evidence, so the
# length rule below requires the citation count and the evidence to survive.
IMPROVED = "IMPROVED"
REGRESSED = "REGRESSED"
CHANGED = "CHANGED"


def compare(before: dict, after: dict) -> list:
    """Observed changes between the two answers, each with a defensible verdict.

    Written from the measurements rather than asserted. A phase that reports
    only its wins is not a review, so regressions are first-class here and the
    heading this feeds does not use the word "better".
    """
    notes = []

    # --- length: an improvement ONLY if nothing was lost with the words ----
    delta = after["words_direct"] - before["words_direct"]
    kept_citations = len(after["citations_total"]) >= len(before["citations_total"])
    kept_claims = after["claims"] >= before["claims"] - 1
    if abs(delta) >= 15:
        if delta < 0 and kept_citations and kept_claims:
            notes.append((IMPROVED,
                          f"shorter by {abs(delta)} words with its citations and points intact "
                          f"({before['words_direct']} -> {after['words_direct']})"))
        elif delta < 0:
            notes.append((CHANGED,
                          f"shorter by {abs(delta)} words, but "
                          + ("citations fell from "
                             f"{len(before['citations_total'])} to "
                             f"{len(after['citations_total'])}" if not kept_citations
                             else f"points fell from {before['claims']} to {after['claims']}")
                          + " - shorter is not better if evidence went with it"))
        else:
            notes.append((CHANGED,
                          f"longer by {delta} words ({before['words_direct']} -> "
                          f"{after['words_direct']}); length alone is not an improvement"))

    # --- the quick band ----------------------------------------------------
    was_in = aq.in_band(before["words_direct"])
    now_in = aq.in_band(after["words_direct"])
    if now_in and not was_in:
        notes.append((IMPROVED, f"moved into the {aq.QUICK_WORD_BAND[0]}-"
                                f"{aq.QUICK_WORD_BAND[1]} word band"))
    elif was_in and not now_in:
        notes.append((REGRESSED, f"left the {aq.QUICK_WORD_BAND[0]}-"
                                 f"{aq.QUICK_WORD_BAND[1]} word band "
                                 f"({after['words_direct']} words)"))

    # --- grounding ---------------------------------------------------------
    if before["claims_uncited"] and not after["claims_uncited"]:
        notes.append((IMPROVED, f"{before['claims_uncited']} uncited claim(s) eliminated"))
    if after["claims_uncited"] > before["claims_uncited"]:
        notes.append((REGRESSED, f"{after['claims_uncited']} uncited claim(s), was "
                                 f"{before['claims_uncited']}"))

    # --- boilerplate -------------------------------------------------------
    if before["opens_with_boilerplate"] and not after["opens_with_boilerplate"]:
        notes.append((IMPROVED, "boilerplate opening removed - it now opens with the finding"))
    if after["opens_with_boilerplate"] and not before["opens_with_boilerplate"]:
        notes.append((REGRESSED, "now opens with boilerplate"))

    # --- repetition --------------------------------------------------------
    if before["repetition"] - after["repetition"] >= 0.15:
        notes.append((IMPROVED, f"repetition removed ({before['repetition']} -> "
                                f"{after['repetition']})"))
    if after["repetition"] - before["repetition"] >= 0.15:
        notes.append((REGRESSED, f"more repetition ({before['repetition']} -> "
                                 f"{after['repetition']})"))

    # --- synthesis: CITED synthesis is the improvement, not synthesis ------
    if after.get("synthesis_cited", 0) and not before.get("synthesis_cited", 0):
        notes.append((IMPROVED, "added a cited conclusion where there was none: "
                                f"\"{after['synthesis_lines'][0][:110]}\""))
    elif after["has_synthesis"] and not before["has_synthesis"]:
        verdict = REGRESSED if after.get("synthesis_uncited", 0) else CHANGED
        notes.append((verdict, "added a conclusion"
                      + (" that carries no citation" if after.get("synthesis_uncited", 0)
                         else "")))
    if before["has_synthesis"] and not after["has_synthesis"]:
        notes.append((CHANGED, "no longer carries a marked closing conclusion; check whether "
                               "the takeaway now carries it instead"))
    if before.get("synthesis_uncited", 0) and not after.get("synthesis_uncited", 0):
        notes.append((IMPROVED, "its conclusion now carries citations"))

    # --- differences that are differences ----------------------------------
    if after["has_what_changed"] and not before["has_what_changed"]:
        notes.append((CHANGED, "now says what changed, not only what is"))
    if after.get("vendors_named") and not before.get("vendors_named"):
        notes.append((CHANGED, "names the publisher: "
                               + ", ".join(after["vendors_named"][:3])))

    if not notes:
        notes.append((CHANGED, "no material difference measured"))
    return notes


def verdict_counts(rows: list) -> dict:
    counts = {IMPROVED: 0, REGRESSED: 0, CHANGED: 0}
    for row in rows:
        for verdict, _ in row["notes"]:
            counts[verdict] = counts.get(verdict, 0) + 1
    return counts


def answer_body(text: str) -> str:
    """The answer as the model wrote it: no front matter, no appendices.

    Phase 14.3.3. The report used to slice the whole file at 2,600 characters,
    which spent most of the budget on metadata it prints in its own table and
    then stopped inside the Direct Answer. Slicing by structure instead means
    the thing being reported on is never cut in the middle of a sentence.
    """
    body = text or ""
    for marker in ANSWER_BODY_MARKERS:
        index = body.find(marker)
        if index >= 0:
            body = body[index:]
            break
    tail = body.find("## 6. Confidence")
    if tail >= 0:
        rule = body.find("\n---\n", tail)
        if rule > 0:
            body = body[:rule]
    return body.strip()


def answer_excerpt(text: str, path_hint: str = "") -> str:
    """answer_body, trimmed at a paragraph break only if it is enormous."""
    body = answer_body(text)
    if len(body) <= ANSWER_EXCERPT_GUARD:
        return body
    cut = body.rfind("\n\n", 0, ANSWER_EXCERPT_GUARD)
    cut = cut if cut > 0 else ANSWER_EXCERPT_GUARD
    left = len(body) - cut
    where = f" Read the whole answer at {path_hint}." if path_hint else ""
    return (body[:cut].rstrip()
            + f"\n\n[... {left} more characters, trimmed at a paragraph break by the report, "
              f"not by the model.{where}]")


def completeness_of(text: str) -> dict:
    """The deterministic completeness guard, applied to an answer on disk.

    The same ask_brain function the pipeline runs, so the report cannot have a
    second opinion about what "finished" means. It reads answer_body() rather
    than the file, because the file's appendices would otherwise be read as
    part of section 6.
    """
    body = answer_body(text)
    if not body.strip():
        return {"incomplete": False, "reasons": [], "missing_sections": []}
    return ab.generation_completeness(body)


def report_markdown(rows: list, summary: dict, created_at: str) -> str:
    out = [
        "# Phase 14 - answer quality, before and after",
        "",
        f"- generated_by: `run_phase14_answer_review.py` ({PHASE})",
        f"- created_at: {created_at}",
        f"- questions reviewed: {len(rows)}",
        "",
        "Both sides ran the same retrieval against the same index. Nothing was re-embedded, no "
        "weight was changed, and no model graded anything here - every number below is a count "
        "over the answer text, from `scripts/answer_quality.py`.",
        "",
        "**How to read \"marked closing conclusion\".** It counts a closing line that opens with "
        "a synthesis marker. It is NOT the same as \"the answer reaches a conclusion\": the "
        "prompt asks for the conclusion in the TAKEAWAY, and an answer that puts it there and "
        "then adds a closing line as well is repeating itself. A low count here is a reason to "
        "read the takeaways, not evidence that the answers only transcribe.",
        "",
        "## The corpus, in numbers",
        "",
        "| measure | before | after | change |",
        "|---|---|---|---|",
    ]
    for label, key, direction in (
        ("Mean words, whole answer", "mean_words_total", "lower"),
        ("Mean words, Direct Answer", "mean_words_direct", "lower"),
        ("Mean words, sections 2-6", "mean_words_apparatus", "lower"),
        ("Mean claims in Direct Answer", "mean_claims", "-"),
        ("Mean repetition in Direct Answer", "mean_repetition", "lower"),
        ("Answers opening with boilerplate", "boilerplate_openings", "lower"),
        ("Answers with a marked closing conclusion", "with_synthesis", "higher"),
        # Phase 14.1. The number that matters is not whether a conclusion exists
        # but whether a reader can trace it back to documents. An uncited
        # conclusion is the defect the first live run surfaced.
        ("  of those, conclusions CITED", "synthesis_cited", "higher"),
        ("  of those, conclusions UNCITED", "synthesis_uncited", "lower"),
        ("Answers saying what changed", "with_what_changed", "higher"),
        ("Answers naming a publisher", "named_a_publisher", "higher"),
        ("Substantive answers in the 100-220 word band",
         "substantive_in_quick_band", "higher"),
        ("Uncited claims across the corpus", "uncited_claims_total", "lower"),
        # Phase 14.3: the two overruns the brief asks to be counted, not averaged.
        # A mean hides four long answers behind sixteen short ones.
        ("Direct Answers over 220 words", "direct_over_220", "lower"),
        ("Unsupported answers over 80 words (whole answer)",
         "unsupported_over_80", "lower"),
        ("Mean words, unsupported answers (whole answer)",
         "mean_words_unsupported", "lower"),
        # Phase 14.3.3. An answer that stopped mid-sentence, or that the API
        # reported stopping early. Counted, never averaged: one is one too many.
        ("Answers whose generation did not finish", "generation_incomplete", "lower"),
    ):
        old = summary["before"].get(key, 0)
        new = summary["after"].get(key, 0)
        delta = round(new - old, 2)
        arrow = "same" if delta == 0 else (f"+{delta}" if delta > 0 else str(delta))
        if direction != "-" and delta:
            moved_right_way = (delta < 0) if direction == "lower" else (delta > 0)
            arrow += "  \u2713" if moved_right_way else "  \u2717"
        out.append(f"| {label} | {old} | {new} | {arrow} |")

    counts = summary.get("verdicts") or {}
    out += [
        "",
        "A tick marks a measure that moved the way the phase intended. It is a direction, not a "
        "proof: the only claims of improvement in this report are the ones labelled IMPROVED "
        "below, each of which has a deterministic rule behind it.",
        "",
        f"Verdicts across all questions: **{counts.get('IMPROVED', 0)} IMPROVED**, "
        f"{counts.get('CHANGED', 0)} CHANGED, **{counts.get('REGRESSED', 0)} REGRESSED**.",
        "",
        "## Question by question", ""]
    for row in rows:
        out += [
            f"### {row['ticker']} - {row['category']}",
            "",
            f"**{row['question']}**",
            "",
            "| | before | after |",
            "|---|---|---|",
            f"| words (Direct Answer) | {row['before']['words_direct']} | "
            f"{row['after']['words_direct']} |",
            f"| words (whole answer) | {row['before']['words_total']} | "
            f"{row['after']['words_total']} |",
            f"| citations used | {row['before_citations'] or '-'} | "
            f"{row['after_citations'] or '-'} |",
            f"| confidence | {row['before_confidence'] or '-'} | "
            f"{row['after_confidence'] or '-'} |",
            f"| documents selected | {len(row['before_sources']['documents'])} | "
            f"{len(row['after_sources']['documents'])} |",
            f"| source set identical | {'yes' if row['same_sources'] else 'NO'} | |",
            "",
            "Observed changes:",
            "",
        ]
        out += [f"- **{verdict}** - {note}" for verdict, note in row["notes"]]
        if row.get("after_generation_incomplete"):
            out += ["", "> **The generation did not finish.** "
                    + "; ".join(row.get("after_generation_reasons") or [])
                    + " Nothing was rewritten, and the confidence was capped at Low.", ""]
        out += ["", "<details><summary>before</summary>", "",
                "```", answer_excerpt(row["before_text"]), "```", "", "</details>", "",
                "<details><summary>after</summary>", "",
                "```", answer_excerpt(row["after_text"], row.get("after_answer_path", "")),
                "```", "", "</details>", ""]
    return "\n".join(out)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Phase 14: the same questions, before and after the new prompt.")
    parser.add_argument("--project-root", default=".", help="Project root.")
    parser.add_argument("--db", default="data/document_metadata.sqlite")
    parser.add_argument("--answer-style", default="quick", choices=("quick", "deep"))
    parser.add_argument("--before", default="",
                        help="What to compare against. Default: the Phase 12 golden run. Pass a "
                             "previous phase14_answer_review_*.json to measure one run against "
                             "another instead.")
    parser.add_argument("--limit", type=int, default=0, help="Only the first N questions.")
    parser.add_argument("--only", default="",
                        help="Comma-separated TICKER:category selectors, so one or two "
                             "questions can be re-asked without paying for thirty. "
                             "'MSFT:risk_or_credit,AMZN:analyst_concern'. A bare ticker or a "
                             "bare category selects all of that ticker or category. The report "
                             "it writes covers only the questions asked.")
    parser.add_argument("--dry-run", action="store_true",
                        help="List what would be asked and what it would cost. No model call.")
    parser.add_argument("--confirm-cost", action="store_true",
                        help="Required to actually ask. Without it, this refuses.")
    args = parser.parse_args(argv)

    project_root = Path(args.project_root).expanduser().resolve()
    db_path = (project_root / args.db) if not Path(args.db).is_absolute() else Path(args.db)

    questions = load_questions(project_root)
    if args.only:
        wanted = [s.strip().lower() for s in args.only.split(",") if s.strip()]

        def selected_question(entry: dict) -> bool:
            ticker = str(entry["ticker"]).lower()
            category = str(entry["category"]).lower()
            return any(sel in (ticker, category, f"{ticker}:{category}") for sel in wanted)

        questions = [q for q in questions if selected_question(q)]
        if not questions:
            safe_print(f"--only {args.only!r} matched no question. Nothing was asked.")
            return 2
    if args.limit:
        questions = questions[:args.limit]
    before = load_before(project_root, args.before)

    missing = [q["question"] for q in questions if q["question"] not in before]
    estimated = len(questions) * ESTIMATED_COST_PER_QUESTION

    safe_print("=" * 74)
    safe_print(f"Phase 14 answer review - {len(questions)} question(s), style "
               f"'{args.answer_style}'")
    safe_print("=" * 74)
    safe_print(f"  before answers found : {len(questions) - len(missing)}/{len(questions)}")
    if missing:
        safe_print(f"  WITHOUT a before answer ({len(missing)}): these will be reported "
                   "after-only")
        for text in missing[:5]:
            safe_print(f"    - {text[:88]}")
    safe_print(f"  estimated cost       : ${estimated:.4f}")
    safe_print("")

    if args.dry_run:
        for entry in questions:
            safe_print(f"  {entry['ticker']:5s} {entry['category']:26s} "
                       f"{entry['question'][:70]}")
        safe_print("\nDry run. Nothing was asked and nothing was spent.")
        return 0
    if not args.confirm_cost:
        safe_print("Refusing to run without --confirm-cost. Nothing was spent.")
        return 3

    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows = []
    for index, entry in enumerate(questions, start=1):
        safe_print(f"[{index}/{len(questions)}] {entry['ticker']} {entry['category']}")
        result = ask(project_root, db_path, entry, args.answer_style)
        if result["run_id"] is None:
            safe_print(f"    no run recorded (exit {result['returncode']}): "
                       f"{result['stderr'][:160]}")
            continue
        row = run_row(project_root, db_path, result["run_id"])
        after_path = project_file(project_root, row.get("answer_path") or "")
        after_text = (after_path.read_text(encoding="utf-8", errors="replace")
                      if after_path.is_file() else "")
        after_sources = sources_of(project_root, row.get("retrieval_log_path") or "")

        old = before.get(entry["question"])
        if old and old.get("sources"):
            before_sources = old["sources"]
        elif old:
            before_sources = sources_of(project_root, old["retrieval_log_path"])
        else:
            before_sources = {"documents": [], "vendors": [], "chunks": [], "files": []}
        before_measure = (aq.measure(old["text"], before_sources.get("vendors")) if old
                          else None)
        after_measure = aq.measure(after_text, after_sources.get("vendors"))

        # Phase 14.3.3. Two signals, and they are not the same thing: the
        # deterministic guard reads the text, the run row carries what the API
        # itself said. An answer can be cut without the API admitting it, and
        # the API can report 'incomplete' on text that happens to end cleanly.
        after_complete = completeness_of(after_text)
        before_complete = completeness_of(old["text"] if old else "")
        api_status = str(row.get("generation_status") or "")
        api_incomplete = bool(str(row.get("generation_incomplete") or "0") == "1"
                              or (api_status and api_status != "completed"))

        rows.append({
            "ticker": entry["ticker"], "category": entry["category"],
            "question": entry["question"],
            "after_generation_incomplete": bool(after_complete["incomplete"] or api_incomplete),
            "after_generation_reasons": (after_complete["reasons"]
                                         + ([f"the answer model reported status "
                                             f"'{api_status}'"] if api_status
                                            and api_status != "completed" else [])),
            "after_generation_status": api_status,
            "after_max_output_tokens": row.get("answer_max_output_tokens") or "",
            "after_retry_used": str(row.get("answer_retry_used") or "0") == "1",
            "after_answer_path": str(row.get("answer_path") or ""),
            "before_generation_incomplete": bool(before_complete["incomplete"]),
            "before": before_measure or {k: 0 for k in after_measure},
            "after": after_measure,
            "before_text": old["text"] if old else "(no before answer on disk)",
            "after_text": after_text,
            "before_citations": old["citations"] if old else "",
            "after_citations": str(row.get("citations_used") or ""),
            "before_confidence": old["confidence"] if old else "",
            "after_confidence": str(row.get("final_confidence") or ""),
            "before_sources": before_sources, "after_sources": after_sources,
            "same_sources": before_sources["documents"] == after_sources["documents"],
            "after_run_id": result["run_id"],
            "notes": (compare(before_measure, after_measure) if before_measure
                      else ["no before answer on disk, so no comparison was made"]),
        })
        if rows[-1]["after_generation_incomplete"]:
            safe_print("    GENERATION DID NOT FINISH: "
                       + "; ".join(rows[-1]["after_generation_reasons"])[:200])
        safe_print(f"    run {result['run_id']}: "
                   f"{before_measure['words_direct'] if before_measure else '?'} -> "
                   f"{after_measure['words_direct']} words, "
                   f"synthesis {'yes' if after_measure['has_synthesis'] else 'no'}, "
                   f"sources {'same' if rows[-1]['same_sources'] else 'MOVED'}")

    def overruns(side: str) -> dict:
        """The counts a mean would hide. Category-aware, so an unsupported answer
        is judged as an unsupported answer rather than against the quick band."""
        usable = [r for r in rows if r[side].get("words_total")]
        unsupported = [r for r in usable if r["category"] == "unsupported_no_evidence"]
        substantive = [r for r in usable if r["category"] in SUBSTANTIVE_CATEGORIES]
        return {
            "generation_incomplete": sum(1 for r in rows
                                         if r.get(f"{side}_generation_incomplete")),
            "direct_over_220": sum(1 for r in substantive
                                   if r[side]["words_direct"] > aq.QUICK_WORD_BAND[1]),
            "unsupported_over_80": sum(1 for r in unsupported
                                       if r[side]["words_total"] > UNSUPPORTED_WORD_CAP),
            "mean_words_unsupported": (round(sum(r[side]["words_total"] for r in unsupported)
                                             / len(unsupported), 2) if unsupported else 0),
        }

    summary = {
        "before": {**aq.summarise([r["before"] for r in rows if r["before"].get("words_total")]),
                   **overruns("before")},
        "after": {**aq.summarise([r["after"] for r in rows]), **overruns("after")},
        "source_sets_identical": sum(1 for r in rows if r["same_sources"]),
        "questions": len(rows),
        "verdicts": verdict_counts(rows),
    }

    reports = project_root / "07_Evaluation" / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    stamp = created_at.replace(":", "").replace("-", "")[:15]
    md_path = reports / f"phase14_answer_review_{stamp}.md"
    json_path = reports / f"phase14_answer_review_{stamp}.json"
    md_path.write_text(report_markdown(rows, summary, created_at), encoding="utf-8")
    json_path.write_text(json.dumps({"summary": summary, "rows": rows}, indent=1,
                                    default=str), encoding="utf-8")

    safe_print("")
    safe_print("=" * 74)
    for key in ("mean_words_total", "mean_words_direct", "mean_repetition",
                "with_synthesis", "synthesis_cited", "synthesis_uncited",
                "boilerplate_openings", "substantive_in_quick_band",
                "uncited_claims_total", "direct_over_220", "unsupported_over_80",
                "mean_words_unsupported", "generation_incomplete"):
        safe_print(f"  {key:34s} {summary['before'].get(key, '-'):>9} -> "
                   f"{summary['after'].get(key, '-'):>9}")
    safe_print(f"  {'source sets identical':34s} "
               f"{summary['source_sets_identical']}/{summary['questions']}")
    safe_print("")
    safe_print(f"  {md_path}")
    safe_print(f"  {json_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
