// chat/ui.js – modal UI helpers & utilities for chat subsystem

import { dbg } from "../util.js?v=2";
import { finalizeBubble, saveTranscript, pendingBuffers as _pendingBuffers } from "./stream.js?v=2";

// Internal cache so we don't create duplicate stop buttons.
const _stopBtnSym = Symbol("stopBtnSym");

/**
 * Locate key chat elements relative to a node inside a modal dialog.
 * Returns {} if modal not found so callers can short-circuit.
 */
export function getModalParts(el) {
  const modal = el?.closest && el.closest(".modal");
  if (!modal) return {};
  const form = modal.querySelector("form[hx-post='/chat']");
  const textarea = form?.querySelector("textarea[name='message']");
  const submitBtn = form?.querySelector("button[type='submit'],input[type='submit']");

  // Skip stop button for PDF viewer layout – it overlaps UI.
  const isPdfViewer = !!modal.querySelector(".modal-content.pdf-viewer");

  let stopBtn = null;
  if (!isPdfViewer) {
    stopBtn = modal[_stopBtnSym];
    if (!stopBtn) {
      stopBtn = document.createElement("button");
      stopBtn.type = "button";
      stopBtn.textContent = "Stop";
      stopBtn.className = "stop-btn text-sm px-2 py-1 rounded bg-red-600 text-white";
      stopBtn.addEventListener("click", () => {
        const activeStream = modal.querySelector("[sse-connect]");
        if (activeStream) {
          activeStream.dataset.interrupted = "1";
          activeStream.classList.add("interrupted");
          const src = activeStream.__eventSource;
          if (src && typeof src.close === "function") src.close();
        }
        stopBtn.classList.add("hidden");
      });
      modal.appendChild(stopBtn);
      modal[_stopBtnSym] = stopBtn;
    }
  }

  return { modal, form, textarea, submitBtn, stopBtn };
}

/**
 * Enable / disable textarea and submit / stop controls in a modal.
 */
export function setControlsEnabled(modal, enabled) {
  if (!modal) return;
  const textarea = modal.querySelector("textarea[name='message']");
  const submitBtn = modal.querySelector("button[type='submit'],input[type='submit']");
  if (textarea) {
    textarea.disabled = !enabled;
    if (enabled) textarea.removeAttribute("disabled");
    else textarea.setAttribute("disabled", "true");
  }
  if (submitBtn) {
    submitBtn.disabled = !enabled;
    if (enabled) submitBtn.removeAttribute("disabled");
    else submitBtn.setAttribute("disabled", "true");
  }
  const { stopBtn } = getModalParts(modal);
  if (stopBtn) {
    if (enabled) stopBtn.classList.add("hidden");
    else stopBtn.classList.remove("hidden");
  }

  dbg("controls", { enabled, modal });
}

/**
 * Adds clipboard copy buttons to <pre><code> blocks.
 * Extracted from legacy app.js so other modules can call after content render.
 */
export function addCopyButtons(container) {
  container.querySelectorAll("pre").forEach((pre) => {
    if (pre.querySelector(".copy-btn")) return; // already added
    const btn = document.createElement("button");
    btn.className = "copy-btn hidden";
    btn.type = "button";
    btn.title = "Copy code";
    btn.setAttribute("aria-label", "Copy code block");
    btn.textContent = "📋";
    btn.addEventListener("click", () => {
      navigator.clipboard.writeText(pre.innerText).then(() => {
        btn.textContent = "✔";
        setTimeout(() => (btn.textContent = "📋"), 1500);
      });
    });
    pre.appendChild(btn);
    pre.addEventListener("mouseenter", () => btn.classList.remove("hidden"));
    pre.addEventListener("mouseleave", () => btn.classList.add("hidden"));
  });
}


// ---------------------------------------------------------------------------
// Scroll helper – force container bottom after layout paint.
// ---------------------------------------------------------------------------
export function scrollToBottom(container, delay = 0) {
  if (!(container instanceof HTMLElement)) return;
  const doScroll = () => {
    container.scrollTop = container.scrollHeight;
  };
  if (delay > 0) setTimeout(doScroll, delay);
  else requestAnimationFrame(() => requestAnimationFrame(doScroll));
}

// ---------------------------------------------------------------------------
// Autosize textarea helper (max 240px height similar to legacy).
// ---------------------------------------------------------------------------
function attachAutoResize(ta) {
  const resize = () => {
    ta.style.height = "auto";
    ta.style.height = Math.min(ta.scrollHeight, 240) + "px";
  };
  ta.addEventListener("input", resize);
  resize();
}

// ---------------------------------------------------------------------------
// Enhance modal on insertion (autosize, scroll bottom, extras).
// ---------------------------------------------------------------------------
export function enhanceModal(modal) {
  if (!modal) return;
  const ta = modal.querySelector("textarea[name='message']");
  if (ta && !ta.dataset.autosize) {
    ta.dataset.autosize = "1";
    attachAutoResize(ta);
  }
  const log = modal.querySelector(".chat-log-container");
  if (log) {
    // Sanitize any orphan nodes in case a previous close missed cleanUp.
    const zap = [];
    const msgs = Array.from(log.children || []);
    for (let i = 0; i < msgs.length; i++) {
      const el = msgs[i];
      const isAssistant = el.classList.contains("assistant-message");
      const inFlight = el.hasAttribute("sse-connect"); // still streaming

      if (
        isAssistant &&
        !inFlight && // <-- KEEP active stream bubble
        (
          el.dataset.finalised !== "1" ||
          el.dataset.interrupted === "1" ||
          el.classList.contains("interrupted") ||
          el.classList.contains("typing")
        )
      ) {
        zap.push(el);
        const prev = el.previousElementSibling;
        if (prev && prev.classList.contains("user-message")) zap.push(prev);
      }
    }
    // Remove a trailing user prompt without assistant reply.
    const last = log.lastElementChild;
    if (last && last.classList.contains("user-message")) zap.push(last);
    zap.forEach((el) => el.remove());
    if (zap.length) console.debug('[oh] sanitizeLog removed', zap.length, 'stale nodes');

    // Always ensure controls are enabled after sanitation
    const ta = modal.querySelector("textarea[name='prompt']");
    const submitBtn = modal.querySelector("button[type='submit']");
    if (ta) ta.disabled = false;
    if (submitBtn) submitBtn.disabled = false;
    setControlsEnabled(modal, true);

    scrollToBottom(log);
  }

  // Attach one-time close handler to clean up in-flight streams
  if (!modal.dataset.closeHook) {
    modal.dataset.closeHook = "1";
    const cleanUp = () => {
      const hidden = modal.querySelector("#hidden-paper-id");

      // If log container is already removed (edge-case race on modal close),
      // exit early to avoid null deref errors.
      if (!log) return;

      // ------------------------------------------------------------------
      // 1. Find any unfinished assistant bubble (streaming, typing, or not yet
      //    marked finalised) and remove it along with the paired user prompt.
      // ------------------------------------------------------------------
      const toZap = [];
      const unfinishedSel = ".assistant-message.typing, .assistant-message.interrupted, .assistant-message:not([data-finalised='1'])";
      log.querySelectorAll(unfinishedSel).forEach((el) => {
        toZap.push(el);
        const prev = el.previousElementSibling;
        if (prev && prev.classList.contains("user-message")) toZap.push(prev);
      });
      toZap.forEach((el) => el.remove());
      // Also drop any SSE token buffer bookkeeping attached to removed bubbles.
      toZap.forEach((el) => {
        const entry = _pendingBuffers.get(el);
        if (entry?.typingTimer) clearInterval(entry.typingTimer);
        _pendingBuffers.delete(el);
      });
      if (toZap.length) console.debug('[oh] cleanUp removed', toZap.length, 'unfinished nodes');

      // After zapping unfinished assistants, remove ANY user-message that is not
      // immediately followed by an assistant-message. Handles cases where the
      // assistant bubble was already removed earlier (connection error etc.)
      const nodes = Array.from(log.children || []);
      nodes.forEach((el, idx) => {
        if (el.classList.contains("user-message")) {
          const nxt = nodes[idx + 1];
          if (!nxt || !nxt.classList.contains("assistant-message")) el.remove();
        }
      });

      // ------------------------------------------------------------------
      // 2. Always notify backend to reset stream_active so subsequent /chat
      //    calls are accepted, even if we didn't detect an activeStream.
      // ------------------------------------------------------------------
      if (hidden && hidden.value) {
        fetch("/chat/abort", {
          method: "POST",
          headers: { "Content-Type": "application/x-www-form-urlencoded" },
          body: `paper_id=${encodeURIComponent(hidden.value)}`,
        }).catch(() => {});
      }

      // Persist current transcript before DOM is wiped by the hx-on:click handler.
      if (log) {
        // Build a clean transcript similar to _debouncedSave to avoid caching
        // interrupted / stopped bubbles or dangling prompts.
        const msgs = Array.from(log.children || []);
        const cleaned = [];
        for (let i = 0; i < msgs.length; i++) {
          const el = msgs[i];
          if (el.classList.contains("user-message")) {
            const nxt = msgs[i + 1];
            const isCleanAssistant =
              nxt &&
              nxt.classList.contains("assistant-message") &&
              nxt.dataset.finalised === "1" &&
              nxt.dataset.interrupted !== "1" &&
              !nxt.classList.contains("interrupted") &&
              !nxt.querySelector(".response-stopped");
            if (isCleanAssistant) {
              cleaned.push(el, nxt);
              i++; // skip paired assistant already handled
            }
          } else if (el.classList.contains("assistant-message")) {
            if (
              el.dataset.finalised === "1" &&
              el.dataset.interrupted !== "1" &&
              !el.classList.contains("interrupted") &&
              !el.querySelector(".response-stopped")
            ) {
              cleaned.push(el);
            }
          } else {
            cleaned.push(el);
          }
        }

        const MAX_CHAT_LINES = 100;
        const recent = cleaned.slice(-MAX_CHAT_LINES);
        const html = recent.map((n) => n.outerHTML).join("");

        // Avoid clobbering an existing cached transcript with an empty string
        // (happens when the user aborted mid-stream and no clean pairs remain).
        if (!html.trim()) return;

        const pid =
          log.dataset.chatKey ||
          log.dataset.chatPid ||
          (log.id ? log.id.replace("chat-log-", "") : "");
        if (pid) saveTranscript(null, pid, html);
      }

      // Re-enable controls so the user can continue chatting if they reopen.
      setControlsEnabled(modal, true);
    };

    // 1) Native <dialog> close (not used currently but safe to keep)
    modal.addEventListener("close", cleanUp);

    // Expose for external beforeSwap hook
    modal._ohCleanUp = cleanUp;

    // 2) Our custom ✕ button empties #modal-container – intercept click BEFORE swap
    const btn = modal.querySelector(".modal-close-btn");
    if (btn) {
      // Capture phase guarantees we run before the hx-on:click handler that
      // nukes #modal-container so our activeStream element is still in the DOM.
      btn.addEventListener("click", cleanUp, { once: true, capture: true });
    }
  }
}

// ---------------------------------------------------------------------------
// One-off style injection for caret, copy-btn, stop spinner, etc.
// ---------------------------------------------------------------------------
(function () {
  if (document.getElementById("oh-extra-styles")) return;
  const style = document.createElement("style");
  style.id = "oh-extra-styles";
  style.textContent = `
    .caret { font-weight: 400; }
    .oh-typing { display:inline-flex; align-items:center; gap:8px; }
    .oh-typing-dots { display:inline-flex; gap:4px; }
    .oh-typing-dots i { width:6px; height:6px; border-radius:50%; background:currentColor; opacity:.3; animation:oh-dot-pulse 1.2s ease-in-out infinite; }
    .oh-typing-dots i:nth-child(2) { animation-delay:.15s; }
    .oh-typing-dots i:nth-child(3) { animation-delay:.3s; }
    @keyframes oh-dot-pulse { 0%,60%,100% { opacity:.3; transform:translateY(0); } 30% { opacity:1; transform:translateY(-3px); } }
    .oh-typing-status { font-size:.8em; opacity:.65; font-style:italic; }
    .stream-cursor { display:inline-block; width:9px; height:1.05em; margin-left:1px; vertical-align:text-bottom; background:currentColor; border-radius:1px; animation:oh-caret-blink 1s steps(2,jump-none) infinite; }
    @keyframes oh-caret-blink { to { visibility:hidden; } }
    .chat-cite { color:#2563eb; cursor:help; font-size:.72em; margin-left:1px; }
    .chat-sources { margin-top:.6rem; padding-top:.45rem; border-top:1px solid rgba(128,128,128,.35); font-size:.8em; opacity:.85; }
    .chat-sources-label { font-weight:600; margin-bottom:.2em; font-size:.85em; letter-spacing:.02em; }
    .chat-source-item { margin:.15em 0; }
    .chat-source-n { color:#2563eb; margin-right:.3em; }
    .copy-btn { position: absolute; top:4px; right:4px; border:none; background:transparent; cursor:pointer; }
    .copy-btn:hover { color:#ef4444; }
    .export-btn { background:transparent; border:none; cursor:pointer; margin-left:6px; }
    .response-stopped { font-style: italic; color:#6b7280; }
    .response-stopped .stopped-x { color:#ef4444; margin-right:4px; }
    .stop-spinner { width:16px;height:16px;border:2px solid #d1d5db;border-top-color:#ef4444;border-radius:50%;animation:oh-spin 0.8s linear infinite;display:inline-block;vertical-align:middle; }
    @keyframes oh-spin { to { transform: rotate(360deg); } }
  `;
  document.head.appendChild(style);
})();

// Submit on Enter (without Shift) inside any chat textarea
document.body.addEventListener("keydown", (evt) => {
  const ta = evt.target;
  if (!(ta instanceof HTMLTextAreaElement)) return;
  if (!ta.closest(".modal")) return; // only within chat modal
  const MAX_CHARS = 2400; // ~1.5k tokens safeguard
  if (evt.key === "Enter" && !evt.shiftKey) {
    evt.preventDefault();
    if (ta.value.length > MAX_CHARS) {
      ta.value = ta.value.slice(0, MAX_CHARS);
    }
    const form = ta.closest("form");
    if (form) form.requestSubmit();
  }
});

console.debug("[oh] chat/ui.js loaded");
