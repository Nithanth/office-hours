// pagination.js – lightweight client-side pager for papers list
const PAGE_SIZE = 10;
let currentPage = 0;
let totalPages = 1;

function sliceNodes(list) {
  return Array.from(list.children);
}

function renderPage(n) {
  const list = document.getElementById("papers-list");
  if (!list) return;
  const nodes = sliceNodes(list);
  totalPages = Math.ceil(nodes.length / PAGE_SIZE) || 1;
  currentPage = Math.max(0, Math.min(n, totalPages - 1));
  nodes.forEach((li, idx) => {
    li.style.display = idx >= currentPage * PAGE_SIZE && idx < (currentPage + 1) * PAGE_SIZE ? "" : "none";
  });
  window.scrollTo({ top: 0, behavior: 'smooth' });
  updatePager();
}

function buildPager() {
  const pager = document.getElementById("pager");
  if (!pager) return;
  totalPages <= 1 ? (pager.style.display = 'none') : (pager.style.display = 'block');
  pager.innerHTML = ""; // reset
  const btn = (label, page, disabled = false) => {
    const b = document.createElement("button");
    b.className = "pager-btn";
    b.textContent = label;
    if (disabled) {
      b.disabled = true;
    } else {
      b.addEventListener("click", () => renderPage(page));
    }
    pager.appendChild(b);
  };

  btn("‹ Prev", currentPage - 1, currentPage === 0);
  const sep = document.createElement("span");
  sep.textContent = " | ";
  sep.style.margin = "0 4px";
  pager.appendChild(sep);
  btn("Next ›", currentPage + 1, currentPage >= totalPages - 1);
}

function updatePager() {
  // Rebuild to reflect active state/disable
  buildPager();
}

export function initPagination() {
  const list = document.getElementById("papers-list");
  if (!list) return;
  // Recalculate total from visible items
  totalPages = Math.ceil(Array.from(list.children).length / PAGE_SIZE) || 1;
  renderPage(0);
}

// Expose globally so sort/filter in main.js can re-init after reordering
window.initPagination = initPagination;

// Auto-init when list is present on first load
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initPagination, { once: true });
} else {
  initPagination();
}

document.body.addEventListener('htmx:afterSwap', e => {
    if (e.target && e.target.id === 'search-results-container') {
      // list just arrived, build pager
      initPagination();
    }
  });