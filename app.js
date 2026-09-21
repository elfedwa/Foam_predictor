const els = {
  form: document.getElementById("predict-form"),
  mode: document.getElementById("mode"),
  surf: document.getElementById("surfactant_type"),
  nano: document.getElementById("nanoparticle_type"),
  conc: document.getElementById("concentration_wt"),
  concSlider: document.getElementById("concentration_wt_slider"),
  t0: document.getElementById("time_min_s"),
  t0Slider: document.getElementById("time_min_s_slider"),
  t1: document.getElementById("time_max_s"),
  t1Slider: document.getElementById("time_max_s_slider"),
  hfoam: document.getElementById("hfoam"),
  hfoamSlider: document.getElementById("hfoam_slider"),
  hliquid: document.getElementById("hliquid"),
  hliquidSlider: document.getElementById("hliquid_slider"),
  hdFields: document.getElementById("hd-fields"),
  notes: document.getElementById("model-notes"),
  canvas: document.getElementById("curve-chart"),
  bcHalfLife: document.getElementById("bc-half-life"),
  bcHalfLifeRange: document.getElementById("bc-half-life-range"),
  bcRetention: document.getElementById("bc-retention"),
  ravgGrowth: document.getElementById("ravg-growth"),
  bcUncertainty: document.getElementById("bc-uncertainty"),
  ravgUncertainty: document.getElementById("ravg-uncertainty"),
  trust: document.getElementById("trust"),
  visual: document.getElementById("visual-explanation"),
  cues: document.getElementById("science-cues"),
  bcSupports: document.getElementById("bc-supports"),
  ravgSupports: document.getElementById("ravg-supports"),
  subtitle: document.getElementById("curve-subtitle"),
  tooltip: document.getElementById("chart-tooltip"),
};

let currentPrediction = null;
let currentChart = null;
let predictTimer = null;
let latestPredictId = 0;

function opt(value) {
  const o = document.createElement("option");
  o.value = value;
  o.textContent = value;
  return o;
}

function fmtPct(x) {
  return `${Math.round(x * 100)}%`;
}

function fmtUncertaintyPct(x) {
  if (x === null || x === undefined || Number.isNaN(Number(x))) return "--";
  const n = Number(x);
  const digits = n >= 10 ? 0 : 1;
  return `±${n.toFixed(digits)}%`;
}

function fmtValue(x, digits = 1) {
  if (x === null || x === undefined || Number.isNaN(Number(x))) return "--";
  return Number(x).toFixed(digits);
}

function fmtSeconds(x) {
  if (x === null || x === undefined || Number.isNaN(Number(x))) return "--";
  return `${Number(x).toFixed(1)} s`;
}

function trimZeros(text) {
  return String(text).replace(/(\.\d*?[1-9])0+$/, "$1").replace(/\.0+$/, "");
}

function formatControlValue(input, value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "";
  if (input === els.t0 || input === els.t1) return String(Math.round(n));
  if (input === els.conc) return trimZeros(n.toFixed(5));
  if (input === els.hfoam || input === els.hliquid) return trimZeros(n.toPrecision(4));
  return String(n);
}

function applyRange(input, slider, spec) {
  if (!input || !slider || !spec) return;
  slider.min = spec.min;
  slider.max = spec.max;
  slider.step = spec.step;
  input.step = spec.step;
  input.min = formatControlValue(input, spec.min);
  input.max = formatControlValue(input, spec.max);
}

function syncSliderPair(input, slider) {
  if (!input || !slider) return;
  const clampToSlider = value => {
    const min = Number(slider.min);
    const max = Number(slider.max);
    if (!Number.isFinite(value)) return slider.value;
    return Math.min(max, Math.max(min, value));
  };
  const normalize = () => {
    slider.value = clampToSlider(Number(input.value));
    input.value = formatControlValue(input, slider.value);
  };
  input.addEventListener("input", () => {
    slider.value = clampToSlider(Number(input.value));
    window.clearTimeout(input._formatTimer);
    input._formatTimer = window.setTimeout(() => {
      input.value = formatControlValue(input, slider.value);
    }, 650);
  });
  input.addEventListener("change", normalize);
  input.addEventListener("blur", normalize);
  slider.addEventListener("input", () => {
    input.value = formatControlValue(input, slider.value);
  });
}

function normalizeSliderControls() {
  [
    [els.conc, els.concSlider],
    [els.t0, els.t0Slider],
    [els.t1, els.t1Slider],
    [els.hfoam, els.hfoamSlider],
    [els.hliquid, els.hliquidSlider],
  ].forEach(([input, slider]) => {
    if (!input || !slider || input === document.activeElement) return;
    slider.value = Math.min(Number(slider.max), Math.max(Number(slider.min), Number(input.value)));
    input.value = formatControlValue(input, slider.value);
  });
}

function setSliderValues() {
  [
    [els.conc, els.concSlider],
    [els.t0, els.t0Slider],
    [els.t1, els.t1Slider],
    [els.hfoam, els.hfoamSlider],
    [els.hliquid, els.hliquidSlider],
  ].forEach(([input, slider]) => {
    if (input && slider) {
      input.value = formatControlValue(input, input.value);
      slider.value = input.value;
    }
  });
}

function setNotes(meta) {
  const mode = els.mode.value;
  const notes = mode === "hd"
    ? [
        `BC HD: ${meta.models.bc_hd.version}, known R2 ${Number(meta.models.bc_hd.known.R2).toFixed(4)}`,
        `Ravg HD: ${meta.models.ravg_hd.version}, focused reject ${Number(meta.models.ravg_hd.metrics.focused_50k_reject_fraction).toFixed(4)}`,
        "Use when HD foam/liquid measurements are available."
      ]
    : [
        `BC no-HD: ${meta.models.bc_no_hd.version}, known R2 ${Number(meta.models.bc_no_hd.known.R2).toFixed(4)}`,
        `Ravg no-HD: ${meta.models.ravg_no_hd.version}, focused reject ${Number(meta.models.ravg_no_hd.metrics.focused_50k_reject_fraction).toFixed(4)}`,
        "Use when HD foam/liquid measurements are not available."
      ];
  els.notes.innerHTML = "";
  notes.forEach(text => {
    const li = document.createElement("li");
    li.textContent = text;
    els.notes.appendChild(li);
  });
}

function path(ctx, xs, ys, xScale, yScale) {
  ctx.beginPath();
  xs.forEach((x, i) => {
    const px = xScale(x);
    const py = yScale(ys[i]);
    if (i === 0) ctx.moveTo(px, py);
    else ctx.lineTo(px, py);
  });
}

function fillBand(ctx, xs, lows, highs, xScale, yScale, color) {
  ctx.beginPath();
  xs.forEach((x, i) => {
    const px = xScale(x);
    const py = yScale(highs[i]);
    if (i === 0) ctx.moveTo(px, py);
    else ctx.lineTo(px, py);
  });
  for (let i = xs.length - 1; i >= 0; i--) {
    ctx.lineTo(xScale(xs[i]), yScale(lows[i]));
  }
  ctx.closePath();
  ctx.fillStyle = color;
  ctx.fill();
}

function shadeRect(ctx, x0, x1, pad, h, color) {
  const left = Math.min(x0, x1);
  const width = Math.abs(x1 - x0);
  if (width <= 0) return;
  ctx.fillStyle = color;
  ctx.fillRect(left, pad.top, width, h - pad.top - pad.bottom);
}

function regimeAtTime(data, time) {
  const half = data.bc.half_life;
  if (half.value_s === null) return { label: "Stable through selected window", tone: "stable" };
  if (half.lower_s !== null && half.upper_s !== null && time >= half.lower_s && time <= half.upper_s) {
    return { label: "Half-life uncertainty zone", tone: "watch" };
  }
  if (time < half.value_s) return { label: "Before predicted half-life", tone: "stable" };
  return { label: "After predicted half-life", tone: "risk" };
}

function drawRegimes(ctx, data, xScale, xMin, xMax, pad, h) {
  const half = data.bc.half_life;
  if (half.value_s === null) {
    shadeRect(ctx, xScale(xMin), xScale(xMax), pad, h, "rgba(31, 138, 91, 0.07)");
    return;
  }
  const low = Math.max(xMin, half.lower_s ?? half.value_s);
  const high = Math.min(xMax, half.upper_s ?? half.value_s);
  if (low > xMin) shadeRect(ctx, xScale(xMin), xScale(low), pad, h, "rgba(31, 138, 91, 0.06)");
  if (high >= low) shadeRect(ctx, xScale(low), xScale(high), pad, h, "rgba(183, 121, 31, 0.10)");
  if (high < xMax) shadeRect(ctx, xScale(high), xScale(xMax), pad, h, "rgba(178, 58, 72, 0.07)");
}

function drawChart(data, hoverIndex = null) {
  const canvas = els.canvas;
  const ctx = canvas.getContext("2d");
  const w = canvas.width;
  const h = canvas.height;
  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, w, h);

  const pad = { left: 74, right: 74, top: 28, bottom: 58 };
  const times = data.times_s;
  const bc = data.bc.values;
  const ravg = data.ravg.values;
  const bcLower = data.bc.lower;
  const bcUpper = data.bc.upper;
  const ravgLower = data.ravg.lower;
  const ravgUpper = data.ravg.upper;
  const view = data.view_window_s || {};
  let xMin = Number.isFinite(Number(view.time_min_s)) ? Number(view.time_min_s) : Math.min(...times);
  let xMax = Number.isFinite(Number(view.time_max_s)) ? Number(view.time_max_s) : Math.max(...times);
  if (xMax < xMin) [xMin, xMax] = [xMax, xMin];
  if (Math.abs(xMax - xMin) < 1e-9) xMax = xMin + 1;
  const visibleIdx = times
    .map((t, i) => ({ t, i }))
    .filter(({ t }) => t >= xMin && t <= xMax)
    .map(({ i }) => i);
  const yIdx = visibleIdx.length ? visibleIdx : times.map((_, i) => i);
  const bcMax = Math.max(...yIdx.map(i => bcUpper[i]), ...yIdx.map(i => bc[i])) * 1.1 || 1;
  const rMax = Math.max(...yIdx.map(i => ravgUpper[i]), ...yIdx.map(i => ravg[i])) * 1.1 || 1;
  const xScale = x => pad.left + ((x - xMin) / (xMax - xMin)) * (w - pad.left - pad.right);
  const yBC = y => h - pad.bottom - (y / bcMax) * (h - pad.top - pad.bottom);
  const yR = y => h - pad.bottom - (y / rMax) * (h - pad.top - pad.bottom);
  currentChart = { pad, xMin, xMax, bcMax, rMax };

  ctx.strokeStyle = "#d9dfdc";
  ctx.lineWidth = 1;
  drawRegimes(ctx, data, xScale, xMin, xMax, pad, h);

  ctx.fillStyle = "#687170";
  ctx.font = "16px system-ui";
  for (let i = 0; i <= 5; i++) {
    const y = pad.top + i * (h - pad.top - pad.bottom) / 5;
    ctx.beginPath(); ctx.moveTo(pad.left, y); ctx.lineTo(w - pad.right, y); ctx.stroke();
    const bcTick = bcMax - i * bcMax / 5;
    const rTick = rMax - i * rMax / 5;
    ctx.fillStyle = "#147b8a";
    ctx.textAlign = "right";
    ctx.fillText(bcTick.toFixed(bcMax > 20 ? 0 : 1), pad.left - 10, y + 5);
    ctx.fillStyle = "#725a8f";
    ctx.textAlign = "left";
    ctx.fillText(rTick.toFixed(rMax > 100 ? 0 : 1), w - pad.right + 10, y + 5);
  }
  ctx.textAlign = "center";
  for (let i = 0; i <= 5; i++) {
    const x = pad.left + i * (w - pad.left - pad.right) / 5;
    ctx.beginPath(); ctx.moveTo(x, pad.top); ctx.lineTo(x, h - pad.bottom); ctx.stroke();
    const t = xMin + i * (xMax - xMin) / 5;
    ctx.fillText(String(Math.round(t)), x - 16, h - 20);
  }

  ctx.save();
  ctx.translate(22, h / 2 + 58);
  ctx.rotate(-Math.PI / 2);
  ctx.fillStyle = "#147b8a";
  ctx.fillText("BC(t)", 0, 0);
  ctx.restore();
  ctx.fillStyle = "#725a8f";
  ctx.fillText("Ravg(t)", w - 70, 26);

  ctx.save();
  ctx.beginPath();
  ctx.rect(pad.left, pad.top, w - pad.left - pad.right, h - pad.top - pad.bottom);
  ctx.clip();

  fillBand(ctx, times, bcLower, bcUpper, xScale, yBC, "rgba(20, 123, 138, 0.14)");
  fillBand(ctx, times, ravgLower, ravgUpper, xScale, yR, "rgba(114, 90, 143, 0.14)");

  path(ctx, times, bc, xScale, yBC);
  ctx.strokeStyle = "#147b8a";
  ctx.lineWidth = 4;
  ctx.stroke();

  path(ctx, times, ravg, xScale, yR);
  ctx.strokeStyle = "#725a8f";
  ctx.lineWidth = 4;
  ctx.stroke();
  ctx.restore();

  ctx.fillStyle = "#3c4544";
  ctx.font = "18px system-ui";
  ctx.textAlign = "center";
  ctx.fillText("time (s)", w / 2 - 34, h - 20);

  if (hoverIndex !== null) {
    const t = times[hoverIndex];
    const x = xScale(t);
    ctx.strokeStyle = "rgba(27, 29, 31, 0.55)";
    ctx.lineWidth = 1.5;
    ctx.setLineDash([5, 5]);
    ctx.beginPath(); ctx.moveTo(x, pad.top); ctx.lineTo(x, h - pad.bottom); ctx.stroke();
    ctx.setLineDash([]);

    ctx.fillStyle = "#147b8a";
    ctx.beginPath(); ctx.arc(x, yBC(bc[hoverIndex]), 5, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = "#725a8f";
    ctx.beginPath(); ctx.arc(x, yR(ravg[hoverIndex]), 5, 0, Math.PI * 2); ctx.fill();
  }
}

function showHover(index, clientX, clientY) {
  if (!currentPrediction || index === null) return;
  const data = currentPrediction;
  const regime = regimeAtTime(data, data.times_s[index]);
  els.tooltip.hidden = false;
  els.tooltip.className = `chart-tooltip ${regime.tone}`;
  els.tooltip.innerHTML = [
    `<strong>${Math.round(data.times_s[index])} s</strong>`,
    `<span>${regime.label}</span>`,
    `<span>BC ${fmtValue(data.bc.values[index], 2)} ±${fmtValue(data.bc.uncertainty_pct[index], 1)}%</span>`,
    `<span>Ravg ${fmtValue(data.ravg.values[index], 1)} ±${fmtValue(data.ravg.uncertainty_pct[index], 1)}%</span>`
  ].join("");
  const band = els.canvas.closest(".chart-band").getBoundingClientRect();
  const x = clientX - band.left;
  const y = clientY - band.top;
  els.tooltip.style.left = `${Math.min(Math.max(x + 14, 8), band.width - 205)}px`;
  els.tooltip.style.top = `${Math.min(Math.max(y + 14, 8), band.height - 116)}px`;
}

function hideHover() {
  els.tooltip.hidden = true;
  if (currentPrediction) drawChart(currentPrediction);
}

function installChartHover() {
  els.canvas.addEventListener("mousemove", event => {
    if (!currentPrediction || !currentChart) return;
    const rect = els.canvas.getBoundingClientRect();
    const xCanvas = (event.clientX - rect.left) * (els.canvas.width / rect.width);
    const { pad, xMin, xMax } = currentChart;
    if (xCanvas < pad.left || xCanvas > els.canvas.width - pad.right) {
      hideHover();
      return;
    }
    const frac = (xCanvas - pad.left) / (els.canvas.width - pad.left - pad.right);
    const time = xMin + frac * (xMax - xMin);
    const index = currentPrediction.times_s.reduce((best, t, i, arr) => (
      Math.abs(t - time) < Math.abs(arr[best] - time) ? i : best
    ), 0);
    drawChart(currentPrediction, index);
    showHover(index, event.clientX, event.clientY);
  });
  els.canvas.addEventListener("mouseleave", hideHover);
}

function renderSupports(listEl, supports) {
  listEl.innerHTML = "";
  supports.slice(0, 4).forEach(s => {
    const li = document.createElement("li");
    li.textContent = `${s.branch}, C=${Number(s.concentration_wt).toPrecision(4)} (w=${Number(s.weight).toFixed(2)})`;
    listEl.appendChild(li);
  });
}

function renderPrediction(data) {
  currentPrediction = data;
  els.bcHalfLife.textContent = data.bc.half_life.label;
  const halfRange = data.bc.half_life.lower_s === null || data.bc.half_life.upper_s === null
    ? `threshold ${fmtValue(data.bc.half_life.threshold, 2)} BC`
    : `${fmtSeconds(data.bc.half_life.lower_s)} to ${fmtSeconds(data.bc.half_life.upper_s)} (${fmtUncertaintyPct(data.bc.half_life.uncertainty_pct)})`;
  els.bcHalfLifeRange.textContent = halfRange;
  els.bcRetention.textContent = fmtPct(data.bc.retention);
  els.ravgGrowth.textContent = fmtPct(data.ravg.growth_fraction);
  els.bcUncertainty.textContent = fmtUncertaintyPct(data.bc.median_uncertainty_pct);
  els.ravgUncertainty.textContent = fmtUncertaintyPct(data.ravg.median_uncertainty_pct);
  els.trust.textContent = fmtPct(data.stability.combined_trust);
  els.visual.textContent = data.stability.visual_explanation;
  els.subtitle.textContent = `${data.input.surfactant_type} | ${data.input.nanoparticle_type}, C=${data.input.concentration_wt}`;
  els.cues.innerHTML = "";
  data.stability.science_cues.forEach(cue => {
    const div = document.createElement("div");
    div.textContent = cue;
    els.cues.appendChild(div);
  });
  renderSupports(els.bcSupports, data.bc.supports);
  renderSupports(els.ravgSupports, data.ravg.supports);
  drawChart(data);
}

async function predict() {
  normalizeSliderControls();
  const requestId = ++latestPredictId;
  const payload = {
    mode: els.mode.value,
    surfactant_type: els.surf.value,
    nanoparticle_type: els.nano.value,
    concentration_wt: Number(els.conc.value),
    time_min_s: Number(els.t0.value),
    time_max_s: Number(els.t1.value),
    hfoam: Number(els.hfoam.value),
    hliquid: Number(els.hliquid.value)
  };
  const res = await fetch("/api/predict", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
  const data = await res.json();
  if (data.error) throw new Error(data.error);
  if (requestId !== latestPredictId) return;
  renderPrediction(data);
}

function showPredictionError(err) {
  els.bcHalfLife.textContent = "Error";
  els.bcHalfLifeRange.textContent = err.message;
}

function schedulePredict(delay = 250) {
  window.clearTimeout(predictTimer);
  predictTimer = window.setTimeout(() => {
    predict().catch(showPredictionError);
  }, delay);
}

async function init() {
  const meta = await (await fetch("/api/meta")).json();
  meta.surfactants.forEach(x => els.surf.appendChild(opt(x)));
  meta.nanoparticles.forEach(x => els.nano.appendChild(opt(x)));
  els.surf.value = meta.defaults.surfactant_type;
  els.nano.value = meta.defaults.nanoparticle_type;
  els.conc.value = meta.defaults.concentration_wt;
  els.t0.value = meta.defaults.time_min_s;
  els.t1.value = meta.defaults.time_max_s;
  els.hfoam.value = meta.defaults.hfoam;
  els.hliquid.value = meta.defaults.hliquid;
  applyRange(els.conc, els.concSlider, meta.ranges.concentration_wt);
  applyRange(els.t0, els.t0Slider, meta.ranges.time_s);
  applyRange(els.t1, els.t1Slider, meta.ranges.time_s);
  applyRange(els.hfoam, els.hfoamSlider, meta.ranges.hfoam);
  applyRange(els.hliquid, els.hliquidSlider, meta.ranges.hliquid);
  [
    [els.conc, els.concSlider],
    [els.t0, els.t0Slider],
    [els.t1, els.t1Slider],
    [els.hfoam, els.hfoamSlider],
    [els.hliquid, els.hliquidSlider],
  ].forEach(([input, slider]) => syncSliderPair(input, slider));
  setSliderValues();
  setNotes(meta);
  els.mode.addEventListener("change", () => {
    els.hdFields.style.display = els.mode.value === "hd" ? "grid" : "none";
    setNotes(meta);
  });
  els.form.addEventListener("input", () => schedulePredict());
  els.form.addEventListener("change", () => schedulePredict(50));
  els.form.addEventListener("submit", event => {
    event.preventDefault();
    schedulePredict(0);
  });
  installChartHover();
  await predict();
}

init();
