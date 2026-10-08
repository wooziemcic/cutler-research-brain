#!/usr/bin/env python3
"""Phase 14 - answer quality, checked without a model.

WHAT THIS SUITE IS FOR
----------------------
Phase 14 changed how answers are written. That is the hardest kind of change to
test honestly, because the obvious test - ask a model whether the answer is
good - is the one test that proves nothing. A model grading a model is not
evidence; it is two opinions.

So this suite does three things instead, none of which calls an API:

  1. It tests the RUBRIC against fixtures. A measure of answer quality is only
     worth having if it can tell a good answer from a bad one, so the fixtures
     below are deliberately written well and deliberately written badly, and the
     rubric has to separate them. A rubric nobody has tested is a number nobody
     should trust.

  2. It tests the PROMPT. Every rule the brief asked for is either in the
     instruction hierarchy or it is not, and that is a string check. Every rule
     an earlier phase put there and a deterministic check still reads - the
     exact directness sentence, the six section names, the per-sentence citation
     rule - has to still be there, because rewording one silently disables the
     check that reads it.

  3. It tests that NOTHING ELSE MOVED. Retrieval weights, index writes, ticker
     isolation, the refusal paths, the confidence ceilings, the Phase 13.1
     follow-up machinery and the citation-to-document controls are all asserted
     unchanged.

The real before/after on live answers is a separate thing, and it costs money:
  py -3.11 scripts\\run_phase14_answer_review.py --confirm-cost

  AI drafts. Humans verify. Cutler decides.

Usage:
  py -3.11 tests\\test_phase14.py
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "09_Web_App_Future" / "phase7a_local_ui"
SCRIPTS = ROOT / "scripts"

sys.path.insert(0, str(UI))
sys.path.insert(0, str(SCRIPTS))
os.environ.setdefault("CUTLER_PROJECT_ROOT", str(ROOT))

FAILS = []


def check(label, condition, detail=""):
    print(("  PASS  " if condition else "  FAIL  ") + label + (f"   {detail}" if detail else ""))
    if not condition:
        FAILS.append(label)


def code_only(text: str) -> str:
    """Source with comments and docstrings removed.

    A check that greps a whole file matches the comment explaining a behaviour
    and reports it as present. Claims about what code DOES are made against the
    code - the lesson of Phase 11.2, learned again in 12.1.
    """
    stripped = re.sub(r'"""(?:.|\n)*?"""', '""', text)
    stripped = re.sub(r"/\*(?:.|\n)*?\*/", "", stripped)
    return "\n".join(line for line in stripped.splitlines()
                     if not line.lstrip().startswith(("#", "//")))


import answer_quality as aq                                        # noqa: E402
import ask_brain as ab                                             # noqa: E402

ASK_BRAIN_SRC = (SCRIPTS / "ask_brain.py").read_text(encoding="utf-8")
ASK_BRAIN_CODE = code_only(ASK_BRAIN_SRC)
APP_SRC = (UI / "app.py").read_text(encoding="utf-8")
APP_CODE = code_only(APP_SRC)
JS = (UI / "static" / "app.js").read_text(encoding="utf-8")
JS_CODE = code_only(JS)
EVAL_SRC = (SCRIPTS / "run_multi_ticker_evaluation.py").read_text(encoding="utf-8")
REVIEW_SRC = (SCRIPTS / "run_phase14_answer_review.py").read_text(encoding="utf-8")
PROMPT = ab.SYSTEM_INSTRUCTION
import run_phase14_answer_review as rv                             # noqa: E402

# The prompt is pinned by SIZE as well as by content, so a later phase cannot
# quietly re-grow it. 2,217 words was the 14.3.1 prompt; 14.3.2 cut it to 1,822;
# Phase 14.3.4 spent 36 of those words removing the takeaway exemption and
# stating the rule it was hiding. The quick hierarchy has not moved since 14.3.2.
PROMPT_WORDS_1431 = 2217
PROMPT_WORDS = 1858
QUICK_WORDS_1432 = 205

# One evidence chunk, shaped like a real one, for the deterministic builders.
SELECTED_FIXTURE = [{
    "citation": 1, "file_name": "MSFT 10-Q Q2 FY26.pdf", "page_start_guess": 34,
    "page_end_guess": 36, "document_id": "d1", "chunk_id": "c1",
    "folder_date_guess": "2026-01", "source_type_guess": "company",
    "document_type_guess": "10-Q", "relative_path": "MSFT/10Q.pdf", "kind": "company",
}]


# ===========================================================================
# The fixture set.
#
# Six pairs. Each pair is the same question answered twice - once the way the
# Phase 12 answers read, once the way Phase 14 asks for - so every measure is
# exercised against a case it must accept AND a case it must reject. Written by
# hand rather than sampled from live output, because a fixture that came out of
# the system cannot be used to test the system.
# ===========================================================================

def answer(direct: str, used="- [1] the filing.", confidence="Medium - partial source set.",
           limits="- The pack covers one quarter only.",
           review="- Confirm the figures in the full filing.") -> str:
    return (f"## 1. Direct Answer\n\n{direct}\n\n"
            f"## 2. Evidence Used\n\n{used}\n\n"
            f"## 3. Source Citations\n\n- [1] MSFT 10-Q Q2 FY26.pdf, pp. 34-36.\n\n"
            f"## 4. Evidence Limitations\n\n{limits}\n\n"
            f"## 5. Needs Human Review\n\n{review}\n\n"
            f"## 6. Confidence\n\n{confidence}\n")


FIXTURES = {
    # --- the opening ------------------------------------------------------
    "opens_with_provenance": answer(
        "Based on the provided sources, Microsoft reported Microsoft Cloud revenue of "
        "$51.5 billion, an increase of 26% [1]."),
    "opens_with_the_finding": answer(
        "Microsoft Cloud revenue reached $51.5 billion in the December quarter, up 26% [1]."),
    "opens_with_retrieved_source_text": answer(
        "The retrieved source text says Microsoft Cloud revenue grew 26% in the quarter [1]."),
    # The one opening that must NEVER be treated as boilerplate: it is the exact
    # sentence Ask Brain is required to produce, and the directness cap reads it.
    "opens_with_the_mandated_directness_sentence": answer(
        "The retrieved evidence is related, but it does not directly answer the question."),

    # --- repetition -------------------------------------------------------
    "repeats_itself": answer(
        "Azure growth remains strong in the quarter [1].\n\n"
        "- Azure continues to grow strongly across the period [1].\n"
        "- Overall, Azure growth is still strong in the quarter [1].\n"
        "- Azure growth remained strong through the quarter [1]."),
    "says_each_thing_once": answer(
        "Azure grew 39% in the December quarter, and capacity is the binding constraint [1].\n\n"
        "- Microsoft Cloud revenue reached $51.5 billion, up 26% [1].\n"
        "- Capital spending rose as data-centre capacity was added [2].\n"
        "- Several analyst notes name depreciation as the main near-term margin risk [3][4]."),

    # --- synthesis --------------------------------------------------------
    "has_marked_synthesis": answer(
        "Azure grew 39% in the December quarter [1].\n\n"
        "- Capital spending rose as capacity was added [2].\n\n"
        "Taken together, the question is shifting from whether demand exists to whether it "
        "can be monetised faster than the cost of supporting it."),
    "has_synthesis_smuggling_a_figure": answer(
        "Azure grew 39% in the December quarter [1].\n\n"
        "Taken together, margins will compress by roughly 300 basis points next year."),
    "has_no_synthesis": answer(
        "Azure grew 39% in the December quarter [1].\n\n"
        "- Capital spending rose as capacity was added [2]."),

    # --- attribution ------------------------------------------------------
    "names_the_publisher": answer(
        "Bernstein's note expects higher AI-related depreciation to pressure margins near "
        "term [3]."),
    "generic_analyst_attribution": answer(
        "Analysts expect higher depreciation to pressure margins near term [3]."),
    "presents_analyst_view_as_company_fact": answer(
        "The company reported that depreciation will pressure margins near term [3]."),

    # --- length -----------------------------------------------------------
    "quick_length": answer(" ".join(["Azure grew in the quarter [1]."] * 2)
                           + "\n\n" + "\n".join(f"- Point {n} about capacity and cost "
                                                f"across the period, with detail [{n}]."
                                                for n in range(1, 5))
                           + "\n\n" + " ".join(["word"] * 120) + " [1]."),
    "bloated_length": answer("\n".join(
        f"- A point about segment {n}, its revenue, its growth rate, its margin, its "
        f"capacity constraints and its outlook, stated at length [{n}]." for n in range(1, 9))
        + "\n\n" + " ".join(["filler"] * 420) + " [1]."),

    # --- refusal paths ----------------------------------------------------
    "refusal_recommendation": answer(
        "I cannot provide a buy/sell/hold recommendation or tell Cutler what action to take.",
        confidence="Low - no answer to the question as asked was produced."),
    "refusal_unsupported": answer(
        "I did not find any retrieved evidence that reports Microsoft's fiscal 1998 results.",
        confidence="Low - the evidence is not about the question."),
}


# ===========================================================================
# Phase 14.3.2 fixtures.
#
# The question behind the first pair is the live MSFT analyst_concern question,
# whose pack held eight chunks. The selective answer uses two of them; the
# inventory answer uses all eight and runs 100 words longer while answering the
# same question no better. Every one of these is written by hand.
# ===========================================================================

FIXTURES_1432 = {
    "selects_two_of_eight": answer(
        "Microsoft's AI build-out is still a growth driver, but the depreciation it creates "
        "is now the larger near-term margin question [1][3].\n\n"
        "- Microsoft Cloud revenue reached $51.5 billion in the December quarter, up 26%, "
        "with Azure the largest contributor to that growth [1].\n"
        "- Several analyst notes name the depreciation arising from this year's capacity "
        "additions as the main margin risk into next year, and none of them quantifies it "
        "beyond a range [3][4].\n"
        "- Management has not given a depreciation figure for the coming year, so the size "
        "of the drag is an estimate in every source that offers one [1][3].",
        used="- [1] the filing.\n- [3] a sell-side note.\n- [4] a second note."),

    "inventories_all_eight": answer(
        "Based on the retrieved research, there are several analyst concerns about "
        "Microsoft [1][2][3][4][5][6][7][8].\n\n"
        "- UBS research estimates capacity additions continue into next year [2].\n"
        "- Bernstein's note expects higher AI-related depreciation to pressure margins near "
        "term [3].\n"
        "- A third note raises the question of monetisation timing against capital "
        "intensity [4].\n"
        "- A fourth note points to competition in enterprise AI workloads [5].\n"
        "- A fifth note discusses the cost of supporting inference at scale [6].\n"
        "- A sixth note considers the mix shift within Microsoft Cloud [7].\n"
        "- A seventh note raises the pace of commercial bookings, and treats the "
        "comparison against the prior year as the figure to watch rather than the "
        "absolute level [8].\n"
        "- Microsoft Cloud revenue reached $51.5 billion in the December quarter, an "
        "increase of 26% against the same quarter a year earlier, with Azure the largest "
        "single contributor to that growth [1].\n\n"
        "Taken together, the research identifies several concerns: (1) depreciation, "
        "(2) monetisation timing, (3) competition, and (4) the cost of supporting "
        "inference, each of which bears on the margin outlook in a different way.\n\n"
        "Why it matters: the balance between AI-driven growth and the cost of supporting "
        "that growth is the central question across all eight retrieved sources, and the "
        "analyst community has not settled it, which is why the range of estimates in the "
        "pack is as wide as it is and why no single note should be read as the consensus "
        "view on the margin outlook for the coming year."),

    # A risk question with plenty of evidence, answered under the ceiling: three
    # distinct risks, each cited, no summary, no closer.
    "dense_risk_capped": answer(
        "The retrieved research names three credit risks, of which lease maturity "
        "concentration is the one every source raises [1][2].\n\n"
        "- A ratings note flags the concentration of lease maturities in the next two "
        "years, and treats re-leasing spreads as the main variable [1].\n"
        "- A second note puts the same exposure in the context of a higher cost of debt on "
        "refinancing, without estimating the effect on coverage [2].\n"
        "- Tenant credit quality in the smaller-format centres is named as a separate risk, "
        "and only one source quantifies it, at a low single-digit share of base rent [3].\n"
        "- The same source treats the exposure as manageable at that level, while noting "
        "that it has not been tested through a weaker retail environment [3].",
        used="- [1] a ratings note.\n- [2] a second note.\n- [3] a third note."),

    # Three sources agreeing is one point with three citations.
    "combined_point_multi_cite": answer(
        "Depreciation from this year's capacity additions is the margin risk the research "
        "agrees on [2][3][5].\n\n"
        "- Three analyst notes name depreciation as the main near-term margin pressure, and "
        "none of them quantifies it beyond a range [2][3][5].\n"
        "- Microsoft Cloud revenue reached $51.5 billion in the quarter, up 26% [1].",
        used="- [1] the filing.\n- [2] a note.\n- [3] a note.\n- [5] a note."),

    "one_sentence_per_source": answer(
        "Depreciation from this year's capacity additions is the margin risk the research "
        "agrees on [2][3][5].\n\n"
        "- UBS research names depreciation as the main near-term margin pressure [2].\n"
        "- Bernstein's note names depreciation as the main near-term margin pressure [3].\n"
        "- A third note also names depreciation as the main near-term margin pressure [5].\n"
        "- Microsoft Cloud revenue reached $51.5 billion in the quarter, up 26% [1].",
        used="- [1] the filing.\n- [2] a note.\n- [3] a note.\n- [5] a note."),

    # The AMZN company_update shape: one point that stops being a point.
    "point_becomes_an_inventory": answer(
        "Amazon's margin picture improved across the segments the research covers [1].\n\n"
        "- Operating margin expanded in North America to the level reported in the "
        "quarter, while the international segment moved from a loss to a small profit, AWS "
        "operating margin was reported separately again at a higher level than the prior "
        "year, advertising revenue grew faster than total revenue, shipping cost per unit "
        "was described as lower on a like-for-like basis, and fulfilment network density "
        "was named as the reason for part of that improvement "
        "[1][2][3][4][5].\n"
        "- Capital spending rose again in the quarter as fulfilment and data-centre "
        "capacity was added, and the filing describes the increase as planned [2].\n"
        "- A ratings note treats the margin improvement as durable rather than cyclical, "
        "and names cost per unit as the reason, while a second note is more cautious and "
        "treats part of the gain as a function of the demand environment rather than of "
        "the cost base [3][4].\n"
        "- Advertising revenue is reported separately and grew faster than total revenue "
        "again in the quarter, which the filing describes as a mix benefit to margin "
        "rather than a volume effect [1].\n\n"
        "Taken together, the research describes margin improvement across North America, "
        "international and AWS, driven by cost per unit, network density and advertising "
        "mix, each of which the sources treat separately.\n\n"
        "Why it matters: whether the improvement persists depends on which of those "
        "drivers the sources treat as structural rather than cyclical.",
        used="- [1] the filing.\n- [2] a note.\n- [3] a ratings note."),

    # The shape group C prescribes for a question the pack cannot answer.
    "unsupported_minimal": (
        "## 1. Direct Answer\n\n"
        "The retrieved evidence does not contain enough information to answer this "
        "reliably. The pack holds no AT&T disclosures, filings or analyst coverage dated "
        "1998 or earlier, and nothing reporting that fiscal year.\n\n"
        "## 2. Evidence Used\n\nNone - no supplied chunk is evidence for this "
        "question.\n\n"
        "## 3. Source Citations\n\nNone.\n\n"
        "## 4. Evidence Limitations\n\nThe pack covers 2024-2026 only.\n\n"
        "## 5. Needs Human Review\n\nSearch the archive for 1998 filings.\n\n"
        "## 6. Confidence\n\nLow - the evidence is not about the question.\n"),

    # The live T answer's shape: 93 words of Direct Answer and 267 more of
    # apparatus restating the same absence, citing six documents to prove it.
    "unsupported_padded": answer(
        "Because no 1998-period company disclosures, SEC filings, or analyst notes are "
        "present in the retrieved evidence, the archive material supplied here cannot "
        "report or summarize AT&T's fiscal 1998 results [2][3][4][5][6].\n\n"
        "- The retrieved documents cover 2024 to 2026 [2][3].\n"
        "- None of them is a 1998-period filing, annual report or analyst note "
        "[4][5][6].",
        used="- [2] a 2025 note.\n- [3] a 2025 note.\n- [4] a 2026 note.\n"
             "- [5] a 2026 note.\n- [6] a 2024 filing.",
        limits="- The pack contains no 1998 material.\n"
               "- No annual report from that period is present.\n"
               "- No analyst coverage from that period is present.",
        review="- Confirm whether 1998 filings exist in the archive at all.\n"
               "- Confirm whether the period is in scope for the index.\n"
               "- Confirm the ticker mapping for that period.",
        confidence="Low - the evidence is not about the question."),
}


# ===========================================================================
print("=== 1. THE RUBRIC CAN TELL A GOOD ANSWER FROM A BAD ONE ===")

M = {name: aq.measure(text) for name, text in FIXTURES.items()}

check("a provenance opening is caught",
      M["opens_with_provenance"]["opens_with_boilerplate"])
check("'The retrieved source text says' is caught too",
      M["opens_with_retrieved_source_text"]["opens_with_boilerplate"])
check("an opening that leads with the finding is not caught",
      not M["opens_with_the_finding"]["opens_with_boilerplate"],
      M["opens_with_the_finding"]["opening"][:70])
check("the mandated directness sentence is NEVER treated as boilerplate",
      not M["opens_with_the_mandated_directness_sentence"]["opens_with_boilerplate"],
      "this is the sentence the directness cap reads; banning it would break the cap")
check("neither refusal opening is treated as boilerplate",
      not M["refusal_recommendation"]["opens_with_boilerplate"]
      and not M["refusal_unsupported"]["opens_with_boilerplate"])

check("an answer that says one thing four ways scores as repetitive",
      M["repeats_itself"]["repetition"] >= 0.5, str(M["repeats_itself"]["repetition"]))
check("an answer whose points differ does not",
      M["says_each_thing_once"]["repetition"] <= 0.2,
      str(M["says_each_thing_once"]["repetition"]))
check("the repetitive answer scores worse than the varied one",
      M["repeats_itself"]["repetition"] > M["says_each_thing_once"]["repetition"])

check("a marked, figure-free conclusion counts as synthesis",
      M["has_marked_synthesis"]["has_synthesis"],
      str(M["has_marked_synthesis"]["synthesis_lines"])[:90])
check("an answer with no conclusion is reported as having none",
      not M["has_no_synthesis"]["has_synthesis"])
check("a 'synthesis' line carrying a figure is NOT exempt from citing",
      not ab.is_synthesis_sentence(
          "Taken together, margins will compress by roughly 300 basis points next year."),
      "a number is a fact and belongs to a source")
check("so that answer is counted as an uncited claim",
      M["has_synthesis_smuggling_a_figure"]["claims_uncited"] >= 1,
      str(M["has_synthesis_smuggling_a_figure"]["claims_uncited"]))
check("the honest synthesis is not counted as an uncited claim",
      M["has_marked_synthesis"]["claims_uncited"] == 0,
      str(M["has_marked_synthesis"]["claims_uncited"]))

check("a named publisher is detected when the pack names it",
      aq.measure(FIXTURES["names_the_publisher"], ["Bernstein", "UBS"])["vendors_named"]
      == ["Bernstein"])
check("a publisher the pack does not have is not invented",
      aq.measure(FIXTURES["generic_analyst_attribution"],
                 ["Bernstein"])["vendors_named"] == [])
check("with no vendor list, publisher attribution is unknown rather than absent",
      aq.measure(FIXTURES["names_the_publisher"])["vendors_named"] is None,
      "reporting 'no publisher' because nobody supplied the list would be invented bad news")

check("a long answer is outside the quick band",
      not aq.in_band(M["bloated_length"]["words_direct"]),
      str(M["bloated_length"]["words_direct"]))
check("a 100-220 word answer is inside it",
      aq.in_band(M["quick_length"]["words_direct"]),
      str(M["quick_length"]["words_direct"]))

check("the rubric runs no model", "openai" not in aq.__file__.lower()
      and not re.search(r"import openai|OpenAI\(|responses\.create",
                        (SCRIPTS / "answer_quality.py").read_text(encoding="utf-8")))
check("the rubric rewrites nothing",
      not re.search(r"\.write_text\(|open\([^)]*[\"']w", code_only(
          (SCRIPTS / "answer_quality.py").read_text(encoding="utf-8"))))

# ===========================================================================
print("\n=== 2. THE PROMPT IS A HIERARCHY, AND GROUNDING IS ON TOP ===")
for label, marker in (("A. grounding", "=== A. GROUNDING ==="),
                      ("B. attribution", "=== B. ATTRIBUTION ==="),
                      ("C. answer structure", "=== C. HOW TO WRITE THE ANSWER ==="),
                      ("D. confidence and refusal", "=== D. CONFIDENCE AND REFUSAL ==="),
                      ("E. citation format", "=== E. FORMAT ===")):
    check(f"group {label} exists", marker in PROMPT)
check("the groups are in order",
      [PROMPT.index(m) for m in ("=== A.", "=== B.", "=== C.", "=== D.", "=== E.")]
      == sorted(PROMPT.index(m) for m in ("=== A.", "=== B.", "=== C.", "=== D.", "=== E.")))
check("grounding is declared to outrank style, in the prompt itself",
      "A is absolute" in PROMPT and "Style never overrides factual discipline" in PROMPT)
check("the length rule is its own group, appended at call time",
      "=== F. LENGTH ===" in ab.answer_instructions({"answer_style": "quick"})
      and "=== F. LENGTH ===" not in PROMPT)
check("the prompt is not enormous", len(PROMPT.split()) < 2300,
      f"{len(PROMPT.split())} words")

print("\n  -- rules this phase introduced --")
for label, needle in (
        ("lead with the answer", "Lead with the answer"),
        ("banned opening: based on the provided sources", "Based on the provided \\\nsources"),
        ("banned opening: the research indicates", "The research indicates"),
        ("banned opening: according to the documents", "According to the documents"),
        ("banned opening: here is a summary", "Here is a summary"),
        ("open with the finding, not with where it came from",
         "Open with the finding itself"),
        ("the pack is a menu, not a checklist",
         "EVIDENCE SUPPLIED IS A MENU, NOT A CHECKLIST"),
        ("a large pack is not a reason to write more",
         "A large pack is never a reason to write a longer answer"),
        ("say each thing once", "Say each thing once"),
        ("an implication is stated once, inside the sentence carrying the fact",
         "say it once, in the sentence that"),
        ("do not invent implications", "Do not invent implications"),
        ("a change question leads with what moved and against what",
         "lead with what moved and against"),
        ("name the publisher", "Name the publisher"),
        ("synthesis must be marked as synthesis", "it must be written so a reader can tell"),
        ("synthesis carries no figure", "Keep every figure out of a conclusion"),
        ("the gap may be named in ONE further sentence, not inventoried",
         "ONE further sentence naming the gap"),
        ("sections 2-6 are the record, not a second answer",
         "Sections 2 to 6 are the record, not a second answer"),
):
    check(f"the prompt says: {label}", needle.replace("\\\n", "") in PROMPT.replace("\\\n", "")
          or needle in PROMPT)

print("\n  -- rules earlier phases put there, that checks still read --")
for label, needle in (
        ("Phase 8.1: a citation in every factual sentence", "EVERY sentence that states"),
        ("Phase 8.1: no uncited opening summary", "uncited summary sentence"),
        ("Phase 8.1: a citation on every bullet", "every bullet must carry a citation"),
        ("Phase 8.1: never restate a rating word", "do not restate the rating word"),
        ("Phase 6.1: the exact directness sentence",
         "The retrieved evidence is related, but it does not directly answer the question."),
        ("Phase 12.1: claims in 1, citations only in 2 and 3 is the defect",
         "the specific defect this rule exists to prevent"),
        ("Phase 12.1: no company attribution without a company source",
         "no sentence in your answer may attribute anything to the company"),
        ("the six section names, in order", "## 6. Confidence"),
        ("the confidence grades", "exactly one of High, Medium, or Low"),
        ("never say 'Cutler should'", '"Cutler should..."'),
        ("never say 'the company will'", '"The company will..."'),
):
    check(f"still there: {label}", needle in PROMPT)

check("the six sections the parser reads are all still named in the prompt",
      all(f"## {n}. {name}" in PROMPT
          for n, name in enumerate(ab.ANSWER_SECTIONS, start=1)),
      str([name for name in ab.ANSWER_SECTIONS if name not in PROMPT]))

# ===========================================================================
print("\n=== 3. QUICK AND DEEP ARE WRITTEN DIFFERENTLY, RETRIEVED IDENTICALLY ===")
quick = ab.ANSWER_STYLES["quick"]
deep = ab.ANSWER_STYLES["deep"]
check("there are exactly two styles", set(ab.ANSWER_STYLES) == {"quick", "deep"})
check("quick names the 100-180 target", "100-180 words" in quick)
check("and 220 as a maximum rather than a target",
      "220 words is a maximum, not a target" in quick)
check("quick says the record must be shorter than the note",
      "shorter than section 1" in quick)
# The accuracy guard used to sit in both the style block and group C. It now
# sits once, in group C, where it applies to both styles.
check("brevity is explicitly outranked by grounding, in ONE place",
      PROMPT.count("outrank brevity") == 1
      and "Three things still outrank brevity" in PROMPT
      and "outrank brevity" not in quick + deep)
check("deep is told it is not quick-with-more-words",
      "more adjectives" in deep and "not for more" in deep)
check("deep asks for disagreement to be named",
      "disagree" in deep and "cite both" in deep)
check("deep asks company figures to be separated from analyst estimates",
      "separate company-reported figures from analyst" in deep)
check("neither style mentions a retrieval weight or a ranking rule",
      not re.search(r"\bweight|\brank|top.?k|\bembed|\bvector|bm25|\bscore|\bsimilarit|"
                    r"\bretriev|\bcandidate", quick + deep, re.I),
      "a writing instruction that mentions ranking is a retrieval change in disguise")
check("'chunk' appears in a style block only as a unit of citation, never of ranking",
      all("chunk" not in part.lower() or "per cited chunk" in part.lower()
          for part in (quick, deep)),
      "section 2 is one line per cited chunk; that is formatting, not retrieval")
check("an unknown style falls back to quick rather than to no guidance",
      "100-180 words" in ab.answer_instructions({"answer_style": "nonsense"}))
check("the default style is quick", ab.DEFAULT_ANSWER_STYLE == "quick")
check("the style is recorded on the run, so a run can be reproduced",
      '"answer_style": context.get("answer_style")' in ASK_BRAIN_CODE
      or '"answer_style": context[' in ASK_BRAIN_CODE)
check("--answer-style is a real flag with exactly those choices",
      "--answer-style" in ASK_BRAIN_CODE and "ANSWER_STYLE_CHOICES" in ASK_BRAIN_CODE)

import app as ui                                                   # noqa: E402

check("the UI's Deep Research selects the deep writing style",
      ui.DEPTH_PRESETS["deep"]["answer_style"] == "deep"
      and ui.DEPTH_PRESETS["quick"]["answer_style"] == "quick")
check("depth still changes only how much is read, not how it is ranked",
      {k: ui.DEPTH_PRESETS["quick"][k]
       for k in ("top_k", "max_chunks", "max_chunks_per_document")}
      == {"top_k": 30, "max_chunks": 8, "max_chunks_per_document": 2},
      str(ui.DEPTH_PRESETS["quick"]))
values = ui.validate(ui.AskRequest(question="What did the company report?", ticker="MSFT"))
check("a request naming no depth still writes the quick shape",
      values["answer_style"] == "quick", str(values["answer_style"]))
check("the style reaches Ask Brain on the command line",
      "--answer-style" in ui.build_command(values))

# ===========================================================================
print("\n=== 4. ATTRIBUTION SURVIVES BEING DONE PROPERLY ===")
pack = [{"kind": "analyst", "source_vendor_candidates": "Bernstein"},
        {"kind": "analyst", "source_vendor_candidates": "Green Street"}]
check("the pack's own publishers are what the checks know about",
      ab.pack_vendors(pack) == ["Bernstein", "Green Street"], str(ab.pack_vendors(pack)))
check("nothing is hard-coded: an empty pack names nobody", ab.pack_vendors([]) == [])
check("a generic analyst word still governs a sentence",
      ab.analyst_governor("Analysts expect margin pressure [3].", []))
check("a named publisher governs a sentence too",
      ab.analyst_governor("Bernstein's note says the company reported a rise [1].", pack),
      "the Phase 14 instruction is to name the publisher; the check has to recognise it")
check("an unattributed company claim is still a violation",
      ab.attribution_violations("The company reported a rise in revenue [1].", pack))
check("the same claim governed by a named publisher is not",
      not ab.attribution_violations(
          "Bernstein's note records that management said revenue rose [1].", pack))
check("reported speech through a generic note is still exempt (Phase 12.1)",
      not ab.attribution_violations(
          "One note states management said it would fund this with cash on hand [2].", pack))
check("a pack holding a company source disables the check entirely",
      ab.attribution_violations("The company reported a rise [1].",
                                [{"kind": "company_direct"}]) == [])
check("the Phase 12 evaluator uses the same definition, not a second one",
      "ab.analyst_governor" in EVAL_SRC and "attributes_to_a_third_party" in EVAL_SRC)

import run_multi_ticker_evaluation as mte                          # noqa: E402

check("the evaluator credits a named publisher as attribution",
      mte.attributes_to_a_third_party("Green Street sees cap rates widening [2].", pack))
check("the evaluator still credits a generic one",
      mte.attributes_to_a_third_party("Analysts see cap rates widening [2].", pack))
check("and still reports an unattributed opinion as unattributed",
      not mte.attributes_to_a_third_party("Cap rates may widen [2].", pack))

# ===========================================================================
print("\n=== 5. THE REFUSAL AND CONFIDENCE RULES ARE UNTOUCHED ===")
check("the recommendation refusal is still written by the script, not the model",
      "def build_refusal_answer(" in ASK_BRAIN_CODE
      and "not a thing" in ASK_BRAIN_SRC)
check("the refusal sentence is still a constant", "REFUSAL_SENTENCE" in ASK_BRAIN_CODE)
for pattern_name in ("buy/sell/hold call", "recommendation", "instruction to Cutler",
                     "trade instruction", "price target", "rating language"):
    check(f"the banned-language pattern for {pattern_name!r} survives",
          repr(pattern_name) in ASK_BRAIN_SRC or f'"{pattern_name}"' in ASK_BRAIN_SRC)
check("the unsupported-subject answer is still written by the script",
      "def build_unsupported_subject_answer(" in ASK_BRAIN_CODE)
check("an unsupported subject still caps at Low",
      any(c[0] == "Low" and c[1] == "subject_absent_from_evidence"
          for c in ab.post_generation_ceilings({"unsupported_subject": True})))
check("a refused recommendation still caps at Low",
      any(c[0] == "Low" and c[1] == "recommendation_refused"
          for c in ab.post_generation_ceilings({"recommendation_refused": True})))
check("uncited claims still cap at Low",
      any(c[0] == "Low" for c in ab.post_generation_ceilings(
          {"citation_audit": {"uncited_sentences": ["Margins fell sharply this year."]}})))
check("a marked synthesis line does not trigger that cap",
      ab.post_generation_ceilings({"citation_audit": {"uncited_sentences": []}}) == []
      and ab.is_non_factual_sentence(
          "Taken together, the debate has moved from demand to cost."))

for question, ceiling in (("What is the most recent development in Azure?", "Medium"),
                          ("What risks does the research identify?", "Medium"),
                          ("What concerns do analysts raise about margins?", "Medium")):
    ceilings = ab.question_shape_ceilings(question)
    check(f"the question-shape ceiling for {question[:38]!r} is still {ceiling}",
          any(c[0] == ceiling for c in ceilings), str([c[0] for c in ceilings]))
check("there is still no escape clause in the question-shape ceilings",
      "escape" not in ASK_BRAIN_CODE.split("def question_shape_ceilings(")[1][:1400].lower())
check("confidence is still deterministic, never the model grading itself",
      "def cap_grade(" in ASK_BRAIN_CODE)
check("the directness cap still reads the Direct Answer and Confidence only",
      'DIRECTNESS_SECTIONS = ("Direct Answer", "Confidence")' in ASK_BRAIN_SRC)

# ===========================================================================
print("\n=== 6. RETRIEVAL, INDEXING AND TICKER ISOLATION DID NOT MOVE ===")
check("the hybrid weights are unchanged",
      (ab.W_VECTOR, ab.W_KEYWORD, ab.W_PRIORITY, ab.W_RECENCY) == (0.65, 0.25, 0.10, 0.10),
      str((ab.W_VECTOR, ab.W_KEYWORD, ab.W_PRIORITY, ab.W_RECENCY)))
check("the selection penalties are unchanged",
      (ab.PRIORITY_BONUS, ab.PRIORITY_PENALTY, ab.DOC_REPEAT_PENALTY,
       ab.SOURCE_REPEAT_PENALTY) == (0.06, 0.03, 0.05, 0.03))
check("the weak-evidence floor is unchanged", ab.WEAK_EVIDENCE_SIMILARITY == 0.25)
check("the retrieval modes are unchanged",
      ab.RETRIEVAL_MODES == ("vector", "keyword", "hybrid")
      and ab.DEFAULT_RETRIEVAL_MODE == "hybrid")
check("the ticker list is unchanged, and no ticker was added",
      ab.KNOWN_TICKERS == ("MSFT", "KIM", "KR", "MRK", "T", "VZ", "AMZN", "SLB", "B", "ABBV"),
      str(ab.KNOWN_TICKERS))
check("chunk lookups are still ticker-scoped",
      "ticker" in ASK_BRAIN_CODE.split("def fetch_chunk_rows(")[1][:700])
check("this phase wrote nothing to rag_chunks",
      not re.search(r"(INSERT|UPDATE|DELETE)\s+(INTO\s+)?rag_chunks",
                    code_only(REVIEW_SRC) + code_only(
                        (SCRIPTS / "answer_quality.py").read_text(encoding="utf-8")), re.I))
check("the review script opens the database read-only",
      "mode=ro" in REVIEW_SRC and "sqlite3.connect(f\"file:" in REVIEW_SRC)
check("the review script never opens the V drive",
      "V:" not in REVIEW_SRC and "SOURCE_ARCHIVE" not in REVIEW_SRC)
check("the review script never imports chromadb or openai",
      "chromadb" not in REVIEW_SRC
      and not re.search(r"^\s*(import openai|from openai)", REVIEW_SRC, re.M))
check("the review script asks through ask_brain.py, as a person would",
      "ask_brain.py" in REVIEW_SRC and "shell=False" in REVIEW_SRC)
check("it refuses to spend anything without --confirm-cost",
      "--confirm-cost" in REVIEW_SRC and "Refusing to run without --confirm-cost" in REVIEW_SRC)
check("it reports whether the source set moved, rather than claiming credit",
      "same_sources" in REVIEW_SRC and "source sets identical" in REVIEW_SRC)
check("it reports regressions as well as wins",
      REVIEW_SRC.count("REGRESSED") >= 5, str(REVIEW_SRC.count("REGRESSED")))

# ===========================================================================
print("\n=== 7. PHASE 13.1 FOLLOW-UP BEHAVIOUR IS UNCHANGED ===")
for text, expected in (("Which documents support this?", "evidence_reference"),
                       ("Where did this come from?", "evidence_reference"),
                       ("Show me the source.", "evidence_reference"),
                       ("Which analyst said that?", "evidence_reference"),
                       ("Why?", "contextual"),
                       ("What changed from before?", "contextual"),
                       ("Tell me more about the second point.", "contextual"),
                       ("What are Kroger's grocery margins doing?", "new_subject")):
    got = ui.classify_follow_up(text, "MSFT")["kind"]
    check(f"{text[:34]!r} is still a {expected}", got == expected, got)
check("an evidence-reference reply still makes no model call",
      '"run_id": None' in APP_CODE
      and "evidence_answer_markdown" in APP_CODE
      and '"estimated_cost_usd": 0' in APP_CODE)
check("and still records no run",
      "no new run row" in APP_SRC or "derived_from_run_id" in APP_CODE)
check("a contextual follow-up still restricts to the previous answer's documents",
      "restrict_to_documents" in APP_CODE and "--restrict-to-documents" in APP_CODE)
check("the restriction is still a pre-ranking filter in Ask Brain",
      "restrict-to-documents" in ASK_BRAIN_SRC.split("def filter_chunks(")[1][:2400])
check("no chat memory or hidden session state was added",
      not re.search(r"\b(chat_history|conversation_history|session_memory|message_history)\b",
                    APP_CODE + ASK_BRAIN_CODE))
check("the follow-up context is still a run id and a flag, nothing more",
      "follow_up: bool = False" in APP_CODE and "previous_run_id: int = 0" in APP_CODE)

# ===========================================================================
print("\n=== 8. THE CITATION CONTROLS STILL REACH THE EVIDENCE ===")
check("every [n] in an answer is still a control", "function linkCitations(" in JS_CODE)
check("clicking one still opens the source and the document",
      "function openCitation(" in JS_CODE and "openEvidence(citation)" in JS_CODE)
check("it still restores the panel first",
      "setPanelHidden(false)" in JS_CODE.split("function openCitation(")[1][:700])
check("the evidence records are still built ticker-scoped",
      "def chunk_passages(" in APP_CODE and "WHERE ticker = ? AND chunk_id IN" in APP_CODE)
check("the rail still opens the original at the recorded page",
      'fragment = page > 0 ? "#page=" + page : ""' in JS_CODE)
check("the preview guard is unchanged",
      "def preview_status(" in APP_CODE and "source_path_outside_allowed_archive" in APP_CODE)

# ===========================================================================
print("\n=== 9. THE PHASE 12 SUITE AND ITS DATA ARE INTACT ===")
combined = ROOT / "07_Evaluation" / "golden_questions" / "multi_ticker_eval_questions.json"
check("the 60-question suite is still on disk", combined.exists())
if combined.exists():
    payload = json.loads(combined.read_text(encoding="utf-8"))
    check("it still holds 60 questions", len(payload["questions"]) == 60,
          str(len(payload["questions"])))
    check("across the same ten tickers",
          len({q["ticker"] for q in payload["questions"]}) == 10)
    check("and no question text was edited for this phase",
          str(payload.get("schema_version", "")).startswith("phase12"),
          str(payload.get("schema_version")))
check("the evaluator still runs Ask Brain at the default writing style",
      "--answer-style" not in EVAL_SRC,
      "the golden evaluation must compare like with like")
check("the ten evaluator checks are all still registered",
      all(f"def check_{name}(" in EVAL_SRC for name in
          ("run_completed", "refusal", "citation_coverage", "source_grounding",
           "ticker_correctness", "confidence_calibration", "evidence_kind",
           "unsupported_claims", "date_awareness", "source_diversity")))
check("the figure-grounding check still reads the Direct Answer",
      'RE_FIGURE.finditer(context["direct_answer"]' in EVAL_SRC,
      "this is what keeps a synthesis line from smuggling in a number")

# ===========================================================================
print("\n=== 10. NOTHING WAS REWRITTEN AFTER GENERATION ===")
check("the answer text is still written exactly as the model produced it",
      ASK_BRAIN_SRC.count("every character") >= 2
      and "exactly as the model wrote it" in ASK_BRAIN_SRC,
      "the audit reads a normalised COPY; the file keeps the original bytes")
check("the audit still flags and never rewrites",
      "Flags, never rewrites" in ASK_BRAIN_SRC)
check("no post-generation regex rewrites the answer body",
      not re.search(r"answer\s*=\s*re\.sub|context\[[\"']answer[\"']\]\s*=\s*re\.sub",
                    ASK_BRAIN_CODE))
check("the confidence ceilings change the grade, never the words",
      "Nothing here rewrites the answer and nothing invents a citation" in ASK_BRAIN_SRC)
check("the phase marker moved to 14",
      ab.PIPELINE_VERSION.startswith("phase14") and ui.PHASE.startswith("phase14"),
      f"{ab.PIPELINE_VERSION} / {ui.PHASE}")


# ===========================================================================
print("\n=== 11. PHASE 14.1: THE LIVE GROUNDING REGRESSION ===")
#
# The first live run produced 12 uncited claims against a baseline of 0. The
# three causes, and a fixture for each, so none of them can come back quietly.
#
#   A (9 of 12)  The prompt told the model to say WHAT IS MISSING when evidence
#                is insufficient. It did, in wordings the audit's refusal
#                vocabulary had never seen. Those sentences are refusals, and
#                Phase 8.1 settled that a refusal needs no citation.
#   B (2 of 12)  Marked synthesis making a substantive research conclusion,
#                written without citations. The fix is to CITE it, not to exempt
#                it - and the exemption was narrowed so the next one cannot slip
#                through by leaving the digits out.
#   C (1 of 12)  The sentence splitter cut "a J.P. Morgan credit note identifies
#                ..." in half and reported the half without the citation. The
#                answer was fine; the reader was broken.

print("\n  -- cause A: insufficiency language the new prompt asks for --")
# Written in the shapes the live run actually produced, not pasted from it:
# each one is the pattern, with a different company and period.
INSUFFICIENCY = (
    ("none-of-the-chunks",
     "None of the eight retrieved chunks contains a Kroger disclosure about "
     "pharmacy margins."),
    ("chunks-contain-no",
     "The supplied chunks contain no Verizon annual report, 10-K or press release for "
     "fiscal 2004, so revenue and EPS for that year are not available in the pack."),
    # Phase 14.2 deliberately TIGHTENED this one. The live sentence was
    # "the most recent development ... cannot be determined from the supplied
    # evidence", whose subject is a business fact, not the container - so under
    # the subject rule it is a claim and cites. The refusal has to be written
    # with the evidence as the subject, which is what the prompt now asks for,
    # and that version is in PACK_ABSENCE below.
    ("container-subject refusal",
     "The supplied evidence does not identify the most recent development in Merck's "
     "pipeline or the date it was reported."),
    ("cannot-answer",
     "Because none of the provided chunks cite SLB's fiscal 2002 results, the archive "
     "material supplied here cannot answer what SLB reported for that year."),
    ("would-need",
     "To answer the question I would need a company press release, Form 10-K or "
     "comparable SEC filing covering AbbVie's fiscal 2003 - those items are not present "
     "here."),
    ("none-of-which-appear",
     "To answer this we would need the fiscal 2007 Form 10-K or an archived 8-K, none of "
     "which appear in the retrieved chunks."),
    ("pack-lacks",
     "Because the pack lacks 2009 material, no numerical statement about that year can be "
     "supported by the provided evidence."),
    ("expanded-search-required",
     "A different or expanded document search of the archive is required to answer the "
     "question."),
)
for label, sentence in INSUFFICIENCY:
    check(f"a refusal of the {label!r} shape needs no citation",
          ab.is_non_factual_sentence(sentence), sentence[:80])

# The exemption is anchored on talking ABOUT THE PACK. These look similar and
# are claims about a company, so every one of them must still cite.
NOT_INSUFFICIENCY = (
    "None of the retail segments grew this quarter.",
    "The company reported no growth in the quarter.",
    "Kroger contains no pharmacy business after the divestiture.",
    "Management said the pipeline lacks a near-term catalyst.",
    "The filing shows the segment cannot answer for the shortfall.",
    "The segment cannot answer that charge.",
    "None of the three mills lacks capacity this year.",
)
for sentence in NOT_INSUFFICIENCY:
    check(f"a company claim is still a claim: {sentence[:46]!r}",
          not ab.is_non_factual_sentence(sentence))
# Phase 14.2: the same sentence with a business subject is a claim, and the
# container-subject rewrite of it is a refusal. One shape cites, the other does
# not, and the difference is the subject - which is the whole rule.
check("a refusal with a BUSINESS subject still cites",
      not ab.is_non_factual_sentence(
          "Therefore, the most recent development in Merck's pipeline cannot be determined "
          "from the supplied evidence."),
      "subject is 'the most recent development', not the pack")
check("the same refusal with the EVIDENCE as subject does not",
      ab.is_non_factual_sentence(
          "The supplied evidence does not identify the most recent development in Merck's "
          "pipeline."))

print("\n  -- cause B: a substantive conclusion must cite, marked or not --")
# Every one of these carries a synthesis marker. Every one makes a research
# claim. None may buy its way out of citing by being labelled a conclusion.
SUBSTANTIVE_SYNTHESIS = (
    ("directional",
     "Taken together, these disclosures show continued postpaid phone additions "
     "alongside material fiber subscriber growth."),
    ("consensus-across-sources",
     "Taken together, these sources show consistent concern across sell-side, credit and "
     "company filings about the scale of capital spending."),
    ("causal",
     "The net effect is that free cash flow falls because capital spending is rising."),
    ("comparison",
     "On balance, margins are under more pressure than they were a year ago."),
    ("company-performance",
     "Taken together, the sources indicate AWS is the primary driver of revenue and "
     "margin strength."),
    ("carries-a-figure",
     "Taken together, margins compress by roughly 300 basis points next year."),
)
for label, sentence in SUBSTANTIVE_SYNTHESIS:
    check(f"a {label} conclusion is denied the exemption",
          not ab.is_synthesis_sentence(sentence), sentence[:76])
    check(f"  and so it is counted as an uncited claim ({label})",
          aq.measure(answer(sentence))["claims_uncited"] >= 1)
    check(f"  but citing it clears the finding ({label})",
          aq.measure(answer(sentence.rstrip(".") + " [1][2]."))["claims_uncited"] == 0)

# What survives: a line that frames the evidence without asserting anything new.
CONNECTIVE = (
    "Taken together, these are two different questions rather than one.",
    "Why it matters: this is the trade-off Cutler has to weigh before acting.",
    "On balance, the sources address the same topic from different angles.",
)
for sentence in CONNECTIVE:
    check(f"connective language stays exempt: {sentence[:48]!r}",
          ab.is_synthesis_sentence(sentence))

check("a cited conclusion still registers as a conclusion",
      aq.measure(answer("Azure grew in the quarter [1].\n\nTaken together, the question is "
                        "moving from demand to cost [1][2]."))["synthesis_cited"] == 1)
check("and an uncited one is reported as uncited, not hidden",
      aq.measure(answer("Azure grew in the quarter [1].\n\nTaken together, the question is "
                        "moving from demand to cost."))["synthesis_uncited"] == 1)

print("\n  -- Phase 14.2: an absence statement is known by its SUBJECT --")
# The Phase 14.1 patterns were written from the nine sentences in front of me,
# so they matched those and not the shape they shared. Six more arrived saying
# the same thing differently. These fixtures are written as the SHAPE, with
# companies and periods that appear in none of the live answers.
PACK_ABSENCE = (
    ("pack-as-subject, 'do not contain'",
     "The supplied chunks are company filings reporting Kroger's 2025-2026 results and do not "
     "contain any disclosure, figures or commentary for fiscal 1998."),
    ("pack-as-subject, 'includes no'",
     "The research pack contains sell-side and industry notes but includes no third-party "
     "commentary that mentions Merck specifically."),
    ("'what is missing' with the container named",
     "What is missing is any analyst excerpt or line item in these documents that addresses "
     "Verizon by name."),
    ("pack-as-subject, long compound",
     "The pack contains analyst and company material about other issuers but does not include "
     "any analyst notes that identify risks to SLB specifically; what is missing are "
     "SLB-specific filings that discuss those areas."),
    ("negative existential subject",
     "No retrieved chunk mentions \"fiscal 1998\" or provides Kroger's results for that year."),
    ("pack-as-subject, semicolon continuation",
     "The provided chunks do not include any company disclosure that reports AbbVie's fiscal "
     "2011 results; the pack contains 2024-2026 material instead."),
    ("'the set' as subject",
     "The set contains recent-era earnings releases and analyst notes but no 1998 SEC filings."),
    ("archive excerpts as subject",
     "The archive excerpts supplied here do not cover that period at all."),
)
for label, sentence in PACK_ABSENCE:
    check(f"recognised by subject: {label}", ab.is_pack_absence_sentence(sentence),
          sentence[:84])
    check(f"  so it raises no uncited claim ({label})",
          aq.measure(answer(sentence))["claims_uncited"] == 0)

# The boundary the brief drew in bold: a company or business subject cites, even
# when what it says is itself about absence.
COMPANY_SUBJECT = (
    "Barnes Group's margins are not disclosed anywhere in the pack.",
    "Kimco's 1998 results are not present in these documents.",
    "Azure revenue did not grow in the quarter.",
    "Amazon's fiscal 2011 results were weaker than 2010.",
    "Management did not provide guidance for next year.",
    "AT&T has no exposure to that end market.",
    "None of the retail segments grew this quarter.",
    "Azure's growth does not include the acquisition.",
)
for sentence in COMPANY_SUBJECT:
    check(f"a company subject still cites: {sentence[:48]!r}",
          not ab.is_pack_absence_sentence(sentence)
          and not ab.is_non_factual_sentence(sentence))
check("a statement about the pack that asserts no absence is not a refusal",
      not ab.is_pack_absence_sentence(
          "The pack contains three SEC filings and five research notes."),
      "it is a claim about the pack, and it can cite like any other")

print("\n  -- cause C: the sentence splitter --")
SPLIT_TRAPS = (
    ("an initial",
     'A J.P. Morgan credit note identifies "Risks to the rating" and macro headwinds [4].'),
    ("an ellipsis inside a quotation",
     'The note identifies "Risks to the rating: ... M&A integration" as the main one [4].'),
    ("both at once",
     'A J.P. Morgan note identifies "Risks: ... integration" and macro headwinds [4].'),
)
for label, sentence in SPLIT_TRAPS:
    check(f"{label} does not split a cited sentence in half",
          len(ab.RE_SENTENCE.split(sentence)) == 1,
          str(ab.RE_SENTENCE.split(sentence)))
    check(f"  so it raises no uncited claim ({label})",
          aq.measure(answer(sentence))["claims_uncited"] == 0)
check("two real sentences still split into two",
      len(ab.RE_SENTENCE.split("Azure grew [1]. Capex rose [2].")) == 2)
check("the rubric and the audit use the same splitter, character for character",
      aq.RE_SENTENCE.pattern == ab.RE_SENTENCE.pattern,
      "two splitters that disagree are two answers to 'what is a sentence'")

print("\n  -- the prompt rules Phase 14.1 added --")
for label, needle in (
        ("cite your conclusions", "CITE YOUR CONCLUSIONS"),
        ("a conclusion carries the citations it rests on",
         "carries the citations of the claims it rests on"),
        ("only a connective conclusion may go uncited",
         "asserts nothing new about the world"),
        ("naming the classes that must cite", "a direction, a comparison, a cause, a consensus"),
        ("one point makes one research point", "Each point makes ONE research point"),
        ("several sources for one conclusion is one point",
         "that is one point citing [2][3][5], not three points"),
        ("quote only the words that carry the fact",
         "Quote only the words that carry the fact"),
        ("an unsupported answer stops at 30-60 words", "30-60 words, and stop"),
        ("and must not inventory what the pack does contain",
         "Do not inventory what the pack DOES contain"),
        ("a takeaway that already concludes needs no closing line",
         "write no closing line at all"),
):
    flat = PROMPT.replace("\\\n", "")
    check(f"the prompt says: {label}",
          needle.replace("\\\n", "") in flat or needle in PROMPT
          or needle in ab.ANSWER_STYLES["quick"])

check("group B3 and group E agree about when a conclusion cites",
      "the purely connective conclusion described in B3" in PROMPT
      and "The only conclusion that may carry no citation" in PROMPT,
      "a prompt that says both things lets the model choose")
# Phase 14.3.1 capped a point at roughly 35 words as well as capping the count.
# Phase 14.3.2 dropped the word cap: two numbers on one sentence is the conflict
# this phase exists to remove, and what makes a point long is enumerating inside
# it, which rules 3 and 4 address directly. The cap on COUNT stays.
check("the quick style caps how many points, in one place",
      "AT MOST TWO supporting points" in ab.ANSWER_STYLES["quick"]
      and "AT MOST" not in PROMPT)
check("and says what counts towards that cap, so a summary cannot escape it",
      "a paragraph that summarises the points is one of them"
      in ab.ANSWER_STYLES["quick"]
      and "five, not three" in ab.ANSWER_STYLES["quick"])
check("the quick style budgets sections 2 to 6",
      "shorter than section 1" in ab.ANSWER_STYLES["quick"])

print("\n  -- what the live run proved, kept as a guard --")
check("the baseline property that must never regress is zero uncited claims",
      aq.measure(FIXTURES["says_each_thing_once"])["claims_uncited"] == 0)
check("a refusal answer still carries no uncited claims",
      aq.measure(FIXTURES["refusal_recommendation"])["claims_uncited"] == 0
      and aq.measure(FIXTURES["refusal_unsupported"])["claims_uncited"] == 0)
check("with_synthesis counts a MARKED CLOSING LINE, and the rubric says so",
      "not the same as" in (SCRIPTS / "answer_quality.py").read_text(encoding="utf-8")
      and "conclusion in the takeaway" in
      (SCRIPTS / "answer_quality.py").read_text(encoding="utf-8"),
      "seven of nine answers without a marked line had their conclusion in the takeaway")



# ===========================================================================
print("\n=== 12. PHASE 14.3: ANSWER ECONOMY AND EVALUATOR HONESTY ===")
#
# The four over-band answers in the live run all had the same shape: they made
# their points, enumerated them AGAIN in a summary, then concluded. Two or three
# passes over the same material. The rule that was there - "do not restate the
# takeaway as a conclusion" - did not reach it, because those answers had no
# leading takeaway to restate.

print("\n  -- the enumerating summary --")
for label, needle in (
        ("a re-listing summary is not a conclusion",
         "A summary that re-lists the points you have just made is NOT a conclusion"),
        ("it names the shape explicitly", "is a table of contents written"),
        ("a conclusion must add something the points do not",
         "says something the points do \\\nnot"),
        ("a closing line is optional, never compulsory",
         "write no closing line at all"),
        ("never a summary and a closing line", "Never a summary AND a closing line"),
        ("a summary and a closer count towards the point cap",
         "Three source points plus a summary plus a closer is five, not three"),
):
    flat = PROMPT.replace("\\\n", "")
    check(f"the prompt says: {label}",
          needle.replace("\\\n", "") in flat
          or needle.replace("\\\n", "") in ab.ANSWER_STYLES["quick"].replace("\\\n", ""))

print("\n  -- an unsupported answer stops cleanly --")
for label, needle in (
        ("cite nothing when the evidence does not support the question", "CITE NOTHING"),
        ("a document lacking the fact is not evidence for it",
         "is not evidence for it"),
        ("citing eight documents to prove absence is padding", "padding dressed as rigour"),
        ("section 2 reads None", 'reads "None - no supplied chunk is evidence'),
        ("section 3 reads None", 'section 3 reads "None."'),
        ("sections 4 and 5 are one sentence each",
         "sections 4 and 5 are ONE sentence each"),
        ("the absence is not repeated in 4 and 5",
         "do not repeat the absence in sections 4 and 5"),
):
    check(f"the prompt says: {label}", needle in PROMPT.replace("\\\n", ""))

check("the refusal builders were NOT touched by this phase",
      "def build_refusal_answer(" in ASK_BRAIN_CODE
      and "def build_unsupported_subject_answer(" in ASK_BRAIN_CODE
      and "REFUSAL_SENTENCE" in ASK_BRAIN_CODE,
      "shortening a refusal is a change to refusal logic, which this phase may not make")
check("low-confidence behaviour is untouched",
      any(c[0] == "Low" and c[1] == "subject_absent_from_evidence"
          for c in ab.post_generation_ceilings({"unsupported_subject": True}))
      and any(c[0] == "Low" and c[1] == "recommendation_refused"
              for c in ab.post_generation_ceilings({"recommendation_refused": True})))

print("\n  -- the evaluator reports changes, not compliments --")
import run_phase14_answer_review as rv                             # noqa: E402

check("the per-question heading is 'Observed changes'",
      '"Observed changes:"' in REVIEW_SRC)
check("the old heading is gone",
      "Why the new answer is better" not in REVIEW_SRC)
check("there are three verdicts and 'better' is not one of them",
      {rv.IMPROVED, rv.REGRESSED, rv.CHANGED} == {"IMPROVED", "REGRESSED", "CHANGED"})
check("every note carries a verdict",
      all(isinstance(n, tuple) and len(n) == 2 for n in rv.compare(
          aq.measure(FIXTURES["says_each_thing_once"]),
          aq.measure(FIXTURES["repeats_itself"]))))


def verdicts(before_text, after_text):
    return {v for v, _ in rv.compare(aq.measure(before_text), aq.measure(after_text))}


SHORT = answer("Azure grew 39% in the December quarter [1].")
LONG = answer("Azure grew 39% in the December quarter [1].\n\n"
              + "\n".join(f"- A further point about capacity, cost and the outlook "
                           f"for segment {n}, stated at length [{n}]." for n in range(2, 8)))
length_notes = [(v, n) for v, n in rv.compare(aq.measure(SHORT), aq.measure(LONG))
                if "longer" in n or "shorter" in n]
check("growing in length is never itself an IMPROVED verdict",
      length_notes and all(v != rv.IMPROVED for v, _ in length_notes),
      str(length_notes))
check("and it is reported as a change, with the reason",
      any("length alone is not an improvement" in n for _, n in length_notes))
# Two answers both inside the band, so no band rule can fire and length is the
# only thing that moved.
IN_BAND_SHORT = answer(" ".join(["Azure grew in the quarter [1]."] * 4)
                       + " " + " ".join(["word"] * 90) + " [1].")
IN_BAND_LONG = answer(" ".join(["Azure grew in the quarter [1]."] * 4)
                      + " " + " ".join(["word"] * 150) + " [1].")
check("with the band held constant, longer earns no IMPROVED at all",
      rv.IMPROVED not in verdicts(IN_BAND_SHORT, IN_BAND_LONG),
      str(rv.compare(aq.measure(IN_BAND_SHORT), aq.measure(IN_BAND_LONG))))

# Shorter counts only when the evidence survived the cut.
RICH = answer("Azure grew 39% [1].\n\n- Capex rose as capacity was added [2].\n"
              "- Analysts name depreciation as the margin risk [3][4].\n"
              "- Microsoft Cloud revenue reached $51.5 billion [5].\n"
              "- The filing records the constant-currency figure [6].")
THIN = answer("Azure grew 39% [1].")
check("shorter WITH the citations intact earns IMPROVED",
      rv.IMPROVED in verdicts(LONG, answer(
          "Azure grew 39% in the December quarter [1].\n\n"
          "- Capex rose as capacity was added [2].\n"
          "- Analysts name depreciation as the margin risk [3][4].\n"
          "- Microsoft Cloud revenue reached $51.5 billion [5].\n"
          "- The filing records the constant-currency figure [6].\n"
          "- A sixth point carrying its own source [7].")))
check("shorter that LOST citations does not",
      rv.IMPROVED not in verdicts(RICH, THIN),
      str(rv.compare(aq.measure(RICH), aq.measure(THIN))))
check("and it says what went with the words",
      any("shorter is not better if evidence went with it" in n
          for _, n in rv.compare(aq.measure(RICH), aq.measure(THIN))))

UNCITED_CONCLUSION = answer("Azure grew 39% [1].\n\nTaken together, margins are under more "
                            "pressure than a year ago.")
CITED_CONCLUSION = answer("Azure grew 39% [1].\n\nTaken together, margins are under more "
                          "pressure than a year ago [1][2].")
check("an added CITED conclusion earns IMPROVED",
      rv.IMPROVED in verdicts(answer("Azure grew 39% [1]."), CITED_CONCLUSION))
check("an added UNCITED conclusion is a regression, not an improvement",
      rv.REGRESSED in verdicts(answer("Azure grew 39% [1]."), UNCITED_CONCLUSION)
      and rv.IMPROVED not in verdicts(answer("Azure grew 39% [1]."), UNCITED_CONCLUSION))
check("eliminating an uncited claim earns IMPROVED",
      rv.IMPROVED in verdicts(UNCITED_CONCLUSION, CITED_CONCLUSION))
check("leaving the quick band in EITHER direction is a regression",
      rv.REGRESSED in verdicts(FIXTURES["quick_length"], FIXTURES["bloated_length"])
      and rv.REGRESSED in verdicts(FIXTURES["quick_length"], THIN))

print("\n  -- the counters a mean would hide --")
check("the report counts Direct Answers over 220 words",
      "direct_over_220" in REVIEW_SRC and "Direct Answers over 220 words" in REVIEW_SRC)
check("and unsupported answers over 80 words", "unsupported_over_80" in REVIEW_SRC)
check("the unsupported cap is measured on the WHOLE answer, where the bloat was",
      'r[side]["words_total"] > UNSUPPORTED_WORD_CAP' in REVIEW_SRC)
check("the cap is a named constant", rv.UNSUPPORTED_WORD_CAP == 80)
check("the counters are category-aware, so a refusal is not judged on the quick band",
      "SUBSTANTIVE_CATEGORIES" in REVIEW_SRC
      and rv.SUBSTANTIVE_CATEGORIES == ("company_update", "analyst_concern", "risk_or_credit",
                                        "timeline_or_recent_change"))
check("the verdict tally is reported",
      "def verdict_counts(" in REVIEW_SRC and "IMPROVED**" in REVIEW_SRC)
check("a tick is described as a direction, never as proof",
      "It is a direction, not a " in REVIEW_SRC)

print("\n  -- Phase 14.3 changed nothing it was told not to --")
check("retrieval weights unchanged",
      (ab.W_VECTOR, ab.W_KEYWORD, ab.W_PRIORITY, ab.W_RECENCY) == (0.65, 0.25, 0.10, 0.10))
check("selection penalties unchanged",
      (ab.PRIORITY_BONUS, ab.PRIORITY_PENALTY, ab.DOC_REPEAT_PENALTY,
       ab.SOURCE_REPEAT_PENALTY) == (0.06, 0.03, 0.05, 0.03))
check("source-priority logic unchanged",
      ab.SOURCE_PRIORITIES == ("balanced", "company", "analyst", "none")
      if hasattr(ab, "SOURCE_PRIORITIES") else "def apply_source_priority" in ASK_BRAIN_CODE
      or "source_priority" in ASK_BRAIN_CODE)
check("the answer model is still chosen by .env, not by this phase",
      'os.environ.get("OPENAI_ANSWER_MODEL"' in ASK_BRAIN_SRC)
check("no reranking step was added",
      not re.search(r"def rerank|rerank\(", ASK_BRAIN_CODE))
check("the review script still writes nothing to the index or the archive",
      "mode=ro" in REVIEW_SRC and "chromadb" not in REVIEW_SRC
      and not re.search(r"(INSERT|UPDATE|DELETE)\s+(INTO\s+)?rag_chunks",
                        code_only(REVIEW_SRC), re.I))


# ===========================================================================
print("\n=== 13. PHASE 14.3.2: PROMPT CONSOLIDATION AND SELECTION ===")
#
# Phase 14.3 and 14.3.1 both tried to shorten quick answers by ADDING a rule,
# and both failed in the same way. The live run after 14.3.1:
#
#     mean_words_direct       124.37 -> 124.50
#     substantive_in_band          10 -> 8
#     uncited_claims                0 -> 2
#
# The diagnosis was that five of the five over-length answers had one shape -
# one point per source, then a summary restating them, then a closer - and that
# the prompt invited that closer in four separate places while the point cap
# never reached the summary unit at all. So this phase deleted rules instead of
# adding them: the length hierarchy now lives in ONE block, and group C states
# ONE priority. These checks assert that consolidation, and the behaviour it is
# supposed to produce. They do not pin the wording of the new text.

print("\n  -- the prompt got smaller, and the rules stopped competing --")
BASELINE_WORDS = 2217                   # the 14.3.1 prompt, measured in the brief
check("the prompt is shorter than the 14.3.1 prompt it replaced",
      len(PROMPT.split()) < BASELINE_WORDS,
      f"{len(PROMPT.split())} words, was {BASELINE_WORDS}")
check("and materially shorter, not trimmed",
      len(PROMPT.split()) < BASELINE_WORDS * 0.9,
      f"{BASELINE_WORDS - len(PROMPT.split())} words removed")
check("group C states one priority, at the top of the group",
      PROMPT.index("ANSWER THE QUESTION WITH THE MINIMUM EVIDENCE")
      - PROMPT.index("=== C. HOW TO WRITE THE ANSWER ===") < 120,
      "a priority stated halfway down a group is not a priority")
check("the length numbers live in the style block only, never in the base prompt",
      not re.search(r"\b(100|180|220)\b", PROMPT)
      and all(n in ab.ANSWER_STYLES["quick"] for n in ("100", "180", "220")),
      "two places naming a length is how 14.3.1 produced a 2,280-word prompt")
check("there is exactly ONE length-and-depth block per answer",
      ab.answer_instructions({"answer_style": "quick"}).count(
          "LENGTH AND DEPTH FOR THIS ANSWER") == 1)
check("no competing point-count rule survives anywhere",
      not re.search(r"two to four|2-4 supporting|two or three (specific )?items?|"
                    r"AT MOST THREE", PROMPT + ab.ANSWER_STYLES["quick"], re.I),
      "every count the live answers overshot was a count stated twice")
check("sections 4 and 5 ask for ONE or TWO items, matching the economy rules",
      PROMPT.count("ONE or TWO") == 2)

print("\n  -- a dense pack is a menu: eight sources, two needed --")
for key in ("selects_two_of_eight", "inventories_all_eight"):
    assert key in FIXTURES_1432
m_sel = aq.measure(FIXTURES_1432["selects_two_of_eight"])
m_inv = aq.measure(FIXTURES_1432["inventories_all_eight"])
check("the selective answer is inside the quick band",
      aq.in_band(m_sel["words_direct"]), f'{m_sel["words_direct"]} words')
check("and cites a minority of the pack, not all of it",
      len(m_sel["citations_in_direct"]) <= 3, str(m_sel["citations_in_direct"]))
check("the inventory answer to the SAME question overshoots the ceiling",
      m_inv["words_direct"] > aq.QUICK_WORD_BAND[1], f'{m_inv["words_direct"]} words')
check("and the overshoot is extra sources, not extra answer",
      len(m_inv["citations_in_direct"]) > len(m_sel["citations_in_direct"])
      and len(aq.claim_units(aq.parse_sections(FIXTURES_1432["inventories_all_eight"])
                             ["Direct Answer"]))
      > len(aq.claim_units(aq.parse_sections(FIXTURES_1432["selects_two_of_eight"])
                           ["Direct Answer"])))
check("selecting did not cost grounding: the selective answer cites everything",
      m_sel["claims_uncited"] == 0)
check("while the inventory answer's re-listing summary is itself uncited",
      m_inv["claims_uncited"] >= 1,
      "a summary that restates four cited points is a new uncited claim")

print("\n  -- a dense risk question fits under the ceiling without truncation --")
m_risk = aq.measure(FIXTURES_1432["dense_risk_capped"])
check("the risk answer is inside the band",
      aq.in_band(m_risk["words_direct"]), f'{m_risk["words_direct"]} words')
check("it carries three distinct risks, not one restated",
      len(aq.claim_units(aq.parse_sections(FIXTURES_1432["dense_risk_capped"])
                         ["Direct Answer"])) >= 3
      and m_risk["repetition"] < aq.REPETITION_OVERLAP,
      f'repetition {m_risk["repetition"]}')
check("and it is a finished answer, not a cut-off one",
      FIXTURES_1432["dense_risk_capped"].strip().endswith(".")
      and "..." not in aq.parse_sections(
          FIXTURES_1432["dense_risk_capped"])["Direct Answer"],
      "the cap is an instruction to the model, never a cut applied to its output")
check("every risk still carries its citation", m_risk["claims_uncited"] == 0)

print("\n  -- one point may rest on several sources --")
m_comb = aq.measure(FIXTURES_1432["combined_point_multi_cite"])
def units_citing(key, marker):
    """How many separate claim units carry this citation marker."""
    section = aq.parse_sections(FIXTURES_1432[key])["Direct Answer"]
    return sum(1 for unit in aq.claim_units(section) if marker in unit)


check("three sources that agree are carried by ONE supporting point, not three",
      (units_citing("combined_point_multi_cite", "[2]"),
       units_citing("combined_point_multi_cite", "[3]"),
       units_citing("combined_point_multi_cite", "[5]")) == (2, 2, 2),
      "the takeaway and one supporting point, never one point per source")
check("whereas enumerating puts each of them in a unit of its own",
      units_citing("one_sentence_per_source", "[2]") == 2
      and len(aq.claim_units(aq.parse_sections(
          FIXTURES_1432["one_sentence_per_source"])["Direct Answer"]))
      > len(aq.claim_units(aq.parse_sections(
          FIXTURES_1432["combined_point_multi_cite"])["Direct Answer"])))
check("and it carries all three citations", m_comb["claims_uncited"] == 0
      and {2, 3, 5} <= set(m_comb["citations_in_direct"]))
check("combining is cheaper than enumerating, measurably",
      m_comb["words_direct"] < aq.measure(
          FIXTURES_1432["one_sentence_per_source"])["words_direct"])
check("and the enumerated version says the same thing repeatedly",
      aq.measure(FIXTURES_1432["one_sentence_per_source"])["repetition"]
      > m_comb["repetition"])

print("\n  -- a point that becomes an evidence inventory --")
m_pt = aq.measure(FIXTURES_1432["point_becomes_an_inventory"])
inv_units = aq.claim_units(
    aq.parse_sections(FIXTURES_1432["point_becomes_an_inventory"])["Direct Answer"])
check("the defect is visible as length, so the review script counts it",
      m_pt["words_direct"] > aq.QUICK_WORD_BAND[1], f'{m_pt["words_direct"]} words')
check("it is ONE unit carrying several unrelated facts",
      max(aq.words(u) for u in inv_units) > 55,
      f"longest unit {max(aq.words(u) for u in inv_units)} words - an inventory "
      f"with a full stop")
check("it is not caught by repetition, which is why a word count is needed",
      m_pt["repetition"] < aq.REPETITION_OVERLAP,
      "enumerating different facts is not repeating one")
check("the review script reports exactly this overrun",
      "direct_over_220" in REVIEW_SRC and str(aq.QUICK_WORD_BAND[1]) in REVIEW_SRC)

print("\n  -- the unsupported answer, 30 to 60 words and nothing cited --")
m_uns = aq.measure(FIXTURES_1432["unsupported_minimal"])
check("the Direct Answer sits in the 30-60 word range",
      30 <= m_uns["words_direct"] <= 60, f'{m_uns["words_direct"]} words')
check("it cites nothing, because a document lacking the fact is not evidence for it",
      m_uns["citations_in_direct"] == [])
check("and it raises no uncited claim", m_uns["claims_uncited"] == 0)
check("the whole answer stays under the review script's unsupported cap",
      m_uns["words_total"] <= rv.UNSUPPORTED_WORD_CAP,
      f'{m_uns["words_total"]} words, cap {rv.UNSUPPORTED_WORD_CAP}')
check("that cap has little headroom over the prescribed shape, and is a bloat "
      "signal rather than a conformance test",
      rv.UNSUPPORTED_WORD_CAP - m_uns["words_total"] < 25,
      "a 60-word Direct Answer plus six mandatory sections exceeds 80 words while "
      "conforming; read unsupported_over_80 alongside words_direct")
check("the padded version of the same refusal is over that cap",
      aq.measure(FIXTURES_1432["unsupported_padded"])["words_total"]
      > rv.UNSUPPORTED_WORD_CAP,
      "sections 2-5 restating the absence is how a 93-word answer reached 360")
check("and the padded version cites documents that are not evidence",
      aq.measure(FIXTURES_1432["unsupported_padded"])["citations_in_direct"] != [])

print("\n  -- the two live uncited claims: fixed by wording, not by exemption --")
# Both sentences below came out of the live 14.3.1 run and were correctly
# flagged. Neither is a false positive, so the audit must NOT learn them.
LIVE_UNCITED = (
    ("T unsupported, subject buried behind two clauses",
     "Because no 1998-period company disclosures, SEC filings, or analyst notes are present "
     "in the retrieved evidence, the archive material supplied here cannot report or "
     "summarize AT&T's fiscal 1998 results."),
    ("B risk_or_credit, subject is an infinitive phrase",
     "To answer the question requires Barnes Group-specific sources such as filings or "
     "analyst coverage."),
)
for label, sentence in LIVE_UNCITED:
    check(f"the audit still flags it ({label})",
          not ab.is_pack_absence_sentence(sentence)
          and not ab.is_non_factual_sentence(sentence),
          "a sentence this long and this indirect is not a recognised absence form")

# The prescribed wording is exempt already, with no change to the audit - which
# is why this phase changed the prompt and not answer_quality.py.
for sentence in ("The retrieved evidence does not contain enough information to answer "
                 "this reliably.",
                 "The pack holds no Barnes Group filings or analyst coverage.",
                 "The supplied chunks contain nothing dated 1998."):
    check(f"the prescribed form is exempt: {sentence[:46]}...",
          ab.is_non_factual_sentence(sentence) or ab.is_pack_absence_sentence(sentence))
check("the prompt prescribes that form verbatim, so the model can produce it",
      "The retrieved evidence does not contain enough information to answer this reliably."
      in PROMPT.replace("\\\n", ""))
check("and keeps the evidence as the grammatical subject of the gap sentence",
      "with the evidence still as the" in PROMPT
      and "says something about Kimco" in PROMPT)
check("a company subject is still a claim that cites, however it is phrased",
      not ab.is_pack_absence_sentence(
          "Kimco's 1998 results are not present in these documents.")
      and not ab.is_pack_absence_sentence(
          "Barnes Group's filings are not disclosed in the retrieved evidence."))
check("the audit was not loosened to absorb the live sentences",
      aq.MODULE_VERSION == "phase14.2-1.0", aq.MODULE_VERSION)
check("the shared definition of 'not a claim' is still a single function",
      "from ask_brain import" in EVAL_SRC or "ab.is_non_factual_sentence" in EVAL_SRC
      or "analyst_governor" in EVAL_SRC)

print("\n  -- no synthesis closer was forced, and none was bought cheaply --")
check("an answer with no closing line is a legitimate answer",
      aq.measure(FIXTURES["has_no_synthesis"])["claims_uncited"] == 0
      and not aq.measure(FIXTURES["has_no_synthesis"])["has_synthesis"],
      "with_synthesis is an observation, never a target")
check("the prompt says a takeaway can BE the conclusion",
      "write no closing line at all" in PROMPT)
check("a substantive conclusion still cites, marked or not",
      not ab.is_synthesis_sentence(
          "Taken together, margins are under more pressure than a year ago."))
check("synthesis carrying a figure is still an uncited claim",
      aq.measure(FIXTURES["has_synthesis_smuggling_a_figure"])["claims_uncited"] >= 1)
check("and a connective conclusion is still allowed to go uncited",
      aq.measure(FIXTURES["has_marked_synthesis"])["synthesis_uncited"] >= 0
      and aq.measure(FIXTURES["has_marked_synthesis"])["claims_uncited"] == 0)

print("\n  -- Phase 14.3.2 is prompt-only --")
check("the word caps are never enforced by cutting the generated text",
      not re.search(r"(?:answer|direct_answer)\s*=\s*\w*answer\w*\[\s*:\s*\d+",
                    ASK_BRAIN_CODE)
      and "textwrap.shorten" not in ASK_BRAIN_CODE
      and not re.search(r"def\s+truncate|\.truncate\(", ASK_BRAIN_CODE),
      "the cap is a generation instruction, never a cut applied to the output")
check("the answer is still written exactly as the model produced it",
      "exactly as the model wrote it" in ASK_BRAIN_SRC)
check("the rubric was not touched", aq.MODULE_VERSION == "phase14.2-1.0")
check("the evaluator's Phase 14.3 behaviour survives",
      '"Observed changes:"' in REVIEW_SRC and "def verdict_counts(" in REVIEW_SRC
      and {rv.IMPROVED, rv.REGRESSED, rv.CHANGED} == {"IMPROVED", "REGRESSED", "CHANGED"}
      and "Why the new answer is better" not in REVIEW_SRC,
      "14.3.2 made no evaluator change; 14.3.3 fixed its report cut and added "
      "generation_incomplete, and neither may undo the 14.3 verdict wording")
check("the Phase 14.3 counters survive",
      "direct_over_220" in REVIEW_SRC and "unsupported_over_80" in REVIEW_SRC)
check("the deep style was not touched",
      "may run to about 400 words" in ab.ANSWER_STYLES["deep"]
      and "100-180" not in ab.ANSWER_STYLES["deep"]
      and "AT MOST" not in ab.ANSWER_STYLES["deep"])
check("retrieval, ranking and selection are untouched",
      (ab.W_VECTOR, ab.W_KEYWORD, ab.W_PRIORITY, ab.W_RECENCY) == (0.65, 0.25, 0.10, 0.10)
      and (ab.PRIORITY_BONUS, ab.PRIORITY_PENALTY, ab.DOC_REPEAT_PENALTY,
           ab.SOURCE_REPEAT_PENALTY) == (0.06, 0.03, 0.05, 0.03)
      and ab.WEAK_EVIDENCE_SIMILARITY == 0.25
      and ab.RETRIEVAL_MODES == ("vector", "keyword", "hybrid"))
check("the deterministic refusal builders are untouched",
      "def build_refusal_answer(" in ASK_BRAIN_CODE
      and "def build_unsupported_subject_answer(" in ASK_BRAIN_CODE
      and "REFUSAL_SENTENCE" in ASK_BRAIN_CODE)
check("the confidence ceilings are untouched",
      any(c[0] == "Low" for c in ab.post_generation_ceilings(
          {"citation_audit": {"uncited_sentences": ["Margins fell sharply this year."]}}))
      and any(c[0] == "Low" and c[1] == "subject_absent_from_evidence"
              for c in ab.post_generation_ceilings({"unsupported_subject": True})))
check("the six-section contract is unchanged",
      tuple(ab.ANSWER_SECTIONS) == ("Direct Answer", "Evidence Used", "Source Citations",
                                    "Evidence Limitations", "Needs Human Review",
                                    "Confidence")
      and tuple(aq.ANSWER_SECTIONS) == tuple(ab.ANSWER_SECTIONS),
      str(ab.ANSWER_SECTIONS))
check("the pipeline version is at or past this phase",
      ab.PIPELINE_VERSION >= "phase14.3.2" and ui.PHASE >= "phase14.3.2"
      and ab.PIPELINE_VERSION == ui.PHASE,
      f"{ab.PIPELINE_VERSION} / {ui.PHASE}")

# ===========================================================================
print("\n=== 14. PHASE 14.3.3: GENERATION COMPLETION INTEGRITY ===")
#
# Two answers in the 14.3.2 live run looked like they stopped mid-sentence:
#
#     run 388  MSFT risk_or_credit     Direct Answer ended "Taken together,"
#     run 411  AMZN analyst_concern    Direct Answer ended "which could force"
#
# Neither had. Both answer files hold all six sections and both Direct Answers
# run to a full stop with citations. The cut was in the REPORT: the <details>
# blocks were written as `after_text.strip()[:2600]`, and character 2600 of
# those two files lands exactly after those two fragments.
#
# So this section checks two separate things. First, that the report renders
# the answer by structure and can no longer cut mid-sentence. Second, that the
# pipeline would now NOTICE a real cut - because before this phase it could
# not: the API's own status was returned by call_answer_model and discarded by
# its caller, and an "incomplete" response WITH partial text was accepted,
# audited and filed exactly like a finished answer.

print("\n  -- the report cannot cut an answer mid-sentence --")
check("the blind character slice is gone from the report",
      "[:2600]" not in REVIEW_SRC,
      "raising the number would only move the cut")
check("the excerpt is taken by structure, not by character count",
      "def answer_body(" in REVIEW_SRC and "ANSWER_BODY_MARKERS" in REVIEW_SRC)

# An answer file shaped like the real ones: front matter, then the six
# sections, then the appendices. Built here rather than pasted from the live
# run, because what is being tested is where the cut falls, not the prose.
_FRONT = ("# Ask Brain answer - MSFT\n\n**Question:** What risks does the research "
          "identify?\n\n"
          + "".join(f"- metadata_line_{n}: " + "x" * 60 + "\n" for n in range(1, 19))
          + "\n---\n\n")
_LONG_DIRECT = (" ".join(
    f"Risk {n} is named by the research and carries its own citation [{n % 8 + 1}]."
    for n in range(1, 26)) + " Taken together, these notes frame a trade-off [1][3].")
_FILE_SHAPED = (_FRONT
                + "## 1. Direct Answer\n\n" + _LONG_DIRECT + "\n\n"
                + "## 2. Evidence Used\n\n- [1] the filing.\n- [3] a note.\n\n"
                + "## 3. Source Citations\n\n- [1] MSFT 10-Q.pdf, pp. 34-36\n\n"
                + "## 4. Evidence Limitations\n\nOne quarter only.\n\n"
                + "## 5. Needs Human Review\n\nConfirm the figures.\n\n"
                + "## 6. Confidence\n\nMedium - partial source set.\n\n"
                + "---\n\n## Source table\n\n| # | file_name |\n|---|---|\n| 1 | x.pdf |\n"
                + "\nRetrieval log: `06_RAG_Index/retrieval_logs/x.json`\n")

check("the fixture reproduces the failure: a blind 2,600-char slice lands mid-sentence",
      len(_FILE_SHAPED) > 2600
      and not re.search(r"[.!?][\"'\u201d\u2019)\]]*$", _FILE_SHAPED.strip()[:2600]),
      repr(_FILE_SHAPED.strip()[2560:2600]))

_excerpt = rv.answer_excerpt(_FILE_SHAPED)
check("the excerpt carries all six sections",
      all(f"## {n}." in _excerpt for n in range(1, 7)),
      str([n for n in range(1, 7) if f"## {n}." not in _excerpt]))
check("and the Direct Answer in it is whole",
      _LONG_DIRECT in _excerpt)
check("the excerpt ends a sentence, so no reader can mistake it for a cut generation",
      bool(re.search(r"[.!?][\"'\u201d\u2019)\]]*$", _excerpt.strip())),
      repr(_excerpt.strip()[-40:]))
check("the metadata block the report already tabulates is not what fills the excerpt",
      "metadata_line_1" not in _excerpt)
check("and neither are the appendices",
      "## Source table" not in _excerpt and "Retrieval log" not in _excerpt)

# The guard sits above every answer in the corpus, so nothing real is trimmed -
# but when it does fire it must say so rather than stopping silently.
_ENORMOUS = (_FILE_SHAPED.replace(_LONG_DIRECT, _LONG_DIRECT * 40))
_trimmed = rv.answer_excerpt(_ENORMOUS, "06_RAG_Index/answer_logs/x.md")
check("an enormous answer is trimmed at a paragraph break, and says so",
      "trimmed at a paragraph break" in _trimmed
      and "06_RAG_Index/answer_logs/x.md" in _trimmed)
check("the trim is attributed to the report, not to the model",
      "not by the model" in _trimmed)
check("the guard is above the longest answer in the corpus",
      rv.ANSWER_EXCERPT_GUARD > 8040,
      f"guard {rv.ANSWER_EXCERPT_GUARD}; the longest of the 60 before/after answers is 8,040")

print("\n  -- the deterministic completeness guard --")


def _answer_sections(direct, confidence="Medium - partial source set.",
                     used="- [1] the filing.", citations="- [1] MSFT 10-Q.pdf, pp. 34-36",
                     limits="One quarter only.", review="Confirm the figures.",
                     drop=()):
    parts = [("Direct Answer", direct), ("Evidence Used", used),
             ("Source Citations", citations), ("Evidence Limitations", limits),
             ("Needs Human Review", review), ("Confidence", confidence)]
    return "".join(f"## {n}. {name}\n\n{body}\n\n"
                   for n, (name, body) in enumerate(parts, start=1)
                   if name not in drop)


_COMPLETE = _answer_sections(
    "Azure grew 39% in the December quarter, and capacity is the binding constraint [1]. "
    "Several analyst notes name depreciation as the main near-term margin risk [3][4].")
check("a complete six-section answer passes",
      not ab.generation_completeness(_COMPLETE)["incomplete"],
      str(ab.generation_completeness(_COMPLETE)["reasons"]))

for name in ab.ANSWER_SECTIONS:
    missing = ab.generation_completeness(_answer_sections(
        "Azure grew 39% in the December quarter [1].", drop=(name,)))
    check(f"a missing '{name}' section is flagged",
          missing["incomplete"] and name in missing["missing_sections"])

# The two live tails. Neither is a prose judgement: "Taken together," ends on
# punctuation that cannot end a sentence, and "which could force" ends without
# finishing one at all.
for label, direct in (
        ("a Direct Answer ending \"Taken together,\"",
         "Risk one is capacity [1]. Risk two is depreciation [3]. Taken together,"),
        ("a Direct Answer ending \"which could force\"",
         "Analysts flag funding uncertainty [2][1], which could force"),
        ("a Direct Answer ending on a colon",
         "The research identifies three risks:"),
        ("a Direct Answer ending on a dash",
         "Capacity is constrained in some regions [1] -"),
        ("a Direct Answer ending on a conjunction",
         "Capex rose and margins compressed [2] but"),
):
    result = ab.generation_completeness(_answer_sections(direct))
    check(f"flagged: {label}", result["incomplete"], str(result["reasons"]))

for label, direct in (
        ("a sentence ending in a full stop after its citations",
         "Azure grew 39% in the December quarter [1][3]."),
        ("a sentence ending in a quoted phrase",
         "One check reports that in some regions \"there is no capacity\" [1]."),
        ("a refusal sentence",
         "I cannot provide a buy/sell/hold recommendation or tell Cutler what action to take."),
        ("the mandated directness sentence",
         "The retrieved evidence is related, but it does not directly answer the question."),
        ("the prescribed unsupported wording",
         "The retrieved evidence does not contain enough information to answer this reliably. "
         "The pack holds no AT&T disclosures dated 1998 or earlier."),
):
    result = ab.generation_completeness(_answer_sections(direct))
    check(f"NOT flagged: {label}", not result["incomplete"], str(result["reasons"]))

# Measured, not assumed: sections 2 and 3 end on a file name or a page range
# often enough that requiring a full stop there would flag good answers.
check("a Source Citations section ending on a page range is not flagged",
      not ab.generation_completeness(_answer_sections(
          "Azure grew 39% [1].",
          citations="- [1] MSFT 10Q Q2 FY26 1.28.26.pdf, pages 34-36"))["incomplete"])
check("an Evidence Used section ending on a source name is not flagged",
      not ab.generation_completeness(_answer_sections(
          "Azure grew 39% [1].",
          used="- [8] third-party research, 2026-07-30, Research Note"))["incomplete"])
check("the full-stop rule applies to the prose sections only, and names which",
      ab.PROSE_SECTIONS == ("Direct Answer", "Evidence Limitations", "Needs Human Review"),
      str(ab.PROSE_SECTIONS))
check("a Confidence section with no grade is flagged",
      ab.generation_completeness(_answer_sections(
          "Azure grew 39% [1].", confidence="The evidence is"))["incomplete"])
check("the deterministic refusal builders still pass the guard",
      not ab.generation_completeness(ab.build_refusal_answer(
          SELECTED_FIXTURE, {"is_request": True, "reason": "asks for a decision"},
          "MSFT"))["incomplete"]
      and not ab.generation_completeness(ab.build_unsupported_subject_answer(
          {"missing": ["fiscal 1998"], "present": [], "terms": ["fiscal 1998"]},
          SELECTED_FIXTURE, "MSFT", "company_update"))["incomplete"],
      "a guard that flags this project's own deterministic answers is a broken guard")

print("\n  -- an unfinished answer is Low, and nothing is rewritten --")
check("the guard caps confidence at Low",
      any(c[0] == "Low" and c[1] == "generation_incomplete"
          for c in ab.post_generation_ceilings({
              "generation_incomplete": True,
              "generation_incomplete_reasons": ["section 'Direct Answer' ends on \",\""]})))
check("an API-reported incomplete caps it too, even when the text ends cleanly",
      any(c[0] == "Low" and c[1] == "generation_incomplete"
          for c in ab.post_generation_ceilings({"generation_status": "incomplete",
                                                "generation_incomplete_reason":
                                                    "max_output_tokens"})),
      "the two signals are not the same thing and either one is enough")
check("a clean generation is not capped by this rule",
      not any(c[1] == "generation_incomplete"
              for c in ab.post_generation_ceilings({"generation_status": "completed"})))
check("a run that called no model is not capped by this rule",
      not any(c[1] == "generation_incomplete" for c in ab.post_generation_ceilings({})),
      "the deterministic refusal paths leave generation_status empty")
check("audit_answer reports the guard rather than acting on it",
      "generation_incomplete" in ab.audit_answer(
          _answer_sections("Analysts flag funding uncertainty [2], which could force"), [])
      and ab.audit_answer(_answer_sections(
          "Analysts flag funding uncertainty [2], which could force"),
          [])["generation_incomplete"] is True)
check("the answer text is not altered by the guard",
      ab.audit_answer(_COMPLETE, [])["sections"]["Direct Answer"]
      in _COMPLETE,
      "the guard reports; it never rewrites")
check("no truncation or rewriting was added to the pipeline",
      not re.search(r"(?:answer|direct_answer|answer_text)\s*=\s*\w*answer\w*\[\s*:\s*\d+",
                    ASK_BRAIN_CODE)
      and "textwrap.shorten" not in ASK_BRAIN_CODE
      and not re.search(r"def\s+truncate|\.truncate\(", ASK_BRAIN_CODE))
check("the answer is still written exactly as the model produced it",
      "exactly as the model wrote it" in ASK_BRAIN_SRC)

print("\n  -- the API's own verdict is recorded, not discarded --")
check("the incomplete reason is read from the Responses shape AND the finish_reason shape",
      "def incomplete_reason_of(" in ASK_BRAIN_CODE
      and "incomplete_details" in ASK_BRAIN_CODE
      and "finish_reason" in ASK_BRAIN_CODE)
check("the run row carries the generation status, the reason and the budget",
      all(column in ab.RUN_COLUMNS for column in (
          "generation_status", "generation_incomplete", "generation_incomplete_reason",
          "answer_max_output_tokens", "answer_retry_used")),
      "a new column is added in place by create_schema, so no run history is lost")
check("the schema migration path is the existing one, not a rebuild",
      "ALTER TABLE ask_brain_runs ADD COLUMN" in ASK_BRAIN_CODE
      and "DROP TABLE" not in ASK_BRAIN_CODE)
check("the caller no longer drops the status on the floor",
      'context["generation_status"] = result["status"]' in ASK_BRAIN_CODE)


# A stub client, so the retry path is exercised without a key, a network or a
# cent. Each call records the kwargs it was given, which is how the test below
# can assert that the retry re-sent the SAME evidence rather than new evidence.
class _StubResponse:
    def __init__(self, text, status="completed", reason=None):
        self.output_text = text
        self.status = status
        self.incomplete_details = type("D", (), {"reason": reason})() if reason else None
        self.usage = type("U", (), {"input_tokens": 1000, "output_tokens": 300})()


class _StubClient:
    def __init__(self, *scripted):
        self.scripted = list(scripted)
        self.calls = []
        outer = self

        class _Responses:
            @staticmethod
            def create(**kwargs):
                outer.calls.append(kwargs)
                return outer.scripted[min(len(outer.calls) - 1, len(outer.scripted) - 1)]

        self.responses = _Responses()


_SETTINGS = {"answer_model": "gpt-5-mini", "reasoning_effort": "low",
             "max_output_tokens": 6000, "answer_style": "quick"}
_USER_INPUT = "QUESTION: What risks?\n\nEVIDENCE:\n[1] chunk one\n[3] chunk three"

_clean = _StubClient(_StubResponse(_COMPLETE))
_result = ab.call_answer_model(_clean, dict(_SETTINGS), _USER_INPUT)
check("a clean generation makes exactly one call",
      len(_clean.calls) == 1, f"{len(_clean.calls)} call(s)")
check("and reports status completed with no retry",
      _result["status"] == "completed" and not _result["retry_used"]
      and _result["incomplete_reason"] == "")
check("the budget actually used is reported",
      _result["max_output_tokens"] == 6000, str(_result["max_output_tokens"]))

_cut = _answer_sections("Risk one is capacity [1]. Taken together,")
_recovered = _StubClient(_StubResponse(_cut, "incomplete", "max_output_tokens"),
                         _StubResponse(_COMPLETE))
_result = ab.call_answer_model(_recovered, dict(_SETTINGS), _USER_INPUT)
check("an incomplete generation WITH partial text is retried",
      len(_recovered.calls) == 2, f"{len(_recovered.calls)} call(s)")
check("the length reason is recorded",
      _recovered.calls[0] is not None and _result["incomplete_reason"] == "",
      "the retry completed, so the recorded reason is the retry's")
check("the retry's text REPLACES the cut text and is never stitched onto it",
      _result["text"] == _COMPLETE.strip()
      and "Taken together," not in _result["text"])
check("the retry is logged as a retry", _result["retry_used"] is True)
check("the retry raises the output budget rather than shortening the writing rules",
      _recovered.calls[1]["max_output_tokens"] == 2 * _recovered.calls[0]["max_output_tokens"],
      str([c["max_output_tokens"] for c in _recovered.calls]))
check("the retry re-sends the SAME question and the SAME evidence",
      _recovered.calls[1]["input"] == _recovered.calls[0]["input"] == _USER_INPUT,
      "a retry that re-retrieved would be answering a different question")
check("and the SAME writing instructions, so the style did not change either",
      _recovered.calls[1]["instructions"] == _recovered.calls[0]["instructions"]
      == ab.answer_instructions(_SETTINGS))
check("the retry does not broaden the evidence pack",
      all("chunk one" in c["input"] and "chunk three" in c["input"]
          and c["input"].count("[") == _USER_INPUT.count("[")
          for c in _recovered.calls))
check("no retrieval function is reachable from the generation call",
      not re.search(r"(?:filter_chunks|rank_candidates|select_|embed_query|open_collection)\(",
                    ASK_BRAIN_CODE.split("def call_answer_model(")[1].split("\ndef ")[0]),
      "the retry must not be able to change what it is answering from")

_still = _StubClient(_StubResponse(_cut, "incomplete", "max_output_tokens"),
                     _StubResponse(_cut, "incomplete", "max_output_tokens"),
                     _StubResponse(_COMPLETE))
_result = ab.call_answer_model(_still, dict(_SETTINGS), _USER_INPUT)
check("there is a STRICT maximum of one retry",
      len(_still.calls) == 2, f"{len(_still.calls)} call(s) - a second retry is not allowed")
check("a still-incomplete retry is kept, flagged and capped rather than hidden",
      _result["status"] == "incomplete"
      and _result["incomplete_reason"] == "max_output_tokens"
      and ab.generation_completeness(_result["text"])["incomplete"]
      and any(c[0] == "Low" for c in ab.post_generation_ceilings(
          {"generation_status": _result["status"]})))

_empty = _StubClient(_StubResponse("", "incomplete", "max_output_tokens"),
                     _StubResponse("", "incomplete", "max_output_tokens"))
try:
    ab.call_answer_model(_empty, dict(_SETTINGS), _USER_INPUT)
    _raised = ""
except RuntimeError as exc:
    _raised = str(exc)
check("an answer that never arrives is still an error, not an empty answer",
      "returned no text" in _raised and "OPENAI_ANSWER_MAX_OUTPUT_TOKENS" in _raised,
      _raised[:90])

print("\n  -- the review script reports and can target --")
check("generation_incomplete is one of the reported metrics",
      '"generation_incomplete"' in REVIEW_SRC
      and "Answers whose generation did not finish" in REVIEW_SRC)
check("it is counted, not averaged",
      "generation_incomplete\": sum(1 for r in rows" in REVIEW_SRC
      or re.search(r"generation_incomplete\W+sum\(", REVIEW_SRC) is not None)
check("the report uses the pipeline's own completeness function, not a second opinion",
      "ab.generation_completeness(" in REVIEW_SRC)
check("the API-reported status is read from the run row too",
      '"generation_status"' in REVIEW_SRC and "answer_max_output_tokens" in REVIEW_SRC)
check("--only exists, so two questions can be re-asked without paying for thirty",
      '"--only"' in REVIEW_SRC and "selected_question" in REVIEW_SRC)
check("--confirm-cost is still required to spend anything",
      "Refusing to run without --confirm-cost" in REVIEW_SRC)
check("the review script still never opens the V drive or imports a model client",
      not re.search(r"V:\\\\|chromadb|import openai", REVIEW_SRC))

print("\n  -- Phase 14.3.3 changed nothing it was told not to --")
check("the Phase 14.3.2 compression survives, in content and in size",
      len(PROMPT.split()) == PROMPT_WORDS
      and len(PROMPT.split()) < PROMPT_WORDS_1431 * 0.9
      and "ANSWER THE QUESTION WITH THE MINIMUM EVIDENCE" in PROMPT
      and "EVIDENCE SUPPLIED IS A MENU, NOT A CHECKLIST" in PROMPT,
      f"{len(PROMPT.split())} words, expected {PROMPT_WORDS}, "
      f"14.3.1 baseline {PROMPT_WORDS_1431}")
check("the quick compression hierarchy is unchanged",
      "AT MOST TWO supporting points" in ab.ANSWER_STYLES["quick"]
      and "100-180 words" in ab.ANSWER_STYLES["quick"]
      and "220 words is a maximum, not a target" in ab.ANSWER_STYLES["quick"]
      and "30-60 words, and stop" in ab.ANSWER_STYLES["quick"])
check("no answer-style rule was added",
      len(ab.ANSWER_STYLES["quick"].split()) == QUICK_WORDS_1432
      and len(ab.ANSWER_STYLES["deep"].split()) == 132,
      f'{len(ab.ANSWER_STYLES["quick"].split())} / '
      f'{len(ab.ANSWER_STYLES["deep"].split())} words')
check("the lower word bound was not optimised: nothing forces an answer up to 100 words",
      "100-180 words" in ab.ANSWER_STYLES["quick"]
      and not re.search(r"at least \d+ words|minimum of \d+ words|no fewer than",
                        PROMPT + ab.ANSWER_STYLES["quick"], re.I),
      "KIM analyst_concern at 76 words and T company_update at 76 are correct answers")
check("the unsupported whole-answer cap is still a diagnostic, not a structural rule",
      rv.UNSUPPORTED_WORD_CAP == 80
      and tuple(ab.ANSWER_SECTIONS) == tuple(aq.ANSWER_SECTIONS)
      and len(ab.ANSWER_SECTIONS) == 6,
      "the six-section contract was not shortened to fit a counter")
check("the rubric was not touched", aq.MODULE_VERSION == "phase14.2-1.0", aq.MODULE_VERSION)
check("retrieval, ranking and selection are untouched",
      (ab.W_VECTOR, ab.W_KEYWORD, ab.W_PRIORITY, ab.W_RECENCY) == (0.65, 0.25, 0.10, 0.10)
      and (ab.PRIORITY_BONUS, ab.PRIORITY_PENALTY, ab.DOC_REPEAT_PENALTY,
           ab.SOURCE_REPEAT_PENALTY) == (0.06, 0.03, 0.05, 0.03)
      and ab.WEAK_EVIDENCE_SIMILARITY == 0.25
      and ab.RETRIEVAL_MODES == ("vector", "keyword", "hybrid"))
check("this phase wrote nothing to any index",
      "INSERT INTO rag_chunks" not in ASK_BRAIN_CODE
      and "collection.add(" not in ASK_BRAIN_CODE)
check("the refusal and confidence paths are untouched",
      "def build_refusal_answer(" in ASK_BRAIN_CODE
      and "def build_unsupported_subject_answer(" in ASK_BRAIN_CODE
      and "REFUSAL_SENTENCE" in ASK_BRAIN_CODE
      and any(c[0] == "Low" and c[1] == "recommendation_refused"
              for c in ab.post_generation_ceilings({"recommendation_refused": True})))
check("the pipeline version is at or past Phase 14.3.3",
      ab.PIPELINE_VERSION >= "phase14.3.3" and ab.PIPELINE_VERSION == ui.PHASE,
      f"{ab.PIPELINE_VERSION} / {ui.PHASE}")

# ===========================================================================
print("\n=== 15. PHASE 14.3.4: THE TAKEAWAY IS NOT AN EXEMPTION ===")
#
# The 14.3.2 live run left exactly two uncited claims, and both were the same
# shape - an opening takeaway stating an umbrella claim with the citations
# deferred to the specifics beneath it:
#
#   run 388 MSFT  "Analyst research identifies two chief risk areas for
#                  Microsoft around AI investment and cloud capacity."
#   run 411 AMZN  "Analysts in the supplied research pack say heavy AI
#                  infrastructure capex has produced a near-term hit to free
#                  cash flow and raised funding and execution concerns."
#
# The model was obeying the prompt. Group E offered a choice - "either put the
# claim and its citation in that first sentence, OR start with the takeaway" -
# and the second branch reads as an alternative to citing. Phase 14 wrote that
# clause itself. This phase deletes it and says what it was hiding.
#
# The audit is NOT touched. Both sentences were already flagged correctly, so
# there was never a false positive to fix; the generated wording was wrong.

print("\n  -- the exemption is gone --")
check("the prompt no longer offers the takeaway as an alternative to citing",
      "or start with the takeaway" not in PROMPT
      and "start with the takeaway" not in PROMPT,
      "this was the whole defect: a choice where there should have been a rule")
check("and the Phase 8.1 rule it was attached to is still there, word for word",
      "Do not open with an uncited summary sentence" in PROMPT
      and '"The retrieved source text says the following"' in PROMPT,
      "the banned provenance opening is read by the rubric, so its wording is load-bearing")
check("the opening must now carry its own citation",
      "put the claim and its citation in that first sentence" in PROMPT)

print("\n  -- and the rule it was hiding is explicit --")
check("a takeaway is named as NOT an exception",
      "A takeaway is NOT an exception" in PROMPT)
check("the classes of claim are named, so 'summary' is not a loophole",
      all(phrase in PROMPT for phrase in (
          "an opening takeaway that states a direction",
          "a count of what the research identifies",
          "anything about the company is a claim")),
      "'identifies two chief risk areas' is a count; that is what run 388 wrote")
check("and the citation is required in the SAME sentence, not in section 2 or 3",
      "its citations belong in that same sentence" in PROMPT
      and "a marker in the evidence list does not cite the claim" in PROMPT)
check("the three real exemptions are unchanged and still the only ones",
      "The only sentences that may carry no citation are sentences that decline to answer"
      in PROMPT
      and "sentences that state the evidence is insufficient" in PROMPT
      and "the purely connective conclusion described in B3" in PROMPT)

print("\n  -- the two live sentences, and the shape that replaces them --")
_LIVE_UNCITED_TAKEAWAYS = (
    ("run 388 MSFT risk_or_credit",
     "Analyst research identifies two chief risk areas for Microsoft around AI "
     "investment and cloud capacity."),
    ("run 411 AMZN analyst_concern",
     "Analysts in the supplied research pack say heavy AI infrastructure capex has "
     "produced a near-term hit to free cash flow and raised funding and execution "
     "concerns."),
)
for label, sentence in _LIVE_UNCITED_TAKEAWAYS:
    check(f"still an uncited claim, with the audit untouched ({label})",
          not ab.is_non_factual_sentence(sentence)
          and not ab.is_pack_absence_sentence(sentence)
          and not ab.is_synthesis_sentence(sentence),
          "neither sentence was ever a false positive")
    check(f"  and it still caps confidence to Low ({label})",
          any(c[0] == "Low" and c[1] == "uncited_claims_in_direct_answer"
              for c in ab.post_generation_ceilings(
                  {"citation_audit": {"uncited_sentences": [sentence]}})))

# The same two openings with their citations attached: the fix is in the
# generated wording, and the audit already accepts the corrected form.
for label, sentence in (
        ("a counted umbrella takeaway, cited",
         "Analyst research identifies two chief risk areas for Microsoft around AI "
         "investment and cloud capacity [1][6]."),
        ("an attributed takeaway, cited",
         "Analysts say heavy AI infrastructure capex has produced a near-term hit to "
         "free cash flow [2][5]."),
):
    body = ("## 1. Direct Answer\n\n" + sentence
            + "\n\n## 2. Evidence Used\n\n- [1] a note.\n\n"
              "## 3. Source Citations\n\n- [1] MSFT BI 7.7.26.pdf, pp. 1-2\n\n"
              "## 4. Evidence Limitations\n\nOne period only.\n\n"
              "## 5. Needs Human Review\n\nConfirm the figures.\n\n"
              "## 6. Confidence\n\nMedium - partial source set.\n")
    measured = aq.measure(body)
    check(f"the corrected form raises no uncited claim ({label})",
          measured["claims_uncited"] == 0, str(measured["claims_uncited"]))
    check(f"  and its opening is recognised as cited ({label})",
          measured["opening_cited"] and not measured["opens_with_boilerplate"])
    check(f"  and it is still a finished answer ({label})",
          not ab.generation_completeness(body)["incomplete"])

check("a cited takeaway is not thereby exempt from being a claim",
      not ab.is_synthesis_sentence(
          "Analyst research identifies two chief risk areas for Microsoft [1][6]."),
      "it cites because it is a claim, not because citing makes it something else")
check("a genuinely connective opening may still go uncited",
      ab.is_synthesis_sentence(
          "Taken together, these are two different questions rather than one."))
check("and a refusal opening still needs no citation",
      aq.measure(FIXTURES["refusal_recommendation"])["claims_uncited"] == 0
      and aq.measure(FIXTURES["refusal_unsupported"])["claims_uncited"] == 0)
check("the mandated directness sentence still needs no citation",
      aq.measure(FIXTURES["opens_with_the_mandated_directness_sentence"])
      ["claims_uncited"] == 0)

print("\n  -- Phase 14.3.4 is one clause of group E and nothing else --")
check("the change is inside group E",
      PROMPT.index("A takeaway is NOT an exception") > PROMPT.index("=== E. FORMAT ==="))
check("the quick hierarchy is untouched",
      len(ab.ANSWER_STYLES["quick"].split()) == QUICK_WORDS_1432
      and "AT MOST TWO supporting points" in ab.ANSWER_STYLES["quick"]
      and "100-180 words" in ab.ANSWER_STYLES["quick"]
      and "220 words is a maximum, not a target" in ab.ANSWER_STYLES["quick"]
      and "30-60 words, and stop" in ab.ANSWER_STYLES["quick"],
      f'{len(ab.ANSWER_STYLES["quick"].split())} words')
check("the deep style is untouched",
      len(ab.ANSWER_STYLES["deep"].split()) == 132)
check("unsupported-answer behaviour is untouched",
      "The retrieved evidence does not contain enough information to answer this reliably."
      in PROMPT
      and "CITE NOTHING" in PROMPT
      and 'section 3 reads "None."' in PROMPT
      and "def build_unsupported_subject_answer(" in ASK_BRAIN_CODE)
check("refusal behaviour is untouched",
      "def build_refusal_answer(" in ASK_BRAIN_CODE and "REFUSAL_SENTENCE" in ASK_BRAIN_CODE)
check("the audit was not loosened or tightened",
      aq.MODULE_VERSION == "phase14.2-1.0"
      and "def is_pack_absence_sentence(" in ASK_BRAIN_CODE
      and "def is_non_factual_sentence(" in ASK_BRAIN_CODE,
      aq.MODULE_VERSION)
check("confidence logic is untouched: the same six ceilings, all Low",
      [c[1] for ctx in (
          {"citation_audit": {"uncited_sentences": ["Margins fell sharply this year."]}},
          {"attribution_violations": ["The company reported X."]},
          {"unsupported_subject": True},
          {"recommendation_refused": True},
          {"generation_incomplete": True, "generation_incomplete_reasons": ["x"]},
          {"generation_status": "incomplete"})
       for c in ab.post_generation_ceilings(ctx)]
      == ["uncited_claims_in_direct_answer",
          "company_attribution_without_company_evidence",
          "subject_absent_from_evidence", "recommendation_refused",
          "generation_incomplete", "generation_incomplete"])
check("the evaluator is untouched by this phase",
      rv.PHASE == "phase14.3.3-1.0"
      and '"Observed changes:"' in REVIEW_SRC
      and "def answer_body(" in REVIEW_SRC
      and "[:2600]" not in REVIEW_SRC,
      f"{rv.PHASE} - this phase changed no evaluator logic, so its version did not move")
check("the Phase 14.3.3 completeness guard is untouched",
      ab.PROSE_SECTIONS == ("Direct Answer", "Evidence Limitations", "Needs Human Review")
      and ab.generation_completeness(
          _answer_sections("Risk one is capacity [1]. Taken together,"))["incomplete"]
      and not ab.generation_completeness(_COMPLETE)["incomplete"])
check("retrieval, ranking and selection are untouched",
      (ab.W_VECTOR, ab.W_KEYWORD, ab.W_PRIORITY, ab.W_RECENCY) == (0.65, 0.25, 0.10, 0.10)
      and (ab.PRIORITY_BONUS, ab.PRIORITY_PENALTY, ab.DOC_REPEAT_PENALTY,
           ab.SOURCE_REPEAT_PENALTY) == (0.06, 0.03, 0.05, 0.03)
      and ab.WEAK_EVIDENCE_SIMILARITY == 0.25
      and ab.RETRIEVAL_MODES == ("vector", "keyword", "hybrid"))
check("nothing was written to any index or corpus",
      "INSERT INTO rag_chunks" not in ASK_BRAIN_CODE and "collection.add(" not in ASK_BRAIN_CODE)
check("no truncation or rewriting was introduced",
      not re.search(r"(?:answer|direct_answer|answer_text)\s*=\s*\w*answer\w*\[\s*:\s*\d+",
                    ASK_BRAIN_CODE)
      and "textwrap.shorten" not in ASK_BRAIN_CODE)
check("the pipeline version is at or past Phase 14.3.4",
      ab.PIPELINE_VERSION >= "phase14.3.4" and ab.PIPELINE_VERSION == ui.PHASE,
      f"{ab.PIPELINE_VERSION} / {ui.PHASE}")

# ===========================================================================
print("\n=== 16. PHASE 14.3.5: ONE BOUNDED COMPLIANCE RETRY ===")
#
# After the 14.3.4 live run, two defects were left, and both survived three
# phases of prompt wording:
#
#   MSFT risk_or_credit  opened "Research identifies two principal risks for
#                        Microsoft around AI investment and cloud capacity." -
#                        uncited, one run after group E was changed to say in
#                        as many words that a takeaway is not an exception.
#   AMZN analyst_concern "...consume cash indefinitely [1][5]. [1][5]" - a
#                        citation marker standing alone, citing nothing. The
#                        audit never saw it: at nine characters it is far below
#                        the length at which a fragment counts as a claim.
#
# So this phase stops writing prose and adds a deterministic recovery path. It
# is bounded by arithmetic, not by good intentions: AnswerCallBudget gives one
# question a hard ceiling of generative calls that the length retry and the
# compliance retry both spend from, so neither can feed the other.

print("\n  -- a citation marker standing on its own --")
for label, text, expected in (
        ("the live AMZN tail",
         "Analysts flag funding uncertainty, and the business may consume cash "
         "indefinitely [1][5]. [1][5]", True),
        ("a marker alone on its own line", "Margins compressed [2].\n[2][5]", True),
        ("a marker alone as a bullet", "- Margins compressed [2].\n- [2][5]", True),
        ("a marker alone with its own full stop", "Margins compressed [2]. [5].", True),
        ("a normal cited sentence", "Azure grew 39% in the December quarter [1].", False),
        ("several markers inside one sentence",
         "Three notes name depreciation as the main risk [2][3][5].", False),
        ("a sentence ending in several markers and a stop",
         "...near-term revenue, margin and execution risks [6][5][7].", False),
        ("a quoted figure followed by its marker",
         'One check reports "there is no capacity" in some regions [1].', False),
):
    found = ab.citation_only_fragments(text)
    check(f"{'caught' if expected else 'not caught'}: {label}",
          bool(found) == expected, str(found))

check("the detector reads letters and digits, not a list of shapes",
      ab.citation_only_fragments("[11][207]") and not ab.citation_only_fragments("Q4 [1]."),
      "strip the markers; if no letter and no digit is left, the fragment carries no claim")
check("a stray marker makes the completeness guard fail",
      ab.generation_completeness(_answer_sections(
          "Margins compressed [2]. [2][5]."))["incomplete"],
      "it is not an unfinished sentence, but it is an unfinished answer")
check("and the guard names it as a citation-only fragment, not as a cut generation",
      any("citation-only" in r for r in ab.generation_completeness(_answer_sections(
          "Margins compressed [2]. [2][5]."))["reasons"]))
check("audit_answer exposes it for the compliance pass to read",
      ab.audit_answer(_answer_sections("Margins compressed [2]. [2][5]."),
                      [])["citation_only_fragments"] == ["[2][5]."])
check("the deterministic refusal and unsupported answers carry no stray markers",
      not ab.citation_only_fragments(ab.parse_sections(ab.build_refusal_answer(
          SELECTED_FIXTURE, {"is_request": True, "reason": "asks"}, "MSFT"))["Direct Answer"])
      and not ab.citation_only_fragments(ab.parse_sections(
          ab.build_unsupported_subject_answer(
              {"missing": ["fiscal 1998"], "present": [], "terms": ["fiscal 1998"]},
              SELECTED_FIXTURE, "MSFT", "company_update"))["Direct Answer"]))

print("\n  -- what does and does not warrant a repair --")
_UNCITED_OPENER = ("Research identifies two principal risks for Microsoft around AI investment "
                   "and cloud capacity. First, capacity constraints limit Azure upside [1][6]. "
                   "Second, large AI commitments raise return questions [2][5].")
_CITED_OPENER = ("Research identifies two principal risks for Microsoft around AI investment "
                 "and cloud capacity [1][2]. Capacity constraints limit Azure upside [1][6]. "
                 "Large AI commitments raise return questions [2][5].")
_STRAY_TAIL = ("Heavy AI capex has hit free cash flow and raised funding questions [2][5]. "
               "Analysts see no long-term funding plan, which could consume cash "
               "indefinitely [1][5]. [1][5]")
_CLEAN = ("Azure grew 39% in the December quarter, and capacity is the binding constraint [1]. "
          "Several analyst notes name depreciation as the main near-term margin risk [3][4].")

for label, direct, expected in (
        ("an uncited opening takeaway", _UNCITED_OPENER, True),
        ("the same takeaway, cited", _CITED_OPENER, False),
        ("a standalone citation-only tail", _STRAY_TAIL, True),
        ("a clean answer", _CLEAN, False),
):
    failures = ab.compliance_failures(ab.audit_answer(_answer_sections(direct), []))
    check(f"{'warrants a repair' if expected else 'warrants none'}: {label}",
          bool(failures) == expected, str(failures))

check("the repair pass covers exactly the two repairable defects",
      len(ab.compliance_failures(ab.audit_answer(
          _answer_sections(_UNCITED_OPENER + " [9]"), []))) == 2,
      "an uncited claim and a stray marker, and nothing else")
check("a missing section does NOT trigger a repair pass",
      not ab.compliance_failures(ab.audit_answer(
          _answer_sections(_CLEAN, drop=("Needs Human Review",)), [])),
      "that is not repairable by reformatting, and the ceilings already handle it")
check("a cut generation does NOT trigger a repair pass",
      not ab.compliance_failures(ab.audit_answer(
          _answer_sections("Risk one is capacity [1]. Taken together,"), [])),
      "the length retry owns that; two paths for one defect is how a loop starts")

print("\n  -- the call budget is a hard ceiling, not a convention --")
check("the ceiling is three generative calls per question",
      ab.MAX_ANSWER_MODEL_CALLS == 3, str(ab.MAX_ANSWER_MODEL_CALLS))
_budget = ab.AnswerCallBudget()
check("the budget counts down", [_budget.spend("a"), _budget.spend("b"),
                                 _budget.spend("c")] == [1, 2, 3]
      and _budget.remaining == 0)
try:
    _budget.spend("d")
    _over = False
except RuntimeError:
    _over = True
check("and refuses the call after it, rather than making it",
      _over, "an exhausted budget raises; it never silently calls anyway")
check("it records what each call was spent on",
      _budget.spent_on == ["a", "b", "c"], str(_budget.spent_on))
check("the transport retries are a different thing and are themselves bounded",
      ab.MAX_RETRIES == 4 and "calls.spend(why)" in ASK_BRAIN_CODE,
      "one logical call spends once, however many times the request has to be posted, "
      "so the ceiling on HTTP requests is MAX_ANSWER_MODEL_CALLS * MAX_RETRIES")

print("\n  -- one repair call, with the same question and the same evidence --")
_REPAIR_SETTINGS = {"answer_model": "gpt-5-mini", "reasoning_effort": "low",
                    "max_output_tokens": 6000, "answer_style": "quick"}
_REPAIR_INPUT = "QUESTION: What risks?\n\nEVIDENCE:\n[1] chunk one\n[5] chunk five"
_bad = _answer_sections(_UNCITED_OPENER)
_good = _answer_sections(_CITED_OPENER)

_calls = ab.AnswerCallBudget()
_calls.spend("answer")                       # the original answer, already made
_client = _StubClient(_StubResponse(_good))
_repair = ab.compliance_pass(_client, dict(_REPAIR_SETTINGS), _REPAIR_INPUT, _bad,
                             ab.audit_answer(_bad, []), _calls)
check("an uncited opening takeaway spends exactly ONE more call",
      _repair["used"] and len(_client.calls) == 1 and _calls.used == 2,
      f'{len(_client.calls)} call(s), budget {_calls.used}/{_calls.limit}')
check("the repaired answer REPLACES the first and is not stitched onto it",
      _repair["text"] == _good.strip()
      and "First, capacity constraints" not in _repair["text"],
      "two answers concatenated is not an answer")
check("the repair is recorded with the reason it was needed",
      _repair["reason"].startswith("repaired: ")
      and "carry no citation" in _repair["reason"])
check("the retry sends the SAME question and the SAME evidence, with nothing removed",
      _client.calls[0]["input"].startswith(_REPAIR_INPUT),
      "the pack is the prefix of the repair input, byte for byte")
check("and nothing added to the pack: only the previous answer, to be repaired",
      _client.calls[0]["input"] == _REPAIR_INPUT + ab.REPAIR_INPUT_HEADER + _bad
      and _client.calls[0]["input"].count("chunk one") == 1
      and _client.calls[0]["input"].count("[5] chunk five") == 1)
check("the SAME answer style is sent, once",
      ab.ANSWER_STYLES["quick"] in _client.calls[0]["instructions"]
      and _client.calls[0]["instructions"].count("LENGTH AND DEPTH FOR THIS ANSWER") == 1)
check("the base prompt is sent unchanged, with the repair pass appended",
      _client.calls[0]["instructions"].startswith(ab.SYSTEM_INSTRUCTION)
      and "=== G. REPAIR PASS (this call only) ===" in _client.calls[0]["instructions"])
check("the repair pass is NOT part of the base prompt",
      "REPAIR PASS" not in PROMPT and "REPAIR PASS" not in ab.ANSWER_STYLES["quick"],
      "a normal answer is written against exactly the prompt 14.3.4 left behind")
check("the repair pass names the defect it must fix",
      "carry no citation" in _client.calls[0]["instructions"])
check("it forbids changing the substance",
      all(phrase in ab.COMPLIANCE_REPAIR_INSTRUCTION for phrase in (
          "must not change the substance", "the same length or shorter",
          "No new research, no new points, no new sources")))
check("it requires the opening sentence to carry its own citations",
      "the OPENING sentence included" in ab.COMPLIANCE_REPAIR_INSTRUCTION
      and "inside that same sentence" in ab.COMPLIANCE_REPAIR_INSTRUCTION)
check("it prohibits a standalone citation marker",
      "cites nothing and must not appear anywhere" in ab.COMPLIANCE_REPAIR_INSTRUCTION)
check("and it keeps the quick ceiling",
      "section 1 at or under 220 words" in ab.COMPLIANCE_REPAIR_INSTRUCTION
      and "all six numbered" in ab.COMPLIANCE_REPAIR_INSTRUCTION)

_calls = ab.AnswerCallBudget(); _calls.spend("answer")
_client = _StubClient(_StubResponse(_answer_sections(
    "Heavy AI capex has hit free cash flow [2][5]. Analysts see no funding plan [1][5].")))
_repair = ab.compliance_pass(_client, dict(_REPAIR_SETTINGS), _REPAIR_INPUT,
                             _answer_sections(_STRAY_TAIL),
                             ab.audit_answer(_answer_sections(_STRAY_TAIL), []), _calls)
check("a standalone citation tail also spends exactly ONE call",
      _repair["used"] and len(_client.calls) == 1)
check("and the repaired answer has no stray marker left",
      not ab.compliance_failures(ab.audit_answer(_repair["text"], [])))

for label, direct in (("a clean answer", _CLEAN), ("a cited takeaway", _CITED_OPENER)):
    _calls = ab.AnswerCallBudget(); _calls.spend("answer")
    _client = _StubClient(_StubResponse(_good))
    _repair = ab.compliance_pass(_client, dict(_REPAIR_SETTINGS), _REPAIR_INPUT,
                                 _answer_sections(direct),
                                 ab.audit_answer(_answer_sections(direct), []), _calls)
    check(f"no repair call is made for {label}",
          not _repair["used"] and _client.calls == [] and _calls.used == 1,
          f'{len(_client.calls)} call(s)')
    check(f"  and nothing is recorded as a repair ({label})", _repair["reason"] == "")

print("\n  -- a repair that does not work is kept, flagged and capped --")
_calls = ab.AnswerCallBudget(); _calls.spend("answer")
_still_bad = _answer_sections(
    "Research identifies two principal risks for Microsoft. Capacity limits Azure [1][6].")
_client = _StubClient(_StubResponse(_still_bad))
_repair = ab.compliance_pass(_client, dict(_REPAIR_SETTINGS), _REPAIR_INPUT, _bad,
                             ab.audit_answer(_bad, []), _calls)
_after = ab.compliance_failures(ab.audit_answer(_repair["text"], []))
check("the repaired text is kept even when it is still wrong",
      _repair["used"] and _repair["text"] == _still_bad.strip())
check("the remaining defect is still detected",
      bool(_after), str(_after))
check("and it still caps confidence to Low, exactly as before this phase",
      any(c[0] == "Low" and c[1] == "uncited_claims_in_direct_answer"
          for c in ab.post_generation_ceilings(
              {"citation_audit": ab.audit_answer(_repair["text"], [])["citation_audit"]})))
check("there is NO second compliance retry",
      len(_client.calls) == 1 and _calls.used == 2,
      "compliance_pass is called once from one place and never loops")
check("the wiring calls it once, outside any loop",
      ASK_BRAIN_CODE.count("compliance_pass(") == 2
      and not re.search(r"(?:while|for)[^\n]*\n(?:[^\n]*\n){0,6}?[^\n]*compliance_pass\(",
                        ASK_BRAIN_CODE.split("def compliance_pass(")[-1]),
      "one definition and one call site")

print("\n  -- the two retries cannot feed each other --")
_calls = ab.AnswerCallBudget()
_client = _StubClient(_StubResponse(_bad, "incomplete", "max_output_tokens"),
                      _StubResponse(_bad), _StubResponse(_good), _StubResponse(_good))
_result = ab.call_answer_model(_client, dict(_REPAIR_SETTINGS), _REPAIR_INPUT, _calls)
check("the answer plus a length retry spends two of the three calls",
      _calls.used == 2 and _result["retry_used"], f"{_calls.used}/{_calls.limit}")
_repair = ab.compliance_pass(_client, dict(_REPAIR_SETTINGS), _REPAIR_INPUT, _result["text"],
                             ab.audit_answer(_result["text"], []), _calls)
check("the compliance retry then spends the third, and the budget is empty",
      _repair["used"] and _calls.used == 3 and _calls.remaining == 0,
      f"{_calls.used}/{_calls.limit}")
check("the worst legal sequence is exactly three generative calls",
      len(_client.calls) == 3, f"{len(_client.calls)} call(s)")

_calls = ab.AnswerCallBudget(limit=1); _calls.spend("answer")
_client = _StubClient(_StubResponse(_good))
_repair = ab.compliance_pass(_client, dict(_REPAIR_SETTINGS), _REPAIR_INPUT, _bad,
                             ab.audit_answer(_bad, []), _calls)
check("with no call left, no repair is attempted and nothing raises",
      not _repair["used"] and _client.calls == [])
check("and the reason says why it was not attempted",
      "call budget" in _repair["reason"] and "needed:" in _repair["reason"],
      _repair["reason"][:80])

_calls = ab.AnswerCallBudget(limit=1)
_client = _StubClient(_StubResponse(_bad, "incomplete", "max_output_tokens"))
_result = ab.call_answer_model(_client, dict(_REPAIR_SETTINGS), _REPAIR_INPUT, _calls)
check("an incomplete answer with no call left is kept, not retried",
      len(_client.calls) == 1 and not _result["retry_used"]
      and _result["status"] == "incomplete")
check("and the status still caps it to Low",
      any(c[0] == "Low" and c[1] == "generation_incomplete"
          for c in ab.post_generation_ceilings({"generation_status": _result["status"]})))

print("\n  -- no retrieval anywhere in the retry path --")
_RETRY_PATH = "".join(
    ASK_BRAIN_CODE.split(f"def {name}(")[-1].split("\ndef ")[0]
    for name in ("compliance_pass", "compliance_failures", "call_answer_model",
                 "_responses_call"))
check("no retrieval, ranking or embedding call is reachable from the retry path",
      not re.search(r"(?:filter_chunks|rank_candidates|select_chunks|embed_query|"
                    r"open_collection|query_collection|collection\.|build_user_input)\(",
                    _RETRY_PATH),
      "a retry that re-retrieved would be answering a different question")
check("the repair gets its evidence only from the input it was handed",
      "user_input + REPAIR_INPUT_HEADER + answer_text" in ASK_BRAIN_CODE)

print("\n  -- the record --")
check("the run row carries the compliance fields",
      all(c in ab.RUN_COLUMNS for c in ("compliance_retry_used", "compliance_retry_reason",
                                        "answer_model_calls")))
check("and the Phase 14.3.3 generation fields survive",
      all(c in ab.RUN_COLUMNS for c in ("generation_status", "generation_incomplete",
                                        "generation_incomplete_reason",
                                        "answer_max_output_tokens", "answer_retry_used")))
check("the reason records both the defect and what survived the repair",
      "after the repair: " in ASK_BRAIN_CODE and "compliance_retry_did_not_fix_it" in
      ASK_BRAIN_CODE,
      "a repair that made things worse has to be visible on the row, not inferred")
check("the answer file says a repair happened",
      "compliance repair: ONE retry, same question and same evidence" in ASK_BRAIN_SRC)

print("\n  -- Phase 14.3.5 changed nothing it was told not to --")
check("no prompt prose was added: the base prompt is byte-identical to 14.3.4",
      len(PROMPT.split()) == PROMPT_WORDS and "A takeaway is NOT an exception" in PROMPT,
      f"{len(PROMPT.split())} words, expected {PROMPT_WORDS}")
check("the quick hierarchy is untouched",
      len(ab.ANSWER_STYLES["quick"].split()) == QUICK_WORDS_1432
      and "AT MOST TWO supporting points" in ab.ANSWER_STYLES["quick"])
check("the deep style is untouched",
      len(ab.ANSWER_STYLES["deep"].split()) == 132
      and "may run to about 400 words" in ab.ANSWER_STYLES["deep"])
check("answer_quality definitions are untouched",
      aq.MODULE_VERSION == "phase14.2-1.0"
      and aq.QUICK_WORD_BAND == (100, 220) and aq.REPETITION_OVERLAP == 0.6
      and aq.RE_SENTENCE.pattern == ab.RE_SENTENCE.pattern, aq.MODULE_VERSION)
check("the confidence ceilings are the same six, in the same order",
      [c[1] for ctx in (
          {"citation_audit": {"uncited_sentences": ["Margins fell sharply this year."]}},
          {"attribution_violations": ["The company reported X."]},
          {"unsupported_subject": True},
          {"recommendation_refused": True},
          {"generation_incomplete": True, "generation_incomplete_reasons": ["x"]},
          {"generation_status": "incomplete"})
       for c in ab.post_generation_ceilings(ctx)]
      == ["uncited_claims_in_direct_answer",
          "company_attribution_without_company_evidence",
          "subject_absent_from_evidence", "recommendation_refused",
          "generation_incomplete", "generation_incomplete"],
      "no ceiling was added for the repair: a repaired answer is judged by the same rules")
check("unsupported and refusal logic are untouched, and skip the repair pass",
      "def build_refusal_answer(" in ASK_BRAIN_CODE
      and "def build_unsupported_subject_answer(" in ASK_BRAIN_CODE
      and 'if context["answer_generated"]:' in ASK_BRAIN_CODE,
      "a script-written answer cites nothing by design; there is nothing to repair")
check("retrieval, ranking and selection are untouched",
      (ab.W_VECTOR, ab.W_KEYWORD, ab.W_PRIORITY, ab.W_RECENCY) == (0.65, 0.25, 0.10, 0.10)
      and (ab.PRIORITY_BONUS, ab.PRIORITY_PENALTY, ab.DOC_REPEAT_PENALTY,
           ab.SOURCE_REPEAT_PENALTY) == (0.06, 0.03, 0.05, 0.03)
      and ab.WEAK_EVIDENCE_SIMILARITY == 0.25
      and ab.RETRIEVAL_MODES == ("vector", "keyword", "hybrid"))
check("nothing was written to any index or corpus",
      "INSERT INTO rag_chunks" not in ASK_BRAIN_CODE and "collection.add(" not in ASK_BRAIN_CODE)
check("no answer is rewritten by this script: the repair is the model's own text",
      not re.search(r"(?:answer|direct_answer|answer_text)\s*=\s*\w*answer\w*\[\s*:\s*\d+",
                    ASK_BRAIN_CODE)
      and "textwrap.shorten" not in ASK_BRAIN_CODE
      and "exactly as the model wrote it" in ASK_BRAIN_SRC,
      "the repair replaces one model answer with another model answer")
check("the evaluator was not touched by this phase",
      rv.PHASE == "phase14.3.3-1.0" and '"Observed changes:"' in REVIEW_SRC
      and "def answer_body(" in REVIEW_SRC)
check("the pipeline version is at or past Phase 14.3.5",
      ab.PIPELINE_VERSION >= "phase14.3.5" and ab.PIPELINE_VERSION == ui.PHASE,
      f"{ab.PIPELINE_VERSION} / {ui.PHASE}")

# ===========================================================================
print("\n=== 17. PHASE 14.3.6: THE QUICK CEILING, AS A THIRD TRIGGER ===")
#
# The full 30-question Phase 14.3.5 run came back clean on everything the last
# four phases were about:
#
#     uncited_claims_total      0        synthesis_uncited        0
#     generation_incomplete     0        source sets identical    30/30
#     mean_words_direct         85.03    direct_over_220          1
#
# with one ceiling violation left: AMZN risk_or_credit at 238 Direct Answer
# words. Worth recording what that run also showed - the Phase 14.3.5
# compliance retry fired on ZERO of the thirty answers. The grounding result
# was won by the 14.3.4 prompt fix; the retry was a net that never had to
# catch anything. So this phase does not build a second net. It adds the one
# remaining defect to the net that is already there.

print("\n  -- one number, counted one way --")
check("the ceiling this script acts on is the band the rubric reports against",
      ab.QUICK_DIRECT_WORD_CEILING == aq.QUICK_WORD_BAND[1] == 220,
      f"{ab.QUICK_DIRECT_WORD_CEILING} vs {aq.QUICK_WORD_BAND}")
check("and the words are counted with the rubric's own pattern, character for character",
      ab.RE_ANSWER_WORD.pattern == aq.RE_WORD.pattern,
      "two ways of counting the words of one answer is two answers to 'how long is this'")
check("the counts agree on a real answer",
      ab.answer_word_count(aq.answer_body(FIXTURES["says_each_thing_once"]))
      == aq.measure(FIXTURES["says_each_thing_once"])["words_direct"]
      or ab.answer_word_count(aq.parse_sections(
          FIXTURES["says_each_thing_once"])["Direct Answer"])
      == aq.measure(FIXTURES["says_each_thing_once"])["words_direct"])
check("the quick style still states the same number to the model",
      "220 words is a maximum, not a target" in ab.ANSWER_STYLES["quick"],
      "the trigger enforces what the instruction already asks for")
check("ask_brain's question tokeniser is a different thing and was not touched",
      ab.RE_WORD.pattern == r"[a-z0-9][a-z0-9&.\-]*",
      "RE_WORD tokenises a QUESTION; RE_ANSWER_WORD counts an ANSWER")


def _of_length(n: int) -> str:
    """A six-section answer whose Direct Answer is exactly n words."""
    body = " ".join(["word"] * (n - 1)) + " [1]."
    return _answer_sections(body)


print("\n  -- the boundary, exactly --")
for words, style, expected in (
        (219, "quick", False),
        (220, "quick", False),
        (221, "quick", True),
        (238, "quick", True),        # the live AMZN risk_or_credit length
        (400, "quick", True),
        (221, "deep", False),
        (238, "deep", False),
        (420, "deep", False),
):
    answer = _of_length(words)
    failures = ab.compliance_failures(ab.audit_answer(answer, []), style)
    length = [f for f in failures if f.startswith("the Direct Answer runs")]
    check(f"{words} words, {style}: {'one length failure' if expected else 'none'}",
          bool(length) == expected and len(length) <= 1, str(failures))

check("the Direct Answer really is the length the fixture claims",
      ab.answer_word_count(ab.parse_sections(_of_length(238))["Direct Answer"]) == 238)
check("a deep answer is never over-length here, by design",
      not ab.compliance_failures(ab.audit_answer(_of_length(420), []), "deep"),
      "220 is the quick ceiling; deep has its own and much larger room")
check("an unknown style is judged as quick, exactly as it is WRITTEN as quick",
      ab.resolved_answer_style("nonsense") == "quick"
      and ab.resolved_answer_style("") == "quick"
      and ab.resolved_answer_style("deep") == "deep"
      and bool(ab.compliance_failures(ab.audit_answer(_of_length(238), []), "nonsense")),
      "answer_instructions falls back to quick, so the ceiling must too")
check("the failure names the count, the ceiling and how to get under it",
      all(phrase in ab.compliance_failures(ab.audit_answer(_of_length(238), []), "quick")[0]
          for phrase in ("238 words", "maximum of 220", "removing redundancy and excess detail",
                         "not by dropping a point, a risk category or a citation")))
check("the lower bound is NOT enforced: a short answer triggers nothing",
      not ab.compliance_failures(ab.audit_answer(_of_length(30), []), "quick")
      and not ab.compliance_failures(ab.audit_answer(_of_length(76), []), "quick"),
      "76-word answers are correct answers; nothing may push one upward")

print("\n  -- it is the same retry, not a second one --")
_L_SETTINGS = {"answer_model": "gpt-5-mini", "reasoning_effort": "low",
               "max_output_tokens": 6000, "answer_style": "quick"}
_L_INPUT = "QUESTION: What risks?\n\nEVIDENCE:\n[1] chunk one\n[5] chunk five"
_LONG = _of_length(238)
_SHORT = _of_length(150)

_calls = ab.AnswerCallBudget(); _calls.spend("answer")
_client = _StubClient(_StubResponse(_SHORT))
_repair = ab.compliance_pass(_client, dict(_L_SETTINGS), _L_INPUT, _LONG,
                             ab.audit_answer(_LONG, []), _calls)
check("a 238-word quick answer spends exactly ONE more call",
      _repair["used"] and len(_client.calls) == 1 and _calls.used == 2,
      f'{len(_client.calls)} call(s), budget {_calls.used}/{_calls.limit}')
check("the shortened answer REPLACES the long one, not appended to it",
      _repair["text"] == _SHORT.strip()
      and ab.answer_word_count(ab.parse_sections(_repair["text"])["Direct Answer"]) == 150)
check("and it is now inside the ceiling",
      not ab.compliance_failures(ab.audit_answer(_repair["text"], []), "quick"))
check("the retry sends the SAME question and the SAME evidence, nothing removed",
      _client.calls[0]["input"].startswith(_L_INPUT)
      and _client.calls[0]["input"] == _L_INPUT + ab.REPAIR_INPUT_HEADER + _LONG)
check("the SAME answer style is sent, once",
      ab.ANSWER_STYLES["quick"] in _client.calls[0]["instructions"]
      and _client.calls[0]["instructions"].count("LENGTH AND DEPTH FOR THIS ANSWER") == 1
      and ab.ANSWER_STYLES["deep"] not in _client.calls[0]["instructions"])
check("the base prompt is sent unchanged",
      _client.calls[0]["instructions"].startswith(ab.SYSTEM_INSTRUCTION))
check("the repair pass tells it what to keep and what to cut",
      all(phrase in ab.COMPLIANCE_REPAIR_INSTRUCTION for phrase in (
          "LENGTH repair", "Keep every DISTINCT category the question asked",
          "Within that, cut freely")))
check("and not to pad upward to a word count",
      "Do not pad upward" in ab.COMPLIANCE_REPAIR_INSTRUCTION
      and "that is a better answer, not a worse one" in ab.COMPLIANCE_REPAIR_INSTRUCTION)
check("the repair pass is still sent only on a retry, never in the base prompt",
      "LENGTH repair" not in PROMPT and "LENGTH repair" not in ab.ANSWER_STYLES["quick"]
      and "LENGTH repair" not in ab.ANSWER_STYLES["deep"])

for label, direct_words in (("exactly 220", 220), ("well below", 150)):
    _calls = ab.AnswerCallBudget(); _calls.spend("answer")
    _client = _StubClient(_StubResponse(_SHORT))
    _answer = _of_length(direct_words)
    _repair = ab.compliance_pass(_client, dict(_L_SETTINGS), _L_INPUT, _answer,
                                 ab.audit_answer(_answer, []), _calls)
    check(f"no repair call for a quick answer {label}",
          not _repair["used"] and _client.calls == [] and _calls.used == 1,
          f'{len(_client.calls)} call(s)')

_calls = ab.AnswerCallBudget(); _calls.spend("answer")
_client = _StubClient(_StubResponse(_of_length(420)))
_deep = dict(_L_SETTINGS, answer_style="deep")
_repair = ab.compliance_pass(_client, _deep, _L_INPUT, _of_length(420),
                             ab.audit_answer(_of_length(420), []), _calls)
check("no repair call for a 420-word DEEP answer",
      not _repair["used"] and _client.calls == [] and _calls.used == 1,
      "deep behaviour is untouched by this phase")

print("\n  -- a repair that is still too long is kept, once --")
_calls = ab.AnswerCallBudget(); _calls.spend("answer")
_STILL_LONG = _of_length(231)
_client = _StubClient(_StubResponse(_STILL_LONG))
_repair = ab.compliance_pass(_client, dict(_L_SETTINGS), _L_INPUT, _LONG,
                             ab.audit_answer(_LONG, []), _calls)
_remaining = ab.compliance_failures(ab.audit_answer(_repair["text"], []), "quick")
check("the still-long repair is kept",
      _repair["used"] and _repair["text"] == _STILL_LONG.strip())
check("the surviving failure is recorded with its new count",
      len(_remaining) == 1 and "231 words" in _remaining[0], str(_remaining))
check("there is NO second compliance retry",
      len(_client.calls) == 1 and _calls.used == 2,
      "one repair per question, whatever the repair did")
check("the wiring records what survived the repair",
      "after the repair: " in ASK_BRAIN_CODE
      and "compliance_retry_did_not_fix_it" in ASK_BRAIN_CODE)
check("an over-long answer is NOT cut by this script",
      not re.search(r"(?:answer|direct_answer|answer_text)\s*=\s*\w*answer\w*\[\s*:\s*\d+",
                    ASK_BRAIN_CODE)
      and "textwrap.shorten" not in ASK_BRAIN_CODE,
      "the repair is the model's own shorter answer, never a truncation of the long one")

print("\n  -- the ceiling on calls did not move --")
check("still three generative calls per question",
      ab.MAX_ANSWER_MODEL_CALLS == 3, str(ab.MAX_ANSWER_MODEL_CALLS))
_calls = ab.AnswerCallBudget()
_client = _StubClient(_StubResponse(_LONG, "incomplete", "max_output_tokens"),
                      _StubResponse(_LONG), _StubResponse(_SHORT), _StubResponse(_SHORT))
_result = ab.call_answer_model(_client, dict(_L_SETTINGS), _L_INPUT, _calls)
_repair = ab.compliance_pass(_client, dict(_L_SETTINGS), _L_INPUT, _result["text"],
                             ab.audit_answer(_result["text"], []), _calls)
check("a length retry plus a length repair is exactly three calls, and the budget is empty",
      len(_client.calls) == 3 and _calls.used == 3 and _calls.remaining == 0,
      f"{len(_client.calls)} call(s), {_calls.used}/{_calls.limit}")
check("and the third trigger cannot add a fourth",
      ab.compliance_failures(ab.audit_answer(_repair["text"], []), "quick") == []
      or _calls.remaining == 0)
_calls = ab.AnswerCallBudget(limit=1); _calls.spend("answer")
_client = _StubClient(_StubResponse(_SHORT))
_repair = ab.compliance_pass(_client, dict(_L_SETTINGS), _L_INPUT, _LONG,
                             ab.audit_answer(_LONG, []), _calls)
check("with no call left, an over-long answer is kept and nothing raises",
      not _repair["used"] and _client.calls == [] and "call budget" in _repair["reason"])

print("\n  -- no retrieval on a length repair either --")
_RETRY_PATH_1436 = "".join(
    ASK_BRAIN_CODE.split(f"def {name}(")[-1].split("\ndef ")[0]
    for name in ("compliance_pass", "compliance_failures", "answer_word_count",
                 "resolved_answer_style", "call_answer_model", "_responses_call"))
check("nothing in the retry path can retrieve, rank or embed",
      not re.search(r"(?:filter_chunks|rank_candidates|select_chunks|embed_query|"
                    r"open_collection|query_collection|collection\.|build_user_input)\(",
                    _RETRY_PATH_1436))
check("the length trigger reads the answer it was handed, and nothing else",
      'audit.get("sections") or {}).get("Direct Answer")' in ASK_BRAIN_SRC)

print("\n  -- the three triggers, and only three --")
check("all three are produced by ONE function",
      ASK_BRAIN_CODE.count("def compliance_failures(") == 1)
check("and consumed by ONE retry",
      ASK_BRAIN_CODE.count("def compliance_pass(") == 1
      and ASK_BRAIN_CODE.count("compliance_pass(") == 2)
# One answer with all three defects at once: an uncited opening takeaway, a
# stray marker, and 258 Direct Answer words. Three failures, ONE retry.
_ALL_THREE = _answer_sections(
    "Research identifies three principal credit risks for Amazon around AI infrastructure "
    "spending and free cash flow. "
    + " ".join(f"Risk {n} is described in the retrieved notes and carries its own citation "
               f"marker [{n % 5 + 1}]." for n in range(1, 17))
    + " [2][5]")
_f = ab.compliance_failures(ab.audit_answer(_ALL_THREE, []), "quick")
check("an answer with all three defects reports all three",
      len(_f) == 3, str(len(_f)) + " failure(s): " + " | ".join(x[:46] for x in _f))
_calls = ab.AnswerCallBudget(); _calls.spend("answer")
_client = _StubClient(_StubResponse(_SHORT))
_repair = ab.compliance_pass(_client, dict(_L_SETTINGS), _L_INPUT, _ALL_THREE,
                             ab.audit_answer(_ALL_THREE, []), _calls)
check("and three defects still buy exactly ONE retry, which names all three",
      _repair["used"] and len(_client.calls) == 1 and _calls.used == 2
      and all(p in _client.calls[0]["instructions"]
              for p in ("carry no citation", "stand on their own", "runs 258 words")),
      f'{len(_client.calls)} call(s)')
check("a missing section still triggers no repair",
      not ab.compliance_failures(ab.audit_answer(
          _of_length(150), []), "quick")
      and not ab.compliance_failures(ab.audit_answer(
          _answer_sections("Azure grew 39% [1].", drop=("Confidence",)), []), "quick"))
check("a cut generation still triggers no repair",
      not ab.compliance_failures(ab.audit_answer(
          _answer_sections("Risk one is capacity [1]. Taken together,"), []), "quick"))

print("\n  -- Phase 14.3.6 changed nothing it was told not to --")
check("the base prompt is byte-identical to 14.3.4",
      len(PROMPT.split()) == PROMPT_WORDS and "A takeaway is NOT an exception" in PROMPT,
      f"{len(PROMPT.split())} words, expected {PROMPT_WORDS}")
check("the quick hierarchy is untouched",
      len(ab.ANSWER_STYLES["quick"].split()) == QUICK_WORDS_1432)
check("the deep style is untouched",
      len(ab.ANSWER_STYLES["deep"].split()) == 132
      and "may run to about 400 words" in ab.ANSWER_STYLES["deep"])
check("answer_quality definitions are untouched",
      aq.MODULE_VERSION == "phase14.2-1.0" and aq.QUICK_WORD_BAND == (100, 220)
      and aq.DEEP_WORD_BAND == (150, 420) and aq.REPETITION_OVERLAP == 0.6
      and aq.RE_SENTENCE.pattern == ab.RE_SENTENCE.pattern, aq.MODULE_VERSION)
check("the confidence ceilings are the same six, in the same order",
      [c[1] for ctx in (
          {"citation_audit": {"uncited_sentences": ["Margins fell sharply this year."]}},
          {"attribution_violations": ["The company reported X."]},
          {"unsupported_subject": True},
          {"recommendation_refused": True},
          {"generation_incomplete": True, "generation_incomplete_reasons": ["x"]},
          {"generation_status": "incomplete"})
       for c in ab.post_generation_ceilings(ctx)]
      == ["uncited_claims_in_direct_answer",
          "company_attribution_without_company_evidence",
          "subject_absent_from_evidence", "recommendation_refused",
          "generation_incomplete", "generation_incomplete"],
      "no ceiling was added for being over-length: that is a style defect, not a grounding one")
check("unsupported and refusal logic are untouched and still skip the repair pass",
      "def build_refusal_answer(" in ASK_BRAIN_CODE
      and "def build_unsupported_subject_answer(" in ASK_BRAIN_CODE
      and 'if context["answer_generated"]:' in ASK_BRAIN_CODE)
check("an unsupported answer is never repaired for length",
      not ab.compliance_failures(ab.audit_answer(ab.build_unsupported_subject_answer(
          {"missing": ["fiscal 1998"], "present": [], "terms": ["fiscal 1998"]},
          SELECTED_FIXTURE, "MSFT", "company_update"), []), "quick"),
      "it is written by this script, and its length is this script's decision")
check("retrieval, ranking and selection are untouched",
      (ab.W_VECTOR, ab.W_KEYWORD, ab.W_PRIORITY, ab.W_RECENCY) == (0.65, 0.25, 0.10, 0.10)
      and (ab.PRIORITY_BONUS, ab.PRIORITY_PENALTY, ab.DOC_REPEAT_PENALTY,
           ab.SOURCE_REPEAT_PENALTY) == (0.06, 0.03, 0.05, 0.03)
      and ab.WEAK_EVIDENCE_SIMILARITY == 0.25
      and ab.RETRIEVAL_MODES == ("vector", "keyword", "hybrid"))
check("nothing was written to any index or corpus",
      "INSERT INTO rag_chunks" not in ASK_BRAIN_CODE and "collection.add(" not in ASK_BRAIN_CODE)
check("the evaluator was not touched by this phase",
      rv.PHASE == "phase14.3.3-1.0" and '"Observed changes:"' in REVIEW_SRC
      and "def answer_body(" in REVIEW_SRC and "[:2600]" not in REVIEW_SRC)
check("the pipeline version is at or past Phase 14.3.6",
      ab.PIPELINE_VERSION >= "phase14.3.6" and ab.PIPELINE_VERSION == ui.PHASE,
      f"{ab.PIPELINE_VERSION} / {ui.PHASE}")

# ===========================================================================
print("\n=== 18. PHASE 14.3.7: THE LENGTH REPAIR THAT ACTUALLY COMPRESSES ===")
#
# The 14.3.6 live run proved the wiring and disproved the wording:
#
#     AMZN risk_or_credit   initial 270 words -> repaired 249 words
#     compliance_retry_used yes    answer_model_calls 2
#     uncited claims 0   generation_incomplete 0   source set identical
#
# 21 words off a 50-word overshoot, because the instruction asked for every
# point, every risk, every qualification AND every citation marker to survive.
# Cutting words means cutting something, and that wording left nothing to cut.
#
# WHAT THIS SECTION CAN AND CANNOT PROVE. This phase changed prose, so these
# checks do two things: pin that the instruction states the contract, and show
# on fixtures that a conforming repair satisfies it while a non-conforming one
# is distinguishable. No test here can prove the model will comply - only the
# live run can, and that is the one number to read afterwards.

print("\n  -- the contract the instruction now states --")
for label, phrase in (
        ("it outranks 'the same points' for a length repair, so there is one rule, not two",
         'this paragraph takes precedence over "the same points" above'),
        ("and says why: cutting words means cutting something",
         "cutting words means cutting something"),
        ("every DISTINCT requested category survives",
         "Keep every DISTINCT category the question asked for"),
        ("named by example, so 'category' is not abstract",
         "capital expenditure, competition and regulation, all three are still there"),
        ("the core conclusion of each category survives",
         "keep the core conclusion of each one"),
        ("with enough citations for every claim left standing",
         "every factual claim left standing is supported"),
        ("a redundant example may go", "a redundant example"),
        ("a secondary number may go", "a secondary number"),
        ("a repeated qualification may go", "a qualification already made once"),
        ("a duplicate citation may go", "a duplicate citation"),
        ("the second and third source saying the same thing may go",
         "the second and third source saying what the first already said"),
        ("a re-listing closing synthesis may go",
         "a closing synthesis that only re-lists the categories above"),
        ("dropping a requested category is forbidden",
         "drop a category the question asked for"),
        ("introducing a new claim is forbidden",
         "introduce a claim that was not in the previous answer"),
        ("removing the only citation of a retained claim is forbidden",
         "remove the only citation supporting a claim you kept"),
        ("the target is 160 to 200 words", "Aim at 160 to 200 words for section 1"),
        ("with 220 still the absolute maximum", "220 the absolute maximum"),
        ("one paragraph or bullet per category",
         "one compact paragraph or one bullet per requested category"),
        ("and no padding upward", "Do not pad upward"),
):
    check(f"the repair pass says: {label}", phrase in ab.COMPLIANCE_REPAIR_INSTRUCTION)

check("grounding is defined as every RETAINED claim cited, not every marker kept",
      "grounding means every claim you keep still carries a citation, not that every marker "
      "from the previous answer survives" in ab.COMPLIANCE_REPAIR_INSTRUCTION,
      "this is the distinction the 14.3.6 wording collapsed")
check("the 14.3.6 wording that left nothing to cut is gone",
      "Keep every risk, category, qualification and citation marker that is there"
      not in ab.COMPLIANCE_REPAIR_INSTRUCTION)
check("it is still sent only on a retry, never in the base prompt",
      "LENGTH repair" not in PROMPT
      and "cut freely" not in PROMPT + ab.ANSWER_STYLES["quick"] + ab.ANSWER_STYLES["deep"])
check("the citation-repair half of the pass is untouched",
      all(p in ab.COMPLIANCE_REPAIR_INSTRUCTION for p in (
          "the OPENING sentence included", "inside that same sentence",
          "cites nothing and must not appear anywhere", "Keep all six numbered")))

# ---------------------------------------------------------------------------
# A three-category risk answer, before and after a conforming repair. The
# question is the live one: capex, competition and regulation.
# ---------------------------------------------------------------------------
CATEGORIES = {
    "capital expenditure": ("capex", "capital expenditure", "capital intensity"),
    "competition": ("competition", "competitive", "rivals"),
    "regulation": ("regulation", "regulatory", "antitrust"),
}


def categories_in(direct: str) -> set:
    low = (direct or "").lower()
    return {name for name, words in CATEGORIES.items() if any(w in low for w in words)}


def cited_claims(direct: str):
    """(claims, uncited claims) in a Direct Answer, by the shared audit."""
    units = aq.claim_units(direct)
    return len(units), [u for u in units if not aq.RE_CITATION.search(u)]


# 253 Direct Answer words, three categories, and four things a length repair
# is now allowed to remove: a worked example, a secondary number, a repeated
# qualification, and a closing synthesis that only re-lists the categories.
_LONG_3CAT = _answer_sections(
    "Three risks dominate the retrieved research on Amazon's AI build-out [1][2].\n\n"
    "- Capital expenditure: capex rose again and free cash flow fell to a use of $18 billion "
    "in the quarter, with annual free cash flow down from $33 billion to $8 billion, and AWS "
    "capital intensity above 100% of sales [2][5]. A second note puts quarterly borrowing at "
    "$53 billion and adds that the funding mix is not yet settled [2]. A third note repeats "
    "that capital intensity is the binding constraint on near-term cash generation [3]. For "
    "example, one note describes a single regional data-centre programme in detail, naming "
    "its phases and its expected commissioning window, as evidence of the scale involved "
    "[5].\n"
    "- Competition: competition for AI workloads is intensifying, with rivals pricing "
    "aggressively for inference capacity and bundling it with existing commitments [1][6]. "
    "Another note makes the same point about competitive pricing in almost the same terms "
    "[3]. A further note adds that competitive intensity may persist well beyond the current "
    "planning horizon [6].\n"
    "- Regulation: regulatory scrutiny of large AI investments is named as a risk, and one "
    "note flags antitrust review of cloud bundling as the specific mechanism [4]. The same "
    "regulatory point is repeated elsewhere in the pack, again without quantification [7].\n\n"
    "Taken together, the research identifies three risks: capital expenditure, competition "
    "and regulation, each of which bears on free cash flow in a different way, and all three "
    "are treated as material by the notes that raise them [1][2][3][4][5][6][7].")

# 166 words: all three categories, each with its core conclusion and its
# citations, and nothing in it that was not in the answer above.
_REPAIRED_3CAT = _answer_sections(
    "Three risks dominate the retrieved research on Amazon's AI build-out, and all three run "
    "through free cash flow [1][2].\n\n"
    "- Capital expenditure: capex rose again and free cash flow fell to a use of $18 billion "
    "in the quarter, with annual free cash flow down from $33 billion to $8 billion and AWS "
    "capital intensity above 100% of sales, which the notes treat as the binding constraint "
    "on near-term cash generation; the funding mix behind it is not yet settled [2][5].\n"
    "- Competition: competition for AI workloads is intensifying, with rivals pricing "
    "aggressively for inference capacity and bundling it with existing commitments, and the "
    "notes expect that pressure to persist beyond the current planning horizon rather than "
    "easing with the next capacity cycle [1][6].\n"
    "- Regulation: regulatory scrutiny of large AI investments is named as a risk, with "
    "antitrust review of cloud bundling named as the specific mechanism, and none of the "
    "notes quantifies the exposure, names a jurisdiction or gives a timetable for it [4].")

print("\n  -- a 250-word three-category answer compresses under 220 --")
_long_direct = ab.parse_sections(_LONG_3CAT)["Direct Answer"]
_rep_direct = ab.parse_sections(_REPAIRED_3CAT)["Direct Answer"]
_long_words = ab.answer_word_count(_long_direct)
_rep_words = ab.answer_word_count(_rep_direct)
check("the long answer is over the ceiling and triggers the repair",
      _long_words > 220
      and len([f for f in ab.compliance_failures(ab.audit_answer(_LONG_3CAT, []), "quick")
               if f.startswith("the Direct Answer runs")]) == 1,
      f"{_long_words} words")
check("the repaired answer is inside the ceiling and triggers nothing",
      _rep_words <= 220
      and not ab.compliance_failures(ab.audit_answer(_REPAIRED_3CAT, []), "quick"),
      f"{_rep_words} words")
check("and it lands in the 160-200 band the instruction asks for",
      160 <= _rep_words <= 200, f"{_rep_words} words")
check("the compression is material, not cosmetic",
      _long_words - _rep_words >= 50,
      f"{_long_words} -> {_rep_words} words; 14.3.6 managed 270 -> 249")

print("\n  -- all three categories survive --")
check("the long answer carries all three categories",
      categories_in(_long_direct) == set(CATEGORIES), str(categories_in(_long_direct)))
check("and so does the repaired one",
      categories_in(_rep_direct) == set(CATEGORIES), str(categories_in(_rep_direct)))
_DROPPED = _answer_sections(
    "Two risks dominate the retrieved research [1][2].\n\n"
    "- Capital expenditure: capex rose and free cash flow fell to a use of $18 billion [2][5].\n"
    "- Competition: competition for AI workloads is intensifying [1][6].")
check("a repair that dropped a category is distinguishable from one that did not",
      categories_in(ab.parse_sections(_DROPPED)["Direct Answer"]) < set(CATEGORIES)
      and "regulation" not in categories_in(ab.parse_sections(_DROPPED)["Direct Answer"]),
      "the instruction forbids it; nothing in the code enforces it, so the live answer "
      "is what has to be read")

print("\n  -- what was allowed to go, and what was not --")
check("the redundant worked example is gone",
      "single regional data-centre programme" in _long_direct
      and "single regional data-centre programme" not in _rep_direct)
check("a secondary number is gone, and the primary one is kept",
      "$53 billion" in _long_direct and "$53 billion" not in _rep_direct
      and "$18 billion" in _long_direct and "$18 billion" in _rep_direct)
check("the repeated qualification is gone",
      "may persist" in _long_direct
      and _rep_direct.count("persist") == 1)
check("the re-listing closing synthesis is gone",
      "Taken together, the research identifies three risks" in _long_direct
      and "Taken together" not in _rep_direct)
_rep_figures = set(re.findall(r"\$?\d[\d,.]*%?", _rep_direct)) - set(
    re.findall(r"\[(\d{1,3})\]", _rep_direct))
check("and no new claim appeared: every figure in the repair was in the original",
      all(fig in _long_direct for fig in _rep_figures),
      str(sorted(f for f in _rep_figures if f not in _long_direct))
      + " - a repair may only remove, never invent")

print("\n  -- duplicate sources consolidated, grounding intact --")
_long_markers = aq.RE_CITATION.findall(_long_direct)
_rep_markers = aq.RE_CITATION.findall(_rep_direct)
check("the repair carries fewer citation MARKERS than the original",
      len(_rep_markers) < len(_long_markers),
      f"{len(_long_markers)} -> {len(_rep_markers)} markers")
check("and fewer distinct sources, because duplicates were consolidated",
      set(_rep_markers) < set(_long_markers),
      f"{sorted(set(_long_markers))} -> {sorted(set(_rep_markers))}")
check("yet every retained claim is still cited",
      cited_claims(_rep_direct)[1] == [], str(cited_claims(_rep_direct)[1]))
check("and the audit agrees: no uncited claim in the repaired answer",
      aq.measure(_REPAIRED_3CAT)["claims_uncited"] == 0
      and not ab.compliance_failures(ab.audit_answer(_REPAIRED_3CAT, []), "quick"))
check("dropping a redundant source is therefore NOT a grounding regression",
      len(set(_rep_markers)) < len(set(_long_markers))
      and aq.measure(_REPAIRED_3CAT)["claims_uncited"] == 0,
      "fewer markers with every claim still supported is the point of this phase")

_STRIPPED = _answer_sections(
    "Three risks dominate the retrieved research on Amazon's AI build-out [1].\n\n"
    "- Capital expenditure: capex rose again and free cash flow fell to a use of $18 billion "
    "in the quarter, with AWS capital intensity above 100% of sales.\n"
    "- Competition: competition for AI workloads is intensifying, with rivals pricing "
    "aggressively for inference capacity [1].\n"
    "- Regulation: regulatory scrutiny of large AI investments is named as a risk [4].")
check("a repair that removed the ONLY citation of a retained claim is still caught",
      aq.measure(_STRIPPED)["claims_uncited"] >= 1
      and any("carry no citation" in f
              for f in ab.compliance_failures(ab.audit_answer(_STRIPPED, []), "quick")),
      "the licence to drop a duplicate is not a licence to drop the last one")
check("and it would still cap confidence to Low",
      any(c[0] == "Low" and c[1] == "uncited_claims_in_direct_answer"
          for c in ab.post_generation_ceilings(
              {"citation_audit": ab.audit_answer(_STRIPPED, [])["citation_audit"]})))

print("\n  -- still one retry, still three calls, still no retrieval --")
_C7 = {"answer_model": "gpt-5-mini", "reasoning_effort": "low",
       "max_output_tokens": 6000, "answer_style": "quick"}
_I7 = "QUESTION: What risks?\n\nEVIDENCE:\n[1] chunk one\n[5] chunk five"
_calls = ab.AnswerCallBudget(); _calls.spend("answer")
_client = _StubClient(_StubResponse(_REPAIRED_3CAT))
_repair = ab.compliance_pass(_client, dict(_C7), _I7, _LONG_3CAT,
                             ab.audit_answer(_LONG_3CAT, []), _calls)
check("a 250-word answer buys exactly ONE repair call",
      _repair["used"] and len(_client.calls) == 1 and _calls.used == 2,
      f"{len(_client.calls)} call(s), {_calls.used}/{_calls.limit}")
check("the repaired text replaces the long one",
      _repair["text"] == _REPAIRED_3CAT.strip())
check("the new length wording reached the model",
      "Keep every DISTINCT category" in _client.calls[0]["instructions"]
      and "Aim at 160 to 200 words" in _client.calls[0]["instructions"])
check("the same question, the same evidence, nothing added or removed",
      _client.calls[0]["input"] == _I7 + ab.REPAIR_INPUT_HEADER + _LONG_3CAT)
check("the same style, once",
      _client.calls[0]["instructions"].count("LENGTH AND DEPTH FOR THIS ANSWER") == 1
      and ab.ANSWER_STYLES["quick"] in _client.calls[0]["instructions"])

_calls = ab.AnswerCallBudget(); _calls.spend("answer")
_client = _StubClient(_StubResponse(_answer_sections(
    "Three risks dominate [1][2].\n\n- Capital expenditure: capex rose [2][5].\n"
    "- Competition: competition is intensifying [1][6].\n"
    "- Regulation: regulatory scrutiny is a risk [4]. " + " ".join(["filler"] * 215) + " [1].")))
_repair = ab.compliance_pass(_client, dict(_C7), _I7, _LONG_3CAT,
                             ab.audit_answer(_LONG_3CAT, []), _calls)
_remaining = ab.compliance_failures(ab.audit_answer(_repair["text"], []), "quick")
check("a repair that is STILL over 220 is kept and its new count recorded",
      _repair["used"] and len(_remaining) == 1
      and _remaining[0].startswith("the Direct Answer runs"), str(_remaining))
check("and there is NO second retry",
      len(_client.calls) == 1 and _calls.used == 2)
check("the call ceiling is still three",
      ab.MAX_ANSWER_MODEL_CALLS == 3)
check("nothing in the retry path can retrieve, rank or embed",
      not re.search(r"(?:filter_chunks|rank_candidates|select_chunks|embed_query|"
                    r"open_collection|query_collection|collection\.|build_user_input)\(",
                    "".join(ASK_BRAIN_CODE.split(f"def {name}(")[-1].split("\ndef ")[0]
                            for name in ("compliance_pass", "compliance_failures",
                                         "call_answer_model", "_responses_call"))))

print("\n  -- Phase 14.3.7 changed one paragraph and nothing else --")
check("the base prompt is byte-identical to 14.3.4",
      len(PROMPT.split()) == PROMPT_WORDS and "A takeaway is NOT an exception" in PROMPT,
      f"{len(PROMPT.split())} words, expected {PROMPT_WORDS}")
check("compliance_failures and its three triggers are unchanged",
      ASK_BRAIN_CODE.count("def compliance_failures(") == 1
      and len(ab.compliance_failures(ab.audit_answer(_ALL_THREE, []), "quick")) == 3
      and ab.QUICK_DIRECT_WORD_CEILING == aq.QUICK_WORD_BAND[1] == 220)
check("compliance_pass is unchanged: one definition, one call site",
      ASK_BRAIN_CODE.count("def compliance_pass(") == 1
      and ASK_BRAIN_CODE.count("compliance_pass(") == 2)
check("the budget, the ceiling and replace-not-stitch are unchanged",
      ab.MAX_ANSWER_MODEL_CALLS == 3 and ab.AnswerCallBudget().limit == 3
      and "REPAIR_INPUT_HEADER" in ASK_BRAIN_CODE)
check("the quick hierarchy and the deep style are untouched",
      len(ab.ANSWER_STYLES["quick"].split()) == QUICK_WORDS_1432
      and len(ab.ANSWER_STYLES["deep"].split()) == 132
      and "may run to about 400 words" in ab.ANSWER_STYLES["deep"])
check("a deep answer is still never length-repaired",
      not ab.compliance_failures(ab.audit_answer(_of_length(420), []), "deep"))
check("answer_quality definitions are untouched",
      aq.MODULE_VERSION == "phase14.2-1.0" and aq.QUICK_WORD_BAND == (100, 220)
      and aq.DEEP_WORD_BAND == (150, 420) and aq.REPETITION_OVERLAP == 0.6
      and aq.RE_SENTENCE.pattern == ab.RE_SENTENCE.pattern
      and aq.RE_WORD.pattern == ab.RE_ANSWER_WORD.pattern, aq.MODULE_VERSION)
check("the confidence ceilings are the same six, in the same order",
      [c[1] for ctx in (
          {"citation_audit": {"uncited_sentences": ["Margins fell sharply this year."]}},
          {"attribution_violations": ["The company reported X."]},
          {"unsupported_subject": True},
          {"recommendation_refused": True},
          {"generation_incomplete": True, "generation_incomplete_reasons": ["x"]},
          {"generation_status": "incomplete"})
       for c in ab.post_generation_ceilings(ctx)]
      == ["uncited_claims_in_direct_answer",
          "company_attribution_without_company_evidence",
          "subject_absent_from_evidence", "recommendation_refused",
          "generation_incomplete", "generation_incomplete"])
check("unsupported and refusal logic are untouched",
      "def build_refusal_answer(" in ASK_BRAIN_CODE
      and "def build_unsupported_subject_answer(" in ASK_BRAIN_CODE
      and "REFUSAL_SENTENCE" in ASK_BRAIN_CODE)
check("retrieval, ranking and selection are untouched",
      (ab.W_VECTOR, ab.W_KEYWORD, ab.W_PRIORITY, ab.W_RECENCY) == (0.65, 0.25, 0.10, 0.10)
      and (ab.PRIORITY_BONUS, ab.PRIORITY_PENALTY, ab.DOC_REPEAT_PENALTY,
           ab.SOURCE_REPEAT_PENALTY) == (0.06, 0.03, 0.05, 0.03)
      and ab.WEAK_EVIDENCE_SIMILARITY == 0.25
      and ab.RETRIEVAL_MODES == ("vector", "keyword", "hybrid"))
check("nothing was written to any index or corpus",
      "INSERT INTO rag_chunks" not in ASK_BRAIN_CODE and "collection.add(" not in ASK_BRAIN_CODE)
check("no answer is truncated by this script",
      not re.search(r"(?:answer|direct_answer|answer_text)\s*=\s*\w*answer\w*\[\s*:\s*\d+",
                    ASK_BRAIN_CODE)
      and "textwrap.shorten" not in ASK_BRAIN_CODE,
      "a shorter answer is the model's own shorter answer")
check("the evaluator was not touched by this phase",
      rv.PHASE == "phase14.3.3-1.0" and '"Observed changes:"' in REVIEW_SRC
      and "def answer_body(" in REVIEW_SRC)
check("the pipeline version records this phase",
      ab.PIPELINE_VERSION == "phase14.3.7-1.0" and ui.PHASE == "phase14.3.7-1.0",
      f"{ab.PIPELINE_VERSION} / {ui.PHASE}")

print("\n" + "=" * 70)
if FAILS:
    print(f"{len(FAILS)} FAILING CHECK(S):")
    for name in FAILS:
        print("  - " + name)
    sys.exit(1)
print("ALL CHECKS PASSED")
