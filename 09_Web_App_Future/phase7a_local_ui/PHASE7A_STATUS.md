# Phase 7A status — MSFT Ask Brain local demo UI

_Cutler Capital Management LLC — internal. Phases 1 through 6.1 approved; Phase 7A delivered,
then extended by 7A.1 (console encoding) and 7B (diagnostics and demo polish)._

> **This file describes the 7A foundation and is still accurate about it.** The UI has since
> moved to **phase7b-1.0** — readable controls, demo presets, an answer summary card with copy
> buttons, a sources panel, a retrieval diagnostics table, an explained audit box and a review
> queue. See `PHASE7B_STATUS.md` for what changed and why.

> **AI drafts. Humans verify. Cutler decides.**

---

## What was built

A local web page that runs Ask Brain for MSFT so nobody has to type a PowerShell command.

It is a **window onto the pipeline, not a new pipeline**. It performs no retrieval, no ranking,
no embedding and no answering of its own. Every question is handed to `scripts/ask_brain.py`
exactly as if it had been typed at the prompt; every answer, retrieval log, Obsidian note,
diagnostics file and database row is produced by that script and nothing else. If the UI were
deleted tomorrow, nothing about the archive or the pipeline would change.

```
09_Web_App_Future\phase7a_local_ui\
├─ app.py                 FastAPI backend, 5 endpoints
├─ templates\index.html   the single page
├─ static\styles.css      plain CSS, no framework
├─ static\app.js          vanilla JS, includes a small escaping markdown renderer
├─ README.md              how to run it and how it stays safe
└─ PHASE7A_STATUS.md      this file
```

Stack: FastAPI + uvicorn + Jinja2 + plain HTML/CSS/JS. No Streamlit, no React, no CDN, no
external font. Every asset loads from disk, so the page works with no network at all.

---

## How to run it

```bat
py -3.11 -m pip install fastapi uvicorn jinja2 python-multipart

cd "C:\Users\AI Admin\Documents\CutlerResearchBrain\09_Web_App_Future\phase7a_local_ui"
py -3.11 -m uvicorn app:app --reload --host 127.0.0.1 --port 8000
```

Open **http://127.0.0.1:8000**. Stop with `Ctrl+C`.

The app runs Ask Brain with the **same interpreter that is running the server**
(`sys.executable`), so launching uvicorn with `py -3.11` is what makes Ask Brain run under 3.11.

---

## Endpoints

| Method | Path | Returns |
|---|---|---|
| GET | `/` | The page |
| GET | `/api/health` | `status`, `project_root`, `db_exists`, `chroma_exists`, `keyword_index_exists`, `msft_chunks`, `msft_documents`, `latest_ask_run`, `pipeline_version`, plus the source/document type lists that populate the filter autocompletes, and any warnings |
| POST | `/api/ask` | `status`, `answer_markdown`, `run_id`, `answer_path`, `obsidian_path`, `retrieval_log_path`, `final_confidence`, `citations_used`, `audit_flags`, `stdout_summary`, `error_message`, plus the confidence detail, filter summary and evidence counts |
| GET | `/api/runs?limit=25` | Recent runs from `ask_brain_runs`, newest first |
| GET | `/api/run/{run_id}` | Run metadata, the answer markdown, a retrieval-log summary with the evidence list, and the diagnostics CSV path |

`docs_url` and `redoc_url` are switched off. A demo tool does not need a second surface.

---

## Safety notes

- **No shell, ever.** Ask Brain is launched with an argument **list** and `shell=False`. A
  question containing quotes, semicolons or ampersands is one argument, never a command. A test
  asks a deliberately shell-shaped question and confirms nothing executed.
- **Everything is validated before it reaches a command line.** Ticker against an allow-list
  (`MSFT` only — enforced server-side, not merely absent from the dropdown), retrieval mode and
  source priority against fixed vocabularies, the three size settings against ranges, dates
  against `YYYY-MM-DD` *and* the calendar, every string length-capped and stripped of control
  characters.
- **One question at a time.** Two concurrent runs would both rewrite the same report files and
  race on SQLite, so a second request gets HTTP 409 and a plain explanation rather than a
  corrupted record.
- **The app reads; Ask Brain writes.** `app.py` contains no `INSERT`, `UPDATE` or `DELETE`, never
  opens Chroma, and makes no OpenAI call of its own. The only API calls in a question are the ones
  Ask Brain already makes: one embeddings call, one responses call.
- **No file outside the project root is ever read**, even if a database row points there.
  Verified by pointing a run row at `../../../etc/passwd` and confirming the read is refused.
- **Answer text is escaped before rendering.** The markdown renderer escapes all HTML first and
  only then applies formatting, so model output cannot inject markup into the page.
- **The V Drive is never touched.** No path in this app refers to it.
- **Local only, no authentication.** Bound to `127.0.0.1` by the documented run command. Anyone
  who can reach the port can ask questions and read every answer in the archive, so it must not
  be moved to `0.0.0.0`, tunnelled, or port-forwarded.

---

## What Phase 7A does not do

- No authentication, no user accounts, no roles.
- No deployment, no service install, no production hardening, no HTTPS.
- No exposure beyond localhost.
- No charts, no dashboards, no analytics views.
- No other ticker — MSFT is the only index that exists, and the server rejects anything else.
- No embedding, no re-embedding, no Chroma rebuild, no keyword-index rebuild, no OCR.
- No editing of answers, no deleting of runs, no writing to the archive.
- No investment recommendations, and no answer from model memory — those boundaries live in
  `ask_brain.py` and are unchanged.

---

## Phase 7A.1 — Windows console encoding (fixed)

**Reported:** a run completed and wrote its answer, Obsidian note, retrieval log and database row
correctly, then `ask_brain.py` died printing the answer:

```
UnicodeEncodeError: 'charmap' codec can't encode character '\u2011'
```

**Cause:** U+2011 is a non-breaking hyphen — the character the answer model uses in "non‑GAAP". A
Windows console defaults to a legacy code page, usually cp1252, which cannot encode it. The
research was fine; only the display of it failed, and it failed loudly enough to look like a
failed run.

**Fixed in three places:**

1. `ask_brain.py` asks stdout and stderr for UTF-8 at startup (`configure_console_encoding()`),
   which removes the problem at the source. Every print goes through `safe_print()`, which falls
   back to replacement characters if a console still cannot render something, and `print_answer`
   is wrapped so a display failure can never undo a completed run.
2. The UI runs Ask Brain with `PYTHONUTF8=1` and `PYTHONIOENCODING=utf-8`, and reads its output
   as UTF-8 with `errors="replace"`, so even a stray byte cannot break the reply to the browser.
3. The same UTF-8 guard was added to the other console scripts — `search_rag_index.py`,
   `build_keyword_index.py` and all seven inspectors — because they print archive text and
   question text too, and the same crash was waiting in every one of them.

**What did not change:** nothing that reaches a file. The answer on disk keeps every character
exactly as the model wrote it; only the console gets a substitute when it cannot draw one. If
Ask Brain ever records a completed run but exits non-zero anyway, the UI now shows the answer
with a note explaining the exit code, rather than an error.

## Known limitations

1. **One question at a time.** By design, but it means a long-running question blocks the next
   one. The second caller is told to wait rather than being queued.
2. **No streaming.** The page waits for the whole answer. A filtered question over the full index
   can take the better part of a minute with nothing to watch. The busy panel says what is
   happening; it is not a progress bar.
3. **No cancel button.** Once a question is sent, it runs to completion or hits the 600-second
   timeout. Stopping the server does not stop an in-flight subprocess.
4. **Subprocess overhead.** Each question pays a fresh Python start plus imports (roughly a
   second). This is the price of isolation, and it is the right trade for a demo: the UI cannot
   corrupt Ask Brain's state because it does not share it.
5. **The markdown renderer is small on purpose.** Headings, bold, italics, code spans, lists,
   blockquotes, rules and tables — which is everything Ask Brain writes. Anything more exotic
   renders as plain text rather than being interpreted.
6. **Run history is capped at 25 rows** in the panel (`/api/runs?limit=` goes to 200). There is no
   search over past questions.
7. **Health counts come from SQLite, not from Chroma itself.** If the Chroma store were deleted
   while the database still had rows, the panel would show the chunk count but the folder check
   would read "no". Both are shown for that reason.
8. **No mobile layout.** It collapses to one column on a narrow window, but it is built for a
   laptop screen.

---

## Next steps (not started, not approved)

- Phase 7B: whatever the demo shows is actually missing. Worth capturing the first few sessions
  of real use before deciding — the useful list is the questions people try that the archive
  answers badly, and `inspect_retrieval_quality.py` already collects those.
- Candidates, in rough order of likely value: search over past questions; a side-by-side view of
  two retrieval modes on the same question; showing the evidence chunks inline under the answer
  rather than only their citations; export of an answer to `.docx`.
- Anything beyond one machine and one person needs authentication first, and that is a different
  conversation from a demo UI.

---

## Testing performed (cloud container, fully offline)

Around 130 automated checks, all passing. Ask Brain was launched as a **real subprocess** — the
same code path the UI uses in production — pointed at a shim that patches the OpenAI client, so
the argument handling, the run row, the files and the reports were all exercised without a
network call or a key.

Covered: the page and both static assets; every field on `/api/health`; thirteen validation
rejections including a shell-shaped question that wrote no file; a full ask with
`--source-type "SEC Filing"` producing an answer, an Obsidian note, a retrieval log and a
diagnostics CSV; a filter combination that empties the candidate set (status `no_evidence`, no
answer model call); `--no-save`; run listing with limit clamping; run detail with the evidence
list in citation order; path traversal refused; a missing file reported rather than crashed on;
markup in an answer served as text; the concurrency lock refusing a second question and releasing
afterwards; and a static sweep of `app.py` for shell use, database writes, Chroma access, OpenAI
calls, V-drive paths and the ticker allow-list.

The Phase 5B and Phase 6 suites were re-run afterwards and still pass. The MSFT chunk count was
unchanged by the whole exercise.
