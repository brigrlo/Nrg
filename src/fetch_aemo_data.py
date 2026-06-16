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

def clean(v):
    return v.strip().strip('"').strip("'")

def normalise_ts(ts):
    return clean(ts).replace("/", "-")

def sf(v):
    try:
        return float(clean(v))
    except:
        return None

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
    return lines

def fetch_actual():
    # DISPATCHIS REGIONSUM confirmed layout:
    # D,DISPATCH,REGIONSUM,<ver>,SETTLEMENTDATE,RUNNO,REGIONID,INTERVENTION,TOTALDEMAND,...
    #  0    1         2     3         4            5      6          7            8
    print("\n=== ACTUAL DEMAND ===")
    records = []
    for url in get_links("DispatchIS_Reports", r"PUBLIC_DISPATCHIS"):
        try:
            lines = get_csv_lines(url)
            shown = False
            for line in lines:
                cols = line.split(",")
                if len(cols) > 8 and clean(cols[2]) == "REGIONSUM":
                    if not shown:
                        print(f"  REGIONSUM cols: {[clean(c) for c in cols[:10]]}")
                        shown = True
                    ts     = normalise_ts(cols[4])
                    region = clean(cols[6])
                    demand = sf(cols[8])
                    if region in REGIONS and ts and demand is not None and 0 < demand < 100000:
                        records.append({"timestamp": ts, "region": region, "actual_demand_mw": demand})
        except Exception as e:
            print(f"  ERR: {e}")
    print(f"  Records: {len(records)}")
    if records:
        print(f"  Sample: {records[0]}")
    return records

def fetch_forecast():
    # PREDISPATCH REGION_SOLUTION confirmed layout (AEMO MMS schema):
    # D,PREDISPATCH,REGION_SOLUTION,<ver>,DATETIME,REGIONID,PERIODID,INTERVENTION,RRP,TOTALDEMAND,...
    #  0      1            2           3      4         5        6        7        8       9
    print("\n=== FORECAST DEMAND ===")
    records = []
    for url in get_links("Predispatch_Reports", r"PUBLIC_PREDISPATCH"):
        try:
            lines = get_csv_lines(url)
            shown = False
            for line in lines:
                cols = line.split(",")
                if len(cols) > 9 and clean(cols[2]) == "REGION_SOLUTION":
                    if not shown:
                        print(f"  REGION_SOLUTION cols: {[clean(c) for c in cols[:12]]}")
                        shown = True
                    ts     = normalise_ts(cols[4])
                    region = clean(cols[5])
                    demand = sf(cols[9])
                    if region in REGIONS and ts and demand is not None and 0 < demand < 100000:
                        records.append({"timestamp": ts, "region": region, "forecast_demand_mw": demand})
        except Exception as e:
            print(f"  ERR: {e}")
    print(f"  Records: {len(records)}")
    if records:
        print(f"  Sample: {records[0]}")
    return records

def fetch_solar():
    # ROOFTOP ACTUAL: D,ROOFTOP,ACTUAL,<ver>,INTERVAL_DATETIME,REGIONID,POWER,QI,TYPE,LASTCHANGED
    #                  0    1      2     3          4              5      6   7   8      9
    # ROOFTOP FORECAST: D,ROOFTOP,FORECAST,<ver>,LASTCHANGED,REGIONID,INTERVAL_DATETIME,POWERMEAN,...
    #                    0    1       2      3        4          5            6              7
    print("\n=== ROOFTOP SOLAR ===")
    records = []

    for url in get_links("ROOFTOP_PV/ACTUAL", r"ROOFTOP_PV_ACTUAL"):
        try:
            lines = get_csv_lines(url)
            shown = False
            for line in lines:
                cols = line.split(",")
                if len(cols) > 6 and clean(cols[2]) == "ACTUAL":
                    if not shown:
                        print(f"  ROOFTOP ACTUAL cols: {[clean(c) for c in cols[:10]]}")
                        shown = True
                    ts     = normalise_ts(cols[4])
                    region = clean(cols[5])
                    power  = sf(cols[6])
                    if region in REGIONS and ts and power is not None:
                        records.append({"timestamp": ts, "region": region, "rooftop_actual_mw": power})
        except Exception as e:
            print(f"  ERR actual solar: {e}")

    for url in get_links("ROOFTOP_PV/FORECAST", r"ROOFTOP_PV_FORECAST"):
        try:
            lines = get_csv_lines(url)
            shown = False
            for line in lines:
                cols = line.split(",")
                if len(cols) > 7 and clean(cols[2]) == "FORECAST":
                    if not shown:
                        print(f"  ROOFTOP FORECAST cols: {[clean(c) for c in cols[:10]]}")
                        shown = True
                    ts     = normalise_ts(cols[6])
                    region = clean(cols[5])
                    power  = sf(cols[7])
                    if region in REGIONS and ts and power is not None:
                        records.append({"timestamp": ts, "region": region, "rooftop_forecast_mw": power})
        except Exception as e:
            print(f"  ERR forecast solar: {e}")

    print(f"  Records: {len(records)}")
    if records:
        print(f"  Sample: {records[0]}")
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
    print(f"Merged new rows: {len(new_rows)}")

    all_by_key = {(r["timestamp"], r["region"]): r for r in existing}
    for r in new_rows:
        key = (r["timestamp"], r["region"])
        all_by_key[key] = {**all_by_key.get(key, {}), **r}

    cutoff = (datetime.now(timezone.utc) - timedelta(days=HISTORY_DAYS)).strftime("%Y-%m-%d")
    all_rows = sorted(
        [r for r in all_by_key.values() if normalise_ts(r.get("timestamp",""))[:10] >= cutoff],
        key=lambda x: x["timestamp"]
    )
    print(f"Total rows after cutoff: {len(all_rows)}")

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
