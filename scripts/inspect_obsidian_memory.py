#!/usr/bin/env python3
# =============================================================================
# Cutler Research Brain - Phase 4
# inspect_obsidian_memory.py
#
# Read-only console report over the obsidian_notes table and the vault on disk,
# so a human can see what the Obsidian build produced.
#
# Prints:
#   - total Obsidian notes generated
#   - notes by type
#   - daily note / company page / source type / document type / theme counts
#   - RAG-ready index, Needs Review and OCR candidate status
#   - the vault folder structure as it exists on disk
#   - link health by category: internal document stub links, external card
#     links, and broken links split into document stub / company-ticker /
#     section / other, with examples
#   - sample notes of each kind
#
# Usage:
#   python scripts\inspect_obsidian_memory.py ^
#       --db "data\document_metadata.sqlite" ^
#       --project-root "C:\Users\AI Admin\Documents\CutlerResearchBrain"
#
# Reads only. Never writes to the database, the vault, the cards, the extracted
# text or the V Drive. Nothing here summarizes or interprets any document.
# Principle: AI drafts. Humans verify. Cutler decides.
# =============================================================================

from __future__ import annotations

import argparse
import re
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
VAULT_ROOT_NAME = "05_Obsidian_Brain"

NOTE_TYPE_LABELS = {
    "dashboard": "main dashboard",
    "document_card_stub": "document card stubs (link targets)",
    "daily_research_note": "daily research notes",
    "company_page": "company / ticker pages",
    "source_type_page": "source type pages",
    "document_type_page": "document type pages",
    "strategy_theme_page": "strategy theme pages",
    "rag_ready_index": "RAG-ready index",
    "needs_review": "needs review page",
    "ocr_candidates": "OCR candidates page",
    "document_card_index": "document card index",
    "template": "templates",
}

EXPECTED_FOLDERS = [
    "00_Dashboard", "01_Daily_Research_Notes", "02_Company_Pages",
    "03_Source_Type_Pages", "04_Document_Type_Pages", "05_Strategy_Themes",
    "06_RAG_Ready_Index", "07_Needs_Review", "08_OCR_Candidates",
    "09_Document_Card_Index", "09_Document_Card_Index/Card_Stubs", "10_Templates",
]

STUB_FOLDER = "09_Document_Card_Index/Card_Stubs"
GENERATOR = "build_obsidian_memory.py"

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

# The link audit lives in build_obsidian_memory so the builder, the repair
# scripts and this inspector can never disagree about what counts as broken.
try:
    import build_obsidian_memory as vault_module
except ImportError:  # pragma: no cover - inspector still works without it
    vault_module = None


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


def line(label: str, value, width: int = 38) -> None:
    print(f"  {label:<{width}} {value:>10,}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Cutler Research Brain Phase 4 - inspect the Obsidian memory vault.",
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
        if "obsidian_notes" not in tables:
            print("ERROR: no 'obsidian_notes' table in this database.", file=sys.stderr)
            print("Run scripts/build_obsidian_memory.py first.", file=sys.stderr)
            return 1

        notes = scalar(conn, "SELECT COUNT(*) FROM obsidian_notes")
        vault = (project_root / VAULT_ROOT_NAME) if project_root else None

        print("=" * 78)
        print("CUTLER RESEARCH BRAIN - PHASE 4 OBSIDIAN MEMORY")
        print(f"Database: {db_path.resolve()}")
        if vault:
            print(f"Vault   : {vault}")
        print("=" * 78)

        if notes == 0:
            print("\nNo Obsidian notes yet. Run scripts/build_obsidian_memory.py.")
            return 0

        counts = {t: c for t, c in query(
            conn, "SELECT note_type, COUNT(*) FROM obsidian_notes GROUP BY 1")}
        indexed_rows = scalar(conn, "SELECT COALESCE(MAX(source_row_count),0) FROM obsidian_notes "
                                    "WHERE note_type = 'dashboard'")

        print_header("OVERVIEW")
        line("TOTAL OBSIDIAN NOTES GENERATED", notes)
        line("Document cards indexed by the vault", indexed_rows)

        print_header("NOTES BY TYPE")
        for note_type, label in NOTE_TYPE_LABELS.items():
            count = counts.get(note_type, 0)
            print(f"  {note_type:<24} {count:>8,}   {label}")
        extra = {t: c for t, c in counts.items() if t not in NOTE_TYPE_LABELS}
        for note_type, count in sorted(extra.items(), key=lambda kv: -kv[1]):
            print(f"  {note_type:<24} {count:>8,}   (other)")

        print_header("SECTION COUNTS")
        line("Daily research notes", counts.get("daily_research_note", 0))
        line("Company / ticker pages", counts.get("company_page", 0))
        line("Source type pages", counts.get("source_type_page", 0))
        line("Document type pages", counts.get("document_type_page", 0))
        line("Strategy theme pages", counts.get("strategy_theme_page", 0))
        line("Templates", counts.get("template", 0))

        # ---- key index pages -------------------------------------------------
        print_header("KEY PAGES")
        for note_type, label in (("dashboard", "Dashboard"),
                                 ("rag_ready_index", "RAG-ready index"),
                                 ("needs_review", "Needs Review"),
                                 ("ocr_candidates", "OCR candidates"),
                                 ("document_card_index", "Document card index")):
            row = query(conn, "SELECT note_title, note_path, source_row_count FROM obsidian_notes "
                              "WHERE note_type = ? LIMIT 1", (note_type,))
            if row:
                title, path, count = row[0]
                exists = ""
                if project_root:
                    exists = "  [ok]" if (project_root / path).exists() else "  [MISSING FILE]"
                print(f"  {label:<22} {int(count or 0):>8,} rows   {path}{exists}")
            else:
                print(f"  {label:<22} {'not built':>14}")

        # ---- vault folders on disk -------------------------------------------
        if vault and vault.exists():
            print_header("VAULT FOLDERS ON DISK")
            for folder in EXPECTED_FOLDERS:
                path = vault / folder
                if path.exists():
                    files = sorted(path.glob("*.md"))
                    generated = sum(
                        1 for f in files
                        if "generated_by: build_obsidian_memory.py" in
                        f.read_text(encoding="utf-8", errors="replace")[:400]
                    )
                    other = len(files) - generated
                    suffix = f"   ({other} not generated by this script)" if other else ""
                    print(f"  {folder:<26} {len(files):>6,} notes{suffix}")
                else:
                    print(f"  {folder:<26} {'missing':>12}")

        # ---- Phase 4.1: card stubs and link health ---------------------------
        stub_count = scalar(conn, "SELECT COUNT(*) FROM obsidian_notes "
                                  "WHERE note_type = 'document_card_stub'")
        print_header("DOCUMENT CARD STUBS (Phase 4.1 link targets)")
        line("Card stubs generated", stub_count)
        if vault:
            stub_dir = vault / STUB_FOLDER
            exists = stub_dir.exists()
            print(f"  {'Card_Stubs folder exists':<38} {'yes' if exists else 'NO':>10}")
            if exists:
                on_disk = len(list(stub_dir.glob("*.md")))
                line("Stub notes on disk", on_disk)
                if on_disk != stub_count:
                    print(f"  NOTE: {abs(on_disk - stub_count):,} difference between the database "
                          "and disk - re-run refresh_obsidian_links.py.")
            else:
                print("  Run: python scripts\\refresh_obsidian_links.py --db ... "
                      "--project-root ... --rewrite-links")

        if vault and vault.exists() and vault_module is not None:
            _, broken_rows, health = vault_module.audit_vault_links(vault)

            print_header("LINK HEALTH")
            line("Generated notes scanned", health["notes"])
            line("Wikilinks in generated notes", health["wikilinks"])
            line("Internal document stub links", health["internal_stub"])
            line("External card links (outside vault)", health["external"])
            line("Human-written notes (never touched)", health["human_notes"])
            print()
            line("Broken document stub links", health[vault_module.CATEGORY_STUB])
            line("Broken company/ticker links", health[vault_module.CATEGORY_TICKER])
            line("Broken section links", health[vault_module.CATEGORY_SECTION])
            line("Broken other links", health[vault_module.CATEGORY_OTHER])
            line("TOTAL BROKEN WIKILINKS", health["broken"])

            if health["broken"] == 0 and health["external"] == 0:
                print("\n  Every link in the vault resolves. Obsidian navigation is clean.")

            # ---- examples, grouped by category ---------------------------
            by_category = {}
            for note_path, _note_type, target, _alias, category in broken_rows:
                by_category.setdefault(category, []).append(f"{note_path} -> [[{target}]]")

            for category, examples in by_category.items():
                print(f"\n  {category.upper()} ({len(examples):,}) - examples:")
                for item in examples[:8]:
                    print(f"    {item}")
                if len(examples) > 8:
                    print(f"    ...and {len(examples) - 8:,} more; see "
                          "02_Metadata_Index\\obsidian_broken_wikilink_report.csv")

            if health["external"]:
                print("\n  Fix external card links with:")
                print("    python scripts\\refresh_obsidian_links.py --db ... "
                      "--project-root ... --rewrite-links")
            if health[vault_module.CATEGORY_TICKER] or health[vault_module.CATEGORY_OTHER]:
                print("\n  Fix broken ticker links (they become plain code) with:")
                print("    python scripts\\repair_broken_wikilinks.py --db ... "
                      "--project-root ... --rewrite-links")
            if health[vault_module.CATEGORY_STUB]:
                print("\n  Missing document stubs - create them with:")
                print("    python scripts\\refresh_obsidian_links.py --db ... "
                      "--project-root ... --rewrite-links")
            if health[vault_module.CATEGORY_SECTION]:
                print("\n  A main section page is missing - rebuild the vault with:")
                print("    python scripts\\build_obsidian_memory.py --db ... --project-root ...")
        elif vault and vault.exists():
            print_header("LINK HEALTH")
            print("  build_obsidian_memory.py could not be imported, so the link audit was "
                  "skipped.")

        # ---- samples ----------------------------------------------------------
        print_header(f"SAMPLE OF {args.top} CARD STUBS")
        rows = query(conn, "SELECT note_title, related_value, note_path FROM obsidian_notes "
                           "WHERE note_type = 'document_card_stub' ORDER BY note_title LIMIT ?",
                     (args.top,))
        for title, document_id, path in rows or []:
            print(f"  [[{str(document_id)}]]   {path}")
        if not rows:
            print("  (none)")

        print_header(f"SAMPLE OF {args.top} DAILY NOTES")
        rows = query(conn, "SELECT note_title, source_row_count, note_path FROM obsidian_notes "
                           "WHERE note_type = 'daily_research_note' "
                           "ORDER BY source_row_count DESC LIMIT ?", (args.top,))
        for title, count, path in rows or []:
            print(f"  {str(title):<18} {int(count or 0):>6,} documents   {path}")
        if not rows:
            print("  (none)")

        print_header(f"SAMPLE OF {args.top} COMPANY PAGES")
        rows = query(conn, "SELECT note_title, related_value, source_row_count, note_path "
                           "FROM obsidian_notes WHERE note_type = 'company_page' "
                           "ORDER BY source_row_count DESC LIMIT ?", (args.top,))
        for title, ticker, count, path in rows or []:
            print(f"  {str(title):<12} {int(count or 0):>6,} documents   {path}")
        if not rows:
            print("  (none)")

        print_header(f"SAMPLE OF {args.top} GROUPED PAGES")
        rows = query(conn, "SELECT note_type, note_title, source_row_count FROM obsidian_notes "
                           "WHERE note_type IN ('source_type_page','document_type_page',"
                           "'strategy_theme_page') ORDER BY source_row_count DESC LIMIT ?",
                     (args.top,))
        for note_type, title, count in rows or []:
            print(f"  {note_type:<22} {str(title):<34} {int(count or 0):>6,} documents")
        if not rows:
            print("  (none)")

        print()
        print("=" * 78)
        print("This vault is deterministic metadata memory only. No document was summarized or")
        print("interpreted. No thesis, sentiment, rating or investment view was inferred, and")
        print("nothing was concluded from a file existing. No embeddings, no retrieval, no OCR,")
        print("no external APIs. Only notes generated by the build script are managed; anything")
        print("a human adds to the vault is left alone.")
        print("AI drafts. Humans verify. Cutler decides.")
        print("=" * 78)
    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
