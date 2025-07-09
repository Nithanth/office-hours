// report.js – handles actions specific to the rendered research report

// ---------------------------------------------------------------------------
// Copy Report Button (simple clipboard helper)
// ---------------------------------------------------------------------------
function fallbackCopy(text) {
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.style.position = "fixed";
  ta.style.top = "-1000px";
  document.body.appendChild(ta);
  ta.focus();
  ta.select();
  try {
    document.execCommand("copy");
  } finally {
    document.body.removeChild(ta);
  }
}

function getCleanReportText(reportEl) {
  // Helper to walk DOM and collect text with newlines between blocks.
  const lines = [];

  const BLOCK_TAGS = new Set([
    "P",
    "DIV",
    "SECTION",
    "ARTICLE",
    "H1",
    "H2",
    "H3",
    "H4",
    "H5",
    "H6",
    "LI",
    "BR",
  ]);

  function dfs(node) {
    if (node.nodeType === Node.TEXT_NODE) {
      const txt = node.textContent.replace(/\s+/g, " ").trim();
      if (txt) lines.push(txt);
      return;
    }

    if (node.nodeType !== Node.ELEMENT_NODE) return;

    // Skip interactive/UI elements
    const el = node;
    if (
      el.matches?.("button, #report-actions, .copy-icon-btn, .ask-assistant-btn")
    ) {
      return;
    }

    // Skip top-level heading exactly "Report"
    if (
      (el.tagName === "H1" || el.tagName === "H2" || el.tagName === "H3") &&
      el.textContent.trim().toLowerCase() === "report"
    ) {
      return;
    }

    for (const child of el.childNodes) dfs(child);

    if (BLOCK_TAGS.has(el.tagName)) {
      lines.push("\n");
    }
  }

  dfs(reportEl);

  // Collapse multiple consecutive newlines
  return lines.join(" ")
    .replace(/(\s*\n\s*)+/g, "\n\n")
    .trim();
}

function handleCopyClick(ev) {
  const btn = ev.target.closest("#copy-report-btn");
  if (!btn) return; // not our button

  const reportEl = document.getElementById("report-content");
  if (!reportEl) {
    // eslint-disable-next-line no-alert
    alert("Copy failed: report not found.");
    return;
  }

  const txt = getCleanReportText(reportEl);
  const markCopied = () => {
    btn.classList.add("copied");
    setTimeout(() => btn.classList.remove("copied"), 2000);
  };

  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(txt).then(markCopied).catch(() => {
      fallbackCopy(txt);
      markCopied();
    });
  } else {
    fallbackCopy(txt);
    markCopied();
  }
}

// Delegate clicks at document level so the report can be injected later.
document.addEventListener("click", handleCopyClick);

// ---------------------------------------------------------------------------
// MutationObserver – ensure action buttons become visible regardless of HTMX
// timing quirks. Whenever an element with id "report-actions" is added to the
// DOM, strip its `hidden` class and force display:flex. This avoids race
// conditions where HTMX replaces the node after JS already removed the class.
// ---------------------------------------------------------------------------
const actionsObserver = new MutationObserver(() => {
  // Only unhide if progress spinner already gone (report finished)
  const progressGone = !document.getElementById("report-progress");
  if (!progressGone) return;

  document.querySelectorAll("#report-actions.hidden").forEach((el) => {
    el.classList.remove("hidden");
    el.style.display = "flex";
    el.style.justifyContent = "flex-end";

    // Enable notes button once report finished.
    const notesBtn = el.querySelector("#report-notes-btn");
    if (notesBtn) {
      notesBtn.removeAttribute("disabled");
      notesBtn.title = "Generate Notes";
    }
  });
});

// Observe the whole document for added nodes (this is inexpensive for the
// small incremental updates HTMX performs).
actionsObserver.observe(document.body, { childList: true, subtree: true });

// ---------------------------------------------------------------------------
// Reveal action buttons when report streaming finishes (HTMX/SSE)
// ---------------------------------------------------------------------------
document.addEventListener("htmx:afterSwap", () => {
  try {
    // If the progress spinner has been removed, show the actions
    const progressGone = !document.getElementById("report-progress");
    if (progressGone) {
      console.debug("[lux] report.js: afterSwap – progress gone, unhide actions");
      const actions = document.getElementById("report-actions");
      if (actions) {
        actions.classList.remove("hidden");
        actions.style.display = "flex";
        actions.style.justifyContent = "flex-end";

        // Add generic report heading if backend didn't provide one
        const reportEl = document.getElementById("report-content");
        if (reportEl && !reportEl.querySelector("h2")) {
          const h2 = document.createElement("h2");
          h2.textContent = "Research Report";
          h2.className = "text-2xl font-bold mb-4";
          reportEl.insertBefore(h2, reportEl.firstChild);
        }
      }
    }
  } catch (e) {
    console.warn("[lux] report.js: afterSwap error", e);
  }
});

// Fallback: if streaming already finished before JS loaded
document.addEventListener("DOMContentLoaded", () => {
  const progressGone = !document.getElementById("report-progress");
  const actions = document.getElementById("report-actions");
  if (progressGone && actions && actions.classList.contains("hidden")) {
    console.debug("[lux] report.js: fallback unhide actions (DOMContentLoaded)");
    actions.classList.remove("hidden");
    actions.style.display = "flex";
    actions.style.justifyContent = "flex-end";
  }
});

// ---------------------------------------------------------------------------
// Notes dialog helper – lazy-inject <dialog> so user can choose filename & format.
// ---------------------------------------------------------------------------
function openNotesDialog(onConfirm) {
  let dlg = document.getElementById("notes-dialog");
  if (!dlg) {
    document.body.insertAdjacentHTML(
      "beforeend",
      `<dialog id="notes-dialog" class="lux-modal">
         <form method="dialog" id="notes-form">
           <h3 style="margin:0 0 12px">Download Report Notes</h3>

           <label class="block" style="display:block;">
             File name:<br>
             <input name="fname" type="text" value="report_notes" required style="width:100%;margin-top:4px;">
           </label>

           <fieldset style="margin-top:12px;">
             <legend>Format</legend>
             <label style="margin-right:8px;"><input type="radio" name="fmt" value="txt" checked> .txt</label>
             <label><input type="radio" name="fmt" value="docx"> .docx</label>
           </fieldset>

           <menu style="margin-top:16px;display:flex;justify-content:flex-end;gap:8px;">
             <button value="cancel">Cancel</button>
             <button value="ok" class="primary">Generate</button>
           </menu>
         </form>
       </dialog>`
    );
    dlg = document.getElementById("notes-dialog");
    dlg.addEventListener("close", () => {
      if (dlg.returnValue !== "ok") return; // user canceled
      const form = dlg.querySelector("#notes-form");
      const base = form.fname.value.trim() || "report_notes";
      const fmt = form.fmt.value;
      onConfirm(base, fmt);
    });
  }
  dlg.showModal();
}

// ---------------------------------------------------------------------------
// Generate Notes – open dialog, then POST and download.
// ---------------------------------------------------------------------------
function handleNotesClick(ev) {
  const btn = ev.target.closest("#report-notes-btn");
  if (!btn) return;

  const reportEl = document.getElementById("report-content");
  if (!reportEl) return;

  openNotesDialog((baseName, format) => {
    let desiredName = baseName;
    if (!desiredName.toLowerCase().endsWith(`.${format}`)) desiredName += `.${format}`;

    btn.disabled = true;
    const origHtml = btn.innerHTML;
    btn.innerHTML =
      '<span class="lux-spinner small" style="vertical-align:middle;"></span> Generating…';

    const cleaned = getCleanReportText(reportEl);
    const fd = new FormData();
    fd.append("report_text", cleaned);
    fd.append("output_format", format);

    fetch("/generate-report-notes", { method: "POST", body: fd })
      .then((r) => {
        if (!r.ok) throw new Error(`Server responded ${r.status}`);
        return r.blob();
      })
      .then((blob) => {
        const url = URL.createObjectURL(blob);
        const a = Object.assign(document.createElement("a"), { href: url, download: desiredName });
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(url);
      })
      .catch((err) => {
        console.error("[lux] notes download failed", err);
        // eslint-disable-next-line no-alert
        alert("Failed to generate notes. Please try again.");
      })
      .finally(() => {
        btn.disabled = false;
        btn.innerHTML = origHtml;
      });
  });
}

document.addEventListener("click", handleNotesClick);
