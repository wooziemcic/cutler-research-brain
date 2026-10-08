/* ==========================================================================
   Cutler Research Brain - Phase 7B local demo UI
   Vanilla JavaScript, no framework, no CDN.

   The markdown renderer escapes ALL html before it does anything else, so
   answer text - which is model output - can never inject markup into this
   page. The same escaping is applied to every value that comes back from the
   API, including file names and questions.
   ========================================================================== */

(function () {
  "use strict";

  var $ = function (id) { return document.getElementById(id); };
  var activeRunId = null;

  /* ---- demo presets ---------------------------------------------------- */

  var PRESETS = {
    azure: {
      question: "What does Microsoft report about Azure and other cloud services revenue " +
        "growth?",
      retrieval_mode: "hybrid", source_priority: "company", source_type: "SEC Filing",
      document_type: "", top_k: 30, max_chunks: 8, max_chunks_per_document: 2
    },
    analyst: {
      question: "What does the retrieved source text say analysts were concerned about for " +
        "Microsoft?",
      retrieval_mode: "hybrid", source_priority: "analyst", source_type: "",
      document_type: "", top_k: 30, max_chunks: 8, max_chunks_per_document: 2
    },
    margin: {
      question: "What does the retrieved source text say about Microsoft's FY26 operating " +
        "margin guidance?",
      retrieval_mode: "hybrid", source_priority: "balanced", source_type: "",
      document_type: "", top_k: 30, max_chunks: 8, max_chunks_per_document: 2
    },
    capex: {
      question: "What does the retrieved source text say about AI capex, depreciation, and " +
        "margin pressure for Microsoft?",
      retrieval_mode: "hybrid", source_priority: "analyst", source_type: "",
      document_type: "", top_k: 30, max_chunks: 8, max_chunks_per_document: 2
    }
  };

  function applyPreset(name) {
    var preset = PRESETS[name];
    if (!preset) { return; }
    $("question").value = preset.question;
    $("retrieval_mode").value = preset.retrieval_mode;
    $("source_priority").value = preset.source_priority;
    $("top_k").value = preset.top_k;
    $("max_chunks").value = preset.max_chunks;
    $("max_chunks_per_document").value = preset.max_chunks_per_document;
    setSelect("source_type", preset.source_type);
    setSelect("document_type", preset.document_type);
    $("date_from").value = "";
    $("date_to").value = "";
    $("must_contain").value = "";
    describeFilters();
    describeAdvanced();
    show($("form-error"), false);
    $("question").focus();
  }

  function setSelect(id, value) {
    var select = $(id);
    if (!select) { return; }
    var found = false;
    Array.prototype.forEach.call(select.options, function (option) {
      if (option.value === value) { found = true; }
    });
    if (!found && value) {
      // The preset names a filter this index does not contain. Offer it anyway
      // rather than silently dropping it - the run will then say so honestly.
      var option = document.createElement("option");
      option.value = value;
      option.textContent = value + " (not in this index)";
      select.appendChild(option);
    }
    select.value = value || "";
  }

  function describeFilters() {
    var parts = [];
    if ($("source_type").value) { parts.push("source type: " + $("source_type").value); }
    if ($("document_type").value) { parts.push("document type: " + $("document_type").value); }
    if ($("date_from").value) { parts.push("from " + $("date_from").value); }
    if ($("date_to").value) { parts.push("to " + $("date_to").value); }
    if ($("must_contain").value) { parts.push('containing "' + $("must_contain").value + '"'); }
    var note = $("filter-note");
    if (parts.length) {
      note.textContent = "Active: " + parts.join(" · ");
      show(note, true);
    } else {
      show(note, false);
    }
  }

  /* ---- markdown -------------------------------------------------------- */

  function escapeHtml(text) {
    return String(text === undefined || text === null ? "" : text)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function inline(text) {
    return text
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/(^|[\s(])_([^_]+)_(?=[\s).,;:!?]|$)/g, "$1<em>$2</em>");
  }

  function tableRow(line) {
    return line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|")
      .map(function (cell) { return inline(cell.trim()); });
  }

  function isDivider(line) {
    return /^\s*\|?[\s:-]*-[-\s:|]*\|?\s*$/.test(line) && line.indexOf("-") !== -1;
  }

  function renderMarkdown(source) {
    var lines = escapeHtml(source || "").split("\n");
    var out = [];
    var paragraph = [];
    var list = null;

    function flushParagraph() {
      if (paragraph.length) {
        out.push("<p>" + inline(paragraph.join(" ")) + "</p>");
        paragraph = [];
      }
    }
    function flushList() { if (list) { out.push("</" + list + ">"); list = null; } }
    function flushAll() { flushParagraph(); flushList(); }

    for (var i = 0; i < lines.length; i++) {
      var line = lines[i];
      var trimmed = line.trim();
      if (!trimmed) { flushAll(); continue; }

      if (trimmed.indexOf("|") !== -1 && i + 1 < lines.length && isDivider(lines[i + 1])) {
        flushAll();
        var head = tableRow(trimmed);
        var body = [];
        i += 2;
        while (i < lines.length && lines[i].trim().indexOf("|") !== -1) {
          body.push(tableRow(lines[i].trim()));
          i++;
        }
        i--;
        var html = "<div class=\"table-scroll\"><table><thead><tr>";
        head.forEach(function (cell) { html += "<th>" + cell + "</th>"; });
        html += "</tr></thead><tbody>";
        body.forEach(function (row) {
          html += "<tr>";
          row.forEach(function (cell) { html += "<td>" + cell + "</td>"; });
          html += "</tr>";
        });
        out.push(html + "</tbody></table></div>");
        continue;
      }

      var heading = /^(#{1,6})\s+(.*)$/.exec(trimmed);
      if (heading) {
        flushAll();
        var level = Math.min(heading[1].length + 1, 6);
        out.push("<h" + level + ">" + inline(heading[2]) + "</h" + level + ">");
        continue;
      }

      if (/^(---|___|\*\*\*)$/.test(trimmed)) { flushAll(); out.push("<hr>"); continue; }

      if (/^&gt;\s?/.test(trimmed)) {
        flushAll();
        var quote = [trimmed.replace(/^&gt;\s?/, "")];
        while (i + 1 < lines.length && /^\s*&gt;\s?/.test(lines[i + 1])) {
          i++;
          quote.push(lines[i].trim().replace(/^&gt;\s?/, ""));
        }
        out.push("<blockquote><p>" + inline(quote.join(" ")) + "</p></blockquote>");
        continue;
      }

      var bullet = /^[-*+]\s+(.*)$/.exec(trimmed);
      var numbered = /^\d+[.)]\s+(.*)$/.exec(trimmed);
      if (bullet || numbered) {
        flushParagraph();
        var wanted = bullet ? "ul" : "ol";
        if (list !== wanted) { flushList(); out.push("<" + wanted + ">"); list = wanted; }
        out.push("<li>" + inline((bullet || numbered)[1]) + "</li>");
        continue;
      }

      flushList();
      paragraph.push(trimmed);
    }
    flushAll();
    return out.join("\n");
  }

  /* ---- small helpers --------------------------------------------------- */

  function show(element, visible) { if (element) { element.hidden = !visible; } }

  function confidenceClass(value) {
    var v = String(value || "").toLowerCase();
    return (v === "high" || v === "medium" || v === "low") ? " badge-" + v : "";
  }

  function score(value) {
    return (typeof value === "number") ? value.toFixed(3) : "—";
  }

  function copyToClipboard(text, button) {
    function done(ok) {
      var original = button.getAttribute("data-label") || button.textContent;
      button.setAttribute("data-label", original);
      button.textContent = ok ? "copied" : "select it";
      window.setTimeout(function () { button.textContent = original; }, 1400);
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(function () { done(true); },
        function () { fallback(text, button, done); });
    } else {
      fallback(text, button, done);
    }
  }

  function fallback(text, button, done) {
    // No clipboard permission: put the path in a selected field so Ctrl+C works.
    var field = document.createElement("input");
    field.value = text;
    field.setAttribute("readonly", "readonly");
    field.className = "copy-fallback";
    button.parentNode.appendChild(field);
    field.select();
    var ok = false;
    try { ok = document.execCommand("copy"); } catch (exception) { ok = false; }
    if (ok) { field.parentNode.removeChild(field); }
    done(ok);
  }

  function pathRow(label, value) {
    if (!value) { return ""; }
    return "<div class=\"path-row\"><span class=\"path-label\">" + escapeHtml(label) +
      "</span><code class=\"path-value\">" + escapeHtml(value) + "</code>" +
      "<button type=\"button\" class=\"copy\" data-copy=\"" + escapeHtml(value) +
      "\">copy</button></div>";
  }

  function wireCopyButtons(container) {
    Array.prototype.forEach.call(container.querySelectorAll("button.copy"), function (button) {
      button.addEventListener("click", function () {
        copyToClipboard(button.getAttribute("data-copy"), button);
      });
    });
  }

  /* ---- answer rendering ------------------------------------------------ */

  function renderSummary(data) {
    var rows = [];
    function add(label, value) {
      if (value === undefined || value === null || value === "") { return; }
      rows.push("<div class=\"summary-item\"><span>" + escapeHtml(label) + "</span><b>" +
        escapeHtml(String(value)) + "</b></div>");
    }
    add("Run", data.run_id);
    add("Status", data.status);
    add("Final confidence", data.final_confidence);
    add("Model said", data.confidence_stated);
    add("Rule said", data.confidence_rule);
    add("Retrieval", data.retrieval_mode_effective);
    add("Source priority", data.source_priority);
    add("Filters", data.filter_summary || "none");
    add("Evidence chunks", data.selected_chunks);
    add("Documents", data.selected_documents);
    if (data.estimated_cost_usd) {
      add("Estimated cost", "$" + Number(data.estimated_cost_usd).toFixed(5));
    }

    var paths = pathRow("Answer", data.answer_path) +
      pathRow("Obsidian", data.obsidian_path || data.obsidian_answer_path) +
      pathRow("Retrieval log", data.retrieval_log_path) +
      pathRow("Diagnostics", data.diagnostics_path);

    var reason = data.confidence_rule_reason
      ? "<p class=\"summary-reason\"><b>Why that confidence:</b> " +
        escapeHtml(data.confidence_rule_reason) + "</p>"
      : "";

    var element = $("summary");
    element.innerHTML = "<div class=\"summary-grid\">" + rows.join("") + "</div>" + reason +
      (paths ? "<div class=\"paths\">" + paths + "</div>" : "");
    wireCopyButtons(element);
    show(element, true);
  }

  function renderAudit(data) {
    var audit = data.audit || { level: "ok", headline: "", items: [] };
    var element = $("audit");
    var level = audit.level || "ok";
    var title = { ok: "Audit checks passed", warn: "Audit checks: read these",
      stop: "Audit checks: do not use yet" }[level];

    var body = "<div class=\"audit-head audit-" + level + "\"><b>" + escapeHtml(title) +
      "</b><span>" + escapeHtml(audit.headline || "") + "</span></div>";

    if (audit.items && audit.items.length) {
      body += "<ul class=\"audit-list\">";
      audit.items.forEach(function (item) {
        body += "<li class=\"audit-" + escapeHtml(item.severity || "warn") + "\">" +
          "<b>" + escapeHtml(item.label || "check") + "</b>" +
          "<span class=\"audit-flag\">" + escapeHtml(item.flag || "") + "</span>" +
          "<span class=\"audit-why\">" + escapeHtml(item.explanation || "") + "</span></li>";
      });
      body += "</ul>";
    } else {
      body += "<p class=\"hint\">No uncited claims, no unused evidence, no wording this " +
        "project disallows. That is a clean answer, not a verified one - still open the " +
        "cited documents.</p>";
    }
    element.className = "audit audit-level-" + level;
    element.innerHTML = body;
    show(element, true);
  }

  function renderSources(summary, runData) {
    var card = $("sources-card");
    if (!summary || !summary.evidence || !summary.evidence.length) {
      show(card, false);
      if ($("sources-empty")) { show($("sources-empty"), true); }
      if ($("rtab-sources-count")) { $("rtab-sources-count").textContent = ""; }
      return;
    }
    if ($("sources-empty")) { show($("sources-empty"), false); }
    if ($("rtab-sources-count")) {
      $("rtab-sources-count").textContent = "(" + summary.evidence.length + ")";
    }
    renderPanelExtras(summary, runData);
    var evidence = summary.evidence;
    var uncited = evidence.filter(function (item) { return item.cited === false; }).length;
    $("sources-count").textContent = evidence.length +
      (evidence.length === 1 ? " source" : " sources") +
      (uncited ? ", " + uncited + " not cited in the answer" : "");

    var html = evidence.map(function (item) {
      var kind = String(item.source_type_guess || item.source_type || "");
      var company = /SEC Filing|Press Release|Company Material/i.test(kind);
      var analyst = /Research|Monitor|Short Sale/i.test(kind);
      var kindClass = company ? "source-company" : (analyst ? "source-analyst" : "source-other");
      var kindLabel = company ? "company / SEC" : (analyst ? "analyst / industry" : "other");
      var pages = (item.page_start_guess && item.page_end_guess)
        ? (item.page_start_guess === item.page_end_guess
          ? "p." + item.page_start_guess
          : "pp. " + item.page_start_guess + "-" + item.page_end_guess)
        : "no page info";

      // Phase 13.1. The card is an evidence record now: what the document is,
      // who published it, when, which pages, and the exact text the answer was
      // written from. Everything a reader needs to agree or disagree with a
      // claim, without leaving the answer.
      return "<div class=\"source " + kindClass + (item.cited === false ? " source-uncited" : "") +
        "\" id=\"source-card-" + escapeHtml(item.citation) + "\" data-citation=\"" +
        escapeHtml(item.citation) + "\">" +
        "<div class=\"source-top\">" +
        "<span class=\"citation\">[" + escapeHtml(item.citation) + "]</span>" +
        "<span class=\"source-name\">" +
        escapeHtml(item.title || item.file_name || "(unknown file)") + "</span>" +
        "<span class=\"source-kind\">" + escapeHtml(kindLabel) + "</span>" +
        (item.cited === false
          ? "<span class=\"chip chip-warn\">not cited</span>"
          : "<span class=\"chip chip-ok\">cited</span>") +
        "</div>" +
        "<div class=\"source-meta\">" +
        escapeHtml(item.document_date || item.folder_date_guess || "no date") + " · " +
        escapeHtml(item.publisher || "no publisher recorded") + " · " +
        escapeHtml(item.source_type || item.source_type_guess || "no source type") + " · " +
        escapeHtml(item.page_label || pages) +
        "</div>" +
        passageBlock(item) +
        "<div class=\"source-paths\">" +
        "<code>" + escapeHtml(item.relative_path || "") + "</code>" +
        "</div>" +
        // Phase 13: the retrieval arithmetic is still here, one click away. A
        // reader deciding whether to open a document does not need it; someone
        // asking why this passage ranked where it did does.
        "<details class=\"source-why\"><summary>Why this passage</summary>" +
        "<div class=\"source-scores\">" +
        "vector " + score(item.vector_score) + " · keyword " + score(item.keyword_score) +
        " · combined " + score(item.hybrid_score) + "</div>" +
        "<div class=\"source-paths\"><code>" + escapeHtml(item.chunk_id || "") +
        "</code></div></details>" +
        // Phase 10A. The whole point of the library: read a citation, then go
        // and look at what it was drawn from.
        (item.document_id
          ? "<div class=\"source-actions\">" +
            "<button class=\"button-quiet source-open\" data-document=\"" +
            escapeHtml(item.document_id) + "\" data-citation=\"" +
            escapeHtml(item.citation) + "\">Open document</button>" +
            "<button class=\"link-button source-library\" data-document=\"" +
            escapeHtml(item.document_id) + "\">Open in the full library</button>" +
            "<span class=\"chip chip-plain source-preview-chip\" " +
            "data-document=\"" + escapeHtml(item.document_id) + "\" hidden></span>" +
            "</div>"
          : "<p class=\"hint\">This chunk predates document ids, so it cannot be opened " +
            "from here. Find it by file name in the Documents tab.</p>") +
        "</div>";
    }).join("");

    $("sources").innerHTML = html;
    // Phase 13.1. "Open document" opens it in the rail, beside the answer, so
    // verifying a citation does not cost you the answer you were reading. The
    // full library is still one click further on, for browsing rather than
    // checking.
    Array.prototype.forEach.call($("sources").querySelectorAll(".source-open"),
      function (button) {
        button.addEventListener("click", function () {
          openEvidence(Number(button.getAttribute("data-citation")));
        });
      });
    Array.prototype.forEach.call($("sources").querySelectorAll(".source-library"),
      function (button) {
        button.addEventListener("click", function () {
          openDocument(button.getAttribute("data-document"));
        });
      });
    markPreviewableSources();
    show(card, true);
    evidenceState.items = evidence;
  }


  // Phase 10C. Whether a citation's original can be opened in the page is
  // useful before you click, so each source card asks once. One small request
  // per distinct document, and a failure just leaves the chip hidden.
  function markPreviewableSources() {
    var chips = $("sources").querySelectorAll(".source-preview-chip");
    var seen = {};
    Array.prototype.forEach.call(chips, function (chip) {
      var documentId = chip.getAttribute("data-document");
      if (!documentId || seen[documentId]) {
        if (seen[documentId]) { applyPreviewChip(chip, seen[documentId]); }
        return;
      }
      seen[documentId] = "pending";
      fetch("/api/document/" + encodeURIComponent(documentId))
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (data) {
          var preview = data && data.preview ? data.preview : null;
          if (!preview) { return; }
          seen[documentId] = preview;
          Array.prototype.forEach.call(
            $("sources").querySelectorAll('.source-preview-chip[data-document="' +
              documentId + '"]'),
            function (node) { applyPreviewChip(node, preview); });
        })
        .catch(function () { /* the chip simply stays hidden */ });
    });
  }

  function applyPreviewChip(chip, preview) {
    if (!preview || preview === "pending") { return; }
    if (preview.enabled) {
      chip.className = "chip chip-info source-preview-chip";
      chip.textContent = "PDF preview";
    } else if (preview.reason === "source_file_missing") {
      chip.className = "chip chip-stop source-preview-chip";
      chip.textContent = "source missing";
    } else {
      chip.className = "chip chip-plain source-preview-chip";
      chip.textContent = "no preview";
    }
    chip.hidden = false;
  }

  // The advanced panel is collapsed by default, so the settings it hides are
  // summarised on its own summary line. A hidden setting that silently changes
  // an answer is worse than a visible one.
  function describeAdvanced() {
    var note = $("advanced-note");
    if (!note) { return; }
    var modeLabel = $("retrieval_mode").options[$("retrieval_mode").selectedIndex];
    var priorityLabel = $("source_priority").options[$("source_priority").selectedIndex];
    note.textContent = (modeLabel ? modeLabel.text.split(":")[0] : "") + " · " +
      (priorityLabel ? priorityLabel.text : "") + " · " +
      $("max_chunks").value + " passages · " + $("top_k").value + " candidates" +
      ($("ticker").value ? " · " + $("ticker").value : "");
  }

  function renderDiagnostics(runId) {
    var card = $("diagnostics-card");
    var target = $("diagnostics");
    if (!runId) { show(card, false); return; }

    show(card, true);
    target.innerHTML = "<p class=\"muted\">Loading…</p>";

    fetch("/api/diagnostics/" + encodeURIComponent(runId) + "?limit=20")
      .then(function (response) { return response.json(); })
      .then(function (data) {
        if (!data.available) {
          target.innerHTML = "<p class=\"muted\">" +
            escapeHtml(data.note || "Diagnostics were not recorded for this run.") + "</p>";
          return;
        }
        var head = "<div class=\"summary-grid\">" +
          "<div class=\"summary-item\"><span>Candidates considered</span><b>" +
          escapeHtml(data.candidates_considered) + "</b></div>" +
          "<div class=\"summary-item\"><span>Selected chunks</span><b>" +
          escapeHtml(data.selected_chunks) + "</b></div>" +
          "<div class=\"summary-item\"><span>Selected documents</span><b>" +
          escapeHtml(data.selected_documents) + "</b></div>" +
          "<div class=\"summary-item\"><span>Avg vector (all)</span><b>" +
          score(data.avg_vector_score) + "</b></div>" +
          "<div class=\"summary-item\"><span>Avg keyword (all)</span><b>" +
          score(data.avg_keyword_score) + "</b></div>" +
          "<div class=\"summary-item\"><span>Avg combined (all)</span><b>" +
          score(data.avg_hybrid_score) + "</b></div>" +
          "</div>";

        var rows = data.rows.map(function (row) {
          return "<tr class=\"" + (row.selected ? "diag-selected" : "") + "\">" +
            "<td>" + escapeHtml(row.rank) + "</td>" +
            "<td>" + (row.selected ? "yes" : "no") + "</td>" +
            "<td>" + (row.citation ? "[" + escapeHtml(row.citation) + "]" : "") + "</td>" +
            "<td>" + escapeHtml(row.kind) + "</td>" +
            "<td>" + escapeHtml(row.file_name) + "</td>" +
            "<td>" + score(row.vector_score) + "</td>" +
            "<td>" + score(row.keyword_score) + "</td>" +
            "<td>" + score(row.hybrid_score) + "</td>" +
            "<td>" + escapeHtml(row.source_type_guess) + "</td>" +
            "<td>" + escapeHtml(row.document_type_guess) + "</td>" +
            "<td class=\"diag-reason\">" + escapeHtml(row.selection_reason) + "</td>" +
            "</tr>";
        }).join("");

        target.innerHTML = head +
          "<p class=\"hint\">Showing the top " + escapeHtml(data.shown) + " of " +
          escapeHtml(data.candidates_considered) + " candidates. A dash means that retriever " +
          "never returned the chunk at all - not that it scored zero.</p>" +
          "<div class=\"table-scroll\"><table class=\"diag\"><thead><tr>" +
          "<th>rank</th><th>sel</th><th>cite</th><th>kind</th><th>file</th><th>vec</th>" +
          "<th>kw</th><th>score</th><th>source type</th><th>doc type</th><th>why</th>" +
          "</tr></thead><tbody>" + rows + "</tbody></table></div>";
      })
      .catch(function () {
        target.innerHTML = "<p class=\"muted\">Could not load diagnostics for this run.</p>";
      });
  }

  // Phase 13. Everything before the first numbered section is the run's own
  // header: model names, token budgets, the rule-based confidence sentence. It
  // is the first thing a reader saw and the last thing they needed. The file on
  // disk keeps all of it, and Deep Dive links to the file.
  var RE_ANSWER_START = /^\s*#{1,6}\s*1?\.?\s*Direct Answer\s*$/im;

  function answerBody(markdown) {
    var text = markdown || "";
    var match = RE_ANSWER_START.exec(text);
    return match ? text.slice(match.index) : text;
  }

  function renderAnswer(data) {
    show($("placeholder"), false);
    show($("busy"), false);

    var badges = [];
    if (data.final_confidence) {
      badges.push("<span class=\"badge" + confidenceClass(data.final_confidence) +
        "\">Confidence " + escapeHtml(data.final_confidence) + "</span>");
    }
    if (data.status && data.status !== "completed") {
      // Phase 13.1. A status is a machine word; the badge is read by a person.
      // Only the ones this UI can actually produce are translated - anything
      // else is shown verbatim rather than guessed at.
      var STATUS_WORDS = {
        answered_from_previous_evidence: "read back from the previous answer's record",
        no_evidence: "nothing relevant was found",
        timeout: "stopped before it finished",
        failed: "the run did not finish"
      };
      badges.push("<span class=\"badge\">" +
        escapeHtml(STATUS_WORDS[data.status] || data.status) + "</span>");
    }
    if (data.retrieval_mode_effective) {
      badges.push("<span class=\"badge\">" + escapeHtml(data.retrieval_mode_effective) +
        "</span>");
    }
    if (data.directness_cap_applied) {
      badges.push("<span class=\"badge badge-low\">capped: evidence not direct</span>");
    }
    $("badges").innerHTML = badges.join("");

    // Phase 13: the answer, not the form, is now the subject of the page, and
    // the question is its heading - the answer file's own "Question:" line is
    // part of the header this UI no longer renders.
    if ($("research-main")) { $("research-main").classList.add("answered"); }
    if ($("answer-title")) {
      var asked = (data.question || ($("question") ? $("question").value : "") || "Answer");
      $("answer-title").textContent = asked;
    }
    show($("answer-block"), true);
    show($("followbar"), true);
    renderTrust(data.trust, data);
    renderFollowups(data);
    rememberContext(data);

    renderSummary(data);
    renderAudit(data);
    if ($("audit-wrap")) { show($("audit-wrap"), true); }
    if ($("deepdive-empty")) { show($("deepdive-empty"), false); }

    $("answer").innerHTML = linkCitations(renderMarkdown(
      answerBody(data.answer_markdown) || "_No answer file was found for this run._"));
    show($("answer"), true);
    // Phase 13.1. Every [n] in the answer is now a control that selects source
    // n, restores the panel and opens the document it came from. A citation you
    // cannot follow is a footnote, not evidence.
    wireCitations();
    renderFollowUpNote(data);

    if (data.process_warning) {
      $("console-text").textContent = data.process_warning + "\n\n" +
        (data.stdout_summary || "");
      show($("console"), true);
    } else if (data.stdout_summary) {
      $("console-text").textContent = data.stdout_summary;
      show($("console"), true);
    } else {
      show($("console"), false);
    }

    var target = $("answer-block") || $("answer");
    if (target && target.scrollIntoView) {
      target.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }

  /* ---- api ------------------------------------------------------------- */

  function readError(response) {
    return response.json().then(function (body) {
      return (body && (body.detail || body.error_message)) || ("HTTP " + response.status);
    }).catch(function () { return "HTTP " + response.status; });
  }

  function ask() {
    var button = $("ask");
    var error = $("form-error");
    show(error, false);

    var payload = {
      question: $("question").value,
      ticker: $("ticker").value,
      retrieval_mode: $("retrieval_mode").value,
      source_priority: $("source_priority").value,
      // Phase 13. The server maps this to top_k/max_chunks in one place, so the
      // button and the run can never disagree. The explicit numbers are still
      // sent for a caller that has edited them in the advanced drawer.
      depth: depthChoice,
      top_k: Number($("top_k").value),
      max_chunks: Number($("max_chunks").value),
      max_chunks_per_document: Number($("max_chunks_per_document").value),
      date_from: $("date_from").value,
      date_to: $("date_to").value,
      source_type: $("source_type").value,
      document_type: $("document_type").value,
      must_contain: $("must_contain").value,
      save_to_obsidian: $("save_to_obsidian").checked,
      // Phase 13.1. The follow-up context: a flag and a run id, nothing more.
      // The server looks up the previous question and its evidence from that id,
      // so the browser can never claim an answer cited something it did not.
      follow_up: !!pendingFollowUp,
      previous_run_id: pendingFollowUp ? (researchContext.runId || 0) : 0
    };

    if (!payload.question.trim()) {
      error.textContent = "Please type a question, or pick one of the presets.";
      show(error, true);
      return;
    }

    // The glyph stays. Replacing it with the word "Asking" put seven characters
    // inside a 34px square button and broke the composer every time a question
    // was asked - which is exactly the defect this phase exists to fix.
    button.disabled = true;
    button.classList.add("is-busy");
    button.setAttribute("aria-busy", "true");
    ["placeholder", "summary", "audit", "audit-wrap", "answer", "console", "answer-block",
      "trust", "followups"].forEach(function (id) {
      if ($(id)) { show($(id), false); }
    });
    show($("sources-card"), false);
    show($("diagnostics-card"), false);
    if ($("sources-empty")) { show($("sources-empty"), true); }
    resetRailDocument();
    $("badges").innerHTML = "";
    show($("busy"), true);

    fetch("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    }).then(function (response) {
      if (!response.ok) {
        return readError(response).then(function (message) { throw new Error(message); });
      }
      return response.json();
    }).then(function (data) {
      // Phase 13.1. An evidence-reference reply is not a run, so it has no run
      // id to fetch extras with - but it does carry the evidence it was written
      // from, and the rail must show it. Without this the panel emptied itself
      // and then claimed "no sources yet" underneath a tab reading "Sources (8)".
      activeRunId = data.run_id || data.derived_from_run_id || activeRunId;
      renderAnswer(data);
      if (data.run_id) {
        loadRunExtras(data.run_id);
        renderDiagnostics(data.run_id);
      } else if (data.evidence && data.evidence.length) {
        renderSources({ evidence: data.evidence },
          { ticker: data.ticker || ($("ticker") ? $("ticker").value : "") });
      }
      loadRuns();
      loadReview();
      loadHealth();
    }).catch(function (exception) {
      show($("busy"), false);
      show($("placeholder"), true);
      error.textContent = exception.message;
      show(error, true);
    }).then(function () {
      button.disabled = false;
      button.classList.remove("is-busy");
      button.removeAttribute("aria-busy");
      pendingFollowUp = null;
    });
  }

  function loadRunExtras(runId) {
    // The sources panel needs the retrieval log, which /api/ask does not carry.
    fetch("/api/run/" + encodeURIComponent(runId))
      .then(function (response) { return response.json(); })
      .then(function (data) {
        renderSources(data.retrieval_summary, data.run);
        // The trust summary needs the evidence pack, which only this endpoint
        // builds. /api/ask answered first with what it had; this fills it in.
        if (data.trust) { renderTrust(data.trust, data.run); }
        rememberContext({ run_id: runId, question: (data.run || {}).question,
          ticker: (data.run || {}).ticker, evidence: data.evidence });
      })
      .catch(function () { show($("sources-card"), false); });
  }

  function loadRun(runId) {
    // Phase 13: a past answer can be opened from History, from the review
    // queue, from Related - none of which are the page the answer renders on.
    // Switching here covers every entry point, including ones added later.
    switchView("ask");
    fetch("/api/run/" + encodeURIComponent(runId)).then(function (response) {
      if (!response.ok) {
        return readError(response).then(function (message) { throw new Error(message); });
      }
      return response.json();
    }).then(function (data) {
      var run = data.run || {};
      activeRunId = run.run_id;
      renderAnswer({
        status: run.status,
        run_id: run.run_id,
        // Phase 13.1. Without this the heading fell back to whatever was in the
        // question box, so opening a past answer from History labelled it with
        // an unrelated question.
        question: run.question,
        ticker: run.ticker,
        evidence: data.evidence,
        trust: data.trust,
        answer_markdown: data.answer_markdown ||
          "_The answer file for this run is no longer on disk._",
        answer_path: run.answer_path,
        obsidian_path: run.obsidian_answer_path,
        retrieval_log_path: run.retrieval_log_path,
        diagnostics_path: data.diagnostics_path,
        final_confidence: run.final_confidence,
        confidence_stated: run.confidence_stated,
        confidence_rule: run.confidence_rule,
        confidence_rule_reason: run.confidence_rule_reason,
        directness_cap_applied: String(run.directness_cap_applied) === "1",
        citations_used: run.citations_used,
        audit: data.audit,
        retrieval_mode_effective: run.retrieval_mode_effective,
        source_priority: run.source_priority,
        filter_summary: run.filter_summary,
        selected_chunks: run.selected_chunks,
        selected_documents: run.selected_documents,
        estimated_cost_usd: run.estimated_cost_usd,
        error_message: run.error_message,
        stdout_summary: ""
      });
      renderSources(data.retrieval_summary, run);
      renderDiagnostics(run.run_id);
      markActive();
      // Opening a past answer starts a fresh context on it: a follow-up now
      // follows up on THIS answer, not on whatever was last asked.
      pendingFollowUp = null;
      clearContextNote();
      // The company follows the answer, so a follow-up on a past answer about
      // Kroger is asked of Kroger - without wiping the answer just opened.
      if (run.ticker) {
        setSelect("ticker", run.ticker);
        if (typeof describeTicker === "function") { describeTicker(); }
        applyCompanyContext(run.ticker, true);
      }
    }).catch(function (exception) {
      var error = $("form-error");
      error.textContent = exception.message;
      show(error, true);
    });
  }

  function markActive() {
    Array.prototype.forEach.call(document.querySelectorAll("li[data-run]"), function (item) {
      item.classList.toggle("active", String(activeRunId) === item.getAttribute("data-run"));
    });
  }

  function runListItem(run, extra) {
    var confidence = run.final_confidence || run.confidence_rule || "";
    return "<li data-run=\"" + escapeHtml(String(run.run_id)) + "\">" +
      "<div class=\"run-top\">" +
      "<span class=\"run-id\">#" + escapeHtml(String(run.run_id)) + "</span>" +
      (confidence ? "<span class=\"badge" + confidenceClass(confidence) + "\">" +
        escapeHtml(confidence) + "</span>" : "") +
      "</div>" +
      "<div class=\"run-question\">" + escapeHtml(run.question || "(no question)") + "</div>" +
      "<div class=\"run-sub\">" + escapeHtml(run.created_at || "") + " · " +
      escapeHtml(run.retrieval_mode_effective || run.retrieval_mode || "") + " · " +
      escapeHtml(run.status || "") + "</div>" +
      (extra || "") + "</li>";
  }

  function wireRunList(list) {
    Array.prototype.forEach.call(list.querySelectorAll("li[data-run]"), function (item) {
      item.addEventListener("click", function () { loadRun(item.getAttribute("data-run")); });
    });
    markActive();
  }

  function loadRuns() {
    fetch("/api/runs?limit=25").then(function (response) { return response.json(); })
      .then(function (data) {
        var list = $("runs");
        var runs = data.runs || [];
        if (!runs.length) {
          list.innerHTML = "<li class=\"muted\">No questions asked yet.</li>";
          return;
        }
        list.innerHTML = runs.map(function (run) { return runListItem(run); }).join("");
        wireRunList(list);
      }).catch(function () {
        $("runs").innerHTML = "<li class=\"muted\">Could not load run history.</li>";
      });
  }

  function loadReview() {
    fetch("/api/review-queue?limit=20").then(function (response) { return response.json(); })
      .then(function (data) {
        var list = $("review");
        var runs = data.runs || [];
        if (!runs.length) {
          list.innerHTML = "<li class=\"muted\">Nothing flagged. Every recorded run completed " +
            "with acceptable confidence and no audit flags.</li>";
          return;
        }
        var counts = "<li class=\"queue-summary\">" +
          escapeHtml(data.serious_runs || 0) + " serious · " +
          escapeHtml(data.weak_runs || 0) + " weak · " +
          escapeHtml(data.minor_runs || 0) + " reading flags only" +
          "<span class=\"queue-note\">Most good answers carry a reading flag - the evidence " +
          "pack is wider than any one answer needs. Serious ones are first.</span></li>";
        list.innerHTML = counts + runs.map(function (run) {
          var level = run.severity === "serious" ? "stop" : "warn";
          var reasons = (run.reasons || []).map(function (reason) {
            return "<span class=\"reason reason-" + escapeHtml(level) + "\">" +
              escapeHtml(reason) + "</span>";
          }).join("");
          var chip = "<span class=\"severity severity-" + escapeHtml(run.severity || "minor") +
            "\">" + escapeHtml(run.severity || "minor") + "</span>";
          return runListItem(run, "<div class=\"reasons\">" + chip + reasons + "</div>");
        }).join("");
        wireRunList(list);
      }).catch(function () {
        $("review").innerHTML = "<li class=\"muted\">Could not load the review queue.</li>";
      });
  }

  function loadEvaluation() {
    // Phase 8 Part H. A status light, nothing more - no way to start an
    // evaluation from here, because starting one costs money and should be a
    // decision someone makes at a terminal.
    fetch("/api/evaluation-status").then(function (response) { return response.json(); })
      .then(function (data) {
        var box = $("evaluation");
        if (!data.available) {
          box.innerHTML = "<p class=\"muted\">" + escapeHtml(data.note || "No evaluation yet.") +
            "</p>";
          return;
        }
        var rate = data.pass_rate === null || data.pass_rate === undefined
          ? "" : " (" + escapeHtml(String(Math.round(data.pass_rate * 100))) + "%)";
        var html = "<div class=\"eval-headline eval-" + escapeHtml(data.level) + "\">" +
          escapeHtml(data.headline) + "</div>" +
          "<div class=\"eval-counts\">" +
          "<span class=\"eval-pass\">" + escapeHtml(data.pass_count) + " pass</span>" +
          "<span class=\"eval-review\">" + escapeHtml(data.review_count) + " review</span>" +
          "<span class=\"eval-fail\">" + escapeHtml(data.fail_count) + " fail</span>" +
          "<span class=\"muted\">of " + escapeHtml(data.questions_total) + rate + "</span>" +
          "</div>";

        var meta = [
          ["Run", "#" + escapeHtml(String(data.eval_run_id)) + " · " +
            escapeHtml(data.completed_at || "")],
          ["Mode", escapeHtml(data.mode || "")]
        ];
        if (data.total_estimated_cost !== null && data.total_estimated_cost !== undefined) {
          meta.push(["Cost", "$" + escapeHtml(Number(data.total_estimated_cost).toFixed(4))]);
        }
        // Same row shape as the health card, so the two panels read alike.
        html += meta.map(function (row) {
          return "<div class=\"health-row\"><span>" + row[0] + "</span><span>" + row[1] +
            "</span></div>";
        }).join("");

        var attention = data.attention || [];
        if (attention.length) {
          html += "<ul class=\"eval-attention\">" + attention.map(function (item) {
            var tag = item.overall_status === "FAIL" ? "stop" : "warn";
            return "<li><span class=\"reason reason-" + tag + "\">" +
              escapeHtml(item.overall_status) + "</span>" +
              "<span class=\"eval-qid\">" + escapeHtml(item.question_id) + "</span>" +
              "<div class=\"run-question\">" + escapeHtml(item.question || "") + "</div></li>";
          }).join("") + "</ul>";
        }
        if (data.report_path) {
          html += "<p class=\"hint\">Full report: <code>" + escapeHtml(data.report_path) +
            "</code></p>";
        }
        html += "<p class=\"hint\">" + escapeHtml(data.note || "") + "</p>";
        box.innerHTML = html;
      }).catch(function () {
        $("evaluation").innerHTML = "<p class=\"muted\">Could not load evaluation status.</p>";
      });
  }

  function yesNo(value) {
    return value ? "<span class=\"yes\">yes</span>" : "<span class=\"no\">no</span>";
  }

  function loadHealth() {
    fetch("/api/health").then(function (response) { return response.json(); })
      .then(function (data) {
        var rows = [
          ["Database", yesNo(data.db_exists)],
          ["Chroma vector index", yesNo(data.chroma_exists)],
          ["Keyword index", yesNo(data.keyword_index_exists)],
          ["MSFT documents", escapeHtml(Number(data.msft_documents).toLocaleString())],
          ["MSFT chunks", escapeHtml(Number(data.msft_chunks).toLocaleString())],
          ["Ask Brain", escapeHtml(String(data.pipeline_version))],
          ["UI", escapeHtml(String(data.ui_version || ""))],
          ["Questions asked", escapeHtml(String(data.total_runs || 0))],
          ["Needing review", escapeHtml(String(data.flagged_runs || 0))]
        ];
        // Phase 9: ingestion state, read-only. There is deliberately no
        // control here that could start one.
        if (data.last_ingestion_run) {
          rows.push(["Last ingestion",
            "#" + escapeHtml(String(data.last_ingestion_run)) + " " +
            escapeHtml(String(data.last_ingestion_status || "")) + " " +
            escapeHtml(String(data.last_ingestion_completed_at || "").slice(0, 16))]);
          rows.push(["New files last run",
            escapeHtml(String(data.new_files_last_run || 0))]);
          rows.push(["Changed files last run",
            escapeHtml(String(data.changed_files_last_run || 0))]);
          if (data.missing_files_last_run) {
            rows.push(["Missing last run",
              escapeHtml(String(data.missing_files_last_run)) + " (not deleted)"]);
          }
          if (data.stale_rag_count || data.pending_chunks) {
            rows.push(["Index behind archive",
              escapeHtml(String(data.stale_rag_count || 0)) + " stale doc(s), " +
              escapeHtml(String(data.pending_chunks || 0)) + " chunk(s) pending"]);
          }
          if (data.smoke_eval_status) {
            rows.push(["Smoke eval", escapeHtml(String(data.smoke_eval_status))]);
          }
        } else {
          rows.push(["Last ingestion", "never run"]);
        }

        if (data.latest_ask_run) {
          rows.push(["Latest run", "#" + escapeHtml(String(data.latest_ask_run.run_id)) + " " +
            escapeHtml(String(data.latest_ask_run.created_at || ""))]);
        }
        var html = rows.map(function (row) {
          return "<div class=\"health-row\"><span>" + row[0] + "</span><span>" + row[1] +
            "</span></div>";
        }).join("");
        (data.warnings || []).forEach(function (warning) {
          html += "<div class=\"health-warning\">" + escapeHtml(warning) + "</div>";
        });
        $("health").innerHTML = html;

        fillSelect("source_type", data.source_types || [], "Any source type");
        fillSelect("document_type", data.document_types || [], "Any document type");
      }).catch(function () {
        $("health").innerHTML = "<p class=\"no\">Could not reach the server.</p>";
      });
  }

  function fillSelect(id, values, blankLabel) {
    var select = $(id);
    if (!select) { return; }
    var current = select.value;
    select.innerHTML = "<option value=\"\">" + escapeHtml(blankLabel) + "</option>" +
      values.map(function (value) {
        return "<option value=\"" + escapeHtml(value) + "\">" + escapeHtml(value) + "</option>";
      }).join("");
    if (current) { setSelect(id, current); }
  }


  /* ======================================================================
     Phase 11 - which tickers can answer

     A ticker in the dropdown is one this project knows the name of. Whether it
     has an index is a separate fact, read from the server, and shown next to
     the name - because a dropdown that offers ten tickers and answers for two
     is a dropdown that lies.
     ====================================================================== */

  var tickerState = { entries: [], byTicker: {} };

  function loadTickers() {
    return fetch("/api/tickers").then(function (r) { return r.json(); })
      .then(function (data) {
        tickerState.entries = data.tickers || [];
        tickerState.byTicker = {};
        tickerState.entries.forEach(function (entry) {
          tickerState.byTicker[entry.ticker] = entry;
        });

        var select = $("ticker");
        if (select) {
          var current = select.value;
          select.innerHTML = tickerState.entries.map(function (entry) {
            var label = entry.indexed
              ? entry.ticker + " · indexed"
              : entry.ticker + " · " + (entry.status === "pending" ? "pending" : "not indexed");
            return "<option value=\"" + escapeHtml(entry.ticker) + "\"" +
              (entry.indexed ? "" : " data-unindexed=\"1\"") + ">" +
              escapeHtml(label) + "</option>";
          }).join("");
          var wanted = tickerState.byTicker[current] ? current : (data.default || "MSFT");
          select.value = wanted;
        }
        describeTicker();
        return data;
      })
      .catch(function (error) {
        console.error("ticker status failed", error);
        var hint = $("ticker-hint");
        if (hint) { hint.textContent = "Could not read which tickers are indexed."; }
      });
  }

  // The Ask button is disabled for a ticker with no index, and the hint says
  // why and what to run. Refusing after the click would be honest too - the
  // server does refuse - but refusing before it wastes nobody's time.
  function describeTicker() {
    var select = $("ticker");
    var hint = $("ticker-hint");
    var button = $("ask");
    if (!select || !hint) { return; }
    var entry = tickerState.byTicker[select.value];
    if (!entry) {
      hint.textContent = "";
      if (button) { button.disabled = false; }
      return;
    }
    if (entry.indexed) {
      hint.innerHTML = "<span class=\"chip chip-ok\">indexed</span> " +
        // Phase 13: passages, not chunks. The count is the same number the
        // pipeline reports; only the word a reader sees has changed.
        escapeHtml(String(entry.reason).replace(/chunk\(s\)/g, "passages")) + " across " +
        escapeHtml(entry.documents) + " document(s).";
      if (button) {
        button.disabled = false;
        button.textContent = "\u27a4";
      }
    } else {
      hint.innerHTML = "<span class=\"chip chip-warn\">not indexed</span> " +
        escapeHtml(entry.reason) + ". Build it with " +
        "<code>scripts\\build_ticker_batch.py --tickers " + escapeHtml(entry.ticker) +
        " --embed --build-keyword</code>, then reload.";
      if (button) {
        button.disabled = true;
        button.textContent = "Not indexed yet";
      }
    }
  }

  /* ======================================================================
     Phase 10C - system health view and the header status chip

     Built entirely from endpoints that already existed. Nothing here starts a
     run, an ingestion or an evaluation: this is a window, like the rest of the
     page, and a dashboard with buttons that spend money is not a dashboard.
     ====================================================================== */

  var systemState = { loaded: false };

  function statusTone(text, prefix) {
    var p = prefix || "chip";
    var value = String(text || "").toLowerCase();
    if (/^(ok|pass|passed|completed|healthy|green)/.test(value)) { return p + "-ok"; }
    if (/^(not run|never|unknown|none)/.test(value)) { return p + "-plain"; }
    if (/(fail|error|stop|red)/.test(value)) { return p + "-stop"; }
    if (/(review|partial|warn|stale|pending|degraded|amber)/.test(value)) { return p + "-warn"; }
    return p + "-plain";
  }

  function tile(label, value, note, tone) {
    return "<div class=\"tile\">" +
      "<span class=\"tile-label\">" + escapeHtml(label) + "</span>" +
      "<span class=\"tile-value " + (tone || "") + "\">" + escapeHtml(value) + "</span>" +
      (note ? "<span class=\"tile-note\">" + escapeHtml(note) + "</span>" : "") +
      "</div>";
  }

  function missingFileCount() {
    // The library already answers this question; asking it again here would
    // mean two definitions of "missing" that could disagree.
    return fetch("/api/documents?missing=true&limit=1")
      .then(function (r) { return r.ok ? r.json() : { total: null }; })
      .then(function (d) {
        // A database without the Phase 9 columns cannot answer this. "unknown"
        // is the honest word for that; zero would be a claim.
        return d.missing_filter_available === false ? null : d.total;
      })
      .catch(function () { return null; });
  }

  function loadSystemView(force) {
    if (systemState.loaded && !force) { return; }
    systemState.loaded = true;
    var tiles = $("sys-tiles");
    if (tiles) { tiles.innerHTML = "<p class=\"muted\">Loading…</p>"; }

    Promise.all([
      fetch("/api/health").then(function (r) { return r.json(); }),
      fetch("/api/evaluation-status").then(function (r) { return r.ok ? r.json() : null; })
        .catch(function () { return null; }),
      missingFileCount(),
      fetch("/api/documents?limit=1").then(function (r) { return r.ok ? r.json() : null; })
        .catch(function () { return null; }),
      fetch("/api/tickers").then(function (r) { return r.ok ? r.json() : null; })
        .catch(function () { return null; })
    ]).then(function (results) {
      var health = results[0] || {};
      var evaluation = results[1] || {};
      var missing = results[2];
      var library = results[3] || {};

      var indexed = library.total !== undefined && library.total !== null
        ? Number(library.total).toLocaleString() : "unknown";
      // The evaluation endpoint reports a level and a headline, not a status
      // word. Translate here rather than inventing a field on the server.
      var evalStatus = !evaluation.available ? "not run"
        : ({ ok: "passed", check: "needs review", stop: "failed" }[evaluation.level] ||
           String(evaluation.level || "unknown"));
      var evalLine = evaluation.available
        ? (evaluation.pass_count || 0) + " pass · " + (evaluation.review_count || 0) +
          " review · " + (evaluation.fail_count || 0) + " fail"
        : (evaluation.note || "");

      if (tiles) {
        tiles.innerHTML =
          tile("Documents indexed", indexed, "every ticker") +
          tile("MSFT chunks", Number(health.msft_chunks || 0).toLocaleString(),
               Number(health.msft_documents || 0).toLocaleString() + " documents") +
          // "unknown" is not "all present". A database without the Phase 9
          // columns cannot answer this, and saying so is the honest tile.
          tile("Missing source files", missing === null ? "unknown" : String(missing),
               missing === null ? "not recorded in this index"
                 : (missing ? "reported, never deleted" : "all present"),
               missing === null ? "tone-plain" : (missing ? "tone-warn" : "tone-ok")) +
          tile("Last ingestion", String(health.last_ingestion_status || "never run"),
               health.last_ingestion_completed_at
                 ? String(health.last_ingestion_completed_at).slice(0, 16) : "",
               statusTone(health.last_ingestion_status, "tone")) +
          tile("Last evaluation", String(evalStatus), evalLine,
               statusTone(evalStatus, "tone")) +
          tile("Index behind archive",
               String((health.stale_rag_count || 0) + (health.pending_chunks || 0)),
               (health.stale_rag_count || 0) + " stale · " + (health.pending_chunks || 0) +
               " chunk(s) pending",
               (health.stale_rag_count || health.pending_chunks) ? "tone-warn" : "tone-ok") +
          tile("Tickers indexed",
               String((results[4] && results[4].indexed ? results[4].indexed.length : 0)) +
               " of " + String((results[4] && results[4].tickers ? results[4].tickers.length : 0)),
               (results[4] && results[4].indexed ? results[4].indexed.join(", ") : ""),
               (results[4] && results[4].indexed && results[4].indexed.length)
                 ? "tone-ok" : "tone-warn") +
          tile("Ask Brain", String(health.pipeline_version || "unknown"), "") +
          tile("UI", String(health.ui_version || "unknown"), "");
      }

      var ingestion = $("sys-ingestion");
      if (ingestion) {
        ingestion.innerHTML = health.last_ingestion_run
          ? [["Run", "#" + health.last_ingestion_run],
             ["Status", health.last_ingestion_status || ""],
             ["Finished", String(health.last_ingestion_completed_at || "").slice(0, 19)],
             ["New files", health.new_files_last_run || 0],
             ["Changed files", health.changed_files_last_run || 0],
             ["Missing last run", (health.missing_files_last_run || 0) + " (not deleted)"],
             ["Smoke eval", health.smoke_eval_status || "not run"]]
            .map(function (row) {
              return "<div class=\"health-row\"><span>" + escapeHtml(row[0]) + "</span><span>" +
                escapeHtml(String(row[1])) + "</span></div>";
            }).join("")
          : "<p class=\"muted\">No ingestion run has been recorded yet.</p>";
      }

      var evalBox = $("sys-eval");
      if (evalBox) {
        evalBox.innerHTML = "<div class=\"health-row\"><span>Status</span><span>" +
          "<span class=\"chip " + statusTone(evalStatus) + "\">" + escapeHtml(evalStatus) +
          "</span></span></div>" +
          (evalLine ? "<div class=\"health-row\"><span>Result</span><span>" +
            escapeHtml(evalLine) + "</span></div>" : "") +
          (evaluation.completed_at
            ? "<div class=\"health-row\"><span>Run at</span><span>" +
              escapeHtml(String(evaluation.completed_at).slice(0, 19)) + "</span></div>" : "") +
          (evaluation.headline
            ? "<p class=\"hint\">" + escapeHtml(evaluation.headline) + "</p>" : "") +
          "<p class=\"hint\">A passing evaluation is what the MSFT index is trusted on. No " +
          "other ticker has been through it.</p>";
      }
    }).catch(function (error) {
      console.error("system view failed", error);
      if (tiles) {
        tiles.innerHTML = "<p class=\"muted\">Could not load system status: " +
          escapeHtml(error.message) + "</p>";
      }
    });
  }

  function loadAppStatus() {
    var chip = $("app-status");
    if (!chip) { return; }
    Promise.all([
      fetch("/api/health").then(function (r) { return r.json(); }),
      missingFileCount()
    ]).then(function (results) {
      var health = results[0] || {};
      var missing = results[1];
      var problems = [];
      if (health.status && health.status !== "ok") { problems.push(String(health.status)); }
      if ((health.warnings || []).length) { problems.push(health.warnings.length + " warning(s)"); }
      if (health.stale_rag_count || health.pending_chunks) { problems.push("index behind"); }
      if (missing) { problems.push(missing + " missing file(s)"); }

      var label, tone;
      if (!problems.length) {
        label = "Healthy"; tone = "chip-ok";
      } else if (missing && problems.length === 1) {
        label = "Missing files"; tone = "chip-warn";
      } else {
        label = "Needs review"; tone = "chip-warn";
      }
      chip.className = "chip " + tone;
      chip.textContent = label;
      chip.title = problems.length ? problems.join(" · ") : "No warnings reported";
    }).catch(function () {
      chip.className = "chip chip-stop";
      chip.textContent = "Server unreachable";
    });
  }

  /* ======================================================================
     Phase 10A - the document library

     Everything here is read-only. No request this file makes carries a path:
     a document is addressed by its id, and the server builds every path from
     the project's own layout. There is no PDF preview in this phase, and the
     source archive is never opened - the most the UI offers is a path to copy.
     ====================================================================== */

  var docState = { limit: 50, offset: 0, total: 0, current: null, pane: "overview",
    preview: null, sourcePath: "" };

  var VIEWS = ["ask", "history", "companies", "themes", "documents", "evaluations", "system"];

  // Phase 13: one title per view, so the topbar says where you are instead of
  // repeating the product name on every page.
  var VIEW_TITLES = {
    ask: ["New Research", "Ask anything across Cutler's indexed research."],
    history: ["History", "Every question asked, and what it was answered from."],
    companies: ["Companies", "The ten companies whose research is indexed."],
    themes: ["Themes", "How the archive groups by subject."],
    documents: ["Documents", "Browse the archive and read any document."],
    evaluations: ["Evaluations", "How the system scored on the golden questions."],
    system: ["System", "Index health, ingestion and pipeline status."]
  };

  function switchView(name, focusId) {
    if (VIEWS.indexOf(name) < 0) { name = "ask"; }
    Array.prototype.forEach.call(document.querySelectorAll("[data-view]"), function (item) {
      item.classList.toggle("active", item.getAttribute("data-view") === name &&
        !item.hasAttribute("data-focus"));
    });
    VIEWS.forEach(function (view) {
      var node = $("view-" + view);
      if (node) { node.hidden = view !== name; }
    });
    var title = VIEW_TITLES[name];
    if (title && $("view-title")) {
      $("view-title").textContent = title[0];
      $("view-sub").textContent = title[1];
    }
    if (name === "documents" && !$("doc-ticker").dataset.loaded) {
      loadDocumentFilters();
    }
    if (name === "system") { loadSystemView(); }
    if (name === "history") { loadHistory(); }
    if (name === "companies") { loadCompanies(); }
    if (name === "themes") { loadThemes(); }
    if (name === "evaluations") { loadEvaluationDashboard(); }
    if (focusId && $(focusId)) {
      $(focusId).scrollIntoView({ behavior: "smooth", block: "start" });
      $(focusId).classList.add("card-focused");
      window.setTimeout(function () { $(focusId).classList.remove("card-focused"); }, 1600);
    }
  }

  function loadDocumentFilters() {
    fetch("/api/document-filters").then(function (r) { return r.json(); })
      .then(function (data) {
        fillSelect("doc-ticker", data.tickers, "Any ticker");
        fillSelect("doc-source-type", data.source_types, "Any source type");
        fillSelect("doc-document-type", data.document_types, "Any document type");
        $("doc-ticker").dataset.loaded = "1";
        if (!docState.total) { loadDocuments(0); }
      }).catch(function () { /* the list still works without the dropdowns */ });
  }

  function documentQuery() {
    var params = new URLSearchParams();
    var pairs = [
      ["search", $("doc-search").value], ["ticker", $("doc-ticker").value],
      ["source_type", $("doc-source-type").value],
      ["document_type", $("doc-document-type").value],
      ["rag_ready", $("doc-rag").value],
      ["date_from", $("doc-date-from").value], ["date_to", $("doc-date-to").value]
    ];
    pairs.forEach(function (pair) { if (pair[1]) { params.set(pair[0], pair[1]); } });
    if ($("doc-missing").checked) { params.set("missing", "true"); }
    params.set("limit", docState.limit);
    params.set("offset", docState.offset);
    return params.toString();
  }

  function loadDocuments(offset) {
    docState.offset = Math.max(0, offset || 0);
    show($("doc-error"), false);
    $("doc-rows").innerHTML = "<p class=\"muted empty-note\">Loading…</p>";

    fetch("/api/documents?" + documentQuery())
      .then(function (response) {
        if (!response.ok) { throw new Error("The document list could not be loaded."); }
        return response.json();
      })
      .then(function (data) {
        docState.total = data.total;
        $("doc-count").textContent = data.total.toLocaleString() + " document(s)";
        if (!data.documents.length) {
          $("doc-rows").innerHTML = "<p class=\"muted empty-note\">Nothing matches those " +
            "filters. Clear one and load again.</p>";
          show($("doc-pager"), false);
          return;
        }
        $("doc-rows").innerHTML = data.documents.map(documentRow).join("");
        // The whole row is the button, so clicking anywhere in it opens the
        // document. There is no separate target to hunt for.
        Array.prototype.forEach.call($("doc-rows").querySelectorAll("button[data-document]"),
          function (button) {
            button.addEventListener("click", function () {
              openDocument(button.getAttribute("data-document"));
            });
          });
        markSelectedRow();

        var from = data.offset + 1;
        var to = data.offset + data.documents.length;
        $("doc-page-label").textContent = from.toLocaleString() + "–" + to.toLocaleString() +
          " of " + data.total.toLocaleString();
        $("doc-prev").disabled = data.offset <= 0;
        $("doc-next").disabled = to >= data.total;
        show($("doc-pager"), true);
        $("doc-rows").scrollTop = 0;
      })
      .catch(function (error) {
        console.error("document list failed", error);
        $("doc-rows").innerHTML = "<p class=\"muted empty-note\">" +
          escapeHtml(error.message) + "</p>";
      });
  }

  // Phase 10C.1. A library entry, not a spreadsheet row: the file name leads,
  // everything else is a chip or a quiet line under it.
  function documentRow(doc) {
    var rag = Number(doc.usable_for_rag) === 1;
    var missing = doc.marked_missing || !doc.source_path_available;
    var extraction = String(doc.pdf_extraction_status || "");
    var extractionTone = /^extract/.test(extraction) ? "chip-ok"
      : (/(fail|error)/.test(extraction) ? "chip-stop"
        : (extraction ? "chip-warn" : "chip-plain"));
    var facts = [doc.source_type_guess, doc.document_type_guess]
      .filter(function (value) { return !!value; })
      .map(function (value) { return escapeHtml(value); }).join(" · ");

    return "<button type=\"button\" class=\"doc-item\" data-document=\"" +
      escapeHtml(doc.document_id) + "\" title=\"" + escapeHtml(doc.relative_path || "") + "\">" +
      "<span class=\"doc-item-top\">" +
      "<span class=\"doc-item-name\">" + escapeHtml(doc.file_name || "(no name)") + "</span>" +
      "<span class=\"doc-item-date\">" + escapeHtml(doc.document_date || "no date") + "</span>" +
      "</span>" +
      (facts ? "<span class=\"doc-item-facts\">" + facts + "</span>" : "") +
      "<span class=\"doc-item-chips\">" +
      (doc.ticker_candidates_clean
        ? "<span class=\"chip chip-info chip-plain\">" +
          escapeHtml(doc.ticker_candidates_clean) + "</span>" : "") +
      (extraction ? "<span class=\"chip " + extractionTone + "\">" +
        escapeHtml(extraction) + "</span>" : "") +
      "<span class=\"chip " + (rag ? "chip-ok" : "") + "\">" +
      (rag ? "indexed" : "not indexed") + "</span>" +
      "<span class=\"chip " + (missing ? "chip-stop" : "chip-plain") + "\">" +
      (missing ? "source missing" : "source present") + "</span>" +
      "</span>" +
      "</button>";
  }

  // The selected document must stay obvious while the list scrolls and while
  // the reader is open beside it.
  function markSelectedRow() {
    var found = false;
    Array.prototype.forEach.call($("doc-rows").querySelectorAll(".doc-item"),
      function (item) {
        var active = item.getAttribute("data-document") === docState.current;
        item.classList.toggle("active", active);
        if (active) {
          found = true;
          item.setAttribute("aria-current", "true");
          if (item.scrollIntoView) {
            item.scrollIntoView({ block: "nearest" });
          }
        } else {
          item.removeAttribute("aria-current");
        }
      });
    // Opening from a citation can land on a document the current filters do not
    // show. The reader is still right; the list simply does not contain it, and
    // the note says so instead of leaving the two looking unrelated.
    var note = $("doc-list-note");
    if (note) {
      if (docState.current && !found) {
        note.textContent = "The open document is not in the current list.";
        show(note, true);
      } else {
        show(note, false);
      }
    }
    return found;
  }

  function openDocument(documentId) {
    if (!documentId) { return; }
    switchView("documents");
    docState.current = documentId;
    docState.pane = "overview";

    fetch("/api/document/" + encodeURIComponent(documentId))
      .then(function (response) {
        if (!response.ok) {
          return readError(response).then(function (m) { throw new Error(m); });
        }
        return response.json();
      })
      .then(function (data) {
        renderDocumentDetail(data);
        show($("doc-empty"), false);
        show($("doc-detail-card"), true);
        // The reader is its own panel now, so there is nothing to scroll the
        // page to - only its own body to return to the top.
        if ($("doc-reader-body")) { $("doc-reader-body").scrollTop = 0; }
        markSelectedRow();
      })
      .catch(function (error) {
        $("doc-error").textContent = error.message;
        show($("doc-error"), true);
        show($("doc-detail-card"), false);
        show($("doc-empty"), true);
      });
  }

  function renderDocumentDetail(data) {
    var doc = data.document || {};
    var source = data.source || {};
    $("doc-detail-title").textContent = doc.file_name || doc.document_id;

    // Status chips: one row, one shape, strongest signal first. A reader should
    // be able to tell in a glance whether this document is retrievable, whether
    // its original still exists, and whether it can be opened here.
    var chips = [];
    if (source.exists) {
      chips.push("<span class=\"chip chip-ok\">source present</span>");
    } else {
      chips.push("<span class=\"chip chip-stop\">source missing</span>");
    }
    if (Number(doc.usable_for_rag) === 1) {
      chips.push("<span class=\"chip chip-ok\">indexed for Ask Brain</span>");
    } else {
      chips.push("<span class=\"chip\">not indexed</span>");
    }
    var preview = data.preview || {};
    if (preview.enabled) {
      chips.push("<span class=\"chip chip-info\">PDF preview available</span>");
    } else if (preview.reason === "preview_only_available_for_pdf") {
      chips.push("<span class=\"chip chip-warn\">preview: PDF only</span>");
    } else if (preview.reason) {
      chips.push("<span class=\"chip chip-stop\">no preview</span>");
    }
    if (data.chunks && data.chunks.total) {
      chips.push("<span class=\"chip chip-plain\">" + escapeHtml(data.chunks.total) +
        " chunk(s)</span>");
    }
    if (String(doc.review_severity || "") === "High") {
      chips.push("<span class=\"chip chip-stop\">review: High</span>");
    }
    $("doc-detail-badges").innerHTML = chips.join("");

    if (source.warning) {
      $("doc-detail-warning").innerHTML = "<strong>Source file not found.</strong> " +
        escapeHtml(source.warning);
      show($("doc-detail-warning"), true);
    } else {
      show($("doc-detail-warning"), false);
    }

    $("doc-source-path").textContent = source.source_path || "(no path recorded)";
    docState.sourcePath = source.source_path || "";

    var rows = [
      ["Document id", escapeHtml(doc.document_id)],
      ["Date", escapeHtml(doc.document_date || "unknown")],
      ["Relative path", escapeHtml(doc.relative_path || "")],
      ["Ticker(s)", escapeHtml(doc.ticker_candidates_clean || "none detected")],
      ["Source type", escapeHtml(doc.source_type_guess || "")],
      ["Document type", escapeHtml(doc.document_type_guess || "")],
      ["Review", escapeHtml((doc.review_severity || "none") + " · " +
        (doc.review_reason || "no reason recorded"))],
      ["Extraction", escapeHtml(doc.pdf_extraction_status || "not extracted") + " · " +
        escapeHtml(doc.pdf_extraction_quality || "no quality")],
      ["Document card", escapeHtml(doc.document_card_status || "none")],
      ["RAG chunks", escapeHtml(String((data.chunks || {}).total || 0)) + " total, " +
        escapeHtml(String((data.chunks || {}).embedded || 0)) + " retrievable"],
      ["Last seen", escapeHtml(doc.last_seen_at || "not recorded")],
      ["Change status", escapeHtml(doc.file_change_status || "not recorded")]
    ];
    if (doc.rag_skip_reason) {
      rows.push(["Why RAG skipped it", escapeHtml(doc.rag_skip_reason)]);
    }
    $("pane-overview").innerHTML = rows.map(function (row) {
      return "<div class=\"health-row\"><span>" + row[0] + "</span><span>" + row[1] +
        "</span></div>";
    }).join("") +
      "<p class=\"hint\">Every value above is a recorded guess from the pipeline, not a " +
      "verified fact. " + escapeHtml(data.note || "") + "</p>";

    // The text pane owns static markup - its search box, its note line and its
    // body - so it is reset by emptying those, never by clearing the pane. An
    // innerHTML wipe here deletes #doc-text-body along with the listeners bound
    // to the search controls at startup, and the pane then renders as blank
    // with a TypeError in the console. That was the Phase 10A.1 bug.
    ["card", "pages", "chunks", "preview"].forEach(function (pane) {
      var node = $("pane-" + pane);
      node.innerHTML = "";
      delete node.dataset.loadedFor;
    });
    resetTextPane();

    // Phase 10B. The verdict travels with the detail payload, so the pane can
    // be labelled before anyone clicks it - and so the browser never has to
    // guess whether a file is previewable.
    docState.preview = data.preview || null;
    var previewTab = document.querySelector('.subtab[data-pane="preview"]');
    if (previewTab) {
      var enabled = !!(data.preview && data.preview.enabled);
      previewTab.classList.toggle("subtab-muted", !enabled);
      previewTab.title = data.preview ? data.preview.message : "";
    }

    // A citation click lands here too. It opens on Overview on purpose: the
    // reader should see what the document is before its pages appear.
    selectPane("overview");
  }

  function selectPane(pane) {
    docState.pane = pane;
    Array.prototype.forEach.call(document.querySelectorAll(".subtab"), function (tab) {
      tab.classList.toggle("active", tab.getAttribute("data-pane") === pane);
    });
    ["overview", "card", "text", "pages", "chunks", "preview"].forEach(function (name) {
      $("pane-" + name).hidden = name !== pane;
    });
    if (pane === "card") { loadCard(); }
    if (pane === "text") {
      // Re-entering the tab must not throw away a search the user just ran.
      var textBody = $("doc-text-body");
      if (!textBody || textBody.dataset.loadedFor !== docState.current) { loadText(); }
    }
    if (pane === "pages") { loadPages(); }
    if (pane === "preview") { loadPreview(); }
    if (pane === "chunks") { loadChunks(); }
  }

  function docFetch(suffix) {
    return fetch("/api/document/" + encodeURIComponent(docState.current) + suffix)
      .then(function (response) {
        if (!response.ok) {
          return readError(response).then(function (m) { throw new Error(m); });
        }
        return response.json();
      });
  }

  function loadCard() {
    var pane = $("pane-card");
    if (pane.dataset.loadedFor === docState.current) { return; }
    pane.innerHTML = "<p class=\"muted\">Loading…</p>";
    docFetch("/card").then(function (data) {
      pane.dataset.loadedFor = docState.current;
      pane.innerHTML = "<p class=\"hint\"><code>" + escapeHtml(data.path) + "</code></p>" +
        "<article class=\"markdown\">" + renderMarkdown(data.markdown) + "</article>" +
        "<p class=\"hint\">" + escapeHtml(data.note || "") + "</p>";
    }).catch(function (error) {
      pane.innerHTML = "<p class=\"muted\">" + escapeHtml(error.message) + "</p>";
    });
  }

  function resetTextPane() {
    var search = $("doc-text-search");
    if (search) { search.value = ""; }
    var note = $("doc-text-note");
    if (note) { note.textContent = ""; }
    var body = $("doc-text-body");
    if (body) {
      body.innerHTML = "";
      delete body.dataset.loadedFor;
    }
  }

  // The server may name the text field differently as this grows. Read all the
  // names it could reasonably use rather than rendering an empty box because
  // one of them changed.
  function textOf(data) {
    var value = data.text || data.content || data.preview || data.extracted_text || "";
    return typeof value === "string" ? value : "";
  }

  function countLabel(data) {
    var returned = Number(data.chars_returned || textOf(data).length || 0);
    var total = Number(data.total_chars || 0);
    var label = returned.toLocaleString() + " character(s)";
    if (total && total !== returned) { label += " of " + total.toLocaleString(); }
    return label + (data.truncated ? " · truncated preview" : " · complete");
  }

  function renderSnippets(snippets) {
    return snippets.map(function (snippet) {
      var extra = Number(snippet.matches_in_window || 1) > 1
        ? " · " + snippet.matches_in_window + " matches in this passage" : "";
      var heading = snippet.page
        ? "<p class=\"snippet-page\">Page " + escapeHtml(snippet.page) + extra + "</p>"
        : (extra ? "<p class=\"snippet-page\">" + escapeHtml(extra.slice(3)) + "</p>" : "");
      // Prefer the split form so the match can be marked; fall back to the
      // whole snippet when the server only sent that.
      var inner = (typeof snippet.match === "string" && snippet.match !== "")
        ? escapeHtml(snippet.before || "") + "<mark>" + escapeHtml(snippet.match) + "</mark>" +
          escapeHtml(snippet.after || "")
        : escapeHtml(snippet.text || "");
      return "<div class=\"snippet-item\">" + heading +
        "<pre class=\"snippet\">" + inner + "</pre></div>";
    }).join("");
  }

  function loadText(query) {
    var body = $("doc-text-body");
    var note = $("doc-text-note");
    if (!body) { return; }   // pane markup missing: nothing to render into
    body.innerHTML = "<p class=\"muted\">Loading…</p>";
    if (note) { note.textContent = ""; }

    var suffix = "/text" + (query ? "?search=" + encodeURIComponent(query) : "");
    docFetch(suffix).then(function (data) {
      body.dataset.loadedFor = docState.current;

      if (data.mode === "search") {
        var snippets = data.snippets || [];
        var total = Number(data.match_count != null ? data.match_count : data.matches || 0);
        if (note) {
          note.textContent = (data.message ||
            (total + " match(es) for \"" + (data.query || query) + "\"")) +
            " · snippets only, not the whole document" +
            (data.searched === "pages" ? " · searched page by page" : "");
        }
        body.innerHTML = snippets.length
          ? renderSnippets(snippets)
          : "<p class=\"muted\">No match for \"" + escapeHtml(data.query || query) +
            "\" in the extracted text of this document. The text is there - the term is not. " +
            "Clear the search to read the document.</p>";
        return;
      }

      var text = textOf(data);
      if (note) {
        note.textContent = (data.mode === "page"
          ? "Page " + data.page + " of " + (data.page_count || "?") + " · "
          : "") + countLabel(data);
      }
      body.innerHTML = text
        ? "<pre class=\"extracted\">" + escapeHtml(text) + "</pre>"
        : "<p class=\"muted\">This document has an extracted-text file, but it is empty. " +
          "That usually means the PDF held images rather than text. OCR is not part of this " +
          "phase, so there is nothing to show.</p>";
    }).catch(function (error) {
      console.error("extracted text failed", error);
      if (note) { note.textContent = ""; }
      body.innerHTML = "<p class=\"muted\">Could not load the extracted text: " +
        escapeHtml(error.message) + "</p>";
    });
  }

  function loadPage(pageNumber, target, noteTarget) {
    var body = target || $("doc-text-body");
    if (!body) { return; }
    body.innerHTML = "<p class=\"muted\">Loading page " + escapeHtml(pageNumber) + "…</p>";
    docFetch("/text?page=" + encodeURIComponent(pageNumber)).then(function (data) {
      var label = "Page " + data.page + " of " + (data.page_count || "?") + " · " +
        countLabel(data);
      if (noteTarget) { noteTarget.textContent = label; }
      else if ($("doc-text-note")) { $("doc-text-note").textContent = label; }
      var text = textOf(data);
      body.innerHTML = text
        ? "<pre class=\"extracted\">" + escapeHtml(text) + "</pre>"
        : "<p class=\"muted\">Page " + escapeHtml(data.page) +
          " has no extracted text. The page was most likely an image.</p>";
    }).catch(function (error) {
      console.error("page text failed", error);
      body.innerHTML = "<p class=\"muted\">Could not load page " + escapeHtml(pageNumber) +
        ": " + escapeHtml(error.message) + "</p>";
    });
  }

  function loadPages() {
    var pane = $("pane-pages");
    if (pane.dataset.loadedFor === docState.current) { return; }
    pane.innerHTML = "<p class=\"muted\">Loading…</p>";
    docFetch("/pages").then(function (data) {
      pane.dataset.loadedFor = docState.current;
      if (!data.pages || !data.pages.length) {
        pane.innerHTML = "<p class=\"muted\">" + escapeHtml(data.note || "No pages.") + "</p>";
        return;
      }
      pane.innerHTML = "<p class=\"hint\">" + data.page_count + " page(s) of extracted text. " +
        "Click one to read it below. This is the page-level view until a real PDF preview " +
        "exists.</p>" +
        "<div class=\"page-grid\">" + data.pages.map(function (page) {
          return "<button class=\"page-chip\" data-page=\"" + escapeHtml(page.page) + "\">" +
            "<span class=\"page-number\">p." + escapeHtml(page.page) + "</span>" +
            "<span class=\"page-chars\">" + Number(page.char_count).toLocaleString() +
            " chars</span></button>";
        }).join("") + "</div>" +
        "<p id=\"doc-page-note\" class=\"hint\"></p>" +
        "<div id=\"doc-page-body\"><p class=\"muted\">Pick a page above to read its extracted " +
        "text here.</p></div>";

      var pageBody = pane.querySelector("#doc-page-body");
      var pageNote = pane.querySelector("#doc-page-note");
      Array.prototype.forEach.call(pane.querySelectorAll(".page-chip"), function (chip) {
        chip.addEventListener("click", function () {
          Array.prototype.forEach.call(pane.querySelectorAll(".page-chip"), function (other) {
            other.classList.toggle("active", other === chip);
          });
          loadPage(chip.getAttribute("data-page"), pageBody, pageNote);
        });
      });
    }).catch(function (error) {
      console.error("page list failed", error);
      pane.innerHTML = "<p class=\"muted\">Could not load the page list: " +
        escapeHtml(error.message) + "</p>";
    });
  }

  // --- Phase 10B: the original file, viewed in place ------------------------
  //
  // The frame's src is built from the document id and nothing else. There is no
  // path anywhere in this function, and no file:// link is ever produced: a
  // file:// href would not open from a page anyway, and offering one teaches
  // the wrong idea about what this tool does.
  function loadPreview() {
    var pane = $("pane-preview");
    if (pane.dataset.loadedFor === docState.current) { return; }
    var preview = docState.preview || {};
    pane.dataset.loadedFor = docState.current;

    // Phase 10C.1. In a reader panel the document should dominate. The path
    // already sits above the tabs, so this footer is one line: what the viewer
    // is, and how to open the original yourself.
    var footer =
      "<p class=\"preview-foot\">" +
      "<button class=\"link-button\" id=\"doc-preview-copy\">Copy V-drive path</button>" +
      "<span class=\"hint\">Copy the path and paste it into File Explorer if the browser " +
      "preview is not enough — for printing, annotating, or a page the viewer renders " +
      "poorly.</span></p>";

    if (preview.enabled && preview.url) {
      pane.innerHTML =
        "<p class=\"notice notice-info notice-slim\">Read-only preview of the original indexed " +
        "PDF. The source file stays on the V drive and is not copied, changed or moved.</p>" +
        "<div class=\"preview-frame\">" +
        "<iframe id=\"doc-preview-frame\" title=\"Original PDF preview\" " +
        "src=\"" + escapeHtml(preview.url) + "\"></iframe></div>" +
        footer;
    } else {
      var tone = preview.reason === "preview_only_available_for_pdf"
        ? "notice-warn" : "notice-stop";
      // Short headings. The sentence underneath carries the detail, so a
      // heading that repeats it just makes the box say everything twice.
      var heading = {
        source_file_missing: "Original file not found",
        preview_only_available_for_pdf: "Not a PDF" +
          (preview.extension ? " (" + preview.extension + ")" : ""),
        source_path_outside_allowed_archive: "Outside the allowed archive",
        no_source_path_recorded: "No source path recorded"
      }[preview.reason] || "No preview";

      pane.innerHTML =
        "<div class=\"notice " + tone + "\"><strong>" + escapeHtml(heading) + "</strong><br>" +
        escapeHtml(preview.message || "") + "</div>" +
        "<p class=\"hint\">Everything else about this document is still here: the metadata, " +
        "the document card, the extracted text, the pages and the chunks. Nothing is hidden " +
        "because a file could not be opened.</p>" +
        footer;
    }

    var copy = $("doc-preview-copy");
    if (copy) { copy.addEventListener("click", copySourcePath); }
  }

  function loadChunks() {
    var pane = $("pane-chunks");
    if (pane.dataset.loadedFor === docState.current) { return; }
    pane.innerHTML = "<p class=\"muted\">Loading…</p>";
    docFetch("/chunks").then(function (data) {
      pane.dataset.loadedFor = docState.current;
      if (!data.chunks.length) {
        pane.innerHTML = "<p class=\"muted\">" +
          escapeHtml(data.note || "No chunks for this document.") + "</p>";
        return;
      }
      pane.innerHTML = "<p class=\"hint\">" + data.shown + " of " + data.total +
        " chunk(s). This is what Ask Brain can actually retrieve from this document. " +
        escapeHtml(data.note || "") + "</p>" +
        data.chunks.map(function (chunk) {
          var pages = (chunk.page_start_guess && chunk.page_end_guess)
            ? (chunk.page_start_guess === chunk.page_end_guess
              ? "p." + chunk.page_start_guess
              : "pp. " + chunk.page_start_guess + "-" + chunk.page_end_guess)
            : "no page info";
          return "<div class=\"chunk" + (chunk.is_superseded ? " chunk-superseded" : "") + "\">" +
            "<div class=\"chunk-top\">" +
            "<code>" + escapeHtml(chunk.chunk_id) + "</code>" +
            "<span class=\"badge" + (chunk.retrievable ? " badge-high" : " badge-low") + "\">" +
            escapeHtml(chunk.embedding_status || "unknown") + "</span>" +
            "</div>" +
            "<div class=\"chunk-meta\">" + escapeHtml(pages) + " · " +
            escapeHtml(chunk.chunk_token_count || 0) + " tokens" +
            (chunk.chunk_version ? " · version " + escapeHtml(chunk.chunk_version) : "") +
            "</div>" +
            "<pre class=\"chunk-text\">" + escapeHtml(chunk.text_preview) +
            (chunk.preview_truncated ? "\n…" : "") + "</pre></div>";
        }).join("");
    }).catch(function (error) {
      pane.innerHTML = "<p class=\"muted\">" + escapeHtml(error.message) + "</p>";
    });
  }

  function copySourcePath(event) {
    if (!docState.current) { return; }
    // Either copy button may call this - the one above the tabs, or the one in
    // the Original preview pane.
    var trigger = event && event.currentTarget ? event.currentTarget : $("doc-copy-path");
    docFetch("/source-path").then(function (data) {
      var path = data.source_path || "";
      if (!path) { return; }
      var button = trigger || $("doc-copy-path");
      var restore = function () {
        window.setTimeout(function () { button.textContent = "Copy V-drive path"; }, 1600);
      };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(path).then(function () {
          button.textContent = "Copied";
          restore();
        }).catch(function () { button.textContent = "Copy failed - select the path above"; restore(); });
      } else {
        button.textContent = "Select the path above and copy it";
        restore();
      }
    });
  }

  /* ---- wire up --------------------------------------------------------- */

  ["retrieval_mode", "source_priority", "top_k", "max_chunks", "max_chunks_per_document",
   "ticker"].forEach(function (id) {
    if ($(id)) { $(id).addEventListener("change", describeAdvanced); }
  });
  describeAdvanced();

  $("ask").addEventListener("click", ask);
  $("refresh-runs").addEventListener("click", loadRuns);
  $("refresh-review").addEventListener("click", loadReview);
  $("refresh-evaluation").addEventListener("click", loadEvaluation);
  $("question").addEventListener("keydown", function (event) {
    if ((event.ctrlKey || event.metaKey) && event.key === "Enter") { ask(); }
  });
  Array.prototype.forEach.call(document.querySelectorAll("button.preset"), function (button) {
    button.addEventListener("click", function () {
      applyPreset(button.getAttribute("data-preset"));
    });
  });
  ["source_type", "document_type", "date_from", "date_to", "must_contain"].forEach(function (id) {
    var element = $(id);
    if (element) { element.addEventListener("change", describeFilters); }
    if (element) { element.addEventListener("input", describeFilters); }
  });
  $("clear-filters").addEventListener("click", function () {
    ["source_type", "document_type", "date_from", "date_to", "must_contain"].forEach(
      function (id) { $(id).value = ""; });
    describeFilters();
  });

  Array.prototype.forEach.call(document.querySelectorAll(".nav-item[data-view]"),
    function (item) {
      item.addEventListener("click", function () {
        if (item.disabled) { return; }
        switchView(item.getAttribute("data-view"), item.getAttribute("data-focus"));
      });
    });
  if ($("sys-refresh")) {
    $("sys-refresh").addEventListener("click", function () { loadSystemView(true); });
  }
  // The header chip is the one thing on screen at all times, so it is the one
  // thing that should never be stale for long.
  loadAppStatus();
  loadTickers();
  if ($("ticker")) { $("ticker").addEventListener("change", describeTicker); }
  Array.prototype.forEach.call(document.querySelectorAll(".subtab"), function (tab) {
    tab.addEventListener("click", function () { selectPane(tab.getAttribute("data-pane")); });
  });
  $("doc-load").addEventListener("click", function () { loadDocuments(0); });
  $("doc-search").addEventListener("keydown", function (event) {
    if (event.key === "Enter") { loadDocuments(0); }
  });
  $("doc-clear").addEventListener("click", function () {
    ["doc-search", "doc-ticker", "doc-source-type", "doc-document-type", "doc-rag",
      "doc-date-from", "doc-date-to"].forEach(function (id) { $(id).value = ""; });
    $("doc-missing").checked = false;
    loadDocuments(0);
  });
  $("doc-prev").addEventListener("click", function () {
    loadDocuments(docState.offset - docState.limit);
  });
  $("doc-next").addEventListener("click", function () {
    loadDocuments(docState.offset + docState.limit);
  });
  $("doc-detail-close").addEventListener("click", function () {
    show($("doc-detail-card"), false);
    show($("doc-empty"), true);
    docState.current = null;
    markSelectedRow();
  });
  $("doc-copy-path").addEventListener("click", copySourcePath);
  $("doc-text-go").addEventListener("click", function () {
    loadText($("doc-text-search").value.trim());
  });
  $("doc-text-search").addEventListener("keydown", function (event) {
    if (event.key === "Enter") { loadText($("doc-text-search").value.trim()); }
  });
  $("doc-text-reset").addEventListener("click", function () {
    $("doc-text-search").value = "";
    loadText("");
  });

  loadHealth();
  loadRuns();
  loadReview();
  loadEvaluation();

  /* ======================================================================
     Phase 13 - the research workspace

     Everything below is additive. The ask/answer plumbing above is unchanged:
     this layer decides where things are SHOWN and translates the pipeline's
     vocabulary into the words a portfolio manager uses. No request made here
     changes anything; every endpoint it calls is a GET.
     ====================================================================== */

  var depthChoice = "quick";
  var panelState = { pane: "sources", ticker: "", companies: null };

  function setDepth(value) {
    depthChoice = (value === "deep") ? "deep" : "quick";
    Array.prototype.forEach.call(document.querySelectorAll(".depth-option"), function (button) {
      button.classList.toggle("active", button.getAttribute("data-depth") === depthChoice);
    });
    // The advanced numbers follow the choice, so the drawer never contradicts
    // the button. Someone who then edits them by hand is choosing "custom",
    // which is exactly what the drawer is for.
    var preset = depthChoice === "deep"
      ? { top_k: 60, max_chunks: 16, per: 3 }
      : { top_k: 30, max_chunks: 8, per: 2 };
    if ($("top_k")) { $("top_k").value = preset.top_k; }
    if ($("max_chunks")) { $("max_chunks").value = preset.max_chunks; }
    if ($("max_chunks_per_document")) { $("max_chunks_per_document").value = preset.per; }
    if ($("depth-hint")) {
      $("depth-hint").textContent = depthChoice === "deep"
        ? "Deep Research widens the search to 60 candidates and reads up to 16 passages, from "
          + "more documents. Same index, same ranking - more of it is read."
        : "Quick Answer reads the closest 8 passages. Deep Research widens the search and "
          + "reads more of it.";
    }
    if (typeof describeFilters === "function") { describeFilters(); }
  }

  /* ---- the trust summary ---------------------------------------------- */

  function renderTrust(trust, data) {
    var element = $("trust");
    if (!element) { return; }
    if (!trust) {
      // An older run row, or a failure before retrieval. Say the one thing
      // that is known rather than inventing a summary.
      trust = { level: "check", headline: (data && data.final_confidence)
        ? data.final_confidence + " confidence" : "Confidence not recorded",
        points: [], caution: "" };
    }
    var points = (trust.points || []).map(function (text) {
      return "<li>" + escapeHtml(text) + "</li>";
    }).join("");
    element.className = "trust trust-" + (trust.level || "check");
    element.innerHTML =
      "<div class=\"trust-head\"><span class=\"trust-dot\" aria-hidden=\"true\"></span>" +
      escapeHtml(trust.headline || "") + "</div>" +
      (points ? "<ul class=\"trust-points\">" + points + "</ul>" : "") +
      (trust.caution ? "<p class=\"trust-caution\">" + escapeHtml(trust.caution) + "</p>" : "");
    show(element, true);
  }

  /* ---- follow-up suggestions ------------------------------------------ */

  // Phase 13.1. Two kinds of chip, and the difference matters.
  //
  // The first two are ABOUT THIS ANSWER: they carry data-followup, so the click
  // sends the current run id and the server answers them from the evidence
  // already on screen. "Which documents support this?" costs nothing at all.
  //
  // The rest are the selected company's own questions, read from the Phase 12
  // golden question file. None of them is generated, and none of them mentions a
  // company other than the one in the box - the Phase 13 version hardcoded
  // Microsoft's wording and offered it under every ticker.
  function renderFollowups(data) {
    var wrap = $("followups");
    var holder = $("followup-buttons");
    if (!wrap || !holder) { return; }
    var symbol = String((data && data.ticker) ||
      ($("ticker") ? $("ticker").value : "")).toUpperCase();
    var asked = String((data && data.question) || "");

    var chips = [
      { text: "Which documents support this?", followup: true,
        title: "Answered from the sources already on screen - nothing is spent" },
      { text: "Why?", followup: true,
        title: "Searched inside the documents this answer came from" }
    ];
    (companyState.suggestions[symbol] || []).forEach(function (item) {
      if (chips.length < 5 && item.question && item.question !== asked) {
        chips.push({ text: item.question, followup: false, title: item.label });
      }
    });

    holder.innerHTML = chips.map(function (chip) {
      return "<button type=\"button\" class=\"preset suggest-chip\" data-question=\"" +
        escapeHtml(chip.text) + "\"" + (chip.followup ? " data-followup=\"1\"" : "") +
        " title=\"" + escapeHtml(chip.title || "") + "\">" + escapeHtml(chip.text) +
        "</button>";
    }).join("");
    show(wrap, true);
  }

  function companyName(ticker) {
    var symbol = String(ticker || "").toUpperCase();
    var names = { MSFT: "Microsoft", KIM: "Kimco Realty", KR: "Kroger", MRK: "Merck",
      T: "AT&T", VZ: "Verizon", AMZN: "Amazon", SLB: "SLB", B: "Barnes Group",
      ABBV: "AbbVie" };
    return names[symbol] || symbol || "this company";
  }

  /* ---- the research panel --------------------------------------------- */

  var RPANES = ["sources", "documents", "deepdive", "timeline", "company", "related"];

  function selectRPane(name) {
    if (RPANES.indexOf(name) < 0) { name = "sources"; }
    panelState.pane = name;
    Array.prototype.forEach.call(document.querySelectorAll(".rtab"), function (tab) {
      tab.classList.toggle("active", tab.getAttribute("data-rpane") === name);
    });
    RPANES.forEach(function (pane) {
      var node = $("rpane-" + pane);
      if (node) { node.hidden = pane !== name; }
    });
    if (name === "company") { renderPanelCompany(); }
    if (name === "related") { renderPanelRelated(); }
  }

  function renderPanelExtras(summary, runData) {
    panelState.ticker = (runData && runData.ticker) ||
      ($("ticker") ? $("ticker").value : "");
    renderPanelTimeline(summary);
    if (panelState.pane === "company") { renderPanelCompany(); }
    if (panelState.pane === "related") { renderPanelRelated(); }
  }

  function renderPanelTimeline(summary) {
    var element = $("rtimeline");
    if (!element) { return; }
    var evidence = (summary && summary.evidence) || [];
    var dated = evidence.filter(function (item) { return item.folder_date_guess; })
      .sort(function (a, b) {
        return String(a.folder_date_guess).localeCompare(String(b.folder_date_guess));
      });
    if (!dated.length) {
      element.innerHTML = "<p class=\"muted\">None of the sources behind this answer carries a " +
        "date.</p>";
      return;
    }
    element.innerHTML = "<ul class=\"history\">" + dated.map(function (item) {
      return "<li data-document=\"" + escapeHtml(item.document_id || "") + "\">" +
        "<span class=\"history-tag\">" + escapeHtml(item.folder_date_guess) + "</span>" +
        "<span class=\"history-q\">" + escapeHtml(item.file_name || "document") + "</span>" +
        "<span class=\"history-meta\">" + escapeHtml(item.source_type_guess || "") +
        "</span></li>";
    }).join("") + "</ul>";
  }

  function renderPanelCompany() {
    var element = $("rcompany");
    if (!element) { return; }
    var symbol = String(panelState.ticker || ($("ticker") ? $("ticker").value : "")).toUpperCase();
    if (!symbol) {
      element.innerHTML = "<p class=\"muted\">Ask a question first.</p>";
      return;
    }
    fetchCompanies().then(function (list) {
      var match = list.filter(function (row) { return row.ticker === symbol; })[0];
      if (!match) {
        element.innerHTML = "<p class=\"muted\">Nothing recorded for " + escapeHtml(symbol) +
          ".</p>";
        return;
      }
      element.innerHTML =
        "<div class=\"company-tile\"><b>" + escapeHtml(match.company) + "</b>" +
        "<span class=\"company-ticker\">" + escapeHtml(match.ticker) + "</span>" +
        "<div class=\"company-stats\">" +
        escapeHtml(String(match.documents)) + " documents indexed<br>" +
        escapeHtml(String(match.questions_asked)) + " questions asked here<br>" +
        (match.newest_document ? "newest document " + escapeHtml(match.newest_document) : "") +
        "</div></div>";
    }).catch(function () {
      element.innerHTML = "<p class=\"muted\">Could not load company detail.</p>";
    });
  }

  function renderPanelRelated() {
    var element = $("rrelated");
    if (!element) { return; }
    var symbol = String(panelState.ticker || ($("ticker") ? $("ticker").value : "")).toUpperCase();
    fetch("/api/history?limit=40").then(function (r) { return r.json(); })
      .then(function (data) {
        var rows = (data.runs || []).filter(function (row) {
          return String(row.ticker || "").toUpperCase() === symbol;
        }).slice(0, 12);
        if (!rows.length) {
          element.innerHTML = "<p class=\"muted\">No other questions have been asked about " +
            escapeHtml(companyName(symbol)) + " yet.</p>";
          return;
        }
        element.innerHTML = "<ul class=\"history\">" + rows.map(function (row) {
          return "<li data-run=\"" + escapeHtml(String(row.run_id)) + "\">" +
            "<span class=\"history-q\">" + escapeHtml(row.question || "") + "</span>" +
            "<span class=\"history-meta\">" + escapeHtml(row.final_confidence || "") +
            "</span></li>";
        }).join("") + "</ul>";
      }).catch(function () {
        element.innerHTML = "<p class=\"muted\">Could not load related questions.</p>";
      });
  }

  /* ---- History --------------------------------------------------------- */

  function loadHistory() {
    var element = $("history-list");
    if (!element) { return; }
    fetch("/api/history?limit=60").then(function (r) { return r.json(); })
      .then(function (data) {
        var rows = data.runs || [];
        if (!rows.length) {
          element.innerHTML = "<li class=\"muted\">No questions have been asked yet.</li>";
          return;
        }
        element.innerHTML = rows.map(function (row) {
          return "<li data-run=\"" + escapeHtml(String(row.run_id)) + "\">" +
            "<span class=\"history-tag\">" + escapeHtml(row.ticker || "") + "</span>" +
            "<span class=\"history-q\">" + escapeHtml(row.question || "") + "</span>" +
            "<span class=\"history-meta\">" +
            escapeHtml(String(row.created_at || "").slice(0, 16).replace("T", " ")) +
            (row.final_confidence ? " · " + escapeHtml(row.final_confidence) : "") +
            (row.needs_review ? " · needs a read" : "") +
            "</span></li>";
        }).join("");
      }).catch(function () {
        element.innerHTML = "<li class=\"muted\">Could not load history.</li>";
      });
  }

  /* ---- Companies ------------------------------------------------------- */

  function fetchCompanies() {
    if (panelState.companies) { return Promise.resolve(panelState.companies); }
    return fetch("/api/companies").then(function (r) { return r.json(); })
      .then(function (data) {
        panelState.companies = data.companies || [];
        return panelState.companies;
      });
  }

  function loadCompanies() {
    var element = $("companies");
    if (!element) { return; }
    panelState.companies = null;
    fetchCompanies().then(function (rows) {
      if (!rows.length) {
        element.innerHTML = "<p class=\"muted\">No companies are indexed.</p>";
        return;
      }
      element.innerHTML = rows.map(function (row) {
        return "<button type=\"button\" class=\"company-tile\" data-company=\"" +
          escapeHtml(row.ticker) + "\"><b>" + escapeHtml(row.company) + "</b>" +
          "<span class=\"company-ticker\">" + escapeHtml(row.ticker) + "</span>" +
          "<div class=\"company-stats\">" + escapeHtml(String(row.documents)) +
          " documents · " + escapeHtml(String(row.questions_asked)) + " questions" +
          (row.newest_document ? "<br>newest " + escapeHtml(row.newest_document) : "") +
          "</div></button>";
      }).join("");
    }).catch(function () {
      element.innerHTML = "<p class=\"muted\">Could not load companies.</p>";
    });
  }

  /* ---- Themes ---------------------------------------------------------- */

  function loadThemes() {
    var element = $("themes");
    if (!element) { return; }
    fetch("/api/themes").then(function (r) { return r.json(); })
      .then(function (data) {
        var rows = data.themes || [];
        if (!rows.length) {
          element.innerHTML = "<p class=\"muted\">" +
            escapeHtml(data.note || "No themes recorded.") + "</p>";
          return;
        }
        element.innerHTML = rows.map(function (row) {
          return "<div class=\"theme-tile\"><b>" + escapeHtml(row.theme) + "</b>" +
            "<span>" + escapeHtml(String(row.documents)) + " documents" +
            (row.newest ? " · newest " + escapeHtml(row.newest) : "") + "</span></div>";
        }).join("");
      }).catch(function () {
        element.innerHTML = "<p class=\"muted\">Could not load themes.</p>";
      });
  }

  /* ---- Evaluations ----------------------------------------------------- */

  function evalBar(row) {
    var total = Math.max(1, Number(row.total) || 0);
    function pct(value) { return (100 * (Number(value) || 0) / total).toFixed(1) + "%"; }
    return "<div class=\"eval-bar\">" +
      "<i class=\"bar-pass\" style=\"width:" + pct(row.pass_count) + "\"></i>" +
      "<i class=\"bar-review\" style=\"width:" + pct(row.review_count) + "\"></i>" +
      "<i class=\"bar-fail\" style=\"width:" + pct(row.fail_count) + "\"></i></div>";
  }

  function evalRows(rows, nameKey) {
    if (!rows || !rows.length) { return "<p class=\"muted\">Nothing recorded.</p>"; }
    return "<div class=\"eval-rows\">" + rows.map(function (row) {
      var label = nameKey === "ticker"
        ? companyName(row.ticker) + " (" + String(row.ticker) + ")"
        : String(row[nameKey] || "").replace(/_/g, " ");
      return "<div class=\"eval-row\"><span class=\"eval-row-name\">" +
        escapeHtml(label) + "</span>" +
        evalBar(row) + "<span class=\"eval-row-count\">" +
        escapeHtml(String(row.pass_count || 0)) + " pass · " +
        escapeHtml(String(row.review_count || 0)) + " review · " +
        escapeHtml(String(row.fail_count || 0)) + " fail</span></div>";
    }).join("") + "</div>";
  }

  function loadEvaluationDashboard() {
    var head = $("eval-dashboard");
    if (!head) { return; }
    fetch("/api/evaluation-dashboard").then(function (r) { return r.json(); })
      .then(function (data) {
        if (!data.available) {
          head.innerHTML = "<p class=\"muted\">" +
            escapeHtml(data.note || "No golden evaluation has been run yet.") + "</p>";
          $("eval-by-ticker").innerHTML = "";
          $("eval-by-category").innerHTML = "";
          return;
        }
        head.innerHTML =
          "<div class=\"eval-hero\"><div class=\"eval-score\">" +
          "<div class=\"eval-stat eval-pass\"><b>" + escapeHtml(String(data.pass_count)) +
          "</b><span>Pass</span></div>" +
          "<div class=\"eval-stat eval-review\"><b>" + escapeHtml(String(data.review_count)) +
          "</b><span>Review</span></div>" +
          "<div class=\"eval-stat eval-fail\"><b>" + escapeHtml(String(data.fail_count)) +
          "</b><span>Fail</span></div>" +
          "</div><div class=\"eval-meta\">" +
          "<b>" + escapeHtml(String(data.questions_total)) + " questions</b> across " +
          escapeHtml(String((data.tickers || []).length)) + " companies<br>" +
          "Last run " + escapeHtml(String(data.completed_at || "").slice(0, 16)
            .replace("T", " ")) + "<br>" +
          "Critical failures: <b>" + escapeHtml(String(data.critical_failures)) + "</b>" +
          "</div></div>" +
          "<p class=\"eval-note\">" + escapeHtml(data.review_means || "") + "</p>" +
          "<p class=\"eval-note\">" + escapeHtml(data.pass_means || "") + "</p>";
        $("eval-by-ticker").innerHTML = evalRows(data.per_ticker, "ticker");
        $("eval-by-category").innerHTML = evalRows(data.per_category, "category");
      }).catch(function () {
        head.innerHTML = "<p class=\"muted\">Could not load the evaluation.</p>";
      });
  }

  function loadEvaluationQuestions() {
    var element = $("eval-questions");
    if (!element || element.dataset.loaded) { return; }
    fetch("/api/evaluation-questions").then(function (r) { return r.json(); })
      .then(function (data) {
        var rows = data.questions || [];
        if (!rows.length) {
          element.innerHTML = "<p class=\"muted\">No per-question results recorded.</p>";
          return;
        }
        element.innerHTML = rows.map(function (row) {
          var why = row.failed_checks || row.review_checks || "";
          return "<div class=\"eval-qrow\"><span class=\"verdict verdict-" +
            escapeHtml(row.overall_status) + "\">" + escapeHtml(row.overall_status) +
            "</span><span class=\"history-tag\">" + escapeHtml(row.ticker) + "</span>" +
            "<span class=\"history-q\">" + escapeHtml(row.question || "") +
            (why ? "<br><span class=\"muted\">" + escapeHtml(why.replace(/_/g, " ")) +
              "</span>" : "") + "</span>" +
            "<span class=\"history-meta\">" + escapeHtml(row.final_confidence || "") +
            "</span></div>";
        }).join("");
        element.dataset.loaded = "1";
      }).catch(function () {
        element.innerHTML = "<p class=\"muted\">Could not load the question list.</p>";
      });
  }

  /* ---- wiring ---------------------------------------------------------- */

  Array.prototype.forEach.call(document.querySelectorAll(".depth-option"), function (button) {
    button.addEventListener("click", function () {
      setDepth(button.getAttribute("data-depth"));
    });
  });
  Array.prototype.forEach.call(document.querySelectorAll(".rtab"), function (tab) {
    tab.addEventListener("click", function () {
      selectRPane(tab.getAttribute("data-rpane"));
    });
  });
  // Phase 13.1. Collapsing used to toggle a class and nothing else, so the
  // panel had no way back: the button that hid it went with it. One function
  // owns the state now, and it is what shows the restore tab.
  if ($("panel-collapse")) {
    $("panel-collapse").addEventListener("click", function () {
      setPanelHidden(!panelHidden);
    });
  }

  // A suggestion or follow-up chip fills the question box and asks. The old
  // preset buttons still work; these carry the text itself rather than a key.
  document.addEventListener("click", function (event) {
    var chip = event.target.closest ? event.target.closest("[data-question]") : null;
    if (chip) {
      // A chip marked data-followup is about the answer on screen, so it goes
      // through the follow-up path and carries the run id with it. Everything
      // else is an ordinary new question.
      if (chip.getAttribute("data-followup")) {
        startFollowUp(chip.getAttribute("data-question"));
      } else {
        pendingFollowUp = null;
        clearContextNote();
        setQuestionText(chip.getAttribute("data-question"));
        ask();
      }
      return;
    }
    // Only the lists this phase added. #runs has had its own binding since
    // Phase 7B, and handling it here too fired loadRun twice for one click -
    // two answers racing into the same panes, which is how the Phase 10C.1
    // browser walk started failing.
    var row = event.target.closest
      ? event.target.closest("#history-list [data-run], #rrelated [data-run]") : null;
    if (row && row.getAttribute("data-run")) {
      loadRun(Number(row.getAttribute("data-run")));
      return;
    }
    var tile = event.target.closest ? event.target.closest("[data-company]") : null;
    if (tile && tile.getAttribute("data-company")) {
      setSelect("ticker", tile.getAttribute("data-company"));
      if (typeof describeTicker === "function") { describeTicker(); }
      // Phase 13.1. Picking a company from the Companies page is a change of
      // subject, so it gets the same reset as changing the select.
      applyCompanyContext(tile.getAttribute("data-company"), false);
      switchView("ask");
      $("question").focus();
    }
  });

  if ($("followup-send")) {
    $("followup-send").addEventListener("click", sendFollowup);
  }
  if ($("followup")) {
    $("followup").addEventListener("keydown", function (event) {
      if (event.key === "Enter") { sendFollowup(); }
    });
  }

  function sendFollowup() {
    var box = $("followup");
    if (!box || !box.value.trim()) { return; }
    var text = box.value.trim();
    box.value = "";
    // Phase 13.1. This used to copy the text into the question box and ask it
    // cold, which is why "why?" searched the archive for the word "why". It now
    // carries the run it follows on from.
    startFollowUp(text);
    $("research-scroll").scrollTop = 0;
  }

  if ($("refresh-companies")) {
    $("refresh-companies").addEventListener("click", function () {
      panelState.companies = null;
      loadCompanies();
    });
  }
  if ($("refresh-themes")) {
    $("refresh-themes").addEventListener("click", loadThemes);
  }
  if ($("eval-detail")) {
    $("eval-detail").addEventListener("toggle", function () {
      if ($("eval-detail").open) { loadEvaluationQuestions(); }
    });
  }

  /* ======================================================================
     Phase 13.1 - research context, evidence, and a composer that works

     Phase 13 built the workspace. Using it exposed five things that a shell
     cannot fix on its own, because all five are about what the page REMEMBERS
     between one action and the next:

       1. the question box grew under its own button
       2. a follow-up was a brand new question, so "why?" searched the archive
          for the word "why"
       3. collapsing the sources panel lost it until you navigated away and back
       4. changing company left the last company's questions on screen
       5. a citation was a number, not a way to reach the page it came from

     The rule for all of it: context is EXPLICIT and NAMED. There is no
     conversation buffer, no session memory and no agent. A follow-up carries
     one previous run id; the server looks up what that run actually cited. What
     the pipeline was asked is always visible in the run row, which is what
     keeps a run reproducible.
     ====================================================================== */

  // What the page knows about the answer currently on screen. Set from the
  // run's own record, never from the markdown.
  var researchContext = {
    runId: 0, question: "", ticker: "", documentIds: [], chunkIds: [], citations: []
  };

  // The evidence pack behind that answer, and which piece of it is open.
  var evidenceState = { items: [], selected: 0, documentId: "" };

  // Per-company context, cached so switching back and forth is instant.
  var companyState = { ticker: "", suggestions: {}, context: {} };

  // Set only for the moment between pressing a follow-up and the reply
  // arriving. Non-null is what makes ask() send follow_up: true.
  var pendingFollowUp = null;

  var panelHidden = false;

  /* ---- 1. the question composer ---------------------------------------
     The box grows with the question between a one-line minimum and a cap the
     stylesheet also enforces, and the button is a separate grid cell, so text
     can never run underneath it. Autosize is a function rather than an inline
     handler because text arrives four ways - typed, from a suggestion chip,
     from a follow-up, and from a cleared box - and only one of those is an
     input event. That was the bug: a suggestion filled the box and the box
     stayed one line tall. */

  var QUESTION_MIN_HEIGHT = 38;
  var QUESTION_MAX_HEIGHT = 168;

  function autosizeQuestion() {
    var box = $("question");
    if (!box) { return; }
    box.style.height = "auto";
    var wanted = Math.max(QUESTION_MIN_HEIGHT, Math.min(box.scrollHeight, QUESTION_MAX_HEIGHT));
    box.style.height = wanted + "px";
    // At the cap the box scrolls rather than growing, so the last line typed is
    // always the line on screen.
    box.style.overflowY = box.scrollHeight > QUESTION_MAX_HEIGHT ? "auto" : "hidden";
  }

  function setQuestionText(text, focus) {
    var box = $("question");
    if (!box) { return; }
    box.value = String(text === undefined || text === null ? "" : text);
    autosizeQuestion();
    if (focus) {
      box.focus();
      box.setSelectionRange(box.value.length, box.value.length);
    }
  }

  /* ---- 2. follow-up context -------------------------------------------- */

  function rememberContext(data) {
    if (!data) { return; }
    var evidence = data.evidence || [];
    // An evidence-reference reply is not a run, so it must not become the thing
    // the next follow-up follows up on - the answer it was about still is.
    var runId = data.run_id || data.derived_from_run_id || researchContext.runId || 0;
    var documents = [];
    var chunks = [];
    var citations = [];
    evidence.forEach(function (item) {
      if (item.document_id && documents.indexOf(item.document_id) < 0) {
        documents.push(item.document_id);
      }
      if (item.chunk_id && chunks.indexOf(item.chunk_id) < 0) { chunks.push(item.chunk_id); }
      if (item.citation !== null && item.citation !== undefined) { citations.push(item.citation); }
    });
    researchContext.runId = runId;
    if (data.question) { researchContext.question = data.question; }
    if (data.ticker) { researchContext.ticker = String(data.ticker).toUpperCase(); }
    else if ($("ticker")) { researchContext.ticker = $("ticker").value; }
    if (evidence.length) {
      researchContext.documentIds = documents;
      researchContext.chunkIds = chunks;
      researchContext.citations = citations;
      evidenceState.items = evidence;
    }
  }

  // The server decides what a follow-up does; this only reports the decision.
  // A reader who can see "searched inside the 4 documents the previous answer
  // came from" can disagree with it and ask again plainly.
  function renderFollowUpNote(data) {
    var note = $("context-note");
    var text = $("context-note-text");
    if (!note || !text) { return; }
    var plan = data && data.follow_up;
    // A follow-up naming another company is the one "new subject" worth saying
    // out loud: the context was dropped, which is right, but the question still
    // went to the company in the box and the reader needs to know that.
    var warn = !!(plan && plan.names_other_company);
    if (!plan || !plan.kind || (plan.kind === "new_subject" && !warn)) {
      clearContextNote();
      return;
    }
    note.classList.toggle("context-note-warn", warn);
    text.textContent = plan.explanation ||
      "Answered in the context of the previous question.";
    show(note, true);
  }

  function clearContextNote() {
    var note = $("context-note");
    if (note) { show(note, false); }
  }

  // Asking a follow-up is asking a question with one extra fact attached: the
  // run it follows on from. Everything else is the normal ask() path, so there
  // is one retrieval system, one cost gate and one run row.
  function startFollowUp(text) {
    if (!String(text || "").trim()) { return; }
    if (!researchContext.runId) {
      // Nothing to follow up on. Ask it as an ordinary question rather than
      // pretending there is context.
      pendingFollowUp = null;
      setQuestionText(text);
      ask();
      return;
    }
    pendingFollowUp = { text: String(text).trim(), previousRunId: researchContext.runId };
    setQuestionText(text);
    ask();
  }

  /* ---- 3. the research panel, always restorable ------------------------ */

  function setPanelHidden(hidden) {
    panelHidden = !!hidden;
    var workspace = $("view-ask");
    if (workspace) { workspace.classList.toggle("panel-hidden", panelHidden); }
    var restore = $("panel-restore");
    if (restore) {
      show(restore, panelHidden);
      var count = $("panel-restore-count");
      if (count) {
        count.textContent = evidenceState.items.length
          ? "(" + evidenceState.items.length + ")" : "";
      }
    }
  }

  /* ---- 4. answer -> citation -> document ------------------------------- */

  function passageBlock(item) {
    // The text the answer was written from, beside the claim it supports. This
    // is the whole of "verification" for most questions: read the sentence, read
    // the passage, decide whether one follows from the other.
    if (item.supporting_text) {
      return "<details class=\"source-passage\"><summary>The passage this came from" +
        (item.page_label ? " · " + escapeHtml(item.page_label) : "") + "</summary>" +
        "<pre class=\"passage-text\">" + escapeHtml(item.supporting_text) +
        (item.supporting_text_truncated ? "\n…" : "") + "</pre></details>";
    }
    if (item.chunk_id) {
      return "<p class=\"hint\">The retrieved passage for this citation is no longer in the " +
        "index under this company, so it cannot be shown. The document itself is still " +
        "openable.</p>";
    }
    return "";
  }

  // Turn every [n] in the rendered answer into a control. The regex skips
  // anything inside a tag, so an attribute containing a bracketed number is
  // left alone.
  function linkCitations(html) {
    return String(html || "").replace(/(<[^>]*>)|\[(\d{1,3})\]/g,
      function (whole, tag, number) {
        if (tag) { return tag; }
        return "<button type=\"button\" class=\"cite-link\" data-citation=\"" + number +
          "\" title=\"Show the source for citation " + number + "\">[" + number + "]</button>";
      });
  }

  function wireCitations() {
    var answer = $("answer");
    if (!answer) { return; }
    Array.prototype.forEach.call(answer.querySelectorAll(".cite-link"), function (button) {
      button.addEventListener("click", function () {
        openCitation(Number(button.getAttribute("data-citation")));
      });
    });
  }

  function evidenceFor(citation) {
    var wanted = Number(citation);
    var found = null;
    (evidenceState.items || []).forEach(function (item) {
      if (Number(item.citation) === wanted) { found = item; }
    });
    return found;
  }

  function markSelectedSource(citation) {
    Array.prototype.forEach.call(document.querySelectorAll("#sources .source"),
      function (card) {
        card.classList.toggle("source-selected",
          Number(card.getAttribute("data-citation")) === Number(citation));
      });
  }

  // Clicking [3] in the answer. Restore the panel first - a citation must work
  // whether or not the rail happens to be open - then select source 3 and open
  // the document behind it.
  function openCitation(citation) {
    var item = evidenceFor(citation);
    setPanelHidden(false);
    markSelectedSource(citation);
    if (!item) {
      // The answer cited a number the retrieval log does not have. That is an
      // audit finding, not something to paper over.
      selectRPane("sources");
      var lead = $("sources-lead");
      if (lead) {
        lead.textContent = "The answer cites [" + citation + "], but the run's retrieval log " +
          "has no source with that number. This is flagged in the automated checks.";
      }
      return;
    }
    openEvidence(citation);
  }

  function openEvidence(citation) {
    var item = evidenceFor(citation);
    if (!item) { return; }
    evidenceState.selected = Number(citation);
    evidenceState.documentId = item.document_id || "";
    setPanelHidden(false);
    markSelectedSource(citation);
    selectRPane("documents");
    renderRailDocument(item);
  }

  function resetRailDocument() {
    evidenceState.selected = 0;
    evidenceState.documentId = "";
    var body = $("rdoc-body");
    if (body) { body.innerHTML = ""; show(body, false); }
    if ($("rdoc-empty")) { show($("rdoc-empty"), true); }
  }

  function railDocumentHeader(item) {
    return "<div class=\"rdoc-head\">" +
      "<span class=\"citation\">[" + escapeHtml(item.citation) + "]</span>" +
      "<h3 class=\"rdoc-title\">" + escapeHtml(item.title || item.file_name || "document") +
      "</h3></div>" +
      "<div class=\"rdoc-meta\">" +
      escapeHtml(item.publisher || "no publisher recorded") + " · " +
      escapeHtml(item.document_date || "no date recorded") + " · " +
      escapeHtml(item.source_type || "no source type") +
      (item.page_label ? " · " + escapeHtml(item.page_label) : "") +
      " · " + escapeHtml(item.ticker || "") + "</div>";
  }

  function railPassage(item) {
    if (!item.supporting_text) {
      return "<p class=\"hint\">The retrieved passage for this citation is not in the index " +
        "under this company, so it cannot be shown.</p>";
    }
    return "<h4 class=\"rdoc-section\">The passage this answer used</h4>" +
      "<pre class=\"passage-text\">" + escapeHtml(item.supporting_text) +
      (item.supporting_text_truncated ? "\n…" : "") + "</pre>";
  }

  // The original, in the rail, on the page the citation came from.
  //
  // The page is reached with the PDF viewer's own #page fragment - the browser's
  // built-in viewer, told which page to open. There is no coordinate maths and
  // nothing is drawn on top of the document: a highlight that is a few
  // millimetres wrong is worse than no highlight, because it points at the wrong
  // sentence with total confidence.
  function railOriginal(item, preview) {
    var page = Number(item.page_start || item.page_start_guess || 0);
    var fragment = page > 0 ? "#page=" + page : "";
    if (preview && preview.enabled && preview.url) {
      return "<h4 class=\"rdoc-section\">The original document" +
        (page > 0 ? ", at " + escapeHtml(item.page_label || ("p. " + page)) : "") + "</h4>" +
        (page > 0
          ? ""
          : "<p class=\"hint\">No page was recorded for this passage, so the document opens at " +
            "the beginning.</p>") +
        "<div class=\"rdoc-frame\"><iframe title=\"Original PDF preview\" src=\"" +
        escapeHtml(preview.url) + fragment + "\"></iframe></div>";
    }
    // Three different things, said differently. "No preview" when the pipeline
    // recorded a reason, and "the details could not be read" when the request
    // itself failed - which is what happens for a chunk indexed before document
    // ids existed. Reporting the second as the first would send someone looking
    // for a missing file that is not missing.
    var reason = preview
      ? (preview.message || "There is no preview for this document.")
      : "This document's details could not be read, so the original cannot be shown here. "
        + "The passage above is what the answer used.";
    return "<h4 class=\"rdoc-section\">The original document</h4>" +
      "<div class=\"notice notice-warn notice-slim\">" + escapeHtml(reason) + "</div>" +
      (page > 0
        ? "<p class=\"hint\">The passage is on " + escapeHtml(item.page_label || ("p. " + page)) +
          ". Read the extracted text of that page below, or open the document in the full " +
          "library.</p>"
        : "");
  }

  function renderRailDocument(item) {
    var body = $("rdoc-body");
    if (!body) { return; }
    if ($("rdoc-empty")) { show($("rdoc-empty"), false); }
    show(body, true);
    body.innerHTML = railDocumentHeader(item) +
      "<p class=\"muted\">Loading the document…</p>";

    var documentId = item.document_id;
    if (!documentId) {
      body.innerHTML = railDocumentHeader(item) + railPassage(item) +
        "<p class=\"hint\">This passage predates document ids, so the original cannot be opened " +
        "from here.</p>";
      return;
    }

    fetch("/api/document/" + encodeURIComponent(documentId))
      .then(function (response) { return response.ok ? response.json() : null; })
      .then(function (data) {
        var preview = data && data.preview ? data.preview : null;
        var page = Number(item.page_start || item.page_start_guess || 0);
        body.innerHTML = railDocumentHeader(item) +
          railPassage(item) +
          railOriginal(item, preview) +
          "<div class=\"rdoc-actions\">" +
          (page > 0
            ? "<button type=\"button\" class=\"button-quiet rdoc-page\" data-page=\"" + page +
              "\">Read the extracted text of " +
              escapeHtml(item.page_label || ("p. " + page)) + "</button>"
            : "") +
          "<button type=\"button\" class=\"link-button rdoc-library\" data-document=\"" +
          escapeHtml(documentId) + "\">Open in the full library</button>" +
          "</div><div id=\"rdoc-page-text\"></div>";

        var pageButton = body.querySelector(".rdoc-page");
        if (pageButton) {
          pageButton.addEventListener("click", function () {
            var target = $("rdoc-page-text");
            target.innerHTML = "<p class=\"muted\">Loading page…</p>";
            fetch("/api/document/" + encodeURIComponent(documentId) + "/text?page=" +
              encodeURIComponent(pageButton.getAttribute("data-page")))
              .then(function (r) { return r.ok ? r.json() : null; })
              .then(function (payload) {
                var text = payload && (payload.text || payload.extracted_text || "");
                target.innerHTML = text
                  ? "<pre class=\"extracted\">" + escapeHtml(text) + "</pre>"
                  : "<p class=\"muted\">That page has no extracted text. It was most likely an " +
                    "image.</p>";
              })
              .catch(function () {
                target.innerHTML = "<p class=\"muted\">The page text could not be read.</p>";
              });
          });
        }
        var libraryButton = body.querySelector(".rdoc-library");
        if (libraryButton) {
          libraryButton.addEventListener("click", function () {
            openDocument(libraryButton.getAttribute("data-document"));
          });
        }
      })
      .catch(function () {
        body.innerHTML = railDocumentHeader(item) + railPassage(item) +
          "<p class=\"muted\">The document's details could not be read, so the original cannot " +
          "be shown here. The passage above is what the answer used.</p>";
      });
  }

  /* ---- 5. company context ---------------------------------------------- */

  // Every question offered here came out of the Phase 12 golden question file,
  // which was written by hand per company. No model is asked to invent a
  // suggestion: that would cost money, differ between page loads, and put
  // unreviewed text in front of a reader as if the system endorsed it.
  function renderSuggestions(context) {
    var holder = document.querySelector("#suggestions .preset-buttons");
    if (!holder) { return; }
    var suggestions = (context && context.suggestions) || [];
    if (!suggestions.length) { return; }
    holder.innerHTML = suggestions.map(function (item) {
      return "<button type=\"button\" class=\"preset suggest-chip\" data-question=\"" +
        escapeHtml(item.question) + "\" title=\"" + escapeHtml(item.label) + "\">" +
        escapeHtml(item.question) + "</button>";
    }).join("");
    var label = document.querySelector("#suggestions .suggestions-label");
    if (label) {
      label.textContent = "Try asking about " + (context.company || context.ticker);
    }
  }

  function resetAnswerState() {
    ["answer-block", "answer", "trust", "summary", "audit", "audit-wrap", "console", "followups",
      "followbar", "placeholder"].forEach(function (id) {
      if ($(id)) { show($(id), false); }
    });
    if ($("badges")) { $("badges").innerHTML = ""; }
    if ($("research-main")) { $("research-main").classList.remove("answered"); }
    show($("sources-card"), false);
    show($("diagnostics-card"), false);
    if ($("sources-empty")) { show($("sources-empty"), true); }
    if ($("rtab-sources-count")) { $("rtab-sources-count").textContent = ""; }
    if ($("sources")) { $("sources").innerHTML = ""; }
    resetRailDocument();
    clearContextNote();
    evidenceState.items = [];
    evidenceState.selected = 0;
    researchContext = {
      runId: 0, question: "", ticker: ($("ticker") ? $("ticker").value : ""),
      documentIds: [], chunkIds: [], citations: []
    };
    pendingFollowUp = null;
    selectRPane("sources");
    setPanelHidden(false);
  }

  // Changing company changes the subject. Everything on screen that was about
  // the last company goes, because an answer about Kroger sitting under a
  // Kimco heading is worse than an empty screen.
  function applyCompanyContext(symbol, keepAnswer) {
    var ticker = String(symbol || "").toUpperCase();
    if (!ticker) { return Promise.resolve(null); }
    companyState.ticker = ticker;
    panelState.ticker = ticker;
    if (!keepAnswer) { resetAnswerState(); }

    var cached = companyState.context[ticker];
    var ready = cached
      ? Promise.resolve(cached)
      : fetch("/api/company-context/" + encodeURIComponent(ticker))
        .then(function (response) { return response.ok ? response.json() : null; })
        .then(function (context) {
          if (context) { companyState.context[ticker] = context; }
          return context;
        });

    return ready.then(function (context) {
      if (!context || companyState.ticker !== ticker) { return context; }
      companyState.suggestions[ticker] = context.suggestions || [];
      var box = $("question");
      if (box && context.placeholder) { box.placeholder = context.placeholder; }
      renderSuggestions(context);
      if (panelState.pane === "company") { renderPanelCompany(); }
      if (panelState.pane === "related") { renderPanelRelated(); }
      return context;
    }).catch(function () { return null; });
  }

  /* ---- wiring ---------------------------------------------------------- */

  if ($("question")) {
    $("question").addEventListener("input", autosizeQuestion);
  }
  if ($("panel-restore")) {
    $("panel-restore").addEventListener("click", function () { setPanelHidden(false); });
  }
  if ($("context-clear")) {
    $("context-clear").addEventListener("click", function () {
      pendingFollowUp = null;
      clearContextNote();
      setQuestionText("", true);
    });
  }
  if ($("ticker")) {
    $("ticker").addEventListener("change", function () {
      applyCompanyContext($("ticker").value, false);
    });
  }

  applyCompanyContext($("ticker") ? $("ticker").value : "MSFT", true);
  autosizeQuestion();
  setPanelHidden(false);

  setDepth("quick");
})();
