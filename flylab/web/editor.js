/* FLYLAB - the node canvas.
   Two kinds of wire: run order (white, arrow pins) and values (coloured, round
   pins). Dragging from any pin starts a wire; dropping on a compatible pin
   finishes it. */

const Editor = (FL.editor = {
  nodes: {}, wires: {}, selected: null, scale: 1, panX: 40, panY: 40,
  drag: null, wiring: null, history: [], future: [], name: 'Untitled workflow',
  meta: {},
});

const TYPE_COLOUR = () => (FL.blockCat ? FL.blockCat.types : {});
const SVG_NS = 'http://www.w3.org/2000/svg';

/* document.createElement('svg') makes an unknown HTML element whose SVG
   children never paint. The wire layer has to be built in the SVG namespace. */
const svgNode = (tag, attrs = {}) => {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  return node;
};

Editor.mount = (root) => {
  root.innerHTML = '';
  root.append(el('div', { id: 'editor-layout' },
    el('aside', { id: 'palette' },
      el('div', { class: 'side-head' }, el('h2', { text: 'Blocks' }),
        FL.tipify(el('input', {
          id: 'pal-search', placeholder: 'search', style: 'width:96px',
          oninput: (e) => Editor.filterPalette(e.target.value),
        }), { title: 'Find a block', body: 'Type part of a name, or what you want to do - "pin", "camera", "reward", "loop".' })),
      el('div', { class: 'side-scroll', id: 'pal-list' })),
    el('div', { id: 'canvas-wrap' },
      el('canvas', { id: 'grid-bg' }),
      svgNode('svg', { id: 'wires' }),
      el('div', { id: 'nodes' }),
      el('div', { id: 'problems' }),
      el('div', { id: 'run-strip' }),
      el('canvas', { id: 'minimap', width: 170, height: 110 }),
      el('div', { id: 'canvas-tools' })),
    el('aside', { id: 'inspector' },
      el('div', { class: 'side-head' }, el('h2', { text: 'Block settings' }),
        FL.tipify(el('button', {
          class: 'ghost small', id: 'adv-toggle', text: 'Advanced',
          onclick: () => { $('#inspector').classList.toggle('showing-advanced'); },
        }), { title: 'Advanced settings', body: 'Show the settings most workflows never need to change: timings, capture sizes, drive strengths.' })),
      el('div', { class: 'side-scroll', id: 'insp-body' }))));
  root.append(el('div', { id: 'console' },
    el('div', { class: 'ch' }, el('h3', { text: 'Run log' }),
      FL.tipify(el('button', {
        class: 'ghost small', text: 'Pin', onclick: (e) => {
          FL.consolePinned = !FL.consolePinned;
          e.target.classList.toggle('primary', FL.consolePinned);
        },
      }), { title: 'Stop auto-scrolling', body: 'Holds the log still so you can read something that has already gone past.' }),
      FL.tipify(el('button', {
        class: 'ghost small', text: 'Clear',
        onclick: () => { $('#console .lines').innerHTML = ''; },
      }), { title: 'Clear the log view', body: 'Only clears what is on screen. The run\'s own log file is untouched.' })),
    FL.tipify(el('div', { id: 'displays' }), {
      title: 'Live readouts',
      body: 'Every "Show on the dashboard" block in this workflow appears here while a run is going.',
    }),
    el('div', { class: 'lines' })));
  Editor.buildPalette();
  Editor.buildTools();
  Editor.bindCanvas();
  Editor.draw();
};

/* ------------------------------------------------------------------ palette */
Editor.buildPalette = () => {
  const list = $('#pal-list');
  list.innerHTML = '';
  const categories = FL.blockCat.categories;
  for (const category of categories) {
    const items = FL.blockCat.blocks.filter((b) => b.category === category.key);
    if (!items.length) continue;
    const group = el('div', { class: `pal-group${['flow', 'source', 'vision'].includes(category.key) ? ' open' : ''}`, 'data-cat': category.key },
      FL.tipify(el('div', { class: 'head', onclick: (e) => e.currentTarget.parentElement.classList.toggle('open') },
        el('span', { class: 'swatch', style: `background:${category.accent}` }),
        category.label,
        el('span', { class: 'count', text: items.length })),
        { title: category.label, body: category.plain }),
      el('div', { class: 'items' }, items.map((block) => Editor.paletteItem(block))));
    list.append(group);
  }
};

Editor.paletteItem = (block) => {
  const colour = (FL.blockCat.categories.find((c) => c.key === block.category) || {}).accent || '#888';
  const node = el('div', {
    class: 'pal-item', draggable: 'true', 'data-key': block.key,
    'data-search': `${block.key} ${block.name} ${block.plain} ${block.summary}`.toLowerCase(),
    ondragstart: (e) => e.dataTransfer.setData('text/flylab', block.key),
    ondblclick: () => Editor.addNode(block.key, 120 - Editor.panX, 120 - Editor.panY),
  }, el('span', { class: 'dot', style: `background:${colour}` }),
    el('span', { class: 'nm', text: block.name }));
  node.append(el('span', { class: 'pl', text: block.plain }));
  return FL.tipify(node, {
    title: block.name,
    body: block.plain,
    extra: block.summary,
    caution: block.caution,
    meta: [
      block.inputs.length ? `${block.inputs.length} in` : null,
      block.outputs.length ? `${block.outputs.length} out` : null,
      block.needs.length ? `needs ${block.needs.join(', ')}` : null,
      'drag onto the canvas',
    ].filter(Boolean).join(' · '),
  });
};

Editor.filterPalette = (query) => {
  const text = query.trim().toLowerCase();
  $$('#pal-list .pal-group').forEach((group) => {
    let visible = 0;
    $$('.pal-item', group).forEach((item) => {
      const match = !text || item.dataset.search.includes(text);
      item.style.display = match ? '' : 'none';
      if (match) visible += 1;
    });
    group.style.display = visible ? '' : 'none';
    if (text) group.classList.add('open');
  });
};

/* -------------------------------------------------------------------- tools */
Editor.buildTools = () => {
  const tools = $('#canvas-tools');
  tools.innerHTML = '';
  const button = (label, tip, handler, kind = 'ghost') =>
    FL.tipify(el('button', { class: `${kind} small`, text: label, onclick: handler }), tip);
  tools.append(
    button('Fit', { title: 'Fit to view', body: 'Zoom and pan so every block is on screen.' }, () => Editor.fit()),
    button('+', { title: 'Zoom in', body: 'Also: scroll wheel over the canvas.' }, () => Editor.zoom(1.2)),
    button('-', { title: 'Zoom out', body: 'Also: scroll wheel over the canvas.' }, () => Editor.zoom(1 / 1.2)),
    button('Tidy', { title: 'Tidy up', body: 'Snap every block onto the grid, without changing any wiring.' }, () => Editor.tidy()),
    button('Undo', { title: 'Undo', body: 'Steps back through canvas edits. Ctrl+Z does the same.' }, () => Editor.undo()),
    button('Redo', { title: 'Redo', body: 'Steps forward again. Ctrl+Shift+Z does the same.' }, () => Editor.redo()),
  );
  const strip = $('#run-strip');
  strip.innerHTML = '';
  strip.append(
    FL.tipify(el('span', { class: 'chip', id: 'wf-name', text: Editor.name }),
      { title: 'Open project', body: 'The workflow on the canvas. Projects holds the rest of your saved work.' }),
    button('Check', { title: 'Check the workflow', body: 'Looks for missing connections, impossible wires, loops with no exit and hardware this machine does not have. Problems appear top-left; click one to jump to the block.' }, () => Editor.check()),
    button('Save', { title: 'Save', body: 'Writes the workflow into the open project. Ctrl+S does the same.' }, () => Editor.save(), 'primary'),
    button('Run', { title: 'Run this workflow', body: 'Checks it, then starts. Blocks light up as they run and the log fills in underneath.' }, () => FL.views.editor.runNow(), 'go'),
    button('Stop', { title: 'Stop the run', body: 'Ends the run at the next block boundary. Whatever has been saved stays saved.' }, () => FL.api('/api/run/stop', { body: {} }), 'danger'),
  );
};

/* ------------------------------------------------------------------- canvas */
Editor.bindCanvas = () => {
  const wrap = $('#canvas-wrap');
  wrap.addEventListener('wheel', (event) => {
    event.preventDefault();
    const rect = wrap.getBoundingClientRect();
    const factor = event.deltaY < 0 ? 1.1 : 1 / 1.1;
    Editor.zoomAt(factor, event.clientX - rect.left, event.clientY - rect.top);
  }, { passive: false });

  wrap.addEventListener('mousedown', (event) => {
    if (event.target.closest('.node') || event.target.closest('#canvas-tools')
      || event.target.closest('#run-strip') || event.target.closest('#problems')) return;
    Editor.select(null);
    const start = { x: event.clientX, y: event.clientY, px: Editor.panX, py: Editor.panY };
    const move = (e) => {
      Editor.panX = start.px + (e.clientX - start.x);
      Editor.panY = start.py + (e.clientY - start.y);
      Editor.applyTransform();
    };
    const up = () => { removeEventListener('mousemove', move); removeEventListener('mouseup', up); };
    addEventListener('mousemove', move); addEventListener('mouseup', up);
  });

  wrap.addEventListener('dragover', (event) => event.preventDefault());
  wrap.addEventListener('drop', (event) => {
    event.preventDefault();
    const key = event.dataTransfer.getData('text/flylab');
    if (!key) return;
    const rect = wrap.getBoundingClientRect();
    Editor.addNode(key,
      (event.clientX - rect.left - Editor.panX) / Editor.scale,
      (event.clientY - rect.top - Editor.panY) / Editor.scale);
  });

  addEventListener('mousemove', (event) => {
    if (!Editor.wiring) return;
    const rect = $('#canvas-wrap').getBoundingClientRect();
    Editor.wiring.x = (event.clientX - rect.left - Editor.panX) / Editor.scale;
    Editor.wiring.y = (event.clientY - rect.top - Editor.panY) / Editor.scale;
    Editor.drawWires();
  });
  addEventListener('mouseup', () => {
    if (Editor.wiring) { Editor.wiring = null; Editor.drawWires(); }
  });
  addEventListener('keydown', (event) => {
    if (FL.page !== 'editor') return;
    const typing = ['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement.tagName);
    if (typing) return;
    if ((event.key === 'Delete' || event.key === 'Backspace') && Editor.selected) {
      event.preventDefault(); Editor.removeNode(Editor.selected);
    }
    if (event.key.toLowerCase() === 'z' && (event.ctrlKey || event.metaKey)) {
      event.preventDefault(); event.shiftKey ? Editor.redo() : Editor.undo();
    }
    if (event.key.toLowerCase() === 's' && (event.ctrlKey || event.metaKey)) {
      event.preventDefault(); Editor.save();
    }
    if (event.key.toLowerCase() === 'd' && (event.ctrlKey || event.metaKey) && Editor.selected) {
      event.preventDefault(); Editor.duplicate(Editor.selected);
    }
  });
};

Editor.applyTransform = () => {
  const transform = `translate(${Editor.panX}px, ${Editor.panY}px) scale(${Editor.scale})`;
  $('#nodes').style.transform = transform;
  $('#wires').style.transform = transform;
  Editor.drawGrid();
  Editor.drawMinimap();
};

Editor.zoom = (factor) => {
  const wrap = $('#canvas-wrap').getBoundingClientRect();
  Editor.zoomAt(factor, wrap.width / 2, wrap.height / 2);
};

Editor.zoomAt = (factor, cx, cy) => {
  const next = Math.max(0.28, Math.min(2.2, Editor.scale * factor));
  const ratio = next / Editor.scale;
  Editor.panX = cx - (cx - Editor.panX) * ratio;
  Editor.panY = cy - (cy - Editor.panY) * ratio;
  Editor.scale = next;
  Editor.applyTransform();
};

Editor.fit = () => {
  const list = Object.values(Editor.nodes);
  if (!list.length) { Editor.scale = 1; Editor.panX = 60; Editor.panY = 60; Editor.applyTransform(); return; }
  const wrap = $('#canvas-wrap').getBoundingClientRect();
  if (wrap.width < 40 || wrap.height < 40) {
    // The canvas has no size yet (the page is still hidden). Fitting now would
    // zoom to nothing, so leave it for the next time the page is shown.
    Editor.needsFit = true;
    return;
  }
  Editor.needsFit = false;
  const minX = Math.min(...list.map((n) => n.x)) - 40;
  const minY = Math.min(...list.map((n) => n.y)) - 40;
  const maxX = Math.max(...list.map((n) => n.x + 230)) + 40;
  const maxY = Math.max(...list.map((n) => n.y + 220)) + 40;
  Editor.scale = Math.max(0.45, Math.min(1.2, Math.min(wrap.width / (maxX - minX), wrap.height / (maxY - minY))));
  Editor.panX = -minX * Editor.scale + 20;
  Editor.panY = -minY * Editor.scale + 20;
  Editor.applyTransform();
};

Editor.drawGrid = () => {
  const canvas = $('#grid-bg');
  const wrap = $('#canvas-wrap').getBoundingClientRect();
  canvas.width = wrap.width; canvas.height = wrap.height;
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  const size = 28 * Editor.scale;
  if (size < 6) return;
  ctx.strokeStyle = '#111a21'; ctx.lineWidth = 1;
  ctx.beginPath();
  for (let x = Editor.panX % size; x < canvas.width; x += size) { ctx.moveTo(x, 0); ctx.lineTo(x, canvas.height); }
  for (let y = Editor.panY % size; y < canvas.height; y += size) { ctx.moveTo(0, y); ctx.lineTo(canvas.width, y); }
  ctx.stroke();
  ctx.strokeStyle = '#18242c';
  ctx.beginPath();
  for (let x = Editor.panX % (size * 5); x < canvas.width; x += size * 5) { ctx.moveTo(x, 0); ctx.lineTo(x, canvas.height); }
  for (let y = Editor.panY % (size * 5); y < canvas.height; y += size * 5) { ctx.moveTo(0, y); ctx.lineTo(canvas.width, y); }
  ctx.stroke();
};

/* -------------------------------------------------------------------- model */
Editor.load = (document_) => {
  Editor.nodes = {}; Editor.wires = {};
  Editor.name = document_.name || 'Untitled workflow';
  Editor.meta = document_.meta || {};
  for (const node of document_.nodes || []) Editor.nodes[node.id] = { ...node };
  for (const wire of document_.wires || []) Editor.wires[wire.id] = { ...wire };
  Editor.history = []; Editor.future = [];
  Editor.selected = null;
  FL.dirty = false;
  if ($('#wf-name')) $('#wf-name').textContent = Editor.name;
  Editor.draw();
  Editor.fit();
};

Editor.document = () => ({
  name: Editor.name,
  nodes: Object.values(Editor.nodes),
  wires: Object.values(Editor.wires),
  meta: Editor.meta,
});

Editor.snapshot = () => {
  Editor.history.push(JSON.stringify(Editor.document()));
  if (Editor.history.length > 60) Editor.history.shift();
  Editor.future = [];
  FL.dirty = true;
};

Editor.undo = () => {
  if (!Editor.history.length) return;
  Editor.future.push(JSON.stringify(Editor.document()));
  const previous = JSON.parse(Editor.history.pop());
  const keep = Editor.history.slice();
  Editor.load(previous);
  Editor.history = keep;
  FL.dirty = true;
};

Editor.redo = () => {
  if (!Editor.future.length) return;
  const keep = Editor.history.slice();
  keep.push(JSON.stringify(Editor.document()));
  const next = JSON.parse(Editor.future.pop());
  Editor.load(next);
  Editor.history = keep;
  FL.dirty = true;
};

const uid = (prefix) => prefix + Math.random().toString(36).slice(2, 12);

Editor.addNode = (type, x, y) => {
  const block = FL.blocks[type];
  if (!block) return null;
  if (block.limit && Object.values(Editor.nodes).filter((n) => n.type === type).length >= block.limit) {
    FL.toast('Only one allowed', `A workflow can have one '${block.name}' block.`, 'bad');
    return null;
  }
  Editor.snapshot();
  const params = {};
  for (const param of block.params) params[param.key] = param.default;
  const snap = (FL.state.settings || {}).grid_snap || 10;
  const node = {
    id: uid('n'), type, label: '', notes: '', params, disabled: false,
    x: Math.round(x / snap) * snap, y: Math.round(y / snap) * snap,
  };
  Editor.nodes[node.id] = node;
  Editor.draw();
  Editor.select(node.id);
  return node;
};

Editor.duplicate = (id) => {
  const source = Editor.nodes[id];
  if (!source) return;
  Editor.snapshot();
  const copy = { ...JSON.parse(JSON.stringify(source)), id: uid('n'), x: source.x + 30, y: source.y + 30 };
  Editor.nodes[copy.id] = copy;
  Editor.draw(); Editor.select(copy.id);
};

Editor.removeNode = (id) => {
  Editor.snapshot();
  delete Editor.nodes[id];
  for (const [wireId, wire] of Object.entries(Editor.wires)) {
    if (wire.source === id || wire.target === id) delete Editor.wires[wireId];
  }
  Editor.selected = null;
  Editor.draw();
  Editor.inspect();
};

Editor.connect = (source, sourcePort, target, targetPort, kind) => {
  if (source === target) return;
  Editor.snapshot();
  if (kind === 'flow') {
    for (const [id, wire] of Object.entries(Editor.wires)) {
      if (wire.kind === 'flow' && wire.source === source && wire.source_port === sourcePort) delete Editor.wires[id];
    }
  } else {
    const port = (FL.blocks[Editor.nodes[target].type].inputs || []).find((p) => p.key === targetPort);
    if (!port || !port.multiple) {
      for (const [id, wire] of Object.entries(Editor.wires)) {
        if (wire.kind === 'value' && wire.target === target && wire.target_port === targetPort) delete Editor.wires[id];
      }
    }
  }
  const wire = { id: uid('w'), kind, source, source_port: sourcePort, target, target_port: targetPort || '' };
  Editor.wires[wire.id] = wire;
  Editor.draw();
};

Editor.tidy = () => {
  Editor.snapshot();
  const snap = (FL.state.settings || {}).grid_snap || 10;
  for (const node of Object.values(Editor.nodes)) {
    node.x = Math.round(node.x / snap) * snap;
    node.y = Math.round(node.y / snap) * snap;
  }
  Editor.draw();
};

/* ------------------------------------------------------------------ drawing */
Editor.draw = () => {
  const host = $('#nodes');
  if (!host) return;
  host.innerHTML = '';
  for (const node of Object.values(Editor.nodes)) host.append(Editor.renderNode(node));
  Editor.applyTransform();
  Editor.drawWires();
};

Editor.renderNode = (node) => {
  const block = FL.blocks[node.type];
  const colour = block
    ? (FL.blockCat.categories.find((c) => c.key === block.category) || {}).accent
    : '#ff5d73';
  const box = el('div', {
    class: `node${Editor.selected === node.id ? ' sel' : ''}${node.disabled ? ' off' : ''}`,
    id: `node-${node.id}`,
    style: `left:${node.x}px; top:${node.y}px; border-color:${colour}55`,
    onmousedown: (event) => {
      // Clicking anywhere on a block opens it; pins handle their own events.
      if (!event.target.closest('.pin')) Editor.select(node.id);
    },
  });
  if (!block) {
    box.append(el('div', { class: 'nhead' }, el('span', { class: 'bar', style: 'background:#ff5d73' }),
      el('span', { class: 'nm', text: node.type })),
      el('div', { class: 'plain', text: 'This block type is not in this version.' }));
    return box;
  }
  const head = el('div', { class: 'nhead' },
    el('span', { class: 'bar', style: `background:${colour}` }),
    el('span', { class: 'nm', text: node.label || block.name }),
    el('span', { class: 'ms', id: `ms-${node.id}`, text: '' }));
  FL.tipify(head, {
    title: node.label ? `${node.label} - ${block.name}` : block.name,
    body: block.plain, extra: block.summary, caution: block.caution,
    meta: node.notes || 'drag to move · click to open its settings',
  });
  box.append(head);
  box.append(el('div', { class: 'plain', text: block.plain }));

  const ports = el('div', { class: 'ports' });
  if (block.flow_in) ports.append(Editor.renderPort(node, { key: 'in', label: 'Run', plain: 'Where the run arrives' }, 'flow-in'));
  for (const out of block.flow_out) ports.append(Editor.renderPort(node, out, 'flow-out'));
  for (const input of block.inputs) ports.append(Editor.renderPort(node, input, 'value-in'));
  for (const output of block.outputs) ports.append(Editor.renderPort(node, output, 'value-out'));
  box.append(ports);
  if (block.needs.length) {
    const missing = block.needs.filter((k) => {
      const device = (FL.state.devices || []).find((d) => d.key === k);
      return device && !device.available;
    });
    if (missing.length) {
      box.append(FL.tipify(el('div', { class: 'needs', text: `needs ${missing.join(', ')}` }), {
        title: 'Hardware not present',
        body: 'This block uses something this machine cannot reach. Install the driver, or turn on device simulation in the run settings to try the workflow anyway.',
        meta: 'Simulated values are labelled everywhere they appear',
      }));
    }
  }

  head.addEventListener('mousedown', (event) => {
    if (event.button !== 0) return;
    event.stopPropagation();
    Editor.select(node.id);
    const start = { x: event.clientX, y: event.clientY, nx: node.x, ny: node.y };
    let moved = false;
    const move = (e) => {
      if (!moved) { Editor.snapshot(); moved = true; }
      const snap = (FL.state.settings || {}).grid_snap || 10;
      node.x = Math.round((start.nx + (e.clientX - start.x) / Editor.scale) / snap) * snap;
      node.y = Math.round((start.ny + (e.clientY - start.y) / Editor.scale) / snap) * snap;
      box.style.left = `${node.x}px`; box.style.top = `${node.y}px`;
      Editor.drawWires();
    };
    const up = () => {
      removeEventListener('mousemove', move); removeEventListener('mouseup', up);
      Editor.drawMinimap();
    };
    addEventListener('mousemove', move); addEventListener('mouseup', up);
  });
  head.addEventListener('dblclick', () => { node.disabled = !node.disabled; Editor.draw(); });
  return box;
};

Editor.renderPort = (node, port, role) => {
  const flow = role.startsWith('flow');
  const out = role.endsWith('out');
  const colour = flow ? '#cbd8e2' : (TYPE_COLOUR()[port.type] || {}).colour || '#888';
  const linked = Object.values(Editor.wires).some((wire) => (
    out ? (wire.source === node.id && wire.source_port === port.key)
      : (wire.target === node.id && (flow ? wire.kind === 'flow' : wire.target_port === port.key))
  ));
  const pin = el('span', {
    class: `pin${flow ? ' flow' : ''}`,
    style: `background:${linked ? colour : '#0a0f13'};border-color:${colour}`,
    'data-node': node.id, 'data-port': port.key, 'data-role': role,
  });
  pin.addEventListener('mousedown', (event) => {
    event.stopPropagation(); event.preventDefault();
    Editor.wiring = { node: node.id, port: port.key, role, kind: flow ? 'flow' : 'value', x: 0, y: 0 };
  });
  pin.addEventListener('mouseup', (event) => {
    event.stopPropagation();
    const from = Editor.wiring;
    if (!from) return;
    Editor.wiring = null;
    if (from.kind !== (flow ? 'flow' : 'value')) {
      FL.toast('Cannot connect', 'Run-order pins connect to run-order pins; value pins to value pins.', 'bad');
      Editor.drawWires(); return;
    }
    const fromOut = from.role.endsWith('out');
    if (fromOut === out) {
      FL.toast('Cannot connect', 'Wires go from an output on one block to an input on another.', 'bad');
      Editor.drawWires(); return;
    }
    if (fromOut) Editor.connect(from.node, from.port, node.id, flow ? '' : port.key, from.kind);
    else Editor.connect(node.id, port.key, from.node, flow ? '' : from.port, from.kind);
  });
  const row = el('div', { class: `port ${out ? 'out' : 'in'}${linked ? ' linked' : ''}` },
    pin, el('span', { class: 'pl', text: port.label }));
  const typeInfo = TYPE_COLOUR()[port.type] || {};
  FL.tipify(row, {
    title: port.label,
    body: port.plain,
    extra: port.tip && port.tip !== port.plain ? port.tip : '',
    meta: flow ? 'run order' : `${typeInfo.label || port.type}${port.multiple ? ' · accepts several' : ''}${port.required ? ' · required' : ''}`,
  });
  return row;
};

Editor.pinPosition = (nodeId, portKey, role) => {
  const pin = document.querySelector(
    `#node-${CSS.escape(nodeId)} .pin[data-port="${CSS.escape(portKey)}"][data-role="${role}"]`);
  const host = $('#nodes');
  if (!pin || !host) return null;
  const a = pin.getBoundingClientRect();
  const b = host.getBoundingClientRect();
  return {
    x: (a.left + a.width / 2 - b.left) / Editor.scale,
    y: (a.top + a.height / 2 - b.top) / Editor.scale,
  };
};

Editor.drawWires = () => {
  const svg = $('#wires');
  if (!svg) return;
  svg.innerHTML = '';
  svg.setAttribute('width', '100%'); svg.setAttribute('height', '100%');
  svg.style.overflow = 'visible';
  const path = (a, b, className, colour) => {
    const dx = Math.max(38, Math.abs(b.x - a.x) * 0.45);
    const node = svgNode('path', {
      d: `M${a.x},${a.y} C${a.x + dx},${a.y} ${b.x - dx},${b.y} ${b.x},${b.y}`,
      class: className,
    });
    if (colour) node.setAttribute('stroke', colour);
    return node;
  };
  for (const wire of Object.values(Editor.wires)) {
    const flow = wire.kind === 'flow';
    const from = Editor.pinPosition(wire.source, wire.source_port, flow ? 'flow-out' : 'value-out');
    const to = Editor.pinPosition(wire.target, flow ? 'in' : wire.target_port, flow ? 'flow-in' : 'value-in');
    if (!from || !to) continue;
    const block = FL.blocks[Editor.nodes[wire.source] ? Editor.nodes[wire.source].type : ''];
    const port = block && (block.outputs || []).find((p) => p.key === wire.source_port);
    const colour = flow ? null : (port ? port.colour : '#7c8894');
    const line = path(from, to, `wire ${flow ? 'flow' : ''}${Editor.hotWire === wire.id ? ' hot' : ''}`, colour);
    line.style.pointerEvents = 'stroke';
    line.style.cursor = 'pointer';
    line.addEventListener('click', async () => {
      if (await FL.confirm('Remove this wire?',
        'The blocks stay; only the connection between them goes.', 'Remove wire')) {
        Editor.snapshot(); delete Editor.wires[wire.id]; Editor.draw();
      }
    });
    svg.append(line);
  }
  if (Editor.wiring) {
    const from = Editor.pinPosition(Editor.wiring.node, Editor.wiring.port, Editor.wiring.role);
    if (from) svg.append(path(from, { x: Editor.wiring.x, y: Editor.wiring.y }, 'wire ghost'));
  }
  Editor.drawMinimap();
};

Editor.drawMinimap = () => {
  const canvas = $('#minimap');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  const list = Object.values(Editor.nodes);
  if (!list.length) return;
  const minX = Math.min(...list.map((n) => n.x)) - 30;
  const minY = Math.min(...list.map((n) => n.y)) - 30;
  const maxX = Math.max(...list.map((n) => n.x + 210)) + 30;
  const maxY = Math.max(...list.map((n) => n.y + 160)) + 30;
  const scale = Math.min(canvas.width / (maxX - minX), canvas.height / (maxY - minY));
  for (const node of list) {
    const block = FL.blocks[node.type];
    const colour = block ? (FL.blockCat.categories.find((c) => c.key === block.category) || {}).accent : '#ff5d73';
    ctx.fillStyle = Editor.selected === node.id ? '#ffb02e' : `${colour}aa`;
    ctx.fillRect((node.x - minX) * scale, (node.y - minY) * scale,
      Math.max(2, 208 * scale), Math.max(2, 46 * scale));
  }
  const wrap = $('#canvas-wrap').getBoundingClientRect();
  ctx.strokeStyle = '#4fd1e0'; ctx.lineWidth = 1;
  ctx.strokeRect((-Editor.panX / Editor.scale - minX) * scale, (-Editor.panY / Editor.scale - minY) * scale,
    (wrap.width / Editor.scale) * scale, (wrap.height / Editor.scale) * scale);
};

/* ------------------------------------------------------------------ live run */
Editor.flash = (nodeId, record) => {
  const box = $(`#node-${CSS.escape(nodeId)}`);
  if (box) {
    box.classList.remove('firing', 'err');
    void box.offsetWidth;
    box.classList.add('firing');
  }
  const ms = $(`#ms-${CSS.escape(nodeId)}`);
  if (ms && FL.live.nodeMs[nodeId] !== undefined) ms.textContent = `${FL.num(FL.live.nodeMs[nodeId], 1)}ms`;
  for (const [id, wire] of Object.entries(Editor.wires)) {
    if (wire.source === nodeId && wire.kind === 'flow') { Editor.hotWire = id; break; }
  }
  Editor.drawWires();
};

Editor.markError = (nodeId, message) => {
  const box = $(`#node-${CSS.escape(nodeId)}`);
  if (!box) return;
  box.classList.add('err');
  if (!box.querySelector('.badge')) box.append(el('div', { class: 'badge', text: '!', tip: { title: 'This block failed', body: message } }));
};

Editor.markIteration = (n) => {
  const chip = $('#wf-name');
  if (chip) chip.textContent = `${Editor.name} · pass ${n}`;
};

Editor.showProblems = (problems) => {
  const box = $('#problems');
  if (!box) return;
  box.innerHTML = '';
  if (!problems.length) {
    box.append(el('div', { class: 'p' }, el('span', { class: 'sev', style: 'background:#57d98a' }),
      el('span', {}, el('div', { text: 'No problems found.' }),
        el('div', { class: 'fix', text: 'Every required input is connected and the run order reaches every block.' }))));
    setTimeout(() => { if (box.childElementCount === 1) box.innerHTML = ''; }, 4000);
    return;
  }
  const colours = { error: '#ff5d73', warning: '#ffb02e', note: '#4fd1e0' };
  const counts = problems.reduce((acc, p) => ({ ...acc, [p.severity]: (acc[p.severity] || 0) + 1 }), {});
  box.append(el('div', { class: 'ph' },
    `${counts.error || 0} to fix · ${counts.warning || 0} to know about`,
    FL.tipify(el('span', { class: 'x', text: 'hide', onclick: () => { box.innerHTML = ''; } }),
      { title: 'Hide this list', body: 'The problems stay; press Check to see them again.' })));
  for (const problem of problems) {
    box.append(el('div', {
      class: 'p',
      onclick: () => { if (problem.node) { Editor.select(problem.node); Editor.centre(problem.node); } },
    }, el('span', { class: 'sev', style: `background:${colours[problem.severity]}` }),
      el('span', {}, el('div', { text: problem.message }),
        problem.fix ? el('div', { class: 'fix', text: problem.fix }) : null)));
  }
};

Editor.centre = (nodeId) => {
  const node = Editor.nodes[nodeId];
  if (!node) return;
  const wrap = $('#canvas-wrap').getBoundingClientRect();
  Editor.panX = wrap.width / 2 - (node.x + 104) * Editor.scale;
  Editor.panY = wrap.height / 2 - (node.y + 60) * Editor.scale;
  Editor.applyTransform();
};

Editor.check = async () => {
  try {
    const result = await FL.api('/api/workflow/check', { body: { workflow: Editor.document() } });
    FL.problems = result.problems;
    Editor.showProblems(result.problems);
    const errors = result.problems.filter((p) => p.severity === 'error').length;
    FL.toast(errors ? `${errors} problem${errors > 1 ? 's' : ''} to fix` : 'Workflow checks out',
      errors ? 'Click a problem to jump to the block.' : `${result.nodes} blocks, ${result.wires} wires.`,
      errors ? 'bad' : 'good');
    return errors === 0;
  } catch (error) { FL.toast('Check failed', error.message, 'bad'); return false; }
};

Editor.save = async () => {
  if (!FL.project) { FL.toast('No project open', 'Open or create one on the Projects page first.', 'bad'); return; }
  try {
    await FL.api(`/api/projects/${FL.project.id}`, { method: 'PATCH', body: { workflow: Editor.document() } });
    FL.dirty = false;
    FL.toast('Saved', `${Editor.name} · ${Object.keys(Editor.nodes).length} blocks`, 'good');
  } catch (error) { FL.toast('Could not save', error.message, 'bad'); }
};

/* --------------------------------------------------------------- inspector */
Editor.select = (nodeId) => {
  Editor.selected = nodeId;
  $$('.node').forEach((box) => box.classList.toggle('sel', box.id === `node-${nodeId}`));
  Editor.inspect();
  Editor.drawMinimap();
};

Editor.inspect = () => {
  const body = $('#insp-body');
  if (!body) return;
  body.innerHTML = '';
  const node = Editor.nodes[Editor.selected];
  if (!node) {
    body.append(el('div', { class: 'insp-empty' },
      el('p', { text: 'Click a block to see what it does and change its settings.' }),
      el('p', { text: 'Drag a block from the left onto the canvas to add one. Drag from an outlet to an inlet to wire two together.' }),
      el('p', { text: 'Hovering anything in this interface explains it.' })));
    return;
  }
  const block = FL.blocks[node.type];
  if (!block) { body.append(el('div', { class: 'insp-empty', text: `Unknown block type ${node.type}` })); return; }

  body.append(el('div', { class: 'insp-section' },
    el('h3', { text: block.name }),
    el('p', { style: 'margin:0 0 6px;color:var(--text-2)', text: block.plain }),
    el('p', { style: 'margin:0;font-size:11.5px;color:var(--dim)', text: block.summary }),
    block.caution ? el('div', { class: 'note warn', text: `Caution: ${block.caution}` }) : null,
    ...(block.tips || []).map((tip) => el('div', { class: 'note', text: tip }))));

  body.append(el('div', { class: 'insp-section' },
    FL.field({
      label: 'Name on the canvas', plain: 'Optional. Replaces the block name on this one block.',
      tip: 'Useful when a workflow has several of the same block - "left wheel", "right wheel".',
      value: node.label, onchange: (v) => { Editor.snapshot(); node.label = v; Editor.draw(); },
    }),
    FL.field({
      label: 'Notes', kind: 'code', plain: 'Why this block is here.',
      tip: 'Shown when you hover the block, and written into the saved workflow.',
      value: node.notes, onchange: (v) => { Editor.snapshot(); node.notes = v; Editor.draw(); },
    }),
    FL.field({
      label: 'Skip this block', kind: 'bool', plain: 'Leave it in place but do not run it',
      tip: 'The run passes straight through. Handy for switching a piece of a workflow off without unwiring it.',
      value: node.disabled, onchange: (v) => { Editor.snapshot(); node.disabled = v; Editor.draw(); },
    })));

  if (block.params.length) {
    const section = el('div', { class: 'insp-section' }, el('h3', { text: 'Settings' }));
    for (const param of block.params) {
      if (param.depends && param.depends.length === 2 &&
        String(node.params[param.depends[0]]) !== String(param.depends[1])) continue;
      const field = FL.field({
        label: param.label, plain: param.plain, tip: param.tip, kind: fieldKind(param),
        value: node.params[param.key], options: paramOptions(param),
        min: param.minimum, max: param.maximum, step: param.step, unit: param.unit,
        placeholder: param.placeholder,
        onchange: (value) => {
          Editor.snapshot();
          node.params[param.key] = value;
          Editor.draw();
          if (param.depends || param.key === 'action' || param.key === 'mode' || param.key === 'use_region') Editor.inspect();
        },
      });
      if (param.advanced) field.classList.add('param-adv');
      section.append(field);
      if (param.kind === 'channel') section.append(Editor.channelNote(node.params[param.key]));
      if (param.kind === 'pin') section.append(Editor.pinNote(node.params[param.key]));
    }
    body.append(section);
  }

  body.append(el('div', { class: 'insp-section' },
    el('div', { class: 'row' },
      FL.tipify(el('button', { class: 'ghost small', text: 'Duplicate', onclick: () => Editor.duplicate(node.id) }),
        { title: 'Duplicate', body: 'A copy with the same settings, offset slightly. Ctrl+D does the same.' }),
      FL.tipify(el('button', { class: 'danger small', text: 'Delete', onclick: () => Editor.removeNode(node.id) }),
        { title: 'Delete this block', body: 'Removes the block and every wire attached to it. Undo brings it back.' }))));

  function fieldKind(param) {
    if (['channel', 'pin', 'select'].includes(param.kind)) return 'select';
    if (param.kind === 'bool') return 'bool';
    if (param.kind === 'code') return 'code';
    if (param.kind === 'number') return 'number';
    return 'text';
  }
  function paramOptions(param) {
    if (param.kind === 'channel') {
      return param.options.map((option) => {
        const resolved = Editor.channelInfo(option.value);
        const count = resolved ? ` (${resolved.cells} cells)` : '';
        return { value: option.value, label: `${option.label}${count}`, plain: option.plain };
      });
    }
    if (param.kind === 'pin') {
      return param.options.map((o) => ({ value: Number(o.value), label: o.label, plain: o.plain }));
    }
    if (param.key === 'port' && (node.type === 'input.serial' || node.type === 'output.serial')) {
      const device = (FL.state.devices || []).find((d) => d.key === 'serial') || {};
      const ports = (device.ports || []).map((p) => ({ value: p.device, label: `${p.device} - ${p.description}`, plain: p.hwid }));
      return ports.length ? ports : [{ value: '', label: 'No serial port found', plain: '' }];
    }
    return param.options.map((o) => ({ value: o.value, label: o.label, plain: o.plain }));
  }
};

Editor.channelInfo = (key) => {
  const session = FL.state.session || {};
  const catalogue = session.catalogue || {};
  const atlas = catalogue.atlas || {};
  return (atlas.channels || []).find((c) => c.key === key);
};

Editor.channelNote = (key) => {
  const channel = FL.channels[key];
  const resolved = Editor.channelInfo(key);
  if (!channel) return el('span');
  const note = el('div', { class: `note${resolved && !resolved.present ? ' warn' : ''}` },
    el('b', { text: channel.name }), ' - ', channel.plain,
    el('div', { style: 'margin-top:5px;color:var(--dim);font-size:11.5px', text: channel.detail }),
    resolved ? el('div', { style: 'margin-top:5px;font-family:var(--mono);font-size:10.5px' },
      resolved.present
        ? `${resolved.cells} cells in this dataset (${resolved.cells_left} left, ${resolved.cells_right} right)`
        : 'Not present in the loaded dataset - this block would fail.') : null,
    channel.notes ? el('div', { style: 'margin-top:5px;color:var(--amber);font-size:11.5px', text: channel.notes }) : null);
  return note;
};

Editor.pinNote = (pin) => {
  const info = (FL.catalogue.blocks.pins || []).find((p) => Number(p.bcm) === Number(pin));
  if (!info) return el('span');
  return el('div', { class: 'note' },
    el('b', { text: info.label }), ' - normally used for: ', info.default_use,
    el('div', { style: 'margin-top:4px;color:var(--dim);font-size:11.5px',
      text: 'BCM numbering. Header pin ' + info.physical + ' counting from the corner nearest the SD card slot.' }));
};
