/* Isolated visual prototype. All research content below is a local fixture. */
const paths = {
  home: '<path d="m3 10 9-7 9 7v10a1 1 0 0 1-1 1h-5v-7H9v7H4a1 1 0 0 1-1-1z"/>',
  search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 4.5 4.5"/>',
  book: '<path d="M12 5c-3-2-6-2-10-1v15c4-1 7-1 10 1 3-2 6-2 10-1V4c-4-1-7-1-10 1Zm0 0v15"/>',
  folder: '<path d="M3 7V4h6l2 3h10v13H3z"/>',
  file: '<path d="M14 2H5v20h14V7zM14 2v5h5M8 12h8m-8 4h6"/>',
  arrow: '<path d="M4 12h16m-6-6 6 6-6 6"/>',
  diagonal: '<path d="M5 19 19 5M5 5h14v14"/>',
  plus: '<path d="M12 4v16M4 12h16"/>',
  chevron: '<path d="m9 5 7 7-7 7"/>',
  down: '<path d="m6 9 6 6 6-6"/>',
  settings: '<path d="M4 6h16M4 12h16M4 18h16"/><circle cx="8" cy="6" r="2"/><circle cx="16" cy="12" r="2"/><circle cx="10" cy="18" r="2"/>',
  moon: '<path d="M20.5 14A9 9 0 0 1 10 3.5 9 9 0 1 0 20.5 14Z"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 1v2m0 18v2M1 12h2m18 0h2M4 4l1.5 1.5m13 13L20 20M4 20l1.5-1.5m13-13L20 4"/>',
  sparkle: '<path d="m12 2 2.5 7.5L22 12l-7.5 2.5L12 22l-2.5-7.5L2 12l7.5-2.5z"/>',
  chat: '<path d="M21 11.5a8.5 8.5 0 0 1-8.5 8.5H3l2-5a8.5 8.5 0 1 1 16-3.5Z"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  bookmark: '<path d="M5 3h14v19l-7-5-7 5z"/>',
  check: '<path d="m5 12 4 4L19 6"/>',
  more: '<circle cx="4" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="20" cy="12" r="1"/>',
  link: '<path d="m10 13 4-4m-6 6-2 2a4 4 0 0 1-6-6l5-5a4 4 0 0 1 6 0m2 3 2-2a4 4 0 0 1 6 6l-5 5a4 4 0 0 1-6 0" transform="translate(2 0)"/>',
  expand: '<path d="M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5"/>',
  refresh: '<path d="M20 8a8 8 0 1 0 0 8m0-14v6h-6"/>',
  notes: '<path d="M4 3h16v18H4zM8 8h8m-8 4h8m-8 4h4"/>',
};
const icon = (name, cls = '') => `<svg class="icon ${cls}" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.55" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name] || paths.file}</svg>`;
const page = document.body.dataset.page;
const currentTheme = new URLSearchParams(location.search).get('theme') || localStorage.getItem('reading-room-theme') || 'light';
document.documentElement.dataset.theme = currentTheme === 'dark' ? 'dark' : 'light';
const brand = `<span class="brand-mark">o<span>h</span><i></i></span><span class="brand-name">office hours<span>A little room for research.</span></span>`;

function sidebar() {
  return `<aside class="sidebar ${page === 'reader' ? 'compact' : ''}">
    <a class="brand" href="index.html" aria-label="Office Hours home">${brand}</a>
    <div class="workspace"><span class="workspace-monogram">N</span><span>Personal workspace<small>Make yourself at home</small></span>${icon('down')}</div>
    <nav class="main-nav" aria-label="Main navigation">
      <a href="index.html" class="${page === 'home' ? 'active' : ''}" title="Home">${icon('home')}<span>Home</span></a>
      <a href="index.html#search" title="Discover">${icon('search')}<span>Discover</span><kbd>⌘ K</kbd></a>
      <a href="topic.html" class="${page === 'topic' ? 'active' : ''}" title="My topics">${icon('folder')}<span>My topics</span><small>4</small></a>
      <a href="reader.html" title="Reading list">${icon('book')}<span>Reading list</span><small>12</small></a>
    </nav>
    <div class="sidebar-topics"><div class="nav-label">YOUR TOPICS <button class="icon-button" aria-label="Create topic" data-demo="New topics begin with a question. Try the search on Home.">${icon('plus')}</button></div>
      <a class="${page === 'topic' ? 'selected' : ''}" href="topic.html"><i class="topic-dot rust"></i>Retrieval-augmented…</a>
      <a href="topic.html"><i class="topic-dot sage"></i>Mechanistic interpretability</a>
      <a href="topic.html"><i class="topic-dot ochre"></i>Learning to reason</a>
      <a href="topic.html"><i class="topic-dot blue"></i>Human–AI collaboration</a>
    </div>
    <div class="sidebar-bottom"><div class="quiet-note">Good research begins<br>with a better question.<span>Stay curious.</span></div>
      <button class="theme-toggle" aria-label="Toggle color theme">${icon('moon')}<span>Evening reading</span><span class="toggle-track"><i></i></span></button>
      <div class="profile"><span class="avatar">NR</span><span>Nithanth<small>Personal account</small></span><button class="icon-button" aria-label="Account settings" data-demo="This is a visual prototype. Your account and settings are unchanged.">${icon('settings')}</button></div>
    </div>
  </aside>`;
}

const topbar = (crumb, right = '') => `<header class="topbar"><div class="breadcrumb"><a href="index.html">Your workspace</a><span>/</span>${crumb}</div><div class="topbar-right">${right}<span class="prototype-label">THE READING ROOM <i></i> LOOK A</span></div></header>`;

function topicArt(type) {
  if (type === 'orbit') return `<svg viewBox="0 0 140 100" fill="none" aria-hidden="true"><ellipse cx="70" cy="50" rx="51" ry="21" transform="rotate(-28 70 50)"/><ellipse cx="70" cy="50" rx="51" ry="21" transform="rotate(28 70 50)"/><ellipse cx="70" cy="50" rx="21" ry="43"/><circle cx="70" cy="50" r="5" class="fill"/><circle cx="111" cy="21" r="3" class="fill"/></svg>`;
  if (type === 'network') return `<svg viewBox="0 0 140 100" fill="none" aria-hidden="true"><path d="m25 27 43 20 43-28M25 27l4 52 39-32 43 29 3-57M29 79l82-3M68 47l4-36m-4 36 3 44"/><circle cx="25" cy="27" r="5"/><circle cx="29" cy="79" r="5"/><circle cx="68" cy="47" r="8"/><circle cx="111" cy="19" r="5"/><circle cx="111" cy="76" r="5"/><circle cx="72" cy="11" r="3"/><circle cx="71" cy="91" r="3"/></svg>`;
  return `<svg viewBox="0 0 140 100" fill="none" aria-hidden="true"><path d="M24 80h92M33 78V59h16v19M62 78V40h16v38M91 78V21h16v57M25 45l36-23 25 5 27-18m-11 0h11v11"/></svg>`;
}

function home() {
  return `${topbar('<span>Home</span>', '<span class="date-label">WEDNESDAY, NOVEMBER 18</span>')}
  <main class="home-content">
    <section class="home-hero"><div class="hero-copy"><div class="eyebrow"><span class="tiny-star">✳</span> A SPACE FOR YOUR NEXT IDEA</div><h1>Follow your curiosity.<br><em>Find your next thought.</em></h1><p>Explore the literature, connect the dots, and make room<br class="desktop-break"> for the ideas that stay with you.</p></div>
      <div class="hero-illustration" aria-hidden="true"><span class="illustration-label">THE ART OF PAYING ATTENTION</span><svg viewBox="0 0 300 250" fill="none"><circle cx="154" cy="106" r="71" stroke-dasharray="2 5"/><path d="M67 179c24-14 52-13 80 3V89c-28-16-56-17-80-3v93Zm80 3c29-16 57-17 85-3V86c-28-14-56-13-85 3v93Z"/><path d="M75 187c23-11 47-9 72 3 28-13 57-14 77-3M147 182v8M83 102c14-5 29-3 46 3m-46 8c14-5 29-3 46 3m-46 8c14-5 29-3 46 3m-46 8c14-5 29-3 46 3m-46 8c14-5 29-3 35 0m48-41c16-6 31-8 47-3m-47 14c16-6 31-8 47-3m-47 14c16-6 31-8 47-3m-47 14c16-6 31-8 47-3m-47 14c16-6 31-8 36-6"/><path d="m204 48 5-14 5 14 14 5-14 5-5 14-5-14-14-5z" class="ink-fill"/><path d="M52 55v15m-7-7h15M251 132v11m-5-5h11"/><path d="M44 205h216"/><ellipse cx="150" cy="205" rx="60" ry="3" class="wash-fill"/></svg><span class="illustration-caption">Less searching. More understanding.</span></div></section>
    <section id="search" class="search-section"><form id="research-search"><label for="question">What would you like to understand?</label><div class="search-input-row">${icon('search')}<input id="question" name="q" placeholder="Ask a question, explore a topic, or paste a paper URL…" required autocomplete="off"><button class="primary-button" type="submit">Explore ${icon('arrow')}</button></div><div class="search-meta"><span>${icon('sparkle')} A conversation with the literature, not just a list of links.</span><span>Researcher ${icon('down')}</span></div></form>
      <div class="suggestions"><span>A place to start</span><button data-query="How do language models learn to reason?">How do language models reason? ${icon('diagonal')}</button><button data-query="What makes retrieval-augmented generation work?">What makes RAG work? ${icon('diagonal')}</button><button data-query="Mechanistic interpretability">Inside the black box ${icon('diagonal')}</button></div>
    </section>
    <section class="topics-section"><div class="section-heading"><div><span class="eyebrow">YOUR ONGOING EXPLORATIONS</span><h2>Pick up the thread</h2></div><a class="text-link" href="topic.html">All topics ${icon('arrow')}</a></div>
      <div class="topic-cards">
        <a class="topic-card rust-card" href="topic.html"><div class="topic-card-top"><span class="pill"><i></i> 3 new papers</span>${icon('diagonal')}</div>${topicArt('orbit')}<div><h3>Retrieval-augmented<br>generation</h3><p>Grounding language in knowledge.</p><footer><span>24 papers <b>·</b> 2 reports</span><span>Updated today</span></footer></div></a>
        <a class="topic-card sage-card" href="topic.html"><div class="topic-card-top"><span class="topic-category">MACHINE LEARNING</span>${icon('diagonal')}</div>${topicArt('network')}<div><h3>Mechanistic<br>interpretability</h3><p>Understanding what happens inside.</p><footer><span>18 papers <b>·</b> 1 report</span><span>2 days ago</span></footer></div></a>
        <a class="topic-card ochre-card" href="topic.html"><div class="topic-card-top"><span class="topic-category">REASONING & COGNITION</span>${icon('diagonal')}</div>${topicArt('steps')}<div><h3>Learning<br>to reason</h3><p>From patterns to possibilities.</p><footer><span>12 papers <b>·</b> 1 report</span><span>4 days ago</span></footer></div></a>
      </div>
    </section>
    <section class="home-lower"><div class="reading-list"><div class="section-heading"><h2>Still on your mind</h2><a class="text-link" href="reader.html">Reading list ${icon('arrow')}</a></div><a class="reading-row" href="reader.html"><span class="paper-number">01</span><div><h3>Retrieval-Augmented Generation for<br>Knowledge-Intensive NLP Tasks</h3><p>Lewis et al. <b>·</b> NeurIPS 2020 <b>·</b> 14 min read</p></div><span class="reading-status">Continue reading ${icon('arrow')}</span></a><a class="reading-row" href="reader.html"><span class="paper-number">02</span><div><h3>Lost in the Middle: How Language Models<br>Use Long Contexts</h3><p>Liu et al. <b>·</b> TACL 2024 <b>·</b> 12 min read</p></div>${icon('bookmark')}</a></div><aside class="library-note"><span class="eyebrow">FROM YOUR MARGINS</span><span class="quote-mark">“</span><blockquote>Retrieval isn’t just about finding more information. It’s about finding the right context.</blockquote><a href="topic.html">Your note on RAG ${icon('arrow')}</a></aside></section>
    <footer class="page-footer"><span>Office Hours <i>— a quieter kind of research.</i></span><span>Sample workspace · Visual concept A</span></footer>
  </main>`;
}

function topic() {
  return `${topbar('<a href="topic.html">My topics</a><span>/</span><span>Retrieval-augmented generation</span>')}
    <main class="topic-content"><div class="topic-heading"><div><div class="eyebrow"><i class="topic-dot rust"></i> YOUR RESEARCH TOPIC <span class="topic-kind">Living collection</span></div><h1>Retrieval-augmented<br><em>generation</em></h1><p>How we give language models a world beyond their weights.</p><div class="topic-detail"><span>24 papers</span><b>·</b><span>2 report versions</span><b>·</b><span>Updated today</span><span class="tracking-indicator"><i></i> Keeping an eye on the literature</span></div></div><div class="topic-actions"><button class="secondary-button" data-demo="A private sharing link would be created here. Nothing has been published.">${icon('link')} Share topic</button><button class="icon-button" aria-label="More topic actions" data-demo="This collection is sample content for the design concept.">${icon('more')}</button></div></div>
    <nav class="topic-tabs" aria-label="Topic views"><button class="active" data-tab="overview">Overview</button><button data-tab="papers">Papers <span>24</span></button><button data-tab="notes">Your notes <span>6</span></button><button data-tab="history">Report history <span>2</span></button><div></div><button class="update-report" data-demo="Report generation is not connected in this visual prototype.">${icon('refresh')} Update report</button></nav>
    <div id="overview-panel" class="topic-grid"><article class="research-report"><div class="report-kicker"><span>${icon('sparkle')} THE RESEARCH BRIEF</span><span>VERSION 02 <b>·</b> NOV 18, 2026</span></div><h2>The landscape, in brief.</h2><p class="report-deck">A more grounded way to generate.</p><p>Language models know a great deal. But their knowledge is frozen in time, hard to trace, and sometimes confidently wrong. Retrieval-augmented generation (RAG) offers a simple shift: <strong>let the model look things up before it speaks.</strong> <a class="citation" href="reader.html">1</a></p><p>By pairing a retriever with a generator, RAG brings external evidence into the conversation. The original architecture showed that this combination could outperform much larger, purely parametric models on knowledge-intensive tasks. <a class="citation" href="reader.html">1</a> <a class="citation" href="reader.html">2</a></p><div class="report-divider"><span>THREE IDEAS TO TAKE WITH YOU</span></div><div class="takeaway"><span>01</span><div><h3>Better retrieval beats more context.</h3><p>What you retrieve matters more than how much you retrieve. Irrelevant passages can dilute the signal, especially in long contexts. <a class="citation" href="reader.html">3</a></p></div></div><div class="takeaway"><span>02</span><div><h3>Retrieval is becoming a reasoning step.</h3><p>Newer systems decide when to search, evaluate what they find, and search again. The pipeline is giving way to a loop. <a class="citation" href="reader.html">4</a></p></div></div><div class="takeaway"><span>03</span><div><h3>Grounded doesn’t always mean faithful.</h3><p>A source in the context is not a guarantee that an answer follows it. Attribution and evaluation remain open questions. <a class="citation" href="reader.html">5</a></p></div></div><a href="reader.html" class="report-source-link">Explore the foundational paper ${icon('arrow')}</a><div class="report-footnote">A sample synthesis for this visual concept. Always follow the sources.</div></article>
      <aside class="topic-aside"><div class="assistant-invite"><span class="assistant-orb">${icon('sparkle')}</span><h3>Think it through.</h3><p>Ask a question across the papers<br>in this collection.</p><a class="primary-button" href="reader.html">Ask your research assistant ${icon('arrow')}</a><span class="small-note">Grounded in your 24 sources</span></div>
      <section class="topic-side-section"><div class="aside-heading"><h3>Fresh in the collection</h3><span class="pill">3 new</span></div><a class="new-paper" href="reader.html"><span>01</span><div><h4>Self-RAG: Learning to Retrieve, Generate, and Critique</h4><p>Asai et al. <b>·</b> ICLR 2024</p></div>${icon('diagonal')}</a><a class="new-paper" href="reader.html"><span>02</span><div><h4>Corrective Retrieval Augmented Generation</h4><p>Yan et al. <b>·</b> 2024</p></div>${icon('diagonal')}</a><a class="text-link" href="#papers" data-open-papers>See all papers ${icon('arrow')}</a></section>
      <section class="topic-side-section tracked"><div class="aside-heading"><h3>Following the thread</h3>${icon('settings')}</div><p>We watch for new work, so you don’t have to.</p><div class="tracked-query">${icon('search')} retrieval augmented generation</div><div class="tracked-query">${icon('search')} grounded language models</div><button class="text-link" data-demo="Tracked searches are sample fixtures in this prototype.">${icon('plus')} Add a tracked search</button><div class="last-checked"><i></i> Last checked 2 hours ago</div></section>
      <section class="margin-note"><span class="eyebrow">A QUESTION WORTH ASKING</span><p>When should a model trust<br>what it knows — and when<br>should it look again?</p><a class="text-link" href="reader.html">Explore with your assistant ${icon('arrow')}</a></section></aside>
    </div><div id="alternate-panel" hidden></div><footer class="page-footer"><span>Your ideas, connected.</span><a href="index.html">Back to your workspace ${icon('arrow')}</a></footer></main>`;
}

function paperDocument() {
  return `<div class="paper-document"><div class="paper-publication">ADVANCES IN NEURAL INFORMATION PROCESSING SYSTEMS 33 · 2020</div><h2>Retrieval-Augmented Generation<br>for Knowledge-Intensive NLP Tasks</h2><div class="paper-authors">Patrick Lewis, Ethan Perez, Aleksandra Piktus, Fabio Petroni,<br>Vladimir Karpukhin, Naman Goyal, Heinrich Küttler, Mike Lewis,<br>Wen-tau Yih, Tim Rocktäschel, Sebastian Riedel, Douwe Kiela</div><div class="paper-institution">Facebook AI Research · University College London · New York University</div><div class="paper-abstract"><h3>Abstract</h3><p>Large pre-trained language models have been shown to store factual knowledge in their parameters, and achieve state-of-the-art results when fine-tuned on downstream NLP tasks. However, their ability to access and precisely manipulate knowledge is still limited, and hence on knowledge-intensive tasks, their performance lags behind task-specific architectures.</p><p><mark>We explore a general-purpose fine-tuning recipe for retrieval-augmented generation (RAG) — models which combine pre-trained parametric and non-parametric memory for language generation.</mark> We introduce RAG models where the parametric memory is a pre-trained seq2seq model and the non-parametric memory is a dense vector index of Wikipedia, accessed with a pre-trained neural retriever.</p></div><div class="paper-body-columns"><section><h3>1 &nbsp; Introduction</h3><p>Pre-trained neural language models have been shown to learn a substantial amount of in-depth knowledge from data. They can do so without any access to an external memory, as a parameterized implicit knowledge base.</p><p>This development is exciting, but such models do have downsides: they cannot easily expand or revise their memory, cannot straightforwardly provide insight into their predictions, and may produce hallucinations.</p><p>Hybrid models that combine parametric memory with non-parametric memory can address these issues because knowledge can be directly revised and expanded.</p></section><section><h3>2 &nbsp; Methods</h3><p>We explore RAG models, which use the input sequence x to retrieve text documents z and use them as additional context when generating the target sequence y.</p><div class="rag-diagram"><span>Question</span><i>↓</i><span class="retriever">Retriever</span><i>↓</i><div><span>Document</span><span>Document</span></div><i>↓</i><span class="generator">Generator</span><i>↓</i><b>Grounded answer</b></div><div class="diagram-caption">Fig. 1. A simplified view of retrieval-augmented generation.</div></section></div><div class="paper-page-number">1</div></div>`;
}

function reader() {
  return `${topbar('<a href="topic.html">Retrieval-augmented generation</a><span>/</span><span>Paper reader</span>', '<button class="text-link" data-demo="This paper is already saved in your sample reading list.">'+icon('check')+' Saved to reading list</button>')}
    <div class="reader-titlebar"><div><a href="topic.html" class="reader-back" aria-label="Back to topic">${icon('arrow')}</a><div><h1>Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks</h1><p>Lewis et al. <b>·</b> NeurIPS 2020 <b>·</b> In your RAG collection</p></div></div><button class="secondary-button" data-demo="External links are disabled in this self-contained visual prototype.">Original paper ${icon('diagonal')}</button></div>
    <div class="reader-layout"><aside class="document-outline"><div class="outline-label">IN THIS PAPER</div><nav aria-label="Paper outline"><a class="active" href="#abstract">Abstract</a><a href="#introduction">1 &nbsp; Introduction</a><a href="#methods">2 &nbsp; Methods</a><a href="#results">3 &nbsp; Experiments</a><a href="#related">4 &nbsp; Related work</a><a href="#conclusion">5 &nbsp; Conclusion</a><a href="#references">References</a></nav><div class="outline-bottom">${icon('bookmark')}<span>Make it your own.<small>Select any passage to<br>save a note or ask a question.</small></span></div></aside>
    <section class="document-space" aria-label="Paper document"><div class="document-toolbar"><span>PDF DOCUMENT</span><div><button class="icon-button" aria-label="Previous page" data-demo="The prototype shows the first page only.">${icon('chevron','rotate')}</button><span class="page-count">1 <i>/ 12</i></span><button class="icon-button" aria-label="Next page" data-demo="The prototype shows the first page only.">${icon('chevron')}</button><span class="toolbar-divider"></span><button class="text-link" data-demo="The paper is fitted to the reading pane.">Fit width ${icon('down')}</button><button class="icon-button" aria-label="Expand paper" id="focus-paper">${icon('expand')}</button></div></div>${paperDocument()}<div class="document-prototype-note">Typeset preview · Sample paper excerpt, not a live PDF viewer</div></section>
    <aside class="reader-assistant"><div class="assistant-header"><div><span class="assistant-orb">${icon('sparkle')}</span><span>Your reading companion<small>A conversation with this paper</small></span></div><button class="icon-button" aria-label="More assistant options" data-demo="This reading companion uses a prewritten sample conversation.">${icon('more')}</button></div><div class="assistant-tabs"><button class="active" data-assistant-tab="conversation">Conversation</button><button data-assistant-tab="notes">Your notes <span>2</span></button></div><div class="conversation" id="conversation"><div class="conversation-date"><span>TAKE A CLOSER LOOK</span></div><div class="user-message">What’s the core idea of this paper?</div><div class="assistant-message"><div class="assistant-message-label">${icon('sparkle')} OFFICE HOURS</div><p>Think of it as giving a language model an <strong>open-book exam.</strong></p><p>Instead of relying only on what it learned during training, the model first retrieves relevant passages from an external collection. Then it uses those passages to write its answer.</p><div class="answer-highlight"><span>THE KEY DISTINCTION</span><p><strong>Parametric memory</strong> is what the model knows.<br><strong>Non-parametric memory</strong> is what it can look up.</p></div><p>The contribution is bringing those two kinds of memory together — and training them to work as one. <a class="citation" href="#abstract">p. 1</a></p><button class="source-quote" id="source-quote"><span>${icon('file')} FROM THE PAPER <span>PAGE 1 ${icon('diagonal')}</span></span><q>models which combine pre-trained parametric and non-parametric memory</q></button><div class="message-actions"><button class="text-link save-answer">${icon('bookmark')} Save as note</button><span>Grounded in this paper ${icon('check')}</span></div></div><div class="follow-up"><span>KEEP EXPLORING</span><button data-followup="How does the retriever learn?">How does the retriever learn? ${icon('diagonal')}</button><button data-followup="What are the limitations?">What are the limitations? ${icon('diagonal')}</button></div></div><div class="assistant-composer"><div class="selected-passage">${icon('file')} Asking about this paper <span>12 pages</span></div><form id="chat-form"><label class="sr-only" for="chat-question">Ask about this paper</label><textarea id="chat-question" rows="2" placeholder="A question, a connection, a what if…" required></textarea><div><span>${icon('plus')} Add context</span><button type="submit" class="send-button" aria-label="Send question">${icon('arrow')}</button></div></form><p>Read critically. Follow the sources.</p></div></aside></div>`;
}

document.getElementById('app').innerHTML = `${sidebar()}<div class="app-main ${page === 'reader' ? 'reader-main' : ''}">${page === 'topic' ? topic() : page === 'reader' ? reader() : home()}</div><div class="toast" role="status" hidden></div>`;

let toastTimer;
function toast(message) {
  const el = document.querySelector('.toast');
  el.textContent = message;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, 4800);
}
function syncThemeToggle() {
  const dark = document.documentElement.dataset.theme === 'dark';
  const button = document.querySelector('.theme-toggle');
  button.innerHTML = `${icon(dark ? 'sun' : 'moon')}<span>${dark ? 'Daylight reading' : 'Evening reading'}</span><span class="toggle-track"><i></i></span>`;
  button.setAttribute('aria-pressed', String(dark));
}
syncThemeToggle();
document.querySelector('.theme-toggle').addEventListener('click', () => {
  const theme = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
  document.documentElement.dataset.theme = theme;
  localStorage.setItem('reading-room-theme', theme);
  const url = new URL(location.href);
  url.searchParams.set('theme', theme);
  history.replaceState({}, '', url);
  syncThemeToggle();
});
document.querySelectorAll('[data-demo]').forEach(el => el.addEventListener('click', () => toast(el.dataset.demo)));
document.querySelectorAll('[data-query]').forEach(el => el.addEventListener('click', () => {
  const input = document.getElementById('question');
  input.value = el.dataset.query;
  input.focus();
}));
document.getElementById('research-search')?.addEventListener('submit', e => {
  e.preventDefault();
  toast('Opening a sample collection. Search and AI calls are not connected in this concept.');
  setTimeout(() => { location.href = 'topic.html'; }, 700);
});
document.addEventListener('keydown', e => {
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
    e.preventDefault();
    if (page === 'home') document.getElementById('question').focus();
    else location.href = 'index.html#search';
  }
});
const samplePapers = [
  ['Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks', 'Lewis et al.', 'NeurIPS 2020', 'Reading'],
  ['Dense Passage Retrieval for Open-Domain Question Answering', 'Karpukhin et al.', 'EMNLP 2020', 'To read'],
  ['Lost in the Middle: How Language Models Use Long Contexts', 'Liu et al.', 'TACL 2024', 'To read'],
  ['Self-RAG: Learning to Retrieve, Generate, and Critique', 'Asai et al.', 'ICLR 2024', 'New'],
  ['Corrective Retrieval Augmented Generation', 'Yan et al.', '2024', 'New'],
];
function switchTab(name) {
  document.querySelectorAll('[data-tab]').forEach(button => button.classList.toggle('active', button.dataset.tab === name));
  document.getElementById('overview-panel').hidden = name !== 'overview';
  const panel = document.getElementById('alternate-panel');
  panel.hidden = name === 'overview';
  if (name === 'papers') panel.innerHTML = `<div class="alternate-heading"><h2>The papers behind the ideas.</h2><span>Showing 5 sample papers from a 24-paper collection</span></div>${samplePapers.map((paper, i) => `<a class="reading-row" href="reader.html"><span class="paper-number">0${i+1}</span><div><h3>${paper[0]}</h3><p>${paper[1]} <b>·</b> ${paper[2]}</p></div><span class="pill">${paper[3]}</span>${icon('arrow')}</a>`).join('')}`;
  if (name === 'notes') panel.innerHTML = '<div class="alternate-heading"><h2>Thoughts worth keeping.</h2><span>Your sample notes</span></div><div class="saved-note"><span class="eyebrow">ON RETRIEVAL-AUGMENTED GENERATION</span><h3>Retrieval isn’t just about finding more information. It’s about finding the right context.</h3><p>Saved today · Linked to Lewis et al., 2020</p></div>';
  if (name === 'history') panel.innerHTML = '<div class="alternate-heading"><h2>A record of your understanding.</h2><span>Every report is a snapshot, not an overwrite.</span></div><div class="reading-row"><span class="paper-number">02</span><div><h3>The landscape, in brief.</h3><p>November 18, 2026 · 24 sources · Current version</p></div></div><div class="reading-row"><span class="paper-number">01</span><div><h3>A first look at retrieval-augmented generation.</h3><p>November 12, 2026 · 18 sources</p></div></div>';
}
document.querySelectorAll('[data-tab]').forEach(el => el.addEventListener('click', () => switchTab(el.dataset.tab)));
document.querySelector('[data-open-papers]')?.addEventListener('click', e => { e.preventDefault(); switchTab('papers'); });
document.getElementById('focus-paper')?.addEventListener('click', () => document.querySelector('.reader-layout').classList.toggle('focus-mode'));
document.querySelectorAll('.document-outline a').forEach(el => el.addEventListener('click', e => {
  e.preventDefault();
  document.querySelectorAll('.document-outline a').forEach(a => a.classList.toggle('active', a === el));
  toast(`${el.textContent.trim()} selected. This typeset preview contains page 1 only.`);
}));
document.getElementById('source-quote')?.addEventListener('click', () => {
  document.querySelector('.paper-document mark').classList.add('source-flash');
  document.querySelector('.paper-document mark').scrollIntoView({ behavior: 'smooth', block: 'center' });
  setTimeout(() => document.querySelector('.paper-document mark').classList.remove('source-flash'), 2200);
});
document.querySelector('.save-answer')?.addEventListener('click', e => {
  e.currentTarget.innerHTML = `${icon('check')} Saved to your notes`;
  toast('Saved locally for this sample session.');
});
function addReply(question) {
  const container = document.getElementById('conversation');
  const user = document.createElement('div');
  user.className = 'user-message';
  user.textContent = question;
  container.append(user);
  const reply = document.createElement('div');
  reply.className = 'assistant-message';
  reply.innerHTML = `<div class="assistant-message-label">${icon('sparkle')} SAMPLE RESPONSE</div><p>This is an unconnected visual prototype. In the live product, your assistant would answer with evidence from this paper.</p>`;
  container.append(reply);
  container.scrollTop = container.scrollHeight;
}
document.querySelectorAll('[data-followup]').forEach(el => el.addEventListener('click', () => addReply(el.dataset.followup)));
document.getElementById('chat-form')?.addEventListener('submit', e => {
  e.preventDefault();
  const input = document.getElementById('chat-question');
  if (input.value.trim()) addReply(input.value.trim());
  input.value = '';
});
document.querySelectorAll('[data-assistant-tab]').forEach(el => el.addEventListener('click', () => {
  if (el.dataset.assistantTab === 'notes') toast('Two sample notes: “Parametric vs. non-parametric memory” and “Retrieval as a reasoning step”.');
}));
