/* FLYLAB - the pages. One object per section of the interface. */

FL.views = {};

FL.views.init = () => {
  for (const page of FL.PAGES) {
    const view = FL.views[page.key];
    if (view && view.build) view.build($(`#page-${page.key}`));
  }
};

const head = (title, text, ...actions) => el('div', { class: 'page-head' },
  el('div', { class: 'lead' }, el('h1', { text: title }), el('p', { text })),
  el('div', { class: 'row' }, actions));

const tipButton = (label, tip, handler, kind = '') =>
  FL.tipify(el('button', { class: kind, text: label, onclick: handler }), tip);

/* ================================================================= STATUS */
FL.views.overview = {
  build(root) { this.root = root; },
  show() {
    const root = this.root;
    const state = FL.state || {};
    const session = state.session || {};
    const dataset = session.dataset || {};
    const catalogue = session.catalogue || {};
    const atlas = catalogue.atlas || {};
    const workspace = state.workspace || {};
    const devices = state.devices || [];
    const host = state.host || {};
    const run = state.run || {};
    root.innerHTML = '';
    root.append(head('Status',
      'What is loaded right now, what this machine can reach, and where to go next.',
      tipButton('Refresh', { title: 'Re-read everything', body: 'Asks the service for the current state, re-probes hardware, and redraws this page.' },
        () => FL.refresh(), 'ghost'),
      tipButton('Guide', { title: 'How this fits together', body: 'A short walk through what each page is for and the order to use them in.' },
        () => FL.views.overview.helpModal(), 'ghost')));

    const loaded = state.session_loaded;
    root.append(el('div', { class: 'grid g4', style: 'margin-bottom:12px' },
      FL.tipify(FL.metric('Cells loaded', loaded ? (dataset.neurons || 0).toLocaleString() : 'none',
        loaded ? dataset.release : 'no dataset loaded'),
        { title: 'Neurons in the loaded graph', body: 'Every annotated neuronal object the release retains. They all integrate every step; nothing is pruned.' }),
      FL.tipify(FL.metric('Connections', loaded ? (dataset.directed_edges || 0).toLocaleString() : '--',
        'directed, contact-count weighted', 'cyan'),
        { title: 'Directed connections', body: 'Every released edge between retained cells, weak ones and self-connections included. The weight is the reconstructed synaptic contact count.' }),
      FL.tipify(FL.metric('Channels present', loaded ? `${atlas.present || 0} / ${atlas.total || 0}` : '--',
        'named input, output and teaching groups', 'green'),
        { title: 'Addressable channels', body: 'Named groups of real cells this dataset actually contains. A channel with no cells is reported rather than hidden.' }),
      FL.tipify(FL.metric('Projects', workspace.projects || 0,
        `${workspace.models || 0} saved models · ${FL.bytes(workspace.disk_bytes || 0)}`, 'green'),
        { title: 'Your saved work', body: 'Projects and models in this workspace folder.', meta: workspace.root })));

    const left = el('div', { class: 'grid' });
    const right = el('div', { class: 'grid' });

    // --- network
    left.append(FL.panel(loaded ? 'Loaded network' : 'No network loaded',
      loaded ? el('div', {},
        el('dl', { class: 'kv' },
          el('dt', { text: 'Release' }), el('dd', { text: dataset.release || '--' }),
          el('dt', { text: 'Folder' }), el('dd', { text: dataset.root || '--' }),
          el('dt', { text: 'Compartments' }), el('dd', { text: (catalogue.compartments || {}).preset_label || '--' }),
          el('dt', { text: 'Plastic synapses' }), el('dd', { text: ((catalogue.compartments || {}).plastic_edges || 0).toLocaleString() }),
          el('dt', { text: 'Photoreceptors' }), el('dd', { text: `${((catalogue.graph || {}).retina_cells || 0).toLocaleString()} brightness, ${((catalogue.graph || {}).colour_cells || 0).toLocaleString()} colour` }),
          el('dt', { text: 'Verified' }), el('dd', { text: (dataset.checks || []).length ? dataset.checks.join('; ') : 'fixture - no source checks apply' })),
        el('div', { class: 'row', style: 'margin-top:10px' },
          tipButton('Open the network view', { title: 'Network page', body: 'Live activity across the whole graph, plus every channel it contains.' }, () => FL.go('connectome'), 'ghost'),
          tipButton('Unload', { title: 'Unload the network', body: 'Frees the memory it is using. Anything unsaved in the network is lost; saved models are untouched.' },
            async () => {
              if (await FL.confirm('Unload the network?', 'Activity and any unsaved synaptic changes are discarded. Saved models stay on disk.', 'Unload')) {
                await FL.api('/api/session/unload', { body: {} });
                FL.viewport.reset(); FL.refresh();
              }
            }, 'danger small')))
        : el('div', {},
          el('p', { style: 'margin-top:0;color:var(--text-2)' },
            'Nothing is loaded yet. You need a dataset before a workflow can touch the network.'),
          el('div', { class: 'note' },
            el('b', { text: 'The real thing: ' }),
            'about 1.1 GB from the MaleCNS v1.0 release, checked against committed checksums and compiled into 166,700 cells and 25,582,938 connections.'),
          el('div', { class: 'note warn' },
            el('b', { text: 'Or try the software first: ' }),
            'the bench fixture is a small randomly wired graph with the same shape. It exercises every part of this program in seconds and tells you nothing about flies.'),
          el('div', { class: 'row', style: 'margin-top:10px' },
            tipButton('Go to Housekeeping', { title: 'Download the connectome', body: 'Housekeeping runs the download, the checksum checks and the compile, with progress.' }, () => FL.go('housekeeping'), 'primary'),
            tipButton('Build the bench fixture', { title: 'Synthetic test graph', body: 'Builds a small random graph so you can learn the interface now and download the real dataset later.' },
              () => FL.views.housekeeping.buildFixture(), 'ghost'))),
      { accent: loaded ? 'accent-cyan' : 'accent-amber', tag: loaded ? 'ready' : 'not loaded' }));

    // --- datasets on disk
    const datasets = state.datasets || [];
    left.append(FL.panel('Datasets on this machine',
      datasets.length ? el('div', {}, datasets.map((entry) => el('div', { class: 'list-item' },
        el('span', { class: `lamp ${entry.ready ? 'ok' : 'warn'}` }),
        el('div', { class: 'grow' },
          el('div', { class: 'nm', text: entry.fixture ? 'Bench fixture (synthetic)' : 'MaleCNS v1.0' }),
          el('div', { class: 'sub', text: entry.root })),
        el('span', { class: 'chip', text: entry.ready ? `${(entry.manifest.neurons || 0).toLocaleString()} cells` : 'not compiled' }),
        tipButton('Load', { title: 'Load this dataset', body: 'Reads the graph, compiles the simulation kernel if needed, and indexes every channel. Takes a few seconds on the fixture, longer on the full release.' },
          () => FL.views.overview.loadDataset(entry.root), 'primary small'))))
        : el('div', { class: 'empty' }, el('h3', { text: 'Nothing downloaded yet' }),
          el('div', { text: 'Housekeeping has the download, and the bench fixture if you want to try the software first.' })),
      { tag: `${datasets.length} found` }));

    // --- recent projects
    left.append(FL.panel('Pick up where you left off',
      (workspace.last_project ? el('div', {},
        el('div', { class: 'list-item' },
          el('div', { class: 'grow' },
            el('div', { class: 'nm', text: workspace.last_project.name }),
            el('div', { class: 'sub', text: `${workspace.last_project.blocks} blocks · ${workspace.last_project.runs} runs · edited ${FL.ago(workspace.last_project.updated)}` })),
          tipButton('Open', { title: 'Open this project', body: 'Loads its workflow onto the canvas with the settings and model it was last using.' },
            () => FL.views.projects.open(workspace.last_project.id), 'primary small')),
        el('div', { class: 'row' },
          tipButton('All projects', { title: 'Project library', body: 'Everything saved here, with run history.' }, () => FL.go('projects'), 'ghost'),
          tipButton('New project', { title: 'Start something new', body: 'Choose a starting template - several are complete working workflows.' },
            () => FL.views.projects.createModal(), 'ghost')))
        : el('div', { class: 'empty' }, el('h3', { text: 'No projects yet' }),
          el('div', { text: 'A project is a workflow plus the settings and model it runs with.' }),
          el('div', { class: 'row', style: 'justify-content:center;margin-top:10px' },
            tipButton('Create the first one', { title: 'New project', body: 'Start from a template - "Bench check" runs with no hardware at all.' },
              () => FL.views.projects.createModal(), 'primary')))),
      {}));

    // --- hardware
    right.append(FL.panel('What this machine can reach',
      el('div', {}, devices.map((device) => FL.tipify(el('div', { class: 'channel-row' },
        el('span', { class: `lamp ${device.available ? 'ok' : ''}` }),
        el('div', { class: 'nm' }, el('b', { text: device.name }), el('span', { text: device.plain })),
        el('span', { class: `chip ${device.available ? 'good' : ''}`, text: device.available ? device.backend : 'not installed' })),
        {
          title: device.name, body: device.detail,
          extra: device.available ? `Using ${device.backend}.` : `Install: ${device.install_hint}`,
          meta: [device.platform_note, device.simulated_note].filter(Boolean).join(' '),
        }))),
      {
        tag: `${devices.filter((d) => d.available).length}/${devices.length}`,
        actions: [tipButton('Re-check', { title: 'Probe again', body: 'Re-imports every driver and re-lists serial ports. Run it after plugging something in.' },
          async () => { await FL.api('/api/devices?refresh=true'); FL.refresh(); }, 'ghost small')],
      }));

    right.append(FL.panel('Host',
      el('dl', { class: 'kv' },
        el('dt', { text: 'Python' }), el('dd', { text: host.python || '--' }),
        el('dt', { text: 'Platform' }), el('dd', { text: host.platform || '--' }),
        el('dt', { text: 'Processors' }), el('dd', { text: String(host.cpu_count || '--') }),
        el('dt', { text: 'Compiler' }), el('dd', { text: host.compiler || 'none found' }),
        el('dt', { text: 'Display' }), el('dd', { text: host.display || 'headless' }),
        el('dt', { text: 'Reachable at' }), el('dd', { text: ((state.settings || {}).urls || []).join('  ') })),
      { tag: `up ${FL.num((state.uptime_seconds || 0) / 60, 0)}m` }));

    if (run && run.state && run.state.run_id) {
      right.append(FL.panel('Last run',
        el('div', {},
          el('dl', { class: 'kv' },
            el('dt', { text: 'Run' }), el('dd', { text: run.state.run_id }),
            el('dt', { text: 'Outcome' }), el('dd', { text: `${run.state.status} - ${run.state.detail || run.state.reason}` }),
            el('dt', { text: 'Iterations' }), el('dd', { text: String(run.state.iteration) }),
            el('dt', { text: 'Network steps' }), el('dd', { text: String(run.neural_steps || 0) })),
          run.goal && run.goal.goal ? el('div', { class: 'note' }, el('b', { text: 'Goal: ' }), run.goal.goal) : null,
          run.goal && run.goal.attempts ? el('div', { class: 'note' },
            `${run.goal.correct} of ${run.goal.attempts} scored right (${FL.num(run.goal.accuracy * 100, 1)}%). ${run.goal.caveat}`) : null),
        { accent: run.state.status === 'failed' ? 'accent-red' : '' }));
    }

    root.append(el('div', { class: 'grid g2' }, left, right));
  },

  async loadDataset(path) {
    FL.toast('Loading', 'Reading the graph and compiling the kernel. This can take a minute on the full release.');
    try {
      await FL.api('/api/session/load', { body: { path } });
      FL.toast('Network loaded', 'Ready to run.', 'good');
      await FL.refresh();
    } catch (error) { FL.toast('Could not load', error.message, 'bad'); }
  },

  helpModal() {
    FL.modal({
      title: 'How this fits together',
      width: 720,
      body: el('div', {},
        el('p', { text: 'FLYLAB puts a fly’s reconstructed nervous system between this computer’s inputs and its outputs. You choose what reaches which cells, how long the network runs, and what its activity drives.' }),
        el('ol', { style: 'padding-left:18px;color:var(--text-2);line-height:1.75' },
          el('li', {}, el('b', { text: 'Housekeeping' }), ' - download and verify the connectome, or build the bench fixture to try the software first.'),
          el('li', {}, el('b', { text: 'Status' }), ' - load a dataset. Nothing that touches the network works until this is done.'),
          el('li', {}, el('b', { text: 'Projects' }), ' - make a project from a template. Several templates are complete working workflows.'),
          el('li', {}, el('b', { text: 'Workflow' }), ' - the canvas. Inputs on the left, the network in the middle, outputs on the right. Press Run.'),
          el('li', {}, el('b', { text: 'Bench' }), ' - when something does not behave: drive one channel, read another, and test hardware on its own.'),
          el('li', {}, el('b', { text: 'Network' }), ' - watch activity spread while a run is going, and browse every channel the dataset contains.'),
          el('li', {}, el('b', { text: 'Training' }), ' - run a training schedule and the frozen-synapse control that any learning claim needs.'),
          el('li', {}, el('b', { text: 'Models' }), ' - label and reload what the network has learned.')),
        el('div', { class: 'note' }, el('b', { text: 'Hover anything. ' }),
          'Every button, port and setting in this interface describes itself: the technical term, what it literally does, and what happens when you use it.'),
        el('div', { class: 'note warn' }, el('b', { text: 'What this is not. ' }),
          'The connectome supplies anatomy. Dynamics, receptors and most neuromodulation are not modelled, and no useful learning has been demonstrated. Teaching pulses are engineered current injections into identified cells - not rewards, not punishments, and nothing is experienced.')),
      actions: [{ label: 'Got it', kind: 'primary', value: true }],
    });
  },
};

/* =============================================================== PROJECTS */
FL.views.projects = {
  build(root) { this.root = root; },
  async show() {
    const root = this.root;
    root.innerHTML = '';
    root.append(head('Projects',
      'A project holds a workflow, the settings it runs with, the model it resumes from, and everything it has done so far.',
      tipButton('New project', { title: 'Create a project', body: 'Pick a starting template. "Bench check" needs no hardware and confirms the whole path works.' },
        () => this.createModal(), 'primary')));
    let projects = [];
    try { projects = (await FL.api('/api/projects')).projects; }
    catch (error) { FL.toast('Could not list projects', error.message, 'bad'); }
    if (!projects.length) {
      root.append(el('div', { class: 'panel' }, el('div', { class: 'empty' },
        el('h3', { text: 'Nothing saved yet' }),
        el('div', { text: 'Start from a template: each one is a working workflow you can take apart.' }),
        el('div', { class: 'grid g3', style: 'margin-top:16px;text-align:left' },
          FL.templates.filter((t) => t.key !== 'empty').map((template) => el('div', {
            class: 'card', onclick: () => this.createModal(template.key),
          }, el('h3', { text: template.name }), el('p', { text: template.plain }),
            el('div', { class: 'meta', text: template.needs.length ? `needs ${template.needs.join(', ')}` : 'no hardware needed' })))))));
      return;
    }
    const table = el('table', {}, el('thead', {}, el('tr', {},
      el('th', { text: 'Project' }), el('th', { text: 'Blocks' }), el('th', { text: 'Runs' }),
      el('th', { text: 'Model' }), el('th', { text: 'Edited' }), el('th', { text: '' }))));
    const body = el('tbody');
    for (const project of projects) {
      body.append(el('tr', {},
        el('td', {}, el('div', { style: 'font-size:13px', text: project.name }),
          el('div', { style: 'color:var(--dim);font-size:11px', text: project.description || (project.template ? `from the ${project.template} template` : '') })),
        el('td', { class: 'mono', text: String(project.blocks) }),
        el('td', { class: 'mono', text: String(project.runs) }),
        el('td', { class: 'mono', text: project.model_id ? project.model_id.slice(0, 18) : '--' }),
        el('td', { class: 'mono', text: FL.ago(project.updated) }),
        el('td', {}, el('div', { class: 'row tight' },
          tipButton('Open', { title: 'Open on the canvas', body: 'Loads the workflow into the editor and makes this the project that Save writes to.' },
            () => this.open(project.id), 'primary small'),
          tipButton('Copy', { title: 'Duplicate', body: 'A second copy with its own history, so you can try a change without losing what works.' },
            () => this.duplicate(project.id), 'ghost small'),
          tipButton('Delete', { title: 'Delete this project', body: 'Removes the workflow, its settings and its run history. Saved models are not touched.' },
            () => this.remove(project.id, project.name), 'danger small')))));
    }
    table.append(body);
    root.append(FL.panel('Saved projects', table, { pad0: true, tag: `${projects.length}` }));
  },

  createModal(preset) {
    let name = '';
    let template = preset || 'bench';
    let description = '';
    const cards = el('div', { class: 'grid g3', style: 'margin-top:10px' });
    const draw = () => {
      cards.innerHTML = '';
      for (const entry of FL.templates) {
        cards.append(FL.tipify(el('div', {
          class: `card${entry.key === template ? ' on' : ''}`,
          onclick: () => { template = entry.key; draw(); },
        }, el('h3', { text: entry.name }), el('p', { text: entry.plain }),
          el('div', { class: 'meta', text: entry.needs.length ? `needs ${entry.needs.join(', ')}` : 'no hardware needed' })),
          { title: entry.name, body: entry.plain, extra: entry.detail }));
      }
    };
    draw();
    FL.modal({
      title: 'New project',
      width: 760,
      body: el('div', {},
        FL.field({ label: 'Name', plain: 'What this project is for.', tip: 'Shown in the project list and in run summaries.', value: '', placeholder: 'Wall follower', onchange: (v) => { name = v; } }),
        FL.field({ label: 'Description', kind: 'code', plain: 'Optional. A sentence for later you.', tip: 'A folder of projects called "test2" is no use in a month.', value: '', onchange: (v) => { description = v; } }),
        el('h3', { style: 'margin-top:12px', text: 'Start from' }), cards),
      actions: [
        { label: 'Cancel', kind: 'ghost', value: false },
        {
          label: 'Create', kind: 'primary',
          run: async () => {
            if (!name.trim()) { FL.toast('Needs a name', 'Give the project a name first.', 'bad'); return false; }
            try {
              const project = await FL.api('/api/projects', { body: { name, template, description } });
              FL.toast('Project created', project.name, 'good');
              await FL.views.projects.open(project.id);
              return true;
            } catch (error) { FL.toast('Could not create', error.message, 'bad'); return false; }
          },
        },
      ],
    });
  },

  async open(id) {
    try {
      const project = await FL.api(`/api/projects/${id}`);
      FL.project = project;
      FL.editor.load(project.workflow);
      FL.go('editor');
      FL.log('project', `Opened ${project.name}`);
    } catch (error) { FL.toast('Could not open', error.message, 'bad'); }
  },

  async duplicate(id) {
    try { await FL.api(`/api/projects/${id}/duplicate`, { body: {} }); this.show(); }
    catch (error) { FL.toast('Could not duplicate', error.message, 'bad'); }
  },

  async remove(id, name) {
    if (!await FL.confirm('Delete this project?',
      `"${name}", its workflow and its run history are removed from disk. Saved models are not affected. This cannot be undone.`,
      'Delete project')) return;
    try {
      await FL.api(`/api/projects/${id}`, { method: 'DELETE' });
      if (FL.project && FL.project.id === id) FL.project = null;
      this.show(); FL.refresh();
    } catch (error) { FL.toast('Could not delete', error.message, 'bad'); }
  },
};

/* ================================================================ WORKFLOW */
FL.views.editor = {
  build(root) { this.root = root; FL.editor.mount(root); },
  show() {
    if (!FL.project) {
      FL.editor.load({ name: 'No project open', nodes: [], wires: [], meta: {} });
      FL.toast('No project open', 'Open or create one on the Projects page.', 'bad');
    }
    FL.editor.applyTransform();
    // The canvas has its real size only once the page is visible.
    requestAnimationFrame(() => {
      FL.editor.drawGrid();
      if (FL.editor.needsFit) FL.editor.fit();
      FL.editor.drawWires();
    });
  },
  async runNow() {
    if (!FL.project) { FL.toast('No project open', 'Open a project before running.', 'bad'); return; }
    if (!FL.state.session_loaded) {
      const uses = Object.values(FL.editor.nodes).some((n) =>
        FL.blocks[n.type] && ['encode', 'brain', 'decode'].includes(FL.blocks[n.type].category));
      if (uses) {
        FL.toast('No network loaded', 'This workflow touches the network. Load a dataset on the Status page first.', 'bad');
        return;
      }
    }
    if (!await FL.editor.check()) return;
    await FL.editor.save();
    const settings = await FL.views.editor.settingsModal();
    if (!settings) return;
    try {
      const started = await FL.api('/api/run/start', {
        body: { project_id: FL.project.id, workflow: FL.editor.document(), settings },
      });
      FL.toast('Run started', started.run_id, 'good');
      $('#displays').innerHTML = '';
    } catch (error) { FL.toast('Could not start', error.message, 'bad'); }
  },

  settingsModal() {
    const defaults = FL.state.settings || {};
    const stored = (FL.project && FL.project.run_settings) || {};
    const values = {
      iterations: stored.iterations ?? defaults.iterations ?? 10,
      max_seconds: stored.max_seconds ?? defaults.max_seconds ?? 0,
      interval_seconds: stored.interval_seconds ?? defaults.interval_seconds ?? 0,
      simulate_devices: stored.simulate_devices ?? defaults.simulate_devices ?? true,
      stop_on_error: stored.stop_on_error ?? defaults.stop_on_error ?? true,
      learning: stored.learning ?? defaults.learning ?? true,
    };
    return FL.modal({
      title: 'Run settings',
      width: 620,
      body: el('div', {},
        el('p', { style: 'margin-top:0;color:var(--text-2)', text: 'These apply to this run and are remembered with the project.' }),
        FL.field({
          label: 'Iterations', kind: 'number', min: 0, max: 1000000, step: 1, value: values.iterations,
          plain: 'How many times to run the workflow from Start.',
          tip: '0 means keep going until something stops it: a Stop block, the goal being reached, the time limit, or you.',
          onchange: (v) => { values.iterations = v; },
        }),
        FL.field({
          label: 'Time limit', kind: 'number', min: 0, max: 86400, step: 10, unit: 's', value: values.max_seconds,
          plain: 'Stop after this many real seconds.',
          tip: '0 means no limit. Worth setting for anything driving hardware unattended.',
          onchange: (v) => { values.max_seconds = v; },
        }),
        FL.field({
          label: 'Gap between iterations', kind: 'number', min: 0, max: 3600, step: 0.1, unit: 's', value: values.interval_seconds,
          plain: 'Wait at least this long before starting the next pass.',
          tip: 'Use it to pace a workflow that drives a motor or polls a sensor faster than the hardware can cope with.',
          onchange: (v) => { values.interval_seconds = v; },
        }),
        FL.field({
          label: 'Allow synapses to change', kind: 'bool', value: values.learning,
          plain: 'Let the plasticity rule write to memory during this run',
          tip: 'Turn it off for a frozen control: same network, same inputs, nothing allowed to change. Any claim that a workflow learned something has to be compared against this.',
          onchange: (v) => { values.learning = v; },
        }),
        FL.field({
          label: 'Simulate missing hardware', kind: 'bool', value: values.simulate_devices,
          plain: 'Substitute plausible values when a device is not present',
          tip: 'Lets a workflow be built and stepped through on a laptop. Simulated values are labelled as such everywhere they appear, including in the run log.',
          onchange: (v) => { values.simulate_devices = v; },
        }),
        FL.field({
          label: 'Stop if a block fails', kind: 'bool', value: values.stop_on_error,
          plain: 'End the run on the first error',
          tip: 'With this off, a failing block is logged and the run carries on, which is usually what you want for a long unattended run and never what you want while debugging.',
          onchange: (v) => { values.stop_on_error = v; },
        }),
        !FL.state.settings.simulate_devices && !values.simulate_devices
          ? el('div', { class: 'note warn', text: 'Hardware blocks will drive real devices during this run.' }) : null),
      actions: [
        { label: 'Cancel', kind: 'ghost', value: null },
        {
          label: 'Start run', kind: 'go',
          run: async () => {
            if (FL.project) {
              try { await FL.api(`/api/projects/${FL.project.id}`, { method: 'PATCH', body: { run_settings: values } }); }
              catch { /* the run still goes ahead with these settings */ }
            }
            return values;
          },
        },
      ],
    });
  },
};

/* --------------------------------------------------------- live run helpers */
FL.views.run = {
  update() {
    const chip = $('#wf-name');
    if (chip && FL.live.goal && FL.live.goal.attempts) {
      chip.textContent = `${FL.editor.name} · pass ${FL.live.iteration} · ${FL.live.goal.correct}/${FL.live.goal.attempts} right`;
    }
    FL.refreshStatus();
  },
  neural() {
    if (FL.page === 'connectome') FL.views.connectome.tick();
  },
  displays(panels) {
    const strip = $('#displays');
    if (!strip) return;
    strip.innerHTML = '';
    for (const panel of panels) strip.append(FL.views.run.readout(panel));
  },
  readout(panel) {
    const span = (panel.maximum - panel.minimum) || 1;
    const fraction = Math.max(0, Math.min(1, (panel.number - panel.minimum) / span));
    const box = el('div', { class: 'readout' },
      el('div', { class: 'rl', text: panel.label }));
    if (panel.style === 'text') {
      box.append(el('div', { class: 'rv', style: 'font-size:14px', text: String(panel.value ?? '--') }));
    } else {
      box.append(el('div', { class: 'rv' }, FL.num(panel.number, 2),
        panel.unit ? el('span', { class: 'u', text: panel.unit }) : null));
    }
    if (panel.style === 'bar' || panel.style === 'gauge') {
      box.append(el('div', { class: 'track' }, el('i', { style: `left:0;width:${fraction * 100}%` })));
    } else if (panel.style === 'meter') {
      const centre = 50;
      const width = Math.abs(fraction - 0.5) * 100;
      box.append(el('div', { class: 'track centre' },
        el('i', { style: `left:${fraction >= 0.5 ? centre : centre - width}%;width:${width}%` })));
    }
    return FL.tipify(box, {
      title: panel.label,
      body: `Live readout from this workflow, shown as ${panel.style}.`,
      meta: `range ${panel.minimum} to ${panel.maximum}${panel.unit ? ` ${panel.unit}` : ''}`,
    });
  },
};

/* ================================================================= NETWORK */
FL.views.connectome = {
  build(root) {
    this.root = root;
    root.classList.add('flush');
    root.innerHTML = '';
    root.append(el('div', { id: 'connectome-layout' },
      el('div', { id: 'viewport-wrap' },
        el('canvas', { id: 'viewport' }),
        FL.tipify(el('div', { id: 'viewport-hud' }), {
          title: 'Live totals',
          body: 'Spikes across the whole network in the last step, and how much neural time has been simulated since the network was loaded.',
        }),
        FL.tipify(el('div', { id: 'viewport-legend' }), {
          title: 'About this picture',
          body: 'Each dot is one cell, brightness set by how hard it fired in the last network step. Regions are placed for legibility - these are not anatomical coordinates and not to scale.',
        })),
      el('aside', { id: 'conn-side', style: 'background:var(--panel);border-left:1px solid var(--line);display:flex;flex-direction:column;min-height:0' },
        el('div', { class: 'side-head' }, el('h2', { text: 'Channels' }),
          FL.tipify(el('input', { id: 'chan-search', placeholder: 'search', style: 'width:96px', oninput: (e) => FL.views.connectome.filter(e.target.value) }),
            { title: 'Find a channel', body: 'Search the technical name, the plain description or the modality - "smell", "steering", "reward".' })),
        el('div', { class: 'side-scroll', id: 'chan-list' }))));
    FL.viewport.attach($('#viewport'));
  },
  async show() {
    FL.viewport.resize();
    await this.load();
  },
  async load() {
    const legend = $('#viewport-legend');
    if (!FL.state.session_loaded) {
      FL.viewport.reset();
      if (legend) legend.textContent = 'No network loaded.';
      $('#chan-list').innerHTML = '';
      $('#chan-list').append(el('div', { class: 'empty' },
        el('h3', { text: 'Nothing loaded' }),
        el('div', { text: 'Load a dataset from the Status page to see the network and its channels.' })));
      return;
    }
    try {
      const layout = await FL.api('/api/session/layout');
      FL.viewport.setLayout(layout);
      if (legend) legend.textContent = `${layout.cells_shown.toLocaleString()} of ${layout.cells_total.toLocaleString()} cells shown · ${layout.note}`;
      const frame = await FL.api('/api/session/frame');
      FL.viewport.push(frame);
      this.tick();
    } catch (error) { FL.toast('Could not read the network', error.message, 'bad'); }
    this.renderChannels();
  },
  tick() {
    const hud = $('#viewport-hud');
    const frame = FL.viewport.activity;
    if (!hud || !frame) return;
    hud.innerHTML = '';
    hud.append(
      el('div', {}, el('b', { text: (frame.total_spikes || 0).toLocaleString() }), ' spikes last step'),
      el('div', { text: `${FL.num(frame.brain_ms / 1000, 2)} s of neural time` }),
      el('div', { text: `${FL.num(frame.window_seconds * 1000, 0)} ms window` }));
    this.renderChannels();
  },
  filter(query) {
    const text = query.trim().toLowerCase();
    $$('#chan-list .channel-row').forEach((row) => {
      row.style.display = !text || row.dataset.search.includes(text) ? '' : 'none';
    });
  },
  renderChannels() {
    const list = $('#chan-list');
    if (!list) return;
    const session = FL.state.session || {};
    const atlas = (session.catalogue || {}).atlas || {};
    const channels = atlas.channels || [];
    const activity = {};
    for (const entry of FL.live.activity || []) activity[entry.key] = entry;
    const scroll = list.scrollTop;
    list.innerHTML = '';
    const roles = [
      ['input', 'Senses - what can reach the network'],
      ['output', 'Outputs - what the network can drive'],
      ['modulatory', 'Teaching - where a training signal is delivered'],
      ['internal', 'Inside - populations you can watch but not address directly'],
    ];
    for (const [role, title] of roles) {
      const group = channels.filter((c) => c.role === role);
      if (!group.length) continue;
      list.append(el('h3', { style: 'padding:9px 8px 5px', text: title }));
      for (const channel of group) {
        const live = activity[channel.key];
        const row = el('div', {
          class: `channel-row${channel.present ? '' : ' absent'}`,
          'data-search': `${channel.key} ${channel.name} ${channel.plain} ${channel.modality} ${(channel.tags || []).join(' ')}`.toLowerCase(),
        }, el('div', { class: 'nm' },
          el('b', { text: channel.name }),
          el('span', { text: channel.plain })),
          el('span', { class: 'n', text: channel.present ? `${channel.cells}` : 'absent' }),
          live ? el('span', { class: 'n', style: 'color:var(--amber);width:52px;text-align:right', text: `${FL.num(live.hz, 1)} Hz` }) : null);
        FL.tipify(row, {
          title: channel.name,
          body: channel.detail,
          extra: channel.present
            ? `${channel.cells} cells here: ${channel.cells_left} left, ${channel.cells_right} right, ${channel.cells_unsided} unsided.`
            : 'No cell in this dataset matches this channel. A block addressing it would fail rather than invent one.',
          caution: channel.notes,
          meta: `${channel.modality_plain || channel.modality} · ${channel.units} · ${channel.fidelity_note || ''}`,
        });
        row.onclick = () => FL.views.connectome.channelModal(channel);
        list.append(row);
      }
    }
    list.scrollTop = scroll;
  },
  channelModal(channel) {
    const subtypes = Object.entries(channel.subtypes || {});
    FL.modal({
      title: channel.name,
      width: 640,
      body: el('div', {},
        el('p', { style: 'margin-top:0;font-size:14px', text: channel.plain }),
        el('p', { style: 'color:var(--text-2)', text: channel.detail }),
        el('dl', { class: 'kv' },
          el('dt', { text: 'Key' }), el('dd', { text: channel.key }),
          el('dt', { text: 'Role' }), el('dd', { text: channel.role }),
          el('dt', { text: 'Modality' }), el('dd', { text: channel.modality_plain || channel.modality }),
          el('dt', { text: 'Units' }), el('dd', { text: channel.units }),
          el('dt', { text: 'Cells here' }), el('dd', { text: channel.present ? `${channel.cells} (${channel.cells_left} L / ${channel.cells_right} R / ${channel.cells_unsided} unsided)` : 'none in this dataset' })),
        el('div', { class: 'note warn' }, el('b', { text: 'What is modelled: ' }), channel.fidelity_note || ''),
        channel.notes ? el('div', { class: 'note' }, channel.notes) : null,
        subtypes.length ? el('div', {}, el('h3', { style: 'margin:12px 0 6px', text: `Annotated types (${subtypes.length})` }),
          el('div', { style: 'font-family:var(--mono);font-size:11px;color:var(--text-2);max-height:180px;overflow:auto' },
            subtypes.map(([name, count]) => el('div', { text: `${name}  ${count}` })))) : null,
        (channel.evidence || []).length ? el('div', {}, el('h3', { style: 'margin:12px 0 6px', text: 'Published sources' }),
          channel.evidence.map((url) => el('div', {}, el('a', { href: url, target: '_blank', rel: 'noreferrer', text: url })))) : null),
      actions: [
        { label: 'Try it on the bench', kind: 'primary', run: () => { FL.views.bench.preset(channel); FL.go('bench'); return true; },
          tip: { title: 'Open the bench', body: 'Sets this channel up on the tuning bench so you can drive it, or read it, on its own.' } },
        { label: 'Close', kind: 'ghost', value: true },
      ],
    });
  },
};

/* =================================================================== BENCH */
FL.views.bench = {
  state: { drive: [], read: [], teach: [], duration: 200, learning: false },
  build(root) { this.root = root; },
  preset(channel) {
    if (channel.role === 'modulatory') this.state.teach = [channel.key];
    else if (channel.role === 'input') this.state.drive = [{ channel: channel.key, value: 1, low: 0, high: 1, side: 'both', curve: 'saturating' }];
    else this.state.read = [channel.key];
    if (this.root && this.root.classList.contains('on')) this.show();
  },
  show() {
    const root = this.root;
    root.innerHTML = '';
    root.append(head('Bench',
      'Set up, debug and tune without running a workflow. Drive one channel, read another, and check each piece of hardware on its own.',
      tipButton('Reset', { title: 'Clear the bench', body: 'Empties the drive and read lists. It does not change the network.' },
        () => { this.state = { drive: [], read: [], teach: [], duration: 200, learning: false }; this.show(); }, 'ghost')));
    if (!FL.state.session_loaded) {
      root.append(el('div', { class: 'panel' }, el('div', { class: 'empty' },
        el('h3', { text: 'No network loaded' }),
        el('div', { text: 'The probe drives real cells, so a dataset has to be loaded first.' }),
        el('div', { style: 'margin-top:10px' },
          tipButton('Go to Status', { title: 'Load a dataset', body: 'The Status page lists what is on this machine and loads one.' }, () => FL.go('overview'), 'primary')))));
      root.append(this.devicePanel());
      return;
    }
    const atlas = ((FL.state.session || {}).catalogue || {}).atlas || {};
    const channels = (atlas.channels || []).filter((c) => c.present);
    const inputs = channels.filter((c) => c.role === 'input');
    const outputs = channels.filter((c) => c.role !== 'input');
    const teachers = channels.filter((c) => c.role === 'modulatory');

    const driveBox = el('div');
    const renderDrive = () => {
      driveBox.innerHTML = '';
      if (!this.state.drive.length) driveBox.append(el('div', { class: 'note', text: 'Nothing is being driven. The network will run on its background activity alone, which is a useful baseline in itself.' }));
      this.state.drive.forEach((entry, index) => {
        const info = channels.find((c) => c.key === entry.channel) || {};
        driveBox.append(el('div', { style: 'border:1px solid var(--line);padding:9px;margin-bottom:8px;background:var(--deep)' },
          el('div', { class: 'spread' }, el('b', { text: info.name || entry.channel }),
            tipButton('Remove', { title: 'Stop driving this channel', body: 'Takes it out of the next probe.' },
              () => { this.state.drive.splice(index, 1); renderDrive(); }, 'danger small')),
          el('div', { style: 'color:var(--dim);font-size:11.5px;margin:3px 0 7px', text: info.plain || '' }),
          FL.field({
            label: 'Reading', kind: 'number', step: 0.01, value: entry.value,
            plain: `In the range below. Units: ${info.units || 'arbitrary'}.`,
            tip: 'This is the number a real sensor would report. It is rescaled to 0-1 using the range, then shaped into current.',
            onchange: (v) => { entry.value = v; },
          }),
          el('div', { class: 'row' },
            el('div', { style: 'flex:1' }, FL.field({
              label: 'Range low', kind: 'number', step: 0.01, value: entry.low,
              plain: 'The reading that means nothing is happening', tip: 'Can be higher than the high value, for a sensor that counts down as something approaches.',
              onchange: (v) => { entry.low = v; },
            })),
            el('div', { style: 'flex:1' }, FL.field({
              label: 'Range high', kind: 'number', step: 0.01, value: entry.high,
              plain: 'The reading that means full strength', tip: 'Readings outside the range are clamped.',
              onchange: (v) => { entry.high = v; },
            }))),
          el('div', { class: 'row' },
            el('div', { style: 'flex:1' }, FL.field({
              label: 'Side', kind: 'select', value: entry.side,
              options: [{ value: 'both', label: 'Both sides' }, { value: 'left', label: 'Left only' }, { value: 'right', label: 'Right only' }],
              plain: 'Which hemisphere to drive', tip: 'Driving one side is how you give the network something asymmetric to respond to.',
              onchange: (v) => { entry.side = v; },
            })),
            el('div', { style: 'flex:1' }, FL.field({
              label: 'Shape', kind: 'select', value: entry.curve,
              options: [{ value: 'saturating', label: 'Saturating' }, { value: 'linear', label: 'Straight' }],
              plain: 'How the reading becomes current', tip: 'Saturating matches the photoreceptor adapter: sensitive near zero, flattening at the top.',
              onchange: (v) => { entry.curve = v; },
            })))));
      });
    };
    renderDrive();

    const addDrive = el('select', {}, el('option', { value: '', text: 'Add a sense to drive...' }),
      inputs.map((c) => el('option', { value: c.key, text: `${c.name} - ${c.plain} (${c.cells})` })));
    addDrive.onchange = () => {
      if (!addDrive.value) return;
      this.state.drive.push({ channel: addDrive.value, value: 1, low: 0, high: 1, side: 'both', curve: 'saturating' });
      addDrive.value = ''; renderDrive();
    };
    FL.tipify(addDrive, { title: 'Drive a sense', body: 'Pick a group of real sensory cells and give it a reading. Every channel here exists in the loaded dataset; the count is how many cells it has.' });

    const teachBox = el('div', { class: 'row' });
    for (const channel of teachers) {
      const on = this.state.teach.includes(channel.key);
      teachBox.append(FL.tipify(el('button', {
        class: on ? 'primary small' : 'ghost small', text: channel.name,
        onclick: () => {
          this.state.teach = on ? this.state.teach.filter((k) => k !== channel.key) : [...this.state.teach, channel.key];
          this.show();
        },
      }), { title: channel.name, body: channel.detail, caution: 'An engineered current injection into identified cells. Not a reward, not a punishment, and nothing is experienced.' }));
    }

    const readBox = el('div', { style: 'max-height:260px;overflow:auto' });
    for (const channel of outputs) {
      const on = this.state.read.includes(channel.key);
      readBox.append(FL.tipify(el('label', { class: 'inline-check', style: 'padding:3px 0' },
        el('input', {
          type: 'checkbox', checked: on,
          onchange: (e) => {
            this.state.read = e.target.checked
              ? [...this.state.read, channel.key] : this.state.read.filter((k) => k !== channel.key);
          },
        }),
        el('span', {}, el('b', { text: channel.name }),
          el('span', { style: 'color:var(--dim)', text: ` - ${channel.plain}` }))),
        { title: channel.name, body: channel.detail, meta: `${channel.cells} cells · ${channel.units}` }));
    }

    const results = el('div', { id: 'bench-results' });
    const runProbe = async () => {
      try {
        FL.toast('Probing', `${this.state.duration} ms of neural time`);
        const result = await FL.api('/api/session/probe', {
          body: {
            drive: this.state.drive, teach: this.state.teach, read: this.state.read,
            duration_ms: this.state.duration, learning: this.state.learning,
          },
        });
        FL.viewport.push(result.viewport);
        FL.live.activity = result.activity;
        this.renderResults(results, result);
      } catch (error) { FL.toast('Probe failed', error.message, 'bad'); }
    };

    root.append(el('div', { class: 'grid g2' },
      el('div', { class: 'grid' },
        FL.panel('Drive', el('div', {}, driveBox, el('div', { style: 'margin-top:8px' }, addDrive)),
          { accent: 'accent-amber', tip: { title: 'What goes in', body: 'Each entry delivers current to one named group of real sensory cells for the length of the probe.' } }),
        FL.panel('Teaching pulse', el('div', {}, teachBox,
          el('div', { class: 'note warn', style: 'margin-top:9px' },
            'Selected clusters receive a full-strength pulse during the probe. This is a current injection into identified dopaminergic cells: it is how the plasticity rule is triggered, and it is not a reward or a punishment.')),
          { accent: 'accent-amber' }),
        FL.panel('Run the probe', el('div', {},
          FL.field({
            label: 'Neural time', kind: 'number', min: 1, max: 20000, step: 10, unit: 'ms', value: this.state.duration,
            plain: 'How long to let the network run', tip: 'Simulated time, not real time. Longer gives signals more chance to travel and traces more chance to overlap with dopamine.',
            onchange: (v) => { this.state.duration = v; },
          }),
          FL.field({
            label: 'Allow synapses to change', kind: 'bool', value: this.state.learning,
            plain: 'Let this probe write to memory',
            tip: 'Off by default so probing does not quietly alter a model you are about to run. Turn it on to test whether a pairing changes anything.',
            onchange: (v) => { this.state.learning = v; },
          }),
          el('div', { class: 'row' },
            tipButton('Probe', { title: 'Run the probe now', body: 'Delivers everything above, advances neural time, then reads the selected channels back.' }, runProbe, 'go wide')))),
      ),
      el('div', { class: 'grid' },
        FL.panel('Read back', readBox, { accent: 'accent-cyan', tip: { title: 'What comes out', body: 'Every channel ticked here is measured after the probe. Rates are means over the probe window.' } }),
        FL.panel('Result', results, { tag: 'last probe' }),
        this.devicePanel())));
  },

  renderResults(root, result) {
    root.innerHTML = '';
    const step = result.step || {};
    root.append(el('div', { class: 'grid g3', style: 'margin-bottom:10px' },
      FL.metric('Total spikes', (step.total_spikes || 0).toLocaleString(), `${step.duration_ms} ms window`),
      FL.metric('Compute', `${FL.num((step.compute_seconds || 0) * 1000, 0)} ms`, 'real time used', 'cyan'),
      FL.metric('Synapses changed', (step.memory || {}).changed_edges ?? 0,
        `of ${((step.memory || {}).plastic_edges || 0).toLocaleString()} plastic`, 'green')));
    if (!(result.read || []).length) {
      root.append(el('div', { class: 'note', text: 'Nothing was selected to read back. Tick a channel above and probe again.' }));
    }
    for (const reading of result.read || []) {
      root.append(el('div', { style: 'margin-bottom:9px' },
        el('div', { class: 'spread' }, el('b', { text: reading.name }),
          el('span', { class: 'mono', style: 'color:var(--amber)', text: `${FL.num(reading.hz, 2)} Hz` })),
        el('div', { style: 'color:var(--dim);font-size:11px', text: `${reading.active_cells} of ${reading.cells} cells fired · left ${FL.num(reading.left_hz, 1)} / right ${FL.num(reading.right_hz, 1)} · difference ${FL.num(reading.difference_hz, 2)}` }),
        FL.meter('', reading.hz, Math.max(10, reading.hz * 1.3), 'cyan')));
    }
    if ((step.stimuli || []).length) {
      root.append(el('h3', { style: 'margin:12px 0 5px', text: 'Delivered' }));
      for (const stimulus of step.stimuli) {
        root.append(el('div', { style: 'font-family:var(--mono);font-size:11px;color:var(--text-2)',
          text: `${stimulus.channel}: ${stimulus.cells} cells, peak ${FL.num(stimulus.peak_current_mv, 1)} mV` }));
      }
    }
    if (step.synthetic_fixture) {
      root.append(el('div', { class: 'note warn', text: 'These numbers came from the synthetic bench fixture. They describe a random graph.' }));
    }
  },

  devicePanel() {
    const devices = FL.state.devices || [];
    const body = el('div');
    for (const device of devices) {
      body.append(el('div', { class: 'channel-row' },
        el('span', { class: `lamp ${device.available ? 'ok' : ''}` }),
        el('div', { class: 'nm' }, el('b', { text: device.name }), el('span', { text: device.plain })),
        device.key === 'gpio'
          ? tipButton('Test', { title: 'Blink a pin', body: 'Sets a pin high, waits, then low. Confirms wiring before a workflow depends on it.' },
            () => FL.views.bench.gpioTest(), 'ghost small')
          : el('span', { class: `chip ${device.available ? 'good' : ''}`, text: device.available ? 'ready' : 'absent' })));
    }
    return FL.panel('Hardware', body, {
      tag: 'this machine',
      tip: { title: 'Device check', body: 'What this machine can reach right now. A missing driver is named with the command that installs it.' },
      actions: [tipButton('Re-check', { title: 'Probe again', body: 'Re-imports every driver and re-lists serial ports.' },
        async () => { await FL.api('/api/devices?refresh=true'); await FL.refresh(); FL.views.bench.show(); }, 'ghost small')],
    });
  },

  gpioTest() {
    FL.modal({
      title: 'Pin test',
      body: el('div', {},
        el('p', { text: 'The bench cannot drive pins on its own: pin output happens inside a workflow, so that everything which touches hardware is written down in one place and appears in the run log.' }),
        el('div', { class: 'note' }, 'Make a project from the "Pin in, pin out" template. It reads a pin, runs the network briefly and drives an output pin, and you can watch every step of it happen.'),
        el('div', { class: 'note warn' }, 'Raspberry Pi pins are 3.3 V. A 5 V signal on an input can destroy the board, and a motor drawn directly from a pin will too.')),
      actions: [
        { label: 'Create that project', kind: 'primary', run: () => { FL.views.projects.createModal('pins'); return true; } },
        { label: 'Close', kind: 'ghost', value: true },
      ],
    });
  },
};

/* ================================================================ TRAINING */
FL.views.training = {
  build(root) { this.root = root; },
  async show() {
    const root = this.root;
    root.innerHTML = '';
    root.append(head('Training',
      'Run a training schedule, then run the controls that decide whether anything was actually learned.',
      tipButton('What counts as learning', { title: 'The standard to meet', body: 'What has to be true before a result means anything, and what this platform can and cannot show.' },
        () => this.standardModal(), 'ghost')));

    let projects = [];
    try { projects = (await FL.api('/api/projects')).projects; } catch { /* listed empty below */ }
    const withHistory = projects.filter((p) => p.runs > 0);

    root.append(el('div', { class: 'grid g2' },
      el('div', { class: 'grid' },
        FL.panel('Training run', el('div', {},
          el('p', { style: 'margin-top:0;color:var(--text-2)' },
            'A training workflow shows the network something, reads what it did, scores it, and delivers a teaching pulse into identified dopaminergic cells. The "Two-choice training" template is exactly that arrangement and runs with no hardware.'),
          el('div', { class: 'grid g3', style: 'margin:10px 0' },
            FL.metric('Plastic synapses', (((FL.state.session || {}).catalogue || {}).compartments || {}).plastic_edges?.toLocaleString() || '--',
              'existing KC to MBON connections'),
            FL.metric('Compartments', (((FL.state.session || {}).catalogue || {}).compartments || {}).output_neurons || '--',
              'output neurons being written to', 'cyan'),
            FL.metric('Learning', (FL.state.settings || {}).learning ? 'ON' : 'FROZEN',
              'default for new runs', (FL.state.settings || {}).learning ? 'green' : 'red')),
          el('div', { class: 'row' },
            tipButton('New training project', { title: 'Start from the training template', body: 'Trial loop, choice readout, outcome scoring and the teaching pulse, already wired together.' },
              () => FL.views.projects.createModal('train'), 'primary'),
            tipButton('Open the Bench', { title: 'Check the pairing works', body: 'Drive a sense, deliver a teaching pulse, and see whether any synapse changed - before committing to a long run.' },
              () => FL.go('bench'), 'ghost'))),
          { accent: 'accent-amber' }),

        FL.panel('The controls', el('div', {},
          el('p', { style: 'margin-top:0;color:var(--text-2)' },
            'Run each of these against the same starting model, and compare. Without them, a rising score is not evidence of anything.'),
          el('div', {}, [
            ['Frozen synapses', 'Same workflow, same inputs, "Allow synapses to change" off. If the score rises anyway, the change was not in the synapses.',
              'Turn it off in the run settings, or drop a "Freeze or unfreeze learning" block at the start.'],
            ['Shuffled teaching', 'Deliver the reward-linked pulse after the wrong answers and the punishment-linked pulse after the right ones, or randomise which. If the score still rises, the pairing is not what is doing it.',
              'Swap the two channels on the "Teach from the outcome" block.'],
            ['Random input', 'Replace the real input with the Random number block. Tells you how much of the behaviour comes from what is being sensed.',
              'Swap the source block; leave everything else identical.'],
            ['Memory reset', 'Restore the starting model and run again. Any benefit that survives a reset came from the fixed wiring, not from what was learned.',
              'Load the parent model from the Model library before the run.'],
            ['Fresh starts', 'Run the whole thing several times from the untouched baseline. One lucky run is not a result.',
              'Turn off "Keep what was learned" on the trial block, or reload the baseline model each time.'],
          ].map(([title, why, how]) => el('div', { style: 'border-left:2px solid var(--line-2);padding:7px 11px;margin-bottom:8px' },
            el('b', { text: title }),
            el('div', { style: 'color:var(--text-2);font-size:12px;margin-top:2px', text: why }),
            el('div', { style: 'color:var(--dim);font-size:11px;margin-top:3px', text: how })))),
        ), { accent: 'accent-cyan' })),

      el('div', { class: 'grid' },
        FL.panel('Retrain from a saved model', el('div', {},
          el('p', { style: 'margin-top:0;color:var(--text-2)' },
            'Load a model, then run a training workflow against it. Everything it had learned carries over; the new run continues from there.'),
          el('div', { class: 'row' },
            tipButton('Model library', { title: 'Pick a starting model', body: 'Load one into the current network, then run a training project against it.' },
              () => FL.go('models'), 'primary'),
            tipButton('Save the current network', { title: 'Snapshot now', body: 'Writes what the loaded network currently holds, with a label. Do this before a run you might want to undo.' },
              () => FL.views.models.saveModal(), 'ghost'))),
          {}),

        FL.panel('Recent runs', withHistory.length
          ? el('div', {}, withHistory.slice(0, 12).map((project) => el('div', { class: 'list-item' },
            el('div', { class: 'grow' },
              el('div', { class: 'nm', text: project.name }),
              el('div', { class: 'sub', text: `${project.runs} runs · last edited ${FL.ago(project.updated)}` })),
            tipButton('History', { title: 'Run history', body: 'Every run of this project: how it ended, how it scored, and where its files are.' },
              () => FL.views.training.historyModal(project.id), 'ghost small'))))
          : el('div', { class: 'empty' }, el('h3', { text: 'No runs yet' }),
            el('div', { text: 'Run history appears here once a project has run at least once.' })),
          { tag: `${withHistory.length}` }))));
  },

  async historyModal(projectId) {
    let project;
    try { project = await FL.api(`/api/projects/${projectId}`); }
    catch (error) { FL.toast('Could not read history', error.message, 'bad'); return; }
    const runs = (project.history || []).slice().reverse();
    FL.modal({
      title: `${project.name} - run history`,
      width: 820,
      body: runs.length ? el('table', {},
        el('thead', {}, el('tr', {}, el('th', { text: 'Run' }), el('th', { text: 'Ended' }),
          el('th', { text: 'Passes' }), el('th', { text: 'Steps' }), el('th', { text: 'Score' }), el('th', { text: 'Goal' }))),
        el('tbody', {}, runs.map((run) => {
          const goal = run.goal || {};
          const state = run.state || {};
          return el('tr', {},
            el('td', { class: 'mono' }, run.run_id || '--',
              run.synthetic_fixture ? el('div', { style: 'color:var(--amber);font-size:10px', text: 'fixture' }) : null),
            el('td', {}, el('div', { text: state.reason || '--' }),
              el('div', { style: 'color:var(--dim);font-size:10.5px', text: state.detail || '' })),
            el('td', { class: 'mono', text: String(run.iterations ?? '--') }),
            el('td', { class: 'mono', text: String(run.neural_steps ?? '--') }),
            el('td', { class: 'mono', text: goal.attempts ? `${goal.correct}/${goal.attempts} (${FL.num(goal.accuracy * 100, 0)}%)` : '--' }),
            el('td', { style: 'font-size:11.5px;color:var(--text-2)', text: (goal.goal || '').slice(0, 80) }));
        })))
        : el('div', { class: 'empty', text: 'This project has not run yet.' }),
      actions: [{ label: 'Close', kind: 'ghost', value: true }],
    });
  },

  standardModal() {
    FL.modal({
      title: 'What would count as learning',
      width: 720,
      body: el('div', {},
        el('p', { text: 'This platform can show three things honestly: that an input reaches the memory cells, that the identified dopaminergic cells fire when a teaching pulse is delivered, and that pairing the two changes eligible synapses. Those are mechanism checks.' }),
        el('p', { style: 'color:var(--text-2)' }, 'None of them is evidence that the network learned anything useful. Synapses change under this rule whenever dopamine arrives while cue activity is still eligible - including when the dopamine has nothing to do with the task.'),
        el('div', { class: 'note warn' },
          el('b', { text: 'A score is not a result. ' }),
          'A workflow that always answers the same way scores well on a task where that answer is usually right. Compare against the frozen-synapse run and the shuffled-teaching run before drawing any conclusion.'),
        el('p', { style: 'color:var(--text-2)' }, 'To claim learned behaviour you would need held-out material the network has not seen, independent starts, frozen-weight and shuffled-reinforcement controls, equal opportunity for every condition, evidence the benefit persists, and evidence it disappears when the learned weights are reset.'),
        el('div', { class: 'note' }, 'The full statement of what is and is not modelled is in docs/model.md, and what has actually been checked is in docs/validation.md.')),
      actions: [{ label: 'Close', kind: 'ghost', value: true }],
    });
  },
};

/* ================================================================== MODELS */
FL.views.models = {
  build(root) { this.root = root; },
  async show() {
    const root = this.root;
    root.innerHTML = '';
    root.append(head('Model library',
      'Saved networks: membrane state, traces and every synaptic efficacy, with a label and notes so you know what each one is.',
      tipButton('Save the current network', { title: 'Snapshot now', body: 'Writes what the loaded network holds right now. Needs a dataset loaded.' },
        () => this.saveModal(), 'primary')));
    let models = [];
    try { models = (await FL.api('/api/models')).models; }
    catch (error) { FL.toast('Could not list models', error.message, 'bad'); }
    if (!models.length) {
      root.append(el('div', { class: 'panel' }, el('div', { class: 'empty' },
        el('h3', { text: 'No models saved yet' }),
        el('div', { text: 'Save one from here, or drop a "Save the network" block into a workflow to snapshot automatically - for example only on a correct trial.' }))));
      return;
    }
    const table = el('table', {}, el('thead', {}, el('tr', {},
      el('th', { text: 'Label' }), el('th', { text: 'Memory' }), el('th', { text: 'Neural time' }),
      el('th', { text: 'Compartments' }), el('th', { text: 'Saved' }), el('th', { text: '' }))));
    const body = el('tbody');
    for (const model of models) {
      const memory = model.memory || {};
      body.append(el('tr', {},
        el('td', {},
          el('div', { style: 'font-size:13px' }, model.label,
            model.synthetic_fixture ? el('span', { class: 'chip', style: 'margin-left:6px', text: 'fixture' }) : null,
            model.available ? null : el('span', { class: 'chip bad', style: 'margin-left:6px', text: 'file missing' })),
          el('div', { style: 'color:var(--dim);font-size:11px', text: model.notes || 'no notes' })),
        FL.tipify(el('td', { class: 'mono', text: `${(memory.changed_edges ?? 0).toLocaleString()} changed` }),
          { title: 'Synaptic memory', body: `${(memory.plastic_edges || 0).toLocaleString()} plastic synapses; ${(memory.changed_edges || 0).toLocaleString()} differ from their starting efficacy. Mean efficacy ${FL.num(memory.mean_efficacy, 4)} of baseline.`, meta: memory.sha256 ? `weights ${memory.sha256.slice(0, 16)}` : '' }),
        el('td', { class: 'mono', text: `${FL.num((model.brain_ms || 0) / 1000, 1)} s` }),
        el('td', { class: 'mono', text: model.compartments || '--' }),
        el('td', { class: 'mono', text: FL.ago(model.created) }),
        el('td', {}, el('div', { class: 'row tight' },
          tipButton('Load', { title: 'Restore this model', body: 'Replaces what the loaded network currently holds. It is refused unless the graph, compartments and parameters match exactly, so a model can never be quietly applied to a different network.' },
            () => this.load(model), 'primary small'),
          tipButton('Edit', { title: 'Label and notes', body: 'Rename it and write down what it was trained on. Worth doing now rather than later.' },
            () => this.editModal(model), 'ghost small'),
          tipButton('Details', { title: 'Provenance', body: 'Exactly what this came from: dataset, parameters, source checksums.' },
            () => this.detailModal(model), 'ghost small'),
          tipButton('Delete', { title: 'Remove this model', body: 'Takes it out of the library, and optionally deletes the checkpoint file too.' },
            () => this.remove(model), 'danger small')))));
    }
    table.append(body);
    root.append(FL.panel('Saved models', table, { pad0: true, tag: `${models.length}` }));
    root.append(el('div', { class: 'note', style: 'margin-top:10px' },
      el('b', { text: 'What a checkpoint contains: ' }),
      'every synaptic efficacy, every membrane voltage, the eligibility and modulator traces, and a record of the exact graph and parameters it came from. Loading one into a different network is refused, not approximated.'));
  },

  saveModal() {
    if (!FL.state.session_loaded) { FL.toast('No network loaded', 'Load a dataset before saving a model.', 'bad'); return; }
    let label = '';
    let notes = '';
    FL.modal({
      title: 'Save the current network',
      body: el('div', {},
        FL.field({ label: 'Label', plain: 'A name you will recognise later.', tip: 'Shown in the library and in run summaries.', value: '', placeholder: 'after 40 left-turn trials', onchange: (v) => { label = v; } }),
        FL.field({ label: 'Notes', kind: 'code', plain: 'What it was trained on, and anything odd about the run.', tip: 'A checkpoint with no notes is nearly useless a week later. Write down the task, the number of trials, and which controls you ran.', value: '', onchange: (v) => { notes = v; } }),
        FL.state.session && FL.state.session.synthetic_fixture
          ? el('div', { class: 'note warn', text: 'This network is the synthetic bench fixture. The saved model is a snapshot of a random graph.' }) : null),
      actions: [
        { label: 'Cancel', kind: 'ghost', value: false },
        {
          label: 'Save', kind: 'primary',
          run: async () => {
            try {
              await FL.api('/api/models', { body: { label: label || 'snapshot', notes, project_id: FL.project ? FL.project.id : '' } });
              FL.views.models.show(); FL.refresh();
              return true;
            } catch (error) { FL.toast('Could not save', error.message, 'bad'); return false; }
          },
        },
      ],
    });
  },

  editModal(model) {
    let label = model.label;
    let notes = model.notes;
    let tags = (model.tags || []).join(', ');
    FL.modal({
      title: 'Edit model',
      body: el('div', {},
        FL.field({ label: 'Label', plain: 'What to call it.', tip: 'Renaming does not touch the checkpoint file.', value: label, onchange: (v) => { label = v; } }),
        FL.field({ label: 'Notes', kind: 'code', plain: 'What it was trained on.', tip: 'Include the controls you ran, not just the result.', value: notes, onchange: (v) => { notes = v; } }),
        FL.field({ label: 'Tags', plain: 'Comma separated.', tip: 'Useful for grouping: "control", "frozen", "shuffled", "baseline".', value: tags, onchange: (v) => { tags = v; } })),
      actions: [
        { label: 'Cancel', kind: 'ghost', value: false },
        {
          label: 'Save changes', kind: 'primary',
          run: async () => {
            try {
              await FL.api(`/api/models/${model.id}`, {
                method: 'PATCH',
                body: { label, notes, tags: tags.split(',').map((t) => t.trim()).filter(Boolean) },
              });
              FL.views.models.show();
              return true;
            } catch (error) { FL.toast('Could not save', error.message, 'bad'); return false; }
          },
        },
      ],
    });
  },

  detailModal(model) {
    const provenance = model.provenance || {};
    FL.modal({
      title: model.label,
      width: 760,
      body: el('div', {},
        el('dl', { class: 'kv' },
          el('dt', { text: 'Saved' }), el('dd', { text: FL.clock(model.created) }),
          el('dt', { text: 'File' }), el('dd', { text: model.path }),
          el('dt', { text: 'Size' }), el('dd', { text: FL.bytes(model.bytes) }),
          el('dt', { text: 'Checksum' }), el('dd', { text: model.sha256 || '--' }),
          el('dt', { text: 'Dataset' }), el('dd', { text: model.dataset || '--' }),
          el('dt', { text: 'Compartments' }), el('dd', { text: model.compartments || '--' }),
          el('dt', { text: 'Neural time' }), el('dd', { text: `${FL.num((model.brain_ms || 0) / 1000, 2)} s` }),
          el('dt', { text: 'Steps' }), el('dd', { text: String(model.steps || 0) })),
        el('h3', { style: 'margin:12px 0 6px', text: 'Memory' }),
        el('dl', { class: 'kv' },
          ...Object.entries(model.memory || {}).flatMap(([k, v]) =>
            [el('dt', { text: k.replace(/_/g, ' ') }), el('dd', { text: String(v) })])),
        provenance.config ? el('div', {}, el('h3', { style: 'margin:12px 0 6px', text: 'Parameters at save time' }),
          el('div', { style: 'font-family:var(--mono);font-size:11px;color:var(--text-2);max-height:170px;overflow:auto' },
            Object.entries(provenance.config).map(([k, v]) => el('div', { text: `${k} = ${v}` })))) : null,
        model.synthetic_fixture ? el('div', { class: 'note warn', text: 'Saved from the synthetic bench fixture.' }) : null),
      actions: [{ label: 'Close', kind: 'ghost', value: true }],
    });
  },

  async load(model) {
    if (!FL.state.session_loaded) { FL.toast('No network loaded', 'Load the matching dataset first.', 'bad'); return; }
    if (!await FL.confirm('Load this model?',
      `Everything the loaded network currently holds is replaced by "${model.label}". Save the current state first if you want to keep it.`,
      'Load model', 'primary')) return;
    try {
      await FL.api(`/api/models/${model.id}/load`, { body: {} });
      FL.toast('Model loaded', model.label, 'good');
      FL.refresh();
    } catch (error) { FL.toast('Could not load', error.message, 'bad'); }
  },

  async remove(model) {
    const result = await FL.modal({
      title: 'Delete model',
      body: el('div', {}, el('p', { text: `Remove "${model.label}" from the library?` }),
        el('div', { class: 'note', text: 'The library entry goes either way. Choose whether the checkpoint file on disk goes with it.' }),
        el('div', { class: 'mono', style: 'margin-top:8px;color:var(--dim);font-size:11px', text: model.path })),
      actions: [
        { label: 'Cancel', kind: 'ghost', value: null },
        { label: 'Remove entry only', kind: 'ghost', value: 'entry' },
        { label: 'Delete file too', kind: 'danger', value: 'file' },
      ],
    });
    if (!result) return;
    try {
      await FL.api(`/api/models/${model.id}?remove_file=${result === 'file'}`, { method: 'DELETE' });
      this.show(); FL.refresh();
    } catch (error) { FL.toast('Could not delete', error.message, 'bad'); }
  },
};

/* =========================================================== HOUSEKEEPING */
FL.views.housekeeping = {
  build(root) { this.root = root; },
  async show() {
    const root = this.root;
    root.innerHTML = '';
    root.append(head('Housekeeping',
      'Get the connectome onto this machine, prove it is intact, and keep the workspace tidy.',
      tipButton('Re-check hardware', { title: 'Probe the drivers again', body: 'Re-imports every optional package and re-lists serial ports.' },
        async () => { await FL.api('/api/devices?refresh=true'); FL.refresh(); this.show(); }, 'ghost')));

    let info = { datasets: [], sources: {}, steps: [] };
    try { info = await FL.api('/api/datasets'); } catch (error) { FL.toast('Could not read datasets', error.message, 'bad'); }
    const total = Object.values(info.sources).reduce((sum, s) => sum + s.bytes, 0);

    const left = el('div', { class: 'grid' });
    const right = el('div', { class: 'grid' });

    left.append(FL.panel('Download the connectome', el('div', {},
      el('p', { style: 'margin-top:0;color:var(--text-2)' },
        `Three files from the MaleCNS v1.0 release, about ${FL.bytes(total)} in total. They are checked against SHA-256 locks committed in this repository, normalised, and compiled into the arrays the simulator loads.`),
      el('div', {}, Object.entries(info.sources).map(([name, source]) => FL.tipify(el('div', { class: 'channel-row' },
        el('div', { class: 'nm' }, el('b', { text: name }), el('span', { text: FL.bytes(source.bytes) })),
        el('span', { class: 'n', text: source.sha256.slice(0, 12) })),
        { title: name, body: 'Downloaded from the release and checked against the committed checksum before anything is installed.', meta: source.url }))),
      el('div', { id: 'prep-progress', style: 'margin:12px 0' }),
      el('div', { class: 'row' },
        tipButton('Download and prepare', {
          title: 'Fetch and compile',
          body: 'Downloads anything missing, checks every file, normalises the annotations and compiles the graph. Around 1.1 GB and several minutes on a good connection, longer on a Raspberry Pi.',
          caution: 'Needs about 4 GB of free space for the download and the compiled arrays together.',
        }, () => this.prepare(), 'primary'),
        tipButton('Verify what is here', { title: 'Re-check a prepared dataset', body: 'Re-reads every compiled array, the source annotations and the neuron ordering, and compares them against the locks. Run it if anything seems wrong.' },
          () => this.verify(), 'ghost'),
        tipButton('Build the bench fixture', { title: 'Synthetic test graph', body: 'A small randomly wired graph with the same shape as the real thing. It exercises the whole program in seconds and tells you nothing about flies.' },
          () => this.buildFixture(), 'ghost')),
      el('div', { class: 'note' }, el('b', { text: 'Where it goes: ' }),
        `${(FL.state.workspace || {}).root || '~/.flylab'}/datasets/. Set a different workspace with --workspace when starting.`)),
      { accent: 'accent-amber', tag: 'about 1.1 GB' }));

    left.append(FL.panel('What preparing actually does',
      el('div', {}, info.steps.map((step, index) => el('div', { style: 'display:flex;gap:11px;padding:7px 0;border-bottom:1px solid #16202a' },
        el('span', { class: 'mono', style: 'color:var(--amber)', text: String(index + 1).padStart(2, '0') }),
        el('div', {}, el('b', { text: step.label }),
          el('div', { style: 'color:var(--text-2);font-size:11.5px', text: step.detail }))))),
      { tip: { title: 'The pipeline', body: 'Every stage is checked. If a checksum does not match, nothing is installed and the error says which file.' } }));

    right.append(FL.panel('Datasets on disk',
      info.datasets.length ? el('div', {}, info.datasets.map((entry) => el('div', {
        style: 'border:1px solid var(--line);padding:10px;margin-bottom:8px;background:var(--deep)',
      },
        el('div', { class: 'spread' },
          el('b', { text: entry.fixture ? 'Bench fixture (synthetic)' : 'MaleCNS v1.0' }),
          el('span', { class: `chip ${entry.ready ? 'good' : 'bad'}`, text: entry.ready ? 'compiled' : 'incomplete' })),
        el('div', { class: 'mono', style: 'font-size:10.5px;color:var(--dim);margin:4px 0', text: entry.root }),
        el('dl', { class: 'kv' },
          el('dt', { text: 'Cells' }), el('dd', { text: (entry.manifest.neurons || 0).toLocaleString() }),
          el('dt', { text: 'Connections' }), el('dd', { text: (entry.manifest.edges || 0).toLocaleString() }),
          el('dt', { text: 'Sources' }), el('dd', { text: `${FL.bytes(entry.downloaded_bytes)} of ${FL.bytes(entry.required_bytes)}` }),
          el('dt', { text: 'Graph file' }), el('dd', { text: FL.bytes(entry.graph_bytes) })),
        entry.manifest.warning ? el('div', { class: 'note warn', text: entry.manifest.warning }) : null,
        el('div', { class: 'row', style: 'margin-top:8px' },
          tipButton('Load', { title: 'Load this network', body: 'Reads the graph, compiles the kernel if needed and indexes every channel.' },
            () => FL.views.overview.loadDataset(entry.root), 'primary small'),
          tipButton('Verify', { title: 'Check it again', body: 'Re-runs every checksum against the committed locks.' },
            () => this.verify(entry.root), 'ghost small')))))
        : el('div', { class: 'empty', text: 'Nothing on disk yet.' }),
      { tag: `${info.datasets.length}` }));

    const host = FL.state.host || {};
    right.append(FL.panel('Dependencies',
      el('div', {}, (host.capabilities || []).map((capability) => FL.tipify(el('div', { class: 'channel-row' },
        el('span', { class: `lamp ${capability.available ? 'ok' : ''}` }),
        el('div', { class: 'nm' }, el('b', { text: capability.name }), el('span', { text: capability.plain })),
        el('span', { class: 'n', style: 'font-size:10px', text: capability.available ? capability.backend : 'not installed' })),
        {
          title: capability.name,
          body: capability.available ? `Using ${capability.backend}.` : `Not installed. ${capability.install_hint}`,
          extra: capability.detail, meta: capability.platform_note,
        }))),
      {
        tag: 'optional',
        tip: { title: 'Optional packages', body: 'Everything here can be installed later. A block that needs one reports which package is missing rather than failing obscurely.' },
      }));

    right.append(FL.panel('Workspace',
      el('div', {},
        el('dl', { class: 'kv' },
          el('dt', { text: 'Folder' }), el('dd', { text: (FL.state.workspace || {}).root || '--' }),
          el('dt', { text: 'Projects' }), el('dd', { text: String((FL.state.workspace || {}).projects || 0) }),
          el('dt', { text: 'Models' }), el('dd', { text: `${(FL.state.workspace || {}).models_available || 0} available of ${(FL.state.workspace || {}).models || 0}` }),
          el('dt', { text: 'On disk' }), el('dd', { text: FL.bytes((FL.state.workspace || {}).disk_bytes || 0) })),
        el('div', { class: 'note' },
          'Everything in the workspace is plain JSON beside its files. It stays readable without this program, and it is safe to back up or move.')),
      {}));

    root.append(el('div', { class: 'grid g2' }, left, right));
    if (FL.state.task && FL.state.task.status === 'working') this.progress(FL.state.task);
  },

  progress(task) {
    const box = $('#prep-progress');
    if (!box) return;
    box.innerHTML = '';
    if (!task || !task.name || task.status === 'idle') return;
    box.append(
      el('div', { class: 'spread', style: 'margin-bottom:5px' },
        el('b', { text: task.name }),
        el('span', { class: 'mono', style: 'font-size:11px;color:var(--text-2)', text: task.status })),
      el('div', { class: `progress ${task.status === 'working' && !task.progress ? 'indet' : ''}` },
        el('i', { style: `width:${Math.round((task.progress || 0) * 100)}%` })),
      el('div', { id: 'prep-detail', class: 'mono', style: 'font-size:11px;color:var(--dim);margin-top:5px', text: task.detail || '' }));
    if (task.status === 'failed') box.append(el('div', { class: 'note bad', text: task.detail }));
    if (task.status === 'done') box.append(el('div', { class: 'note good', text: task.detail }));
  },

  detail(event) {
    const box = $('#prep-detail');
    if (!box || !event) return;
    const read = event.read || 0;
    const total = event.total || 0;
    box.textContent = total
      ? `${event.step}: ${event.file || ''} ${FL.bytes(read)} of ${FL.bytes(total)} (${Math.round((read / total) * 100)}%)`
      : `${event.step}: ${event.status || event.file || ''}`;
  },

  async prepare() {
    if (!await FL.confirm('Download the connectome?',
      'About 1.1 GB is downloaded, checked and compiled. Allow around 4 GB of free space and several minutes. You can keep using the rest of the interface while it runs.',
      'Start download', 'primary')) return;
    try { await FL.api('/api/datasets/prepare', { body: {} }); FL.toast('Download started', 'Progress appears on this page.'); }
    catch (error) { FL.toast('Could not start', error.message, 'bad'); }
  },

  async verify(path) {
    try {
      const report = await FL.api('/api/datasets/verify', { body: { path: path || '' } });
      FL.modal({
        title: 'Verification report',
        body: el('div', {},
          el('dl', { class: 'kv' },
            el('dt', { text: 'Folder' }), el('dd', { text: report.root }),
            el('dt', { text: 'Release' }), el('dd', { text: report.release }),
            el('dt', { text: 'Cells' }), el('dd', { text: (report.neurons || 0).toLocaleString() }),
            el('dt', { text: 'Connections' }), el('dd', { text: (report.directed_edges || 0).toLocaleString() }),
            el('dt', { text: 'Mapped receptors' }), el('dd', { text: String(report.retina_mapped) })),
          report.checks.length
            ? el('div', { class: 'note good' }, el('b', { text: 'Checked: ' }), report.checks.join('; '))
            : el('div', { class: 'note warn', text: 'No source checks apply to a fixture: there is no released file to compare it against.' }),
          report.warning ? el('div', { class: 'note warn', text: report.warning }) : null),
        actions: [{ label: 'Close', kind: 'ghost', value: true }],
      });
    } catch (error) { FL.toast('Verification failed', error.message, 'bad'); }
  },

  async buildFixture() {
    if (!await FL.confirm('Build the bench fixture?',
      'A small randomly wired graph is written to the workspace so you can try the software without downloading anything. Every result produced from it is labelled as synthetic, in the interface and in saved files.',
      'Build fixture', 'primary')) return;
    try {
      const result = await FL.api('/api/datasets/fixture', { body: {} });
      FL.toast('Fixture built', `${(result.manifest.neurons || 0).toLocaleString()} cells`, 'good');
      await FL.views.overview.loadDataset(result.root);
      if (FL.page === 'housekeeping') this.show();
    } catch (error) { FL.toast('Could not build', error.message, 'bad'); }
  },
};

/* ================================================================ SETTINGS */
FL.views.settings = {
  build(root) { this.root = root; },
  show() {
    const root = this.root;
    const settings = FL.state.settings || {};
    root.innerHTML = '';
    root.append(head('Settings',
      'Defaults for new sessions and runs, how the interface behaves, and who is allowed to reach it.'));

    const save = async (changes) => {
      try {
        const result = await FL.api('/api/settings', { body: changes });
        FL.state.settings = result.settings;
        FL.applyTheme();
        FL.refreshStatus();
        if (result.restart_required) {
          FL.toast('Saved - restart needed', 'The listening address changes when FLYLAB restarts. Access rules are already in force.', 'good');
        }
        return result;
      } catch (error) { FL.toast('Could not save', error.message, 'bad'); return null; }
    };
    this.save = save;

    const left = el('div', { class: 'grid' });
    const right = el('div', { class: 'grid' });

    /* --- network access */
    const copy = (url) => navigator.clipboard.writeText(url).then(
      () => FL.toast('Copied', url, 'good'), () => FL.toast('Could not copy', url, 'bad'));
    const share = settings.share_url;
    const addressPanel = el('div', {});
    if (settings.lan_enabled && share) {
      addressPanel.append(
        el('div', { class: 'note good' },
          el('b', { text: 'Open from any device on this network: ' }),
          el('a', { href: share, target: '_blank', rel: 'noreferrer', class: 'mono', text: share })),
        el('div', { class: 'row', style: 'margin-bottom:8px' },
          tipButton('Copy address', { title: 'Copy the address to share', body: 'Copies the full address, including the access key if one is required, ready to paste into a browser on a phone or another computer.' },
            () => copy(share), 'primary small'),
          ...(settings.addresses || []).slice(1).map((address) => FL.tipify(
            el('span', { class: 'chip', style: 'cursor:pointer',
              text: `${address}:${settings.port}`,
              onclick: () => copy(`http://${address}:${settings.port}/${settings.access_key && settings.lan_require_key ? `?key=${settings.access_key}` : ''}`) }),
            { title: 'Another address for this machine', body: 'This machine answers on more than one interface. Click to copy this one instead.' }))));
    } else if (settings.lan_enabled) {
      addressPanel.append(el('div', { class: 'note warn' },
        'Listening on every interface, but no network address could be found for this machine. It may not be attached to a network yet.'));
    } else {
      addressPanel.append(el('div', { class: 'note' },
        'Local only. Nothing outside this machine can connect, whatever address it tries.'));
    }

    left.append(FL.panel('Who can reach this interface', el('div', {},
      addressPanel,
      FL.field({
        label: 'Share on this network', kind: 'bool', value: settings.lan_enabled,
        plain: 'Listen on every interface, so other devices can open this interface at this machine’s address',
        tip: 'On by default, because the usual place to run FLYLAB is a headless Raspberry Pi or a workshop machine driven from a laptop. Off binds loopback only, and every request from anywhere else is refused.',
        onchange: async (v) => {
          if (!v && !await FL.confirm('Keep this to this machine only?',
            'Other devices will be locked out immediately, and FLYLAB will bind loopback only the next time it starts. Anything you are running from a phone or another computer will stop working.',
            'Local only', 'danger')) { this.show(); return; }
          await save({ lan_enabled: v }); this.show();
        },
      }),
      FL.field({
        label: 'Require an access key', kind: 'bool', value: settings.lan_require_key,
        plain: 'Devices on the network must carry a key in the address',
        tip: 'Off by default, so the address just works. Turn it on for a network you do not control. Connections from this machine itself never need the key. It is a single shared secret over plain HTTP: appropriate for a workshop network and nothing more.',
        onchange: async (v) => { await save({ lan_require_key: v }); this.show(); },
      }),
      settings.lan_enabled && settings.lan_require_key ? el('div', {},
        el('div', { class: 'note warn' }, el('b', { text: 'Access key: ' }),
          el('span', { class: 'mono', text: settings.access_key })),
        el('div', { class: 'row' },
          tipButton('New key', { title: 'Replace the access key', body: 'Generates a new one. Every device using the old address stops working immediately.' },
            async () => {
              if (await FL.confirm('Replace the access key?', 'Any device using the old address will be locked out until you give it the new one.', 'Replace key')) {
                await save({ regenerate_key: true }); this.show();
              }
            }, 'danger small'))) : null,
      settings.open_to_network ? el('div', { class: 'note warn' },
        el('b', { text: 'No access key is required. ' }),
        'Anyone who can reach this machine on the network can open this interface, and it can drive GPIO pins, move the pointer and start programs here.') : null,
      FL.field({
        label: 'Port', kind: 'number', min: 1, max: 65535, step: 1, value: settings.port,
        plain: 'Which port to listen on', tip: 'Takes effect when FLYLAB restarts. 8765 by default.',
        onchange: (v) => save({ port: v }),
      }),
      el('div', { class: 'mono', style: 'font-size:11.5px;margin-top:8px' },
        (settings.urls || []).map((url) => el('div', {},
          el('a', { href: url, target: '_blank', rel: 'noreferrer', text: url })))),
      el('div', { class: 'note' }, el('b', { text: 'How this is enforced: ' }),
        'the listening address follows this setting when FLYLAB starts, and every request and socket is checked against the current setting again as it arrives. Switching to local only locks out remote devices immediately, without a restart.')),
      { accent: settings.open_to_network ? 'accent-amber' : '',
        tag: settings.lan_enabled ? (settings.lan_require_key ? 'shared, key required' : 'shared, open') : 'local only' }));

    /* --- session defaults */
    left.append(FL.panel('Network defaults', el('div', {},
      el('p', { style: 'margin-top:0;color:var(--text-2)', text: 'Applied when a dataset is loaded. Changing them takes effect the next time you load.' }),
      FL.field({
        label: 'Learning compartments', kind: 'select', value: settings.compartments,
        options: Object.entries((((FL.state.session || {}).catalogue || {}).compartment_presets) || {
          alpha1_gamma1pedc: { label: 'alpha1 + gamma1pedc (validated pair)' },
          mushroom_body_full: { label: 'Whole mushroom body' },
        }).map(([value, entry]) => ({ value, label: entry.label, plain: entry.plain })),
        plain: 'Which mushroom-body compartments the plasticity rule may write to',
        tip: 'The validated pair is PAM11 to MBON07 and PPL101 to MBON11 - the two compartments every recorded result in this project used. The whole mushroom body opens every compartment the release supports, which is a much larger and entirely unvalidated model.',
        onchange: (v) => save({ compartments: v }),
      }),
      FL.field({
        label: 'Allow synapses to change', kind: 'bool', value: settings.learning,
        plain: 'Default for new runs', tip: 'Each run can override this. Turning it off globally is a good way to make sure a session of tuning does not quietly alter a model.',
        onchange: (v) => save({ learning: v }),
      }),
      FL.field({
        label: 'Neural time per step', kind: 'number', min: 0.1, max: 60000, step: 10, unit: 'ms', value: settings.step_ms,
        plain: 'Default for the network block', tip: 'Simulated time, not real time. Longer costs proportionally more computation.',
        onchange: (v) => save({ step_ms: v }),
      }),
      FL.field({
        label: 'Teaching pulse length', kind: 'number', min: 0.1, max: 60000, step: 10, unit: 'ms', value: settings.teach_pulse_ms,
        plain: 'How long a teaching pulse lasts', tip: 'Clamped to the step it lands in. 200 ms is what the recorded results used.',
        onchange: (v) => save({ teach_pulse_ms: v }),
      }),
      FL.field({
        label: 'Teaching pulse strength', kind: 'number', min: 0, max: 200, step: 1, unit: 'mV', value: settings.teach_current_mv,
        plain: 'Injected current at full strength', tip: 'There is no calibration that makes a particular value correct. 20 is what the recorded results used.',
        onchange: (v) => save({ teach_current_mv: v }),
      }),
      FL.field({
        label: 'Rate bin', kind: 'number', min: 0.1, max: 10, step: 0.1, unit: 'ms', value: settings.neural_bin_ms,
        plain: 'How finely spike counts feed the plasticity rule',
        tip: 'The rule assumes a constant rate inside each bin, so it is capped at 10 ms. Smaller is more faithful and slower.',
        onchange: (v) => save({ neural_bin_ms: v }),
      }),
      FL.field({
        label: 'Lamina bias current', kind: 'number', min: 0, max: 60, step: 0.5, unit: 'mV', value: settings.lamina_bias,
        plain: 'Standing current into the first visual relay',
        tip: 'A display adapter, not measured physiology: real lamina cells are graded and this simulation spikes. It exists because the visual path is otherwise almost silent.',
        onchange: (v) => save({ lamina_bias: v }),
      })),
      { tag: 'on load' }));

    /* --- run defaults */
    right.append(FL.panel('Run defaults', el('div', {},
      FL.field({
        label: 'Iterations', kind: 'number', min: 0, max: 1000000, step: 1, value: settings.iterations,
        plain: 'How many passes a new run does', tip: '0 means keep going until something stops it.',
        onchange: (v) => save({ iterations: v }),
      }),
      FL.field({
        label: 'Time limit', kind: 'number', min: 0, max: 86400, step: 10, unit: 's', value: settings.max_seconds,
        plain: 'Stop a run after this long', tip: '0 means no limit. Worth setting for anything driving hardware unattended.',
        onchange: (v) => save({ max_seconds: v }),
      }),
      FL.field({
        label: 'Gap between iterations', kind: 'number', min: 0, max: 3600, step: 0.1, unit: 's', value: settings.interval_seconds,
        plain: 'Pace a run so hardware can keep up', tip: 'Applies between passes, not between blocks.',
        onchange: (v) => save({ interval_seconds: v }),
      }),
      FL.field({
        label: 'Simulate missing hardware', kind: 'bool', value: settings.simulate_devices,
        plain: 'Substitute plausible values when a device is absent',
        tip: 'Lets a workflow be built on a laptop and run on a robot. Simulated values are labelled everywhere they appear.',
        onchange: (v) => save({ simulate_devices: v }),
      }),
      FL.field({
        label: 'Stop if a block fails', kind: 'bool', value: settings.stop_on_error,
        plain: 'End a run on the first error', tip: 'On while you are building something; off for long unattended runs where one bad reading should not end everything.',
        onchange: (v) => save({ stop_on_error: v }),
      }),
      FL.field({
        label: 'Telemetry every N steps', kind: 'number', min: 1, max: 1000, step: 1, value: settings.telemetry_every,
        plain: 'How often the live network view updates',
        tip: 'Raise it if a long run is making the interface sluggish. It changes the display only, never the simulation.',
        onchange: (v) => save({ telemetry_every: v }),
      })),
      { tag: 'new runs' }));

    /* --- interface */
    right.append(FL.panel('Interface', el('div', {},
      FL.field({
        label: 'Hover descriptions', kind: 'bool', value: settings.show_tips,
        plain: 'Explain every control on hover',
        tip: 'The descriptions are the documentation for this interface. Turn them off once you know your way around.',
        onchange: (v) => { save({ show_tips: v }); FL.tipsOn = v; },
      }),
      FL.field({
        label: 'Reduce motion', kind: 'bool', value: settings.reduce_motion,
        plain: 'Stop animations and the screen texture',
        tip: 'Turns off block flashes, wire animation and the raster overlay. The live network view still updates.',
        onchange: (v) => save({ reduce_motion: v }),
      }),
      FL.field({
        label: 'Grid snap', kind: 'number', min: 1, max: 100, step: 1, unit: 'px', value: settings.grid_snap,
        plain: 'How far apart blocks can sit on the canvas', tip: '1 is free placement; 10 keeps a workflow tidy on its own.',
        onchange: (v) => save({ grid_snap: v }),
      }),
      FL.field({
        label: 'Cells drawn in the network view', kind: 'number', min: 200, max: 20000, step: 100, value: settings.viewport_cells,
        plain: 'How many cells the viewport samples',
        tip: 'Drawing every one of 166,700 cells is slow; the viewport samples a fixed subset chosen once and kept stable. Takes effect the next time a dataset is loaded.',
        onchange: (v) => save({ viewport_cells: v }),
      }),
      FL.field({
        label: 'Open a browser on start-up', kind: 'bool', value: settings.open_browser,
        plain: 'Launch a browser window when FLYLAB starts',
        tip: 'Turn it off for a headless machine, or when running FLYLAB as a service.',
        onchange: (v) => save({ open_browser: v }),
      })),
      {}));

    right.append(FL.panel('Honest limits', el('div', {},
      el('p', { style: 'margin-top:0;color:var(--text-2)' },
        'The connectome supplies anatomy. It does not supply dynamics, receptor identity, most neuromodulation, or behaviour.'),
      el('div', { class: 'note' }, 'Cells here are approximate leaky integrate-and-fire units. Transmitter identity sets a coarse excitatory or inhibitory sign; that is not receptor physiology.'),
      el('div', { class: 'note' }, 'Photoreceptors and lamina cells are graded in a real fly. Driving them with spikes and a standing bias current is an adapter chosen to make the visual path respond at all.'),
      el('div', { class: 'note warn' }, 'Teaching pulses are engineered current injections into identified dopaminergic cells. They are not rewards, not punishments, and nothing is experienced.'),
      el('div', { class: 'note bad' }, 'No useful learning has been demonstrated. Synapses changing is a mechanism check, not a result.'),
      el('div', { style: 'margin-top:8px;color:var(--dim);font-size:11.5px' },
        'The full statement is in docs/model.md; what has actually been checked is in docs/validation.md.')),
      { accent: 'accent-red' }));

    root.append(el('div', { class: 'grid g2' }, left, right));
  },
};
