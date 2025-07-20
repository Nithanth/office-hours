// main.js – ES module entrypoint for Lux Scholar frontend
// This file bootstraps split modules to keep the bundle manageable and ensure
// shared helpers are loaded site-wide.

// Controls for shuffle/reset buttons (defines window.disableShuffle etc.)
import "./shuffle_controls.js";
// Pin feature removed – module import dropped
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
import "./chat/stream.js";
import "./chat/ui.js";
import "./chat/events.js";
// import "./citation.js"; // hover cards disabled per user preference

// Markdown helpers – only pull the symbol we need (module still executes once)
import { ensureMarkdownLibs } from "./chat/markdown.js";
// Kick off background load early; no await needed.
ensureMarkdownLibs();

// Report page helpers (copy button, etc.)
import "./report.js";

// ---------------------------------------------------------------------------
// Simple-reload snapshot & restore
//   • On normal reload (Cmd-R / F5) we capture scrollY + open chat modal pid
//     into sessionStorage so the user lands exactly where they were.
//   • Shift-Reload (hard refresh) sets a flag so nothing is restored and the
//     server chat state is purged via /chat/clear_session beacon (handled in
//     chat/events.js).
// ---------------------------------------------------------------------------

(function () {
  const STATE_KEY = "luxViewState";
  const HARD_FLAG = "luxHardReload";

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

window.__LUX_MODULES_ACTIVE = true;
console.debug("[lux] main.js loaded (ESM)");

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

console.debug("[lux] main.js history clean listener wired");
