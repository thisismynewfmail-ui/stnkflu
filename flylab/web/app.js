/* FLYLAB - core: state, transport, routing, tooltips, small components.
   No build step and no external libraries: the file the browser runs is the
   file in the repository. */

const FL = (window.FL = {
  state: {},
  catalogue: null,
  blocks: {},
  channels: {},
  templates: [],
  page: 'overview',
  project: null,
  workflow: null,
  problems: [],
  runEvents: [],
  live: { activity: [], viewport: null, displays: {}, goal: null, iteration: 0, nodeMs: {} },
  socket: null,
  dirty: false,
  tipsOn: true,
});

/* ------------------------------------------------------------------ utils */
const el = (tag, attrs = {}, ...kids) => {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'class') node.className = v;
    else if (k === 'html') node.innerHTML = v;
    else if (k === 'text') node.textContent = v;
    else if (k.startsWith('on') && typeof v === 'function') node.addEventListener(k.slice(2), v);
    else if (k === 'tip') FL.tipify(node, v);
    else node.setAttribute(k, v === true ? '' : v);
  }
  for (const kid of kids.flat(3)) {
    if (kid === null || kid === undefined || kid === false) continue;
    node.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return node;
};
FL.el = el;
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
FL.$ = $; FL.$$ = $$;

FL.num = (v, digits = 2) => {
  if (v === null || v === undefined || v === '' || Number.isNaN(Number(v))) return '--';
  const n = Number(v);
  if (!Number.isFinite(n)) return '--';
  if (Math.abs(n) >= 10000) return n.toLocaleString(undefined, { maximumFractionDigits: 0 });
  return n.toFixed(digits).replace(/\.0+$/, '');
};
FL.bytes = (n) => {
  if (!n) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.min(units.length - 1, Math.floor(Math.log(n) / Math.log(1024)));
  return `${(n / 1024 ** i).toFixed(i ? 1 : 0)} ${units[i]}`;
};
FL.ago = (t) => {
  if (!t) return 'never';
  const s = Math.max(0, Date.now() / 1000 - t);
  if (s < 60) return `${Math.round(s)}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
};
FL.clock = (t) => new Date((t || 0) * 1000).toLocaleString();

/* --------------------------------------------------------------- transport */
FL.api = async (path, options = {}) => {
  const key = new URLSearchParams(location.search).get('key');
  const url = key ? `${path}${path.includes('?') ? '&' : '?'}key=${encodeURIComponent(key)}` : path;
  const init = { method: options.method || (options.body ? 'POST' : 'GET'), headers: {} };
  if (options.body !== undefined) {
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(options.body);
  }
  const response = await fetch(url, init);
  const text = await response.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = { detail: text }; }
  if (!response.ok) {
    const message = (data && (data.detail || data.error)) || `${response.status} ${response.statusText}`;
    const error = new Error(typeof message === 'string' ? message : JSON.stringify(message));
    error.status = response.status;
    error.payload = data;
    throw error;
  }
  return data;
};

FL.connect = () => {
  const key = new URLSearchParams(location.search).get('key');
  const scheme = location.protocol === 'https:' ? 'wss' : 'ws';
  const url = `${scheme}://${location.host}/ws${key ? `?key=${encodeURIComponent(key)}` : ''}`;
  let socket;
  try { socket = new WebSocket(url); } catch { return; }
  FL.socket = socket;
  socket.onopen = () => FL.setLink(true);
  socket.onclose = () => { FL.setLink(false); setTimeout(FL.connect, 2500); };
  socket.onerror = () => FL.setLink(false);
  socket.onmessage = (event) => {
    let payload;
    try { payload = JSON.parse(event.data); } catch { return; }
    FL.onEvent(payload);
  };
  setInterval(() => { if (socket.readyState === 1) socket.send('ping'); }, 25000);
};

FL.setLink = (up) => {
  FL.linked = up;
  const lamp = $('#link-lamp');
  if (lamp) lamp.className = `lamp ${up ? 'ok' : 'bad'}`;
  const label = $('#link-text');
  if (label) label.textContent = up ? 'LINKED' : 'NO LINK';
};

/* ------------------------------------------------------------------ events */
FL.onEvent = (event) => {
  switch (event.kind) {
    case 'hello':
      FL.applyState(event.state);
      break;
    case 'settings':
      FL.state.settings = event.settings;
      FL.applyTheme();
      break;
    case 'session':
      FL.state.session = event.session;
      FL.state.session_loaded = true;
      FL.refreshStatus();
      if (FL.page === 'connectome') FL.views.connectome.load();
      break;
    case 'task':
      FL.state.task = event;
      FL.refreshStatus();
      if (FL.page === 'housekeeping') FL.views.housekeeping.progress(event);
      break;
    case 'progress':
      if (FL.page === 'housekeeping') FL.views.housekeeping.detail(event.detail);
      break;
    case 'started':
      FL.runEvents = [];
      FL.live.nodeMs = {};
      FL.log('run', `Run ${event.state.run_id} started`, 'good');
      FL.refreshStatus();
      break;
    case 'iteration':
      FL.live.iteration = event.iteration;
      FL.live.goal = event.goal;
      FL.editor && FL.editor.markIteration(event.iteration);
      FL.views.run && FL.views.run.update();
      break;
    case 'block':
      FL.live.nodeMs[event.node] = event.ms;
      FL.editor && FL.editor.flash(event.node, event.record);
      FL.log(event.label || event.name, FL.describeRecord(event.record));
      break;
    case 'block_error':
      FL.editor && FL.editor.markError(event.error.node, event.error.message);
      FL.log(event.error.name, event.error.message, 'err');
      break;
    case 'neural':
      FL.live.viewport = event.viewport;
      FL.live.activity = event.channels || [];
      FL.live.step = event.step;
      FL.viewport && FL.viewport.push(event.viewport);
      FL.views.run && FL.views.run.neural(event);
      break;
    case 'display':
      FL.live.displays = event.panels;
      FL.views.run && FL.views.run.displays(event.panels);
      break;
    case 'log':
      FL.log(event.label || 'record', FL.describeRecord(event));
      break;
    case 'model':
      FL.toast('Model saved', event.model.label, 'good');
      break;
    case 'check':
      FL.problems = event.problems || [];
      FL.editor && FL.editor.showProblems(FL.problems);
      break;
    case 'finished':
      FL.log('run', `Finished: ${event.state.detail || event.state.reason}`,
        event.state.status === 'failed' ? 'err' : 'good');
      FL.toast('Run finished', event.state.detail || event.state.reason,
        event.state.status === 'failed' ? 'bad' : 'good');
      FL.live.goal = event.goal;
      FL.refresh();
      break;
    case 'run_error':
      FL.log('run', event.message, 'err');
      FL.toast('Run failed', event.message, 'bad');
      break;
    case 'dataset':
      FL.toast('Dataset ready', `${event.report.neurons.toLocaleString()} cells`, 'good');
      FL.refresh();
      break;
    default:
      break;
  }
};

FL.describeRecord = (record) => {
  if (record === null || record === undefined) return '';
  if (typeof record !== 'object') return String(record);
  const parts = [];
  for (const [k, v] of Object.entries(record)) {
    if (v === null || v === undefined || v === '' || k === 'at') continue;
    if (typeof v === 'object') {
      if (Array.isArray(v)) parts.push(`${k}=[${v.length}]`);
      continue;
    }
    parts.push(`${k}=${typeof v === 'number' ? FL.num(v, 3) : v}`);
    if (parts.length >= 6) break;
  }
  return parts.join('  ');
};

/* ------------------------------------------------------------------ console */
FL.log = (source, message, kind = '') => {
  if (!message) return;
  const box = $('#console .lines');
  if (!box) return;
  const line = el('div', { class: `ln ${kind}` },
    el('span', { class: 't', text: new Date().toLocaleTimeString([], { hour12: false }) }),
    el('span', { class: 's', text: String(source).slice(0, 40) }),
    el('span', { class: 'm', text: String(message).slice(0, 400) }));
  box.append(line);
  while (box.childElementCount > 400) box.firstElementChild.remove();
  if (!FL.consolePinned) box.scrollTop = box.scrollHeight;
};

FL.toast = (title, body = '', kind = '') => {
  const node = el('div', { class: `toast ${kind}` },
    el('div', { class: 'tt', text: title }),
    body ? el('div', { text: String(body).slice(0, 260) }) : null);
  $('#toasts').append(node);
  setTimeout(() => { node.style.opacity = '0'; setTimeout(() => node.remove(), 300); }, kind === 'bad' ? 8000 : 4200);
};

/* ----------------------------------------------------------------- tooltips */
FL.tipify = (node, spec) => {
  if (!spec) return node;
  if (typeof spec === 'string') node.dataset.tip = spec;
  else {
    if (spec.title) node.dataset.tipTitle = spec.title;
    if (spec.body) node.dataset.tip = spec.body;
    if (spec.extra) node.dataset.tipExtra = spec.extra;
    if (spec.meta) node.dataset.tipMeta = spec.meta;
    if (spec.caution) node.dataset.tipCaution = spec.caution;
  }
  return node;
};

FL.initTips = () => {
  const tip = $('#tip');
  let timer = null;
  const hide = () => { clearTimeout(timer); tip.classList.remove('on'); };
  document.addEventListener('mouseover', (event) => {
    if (!FL.tipsOn) return;
    const target = event.target.closest('[data-tip], [data-tip-title]');
    if (!target) { hide(); return; }
    clearTimeout(timer);
    timer = setTimeout(() => {
      tip.innerHTML = '';
      if (target.dataset.tipTitle) tip.append(el('div', { class: 't-title', text: target.dataset.tipTitle }));
      if (target.dataset.tip) tip.append(el('div', { class: 't-body', text: target.dataset.tip }));
      if (target.dataset.tipExtra) tip.append(el('div', { class: 't-extra', text: target.dataset.tipExtra }));
      if (target.dataset.tipCaution) tip.append(el('div', { class: 't-caution', text: `Caution: ${target.dataset.tipCaution}` }));
      if (target.dataset.tipMeta) tip.append(el('div', { class: 't-meta', text: target.dataset.tipMeta }));
      const box = target.getBoundingClientRect();
      tip.classList.add('on');
      const size = tip.getBoundingClientRect();
      let left = box.left;
      let top = box.bottom + 8;
      if (left + size.width > innerWidth - 12) left = Math.max(12, innerWidth - size.width - 12);
      if (top + size.height > innerHeight - 12) top = Math.max(12, box.top - size.height - 8);
      tip.style.left = `${left}px`;
      tip.style.top = `${top}px`;
    }, 190);
  });
  document.addEventListener('mouseout', hide);
  document.addEventListener('mousedown', hide);
  window.addEventListener('scroll', hide, true);
};

/* -------------------------------------------------------------------- modal */
FL.modal = ({ title, body, actions = [], width }) => new Promise((resolve) => {
  const back = $('#modal-back');
  const box = $('#modal');
  if (width) box.style.width = `min(${width}px, 100%)`;
  const close = (value) => { back.classList.remove('on'); box.innerHTML = ''; resolve(value); };
  box.innerHTML = '';
  box.append(
    el('header', {}, el('h2', { text: title }),
      el('button', { class: 'ghost small', text: 'Close', onclick: () => close(null) })),
    el('div', { class: 'mbody' }, body),
    el('div', { class: 'mfoot' }, actions.map((action) => el('button', {
      class: action.kind || '', text: action.label, tip: action.tip,
      onclick: async () => {
        if (action.run) {
          const value = await action.run(box);
          if (value === false) return;
          close(value);
        } else close(action.value ?? true);
      },
    }))));
  back.classList.add('on');
  back.onclick = (event) => { if (event.target === back) close(null); };
});

FL.confirm = (title, message, confirmLabel = 'Confirm', kind = 'danger') =>
  FL.modal({
    title,
    body: el('div', {}, el('p', { text: message })),
    actions: [
      { label: 'Cancel', kind: 'ghost', value: false },
      { label: confirmLabel, kind, value: true },
    ],
  });

/* ------------------------------------------------------------------ fields */
FL.field = (spec) => {
  const { label, plain, tip, kind = 'text', value, options = [], min, max, step, unit,
    placeholder, onchange, disabled } = spec;
  let input;
  if (kind === 'select') {
    input = el('select', { disabled });
    for (const option of options) {
      const opt = el('option', { value: option.value, text: option.label });
      if (String(option.value) === String(value)) opt.selected = true;
      if (option.plain) opt.title = option.plain;
      input.append(opt);
    }
    input.onchange = () => onchange && onchange(coerce(input.value, options));
  } else if (kind === 'bool') {
    const check = el('input', { type: 'checkbox', disabled });
    check.checked = !!value;
    check.onchange = () => onchange && onchange(check.checked);
    input = el('label', { class: 'switch' }, check, el('span', { class: 'track' }),
      el('span', { class: 'txt', text: plain || '' }));
  } else if (kind === 'code') {
    input = el('textarea', { placeholder: placeholder || '', disabled });
    input.value = value ?? '';
    input.onchange = () => onchange && onchange(input.value);
  } else if (kind === 'range') {
    input = el('input', { type: 'range', min, max, step, disabled });
    input.value = value ?? 0;
    input.oninput = () => onchange && onchange(Number(input.value));
  } else {
    const type = (kind === 'number') ? 'number' : 'text';
    input = el('input', { type, placeholder: placeholder || '', min, max, step, disabled });
    input.value = value ?? '';
    input.onchange = () => onchange && onchange(type === 'number' ? Number(input.value) : input.value);
  }
  const labelNode = el('label', { class: 'field' },
    kind === 'bool' ? null : el('span', { class: 'lbl' },
      label, unit ? el('span', { style: 'color:var(--dim)', text: `(${unit})` }) : null),
    input,
    kind !== 'bool' && plain ? el('span', { class: 'hint', text: plain }) : null);
  FL.tipify(labelNode, { title: label, body: plain, extra: tip });
  return labelNode;

  function coerce(raw, opts) {
    const match = opts.find((o) => String(o.value) === raw);
    return match ? match.value : raw;
  }
};

FL.metric = (label, value, sub, kind = '') => el('div', { class: `metric ${kind}` },
  el('div', { class: 'label', text: label }),
  el('div', { class: `value${String(value).length > 9 ? ' small' : ''}`, text: value }),
  sub ? el('div', { class: 'sub', text: sub }) : null);

FL.panel = (title, body, opts = {}) => el('section', { class: `panel ${opts.accent || ''}`, tip: opts.tip },
  el('header', {}, el('h2', { text: title }),
    opts.tag ? el('span', { class: 'tag', text: opts.tag }) : null,
    ...(opts.actions || [])),
  el('div', { class: `body ${opts.pad0 ? 'pad0' : ''}` }, body));

FL.meter = (name, value, max, colour = '') => {
  const width = Math.max(0, Math.min(100, (Number(value) / (max || 1)) * 100));
  return el('div', { class: 'meter' },
    el('div', { class: 'top' }, el('span', { class: 'n', text: name }),
      el('span', { class: 'v', text: FL.num(value, 1) })),
    el('div', { class: 'bar' }, el('i', { class: colour, style: `width:${width}%` })));
};

/* ------------------------------------------------------------------ routing */
FL.PAGES = [
  { key: 'overview', label: 'Status', icon: 'M3 12h4l2 6 4-16 2 10h6',
    title: 'Status', tip: 'What is loaded, what this machine can reach, and what happened most recently.' },
  { key: 'projects', label: 'Projects', icon: 'M3 7h6l2 2h10v11H3z',
    title: 'Projects', tip: 'Saved work: a workflow, the settings it runs with, and the model it resumes from. Open one to pick it back up.' },
  { key: 'editor', label: 'Workflow', icon: 'M5 6h5v5H5zM14 13h5v5h-5zM10 8.5h4v7',
    title: 'Workflow', tip: 'The node canvas. Build what the network does: what it senses, how long it runs, what it decides, what it drives.' },
  { key: 'connectome', label: 'Network', icon: 'M12 3v4M12 17v4M3 12h4M17 12h4M6 6l3 3M18 6l-3 3M6 18l3-3M18 18l-3-3',
    title: 'Network', tip: 'Live view of the loaded connectome, with every input, output and modulatory channel it contains.' },
  { key: 'bench', label: 'Bench', icon: 'M4 19h16M7 19V9M17 19V5M12 19v-8',
    title: 'Bench', tip: 'Set up, debug and tune. Drive one channel, read another, and check that hardware responds - without running a whole workflow.' },
  { key: 'training', label: 'Training', icon: 'M4 18l5-6 4 4 7-9M4 6h4',
    title: 'Training', tip: 'Train and retrain: schedules, the frozen-synapse control, and comparison between runs.' },
  { key: 'models', label: 'Models', icon: 'M12 3l8 4.5v9L12 21l-8-4.5v-9z M12 12l8-4.5M12 12v9M12 12L4 7.5',
    title: 'Model library', tip: 'Saved networks. Label them, write down what they were trained on, load one back.' },
  { key: 'housekeeping', label: 'Files', icon: 'M4 6h16v13H4zM4 6l2-3h5l2 3',
    title: 'Housekeeping', tip: 'Download and verify the connectome, build the bench fixture, check dependencies, and clear space.' },
  { key: 'settings', label: 'Settings', icon: 'M12 9a3 3 0 100 6 3 3 0 000-6zM4 12h2M18 12h2M12 4v2M12 18v2',
    title: 'Settings', tip: 'Defaults for new sessions and runs, interface options, and who is allowed to reach this interface.' },
];

FL.go = (key) => {
  if (!FL.PAGES.some((page) => page.key === key)) key = 'overview';
  FL.page = key;
  location.hash = key;
  $$('#rail-buttons .rail-btn').forEach((button) =>
    button.classList.toggle('on', button.dataset.page === key));
  $$('.page').forEach((page) => page.classList.toggle('on', page.id === `page-${key}`));
  const view = FL.views[key];
  if (view && view.show) view.show();
};

/* ------------------------------------------------------------------- state */
FL.applyState = (state) => {
  FL.state = state || {};
  FL.tipsOn = FL.state.settings ? FL.state.settings.show_tips !== false : true;
  FL.applyTheme();
  FL.refreshStatus();
};

FL.applyTheme = () => {
  const settings = FL.state.settings || {};
  document.body.classList.toggle('reduce-motion', !!settings.reduce_motion);
  FL.tipsOn = settings.show_tips !== false;
};

FL.refresh = async () => {
  try {
    FL.applyState(await FL.api('/api/state'));
    const view = FL.views[FL.page];
    if (view && view.show) view.show();
  } catch (error) { FL.log('state', error.message, 'err'); }
};

FL.refreshStatus = () => {
  const state = FL.state || {};
  const session = state.session || {};
  const dataset = session.dataset || {};
  const run = state.run || {};
  const fixture = !!(session.synthetic_fixture || dataset.synthetic_fixture);
  $('#fixture-banner').classList.toggle('on', fixture);
  const stats = $('#topbar-stats');
  if (!stats) return;
  stats.innerHTML = '';
  stats.append(
    FL.tipify(el('div', { class: 'stat' },
      el('span', { class: `lamp ${state.session_loaded ? 'ok' : 'warn'}` }),
      state.session_loaded
        ? el('span', {}, el('b', { text: (dataset.neurons || 0).toLocaleString() }), ' cells')
        : el('span', { text: 'no network loaded' })),
      { title: 'Loaded network',
        body: state.session_loaded
          ? `${dataset.release || 'dataset'}: ${(dataset.neurons || 0).toLocaleString()} cells, ${(dataset.directed_edges || 0).toLocaleString()} connections.`
          : 'No connectome is loaded. Housekeeping has the download; the Status page has the Load button.',
        meta: dataset.root || '' }),
    FL.tipify(el('div', { class: 'stat' },
      el('span', { class: `lamp ${run.active ? 'live' : ''}`, id: 'run-lamp' }),
      el('span', { id: 'run-text', text: run.active ? `RUN ${run.state ? run.state.iteration : 0}` : 'IDLE' })),
      { title: 'Run state',
        body: run.active ? 'A workflow is running. The Workflow page shows which block is active.'
          : 'Nothing is running. Open a project and press Run.' }),
    FL.tipify(el('div', { class: 'stat' },
      el('span', { class: 'lamp', id: 'link-lamp' }),
      el('span', { id: 'link-text', text: 'LINK' })),
      { title: 'Live connection',
        body: 'The browser holds an open connection for telemetry. If it drops, the interface reconnects on its own.' }),
    FL.tipify(el('div', { class: 'stat', onclick: () => FL.go('settings'), style: 'cursor:pointer' },
      el('span', { class: `lamp ${(state.settings || {}).lan_enabled ? 'warn' : 'ok'}` }),
      el('span', { text: (state.settings || {}).lan_enabled ? 'SHARED ON NETWORK' : 'LOCAL ONLY' })),
      { title: 'Who can reach this interface',
        body: (state.settings || {}).lan_enabled
          ? ((state.settings || {}).lan_require_key
            ? 'Shared on this network, with an access key required from other devices.'
            : 'Shared on this network with no access key. Anyone who can reach this machine can drive its GPIO pins, move its pointer and start programs on it.')
          : 'Local only. Nothing outside this machine can connect.',
        extra: (state.settings || {}).share_url
          ? `Open it elsewhere at ${(state.settings || {}).share_url}`
          : '',
        meta: 'Click to open Settings' }),
  );
  FL.setLink(!!FL.linked);
};

/* -------------------------------------------------------------------- boot */
FL.boot = async () => {
  FL.initTips();
  const rail = $('#rail-buttons');
  for (const page of FL.PAGES) {
    rail.append(FL.tipify(el('button', {
      class: 'rail-btn', 'data-page': page.key, onclick: () => FL.go(page.key),
    }, el('span', { html: `<svg viewBox="0 0 24 24"><path d="${page.icon}"/></svg>` }), page.label),
      { title: page.title, body: page.tip }));
  }
  $('#rail-help').onclick = () => FL.views.overview.helpModal();
  const pages = $('#pages');
  for (const page of FL.PAGES) {
    pages.append(el('section', { class: `page${page.key === 'editor' ? ' flush' : ''}`, id: `page-${page.key}` }));
  }
  try {
    const [state, catalogue] = await Promise.all([FL.api('/api/state'), FL.api('/api/catalogue')]);
    FL.catalogue = catalogue;
    FL.blockCat = catalogue.blocks;
    for (const block of catalogue.blocks.blocks) FL.blocks[block.key] = block;
    for (const channel of catalogue.blocks.channels) FL.channels[channel.key] = channel;
    FL.templates = catalogue.templates;
    FL.applyState(state);
  } catch (error) {
    document.body.innerHTML =
      `<div style="padding:40px;font-family:var(--mono);color:#ff5d73">` +
      `<h1 style="font-family:var(--stencil)">FLYLAB could not start</h1>` +
      `<p>${error.message}</p>` +
      `<p style="color:#9db0bd">If you are connecting from another device, local-network access has to be` +
      ` turned on in Settings on the machine running FLYLAB, and the address needs the access key it shows.</p></div>`;
    return;
  }
  FL.views.init();
  FL.connect();
  FL.go((location.hash || '#overview').slice(1));
  window.addEventListener('hashchange', () => FL.go(location.hash.slice(1)));
  window.addEventListener('beforeunload', (event) => {
    if (FL.dirty) { event.preventDefault(); event.returnValue = ''; }
  });
  setInterval(() => { if (!document.hidden) FL.refreshStatus(); }, 5000);
};
