#!/usr/bin/env python3
# =============================================================================
# Cutler Research Brain - Phase 1.2
# scan_source_archive.py
#
# READ-ONLY scanner for the Cutler research archive on the V Drive.
#
# What it does:
#   1. Walks a source folder recursively (never writing anything to it).
#   2. Collects file system metadata for every supported document.
#   3. Computes a SHA-256 hash of each file (used for duplicate detection).
#   4. Makes conservative GUESSES about dates, ticker candidates, research
#      source/vendor, document type and strategy theme from the PATH ONLY.
#   5. Grades every row for human review (severity + category).
#   6. Writes a raw record file (data/raw_scan.jsonl), then hands off to
#      build_metadata_index.py to build the SQLite index and the reports.
#
# PHASE 1.1 CHANGES (metadata quality)
#   - detected_year now means the ARCHIVE / FOLDER year, not any year that
#     happens to appear in a file name. Bond maturities like 2029 or 2036 can
#     no longer become detected_year.
#   - New year columns: archive_year, archive_month, folder_year, folder_month,
#     filename_year_mentions, possible_maturity_years.
#   - Tickers split into ticker_candidates_raw / ticker_candidates_clean /
#     source_vendor_candidates, plus ticker_confidence.
#   - needs_review is graded: review_severity (High/Medium/Low/None) and
#     review_category (semicolon-separated).
#
# PHASE 1.2 CHANGES (metadata QA)
#   - detected_year is now clamped to the plausible window and resolved from the
#     folder date -> archive path year -> folder name -> file name. A maturity
#     year in a FOLDER name (the last leak) can no longer win. New columns
#     date_confidence and detected_year_source record how the year was reached.
#   - Sensitive-data flagging is two-tier: strong restricted phrases stay High,
#     ordinary public-company language drops to Low. New columns
#     sensitive_keyword_hits and sensitive_context_guess.
#   - Source and document types fall back to the archive's folder structure,
#     which is what cuts the Unknown counts.
#   - Ticker noise: BI / QT / WF / CNN default to research vendors, AI is
#     treated as a theme word in technology context, and a protected list keeps
#     real Cutler names safe.
#
# What it deliberately does NOT do (Phase 1 scope):
#   - open or parse document contents
#   - extract PDF text
#   - summarize anything
#   - call OpenAI, Claude or any external API
#   - create embeddings or a vector index
#   - make investment conclusions
#
# Principle: AI drafts. Humans verify. Cutler decides.
#
# Usage:
#   python scripts\scan_source_archive.py ^
#       --source "V:\Companies\A Current Month\2026" ^
#       --project-root "C:\Users\AI Admin\Documents\CutlerResearchBrain"
# =============================================================================

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent

SUPPORTED_EXTENSIONS = {
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".xlsm",
    ".csv",
    ".txt",
    ".md",
    ".ppt",
    ".pptx",
}

SKIP_DIRS = {
    ".pytest_cache",
    "outputs",
    "__pycache__",
    ".git",
    "node_modules",
}

# Read files in 1 MB chunks when hashing so huge files never blow up memory.
HASH_CHUNK_BYTES = 1024 * 1024

# Review severity ordering, worst first.
SEVERITY_ORDER = ["High", "Medium", "Low", "None"]

DEFAULT_SEVERITY_MAP = {
    "sensitive_data": "High",
    "scan_error": "High",
    "possible_sensitive_data": "Medium",
    "duplicate": "Medium",
    "no_ticker_on_research": "Medium",
    "public_language_sensitive": "Low",
    "unknown_source_type": "Low",
    "unknown_document_type": "Low",
}

CATEGORY_REASONS = {
    "sensitive_data": "restricted-data language in path",
    "scan_error": "error reading metadata or hashing the file",
    "possible_sensitive_data": "sensitive word in path with no explaining context",
    "duplicate": "duplicate of another file",
    "no_ticker_on_research": "no ticker candidate on a research-looking document",
    "public_language_sensitive": "sensitive word used as ordinary company language",
    "unknown_source_type": "source_type_guess is Unknown",
    "unknown_document_type": "document_type_guess is Unknown",
}

# sensitive_context_guess values
CONTEXT_RESTRICTED = "possible_restricted_data"
CONTEXT_PUBLIC = "public_company_language"
CONTEXT_RESEARCH = "research_business_language"
CONTEXT_MANUAL = "needs_manual_check"
CONTEXT_NONE = "none"

LOGGER = logging.getLogger("phase1_scan")


# --------------------------------------------------------------------------
# Fallback classification rules
# --------------------------------------------------------------------------
# configs/*.yaml is the source of truth. These built-in defaults exist only so
# the scanner still runs (with a loud warning) if PyYAML is not installed or a
# config file is missing. Keep them minimal on purpose - if you are tuning
# rules, tune the YAML.
# --------------------------------------------------------------------------

FALLBACK_SOURCE_RULES = {
    "source_types": [
        {"name": "Template", "keywords": ["template", "checklist", "worksheet"]},
        {"name": "Green Street Research", "keywords": ["green street", "greenstreet"]},
        {"name": "Short Sale Research", "keywords": ["short sale", "short report", "short thesis"]},
        {"name": "SEC Filing", "keywords": ["10-k", "10-q", "8-k", "proxy statement", "edgar", "prospectus"]},
        {"name": "Company Press Release", "keywords": ["press release", "earnings release", "announces"]},
        {"name": "Convertible Research", "keywords": ["convertible", "exchangeable", "converts"]},
        {"name": "Credit Research", "keywords": ["credit note", "credit research", "high yield", "covenant"]},
        {"name": "REIT Research", "keywords": ["reit", "real estate", "net lease"]},
        {"name": "Bank Research", "keywords": ["bank", "bancorp", "bancshares", "thrift"]},
        {"name": "Market Monitor", "keywords": ["monitor", "market update", "recap", "tracker"]},
        {"name": "Industry Research", "keywords": ["industry", "sector", "primer", "outlook"]},
        {"name": "Sell-Side Research", "keywords": ["research note", "initiation", "sell-side", "analyst"]},
    ],
    "source_type_extension_defaults": {},
    "document_types": [
        {"name": "Template", "keywords": ["template", "checklist", "worksheet"]},
        {"name": "10-K", "keywords": ["10-k", "10k", "annual report"]},
        {"name": "10-Q", "keywords": ["10-q", "10q", "quarterly report"]},
        {"name": "8-K", "keywords": ["8-k", "8k"]},
        {"name": "Press Release", "keywords": ["press release", "earnings release", "announces"]},
        {"name": "Investor Presentation", "keywords": ["presentation", "investor deck", "roadshow"]},
        {"name": "Convertible Note", "keywords": ["convertible", "exchangeable"]},
        {"name": "Credit Note", "keywords": ["credit note", "credit memo", "credit research"]},
        {"name": "Research Note", "keywords": ["research", "note", "memo", "update", "thesis", "report"]},
    ],
    "document_type_extension_overrides": {
        ".xls": "Spreadsheet",
        ".xlsx": "Spreadsheet",
        ".xlsm": "Spreadsheet",
        ".csv": "Spreadsheet",
    },
    "document_type_extension_defaults": {
        ".ppt": "Investor Presentation",
        ".pptx": "Investor Presentation",
    },
    "strategy_themes": [
        {"name": "Short Sale Research", "keywords": ["short sale", "short report", "short thesis"]},
        {"name": "Convertibles and Exchangeables", "keywords": ["convertible", "exchangeable", "converts"]},
        {"name": "Community Banks", "keywords": ["bank", "bancorp", "bancshares", "thrift"]},
        {"name": "REITs and Real Estate", "keywords": ["reit", "real estate", "net lease", "cap rate"]},
        {"name": "M&A and Strategic Transactions", "keywords": ["merger", "acquisition", "m&a", "tender offer"]},
        {"name": "Healthcare Catalysts", "keywords": ["pharma", "biotech", "fda", "clinical"]},
        {"name": "Energy and Commodities", "keywords": ["energy", "oil", "natural gas", "uranium"]},
        {"name": "Retail and Consumer", "keywords": ["retail", "consumer", "restaurant"]},
        {"name": "Technology and AI Infrastructure", "keywords": ["semiconductor", "software", "data center"]},
        {"name": "Credit and Capital Structure", "keywords": ["credit", "bond", "debt", "restructuring"]},
        {"name": "Market Sentiment and Monitors", "keywords": ["sentiment", "monitor", "recap", "positioning"]},
    ],
    "strategy_theme_extension_defaults": {},
    "sensitive_keywords": [
        "client", "clients", "portfolio", "portfolios", "holding", "holdings",
        "account", "accounts", "performance", "allocation", "position",
        "positions", "aum", "nav", "custody", "statement", "blotter", "pnl",
    ],
    # ---- Phase 1.2 fallbacks ------------------------------------------
    "sensitive_strong_phrases": [
        "client account", "client portfolio", "client holdings", "portfolio holdings",
        "holdings detail", "account statement", "portfolio performance",
        "performance report", "model portfolio", "asset allocation",
        "portfolio allocation", "portfolio review", "trade blotter",
        "capital account", "separately managed", "managed account",
    ],
    "sensitive_weak_keywords": [
        "client", "clients", "portfolio", "portfolios", "holding", "holdings",
        "account", "accounts", "performance", "allocation", "position",
        "positions", "aum", "nav", "custody", "statement", "statements",
        "blotter", "pnl",
    ],
    "public_language_phrases": [
        "expands portfolio", "product portfolio", "loan portfolio",
        "portfolio company", "financial statement", "financial statements",
        "income statement", "proxy statement", "performance-based",
        "performance based", "stock performance", "operating performance",
        "market position", "competitive position", "capital position",
        "capital allocation", "positioned for", "deposit accounts",
    ],
    "research_business_phrases": [
        "research note", "press release", "earnings release", "10-k", "10-q",
        "8-k", "investor presentation", "transcript", "market monitor",
        "sector", "industry", "green street",
    ],
    "source_type_folder_rules": [
        {"folders": ["green street", "greenstreet"], "source_type": "Green Street Research"},
        {"folders": ["converts", "convertibles", "exchangeables"], "source_type": "Convertible Research"},
        {"folders": ["reits", "reit", "real estate"], "source_type": "REIT Research"},
        {"folders": ["credit", "bonds", "fixed income"], "source_type": "Credit Research"},
        {"folders": ["banks", "bank", "community banks"], "source_type": "Bank Research"},
        {"folders": ["short sale", "shorts", "short ideas"], "source_type": "Short Sale Research"},
        {"folders": ["monitors", "monitor"], "source_type": "Market Monitor"},
        {"folders": ["research", "sell side", "sell-side"], "source_type": "Sell-Side Research",
         "industry_alternate": "Industry Research"},
        {"folders": ["industry", "sectors", "sector"], "source_type": "Industry Research"},
        {"folders": ["companies", "company", "names"], "source_type": "Company Material"},
        {"folders": ["templates", "template"], "source_type": "Template"},
    ],
    "document_type_folder_rules": [
        {"folders": ["companies", "company", "names"], "document_type": "Company Material"},
        {"folders": ["templates", "template"], "document_type": "Template"},
        {"folders": ["converts", "convertibles"], "document_type": "Convertible Note"},
        {"folders": ["credit", "bonds"], "document_type": "Credit Note"},
        {"folders": ["research", "monitors", "banks", "reits", "industry"],
         "document_type": "Research Note"},
    ],
    "strong_filename_signals": {
        "template": ["template", "blank form", "checklist", "worksheet"],
        "sec_filing": ["10-k", "10k", "10-q", "10q", "8-k", "8k", "s-1", "424b",
                       "def 14a", "13d", "13g", "proxy statement", "prospectus"],
        "press_release": ["press release", "news release", "earnings release",
                          "announces", "announcement"],
        "research_note": ["research note", "initiation", "initiating coverage"],
    },
    "industry_filename_signals": [
        "sector", "industry", "primer", "landscape", "outlook", "survey", "monitor",
    ],
    "earnings_filename_signals": [
        "earnings", "results", "quarterly", "annual report", "financial statement",
        "financial statements", "financial results",
    ],
    "research_looking_source_types": [
        "Sell-Side Research", "Credit Research", "Convertible Research",
        "REIT Research", "Bank Research", "Green Street Research",
        "Short Sale Research", "SEC Filing", "Company Press Release",
    ],
    "research_looking_document_types": [
        "Research Note", "8-K", "10-K", "10-Q", "Press Release",
        "Investor Presentation", "Credit Note", "Convertible Note",
    ],
    "security_context_keywords": [
        "convertible", "convert", "converts", "exchangeable", "note", "notes",
        "bond", "bonds", "debenture", "due", "maturity", "coupon", "senior",
        "subordinated", "preferred", "indenture", "%",
    ],
    "maturity_year_lookahead": 1,
    "min_plausible_year": 1990,
    "max_plausible_year_offset": 1,
    "review_severity_map": dict(DEFAULT_SEVERITY_MAP),
    "review_category_order": [
        "sensitive_data", "scan_error", "duplicate", "no_ticker_on_research",
        "unknown_source_type", "unknown_document_type",
    ],
}

FALLBACK_TICKER_RULES = {
    "min_length": 1,
    "max_length": 5,
    "max_candidates_per_file": 5,
    "max_raw_candidates_per_file": 10,
    "skip_all_caps_names": True,
    "all_caps_token_threshold": 4,
    "scan_parent_folder": False,
    "exclusions": [
        "PDF", "DOC", "DOCX", "XLS", "XLSX", "XLSM", "PPT", "PPTX", "CSV",
        "TXT", "MD", "LLC", "INC", "CORP", "THE", "AND", "FOR", "NEW", "OLD",
        "USE", "THIS", "ONE", "NOTE", "REPORT", "TEMPLATE", "RESEARCH",
        "CREDIT", "BANK", "BANKS", "REIT", "REITS", "JUNE", "JULY", "AUG",
        "SEP", "SEPT", "OCT", "NOV", "DEC", "JAN", "FEB", "MAR", "APR", "MAY",
    ],
    "vendor_tokens": [
        "JPM", "GS", "MS", "UBS", "RBC", "DB", "WF", "WFC", "BAML", "BOFA",
        "CITI", "CS", "BARC", "BARCLAYS", "DEUTSCHE", "WELLS", "FARGO",
        "MORGAN", "STANLEY", "GOLDMAN", "KBW", "JEFF", "STIFEL", "PIPER",
        "BTIG", "BI", "BBG", "CNN", "QT", "SNL", "FITCH", "MOODYS",
    ],
    "vendor_phrases": [
        "j.p. morgan", "jp morgan", "goldman sachs", "morgan stanley",
        "wells fargo", "deutsche bank", "credit suisse", "barclays",
        "raymond james", "green street", "bloomberg intelligence",
    ],
    "source_context_keywords": [
        "research", "note", "initiation", "coverage", "estimates", "rating",
        "analyst", "sell-side", "sellside", "morning", "daily", "weekly",
    ],
    "source_position_rules": {
        "after_dash_separator": True,
        "in_last_two_tokens": True,
        "research_context_in_path": True,
    },
    # ---- Phase 1.2 fallbacks ------------------------------------------
    "strong_vendor_tokens": ["BI", "QT", "WF", "CNN", "CNBC", "WSJ", "BBG", "SNL", "GSA"],
    "strong_vendor_first_token_exception": True,
    "company_context_keywords": [
        "earnings", "results", "10-k", "10-q", "8-k", "press release", "investor",
        "dividend", "guidance", "acquisition", "merger", "quarterly", "annual",
    ],
    "never_ticker_tokens": ["CNN", "CNBC", "WSJ", "BARRONS", "REUTERS", "BBG"],
    "theme_tokens": {
        "AI": ["artificial intelligence", "agentic", "ai infrastructure",
               "ai capabilities", "machine learning", "data center", "gpu",
               "generative", "llm", "hyperscaler", "semiconductor"],
        "EV": ["electric vehicle", "ev charging"],
        "ESG": ["sustainability", "climate"],
    },
    "protected_tickers": [
        "MSFT", "AMZN", "AAPL", "GOOGL", "META", "NVDA", "KR", "ACI", "VICI",
        "KIM", "ABBV", "PFE", "CVX", "XOM", "HBT", "LUMN",
    ],
}


# --------------------------------------------------------------------------
# Logging
# --------------------------------------------------------------------------

def setup_logging(log_path: Path, verbose: bool = False) -> None:
    """Log to both the console and logs/phase1_scan.log."""
    log_path.parent.mkdir(parents=True, exist_ok=True)

    LOGGER.setLevel(logging.DEBUG)
    LOGGER.handlers.clear()

    fmt = logging.Formatter("%(asctime)s  %(levelname)-8s  %(message)s", "%Y-%m-%d %H:%M:%S")

    file_handler = logging.FileHandler(log_path, mode="a", encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(fmt)
    LOGGER.addHandler(file_handler)

    console = logging.StreamHandler(stream=sys.stdout)
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    console.setFormatter(logging.Formatter("%(levelname)-8s %(message)s"))
    LOGGER.addHandler(console)


# --------------------------------------------------------------------------
# Config loading
# --------------------------------------------------------------------------

def load_yaml_config(path: Path, fallback: dict, label: str) -> tuple[dict, str]:
    """Load a YAML config file, falling back to built-in rules if we cannot.

    Returns (rules_dict, source_description).
    """
    try:
        import yaml  # type: ignore
    except ImportError:
        LOGGER.warning(
            "PyYAML is not installed - using BUILT-IN fallback rules for %s. "
            "Run: pip install -r requirements.txt", label
        )
        return dict(fallback), "builtin-fallback (PyYAML missing)"

    if not path.exists():
        LOGGER.warning("Config not found: %s - using BUILT-IN fallback rules for %s.", path, label)
        return dict(fallback), f"builtin-fallback (missing {path.name})"

    try:
        with path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        if not isinstance(data, dict):
            raise ValueError("top-level YAML structure must be a mapping")
        # Fill in any keys the file omitted so downstream code never KeyErrors.
        merged = dict(fallback)
        merged.update(data)
        LOGGER.info("Loaded %s rules from %s", label, path)
        return merged, str(path)
    except Exception as exc:  # noqa: BLE001 - never let a config problem kill the scan
        LOGGER.error("Could not parse %s (%s) - using BUILT-IN fallback rules.", path, exc)
        return dict(fallback), f"builtin-fallback (parse error in {path.name})"


# --------------------------------------------------------------------------
# Path helpers
# --------------------------------------------------------------------------

def os_path(path: Path) -> str:
    """Return a string path safe for long Windows paths (>260 chars).

    On Windows we prefix with the extended-length marker so os.stat() and
    open() keep working deep inside the archive. No-op on other platforms.
    """
    raw = str(path)
    if os.name != "nt":
        return raw
    if raw.startswith("\\\\?\\"):
        return raw
    if len(raw) < 240:
        return raw
    if raw.startswith("\\\\"):  # UNC path, e.g. \\server\share\...
        return "\\\\?\\UNC\\" + raw[2:]
    return "\\\\?\\" + raw


def is_relative_to(child: Path, parent: Path) -> bool:
    """Path.is_relative_to() equivalent that also works on Python 3.8."""
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except (ValueError, OSError):
        return False


def validate_paths(source: Path, project_root: Path) -> None:
    """Safety gate. Refuse to run if the setup could write to the archive.

    Rules enforced here:
      - the source folder must exist and be a directory
      - the project root must NOT live inside the source archive
      - the project root must NOT be on the read-only V Drive
      - the source must NOT live inside the project root
    """
    if not source.exists() or not source.is_dir():
        raise SystemExit(f"ERROR: source folder does not exist or is not a directory: {source}")

    if is_relative_to(project_root, source):
        raise SystemExit(
            "ERROR: --project-root is inside --source. Outputs would be written to the "
            "read-only research archive. Aborting.\n"
            f"  source       = {source}\n"
            f"  project-root = {project_root}"
        )

    if is_relative_to(source, project_root):
        raise SystemExit(
            "ERROR: --source is inside --project-root. Refusing to scan the project's own "
            "outputs as if they were the archive.\n"
            f"  source       = {source}\n"
            f"  project-root = {project_root}"
        )

    # Explicit V Drive guard: nothing we generate may land on the archive drive.
    project_drive = os.path.splitdrive(str(project_root.resolve()))[0].upper().rstrip(":")
    if project_drive == "V":
        raise SystemExit(
            f"ERROR: --project-root is on the V Drive ({project_root}). "
            "The V Drive is read-only. Aborting."
        )

    source_drive = os.path.splitdrive(str(source.resolve()))[0].upper().rstrip(":")
    if source_drive and project_drive and source_drive == project_drive and source_drive == "V":
        raise SystemExit("ERROR: outputs cannot be written to the source drive. Aborting.")


def assert_output_is_safe(out_path: Path, source: Path, project_root: Path) -> None:
    """Belt and braces: every output path must sit inside the project root."""
    if not is_relative_to(out_path, project_root):
        raise SystemExit(f"ERROR: refusing to write outside the project root: {out_path}")
    if is_relative_to(out_path, source):
        raise SystemExit(f"ERROR: refusing to write inside the source archive: {out_path}")


# --------------------------------------------------------------------------
# Date detection
# --------------------------------------------------------------------------

MONTH_NAMES = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}

# 6.22.2026 / 06-22-2026 / 6_22_26  -> month, day, year
RE_MDY = re.compile(r"(?<!\d)(\d{1,2})[.\-_](\d{1,2})[.\-_](\d{2}|\d{4})(?!\d)")
# 2026-06-22 / 2026.06.22
RE_YMD = re.compile(r"(?<!\d)(19|20)(\d{2})[.\-_](\d{1,2})[.\-_](\d{1,2})(?!\d)")
# 20260622
RE_YMD_COMPACT = re.compile(r"(?<!\d)((?:19|20)\d{2})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])(?!\d)")
# Bare year anywhere in a path segment
RE_YEAR = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
# A path segment that is exactly a year, e.g. the "2026" archive folder
RE_YEAR_ONLY = re.compile(r"^\s*((?:19|20)\d{2})\s*$")
# "June 2026", "Jun 2026"
RE_MONTH_NAME = re.compile(
    r"(?<![a-z])(jan|january|feb|february|mar|march|apr|april|may|jun|june|jul|july|"
    r"aug|august|sep|sept|september|oct|october|nov|november|dec|december)(?![a-z])"
)
# "2026-06" style month folders
RE_YEAR_MONTH = re.compile(r"(?<!\d)((?:19|20)\d{2})[.\-_](0?[1-9]|1[0-2])(?!\d)")
# A folder that is just a month number, e.g. "06"
RE_MONTH_ONLY = re.compile(r"^\s*(0?[1-9]|1[0-2])\s*$")


def _valid_date(year: int, month: int, day: int) -> str | None:
    """Return YYYY-MM-DD if this is a plausible archive date, else None."""
    if not (1990 <= year <= 2100):
        return None
    if not (1 <= month <= 12):
        return None
    if not (1 <= day <= 31):
        return None
    try:
        return datetime(year, month, day).strftime("%Y-%m-%d")
    except ValueError:
        return None


def find_date_in_text(text: str) -> str | None:
    """Find the first plausible full date in a single path segment."""
    m = RE_YMD.search(text)
    if m:
        year = int(m.group(1) + m.group(2))
        found = _valid_date(year, int(m.group(3)), int(m.group(4)))
        if found:
            return found

    m = RE_YMD_COMPACT.search(text)
    if m:
        found = _valid_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if found:
            return found

    m = RE_MDY.search(text)
    if m:
        raw_year = m.group(3)
        year = int(raw_year) if len(raw_year) == 4 else 2000 + int(raw_year)
        found = _valid_date(year, int(m.group(1)), int(m.group(2)))
        if found:
            return found

    return None


def year_month_from_segment(segment: str) -> tuple[str, str]:
    """Pull (year, month) hints out of one folder name. Empty strings if none."""
    year = ""
    month = ""

    m = RE_YEAR_MONTH.search(segment)
    if m:
        return m.group(1), f"{int(m.group(2)):02d}"

    m = RE_YEAR.search(segment)
    if m:
        year = m.group(1)

    m = RE_MONTH_NAME.search(segment.lower())
    if m:
        month = f"{MONTH_NAMES[m.group(1)]:02d}"
    elif RE_MONTH_ONLY.match(segment):
        month = f"{int(segment.strip()):02d}"

    return year, month


def derive_archive_context(source_root: Path) -> tuple[str, str]:
    """Work out the archive year/month implied by the SCAN ROOT itself.

    For "V:\\Companies\\A Current Month\\2026" this returns ("2026", "").
    The deepest year-bearing segment wins, so ".../2026/06 June" gives
    ("2026", "06").
    """
    archive_year = ""
    archive_month = ""
    for segment in Path(source_root).parts:
        seg_year, seg_month = year_month_from_segment(str(segment))
        if seg_year:
            archive_year = seg_year
            archive_month = seg_month or ""
        elif seg_month and archive_year:
            # A month folder sitting under the year folder.
            archive_month = seg_month
    return archive_year, archive_month


def archive_context_from_relative(relative_parts: list[str]) -> tuple[str, str]:
    """Archive year/month from the folders BELOW the scan root.

    Used when the scan root itself carries no year (e.g. scanning
    "V:\\Companies\\A Current Month" directly, where 2026 is a subfolder).
    Shallowest folders win, because that is where the archive year lives.
    """
    archive_year = ""
    archive_month = ""
    for segment in relative_parts:
        m = RE_YEAR_ONLY.match(str(segment))
        if m and not archive_year:
            archive_year = m.group(1)
            continue
        if archive_year and not archive_month:
            _, seg_month = year_month_from_segment(str(segment))
            if seg_month:
                archive_month = seg_month
    return archive_year, archive_month


def plausible_year_bounds(rules: dict) -> tuple[int, int]:
    """(min, max) year that may ever be used as detected_year."""
    min_year = int(rules.get("min_plausible_year", 1990))
    max_year = datetime.now().year + int(rules.get("max_plausible_year_offset", 1))
    return min_year, max_year


def folder_date_and_period(relative_parts: list[str], rules: dict) -> dict:
    """Year/month signals taken from FOLDER names only.

    Returns a dict with:
        folder_date_guess  YYYY-MM-DD from a daily folder such as 6.22.2026
        folder_year        year from that date, else a loose folder year
        folder_month       month from that date, else a loose folder month
        year_source        "date" | "loose" | ""
        future_years       implausible/future years seen in folder names, e.g.
                           a security folder called "XYZ Converts 2029"

    Phase 1.2: a folder year beyond the plausible window can NEVER become
    folder_year. That was the remaining leak putting 2028-2032 into
    detected_year - the maturity was in a folder name, not a file name.
    """
    min_year, max_year = plausible_year_bounds(rules)
    future_years: list[str] = []

    folder_date = ""
    for segment in reversed(relative_parts):  # deepest folder first
        found = find_date_in_text(str(segment))
        if found:
            year = int(found[0:4])
            if min_year <= year <= max_year:
                folder_date = found
                break
            if year > max_year and found[0:4] not in future_years:
                future_years.append(found[0:4])

    if folder_date:
        return {
            "folder_date_guess": folder_date,
            "folder_year": folder_date[0:4],
            "folder_month": folder_date[5:7],
            "year_source": "date",
            "future_years": future_years,
        }

    folder_year = ""
    folder_month = ""
    for segment in reversed(relative_parts):
        seg_year, seg_month = year_month_from_segment(str(segment))
        if seg_year:
            year = int(seg_year)
            if min_year <= year <= max_year:
                if not folder_year:
                    folder_year = seg_year
            elif year > max_year and seg_year not in future_years:
                future_years.append(seg_year)
        if seg_month and not folder_month:
            folder_month = seg_month

    return {
        "folder_date_guess": "",
        "folder_year": folder_year,
        "folder_month": folder_month,
        "year_source": "loose" if folder_year else "",
        "future_years": future_years,
    }


def filename_year_analysis(stem: str, base_year: str, rules: dict,
                           extra_future_years: list[str] | None = None) -> tuple[list[str], list[str]]:
    """(filename_year_mentions, possible_maturity_years) for one file name.

    A year in a file name is treated as a possible maturity when it is further
    out than the archive year and either (a) the name carries security wording
    such as "convertible", "notes" or "due", or (b) it is beyond
    current year + maturity_year_lookahead. This is what stops 2028 / 2029 /
    2030 / 2032 / 2036 from polluting detected_year.
    """
    extra_future_years = extra_future_years or []
    mentions = sorted({m.group(1) for m in RE_YEAR.finditer(stem)})
    if not mentions and not extra_future_years:
        return [], []

    lowered = " " + stem.lower() + " "
    security_keywords = [str(k).lower() for k in rules.get("security_context_keywords", [])]
    has_security_context = any(k in lowered for k in security_keywords)

    lookahead = int(rules.get("maturity_year_lookahead", 1))
    current_year = datetime.now().year
    hard_future_cutoff = current_year + lookahead
    base = int(base_year) if base_year.isdigit() else current_year

    maturities = []
    for year_text in mentions:
        year = int(year_text)
        if year > hard_future_cutoff:
            maturities.append(year_text)
        elif has_security_context and year > base:
            maturities.append(year_text)

    # Future years found in FOLDER names (e.g. a "XYZ Converts 2029" folder)
    # are maturities too - they must never reach detected_year.
    for year_text in extra_future_years:
        if year_text not in maturities:
            maturities.append(year_text)

    return mentions, sorted(maturities)


def resolve_detected_year(folder_info: dict, archive_year: str, filename_date: str | None,
                          filename_mentions: list[str], maturity_years: list[str],
                          rules: dict) -> tuple[str, str, str]:
    """(detected_year, date_confidence, year_source) in strict priority order.

    1. the daily folder date year        -> High confidence
    2. the archive path year             -> High confidence
    3. a loose year in a folder name     -> High confidence (still folder-level)
    4. a full date inside the file name  -> Medium confidence
    5. a plausible loose year in the file name, never a maturity -> Low
    6. blank                                                     -> None

    Phase 1.2 guarantee: whatever wins, it is clamped to the plausible window,
    so a maturity year can never become detected_year. For a scan of
    "V:\\Companies\\A Current Month\\2026" this makes detected_year 2026 for
    essentially every row.
    """
    min_year, max_year = plausible_year_bounds(rules)

    def ok(year_text: str) -> bool:
        return bool(year_text) and year_text.isdigit() and min_year <= int(year_text) <= max_year

    if folder_info.get("year_source") == "date" and ok(folder_info.get("folder_year", "")):
        return folder_info["folder_year"], "High", "folder_date"

    if ok(archive_year):
        return archive_year, "High", "archive_path"

    if folder_info.get("year_source") == "loose" and ok(folder_info.get("folder_year", "")):
        return folder_info["folder_year"], "High", "folder_name"

    if filename_date:
        year = filename_date[0:4]
        if ok(year):
            return year, "Medium", "filename_date"

    maturity_set = set(maturity_years)
    plausible = [y for y in filename_mentions if y not in maturity_set and ok(y)]
    if plausible:
        return max(plausible), "Low", "filename_year"

    return "", "None", "none"


# --------------------------------------------------------------------------
# Ticker / vendor detection
# --------------------------------------------------------------------------

RE_TOKEN_SPLIT = re.compile(r"[^A-Za-z]+")

# Strips the letter part of filing/period shorthand that sits right after a
# digit, so "8-K", "10-Q", "10K" and "2026Q2" do not leak K / Q as "tickers".
RE_FORM_SUFFIX = re.compile(r"(?<=\d)-?[A-Za-z]{1,2}(?![A-Za-z])")

# Same idea in the other direction: "Q2", "H1", "FY26" - a one or two letter
# prefix glued to digits is a period marker, not a ticker.
RE_PERIOD_PREFIX = re.compile(r"(?<![A-Za-z])[A-Za-z]{1,2}(?=\d)")


def analyze_tickers(file_stem: str, path_haystack: str, rules: dict) -> dict:
    """Split uppercase tokens into ticker candidates and research vendors.

    Returns a dict with:
        raw        list  - every uppercase token that passed the length filter
        clean      list  - raw minus exclusions minus source-position vendors
        vendors    list  - vendor tokens that sat in a source-like position
        confidence str   - High / Medium / Low / None

    Nothing here is verified. A wrong ticker is worse than a blank one, so the
    rules stay deliberately conservative.
    """
    min_len = int(rules.get("min_length", 1))
    max_len = int(rules.get("max_length", 5))
    max_clean = int(rules.get("max_candidates_per_file", 5))
    max_raw = int(rules.get("max_raw_candidates_per_file", 10))
    skip_all_caps = bool(rules.get("skip_all_caps_names", True))
    all_caps_threshold = int(rules.get("all_caps_token_threshold", 4))

    exclusions = {str(x).upper() for x in rules.get("exclusions", [])}
    vendor_tokens = {str(x).upper() for x in rules.get("vendor_tokens", [])}
    vendor_phrases = [str(x).lower() for x in rules.get("vendor_phrases", [])]
    context_keywords = [str(x).lower() for x in rules.get("source_context_keywords", [])]
    position_rules = rules.get("source_position_rules", {}) or {}

    # ---- Phase 1.2 rules -------------------------------------------------
    strong_vendors = {str(x).upper() for x in rules.get("strong_vendor_tokens", [])}
    never_tickers = {str(x).upper() for x in rules.get("never_ticker_tokens", [])}
    protected = {str(x).upper() for x in rules.get("protected_tickers", [])}
    first_token_exception = bool(rules.get("strong_vendor_first_token_exception", True))
    company_keywords = [str(x).lower() for x in rules.get("company_context_keywords", [])]
    theme_tokens = {
        str(k).upper(): [str(v).lower() for v in (vals or [])]
        for k, vals in (rules.get("theme_tokens", {}) or {}).items()
    }

    empty = {"raw": [], "clean": [], "vendors": [], "confidence": "None"}

    cleaned_stem = RE_PERIOD_PREFIX.sub(" ", RE_FORM_SUFFIX.sub(" ", file_stem))
    tokens = [t for t in RE_TOKEN_SPLIT.split(cleaned_stem) if t]
    if not tokens:
        return empty

    alpha_tokens = [t for t in tokens if t.isalpha()]
    upper_tokens = [t for t in alpha_tokens if t.isupper()]

    # "ALL CAPS TITLE.pdf" - not a ticker, just shouting.
    if skip_all_caps and len(upper_tokens) >= all_caps_threshold and len(upper_tokens) == len(alpha_tokens):
        return empty

    # ---- raw candidates (audit trail, noise included) --------------------
    raw: list[str] = []
    positions: dict[str, int] = {}
    for index, token in enumerate(tokens):
        if not token.isalpha() or not token.isupper():
            continue
        if not (min_len <= len(token) <= max_len):
            continue
        if token not in raw:
            raw.append(token)
            positions[token] = index

    if not raw:
        return empty

    # ---- source-like context -------------------------------------------
    stem_lower = " " + file_stem.lower() + " "
    context_hit = (
        any(phrase in path_haystack for phrase in vendor_phrases)
        or any(keyword in stem_lower for keyword in context_keywords)
    )

    trailing = ""
    if " - " in file_stem:
        trailing = file_stem.rsplit(" - ", 1)[1]
    elif "_-_" in file_stem:
        trailing = file_stem.rsplit("_-_", 1)[1]

    token_count = len(tokens)

    def in_source_position(token: str) -> bool:
        """Is this vendor token acting as the SOURCE rather than the subject?"""
        if position_rules.get("after_dash_separator", True) and trailing and token in trailing:
            return True
        if position_rules.get("in_last_two_tokens", True) and positions.get(token, 0) >= max(1, token_count - 2):
            return True
        if position_rules.get("research_context_in_path", True) and context_hit:
            return True
        return False

    company_context = any(keyword in stem_lower for keyword in company_keywords)

    def theme_context(token: str) -> bool:
        """Is this token being used as a concept rather than a company?

        "AI" next to "artificial intelligence", "agentic AI", "AI
        infrastructure", "data center" and so on is a theme word. Without that
        context AI stays a candidate, because it is also a real listed ticker.
        """
        for phrase in theme_tokens.get(token, []):
            if phrase in stem_lower or phrase in path_haystack:
                return True
        return False

    # ---- split into clean tickers and vendors ----------------------------
    clean: list[str] = []
    vendors: list[str] = []
    ambiguous = False

    for token in raw:
        # Known Cutler names always survive, whatever else a rule says.
        if token in protected:
            clean.append(token)
            continue
        if token in exclusions:
            continue
        # News outlets and the like are never the subject of a file.
        if token in never_tickers:
            if token in vendor_tokens and token not in vendors:
                vendors.append(token)
            continue
        # Theme words (AI, EV, ESG) in a technology/theme context.
        if token in theme_tokens and theme_context(token):
            continue
        # Strong vendors default to the vendor column; the only way out is
        # being the first token of a file name that reads like a company file.
        if token in strong_vendors:
            if first_token_exception and positions.get(token, 99) == 0 and company_context:
                clean.append(token)
                ambiguous = True
            else:
                vendors.append(token)
            continue
        if token in vendor_tokens:
            if in_source_position(token):
                vendors.append(token)
            else:
                # Could genuinely be the company (e.g. "GS 10-Q.pdf").
                # Keep it, but the row can never be High confidence.
                clean.append(token)
                ambiguous = True
            continue
        clean.append(token)

    clean = clean[:max_clean]
    vendors = vendors[: max_clean]

    # ---- confidence -------------------------------------------------------
    if not clean:
        confidence = "Low" if vendors else "None"
    else:
        first = clean[0]
        early = positions.get(first, 99) <= 1
        if early and not ambiguous and first not in vendor_tokens:
            confidence = "High"
        else:
            confidence = "Medium"

    return {
        "raw": raw[:max_raw],
        "clean": clean,
        "vendors": vendors,
        "confidence": confidence,
    }


# --------------------------------------------------------------------------
# Classification
# --------------------------------------------------------------------------

def classify_by_keywords(haystack: str, rules_list: list, extension: str,
                         extension_defaults: dict, extension_overrides: dict | None = None) -> str:
    """Classify one field.

    Order: extension overrides (hard facts about the format) -> keyword rules
    (first match wins) -> extension defaults -> "Unknown".
    """
    if extension_overrides and extension in extension_overrides:
        return str(extension_overrides[extension])

    for rule in rules_list or []:
        name = rule.get("name") if isinstance(rule, dict) else None
        if not name:
            continue
        for keyword in rule.get("keywords", []) or []:
            if str(keyword).lower() in haystack:
                return str(name)
    return str((extension_defaults or {}).get(extension, "Unknown"))


def _segments_lower(relative_parts: list[str]) -> list[str]:
    return [str(p).strip().lower() for p in relative_parts]


def match_folder_rule(relative_parts: list[str], rules_list: list, key: str,
                      stem_lower: str, industry_signals: list[str]) -> str:
    """Classify from the archive's own folder structure (Phase 1.2).

    A rule fires when one of its folder names IS a path segment, or appears
    inside one ("Community Banks" contains "banks"). Deepest folder wins, so
    ...\\Companies\\Banks\\ is treated as Banks.
    """
    segments = _segments_lower(relative_parts)
    for segment in reversed(segments):  # deepest first
        for rule in rules_list or []:
            if not isinstance(rule, dict):
                continue
            names = [str(f).lower() for f in rule.get("folders", [])]
            if not any(name == segment or name in segment for name in names):
                continue
            value = rule.get(key)
            alternate = rule.get("industry_alternate")
            if alternate and any(sig in stem_lower for sig in industry_signals):
                return str(alternate)
            if value:
                return str(value)
    return ""


def strong_filename_signal(stem_lower: str, rules: dict) -> tuple[str, str]:
    """(source_type, document_type) when the FILE NAME settles it on its own.

    Filing shorthand, press-release wording and explicit research-note wording
    outrank the folder the file happens to live in.
    """
    signals = rules.get("strong_filename_signals", {}) or {}

    for keyword in signals.get("template", ["template", "blank form", "checklist"]) or []:
        if str(keyword).lower() in stem_lower:
            return "Template", "Template"

    forms = [("10-k", "10-K"), ("10k", "10-K"), ("10-q", "10-Q"), ("10q", "10-Q"),
             ("8-k", "8-K"), ("8k", "8-K")]
    for needle, doc_type in forms:
        if needle in stem_lower:
            return "SEC Filing", doc_type

    for keyword in signals.get("sec_filing", []) or []:
        if str(keyword).lower() in stem_lower:
            return "SEC Filing", "Unknown"

    for keyword in signals.get("press_release", []) or []:
        if str(keyword).lower() in stem_lower:
            return "Company Press Release", "Press Release"

    for keyword in signals.get("research_note", []) or []:
        if str(keyword).lower() in stem_lower:
            return "Sell-Side Research", "Research Note"

    return "", ""


def earnings_document_type(stem_lower: str, rules: dict) -> str:
    """Earnings / results wording -> the Phase 1.2 'Earnings / Financial Results' type."""
    for keyword in rules.get("earnings_filename_signals", []) or []:
        if str(keyword).lower() in stem_lower:
            return "Earnings / Financial Results"
    return ""


def guess_source_folder(relative_parts: list[str]) -> str:
    """Best guess at the meaningful 'source' folder for the document.

    Uses the top-level folder under the scan root when there is one, since
    that is usually the company or research family folder.
    """
    if not relative_parts:
        return ""
    return relative_parts[0]


RE_WORD_SAFE = re.compile(r"^[a-z0-9]+$")


def find_hits(haystack: str, needles, word_boundary: bool = False) -> list[str]:
    """Which of these lowercase keywords/phrases appear in the text."""
    hits = []
    for needle in needles or []:
        text = str(needle).lower().strip()
        if not text:
            continue
        if word_boundary and RE_WORD_SAFE.match(text):
            if re.search(rf"\b{re.escape(text)}\b", haystack):
                hits.append(text)
        elif text in haystack:
            hits.append(text)
    return hits


def classify_sensitive(haystack: str, rules: dict) -> tuple[str, list[str], str]:
    """(review_category, sensitive_keyword_hits, sensitive_context_guess).

    Phase 1.2 two-tier test. A research archive is full of words like
    "portfolio", "performance" and "statement" used in ordinary public-company
    language, and treating all of them as restricted data buried the rows that
    actually matter.

      strong restricted phrase          -> sensitive_data           (High)
      weak word + public-company phrase -> public_language_sensitive (Low)
      weak word + research context      -> public_language_sensitive (Low)
      weak word, nothing explaining it  -> possible_sensitive_data   (Medium)
      nothing                           -> no category              (None)

    Nothing is hidden: every row with any hit still lands in
    sensitive_data_review_report.csv with its context guess.
    """
    strong_hits = find_hits(haystack, rules.get("sensitive_strong_phrases", []))
    weak_keywords = rules.get("sensitive_weak_keywords") or rules.get("sensitive_keywords", [])
    weak_hits = find_hits(haystack, weak_keywords, word_boundary=True)

    if strong_hits:
        hits = strong_hits + [h for h in weak_hits if h not in strong_hits]
        return "sensitive_data", hits[:8], CONTEXT_RESTRICTED

    if not weak_hits:
        return "", [], CONTEXT_NONE

    public_hits = find_hits(haystack, rules.get("public_language_phrases", []))
    if public_hits:
        return "public_language_sensitive", (weak_hits + public_hits)[:8], CONTEXT_PUBLIC

    research_hits = find_hits(haystack, rules.get("research_business_phrases", []))
    if research_hits:
        return "public_language_sensitive", weak_hits[:8], CONTEXT_RESEARCH

    return "possible_sensitive_data", weak_hits[:8], CONTEXT_MANUAL


def grade_review(categories: list[str], rules: dict) -> tuple[str, str, str]:
    """(review_severity, review_category, review_reason) from raw categories."""
    severity_map = rules.get("review_severity_map") or DEFAULT_SEVERITY_MAP
    order = rules.get("review_category_order") or list(DEFAULT_SEVERITY_MAP)

    ranked = [c for c in order if c in categories]
    ranked += [c for c in categories if c not in ranked]

    if not ranked:
        return "None", "", ""

    severity = "None"
    for category in ranked:
        candidate = str(severity_map.get(category, "Low"))
        if candidate not in SEVERITY_ORDER:
            candidate = "Low"
        if SEVERITY_ORDER.index(candidate) < SEVERITY_ORDER.index(severity):
            severity = candidate

    reason = "; ".join(CATEGORY_REASONS.get(c, c) for c in ranked)
    return severity, ";".join(ranked), reason


# --------------------------------------------------------------------------
# Per-file record building
# --------------------------------------------------------------------------

def sha256_file(path: Path, max_bytes: int = 0) -> str:
    """SHA-256 of the file contents, read in chunks. Read-only, always."""
    digest = hashlib.sha256()
    read = 0
    with open(os_path(path), "rb") as fh:
        while True:
            chunk = fh.read(HASH_CHUNK_BYTES)
            if not chunk:
                break
            digest.update(chunk)
            read += len(chunk)
            if max_bytes and read >= max_bytes:
                break
    return digest.hexdigest()


def make_document_id(relative_path: str) -> str:
    """Stable, deterministic id derived from the file's path in the archive."""
    normalized = relative_path.replace("\\", "/").lower()
    return "doc_" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def to_iso(timestamp: float | None) -> str:
    if not timestamp:
        return ""
    try:
        return datetime.fromtimestamp(timestamp).replace(microsecond=0).isoformat()
    except (OSError, OverflowError, ValueError):
        return ""


def build_record(file_path: Path, source_root: Path, source_rules: dict,
                 ticker_rules: dict, max_hash_bytes: int, processed_at: str,
                 archive_context: tuple[str, str] = ("", "")) -> dict:
    """Collect metadata + guesses for one file. Never raises - errors are recorded."""
    errors: list[str] = []

    try:
        relative_path = str(file_path.relative_to(source_root))
    except ValueError:
        relative_path = str(file_path)

    relative_parts = [str(p) for p in Path(relative_path).parts[:-1]]  # folders only
    file_name = file_path.name
    stem = file_path.stem
    extension = file_path.suffix.lower()
    parent_folder = file_path.parent.name

    # ---- file system metadata -------------------------------------------
    size_bytes = 0
    created_time = ""
    modified_time = ""
    try:
        stat = os.stat(os_path(file_path))
        size_bytes = stat.st_size
        created_time = to_iso(getattr(stat, "st_birthtime", None) or stat.st_ctime)
        modified_time = to_iso(stat.st_mtime)
    except PermissionError as exc:
        errors.append(f"permission denied reading metadata: {exc}")
    except OSError as exc:
        errors.append(f"metadata error: {exc}")

    # ---- hash -------------------------------------------------------------
    file_hash = ""
    if not errors:
        try:
            file_hash = sha256_file(file_path, max_hash_bytes)
        except PermissionError as exc:
            errors.append(f"permission denied hashing file: {exc}")
        except OSError as exc:
            errors.append(f"hash error: {exc}")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"unexpected hash error: {exc}")

    # ---- text used for keyword matching ----------------------------------
    haystack = relative_path.replace("\\", " ").replace("/", " ").replace("_", " ").lower()
    haystack = f" {haystack} "  # padding so ' ai ' style keywords can match

    # ---- dates (Phase 1.1 precedence) ------------------------------------
    archive_year, archive_month = archive_context
    if not archive_year:
        archive_year, archive_month = archive_context_from_relative(relative_parts)

    folder_info = folder_date_and_period(relative_parts, source_rules)
    folder_date_guess = folder_info["folder_date_guess"]
    folder_year = folder_info["folder_year"]
    folder_month = folder_info["folder_month"]

    filename_date = find_date_in_text(stem)
    filename_mentions, maturity_years = filename_year_analysis(
        stem, folder_year or archive_year, source_rules, folder_info["future_years"]
    )

    detected_year, date_confidence, year_source = resolve_detected_year(
        folder_info, archive_year, filename_date, filename_mentions, maturity_years, source_rules
    )

    detected_month = folder_month or archive_month or ""
    if not detected_month and filename_date and filename_date[0:4] == detected_year:
        detected_month = filename_date[5:7]

    # ---- tickers and vendors ---------------------------------------------
    tickers = analyze_tickers(stem, haystack, ticker_rules)
    ticker_clean = ",".join(tickers["clean"])
    ticker_raw = ",".join(tickers["raw"])
    vendor_candidates = ",".join(tickers["vendors"])

    # ---- classification (Phase 1.2 precedence) ---------------------------
    # 1. a strong filing / press-release signal in the FILE NAME
    # 2. the archive's own folder structure
    # 3. the keyword rules in configs/source_type_rules.yaml
    # 4. extension defaults, else Unknown
    stem_lower = " " + stem.lower().replace("_", " ") + " "
    industry_signals = [str(s).lower() for s in source_rules.get("industry_filename_signals", [])]

    strong_source, strong_document = strong_filename_signal(stem_lower, source_rules)

    source_type_guess = strong_source or match_folder_rule(
        relative_parts, source_rules.get("source_type_folder_rules", []),
        "source_type", stem_lower, industry_signals,
    )
    if not source_type_guess:
        source_type_guess = classify_by_keywords(
            haystack, source_rules.get("source_types", []), extension,
            source_rules.get("source_type_extension_defaults", {}),
        )

    # Document type: the file format is the hardest fact, then the file name,
    # then earnings wording, then keywords, then the folder.
    overrides = source_rules.get("document_type_extension_overrides", {}) or {}
    if extension in overrides:
        document_type_guess = str(overrides[extension])
    else:
        document_type_guess = strong_document if strong_document and strong_document != "Unknown" else ""
        if not document_type_guess:
            document_type_guess = earnings_document_type(stem_lower, source_rules)
        if not document_type_guess:
            document_type_guess = classify_by_keywords(
                haystack, source_rules.get("document_types", []), extension,
                {},  # extension defaults applied below, after the folder rules
            )
        if document_type_guess == "Unknown":
            document_type_guess = ""
        if not document_type_guess:
            document_type_guess = match_folder_rule(
                relative_parts, source_rules.get("document_type_folder_rules", []),
                "document_type", stem_lower, industry_signals,
            )
        if not document_type_guess:
            defaults = source_rules.get("document_type_extension_defaults", {}) or {}
            document_type_guess = str(defaults.get(extension, "Unknown"))

    strategy_theme_guess = classify_by_keywords(
        haystack, source_rules.get("strategy_themes", []), extension,
        source_rules.get("strategy_theme_extension_defaults", {}),
    )

    # ---- review grading ---------------------------------------------------
    categories: list[str] = []

    sensitive_category, sensitive_hits, sensitive_context = classify_sensitive(
        haystack, source_rules
    )
    if sensitive_category:
        categories.append(sensitive_category)
    if errors:
        categories.append("scan_error")

    research_sources = set(source_rules.get("research_looking_source_types", []))
    research_docs = set(source_rules.get("research_looking_document_types", []))
    looks_like_research = source_type_guess in research_sources or document_type_guess in research_docs
    if looks_like_research and not ticker_clean:
        categories.append("no_ticker_on_research")

    if source_type_guess == "Unknown":
        categories.append("unknown_source_type")
    if document_type_guess == "Unknown":
        categories.append("unknown_document_type")
    # NOTE: the "duplicate" category is added later, in build_metadata_index.py,
    # where hashes can be compared across the whole index.

    severity, category_text, reason = grade_review(categories, source_rules)

    # Spell out WHICH sensitive words matched, right next to that reason.
    if sensitive_hits and sensitive_category:
        hits = ", ".join(sorted({str(k).lower() for k in sensitive_hits})[:5])
        base_reason = CATEGORY_REASONS.get(sensitive_category, "")
        if base_reason:
            reason = reason.replace(base_reason, f"{base_reason} ({hits})")
    for err in errors:
        reason += f" [{err}]"

    return {
        "document_id": make_document_id(relative_path),
        "file_name": file_name,
        "file_extension": extension,
        "file_path": str(file_path),
        "relative_path": relative_path,
        "parent_folder": parent_folder,
        "source_folder_guess": guess_source_folder(relative_parts),
        "file_size_bytes": size_bytes,
        "created_time": created_time,
        "modified_time": modified_time,
        "file_hash_sha256": file_hash,
        # duplicate_status / duplicate_of are assigned in build_metadata_index.py,
        # where we can compare hashes across the whole index, not just this scan.
        "duplicate_status": "error" if errors else "pending",
        "duplicate_of": "",
        "folder_date_guess": folder_date_guess,
        "detected_year": detected_year,
        "detected_month": detected_month,
        "ticker_candidates": ticker_clean,          # legacy column == clean
        "source_type_guess": source_type_guess,
        "document_type_guess": document_type_guess,
        "strategy_theme_guess": strategy_theme_guess,
        "needs_review": 1 if categories else 0,
        "review_reason": reason.strip(),
        "processed_at": processed_at,
        # ---- Phase 1.1 columns -------------------------------------------
        "archive_year": archive_year,
        "archive_month": archive_month,
        "folder_year": folder_year,
        "folder_month": folder_month,
        "filename_year_mentions": ",".join(filename_mentions),
        "possible_maturity_years": ",".join(maturity_years),
        "source_vendor_candidates": vendor_candidates,
        "ticker_candidates_raw": ticker_raw,
        "ticker_candidates_clean": ticker_clean,
        "ticker_confidence": tickers["confidence"],
        "review_severity": severity,
        "review_category": category_text,
        # ---- Phase 1.2 columns -------------------------------------------
        "date_confidence": date_confidence,
        "detected_year_source": year_source,
        "sensitive_keyword_hits": ",".join(sensitive_hits),
        "sensitive_context_guess": sensitive_context,
        # ---- Phase 2 gate -------------------------------------------------
        # High review = do not extract text until a human has cleared the row.
        # build_metadata_index.py recomputes this after duplicate grading.
        "exclude_from_phase2_extraction": 1 if severity == "High" else 0,
        # ---- bookkeeping --------------------------------------------------
        "scan_source_root": str(source_root),
    }


# --------------------------------------------------------------------------
# Directory walking
# --------------------------------------------------------------------------

def walk_supported_files(source_root: Path, skip_dirs: set, extensions: set):
    """Yield supported files under source_root. Never raises on a bad folder.

    Uses an explicit stack (not os.walk) so a permission error on one folder
    only skips that folder and the scan continues.
    """
    stack = [source_root]
    stats = {"dirs_visited": 0, "dirs_skipped": 0, "dir_errors": 0,
             "files_seen": 0, "files_unsupported": 0}

    while stack:
        current = stack.pop()
        stats["dirs_visited"] += 1
        try:
            with os.scandir(os_path(current)) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            if entry.name in skip_dirs or entry.name.startswith("~$"):
                                stats["dirs_skipped"] += 1
                                LOGGER.debug("Skipping folder: %s", entry.path)
                                continue
                            stack.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            stats["files_seen"] += 1
                            name = entry.name
                            if name.startswith("~$"):  # Office lock files
                                stats["files_unsupported"] += 1
                                continue
                            if Path(name).suffix.lower() not in extensions:
                                stats["files_unsupported"] += 1
                                continue
                            yield Path(entry.path), stats
                    except OSError as exc:
                        stats["dir_errors"] += 1
                        LOGGER.warning("Could not inspect entry in %s: %s", current, exc)
        except PermissionError as exc:
            stats["dir_errors"] += 1
            LOGGER.warning("Permission denied, skipping folder: %s (%s)", current, exc)
        except OSError as exc:
            stats["dir_errors"] += 1
            LOGGER.warning("Could not read folder, skipping: %s (%s)", current, exc)

    # Final yield of stats so the caller always gets the totals.
    yield None, stats


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Cutler Research Brain Phase 1.2 - read-only source archive scanner.",
    )
    parser.add_argument(
        "--source", required=True,
        help=r'Folder to scan, e.g. "V:\Companies\A Current Month\2026"',
    )
    parser.add_argument(
        "--project-root", required=True,
        help=r'Project root for all outputs, e.g. "C:\Users\AI Admin\Documents\CutlerResearchBrain"',
    )
    parser.add_argument(
        "--archive-year", default="",
        help="Force the archive year instead of deriving it from the source path.",
    )
    parser.add_argument(
        "--limit", type=int, default=0,
        help="Stop after N files (0 = no limit). Useful for a quick trial run.",
    )
    parser.add_argument(
        "--max-hash-mb", type=float, default=0,
        help="Only hash the first N MB of each file (0 = hash the whole file).",
    )
    parser.add_argument(
        "--no-index", action="store_true",
        help="Only write data/raw_scan.jsonl; skip building the SQLite index and reports.",
    )
    parser.add_argument(
        "--reset-db", action="store_true",
        help="Drop and rebuild the documents table instead of updating it in place.",
    )
    parser.add_argument("--verbose", action="store_true", help="Chattier console output.")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    source = Path(args.source).expanduser()
    project_root = Path(args.project_root).expanduser().resolve()

    # SAFETY FIRST: validate before creating a single file or folder, so a bad
    # --project-root can never cause even a log file to land on the V Drive.
    validate_paths(source, project_root)

    log_path = project_root / "logs" / "phase1_scan.log"
    setup_logging(log_path, verbose=args.verbose)

    started = time.time()
    processed_at = datetime.now(timezone.utc).astimezone().replace(microsecond=0).isoformat()

    archive_context = derive_archive_context(source)
    if args.archive_year:
        archive_context = (args.archive_year, archive_context[1])

    LOGGER.info("=" * 78)
    LOGGER.info("Cutler Research Brain - Phase 1.2 source archive scan")
    LOGGER.info("Source (READ-ONLY): %s", source)
    LOGGER.info("Project root       : %s", project_root)
    LOGGER.info("Archive year/month : %s / %s",
                archive_context[0] or "(none in path)", archive_context[1] or "-")
    LOGGER.info("=" * 78)

    # ---- remaining safety gates ------------------------------------------
    data_dir = project_root / "data"
    index_dir = project_root / "02_Metadata_Index"
    for out_dir in (data_dir, index_dir, log_path.parent):
        assert_output_is_safe(out_dir, source, project_root)
        out_dir.mkdir(parents=True, exist_ok=True)

    records_path = data_dir / "raw_scan.jsonl"
    assert_output_is_safe(records_path, source, project_root)

    # ---- rules ------------------------------------------------------------
    configs_dir = project_root / "configs"
    source_rules, source_rules_origin = load_yaml_config(
        configs_dir / "source_type_rules.yaml", FALLBACK_SOURCE_RULES, "classification"
    )
    ticker_rules, ticker_rules_origin = load_yaml_config(
        configs_dir / "ticker_rules.yaml", FALLBACK_TICKER_RULES, "ticker"
    )

    max_hash_bytes = int(args.max_hash_mb * 1024 * 1024) if args.max_hash_mb else 0

    # ---- scan -------------------------------------------------------------
    indexed = 0
    errored = 0
    walk_stats: dict = {}

    LOGGER.info("Scanning... (progress printed every 250 files)")

    with records_path.open("w", encoding="utf-8") as out:
        for file_path, stats in walk_supported_files(source, SKIP_DIRS, SUPPORTED_EXTENSIONS):
            walk_stats = stats
            if file_path is None:  # generator finished; stats are final
                break

            try:
                record = build_record(
                    file_path, source, source_rules, ticker_rules, max_hash_bytes,
                    processed_at, archive_context,
                )
            except Exception as exc:  # noqa: BLE001 - one bad file must never stop the scan
                errored += 1
                LOGGER.error("Unhandled error building record for %s: %s", file_path, exc)
                continue

            if record["duplicate_status"] == "error":
                errored += 1
                LOGGER.warning("Recorded with errors: %s (%s)", file_path, record["review_reason"])

            out.write(json.dumps(record, ensure_ascii=False) + "\n")
            indexed += 1

            if indexed % 250 == 0:
                LOGGER.info("  ...%d files indexed", indexed)

            if args.limit and indexed >= args.limit:
                LOGGER.info("Reached --limit of %d files; stopping the walk.", args.limit)
                break

    elapsed = time.time() - started

    LOGGER.info("-" * 78)
    LOGGER.info("Walk complete in %.1f seconds", elapsed)
    LOGGER.info("  folders visited     : %s", walk_stats.get("dirs_visited", 0))
    LOGGER.info("  folders skipped     : %s", walk_stats.get("dirs_skipped", 0))
    LOGGER.info("  folder/entry errors : %s", walk_stats.get("dir_errors", 0))
    LOGGER.info("  files seen          : %s", walk_stats.get("files_seen", 0))
    LOGGER.info("  unsupported skipped : %s", walk_stats.get("files_unsupported", 0))
    LOGGER.info("  files indexed       : %s", indexed)
    LOGGER.info("  files with errors   : %s", errored)
    LOGGER.info("  raw records         : %s", records_path)
    LOGGER.info("-" * 78)

    if indexed == 0:
        LOGGER.warning("No supported files were found under %s - nothing to index.", source)

    if args.no_index:
        LOGGER.info("--no-index set; skipping SQLite index and reports.")
        return 0

    # ---- hand off to the index builder ------------------------------------
    sys.path.insert(0, str(SCRIPT_DIR))
    try:
        import build_metadata_index  # noqa: PLC0415 - intentional late import
    except ImportError as exc:
        LOGGER.error("Could not import build_metadata_index.py (%s). Raw records are still at %s",
                     exc, records_path)
        return 1

    scan_context = {
        "source_root": str(source),
        "project_root": str(project_root),
        "archive_year": archive_context[0],
        "archive_month": archive_context[1],
        "processed_at": processed_at,
        "scan_seconds": round(elapsed, 1),
        "files_indexed_this_run": indexed,
        "files_with_errors_this_run": errored,
        "folders_visited": walk_stats.get("dirs_visited", 0),
        "folders_skipped": walk_stats.get("dirs_skipped", 0),
        "folder_errors": walk_stats.get("dir_errors", 0),
        "files_seen": walk_stats.get("files_seen", 0),
        "files_unsupported": walk_stats.get("files_unsupported", 0),
        "classification_rules": source_rules_origin,
        "ticker_rules": ticker_rules_origin,
        "limit_applied": args.limit or 0,
        "phase": "1.2",
    }

    build_metadata_index.build_index(
        project_root=project_root,
        records_path=records_path,
        scan_context=scan_context,
        reset=args.reset_db,
        logger=LOGGER,
        source_rules=source_rules,
    )

    LOGGER.info("Phase 1.2 scan finished. Review the outputs in 02_Metadata_Index.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
