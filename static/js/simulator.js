// scenario simulator: load a real baseline, expose sliders for the knobs the
// backend understands, debounce reruns, and analyse the result four ways.
//
//   impact       before vs after for every model, plus the cascade coupling
//   sensitivity  every knob swept to both ends, ranked by how far it moves things
//   response     the full curve of every model against one knob
//   breaking     solve for the knob value where one hazard crosses a risk band
//
// the baseline is cached server-side for 10 minutes per location, so only the
// first load is slow: after that a slider drag is a pure re-score.

let loc = null, meta = null, timer = null, firstLoad = true;
let lastRun = null, tab = "impact";
const charts = {};

const $ = (id) => document.getElementById(id);
const PALETTE = ["#35b39c", "#e0a052", "#7b9fe0", "#e05252", "#b57bd6", "#5fc9d6",
                 "#d68f7b", "#8fd67b", "#d67bb5", "#7b8fd6"];

function fmt(v, unit) {
  const s = unit === "x" ? `${v}x` : `${v > 0 && unit !== "x" ? "+" : ""}${v} ${unit}`;
  return s;
}

// ---------------------------------------------------------------- setup

async function loadBaseline(picked) {
  loc = picked;
  firstLoad = true;
  TS.saveLocation(picked);
  $("empty-state").hidden = true;
  $("sim-ui").hidden = false;
  $("sim-results").innerHTML = `<div class="spinner-line">Building the real-conditions baseline for ${picked.name}. First load takes about 20 to 40 seconds while live data comes in, then every slider is instant...</div>`;
  if (!meta) {
    meta = await TS.fetchJSON("/api/scenario/knobs");
    buildKnobs();
    buildPresets();
    buildTabs();
    applyHash();          // a shared link arrives with its sliders already set
  }
  await rerun();
}

function buildKnobs() {
  const box = $("knobs");
  box.innerHTML = "";
  const groups = {};
  for (const [key, k] of Object.entries(meta.knobs)) (groups[k.group] ||= []).push([key, k]);

  for (const [group, entries] of Object.entries(groups)) {
    const g = document.createElement("div");
    g.className = "knob-group";
    g.innerHTML = `<div class="kg-title">${group}</div>`;
    for (const [key, k] of entries) {
      const div = document.createElement("div");
      div.className = "knob";
      div.id = `knob-row-${key}`;
      div.innerHTML = `
        <div class="k-head">
          <label for="knob-${key}">${k.label}</label>
          <span class="val" id="val-${key}">${fmt(k.default, k.unit)}</span>
        </div>
        <input type="range" id="knob-${key}" min="${k.min}" max="${k.max}" step="${k.step}"
               value="${k.default}" aria-label="${k.label}">
        <div class="k-help">${k.help}</div>`;
      g.appendChild(div);
      div.querySelector("input").addEventListener("input", () => {
        paintKnob(key);
        clearTimeout(timer);
        timer = setTimeout(rerun, 350);   // debounce so dragging feels instant
      });
    }
    box.appendChild(g);
  }
}

function paintKnob(key) {
  const k = meta.knobs[key];
  const v = parseFloat($(`knob-${key}`).value);
  $(`val-${key}`).textContent = fmt(v, k.unit);
  // highlight anything moved off its observed value, so it is obvious at a
  // glance which parts of the scenario are hypothetical
  $(`knob-row-${key}`).classList.toggle("touched", Math.abs(v - k.default) > 1e-9);
}

function buildPresets() {
  const row = $("preset-row");
  row.innerHTML = "";
  for (const [key, p] of Object.entries(meta.presets)) {
    const btn = document.createElement("button");
    btn.className = "btn btn-ghost btn-sm";
    btn.textContent = p.label;
    btn.title = p.note;
    btn.addEventListener("click", () => { setDeltas(p.deltas); rerun(); });
    row.appendChild(btn);
  }
}

function setDeltas(deltas) {
  for (const [key, k] of Object.entries(meta.knobs)) {
    $(`knob-${key}`).value = deltas[key] ?? k.default;
    paintKnob(key);
  }
}

function currentDeltas() {
  const d = {};
  for (const key of Object.keys(meta.knobs)) d[key] = parseFloat($(`knob-${key}`).value);
  return d;
}

// only the knobs actually moved, which keeps share links short and readable
function activeDeltas() {
  const d = {};
  for (const [key, k] of Object.entries(meta.knobs)) {
    const v = parseFloat($(`knob-${key}`).value);
    if (Math.abs(v - k.default) > 1e-9) d[key] = v;
  }
  return d;
}

function buildTabs() {
  document.querySelectorAll("#sim-tabs button").forEach(btn => {
    btn.addEventListener("click", () => {
      tab = btn.dataset.tab;
      document.querySelectorAll("#sim-tabs button").forEach(b => b.classList.toggle("on", b === btn));
      document.querySelectorAll("[data-pane]").forEach(p => { p.hidden = p.dataset.pane !== tab; });
      refreshTab();
    });
  });

  const knobOpts = Object.entries(meta.knobs)
    .map(([key, k]) => `<option value="${key}">${k.label}</option>`).join("");
  $("sweep-knob").innerHTML = knobOpts;
  $("thr-knob").innerHTML = knobOpts;
  $("thr-target").innerHTML = Object.keys(meta.bands)
    .map(b => `<option value="${b}"${b === "High" ? " selected" : ""}>${b} risk</option>`).join("");
  $("sweep-knob").addEventListener("change", runSweep);
  $("thr-knob").addEventListener("change", runThreshold);
  $("thr-module").addEventListener("change", runThreshold);
  $("thr-target").addEventListener("change", runThreshold);
  $("btn-share").addEventListener("click", share);
  $("btn-csv").addEventListener("click", exportCSV);
}

// ---------------------------------------------------------------- impact

async function post(path, body) {
  try {
    return await TS.fetchJSON(path, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch (e) {
    return { error: "Could not reach the server. Check your connection and try again." };
  }
}

function locName() {
  return `${loc.name}${loc.admin1 ? ", " + loc.admin1 : ""}`;
}

function payload(extra = {}) {
  return { lat: loc.lat, lon: loc.lon, name: locName(), deltas: currentDeltas(), ...extra };
}

async function rerun() {
  if (!loc) return;
  const busy = $("sim-busy");
  busy.textContent = firstLoad ? "building baseline" : "updating";
  busy.hidden = false;
  const data = await post("/api/scenario/run", payload());
  busy.hidden = true;   // always clears, even on error
  firstLoad = false;
  if (data.error) {
    $("sim-results").innerHTML = `<div class="error-note">${data.error}</div>`;
    return;
  }
  lastRun = data;
  writeHash();

  if (!$("thr-module").options.length) {
    $("thr-module").innerHTML = data.results
      .map(r => `<option value="${r.module}">${r.title}</option>`).join("");
  }

  $("sim-results").innerHTML = data.results.map(r => {
    const dir = r.change > 0.5 ? "risk-high" : r.change < -0.5 ? "risk-low" : "muted";
    const sign = r.change > 0 ? "+" : "";
    return `<div class="delta-row">
      <span class="name"><svg width="15" height="15" style="vertical-align: -2px; color: var(--ink-3)"><use href="#i-${r.module}"/></svg> ${r.title}</span>
      <span class="num muted">${r.before}</span>
      <span class="arrow">&rarr;</span>
      <span class="num">${r.after}</span>
      <span class="delta-badge ${dir}">${sign}${r.change}</span>
    </div>`;
  }).join("");

  // knock-on effects: a scenario that dries everything out lifts wildfire
  // through drought, not just directly, and that is worth showing explicitly
  $("cascade-box").innerHTML = (data.cascades || []).length
    ? `<div class="panel-title mt-3"><svg width="15" height="15"><use href="#i-cascade"/></svg> Knock-on effects in this scenario</div>`
      + data.cascades.map(c =>
          `<div class="cascade-note"><span class="cb">+${c.boost.toFixed(1)}</span>
           <span>${c.label || `${c.source} raises ${c.target}`}</span></div>`).join("")
    : "";

  drawImpactChart(data);
  refreshTab();
}

function drawImpactChart(data) {
  charts.impact?.destroy();
  charts.impact = new Chart($("sim-chart"), {
    type: "bar",
    data: {
      labels: data.results.map(r => r.title.replace(" Intelligence", "")),
      datasets: [
        { label: "Baseline (real conditions)", data: data.results.map(r => r.before),
          backgroundColor: "rgba(100, 115, 138, 0.55)", borderRadius: 4 },
        { label: "Scenario", data: data.results.map(r => r.after),
          backgroundColor: TS.theme.charts[0], borderRadius: 4 },
      ],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      scales: {
        x: { grid: { display: false }, ticks: { maxRotation: 45, minRotation: 0 } },
        y: { min: 0, max: 100, grid: { color: "rgba(35,46,65,0.4)" }, border: { display: false } },
      },
      plugins: { legend: { position: "top", align: "end" } },
    },
  });
}

// the analysis panes are expensive, so only the visible one runs
function refreshTab() {
  if (tab === "sensitivity") runSensitivity();
  else if (tab === "response") runSweep();
  else if (tab === "breaking") runThreshold();
}

// ------------------------------------------------------------ sensitivity

async function runSensitivity() {
  const box = $("sens-body");
  box.innerHTML = `<div class="spinner-line">Sweeping every knob across its full range...</div>`;
  const data = await post("/api/scenario/sensitivity", payload());
  if (data.error) { box.innerHTML = `<div class="error-note">${data.error}</div>`; return; }

  const max = Math.max(...data.sensitivity.map(s => s.swing), 1);
  box.innerHTML = data.sensitivity.map(s => {
    const driven = s.driven ? data.titles[s.driven] : "";
    return `<div class="sens-row">
      <div><div class="sn">${s.label}</div>
           <div class="drv">${s.swing > 0.5 ? `hits ${driven} hardest` : "no effect here"}</div></div>
      <div class="track"><div class="fill" style="width: ${(s.swing / max * 100).toFixed(1)}%"></div></div>
      <div class="sv">${s.swing.toFixed(1)}</div>
    </div>`;
  }).join("") + `<p class="muted small mt-2">${data.note}. The number is the largest
    risk-score swing any model shows between the two ends of that slider.</p>`;
}

// --------------------------------------------------------------- response

async function runSweep() {
  const box = $("sweep-note");
  box.textContent = "Computing the response curve...";
  const data = await post("/api/scenario/sweep", payload({ knob: $("sweep-knob").value, steps: 21 }));
  if (data.error) { box.innerHTML = `<span class="error-note">${data.error}</span>`; return; }

  const flat = data.series.filter(s => Math.max(...s.data) - Math.min(...s.data) < 0.5).length;
  box.textContent = `Every model scored across the full ${data.label.toLowerCase()} range, with all `
    + `other sliders held where you left them. ${data.series.length - flat} of ${data.series.length} `
    + `models respond to this variable at this location.`;

  charts.sweep?.destroy();
  charts.sweep = new Chart($("sweep-chart"), {
    type: "line",
    data: {
      labels: data.values.map(v => `${v}${data.unit === "x" ? "x" : ""}`),
      datasets: data.series.map((s, i) => ({
        label: s.title.replace(" Intelligence", ""), data: s.data,
        borderColor: PALETTE[i % PALETTE.length], backgroundColor: "transparent",
        borderWidth: 2, pointRadius: 0, pointHoverRadius: 4, tension: 0.3,
      })),
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      scales: {
        x: { grid: { display: false }, title: { display: true, text: `${data.label} (${data.unit})` },
             ticks: { maxTicksLimit: 11, maxRotation: 0 } },
        y: { min: 0, max: 100, grid: { color: "rgba(35,46,65,0.4)" }, border: { display: false },
             title: { display: true, text: "Risk score" } },
      },
      plugins: { legend: { position: "top", align: "end", labels: { boxWidth: 10, padding: 12 } } },
    },
  });
}

// --------------------------------------------------------------- breaking

async function runThreshold() {
  const box = $("thr-answer");
  box.innerHTML = `<div class="spinner-line">Solving...</div>`;
  const data = await post("/api/scenario/threshold", payload({
    module: $("thr-module").value, knob: $("thr-knob").value, target: $("thr-target").value }));
  if (data.error) { box.innerHTML = `<div class="error-note">${data.error}</div>`; return; }

  box.innerHTML = data.reachable
    ? `<div class="big">${data.value > 0 && data.unit !== "x" ? "+" : ""}${data.value} ${data.unit}</div>
       <div class="lede">${data.message} It currently sits at ${data.current_score}.</div>`
    : `<div class="big" style="color: var(--ink-3)">out of range</div>
       <div class="lede">${data.message} It currently sits at ${data.current_score}.</div>`;
}

// ------------------------------------------------------- share and export

function writeHash() {
  const d = activeDeltas();
  const parts = Object.entries(d).map(([k, v]) => `${k}=${v}`);
  if (loc) parts.unshift(`at=${loc.lat.toFixed(3)},${loc.lon.toFixed(3)}`);
  history.replaceState(null, "", parts.length ? `#${parts.join("&")}` : location.pathname);
}

function applyHash() {
  const hash = location.hash.slice(1);
  if (!hash) return;
  const deltas = {};
  for (const part of hash.split("&")) {
    const [k, v] = part.split("=");
    if (k in meta.knobs) deltas[k] = parseFloat(v);
  }
  if (Object.keys(deltas).length) setDeltas(deltas);
}

async function share() {
  writeHash();
  try {
    await navigator.clipboard.writeText(location.href);
    $("btn-share").textContent = "Link copied";
  } catch (e) {
    $("btn-share").textContent = "Copy from the address bar";
  }
  setTimeout(() => { $("btn-share").textContent = "Share this scenario"; }, 2200);
}

function exportCSV() {
  if (!lastRun) return;
  const d = activeDeltas();
  const lines = [`# TerraSight scenario, ${locName()}, ${new Date().toISOString()}`];
  lines.push(`# adjustments: ${Object.entries(d).map(([k, v]) =>
    `${meta.knobs[k].label} ${fmt(v, meta.knobs[k].unit)}`).join("; ") || "none (observed conditions)"}`);
  lines.push("module,title,baseline_score,scenario_score,change");
  for (const r of lastRun.results) {
    lines.push(`${r.module},"${r.title}",${r.before},${r.after},${r.change}`);
  }
  const url = URL.createObjectURL(new Blob([lines.join("\n")], { type: "text/csv" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = `terrasight-scenario-${loc.name.toLowerCase().replace(/\W+/g, "-")}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

TS.searchBox($("loc-search"), loadBaseline);
const saved = TS.initialLocation();
if (saved) { $("loc-search").value = saved.name || ""; loadBaseline(saved); }
