#!/usr/bin/env python3
"""Phase 11 Part A - what indexing these tickers would actually cost.

This script embeds nothing, writes nothing to Chroma, and calls no API. It
answers one question before any money is spent: for each ticker, what is in the
archive, how much of it is RAG-eligible, roughly how many chunks and tokens
that is, and what the embedding would cost.

It reads the archive's metadata only. The V drive is not opened at all - not
even to stat a file - because everything this report needs is already in the
index.

The estimate is deliberately conservative and is not a bill. The authority on
cost is the batch runner's own gate, which uses the same selection logic from
`ticker_selection.py` so the two cannot disagree.

  AI drafts. Humans verify. Cutler decides.

Usage:
  py -3.11 scripts\\plan_ticker_expansion.py ^
      --project-root "C:\\Users\\AI Admin\\Documents\\CutlerResearchBrain" ^
      --db "data\\document_metadata.sqlite" ^
      --tickers KIM KR MRK T VZ AMZN SLB B ABBV --export
"""
from __future__ import annotations

import argparse
import csv
import os
import sqlite3
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ticker_selection as ts                                     # noqa: E402

PIPELINE_VERSION = "phase11-1.0"

DEFAULT_COST_PER_1M_TOKENS = 0.02      # text-embedding-3-small, same as Phase 5A
DEFAULT_CHUNK_SIZE = 900               # tokens, same as build_rag_index.py
CHARS_PER_TOKEN = 4.0                  # the estimator used across this project
LARGE_DOCUMENT_CHARS = 400_000         # ~100k tokens: worth a second look
SHORT_TICKERS = ("T", "B", "K", "C", "F", "V", "X")


def configure_console_encoding() -> None:
    """Windows consoles are not UTF-8 by default and this report has arrows."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def safe_print(*parts) -> None:
    text = " ".join(str(part) for part in parts)
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode("ascii", "replace").decode("ascii"))


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 60000")
    return conn


def estimate_tokens(row: dict) -> int:
    """Tokens in one document's extracted text.

    Uses the recorded character count, because the point is to estimate before
    reading anything. A document with no recorded count contributes zero and is
    listed as a warning rather than silently guessed at.
    """
    chars = int(row.get("total_text_chars") or 0)
    return int(chars / CHARS_PER_TOKEN) if chars > 0 else 0


def estimate_chunks(tokens: int, chunk_size: int) -> int:
    return max(1, -(-tokens // chunk_size)) if tokens else 0


def date_range(rows: list) -> tuple:
    dates = sorted({str(row.get("folder_date_guess") or "").strip()
                    for row in rows if str(row.get("folder_date_guess") or "").strip()})
    return (dates[0], dates[-1]) if dates else ("", "")


def breakdown(rows: list, field: str) -> list:
    counter = Counter(str(row.get(field) or "Unknown") for row in rows)
    return counter.most_common()


def analyse(conn: sqlite3.Connection, project_root: Path, ticker: str,
            chunk_size: int, cost_per_1m: float, limit: int = 0) -> dict:
    """Everything the report says about one ticker."""
    rows = ts.candidate_rows(conn, ticker)
    eligible = [row for row in rows if ts.is_eligible(row)]
    if limit:
        eligible = eligible[:limit]
    ineligible = [row for row in rows if not ts.is_eligible(row)]

    for row in eligible:
        row["_tokens"] = estimate_tokens(row)
        row["_chunks"] = estimate_chunks(row["_tokens"], chunk_size)

    tokens = sum(row["_tokens"] for row in eligible)
    chunks = sum(row["_chunks"] for row in eligible)
    state = ts.index_state(conn, project_root, ticker)

    # --- warnings: everything a person should know before spending ----------
    warnings = []
    if ticker in SHORT_TICKERS:
        naive = conn.execute(
            "SELECT COUNT(*) FROM documents "
            " WHERE UPPER(COALESCE(ticker_candidates_clean,'')) LIKE ?",
            (f"%{ticker}%",)).fetchone()[0]
        warnings.append(
            f"SHORT TICKER: '{ticker}' is {len(ticker)} character(s). Matching is by whole "
            f"token, never substring. A substring match would have selected {naive} "
            f"document(s) instead of {len(rows)}.")

    multi = [row for row in rows if len(ts.ticker_tokens(row["ticker_candidates_clean"])) > 1]
    if multi:
        warnings.append(f"{len(multi)} document(s) are tagged with more than one ticker and "
                        f"will also be indexed under the others.")

    missing_text = [row for row in eligible if not row.get("document_text_path")]
    if missing_text:
        warnings.append(f"{len(missing_text)} eligible document(s) have no extracted text path "
                        f"recorded. They will produce no chunks.")

    no_chars = [row for row in eligible if row["_tokens"] == 0]
    if no_chars:
        warnings.append(f"{len(no_chars)} eligible document(s) have no recorded character "
                        f"count, so their tokens and cost are NOT included in this estimate.")

    large = [row for row in eligible if int(row.get("total_text_chars") or 0)
             > LARGE_DOCUMENT_CHARS]
    if large:
        warnings.append(f"{len(large)} document(s) are very large (over "
                        f"{LARGE_DOCUMENT_CHARS:,} characters) and dominate the cost.")

    scanned = [row for row in rows if str(row.get("scanned_pdf_suspected") or "0") in ("1", "True")]
    if scanned:
        warnings.append(f"{len(scanned)} document(s) look like scanned PDFs. OCR is not part of "
                        f"this phase, so they will contribute little or no text.")

    missing_source = [row for row in rows
                      if str(row.get("file_change_status") or "") == "missing_file"]
    if missing_source:
        warnings.append(f"{len(missing_source)} document(s) are flagged missing_file. Their "
                        f"extracted text is still indexable; the original is not on the drive.")

    stale = [row for row in rows if str(row.get("stale_extraction") or "0") in ("1", "True")
             or str(row.get("stale_document_card") or "0") in ("1", "True")]
    if stale:
        warnings.append(f"{len(stale)} document(s) have a stale extraction or card. Consider "
                        f"running the incremental ingestion before indexing them.")

    if state["embedded"]:
        warnings.append(f"ALREADY INDEXED: {state['embedded']} embedded chunk(s) exist for "
                        f"{ticker}. A build will add only what is pending, never re-embed.")
    if not eligible:
        warnings.append("Nothing is RAG-eligible for this ticker. A build would do nothing.")

    top = sorted(eligible, key=lambda row: row["_tokens"], reverse=True)[:20]
    from_date, to_date = date_range(rows)

    return {
        "ticker": ticker,
        "documents_total": len(rows),
        "documents_eligible": len(eligible),
        "documents_ineligible": len(ineligible),
        "source_types": breakdown(rows, "source_type_guess"),
        "document_types": breakdown(rows, "document_type_guess"),
        "date_from": from_date,
        "date_to": to_date,
        "estimated_tokens": tokens,
        "estimated_chunks": chunks,
        "estimated_cost_usd": round(tokens / 1_000_000 * cost_per_1m, 6),
        "duplicates": sum(1 for row in rows
                          if str(row.get("duplicate_status") or "") == "duplicate"),
        "high_review": sum(1 for row in rows
                           if str(row.get("review_severity") or "") == "High"),
        "skipped": len(ineligible),
        "scanned": len(scanned),
        "missing_source": len(missing_source),
        "multi_ticker": len(multi),
        "warnings": warnings,
        "top_documents": top,
        "ineligible_reasons": Counter(
            reason for row in ineligible for reason in ts.ineligibility_reasons(row)
        ).most_common(),
        "index_state": state,
    }


def write_markdown(path: Path, results: list, settings: dict, unassigned: list) -> None:
    total_cost = sum(r["estimated_cost_usd"] for r in results)
    total_chunks = sum(r["estimated_chunks"] for r in results)
    total_tokens = sum(r["estimated_tokens"] for r in results)
    total_eligible = sum(r["documents_eligible"] for r in results)

    lines = [
        "# Ticker expansion plan",
        "",
        f"_{PIPELINE_VERSION} · generated {settings['generated_at']}_",
        "",
        "> **Nothing has been embedded.** This report reads the metadata index only. No API",
        "> call was made, no Chroma collection was opened, and the V drive was not touched.",
        "",
        "> AI drafts. Humans verify. Cutler decides.",
        "",
        "## What a build would cost",
        "",
        "| Ticker | Documents | RAG-eligible | Est. chunks | Est. tokens | Est. cost | Already indexed |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for r in results:
        state = r["index_state"]
        indexed = (f"{state['embedded']:,} chunk(s)" if state["embedded"] else "no")
        lines.append(
            f"| **{r['ticker']}** | {r['documents_total']:,} | {r['documents_eligible']:,} | "
            f"{r['estimated_chunks']:,} | {r['estimated_tokens']:,} | "
            f"${r['estimated_cost_usd']:.4f} | {indexed} |")
    lines += [
        f"| **TOTAL** | | {total_eligible:,} | {total_chunks:,} | {total_tokens:,} | "
        f"**${total_cost:.4f}** | |",
        "",
        f"Cost is estimated at ${settings['cost_per_1m']:.4f} per 1M tokens "
        f"({settings['embedding_model']}), with tokens estimated at "
        f"{CHARS_PER_TOKEN:.0f} characters each and chunks at "
        f"{settings['chunk_size']:,} tokens. **It is an estimate, not a bill.**",
        "",
    ]

    if unassigned:
        lines += [
            "## Documents belonging to no ticker",
            "",
            f"{len(unassigned)} document(s) have raw ticker candidates but no clean one — the",
            "Phase 1 cleaner refused to choose between two companies rather than guess. They",
            "**will not be indexed for any ticker**, including these. Examples:",
            "",
        ]
        for row in unassigned[:10]:
            lines.append(f"- `{row['ticker_candidates_raw']}` — {row['file_name']}")
        lines.append("")

    for r in results:
        state = r["index_state"]
        lines += [
            "---",
            "",
            f"## {r['ticker']}",
            "",
            f"- **Documents tagged {r['ticker']}:** {r['documents_total']:,}",
            f"- **RAG-eligible:** {r['documents_eligible']:,}",
            f"- **Not eligible:** {r['documents_ineligible']:,}",
            f"- **Date range:** {r['date_from'] or 'unknown'} to {r['date_to'] or 'unknown'}",
            f"- **Estimated chunks:** {r['estimated_chunks']:,}",
            f"- **Estimated tokens:** {r['estimated_tokens']:,}",
            f"- **Estimated cost:** ${r['estimated_cost_usd']:.4f}",
            f"- **Duplicates:** {r['duplicates']:,} · **High review:** {r['high_review']:,} · "
            f"**Scanned:** {r['scanned']:,} · **Missing source:** {r['missing_source']:,}",
            f"- **Existing index:** {state['embedded']:,} embedded, {state['pending']:,} pending, "
            f"{state['failed']:,} failed · Chroma "
            f"{'present' if state['chroma_present'] else 'absent'} · keyword index "
            f"{'present' if state['keyword_present'] else 'absent'}",
            "",
        ]
        if r["warnings"]:
            lines += ["### Warnings", ""]
            lines += [f"- {warning}" for warning in r["warnings"]]
            lines.append("")
        if r["source_types"]:
            lines += ["### Source types", "", "| Source type | Documents |", "|---|---:|"]
            lines += [f"| {name} | {count:,} |" for name, count in r["source_types"]]
            lines.append("")
        if r["document_types"]:
            lines += ["### Document types", "", "| Document type | Documents |", "|---|---:|"]
            lines += [f"| {name} | {count:,} |" for name, count in r["document_types"]]
            lines.append("")
        if r["ineligible_reasons"]:
            lines += ["### Why documents were excluded", "", "| Reason | Documents |", "|---|---:|"]
            lines += [f"| {name} | {count:,} |" for name, count in r["ineligible_reasons"]]
            lines.append("")
        if r["top_documents"]:
            lines += [
                "### Largest 20 eligible documents",
                "",
                "| Date | File | Est. tokens | Est. chunks |",
                "|---|---|---:|---:|",
            ]
            for row in r["top_documents"]:
                lines.append(
                    f"| {row.get('folder_date_guess') or ''} | {row.get('file_name') or ''} | "
                    f"{row['_tokens']:,} | {row['_chunks']:,} |")
            lines.append("")

    lines += [
        "---",
        "",
        "## What to do with this",
        "",
        "1. Read the warnings above, especially for any ticker of one or two letters.",
        "2. Dry-run the batch: `py -3.11 scripts\\build_ticker_batch.py ... --dry-run`",
        "3. If the cost is acceptable, run it with `--embed --max-total-cost <amount>`.",
        "",
        "The batch runner refuses to spend more than `--max-total-cost` unless",
        "`--confirm-cost` is also passed. It uses the same selection logic as this report,",
        "so the two cannot disagree about what would be indexed.",
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def write_csv(path: Path, results: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow([
            "ticker", "documents_total", "documents_eligible", "documents_ineligible",
            "estimated_chunks", "estimated_tokens", "estimated_cost_usd", "date_from",
            "date_to", "duplicates", "high_review", "scanned", "missing_source",
            "multi_ticker", "already_embedded", "chroma_present", "keyword_present",
            "warnings"])
        for r in results:
            state = r["index_state"]
            writer.writerow([
                r["ticker"], r["documents_total"], r["documents_eligible"],
                r["documents_ineligible"], r["estimated_chunks"], r["estimated_tokens"],
                f"{r['estimated_cost_usd']:.6f}", r["date_from"], r["date_to"],
                r["duplicates"], r["high_review"], r["scanned"], r["missing_source"],
                r["multi_ticker"], state["embedded"], int(state["chroma_present"]),
                int(state["keyword_present"]), " | ".join(r["warnings"])])


def main() -> int:
    configure_console_encoding()
    parser = argparse.ArgumentParser(
        description="Phase 11: estimate what indexing each ticker would cost. Embeds nothing.")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--db", default="data/document_metadata.sqlite")
    parser.add_argument("--tickers", nargs="+", default=list(ts.PHASE11_TICKERS))
    parser.add_argument("--year", default="2026", help="Archive year, for the report header.")
    parser.add_argument("--limit", type=int, default=0,
                        help="Cap eligible documents per ticker, for a smaller trial.")
    parser.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE)
    parser.add_argument("--export", action="store_true",
                        help="Write the markdown and CSV reports.")
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

    cost_per_1m = float(os.environ.get("OPENAI_EMBEDDING_COST_PER_1M", "")
                        or DEFAULT_COST_PER_1M_TOKENS)
    settings = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "cost_per_1m": cost_per_1m,
        "chunk_size": args.chunk_size,
        "embedding_model": os.environ.get("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small"),
        "year": args.year,
    }

    conn = connect(db_path)
    try:
        results = [analyse(conn, project_root, ticker, args.chunk_size, cost_per_1m, args.limit)
                   for ticker in tickers]
        unassigned = ts.unassigned_documents(conn)
    finally:
        conn.close()

    safe_print("=" * 78)
    safe_print(f"TICKER EXPANSION PLAN  {PIPELINE_VERSION}   (nothing was embedded)")
    safe_print("=" * 78)
    safe_print(f"{'Ticker':<8}{'Docs':>7}{'Eligible':>10}{'Chunks':>9}{'Tokens':>12}{'Cost':>10}"
               f"   Already indexed")
    for r in results:
        state = r["index_state"]
        safe_print(f"{r['ticker']:<8}{r['documents_total']:>7,}{r['documents_eligible']:>10,}"
                   f"{r['estimated_chunks']:>9,}{r['estimated_tokens']:>12,}"
                   f"{'$' + format(r['estimated_cost_usd'], '.4f'):>10}"
                   f"   {state['embedded']:,} chunk(s)" if state["embedded"] else
                   f"{r['ticker']:<8}{r['documents_total']:>7,}{r['documents_eligible']:>10,}"
                   f"{r['estimated_chunks']:>9,}{r['estimated_tokens']:>12,}"
                   f"{'$' + format(r['estimated_cost_usd'], '.4f'):>10}   no")
    safe_print("-" * 78)
    safe_print(f"{'TOTAL':<8}{'':>7}{sum(r['documents_eligible'] for r in results):>10,}"
               f"{sum(r['estimated_chunks'] for r in results):>9,}"
               f"{sum(r['estimated_tokens'] for r in results):>12,}"
               f"{'$' + format(sum(r['estimated_cost_usd'] for r in results), '.4f'):>10}")
    safe_print("")

    for r in results:
        if r["warnings"]:
            safe_print(f"{r['ticker']}:")
            for warning in r["warnings"]:
                safe_print(f"   - {warning}")
    if unassigned:
        safe_print(f"\n{len(unassigned)} document(s) have raw ticker candidates but no clean "
                   f"one, and belong to no ticker.")

    if args.export:
        reports = project_root / "11_Ticker_Expansion" / "reports"
        md_path = reports / "ticker_expansion_plan.md"
        csv_path = reports / "ticker_expansion_plan.csv"
        write_markdown(md_path, results, settings, unassigned)
        write_csv(csv_path, results)
        safe_print(f"\nWrote {md_path}")
        safe_print(f"Wrote {csv_path}")
    else:
        safe_print("\n(Pass --export to write the markdown and CSV reports.)")

    safe_print("\nNothing was embedded. No API call was made. The V drive was not opened.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
