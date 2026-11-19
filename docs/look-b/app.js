/* Isolated visual prototype. All research, counts, and assistant replies are fixtures. */
const icons = {
  search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
  home: '<path d="m3 11 9-8 9 8v10h-7v-7h-4v7H3z"/>',
  library: '<path d="M4 4h4v16H4zM11 4h4v16h-4zM18 5l3-1 3 15-3 1z"/>',
  book: '<path d="M12 5v16M3 4c4-1 6-1 9 1 3-2 5-2 9-1v15c-4-1-6-1-9 1-3-2-5-2-9-1z"/>',
  file: '<path d="M5 2h9l5 5v15H5zM14 2v6h5M8 12h8M8 16h6"/>',
  chat: '<path d="M3 4h18v13H9l-6 4zM7 9h10M7 13h6"/>',
  arrow: '<path d="M5 12h14m-6-6 6 6-6 6"/>',
  up: '<path d="M12 20V4m-6 6 6-6 6 6"/>',
  diagonal: '<path d="M5 19 19 5M5 5h14v14"/>',
  chevron: '<path d="m9 5 7 7-7 7"/>',
  moon: '<path d="M20 15A9 9 0 0 1 9 3a9 9 0 1 0 11 12Z"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 1v2m0 18v2M1 12h2m18 0h2M4 4l2 2m12 12 2 2M4 20l2-2M18 6l2-2"/>',
  plus: '<path d="M12 4v16M4 12h16"/>',
  bookmark: '<path d="M6 3h12v19l-6-4-6 4z"/>',
  sliders: '<path d="M4 7h16M4 17h16M8 4v6m8 4v6"/>',
  refresh: '<path d="M20 7v5h-5M4 17v-5h5M5 8a8 8 0 0 1 13-3l2 3M4 16l2 3a8 8 0 0 0 13-3"/>',
  link: '<path d="m10 8 3-3a5 5 0 0 1 7 7l-3 3M14 16l-3 3a5 5 0 0 1-7-7l3-3M8 16l8-8"/>',
  quote: '<path d="M4 11h6v8H3v-8l4-6M15 11h6v8h-7v-8l4-6"/>',
  copy: '<path d="M8 8h13v13H8zM16 8V3H3v13h5"/>',
  thumbs: '<path d="M8 21H3V10h5m0 11h9l4-10V8h-8l1-6h-3l-3 8z"/>',
  expand: '<path d="M3 9V3h6m6 0h6v6M3 15v6h6m6 0h6v-6"/>',
  download: '<path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5"/>',
  check: '<path d="m4 12 5 5L20 6"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v6l4 2"/>',
  spark: '<path d="m12 2 3 7 7 3-7 3-3 7-3-7-7-3 7-3z"/>'
};
const icon = name => `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true">${icons[name] || icons.spark}</svg>`;
const escapeHTML = value => String(value).replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
const params = new URLSearchParams(location.search);
let theme = params.get('theme') || localStorage.getItem('oh-signal-theme') || 'light';
if (!['light', 'dark'].includes(theme)) theme = 'light';
let currentPage = 'home';
let topicTab = 'Overview';
let chatTab = 'Ask this paper';
let toastTimer;

function notify(message) {
  const toast = document.getElementById('toast');
  toast.textContent = message;
  toast.classList.add('visible');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove('visible'), 3600);
}

function shell(content, page) {
  const nav = (target, label, symbol, active) => `<a class="nav-link ${active ? 'active' : ''}" href="#${target}" ${active ? 'aria-current="page"' : ''}>${icon(symbol)}<span>${label}</span></a>`;
  return `<aside class="rail" aria-label="Workspace navigation">
    <a class="monogram" href="#home" aria-label="Office Hours home">o/h</a>
    <nav aria-label="Main">${nav('home','Discover','search',page==='home')}${nav('topic','My topics','library',page==='topic')}${nav('reader','Reading','book',page==='reader')}</nav>
    <div class="rail-bottom"><button class="theme-toggle" aria-label="Switch to ${theme==='light'?'dark':'light'} theme" data-action="theme">${icon(theme==='light'?'moon':'sun')}</button><button class="avatar" data-demo="Account settings are outside this visual study." aria-label="Account: Nithanth Ram">NR</button></div>
  </aside>
  <div class="workspace"><header class="topbar"><a href="#home" class="brand">office hours<span>A place for your next question.</span></a><div class="top-right"><span class="prototype-label mono">SIGNAL / DESIGN STUDY B</span><a class="top-link" href="#topic">${icon('chat')} Research assistant</a><span class="muted">Personal workspace ${icon('chevron').replace('class="icon"','class="icon" style="display:inline-block;width:10px;height:10px;vertical-align:middle;margin-left:9px"')}</span></div></header>${content}</div>`;
}

function footer() {
  return `<footer class="page-footer"><span class="footer-word">FOLLOW THE QUESTION.</span><span>Private by default. Curious by nature.</span><span class="mono" style="font-size:8px;letter-spacing:.5px">Mockup · sample content</span></footer>`;
}

function orbital() {
  return `<div class="orbit-art" aria-hidden="true"><svg viewBox="0 0 490 310"><defs><pattern id="dots" width="17" height="17" patternUnits="userSpaceOnUse"><circle cx="1" cy="1" r=".65" fill="currentColor" opacity=".22"/></pattern></defs><rect x="44" y="21" width="389" height="263" fill="url(#dots)"/><path d="M30 154h429M246 7v295" class="orbital-faint"/><circle cx="246" cy="154" r="99" class="orbit-core"/><ellipse cx="246" cy="154" rx="179" ry="67" transform="rotate(-30 246 154)" class="orbital"/><ellipse cx="246" cy="154" rx="179" ry="67" transform="rotate(30 246 154)" class="orbital"/><ellipse cx="246" cy="154" rx="67" ry="137" class="orbital"/><circle cx="246" cy="154" r="18" class="orbital"/><path d="M246 122v64m-32-32h64" class="orbital"/><circle cx="99" cy="204" r="6" fill="var(--ink)"/><circle cx="361" cy="224" r="4" fill="var(--ink)"/><circle cx="260" cy="19" r="4" fill="var(--ink)"/><path d="M30 24v-8h8m405 0h8v8M30 285v8h8m405 0h8v-8" class="orbital-faint"/><text x="47" y="37">FIG. 01</text><text x="321" y="283">IDEAS IN ORBIT</text></svg><span class="orbit-corner mono">A little further from the obvious.</span></div>`;
}

function home() {
  return `<main class="page home-page" id="main"><div class="home-hero"><div class="hero-copy"><div class="eyebrow muted">YOUR RESEARCH, IN GOOD COMPANY.</div><h1>Less noise.<br>More <span>signal.</span></h1><p>Find the papers that matter.<br>Make the connections that are yours.</p></div>${orbital()}</div>
    <form class="search-module" id="search-form"><div class="search-tabs" role="tablist" aria-label="Search mode"><button type="button" class="search-tab active" role="tab" aria-selected="true" data-search="topic">Explore a question</button><button type="button" class="search-tab" role="tab" aria-selected="false" data-search="link">Start with a paper</button></div><div class="search-row">${icon('search')}<input id="research-query" aria-label="Research question or paper URL" placeholder="What are you curious about?" autocomplete="off"><span class="keyboard-key">/ to focus</span><button class="search-button" type="submit">Find my next idea <span class="arrow">↗</span></button></div></form>
    <div class="search-suggestions"><span class="muted">A few rabbit holes</span><button class="suggestion" data-query="Reasoning in language models">Reasoning in language models ↗</button><button class="suggestion" data-query="Mechanistic interpretability">Mechanistic interpretability ↗</button><button class="suggestion" data-query="World models">World models ↗</button></div>
    <section class="home-collection"><div class="section-line"><h2 class="section-title">Pick up the thread.<small>Your topics, always evolving.</small></h2><a class="text-link" href="#topic">All topics <span>↗</span></a></div><div class="topic-grid">
      <a class="topic-card featured" href="#topic"><div class="eyebrow">01 / MACHINE LEARNING</div><div class="card-marker" aria-hidden="true"><i></i><i></i><i></i></div><h3>How models<br>learn to reason.</h3><span class="card-badge">+3 new papers</span><div class="topic-card-foot"><span>24 papers <span style="padding:0 6px">·</span> Updated today</span><span class="arrow">↗</span></div></a>
      <a class="topic-card" href="#topic"><div class="eyebrow muted">02 / INTERPRETABILITY</div><div class="card-marker" aria-hidden="true" style="transform:rotate(90deg)"><i></i><i></i><i></i></div><h3>Inside the<br>black box.</h3><div class="topic-card-foot"><span class="muted">18 papers <span style="padding:0 6px">·</span> Updated yesterday</span><span class="arrow">↗</span></div></a>
      <a class="topic-card" href="#topic"><div class="eyebrow muted">03 / EMBODIED AI</div><div class="card-marker" aria-hidden="true" style="transform:rotate(180deg)"><i></i><i></i><i></i></div><h3>Learning<br>the world.</h3><div class="topic-card-foot"><span class="muted">12 papers <span style="padding:0 6px">·</span> Updated Nov 16</span><span class="arrow">↗</span></div></a>
    </div></section><div class="home-bottom"><a href="#reader"><span class="tiny-cover">${icon('file')}</span>On your desk <strong>Chain-of-Thought Prompting…</strong> <span style="margin-left:8px">Continue reading ↗</span></a><span><span class="online-dot"></span>3 topics quietly keeping up with the literature.</span></div>${footer()}</main>`;
}

const papers = [
  ['Chain-of-Thought Prompting Elicits Reasoning in Large Language Models','Wei et al.','NeurIPS 2022','Reading','Foundational'],
  ['Self-Consistency Improves Chain of Thought Reasoning','Wang et al.','ICLR 2023','To read','Decoding'],
  ['Tree of Thoughts: Deliberate Problem Solving with Large Language Models','Yao et al.','NeurIPS 2023','To read','Search'],
  ['Let’s Verify Step by Step','Lightman et al.','ICLR 2024','Read','Verification']
];

function paperRows(limit = papers.length) {
  return papers.slice(0, limit).map((p,i) => `<a class="paper-row" href="#reader"><span class="paper-number">0${i+1}</span><div class="paper-info"><h3>${p[0]}</h3><p>${p[1]} <span style="padding:0 5px">/</span> ${p[2]}</p><div class="paper-tags"><span class="paper-tag ${i===0?'reading':''}">${p[3]}</span><span class="paper-tag">${p[4]}</span></div></div>${icon('diagonal')}</a>`).join('');
}

function synthesis() {
  return `<article class="synthesis"><div class="synthesis-head"><span class="eyebrow muted">THE BIG PICTURE / REPORT 04</span><span class="new-label mono">UP TO DATE</span></div><h2>Reasoning is becoming a process,<br>not just an answer.</h2><p>From chain-of-thought to search and verification, a common thread is emerging: giving models room to think changes what they can solve. Here’s how the ideas connect — and where the evidence is still thin.</p><div class="synthesis-meta"><span>Based on 24 papers · 8 min read · Nov 18</span><a href="#topic" data-topic-tab="Reports">Read the synthesis <span class="arrow">↗</span></a></div></article>`;
}

function topicContent() {
  if (topicTab === 'Papers') return `<section class="paper-section" style="margin-top:0"><div class="section-line"><h2>Your reading list <span class="muted" style="font-size:11px">/ 24</span></h2><button class="text-link" data-demo="The design preview shows four representative papers.">${icon('sliders')} Filter</button></div><div class="paper-list">${paperRows()}</div><p class="mock-note">Showing 4 sample papers from this topic.</p></section>`;
  if (topicTab === 'Reports') return `<article class="inline-empty"><div class="eyebrow muted">REPORT 04 / NOV 18, 2026</div><h2 style="margin-top:12px">A field guide to machine reasoning.</h2><p>Sample synthesis · 24 sources · 8 min read</p><h3>From prompting to deliberate computation</h3><p style="margin-top:12px;line-height:1.9">Chain-of-thought prompting showed that intermediate reasoning can help language models solve multi-step problems. Later work shifted attention toward sampling multiple paths, searching over alternatives, and verifying the steps rather than trusting a single completion.</p><h3>The open question</h3><p style="margin-top:12px;line-height:1.9">When is a longer chain actually better reasoning, and when is it simply more text? Compare the assumptions, evaluation settings, and failure cases before drawing a conclusion.</p><a href="#reader" class="button primary">Read a source paper ${icon('arrow')}</a><p class="mock-note">Illustrative copy for this visual study, not a generated research report.</p></article>`;
  if (topicTab === 'Notes') return `<article class="inline-empty"><div class="eyebrow muted">YOUR WORKING NOTES</div><h2 style="margin-top:14px">The part I want to understand</h2><p style="font-family:Georgia,serif;font-size:20px;line-height:1.7">Is better reasoning about better models, or giving the same model more room to think?</p><p>Compare self-consistency with process supervision. Look for controlled studies with matched inference budgets.</p><button class="button" data-demo="Notes are sample content in this design study.">Edit note ${icon('arrow')}</button></article>`;
  return `${synthesis()}<section class="paper-section"><div class="section-line"><h2>A good place to start.</h2><button class="text-link" data-topic-tab="Papers">View all 24 <span>↗</span></button></div><div class="paper-list">${paperRows(3)}</div></section>`;
}

function topic() {
  return `<main class="page topic-page" id="main"><div class="breadcrumb"><a href="#home">Workspace</a>${icon('chevron')}<a href="#home">My topics</a>${icon('chevron')}<span>Machine learning</span></div><section class="topic-intro"><div><div class="eyebrow muted">TOPIC 01 / AN ONGOING EXPLORATION</div><h1>How models<br>learn to reason.</h1><p>Tracing the path from plausible next words to deliberate thought.</p></div><div class="topic-actions"><button class="button" data-demo="Sharing is not connected in this visual prototype.">${icon('link')} Share topic</button><button class="button primary" data-action="ask-topic">${icon('spark')} Ask this topic</button></div></section>
    <div class="topic-stats"><div class="stat"><strong>24</strong><span>saved papers</span></div><div class="stat"><strong>04</strong><span>report versions</span></div><div class="stat"><strong>03</strong><span>new this week</span></div><div class="topic-feed"><span class="online-dot"></span>Following 2 searches <span style="padding:0 5px">/</span><button data-demo="Sample feed is up to date. No external search was made." aria-label="Refresh tracked searches">${icon('refresh')}</button></div></div>
    <div class="topic-tabs" role="tablist" aria-label="Topic sections">${['Overview','Papers','Reports','Notes'].map((name,i)=>`<button class="topic-tab ${topicTab===name?'active':''}" role="tab" aria-selected="${topicTab===name}" data-topic-tab="${name}">${name}${i?`<span>${[0,24,4,2][i]}</span>`:''}</button>`).join('')}</div>
    <div class="topic-layout"><div class="topic-content">${topicContent()}</div><aside class="topic-aside"><section><div class="aside-top"><h2>A thought to keep.</h2><button aria-label="Add a note" data-topic-tab="Notes">${icon('plus')}</button></div><p class="note-copy">“Is better reasoning about better models, or giving the same model <mark>more room to think?</mark>”</p><p class="note-time">Your note · 2 days ago</p></section><section class="aside-section"><div class="aside-top"><h2>Keeping an eye on</h2>${icon('search')}</div><div class="tracked-query">${icon('search')} Test-time compute scaling</div><div class="tracked-query">${icon('search')} Chain-of-thought reasoning</div><p class="tracking-note">New work finds its way here automatically.</p></section><section class="aside-section"><h2>Connected threads</h2><a href="#home" class="related-topic">Inside the black box <span class="arrow">↗</span></a><a href="#home" class="related-topic">Learning the world <span class="arrow">↗</span></a></section></aside></div>${footer()}</main>`;
}

function paperFigure() {
  return `<div class="paper-figure"><svg viewBox="0 0 216 137" role="img" aria-label="Illustrative diagram contrasting a direct answer with step-by-step reasoning"><g fill="none" stroke="#b1b9a4" stroke-width="1"><path d="M17 27h181M17 89h181"/></g><g font-family="Arial" font-size="7" fill="#57634b"><text x="6" y="11">STANDARD PROMPTING</text><text x="6" y="69">CHAIN-OF-THOUGHT PROMPTING</text></g><rect x="6" y="19" width="60" height="27" rx="3" fill="#e4e7dc"/><rect x="150" y="19" width="60" height="27" rx="3" fill="#e4e7dc"/><g font-family="Arial" font-size="8" fill="#30392b" text-anchor="middle"><text x="36" y="36">Question</text><text x="180" y="36">Answer</text></g><path d="M76 33h62m-4-3 4 3-4 3" stroke="#57634b" fill="none"/><rect x="6" y="80" width="46" height="28" rx="3" fill="#e4e7dc"/><rect x="69" y="80" width="77" height="28" rx="3" fill="#d5f45a"/><rect x="164" y="80" width="46" height="28" rx="3" fill="#e4e7dc"/><g font-family="Arial" font-size="7" fill="#30392b" text-anchor="middle"><text x="29" y="97">Question</text><text x="107" y="92">Intermediate</text><text x="107" y="102">reasoning steps</text><text x="187" y="97">Answer</text></g><path d="M54 94h12m-3-3 3 3-3 3m86 0h12m-3-3 3 3-3 3" stroke="#57634b" fill="none"/><text x="108" y="128" text-anchor="middle" font-family="Arial" font-size="6" fill="#75816b">MORE SPACE BETWEEN QUESTION AND ANSWER</text></svg></div><p class="figure-caption">Figure 1. An illustrative comparison of direct and step-by-step prompting. Reconstructed for this design study.</p>`;
}

function paperDocument() {
  return `<article class="paper-sheet" id="paper-document"><div class="journal-mark"><span>NEURIPS 2022</span><span>RESEARCH PAPER / SAMPLE EXCERPT</span></div><h1>Chain-of-Thought Prompting<br>Elicits Reasoning in<br>Large Language Models</h1><p class="paper-authors">Jason Wei · Xuezhi Wang · Dale Schuurmans · Maarten Bosma<br>Fei Xia · Ed Chi · Quoc V. Le · Denny Zhou</p><p class="paper-affiliation">Google Research, Brain Team</p><section class="abstract"><h2>Abstract</h2><p>Large language models can produce fluent text, yet complex reasoning remains challenging. This work explores how providing intermediate reasoning steps — a chain of thought — can improve performance on multi-step tasks. <mark>Rather than asking only for an answer, the prompt demonstrates how to arrive at one.</mark> Examples of reasoning are included in the context, without updating model parameters. The results suggest that sufficiently large models can benefit from this simple change across arithmetic, commonsense, and symbolic reasoning tasks.</p></section><div class="paper-columns"><section><h2>1 &nbsp; Introduction</h2><p>What does it take for a language model to solve a problem that requires more than a single step? Scaling has unlocked striking capabilities, but getting a model to reason reliably remains an open challenge.</p><p>Consider a word problem. A direct answer reveals little about the path taken. By contrast, a sequence of intermediate steps breaks a difficult question into smaller pieces and makes the computation easier to inspect.</p>${paperFigure()}</section><section><h2>2 &nbsp; Chain-of-Thought Prompting</h2><p>The central idea is to augment few-shot examples with a natural-language rationale. Each demonstration contains a question, a chain of intermediate steps, and the final answer.</p><p>This differs from standard prompting, where examples provide only an input and an output. The intermediate text gives the model room to decompose a problem before committing to a result.</p><h2 style="margin-top:18px">An emergent ability</h2><p>Benefits are most apparent at larger model scales. The effectiveness of the method depends on the model, the task, and the demonstrations supplied in the prompt.</p><p class="column-extra">The broader implication is that how we ask a question can change the computation a model performs. This opens a line of work on reasoning paths, verification, and inference-time computation.</p></section></div><div class="page-number">1</div></article>`;
}

function initialAnswer() {
  return `<div class="user-question">What’s the core idea, and why does it matter?</div><div class="assistant-label">${icon('spark')} Office Hours <span class="muted" style="font-size:8px;font-weight:400;margin-left:auto">SAMPLE RESPONSE</span></div><div class="answer"><p>The simple shift: <strong>show the model how to think through a problem, not just what the answer looks like.</strong> <button class="cite" data-action="citation">p. 1 ↗</button></p><ol><li><strong>Standard prompting</strong> gives the model question → answer examples.</li><li><strong>Chain-of-thought</strong> adds the steps in between: question → reasoning → answer.</li></ol><p>It matters because this can unlock stronger reasoning <strong>without retraining the model.</strong> The reasoning examples live in the prompt itself.</p><p>One caveat: gains depend on model scale and the task. Longer explanations aren’t automatically better reasoning.</p></div><div class="source-card"><div class="eyebrow muted">GROUNDED IN THE PAPER</div><p>“The prompt demonstrates how to arrive at [an answer].”</p><a href="#reader" data-action="citation">Page 1 · Abstract <span>↗</span></a></div><div class="answer-actions"><button data-demo="Sample response copied in the visual prototype." aria-label="Copy sample response">${icon('copy')}</button><button data-demo="Thanks — feedback is only simulated in this design study." aria-label="Helpful response">${icon('thumbs')}</button><button data-demo="Sample answer saved to this prototype’s notes view." data-action="save-note" aria-label="Save response as a note">${icon('bookmark')}</button></div>`;
}

function chatBody() {
  if (chatTab === 'Notes') return `<div class="eyebrow muted">FROM YOUR READING</div><p class="note-copy">Prompting changes the structure of the task, not the model weights.</p><p class="note-time">Sample note · linked to page 1</p><div class="aside-section"><p class="answer">Compare with self-consistency: does sampling multiple reasoning paths improve reliability?</p></div>`;
  if (chatTab === 'Outline') return `<div class="eyebrow muted">IN THIS PAPER</div>${['Abstract','1  Introduction','2  Chain-of-Thought Prompting','An emergent ability'].map(t=>`<button class="tracked-query" style="width:100%;text-align:left" data-action="citation">${t} <span style="margin-left:auto">01</span></button>`).join('')}<p class="mock-note">One-page excerpt in this visual prototype.</p>`;
  return `<div class="chat-kicker"><span class="assistant-symbol">✳</span><span class="mono muted">THINK WITH THE PAPER.</span></div><div id="chat-messages">${initialAnswer()}</div>`;
}

function reader() {
  return `<main class="reader-page" id="main"><div class="reader-toolbar"><div class="breadcrumb"><a href="#topic">← Back to topic</a><span>/</span><span>How models learn to reason</span></div><div class="reader-controls"><span class="reader-progress">01 / 43</span><button data-demo="This is a one-page visual excerpt, not the full PDF." aria-label="Expand document">${icon('expand')}</button><button class="button save-paper" data-action="save-paper">${icon('check')} In your library</button></div></div><div class="reader-layout"><section class="document-pane" aria-label="Paper preview"><div class="doc-head"><span>arXiv:2201.11903 <span style="margin:0 8px">/</span> PDF READER</span><span>100% <button data-demo="Zoom controls are illustrative in this visual study." aria-label="Zoom options">⌄</button><button data-demo="Download the original paper from arxiv.org/abs/2201.11903." aria-label="Download original PDF">${icon('download')}</button></span></div>${paperDocument()}</section><aside class="chat-pane" aria-label="Paper assistant"><div class="chat-tabs" role="tablist" aria-label="Reader tools">${['Ask this paper','Notes','Outline'].map(t=>`<button class="chat-tab ${chatTab===t?'active':''}" role="tab" aria-selected="${chatTab===t}" data-chat-tab="${t}">${t}${t==='Notes'?'<span>2</span>':''}</button>`).join('')}</div><div class="chat-body">${chatBody()}</div><div class="chat-composer"><button class="follow-up" data-follow-up="How does this compare to self-consistency?">How does this compare to self-consistency? ↗</button><form class="composer-box" id="chat-form"><textarea aria-label="Ask a follow-up about the paper" placeholder="Pull on a thread…" rows="2"></textarea><div class="composer-bottom"><span>THIS PAPER ONLY <span style="padding:0 5px">·</span> SAMPLE MODE</span><button class="send-button" type="submit" aria-label="Send question">${icon('up')}</button></div></form><p class="chat-disclaimer">An assistant, not an authority. Always check the source.</p></div></aside></div></main>`;
}

function render() {
  const requested = location.hash.slice(1) || 'home';
  currentPage = ['home','topic','reader'].includes(requested) ? requested : 'home';
  document.documentElement.dataset.theme = theme;
  document.title = `${{home:'Discover',topic:'How models learn to reason',reader:'Chain-of-Thought Prompting'}[currentPage]} · Office Hours / SIGNAL`;
  document.getElementById('app').innerHTML = shell(({home,topic,reader}[currentPage])(), currentPage);
}

document.addEventListener('click', event => {
  const button = event.target.closest('button, a');
  if (!button) return;
  if (button.dataset.demo) notify(button.dataset.demo);
  if (button.dataset.action === 'theme') {
    theme = theme === 'light' ? 'dark' : 'light';
    localStorage.setItem('oh-signal-theme', theme);
    const url = new URL(location.href);
    url.searchParams.set('theme', theme);
    history.replaceState(null, '', url);
    render();
  }
  if (button.dataset.query) {
    document.getElementById('research-query').value = button.dataset.query;
    document.getElementById('research-query').focus();
  }
  if (button.dataset.search) {
    document.querySelectorAll('[data-search]').forEach(tab => {
      const selected = tab === button;
      tab.classList.toggle('active', selected);
      tab.setAttribute('aria-selected', String(selected));
    });
    document.getElementById('research-query').placeholder = button.dataset.search === 'link' ? 'Paste an arXiv link or paper title…' : 'What are you curious about?';
    document.getElementById('research-query').focus();
  }
  if (button.dataset.topicTab) {
    event.preventDefault();
    topicTab = button.dataset.topicTab;
    render();
  }
  if (button.dataset.chatTab) {
    chatTab = button.dataset.chatTab;
    render();
  }
  if (button.dataset.followUp) {
    const textarea = document.querySelector('#chat-form textarea');
    textarea.value = button.dataset.followUp;
    textarea.focus();
  }
  if (button.dataset.action === 'citation') {
    event.preventDefault();
    document.querySelector('.abstract').scrollIntoView({behavior:'smooth',block:'center'});
    notify('Page 1 · Abstract — highlighted in the sample excerpt.');
  }
  if (button.dataset.action === 'ask-topic') {
    notify('This preview’s paper assistant is ready with an example conversation.');
    location.hash = 'reader';
    chatTab = 'Ask this paper';
  }
  if (button.dataset.action === 'save-paper') {
    const saved = button.textContent.includes('In your library');
    button.innerHTML = `${icon(saved?'plus':'check')} ${saved?'Save paper':'In your library'}`;
    notify(saved?'Removed from the sample library.':'Added to the sample library.');
  }
  if (button.dataset.action === 'save-note') {
    chatTab = 'Notes';
    render();
  }
});

document.addEventListener('submit', event => {
  if (event.target.id === 'search-form') {
    event.preventDefault();
    const query = document.getElementById('research-query').value.trim();
    if (!query) return document.getElementById('research-query').focus();
    topicTab = 'Overview';
    location.hash = 'topic';
    notify('Sample topic opened. This prototype does not call search or AI services.');
  }
  if (event.target.id === 'chat-form') {
    event.preventDefault();
    const textarea = event.target.querySelector('textarea');
    const question = textarea.value.trim();
    if (!question) return textarea.focus();
    if (chatTab !== 'Ask this paper') {
      chatTab = 'Ask this paper';
      render();
    }
    const messages = document.getElementById('chat-messages');
    messages.insertAdjacentHTML('beforeend', `<div class="user-question" style="margin-top:24px">${escapeHTML(question)}</div><div class="assistant-label">${icon('spark')} Sample response</div><p class="answer">This is an interactive design study, not a live assistant. In the product, this is where an answer grounded in the paper would appear, with citations back to the relevant passages.</p>`);
    document.querySelector('#chat-form textarea').value = '';
    messages.lastElementChild.scrollIntoView({behavior:'smooth',block:'center'});
  }
});

document.addEventListener('keydown', event => {
  if (event.key === '/' && currentPage === 'home' && !['INPUT','TEXTAREA'].includes(document.activeElement.tagName)) {
    event.preventDefault();
    document.getElementById('research-query').focus();
  }
});
window.addEventListener('hashchange', () => { render(); window.scrollTo(0,0); });
render();
