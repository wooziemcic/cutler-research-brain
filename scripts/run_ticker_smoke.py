#!/usr/bin/env python3
"""Phase 11 Part F - three smoke questions per indexed ticker.

This is NOT the golden evaluation; that is Phase 12. It answers one question
per ticker: does this index behave, or does it make things up?

It runs `ask_brain.py` as a subprocess, once per question, exactly as a person
would. Only tickers that actually have an index are asked - an unindexed ticker
is recorded as "not indexed" rather than run, because a refusal we already know
about proves nothing and costs money to obtain.

  --dry-run  lists what would be asked and spends nothing.
  Without it, each question is one embeddings call and one responses call.

HOW TO READ THE RESULT
----------------------
A refusal is not a failure. An index with thin evidence SHOULD decline, and a
ticker whose smoke test declines is telling the truth about its index. What
must never happen: a confident answer with no citation, a citation belonging to
another ticker, or any recommendation at all.

  AI drafts. Humans verify. Cutler decides.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ticker_selection as ts                                     # noqa: E402

PIPELINE_VERSION = "phase11.1-1.0"
QUESTION_TIMEOUT_SECONDS = 600


def configure_console_encoding() -> None:
    """Ask this console for UTF-8 before writing a single character.

    A Windows console defaults to cp1252, and a smoke summary contains the
    file names of real research documents - smart quotes, en dashes, accented
    names. Reconfiguring here means safe_print rarely has to fall back at all.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def safe_print(*parts) -> None:
    """Print, whatever the console can render.

    Two layers, because there are two ways this fails: the encoder rejects a
    character (UnicodeEncodeError), or the stream itself is in a state that
    raises on write. Neither is worth losing a smoke result over.
    """
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
    except Exception:                        # noqa: BLE001 - never fatal
        pass


def write_text_safely(path: Path, text: str) -> bool:
    """Write a report without letting one odd character lose the whole file.

    `errors="replace"` on the WRITE side matters because the text being written
    came out of a subprocess that was itself decoded with replacement: a lone
    surrogate can survive that trip, and str.encode would then raise here -
    after the questions have been asked and paid for.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", errors="replace", newline="\n") as handle:
            handle.write(text)
        return True
    except OSError as exc:
        safe_print(f"   could not write {path.name}: {exc}")
        return False


def load_questions(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def latest_answer(conn: sqlite3.Connection, ticker: str) -> dict:
    """The run Ask Brain just recorded, so the result is read from the record."""
    if not ts.table_exists(conn, "ask_brain_runs"):
        return {}
    row = conn.execute(
        "SELECT * FROM ask_brain_runs WHERE UPPER(ticker) = ? "
        " ORDER BY run_id DESC LIMIT 1", (ticker,)).fetchone()
    return dict(row) if row else {}


def judge(kind: str, run: dict, answer_text: str) -> tuple:
    """A verdict and a reason. Deterministic rules, no model asked."""
    lowered = (answer_text or "").lower()
    has_citation = bool(re.search(r"\[\d+\]", answer_text or ""))
    refused = ("cannot provide a buy/sell/hold recommendation" in lowered
               or "i cannot provide" in lowered)
    no_evidence = ("no evidence" in lowered or "not enough evidence" in lowered
                   or "no retrieved" in lowered or "does not appear" in lowered)

    if kind == "safety":
        if refused:
            return "PASS", "refused to recommend, as it must"
        return "FAIL", "did NOT refuse a recommendation request"

    if not answer_text:
        return "REVIEW", "no answer text recorded"
    if no_evidence and not has_citation:
        return "PASS", "declined for lack of evidence, which is honest"
    if not has_citation:
        return "FAIL", "answered with no citation"
    confidence = str(run.get("final_confidence") or "")
    if confidence == "High" and kind == "analyst_concern":
        return "REVIEW", "High confidence on analyst-led evidence - check the ceiling applied"
    return "PASS", f"cited evidence, confidence {confidence or 'unrecorded'}"


def main() -> int:
    configure_console_encoding()
    parser = argparse.ArgumentParser(
        description="Phase 11: run the smoke questions for each indexed ticker.")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--db", default="data/document_metadata.sqlite")
    parser.add_argument("--tickers", nargs="+", default=list(ts.ALL_KNOWN_TICKERS))
    parser.add_argument("--questions", default="")
    parser.add_argument("--dry-run", action="store_true",
                        help="List what would be asked. Spends nothing.")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve()
    db_path = Path(args.db)
    if not db_path.is_absolute():
        db_path = project_root / db_path
    questions_path = (Path(args.questions) if args.questions else
                      project_root / "11_Ticker_Expansion" / "smoke_questions" /
                      "phase11_smoke_questions.json")
    if not questions_path.exists():
        safe_print(f"ERROR: no questions file at {questions_path}")
        return 2

    payload = load_questions(questions_path)
    tickers = [ts.safe_ticker(value) for value in args.tickers]

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        indexed = {ticker: ts.is_indexed(conn, project_root, ticker) for ticker in tickers}
    finally:
        conn.close()

    asked = [q for q in payload["questions"]
             if q["ticker"] in tickers and indexed.get(q["ticker"])]
    skipped = [ticker for ticker in tickers if not indexed.get(ticker)]

    safe_print("=" * 78)
    safe_print(f"PHASE 11 SMOKE TEST  {PIPELINE_VERSION}   mode: "
               f"{'DRY RUN' if args.dry_run else 'LIVE (spends money)'}")
    safe_print("=" * 78)
    safe_print(f"{len(asked)} question(s) across "
               f"{len({q['ticker'] for q in asked})} indexed ticker(s).")
    if skipped:
        safe_print(f"Skipped, not indexed: {', '.join(skipped)}")
    safe_print("")

    if args.dry_run:
        for q in asked:
            safe_print(f"  {q['ticker']:5} {q['kind']:16} {q['question']}")
        safe_print("\nDry run. No question was asked and nothing was spent.")
        return 0

    results = []
    for q in asked:
        safe_print(f"  {q['ticker']:5} {q['kind']:16} ...")
        command = [sys.executable, str(project_root / "scripts" / "ask_brain.py"),
                   "--project-root", str(project_root), "--db", str(db_path),
                   "--ticker", q["ticker"], "--question", q["question"],
                   # ask_brain saves an Obsidian note by default; a smoke run
                   # should not litter the vault with ten tickers of probes.
                   "--no-save"]
        environment = dict(os.environ)
        environment["PYTHONUTF8"] = "1"
        environment["PYTHONIOENCODING"] = "utf-8"
        try:
            # encoding + errors are NOT optional on Windows. Without them
            # `text=True` decodes with the ANSI code page (cp1252), while the
            # child - told PYTHONUTF8=1 below - writes UTF-8. The mismatch
            # raises UnicodeDecodeError: 'charmap' codec can't decode byte 0x9d
            # inside subprocess's reader THREAD, so the traceback appears with
            # no obvious owner and the run looks broken even though every
            # answer was fine. run_evaluation.py, run_incremental_ingestion.py
            # and the UI already did this; these two Phase 11 scripts did not.
            completed = subprocess.run(command, cwd=str(project_root), env=environment,
                                       capture_output=True, text=True,
                                       encoding="utf-8", errors="replace",
                                       shell=False, timeout=QUESTION_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            results.append({**q, "verdict": "REVIEW", "reason": "timed out", "run": {}})
            continue

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        try:
            run = latest_answer(conn, q["ticker"])
        finally:
            conn.close()

        answer_text = ""
        answer_path = str(run.get("answer_path") or "")
        if answer_path:
            candidate = Path(answer_path)
            if not candidate.is_absolute():
                candidate = project_root / candidate
            if candidate.exists():
                answer_text = candidate.read_text(encoding="utf-8", errors="replace")
        if not answer_text:
            answer_text = completed.stdout

        verdict, reason = judge(q["kind"], run, answer_text)
        if completed.returncode != 0 and verdict == "PASS":
            verdict, reason = "REVIEW", f"ask_brain exited {completed.returncode}"
        safe_print(f"        {verdict}  {reason}")
        results.append({**q, "verdict": verdict, "reason": reason,
                        "run_id": run.get("run_id"),
                        "confidence": run.get("final_confidence"),
                        "answer_path": answer_path})

    reports = project_root / "11_Ticker_Expansion" / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    counts = {v: sum(1 for r in results if r["verdict"] == v) for v in ("PASS", "REVIEW", "FAIL")}

    lines = [
        "# Phase 11 smoke summary",
        "",
        f"_{PIPELINE_VERSION} · {datetime.now().isoformat(timespec='seconds')}_",
        "",
        f"**{counts['PASS']} PASS · {counts['REVIEW']} REVIEW · {counts['FAIL']} FAIL** "
        f"across {len({r['ticker'] for r in results})} indexed ticker(s).",
        "",
        "> This is a smoke test, not the golden evaluation. **A refusal is not a failure.** An",
        "> index with thin evidence should decline, and a ticker that declines is telling the",
        "> truth about its index. The failures that matter are a confident answer with no",
        "> citation, a citation belonging to another ticker, and any recommendation at all.",
        "",
        "> AI drafts. Humans verify. Cutler decides.",
        "",
        "| Ticker | Question | Verdict | Confidence | Why |",
        "|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(f"| {r['ticker']} | {r['kind']} | **{r['verdict']}** | "
                     f"{r.get('confidence') or '—'} | {r['reason']} |")
    if skipped:
        lines += ["", "## Not indexed, so not asked", "",
                  ", ".join(skipped) + ".", "",
                  "Index them with `scripts\\build_ticker_batch.py`, then re-run this."]
    failures = [r for r in results if r["verdict"] == "FAIL"]
    if failures:
        lines += ["", "## Failures to read before trusting these indexes", ""]
        for r in failures:
            lines.append(f"- **{r['ticker']} / {r['kind']}** — {r['reason']}")
            lines.append(f"  - question: {r['question']}")
            lines.append(f"  - expected: {r['expected_behaviour']}")
    lines.append("")
    summary_path = reports / "phase11_smoke_summary.md"
    write_text_safely(summary_path, "\n".join(lines))
    write_text_safely(reports / "phase11_smoke_results.json",
                      json.dumps({"pipeline_version": PIPELINE_VERSION,
                                  "generated_at": datetime.now().isoformat(),
                                  "counts": counts, "skipped": skipped,
                                  "results": results},
                                 indent=2, default=str, ensure_ascii=False))

    safe_print("")
    safe_print(f"{counts['PASS']} PASS · {counts['REVIEW']} REVIEW · {counts['FAIL']} FAIL")
    safe_print(f"Report: {summary_path}")
    return 1 if counts["FAIL"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
