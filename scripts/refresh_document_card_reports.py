#!/usr/bin/env python3
# =============================================================================
# Cutler Research Brain - Phase 3.1
# refresh_document_card_reports.py
#
# Repairs and regenerates Phase 3 document card OUTPUTS from data that already
# exists. It never opens a source PDF, never touches the V Drive, never re-runs
# extraction, and never changes a character of extracted text.
#
# WHY THIS EXISTS
# ---------------
# Phase 3.0 put three very different situations into one bucket called
# "missing_text". Phase 3.1 separates them so a later phase can tell an OCR
# candidate from a genuinely absent file:
#
#   missing_text_file    the expected document_text.txt is not on disk
#   empty_text           extraction ran, but no meaningful text came out
#   scanned_pdf_no_text  the same, and Phase 2 flagged an image-only scan
#
# WHAT EACH FLAG DOES
#   (no flags)       regenerate the reports only - nothing else is touched
#   --fix-reasons    recompute rag_exclusion_reason and usable_for_rag from the
#                    database, the extraction metadata and whether the text file
#                    exists; update document_cards and documents. Card files on
#                    disk are left alone.
#   --rewrite-cards  rewrite document_card.md and document_card.json for cards
#                    that already exist, using the cleaned labels. The preview,
#                    title and headings are reused verbatim from the existing
#                    card - no text is re-derived and nothing is re-interpreted.
#
# The reason logic lives in build_document_cards.py and is imported here, so a
# fresh card build and this repair path can never disagree.
#
# No summarization, no interpretation, no investment conclusions, no OCR, no
# embeddings, no RAG, no Obsidian, no external APIs.
# Principle: AI drafts. Humans verify. Cutler decides.
#
# Usage:
#   python scripts\refresh_document_card_reports.py ^
#       --db "data\document_metadata.sqlite" ^
#       --project-root "C:\Users\AI Admin\Documents\CutlerResearchBrain" ^
#       --fix-reasons --rewrite-cards
# =============================================================================

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

try:
    import build_document_cards as cards  # noqa: E402 - sibling script, loaded by path
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "ERROR: could not import build_document_cards.py from the scripts folder.\n"
        f"  ({exc})"
    ) from exc

LOGGER = logging.getLogger("phase3_card_reports")


# --------------------------------------------------------------------------
# Reason recomputation
# --------------------------------------------------------------------------

def resolve_text_path(row: sqlite3.Row, project_root: Path) -> Path | None:
    """Where this document's extracted text should be, per Phase 2's own record."""
    recorded = cards.value_of(row, "document_text_path")
    if recorded:
        path = Path(recorded)
        if not path.is_absolute():
            path = project_root / path
        return path

    year = cards.safe_component(cards.value_of(row, "archive_year"), "unknown_year")
    document_id = cards.safe_component(cards.value_of(row, "document_id"), "")
    if not document_id:
        return None
    return project_root / "03_Extracted_Text" / year / document_id / "document_text.txt"


def meaningful_chars(text_path: Path | None, fallback_chars: int) -> int:
    """Character count of the cleaned text file, or Phase 2's count if unreadable.

    Counting characters is not reading a document: nothing here inspects what
    the text says.
    """
    if text_path and text_path.exists():
        try:
            raw = text_path.read_text(encoding="utf-8", errors="replace")
            return len(cards.clean_text(raw))
        except OSError:
            return fallback_chars
    return 0


def fix_reasons(conn: sqlite3.Connection, project_root: Path) -> dict:
    """Recompute rag_exclusion_reason / usable_for_rag for every card row."""
    stats = {"checked": 0, "reason_changed": 0, "usable_changed": 0, "legacy_fixed": 0}
    transitions: dict[str, int] = {}

    rows = list(conn.execute(
        """
        SELECT c.document_id, c.card_status, c.card_quality,
               c.usable_for_rag        AS old_usable,
               c.rag_exclusion_reason  AS old_reason,
               c.card_folder, c.card_json_path, c.card_md_path,
               d.file_extension, d.duplicate_status, d.review_severity, d.archive_year,
               COALESCE(d.pdf_extraction_status, '')  AS pdf_extraction_status,
               COALESCE(d.pdf_extraction_quality, '') AS pdf_extraction_quality,
               COALESCE(d.pdf_total_text_chars, 0)    AS pdf_total_text_chars,
               COALESCE(e.scanned_pdf_suspected, 0)   AS scanned_pdf_suspected,
               COALESCE(e.extraction_status, '')      AS e_status,
               COALESCE(e.extraction_quality, '')     AS e_quality,
               COALESCE(e.document_text_path, '')     AS document_text_path
          FROM document_cards c
          LEFT JOIN documents d       ON d.document_id = c.document_id
          LEFT JOIN pdf_extractions e ON e.document_id = c.document_id
        """
    ))

    for row in rows:
        stats["checked"] += 1
        document_id = cards.value_of(row, "document_id")

        # The pdf_extractions table is the authority; the documents mirror is a copy.
        status = cards.value_of(row, "e_status") or cards.value_of(row, "pdf_extraction_status")
        quality = cards.value_of(row, "e_quality") or cards.value_of(row, "pdf_extraction_quality")
        scanned = bool(int(cards.value_of(row, "scanned_pdf_suspected", "0") or 0))

        text_path = resolve_text_path(row, project_root)
        text_exists = bool(text_path and text_path.exists())
        chars = meaningful_chars(text_path, int(cards.value_of(row, "pdf_total_text_chars", "0") or 0))

        usable, reason = cards.assess_rag_readiness(
            status, quality,
            cards.value_of(row, "review_severity"),
            cards.value_of(row, "duplicate_status"),
            scanned,
            meaningful_chars=chars,
            text_file_exists=text_exists,
            file_extension=cards.value_of(row, "file_extension", ".pdf"),
        )

        old_reason = cards.value_of(row, "old_reason") or cards.REASON_NONE
        old_usable = int(cards.value_of(row, "old_usable", "0") or 0)

        if reason != old_reason:
            stats["reason_changed"] += 1
            transitions[f"{old_reason} -> {reason}"] = transitions.get(f"{old_reason} -> {reason}", 0) + 1
            if old_reason == cards.REASON_LEGACY_MISSING_TEXT:
                stats["legacy_fixed"] += 1
        if int(bool(usable)) != old_usable:
            stats["usable_changed"] += 1

        conn.execute(
            "UPDATE document_cards SET rag_exclusion_reason = ?, usable_for_rag = ? "
            "WHERE document_id = ?",
            (reason, 1 if usable else 0, document_id),
        )
        conn.execute(
            "UPDATE documents SET usable_for_rag = ? WHERE document_id = ?",
            (1 if usable else 0, document_id),
        )

    conn.commit()

    LOGGER.info("Reason repair: %d row(s) checked, %d reason(s) changed, "
                "%d usable_for_rag flag(s) changed, %d legacy 'missing_text' row(s) resolved.",
                stats["checked"], stats["reason_changed"], stats["usable_changed"],
                stats["legacy_fixed"])
    for transition, count in sorted(transitions.items(), key=lambda kv: -kv[1]):
        LOGGER.info("    %-52s %6d", transition, count)

    stats["transitions"] = transitions
    return stats


# --------------------------------------------------------------------------
# Card rewriting
# --------------------------------------------------------------------------

def rewrite_cards(conn: sqlite3.Connection, project_root: Path) -> dict:
    """Rewrite existing card files with the cleaned labels.

    The preview, title and heading candidates are copied verbatim out of the
    existing document_card.json. Nothing is re-derived from the source text and
    nothing is re-interpreted - only the label fields change.
    """
    stats = {"rewritten": 0, "skipped_no_file": 0, "failed": 0}

    rows = list(conn.execute(
        """
        SELECT c.document_id, c.card_status, c.card_quality, c.usable_for_rag,
               c.rag_exclusion_reason, c.card_folder, c.card_json_path, c.card_md_path,
               d.file_name, d.relative_path, d.file_path, d.archive_year, d.detected_year,
               d.detected_month, d.folder_date_guess, d.ticker_candidates_clean,
               d.source_vendor_candidates, d.source_type_guess, d.document_type_guess,
               d.strategy_theme_guess, d.review_severity, d.review_category,
               d.sensitive_context_guess, d.duplicate_status,
               COALESCE(e.extraction_status, d.pdf_extraction_status, '')   AS pdf_extraction_status,
               COALESCE(e.extraction_quality, d.pdf_extraction_quality, '') AS pdf_extraction_quality,
               COALESCE(e.page_count, d.pdf_page_count, 0)                  AS pdf_page_count,
               COALESCE(e.total_text_chars, d.pdf_total_text_chars, 0)      AS pdf_total_text_chars,
               COALESCE(e.scanned_pdf_suspected, 0)                         AS scanned_pdf_suspected,
               COALESCE(e.table_heavy_guess, 0)                             AS table_heavy_guess,
               COALESCE(e.output_folder, '')                                AS extraction_output_folder,
               COALESCE(e.document_text_path, '')                           AS document_text_path,
               COALESCE(e.metadata_json_path, '')                           AS metadata_json_path
          FROM document_cards c
          LEFT JOIN documents d       ON d.document_id = c.document_id
          LEFT JOIN pdf_extractions e ON e.document_id = c.document_id
         WHERE c.card_status = ?
        """,
        (cards.CARD_CREATED,),
    ))

    for row in rows:
        document_id = cards.value_of(row, "document_id")
        folder = cards.value_of(row, "card_folder")
        card_dir = Path(folder) if folder else None
        if card_dir and not card_dir.is_absolute():
            card_dir = project_root / card_dir
        if not card_dir:
            year = cards.safe_component(cards.value_of(row, "archive_year"), "unknown_year")
            card_dir = (project_root / "04_Document_Cards" / year /
                        cards.safe_component(document_id, "unknown_document"))

        cards.assert_inside_project(card_dir, project_root)
        json_path = card_dir / "document_card.json"
        md_path = card_dir / "document_card.md"

        if not json_path.exists():
            stats["skipped_no_file"] += 1
            LOGGER.debug("No existing card JSON to rewrite: %s", json_path)
            continue

        try:
            previous = json.loads(json_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            stats["failed"] += 1
            LOGGER.error("Could not read existing card for %s: %s", document_id, exc)
            continue

        try:
            usable = bool(int(cards.value_of(row, "usable_for_rag", "0") or 0))
            card = cards.build_card_json(
                row,
                cards.value_of(row, "card_status") or cards.CARD_CREATED,
                cards.value_of(row, "card_quality") or cards.QUALITY_MEDIUM,
                usable,
                cards.value_of(row, "rag_exclusion_reason") or cards.REASON_NONE,
                previous.get("deterministic_preview", "") or "",
                previous.get("detected_title_guess", "") or "",
                previous.get("detected_heading_candidates", []) or [],
                project_root,
            )
            # Keep the original build timestamp; record the repair separately.
            card["generated_at"] = previous.get("generated_at", card["generated_at"])
            card["labels_refreshed_at"] = datetime.now().replace(microsecond=0).isoformat()
            card["labels_refreshed_by"] = "refresh_document_card_reports.py (Phase 3.1)"

            json_path.write_text(json.dumps(card, indent=2, ensure_ascii=False), encoding="utf-8")
            md_path.write_text(cards.render_card_md(card), encoding="utf-8", errors="replace")
            stats["rewritten"] += 1
        except Exception as exc:  # noqa: BLE001 - one bad card must not stop the run
            stats["failed"] += 1
            LOGGER.error("Rewrite FAILED for %s: %s", document_id, exc)

    LOGGER.info("Card rewrite: %d rewritten, %d had no card file, %d failed.",
                stats["rewritten"], stats["skipped_no_file"], stats["failed"])
    return stats


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Cutler Research Brain Phase 3.1 - repair document card labels and rebuild "
                    "the card reports. Never opens a source PDF.",
    )
    parser.add_argument("--db", required=True,
                        help=r'SQLite index, e.g. "data\document_metadata.sqlite"')
    parser.add_argument("--project-root", required=True,
                        help=r'Project root, e.g. "C:\Users\AI Admin\Documents\CutlerResearchBrain"')
    parser.add_argument("--fix-reasons", action="store_true",
                        help="Recompute rag_exclusion_reason and usable_for_rag in the database.")
    parser.add_argument("--rewrite-cards", action="store_true",
                        help="Rewrite existing document_card.md / .json with the cleaned labels.")
    parser.add_argument("--verbose", action="store_true", help="Chattier console output.")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    project_root = Path(args.project_root).expanduser().resolve()
    cards.validate_project_root(project_root)

    db_path = Path(args.db).expanduser()
    if not db_path.is_absolute():
        db_path = (project_root / db_path).resolve()
    if not db_path.exists():
        raise SystemExit(f"ERROR: database not found: {db_path}")

    log_path = project_root / "logs" / "phase3_document_cards.log"
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
    # The report writers in build_document_cards log through their own logger.
    cards.LOGGER.handlers = LOGGER.handlers
    cards.LOGGER.setLevel(logging.DEBUG)

    LOGGER.info("=" * 78)
    LOGGER.info("Phase 3.1 - document card label repair and report refresh")
    LOGGER.info("Database    : %s", db_path)
    LOGGER.info("Project root: %s", project_root)
    LOGGER.info("Mode        : fix_reasons=%s  rewrite_cards=%s", args.fix_reasons, args.rewrite_cards)
    LOGGER.info("No PDF is opened, no extraction re-runs, no extracted text is changed.")
    LOGGER.info("=" * 78)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "document_cards" not in tables:
            raise SystemExit(
                f"ERROR: no 'document_cards' table in {db_path}\n"
                "Run scripts/build_document_cards.py first."
            )

        reason_stats = fix_reasons(conn, project_root) if args.fix_reasons else {}
        rewrite_stats = rewrite_cards(conn, project_root) if args.rewrite_cards else {}

        if not args.fix_reasons and not args.rewrite_cards:
            LOGGER.info("No repair flags given - regenerating reports only.")

        run_stats = {
            "runtime_seconds": 0.0,
            "considered": reason_stats.get("checked", 0),
            "written": rewrite_stats.get("rewritten", 0),
            "reused": 0,
            "limit": 0,
            "force": args.rewrite_cards,
            "min_quality": "",
            "include_high_review": False,
            "include_duplicates": False,
        }
        totals = cards.write_reports(conn, project_root, run_stats)

        LOGGER.info("-" * 78)
        LOGGER.info("Cards total        : %s", f"{totals['cards']:,}")
        LOGGER.info("Cards created      : %s", f"{totals['created']:,}")
        LOGGER.info("Usable for RAG     : %s", f"{totals['usable']:,}")
        LOGGER.info("Not usable for RAG : %s", f"{totals['not_usable']:,}")
        for reason, count in conn.execute(
            "SELECT COALESCE(rag_exclusion_reason,'none'), COUNT(*) FROM document_cards "
            "WHERE usable_for_rag = 0 GROUP BY 1 ORDER BY COUNT(*) DESC"
        ):
            LOGGER.info("    %-22s %6d   %s", reason, count,
                        cards.REASON_MEANINGS.get(reason, "LEGACY value"))
        LOGGER.info("-" * 78)
        LOGGER.info("Reports refreshed. No PDF was opened, no extraction re-run, no extracted "
                    "text changed, and nothing was summarized or interpreted.")
    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
