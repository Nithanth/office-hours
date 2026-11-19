import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source = readFileSync(new URL('./app.js', import.meta.url), 'utf8');

function preview(hash = '#home', search = '?theme=light') {
  const app = { innerHTML: '' };
  const root = { dataset: {} };
  const events = {};
  const store = new Map();
  const location = { hash, search, href: `http://example.test/${search}${hash}` };
  const context = vm.createContext({
    URLSearchParams, URL, location, setTimeout, clearTimeout,
    localStorage: { getItem: key => store.get(key), setItem: (key, value) => store.set(key, value) },
    history: { replaceState: (_state, _title, url) => { location.href = String(url); } },
    document: {
      documentElement: root,
      getElementById: id => id === 'app' ? app : null,
      addEventListener: (name, handler) => { events[name] = handler; },
    },
    window: { addEventListener() {}, scrollTo() {} },
  });
  vm.runInContext(source, context);
  return { app, root, events, context, location, store };
}

for (const theme of ['light', 'dark']) {
  for (const [route, heading] of [
    ['home', 'Less noise.'],
    ['topic', 'learn to reason.'],
    ['reader', 'Chain-of-Thought Prompting'],
  ]) {
    test(`${route} renders its seeded layout in ${theme}`, () => {
      const { app, root } = preview(`#${route}`, `?theme=${theme}`);
      assert.equal(root.dataset.theme, theme);
      assert.ok(app.innerHTML.includes(heading));
      assert.equal((app.innerHTML.match(/<main\b/g) || []).length, 1);
      assert.equal((app.innerHTML.match(/aria-current="page"/g) || []).length, 1);
      assert.equal((app.innerHTML.match(/<a\s/g) || []).length,
        (app.innerHTML.match(/<\/a>/g) || []).length);
    });
  }
}

test('unknown routes and themes fall back safely', () => {
  const { app, root } = preview('#missing', '?theme=invalid');
  assert.ok(app.innerHTML.includes('Less noise.'));
  assert.equal(root.dataset.theme, 'light');
});

test('theme control changes the page, stored preference, and shareable URL', () => {
  const { context, events, root, location, store } = preview();
  const button = { dataset: { action: 'theme' } };
  events.click({ target: { closest: () => button } });
  assert.equal(root.dataset.theme, 'dark');
  assert.equal(store.get('oh-signal-theme'), 'dark');
  assert.ok(location.href.includes('theme=dark'));
  assert.ok(vm.runInContext('shell(home(), "home")', context).includes('Switch to light theme'));
});

test('topic sections render distinct reports, notes, and paper lists', () => {
  const { context } = preview('#topic');
  for (const [tab, expected] of [
    ['Reports', 'A field guide to machine reasoning.'],
    ['Notes', 'The part I want to understand'],
    ['Papers', 'Your reading list'],
  ]) {
    const html = vm.runInContext(`topicTab = ${JSON.stringify(tab)}; topicContent()`, context);
    assert.ok(html.includes(expected));
  }
});

test('typed chat content is escaped before rendering', () => {
  const { context } = preview('#reader');
  const escaped = vm.runInContext(`escapeHTML('<img src=x onerror="alert(1)"> &')`, context);
  assert.equal(escaped, '&lt;img src=x onerror=&quot;alert(1)&quot;&gt; &amp;');
});

test('reader keeps source, assistant, and explicit sample disclosure together', () => {
  const { app } = preview('#reader');
  for (const expected of ['paper-document', 'Paper assistant', 'SAMPLE RESPONSE', 'SAMPLE MODE']) {
    assert.ok(app.innerHTML.includes(expected));
  }
});
