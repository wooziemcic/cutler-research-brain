# Phase 7B status — UI diagnostics and demo polish

_Cutler Capital Management LLC — internal. Phases 1 through 7A.1 approved; Phase 7B delivered._

> **AI drafts. Humans verify. Cutler decides.**

UI version **phase7b-1.0**. Same folder, same stack, same single page: FastAPI + Jinja2 + plain
CSS + vanilla JavaScript. No framework was added, and nothing about retrieval, answering,
embeddings, the Chroma store or the keyword index was touched.

---

## What changed from 7A

Phase 7A proved the plumbing worked. Phase 7B makes it legible to someone who did not build it.

| | 7A | 7B |
|---|---|---|
| Dropdowns | raw backend values (`company`, `hybrid`) | plain labels (*Company / SEC first*, *Hybrid: vector + keyword*) mapped to the same values |
| Filters | collapsed `<details>`, free-text with autocomplete | their own **Evidence filters** card, real dropdowns fed by the index, a live "Active:" line, and a Clear button |
| Starting a question | type it yourself | four **demo presets** that fill the question *and* the settings it works best with |
| Answer header | a badge and a few counts | a **summary card**: run, status, all three confidences, retrieval mode, priority, filters, evidence counts, cost, and every file path with a **copy button** |
| Sources | dense markdown table inside the answer | a **Sources used** panel: one card per chunk, colour-coded company/SEC vs analyst/industry, scores shown, and evidence the answer never cited marked as such |
| Diagnostics | a path in the answer file | a collapsible **Retrieval diagnostics** panel: candidates considered, averages, and the top 20 candidates with vector/keyword/combined scores and the reason each was or was not selected |
| Audit flags | a yellow list | a traffic-lit **Audit checks** box with a plain-English explanation of every flag |
| Trust | you had to remember which answers were weak | a **Questions needing review** panel, sorted by severity |
| Health | five lines | indexes, counts, both versions, questions asked, how many need review, and warnings |

New endpoints: `GET /api/review-queue` and `GET /api/diagnostics/{run_id}`. Existing endpoints
gained fields; none changed shape or was removed, so anything built against 7A still works.

---

## How to run it

Unchanged from 7A:

```bat
cd "C:\Users\AI Admin\Documents\CutlerResearchBrain\09_Web_App_Future\phase7a_local_ui"
py -3.11 -m uvicorn app:app --reload --host 127.0.0.1 --port 8000
```

Open **http://127.0.0.1:8000**. `Ctrl+C` stops it. No new dependencies.

---

## The demo presets

Each fills the question box and the settings, and then stops. Nothing runs until you press Ask.

| Preset | Question | Settings |
|---|---|---|
| **Azure growth from filings** | What does Microsoft report about Azure and other cloud services revenue growth? | hybrid · company · source type **SEC Filing** |
| **Analyst concerns** | What does the retrieved source text say analysts were concerned about for Microsoft? | hybrid · analyst |
| **FY26 operating margin** | What does the retrieved source text say about Microsoft's FY26 operating margin guidance? | hybrid · balanced |
| **AI capex and margin pressure** | What does the retrieved source text say about AI capex, depreciation, and margin pressure for Microsoft? | hybrid · analyst |

All four use top_k 30, 8 evidence chunks, 2 per document.

The third one is the honest one to demo. It is the question the archive answers *badly*, and it
shows the system saying so: the answer reports that the evidence is related but not directly
responsive, and confidence is capped at Low. A demo that only shows the good cases teaches people
to trust the tool more than they should.

---

## Reading the screen

**Audit checks** is traffic-lit:

- **Green** — every automated check passed. Clean, not verified. Still open the cited documents.
- **Amber** — something to read carefully. This is the normal state of a good answer. The most
  common flag, *supplied chunks not cited*, just means the model did not need every chunk it was
  given, which is what a deliberately wide evidence pack looks like when it is working.
- **Red** — only two things get here: wording this project does not allow (a buy/sell call, a
  rating, a prediction), or a citation pointing at evidence that was never supplied. Do not use
  the answer until a person has reviewed it.

Every flag carries a one-line explanation. Nothing is hidden at any level.

**Questions needing review** uses the criteria in the brief — Low confidence, any audit flag, a
directness cap, or a run that did not complete — and then sorts by how much it matters:

- **serious** — unsafe wording or a citation pointing at nothing;
- **weak** — the answer itself did not stand up: Low confidence, a directness cap, or a run that
  did not finish;
- **minor** — reading flags only.

That ordering exists because of something the test run showed: on the sandbox fixture, *20 of 43
runs* matched "audit flags not empty", since almost every good answer carries one. A queue where
everything is flagged is a queue nobody reads. Nothing is removed from it — the counts at the top
say how many fall into each band, and the serious ones are first.

**Sources used** shows the evidence in citation order, with company/SEC material and
analyst/industry research colour-coded on the left edge, and any chunk the answer never cited
marked *not cited*. It is read-only.

---

## Safety notes

Everything from 7A still holds, and 7B added no new attack surface — the two new endpoints are
read-only and take an integer run id.

- **No shell, ever.** Ask Brain is launched with an argument list and `shell=False`.
- **Every input validated before it reaches a command line.** MSFT-only allow-list enforced
  server-side; mode and priority against fixed vocabularies; numbers against ranges; dates
  against the calendar; strings length-capped and stripped of control characters.
- **One question at a time**, so two runs cannot race on SQLite or the report files.
- **The app reads; Ask Brain writes.** No `INSERT`/`UPDATE`/`DELETE`, no Chroma access, no OpenAI
  call of its own. The only API calls in a question are Ask Brain's own: one embeddings, one
  responses.
- **No file outside the project root is ever read**, including the diagnostics CSV, whose path
  comes from a database row and is checked before it is opened.
- **Everything rendered is escaped first** — answer markdown, file names, questions, selection
  reasons. Model output cannot inject markup into the page.
- **The V Drive is never touched.**
- **No authentication, localhost only.** Anyone who can reach the port can read every answer in
  the archive. Do not move it to `0.0.0.0`, tunnel it, or port-forward it.

---

## Limitations

Everything in `PHASE7A_STATUS.md` still applies (one question at a time, no streaming, no cancel,
~1s subprocess overhead, small markdown subset, laptop layout). Phase 7B adds:

1. **The diagnostics panel shows the top 20 candidates.** The full trace is in the CSV; the API
   will go to 200 with `?limit=`, but the panel deliberately does not.
2. **Loading an old run re-reads its files.** If an answer file was moved or deleted, the panel
   says so rather than reconstructing it — by design, since the file is the record.
3. **Severity banding is a heuristic**, not a policy. It ranks; it never removes. A "minor" run
   can still be wrong, which is why the flags are all still shown.
4. **The review queue reads the whole run history**, so it grows with use. It is capped at 20 in
   the panel and 100 in the API.
5. **No search over past questions.** The panels are chronological.
6. **Copy buttons copy a path, not a file.** The browser cannot open local files, and this app
   does not try.

---

## What is not started

No Phase 7C. No authentication, no accounts, no roles. No deployment, no service install, no
HTTPS, no exposure beyond localhost. No charts. No other ticker. No React, no Streamlit, no CDN,
no external font. No changes to retrieval, answer generation, embeddings, the Chroma store or the
keyword index — Phase 7B is a display layer over what Phases 1 to 6.1 already produced.

---

## Testing performed (cloud container, fully offline)

A dedicated Phase 7B suite (~120 checks) plus every earlier suite re-run, all passing. Ask Brain
was launched as a real subprocess pointed at a shim that stubs the OpenAI client, so no network
call was made and no key was needed.

Phase 7B covered: the phase label; all seven readable dropdown labels mapped to the four/three
unchanged backend values; the filters card as real dropdowns; all four presets defined with the
specified questions and settings, and confirmed not to auto-run; every health field including the
new ones; a full ask returning every summary field; copy buttons using `navigator.clipboard` with
an `execCommand` fallback and no `file://` anywhere; the sources panel's evidence list with the
`cited` flag computed against `citations_used`; the diagnostics endpoint with its columns, rank
ordering, limit clamping, 404 for an unknown run, and the exact "Diagnostics were not recorded for
this run." message when the CSV is missing; the audit classifier against seven hand-built cases
(clean, unused evidence, uncited sentence, directness cap, unknown citation, unsafe wording, cap
via column); the review queue matching the database criteria exactly, carrying severity, sorting
serious first and newest-first within each band, and hiding nothing; loading an old run creating
no new run; and a full regression sweep confirming no shell use, no database writes, no Chroma
access, no OpenAI call, no V-drive path, the MSFT allow-list, the UTF-8 child environment, the
path-traversal guard, the escaping, and that a shell-shaped question is still inert.
