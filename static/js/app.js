// Minimal JavaScript if needed.
// For now, HTMX will handle most dynamic interactions.
// console.log("Custom app.js loaded.");

// === DEBUG TRACING =============================================
const dbg = (...args) => console.debug("[chat-dbg]", ...args);
// ===============================================================

// Utility: temporarily disable/enable shuffle controls while a report is generating
const shuffleBtnIds = ["shuffle-btn", "shuffle-all-btn", "reset-btn"];
let reportBusy = false;

function disableShuffle() {
  reportBusy = true;
  shuffleBtnIds.forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.disabled = true;
  });
}

function enableShuffle() {
  reportBusy = false;
  shuffleBtnIds.forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.disabled = false;
  });
}

// Global click guard to prevent any shuffle-related requests while report is busy
document.addEventListener(
  "click",
  (evt) => {
    if (reportBusy && evt.target.closest("#shuffle-btn, #shuffle-all-btn, #reset-btn")) {
      evt.preventDefault();
      evt.stopPropagation();
    }
  },
  true
);

console.log("app.js loaded");

// --------------------------------------------------
// Phase 2: Scroll listener detach once at bottom
// --------------------------------------------------

document.addEventListener("DOMContentLoaded", () => {
  const chatLog = document.getElementById("chat-log") || document.getElementById("chat-scroll");
  if (!chatLog) return;

  const scrollHandler = () => {
    const atBottom = Math.abs(chatLog.scrollHeight - chatLog.scrollTop - chatLog.clientHeight) < 4;
    if (atBottom) {
      chatLog.removeEventListener("scroll", scrollHandler, { passive: true });
    }
  };

  chatLog.addEventListener("scroll", scrollHandler, { passive: true });
});

// --------------------------------------------------
// Phase 2.1: Disable shuffle controls while a report is generating
// --------------------------------------------------

// When the user triggers a new report generation we disable the shuffle controls
document.addEventListener("click", (evt) => {
  const tgt = evt.target;
  if (!(tgt instanceof HTMLElement)) return;

  // Detect generate-report submit button (works even after HTMX swaps)
  if (tgt.id === "generate-report-btn") {
    // Disable shuffle and reset any stored chat transcripts so a fresh report
    // opens with a clean chat modal (no stale messages from prior runs).
    disableShuffle();
    if (window.__luxChatCache) window.__luxChatCache.clear();
  }
});

// Re-enable shuffle when backend signals report completion (see listener below)

// (legacy dark-mode toggle removed; settings.js now owns theme logic)

// ---------------------------------------------------------------------------
// Phase 3-A: transient in-memory transcript cache (cleared on page refresh)
// ---------------------------------------------------------------------------
// We no longer persist chat logs to localStorage. Instead, store them in a
// process-wide Map so modals reopened within the same SPA session restore, but
// a full page reload starts fresh.

window.__luxChatCache = window.__luxChatCache || new Map();

function saveTranscript(_sessionId, paperId, html) {
  // sessionId retained for call-site compatibility, but ignored.
  window.__luxChatCache.set(paperId, html);

  // DEBUG: log first 200 chars of what we just cached
  try {
    console.debug("[cache save]", paperId, html.substring(0, 200).replace(/\n/g, "↩"));
  } catch (_) {}
}

function loadTranscript(_sessionId, paperId) {
  return window.__luxChatCache.get(paperId) || null;
}

// Save / load transcript for given paper + session
// function saveTranscript(sessionId, paperId, html) {
//   const key = `lux:chat:${sessionId}:${paperId}`;
//   const payload = {
//     ts: Date.now(),
//     data: compressMaybe(html),
//   };

//   try {
//     localStorage.setItem(key, JSON.stringify(payload));
//   } catch (e) {
//     console.warn("LocalStorage quota exceeded. Evicting oldest items.");
//     // Quota exceeded – simple LRU: find all chat keys, sort by timestamp, remove oldest 5, retry.
//     const keysToEvict = [];
//     for (let i = 0; i < localStorage.length; i++) {
//       const k = localStorage.key(i);
//       if (k && k.startsWith("lux:chat:")) {
//         try {
//           const item = JSON.parse(localStorage.getItem(k));
//           if (item && item.ts) keysToEvict.push({ key: k, ts: item.ts });
//         } catch (_) {}
//       }
//     }
//     keysToEvict.sort((a, b) => a.ts - b.ts);
//     for (let i = 0; i < Math.min(5, keysToEvict.length); i++) {
//       localStorage.removeItem(keysToEvict[i].key);
//     }

//     // Retry once
//     try {
//       localStorage.setItem(key, JSON.stringify(payload));
//     } catch (e2) {
//       alert("Local storage full — older chats were purged to free space.");
//       console.error("Failed to save transcript even after eviction:", e2);
//     }
//   }
// }

// function loadTranscript(sessionId, paperId) {
//   const key = `lux:chat:${sessionId}:${paperId}`;
//   try {
//     const stored = localStorage.getItem(key);
//     if (!stored) return null;
//     const payload = JSON.parse(stored);
//     return decompressMaybe(payload.data);
//   } catch (e) {
//     console.error("Failed to load/parse transcript:", e);
//     return null;
//   }
// }

// ---------------------------------------------------------------------------
// Phase 4-B – Token batching for smoother UI
// ---------------------------------------------------------------------------
const _pendingTokenBuffers = new Map(); // el -> entry

function _decodeEntities(htmlStr) {
  const txt = document.createElement("textarea");
  txt.innerHTML = htmlStr;
  return txt.value;
}

// Utility to get modal elements around *el*
function _getModalParts(el) {
  const modal = el.closest && el.closest(".modal");
  if (!modal) return {};
  const form = modal.querySelector("form[hx-post='/chat']");
  const textarea = form?.querySelector("textarea[name='message']");
  const submitBtn = form?.querySelector("button[type='submit'],input[type='submit']");
  let stopBtn = form?.querySelector(".stop-gen-btn-global");
  if (!stopBtn && form) {
    stopBtn = document.createElement("button");
    stopBtn.className = "stop-gen-btn-global";
    stopBtn.textContent = "⏹";
    stopBtn.title = "Stop generation (Esc)";
    stopBtn.style.marginRight = "8px";
    stopBtn.type = "button"; // prevent form submit
    /* ---- Duplicate inline stop-handler no longer used: replaced by single
       global listener at bottom of file to avoid double execution & races. ----
    stopBtn.addEventListener("click", async () => {
      // (old logic now in global handler)
    */
  }
  // Always mount the button in the form so the global click handler can
  // catch it. Place it just before the textarea/submit (left-most)
  form.insertBefore(stopBtn, textarea || submitBtn || form.firstChild);
  return { modal, form, textarea, submitBtn, stopBtn };
}

// Helper to create and start animated typing dots on *target* element.
function _startTypingIndicator(targetEl, batchMs) {
  console.debug("[chat] start typing indicator", { targetEl, batchMs });
  const contentSpan = document.createElement("span");
  contentSpan.className = "msg-content caret";
  contentSpan.textContent = "."; // will cycle . .. ...

  const entry = {
    buf: [],
    lastFlush: performance.now(),
    flushTimer: null,
    first: true,
    batchMs,
    typingTimer: null,
    md: "", // accumulated markdown
    source: null, // EventSource
    contentEl: contentSpan,
  };
  // Reset bubble & inject children
  targetEl.innerHTML = "";
  targetEl.appendChild(contentSpan);
  entry.typingTimer = setInterval(() => {
    const states = [".", "..", "..."];
    const current = contentSpan.textContent;
    const nextIdx = (states.indexOf(current) + 1) % states.length;
    contentSpan.textContent = states[nextIdx];
  }, 500);
  _pendingTokenBuffers.set(targetEl, entry);

  // Disable chat form controls & show global stop button
  const modal = targetEl.closest('.modal');
  if (modal) {
    _setControlsEnabled(modal, false);
  }
  const { form, textarea, submitBtn, stopBtn } = _getModalParts(targetEl);
  if (textarea) textarea.setAttribute("disabled", "true");
  if (submitBtn) submitBtn.setAttribute("disabled", "true");
  if (stopBtn) stopBtn.style.display = "inline-block";

  return entry;
}

function _flushTokens(targetEl) {
  const entry = _pendingTokenBuffers.get(targetEl);
  if (!entry || entry.buf.length === 0) return;
  if (entry.first) {
    if (entry.typingTimer) {
      clearInterval(entry.typingTimer);
      entry.typingTimer = null;
    }
    targetEl.classList.remove("typing");
    // Replace caret span with fresh container to avoid bold font-weight
    const newSpan = document.createElement("span");
    newSpan.className = "msg-content";
    entry.contentEl.replaceWith(newSpan);
    entry.contentEl = newSpan;
    entry.first = false;
  }
  let tokenBatch = entry.buf.join("");
  // Decode HTML entities (&quot;) and convert <br> to newline so markdown sees raw text
  tokenBatch = _decodeEntities(tokenBatch).replace(/<br\s*\/?>/gi, "\n");
  entry.md += tokenBatch;
  // Render full markdown every flush for true streaming effect
  if (window.marked) {
    try {
      entry.contentEl.innerHTML = window.marked.parse(entry.md, {
        gfm: true,
        breaks: true,
        smartLists: true,
      });
    } catch (e) {
      console.error("parse error", e);
    }
  } else {
    // Lazy-load marked.js once
    if (!document.getElementById("marked-lib")) {
      const s = document.createElement("script");
      s.src = "https://cdn.jsdelivr.net/npm/marked/marked.min.js";
      s.id = "marked-lib";
      document.head.appendChild(s);
    }
  }
  entry.buf.length = 0;
  entry.lastFlush = performance.now();

  // Auto-scroll only if user hasn’t scrolled up
  const container = targetEl.closest(
    ".chat-log-container, .chat-log, .chat-container, .modal-content"
  );
  if (container instanceof HTMLElement) {
    const nearBottom =
      container.scrollTop + container.clientHeight >= container.scrollHeight - 100;
    if (nearBottom) {
      container.scrollTop = container.scrollHeight;
    }
  }
}

// Markdown helper – load marked & DOMPurify once and render HTML
let _mdLibPromise = null;
function _ensureMarkdownLibs() {
  if (window.marked && window.DOMPurify) return Promise.resolve();
  if (_mdLibPromise) return _mdLibPromise;
  _mdLibPromise = Promise.all([
    new Promise((resolve, reject) => {
      if (window.marked) return resolve();
      const s = document.createElement("script");
      s.src = "https://cdn.jsdelivr.net/npm/marked/marked.min.js";
      s.onload = resolve;
      s.onerror = reject;
      document.head.appendChild(s);
    }),
    new Promise((resolve, reject) => {
      if (window.DOMPurify) return resolve();
      const p = document.createElement("script");
      p.src = "https://cdn.jsdelivr.net/npm/dompurify@3.1.2/dist/purify.min.js";
      p.onload = resolve;
      p.onerror = reject;
      document.head.appendChild(p);
    }),
  ]);
  return _mdLibPromise;
}

async function _renderMarkdownPartial(el) {
  try {
    await _ensureMarkdownLibs();
    let rawMd = _decodeEntities(el.textContent);
    const fences = (rawMd.match(/```/g) || []).length;
    if (fences % 2 === 1) rawMd += "\n```"; // close open code block
    const dirtyHtml = window.marked.parse(rawMd, {
      gfm: true,
      breaks: true,
      smartLists: true,
    });

    const safeHtml = window.DOMPurify
      ? window.DOMPurify.sanitize(dirtyHtml)
      : dirtyHtml;

    el.innerHTML = safeHtml;

    // Load highlight.js once
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

    ensureHighlightLoaded().then(() => {
      el.querySelectorAll("pre code").forEach((block) => {
        if (window.hljs) window.hljs.highlightElement(block);
      });
      addCopyButtons(el);
    });
  } catch (e) {
    console.error("[chat] Failed to render partial markdown", e);
  }
}

// Intercept SSE messages *before* HTMX swaps them so we can render tokens
// ourselves and cancel the default behaviour that would insert duplicates.
document.body.addEventListener("htmx:sseBeforeMessage", (evt) => {
  // ---------------------------------------------------------------
  // Generic JSON envelope handler for all SSE messages
  // ---------------------------------------------------------------
  try {
    if (typeof evt.detail?.data === "string") {
      const raw = evt.detail.data;
      const trimmed = raw.trimStart();
      if (trimmed.startsWith("{")) {
        try {
          const parsed = JSON.parse(trimmed);
          if (parsed && typeof parsed.html === "string") {
            // Mutate the event data so downstream listeners & HTMX swap receive raw HTML
            evt.detail.data = parsed.html;
          }
        } catch (e) {
          // malformed JSON – let HTMX handle as-is
        }
      }
    }
  } catch (jsonErr) {
    // Not JSON – ignore.
  }

  const target = evt.target; // element with sse-swap attribute
  if (!(target instanceof HTMLElement)) return;

  // If user has pressed Stop and this bubble was flagged as interrupted, ignore
  // any further incoming tokens that were already in the browser’s EventSource
  // queue when we closed it.
  if (target.classList.contains("interrupted")) {
    evt.preventDefault();
    return;
  }

  // Only handle bubbles that opt-in via data-batch-ms. Let all other SSE
  // messages fall back to HTMX’s default swap behaviour.
  const batchMsAttr = target.dataset.batchMs;
  if (batchMsAttr === undefined) {
    return; // not a chat bubble we manage
  }

  // This is our chat bubble – cancel HTMX default swapping.
  evt.preventDefault();
  // If the server sent a named event (e.g. `event: error`) the type property will carry it.
  if (evt.detail && evt.detail.type === "error") {
    // Mark bubble as failed only on explicit server-side error, not on normal close.
    target.classList.add("failed");
    target.innerHTML += "<br><em>(stream aborted)</em>";
    return;
  }

  // Handle server-sent finalisation event
  if (evt.detail && evt.detail.type === "done") {
    _finalizeBubble(target);
    return;
  }

  const token = evt.detail && typeof evt.detail.data === "string" ? evt.detail.data : "";
  if (!token) return;
  let entry = _pendingTokenBuffers.get(target);
  if (!entry) {
    const batchMsVal = parseInt(batchMsAttr || "20", 10);
    entry = _startTypingIndicator(target, batchMsVal);
    // store EventSource pointer
    entry.source = evt.detail?.source || null;
  }
  entry.buf.push(token);

  const now = performance.now();
  const since = now - entry.lastFlush;
  if (since > 120) {
    _flushTokens(target);
    return;
  }
  if (!entry.flushTimer) {
    entry.flushTimer = setTimeout(() => {
      console.debug("[chat] flush tokens batch", { size: entry.buf.length });
      _flushTokens(target);
      entry.flushTimer = null;
    }, entry.batchMs); // batch window
  }
});

// Finish a streamed assistant bubble: flush tokens, run markdown parse, clean up timers and UI state.
function _finalizeBubble(targetEl) {
  if (!targetEl) return;
  if (targetEl.dataset.finalised === "1") return; // idempotency guard

  // Clean up UI controls regardless of interruption
  const wasInterrupted = targetEl.dataset.interrupted === "1" || targetEl.classList.contains("interrupted");

  _flushTokens(targetEl);

  let entry = _pendingTokenBuffers.get(targetEl);
  
  if (entry && entry.typingTimer) {
    clearInterval(entry.typingTimer);
    entry.typingTimer = null;
  }

  // Early-exit guard: if the bubble was interrupted and already shows the
  // red stop notice (no original markdown to render), skip markdown
  // processing to prevent a duplicate grey notice being appended.
  const noOriginalMd = !entry || !entry.md || entry.md.trim() === "";
  if (wasInterrupted && noOriginalMd) {
    if (!targetEl.querySelector(".response-stopped")) {
      const wrapper = document.createElement("span");
      wrapper.className = "response-stopped";
      const x = document.createElement("span");
      x.className = "stopped-x";
      x.textContent = "✖";
      wrapper.appendChild(x);
      wrapper.appendChild(document.createTextNode(" response stopped"));
      targetEl.appendChild(wrapper);
    }
    targetEl.dataset.finalised = "1";
    _pendingTokenBuffers.delete(targetEl);
    enableShuffle();

    // IMPORTANT: strip SSE attributes so _maybeReleaseControls no longer
    // sees an active stream. Without this the textarea stays disabled on
    // the next turn because the modal still matches [sse-connect].
    targetEl.removeAttribute("sse-connect");
    targetEl.removeAttribute("hx-sse-reconnect-delay");
    targetEl.removeAttribute("hx-ext");
    targetEl.removeAttribute("data-batch-ms");

    const modal = targetEl.closest('.modal');
    if (modal) {
      _setControlsEnabled(modal, true);
    }

    _debouncedSave(targetEl);
    return;
  }

  dbg("_finalizeBubble", { interrupted: targetEl.dataset.interrupted, id: targetEl.id });

  // Lazy-load marked.js once
  let _markedPromise = null;
  function ensureMarkedLoaded() {
    if (window.marked) return Promise.resolve();
    if (_markedPromise) return _markedPromise;
    _markedPromise = new Promise((resolve) => {
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
    return _markedPromise;
  }

  // Lazy-load DOMPurify once for XSS-safe HTML output
  let _purifyPromise = null;
  function ensurePurifyLoaded() {
    if (window.DOMPurify) return Promise.resolve();
    if (_purifyPromise) return _purifyPromise;
    _purifyPromise = new Promise((resolve, reject) => {
      const existing = document.getElementById("dompurify-lib");
      if (existing) {
        existing.addEventListener("load", () => resolve());
        existing.addEventListener("error", reject);
        return;
      }
      const s = document.createElement("script");
      s.src = "https://cdn.jsdelivr.net/npm/dompurify@3.1.2/dist/purify.min.js";
      s.id = "dompurify-lib";
      s.onload = () => resolve();
      s.onerror = reject;
      document.head.appendChild(s);
    });
    return _purifyPromise;
  }

  // Load marked & DOMPurify, then render safe HTML
  ensureMarkedLoaded()
    .then(() => ensurePurifyLoaded())
    .then(() => {
      let rawMd = _decodeEntities(entry ? entry.md : targetEl.textContent);

      const fenceCt = (rawMd.match(/```/g) || []).length;
      if (fenceCt % 2 === 1) rawMd += "\n```";

      const dirtyHtml = window.marked.parse(rawMd, {
        gfm: true,
        breaks: true,
        smartLists: true,
      });

      const safeHtml = window.DOMPurify
        ? window.DOMPurify.sanitize(dirtyHtml)
        : dirtyHtml;

      targetEl.innerHTML = safeHtml;

      if (wasInterrupted && !targetEl.querySelector('.response-stopped')) {
        const wrapper = document.createElement('span');
        wrapper.className = 'response-stopped';
        const x = document.createElement('span');
        x.className = 'stopped-x';
        x.textContent = '✖';
        wrapper.appendChild(x);
        wrapper.appendChild(document.createTextNode(' response stopped'));
        targetEl.appendChild(wrapper);
      }

      // Load highlight.js once
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

      ensureHighlightLoaded().then(() => {
        targetEl.querySelectorAll("pre code").forEach((block) => {
          if (window.hljs) window.hljs.highlightElement(block);
        });
        addCopyButtons(targetEl);
      });
    })
    .catch((e) => console.error("Failed to load marked.js", e))
    .finally(() => {
      // mark as finalised to prevent duplicate parsing
      targetEl.dataset.finalised = "1";
      _pendingTokenBuffers.delete(targetEl);
      enableShuffle(); // unlock shuffle buttons
      const modal = targetEl.closest('.modal');
      if (modal) {
        _setControlsEnabled(modal, true);
      }
      _debouncedSave(targetEl);
    });

  // Prevent any further reconnects & final UI cleanup
  targetEl.removeAttribute("sse-connect");
  targetEl.removeAttribute("hx-sse-reconnect-delay");
  targetEl.removeAttribute("hx-ext");
  targetEl.removeAttribute("data-batch-ms");

  // NOTE: Active stream counter already decremented above (either in early
  // exit path or in the .finally() block). Do NOT decrement again here – that
  // caused negative counts and premature re-enable. We keep this section as a
  // no-op placeholder in case additional cleanup is needed later.
}

// Debounced transcript saver – avoids frequent writes while typing/rendering
// ---------------------------------------------------------------------------
// _finalizeBubble calls this helper to persist the updated chat log HTML only
// after rendering is complete. We keep a per-container timer so reopening and
// rapid token flushes don’t spam the in-memory cache.

const _saveTimers = new WeakMap(); // chatLogContainer ➜ timeoutId
const MAX_CHAT_LINES = 60;  // history cap when caching transcript – keep last N bubbles

function _debouncedSave(bubbleEl) {
  if (!bubbleEl) return;
  const container = bubbleEl.closest && bubbleEl.closest('.chat-log-container');
  if (!container) return;

  // If any stream is still active (Element with sse-connect or .typing), defer
  // saving except when we were explicitly called for an interrupted bubble.
  const hasActiveStream = container.querySelector('[sse-connect], .assistant-message.typing');
  if (hasActiveStream && !bubbleEl.classList.contains('interrupted')) {
    console.debug('[cache skip]', 'activeStream', !!hasActiveStream, 'bubbleInterrupted', bubbleEl.classList.contains('interrupted'));
    return; // still streaming – come back later.
  }

  console.debug('[cache try]', 'chatKey', container.dataset.chatKey || container.dataset.chatPid || (container.id ? container.id.replace("chat-log-", "") : ""), 'bubbleInterrupted', bubbleEl.classList.contains('interrupted'));

  // Prefer explicit data-paper-id stamped by initChat; fall back to parsing the element id.
  const chatKey = container.dataset.chatKey || container.dataset.chatPid || (container.id ? container.id.replace("chat-log-", "") : "");
  if (!chatKey) return;

  // ---------------------------------------------------------------------
  // Sanitise: for any interrupted bubbles, guarantee they contain ONLY the
  // red stop notice. This prevents earlier streamed tokens from leaking
  // into the cached HTML when multiple debounced saves race.
  // ---------------------------------------------------------------------
  container.querySelectorAll('.assistant-message.interrupted').forEach((msg) => {
    // Unconditionally replace content to ensure stale tokens are purged.
    msg.innerHTML = '<span class="response-stopped"><span class="stopped-x">✖</span> response stopped</span>';
    msg.dataset.finalised = "1";
    // Also strip SSE attributes so the modal no longer matches [sse-connect].
    msg.removeAttribute("sse-connect");
    msg.removeAttribute("hx-sse-reconnect-delay");
    msg.removeAttribute("hx-ext");
    msg.removeAttribute("data-batch-ms");
  });

  clearTimeout(_saveTimers.get(container));
  const t = setTimeout(() => {
    const msgs = Array.from(container.children || []);
    const recent = msgs.slice(-MAX_CHAT_LINES);
    const html = recent.map((n) => n.outerHTML).join("");
    saveTranscript(null, chatKey, html);
  }, 120); // small delay lets DOM settle
  _saveTimers.set(container, t);
}

// ---------------------------------------------------------------------------
// Central helper: enable/disable textarea / submit / stop controls
// ---------------------------------------------------------------------------
function _setControlsEnabled(modal, enabled) {
  if (!modal) return;
  const textarea = modal.querySelector("textarea[name='message']");
  const submitBtn = modal.querySelector("button[type='submit'],input[type='submit']");
  const stopBtn = modal.querySelector('.stop-gen-btn-global');
  if (textarea) {
    textarea.disabled = !enabled;
    if (enabled) textarea.removeAttribute('disabled');
    else textarea.setAttribute('disabled', 'true');
  }
  if (submitBtn) {
    submitBtn.disabled = !enabled;
    if (enabled) submitBtn.removeAttribute('disabled');
    else submitBtn.setAttribute('disabled','true');
  }
  if (stopBtn) {
    stopBtn.disabled = !enabled;
    stopBtn.style.display = enabled ? 'none' : 'inline-block';
    stopBtn.innerHTML = enabled ? '⏹' : '<span class="stop-spinner"></span>';
  }
}

// Backwards-compat shim so existing calls keep working.
function _maybeReleaseControls(modal) {
  _setControlsEnabled(modal, true);
}



// Refocus input and stop HTMX from reconnecting after the stream ends.
if (!window.__luxFinalizeReg) {
  window.__luxFinalizeReg = true;

  const _luxFinalizeHandler = (evt) => {
    const targetEl = evt.detail?.elt;
    if (!targetEl || !targetEl.classList.contains("assistant-message")) return;

    // Prevent HTMX attempting to reconnect; close the source explicitly.
    const src = evt.detail.source;
    if (src && typeof src.close === "function") {
      dbg("closing EventSource", src);
      // slight delay so HTMX finishes wiring listeners; prevents missing sseClose
      setTimeout(() => src.close(), 50);
    }

    evt.preventDefault();

    _finalizeBubble(targetEl);

    // Focus input for next turn
    const inp = document.getElementById("chat-input");
    if (inp instanceof HTMLElement) inp.focus();
  };

  // Capture phase so this fires *before* child hx_on handlers (e.g., saveTranscript)
  document.body.addEventListener("htmx:sseClose", _luxFinalizeHandler, true);
}

// Handle connection errors by force-closing the source & cleaning up so the server 
// doesn’t remain in a perpetually "busy" state due to reconnection attempts from the extension.
['sseDone','sseClose'].forEach(evtName =>
  document.body.addEventListener(`htmx:${evtName}`, ev => {
    const el = ev.detail.elt;
    if (el?.classList.contains('assistant-message')) _finalizeBubble(el);
  }));

// Handle connection errors by force-closing the source & cleaning up so the
// server doesn’t remain in a perpetually "busy" state due to reconnection
// attempts from the extension.
document.body.addEventListener("htmx:sseError", (evt) => {
  const src = evt.detail?.source;
  if (src && typeof src.close === "function") {
    dbg("closing EventSource", src);
    // slight delay so HTMX finishes wiring listeners; prevents missing sseClose
    setTimeout(() => src.close(), 50);
  }

  const el = evt.detail?.elt;

  // Flush & finalise even on error so raw markdown does not remain.
  if (el?.classList.contains("assistant-message")) {
    const entry = _pendingTokenBuffers.get(el);
    // Treat as interrupted ONLY when *no* markdown has ever flushed.
    // That is: we have no pending entry, OR entry.first is still true *and*
    // no markdown accumulated yet. If tokens have streamed (entry.first === false
    // or entry.md has length), we let normal finalisation render them.
    if (!entry || (entry.first && (!entry.md || entry.md.length === 0))) {
      el.dataset.interrupted = "1";
      el.classList.add("interrupted");
      const caret = el.querySelector(".caret");
      if (caret) caret.remove();
      el.classList.remove("typing");
      el.innerHTML =
        '<span class="response-stopped"><span class="stopped-x">✖</span> response stopped</span>';
    }
    _finalizeBubble(el);
  }

  // Do NOT strip attributes here – let HTMX fire htmx:sseClose so
  // _finalizeBubble and _maybeReleaseControls run reliably.

  // _finalizeBubble already re-enabled the form controls. Just make sure the
  // global stop button is hidden (it may still be visible if we aborted very
  // quickly).
  const { stopBtn } = _getModalParts(el || {});
  if (stopBtn) stopBtn.style.display = "none";
});

// Debug trace for SSE lifecycle
['sseClose','sseDone','sseError'].forEach(evtName =>
  document.body.addEventListener(`htmx:${evtName}`, e => dbg(evtName, e.detail.elt))
);

document.body.addEventListener("htmx:beforeRequest", (evt) => {
  const form = evt.target;
  if (!(form instanceof HTMLFormElement)) return;
  if (form.getAttribute("hx-post") !== "/chat") return;
  const textarea = form.querySelector("textarea[name='message']");
  const trimmed = (textarea?.value || "").trim();
  if (trimmed.length === 0) {
    // Cancel HTMX request for empty message
    evt.preventDefault();
    if (textarea) textarea.focus();
    return;
  }
  const modal = form.closest('.modal');
  const activeStream = modal?.querySelector('[sse-connect]');
  dbg("beforeRequest", { activeStream, textareaDisabled: textarea?.disabled });
  if (activeStream) {
    evt.preventDefault();
    if (textarea) textarea.focus();
    // controls remain disabled, no need for toast
    return;
  }
  // Ensure stop button exists, then disable all controls via central helper
  _getModalParts(modal); // inserts stopBtn if missing
  _setControlsEnabled(modal, false);
});

// ---------------------------------------------------------------------------
// Phase 5 – Citation Hover Cards & Jump-to-Source
// ---------------------------------------------------------------------------
const citationCardCache = new Map();
let tooltipDiv = document.getElementById("citation-tooltip");
if (!tooltipDiv) {
  tooltipDiv = document.createElement("div");
  tooltipDiv.id = "citation-tooltip";
  tooltipDiv.style.position = "absolute";
  tooltipDiv.style.display = "none";
  tooltipDiv.style.zIndex = "9999";
  tooltipDiv.style.pointerEvents = "none";
  document.body.appendChild(tooltipDiv);
}

function _positionTooltip(ev) {
  const pad = 12;
  tooltipDiv.style.left = `${ev.pageX + pad}px`;
  tooltipDiv.style.top = `${ev.pageY + pad}px`;
}

// Delegated hover
let hoverTimer = null;
document.body.addEventListener("mouseover", (ev) => {
  const sup = ev.target.closest("[data-paper-id]");
  if (!sup) return;
  const paperId = sup.dataset.paperId;
  // Debounce to avoid flicker when quickly passing over citations
  hoverTimer = setTimeout(async () => {
    let html = citationCardCache.get(paperId);
    if (!html) {
      try {
        const resp = await fetch(`/papers/${paperId}/card`);
        if (!resp.ok) return;
        html = await resp.text();
        citationCardCache.set(paperId, html);
      } catch (e) {
        return;
      }
    }
    tooltipDiv.innerHTML = html;
    tooltipDiv.style.display = "block";
    _positionTooltip(ev);
  }, 150);
});

document.body.addEventListener("mousemove", (ev) => {
  if (tooltipDiv.style.display === "block") {
    _positionTooltip(ev);
  }
});

document.body.addEventListener("mouseout", (ev) => {
  if (hoverTimer) {
    clearTimeout(hoverTimer);
    hoverTimer = null;
  }
  if (ev.target.closest && ev.target.closest("[data-paper-id]")) {
    tooltipDiv.style.display = "none";
  }
});

// Click to jump to paper in list and pulse highlight
function _pulse(el) {
  el.animate([{ background: "#fffbcc" }, { background: "" }], {
    duration: 800,
  });
}

document.body.addEventListener("click", (ev) => {
  const cite = ev.target.closest("[data-paper-id]");
  if (!cite) return;
  const pid = cite.dataset.paperId;
  const listEl = document.getElementById(`paper-${pid}`);
  if (listEl) {
    listEl.scrollIntoView({ behavior: "smooth", block: "center" });
    _pulse(listEl);
  }
});

// ---------------------------------------------------------------------------
// Phase 3-B – Session restore
// ---------------------------------------------------------------------------
function initChat(sessionId, paperId) {
  const chatLog = document.getElementById(`chat-log-${paperId}`);
  if (!chatLog) {
    console.error("Chat log container not found for paper:", paperId);
    return;
  }

  // If this modal was opened before and then hidden, the chat log already
  // contains prior bubbles (server-injected or cached). We leave them in place
  // unless we have a newer cached copy – in that case we replace the DOM to
  // ensure consistency (e.g., interrupted sanitisation done post-hoc).
  const stableKey = chatLog.dataset.chatKey || paperId;
  const transcriptHtml = loadTranscript(sessionId, stableKey);

  if (typeof transcriptHtml === "string" && transcriptHtml.trim()) {
    chatLog.innerHTML = transcriptHtml;

    // Extra safety: re-sanitise any interrupted bubbles that may still contain
    // stale streamed tokens from a previous session before we hardened the
    // stop logic. This guarantees the cached HTML we just inserted shows only
    // the red stop notice and no partial answer.
    chatLog.querySelectorAll('.assistant-message.interrupted').forEach((msg) => {
      msg.innerHTML = '<span class="response-stopped"><span class="stopped-x">✖</span> response stopped</span>';
      msg.dataset.finalised = "1";
    });

    // Persist the cleaned transcript back to cache so future opens are safe.
    _debouncedSave(chatLog.lastElementChild || chatLog);

    // DEBUG: log what we just injected
    try {
      console.debug("[cache load]", paperId, transcriptHtml.substring(0, 200).replace(/\n/g, "↩"));
    } catch (_) {}

    _renderStaticMarkdown(chatLog);

    // scroll to newest
    chatLog.scrollTop = chatLog.scrollHeight;
  } else {
    // Fresh chat with no cached transcript → still upgrade any static md
    _renderStaticMarkdown(chatLog);
  }
}

// ---------------------------------------------------------------------------
// UX  – Submit on Enter (Shift+Enter for newline)
// ---------------------------------------------------------------------------
document.addEventListener("keydown", (ev) => {
  if (ev.key === "Escape") {
    // Esc acts as stop shortcut
    const { stopBtn } = _getModalParts(ev.target);
    if (stopBtn && stopBtn.style.display !== "none") stopBtn.click();
    return;
  }
  if (ev.key !== "Enter" || ev.shiftKey) return;
  const ta = ev.target;
  if (!(ta instanceof HTMLTextAreaElement)) return;
  if (ta.disabled) return;

  const form = ta.closest("form");
  if (!form || form.getAttribute("hx-post") !== "/chat") return;

  ev.preventDefault();
  // htmx 1.9 automatically picks up requestSubmit
  if (typeof form.requestSubmit === "function") {
    form.requestSubmit();
  } else {
    form.dispatchEvent(new Event("submit", { cancelable: true }));
  }
});

// ---------------------------------------------------------------------------
// Clear textarea after HTMX sends /chat request (works for both Enter and button)
// ---------------------------------------------------------------------------
document.body.addEventListener("htmx:afterRequest", (evt) => {
  const elt = evt.target;
  if (elt instanceof HTMLFormElement && elt.getAttribute("hx-post") === "/chat") {
    const ta = elt.querySelector("textarea[name='message']");
    if (ta) ta.value = "";

    // Auto-scroll container to bottom after user message sent
    const modal = elt.closest(".modal");
    const chatLog = modal && modal.querySelector(".chat-log-container");
    if (chatLog) chatLog.scrollTop = chatLog.scrollHeight;
  }
});

// ---------------------------------------------------------------------------
// Kick off typing indicator immediately after HTMX swaps in assistant bubble
// ---------------------------------------------------------------------------
document.body.addEventListener("htmx:afterSwap", (evt) => {
  const root = evt.detail?.target;
  if (!(root instanceof HTMLElement)) return;
  // Find any newly inserted assistant-message.typing elements without entry
  root.querySelectorAll(".assistant-message.typing").forEach((el) => {
    if (!_pendingTokenBuffers.has(el) && !el.classList.contains("interrupted")) {
      const batchMsVal = parseInt(el.dataset.batchMs || "20", 10);
      _startTypingIndicator(el, batchMsVal);
    }
  });

  // Kick off chat restore for newly swapped chat modals if inline hx_on failed
  root.querySelectorAll(".chat-log-container").forEach((log) => {
    if (log.dataset.initDone === "1") return;
    const pid = log.dataset.chatKey || log.dataset.chatPid || (log.id ? log.id.replace("chat-log-", "") : "");
    if (!pid) return;
    try {
      initChat(null, pid);
      log.dataset.initDone = "1";
    } catch (err) {
      console.error("initChat fallback error", err);
    }
  });

  // If a modal was just inserted, add extra UI (export, autosize)
  const modal = root.closest && root.closest(".modal");
  if (modal) enhanceModal(modal);
});

// ---------------------------------------------------------------------------
// Helpers added for code-copy, highlight, dark-mode, export, autosize
// ---------------------------------------------------------------------------
function addCopyButtons(container) {
  container.querySelectorAll("pre").forEach((pre) => {
    if (pre.querySelector(".copy-btn")) return; // already added
    const btn = document.createElement("button");
    btn.className = "copy-btn hidden";
    btn.type = "button";
    btn.title = "Copy code";
    btn.textContent = "📋";
    btn.addEventListener("click", () => {
      navigator.clipboard.writeText(pre.innerText).then(() => {
        btn.textContent = "✔";
        setTimeout(() => (btn.textContent = "📋"), 1500);
      });
    });
    pre.style.position = "relative";
    pre.appendChild(btn);
  });
}

function toggleDark() {
  const isDark = document.body.classList.toggle("lux-dark");
}

function exportChat(modal) {
  const reportEl = document.getElementById("report-content");
  if (!reportEl) return;

  // Clone so we can safely remove interactive UI bits before extracting text
  const clone = reportEl.cloneNode(true);
  clone.querySelectorAll("#copy-report-btn, .ask-assistant-btn").forEach((el) => el.remove());

  // Collect headings & paragraph-like elements in document order so we keep
  // the same visible spacing. Each gets joined by two newlines.
  const paraSelector = [
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "p",
    "li",
    "blockquote",
    "pre",
  ].join(",");

  const linesArr = Array.from(clone.querySelectorAll(paraSelector))
    .map((el) => el.innerText.replace(/\s+$/g, "").replace(/^\s+/g, ""))
    .filter(Boolean);

  let txt = linesArr.join("\n\n");

  // Fallback: if above fails (empty), use full innerText.
  if (!txt) txt = clone.innerText;

  // Strip any leftover Ask-Assistant label + collapse >2 blanks into exactly 2.
  txt = txt.replace(/\n?\s*Ask Assistant About This Paper\s*\n?/g, "\n\n").replace(/\n{3,}/g, "\n\n").trim();

  function _fallbackCopy(text) {
    // Use a hidden div with <br> so execCommand copies rich text & preserves line breaks
    const div = document.createElement("div");
    div.style.position = "fixed";
    div.style.pointerEvents = "none";
    div.style.opacity = "0";
    div.style.left = "-9999px";
    div.innerHTML = text.replace(/\n/g, "<br>");
    document.body.appendChild(div);

    const range = document.createRange();
    range.selectNodeContents(div);
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);

    try {
      const ok = document.execCommand("copy");
      console.info(`[copy-report] Copied via fallback (execCommand) – ${ok}`);
    } catch (err) {
      console.error("[copy-report] Fallback copy failed", err);
    }

    selection.removeAllRanges();
    document.body.removeChild(div);
  }

  async function copyAsHtmlAndText(textPlain) {
    if (!navigator.clipboard || typeof navigator.clipboard.write !== "function") {
      return Promise.reject(new Error("Clipboard.write unsupported"));
    }
    const html = textPlain.replace(/\n/g, "<br>");
    const blobPlain = new Blob([textPlain], { type: "text/plain" });
    const blobHtml = new Blob([html], { type: "text/html" });
    const item = new ClipboardItem({ "text/plain": blobPlain, "text/html": blobHtml });
    return navigator.clipboard.write([item]);
  }

  console.log("[copy-report] preview →", JSON.stringify(txt.slice(0, 500)));
  if (!txt) {
    console.warn("[copy-report] Empty report text – nothing copied.");
    return;
  }

  if (navigator.clipboard && typeof navigator.clipboard.write === "function") {
    copyAsHtmlAndText(txt)
      .then(() => {
        console.info(
          `[copy-report] Copied report text – ${txt.length} chars (truncated)`
        );
      })
      .catch((err) => {
        console.error("[copy-report] Clipboard API failed", err);
        _fallbackCopy(txt);
      })
      .finally(() => {
        const btn = document.getElementById("copy-report-btn");
        if (btn) {
          btn.classList.add("copied");
          setTimeout(() => btn.classList.remove("copied"), 1200);
        }
      });
  } else if (navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
    navigator.clipboard.writeText(txt)
      .then(() => {
        console.info(
          `[copy-report] Copied report text – ${txt.length} chars (truncated)`
        );
      })
      .catch((err) => {
        console.error("[copy-report] Clipboard API failed", err);
        _fallbackCopy(txt);
      })
      .finally(() => {
        const btn = document.getElementById("copy-report-btn");
        if (btn) {
          btn.classList.add("copied");
          setTimeout(() => btn.classList.remove("copied"), 1200);
        }
      });
  } else {
    _fallbackCopy(txt);
    const btn = document.getElementById("copy-report-btn");
    if (btn) {
      btn.classList.add("copied");
      setTimeout(() => btn.classList.remove("copied"), 1200);
    }
  }
}

function attachAutoResize(ta) {
  const resize = () => {
    ta.style.height = "auto";
    ta.style.height = Math.min(ta.scrollHeight, 240) + "px";
  };
  ta.addEventListener("input", resize);
  resize();
}

function enhanceModal(modal) {
  // Transcript download button removed per UX feedback
  const header = modal.querySelector(".modal-header");
  /* no-op: export button removed */

  const ta = modal.querySelector("textarea[name='message']");
  if (ta && !ta.dataset.autosize) {
    ta.dataset.autosize = "1";
    attachAutoResize(ta);
  }
}

// Inject caret & copy button styles once
(function () {
  if (document.getElementById("lux-extra-styles")) return;
  const style = document.createElement("style");
  style.id = "lux-extra-styles";
  style.textContent = `
    .caret { font-weight: 400; }
    .copy-btn { position: absolute; top:4px; right:4px; border:none; background:transparent; cursor:pointer; }
    .copy-btn:hover { color:#ef4444; }
    .export-btn { background:transparent; border:none; cursor:pointer; margin-left:6px; }
    .response-stopped { font-style: italic; color:#6b7280; }
    .response-stopped .stopped-x { color:#ef4444; margin-right:4px; }
    .stop-spinner { width:16px;height:16px;border:2px solid #d1d5db;border-top-color:#ef4444;border-radius:50%;animation:lux-spin 0.8s linear infinite;display:inline-block;vertical-align:middle; }
    @keyframes lux-spin { to { transform: rotate(360deg); } }
  `;
  document.head.appendChild(style);
})();

document.body.addEventListener("click", async (ev) => {
  const stopBtn = ev.target.closest(".stop-gen-btn-global");
  if (!stopBtn) return;

  dbg("STOP click");

  // mark global stream state as stopping – controls already disabled
  const modalEl = stopBtn.closest(".modal");

  // Close active EventSources and flush pending entries
  const streams = stopBtn.closest(".modal").querySelectorAll("[sse-connect]");
  streams.forEach((el) => {
    if (el._htmx_source && typeof el._htmx_source.close === "function") {
      dbg("closing EventSource", el);
      // slight delay so HTMX finishes wiring listeners; prevents missing sseClose
      setTimeout(() => el._htmx_source.close(), 50);
    }
    // Do NOT strip attributes here – let HTMX fire htmx:sseClose so
    // _finalizeBubble and _maybeReleaseControls run reliably.
  });

  for (const [node, ent] of _pendingTokenBuffers.entries()) {
    if (!stopBtn.closest(".modal").contains(node)) continue;
    _flushTokens(node);
    // Replace content with stopped notice
    node.innerHTML = '<span class="response-stopped"><span class="stopped-x">✖</span> response stopped</span>';
    // mark as interrupted so _finalizeBubble() can skip markdown rendering
    node.dataset.interrupted = "1";
    _pendingTokenBuffers.delete(node);
    // Clear any ongoing typing animation so dots stop blinking.
    if (ent && ent.typingTimer) {
      clearInterval(ent.typingTimer);
      ent.typingTimer = null;
    }
    const caretEl = node.querySelector(".caret");
    if (caretEl) caretEl.remove();

    node.classList.add("interrupted");
    node.classList.remove("typing");

    // Explicitly finalise here in case no SSE event fires (e.g. Stop pressed
    // before EventSource established). This will strip SSE attrs, decrement
    // __activeStreams and potentially re-enable controls when count hits 0.
    _finalizeBubble(node);

    // Persist updated chat log so modal reopen shows stopped state.
    _debouncedSave(node);
  }

  // Also cover bubbles that are still in the DOM as `.typing` but never
  // entered `_pendingTokenBuffers` because the backend hadn’t emitted a
  // first token yet (common when paper excerpt fetch fails or the user
  // stops very quickly). These would otherwise be left “stuck” and cause
  // the next request to inherit a ghost typing indicator.
  const orphanTyping = stopBtn.closest(".modal").querySelectorAll(
    ".assistant-message.typing"
  );
  orphanTyping.forEach((node) => {
    if (_pendingTokenBuffers.has(node)) return; // already handled above

    _flushTokens(node);
    node.innerHTML = '<span class="response-stopped"><span class="stopped-x">✖</span> response stopped</span>';
    node.dataset.interrupted = "1";

    const caretEl = node.querySelector(".caret");
    if (caretEl) caretEl.remove();

    node.classList.add("interrupted");
    node.classList.remove("typing");

    // Persist transcript right away for orphan typing bubbles as well.
    _debouncedSave(node);
  });

  // Controls are already locked in _setControlsEnabled(false); just show spinner.
  stopBtn.disabled = true;
  stopBtn.innerHTML = '<span class="stop-spinner"></span>';

  
});

// ---------------------- Copy Report ----------------------------
document.body.addEventListener("click", (ev) => {
  const btn = ev.target.closest("#copy-report-btn");
  if (!btn) return;
  const reportEl = document.getElementById("report-content");
  if (!reportEl) return;

  // Clone so we can safely remove interactive UI bits before extracting text
  const clone = reportEl.cloneNode(true);
  clone.querySelectorAll("#copy-report-btn, .ask-assistant-btn").forEach((el) => el.remove());

  const paraSelector = [
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "p",
    "li",
    "blockquote",
    "pre",
  ].join(",");

  const linesArr2 = Array.from(clone.querySelectorAll(paraSelector))
    .map((el) => el.innerText.replace(/\s+$/g, "").replace(/^\s+/g, ""))
    .filter(Boolean);
  let txt = linesArr2.join("\n\n");
  if (!txt) txt = clone.innerText;
  txt = txt.replace(/\n?\s*Ask Assistant About This Paper\s*\n?/g, "\n\n").replace(/\n{3,}/g, "\n\n").trim();

  function _fallbackCopy(text) {
    // Use a hidden div with <br> so execCommand copies rich text & preserves line breaks
    const div = document.createElement("div");
    div.style.position = "fixed";
    div.style.pointerEvents = "none";
    div.style.opacity = "0";
    div.style.left = "-9999px";
    div.innerHTML = text.replace(/\n/g, "<br>");
    document.body.appendChild(div);

    const range = document.createRange();
    range.selectNodeContents(div);
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);

    try {
      const ok = document.execCommand("copy");
      console.info(`[copy-report] Copied via fallback (execCommand) – ${ok}`);
    } catch (err) {
      console.error("[copy-report] Fallback copy failed", err);
    }

    selection.removeAllRanges();
    document.body.removeChild(div);
  }

  async function copyAsHtmlAndText(textPlain) {
    if (!navigator.clipboard || typeof navigator.clipboard.write !== "function") {
      return Promise.reject(new Error("Clipboard.write unsupported"));
    }
    const html = textPlain.replace(/\n/g, "<br>");
    const blobPlain = new Blob([textPlain], { type: "text/plain" });
    const blobHtml = new Blob([html], { type: "text/html" });
    const item = new ClipboardItem({ "text/plain": blobPlain, "text/html": blobHtml });
    return navigator.clipboard.write([item]);
  }

  console.log("[copy-report] preview →", JSON.stringify(txt.slice(0, 500)));
  if (!txt) {
    console.warn("[copy-report] Empty report text – nothing copied.");
    return;
  }

  if (navigator.clipboard && typeof navigator.clipboard.write === "function") {
    copyAsHtmlAndText(txt)
      .then(() => {
        console.info(
          `[copy-report] Copied report text – ${txt.length} chars (truncated)`
        );
      })
      .catch((err) => {
        console.error("[copy-report] Clipboard API failed", err);
        _fallbackCopy(txt);
      })
      .finally(() => {
        btn.classList.add("copied");
        setTimeout(() => btn.classList.remove("copied"), 1200);
      });
  } else if (navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
    navigator.clipboard.writeText(txt)
      .then(() => {
        console.info(
          `[copy-report] Copied report text – ${txt.length} chars (truncated)`
        );
      })
      .catch((err) => {
        console.error("[copy-report] Clipboard API failed", err);
        _fallbackCopy(txt);
      })
      .finally(() => {
        btn.classList.add("copied");
        setTimeout(() => btn.classList.remove("copied"), 1200);
      });
  } else {
    _fallbackCopy(txt);
    btn.classList.add("copied");
    setTimeout(() => btn.classList.remove("copied"), 1200);
  }
});

document.body.addEventListener("htmx:sseMessage", (evt) => {
  if (evt.detail && evt.detail.type === "done") {
    document
      .querySelectorAll(
        ".hidden.copy-btn, .copy-btn.hidden, .hidden.copy-icon-btn, .copy-icon-btn.hidden"
      )
      .forEach((btn) => {
        btn.classList.remove("hidden");
      });
    enableShuffle(); // unlock shuffle buttons
  }
});

// ---------------------------------------------------------------------------
// Utility: upgrade stored plain-text markdown bubbles to HTML
// ---------------------------------------------------------------------------
function _renderStaticMarkdown(container) {
  if (!container) return;

  const elems = container.querySelectorAll(".assistant-message");
  if (!elems.length) return;

  _ensureMarkdownLibs().then(() => {
    elems.forEach((el) => {
      if (el.dataset.finalised === "1") {
        const raw = el.textContent || "";
        if (!/[\*`_\[\]]/.test(raw)) return; // looks like proper HTML already
        // else, clear the flag so we proceed to render it now.
        delete el.dataset.finalised;
      }
      // Handle interrupted bubbles: ensure we wipe any partial markdown and inject the stop notice once.
      if (el.dataset.interrupted === "1" || el.classList.contains("interrupted")) {
        if (!el.querySelector('.response-stopped')) {
          el.innerHTML = "";
          const wrapper = document.createElement("span");
          wrapper.className = "response-stopped";
          const x = document.createElement("span");
          x.className = "stopped-x";
          x.textContent = "✖";
          wrapper.appendChild(x);
          wrapper.appendChild(document.createTextNode(" response stopped"));
          el.appendChild(wrapper);
        }
        el.dataset.finalised = "1";
        return;
      }

      let md = el.textContent || "";
      const fenceCt = (md.match(/```/g) || []).length;
      if (fenceCt % 2 === 1) md += "\n```";

      const dirty = window.marked.parse(md, {
        gfm: true,
        breaks: true,
        smartLists: true,
      });

      const safe = window.DOMPurify ? window.DOMPurify.sanitize(dirty) : dirty;
      el.innerHTML = safe;

      if (window.hljs) {
        el.querySelectorAll("pre code").forEach((block) => window.hljs.highlightElement(block));
      }
    });

    // After upgrading markdown, save updated transcript so reopening modal shows rendered HTML
    if (elems.length) {
      _debouncedSave(elems[elems.length - 1]);
    }
  });
}
