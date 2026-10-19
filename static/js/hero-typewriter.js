// Typewriter placeholder — cycles real example queries with a blinking
// block cursor in the hero search field. Self-terminates once
// .empty-state leaves the DOM (first search swaps it out), and does
// nothing at all under prefers-reduced-motion.
(function () {
  var input = document.getElementById("search_query");
  if (!input) return;
  if (window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

  var queries = [
    "implicit neural representations",
    "papers by Yann LeCun",
    "arxiv.org/abs/1706.03762",
    "state space models",
  ];
  var qi = 0;
  var pos = 0;
  var deleting = false;

  function tick() {
    var el = document.getElementById("search_query");
    if (!el || !document.querySelector(".empty-state")) return; // hero gone — stop
    var q = queries[qi];
    pos += deleting ? -1 : 1;
    el.setAttribute("placeholder", "try: " + q.slice(0, pos) + " ▊");
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

  setTimeout(tick, 700);
})();
