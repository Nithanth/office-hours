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

test("paper tone resets keep the PDF, page and zoom without changing app theme", async () => {
  const listeners = new Map();
  const element = (dataset={}) => ({
    dataset,style:{},clientWidth:700,isConnected:true,offsetWidth:80,
    appendChild(){},addEventListener(){},remove(){},
    getBoundingClientRect:()=>({right:900,top:150}),
  });
  let ready;
  const root = {
    dataset:{readerOriginal:'false'},
    addEventListener:(name,handler)=>listeners.set(name,handler),
    dispatchEvent:event=>{if(event.type==='oh:pdf-ready')ready=event.detail;},
  };
  const container=element({pdfSrc:'/sample.pdf'});container.closest=()=>root;
  const pdfDoc={numPages:7};
  let options;
  class EventBus {
    handlers={};
    on(name,fn){this.handlers[name]=fn;}
    emit(name){this.handlers[name]?.();}
  }
  class PDFViewer {
    currentScale=1;currentScaleValue='auto';currentPageNumber=1;
    constructor(value){options=value;this.eventBus=value.eventBus;this.pageColors=value.pageColors;}
    setDocument(doc){
      // pdf.js 3.11's embedded annotation editor has no AltTextManager to
      // destroy. The read-only mode must avoid that teardown on tone resets.
      if(this.pdfDocument && options.annotationEditorMode!==-1)throw new Error('Missing AltTextManager');
      this.pdfDocument=doc;
      if(doc)queueMicrotask(()=>this.eventBus.emit('pagesinit'));
    }
  }
  const appTheme={dark:true};
  const context=vm.createContext({
    document:{
      querySelector:()=>({}),querySelectorAll:()=>[],addEventListener(){},
      documentElement:{classList:{contains:()=>appTheme.dark}},
      createElement:()=>element(),body:{addEventListener(){},appendChild(){}},
    },
    window:{
      addEventListener(){},
      pdfjsLib:{GlobalWorkerOptions:{},AnnotationEditorType:{DISABLE:-1},getDocument:()=>({promise:Promise.resolve(pdfDoc)})},
      pdfjsViewer:{EventBus,PDFViewer,PDFLinkService:class{setViewer(){}setDocument(){}}},
    },
    MutationObserver:class{observe(){}},
    CustomEvent:class{constructor(type,options){this.type=type;this.detail=options.detail;}},
    console:{log(){},warn(){}},URL,URLSearchParams,Set,queueMicrotask,
    location:{href:'http://localhost/'},container,
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../../static/js/pdf-viewer.js'),'utf8'),context);
  await vm.runInContext('renderPdf(container)',context);
  assert.equal(ready.viewer.pageColors.background,'#1f1f23');
  ready.viewer.currentPageNumber=4;ready.viewer.currentScaleValue=1.4;
  listeners.get('oh:paper-tone')({detail:{dark:false}});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(ready.viewer.pdfDocument,pdfDoc);
  assert.equal(ready.viewer.pageColors,null);
  assert.equal(ready.viewer.currentPageNumber,4);
  assert.equal(ready.viewer.currentScaleValue,1.4);
  assert.equal(appTheme.dark,true);
  listeners.get('oh:paper-tone')({detail:{dark:true}});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(ready.viewer.pageColors.foreground,'#e5e5e1');
  assert.equal(ready.viewer.currentPageNumber,4);
});
