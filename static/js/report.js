// report.js – handles actions specific to the rendered research report

// ---------------------------------------------------------------------------
// One-off style & constants (module scope)
// ---------------------------------------------------------------------------
// Inject utility class so we avoid setting style props in JS.
(function addReportStyles() {
  if (document.getElementById("lux-report-styles")) return;
  const style = document.createElement("style");
  style.id = "lux-report-styles";
  style.textContent = ".lux-flex-end{display:flex;justify-content:flex-end;}";
  document.head.appendChild(style);
})();

// Hoisted so we don’t recreate on every getCleanReportText() call.
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
    const ok = document.execCommand("copy");
    if (!ok) console.warn("[lux] execCommand('copy') returned false");
  } catch (e) {
    console.warn("[lux] execCommand('copy') failed", e);
  } finally {
    document.body.removeChild(ta);
  }
}

function getCleanReportText(reportEl) {
  // Helper to walk DOM and collect text with newlines between blocks.
  const lines = [];

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
    el.classList.add("lux-flex-end");

    // Enable notes + blog buttons once report finished.
    const notesBtn = el.querySelector("#report-notes-btn");
    if (notesBtn) {
      notesBtn.removeAttribute("disabled");
      notesBtn.title = "Generate Notes";
    }
    const blogBtn = el.querySelector("#report-blog-btn");
    if (blogBtn) {
      blogBtn.removeAttribute("disabled");
      blogBtn.title = "Generate Blog Post";
    }
  });
});

// Observe the whole document for added nodes (this is inexpensive for the
// small incremental updates HTMX performs).
actionsObserver.observe(document.body, { childList: true, subtree: true });

// ---------------------------------------------------------------------------
// Ensure HTMX processes hx-* attributes on SSE-injected report blocks
// (e.g. "Ask Assistant" buttons with hx-get).
// ---------------------------------------------------------------------------
document.addEventListener("htmx:afterSwap", (evt) => {
  const tgt = evt?.detail?.target;
  if (tgt && tgt.id === "report-blocks" && window.htmx) {
    window.htmx.process(tgt);
  }
});

// ---------------------------------------------------------------------------
// Reveal action buttons when report streaming finishes (HTMX/SSE)
// ---------------------------------------------------------------------------
document.addEventListener("htmx:afterSwap", (evt) => {
  // Only trigger when the swap pertains to the report progress element (i.e. final "done" SSE swap).
  const tgt = evt?.detail?.target;
  if (!tgt || tgt.id !== "report-progress") {
    return;
  }

  try {
    // If the progress spinner has been removed, show the actions
    const progressGone = !document.getElementById("report-progress");
    if (progressGone) {
      console.debug("[lux] report.js: afterSwap - progress gone, unhide actions");
      const actions = document.getElementById("report-actions");
      if (actions) {
        actions.classList.remove("hidden");
        actions.classList.add("lux-flex-end");

        // Toast feedback – notify user that report is ready
        if (window.showToast && !window._luxReportToastShown) {
          window.showToast("Report ready! Click the copy button to grab the text.");
          window._luxReportToastShown = true;
        }

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
    actions.classList.add("lux-flex-end");

    if (window.showToast && !window._luxReportToastShown) {
      window.showToast("Report ready! Click the copy button to grab the text.");
      window._luxReportToastShown = true;
    }
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
             <label style="margin-right:8px;"><input type="radio" name="fmt" value="md" checked> .md</label>
             <label style="margin-right:8px;"><input type="radio" name="fmt" value="txt"> .txt</label>
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

// ---------------------------------------------------------------------------
// Blog Post generation – POST report text, open rendered blog in new tab
// ---------------------------------------------------------------------------
function handleBlogClick(ev) {
  const btn = ev.target.closest("#report-blog-btn");
  if (!btn) return;

  const reportEl = document.getElementById("report-content");
  if (!reportEl) return;

  btn.disabled = true;
  const origHtml = btn.innerHTML;
  btn.innerHTML =
    '<span class="lux-spinner small" style="vertical-align:middle;"></span> Writing…';

  const cleaned = getCleanReportText(reportEl);
  const fd = new FormData();
  fd.append("report_text", cleaned);

  fetch("/generate-blog", { method: "POST", body: fd })
    .then((r) => {
      if (!r.ok) throw new Error(`Server responded ${r.status}`);
      return r.json();
    })
    .then((data) => {
      if (data.url) {
        window.open(data.url, "_blank");
      }
    })
    .catch((err) => {
      console.error("[lux] blog generation failed", err);
      // eslint-disable-next-line no-alert
      alert("Failed to generate blog post. Please try again.");
    })
    .finally(() => {
      btn.disabled = false;
      btn.innerHTML = origHtml;
    });
}

document.addEventListener("click", handleBlogClick);

(function () {
  // simple toast fallback if settings.js not loaded
  if (typeof window.showToast !== "function") {
    window.showToast = function (msg, ms = 3500) {
      const t = document.createElement("div");
      t.textContent = msg;
      t.style.cssText =
        "position:fixed;bottom:24px;left:50%;transform:translateX(-50%);" +
        "background:#333;color:#fff;padding:8px 12px;border-radius:4px;z-index:9999;" +
        "font-size:14px;opacity:0;transition:opacity .2s";
      document.body.appendChild(t);
      requestAnimationFrame(() => (t.style.opacity = "1"));
      setTimeout(() => {
        t.style.opacity = "0";
        setTimeout(() => t.remove(), 300);
      }, ms);
    };
  }
})();
