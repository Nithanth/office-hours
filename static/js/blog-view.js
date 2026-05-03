/**
 * blog-view.js — Standalone blog renderer.
 *
 * Reads raw markdown from the hidden #blog-raw element, renders it with
 * marked.js + DOMPurify, initializes Mermaid diagrams & KaTeX math,
 * and adds scroll-driven fade-in animations + reading progress bar.
 */

/* globals: marked, DOMPurify, mermaid, katex, renderMathInElement, hljs */

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
  { id: "hljs-css", href: "https://cdn.jsdelivr.net/npm/highlight.js@11/styles/github.min.css", tag: "link" },
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
  const mdProcessed = md.replace(/```mermaid\n([\s\S]*?)```/g, (_, code) => {
    const idx = mermaidBlocks.length;
    mermaidBlocks.push(code.trim());
    return `<div class="blog-mermaid-wrapper blog-fade-in" data-mermaid-idx="${idx}"></div>`;
  });

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
        ADD_ATTR: ["data-mermaid-idx", "class"],
      })
    : html;

  return { html, mermaidBlocks };
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

  const body = lines.slice(bodyStart).join("\n").trim();
  return { title, body };
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
async function renderMermaidDiagrams(mermaidBlocks) {
  if (!window.mermaid || mermaidBlocks.length === 0) return;

  const isDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
  mermaid.initialize({
    startOnLoad: false,
    theme: isDark ? "dark" : "default",
    fontFamily: "Inter, sans-serif",
    flowchart: { curve: "monotoneX", padding: 16 },
    themeVariables: isDark
      ? { primaryColor: "#818cf8", primaryTextColor: "#e2e8f0", lineColor: "#475569" }
      : { primaryColor: "#6366f1", primaryTextColor: "#1e293b", lineColor: "#94a3b8" },
  });

  const wrappers = document.querySelectorAll("[data-mermaid-idx]");
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
// Render KaTeX
// ---------------------------------------------------------------------------
function renderKaTeX() {
  if (!window.renderMathInElement) return;
  const article = document.querySelector(".blog-article");
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
// Scroll animations
// ---------------------------------------------------------------------------
function initScrollAnimations() {
  // Reading progress bar
  const progressBar = document.querySelector(".blog-progress-bar");
  if (progressBar) {
    window.addEventListener("scroll", () => {
      const scrollTop = window.scrollY;
      const docHeight = document.documentElement.scrollHeight - window.innerHeight;
      const progress = docHeight > 0 ? (scrollTop / docHeight) * 100 : 0;
      progressBar.style.width = progress + "%";
    }, { passive: true });
  }

  // Fade-in on scroll (intersection observer)
  const fadeEls = document.querySelectorAll(".blog-fade-in");
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
}

// ---------------------------------------------------------------------------
// Add fade-in classes to rendered HTML elements
// ---------------------------------------------------------------------------
function addFadeInClasses() {
  const article = document.querySelector(".blog-article");
  if (!article) return;

  const targets = article.querySelectorAll("h2, h3, p, ul, ol, blockquote, pre, table, .blog-mermaid-wrapper, hr");
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

function initActions(rawMd) {
  document.getElementById("blog-copy-btn")?.addEventListener("click", () => {
    navigator.clipboard.writeText(rawMd).then(() => showToast("Copied to clipboard"));
  });

  document.getElementById("blog-download-btn")?.addEventListener("click", () => {
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

  document.getElementById("blog-save-html-btn")?.addEventListener("click", () => {
    const htmlContent = document.documentElement.outerHTML;
    const blob = new Blob(["<!DOCTYPE html>\n" + htmlContent], { type: "text/html" });
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
  });
}

// ---------------------------------------------------------------------------
// Main init
// ---------------------------------------------------------------------------
async function initBlog() {
  const rawEl = document.getElementById("blog-raw");
  if (!rawEl) return;

  const rawMd = rawEl.textContent;
  const { title, body } = extractTitleAndBody(rawMd);

  // Set hero
  const heroTitle = document.querySelector(".blog-hero h1");
  if (heroTitle) heroTitle.textContent = title;

  const metaDate = document.getElementById("blog-date");
  if (metaDate) metaDate.textContent = formatDate();

  const metaTime = document.getElementById("blog-read-time");
  if (metaTime) metaTime.textContent = readingTime(rawMd) + " min read";

  document.title = title + " — Lux Scholar";

  // Show loading
  const articleEl = document.querySelector(".blog-article");

  // Load libs
  await loadAllDeps();

  // Render markdown
  const { html, mermaidBlocks } = renderMarkdown(body);
  if (articleEl) articleEl.innerHTML = html;

  // Post-processing
  addFadeInClasses();
  await renderMermaidDiagrams(mermaidBlocks);
  renderKaTeX();
  initScrollAnimations();
  initActions(rawMd);

  // Remove loading state
  document.body.classList.remove("blog-is-loading");
}

// Run on DOM ready
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initBlog);
} else {
  initBlog();
}
