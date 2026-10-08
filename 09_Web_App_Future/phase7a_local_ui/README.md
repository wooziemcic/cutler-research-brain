# Cutler Research Brain local UI (phase13.1-1.0)

A local web application with a dark navy sidebar and three working views: **Ask Brain**, which
runs Ask Brain for MSFT so nobody has to type a PowerShell command; **Documents**, which browses
the whole indexed archive, reads the text extracted from each file and previews indexed PDFs; and
**System health**, a read-only dashboard of what the pipeline has and has not done.

It is a **window onto the pipeline, not a new pipeline**. It does not retrieve, rank, embed or
answer anything itself. Every question is handed to `scripts/ask_brain.py` exactly as if it had
been typed at the prompt, and every answer, log, Obsidian note and database row is produced by
that script and nothing else.

> **AI drafts. Humans verify. Cutler decides.**

---

## Install

```bat
py -3.11 -m pip install fastapi uvicorn jinja2 python-multipart
```

(These are also in the project's `requirements.txt`.)

## Run

```bat
cd "C:\Users\AI Admin\Documents\CutlerResearchBrain\09_Web_App_Future\phase7a_local_ui"
py -3.11 -m uvicorn app:app --reload --host 127.0.0.1 --port 8000
```

Then open **http://127.0.0.1:8000**

Stop it with `Ctrl+C`.

`--host 127.0.0.1` is not decoration. There is **no authentication** — anyone who can reach the
port can ask questions and read every answer in the archive. Do not change the host to `0.0.0.0`
and do not put it behind a tunnel or a port forward.

The app runs Ask Brain with **the same Python that is running the server** (`sys.executable`), so
launching uvicorn with `py -3.11` is what makes Ask Brain run under 3.11 too.

---

## The shell (Phase 10C)

**Sidebar.** Cutler Capital Management LLC / Research Brain, then the views: Ask Brain and
Documents under *Workspace*; System health, Evaluation and Ingestion under *System*. Under *Coming
later*: Companies, Themes, Timeline, Reports, Settings — present and visibly disabled, because a
navigation that hides what is planned tells you less than one that shows it greyed out.

**Header.** The product name, the subtitle, a **system status chip** (Healthy / Needs review /
Missing files, with the reasons in its tooltip) and the creed, on every screen.

**Workspace.** Exactly one view is on screen at a time. Ask Brain and System health scroll the
page; Documents is a fixed three-panel frame whose panels scroll individually. Nothing is a page reload; this is still one
HTML document, one stylesheet and one script, all served from disk.

---

## Ask Brain

**Left** — the **ticker** (which decides *which index answers*, so it sits with the question, not
in Advanced controls), the question itself, four presets that fill both the question and the
settings they work best with, an **Advanced controls** panel (collapsed, with its current settings summarised on the
summary line so a hidden setting can never silently change an answer), and an **Evidence filters**
card: source type and document type as dropdowns built from what is actually in the index, a date
range, and an exact text filter.

**Middle** — a **summary card** (run, status, all three confidences, retrieval mode, priority,
filters, evidence counts, cost, and every file path with a copy button), a traffic-lit **Audit
checks** box that explains each flag in plain English, the answer itself, a **Sources used**
panel showing exactly what the model was given, and a collapsible **Retrieval diagnostics** table
of the top 20 candidates with their scores and the reason each was or was not selected.

Each **source card** carries its citation number, file name, date, source and document type, page
range, the retrieval scores, a **cited / not cited** chip, a chip saying whether that document's
original can be previewed, and an **Open document** button that takes you straight to it.

**Right rail** — last evaluation, **Questions needing review** banded serious / weak /
reading-flags-only with the serious ones first, and recent answers. Click either to reload an answer; it reads from disk, so
it does not re-ask the question and costs nothing.

`Ctrl+Enter` in the question box asks.

---

## Documents — a three-panel workspace (Phase 10A/10B, rebuilt in 10C.1)

The second view answers the question the Ask Brain view cannot: *what is actually in here, and what
does this particular file say?*

It is three panels side by side, each scrolling on its own inside one fixed-height frame. **The
page itself does not scroll.** That is what removed the three faults of the original stacked
layout: the reader opened underneath the list, the pagination and the reader fought for the same
space, and the page footer floated across the rows.

**Left — filters.** Free text across file name, path and ticker; ticker, source type and document
type dropdowns built from the database; RAG-ready; a date range; and a checkbox for documents whose
source file is no longer where it was indexed.

**Middle — the library.** One entry per document, not a spreadsheet row: the file name leads, the
date sits opposite it, source and document type read underneath, and the rest are chips — ticker,
extraction status, indexed or not, source present or missing. **Clicking anywhere on an entry opens
it**, and the open one stays marked while the list scrolls. The count is in the panel header and
the pager is pinned to the panel's foot. The list holds metadata only — never document text.

**Right — the reader.** The document opens *beside* the list, not below it. Title, status chips,
the missing-source warning when there is one, the V-drive path, then six tabs. The reader's body
scrolls independently of the list, so you can keep your place in both.

If you open a document from an Ask Brain citation while the list is filtered to something else, the
library says so rather than leaving the two panels looking unrelated.

The six panes:

| Pane | What it shows |
|---|---|
| Overview | Every metadata field the database holds, including the Phase 9 ingestion columns when they exist |
| Document card | The markdown card from `04_Document_Cards`, rendered |
| Extracted text | The text from `03_Extracted_Text`, first 12,000 characters, with in-document search that returns matching snippets |
| Pages | Per-page text files when the extractor wrote them; click a page and its text opens below the page buttons |
| Chunks | The RAG chunks for this document, in order, with a 600-character preview each |
| Original preview | The indexed PDF itself, filling the reader panel (Phase 10B) |

Above the panes is the document's **V-drive path** and a **Copy V-drive path** button. Indexed
PDFs open in the Original preview pane, which takes whatever height the reader panel has left; for
anything else, copy the path and open it in File Explorer. See the boundary below.

Citations in the Ask Brain view carry an **Open document** button, which switches to the
Documents view with that document already open. That is the loop the phase exists to close: read an
answer, click the evidence, read the source text it came from.

### Original preview, and the line around it (Phase 10B)

The sixth pane shows the original PDF, rendered in the page. One endpoint may open one file, and
it is fenced on four sides:

1. **It takes a document id, never a path.** The id must match `doc_` + 4-64 characters or the
   request is refused with 400 before anything is touched. The path comes from the database row,
   and the server builds it.
2. **The path must resolve under the configured archive root** (`CUTLER_SOURCE_ROOT`, default
   `V:\Companies\A Current Month`). Resolved first, then compared, so `..`, encoded `..` and
   symlinks all fail the same check. A path inside the project's own folders is refused too.
3. **It must be a `.pdf`.** Anything else returns 415 and the pane says *"Original preview is only
   enabled for PDFs in Phase 10B."*
4. **It is read and streamed, never copied.** No write, no rename, no move, no temporary copy into
   the project. The file stays exactly where the archive has it.

Everything else in the Documents tab is still extracted text from inside the project. The V Drive
remains read-only: this phase reads one file for viewing and writes nothing to it, ever.

Query parameters are not read by the preview endpoint at all, so `?path=`, `?file=` and their
relatives are inert rather than merely rejected. Each attempt is recorded in
`logs\phase10b_preview_access.log` - timestamp, document id, file name, served or refused - and
never any file content.

Copy the path and open the file yourself when you need more than a viewer: printing, annotating,
another file type, or a page the browser renders poorly.

A missing source file **hides nothing**. The document still lists, still opens, still shows its
metadata, card, text, pages and chunks; the detail card simply carries a warning that the original
was not found at its last indexed path — and that warning says to confirm the V drive was fully
mounted before treating the file as deleted, because an unmounted share looks exactly like a
deletion.

---

## System health

A read-only dashboard assembled from endpoints that already existed — documents indexed, MSFT
chunks, missing source files, last ingestion, last evaluation, how far the index is behind the
archive, and both version numbers — followed by the ingestion and evaluation detail and every field
the health endpoint reports.

There is deliberately **no button on that page that starts anything**. Ingestion and evaluation
cost time and money and are started from the command line on purpose; a dashboard that can spend
money is not a dashboard.

A database that predates the Phase 9 ingestion columns cannot say which source files are missing.
The tile says **unknown** rather than zero, because zero would be a claim.

---

## Endpoints

| Method | Path | What it does |
|---|---|---|
| GET | `/` | The page |
| GET | `/api/health` | Index presence, MSFT chunk/document counts, latest run, versions, warnings |
| POST | `/api/ask` | Validates input, runs Ask Brain once, returns the answer and every path |
| GET | `/api/runs?limit=25` | Recent runs from `ask_brain_runs`, newest first |
| GET | `/api/run/{run_id}` | One run: metadata, answer markdown, retrieval summary with a `cited` flag per chunk, audit classification, diagnostics path |
| GET | `/api/diagnostics/{run_id}?limit=20` | The per-candidate retrieval trace for one run, parsed from the CSV Ask Brain wrote |
| GET | `/api/review-queue?limit=20` | Runs worth a second look, banded by severity |
| GET | `/api/documents` | The library: filtered, paged metadata. Never document text |
| GET | `/api/document-filters` | The dropdown vocabularies, built from the database |
| GET | `/api/tickers` | **Phase 11.** Every known ticker and whether it has a usable index |
| GET | `/api/document/{document_id}` | One document: metadata, source presence, which panes have content |
| GET | `/api/document/{document_id}/card` | The document card markdown |
| GET | `/api/document/{document_id}/text` | Extracted text; `search=` (or `q=`) returns snippets, `page=` returns one page |
| GET | `/api/document/{document_id}/pages` | The per-page list; `page=` returns that page's text |
| GET | `/api/document/{document_id}/chunks` | The RAG chunks for this document, with previews |
| GET | `/api/document/{document_id}/source-path` | The V-drive path as text, plus whether the file is still there |
| GET | `/api/document/{document_id}/preview` | **Phase 10B.** Streams the indexed PDF inline. The only endpoint that opens an original file |

Every one of those takes a **document id**, never a path. The id must match `doc_` followed by
4-64 lowercase hex-ish characters or the request is rejected with 400 before anything is opened;
the path is then built by the server from the project root. There is no endpoint anywhere in this
app that accepts a file path from the browser.

Every response from `/text` names the `mode` it is answering in - `document_preview`, `search`
or `page` - along with `chars_returned`, `total_chars` and `truncated`, so a caller can always
tell what it was given. A search accepts **either** `search=` or `q=`: one spelling silently
returning the whole document instead of snippets is exactly the kind of quiet wrong answer this
system is built to avoid. Snippets carry the page they came from when per-page text exists, and
matches close together collapse into one passage rather than printing the same paragraph twice.

There is no interactive API documentation page: `docs_url` and `redoc_url` are switched off,
because a demo tool does not need a second surface.

---

## How it stays safe

- **No shell.** Ask Brain is launched with an argument **list** and `shell=False`. A question
  containing quotes, semicolons or ampersands is one argument, never a command. There is a test
  that asks a shell-shaped question and checks that nothing executed.
- **Everything is validated before it reaches a command line.** Ticker against an allow-list
  (`MSFT` only), retrieval mode and source priority against fixed vocabularies, the three numbers
  against ranges, dates against `YYYY-MM-DD` *and* the calendar, every string length-capped and
  stripped of control characters.
- **One question at a time.** Two concurrent runs would both rewrite the same report files and
  race on SQLite, so a second request gets a clear "try again in a moment" rather than a corrupted
  record.
- **The app reads; Ask Brain writes.** `app.py` contains no `INSERT`, `UPDATE` or `DELETE`, does
  not touch Chroma, and makes no OpenAI call of its own. The only API calls in a question are the
  ones Ask Brain already makes: one embeddings call, one responses call.
- **No file outside the project root is ever read**, even if a database row points there. Every
  path is fully resolved *first* and then checked against the project root, so `..` segments,
  URL-encoded `..`, and symlinks are all caught by the same check.
- **Exactly one endpoint opens an original file**, and only after the four checks above. Every
  other endpoint still only `stat`s a source path. `FileResponse` appears once in the whole
  application, in `document_preview`, and there is a test that fails if a second one appears.
- **Nothing is ever written to the archive.** The app opens no file for writing anywhere except
  the preview access log inside the project.
- **Answer text is escaped before rendering.** The markdown renderer escapes all HTML first, then
  applies formatting, so model output cannot inject markup into the page.
- **The V Drive is never touched.**

---

## What this is not

No authentication. No deployment. No production hardening. No charts. No Streamlit, no React, no
CDN, no external font — every file loads from disk, so the page works with no network at all.

Phase 10C changed the shell and the styling only: no retrieval, answering, prompt, ingestion or
evaluation logic was touched, and no endpoint changed except one bug fix (see below). No preview of
anything but PDFs - other file types show their metadata, card, text and chunks and
say so plainly. No page-jump from a citation into the PDF, no annotation, no download button, and
no OCR. Ask Brain answers for any ticker that has been indexed —
MSFT plus the Phase 11 set as each is built. A ticker with no index is refused with *"This ticker
has not been indexed yet"*, and **never answered from another ticker's index**; the dropdown says
which is which and disables Ask for the rest. The Documents tab lists every ticker in the database
regardless, since listing metadata costs nothing.

It is a demo for one person on one machine.

---

## If something looks wrong

- **"No keyword index"** in the health panel — hybrid retrieval is silently falling back to
  vector-only. Run `scripts\build_keyword_index.py`.
- **An answer says no evidence was found** — that is the system working. Ask Brain does not answer
  without retrieved evidence, and if filters emptied the candidate set the reply names the filter
  responsible.
- **A run fails** — open the console section under the answer, then `logs\phase5b_ask_brain.log`.
- **Odd characters in the console** — Ask Brain asks Windows for UTF-8 and the UI runs it with
  `PYTHONUTF8=1`, so answers containing a non-breaking hyphen or a curly quote print correctly. If
  a terminal still cannot draw one it becomes `?` on screen only; the file on disk always keeps
  the original character.
- **The page loads but every panel is empty** — the server cannot find the database. Check the
  path printed in the terminal when the server started.
- **A document's text pane says nothing was extracted** — the extraction never produced a file for
  it, or the file was moved. Check `03_Extracted_Text` and the document's extraction status in the
  Overview pane; `scripts\run_incremental_ingestion.py --retry-failed-extraction` is the repair.
- **The preview frame is blank but everything else works** - some browsers refuse to render a PDF
  inline. The file is still there: copy the path from the pane and open it yourself. The pane says
  this too.
- **Every preview returns 403** - the archive root does not match where the index thinks files
  live. Check `CUTLER_SOURCE_ROOT`, or the `archive_root` value in any document's `preview` block.
- **"Missing source files: unknown" on the System health page** — this index has not had the
  Phase 9 ingestion migrations, so that state was never recorded. Run an incremental ingestion.
- **A document is flagged as missing its source** — before treating it as deleted, confirm the V
  drive was actually mounted when the last ingestion ran. An unmounted share reports every file as
  missing.

Every answer here is a draft assembled from quotations. A citation means a chunk was handed to
the model, not that the claim is true. Open the cited document before using any of it.


---

## Phase 13 — the research workspace

Phase 10C built a shell around an engineering console. Everything was visible at once: three
cards of controls beside the answer, a right rail of three more, the vocabulary of the pipeline on
the front page, and a navigation that listed five things that did not exist. Phase 13 is the same
product, arranged the way someone who reads research all day would expect.

### Navigation

Seven views, each listed **once**:

| View | What it is | Where it came from |
|---|---|---|
| New Research | ask, read, follow up | the old Ask Brain view |
| History | every question asked, and the ones needing a read | the old right rail |
| Companies | the ten indexed companies | new, light |
| Themes | how the archive groups by subject | new, light |
| Documents | the archive, and the reader | unchanged |
| Evaluations | the golden evaluation dashboard | the old System card |
| System | index health, ingestion, pipeline status | unchanged |

Phase 10C had "Evaluation" and "Ingestion" as sidebar entries that scrolled the System page, and
five disabled "coming later" items. Both are gone: every entry now goes somewhere, and nothing is
reachable from two places.

### The ask page

One question box, a Quick Answer / Deep Research choice, and suggested questions. After an answer:
the question becomes the heading, a **trust summary** sits above the text, and a follow-up box
waits at the foot of the column. The right panel carries Sources, Documents, Deep Dive, Timeline,
Company and Related.

The trust summary is a translation, not a new judgement. The pipeline already recorded all of it:

```
confidence = Medium, company_direct = 0, source_kind = analyst, selected_chunks = 8
->  Medium confidence
    8 sources reviewed across 6 documents · Mostly analyst research
    · No direct company source used · Sources from 2026-04-02 to 2026-09-10
```

### Where the technical detail went

Nothing was removed. It moved one click away:

| Was on the front page | Is now |
|---|---|
| retrieval mode, source priority, top_k, chunk counts | **Advanced controls and filters** drawer |
| the evidence filters (source type, document type, dates, must-contain) | same drawer |
| the fifteen-row run summary | **Deep Dive** tab |
| retrieval diagnostics | **Deep Dive** tab |
| vector / keyword / combined scores and the chunk id, on every source | **Why this passage** on each source |
| the answer file's header — model names, token budgets, the rule sentence | the answer file, which Deep Dive links to |

The words changed too, where they were the pipeline's rather than a reader's: chunks are
*passages*, "RAG ready" is *searchable*, and the run's own header no longer opens the answer.

### Quick Answer and Deep Research

UI-level routing over parameters `ask_brain.py` has accepted since Phase 6. There is no second
retrieval system, no new ranking and no changed weight.

| | top_k | passages read | per document |
|---|---:|---:|---:|
| Quick Answer | 30 | 8 | 2 |
| Deep Research | 60 | 16 | 3 |

The mapping lives in `DEPTH_PRESETS` in `app.py`, in one place, so the button and the run cannot
disagree. A request that sends no `depth` keeps whatever numbers it sent — every pre-Phase-13
caller behaves exactly as before.

### New endpoints

All read-only: `/api/history`, `/api/companies`, `/api/themes`, `/api/evaluation-dashboard`,
`/api/evaluation-questions`. Every endpoint that existed before Phase 13 still answers, unchanged;
`/api/run/{id}` and `/api/ask` gained a `trust` block alongside their existing fields rather than
instead of them.

---

## Phase 13.1 — the interactions Phase 13 left broken

Phase 13 built the workspace. Using it exposed five defects, and all five were the same
defect wearing different clothes: **the page threw away its context between one action and
the next.**

The rule this phase works to: context is **explicit and named**. There is no conversation
buffer, no session memory and no agent. A follow-up carries one previous run id, and the
server looks up what that run actually cited. What the pipeline was asked is always visible
in the run row, which is what keeps a run reproducible.

### 1. The question composer

The text box and the send button are now separate cells of a CSS grid, so no amount of
typing can put a character underneath the button — that is a layout guarantee, not a
padding value that happens to be big enough today. The box grows from one line to a
168-pixel cap and then scrolls.

Autosize is a named function (`autosizeQuestion`) rather than an inline `input` handler,
because text arrives four ways — typed, from a suggestion chip, from a follow-up, and from
a cleared box — and only one of those is an input event. That was the bug: a suggestion
filled the box and the box stayed one line tall.

Asking no longer writes the word "Asking…" into a 34-pixel square button. It shows a busy
state and keeps the glyph.

### 2. Follow-up context

A follow-up sends two things and no more: `follow_up: true` and `previous_run_id`. The
server looks the rest up itself — the previous question, and the documents and chunks that
run actually cited — so the browser can never claim an answer cited something it did not.

`classify_follow_up()` sorts a follow-up into one of three kinds with regular expressions.
No model is asked to classify anything.

| Kind | Example | What happens | Cost |
|---|---|---|---|
| `evidence_reference` | "Which documents support this?", "Show me the source." | The previous run's evidence list is read back and formatted. No search, no model call, no new run row. | nothing |
| `contextual` | "Why?", "Tell me more about the second point." | The question is composed with the previous question named once, and retrieval is filtered to the documents the previous answer came from. | one normal run |
| `new_subject` | "What does Kroger report about grocery margins?" | Fresh retrieval across the whole index. Context is dropped. | one normal run |

A follow-up that names a different company is always `new_subject`, and the page says so in
a warning: the question still went to the company in the box, and reading a refusal as "the
archive has nothing" would be wrong.

`GET /api/follow-up-plan?question=…&ticker=…&previous_run_id=…` returns the decision without
acting on it, so the page can tell a reader what pressing the button will do before anything
is spent.

**How a contextual follow-up reaches Ask Brain:** as a **filter**, `--restrict-to-documents`,
applied in `filter_chunks()` beside `--date-from`, `--source-type` and the rest — before
ranking, touching no weight, no prompt and no score. Empty (the default) changes nothing,
and the filter is named in the run's own `filter_summary`, so a restricted run says so.

### 3. The right panel is always restorable

Collapsing used to toggle a class, which meant the button that hid the panel went with it.
`setPanelHidden()` owns that state now, and a **Sources** tab sits on the right edge of the
workspace whenever the panel is hidden. Opening a citation or a source restores the panel by
itself. Nothing requires navigating away and back.

### 4. Changing company changes the research context

`GET /api/company-context/{ticker}` returns everything one company change needs: the
company's name, four suggested questions, a placeholder, and its document counts. Changing
the select clears the answer that was about the last company — an answer about Kroger
sitting under a Kimco heading is worse than an empty screen.

The four suggested questions are **read from the Phase 12 golden question file**, which was
written by hand per company. They are not generated: a suggestion produced by an API call
would cost money, differ between page loads, and put unreviewed text in front of a reader as
if the system endorsed it. The two Phase 12 categories that exist to prove the system
refuses are deliberately not offered. `T` reads as **AT&T** and `B` as **Barnes Group**
everywhere.

### 5. Answer → citation → document

Every `[n]` in an answer is a control. Clicking it restores the panel, selects source *n*,
and opens the document behind it in the rail, beside the answer — verifying a citation
should not cost you the answer you were reading.

Each source is now a structured **evidence record**: citation number, document id, chunk id,
title, source type, publisher, document date, page range, the ticker, and the exact
retrieved passage. The passage is read from `rag_chunks` by `(chunk_id, ticker)` — the
Phase 11.3 key — and never by `chunk_id` alone, with no fallback.

The rail shows the passage first, then the original PDF opened at the recorded page using
the viewer's own `#page=` fragment. **There is no coordinate highlighting.** A highlight that
is a few millimetres wrong is worse than no highlight, because it points at the wrong
sentence with total confidence. The preview is still served only by the guarded
`/api/document/{id}/preview` endpoint; no path ever reaches the browser.

The Sources tab is titled *Sources used in this answer*; the rail's other tab is
**Document** — singular, the one behind the source just opened. The whole archive is still
the **Documents** page in the sidebar, and each source links to it.

### Phase 13.1 endpoints

Two, both GETs: `/api/follow-up-plan` and `/api/company-context/{ticker}`. `/api/run/{id}`
and `/api/ask` gained an `evidence` list and a `follow_up` block alongside their existing
fields rather than instead of them.
