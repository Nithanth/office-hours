// chat/stream.js – SSE streaming & typing indicator logic
// Handles token buffering, markdown render, and finalisation of assistant messages.

import { dbg, decodeEntities } from "../util.js?v=2";
import { getModalParts, setControlsEnabled, addCopyButtons, scrollToBottom } from "./ui.js?v=2";
import { ensureMarkdownLibs, ensureHighlightLoaded as mdHighlightLoaded, ensureKatexLoaded } from "./markdown.js?v=2";

// Pre-load markdown libs so they're available for progressive rendering
ensureMarkdownLibs();

// Map<Element, Entry> tracking in-flight SSE token buffers
const _pendingTokenBuffers = new Map();

/**
 * Start animated typing indicator on a just-inserted assistant bubble and register
 * token buffer bookkeeping. Returns the entry stored in _pendingTokenBuffers.
 */
function _startTypingIndicator(targetEl, batchMs) {
  dbg("typing-indicator →", { targetEl, batchMs });

  // If HTMX has already swapped some early token content into the bubble
  // before our listener took over, capture that text so we don't lose it
  // when we replace the bubble's HTML with the caret span.
  const preExisting = targetEl.textContent || "";

  const contentSpan = document.createElement("span");
  contentSpan.className = "caret";
  contentSpan.textContent = "●"; // cycles through ● ●● ●●●

  const entry = {
    buf: [],
    lastFlush: performance.now(),
    lastRender: 0,
    flushTimer: null,
    first: true,
    batchMs,
    typingTimer: null,
    md: "", // start fresh – ignore placeholder/ellipsis to avoid ".." prefix
    source: null, // EventSource reference
    contentEl: contentSpan,
  };


  targetEl.innerHTML = "";
  targetEl.appendChild(contentSpan);

  // Ensure log stays scrolled while the assistant is typing
  const container = targetEl.closest(".chat-log-container");
  if (container) scrollToBottom(container);

  entry.typingTimer = setInterval(() => {
    const states = ["●", "●●", "●●●"];
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
  if (!entry) return;
  // Allow first flush even when there are no new tokens so that any
  // pre-existing text (captured before we inserted the caret) replaces the
  // typing indicator. Skip only when we already flushed once and have no new
  // tokens to add.
  if (entry.buf.length === 0 && !entry.first) return;
  if (entry.first) {
    // First flush: remove caret and prepare container
    if (entry.typingTimer) clearInterval(entry.typingTimer);
    targetEl.classList.remove("typing");
    const newSpan = document.createElement("span");
    newSpan.className = "msg-content";
    entry.contentEl.replaceWith(newSpan);
    entry.contentEl = newSpan;
    entry.first = false;
  } else {
    // Subsequent flushes – if HTMX error handler wiped the bubble's innerHTML
    // our contentEl span will have been removed from the DOM. Ensure it is
    // re-attached so text becomes visible again.
    if (!targetEl.contains(entry.contentEl)) {
      targetEl.appendChild(entry.contentEl);
    }
  }
  let tokenBatch = entry.buf.join("");
  entry.buf.length = 0;
  tokenBatch = decodeEntities(tokenBatch).replace(/<br\s*\/?>/gi, "\n");
  entry.md += tokenBatch;
  entry.lastFlush = performance.now();

  // --- Progressive markdown rendering (throttled) ---
  const now = performance.now();
  if (window.marked && (now - entry.lastRender >= 100)) {
    try {
      // Protect LaTeX from marked.js
      const mathPlaceholders = [];
      let protectedMd = entry.md;

      protectedMd = protectedMd.replace(/\$\$([\s\S]*?)\$\$/g, (m) => {
        mathPlaceholders.push(m);
        return `%%MATH${mathPlaceholders.length - 1}%%`;
      });
      protectedMd = protectedMd.replace(/\\\[([\s\S]*?)\\\]/g, (m) => {
        mathPlaceholders.push(m);
        return `%%MATH${mathPlaceholders.length - 1}%%`;
      });
      protectedMd = protectedMd.replace(/\$([^\$\n]+?)\$/g, (m) => {
        mathPlaceholders.push(m);
        return `%%MATH${mathPlaceholders.length - 1}%%`;
      });
      protectedMd = protectedMd.replace(/\\\((.*?)\\\)/g, (m) => {
        mathPlaceholders.push(m);
        return `%%MATH${mathPlaceholders.length - 1}%%`;
      });

      // Close any open code fences so marked doesn't choke on incomplete blocks
      const fenceCt = (protectedMd.match(/```/g) || []).length;
      const mdSrc = fenceCt % 2 === 1 ? `${protectedMd}\n\n\`\`\`` : protectedMd;

      // Parse with marked (skip DOMPurify, KaTeX, highlight.js for perf – applied on finalize)
      let html = window.marked.parse(mdSrc, { gfm: true, breaks: true });

      // Restore math placeholders
      mathPlaceholders.forEach((original, i) => {
        html = html.replace(`%%MATH${i}%%`, original);
      });

      entry.contentEl.innerHTML = html;
      entry.lastRender = now;
    } catch (e) {
      // If marked throws, fall back to plain text
      entry.contentEl.textContent = entry.md;
    }
  } else if (!window.marked) {
    // marked.js not yet loaded – fall back to raw text
    entry.contentEl.textContent = entry.md;
  }
  // If marked is available but throttle hasn't elapsed, skip render (keep previous innerHTML)

  // Keep viewport pinned to bottom as tokens flush
  const container = targetEl.closest(".chat-log-container");
  if (container) scrollToBottom(container);
  console.debug('[lux]  flushTokens',
    { id: targetEl.id, mdLen: entry.md.length, first: entry.first });
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
      // --- Protect LaTeX from marked.js -----------------------------------
      // marked will mangle backslashes and underscores inside math blocks.
      // We temporarily replace them with placeholders, parse markdown, then
      // restore them so KaTeX can render properly.
      const mathPlaceholders = [];
      let protectedMd = rawMd;

      // Protect display math ($$...$$) and (\[...\])
      protectedMd = protectedMd.replace(/\$\$([\s\S]*?)\$\$/g, (m) => {
        mathPlaceholders.push(m);
        return `%%MATH${mathPlaceholders.length - 1}%%`;
      });
      protectedMd = protectedMd.replace(/\\\[([\s\S]*?)\\\]/g, (m) => {
        mathPlaceholders.push(m);
        return `%%MATH${mathPlaceholders.length - 1}%%`;
      });
      // Protect inline math ($...$) and (\(...\))
      protectedMd = protectedMd.replace(/\$([^\$\n]+?)\$/g, (m) => {
        mathPlaceholders.push(m);
        return `%%MATH${mathPlaceholders.length - 1}%%`;
      });
      protectedMd = protectedMd.replace(/\\\((.*?)\\\)/g, (m) => {
        mathPlaceholders.push(m);
        return `%%MATH${mathPlaceholders.length - 1}%%`;
      });

      // Make sure code fences are balanced
      const fenceCt = (protectedMd.match(/```/g) || []).length;
      const mdSrc = fenceCt % 2 === 1 ? `${protectedMd}\n\n\`\`\`` : protectedMd;
      const dirtyHtml = window.marked.parse(mdSrc, { gfm: true, breaks: true });
      const safeHtml = window.DOMPurify ? window.DOMPurify.sanitize(dirtyHtml) : dirtyHtml;

      // Restore math placeholders
      let finalHtml = safeHtml;
      mathPlaceholders.forEach((original, i) => {
        finalHtml = finalHtml.replace(`%%MATH${i}%%`, original);
      });
      targetEl.innerHTML = finalHtml;
    })
    // --- KaTeX math rendering --------------------------------------------
    .then(() => ensureKatexLoaded())
    .then(() => {
      if (window.renderMathInElement) {
        window.renderMathInElement(targetEl, {
          delimiters: [
            { left: "$$", right: "$$", display: true },
            { left: "$", right: "$", display: false },
            { left: "\\(", right: "\\)", display: false },
            { left: "\\[", right: "\\]", display: true },
          ],
          throwOnError: false,
        });
      }
    })
    // ---------------------------------------------------------------------
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

    // Do NOT close the EventSource here.  The server emits a final "sse-close"
    // control event that instructs the HTMX SSE extension to shut down the
    // connection cleanly.  Closing it manually right after we receive
    // `message_done` races with that control event and results in an
    // `htmx:sseError`, which incorrectly marks the bubble as interrupted.
    // Setting a very long reconnect delay prevents htmx from attempting to
    // re-establish the stream after we’ve closed it.
    targetEl.setAttribute("hx-sse-reconnect-delay", "2147483647");

    console.debug('[lux]  doneCleanup',
      { id: targetEl.id,
        htmlLen: targetEl.innerHTML.length,
        textSample: targetEl.textContent.slice(0, 120) });
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

    // Always prefer stable paperId (data-chat-pid) so cached key survives across
    // modal instances. Fallback to explicit chatKey or generated id when paperId
    // is absent (edge cases).
    const chatKey =
      container.dataset.chatPid || container.dataset.chatKey || container.id?.replace("chat-log-", "");
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
// Manual EventSource handling for chat bubbles (bypass HTMX SSE extension)
// ---------------------------------------------------------------------------
function _attachEventSource(bubbleEl) {
  if (bubbleEl.dataset.streamAttached === "1") return; // already attached
  const url = bubbleEl.dataset.streamUrl;
  if (!url) return;

  bubbleEl.dataset.streamAttached = "1";

  const es = new EventSource(url, { withCredentials: true });
  let entry = null;
  const batchMs = parseInt(bubbleEl.dataset.batchMs || "20", 10);

  const onAssistant = (evt) => {
    if (!entry) {
      entry = _startTypingIndicator(bubbleEl, batchMs);
      entry.source = es;
    }
    entry.buf.push(evt.data);
    const now = performance.now();
    if (now - entry.lastFlush > entry.batchMs) {
      _flushTokens(bubbleEl);
    }
  };

  es.addEventListener("assistant", onAssistant);
  es.addEventListener("message_done", () => {
    // flush remaining and finalise
    _finalizeBubble(bubbleEl);
  });
  es.addEventListener("sse-close", () => {
    es.close();
  });
  es.addEventListener("error", () => {
    // mark interrupted & finalise gracefully
    bubbleEl.dataset.interrupted = "1";
    if (entry) entry.buf.push("\n");
    _finalizeBubble(bubbleEl);
    es.close();
  });
}

// Observe DOM swaps to start streams for new assistant bubbles
function _setupBubbleObserver() {
  const observe = (root) => {
    root.querySelectorAll(".assistant-message.typing[data-stream-url]").forEach(_attachEventSource);
  };

  // Initial scan (page load)
  observe(document);

  // After each HTMX swap
  document.body.addEventListener("htmx:afterSwap", (evt) => {
    const tgt = evt.detail?.target;
    if (tgt) observe(tgt);
  });
}

_setupBubbleObserver();

console.debug("[lux] chat/stream.js loaded");

// Re-export helpers for other modules
export const pendingBuffers = _pendingTokenBuffers;
export const startTypingIndicator = _startTypingIndicator;
export const flushTokens = _flushTokens;
export const finalizeBubble = _finalizeBubble;