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
// Phase 2.1: Clear report areas when shuffle/reset buttons are clicked
// --------------------------------------------------

function clearReportAreas() {
  const configArea = document.getElementById("report-config-area");
  const displayArea = document.getElementById("report-display-area");
  if (configArea) configArea.innerHTML = "";
  if (displayArea) displayArea.innerHTML = "";
}

// Delegate click handling so it works even after HTMX swaps
document.addEventListener("click", (evt) => {
  const tgt = evt.target;
  if (!(tgt instanceof HTMLElement)) return;
  const id = tgt.id;
  if (id === "shuffle-btn" || id === "shuffle-all-btn" || id === "reset-btn") {
    clearReportAreas();
  }
});

// Set up Lux dark-mode toggle
function setupLuxDarkToggle() {
  const toggle = document.getElementById("lux-dark-toggle");
  if (!toggle) return;
  // Ensure label matches current theme on first paint
  if (document.body.classList.contains("lux-dark")) {
    toggle.textContent = "☀️ Light Mode";
  } else {
    toggle.textContent = "🌙 Dark Mode";
  }
  toggle.addEventListener("click", () => {
    console.log("Dark mode toggle clicked!");
    const isDarkMode = document.body.classList.contains("lux-dark");
    if (isDarkMode) {
      document.body.classList.remove('lux-dark');
      document.body.style.backgroundColor = '';
    } else {
      document.body.classList.add('lux-dark');
      document.body.style.backgroundColor = '#1a1a1a';
    }

    if (document.body.classList.contains("lux-dark")) {
      toggle.textContent = '☀️ Light Mode';
    } else {
      toggle.textContent = '🌙 Dark Mode';
    }
  });
}

document.addEventListener("DOMContentLoaded", setupLuxDarkToggle);

(function () {
  // Ensure clean start (light)
  document.body.classList.remove("lux-dark");
})();

// ---------------------------------------------------------------------------
// Phase 3-A: localStorage compression helpers
// --------------------------------------------------

// Use LZString if loaded, otherwise fallback to plain text.
function compressMaybe(str) {
  if (!str || typeof LZString === "undefined") {
    return "raw:" + str;
  }
  // Compress if it results in a smaller string, otherwise store raw.
  const compressed = LZString.compressToBase64(str);
  return compressed.length < str.length ? "lz:" + compressed : "raw:" + str;
}

function decompressMaybe(stored) {
  if (!stored) return null;
  if (stored.startsWith("lz:")) {
    try {
      return LZString.decompressFromBase64(stored.slice(3));
    } catch (_) {
      return null; // Decompression failed
    }
  }
  if (stored.startsWith("raw:")) return stored.slice(4);
  return stored; // Fallback for old data without prefix
}

// Save / load transcript for given paper + session
function saveTranscript(sessionId, paperId, html) {
  const key = `lux:chat:${sessionId}:${paperId}`;
  const payload = {
    ts: Date.now(),
    data: compressMaybe(html),
  };

  try {
    localStorage.setItem(key, JSON.stringify(payload));
  } catch (e) {
    console.warn("LocalStorage quota exceeded. Evicting oldest items.");
    // Quota exceeded – simple LRU: find all chat keys, sort by timestamp, remove oldest 5, retry.
    const keysToEvict = [];
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i);
      if (k && k.startsWith("lux:chat:")) {
        try {
          const item = JSON.parse(localStorage.getItem(k));
          if (item && item.ts) keysToEvict.push({ key: k, ts: item.ts });
        } catch (_) {}
      }
    }
    keysToEvict.sort((a, b) => a.ts - b.ts);
    for (let i = 0; i < Math.min(5, keysToEvict.length); i++) {
      localStorage.removeItem(keysToEvict[i].key);
    }

    // Retry once
    try {
      localStorage.setItem(key, JSON.stringify(payload));
    } catch (e2) {
      alert("Local storage full — older chats were purged to free space.");
      console.error("Failed to save transcript even after eviction:", e2);
    }
  }
}

function loadTranscript(sessionId, paperId) {
  const key = `lux:chat:${sessionId}:${paperId}`;
  try {
    const stored = localStorage.getItem(key);
    if (!stored) return null;
    const payload = JSON.parse(stored);
    return decompressMaybe(payload.data);
  } catch (e) {
    console.error("Failed to load/parse transcript:", e);
    return null;
  }
}

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
  _flushTokens(targetEl);
  const entry = _pendingTokenBuffers.get(targetEl);
  if (entry && entry.typingTimer) {
    clearInterval(entry.typingTimer);
    entry.typingTimer = null;
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
      if (targetEl.dataset.interrupted === "1" || targetEl.classList.contains("interrupted")) {
        _pendingTokenBuffers.delete(targetEl);

        targetEl.removeAttribute("sse-connect");
        targetEl.removeAttribute("hx-sse-reconnect-delay");
        targetEl.removeAttribute("hx-ext");
        targetEl.removeAttribute("data-batch-ms");

        const { textarea, submitBtn, stopBtn } = _getModalParts(targetEl);
        if (textarea) textarea.removeAttribute("disabled");
        if (submitBtn) submitBtn.removeAttribute("disabled");
        if (stopBtn) {
          stopBtn.disabled = false;
          stopBtn.innerHTML = "⏹";
          stopBtn.style.display = "none";
        }

        targetEl.dataset.finalised = "1";
        return;
      }

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
    .catch((e) => console.error("Failed to load marked.js", e));

  _pendingTokenBuffers.delete(targetEl);

  // Prevent any further reconnects & final UI cleanup
  targetEl.removeAttribute("sse-connect");
  targetEl.removeAttribute("hx-sse-reconnect-delay");
  targetEl.removeAttribute("hx-ext");
  targetEl.removeAttribute("data-batch-ms");

  const { textarea, submitBtn, stopBtn } = _getModalParts(targetEl);
  if (textarea) textarea.removeAttribute("disabled");
  if (submitBtn) submitBtn.removeAttribute("disabled");
  if (stopBtn) {
    stopBtn.disabled = false;
    stopBtn.innerHTML = "⏹";
    stopBtn.style.display = "none";
  }

  // mark as finalised to prevent duplicate parsing
  targetEl.dataset.finalised = "1";
}

function _maybeReleaseControls(modal) {
  if (!modal) return;
  if (modal.querySelector('[sse-connect]')) return; // still stopping

  const textarea = modal.querySelector("textarea[name='message']");
  const submitBtn = modal.querySelector("button[type='submit'],input[type='submit']");
  const stopBtn = modal.querySelector('.stop-gen-btn-global');

  if (textarea) textarea.removeAttribute('disabled');
  if (submitBtn) submitBtn.removeAttribute('disabled');
  if (stopBtn) {
    stopBtn.disabled = false;
    stopBtn.innerHTML = '⏹';
    stopBtn.style.display = 'none';
  }
}

// Refocus input and stop HTMX from reconnecting after the stream ends.
document.body.addEventListener("htmx:sseClose", (evt) => {
  const targetEl = evt.detail.elt;
  // Ensure we’re only acting on the assistant message bubble from the chat
  if (!targetEl || !targetEl.classList.contains("assistant-message")) return;

  // 0. Explicitly close the EventSource so the browser does **not** reconnect
  const src = evt.detail.source;
  if (src && typeof src.close === "function") {
    try {
      src.close();
    } catch (_) {}
  }

  evt.preventDefault();

  _finalizeBubble(targetEl);
  // Refocus the chat input for the next message
  const inp = document.getElementById("chat-input");
  if (inp instanceof HTMLElement) inp.focus();
});

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

  // Flush & finalise even on error so raw markdown does not remain
  if (el?.classList.contains("assistant-message")) {
    // If no tokens ever arrived, there is no entry in _pendingTokenBuffers.
    // Treat this the same as a manual Stop so we don’t leave a ghost typing bubble.
    if (!_pendingTokenBuffers.has(el)) {
      el.dataset.interrupted = "1";
      const caret = el.querySelector(".caret");
      if (caret) caret.remove();
      el.classList.remove("typing");
      el.innerHTML =
        '<span class="response-stopped"><span class="stopped-x">✖</span> response stopped</span>';
    }
    _finalizeBubble(el);
  }

  if (el) {
    el.removeAttribute("sse-connect");
    el.removeAttribute("hx-ext");
    el.removeAttribute("data-batch-ms");
  }

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

// Constant for Stop grace delay (ms)
const STOP_GRACE_MS = 2000;

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
  const submitBtn = form.querySelector("button[type='submit'],input[type='submit']");
  const stopBtn = form.querySelector(".stop-gen-btn-global");
  if (textarea) textarea.setAttribute("disabled", "true");
  if (submitBtn) submitBtn.setAttribute("disabled", "true");
  if (stopBtn) stopBtn.style.display = "inline-block";
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

  const transcriptData = loadTranscript(sessionId, paperId);

  if (transcriptData && transcriptData.data) {
    const toast = document.createElement("div");
    toast.id = `restore-toast-${paperId}`;
    toast.className = "restore-toast";
    toast.innerHTML = `
      <span style="margin-right: 10px;">Restore previous chat?</span>
      <button id="restore-yes-${paperId}" class="toast-btn">Yes</button>
      <button id="restore-no-${paperId}" class="toast-btn">No</button>
    `;

    chatLog.prepend(toast);

    document.getElementById(`restore-yes-${paperId}`).addEventListener("click", () => {
      chatLog.innerHTML = transcriptData.data;
      // After restoring, make sure the view is scrolled to the bottom
      chatLog.scrollTop = chatLog.scrollHeight;
    });

    document.getElementById(`restore-no-${paperId}`).addEventListener("click", () => {
      toast.remove();
    });
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
    btn.className = "copy-btn";
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

  // Close active EventSources and flush pending entries
  const streams = stopBtn.closest(".modal").querySelectorAll("[sse-connect]");
  streams.forEach((el) => {
    if (el._htmx_source && typeof el._htmx_source.close === "function") {
      dbg("closing EventSource", el);
      // slight delay so HTMX finishes wiring listeners; prevents missing sseClose
      setTimeout(() => el._htmx_source.close(), 50);
    }
    // Do NOT strip the sse-connect/hx-ext attributes here; leaving them
    // intact lets the HTMX SSE extension fire the `htmx:sseClose` event once
    // it detects the connection closed. `_finalizeBubble()` will remove the
    // attributes after it finishes, so we avoid a race that left controls
    // disabled indefinitely.
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

    // Do NOT call _finalizeBubble() here; we must wait until the server
    // reports the stream is closed (via sseError/sseClose) to avoid a
    // race where the next request is sent while the backend still has
    // stream_active=True.
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
    node.innerHTML =
      '<span class="response-stopped"><span class="stopped-x">✖</span> response stopped</span>';
    node.dataset.interrupted = "1";

    const caretEl = node.querySelector(".caret");
    if (caretEl) caretEl.remove();

    node.classList.add("interrupted");
    node.classList.remove("typing");
  });

  // Keep controls disabled until we receive htmx:sseClose/_finalizeBubble.
  // Show spinner on stop button until finalisation; do not hide here.
  stopBtn.disabled = true;
  stopBtn.innerHTML = '<span class="stop-spinner"></span>';

  // Re-enable textarea & submit after a short grace period so the backend
  // has time to clear `stream_active` (keep-alive ≤1 s). This restores the
  // form to the ready state without re-introducing the earlier race.
  const { textarea, submitBtn } = _getModalParts(stopBtn);
  setTimeout(() => {
    dbg("grace-timer fired");
    _maybeReleaseControls(stopBtn.closest('.modal'));
  }, STOP_GRACE_MS);
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
