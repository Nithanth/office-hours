/* util.js – thin shared helpers for Lux Scholar frontend (ESM).
 * Only exports widely-used utilities; keep lean. */

// Debug logger wrapper. Keeps signature identical to console.debug so callers
// can simply replace it. If you want to silence logs in production, toggle the
// DEBUG flag.
export function dbg(...args) {
  // eslint-disable-next-line no-console
  if (window.DEBUG !== false) console.debug("[lux]", ...args);
}

// HTML entity decoder – handy when turning HTML string back into plain text
// (used by chat markdown rendering).
export function decodeEntities(htmlStr) {
  const txt = document.createElement("textarea");
  txt.innerHTML = htmlStr;
  return txt.value;
}
