/*
 * htmx-sse-pool.js – overrides htmx.createEventSource so multiple
 * EventSource connections (one per URL) can coexist.  This fixes the
 * issue where a second SSE stream (e.g., chat) closes the existing
 * report stream.
 */
(function () {
  if (!window.htmx) {
    return; // HTMX not yet loaded – script included too early.
  }
  const pool = {};
  window.htmx.createEventSource = function (url) {
    let src = pool[url];
    if (!src || src.readyState === EventSource.CLOSED) {
      src = new EventSource(url, { withCredentials: true });
      pool[url] = src;
    }
    return src;
  };
})();
