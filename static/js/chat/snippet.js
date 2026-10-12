// chat/snippet.js – selection-to-chat inside the two-pane document viewer.
// Select text in the PDF (pdf.js text layer) or the proxied HTML iframe, then
// press Cmd/Ctrl+L or the floating "ask" button to quote it into the chat input.

const _SNIPPET_MAX_CHARS = 1500;
let _fab = null; // floating "ask about this" button

function _viewerModal() {
  const content = document.querySelector(".modal .modal-content.pdf-viewer");
  return content ? content.closest(".modal") : null;
}

function _docFrame() {
  return document.getElementById("doc-frame");
}

// Read the current selection from the document pane: main document (PDF text
// layer) or the same-origin HTML iframe. Returns { text, rect }.
function _readDocSelection() {
  const sel = window.getSelection();
  let text = "";
  let rect = null;

  if (sel && sel.rangeCount > 0) {
    const t = sel.toString().trim();
    if (t && sel.anchorNode && sel.anchorNode.parentElement?.closest(".modal-content.pdf-viewer, .pdf-container")) {
      text = t;
      rect = sel.getRangeAt(0).getBoundingClientRect();
    }
  }

  const frame = _docFrame();
  if (!text && frame && frame.contentWindow) {
    try {
      const fsel = frame.contentWindow.getSelection();
      const t = (fsel?.toString() || "").trim();
      if (t) {
        text = t;
        const r = fsel.getRangeAt(0).getBoundingClientRect();
        const fr = frame.getBoundingClientRect();
        rect = {
          top: r.top + fr.top,
          left: r.left + fr.left,
          right: r.right + fr.left,
          bottom: r.bottom + fr.top,
        };
      }
    } catch (_) {
      /* cross-origin frame – unreachable via /html-proxy but stay safe */
    }
  }
  return { text: text.slice(0, _SNIPPET_MAX_CHARS), rect };
}

function _ensureFab() {
  if (_fab) return _fab;
  _fab = document.createElement("button");
  _fab.type = "button";
  _fab.className = "snippet-fab";
  _fab.textContent = "→ ask about this";
  _fab.style.cssText =
    "position:fixed;z-index:1200;display:none;padding:5px 10px;" +
    "font-family:var(--mono,monospace);font-size:12px;cursor:pointer;" +
    "background:var(--oh-accent,#0d6e63);color:#fff;border:1px solid #222;" +
    "border-radius:4px;box-shadow:2px 2px 0 #222;";
  _fab.addEventListener("mousedown", (e) => e.preventDefault()); // keep selection
  _fab.addEventListener("click", () => {
    const modal = _viewerModal();
    const { text } = _readDocSelection();
    if (modal && text) _insertSnippet(modal, text);
    _hideFab();
  });
  document.body.appendChild(_fab);
  return _fab;
}

function _showFab(rect) {
  const fab = _ensureFab();
  fab.style.left = `${Math.min(rect.right, window.innerWidth - 140)}px`;
  fab.style.top = `${Math.max(rect.bottom + 6, 8)}px`;
  fab.style.display = "block";
}

function _hideFab() {
  if (_fab) _fab.style.display = "none";
}

// Insert the snippet as a markdown-style quote at the top of the chat textarea.
function _insertSnippet(modal, text) {
  const ta = modal.querySelector("textarea[name='message']");
  if (!ta) return;
  const quoted = text
    .split("\n")
    .map((l) => (l.trim() ? `> ${l.trim()}` : ">"))
    .join("\n");
  if (!ta.value.includes(quoted)) {
    const rest = ta.value.trim();
    ta.value = `${quoted}\n\n${rest}`.replace(/\s+$/, "\n\n");
  }
  ta.focus();
  ta.setSelectionRange(ta.value.length, ta.value.length);
  // nudge the autosize listener if present
  ta.dispatchEvent(new Event("input", { bubbles: true }));
}

// Cmd/Ctrl+L while a document viewer is open: quote selection into chat.
document.addEventListener("keydown", (e) => {
  if (!(e.metaKey || e.ctrlKey) || e.shiftKey || e.altKey) return;
  if ((e.key || "").toLowerCase() !== "l") return;
  const modal = _viewerModal();
  if (!modal) return;
  const { text } = _readDocSelection();
  if (!text) return;
  e.preventDefault();
  _insertSnippet(modal, text);
  _hideFab();
});

// Floating button after a selection settles in the document pane.
let _selTimer = null;
function _onMaybeSelection() {
  clearTimeout(_selTimer);
  _selTimer = setTimeout(() => {
    if (!_viewerModal()) {
      _hideFab();
      return;
    }
    const { text, rect } = _readDocSelection();
    if (text && rect) _showFab(rect);
    else _hideFab();
  }, 150);
}
document.addEventListener("selectionchange", _onMaybeSelection);
document.addEventListener("mouseup", _onMaybeSelection);

// Attach the same listeners inside the (same-origin) HTML iframe once it loads.
function _hookFrame() {
  const frame = _docFrame();
  if (!frame || frame.dataset.snippetHooked) return;
  frame.dataset.snippetHooked = "1";
  try {
    frame.addEventListener("load", () => {
      try {
        frame.contentDocument.addEventListener("selectionchange", _onMaybeSelection);
        frame.contentDocument.addEventListener("mouseup", _onMaybeSelection);
      } catch (_) {
        /* ignore */
      }
    });
  } catch (_) {
    /* ignore */
  }
}
_hookFrame();
document.body.addEventListener("htmx:afterSwap", _hookFrame);

// Quote-only submits ("just explain this passage") get a default instruction.
document.body.addEventListener("htmx:beforeRequest", (evt) => {
  const form = evt.target;
  if (!(form instanceof HTMLFormElement)) return;
  if (form.getAttribute("hx-post") !== "/chat") return;
  const ta = form.querySelector("textarea[name='message']");
  if (!ta) return;
  const v = ta.value.trim();
  if (!v.startsWith(">")) return;
  const hasQuestion = v
    .split("\n")
    .some((l) => l.trim() && !l.trim().startsWith(">"));
  if (!hasQuestion) ta.value = `${v}\nExplain this passage.`;
});

// Clean up when the viewer modal closes.
document.addEventListener("click", (e) => {
  if (e.target.closest && e.target.closest(".modal-close-btn")) _hideFab();
});

console.debug("[oh] chat/snippet.js loaded");
