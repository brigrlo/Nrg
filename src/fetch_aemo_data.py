import requests, zipfile, io, csv, json, re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from collections import defaultdict

REGIONS = ["NSW1", "VIC1", "QLD1", "SA1", "TAS1"]
OUTPUT_DIR = Path("data")
OUTPUT_DIR.mkdir(exist_ok=True)
HISTORY_DAYS = 90
NEMWEB_BASE = "https://nemweb.com.au/Reports/Current"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; AEMO-Dashboard/1.0)"}

def get_links(path, pattern):
    url = f"{NEMWEB_BASE}/{path}/"
    print(f"GET {url}")
    r = requests.get(url, timeout=30, headers=HEADERS)
    print(f"Status: {r.status_code}")
    if r.status_code != 200:
        print(f"Body: {r.text[:300]}")
        return []
    links = re.findall(r'href="([^"]+\.zip)"', r.text, re.IGNORECASE)
    matched = [l for l in links if re.search(pattern, l, re.IGNORECASE)]
    print(f"Matched zips: {len(matched)}")
    return [("https://nemweb.com.au" + l if l.startswith("/") else l) for l in matched[-2:]]

def extract_csv(zip_url):
    r = requests.get(zip_url, timeout=60, headers=HEADERS)
    print(f"  ZIP {r.status_code} {len(r.content)} bytes")
    rows = []
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        for name in zf.namelist():
            if name.upper().endswith(".CSV"):
                with zf.open(name) as f:
                    lines = f.read().decode("utf-8", errors="replace").splitlines()
                    dlines = [l for l in lines if l.startswith("D,")]
                    print(f"  {name}: {len(lines)} lines, {len(dlines)} data rows")
                    if lines:
                        print(f"  First line: {lines[0][:120]}")
                    rows.extend(list(csv.DictReader(dlines)))
    return rows

def sf(v):
    try:
        return float(v)
    except:
        return None

def fetch_actual():
    print("\n=== ACTUAL DEMAND ===")
    records = []
    for url in get_links("Dispatch_SCADA", r"PUBLIC_DISPATCHSCADA"):
        try:
            rows = extract_csv(url)
            if rows:
                print(f"  Keys: {list(rows[0].keys())[:10]}")
            for row in rows:
                if row.get("REGIONID") in REGIONS:
                    records.append({"timestamp": row.get("SETTLEMENTDATE","").strip(), "region": row.get("REGIONID","").strip(), "actual_demand_mw": sf(row.get("TOTALDEMAND"))})
        except Exception as e:
            print(f"  ERR: {e}")
    print(f"  Records: {len(records)}")
    return records

def fetch_forecast():
    print("\n=== FORECAST DEMAND ===")
    records = []
    for url in get_links("Predispatch_Reports", r"PUBLIC_PREDISPATCH_REGION_SOLUTION"):
        try:
            rows = extract_csv(url)
            if rows:
                print(f"  Keys: {list(rows[0].keys())[:10]}")
            for row in rows:
                if row.get("REGIONID") in REGIONS:
                    records.append({"timestamp": row.get("DATETIME", row.get("PERIODID","")).strip(), "region": row.get("REGIONID","").strip(), "forecast_demand_mw": sf(row.get("TOTALDEMAND"))})
        except Exception as e:
            print(f"  ERR: {e}")
    print(f"  Records: {len(records)}")
    return records

def fetch_solar():
    print("\n=== ROOFTOP SOLAR ===")
    records = []
    subdirs = [("ACTUAL", "rooftop_actual_mw"), ("FORECAST", "rooftop_forecast_mw")]
    for subdir, label in subdirs:
        print(f"  Subdir: {subdir}")
        for url in get_links(f"ROOFTOP_PV/{subdir}", r"ROOFTOP_PV"):
            try:
                rows = extract_csv(url)
                if rows:
                    print(f"  Keys: {list(rows[0].keys())[:10]}")
                for row in rows:
                    if row.get("REGIONID") in REGIONS:
                        records.append({"timestamp": row.get("INTERVAL_DATETIME","").strip(), "region": row.get("REGIONID","").strip(), label: sf(row.get("POWER"))})
            except Exception as e:
                print(f"  ERR: {e}")
    print(f"  Records: {len(records)}")
    return records

def merge(actual, forecast, solar):
    idx = {}
    def upsert(r, *fields):
        key = (r["timestamp"], r["region"])
        if key not in idx:
            idx[key] = {"timestamp": r["timestamp"], "region": r["region"]}
        for f in fields:
            if f in r and r[f] is not None:
                idx[key][f] = r[f]
    for r in actual:
        upsert(r, "actual_demand_mw")
    for r in forecast:
        upsert(r, "forecast_demand_mw")
    for r in solar:
        for f in ["rooftop_actual_mw", "rooftop_forecast_mw"]:
            if f in r:
                upsert(r, f)
    return sorted(idx.values(), key=lambda x: x["timestamp"])

def peak_days(rows, n=10):
    dm = defaultdict(float)
    for row in rows:
        k = (row.get("region",""), row.get("timestamp","")[:10])
        d = row.get("actual_demand_mw") or 0
        if d > dm[k]:
            dm[k] = d
    by_r = defaultdict(list)
    for (reg, date), val in dm.items():
        if val > 0:
            by_r[reg].append({"region": reg, "date": date, "peak_demand_mw": val})
    return {r: sorted(v, key=lambda x: x["peak_demand_mw"], reverse=True)[:n] for r, v in by_r.items()}

def main():
    print("=== AEMO Ingest ===")
    csv_path = OUTPUT_DIR / "forecast_vs_actual.csv"
    peaks_path = OUTPUT_DIR / "peak_days.json"

    existing = []
    if csv_path.exists():
        with open(csv_path, newline="") as f:
            existing = list(csv.DictReader(f))
    print(f"Existing rows: {len(existing)}")

    actual = fetch_actual()
    forecast = fetch_forecast()
    solar = fetch_solar()

    new_rows = merge(actual, forecast, solar)
    all_by_key = {(r["timestamp"], r["region"]): r for r in existing}
    for r in new_rows:
        key = (r["timestamp"], r["region"])
        all_by_key[key] = {**all_by_key.get(key, {}), **r}

    cutoff = (datetime.now(timezone.utc) - timedelta(days=HISTORY_DAYS)).strftime("%Y-%m-%d")
    all_rows = sorted([r for r in all_by_key.values() if r.get("timestamp","") >= cutoff], key=lambda x: x["timestamp"])
    print(f"Total rows: {len(all_rows)}")

    fields = ["timestamp","region","actual_demand_mw","forecast_demand_mw","rooftop_actual_mw","rooftop_forecast_mw"]
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(all_rows)
    print(f"Saved {len(all_rows)} rows to {csv_path}")

    with open(peaks_path, "w") as f:
        json.dump(peak_days(all_rows), f, indent=2)
    print("Done.")

if __name__ == "__main__":
    main()
