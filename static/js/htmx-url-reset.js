// ---------------------------------------------------------------------------
// Keep history-cache snapshots = the fully-settled view.
// htmx saves the body at push-time, before the swap settles — the entry can
// capture an emptied results container (Back then restores a bare card).
// Record each path's settled body after every swap; whenever htmx writes a
// history entry, overwrite it with the recorded settled version.
// ---------------------------------------------------------------------------

const settledBodies = new Map();
settledBodies.set(window.location.pathname + window.location.search, {
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
  // Error-only swaps in the results container replace
  // the visible view but carry no URL of their own — don't bake them into
  // the snapshot, or Back would restore the error over the prior results.
  const container = document.getElementById('search-results-container');
  const isErrorOnly =
    container &&
    container.children.length === 1 &&
    container.firstElementChild &&
    container.firstElementChild.matches('.oh-toast, .error-message, .alert-error');
  if (isErrorOnly) return;
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
