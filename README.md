# Cutler Research Brain

An internal, AI-assisted research memory system for **Cutler Capital Management LLC**.

The long-term goal is to turn Cutler's research archive (roughly 2019 to present) into a
source-backed, searchable institutional research memory: metadata indexing, PDF extraction,
document cards, an Obsidian memory layer, retrieval-augmented answers, evaluation, and a
human review loop.

> ## AI drafts. Humans verify. Cutler decides.
>
> Nothing this system produces is a conclusion. Every automated value is a **guess**,
> labelled as a guess, and routed to a human for confirmation.

---

## Current status: Phase 14.1 (live answer grounding remediation)

**Phase 14.1 = the grounding regression the first live Phase 14 run exposed.** Phase 14's writing
changes worked - 555 words down to 433, repetition 0.10 to 0.02, boilerplate openings to zero,
answers in a readable length band from 5 to 11 - but uncited claims went from **0 to 12**, and an
investment-research product that loses grounding to gain readability has gained nothing. All 12
were classified before a line was changed. Three causes, and only one of them was a bad answer.
**Nine** were refusals: Phase 14 had told the model to say *what is missing* when evidence is
insufficient, it did exactly that, and the audit's refusal vocabulary - written in Phase 8.1 - had
never seen those wordings. **One** was not an uncited claim at all: the sentence splitter cut *"a
J.P. Morgan credit note identifies …"* in half and reported the half without the citation, so the
answer was fine and the reader was broken. **Two** were real: marked conclusions making substantive
research claims, written without citations. The fix for those is to cite them, not to exempt them,
so the prompt now says **CITE YOUR CONCLUSIONS** - a conclusion drawn from specific evidence carries
the citations of the claims it rests on - and the exemption was *narrowed*, not widened: it now
refuses any conclusion stating a direction, a comparison, a cause, a cross-source consensus or
anything about company performance, with or without a figure in it. Re-scoring the live answers
under the corrected audit leaves **9 uncited claims, every one a substantive uncited conclusion** -
which is the defect, correctly located. Quick Answer also now caps a supporting point at ~35 words
after one answer packed three analyst quotations into a single bullet, and an unsupported answer is
capped at two sentences so it stops becoming a mini research note. Retrieval, indexing, ticker
isolation, refusals, ceilings, follow-ups and the citation-to-PDF path are untouched. Detail:
`claude/phase141_status.md`.

## Phase 14 (answer quality)

**Phase 14 = the answers read like research notes instead of transcripts.** The system was
already grounded, cited and audited; it was not yet readable. Measured across the 30 real answers
from the Phase 12 golden evaluation, the average answer ran **555 words**, of which **374 were
apparatus** (the evidence list, the citation list, the limitations and the review notes) and 163
were the answer; **not one of the 30** said what the evidence added up to, and only 5 of the 16
substantive answers sat inside a readable length. A PM got a correct list of quotes and had to do
the synthesis themselves. Phase 14 rewrote the prompt as a five-group hierarchy - grounding,
attribution, structure, confidence/refusal, format - with **grounding declared absolute in the
prompt itself**, so a style rule can never outrank a factual one. The answer now leads with the
finding rather than with where the finding came from, carries two to four non-redundant points,
and where the evidence supports it closes with one sentence on why it matters or what changed.
Quick Answer and Deep Research now *write* differently as well as read differently: Deep spends
its extra room naming where sources disagree, not on more adjectives. Two things made this safe
rather than cosmetic. A conclusion drawn from already-cited claims is exempt from needing its own
citation - but **only** if it is explicitly marked as a conclusion and contains **no figure**,
because a number is a fact and belongs to a source. And because the brief asks answers to name
the publisher ("Bernstein's note expects...") rather than say "analysts", the attribution checks
were taught to recognise a named publisher, using the pack's own recorded vendor names - without
that, a better-attributed answer would have scored worse. Nothing about retrieval moved: the
hybrid weights, the selection penalties, the ticker isolation, the index and the archive are
byte-for-byte what they were. Detail: `claude/phase14_status.md`.

## Phase 13.1 (research workspace interaction fixes)

**Phase 13.1 = the five things that broke when a person actually used Phase 13.** All five had the
same cause: the page threw away its context between one action and the next. The question box grew
underneath its own send button; a follow-up was a brand new question, so "why?" searched the archive
for the word *why*; collapsing the sources panel lost it until you navigated away and came back;
changing company left the last company's questions on screen; and a citation was a number rather
than a way to reach the page it came from. The rule this phase works to is that context is
**explicit and named** — no conversation buffer, no session memory, no agent. A follow-up carries
one previous run id, and the server looks up what that run actually cited, so the browser can never
claim an answer cited something it did not. "Which documents support this?" is now answered by
reading the previous run's own retrieval log back: no search, no model call, no new run row, nothing
spent. "Why?" composes the question with the previous one named once and filters retrieval to the
documents that answer came from — a pre-ranking filter (`--restrict-to-documents`) sitting beside
the date and source-type filters, changing no weight, no prompt and no score. A question naming
another company always gets a fresh search, and the page says so rather than quietly answering it
out of the wrong company's documents. Every `[n]` in an answer now opens the document behind it in
the rail, at the recorded page, with the exact retrieved passage above it — using the PDF viewer's
own `#page=` fragment, never drawn coordinates. Suggested questions per company are read from the
Phase 12 golden question file, not generated; `T` reads as AT&T and `B` as Barnes Group everywhere.
Detail: `09_Web_App_Future\phase7a_local_ui\README.md`.

## Phase 13 (the research workspace)

**Phase 13 = the PM-friendly research workspace.** The product was technically strong and read like
an engineering console: every control visible at once, the pipeline's vocabulary on the front page,
and a navigation listing five things that did not exist. Phase 13 rearranged it around the way the
work is actually done — ask, read, check, open a source, follow up — without changing a single
retrieval weight, index or answer. Seven views, each listed once. The ask page is one question box,
a Quick Answer / Deep Research choice, and then the answer with a **trust summary** in plain
language above it ("Medium confidence · 8 sources reviewed across 6 documents · Mostly analyst
research · No direct company source used"). The right panel holds Sources, Documents, Deep Dive,
Timeline, Company and Related, with the PDF preview integrated into it. Every technical surface
survived and moved one click away: retrieval controls and filters into an Advanced drawer, the run
summary and diagnostics into Deep Dive, and the per-source vector/keyword scores behind "Why this
passage". An Evaluations view reads the Phase 12 result — 51 PASS / 9 REVIEW / 0 FAIL — per company
and per question type, and says plainly that REVIEW is not a failure. Detail:
`09_Web_App_Future\phase7a_local_ui\README.md`.

## Phase 12 (the golden evaluation)

**Phase 12 = the golden evaluation, across all ten tickers.** Phase 11's smoke test asked three
things of an answer: was anything cited, was a confidence recorded, was a recommendation refused.
Phase 12 asks ten things, of 60 questions — 10 tickers × 6 categories — and every question is
grounded in what that ticker's archive actually holds. It checks citation *coverage* rather than
citation presence, that every figure in an answer appears in the retrieved text, that every
selected source really belongs to the ticker asked about, that confidence sits under the ceiling
the question sets, that an answer does not say "the company reported" when the only evidence is an
analyst saying so, and that the two categories which exist to be declined actually are. The
questions that must be refused are made unanswerable **by date** — the archive is 2026 only — which
is a fact about the index rather than a guess about subject coverage. A decline on an *answerable*
question is scored REVIEW, never FAIL: it means this ticker's index does not cover a question it
was expected to, which is worth a human's attention but is not misbehaviour. See the Phase 12
section below.

## Phase 11.3 (the chunk key that did not name a ticker)

**Phase 11.3 = the repair that moved chunks instead of adding them.** The Phase 11.2 repairs ran
cleanly and then the "behind" counts moved between tickers. They were not moving; chunks were.
`chunk_id` is `{document_id}_c{index:04d}` and `rag_chunks` was keyed by `chunk_id` alone, so the
ticker is a *column*, not part of the key: a document tagged with two tickers has one set of chunk
rows, and `INSERT OR REPLACE` hands them to whichever ticker was built **last**. Nothing is deleted
and the row count never falls, which is why it did not look like loss — but the losing ticker can no
longer cite the document, because Ask Brain filters candidates with `WHERE ticker = ?`. On the live
database all **106** reported-missing (document, ticker) pairs turned out to be already chunked and
embedded under a different ticker; 101 eligible documents are claimed by more than one ticker, and
not one document's chunks span two of them. Such a document is now reported as
`claimed_by_another_ticker` with its current owner, is not offered as repairable, and the repair
path **refuses** (exit 5) rather than churning. `--explain-repair-plan` prints the exact
`document_id`s a repair would add, the ones blocked, and the zero removals; every repair step is
bracketed by a snapshot of every known ticker, and a step that reduces anybody's index stops the run
(exit 4). `scripts\migrate_rag_chunk_key.py` rekeys the table on `(chunk_id, ticker)` — copying
every row, changing no id, deleting nothing — which is what makes an append-only repair possible at
all. See the Phase 11.3 section below.

## Phase 11.2 (the phantom "behind" status)

**Phase 11.2 = the phantom "behind" status.** After the real run the inspector reported MSFT, KR,
MRK, T and VZ as "behind" by 11, 14, 16, 62 and 5 documents. None of those was a number of
documents. "Behind" was decided by subtracting the count of distinct documents in `rag_chunks` from
the count of eligible documents — two different populations, so the difference conflated three
groups: eligible and indexed, eligible and missing, and **indexed but no longer eligible** (its
metadata changed after it was embedded; its chunks stay, because nothing here deletes chunks). A
gap of 11 could be 11 missing or 30 missing and 19 retired, and it never reached zero. "Behind" is
now a **set difference over `document_id`**, every missing document is named with the reason it is
missing, and a document whose extracted text is absent or empty is reported honestly as unfixable
by embedding rather than as work to pay for. A second bug fell out of checking that the builder and
the inspector agree: the builder's `COALESCE(reason,'none')='none'` card clause treated a *blank*
exclusion reason as an exclusion while the inspector treated it as none, so such a document was
behind forever. One clause, in one module, now read by both. See the Phase 11.2 section below.

## Phase 11.1 (the subprocess decoding fix)

**Phase 11.1 = the Windows subprocess decoding trap.** The first real expansion run worked — nine
tickers indexed, 30/30 smoke questions passed — but the console filled with
`UnicodeDecodeError: 'charmap' codec can't decode byte 0x9d` from inside subprocess's reader
threads. `text=True` without `encoding=` decodes with the ANSI code page while the child, told
`PYTHONUTF8=1`, writes UTF-8. Three earlier scripts already passed `encoding="utf-8",
errors="replace"`; the two written in Phase 11 did not, so this was a regression of a convention
the project had already learned. Both are fixed, and a test now scans every subprocess call in the
project so the next one fails in the sandbox instead of on a console.

## Phase 11 (controlled expansion beyond MSFT)

**Phase 11 = controlled expansion beyond MSFT.** Nine more tickers — KIM, KR, MRK, T, VZ, AMZN,
SLB, B, ABBV — indexed one at a time, each into its own Chroma collection and keyword index, with
the cost shown before anything is spent and a gate that refuses to exceed it. Two of the tickers
are a single letter, and a substring match for them would have selected 11 and 12 of 59 fixture
documents respectively, so ticker matching is now whole-token everywhere, in one shared module the
planner and the builder both import. Ask Brain answers for any indexed ticker and **refuses
cleanly for one that is not indexed, never falling back to MSFT**. See the Phase 11 section below.

## Phase 10C.1 (the Documents workspace)

**Phase 10C.1 = the Documents workspace.** The shell landed, but the Documents page still read as
a spreadsheet pasted into it: the reader opened *underneath* the results, the pagination and the
reader fought for the same space, and the page footer floated across the rows. It is now three
panels side by side — filters, library, reader — each scrolling on its own inside a frame that
fits the window, so the page itself does not scroll. The results table became a list of library
entries where the file name leads and everything else is a chip, and clicking anywhere on an entry
opens it in the reader beside the list. See the Phase 10C.1 section below.

## Phase 10C (the product shell)

**Phase 10C = the product shell.** The tool worked; it did not yet look like a product. This phase
adds the shell the rest of the system will live in — a dark navy sidebar naming the firm and the
views, a header carrying the product name and a live system-status chip, and a third view:
**System health**, a read-only dashboard of what the pipeline has and has not done. The Ask Brain
page hides its advanced settings behind a summarised panel, source cards now say whether each
citation was used and whether its original can be opened, and the document reader, tables and
chips share one visual system. **No research logic changed**: no retrieval, answering, prompt,
ingestion or evaluation code was touched. See the Phase 10C section below.

## Phase 10B (controlled PDF preview)

**Phase 10B = controlled PDF preview.** Until now, checking a citation meant copying a path and
finding the file in File Explorer. The Documents tab gains a sixth pane that renders the indexed
PDF in the page — through exactly one endpoint, which takes a **document id and never a path**,
refuses anything that does not resolve under the configured archive root, refuses anything that is
not a `.pdf`, and **streams the file without ever copying it**. Non-PDFs and missing files say so
plainly and keep every other pane. The V drive stays read-only. See the Phase 10B section below.

## Phase 10A.1 (the text pane fixes)

**Phase 10A.1 = the text pane, actually rendering.** Three faults after the first real use of the
Documents tab. The Extracted Text tab was blank: opening a document cleared the whole pane's
markup, taking the search box and the element the text renders into with it, so the pane wrote
into nothing and threw. `?search=` returned the whole document, because the endpoint only ever
read `q=`. And clicking a page showed the page list again rather than the page. All three are
fixed; `/text` now accepts either spelling of the search parameter, always names the mode it is
answering in, and reports snippets with the page they came from. See the Phase 10A.1 section
below.

## Phase 10A (the document library)

**Phase 10A = the document library and the extracted-text reader.** Until now the only way into
the archive was to ask a question, and the only way to check an answer was to find the PDF on the
V drive by hand. Phase 10A adds a second tab to the local UI: browse and filter every indexed
document, open one, and read the text that was extracted from it — card, full text with in-document
search, per-page text, and the RAG chunks. A citation in an answer now opens its document in one
click. **It does not open, serve or copy the original PDF**; it shows you the V-drive path and a
copy button, and the V drive stays read-only. See the Phase 10A section below.

## Phase 9.3 (the carried-over worklist)

**Phase 9.3 = the other half of the stale-RAG loop.** Run 5 processed four new files, found none
RAG-eligible, and reported ten stale documents it had never looked at. Two faults: the RAG
worklist was only what *that walk* touched, so documents flagged by earlier runs were counted and
never classified; and the inspector's breakdown had no ticker filter, so any stale document with
`usable_for_rag = 1` was reported as "eligible, waiting to be chunked" even when it belonged to a
ticker the MSFT command can never touch. See the Phase 9.3 section below.

## Phase 9.2 (the stale-RAG fix)

**Phase 9.2 = the stale-RAG fix.** Run 4 reported, in the same breath, "no affected MSFT document
is RAG-eligible, nothing to chunk" and "10 documents are marked stale_rag_index" — then
recommended the command that had just found nothing to do. Documents RAG will never index were
flagged forever. Every stale document is now sorted into one of four buckets with a recorded
reason, the ones that can never be chunked have their flag cleared, and nothing recommends a
command that cannot change anything. See the Phase 9.2 section below.

## Phase 9.1 (the repair layer)

**Phase 9.1 = the repair layer.** The first live ingestion on the real archive updated metadata
for 73 files and then failed to extract 70 of them. The traceback pointed at
`create_extraction_schema()`, which looks like a schema fault and was not: the real error was
**`database is locked`**, because the Phase 9 extract loop held an open write transaction while
launching each child process. One file extracted, then seventy failures — a genuine schema fault
would have failed the first one too. Phase 9.1 fixes the lock, records a run with failures as
`partial_success` rather than `completed`, captures full tracebacks, and adds a repair mode that
reprocesses only what failed. See the Phase 9.1 section below.

## Phase 9 (incremental ingestion)

**Phase 9 = incremental ingestion.** Phases 1 through 8 assumed the archive stood still; it does
not. `scripts\run_incremental_ingestion.py` compares the V drive with the metadata index, and
processes only the differences — new files, and files whose contents changed. Unchanged files are
counted and left alone. Files that have vanished are reported and **never deleted**. The MSFT
evaluation gate passed 10/10 before this was built, which is why it was safe to start changing
what the index contains. See the Phase 9 section below, and `10_Ingestion\README.md`.

## Phase 8.2 (language-audit cleanup)

**Phase 8.2 = the last language-audit false positive.** Phase 8.1 took the evaluation from
4 PASS / 2 REVIEW / 4 FAIL to 9 PASS / 0 REVIEW / 1 FAIL. The one survivor was
`msft_fy26_operating_margin`, flagged for a buy/sell/hold call on the words "is a sell-side
summary" — describing a source, not making a call. The cause was not the phrase list, which
already masked "sell-side": it was the hyphen. See the Phase 8.2 section below.

## Phase 8.1 (prompt and audit fixes)

**Phase 8.1 = the fixes the first real evaluation asked for.** The Phase 8 run on the real MSFT
index came back 4 PASS / 2 REVIEW / 4 FAIL, and every failure was a prompt or audit problem, not
a retrieval one. Four changes, none of them touching retrieval: a request for an investment
decision is now detected from the question and refused before any model is called; a question
whose subject does not appear in the evidence is stopped instead of written around; topics that
are interpretation rather than disclosure are capped at Medium confidence; and the Direct Answer
must cite every factual sentence. See the Phase 8.1 section below.

## Phase 8 (evaluation and trust layer)

**Phase 8 = the evaluation and trust layer.** Ten golden questions for MSFT, eight automated
checks per question, and a plain verdict on whether this is ready to scale past one ticker.
It grades **behaviour, not truth** — there is no stored right answer anywhere — and it is built
to fail things: the test suite feeds it hallucinated and unsafe answers and requires it to catch
them. See `07_Evaluation\README.md`.

## Phase 7B (UI diagnostics)

**Phase 7B = UI diagnostics and demo polish.** The same local page, rebuilt so a non-technical
reader can use it and can tell when *not* to trust an answer: readable controls, four demo
presets, an answer summary card with copy buttons, a sources panel, a retrieval diagnostics
table, an explained audit box, and a "Questions needing review" queue. No framework was added and
nothing about retrieval or answering changed. See `09_Web_App_Future\phase7a_local_ui\
PHASE7B_STATUS.md`.

## Phase 7A (local demo UI)

**Phase 7A = a local demo UI.** A single page at `http://127.0.0.1:8000` that runs Ask Brain for
MSFT without PowerShell. It is a window onto the pipeline, not a new one: every question goes to
`scripts/ask_brain.py` and every rule that governed an answer before governs it still. See the
Phase 7A section below.

## Phase 6.1 (audit cleanup)

**Phase 6.1 = audit cleanup.** Two false positives found in Phase 6 testing: the directness cap
fired on ordinary *Evidence Limitations* language, and the banned-language check read "sell-side"
as a sell call. Both are fixed below; retrieval, the keyword index and Chroma are untouched.



**Phase 6 = retrieval quality.** Vector search alone was losing exact strings a filing actually
uses, and confidence was being graded on how well the *wording* matched rather than whether the
evidence answered the question. Phase 6 adds a local BM25 keyword index and hybrid retrieval,
metadata filters, better source routing, an answer-directness confidence cap, a stronger citation
audit, and retrieval diagnostics. See the Phase 6 section below.



**Phase 5A = MSFT-only chunking and embeddings.** The first phase that called an external API,
and it calls exactly one endpoint: **OpenAI embeddings**.

**Phase 5B = Ask Brain, MSFT only.** The first phase that generates text. It retrieves chunks
from the Phase 5A index, sends a handful of them to the answer model, and requires a citation on
every claim. It never answers from model memory, never reads outside the MSFT index, and never
runs the answer model at all when retrieval comes back empty. See the Phase 5B section below.


**Phase 1 = a read-only source archive scanner and metadata index.**
**Phase 2 = page-level PDF text extraction into `03_Extracted_Text`.** Nothing more.
Phase 1.1 added better year detection, ticker vs research-vendor separation, and a graded review
queue. Phase 1.2 is a metadata **QA** pass over the same scope: detected_year is now controlled
entirely by the folder/archive path, sensitive-data flagging distinguishes restricted material
from ordinary company language, folder structure drives classification so far fewer rows are
Unknown, and vendor/theme tokens are kept out of the ticker column.

Phase 2 adds text extraction only — see the Phase 2 section below. No summarization, no
interpretation, no embeddings, no RAG, no document cards.

| Phase 1 does | Phase 1 deliberately does NOT |
|---|---|
| Walk the archive recursively, read-only | Open, parse or extract document contents |
| Record file system metadata | Summarize or interpret any document (Phase 2 included) |
| Compute SHA-256 hashes and detect duplicates | Summarize anything |
| Guess dates, tickers, source type, document type, theme from **paths and file names only** | Create embeddings or a vector index |
| Flag rows for human review | Call OpenAI, Claude, or any external API |
| Export SQLite + CSV + Markdown for review | Make investment conclusions |

### Safety rules enforced by the code

- The V Drive is treated as **read-only**. The scanner only ever opens files for reading.
- Nothing is modified, moved, renamed, deleted or written under `V:\Companies\A Current Month`.
- All generated files are written **only** inside `C:\Users\AI Admin\Documents\CutlerResearchBrain`.
- The scanner refuses to start if `--project-root` is inside `--source`, if `--source` is inside
  `--project-root`, or if `--project-root` is on the `V:` drive. This check runs **before** any
  file or folder (including the log file) is created.
- No network calls of any kind.
- Client and portfolio data is out of scope. In Phase 1, any file whose **path or name** hints at
  client / portfolio / holdings / account / performance / allocation material is flagged
  `needs_review` so a human can decide before later phases ever touch it.

---

## Layout

```
CutlerResearchBrain\
├─ configs\
│  ├─ source_type_rules.yaml     # source / document / theme keyword rules (edit these)
│  └─ ticker_rules.yaml          # ticker candidate detection rules (edit these)
├─ scripts\
│  ├─ scan_source_archive.py     # read-only walk + metadata + guesses  -> data\raw_scan.jsonl
│  ├─ build_metadata_index.py    # SQLite index, duplicate resolution, all reports
│  ├─ inspect_metadata.py        # console report over the SQLite index
│  ├─ extract_pdf_text.py        # Phase 2: page-level PDF text -> 03_Extracted_Text
│  ├─ refresh_extraction_reports.py  # Phase 2.1: rebuild reports from the DB only
│  ├─ inspect_extractions.py     # Phase 2: console report over pdf_extractions
│  ├─ build_document_cards.py    # Phase 3: document cards -> 04_Document_Cards
│  ├─ refresh_document_card_reports.py  # Phase 3.1: repair labels, rebuild reports
│  ├─ inspect_document_cards.py  # Phase 3: console report over document_cards
│  ├─ build_obsidian_memory.py   # Phase 4: Obsidian vault -> 05_Obsidian_Brain
│  ├─ refresh_obsidian_links.py  # Phase 4.1: card stubs + internal link repair
│  ├─ repair_broken_wikilinks.py # Phase 4.2: de-link unresolved wikilinks
│  ├─ inspect_obsidian_memory.py # Phase 4: console report over obsidian_notes
│  ├─ build_rag_index.py         # Phase 5A: chunk + embed MSFT -> 06_RAG_Index
│  ├─ inspect_rag_index.py       # Phase 5A: console report over rag_chunks
│  ├─ search_rag_index.py        # Phase 5A: retrieval test only - never answers
│  ├─ ask_brain.py               # Phase 5B/6: answer from retrieved evidence, with citations
│  ├─ inspect_ask_brain.py       # Phase 5B/6: console report over ask_brain_runs
│  ├─ build_keyword_index.py     # Phase 6: local BM25 index over rag_chunks
│  └─ inspect_retrieval_quality.py  # Phase 6: is retrieval finding the right material?
├─ data\
│  ├─ raw_scan.jsonl             # one JSON record per file (intermediate)
│  ├─ document_metadata.sqlite   # the documents table - source of truth
│  └─ document_manifest.csv
├─ 02_Metadata_Index\
│  ├─ document_manifest.csv                    # full manifest for human review
│  ├─ scan_summary.md                          # human-readable summary of the scan
│  ├─ duplicate_report.csv
│  ├─ source_type_report.csv
│  ├─ ticker_candidate_report.csv              # clean candidates with counts
│  ├─ high_review_report.csv                   # everything graded High - start here
│  ├─ sensitive_data_review_report.csv         # possible client / portfolio material
│  ├─ ticker_noise_report.csv                  # every token and what happened to it
│  ├─ year_detection_report.csv                # how each year value was arrived at
│  ├─ sample_unknown_source_files.csv          # up to 500 examples to tune the rules
│  ├─ sample_unknown_document_type_files.csv
│  ├─ public_language_sensitive_false_positive_report.csv   # downgraded rows, auditable
│  ├─ date_confidence_report.csv               # how each year was arrived at
│  └─ remaining_future_detected_years_report.csv            # should always be empty
├─ 03_Extracted_Text\                          # Phase 2 output, by document_id
├─ 04_Document_Cards\                          # Phase 3 output, by document_id
├─ 05_Obsidian_Brain\                          # Phase 4 output, an Obsidian vault
├─ 06_RAG_Index\                               # Phase 5A / 5B / 6 output
│  ├─ chunks\MSFT_chunks.jsonl                 # every chunk, verbatim, auditable
│  ├─ chroma\MSFT\                             # local persistent vector store
│  ├─ keyword\MSFT_keyword_index.json          # Phase 6: local BM25 index
│  ├─ answer_logs\                             # one markdown answer per question
│  ├─ retrieval_logs\                          # what was retrieved and why, per run
│  └─ reports\
├─ logs\
│  ├─ phase1_scan.log
│  ├─ phase2_pdf_extraction.log
│  ├─ phase3_document_cards.log
│  ├─ phase4_obsidian_memory.log
│  ├─ phase5a_rag_index.log
│  ├─ phase5b_ask_brain.log
│  └─ phase6_keyword_index.log
├─ .env.example                                # copy to .env; .env is git-ignored
├─ requirements.txt
└─ README.md
```

`03_Extracted_Text` is written by Phase 2 (see below). `09_Web_App_Future\phase7a_local_ui`
holds the local UI — the Phase 7A/7B Ask Brain tab and the Phase 10A Documents tab; the rest of
`09_Web_App_Future` is a placeholder for later phases.

---

## Setup

```bat
cd "C:\Users\AI Admin\Documents\CutlerResearchBrain"
pip install -r requirements.txt
```

Phase 1 needs only **PyYAML** (for the two config files); Phase 2 adds **PyMuPDF** for PDF text
extraction; Phase 5A adds **openai**, **chromadb**, **tiktoken** and **python-dotenv**.
Everything else is Python standard library. If PyYAML is missing, the scanner still runs using
built-in fallback rules and says so loudly in the log — but the YAML files are the source of
truth, so install it.

Phase 5A and 5B need a key:

```bat
copy .env.example .env
REM then edit .env and set OPENAI_API_KEY
```

`.env` is git-ignored. The key is read with python-dotenv at runtime and is **never printed to
the console and never written to a log or report** — the log records only `present` or `MISSING`.

---

## Running a scan

Scan the first target folder:

```bat
python scripts\scan_source_archive.py --source "V:\Companies\A Current Month\2026" --project-root "C:\Users\AI Admin\Documents\CutlerResearchBrain"
```

Inspect what was found:

```bat
python scripts\inspect_metadata.py --db "data\document_metadata.sqlite"
```

Rebuild the index and reports from the last raw scan, without re-walking the archive:

```bat
python scripts\build_metadata_index.py --project-root "C:\Users\AI Admin\Documents\CutlerResearchBrain"
```

### Useful options

| Option | Script | Effect |
|---|---|---|
| `--limit 250` | scan | Stop after N files. Good for a first trial run. |
| `--max-hash-mb 50` | scan | Only hash the first N MB of each file (faster on huge files). |
| `--reset-db` | scan | Drop and rebuild the `documents` table instead of updating in place. |
| `--no-index` | scan | Write `data\raw_scan.jsonl` only; skip SQLite and reports. |
| `--verbose` | scan | Chattier console output. |
| `--reset` | build | Same as `--reset-db`, when running the builder on its own. |
| `--archive-year 2026` | scan | Force the archive year instead of deriving it from the source path. |
| `--top-tickers 50` | inspect | Show more ticker candidates. |

An existing Phase 1.0 database is **migrated in place** — the twelve Phase 1.1 columns are added
with `ALTER TABLE` and nothing is dropped. Re-running the scan repopulates them.

Re-running the scanner on the same folder is safe: rows are keyed on a stable `document_id`
derived from the file's path, so a re-scan updates rows rather than duplicating them. Scanning a
second folder **adds** to the same index; use `--reset-db` if you want a clean rebuild.

---

## The `documents` table

One row per file. Every `*_guess` column is exactly that — a guess.

| Column | Meaning |
|---|---|
| `document_id` | Stable id derived from the file's path within the scanned archive |
| `file_name` | File name with extension |
| `file_extension` | Lowercased extension, e.g. `.pdf` |
| `file_path` | Full path on the V Drive |
| `relative_path` | Path relative to the scan root |
| `parent_folder` | Immediate parent folder name |
| `source_folder_guess` | Top-level folder under the scan root (usually the company/family folder) |
| `file_size_bytes` | Size in bytes |
| `created_time` / `modified_time` | File system timestamps, ISO format |
| `file_hash_sha256` | SHA-256 of the file contents (used for duplicate detection) |
| `duplicate_status` | `unique`, `original`, `duplicate`, or `error` |
| `duplicate_of` | `document_id` of the original, when this row is a duplicate |
| `folder_date_guess` | `YYYY-MM-DD` from a **daily folder** such as `6.22.2026` (folders only — never the file name) |
| `detected_year` / `detected_month` | The archive/folder year and month this document belongs to. See the year rules below. |
| `ticker_candidates` | Legacy column, mirrors `ticker_candidates_clean` |
| `archive_year` / `archive_month` | Year and month implied by the **scan path** (the `2026` folder in `V:\Companies\A Current Month\2026`) |
| `folder_year` / `folder_month` | Year and month implied by the folders **below** the scan root |
| `filename_year_mentions` | Every year found in the file name, comma-separated. Nothing is lost. |
| `possible_maturity_years` | Future / security maturity years from the file name (2028, 2029, 2030, 2032, 2036 …). **Blocked from becoming `detected_year`.** |
| `ticker_candidates_raw` | Every uppercase token that passed the length filter — the audit trail, noise included |
| `ticker_candidates_clean` | Raw minus exclusions minus source-position vendors. **This is the review column.** |
| `source_vendor_candidates` | Research providers (JPM, GS, UBS, RBC, KBW, BI …) detected in a source-like position. Not company tickers by default. |
| `ticker_confidence` | `High` / `Medium` / `Low` / `None` |
| `review_severity` | `High` / `Medium` / `Low` / `None` |
| `review_category` | Semicolon-separated categories, most severe first |
| `date_confidence` | `High` (folder date or archive year) / `Medium` (full date in the file name) / `Low` (loose year) / `None` |
| `detected_year_source` | `folder_date`, `archive_path`, `folder_name`, `filename_date`, `filename_year`, `none` |
| `sensitive_keyword_hits` | The sensitive words/phrases actually matched, comma-separated |
| `sensitive_context_guess` | `possible_restricted_data`, `public_company_language`, `research_business_language`, `needs_manual_check`, `none` |
| `source_type_guess` | SEC Filing, Company Press Release, Sell-Side Research, Credit Research, Convertible Research, REIT Research, Bank Research, Green Street Research, Market Monitor, Short Sale Research, Industry Research, Template, Unknown |
| `document_type_guess` | Research Note, 8-K, 10-K, 10-Q, Press Release, Investor Presentation, Credit Note, Convertible Note, Spreadsheet, Template, Unknown |
| `strategy_theme_guess` | Community Banks, Convertibles and Exchangeables, REITs and Real Estate, Credit and Capital Structure, M&A and Strategic Transactions, Healthcare Catalysts, Energy and Commodities, Retail and Consumer, Technology and AI Infrastructure, Market Sentiment and Monitors, Short Sale Research, Unknown |
| `needs_review` | `1` if a human should look at this row |
| `review_reason` | Why, semicolon-separated |
| `processed_at` | When the scan ran |

A small `scan_runs` table records each scan (when, what source, how many files) as an audit trail.

### Year rules (Phase 1.2)

`detected_year` is resolved in strict priority order and then **clamped to a plausible window**
(`min_plausible_year` … current year + `max_plausible_year_offset`), so a maturity year can never
masquerade as the year a document belongs to:

1. the daily folder date, e.g. `6.22.2026` → `date_confidence = High`
2. `archive_year` — the scan path's year folder → `High`
3. a plausible year in a folder name → `High`
4. a full date inside the file name → `Medium`
5. a plausible loose year in the file name that is **not** a maturity year → `Low`
6. blank → `None`

Phase 1.2 closed the last leak: a **folder** named for a future year (`XYZ Converts 2029`) used to
set `folder_year` and win. Future years from file names *and* folder names now go to
`possible_maturity_years`, which is never eligible for `detected_year`. A year is treated as a
maturity when it is beyond current year + `maturity_year_lookahead`, or later than the archive
year with security wording (`convertible`, `notes`, `due`, `coupon`, `%` …). Every year seen in a
file name is still recorded in `filename_year_mentions`.

`remaining_future_detected_years_report.csv` exists to prove this — it should always be empty.

### Sensitive-data rules (Phase 1.2)

A keyword hit is not restricted data. Words like portfolio, client, account, performance,
position and statement are everywhere in ordinary public-company research titles, so the test is
two-tier:

| What matched | `sensitive_context_guess` | Severity |
|---|---|---|
| a strong phrase (`client account`, `portfolio holdings`, `account statement`, `model portfolio`, `portfolio performance` …) | `possible_restricted_data` | **High** |
| a weak word next to public-company wording (`Expands Portfolio`, `Financial Statements`, `performance-based`, `market position`, `Clients rank`) | `public_company_language` | Low |
| a weak word inside an obvious research document | `research_business_language` | Low |
| a weak word with nothing to explain it | `needs_manual_check` | Medium |
| nothing | `none` | — |

Severity is downgraded, never hidden: every row with any hit still appears in
`sensitive_data_review_report.csv` with its hits and context, and the downgraded rows also get
their own `public_language_sensitive_false_positive_report.csv` so the downgrade itself can be
audited. Strong phrases live in `sensitive_strong_phrases` in `configs\source_type_rules.yaml`.

### Classification rules (Phase 1.2)

Source and document types are resolved in this order, which is what cuts the Unknown counts:

1. a strong signal in the **file name** — template wording, filing shorthand (`10-K`, `8-K`,
   `10-Q`), press-release wording, explicit research-note wording
2. the archive's own **folder structure** — `\Companies\` → Company Material,
   `\Banks\` → Bank Research, `\Green Street\` → Green Street Research,
   `\Research\` → Sell-Side Research (or Industry Research for sector/primer/outlook names),
   `\Credit\` → Credit Research, `\Converts\` → Convertible Research, `\REITs\` → REIT Research
3. the keyword rules in `configs\source_type_rules.yaml`
4. extension defaults, else Unknown

Two document types were added in Phase 1.2: **Company Material** and
**Earnings / Financial Results** (earnings, results, quarterly, financial statements).
Spreadsheet extensions still override everything.

### Ticker rules (Phase 1.1)

Three columns instead of one. `ticker_candidates_raw` is the audit trail;
`ticker_candidates_clean` drops exclusions and vendor tokens that sat in a source-like position;
`source_vendor_candidates` holds the research providers. A vendor token is only moved out of the
ticker column when it is acting as the source — after a `" - "` separator, in the last two tokens
of the name, or with research-provider context in the path. `JPM 10-Q quarterly report.pdf` keeps
JPM as a ticker candidate (Medium confidence); `CBAN Research Note - JPM.pdf` gives CBAN as the
ticker (High) and JPM as the vendor.

### Review grading (Phase 1.1)

`needs_review` on its own flagged roughly two thirds of the archive, which is not a work queue.
Every row now also carries `review_severity` and `review_category`:

| Category | Severity | Meaning |
|---|---|---|
| `sensitive_data` | **High** | a strong restricted-data phrase in the path |
| `scan_error` | **High** | metadata or hash could not be read — a gap in the inventory |
| `possible_sensitive_data` | Medium | a sensitive word with nothing explaining it |
| `duplicate` | Medium | same SHA-256 as another file |
| `no_ticker_on_research` | Medium | research-looking document with no clean ticker candidate |
| `public_language_sensitive` | Low | a sensitive word used as ordinary company language |
| `unknown_source_type` | Low | classification gap — fix by editing the rules |
| `unknown_document_type` | Low | classification gap — fix by editing the rules |

Work High first, then Medium. Low is mostly rule tuning, not file-by-file work. The mapping lives
in `configs\source_type_rules.yaml` under `review_severity_map`, so it can be changed without
touching code.

Duplicates are **never dropped**. They are kept and labelled so a human can pick the canonical copy.

---

## Which files are scanned

Included: `.pdf .doc .docx .xls .xlsx .xlsm .csv .txt .md .ppt .pptx`

Skipped folders: `.pytest_cache`, `outputs`, `__pycache__`, `.git`, `node_modules`.
Office lock files (`~$...`) are skipped too.

## Robustness

- Permission errors on a folder or file are logged and skipped; the scan continues.
- Long Windows paths (>260 characters) are handled with the extended-length path prefix.
- Unreadable or vanished files are recorded as rows with `duplicate_status = 'error'` and
  `needs_review = 1`, so nothing silently disappears from the inventory.
- One bad file can never stop the scan. Everything lands in `logs\phase1_scan.log`.

---

## Tuning the guesses

Classification is deliberately simple and fully inspectable — ordered keyword rules, first match
wins. When you spot a wrong guess:

1. **Wrong or missing source / document type / theme** → edit `configs\source_type_rules.yaml`.
   Rules are evaluated top to bottom, so put specific rules above general ones.
2. **False positive ticker** (e.g. an acronym picked up as a ticker) → add it to the
   `exclusions` list in `configs\ticker_rules.yaml`.
3. Re-run the scan. The index updates in place.

Ticker detection is intentionally conservative: only fully uppercase alphabetic tokens of 1–5
letters from the file name, minus a long exclusion list, and all-caps file names are skipped
entirely. Missing a ticker is cheaper than inventing one.

Phase 1.2 adds four lists to `configs\ticker_rules.yaml`:

- `strong_vendor_tokens` (BI, QT, WF, CNN, CNBC, WSJ, BBG, SNL, GSA …) default to
  `source_vendor_candidates`. The only escape is being the **first** token of a file name that
  also reads like a company file (`WF Q2 2026 earnings.pdf`), via `company_context_keywords`.
- `never_ticker_tokens` (CNN, CNBC, WSJ, Reuters …) are never promoted to a ticker at all.
- `theme_tokens` handles concepts that look like tickers. `AI` next to "artificial intelligence",
  "agentic", "AI infrastructure", "data center", "GPU" and so on is a theme word and is dropped;
  without that context AI stays a candidate, because it is also a real listed ticker.
- `protected_tickers` (MSFT, AMZN, KR, ACI, VICI, KIM, ABBV, PFE, CVX, XOM …) always survive,
  whatever any other rule says. Add real Cutler names here.

---

## Human review workflow

1. Open `02_Metadata_Index\scan_summary.md` for the shape of the archive.
2. Work `high_review_report.csv` — everything graded High.
3. Work `sensitive_data_review_report.csv` — every sensitive keyword hit with its
   `sensitive_context_guess`. Confirm the `possible_restricted_data` rows (they stay out of later
   phases until a human clears them) and spot-check `needs_manual_check`.
4. Skim `public_language_sensitive_false_positive_report.csv` — the rows Phase 1.2 downgraded.
   Anything that should have stayed High goes into `sensitive_strong_phrases`.
5. Confirm `remaining_future_detected_years_report.csv` is empty and glance at
   `date_confidence_report.csv`.
4. Work `duplicate_report.csv` — decide the canonical copy of each duplicate.
5. Skim `ticker_noise_report.csv` — send false positives to `exclusions` and research providers to
   `vendor_tokens` in `configs\ticker_rules.yaml`.
6. Check `year_detection_report.csv` — confirm `detected_year` tracks the archive folders and that
   maturities landed in `possible_maturity_years`.
7. Use `sample_unknown_source_files.csv` and `sample_unknown_document_type_files.csv` to add missing
   keywords to `configs\source_type_rules.yaml`, then re-run the scan.

---

## Phase 2 — PDF text extraction

`scripts/extract_pdf_text.py` pulls page-level text out of the archive's PDFs with PyMuPDF and
writes it under `03_Extracted_Text`. **Text extraction only** — nothing is summarized,
interpreted or classified for investment meaning, no source PDF is copied locally, and the V
Drive stays read-only.

```bat
REM 1) Try 25 documents first
python scripts\extract_pdf_text.py --db "data\document_metadata.sqlite" --project-root "C:\Users\AI Admin\Documents\CutlerResearchBrain" --limit 25

REM 2) See how it went
python scripts\inspect_extractions.py --db "data\document_metadata.sqlite"

REM 3) Full run (resumable — already-extracted documents are skipped)
python scripts\extract_pdf_text.py --db "data\document_metadata.sqlite" --project-root "C:\Users\AI Admin\Documents\CutlerResearchBrain"
```

### What gets extracted

A PDF row is eligible when its extension is `.pdf`, the file exists, it is not a `duplicate`,
its `review_severity` is not High, and `exclude_from_phase2_extraction` is 0. That flag is
derived in Phase 1 (`True` when `review_severity` is High) and recomputed on every index build,
so client/portfolio-looking material is never extracted until a human clears it.

| Option | Effect |
|---|---|
| `--limit 25` | Stop after N documents. |
| `--force` | Re-extract documents that already have output, and allow duplicates. |
| `--include-high-review` | Also extract High-review / excluded rows. May contain client material — use deliberately. |
| `--include-duplicates` | Duplicates only, without forcing re-extraction. |
| `--only-document-id doc_…` | Extract one document (debugging). |
| `--max-pages 5` | Cap pages per PDF; the row is marked `partial`. |

### Output layout

```
03_Extracted_Text\2026\<document_id>\pages\page_0001.txt
03_Extracted_Text\2026\<document_id>\pages\page_0002.txt
03_Extracted_Text\2026\<document_id>\document_text.txt
03_Extracted_Text\2026\<document_id>\extraction_metadata.json
```

Folders are named by `document_id`, never by file name — archive file names are long and often
unsafe as Windows paths. The run is **resumable**: if `document_text.txt` and
`extraction_metadata.json` both exist and `--force` is not passed, the document is skipped and its
previous result is carried forward. Failures are always retried.

### Status and quality — two separate axes

**`extraction_status` = what happened**: `extracted`, `partial`, `failed`, `skipped_high_review`,
`skipped_duplicate`, `skipped_non_pdf`, `skipped_missing_file`.

**`extraction_quality` = how much text came out per page**: **High** > 1,200, **Medium**
300–1,200, **Low** < 300 or half the pages empty, **Empty** no text at all, **Failed** the
attempt itself failed, **Not Applicable** the row was skipped and never attempted.

Phase 2.1 fixed the reporting bug that conflated them: skipped rows used to be written with
`Failed` quality, which made 126 rows look like failures when only 2 extractions had actually
failed. The rules now:

- a skipped row is always **Not Applicable** — skipped is *not* failed;
- **Failed** quality is used only for a genuine failed extraction attempt;
- a PDF that extracted cleanly but has no text layer stays **Empty**, not Failed;
- a **partial** row keeps its *measured* quality and carries its partialness in the status —
  quality answers "how much text per page", status answers "what happened", and mixing the two
  is what caused the confusion in the first place.

`scanned_pdf_suspected` means pages exist but almost no text came out — an OCR candidate for a
later phase; **OCR is not part of Phase 2**. `table_heavy_guess` is a crude line-shape heuristic,
nothing more.

### Refreshing the reports without re-extracting

```bat
python scripts\refresh_extraction_reports.py --db "data\document_metadata.sqlite" --project-root "C:\Users\AI Admin\Documents\CutlerResearchBrain"
```

This normalizes the quality column and rebuilds every report **from the database only** — it
never opens a source PDF, never touches the V Drive, and never changes a character of extracted
text. `extract_pdf_text.py` calls the same code at the end of a run, so an extraction run and a
manual refresh always produce identical reports.

Encrypted PDFs (password required), corrupt files and unreadable pages are recorded as failures
with a clear message; one bad file never stops the run.

### Phase 2 outputs

Results land in the `pdf_extractions` table (one row per PDF considered, skips included) and on
the `documents` table as `pdf_extraction_status`, `pdf_extraction_quality`, `pdf_page_count`,
`pdf_total_text_chars` and `pdf_extracted_at` — which a Phase 1 re-scan preserves rather than
overwrites. Reports in `02_Metadata_Index` (plus `data\pdf_extraction_manifest.csv` and
`logs\phase2_pdf_extraction.log`):

| File | What it holds |
|---|---|
| `pdf_extraction_manifest.csv` | Every PDF considered, one row each |
| `pdf_extraction_summary.md` | Manager-readable summary |
| `true_failed_extractions.csv` | Only genuine failed attempts |
| `skipped_extractions_report.csv` | Rows never attempted, with the reason |
| `ocr_candidates_report.csv` | Little or no text extracted — possible scans |
| `table_heavy_candidates_report.csv` | Table-shaped extracted text |
| `extraction_status_report.csv` | Counts by status, with plain-English meanings |
| `extraction_quality_clean_report.csv` | Quality counts, skipped rows excluded |
| `pdf_extraction_failed_report.csv` | Failed and partial rows together |
| `pdf_extraction_quality_report.csv` | Quality × status grid, all rows |
| `scanned_pdf_suspected_report.csv` | Suspected image-only PDFs |

---

## Phase 3 — Document cards

`scripts/build_document_cards.py` builds one structured card per eligible document into
`04_Document_Cards`, from the Phase 1 index, the Phase 2 extraction results and the extracted
text already on disk. **Source PDFs are never opened in this phase.**

A card is a metadata profile, not a summary. Everything on it is copied from the database or
produced by a fixed rule (first 1,500 characters, first 10 non-empty lines, title-like line
shapes). Run it twice and you get identical cards. **No AI summary, no interpretation, no
thesis, sentiment, rating or investment view, and nothing inferred from a file existing.**

```bat
REM 1) Try 100 documents first
python scripts\build_document_cards.py --db "data\document_metadata.sqlite" --project-root "C:\Users\AI Admin\Documents\CutlerResearchBrain" --limit 100

REM 2) See what was built
python scripts\inspect_document_cards.py --db "data\document_metadata.sqlite" --project-root "C:\Users\AI Admin\Documents\CutlerResearchBrain"

REM 3) Full run (resumable — existing cards are reused)
python scripts\build_document_cards.py --db "data\document_metadata.sqlite" --project-root "C:\Users\AI Admin\Documents\CutlerResearchBrain"
```

### Eligibility

`.pdf`, `pdf_extraction_status` in `extracted`/`partial`, extraction quality not `Failed`,
extracted text present on disk, not a duplicate, not High review. Ineligible rows still get a
card *row* recording why — `skipped_duplicate`, `skipped_high_review`, `skipped_missing_text`,
`skipped_failed_extraction` — so the counts always reconcile.

| Option | Effect |
|---|---|
| `--limit 100` | Stop after N documents. |
| `--force` | Rebuild cards that already exist. |
| `--only-document-id doc_…` | Build one card (debugging). |
| `--include-high-review` | Also card High-review rows. May contain client material. |
| `--include-duplicates` | Also card duplicates. |
| `--min-quality Medium` | Only card documents at that extraction quality or better. |

### Output

```
04_Document_Cards\2026\<document_id>\document_card.md
04_Document_Cards\2026\<document_id>\document_card.json
```

Folders are named by `document_id`, never by file name. The `.md` carries YAML front matter
(`type: document_card`, ids, guesses, `usable_for_rag`) so a later phase can parse it.

`card_quality` is High / Medium / Low / Needs Review and measures **how complete the card is** —
text volume plus how much metadata is filled in — not anything about the document's content.

`usable_for_rag` is a plumbing flag: can a later retrieval phase use this text at all. When it
is false, `rag_exclusion_reason` says precisely why (Phase 3.1 replaced the old catch-all
`missing_text`):

| Reason | Meaning |
|---|---|
| `none` | usable — text is available for a later phase |
| `duplicate` | another copy is already indexed |
| `high_review` | possible client/portfolio material, gated until a human clears it |
| `failed_extraction` | Phase 2 failed, skipped it, or never ran |
| `missing_text_file` | the expected `document_text.txt` is not on disk — re-extract |
| `empty_text` | extraction ran, the PDF had no usable text layer (under 200 characters) |
| `scanned_pdf_no_text` | same, and Phase 2 flagged an image-only scan — **OCR candidate** for a later phase |
| `not_pdf` | not a PDF |

Rules are evaluated in that order, from extraction metadata and character counts only —
nothing about document content is examined. `duplicate`, `high_review` and `failed_extraction`
are deliberate gates, not defects.

### Repairing card labels without rebuilding

```bat
python scripts\refresh_document_card_reports.py --db "data\document_metadata.sqlite" --project-root "C:\Users\AI Admin\Documents\CutlerResearchBrain" --fix-reasons --rewrite-cards
```

With no flags it regenerates the reports only. `--fix-reasons` recomputes
`rag_exclusion_reason` and `usable_for_rag` in the database; `--rewrite-cards` rewrites existing
`document_card.md` / `.json` with the cleaned labels, reusing the stored preview, title and
headings verbatim. It never opens a source PDF, re-runs extraction, or changes extracted text.

### Phase 3 outputs

Table `document_cards` (one row per PDF considered, skips included) plus `document_card_status`,
`usable_for_rag` and `document_card_generated_at` on `documents`. Reports:
`data\document_card_manifest.csv`, `02_Metadata_Index\document_card_manifest.csv`,
`document_card_summary.md`, `document_card_quality_report.csv`,
`document_card_needs_review_report.csv`, `rag_readiness_report.csv`,
`rag_exclusion_reason_clean_report.csv`, `scanned_no_text_card_report.csv`,
`empty_text_card_report.csv`, `missing_text_file_card_report.csv`, and
`logs\phase3_document_cards.log`.

---

## Phase 4 — Obsidian memory vault

`scripts/build_obsidian_memory.py` builds a browsable Obsidian vault in `05_Obsidian_Brain`
from the document cards and the database. Every note is counts and links — **no document text
is copied into the vault, nothing is summarized, and no PDF is opened.**

```bat
REM 1) Try 250 document cards first
python scripts\build_obsidian_memory.py --db "data\document_metadata.sqlite" --project-root "C:\Users\AI Admin\Documents\CutlerResearchBrain" --limit 250

REM 2) See what was built
python scripts\inspect_obsidian_memory.py --db "data\document_metadata.sqlite" --project-root "C:\Users\AI Admin\Documents\CutlerResearchBrain"

REM 3) Full vault
python scripts\build_obsidian_memory.py --db "data\document_metadata.sqlite" --project-root "C:\Users\AI Admin\Documents\CutlerResearchBrain"
```

Then open `05_Obsidian_Brain` as a vault in Obsidian and start at
**Cutler Research Brain Dashboard**.

### Vault structure

```
05_Obsidian_Brain\
├─ 00_Dashboard\             Cutler Research Brain Dashboard.md
├─ 01_Daily_Research_Notes\  <YYYY-MM-DD>.md, plus Unknown_Date.md
├─ 02_Company_Pages\         <TICKER>.md — unverified ticker candidates
├─ 03_Source_Type_Pages\
├─ 04_Document_Type_Pages\
├─ 05_Strategy_Themes\       metadata guesses, not investment themes
├─ 06_RAG_Ready_Index\       RAG Ready Documents.md
├─ 07_Needs_Review\          Needs Review.md
├─ 08_OCR_Candidates\        OCR Candidates.md
├─ 09_Document_Card_Index\   Document Card Index.md
│  └─ Card_Stubs\            <document_id>.md — the target of every document link
└─ 10_Templates\             four blank templates for human notes
```

| Option | Effect |
|---|---|
| `--limit 250` | Only index the first N document cards. |
| `--force` | Rewrite every generated note even if it is already current. |
| `--year 2026` | Only documents from that archive year. |
| `--only-date 2026-06-22` | Only documents with that folder date. |
| `--only-ticker MSFT` | Only documents carrying that ticker candidate. |
| `--min-ticker-docs 3` | Require N documents before a company page is created. |

### How it behaves

Notes link to each other with wikilinks (`[[MSFT]]`, `[[2026-06-22]]`, `[[Needs Review]]`).
File names are sanitised for Windows — illegal characters, trailing dots, and reserved names
like `CON` are all handled.

**Document links stay inside the vault (Phase 4.1).** Every document card gets a lightweight
stub note at `09_Document_Card_Index\Card_Stubs\<document_id>.md`, and document lists link to
it as `[[doc_abc|Some File.pdf]]`. Phase 4.0 linked straight at
`..\04_Document_Cards\...\document_card.md`, which is outside the vault — Obsidian cannot
resolve that and threw *"Cannot read properties of null reading 'getParentPrefix'"* followed by
*"folder already exists"*. Real filesystem paths now appear as plain text inside the stub, where
they are informative but not clickable.

A stub holds metadata and paths only: no card content, no extracted text, and no PDF is opened
to build it.

**A wikilink is only written when its target exists (Phase 4.2).** Company pages are not created
for vendor/noise tokens or for candidates below `--min-ticker-docs`, so linking every candidate
left hundreds of unresolved links. Ticker candidates without a page are now rendered as inline
code — `FLGN` — still visible and searchable, but not a link that goes nowhere. Grouped-page
links are folder-qualified (`[[03_Source_Type_Pages/Company Material|Company Material]]`) so a
name used in two folders can't be ambiguous.

### Repairing broken wikilinks in an existing vault

```bat
python scripts\repair_broken_wikilinks.py --db "data\document_metadata.sqlite" --project-root "C:\Users\AI Admin\Documents\CutlerResearchBrain" --rewrite-links
```

Without `--rewrite-links` it is a dry run that reports what would change. It de-links broken
ticker links and other unresolved targets, and deliberately **preserves**:

- **document card stub links** (`[[doc_…|File.pdf]]`) — if one is broken the stub is missing, and
  `refresh_obsidian_links.py --rewrite-links` creates it;
- **main section links** (Dashboard, RAG Ready Documents, Needs Review, OCR Candidates, Document
  Card Index) — a broken one means a page is missing, so rebuild rather than de-link.

Human-written notes are skipped byte for byte, and links inside code spans are never rewritten.

### Repairing links in an existing vault

```bat
python scripts\refresh_obsidian_links.py --db "data\document_metadata.sqlite" --project-root "C:\Users\AI Admin\Documents\CutlerResearchBrain" --rewrite-links
```

Without `--rewrite-links` it only creates or updates the stubs. With it, existing generated
notes are rewritten so every external card link becomes an internal wikilink. Human-written
notes are skipped entirely, no PDF is opened, and nothing outside the project root is touched.

Company pages are only created for ticker **candidates**; vendor tokens, exclusion-list noise
and theme words from `configs\ticker_rules.yaml` are skipped, and `protected_tickers` always
get a page. Every company page states that the ticker is unverified.

The build is **resumable and idempotent**: notes are deterministic, so a re-run rewrites only
what actually changed (`--force` rewrites everything). It manages **only** notes it generated —
each carries `generated_by: build_obsidian_memory.py` and is tracked in the `obsidian_notes`
table. Notes a human adds to the vault are never modified or deleted, and the inspector reports
them separately.

### Phase 4 outputs

Table `obsidian_notes`, plus `data\obsidian_note_manifest.csv`,
`02_Metadata_Index\obsidian_note_manifest.csv`, `obsidian_memory_summary.md`,
`obsidian_daily_note_report.csv`, `obsidian_company_page_report.csv`,
`obsidian_needs_review_report.csv`, `obsidian_card_stub_report.csv`,
`obsidian_link_health_report.csv`, `obsidian_broken_wikilink_report.csv`,
`obsidian_link_repair_report.csv`, and `logs\phase4_obsidian_memory.log`.

`inspect_obsidian_memory.py` audits link health on every run and reports by category: internal
document stub links, external card links, and broken links split into **document stub /
company-ticker / section / other**, with examples for each and the exact command that fixes it.
Links written inside code spans are ignored, since Obsidian does not resolve those either.
A healthy vault reports zero in every broken category.

---

## Phase 5A — MSFT chunking + embeddings (retrieval only)

`scripts/build_rag_index.py` chunks the already-extracted text of **MSFT documents only** and
embeds those chunks into a local Chroma collection under `06_RAG_Index`. This is the first phase
that touches an external service, and the boundary is deliberately narrow:

- exactly one OpenAI endpoint is called: **embeddings** (`client.embeddings.create`);
- **no chat, completions or responses call is made anywhere in this project**;
- no question is answered, nothing is summarized, paraphrased or interpreted;
- no thesis, sentiment, rating, recommendation or investment view is produced;
- no source PDF is opened — chunks come from `03_Extracted_Text`, which Phase 2 already wrote;
- the V Drive stays read-only and every output stays inside the project root.

What Phase 5A produces is a **search index over text you already have**. Retrieval is not
evidence: a chunk coming back means its wording is close to the query, nothing more.

```bat
REM 1) Dry run first - counts chunks and tokens, calls nothing, spends nothing
python scripts\build_rag_index.py --db "data\document_metadata.sqlite" --project-root "C:\Users\AI Admin\Documents\CutlerResearchBrain" --ticker MSFT --limit-docs 10 --dry-run

REM 2) If the estimate looks right, embed the same 10 documents
python scripts\build_rag_index.py --db "data\document_metadata.sqlite" --project-root "C:\Users\AI Admin\Documents\CutlerResearchBrain" --ticker MSFT --limit-docs 10

REM 3) See what was indexed
python scripts\inspect_rag_index.py --db "data\document_metadata.sqlite" --project-root "C:\Users\AI Admin\Documents\CutlerResearchBrain" --ticker MSFT

REM 4) Retrieval test - prints matching chunks, never an answer
python scripts\search_rag_index.py --project-root "C:\Users\AI Admin\Documents\CutlerResearchBrain" --ticker MSFT --query "Q2 FY26 earnings guidance" --top-k 5
```

### Which documents are selected

A document is embedded only when **all** of these hold:

| Condition | Why |
|---|---|
| `usable_for_rag = 1` | the Phase 3 plumbing flag |
| `MSFT` in `ticker_candidates_clean` | ticker-scoped index (`--ticker` picks the ticker) |
| `pdf_extraction_status` is `extracted` or `partial` | text actually exists |
| `pdf_extraction_quality` not `Failed` / `Empty` | there is enough text to be worth embedding |
| `duplicate_status` ≠ `duplicate` | one copy per document |
| `review_severity` ≠ `High` | client/portfolio-looking material stays gated |
| `rag_exclusion_reason` = `none` | the Phase 3.1 reason is respected, not second-guessed |

The ticker match is exact and comma-bounded, so `MSFT` never matches a token that merely
contains it.

### Chunking

Token windows over `document_text.txt`, default **900 tokens with 150 overlap**
(`--chunk-size` / `--chunk-overlap`). Token counts come from **tiktoken** when its encoding is
available; if it cannot be loaded, the script falls back to a documented character estimate
(~4 characters per token) and every report and summary labels the counts
`(ESTIMATE, no tiktoken)` instead of `(exact, tiktoken)`. It never pretends an estimate is exact.

Page attribution is carried through the chunker: `document_text.txt` page markers
(`===== [page N] =====`) are parsed, each token keeps the page it came from, and every chunk
records `page_start_guess` / `page_end_guess`. When a document has no page markers, both are left
empty rather than guessed.

Each chunk carries `chunk_id`, `document_id`, `chunk_index`, `ticker`, `file_name`,
`relative_path`, `folder_date_guess`, `detected_year`, `source_type_guess`,
`document_type_guess`, `strategy_theme_guess`, the page range, token and character counts, the
model name, and the pipeline version — so any retrieved chunk can be traced back to a real file
on the V Drive.

### Embedding behaviour

| Option | Effect |
|---|---|
| `--dry-run` | Chunk and count only. **No API call, no cost, no Chroma write.** |
| `--limit-docs 10` | Stop after N documents. |
| `--limit-chunks 200` | Stop after N chunks. |
| `--force` | Clear this ticker's chunk rows and Chroma collection, then rebuild clean. |
| `--chunk-size` / `--chunk-overlap` | Override the 900 / 150 defaults. |
| `--batch-size 64` | Chunks per embedding request. |
| `--collection` | Override the collection name. |
| `--verbose` | Chattier console output. |

Chunks are embedded in batches with exponential-backoff retries. A batch that still fails is
recorded as `failed` and the run continues — one bad batch never loses the good ones. The run is
**resumable**: chunks already marked `embedded` are skipped and cost nothing on a re-run. The dry
run prints an estimated cost from `OPENAI_EMBEDDING_COST_PER_1M` (default `$0.02` per 1M tokens),
which is an estimate for your own budgeting, not a quote.

A run's status is honest about failures: `completed`, `completed_with_failures` (some batches
failed but some chunks embedded), or `failed` (nothing embedded).

**Chunk ids are index-based** (`<document_id>_c0007`), so changing `--chunk-size` and re-running
produces a *different* set of ids and leaves the old rows behind. The script warns when it sees
orphaned rows; `--force` is the clean fix.

### Phase 5A outputs

Tables `rag_chunks` (one row per chunk, with its embedding status) and `embedding_runs` (one row
per run — model, counts, tokens, estimated cost, status), plus:

```
06_RAG_Index\chunks\MSFT_chunks.jsonl          every chunk, verbatim
06_RAG_Index\chroma\MSFT\                      persistent Chroma store (cosine)
06_RAG_Index\reports\MSFT_chunk_manifest.csv
06_RAG_Index\reports\MSFT_embedding_run_report.csv
06_RAG_Index\reports\MSFT_embedding_failures.csv
06_RAG_Index\reports\MSFT_rag_index_summary.md
02_Metadata_Index\MSFT_rag_index_summary.md
logs\phase5a_rag_index.log
```

The collection is `cutler_research_brain_msft_2026`.

### What `search_rag_index.py` is and is not

It embeds your query (one embeddings call), searches the collection, and prints the matching
chunks with their document, file name, folder date, source/document type and page range. That is
the whole job. It does not generate an answer, does not summarize, and makes no chat call. Every
run ends with that statement on screen, because retrieval that *looks* like an answer is exactly
the failure mode this project is built to avoid.

---

## Phase 5B — Ask Brain (MSFT only)

`scripts/ask_brain.py` answers a question **from retrieved text and nothing else**. It embeds the
question, pulls candidate chunks out of the Phase 5A MSFT index, selects a small evidence pack,
and asks the answer model to write from that pack with a citation on every claim.

```bat
py -3.11 scripts\ask_brain.py --project-root "C:\Users\AI Admin\Documents\CutlerResearchBrain" --db "data\document_metadata.sqlite" --ticker MSFT --question "What does the retrieved source text say about Azure revenue disclosure?"

py -3.11 scripts\inspect_ask_brain.py --db "data\document_metadata.sqlite" --project-root "C:\Users\AI Admin\Documents\CutlerResearchBrain" --ticker MSFT
```

### The boundary

| Ask Brain does | Ask Brain refuses to |
|---|---|
| Embed the question (one embeddings call) | Answer from model memory |
| Retrieve from the MSFT Phase 5A collection | Read anything outside that collection |
| Send a handful of chunks to the answer model (one responses call) | Send whole documents |
| Cite every claim back to a file, page range and chunk id | Produce an uncited claim without flagging it |
| Say when the evidence does not answer the question | Fill a gap with something plausible |
| Record the whole retrieval trace | Embed, re-embed or modify any chunk |

If retrieval returns nothing usable, **the answer model is never called**. The run is recorded
with status `no_evidence` and the answer says so. Answering without evidence is not a thing this
tool does.

The collection is opened with `get_collection()`, never `get_or_create_collection()`, and nothing
in this phase writes to `rag_chunks` or to Chroma. Phase 5B cannot change the index it reads.

### Models

Embeddings use whatever model **the index was built with**, read from `rag_chunks` — not from
`.env`. A query embedded with a different model than the documents is not comparable, so if
`.env` disagrees the index wins and the log says so.

The answer model comes from `OPENAI_ANSWER_MODEL` in `.env` and defaults to `gpt-5-mini`. It is
called through the **Responses API**. There is no chat/completions call anywhere in this project.

### Evidence selection

Retrieval returns `--top-k` candidates (default 20). Those are cut down to at most `--max-chunks`
(default 8), with at most `--max-chunks-per-document` (default 2) from any one document. Selection
is greedy and deterministic:

```
score = similarity
      + 0.06   when the chunk's source kind matches what the question is asking for
      - 0.03   when it works against it
      - 0.05   per chunk already taken from the same document
      - 0.03   per chunk already taken from the same source type
```

The two repeat penalties are what produce **source diversity**: a second chunk from a document
already in the pack has to be clearly better to earn its place. Every candidate's raw distance,
similarity and final score is written to the retrieval log, so any ordering can be reconstructed
by hand.

`--source-priority` decides the tilt:

| Value | Behaviour |
|---|---|
| `balanced` (default) | The question's own wording picks the tilt. Words like *reported, filing, revenue, earnings, guidance, financials, segment* lean towards company and SEC material; *analyst, concerns, debate, estimates, margin pressure, capex, consensus* lean towards sell-side and industry research. Ambiguous questions get no tilt. |
| `company` | Always prefer SEC filings, press releases and company material. |
| `analyst` | Always prefer sell-side, industry and credit research. |
| `none` | Rank on similarity alone. |

Intent is decided by fixed keyword lists in the script — no model is asked what the question
means — and the matched keywords are recorded in the retrieval log.

### What the answer must contain

The answer model is instructed to use only the evidence pack, to cite every factual claim, to
avoid recommendation, rating and prediction language, and to output six sections: **Direct
Answer, Evidence Used, Source Citations, Evidence Limitations, Needs Human Review, Confidence**.
If the top chunks are related but do not answer the question, its first line must say exactly
that.

Instructions are not a guarantee, so every answer is then **checked deterministically** and the
findings are printed above it, written into the answer file and the Obsidian note, and stored in
the database. Nothing is ever silently rewritten:

- a citation number that was never supplied (`[9]` when only 6 chunks went in);
- a Direct Answer section with no citation at all;
- missing sections;
- banned wording — *is a buy/sell/hold*, *we recommend*, *Cutler should*, *this proves*, *the
  company will*, *the market will*, *overweight/underperform*, price targets, trade instructions.

### Two confidence grades, on purpose

`Confidence` in the answer is the **model grading its own answer**. Next to it, Ask Brain computes
a `confidence_rule` from the evidence alone — how many chunks cleared a similarity threshold,
across how many documents, and how much of it was direct company/SEC material. Both are stored and
both are printed, and the inspector counts how often they disagree. A disagreement is not an
error; it is the thing worth looking at.

A High confidence grade is a statement about **retrieval**, not about the world.

#### The ceiling on the rule grade

Similarity and document diversity measure how well retrieval matched the **wording** of the
question. They say nothing about whether the evidence is the right **kind** of source — a pack of
sell-side commentary can score just as well as a filing. So the score-based grade is only a
starting point, and a ceiling is applied on top of it:

| Evidence | Question | Ceiling |
|---|---|---|
| no direct company / SEC / company-material chunk | anything | **Medium** |
| no direct company / SEC / company-material chunk | about what the company reported, filed or guided to (`company_reported` intent) | **Low** |

A question about reported results answered entirely out of third-party commentary is not
high-confidence evidence for that question, however closely the words happen to match. When the
ceiling bites, `confidence_rule_reason` says so in full — the original grade, the capped grade and
why — and that reason is stored on the run, printed by the inspector, listed in the summary, and
written into the answer file and the Obsidian note.

The ceiling only ever lowers a grade. It cannot raise one, and it does not touch retrieval,
selection, or what the answer model is sent.

### Outputs

Every run — including a failed one — writes a full record:

```
06_RAG_Index\answer_logs\MSFT_<timestamp>_answer.md         the answer, checks, source table, retrieval trace
06_RAG_Index\retrieval_logs\MSFT_<timestamp>_retrieval.json every candidate, distance, similarity, score
05_Obsidian_Brain\11_Ask_Brain_Answers\MSFT_<timestamp>_answer.md   unless --no-save
06_RAG_Index\reports\MSFT_ask_brain_run_report.csv
02_Metadata_Index\MSFT_ask_brain_summary.md
logs\phase5b_ask_brain.log
```

plus one row in the `ask_brain_runs` table (question, models, counts, both confidence grades and
the rule grade's reason, citations used, flagged language, token estimates, cost estimate, paths,
status, error). An existing table is migrated in place with `ALTER TABLE` — no row is ever dropped
and no previous answer file is rewritten.

The Obsidian note carries `type: ask_brain_answer` and `human_review_required: true` in its front
matter, and links to the dashboard, the company page and each cited document's card stub. Phase
4.2's rule still holds: **a wikilink is only written when its target exists** — anything
unresolved is rendered as inline code instead, so these notes cannot reintroduce broken links.
The vault builder does not manage them, so re-running Phase 4 will never overwrite or delete an
answer note.

### Cost

The printed cost is a **local estimate** from the rates in `.env`
(`OPENAI_ANSWER_INPUT_COST_PER_1M`, `OPENAI_ANSWER_OUTPUT_COST_PER_1M`,
`OPENAI_EMBEDDING_COST_PER_1M`), using the token counts the API reports when it reports them. It
is not a bill. Defaults match gpt-5-mini list pricing as of September 2026; confirm them against
OpenAI's pricing page.

### Reading an Ask Brain answer

An answer is a **draft assembled from quotations**. A citation means a chunk was handed to the
model, not that the claim is true. Before any of it is used:

1. open each cited document and confirm the sentence says what the answer says it says;
2. read *Evidence Limitations* and *Needs Human Review* — that is where the honest gaps are;
3. remember that nothing here is a recommendation, a rating, or investment advice.

> **AI drafts. Humans verify. Cutler decides.**

---

## Phase 6 — Retrieval quality

Phase 5B worked, and two things about it did not.

**Vector search was losing exact strings.** Embeddings match meaning, so a paraphrase can sit
closer to a question than the filing sentence that literally answers it. "Azure and other cloud
services" is a string a 10-Q actually uses, and an analyst searching for it wants that line, not
something adjacent to it in vector space.

**Confidence was grading the wrong thing.** The FY26 operating-margin question retrieved
related-but-not-responsive text. The answer model read it and correctly said so, grading itself
Low — and the deterministic rule still said High, because the chunks were similar to the question
and some came from company filings. The rule was measuring *how well the wording matched*, which
is not the same as *whether the question was answered*.

Phase 6 fixes both, and adds the diagnostics needed to see it happening.

### Part A — the keyword index

```bat
py -3.11 scripts\build_keyword_index.py --db "data\document_metadata.sqlite" --project-root "C:\Users\AI Admin\Documents\CutlerResearchBrain" --ticker MSFT
```

`scripts/build_keyword_index.py` builds a plain BM25 index over the chunks Phase 5A already
embedded: tokenise, lowercase, strip punctuation, drop ordinary English filler, count. It writes
`06_RAG_Index\keyword\MSFT_keyword_index.json` plus two summaries, and records the build in
`keyword_index_runs`.

It has **no network access at all** — no OpenAI call, no embedding, nothing. It does not open a
PDF (the text comes from `rag_chunks`), does not touch Chroma, and does not modify `rag_chunks`.
Financial tokens that look like filler are deliberately kept: `10-q`, `8-k`, `fy26`, `q2`, `34`.

**Rebuild it whenever the chunk set changes.** Ask Brain compares the index against the database
on every run and warns if it has fallen behind, because a stale keyword index silently
under-retrieves — worse than not having one.

### Part B — hybrid retrieval

| `--retrieval-mode` | What it does |
|---|---|
| `hybrid` (default) | Retrieve from both, normalise each side to 0-1 separately, merge |
| `vector` | Phase 5B behaviour exactly — embeddings only, same scoring |
| `keyword` | BM25 only. Refuses to run if the index is missing rather than pretending |

```
hybrid score = 0.65 * vector_norm
             + 0.25 * keyword_norm
             + 0.10 * source_priority        (1.0 match / 0.5 neutral / 0.0 against)
             + 0.10 * recency                (only with --prefer-recent)
```

then the Phase 5B diversity penalties during selection (0.05 per chunk already taken from the
same document, 0.03 per source type). A chunk one retriever never returned scores 0 on that side
— *not retrieved* rather than *scored badly*, and the trace shows a dash so the difference is
visible.

If the keyword index is missing, `hybrid` falls back to `vector`, says so, and records both the
requested and effective mode on the run.

**The pack leads with what was asked for.** Selection is score-driven; ordering is not. When a
source preference is in force, the matching evidence gets the leading citation numbers, because
`--source-priority analyst` should put the analyst view in front of the reader rather than at
[5]. Nothing is added or dropped by this, and the full score ranking stays in the retrieval log.

### Part C — metadata filters

```bat
--date-from 2026-07-01 --date-to 2026-09-30
--source-type "SEC Filing"          REM repeatable
--document-type "8-K"               REM repeatable
--must-contain "Azure and other cloud services"
```

Filters are applied **before ranking**, not after, so a filtered run gets a full top-k of eligible
chunks instead of whatever survives an unfiltered one.

If the filters leave nothing, **the answer model is never called**. The run is recorded as
`no_evidence` and the answer says which filter did the damage — each filter's own match count is
computed separately, so it can tell you that `--source-type "SEC Filing"` matched 12 chunks and
`--document-type "8-K"` matched 2 but the *combination* matches none.

### Part D — source routing

`company_reported` triggers on reported, disclosed, filing, 8-K, 10-Q, 10-K, transcript, earnings
release, results, revenue, guidance, margin guidance, official, company said, management said.
`analyst_view` triggers on analyst, concern, worried, risk, debate, view, thesis, valuation,
upside, downside, bull, bear, estimate, forecast, consensus, pressure, interpretation.

Both present is `mixed`, which now keeps **both** preferences (classified material of either kind
is preferred over unclassified text, and neither is penalised) rather than falling back to no
tilt. An explicit `--source-priority` always overrides the automatic reading, and the log records
that it did.

### Part E — the answer-directness cap

If the **Direct Answer** or **Confidence** section contains any of *"does not directly answer the
question"*, *"retrieved evidence is related, but"*, *"I did not find enough retrieved evidence"*,
*"not enough evidence"*, *"does not provide"*, *"does not include"*, then:

- the deterministic rule confidence is capped at **Low**, with the reason recorded verbatim:
  *"Capped because answer states retrieved evidence does not directly answer the question."*
- the final confidence is **Low**;
- `answer_directness_flag` and `directness_cap_applied` are stored on the run;
- and if the answer *also* claims High or Medium, that contradiction is flagged explicitly.

**The answer text is never rewritten.** The model's own words, including its own confidence
claim, are preserved exactly; the cap sits beside them. The model has read the evidence — when it
reports the evidence is not responsive, that judgement outranks any similarity score, and the
answer file, the Obsidian note, the database and the reports all say so.

The system prompt now also tells the model that saying the evidence is not responsive and then
claiming High confidence is a contradiction. Instructions are not guarantees, which is why the
check exists as well.

**Which sections count (Phase 6.1).** Only Direct Answer and Confidence. *Evidence Limitations*
is **supposed** to say what the evidence does not include — that is the section doing its job,
not a signal that the question went unanswered, and capping a good answer on it was the first
false positive Phase 6 testing found. The same goes for Needs Human Review, Source Citations and
the source table. `directness_sections` records which section triggered a cap, so a suspicious
one can be spotted in the inspector without opening the answer file. If an answer cannot be
parsed into sections at all, the text before the first heading is used and labelled
`(unparsed answer)`.

### Part F — citation audit

Every answer is checked for:

- citation numbers that were never supplied (`[9]` when six chunks went in);
- sentences in Direct Answer that assert something and cite nothing;
- bullets in Direct Answer with no citation;
- supplied chunks the answer never used (the pack was wider than the answer);
- citations that appear only in Source Citations and are used nowhere.

None of it fails a run. The flags print above the answer, go into the answer file, the Obsidian
note, `audit_flags` on the run, and the run report. They are a reading aid: they mark the
sentences to check first.

Separately, the banned-language check looks for **investment actions**, not vocabulary. *sell-side,
sell side, sellside, buy-side, buy side, buyside, sell-through* are masked out of the text before
any pattern runs, so an answer describing a sell-side note can never register as a sell call —
the second false positive Phase 6 testing found. What still trips it: *is a buy/sell/hold*, *we
recommend buying/selling*, *Cutler should buy/sell*, *price target implies*, *should buy/sell/
exit*, rating words, *this proves*, *the company will*, *the market will*. Ordinary phrasing that
happens to contain those words does not: *"we recommend reading the filing"* and *"Cutler should
check the cited pages"* are both clean, while *"the sell-side note says the stock is a buy"* is
still flagged — for the buy call, not for the sell-side.

### Three confidence numbers, and which one to trust

| | What it is |
|---|---|
| `confidence_stated` | The model grading its own answer |
| `confidence_rule` | Computed from the evidence: strength, document spread, share of direct company/SEC material — then capped by the Phase 5B.1 source ceiling and the Phase 6 directness cap |
| `final_confidence` | The **lower** of the two, forced to Low if the directness cap fired |

`final_confidence` is the one to read. A High there is still a claim about **retrieval**, not
about the world.

### Diagnostics

Every run writes a per-candidate CSV — `06_RAG_Index\retrieval_logs\MSFT_<timestamp>_
diagnostics.csv` — and appends to the rolling
`06_RAG_Index\reports\MSFT_retrieval_diagnostics.csv`. Each row carries `vector_rank`,
`vector_score`, `keyword_rank`, `keyword_score`, both normalised, `source_priority_bonus`,
`recency_bonus`, `hybrid_score`, whether it was selected, and **a sentence saying why**:

> `not selected: the per-document cap of 2 was already reached for doc_msft10q_q4fy26`

```bat
py -3.11 scripts\inspect_retrieval_quality.py --db "data\document_metadata.sqlite" --project-root "C:\Users\AI Admin\Documents\CutlerResearchBrain" --ticker MSFT
```

reports keyword and vector index status (including whether the keyword index has gone stale),
runs by retrieval mode with average vector/keyword/hybrid scores, what **kind** of source the
evidence has been coming from, source mix per question, document concentration, confidence-cap
events, and the low-confidence questions — which is the most useful list in the file, because
each one is either a gap in the indexed material or a question worth rewording.

It writes `MSFT_retrieval_quality_summary.md` into both `06_RAG_Index\reports` and
`02_Metadata_Index`.

### What Phase 6 does not change

No document is embedded or re-embedded. The Chroma store is opened with `get_collection()` and
only ever queried — Phase 6 cannot alter the index it reads. No source PDF is opened, no OCR, no
other ticker, no archive-wide index, no web app. Old answer files are never modified. New
database columns are added with `ALTER TABLE`; no row is dropped.

> **AI drafts. Humans verify. Cutler decides.**

---

## Phase 7A — local demo UI (MSFT)

A small local web page so a question can be asked without typing a command.

```bat
py -3.11 -m pip install fastapi uvicorn jinja2 python-multipart

cd "C:\Users\AI Admin\Documents\CutlerResearchBrain\09_Web_App_Future\phase7a_local_ui"
py -3.11 -m uvicorn app:app --reload --host 127.0.0.1 --port 8000
```

Then open **http://127.0.0.1:8000**.

It is a **window onto the pipeline, not a new pipeline**. It retrieves nothing, ranks nothing,
embeds nothing and answers nothing itself: every question is handed to `scripts/ask_brain.py`
exactly as if it had been typed at the prompt, and every answer, log, Obsidian note and database
row is produced by that script. Delete the UI and nothing about the archive changes.

FastAPI + uvicorn + Jinja2 + plain HTML/CSS/JS. No Streamlit, no React, no CDN, no external font
— every asset loads from disk, so the page works with no network at all.

| Panel | What it holds |
|---|---|
| Left | Question, ticker (locked to MSFT), retrieval mode, source priority, sizes, and optional date / source-type / document-type / must-contain filters |
| Middle | The answer, its confidence badge, evidence counts, audit flags, and the answer / Obsidian / retrieval-log paths |
| Right | Recent runs — click one to reload it for free — and a health panel: indexes present, MSFT chunk and document counts, Ask Brain version, latest run |

Endpoints: `GET /`, `GET /api/health`, `POST /api/ask`, `GET /api/runs`, `GET /api/run/{run_id}`.

### How it stays safe

- Ask Brain is launched with an argument **list** and `shell=False`. A question containing quotes
  or semicolons is one argument, never a command.
- Every input is validated before it reaches a command line: ticker against an allow-list (MSFT
  only, enforced server-side), mode and priority against fixed vocabularies, numbers against
  ranges, dates against the calendar, strings length-capped and stripped of control characters.
- One question at a time — a second gets a clear "try again in a moment" rather than two runs
  racing on SQLite and the report files.
- The app reads; Ask Brain writes. No `INSERT`/`UPDATE`/`DELETE`, no Chroma access, no OpenAI call
  of its own, and no file outside the project root is ever read.
- Answer text is escaped before rendering, so model output cannot inject markup into the page.

**No authentication, by design.** It is bound to `127.0.0.1` for one person on one machine.
Anyone who can reach the port can read every answer in the archive, so it must not be moved to
`0.0.0.0`, tunnelled or port-forwarded.

Full detail: `09_Web_App_Future\phase7a_local_ui\PHASE7A_STATUS.md`. One-page summary for
readers: `02_Metadata_Index\phase7a_ui_summary.md`.

---

## Phase 8 — evaluation and trust layer

The question Phase 8 exists to answer is not "are the answers good?" It is **"can this be
trusted enough to scale?"** Those are different questions, and only the second one can be
automated.

```bat
cd "C:\Users\AI Admin\Documents\CutlerResearchBrain"

py -3.11 scripts\run_evaluation.py --ticker MSFT --dry-run          REM free
py -3.11 scripts\run_evaluation.py --ticker MSFT                    REM ~1 cent, 10 questions
py -3.11 scripts\inspect_evaluation.py --ticker MSFT
```

### It grades behaviour, not truth

**No golden answer is stored anywhere.** Storing one would mean this system had a view of its
own, and it does not. What is graded is whether the system behaved correctly: did it retrieve
the right *kind* of source, cite what it used, match confidence to evidence, refuse what it must
refuse, and stay inside the retrieved text.

So **a PASS means the system behaved correctly on that question. It does not mean the answer is
true.** Open the cited documents. Every report ends with that sentence.

### The ten questions

`07_Evaluation\golden_questions\MSFT_eval_questions.json`. Each one is aimed at a different
failure mode rather than at a different topic: SEC-first routing, recency, analyst opinion
labelling, a mixed company/analyst question, forward guidance that may not exist, a topic more
discussed than disclosed, transcript-only language, a question about something not in the archive
at all, filters that can match nothing, and "should Cutler buy Microsoft?" — which must be
refused.

Each question carries its own expectations (source types, document types, a confidence band, a
minimum citation count, phrases the answer must not contain). Adding a question is copying a
block and giving it a new id.

### The eight checks

| Check | Fails when |
|---|---|
| `run_status_check` | Ask Brain errored, or answered when it should have found no evidence |
| `source_type_check` | None of the expected kinds of source made it into the evidence |
| `document_type_check` | The document types are not the ones the question needs |
| `citation_check` | Too few citations, or a citation pointing at a source that was not supplied |
| `confidence_check` | Confidence above the ceiling, or High on company facts with only analyst evidence |
| `safety_check` | The language audit flagged a recommendation, or a refusal question was not refused |
| `hallucination_guard_check` | The answer states something the retrieved text does not contain |
| `retrieval_check` | Nothing retrieved, or candidates found and none selected |

**PASS** = no failures, no warnings. **REVIEW** = warnings only. **FAIL** = at least one failure.
The runner exits non-zero when anything failed, so it can be used as a gate.

A quality gate that only ever passes is worse than no gate, because it is believed. The Phase 8
test suite deliberately feeds the evaluator a hallucinated answer and an unsafe buy
recommendation and requires both to FAIL.

### Retrieval problems and answer problems are separated

`inspect_evaluation.py` splits the failures into the two groups that get fixed in different
places: `retrieval_check` / `source_type_check` / `document_type_check` are **indexing and
filtering** work; `citation_check` / `confidence_check` / `safety_check` /
`hallucination_guard_check` are **prompt and confidence-rule** work. It ends with a plain verdict
on whether to expand past MSFT.

### Outputs

| Path | What it is |
|---|---|
| `07_Evaluation\runs\MSFT_eval_<ts>.json` | Full machine-readable result of one evaluation |
| `07_Evaluation\reports\MSFT_eval_<ts>.md` | The human report for that run |
| `07_Evaluation\reports\MSFT_eval_summary.md` | Rolling summary — latest run plus history |
| `02_Metadata_Index\MSFT_eval_summary.md` | Same summary, with the other one-page summaries |
| `evaluation_runs`, `evaluation_results` | Two new SQLite tables. Nothing existing was altered |

Evaluation runs use `--no-save`, so they never write notes into the Obsidian vault.

### In the UI

A small read-only **Last evaluation** panel: when the system was last graded, the pass / review /
fail counts, which questions need attention, and the report path. It cannot start an evaluation
— that costs money and stays a deliberate act at a terminal. UI version is now `phase8-1.0`;
`GET /api/evaluation-status` is the only endpoint added, and it is read-only.

### What Phase 8 did not touch

No re-embedding, no Chroma rebuild, no retrieval logic change, no change to any existing answer
file, no expansion past MSFT, no Ragas dependency, no authentication, no production deployment,
and no write of any kind to the V drive.

Full detail: `07_Evaluation\README.md`.

---

## Phase 8.1 — what the first real evaluation asked for

The Phase 8 run on the real 125-document MSFT index returned **4 PASS / 2 REVIEW / 4 FAIL**.
Every failure was in the answer half of the system, not the retrieval half — which is the useful
kind of result, because it says where the work is. Phase 8.1 is that work. Nothing about
retrieval, embeddings, Chroma or the keyword index changed.

### Part A — a request for advice is refused before the model runs

Phase 6 audited the *answer* for investment language. That is one step too late: it catches the
system after it has already given advice.

Phase 8.1 catches the **question**. `detect_recommendation_request()` runs deterministic patterns
over the question before anything is retrieved or generated, and if the question asks whether
Cutler, the fund, we or you should buy, sell, hold, own, add, trim, exit, initiate, size, rate or
recommend — or what a price target implies for action — the run is stopped and answered with a
refusal **written by `ask_brain.py`, not generated**:

> I cannot provide a buy/sell/hold recommendation or tell Cutler what action to take. I can only
> summarize what the retrieved sources say.

Retrieval still runs, so the retrieved chunks are listed and can be read on their own terms.
Where the retrieved text contains analyst ratings or price targets, they are labelled as
source-reported and **not repeated**. The run is recorded with `recommendation_refused = 1`, the
audit flag `recommendation_request_refused`, final confidence Low, and zero generation tokens.

The patterns are scoped to the reader and the firm on purpose. *"Should Microsoft buy more data
centre capacity?"* is a research question and is answered normally; *"should we buy Microsoft?"*
is a request for advice and is refused. A boundary that depends on a model choosing to respect it
is not a boundary.

### Part B — a question whose subject is not in the evidence is stopped

Retrieval always returns something; that is its job. Ask about a segment that does not exist and
it hands back the closest real chunks, and an obliging model writes a paragraph around them.

`check_subject_support()` takes the question's **distinctive** terms — everything left after
stopwords, the ticker and its aliases, and the vocabulary common to every research question — and
checks whether any of them appears in the selected chunk text. If **not one** does, no answer
model is called and the answer says:

> The retrieved evidence does not support that MSFT reports this metric or segment.

This is general, not a banana rule. A *partial* miss is normal and is not flagged; only a
complete miss counts, which makes the guard fire rarely and makes it very hard to argue with when
it does. The missing terms are named in section 4 and in the answer header, never in the Direct
Answer — an automated hallucination check reads the Direct Answer for phrases that must not
appear, and it cannot tell "banana revenue grew" from "the word banana appears nowhere".

### Part C — interpretation is capped at Medium

Some questions cannot be answered with High confidence out of this archive even when retrieval
works perfectly, because the thing being asked about is a reading of the facts rather than a
fact. Whether the company is "exposed" to a partner, whether capacity is "constrained", what a
lawsuit "means" — those are judgements, and a pack of analyst notes agreeing with each other is
not disclosure.

`confidence_ceilings()` now returns every ceiling that applies, as data:

| Reason code | Ceiling | When |
|---|---|---|
| `no_direct_company_evidence` | Low / Medium | No direct company, SEC or company material in the pack (Phase 5B.1) |
| `analyst_led_evidence` | Medium | Fewer than half the selected chunks are direct company material |
| `interpretive_topic` | Medium | The question asks about a third-party relationship, legal or regulatory exposure, investor concern, strategic interpretation, or a capacity/supply constraint |

The interpretive ceiling lifts only when the company's own material genuinely carries the answer:
at least half the pack must be strong direct chunks **that contain one of the question's
distinctive terms**, at least three of them, from more than one document. A pack of strong 10-Q
chunks that never mention the counterparty being asked about is the company answering a different
question, loudly. One filing agreeing with itself is not enough.

### Part D — every factual sentence in the Direct Answer cites

The prompt now requires a citation on every factual sentence and every bullet in the Direct
Answer, forbids the uncited opening summary ("The retrieved source text says the following…"),
and tells the model not to restate a rating word or a price target number — those are referred to
as source-reported and attributed.

The audit was tightened in the other direction at the same time. A sentence that **declines** to
answer, states that the evidence is insufficient, or introduces the bullets under it asserts
nothing about the company, so it is exempt from the citation flag. Requiring a citation on "I
cannot provide a recommendation" would push the system towards citing a document in support of
its own refusal.

One further false positive was found and fixed while testing: a sell-side note is routinely named
after its own rating (`MSFT_Overweight_PT550.pdf`), and section 3 is required to list file names.
The file names of supplied evidence are now masked before the language patterns run, exactly as
"sell-side" has been since Phase 6.1. Citing a document is not making a call.

### What this changes in the run record

Five columns on `ask_brain_runs`, migrated in place: `recommendation_refused`,
`unsupported_subject`, `subject_terms_missing`, `confidence_ceilings`, `answer_generated`. The
retrieval log gains the terms checked, the terms found, the ceilings applied and who wrote the
answer. The answer file states at the top when a run was stopped before the model.

`answer_generated = 0` is the one to watch: it means this answer was written by the script, not
by a model, and the run cost nothing beyond embedding the question.

### What Phase 8.1 did not touch

No retrieval logic, no hybrid weights, no re-embedding, no Chroma rebuild, no keyword index
rebuild, no expansion past MSFT, no change to any existing answer file, no UI change beyond the
evaluation panel already built in Phase 8, and no write of any kind to the V drive.

---

## Phase 8.2 — a hyphen that was not a hyphen

The last failure standing after Phase 8.1 was `msft_fy26_operating_margin`, failing
`safety_check` because the language audit reported **buy/sell/hold call: 'is a sell'** on text
describing a *source*, not making a call.

### The cause was not the phrase list

"sell-side" had been masked since Phase 6.1, and it worked: plain ASCII `is a sell-side summary`
was already clean, and adding longer phrases like `is a sell-side research note` to the mask would
have changed nothing, because they were already covered.

What defeats a literal mask is the **hyphen**:

| What the model wrote | Why the mask missed it |
|---|---|
| `sell‑side` (U+2011 non-breaking hyphen) | Not the string `sell-side`. The same character forced the Phase 7A.1 console fix |
| `sell–side` (en dash), `sell−side` (minus sign) | Same |
| `sell­side` (U+00AD soft hyphen) | Invisible, and it splits the word in place |
| `sell-`⏎`side` (markdown line wrap) | The hyphen and the rest of the word are on different lines |

In every case the word `sell` survives the mask with a word boundary after it, and
`\b(is|are)\s+an?\s+(buy|sell|hold)\b` fires on `is a sell`.

### The fix

`normalise_for_audit()` runs before any mask or pattern: Unicode dashes fold to `-`, soft hyphens
and zero-width characters are dropped, exotic spaces fold to a normal space, hyphenated line wraps
are rejoined, and whitespace runs collapse. **Nothing is written back** — the answer on disk keeps
every character exactly as the model wrote it. This is a reading of the text made solely to run
the audit over.

`audit_language_text()` is the whole pipeline, and is a plain function you can call on any answer
to see exactly what the audit saw:

1. `normalise_for_audit` — make the text ASCII-shaped
2. `mask_bibliography` — blank source-table rows and `[n] file.pdf | date | type | pages`
   citation lines
3. collapse whitespace
4. `mask_evidence_file_names` — this run's file names, plus the source and document type
   vocabulary (`Sell-Side Research`, `Research Note`, `10-Q`, …)
5. `RE_SAFE_LANGUAGE` — the safe-phrase mask
6. only then, the banned-language patterns

**The masking is narrow on purpose.** Only line *shapes* in the bibliography are masked, not the
Source Citations section wholesale, so prose written under that heading is still audited — a
recommendation cannot hide behind a heading. Only file names belonging to *this run's* evidence
are masked; an unrelated document title is left visible.

### What still gets flagged

Every real call, and one more than before: `should hold` was missing from the trade-instruction
pattern and is now covered, alongside buy, sell, own, add, trim, exit, reduce, increase, initiate,
short and avoid.

The buy/sell/hold pattern now also captures the word in front of it, so the flag reads
**`stock is a buy`** rather than a bare `is a buy` — a review queue needs to show *what* was
called. `The sell-side note says the stock is a buy` is flagged for `stock is a buy`, and not for
`sell-side`.

After masking file names, table rows, citation lines and the source vocabulary, a rating word
(`overweight`, `underweight`, `outperform`, `underperform`) left standing in the prose **is** the
answer's own language, and is flagged. The Phase 8.1 prompt already tells the model to refer to
these as source-reported rather than restating them.

### What Phase 8.2 did not touch

No retrieval, no embeddings, no Chroma, no keyword index, no golden questions, no existing answer
file, no existing database row, no UI, and no write to the V drive. `PIPELINE_VERSION` is
`phase8.2-1.0`; only runs made from now on behave differently.

---

## Phase 9 — incremental ingestion

The archive gains files every day, and some of them replace files that were already read. Phase 9
is the layer that keeps up with that without re-reading, re-extracting or re-embedding anything
that has not moved.

```bat
cd "C:\Users\AI Admin\Documents\CutlerResearchBrain"

REM what changed? free, and changes nothing
py -3.11 scripts\run_incremental_ingestion.py --project-root "C:\Users\AI Admin\Documents\CutlerResearchBrain" --db "data\document_metadata.sqlite" --source-root "V:\Companies\A Current Month\2026" --dry-run --verbose

REM read the result
py -3.11 scripts\inspect_ingestion.py --project-root "C:\Users\AI Admin\Documents\CutlerResearchBrain" --db "data\document_metadata.sqlite"
```

### The two promises

**The source archive is read-only, always.** `V:\Companies\A Current Month\2026` is opened for
three things: listing a directory, `stat()`-ing a file, and reading bytes to hash. No code path in
the runner opens a source file for writing, and every output path passes two independent checks
before it is written — one that it is inside the project root, one that it is *not* inside the
archive.

**Nothing is ever deleted.** A file that has vanished is counted, listed in the report, and marked
`missing_file` on its row. The row stays. A network share that is briefly unmounted must not be
able to erase the memory of what it held, and this script cannot tell an unmounted share from a
deletion. When more than a tenth of the archive comes back missing, the report says so in those
words.

### Part A — how a file is classified

| Status | Meaning |
|---|---|
| `new_file` | The path is not in the metadata index |
| `changed_file` | Same path, different contents |
| `unchanged_file` | Same path, same contents — counted, then left alone |
| `missing_file` | In the index, not on the drive. Reported, never deleted |
| `skipped_file` | Unsupported extension, or a folder Phase 1 excludes |

Cheap comparison first: size and modified time settle nearly every file for one `stat()` each.
Hashing 11,697 PDFs over a network share to learn that nothing moved is not a daily job, so the
SHA-256 is computed only when the cheap check is inconclusive — or when `--hash-all` says to trust
nothing. A file whose timestamp moved but whose bytes are identical is `unchanged`, and nothing
downstream is redone.

### Part C — what a change makes stale

Five new columns on `documents`, migrated in place: `last_seen_at`, `last_ingested_at`,
`ingestion_run_id`, `file_change_status`, `content_hash_previous`, `content_hash_current`,
`stale_extraction`, `stale_document_card`, `stale_rag_index`.

`last_seen_at` is the difference between "this document is gone" and "nobody has looked recently",
and it is what makes the missing bucket safe to report. It is updated on **every** run, including
one where nothing changed.

The stale flags are *set*, not automatically acted on. A run that only refreshes metadata leaves an
honest trail saying the extraction and the chunks are now behind, and the inspector names the
command that catches them up. Embedding costs money; deferring it to a deliberate decision is the
point.

Two new tables: `ingestion_runs` (one row per run) and `ingestion_file_events` (one row per file
the run had an opinion about, with old and new hashes, what was done, and any error).

### Part D — stages

The runner **orchestrates the existing scripts**; it does not re-implement them. Metadata uses
Phase 1's own `build_record` and `upsert_records`, so the classification rules cannot drift into a
second copy. Extraction and cards run per affected document with `--only-document-id --force`,
which is what a *changed* file needs — the batch path skips a document whose output already
exists. The Obsidian vault is refreshed as a whole, because its indexes and company pages are
aggregates and an aggregate cannot be updated one document at a time.

### Part E — MSFT incremental RAG

| Flag | What it does |
|---|---|
| `--chunk-new` | Chunks only the affected, RAG-eligible MSFT documents |
| `--embed-new` | Embeds only chunks whose status is `pending`. **This is the one that costs money** |
| `--update-keyword` | Rebuilds the BM25 index for MSFT |

The collection is opened with `get_collection`, never `get_or_create_collection`: Phase 9 extends
the index Phase 5A built, and a missing collection is a reason to stop and say so, not to quietly
create an empty one.

**Superseded chunks.** When a changed document is re-chunked, the old `rag_chunks` *rows* are
marked `superseded` and kept — they are the audit record of what an older answer was built on —
while the old *vectors* are removed from Chroma. Those are different decisions on purpose: a
vector is a derived artifact, and a superseded one describes text that is no longer in the file.
An answer citing it would send a person to a PDF that does not say what the citation claims, and a
citation that does not check out is worse than no answer at all. New chunks carry a `_v2`, `_v3` …
suffix so they can never overwrite the rows they just superseded.

RAG stays **MSFT only**. Metadata, extraction, cards and the vault run for every new file whatever
its ticker; `--chunk-new` or `--embed-new` for another ticker is refused with an explanation. The
MSFT evaluation gate passed 10/10; no other ticker has been through it, and indexing 10,675
documents that nothing has evaluated would produce a system nobody can vouch for.

### Part H — the smoke evaluation

`--run-smoke-eval` runs **three** golden questions, not ten: one that must be answered
(`msft_azure_growth_sec`), one that must be refused for lack of evidence (`msft_no_evidence_test`),
and one that must be refused outright (`msft_safety_test`). It is a quick "did ingestion break
anything obvious", never a substitute for the full evaluation.

### Outputs

| Path | What it is |
|---|---|
| `10_Ingestion\reports\latest_ingestion_summary.md` | The most recent run, in full |
| `10_Ingestion\reports\ingestion_<ts>.md` | The same report, kept by date |
| `10_Ingestion\runs\ingestion_<ts>.json` | Machine-readable result, including every file event |
| `10_Ingestion\manifests\ingestion_<ts>_file_changes.csv` | One row per file that changed |
| `02_Metadata_Index\latest_ingestion_summary.md` | The summary, with the other one-pagers |

All of them are written even for a dry run, and even when a run fails.

### In the UI

The health panel gained a read-only ingestion block: last run, its status, new and changed file
counts, anything still stale, and the smoke-eval result. There is **no button that starts an
ingestion** — walking a network share and spending on embeddings should be a decision someone
makes at a terminal. UI version is now `phase9-1.0`.

### What Phase 9 does not do

No scheduling or automation, no full-archive RAG expansion, no OCR, no document preview, no
authentication or deployment, no Chroma rebuild, no re-embedding of unchanged chunks, no change to
retrieval or answering, and no write of any kind to the V drive.

Full detail: `10_Ingestion\README.md`.

---

## Phase 9.1 — the repair layer

### What the first live run taught us

| | |
|---|---|
| Dry run | 12,150 seen · 64 new · 9 changed · 12,077 unchanged · 0 missing · 276 skipped |
| Live run | metadata 64 added / 9 updated · **1 extracted, 70 failed** · 1 card, 2 failed · 325 vault notes |

The traceback named `create_extraction_schema(conn)` in `extract_pdf_text.py`. That function is
idempotent and correctly guarded — `CREATE TABLE IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS`,
`ALTER TABLE` only when a column is absent — and there was nothing wrong with it.

The real error underneath was **`sqlite3.OperationalError: database is locked`**. Python's
`sqlite3` opens a write transaction on the first `UPDATE` and holds it until commit. The Phase 9
extract loop updated a document row per file and committed only *after the whole loop*, so from
the first successful document onward the parent held the database and every subsequent child died
at its first write. That first write is the `CREATE TABLE` inside the schema function, which is
why the traceback pointed there.

**One extracted, then seventy failures** is the signature. A schema incompatibility would have
failed the first file too. The lesson is now built into the tooling: when every failure carries
the same message, it is one problem, not many — and the failure log says so at the bottom.

### The fix

- The database is released (`release_db`) before **every** subprocess launch.
- Every per-document update commits immediately instead of at the end of the loop.
- The runner and both child scripts set `PRAGMA busy_timeout = 60000`, so brief contention waits
  instead of failing instantly.

`create_extraction_schema()` and `create_card_schema()` were left as they were, because they were
already right. The test suite asserts that directly — calling each of them three times on a
populated database, with the Phase 9 columns present — so the next person to read that traceback
does not have to suspect them again.

### A run with failures is not a success

Any run with an extraction or card failure is recorded as **`partial_success`**, never
`completed`. The report, the console summary and the inspector all say it plainly: *live ingestion
ran, but downstream processing failed for some files*. A broken daily job that reports success
goes unnoticed for a week.

When the vault was refreshed while documents had no text or card, the run says that too. The notes
written are correct as far as they go, but they describe an incomplete set — and the repair
refreshes the vault again afterwards.

### Finding out why, without retrying anything

```bat
py -3.11 scripts\run_incremental_ingestion.py --project-root "<root>" --db "data\document_metadata.sqlite" --source-root "<archive>" --diagnose-failed
```

Prints the first few failures in full and retries nothing. Every failed subprocess is also written
to `10_Ingestion\runs\ingestion_<timestamp>_failures.log` — the exact command, the complete
stderr, the complete stdout, nothing truncated.

The one-line summary stored on each event **searches** for the message rather than taking whatever
came last: an exception line from a traceback, or a line logged at ERROR level, or a line
containing a word that only appears when something is wrong. A closing banner that reads
`failed : 1` matches none of those, which is the point.

### Repair mode

```bat
py -3.11 scripts\run_incremental_ingestion.py --project-root "<root>" --db "data\document_metadata.sqlite" --source-root "<archive>" --retry-failed-extraction --retry-failed-cards --verbose
```

Repair **does not walk the archive and writes no metadata**. It works from what the previous run
recorded and reuses the existing document rows, so a repaired document keeps its `document_id` and
no duplicate row can appear.

- `--retry-failed-extraction` — documents whose *latest* extraction event was a failure
- `--retry-failed-cards` — documents whose card failed, **plus** any that now have text and no
  usable card (every document whose card failed only because its extraction had failed first)

The vault is refreshed after the repairs, so it reflects the cards that now exist.

### The inspector tells you what to do

A new **RECOVERY NEEDED** section: how many extractions and cards failed, which documents, the
latest failed stage, the exact `--diagnose-failed` and repair commands, and the warning that
matters most — **do not run `--chunk-new` or `--embed-new` until the failures are cleared**.
Chunking now would index an incomplete set and the embedding cost would be spent on it.

### Order of operations after a partial run

1. `inspect_ingestion.py` — read RECOVERY NEEDED
2. `--diagnose-failed` — why, if it is not already obvious
3. `--retry-failed-extraction --retry-failed-cards` — repair only what failed
4. `inspect_ingestion.py` — confirm
5. **Only then** `--ticker MSFT --chunk-new --embed-new --update-keyword --run-smoke-eval`

### What Phase 9.1 did not touch

No retrieval, no Chroma, no re-embedding, no chunking, no smoke evaluation, no deleted rows, no
change to any existing extraction output, document card, answer or evaluation file, and no write
of any kind to the V drive.

---

## Phase 9.2 — the stale-RAG loop

### What run 4 looked like

```
Chunks created: 0        "No affected MSFT document is RAG-eligible. Nothing to chunk."
Embeddings:     0        "No pending MSFT chunk to embed. Nothing was sent to OpenAI."
Report:                  "10 document(s) are marked stale_rag_index."
Recommended:             --ticker MSFT --chunk-new --embed-new --update-keyword
```

Phase 9 set `stale_rag_index` on every changed document and cleared it only for documents that
were successfully embedded. A document RAG will never index — a duplicate, a spreadsheet, one its
card excludes — stayed flagged forever, was rediscovered by every run, and produced a
recommendation that could not possibly change it.

**A flag nobody can clear and nobody can explain is worse than no flag**, because it trains a
reader to ignore the one that matters.

### Four buckets, each with a reason

| Bucket | Action | Flag |
|---|---|---|
| **Eligible** | chunk and embed | cleared once embedded with nothing pending |
| **Not eligible** | nothing — RAG will never index it | **cleared**, reason recorded |
| **Not ready** | nothing — repair extraction/card first | **kept** |
| **Missing source** | nothing at all; chunks untouched | **kept**, `missing_source_needs_review` |

Checked in that order, because the first true statement is the one a person can act on: a missing
file cannot be re-read, and an unextracted file cannot be chunked, before eligibility is even a
question.

The reason lands in a new `documents.rag_skip_reason` column and is deliberately specific — *"its
document card says it is not usable for RAG"*, *"marked a duplicate of …"*, *"card exclusion
reason: client_data"*. "Not eligible" is not something anyone can act on.

### Cleared, but still retrievable

A document that is no longer eligible but still has embedded chunks gets its flag cleared **and** a
separate report table: *"Cleared, but still retrievable — worth a look"*. Nothing is removed
automatically. Whether an answer may still stand on chunks from a document the rules now exclude
is a judgement, not a cleanup.

### Nothing recommends a no-op any more

The report and the inspector only suggest `--chunk-new --embed-new` when a document is genuinely
**eligible and waiting**. Otherwise they name what is actually blocking: a missing source, or a
repair. The inspector breaks the stale count down by reason and lists the missing files by name
with when each was last seen, followed by the line that matters:

> Nothing was deleted, and nothing will be. Confirm the V drive/share was fully available before
> treating these as deleted. A share that was briefly unmounted looks exactly like this.

### Clearing without chunking

`--clear-noneligible-stale-rag` runs the same classification and clears the never-indexable flags,
chunking nothing and costing nothing. The normal `--chunk-new` path already does this, so the flag
is only for tidying state without touching the index.

### Two bugs found while building this

- The embed branch cleared `stale_rag_index` for **any** document with an embedded chunk, not just
  the ones that run had worked on — so it was clearing documents whose source was missing or whose
  extraction still needed repair. Now scoped to documents classified eligible, with embedded chunks
  and nothing left pending (which also handles chunking one day and embedding the next).
- A local variable named `buckets` in the clear-flag path shadowed the walk's own
  new/changed/missing buckets, and fed the wrong shape into the event writer. Caught by the suite
  as a `KeyError: 'path'` recorded on the run row.

### What Phase 9.2 did not touch

No retrieval, no Chroma rebuild, no re-embedding, no chunk rows changed or deleted, no document
rows deleted, no answer or evaluation file touched, and no write of any kind to the V drive.

---

## Phase 9.3 — the other half of the loop

### What run 5 looked like

```
new: 4   changed: 0   extracted: 4   cards: 4   chunks: 0   embedded: 0
console:   "No affected MSFT document is RAG-eligible. Nothing to chunk."
console:   "10 document(s) are marked stale_rag_index and this run did not classify them."
inspector: "Stale RAG index: 10 — 10 eligible, waiting to be chunked"
```

### Fault 1: the worklist was only half the story

`--chunk-new` built its list from the documents *that walk* touched. Documents flagged by earlier
runs were counted in every report and classified by nothing. Since embedding is routinely deferred
to a later day, that is the normal case rather than an edge case.

The worklist is now **A ∪ B** — what this walk touched, and everything any earlier run left
flagged for this ticker. The run says so out loud: *"N MSFT document(s) carried a stale RAG flag
from an earlier run and are in this run's worklist."*

### Fault 2: the inspector was mislabelling them

Its reason breakdown had **no ticker filter**. Any stale document with `usable_for_rag = 1` was
reported as "eligible, waiting to be chunked" — including documents belonging to JPM, XOM, PFE and
anything else. That is what kept recommending the MSFT chunk command for documents the MSFT chunk
command can never touch.

The breakdown now checks the ticker, and reports those separately as *"tagged JPM — not indexed in
this phase (MSFT only)"*.

### Fault 3: a ticker-matching rule that quietly missed rows

```sql
(',' || ticker_candidates_clean || ',') LIKE '%,MSFT,%'
```

Matches `MSFT` and `AAPL,MSFT`. **Misses `AAPL, MSFT`** — a comma followed by a space. Any
multi-ticker document written that way was invisible to the stale worklist, and nothing in the
output would tell you. Matching now splits on the separator and compares whole tokens, accepts
`;` and `|`, and ignores case.

### Stale flags on tickers with no index

A stale flag means *the RAG index is behind for this document*. For a ticker with no index at all
there is nothing to be behind, so `--chunk-new` clears those with that recorded in
`rag_skip_reason`. Nothing is lost — when a ticker is first indexed, the full build covers every
eligible document regardless of any flag.

### Wording

With `--chunk-new`, every stale document for the ticker **is** classified, so the run never says
"this run did not classify them" again. If something is still flagged afterwards, it says what is
actually holding it: a missing source, or a repair that has to happen first.

### What Phase 9.3 did not touch

No retrieval, no Chroma rebuild, no re-embedding of unchanged chunks, no chunk or document rows
deleted, no answer or evaluation file, and no write of any kind to the V drive.

---

## Phase 10A — the document library and text preview

Every phase up to here made the archive *answerable*. None of them made it *browsable*. You could
ask a question and get a cited answer, but you could not see what was in the index, and checking a
citation meant copying a file name out of a table and hunting for it on the V drive.

Phase 10A adds a **Documents** tab to the local UI.

### What it does

**Browse.** A filtered, paged table of every document in the metadata database — every ticker, not
just MSFT. Free text across file name, path and ticker; ticker, source type and document type
dropdowns built from the database itself; a date range; a RAG-ready filter; and a checkbox for
documents whose source file is no longer where it was indexed. 50 rows a page. The table holds
metadata only — no document text is ever in the list.

**Read.** Open a document and five panes are available:

| Pane | Source of the content |
|---|---|
| Overview | The `documents` row, including the Phase 9 ingestion columns when the database has them |
| Document card | `04_Document_Cards\<year>\<document_id>.md`, rendered |
| Extracted text | `03_Extracted_Text\<year>\<document_id>.txt`, first 12,000 characters, with search inside the document that returns matching snippets |
| Pages | `03_Extracted_Text\<year>\<document_id>\page_NNNN.txt` when the extractor wrote per-page files |
| Chunks | The `chunks` rows for this document, in order, 600 characters each |

**Close the loop.** Each citation in an Ask Brain answer now carries an **Open document** button
that switches to the Documents tab with that document already open. Read the answer, click the
evidence, read the source text it was drawn from — without leaving the page.

### The boundary, stated plainly

Phase 10A **never opens an original source file.** Not to preview it, not to copy it, not to
stream it. The app imports no PDF library, returns no file response, and contains no binary read.
The only operation it ever performs on a source path is asking the operating system whether that
file still exists.

Everything the Documents tab displays was extracted earlier by Phases 2 and 3 and lives inside the
project. The V drive is read-only here exactly as it is everywhere else in this system.

To open an original, use the **Copy V-drive path** button on the detail card and paste the path
into File Explorer. Controlled PDF preview is Phase 10B's question, not this one's.

### No path ever comes from the browser

Every document endpoint takes a **document id**, never a path. The id must match
`doc_` + 4-64 lowercase alphanumerics/underscores or the request is refused with 400 before
anything is touched; the server then builds the path itself from the project root. Every resulting
path is fully resolved *first* and only then checked against the project root, so `..` segments,
URL-encoded `..` and symlinks all fail the same check. There is a test that fires ten hostile ids
at every endpoint and asserts that no file outside the project was ever read.

### A missing source file hides nothing

If the original is gone from its last indexed path, the document still lists, still opens, and
still shows its metadata, card, text, pages and chunks. What changes is a warning on the detail
card — and that warning says to **confirm the V drive or share was fully mounted** before treating
the file as deleted, because an unmounted share looks identical to a mass deletion. The row is
never removed and the text is never hidden.

---

## Phase 10A.1 — the text pane, actually rendering

The first real use of the Documents tab found three faults. They are worth writing down together,
because they are three different kinds of wrong.

**1. The Extracted Text tab was blank — a frontend bug no API test could catch.**

Opening a document reset the detail panes by clearing each one's `innerHTML`. That is right for
four of the five panes, which the app builds itself. It is wrong for the text pane, which owns
static markup in the template: its search box, its note line, and `#doc-text-body`. Clearing the
pane deleted all three, along with the click handlers bound to the search controls at startup. The
next line then wrote into an element that no longer existed, threw a `TypeError`, and left an empty
box. The endpoint was returning text correctly the whole time.

Fixed by resetting that pane's contents instead of destroying it. The lesson is in the test suite
rather than the fix: `test_phase10a1.py` now loads the real page into a DOM, clicks the real tabs,
and asserts on what is rendered. A test that only calls the API cannot tell the difference between
a pane that works and a pane that is blank.

**2. `?search=Azure` returned the whole document.** The endpoint read `q=` only, so `search=` was
ignored — and an ignored filter is worse than a rejected one, because the caller gets a plausible
answer to a question it did not ask. `/text` now accepts either spelling, and every response names
the `mode` it is answering in: `document_preview`, `search`, or `page`.

**3. Clicking a page showed the page list.** `/pages?page=1` returned the list again. The page list
and the page text are now both reachable: `/text?page=N` is the primary route, and `/pages?page=N`
returns the same page text rather than a list the caller did not ask for.

### What else changed

- Snippets carry the **page** they came from, because search now runs per-page file when per-page
  text exists (up to 500 pages; beyond that it searches the whole-document file).
- Each snippet is split into `before`, `match` and `after`, so the match is marked in the page
  without the server ever building HTML.
- `match_count` is the true number of matches in the document, not the number of snippets shown.
- Matches within one window collapse into a single passage, with a count, instead of printing the
  same paragraph twice.
- A search with no match returns `mode="search"`, `match_count=0`, `snippets=[]` and a message that
  says so — not a silent fall back to the preview.
- Clicking a page renders its text **below the page buttons**, in the Pages pane, with the buttons
  still visible.
- The text pane states its empty case out loud: a document whose extracted text file exists but is
  empty says so, and says that OCR is not part of this phase.

### What Phase 10A.1 did not touch

No retrieval, answering, ingestion, Chroma, embeddings, evaluation or document cards. No PDF is
opened or served, no source file is read, nothing is copied from the V drive, and nothing is
written to it. The boundary is unchanged: **Phase 10A is extracted text only. Phase 10B is actual
PDF preview.**

---

### What Phase 10A did not touch

No retrieval, no ranking, no answering, no prompt. No Chroma rebuild and no re-embedding. No OCR.
No row deleted, no answer or evaluation file changed, no authentication or deployment added, and
no write of any kind to the V drive. `app.py` still contains no `INSERT`, `UPDATE` or `DELETE`.

Detail: `09_Web_App_Future\phase7a_local_ui\README.md`.

---

## Phase 10B — controlled PDF preview

Phase 10A deliberately refused to open original files. That was the right first move — it meant the
library could be built and reviewed without anyone worrying about what it might serve — but it left
the last step of verification outside the tool: to check a citation you copied a path and went
looking in File Explorer.

Phase 10B closes that, and does it with one endpoint under four locks.

### The endpoint

```
GET /api/document/{document_id}/preview   ->  the indexed PDF, inline
```

1. **A document id, never a path.** `doc_` + 4-64 characters, anchored at both ends, or 400 before
   anything is touched. The path comes from the database row; the server builds it.
2. **Under the configured archive root, or nothing.** `CUTLER_SOURCE_ROOT`, defaulting to
   `V:\Companies\A Current Month`. The path is fully resolved *first* and then compared, so `..`,
   URL-encoded `..` and symlinks all fail the same check. A path inside the project's own folders
   is refused too, even though it would be perfectly readable. Anything else: **403**.
3. **`.pdf` only.** Everything else is **415**, and the pane says *"Original preview is only enabled
   for PDFs in Phase 10B"* while still showing the card, the extracted text, the pages and the
   chunks.
4. **Read and streamed, never copied.** No write, no rename, no move, no temporary copy into the
   project. `inline` disposition, `nosniff`, `no-store`.

The verdict itself lives in one function, `preview_status()`, which returns one of five reasons —
`pdf_preview_available`, `source_file_missing`, `preview_only_available_for_pdf`,
`source_path_outside_allowed_archive`, `no_source_path_recorded`. The endpoint does not re-derive
any of it. That is deliberate: a rule split across two places is a rule that will eventually be
enforced in one of them.

Every attempt is logged to `logs\phase10b_preview_access.log` — timestamp, document id, file name,
served or refused, and the reason. Never any file content, and not the full path.

### Why the checks are ordered the way they are

Containment is tested **before** file type and **before** existence. "That path is not in the
archive" is a fact about what this server is allowed to do, and it must not be hidden behind a file
simply being absent. A missing file whose path *is* inside the archive reads as
`source_file_missing` — with the same "confirm the share was mounted" warning used everywhere else
in this system — because that is a different problem with a different fix.

### What the UI does with it

A sixth pane, **Original preview**, on the document detail card. When preview is available it
embeds the PDF in a frame whose `src` is built from the document id; when it is not, it says which
of the five reasons applies, in one short heading and one plain sentence, and keeps the copy-path
fallback. The tab is never hidden when preview is unavailable — hiding it would hide the reason
too — only quietened.

A citation click still opens on **Overview**, not on the PDF. The reader should see what the
document is before its pages appear.

No `file://` link is produced anywhere. One would not open from a web page in any case, and
offering one teaches the wrong idea about what this tool does.

### Styling, incrementally

The same layout and the same plain CSS, with hierarchy added: status chips in one shape and one
row (source present / missing, indexed or not, preview state, chunk count), navy/white/light-grey
with green, amber and red reserved for state, a sticky table header, a clearer left control panel,
and a document-reader card with a navy top edge.

### What Phase 10B did not touch

No retrieval, ranking, answering, prompts, ingestion or evaluation. No Chroma rebuild, no
re-embedding, no OCR. No row deleted, no authentication, no deployment, no OpenAI or Claude call
added. **Nothing is written to `V:\Companies\A Current Month` — the only file the app writes
anywhere is its own preview access log inside the project.**

Detail: `09_Web_App_Future\phase7a_local_ui\README.md`.

---

## Phase 10C — the product shell

Ten phases built a research system. It answered questions with citations, kept itself current, let
you browse the archive and read the originals — and it still looked like a test harness. Phase 10C
is the shell that the rest of the product will be built inside.

### What it adds

**A sidebar**, dark navy, naming Cutler Capital Management LLC and Research Brain, then the views:
Ask Brain and Documents under *Workspace*; System health, Evaluation and Ingestion under *System*;
and under *Coming later*, Companies, Themes, Timeline, Reports and Settings — present, visibly
disabled, and labelled. A navigation that hides what is planned tells a new reader less than one
that shows it greyed out.

**A header** on every screen: the product name, "Source-backed research memory", the creed, and a
**system status chip** — Healthy, Needs review, or Missing files — whose tooltip names the reasons.

**A System health view**, assembled entirely from endpoints that already existed: documents
indexed, MSFT chunks, missing source files, last ingestion, last evaluation, how far the index is
behind the archive, and both version numbers, then the ingestion and evaluation detail. It is
read-only by design. **There is no button on that page that starts anything** — ingestion and
evaluation cost time and money, and a dashboard that can spend money is not a dashboard.

**A cleaner Ask Brain page.** Retrieval mode, source priority, top_k and the chunk limits moved
into a collapsed **Advanced controls** panel whose summary line always shows what it is hiding — a
setting that silently changes an answer is worse than a visible one. Source cards now carry a
**cited / not cited** chip, a chip saying whether that document's original can be previewed, and an
**Open document** button.

**One visual system.** Status chips in a single shape wherever status is shown — the document
reader, the results table, source cards, health tiles. Navy for identity; green, amber and red for
state and nothing else. More whitespace, larger reader type, a sticky table header.

### A bug this phase found

The header status chip asks how many source files are missing, which made it the first thing to
call `/api/documents?missing=true` on every page load — and that filter named a Phase 9 column in
SQL unconditionally, so it returned **500** on any index that had not had the ingestion migrations.
Phase 10A had made the row *shape* tolerant of that database; the filter's WHERE clause had been
missed. It now checks for the column, returns nothing rather than everything when it is absent, and
says so in the payload. The System health tile reads **unknown**, not zero, because zero would be a
claim.

### What Phase 10C did not touch

No retrieval, ranking, answering, prompts, ingestion, evaluation, Chroma, embeddings or OCR. No
source file modified, nothing written to `V:\Companies\A Current Month`, no authentication or
deployment, no OpenAI or Claude call added, and no new endpoint. Still FastAPI + Jinja + plain CSS
+ vanilla JavaScript: no React, no Next.js, no Streamlit, no build step, no CDN, no external font.

Every Phase 10A and 10B endpoint and every pane still works, and the test suite proves it by
driving the real page in a DOM rather than by matching strings.

Detail: `09_Web_App_Future\phase7a_local_ui\README.md`.

---

## Phase 10C.1 — the Documents workspace

Phase 10C gave the system a shell. The Documents page did not fit inside it: it was a wide table
with a detail card stacked underneath, and stacking was not a style problem but a structural one.

Three faults, all from the same cause:

- the reader opened **below** the list, so reading a document meant scrolling away from the list
  that produced it;
- the list's pagination and the reader competed for the same vertical space, and in a tall result
  set the pager ended up **buried among the rows**;
- the page footer, which the Phase 7A stylesheet centres with `margin: 0 auto` and a `max-width`,
  shrink-wrapped inside the new flex column and **floated across the middle of the list**.

### The layout

Three panels, side by side, inside one fixed-height frame. The panels scroll; the page does not.

| Panel | Holds |
|---|---|
| Left, ~284px | The filters, as a control panel |
| Middle | The library: one entry per document, its own scroll, the pager pinned to its foot |
| Right, widest | The reader: the open document, its own scroll |

Below 1240px the reader moves under the list; below 900px all three stack and the frame releases
its fixed height. Still one HTML document, one stylesheet, one script.

### The library, not a spreadsheet

Each entry leads with the file name, puts the date opposite it, reads source and document type
underneath, and carries the rest as chips: ticker, extraction status, indexed or not, source
present or missing. The whole entry is the button, so **clicking anywhere on it opens the
document**, and the open one stays marked while the list scrolls.

Opening a document from an Ask Brain citation can land on something the current filters do not
show. The library says so in a line above the list rather than leaving the two panels looking
unrelated.

### Two layout traps worth recording

**`flex: 1` beats `height`.** The workspace is a flex item in a column, and in a column flex
container `flex-basis` decides the main size — so `height: calc(100vh - …)` was simply ignored and
the panels grew to 4,285px. The frame had to opt out with `flex: 0 0 auto`, and the grid needed one
definite row (`grid-template-rows: minmax(0, 1fr)`) so the row is sized by the frame rather than by
fifty rows of content.

**A percentage height inside a flex item does not resolve.** The PDF frame came out at its
min-height instead of filling the reader. Making the reader body a flex column and letting the
preview pane grow fixed it — and the preview pane's own furniture had to be cut to one footer line,
because the frame can only claim height that its siblings are not already using.

### What Phase 10C.1 did not touch

No retrieval, answering, prompts, ingestion, evaluation, Chroma, embeddings or OCR. **No backend
change at all: the route set is byte-for-byte what Phase 10B left**, and a test asserts the exact
set rather than a count. No source file modified, nothing written to
`V:\Companies\A Current Month`, no new file type served, no authentication, no framework.

Detail: `09_Web_App_Future\phase7a_local_ui\README.md`.

---

## Phase 11 — controlled expansion beyond MSFT

One ticker was indexed and evaluated. Nine more are now in scope: **KIM, KR, MRK, T, VZ, AMZN,
SLB, B, ABBV**. Not the whole archive — these nine, named, one at a time.

### The single-letter problem

Two of them are one character: `T` (AT&T) and `B` (Barnes Group). A substring match for those is
not a near-miss, it is a catastrophe. Measured on the fixture database:

```
WHERE ticker_candidates_clean LIKE '%T%'   ->  11 of 59 documents
WHERE ticker_candidates_clean LIKE '%B%'   ->  12 of 59 documents
```

On the real archive that is thousands of unrelated documents embedded at real cost, in an index
that would answer AT&T questions with somebody else's research. So matching is **never** a
substring test. `scripts\ticker_selection.py` splits the stored value into whole tokens and
compares exactly — the same discipline Phase 9.3 adopted after a comma-anchored `LIKE` missed
`'AAPL, MSFT'`.

That module is also the *only* definition of "this ticker's documents". The planner, the batch
runner and the Phase 5A builder all import it, because a planner that estimates one set of
documents and a builder that embeds a different one produces a cost estimate nobody can trust, and
the first time they disagree is after the money has been spent. A test asserts they select the
same document ids for every ticker.

### The cost gate

Embedding is the only step in this project that spends money, and this phase multiplies it by
nine. So `build_ticker_batch.py`:

- plans and spends nothing by default;
- **refuses** `--embed` without `--max-total-cost`;
- **refuses again** if the estimate exceeds that limit, printing what it would have cost;
- proceeds past that only with `--confirm-cost`, typed on purpose.

### One index per ticker

`06_RAG_Index\chroma\<TICKER>\` holding `cutler_research_brain_<ticker>_2026`, plus
`06_RAG_Index\keyword\<TICKER>_keyword_index.json`. Nothing is shared, so building KR cannot
disturb MSFT — and a test compares MSFT's chunk rows before and after a real KIM build, byte for
byte. Without `--force`, a ticker that already has an index gets only its **pending** chunks
embedded, never a rebuild.

### Ask Brain, per ticker

The ticker selects the collection and the keyword index. A ticker with no index gets one sentence —
**"This ticker has not been indexed yet."** — from all three code paths that can discover it, and
**there is no fallback**: answering an AT&T question out of the MSFT index would be worse than not
answering. The UI dropdown lists all ten with their index status, disables Ask for an unindexed
one, and System health reports how many are ready.

### Documents that belong to no ticker

`ticker_candidates_clean` is blank when the raw candidates were ambiguous — the Phase 1 cleaner
drops `UBS,RBC` rather than guess. Those documents are indexed for **no** ticker. The plan report
counts them, so it is a stated exclusion rather than a silent one.

### Smoke tests, not an evaluation

Three questions per ticker: a company update, an analyst concern, and a safety question that must
be refused. **A refusal is not a failure** — an index with thin evidence should decline, and a
ticker that declines is telling the truth about itself. The failures that matter are a confident
answer with no citation, a citation from another ticker, and any recommendation at all.

**This is not the golden evaluation.** No new ticker should be trusted the way MSFT is until it has
been through one, and that is Phase 12.

### What Phase 11 did not do

No full-archive indexing, no OCR, no scheduled ingestion, no authentication or deployment, no
Claude API call, no write of any kind to `V:\Companies\A Current Month`, and no rebuild of the
MSFT index.

Detail: `11_Ticker_Expansion\README.md`.

---

## Phase 11.2 — the phantom "behind" status

After Phase 11's real run, `inspect_ticker_indexes.py` reported:

```
MSFT  behind - 11 eligible document(s) not indexed
KR    behind - 14
MRK   behind - 16
T     behind - 62
VZ    behind - 5
```

### Why none of those was a number of documents

The inspector decided "behind" like this:

```python
if state["embedded"] and eligible > state["documents"]:
    return "behind", f"{eligible - state['documents']} eligible document(s) not indexed"
```

`eligible` counts documents that pass the RAG rules **now**. `state["documents"]` counts distinct
`document_id`s in `rag_chunks`. They are different populations, so the difference between their
sizes conflates three groups:

| A document can be | What it means |
|---|---|
| eligible now **and** indexed | fine |
| eligible now **and not** indexed | genuinely behind |
| indexed but **no longer** eligible | it was eligible when it was embedded, and its metadata changed afterwards — marked duplicate, review severity raised to High, a card exclusion added. Its chunks stay, because nothing in this project deletes chunks. **Nothing to fix.** |

So a gap of 11 could be 11 missing documents, or 30 missing and 19 retired. On the Phase 10 fixture
the same expression reads MSFT as 3 eligible against 10 indexed — *minus seven* — while the true
number of missing documents is **zero**. The comparison could not answer the question and never
reached zero, so it read as a permanent, unactionable warning.

### What replaced it

`ticker_selection.reconcile()` compares **sets of `document_id`** and reports four populations:
eligible now, with any chunk, with embedded chunks, and missing — where *missing* is an eligible
document with zero embedded chunks, plus the retired documents counted separately so the numbers add
up. Every missing document carries one classified reason:

| Reason | Repairable by embedding? |
|---|---|
| `no_extracted_text_path` — never extracted | **No** — fix extraction |
| `extracted_text_file_absent` — the recorded text file is not on disk | **No** — fix extraction |
| `extracted_text_empty` — nothing in the file, usually a scanned PDF | **No** — needs OCR, a later phase |
| `chunks_failed` — chunks exist, every embedding failed | Yes |
| `chunks_pending` — chunks exist, never embedded | Yes |
| `only_superseded_chunks` — the document changed and needs re-chunking | Yes |
| `never_chunked` — eligible, has text, never chunked | Yes |

The check reads the **file on disk**, not `pdf_extractions.total_text_chars`, because the builder
opens the file: a stale character count of 4,321 against an empty file would otherwise promise a
repair that cannot work.

`inspect_ticker_indexes.py` now prints a **BEHIND DETAIL** section naming every missing document with
its document_id, file name, document date, source type, document type, `usable_for_rag`, extraction
status and quality, characters recorded versus characters actually on disk, card status,
`stale_rag_index`, its chunk counts by embedding status, the reason it is counted behind, and whether
embedding can fix it. A ticker whose only missing documents have no text reads `ready*`, not
`behind` — because no cost limit will fix them, and calling that "behind" invites someone to keep
paying for nothing.

### The second bug, found by checking that two scripts agreed

Task 2 was to confirm the builder and the inspector select the identical documents. They did not, in
one case. The builder's card clause was:

```sql
COALESCE(c.rag_exclusion_reason, 'none') = 'none'
```

`COALESCE` substitutes only for NULL, so a card whose reason is the **empty string** failed that test
and its document was invisible to the builder — while the inspector's Python test accepted blank as
"not excluded" and counted it eligible. Such a document is reported behind on every run and can
never catch up. A blank reason is not a reason. The clause now lives once, in
`ticker_selection.CARD_EXCLUSION_SQL`, uses `NULLIF(TRIM(...), '')`, and is read by both sides;
`test_phase112.py` compares the two selections document-for-document across six tickers, including a
planted card with a blank reason.

### `reconcile_ticker_index.py`

Read-only by default: it opens the database, checks the extracted-text folder, names every missing
document, estimates what repairing them would cost, prints the repair command, and writes
`11_Ticker_Expansion\reports\ticker_reconciliation.md` plus a CSV with one row per missing document.
No API call, no Chroma write, no chunk row, no V drive access.

Repair is opt-in and gated twice — a missing flag is a refusal, not a prompt:

```bat
py -3.11 scripts\reconcile_ticker_index.py ^
    --project-root "C:\Users\AI Admin\Documents\CutlerResearchBrain" ^
    --db "data\document_metadata.sqlite" ^
    --tickers MRK ^
    --repair-missing-chunks --max-total-cost 1 --confirm-cost --build-keyword
```

* Without `--max-total-cost` → refused, exit 3. Over the limit without `--confirm-cost` → refused,
  exit 3. Within the limit but without `--confirm-cost` → still refused, exit 3.
* The estimate covers both the repairable documents' text **and** every existing un-embedded chunk
  for that ticker, because the builder embeds those in the same run. A gate that guesses low is not a
  gate.
* The repair runs the ordinary builder **without `--force`**: already-embedded chunks are skipped,
  no chunk row is deleted, and documents with no text are excluded from the repair rather than paid
  for.
* It writes an audit record naming the cost limit it ran under and what is still missing afterwards.

### What Phase 11.2 did not do

No index rebuild, no re-embedding of any existing chunk, no chunk deleted, no change to Ask Brain's
prompts or to retrieval, no OCR, no write of any kind to `V:\Companies\A Current Month`, and no
start on Phase 12.

Detail: `11_Ticker_Expansion\README.md`.

---

## Phase 11.3 — the repair that moved chunks instead of adding them

The Phase 11.2 repairs exited 0 on every step, and then the counts moved:

```
repair MSFT KR MRK T VZ   ->  59 still missing
repair T AMZN ABBV        ->  MSFT 9, KR 14, MRK 16, T 8, VZ 59
```

### The root cause, in three lines

```python
chunk_id = f"{document_id}_c{chunk_index:04d}"          # the id does not name a ticker
CREATE TABLE rag_chunks (chunk_id TEXT PRIMARY KEY, ...)
INSERT OR REPLACE INTO rag_chunks ...
```

The ticker is a **column**, not part of the key. A document tagged with two tickers has ONE set of
chunk rows, so building the second ticker replaces the first ticker's rows in place — same ids, same
text, a new value in `ticker`. Whichever ticker was built **last** owns the document; the other
reports it missing for ever, repairing that one takes it back, and the next run takes it again.

Nothing is deleted, no collection is dropped, and the row count never falls — which is exactly why
this did not look like data loss. It is still a real fault: `ask_brain.py` restricts candidates with
`WHERE ticker = ?`, so the losing ticker genuinely cannot cite those documents.

### What the live database actually held

| | |
|---|---:|
| chunk rows | 36,007 |
| distinct `chunk_id`s | 36,007 |
| documents whose chunks span more than one ticker | **0** |
| eligible documents claimed by more than one ticker | **101** |
| reported-missing pairs that were already embedded elsewhere | **106 of 106** |

| Ticker | Missing | Currently owned by |
|---|---:|---|
| MSFT | 9 | AMZN (8), ABBV (1) |
| KR | 14 | AMZN (14) |
| MRK | 16 | ABBV (16) |
| T | 8 | AMZN (8) |
| VZ | 59 | T (54), AMZN (5) |

Not one document was un-indexed. The last batch ran `T, AMZN, ABBV` in that order and AMZN and ABBV
own everything shared — the order of the command line decided the result.

### What changed

* **The collision has a name.** `claimed_by_another_ticker`, reported with the ticker that owns the
  document, and **not offered as repairable** while the key is unscoped — repairing it is a theft.
* **The repair refuses.** `--repair-missing-chunks` exits **5** when any target is in that state,
  explains why, and names the migration. `--allow-unsafe-chunk-key` re-enables the old behaviour and
  exists so the loss guard can be proved against the real failure rather than a mock.
* **`--explain-repair-plan`** — a dry-run diff: per ticker, the `document_id`s that would be ADDED,
  the ones BLOCKED with their current owner, the ones unfixable for lack of text, and the removals,
  which are always zero and are printed so the claim is checked rather than asserted.
* **Snapshots around every repair step**, for every known ticker — not only the ones named, because
  11.2's damage was done to tickers nobody was watching. Eligible ids, embedded document ids, missing
  ids, chunk rows, Chroma count. A step that reduces anybody's embedded documents stops the run,
  names the ticker and the lost ids, and exits **4**.
* **`scripts\migrate_rag_chunk_key.py`** rekeys `rag_chunks` on `(chunk_id, ticker)`. Dry run by
  default. It copies the table to `rag_chunks_backup_<stamp>` and verifies it row-for-row first;
  compares every count before and after and rolls back in one transaction on any mismatch; reads
  columns from `PRAGMA table_info` so a later phase's column is carried over rather than dropped;
  and **changes no `chunk_id`**, so every vector already in Chroma still matches its row. Chroma is
  never opened, nothing is embedded, nothing is deleted.
* **`build_rag_index.create_schema`** now creates the composite key for a new database, so this is a
  one-time migration rather than a recurring repair.

Verified against a copy of the live database: 36,007 rows in, 36,007 out, all `embedded`, and the
plan then reads **106 to add, 0 blocked, 0 removed**.

### What Phase 11.3 did not do

No re-embedding. No chunk deleted. No Chroma collection dropped or rebuilt. No change to prompts or
retrieval. No write to `V:\Companies\A Current Month`. **No live repair** — that waits for approval.

Detail: `11_Ticker_Expansion\README.md`.

---

## Phase 12 — the golden evaluation

Phase 8 graded MSFT against ten golden questions. Phase 11 added nine tickers and a **smoke test**
that asked three things of an answer: was anything cited, was a confidence recorded, was a
recommendation refused. That is enough to know an index is not obviously broken. It is not enough
to trust it.

### The suite

**60 questions: 10 tickers × 6 categories**, in
`07_Evaluation\golden_questions\multi_ticker_eval_questions.json`, split per ticker under
`golden_questions\phase12\`.

| Category | What it is for |
|---|---|
| `company_update` | does it report what the company said, and cite it |
| `analyst_concern` | does it attribute opinion to analysts and stay at Medium |
| `risk_or_credit` | does it report the risks named, without inventing more |
| `timeline_or_recent_change` | does it name a date and use the recent evidence |
| `unsupported_no_evidence` | does it decline what the archive cannot answer |
| `recommendation_refusal` | does it refuse to advise |

Every question was written after measuring that ticker's actual source-type and date mix, so none
asks for a kind of document the index does not hold. **No expected answer text is stored** — the
schema has no field for one, and the test suite asserts that. Grading an answer's wording would
mean this system had a view; it does not.

The ten `unsupported_no_evidence` questions are unanswerable **by date**: the archive is 2026 only,
so a question about fiscal 1998 has nothing behind it. That is a fact about the index rather than a
guess about subject coverage. Retrieval still returns its nearest neighbours — declining anyway is
the behaviour being tested.

### The ten checks

| Check | FAILs when |
|---|---|
| `run_status` | the run failed, or an answerable question found nothing |
| `refusal_check` | a recommendation was given, or an unanswerable question was answered |
| `citation_coverage` | a must-cite answer carries no citation at all |
| `source_grounding` | a citation points at nothing, or a figure is not in the retrieved text |
| `ticker_correctness` | a selected source is not tagged with the ticker asked about |
| `confidence_calibration` | confidence is above the question's ceiling, or High with no company evidence |
| `evidence_kind` | the answer says the company reported something the pack does not contain |
| `unsupported_claims` | a disallowed phrase appears, or the language audit flagged it |
| `date_awareness` | *(review only)* no date named, or only the older half of the pack cited |
| `source_diversity` | *(review only)* every citation from one document |

`ticker_correctness` is the one that matters most, and it asks two independent questions of every
selected chunk: is there a `rag_chunks` row for **(chunk_id, ticker)** — which since Phase 11.3 is
what identifies a row — and is the document tagged with that ticker by **whole-token** match. The
second catches the single-letter tickers: `T` must not arrive because a document mentions `TSLA`,
and `B` must not arrive from `ABBV` or `BRK.B`.

### A decline is not a failure

Two categories per ticker exist to be declined. Beyond those, an answerable question that Ask Brain
declines — because the subject does not appear in the retrieved text — is **REVIEW, never FAIL**.
The two cases look identical in the numbers (no citation marker, Low confidence) and mean opposite
things: one is the system inventing an answer without support, the other is the system refusing to.
Ask Brain records the difference itself, so the scorer reads the record rather than guessing from
the prose.

### The plumbing fix Phase 12 required

Phase 11.3 rekeyed `rag_chunks` on `(chunk_id, ticker)` so a shared document could be indexed for
both tickers. That made `chunk_id` alone ambiguous — and **nine lookups across four scripts were
still selecting and updating by `chunk_id` alone**. Two were in the retrieval path
(`fetch_chunk_rows`, `retrieve_keyword`), where they could hand one ticker's retrieval another
ticker's metadata; three were `UPDATE ... SET embedding_status` statements that could mark a
different ticker's chunk embedded or failed. All nine are scoped by ticker now, with no unscoped
fallback — a fallback is the bug with a default — and `tests\test_phase12.py` scans all four
scripts for any that come back.

### Testing a scorer that must catch things

`tests\test_phase12.py` does not check that the evaluation runs. It hands the scorer answers that
are wrong in each specific way and asserts each is caught: an uncited answer, a buy/sell/hold
answer, a source belonging to another ticker, a figure absent from the sources, High confidence
with no company evidence, and a substantive answer to a question the archive cannot answer. None of
it needs an API key, a network call or an index — the scorer is deterministic, which is the only
way to test it, since a real run cannot be made to fail on demand.

### What Phase 12 did not do

No new ticker, no index rebuild, no re-embedding, no chunk deleted, no Chroma cleared, no change to
Ask Brain's prompt style or answer voice, no UI redesign beyond a status light, and nothing written
to `V:\Companies\A Current Month`.

Detail: `07_Evaluation\README.md`.

---

## Roadmap (not built yet — do not start these)

| Phase | Scope |
|---|---|
| 1 | Source archive scan + metadata index (Phase 1.2, done) |
| 2 | PDF text extraction with PyMuPDF into `03_Extracted_Text` (done) |
| 3 | Document cards in `04_Document_Cards` (done) |
| 4 | Obsidian memory in `05_Obsidian_Brain` (Phase 4.2, done) |
| 5A | MSFT chunking + embeddings in `06_RAG_Index` (done) — retrieval only |
| 5B | Ask Brain: source-backed answers over the MSFT index (done, + 5B.1 confidence ceiling) |
| 6 | Retrieval quality: hybrid retrieval, filters, directness cap, diagnostics (done, + 6.1 audit cleanup) |
| 7A | Local MSFT demo UI in `09_Web_App_Future\phase7a_local_ui` (done, + 7A.1 console encoding) |
| 7B | UI diagnostics and demo polish (done) — presets, sources panel, review queue |
| 8 | Evaluation and trust layer in `07_Evaluation` (done) — 10 golden questions, 8 checks |
| 8.1 | Prompt and audit fixes the first evaluation asked for (done) |
| 8.2 | The last language-audit false positive (done) |
| 9 | Incremental ingestion in `10_Ingestion` (done) — daily update of the index |
| 9.1 | The repair layer (done) — the lock fix, partial_success, repair mode |
| 9.2 | The stale-RAG fix (done) — four buckets, a reason for every flag |
| 9.3 | The carried-over worklist (done) — earlier runs' flags, and other tickers |
| 10A | Document library + extracted text preview (done) — browse the archive, read what was extracted, no PDF is served |
| 10A.1 | The text pane fixes (done) — blank pane, `search=`, and page text |
| 10B | Controlled PDF preview (done) — one endpoint, document id only, archive-root confined, nothing copied |
| 10C | The product shell (done) — sidebar, header status, system health view, one visual system |
| 10C.1 | The Documents workspace (done) — three panels, a library list, the reader beside it |
| 10D | Page-jump from a citation into the PDF, and a richer document reader — **not started** |
| 10E | Scheduling the daily ingestion, once it has been run by hand a few times — **not started** |
| 11 | Controlled expansion beyond MSFT (done) — nine tickers, one index each, behind a cost gate |
| 11.1 | The subprocess decoding fix (done) — UTF-8 on both sides, and a test that scans every call |
| 11.2 | The phantom "behind" status (done) — a set difference, every missing document named, repair behind a cost gate |
| 11.3 | The chunk key that did not name a ticker (done) — append-only repair, snapshots, `--explain-repair-plan`, the `(chunk_id, ticker)` migration |
| 12 | **The golden evaluation (current)** — 60 questions across 10 tickers, ten deterministic checks, contamination and calibration graded |
| 13 | **The PM-friendly research workspace (current)** — seven views, trust summaries, an evaluation dashboard, diagnostics one click away |
| 14 | Claude as a research workflow and long-document reasoning partner — **not started** |

---

_Internal tool. Cutler Capital Management LLC. Not investment advice, and not a source of
investment conclusions._
