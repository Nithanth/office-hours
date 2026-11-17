const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

test("PDF fallback swaps a fragment and initializes its new PDF container", async () => {
  let observe;
  const requested = [];
  const rendered = [];
  const source = "https://openreview.net/forum?id=paper";
  const element = (dataset = {}) => ({
    dataset, style: {}, clientWidth: 500, nodeType: 1,
    classList: { contains: () => true, remove() {} },
    matches: () => true,
    querySelectorAll: () => [],
    appendChild() {},
    replaceChildren(child) {
      this.child = child;
      if (child.dataset) observe([{ addedNodes: [child] }]);
    },
  });
  const candidate = element({
    pdfSrc: "/pdf-proxy?url=https%3A%2F%2Farxiv.org%2Fpdf%2F2401.12345.pdf",
    originalUrl: source,
  });
  const document = {
    querySelector: () => ({}),
    querySelectorAll: () => [],
    addEventListener() {},
    body: { addEventListener() {} },
    createElement(tag) {
      assert.notEqual(tag, "iframe");
      return tag === "template" ? { content: candidate } : element();
    },
  };
  const context = vm.createContext({
    document, console: { log() {}, warn() {} }, URL, URLSearchParams, Set,
    location: { href: "http://localhost/" },
    window: {
      pdfjsLib: {
        GlobalWorkerOptions: {},
        getDocument({ url }) {
          rendered.push(url);
          return { promise: rendered.length === 1 ? Promise.reject(new Error("403")) : new Promise(() => {}) };
        },
      },
      pdfjsViewer: {
        EventBus: class {},
        PDFLinkService: class { setViewer() {} },
        PDFViewer: class {},
      },
    },
    MutationObserver: class {
      constructor(callback) { observe = callback; }
      observe() {}
    },
    fetch: async (url) => {
      requested.push(url);
      return { ok: true, text: async () => '<div class="pdf-container"></div>' };
    },
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, "../../static/js/pdf-viewer.js"), "utf8"), context);
  context.container = element({ pdfSrc: "/pdf-proxy?url=https%3A%2F%2Fpublisher.test%2Fpaper.pdf", originalUrl: source });
  await vm.runInContext("renderPdf(container)", context);
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(requested.length, 1);
  assert.ok(requested[0].startsWith("/open-copy-pane?"));
  assert.equal(context.container.child, candidate);
  assert.equal(rendered.length, 2);
  assert.equal(rendered[1], candidate.dataset.pdfSrc);
});
