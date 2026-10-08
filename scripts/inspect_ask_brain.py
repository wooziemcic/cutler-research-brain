#!/usr/bin/env python3
# =============================================================================
# Cutler Research Brain - Phase 5B
# inspect_ask_brain.py
#
# Console report over the ask_brain_runs table. Read-only: it opens the
# database, prints what has been asked and what came back, and writes nothing.
# No OpenAI call, no Chroma query, no PDF opened, no V Drive access.
#
# Usage:
#   py -3.11 scripts\inspect_ask_brain.py ^
#       --db "data\document_metadata.sqlite" ^
#       --project-root "C:\Users\AI Admin\Documents\CutlerResearchBrain" ^
#       --ticker MSFT
# =============================================================================

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
import textwrap
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


def connect(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise SystemExit(f"ERROR: database not found: {db_path}")
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def columns_of(conn: sqlite3.Connection, table: str) -> set:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def value(row: sqlite3.Row, key: str, default=""):
    try:
        got = row[key]
    except (IndexError, KeyError):
        return default
    return default if got is None else got


def short(text: str, limit: int) -> str:
    text = str(text or "").strip().replace("\n", " ")
    return text if len(text) <= limit else text[:limit - 3] + "..."


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Cutler Research Brain Phase 5B - console report over Ask Brain runs. "
                    "Read-only.")
    parser.add_argument("--db", default="data/document_metadata.sqlite",
                        help="SQLite path; relative paths resolve against --project-root.")
    parser.add_argument("--project-root", required=True,
                        help=r'e.g. "C:\\Users\\AI Admin\\Documents\\CutlerResearchBrain"')
    parser.add_argument("--ticker", default="MSFT", help="Ticker to report on (default MSFT).")
    parser.add_argument("--top", type=int, default=10,
                        help="How many recent runs to show in detail (default 10).")
    args = parser.parse_args(argv)

    project_root = Path(args.project_root).expanduser().resolve()
    db_path = Path(args.db).expanduser()
    if not db_path.is_absolute():
        db_path = (project_root / db_path).resolve()
    ticker = args.ticker.strip().upper()

    conn = connect(db_path)

    rule(f"ASK BRAIN - {ticker}")
    print(f"  database     : {db_path}")
    print(f"  project root : {project_root}")

    if not table_exists(conn, "ask_brain_runs"):
        print()
        print("  No ask_brain_runs table. Ask Brain has never run against this database.")
        print("  Ask a question first:")
        print(f"    python scripts\\ask_brain.py --project-root \"{project_root}\" "
              f"--ticker {ticker} --question \"...\"")
        conn.close()
        return 0

    have = columns_of(conn, "ask_brain_runs")

    # ---- index this ticker can draw on ------------------------------------
    if table_exists(conn, "rag_chunks"):
        idx = conn.execute(
            "SELECT COUNT(*) AS chunks, COUNT(DISTINCT document_id) AS docs, "
            "MAX(embedding_model) AS model FROM rag_chunks "
            "WHERE ticker = ? AND embedding_status = 'embedded'", (ticker,)).fetchone()
        print(f"  index        : {idx['chunks'] or 0} embedded chunks / "
              f"{idx['docs'] or 0} documents  ({idx['model'] or 'no model recorded'})")

    # ---- totals -----------------------------------------------------------
    totals = conn.execute(
        "SELECT COUNT(*) AS runs, "
        "  SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) AS completed, "
        "  SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed, "
        "  SUM(CASE WHEN status='no_evidence' THEN 1 ELSE 0 END) AS no_evidence, "
        "  COALESCE(SUM(estimated_cost_usd),0) AS cost, "
        "  COALESCE(SUM(prompt_tokens_estimate),0) AS in_tok, "
        "  COALESCE(SUM(output_tokens_estimate),0) AS out_tok, "
        "  MIN(created_at) AS first_run, MAX(created_at) AS last_run "
        "FROM ask_brain_runs WHERE ticker = ?", (ticker,)).fetchone()

    rule("TOTALS")
    print(f"  total runs                       : {totals['runs'] or 0}")
    print(f"  successful (completed)           : {totals['completed'] or 0}")
    print(f"  failed                           : {totals['failed'] or 0}")
    print(f"  stopped with no evidence         : {totals['no_evidence'] or 0}"
          "   <- no answer model was called")
    print(f"  first run                        : {totals['first_run'] or '-'}")
    print(f"  latest run                       : {totals['last_run'] or '-'}")
    print(f"  input tokens (est. or reported)  : {totals['in_tok'] or 0:,}")
    print(f"  output tokens (est. or reported) : {totals['out_tok'] or 0:,}")
    print(f"  estimated spend, all runs        : ${float(totals['cost'] or 0):.4f}")
    print("    (a local estimate from the rates in .env - it is not a bill)")

    if not (totals["runs"] or 0):
        print()
        print(f"  No Ask Brain runs recorded for {ticker} yet.")
        conn.close()
        return 0

    # ---- retrieval mode (Phase 6) -----------------------------------------
    if "retrieval_mode" in have:
        rule("RETRIEVAL MODE")
        for row in conn.execute(
            "SELECT COALESCE(NULLIF(retrieval_mode_effective,''), "
            "       COALESCE(NULLIF(retrieval_mode,''),'(not recorded)')) AS mode, "
            "  COUNT(*) AS n, "
            "  AVG(NULLIF(avg_vector_score,'')) AS v, "
            "  AVG(NULLIF(avg_keyword_score,'')) AS k, "
            "  AVG(NULLIF(avg_hybrid_score,'')) AS h, "
            "  AVG(NULLIF(candidates_considered,'')) AS c "
            "FROM ask_brain_runs WHERE ticker = ? GROUP BY mode ORDER BY n DESC", (ticker,)
        ):
            def num(value, width=6):
                return f"{value:{width}.3f}" if isinstance(value, float) else "     -"
            print(f"  {row['mode']:<10} runs {row['n']:>3}   "
                  f"avg vector {num(row['v'])}   avg keyword {num(row['k'])}   "
                  f"avg hybrid {num(row['h'])}")
            print(f"  {'':<10} avg candidates considered: "
                  f"{row['c']:.1f}" if isinstance(row["c"], float) else "")
        print("  Scores are averaged over the SELECTED evidence only. A dash means that")
        print("  retriever contributed nothing to those runs.")

        downgraded = conn.execute(
            "SELECT COUNT(*) FROM ask_brain_runs WHERE ticker = ? "
            "AND COALESCE(retrieval_mode,'') <> '' "
            "AND COALESCE(retrieval_mode_effective,'') <> '' "
            "AND retrieval_mode <> retrieval_mode_effective", (ticker,)).fetchone()[0]
        if downgraded:
            print(f"  {downgraded} run(s) asked for one mode and ran another - usually a missing")
            print("  keyword index. Build one: py -3.11 scripts\\build_keyword_index.py ...")

    # ---- models used ------------------------------------------------------
    rule("MODELS USED")
    for row in conn.execute(
        "SELECT answer_model, embedding_model, COUNT(*) AS n, MAX(created_at) AS last "
        "FROM ask_brain_runs WHERE ticker = ? GROUP BY answer_model, embedding_model "
        "ORDER BY n DESC", (ticker,)
    ):
        print(f"  {row['n']:>4}  answer: {row['answer_model'] or '-':<22} "
              f"query: {row['embedding_model'] or '-':<24} last: {row['last'] or '-'}")

    # ---- confidence -------------------------------------------------------
    if "confidence_stated" in have:
        rule("CONFIDENCE")
        print("  stated by the model:")
        for row in conn.execute(
            "SELECT COALESCE(NULLIF(confidence_stated,''),'(not stated)') AS c, COUNT(*) AS n "
            "FROM ask_brain_runs WHERE ticker = ? GROUP BY c ORDER BY n DESC", (ticker,)
        ):
            print(f"    {row['c']:<14} {row['n']}")
        if "confidence_rule" in have:
            print("  computed from the evidence (deterministic):")
            for row in conn.execute(
                "SELECT COALESCE(NULLIF(confidence_rule,''),'(none)') AS c, COUNT(*) AS n "
                "FROM ask_brain_runs WHERE ticker = ? GROUP BY c ORDER BY n DESC", (ticker,)
            ):
                print(f"    {row['c']:<14} {row['n']}")
            disagreements = conn.execute(
                "SELECT COUNT(*) FROM ask_brain_runs WHERE ticker = ? AND status='completed' "
                "AND confidence_stated <> '' AND confidence_rule <> '' "
                "AND confidence_stated <> confidence_rule", (ticker,)).fetchone()[0]
            print(f"  runs where the two disagree      : {disagreements}")
            if disagreements:
                print("    A disagreement is not an error. The model grades its own answer; the")
                print("    rule grades the evidence. Read both before trusting either.")

    # ---- confidence ceiling ----------------------------------------------
    if "confidence_rule_reason" in have:
        capped = conn.execute(
            "SELECT run_id, created_at, confidence_rule, question, confidence_rule_reason "
            "FROM ask_brain_runs WHERE ticker = ? "
            "AND confidence_rule_reason LIKE '%capped from%' "
            "ORDER BY run_id DESC LIMIT 10", (ticker,)).fetchall()
        no_direct = conn.execute(
            "SELECT COUNT(*) FROM ask_brain_runs WHERE ticker = ? AND status = 'completed' "
            "AND (confidence_rule_reason LIKE '%not one selected chunk is direct%' "
            "     OR confidence_rule_reason LIKE '%no selected chunk is direct%')",
            (ticker,)).fetchone()[0]
        rule("CONFIDENCE CEILING")
        print(f"  answers with no direct company/SEC/company-material evidence : {no_direct}")
        print(f"  answers whose grade was capped because of it                 : {len(capped)}")
        if capped:
            print("  Similarity measures how closely retrieval matched the WORDING of the")
            print("  question, not whether the evidence is the right KIND of source.")
            for row in capped:
                print(f"    run {row['run_id']} -> {row['confidence_rule']}: "
                      f"{short(row['question'], 60)}")
                wrap(short(row["confidence_rule_reason"], 300), "        ")
        else:
            print("  Nothing capped. Either the evidence included the company's own material,")
            print("  or the grade was already at or below its ceiling.")

    # ---- directness caps (Phase 6) ----------------------------------------
    if "directness_cap_applied" in have:
        has_sections = "directness_sections" in have
        columns = ("run_id, created_at, question, confidence_stated, final_confidence"
                   + (", directness_sections" if has_sections else ""))
        capped_rows = conn.execute(
            f"SELECT {columns} "
            "FROM ask_brain_runs WHERE ticker = ? AND COALESCE(directness_cap_applied,'0') = '1' "
            "ORDER BY run_id DESC LIMIT 10", (ticker,)).fetchall()
        rule("ANSWER-DIRECTNESS CAPS")
        print("  When an answer says its own evidence does not directly answer the question,")
        print("  final confidence is capped at Low. The model read the evidence; that verdict")
        print("  outranks any similarity score.")
        print("  Only the Direct Answer and Confidence sections count. Evidence Limitations")
        print("  saying what the evidence does NOT include is that section doing its job.")
        if capped_rows:
            print(f"  {len(capped_rows)} run(s) capped:")
            for row in capped_rows:
                where = value(row, "directness_sections", "") if has_sections else ""
                print(f"    run {row['run_id']}  stated {row['confidence_stated'] or '-'} "
                      f"-> final {row['final_confidence'] or '-'}"
                      + (f"   [triggered in: {where}]" if where else ""))
                wrap(short(row["question"], 200), "        ")
                if where and "(unparsed answer)" in where:
                    print("        NOTE: the answer had no parseable sections, so its opening")
                    print("        text was used. Check this one - it may be a false positive.")
        else:
            print("  None. No answer has reported its own evidence as non-responsive.")

    # ---- no-evidence runs, by filter (Phase 6) ----------------------------
    if "filter_summary" in have:
        empties = conn.execute(
            "SELECT run_id, created_at, question, filter_summary, error_message "
            "FROM ask_brain_runs WHERE ticker = ? AND status = 'no_evidence' "
            "ORDER BY run_id DESC LIMIT 10", (ticker,)).fetchall()
        rule("RUNS THAT FOUND NO EVIDENCE")
        if empties:
            for row in empties:
                print(f"  run {row['run_id']}  {row['created_at']}")
                wrap(f"question: {short(row['question'], 200)}", "      ")
                wrap(f"filters : {row['filter_summary'] or 'none'}", "      ")
                wrap(f"cause   : {short(value(row, 'error_message', ''), 200)}", "      ")
            print("  No answer model was called for any of these.")
        else:
            print("  None. Every run retrieved something.")

    # ---- audit flags (Phase 6) --------------------------------------------
    if "audit_flags" in have:
        rows_with_flags = conn.execute(
            "SELECT run_id, audit_flags FROM ask_brain_runs WHERE ticker = ? "
            "AND COALESCE(audit_flags,'') <> '' ORDER BY run_id DESC", (ticker,)).fetchall()
        rule("CITATION AUDIT FLAGS")
        if rows_with_flags:
            kinds = {}
            for row in rows_with_flags:
                for flag in str(row["audit_flags"]).split(";"):
                    flag = flag.strip()
                    if not flag:
                        continue
                    label = flag.split(":")[0].strip()
                    label = re.sub(r"^\d+\s+", "", label)
                    kinds[label] = kinds.get(label, 0) + 1
            print(f"  {len(rows_with_flags)} run(s) carry at least one flag.")
            for label, count in sorted(kinds.items(), key=lambda x: -x[1]):
                print(f"    {count:>3}  {label}")
            print("  Flags are reading aids, not failures. They mark the sentences to check")
            print("  first: uncited claims, unused evidence, citations pointing nowhere.")
        else:
            print("  No flags. Every answer cited what it used and used what it cited.")

    # ---- evidence shape ---------------------------------------------------
    shape = conn.execute(
        "SELECT AVG(selected_chunks) AS avg_chunks, MIN(selected_chunks) AS min_chunks, "
        "  MAX(selected_chunks) AS max_chunks, AVG(selected_documents) AS avg_docs "
        "FROM ask_brain_runs WHERE ticker = ? AND status='completed'", (ticker,)).fetchone()
    if shape and shape["avg_chunks"] is not None:
        rule("EVIDENCE PER ANSWER")
        print(f"  chunks    avg {shape['avg_chunks']:.1f}   min {shape['min_chunks']}   "
              f"max {shape['max_chunks']}")
        print(f"  documents avg {shape['avg_docs']:.1f}")

    # ---- flagged language -------------------------------------------------
    if "flagged_language" in have:
        flagged = conn.execute(
            "SELECT run_id, created_at, flagged_language FROM ask_brain_runs "
            "WHERE ticker = ? AND COALESCE(flagged_language,'') <> '' "
            "ORDER BY run_id DESC LIMIT 20", (ticker,)).fetchall()
        rule("LANGUAGE CHECK")
        if flagged:
            print(f"  {len(flagged)} answer(s) contained wording this project does not allow.")
            print("  Nothing was rewritten - these are flags for a human. Each flag quotes the")
            print("  exact matched text, so a false positive is visible without opening the file.")
            for row in flagged:
                print(f"    run {row['run_id']} ({row['created_at']}):")
                wrap(row["flagged_language"], "        ")
            print("  'sell-side', 'buy-side' and 'sell-through' are masked before this check")
            print("  runs, so describing a sell-side note can never register as a sell call.")
        else:
            print("  Clean. No run produced recommendation, rating or prediction language.")

    # ---- failures ---------------------------------------------------------
    failures = conn.execute(
        "SELECT run_id, created_at, status, question, error_message FROM ask_brain_runs "
        "WHERE ticker = ? AND status <> 'completed' ORDER BY run_id DESC LIMIT 10",
        (ticker,)).fetchall()
    rule("RUNS THAT DID NOT COMPLETE")
    if failures:
        for row in failures:
            print(f"  run {row['run_id']}  {row['created_at']}  [{row['status']}]")
            wrap(f"question: {short(row['question'], 300)}", "      ")
            wrap(f"reason  : {short(value(row, 'error_message', '(none recorded)'), 300)}",
                 "      ")
    else:
        print("  None. Every run completed.")

    # ---- recent runs ------------------------------------------------------
    recent = conn.execute(
        "SELECT * FROM ask_brain_runs WHERE ticker = ? ORDER BY run_id DESC LIMIT ?",
        (ticker, max(1, args.top))).fetchall()

    rule(f"LATEST {len(recent)} RUN(S)")
    for row in recent:
        print()
        print(f"  run {row['run_id']}  {value(row, 'created_at', '-')}  "
              f"[{value(row, 'status', '-')}]")
        wrap(f"Q: {value(row, 'question', '(none)')}", "      ")
        print(f"      model          : {value(row, 'answer_model', '-')}  "
              f"(query: {value(row, 'embedding_model', '-')})")
        print(f"      retrieval      : top_k {value(row, 'top_k', '-')}, "
              f"priority {value(row, 'source_priority', '-')}")
        print(f"      evidence       : {value(row, 'selected_chunks', 0)} chunk(s) from "
              f"{value(row, 'selected_documents', 0)} document(s)")
        print(f"      citations used : "
              f"{value(row, 'citations_used', '(none recorded)') or '(none)'}")
        if value(row, "retrieval_mode_effective", ""):
            print(f"      mode           : {row['retrieval_mode_effective']}"
                  + (f"  (asked for {row['retrieval_mode']})"
                     if value(row, "retrieval_mode", "") != row["retrieval_mode_effective"]
                     else ""))
        if value(row, "filter_summary", ""):
            wrap(f"filters        : {row['filter_summary']}", "      ")
        print(f"      confidence     : stated "
              f"{value(row, 'confidence_stated', '-') or '-'}  |  rule "
              f"{value(row, 'confidence_rule', '-') or '-'}  |  final "
              f"{value(row, 'final_confidence', '-') or '-'}"
              + ("  [directness cap]"
                 if str(value(row, "directness_cap_applied", "0")) == "1" else ""))
        if value(row, "confidence_rule_reason", ""):
            wrap(f"rule basis     : {row['confidence_rule_reason']}", "      ")
        print(f"      tokens         : {value(row, 'prompt_tokens_estimate', 0):,} in / "
              f"{value(row, 'output_tokens_estimate', 0):,} out")
        print(f"      est. cost      : ${float(value(row, 'estimated_cost_usd', 0) or 0):.5f}")
        answer_rel = value(row, "answer_path", "")
        obsidian_rel = value(row, "obsidian_answer_path", "")
        retrieval_rel = value(row, "retrieval_log_path", "")
        for label, rel in (("answer", answer_rel), ("obsidian", obsidian_rel),
                           ("retrieval", retrieval_rel)):
            if not rel:
                if label == "obsidian":
                    print("      obsidian       : (not saved - --no-save, or the run failed)")
                continue
            exists = (project_root / rel).exists()
            print(f"      {label:<14} : {rel}{'' if exists else '   [MISSING ON DISK]'}")
        if value(row, "flagged_language", ""):
            wrap(f"FLAGGED: {row['flagged_language']}", "      ")
        if value(row, "error_message", ""):
            wrap(f"error  : {short(row['error_message'], 300)}", "      ")

    conn.close()

    rule("READ THIS BEFORE USING ANY ANSWER")
    print("  Every answer above was written only from chunks retrieved out of the")
    print(f"  {ticker} index. None of it has been verified. A citation means a chunk was")
    print("  supplied to the model, not that the claim is correct - open the document and")
    print("  check the sentence yourself.")
    print("  Nothing here is a recommendation, a rating, or investment advice.")
    print("  AI drafts. Humans verify. Cutler decides.")
    print("=" * WIDTH)
    return 0


if __name__ == "__main__":
    sys.exit(main())
