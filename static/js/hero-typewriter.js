// Typewriter placeholder — cycles real example queries with a blinking
// block cursor in the hero search field. Three states: empty+unfocused
// cycles the examples, empty+focused holds a static ▊ at the start
// (idle-terminal feel), typing uses the native accent caret. Self-
// terminates once .empty-state leaves the DOM (first search swaps it
// out), and does nothing under prefers-reduced-motion.
(function () {
  var input = document.getElementById("search_query");
  if (!input) return;

  var IDLE = "▊";

  if (window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    // Static idle cursor when focused, nothing when blurred.
    input.addEventListener("focus", function () {
      if (!input.value) input.setAttribute("placeholder", IDLE);
    });
    input.addEventListener("blur", function () {
      if (!input.value) input.setAttribute("placeholder", "");
    });
    return;
  }

  var queries = [
    "implicit neural representations",
    "papers by Yann LeCun",
    "arxiv.org/abs/1706.03762",
    "state space models",
  ];
  var qi = 0;
  var pos = 0;
  var deleting = false;
  var focused = false;

  function tick() {
    var el = document.getElementById("search_query");
    if (!el || !document.querySelector(".empty-state")) return; // hero gone — stop
    if (focused) {
      el.setAttribute("placeholder", IDLE);
      return; // blur handler restarts the cycle
    }
    var q = queries[qi];
    pos += deleting ? -1 : 1;
    el.setAttribute("placeholder", "try: " + q.slice(0, pos) + " " + IDLE);
    var delay = deleting ? 28 : 55;
    if (!deleting && pos >= q.length) {
      deleting = true;
      delay = 1800;
    }
    if (deleting && pos <= 0) {
      deleting = false;
      qi = (qi + 1) % queries.length;
      delay = 500;
    }
    setTimeout(tick, delay);
  }

  input.addEventListener("focus", function () {
    focused = true;
    if (!input.value) input.setAttribute("placeholder", IDLE);
  });
  input.addEventListener("blur", function () {
    focused = false;
    if (!input.value) {
      pos = 0;
      deleting = false;
      setTimeout(tick, 500);
    }
  });
  // Field cleared while focused — restore the idle cursor placeholder.
  input.addEventListener("input", function () {
    if (focused && !input.value) input.setAttribute("placeholder", IDLE);
  });

  setTimeout(tick, 700);
})();
