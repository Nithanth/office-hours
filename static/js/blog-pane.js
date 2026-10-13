/**
 * blog-pane.js — Embedded blog view for the report page.
 *
 * Turns the report's "Blog" affordance into a view toggle: the report content
 * swaps for a rendered blog article in-place instead of opening the standalone
 * /blog-view popup. Blogs are generated with report_id so they persist on the
 * report row (reports.blog_markdown) and reload instantly on later visits.
 *
 * Falls back gracefully: with no report_id (anonymous / unsaved report) the
 * Blog button keeps the original popup flow handled by report.js.
 *
 * Requires: blog-view.js (window.ohBlogRender), blog-modal.js
 * (window.ohBlog.openCustomizeModal), report.js (getCleanReportText).
 */

(function () {
  const POLL_MS = 1500;
  const POLL_TIMEOUT_MS = 240000;

  const PANE_LOADING_MESSAGES = [
    "Reading the report…",
    "Identifying the most striking findings…",
    "Planning the narrative arc…",
    "Sketching diagrams where they help…",
    "Composing the introduction…",
    "Polishing the prose…",
    "Almost there—final pass…",
  ];

  let pane = null;
  let articleEl = null;
  let metaEl = null;
  let reviseBtn = null;
  let pollAbort = { cancelled: false };
  let msgRotator = null;

  function reportId() {
    const el = document.getElementById("report-content");
    return (el && el.dataset.reportId) || "";
  }

  function reportEl() {
    return document.getElementById("report-content");
  }

  function setView(view) {
    const showing = view === "blog";
    if (pane) pane.hidden = !showing;
    const content = reportEl();
    if (content) content.classList.toggle("oh-blog-view-active", showing);

    // Streamed report page: #report-content is a card containing the report
    // blocks + action bar — hide them but keep the card (and view toggle).
    // Detail page: #report-content IS the report body — hide it whole; the
    // pane is a sibling.
    const blocks = document.getElementById("report-blocks");
    if (blocks) {
      blocks.hidden = showing;
      const actions = document.getElementById("report-actions");
      if (actions) actions.hidden = showing;
      const progress = document.getElementById("report-progress");
      if (progress) progress.hidden = showing;
    } else if (content) {
      content.hidden = showing;
    }

    document.querySelectorAll("#report-view-toggle [data-view]").forEach((b) => {
      b.classList.toggle("active", b.dataset.view === view);
    });
    if (!showing && pollAbort) pollAbort.cancelled = true;
    if (!showing) stopMsgRotator();
  }

  // ------------------------------------------------------------------
  // Pane construction
  // ------------------------------------------------------------------
  function ensurePane() {
    if (pane) return pane;
    const host = reportEl();
    if (!host) return null;

    pane = document.getElementById("blog-pane");
    if (!pane) {
      pane = document.createElement("div");
      pane.id = "blog-pane";
      pane.className = "blog-scope";
      pane.hidden = true;
      if (document.getElementById("report-blocks")) {
        // Streamed report: mount inside the card below the report body so the
        // view toggle stays visible above it.
        host.appendChild(pane);
      } else {
        // Detail page: #report-content is the report body itself — mount as a
        // sibling so it can be swapped for the report.
        host.insertAdjacentElement("afterend", pane);
      }
    }

    if (!pane.querySelector(".blog-pane-bar")) {
      pane.innerHTML =
        '<div class="blog-pane-bar">' +
        '  <button type="button" id="blog-pane-back" class="blog-nav-btn" title="Back to report">← Report</button>' +
        '  <span class="blog-pane-meta" id="blog-pane-meta"></span>' +
        '  <span class="blog-pane-bar-spacer"></span>' +
        '  <button type="button" id="blog-pane-revise" class="blog-nav-btn" title="Revise this blog" hidden>Revise</button>' +
        "</div>" +
        '<article class="blog-article" id="blog-pane-article"></article>';
    }

    articleEl = pane.querySelector("#blog-pane-article");
    metaEl = pane.querySelector("#blog-pane-meta");
    reviseBtn = pane.querySelector("#blog-pane-revise");

    pane.querySelector("#blog-pane-back").addEventListener("click", () => setView("report"));
    reviseBtn.addEventListener("click", startRevise);

    return pane;
  }

  // ------------------------------------------------------------------
  // Loading / error states inside the article
  // ------------------------------------------------------------------
  function stopMsgRotator() {
    if (msgRotator) {
      clearInterval(msgRotator);
      msgRotator = null;
    }
  }

  function showLoading() {
    if (!articleEl) return;
    articleEl.innerHTML =
      '<div class="blog-loading" role="status" aria-live="polite">' +
      '<h2 class="blog-loading-headline">Writing your blog<span class="dots"></span></h2>' +
      '<p class="blog-loading-substep" id="blog-pane-substep"></p>' +
      "</div>";
    const sub = articleEl.querySelector("#blog-pane-substep");
    let i = 0;
    if (sub) sub.textContent = PANE_LOADING_MESSAGES[0];
    stopMsgRotator();
    msgRotator = setInterval(() => {
      if (!sub) return;
      i = (i + 1) % PANE_LOADING_MESSAGES.length;
      sub.textContent = PANE_LOADING_MESSAGES[i];
    }, 4200);
    if (reviseBtn) reviseBtn.hidden = true;
    if (metaEl) metaEl.textContent = "";
  }

  function showError(message) {
    if (!articleEl) return;
    stopMsgRotator();
    articleEl.innerHTML =
      '<div class="blog-error" style="padding:2rem;border:1px solid #f59e0b;border-radius:8px;background:rgba(245,158,11,0.08);">' +
      '<h2 style="margin-top:0;">Blog generation failed</h2>' +
      '<p style="opacity:0.85;"></p>' +
      '<button type="button" class="btn-secondary" id="blog-pane-retry" style="margin-top:1rem;">Try again</button>' +
      "</div>";
    const p = articleEl.querySelector(".blog-error p");
    if (p) p.textContent = String(message || "Something went wrong.");
    const retry = articleEl.querySelector("#blog-pane-retry");
    if (retry) retry.addEventListener("click", openGenerateModal);
  }

  function showEmptyCta() {
    if (!articleEl) return;
    stopMsgRotator();
    articleEl.innerHTML =
      '<div class="blog-pane-cta">' +
      '<div class="blog-pane-cta-icon" aria-hidden="true"><i class="fa-solid fa-pen-nib"></i></div>' +
      "<h2>Read this report as a blog</h2>" +
      "<p>A clean explainer with diagrams, math, and visuals — generated once, saved with the report.</p>" +
      '<button type="button" class="btn-secondary" id="blog-pane-generate">Generate blog view</button>' +
      "</div>";
    const btn = articleEl.querySelector("#blog-pane-generate");
    if (btn) btn.addEventListener("click", openGenerateModal);
  }

  // ------------------------------------------------------------------
  // Rendering
  // ------------------------------------------------------------------
  async function renderBlog(markdown) {
    if (!articleEl) return;
    stopMsgRotator();
    try {
      const title = await window.ohBlogRender.renderInto(articleEl, markdown);
      if (metaEl) {
        metaEl.textContent =
          (title ? title + " · " : "") +
          window.ohBlogRender.readingTime(markdown) +
          " min read";
      }
      if (reviseBtn) reviseBtn.hidden = false;
    } catch (err) {
      console.error("[blog-pane] render failed", err);
      showError("The blog loaded but could not be rendered.");
    }
  }

  // ------------------------------------------------------------------
  // Status + polling
  // ------------------------------------------------------------------
  async function fetchState() {
    const id = reportId();
    if (!id) return { state: "empty" };
    const resp = await fetch(`/reports/${encodeURIComponent(id)}/blog`, {
      headers: { Accept: "application/json" },
    });
    if (resp.status === 401 || resp.status === 404) return { state: "empty" };
    if (!resp.ok) return { state: "empty" };
    return resp.json();
  }

  async function pollToken(token, signal) {
    const start = Date.now();
    const url = `/blog-status?token=${encodeURIComponent(token)}`;
    while (Date.now() - start < POLL_TIMEOUT_MS) {
      if (signal.cancelled) return null;
      const resp = await fetch(url, { headers: { Accept: "application/json" } });
      if (resp.ok) {
        const data = await resp.json();
        if (data.state === "ready" && typeof data.markdown === "string") {
          return data.markdown;
        }
        if (data.state === "error") {
          throw new Error(data.error || "Blog generation failed.");
        }
      }
      await new Promise((r) => setTimeout(r, POLL_MS));
    }
    throw new Error("Timed out waiting for blog generation.");
  }

  // ------------------------------------------------------------------
  // Generate + revise flows
  // ------------------------------------------------------------------
  function openCustomizeModal(opts) {
    if (window.ohBlog && window.ohBlog.openCustomizeModal) {
      return window.ohBlog.openCustomizeModal(opts);
    }
    if (opts && typeof opts.onSubmit === "function") {
      opts.onSubmit({ audience: "technical", length: "medium", tone: "distill", extra: "" });
    }
  }

  function openGenerateModal() {
    openCustomizeModal({
      onSubmit: (opts) => launchGeneration(opts),
    });
  }

  async function launchGeneration(opts) {
    const id = reportId();
    const el = reportEl();
    const cleanFn =
      (window.ohBlog && window.ohBlog.getCleanReportText) ||
      (typeof getCleanReportText === "function" ? getCleanReportText : null);
    const cleaned = el && cleanFn ? cleanFn(el) : "";

    showLoading();
    pollAbort = { cancelled: false };
    const signal = pollAbort;

    const fd = new FormData();
    fd.append("report_text", cleaned);
    if (opts) {
      if (opts.audience) fd.append("audience", opts.audience);
      if (opts.length) fd.append("length", opts.length);
      if (opts.tone) fd.append("tone", opts.tone);
      if (opts.theme) fd.append("theme", opts.theme);
      if (opts.extra) fd.append("extra", opts.extra);
    }
    if (id) fd.append("report_id", id);

    try {
      const resp = await fetch("/generate-blog", { method: "POST", body: fd });
      if (!resp.ok) throw new Error(`Server responded ${resp.status}`);
      const data = await resp.json();
      const md = await pollToken(data.token, signal);
      if (md == null || signal.cancelled) return;
      await renderBlog(md);
    } catch (err) {
      if (signal.cancelled) return;
      console.error("[blog-pane] generation failed", err);
      showError(err && err.message ? err.message : String(err));
    }
  }

  function startRevise() {
    openCustomizeModal({
      mode: "regenerate",
      current: { audience: "", length: "", tone: "", theme: "", extra: "" },
      onSubmit: (opts) => launchRevise(opts),
    });
  }

  async function launchRevise(opts) {
    const id = reportId();
    if (!id) return;

    showLoading();
    pollAbort = { cancelled: false };
    const signal = pollAbort;

    const fd = new FormData();
    fd.append("report_id", id);
    if (opts) {
      if (opts.audience) fd.append("audience", opts.audience);
      if (opts.length) fd.append("length", opts.length);
      if (opts.tone) fd.append("tone", opts.tone);
      if (opts.theme) fd.append("theme", opts.theme);
      if (opts.extra) fd.append("extra", opts.extra);
    }

    try {
      const resp = await fetch("/regenerate-blog", { method: "POST", body: fd });
      if (!resp.ok) {
        const body = await resp.json().catch(() => ({}));
        throw new Error(body.detail || `Server responded ${resp.status}`);
      }
      const data = await resp.json();
      const md = await pollToken(data.token, signal);
      if (md == null || signal.cancelled) return;
      await renderBlog(md);
    } catch (err) {
      if (signal.cancelled) return;
      console.error("[blog-pane] revise failed", err);
      showError(err && err.message ? err.message : String(err));
    }
  }

  // ------------------------------------------------------------------
  // Entry point — the Blog side of the view toggle
  // ------------------------------------------------------------------
  async function openBlogView() {
    if (!ensurePane()) return;
    setView("blog");

    showLoading();
    pollAbort = { cancelled: false };
    const signal = pollAbort;

    try {
      const state = await fetchState();
      if (signal.cancelled) return;
      if (state.state === "ready" && state.markdown) {
        await renderBlog(state.markdown);
      } else if (state.state === "pending" && state.token) {
        const md = await pollToken(state.token, signal);
        if (md == null || signal.cancelled) return;
        await renderBlog(md);
      } else if (state.state === "error") {
        showError(state.error || "Blog generation failed.");
      } else {
        showEmptyCta();
      }
    } catch (err) {
      if (signal.cancelled) return;
      console.error("[blog-pane] open failed", err);
      showEmptyCta();
    }
  }

  // ------------------------------------------------------------------
  // Wiring
  // ------------------------------------------------------------------
  // Segmented toggle (rendered by the page; both report surfaces use it).
  document.addEventListener("click", (ev) => {
    const btn = ev.target.closest("#report-view-toggle [data-view]");
    if (!btn) return;
    ev.preventDefault();
    if (btn.dataset.view === "blog") {
      openBlogView();
    } else {
      setView("report");
    }
  });

  // The "Blog" action button on the streamed report page: when the report was
  // persisted (dataset.reportId set by the report-saved SSE event), route to
  // the in-page pane. Without an id the original popup flow in report.js runs.
  document.addEventListener(
    "click",
    (ev) => {
      const btn = ev.target.closest("#report-blog-btn");
      if (!btn || btn.disabled) return;
      if (!reportId()) return; // anonymous / unsaved → let report.js open the popup
      ev.preventDefault();
      ev.stopImmediatePropagation();
      openBlogView();
    },
    true
  );

  window.ohBlogPane = { open: openBlogView, close: () => setView("report") };
})();
