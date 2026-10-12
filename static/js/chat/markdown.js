// chat/markdown.js – Markdown rendering helpers
// Provides lazy-loading of marked.js, DOMPurify, highlight.js and helper
// functions to render markdown safely in chat bubbles.

import { addCopyButtons } from "./ui.js?v=2";
import { dbg } from "../util.js?v=2";

// Snapshot dark-mode at module load so we don’t query DOM on every call.
const _isDarkMode = document.body.classList.contains("oh-dark");

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
    css.href = `https://cdn.jsdelivr.net/npm/highlight.js@11.9.0/styles/github${
      _isDarkMode ? "-dark" : ""
    }.min.css`;
    document.head.appendChild(css);

    const s = document.createElement("script");
    s.src = "https://cdn.jsdelivr.net/npm/highlight.js@11.9.0/highlight.min.js";
    let settled = false;
    const done = () => {
      if (settled) return;
      settled = true;
      resolve();
    };
    s.onload = done;
    s.onerror = () => {
      console.warn("[oh] highlight.js failed to load - continuing without syntax highlight");
      done();
    };
    // Fallback: some browsers (e.g. Chrome with CORB) may block the response
    // without triggering onload/onerror. Ensure we proceed regardless.
    setTimeout(done, 3000);
    document.head.appendChild(s);
  });
  return _hlPromise;
}

// ---------------------------------------------------------------------------
// KaTeX loader – lazy-load CSS, core, and auto-render helper.
// ---------------------------------------------------------------------------
let _katexPromise = null;
export function ensureKatexLoaded() {
  if (window.katex && window.renderMathInElement) return Promise.resolve();
  if (_katexPromise) return _katexPromise;

  _katexPromise = new Promise((resolve) => {
    // Inject CSS first so equations don’t flash unstyled
    const css = document.createElement("link");
    css.rel = "stylesheet";
    css.href = "https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.css";
    document.head.appendChild(css);

    // Core KaTeX must load BEFORE auto-render (it depends on window.katex)
    const s = document.createElement("script");
    s.src = "https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.js";
    s.onload = () => {
      // Now load auto-render after katex is available
      const auto = document.createElement("script");
      auto.src = "https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/contrib/auto-render.min.js";
      auto.onload = () => resolve();
      auto.onerror = () => {
        console.warn("[oh] auto-render.min.js failed to load");
        resolve(); // best-effort
      };
      document.head.appendChild(auto);
    };
    s.onerror = () => {
      console.warn("[oh] katex.min.js failed to load");
      resolve(); // best-effort
    };
    document.head.appendChild(s);
  });

  return _katexPromise;
}

// ---------------------------------------------------------------------------
// Render helpers
// ---------------------------------------------------------------------------
export async function renderMarkdownPartial(el) {
  try {
    await ensureMarkdownLibs();

    let rawMd = el.textContent ?? "";

    // --- Protect LaTeX from marked.js ------------------------------------
    const mathPlaceholders = [];
    let protectedMd = rawMd;
    protectedMd = protectedMd.replace(/\$\$([\s\S]*?)\$\$/g, (m) => {
      mathPlaceholders.push(m); return `%%MATH${mathPlaceholders.length - 1}%%`;
    });
    protectedMd = protectedMd.replace(/\\\[([\s\S]*?)\\\]/g, (m) => {
      mathPlaceholders.push(m); return `%%MATH${mathPlaceholders.length - 1}%%`;
    });
    protectedMd = protectedMd.replace(/\$([^\$\n]+?)\$/g, (m) => {
      mathPlaceholders.push(m); return `%%MATH${mathPlaceholders.length - 1}%%`;
    });
    protectedMd = protectedMd.replace(/\\\((.*?)\\\)/g, (m) => {
      mathPlaceholders.push(m); return `%%MATH${mathPlaceholders.length - 1}%%`;
    });

    // Close any still-open code block fence so marked won’t choke.
    const fenceCount = (protectedMd.match(/```/g) || []).length;
    if (fenceCount % 2 === 1) protectedMd += "\n```";

    const dirtyHtml = window.marked.parse(protectedMd, {
      gfm: true,
      breaks: true,
      smartLists: true,
    });
    const safeHtml = window.DOMPurify ? window.DOMPurify.sanitize(dirtyHtml) : dirtyHtml;

    // Restore math placeholders
    let finalHtml = safeHtml;
    mathPlaceholders.forEach((original, i) => {
      finalHtml = finalHtml.replace(`%%MATH${i}%%`, original);
    });
    el.innerHTML = finalHtml;

    // Render LaTeX expressions with KaTeX (lazy-loaded)
    await ensureKatexLoaded();
    if (window.renderMathInElement) {
      window.renderMathInElement(el, {
        delimiters: [
          { left: "$$", right: "$$", display: true },
          { left: "$", right: "$", display: false },
          { left: "\\(", right: "\\)", display: false },
          { left: "\\[", right: "\\]", display: true },
        ],
        throwOnError: false,
      });
    }

    await ensureHighlightLoaded();
    el.querySelectorAll("pre code").forEach((block) => window.hljs?.highlightElement(block));

    addCopyButtons(el);
  } catch (err) {
    console.error("[oh] markdown partial render failed", err);
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

console.debug("[oh] chat/markdown.js loaded");
