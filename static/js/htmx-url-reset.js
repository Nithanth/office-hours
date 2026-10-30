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
// Keep the "/" history-cache entry = the pristine page.
// htmx's pushUrlIntoHistory saves the *post-swap* body under the current path
// before pushing — so the first fragment swap overwrites cache["/"] with a
// body that already lost the hero/intro chrome. On restore, Back to "/" then
// shows a bare search card. Snapshot the body at load and pin it back into the
// "/" cache entry right after each history save.
// ---------------------------------------------------------------------------

const pristineBodyHtml = document.body.innerHTML;
const pristineTitle = document.title;

document.body.addEventListener('htmx:beforeHistorySave', (evt) => {
  if (evt.detail.path !== '/' || !pristineBodyHtml) return;
  setTimeout(() => {
    try {
      const cache = JSON.parse(
        localStorage.getItem('htmx-history-cache') || '[]'
      );
      let changed = false;
      for (const item of cache) {
        if (item.url === '/') {
          item.content = pristineBodyHtml;
          item.title = pristineTitle;
          changed = true;
        }
      }
      if (changed) {
        localStorage.setItem('htmx-history-cache', JSON.stringify(cache));
      }
    } catch (_) {
      // best-effort — a stale "/" entry just restores a barer page
    }
  }, 0);
});
