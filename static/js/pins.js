/* pins.js – manage user-pinned papers per query.
   State lives in sessionStorage so it survives soft reloads but resets when
   the user leaves the page (consistent with chat memory rules).
*/

const QUERY_KEY_INPUT = "#original_query_for_report_hidden";
const STORAGE_PREFIX = "luxPins:";
// Hard limit: main paper + up to 9 extras
const MAX_PINS = 10;

function storageKey() {
  const q = document.querySelector(QUERY_KEY_INPUT)?.value || "";
  return STORAGE_PREFIX + q.trim().toLowerCase();
}

function loadSet() {
  try {
    return new Set(JSON.parse(sessionStorage.getItem(storageKey()) || "[]"));
  } catch (_) {
    return new Set();
  }
}

function saveSet(set) {
  try {
    sessionStorage.setItem(storageKey(), JSON.stringify([...set]));
  } catch (_) {
    /* quota exceeded – ignore */
  }
}

function notify(msg) {
  if (typeof window.showToast === "function") window.showToast(msg);
  else console.warn("[lux]", msg);
}

/* ------------------------------------------------------------------------- */
// Public helpers (exported)
/* ------------------------------------------------------------------------- */

export function togglePin(li) {
  if (!li) return;
  const id = li.dataset.paperId;
  if (!id) return;

  // Prevent user from unpinning the main/input paper (checkbox has .pinned-checkbox)
  if (li.querySelector(".pinned-checkbox")) {
    notify("The main paper is always pinned and counted.");
    return;
  }

  const set = loadSet();
  const btn = li.querySelector(".pin-btn");
  const checkbox = li.querySelector(".paper-select-checkbox");
  let pinned;
  if (set.has(id)) {
    set.delete(id);
    pinned = false;
  } else {
    if (set.size >= MAX_PINS) {
      notify(`You can only pin up to ${MAX_PINS} papers.`);
      return;
    }
    set.add(id);
    pinned = true;
  }
  if (btn) {
    btn.textContent = pinned ? "📌" : "📍";
    btn.setAttribute("aria-label", pinned ? "Un-pin paper" : "Pin paper");
    btn.setAttribute("title", pinned ? "Un-pin paper" : "Pin paper");
  }

  // Auto-include pinned papers in report selection checkbox
  if (checkbox) checkbox.checked = pinned;

  saveSet(set);
  reorder();
}

export function reorder() {
  const list = document.querySelector("#papers-list");
  if (!list) return;

  const set = loadSet();

  // Ensure query's main paper (if present) is always in the set
  const mainCb = list.querySelector("input.pinned-checkbox");
  if (mainCb) {
    const mainLi = mainCb.closest("li");
    if (mainLi && mainLi.dataset.paperId) {
      set.add(mainLi.dataset.paperId);
    }
  }

  // Persist any automatic additions
  saveSet(set);

  // Move pinned items to the top, preserving their DOM order.
  [...list.children].forEach((li) => {
    if (set.has(li.dataset.paperId)) list.prepend(li);
  });

  // If the user reduced list length, prune excess pins
  if (set.size > list.children.length) {
    const keep = new Set(
      [...list.children].map((li) => li.dataset.paperId).filter((id) => set.has(id))
    );
    saveSet(keep);
  }

  // Update visual state for non-DOM pins (e.g., after increase)
  [...list.children].forEach((li) => {
    const isPinned = set.has(li.dataset.paperId);
    const btn = li.querySelector(".pin-btn");
    if (btn) {
      btn.textContent = isPinned ? "📌" : "📍";
      btn.setAttribute("aria-label", isPinned ? "Un-pin paper" : "Pin paper");
      btn.setAttribute("title", isPinned ? "Un-pin paper" : "Pin paper");
    }
    // Keep checkbox state in sync during reorder
    const cb = li.querySelector(".paper-select-checkbox");
    if (cb) cb.checked = isPinned;
  });
}

/* ------------------------------------------------------------------------- */
// Wiring
/* ------------------------------------------------------------------------- */

document.body.addEventListener("click", (e) => {
  const btn = e.target.closest(".pin-btn");
  if (!btn) return;
  e.preventDefault();
  const li = btn.closest("li");
  togglePin(li);
});

// When HTMX swaps in a new papers list (shuffle/reset/url search etc.)
// Run afterSwap to restore pin order + styles.

document.body.addEventListener("htmx:afterSwap", (e) => {
  if (e.target.id === "papers-list") reorder();
});

// Clear pins when a completely new search request starts.
document.body.addEventListener("htmx:beforeRequest", (e) => {
  const path = e.detail?.requestConfig?.path || "";
  if (path.startsWith("/search") || path.startsWith("/reset_results")) {
    sessionStorage.removeItem(storageKey());
  }
});

// Initial pass (soft reload) – wait for content then apply.
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", reorder, { once: true });
} else {
  reorder();
}
