const els = {
  form: document.getElementById("predict-form"),
  surfactant: document.getElementById("surfactant_type"),
  nanoparticle: document.getElementById("nanoparticle_type"),
  concentration: document.getElementById("concentration_wt"),
  concentrationSlider: document.getElementById("concentration_wt_slider"),
  time: document.getElementById("time_s"),
  timeSlider: document.getElementById("time_s_slider"),
  hfoam: document.getElementById("hfoam"),
  hfoamSlider: document.getElementById("hfoam_slider"),
  globalSsim: document.getElementById("global-ssim"),
  globalRegime: document.getElementById("global-regime"),
  trustMetric: document.querySelector(".trust-metric"),
  trust: document.getElementById("trust"),
  supportConfidence: document.getElementById("support-confidence"),
  regime: document.getElementById("regime"),
  transitionTime: document.getElementById("transition-time"),
  expectedSsim: document.getElementById("expected-ssim"),
  imageMae: document.getElementById("image-mae"),
  regimeAccuracy: document.getElementById("regime-accuracy"),
  subtitle: document.getElementById("frame-subtitle"),
  expertBadge: document.getElementById("expert-badge"),
  predictedFrame: document.getElementById("predicted-frame"),
  prototypeCaption: document.getElementById("prototype-caption"),
  bubbleCount: document.getElementById("bubble-count"),
  bubbleCountSd: document.getElementById("bubble-count-sd"),
  bubbleRadius: document.getElementById("bubble-radius"),
  bubbleRadiusSd: document.getElementById("bubble-radius-sd"),
  wallFraction: document.getElementById("wall-fraction"),
  wallFractionSd: document.getElementById("wall-fraction-sd"),
  wallThickness: document.getElementById("wall-thickness"),
  wallThicknessSd: document.getElementById("wall-thickness-sd"),
  prototypeDistance: document.getElementById("prototype-distance"),
  supportStrip: document.getElementById("support-strip"),
  selectedExpert: document.getElementById("selected-expert"),
  classifier: document.getElementById("classifier"),
  validationFrames: document.getElementById("validation-frames"),
  inputTime: document.getElementById("input-time"),
  inputHfoam: document.getElementById("input-hfoam"),
  inputConcentration: document.getElementById("input-concentration"),
  inputBranch: document.getElementById("input-branch"),
};

let meta = null;
let predictTimer = null;
let latestRequest = 0;

function option(value) {
  const element = document.createElement("option");
  element.value = value;
  element.textContent = value;
  return element;
}

function trimZeros(text) {
  return String(text).replace(/(\.\d*?[1-9])0+$/, "$1").replace(/\.0+$/, "");
}

function formatControl(input, value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "";
  if (input === els.concentration) return trimZeros(number.toFixed(6));
  if (input === els.time) return String(Math.round(number));
  if (input === els.hfoam) return trimZeros(number.toFixed(1));
  return String(number);
}

function formatPercent(value, digits = 1) {
  return `${(Number(value) * 100).toFixed(digits)}%`;
}

function formatValue(value, digits = 2) {
  const number = Number(value);
  return Number.isFinite(number) ? number.toFixed(digits) : "--";
}

function expertLabel(value) {
  return String(value || "--").replaceAll("_", " ");
}

function setRange(input, slider, spec) {
  input.min = formatControl(input, spec.min);
  input.max = formatControl(input, spec.max);
  input.step = spec.step;
  slider.min = spec.min;
  slider.max = spec.max;
  slider.step = spec.step;
}

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

function syncPair(input, slider) {
  const normalizeInput = () => {
    const value = clamp(Number(input.value), Number(slider.min), Number(slider.max));
    input.value = formatControl(input, value);
    slider.value = value;
  };
  input.addEventListener("input", () => {
    slider.value = clamp(Number(input.value), Number(slider.min), Number(slider.max));
  });
  input.addEventListener("change", normalizeInput);
  input.addEventListener("blur", normalizeInput);
  slider.addEventListener("input", () => {
    input.value = formatControl(input, slider.value);
  });
}

function branchSpec() {
  return meta.ranges[els.surfactant.value][els.nanoparticle.value];
}

function applyBranchDefaults(resetValues = true) {
  const spec = branchSpec();
  setRange(els.concentration, els.concentrationSlider, spec.concentration_wt);
  setRange(els.time, els.timeSlider, spec.time_s);
  setRange(els.hfoam, els.hfoamSlider, spec.hfoam);
  if (resetValues) {
    els.concentration.value = formatControl(els.concentration, spec.defaults.concentration_wt);
    els.time.value = formatControl(els.time, spec.defaults.time_s);
    els.hfoam.value = formatControl(els.hfoam, spec.defaults.hfoam);
  }
  els.concentrationSlider.value = els.concentration.value;
  els.timeSlider.value = els.time.value;
  els.hfoamSlider.value = els.hfoam.value;
}

function populateNanoparticles(resetValues = true) {
  const values = meta.nanoparticles_by_surfactant[els.surfactant.value];
  const previous = els.nanoparticle.value;
  els.nanoparticle.innerHTML = "";
  values.forEach(value => els.nanoparticle.appendChild(option(value)));
  if (!resetValues && values.includes(previous)) els.nanoparticle.value = previous;
  applyBranchDefaults(resetValues);
}

function normalizeControls() {
  [
    [els.concentration, els.concentrationSlider],
    [els.time, els.timeSlider],
    [els.hfoam, els.hfoamSlider],
  ].forEach(([input, slider]) => {
    if (input === document.activeElement) return;
    const value = clamp(Number(input.value), Number(slider.min), Number(slider.max));
    input.value = formatControl(input, value);
    slider.value = value;
  });
}

function renderMorphology(morphology) {
  const count = morphology.bubble_count;
  const radius = morphology.bubble_radius_px;
  const wallFraction = morphology.wall_fraction;
  const wallThickness = morphology.wall_thickness_px;
  els.bubbleCount.textContent = formatValue(count.value, 0);
  els.bubbleCountSd.textContent = `support SD ±${formatValue(count.support_sd, 1)}`;
  els.bubbleRadius.textContent = `${formatValue(radius.value, 1)} px`;
  els.bubbleRadiusSd.textContent = `support SD ±${formatValue(radius.support_sd, 1)}`;
  els.wallFraction.textContent = formatPercent(wallFraction.value, 1);
  els.wallFractionSd.textContent = `support SD ±${formatPercent(wallFraction.support_sd, 1)}`;
  els.wallThickness.textContent = `${formatValue(wallThickness.value, 1)} px`;
  els.wallThicknessSd.textContent = `support SD ±${formatValue(wallThickness.support_sd, 1)}`;
}

function renderSupports(supports) {
  els.supportStrip.innerHTML = "";
  supports.forEach((support, index) => {
    const figure = document.createElement("figure");
    figure.className = "support-card";
    const image = document.createElement("img");
    image.src = support.image_url;
    image.alt = `Support frame ${index + 1}`;
    const caption = document.createElement("figcaption");
    const title = document.createElement("strong");
    title.textContent = support.nanoparticle;
    const detail = document.createTextNode(
      `${Math.round(support.time_s)} s | C=${Number(support.concentration_wt).toPrecision(4)} | d=${support.distance.toFixed(2)}`
    );
    caption.append(title, detail);
    figure.append(image, caption);
    els.supportStrip.appendChild(figure);
  });
}

function renderPrediction(data) {
  const recon = data.reconstruction;
  const validation = data.validation;
  const selected = recon.alternatives[0];
  const tone = recon.trust_label.toLowerCase();
  els.trustMetric.className = `metric trust-metric ${tone}`;
  els.trust.textContent = recon.trust_label;
  els.supportConfidence.textContent = `${formatPercent(recon.combined_trust)} combined trust | ${formatPercent(recon.support_confidence)} support`;
  els.regime.textContent = recon.regime;
  els.transitionTime.textContent = recon.transition_time_s === null
    ? `${expertLabel(recon.classifier)} classifier`
    : `transition near ${Math.round(recon.transition_time_s)} s`;
  els.expectedSsim.textContent = validation.SSIM.toFixed(3);
  els.imageMae.textContent = validation.image_MAE.toFixed(2);
  els.regimeAccuracy.textContent = formatPercent(validation.supported_balanced_regime_accuracy);
  els.subtitle.textContent = `${data.branch} | C=${Number(data.input.concentration_wt).toPrecision(4)} | ${Math.round(data.input.time_s)} s`;
  els.expertBadge.textContent = expertLabel(validation.selected_expert);
  els.predictedFrame.src = recon.image_url;
  els.prototypeCaption.textContent = `Nearest support: ${selected.nanoparticle}, C=${Number(selected.concentration_wt).toPrecision(4)}, ${Math.round(selected.time_s)} s`;
  els.prototypeDistance.textContent = recon.prototype_distance.toFixed(3);
  renderMorphology(recon.morphology);
  renderSupports(recon.alternatives);
  els.selectedExpert.textContent = expertLabel(validation.selected_expert);
  els.classifier.textContent = expertLabel(recon.classifier);
  els.validationFrames.textContent = String(validation.n_test_frames);
  els.inputTime.textContent = `${Math.round(data.input.time_s)} s`;
  els.inputHfoam.textContent = formatValue(data.input.hfoam, 1);
  els.inputConcentration.textContent = Number(data.input.concentration_wt).toPrecision(4);
  els.inputBranch.textContent = data.branch;
}

function showError(error) {
  els.trustMetric.className = "metric trust-metric low";
  els.trust.textContent = "Error";
  els.supportConfidence.textContent = error.message;
}

async function predict() {
  normalizeControls();
  const requestId = ++latestRequest;
  const response = await fetch("/api/predict", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      surfactant_type: els.surfactant.value,
      nanoparticle_type: els.nanoparticle.value,
      concentration_wt: Number(els.concentration.value),
      time_s: Number(els.time.value),
      hfoam: Number(els.hfoam.value),
    }),
  });
  const data = await response.json();
  if (!response.ok || data.error) throw new Error(data.error || `Prediction failed (${response.status})`);
  if (requestId !== latestRequest) return;
  renderPrediction(data);
}

function schedulePrediction(delay = 220) {
  window.clearTimeout(predictTimer);
  predictTimer = window.setTimeout(() => predict().catch(showError), delay);
}

async function init() {
  const response = await fetch("/api/meta");
  meta = await response.json();
  meta.surfactants.forEach(value => els.surfactant.appendChild(option(value)));
  els.surfactant.value = meta.defaults.surfactant_type;
  populateNanoparticles(true);
  els.nanoparticle.value = meta.defaults.nanoparticle_type;
  applyBranchDefaults(true);

  const metrics = meta.champion.weighted_metrics;
  els.globalSsim.textContent = Number(metrics.SSIM).toFixed(3);
  els.globalRegime.textContent = formatPercent(metrics.supported_balanced_regime_accuracy);

  syncPair(els.concentration, els.concentrationSlider);
  syncPair(els.time, els.timeSlider);
  syncPair(els.hfoam, els.hfoamSlider);

  els.surfactant.addEventListener("change", () => {
    populateNanoparticles(true);
    schedulePrediction(0);
  });
  els.nanoparticle.addEventListener("change", () => {
    applyBranchDefaults(true);
    schedulePrediction(0);
  });
  els.form.addEventListener("input", () => schedulePrediction());
  els.form.addEventListener("change", () => schedulePrediction(40));
  els.form.addEventListener("submit", event => event.preventDefault());
  await predict();
}

init().catch(showError);
