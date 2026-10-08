#!/usr/bin/env python3
# =============================================================================
# Cutler Research Brain - Phase 14
# answer_quality.py - what "a better answer" means, written down
#
# "The answers read better" is not a claim anyone can check. This module turns
# it into numbers a person could count by hand: how long the answer is, how
# much of it repeats itself, whether it opens with the finding or with where the
# finding came from, whether a third party is named, whether there is a
# conclusion as well as a transcription.
#
# NOTHING HERE RUNS A MODEL, and nothing here is a judge. Every measure is a
# regex or a count over text that already exists on disk. A model grading its
# own output is not evidence, which is why Phase 14's evaluation does not use
# one.
#
# NOTHING HERE REWRITES AN ANSWER either. This module only reads. Phase 14's
# rule is that answer quality comes from the prompt, not from post-processing
# that edits financial meaning after the fact.
#
# Two consumers, one definition:
#   tests/test_phase14.py              asserts the properties hold
#   scripts/run_phase14_answer_review.py  reports before/after on real answers
#
#   AI drafts. Humans verify. Cutler decides.
# =============================================================================

from __future__ import annotations

import re

MODULE_VERSION = "phase14.2-1.0"

# The six sections Ask Brain writes. Imported rather than redefined where
# possible; duplicated here only so this module can read an answer FILE without
# importing the whole pipeline.
ANSWER_SECTIONS = (
    "Direct Answer", "Evidence Used", "Source Citations",
    "Evidence Limitations", "Needs Human Review", "Confidence",
)

RE_SECTION = re.compile(
    r"^\s*#{1,6}\s*\d?\.?\s*(" + "|".join(ANSWER_SECTIONS) + r")\s*$", re.I | re.M)
RE_CITATION = re.compile(r"\[(\d{1,3})\]")
# Phase 14.1. The same splitter ask_brain uses, including its two guards: do not
# split after an INITIAL ("J.P. Morgan") or after an ELLIPSIS. The first live run
# reported "Morgan credit note identifies ..." as an uncited claim when the real
# sentence was fully cited - a defect in the reader, not the answer. Two
# splitters that disagree are two different answers to "what is a sentence", so
# this one is kept character-for-character in step with ask_brain's.
RE_SENTENCE = re.compile(r"(?<![A-Z]\.)(?<!\.\.)(?<=[.!?])\s+(?=[A-Z\"'\[])")
RE_BULLET = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(.*)$", re.M)
RE_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9'%$.,/-]*")

# --- the openings Phase 14 bans ---------------------------------------------
#
# Each one spends the reader's first line on provenance. The citation already
# says where the claim came from; the first line should say what it is.
#
# "The retrieved evidence is related, but it does not directly answer the
# question" is NOT here and must never be: it is the exact sentence Ask Brain is
# required to produce when the evidence is off-target, and the directness cap
# keys off it. An answer whose subject IS the limitation of the evidence is
# allowed - in fact required - to open that way.
BANNED_OPENINGS = (
    r"^based on the (provided|available|retrieved|supplied|indexed)",
    r"^based on (the )?research",
    r"^the research (indicates|suggests|shows)",
    r"^according to the (documents|provided|retrieved|sources|evidence)",
    r"^here is (a |the )?(summary|overview|breakdown)",
    r"^the (provided|retrieved|supplied) (sources|documents|evidence|text|chunks)\s+"
    r"(indicate|suggest|show|say|state)",
    r"^the retrieved source text says",
    r"^the following (is a )?summar",
    r"^this (answer|response) (summari|provid)",
    r"^(in summary|to summari[sz]e|in conclusion|overall,)",
    r"^i (will|can) (summari|provid|outlin)",
)
RE_BANNED_OPENING = tuple(re.compile(p, re.I) for p in BANNED_OPENINGS)

# A sentence that states what the evidence adds up to. Kept in step with
# ask_brain.RE_SYNTHESIS_LEAD - the prompt names these markers, the audit
# exempts them, and this module counts them.
RE_SYNTHESIS_LEAD = re.compile(
    r"^\s*(?:\*\*)?(?:taken together|read together|on balance|the net effect|"
    r"what this means|why (?:it|this) matters|the through-line|put together|"
    r"seen together|together,? (?:these|the evidence|they))\b", re.I)

# A sentence that says what MOVED rather than what is.
RE_WHAT_CHANGED = re.compile(
    r"\b(compared with|compared to|versus the prior|against the prior|since the prior|"
    r"the change is|changed from|shifted from|moved from|rose from|fell from|"
    r"became more|became less|has moved|up from|down from|in contrast to the prior|"
    r"no comparison is possible|only one period)\b", re.I)

RE_REFUSAL = re.compile(
    r"\b(cannot provide|can't provide|do not provide|does not provide|no recommendation|"
    r"did not find|does not support|is related, but it does not directly answer)\b", re.I)

# Quick Answer's target band, from the Phase 14 brief. A band, not a cap: an
# answer is not wrong for being 95 words, it is wrong for being 550.
QUICK_WORD_BAND = (100, 220)
DEEP_WORD_BAND = (150, 420)

# How much of a section may restate itself before it is repeating. Two claims
# sharing this fraction of their content words are the same claim twice.
REPETITION_OVERLAP = 0.6


def answer_body(text: str) -> str:
    """The part a reader sees: from the first section heading onward.

    An answer FILE starts with a metadata header - model, confidence, flags -
    that the UI strips before rendering. Counting it as answer length would
    make every answer look longer than it reads.
    """
    match = RE_SECTION.search(text or "")
    return text[match.start():] if match else (text or "")


def strip_appendices(text: str) -> str:
    """Drop the source table and retrieval trace appended to an answer file.

    They are a record, not prose. A 60-row retrieval trace is not something a
    reader reads, and letting it into a word count makes the measure useless.
    """
    cut = re.split(r"^##\s*(?:Source table|Retrieval trace)\s*$", text or "",
                   maxsplit=1, flags=re.M)[0]
    return re.sub(r"^\s*\|.*$", "", cut, flags=re.M)


def readable_answer(text: str) -> str:
    """What a person actually reads, from a raw answer file."""
    return strip_appendices(answer_body(text))


def parse_sections(text: str) -> dict:
    found = {}
    matches = list(RE_SECTION.finditer(text or ""))
    for index, match in enumerate(matches):
        name = match.group(1).strip().title()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        found[name] = text[match.end():end].strip()
    return found


def words(text: str) -> int:
    return len(RE_WORD.findall(text or ""))


def _asserts_something(text: str) -> bool:
    """Is this a claim, or a refusal, a lead-in, or a marked conclusion?

    Ask Brain settled this question in Phase 8.1 and extended it in Phase 14,
    and its answer is the one the audit and the Phase 12 evaluator both use. A
    second definition here would drift from those two and start reporting
    "uncited claims" that neither of them considers claims - which is how a
    measurement ends up arguing with the system it measures.

    When ask_brain cannot be imported (this module is also useful on a machine
    with only answer files on it), everything substantial counts as a claim.
    That direction is safe: it over-reports rather than under-reports.
    """
    try:
        import ask_brain  # noqa: PLC0415 - sibling script, optional here
    except ImportError:
        return True
    return not ask_brain.is_non_factual_sentence(text)


def claim_units(section: str) -> list:
    """Every bullet and every substantial sentence that ASSERTS something."""
    bullets = [b.strip() for b in RE_BULLET.findall(section or "") if b.strip()]
    rest = RE_BULLET.sub("", section or "")
    sentences = [s.strip() for s in RE_SENTENCE.split(rest) if len(s.strip()) >= 40]
    return [unit for unit in bullets + sentences if _asserts_something(unit)]


def opening_line(section: str) -> str:
    """The first thing a reader reads - the takeaway, or the first bullet."""
    body = (section or "").strip()
    if not body:
        return ""
    first_bullet = RE_BULLET.search(body)
    line = first_bullet.group(1) if first_bullet and first_bullet.start() < 4 else body
    parts = RE_SENTENCE.split(line.strip(), maxsplit=1)
    return (parts[0] if parts else "").strip()


def opens_with_boilerplate(section: str) -> bool:
    """Does the answer spend its first line on provenance instead of findings?"""
    opening = opening_line(section)
    return any(pattern.search(opening) for pattern in RE_BANNED_OPENING)


_STOPWORDS = frozenset("""
the a an of for and to in on that is was were are it its this with as by at from or
be been has have had not no than then there their they which who what when
""".split())


def content_words(text: str) -> set:
    bare = RE_CITATION.sub("", text or "").lower()
    bare = re.sub(r"[^a-z0-9 ]+", " ", bare)
    return {w for w in bare.split() if w not in _STOPWORDS and len(w) > 2}


def repetition(section: str) -> float:
    """How much of a section restates itself, 0.0 (none) to 1.0 (all).

    Every pair of claims is compared by content-word overlap. "Azure growth
    remains strong" and "Overall, Azure growth is still strong" share nearly all
    of their content words: that is one point written twice, which is the defect
    the Phase 14 brief names in section 7.
    """
    units = [content_words(u) for u in claim_units(section)]
    units = [u for u in units if len(u) >= 4]
    if len(units) < 2:
        return 0.0
    pairs = overlapping = 0
    for i in range(len(units)):
        for j in range(i + 1, len(units)):
            smaller = min(len(units[i]), len(units[j]))
            if not smaller:
                continue
            pairs += 1
            if len(units[i] & units[j]) / smaller >= REPETITION_OVERLAP:
                overlapping += 1
    return round(overlapping / pairs, 3) if pairs else 0.0


# Phase 14.1. The marker list is kept in step with ask_brain.RE_SYNTHESIS_LEAD
# on purpose: the prompt names these markers, the audit reads them, and this
# counts them. Three copies that drift are three different answers to "is this
# a conclusion".
#
# A WARNING ABOUT WHAT THIS COUNTS. `has_synthesis` is true only when there is a
# MARKED CLOSING LINE. It is not the same as "the answer reaches a conclusion":
# in the first live run, seven of the nine answers without a marked line had
# their conclusion in the takeaway sentence, which is exactly where the prompt
# asks for it, and adding a closing line as well would have been repetition.
# Read a low `with_synthesis` as "look at the takeaways", never as "these
# answers only transcribe".
def synthesis_lines(section: str) -> list:
    """Sentences that say what the evidence adds up to, rather than what it says."""
    units = claim_units(section)
    units += [s.strip() for s in (section or "").splitlines() if s.strip()]
    found = []
    for unit in units:
        text = unit.strip().lstrip("-*+ ").strip()
        if RE_SYNTHESIS_LEAD.match(text) and text not in found:
            found.append(text)
    return found


def vendor_mentions(section: str, vendors) -> list:
    """Which of the pack's own publishers the answer names."""
    found = []
    for vendor in vendors or []:
        name = str(vendor or "").strip()
        if len(name) >= 2 and re.search(r"\b" + re.escape(name) + r"\b", section or "", re.I):
            if name not in found:
                found.append(name)
    return found


def measure(raw_answer: str, vendors=None) -> dict:
    """Every Phase 14 property of one answer, as plain numbers.

    `vendors` is the pack's recorded publisher list, when the caller has it.
    Without it, publisher attribution is reported as unknown rather than as
    absent - saying "no publisher named" because nobody told us the publishers
    would be a measurement that invents its own bad news.
    """
    body = readable_answer(raw_answer or "")
    sections = parse_sections(body)
    direct = sections.get("Direct Answer", "")
    claims = claim_units(direct)
    cited = [c for c in claims if RE_CITATION.search(c)]
    synthesis = synthesis_lines(direct)

    apparatus = sum(words(sections.get(name, ""))
                    for name in ANSWER_SECTIONS if name != "Direct Answer")

    return {
        "words_total": words(body),
        "words_direct": words(direct),
        "words_apparatus": apparatus,
        "sections_present": sorted(sections),
        "sections_missing": [n for n in ANSWER_SECTIONS if n not in sections],
        "claims": len(claims),
        "claims_cited": len(cited),
        "claims_uncited": len(claims) - len(cited),
        "citations_in_direct": sorted({int(n) for n in RE_CITATION.findall(direct)}),
        "citations_total": sorted({int(n) for n in RE_CITATION.findall(body)}),
        "opening": opening_line(direct)[:200],
        "opens_with_boilerplate": opens_with_boilerplate(direct),
        "opening_cited": bool(RE_CITATION.search(opening_line(direct))),
        "repetition": repetition(direct),
        "synthesis_lines": synthesis,
        "has_synthesis": bool(synthesis),
        # Phase 14.1. A conclusion a reader cannot trace back to documents is
        # the thing this system exists not to produce, so the split between
        # cited and uncited conclusions is the number that actually matters.
        "synthesis_cited": sum(1 for s in synthesis if RE_CITATION.search(s)),
        "synthesis_uncited": sum(1 for s in synthesis if not RE_CITATION.search(s)),
        "has_what_changed": bool(RE_WHAT_CHANGED.search(direct)),
        "is_refusal": bool(RE_REFUSAL.search(direct)),
        "vendors_named": (vendor_mentions(direct, vendors) if vendors is not None else None),
        "confidence": (re.search(r"\b(High|Medium|Low)\b", sections.get("Confidence", ""))
                       or [None, ""])[1] if sections.get("Confidence") else "",
    }


def in_band(count: int, band=QUICK_WORD_BAND) -> bool:
    return band[0] <= count <= band[1]


def summarise(rows: list) -> dict:
    """The corpus-level numbers a before/after comparison is made of."""
    if not rows:
        return {}
    total = len(rows)

    def mean(key):
        return round(sum(r[key] for r in rows) / total, 2)

    substantive = [r for r in rows if not r["is_refusal"]]
    return {
        "answers": total,
        "mean_words_total": mean("words_total"),
        "mean_words_direct": mean("words_direct"),
        "mean_words_apparatus": mean("words_apparatus"),
        "mean_claims": mean("claims"),
        "mean_repetition": mean("repetition"),
        "boilerplate_openings": sum(1 for r in rows if r["opens_with_boilerplate"]),
        "with_synthesis": sum(1 for r in rows if r["has_synthesis"]),
        "synthesis_cited": sum(r.get("synthesis_cited", 0) for r in rows),
        "synthesis_uncited": sum(r.get("synthesis_uncited", 0) for r in rows),
        "with_what_changed": sum(1 for r in rows if r["has_what_changed"]),
        "uncited_claims_total": sum(r["claims_uncited"] for r in rows),
        "substantive_answers": len(substantive),
        "substantive_in_quick_band": sum(1 for r in substantive
                                         if in_band(r["words_direct"])),
        "named_a_publisher": sum(1 for r in rows if r.get("vendors_named")),
    }
