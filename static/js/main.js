// main.js – ES module entrypoint for Office Hours frontend
// This file bootstraps split modules to keep the bundle manageable and ensure
// shared helpers are loaded site-wide.

// Ensure HTMX opens multiple SSE streams concurrently (chat + report, etc.)
// Script is idempotent; importing here avoids separate <script> tag.
import "./htmx-sse-pool.js";

// Disable HTMX inline-eval to comply with strict CSP (avoids unsafe-eval errors)
if (window.htmx) {
  window.htmx.config.allowEval = false;
}

// Shared utilities (dbg, debounce, etc.) – side-effect free
import "./util.js";
import "./pagination.js";

// Chat subsystem (streams, UI, events)
// Cache-bust: v2 forces browser to reload these modules
import "./chat/stream.js?v=2";
import "./chat/ui.js?v=2";
import "./chat/events.js?v=2";
import "./chat/snippet.js?v=1";

// Markdown helpers – only pull the symbol we need (module still executes once)
import { ensureMarkdownLibs } from "./chat/markdown.js?v=2";
// Kick off background load early; no await needed.
ensureMarkdownLibs();

// Shared blog customization modal (used by report.js + blog-view.js).
// Imported before report.js so window.ohBlog.openCustomizeModal is registered
// by the time the Blog button click handler attaches.
import "./blog-modal.js?v=1";

// Report page helpers (copy button, etc.)
import "./report.js?v=2";

// ---------------------------------------------------------------------------
// Simple-reload snapshot & restore
//   • On normal reload (Cmd-R / F5) we capture scrollY + open chat modal pid
//     into sessionStorage so the user lands exactly where they were.
//   • Shift-Reload (hard refresh) sets a flag so nothing is restored and the
//     server chat state is purged via /chat/clear_session beacon (handled in
//     chat/events.js).
// ---------------------------------------------------------------------------

(function () {
  const STATE_KEY = "ohViewState";
  const HARD_FLAG = "ohHardReload";

  /** Restore view state on *simple* reload. */
  function restoreViewState() {
    const navEntry = performance.getEntriesByType("navigation")[0];
    const navType = navEntry && navEntry.type ? navEntry.type : "navigate";

    const hardReload = sessionStorage.getItem(HARD_FLAG) === "1";
    if (hardReload) sessionStorage.removeItem(HARD_FLAG);

    // Only restore when navigation is a plain reload AND not flagged as hard.
    if (navType !== "reload" || hardReload) {
      // Hard cases: let chat/events.js purge session; no restore.
      sessionStorage.removeItem(STATE_KEY);
      return;
    }

    const raw = sessionStorage.getItem(STATE_KEY);
    if (!raw) return;
    sessionStorage.removeItem(STATE_KEY); // one-shot snapshot
    let parsed;
    try {
      parsed = JSON.parse(raw);
    } catch (_) {
      return;
    }

    const { scrollY = 0, openPid = null } = parsed;

    // Re-open chat modal if it was active.
    if (openPid && window.htmx) {
      // #modal-container is always on the base template.
      window.htmx.ajax("GET", `/chat-modal?paper_id=${encodeURIComponent(openPid)}`, "#modal-container");
    }

    // Restore scroll *after* first paint.
    requestAnimationFrame(() => window.scrollTo(0, scrollY));
  }

  /** Save view state just before a *simple* reload exits. */
  function saveViewState(ev) {
    // Treat Shift key as intent for hard refresh – skip snapshot and mark.
    if (ev.shiftKey) {
      sessionStorage.setItem(HARD_FLAG, "1");
      return;
    }

    const viewerOpen = !!document.querySelector(".modal-content.pdf-viewer");
    if (viewerOpen) return; // treat like hard reload

    const modal = document.querySelector(".modal .chat-log-container[data-chat-pid]");
    const pid = modal ? modal.dataset.chatPid : null;

    const state = {
      scrollY: window.scrollY,
      openPid: pid,
    };
    try {
      sessionStorage.setItem(STATE_KEY, JSON.stringify(state));
    } catch (_) {
      /* storage full or disabled – ignore */
    }
  }

  window.addEventListener("beforeunload", saveViewState, { capture: true });
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", restoreViewState, { once: true });
  } else {
    restoreViewState();
  }
})();

window.__OH_MODULES_ACTIVE = true;
console.debug("[oh] main.js loaded (ESM)");

// ---------------------------------------------------------------------------
// Clear ?search_query param from URL after results render (was inline <script>)
// ---------------------------------------------------------------------------

document.body.addEventListener("htmx:afterSwap", (evt) => {
  const tgt = evt.target;
  if (tgt && tgt.id === "papers-list") {
    try {
      // Replace history without reloading
      history.replaceState(null, "", "/");
    } catch (_) {
      // noop
    }
  }
});

console.debug("[oh] main.js history clean listener wired");

// ---------------------------------------------------------------------------
// Sort by date & filter by author (client-side, on search results)
// ---------------------------------------------------------------------------

(function () {
  let sortDesc = true; // newest first by default

  function getPapersList() {
    return document.getElementById("papers-list");
  }

  function reinitPagination() {
    // Re-trigger pagination after sort/filter so page slicing matches new order
    if (typeof window.initPagination === "function") {
      window.initPagination();
    } else {
      // Fallback: show all items if pagination module not available
      const ul = getPapersList();
      if (ul) ul.querySelectorAll("li").forEach((li) => (li.style.display = ""));
    }
  }

  function handleSortDate() {
    const ul = getPapersList();
    if (!ul) return;
    // Clear any author filter first
    const filterInput = document.getElementById("author-filter-input");
    if (filterInput) filterInput.value = "";
    ul.querySelectorAll("li").forEach((li) => (li.style.display = ""));

    const items = Array.from(ul.querySelectorAll("li"));
    items.sort((a, b) => {
      const da = a.dataset.date || "";
      const db = b.dataset.date || "";
      return sortDesc ? db.localeCompare(da) : da.localeCompare(db);
    });
    items.forEach((li) => ul.appendChild(li));
    sortDesc = !sortDesc;
    const btn = document.getElementById("sort-date-btn");
    if (btn) btn.innerHTML = sortDesc
      ? '<i class="fa-solid fa-arrow-down-short-wide"></i> Date ↓'
      : '<i class="fa-solid fa-arrow-up-short-wide"></i> Date ↑';
    reinitPagination();
  }

  function handleAuthorFilter(ev) {
    const query = (ev.target.value || "").toLowerCase().trim();
    const ul = getPapersList();
    if (!ul) return;
    ul.querySelectorAll("li").forEach((li) => {
      if (!query) {
        li.style.display = "";
        return;
      }
      const authors = li.dataset.authors || "";
      li.style.display = authors.includes(query) ? "" : "none";
    });
  }

  document.addEventListener("click", (ev) => {
    if (ev.target.closest("#sort-date-btn")) handleSortDate();
  });

  document.addEventListener("input", (ev) => {
    if (ev.target.id === "author-filter-input") handleAuthorFilter(ev);
  });
})();

// ---------------------------------------------------------------------------
// Select All / Deselect All paper checkboxes
// ---------------------------------------------------------------------------
(function () {
  document.addEventListener("click", (ev) => {
    if (ev.target.closest("#select-all-btn")) {
      document
        .querySelectorAll(".paper-select-checkbox:not(.pinned-checkbox)")
        .forEach((cb) => (cb.checked = true));
    }
    if (ev.target.closest("#deselect-all-btn")) {
      document
        .querySelectorAll(".paper-select-checkbox:not(.pinned-checkbox)")
        .forEach((cb) => (cb.checked = false));
    }
  });
})();

// ---------------------------------------------------------------------------
// Abstract preview popover
// ---------------------------------------------------------------------------
(function () {
  // Lazy-inject the popover dialog once
  function ensureDialog() {
    let dlg = document.getElementById("abstract-popover");
    if (dlg) return dlg;
    document.body.insertAdjacentHTML(
      "beforeend",
      `<dialog id="abstract-popover" class="oh-modal" style="max-width:540px;max-height:70vh;overflow-y:auto;">
         <div id="abstract-popover-body" style="white-space:pre-wrap;font-size:0.9rem;line-height:1.5;"></div>
         <menu style="margin-top:12px;display:flex;justify-content:flex-end;">
           <button value="cancel" style="font-size:0.85rem;">Close</button>
         </menu>
       </dialog>`
    );
    dlg = document.getElementById("abstract-popover");
    dlg.addEventListener("click", (e) => {
      // Close on backdrop click
      if (e.target === dlg) dlg.close();
    });
    return dlg;
  }

  document.addEventListener("click", (ev) => {
    const btn = ev.target.closest(".abstract-toggle-btn");
    if (!btn) return;
    const abstract = btn.dataset.abstract;
    if (!abstract) return;
    const dlg = ensureDialog();
    // Get paper title from sibling <a> tag
    const li = btn.closest("li");
    const titleEl = li ? li.querySelector("a") : null;
    const title = titleEl ? titleEl.textContent.trim() : "Abstract";
    dlg.querySelector("#abstract-popover-body").innerHTML =
      `<strong>${title.replace(/</g, "&lt;")}</strong><hr style="margin:8px 0;">` +
      abstract.replace(/</g, "&lt;").replace(/>/g, "&gt;");
    dlg.showModal();
  });
})();

// ---------------------------------------------------------------------------
// BibTeX citation copy
// ---------------------------------------------------------------------------
(function () {
  function generateBibtex(btn) {
    const title = btn.dataset.bibTitle || "Untitled";
    const authors = btn.dataset.bibAuthors || "Unknown";
    const year = btn.dataset.bibYear || "n.d.";
    const url = btn.dataset.bibUrl || "";

    // Generate a citekey from first author surname + year
    const firstAuthor = authors.split(";")[0].trim().split(" ").pop() || "unknown";
    const citekey = firstAuthor.toLowerCase().replace(/[^a-z]/g, "") + year;

    return `@article{${citekey},
  title = {${title}},
  author = {${authors}},
  year = {${year}},
  url = {${url}}
}`;
  }

  function copyToClipboard(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(text);
    }
    // Fallback for non-HTTPS contexts
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.cssText = "position:fixed;left:-9999px;";
    document.body.appendChild(ta);
    ta.select();
    document.execCommand("copy");
    document.body.removeChild(ta);
    return Promise.resolve();
  }

  document.addEventListener("click", (ev) => {
    const btn = ev.target.closest(".bibtex-copy-btn");
    if (!btn) return;
    const bibtex = generateBibtex(btn);
    copyToClipboard(bibtex).then(() => {
      btn.style.opacity = "1";
      btn.title = "Copied!";
      const icon = btn.querySelector("i");
      if (icon) {
        icon.className = "fa-solid fa-check";
        setTimeout(() => { icon.className = "fa-solid fa-quote-right"; }, 1500);
      }
      if (window.showToast) window.showToast("BibTeX copied to clipboard");
      setTimeout(() => {
        btn.style.opacity = "0.6";
        btn.title = "Copy BibTeX citation";
      }, 1500);
    });
  });
})();

