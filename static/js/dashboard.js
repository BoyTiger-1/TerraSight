// command center: global map with live event overlays, GIBS science layers,
// and the run-everything risk matrix for a searched location

const map = TS.makeMap("map");
let eventLayer = L.layerGroup().addTo(map);
let locMarker = null;
const gibsActive = {};

// GIBS layer toggle buttons on the map
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

// ---------- live feeds ----------
const CAT_ICON = { wildfires: "wildfire", severeStorms: "cyclone", volcanoes: "volcano",
  floods: "flood", seaLakeIce: "avalanche", earthquakes: "earthquake" };

(async () => {
  const data = await TS.fetchJSON("/api/live/overview");
  if (data.error) return;

  const evBox = document.getElementById("feed-events");
  evBox.innerHTML = "";
  (data.eonet_events || []).slice(0, 9).forEach((e) => {
    const row = document.createElement("div");
    row.className = "feed-item";
    row.innerHTML = `<span class="tag">${(e.category || "event").replace(/([A-Z])/g, " $1")}</span>
      <span class="t">${e.title}</span>
      <span class="m">${e.date ? e.date.slice(5, 10) : ""}</span>`;
    evBox.appendChild(row);
    if (e.lat != null) TS.dot(e, CAT_ICON[e.category] ? undefined : "event")
      .addTo(eventLayer).bindPopup(`<strong>${e.title}</strong><br>${e.category || ""}`);
  });
  if (!evBox.children.length) evBox.innerHTML = `<p class="muted small">No open events reported.</p>`;

  const stBox = document.getElementById("feed-storms");
  stBox.innerHTML = "";
  (data.active_storms || []).forEach((s) => {
    const row = document.createElement("div");
    row.className = "feed-item";
    row.innerHTML = `<span class="tag">${s.classification || "TC"}</span>
      <span class="t">${s.name}</span><span class="m">${s.intensity_kt || "?"} kt</span>`;
    stBox.appendChild(row);
    if (s.lat != null) TS.dot({ lat: s.lat, lon: s.lon, label: `${s.name}, ${s.intensity_kt} kt`, kind: "storm" }).addTo(eventLayer);
  });
  if (!stBox.children.length) stBox.innerHTML = `<p class="muted small">No active tropical cyclones in the Atlantic or East Pacific basins.</p>`;

  const qBox = document.getElementById("feed-quakes");
  qBox.innerHTML = "";
  (data.significant_quakes || []).slice(0, 9).forEach((q) => {
    const row = document.createElement("div");
    row.className = "feed-item";
    row.innerHTML = `<span class="tag risk-${q.magnitude >= 6.5 ? "extreme" : q.magnitude >= 5.5 ? "high" : "moderate"}">M${q.magnitude?.toFixed(1)}</span>
      <span class="t">${(q.place || "").slice(0, 44)}</span>`;
    qBox.appendChild(row);
    if (q.lat != null) TS.dot({ lat: q.lat, lon: q.lon, label: `M${q.magnitude} ${q.place}`, kind: "quake" }).addTo(eventLayer);
  });
})();

// ---------- location assessment ----------
const matrixBody = document.getElementById("matrix-body");
const matrixLoc = document.getElementById("matrix-loc");

async function assess(loc) {
  TS.saveLocation(loc);
  matrixLoc.textContent = `· ${loc.name}`;
  matrixBody.innerHTML = `<div class="spinner-line">Running 15 modules against live NASA, NOAA, USGS, and Open-Meteo feeds. First run on a new location takes about a minute...</div>`;
  document.getElementById("cascade-card").hidden = true;

  map.flyTo([loc.lat, loc.lon], 8, { duration: 1.4 });
  if (locMarker) map.removeLayer(locMarker);
  locMarker = L.circleMarker([loc.lat, loc.lon], { radius: 9, color: TS.theme.accent, weight: 2, fillOpacity: 0.25 })
    .addTo(map).bindPopup(`<strong>${loc.name}</strong>`);

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
      <a href="/module/${slug}">${title}${boost ? ` <span class="tag" title="raised by cascade coupling">coupled</span>` : ""}</a>
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

  // active cascades
  const active = (data.edges || []).filter(e => e.active && e.boost > 0);
  if (active.length) {
    const card = document.getElementById("cascade-card");
    card.hidden = false;
    document.getElementById("cascade-body").innerHTML = active.map(e => `
      <div class="feed-item">
        <span class="tag">${e.source} to ${e.target}</span>
        <span class="t small">${e.mechanism}</span>
        <span class="m risk-high">+${e.boost}</span>
      </div>`).join("");
  }
}

TS.searchBox(document.getElementById("loc-search"), assess);

// restore the last session location if there is one
const saved = TS.initialLocation();
if (saved) {
  document.getElementById("loc-search").value = saved.name || "";
  assess(saved);
}
