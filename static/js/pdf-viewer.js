// pdf-viewer.js – lightweight loader for pdf.js.
// We load the standard UMD build (global `pdfjsLib`) dynamically so we avoid ESM quirks / CORS 404s.
console.log("[pdf-viewer] script loaded");

const PDFJS_VERSION = "3.11.174";
const CDN_BASE = `https://cdn.jsdelivr.net/npm/pdfjs-dist@${PDFJS_VERSION}/build`;

// Inject official pdf.js text-layer CSS once so overlay text is invisible but selectable.
if (!document.querySelector('link[data-pdfjs-css]')) {
  const cssLink = document.createElement('link');
  cssLink.rel = 'stylesheet';
  cssLink.href = `https://cdn.jsdelivr.net/npm/pdfjs-dist@${PDFJS_VERSION}/web/pdf_viewer.css`;
  cssLink.setAttribute('data-pdfjs-css', '1');
  document.head.appendChild(cssLink);
}

async function loadPdfJs() {
  if (window.pdfjsLib) return window.pdfjsLib;
  return new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = `${CDN_BASE}/pdf.min.js`;
    script.async = true;
    script.onload = () => {
      if (window.pdfjsLib) resolve(window.pdfjsLib);
      else reject(new Error("pdfjsLib global missing after load"));
    };
    script.onerror = () => reject(new Error("Failed to load pdf.js library"));
    document.head.appendChild(script);
  });
}

// Load pdf.js WebViewer script & CSS once
async function loadPdfViewer() {
  if (window.pdfjsViewer) return window.pdfjsViewer;

  await loadPdfJs();

  // Inject viewer CSS (only once)
  if (!document.getElementById("pdfjs-viewer-css")) {
    const link = document.createElement("link");
    link.id = "pdfjs-viewer-css";
    link.rel = "stylesheet";
    link.href = `https://cdn.jsdelivr.net/npm/pdfjs-dist@${PDFJS_VERSION}/web/pdf_viewer.css`;
    document.head.appendChild(link);
  }

  return new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = `https://cdn.jsdelivr.net/npm/pdfjs-dist@${PDFJS_VERSION}/web/pdf_viewer.js`;
    script.async = true;
    script.onload = () => resolve(window.pdfjsViewer);
    script.onerror = () => reject(new Error("Failed to load pdf.js viewer"));
    document.head.appendChild(script);
  });
}

// Scaling handled by pdf.js WebViewer; no extra constants needed.

async function renderPdf(container) {
  // Wait until container is visible (non-zero width) – prevents blank render
  if (container.clientWidth < 50) {
    // Retry once layout stabilises
    requestAnimationFrame(() => renderPdf(container));
    return;
  }

  // Insert a lightweight loading overlay (unique per render)
  const spinner = document.createElement("div");
  spinner.className = "pdf-loading";
  spinner.innerHTML =
    '<svg class="spin" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#888" stroke-width="2"><circle cx="12" cy="12" r="10" stroke-opacity="0.25"/><path d="M22 12a10 10 0 0 1-10 10" stroke-linecap="round"/></svg> <span style="margin-left:6px;">Loading…</span>';
  container.appendChild(spinner);

  const pdfjsLib = await loadPdfJs();
  const pdfjsViewer = await loadPdfViewer();

  const { GlobalWorkerOptions, getDocument } = pdfjsLib;
  const { EventBus, PDFLinkService, PDFViewer } = pdfjsViewer;

  if (!GlobalWorkerOptions.workerSrc) {
    GlobalWorkerOptions.workerSrc = `${CDN_BASE}/pdf.worker.min.js`;
  }

  const src = container.dataset.pdfSrc;
  if (!src || container.dataset.rendered) return;
  container.dataset.rendered = "1";

  // Keep outer .pdf-container in flex layout; create inner scroll box required by PDFJS
  container.style.position = "relative";

  const scrollEl = document.createElement("div");
  scrollEl.style.position = "absolute";
  scrollEl.style.top = 0;
  scrollEl.style.left = 0;
  scrollEl.style.right = 0;
  scrollEl.style.bottom = 0;
  scrollEl.style.overflow = "auto";
  container.appendChild(scrollEl);

  const viewerEl = document.createElement("div");
  viewerEl.className = "pdfViewer";
  scrollEl.appendChild(viewerEl);

  // Set up viewer plumbing (event bus, link svc)
  const eventBus = new EventBus();
  const linkService = new PDFLinkService({ eventBus });
  const viewer = new PDFViewer({
    container: scrollEl, // scroll container (absolute inside)
    viewer: viewerEl,   // inner pages container
    eventBus,
    linkService,
    textLayerMode: 2, // selectable text layer on
    maxCanvasPixels: 0, // enable CSS-based zoom without raster limits
  });
  linkService.setViewer(viewer);

  try {
    const pdfDoc = await getDocument({ url: src, withCredentials: false, fontExtraProperties: true }).promise;
    viewer.setDocument(pdfDoc);
    linkService.setDocument(pdfDoc);
    // "auto" fits the page to the viewer width — the old page-width +
    // scale>1 clamp left the doc smaller than the pane on wide viewers.
    viewer.currentScaleValue = "auto";

    eventBus.on("pagesinit", () => {
      spinner.remove();
    });

    // --- Enhanced zoom UI --------------------------
    const zoomBar = document.createElement("div");
    // Fixed to viewport, horizontally tracks PDF column. Hidden until hover.
    zoomBar.style.cssText = "position:fixed;top:16px;z-index:1000;display:flex;align-items:center;gap:4px;background:#fff;border:2px solid #222;border-radius:6px;padding:3px 6px;font-family:system-ui,sans-serif;font-size:13px;box-shadow:0 3px 8px rgba(0,0,0,0.4);opacity:0;transition:opacity .2s;pointer-events:auto;";

    const makeBtn = (sign) => {
      const b = document.createElement("button");
      b.textContent = "🔍"; // magnifier base
      b.style.cssText = "position:relative;border:none;background:none;padding:2px 4px;cursor:pointer;font-size:18px;line-height:1;";
      const overlay = document.createElement("span");
      overlay.textContent = sign;
      overlay.style.cssText = "position:absolute;left:50%;top:50%;transform:translate(-85%,-85%);font-size:10px;font-weight:bold;color:#000;pointer-events:none;";
      b.appendChild(overlay);
      b.onmouseover = () => (b.style.background = '#e8e8e8');
      b.onmouseout = () => (b.style.background = 'none');
      return b;
    };

    const percentSpan = document.createElement("span");

    const updatePercent = () => {
      percentSpan.textContent = Math.round(viewer.currentScale * 100) + "%";
    };

    const zoomInBtn = makeBtn("+");
    const zoomOutBtn = makeBtn("−");

    // Zoom settings: 20% increments (0.2 scale) between 20% and 200%
    const step = 0.2;
    const clamp = (v, min, max) => Math.min(Math.max(v, min), max);
    const quantize = (v) => Math.round(v / step) * step;

    zoomInBtn.onclick = () => {
      const next = quantize(viewer.currentScale + step);
      viewer.currentScale = clamp(next, 0.2, 2);
      updatePercent();
    };

    zoomOutBtn.onclick = () => {
      const next = quantize(viewer.currentScale - step);
      viewer.currentScale = clamp(next, 0.2, 2);
      updatePercent();
    };

    // Keep percent display in sync with any programmatic scale changes
    eventBus.on("scalechanging", () => updatePercent());

    updatePercent();
    zoomBar.appendChild(zoomOutBtn);
    zoomBar.appendChild(percentSpan);
    zoomBar.appendChild(zoomInBtn);

    document.body.appendChild(zoomBar);

    // Invisible hotspot to reveal the bar
    const hotspot = document.createElement("div");
    hotspot.style.cssText = "position:fixed;top:0;height:60px;width:80px;z-index:900;opacity:0;";
    document.body.appendChild(hotspot);

    // Function to keep bar (and hotspot) aligned with PDF column
    const placeAtPdfCorner = () => {
      const rect = scrollEl.getBoundingClientRect();
      const leftPx = rect.right - zoomBar.offsetWidth - 16;
      const topPx = Math.max(rect.top + 8, 8); // 8px inside PDF, but never above viewport
      zoomBar.style.left = `${leftPx}px`;
      zoomBar.style.top = `${topPx}px`;

      hotspot.style.left = `${rect.right - 80}px`;
      hotspot.style.top = `${Math.max(rect.top, 0)}px`;
    };

    placeAtPdfCorner();
    window.addEventListener("scroll", placeAtPdfCorner);
    window.addEventListener("resize", placeAtPdfCorner);

    // Hover logic
    let hideTimer;
    const showBar = () => {
      clearTimeout(hideTimer);
      zoomBar.style.opacity = 1;
    };
    const scheduleHide = () => {
      clearTimeout(hideTimer);
      hideTimer = setTimeout(() => (zoomBar.style.opacity = 0), 800);
    };
    [hotspot, zoomBar].forEach(el => {
      el.addEventListener("mouseenter", showBar);
      el.addEventListener("mouseleave", scheduleHide);
    });
    // --- End zoom UI ------------------------------
  } catch (err) {
    console.warn("pdf.js viewer error", err);
    container.innerHTML =
      '<div style="padding:1rem;color:#b00;">Unable to display PDF inline. <a href="' +
      src +
      '" target="_blank" rel="noopener" style="color:#06c;text-decoration:underline;">Open in new tab</a>.</div>';
  }

  // Shortcut: Cmd/Ctrl+L — quote current selection into chat box
  const quoteSelectionToChat = () => {
    const sel = window.getSelection();
    let text = sel ? sel.toString() : '';
    if (text) {
      text = text.replace(/\s+/g, ' ').replace(/\u00A0/g, ' ').trim();
    }
    if (!text) return;
    const quoted = `"${text}" `;
    const chatBox = document.querySelector('.chat-panel textarea[name="message"]');
    if (chatBox) {
      chatBox.value = quoted + chatBox.value;
      chatBox.focus();
    }
  };

  // Listen at document level so it works even if focus is on the PDF canvas.
  document.addEventListener('keydown', (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'l') {
      e.preventDefault();
      quoteSelectionToChat();
    }
  });
}

function init() {
  const processContainers = (root) => {
    root.querySelectorAll?.(".pdf-container").forEach((el) => {
      // If this container was previously rendered (attribute set), purge children
      // so re-render works after modal reopen.
      if (el.dataset.rendered) {
        el.innerHTML = "";
        delete el.dataset.rendered;
      }
      renderPdf(el);
    });
  };

  // Initial containers
  processContainers(document);

  // Handle HTMX swaps / dynamic injections
  document.body.addEventListener("htmx:afterSwap", (e) => {
    processContainers(e.target);
  });

  // Fallback: observe DOM for any future .pdf-container nodes (covers cases where
  // modal HTML is inserted without triggering htmx events, e.g. raw innerHTML).
  const mo = new MutationObserver((mutList) => {
    for (const m of mutList) {
      for (const n of m.addedNodes) {
        if (n.nodeType !== 1) continue; // elements only
        if (n.classList?.contains("pdf-container")) {
          processContainers(n);
        } else {
          n.querySelectorAll?.(".pdf-container").forEach((el) => processContainers(el));
        }
      }
    }
  });
  mo.observe(document.body, { childList: true, subtree: true });
}

init();
