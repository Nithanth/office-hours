// Standalone design fixture: hand-positioned papers, not computed embeddings.
// Selection never changes coordinates. No API calls or production data.
(() => {
  if (document.body.dataset.page !== 'topic') return;

  const groups = [
    { id: 'retrieval', label: 'Retrieval & fusion', x: 8, y: 11,
      question: 'How do these papers combine retrieval and generation?',
      answer: 'The shared idea is to retrieve evidence before answering. The differences are in how passages are retrieved and where the generator combines them.' },
    { id: 'adaptive', label: 'Adaptive retrieval', x: 61, y: 11,
      question: 'When do these methods decide to retrieve?',
      answer: 'This group explores whether retrieval should be a fixed step or a decision made while generating. A useful reading question is what triggers another search, and how the system judges its evidence.' },
    { id: 'evaluation', label: 'Evaluation & context', x: 8, y: 63,
      question: 'What can still go wrong with good evidence?',
      answer: 'Relevant context does not guarantee a grounded answer. Read this group for evidence placement, faithful use of sources, and the distinction between retrieval quality and answer quality.' },
    { id: 'attribution', label: 'Attribution & trust', x: 61, y: 63,
      question: 'How can I check whether an answer is supported?',
      answer: 'These papers focus on connecting claims to sources. Look for whether citations actually support a claim, rather than simply appearing alongside it.' },
  ];
  const rows = [
    ['rag','retrieval','Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks','Lewis et al. · 2020','reading',12,27],
    ['dpr','retrieval','Dense Passage Retrieval for Open-Domain Question Answering','Karpukhin et al. · 2020','done',21,29],
    ['fid','retrieval','Leveraging Passage Retrieval with Generative Models for Open Domain Question Answering','Izacard & Grave · 2021','to-read',31,24],
    ['realm','retrieval','REALM: Retrieval-Augmented Language Model Pre-Training','Guu et al. · 2020','done',15,41],
    ['atlas','retrieval','Atlas: Few-shot Learning with Retrieval Augmented Language Models','Izacard et al. · 2023','to-read',25,43],
    ['retro','retrieval','Improving Language Models by Retrieving from Trillions of Tokens','Borgeaud et al. · 2022','to-read',36,36],
    ['contriever','retrieval','Unsupervised Dense Information Retrieval with Contrastive Learning','Izacard et al. · 2022','done',19,54],
    ['replug','retrieval','REPLUG: Retrieval-Augmented Black-Box Language Models','Shi et al. · 2024','to-read',34,49],
    ['self-rag','adaptive','Self-RAG: Learning to Retrieve, Generate, and Critique','Asai et al. · 2024','reading',64,27],
    ['crag','adaptive','Corrective Retrieval Augmented Generation','Yan et al. · 2024','to-read',75,24],
    ['flare','adaptive','Active Retrieval Augmented Generation','Jiang et al. · 2023','to-read',86,33],
    ['ircot','adaptive','Interleaving Retrieval with Chain-of-Thought Reasoning for Knowledge-Intensive Multi-Step Questions','Trivedi et al. · 2023','to-read',69,41],
    ['self-ask','adaptive','Measuring and Narrowing the Compositionality Gap in Language Models','Press et al. · 2023','done',79,49],
    ['adaptive-rag','adaptive','Adaptive-RAG: Learning to Adapt Retrieval-Augmented Large Language Models through Question Complexity','Jeong et al. · 2024','to-read',90,46],
    ['lost-middle','evaluation','Lost in the Middle: How Language Models Use Long Contexts','Liu et al. · 2024','done',13,78],
    ['ragas','evaluation','RAGAS: Automated Evaluation of Retrieval Augmented Generation','Es et al. · 2024','reading',24,75],
    ['rgb','evaluation','Benchmarking Large Language Models in Retrieval-Augmented Generation','Chen et al. · 2024','to-read',35,83],
    ['ares','evaluation','ARES: An Automated Evaluation Framework for Retrieval-Augmented Generation Systems','Saad-Falcon et al. · 2024','to-read',40,74],
    ['neg-rejection','evaluation','Making Retrieval-Augmented Language Models Robust to Irrelevant Context','Yoran et al. · 2024','to-read',26,91],
    ['alce','attribution','Enabling Large Language Models to Generate Text with Citations','Gao et al. · 2023','done',64,79],
    ['attr-first','attribution','Attribute First, then Generate: Locally-attributable Grounded Text Generation','Slobodkin et al. · 2024','to-read',75,85],
    ['webgpt','attribution','WebGPT: Browser-assisted Question-Answering with Human Feedback','Nakano et al. · 2021','done',87,77],
    ['gophercite','attribution','Teaching Language Models to Support Answers with Verified Quotes','Menick et al. · 2022','to-read',69,93],
    ['rarr','attribution','RARR: Researching and Revising What Language Models Say, Using Language Models','Gao et al. · 2023','to-read',85,92],
  ];
  const papers = rows.map(([id, group, title, byline, status, x, y]) => ({id, group, title, byline, status, x, y}));
  const statuses = { 'to-read': 'To read', reading: 'Reading', done: 'Done' };
  const state = { group: null, paper: null, reply: false };
  const esc = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const groupPapers = id => papers.filter(p => !id || p.group === id);
  const activeGroup = () => groups.find(g => g.id === state.group);

  function mapMarkup() {
    const group = activeGroup();
    const shown = groupPapers(state.group);
    return `<section class="paper-map" aria-labelledby="paper-map-title">
      <div class="map-intro"><div><h2 id="paper-map-title">Find your next paper</h2><p>Follow a subtopic, or select a paper to take a closer look.</p></div><span class="map-pinned">${ic('bookmark')} Layout pinned</span></div>
      <div class="map-scope-bar"><button type="button" class="map-all ${state.group?'':'active'}" data-map-group="all">All papers <span>${papers.length}</span></button><span class="map-scope-divider">/</span><span id="map-current-scope">${group?esc(group.label):`${groups.length} subtopics`}</span>${group?'<button type="button" class="map-clear" data-map-group="all">Clear selection '+ic('close')+'</button>':''}</div>
      <div class="map-workbench">
        <div class="map-explorer"><div class="map-stage" role="group" aria-label="Illustrative paper map. Select a labeled subtopic or paper.">
          <svg class="map-regions" viewBox="0 0 600 390" preserveAspectRatio="none" aria-hidden="true"><path d="M36 63 Q83 39 204 63 Q273 99 250 192 Q199 237 65 220 Q18 168 36 63Z"/><path d="M345 58 Q462 39 557 75 Q587 147 546 215 Q445 237 359 195 Q317 127 345 58Z"/><path d="M40 261 Q134 225 236 263 Q278 309 245 373 Q121 400 38 356 Q15 302 40 261Z"/><path d="M345 264 Q447 227 548 265 Q585 305 552 378 Q434 406 348 367 Q319 314 345 264Z"/></svg>
          ${groups.map(g=>`<button type="button" class="map-region-label ${state.group===g.id?'selected':''}" style="left:${g.x}%;top:${g.y}%" data-map-group="${g.id}" aria-pressed="${state.group===g.id}">${esc(g.label)}<span>${groupPapers(g.id).length} papers ${ic('arrow')}</span></button>`).join('')}
          ${papers.map(p=>`<button type="button" class="map-dot status-${p.status} ${state.group&&state.group!==p.group?'dimmed':''} ${state.paper===p.id?'selected':''}" style="left:${p.x}%;top:${p.y}%" data-map-paper="${p.id}" aria-label="${esc(p.title)} — ${statuses[p.status]}" aria-pressed="${state.paper===p.id}" title="${esc(p.title)} · ${statuses[p.status]}"><span></span></button>`).join('')}
        </div><div class="map-legend" aria-label="Paper reading status">${Object.entries(statuses).map(([key,label])=>`<span><i class="map-status-dot status-${key}"></i>${label}<small>${papers.filter(p=>p.status===key).length}</small></span>`).join('')}</div><p class="map-explainer">Nearby papers cover related ideas. Distance is approximate, not a measure of quality.</p><p class="map-fixture-note">Illustrative map · fixed sample positions, not a live embedding</p></div>
        <aside class="map-paper-panel" aria-label="Papers and scoped topic chat"><div class="map-list-heading"><div><span class="section-label">${group?'SELECTED SUBTOPIC':'IN THIS TOPIC'}</span><h3>${group?esc(group.label):'All papers'}</h3></div><span>${shown.length}</span></div><div class="map-paper-list" role="group" aria-label="${group?esc(group.label):'All topic'} papers">${shown.map(p=>`<article class="map-paper-row ${state.paper===p.id?'selected':''}" data-map-row="${p.id}"><button type="button" class="map-paper-select" data-map-paper="${p.id}" aria-pressed="${state.paper===p.id}"><span class="map-row-title">${esc(p.title)}</span><span class="map-row-meta">${esc(p.byline)}</span><span class="map-row-status"><i class="map-status-dot status-${p.status}"></i>${statuses[p.status]}</span></button>${state.paper===p.id?`<div class="map-paper-actions"><a href="reader.html">Open paper ${ic('arrow')}</a><button type="button" data-map-reading="${p.id}">${p.status==='reading'?ic('check')+' Reading':'Mark reading'}</button></div>`:''}</article>`).join('')}</div>
          <section class="map-topic-chat" aria-labelledby="map-chat-heading"><div class="map-chat-heading"><h3 id="map-chat-heading">${ic('chat')} Topic chat</h3><span>${shown.length} papers</span></div><p class="map-chat-scope">${group?'Only '+esc(group.label):'Across this topic'}</p><button type="button" class="map-suggested-question" id="map-suggested-question">${group?esc(group.question):'Where should I start with this topic?'} ${ic('arrow')}</button><div class="map-chat-reply" id="map-chat-reply" role="status" ${state.reply?'':'hidden'}><span>SAMPLE RESPONSE · ${shown.length} PAPERS</span><p>${group?esc(group.answer):'Start with the original RAG paper, then explore retrieval methods, adaptive search, evaluation and attribution. Select a subtopic to narrow both this list and the conversation.'}</p><button type="button" data-map-paper="${shown[0].id}">View source ${ic('diagonal')}</button></div><form id="map-chat-form"><label class="sr-only" for="map-chat-input">Ask ${group?esc(group.label):'this topic'}</label><input id="map-chat-input" placeholder="Ask ${group?'these '+shown.length+' papers':'this topic'}…" autocomplete="off" required><button type="submit" aria-label="Send topic question">${ic('up')}</button></form><p class="map-chat-fixture">Sample chat · no model connected</p></section>
        </aside>
      </div><p class="map-selection-status sr-only" role="status">${group?`${esc(group.label)} selected. ${shown.length} papers. Topic chat uses this subtopic.`:'All 24 papers. Topic chat uses the whole collection.'}</p>
    </section>`;
  }

  function render(focusTarget) {
    const panel = document.getElementById('topic-alt-panel');
    panel.innerHTML = mapMarkup();
    panel.querySelectorAll('[data-map-group]').forEach(button=>button.addEventListener('click',()=>{
      state.group=button.dataset.mapGroup==='all'?null:button.dataset.mapGroup;
      state.paper=null;state.reply=false;
      render({group:button.dataset.mapGroup});
    }));
    panel.querySelectorAll('[data-map-paper]').forEach(button=>button.addEventListener('click',()=>{
      const paper=papers.find(p=>p.id===button.dataset.mapPaper);
      state.paper=paper.id;
      if(state.group!==paper.group){state.group=paper.group;state.reply=false;}
      render({paper:paper.id});
      const row=panel.querySelector(`[data-map-row="${paper.id}"]`);
      const list=panel.querySelector('.map-paper-list');
      list.scrollTop=row.offsetTop;
    }));
    panel.querySelectorAll('[data-map-reading]').forEach(button=>button.addEventListener('click',()=>{
      papers.find(p=>p.id===button.dataset.mapReading).status='reading';
      render({paper:state.paper});
    }));
    const showReply=()=>{
      state.reply=true;
      panel.querySelector('#map-chat-reply').hidden=false;
    };
    panel.querySelector('#map-suggested-question').addEventListener('click',showReply);
    panel.querySelector('#map-chat-form').addEventListener('submit',event=>{
      event.preventDefault();
      if(panel.querySelector('#map-chat-input').value.trim())showReply();
      panel.querySelector('#map-chat-input').value='';
    });
    if(focusTarget?.group)panel.querySelector(`[data-map-group="${focusTarget.group}"]`)?.focus({preventScroll:true});
    if(focusTarget?.paper)panel.querySelector(`.map-dot[data-map-paper="${focusTarget.paper}"]`)?.focus({preventScroll:true});
  }

  const tab=document.createElement('button');
  // Preserve the word boundary when the map's compact heading hides the break.
  document.querySelector('.topic-title-block h1 br')?.after(document.createTextNode(' '));
  tab.type='button';tab.dataset.topicTab='map';tab.textContent='Paper map';
  document.querySelector('[data-topic-tab="papers"]').after(tab);
  tab.addEventListener('click',()=>{
    topicTab('map');
    document.querySelector('.topic-sheet').classList.add('map-active');
    render();
  });
  document.querySelectorAll('[data-topic-tab]').forEach(button=>{
    button.setAttribute('role','tab');
    button.setAttribute('aria-selected',String(button.classList.contains('active')));
    if(button!==tab)button.addEventListener('click',()=>document.querySelector('.topic-sheet').classList.remove('map-active'));
  });
  if(new URLSearchParams(location.search).get('view')==='map')tab.click();
})();
