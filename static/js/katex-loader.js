// Use the module build so auto-render imports its matching KaTeX core.
const baseURL = "https://cdn.jsdelivr.net/npm/katex@0.16.9/dist";
let loading;

export function ensureKatexLoaded() {
  if (!loading) {
    const stylesheet = document.createElement("link");
    stylesheet.rel = "stylesheet";
    stylesheet.href = baseURL + "/katex.min.css";
    document.head.appendChild(stylesheet);

    loading = import(baseURL + "/contrib/auto-render.mjs")
      .then(({ default: renderMathInElement }) => {
        window.renderMathInElement = renderMathInElement;
      })
      .catch(error => {
        console.warn("Could not load the math renderer.", error);
      });
  }
  return loading;
}
