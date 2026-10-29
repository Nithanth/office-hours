"""Shared reading-status select — used by the library list and topic pages.

The change handler posts to /api/library/status, which upserts the status
(and claims the paper into the library when it wasn't saved). One per page:
emit ``status_change_script`` once alongside the selects.
"""

from __future__ import annotations

import html

STATUS_LABELS = {
    "to_read": "To read",
    "reading": "Reading",
    "done": "Done",
}


def status_options(current: str, paper_id: str) -> str:
    opts = []
    for value, label in STATUS_LABELS.items():
        sel = " selected" if value == current else ""
        opts.append(f'<option value="{value}"{sel}>{label}</option>')
    return (
        f'<select class="lib-status-select" data-paper-id="{html.escape(paper_id, quote=True)}">'
        + "".join(opts)
        + "</select>"
    )


STATUS_CHANGE_SCRIPT = """<script>
(function() {
  if (window.__ohStatusBound) return;
  window.__ohStatusBound = true;
  document.addEventListener("change", function(ev) {
    const sel = ev.target.closest(".lib-status-select");
    if (!sel) return;
    fetch("/api/library/status", {
      method: "POST",
      headers: {"Content-Type": "application/x-www-form-urlencoded"},
      body: new URLSearchParams({paper_id: sel.dataset.paperId, status: sel.value}).toString(),
    }).then(() => location.reload());
  });
})();
</script>"""
