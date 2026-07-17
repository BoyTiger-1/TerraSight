// scenario simulator: load a real baseline, expose sliders for the knobs the
// backend understands, debounce reruns, and chart before vs after

let loc = null, knobsMeta = null, chart = null, timer = null, firstLoad = true;

const $ = (id) => document.getElementById(id);

const PRESETS = {
  heat: { temp_delta_c: 5, precip_mult: 0.2, rh_delta_pct: -15 },
  storm: { precip_mult: 2.5, wind_mult: 1.9, rh_delta_pct: 15 },
  climate2c: { temp_delta_c: 2 },
  reset: {},
};

async function loadBaseline(picked) {
  loc = picked;
  firstLoad = true;
  TS.saveLocation(picked);
  $("empty-state").hidden = true;
  $("sim-ui").hidden = false;
  $("sim-results").innerHTML = `<div class="spinner-line">Building the real-conditions baseline for ${picked.name}. First load takes about 20 to 40 seconds while live data comes in, then every slider is instant...</div>`;
  if (!knobsMeta) {
    knobsMeta = await TS.fetchJSON("/api/scenario/knobs");
    buildKnobs();
  }
  await rerun();
}

function buildKnobs() {
  const box = $("knobs");
  box.innerHTML = "";
  for (const [key, k] of Object.entries(knobsMeta)) {
    const div = document.createElement("div");
    div.className = "knob";
    div.innerHTML = `
      <div class="k-head">
        <label for="knob-${key}">${k.label}</label>
        <span class="val" id="val-${key}">${k.default}${k.unit === "x" ? "x" : " " + k.unit}</span>
      </div>
      <input type="range" id="knob-${key}" min="${k.min}" max="${k.max}" step="${k.step}" value="${k.default}">`;
    box.appendChild(div);
    const input = div.querySelector("input");
    input.addEventListener("input", () => {
      $(`val-${key}`).textContent = `${input.value > 0 && k.unit === "C" && key.includes("delta") ? "+" : ""}${input.value}${k.unit === "x" ? "x" : " " + k.unit}`;
      clearTimeout(timer);
      timer = setTimeout(rerun, 350); // debounce so dragging feels instant
    });
  }
  document.querySelectorAll("[data-preset]").forEach(btn => {
    btn.addEventListener("click", () => {
      const preset = PRESETS[btn.dataset.preset];
      for (const [key, k] of Object.entries(knobsMeta)) {
        const v = preset[key] ?? k.default;
        $(`knob-${key}`).value = v;
        $(`val-${key}`).textContent = `${v}${k.unit === "x" ? "x" : " " + k.unit}`;
      }
      rerun();
    });
  });
}

function currentDeltas() {
  const d = {};
  for (const key of Object.keys(knobsMeta)) d[key] = parseFloat($(`knob-${key}`).value);
  return d;
}

async function rerun() {
  if (!loc) return;
  const busy = $("sim-busy");
  busy.textContent = firstLoad ? "building baseline" : "updating";
  busy.hidden = false;
  const name = `${loc.name}${loc.admin1 ? ", " + loc.admin1 : ""}`;
  let data;
  try {
    data = await TS.fetchJSON("/api/scenario/run", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ lat: loc.lat, lon: loc.lon, name, deltas: currentDeltas() }),
    });
  } catch (e) {
    data = { error: "Could not reach the server. Check your connection and try again." };
  }
  busy.hidden = true;   // always clears, even on error
  firstLoad = false;
  if (data.error) {
    $("sim-results").innerHTML = `<div class="error-note">${data.error}</div>`;
    return;
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

  // grouped bar chart, baseline vs scenario
  if (chart) chart.destroy();
  chart = new Chart($("sim-chart"), {
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

TS.searchBox($("loc-search"), loadBaseline);
const saved = TS.initialLocation();
if (saved) { $("loc-search").value = saved.name || ""; loadBaseline(saved); }
