// chat/stream.js – SSE streaming & typing indicator logic
// Handles token buffering, markdown render, and finalisation of assistant messages.

import { dbg, decodeEntities, debounce } from "../util.js";
import { getModalParts, setControlsEnabled, addCopyButtons } from "./ui.js";
import { ensureMarkdownLibs, ensureHighlightLoaded as mdHighlightLoaded } from "./markdown.js";

// Map<Element, Entry> tracking in-flight SSE token buffers
const _pendingTokenBuffers = new Map();

/**
 * Start animated typing indicator on a just-inserted assistant bubble and register
 * token buffer bookkeeping. Returns the entry stored in _pendingTokenBuffers.
 */
function _startTypingIndicator(targetEl, batchMs) {
  dbg("typing-indicator →", { targetEl, batchMs });
  const contentSpan = document.createElement("span");
  contentSpan.className = "msg-content caret";
  contentSpan.textContent = "."; // cycles through . .. ...

  const entry = {
    buf: [],
    lastFlush: performance.now(),
    flushTimer: null,
    first: true,
    batchMs,
    typingTimer: null,
    md: "", // accumulated markdown
    source: null, // EventSource reference
    contentEl: contentSpan,
  };

  // Reset bubble & inject children
  targetEl.innerHTML = "";
  targetEl.appendChild(contentSpan);
  entry.typingTimer = setInterval(() => {
    const states = [".", "..", "..."];
    const current = contentSpan.textContent;
    contentSpan.textContent = states[(states.indexOf(current) + 1) % states.length];
  }, 500);
  _pendingTokenBuffers.set(targetEl, entry);

  // Disable chat controls while streaming
  const modal = targetEl.closest(".modal");
  if (modal) setControlsEnabled(modal, false);
  const { textarea, submitBtn } = getModalParts(targetEl);
  if (textarea) textarea.setAttribute("disabled", "true");
  if (submitBtn) submitBtn.setAttribute("disabled", "true");

  return entry;
}

/** Flush buffered markdown tokens into `entry.md`. */
function _flushTokens(targetEl) {
  const entry = _pendingTokenBuffers.get(targetEl);
  if (!entry || entry.buf.length === 0) return;
  if (entry.first) {
    // First flush: remove caret and prepare container
    if (entry.typingTimer) clearInterval(entry.typingTimer);
    targetEl.classList.remove("typing");
    const newSpan = document.createElement("span");
    newSpan.className = "msg-content";
    entry.contentEl.replaceWith(newSpan);
    entry.contentEl = newSpan;
    entry.first = false;
  }
  let tokenBatch = entry.buf.join("");
  entry.buf.length = 0;
  tokenBatch = decodeEntities(tokenBatch).replace(/<br\s*\/?>/gi, "\n");
  entry.md += tokenBatch;
  entry.lastFlush = performance.now();
  entry.contentEl.textContent = entry.md; // raw until finalised (placeholder)
}

// ---------------------------------------------------------------------------
// Markdown helpers now provided by chat/markdown.js
// ---------------------------------------------------------------------------
/* legacy markdown helpers removed – now using chat/markdown.js */
let _markedPromise = null;
function ensureMarkedLoaded() {
  if (window.marked) return Promise.resolve();
  if (_markedPromise) return _markedPromise;
  _markedPromise = new Promise((resolve) => {
    const s = document.createElement("script");
    s.src = "https://cdnjs.cloudflare.com/ajax/libs/marked/12.0.1/marked.min.js";
    s.onload = () => resolve();
    document.head.appendChild(s);
  });
  return _markedPromise;
}

let _purifyPromise = null;
function ensurePurifyLoaded() {
  if (window.DOMPurify) return Promise.resolve();
  if (_purifyPromise) return _purifyPromise;
  _purifyPromise = new Promise((resolve) => {
    const s = document.createElement("script");
    s.src = "https://cdn.jsdelivr.net/npm/dompurify@3.1.2/dist/purify.min.js";
    s.onload = () => resolve();
    document.head.appendChild(s);
  });
  return _purifyPromise;
}

let _hlPromise = null;
function ensureHighlightLoaded() {
  if (window.hljs) return Promise.resolve();
  if (_hlPromise) return _hlPromise;
  _hlPromise = new Promise((resolve) => {
    const dark = document.body.classList.contains("lux-dark");
    const css = document.createElement("link");
    css.rel = "stylesheet";
    css.href = `https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github${
      dark ? "-dark" : ""
    }.min.css`;
    document.head.appendChild(css);

    const s = document.createElement("script");
    s.src = "https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js";
    s.onload = () => resolve();
    document.head.appendChild(s);
  });
  return _hlPromise;
}

/** Render final markdown & clean up timers, attributes, UI state. */
function _finalizeBubble(targetEl) {
  if (!targetEl || targetEl.dataset.finalised === "1") return;

  // Flush any remaining tokens
  _flushTokens(targetEl);
  const entry = _pendingTokenBuffers.get(targetEl);
  if (entry?.typingTimer) clearInterval(entry.typingTimer);

  const wasInterrupted = targetEl.dataset.interrupted === "1" || targetEl.classList.contains("interrupted");
  const rawMd = decodeEntities(entry ? entry.md : targetEl.textContent);
  if (wasInterrupted && !rawMd.trim()) {
    // Show stopped notice if absent
    if (!targetEl.querySelector(".response-stopped")) {
      const wrapper = document.createElement("span");
      wrapper.className = "response-stopped";
      wrapper.innerHTML = '<span class="stopped-x">✖</span> response stopped';
      targetEl.appendChild(wrapper);
    }
    doneCleanup();
    return;
  }

  ensureMarkdownLibs()
    .then(() => {
      // Make sure code fences are balanced
      const fenceCt = (rawMd.match(/```/g) || []).length;
      const mdSrc = fenceCt % 2 === 1 ? `${rawMd}\n\n\`\`\`` : rawMd;
      const dirtyHtml = window.marked.parse(mdSrc, { gfm: true, breaks: true });
      const safeHtml = window.DOMPurify ? window.DOMPurify.sanitize(dirtyHtml) : dirtyHtml;
      targetEl.innerHTML = safeHtml;
    })
    .then(() => mdHighlightLoaded())
    .then(() => {
      targetEl.querySelectorAll("pre code").forEach((block) => window.hljs && window.hljs.highlightElement(block));
      addCopyButtons(targetEl);
    })
    .finally(doneCleanup)
    .catch((e) => console.error("markdown render error", e));

  function doneCleanup() {
    targetEl.dataset.finalised = "1";
    _pendingTokenBuffers.delete(targetEl);
    if (typeof window.enableShuffle === "function") window.enableShuffle();

    // Re-enable modal controls
    const modal = targetEl.closest(".modal");
    if (modal) setControlsEnabled(modal, true);

    // Remove SSE attributes to stop reconnection attempts
    ["sse-connect", "hx-sse-reconnect-delay", "hx-ext", "data-batch-ms"].forEach((attr) =>
      targetEl.removeAttribute(attr)
    );

    // Persist transcript (debounced)
    _debouncedSave(targetEl);
  }
}

// ---------------------------------------------------------------------------
// Transcript caching (in-memory Map per session)
// ---------------------------------------------------------------------------
window.__luxChatCache = window.__luxChatCache || new Map();
function saveTranscript(_sessionId, key, html) {
  window.__luxChatCache.set(key, html);
}

const _saveTimers = new WeakMap();
const MAX_CHAT_LINES = 60;
function _debouncedSave(bubbleEl) {
  const container = bubbleEl?.closest?.(".chat-log-container");
  if (!container) return;
  clearTimeout(_saveTimers.get(container));
  const t = setTimeout(() => {
    const msgs = Array.from(container.children || []);
    const recent = msgs.slice(-MAX_CHAT_LINES);
    const html = recent.map((n) => n.outerHTML).join("");
    const chatKey = container.dataset.chatKey || container.dataset.chatPid || container.id?.replace("chat-log-", "");
    if (chatKey) saveTranscript(null, chatKey, html);
  }, 120);
  _saveTimers.set(container, t);
}

// ---------------------------------------------------------------------------
// SSE Event listeners
// ---------------------------------------------------------------------------
// Intercept messages so we can batch tokens before HTMX swaps default.
document.body.addEventListener("htmx:sseBeforeMessage", (evt) => {
  const target = evt.target; // element with sse-swap attr
  if (!(target instanceof HTMLElement)) return;

  // Ignore bubbles without data-batch-ms attr
  const batchMsAttr = target.dataset.batchMs;
  if (batchMsAttr === undefined) return;

  // Cancel default swap
  evt.preventDefault();

  if (evt.detail?.type === "error") {
    target.classList.add("failed");
    target.innerHTML += "<br><em>(stream aborted)</em>";
    return;
  }

  if (evt.detail?.type === "done") {
    _finalizeBubble(target);
    return;
  }

  const token = typeof evt.detail?.data === "string" ? evt.detail.data : "";
  if (!token) return;
  let entry = _pendingTokenBuffers.get(target);
  if (!entry) {
    entry = _startTypingIndicator(target, parseInt(batchMsAttr || "20", 10));
    entry.source = evt.detail?.source || null;
  }
  entry.buf.push(token);

  const now = performance.now();
  if (now - entry.lastFlush > 120) {
    _flushTokens(target);
    return;
  }
  if (!entry.flushTimer) {
    entry.flushTimer = setTimeout(() => {
      _flushTokens(target);
      entry.flushTimer = null;
    }, entry.batchMs);
  }
});

// On close/done events: ensure bubble finalises
["sseDone", "sseClose"].forEach((evtName) => {
  document.body.addEventListener(`htmx:${evtName}`, (ev) => {
    const el = ev.detail?.elt;
    if (el?.classList.contains("assistant-message")) _finalizeBubble(el);
  }, true);
});

// Close EventSource on error & mark interrupted
document.body.addEventListener("htmx:sseError", (evt) => {
  const src = evt.detail?.source;
  if (src?.close) setTimeout(() => src.close(), 50);
  const el = evt.detail?.elt;
  if (el?.classList.contains("assistant-message")) {
    const entry = _pendingTokenBuffers.get(el);
    if (!entry || (entry.first && (!entry.md || entry.md.length === 0))) {
      el.dataset.interrupted = "1";
      el.classList.add("interrupted");
      el.innerHTML = '<span class="response-stopped"><span class="stopped-x">✖</span> response stopped</span>';
    }
    _finalizeBubble(el);
  }
});

console.debug("[lux] chat/stream.js loaded");

// Re-export helpers for other modules
export const pendingBuffers = _pendingTokenBuffers;
export const startTypingIndicator = _startTypingIndicator;
export const flushTokens = _flushTokens;
export const finalizeBubble = _finalizeBubble;
export function loadTranscript(_sessionId, key) {
  return window.__luxChatCache?.get(key) || null;
}

// TODO: migrate _pendingTokenBuffers, _startTypingIndicator, _flushTokens, and
// related SSE event-source handling from legacy app.js.

console.debug("[lux] chat/stream.js loaded (placeholder)");
