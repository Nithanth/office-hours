/**
 * "More Like This" — inline similar paper recommendations.
 *
 * Handles the ✦ button click on paper cards: fetches similar papers
 * from /api/similar, displays them in a collapsible panel below the
 * anchor paper, and supports toggle (click again to collapse).
 */

(function () {
  "use strict";

  // Track which papers have an active similar panel (to avoid duplicate fetches)
  const _activeRequests = new Set();

  /**
   * Toggle the similar-papers panel for a given paper card.
   */
  async function toggleSimilar(btn) {
    const paperUrl = btn.getAttribute("data-paper-url");
    const paperTitle = btn.getAttribute("data-paper-title");
    if (!paperUrl) return;

    // Find the container within the same <li> parent
    const li = btn.closest("li");
    if (!li) return;
    const container = li.querySelector(".similar-container");
    if (!container) return;

    // Toggle: if already visible, collapse it
    if (container.style.display !== "none" && container.innerHTML) {
      container.style.display = "none";
      btn.style.opacity = "0.6";
      btn.classList.remove("active");
      return;
    }

    // Prevent double-fetch
    const key = paperUrl;
    if (_activeRequests.has(key)) return;
    _activeRequests.add(key);

    // Loading state
    btn.style.opacity = "1";
    btn.classList.add("active");
    const origHTML = btn.innerHTML;
    btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i>';
    btn.disabled = true;

    try {
      const formData = new FormData();
      formData.append("paper_url", paperUrl);
      formData.append("paper_title", paperTitle || "");

      const resp = await fetch("/api/similar", {
        method: "POST",
        body: formData,
      });

      const html = await resp.text();
      container.innerHTML = html;
      container.style.display = "block";

      // Animate in
      container.style.opacity = "0";
      container.style.transition = "opacity 0.25s ease";
      requestAnimationFrame(() => {
        container.style.opacity = "1";
      });
    } catch (err) {
      container.innerHTML =
        '<div style="padding:8px;color:var(--fg-muted,#888);font-size:0.85rem;">' +
        "Failed to load similar papers.</div>";
      container.style.display = "block";
    } finally {
      btn.innerHTML = origHTML;
      btn.disabled = false;
      _activeRequests.delete(key);
    }
  }

  // Event delegation — works with HTMX-loaded content
  document.addEventListener("click", function (e) {
    const btn = e.target.closest(".similar-btn");
    if (btn) {
      e.preventDefault();
      e.stopPropagation();
      toggleSimilar(btn);
    }
  });
})();
