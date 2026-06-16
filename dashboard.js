/**
 * dashboard.js
 * AEMO Energy Dashboard — Chart.js logic
 * Loads data/forecast_vs_actual.csv and data/peak_days.json from the repo.
 */

// ── Config ────────────────────────────────────────────────────────────────────
const DATA_CSV   = "./data/forecast_vs_actual.csv";
const PEAKS_JSON = "./data/peak_days.json";

// ── State ─────────────────────────────────────────────────────────────────────
let allData    = [];   // parsed CSV rows (all regions)
let peaksData  = {};   // { NSW1: [{date, peak_demand_mw},...], ... }
let charts     = {};   // Chart.js instances

let state = {
  region:   "NSW1",
  window:   7,         // days
  layers:   { forecast: true, actual: true, solar: true },
  view:     "line",    // "line" | "area"
  peakDate: null,      // ISO date string if a peak day is selected
};

// ── Chart defaults ────────────────────────────────────────────────────────────
Chart.defaults.color = "#7986a8";
Chart.defaults.borderColor = "#1e2d47";
Chart.defaults.font.family = "'Inter', system-ui, sans-serif";

// ── Boot ──────────────────────────────────────────────────────────────────────
async function init() {
  try {
    await Promise.all([loadCSV(), loadPeaks()]);
    createCharts();
    render();
    bindControls();
    document.getElementById("last-updated").innerHTML =
      `<span class="pulse"></span>Updated ${new Date().toLocaleString("en-AU")}`;
  } catch (err) {
    document.getElementById("last-updated").textContent = "⚠ Could not load data";
    console.error(err);
  }
}

// ── Data loading ──────────────────────────────────────────────────────────────
async function loadCSV() {
  const res  = await fetch(DATA_CSV);
  const text = await res.text();
  return new Promise((resolve, reject) => {
    Papa.parse(text, {
      header: true,
      dynamicTyping: true,
      skipEmptyLines: true,
      complete: (results) => {
        allData = results.data.map(r => ({
          ...r,
          _date: new Date(r.timestamp.replace(" ", "T")),
        }));
        resolve();
      },
      error: reject,
    });
  });
}

async function loadPeaks() {
  const res  = await fetch(PEAKS_JSON);
  peaksData  = await res.json();
  populatePeakDropdown();
}

// ── Filter helpers ────────────────────────────────────────────────────────────
function getFilteredRows() {
  const now     = new Date();
  const cutoff  = new Date(now - state.window * 864e5);
  return allData.filter(r =>
    r.region === state.region &&
    r._date  >= cutoff &&
    r._date  <= now
  );
}

function getPeakDayRows() {
  if (!state.peakDate) return null;
  const date = state.peakDate;
  return allData.filter(r =>
    r.region === state.region &&
    r.timestamp.startsWith(date)
  );
}

// ── Populate peak dropdown ────────────────────────────────────────────────────
function populatePeakDropdown() {
  const sel   = document.getElementById("peak-select");
  const peaks = peaksData[state.region] || [];
  // Clear except placeholder
  while (sel.options.length > 1) sel.remove(1);
  peaks.forEach((p, i) => {
    const opt        = document.createElement("option");
    opt.value        = p.date;
    const medal      = i === 0 ? "🥇" : i === 1 ? "🥈" : i === 2 ? "🥉" : `#${i+1}`;
    opt.textContent  = `${medal}  ${p.date}  —  ${Math.round(p.peak_demand_mw).toLocaleString()} MW`;
    sel.appendChild(opt);
  });
}

// ── Render peak table ─────────────────────────────────────────────────────────
function renderPeakTable() {
  const container = document.getElementById("peak-table-container");
  const peaks     = peaksData[state.region] || [];
  document.getElementById("peak-region-label").textContent =
    state.region.replace("1", "");

  if (!peaks.length) {
    container.innerHTML = `<div class="loading">No peak data available</div>`;
    return;
  }

  const rows = peaks.map((p, i) => {
    const rankClass = i === 0 ? "rank-1" : i === 1 ? "rank-2" : i === 2 ? "rank-3" : "rank-other";
    return `
      <tr onclick="selectPeakDay('${p.date}')">
        <td><span class="rank-badge ${rankClass}">${i + 1}</span></td>
        <td>${p.date}</td>
        <td style="color:var(--accent);font-weight:700">${Math.round(p.peak_demand_mw).toLocaleString()} MW</td>
        <td style="color:var(--muted);font-size:0.7rem">${getDayOfWeek(p.date)}</td>
      </tr>`;
  }).join("");

  container.innerHTML = `
    <table class="peak-table">
      <thead>
        <tr>
          <th>#</th><th>Date</th><th>Peak Demand</th><th>Day</th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>`;
}

function getDayOfWeek(dateStr) {
  const days = ["Sun","Mon","Tue","Wed","Thu","Fri","Sat"];
  return days[new Date(dateStr + "T00:00:00").getDay()];
}

// ── Select peak day from table click ─────────────────────────────────────────
function selectPeakDay(date) {
  state.peakDate = date;
  document.getElementById("peak-select").value = date;
  render();
}
window.selectPeakDay = selectPeakDay;

// ── Create Chart.js instances ─────────────────────────────────────────────────
function createCharts() {
  charts.demand = new Chart(
    document.getElementById("demand-chart").getContext("2d"),
    {
      type: "line",
      data: { labels: [], datasets: [] },
      options: baseChartOptions("MW", "Demand (MW)"),
    }
  );

  charts.solar = new Chart(
    document.getElementById("solar-chart").getContext("2d"),
    {
      type: "line",
      data: { labels: [], datasets: [] },
      options: baseChartOptions("MW", "Solar (MW)"),
    }
  );

  charts.error = new Chart(
    document.getElementById("error-chart").getContext("2d"),
    {
      type: "bar",
      data: { labels: [], datasets: [] },
      options: {
        ...baseChartOptions("MW", "Error (MW)"),
        plugins: {
          ...baseChartOptions().plugins,
          annotation: {},
        },
      },
    }
  );
}

function baseChartOptions(unit = "MW", yLabel = "") {
  return {
    responsive: true,
    maintainAspectRatio: false,
    interaction: { mode: "index", intersect: false },
    animation: { duration: 300 },
    plugins: {
      legend:  { display: false },
      tooltip: {
        backgroundColor: "#131929",
        borderColor: "#1e2d47",
        borderWidth: 1,
        titleColor: "#e8eaf6",
        bodyColor: "#7986a8",
        callbacks: {
          label: ctx => ` ${ctx.dataset.label}: ${ctx.parsed.y != null ? Math.round(ctx.parsed.y).toLocaleString() : "—"} ${unit}`,
        },
      },
    },
    scales: {
      x: {
        ticks: { maxTicksLimit: 12, maxRotation: 0, font: { size: 11 } },
        grid:  { color: "#1e2d47" },
      },
      y: {
        title: { display: !!yLabel, text: yLabel, color: "#7986a8", font: { size: 11 } },
        grid:  { color: "#1e2d47" },
        ticks: {
          callback: v => v != null ? `${Math.round(v).toLocaleString()}` : "",
          font: { size: 11 },
        },
      },
    },
  };
}

// ── Main render ───────────────────────────────────────────────────────────────
function render() {
  const rows = state.peakDate ? getPeakDayRows() : getFilteredRows();

  if (!rows || rows.length === 0) {
    console.warn("No data for current selection");
    return;
  }

  const labels = rows.map(r => formatLabel(r._date, !!state.peakDate));

  // Demand chart
  updateDemandChart(rows, labels);

  // Solar chart
  updateSolarChart(rows, labels);

  // Error chart
  updateErrorChart(rows, labels);

  // Stats
  updateStats(rows);

  // Peak table
  renderPeakTable();
}

function formatLabel(date, isDay) {
  if (isDay) {
    return date.toLocaleTimeString("en-AU", { hour: "2-digit", minute: "2-digit" });
  }
  return date.toLocaleDateString("en-AU", { day: "numeric", month: "short" }) +
    " " + date.toLocaleTimeString("en-AU", { hour: "2-digit", minute: "2-digit" });
}

function updateDemandChart(rows, labels) {
  const fill = state.view === "area";

  const actualDs = {
    label: "Actual Demand",
    data:  rows.map(r => r.actual_demand_mw ?? null),
    borderColor: "#ef5350",
    backgroundColor: fill ? "rgba(239,83,80,0.15)" : "transparent",
    fill: fill,
    borderWidth: 2,
    pointRadius: 0,
    tension: 0.3,
    hidden: !state.layers.actual,
  };

  const forecastDs = {
    label: "Forecast Demand",
    data:  rows.map(r => r.forecast_demand_mw ?? null),
    borderColor: "#4dd0e1",
    backgroundColor: fill ? "rgba(77,208,225,0.08)" : "transparent",
    fill: fill,
    borderDash: [6, 3],
    borderWidth: 2,
    pointRadius: 0,
    tension: 0.3,
    hidden: !state.layers.forecast,
  };

  charts.demand.data.labels   = labels;
  charts.demand.data.datasets = [actualDs, forecastDs];
  charts.demand.update();
}

function updateSolarChart(rows, labels) {
  const fill = state.view === "area";

  const solarActDs = {
    label: "Solar Actual",
    data:  rows.map(r => r.rooftop_actual_mw ?? null),
    borderColor: "#ffd54f",
    backgroundColor: fill ? "rgba(255,213,79,0.2)" : "transparent",
    fill: fill,
    borderWidth: 2,
    pointRadius: 0,
    tension: 0.3,
    hidden: !state.layers.solar,
  };

  const solarFcDs = {
    label: "Solar Forecast",
    data:  rows.map(r => r.rooftop_forecast_mw ?? null),
    borderColor: "#ff8f00",
    backgroundColor: "transparent",
    borderDash: [6, 3],
    borderWidth: 2,
    pointRadius: 0,
    tension: 0.3,
    hidden: !state.layers.solar,
  };

  charts.solar.data.labels   = labels;
  charts.solar.data.datasets = [solarActDs, solarFcDs];
  charts.solar.update();
}

function updateErrorChart(rows, labels) {
  const errors = rows.map(r => {
    if (r.actual_demand_mw != null && r.forecast_demand_mw != null)
      return r.actual_demand_mw - r.forecast_demand_mw;
    return null;
  });

  const colors = errors.map(e =>
    e == null ? "#1e2d47" :
    e > 0     ? "rgba(239,83,80,0.7)" :
                "rgba(77,208,225,0.7)"
  );

  charts.error.data.labels = labels;
  charts.error.data.datasets = [{
    label: "Forecast Error",
    data:  errors,
    backgroundColor: colors,
    borderWidth: 0,
  }];
  charts.error.update();
}

// ── Stats bar ─────────────────────────────────────────────────────────────────
function updateStats(rows) {
  const last = [...rows].reverse().find(r => r.actual_demand_mw != null);
  const lastFc = [...rows].reverse().find(r => r.forecast_demand_mw != null);
  const lastSolar = [...rows].reverse().find(r => r.rooftop_actual_mw != null);
  const peak = Math.max(...rows.map(r => r.actual_demand_mw || 0));

  setText("stat-current",  last     ? Math.round(last.actual_demand_mw).toLocaleString() : "—");
  setText("stat-forecast", lastFc   ? Math.round(lastFc.forecast_demand_mw).toLocaleString() : "—");
  setText("stat-solar",    lastSolar ? Math.round(lastSolar.rooftop_actual_mw).toLocaleString() : "—");
  setText("stat-peak",     peak > 0 ? Math.round(peak).toLocaleString() : "—");

  const errEl = document.getElementById("stat-error");
  if (last && lastFc) {
    const err = last.actual_demand_mw - lastFc.forecast_demand_mw;
    errEl.textContent = (err >= 0 ? "+" : "") + Math.round(err).toLocaleString();
    errEl.className   = "stat-value delta " + (err >= 0 ? "positive" : "negative");
  } else {
    errEl.textContent = "—";
    errEl.className   = "stat-value delta";
  }
}

function setText(id, val) {
  const el = document.getElementById(id);
  if (el) el.textContent = val;
}

// ── Controls binding ──────────────────────────────────────────────────────────
function bindControls() {
  document.getElementById("region-select").addEventListener("change", e => {
    state.region   = e.target.value;
    state.peakDate = null;
    document.getElementById("peak-select").value = "";
    populatePeakDropdown();
    render();
  });

  document.getElementById("window-select").addEventListener("change", e => {
    state.window   = +e.target.value;
    state.peakDate = null;
    document.getElementById("peak-select").value = "";
    render();
  });

  document.getElementById("peak-select").addEventListener("change", e => {
    state.peakDate = e.target.value || null;
    render();
  });

  document.querySelectorAll(".toggle-btn[data-layer]").forEach(btn => {
    btn.addEventListener("click", () => {
      const layer = btn.dataset.layer;
      state.layers[layer] = !state.layers[layer];
      btn.classList.toggle("active", state.layers[layer]);
      render();
    });
  });

  document.querySelectorAll(".view-btn[data-view]").forEach(btn => {
    btn.addEventListener("click", () => {
      state.view = btn.dataset.view;
      document.querySelectorAll(".view-btn").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      render();
    });
  });
}

// ── Start ─────────────────────────────────────────────────────────────────────
init();
