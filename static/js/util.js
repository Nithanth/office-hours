/* util.js – shared helpers for Lux Scholar frontend (ES module) */

// Debug logger wrapper. Keeps signature identical to console.debug so callers
// can simply replace it. If you want to silence logs in production, toggle the
// DEBUG flag.
export function dbg(...args) {
  // eslint-disable-next-line no-console
  if (window.DEBUG !== false) console.debug("[lux]", ...args);
}

// Simple debounce helper. Returns a wrapped function that delays execution
// until `wait` ms have elapsed since the last call.
export function debounce(fn, wait = 120) {
  let timerId;
  // eslint-disable-next-line func-names
  return function (...args) {
    clearTimeout(timerId);
    // Preserve `this` so the original context is not lost.
    // eslint-disable-next-line consistent-this
    const that = this;
    timerId = setTimeout(() => {
      fn.apply(that, args);
    }, wait);
  };
}

// HTML entity decoder – handy when you need to turn a raw string back into
// display text (used by chat markdown rendering).
export function decodeEntities(htmlStr) {
  const txt = document.createElement("textarea");
  txt.innerHTML = htmlStr;
  return txt.value;
}
