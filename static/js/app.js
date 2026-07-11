// shared frontend runtime: nav behavior, scroll reveals, the location search
// component, chart theming, the risk gauge, and the leaflet map factory.
// every page script builds on the TS namespace defined here.

const TS = window.TS = {};

// pull the design tokens out of CSS so charts and maps always match the theme
const css = getComputedStyle(document.documentElement);
TS.theme = {
  ink: css.getPropertyValue("--ink").trim(),
  ink2: css.getPropertyValue("--ink-2").trim(),
  ink3: css.getPropertyValue("--ink-3").trim(),
  border: css.getPropertyValue("--border").trim(),
  accent: css.getPropertyValue("--accent").trim(),
  surface: css.getPropertyValue("--surface").trim(),
  risk: {
    Low: css.getPropertyValue("--risk-low").trim(),
    Moderate: css.getPropertyValue("--risk-moderate").trim(),
    High: css.getPropertyValue("--risk-high").trim(),
    Extreme: css.getPropertyValue("--risk-extreme").trim(),
  },
  charts: [
    css.getPropertyValue("--chart-1").trim(),
    css.getPropertyValue("--chart-2").trim(),
    css.getPropertyValue("--chart-3").trim(),
    css.getPropertyValue("--chart-4").trim(),
  ],
};

// ---------- nav ----------
const nav = document.getElementById("nav");
addEventListener("scroll", () => nav.classList.toggle("scrolled", scrollY > 12), { passive: true });
nav.classList.toggle("scrolled", scrollY > 12);
const burger = document.getElementById("nav-burger");
if (burger) burger.addEventListener("click", () =>
  document.getElementById("nav-links").classList.toggle("open"));

// ---------- reveal on scroll ----------
const io = new IntersectionObserver((entries) => {
  for (const e of entries) if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); }
}, { threshold: 0.12 });
document.querySelectorAll(".reveal").forEach(el => io.observe(el));

// ---------- helpers ----------
TS.fetchJSON = async (url, opts) => {
  const r = await fetch(url, opts);
  const data = await r.json().catch(() => ({ error: "Bad response from server" }));
  if (!r.ok && !data.error) data.error = `Request failed (${r.status})`;
  return data;
};

TS.fmt = (n, digits = 0) => n == null ? "?" : Number(n).toLocaleString("en-US", { maximumFractionDigits: digits });

TS.riskColor = (level) => TS.theme.risk[level] || TS.theme.ink3;

// count numbers up fast when they scroll into view
TS.countUp = (el, target, suffix = "", dur = 900) => {
  const start = performance.now();
  const from = 0;
  const step = (t) => {
    const p = Math.min((t - start) / dur, 1);
    const eased = 1 - Math.pow(1 - p, 3);
    el.textContent = Math.round(from + (target - from) * eased).toLocaleString("en-US") + suffix;
    if (p < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
};

// ---------- location search (used on every tool page) ----------
// builds an accessible autocomplete against /api/geocode. onPick gets
// {name, lat, lon, admin1, country}
TS.searchBox = (input, onPick) => {
  const wrap = input.closest(".searchbox");
  let box = null, items = [], hl = -1, timer = null;

  const close = () => { if (box) box.remove(); box = null; items = []; hl = -1; };

  const render = (results) => {
    close();
    box = document.createElement("div");
    box.className = "results";
    if (!results.length) {
      box.innerHTML = `<div class="empty">No places found. Try a city name like "Denver" or "Tokyo".</div>`;
    }
    results.forEach((r) => {
      const b = document.createElement("button");
      b.type = "button";
      b.innerHTML = `<span>${r.name}${r.admin1 ? ", " + r.admin1 : ""}</span>
        <span class="meta">${r.country || ""} ${r.population ? "pop " + TS.fmt(r.population) : ""}</span>`;
      b.addEventListener("click", () => { close(); input.value = `${r.name}${r.admin1 ? ", " + r.admin1 : ""}`; onPick(r); });
      box.appendChild(b);
      items.push(b);
    });
    wrap.appendChild(box);
  };

  input.addEventListener("input", () => {
    clearTimeout(timer);
    const q = input.value.trim();
    if (q.length < 2) { close(); return; }
    timer = setTimeout(async () => {
      const results = await TS.fetchJSON(`/api/geocode?q=${encodeURIComponent(q)}`);
      if (Array.isArray(results)) render(results);
    }, 220);
  });

  input.addEventListener("keydown", (e) => {
    if (!box) return;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      hl = e.key === "ArrowDown" ? Math.min(hl + 1, items.length - 1) : Math.max(hl - 1, 0);
      items.forEach((b, i) => b.classList.toggle("hl", i === hl));
    } else if (e.key === "Enter" && hl >= 0) {
      e.preventDefault(); items[hl].click();
    } else if (e.key === "Escape") close();
  });

  document.addEventListener("click", (e) => { if (!wrap.contains(e.target)) close(); });
};

// remember the last place the user looked at, across pages
TS.saveLocation = (loc) => sessionStorage.setItem("ts-loc", JSON.stringify(loc));
TS.loadLocation = () => {
  try { return JSON.parse(sessionStorage.getItem("ts-loc")); } catch { return null; }
};

// ?lat=..&lon=..&name=.. in the URL beats the session, so links are shareable
TS.initialLocation = () => {
  const qs = new URLSearchParams(location.search);
  if (qs.get("lat") && qs.get("lon")) {
    return { lat: parseFloat(qs.get("lat")), lon: parseFloat(qs.get("lon")),
             name: qs.get("name") || `${qs.get("lat")}, ${qs.get("lon")}` };
  }
  return TS.loadLocation();
};

// ---------- chart.js theming ----------
if (window.Chart) {
  Chart.defaults.color = TS.theme.ink3;
  Chart.defaults.borderColor = "rgba(35, 46, 65, 0.55)";
  Chart.defaults.font.family = "'JetBrains Mono', monospace";
  Chart.defaults.font.size = 10.5;
  Chart.defaults.plugins.legend.labels.boxWidth = 9;
  Chart.defaults.plugins.legend.labels.boxHeight = 9;
  Chart.defaults.plugins.legend.labels.usePointStyle = true;
  Chart.defaults.plugins.tooltip.backgroundColor = "#171f2d";
  Chart.defaults.plugins.tooltip.borderColor = TS.theme.border;
  Chart.defaults.plugins.tooltip.borderWidth = 1;
  Chart.defaults.plugins.tooltip.titleColor = TS.theme.ink;
  Chart.defaults.plugins.tooltip.bodyColor = TS.theme.ink2;
  Chart.defaults.plugins.tooltip.padding = 10;
  Chart.defaults.animation.duration = 600;
}

// standard timeline chart: thin 2px lines, no point clutter, crosshair tooltip
TS.lineChart = (canvas, labels, series, opts = {}) => {
  const short = labels.map(l => typeof l === "string" ? l.replace("T", " ").slice(5, opts.hourly ? 16 : 10) : l);
  return new Chart(canvas, {
    type: opts.type || "line",
    data: {
      labels: short,
      datasets: series.map((s, i) => ({
        label: s.name + (s.unit ? ` (${s.unit})` : ""),
        data: s.data,
        borderColor: TS.theme.charts[i % 4],
        backgroundColor: opts.type === "bar" ? TS.theme.charts[i % 4] : "transparent",
        borderWidth: 2,
        pointRadius: 0,
        pointHoverRadius: 4,
        pointHoverBackgroundColor: TS.theme.charts[i % 4],
        tension: 0.35,
        borderDash: s.name.toLowerCase().includes("baseline") || s.name.toLowerCase().includes("threshold") ? [5, 5] : [],
        borderRadius: 4,
        spanGaps: true,
      })),
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      scales: {
        x: { grid: { display: false }, ticks: { maxTicksLimit: 8, maxRotation: 0 } },
        y: { grid: { color: "rgba(35,46,65,0.4)" }, border: { display: false }, ticks: { maxTicksLimit: 6 } },
      },
      plugins: { legend: { display: series.length > 1, position: "top", align: "end" } },
      ...opts.chart,
    },
  });
};

// ---------- risk gauge ----------
TS.gauge = (el, score, level) => {
  const r = 88, c = 2 * Math.PI * r;
  const color = TS.riskColor(level);
  el.innerHTML = `
    <svg width="210" height="210" viewBox="0 0 210 210">
      <circle class="track" cx="105" cy="105" r="${r}" fill="none" stroke-width="13"/>
      <circle class="fill" cx="105" cy="105" r="${r}" fill="none" stroke="${color}" stroke-width="13"
        stroke-dasharray="${c}" stroke-dashoffset="${c}"/>
    </svg>
    <div class="gauge-num">
      <span class="v num" style="color:${color}">0</span>
      <span class="of">RISK / 100</span>
    </div>`;
  requestAnimationFrame(() => {
    el.querySelector(".fill").style.strokeDashoffset = c * (1 - score / 100);
    TS.countUp(el.querySelector(".v"), Math.round(score));
  });
};

// ---------- maps ----------
// dark carto base + NASA GIBS science overlays. GIBS is keyless and global.
TS.gibsDate = () => {
  const d = new Date(Date.now() - 86400e3 * 2); // 2 days back, always published
  return d.toISOString().slice(0, 10);
};

TS.GIBS_LAYERS = {
  satellite: { name: "Satellite (MODIS)", id: "MODIS_Terra_CorrectedReflectance_TrueColor", fmt: "jpg", max: 9 },
  thermal: { name: "Fire detections", id: "MODIS_Terra_Thermal_Anomalies_All", fmt: "png", max: 9 },
  snow: { name: "Snow cover", id: "MODIS_Terra_NDSI_Snow_Cover", fmt: "png", max: 8 },
  temp: { name: "Surface temp", id: "MODIS_Terra_Land_Surface_Temp_Day", fmt: "png", max: 7 },
  aerosol: { name: "Aerosol depth", id: "MODIS_Combined_Value_Added_AOD", fmt: "png", max: 6 },
  precip: { name: "Precipitation", id: "IMERG_Precipitation_Rate", fmt: "png", max: 6 },
  ndvi: { name: "Vegetation (NDVI)", id: "MODIS_Terra_NDVI_8Day", fmt: "png", max: 9 },
  soil: { name: "Soil moisture", id: "SMAP_L4_Analyzed_Surface_Soil_Moisture", fmt: "png", max: 6 },
  clouds: { name: "Satellite (MODIS)", id: "MODIS_Terra_CorrectedReflectance_TrueColor", fmt: "jpg", max: 9 },
};

TS.gibsLayer = (key) => {
  const l = TS.GIBS_LAYERS[key];
  if (!l) return null;
  return L.tileLayer(
    `https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/${l.id}/default/${TS.gibsDate()}/GoogleMapsCompatible_Level${l.max}/{z}/{y}/{x}.${l.fmt}`,
    { maxNativeZoom: l.max, maxZoom: 12, opacity: key === "satellite" || key === "clouds" ? 0.85 : 0.62,
      attribution: "NASA GIBS" });
};

TS.makeMap = (id, center = [39, -98], zoom = 4) => {
  const map = L.map(id, { zoomControl: true, attributionControl: true, worldCopyJump: true })
    .setView(center, zoom);
  L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
    maxZoom: 19, attribution: "&copy; OpenStreetMap &copy; CARTO",
  }).addTo(map);
  return map;
};

// small colored dot markers per hazard kind
TS.KIND_COLORS = { fire: "#e0703a", quake: "#b06fb0", gauge: "#5b93d9", storm: "#d9a13b",
  volcano: "#e05252", city: "#9aa8bb", event: "#35b39c" };

TS.dot = (p, kindOverride) => {
  const kind = kindOverride || p.kind || "event";
  const color = TS.KIND_COLORS[kind] || TS.theme.accent;
  return L.circleMarker([p.lat, p.lon], {
    radius: kind === "city" ? 4 : 6, color, weight: 1.5,
    fillColor: color, fillOpacity: 0.45,
  }).bindPopup(`<strong>${p.label || kind}</strong>`);
};
