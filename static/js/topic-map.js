/* Topic map: real UMAP results, stable coordinates while exploring a collection. */
(() => {
  "use strict";
  const root = document.getElementById("topic-paper-map");
  if (!root) return;
  let data = null,
    group = null,
    paper = null,
    loading = false,
    plotObserver = null;
  const labels = { to_read: "To read", reading: "Reading", done: "Done" };
  const el = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };
  const button = (text, handler, className) => {
    const node = el("button", className, text);
    node.type = "button";
    node.addEventListener("click", handler);
    return node;
  };
  const visible = () =>
    data.papers.filter((p) => group === null || p.cluster === group);
  const activeGroup = () => data.clusters.find((c) => c.id === group);
  function placeLabels(plot) {
    const bounds = plot.getBoundingClientRect();
    const points = Array.from(plot.querySelectorAll(".topic-map-point")).map(
      (point) => {
        const r = point.getBoundingClientRect();
        return {
          left: r.left - bounds.left - 4,
          right: r.right - bounds.left + 4,
          top: r.top - bounds.top - 4,
          bottom: r.bottom - bounds.top + 4,
        };
      },
    );
    const occupied = [...points];
    const intersects = (a, b) =>
      a.left < b.right &&
      a.right > b.left &&
      a.top < b.bottom &&
      a.bottom > b.top;
    plot.querySelectorAll(".topic-map-label").forEach((label) => {
      if (!label.offsetWidth) return; // Small screens use the named filter chips.
      const w = label.offsetWidth,
        h = label.offsetHeight;
      const baseX = (Number(label.dataset.anchorX) * bounds.width) / 100;
      const baseY = (Number(label.dataset.anchorY) * bounds.height) / 100;
      let placed = false;
      // Deterministic local placement changes labels, never embedding points.
      const shifts = [0, -24, 24, -48, 48, -72, 72, -96, 96];
      for (const dy of shifts) {
        for (const dx of shifts) {
          const x = Math.max(
              w / 2 + 4,
              Math.min(bounds.width - w / 2 - 4, baseX + dx),
            ),
            y = baseY + dy;
          const rect = {
            left: x - w / 2,
            right: x + w / 2,
            top: y - h,
            bottom: y,
          };
          if (
            rect.top < 4 ||
            rect.bottom > bounds.height - 4 ||
            occupied.some((other) => intersects(rect, other))
          )
            continue;
          label.style.left = `${x}px`;
          label.style.top = `${y}px`;
          label.style.visibility = "visible";
          occupied.push(rect);
          placed = true;
          break;
        }
        if (placed) break;
      }
      // Keep the accessible named chips instead of drawing an overlapping label.
      if (!placed) label.style.visibility = "hidden";
    });
  }
  function openPaper(p) {
    if (!p.url) return;
    const params = new URLSearchParams({
      url: p.url,
      title: p.title || "Paper",
    });
    window.htmx?.ajax("GET", `/viewer/${encodeURIComponent(p.id)}?${params}`, {
      target: "#modal-container",
      swap: "innerHTML",
    });
  }
  function choosePaper(p) {
    paper = p.id;
    group = p.cluster;
    render({ paper: p.id });
  }
  async function setReading(p, control) {
    control.disabled = true;
    try {
      const response = await fetch("/api/library/status", {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams({ paper_id: p.id, status: "reading" }),
      });
      const result = await response.json();
      if (!response.ok || !result.ok)
        throw new Error(result.detail || "Could not update reading status.");
      p.status = "reading";
      document.querySelectorAll(".lib-status-select").forEach((select) => {
        if (select.dataset.paperId === p.id) select.value = "reading";
      });
      render({ paper: p.id });
    } catch (error) {
      const status = root.querySelector("[data-map-status]");
      status.textContent = error.message;
      control.disabled = false;
    }
  }
  function render(focus) {
    const rows = visible();
    const cluster = activeGroup();
    plotObserver?.disconnect();
    root.replaceChildren();
    const heading = el("div", "topic-map-heading");
    const intro = el("div");
    intro.append(
      el("h2", "", "Find your next paper"),
      el(
        "p",
        "",
        "Follow a subtopic, or select a paper to take a closer look.",
      ),
    );
    heading.append(
      intro,
      button("Refresh map", () => load(true), "btn-secondary"),
    );
    root.append(heading);
    const groups = el("div", "topic-map-groups");
    groups.setAttribute("aria-label", "Filter by subtopic");
    const all = button(`All papers · ${data.papers.length}`, () => {
      group = null;
      paper = null;
      render({ group: "all" });
    });
    all.dataset.mapGroup = "all";
    all.setAttribute("aria-pressed", String(group === null));
    groups.append(all);
    data.clusters.forEach((c) => {
      const count = data.papers.filter((p) => p.cluster === c.id).length;
      const control = button(`${c.label} · ${count}`, () => {
        group = c.id;
        paper = null;
        render({ group: String(c.id) });
      });
      if (c.id === -1)
        control.title = "These papers have no confident subtopic assignment.";
      control.dataset.mapGroup = String(c.id);
      control.setAttribute("aria-pressed", String(group === c.id));
      groups.append(control);
    });
    root.append(groups);
    const layout = el("div", "topic-map-layout"),
      explorer = el("div", "topic-map-plot-wrap"),
      plot = el("div", "topic-map-plot");
    plot.setAttribute("role", "group");
    plot.setAttribute(
      "aria-label",
      "Paper similarity map. Use the named subtopics to filter.",
    );
    data.clusters.forEach((c) => {
      const items = data.papers.filter((p) => p.cluster === c.id);
      if (!items.length) return;
      const x = items.reduce((sum, p) => sum + p.x, 0) / items.length;
      const y = Math.min(...items.map((p) => p.y)) - 0.18;
      const label = el("span", "topic-map-label", c.label);
      label.dataset.anchorX = String(12 + (x + 1) * 38);
      label.dataset.anchorY = String(19 + (y + 1) * 32);
      label.style.left = `${label.dataset.anchorX}%`;
      label.style.top = `${label.dataset.anchorY}%`;
      plot.append(label);
    });
    data.papers.forEach((p) => {
      const point = button("", () => choosePaper(p), "topic-map-point");
      point.style.left = `${12 + (p.x + 1) * 38}%`;
      point.style.top = `${19 + (p.y + 1) * 32}%`;
      point.dataset.status = p.status || "to_read";
      point.dataset.mapPaper = p.id;
      point.classList.toggle("dimmed", group !== null && group !== p.cluster);
      point.setAttribute("aria-pressed", String(paper === p.id));
      point.setAttribute(
        "aria-label",
        `${p.title} — ${labels[p.status] || labels.to_read}`,
      );
      point.title = `${p.title} · ${labels[p.status] || labels.to_read}`;
      point.append(el("span"));
      plot.append(point);
    });
    explorer.append(plot);
    const key = el("div", "topic-map-key");
    Object.entries(labels).forEach(([status, label]) => {
      const item = el("span");
      item.append(el("i", status), document.createTextNode(label));
      key.append(item);
    });
    explorer.append(
      key,
      el(
        "p",
        "topic-map-caption",
        "Nearby papers cover related ideas. Distance is approximate, not a measure of quality. Subtopic names come from paper titles.",
      ),
      el(
        "p",
        "topic-map-state",
        "Layout stays fixed while filtering. Refresh recalculates the map.",
      ),
    );
    const side = el("aside", "topic-map-side");
    side.append(
      el("h3", "", `${cluster?.label || "All papers"} · ${rows.length}`),
    );
    const list = el("div", "topic-map-list");
    list.setAttribute("aria-label", "Papers in the selected subtopic");
    rows.forEach((p) => {
      const row = el("article", "topic-map-row");
      row.classList.toggle("selected", paper === p.id);
      row.dataset.mapRow = p.id;
      const select = button(p.title, () => choosePaper(p));
      select.setAttribute("aria-pressed", String(paper === p.id));
      row.append(
        select,
        el(
          "small",
          "",
          [
            (p.authors || []).slice(0, 2).join(", "),
            p.year,
            labels[p.status] || labels.to_read,
          ]
            .filter(Boolean)
            .join(" · "),
        ),
      );
      if (paper === p.id) {
        const actions = el("div", "topic-map-row-actions");
        if (p.url) actions.append(button("Open paper →", () => openPaper(p)));
        const read = button(
          p.status === "reading" ? "Reading" : "Mark reading",
          () => setReading(p, read),
        );
        read.disabled = p.status === "reading";
        actions.append(read);
        row.append(actions);
      }
      list.append(row);
    });
    side.append(list);
    const chat = el("section", "topic-map-chat");
    chat.append(
      el("h4", "", "Topic chat"),
      el(
        "p",
        "",
        cluster
          ? `Only ${cluster.label} · ${rows.length} papers`
          : `Across this topic · ${rows.length} papers`,
      ),
    );
    chat.append(
      button(
        cluster
          ? cluster.id === -1
            ? "Ask these papers →"
            : "Ask this subtopic →"
          : "Ask this topic →",
        () => {
          const params = new URLSearchParams({
            paper_id: "topic",
            topic_id: root.dataset.topicId,
            title: cluster
              ? `Ask about ${cluster.label}`
              : "Ask about this topic",
          });
          window.htmx?.ajax(
            group !== null ? "POST" : "GET",
            "/chat-modal?" + params,
            {
              values:
                group !== null
                  ? { paper_ids: JSON.stringify(rows.map((p) => p.id)) }
                  : {},
              target: "#modal-container",
              swap: "innerHTML",
            },
          );
        },
        "btn-secondary",
      ),
    );
    if (cluster)
      chat.append(
        el("p", "", "This subtopic conversation lasts for this session."),
      );
    side.append(chat);
    layout.append(explorer, side);
    root.append(layout);
    const status = el(
      "p",
      "topic-map-state",
      `${rows.length} papers. Topic chat ${cluster ? "uses only this subtopic" : "uses the whole collection"}.`,
    );
    status.dataset.mapStatus = "";
    status.setAttribute("role", "status");
    root.append(status);
    placeLabels(plot);
    if (window.ResizeObserver) {
      plotObserver = new ResizeObserver(() => placeLabels(plot));
      plotObserver.observe(plot);
    }
    if (focus?.group)
      Array.from(groups.querySelectorAll("button"))
        .find((b) => b.dataset.mapGroup === focus.group)
        ?.focus({ preventScroll: true });
    if (focus?.paper) {
      Array.from(plot.querySelectorAll("button"))
        .find((b) => b.dataset.mapPaper === focus.paper)
        ?.focus({ preventScroll: true });
      const selected = Array.from(list.children).find(
        (row) => row.dataset.mapRow === focus.paper,
      );
      if (selected)
        list.scrollTop = selected.offsetTop - list.firstElementChild.offsetTop;
    }
  }
  async function load(refresh = false) {
    if (loading || (data && !refresh)) return;
    loading = true;
    root.replaceChildren(el("p", "topic-map-state", "Mapping related papers…"));
    try {
      const params = new URLSearchParams({
        scope: "topic",
        topic_id: root.dataset.topicId,
      });
      const response = await fetch("/api/cluster?" + params, {
        method: "POST",
      });
      let result;
      try {
        result = await response.json();
      } catch (_) {
        throw new Error(
          "The map could not be loaded. Please try again in a moment.",
        );
      }
      if (!response.ok || !result.ok)
        throw new Error(result.error || "The map could not be loaded.");
      if (!Array.isArray(result.papers) || !Array.isArray(result.clusters))
        throw new Error("The map returned an invalid response.");
      data = result;
      group = null;
      paper = null;
      render();
    } catch (error) {
      const message = el("p", "topic-map-state", error.message);
      message.setAttribute("role", "alert");
      root.replaceChildren(
        message,
        button("Try again", () => load(true), "btn-secondary"),
      );
    } finally {
      loading = false;
    }
  }
  document.addEventListener("oh:topic-view", (event) => {
    if (event.detail === "map") load();
  });
  if (
    document
      .querySelector('[data-workspace-tab="map"]')
      ?.getAttribute("aria-selected") === "true"
  )
    load();
})();
