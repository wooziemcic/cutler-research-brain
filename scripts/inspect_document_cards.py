#!/usr/bin/env python3
# =============================================================================
# Cutler Research Brain - Phase 3
# inspect_document_cards.py
#
# Read-only console report over the document_cards table, so a human can see
# what the card build produced before anything downstream is built.
#
# Prints:
#   - total document cards created
#   - card status counts
#   - card quality counts
#   - usable_for_rag / not usable counts
#   - top RAG exclusion reasons
#   - counts by source type, document type and extraction quality
#   - a sample of cards created
#   - a sample of cards NOT usable for RAG
#
# Usage:
#   python scripts\inspect_document_cards.py ^
#       --db "data\document_metadata.sqlite" ^
#       --project-root "C:\Users\AI Admin\Documents\CutlerResearchBrain"
#
# Reads only. Never writes to the database, the cards, the extracted text or
# the V Drive. Nothing here summarizes or interprets any document.
# Principle: AI drafts. Humans verify. Cutler decides.
# =============================================================================

from __future__ import annotations

import argparse
import sqlite3
import sys
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


TOP_N = 20

CARD_CREATED = "created"

STATUS_LABELS = {
    "created": "card written",
    "skipped_duplicate": "skipped - duplicate of another file",
    "skipped_high_review": "skipped - High-review gate",
    "skipped_missing_text": "skipped - no extracted text on disk",
    "skipped_failed_extraction": "skipped - extraction failed or not a PDF",
    "failed": "card build failed",
}

# Phase 3.1 reason vocabulary, in the order they are printed. The old catch-all
# "missing_text" is legacy: it is only shown if rows still carry it.
REASON_ORDER = [
    "none",
    "duplicate",
    "high_review",
    "failed_extraction",
    "missing_text_file",
    "empty_text",
    "scanned_pdf_no_text",
    "not_pdf",
]

REASON_LABELS = {
    "none": "usable - text available for a later phase",
    "duplicate": "duplicate of another indexed file",
    "high_review": "High review - possible client/portfolio material",
    "failed_extraction": "extraction failed, was skipped, or never ran",
    "missing_text_file": "expected document_text.txt is missing on disk",
    "empty_text": "extraction ran but produced no meaningful text",
    "scanned_pdf_no_text": "likely image-only scan - OCR candidate for a later phase",
    "not_pdf": "not a PDF",
    "missing_text": "LEGACY Phase 3.0 value - run refresh_document_card_reports.py --fix-reasons",
}

LEGACY_REASONS = {"missing_text"}


def open_readonly(db_path: Path) -> sqlite3.Connection:
    try:
        conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    except sqlite3.OperationalError:
        conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def query(conn: sqlite3.Connection, sql: str, params=()) -> list[tuple]:
    return [tuple(r) for r in conn.execute(sql, params)]


def scalar(conn: sqlite3.Connection, sql: str, params=()) -> int:
    return int(conn.execute(sql, params).fetchone()[0] or 0)


def print_header(title: str) -> None:
    print()
    print(title)
    print("-" * max(len(title), 46))


def line(label: str, value, width: int = 40) -> None:
    print(f"  {label:<{width}} {value:>10,}")


def print_counts(rows, label_width: int = 30, note: dict | None = None) -> None:
    if not rows:
        print("  (none)")
        return
    total = sum(int(r[1] or 0) for r in rows) or 1
    for label, count in rows:
        text = str(label) if str(label).strip() else "(none)"
        suffix = f"   {note.get(text, '')}" if note else ""
        print(f"  {text:<{label_width}} {int(count):>8,}  {int(count) / total * 100:>5.1f}%{suffix}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Cutler Research Brain Phase 3 - inspect the document cards.",
    )
    parser.add_argument("--db", required=True,
                        help=r'SQLite index, e.g. "data\document_metadata.sqlite"')
    parser.add_argument("--project-root", default="",
                        help=r'Project root, e.g. "C:\Users\AI Admin\Documents\CutlerResearchBrain"')
    parser.add_argument("--top", type=int, default=TOP_N,
                        help=f"How many rows in the sample listings (default {TOP_N}).")
    args = parser.parse_args(argv)

    project_root = Path(args.project_root).expanduser().resolve() if args.project_root else None

    db_path = Path(args.db).expanduser()
    if not db_path.is_absolute() and project_root:
        db_path = (project_root / db_path).resolve()
    if not db_path.exists():
        print(f"ERROR: database not found: {db_path}", file=sys.stderr)
        return 1

    conn = open_readonly(db_path)
    try:
        tables = {r[0] for r in query(conn, "SELECT name FROM sqlite_master WHERE type='table'")}
        if "document_cards" not in tables:
            print("ERROR: no 'document_cards' table in this database.", file=sys.stderr)
            print("Run scripts/build_document_cards.py first.", file=sys.stderr)
            return 1

        cards = scalar(conn, "SELECT COUNT(*) FROM document_cards")

        print("=" * 78)
        print("CUTLER RESEARCH BRAIN - PHASE 3 DOCUMENT CARDS")
        print(f"Database: {db_path.resolve()}")
        if project_root:
            print(f"Cards   : {project_root / '04_Document_Cards'}")
        print("=" * 78)

        if cards == 0:
            print("\nNo document cards yet. Run scripts/build_document_cards.py.")
            return 0

        created = scalar(conn, "SELECT COUNT(*) FROM document_cards WHERE card_status = ?",
                         (CARD_CREATED,))
        skipped = scalar(conn, "SELECT COUNT(*) FROM document_cards WHERE card_status LIKE 'skipped%'")
        failed = scalar(conn, "SELECT COUNT(*) FROM document_cards WHERE card_status = 'failed'")
        usable = scalar(conn, "SELECT COUNT(*) FROM document_cards WHERE usable_for_rag = 1")
        not_usable = scalar(conn, "SELECT COUNT(*) FROM document_cards WHERE usable_for_rag = 0")
        needs_review = scalar(conn, "SELECT COUNT(*) FROM document_cards "
                                    "WHERE card_quality = 'Needs Review'")
        preview_chars = scalar(conn, "SELECT COALESCE(SUM(deterministic_preview_chars),0) "
                                     "FROM document_cards")
        pdfs = scalar(conn, "SELECT COUNT(*) FROM documents "
                            "WHERE LOWER(COALESCE(file_extension,'')) = '.pdf'") \
            if "documents" in tables else 0

        print_header("OVERVIEW")
        if pdfs:
            line("PDF rows in the metadata index", pdfs)
        line("Card rows in total", cards)
        print()
        line("DOCUMENT CARDS CREATED", created)
        line("Rows skipped (no card written)", skipped)
        line("Card build failures", failed)
        print()
        line("Usable for RAG", usable)
        line("NOT usable for RAG", not_usable)
        line("Cards marked Needs Review", needs_review)
        if created:
            print(f"\n  {usable / created * 100:.1f}% of created cards are RAG-ready.")

        print_header("CARD STATUS")
        print_counts(query(conn, "SELECT COALESCE(card_status,'(none)'), COUNT(*) "
                                 "FROM document_cards GROUP BY 1 ORDER BY COUNT(*) DESC"),
                     label_width=28, note=STATUS_LABELS)

        print_header("CARD QUALITY (created cards only)")
        print_counts(query(conn, "SELECT COALESCE(card_quality,'(none)'), COUNT(*) "
                                 "FROM document_cards WHERE card_status = ? "
                                 "GROUP BY 1 ORDER BY COUNT(*) DESC", (CARD_CREATED,)),
                     label_width=16)
        print("\n  Card quality = how COMPLETE the card is (text volume + metadata filled in).")
        print("  It is not a judgement about the document's content.")

        # ---- RAG exclusion reasons, one line per reason, fixed order -------
        counts = {
            (reason or "none"): count
            for reason, count in query(
                conn, "SELECT COALESCE(rag_exclusion_reason,'none'), COUNT(*) "
                      "FROM document_cards GROUP BY 1")
        }
        legacy = {r: n for r, n in counts.items()
                  if r not in REASON_ORDER and n}

        print_header("RAG EXCLUSION REASONS (every card, one line per reason)")
        for reason in REASON_ORDER:
            count = counts.get(reason, 0)
            mark = "usable  " if reason == "none" else "excluded"
            share = count / cards * 100 if cards else 0.0
            print(f"  {reason:<22} {count:>8,}  {share:>5.1f}%  {mark}  "
                  f"{REASON_LABELS.get(reason, '')}")

        if legacy:
            print()
            print("  LEGACY VALUES STILL PRESENT (Phase 3.0):")
            for reason, count in sorted(legacy.items(), key=lambda kv: -kv[1]):
                print(f"  {reason:<22} {count:>8,}         {REASON_LABELS.get(reason, '')}")
            print("\n  Run: python scripts\\refresh_document_card_reports.py --db ... "
                  "--project-root ... --fix-reasons --rewrite-cards")

        print()
        print("  missing_text_file   = the pipeline's own text output is absent - re-extract.")
        print("  empty_text          = extraction ran, the PDF had no usable text layer.")
        print("  scanned_pdf_no_text = image-only scan; OCR candidate for a LATER phase.")
        print("  duplicate / high_review / failed_extraction are gates, not defects.")

        join_ok = "documents" in tables
        if join_ok:
            print_header("CREATED CARDS BY SOURCE TYPE GUESS")
            print_counts(query(conn,
                               "SELECT COALESCE(d.source_type_guess,'(none)'), COUNT(*) "
                               "FROM document_cards c LEFT JOIN documents d "
                               "ON d.document_id = c.document_id WHERE c.card_status = ? "
                               "GROUP BY 1 ORDER BY COUNT(*) DESC", (CARD_CREATED,)))

            print_header("CREATED CARDS BY DOCUMENT TYPE GUESS")
            print_counts(query(conn,
                               "SELECT COALESCE(d.document_type_guess,'(none)'), COUNT(*) "
                               "FROM document_cards c LEFT JOIN documents d "
                               "ON d.document_id = c.document_id WHERE c.card_status = ? "
                               "GROUP BY 1 ORDER BY COUNT(*) DESC", (CARD_CREATED,)))

            print_header("CREATED CARDS BY PDF EXTRACTION QUALITY")
            print_counts(query(conn,
                               "SELECT COALESCE(d.pdf_extraction_quality,'(none)'), COUNT(*) "
                               "FROM document_cards c LEFT JOIN documents d "
                               "ON d.document_id = c.document_id WHERE c.card_status = ? "
                               "GROUP BY 1 ORDER BY COUNT(*) DESC", (CARD_CREATED,)),
                         label_width=16)

        print_header("TEXT ON THE CARDS")
        line("Total deterministic preview characters", preview_chars, width=40)
        if created:
            line("Average preview characters per card", preview_chars // created, width=40)
        avg_headings = conn.execute(
            "SELECT COALESCE(AVG(heading_candidate_count),0) FROM document_cards "
            "WHERE card_status = ?", (CARD_CREATED,)).fetchone()[0]
        print(f"  {'Average heading candidates per card':<40} {float(avg_headings or 0):>10.1f}")
        print("\n  Previews are verbatim slices of extracted text. Nothing was summarized.")

        # ---- samples --------------------------------------------------------
        name_expr = "COALESCE(d.relative_path, c.document_id)" if join_ok else "c.document_id"
        from_expr = ("document_cards c LEFT JOIN documents d ON d.document_id = c.document_id"
                     if join_ok else "document_cards c")

        print_header(f"SAMPLE OF {args.top} CARDS CREATED")
        rows = query(conn, f"""
            SELECT {name_expr}, c.card_quality, c.usable_for_rag,
                   c.deterministic_preview_chars, c.heading_candidate_count
              FROM {from_expr}
             WHERE c.card_status = ?
             ORDER BY c.deterministic_preview_chars DESC
             LIMIT ?
        """, (CARD_CREATED, args.top))
        if rows:
            for name, quality, rag, chars, headings in rows:
                print(f"  {str(quality or ''):<13} rag={'yes' if rag else 'no ':<3} "
                      f"{int(chars or 0):>5} chars  {int(headings or 0):>2} hdr  {str(name)[:52]}")
        else:
            print("  (none)")

        print_header(f"SAMPLE OF {args.top} CARDS NOT USABLE FOR RAG")
        rows = query(conn, f"""
            SELECT {name_expr}, c.card_status, c.rag_exclusion_reason
              FROM {from_expr}
             WHERE c.usable_for_rag = 0
             ORDER BY c.rag_exclusion_reason, {name_expr}
             LIMIT ?
        """, (args.top,))
        if rows:
            for name, status, reason in rows:
                print(f"  {str(reason or ''):<22} {str(status or ''):<26} {str(name)[:44]}")
        else:
            print("  (none)")

        print()
        print("=" * 78)
        print("Document cards are metadata profiles with a verbatim text preview. No document")
        print("was summarized, paraphrased or interpreted; no thesis, sentiment, rating or")
        print("investment view is expressed or implied; nothing was inferred from a file simply")
        print("existing. No external API, no embeddings, no RAG, no OCR. V Drive untouched.")
        print("AI drafts. Humans verify. Cutler decides.")
        print("=" * 78)
    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
