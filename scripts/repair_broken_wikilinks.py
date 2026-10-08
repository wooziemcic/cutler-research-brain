#!/usr/bin/env python3
# =============================================================================
# Cutler Research Brain - Phase 4.2
# repair_broken_wikilinks.py
#
# Removes unresolved wikilinks from an existing Obsidian vault, without
# rebuilding it and without inventing notes to satisfy links.
#
# THE PROBLEM
# -----------
# Phase 4.0/4.1 wrote a wikilink for every ticker CANDIDATE it saw:
#
#   [[FLGN]]  [[DOE]]  [[NETL]]  [[BNP]]  [[AXA]]
#
# But company pages are only created for candidates that survive the vendor and
# noise filters and meet --min-ticker-docs. Every other candidate left an
# unresolved link - 377 of them - cluttering the graph view and going nowhere
# when clicked.
#
# THE FIX
# -------
# A link is only a link when its target exists. Broken ticker links become
# inline code (`FLGN`): still visible, still searchable, no longer a dead link.
# Nothing is deleted and no page is fabricated to satisfy a link.
#
# WHAT IS AND IS NOT TOUCHED
#   rewritten  broken company/ticker links, and other broken links that are not
#              document stubs or main section pages
#   left alone document card stub links ([[doc_...|File.pdf]]) - Phase 4.1 owns
#              those, and refresh_obsidian_links.py creates any missing stubs
#   left alone main section links (Dashboard, RAG Ready Documents, Needs Review,
#              OCR Candidates, Document Card Index) - if one of those is broken
#              the vault is incomplete, and rebuilding is the right fix, not
#              de-linking the navigation
#   left alone every human-written note, byte for byte
#
# No PDF is opened, no extracted text is read, nothing is summarized or
# interpreted, no external API is called, and the V Drive is never touched.
#
# Principle: AI drafts. Humans verify. Cutler decides.
#
# Usage:
#   python scripts\repair_broken_wikilinks.py ^
#       --db "data\document_metadata.sqlite" ^
#       --project-root "C:\Users\AI Admin\Documents\CutlerResearchBrain" ^
#       --rewrite-links
# =============================================================================

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime
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

LOGGER = logging.getLogger("phase4_wikilink_repair")

# Categories that are repaired by de-linking.
REPAIRABLE = {vault.CATEGORY_TICKER, vault.CATEGORY_OTHER}
# Categories that are reported but deliberately left as they are.
PRESERVED = {vault.CATEGORY_STUB, vault.CATEGORY_SECTION}


def repair_note_text(text: str, targets: set) -> tuple[str, list[tuple]]:
    """De-link unresolved wikilinks in one note. Returns (text, changes).

    Only links whose target does not exist are touched, and only when the
    category is repairable. Code spans are left alone because Obsidian does not
    resolve links inside them anyway.
    """
    changes: list[tuple] = []

    # Work over the note in segments so links inside code are never rewritten.
    protected_spans = [(m.start(), m.end()) for m in vault.RE_FENCED.finditer(text)]
    protected_spans += [(m.start(), m.end()) for m in vault.RE_INLINE_CODE.finditer(text)]

    def in_code(position: int) -> bool:
        return any(start <= position < end for start, end in protected_spans)

    def replace(match):
        if in_code(match.start()):
            return match.group(0)
        target = (match.group(1) or "").strip()
        alias = (match.group(2) or "").strip()
        if not target or target in targets:
            return match.group(0)

        category = vault.classify_broken(target)
        if category not in REPAIRABLE:
            return match.group(0)  # stub and section links stay as they are

        label = alias or target.rsplit("/", 1)[-1]
        changes.append((target, alias, category, f"`{label}`"))
        return f"`{label}`"

    return vault.RE_WIKILINK.sub(replace, text), changes


def repair_vault(project_root: Path, rewrite: bool) -> tuple[dict, list[tuple]]:
    """Scan every generated note; de-link unresolved links when asked to."""
    vault_root = project_root / vault.VAULT_ROOT_NAME
    stats = {"scanned": 0, "rewritten": 0, "delinked": 0, "preserved": 0,
             "human_skipped": 0, "failed": 0}
    repair_rows: list[tuple] = []

    if not vault_root.exists():
        LOGGER.warning("Vault not found at %s - nothing to repair.", vault_root)
        return stats, repair_rows

    targets = vault.resolvable_targets(vault_root)
    LOGGER.info("Resolvable link targets in the vault: %d", len(targets))
    when = datetime.now().replace(microsecond=0).isoformat()

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
        new_text, changes = repair_note_text(text, targets)
        relative = str(note.relative_to(vault_root)).replace("\\", "/")

        for target, alias, category, replacement in changes:
            repair_rows.append((relative, target, alias, category, replacement,
                                "de-linked" if rewrite else "would de-link", when))

        if changes and rewrite:
            try:
                vault.assert_inside_project(note, project_root)
                note.write_text(new_text, encoding="utf-8", errors="replace")
                stats["rewritten"] += 1
                stats["delinked"] += len(changes)
                LOGGER.debug("Repaired %s (%d link(s) de-linked)", note.name, len(changes))
            except OSError as exc:
                stats["failed"] += 1
                LOGGER.error("Could not write %s: %s", note, exc)
        elif changes:
            stats["delinked"] += len(changes)

    return stats, repair_rows


def write_repair_report(project_root: Path, rows: list[tuple]) -> None:
    path = project_root / "02_Metadata_Index" / "obsidian_link_repair_report.csv"
    vault.assert_inside_project(path, project_root)
    vault.write_csv(path,
                    ["note_path", "broken_target", "link_alias", "category",
                     "replacement", "action", "repaired_at"],
                    rows)


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Cutler Research Brain Phase 4.2 - de-link unresolved wikilinks in the "
                    "Obsidian vault. Document stub links and section links are preserved.",
    )
    parser.add_argument("--db", required=True,
                        help=r'SQLite index, e.g. "data\document_metadata.sqlite"')
    parser.add_argument("--project-root", required=True,
                        help=r'Project root, e.g. "C:\Users\AI Admin\Documents\CutlerResearchBrain"')
    parser.add_argument("--rewrite-links", action="store_true",
                        help="Actually rewrite the notes. Without it this is a dry run.")
    parser.add_argument("--verbose", action="store_true", help="Chattier console output.")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

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
    vault_root = project_root / vault.VAULT_ROOT_NAME

    LOGGER.info("=" * 78)
    LOGGER.info("Phase 4.2 - broken wikilink repair")
    LOGGER.info("Project root: %s", project_root)
    LOGGER.info("Vault       : %s", vault_root)
    LOGGER.info("Mode        : %s", "rewrite" if args.rewrite_links else "DRY RUN (no changes)")
    LOGGER.info("No PDF is opened, no extracted text is read, nothing is summarized.")
    LOGGER.info("=" * 78)

    # ---- before ----------------------------------------------------------
    _, broken_before, totals_before = vault.audit_vault_links(vault_root)
    LOGGER.info("Before: %d broken wikilink(s) - stub %d, ticker %d, section %d, other %d",
                totals_before["broken"], totals_before[vault.CATEGORY_STUB],
                totals_before[vault.CATEGORY_TICKER], totals_before[vault.CATEGORY_SECTION],
                totals_before[vault.CATEGORY_OTHER])
    for row in broken_before[:10]:
        LOGGER.info("    %s -> [[%s]]  (%s)", row[0], row[2], row[4])

    stats, repair_rows = repair_vault(project_root, args.rewrite_links)
    write_repair_report(project_root, repair_rows)

    # ---- after -----------------------------------------------------------
    health = vault.write_link_health_report(
        project_root, project_root / "02_Metadata_Index" / "obsidian_link_health_report.csv")

    LOGGER.info("-" * 78)
    LOGGER.info("Generated notes scanned  : %d", stats["scanned"])
    LOGGER.info("Notes rewritten          : %d", stats["rewritten"])
    LOGGER.info("Links de-linked          : %d", stats["delinked"])
    LOGGER.info("Human notes untouched    : %d", stats["human_skipped"])
    LOGGER.info("")
    LOGGER.info("External card links      : %d  (should be 0)", health["external"])
    LOGGER.info("Broken document stubs    : %d  (should be 0)", health[vault.CATEGORY_STUB])
    LOGGER.info("Broken company/ticker    : %d  (should be 0)", health[vault.CATEGORY_TICKER])
    LOGGER.info("Broken section links     : %d  (should be 0)", health[vault.CATEGORY_SECTION])
    LOGGER.info("Broken other links       : %d", health[vault.CATEGORY_OTHER])
    LOGGER.info("TOTAL broken wikilinks   : %d", health["broken"])
    LOGGER.info("Runtime                  : %.1f seconds", time.time() - started)
    LOGGER.info("-" * 78)

    if not args.rewrite_links and stats["delinked"]:
        LOGGER.info("DRY RUN: %d link(s) would be de-linked. Re-run with --rewrite-links.",
                    stats["delinked"])
    if health[vault.CATEGORY_STUB]:
        LOGGER.warning("Broken document stub links remain. Create the missing stubs with:")
        LOGGER.warning("  python scripts\\refresh_obsidian_links.py --db ... "
                       "--project-root ... --rewrite-links")
    if health[vault.CATEGORY_SECTION]:
        LOGGER.warning("Broken section links remain - a main page is missing. Rebuild with:")
        LOGGER.warning("  python scripts\\build_obsidian_memory.py --db ... --project-root ...")

    LOGGER.info("%s", vault.SAFETY_NOTE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
