#!/usr/bin/env python3
# =============================================================================
# Cutler Research Brain - Phase 4.1
# refresh_obsidian_links.py
#
# Repairs Obsidian navigation in an existing vault without rebuilding it.
#
# THE PROBLEM
# -----------
# Phase 4.0 linked document lists straight at the card files:
#
#   [Some File.pdf](../../04_Document_Cards/2026/doc_abc/document_card.md)
#
# 04_Document_Cards sits OUTSIDE the vault, so Obsidian cannot resolve the link
# and throws "Cannot read properties of null reading 'getParentPrefix'",
# usually followed by "folder already exists".
#
# THE FIX
# -------
# Every document gets a lightweight STUB note inside the vault at
# 09_Document_Card_Index/Card_Stubs/<document_id>.md, and every document link
# becomes an ordinary internal wikilink:
#
#   [[doc_abc|Some File.pdf]]
#
# The real filesystem paths survive as plain text inside the stub, where they
# are informative but not clickable.
#
# WHAT EACH FLAG DOES
#   (no flags)       create/update the stubs and rebuild the reports
#   --rewrite-links  also rewrite existing generated notes, converting external
#                    card links into internal wikilinks
#
# SAFETY
#   - only notes carrying `generated_by: build_obsidian_memory.py` are touched;
#     anything a human wrote in the vault is read past and left byte-identical
#   - no source PDF is opened, no extracted text is read or copied
#   - nothing is written outside the project root, and never to the V Drive
#   - no summaries, no interpretation, no APIs, no embeddings, no RAG, no OCR
#
# Principle: AI drafts. Humans verify. Cutler decides.
#
# Usage:
#   python scripts\refresh_obsidian_links.py ^
#       --db "data\document_metadata.sqlite" ^
#       --project-root "C:\Users\AI Admin\Documents\CutlerResearchBrain" ^
#       --rewrite-links
# =============================================================================

from __future__ import annotations

import argparse
import logging
import re
import sqlite3
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

try:
    import build_obsidian_memory as vault  # noqa: E402 - sibling script, loaded by path
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "ERROR: could not import build_obsidian_memory.py from the scripts folder.\n"
        f"  ({exc})"
    ) from exc

LOGGER = logging.getLogger("phase4_obsidian_links")

# [label](any/path/04_Document_Cards/<year>/<document_id>/document_card.md)
RE_CARD_LINK = re.compile(
    r"\[([^\]]*)\]\(([^)]*04_Document_Cards/[^)]*?/(doc_[A-Za-z0-9]+)/document_card\.md)\)"
)
# Any other markdown link that points into 04_Document_Cards (folder links, etc.)
RE_OTHER_CARD_LINK = re.compile(r"\[([^\]]*)\]\(([^)]*04_Document_Cards[^)]*)\)")


# --------------------------------------------------------------------------
# Stub creation
# --------------------------------------------------------------------------

class SimpleWriter(vault.VaultWriter):
    """VaultWriter with the same behaviour, logging through this script."""


def build_stubs(conn: sqlite3.Connection, project_root: Path, args) -> tuple[int, dict]:
    """Create or update one stub note per document card."""
    vault_root = project_root / vault.VAULT_ROOT_NAME
    stub_dir = vault_root / vault.FOLDERS["card_stubs"]
    vault.assert_inside_project(stub_dir, project_root)
    stub_dir.mkdir(parents=True, exist_ok=True)

    rows = vault.load_rows(conn, args)
    LOGGER.info("Document cards to stub: %d", len(rows))

    writer = SimpleWriter(project_root, vault_root, conn, force=False)
    vault.build_card_stubs(writer, rows, project_root)
    conn.commit()

    LOGGER.info("Stub notes: %d written, %d already current, %d failed.",
                writer.written, writer.unchanged, writer.failed)
    return len(rows), {"written": writer.written, "unchanged": writer.unchanged,
                       "failed": writer.failed}


# --------------------------------------------------------------------------
# Link rewriting
# --------------------------------------------------------------------------

def rewrite_note(text: str) -> tuple[str, int, int]:
    """Convert external card links in one note. Returns (text, converted, delinked)."""
    converted = 0
    delinked = 0

    def to_wikilink(match: re.Match) -> str:
        nonlocal converted
        label = vault.link_label(match.group(1), match.group(3))
        converted += 1
        return f"[[{match.group(3)}|{label}]]"

    text = RE_CARD_LINK.sub(to_wikilink, text)

    def to_plain(match: re.Match) -> str:
        """A link into 04_Document_Cards that is not a card file (a folder, say).

        It cannot be a wikilink because there is no note to point at, so it
        becomes plain code text - informative, and no longer a broken link.
        """
        nonlocal delinked
        delinked += 1
        return f"{match.group(1)} `{match.group(2)}`".strip()

    text = RE_OTHER_CARD_LINK.sub(to_plain, text)
    return text, converted, delinked


def rewrite_links(project_root: Path) -> dict:
    """Rewrite every GENERATED note in the vault. Human notes are never touched."""
    vault_root = project_root / vault.VAULT_ROOT_NAME
    stats = {"scanned": 0, "rewritten": 0, "converted": 0, "delinked": 0,
             "human_skipped": 0, "failed": 0}

    if not vault_root.exists():
        LOGGER.warning("Vault not found at %s - nothing to rewrite.", vault_root)
        return stats

    for note in sorted(vault_root.rglob("*.md")):
        try:
            text = note.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            stats["failed"] += 1
            LOGGER.error("Could not read %s: %s", note, exc)
            continue

        generated = vault.RE_GENERATED_BY.search(text[:600])
        if not generated or vault.GENERATOR not in generated.group(1):
            stats["human_skipped"] += 1
            LOGGER.debug("Human-written note left alone: %s", note.name)
            continue

        stats["scanned"] += 1
        new_text, converted, delinked = rewrite_note(text)
        if converted or delinked:
            try:
                vault.assert_inside_project(note, project_root)
                note.write_text(new_text, encoding="utf-8", errors="replace")
                stats["rewritten"] += 1
                stats["converted"] += converted
                stats["delinked"] += delinked
                LOGGER.debug("Rewrote %s (%d wikilinks, %d de-linked paths)",
                             note.name, converted, delinked)
            except OSError as exc:
                stats["failed"] += 1
                LOGGER.error("Could not write %s: %s", note, exc)

    LOGGER.info("Link rewrite: %d generated note(s) scanned, %d rewritten, "
                "%d card link(s) converted to wikilinks, %d path link(s) de-linked, "
                "%d human note(s) left untouched.",
                stats["scanned"], stats["rewritten"], stats["converted"],
                stats["delinked"], stats["human_skipped"])
    return stats


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Cutler Research Brain Phase 4.1 - create document card stub notes and "
                    "repoint Obsidian document links inside the vault.",
    )
    parser.add_argument("--db", required=True,
                        help=r'SQLite index, e.g. "data\document_metadata.sqlite"')
    parser.add_argument("--project-root", required=True,
                        help=r'Project root, e.g. "C:\Users\AI Admin\Documents\CutlerResearchBrain"')
    parser.add_argument("--rewrite-links", action="store_true",
                        help="Rewrite existing generated notes to use internal stub wikilinks.")
    parser.add_argument("--limit", type=int, default=0,
                        help="Only stub the first N document cards (0 = all).")
    parser.add_argument("--year", default="", help="Only documents from this archive year.")
    parser.add_argument("--only-date", default="", help="Only documents with this folder date.")
    parser.add_argument("--only-ticker", default="", help="Only documents with this ticker candidate.")
    parser.add_argument("--verbose", action="store_true", help="Chattier console output.")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    args.force = False
    args.min_ticker_docs = 1

    project_root = Path(args.project_root).expanduser().resolve()
    vault.validate_project_root(project_root)

    db_path = Path(args.db).expanduser()
    if not db_path.is_absolute():
        db_path = (project_root / db_path).resolve()
    if not db_path.exists():
        raise SystemExit(f"ERROR: database not found: {db_path}")

    log_path = project_root / "logs" / "phase4_obsidian_memory.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    LOGGER.setLevel(logging.DEBUG)
    LOGGER.handlers.clear()
    file_handler = logging.FileHandler(log_path, mode="a", encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-8s  %(message)s",
                                                "%Y-%m-%d %H:%M:%S"))
    LOGGER.addHandler(file_handler)
    console = logging.StreamHandler(stream=sys.stdout)
    console.setLevel(logging.DEBUG if args.verbose else logging.INFO)
    console.setFormatter(logging.Formatter("%(levelname)-8s %(message)s"))
    LOGGER.addHandler(console)
    vault.LOGGER.handlers = LOGGER.handlers
    vault.LOGGER.setLevel(logging.DEBUG)

    started = time.time()
    LOGGER.info("=" * 78)
    LOGGER.info("Phase 4.1 - Obsidian link repair")
    LOGGER.info("Database    : %s", db_path)
    LOGGER.info("Project root: %s", project_root)
    LOGGER.info("Mode        : rewrite_links=%s", args.rewrite_links)
    LOGGER.info("No PDF is opened, no extracted text is read, nothing is summarized.")
    LOGGER.info("=" * 78)

    conn = vault.connect(db_path)
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "document_cards" not in tables:
            raise SystemExit("ERROR: no 'document_cards' table. Run Phase 3 first.")

        vault.create_note_schema(conn)

        card_count, stub_stats = build_stubs(conn, project_root, args)
        rewrite_stats = rewrite_links(project_root) if args.rewrite_links else {}
        if not args.rewrite_links:
            LOGGER.info("--rewrite-links not given: stubs were created/updated, existing notes "
                        "were left as they are.")

        rows = vault.load_rows(conn, args)
        totals = vault.collect_totals(conn)
        run_stats = {
            "runtime_seconds": time.time() - started,
            "written": stub_stats.get("written", 0) + rewrite_stats.get("rewritten", 0),
            "unchanged": stub_stats.get("unchanged", 0),
            "failed": stub_stats.get("failed", 0) + rewrite_stats.get("failed", 0),
            "tickers_skipped": 0,
            "limit": args.limit,
            "year": args.year,
            "only_date": args.only_date,
            "only_ticker": args.only_ticker,
            "force": False,
        }
        vault.write_reports(conn, project_root, rows, totals, run_stats)
        conn.commit()

        health = vault.write_link_health_report(
            project_root, project_root / "02_Metadata_Index" / "obsidian_link_health_report.csv")

        LOGGER.info("-" * 78)
        LOGGER.info("Document cards stubbed : %d", card_count)
        LOGGER.info("Stub notes written     : %d (%d already current)",
                    stub_stats.get("written", 0), stub_stats.get("unchanged", 0))
        if args.rewrite_links:
            LOGGER.info("Notes rewritten        : %d", rewrite_stats.get("rewritten", 0))
            LOGGER.info("Card links converted   : %d", rewrite_stats.get("converted", 0))
            LOGGER.info("Path links de-linked   : %d", rewrite_stats.get("delinked", 0))
        LOGGER.info("Internal stub links    : %d", health.get("internal_stub", 0))
        LOGGER.info("External card links    : %d  (should be 0)", health.get("external", 0))
        LOGGER.info("Broken wikilinks       : %d  (should be 0)", health.get("broken", 0))
        LOGGER.info("Human notes untouched  : %d", health.get("human_notes", 0))
        LOGGER.info("-" * 78)
        LOGGER.info("%s", vault.SAFETY_NOTE)
    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
