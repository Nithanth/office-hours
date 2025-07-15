// chat/markdown.js – Markdown rendering helpers
// Provides lazy-loading of marked.js, DOMPurify, highlight.js and helper
// functions to render markdown safely in chat bubbles.

import { addCopyButtons } from "./ui.js";
import { dbg } from "../util.js";

// Snapshot dark-mode at module load so we don’t query DOM on every call.
const _isDarkMode = document.body.classList.contains("lux-dark");

// ---------------------------------------------------------------------------
// Lazy-loader for marked.js & DOMPurify so we pay the cost only on demand.
// ---------------------------------------------------------------------------
let _mdLibPromise = null;
export function ensureMarkdownLibs() {
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

// ---------------------------------------------------------------------------
// highlight.js loader – picks dark/light theme on the fly.
// ---------------------------------------------------------------------------
let _hlPromise = null;
export function ensureHighlightLoaded() {
  if (window.hljs) return Promise.resolve();
  if (_hlPromise) return _hlPromise;

  _hlPromise = new Promise((resolve) => {
    const css = document.createElement("link");
    css.rel = "stylesheet";
    css.href = `https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github${
      _isDarkMode ? "-dark" : ""
    }.min.css`;
    document.head.appendChild(css);

    const s = document.createElement("script");
    s.src = "https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js";
    s.onload = () => resolve();
    document.head.appendChild(s);
  });
  return _hlPromise;
}

// ---------------------------------------------------------------------------
// Render helpers
// ---------------------------------------------------------------------------
export async function renderMarkdownPartial(el) {
  try {
    await ensureMarkdownLibs();

    let rawMd = el.textContent ?? "";
    // Close any still-open code block fence so marked won't choke.
    const fenceCount = (rawMd.match(/```/g) || []).length;
    if (fenceCount % 2 === 1) rawMd += "\n```";

    const dirtyHtml = window.marked.parse(rawMd, {
      gfm: true,
      breaks: true,
      smartLists: true,
    });
    const safeHtml = window.DOMPurify ? window.DOMPurify.sanitize(dirtyHtml) : dirtyHtml;
    el.innerHTML = safeHtml;

    await ensureHighlightLoaded();
    el.querySelectorAll("pre code").forEach((block) => window.hljs?.highlightElement(block));

    addCopyButtons(el);
  } catch (err) {
    console.error("[lux] markdown partial render failed", err);
  }
}

// Upgrade stored plain-text bubbles to HTML once libs are loaded.
export function renderStaticMarkdown(container) {
  if (!container) return;

  const elems = container.querySelectorAll(".assistant-message");
  if (!elems.length) return;

  ensureMarkdownLibs().then(() => {
    elems.forEach((el) => {
      // If already finalised & looks like HTML, skip.
      if (el.dataset.finalised === "1") {
        const raw = el.textContent || "";
        if (!(/[\*`_\[\]]/.test(raw))) return;
        delete el.dataset.finalised; // fall through for re-render
      }

      // Interrupted bubbles – wipe & inject stop notice.
      if (el.dataset.interrupted === "1" || el.classList.contains("interrupted")) {
        if (!el.querySelector(".response-stopped")) {
          el.innerHTML =
            '<span class="response-stopped"><span class="stopped-x">✖</span> response stopped</span>';
        }
        el.dataset.finalised = "1";
        return;
      }

      // Normal markdown render
      renderMarkdownPartial(el).then(() => {
        el.dataset.finalised = "1";
      });
    });
  });
}

console.debug("[lux] chat/markdown.js loaded");
