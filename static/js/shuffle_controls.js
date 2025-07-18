// shuffle_controls.js – enable/disable shuffle/reset buttons for HTMX generation events
// Keeps legacy inline attributes working by exposing functions globally

export const SHUFFLE_BTN_IDS = ["shuffle-btn", "shuffle-all-btn", "reset-btn"];
let _busy = false;

export function disableShuffle() {
  _busy = true;
  document.dispatchEvent(new CustomEvent("shufflebusychange", { detail: { busy: true } }));
  SHUFFLE_BTN_IDS.forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.disabled = true;
  });
}

export function enableShuffle() {
  _busy = false;
  document.dispatchEvent(new CustomEvent("shufflebusychange", { detail: { busy: false } }));
  SHUFFLE_BTN_IDS.forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.disabled = false;
  });
}

export function isShuffleBusy() {
  return _busy;
}

// expose for inline HTMX attributes still present in templates (legacy)
window.disableShuffle = disableShuffle;
window.enableShuffle = enableShuffle;
window.isShuffleBusy = isShuffleBusy;

// For module consumers
export default { disableShuffle, enableShuffle, isShuffleBusy };

console.debug("[lux] shuffle_controls.js initialised");

// ---------------------------------------------------------------------------
// Global HTMX hooks – toggle shuffle buttons automatically
// ---------------------------------------------------------------------------

// Disable on any HTMX request that originates from one of the shuffle controls
document.body.addEventListener("htmx:beforeRequest", (ev) => {
  const src = ev.target;
  if (src && src.id && SHUFFLE_BTN_IDS.includes(src.id)) {
    disableShuffle();
  }
});

// Re-enable after the response for those requests has completed loading
document.body.addEventListener("htmx:afterOnLoad", (ev) => {
  const src = ev.target;
  if (src && src.id && SHUFFLE_BTN_IDS.includes(src.id)) {
    enableShuffle();
  }
});

// Fallback: if something goes wrong, re-enable after a timeout (10 s)
document.body.addEventListener("shufflebusychange", (ev) => {
  if (ev.detail?.busy) {
    setTimeout(() => {
      if (isShuffleBusy()) enableShuffle();
    }, 10_000);
  }
});

console.debug("[lux] shuffle_controls.js listeners wired");
