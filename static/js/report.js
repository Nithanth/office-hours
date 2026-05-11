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
      blogBtn.title = "Generate Blog Post (opens customization)";
    }
    const notionBtn = el.querySelector("#report-notion-btn");
    if (notionBtn) {
      notionBtn.removeAttribute("disabled");
      notionBtn.title = "Export to Notion";
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

document.body.addEventListener("htmx:sseMessage", (evt) => {
  if (evt.detail?.type !== "report-saved") return;
  try {
    const payload = JSON.parse(evt.detail.data || "{}");
    if (!payload.report_id) return;
    const reportEl = document.getElementById("report-content");
    if (reportEl) reportEl.dataset.reportId = payload.report_id;
    document.querySelectorAll(".ask-assistant-btn[hx-get]").forEach((btn) => {
      const current = btn.getAttribute("hx-get") || "";
      if (current && !current.includes("report_id=")) {
        const sep = current.includes("?") ? "&" : "?";
        btn.setAttribute("hx-get", `${current}${sep}report_id=${encodeURIComponent(payload.report_id)}`);
      }
    });
    if (window.htmx) window.htmx.process(document.body);
  } catch (err) {
    console.warn("[lux] could not process report-saved event", err);
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

// HTML written into the popup window IMMEDIATELY (inside the click handler)
// so the user never sees an about:blank flash while waiting for /generate-blog
// to return the URL. Once the URL resolves, we navigate the popup. The themed
// loading shell here mirrors the real /blog-view loading state, so the swap
// is visually seamless.
function buildBlogPopupShell(theme) {
  const isDark = theme === "dark";
  const bg = isDark ? "#0f172a" : "#fafafa";
  const text = isDark ? "#e2e8f0" : "#1a1a2e";
  const textSecondary = isDark ? "#94a3b8" : "#64748b";
  const accent = isDark ? "#818cf8" : "#6366f1";
  return (
    '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">' +
    '<title>Composing your blog… — Nithu&#39;s PhD intern</title>' +
    '<meta name="viewport" content="width=device-width, initial-scale=1">' +
    '<style>' +
    '*{box-sizing:border-box;margin:0;padding:0}' +
    `body{background:${bg};color:${text};` +
    "font-family:Inter,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;" +
    'min-height:100vh;display:flex;flex-direction:column;align-items:center;' +
    'justify-content:center;text-align:center;padding:24px;line-height:1.6}' +
    `.brand{position:fixed;top:16px;left:24px;font-size:14px;font-weight:600;color:${textSecondary};letter-spacing:-0.01em}` +
    '.icon{position:relative;width:96px;height:96px;display:flex;align-items:center;justify-content:center;margin-bottom:24px}' +
    `.icon svg{width:64px;height:64px;color:${accent};animation:pulse 2.4s ease-in-out infinite}` +
    `.icon::before,.icon::after{content:"";position:absolute;inset:0;border-radius:50%;border:2px solid ${accent};opacity:0;animation:ring 2.4s ease-out infinite}` +
    '.icon::after{animation-delay:1.2s}' +
    `h1{font-size:clamp(22px,3.5vw,30px);font-weight:700;letter-spacing:-0.02em;color:${text};margin-bottom:12px}` +
    '.dots::after{content:"";display:inline-block;width:1em;text-align:left;animation:dots 1.6s steps(4,end) infinite}' +
    `p{font-size:15px;color:${textSecondary};max-width:420px}` +
    '@keyframes pulse{0%,100%{transform:scale(1);opacity:1}50%{transform:scale(1.06);opacity:0.85}}' +
    '@keyframes ring{0%{transform:scale(0.6);opacity:0.6}100%{transform:scale(1.7);opacity:0}}' +
    '@keyframes dots{0%{content:""}25%{content:"."}50%{content:".."}75%{content:"..."}100%{content:""}}' +
    '</style></head><body>' +
    '<div class="brand">Nithu&#39;s PhD intern</div>' +
    '<div class="icon" aria-hidden="true">' +
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">' +
    '<path d="M4 4h11l5 5v11a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1z" />' +
    '<path d="M14 4v5h5" /><path d="M8 13h8" /><path d="M8 17h5" />' +
    '</svg></div>' +
    '<h1>Writing your blog<span class="dots"></span></h1>' +
    '<p>Synthesizing the report and sketching diagrams. This usually takes 10–30 seconds.</p>' +
    '<noscript><p style="color:' + accent + ';margin-top:24px">Blog requires JavaScript. ' +
    'Please enable it and reload.</p></noscript>' +
    '</body></html>'
  );
}

// Top-level click router for `#report-blog-btn`. Opens the customization
// modal; the actual blog launch happens inside the modal's submit handler so
// `window.open()` runs synchronously from a real user gesture (otherwise
// browsers block the popup).
function handleBlogClick(ev) {
  const btn = ev.target.closest("#report-blog-btn");
  if (!btn) return;
  ev.preventDefault();
  const reportEl = document.getElementById("report-content");
  if (!reportEl) return;
  if (btn.disabled) return;
  openBlogCustomizeModal({
    onSubmit: (opts) => {
      launchBlogGeneration({ btn, reportEl, opts });
    },
  });
}

document.addEventListener("click", handleBlogClick);

// ---------------------------------------------------------------------------
// Blog customization modal
// ---------------------------------------------------------------------------
// The modal itself is defined in `static/js/blog-modal.js` so it can be shared
// with the blog-view page (which uses it for the Revise button). This module
// just delegates to `window.luxBlog.openCustomizeModal`.

function openBlogCustomizeModal(opts) {
  if (window.luxBlog && window.luxBlog.openCustomizeModal) {
    return window.luxBlog.openCustomizeModal(opts);
  }
  console.error("[lux] blog-modal.js not loaded — falling back to defaults");
  if (opts && typeof opts.onSubmit === "function") {
    opts.onSubmit({ audience: "technical", length: "medium", tone: "distill", extra: "" });
  }
}


// ---------------------------------------------------------------------------
// Launch a blog generation — opens the popup synchronously and posts the form.
// Called from inside a real user-gesture handler (modal submit / regenerate
// button) so window.open() isn't popup-blocked.
// ---------------------------------------------------------------------------
function launchBlogGeneration({ btn, reportEl, opts, endpoint = "/generate-blog", extraFields = {} }) {
  const originalHtml = btn ? btn.innerHTML : null;
  if (btn) {
    btn.disabled = true;
    btn.innerHTML =
      '<span class="lux-spinner small" style="vertical-align:middle;"></span> Writing…';
  }

  // Synchronous popup so the browser counts it as a user-initiated open.
  const blogWindow = window.open("about:blank", "_blank");
  if (blogWindow) {
    try {
      const theme = (function () {
        try { return localStorage.getItem("theme") === "dark" ? "dark" : "light"; }
        catch (_) { return "light"; }
      })();
      blogWindow.document.open();
      blogWindow.document.write(buildBlogPopupShell(theme));
      blogWindow.document.close();
    } catch (writeErr) {
      console.warn("[lux] could not seed popup loading shell", writeErr);
    }
  }

  const fd = new FormData();
  const cleaned = reportEl ? getCleanReportText(reportEl) : "";
  if (cleaned) fd.append("report_text", cleaned);
  if (opts) {
    if (opts.audience) fd.append("audience", opts.audience);
    if (opts.length)   fd.append("length",   opts.length);
    if (opts.tone)     fd.append("tone",     opts.tone);
    if (opts.extra)    fd.append("extra",    opts.extra);
  }
  for (const [k, v] of Object.entries(extraFields || {})) {
    if (v != null) fd.append(k, v);
  }

  fetch(endpoint, { method: "POST", body: fd })
    .then((r) => {
      if (!r.ok) throw new Error(`Server responded ${r.status}`);
      return r.json();
    })
    .then((data) => {
      if (data.url && blogWindow) {
        blogWindow.location.href = data.url;
      } else if (data.url) {
        window.open(data.url, "_blank");
      }
    })
    .catch((err) => {
      console.error("[lux] blog generation failed", err);
      if (blogWindow) blogWindow.close();
      // eslint-disable-next-line no-alert
      alert("Failed to generate blog post. Please try again.");
    })
    .finally(() => {
      if (btn) {
        btn.disabled = false;
        if (originalHtml != null) btn.innerHTML = originalHtml;
      }
    });
}

// Expose report-page-specific helpers. The modal itself is already registered
// on `window.luxBlog.openCustomizeModal` by blog-modal.js.
window.luxBlog = window.luxBlog || {};
window.luxBlog.launch = launchBlogGeneration;
window.luxBlog.buildPopupShell = buildBlogPopupShell;

// ---------------------------------------------------------------------------
// Notion export – POST report text, open Notion page in new tab
// ---------------------------------------------------------------------------
function handleNotionClick(ev) {
  const btn = ev.target.closest("#report-notion-btn");
  if (!btn) return;

  const reportEl = document.getElementById("report-content");
  if (!reportEl) return;

  btn.disabled = true;
  const origHtml = btn.innerHTML;
  btn.innerHTML =
    '<span class="lux-spinner small" style="vertical-align:middle;"></span> Exporting…';

  // Open window synchronously so browsers don't block it as a popup.
  const notionWindow = window.open("about:blank", "_blank");

  const cleaned = getCleanReportText(reportEl);
  const fd = new FormData();
  fd.append("report_text", cleaned);

  // Pull title from the first heading in the report, if any.
  const firstH = reportEl.querySelector("h1, h2, h3");
  fd.append("title", firstH ? firstH.textContent.trim() : "Lux Scholar Report");
  if (reportEl.dataset.reportId) fd.append("report_id", reportEl.dataset.reportId);

  // Include audience and model so Notion gets structured properties.
  const audSel = document.getElementById("aud-select");
  const modelSel = document.getElementById("model-select");
  if (audSel && audSel.value) fd.append("audience", audSel.value);
  if (modelSel && modelSel.value) fd.append("model", modelSel.value);

  fetch("/export-notion", { method: "POST", body: fd })
    .then(async (r) => {
      const contentType = r.headers.get("content-type") || "";
      const payload = contentType.includes("application/json")
        ? await r.json()
        : { detail: await r.text() };

      if (!r.ok) {
        throw new Error(payload.detail || `Server responded ${r.status}`);
      }
      return payload;
    })
    .then((data) => {
      if (data.url && notionWindow) {
        notionWindow.location.href = data.url;
        if (typeof window.showToast === "function") {
          window.showToast("Exported to Notion!");
        }
      } else if (data.url) {
        window.open(data.url, "_blank");
      }
    })
    .catch((err) => {
      console.error("[lux] notion export failed", err);
      if (notionWindow) notionWindow.close();
      // eslint-disable-next-line no-alert
      alert(err.message || "Failed to export to Notion. Check your NOTION_API_KEY in .env.");
    })
    .finally(() => {
      btn.disabled = false;
      btn.innerHTML = origHtml;
    });
}

document.addEventListener("click", handleNotionClick);

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
