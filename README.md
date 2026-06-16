# ⚡ AEMO Energy Dashboard

A GitHub-hosted web dashboard showing **AEMO NEM forecast vs actual demand** and **rooftop solar**, with automatic 30-minute data ingestion via GitHub Actions and a top-10 peak demand day selector.

---

## Features

- 📈 **Forecast vs Actual Demand** — overlaid line/area chart per NEM region
- ☀️ **Rooftop Solar** — actual and AEMO forecast generation
- 📉 **Forecast Error** — bar chart of actual minus forecast
- 🏆 **Top 10 Peak Days** — dropdown + table, click any row to zoom into that day
- 🔁 **Auto-ingestion** — GitHub Actions fetches NEMWEB data every 30 minutes
- 🗂️ **Rolling 90-day history** — keeps data lean, no database needed

---

## Quick Start

### 1. Fork / clone this repo

```bash
git clone https://github.com/YOUR_USERNAME/aemo-dashboard.git
cd aemo-dashboard
```

### 2. Enable GitHub Actions

Go to **Settings → Actions → General** and set:
- "Allow all actions and reusable workflows" ✓
- Under "Workflow permissions" → **Read and write permissions** ✓

### 3. Enable GitHub Pages

Go to **Settings → Pages**:
- Source: **Deploy from a branch**
- Branch: `main` / `root`

Your dashboard will be live at:
`https://YOUR_USERNAME.github.io/aemo-dashboard/`

### 4. Run the first data fetch manually

Go to **Actions → Update AEMO Data → Run workflow** to seed your initial data.

After that, it runs automatically every 30 minutes.

---

## Local Development

```bash
pip install requests

# Fetch data locally
python src/fetch_aemo_data.py

# Serve the dashboard (Python)
python -m http.server 8080
# Then open http://localhost:8080
```

> ⚠️ **CORS note:** The dashboard fetches local CSV/JSON files, so you must serve via a local HTTP server (not `file://`).

---

## Data Sources (NEMWEB)

| Feed | NEMWEB path |
|------|------------|
| Actual demand (5-min SCADA) | `/Reports/Current/Dispatch_SCADA/` |
| Forecast demand (pre-dispatch) | `/Reports/Current/Predispatch_Reports/` |
| Rooftop solar actual | `/Reports/Current/ROOFTOP_PV/ACTUAL/` |
| Rooftop solar forecast | `/Reports/Current/ROOFTOP_PV/FORECAST/` |

All data is sourced from the Australian Energy Market Operator's public [NEMWEB portal](https://nemweb.com.au).

---

## Repo Structure

```
aemo-dashboard/
├── .github/
│   └── workflows/
│       └── update-data.yml      # Scheduled GitHub Actions job
├── data/
│   ├── forecast_vs_actual.csv   # Auto-generated — do not edit manually
│   └── peak_days.json           # Top 10 peak days per region
├── src/
│   └── fetch_aemo_data.py       # Ingestion + processing script
├── index.html                   # Dashboard UI
├── dashboard.js                 # Chart logic
└── README.md
```

---

## Customisation

| What | Where |
|------|-------|
| Add/remove NEM regions | `REGIONS` list in `fetch_aemo_data.py` |
| Change history window | `HISTORY_DAYS` in `fetch_aemo_data.py` |
| Change update frequency | `cron` in `update-data.yml` (min 5 min on free GitHub) |
| Change top-N peak days | `top_n` arg in `compute_peak_days()` |
| Chart colours / fonts | CSS variables in `index.html` `:root` block |

---

## Extending

- **Add FCAS / price data**: pull `Dispatch_PRICE` from NEMWEB, add new dataset to `dashboard.js`
- **Email alerts on peak**: add a Python step to `update-data.yml` that sends email if demand > threshold
- **Export to Excel**: add a "Download CSV" button in `dashboard.js` using `Blob` + `URL.createObjectURL`
- **Multiple regions overlay**: extend `state` to support multi-region selection

---

## License

MIT — data © Australian Energy Market Operator (AEMO), used under AEMO's open data terms.
