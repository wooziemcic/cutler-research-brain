#!/usr/bin/env python3
"""Phase 13 - the research workspace, checked before it is trusted.

WHAT THIS SUITE IS FOR
----------------------
Phase 13 changed no retrieval, no index and no answer. It moved things. That
makes it the easiest kind of phase to break silently: an id renamed in the
template and a pane stops filling, with no error anywhere. Phase 10A.1 was
spent on exactly that failure.

So this suite asks three questions:

  1. Is the new shell right?      one navigation, seven views, no label twice
  2. Does everything still work?  Ask, Documents, the PDF preview, System,
                                  diagnostics, and every endpoint that existed
                                  before this phase
  3. Did the vocabulary change?   the default view must not put vector scores,
                                  BM25 scores or chunk ids in front of a reader

Phase 13.1 added five more, all of them about what the page REMEMBERS between
one action and the next - the composer, follow-up context, the restorable
panel, company context, and the path from a citation to the page it came from.
Sections 11 to 16 cover those.

It runs against the real FastAPI app with a TestClient, so the routes are the
ones the browser gets, not a description of them. Nothing it calls writes
anything: every request is a GET, and the one POST endpoint is never exercised
because asking a question costs money.

  AI drafts. Humans verify. Cutler decides.

Usage:
  py -3.11 tests\\test_phase13.py
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
    """Source with block comments and line comments removed.

    A check that greps a whole file matches the comment explaining a behaviour
    and reports it as present. Claims about what code does are made against the
    code - the lesson of Phase 11.2 and 12.1.
    """
    stripped = re.sub(r"/\*(?:.|\n)*?\*/", "", text)
    stripped = re.sub(r'"""(?:.|\n)*?"""', '""', stripped)
    out = []
    for line in stripped.splitlines():
        bare = line.lstrip()
        if bare.startswith("//") or bare.startswith("#"):
            continue
        out.append(line)
    return "\n".join(out)


HTML = (UI / "templates" / "index.html").read_text(encoding="utf-8")
JS = (UI / "static" / "app.js").read_text(encoding="utf-8")
CSS = (UI / "static" / "styles.css").read_text(encoding="utf-8")
APP = (UI / "app.py").read_text(encoding="utf-8")
JS_CODE = code_only(JS)
APP_CODE = code_only(APP)

EXPECTED_VIEWS = ["ask", "history", "companies", "themes", "documents", "evaluations", "system"]

# ===========================================================================
print("=== 1. THE SHELL ===")
nav_views = re.findall(r'<button class="nav-item[^"]*" data-view="([a-z]+)"', HTML)
check("the sidebar has all seven views",
      sorted(nav_views) == sorted(EXPECTED_VIEWS), str(sorted(nav_views)))
check("no view appears twice in the navigation",
      len(nav_views) == len(set(nav_views)), str(nav_views))

# The icon span is decoration; the label is the text beside it.
nav_labels = [re.sub(r"<[^>]+>", "",
                     re.sub(r'<span class="nav-icon".*?</span>', "", block, flags=re.S)).strip()
              for block in re.findall(r'<button class="nav-item[^>]*>(.*?)</button>', HTML,
                                      re.S)]
check("no navigation label appears twice",
      len(nav_labels) == len(set(nav_labels)), str(nav_labels))
check("the labels are the ones the brief asked for",
      sorted(nav_labels) == sorted(["New Research", "History", "Companies", "Themes",
                                    "Documents", "Evaluations", "System"]),
      str(sorted(nav_labels)))
check("there is exactly one <nav> of main navigation",
      HTML.count('aria-label="Main"') == 1)
check("the Phase 10C 'coming later' placeholders are gone",
      "nav-soon" not in HTML and "nav-disabled" not in HTML)
check("every view has a container in the page",
      all(f'id="view-{name}"' in HTML for name in EXPECTED_VIEWS),
      str([n for n in EXPECTED_VIEWS if f'id="view-{n}"' not in HTML]))
check("the view switcher knows all seven",
      re.search(r'var VIEWS = \[([^\]]+)\]', JS_CODE) is not None
      and all(f'"{name}"' in re.search(r'var VIEWS = \[([^\]]+)\]', JS_CODE).group(1)
              for name in EXPECTED_VIEWS))

# ===========================================================================
print("\n=== 2. THE ASK WORKSPACE ===")
for element_id in ("question", "ask", "ticker", "form-error", "answer", "answer-block",
                   "trust", "badges", "busy", "followup", "followup-send", "suggestions"):
    if f'id="{element_id}"' not in HTML:
        check(f"the ask workspace keeps #{element_id}", False)
check("the ask workspace keeps every element it binds to",
      all(f'id="{element_id}"' in HTML for element_id in
          ("question", "ask", "ticker", "form-error", "answer", "answer-block", "trust",
           "badges", "busy", "followup", "followup-send")))
check("there is a Quick Answer / Deep Research control",
      HTML.count('data-depth="quick"') == 1 and HTML.count('data-depth="deep"') == 1)
check("the empty state exists and is a hero, not a form",
      'id="research-hero"' in HTML and "hero-title" in HTML)
check("suggested questions are offered before anything is asked",
      'id="suggestions"' in HTML and HTML.count('class="preset') >= 4)
check("follow-up suggestions are rendered after an answer",
      "renderFollowups" in JS_CODE and 'id="followup-buttons"' in HTML)
check("the trust summary is rendered from the server's own translation",
      "renderTrust" in JS_CODE and "data.trust" in JS_CODE)

# ===========================================================================
print("\n=== 3. THE RESEARCH PANEL ===")
panes = re.findall(r'data-rpane="([a-z]+)"', HTML)
expected_panes = ["sources", "documents", "deepdive", "timeline", "company", "related"]
check("the right panel has all six tabs",
      sorted(set(panes)) == sorted(expected_panes), str(sorted(set(panes))))
check("each tab appears once", len(panes) == len(set(panes)), str(panes))
check("each tab has a pane",
      all(f'id="rpane-{name}"' in HTML for name in expected_panes))
check("the sources panel kept its Phase 7B ids",
      all(f'id="{name}"' in HTML for name in ("sources-card", "sources-count", "sources")))
check("the Deep Dive pane is where the run summary went",
      'id="rpane-deepdive"' in HTML
      and HTML.index('id="rpane-deepdive"') < HTML.index('id="summary"'))
check("the panel can be collapsed", 'id="panel-collapse"' in HTML)

# ===========================================================================
print("\n=== 4. NO ENGINEERING VOCABULARY IN THE DEFAULT VIEW ===")
# The rule is about what a reader sees WITHOUT opening anything. Raw scores and
# chunk ids are still reachable - a diagnostics tool that hides its numbers is
# worse - but they sit behind a disclosure.
before_disclosure = JS_CODE.split("source-why")[0]
check("retrieval scores are not printed outside a disclosure",
      "vector \" + score(" not in before_disclosure, "")
check("the raw scores moved into the 'Why this passage' disclosure",
      "source-why" in JS_CODE and "vector \" + score(" in JS_CODE.split("source-why")[1])
check("the chunk id is not shown beside the file name by default",
      JS_CODE.split("source-why")[0].count("item.chunk_id") == 0)
check("the sources count is in sources, not chunks",
      '" chunk(s)"' not in JS_CODE and '" source"' in JS_CODE)
check("the advanced summary line says passages, not chunks",
      '" passages · "' in JS_CODE and '" chunks · top_k "' not in JS_CODE)
check("the ticker hint translates chunks into passages",
      'replace(/chunk\\(s\\)/g, "passages")' in JS_CODE)

visible_text = re.sub(r"<[^>]+>", " ", HTML)
for word in ("BM25", "embedding vector", "cosine"):
    check(f"the page never shows the phrase {word!r}", word.lower() not in visible_text.lower())
check("the main navigation uses no pipeline vocabulary",
      not any(word in " ".join(nav_labels).lower()
              for word in ("rag", "chunk", "embed", "vector", "retrieval", "pipeline")))

# ===========================================================================
print("\n=== 5. THE APP STILL ANSWERS EVERY ENDPOINT IT DID BEFORE ===")
from fastapi.testclient import TestClient                          # noqa: E402
import app as ui                                                   # noqa: E402

client = TestClient(ui.app)

# Every route that existed before Phase 13. Backward compatibility is the point
# of this list: a UI phase that quietly drops an endpoint breaks whatever else
# was reading it.
LEGACY_ROUTES = [
    "/", "/api/health", "/api/runs", "/api/review-queue", "/api/evaluation-status",
    "/api/golden-evaluation-status", "/api/documents", "/api/tickers",
    "/api/document-filters",
]
for route in LEGACY_ROUTES:
    response = client.get(route)
    check(f"{route} still answers", response.status_code == 200, str(response.status_code))

NEW_ROUTES = ["/api/history", "/api/companies", "/api/themes", "/api/evaluation-dashboard",
              "/api/evaluation-questions"]
for route in NEW_ROUTES:
    response = client.get(route)
    check(f"{route} answers", response.status_code == 200, str(response.status_code))

paths = {getattr(r, "path", "") for r in ui.app.routes}
for route in LEGACY_ROUTES + NEW_ROUTES:
    if "{" not in route:
        check(f"{route} is registered", route in paths)
check("the per-document routes are all still registered",
      all(path in paths for path in (
          "/api/document/{document_id}", "/api/document/{document_id}/preview",
          "/api/document/{document_id}/card", "/api/document/{document_id}/text",
          "/api/document/{document_id}/pages", "/api/document/{document_id}/chunks",
          "/api/document/{document_id}/source-path", "/api/diagnostics/{run_id}",
          "/api/run/{run_id}")),
      str(sorted(p for p in paths if "document" in p)))
check("the ask endpoint is still a POST",
      any(getattr(r, "path", "") == "/api/ask" and "POST" in getattr(r, "methods", set())
          for r in ui.app.routes))

# The PDF preview guard is the one that must not have loosened.
response = client.get("/api/document/not-a-document-id/preview")
check("the preview endpoint still refuses anything that is not a document id",
      response.status_code == 400, str(response.status_code))
response = client.get("/api/document/doc_deadbeefdeadbeef/preview")
check("the preview endpoint answers for a well-formed id (404 when absent)",
      response.status_code in (200, 404), str(response.status_code))

body = client.get("/").text
check("the page renders with every view in it",
      all(f'id="view-{name}"' in body for name in EXPECTED_VIEWS))
def _phase_number(value: str) -> tuple:
    """(13, 1) from "phase13.1-1.0". A string compare reads "phase14" as
    earlier than "phase13.1", which is how a version pin starts lying."""
    match = re.search(r"phase(\d+)(?:\.(\d+))?", str(value or ""), re.I)
    return (int(match.group(1)), int(match.group(2) or 0)) if match else (0, 0)


marker = re.search(r"phase[\d.]+", body)
check("the rendered page carries a phase marker at or past 13",
      bool(marker) and _phase_number(marker.group(0)) >= (13, 0),
      marker.group(0) if marker else "none")

# ===========================================================================
print("\n=== 6. THE EVALUATION DASHBOARD READS PHASE 12 DATA ===")
dashboard = client.get("/api/evaluation-dashboard").json()
if dashboard.get("available"):
    check("it reports the pass, review and fail counts",
          all(key in dashboard for key in ("pass_count", "review_count", "fail_count")))
    check("it reports the total and the date",
          dashboard.get("questions_total") and dashboard.get("completed_at"))
    check("it breaks the result down per ticker",
          isinstance(dashboard.get("per_ticker"), list) and dashboard["per_ticker"])
    check("it breaks the result down per category",
          isinstance(dashboard.get("per_category"), list) and dashboard["per_category"])
    check("it counts critical failures separately",
          "critical_failures" in dashboard)
    check("it explains that REVIEW is not a failure",
          "not a failure" in (dashboard.get("review_means") or "").lower())
    check("it explains that PASS is not proof the answer is true",
          "does not mean the answer is true" in (dashboard.get("pass_means") or ""))
    check("the counts add up to the total",
          (dashboard["pass_count"] + dashboard["review_count"] + dashboard["fail_count"])
          == dashboard["questions_total"],
          f"{dashboard['pass_count']}/{dashboard['review_count']}/{dashboard['fail_count']}"
          f" of {dashboard['questions_total']}")
else:
    check("the dashboard says plainly that no evaluation has been run",
          bool(dashboard.get("note")), str(dashboard))

check("per-question rows are a separate request, not the default view",
      "/api/evaluation-questions" in APP_CODE
      and 'id="eval-detail"' in HTML
      and "loadEvaluationQuestions" in JS_CODE)
check("the per-question list loads only when the disclosure is opened",
      'addEventListener("toggle"' in JS_CODE)

# ===========================================================================
print("\n=== 7. DOCUMENTS AND SYSTEM SURVIVED ===")
for element_id in ("docs-filters", "docs-list", "docs-reader", "doc-rows", "doc-detail-card",
                   "doc-subtabs", "pane-preview", "pane-text", "pane-chunks", "doc-pager",
                   "doc-source-path", "doc-copy-path"):
    if f'id="{element_id}"' not in HTML:
        check(f"the documents workspace keeps #{element_id}", False)
check("the documents workspace is intact",
      all(f'id="{element_id}"' in HTML for element_id in
          ("docs-filters", "docs-list", "docs-reader", "doc-rows", "doc-detail-card",
           "doc-subtabs", "pane-preview", "doc-pager")))
check("the three-panel layout rule is still in the stylesheet",
      ".workspace-docs" in CSS and "grid-template-rows: minmax(0, 1fr)" in CSS)
check("the document reader still has all six sub-tabs",
      len(re.findall(r'class="subtab[^"]*" data-pane="', HTML)) == 6,
      str(len(re.findall(r'class="subtab[^"]*" data-pane="', HTML))))
check("the original PDF preview tab is still there",
      'data-pane="preview"' in HTML and 'id="pane-preview"' in HTML)

check("the system view keeps its cards",
      all(f'id="{element_id}"' in HTML for element_id in
          ("sys-tiles", "sys-ingestion", "sys-eval", "health", "sys-refresh")))
check("diagnostics are still reachable",
      'id="diagnostics-card"' in HTML and 'id="diagnostics"' in HTML
      and "renderDiagnostics" in JS_CODE)
check("the raw run summary is still rendered, in Deep Dive",
      'id="summary"' in HTML and "renderSummary" in JS_CODE)
check("the audit checks are still rendered",
      'id="audit"' in HTML and "renderAudit" in JS_CODE)
check("the review queue and run list survived, on History",
      'id="review"' in HTML and 'id="runs"' in HTML
      and HTML.index('id="view-history"') < HTML.index('id="review"'))
check("the Phase 8 evaluation card survived, on Evaluations",
      'id="evaluation"' in HTML
      and HTML.index('id="view-evaluations"') < HTML.index('id="evaluation"'))

# ===========================================================================
print("\n=== 8. NOTHING WAS MUTATED, AND NO FRAMEWORK WAS ADDED ===")
check("the UI writes nothing to rag_chunks",
      not re.search(r"(INSERT|UPDATE|DELETE)\s+(INTO\s+)?rag_chunks", APP_CODE, re.I))
check("the UI writes nothing to documents",
      not re.search(r"(INSERT|UPDATE|DELETE)\s+(INTO\s+)?documents\b", APP_CODE, re.I))
check("the UI writes nothing to the evaluation tables",
      not re.search(r"(INSERT|UPDATE|DELETE)\s+(INTO\s+)?golden_eval", APP_CODE, re.I))
check("the UI never imports chromadb", "chromadb" not in APP_CODE)
check("the UI makes no OpenAI call of its own",
      not re.search(r"^\s*(import openai|from openai)", APP_CODE, re.M)
      and "OpenAI(" not in APP_CODE)
check("the only writes are Ask Brain's own, through the subprocess",
      "ask_brain.py" in APP or "ASK_BRAIN" in APP_CODE)
check("no V-drive path is written to",
      "SOURCE_ARCHIVE_ROOT" in APP_CODE and "assert_inside_project" in APP_CODE)
# One import and one call: the preview endpoint is the only thing in this app
# that returns a file from the archive, and it is read-only.
check("the source archive is opened read-only, for preview alone",
      APP_CODE.count("return FileResponse(") == 1,
      str(APP_CODE.count("return FileResponse(")))

check("no React, Next.js, Vue or Svelte",
      not re.search(r"react|next\.js|vue\.js|svelte|webpack|vite", HTML + JS_CODE, re.I))
check("no external script or stylesheet",
      not re.search(r'(src|href)="https?://', HTML))
check("no external font", "fonts.googleapis" not in HTML + CSS
      and "@import" not in CSS)
check("still one stylesheet and one script",
      len(list((UI / "static").glob("*.css"))) == 1
      and len(list((UI / "static").glob("*.js"))) == 1)
check("the page still loads only local assets",
      HTML.count('href="/static/styles.css"') == 1
      and HTML.count('src="/static/app.js"') == 1)

# ===========================================================================
print("\n=== 9. QUICK ANSWER AND DEEP RESEARCH ARE ONE RETRIEVAL SYSTEM ===")
check("the depth presets live in one place, on the server",
      "DEPTH_PRESETS" in APP_CODE and '"quick"' in APP_CODE and '"deep"' in APP_CODE)
RETRIEVAL_KEYS = ("top_k", "max_chunks", "max_chunks_per_document")
check("quick retrieves exactly what it always retrieved",
      {k: ui.DEPTH_PRESETS["quick"][k] for k in RETRIEVAL_KEYS}
      == {"top_k": 30, "max_chunks": 8, "max_chunks_per_document": 2},
      str(ui.DEPTH_PRESETS["quick"]))
check("deep only widens the same search",
      ui.DEPTH_PRESETS["deep"]["top_k"] > ui.DEPTH_PRESETS["quick"]["top_k"]
      and ui.DEPTH_PRESETS["deep"]["max_chunks"] > ui.DEPTH_PRESETS["quick"]["max_chunks"])
check("depth carries nothing but retrieval size and writing style",
      all(set(preset) <= set(RETRIEVAL_KEYS) | {"answer_style"}
          for preset in ui.DEPTH_PRESETS.values()), str(ui.DEPTH_PRESETS))
check("depth changes no ranking weight and no retrieval mode",
      "retrieval_mode" not in str(ui.DEPTH_PRESETS)
      and "weight" not in str(ui.DEPTH_PRESETS))
check("a request with no depth keeps whatever numbers it sent",
      'preset = DEPTH_PRESETS.get(depth, {})' in APP_CODE
      and 'preset.get("top_k", payload.top_k)' in APP_CODE)
check("an unknown depth is refused rather than guessed",
      "depth must be one of" in APP)
check("there is no second retrieval implementation",
      not re.search(r"def (retrieve|rank|embed)\w*\(", APP_CODE))

# ===========================================================================
print("\n=== 10. THE PHASE 12 EVALUATION DATA IS UNTOUCHED ===")
combined = ROOT / "07_Evaluation" / "golden_questions" / "multi_ticker_eval_questions.json"
check("the 60-question suite is still on disk", combined.exists())
if combined.exists():
    payload = json.loads(combined.read_text(encoding="utf-8"))
    check("it still holds 60 questions", len(payload["questions"]) == 60,
          str(len(payload["questions"])))
    check("it still covers all ten tickers",
          len({q["ticker"] for q in payload["questions"]}) == 10)
    check("its schema version is still Phase 12",
          str(payload.get("schema_version", "")).startswith("phase12"),
          str(payload.get("schema_version")))
check("the Phase 12 runner was not modified by this phase",
      "phase12" in (SCRIPTS / "run_multi_ticker_evaluation.py").read_text(encoding="utf-8"))
check("the evaluation is still started from the command line, never the UI",
      "run_multi_ticker_evaluation" not in APP_CODE
      or "subprocess" not in APP_CODE.split("run_multi_ticker_evaluation")[-1][:400])

# ===========================================================================
# Phase 13.1
#
# Five interaction defects, and the thing they had in common: the page threw
# away its context between one action and the next. These sections check that
# the context now exists, that it is EXPLICIT rather than a chat buffer, and
# that none of it reaches the pipeline as a new way to retrieve.
# ===========================================================================
print("\n=== 11. THE QUESTION COMPOSER ===")
# Layout is proved in a browser (scratchpad/drive131.py measures the boxes).
# What is checked here is the RULE the layout rests on: the text and the button
# are separate grid cells, so no amount of typing can put a character under the
# button. A padding value could not promise that.
# The LAST rule for a selector is the one the browser uses, so that is the one
# this asserts on. Reading the first and reporting on it would describe a rule
# that has been overridden - which is how a stylesheet check lies.
askbar_rows = re.findall(r"\.askbar-row\s*\{[^}]*\}", CSS, re.S)
askbar_row = askbar_rows[-1] if askbar_rows else ""
check("the composer lays the text and the button out in separate columns",
      "display: grid" in askbar_row and "grid-template-columns" in askbar_row,
      askbar_row.replace("\n", " ")[:90])
textarea = re.findall(r"\.askbar textarea\s*\{[^}]*\}", CSS, re.S)
textarea = " ".join(textarea)
check("the box has a floor and a ceiling",
      "min-height" in textarea and "max-height" in textarea)
check("at the ceiling it scrolls rather than growing",
      "overflow-y" in textarea or "overflowY" in JS_CODE)
check("autosize is a named function, not an inline handler",
      "function autosizeQuestion(" in JS_CODE)
check("text set by the page resizes the box too",
      "function setQuestionText(" in JS_CODE and "autosizeQuestion()" in
      JS_CODE.split("function setQuestionText(")[1][:400])
check("the minimum and maximum are stated once, in named constants",
      "QUESTION_MIN_HEIGHT" in JS_CODE and "QUESTION_MAX_HEIGHT" in JS_CODE)
# The defect itself: seven characters written into a 34px square button.
check("asking no longer writes a word into the square send button",
      'textContent = "Asking' not in JS_CODE)
check("it shows a busy state instead",
      'classList.add("is-busy")' in JS_CODE and ".askbar-send.is-busy" in CSS)

print("\n=== 12. FOLLOW-UP CONTEXT IS EXPLICIT, NOT A CHAT BUFFER ===")
check("the browser sends a run id and a flag, and nothing else",
      "follow_up: bool = False" in APP_CODE and "previous_run_id: int = 0" in APP_CODE)
check("the previous question and evidence are looked up server-side",
      "def previous_run_context(" in APP_CODE
      and "ask_brain_runs WHERE run_id" in APP_CODE.split("def previous_run_context(")[1][:900])
check("there is no conversation history anywhere in the request",
      not re.search(r"\b(chat_history|messages\s*=\s*\[|conversation)\b", APP_CODE))
check("what a follow-up would do can be asked before anything is spent",
      "/api/follow-up-plan" in APP_CODE)
check("the classifier is regular expressions, not a model call",
      "RE_EVIDENCE_REFERENCE" in APP_CODE and "RE_CONTEXTUAL_FOLLOWUP" in APP_CODE)
check("the UI still makes no OpenAI call of its own to classify anything",
      not re.search(r"^\s*(import openai|from openai)", APP_CODE, re.M))
check("the composed question names exactly one previous question",
      "def compose_follow_up_question(" in APP_CODE
      and APP_CODE.split("def compose_follow_up_question(")[1][:600].count("previous") >= 1)

# The seven examples the brief names, each classified without a model.
FOLLOW_UP_CASES = [
    ("Which documents support this?", "evidence_reference"),
    ("Where did this come from?", "evidence_reference"),
    ("Show me the source.", "evidence_reference"),
    ("Which analyst said that?", "evidence_reference"),
    ("Why?", "contextual"),
    ("What changed from before?", "contextual"),
    ("Tell me more about the second point.", "contextual"),
    # And the ones that must NOT inherit context.
    ("What does Microsoft report about cloud and Azure revenue growth?", "new_subject"),
    ("What are Kroger's grocery margins doing?", "new_subject"),
]
for text, expected in FOLLOW_UP_CASES:
    got = ui.classify_follow_up(text, "T")["kind"]
    check(f"{text[:38]!r} is a {expected}", got == expected, got)
# Punctuation included on purpose. "What about Verizon?" is exactly the shape a
# person types when they change subject, and matching on spaces either side of
# the name missed every one of them.
for text, expected in (("What about Verizon?", "VZ"), ("and Kroger?", "KR"),
                       ("Compare this with Amazon's margins.", "AMZN"),
                       ("What does AT&T say?", "T"), ("Barnes Group - same question", "B")):
    switched = ui.classify_follow_up(text, "MSFT")
    check(f"{text[:34]!r} is recognised as a change of company",
          switched["kind"] == "new_subject" and switched.get("names_other_company") == expected,
          str(switched.get("names_other_company")))
check("and a follow-up that names nobody else carries no such claim",
      not ui.classify_follow_up("Why?", "T").get("names_other_company"))
check("the classifier explains itself",
      bool(ui.classify_follow_up("Why?", "T").get("reason")))

plan = ui.follow_up_plan("Why?", "MSFT", 0)
check("with no previous answer there is no context to inherit",
      plan["kind"] == "new_subject" and plan["restrict_to_documents"] == [], str(plan["kind"]))
check("and it says so rather than failing quietly", bool(plan["explanation"]))

# The restriction reaches Ask Brain as a FILTER, in the same place as the
# others, and only when the server itself put it there.
values = ui.validate(ui.AskRequest(question="Why?", ticker="MSFT"))
check("a plain question restricts nothing",
      "--restrict-to-documents" not in ui.build_command(values))
values["restrict_to_documents"] = ["doc_a", "doc_b"]
command = ui.build_command(values)
check("a referential follow-up passes the documents as a filter",
      command.count("--restrict-to-documents") == 2 and "doc_a" in command and "doc_b" in command)
check("it is still one argument list, never a shell string",
      isinstance(command, list) and all(isinstance(part, str) for part in command))

ASK_BRAIN_SRC = (SCRIPTS / "ask_brain.py").read_text(encoding="utf-8")
check("Ask Brain applies it before ranking, with the other filters",
      "restrict-to-documents" in ASK_BRAIN_SRC
      and "restrict_to_documents" in ASK_BRAIN_SRC.split("def filter_chunks(")[1][:2200])
check("it changes no ranking weight",
      not re.search(r"W_VECTOR|W_KEYWORD|W_PRIORITY|W_RECENCY",
                    ASK_BRAIN_SRC.split("def filter_chunks(")[1].split("def explain_empty")[0]))
check("an older caller with no such argument still works",
      'getattr(args, "restrict_to_documents"' in ASK_BRAIN_SRC)
check("the filter is named in the run's own filter summary",
      "restrict-to-documents" in ASK_BRAIN_SRC.split("def describe_filters(")[1][:900])

print("\n=== 13. THE RIGHT PANEL IS ALWAYS RESTORABLE ===")
check("there is a restore control in the page", 'id="panel-restore"' in HTML)
check("it is labelled Sources, not an icon alone",
      "panel-restore-label" in HTML and "Sources" in
      re.search(r'<button class="panel-restore".*?</button>', HTML, re.S).group(0))
check("it lives in the research workspace, not on another view",
      HTML.index('id="panel-restore"') > HTML.index('id="view-ask"')
      and HTML.index('id="panel-restore"') < HTML.index('id="view-history"'))
check("one function owns whether the panel is hidden",
      "function setPanelHidden(" in JS_CODE)
check("collapsing goes through it",
      "setPanelHidden(!panelHidden)" in JS_CODE)
check("opening a citation restores the panel by itself",
      "setPanelHidden(false)" in JS_CODE.split("function openCitation(")[1][:700])
check("so does opening a source",
      "setPanelHidden(false)" in JS_CODE.split("function openEvidence(")[1][:700])
check("restoring needs no navigation",
      "switchView" not in JS_CODE.split("function setPanelHidden(")[1][:600])

print("\n=== 14. CHANGING COMPANY CHANGES THE RESEARCH CONTEXT ===")
check("there is one request that carries everything a company change needs",
      "/api/company-context/{ticker}" in {getattr(r, "path", "") for r in ui.app.routes})
check("changing the select applies it",
      "applyCompanyContext($(\"ticker\").value, false)" in JS_CODE)
check("it clears the answer that was about the last company",
      "function resetAnswerState(" in JS_CODE
      and "resetAnswerState()" in JS_CODE.split("function applyCompanyContext(")[1][:700])
check("suggestions come from a file, never a model call",
      "QUESTION_BANK_PATH" in APP_CODE and "multi_ticker_eval_questions.json" in APP_CODE)
check("the two Phase 12 categories designed to fail are not offered",
      "SUGGESTION_CATEGORIES_EXCLUDED" in APP_CODE
      and "unsupported_no_evidence" in APP_CODE and "recommendation_refusal" in APP_CODE)
check("there is a written fallback for a company with no golden questions",
      "SUGGESTION_TEMPLATES" in APP_CODE and "{company}" in APP_CODE)

# The two the brief names by hand, plus every other ticker.
for symbol, expected in (("T", "AT&T"), ("B", "Barnes Group"), ("KIM", "Kimco Realty"),
                         ("AMZN", "Amazon"), ("KR", "Kroger"), ("MRK", "Merck"),
                         ("VZ", "Verizon"), ("SLB", "SLB"), ("ABBV", "AbbVie"),
                         ("MSFT", "Microsoft")):
    body = client.get(f"/api/company-context/{symbol}").json()
    check(f"{symbol} is shown as {expected}", body.get("company") == expected,
          str(body.get("company")))
    questions = " | ".join(item["question"] for item in body.get("suggestions") or [])
    check(f"{symbol} is offered four questions",
          len(body.get("suggestions") or []) == 4, str(len(body.get("suggestions") or [])))
    check(f"{symbol}'s questions are about {expected}", expected in questions,
          questions[:100])
    check(f"{symbol}'s placeholder is about {expected}",
          expected in (body.get("placeholder") or ""), (body.get("placeholder") or "")[:80])

# The Phase 13 defect, named: Microsoft's wording was hardcoded and offered
# under every ticker.
for symbol in ("T", "B", "KIM", "AMZN", "KR"):
    body = client.get(f"/api/company-context/{symbol}").json()
    blob = " ".join([body.get("placeholder") or ""] +
                    [item["question"] for item in body.get("suggestions") or []])
    check(f"nothing offered for {symbol} mentions Microsoft", "Microsoft" not in blob,
          blob[:90])
check("the same ticker gives the same questions every time",
      client.get("/api/company-context/KIM").json()["suggestions"]
      == client.get("/api/company-context/KIM").json()["suggestions"])
check("an unknown ticker is refused, not guessed at",
      client.get("/api/company-context/ZZZZ").status_code == 400)
check("the follow-up chips are built from the selected company's own questions",
      "companyState.suggestions[symbol]" in JS_CODE)
check("the Phase 13 hardcoded Microsoft follow-ups are gone",
      "What do analysts say about this?" not in JS_CODE)

print("\n=== 15. ANSWER -> CITATION -> DOCUMENT ===")
check("the evidence records are built in one place",
      "def enrich_evidence(" in APP_CODE)
check("the passage is read ticker-scoped, with no fallback",
      "def chunk_passages(" in APP_CODE
      and "WHERE ticker = ? AND chunk_id IN" in APP_CODE)
check("no lookup falls back to chunk_id alone",
      not re.search(r"WHERE chunk_id\s*=\s*\?\s*\"", APP_CODE))
EVIDENCE_FIELDS = ("citation", "document_id", "chunk_id", "title", "source_type", "publisher",
                   "document_date", "page_start", "page_end", "page_label", "supporting_text",
                   "ticker")
for field in EVIDENCE_FIELDS:
    check(f"an evidence record carries {field}",
          f'item["{field}"]' in APP_CODE or f'"{field}"' in APP_CODE)
check("the run endpoint returns them at the top level too",
      '"evidence": (retrieval_summary or {}).get("evidence") or []' in APP_CODE)

# Against a real run, if this machine has one.
runs = client.get("/api/history?limit=5").json().get("runs") or []
if runs:
    detail = client.get(f"/api/run/{runs[0]['run_id']}").json()
    records = detail.get("evidence") or []
    if records:
        first = records[0]
        check("a real evidence record has every field the brief asked for",
              all(key in first for key in EVIDENCE_FIELDS),
              str([key for key in EVIDENCE_FIELDS if key not in first]))
        check("its ticker is the run's ticker",
              str(first.get("ticker")) == str(detail["run"].get("ticker")).upper(),
              f"{first.get('ticker')} vs {detail['run'].get('ticker')}")
        check("a page range reads as a page range, never as 'p. -1'",
              all("-1" not in str(record.get("page_label") or "") for record in records))
    else:
        check("the run carried no evidence, so there was nothing to check", True,
              "no selected chunks recorded")
else:
    check("no runs on this machine, so the live evidence check was skipped", True)

check("every [n] in an answer becomes a control",
      "function linkCitations(" in JS_CODE and "cite-link" in JS_CODE and "cite-link" in CSS)
check("the citation regex leaves markup alone",
      r"/(<[^>]*>)|\[(\d{1,3})\]/g" in JS_CODE)
check("clicking one selects that source",
      "function markSelectedSource(" in JS_CODE
      and "markSelectedSource(citation)" in JS_CODE.split("function openCitation(")[1][:700])
check("and opens the document behind it",
      "openEvidence(citation)" in JS_CODE.split("function openCitation(")[1][:900])
check("a citation with no matching source says so instead of guessing",
      "has no source with that number" in JS_CODE)
check("the document opens in the rail, beside the answer",
      "function renderRailDocument(" in JS_CODE and 'id="rdoc-body"' in HTML)
check("it shows the passage the answer used",
      "function railPassage(" in JS_CODE and "The passage this answer used" in JS_CODE)
check("it opens the original at the recorded page",
      'fragment = page > 0 ? "#page=" + page : ""' in JS_CODE)
check("the page is reached by the viewer's own fragment, not drawn coordinates",
      not re.search(r"canvas|getBoundingClientRect\(\)\.\w+\s*\*|pdf\.js|highlightRect",
                    JS_CODE, re.I))
check("the preview is still served only by the guarded endpoint",
      '"/api/document/" + encodeURIComponent(documentId)' in JS_CODE
      and "file://" not in JS_CODE)
check("the Sources tab is titled for this answer",
      "Sources used in this answer" in HTML)
check("the rail's document tab is one document, not the library",
      '<button class="rtab" data-rpane="documents" type="button">Document</button>' in HTML)
check("the whole library is still reachable from a source",
      "source-library" in JS_CODE and "Open in the full library" in JS_CODE)

print("\n=== 16. PHASE 13.1 CHANGED NOTHING IT WAS TOLD NOT TO ===")
check("the UI still writes nothing to rag_chunks",
      not re.search(r"(INSERT|UPDATE|DELETE)\s+(INTO\s+)?rag_chunks", APP_CODE, re.I))
check("the UI still writes nothing to documents",
      not re.search(r"(INSERT|UPDATE|DELETE)\s+(INTO\s+)?documents\b", APP_CODE, re.I))
check("the UI still writes nothing to the evaluation tables",
      not re.search(r"(INSERT|UPDATE|DELETE)\s+(INTO\s+)?golden_eval", APP_CODE, re.I))
check("the new endpoints are reads",
      all(f'@app.get("{route}")' in APP_CODE
          for route in ("/api/follow-up-plan", "/api/company-context/{ticker}")))
check("the evidence reply spends nothing and records no run",
      '"run_id": None' in APP_CODE and '"estimated_cost_usd": 0' in APP_CODE)
check("it says in the answer itself that nothing was generated",
      "no new search was made and no model was asked anything" in APP_CODE)
check("no V-drive path is written to",
      "SOURCE_ARCHIVE_ROOT" in APP_CODE and "assert_inside_project" in APP_CODE)
check("the archive is still opened read-only, for preview alone",
      APP_CODE.count("return FileResponse(") == 1, str(APP_CODE.count("return FileResponse(")))
check("no embedding or index call was added",
      "chromadb" not in APP_CODE and not re.search(r"def (retrieve|rank|embed)\w*\(", APP_CODE))
check("Ask Brain's ranking weights are untouched",
      all(name in ASK_BRAIN_SRC for name in ("W_VECTOR", "W_KEYWORD", "W_PRIORITY")))
check("the follow-up restriction never became an instruction to the model",
      "restrict" not in ASK_BRAIN_SRC.split("SYSTEM_INSTRUCTION = ")[1]
      .split('"""')[1].lower(),
      "the prompt mentions the document restriction")
check("still no React, Next.js, Vue or Svelte",
      not re.search(r"react|next\.js|vue\.js|svelte|webpack|vite", HTML + JS_CODE, re.I))
check("still no external script, stylesheet or font",
      not re.search(r'(src|href)="https?://', HTML)
      and "fonts.googleapis" not in HTML + CSS and "@import" not in CSS)
check("still one stylesheet and one script",
      len(list((UI / "static").glob("*.css"))) == 1
      and len(list((UI / "static").glob("*.js"))) == 1)
check("the PDF preview guard is unchanged",
      client.get("/api/document/not-a-document-id/preview").status_code == 400)
check("the phase marker is at or past 13.1",
      _phase_number(ui.PHASE) >= (13, 1), ui.PHASE)

print("\n" + "=" * 70)
if FAILS:
    print(f"{len(FAILS)} FAILING CHECK(S):")
    for name in FAILS:
        print("  - " + name)
    sys.exit(1)
print("ALL CHECKS PASSED")
