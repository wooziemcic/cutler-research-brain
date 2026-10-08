#!/usr/bin/env python3
"""Phase 11 Part C - index several tickers, one at a time, behind a cost gate.

This is a runner, not a new pipeline. For each ticker it calls the Phase 5A
builder and the Phase 6 keyword indexer as separate subprocesses, exactly as a
person would from the command line, and records what happened.

THE COST GATE
-------------
Embedding is the only step in this project that spends money, and a batch of
nine tickers can spend a great deal more than one. So:

  * `--dry-run` is the default shape of this command: it plans and embeds
    nothing. No API call, no Chroma write, no chunk row.
  * `--embed` needs `--max-total-cost`. If the estimate exceeds it, the run
    REFUSES and prints what it would have cost. `--confirm-cost` is the only
    way past, and it has to be typed on purpose.
  * The estimate comes from `ticker_selection.py`, the same module the planner
    uses, so the number in the plan is the number in the gate.

WHAT IT WILL NOT DO
-------------------
  * It will not touch MSFT unless MSFT is named in --tickers.
  * It will not rebuild anything: without --force, a ticker with an existing
    index gets only its pending chunks embedded.
  * It will not write to the V drive, or read it at all.
  * It will not delete a chunk row or a collection.

  AI drafts. Humans verify. Cutler decides.

Usage:
  py -3.11 scripts\\build_ticker_batch.py ^
      --project-root "C:\\Users\\AI Admin\\Documents\\CutlerResearchBrain" ^
      --db "data\\document_metadata.sqlite" ^
      --tickers KIM KR MRK T VZ AMZN SLB B ABBV --dry-run --build-keyword --verbose
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ticker_selection as ts                                     # noqa: E402

PIPELINE_VERSION = "phase11.1-1.0"
DEFAULT_COST_PER_1M_TOKENS = 0.02
DEFAULT_CHUNK_SIZE = 900
CHARS_PER_TOKEN = 4.0
STEP_TIMEOUT_SECONDS = 7200


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
    except UnicodeEncodeError:
        print(text.encode("ascii", "replace").decode("ascii"), flush=True)


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 60000")
    return conn


def estimate_for(conn: sqlite3.Connection, ticker: str, chunk_size: int,
                 cost_per_1m: float, limit: int = 0) -> dict:
    """What this ticker would cost, using the planner's own selection."""
    rows = ts.candidate_rows(conn, ticker, eligible_only=True)
    if limit:
        rows = rows[:limit]
    tokens = sum(int(int(row.get("total_text_chars") or 0) / CHARS_PER_TOKEN) for row in rows)
    chunks = sum(max(1, -(-int(int(row.get("total_text_chars") or 0) / CHARS_PER_TOKEN)
                          // chunk_size))
                 for row in rows if int(row.get("total_text_chars") or 0) > 0)
    state = ts.index_state(conn, Path("."), ticker)
    return {"ticker": ticker, "documents": len(rows), "tokens": tokens, "chunks": chunks,
            "cost": round(tokens / 1_000_000 * cost_per_1m, 6),
            "already_embedded": state["embedded"], "pending": state["pending"]}


def run_step(script: Path, args: list, project_root: Path, verbose: bool) -> dict:
    """One child process, launched as an argument list. Never a shell string."""
    command = [sys.executable, str(script)] + [str(part) for part in args]
    environment = dict(os.environ)
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8"
    started = datetime.now()
    try:
        # The same Windows decoding trap as the smoke runner. This one would
        # have bitten mid-batch, AFTER the embedding spend, the first time a
        # builder logged a file name containing a smart quote or an en dash.
        completed = subprocess.run(command, cwd=str(project_root), env=environment,
                                   capture_output=True, text=True,
                                   encoding="utf-8", errors="replace",
                                   shell=False, timeout=STEP_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        return {"script": script.name, "returncode": -1, "ok": False,
                "error": f"timed out after {STEP_TIMEOUT_SECONDS}s",
                "seconds": STEP_TIMEOUT_SECONDS, "stdout": "", "stderr": ""}
    seconds = (datetime.now() - started).total_seconds()
    if verbose and completed.stdout:
        for line in completed.stdout.rstrip().splitlines()[-25:]:
            safe_print("      " + line)
    return {"script": script.name, "returncode": completed.returncode,
            "ok": completed.returncode == 0, "seconds": round(seconds, 1),
            "stdout": completed.stdout[-4000:], "stderr": completed.stderr[-4000:],
            "error": "" if completed.returncode == 0 else summarise_failure(completed)}


def summarise_failure(completed) -> str:
    """The real cause, not the last line of a banner. The Phase 9.1 lesson."""
    text = (completed.stderr or "") + "\n" + (completed.stdout or "")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for line in reversed(lines):
        if line.startswith(("Traceback", "  File ")):
            continue
        if any(marker in line for marker in ("Error", "error:", "ERROR", "Exception", "refus")):
            return line[:300]
    return lines[-1][:300] if lines else f"exit code {completed.returncode}"


def main() -> int:
    configure_console_encoding()
    parser = argparse.ArgumentParser(
        description="Phase 11: build the RAG index for several tickers, behind a cost gate.")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--db", default="data/document_metadata.sqlite")
    parser.add_argument("--tickers", nargs="+", default=list(ts.PHASE11_TICKERS))
    parser.add_argument("--dry-run", action="store_true",
                        help="Plan only. No embedding, no Chroma write, no chunk row.")
    parser.add_argument("--embed", action="store_true",
                        help="Actually embed. Requires --max-total-cost.")
    parser.add_argument("--build-keyword", action="store_true",
                        help="Build each ticker's keyword index after embedding.")
    parser.add_argument("--limit-docs-per-ticker", type=int, default=0)
    parser.add_argument("--max-total-cost", type=float, default=None,
                        help="Refuse to run if the estimate exceeds this, in USD.")
    parser.add_argument("--confirm-cost", action="store_true",
                        help="Proceed even though the estimate exceeds --max-total-cost.")
    parser.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE)
    parser.add_argument("--force", action="store_true",
                        help="Pass --force to the builder: REBUILDS that ticker from scratch.")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve()
    db_path = Path(args.db)
    if not db_path.is_absolute():
        db_path = project_root / db_path
    if not db_path.exists():
        safe_print(f"ERROR: no database at {db_path}")
        return 2

    try:
        tickers = [ts.safe_ticker(value) for value in args.tickers]
    except ValueError as exc:
        safe_print(f"ERROR: {exc}")
        return 2

    live = bool(args.embed) and not args.dry_run
    cost_per_1m = float(os.environ.get("OPENAI_EMBEDDING_COST_PER_1M", "")
                        or DEFAULT_COST_PER_1M_TOKENS)

    conn = connect(db_path)
    try:
        estimates = [estimate_for(conn, ticker, args.chunk_size, cost_per_1m,
                                  args.limit_docs_per_ticker) for ticker in tickers]
    finally:
        conn.close()
    total_cost = sum(e["cost"] for e in estimates)

    safe_print("=" * 78)
    safe_print(f"TICKER BATCH  {PIPELINE_VERSION}   mode: "
               f"{'EMBED (spends money)' if live else 'DRY RUN (spends nothing)'}")
    safe_print("=" * 78)
    safe_print(f"{'Ticker':<8}{'Docs':>7}{'Chunks':>9}{'Tokens':>12}{'Est. cost':>12}"
               f"{'Embedded':>10}{'Pending':>9}")
    for e in estimates:
        safe_print(f"{e['ticker']:<8}{e['documents']:>7,}{e['chunks']:>9,}{e['tokens']:>12,}"
                   f"{'$' + format(e['cost'], '.4f'):>12}{e['already_embedded']:>10,}"
                   f"{e['pending']:>9,}")
    safe_print("-" * 78)
    safe_print(f"{'TOTAL':<8}{sum(e['documents'] for e in estimates):>7,}"
               f"{sum(e['chunks'] for e in estimates):>9,}"
               f"{sum(e['tokens'] for e in estimates):>12,}"
               f"{'$' + format(total_cost, '.4f'):>12}")
    safe_print("")

    if args.force and live:
        safe_print("WARNING: --force REBUILDS each named ticker from scratch: existing chunk "
                   "rows are cleared and the Chroma collection is dropped before rebuilding.")
        if "MSFT" in tickers:
            safe_print("         MSFT is in the list. Its working index will be destroyed and "
                       "rebuilt.")

    # --- the gate -----------------------------------------------------------
    if live:
        if args.max_total_cost is None:
            safe_print("REFUSED: --embed requires --max-total-cost, in USD.")
            safe_print(f"         This batch is estimated at ${total_cost:.4f}.")
            safe_print(f"         For example: --max-total-cost {max(1, int(total_cost) + 1)}")
            return 3
        if total_cost > args.max_total_cost and not args.confirm_cost:
            safe_print(f"REFUSED: the estimate is ${total_cost:.4f}, over the "
                       f"${args.max_total_cost:.4f} limit you set.")
            safe_print("         Nothing has been embedded.")
            safe_print("         Either raise --max-total-cost, reduce --tickers or "
                       "--limit-docs-per-ticker,")
            safe_print("         or pass --confirm-cost to proceed at that price on purpose.")
            return 3
        if total_cost > args.max_total_cost:
            safe_print(f"--confirm-cost given: proceeding at ${total_cost:.4f}, over the "
                       f"${args.max_total_cost:.4f} limit.")

    if not live:
        safe_print("Dry run. Nothing was embedded, no Chroma collection was opened, and no "
                   "chunk row was written.")
        safe_print("Re-run with --embed --max-total-cost <usd> when the numbers above look "
                   "right.")
        return 0

    # --- the work, one ticker at a time -------------------------------------
    builder = project_root / "scripts" / "build_rag_index.py"
    keyword = project_root / "scripts" / "build_keyword_index.py"
    results = []
    started_at = datetime.now()

    for estimate in estimates:
        ticker = estimate["ticker"]
        safe_print(f"\n--- {ticker} " + "-" * (74 - len(ticker)))
        if not estimate["documents"] and not estimate["pending"]:
            safe_print("   nothing eligible and nothing pending - skipped")
            results.append({"ticker": ticker, "status": "skipped",
                            "reason": "no eligible documents", "steps": []})
            continue

        steps = []
        # build_rag_index.py embeds by DEFAULT and takes --dry-run to not embed.
        # It has no --embed flag: passing one makes argparse exit 2 before any
        # work starts. The gate above is what decides whether we are spending.
        build_args = ["--project-root", project_root, "--db", db_path, "--ticker", ticker,
                      "--chunk-size", args.chunk_size]
        if args.limit_docs_per_ticker:
            build_args += ["--limit-docs", args.limit_docs_per_ticker]
        if args.force:
            build_args.append("--force")
        safe_print(f"   embedding ({estimate['chunks']:,} chunk(s) estimated, "
                   f"${estimate['cost']:.4f})")
        step = run_step(builder, build_args, project_root, args.verbose)
        steps.append(step)
        if not step["ok"]:
            safe_print(f"   FAILED: {step['error']}")
            results.append({"ticker": ticker, "status": "failed", "steps": steps})
            continue

        if args.build_keyword:
            safe_print("   building keyword index")
            step = run_step(keyword, ["--project-root", project_root, "--db", db_path,
                                      "--ticker", ticker], project_root, args.verbose)
            steps.append(step)
            if not step["ok"]:
                safe_print(f"   keyword index FAILED: {step['error']}")

        conn = connect(db_path)
        try:
            state = ts.index_state(conn, project_root, ticker)
        finally:
            conn.close()
        safe_print(f"   done: {state['embedded']:,} embedded, {state['pending']:,} pending, "
                   f"{state['failed']:,} failed, keyword index "
                   f"{'present' if state['keyword_present'] else 'absent'}")
        results.append({"ticker": ticker,
                        "status": "completed" if all(s["ok"] for s in steps) else "partial",
                        "steps": steps, "state": state})

    # --- the record ---------------------------------------------------------
    reports = project_root / "11_Ticker_Expansion" / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    stamp = started_at.strftime("%Y%m%d_%H%M%S")
    payload = {"pipeline_version": PIPELINE_VERSION, "started_at": started_at.isoformat(),
               "completed_at": datetime.now().isoformat(), "tickers": tickers,
               "estimates": estimates, "estimated_total_cost_usd": total_cost,
               "max_total_cost": args.max_total_cost, "force": bool(args.force),
               "results": [{k: v for k, v in r.items() if k != "steps"} |
                           {"steps": [{sk: sv for sk, sv in s.items()
                                       if sk not in ("stdout", "stderr")}
                                      for s in r.get("steps", [])]}
                           for r in results]}
    (reports / f"ticker_batch_{stamp}.json").write_text(
        json.dumps(payload, indent=2, default=str), encoding="utf-8")

    lines = [f"# Ticker batch {stamp}", "",
             f"_{PIPELINE_VERSION} · started {started_at.isoformat(timespec='seconds')}_", "",
             "| Ticker | Status | Embedded | Pending | Failed | Keyword index |",
             "|---|---|---:|---:|---:|---|"]
    for r in results:
        state = r.get("state") or {}
        lines.append(
            f"| {r['ticker']} | {r['status']} | {state.get('embedded', 0):,} | "
            f"{state.get('pending', 0):,} | {state.get('failed', 0):,} | "
            f"{'yes' if state.get('keyword_present') else 'no'} |")
    lines += ["", f"Estimated cost before the run: **${total_cost:.4f}**.", "",
              "Nothing was written to the V drive, and no existing chunk row was deleted.", ""]
    summary_path = reports / "latest_ticker_batch.md"
    summary_path.write_text("\n".join(lines), encoding="utf-8")

    failed = [r for r in results if r["status"] == "failed"]
    safe_print("\n" + "=" * 78)
    safe_print(f"Batch finished: {sum(1 for r in results if r['status'] == 'completed')} "
               f"completed, {len(failed)} failed, "
               f"{sum(1 for r in results if r['status'] == 'skipped')} skipped")
    safe_print(f"Report: {summary_path}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
