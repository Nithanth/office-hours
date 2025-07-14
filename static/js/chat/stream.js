// chat/stream.js – SSE streaming & typing indicator logic
// Handles token buffering, markdown render, and finalisation of assistant messages.

import { dbg, decodeEntities } from "../util.js";
import { getModalParts, setControlsEnabled, addCopyButtons, scrollToBottom } from "./ui.js";
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
  contentSpan.className = "caret";
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

  // Ensure log stays scrolled while the assistant is typing
  const container = targetEl.closest(".chat-log-container");
  if (container) scrollToBottom(container);

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

  // Keep viewport pinned to bottom as tokens flush
  const container = targetEl.closest(".chat-log-container");
  if (container) scrollToBottom(container);
}

// ---------------------------------------------------------------------------
// Markdown helpers now provided by chat/markdown.js – legacy loaders removed.
// ---------------------------------------------------------------------------
/* legacy markdown helpers removed – now using chat/markdown.js */

/** Render final markdown & clean up timers, attributes, UI state. */
function _finalizeBubble(targetEl) {
  console.debug('[lux] _finalizeBubble enter', { id: targetEl?.id, interrupted: targetEl?.dataset?.interrupted });
  if (!targetEl || targetEl.dataset.finalised === "1") return;

  // Mark as finalised *early* to prevent a late htmx:sseError event from
  // mis-classifying a completed response as interrupted before we finish
  // markdown rendering. The rest of this function will refine the contents
  // but callers (cleaners, transcript builders) can already rely on the flag.
  targetEl.dataset.finalised = "1";

  // Flush any remaining tokens
  _flushTokens(targetEl);
  // Ensure .typing state cleared so UI can send further messages
  targetEl.classList.remove("typing");
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
    // If no tokens were flushed we still need to remove the caret span so the
    // response-stopped banner (or empty bubble) looks nice.
    if (entry && entry.first) {
      const caretSpan = entry.contentEl;
      if (caretSpan && caretSpan.parentElement === targetEl) caretSpan.remove();
      entry.first = false;
    }
    _pendingTokenBuffers.delete(targetEl);
    if (typeof window.enableShuffle === "function") window.enableShuffle();

    // Re-enable modal controls
    const modal = targetEl.closest(".modal");
    if (modal) setControlsEnabled(modal, true);

    // Final scroll to show completed message
    const container = targetEl.closest(".chat-log-container");
    if (container) scrollToBottom(container);

    // Persist transcript *after* final HTML render so cache stays clean
    _debouncedSave(targetEl, true);

    // Remove SSE attributes to stop reconnection attempts
    ["sse-connect", "hx-sse-reconnect-delay", "hx-ext", "data-batch-ms"].forEach((attr) =>
      targetEl.removeAttribute(attr)
    );

    // Persist transcript (debounced)
  }
}

// ---------------------------------------------------------------------------
// Transcript caching (in-memory Map per session)
// ---------------------------------------------------------------------------
window.__luxChatCache = window.__luxChatCache || new Map();
function saveTranscript(_sessionId, key, html) {
  console.debug('[lux] saveTranscript', { key, chars: html.length });
  window.__luxChatCache.set(key, html);
}

export { saveTranscript };

export function loadTranscript(_sessionId, key) {
  const val = window.__luxChatCache?.get(key) || null;
  console.debug('[lux] loadTranscript', { key, chars: val ? val.length : 0, sample: (val || '').slice(0, 120) });
  return val;
}

const MAX_CHAT_LINES = 100;

export const buildCleanTranscript = (container) => {
  const msgs = Array.from(container.children || []);
  const cleaned = [];

  msgs.forEach((el) => {
    if (el.classList.contains("assistant-message")) {
      const isClean =
        el.dataset.finalised === "1" &&
        el.dataset.interrupted !== "1" &&
        !el.classList.contains("interrupted") &&
        !el.querySelector(".response-stopped");

      console.debug('[lux] buildCleanTranscript assistant', {
        id: el.id,
        finalised: el.dataset.finalised,
        interrupted: el.dataset.interrupted,
        hasInterruptedCls: el.classList.contains('interrupted'),
        hasStopped: !!el.querySelector('.response-stopped'),
        taken: isClean
      });

      if (isClean) {
        // Ensure the preceding user prompt (if any) is included first
        const prev = el.previousElementSibling;
        if (prev && prev.classList.contains("user-message") && !cleaned.includes(prev)) {
          cleaned.push(prev);
        }
        cleaned.push(el);
      }
    } else if (el.classList.contains("user-message")) {
      // Do NOT push standalone user prompts; we'll include it when its assistant
      // reply passes the isClean check above. This guarantees dangling prompts
      // from aborted turns are omitted from the cached transcript.
      return;
    } else {
      cleaned.push(el); // system/info blocks, etc.
    }
  });

  return cleaned;
};

const _saveTimers = new WeakMap();
function _debouncedSave(bubbleEl, immediate = false) {
  const container = bubbleEl?.closest?.(".chat-log-container");
  if (!container) return;
  if (!immediate) clearTimeout(_saveTimers.get(container));
  const runner = () => {
    const cleaned = buildCleanTranscript(container);
    const recent = cleaned.slice(-MAX_CHAT_LINES);
    const html = recent.map((n) => n.outerHTML).join("");

    // Skip if we ended up with an empty transcript (e.g., assistant still typing).
    if (!html.trim()) return;

    const chatKey =
      container.dataset.chatKey || container.dataset.chatPid || container.id?.replace("chat-log-", "");
    console.debug('[lux] saveTranscript', chatKey, {
      cleanedLen: cleaned.length,
      htmlChars: html.length,
      sample: html.slice(0, 80)
    });
    if (chatKey) saveTranscript(null, chatKey, html);
  };

  if (immediate) {
    runner();
  } else {
    const t = setTimeout(runner, 120);
    _saveTimers.set(container, t);
  }
}

// ---------------------------------------------------------------------------
// SSE Event listeners
// ---------------------------------------------------------------------------
// Intercept messages so we can batch tokens before HTMX swaps default.
document.body.addEventListener("htmx:sseBeforeMessage", (evt) => {
  const target = evt.target; // element with sse-swap attr
  if (!(target instanceof HTMLElement)) return;

  // Log every SSE message before we decide what to do with it.
  const dbgType = evt.detail?.type ?? "token";
  const dbgData = typeof evt.detail?.data === "string" ? evt.detail.data.slice(0, 80) : "";
  console.debug('[lux] sseBeforeMessage', { id: target.id, type: dbgType, preview: dbgData });

  // Only intercept and batch messages for chat bubbles that opted-in via data-batch-ms.
  const batchMsAttr = target.dataset.batchMs;
  if (batchMsAttr === undefined) {
    return; // Allow HTMX to handle normally (e.g. report streams)
  }

  // Cancel default swap – we'll batch and flush ourselves.
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
    console.debug(`[lux] event:hx-${evtName}`, { id: el?.id, finalised: el?.dataset?.finalised });
    if (el?.classList.contains("assistant-message")) _finalizeBubble(el);
  }, true);
});

// Close EventSource on error & mark interrupted
document.body.addEventListener("htmx:sseError", (evt) => {
  const el = evt.detail?.elt;
  // If bubble already completed, treat as normal close not error.
  if (el?.dataset?.finalised === "1") return;

  const src = evt.detail?.source;
  if (src?.close) setTimeout(() => src.close(), 50);

  if (el?.classList.contains("assistant-message")) {
    console.warn('[lux] sseError for bubble', { id: el.id, textLen: el.textContent.length });
    // If we actually received some tokens, treat this as a graceful close
    // (Gemini sometimes closes the stream which HTMX surfaces as sseError).
    const hasContent = el.textContent.trim().length > 0;

    if (!hasContent) {
      el.dataset.interrupted = "1";
      el.classList.add("interrupted");

      if (!el.querySelector(".response-stopped")) {
        el.innerHTML =
          '<span class="response-stopped"><span class="stopped-x">✖</span> response stopped</span>';
      }
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
export const _loadTranscriptCompat = loadTranscript;

console.debug("[lux] chat/stream.js loaded (placeholder)");
