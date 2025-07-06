// chat/ui.js – modal UI helpers & utilities for chat subsystem

import { dbg } from "../util.js";

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

  // Stop button is inserted on-demand (legacy design kept for parity)
  let stopBtn = modal[_stopBtnSym];
  if (!stopBtn) {
    stopBtn = document.createElement("button");
    stopBtn.type = "button";
    stopBtn.textContent = "Stop";
    stopBtn.className = "stop-btn hidden text-sm px-2 py-1 rounded bg-red-600 text-white";
    stopBtn.addEventListener("click", () => {
      const activeStream = modal.querySelector("[sse-connect]");
      if (activeStream) {
        activeStream.dataset.interrupted = "1";
        activeStream.classList.add("interrupted");
        const src = activeStream.__eventSource;
        if (src && typeof src.close === "function") src.close();
      }
      stopBtn.style.display = "none";
    });
    modal.appendChild(stopBtn);
    modal[_stopBtnSym] = stopBtn;
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
  if (stopBtn) stopBtn.style.display = enabled ? "none" : "inline-block";

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
  if (log) scrollToBottom(log);
}

// ---------------------------------------------------------------------------
// One-off style injection for caret, copy-btn, stop spinner, etc.
// ---------------------------------------------------------------------------
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

console.debug("[lux] chat/ui.js loaded");
