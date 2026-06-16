"""
fetch_aemo_data.py
Fetches AEMO forecast vs actual demand and rooftop solar from NEMWEB.
Designed to run as a GitHub Actions scheduled job.

Regions: NSW1, VIC1, QLD1, SA1, TAS1
"""

import requests
import zipfile
import io
import csv
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────
REGIONS = ["NSW1", "VIC1", "QLD1", "SA1", "TAS1"]
OUTPUT_DIR = Path("data")
OUTPUT_DIR.mkdir(exist_ok=True)

NEMWEB_BASE = "https://nemweb.com.au/Reports/Current"

# How many days of history to keep in the rolling CSV
HISTORY_DAYS = 90

# ── Helpers ───────────────────────────────────────────────────────────────────

def get_latest_zip_links(report_path: str, pattern: str) -> list[str]:
    """Scrape NEMWEB directory listing and return matching zip URLs."""
    url = f"{NEMWEB_BASE}/{report_path}/"
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    links = re.findall(r'href="([^"]+\.zip)"', r.text, re.IGNORECASE)
    matched = [l for l in links if re.search(pattern, l, re.IGNORECASE)]
    # Return last 2 (most recent) to avoid gaps
    return [f"https://nemweb.com.au{l}" if l.startswith("/") else l
            for l in matched[-2:]]


def download_and_extract_csv(zip_url: str) -> list[dict]:
    """Download a NEMWEB zip and parse the inner CSV rows."""
    r = requests.get(zip_url, timeout=60)
    r.raise_for_status()
    rows = []
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        for name in zf.namelist():
            if name.upper().endswith(".CSV"):
                with zf.open(name) as f:
                    text = f.read().decode("utf-8", errors="replace")
                    reader = csv.DictReader(
                        line for line in text.splitlines()
                        if line.startswith("D,")   # NEMWEB data rows start with D
                    )
                    rows.extend(list(reader))
    return rows


# ── Actual Demand (Dispatch SCADA) ────────────────────────────────────────────

def fetch_actual_demand() -> list[dict]:
    """
    Pull 5-min dispatch SCADA totals.
    Key columns: SETTLEMENTDATE, REGIONID, TOTALDEMAND
    """
    links = get_latest_zip_links("Dispatch_SCADA", r"PUBLIC_DISPATCHSCADA")
    records = []
    for link in links:
        try:
            rows = download_and_extract_csv(link)
            for row in rows:
                if row.get("REGIONID") in REGIONS:
                    records.append({
                        "timestamp": row.get("SETTLEMENTDATE", "").strip(),
                        "region": row.get("REGIONID", "").strip(),
                        "actual_demand_mw": safe_float(row.get("TOTALDEMAND")),
                        "type": "actual_demand",
                    })
        except Exception as e:
            print(f"  Warning: could not fetch {link}: {e}")
    return records


# ── Pre-dispatch Forecast ─────────────────────────────────────────────────────

def fetch_forecast_demand() -> list[dict]:
    """
    Pull pre-dispatch regional demand forecasts (30-min intervals).
    Key columns: PERIODID, REGIONID, TOTALDEMAND (forecast)
    """
    links = get_latest_zip_links(
        "Predispatch_Reports", r"PUBLIC_PREDISPATCH_REGION_SOLUTION"
    )
    records = []
    for link in links:
        try:
            rows = download_and_extract_csv(link)
            for row in rows:
                if row.get("REGIONID") in REGIONS:
                    records.append({
                        "timestamp": row.get("DATETIME", row.get("PERIODID", "")).strip(),
                        "region": row.get("REGIONID", "").strip(),
                        "forecast_demand_mw": safe_float(row.get("TOTALDEMAND")),
                        "type": "forecast_demand",
                    })
        except Exception as e:
            print(f"  Warning: could not fetch {link}: {e}")
    return records


# ── Rooftop Solar ─────────────────────────────────────────────────────────────

def fetch_rooftop_solar() -> list[dict]:
    """
    Pull AEMO rooftop PV actual and forecast.
    Key columns: INTERVAL_DATETIME, REGIONID, POWER (MW)
    """
    records = []

    for subdir, label in [("ACTUAL", "rooftop_actual_mw"), ("FORECAST", "rooftop_forecast_mw")]:
        links = get_latest_zip_links(f"ROOFTOP_PV/{subdir}", r"ROOFTOP_PV")
        for link in links:
            try:
                rows = download_and_extract_csv(link)
                for row in rows:
                    if row.get("REGIONID") in REGIONS:
                        records.append({
                            "timestamp": row.get("INTERVAL_DATETIME", "").strip(),
                            "region": row.get("REGIONID", "").strip(),
                            label: safe_float(row.get("POWER")),
                            "type": label,
                        })
            except Exception as e:
                print(f"  Warning: could not fetch {link}: {e}")

    return records


# ── Merge & Save ──────────────────────────────────────────────────────────────

def merge_records(actual, forecast, solar) -> list[dict]:
    """
    Merge all records by (timestamp, region) into a single table.
    """
    index: dict[tuple, dict] = {}

    def upsert(r: dict, *fields):
        key = (r["timestamp"], r["region"])
        if key not in index:
            index[key] = {"timestamp": r["timestamp"], "region": r["region"]}
        for f in fields:
            if f in r and r[f] is not None:
                index[key][f] = r[f]

    for r in actual:
        upsert(r, "actual_demand_mw")
    for r in forecast:
        upsert(r, "forecast_demand_mw")
    for r in solar:
        if "rooftop_actual_mw" in r:
            upsert(r, "rooftop_actual_mw")
        if "rooftop_forecast_mw" in r:
            upsert(r, "rooftop_forecast_mw")

    rows = sorted(index.values(), key=lambda x: x["timestamp"])
    return rows


def safe_float(val) -> float | None:
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def compute_peak_days(rows: list[dict], top_n: int = 10) -> list[dict]:
    """Find top N peak demand days per region."""
    from collections import defaultdict
    daily_max: dict[tuple, float] = defaultdict(float)

    for row in rows:
        ts = row.get("timestamp", "")
        date = ts[:10]  # YYYY-MM-DD
        region = row.get("region", "")
        demand = row.get("actual_demand_mw") or 0
        key = (region, date)
        if demand > daily_max[key]:
            daily_max[key] = demand

    peaks = [
        {"region": k[0], "date": k[1], "peak_demand_mw": v}
        for k, v in daily_max.items()
        if v > 0
    ]
    # Group by region, take top N
    from collections import defaultdict as dd
    by_region: dict[str, list] = dd(list)
    for p in peaks:
        by_region[p["region"]].append(p)

    result = {}
    for region, days in by_region.items():
        top = sorted(days, key=lambda x: x["peak_demand_mw"], reverse=True)[:top_n]
        result[region] = top

    return result


def trim_to_history(rows: list[dict], days: int) -> list[dict]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    return [r for r in rows if r.get("timestamp", "") >= cutoff]


def load_existing_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def save_csv(rows: list[dict], path: Path):
    if not rows:
        return
    fields = ["timestamp", "region", "actual_demand_mw", "forecast_demand_mw",
              "rooftop_actual_mw", "rooftop_forecast_mw"]
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"  Saved {len(rows)} rows → {path}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("=== AEMO Data Ingestion ===")
    csv_path = OUTPUT_DIR / "forecast_vs_actual.csv"
    peaks_path = OUTPUT_DIR / "peak_days.json"

    print("Loading existing data...")
    existing = load_existing_csv(csv_path)
    existing_keys = {(r["timestamp"], r["region"]) for r in existing}

    print("Fetching actual demand...")
    actual = fetch_actual_demand()
    print(f"  Got {len(actual)} actual demand records")

    print("Fetching forecast demand...")
    forecast = fetch_forecast_demand()
    print(f"  Got {len(forecast)} forecast demand records")

    print("Fetching rooftop solar...")
    solar = fetch_rooftop_solar()
    print(f"  Got {len(solar)} rooftop solar records")

    print("Merging data...")
    new_merged = merge_records(actual, forecast, solar)

    # Combine with existing, dedup by (timestamp, region)
    all_rows_by_key = {(r["timestamp"], r["region"]): r for r in existing}
    for r in new_merged:
        key = (r["timestamp"], r["region"])
        all_rows_by_key[key] = {**all_rows_by_key.get(key, {}), **r}

    all_rows = sorted(all_rows_by_key.values(), key=lambda x: x["timestamp"])
    all_rows = trim_to_history(all_rows, HISTORY_DAYS)

    save_csv(all_rows, csv_path)

    print("Computing peak days...")
    peaks = compute_peak_days(all_rows)
    with open(peaks_path, "w") as f:
        json.dump(peaks, f, indent=2)
    print(f"  Saved peak days → {peaks_path}")

    print("Done.")


if __name__ == "__main__":
    main()
