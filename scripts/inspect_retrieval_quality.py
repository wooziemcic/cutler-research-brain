#!/usr/bin/env python3
# =============================================================================
# Cutler Research Brain - Phase 6
# inspect_retrieval_quality.py
#
# Is retrieval actually finding the right material? This report answers that
# from what the system has already recorded: the keyword index, the vector
# index, and every Ask Brain run's retrieval diagnostics.
#
# It reads. It writes two summary files inside the project and nothing else.
# No OpenAI call, no Chroma write, no PDF opened, no V Drive access.
#
# The questions it is built to answer:
#   - is the keyword index current, or has the chunk set moved on without it?
#   - which retrieval mode is actually being used?
#   - what KIND of source is the evidence coming from, question by question?
#   - where is confidence being capped, and why?
#   - is one document dominating the evidence packs?
#
# None of it says whether an answer was right. That is a human's job.
#
# Usage:
#   py -3.11 scripts\inspect_retrieval_quality.py ^
#       --db "data\document_metadata.sqlite" ^
#       --project-root "C:\Users\AI Admin\Documents\CutlerResearchBrain" ^
#       --ticker MSFT
# =============================================================================

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import textwrap
from collections import Counter
from pathlib import Path


# ---------------------------------------------------------------------------
# Console output (Phase 7A.1)
# A Windows console defaults to a legacy code page - usually cp1252 - which
# cannot encode characters that appear in archive text and in model answers
# (the non-breaking hyphen U+2011, curly quotes, dashes). Without this, a
# report could die halfway through printing. Ask for UTF-8 up front; anything
# still unprintable is replaced rather than raised.
# ---------------------------------------------------------------------------

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
PIPELINE_VERSION = "phase6-1.0"


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


def columns_of(conn: sqlite3.Connection, table: str) -> set:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def value(row, key, default=""):
    try:
        got = row[key]
    except (IndexError, KeyError):
        return default
    return default if got is None else got


# ---------------------------------------------------------------------------
# Index status
# ---------------------------------------------------------------------------

def keyword_status(project_root: Path, ticker: str, conn: sqlite3.Connection) -> dict:
    path = project_root / "06_RAG_Index" / "keyword" / f"{ticker}_keyword_index.json"
    status = {"path": path, "exists": path.exists(), "current": None, "missing": 0, "stale": 0,
              "index": None}
    if not path.exists():
        return status
    try:
        with path.open(encoding="utf-8") as fh:
            index = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        status["error"] = f"{type(exc).__name__}: {exc}"
        return status
    status["index"] = index

    if table_exists(conn, "rag_chunks"):
        current = {r[0] for r in conn.execute(
            "SELECT chunk_id FROM rag_chunks WHERE ticker = ? AND COALESCE(chunk_text,'') <> ''",
            (ticker,))}
        indexed = set(index.get("chunk_ids") or [])
        status["missing"] = len(current - indexed)
        status["stale"] = len(indexed - current)
        status["current"] = not status["missing"] and not status["stale"]
    return status


def vector_status(project_root: Path, ticker: str, conn: sqlite3.Connection) -> dict:
    store = project_root / "06_RAG_Index" / "chroma" / ticker
    status = {"path": store, "exists": store.exists(), "count": None, "collection": None}
    if table_exists(conn, "rag_chunks"):
        row = conn.execute(
            "SELECT COUNT(*) AS chunks, COUNT(DISTINCT document_id) AS docs, "
            "  SUM(CASE WHEN embedding_status='embedded' THEN 1 ELSE 0 END) AS embedded, "
            "  MAX(embedding_model) AS model, MAX(chroma_collection) AS collection "
            "FROM rag_chunks WHERE ticker = ?", (ticker,)).fetchone()
        status.update({"chunks": row["chunks"] or 0, "documents": row["docs"] or 0,
                       "embedded": row["embedded"] or 0, "model": row["model"],
                       "collection": row["collection"]})
    return status


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------

def load_diagnostics(project_root: Path, ticker: str) -> list:
    """Read the rolling diagnostics CSV written by ask_brain.py."""
    path = project_root / "06_RAG_Index" / "reports" / f"{ticker}_retrieval_diagnostics.csv"
    if not path.exists():
        return []
    import csv  # noqa: PLC0415
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def as_float(value_):
    try:
        return float(value_)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def build_summary(ticker: str, kw: dict, vec: dict, runs: list, diagnostics: list) -> str:
    selected = [d for d in diagnostics if (d.get("selected") or "").lower() == "yes"]
    source_mix = Counter(d.get("source_type_guess") or "(none)" for d in selected)
    type_mix = Counter(d.get("document_type_guess") or "(none)" for d in selected)
    kind_mix = Counter(d.get("kind") or "(none)" for d in selected)
    doc_mix = Counter(d.get("document_id") or "(none)" for d in selected)
    modes = Counter(str(value(r, "retrieval_mode_effective", "(not recorded)")) for r in runs)

    capped = [r for r in runs if str(value(r, "directness_cap_applied", "0")) == "1"]
    low = [r for r in runs if str(value(r, "final_confidence", "")) == "Low"]
    no_evidence = [r for r in runs if value(r, "status", "") == "no_evidence"]

    lines = [
        f"# Retrieval quality summary - {ticker}",
        "",
        f"_Generated by `inspect_retrieval_quality.py` ({PIPELINE_VERSION}) from the keyword "
        "index, the vector index and every recorded Ask Brain run._",
        "",
        "## What this measures, and what it does not",
        "",
        "This is a report on **retrieval**: what was searched, what came back, and what kind of "
        "source the evidence came from. It cannot tell you whether an answer was correct - "
        "nothing in this system can. It tells you whether the right material was even in front "
        "of the model when it wrote.",
        "",
        "> **AI drafts. Humans verify. Cutler decides.**",
        "",
        "## Indexes",
        "",
    ]

    if kw["exists"] and kw.get("index"):
        index = kw["index"]
        state = ("current" if kw["current"] else
                 f"OUT OF DATE - {kw['missing']} chunk(s) missing, {kw['stale']} stale")
        lines += [
            f"- Keyword (BM25): **{index.get('chunk_count', 0):,} chunks**, "
            f"{index.get('document_count', 0):,} documents, "
            f"{index.get('vocabulary_size', 0):,} terms. Built {index.get('created_at', '?')}. "
            f"**{state}.**",
        ]
        if not kw["current"]:
            lines.append("  - Rebuild it: `py -3.11 scripts\\build_keyword_index.py --force ...` "
                         "A stale keyword index silently under-retrieves, which is worse than "
                         "having none.")
    else:
        lines.append("- Keyword (BM25): **not built**. Hybrid retrieval falls back to "
                     "vector-only until `build_keyword_index.py` has run.")

    lines += [
        f"- Vector (Chroma): **{vec.get('embedded', 0):,} embedded chunks** across "
        f"{vec.get('documents', 0):,} documents, collection "
        f"`{vec.get('collection') or '(unknown)'}`, model `{vec.get('model') or '(unknown)'}`. "
        f"Store present: {'yes' if vec['exists'] else 'NO'}.",
        "",
        "## Ask Brain runs by retrieval mode",
        "",
    ]
    if modes:
        lines += ["| mode | runs |", "|---|---|"]
        for name, count in modes.most_common():
            lines.append(f"| {name} | {count} |")
    else:
        lines.append("_No runs recorded yet._")

    lines += ["", "## What kind of source the evidence came from", "",
              f"Across {len(selected):,} selected evidence chunk(s) in "
              f"{len({d.get('run_id') for d in selected}):,} run(s).",
              ""]
    if kind_mix:
        lines += ["| kind | chunks | share |", "|---|---|---|"]
        total = sum(kind_mix.values())
        for name, count in kind_mix.most_common():
            lines.append(f"| {name} | {count} | {count / total:.0%} |")
        lines += ["",
                  "`company_direct` is the company's own material - filings, press releases, "
                  "transcripts. `analyst` is third-party research. A question about reported "
                  "results whose evidence is all `analyst` is exactly the case the confidence "
                  "ceiling exists to catch.", ""]
    if source_mix:
        lines += ["| source_type_guess | chunks |", "|---|---|"]
        for name, count in source_mix.most_common(12):
            lines.append(f"| {name} | {count} |")
    if type_mix:
        lines += ["", "| document_type_guess | chunks |", "|---|---|"]
        for name, count in type_mix.most_common(12):
            lines.append(f"| {name} | {count} |")

    lines += ["", "## Document concentration", "",
              "If a handful of documents supply most of the evidence across every question, the "
              "index is narrower than it looks, or the per-document cap is too generous.",
              ""]
    if doc_mix:
        total = sum(doc_mix.values())
        top = doc_mix.most_common(10)
        share = sum(c for _, c in top) / total if total else 0
        lines.append(f"The top {len(top)} document(s) supply **{share:.0%}** of all selected "
                     f"evidence, out of {len(doc_mix)} document(s) that have ever been selected.")
        lines += ["", "| document_id | times selected |", "|---|---|"]
        for name, count in top:
            lines.append(f"| `{name}` | {count} |")

    lines += ["", "## Confidence", "",
              f"- Runs whose final confidence is Low: **{len(low)}** of {len(runs)}",
              f"- Runs capped because the answer said its own evidence was not direct: "
              f"**{len(capped)}**",
              f"- Runs that found no evidence at all (no answer model called): "
              f"**{len(no_evidence)}**",
              ""]
    if capped:
        lines += ["Capped runs:", ""]
        for r in capped[:15]:
            lines.append(f"- run {value(r, 'run_id')}: {short(value(r, 'question'), 120)}")
    if no_evidence:
        lines += ["", "No-evidence runs and the filters in force:", ""]
        for r in no_evidence[:15]:
            lines.append(f"- run {value(r, 'run_id')} (filters: "
                         f"{value(r, 'filter_summary', 'none') or 'none'}): "
                         f"{short(value(r, 'question'), 100)}")

    lines += ["", "## Low-confidence questions", "",
              "These are the questions the archive currently answers badly. They are the most "
              "useful list in this file: each one is either a gap in the indexed material or a "
              "question that needs rewording closer to how a document would put it.",
              ""]
    if low:
        for r in low[:20]:
            lines.append(f"- run {value(r, 'run_id')} "
                         f"(stated {value(r, 'confidence_stated', '-') or '-'}, rule "
                         f"{value(r, 'confidence_rule', '-') or '-'}): "
                         f"{short(value(r, 'question'), 120)}")
    else:
        lines.append("_None._")

    lines += ["", "## Where to look next", "",
              f"- Per-candidate diagnostics: `06_RAG_Index\\reports\\{ticker}_retrieval_"
              "diagnostics.csv` - one row per candidate per run, with both scores and the "
              "reason it was or was not selected.",
              f"- Per-run detail: `06_RAG_Index\\retrieval_logs\\{ticker}_<timestamp>_"
              "retrieval.json`",
              f"- Answers: `06_RAG_Index\\answer_logs\\`",
              ""]
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Cutler Research Brain Phase 6 - retrieval quality report. Read-only apart "
                    "from the two summary files it writes.")
    parser.add_argument("--db", default="data/document_metadata.sqlite",
                        help="SQLite path; relative paths resolve against --project-root.")
    parser.add_argument("--project-root", required=True,
                        help=r'e.g. "C:\\Users\\AI Admin\\Documents\\CutlerResearchBrain"')
    parser.add_argument("--ticker", default="MSFT", help="Ticker to report on (default MSFT).")
    parser.add_argument("--top", type=int, default=10, help="Rows in the detail lists.")
    args = parser.parse_args(argv)

    project_root = Path(args.project_root).expanduser().resolve()
    db_path = Path(args.db).expanduser()
    if not db_path.is_absolute():
        db_path = (project_root / db_path).resolve()
    ticker = args.ticker.strip().upper()
    top = max(1, args.top)

    conn = connect(db_path)

    rule(f"RETRIEVAL QUALITY - {ticker}")
    print(f"  database     : {db_path}")
    print(f"  project root : {project_root}")

    # ---- indexes ----------------------------------------------------------
    kw = keyword_status(project_root, ticker, conn)
    vec = vector_status(project_root, ticker, conn)

    rule("KEYWORD INDEX (BM25)")
    if not kw["exists"]:
        print(f"  NOT BUILT. Expected at {kw['path']}")
        print("  Hybrid retrieval will fall back to vector-only until it exists.")
        print(f"  Build it: py -3.11 scripts\\build_keyword_index.py --ticker {ticker} "
              f"--project-root \"{project_root}\"")
    elif kw.get("error"):
        print(f"  UNREADABLE: {kw['error']}")
        print("  Rebuild it with --force.")
    else:
        index = kw["index"]
        print(f"  file          : {kw['path'].name} "
              f"({kw['path'].stat().st_size / 1_048_576:.1f} MB)")
        print(f"  built         : {index.get('created_at', '?')}  "
              f"({index.get('pipeline_version', '?')})")
        print(f"  chunks        : {index.get('chunk_count', 0):,}")
        print(f"  documents     : {index.get('document_count', 0):,}")
        print(f"  vocabulary    : {index.get('vocabulary_size', 0):,} terms")
        print(f"  avg chunk     : {index.get('avg_chunk_length', 0):.0f} tokens")
        print(f"  scorer        : {index.get('scorer', '?')} "
              f"(k1={index.get('k1')}, b={index.get('b')})")
        if kw["current"]:
            print("  status        : CURRENT - it covers exactly the chunks in the database")
        else:
            print(f"  status        : OUT OF DATE - {kw['missing']} chunk(s) in the database are "
                  f"not indexed, {kw['stale']} indexed chunk(s) no longer exist")
            print("                  Rebuild: py -3.11 scripts\\build_keyword_index.py --force ...")
            print("                  A stale index under-retrieves silently.")

    if table_exists(conn, "keyword_index_runs"):
        builds = conn.execute(
            "SELECT run_id, created_at, status, chunks_indexed, vocabulary_size, error_message "
            "FROM keyword_index_runs WHERE ticker = ? ORDER BY run_id DESC LIMIT 5",
            (ticker,)).fetchall()
        if builds:
            print("  recent builds :")
            for row in builds:
                print(f"    run {row['run_id']}  {row['created_at']}  [{row['status']}]  "
                      f"{row['chunks_indexed'] or 0:,} chunks / "
                      f"{row['vocabulary_size'] or 0:,} terms")
                if row["error_message"]:
                    wrap(row["error_message"], "        ")

    rule("VECTOR INDEX (CHROMA)")
    print(f"  store present : {'yes' if vec['exists'] else 'NO'}  ({vec['path']})")
    print(f"  collection    : {vec.get('collection') or '(unknown)'}")
    print(f"  model         : {vec.get('model') or '(unknown)'}")
    print(f"  chunks        : {vec.get('embedded', 0):,} embedded of {vec.get('chunks', 0):,} "
          f"across {vec.get('documents', 0):,} documents")

    # ---- runs -------------------------------------------------------------
    runs = []
    if table_exists(conn, "ask_brain_runs"):
        have = columns_of(conn, "ask_brain_runs")
        wanted = [c for c in (
            "run_id", "created_at", "status", "question", "retrieval_mode",
            "retrieval_mode_effective", "filter_summary", "selected_chunks",
            "selected_documents", "source_priority", "confidence_stated", "confidence_rule",
            "final_confidence", "directness_cap_applied", "avg_vector_score",
            "avg_keyword_score", "avg_hybrid_score", "candidates_considered", "audit_flags",
        ) if c in have]
        runs = conn.execute(
            f"SELECT {','.join(wanted)} FROM ask_brain_runs WHERE ticker = ? "
            "ORDER BY run_id DESC", (ticker,)).fetchall()

    rule("ASK BRAIN RUNS BY RETRIEVAL MODE")
    if not runs:
        print("  No Ask Brain runs recorded yet. Ask a question first.")
    else:
        modes = Counter(str(value(r, "retrieval_mode_effective", "(not recorded)")) for r in runs)
        for name, count in modes.most_common():
            vectors = [as_float(value(r, "avg_vector_score")) for r in runs
                       if str(value(r, "retrieval_mode_effective", "")) == name]
            keywords = [as_float(value(r, "avg_keyword_score")) for r in runs
                        if str(value(r, "retrieval_mode_effective", "")) == name]
            hybrids = [as_float(value(r, "avg_hybrid_score")) for r in runs
                       if str(value(r, "retrieval_mode_effective", "")) == name]

            def mean(values):
                clean = [v for v in values if v is not None]
                return f"{sum(clean) / len(clean):.3f}" if clean else "-"

            print(f"  {name:<10} {count:>3} run(s)   avg vector {mean(vectors):>6}   "
                  f"avg keyword {mean(keywords):>6}   avg hybrid {mean(hybrids):>6}")
        print("  Averages are over the SELECTED evidence only.")

    # ---- diagnostics ------------------------------------------------------
    diagnostics = load_diagnostics(project_root, ticker)
    selected = [d for d in diagnostics if (d.get("selected") or "").lower() == "yes"]

    rule("EVIDENCE SELECTED, BY SOURCE")
    if not selected:
        print("  No diagnostics recorded yet "
              f"(06_RAG_Index\\reports\\{ticker}_retrieval_diagnostics.csv).")
    else:
        kinds = Counter(d.get("kind") or "(none)" for d in selected)
        total = sum(kinds.values())
        print(f"  {total:,} selected chunk(s) across "
              f"{len({d.get('run_id') for d in selected}):,} run(s)")
        for name, count in kinds.most_common():
            print(f"    {name:<16} {count:>5}  {count / total:>5.0%}")
        print()
        print("  by source_type_guess:")
        for name, count in Counter(
                d.get("source_type_guess") or "(none)" for d in selected).most_common(top):
            print(f"    {name:<32} {count:>5}")
        print("  by document_type_guess:")
        for name, count in Counter(
                d.get("document_type_guess") or "(none)" for d in selected).most_common(top):
            print(f"    {name:<32} {count:>5}")

        rule("SOURCE MIX BY QUESTION")
        by_run = {}
        for row in selected:
            by_run.setdefault(row.get("run_id"), []).append(row)
        for run_id in sorted(by_run, key=lambda x: -int(x) if str(x).isdigit() else 0)[:top]:
            rows = by_run[run_id]
            kinds = Counter(r.get("kind") or "(none)" for r in rows)
            mode = rows[0].get("retrieval_mode_effective") or "?"
            priority = rows[0].get("source_priority") or "?"
            print(f"  run {run_id}  [{mode}, priority {priority}]  "
                  + ", ".join(f"{k}: {v}" for k, v in kinds.most_common()))
            wrap(short(rows[0].get("question", ""), 200), "      ")

        rule("DOCUMENT CONCENTRATION")
        docs = Counter(d.get("document_id") or "(none)" for d in selected)
        total = sum(docs.values())
        head = docs.most_common(top)
        share = sum(c for _, c in head) / total if total else 0
        print(f"  {len(docs)} distinct document(s) have ever been selected.")
        print(f"  The top {len(head)} supply {share:.0%} of all selected evidence.")
        for name, count in head:
            print(f"    {count:>4}  {name}")
        if share > 0.8 and len(docs) > len(head):
            print("  That is concentrated. Either the index is narrower than it looks, or the")
            print("  per-document cap (--max-chunks-per-document) is letting one file dominate.")

        rule("SAMPLE RETRIEVAL DIAGNOSTICS")
        latest = max((d.get("run_id") for d in diagnostics if str(d.get("run_id")).isdigit()),
                     key=lambda x: int(x), default=None)
        sample = [d for d in diagnostics if d.get("run_id") == latest][:12]
        if sample:
            print(f"  run {latest}: {short(sample[0].get('question', ''), 120)}")
            print(f"  mode {sample[0].get('retrieval_mode_effective')}, "
                  f"filters: {sample[0].get('filters') or 'none'}")
            print()
            print(f"  {'rank':>4} {'vec':>6} {'kw':>6} {'score':>7} {'sel':>4}  kind / file")
            for row in sample:
                def cell(key):
                    number = as_float(row.get(key))
                    return f"{number:6.3f}" if number is not None else "     -"
                print(f"  {row.get('rank', '?'):>4} {cell('vector_score_norm')} "
                      f"{cell('keyword_score_norm')} {cell('hybrid_score'):>7} "
                      f"{row.get('selected', '?'):>4}  {row.get('kind', '?'):<14} "
                      f"{short(row.get('file_name', ''), 34)}")
            print()
            print("  A dash means that retriever never returned the chunk at all - not that it")
            print("  scored zero.")

    # ---- confidence -------------------------------------------------------
    if runs:
        rule("CONFIDENCE CAP EVENTS")
        capped = [r for r in runs if str(value(r, "directness_cap_applied", "0")) == "1"]
        print(f"  capped because the answer said its evidence was not direct : {len(capped)}")
        for row in capped[:top]:
            print(f"    run {row['run_id']}  stated "
                  f"{value(row, 'confidence_stated', '-') or '-'} -> final "
                  f"{value(row, 'final_confidence', '-') or '-'}")
            wrap(short(value(row, "question"), 200), "        ")

        low = [r for r in runs if str(value(r, "final_confidence", "")) == "Low"]
        rule("LOW-CONFIDENCE QUESTIONS")
        print("  These are the questions the indexed material currently answers badly.")
        print("  Each one is a gap in the archive or a question worth rewording.")
        if low:
            for row in low[:top]:
                print(f"    run {row['run_id']}  (stated "
                      f"{value(row, 'confidence_stated', '-') or '-'} / rule "
                      f"{value(row, 'confidence_rule', '-') or '-'})")
                wrap(short(value(row, "question"), 200), "        ")
        else:
            print("    None.")

    # ---- write the summary ------------------------------------------------
    summary = build_summary(ticker, kw, vec, runs, diagnostics)
    for path in (project_root / "06_RAG_Index" / "reports"
                 / f"{ticker}_retrieval_quality_summary.md",
                 project_root / "02_Metadata_Index" / f"{ticker}_retrieval_quality_summary.md"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(summary, encoding="utf-8")
    print()
    print(f"  Wrote 06_RAG_Index\\reports\\{ticker}_retrieval_quality_summary.md")
    print(f"  Wrote 02_Metadata_Index\\{ticker}_retrieval_quality_summary.md")

    conn.close()

    rule("WHAT THIS DOES NOT TELL YOU")
    print("  Nothing here says an answer was correct. It says what was searched, what came")
    print("  back, and what kind of source it came from. A high score means the words matched;")
    print("  it is not evidence of anything on its own.")
    print("  AI drafts. Humans verify. Cutler decides.")
    print("=" * WIDTH)
    return 0


if __name__ == "__main__":
    sys.exit(main())
