/* Shared workspace behavior. All research data still comes from existing APIs. */
(() => {
  "use strict";
  const topicTabs = Array.from(
    document.querySelectorAll("[data-workspace-tab]"),
  );
  function selectTab(tab, focus = false) {
    topicTabs.forEach((button) => {
      const selected = button === tab;
      button.setAttribute("aria-selected", String(selected));
      button.tabIndex = selected ? 0 : -1;
      document.getElementById(button.getAttribute("aria-controls")).hidden =
        !selected;
    });
    if (focus) tab.focus();
    const url = new URL(location.href);
    url.hash = tab.dataset.workspaceTab;
    history.replaceState(null, "", url);
    document.dispatchEvent(
      new CustomEvent("oh:topic-view", { detail: tab.dataset.workspaceTab }),
    );
  }
  topicTabs.forEach((tab, index) => {
    tab.addEventListener("click", () => selectTab(tab));
    tab.addEventListener("keydown", (event) => {
      let target;
      if (event.key === "ArrowRight") target = (index + 1) % topicTabs.length;
      if (event.key === "ArrowLeft")
        target = (index - 1 + topicTabs.length) % topicTabs.length;
      if (event.key === "Home") target = 0;
      if (event.key === "End") target = topicTabs.length - 1;
      if (target === undefined) return;
      event.preventDefault();
      selectTab(topicTabs[target], true);
    });
  });
  const initialTab = topicTabs.find(
    (tab) => tab.dataset.workspaceTab === location.hash.slice(1),
  );
  if (initialTab) selectTab(initialTab);

  const isDark = () => document.documentElement.classList.contains("oh-dark");
  const readers = new Set();
  function initializeReader(root) {
    if (root.dataset.workspaceReader) return;
    root.dataset.workspaceReader = "1";
    readers.add(root);
    const lifecycle = new AbortController();
    root.ohCleanupReader = () => lifecycle.abort();
    let originalOverride = null;
    let pdf = null;
    const tools = document.createElement("div");
    tools.className = "reader-controls";
    tools.innerHTML =
      '<button type="button" data-reader-tools aria-haspopup="dialog" aria-expanded="false">Contents &amp; marks</button><div><button type="button" data-reader-original aria-pressed="false" title="Original page preserves the PDF’s source colors; dark paper changes its rendering colors.">Original page</button><button type="button" data-reader-focus aria-pressed="false">Focus</button></div>';
    root.querySelector(".viewer-grid")?.before(tools);
    const opener = tools.querySelector("[data-reader-tools]");
    const appearance = tools.querySelector("[data-reader-original]");
    try {
      const source = new URL(root.dataset.srcUrl);
      if (["https:", "http:"].includes(source.protocol)) {
        const link = document.createElement("a");
        link.href = source.href;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.textContent =
          root.dataset.viewer === "pdf" ? "Source PDF ↗" : "Source page ↗";
        tools.querySelector("div").prepend(link);
      }
    } catch (_) {
      /* Some text-only readers have no external source. */
    }
    const dialog = document.createElement("dialog");
    dialog.className = "reader-tools-dialog";
    dialog.setAttribute("aria-label", "Contents and marks");
    dialog.innerHTML =
      '<div class="reader-tools-header"><h3>Contents &amp; marks</h3><button type="button" aria-label="Close contents and marks" autofocus>×</button></div><div class="reader-tools-body"><h4>CONTENTS</h4><div data-reader-contents><p>Contents appear when a PDF has loaded.</p></div><section class="reader-marks"><h4>SAVED PASSAGES</h4><p>Select text in the paper, then save it here. Marks are saved on this browser.</p><button type="button" class="reader-save-mark">Save selected passage</button><p data-reader-mark-status role="status"></p><div data-reader-marks></div></section></div>';
    root.append(dialog);
    opener.addEventListener("click", () => {
      dialog.showModal();
      opener.setAttribute("aria-expanded", "true");
    });
    dialog
      .querySelector(".reader-tools-header button")
      .addEventListener("click", () => dialog.close());
    // A nested native dialog owns Escape. Do not let the reader's global
    // modal shortcut dismiss the paper behind it in the same key event.
    dialog.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        dialog.close();
      }
    });
    dialog.addEventListener("cancel", (event) => {
      event.preventDefault();
      dialog.close();
    });
    dialog.addEventListener("close", () => {
      opener.setAttribute("aria-expanded", "false");
      opener.focus({ preventScroll: true });
    });
    dialog.addEventListener("click", (event) => {
      if (event.target !== dialog) return;
      const rect = dialog.getBoundingClientRect();
      if (
        event.clientX < rect.left ||
        event.clientX > rect.right ||
        event.clientY < rect.top ||
        event.clientY > rect.bottom
      )
        dialog.close();
    });
    const focus = tools.querySelector("[data-reader-focus]");
    focus.addEventListener("click", () => {
      const focused = root.classList.toggle("reader-focused");
      focus.setAttribute("aria-pressed", String(focused));
      focus.textContent = focused ? "Show chat" : "Focus";
      const chat = root.querySelector(".chat-column");
      if (chat) chat.inert = focused;
      window.dispatchEvent(new Event("resize"));
    });
    function syncPaper(reset = false) {
      if (reset) originalOverride = null;
      const original = originalOverride ?? !isDark();
      root.dataset.readerOriginal = String(original);
      appearance.setAttribute("aria-pressed", String(original));
      appearance.disabled = !pdf;
      root.dispatchEvent(
        new CustomEvent("oh:paper-tone", { detail: { dark: !original } }),
      );
    }
    appearance.addEventListener("click", () => {
      originalOverride = root.dataset.readerOriginal !== "true";
      syncPaper();
    });
    root.ohSyncPaper = () => syncPaper(true);
    syncPaper();
    const storageKey = () =>
      root.dataset.readerKey
        ? "oh-paper-marks:" + root.dataset.readerKey
        : null;
    let key = storageKey();
    let marks = [];
    function loadMarks() {
      marks = [];
      if (!key) return; // Never read unscoped/legacy keys across accounts.
      try {
        const saved = JSON.parse(localStorage.getItem(key) || "[]");
        if (Array.isArray(saved))
          marks = saved
            .filter(
              (mark) =>
                typeof mark?.text === "string" &&
                Number.isInteger(mark.page) &&
                mark.page > 0,
            )
            .slice(0, 50);
      } catch (_) {
        /* Storage may be disabled; keep the reader usable. */
      }
    }
    loadMarks();
    function renderMarks() {
      const list = dialog.querySelector("[data-reader-marks]");
      list.replaceChildren();
      if (!marks.length) {
        const empty = document.createElement("p");
        empty.textContent = "No saved passages yet.";
        list.append(empty);
      }
      marks.forEach((mark) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "reader-mark";
        button.textContent = mark.text;
        const page = document.createElement("small");
        page.textContent = `Page ${mark.page}`;
        button.append(page);
        button.addEventListener("click", () => {
          if (pdf && mark.page <= pdf.pdfDoc.numPages)
            pdf.viewer.currentPageNumber = mark.page;
          dialog.close();
        });
        list.append(button);
      });
    }
    renderMarks();
    let selection = "",
      selectionPage = 1;
    root
      .querySelector(".topic-paper-select")
      ?.addEventListener("change", (event) => {
        root.dataset.readerKey =
          event.target.selectedOptions[0]?.dataset.readerKey || "";
        key = storageKey();
        selection = "";
        pdf = null; // The old PDF must not accept marks while the next one loads.
        appearance.disabled = true;
        loadMarks();
        renderMarks();
        dialog.querySelector("[data-reader-contents]").textContent =
          "Loading contents…";
      });
    document.addEventListener(
      "selectionchange",
      () => {
        if (!root.isConnected) return;
        const selected = window.getSelection();
        if (
          selected?.anchorNode &&
          root.querySelector(".doc-pane-wrap")?.contains(selected.anchorNode)
        ) {
          const text = selected.toString().replace(/\s+/g, " ").trim();
          if (text) {
            const node = selected.rangeCount
              ? selected.getRangeAt(0).startContainer
              : null;
            const element = node?.nodeType === 1 ? node : node?.parentElement;
            const page = Number(
              element?.closest(".page[data-page-number]")?.dataset.pageNumber,
            );
            selection =
              Number.isInteger(page) && page > 0 ? text.slice(0, 1200) : "";
            selectionPage = page;
          }
        }
      },
      { signal: lifecycle.signal },
    );
    dialog.querySelector(".reader-save-mark").addEventListener("click", () => {
      const status = dialog.querySelector("[data-reader-mark-status]");
      if (!key) {
        status.textContent =
          "Saving passages is unavailable without a private reader identity.";
        return;
      }
      if (!selection || !pdf) {
        status.textContent = "Select a passage in the PDF first.";
        return;
      }
      const updated = [
        { text: selection, page: selectionPage },
        ...marks,
      ].slice(0, 50);
      try {
        localStorage.setItem(key, JSON.stringify(updated));
        marks = updated;
        selection = "";
        status.textContent = "Passage saved on this browser.";
        renderMarks();
      } catch (_) {
        status.textContent =
          "Could not save this passage. Browser storage may be full or disabled.";
      }
    });
    root.addEventListener("oh:pdf-ready", async (event) => {
      pdf = event.detail;
      appearance.disabled = false;
      const contents = dialog.querySelector("[data-reader-contents]");
      contents.replaceChildren();
      try {
        const outline = await pdf.pdfDoc.getOutline();
        if (!root.isConnected) return;
        function appendItems(items, depth = 0) {
          items.forEach((item) => {
            const button = document.createElement("button");
            button.type = "button";
            button.className = "reader-outline-item";
            button.textContent = item.title;
            button.style.paddingLeft = `${Math.min(depth, 4) * 10}px`;
            button.addEventListener("click", () => {
              if (item.dest) pdf.linkService.goToDestination(item.dest);
              dialog.close();
            });
            contents.append(button);
            if (item.items?.length) appendItems(item.items, depth + 1);
          });
        }
        if (outline?.length) appendItems(outline);
        else
          contents.textContent =
            "This PDF has no table of contents. You can still save passages below.";
      } catch (_) {
        contents.textContent = "The table of contents could not be loaded.";
      }
    });
  }
  function scanReaders() {
    readers.forEach((root) => {
      if (!root.isConnected) {
        root.ohCleanupReader();
        readers.delete(root);
      }
    });
    document
      .querySelectorAll(".modal-content.pdf-viewer")
      .forEach(initializeReader);
  }
  scanReaders();
  new MutationObserver(scanReaders).observe(document.body, {
    childList: true,
    subtree: true,
  });
  new MutationObserver(() => {
    document
      .querySelectorAll(".pdf-viewer")
      .forEach((root) => root.ohSyncPaper?.());
  }).observe(document.documentElement, {
    attributes: true,
    attributeFilter: ["class"],
  });
})();
