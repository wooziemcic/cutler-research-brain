#!/usr/bin/env python3
# =============================================================================
# Cutler Research Brain - Phase 5B
# ask_brain.py
#
# Ask a question. Retrieve MSFT chunks that were already embedded in Phase 5A.
# Select a small evidence pack. Ask the answer model to answer ONLY from that
# evidence pack, with a citation on every claim. Save the answer, the evidence
# and the retrieval trace so a human can check every sentence against a real
# file on the V Drive.
#
# WHAT THIS SCRIPT WILL NOT DO
#   - It will not answer from model memory. If retrieval returns nothing, no
#     answer model is called at all.
#   - It will not read documents outside the MSFT Chroma index.
#   - It will not embed, re-embed or modify any document chunk. It opens the
#     Phase 5A collection with get_collection() and only ever queries it.
#   - It will not make an investment recommendation, or infer a thesis,
#     sentiment, rating, catalyst or investment view that the retrieved text
#     does not directly support.
#   - It will not open a source PDF, and it will not touch the V Drive.
#   - It will not print or log the API key.
#
# OpenAI endpoints used, and no others:
#   - embeddings   : ONE call, to embed the question
#   - responses    : ONE call, to write the answer from the evidence pack
#
# An answer produced here is a DRAFT built from retrieved text. It is not a
# conclusion, not verified, and not advice.
#
#   AI drafts. Humans verify. Cutler decides.
#
# Usage:
#   py -3.11 scripts\ask_brain.py ^
#       --project-root "C:\Users\AI Admin\Documents\CutlerResearchBrain" ^
#       --db "data\document_metadata.sqlite" --ticker MSFT ^
#       --question "What does the retrieved source text say about Azure revenue disclosure?"
# =============================================================================

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import re
import sqlite3
import sys
import textwrap
import time
from datetime import datetime, timezone
from pathlib import Path

PIPELINE_VERSION = "phase14.3.7-1.0"

DEFAULT_TICKER = "MSFT"

# Phase 11. The expansion set. A ticker being listed here means the project
# knows the name, NOT that an index exists - ask_brain refuses cleanly for one
# that has not been built, and never falls back to another ticker's index.
NOT_INDEXED_MESSAGE = "This ticker has not been indexed yet."
KNOWN_TICKERS = ("MSFT", "KIM", "KR", "MRK", "T", "VZ", "AMZN", "SLB", "B", "ABBV")
DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"
DEFAULT_ANSWER_MODEL = "gpt-5-mini"

DEFAULT_TOP_K = 20
DEFAULT_MAX_CHUNKS = 8
DEFAULT_MAX_CHUNKS_PER_DOCUMENT = 2
DEFAULT_SOURCE_PRIORITY = "balanced"

DEFAULT_MAX_OUTPUT_TOKENS = 6000   # reasoning models spend part of this budget thinking
MAX_RETRIES = 4
RETRY_BASE_SECONDS = 2.0

# Phase 14.3.6. The quick ceiling, as a number this script can act on rather
# than only words in the prompt. It must equal answer_quality.QUICK_WORD_BAND[1]
# - the review script reports an answer as over-length against that band, and a
# repair trigger that disagreed with the metric reporting it would be worse than
# no trigger at all. tests/test_phase14.py pins the two together.
QUICK_DIRECT_WORD_CEILING = 220

# Character-identical to answer_quality.RE_WORD, and pinned to it by the same
# test. Two ways of counting the words of one answer is two answers to "how long
# is this", which is the defect Phase 11.2 learned to test for; ask_brain cannot
# import answer_quality, because answer_quality imports ask_brain for the shared
# definition of a claim. (ask_brain's own RE_WORD is a different thing - it
# tokenises a QUESTION, lowercase and ampersands included.)
RE_ANSWER_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9'%$.,/-]*")

# Phase 14.3.5. The hard ceiling on GENERATIVE calls for one question, shared by
# every retry path there is: the Phase 14.3.3 length retry and the compliance
# retry below both spend from it. Three is exactly enough for the worst legal
# sequence - the answer, one length retry, one compliance retry - and one too
# few for any loop between them. MAX_RETRIES above is a different thing: those
# are transport re-sends of the SAME request after a network error, so the
# absolute ceiling on HTTP requests for one question is
# MAX_ANSWER_MODEL_CALLS * MAX_RETRIES, and it is finite by construction.
MAX_ANSWER_MODEL_CALLS = 3

# Published list prices per 1,000,000 tokens, used ONLY to print a local
# estimate. They are overridable in .env because prices change and this file
# does not get to be the authority on what anything costs.
DEFAULT_EMBED_COST_PER_1M = 0.02
DEFAULT_ANSWER_INPUT_COST_PER_1M = 0.25
DEFAULT_ANSWER_OUTPUT_COST_PER_1M = 2.00

FALLBACK_CHARS_PER_TOKEN = 4

# Selection weights. Small, fixed, and written into every retrieval log so the
# ordering can always be reconstructed by hand.
PRIORITY_BONUS = 0.06            # matches the question's intent (vector mode)
PRIORITY_PENALTY = 0.03          # works against the question's intent (vector mode)
DOC_REPEAT_PENALTY = 0.05        # per chunk already taken from this document
SOURCE_REPEAT_PENALTY = 0.03     # per chunk already taken from this source type
WEAK_EVIDENCE_SIMILARITY = 0.25  # below this, evidence is LABELLED weak (never dropped)

# --- Phase 6: retrieval modes and hybrid scoring ----------------------------
RETRIEVAL_MODES = ("vector", "keyword", "hybrid")
DEFAULT_RETRIEVAL_MODE = "hybrid"

# Hybrid weights. Vector still leads - it is what finds text that means the
# same thing in different words - but keyword score gets a real say, because
# vector search is weak exactly where an analyst is strongest: exact strings
# like "Azure and other cloud services".
W_VECTOR = 0.65
W_KEYWORD = 0.25
W_PRIORITY = 0.10
W_RECENCY = 0.10                 # only when --prefer-recent is passed

# The priority component is 0..1 so its weight above is the whole swing.
PRIORITY_MATCH = 1.0             # the kind of source the question is asking for
PRIORITY_NEUTRAL = 0.5           # neither one thing nor the other
PRIORITY_AGAINST = 0.0           # the opposite kind

# When metadata filters are active, ask Chroma for more than top_k so the
# filter has something to cut down. Capped by the collection size.
VECTOR_OVERSAMPLE = 5

# Phase 6, Part E. If the answer says its own evidence is not responsive, that
# is the most reliable signal in the whole system - more reliable than any
# similarity score - so it caps confidence outright.
DIRECTNESS_PHRASES = (
    "does not directly answer the question",
    "retrieved evidence is related, but",
    "i did not find enough retrieved evidence",
    "not enough evidence",
    "does not provide",
    "does not include",
)
DIRECTNESS_CAP_REASON = ("Capped because answer states retrieved evidence does not directly "
                         "answer the question.")

# Sentences shorter than this in the Direct Answer are not treated as factual
# claims needing a citation ("Yes.", "See below.").
MIN_FACTUAL_SENTENCE_CHARS = 40

STATUS_COMPLETED = "completed"
STATUS_NO_EVIDENCE = "no_evidence"
STATUS_FAILED = "failed"

LOGGER = logging.getLogger("phase5b_ask_brain")


# ---------------------------------------------------------------------------
# Console output (Phase 7A.1)
#
# A Windows console defaults to a legacy code page - usually cp1252 - which
# cannot encode characters the answer model uses freely, such as the
# non-breaking hyphen U+2011 in "non-GAAP". Printing one raised
# UnicodeEncodeError and killed the process AFTER the answer, the retrieval
# log, the Obsidian note and the database row had all been written correctly.
#
# The run was fine. Only the display of it failed. Two defences:
#   1. ask the console for UTF-8 up front, which fixes it properly;
#   2. never let a print failure end a run that already succeeded.
# ---------------------------------------------------------------------------

PRINT_FAILURES = 0


def configure_console_encoding() -> None:
    """Ask stdout and stderr for UTF-8 with replacement on the way out."""
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None or not hasattr(stream, "reconfigure"):
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError, AttributeError):
            # An unusual stream (a pipe already wrapped, a redirected handle).
            # Not fatal: safe_print below still catches whatever it throws.
            pass


def safe_print(text: str = "") -> None:
    """print(), but a console that cannot render a character is not an error.

    Anything unprintable becomes a replacement character. Nothing that reaches
    a file is altered by this - the answer on disk keeps every character
    exactly as the model wrote it.
    """
    global PRINT_FAILURES
    try:
        print(text)
        return
    except UnicodeEncodeError:
        pass
    except (OSError, ValueError):
        # The stream is gone (closed pipe, detached console). Nothing to do.
        PRINT_FAILURES += 1
        return
    try:
        encoding = getattr(sys.stdout, "encoding", None) or "ascii"
        print(str(text).encode(encoding, errors="replace").decode(encoding, errors="replace"))
    except Exception:  # noqa: BLE001 - printing must never end a completed run
        PRINT_FAILURES += 1

# --- Source taxonomy, reused from the Phase 1 classification vocabulary ------

DIRECT_SOURCE_TYPES = {
    "SEC Filing", "Company Press Release", "Company Material",
}
DIRECT_DOCUMENT_TYPES = {
    "8-K", "10-K", "10-Q", "Press Release", "Investor Presentation",
    "Earnings / Financial Results", "Company Material",
}
ANALYST_SOURCE_TYPES = {
    "Sell-Side Research", "Industry Research", "Bank Research", "Credit Research",
    "Convertible Research", "REIT Research", "Green Street Research",
    "Market Monitor", "Short Sale Research",
}
ANALYST_DOCUMENT_TYPES = {
    "Research Note", "Credit Note", "Convertible Note",
}

# Question intent. Deterministic keyword rules - no model decides this.
COMPANY_INTENT_KEYWORDS = (
    "reported", "report", "reports", "disclosed", "disclosure", "filing", "filed",
    "10-k", "10k", "10-q", "10q", "8-k", "8k", "transcript", "earnings release",
    "press release", "results", "revenue", "revenues", "earnings", "guidance",
    "margin guidance", "official", "company said", "management said", "management",
    "financial", "financials", "segment", "eps", "income", "cash flow",
    "balance sheet", "quarter", "quarterly", "fiscal", "company-reported",
    "company reported",
)
ANALYST_INTENT_KEYWORDS = (
    "analyst", "analysts", "concern", "concerns", "concerned", "worried", "worry",
    "risk", "risks", "debate", "view", "views", "thesis", "valuation", "upside",
    "downside", "bull", "bear", "bullish", "bearish", "estimate", "estimates",
    "forecast", "forecasts", "consensus", "pressure", "margin pressure",
    "interpretation", "sentiment", "rating", "price target", "multiple",
    "skeptic", "sceptic", "sell-side", "sell side", "street", "capex",
)

# --- Phase 8.1, Part C: topics that are interpretation, not disclosure -------
#
# Some questions cannot be answered with High confidence out of this archive
# even when retrieval works perfectly, because the thing being asked about is a
# reading of the facts rather than a fact. Whether a company is "exposed" to a
# partner, whether capacity is "constrained", what a lawsuit "means" - those are
# judgements. A pack of analyst notes agreeing with each other is not the
# company's disclosure, and three closely-matching chunks should not produce the
# same confidence as a filing that states the number.
#
# The ceiling lifts only when the company's own material carries the answer.
# --- Phase 12.1: ceilings that come from the SHAPE of the question ----------
#
# The first live golden evaluation returned High confidence on six questions
# that cannot support it: five "what is the most recent development" questions
# and one "what risks does the research identify". Both are syntheses across
# many documents - which document is newest, what the notes collectively worry
# about - and a synthesis is a reading of the archive, not a disclosure in it.
#
# The Phase 8.1 interpretive ceiling already covered some of this, but it lifts
# when the company's own material carries the answer (`company_carries`). That
# escape is right for "is Microsoft exposed to OpenAI" and wrong here: a pack of
# ABBV company material does not make "the risks the research identifies" a
# disclosed fact, and a stack of filings does not make "the most recent
# development" anything other than a judgement about ordering.
#
# So these ceilings have NO escape. They are applied to the final grade after
# generation, because prompting a model not to claim High confidence is a
# request, and this needs to be a rule. Capping is monotone - cap_grade only
# ever lowers - so adding one can never raise a grade another rule has already
# lowered, and the Phase 5B.1 company-direct ceilings continue to apply
# untouched.
RECENCY_QUESTION_KEYWORDS = (
    "most recent", "latest", "recently", "recent development", "recent developments",
    "newest", "last reported", "when was it reported", "when was this reported",
    "what changed", "has changed", "changed most", "since when", "up to date",
    "current state", "as of now", "timeline", "chronology", "most current",
)
RISK_SYNTHESIS_KEYWORDS = (
    "what risks", "which risks", "risks does", "risks do", "risk factors",
    "credit metrics", "credit quality", "leverage", "debt maturities",
    "dividend coverage", "downside risks", "key risks",
)
ANALYST_CONCERN_KEYWORDS = (
    "what concerns", "which concerns", "concerns do", "concerns does", "analysts raise",
    "analysts worry", "analyst concern", "analyst concerns", "bear case", "bull case",
    "what do analysts", "sell-side view", "street view",
)

INTERPRETIVE_TOPIC_GROUPS = (
    ("third-party relationship", (
        "openai", "partner", "partners", "partnership", "partnerships", "joint venture",
        "alliance", "stake in", "equity stake", "exposure", "exposed", "reliance",
        "reliant", "dependence", "dependency", "dependent on", "counterparty",
        "concentration", "relationship with", "arrangement with", "agreement with")),
    ("legal or regulatory exposure", (
        "lawsuit", "lawsuits", "litigation", "legal", "regulator", "regulators",
        "regulatory", "antitrust", "investigation", "investigations", "probe",
        "subpoena", "settlement", "compliance", "penalty", "penalties", "fine",
        "sanction", "sanctions", "injunction")),
    ("investor concern", (
        "concern", "concerns", "concerned", "worried", "worry", "worries", "sentiment",
        "debate", "debated", "skeptic", "skeptical", "sceptic", "bear case", "bull case",
        "controversy", "controversial", "pushback", "backlash", "narrative")),
    ("strategic interpretation", (
        "strategy", "strategic", "positioning", "competitive", "competition", "moat",
        "threat", "threats", "disrupt", "disruption", "implication", "implications",
        "means for", "read-through", "readthrough", "what this means")),
    ("capacity or supply interpretation", (
        "capacity constraint", "capacity constraints", "capacity constrained",
        "constraint", "constraints", "constrained", "bottleneck", "bottlenecks",
        "shortage", "shortages", "supply constrained", "sold out", "backlog",
        "tightness", "tight supply")),
)

# --- Phase 8.1, Part A: questions that ask for an investment decision --------
#
# Phase 6 audited the ANSWER for investment language. That is one step too late:
# it catches the system after it has already given advice. This catches the
# QUESTION, before any model is called, and substitutes a refusal that is
# written here rather than generated. A boundary that depends on a model
# choosing to respect it is not a boundary.
RECOMMENDATION_ACTIONS = (
    "buy", "buys", "buying", "bought", "purchase", "purchasing",
    "sell", "sells", "selling", "sold", "dispose", "liquidate",
    "hold", "holding", "own", "owning", "keep", "keeping",
    "add", "adding", "accumulate", "accumulating", "build",
    "trim", "trimming", "cut", "cutting", "reduce", "reducing",
    "exit", "exiting", "initiate", "initiating", "start", "starting",
    "increase", "increasing", "decrease", "decreasing",
    "short", "shorting", "avoid", "avoiding",
    "overweight", "underweight", "rate", "rating", "recommend",
)
_ACTIONS = "|".join(sorted(RECOMMENDATION_ACTIONS, key=len, reverse=True))
# Who the action would be taken by. Deliberately the reader and the firm - a
# question about what MICROSOFT should buy is a research question, not a request
# for advice, and must not be refused.
_ASKER = (r"(?:cutler|we|i|you|us|the\s+fund|the\s+firm|the\s+portfolio|the\s+team|"
          r"the\s+partnership|our\s+(?:fund|firm|portfolio))")

RECOMMENDATION_PATTERNS = [
    (re.compile(rf"\bshould\s+{_ASKER}\s+(?:be\s+|now\s+|still\s+)*(?:{_ACTIONS})\b", re.I),
     "asks whether we should take an action"),
    (re.compile(rf"\b{_ASKER}\s+should\s+(?:{_ACTIONS})\b", re.I),
     "asks for an instruction to act"),
    (re.compile(rf"\bwhat\s+should\s+{_ASKER}\s+do\b", re.I),
     "asks what we should do"),
    (re.compile(rf"\bwould\s+you\s+(?:{_ACTIONS})\b", re.I),
     "asks for the system's own decision"),
    (re.compile(r"\b(?:do|would|can|could)\s+you\s+recommend\b", re.I),
     "asks for a recommendation"),
    (re.compile(r"\bwhat\s+(?:is|'s)\s+your\s+(?:rating|recommendation|call|verdict)\b", re.I),
     "asks for a rating or call"),
    (re.compile(r"\bis\s+(?:it|this|that|the\s+stock|the\s+shares|[a-z]{1,6})\s+a\s+"
                r"(?:buy|sell|hold)\b", re.I),
     "asks for a buy/sell/hold label"),
    (re.compile(r"\bbuy\s*,?\s*sell\s*,?\s*(?:or\s+)?hold\s*\?", re.I),
     "asks buy, sell or hold"),
    (re.compile(rf"\b(?:{_ACTIONS})\s+(?:more\s+|into\s+)?(?:our|the|its|this)\s+position\b",
                re.I),
     "asks about changing the position"),
    (re.compile(r"\b(?:increase|decrease|reduce|raise|lower)\s+(?:our|the)\s+"
                r"(?:position|weight|exposure|stake|holding)\b", re.I),
     "asks about sizing the position"),
    (re.compile(r"\b(?:worth|time\s+to)\s+(?:buying|selling|owning|adding|trimming|exiting|"
                r"holding)\b", re.I),
     "asks whether it is worth acting"),
    (re.compile(r"\bhow\s+(?:do|would|should)\s+you\s+rate\b", re.I),
     "asks for a rating"),
    (re.compile(r"\bprice\s+target\s+(?:imply|implies|suggest|suggests|mean|means)\b", re.I),
     "asks what a price target implies for action"),
    (re.compile(r"\b(?:based\s+on|given)\s+.{0,40}\bshould\s+" + _ASKER, re.I),
     "asks for a decision from the evidence"),
]

# The exact wording Phase 8.1 requires. It is a constant, not a template, so it
# cannot drift.
REFUSAL_SENTENCE = (
    "I cannot provide a buy/sell/hold recommendation or tell Cutler what action to take. "
    "I can only summarize what the retrieved sources say.")

# --- Phase 8.1, Part B: is the question's subject actually in the evidence? ---
#
# A retrieval system always returns its nearest chunks. Ask it about a segment
# that does not exist and it will hand back the closest real ones, and an
# obliging model will write a paragraph around them. The fix is not a list of
# forbidden words - it is checking whether the distinctive terms of the question
# appear in the evidence at all before anything is written.
QUESTION_STOPWORDS = frozenset("""
a an and any are as at be been being but by can could did do does doing done for from
had has have how in into is it its me my not of on or our ours over own so than that the
their them then there these they this those to under up was we were what when where which
while who whom why will with would you your yours about above after again against all also
am because before below between both during each few further here him his if more most no
nor now once only other out same she some such through too very
""".split())

# Words that appear in almost any research question. They are not what makes a
# question distinctive, so their absence from the evidence proves nothing.
GENERIC_QUESTION_TERMS = frozenset("""
report reports reported reporting disclose discloses disclosed disclosure disclosures
say says said state states stated statement statements text source sources evidence
retrieved document documents documentation filing filings company companies business
segment segments metric metrics number numbers figure figures data detail details
revenue revenues sales growth growing grew guidance guide guided outlook result results
quarter quarterly year years yearly fiscal annual latest recent recently current
total amount level levels rate rates change changes trend trends performance
information anything something view views comment commentary discussion mention mentions
""".split())

RE_WORD = re.compile(r"[a-z0-9][a-z0-9&.\-]*", re.I)
MIN_SUBJECT_TERM_CHARS = 4

# Language the answer must never contain. Flagged, never silently rewritten.
# Phase 6.1. "sell-side" is what a whole category of Cutler's archive is called,
# and an answer describing a sell-side note is not making a sell call. These
# phrases are masked out of the text BEFORE the patterns below ever see it, so
# they cannot trigger anything, however the surrounding sentence is worded.
SAFE_LANGUAGE_PHRASES = (
    # Phase 8.2. The longer forms are listed explicitly even though masking
    # "sell-side" already neutralises all of them - an audit rule that a person
    # has to derive is an audit rule a person will misread.
    "is a sell-side research note", "is a sell side research note",
    "is a sellside research note",
    "appears in a sell-side research note", "appears in a sell side research note",
    "appears in a sellside research note",
    "from a sell-side research note", "from a sell side research note",
    "from a sellside research note",
    "is a sell-side summary", "is a sell side summary", "is a sellside summary",
    "is a sell-side note", "is a sell side note", "is a sellside note",
    "appears in a sell-side note", "appears in a sell side note",
    "appears in a sellside note",
    "from a sell-side note", "from a sell side note", "from a sellside note",
    "is a sell-side", "is a sell side", "is a sellside",
    "sell-side", "sell side", "sellside",
    "buy-side", "buy side", "buyside",
    "sell-through", "sell through",
    # Phase 8.1. A slash- or comma-joined enumeration NAMES the category of
    # thing being refused ("I cannot provide a buy/sell/hold recommendation").
    # It is the opposite of making a call, and the refusal this system is
    # required to give must not be flagged by the audit that exists to catch
    # calls.
    "buy/sell/hold", "buy / sell / hold", "buy-sell-hold",
    "buy, sell or hold", "buy, sell, or hold", "buy, sell and hold",
)
# Longest first: Python's alternation takes the first branch that matches at a
# position, so an unsorted list would mask "sell-side" and leave "research
# note" dangling rather than masking the whole phrase.
RE_SAFE_LANGUAGE = re.compile(
    "|".join(re.escape(phrase) for phrase in
             sorted(SAFE_LANGUAGE_PHRASES, key=len, reverse=True)), re.I)
SAFE_LANGUAGE_MASK = "«vendor»"

# Phase 8.2. What actually caused the surviving false positive.
#
# "sell-side" was already masked, and masked correctly - but only when the
# hyphen is an ASCII hyphen. The model writes a non-breaking hyphen (U+2011) or
# an en dash often enough that Phase 7A.1 had to fix a Windows console crash
# over the same character, and markdown wrapping can leave "sell-" at the end of
# one line and "side" at the start of the next. In every one of those cases the
# ASCII mask misses, the word "sell" survives with a word boundary after it, and
# "is a sell-side note" is read as a sell call.
#
# So the fix is not a longer list of phrases - a longer list in ASCII would have
# missed it too. The fix is to make the text ASCII-shaped before any mask or
# pattern touches it.
UNICODE_DASHES = "‐‑‒–—―⁃−﹘﹣－"
UNICODE_SPACES = "            "
UNICODE_SPACES += "   　"
AUDIT_TRANSLATION = {ord(ch): "-" for ch in UNICODE_DASHES}
AUDIT_TRANSLATION.update({ord(ch): " " for ch in UNICODE_SPACES})
AUDIT_TRANSLATION.update({
    0x00AD: "",      # soft hyphen - invisible, and it splits words in place
    0x200B: "",      # zero-width space
    0x200C: "", 0x200D: "", 0xFEFF: "",
    0x2018: "'", 0x2019: "'", 0x201C: '"', 0x201D: '"',
})
RE_HYPHEN_LINE_WRAP = re.compile(r"-\s*\n\s*")
RE_WHITESPACE_RUN = re.compile(r"\s+")

# Only actual investment-action language belongs here. Describing what a
# document says is the job; telling Cutler what to do with the security is not.
FORBIDDEN_PATTERNS = [
    # Phase 8.2: the optional word in front is captured so the flag reads
    # "stock is a buy" rather than a bare "is a buy". A reader looking at the
    # review queue needs to see WHAT was called, not just that something was.
    (re.compile(r"(?:\b[\w'-]+\s+)?\b(?:is|are)\s+an?\s+(?:buy|sell|hold)\b", re.I),
     "buy/sell/hold call"),
    (re.compile(r"\b(we|i|cutler)\s+(recommend|advise|suggest)\s+"
                r"(buying|selling|holding|owning|adding|trimming|exiting|reducing|"
                r"initiating|avoiding|shorting|a\s+(long|short)\b)", re.I), "recommendation"),
    (re.compile(r"\bcutler\s+should\s+"
                r"(buy|sell|hold|own|add|trim|exit|reduce|increase|initiate|avoid|short|"
                r"overweight|underweight)\b", re.I), "instruction to Cutler"),
    (re.compile(r"\bshould\s+(buy|sell|hold|own|add|trim|exit|reduce|increase|initiate|"
                r"short|avoid|accumulate)\b", re.I), "trade instruction"),
    (re.compile(r"\bprice\s+target\s+(of|is|implies|imply|suggests)\b", re.I), "price target"),
    (re.compile(r"\b(buy|sell|hold)\s+(rating|recommendation|call)\b", re.I),
     "rating language"),
    (re.compile(r"\b(overweight|underweight|outperform|underperform)\b", re.I), "rating language"),
    (re.compile(r"\bthis\s+proves\b", re.I), "overclaim: 'this proves'"),
    (re.compile(r"\bthe\s+company\s+will\b", re.I), "prediction: 'the company will'"),
    (re.compile(r"\bthe\s+market\s+will\b", re.I), "prediction: 'the market will'"),
]

# Phase 6.1. The directness cap reads the answer's own verdict on its evidence,
# and that verdict lives in the Direct Answer (and its restatement in
# Confidence). Evidence Limitations is SUPPOSED to say what the evidence does
# not include - that is the section doing its job, not a signal that the
# question went unanswered.
DIRECTNESS_SECTIONS = ("Direct Answer", "Confidence")

RE_CITATION = re.compile(r"\[(\d{1,3})\]")
SAFE_TICKER = re.compile(r"^[A-Za-z0-9._-]{1,15}$")
RE_UNSAFE_FILE = re.compile(r"[^A-Za-z0-9._-]+")

ANSWER_SECTIONS = [
    "Direct Answer",
    "Evidence Used",
    "Source Citations",
    "Evidence Limitations",
    "Needs Human Review",
    "Confidence",
]

RUN_COLUMNS = [
    "run_id", "ticker", "question", "answer_model", "embedding_model", "top_k",
    "selected_chunks", "selected_documents", "prompt_tokens_estimate",
    "output_tokens_estimate", "estimated_cost_usd", "answer_path",
    "obsidian_answer_path", "retrieval_log_path", "created_at", "status",
    "error_message", "confidence_stated", "confidence_rule", "confidence_rule_reason",
    "source_priority", "citations_used", "flagged_language", "pipeline_version",
    # --- Phase 6 -----------------------------------------------------------
    "retrieval_mode", "retrieval_mode_effective", "answer_directness_flag",
    "directness_cap_applied", "directness_sections", "final_confidence", "filter_summary",
    "hybrid_diagnostics_path", "audit_flags", "candidates_considered",
    "avg_vector_score", "avg_keyword_score", "avg_hybrid_score",
    # --- Phase 8.1 ---------------------------------------------------------
    "recommendation_refused", "unsupported_subject", "subject_terms_missing",
    "confidence_ceilings", "answer_generated",
    # --- Phase 14.3.3 ------------------------------------------------------
    # The API's own verdict on the generation, and the deterministic guard's.
    # Recorded so that an answer which looks cut off can be diagnosed from the
    # run row instead of by paying to ask the question again.
    "generation_status", "generation_incomplete", "generation_incomplete_reason",
    "answer_max_output_tokens", "answer_retry_used",
    # --- Phase 14.3.5 ------------------------------------------------------
    "compliance_retry_used", "compliance_retry_reason", "answer_model_calls",
]


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def setup_logging(log_path: Path, verbose: bool = False) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    LOGGER.setLevel(logging.DEBUG)
    LOGGER.handlers.clear()

    file_handler = logging.FileHandler(log_path, mode="a", encoding="utf-8")
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s  %(levelname)-8s  %(message)s", "%Y-%m-%d %H:%M:%S"))
    LOGGER.addHandler(file_handler)

    console = logging.StreamHandler(stream=sys.stdout)
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    console.setFormatter(logging.Formatter("%(levelname)-8s %(message)s"))
    LOGGER.addHandler(console)


# ---------------------------------------------------------------------------
# Path safety
# ---------------------------------------------------------------------------

def is_relative_to(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except (ValueError, OSError):
        return False


def validate_project_root(project_root: Path) -> None:
    if not project_root.exists():
        raise SystemExit(f"ERROR: --project-root does not exist: {project_root}")
    drive = os.path.splitdrive(str(project_root.resolve()))[0].upper().rstrip(":")
    if drive == "V":
        raise SystemExit(
            f"ERROR: --project-root is on the V Drive ({project_root}). "
            "The V Drive is read-only. Aborting."
        )


def assert_inside_project(path: Path, project_root: Path) -> None:
    if not is_relative_to(path, project_root):
        raise SystemExit(f"ERROR: refusing to write outside the project root: {path}")


def safe_ticker(value: str) -> str:
    ticker = (value or "").strip().upper()
    if not SAFE_TICKER.match(ticker):
        raise SystemExit(f"ERROR: --ticker must be a short alphanumeric symbol, got: {value!r}")
    return ticker


def timestamp_slug(moment: datetime) -> str:
    return moment.strftime("%Y%m%d_%H%M%S")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(moment: datetime) -> str:
    return moment.replace(microsecond=0).isoformat()


# ---------------------------------------------------------------------------
# Environment / OpenAI client
# ---------------------------------------------------------------------------

def _float_env(name: str, default: float) -> float:
    try:
        raw = os.environ.get(name, "").strip()
        return float(raw) if raw else default
    except ValueError:
        LOGGER.warning("%s is not a number; using %.4f", name, default)
        return default


def load_environment(project_root: Path) -> dict:
    """Read .env. The API key is returned but NEVER logged or printed."""
    try:
        from dotenv import load_dotenv  # noqa: PLC0415
        env_path = project_root / ".env"
        if env_path.exists():
            load_dotenv(env_path)
            LOGGER.info("Loaded environment from %s", env_path.name)
        else:
            load_dotenv()
            LOGGER.warning("No .env found in %s - falling back to the process environment.",
                           project_root)
    except ImportError:
        LOGGER.warning("python-dotenv is not installed; using the process environment only.")

    settings = {
        "api_key": os.environ.get("OPENAI_API_KEY", "").strip(),
        "embedding_model": (os.environ.get("OPENAI_EMBEDDING_MODEL", "").strip()
                            or DEFAULT_EMBEDDING_MODEL),
        "answer_model": (os.environ.get("OPENAI_ANSWER_MODEL", "").strip()
                         or DEFAULT_ANSWER_MODEL),
        "base_url": os.environ.get("OPENAI_BASE_URL", "").strip(),
        "timeout": _float_env("OPENAI_TIMEOUT_SECONDS", 120.0),
        "reasoning_effort": os.environ.get("OPENAI_ANSWER_REASONING_EFFORT", "").strip(),
        "embed_cost_per_1m": _float_env("OPENAI_EMBEDDING_COST_PER_1M",
                                        DEFAULT_EMBED_COST_PER_1M),
        "answer_input_cost_per_1m": _float_env("OPENAI_ANSWER_INPUT_COST_PER_1M",
                                               DEFAULT_ANSWER_INPUT_COST_PER_1M),
        "answer_output_cost_per_1m": _float_env("OPENAI_ANSWER_OUTPUT_COST_PER_1M",
                                                DEFAULT_ANSWER_OUTPUT_COST_PER_1M),
        "max_output_tokens": int(_float_env("OPENAI_ANSWER_MAX_OUTPUT_TOKENS",
                                            DEFAULT_MAX_OUTPUT_TOKENS)),
    }

    # Only ever report the SHAPE of the key, never any part of its value.
    LOGGER.info("Embedding model: %s", settings["embedding_model"])
    LOGGER.info("Answer model   : %s", settings["answer_model"])
    LOGGER.info("OPENAI_API_KEY : %s", "present" if settings["api_key"] else "MISSING")
    return settings


def make_openai_client(settings: dict):
    """Create the OpenAI client. Used for embeddings + responses, nothing else."""
    if not settings["api_key"]:
        raise SystemExit(
            "ERROR: OPENAI_API_KEY is not set.\n"
            "  1. Copy .env.example to .env in the project root\n"
            "  2. Put your key in OPENAI_API_KEY\n"
            "  (the key is never printed or logged by these scripts)"
        )
    try:
        from openai import OpenAI  # noqa: PLC0415
    except ImportError as exc:
        raise SystemExit(
            "ERROR: the openai package is not installed.\n"
            "  pip install -r requirements.txt\n"
            f"  ({exc})"
        ) from exc

    kwargs = {"api_key": settings["api_key"], "timeout": settings["timeout"]}
    if settings["base_url"]:
        kwargs["base_url"] = settings["base_url"]
    return OpenAI(**kwargs)


def estimate_tokens(text: str) -> int:
    """Character estimate. Used only when the API does not report usage."""
    return max(1, len(text or "") // FALLBACK_CHARS_PER_TOKEN)


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def connect(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise SystemExit(
            f"ERROR: database not found: {db_path}\n"
            "  Phase 1 through 5A must have run before Ask Brain can be used."
        )
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def create_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ask_brain_runs (
            run_id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker                  TEXT,
            question                TEXT,
            answer_model            TEXT,
            embedding_model         TEXT,
            top_k                   INTEGER,
            selected_chunks         INTEGER,
            selected_documents      INTEGER,
            prompt_tokens_estimate  INTEGER,
            output_tokens_estimate  INTEGER,
            estimated_cost_usd      REAL,
            answer_path             TEXT,
            obsidian_answer_path    TEXT,
            retrieval_log_path      TEXT,
            created_at              TEXT,
            status                  TEXT,
            error_message           TEXT,
            confidence_stated       TEXT,
            confidence_rule         TEXT,
            confidence_rule_reason  TEXT,
            retrieval_mode          TEXT,
            retrieval_mode_effective TEXT,
            answer_directness_flag  TEXT,
            directness_cap_applied  TEXT,
            directness_sections     TEXT,
            final_confidence        TEXT,
            filter_summary          TEXT,
            hybrid_diagnostics_path TEXT,
            audit_flags             TEXT,
            candidates_considered   INTEGER,
            avg_vector_score        REAL,
            avg_keyword_score       REAL,
            avg_hybrid_score        REAL,
            source_priority         TEXT,
            citations_used          TEXT,
            flagged_language        TEXT,
            pipeline_version        TEXT
        )
    """)
    # Migrate an older table in place rather than dropping anything.
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(ask_brain_runs)")}
    for column in RUN_COLUMNS:
        if column not in existing and column != "run_id":
            conn.execute(f"ALTER TABLE ask_brain_runs ADD COLUMN {column} TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ask_brain_ticker ON ask_brain_runs(ticker)")
    conn.commit()


def require_index(conn: sqlite3.Connection, ticker: str) -> dict:
    """Confirm Phase 5A actually built an index for this ticker."""
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS chunks, COUNT(DISTINCT document_id) AS docs, "
            "       MAX(embedding_model) AS model, MAX(chroma_collection) AS collection "
            "FROM rag_chunks WHERE ticker = ? AND embedding_status = 'embedded'",
            (ticker,),
        ).fetchone()
    except sqlite3.OperationalError as exc:
        raise SystemExit(
            f"ERROR: no rag_chunks table in the database ({exc}).\n"
            f"  {NOT_INDEXED_MESSAGE} Phase 5A has not run for any ticker.\n"
            f"  py -3.11 scripts\\build_rag_index.py --ticker {ticker} ..."
        ) from exc

    if not row or not row["chunks"]:
        # The FIRST of the three ways a ticker can turn out to be unindexed:
        # no embedded chunk rows, no Chroma store, or a store with no
        # collection. All three say the same sentence, because to the person
        # asking they are the same situation.
        raise SystemExit(
            f"{NOT_INDEXED_MESSAGE}\n"
            f"  Ticker: {ticker}\n"
            f"  There are no embedded chunks for it in the database.\n"
            f"  Index it first:\n"
            f"    py -3.11 scripts\\build_ticker_batch.py --project-root <root> "
            f"--db <db> --tickers {ticker} --embed --build-keyword --max-total-cost 1"
        )
    LOGGER.info("Phase 5A index: %d embedded chunks across %d documents.",
                row["chunks"], row["docs"])
    return {"chunks": row["chunks"], "documents": row["docs"],
            "embedding_model": row["model"], "collection": row["collection"]}


def fetch_chunk_rows(conn: sqlite3.Connection, chunk_ids: list, ticker: str) -> dict:
    """Chunk metadata straight from the database, which is the source of truth.

    Phase 12: the ticker is part of the lookup now.

    Phase 11.3 rekeyed rag_chunks on (chunk_id, ticker) so that a document two
    tickers share can be indexed for both. chunk_id is "{document_id}_c{index}"
    and therefore no longer identifies ONE row: the same id exists once per
    ticker that holds the document. Selecting by chunk_id alone returns every
    copy, and this dict is keyed by chunk_id, so the last row read wins - which
    means MSFT's retrieval could be handed AMZN's metadata for the same chunk.
    That is precisely the cross-ticker contamination Phase 12 exists to detect,
    so it is scoped here rather than measured. The ticker is REQUIRED - there is
    no unscoped fallback, because a fallback is the bug with a default.
    """
    if not chunk_ids:
        return {}
    out = {}
    step = 400
    for start in range(0, len(chunk_ids), step):
        window = chunk_ids[start:start + step]
        marks = ",".join("?" * len(window))
        rows = conn.execute(
            f"SELECT * FROM rag_chunks WHERE chunk_id IN ({marks}) AND UPPER(ticker) = ?",
            (*window, ticker.upper()))
        for row in rows:
            out[row["chunk_id"]] = dict(row)
    return out


# ---------------------------------------------------------------------------
# Chroma - read only
# ---------------------------------------------------------------------------

def collection_name_for(ticker: str) -> str:
    return f"cutler_research_brain_{ticker.lower()}_2026"


def open_collection(project_root: Path, ticker: str, collection_name: str):
    """Open the EXISTING Phase 5A collection. get_collection, never create."""
    try:
        import chromadb  # noqa: PLC0415
    except ImportError as exc:
        raise SystemExit("ERROR: chromadb is not installed.\n"
                         "  pip install -r requirements.txt\n"
                         f"  ({exc})") from exc

    path = project_root / "06_RAG_Index" / "chroma" / ticker
    if not path.exists():
        # Phase 11. One wording, so the UI and the command line say the same
        # thing, and NO FALLBACK: answering an AT&T question out of the MSFT
        # index would be worse than not answering at all.
        raise SystemExit(
            f"{NOT_INDEXED_MESSAGE}\n"
            f"  Ticker: {ticker}\n"
            f"  There is no Chroma store at {path}.\n"
            f"  Index it first:\n"
            f"    py -3.11 scripts\\build_ticker_batch.py --project-root <root> "
            f"--db <db> --tickers {ticker} --embed --build-keyword --max-total-cost 1"
        )
    client = chromadb.PersistentClient(path=str(path))
    try:
        return client.get_collection(name=collection_name)
    except Exception as exc:  # noqa: BLE001 - missing collection
        available = ""
        try:
            available = "\n  Collections present: " + (
                ", ".join(c.name for c in client.list_collections()) or "(none)")
        except Exception:  # noqa: BLE001
            pass
        raise SystemExit(f"{NOT_INDEXED_MESSAGE}\n"
                         f"  Ticker: {ticker}\n"
                         f"  The store at {path} has no collection "
                         f"'{collection_name}'.{available}\n"
                         f"  ({type(exc).__name__})") from exc


def embed_question(client, model: str, question: str) -> list:
    """ONE embeddings call, for the question only. No document is re-embedded."""
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = client.embeddings.create(model=model, input=[question])
            return list(response.data[0].embedding)
        except Exception as exc:  # noqa: BLE001 - network, auth, rate limit
            last_error = exc
            wait = RETRY_BASE_SECONDS * (2 ** (attempt - 1))
            if attempt < MAX_RETRIES:
                LOGGER.warning("Query embedding attempt %d/%d failed (%s). Retrying in %.0fs.",
                               attempt, MAX_RETRIES, type(exc).__name__, wait)
                time.sleep(wait)
            else:
                LOGGER.error("Query embedding failed after %d attempts (%s).",
                             MAX_RETRIES, type(exc).__name__)
    raise RuntimeError(f"query embedding failed: {type(last_error).__name__}: {last_error}")


# ---------------------------------------------------------------------------
# Phase 6: metadata filters
# ---------------------------------------------------------------------------

def describe_filters(args) -> str:
    """One human-readable line naming every filter in force."""
    parts = []
    if args.date_from:
        parts.append(f"date-from {args.date_from}")
    if args.date_to:
        parts.append(f"date-to {args.date_to}")
    for value in args.source_type or []:
        parts.append(f'source-type "{value}"')
    for value in args.document_type or []:
        parts.append(f'document-type "{value}"')
    if args.must_contain:
        parts.append(f'must-contain "{args.must_contain}"')
    # getattr, not args.restrict_to_documents: this function is called with
    # hand-built namespaces in several test suites, and a filter added later
    # must not make an older caller raise AttributeError.
    restrict = list(getattr(args, "restrict_to_documents", None) or [])
    if restrict:
        parts.append("restrict-to-documents " + ",".join(restrict))
    return "; ".join(parts) or "none"


def valid_date(value: str, flag: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        raise SystemExit(f"ERROR: {flag} must be YYYY-MM-DD, got: {value!r}") from None
    return value


def filter_chunks(conn: sqlite3.Connection, ticker: str, args) -> tuple:
    """Work out which chunks a run is allowed to retrieve, and why.

    Filters are applied BEFORE ranking, not after it, so a filtered run gets a
    full top-k of eligible chunks rather than whatever survives from an
    unfiltered top-k.

    Returns (allowed_chunk_ids or None, diagnostics). None means "no filter" -
    which is not the same as "everything passed", and the caller does not have
    to care about the difference.
    """
    diagnostics = {"filters": describe_filters(args), "per_filter": {}, "combined": None}

    base_sql = "FROM rag_chunks WHERE ticker = ? AND COALESCE(chunk_text,'') <> ''"
    total = conn.execute(f"SELECT COUNT(*) {base_sql}", (ticker,)).fetchone()[0]
    diagnostics["indexed_chunks"] = total

    clauses = []
    params = [ticker]
    if args.date_from:
        clauses.append(("date-from", "COALESCE(folder_date_guess,'') >= ?", [args.date_from]))
    if args.date_to:
        clauses.append(("date-to", "COALESCE(folder_date_guess,'') <> '' AND "
                                   "COALESCE(folder_date_guess,'') <= ?", [args.date_to]))
    if args.source_type:
        marks = ",".join("?" * len(args.source_type))
        clauses.append(("source-type", f"COALESCE(source_type_guess,'') IN ({marks})",
                        list(args.source_type)))
    if args.document_type:
        marks = ",".join("?" * len(args.document_type))
        clauses.append(("document-type", f"COALESCE(document_type_guess,'') IN ({marks})",
                        list(args.document_type)))
    if args.must_contain:
        clauses.append(("must-contain", "chunk_text LIKE ?", [f"%{args.must_contain}%"]))
    restrict = [str(value).strip() for value in
                (getattr(args, "restrict_to_documents", None) or []) if str(value).strip()]
    if restrict:
        marks = ",".join("?" * len(restrict))
        clauses.append(("restrict-to-documents",
                        f"COALESCE(document_id,'') IN ({marks})", restrict))

    if not clauses:
        diagnostics["combined"] = total
        return None, diagnostics

    # How many chunks each filter would allow ON ITS OWN. This is what makes a
    # zero-evidence run explainable: it says which filter did the damage.
    for label, sql, values in clauses:
        count = conn.execute(f"SELECT COUNT(*) {base_sql} AND ({sql})",
                             [ticker] + values).fetchone()[0]
        diagnostics["per_filter"][label] = count

    combined_sql = " AND ".join(f"({sql})" for _, sql, _ in clauses)
    for _, _, values in clauses:
        params.extend(values)
    rows = conn.execute(f"SELECT chunk_id {base_sql} AND {combined_sql}", params).fetchall()
    allowed = [r["chunk_id"] for r in rows]
    diagnostics["combined"] = len(allowed)
    return allowed, diagnostics


def explain_empty_filters(diagnostics: dict) -> str:
    """Say plainly which filter emptied the candidate set."""
    per_filter = diagnostics.get("per_filter") or {}
    if not per_filter:
        return "No filter was applied, so the index itself returned nothing."
    zeros = [name for name, count in per_filter.items() if count == 0]
    lines = [f"The filters in force were: {diagnostics.get('filters')}.",
             f"The {diagnostics.get('indexed_chunks', 0)} indexed chunks were reduced to 0."]
    for name, count in per_filter.items():
        lines.append(f"  - {name} on its own matches {count} chunk(s)")
    if zeros:
        lines.append("Nothing matches " + ", ".join(zeros) + " at all - that filter is the cause.")
    else:
        lines.append("Each filter matches something on its own, so it is the COMBINATION that "
                     "matches nothing. Try relaxing one of them.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Phase 6: retrieval - vector, keyword, hybrid
# ---------------------------------------------------------------------------

def load_keyword_index(project_root: Path, ticker: str, conn: sqlite3.Connection,
                       required: bool):
    """Load the Phase 6 keyword index, or explain why there isn't one."""
    try:
        import build_keyword_index as kw  # noqa: PLC0415 - sibling script
    except ImportError as exc:
        message = ("scripts\\build_keyword_index.py could not be imported "
                   f"({type(exc).__name__}: {exc})")
        if required:
            raise SystemExit(f"ERROR: {message}") from exc
        LOGGER.warning("%s - falling back to vector-only retrieval.", message)
        return None, None

    try:
        index = kw.load_index(project_root, ticker)
    except FileNotFoundError as exc:
        message = (f"no keyword index at {exc}. Build it with:\n"
                   f"  py -3.11 scripts\\build_keyword_index.py --ticker {ticker} "
                   f'--project-root "{project_root}"')
        if required:
            raise SystemExit(f"ERROR: {message}")
        LOGGER.warning("No keyword index found - falling back to vector-only retrieval. "
                       "Build one with build_keyword_index.py to enable hybrid.")
        return None, None
    except (json.JSONDecodeError, KeyError) as exc:
        if required:
            raise SystemExit(f"ERROR: the keyword index is unreadable "
                             f"({type(exc).__name__}). Rebuild it with --force.") from exc
        LOGGER.warning("Keyword index unreadable (%s) - falling back to vector-only.",
                       type(exc).__name__)
        return None, None

    # A stale index is worse than no index, because it silently under-retrieves.
    current_ids = [r[0] for r in conn.execute(
        "SELECT chunk_id FROM rag_chunks WHERE ticker = ? AND COALESCE(chunk_text,'') <> ''",
        (ticker,))]
    up_to_date, missing, stale = kw.index_is_current(index, current_ids)
    if not up_to_date:
        LOGGER.warning("The keyword index is out of date: %d chunk(s) in the database are not "
                       "in it, %d indexed chunk(s) no longer exist. Rebuild it with "
                       "build_keyword_index.py --force. Continuing with what it has.",
                       len(missing), len(stale))
    index["_stale"] = not up_to_date
    return kw, index


def retrieve_vector(collection, vector, top_k: int, allowed_chunk_ids) -> list:
    """Chroma nearest neighbours. Oversamples when a filter is in force."""
    n = top_k
    if allowed_chunk_ids is not None:
        try:
            size = collection.count()
        except Exception:  # noqa: BLE001
            size = top_k * VECTOR_OVERSAMPLE
        n = min(max(top_k * VECTOR_OVERSAMPLE, top_k), max(1, size))
    try:
        results = collection.query(
            query_embeddings=[vector], n_results=max(1, n),
            include=["documents", "metadatas", "distances"])
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Chroma query failed ({type(exc).__name__}: {exc})") from exc

    ids = (results.get("ids") or [[]])[0]
    documents = (results.get("documents") or [[]])[0]
    metadatas = (results.get("metadatas") or [[]])[0]
    distances = (results.get("distances") or [[]])[0]

    allowed = set(allowed_chunk_ids) if allowed_chunk_ids is not None else None
    out = []
    for position, chunk_id in enumerate(ids):
        if allowed is not None and chunk_id not in allowed:
            continue
        distance = distances[position] if position < len(distances) else None
        similarity = (1.0 - float(distance)) if isinstance(distance, (int, float)) else 0.0
        out.append({
            "chunk_id": chunk_id,
            "metadata": dict(metadatas[position] or {}) if position < len(metadatas) else {},
            "text": documents[position] if position < len(documents) else "",
            "distance": distance,
            "vector_score": similarity,
        })
        if len(out) >= top_k:
            break
    for rank, item in enumerate(out, start=1):
        item["vector_rank"] = rank
    return out


def retrieve_keyword(kw, index, conn: sqlite3.Connection, question: str, top_k: int,
                     allowed_chunk_ids, ticker: str) -> list:
    """BM25 over the Phase 6 keyword index, with the exact-phrase boost.

    Phase 12: scoped by ticker - see fetch_chunk_rows. Since Phase 11.3 the same
    chunk_id can exist under two tickers, and the phrase boost must read THIS
    ticker's copy of the text.
    """
    results = kw.search(index, question, limit=max(1, top_k * 2),
                        allowed_chunk_ids=allowed_chunk_ids)
    if not results:
        return []
    texts = {}
    ids = [r["chunk_id"] for r in results]
    step = 400
    for start in range(0, len(ids), step):
        window = ids[start:start + step]
        marks = ",".join("?" * len(window))
        rows = conn.execute(
            f"SELECT chunk_id, chunk_text FROM rag_chunks "
            f" WHERE chunk_id IN ({marks}) AND UPPER(ticker) = ?",
            (*window, ticker.upper()))
        for row in rows:
            texts[row["chunk_id"]] = row["chunk_text"] or ""
    results = kw.apply_phrase_boost(results, texts)
    return results[:max(1, top_k)]


def normalise_scores(values: dict) -> dict:
    """Min-max to 0..1 over the values actually present.

    Chunks that one retriever never returned are not in `values` at all; the
    caller gives them 0. That is the honest reading - "this retriever did not
    find it" - rather than pretending it scored badly.
    """
    if not values:
        return {}
    lowest = min(values.values())
    highest = max(values.values())
    if highest - lowest < 1e-12:
        # Every candidate scored the same. Flat 1.0 if there is any signal at
        # all, flat 0.0 if there is not.
        return {key: (1.0 if highest > 0 else 0.0) for key in values}
    return {key: (value - lowest) / (highest - lowest) for key, value in values.items()}


def priority_component(kind: str, preference: str) -> float:
    if preference == "none":
        return PRIORITY_NEUTRAL
    if preference == "both":
        return PRIORITY_MATCH if kind in ("company_direct", "analyst") else PRIORITY_NEUTRAL
    if preference == "company":
        return {"company_direct": PRIORITY_MATCH, "analyst": PRIORITY_AGAINST}.get(
            kind, PRIORITY_NEUTRAL)
    if preference == "analyst":
        return {"analyst": PRIORITY_MATCH, "company_direct": PRIORITY_AGAINST}.get(
            kind, PRIORITY_NEUTRAL)
    return PRIORITY_NEUTRAL


def recency_component(candidates: list) -> dict:
    """Newest folder_date_guess = 1.0, oldest = 0.0, undated = 0.0.

    Undated chunks are not punished for being old - they are simply given no
    recency credit, because nothing is known about their date.
    """
    dates = {}
    for cand in candidates:
        raw = (cand.get("folder_date_guess") or "").strip()
        try:
            dates[cand["chunk_id"]] = datetime.strptime(raw, "%Y-%m-%d").toordinal()
        except (ValueError, TypeError):
            continue
    if not dates:
        return {}
    return normalise_scores({k: float(v) for k, v in dates.items()})


# ---------------------------------------------------------------------------
# Question intent and evidence selection
# ---------------------------------------------------------------------------

def classify_question(question: str) -> dict:
    """Deterministic keyword rules. No model is asked what the question means."""
    text = " " + (question or "").lower() + " "
    company_hits = sorted({k for k in COMPANY_INTENT_KEYWORDS if k in text})
    analyst_hits = sorted({k for k in ANALYST_INTENT_KEYWORDS if k in text})
    if company_hits and not analyst_hits:
        intent = "company_reported"
    elif analyst_hits and not company_hits:
        intent = "analyst_view"
    elif company_hits and analyst_hits:
        intent = "mixed"
    else:
        intent = "unclassified"
    return {"intent": intent, "company_keyword_hits": company_hits,
            "analyst_keyword_hits": analyst_hits}


def preference_for(source_priority: str, intent: str) -> str:
    """What kind of source this run should lean towards.

    An explicit --source-priority ALWAYS wins over the automatic reading of the
    question. 'balanced' hands the decision to the question's own wording.

    'both' is what a mixed question gets: it prefers classified material of
    either kind over unclassified text, and tilts against neither. 'none' means
    rank on similarity alone.
    """
    if source_priority in ("none", "company", "analyst"):
        return source_priority
    if intent == "company_reported":
        return "company"
    if intent == "analyst_view":
        return "analyst"
    if intent == "mixed":
        return "both"
    return "none"


def detect_recommendation_request(question: str) -> dict:
    """Phase 8.1, Part A. Does this question ask what Cutler should DO?

    Deterministic patterns, run on the question before anything is retrieved or
    generated. Nothing about the outcome depends on a model's cooperation.

    The patterns are scoped to the reader and the firm on purpose. "Should
    Microsoft buy more capacity?" is a research question about the company and
    is answered normally; "should we buy Microsoft?" is a request for advice and
    is refused.
    """
    text = " " + (question or "").strip().lower() + " "
    matched = [label for pattern, label in RECOMMENDATION_PATTERNS if pattern.search(text)]
    return {"is_request": bool(matched), "matched": matched,
            "reason": "; ".join(matched)}


def subject_terms(question: str, ticker: str) -> list:
    """The words that make this question this question.

    Stopwords go, the ticker and its obvious aliases go, and the vocabulary that
    appears in every research question goes. What is left is the subject: the
    segment, metric, product, counterparty or event being asked about.
    """
    aliases = {(ticker or "").lower(), "microsoft", "msft", "corp", "corporation", "inc"}
    terms = []
    for raw in RE_WORD.findall((question or "").lower()):
        word = raw.strip(".-&")
        if len(word) < MIN_SUBJECT_TERM_CHARS:
            continue
        if word in QUESTION_STOPWORDS or word in GENERIC_QUESTION_TERMS or word in aliases:
            continue
        if word not in terms:
            terms.append(word)
    return terms


def term_in_text(term: str, haystack: str) -> bool:
    """Present, allowing for the plural the question happened to use.

    Deliberately crude. A looser match (stemming, synonyms) would let the guard
    below be talked into finding a subject that is not there, and the whole
    point of the guard is that it cannot be talked into anything.
    """
    if term in haystack:
        return True
    if term.endswith("s") and term[:-1] in haystack:
        return True
    return f"{term}s" in haystack


def check_subject_support(question: str, selected: list, ticker: str) -> dict:
    """Phase 8.1, Part B. Is the subject of the question in the evidence at all?

    Retrieval always returns something. That is its job - it ranks, it does not
    judge. So the nonsense question comes back with the nearest real chunks
    attached, and every downstream part of the system then treats them as
    though they were about what was asked.

    This is the check that says otherwise: if not one distinctive term from the
    question appears anywhere in the selected text, the evidence is not about
    this subject, whatever it scored.

    A partial miss is normal and is NOT flagged - a question naming four things
    where the evidence covers two is an ordinary question. Only a complete miss
    counts, which makes this a blunt instrument that fires rarely and is very
    hard to argue with when it does.
    """
    terms = subject_terms(question, ticker)
    haystack = " ".join((c.get("chunk_text") or "") for c in selected).lower()

    present = [t for t in terms if term_in_text(t, haystack)]
    missing = [t for t in terms if t not in present]

    unsupported = bool(terms) and not present and bool(selected)
    return {
        "terms": terms, "present": present, "missing": missing,
        "unsupported": unsupported,
        "reason": ("not one distinctive term from the question appears in the selected "
                   "evidence: " + ", ".join(f"'{t}'" for t in missing)) if unsupported else "",
    }


def interpretive_topics(question: str) -> list:
    """Which kinds of interpretation this question is asking for, if any."""
    text = " " + (question or "").lower() + " "
    found = []
    for name, keywords in INTERPRETIVE_TOPIC_GROUPS:
        if any(keyword in text for keyword in keywords):
            found.append(name)
    return found


def chunk_kind(meta: dict) -> str:
    source = (meta.get("source_type_guess") or "").strip()
    doc_type = (meta.get("document_type_guess") or "").strip()
    if source in DIRECT_SOURCE_TYPES or doc_type in DIRECT_DOCUMENT_TYPES:
        return "company_direct"
    if source in ANALYST_SOURCE_TYPES or doc_type in ANALYST_DOCUMENT_TYPES:
        return "analyst"
    return "other"


def priority_adjustment(kind: str, preference: str) -> float:
    """The Phase 5B tilt, still used by --retrieval-mode vector."""
    if preference == "none":
        return 0.0
    if preference == "both":
        return PRIORITY_BONUS if kind in ("company_direct", "analyst") else 0.0
    if preference == "company":
        if kind == "company_direct":
            return PRIORITY_BONUS
        return -PRIORITY_PENALTY if kind == "analyst" else 0.0
    if preference == "analyst":
        if kind == "analyst":
            return PRIORITY_BONUS
        return -PRIORITY_PENALTY if kind == "company_direct" else 0.0
    return 0.0


def select_evidence(candidates: list, max_chunks: int, max_per_document: int,
                    preference: str) -> tuple:
    """Greedy, deterministic evidence selection.

    Each candidate arrives with a `base_score` - the vector-mode score in Phase
    5B's shape, or the Phase 6 hybrid score. Selection then subtracts:
      - a penalty for each chunk already taken from the same document
      - a penalty for each chunk already taken from the same source type

    The penalties are what produce source diversity: a second chunk from an
    already-used document has to be clearly better to earn its place.

    Every candidate ends up with a `selection_reason` saying what happened to
    it, so a retrieval log explains itself without anyone re-deriving the maths.
    """
    remaining = list(candidates)
    selected = []
    per_document = {}
    per_source = {}

    for cand in candidates:
        cand.setdefault("selection_reason", "")
        cand.setdefault("selection_score", None)

    while remaining and len(selected) < max_chunks:
        best = None
        best_key = None
        best_score = None
        for cand in remaining:
            doc_id = cand["document_id"]
            if per_document.get(doc_id, 0) >= max_per_document:
                continue
            source = cand["source_type_guess"] or "Unknown"
            score = (cand["base_score"]
                     - DOC_REPEAT_PENALTY * per_document.get(doc_id, 0)
                     - SOURCE_REPEAT_PENALTY * per_source.get(source, 0))
            # Ties break on rank, which is already stable, so runs are repeatable.
            key = (round(score, 6), -cand["rank"])
            if best_key is None or key > best_key:
                best_key = key
                best = cand
                best_score = score
        if best is None:
            break
        remaining.remove(best)
        best["selection_score"] = best_score
        doc_taken = per_document.get(best["document_id"], 0)
        source = best["source_type_guess"] or "Unknown"
        source_taken = per_source.get(source, 0)
        notes = []
        if doc_taken:
            notes.append(f"{doc_taken} chunk(s) already taken from this document")
        if source_taken:
            notes.append(f"{source_taken} already taken from this source type")
        best["selection_reason"] = (
            f"selected #{len(selected) + 1}: score {best_score:.4f} "
            f"(base {best['base_score']:.4f}"
            + (f", after penalties for {'; '.join(notes)}" if notes else "")
            + ")")
        selected.append(best)
        per_document[best["document_id"]] = doc_taken + 1
        per_source[source] = source_taken + 1

    for cand in remaining:
        if per_document.get(cand["document_id"], 0) >= max_per_document:
            cand["selection_reason"] = (
                f"not selected: the per-document cap of {max_per_document} was already reached "
                f"for {cand['document_id']}")
        elif len(selected) >= max_chunks:
            cand["selection_reason"] = (
                f"not selected: the evidence pack was full at {max_chunks} chunk(s) and this "
                f"scored below those in it (base {cand['base_score']:.4f})")
        else:
            cand["selection_reason"] = (
                f"not selected: scored below the selected pack (base {cand['base_score']:.4f})")

    # --- order the pack so it leads with what the question asked for -------
    # Selection is score-driven: that decides WHICH chunks make the pack, and
    # the retrieval log keeps every score behind it. Ordering is a separate
    # question. When a source preference is in force, the matching evidence is
    # cited first, because "--source-priority analyst" should put the analyst
    # view in front of the reader, not bury it at [5]. Score order is kept
    # within each group, and nothing is added or dropped by this.
    if preference != "none":
        matching = {"company": {"company_direct"}, "analyst": {"analyst"},
                    "both": {"company_direct", "analyst"}}.get(preference, set())
        before = [c["chunk_id"] for c in selected]
        selected.sort(key=lambda c: 0 if c["kind"] in matching else 1)
        if [c["chunk_id"] for c in selected] != before:
            for cand in selected:
                if cand["kind"] in matching:
                    cand["selection_reason"] += (
                        f"; cited first because the run's source preference is '{preference}'")

    for position, cand in enumerate(selected, start=1):
        cand["citation"] = position
    return selected, per_document, per_source


GRADE_ORDER = {"Low": 0, "Medium": 1, "High": 2}


def cap_grade(grade: str, ceiling: str) -> str:
    """Never let a grade sit above its ceiling."""
    return grade if GRADE_ORDER[grade] <= GRADE_ORDER[ceiling] else ceiling


def question_shape_ceilings(question: str, intent: str = "unclassified") -> list:
    """Ceilings that depend only on what KIND of question this is.

    No evidence is consulted, and there is no escape clause: however good the
    pack, "what is the most recent development" is a judgement about ordering
    and "what risks does the research identify" is a synthesis across notes.
    Neither becomes a disclosed fact because the pack happens to be strong.

    Returned in the same (ceiling, flag, reason) shape as
    confidence_ceilings(), so both feed one capping loop and one audit trail.
    """
    text = " " + (question or "").lower() + " "
    ceilings = []

    if any(keyword in text for keyword in RECENCY_QUESTION_KEYWORDS):
        ceilings.append((
            "Medium", "recency_synthesis",
            "the question asks what is most recent or what changed, which is a judgement about "
            "how the retrieved documents order in time rather than something any one of them "
            "states - the archive can show what it holds, not that nothing newer exists"))

    if any(keyword in text for keyword in RISK_SYNTHESIS_KEYWORDS):
        ceilings.append((
            "Medium", "risk_synthesis",
            "the question asks which risks the material identifies, which is a synthesis across "
            "documents - a pack of company material does not make the synthesis a disclosure"))

    if any(keyword in text for keyword in ANALYST_CONCERN_KEYWORDS) or intent == "analyst_view":
        ceilings.append((
            "Medium", "analyst_opinion",
            "the question asks what analysts think, and analyst opinion is evidence of the "
            "opinion, not of the fact - however many notes agree"))

    return ceilings


def evidence_strength_of(cand: dict) -> float:
    value = cand.get("evidence_strength")
    return float(value) if value is not None else float(cand.get("similarity") or 0.0)


def confidence_ceilings(selected: list, intent: str = "unclassified",
                        question: str = "", ticker: str = "") -> list:
    """Every ceiling that applies to this evidence pack, and why.

    Returned as data rather than folded into one number so the same list can
    justify the grade, populate the audit flags and be read back months later.
    Each entry is (ceiling, flag, reason).

    Phase 5B.1 established the first two: what KIND of source the pack is made
    of caps the grade regardless of how well the words matched. Phase 8.1 adds
    the two that the first real evaluation exposed - a pack that is mostly
    third-party commentary, and a question that asks for an interpretation
    rather than a disclosure.
    """
    # Phase 12.1: the question's own shape caps the grade before any evidence is
    # looked at, and does so even when nothing was selected.
    ceilings = question_shape_ceilings(question, intent)
    if not selected:
        return ceilings

    direct = [c for c in selected if c["kind"] == "company_direct"]
    strong_direct = [c for c in direct if evidence_strength_of(c) >= 0.45]

    # "The company's own material carries this answer" has to mean the company
    # material is about the SUBJECT, not merely that it ranked well. A pack of
    # strong 10-Q chunks that never mention the counterparty being asked about
    # is not the company answering the question - it is the company answering a
    # different question, loudly. So the escape from the interpretive ceiling
    # requires at least two strong direct chunks that actually contain one of
    # the question's distinctive terms, and for those to be at least half the
    # pack.
    terms = subject_terms(question, ticker)
    on_topic_direct = [
        c for c in strong_direct
        if terms and any(term_in_text(t, (c.get("chunk_text") or "").lower()) for t in terms)]
    # "Clearly supports" is a high bar on purpose: half the pack must be the
    # company's own on-topic material, spread across more than one document.
    # One filing chunk that happens to use the word is agreement with itself.
    on_topic_documents = len({c["document_id"] for c in on_topic_direct})
    company_carries = (len(on_topic_direct) * 2 >= len(selected)
                       and len(on_topic_direct) >= 3
                       and on_topic_documents >= 2)

    # NOTE: `ceilings` already holds the question-shape ceilings from above.
    # They are not reset here, and `company_carries` does not lift them.

    if not direct:
        if intent == "company_reported":
            ceilings.append((
                "Low", "no_direct_company_evidence",
                "the question asks what was reported, filed or guided to, but not one selected "
                "chunk is direct company, SEC or company-material text - the whole pack is "
                "third-party or unclassified commentary"))
        else:
            ceilings.append((
                "Medium", "no_direct_company_evidence",
                "no selected chunk is direct company, SEC or company-material text, so however "
                "closely the wording matched, nothing here is the company's own disclosure"))
    elif len(direct) * 2 < len(selected):
        # Phase 8.1, Part C. Some direct material, but the pack is mostly not.
        ceilings.append((
            "Medium", "analyst_led_evidence",
            f"only {len(direct)} of {len(selected)} selected chunks are direct company or SEC "
            "material - the answer rests mostly on third-party commentary"))

    topics = interpretive_topics(question)
    if topics and not company_carries:
        ceilings.append((
            "Medium", "interpretive_topic",
            f"this question asks for {' and '.join(topics)}, which is a reading of the facts "
            "rather than a disclosed fact, and the company's own material does not carry the "
            f"answer ({len(on_topic_direct)} strong direct chunk(s) on this subject, of "
            f"{len(selected)} selected)"))

    return ceilings


def rule_confidence(selected: list, preference: str, intent: str = "unclassified",
                    strength_source: str = "vector similarity", question: str = "",
                    ticker: str = "") -> tuple:
    """A deterministic confidence grade computed from the evidence alone.

    This is recorded next to whatever the model says about its own confidence,
    so the two can be compared instead of trusted.

    Similarity and document diversity measure how well retrieval matched the
    *wording* of the question. They say nothing about whether the evidence is
    the right KIND of source, and a pack of sell-side commentary can score just
    as well as a filing. So the score-based grade is only a starting point, and
    a ceiling is applied on top of it:

      - no direct company / SEC / company-material chunk at all -> Medium at best;
      - and if the question was about what the company reported, filed, or
        guided to, evidence with no direct company source at all is Low;
      - a pack that is mostly third-party commentary -> Medium at best (8.1);
      - a question asking for an interpretation rather than a disclosure ->
        Medium at best, unless the company's own material carries it (8.1).

    A question about reported results answered entirely out of third-party
    commentary is not high-confidence evidence for that question, however
    closely the words happen to match.

    The ceilings themselves live in confidence_ceilings() so the same list can
    be read back as audit flags.
    """
    if not selected:
        return "Low", "no evidence chunks were selected"

    def strength(cand: dict) -> float:
        value = cand.get("evidence_strength")
        return float(value) if value is not None else float(cand.get("similarity") or 0.0)

    strong = [c for c in selected if strength(c) >= 0.45]
    direct = [c for c in selected if c["kind"] == "company_direct"]
    documents = len({c["document_id"] for c in selected})
    top = max(strength(c) for c in selected)
    label = f" ({strength_source})" if strength_source != "vector similarity" else ""

    # --- grade from the retrieval scores alone -----------------------------
    if top < WEAK_EVIDENCE_SIMILARITY:
        grade = "Low"
        reason = (f"best strength {top:.3f}{label} is below the weak-evidence floor "
                  f"{WEAK_EVIDENCE_SIMILARITY:.2f}")
    elif len(strong) >= 3 and documents >= 2 and (direct or preference in ("analyst", "both")):
        grade = "High"
        reason = (f"{len(strong)} chunks at strength >= 0.45{label} across {documents} "
                  f"documents, {len(direct)} of them direct company/SEC material")
    elif len(strong) >= 1 or documents >= 2:
        grade = "Medium"
        reason = (f"{len(strong)} strong chunk(s){label} across {documents} document(s) - "
                  "partial or mixed source set")
    else:
        grade = "Low"
        reason = f"only {len(selected)} related chunk(s), none strongly similar"

    # --- ceilings from what KIND of source the evidence is, and what kind ---
    # --- of question was asked ---------------------------------------------
    for ceiling, _flag, ceiling_reason in confidence_ceilings(selected, intent, question,
                                                              ticker):
        capped = cap_grade(grade, ceiling)
        if capped != grade:
            reason = f"{reason}; capped from {grade} to {capped} because {ceiling_reason}"
        else:
            reason = f"{reason}; {ceiling_reason}"
        grade = capped

    return grade, reason


# ---------------------------------------------------------------------------
# Prompting
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Phase 14: the prompt, as a hierarchy
#
# It used to be one flat block in which "cite every sentence" and "use cautious
# language" had equal standing. That is how a style instruction ends up
# competing with a grounding rule, and the model has no way to know which one
# yields. Five named groups now, with A declared absolute, so the ordering is
# a fact in the prompt rather than something inferred from paragraph order.
#
# What Phase 14 changed is group C. Groups A, B, D and E carry the Phase 6.1,
# 8.1, 8.2 and 12.1 rules forward word for word where they are load-bearing -
# the exact directness sentence, the six section names, the per-sentence
# citation rule, the rating-restatement ban - because each of those is read by
# a deterministic check downstream, and rewording one silently disables it.
# ---------------------------------------------------------------------------

SYSTEM_INSTRUCTION = """You are Cutler Research Brain, an internal research assistant for \
Cutler Capital Management LLC. You write short research notes from retrieved documents, for an \
analyst who will verify them.

Five rule groups follow. A is absolute: where B-E would make an answer less accurate, A wins. \
Style never overrides factual discipline.

=== A. GROUNDING ===

Use only the provided evidence chunks.
Do not use outside knowledge.
Do not use anything you know about this company from training data.
Do not make investment recommendations.
Do not infer a thesis, sentiment, rating, recommendation, or catalyst unless directly supported \
by the evidence.
Numbers, dates and quoted wording must come from the evidence chunks exactly as they appear \
there. Do not compute, round, extrapolate or restate a figure the evidence does not contain.
If a statement you want to make cannot be tied to a supplied chunk, do not make it - say instead \
that the retrieved evidence does not support it.
If the evidence is weak, incomplete, or not directly responsive, say so plainly and early (C says \
how).
If the top chunks are related to the question but do not answer it, your first line must be \
exactly: "The retrieved evidence is related, but it does not directly answer the question."

=== B. ATTRIBUTION ===

Three different things, and the answer must never blur them.

1. What the company reported. Write "the company reported", "management said", "the filing \
shows" or "according to the company" ONLY when a company-direct source - an SEC filing, a \
company press release or company material - is among the supplied chunks AND is the chunk you \
are citing for that statement. The retrieval notes tell you how many company-direct chunks the \
pack contains; if that count is zero, no sentence in your answer may attribute anything to the \
company, to management or to a filing.

2. What a third party argues. Name the publisher when the pack gives you one, and pair the name \
with a source word, because who said it is part of the claim:
- "Bernstein's note expects higher AI-related depreciation to pressure margins near term [3]."
- "UBS research estimates capacity additions continue into next year [2]."
- "Several analyst notes name depreciation as the main near-term margin risk [3][4][5]."
Do not attach a publisher name to every sentence when it adds nothing. Where several sources say \
the same thing, say it once and cite them together.

3. What you conclude from the evidence. This is your reading, not something a document says, and \
it must be written so a reader can tell: begin such a sentence with "Taken together", "Read \
together", "On balance", "The net effect", "What this means" or "Why it matters".

CITE YOUR CONCLUSIONS. This is about HOW to write a conclusion, not an instruction to add one - \
a takeaway that already states what the evidence amounts to is the conclusion, and needs no \
second one. If you do write one, it carries the citations of the claims it rests on:

  "... the debate is moving from whether AI demand exists to whether it can be monetised faster \
than the cost of supporting it [1][3][4]."

That is not a contradiction of "cite every factual claim" - it is the same rule. A conclusion a \
reader cannot trace back to documents is the one thing this system exists not to produce, and \
marking a sentence as synthesis does not make it cheaper to write.

The only conclusion that may carry no citation is one that asserts nothing new about the world - \
connective language such as "Taken together, these are two different questions rather than one." \
The moment a conclusion states a direction, a comparison, a cause, a consensus across sources, or \
anything about the company's performance or figures, it is a claim and it cites. Keep every figure \
out of a conclusion regardless: a number belongs in the cited claim that reported it.

Never present a conclusion of yours as though it appeared in a source, and never present analyst \
commentary as company disclosure.

If the retrieved text contains analyst ratings or price targets, do not restate the rating word \
or the target number. Refer to them as source-reported analyst ratings or price targets, cite \
where they appear, and say plainly that they are the publishing firm's opinion and not a \
recommendation from this system.

=== C. HOW TO WRITE THE ANSWER ===

ANSWER THE QUESTION WITH THE MINIMUM EVIDENCE NEEDED TO SUPPORT THE CONCLUSION. Everything below \
serves that one priority.

Lead with the answer. Do not open with where the answer came from: "Based on the provided \
sources", "The research indicates", "According to the documents", "Here is a summary" and "The \
retrieved source text says" all spend the reader's first line on provenance, and the citation \
already says where it came from. Open with the finding itself, carrying its citation. Open with a \
statement about the evidence only when the limitation of the evidence IS the answer.

  Weak:   "Based on the provided research, Microsoft appears to be spending heavily on AI."
  Strong: "Microsoft's AI spending remains a growth driver, but the cost of supporting it is \
becoming the larger margin question [1][3]."

EVIDENCE SUPPLIED IS A MENU, NOT A CHECKLIST. A pack of eight chunks is what retrieval found, not \
a list of things you owe the reader. Cite the two, three or four that answer the question and \
leave the rest: they are preserved in section 2 and in the Sources panel, so nothing is lost by \
leaving them out of section 1. A large pack is never a reason to write a longer answer.

Three things still outrank brevity: every factual claim carries its support; a material \
qualification is never dropped to save words; and no source is cited that does not support the \
point it sits in.

Say each thing once. Do not restate a point as a conclusion, and do not repeat in section 4 or 5 \
what section 1 already said. A summary that re-lists the points you have just made is NOT a \
conclusion - "Taken together, the research identifies three risks: (1) ..., (2) ..., (3) ..." is \
a table of contents written after the fact. A conclusion says something the points do not: a \
direction, a trade-off, what it means for the question. A good takeaway is often already that \
conclusion, in which case write no closing line at all. Never a summary AND a closing line. Quote \
only the words that carry the fact - a long quotation is not evidence of rigour.

Where the evidence supports an implication or names a change, say it once, in the sentence that \
carries the fact: "deposit costs rose 12 bps [2], which offset part of the asset-yield \
improvement and limited NIM expansion [2]". Do not invent implications, do not add generic \
finance commentary, and for a question about a recent change lead with what moved and against \
what - or say plainly that the pack holds only one period, if it does.

When the evidence does not answer the question, write this and stop:

  "The retrieved evidence does not contain enough information to answer this reliably."

and, only if it helps, ONE further sentence naming the gap, with the evidence still as the \
grammatical subject: "The pack holds no AT&T disclosures from 1998." Keep the evidence as the \
subject in both - "Kimco's 1998 results are not disclosed here" says something about Kimco, and a \
statement about a company is a claim that cites, however it is phrased.

CITE NOTHING in that case: a document that does not contain the requested fact is not evidence \
for it, and citing eight such documents to show that a year is absent is padding dressed as \
rigour. Do not inventory what the pack DOES contain, do not list the missing document types, \
metrics or periods, and do not repeat the absence in sections 4 and 5. Never fill the gap from \
general knowledge. Section 2 then reads "None - no supplied chunk is evidence for this question.", \
section 3 reads "None.", and sections 4 and 5 are ONE sentence each.

=== D. CONFIDENCE AND REFUSAL ===

If you say anywhere in the answer that the evidence does not directly answer the question, is not \
enough, or does not provide or include what was asked for, then section 6 must say Low. Saying \
the evidence is not responsive and then claiming High or Medium confidence is a contradiction, \
and it is checked.

Never say:
- "Microsoft is a buy/sell/hold"
- "Cutler should..."
- "This proves..."
- "The company will..."
- "The market will..."

=== E. FORMAT ===

Answer with exactly these six numbered markdown sections, in this order, and nothing before or \
after them:

## 1. Direct Answer
## 2. Evidence Used
## 3. Source Citations
## 4. Evidence Limitations
## 5. Needs Human Review
## 6. Confidence

Section 1 is the note. EVERY sentence that states a fact must carry a citation, and every bullet \
must carry a citation, in the same sentence as the claim. Do not open with an uncited summary \
sentence such as "The retrieved source text says the following" - put the claim and its citation \
in that first sentence. A takeaway is NOT an exception: an opening takeaway that states a \
direction, a comparison, a cause, a consensus, a count of what the research identifies, or \
anything about the company is a claim, and its citations belong in that same sentence. Putting \
the claims in section 1 and \
the citation numbers only in sections 2 and 3 is the specific defect this rule exists to prevent: \
a marker in the evidence list does not cite the claim. The only sentences that may carry no \
citation are sentences that decline to answer, sentences that state the evidence is insufficient, \
and the purely connective conclusion described in B3 - which asserts nothing new about the world. \
A conclusion that states a direction, a comparison, a cause, a consensus or anything about the \
company cites, like any other claim.

Sections 2 to 6 are the record, not a second answer. Keep them short.
- 2. Evidence Used: one line per chunk you actually cited, naming in a few words what it \
contributed. Do not restate the claim.
- 3. Source Citations: one line per citation, [n] followed by the file name and page range \
exactly as given in the evidence pack. Nothing else.
- 4. Evidence Limitations: what is missing, ambiguous, stale, or only partially responsive. ONE \
or TWO specific items, not a restatement of section 1. "None" is almost never right here.
- 5. Needs Human Review: what a person must check before this is used. ONE or TWO items, and not \
the same items as section 4.
- 6. Confidence: exactly one of High, Medium, or Low, followed by one sentence of justification.
  High = multiple directly relevant chunks from direct company/SEC or highly relevant source \
text support the answer.
  Medium = some relevant chunks support the answer but the source set is partial or mixed.
  Low = retrieval found related text but not enough to answer directly."""


# Phase 14. The same rules, written to one of two lengths.
#
# Deep is NOT Quick with more words. The extra room goes to the part a longer
# answer should actually buy you - where the sources disagree, and what changed
# against what - rather than to more instances of a point already made.
#
# This decides only how the evidence is written up. How MUCH evidence a run gets
# is still top_k and max_chunks, and no weight, filter or ranking rule is
# touched by this setting.
ANSWER_STYLES = {
    # Phase 14.3.2. ONE hierarchy, in ONE place. The competing rules that used
    # to live in group C - the shape bullets, the synthesis paragraph, the
    # one-claim-per-point paragraph, the separate "why it matters" and "what
    # changed" paragraphs - are gone, not duplicated here. Four places inviting
    # a closing sentence is how every over-length answer ended up with a summary
    # AND a closer.
    "quick": (
        "LENGTH AND DEPTH FOR THIS ANSWER: quick. Section 1, in priority order:\n"
        "1. Answer the question in the first sentence.\n"
        "2. Add AT MOST TWO supporting points, unless a third is essential.\n"
        "3. Each point makes ONE research point. It may carry several citations.\n"
        "4. Do not enumerate every fact in the pack or every source. One point per SOURCE is the "
        "commonest way this answer gets long: if three notes say the same thing, that is one "
        "point citing [2][3][5], not three points.\n"
        "5. Prefer the strongest evidence over broader coverage.\n"
        "6. Stop once the question is answered.\n"
        "7. Target 100-180 words for section 1.\n"
        "8. 220 words is a maximum, not a target. Exceed it only for a question that explicitly "
        "asks for detail, enumeration or a comprehensive answer.\n"
        "9. When the evidence does not answer the question: 30-60 words, and stop.\n"
        "10. A large evidence pack is never a reason to write a longer answer.\n"
        "Counting towards those two or three points: a paragraph that summarises the points is "
        "one of them, and so is a closing line. Three source points plus a summary plus a "
        "closer is five, not three.\n"
        "Sections 2 to 6 together should be shorter than section 1."),
    "deep": (
        "LENGTH AND DEPTH FOR THIS ANSWER: deep.\n"
        "Section 1 may run to about 400 words, and the extra room is for synthesis, not for more "
        "examples of the same point. Use it to compare what different sources say and name where "
        "they disagree and by how much; to separate company-reported figures from analyst "
        "estimates of the same thing; to set out what changed against what it changed from; and "
        "to state the caveats that actually bear on the conclusion. Group the points by issue "
        "rather than by document.\n"
        "If two sources conflict, say so explicitly and cite both rather than choosing one "
        "silently. If they agree, say it once and cite them together rather than listing each.\n"
        "A long answer that is the quick answer with more adjectives has failed this "
        "instruction."),
}
DEFAULT_ANSWER_STYLE = "quick"
ANSWER_STYLE_CHOICES = tuple(ANSWER_STYLES)


def evidence_inventory(selected: list) -> str:
    """One neutral line per retrieved chunk, for the deterministic answers.

    File names are deliberately NOT printed here. A sell-side note is often
    named after its own rating ("MSFT_Overweight_PT_550.pdf"), and printing that
    into the answer body would put a rating word in an answer that exists to
    refuse to give one. The source table written into the answer FILE carries
    every file name in full, so nothing is hidden - it is just kept out of the
    prose.
    """
    lines = []
    for cand in selected:
        kind = {"company_direct": "company or SEC material",
                "analyst": "third-party research",
                "other": "unclassified archive text"}.get(cand["kind"], cand["kind"])
        date = cand.get("folder_date_guess") or "undated"
        lines.append(f"- [{cand['citation']}] {kind}, {date}, "
                     f"{cand.get('document_type_guess') or 'type not classified'}")
    return "\n".join(lines) if lines else "None."


def build_refusal_answer(selected: list, detection: dict, ticker: str) -> str:
    """Phase 8.1, Part A. The answer to a request for an investment decision.

    Written here, not generated. The system's refusal to advise is not a thing
    that should be re-decided by a language model on every run.
    """
    citations = ", ".join(f"[{c['citation']}]" for c in selected)
    has_evidence = bool(selected)

    context_line = (
        f"Retrieval still ran, and the {len(selected)} retrieved chunk(s) {citations} are listed "
        "below and in the source table so they can be read directly."
        if has_evidence else
        "Retrieval returned nothing usable for this wording, so there is not even source text to "
        "summarise.")

    return (
        "## 1. Direct Answer\n\n"
        f"{REFUSAL_SENTENCE}\n\n"
        "This question asks for an investment decision, so no answer model was called and no "
        f"view on the security was produced. {context_line}\n\n"
        "## 2. Evidence Used\n\n"
        "No evidence was used to form a view, because no view was formed. What retrieval "
        "returned was:\n\n"
        f"{evidence_inventory(selected)}\n\n"
        "Where the retrieved text contains source-reported analyst ratings or price targets, "
        "those are the opinions of the firms that published them. They are not repeated here, "
        "and they are not this system's view or a recommendation to act on.\n\n"
        "## 3. Source Citations\n\n"
        + (f"{citations} - see the source table below for file names, dates and pages.\n\n"
           if has_evidence else "None.\n\n")
        + "## 4. Evidence Limitations\n\n"
        "The limitation here is not the evidence, it is the question. No amount of retrieved "
        "text would make an investment recommendation an appropriate output of this system. "
        f"Detected as a request for action because it {detection.get('reason') or 'asks for a decision'}.\n\n"
        "## 5. Needs Human Review\n\n"
        f"Any investment judgement on {ticker} is Cutler's to make, from the underlying "
        "documents. If the intent was research rather than a decision, ask what the sources say "
        "about a specific topic instead - for example what was reported, what the risks named in "
        "the filings are, or what third-party research argues.\n\n"
        "## 6. Confidence\n\n"
        "Low - no answer to the question as asked was produced, and none will be.")


def build_unsupported_subject_answer(support: dict, selected: list, ticker: str,
                                     intent: str) -> str:
    """Phase 8.1, Part B. The answer when the subject is not in the evidence.

    The missing terms are named in section 4, not in the Direct Answer. That is
    not squeamishness: an automated hallucination guard reads the Direct Answer
    for phrases that must not appear, and it cannot tell "Microsoft's banana
    revenue grew" from "the word banana appears nowhere". Keeping the invented
    subject out of the Direct Answer entirely means the answer reads correctly
    to a person AND to the check, and section 4 is where a reader looks for
    what the evidence lacked anyway.
    """
    citations = ", ".join(f"[{c['citation']}]" for c in selected)
    headline = (
        f"The retrieved evidence does not support that {ticker} reports this metric or segment."
        if intent == "company_reported" else
        f"The retrieved evidence does not contain the subject of this question for {ticker}.")

    return (
        "## 1. Direct Answer\n\n"
        f"{headline} Retrieval returned its closest matches, but none of them is about what was "
        "asked, so no answer model was called and nothing was written around them.\n\n"
        "## 2. Evidence Used\n\n"
        "None of it, for this question. This is a deliberate stop, not a retrieval failure: "
        "chunks that merely rank highest are not evidence for a subject they never mention. "
        "What retrieval returned was:\n\n"
        f"{evidence_inventory(selected)}\n\n"
        "## 3. Source Citations\n\n"
        + (f"{citations} - see the source table below. They are listed for completeness, not as "
           "support for an answer.\n\n" if selected else "None.\n\n")
        + "## 4. Evidence Limitations\n\n"
        "The distinctive terms of the question were checked against the full text of every "
        "selected chunk. Not one of them appears: "
        + ", ".join(f"'{t}'" for t in support["missing"]) + ".\n\n"
        "Either the archive does not cover this, or the question uses wording the documents do "
        "not - both are possible, and this check cannot tell them apart.\n\n"
        "## 5. Needs Human Review\n\n"
        "If this subject should be in the archive, confirm the relevant documents are in the "
        "index and try the wording the documents themselves would use. If it should not be, "
        "this is the correct answer and nothing needs fixing.\n\n"
        "## 6. Confidence\n\n"
        "Low - the evidence is not about the question.")


def retrieval_provenance(cand: dict) -> str:
    """How this chunk was found. A keyword-only hit has no vector similarity,
    and saying so is more useful than printing a zero that looks like a score.
    """
    parts = []
    if isinstance(cand.get("vector_score"), (int, float)):
        parts.append(f"vector similarity {cand['vector_score']:.4f}")
    if isinstance(cand.get("keyword_score"), (int, float)):
        phrase = ", exact phrase match" if cand.get("phrase_hit") else ""
        parts.append(f"keyword score {cand['keyword_score']:.4f}{phrase}")
    return " + ".join(parts) or "unscored"


def render_evidence_pack(selected: list) -> str:
    blocks = []
    for cand in selected:
        pages = format_pages(cand.get("page_start_guess"), cand.get("page_end_guess"))
        blocks.append(
            f"----- EVIDENCE [{cand['citation']}] -----\n"
            f"chunk_id            : {cand['chunk_id']}\n"
            f"document_id         : {cand['document_id']}\n"
            f"file_name           : {cand.get('file_name') or '(unknown)'}\n"
            f"folder_date_guess   : {cand.get('folder_date_guess') or '(none)'}\n"
            f"source_type_guess   : {cand.get('source_type_guess') or '(none)'}\n"
            f"document_type_guess : {cand.get('document_type_guess') or '(none)'}\n"
            f"page_start_guess    : {cand.get('page_start_guess') if cand.get('page_start_guess') else '(none)'}\n"
            f"page_end_guess      : {cand.get('page_end_guess') if cand.get('page_end_guess') else '(none)'}\n"
            f"relative_path       : {cand.get('relative_path') or '(none)'}\n"
            f"retrieved_by        : {retrieval_provenance(cand)}\n"
            f"chunk_text          :\n{cand.get('chunk_text') or ''}\n"
        )
    return "\n".join(blocks)


def build_user_input(question: str, selected: list, ticker: str, intent: dict,
                     preference: str) -> str:
    strengths = [c.get("evidence_strength") for c in selected
                 if isinstance(c.get("evidence_strength"), (int, float))]
    weak = bool(strengths) and max(strengths) < WEAK_EVIDENCE_SIMILARITY
    notes = [
        f"Ticker index searched: {ticker} (Phase 5A embedded chunks only).",
        f"Question intent (keyword rule): {intent['intent']}.",
        f"Source preference applied: {preference}.",
        f"Evidence chunks provided: {len(selected)}.",
        f"Distinct documents represented: {len({c['document_id'] for c in selected})}.",
    ]
    if weak:
        notes.append("RETRIEVAL WARNING: the best-matching chunk scored below the weak-evidence "
                     "floor. Treat this evidence set as weak and say so.")

    # Phase 12.1: state the pack's composition as a fact, so the attribution
    # rule is not a general instruction the model has to apply to an unknown
    # pack - it is a count it can read.
    direct = [c for c in selected if c.get("kind") == "company_direct"]
    analyst = [c for c in selected if c.get("kind") == "analyst"]
    notes.append(f"Company-direct chunks in this pack (SEC filing, company press release, "
                 f"company material): {len(direct)}.")
    notes.append(f"Analyst / third-party research chunks in this pack: {len(analyst)}.")
    if not direct:
        notes.append("ATTRIBUTION RULE: this pack contains NO company-direct or SEC source. Do "
                     "not write that the company, management or a filing reported, disclosed, "
                     "stated or announced anything. Attribute every statement to the research "
                     "pack - 'the research pack reports', 'analysts estimate', 'the note states' "
                     "- and say explicitly that these are third-party views, not company "
                     "disclosure.")
    return (
        f"QUESTION:\n{question}\n\n"
        f"RETRIEVAL NOTES (metadata, not evidence):\n" + "\n".join(f"- {n}" for n in notes) +
        "\n\nEVIDENCE PACK\n"
        "These are verbatim text chunks extracted from documents in Cutler's archive. They are\n"
        "the only material you may use. Anything not in them is out of scope for this answer.\n\n"
        + render_evidence_pack(selected)
    )


def answer_instructions(settings: dict, extra: str = "") -> str:
    """The system instruction actually sent, with the length group appended.

    Phase 14. The length rule is group F of the same hierarchy rather than a
    sentence buried in the user message, so it is ranked against the other
    groups explicitly - and group A still outranks it. An unknown style falls
    back to quick rather than to no length guidance at all, because "no rule"
    is what produced the 555-word answers this phase exists to fix.
    """
    style = str(settings.get("answer_style") or DEFAULT_ANSWER_STYLE).lower()
    block = ANSWER_STYLES.get(style) or ANSWER_STYLES[DEFAULT_ANSWER_STYLE]
    text = SYSTEM_INSTRUCTION + "\n\n=== F. LENGTH ===\n\n" + block
    # Phase 14.3.5. Only a compliance retry passes anything here, and it passes
    # the repair pass - which is why the base prompt did not have to grow again.
    return text + "\n\n" + extra if extra else text


class AnswerCallBudget:
    """How many generative calls one question may still make.

    Phase 14.3.5. Every path that can call the answer model spends from one of
    these, so "at most one retry" is enforced by arithmetic rather than by each
    path trusting the others to behave. A path that finds the budget empty logs
    it and carries on with the text it has; it never waits, never loops, and
    never silently makes the call anyway.
    """

    def __init__(self, limit: int = MAX_ANSWER_MODEL_CALLS):
        self.limit = max(1, int(limit))
        self.used = 0
        self.spent_on = []

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)

    def spend(self, why: str) -> int:
        if self.used >= self.limit:
            raise RuntimeError(
                f"the answer model call budget for this question is spent "
                f"({self.used}/{self.limit}: {', '.join(self.spent_on)}). "
                f"Refusing to call again for '{why}'.")
        self.used += 1
        self.spent_on.append(why)
        return self.used


# The repair pass. Not part of SYSTEM_INSTRUCTION: it is sent only on a
# compliance retry, so a normal answer is written against exactly the prompt
# Phase 14.3.4 left behind. It names the defect and forbids everything else.
COMPLIANCE_REPAIR_INSTRUCTION = (
    "=== G. REPAIR PASS (this call only) ===\n\n"
    "Your previous answer to this question is at the end of the input. It is right on substance, "
    "and this pass must not change the substance: the same conclusion, the same points, the same "
    "length or shorter, the same evidence. No new research, no new points, no new sources, no "
    "new caveats.\n\n"
    "Repair ONLY this: {failures}\n\n"
    "Rewrite it so that every sentence stating a fact or a takeaway - the OPENING sentence "
    "included - carries its citation markers inside that same sentence, and so that no citation "
    "marker stands on its own: \"[1][5]\" as a sentence, as a line, or as a fragment trailing "
    "another sentence cites nothing and must not appear anywhere. Keep all six numbered "
    "sections, and keep section 1 at or under 220 words.\n\n"
    # Phase 14.3.7. The 14.3.6 wording asked for every point, risk, qualification AND
    # citation marker to survive, which left nothing to cut: the live AMZN repair came
    # back at 249 words from 270. Cutting words means cutting something, so this says
    # what, and it says plainly that it outranks "the same points" above - two rules on
    # one decision is how 14.3.1 produced a prompt the model could satisfy either way.
    "For a LENGTH repair this paragraph takes precedence over \"the same points\" above, because "
    "cutting words means cutting something. Keep every DISTINCT category the question asked "
    "for - if it asked about capital expenditure, competition and regulation, all three are "
    "still there afterwards - and keep the core conclusion of each one, with enough citations "
    "that every factual claim left standing is supported.\n\n"
    "Within that, cut freely: a redundant example, a secondary number, a qualification already "
    "made once, a duplicate citation, the second and third source saying what the first already "
    "said, and a closing synthesis that only re-lists the categories above. Removing a redundant "
    "SOURCE is not a loss of grounding - grounding means every claim you keep still carries a "
    "citation, not that every marker from the previous answer survives.\n\n"
    "What you must NOT do: drop a category the question asked for, introduce a claim that was "
    "not in the previous answer, or remove the only citation supporting a claim you kept.\n\n"
    "Aim at 160 to 200 words for section 1, with 220 the absolute maximum. Prefer one compact "
    "paragraph or one bullet per requested category. Do not pad upward: if the compressed answer "
    "comes in under 160 words with every category intact and every claim cited, that is a better "
    "answer, not a worse one.")

REPAIR_INPUT_HEADER = (
    "\n\n=== YOUR PREVIOUS ANSWER - REPAIR ITS CITATIONS, DO NOT REWRITE IT ===\n\n")


def _responses_call(client, settings: dict, user_input: str, budget: int,
                    calls: "AnswerCallBudget" = None, extra_instructions: str = "",
                    why: str = "answer"):
    """One responses.create, retried on transport errors.

    Phase 14.3.5. The budget is spent once here, for the logical call. The
    transport retries below re-send the SAME request after a network error and
    do not spend again - they are one call that had to be posted more than once,
    not several calls.
    """
    if calls is not None:
        calls.spend(why)
    kwargs = {
        "model": settings["answer_model"],
        "instructions": answer_instructions(settings, extra_instructions),
        "input": user_input,
        "max_output_tokens": budget,
    }
    if settings["reasoning_effort"]:
        kwargs["reasoning"] = {"effort": settings["reasoning_effort"]}

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return client.responses.create(**kwargs)
        except Exception as exc:  # noqa: BLE001 - network, auth, rate limit
            wait = RETRY_BASE_SECONDS * (2 ** (attempt - 1))
            if attempt < MAX_RETRIES:
                LOGGER.warning("Answer attempt %d/%d failed (%s). Retrying in %.0fs.",
                               attempt, MAX_RETRIES, type(exc).__name__, wait)
                time.sleep(wait)
            else:
                raise RuntimeError(
                    f"answer generation failed: {type(exc).__name__}: {exc}") from exc
    raise RuntimeError("answer generation failed")  # pragma: no cover


def incomplete_reason_of(response) -> str:
    """Why the API says it stopped early, or "" when it did not.

    Phase 14.3.3. The Responses API reports this as
    `response.incomplete_details.reason` (typically 'max_output_tokens'); the
    Chat Completions shape calls the same thing `finish_reason == 'length'`.
    Read both, so a change of client surface does not silently lose the signal.
    """
    details = getattr(response, "incomplete_details", None)
    reason = getattr(details, "reason", None) or (
        details.get("reason") if isinstance(details, dict) else None)
    if reason:
        return str(reason)
    for item in getattr(response, "choices", None) or []:
        finish = getattr(item, "finish_reason", None)
        if finish and str(finish) != "stop":
            return str(finish)
    return ""


def call_answer_model(client, settings: dict, user_input: str,
                      calls: "AnswerCallBudget" = None, extra_instructions: str = "",
                      why: str = "answer") -> dict:
    """ONE responses call, and at most ONE retry. The only generative call here.

    A reasoning model spends part of max_output_tokens thinking, so a budget
    that is too small comes back 'incomplete'. Two shapes of that, and Phase
    14.3.3 treats them the same way:

      (a) incomplete with NO text at all - handled since Phase 7
      (b) incomplete WITH partial text  - the answer stops mid-sentence, and
          before this phase it was accepted, audited and filed as finished

    Either way the retry sends the SAME instructions and the SAME user_input,
    so the same question and the same evidence pack - no retrieval happens in
    this function at all - and the retry's text REPLACES the first text. It is
    never stitched onto it, and there is never more than one retry. If the
    retry is also incomplete, the status says so and the deterministic guard
    caps the answer to Low rather than hiding it.
    """
    calls = calls if calls is not None else AnswerCallBudget()
    budget = settings["max_output_tokens"]
    response = _responses_call(client, settings, user_input, budget, calls,
                               extra_instructions, why)
    text = extract_output_text(response)
    status = getattr(response, "status", "") or ""
    reason = incomplete_reason_of(response)
    retried = False

    if status == "incomplete" and not retried and calls.remaining < 1 and text.strip():
        # Phase 14.3.5. The budget is spent, so there is no retry to make. The
        # text is kept exactly as written and the status still says why, which
        # is what caps it to Low.
        LOGGER.warning("The answer came back incomplete (%s) and the call budget for this "
                       "question is spent (%d/%d). Keeping the text as written; it is flagged "
                       "and capped at Low.", reason or "unknown", calls.used, calls.limit)
    elif status == "incomplete" and not retried:
        shape = "no text" if not text.strip() else f"{len(text.strip())} characters of text"
        LOGGER.warning("The answer came back incomplete (%s) with %s at a budget of %d output "
                       "tokens. Retrying ONCE at %d with the same question and the same "
                       "evidence.", reason or "unknown", shape, budget, budget * 2)
        retried = True
        budget *= 2
        response = _responses_call(client, settings, user_input, budget, calls,
                                   extra_instructions, f"{why}:length-retry")
        retry_text = extract_output_text(response)
        if retry_text.strip():
            text = retry_text
        status = getattr(response, "status", "") or ""
        reason = incomplete_reason_of(response)
        if not text.strip():
            raise RuntimeError(
                f"the answer model returned no text at {budget} output tokens "
                f"(status={status or 'unknown'}, reason={reason or 'unknown'}). Raise "
                "OPENAI_ANSWER_MAX_OUTPUT_TOKENS in .env, or lower "
                "OPENAI_ANSWER_REASONING_EFFORT."
            )
        if status == "incomplete":
            LOGGER.warning("The retry was also incomplete (%s). The answer is kept exactly as "
                           "the model wrote it and flagged as an incomplete generation; its "
                           "confidence is capped at Low.", reason or "unknown")
    if not text.strip():
        raise RuntimeError("the answer model returned an empty response.")

    usage = getattr(response, "usage", None)
    input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    exact_usage = bool(input_tokens or output_tokens)
    if not exact_usage:
        input_tokens = (estimate_tokens(answer_instructions(settings, extra_instructions))
                        + estimate_tokens(user_input))
        output_tokens = estimate_tokens(text)

    return {"text": text.strip(), "input_tokens": input_tokens,
            "output_tokens": output_tokens, "exact_usage": exact_usage,
            "status": status or "completed",
            # Phase 14.3.3. Recorded, not discarded: the caller writes these to
            # the run row, so the next time an answer looks cut off the reason
            # is already on the record instead of needing a re-run to find.
            "incomplete_reason": reason,
            "max_output_tokens": budget,
            "retry_used": retried,
            # Phase 14.3.5. What the whole question has cost in generative
            # calls so far, so the caller can see the ceiling being approached.
            "calls_used": calls.used,
            "calls_limit": calls.limit}


def extract_output_text(response) -> str:
    """Prefer output_text; fall back to walking the output items."""
    text = getattr(response, "output_text", None)
    if isinstance(text, str) and text.strip():
        return text
    parts = []
    for item in getattr(response, "output", None) or []:
        for piece in getattr(item, "content", None) or []:
            value = getattr(piece, "text", None)
            if isinstance(value, str):
                parts.append(value)
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Answer post-checks
# ---------------------------------------------------------------------------

def parse_sections(answer: str) -> dict:
    """Pull the six required sections out of the answer, if they are there."""
    found = {}
    pattern = re.compile(r"^\s*#{1,6}\s*\d?\.?\s*(" + "|".join(
        re.escape(name) for name in ANSWER_SECTIONS) + r")\s*$", re.I | re.M)
    matches = list(pattern.finditer(answer))
    for index, match in enumerate(matches):
        name = match.group(1).strip().title()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(answer)
        found[name] = answer[match.end():end].strip()
    return found


def extract_confidence(sections: dict) -> str:
    body = sections.get("Confidence", "")
    match = re.search(r"\b(High|Medium|Low)\b", body, re.I)
    return match.group(1).title() if match else ""


# Phase 14.1. The negative lookbehind stops a split after an INITIAL - the "P."
# in "J.P. Morgan", the "A." in "A. Schulman". Without it, every sentence naming
# a broker by initials was cut in two and the half without the citation was
# reported as an uncited claim. That was one of the 12 live findings, and it was
# a defect in the reader, not in the answer.
#
# The trade-off is deliberate: a real sentence ending in a capital and a period
# ("... filed with the SEC.") is now joined to the next one. Under-splitting is
# the safe direction here - a longer unit is MORE likely to contain a citation,
# so the error moves towards missing a real problem rather than inventing one,
# and a missed uncited claim is still caught by the per-bullet check.
RE_SENTENCE = re.compile(r"(?<![A-Z]\.)(?<!\.\.)(?<=[.!?])\s+(?=[A-Z\"'\[])")
RE_BULLET = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(.*)$", re.M)


def directness_scope(sections: dict, answer: str) -> dict:
    """The parts of the answer whose verdict on the evidence actually counts.

    Phase 6.1 narrowed this. A phrase like "does not include" in *Evidence
    Limitations* is that section working correctly - it is meant to say what
    the evidence lacks - and capping confidence on it punished good answers.
    The verdict that matters is in the Direct Answer, or restated in
    Confidence.

    If the answer could not be parsed into sections at all, the text before the
    first heading is used, since that is where a "this does not answer the
    question" opening would sit. A wholly unparseable answer is already flagged
    separately as missing its sections.
    """
    scope = {name: sections[name] for name in DIRECTNESS_SECTIONS if sections.get(name)}
    if scope:
        return scope
    heading = re.search(r"^\s*#{1,6}\s", answer or "", re.M)
    lead = (answer or "")[:heading.start()] if heading else (answer or "")
    return {"(unparsed answer)": lead} if lead.strip() else {}


def detect_directness(sections: dict, answer: str) -> tuple:
    """Does the answer itself say the evidence is not responsive?

    This is the single most reliable signal available. The model has read the
    evidence; if it reports there that the evidence does not answer the
    question, that judgement outranks any similarity score, and confidence is
    capped.

    Returns (matched phrases, the section names they were found in) so a
    reader can see exactly what triggered a cap.
    """
    hits = []
    where = []
    for name, body in directness_scope(sections, answer).items():
        lowered = (body or "").lower()
        found = [phrase for phrase in DIRECTNESS_PHRASES if phrase in lowered]
        if found:
            where.append(name)
            hits.extend(phrase for phrase in found if phrase not in hits)
    return hits, where


# Phase 8.1, Part D. Sentences that assert nothing about the company and so
# cannot be "uncited claims". Two kinds: a refusal or statement about the
# evidence itself, and a lead-in that introduces the bullets under it.
# Phase 14.1. The words that make a sentence a statement ABOUT THE EVIDENCE
# rather than about the company. Every insufficiency pattern below requires one,
# which is what keeps the exemption from reaching a company claim.
_PACK = (r"(?:retrieved|supplied|provided|indexed|selected|available)?\s*"
         r"(?:chunks?|pack|evidence|archive(?:\s+material)?|documents?|material|"
         r"sources?|corpus|retrieval(?:\s+pack)?)")

# --- Phase 14.2: the container as a grammatical subject --------------------
#
# A noun phrase that names the thing retrieval returned. This is what has to be
# doing the talking for a sentence to be an absence statement rather than a
# claim.
RE_CONTAINER_NP = (
    r"(?:the|these|this|those|our|no|none\s+of\s+(?:the\s+)?|not\s+one\s+of\s+(?:the\s+)?)?\s*"
    r"(?:\w+\s+){0,2}?"
    r"(?:retrieved|supplied|provided|indexed|selected|available|research|evidence|archive)?\s*"
    r"(?:chunks?|pack|evidence(?:\s+pack|\s+set)?|set|archive(?:\s+material|\s+excerpts?)?|"
    r"documents?|material|excerpts?|corpus|retrieval(?:\s+pack)?|filings\s+supplied)"
)

# The connective the model habitually puts before the subject.
_LEAD = (r"(?:\s*(?:therefore|however|but|and|because|although|though|so|in\s+short)\b"
         r"[^,]{0,70},\s*)?")

# The subject position: the container heads the sentence.
RE_CONTAINER_SUBJECT = re.compile(rf"^{_LEAD}\s*{RE_CONTAINER_NP}\b", re.I)

# "What is missing is any analyst excerpt ... in these documents" - the subject
# is the gap itself, and the container is named as where it is missing from.
RE_WHAT_IS_MISSING = re.compile(
    rf"^{_LEAD}\s*what\s+(?:is|are)\s+missing\b.*\b{RE_CONTAINER_NP}\b", re.I | re.S)

# An absence has to be ASSERTED. "The pack contains three filings and five notes"
# is a statement about the pack, but it is not a refusal and it can cite like
# anything else.
RE_ABSENCE_ASSERTED = re.compile(
    r"\b(?:no|not|none|nothing|neither|never|lacks?|lacking|missing|absent|without|"
    r"cannot|can\s?not|could\s+not|does\s+not|do\s+not|did\s+not|is\s+not|are\s+not|"
    r"fails?\s+to|only|rather\s+than)\b", re.I)


def is_pack_absence_sentence(sentence: str) -> bool:
    """Does this sentence say the EVIDENCE lacks something, as its subject?

    Both halves are required, and the subject test is the one doing the work.
    Without it, "does not include" would exempt any sentence containing those
    three words - including "Azure's growth does not include the acquisition",
    which is a claim about a company and has to cite.
    """
    text = (sentence or "").strip().lstrip("-*+ ").strip()
    if not RE_ABSENCE_ASSERTED.search(text):
        return False
    return bool(RE_CONTAINER_SUBJECT.match(text) or RE_WHAT_IS_MISSING.match(text))


NON_FACTUAL_SENTENCE_PATTERNS = tuple(re.compile(p, re.I) for p in (
    r"\bi\s+(?:cannot|can\s?not|can only|do not|don't|did not|didn't)\b",
    # A sentence about this tool is a statement about the machinery, not a
    # claim about the company, so there is nothing for it to cite.
    r"\bthis\s+(?:tool|system)\b",
    r"\banswer\s+model\s+was\s+(?:called|used|invoked|run)\b",
    r"\bthe\s+retrieved\s+evidence\s+(?:does\s+not|is\s+not|did\s+not)\b",
    r"\bthe\s+retrieved\s+(?:text|source\s+text|set|chunks?)\s+(?:does\s+not|do\s+not|"
    r"is\s+not|are\s+not)\b",
    r"\bnot\s+enough\s+retrieved\s+evidence\b",
    r"\bis\s+related,\s+but\s+it\s+does\s+not\s+directly\s+answer\b",
    r"\b(?:is|are)\s+not\s+(?:an\s+)?(?:investment\s+)?(?:advice|recommendation)\b",
    r"\bdeliberate\s+stop\b",
    # --- Phase 14.1 / 14.2: refusal idioms with no company reading ----------
    #
    # Anything that merely says "the container lacks X" is handled above by
    # is_pack_absence_sentence(), which tests the grammatical SUBJECT. What is
    # left here is the set of sentences about the ACT of answering: a company is
    # never the thing that "would need a Form 10-K to answer the question".
    #
    # DELETED IN PHASE 14.2, deliberately - the "none of the chunks",
    # "contains no", "cannot be determined", "lacks" and "not present in"
    # literals. Every one of them was written from a sentence I had in front of
    # me and had no subject test, so every one exempted a company claim wearing
    # the same words: "Kimco's 1998 results are not present in these documents"
    # has a company subject and must cite. A loose literal sitting beside a
    # strict structural rule means the loose one decides, which is the opposite
    # of what adding the structural rule was for.
    #
    # "cannot answer the question" is unambiguous alone - nothing about a
    # company ever "cannot answer the question". The looser objects (what, this,
    # that) need a container in the sentence, or "the segment cannot answer that
    # charge" would be exempted.
    r"\bcannot\s+answer\s+the\s+question\b"
    r"|\bquestion\s+cannot\s+be\s+answered\b",
    rf"{_PACK}\b.{{0,90}}?\bcannot\s+answer\b",
    # "To answer the question I would need a Form 10-K..." - naming what is
    # missing is the Phase 14 instruction, and it asserts nothing about a
    # company.
    r"\bto\s+answer\s+(?:the|this)\s+question\s*,?\s*(?:i|we)\s+would\s+need\b",
    rf"\bwould\s+need\s+(?:a|an|the)\b.*\bnot\s+(?:present|available|included|in)\b",
    r"\bnone\s+of\s+which\s+(?:appear|appears|are\s+present|is\s+present)\b",
    # "A different or expanded document search of the archive is required."
    # A statement about what to do next, not about the company.
    r"\b(?:a\s+)?(?:different|further|expanded|broader|additional)\s+"
    r"(?:document\s+)?(?:search|retrieval|query)\b",
    # "...is required to answer the question"
    r"\b(?:is|are|would\s+be)\s+required\s+to\s+answer\b",
))


def normalise_for_audit(text: str) -> str:
    """Make the text ASCII-shaped so an ASCII mask can actually match it.

    Phase 8.2. Three things defeat a literal mask, and all three appear in real
    model output:

      - a non-breaking hyphen or en dash instead of "-", so "sell-side" is not
        the string "sell-side";
      - a soft hyphen or zero-width space sitting invisibly inside a word;
      - markdown wrapping that leaves "sell-" ending one line and "side"
        starting the next.

    After this, all three read as plain "sell-side". Nothing here is written
    back anywhere: the answer on disk keeps every character exactly as the model
    wrote it. This is a reading of the text made solely to run the audit over.
    """
    folded = (text or "").translate(AUDIT_TRANSLATION)
    folded = RE_HYPHEN_LINE_WRAP.sub("-", folded)
    return folded


RE_TABLE_ROW = re.compile(r"^\s*\|.*$", re.M)
RE_CITATION_LINE = re.compile(
    r"^\s*\[\d{1,3}\]\s.*?(?:\||\.pdf|\.docx?|\.xlsx?|\.pptx?|\.htm|\.txt|\.msg).*$",
    re.M | re.I)


def mask_bibliography(text: str) -> str:
    """Blank out the lines that name documents rather than say anything.

    A source table row and a "[3] MSFT_Overweight_PT550.pdf | 2026-02-03 |
    Sell-Side Research | pages 1-2" citation line are a bibliography. They are
    required output, they are full of the vocabulary this archive is organised
    by, and none of it is the system making a claim.

    Only line SHAPES are masked, not the Source Citations section wholesale.
    Free prose in that section stays visible to the audit, so a recommendation
    cannot be smuggled in by putting it under the right heading.
    """
    text = RE_TABLE_ROW.sub(SAFE_LANGUAGE_MASK, text)
    return RE_CITATION_LINE.sub(SAFE_LANGUAGE_MASK, text)


def source_descriptors(selected: list) -> list:
    """The vocabulary this archive is filed under, plus this pack's own labels.

    "Sell-Side Research" is the NAME of a folder of documents. An answer that
    says where something came from is doing its job.
    """
    names = set(DIRECT_SOURCE_TYPES) | set(ANALYST_SOURCE_TYPES)
    names |= set(DIRECT_DOCUMENT_TYPES) | set(ANALYST_DOCUMENT_TYPES)
    for cand in selected or []:
        for key in ("source_type_guess", "document_type_guess", "file_name"):
            value = str(cand.get(key) or "").strip()
            if value:
                names.add(value)
    return sorted((n for n in names if n), key=len, reverse=True)


def mask_evidence_file_names(text: str, selected: list) -> str:
    """Blank out this pack's file names and source labels before the audit.

    Only names that belong to this run's evidence, plus the fixed classification
    vocabulary, are masked - nothing else in the answer is hidden from the
    patterns. A file name is a reference to a document; it is not the system
    making a call, whatever the document happens to be called.
    """
    names = source_descriptors(selected)
    if not names:
        return text
    pattern = re.compile("|".join(re.escape(name) for name in names), re.I)
    return pattern.sub(SAFE_LANGUAGE_MASK, text)


def audit_language_text(answer: str, selected: list) -> str:
    """The exact text the banned-language patterns are run against.

    Separated out so it can be tested directly, and so a future false positive
    can be diagnosed by looking at what the audit actually saw rather than by
    guessing.
    """
    text = normalise_for_audit(answer)
    text = mask_bibliography(text)
    text = RE_WHITESPACE_RUN.sub(" ", text)
    text = mask_evidence_file_names(text, selected)
    return RE_SAFE_LANGUAGE.sub(SAFE_LANGUAGE_MASK, text)


# Phase 14. A sentence that states what the evidence ADDS UP TO is not a new
# factual claim, and demanding a citation for it would push the system towards
# citing a document in support of a conclusion that document does not contain -
# the same mistake Phase 8.1 found in demanding a citation for a refusal.
#
# The exemption is deliberately narrow, because a wide one would let any
# uncited assertion through by opening with the right two words:
#
#   - the sentence must OPEN with an explicit synthesis marker, so the reader
#     is told whose reading it is before they read it;
#   - it must contain no digit. A number is a fact, it belongs to a source, and
#     it must be cited. This also keeps the Phase 12 figure-grounding check
#     meaningful: every figure in the Direct Answer still has to appear in the
#     retrieved text.
RE_SYNTHESIS_LEAD = re.compile(
    r"^\s*(?:\*\*)?(?:taken together|read together|on balance|the net effect|"
    r"what this means|why (?:it|this) matters|the through-line|put together|"
    r"seen together|together,? (?:these|the evidence|they))\b", re.I)
RE_ANY_DIGIT = re.compile(r"\d")

# Phase 14.1. The live run proved the digit guard alone was not the whole test.
# Both synthesis sentences it caught were substantive research conclusions:
#
#   "Taken together, these disclosures show continued postpaid phone additions
#    alongside material fiber subscriber growth through 4Q25..."
#   "Taken together, these sources show consistent concern across sell-side,
#    credit, and company filings that capex scale could compress free cash
#    flow..."
#
# Both happened to contain digits, so both were correctly denied the exemption.
# But neither was denied for the right reason - each is a directional claim and
# an analyst-consensus claim, and would have been just as substantive written
# without a year in it.
#
# So the exemption now also refuses anything that MAKES A RESEARCH CLAIM: a
# direction, a comparison, a cause, a consensus across sources, a statement
# about company performance. What is left is connective language - a sentence
# that frames the evidence or the question without asserting anything new about
# the world.
#
# This is deliberately a small exemption. In an investment-research product a
# conclusion worth stating is worth citing, and the prompt now says so: synthesis
# should normally carry the citations of the claims it rests on. The exemption
# exists for the line that genuinely has nothing to cite, not as a way to make a
# conclusion cheaper to write.
RE_SUBSTANTIVE_CLAIM = re.compile(
    # a direction or a measurable change
    r"\b(?:rose|rising|fell|falling|grew|growth|growing|declin\w*|increas\w*|decreas\w*|"
    r"higher|lower|improv\w*|deteriorat\w*|accelerat\w*|slow\w*|expand\w*|compress\w*|"
    r"widen\w*|narrow\w*|outperform\w*|underperform\w*|up\s+from|down\s+from|"
    r"stronger|weaker|more\s+than|less\s+than|exceed\w*|surpass\w*)\b"
    # a cause
    r"|\b(?:because|due\s+to|driven\s+by|drives?|as\s+a\s+result|resulting\s+in|"
    r"leads?\s+to|causes?|caused\s+by|owing\s+to|attributable\s+to)\b"
    # a consensus across sources
    r"|\b(?:consistent|consensus|unanimous|all\s+(?:of\s+)?(?:the\s+)?(?:sources|notes|"
    r"analysts)|every\s+(?:source|note|analyst)|several\s+(?:sources|notes|analysts))\b"
    # a statement about what the company or its numbers did
    r"|\b(?:revenue|margins?|earnings|guidance|occupancy|leverage|free\s+cash\s+flow|"
    r"subscribers?|net\s+adds?|backlog|capex|capital\s+expenditure)\b",
    re.I)


def is_synthesis_sentence(sentence: str) -> bool:
    """Connective language that asserts nothing new - and nothing more.

    Three conditions, all required:
      - it OPENS with an explicit synthesis marker, so the reader is told whose
        reading it is before they read it;
      - it contains no digit, because a number is a fact and belongs to a
        source;
      - it makes no research claim - no direction, comparison, cause,
        cross-source consensus or company-performance statement.

    A sentence that fails any of these is a claim, and a claim carries a
    citation. That is not a style preference: a conclusion nobody can trace back
    to a document is the one thing this system exists not to produce.
    """
    text = (sentence or "").strip().lstrip("-*+ ").strip()
    if not RE_SYNTHESIS_LEAD.match(text):
        return False
    if RE_ANY_DIGIT.search(text):
        return False
    return not RE_SUBSTANTIVE_CLAIM.search(text)


def is_non_factual_sentence(sentence: str) -> bool:
    """A sentence that declines, qualifies, or introduces - not one that claims.

    Phase 8.1. Requiring a citation on "I cannot provide a recommendation" would
    push the system towards citing a document in support of its own refusal,
    which is nonsense. Requiring one on "Two points stand out:" would push it
    towards citing the same chunk twice. Everything else still has to cite.
    """
    text = (sentence or "").strip()
    if not text:
        return True
    if text.rstrip().endswith(":"):
        return True
    if is_synthesis_sentence(text):
        return True
    # Phase 14.2. Checked before the literal patterns because it is the general
    # rule and they are the special cases it was derived from.
    if is_pack_absence_sentence(text):
        return True
    return any(pattern.search(text) for pattern in NON_FACTUAL_SENTENCE_PATTERNS)


def audit_citations(sections: dict, answer: str, selected: list) -> dict:
    """Phase 6, Part F. Where the citations are, and where they are missing.

    None of this fails a run. It is a reading aid: it tells a person which
    sentences to be suspicious of, which is the whole job of an audit here.
    """
    supplied = {c["citation"] for c in selected}
    cited_anywhere = sorted({int(n) for n in RE_CITATION.findall(answer)})

    direct = sections.get("Direct Answer", "")
    used = sections.get("Evidence Used", "")
    source_section = sections.get("Source Citations", "")

    in_direct = {int(n) for n in RE_CITATION.findall(direct)}
    in_used = {int(n) for n in RE_CITATION.findall(used)}
    in_sources = {int(n) for n in RE_CITATION.findall(source_section)}

    # Sentences in the Direct Answer that assert something and cite nothing.
    # Phase 8.1: a refusal or a lead-in asserts nothing, so it is exempt. What
    # remains is what the flag was always meant to catch - a factual claim
    # standing on its own.
    body = RE_BULLET.sub("", direct)
    uncited_sentences = [
        s.strip() for s in RE_SENTENCE.split(body)
        if len(s.strip()) >= MIN_FACTUAL_SENTENCE_CHARS
        and not RE_CITATION.search(s)
        and not is_non_factual_sentence(s)
    ]

    bullets = [b.strip() for b in RE_BULLET.findall(direct) if b.strip()]
    uncited_bullets = [b for b in bullets
                       if not RE_CITATION.search(b) and not is_non_factual_sentence(b)]

    flags = []
    unknown = [n for n in cited_anywhere if n not in supplied]
    if unknown:
        flags.append("cites evidence numbers that were never supplied: "
                     + ", ".join(f"[{n}]" for n in unknown))
    unused = sorted(supplied - set(cited_anywhere))
    if unused:
        flags.append(f"{len(unused)} supplied chunk(s) were never cited: "
                     + ", ".join(f"[{n}]" for n in unused))
    if uncited_sentences:
        flags.append(f"{len(uncited_sentences)} sentence(s) in Direct Answer carry no citation - "
                     f"first: \"{uncited_sentences[0][:120]}\"")
    if uncited_bullets:
        flags.append(f"{len(uncited_bullets)} bullet(s) in Direct Answer carry no citation - "
                     f"first: \"{uncited_bullets[0][:120]}\"")
    listed_only = sorted(in_sources - in_direct - in_used)
    if listed_only:
        flags.append("cited in Source Citations but used nowhere in Direct Answer or Evidence "
                     "Used: " + ", ".join(f"[{n}]" for n in listed_only))

    return {
        "citations_used": cited_anywhere, "unknown_citations": unknown,
        "unused_citations": unused, "uncited_sentences": uncited_sentences,
        "uncited_bullets": uncited_bullets, "listed_only_citations": listed_only,
        "bullets_total": len(bullets), "flags": flags,
        "citations_in_direct": sorted(in_direct),
    }


# --- Phase 12.1: attribution, enforced rather than requested -----------------
#
# The first live golden evaluation caught an answer that said the company had
# reported something when the pack held nothing but Credit, Industry and
# Sell-Side research. The instruction not to do that was already in the prompt.
# So this is the deterministic half: the phrasings that assert a company
# statement, matched after generation, against what the pack actually contains.
RE_COMPANY_ATTRIBUTION = re.compile(
    r"\b(?:the company|management|the filing|the 10-[kq]|the 8-k|the annual report|"
    r"the quarterly report|the press release|the earnings release)\s+"
    r"(?:has\s+|had\s+|have\s+)?"
    r"(?:reported|reports|reported that|disclosed|discloses|stated|states|said|says|"
    r"announced|announces|guided|guides|confirmed|confirms|filed|shows|showed)\b"
    r"|\baccording to (?:the company|management|the filing)\b"
    r"|\b(?:company|management)[- ]reported\b", re.I)

# Reported speech is not misattribution. An answer that writes
#
#   "one note records a $15bn investment and states management said it would
#    fund this with cash on hand [2]"
#
# is reporting, correctly and with a citation, what an analyst wrote that
# management said. The attribution chain is explicit and the claim belongs to the
# note. Phase 12.1 found this shape marked as a violation in the first live
# evaluation, which would have pushed the system towards writing worse answers -
# dropping the attribution chain rather than stating it.
#
# So a sentence is only in breach when it asserts the company statement DIRECTLY:
# no analyst source governs the sentence.
RE_ANALYST_GOVERNOR = re.compile(
    r"\b(?:note|notes|research|report|reports|analyst|analysts|broker|brokers|"
    r"sell[- ]side|credit research|industry research|commentary|the pack|"
    r"research pack|estimate|estimates|according to (?:the )?(?:note|research|analysts?))\b",
    re.I)


def pack_vendors(selected: list) -> list:
    """The publisher names the evidence pack itself carries.

    Phase 14 asks for attribution by name - "Bernstein's note expects..." rather
    than "analysts expect...". The generic-word patterns below do not know what
    a Bernstein is, so an answer that followed the new instruction and dropped
    the word "analysts" would read to the checks as UNATTRIBUTED, and a better
    answer would score worse. That would be the checks training the system to
    write badly.

    So the vocabulary comes from the pack: whatever vendor the pipeline recorded
    for these chunks. Nothing is hard-coded, nothing is guessed, and a pack with
    no vendor recorded simply contributes no names.
    """
    names = []
    for cand in selected or []:
        raw = str(cand.get("source_vendor_candidates") or "").strip()
        for part in re.split(r"[;,|]", raw):
            name = part.strip()
            if len(name) >= 2 and name.lower() not in {"none", "unknown", "n/a"}:
                if name not in names:
                    names.append(name)
    return names


def analyst_governor(text: str, selected: list = None) -> bool:
    """Is this sentence governed by a third-party source, named or generic?"""
    if RE_ANALYST_GOVERNOR.search(text or ""):
        return True
    for vendor in pack_vendors(selected or []):
        if re.search(r"\b" + re.escape(vendor) + r"\b", text or "", re.I):
            return True
    return False


def attribution_violations(direct_answer: str, selected: list) -> list:
    """Sentences claiming a company statement the evidence pack cannot support.

    Not a style check. "The company reported X" and "analysts estimate X" are
    different claims about the world, and only one of them can be made out of a
    pack of broker notes. When the pack holds no company-direct chunk, a
    sentence in the Direct Answer that attributes a statement DIRECTLY to the
    company, to management or to a filing is unsupported by construction.

    A sentence that attributes it to an analyst note which itself reports the
    company - "one note states management said it would fund this with cash on
    hand [2]" - is not in breach. That is reported speech: the claim belongs to
    the note, the chain is stated, and there is a citation on it. Flagging it
    would push the system towards dropping the attribution chain, which is the
    opposite of what this check is for.
    """
    if any(c.get("kind") == "company_direct" for c in (selected or [])):
        return []
    found = []
    for sentence in RE_SENTENCE.split(RE_BULLET.sub("", direct_answer or "")):
        text = sentence.strip()
        if not text or not RE_COMPANY_ATTRIBUTION.search(text):
            continue
        if analyst_governor(text, selected):
            continue
        found.append(text)
    return found


def post_generation_ceilings(context: dict) -> list:
    """Ceilings that can only be known once the answer text exists.

    Every one of these is a defect in the answer rather than a property of the
    evidence, and each one makes the answer less trustworthy than its own words
    claim - so each caps the final grade. Returned as (ceiling, flag, reason) to
    match the pre-generation ceilings and share one capping loop.

    Nothing here rewrites the answer and nothing invents a citation. The answer
    keeps its wording; what changes is the confidence attached to it and the
    flags printed above it.
    """
    ceilings = []
    audit = context.get("citation_audit") or {}
    uncited = list(audit.get("uncited_sentences") or []) + list(audit.get("uncited_bullets") or [])
    if uncited:
        ceilings.append((
            "Low", "uncited_claims_in_direct_answer",
            f"{len(uncited)} factual statement(s) in the Direct Answer carry no citation, so "
            f"which source supports them cannot be checked - first: "
            f"\"{uncited[0][:120]}\""))

    violations = context.get("attribution_violations") or []
    if violations:
        ceilings.append((
            "Low", "company_attribution_without_company_evidence",
            f"{len(violations)} statement(s) attribute a disclosure to the company, management "
            "or a filing, but not one selected chunk is company-direct or SEC material - first: "
            f"\"{violations[0][:120]}\""))

    # Phase 14.3.3. An answer that did not finish writing cannot be trusted to
    # the grade it gave itself, whether the deterministic guard noticed the cut
    # or the API itself reported stopping early.
    if context.get("generation_incomplete"):
        reasons = context.get("generation_incomplete_reasons") or []
        ceilings.append((
            "Low", "generation_incomplete",
            "the generation did not finish - " + ("; ".join(reasons) or "reason not recorded")))
    elif context.get("generation_status") not in ("", None, "completed"):
        ceilings.append((
            "Low", "generation_incomplete",
            f"the answer model reported status '{context['generation_status']}'"
            + (f" ({context['generation_incomplete_reason']})"
               if context.get("generation_incomplete_reason") else "")))

    if context.get("unsupported_subject"):
        ceilings.append((
            "Low", "subject_absent_from_evidence",
            "the distinctive terms of the question do not appear in the selected evidence"))

    if context.get("recommendation_refused"):
        ceilings.append((
            "Low", "recommendation_refused",
            "the question asked for an investment recommendation, which this system declines - "
            "there is no evidence-backed answer to be confident about"))

    return ceilings


# ---------------------------------------------------------------------------
# Phase 14.3.3: did the generation FINISH?
# ---------------------------------------------------------------------------
# A finished answer is six sections, each of which ends. Both halves of that
# are checkable without a model and without touching the text.
#
# The tail test is deliberately narrow. It fires on punctuation that cannot
# end a sentence and on a closed list of words that cannot end one either -
# conjunctions, prepositions, articles, and the synthesis lead-ins this prompt
# actually asks for ("Taken together", "Read together", "Given"). It does NOT
# try to parse English. A sentence ending in a citation marker, a figure, a
# file name or a bracketed page range is the normal case and must never be
# flagged, which is why the tail pattern requires a bare word at the very end.

UNFINISHED_TAIL_PUNCTUATION = (",", ":", ";", "-", "\u2013", "\u2014", "/", "&", "+")

UNFINISHED_TAIL_WORDS = frozenset("""
a an and are as at be because been both but by can could did do does either for
from given had has have however if in including into is it its may might must
nor not of on onto or our read should since so such taken than that the their
there these this those though to was were what when where whereas whether which
while who whom whose will with within would yet
""".split())

# A bare word at the very end, with closing quotes or brackets allowed after it.
# Anything else at the end - a full stop, a digit, a citation marker - means the
# sentence reached its own end and this test says nothing about it.
RE_TAIL_WORD = re.compile(r"([A-Za-z][A-Za-z'\u2019-]*)[\"'\u201d\u2019)\]]*\s*$")


# Which sections are prose, and must therefore end a sentence. Measured, not
# assumed: across the 60 answers of the Phase 14.3.2 before/after run, these
# three ended with terminal punctuation 60 times out of 60, while Source
# Citations did so only 43 times and Evidence Used 57 - those two end on a file
# name or a page range ("pp. 34-36", "Research Note") often enough that
# requiring a full stop there would flag good answers. So the full-stop rule
# applies to the prose sections only; the tail tests below apply to all six.
PROSE_SECTIONS = ("Direct Answer", "Evidence Limitations", "Needs Human Review")

RE_SENTENCE_END = re.compile(r"[.!?][\"'\u201d\u2019)\]]*$")


def unfinished_tail(text: str, prose: bool = False) -> str:
    """Why this text looks cut off mid-sentence, or "" when it does not."""
    stripped = (text or "").rstrip()
    if not stripped:
        return "is empty"
    if stripped.endswith(UNFINISHED_TAIL_PUNCTUATION):
        return f"ends on \"{stripped[-40:].strip()}\", which cannot end a sentence"
    match = RE_TAIL_WORD.search(stripped)
    if match and match.group(1).lower() in UNFINISHED_TAIL_WORDS:
        return (f"ends on the word \"{match.group(1)}\", which cannot end a sentence "
                f"(\"...{stripped[-40:].strip()}\")")
    # "...which could force" ends on a transitive verb with nothing to act on.
    # No word list catches that, and no parser belongs in this project; what
    # catches it is that a finished prose section ends a sentence.
    if prose and not RE_SENTENCE_END.search(stripped):
        return (f"ends without finishing a sentence (\"...{stripped[-40:].strip()}\")")
    return ""


def answer_word_count(text: str) -> int:
    """The words of an answer, counted the way the rubric counts them."""
    return len(RE_ANSWER_WORD.findall(text or ""))


def resolved_answer_style(answer_style: str = "") -> str:
    """The style actually in force, resolved exactly as answer_instructions does.

    An unknown style falls back to quick there, so it falls back to quick here:
    an answer written against the quick length rules is judged against the quick
    ceiling, whatever the request called itself.
    """
    style = str(answer_style or DEFAULT_ANSWER_STYLE).lower()
    return style if style in ANSWER_STYLES else DEFAULT_ANSWER_STYLE


def answer_fragments(text: str) -> list:
    """Every line of section 1, and every sentence inside those lines.

    Both, because a stray citation marker can sit on a line of its own or at
    the end of another sentence, and the sentence splitter alone would miss the
    first while a line scan alone would miss the second.
    """
    seen, out = set(), []
    for line in (text or "").splitlines():
        for piece in [line] + RE_SENTENCE.split(line):
            piece = piece.strip()
            if piece and piece not in seen:
                seen.add(piece)
                out.append(piece)
    return out


def citation_only_fragments(direct_answer: str) -> list:
    """Fragments of section 1 that are nothing but citation markers.

    Phase 14.3.5. The AMZN answer ended "...consume cash indefinitely [1][5].
    [1][5]" - and that trailing "[1][5]" cites nothing. It attaches to no
    claim, so no reader can check it against anything, and the citation audit
    never saw it because a nine-character fragment is far below the length at
    which a sentence counts as a claim. It is a formatting failure that looks
    like diligence, which is the worst kind.

    Deterministic and narrow: strip the markers, and if what is left holds no
    letter and no digit while at least one marker was present, the fragment
    carries no claim at all.
    """
    found = []
    for piece in answer_fragments(direct_answer):
        if not RE_CITATION.search(piece):
            continue
        if not re.search(r"[A-Za-z0-9]", RE_CITATION.sub(" ", piece)):
            found.append(piece)
    return found


def generation_completeness(answer: str, sections: dict = None) -> dict:
    """Is this a FINISHED six-section answer? Reports; never rewrites.

    Phase 14.3.3. Two answers in the 14.3.2 live run looked like they stopped
    mid-sentence. They had not - the review report had cut them - but nothing
    in the pipeline could have told the difference, and an answer that really
    does stop mid-sentence is less trustworthy than its own Confidence line
    claims. So this is checked and it caps the grade, exactly like the uncited
    claim check does.
    """
    found = sections if sections is not None else parse_sections(answer)
    missing = [name for name in ANSWER_SECTIONS if name not in found]
    reasons = []
    if missing:
        reasons.append("missing section(s): " + ", ".join(missing))
    for name in ANSWER_SECTIONS:
        if name in missing:
            continue
        defect = unfinished_tail(found.get(name) or "", prose=name in PROSE_SECTIONS)
        if defect:
            reasons.append(f"section '{name}' {defect}")
    # Phase 14.3.5. A citation marker standing on its own is not an unfinished
    # sentence, but it is an unfinished answer: the marker was meant to sit
    # inside a claim and does not.
    stray = citation_only_fragments(found.get("Direct Answer") or "")
    if stray:
        reasons.append(f"section 'Direct Answer' carries {len(stray)} standalone "
                       f"citation-only fragment(s), which cite nothing - first "
                       f"\"{stray[0][:40]}\"")
    # A generation that stopped inside section 6 often leaves the heading with
    # no grade under it. Across the same 60 answers a grade was found 60 times
    # out of 60, so its absence is a defect rather than a style.
    if "Confidence" not in missing and extract_confidence(found) not in ("High", "Medium", "Low"):
        reasons.append("section 'Confidence' names no grade of High, Medium or Low")
    return {"incomplete": bool(reasons), "reasons": reasons, "missing_sections": missing}


def compliance_failures(audit: dict, answer_style: str = "") -> list:
    """The defects a repair pass can fix without any new evidence.

    Three, and they have one thing in common: each is repairable by rewriting
    the answer the model already wrote, from the evidence it already had. Two
    are failures to attach a citation to the claim it supports; the third,
    added in Phase 14.3.6, is a quick answer over its own stated ceiling.

    Everything else an audit can find - a missing section, a cut generation, an
    attribution violation - is either not repairable by rewriting or belongs to
    a path that already owns it, and is left to the ceilings that handle it.

    A deep answer is never over-length here: 220 is the quick ceiling, and deep
    has its own and much larger room by design.
    """
    failures = []
    citation = audit.get("citation_audit") or {}
    uncited = (list(citation.get("uncited_sentences") or [])
               + list(citation.get("uncited_bullets") or []))
    if uncited:
        failures.append(
            f"{len(uncited)} statement(s) in the Direct Answer carry no citation, starting with "
            f"\"{uncited[0][:140]}\" - put its citation markers inside that sentence")
    stray = list(audit.get("citation_only_fragments") or [])
    if stray:
        failures.append(
            f"{len(stray)} citation marker(s) stand on their own, starting with "
            f"\"{stray[0][:40]}\" - a marker must sit in the sentence whose claim it supports, "
            f"never alone")

    # Phase 14.3.6. The quick ceiling, which the writing instructions state and
    # which three phases of prose did not reliably produce.
    if resolved_answer_style(answer_style) == "quick":
        direct = (audit.get("sections") or {}).get("Direct Answer") or ""
        count = answer_word_count(direct)
        if count > QUICK_DIRECT_WORD_CEILING:
            failures.append(
                f"the Direct Answer runs {count} words against a maximum of "
                f"{QUICK_DIRECT_WORD_CEILING} - cut it to {QUICK_DIRECT_WORD_CEILING} or fewer "
                f"by removing redundancy and excess detail, not by dropping a point, a risk "
                f"category or a citation")
    return failures


def compliance_pass(client, settings: dict, user_input: str, answer_text: str,
                    audit: dict, calls: "AnswerCallBudget") -> dict:
    """ONE repair call, or none. Phase 14.3.5.

    Three phases of prompt wording failed on the same two defects, so this does
    not add wording - it audits the answer and, if either defect is present,
    asks for that one thing to be fixed.

    What the retry is given: the SAME instructions plus the repair pass, the
    SAME user input - the same question and the same evidence pack, with the
    previous answer appended for it to repair - and the SAME answer style.
    There is no retrieval in this function, no access to anything that could
    retrieve, and nothing is added to the pack.

    What happens to the result: it REPLACES the previous answer. The two are
    never stitched together. If the repair did not work, the replacement is
    kept and flagged, and the ceilings cap it to Low exactly as before - which
    is the same outcome the un-repaired answer would have had.
    """
    failures = compliance_failures(audit, settings.get("answer_style"))
    if not failures:
        return {"used": False, "reason": ""}

    joined = "; ".join(failures)
    if calls.remaining < 1:
        LOGGER.warning("The answer needs a compliance repair (%s) but the call budget for this "
                       "question is spent (%d/%d). No repair was attempted; the answer is "
                       "flagged and capped at Low.", joined, calls.used, calls.limit)
        return {"used": False,
                "reason": f"needed: {joined}; not attempted - the per-question call budget "
                          f"({calls.used}/{calls.limit}) was already spent"}

    LOGGER.warning("Compliance repair: %s. Asking ONCE more with the same question, the same "
                   "evidence and the same style.", joined)
    result = call_answer_model(
        client, settings, user_input + REPAIR_INPUT_HEADER + answer_text, calls,
        extra_instructions=COMPLIANCE_REPAIR_INSTRUCTION.format(failures=joined),
        why="compliance-retry")
    return {"used": True, "reason": f"repaired: {joined}", **result}


def audit_answer(answer: str, selected: list) -> dict:
    """Deterministic checks on the generated text. Flags, never rewrites."""
    sections = parse_sections(answer)
    missing_sections = [name for name in ANSWER_SECTIONS if name not in sections]
    confidence_stated = extract_confidence(sections)
    completeness = generation_completeness(answer, sections)

    citations = audit_citations(sections, answer, selected)
    directness_hits, directness_sections = detect_directness(sections, answer)

    # Mask the phrases that only look like investment calls, then look for the
    # real thing. Masking rather than post-filtering means "sell-side" is never
    # even visible to the patterns.
    #
    # Phase 8.1 adds the file names of the supplied evidence to the mask. A
    # sell-side note is routinely named after its own rating
    # ("MSFT_Overweight_PT550.pdf"), and section 3 is required to list file
    # names. Flagging an answer for correctly citing the name of a document is
    # the same class of false positive Phase 6.1 fixed for "sell-side": it
    # trains a reader to ignore the audit, which is worse than not having one.
    #
    # Phase 8.2 pulled the whole thing into audit_language_text(), which also
    # folds Unicode dashes to ASCII and masks the bibliography. See there.
    masked = audit_language_text(answer or "", selected)
    flagged = []
    for pattern, label in FORBIDDEN_PATTERNS:
        match = pattern.search(masked)
        if match:
            flagged.append(f"{label}: '{match.group(0).strip()}'")

    direct = sections.get("Direct Answer", "")
    no_citation_in_answer = bool(direct.strip()) and not RE_CITATION.search(direct)

    warnings = []
    audit_flags = list(citations["flags"])
    if missing_sections:
        warnings.append("answer is missing required section(s): " + ", ".join(missing_sections))
        audit_flags.append("missing section(s): " + ", ".join(missing_sections))
    # Phase 14.3.3. The missing-section case is already named above, so only
    # the tail defects are added here - the same defect must not be reported
    # twice under two names.
    tail_reasons = [r for r in completeness["reasons"] if not r.startswith("missing section")]
    if tail_reasons:
        warnings.append("the generation looks unfinished: " + "; ".join(tail_reasons)
                        + " - confidence is capped at Low and nothing was rewritten")
        audit_flags.append("generation_incomplete: " + "; ".join(tail_reasons))
    if citations["unknown_citations"]:
        warnings.append("answer cites evidence numbers that were never provided: "
                        + ", ".join(f"[{n}]" for n in citations["unknown_citations"]))
    if no_citation_in_answer:
        warnings.append("the Direct Answer section contains no citation")
    if citations["uncited_sentences"]:
        warnings.append(f"{len(citations['uncited_sentences'])} sentence(s) in Direct Answer "
                        "state something without citing anything")
    if citations["uncited_bullets"]:
        warnings.append(f"{len(citations['uncited_bullets'])} bullet(s) in Direct Answer carry "
                        "no citation")
    if citations["unused_citations"]:
        warnings.append(f"{len(citations['unused_citations'])} supplied chunk(s) were never "
                        "cited - the evidence pack was wider than the answer used")
    if citations["listed_only_citations"]:
        warnings.append("citation(s) appear only in Source Citations and are used nowhere: "
                        + ", ".join(f"[{n}]" for n in citations["listed_only_citations"]))
    if flagged:
        warnings.append("language check flagged: " + "; ".join(flagged))

    if directness_hits:
        where = " / ".join(directness_sections) or "the answer"
        warnings.append(f"the answer states in {where} that its own evidence is not directly "
                        f"responsive (matched: {'; '.join(directness_hits)}) - confidence is "
                        "capped at Low")
        audit_flags.append(f"answer_directness_cap_applied: true (in {where})")
        if confidence_stated and confidence_stated != "Low":
            warnings.append(f"CONTRADICTION: the answer says in {where} that the evidence is not "
                            f"directly responsive but states {confidence_stated} confidence")
            audit_flags.append(f"stated confidence {confidence_stated} contradicts the answer's "
                               "own statement that the evidence is not direct")

    return {"sections": sections, "missing_sections": missing_sections,
            "generation_incomplete": completeness["incomplete"],
            "generation_incomplete_reasons": completeness["reasons"],
            # Phase 14.3.5. Read by compliance_failures() to decide whether a
            # repair pass is warranted. Reported, never repaired in place.
            "citation_only_fragments": citation_only_fragments(
                sections.get("Direct Answer") or ""),
            "citations_used": citations["citations_used"],
            "unknown_citations": citations["unknown_citations"],
            "unused_citations": citations["unused_citations"],
            "citation_audit": citations,
            "directness_hits": directness_hits,
            "directness_sections": directness_sections,
            "flagged_language": flagged, "audit_flags": audit_flags,
            "warnings": warnings, "confidence_stated": confidence_stated}


# ---------------------------------------------------------------------------
# Output writing
# ---------------------------------------------------------------------------

def format_pages(start, end) -> str:
    try:
        start_i = int(start) if start not in (None, "", 0) else 0
        end_i = int(end) if end not in (None, "", 0) else 0
    except (TypeError, ValueError):
        return "no page info"
    if start_i and end_i:
        return f"{start_i}-{end_i}" if start_i != end_i else str(start_i)
    if start_i:
        return str(start_i)
    return "no page info"


def source_table_lines(selected: list) -> list:
    lines = []
    for cand in selected:
        lines.append(
            f"[{cand['citation']}] {cand.get('file_name') or '(unknown)'}"
            f" | {cand.get('folder_date_guess') or '(no date)'}"
            f" | {cand.get('source_type_guess') or '(no source type)'}"
            f" | {cand.get('document_type_guess') or '(no document type)'}"
            f" | pages {format_pages(cand.get('page_start_guess'), cand.get('page_end_guess'))}"
            f" | {cand['chunk_id']}"
            f" | {cand.get('relative_path') or '(no path)'}"
        )
    return lines


def source_table_markdown(selected: list) -> str:
    header = ("| # | file_name | date | source_type | document_type | pages | chunk_id | "
              "relative_path |")
    rule = "|---|---|---|---|---|---|---|---|"
    rows = []
    for cand in selected:
        cells = [
            f"[{cand['citation']}]",
            str(cand.get("file_name") or "(unknown)").replace("|", "/"),
            str(cand.get("folder_date_guess") or "(no date)"),
            str(cand.get("source_type_guess") or "(none)"),
            str(cand.get("document_type_guess") or "(none)"),
            format_pages(cand.get("page_start_guess"), cand.get("page_end_guess")),
            f"`{cand['chunk_id']}`",
            str(cand.get("relative_path") or "(none)").replace("|", "/"),
        ]
        rows.append("| " + " | ".join(cells) + " |")
    return "\n".join([header, rule] + rows) if rows else "_No sources._"


def yaml_escape(value: str) -> str:
    return '"' + str(value or "").replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ") + '"'


def write_answer_markdown(path: Path, project_root: Path, context: dict) -> None:
    assert_inside_project(path, project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    selected = context["selected"]

    lines = [
        f"# Ask Brain answer - {context['ticker']}",
        "",
        f"**Question:** {context['question']}",
        "",
        f"- generated_by: `ask_brain.py` ({PIPELINE_VERSION})",
        f"- answer_model: `{context['answer_model']}`",
        f"- embedding_model: `{context['embedding_model']}`",
        f"- created_at: {context['created_at']}",
        f"- retrieval: `{context['retrieval_mode_effective']}` mode, top_k {context['top_k']}, "
        f"{context['candidates_considered']} candidate(s) -> "
        f"{len(selected)} evidence chunk(s) from "
        f"{len({c['document_id'] for c in selected})} document(s)",
        f"- filters: {context['filter_summary']}",
        f"- source_priority: `{context['source_priority']}` "
        f"(question intent: {context['intent']['intent']}, preference applied: "
        f"{context['preference']})",
        f"- rule-based confidence: **{context['confidence_rule']}** "
        f"({context['confidence_rule_reason']})",
        f"- model-stated confidence: **{context['confidence_stated'] or 'not stated'}**",
        f"- **final confidence after audit: {context['final_confidence']}**"
        + ("  _(capped: the answer says its own evidence is not directly responsive)_"
           if context["directness_cap_applied"] else ""),
        f"- answer style: `{context.get('answer_style') or DEFAULT_ANSWER_STYLE}` "
        "(how it was written up; retrieval is identical for either)",
        f"- answer written by: {'the answer model' if context['answer_generated'] else 'this script (no model was called)'}",
        *([f"- compliance repair: ONE retry, same question and same evidence "
           f"({context['compliance_retry_reason']})"]
          if context.get("compliance_retry_used") else []),
        *([f"- generation: status `{context.get('generation_status') or 'unknown'}`"
           + (f", reported reason `{context['generation_incomplete_reason']}`"
              if context.get("generation_incomplete_reason") else "")
           + (f", budget {context['answer_max_output_tokens']} output tokens"
              if context.get("answer_max_output_tokens") else "")
           + (", retried ONCE with the same question and the same evidence"
              if context.get("answer_retry_used") else "")
           + ("  _**INCOMPLETE** - the text below did not finish; nothing was rewritten_"
              if context.get("generation_incomplete") else "")]
          if context.get("generation_incomplete")
          or context.get("answer_retry_used")
          or (context.get("generation_status") not in ("", "completed")) else []),
        "",
        "> This answer was drafted from retrieved text only. It is not verified, not a",
        "> conclusion, and not investment advice. **AI drafts. Humans verify. Cutler decides.**",
        "",
        "---",
        "",
    ]

    # Phase 8.1. If the run was stopped before the model, say so at the top -
    # a reader should never have to infer from the prose that no model ran.
    if context["recommendation_refused"]:
        lines += ["## This question was refused", "",
                  "It asks what Cutler should do with a security, which this system does not "
                  "answer. Detected because it "
                  f"{context['recommendation_reason'] or 'asks for a decision'}. "
                  "No answer model was called.", "", "---", ""]
    elif context["unsupported_subject"]:
        lines += ["## The subject of this question is not in the evidence", "",
                  "None of the distinctive terms of the question ("
                  + ", ".join(f"`{t}`" for t in context["subject_terms_missing"])
                  + ") appears anywhere in the selected text, so no answer model was called and "
                    "nothing was written around the chunks retrieval happened to return.",
                  "", "---", ""]

    if context["warnings"]:
        lines += ["## Automated checks flagged this answer", ""]
        lines += [f"- {w}" for w in context["warnings"]]
        lines += ["", "_These are deterministic checks on the generated text. Nothing was "
                      "rewritten - read the answer with these in mind._", "", "---", ""]

    lines += [context["answer"], "", "---", "", "## Source table", "",
              source_table_markdown(selected), ""]

    if context["all_candidates"]:
        lines += ["", "## Retrieval trace (all candidates, before selection)", "",
                  "`vec` and `kw` are each retriever's normalised 0-1 score; a dash means that "
                  "retriever never returned this chunk. `score` is the combined score selection "
                  "actually ranked on.",
                  "",
                  "| rank | vec | kw | score | selected | document_type | file_name | chunk_id |",
                  "|---|---|---|---|---|---|---|---|"]
        chosen = {c["chunk_id"] for c in selected}

        def cell(value):
            return f"{value:.3f}" if isinstance(value, (int, float)) else "-"

        for cand in context["all_candidates"]:
            lines.append(
                f"| {cand['rank']} | {cell(cand.get('vector_score_norm'))} | "
                f"{cell(cand.get('keyword_score_norm'))} | {cell(cand.get('base_score'))} | "
                f"{'yes' if cand['chunk_id'] in chosen else 'no'} | "
                f"{cand.get('document_type_guess') or '(none)'} | "
                f"{str(cand.get('file_name') or '(unknown)').replace('|', '/')} | "
                f"`{cand['chunk_id']}` |")
        lines.append("")

    if context["audit_flags"]:
        lines += ["", "## Citation audit", ""]
        lines += [f"- {flag}" for flag in context["audit_flags"]]
        lines += ["", "_Flags only. Nothing was rewritten, and none of these on its own means "
                      "the answer is wrong - they mark the sentences to check first._", ""]

    lines += ["", f"Retrieval log: `{context['retrieval_log_rel']}`", ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    LOGGER.info("Wrote answer: %s", path)


def vault_target_exists(vault_root: Path, relative: str) -> bool:
    return (vault_root / relative).exists()


def write_obsidian_note(path: Path, project_root: Path, context: dict) -> None:
    """An Obsidian note that links only to targets that actually exist.

    Phase 4.2 established the rule: never write a wikilink whose target is not
    there. Anything unresolved is rendered as inline code instead.
    """
    assert_inside_project(path, project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    vault_root = project_root / "05_Obsidian_Brain"
    selected = context["selected"]

    front = [
        "---",
        "type: ask_brain_answer",
        f"ticker: {context['ticker']}",
        f"question: {yaml_escape(context['question'])}",
        "generated_by: ask_brain.py",
        f"pipeline_version: {PIPELINE_VERSION}",
        f"answer_model: {context['answer_model']}",
        f"embedding_model: {context['embedding_model']}",
        f"created_at: {context['created_at']}",
        "human_review_required: true",
        f"confidence_stated: {context['confidence_stated'] or 'not stated'}",
        f"confidence_rule: {context['confidence_rule']}",
        f"final_confidence: {context['final_confidence']}",
        f"retrieval_mode: {context['retrieval_mode_effective']}",
        f"source_priority: {context['source_priority']}",
        f"filters: {yaml_escape(context['filter_summary'])}",
        f"answer_directness_flag: {str(bool(context['answer_directness_flag'])).lower()}",
        f"directness_cap_applied: {str(bool(context['directness_cap_applied'])).lower()}",
        f"evidence_chunks: {len(selected)}",
        f"evidence_documents: {len({c['document_id'] for c in selected})}",
        "---",
        "",
    ]

    nav = []
    if vault_target_exists(vault_root, "00_Dashboard/Cutler Research Brain Dashboard.md"):
        nav.append("[[Cutler Research Brain Dashboard]]")
    else:
        nav.append("`Cutler Research Brain Dashboard` (not in the vault yet)")
    company_rel = f"02_Company_Pages/{context['ticker']}.md"
    if vault_target_exists(vault_root, company_rel):
        nav.append(f"[[02_Company_Pages/{context['ticker']}|{context['ticker']}]]")
    else:
        nav.append(f"`{context['ticker']}` (no company page in the vault)")

    lines = front + [
        f"# Ask Brain - {context['ticker']} - {context['created_at']}",
        "",
        " | ".join(nav),
        "",
        f"**Question:** {context['question']}",
        "",
        "> Drafted from retrieved source text. **Not verified. Not a conclusion. Not advice.**",
        "> A human must check every citation against the source document before this is used.",
        "> AI drafts. Humans verify. Cutler decides.",
        "",
        "---",
        "",
    ]

    if context["warnings"]:
        lines += ["## Automated checks flagged this answer", ""]
        lines += [f"- {w}" for w in context["warnings"]]
        lines += ["", "---", ""]

    lines += [context["answer"], "", "---", "", "## Sources", ""]

    for cand in selected:
        doc_id = cand["document_id"]
        label = str(cand.get("file_name") or doc_id).replace("|", "/").replace("]", ")")
        stub_rel = f"09_Document_Card_Index/Card_Stubs/{doc_id}.md"
        if vault_target_exists(vault_root, stub_rel):
            link = f"[[{doc_id}|{label}]]"
        else:
            link = f"`{label}` (no card stub - run refresh_obsidian_links.py)"
        lines.append(
            f"- **[{cand['citation']}]** {link} - "
            f"{cand.get('folder_date_guess') or 'no date'} - "
            f"{cand.get('source_type_guess') or 'no source type'} / "
            f"{cand.get('document_type_guess') or 'no document type'} - "
            f"pages {format_pages(cand.get('page_start_guess'), cand.get('page_end_guess'))} - "
            f"chunk `{cand['chunk_id']}`")
        lines.append(f"  - path: `{cand.get('relative_path') or '(none)'}`")

    lines += ["", "---", "",
              f"Answer file: `{context['answer_rel']}`",
              f"Retrieval log: `{context['retrieval_log_rel']}`", ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    LOGGER.info("Wrote Obsidian note: %s", path)


def write_retrieval_log(path: Path, project_root: Path, context: dict) -> None:
    assert_inside_project(path, project_root)
    path.parent.mkdir(parents=True, exist_ok=True)

    def rounded(value):
        return round(float(value), 6) if isinstance(value, (int, float)) else None

    def candidate_record(cand: dict, chosen: bool) -> dict:
        return {
            "rank": cand["rank"],
            "chunk_id": cand["chunk_id"],
            "document_id": cand["document_id"],
            "file_name": cand.get("file_name"),
            "relative_path": cand.get("relative_path"),
            "folder_date_guess": cand.get("folder_date_guess"),
            "source_type_guess": cand.get("source_type_guess"),
            "document_type_guess": cand.get("document_type_guess"),
            "page_start_guess": cand.get("page_start_guess"),
            "page_end_guess": cand.get("page_end_guess"),
            "distance": cand.get("distance"),
            "similarity": rounded(cand.get("similarity")),
            "kind": cand["kind"],
            # --- Phase 6 retrieval diagnostics -----------------------------
            "vector_rank": cand.get("vector_rank"),
            "vector_score": rounded(cand.get("vector_score")),
            "vector_score_norm": rounded(cand.get("vector_score_norm")),
            "keyword_rank": cand.get("keyword_rank"),
            "keyword_score": rounded(cand.get("keyword_score")),
            "keyword_score_norm": rounded(cand.get("keyword_score_norm")),
            "matched_terms": cand.get("matched_terms") or [],
            "phrase_hit": bool(cand.get("phrase_hit")),
            "source_priority_bonus": rounded(cand.get("source_priority_bonus")),
            "recency_bonus": rounded(cand.get("recency_bonus")),
            "hybrid_score": rounded(cand.get("base_score")),
            "selected": chosen,
            "selection_score": rounded(cand.get("selection_score")),
            "selection_reason": cand.get("selection_reason", ""),
            "citation": cand.get("citation") if chosen else None,
            "chunk_char_count": cand.get("chunk_char_count"),
        }

    chosen_ids = {c["chunk_id"] for c in context["selected"]}
    payload = {
        "pipeline_version": PIPELINE_VERSION,
        "created_at": context["created_at"],
        "ticker": context["ticker"],
        "question": context["question"],
        "collection": context["collection"],
        "embedding_model": context["embedding_model"],
        "answer_model": context["answer_model"],
        "top_k": context["top_k"],
        "max_chunks": context["max_chunks"],
        "max_chunks_per_document": context["max_chunks_per_document"],
        "answer_style": context.get("answer_style") or DEFAULT_ANSWER_STYLE,
        "source_priority": context["source_priority"],
        "question_intent": context["intent"],
        "preference_applied": context["preference"],
        "retrieval_mode_requested": context["retrieval_mode"],
        "retrieval_mode_effective": context["retrieval_mode_effective"],
        "filters": context["filter_summary"],
        "filter_diagnostics": context["filter_diagnostics"],
        "prefer_recent": context["prefer_recent"],
        "selection_weights": {
            "hybrid_vector_weight": W_VECTOR,
            "hybrid_keyword_weight": W_KEYWORD,
            "hybrid_priority_weight": W_PRIORITY,
            "hybrid_recency_weight": W_RECENCY if context["prefer_recent"] else 0.0,
            "priority_bonus": PRIORITY_BONUS,
            "priority_penalty": PRIORITY_PENALTY,
            "document_repeat_penalty": DOC_REPEAT_PENALTY,
            "source_repeat_penalty": SOURCE_REPEAT_PENALTY,
            "weak_evidence_similarity": WEAK_EVIDENCE_SIMILARITY,
            "note": ("In vector mode the base score is Phase 5B's similarity + priority "
                     "adjustment. In keyword and hybrid mode it is "
                     "W_VECTOR*vector_norm + W_KEYWORD*keyword_norm + "
                     "W_PRIORITY*priority + optional W_RECENCY*recency. Both then take the "
                     "document and source repeat penalties during selection."),
        },
        "candidates_returned": len(context["all_candidates"]),
        "candidates": [candidate_record(c, c["chunk_id"] in chosen_ids)
                       for c in context["all_candidates"]],
        "selected_chunk_ids": [c["chunk_id"] for c in context["selected"]],
        "selected_documents": sorted({c["document_id"] for c in context["selected"]}),
        "documents_per_source_type": context["per_source"],
        "confidence_rule": context["confidence_rule"],
        "confidence_rule_reason": context["confidence_rule_reason"],
        "confidence_stated": context["confidence_stated"],
        "final_confidence": context["final_confidence"],
        "answer_directness_flag": bool(context["answer_directness_flag"]),
        "directness_cap_applied": bool(context["directness_cap_applied"]),
        "directness_phrases_matched": context["directness_hits"],
        "directness_sections": context["directness_sections"],
        "citations_used": context["citations_used"],
        "unknown_citations": context["unknown_citations"],
        "unused_citations": context["unused_citations"],
        "citation_audit": context["citation_audit"],
        "audit_flags": context["audit_flags"],
        "flagged_language": context["flagged_language"],
        # --- Phase 8.1: why this run answered the way it did ----------------
        "answer_written_by": ("answer model" if context["answer_generated"]
                              else "ask_brain.py (no model call)"),
        "recommendation_request_refused": bool(context["recommendation_refused"]),
        "recommendation_request_reason": context["recommendation_reason"],
        "unsupported_subject": bool(context["unsupported_subject"]),
        "subject_terms_checked": context["subject_terms"],
        "subject_terms_found_in_evidence": context["subject_terms_present"],
        "subject_terms_missing_from_evidence": context["subject_terms_missing"],
        "confidence_ceilings_applied": [
            {"ceiling": ceiling, "reason_code": flag}
            for ceiling, flag in context["confidence_ceilings"]],
        "warnings": context["warnings"],
        "prompt_tokens_estimate": context["prompt_tokens"],
        "output_tokens_estimate": context["output_tokens"],
        "token_counts_are_exact": context["exact_usage"],
        "estimated_cost_usd": context["estimated_cost"],
        "status": context["status"],
        "error_message": context["error_message"],
        "answer_path": context["answer_rel"],
        "obsidian_answer_path": context["obsidian_rel"],
        "note": ("Chunk text is not duplicated here - it lives in "
                 "06_RAG_Index/chunks and in 03_Extracted_Text. This file records what was "
                 "retrieved, how it was ranked, and what was sent to the answer model."),
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    LOGGER.info("Wrote retrieval log: %s", path)


# ---------------------------------------------------------------------------
# Persistence and reports
# ---------------------------------------------------------------------------

def record_run(conn: sqlite3.Connection, context: dict) -> int:
    columns = [c for c in RUN_COLUMNS if c != "run_id"]
    values = {
        "ticker": context["ticker"],
        "question": context["question"],
        "answer_model": context["answer_model"],
        "embedding_model": context["embedding_model"],
        "top_k": context["top_k"],
        "selected_chunks": len(context["selected"]),
        "selected_documents": len({c["document_id"] for c in context["selected"]}),
        "prompt_tokens_estimate": context["prompt_tokens"],
        "output_tokens_estimate": context["output_tokens"],
        "estimated_cost_usd": context["estimated_cost"],
        "answer_path": context["answer_rel"],
        "obsidian_answer_path": context["obsidian_rel"],
        "retrieval_log_path": context["retrieval_log_rel"],
        "created_at": context["created_at"],
        "status": context["status"],
        "error_message": context["error_message"],
        "confidence_stated": context["confidence_stated"],
        "confidence_rule": context["confidence_rule"],
        "confidence_rule_reason": context["confidence_rule_reason"],
        "source_priority": context["source_priority"],
        "citations_used": ",".join(str(n) for n in context["citations_used"]),
        "flagged_language": "; ".join(context["flagged_language"]),
        "pipeline_version": PIPELINE_VERSION,
        "retrieval_mode": context["retrieval_mode"],
        "retrieval_mode_effective": context["retrieval_mode_effective"],
        "answer_directness_flag": "1" if context["answer_directness_flag"] else "0",
        "directness_cap_applied": "1" if context["directness_cap_applied"] else "0",
        "directness_sections": ", ".join(context["directness_sections"]),
        "final_confidence": context["final_confidence"],
        "filter_summary": context["filter_summary"],
        "hybrid_diagnostics_path": context["diagnostics_rel"],
        "audit_flags": "; ".join(context["audit_flags"]),
        "candidates_considered": context["candidates_considered"],
        "avg_vector_score": context["avg_vector_score"],
        "avg_keyword_score": context["avg_keyword_score"],
        "avg_hybrid_score": context["avg_hybrid_score"],
        "recommendation_refused": "1" if context["recommendation_refused"] else "0",
        "unsupported_subject": "1" if context["unsupported_subject"] else "0",
        "subject_terms_missing": ", ".join(context["subject_terms_missing"]),
        "confidence_ceilings": "; ".join(f"{flag} -> {ceiling}"
                                         for ceiling, flag in context["confidence_ceilings"]),
        "answer_generated": "1" if context["answer_generated"] else "0",
        # --- Phase 14.3.3 --------------------------------------------------
        "generation_status": context.get("generation_status") or "",
        "generation_incomplete": "1" if context.get("generation_incomplete") else "0",
        "generation_incomplete_reason": "; ".join(
            context.get("generation_incomplete_reasons") or []
            or ([context["generation_incomplete_reason"]]
                if context.get("generation_incomplete_reason") else [])),
        "answer_max_output_tokens": context.get("answer_max_output_tokens") or 0,
        "answer_retry_used": "1" if context.get("answer_retry_used") else "0",
        # --- Phase 14.3.5 --------------------------------------------------
        "compliance_retry_used": "1" if context.get("compliance_retry_used") else "0",
        "compliance_retry_reason": context.get("compliance_retry_reason") or "",
        "answer_model_calls": context.get("answer_model_calls") or 0,
    }
    marks = ",".join("?" * len(columns))
    cursor = conn.execute(
        f"INSERT INTO ask_brain_runs ({','.join(columns)}) VALUES ({marks})",
        [values[c] for c in columns])
    conn.commit()
    return int(cursor.lastrowid)


def write_csv(path: Path, header: list, rows) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow(header)
        count = 0
        for row in rows:
            writer.writerow(row)
            count += 1
    LOGGER.info("Wrote %s (%d data rows)", path, count)
    return count


DIAGNOSTIC_COLUMNS = [
    "run_id", "created_at", "ticker", "question", "retrieval_mode_effective", "source_priority",
    "filters", "rank", "chunk_id", "document_id", "file_name", "folder_date_guess",
    "source_type_guess", "document_type_guess", "kind", "vector_rank", "vector_score",
    "vector_score_norm", "keyword_rank", "keyword_score", "keyword_score_norm", "phrase_hit",
    "source_priority_bonus", "recency_bonus", "hybrid_score", "selected", "citation",
    "selection_reason",
]


def write_diagnostics(project_root: Path, ticker: str, context: dict, run_id) -> Path:
    """Per-run candidate diagnostics, plus an append to the rolling CSV.

    Two files on purpose: the per-run CSV is what the run's own record points
    at, and the rolling one is what you open to compare runs.
    """
    per_run = (project_root / "06_RAG_Index" / "retrieval_logs"
               / f"{ticker}_{context['slug']}_diagnostics.csv")
    rolling = project_root / "06_RAG_Index" / "reports" / f"{ticker}_retrieval_diagnostics.csv"
    assert_inside_project(per_run, project_root)
    assert_inside_project(rolling, project_root)

    chosen = {c["chunk_id"] for c in context["selected"]}
    citations = {c["chunk_id"]: c["citation"] for c in context["selected"]}

    def value(item, key):
        got = item.get(key)
        if isinstance(got, float):
            return f"{got:.6f}"
        return "" if got is None else got

    rows = []
    for cand in context["all_candidates"]:
        rows.append([
            run_id, context["created_at"], ticker, context["question"],
            context["retrieval_mode_effective"], context["source_priority"],
            context["filter_summary"], cand.get("rank"), cand.get("chunk_id"),
            cand.get("document_id"), cand.get("file_name"), cand.get("folder_date_guess"),
            cand.get("source_type_guess"), cand.get("document_type_guess"), cand.get("kind"),
            value(cand, "vector_rank"), value(cand, "vector_score"),
            value(cand, "vector_score_norm"), value(cand, "keyword_rank"),
            value(cand, "keyword_score"), value(cand, "keyword_score_norm"),
            "yes" if cand.get("phrase_hit") else "no",
            value(cand, "source_priority_bonus"), value(cand, "recency_bonus"),
            value(cand, "base_score"),
            "yes" if cand["chunk_id"] in chosen else "no",
            citations.get(cand["chunk_id"], ""), cand.get("selection_reason", ""),
        ])

    per_run.parent.mkdir(parents=True, exist_ok=True)
    with per_run.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow(DIAGNOSTIC_COLUMNS)
        writer.writerows(rows)

    rolling.parent.mkdir(parents=True, exist_ok=True)
    new_file = not rolling.exists()
    with rolling.open("a", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        if new_file:
            writer.writerow(DIAGNOSTIC_COLUMNS)
        writer.writerows(rows)

    LOGGER.info("Wrote %s (%d candidate rows) and appended to %s",
                per_run.name, len(rows), rolling.name)
    return per_run


def write_reports(conn: sqlite3.Connection, project_root: Path, ticker: str) -> None:
    columns = ["run_id", "created_at", "status", "question", "retrieval_mode",
               "retrieval_mode_effective", "filter_summary", "answer_model", "embedding_model",
               "top_k", "candidates_considered", "selected_chunks", "selected_documents",
               "source_priority", "confidence_stated", "confidence_rule",
               "confidence_rule_reason", "final_confidence", "answer_directness_flag",
               "directness_cap_applied", "directness_sections", "citations_used",
               "audit_flags", "flagged_language",
               "avg_vector_score", "avg_keyword_score", "avg_hybrid_score",
               "prompt_tokens_estimate", "output_tokens_estimate", "estimated_cost_usd",
               "answer_path", "obsidian_answer_path", "retrieval_log_path",
               "hybrid_diagnostics_path", "error_message"]
    rows = conn.execute(
        f"SELECT {','.join(columns)} FROM ask_brain_runs WHERE ticker = ? "
        "ORDER BY run_id DESC", (ticker,)).fetchall()

    report_path = project_root / "06_RAG_Index" / "reports" / f"{ticker}_ask_brain_run_report.csv"
    assert_inside_project(report_path, project_root)
    write_csv(report_path, columns, [[r[c] for c in columns] for r in rows])

    totals = conn.execute(
        "SELECT COUNT(*) AS runs, "
        "  SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed, "
        "  SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed, "
        "  SUM(CASE WHEN status = 'no_evidence' THEN 1 ELSE 0 END) AS no_evidence, "
        "  COALESCE(SUM(estimated_cost_usd), 0) AS cost "
        "FROM ask_brain_runs WHERE ticker = ?", (ticker,)).fetchone()

    index_row = conn.execute(
        "SELECT COUNT(*) AS chunks, COUNT(DISTINCT document_id) AS docs "
        "FROM rag_chunks WHERE ticker = ? AND embedding_status = 'embedded'",
        (ticker,)).fetchone()

    recent = rows[:10]
    lines = [
        f"# Ask Brain summary - {ticker}",
        "",
        f"_Generated {iso(utc_now())} by `ask_brain.py` ({PIPELINE_VERSION})._",
        "",
        "## What Ask Brain is",
        "",
        "Ask Brain answers a question **only** from text it retrieved out of the "
        f"{ticker} Phase 5A index. It embeds the question, pulls a handful of chunks, and asks "
        "the answer model to write from those chunks and cite each one. It has no access to "
        "anything else: not the wider archive, not the open web, not the model's own memory of "
        "this company.",
        "",
        "An answer here is a **draft assembled from quotations**. It is not verified, it is not "
        "a conclusion, and it is not investment advice. Every citation points at a real file so "
        "a person can go and check it.",
        "",
        "> **AI drafts. Humans verify. Cutler decides.**",
        "",
        "## Runs",
        "",
        f"- Total runs: **{totals['runs'] or 0}**",
        f"- Completed: **{totals['completed'] or 0}**",
        f"- Failed: **{totals['failed'] or 0}**",
        f"- Stopped with no evidence (no answer model called): **{totals['no_evidence'] or 0}**",
        f"- Estimated spend across all runs: **${float(totals['cost'] or 0):.4f}** "
        "(local estimate from the rates in `.env`, not a bill)",
        "",
        f"Index searched: **{index_row['chunks'] or 0} embedded chunks** across "
        f"**{index_row['docs'] or 0} documents**. Phase 5B does not add to it.",
        "",
        "## Most recent questions",
        "",
    ]
    if recent:
        lines += ["| run | when | status | mode | confidence (stated / rule / final) | chunks | "
                  "docs | question |",
                  "|---|---|---|---|---|---|---|---|"]
        for r in recent:
            question = str(r["question"] or "").replace("|", "/")
            if len(question) > 80:
                question = question[:80] + "..."
            lines.append(
                f"| {r['run_id']} | {r['created_at']} | {r['status']} | "
                f"{r['retrieval_mode_effective'] or '-'} | "
                f"{r['confidence_stated'] or '-'} / {r['confidence_rule'] or '-'} / "
                f"**{r['final_confidence'] or '-'}** | "
                f"{r['selected_chunks']} | {r['selected_documents']} | {question} |")
    else:
        lines.append("_No runs yet._")

    modes = {}
    for r in rows:
        key = r["retrieval_mode_effective"] or r["retrieval_mode"] or "(not recorded)"
        modes[key] = modes.get(key, 0) + 1
    lines += ["", "## Retrieval mode", "",
              "`hybrid` merges vector and keyword retrieval; `vector` is Phase 5B behaviour; "
              "`keyword` is BM25 alone. A run shows `vector` when hybrid was asked for but no "
              "keyword index was available - build one with `build_keyword_index.py`.",
              "", "| mode | runs |", "|---|---|"]
    for name, count in sorted(modes.items(), key=lambda x: -x[1]):
        lines.append(f"| {name} | {count} |")

    directness = [r for r in rows if str(r["directness_cap_applied"] or "0") == "1"]
    lines += ["", "## Answers that said their own evidence was not responsive", "",
              "When an answer says the retrieved evidence does not directly answer the question, "
              "confidence is capped at Low regardless of similarity. The model has read the "
              "evidence; that judgement outranks any score.",
              ""]
    if directness:
        for r in directness[:20]:
            question = str(r["question"] or "").replace("|", "/")
            lines.append(f"- run {r['run_id']} (final: {r['final_confidence'] or '-'}): "
                         f"{question}")
    else:
        lines.append("_None. No answer has reported its own evidence as non-responsive._")

    capped = [r for r in rows if "capped from" in (r["confidence_rule_reason"] or "")]
    lines += ["", "## Where the rule capped the confidence grade", "",
              "Similarity measures how closely retrieval matched the **wording** of a question. "
              "It says nothing about whether the evidence is the right **kind** of source. So a "
              "pack with no direct company, SEC or company-material chunk in it is capped at "
              "**Medium**, and if the question was about what the company reported, filed or "
              "guided to, it is capped at **Low** - a question about reported results answered "
              "entirely out of third-party commentary is not strong evidence for that question, "
              "however well the words matched.",
              ""]
    if capped:
        for r in capped[:20]:
            print_q = str(r["question"] or "").replace("|", "/")
            lines.append(f"- run {r['run_id']} ({r['confidence_rule']}): {print_q}")
            lines.append(f"  - {r['confidence_rule_reason']}")
    else:
        lines.append("_No run has been capped. Every answer's evidence included direct company, "
                     "SEC or company-material text, or was already graded at or below its "
                     "ceiling._")

    flagged = [r for r in rows if (r["flagged_language"] or "").strip()]
    lines += ["", "## Answers the language check flagged", ""]
    if flagged:
        lines.append("These answers contained wording this project does not allow "
                     "(recommendations, ratings, predictions, overclaims). Nothing was "
                     "rewritten - read them with that in mind.")
        lines.append("")
        for r in flagged[:20]:
            lines.append(f"- run {r['run_id']}: {r['flagged_language']}")
    else:
        lines.append("None. No run produced recommendation, rating or prediction language.")

    lines += ["", "## Where the files are", "",
              f"- Answers: `06_RAG_Index\\answer_logs\\{ticker}_<timestamp>_answer.md`",
              f"- Retrieval traces: `06_RAG_Index\\retrieval_logs\\{ticker}_<timestamp>_retrieval.json`",
              f"- Obsidian notes: `05_Obsidian_Brain\\11_Ask_Brain_Answers\\{ticker}_<timestamp>_answer.md`",
              f"- Run report: `06_RAG_Index\\reports\\{ticker}_ask_brain_run_report.csv`",
              "- Log: `logs\\phase5b_ask_brain.log`",
              "",
              "## What a human still has to do",
              "",
              "1. Open each cited document and confirm the sentence really says what the answer "
              "says it says.",
              "2. Check the *Evidence Limitations* and *Needs Human Review* sections - they are "
              "where the honest gaps are recorded.",
              "3. Treat a High confidence grade as a claim about **retrieval**, not about the "
              "world.",
              "4. Nothing here becomes a Cutler view until a person decides it does.",
              ""]

    summary_path = project_root / "02_Metadata_Index" / f"{ticker}_ask_brain_summary.md"
    assert_inside_project(summary_path, project_root)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text("\n".join(lines), encoding="utf-8")
    LOGGER.info("Wrote %s", summary_path)


# ---------------------------------------------------------------------------
# Console output
# ---------------------------------------------------------------------------

def print_answer(context: dict) -> None:
    selected = context["selected"]
    safe_print()
    safe_print("=" * 78)
    safe_print(f"ASK BRAIN - {context['ticker']}")
    safe_print(f"Question   : {context['question']}")
    safe_print(f"Collection : {context['collection']}")
    safe_print(f"Models     : {context['embedding_model']} (query) / "
          f"{context['answer_model']} (answer)")
    safe_print(f"Retrieval  : {context['retrieval_mode_effective']} mode, top_k {context['top_k']}, "
          f"{context['candidates_considered']} candidate(s), priority "
          f"{context['source_priority']} -> {context['preference']}")
    safe_print(f"Filters    : {context['filter_summary']}")
    safe_print(f"Evidence   : {len(selected)} chunk(s) from "
          f"{len({c['document_id'] for c in selected})} document(s)")
    if context["recommendation_refused"]:
        safe_print("STOPPED     : this question asks for an investment decision. Refused before "
                   "any answer model was called.")
    elif context["unsupported_subject"]:
        safe_print("STOPPED     : the question's subject does not appear in the retrieved text ("
                   + ", ".join(context["subject_terms_missing"])
                   + "). No answer model was called.")
    safe_print("=" * 78)
    safe_print()
    safe_print(context["answer"])
    safe_print()
    safe_print("-" * 78)
    safe_print("SOURCE TABLE")
    safe_print("-" * 78)
    for line in source_table_lines(selected):
        for i, wrapped in enumerate(textwrap.wrap(line, width=76,
                                                  subsequent_indent="      ")):
            safe_print(wrapped)
    safe_print()
    if context["warnings"]:
        safe_print("-" * 78)
        safe_print("AUTOMATED CHECKS FLAGGED THIS ANSWER")
        for warning in context["warnings"]:
            for wrapped in textwrap.wrap(f"- {warning}", width=76, subsequent_indent="  "):
                safe_print(wrapped)
        safe_print()
    if context["audit_flags"]:
        safe_print("-" * 78)
        safe_print("CITATION AUDIT")
        for flag in context["audit_flags"]:
            for wrapped in textwrap.wrap(f"- {flag}", width=76, subsequent_indent="  "):
                safe_print(wrapped)
        safe_print()
    safe_print("-" * 78)
    safe_print(f"Confidence  stated by the model : {context['confidence_stated'] or 'not stated'}")
    safe_print(f"Confidence  from the evidence   : {context['confidence_rule']}")
    for wrapped in textwrap.wrap(context["confidence_rule_reason"], width=74,
                                 initial_indent="    ", subsequent_indent="    "):
        safe_print(wrapped)
    safe_print(f"FINAL CONFIDENCE (after audit)  : {context['final_confidence']}")
    if context["directness_cap_applied"]:
        safe_print("    capped: the answer states its own evidence is not directly responsive")
    safe_print(f"Estimated cost of this run      : ${context['estimated_cost']:.5f} "
          f"({'exact token counts' if context['exact_usage'] else 'estimated tokens'})")
    safe_print("=" * 78)
    safe_print("This answer was written from the retrieved chunks above and nothing else. It has")
    safe_print("not been verified. Check every citation in the source document before using any of")
    safe_print("it. It is not a recommendation and not investment advice.")
    safe_print("AI drafts. Humans verify. Cutler decides.")
    safe_print("=" * 78)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Cutler Research Brain Phase 5B - Ask Brain. Answers only from retrieved "
                    "chunks in the ticker's Phase 5A index, with a citation on every claim.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Nothing here is verified and nothing here is advice. Every answer is a draft
            assembled from quotations, for a human to check against the source documents.
            AI drafts. Humans verify. Cutler decides.
        """),
    )
    parser.add_argument("--project-root", required=True,
                        help=r'e.g. "C:\\Users\\AI Admin\\Documents\\CutlerResearchBrain"')
    parser.add_argument("--db", default="data/document_metadata.sqlite",
                        help="SQLite path; relative paths resolve against --project-root.")
    parser.add_argument("--ticker", default=DEFAULT_TICKER,
                        help=f"Ticker index to search (default {DEFAULT_TICKER}).")
    parser.add_argument("--question", required=True, help="The question to answer.")
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K,
                        help=f"Chunks to retrieve before selection (default {DEFAULT_TOP_K}).")
    parser.add_argument("--max-chunks", type=int, default=DEFAULT_MAX_CHUNKS,
                        help=f"Evidence chunks sent to the answer model "
                             f"(default {DEFAULT_MAX_CHUNKS}).")
    parser.add_argument("--max-chunks-per-document", type=int,
                        default=DEFAULT_MAX_CHUNKS_PER_DOCUMENT,
                        help=f"Cap per document, for source diversity "
                             f"(default {DEFAULT_MAX_CHUNKS_PER_DOCUMENT}).")
    parser.add_argument("--source-priority", default=DEFAULT_SOURCE_PRIORITY,
                        choices=["balanced", "company", "analyst", "none"],
                        help="balanced: let the question's wording pick the tilt (default). "
                             "company: prefer SEC filings and company material. "
                             "analyst: prefer sell-side and industry research. "
                             "none: rank on similarity alone.")
    parser.add_argument("--save", dest="save", action="store_true", default=True,
                        help="Also write the answer into the Obsidian vault (default).")
    parser.add_argument("--no-save", dest="save", action="store_false",
                        help="Do not write the answer into the Obsidian vault.")
    # Phase 14. How the answer is WRITTEN, not what is retrieved for it. The
    # default is "quick", which is the shape Phase 14 defines; retrieval is
    # unaffected either way.
    parser.add_argument("--answer-style", default=DEFAULT_ANSWER_STYLE,
                        choices=list(ANSWER_STYLE_CHOICES),
                        help="How long and how synthesised the written answer should be. "
                             "Retrieval is identical for both.")
    parser.add_argument("--collection", default="", help="Override the Chroma collection name.")
    parser.add_argument("--verbose", action="store_true", help="Chattier console output.")

    # ---- Phase 6 ----------------------------------------------------------
    retrieval = parser.add_argument_group("Phase 6 retrieval")
    retrieval.add_argument("--retrieval-mode", default=DEFAULT_RETRIEVAL_MODE,
                           choices=list(RETRIEVAL_MODES),
                           help="hybrid (default): merge vector and keyword retrieval. "
                                "vector: Phase 5B behaviour, embeddings only. "
                                "keyword: BM25 only, needs build_keyword_index.py.")
    retrieval.add_argument("--prefer-recent", action="store_true", default=False,
                           help="Add a recency bonus based on folder_date_guess.")
    retrieval.add_argument("--debug-retrieval", action="store_true",
                           help="Print every candidate with its component scores.")

    filters = parser.add_argument_group("Phase 6 metadata filters (applied before ranking)")
    filters.add_argument("--date-from", default="", help="Earliest folder_date_guess, YYYY-MM-DD.")
    filters.add_argument("--date-to", default="", help="Latest folder_date_guess, YYYY-MM-DD.")
    filters.add_argument("--source-type", action="append", default=[],
                         help='e.g. "SEC Filing". Repeatable.')
    filters.add_argument("--document-type", action="append", default=[],
                         help='e.g. "8-K". Repeatable.')
    filters.add_argument("--must-contain", default="",
                         help="Only chunks whose text contains this string.")
    # Phase 13.1. A follow-up such as "why?" or "tell me more about the second
    # point" is about the answer already on screen, so the honest thing is to
    # look in the documents that answer was written from. This is a FILTER, in
    # the same place and of the same kind as the five above it: it narrows the
    # candidate pool before ranking and touches no weight, no prompt and no
    # score. Empty - the default - changes nothing whatsoever.
    filters.add_argument("--restrict-to-documents", action="append", default=[],
                         metavar="DOCUMENT_ID",
                         help="Only chunks from these document ids. Repeatable. Used by the "
                              "local UI for follow-up questions about an answer already given.")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    project_root = Path(args.project_root).expanduser().resolve()
    validate_project_root(project_root)          # before any file is created
    configure_console_encoding()                 # before anything is printed
    setup_logging(project_root / "logs" / "phase5b_ask_brain.log", args.verbose)

    ticker = safe_ticker(args.ticker)
    question = (args.question or "").strip()
    if not question:
        raise SystemExit("ERROR: --question is empty.")
    if args.top_k < 1 or args.max_chunks < 1 or args.max_chunks_per_document < 1:
        raise SystemExit("ERROR: --top-k, --max-chunks and --max-chunks-per-document must be "
                         ">= 1.")
    if args.max_chunks > args.top_k:
        LOGGER.warning("--max-chunks (%d) is larger than --top-k (%d); at most %d chunks can "
                       "be selected.", args.max_chunks, args.top_k, args.top_k)

    db_path = Path(args.db).expanduser()
    if not db_path.is_absolute():
        db_path = (project_root / db_path).resolve()

    started = utc_now()
    slug = timestamp_slug(started)
    created_at = iso(started)

    LOGGER.info("=" * 70)
    LOGGER.info("Ask Brain %s | ticker %s | top_k %d | max_chunks %d | per_doc %d | priority %s",
                PIPELINE_VERSION, ticker, args.top_k, args.max_chunks,
                args.max_chunks_per_document, args.source_priority)
    LOGGER.info("Question: %s", question)

    conn = connect(db_path)
    create_schema(conn)
    index_info = require_index(conn, ticker)

    settings = load_environment(project_root)
    # Phase 14. Carried in settings because that is what the answer call reads;
    # it changes the written answer and nothing about retrieval.
    settings["answer_style"] = str(getattr(args, "answer_style", DEFAULT_ANSWER_STYLE)
                                   or DEFAULT_ANSWER_STYLE).lower()
    embedding_model = index_info["embedding_model"] or settings["embedding_model"]
    if (index_info["embedding_model"]
            and index_info["embedding_model"] != settings["embedding_model"]):
        LOGGER.warning("The index was built with '%s' but .env says '%s'. Using the INDEX's "
                       "model - a query embedded with a different model is not comparable.",
                       index_info["embedding_model"], settings["embedding_model"])

    collection_name = (args.collection.strip() or index_info["collection"]
                       or collection_name_for(ticker))
    collection = open_collection(project_root, ticker, collection_name)

    answer_dir = project_root / "06_RAG_Index" / "answer_logs"
    retrieval_dir = project_root / "06_RAG_Index" / "retrieval_logs"
    obsidian_dir = project_root / "05_Obsidian_Brain" / "11_Ask_Brain_Answers"
    for folder in (answer_dir, retrieval_dir):
        assert_inside_project(folder, project_root)
        folder.mkdir(parents=True, exist_ok=True)

    answer_path = answer_dir / f"{ticker}_{slug}_answer.md"
    retrieval_path = retrieval_dir / f"{ticker}_{slug}_retrieval.json"
    obsidian_path = obsidian_dir / f"{ticker}_{slug}_answer.md"

    intent = classify_question(question)
    preference = preference_for(args.source_priority, intent["intent"])
    if args.source_priority in ("company", "analyst", "none"):
        LOGGER.info("Question intent: %s. --source-priority %s OVERRIDES it -> preference: %s",
                    intent["intent"], args.source_priority, preference)
    else:
        LOGGER.info("Question intent: %s -> source preference: %s", intent["intent"], preference)

    # --- Phase 8.1, Part A: is this a request for an investment decision? ---
    # Decided here, from the question alone, before a single API call. The
    # refusal that follows is written by this file, not generated.
    recommendation = detect_recommendation_request(question)
    if recommendation["is_request"]:
        LOGGER.warning("This question asks for an investment decision (%s). It will be refused; "
                       "no answer model will be called.", recommendation["reason"])

    # --- Phase 6: which retrieval mode can actually run --------------------
    kw = kw_index = None
    mode = args.retrieval_mode
    if mode in ("keyword", "hybrid"):
        kw, kw_index = load_keyword_index(project_root, ticker, conn,
                                          required=(mode == "keyword"))
        if kw_index is None:
            mode = "vector"
    if mode != args.retrieval_mode:
        LOGGER.warning("Requested %s retrieval, running %s.", args.retrieval_mode, mode)
    LOGGER.info("Retrieval mode: %s", mode)

    context = {
        "ticker": ticker, "question": question, "created_at": created_at, "slug": slug,
        "collection": collection_name, "embedding_model": embedding_model,
        "answer_model": settings["answer_model"], "top_k": args.top_k,
        # Phase 14: how the answer was written up. Recorded so a run can be
        # reproduced exactly, including its length discipline.
        "answer_style": settings["answer_style"],
        "max_chunks": args.max_chunks,
        "max_chunks_per_document": args.max_chunks_per_document,
        "source_priority": args.source_priority, "intent": intent, "preference": preference,
        "selected": [], "all_candidates": [], "per_source": {},
        "answer": "", "warnings": [], "flagged_language": [], "citations_used": [],
        "unknown_citations": [], "unused_citations": [], "audit_flags": [],
        "citation_audit": {}, "directness_hits": [], "directness_sections": [],
        "answer_directness_flag": False, "directness_cap_applied": False,
        "confidence_stated": "", "confidence_rule": "Low",
        "confidence_rule_reason": "not computed", "final_confidence": "Low",
        "prompt_tokens": 0, "output_tokens": 0, "exact_usage": False,
        # --- Phase 14.3.3: what the generation itself reported -------------
        "generation_status": "", "generation_incomplete": False,
        "generation_incomplete_reason": "", "generation_incomplete_reasons": [],
        "answer_max_output_tokens": 0, "answer_retry_used": False,
        # --- Phase 14.3.5: the bounded compliance repair -------------------
        "compliance_retry_used": False, "compliance_retry_reason": "",
        "answer_model_calls": 0,
        "estimated_cost": 0.0, "status": STATUS_FAILED, "error_message": "",
        "answer_rel": str(answer_path.relative_to(project_root)),
        "obsidian_rel": "", "diagnostics_rel": "",
        "retrieval_log_rel": str(retrieval_path.relative_to(project_root)),
        # --- Phase 6 -------------------------------------------------------
        "retrieval_mode": args.retrieval_mode, "retrieval_mode_effective": mode,
        "prefer_recent": bool(args.prefer_recent),
        "filter_summary": describe_filters(args), "filter_diagnostics": {},
        "candidates_considered": 0,
        "avg_vector_score": None, "avg_keyword_score": None, "avg_hybrid_score": None,
        # --- Phase 8.1 -----------------------------------------------------
        "recommendation_refused": bool(recommendation["is_request"]),
        "recommendation_reason": recommendation["reason"],
        "unsupported_subject": False,
        "subject_terms": [], "subject_terms_present": [], "subject_terms_missing": [],
        # Phase 12.1: statements attributing a disclosure to the company when no
        # company-direct chunk is in the pack. Empty until the answer exists.
        "attribution_violations": [],
        "confidence_ceilings": [],
        "answer_generated": False,
    }

    try:
        client = make_openai_client(settings)

        # --- retrieve ------------------------------------------------------
        vector = embed_question(client, embedding_model, question)
        query_tokens = estimate_tokens(question)

        # --- Phase 6: metadata filters, applied BEFORE ranking -------------
        allowed_ids, filter_diagnostics = filter_chunks(conn, ticker, args)
        context["filter_diagnostics"] = filter_diagnostics
        if allowed_ids is not None:
            LOGGER.info("Filters (%s) leave %d of %d indexed chunk(s) eligible.",
                        filter_diagnostics["filters"], len(allowed_ids),
                        filter_diagnostics["indexed_chunks"])

        vector_hits = keyword_hits = []
        if allowed_ids is not None and not allowed_ids:
            LOGGER.warning("No chunk passes the filters. Retrieval is skipped entirely.")
        else:
            if mode in ("vector", "hybrid"):
                vector_hits = retrieve_vector(collection, vector, args.top_k, allowed_ids)
                LOGGER.info("Vector retrieval returned %d candidate(s).", len(vector_hits))
            if mode in ("keyword", "hybrid") and kw_index is not None:
                keyword_hits = retrieve_keyword(kw, kw_index, conn, question, args.top_k,
                                                allowed_ids, ticker)
                LOGGER.info("Keyword retrieval returned %d candidate(s).", len(keyword_hits))

        # --- merge the two candidate sets ----------------------------------
        merged_ids = []
        by_vector = {h["chunk_id"]: h for h in vector_hits}
        by_keyword = {h["chunk_id"]: h for h in keyword_hits}
        for hit in vector_hits:
            merged_ids.append(hit["chunk_id"])
        for hit in keyword_hits:
            if hit["chunk_id"] not in by_vector:
                merged_ids.append(hit["chunk_id"])

        db_rows = fetch_chunk_rows(conn, merged_ids, ticker)
        candidates = []
        for chunk_id in merged_ids:
            vhit = by_vector.get(chunk_id, {})
            khit = by_keyword.get(chunk_id, {})
            db_row = db_rows.get(chunk_id, {})
            meta = dict(vhit.get("metadata") or {})
            merged = {**meta, **{k: v for k, v in db_row.items() if v not in (None, "")}}
            text = db_row.get("chunk_text") or vhit.get("text") or ""
            cand = {
                "chunk_id": chunk_id,
                "document_id": merged.get("document_id") or "(unknown)",
                "file_name": merged.get("file_name"),
                "relative_path": merged.get("relative_path"),
                "folder_date_guess": merged.get("folder_date_guess"),
                "source_type_guess": merged.get("source_type_guess"),
                "document_type_guess": merged.get("document_type_guess"),
                "page_start_guess": merged.get("page_start_guess"),
                "page_end_guess": merged.get("page_end_guess"),
                "chunk_char_count": merged.get("chunk_char_count"),
                "chunk_text": text,
                "distance": vhit.get("distance"),
                "similarity": vhit.get("vector_score"),
                "vector_rank": vhit.get("vector_rank"),
                "vector_score": vhit.get("vector_score"),
                "keyword_rank": khit.get("keyword_rank"),
                "keyword_score": khit.get("keyword_score"),
                "matched_terms": khit.get("matched_terms") or [],
                "phrase_hit": bool(khit.get("phrase_hit")),
                "selection_score": None, "citation": None,
            }
            cand["kind"] = chunk_kind(cand)
            candidates.append(cand)

        # --- score: vector mode keeps Phase 5B, the rest use the hybrid mix -
        vector_norm = normalise_scores({c["chunk_id"]: c["vector_score"] for c in candidates
                                        if c["vector_score"] is not None})
        keyword_norm = normalise_scores({c["chunk_id"]: c["keyword_score"] for c in candidates
                                         if c["keyword_score"] is not None})
        recency_norm = recency_component(candidates) if args.prefer_recent else {}

        for cand in candidates:
            cand["vector_score_norm"] = vector_norm.get(cand["chunk_id"], 0.0) \
                if cand["vector_score"] is not None else None
            cand["keyword_score_norm"] = keyword_norm.get(cand["chunk_id"], 0.0) \
                if cand["keyword_score"] is not None else None
            cand["source_priority_bonus"] = priority_component(cand["kind"], preference)
            cand["recency_bonus"] = recency_norm.get(cand["chunk_id"], 0.0) \
                if args.prefer_recent else None

            if mode == "vector":
                # Phase 5B, unchanged: raw similarity plus the small tilt.
                cand["base_score"] = ((cand["similarity"] or 0.0)
                                      + priority_adjustment(cand["kind"], preference))
                cand["evidence_strength"] = cand["similarity"] or 0.0
            else:
                score = (W_VECTOR * (cand["vector_score_norm"] or 0.0)
                         + W_KEYWORD * (cand["keyword_score_norm"] or 0.0)
                         + W_PRIORITY * cand["source_priority_bonus"])
                if args.prefer_recent:
                    score += W_RECENCY * (cand["recency_bonus"] or 0.0)
                cand["base_score"] = score
                cand["evidence_strength"] = (cand["similarity"] if cand["similarity"] is not None
                                             else (cand["keyword_score_norm"] or 0.0))

        candidates.sort(key=lambda c: (-c["base_score"], c["chunk_id"]))
        for rank, cand in enumerate(candidates, start=1):
            cand["rank"] = rank
        context["all_candidates"] = candidates
        context["candidates_considered"] = len(candidates)

        strength_source = ("vector similarity" if mode != "keyword"
                           else "normalised keyword score")

        if args.debug_retrieval and candidates:
            safe_print()
            safe_print("-" * 78)
            safe_print(f"RETRIEVAL DEBUG - {mode} mode, {len(candidates)} candidate(s)")
            safe_print(f"{'rank':>4} {'vec':>6} {'kw':>6} {'prio':>5} {'score':>7}  kind / chunk")
            for cand in candidates:
                def show(value):
                    return f"{value:6.3f}" if isinstance(value, (int, float)) else "     -"
                safe_print(f"{cand['rank']:>4} {show(cand['vector_score_norm'])} "
                      f"{show(cand['keyword_score_norm'])} "
                      f"{cand['source_priority_bonus']:5.2f} {cand['base_score']:7.4f}  "
                      f"{cand['kind']:<14} {cand['chunk_id']}")
            safe_print("-" * 78)

        usable = [c for c in candidates if (c["chunk_text"] or "").strip()]
        if len(usable) < len(candidates):
            LOGGER.warning("%d retrieved chunk(s) had no text and were dropped.",
                           len(candidates) - len(usable))

        # --- no evidence => no answer model call ---------------------------
        if not usable and context["recommendation_refused"]:
            # A request for advice is refused whether or not retrieval found
            # anything. The boundary does not depend on the archive.
            context["status"] = STATUS_COMPLETED
            context["answer"] = build_refusal_answer([], recommendation, ticker)
            context["confidence_rule"] = "Low"
            context["confidence_rule_reason"] = (
                "the question asked for an investment decision and was refused")
            context["audit_flags"].append(
                f"recommendation_request_refused: {recommendation['reason']}")
            context["estimated_cost"] = (query_tokens / 1_000_000.0
                                         * settings["embed_cost_per_1m"])
            LOGGER.warning("Refused a request for an investment decision; retrieval had also "
                           "returned nothing usable.")
        elif not usable:
            filtered_out = allowed_ids is not None and not allowed_ids
            context["status"] = STATUS_NO_EVIDENCE
            context["error_message"] = ("filters removed every candidate" if filtered_out
                                        else "retrieval returned no usable chunks")
            context["confidence_rule"] = "Low"
            context["confidence_rule_reason"] = ("filters left nothing to retrieve"
                                                 if filtered_out
                                                 else "retrieval returned nothing")
            context["final_confidence"] = "Low"
            explanation = (explain_empty_filters(filter_diagnostics) if filtered_out else
                           f"The index holds {index_info['chunks']} chunks from "
                           f"{index_info['documents']} {ticker} documents. Either the question's "
                           "wording is far from anything in those documents, or the material "
                           "simply is not in the indexed set.")
            context["answer"] = (
                "## 1. Direct Answer\n\n"
                "I did not find enough retrieved evidence to answer this question. "
                + ("The metadata filters removed every candidate before retrieval ran"
                   if filtered_out else
                   f"Retrieval against the {ticker} index returned no usable text")
                + ", so **no answer model was called** - answering from anything other than "
                "retrieved evidence is not something this tool does.\n\n"
                "## 2. Evidence Used\n\nNone.\n\n"
                "## 3. Source Citations\n\nNone.\n\n"
                "## 4. Evidence Limitations\n\n" + explanation + "\n\n"
                "## 5. Needs Human Review\n\n"
                + ("Relax or correct the filters above, then ask again.\n\n" if filtered_out else
                   "Rephrase the question closer to the language a document would use, or "
                   "confirm the relevant documents are actually in the Phase 5A index.\n\n")
                + "## 6. Confidence\n\nLow - there is no evidence to be confident about.")
            context["estimated_cost"] = (query_tokens / 1_000_000.0
                                         * settings["embed_cost_per_1m"])
            LOGGER.warning("No usable chunks%s. No answer model was called.",
                           " after filtering" if filtered_out else "")
        else:
            # --- select ----------------------------------------------------
            selected, per_document, per_source = select_evidence(
                usable, args.max_chunks, args.max_chunks_per_document, preference)
            context["selected"] = selected
            context["per_source"] = per_source
            LOGGER.info("Selected %d evidence chunk(s) from %d document(s) across %d source "
                        "type(s).", len(selected), len(per_document), len(per_source))
            for cand in selected:
                LOGGER.info("  [%d] vec %s  kw %s  score %.4f  %-14s %s | %s",
                            cand["citation"],
                            f"{cand['vector_score']:.3f}" if cand["vector_score"] is not None
                            else "  -  ",
                            f"{cand['keyword_score']:.3f}" if cand["keyword_score"] is not None
                            else "  -  ",
                            cand["selection_score"], cand["kind"],
                            cand.get("file_name") or "(unknown)", cand["chunk_id"])

            def average(key):
                values = [c[key] for c in selected if isinstance(c.get(key), (int, float))]
                return round(sum(values) / len(values), 6) if values else None

            context["avg_vector_score"] = average("vector_score")
            context["avg_keyword_score"] = average("keyword_score")
            context["avg_hybrid_score"] = average("base_score")

            grade, reason = rule_confidence(selected, preference, intent["intent"],
                                            strength_source, question, ticker)
            context["confidence_rule"] = grade
            context["confidence_rule_reason"] = reason
            context["confidence_ceilings"] = [
                (ceiling, flag) for ceiling, flag, _why
                in confidence_ceilings(selected, intent["intent"], question, ticker)]
            for ceiling, flag in context["confidence_ceilings"]:
                context["audit_flags"].append(f"confidence_ceiling_{flag}: {ceiling}")

            # --- Phase 8.1, Part B: is the subject actually in this pack? ---
            support = check_subject_support(question, selected, ticker)
            context["subject_terms"] = support["terms"]
            context["subject_terms_present"] = support["present"]
            context["subject_terms_missing"] = support["missing"]
            context["unsupported_subject"] = support["unsupported"]
            if support["unsupported"]:
                LOGGER.warning("Subject check: %s. No answer model will be called.",
                               support["reason"])

            # --- answer ----------------------------------------------------
            # Three ways out, and only the last one costs money or involves a
            # model. Both deterministic answers are audited exactly like a
            # generated one - a hand-written answer is not exempt from the
            # checks, and if one of them ever trips a flag, that is worth
            # knowing.
            # Phase 14.3.5. One budget for the whole question, created before
            # the first branch so that every path shares the same ceiling.
            calls = AnswerCallBudget()
            user_input = ""
            if context["recommendation_refused"]:
                answer_text = build_refusal_answer(selected, recommendation, ticker)
                context["answer"] = answer_text
                context["status"] = STATUS_COMPLETED
                context["audit_flags"].append(
                    f"recommendation_request_refused: {recommendation['reason']}")
                LOGGER.warning("Answered with the standing refusal. No answer model was called.")
            elif support["unsupported"]:
                answer_text = build_unsupported_subject_answer(
                    support, selected, ticker, intent["intent"])
                context["answer"] = answer_text
                context["status"] = STATUS_COMPLETED
                context["audit_flags"].append(
                    "unsupported_subject: none of "
                    + ", ".join(f"'{t}'" for t in support["missing"])
                    + " appears in the selected evidence")
            else:
                user_input = build_user_input(question, selected, ticker, intent, preference)
                LOGGER.info("Sending %d evidence chunk(s) (~%d characters) to %s.",
                            len(selected), len(user_input), settings["answer_model"])
                result = call_answer_model(client, settings, user_input, calls)
                answer_text = result["text"]
                context["answer"] = answer_text
                context["prompt_tokens"] = result["input_tokens"]
                context["output_tokens"] = result["output_tokens"]
                context["exact_usage"] = result["exact_usage"]
                # Phase 14.3.3. Previously returned and then discarded here.
                context["generation_status"] = result["status"]
                context["generation_incomplete_reason"] = result.get("incomplete_reason") or ""
                context["answer_max_output_tokens"] = int(
                    result.get("max_output_tokens") or settings["max_output_tokens"])
                context["answer_retry_used"] = bool(result.get("retry_used"))
                if result["status"] != "completed":
                    LOGGER.warning("The answer model reported status '%s'%s at a budget of %d "
                                   "output tokens.", result["status"],
                                   f" ({result.get('incomplete_reason')})"
                                   if result.get("incomplete_reason") else "",
                                   context["answer_max_output_tokens"])
                context["status"] = STATUS_COMPLETED
                context["answer_generated"] = True

            audit = audit_answer(answer_text, selected)

            # --- Phase 14.3.5: at most ONE compliance repair ---------------
            # Only for a generated answer: the deterministic refusal and the
            # unsupported-subject answer are written by this script and cite
            # nothing by design, so there is nothing for a repair pass to do.
            if context["answer_generated"]:
                repair = compliance_pass(client, settings, user_input, answer_text,
                                         audit, calls)
                context["compliance_retry_reason"] = repair["reason"]
                if repair["used"]:
                    context["compliance_retry_used"] = True
                    # Replaced, never stitched.
                    answer_text = repair["text"]
                    context["answer"] = answer_text
                    context["prompt_tokens"] += repair["input_tokens"]
                    context["output_tokens"] += repair["output_tokens"]
                    context["exact_usage"] = (context["exact_usage"]
                                              and repair["exact_usage"])
                    context["answer_max_output_tokens"] = max(
                        context["answer_max_output_tokens"],
                        int(repair.get("max_output_tokens") or 0))
                    if repair.get("retry_used"):
                        context["answer_retry_used"] = True
                    if repair["status"] != "completed":
                        context["generation_status"] = repair["status"]
                        context["generation_incomplete_reason"] = (
                            repair.get("incomplete_reason") or "")
                    audit = audit_answer(answer_text, selected)
                    remaining = compliance_failures(audit, settings.get("answer_style"))
                    context["compliance_retry_reason"] += (
                        "; after the repair: " + ("; ".join(remaining) if remaining
                                                  else "clean"))
                    if remaining:
                        context["audit_flags"].append(
                            "compliance_retry_did_not_fix_it: " + "; ".join(remaining))
                        LOGGER.warning("The compliance repair did not fix it (%s). The repaired "
                                       "answer is kept as written, flagged, and capped at Low.",
                                       "; ".join(remaining))
                    else:
                        LOGGER.info("The compliance repair fixed it. %d of %d generative call(s) "
                                    "used for this question.", calls.used, calls.limit)
                context["answer_model_calls"] = calls.used

            standing_flags = list(context["audit_flags"])
            context.update({
                "warnings": audit["warnings"],
                "flagged_language": audit["flagged_language"],
                "citations_used": audit["citations_used"],
                "unknown_citations": audit["unknown_citations"],
                "unused_citations": audit["unused_citations"],
                "citation_audit": audit["citation_audit"],
                # The flags raised before the answer existed (ceilings, refusal,
                # unsupported subject) are kept - audit_answer only knows about
                # the text.
                "audit_flags": standing_flags + audit["audit_flags"],
                "directness_hits": audit["directness_hits"],
                "directness_sections": audit["directness_sections"],
                "confidence_stated": audit["confidence_stated"],
                # Phase 14.3.3. True when the text itself is unfinished. The
                # API's own 'incomplete' is a separate signal and is handled in
                # post_generation_ceilings, so an answer the guard cannot see a
                # cut in is still capped when the API says it stopped early.
                "generation_incomplete": audit["generation_incomplete"],
                "generation_incomplete_reasons": audit["generation_incomplete_reasons"],
            })
            for warning in audit["warnings"]:
                LOGGER.warning("Answer check: %s", warning)

            # --- Phase 12.1: attribution, checked against the pack ----------
            context["attribution_violations"] = attribution_violations(
                parse_sections(answer_text).get("Direct Answer", ""), selected)
            if context["attribution_violations"]:
                context["audit_flags"].append(
                    f"{len(context['attribution_violations'])} statement(s) attribute a "
                    "disclosure to the company, management or a filing, but no selected chunk is "
                    "company-direct or SEC material - first: "
                    f"\"{context['attribution_violations'][0][:120]}\"")
                LOGGER.warning("Attribution check: %d statement(s) claim a company disclosure "
                               "with no company-direct evidence in the pack.",
                               len(context["attribution_violations"]))

            # --- Phase 8.1: a stopped run is Low, and says why --------------
            if context["recommendation_refused"] or context["unsupported_subject"]:
                why = ("the question asked for an investment decision and was refused"
                       if context["recommendation_refused"] else
                       "the subject of the question does not appear in the selected evidence")
                context["confidence_rule"] = "Low"
                context["confidence_rule_reason"] = (
                    f"{context['confidence_rule_reason']}; forced to Low because {why}")

            # --- Phase 6, Part E: the answer's own verdict on its evidence --
            # Phase 6.1: only the Direct Answer and Confidence sections count.
            if audit["directness_hits"]:
                where = " / ".join(audit["directness_sections"]) or "the answer"
                context["answer_directness_flag"] = True
                context["directness_cap_applied"] = True
                reason = f"{DIRECTNESS_CAP_REASON} (stated in {where})"
                if context["confidence_rule"] != "Low":
                    context["confidence_rule_reason"] = (
                        f"{context['confidence_rule_reason']}; capped from "
                        f"{context['confidence_rule']} to Low. {reason}")
                else:
                    context["confidence_rule_reason"] = (
                        f"{context['confidence_rule_reason']}; {reason}")
                context["confidence_rule"] = "Low"
                LOGGER.warning("Directness cap applied (%s). %s", where, DIRECTNESS_CAP_REASON)

            # A refused or stopped run still paid to embed the question. It did
            # not pay for an answer, and the cost recorded says so.
            context["estimated_cost"] = (
                query_tokens / 1_000_000.0 * settings["embed_cost_per_1m"]
                + context["prompt_tokens"] / 1_000_000.0 * settings["answer_input_cost_per_1m"]
                + context["output_tokens"] / 1_000_000.0 * settings["answer_output_cost_per_1m"]
            )

    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - one run must never corrupt the record
        context["status"] = STATUS_FAILED
        context["error_message"] = f"{type(exc).__name__}: {exc}"
        context["answer"] = (
            "## 1. Direct Answer\n\n"
            f"This run failed before an answer could be produced: {context['error_message']}\n\n"
            "No answer was generated. Nothing here should be read as a finding.\n\n"
            "## 2. Evidence Used\n\nSee the retrieval log for what had been retrieved.\n\n"
            "## 3. Source Citations\n\nNone.\n\n"
            "## 4. Evidence Limitations\n\nThe run did not complete.\n\n"
            "## 5. Needs Human Review\n\nCheck logs\\phase5b_ask_brain.log and re-run.\n\n"
            "## 6. Confidence\n\nLow - the run failed.")
        LOGGER.error("Ask Brain run failed: %s", context["error_message"])

    # --- one final confidence, the lower of the two, capped if not direct ---
    stated = context["confidence_stated"]
    if stated in GRADE_ORDER:
        context["final_confidence"] = (stated if GRADE_ORDER[stated]
                                       <= GRADE_ORDER[context["confidence_rule"]]
                                       else context["confidence_rule"])
    else:
        context["final_confidence"] = context["confidence_rule"]
    if context["directness_cap_applied"]:
        context["final_confidence"] = "Low"

    # --- Phase 12.1: the ceilings applied LAST, after generation -------------
    #
    # Two kinds arrive here. The question-shape ceilings are already folded into
    # confidence_rule, and are re-applied to the final grade so that a model's
    # own stated confidence can never carry a grade past them. The
    # post-generation ceilings can only be known now, because they are defects
    # in the answer text: a factual claim with no citation, or a disclosure
    # attributed to a company whose material is not in the pack.
    #
    # Capping only ever lowers, so every earlier rule - including the Phase 5B.1
    # company-direct ceilings - survives this step untouched.
    intent_name = (context.get("intent") or {}).get("intent", "unclassified")
    final_ceilings = (question_shape_ceilings(question, intent_name)
                      + post_generation_ceilings(context))
    for ceiling, flag, reason in final_ceilings:
        capped = cap_grade(context["final_confidence"], ceiling)
        if capped != context["final_confidence"]:
            context["audit_flags"].append(
                f"confidence capped from {context['final_confidence']} to {capped} "
                f"({flag}): {reason}")
            LOGGER.info("Confidence capped %s -> %s (%s).",
                        context["final_confidence"], capped, flag)
            context["final_confidence"] = capped
    # The recorded list is (ceiling, flag) pairs - keep it that way rather than
    # mixing in bare flag names, which is what a mixed sort was tripping over.
    existing = list(context.get("confidence_ceilings") or [])
    for ceiling, flag, _reason in final_ceilings:
        if (ceiling, flag) not in existing:
            existing.append((ceiling, flag))
    context["confidence_ceilings"] = existing

    # --- write everything, whatever happened -------------------------------
    if args.save and context["status"] != STATUS_FAILED:
        assert_inside_project(obsidian_dir, project_root)
        obsidian_dir.mkdir(parents=True, exist_ok=True)
        context["obsidian_rel"] = str(obsidian_path.relative_to(project_root))

    # The diagnostics file name is derived from the run's timestamp, so it can
    # be recorded on the run row before the file itself is written.
    if context["all_candidates"]:
        context["diagnostics_rel"] = str(
            (Path("06_RAG_Index") / "retrieval_logs" / f"{ticker}_{slug}_diagnostics.csv"))

    write_answer_markdown(answer_path, project_root, context)
    if context["obsidian_rel"]:
        write_obsidian_note(obsidian_path, project_root, context)
    elif not args.save:
        LOGGER.info("--no-save: the Obsidian note was not written.")
    write_retrieval_log(retrieval_path, project_root, context)

    run_id = record_run(conn, context)
    LOGGER.info("Recorded ask_brain_runs.run_id = %d (status %s)", run_id, context["status"])
    if context["all_candidates"]:
        write_diagnostics(project_root, ticker, context, run_id)
    write_reports(conn, project_root, ticker)
    conn.close()

    # The run is already on disk by this point. Displaying it is the last and
    # least important thing that happens, and it is not allowed to undo any of
    # it: a console that cannot render a character is a display problem, not a
    # failed piece of research.
    try:
        print_answer(context)
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning(
            "The answer could not be printed to the console (%s: %s). The run itself completed "
            "- the answer, the retrieval log and the database row were all written. Read the "
            "answer at %s.", type(exc).__name__, exc, context["answer_rel"])
    safe_print()
    safe_print(f"Answer        : {answer_path}")
    if context["obsidian_rel"]:
        safe_print(f"Obsidian note : {obsidian_path}")
    safe_print(f"Retrieval log : {retrieval_path}")
    safe_print(f"Run id        : {run_id}   status: {context['status']}")
    safe_print()

    if PRINT_FAILURES:
        LOGGER.warning("%d console line(s) could not be displayed by this terminal. Nothing on "
                       "disk is affected - the answer file has every character exactly as the "
                       "model wrote it.", PRINT_FAILURES)

    return 0 if context["status"] != STATUS_FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
