// Ensure the browser never stays on a URL containing ?search_query=…
// 1. On first page load: if the param is present, immediately redirect to '/'
//    so the server renders a clean empty page.
// 2. After any HTMX swap: if param sneaks back in, replaceState it away.

const stripSearchParam = () => {
  if (window.location.search.includes('search_query=')) {
    // Replace the current URL in history without reloading the page
    history.replaceState(null, '', '/');
  }
};

// Run once on load
stripSearchParam();

// Also run after every HTMX swap (defensive)
document.body.addEventListener('htmx:afterSwap', () => {
  if (window.location.search.includes('search_query=')) {
    history.replaceState(null, '', '/');
  }
});

// ---------------------------------------------------------------------------
// Keep history-cache snapshots = the fully-settled view.
// htmx saves the body at push-time, before the swap settles — the entry can
// capture an emptied results container (Back then restores a bare card).
// Record each path's settled body after every swap; whenever htmx writes a
// history entry, overwrite it with the recorded settled version.
// ---------------------------------------------------------------------------

const settledBodies = new Map();
settledBodies.set('/', {
  content: document.body.innerHTML,
  title: document.title,
});

const writeCacheEntry = (path, content, title) => {
  try {
    const cache = JSON.parse(
      localStorage.getItem('htmx-history-cache') || '[]'
    );
    let changed = false;
    for (const item of cache) {
      if (item.url === path) {
        item.content = content;
        item.title = title;
        changed = true;
      }
    }
    if (changed) {
      localStorage.setItem('htmx-history-cache', JSON.stringify(cache));
    }
  } catch (_) {
    // best-effort — a stale entry just restores a barer page
  }
};

document.body.addEventListener('htmx:afterSettle', () => {
  const path = window.location.pathname + window.location.search;
  settledBodies.set(path, {
    content: document.body.innerHTML,
    title: document.title,
  });
  writeCacheEntry(path, document.body.innerHTML, document.title);
});

document.body.addEventListener('htmx:beforeHistorySave', (evt) => {
  const settled = settledBodies.get(evt.detail.path);
  if (!settled) return;
  setTimeout(() => writeCacheEntry(evt.detail.path, settled.content, settled.title), 0);
});
