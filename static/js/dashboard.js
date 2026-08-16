// command center. the map has two modes:
//
//   live      worldwide reported activity, dense seismicity plus open events.
//             honest about what it is: a map of what has been *reported*, which
//             is why most of the map is dark most of the time.
//   national  the modelled hazard field over every US state. ~800 land points on
//             a regular grid, each scored by the same trained models the module
//             pages use, so the whole country carries a value whether or not
//             anything has happened there. this is the full pipeline view.

const map = TS.makeMap("map", [22, 8], 2);
let locMarker = null;
const gibsActive = {};
let mode = "live";

const KIND_META = {
  wildfire:   { color: "#e0703a", label: "Wildfires",   icon: "wildfire" },
  earthquake: { color: "#b06fb0", label: "Earthquakes", icon: "earthquake" },
  storm:      { color: "#d9a13b", label: "Storms",      icon: "cyclone" },
  volcano:    { color: "#e05252", label: "Volcanoes",   icon: "volcano" },
  flood:      { color: "#5b93d9", label: "Floods",      icon: "flood" },
  other:      { color: "#35b39c", label: "Other",       icon: "alert" },
};

const active = new Set(Object.keys(KIND_META));   // which kinds feed the heatmap
const heatByKind = {};                            // kind -> [[lat,lon,weight],...]
const markersByKind = {};                         // kind -> L.layerGroup (built lazily)
for (const k of Object.keys(KIND_META)) { heatByKind[k] = []; markersByKind[k] = L.layerGroup(); }

let heatOn = true, markersOn = false, liveCounts = {};
const GRADIENT = { 0.2: "#2f7d8c", 0.4: "#35b39c", 0.6: "#d9a13b", 0.8: "#e0703a", 1.0: "#e05252" };
// the live layer is closer to what leaflet.heat was written for -- scattered
// reports rather than a lattice -- but its legend reads "Reported hazard
// intensity", and intensity is a property of the event, not of how far you have
// zoomed. maxZoom: 0 for the same reason as the grid below: it pins the
// zoom-intensity multiplier at 1 so a magnitude-6 quake is the same colour at
// every zoom. Overlapping reports still add, which is the honest reading of a
// cluster; only the zoom rescaling was wrong.
const heatLayer = L.heatLayer([], {
  radius: 22, blur: 16, maxZoom: 0, minOpacity: 0.22, gradient: GRADIENT,
}).addTo(map);

// the modelled field is a regular lattice, so it wants a wider, softer kernel
// than scattered point events: adjacent cells should blend into a continuous
// surface rather than read as 800 separate dots.
//
// maxZoom: 0 is deliberate and load-bearing. leaflet.heat multiplies every
// point's intensity by
//     v = 1 / 2 ^ clamp(options.maxZoom - map.getZoom(), 0, 12)
// which is right for a density map of scattered events -- zooming in spreads
// them apart, so each one has to count for more -- and completely wrong here.
// These points are a modelled field: each carries an absolute 0-1 risk score,
// and 0.8 has to mean the same colour at every zoom. With maxZoom: 8 the same
// cell rendered 1/16 intensity at zoom 4 and full intensity at zoom 8, so the
// whole country changed colour as you scrolled. Pinning maxZoom to 0 forces
// v == 1 at every zoom, and the gradient then reads the score directly.
const gridHeat = L.heatLayer([], {
  // placeholder geometry; resizeGridHeat() derives the real radius and blur from
  // the on-screen point spacing before this ever draws. kept in the same 0.30
  // blur ratio as KERNEL_BLUR so the two cannot drift apart.
  radius: 40, blur: 12, maxZoom: 0, minOpacity: 0.3, max: 1.0, gradient: GRADIENT,
});

// leaflet.heat sizes its kernel in screen pixels, but the grid is spaced in
// degrees, so a fixed radius only looks continuous at one zoom level. one degree
// is about 11px at zoom 4 and 182px at zoom 8: leave the radius at 40 and the
// national sheet shatters into isolated dots the moment anyone zooms into their
// own state, which reads as missing data rather than as a rendering artifact.
// re-derive it from the actual point spacing on every zoom instead.
//
// The kernel also decides whether the colours are honest, not just whether the
// sheet is continuous, and the binding constraint is how far it actually reaches.
// leaflet.heat draws each point as a radial ramp of `radius` and then applies a
// canvas shadowBlur of `blur` on top, so a point paints out to
//
//     reach = radius * (1 + KERNEL_BLUR)
//
// and every point inside that reach adds its intensity to this one's, because
// the circles composite source-over and _colorize maps the *accumulated* alpha
// through the gradient. Once reach exceeds the point spacing a cell is coloured
// by its neighbours as much as by itself: at radius 0.75x spacing with the stock
// blur of 0.85x radius, reach is 1.39 spacings, and a real 0.30 cell in New
// Mexico sampled green at zoom 7 and orange at zoom 8. Alone it is teal at both.
//
// So reach has to stay under one spacing. Shrinking the radius to get there is
// the wrong lever -- it holds the colour but the sheet breaks into 800 visible
// dots. Shrinking the blur instead buys the same headroom and keeps the disc
// wide: 0.70 * 1.30 = 0.91 spacings of reach, with discs of 0.70 spacings that
// still overlap their neighbours (2 x 0.70 > 1) and read as one surface.
//
// Measured on the live grid, this renders the exact gradient colour for the
// score at every zoom: 0.551 computes to rgb(177,165,83) and samples (177,165,81),
// 0.300 computes to (50,152,148) and samples (50,152,149) -- the same values
// scoreColor() gives the tiled cells, so the two agree where they hand over.
const KERNEL_RATIO = 0.70;
const KERNEL_BLUR = 0.30;
// below this the kernel is too small to read as a surface, above it the blur can
// no longer bridge the gap between points. outside the band the tiled cells take
// over: same numbers, drawn as exact 1-degree squares.
const HEAT_MIN_PX = 12, HEAT_MAX_PX = 260;

function gridGeometry() {
  const spacing = (gridData && gridData.spacing_deg) || 1;
  const c = map.getCenter();
  const z = map.getZoom();
  const a = map.project([c.lat, c.lng], z);
  const b = map.project([c.lat, c.lng + spacing], z);
  const spacingPx = Math.abs(b.x - a.x);
  const radius = spacingPx * KERNEL_RATIO;
  return { radius, faithful: radius >= HEAT_MIN_PX && radius <= HEAT_MAX_PX };
}

function resizeGridHeat() {
  if (!map.hasLayer(gridHeat)) return;
  const g = gridGeometry();
  // never clamp the radius into a range where neighbours would reach this
  // point's centre. if the honest kernel is unusable at this zoom, stop drawing
  // heat and let the cells carry the map instead of drawing a blended sheet that
  // lies about the data.
  gridHeat.setOptions({ radius: g.radius, blur: g.radius * KERNEL_BLUR });
  gridHeat.setLatLngs(g.faithful && gridData ? gridData.points : []);
  if (g.faithful !== !autoCells) {
    autoCells = !g.faithful;
    drawCells();
  }
}

map.on("zoomend", resizeGridHeat);
const gridCells = L.layerGroup();
let gridData = null, gridLayerName = "composite", cellsOn = false, statusTimer = null;
// set by resizeGridHeat when the zoom outruns the heat kernel, so the cells can
// appear on their own without clobbering the user's explicit toggle
let autoCells = false;

// the outlook animation. each lead day is a separate small response, so frames
// are cached by "layer:day" the first time they are drawn: scrubbing back and
// forth then costs nothing, and playing the week through twice only ever fetches
// seven times.
const frameCache = new Map();
let gridDay = 0, playTimer = null, outlookDays = [];

// 0-100 risk score to the same ramp the heat layer uses, for the crisp cells
function scoreColor(v) {
  const stops = [[0, "#1d4f5c"], [25, "#2f7d8c"], [40, "#35b39c"],
                 [55, "#d9a13b"], [70, "#e0703a"], [85, "#e05252"]];
  let c = stops[0][1];
  for (const [t, col] of stops) if (v >= t) c = col;
  return c;
}

function rebuildHeat() {
  const pts = [];
  for (const k of active) pts.push(...heatByKind[k]);
  heatLayer.setLatLngs(pts);
  if (mode === "live" && heatOn && !map.hasLayer(heatLayer)) heatLayer.addTo(map);
}

function syncMarkers() {
  for (const k of Object.keys(KIND_META)) {
    const shouldShow = mode === "live" && markersOn && active.has(k);
    if (shouldShow && !map.hasLayer(markersByKind[k])) markersByKind[k].addTo(map);
    if (!shouldShow && map.hasLayer(markersByKind[k])) map.removeLayer(markersByKind[k]);
  }
}

// ---------- GIBS science layer toggles ----------
const toolbar = document.getElementById("layer-toolbar");
["satellite", "thermal", "precip", "snow"].forEach((key) => {
  const b = document.createElement("button");
  b.textContent = TS.GIBS_LAYERS[key].name;
  b.addEventListener("click", () => {
    if (gibsActive[key]) { map.removeLayer(gibsActive[key]); delete gibsActive[key]; b.classList.remove("on"); }
    else { gibsActive[key] = TS.gibsLayer(key).addTo(map); b.classList.add("on"); }
  });
  toolbar.appendChild(b);
});

// ---------- global heatmap data ----------
(async () => {
  const data = await TS.fetchJSON("/api/live/heatmap");
  if (data.error || !data.points) return;
  data.points.forEach((p) => {
    const kind = KIND_META[p.kind] ? p.kind : "other";
    heatByKind[kind].push([p.lat, p.lon, p.weight]);
    // keep the marker layer light: only notable events, not every M4 quake
    if (p.weight >= 0.55 || kind !== "earthquake") {
      const color = KIND_META[kind].color;
      L.circleMarker([p.lat, p.lon], { radius: 5, color, weight: 1.4, fillColor: color, fillOpacity: 0.5 })
        .bindPopup(`<strong>${KIND_META[kind].label.replace(/s$/, "")}</strong>`)
        .addTo(markersByKind[kind]);
    }
  });
  liveCounts = data.counts || {};
  rebuildHeat();
  if (mode === "live") { buildLiveControls(); buildLegend(); }
})();

// side feeds come from the lighter overview endpoint
(async () => {
  const o = await TS.fetchJSON("/api/live/overview");
  if (!o.error) fillFeeds(o);
})();

// ---------- mode switch ----------
document.querySelectorAll("#map-modes button").forEach(btn => {
  btn.addEventListener("click", () => setMode(btn.dataset.mode));
});

function setMode(next) {
  if (next === mode) return;
  mode = next;
  document.querySelectorAll("#map-modes button").forEach(b => b.classList.toggle("on", b.dataset.mode === mode));
  document.getElementById("national-card").hidden = mode !== "national";

  if (mode === "national") {
    map.removeLayer(heatLayer);
    syncMarkers();
    gridHeat.addTo(map);
    resizeGridHeat();
    document.getElementById("map-hint").textContent =
      "Every point in the country carries a modelled score, not just where something was reported. Click anywhere to run the full 16-module assessment on it.";
    map.flyTo([39.5, -98.5], 4, { duration: 1.0 });
    loadGrid();
  } else {
    stopPlay();
    document.getElementById("outlook-bar").hidden = true;
    map.removeLayer(gridHeat);
    map.removeLayer(gridCells);
    if (heatOn) heatLayer.addTo(map);
    syncMarkers();
    document.getElementById("map-hint").textContent =
      "Brighter areas carry more reported hazard activity. Click anywhere to assess it; filter the field by type below.";
    buildLiveControls();
    buildLegend();
  }
}

// ---------- national model grid ----------
async function loadGrid(layerName, day) {
  // switching hazard resets to today: a wildfire outlook and a flood outlook are
  // different questions, and carrying day 5 across the switch just confuses
  if (layerName && layerName !== gridLayerName) { gridDay = 0; }
  gridLayerName = layerName || gridLayerName;
  if (day != null) gridDay = day;
  const box = document.getElementById("national-body");
  if (!gridData) box.innerHTML = `<div class="spinner-line">Loading the national model grid...</div>`;

  const res = await frame(gridLayerName, gridDay);
  if (res.building || res.error) {
    // a cold first build takes a few minutes; show real progress instead of an
    // empty map that would read as "no data"
    renderBuilding(res.status || null, res.error);
    pollStatus();
    return;
  }
  gridData = res;
  outlookDays = res.days || [];
  gridHeat.setLatLngs(res.points);
  resizeGridHeat();   // may swap heat for cells depending on the current zoom
  drawCells();
  buildGridControls();
  buildOutlook();
  buildLegend();
  renderSummary();
}

function frame(layerName, day) {
  const key = `${layerName}:${day}`;
  if (!frameCache.has(key)) {
    frameCache.set(key, TS.fetchJSON(`/api/national/grid?layer=${layerName}&day=${day}`)
      .then((res) => {
        // a failed frame must not poison the cache, or the retry never happens
        if (res.building || res.error) frameCache.delete(key);
        return res;
      }));
  }
  return frameCache.get(key);
}

// ---------- seven day outlook ----------
const DAY_NAMES = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

function dayLabel(i) {
  if (i === 0) return "Today";
  if (i === 1) return "Tomorrow";
  const iso = outlookDays[i];
  if (!iso) return `+${i} days`;
  // the dates are plain calendar days from the model, so parse them as local
  // noon rather than UTC midnight, which in the Americas lands on the day before
  const d = new Date(`${iso}T12:00:00`);
  return `${DAY_NAMES[d.getDay()]}, +${i}d`;
}

function buildOutlook() {
  const bar = document.getElementById("outlook-bar");
  const slider = document.getElementById("outlook-slider");
  // an older cached grid, built before the outlook existed, has one day in it
  bar.hidden = mode !== "national" || outlookDays.length < 2;
  if (bar.hidden) { stopPlay(); return; }
  slider.max = outlookDays.length - 1;
  slider.value = gridDay;
  document.getElementById("outlook-day").textContent = dayLabel(gridDay);
  document.getElementById("outlook-date").textContent = outlookDays[gridDay] || "";
}

document.getElementById("outlook-slider").addEventListener("input", (e) => {
  stopPlay();
  loadGrid(null, parseInt(e.target.value, 10));
});

document.getElementById("outlook-play").addEventListener("click", () => {
  if (playTimer) { stopPlay(); return; }
  play();
});

async function play() {
  const btn = document.getElementById("outlook-play");
  btn.classList.add("on");
  btn.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="M7 5h4v14H7zM13 5h4v14h-4z"/></svg>`;
  // fetch the whole week before the first frame moves, so the animation runs at
  // a steady beat instead of stuttering on whichever day is still in flight
  await Promise.all(outlookDays.map((_, i) => frame(gridLayerName, i)));
  if (!btn.classList.contains("on")) return;   // stopped while the week loaded
  const step = () => {
    const next = (gridDay + 1) % outlookDays.length;
    loadGrid(null, next);
    // hold a beat longer on today, which is the frame people actually read
    playTimer = setTimeout(step, next === 0 ? 1400 : 800);
  };
  playTimer = setTimeout(step, 600);
}

function stopPlay() {
  clearTimeout(playTimer);
  playTimer = null;
  const btn = document.getElementById("outlook-play");
  btn.classList.remove("on");
  btn.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>`;
}

function renderBuilding(status, message) {
  const box = document.getElementById("national-body");
  const p = status && status.progress;
  const pct = p && p.total ? Math.round(100 * p.done / p.total) : 0;
  box.innerHTML = `
    <p class="muted small">${message || "The national grid is building."}</p>
    <div class="meter mt-2"><i style="width:${pct}%; background: var(--accent)"></i></div>
    <p class="muted small mt-1">${p && p.total ? `${p.done} of ${p.total} grid points scored` : "starting up"}
      ${status && status.phase ? ` (${status.phase} pass)` : ""}</p>`;
}

function pollStatus() {
  clearTimeout(statusTimer);
  statusTimer = setTimeout(async () => {
    if (mode !== "national") return;
    const s = await TS.fetchJSON("/api/national/status");
    if (s.has_grid && !s.building) { loadGrid(); return; }
    renderBuilding(s);
    pollStatus();
  }, 4000);
}

function drawCells() {
  gridCells.clearLayers();
  if ((!cellsOn && !autoCells) || !gridData) { map.removeLayer(gridCells); return; }
  const half = (gridData.spacing_deg || 1) / 2;
  gridData.cells.forEach((c) => {
    L.rectangle([[c.lat - half, c.lon - half], [c.lat + half, c.lon + half]], {
      // opacity is tuned to sit close to what the heat kernel renders at the
      // same score, so crossing the zoom where one hands over to the other is
      // not itself read as the colours changing
      color: scoreColor(c.v), weight: 0.5, fillColor: scoreColor(c.v),
      fillOpacity: 0.6, interactive: true,
    }).bindPopup(cellPopup(c)).addTo(gridCells);
  });
  gridCells.addTo(map);
}

function cellPopup(c) {
  const ctx = c.ctx || {};
  const bits = Object.entries(ctx).slice(0, 5)
    .map(([k, v]) => `<div style="display:flex;gap:10px;justify-content:space-between">
        <span style="color:var(--ink-3)">${k.replace(/_/g, " ")}</span><span>${v}</span></div>`).join("");
  return `<strong>${gridData.label}: ${c.v.toFixed(0)}</strong><br>
    <span style="color:var(--ink-3)">${c.state}, ${c.lat.toFixed(1)}, ${c.lon.toFixed(1)}</span>
    <div style="margin-top:6px;font-size:12px">${bits}</div>`;
}

function buildGridControls() {
  const box = document.getElementById("type-filters");
  box.innerHTML = "";
  for (const [key, meta] of Object.entries(gridData.available_layers || {})) {
    const chip = document.createElement("button");
    chip.className = `chip toggle ${key === gridLayerName ? "on" : "off"}`;
    chip.innerHTML = `${meta.label} <span class="k" style="opacity:.55">${meta.method}</span>`;
    chip.addEventListener("click", () => loadGrid(key));
    box.appendChild(chip);
  }
  const cells = document.createElement("button");
  cells.className = `chip toggle ${cellsOn ? "on" : "off"}`;
  cells.innerHTML = `<svg width="13" height="13" style="vertical-align:-2px"><use href="#i-layers"/></svg> Grid cells`;
  cells.addEventListener("click", () => {
    cellsOn = cells.classList.toggle("on");
    cells.classList.toggle("off", !cellsOn);
    drawCells();
  });
  box.appendChild(cells);
}

function renderSummary() {
  const g = gridData, s = g.summary || {};
  const hours = Math.round((g.age_seconds || 0) / 360) / 10;
  const cov = g.coverage || {};
  const stale = cov.carried_points
    ? ` <span class="muted">${cov.carried_points} carried from the previous pass</span>` : "";
  const lead = gridDay > 0
    ? ` Showing the forecast for <strong>${g.date}</strong>, ${gridDay} day${gridDay > 1 ? "s" : ""} out.`
    : "";

  document.getElementById("national-body").innerHTML = `
    <p class="muted small">${g.count} grid points at ${g.spacing_deg} degree spacing across
      ${(s.states || []).length} states and territories, scored ${hours} h ago by
      ${g.method === "model" ? "a trained classifier" : g.method === "blend" ? "a weighted blend of all layers" : "a documented threshold index"}.${stale}${lead}</p>

    <div class="panel-title mt-3">Highest-risk states, ${g.label.toLowerCase()}
      ${gridDay > 0 ? `<span class="muted">${dayLabel(gridDay).toLowerCase()}</span>` : ""}</div>
    ${[...(s.states || [])].sort((a, b) => (b[gridLayerName] || 0) - (a[gridLayerName] || 0))
      .slice(0, 8).map(st => `
      <div class="matrix-row" style="grid-template-columns: 1fr 90px 44px">
        <a>${st.state}</a>
        <div class="meter"><i style="width:${Math.max(st[gridLayerName] || 0, 2)}%; background:${scoreColor(st[gridLayerName] || 0)}"></i></div>
        <span class="score" style="color:${scoreColor(st[gridLayerName] || 0)}">${(st[gridLayerName] || 0).toFixed(0)}</span>
      </div>`).join("")}

    <div class="panel-title mt-3">National hotspots</div>
    ${(s.hotspots || []).slice(0, 6).map(h => `
      <div class="feed-item hotspot" data-lat="${h.lat}" data-lon="${h.lon}">
        <span class="tag">${h.driver || "composite"}</span>
        <span class="t">${h.state} <span class="muted">${h.lat.toFixed(1)}, ${h.lon.toFixed(1)}</span></span>
        <span class="m" style="color:${scoreColor(h.composite)}">${h.composite.toFixed(0)}</span>
      </div>`).join("")}
    <p class="muted small mt-2">Click a hotspot to fly there and assess it.</p>`;

  document.querySelectorAll(".hotspot").forEach(el => {
    el.addEventListener("click", async () => {
      const lat = parseFloat(el.dataset.lat), lon = parseFloat(el.dataset.lon);
      const loc = await TS.fetchJSON(`/api/reverse-geocode?lat=${lat}&lon=${lon}`);
      assess({ name: loc.name || `${lat.toFixed(2)}, ${lon.toFixed(2)}`, lat, lon, admin1: loc.admin1 });
    });
  });
}

// ---------- filter chips, heatmap + markers toggles ----------
function buildLiveControls() {
  const box = document.getElementById("type-filters");
  box.innerHTML = "";
  Object.keys(KIND_META).forEach((k) => {
    if (!liveCounts[k]) return;
    const chip = document.createElement("button");
    chip.className = active.has(k) ? "chip on" : "chip off";
    chip.innerHTML = `<span class="dot" style="color:${KIND_META[k].color}"></span>${KIND_META[k].label} <span class="k" style="opacity:.6">${liveCounts[k]}</span>`;
    chip.addEventListener("click", () => {
      const on = chip.classList.toggle("on");
      chip.classList.toggle("off", !on);
      if (on) active.add(k); else active.delete(k);
      rebuildHeat(); syncMarkers();
    });
    box.appendChild(chip);
  });

  const heat = document.createElement("button");
  heat.className = `chip toggle ${heatOn ? "on" : "off"}`;
  heat.innerHTML = `<svg width="13" height="13" style="vertical-align:-2px"><use href="#i-layers"/></svg> Heatmap`;
  heat.addEventListener("click", () => {
    heatOn = heat.classList.toggle("on");
    if (heatOn) heatLayer.addTo(map); else map.removeLayer(heatLayer);
  });
  box.appendChild(heat);

  const mk = document.createElement("button");
  mk.className = `chip toggle ${markersOn ? "on" : "off"}`;
  mk.innerHTML = `<svg width="13" height="13" style="vertical-align:-2px"><use href="#i-pin"/></svg> Markers`;
  mk.addEventListener("click", () => {
    markersOn = mk.classList.toggle("on");
    mk.classList.toggle("off", !markersOn);
    syncMarkers();
  });
  box.appendChild(mk);
}

function buildLegend() {
  const box = document.getElementById("map-legend");
  const ramp = `<span class="li"><span class="sw" style="background:linear-gradient(90deg,#2f7d8c,#35b39c,#d9a13b,#e0703a,#e05252)"></span>`;
  const items = mode === "national"
    ? [`${ramp}${gridData ? gridData.label : "Hazard"} score, low to extreme</span>`,
       `<span class="li muted">Modelled everywhere, not only where events were reported</span>`]
    : [`${ramp}Reported hazard intensity</span>`,
       ...Object.keys(KIND_META).filter(k => liveCounts[k])
         .map(k => `<span class="li"><span class="sw" style="background:${KIND_META[k].color}"></span>${KIND_META[k].label}</span>`)];
  items.push(`<span class="li" style="margin-left:auto">Assessed location <span class="sw" style="background:transparent;border:2px solid var(--accent)"></span></span>`);
  box.innerHTML = items.join("");
}

function fillFeeds(data) {
  const evBox = document.getElementById("feed-events");
  evBox.innerHTML = "";
  (data.eonet_events || []).slice(0, 9).forEach((e) => {
    const row = document.createElement("div"); row.className = "feed-item";
    row.innerHTML = `<span class="tag">${(e.category || "event").replace(/([A-Z])/g, " $1")}</span>
      <span class="t">${e.title}</span><span class="m">${e.date ? e.date.slice(5, 10) : ""}</span>`;
    evBox.appendChild(row);
  });
  if (!evBox.children.length) evBox.innerHTML = `<p class="muted small">No open events reported.</p>`;

  const stBox = document.getElementById("feed-storms");
  stBox.innerHTML = "";
  (data.active_storms || []).forEach((s) => {
    const row = document.createElement("div"); row.className = "feed-item";
    row.innerHTML = `<span class="tag">${s.classification || "TC"}</span>
      <span class="t">${s.name}</span><span class="m">${s.intensity_kt || "?"} kt</span>`;
    stBox.appendChild(row);
  });
  if (!stBox.children.length) stBox.innerHTML = `<p class="muted small">No active tropical cyclones in the Atlantic or East Pacific basins.</p>`;

  const qBox = document.getElementById("feed-quakes");
  qBox.innerHTML = "";
  (data.significant_quakes || []).slice(0, 9).forEach((q) => {
    const row = document.createElement("div"); row.className = "feed-item";
    const sev = q.magnitude >= 6.5 ? "extreme" : q.magnitude >= 5.5 ? "high" : "moderate";
    row.innerHTML = `<span class="tag risk-${sev}">M${q.magnitude?.toFixed(1)}</span>
      <span class="t">${(q.place || "").slice(0, 44)}</span>`;
    qBox.appendChild(row);
  });
}

// ---------- click anywhere on the map to assess that location ----------
map.on("click", async (e) => {
  const { lat, lng } = e.latlng;
  matrixLoc.textContent = "· locating...";
  const loc = await TS.fetchJSON(`/api/reverse-geocode?lat=${lat}&lon=${lng}`);
  assess({ name: loc.name || `${lat.toFixed(2)}, ${lng.toFixed(2)}`, lat, lon: lng, admin1: loc.admin1 });
});

// ---------- location assessment ----------
const matrixBody = document.getElementById("matrix-body");
const matrixLoc = document.getElementById("matrix-loc");

async function assess(loc) {
  TS.saveLocation(loc);
  watchLoc = loc;
  syncAddButton();
  matrixLoc.textContent = `· ${loc.name}`;
  matrixBody.innerHTML = `<div class="spinner-line">Running all 16 modules on live data for ${loc.name}. A fresh location takes 20 to 60 seconds while the feeds load...</div>`;
  document.getElementById("cascade-card").hidden = true;

  map.flyTo([loc.lat, loc.lon], Math.max(map.getZoom(), 5), { duration: 1.2 });
  if (locMarker) map.removeLayer(locMarker);
  locMarker = L.circleMarker([loc.lat, loc.lon], { radius: 10, color: TS.theme.accent, weight: 3, fillOpacity: 0.15 })
    .addTo(map).bindPopup(`<strong>${loc.name}</strong>`).openPopup();

  const name = `${loc.name}${loc.admin1 ? ", " + loc.admin1 : ""}`;
  const data = await TS.fetchJSON(`/api/assess-all?lat=${loc.lat}&lon=${loc.lon}&name=${encodeURIComponent(name)}`);
  if (data.error) { matrixBody.innerHTML = `<div class="error-note">${data.error}</div>`; return; }

  const rows = Object.entries(data.results)
    .filter(([, r]) => !r.error)
    .map(([slug, r]) => ({ slug, score: r.assessment.score, level: r.assessment.level,
      title: window.TS_MODULES[slug].title,
      boost: r.cascades && r.cascades.score_after > r.cascades.score_before,
      grid: (r.data_provenance || {}).national_grid_fallback,
      sub: (r.data_provenance || {}).substituted_location }))
    .sort((a, b) => b.score - a.score);

  matrixBody.innerHTML = "";
  rows.forEach(({ slug, score, level, title, boost, sub, grid }) => {
    const row = document.createElement("div");
    row.className = "matrix-row";
    // when a module had to borrow a neighbouring location's data, say so on the
    // row rather than quietly presenting it as a reading for this exact spot
    const tags = (boost ? ` <span class="tag" title="raised by cascade coupling">coupled</span>` : "")
      + (grid ? ` <span class="tag" title="live feed over quota, so this is the national 1-degree model run from ${grid.age_hours}h ago, cell ${Math.round(grid.distance_km)} km away">national model</span>` : "")
      + (sub ? ` <span class="tag" title="no data at this exact point, modelled from ${Math.round(sub.distance_km)} km away">nearest ${Math.round(sub.distance_km)} km</span>` : "");
    row.innerHTML = `
      <svg><use href="#i-${slug}"/></svg>
      <a href="/module/${slug}?lat=${loc.lat}&lon=${loc.lon}&name=${encodeURIComponent(name)}">${title}${tags}</a>
      <div class="meter"><i style="width: 0%; background: ${TS.riskColor(level)}"></i></div>
      <span class="score" style="color: ${TS.riskColor(level)}">${score.toFixed(0)}</span>`;
    matrixBody.appendChild(row);
    requestAnimationFrame(() => row.querySelector("i").style.width = `${Math.max(score, 2)}%`);
  });
  const failed = Object.entries(data.results).filter(([, r]) => r.error);
  if (failed.length) {
    const note = document.createElement("p");
    note.className = "muted small mt-2";
    // a momentary feed timeout and an exhausted daily quota look identical from
    // here but need opposite advice: one is worth retrying immediately, the
    // other will not clear until the upstream counter rolls over at UTC
    // midnight. offering "Retry" for both is how someone ends up clicking it
    // twenty times against a door that stays shut for another eight hours.
    const wait = Math.max(0, ...failed.map(([, r]) => r.retry_after || 0));
    const names = failed.map(([s]) => s).join(", ");
    if (wait > 900) {
      const hrs = Math.floor(wait / 3600), mins = Math.round((wait % 3600) / 60);
      note.innerHTML = `${failed.length} module(s) are waiting on the weather API's `
        + `free-tier quota, which resets in ${hrs ? hrs + "h " : ""}${mins}m: ${names}. `
        + `Everything else on this page is live.`;
    } else {
      note.innerHTML = `${failed.length} module(s) couldn't load just now (a live data `
        + `feed was busy): ${names}. This is usually temporary. `
        + `<a href="#" id="retry-assess">Retry</a>`;
    }
    matrixBody.appendChild(note);
    const retry = document.getElementById("retry-assess");
    if (retry) retry.addEventListener("click", (e) => {
      e.preventDefault(); assess(loc);
    });
  }

  const activeEdges = (data.edges || []).filter(e => e.active && e.boost > 0);
  if (activeEdges.length) {
    document.getElementById("cascade-card").hidden = false;
    document.getElementById("cascade-body").innerHTML = activeEdges.map(e => `
      <div class="feed-item"><span class="tag">${e.source} to ${e.target}</span>
        <span class="t small">${e.mechanism}</span><span class="m risk-high">+${e.boost}</span></div>`).join("");
  }
}

// ---------- watchlist ----------
// the whole point of a watchlist is that it survives the tab closing, and there
// are no accounts here, so it lives in localStorage and is posted with each
// check. the server keeps nothing about who asked, only anonymous grid history.
const WATCH_KEY = "ts-watchlist";
const WATCH_MAX = 25;                       // matches the server's cap
let watchLoc = null;                        // the currently assessed location

function loadWatch() {
  try { return JSON.parse(localStorage.getItem(WATCH_KEY)) || []; } catch { return []; }
}

function saveWatch(list) {
  localStorage.setItem(WATCH_KEY, JSON.stringify(list.slice(0, WATCH_MAX)));
}

function watchKey(loc) { return `${loc.lat.toFixed(3)},${loc.lon.toFixed(3)}`; }

function isWatched(loc) {
  return loc && loadWatch().some(w => watchKey(w) === watchKey(loc));
}

function toggleWatch(loc) {
  const list = loadWatch();
  const at = list.findIndex(w => watchKey(w) === watchKey(loc));
  if (at >= 0) list.splice(at, 1);
  else list.push({ name: loc.name, lat: loc.lat, lon: loc.lon });
  saveWatch(list);
  syncAddButton();
  renderWatch();
}

function syncAddButton() {
  const btn = document.getElementById("watch-add");
  btn.hidden = !watchLoc;
  if (!watchLoc) return;
  const on = isWatched(watchLoc);
  btn.className = `chip ${on ? "on" : ""}`;
  btn.innerHTML = `<svg width="13" height="13" style="vertical-align:-2px"><use href="#i-pin"/></svg>
    ${on ? "Remove" : "Watch"} ${watchLoc.name}`;
}

document.getElementById("watch-add").addEventListener("click", () => {
  if (watchLoc) toggleWatch(watchLoc);
});

document.getElementById("watch-threshold").addEventListener("change", renderWatch);

async function renderWatch() {
  const list = loadWatch();
  const body = document.getElementById("watch-body");
  const empty = document.getElementById("watch-empty");
  document.getElementById("watch-count").textContent = list.length ? `${list.length} saved` : "";
  empty.hidden = list.length > 0;
  if (!list.length) { body.innerHTML = ""; return; }

  body.innerHTML = `<div class="spinner-line">Checking ${list.length} location${list.length > 1 ? "s" : ""}...</div>`;
  const threshold = document.getElementById("watch-threshold").value;
  const res = await TS.fetchJSON("/api/alerts/check", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ watchlist: list, threshold }),
  });
  if (res.building) {
    body.innerHTML = `<p class="muted small">The national grid is still building. Your watchlist
      will be checked as soon as it finishes.</p>`;
    return;
  }
  if (res.error) { body.innerHTML = `<div class="error-note">${res.error}</div>`; return; }
  // the sparkline tooltips name their days, and on the live map the grid has not
  // been loaded yet, so borrow the calendar the alert check came back with
  if (!outlookDays.length) outlookDays = res.days || [];

  body.innerHTML = (res.items || []).map((it) => {
    if (it.error) {
      return `<div class="watch-row"><div class="wr-head"><span class="wr-name">${it.name}</span>
        <span class="muted small">${it.error}</span></div></div>`;
    }
    const w = it.worst;
    const comp = it.layers && it.layers.composite;
    const spark = comp ? sparkline(comp.series) : "";
    // "clear" is a real answer and deserves to be shown as confidently as an
    // alert: a watchlist that only speaks up when something is wrong leaves you
    // wondering every quiet day whether it is still working
    const verdict = w
      ? `<span class="tag risk-${w.band.toLowerCase()}">${w.band}</span>
         <span class="wr-what">${w.label} ${w.already ? "now" : "by " + (w.date || `+${w.lead_days}d`)}</span>
         <span class="wr-score" style="color:${scoreColor(w.peak)}">${w.peak.toFixed(0)}</span>`
      : `<span class="tag">clear</span>
         <span class="wr-what muted">nothing reaches ${it.threshold} this week</span>
         <span class="wr-score muted">${comp ? comp.now.toFixed(0) : ""}</span>`;
    return `
      <div class="watch-row ${w ? "alerting" : ""}">
        <div class="wr-head">
          <span class="wr-name" data-lat="${it.lat}" data-lon="${it.lon}" data-name="${it.name}">${it.name}</span>
          <button class="wr-drop" data-drop="${watchKey(it)}" title="Remove from watchlist" aria-label="Remove ${it.name}">&times;</button>
        </div>
        <div class="wr-body">${verdict}</div>
        ${spark}
        ${it.distance_km > 60 ? `<div class="muted" style="font-size:11px">nearest grid point ${it.distance_km.toFixed(0)} km away</div>` : ""}
      </div>`;
  }).join("");

  body.querySelectorAll(".wr-drop").forEach(btn => btn.addEventListener("click", () => {
    const list = loadWatch().filter(w => watchKey(w) !== btn.dataset.drop);
    saveWatch(list); syncAddButton(); renderWatch();
  }));
  body.querySelectorAll(".wr-name[data-lat]").forEach(el => el.addEventListener("click", () => {
    assess({ name: el.dataset.name, lat: parseFloat(el.dataset.lat), lon: parseFloat(el.dataset.lon) });
  }));

  const n = (res.alerts || []).length;
  if (n) {
    const line = document.createElement("p");
    line.className = "small mt-2";
    line.innerHTML = `<strong>${n}</strong> threshold crossing${n > 1 ? "s" : ""} in the next
      ${(res.days || []).length} days across your watchlist.`;
    body.appendChild(line);
  }
}

// seven little bars, one per lead day, so the shape of the week reads at a glance
function sparkline(series) {
  if (!series || series.length < 2) return "";
  return `<div class="spark" aria-hidden="true">${series.map((v, i) => `
    <i style="height:${Math.max(v, 3)}%; background:${scoreColor(v)}; opacity:${i === 0 ? 1 : 0.72}"
       title="${dayLabel(i)}: ${v.toFixed(0)}"></i>`).join("")}</div>`;
}

// ---------- national change feed ----------
(async () => {
  const res = await TS.fetchJSON("/api/alerts/changes");
  const card = document.getElementById("changes-card");
  if (!res || !res.available || !(res.changes || []).length) return;
  card.hidden = false;
  document.getElementById("changes-window").textContent = `last ${res.hours} h`;
  document.getElementById("changes-body").innerHTML = `
    <p class="muted small" style="margin-bottom:6px">${res.escalations} escalation${res.escalations === 1 ? "" : "s"},
      ${res.de_escalations} easing. Only moves that cross a risk band are listed.</p>
    ${res.changes.slice(0, 8).map(c => `
      <div class="feed-item hotspot" data-lat="${c.lat}" data-lon="${c.lon}">
        <span class="tag risk-${c.to_band.toLowerCase()}">${c.to_band}</span>
        <span class="t">${c.state} <span class="muted">${c.label}</span></span>
        <span class="m" style="color:${c.direction === "up" ? "var(--risk-high)" : "var(--ink-3)"}">
          ${c.direction === "up" ? "+" : ""}${c.delta}</span>
      </div>`).join("")}`;
  document.querySelectorAll("#changes-body .hotspot").forEach(el => {
    el.addEventListener("click", async () => {
      const lat = parseFloat(el.dataset.lat), lon = parseFloat(el.dataset.lon);
      if (isNaN(lat)) return;
      const loc = await TS.fetchJSON(`/api/reverse-geocode?lat=${lat}&lon=${lon}`);
      assess({ name: loc.name || `${lat.toFixed(2)}, ${lon.toFixed(2)}`, lat, lon, admin1: loc.admin1 });
    });
  });
})();

renderWatch();

TS.searchBox(document.getElementById("loc-search"), assess);

const saved = TS.initialLocation();
if (saved) {
  document.getElementById("loc-search").value = saved.name || "";
  assess(saved);
}
