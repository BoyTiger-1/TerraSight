// executive report page: fetch the structured report and lay it out like a
// document an agency would actually circulate. print styles handle PDF export.

const $ = (id) => document.getElementById(id);

async function generate(loc) {
  TS.saveLocation(loc);
  $("empty-state").hidden = true;
  $("paper").hidden = true;
  $("loading").hidden = false;
  $("print-btn").hidden = true;

  const name = `${loc.name}${loc.admin1 ? ", " + loc.admin1 : ""}`;
  const r = await TS.fetchJSON(`/api/report?lat=${loc.lat}&lon=${loc.lon}&name=${encodeURIComponent(name)}`);
  $("loading").hidden = true;
  if (r.error) {
    $("empty-state").hidden = false;
    $("empty-state").innerHTML = `<div class="error-note">${r.error}</div>`;
    return;
  }
  renderReport(r);
}

const levelColor = (level) => TS.riskColor(level);

function renderReport(r) {
  const paper = $("paper");
  paper.hidden = false;
  $("print-btn").hidden = false;

  const riskRows = r.risk_table.map(row => `
    <tr>
      <td>${row.title}</td>
      <td><span class="badge ${row.level.toLowerCase()}"><span class="dot"></span>${row.level}</span></td>
      <td class="num" style="color: ${levelColor(row.level)}">${row.score.toFixed(0)}</td>
      <td class="num">${row.confidence}</td>
      <td class="muted small">${row.kind}</td>
    </tr>`).join("");

  const sections = r.hazard_sections.map(s => `
    <h2>${s.title} <span class="num" style="color: ${levelColor(s.level)}; font-size: 15px"> ${s.score.toFixed(0)}/100 ${s.level}</span></h2>
    <p>${s.narrative}</p>
    ${s.drivers ? `<p><strong>Primary drivers.</strong> ${s.drivers}.</p>` : ""}
    ${s.cascade_note ? `<p><strong>Cascade effects.</strong> ${s.cascade_note}.</p>` : ""}
    <p class="small muted">${s.methodology}</p>`).join("");

  const actionBlock = (list, label) => list.length ? `
    <h3 style="font-size: 15px; margin: 18px 0 8px; text-transform: uppercase; letter-spacing: 0.08em; font-family: var(--font-mono)">${label}</h3>
    ${list.map(a => `<p style="margin-bottom: 8px"><strong>${a.action}.</strong>
      <span class="muted">(${a.audience}, ${a.hazard.toLowerCase().replace(" intelligence", "")})</span><br>
      <span class="small muted">${a.reason}.</span></p>`).join("")}` : "";

  const cascades = r.cascades.length ? `
    <h2>Cascading risk interactions</h2>
    ${r.cascades.map(c => `<p><strong>${c.source} &rarr; ${c.target} (+${c.boost} points).</strong> ${c.mechanism}.</p>`).join("")}`
    : "";

  const exposures = r.exposures.length ? `
    <h2>Economic exposure summary</h2>
    <div class="table-scroll"><table class="data">
      <thead><tr><th>Hazard</th><th>Radius</th><th class="num">Population exposed</th><th class="num">Expected loss</th></tr></thead>
      <tbody>${r.exposures.map(e => `<tr><td>${e.hazard}</td><td>${e.radius_km} km</td>
        <td class="num">${TS.fmt(e.population)}</td><td class="num">${e.loss}</td></tr>`).join("")}</tbody>
    </table></div>
    <p class="small muted" style="margin-top: 8px">Planning estimates accurate to order of magnitude. Not underwriting values.</p>` : "";

  paper.innerHTML = `
    <div class="rp-head">
      <span class="eyebrow">TerraSight Multi-Hazard Assessment</span>
      <h1 style="margin-top: 10px">${r.location.name}</h1>
      <div class="rp-meta">
        <span>${r.generated_date}</span>
        <span>lat ${r.location.lat.toFixed(3)}, lon ${r.location.lon.toFixed(3)}</span>
        <span>${r.risk_table.length} modules evaluated</span>
      </div>
    </div>

    <h2 style="border-top: none; padding-top: 0">Executive summary</h2>
    <p class="rp-summary">${r.summary}</p>

    <h2>Current conditions</h2>
    <p>${r.conditions}</p>

    <h2>Risk register</h2>
    <div class="table-scroll"><table class="data">
      <thead><tr><th>Hazard</th><th>Level</th><th class="num">Score</th><th class="num">Confidence</th><th>Type</th></tr></thead>
      <tbody>${riskRows}</tbody>
    </table></div>

    ${sections}
    ${cascades}

    <h2>Recommended actions</h2>
    ${actionBlock(r.actions.immediate, "Immediate")}
    ${actionBlock(r.actions.high, "High priority")}
    ${actionBlock(r.actions.advisory, "Advisory")}

    ${exposures}

    <h2>Data provenance and confidence</h2>
    <p>${r.confidence_note}</p>
    <p class="small muted">Sources used in this assessment: ${r.data_sources.join("; ")}.</p>
  `;
  paper.scrollIntoView({ behavior: "smooth", block: "start" });
}

TS.searchBox($("loc-search"), generate);
const saved = TS.initialLocation();
if (saved) { $("loc-search").value = saved.name || ""; generate(saved); }
