// citation.js – citation hover cards & list highlight helpers

import { dbg } from "./util.js";

const citationCardCache = new Map();
let tooltipDiv = document.getElementById("citation-tooltip");
if (!tooltipDiv) {
  tooltipDiv = document.createElement("div");
  tooltipDiv.id = "citation-tooltip";
  Object.assign(tooltipDiv.style, {
    position: "absolute",
    display: "none",
    zIndex: 9999,
    pointerEvents: "none",
  });
  document.body.appendChild(tooltipDiv);
}

function positionTooltip(ev) {
  const pad = 12;
  tooltipDiv.style.left = `${ev.pageX + pad}px`;
  tooltipDiv.style.top = `${ev.pageY + pad}px`;
}

// Hover card logic
let hoverTimer = null;

document.body.addEventListener("mouseover", (ev) => {
  const cite = ev.target.closest("[data-paper-id]");
  if (!cite) return;
  const paperId = cite.dataset.paperId;
  hoverTimer = setTimeout(async () => {
    let html = citationCardCache.get(paperId);
    if (!html) {
      try {
        const resp = await fetch(`/papers/${paperId}/card`);
        if (!resp.ok) return;
        html = await resp.text();
        citationCardCache.set(paperId, html);
      } catch (e) {
        dbg("fetch card failed", e);
        return;
      }
    }
    tooltipDiv.innerHTML = html;
    tooltipDiv.style.display = "block";
    positionTooltip(ev);
  }, 150);
});

document.body.addEventListener("mousemove", (ev) => {
  if (tooltipDiv.style.display === "block") positionTooltip(ev);
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

// Click to jump & highlight
function pulse(el) {
  el.animate([{ background: "#fffbcc" }, { background: "" }], { duration: 800 });
}

document.body.addEventListener("click", (ev) => {
  const cite = ev.target.closest("[data-paper-id]");
  if (!cite) return;
  const pid = cite.dataset.paperId;
  const listEl = document.getElementById(`paper-${pid}`);
  if (listEl) {
    listEl.scrollIntoView({ behavior: "smooth", block: "center" });
    pulse(listEl);
  }
});

console.debug("[lux] citation.js loaded");
