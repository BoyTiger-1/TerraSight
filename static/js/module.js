// generic module page renderer. every module returns the same schema, so one
// script draws the gauge, factor bars, timeline, map, recs, and methodology.

const slug = window.TS_SLUG;
let chart = null, modMap = null, markerLayer = null;
let fullTimeline = null;

const $ = (id) => document.getElementById(id);

async function run(loc) {
  TS.saveLocation(loc);
  $("empty-state").hidden = true;
  $("results").hidden = true;
  $("error").hidden = true;
  $("loading").hidden = false;

  const name = `${loc.name}${loc.admin1 ? ", " + loc.admin1 : ""}`;
  const r = await TS.fetchJSON(`/api/assess/${slug}?lat=${loc.lat}&lon=${loc.lon}&name=${encodeURIComponent(name)}`);
  $("loading").hidden = true;

  if (r.error) {
    $("error").hidden = false;
    $("error").innerHTML = `<div class="error-note">${r.error}</div>`;
    return;
  }
  render(r, loc);
}

// where the numbers actually came from. some feeds have no reading at every
// point on earth (river gauges need a river, sea surface temperature needs
// ocean), so the snapshot walks outward until it finds one. that substitution
// is a real caveat on the result and belongs on the page, not buried in JSON.
function renderProvenance(prov, loc) {
  const box = $("provenance");
  if (!box) return;
  const sub = prov.substituted_location;
  const feeds = prov.substituted || [];
  if (!sub && !feeds.length) { box.hidden = true; return; }

  box.hidden = false;
  const parts = [];
  if (sub) {
    parts.push(`<p class="muted small"><strong>Modelled from a nearby location.</strong>
      No feed could be scored at ${loc.name} itself, so this assessment uses
      ${sub.name || "the nearest point with coverage"}, about ${Math.round(sub.distance_km)} km away.
      Confidence has been reduced accordingly.</p>`);
  }
  if (feeds.length) {
    parts.push(`<p class="muted small">${feeds.length} of ${prov.feeds_total} data feeds had no
      reading at this exact point and used the nearest available one:</p>`
      + feeds.slice(0, 6).map(f => `<div class="feed-item">
          <span class="tag">${f.feed}</span>
          <span class="t small">${f.status === "unavailable" ? "no coverage found"
            : f.status === "throttled" ? "archive busy, refreshes on the next load"
            : `nearest reading, ${Math.round(f.distance_km)} km away`}</span></div>`).join(""));
  }
  box.innerHTML = `<div class="panel-title"><svg width="15" height="15"><use href="#i-layers"/></svg>
    Data coverage at this point</div>${parts.join("")}`;
}

// the CMIP6 projection the climate module computes: mean summer-season tmax per
// five-year bucket out to 2050, averaged across three downscaled models. it is
// the one chart on the site that looks forward decades rather than days.
let projChart = null;
function renderProjection(proj) {
  const card = $("projection-card");
  if (!card) return;
  const years = Object.keys(proj || {}).sort();
  if (years.length < 3) { card.hidden = true; return; }

  card.hidden = false;
  const vals = years.map(y => proj[y]);
  const shift = vals[vals.length - 1] - vals[0];
  $("projection-note").textContent =
    `CMIP6 ensemble mean of three downscaled models. Between the ${years[0]}s and the `
    + `${years[years.length - 1]}s the daily maximum shifts by ${shift >= 0 ? "+" : ""}`
    + `${shift.toFixed(1)}°C at this location.`;
  if (projChart) projChart.destroy();
  projChart = TS.lineChart($("projection-chart"), years.map(y => `${y}s`),
    [{ name: "Projected daily max", data: vals, unit: "°C" }]);
}

function render(r, loc) {
  $("results").hidden = false;
  const a = r.assessment;

  TS.gauge($("gauge"), a.score, a.level);
  const lvl = $("level-badge");
  lvl.className = `badge ${a.level.toLowerCase()}`;
  lvl.innerHTML = `<span class="dot"></span> ${a.level} risk`;
  $("conf-badge").textContent = `confidence: ${a.confidence_label} (${Math.round(a.confidence * 100)}%)`;
  $("kind-badge").textContent = a.kind === "impact" ? "impact model" : a.kind;
  $("headline").textContent = a.headline;
  renderProvenance(r.data_provenance || {}, loc);
  renderProjection(r.projection);

  // factor contribution bars, positive pushes risk, negative restrains it
  const fb = $("factors-body");
  fb.innerHTML = "";
  const maxC = Math.max(...r.factors.map(f => Math.abs(f.contribution)), 0.001);
  r.factors.forEach((f) => {
    const row = document.createElement("div");
    row.className = "factor";
    const pct = Math.abs(f.contribution) / maxC * 50;
    const pos = f.contribution >= 0;
    row.innerHTML = `
      <span class="f-name">${f.name}</span>
      <div class="contrib">
        <div class="bar-wrap">
          <span class="bar ${pos ? "pos" : "neg"}" style="width: 0%; ${pos ? "" : "left: auto; right: 50%"}"></span>
        </div>
        <span class="f-val">${f.value ?? "?"}${f.unit ? " " + f.unit : ""}</span>
      </div>
      ${f.detail ? `<span class="f-detail">${f.detail}</span>` : ""}`;
    fb.appendChild(row);
    requestAnimationFrame(() => row.querySelector(".bar").style.width = `${pct}%`);
  });

  // timeline chart with optional zoom window for long series
  if (r.timeline && r.timeline.labels && r.timeline.labels.length > 1) {
    $("timeline-card").hidden = false;
    fullTimeline = r.timeline;
    const isBar = (r.timeline.series[0].kind === "bar") ||
      slug === "earthquake" || slug === "tsunami" || slug === "volcano" || slug === "infrastructure";
    const hourly = String(r.timeline.labels[0] || "").includes("T");
    drawTimeline(1.0, isBar, hourly);
    const win = $("timeline-window");
    if (r.timeline.labels.length > 40) {
      win.hidden = false;
      win.oninput = () => drawTimeline(win.value / 100, isBar, hourly);
    } else { win.hidden = true; }
  } else {
    $("timeline-card").hidden = true;
  }

  // recommendations grouped by priority
  const order = { immediate: 0, high: 1, advisory: 2 };
  const recs = [...r.recommendations].sort((x, y) => order[x.priority] - order[y.priority]);
  $("recs-body").innerHTML = recs.map(rec => `
    <div class="rec ${rec.priority}">
      <div class="rec-head">
        <span class="badge ${rec.priority === "immediate" ? "extreme" : rec.priority === "high" ? "high" : "kind"}">${rec.priority}</span>
        <span class="aud">for ${rec.audience}</span>
      </div>
      <div class="act">${rec.action}</div>
      <div class="why">${rec.reason}</div>
    </div>`).join("");

  // impact block
  if (r.impact) {
    $("impact-body").innerHTML = `
      <div class="split"><span class="muted small">Population within ${r.impact.radius_km} km</span>
        <span class="num">${TS.fmt(r.impact.population_exposed)}</span></div>
      <div class="split mt-1"><span class="muted small">Expected loss at this risk level</span>
        <span class="num" style="color: ${TS.riskColor(a.level)}">${r.impact.expected_loss_label}</span></div>
      <div class="split mt-1"><span class="muted small">Damage ratio</span>
        <span class="num">${r.impact.damage_ratio_pct}%</span></div>
      ${(r.impact.nearby_cities || []).slice(0, 3).map(c =>
        `<div class="split mt-1"><span class="muted small">${c.name}, ${c.state} (${c.distance_km} km)</span>
         <span class="num">${TS.fmt(c.population)}</span></div>`).join("")}
      <p class="muted small mt-2">${r.impact.note}</p>`;
  } else {
    $("impact-body").innerHTML = `<p class="muted small">No impact estimate applies to this assessment.</p>`;
  }

  $("sources-body").innerHTML = r.data_sources.map(s => `<div class="feed-item"><span class="t small">${s}</span></div>`).join("");

  // cascades in and out of this module
  const c = r.cascades;
  if (c && (c.incoming.length || c.outgoing.length)) {
    $("cascade-card").hidden = false;
    $("cascade-body").innerHTML =
      c.incoming.map(e => `<div class="feed-item"><span class="tag">from ${e.source}</span>
        <span class="t small">${e.mechanism}</span><span class="m risk-high">+${e.boost}</span></div>`).join("") +
      c.outgoing.map(e => `<div class="feed-item"><span class="tag">to ${e.target}</span>
        <span class="t small">${e.mechanism}</span><span class="m muted">feeds</span></div>`).join("");
  } else { $("cascade-card").hidden = true; }

  // methodology + model card
  let method = `<p>${r.methodology}</p>`;
  if (r.model_card && r.model_card.cv_roc_auc_mean) {
    const mc = r.model_card;
    method += `<p class="mt-2"><strong>Model card.</strong> ${mc.algorithm}, trained ${mc.trained_at}
      on ${mc.n_samples} samples (${mc.n_positives} real events). Cross-validated ROC AUC
      ${mc.cv_roc_auc_mean} &plusmn; ${mc.cv_roc_auc_std}, balanced accuracy ${mc.cv_balanced_accuracy}.
      Top features by permutation importance: ${Object.keys(mc.feature_importances).slice(0, 4).join(", ")}.</p>`;
  }
  $("method-body").innerHTML = method;

  // map: center on location, add module layers + GIBS overlays
  if (!modMap) {
    modMap = TS.makeMap("mod-map", [loc.lat, loc.lon], 7);
    markerLayer = L.layerGroup().addTo(modMap);
    // click the map to re-run this module at the clicked point
    TS.enableMapPick(modMap, (picked) => {
      $("loc-search").value = picked.name || "";
      run(picked);
    });
  } else {
    modMap.setView([loc.lat, loc.lon], 7);
    markerLayer.clearLayers();
  }
  L.circleMarker([loc.lat, loc.lon], { radius: 9, color: TS.theme.accent, weight: 2, fillOpacity: 0.3 })
    .addTo(markerLayer).bindPopup(`<strong>${r.location.name}</strong>`);
  (r.map_layers.points || []).forEach(p => TS.dot(p).addTo(markerLayer));
  (r.map_layers.gibs || []).slice(0, 1).forEach(key => {
    const layer = TS.gibsLayer(key);
    if (layer) layer.addTo(modMap);
  });
  setTimeout(() => modMap.invalidateSize(), 60);
}

function drawTimeline(fraction, isBar, hourly) {
  const n = fullTimeline.labels.length;
  const keep = Math.max(Math.round(n * fraction), 10);
  const labels = fullTimeline.labels.slice(n - keep);
  const series = fullTimeline.series.map(s => ({ ...s, data: s.data.slice(n - keep) }));
  if (chart) chart.destroy();
  chart = TS.lineChart($("timeline-chart"), labels, series, { type: isBar ? "bar" : "line", hourly });
  $("timeline-title").textContent = series.map(s => s.name).join(" + ");
}

TS.searchBox($("loc-search"), run);

const saved = TS.initialLocation();
if (saved) {
  $("loc-search").value = saved.name || "";
  run(saved);
}
