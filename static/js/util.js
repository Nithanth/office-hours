/* util.js – thin shared helpers for Office Hours frontend (ESM).
 * Only exports widely-used utilities; keep lean.
 *
 * Guidelines:
 *   • Zero dependencies.
 *   • No DOM reads in hot paths – prefer cached params.
 *   • Keep bundle footprint tiny (<1kB).
 */

// Debug logger wrapper. Keeps signature identical to console.debug so callers
// can simply replace it. If you want to silence logs in production, toggle the
// DEBUG flag.
export function dbg(...args) {
  // eslint-disable-next-line no-console
  if (window.DEBUG !== false) console.debug("[oh]", ...args);
}

// HTML entity decoder – handy when turning HTML string back into plain text
// (used by chat markdown rendering).
export function decodeEntities(htmlStr) {
  const txt = document.createElement("textarea");
  txt.innerHTML = htmlStr;
  return txt.value;
}

// ---------------------------------------------------------------------------
// rAF throttle – ensure a callback runs at most once per animation frame.
// Returns a wrapped function with .cancel() to drop a pending invocation.
// ---------------------------------------------------------------------------
export function rafThrottle(fn) {
  let rafId = null;
  let lastArgs = null;
  const wrapper = (...args) => {
    lastArgs = args;
    if (rafId) return;
    rafId = requestAnimationFrame(() => {
      rafId = null;
      // eslint-disable-next-line prefer-spread
      fn.apply(null, lastArgs);
    });
  };
  wrapper.cancel = () => {
    if (rafId) cancelAnimationFrame(rafId);
    rafId = null;
  };
  return wrapper;
}

// Make util functions accessible from console for quick inspection.
window.ohUtil = { dbg, decodeEntities, rafThrottle };
