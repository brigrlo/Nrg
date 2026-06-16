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
        return []
    links = re.findall(r'href="([^"]+\.zip)"', r.text, re.IGNORECASE)
    matched = [l for l in links if re.search(pattern, l, re.IGNORECASE)]
    print(f"Matched zips: {len(matched)}")
    return [("https://nemweb.com.au" + l if l.startswith("/") else l) for l in matched[-2:]]

def get_csv_lines(zip_url):
    r = requests.get(zip_url, timeout=60, headers=HEADERS)
    lines = []
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        for name in zf.namelist():
            if name.upper().endswith(".CSV"):
                with zf.open(name) as f:
                    text = f.read().decode("utf-8", errors="replace")
                    lines = [l for l in text.splitlines() if l.startswith("D,")]
                    print(f"  {name}: {len(lines)} data rows")
    return lines

def sf(v):
    try:
        return float(v.strip())
    except:
        return None

def fetch_actual():
    # Row format: D,DISPATCH,UNIT_SCADA,1,SETTLEMENTDATE,DUID,SCADAVALUE,...
    # We need TOTALDEMAND per region — this file is per-unit, not per-region.
    # Use DISPATCH_REGIONSUM instead.
    print("\n=== ACTUAL DEMAND (REGIONSUM) ===")
    records = []
    for url in get_links("DispatchIS_Reports", r"PUBLIC_DISPATCHIS"):
        try:
            lines = get_csv_lines(url)
            for line in lines:
                cols = line.split(",")
                # REGION solution rows: D,DISPATCH,REGIONSUM,1,...
                if len(cols) > 10 and cols[2].strip() == "REGIONSUM":
                    # cols: D,DISPATCH,REGIONSUM,1,RUN_DATETIME,INTERVENTION,REGIONID,
                    #        TOTALDEMAND,AVAILABLEGENERATION,...
                    region = cols[6].strip()
                    ts     = cols[4].strip()
                    demand = sf(cols[7])
                    if region in REGIONS and ts and demand:
                        records.append({"timestamp": ts, "region": region, "actual_demand_mw": demand})
        except Exception as e:
            print(f"  ERR: {e}")
    print(f"  Records: {len(records)}")
    return records

def fetch_forecast():
    # Pre-dispatch region solution
    print("\n=== FORECAST DEMAND ===")
    records = []
    for url in get_links("Predispatch_Reports", r"PUBLIC_PREDISPATCH_REGION_SOLUTION"):
        try:
            lines = get_csv_lines(url)
            for line in lines:
                cols = line.split(",")
                # D,PREDISPATCH,REGION_SOLUTION,2,PREDISPATCH_SEQ,REGIONID,PERIODID,...,TOTALDEMAND
                if len(cols) > 10 and cols[2].strip() == "REGION_SOLUTION":
                    region = cols[5].strip()
                    ts     = cols[6].strip()
                    # TOTALDEMAND is col 9 in pre-dispatch region solution
                    demand = sf(cols[9]) if len(cols) > 9 else None
                    if region in REGIONS and ts and demand:
                        records.append({"timestamp": ts, "region": region, "forecast_demand_mw": demand})
        except Exception as e:
            print(f"  ERR: {e}")
    print(f"  Records: {len(records)}")
    return records

def fetch_solar():
    print("\n=== ROOFTOP SOLAR ===")
    records = []

    # ACTUAL: D,ROOFTOP,ACTUAL,2,INTERVAL_DATETIME,REGIONID,POWER,...
    for url in get_links("ROOFTOP_PV/ACTUAL", r"ROOFTOP_PV_ACTUAL"):
        try:
            lines = get_csv_lines(url)
            for line in lines:
                cols = line.split(",")
                if len(cols) > 6 and cols[2].strip() == "ACTUAL":
                    ts     = cols[4].strip()
                    region = cols[5].strip()
                    power  = sf(cols[6])
                    if region in REGIONS and ts and power is not None:
                        records.append({"timestamp": ts, "region": region, "rooftop_actual_mw": power})
        except Exception as e:
            print(f"  ERR actual solar: {e}")

    # FORECAST: D,ROOFTOP,FORECAST,1,LASTCHANGED,REGIONID,INTERVAL_DATETIME,POWERMEAN,...
    for url in get_links("ROOFTOP_PV/FORECAST", r"ROOFTOP_PV_FORECAST"):
        try:
            lines = get_csv_lines(url)
            for line in lines:
                cols = line.split(",")
                if len(cols) > 7 and cols[2].strip() == "FORECAST":
                    ts     = cols[6].strip()
                    region = cols[5].strip()
                    power  = sf(cols[7])
                    if region in REGIONS and ts and power is not None:
                        records.append({"timestamp": ts, "region": region, "rooftop_forecast_mw": power})
        except Exception as e:
            print(f"  ERR forecast solar: {e}")

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
    csv_path   = OUTPUT_DIR / "forecast_vs_actual.csv"
    peaks_path = OUTPUT_DIR / "peak_days.json"

    existing = []
    if csv_path.exists():
        with open(csv_path, newline="") as f:
            existing = list(csv.DictReader(f))
    print(f"Existing rows: {len(existing)}")

    actual   = fetch_actual()
    forecast = fetch_forecast()
    solar    = fetch_solar()

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
