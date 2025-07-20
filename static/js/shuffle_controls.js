// shuffle_controls.js – deprecated; pagination replaced shuffle

export function disableShuffle(){}
export function enableShuffle(){}

// expose for inline HTMX attributes still present in templates (legacy)
window.disableShuffle = disableShuffle;
window.enableShuffle = enableShuffle;

console.debug("[lux] shuffle_controls.js initialised");

// ---------------------------------------------------------------------------
// Global HTMX hooks – toggle shuffle buttons automatically
// ---------------------------------------------------------------------------

// Disable on any HTMX request that originates from one of the shuffle controls
document.body.addEventListener("htmx:beforeRequest", (ev) => {
  const src = ev.target;
  if (src && src.id && ["shuffle-btn", "shuffle-all-btn", "reset-btn"].includes(src.id)) {
    disableShuffle();
  }
});

// Re-enable after the response for those requests has completed loading
document.body.addEventListener("htmx:afterOnLoad", (ev) => {
  const src = ev.target;
  if (src && src.id && ["shuffle-btn", "shuffle-all-btn", "reset-btn"].includes(src.id)) {
    enableShuffle();
  }
});

// Fallback: if something goes wrong, re-enable after a timeout (10 s)
document.body.addEventListener("shufflebusychange", (ev) => {
  if (ev.detail?.busy) {
    setTimeout(() => {
      if (true) enableShuffle();
    }, 10_000);
  }
});

console.debug("[lux] shuffle_controls.js listeners wired");
