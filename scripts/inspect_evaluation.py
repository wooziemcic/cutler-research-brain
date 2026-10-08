#!/usr/bin/env python3
# =============================================================================
# Cutler Research Brain - Phase 8
# inspect_evaluation.py - console report over the evaluation history
#
# Read-only. It opens the database, prints what the evaluations found, and
# writes nothing. No OpenAI call, no Chroma query, no PDF opened, no V Drive
# access, no answer produced.
#
# The questions it is built to answer:
#   - is the system behaving, and is it getting better or worse?
#   - which questions keep failing, and do they fail for retrieval reasons or
#     for prompt/audit reasons? Those need different fixes.
#   - is this ready to point at a second ticker?
#
# Usage:
#   py -3.11 scripts\inspect_evaluation.py ^
#       --project-root "C:\Users\AI Admin\Documents\CutlerResearchBrain" ^
#       --db "data\document_metadata.sqlite" --ticker MSFT
# =============================================================================

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import textwrap
from collections import Counter, defaultdict
from pathlib import Path


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

WIDTH = 78
PIPELINE_VERSION = "phase8-1.0"

# Which checks point at retrieval, and which point at the prompt or the audit.
# A failure means different work depending on the answer.
RETRIEVAL_CHECKS = {"retrieval_check", "source_type_check", "document_type_check"}
ANSWER_CHECKS = {"citation_check", "confidence_check", "safety_check",
                 "hallucination_guard_check", "run_status"}


def rule(title: str = "") -> None:
    if title:
        print()
        print("=" * WIDTH)
        print(title)
        print("=" * WIDTH)
    else:
        print("-" * WIDTH)


def wrap(text: str, indent: str = "    ") -> None:
    for line in textwrap.wrap(str(text), width=WIDTH - len(indent),
                              subsequent_indent=indent) or [""]:
        print(f"{indent}{line}" if not line.startswith(indent) else line)


def short(text: str, limit: int) -> str:
    text = str(text or "").strip().replace("\n", " ")
    return text if len(text) <= limit else text[:limit - 3] + "..."


def connect(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise SystemExit(f"ERROR: database not found: {db_path}")
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                        (name,)).fetchone() is not None


def value(row, key, default=""):
    try:
        got = row[key]
    except (IndexError, KeyError):
        return default
    return default if got is None else got


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Cutler Research Brain Phase 8 - console report over evaluation runs. "
                    "Read-only.")
    parser.add_argument("--project-root", required=True,
                        help=r'e.g. "C:\\Users\\AI Admin\\Documents\\CutlerResearchBrain"')
    parser.add_argument("--db", default="data/document_metadata.sqlite",
                        help="SQLite path; relative paths resolve against --project-root.")
    parser.add_argument("--ticker", default="MSFT", help="Ticker to report on (default MSFT).")
    parser.add_argument("--latest", action="store_true",
                        help="Only the most recent evaluation run.")
    parser.add_argument("--top", type=int, default=10, help="Rows in the detail lists.")
    args = parser.parse_args(argv)

    project_root = Path(args.project_root).expanduser().resolve()
    db_path = Path(args.db).expanduser()
    if not db_path.is_absolute():
        db_path = (project_root / db_path).resolve()
    ticker = args.ticker.strip().upper()
    top = max(1, args.top)

    conn = connect(db_path)

    rule(f"ASK BRAIN EVALUATION - {ticker}")
    print(f"  database     : {db_path}")
    print(f"  project root : {project_root}")

    if not table_exists(conn, "evaluation_runs"):
        print()
        print("  No evaluation_runs table. The evaluation has never been run.")
        print("  Start with a dry run:")
        print(f"    py -3.11 scripts\\run_evaluation.py --project-root \"{project_root}\" "
              f"--ticker {ticker} --dry-run")
        conn.close()
        return 0

    runs = conn.execute(
        "SELECT * FROM evaluation_runs WHERE ticker = ? ORDER BY eval_run_id DESC",
        (ticker,)).fetchall()
    if not runs:
        print()
        print(f"  No evaluation runs recorded for {ticker} yet.")
        conn.close()
        return 0

    # ---- totals -----------------------------------------------------------
    rule("EVALUATION HISTORY")
    print(f"  total evaluation runs : {len(runs)}")
    costs = [float(value(r, "total_estimated_cost", 0) or 0) for r in runs]
    print(f"  total estimated spend : ${sum(costs):.4f}")
    print(f"  average per run       : ${(sum(costs) / len(costs)):.4f}")
    print("    (a local estimate from the rates in .env - it is not a bill)")
    print()
    print(f"  {'run':>4}  {'when':<20} {'mode':<9} {'pass':>5} {'review':>7} {'fail':>5} "
          f"{'rate':>6}")
    for row in runs[:top]:
        print(f"  {row['eval_run_id']:>4}  {short(value(row, 'started_at'), 19):<20} "
              f"{short(value(row, 'mode', 'live'), 9):<9} "
              f"{value(row, 'pass_count', 0):>5} {value(row, 'review_count', 0):>7} "
              f"{value(row, 'fail_count', 0):>5} "
              f"{float(value(row, 'pass_rate', 0) or 0):>5.0%}")

    if len(runs) > 1:
        newest, previous = runs[0], runs[1]
        delta = (float(value(newest, "pass_rate", 0) or 0)
                 - float(value(previous, "pass_rate", 0) or 0))
        direction = "up" if delta > 0 else ("down" if delta < 0 else "unchanged")
        print()
        print(f"  Pass rate vs the previous run: {direction} "
              f"({delta:+.0%})")
        if delta < 0:
            print("  Something got worse. Compare the two reports before changing anything else.")

    latest = runs[0]
    rule(f"LATEST RUN ({latest['eval_run_id']})")
    print(f"  started    : {value(latest, 'started_at')}")
    print(f"  completed  : {value(latest, 'completed_at')}")
    print(f"  mode       : {value(latest, 'mode', 'live')}")
    print(f"  questions  : {value(latest, 'questions_total', 0)}")
    print(f"  PASS       : {value(latest, 'pass_count', 0)}")
    print(f"  REVIEW     : {value(latest, 'review_count', 0)}")
    print(f"  FAIL       : {value(latest, 'fail_count', 0)}")
    print(f"  pass rate  : {float(value(latest, 'pass_rate', 0) or 0):.0%}")
    print(f"  cost       : ${float(value(latest, 'total_estimated_cost', 0) or 0):.4f}")
    report = value(latest, "report_path")
    if report:
        exists = (project_root / report).is_file()
        print(f"  report     : {report}{'' if exists else '   [MISSING ON DISK]'}")

    scope = [latest] if args.latest else runs
    scope_ids = [r["eval_run_id"] for r in scope]
    marks = ",".join("?" * len(scope_ids))
    results = conn.execute(
        f"SELECT * FROM evaluation_results WHERE eval_run_id IN ({marks}) "
        "ORDER BY eval_run_id DESC", scope_ids).fetchall()

    latest_results = [r for r in results if r["eval_run_id"] == latest["eval_run_id"]]

    # ---- the latest run, question by question -----------------------------
    rule("LATEST RUN, QUESTION BY QUESTION")
    for row in latest_results:
        status = value(row, "overall_status")
        print(f"  {status:<6} {value(row, 'question_id'):<34} "
              f"{value(row, 'pass_count', 0)}P/{value(row, 'warn_count', 0)}W/"
              f"{value(row, 'fail_count', 0)}F  "
              f"{value(row, 'final_confidence', '-') or '-':<7} "
              f"run {value(row, 'ask_brain_run_id', '-')}")
        if status in ("FAIL", "REVIEW"):
            try:
                checks = json.loads(value(row, "checks_json", "[]"))
            except json.JSONDecodeError:
                checks = []
            for item in checks:
                if item.get("status") in ("fail", "warn"):
                    wrap(f"{item['status'].upper()} {item['check']}: {item['detail']}",
                         "         ")

    # ---- weakest questions across history ---------------------------------
    rule("WEAKEST QUESTIONS")
    scores = defaultdict(lambda: {"runs": 0, "fail": 0, "review": 0})
    for row in results:
        entry = scores[value(row, "question_id")]
        entry["runs"] += 1
        if value(row, "overall_status") == "FAIL":
            entry["fail"] += 1
        elif value(row, "overall_status") == "REVIEW":
            entry["review"] += 1
    ranked = sorted(scores.items(),
                    key=lambda kv: (-kv[1]["fail"], -kv[1]["review"], kv[0]))
    weak = [(qid, s) for qid, s in ranked if s["fail"] or s["review"]]
    if weak:
        print("  Across every recorded evaluation:")
        for question_id, score in weak[:top]:
            print(f"    {question_id:<34} {score['fail']} FAIL / {score['review']} REVIEW "
                  f"of {score['runs']} run(s)")
    else:
        print("  None. Every question has passed every time it has been evaluated.")

    # ---- recurring failure types ------------------------------------------
    rule("RECURRING FAILURE TYPES")
    failures = Counter()
    warnings = Counter()
    for row in results:
        try:
            checks = json.loads(value(row, "checks_json", "[]"))
        except json.JSONDecodeError:
            continue
        for item in checks:
            if item.get("status") == "fail":
                failures[item.get("check", "?")] += 1
            elif item.get("status") == "warn":
                warnings[item.get("check", "?")] += 1
    if failures:
        print("  failures:")
        for name, count in failures.most_common():
            print(f"    {count:>3}  {name}")
    else:
        print("  No check has ever failed.")
    if warnings:
        print("  warnings:")
        for name, count in warnings.most_common():
            print(f"    {count:>3}  {name}")

    # ---- what kind of work each problem needs -----------------------------
    rule("WHAT KIND OF WORK THESE NEED")
    retrieval_problems = defaultdict(set)
    answer_problems = defaultdict(set)
    for row in latest_results:
        try:
            checks = json.loads(value(row, "checks_json", "[]"))
        except json.JSONDecodeError:
            continue
        for item in checks:
            if item.get("status") not in ("fail", "warn"):
                continue
            name = item.get("check", "")
            if name in RETRIEVAL_CHECKS:
                retrieval_problems[name].add(value(row, "question_id"))
            elif name in ANSWER_CHECKS:
                answer_problems[name].add(value(row, "question_id"))

    print("  Needing better RETRIEVAL (the right material was not found):")
    if retrieval_problems:
        for name, questions in sorted(retrieval_problems.items()):
            print(f"    {name}: {', '.join(sorted(questions))}")
        print("    Try --source-priority, a source-type filter, or check whether that material")
        print("    is indexed at all. Changing the prompt will not help these.")
    else:
        print("    None.")

    print("  Needing better PROMPT or AUDIT (the material was there, the answer mishandled it):")
    if answer_problems:
        for name, questions in sorted(answer_problems.items()):
            print(f"    {name}: {', '.join(sorted(questions))}")
        print("    These are about how the answer was written and checked, not about search.")
    else:
        print("    None.")

    # ---- readiness --------------------------------------------------------
    rule("IS THIS READY TO SCALE?")
    fail_count = int(value(latest, "fail_count", 0) or 0)
    review_count = int(value(latest, "review_count", 0) or 0)
    total = int(value(latest, "questions_total", 0) or 0)
    safety_failures = sum(1 for row in latest_results
                          for item in json.loads(value(row, "checks_json", "[]") or "[]")
                          if item.get("check") in ("safety_check",
                                                   "hallucination_guard_check")
                          and item.get("status") == "fail")

    if safety_failures:
        print("  NO. The latest run has a safety or grounding failure. Nothing else matters")
        print("  until that is understood - a system that invents or recommends is worse than")
        print("  no system, because people believe it.")
    elif fail_count:
        print(f"  NOT YET. {fail_count} of {total} questions failed. Fix those before pointing")
        print("  this at a second ticker; the same failure will repeat there, multiplied.")
    elif review_count > total / 2:
        print(f"  PROBABLY NOT. No failures, but {review_count} of {total} questions came back")
        print("  REVIEW. That usually means the archive covers these topics thinly. A second")
        print("  ticker will not fix thin coverage.")
    elif len(runs) < 2:
        print("  MAYBE. The latest run is clean, but there is only one evaluation on record.")
        print("  Run it again after the next change and see whether it stays clean.")
    else:
        print("  LOOKS READY on this evidence. The golden set passes and has done so before.")
        print("  Before scaling, grow the question set: the questions that matter are the ones")
        print("  people actually asked, especially the ones that came back Low confidence.")

    print()
    print("  Either way, this is a smoke alarm, not a fire inspection. It proves the system is")
    print("  behaving as designed on a fixed set of questions. It cannot tell you whether any")
    print("  answer is true.")

    conn.close()
    rule("")
    print("  AI drafts. Humans verify. Cutler decides.")
    print("=" * WIDTH)
    return 0


if __name__ == "__main__":
    sys.exit(main())
