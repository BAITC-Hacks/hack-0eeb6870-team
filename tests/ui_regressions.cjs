'use strict';
// Exercise the shipped handlers without adding a browser/npm dependency to the demo.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'moneygraph/static/app.js'), 'utf8');
const html = fs.readFileSync(path.join(root, 'moneygraph/static/index.html'), 'utf8');
const tick = () => new Promise(resolve => setImmediate(resolve));
const deferred = () => {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return {promise, resolve};
};

function fixture(hash = 'dataset-a') {
  const ids = ['1', '2', ...Array.from({length: 74}, (_, i) => String(i + 3))];
  const nodes = ids.map((gid, i) => ({
    gid, x: i, y: 0, role: 'peripheral', color: '#888', cluster_id: 0,
    depth: i ? 1 : 0, is_seed: i === 0, priority_score: .3+(i%8)*.1, role_score: .2,
    evidence: '1 перевод', sum_in: 5000, sum_out: 5000, in_degree: 1,
    out_degree: 1, in_tx: 1, out_tx: 1, pagerank: .1, pass_ratio: 1,
    priority_factors: {volume: .1}
  }));
  return {
    nodes, edges: [{src: '1', dst: '2'}, ...ids.slice(2).map(dst => ({src: '2', dst})), ...ids.slice(2,9).map(src => ({src,dst:'2'}))],
    clusters: [{cluster_id: 0, n_nodes: nodes.length, n_seed: 1, sum_kzt_internal: 5000, hypothesis: 'Проверка'}],
    meta: {n_nodes: nodes.length, n_seed: 1, n_edges: 68, n_transactions: 68,
      turnover: 340000, n_clusters: 1, n_multiseed_clusters: 0, n_active_components: 1,
      period: ['2026-07-01', '2026-07-31'], elapsed_seconds: .1, dataset_hash: hash,
      demo: true, roles: {peripheral: 'Периферия'}, colors: {peripheral: '#888', boundary: '#999'},
      limitations: 'Тестовый набор', warnings: []}
  };
}

async function boot() {
  const elements = new Map();
  class Element {
    constructor(tag = 'div') {
      this.tagName = tag.toUpperCase(); this.children = []; this.style = {};
      this.dataset = {}; this.value = ''; this.disabled = false; this.checked = false;
      this.listeners = {}; this.attributes = {}; this.classes = new Set(); this._text = '';
      this.classList = {
        add: (...names) => names.forEach(name => this.classes.add(name)),
        remove: (...names) => names.forEach(name => this.classes.delete(name)),
        toggle: (name, enabled) => {
          const on = enabled === undefined ? !this.classes.has(name) : enabled;
          if (on) this.classes.add(name); else this.classes.delete(name);
          return on;
        }
      };
    }
    set id(value) { this._id = value; elements.set(value, this); }
    get id() { return this._id; }
    set className(value) { this.classes = new Set(value.split(/\s+/)); }
    get className() { return [...this.classes].join(' '); }
    set textContent(value) { this._text = String(value); this.children = []; }
    get textContent() { return this._text + this.children.map(child => child.textContent ?? String(child)).join(''); }
    get firstChild() { return this.children[0]; }
    append(...children) { children.forEach(child => { if (typeof child === 'object') child.parentElement = this; this.children.push(child); }); }
    replaceChildren(...children) { this.children = []; this._text = ''; this.append(...children); }
    remove() { if (this.parentElement) this.parentElement.children = this.parentElement.children.filter(child => child !== this); }
    addEventListener(name, handler) { this.listeners[name] = handler; }
    setAttribute(name, value) { this.attributes[name] = value; }
    removeAttribute(name) { delete this.attributes[name]; }
    querySelector(selector) {
      const name = selector.slice(1);
      for (const child of this.children) {
        if (child.classes?.has(name)) return child;
        const nested = child.querySelector?.(selector);
        if (nested) return nested;
      }
      return null;
    }
    getBoundingClientRect() { return {width: 600, height: 500, left: 0, top: 0}; }
    getContext() { return new Proxy({measureText: text => ({width: text.length * 6})}, {get: (target, name) => target[name] || (() => {})}); }
    setPointerCapture() {}
    scrollIntoView() {}
    focus() {}
    requestSubmit() { this.onsubmit?.({preventDefault() {}}); }
  }
  for (const match of html.matchAll(/<([a-z]+)\b[^>]*\bid="([^"]+)"/g)) {
    const element = new Element(match[1]); element.id = match[2];
  }
  const tabs = ['network', 'clusters', 'method'].map(name => {
    const tab = new Element('button'); tab.dataset.tab = name; return tab;
  });
  const document = {
    body: new Element('body'), getElementById: id => elements.get(id) || null,
    createElement: tag => new Element(tag),
    createTextNode: text => { const node = new Element('#text'); node.textContent = text; return node; },
    querySelectorAll: selector => selector === '.tab' ? tabs : []
  };
  const state = {data: fixture(), chats: [], chatReply: null};
  const response = body => ({ok: true, status: 200, json: async () => body});
  const context = vm.createContext({
    document, window: {devicePixelRatio: 1}, console,
    requestAnimationFrame: callback => callback(),
    ResizeObserver: class { observe() {} },
    fetch: async (url, options) => {
      if (url === '/api/data') return response(state.data);
      if (url === '/api/status') return response({configured: false, calls: 0, max_calls: 40});
      if (url.startsWith('/api/node?')) {
        const gid = new URL('http://localhost' + url).searchParams.get('gid');
        return response({node: state.data.nodes.find(node => node.gid === gid), seed_path: ['1', gid]});
      }
      if (url === '/api/chat') {
        const body = JSON.parse(options.body); state.chats.push(body);
        return response(await state.chatReply(body));
      }
      if (url === '/api/upload') { state.data = fixture('dataset-b'); return response({}); }
      throw new Error('Unexpected request: ' + url);
    }
  });
  vm.runInContext(source, context, {filename: 'app.js'});
  for (let i = 0; i < 4; i++) await tick();
  return {elements, state, run: code => vm.runInContext(code, context)};
}

const reply = (text = 'Ответ по данным', hash = 'dataset-a') => ({
  mode: 'local', text, references: [], results: [], dataset_hash: hash
});

(async () => {
  const app = await boot();
  const {run, elements, state} = app;

  run('focus=true;filterGraph();fit()');
  const initialScale = run('view.scale');
  await run('selectNode("2")');
  assert.equal(run('visible.length'), 76);
  assert.ok(run('view.scale') < initialScale / 3, 'larger neighborhood must be reframed');
  assert.ok(run('visible.every(n=>{const p=pos(n);return p.x>=0&&p.x<=graphSize.w&&p.y>=0&&p.y<=graphSize.h})'), 'every neighbor must fit inside the canvas');
  assert.ok(run(`visible.every((a,i)=>visible.slice(i+1).every(b=>{
    const p=pos(a),q=pos(b);
    return Math.hypot(p.x-q.x,p.y-q.y)>radius(a)+radius(b)+2;
  }))`), 'the 76-node hub must have a visible gap between every pair of disks');
  assert.ok(run('new Set([...localPositions.values()].filter(p=>p.x>0).map(p=>p.x)).size>1'), 'large outgoing groups must use multiple columns');
  assert.ok(run('localPositions.get("1").x<0&&localPositions.get("10").x>0'), 'incoming and outgoing groups must remain on opposite sides');
  run('graphSize={w:420,h:420};layoutNeighborhood();fit()');
  assert.ok(run(`visible.every((a,i)=>visible.slice(i+1).every(b=>{
    const p=pos(a),q=pos(b);
    return Math.hypot(p.x-q.x,p.y-q.y)>radius(a)+radius(b)+2;
  }))`), 'disks must remain separated in a narrow desktop graph panel');

  elements.get('fit').onclick();
  assert.equal(run('focus'), true, 'fit must preserve neighborhood mode');
  elements.get('role-filter').value = 'peripheral';
  elements.get('cluster-filter').value = '0';
  elements.get('reset-network').onclick();
  assert.equal(run('focus'), false);
  assert.equal(elements.get('role-filter').value, '');
  assert.equal(elements.get('cluster-filter').value, '');

  const waiting = deferred(); state.chatReply = () => waiting.promise;
  const pending = run('ask("Первый вопрос")');
  elements.get('question').value = 'Следующий вопрос';
  elements.get('question').onkeydown({key: 'Enter', shiftKey: false, preventDefault() {}});
  assert.equal(elements.get('question').value, 'Следующий вопрос', 'Enter while busy must preserve the draft');
  assert.equal(state.chats.length, 1, 'busy form must not issue a second request');
  waiting.resolve(reply()); await pending;

  state.chatReply = body => reply('Проверенный ответ: ' + body.question);
  for (let i = 0; i < 4; i++) await run(`ask(${JSON.stringify('Вопрос ' + i)})`);
  const history = state.chats.at(-1).history;
  assert.equal(history.length, 6, 'send at most three previous pairs');
  assert.deepEqual(history.map(message => message.role), ['user', 'assistant', 'user', 'assistant', 'user', 'assistant']);
  assert.ok(history.every(message => Object.keys(message).length === 2 && message.content.length <= 1000));
  assert.equal(state.chats.at(-1).dataset_hash, 'dataset-a');

  const oldReply = deferred(); state.chatReply = () => oldReply.promise;
  const oldRequest = run('ask("Старый набор")');
  await elements.get('upload').onchange({target: {files: [{}], disabled: false, value: 'data.zip'}});
  assert.equal(run('chatHistory.length'), 0);
  const newReply = deferred(); state.chatReply = () => newReply.promise;
  const newRequest = run('ask("Новый набор")');
  oldReply.resolve(reply('СТАРЫЙ ОТВЕТ')); await oldRequest;
  assert.equal(run('chatBusy'), true, 'a stale response must not unlock a newer request');
  assert.equal(run('chatHistory.length'), 0, 'a stale response must not enter history');
  assert.ok(!elements.get('chat').textContent.includes('СТАРЫЙ ОТВЕТ'));
  newReply.resolve(reply('НОВЫЙ ОТВЕТ', 'dataset-b')); await newRequest;
  assert.equal(run('chatHistory.length'), 2);
  assert.ok(elements.get('chat').textContent.includes('НОВЫЙ ОТВЕТ'));
  assert.deepEqual(state.chats.at(-1).history, [], 'history must reset after upload');
  console.log('UI regressions passed: 76-node layout without overlapping disks, neighborhood fit, current-view fit, filter reset, busy Enter, bounded history, stale upload replies.');
})().catch(error => { console.error(error); process.exitCode = 1; });
