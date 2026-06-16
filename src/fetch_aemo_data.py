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
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

REGIONS = ["NSW1", "VIC1", "QLD1", "SA1", "TAS1"]
OUTPUT_DIR = Path("data")
OUTPUT_DIR.mkdir(exist_ok=True)
HISTORY_DAYS = 90
NEMWEB_BASE = "https://nemweb.com.au/Reports/Current"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; AEMO-Dashboard/1.0)"}


def get_latest_zip_links(report_path, pattern):
    url = f"{NEMWEB_BASE}/{report_path}/"
    print(f"  Fetching directory: {url}")
    r = requests.get(url, timeout=30, headers=HEADERS)
    print(f"  HTTP status: {r.status_code}")
    if r.status_code != 200:
        print(f"  Response body: {r.text[:500]}")
        return []
    links = re.findall(r'href="([^"]+\.zip)"', r.text, re.IGNORECASE)
    print(f"  Found {len(links)} zip links total")
    matched = [l for l in links if re.search(pattern, l, re.IGNORECASE)]
    print(f"  Matched {len(matched)} links for pattern '{pattern}'")
    if matched:
        print(f"  Latest match: {matched[-1]}")
    result = []
    for l in matched[-2:]:
        full = f"https://nemweb.com.au{l}" if l.startswith("/") else l
        result.append(full)
    return result


def download_and_extract_csv(zip_url):
    print(f"    Downloading: {zip_url}")
    r = requests.get(zip_url, timeout=60, headers=HEADERS)
    print(f"    HTTP status: {r.status_code}, size: {len(r.content)} bytes")
    rows = []
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        print(f"    ZIP contents: {zf.namelist()}")
        for name in zf.namelist():
            if name.upper().endswith(".CSV"):
                with zf.open(name) as f:
                    text = f.read().decode("utf-8", errors="replace")
                    lines = text.splitlines()
                    print(f"    CSV lines: {len(lines)}, first 3: {lines[:3]}")
                    data_lines = [l for l in lines if l.startswith("D,")]
                    print(f"    Data rows (D,): {len(data_lines)}")
                    reader = csv.DictReader(data_lines)
                    rows.extend(list(reader))
    return rows


def safe_float(val):
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def fetch_actual_demand():
    print("\n--- Actual Demand ---")
    links = get_latest_zip_links("Dispatch_SCADA", r"PUBLIC_DISPATCHSCADA")
    records = []
    for link in links:
        try:
            rows = download_and_extract_csv(link)
            print(f"    Parsed {len(rows)} rows")
            if rows:
                print(f"    Sample row keys: {list(rows[0].keys())[:8]}")
                print(f"    Sample row: {dict(list(rows[0].items())[:5])}")
            for row in rows:
                if row.get("REGIONID") in REGIONS:
                    records.append({
                        "timestamp": row.get("SETTLEMENTDATE", "").strip(),
                        "region": row.get("REGIONID", "").strip(),
                        "actual_demand_mw": safe_float(row.get("TOTALDEMAND")),
                    })
        except Exception as e:
            print(f"    ERROR: {e}")
    print(f"  Total actual demand records: {len(records)}")
    return records


def fetch_forecast_demand():
    print("\n--- Forecast Demand ---")
    links = get_latest_zip_links("Predispatch_Reports", r"PUBLIC_PREDISPATCH_REGION_SOLUTION")
    records = []
    for link in links:
        try:
            rows = download_and_extract_csv(link)
            print(f"    Parsed {len(rows)} rows")
            if rows:
                print(f"    Sample row keys: {list(rows[0].keys())[:8]}")
            for row in rows:
                if row.get("REGIONID") in REGIONS:
                    records.append({
                        "timestamp": row.get("DATETIME", row.get("PERIODID", "")).strip(),
                        "region": row.get("REGIONID", "").strip(),
                        "forecast_demand_mw": safe_float(row.get("TOTALDEMAND")),
                    })
        except Exception as e:
            print(f"    ERROR: {e}")
    print(f"  Total forecast records: {len(records)}")
    return records


def fetch_rooftop_solar():
    print("\n--- Rooftop Solar ---")
    records = []
    for subdir, label in [("ACTUAL", "rooftop_actual_mw"), ("FORECAST", "rooftop_forecast_mw")]:
        links = get_latest_zip_links(f"ROOFTOP_PV/{subdir}", r"ROOFTOP_PV")
        for link in links:
            try:
                rows = download_and_extract_csv(link)
                print(f"    [{subdir}] Parsed {len(rows)} rows")
                if rows:
                    print(f"    Sample keys: {list(rows[0].keys())[:8]}")
                for row in rows:
                    if row.get("REGIONID") in REGIONS:
                        records.append({
                            "timestamp": row.get("INTERVAL_DATETIME", "").strip(),
                            "region": row.get("REGIONID", "").strip(),
                            label: safe_float(row.get("POWER")),
                        })
            except Exception as e:
                print(f"    ERROR: {e}")
    print(f"  Total solar records: {len(records)}")
    return records


def merge_records(actual, forecast, solar):
    index = {}

    def upsert(r, *fields):
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

    return sorted(index.values(), key=lambda x: x["timestamp"])


def compute_peak_days(rows, top_n=10):
    from collections import defaultdict
    daily_max = defaultdict(float)
    for row in rows:
        date = row.get("timestamp", "")[:10]
        region = row.get("region", "")
        demand = row.get("actual_demand_mw") or 0
        key = (region, date)
        if demand > daily_max[key]:
            daily_max[key] = demand
    by_region = defaultdict(list)
    for (region, date), val in daily_max.items():
        if val > 0:
            by_region[region].append({"region": region, "date": date, "peak_demand_mw": val})
    return {r: sorted(days, key=lambda x: x["peak_demand_mw"], reverse=True)[:top_n]
            for r, days in by_region.items()}


def trim_to_history(rows, days):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    return [r for r in rows if r.get("timestamp", "") >= cutoff]


def load_existing_csv(path):
    if not path.exists():
        return []
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def save_csv(rows, path):
    if not rows:
        print("  No rows to save!")
        return
    fields = ["timestamp", "region", "actual_demand_mw", "forecast_demand_mw",
              "rooftop_actual_mw", "rooftop_forecast_mw"]
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"  Saved {len(rows)} rows -> {path}")


def main():
    print("=== AEMO Data Ingestion (debug mode) ===")
    csv_path = OUTPUT_DIR / "forecast_vs_actual.csv"
    peaks_path = OUTPUT_DIR / "peak_days.json"

    existing = load_existing_csv(csv_path)
    print(f"Existing rows in CSV: {len(existing)}")

    actual = fetch_actual_demand()
    forecast = fetch_forecast_demand()
    solar = fetch_rooftop_solar()

    print(f"\nMerging {len(actual)} actual + {len(forecast)} forecast + {len(solar)} solar records...")
    new_rows = merge_records(actual, forecast, solar)

    all_by_key = {(r["timestamp"], r["region"]): r for r in existing}
    for r in new_rows:
        key = (r["timestamp"], r["region"])
        all_by_key[key] = {**all_by_key.get(key, {}), **r}

    all_rows = sorted(all_by_key.values(), key=lambda x: x["timestamp"])
    all_rows = trim_to_history(all_rows, HISTORY_DAYS)

    print(f"Total rows after merge: {len(all_rows)}")
    save_csv(all_rows, csv_path)

    peaks = compute_peak_days(all_rows)
    with open(peaks_path, "w") as f:
        json.dump(peaks, f, indent=2)
    print(f"Peak days saved -> {peaks_path}")
    print("\nDone.")


if __name__ == "__main__":
    main()
