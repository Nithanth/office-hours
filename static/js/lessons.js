(() => {
  const source = document.querySelector('#lesson-source');
  const editor = document.querySelector('#lesson-editor');
  const scenes = document.querySelector('#lesson-scenes');
  const status = document.querySelector('#lesson-status');
  const planStatus = document.querySelector('#lesson-plan-status');
  const add = document.querySelector('#lesson-add');
  const remove = document.querySelector('#lesson-remove');
  const output = document.querySelector('#lesson-output');
  const empty = document.querySelector('#lesson-empty');
  const example = [
    {title:'What can one coordinate preserve?', kind:'prediction', labels:['Choose a direction','Maximize the spread'], values:[], source:'Illustrative PCA example', narration:'Imagine a ruler through a stretched cloud of points. Which direction would you choose to keep the projected points spread apart?'},
    {title:'Follow the projections', kind:'sequence', labels:['Center the points','Rotate the ruler','Find maximum variance'], values:[], source:'Illustrative PCA example', narration:'Center the data and project each point onto a rotating ruler. The direction with the largest projected variance is the first principal component.'}
  ];

  function field(parent, name, title, value, type = 'input') {
    const label = document.createElement('label');
    label.textContent = title;
    const input = document.createElement(type);
    if (type === 'select') {
      for (const kind of ['prediction', 'sequence', 'comparison', 'network']) input.add(new Option(kind, kind));
    }
    input.name = name;
    input.value = value;
    input.required = true;
    label.append(input);
    parent.append(label);
    return input;
  }

  function edit(board) {
    empty.hidden = true;
    editor.hidden = false;
    scenes.replaceChildren();
    board.scenes.forEach((scene, i) => appendScene(scene, board.source_pages, i === 0));
  }

  function appendScene(scene, sourcePages, open) {
    const i = scenes.children.length;
    const card = document.createElement('details');
    card.open = open;
    const summary = document.createElement('summary');
    const updateTitle = title => { summary.textContent = (i + 1) + '. ' + (title || 'Untitled scene'); };
    updateTitle(scene.title);
    card.append(summary);
    const title = field(card,'title','Scene title',scene.title);
    title.maxLength = 64;
    title.oninput = () => updateTitle(title.value);
    const kind = field(card,'kind','Visual',scene.kind,'select');
    const labels = field(card,'labels','Visual labels · one per line',scene.labels.join('\n'),'textarea');
    labels.rows = 3;
    const values = field(card,'values','Comparison values · comma-separated',scene.values.join(', '));
    const dropped = field(card,'dropped','Dropped units · numbered from 1 across layers', (scene.dropped || []).join(', '));
    dropped.required = false;
    function toggle() {
      values.parentElement.firstChild.textContent = kind.value === 'network' ? 'Layer sizes · comma-separated' : 'Comparison values · comma-separated';
      values.parentElement.hidden = !['comparison','network'].includes(kind.value);
      dropped.parentElement.hidden = kind.value !== 'network';
      values.disabled = values.parentElement.hidden;
    }
    toggle();
    kind.onchange = toggle;
    field(card,'narration','Narration',scene.narration,'textarea').maxLength = 450;
    const reference = field(card,'source','Source reference',scene.source);
    reference.maxLength = 90;
    const figure = document.createElement('details');
    const toggleFigure = document.createElement('summary');
    toggleFigure.textContent = 'Check source page';
    const image = document.createElement('img');
    figure.append(toggleFigure, image);
    card.append(figure);
    function showSource() {
      const url = sourcePages?.[reference.value];
      figure.hidden = !url;
      if (url) { image.src = url; image.alt = reference.value; }
    }
    reference.oninput = showSource;
    showSource();
    scenes.append(card);
    updateSceneActions();
    return card;
  }

  function updateSceneActions() {
    add.hidden = scenes.children.length >= 6;
    remove.hidden = scenes.children.length < 2;
  }

  function blankScene() {
    return {title:'', kind:'sequence', labels:[], values:[], source:'', narration:''};
  }

  document.querySelector('#lesson-manual').onclick = () => {
    planStatus.textContent = '';
    edit({scenes:[blankScene()]});
    scenes.querySelector('input').focus();
  };
  add.onclick = () => appendScene(blankScene(), {}, true).querySelector('input').focus();
  remove.onclick = () => {
    scenes.lastElementChild.remove();
    updateSceneActions();
    add.focus();
  };
  editor.addEventListener('invalid', event => {
    event.target.closest('details').open = true;
  }, true);

  function read() {
    return {scenes: Array.from(scenes.children, card => {
      const get = name => card.querySelector('[name="'+name+'"]').value.trim();
      const labels = get('labels').split('\n').map(x => x.trim()).filter(Boolean);
      if (labels.length < 2 || labels.length > (get('kind') === 'network' ? 4 : 3) || labels.some(x => x.length > 32)) {
        card.open = true;
        throw Error('Use two or three labels (up to four for networks) of up to 32 characters in each scene.');
      }
      return {title:get('title'),kind:get('kind'),labels,
        dropped:get('kind') === 'network' && get('dropped') ? get('dropped').split(',').map(Number) : [],
        values:['comparison','network'].includes(get('kind')) ? get('values').split(',').map(Number) : [],
        narration:get('narration'),source:get('source')};
    })};
  }

  async function api(path, options) {
    const response = await fetch(path,options);
    const data = await response.json();
    if (!response.ok) throw Error(typeof data.detail === 'string' ? data.detail : 'Check the fields in your storyboard and try again.');
    return data;
  }
  function busy(value) {
    for (const button of document.querySelectorAll('.lesson-studio button')) button.disabled = value;
  }
  document.querySelector('#lesson-example')?.addEventListener('click', () => edit({scenes:example}));
  source.onsubmit = async event => {
    event.preventDefault();
    busy(true);
    planStatus.textContent = 'Reading the source and planning scenes…';
    try {
      const data = new FormData(source);
      if (!data.get('pdf')?.size) data.delete('pdf');
      const board = await api('/api/lessons/plan',{method:'POST',body:data});
      edit(board);
      output.querySelector('video').pause();
      output.hidden = true;
      history.replaceState(null, '', location.pathname + location.search);
      status.textContent = '';
      planStatus.textContent = 'Storyboard ready for review.';
      document.querySelector('#lesson-board-title').focus();
    } catch (error) {planStatus.textContent = error.message;} finally {busy(false);}
  };
  async function playback(id) {
    status.textContent = 'Rendering narration and animation…';
    while (true) {
      const job = await api('/api/lessons/'+id);
      if (job.state === 'failed') throw Error('Render failed. Check the storyboard and retry.');
      if (job.state === 'ready') {
        if (editor.hidden) edit(job.board);
        const transcript = document.querySelector('#lesson-transcript');
        transcript.replaceChildren();
        for (const scene of job.board.scenes) {
          const title = document.createElement('h3');
          title.textContent = scene.title;
          const text = document.createElement('p');
          text.textContent = scene.narration;
          const reference = document.createElement('small');
          reference.textContent = scene.source;
          transcript.append(title, text, reference);
        }
        break;
      }
      await new Promise(resolve => setTimeout(resolve,1500));
    }
    const url = '/api/lessons/'+id+'/video';
    output.querySelector('video').src = url;
    output.querySelector('a').href = url;
    output.hidden = false;
    output.querySelector('h2').focus();
    status.textContent = 'Video ready. Available for 24 hours.';
  }
  editor.onsubmit = async event => {
    event.preventDefault(); busy(true);
    try {
      const board = read();
      const scope = new URLSearchParams();
      for (const key of ['paper_id','topic_id']) {
        if (source.elements[key]) scope.set(key, source.elements[key].value);
      }
      const job = await api('/api/lessons/render?' + scope,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(board)});
      history.replaceState(null, "", "#" + job.id);
      output.querySelector('video').pause();
      output.hidden = true;
      await playback(job.id);
    } catch (error) {status.textContent = error.message;} finally {busy(false);}
  };
  window.addEventListener('hashchange', () => location.reload());
  if (/^#[a-f0-9-]{36}$/.test(location.hash)) {
    busy(true);
    playback(location.hash.slice(1)).catch(e => {status.textContent=e.message;}).finally(() => busy(false));
  }
})();
