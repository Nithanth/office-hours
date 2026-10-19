// ---------------------------------------------------------------------------
// Library: save / unsave papers + saved-state markers.
// Delegated listeners work for htmx-injected content (modals, swapped lists).
// ---------------------------------------------------------------------------
(function () {
  let savedIds = null; // Set<string> once fetched

  function markSaved(root) {
    const scope = root || document;
    scope.querySelectorAll(".save-paper-btn").forEach((btn) => {
      const saved = savedIds && savedIds.has(btn.dataset.paperId);
      const icon = btn.querySelector("i");
      if (icon) {
        icon.className = saved
          ? "fa-solid fa-bookmark"
          : "fa-regular fa-bookmark";
      }
      btn.style.opacity = saved ? "1" : "0.6";
      btn.title = saved ? "Saved to library. Click to remove." : "Save to library";
    });
  }

  async function fetchSavedIds() {
    try {
      const r = await fetch("/api/library/ids");
      const j = await r.json();
      savedIds = new Set(j.ids || []);
    } catch {
      savedIds = new Set();
    }
    markSaved();
  }

  document.addEventListener("DOMContentLoaded", fetchSavedIds);
  if (document.body) {
    document.body.addEventListener("htmx:afterSwap", (ev) => markSaved(ev.target));
  }

  document.addEventListener("click", async (ev) => {
    const btn = ev.target.closest(".save-paper-btn");
    if (!btn) return;
    const paperId = btn.dataset.paperId;
    if (!paperId) return;
    const isSaved = savedIds && savedIds.has(paperId);
    const endpoint = isSaved ? "/api/library/unsave" : "/api/library/save";
    const body = { paper_id: paperId };
    if (!isSaved) {
      body.title = btn.dataset.paperTitle || paperId;
      body.url = btn.dataset.paperUrl || "";
      body.authors = btn.dataset.paperAuthors || "";
    }
    try {
      const r = await fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams(body).toString(),
      });
      const j = await r.json();
      if (j.ok && savedIds) {
        if (isSaved) savedIds.delete(paperId);
        else savedIds.add(paperId);
        markSaved();
        if (window.showToast) {
          window.showToast(isSaved ? "Removed from library" : "Saved to library");
        }
      }
    } catch {
      /* offline — leave button as-is */
    }
  });
})();
