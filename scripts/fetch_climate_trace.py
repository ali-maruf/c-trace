"""Pull Climate TRACE facility (asset) data for Bangladesh — used by C-Trace as an independent benchmark.

Run on your machine (needs internet):  python scripts/fetch_climate_trace.py --sector manufacturing
Saves raw JSON to data/public/climate_trace_bgd_<sector>.json and a flat CSV if the schema is recognised.

Endpoint follows the Climate TRACE v6 API (https://api.climatetrace.org/v6/). Schemas change between
releases — if parsing fails, inspect the saved JSON and adjust `flatten()`. Alternative: download the
Bangladesh country package from climatetrace.org/data and drop the CSVs into data/public/.
"""
import argparse
import json
import urllib.parse
import urllib.request

from ctrace.config import PUBLIC_DIR

BASE = "https://api.climatetrace.org/v6"


def get(path: str, **params):
    url = f"{BASE}{path}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=60) as r:
        return json.loads(r.read())


def flatten(payload) -> list[dict]:
    items = payload.get("assets", payload) if isinstance(payload, dict) else payload
    rows = []
    for a in items or []:
        rows.append({
            "id": a.get("Id") or a.get("id"), "name": a.get("Name") or a.get("name"),
            "sector": a.get("Sector") or a.get("sector"), "subsector": a.get("AssetType") or a.get("subsector"),
            "lat": (a.get("Centroid") or {}).get("Geometry", [None, None])[1] if a.get("Centroid") else a.get("lat"),
            "lon": (a.get("Centroid") or {}).get("Geometry", [None, None])[0] if a.get("Centroid") else a.get("lon"),
            "emissions_summary": json.dumps(a.get("EmissionsSummary") or a.get("emissions")),
        })
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sector", default="manufacturing")
    ap.add_argument("--limit", type=int, default=1000)
    args = ap.parse_args()
    payload = get("/assets", countries="BGD", sectors=args.sector, limit=args.limit)
    PUBLIC_DIR.mkdir(parents=True, exist_ok=True)
    raw = PUBLIC_DIR / f"climate_trace_bgd_{args.sector}.json"
    raw.write_text(json.dumps(payload, indent=1))
    rows = flatten(payload)
    if rows:
        import pandas as pd
        pd.DataFrame(rows).to_csv(PUBLIC_DIR / f"climate_trace_bgd_{args.sector}.csv", index=False)
    print(f"[C-Trace] saved {raw} ({len(rows)} assets parsed)")


if __name__ == "__main__":
    main()
