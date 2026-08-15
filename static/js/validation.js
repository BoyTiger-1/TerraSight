// the model validation page. everything is drawn from one precomputed report so
// the page is static and fast; nothing here refits anything in the browser.

const charts = [];

function metric(label, value, note, tone) {
  return `
    <div class="vm">
      <span class="vm-label">${label}</span>
      <span class="vm-value ${tone || ""}">${value}</span>
      <span class="vm-note">${note}</span>
    </div>`;
}

// AUC, ECE and the like have conventional reading points. colouring them keeps
// the reader from having to remember which direction is good on each one.
function tone(kind, v) {
  if (v == null) return "";
  const good = { auc: v >= 0.8, ap: v >= 0.75, skill: v >= 0.25, ece: v <= 0.08 }[kind];
  const bad = { auc: v < 0.65, ap: v < 0.5, skill: v <= 0, ece: v > 0.15 }[kind];
  return good ? "risk-low" : bad ? "risk-extreme" : "risk-moderate";
}

function curveChart(canvas, points, opts) {
  return new Chart(canvas, {
    type: "line",
    data: {
      datasets: [
        { label: opts.label, data: points.map(([x, y]) => ({ x, y })),
          borderColor: TS.theme.charts[0], borderWidth: 2, pointRadius: 0,
          fill: true, backgroundColor: "rgba(53, 179, 156, 0.10)", tension: 0 },
        // the reference line: chance for ROC, the base rate for precision-recall
        { label: opts.refLabel, data: opts.ref, borderColor: TS.theme.ink3,
          borderWidth: 1, borderDash: [5, 5], pointRadius: 0, fill: false },
      ],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      scales: {
        x: { type: "linear", min: 0, max: 1, title: { display: true, text: opts.x },
             grid: { color: "rgba(35,46,65,0.4)" }, ticks: { maxTicksLimit: 6 } },
        y: { type: "linear", min: 0, max: 1, title: { display: true, text: opts.y },
             grid: { color: "rgba(35,46,65,0.4)" }, ticks: { maxTicksLimit: 6 } },
      },
      plugins: { legend: { display: true, position: "top", align: "end" },
                 tooltip: { callbacks: { label: (c) =>
                   `${opts.x} ${c.parsed.x.toFixed(2)}, ${opts.y} ${c.parsed.y.toFixed(2)}` } } },
    },
  });
}

function calibrationChart(canvas, bins) {
  const filled = bins.filter(b => b.n > 0);
  return new Chart(canvas, {
    type: "line",
    data: {
      datasets: [
        { label: "Observed frequency",
          data: filled.map(b => ({ x: b.predicted, y: b.observed, n: b.n })),
          borderColor: TS.theme.charts[0], backgroundColor: TS.theme.charts[0],
          borderWidth: 2, pointRadius: (c) => {
            // the dot size is the bin count, so a point sitting far off the
            // diagonal on four rows does not read like a systematic failure
            const n = (c.raw && c.raw.n) || 0;
            return Math.max(3, Math.min(3 + Math.sqrt(n), 11));
          }, tension: 0.2, fill: false },
        { label: "Perfect calibration", data: [{ x: 0, y: 0 }, { x: 1, y: 1 }],
          borderColor: TS.theme.ink3, borderWidth: 1, borderDash: [5, 5],
          pointRadius: 0, fill: false },
      ],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      scales: {
        x: { type: "linear", min: 0, max: 1, title: { display: true, text: "Predicted probability" },
             grid: { color: "rgba(35,46,65,0.4)" }, ticks: { maxTicksLimit: 6 } },
        y: { type: "linear", min: 0, max: 1, title: { display: true, text: "Observed rate" },
             grid: { color: "rgba(35,46,65,0.4)" }, ticks: { maxTicksLimit: 6 } },
      },
      plugins: {
        legend: { display: true, position: "top", align: "end" },
        tooltip: { callbacks: { label: (c) =>
          `predicted ${c.parsed.x.toFixed(2)}, happened ${c.parsed.y.toFixed(2)} (${c.raw.n} rows)` } },
      },
    },
  });
}

function confusionBlock(c, m) {
  const cell = (label, v, note, cls) => `
    <div class="cm-cell ${cls}">
      <span class="cm-n">${v}</span>
      <span class="cm-l">${label}</span>
      <span class="cm-note">${note}</span>
    </div>`;
  return `
    <div class="cm">
      ${cell("True positives", c.true_positive, "events it caught", "good")}
      ${cell("False negatives", c.false_negative, "events it missed", "bad")}
      ${cell("False positives", c.false_positive, "false alarms", "warn")}
      ${cell("True negatives", c.true_negative, "quiet days called quiet", "good")}
    </div>
    <p class="muted small mt-2">At the ${(c.threshold * 100).toFixed(0)} cutoff the platform uses
      for the High band, this model catches <strong>${(c.recall * 100).toFixed(0)}%</strong> of real
      ${m.name} events, and <strong>${(c.precision * 100).toFixed(0)}%</strong> of the days it flags
      turn out to be real. Those two move against each other; the table below is the whole
      tradeoff, not just the point we picked.</p>`;
}

function thresholdTable(rows, chosen) {
  return `
    <div style="overflow-x:auto">
    <table class="data">
      <thead><tr><th>Cutoff</th><th>Recall</th><th>Precision</th><th>Missed</th>
        <th>False alarms</th><th>Balanced acc.</th></tr></thead>
      <tbody>${rows.filter((_, i) => i % 2 === 0).map(r => `
        <tr class="${Math.abs(r.threshold - chosen) < 0.001 ? "row-mark" : ""}">
          <td>${r.threshold.toFixed(2)}</td>
          <td>${(r.recall * 100).toFixed(0)}%</td>
          <td>${(r.precision * 100).toFixed(0)}%</td>
          <td>${r.false_negative}</td>
          <td>${r.false_positive}</td>
          <td>${(r.balanced_accuracy * 100).toFixed(0)}%</td>
        </tr>`).join("")}
      </tbody>
    </table></div>`;
}

function errorTable(errors) {
  if (!errors.length) return `<p class="muted small">Nothing was misclassified at this cutoff.</p>`;
  return `
    <div style="overflow-x:auto">
    <table class="data">
      <thead><tr><th>Date</th><th>Location</th><th>Truth</th><th>Model said</th></tr></thead>
      <tbody>${errors.map(e => `
        <tr>
          <td>${e.date}</td>
          <td>${e.lat.toFixed(2)}, ${e.lon.toFixed(2)}</td>
          <td class="${e.label ? "risk-high" : "muted"}">${e.label ? "event" : "no event"}</td>
          <td>${(e.predicted * 100).toFixed(0)}
            <span class="muted small">${e.kind}</span></td>
        </tr>`).join("")}
      </tbody>
    </table></div>`;
}

function renderModel(m, threshold) {
  if (!m.available) {
    return `
      <div class="card mt-3">
        <div class="panel-title"><svg width="15" height="15"><use href="#i-${m.name}"/></svg>
          ${m.name} model</div>
        <p class="muted small">${m.reason}</p>
      </div>`;
  }
  const c = m.confusion;
  const spread = m.fold_auc && m.fold_auc.length
    ? `folds ${Math.min(...m.fold_auc).toFixed(2)} to ${Math.max(...m.fold_auc).toFixed(2)}`
    : "single fit";
  return `
    <div class="card mt-3">
      <div class="panel-title"><svg width="15" height="15"><use href="#i-${m.name}"/></svg>
        ${m.name.charAt(0).toUpperCase() + m.name.slice(1)} model
        <span class="muted">${m.n_samples} labelled days, ${m.n_positives} events</span></div>
      ${m.caveat ? `<div class="error-note">${m.caveat}</div>` : ""}

      <div class="vmetrics">
        ${metric("ROC AUC", m.roc_auc.toFixed(3), spread, tone("auc", m.roc_auc))}
        ${metric("Avg precision", m.average_precision.toFixed(3),
                 `base rate ${(m.base_rate * 100).toFixed(0)}%`, tone("ap", m.average_precision))}
        ${metric("Brier skill", m.brier_skill != null ? m.brier_skill.toFixed(3) : "?",
                 `${m.brier.toFixed(3)} vs ${m.brier_reference.toFixed(3)} baseline`,
                 tone("skill", m.brier_skill))}
        ${metric("Calibration error", m.ece.toFixed(3),
                 "mean gap from the diagonal", tone("ece", m.ece))}
      </div>

      <div class="grid-2 mt-3">
        <div>
          <div class="panel-title">Discrimination</div>
          <div class="chart-wrap" style="height:250px"><canvas id="roc-${m.name}"></canvas></div>
        </div>
        <div>
          <div class="panel-title">Precision and recall</div>
          <div class="chart-wrap" style="height:250px"><canvas id="pr-${m.name}"></canvas></div>
        </div>
      </div>

      <div class="grid-2 mt-3">
        <div>
          <div class="panel-title">Calibration</div>
          <div class="chart-wrap" style="height:250px"><canvas id="cal-${m.name}"></canvas></div>
          <p class="muted small mt-1">Dot size is how many days landed in that bin.</p>
        </div>
        <div>
          <div class="panel-title">At the deployed cutoff</div>
          ${confusionBlock(c, m)}
        </div>
      </div>

      <div class="panel-title mt-3">The whole tradeoff</div>
      ${thresholdTable(m.thresholds, threshold)}

      <div class="panel-title mt-3">Where it went wrong</div>
      <p class="muted small">The twelve most confident mistakes, so the failures are inspectable
        rather than averaged away.</p>
      ${errorTable(m.errors || [])}

      <p class="muted small mt-2">${m.method}. Features: ${(m.features || []).join(", ")}.</p>
    </div>`;
}

(async () => {
  const box = document.getElementById("validation-body");
  const rep = await TS.fetchJSON("/api/validation");
  if (rep.error) {
    box.innerHTML = `<div class="card"><div class="error-note">${rep.error}</div></div>`;
    return;
  }
  const threshold = rep.decision_threshold || 0.5;
  box.innerHTML = rep.models.map(m => renderModel(m, threshold)).join("");

  for (const m of rep.models) {
    if (!m.available) continue;
    charts.push(curveChart(document.getElementById(`roc-${m.name}`), m.roc_curve, {
      label: `ROC, AUC ${m.roc_auc.toFixed(3)}`, refLabel: "Chance",
      ref: [{ x: 0, y: 0 }, { x: 1, y: 1 }],
      x: "False positive rate", y: "True positive rate",
    }));
    charts.push(curveChart(document.getElementById(`pr-${m.name}`), m.pr_curve, {
      label: `PR, AP ${m.average_precision.toFixed(3)}`, refLabel: "Base rate",
      ref: [{ x: 0, y: m.base_rate }, { x: 1, y: m.base_rate }],
      x: "Recall", y: "Precision",
    }));
    charts.push(calibrationChart(document.getElementById(`cal-${m.name}`), m.calibration));
  }

  const when = new Date((rep.generated_at || 0) * 1000);
  const note = document.createElement("p");
  note.className = "muted small mt-2";
  note.textContent = `Report generated ${when.toLocaleString()}. Regenerated whenever the models are retrained.`;
  box.appendChild(note);
})();
