/**
 * blog-view.js — Standalone blog renderer.
 *
 * Polls /blog-status with the token in window.__BLOG_TOKEN__ until the LLM
 * has finished, then renders the markdown with marked.js + DOMPurify,
 * initializes Mermaid diagrams & KaTeX math, applies scroll-driven
 * fade-ins, the reading progress bar, and a theme toggle that mirrors
 * the app's localStorage 'theme' key so the blog page matches the main app.
 */

/* globals: marked, DOMPurify, mermaid, katex, renderMathInElement, hljs */

// ---------------------------------------------------------------------------
// Theme — driven by the app's localStorage 'theme' key for cross-page consistency
// ---------------------------------------------------------------------------
function currentTheme() {
  try {
    return localStorage.getItem("theme") === "dark" ? "dark" : "light";
  } catch (_) {
    return "light";
  }
}

function applyTheme(theme) {
  if (theme === "dark") {
    document.documentElement.dataset.theme = "dark";
  } else {
    delete document.documentElement.dataset.theme;
  }
  const btn = document.getElementById("blog-theme-toggle");
  if (btn) btn.textContent = theme === "dark" ? "☀️" : "🌙";
}

function initThemeToggle() {
  const btn = document.getElementById("blog-theme-toggle");
  if (!btn) return;
  applyTheme(currentTheme());
  btn.addEventListener("click", () => {
    const next = currentTheme() === "dark" ? "light" : "dark";
    try { localStorage.setItem("theme", next); } catch (_) {}
    applyTheme(next);
  });
  // Sync if the user changes theme in another tab.
  window.addEventListener("storage", (ev) => {
    if (ev.key === "theme") applyTheme(currentTheme());
  });
}

// ---------------------------------------------------------------------------
// CDN loader
// ---------------------------------------------------------------------------
const CDN_DEPS = [
  { id: "marked", src: "https://cdn.jsdelivr.net/npm/marked@12/marked.min.js" },
  { id: "dompurify", src: "https://cdn.jsdelivr.net/npm/dompurify@3/dist/purify.min.js" },
  { id: "hljs", src: "https://cdn.jsdelivr.net/npm/highlight.js@11/highlight.min.js" },
  { id: "mermaid", src: "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js" },
  { id: "katex-css", href: "https://cdn.jsdelivr.net/npm/katex@0.16/dist/katex.min.css", tag: "link" },
  { id: "katex", src: "https://cdn.jsdelivr.net/npm/katex@0.16/dist/katex.min.js" },
  { id: "katex-auto", src: "https://cdn.jsdelivr.net/npm/katex@0.16/dist/contrib/auto-render.min.js" },
  {
    id: "hljs-css",
    href: currentTheme() === "dark"
      ? "https://cdn.jsdelivr.net/npm/highlight.js@11/styles/github-dark.min.css"
      : "https://cdn.jsdelivr.net/npm/highlight.js@11/styles/github.min.css",
    tag: "link",
  },
];

function loadDep(dep) {
  return new Promise((resolve, reject) => {
    if (document.getElementById("blog-dep-" + dep.id)) return resolve();
    if (dep.tag === "link") {
      const link = document.createElement("link");
      link.id = "blog-dep-" + dep.id;
      link.rel = "stylesheet";
      link.href = dep.href;
      link.onload = resolve;
      link.onerror = reject;
      document.head.appendChild(link);
    } else {
      const script = document.createElement("script");
      script.id = "blog-dep-" + dep.id;
      script.src = dep.src;
      script.onload = resolve;
      script.onerror = reject;
      document.head.appendChild(script);
    }
  });
}

async function loadAllDeps() {
  // Load CSS + core libs in parallel
  const coreDeps = CDN_DEPS.filter(d => d.id !== "katex-auto");
  await Promise.allSettled(coreDeps.map(loadDep));
  // katex-auto depends on katex
  const autoRender = CDN_DEPS.find(d => d.id === "katex-auto");
  if (autoRender) await loadDep(autoRender).catch(() => {});
}

// ---------------------------------------------------------------------------
// Markdown → HTML
// ---------------------------------------------------------------------------
function renderMarkdown(md) {
  // Extract mermaid blocks before marked processes them
  const mermaidBlocks = [];
  let mdProcessed = md.replace(/```mermaid\n([\s\S]*?)```/g, (_, code) => {
    const idx = mermaidBlocks.length;
    mermaidBlocks.push(code.trim());
    return `<div class="blog-mermaid-wrapper blog-fade-in" data-mermaid-idx="${idx}"></div>`;
  });

  // Extract vega-lite blocks (also accept "vegalite" / "vl" aliases). The JSON
  // spec is rendered later by `vega-embed`; here we just stash the raw text
  // and leave a placeholder div with a payload script for a CSP-friendly
  // round-trip through marked + DOMPurify.
  const vegaBlocks = [];
  mdProcessed = mdProcessed.replace(
    /```(?:vega-lite|vegalite|vl)\n([\s\S]*?)```/g,
    (_, code) => {
      const idx = vegaBlocks.length;
      vegaBlocks.push(code.trim());
      return `<div class="blog-vega-wrapper blog-fade-in" data-vega-idx="${idx}"></div>`;
    }
  );

  // Configure marked
  if (window.marked) {
    marked.setOptions({
      gfm: true,
      breaks: false,
      highlight: function (code, lang) {
        if (window.hljs && lang && hljs.getLanguage(lang)) {
          try { return hljs.highlight(code, { language: lang }).value; } catch (_) {}
        }
        if (window.hljs) {
          try { return hljs.highlightAuto(code).value; } catch (_) {}
        }
        return code;
      },
    });
  }

  let html = window.marked ? marked.parse(mdProcessed) : mdProcessed;
  html = window.DOMPurify
    ? DOMPurify.sanitize(html, {
        ADD_TAGS: ["div"],
        ADD_ATTR: ["data-mermaid-idx", "data-vega-idx", "class"],
      })
    : html;

  return { html, mermaidBlocks, vegaBlocks };
}

// ---------------------------------------------------------------------------
// Split title from body
// ---------------------------------------------------------------------------
function extractTitleAndBody(md) {
  const lines = md.split("\n");
  let title = "";
  let bodyStart = 0;

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i].trim();
    if (!line) continue;
    if (line.startsWith("# ")) {
      title = line.replace(/^#\s+/, "");
      bodyStart = i + 1;
      break;
    }
    // No H1 found — use first non-empty line
    title = line;
    bodyStart = i + 1;
    break;
  }

  // Defensive: even though the backend `_normalise_blog_title` runs on every
  // H1, model output still slips wrapper chars through occasionally (esp. when
  // it emits the title as a non-H1 bold line). Mirror the Python logic here
  // so the hero never shows literal asterisks.
  title = normaliseTitleClient(title);

  const body = lines.slice(bodyStart).join("\n").trim();
  return { title, body };
}

// Mirror of `_normalise_blog_title` in `src/api/routers/report_notes.py`.
// Iteratively peels symmetric emphasis wrappers (escaped or not), quotes, and
// stray leading `# ` markers from a title string.
const TITLE_WRAPPER_PAIRS = [
  ["\\*\\*", "\\*\\*"],
  ["\\*", "\\*"],
  ["***", "***"],
  ["**", "**"],
  ["__", "__"],
  ["*", "*"],
  ["_", "_"],
  ["`", "`"],
  ['"', '"'],
  ["'", "'"],
];

function normaliseTitleClient(title) {
  let s = (title || "").trim();
  // Drop redundant "# " prefixes like "# # Real Title".
  while (s.startsWith("# ")) s = s.slice(2).trimStart();
  let changed = true;
  while (changed && s) {
    changed = false;
    for (const [left, right] of TITLE_WRAPPER_PAIRS) {
      if (s.length > left.length + right.length && s.startsWith(left) && s.endsWith(right)) {
        s = s.slice(left.length, s.length - right.length).trim();
        changed = true;
        break;
      }
    }
  }
  return s.replace(/\s+/g, " ").trim();
}

// ---------------------------------------------------------------------------
// Estimate reading time
// ---------------------------------------------------------------------------
function readingTime(text) {
  const words = text.trim().split(/\s+/).length;
  return Math.max(1, Math.ceil(words / 230));
}

// ---------------------------------------------------------------------------
// Format date
// ---------------------------------------------------------------------------
function formatDate() {
  return new Date().toLocaleDateString("en-US", {
    year: "numeric",
    month: "long",
    day: "numeric",
  });
}

// ---------------------------------------------------------------------------
// Render Mermaid diagrams
// ---------------------------------------------------------------------------
async function renderMermaidDiagrams(mermaidBlocks, root) {
  if (!window.mermaid || mermaidBlocks.length === 0) return;
  const scope = root || document;

  const isDark = currentTheme() === "dark";
  mermaid.initialize({
    startOnLoad: false,
    theme: isDark ? "dark" : "default",
    fontFamily: "Inter, sans-serif",
    flowchart: { curve: "monotoneX", padding: 16 },
    themeVariables: isDark
      ? { primaryColor: "#818cf8", primaryTextColor: "#e2e8f0", lineColor: "#475569" }
      : { primaryColor: "#6366f1", primaryTextColor: "#1e293b", lineColor: "#94a3b8" },
  });

  const wrappers = scope.querySelectorAll("[data-mermaid-idx]");
  for (const wrapper of wrappers) {
    const idx = parseInt(wrapper.dataset.mermaidIdx, 10);
    const code = mermaidBlocks[idx];
    if (!code) continue;

    try {
      const id = `mermaid-${idx}-${Date.now()}`;
      const { svg } = await mermaid.render(id, code);
      wrapper.innerHTML = svg;
    } catch (err) {
      console.warn("[blog] Mermaid render failed for block", idx, err);
      // Fallback: show code block
      wrapper.innerHTML = `<pre style="text-align:left;"><code>${code.replace(/</g, "&lt;")}</code></pre>`;
      wrapper.style.borderColor = "#f59e0b";
    }
  }
}

// ---------------------------------------------------------------------------
// Render Vega-Lite charts
// ---------------------------------------------------------------------------
// Charts render inside a sandboxed /vega-frame iframe: vega's expression
// compiler needs 'unsafe-eval', which the page CSP rightly forbids — the
// frame's own CSP allows it. Each iframe gets an opaque origin
// (sandbox=allow-scripts), receives the spec via postMessage, and posts
// its content height back so the parent autosizes it. Bonus: hostile
// specs (expressions run arbitrary JS) are confined to the frame instead
// of executing in the app origin.
const _vegaFrames = new Set();
window.addEventListener("message", (e) => {
  const d = e.data || {};
  if (d.type !== "vega-height") return;
  for (const iframe of _vegaFrames) {
    if (e.source === iframe.contentWindow && typeof d.height === "number") {
      iframe.style.height = Math.min(Math.max(d.height, 80), 1400) + "px";
      return;
    }
  }
});

async function renderVegaCharts(vegaBlocks, root) {
  if (!vegaBlocks || vegaBlocks.length === 0) return;
  const scope = root || document;
  const wrappers = scope.querySelectorAll("[data-vega-idx]");
  if (wrappers.length === 0) return;

  const isDark = currentTheme() === "dark";

  for (const wrapper of wrappers) {
    const idx = parseInt(wrapper.dataset.vegaIdx, 10);
    const rawSpec = vegaBlocks[idx];
    if (!rawSpec) continue;

    let spec;
    try {
      spec = JSON.parse(rawSpec);
    } catch (err) {
      console.warn("[blog] Vega-Lite spec is not valid JSON for block", idx, err);
      wrapper.innerHTML = vegaErrorMarkup("Invalid JSON spec: " + (err && err.message ? err.message : String(err)), rawSpec);
      wrapper.style.borderColor = "#f59e0b";
      continue;
    }

    const iframe = document.createElement("iframe");
    iframe.className = "blog-vega-frame";
    iframe.src = "/vega-frame";
    iframe.setAttribute("sandbox", "allow-scripts");
    iframe.setAttribute("title", "Interactive chart");
    wrapper.innerHTML = "";
    wrapper.appendChild(iframe);
    _vegaFrames.add(iframe);
    iframe.addEventListener("load", () => {
      iframe.contentWindow.postMessage(
        {
          type: "vega-spec",
          idx,
          spec,
          config: {
            actions: { export: true, source: true, compiled: false, editor: true },
            theme: isDark ? "dark" : undefined,
            renderer: "canvas",
            config: {
              background: "transparent",
              view: { stroke: "transparent" },
              axis: isDark
                ? { labelColor: "#94a3b8", titleColor: "#e2e8f0", gridColor: "#1e293b" }
                : undefined,
            },
          },
        },
        "*"
      );
    });
  }
}

function vegaErrorMarkup(message, rawSpec) {
  const safeMsg = String(message).replace(/[<&>]/g, (c) => ({"<":"&lt;","&":"&amp;",">":"&gt;"}[c]));
  const safeSpec = String(rawSpec || "").replace(/[<&>]/g, (c) => ({"<":"&lt;","&":"&amp;",">":"&gt;"}[c]));
  return (
    '<div class="blog-vega-error">' +
    '<p style="margin:0 0 8px 0;color:#f59e0b;font-weight:600;">⚠ Interactive chart could not be rendered</p>' +
    `<p style="margin:0 0 12px 0;opacity:0.8;font-size:13px;">${safeMsg}</p>` +
    '<details><summary style="cursor:pointer;font-size:13px;opacity:0.8;">Show spec</summary>' +
    `<pre style="text-align:left;margin-top:8px;font-size:12px;overflow-x:auto;"><code>${safeSpec}</code></pre>` +
    "</details></div>"
  );
}

// ---------------------------------------------------------------------------
// Render KaTeX
// ---------------------------------------------------------------------------
function renderKaTeX(root) {
  if (!window.renderMathInElement) return;
  const scope = root || document;
  let article = scope.classList && scope.classList.contains("blog-article")
    ? scope
    : scope.querySelector(".blog-article");
  // When handed an explicit element (e.g. #report-content), render inside it
  // even if it isn't a .blog-article — reports contain the same $math$.
  if (!article && scope !== document && scope.nodeType === 1) article = scope;
  if (!article) return;
  renderMathInElement(article, {
    delimiters: [
      { left: "$$", right: "$$", display: true },
      { left: "$", right: "$", display: false },
      { left: "\\[", right: "\\]", display: true },
      { left: "\\(", right: "\\)", display: false },
    ],
    throwOnError: false,
  });
}

// ---------------------------------------------------------------------------
// Image fade-in on load
// ---------------------------------------------------------------------------
function initImageFadeIn(root) {
  const scope = root || document;
  const article = scope.classList && scope.classList.contains("blog-article")
    ? scope
    : scope.querySelector(".blog-article");
  if (!article) return;
  article.querySelectorAll("img").forEach((img) => {
    if (img.complete) {
      img.classList.add("loaded");
    } else {
      img.addEventListener("load", () => img.classList.add("loaded"), { once: true });
      img.addEventListener("error", () => img.classList.add("loaded"), { once: true });
    }
  });
}

// ---------------------------------------------------------------------------
// Scroll animations
// ---------------------------------------------------------------------------
function initScrollAnimations(root) {
  const scope = root || document;
  // Reading progress bar
  const progressBar = scope.querySelector(".blog-progress-bar");
  if (progressBar) {
    window.addEventListener("scroll", () => {
      const scrollTop = window.scrollY;
      const docHeight = document.documentElement.scrollHeight - window.innerHeight;
      const progress = docHeight > 0 ? (scrollTop / docHeight) * 100 : 0;
      progressBar.style.width = progress + "%";
    }, { passive: true });
  }

  initFadeObserver(scope);
}

// Fade-in on scroll (intersection observer). Extracted so pane renders
// (/reports/{id} embedded blog, /topics/{id} sections) can reveal elements
// too — without it .blog-fade-in stays at opacity:0 forever.
// Retained references — a locally-scoped observer can be GC'd before it ever
// delivers entries (seen: /reports/{id} pane rendered behind `await loadAllDeps`
// stayed invisible while the same code worked on /topics).
const _fadeObservers = new Set();

function initFadeObserver(root) {
  const scope = root || document;
  const fadeEls = scope.querySelectorAll(".blog-fade-in");
  if (fadeEls.length === 0) return;

  const observer = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          entry.target.classList.add("visible");
          observer.unobserve(entry.target);
        }
      });
    },
    { threshold: 0.1, rootMargin: "0px 0px -40px 0px" }
  );

  fadeEls.forEach((el) => observer.observe(el));
  _fadeObservers.add(observer);
}

// ---------------------------------------------------------------------------
// Add fade-in classes to rendered HTML elements
// ---------------------------------------------------------------------------
function addFadeInClasses(root) {
  const scope = root || document;
  const article = scope.classList && scope.classList.contains("blog-article")
    ? scope
    : scope.querySelector(".blog-article");
  if (!article) return;

  const targets = article.querySelectorAll("h2, h3, p, ul, ol, blockquote, pre, table, .blog-mermaid-wrapper, .blog-vega-wrapper, hr");
  targets.forEach((el) => {
    if (!el.classList.contains("blog-fade-in")) {
      el.classList.add("blog-fade-in");
    }
  });
}

// ---------------------------------------------------------------------------
// Copy & download actions
// ---------------------------------------------------------------------------
function showToast(msg) {
  let toast = document.querySelector(".blog-toast");
  if (!toast) {
    toast = document.createElement("div");
    toast.className = "blog-toast";
    document.body.appendChild(toast);
  }
  toast.textContent = msg;
  toast.classList.add("show");
  setTimeout(() => toast.classList.remove("show"), 2500);
}

// Defensive: refuse any download / copy action while the blog is still being
// generated. The buttons are also `disabled` in HTML and visibly greyed via
// CSS (blog.css `.blog-nav-btn[disabled]`), but if a stale handler somehow
// fires we toast and bail instead of saving an empty file.
function isBlogStillLoading() {
  return document.body.classList.contains("blog-is-loading");
}

function initActions(rawMd) {
  document.getElementById("blog-copy-btn")?.addEventListener("click", () => {
    if (isBlogStillLoading()) { showToast("Blog is still being written"); return; }
    navigator.clipboard.writeText(rawMd).then(() => showToast("Copied to clipboard"));
  });

  document.getElementById("blog-download-btn")?.addEventListener("click", () => {
    if (isBlogStillLoading()) { showToast("Blog is still being written"); return; }
    const blob = new Blob([rawMd], { type: "text/markdown" });
    const url = URL.createObjectURL(blob);
    const a = Object.assign(document.createElement("a"), {
      href: url,
      download: "research_blog.md",
    });
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    showToast("Downloaded");
  });

  document.getElementById("blog-save-html-btn")?.addEventListener("click", async (ev) => {
    if (isBlogStillLoading()) { showToast("Blog is still being written"); return; }
    const btn = ev.currentTarget;
    if (btn.dataset.busy) return;
    btn.dataset.busy = "1";
    const original = btn.textContent;
    btn.textContent = "Saving…";
    try {
      const html = await buildSelfContainedHtml();
      const blob = new Blob([html], { type: "text/html" });
      const url = URL.createObjectURL(blob);
      const a = Object.assign(document.createElement("a"), {
        href: url,
        download: "research_blog.html",
      });
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      showToast("Saved as HTML");
    } catch (err) {
      console.error("[blog] save-html failed", err);
      showToast("Save failed — see console");
    } finally {
      btn.textContent = original;
      delete btn.dataset.busy;
    }
  });

  document.getElementById("blog-save-pdf-btn")?.addEventListener("click", () => {
    if (isBlogStillLoading()) { showToast("Blog is still being written"); return; }
    // Use the browser's native print → "Save as PDF". A `@media print` block
    // in blog.css hides the nav/buttons and forces light theme for ink.
    document.body.classList.add("blog-printing");
    // Defer to next frame so the class is applied before the print dialog opens.
    requestAnimationFrame(() => {
      window.print();
      // Clean up shortly after dialog returns.
      setTimeout(() => document.body.classList.remove("blog-printing"), 1000);
    });
  });
}

// ---------------------------------------------------------------------------
// Self-contained HTML export
// ---------------------------------------------------------------------------
// Walks every <link rel="stylesheet"> and inlines its CSS into a <style> tag
// so the saved file renders correctly when opened offline. Same-origin CSS is
// fetched via fetch(); cross-origin CSS we leave as a <link> (Google Fonts,
// KaTeX, highlight.js — all have permissive CDN caching), which means the
// saved file still needs internet for fonts/icons but renders fully styled.
async function buildSelfContainedHtml() {
  const clone = document.documentElement.cloneNode(true);

  // Remove the runtime token script — it's session-bound and useless after save.
  clone.querySelectorAll("script").forEach((s) => {
    const txt = s.textContent || "";
    if (txt.includes("__BLOG_TOKEN__")) s.remove();
    if (s.id && s.id.startsWith("blog-dep-")) s.remove();
  });
  // Remove the blog-view.js loader script — the saved file is static.
  clone.querySelectorAll('script[src*="/static/js/blog-view.js"]').forEach((s) => s.remove());
  // Remove dynamically-injected dep stylesheets (we'll inline them instead).
  clone.querySelectorAll('link[id^="blog-dep-"]').forEach((l) => l.remove());

  // Inline all <link rel="stylesheet">. Same-origin: fetch + replace with <style>.
  // Cross-origin: leave as-is (CORS prevents reading the bytes).
  const links = Array.from(clone.querySelectorAll('link[rel="stylesheet"]'));
  await Promise.all(links.map(async (link) => {
    const href = link.getAttribute("href") || "";
    if (!href) return;
    let absHref;
    try {
      absHref = new URL(href, document.baseURI).href;
    } catch (_) { return; }
    const sameOrigin = new URL(absHref).origin === window.location.origin;
    if (!sameOrigin) {
      // Make sure cross-origin <link>s have absolute hrefs in the saved file.
      link.setAttribute("href", absHref);
      return;
    }
    try {
      const resp = await fetch(absHref);
      if (!resp.ok) return;
      let css = await resp.text();
      // Rewrite url(...) refs to absolute URLs so fonts/images still resolve.
      css = css.replace(/url\(\s*(['"]?)([^'")]+)\1\s*\)/g, (m, q, u) => {
        if (/^(data:|https?:|\/\/)/i.test(u)) return m;
        try {
          const abs = new URL(u, absHref).href;
          return `url(${q}${abs}${q})`;
        } catch (_) { return m; }
      });
      const style = document.createElement("style");
      style.setAttribute("data-from", absHref);
      style.textContent = css;
      link.replaceWith(style);
    } catch (_) {
      // Network/CORS failure → leave the link element in place.
    }
  }));

  // Promote <html data-theme="dark"> attribute into the saved markup.
  if (document.documentElement.dataset.theme === "dark") {
    clone.dataset.theme = "dark";
  }

  // Strip the in-page action buttons (Copy / Download / Save / Revise / theme
  // toggle) from the saved file — they're useless in a static export and would
  // be broken without their JS handlers.
  clone.querySelectorAll(".blog-nav-actions, .blog-progress-bar, .blog-loading, .blog-toast, #blog-customize-modal, #report-blog-popup-loader").forEach((n) => n.remove());

  const docComment =
    "<!--\n" +
    "  Saved by Office Hours.\n" +
    "  Self-contained for blog page styles (CSS inlined). Math (KaTeX),\n" +
    "  diagrams (Mermaid), and interactive charts (Vega-Lite) are loaded\n" +
    "  from public CDNs; open this file with internet access for full\n" +
    "  rendering. Theme and colors are baked in.\n" +
    "-->\n";

  return "<!DOCTYPE html>\n" + docComment + clone.outerHTML;
}

// ---------------------------------------------------------------------------
// Loading-state substep messages — cycled while we wait for the LLM
// ---------------------------------------------------------------------------
const LOADING_MESSAGES = [
  "Reading the report…",
  "Identifying the most striking findings…",
  "Planning the narrative arc…",
  "Sketching diagrams where they help…",
  "Composing the introduction…",
  "Polishing the prose…",
  "Almost there—final pass…",
];

function startLoadingMessageRotator() {
  const el = document.getElementById("blog-loading-substep");
  if (!el) return () => {};
  let i = 0;
  el.textContent = LOADING_MESSAGES[0];
  const handle = setInterval(() => {
    i = (i + 1) % LOADING_MESSAGES.length;
    el.classList.add("fading");
    setTimeout(() => {
      el.textContent = LOADING_MESSAGES[i];
      el.classList.remove("fading");
    }, 380);
  }, 4200);
  return () => clearInterval(handle);
}

// ---------------------------------------------------------------------------
// Status polling
// ---------------------------------------------------------------------------
// Polls `/blog-status?token=...` until the server reports the markdown is ready
// (or it errors out). Falls back to the legacy inline `<script id="blog-raw">`
// element when no token is exposed (kept for backward compat).
async function fetchBlogMarkdown(token, { intervalMs = 1500, timeoutMs = 180000 } = {}) {
  const start = Date.now();
  const url = `/blog-status?token=${encodeURIComponent(token)}`;
  while (Date.now() - start < timeoutMs) {
    const resp = await fetch(url, { headers: { Accept: "application/json" } });
    if (resp.status === 404) {
      let detail = "Blog not found or expired.";
      try {
        const body = await resp.json();
        detail = body.error || body.detail || detail;
      } catch (_) {}
      throw new Error(detail);
    }
    if (!resp.ok) {
      throw new Error(`Blog status check failed (HTTP ${resp.status}).`);
    }
    const data = await resp.json();
    if (data.state === "ready" && typeof data.markdown === "string") {
      return data.markdown;
    }
    if (data.state === "error") {
      throw new Error(data.error || "Blog generation failed.");
    }
    await new Promise((r) => setTimeout(r, intervalMs));
  }
  throw new Error("Timed out waiting for blog generation.");
}

function renderError(article, message) {
  if (!article) return;
  const safe = String(message || "Something went wrong.");
  article.innerHTML =
    '<div class="blog-error" style="padding:2rem;border:1px solid #f59e0b;border-radius:8px;background:rgba(245,158,11,0.08);">' +
    '<h2 style="margin-top:0;">We couldn’t load this blog</h2>' +
    '<p style="opacity:0.85;"></p>' +
    '<p style="margin-top:1rem;"><a href="/">← Back to Office Hours</a></p>' +
    "</div>";
  const p = article.querySelector(".blog-error p:first-of-type");
  if (p) p.textContent = safe;
}

// ---------------------------------------------------------------------------
// Main init
// ---------------------------------------------------------------------------
async function initBlog() {
  const articleEl = document.querySelector(".blog-article");
  const heroTitle = document.querySelector(".blog-hero h1");
  const metaDate = document.getElementById("blog-date");
  const metaTime = document.getElementById("blog-read-time");

  initThemeToggle();
  if (metaDate) metaDate.textContent = formatDate();

  // Resolve the markdown source: token-based (new flow) or inline (legacy).
  let rawMd;
  const token = window.__BLOG_TOKEN__;
  let stopRotator = () => {};
  if (token) {
    stopRotator = startLoadingMessageRotator();
    try {
      rawMd = await fetchBlogMarkdown(token);
    } catch (err) {
      console.error("[blog] failed to fetch markdown", err);
      stopRotator();
      if (heroTitle) heroTitle.textContent = "Couldn’t load blog";
      document.title = "Blog — Office Hours";
      renderError(articleEl, err && err.message ? err.message : String(err));
      document.body.classList.remove("blog-is-loading");
      return;
    }
    stopRotator();
  } else {
    const rawEl = document.getElementById("blog-raw");
    if (!rawEl) return;
    rawMd = rawEl.textContent;
  }

  const { title, body } = extractTitleAndBody(rawMd);

  if (heroTitle) heroTitle.textContent = title || "Research Blog";
  if (metaTime) metaTime.textContent = readingTime(rawMd) + " min read";
  document.title = (title || "Blog") + " — Office Hours";

  // Load libs
  await loadAllDeps();

  // Render markdown
  const { html, mermaidBlocks, vegaBlocks } = renderMarkdown(body);
  if (articleEl) articleEl.innerHTML = html;

  // Post-processing
  addFadeInClasses(articleEl);
  initImageFadeIn(articleEl);
  await renderMermaidDiagrams(mermaidBlocks, articleEl);
  await renderVegaCharts(vegaBlocks, articleEl);
  renderKaTeX(articleEl);
  initScrollAnimations(document);
  initActions(rawMd);
  initReviseButton(token);

  // Enable nav-bar action buttons now that content is ready.
  document.querySelectorAll(".blog-nav-btn[disabled]").forEach((b) => {
    if (b.id !== "blog-theme-toggle") b.removeAttribute("disabled");
  });

  // Remove loading state
  document.body.classList.remove("blog-is-loading");
}

// ---------------------------------------------------------------------------
// Revise (regenerate) button
// ---------------------------------------------------------------------------
// Opens the same customization modal (from report.js) in "regenerate" mode and
// POSTs to /regenerate-blog with the previous blog's token + new options +
// feedback. On success, navigates the current tab to the new blog URL.
function initReviseButton(prevToken) {
  const btn = document.getElementById("blog-revise-btn");
  if (!btn || !prevToken) return;
  btn.addEventListener("click", () => {
    if (isBlogStillLoading()) {
      showToast("Blog is still being written");
      return;
    }
    const openModal = window.ohBlog && window.ohBlog.openCustomizeModal;
    if (!openModal) {
      console.error("[blog] customize modal not available (ohBlog missing)");
      return;
    }
    // Pre-populate with the current blog's accent theme so it stays selected.
    const currentAccent = window.__BLOG_ACCENT__ || document.documentElement.dataset.accent || "indigo";
    openModal({
      mode: "regenerate",
      current: { audience: "", length: "", tone: "", theme: currentAccent, extra: "" },
      onSubmit: (opts) => launchRegenerate(prevToken, opts),
    });
  });
}

function launchRegenerate(prevToken, opts) {
  const original = document.getElementById("blog-revise-btn");
  if (original) {
    original.disabled = true;
    original.textContent = "Revising…";
  }
  // Re-enter the loading state so the page shows progress + disables actions.
  document.body.classList.add("blog-is-loading");
  const heroTitle = document.querySelector(".blog-hero h1");
  if (heroTitle) heroTitle.textContent = "Revising your blog";

  const fd = new FormData();
  fd.append("token", prevToken);
  if (opts.audience) fd.append("audience", opts.audience);
  if (opts.length) fd.append("length", opts.length);
  if (opts.tone) fd.append("tone", opts.tone);
  if (opts.theme) fd.append("theme", opts.theme);
  if (opts.extra) fd.append("extra", opts.extra);

  fetch("/regenerate-blog", { method: "POST", body: fd })
    .then((r) => {
      if (!r.ok) {
        return r.json().then((j) => { throw new Error(j.detail || `Server ${r.status}`); }).catch(() => {
          throw new Error(`Server responded ${r.status}`);
        });
      }
      return r.json();
    })
    .then((data) => {
      if (data.url) {
        // Same-tab navigation so the user doesn't accumulate orphan tabs.
        window.location.href = data.url;
      }
    })
    .catch((err) => {
      console.error("[blog] regenerate failed", err);
      showToast("Revision failed: " + (err.message || err));
      document.body.classList.remove("blog-is-loading");
      if (original) {
        original.disabled = false;
        original.textContent = "Revise";
      }
    });
}

// ---------------------------------------------------------------------------
// Shared render API — used by the embedded blog pane (blog-pane.js) to render
// persisted blogs inside the report page without a popup. Every renderer takes
// a root element so it scopes to the pane instead of the whole document.
// ---------------------------------------------------------------------------
window.ohBlogRender = {
  loadAllDeps,
  renderMarkdown,
  extractTitleAndBody,
  renderMermaidDiagrams,
  renderVegaCharts,
  renderKaTeX,
  addFadeInClasses,
  initImageFadeIn,
  readingTime,
  currentTheme,

  // Render markdown into `articleEl` (an element with class .blog-article).
  // Returns the extracted title. Callers handle loading/error states.
  async renderInto(articleEl, rawMd) {
    const { title, body } = extractTitleAndBody(rawMd || "");
    await loadAllDeps();
    const { html, mermaidBlocks, vegaBlocks } = renderMarkdown(body);
    articleEl.innerHTML = html;
    addFadeInClasses(articleEl);
    initImageFadeIn(articleEl);
    initFadeObserver(articleEl);
    await renderMermaidDiagrams(mermaidBlocks, articleEl);
    await renderVegaCharts(vegaBlocks, articleEl);
    renderKaTeX(articleEl);
    return title;
  },
};

// Run on DOM ready — only on the standalone /blog-view page (identified by the
// token script or the legacy inline #blog-raw element). On report pages the
// pane owns rendering via window.ohBlogRender.
function isStandaloneBlogPage() {
  return Boolean(window.__BLOG_TOKEN__ || document.getElementById("blog-raw"));
}

if (isStandaloneBlogPage()) {
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initBlog);
  } else {
    initBlog();
  }
}
