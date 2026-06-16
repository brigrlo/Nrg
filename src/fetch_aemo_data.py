"""
fetch_aemo_data.py - with verbose debugging
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


def get_latest_zip_links(report_path: str, pattern: str) -> list[str]:
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


def download_and_extract_csv(zip_url: str) -> list[dict]:
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
