// command center: a global risk heatmap you can read at a glance and click
// anywhere to assess. dense worldwide seismicity + live events drive the field.

// global view so the whole heatmap is visible on load
const map = TS.makeMap("map", [22, 8], 2);
let locMarker = null;
const gibsActive = {};

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
const notablePoints = [];                         // significant events for the marker layer
for (const k of Object.keys(KIND_META)) { heatByKind[k] = []; markersByKind[k] = L.layerGroup(); }

let heatOn = true, markersOn = false;
const heatLayer = L.heatLayer([], {
  radius: 22, blur: 16, maxZoom: 6, minOpacity: 0.22,
  gradient: { 0.2: "#2f7d8c", 0.4: "#35b39c", 0.6: "#d9a13b", 0.8: "#e0703a", 1.0: "#e05252" },
}).addTo(map);

function rebuildHeat() {
  const pts = [];
  for (const k of active) pts.push(...heatByKind[k]);
  heatLayer.setLatLngs(pts);
  if (heatOn && !map.hasLayer(heatLayer)) heatLayer.addTo(map);
}

function syncMarkers() {
  for (const k of Object.keys(KIND_META)) {
    const shouldShow = markersOn && active.has(k);
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
      notablePoints.push({ ...p, kind });
      const color = KIND_META[kind].color;
      L.circleMarker([p.lat, p.lon], { radius: 5, color, weight: 1.4, fillColor: color, fillOpacity: 0.5 })
        .bindPopup(`<strong>${KIND_META[kind].label.replace(/s$/, "")}</strong>`)
        .addTo(markersByKind[kind]);
    }
  });
  rebuildHeat();
  buildControls(data.counts || {});
})();

// side feeds come from the lighter overview endpoint
(async () => {
  const o = await TS.fetchJSON("/api/live/overview");
  if (!o.error) fillFeeds(o);
})();

// ---------- filter chips, heatmap + markers toggles ----------
function buildControls(counts) {
  const box = document.getElementById("type-filters");
  box.innerHTML = "";
  Object.keys(KIND_META).forEach((k) => {
    if (!counts[k]) return;
    const chip = document.createElement("button");
    chip.className = "chip on";
    chip.innerHTML = `<span class="dot" style="color:${KIND_META[k].color}"></span>${KIND_META[k].label} <span class="k" style="opacity:.6">${counts[k]}</span>`;
    chip.addEventListener("click", () => {
      const on = chip.classList.toggle("on");
      chip.classList.toggle("off", !on);
      if (on) active.add(k); else active.delete(k);
      rebuildHeat(); syncMarkers();
    });
    box.appendChild(chip);
  });

  const heat = document.createElement("button");
  heat.className = "chip toggle on";
  heat.innerHTML = `<svg width="13" height="13" style="vertical-align:-2px"><use href="#i-layers"/></svg> Heatmap`;
  heat.addEventListener("click", () => {
    heatOn = heat.classList.toggle("on");
    if (heatOn) heatLayer.addTo(map); else map.removeLayer(heatLayer);
  });
  box.appendChild(heat);

  const mk = document.createElement("button");
  mk.className = "chip toggle off";
  mk.innerHTML = `<svg width="13" height="13" style="vertical-align:-2px"><use href="#i-pin"/></svg> Markers`;
  mk.addEventListener("click", () => {
    markersOn = mk.classList.toggle("on");
    mk.classList.toggle("off", !markersOn);
    syncMarkers();
  });
  box.appendChild(mk);

  buildLegend(counts);
}

function buildLegend(counts) {
  const box = document.getElementById("map-legend");
  const items = Object.keys(KIND_META).filter(k => counts[k])
    .map(k => `<span class="li"><span class="sw" style="background:${KIND_META[k].color}"></span>${KIND_META[k].label}</span>`);
  items.unshift(`<span class="li"><span class="sw" style="background:linear-gradient(90deg,#35b39c,#d9a13b,#e05252)"></span>Hazard intensity</span>`);
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
      title: window.TS_MODULES[slug].title, boost: r.cascades && r.cascades.score_after > r.cascades.score_before }))
    .sort((a, b) => b.score - a.score);

  matrixBody.innerHTML = "";
  rows.forEach(({ slug, score, level, title, boost }) => {
    const row = document.createElement("div");
    row.className = "matrix-row";
    row.innerHTML = `
      <svg><use href="#i-${slug}"/></svg>
      <a href="/module/${slug}?lat=${loc.lat}&lon=${loc.lon}&name=${encodeURIComponent(name)}">${title}${boost ? ` <span class="tag" title="raised by cascade coupling">coupled</span>` : ""}</a>
      <div class="meter"><i style="width: 0%; background: ${TS.riskColor(level)}"></i></div>
      <span class="score" style="color: ${TS.riskColor(level)}">${score.toFixed(0)}</span>`;
    matrixBody.appendChild(row);
    requestAnimationFrame(() => row.querySelector("i").style.width = `${Math.max(score, 2)}%`);
  });
  const failed = Object.entries(data.results).filter(([, r]) => r.error);
  if (failed.length) {
    const note = document.createElement("p");
    note.className = "muted small mt-2";
    note.textContent = `${failed.length} module(s) had no data for this location: ${failed.map(([s]) => s).join(", ")}.`;
    matrixBody.appendChild(note);
  }

  const activeEdges = (data.edges || []).filter(e => e.active && e.boost > 0);
  if (activeEdges.length) {
    document.getElementById("cascade-card").hidden = false;
    document.getElementById("cascade-body").innerHTML = activeEdges.map(e => `
      <div class="feed-item"><span class="tag">${e.source} to ${e.target}</span>
        <span class="t small">${e.mechanism}</span><span class="m risk-high">+${e.boost}</span></div>`).join("");
  }
}

TS.searchBox(document.getElementById("loc-search"), assess);

const saved = TS.initialLocation();
if (saved) {
  document.getElementById("loc-search").value = saved.name || "";
  assess(saved);
}
