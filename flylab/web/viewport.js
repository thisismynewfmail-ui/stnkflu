/* FLYLAB - the connectome viewport.
   A schematic of where work is happening: regions in fixed positions, one dot
   per sampled cell, brightness set by that cell's firing rate in the last
   network step. The layout is a readable diagram, not anatomical coordinates,
   and the legend says so. */

const Viewport = (FL.viewport = {
  canvas: null, ctx: null, layout: null, activity: null, decay: null,
  hover: null, running: false, lastFrame: 0, peak: 30,
});

const REGION_COLOUR = {
  eye: '#b083f0', optic: '#8f7bf0', antenna: '#4fd1e0', antennal_lobe: '#3fc0c8',
  mouth: '#57d98a', mushroom: '#ffb02e', teaching: '#ff6a2c', mbon: '#ffd23f',
  central_complex: '#3aa0ff', descending: '#f2f5f8', motor: '#ff5d73',
  ascending: '#79a6b8', endocrine: '#c58bd6', other: '#5c6f7d',
};

Viewport.attach = (canvas) => {
  Viewport.canvas = canvas;
  Viewport.ctx = canvas.getContext('2d');
  canvas.onmousemove = (event) => {
    const rect = canvas.getBoundingClientRect();
    Viewport.hover = { x: (event.clientX - rect.left) / rect.width, y: (event.clientY - rect.top) / rect.height };
  };
  canvas.onmouseleave = () => { Viewport.hover = null; };
  Viewport.resize();
  addEventListener('resize', Viewport.resize);
  if (!Viewport.running) { Viewport.running = true; requestAnimationFrame(Viewport.tick); }
};

Viewport.resize = () => {
  const canvas = Viewport.canvas;
  if (!canvas) return;
  const rect = canvas.getBoundingClientRect();
  const ratio = Math.min(2, devicePixelRatio || 1);
  canvas.width = Math.max(320, rect.width * ratio);
  canvas.height = Math.max(240, rect.height * ratio);
};

Viewport.setLayout = (layout) => {
  Viewport.layout = layout;
  Viewport.decay = new Float32Array((layout.x || []).length);
};

Viewport.push = (frame) => {
  if (!frame) return;
  Viewport.activity = frame;
  Viewport.lastFrame = performance.now();
  const peak = Math.max(10, ...(frame.regions || []).map((r) => r.peak_hz || 0));
  Viewport.peak = Viewport.peak * 0.8 + peak * 0.2;
};

Viewport.tick = () => {
  requestAnimationFrame(Viewport.tick);
  const canvas = Viewport.canvas;
  if (!canvas || !canvas.isConnected) { Viewport.running = false; return; }
  const ctx = Viewport.ctx;
  const W = canvas.width;
  const H = canvas.height;
  ctx.clearRect(0, 0, W, H);
  ctx.fillStyle = '#04070a';
  ctx.fillRect(0, 0, W, H);

  const layout = Viewport.layout;
  if (!layout) {
    ctx.fillStyle = '#5b7080';
    ctx.font = `${Math.round(W / 46)}px ui-monospace, monospace`;
    ctx.textAlign = 'center';
    ctx.fillText('NO NETWORK LOADED', W / 2, H / 2);
    ctx.font = `${Math.round(W / 74)}px ui-sans-serif, sans-serif`;
    ctx.fillText('Load a dataset from Status or Housekeeping to see the network.', W / 2, H / 2 + W / 34);
    return;
  }

  const pad = Math.min(W, H) * 0.045;
  const boxW = W - pad * 2;
  const boxH = H - pad * 2;
  const px = (u) => pad + u * boxW;
  const py = (v) => pad + v * boxH;

  // Region envelopes first, so cells sit inside labelled fields.
  for (const region of layout.regions) {
    const colour = REGION_COLOUR[region.key] || '#5c6f7d';
    const live = (Viewport.activity && Viewport.activity.regions
      ? (Viewport.activity.regions.find((r) => r.key === region.key) || {})
      : {});
    const heat = Math.min(1, (live.mean_hz || 0) / Math.max(4, Viewport.peak * 0.6));
    ctx.save();
    ctx.strokeStyle = `${colour}${heat > 0.05 ? '88' : '33'}`;
    ctx.lineWidth = Math.max(1, W / 1100);
    ctx.beginPath();
    ctx.ellipse(px(region.x), py(region.y), region.rx * boxW, region.ry * boxH, 0, 0, Math.PI * 2);
    ctx.stroke();
    if (heat > 0.02) {
      ctx.fillStyle = `${colour}${Math.round(heat * 26).toString(16).padStart(2, '0')}`;
      ctx.fill();
    }
    ctx.restore();
  }

  // Cells.
  const activity = Viewport.activity ? Viewport.activity.activity : null;
  const decay = Viewport.decay;
  const size = Math.max(1.1, W / 620);
  for (let i = 0; i < layout.x.length; i += 1) {
    const rate = activity && activity[i] !== undefined ? activity[i] : 0;
    if (decay) {
      decay[i] = Math.max(rate, decay[i] * 0.90);
    }
    const level = decay ? decay[i] : rate;
    const colour = REGION_COLOUR[layout.regions[layout.region[i]].key] || '#5c6f7d';
    const heat = Math.min(1, level / Math.max(6, Viewport.peak));
    const x = px(layout.x[i]);
    const y = py(layout.y[i]);
    if (heat < 0.02) {
      ctx.fillStyle = '#16212a';
      ctx.fillRect(x, y, size, size);
    } else {
      ctx.fillStyle = colour;
      ctx.globalAlpha = 0.28 + heat * 0.72;
      ctx.fillRect(x - size * 0.5, y - size * 0.5, size * 2, size * 2);
      if (heat > 0.55) {
        ctx.globalAlpha = (heat - 0.55) * 0.8;
        ctx.beginPath();
        ctx.arc(x, y, size * 3.4, 0, Math.PI * 2);
        ctx.fill();
      }
      ctx.globalAlpha = 1;
    }
  }

  // Region labels and readouts.
  ctx.textAlign = 'center';
  for (const region of layout.regions) {
    const colour = REGION_COLOUR[region.key] || '#5c6f7d';
    const live = (Viewport.activity && Viewport.activity.regions
      ? (Viewport.activity.regions.find((r) => r.key === region.key) || {})
      : {});
    const x = px(region.x);
    const y = py(region.y + region.ry) + W / 130;
    ctx.fillStyle = `${colour}cc`;
    ctx.font = `${Math.max(8, Math.round(W / 116))}px ui-sans-serif, sans-serif`;
    const label = region.label.length > 34 ? `${region.label.slice(0, 32)}...` : region.label;
    ctx.fillText(label.toUpperCase(), x, y);
    if (live.cells) {
      ctx.fillStyle = live.mean_hz > 0.3 ? '#ffb02e' : '#5f7482';
      ctx.font = `${Math.max(8, Math.round(W / 128))}px ui-monospace, monospace`;
      ctx.fillText(`${live.mean_hz ? live.mean_hz.toFixed(1) : '0.0'} Hz  ${live.cells} cells`, x, y + W / 96);
    }
  }

  // Hover readout.
  if (Viewport.hover) {
    let best = null;
    for (const region of layout.regions) {
      const dx = (Viewport.hover.x - (pad / W + region.x * boxW / W)) * 1;
      const dy = (Viewport.hover.y - (pad / H + region.y * boxH / H)) * 1;
      const inside = Math.abs(dx) < region.rx * boxW / W && Math.abs(dy) < region.ry * boxH / H;
      if (inside) best = region;
    }
    if (best) {
      const live = (Viewport.activity && Viewport.activity.regions
        ? (Viewport.activity.regions.find((r) => r.key === best.key) || {})
        : {});
      const text = `${best.label}  -  ${live.cells || 0} cells shown, ${(live.mean_hz || 0).toFixed(1)} Hz mean, ${(live.peak_hz || 0).toFixed(0)} Hz peak`;
      ctx.textAlign = 'left';
      ctx.font = `${Math.max(9, Math.round(W / 110))}px ui-monospace, monospace`;
      const width = ctx.measureText(text).width + 18;
      ctx.fillStyle = '#0b1217ee';
      ctx.fillRect(pad, H - pad - W / 34, width, W / 42);
      ctx.strokeStyle = '#46606f';
      ctx.strokeRect(pad, H - pad - W / 34, width, W / 42);
      ctx.fillStyle = '#dde7ee';
      ctx.fillText(text, pad + 9, H - pad - W / 34 + W / 62);
    }
  }
};

Viewport.reset = () => { Viewport.layout = null; Viewport.activity = null; Viewport.decay = null; };
