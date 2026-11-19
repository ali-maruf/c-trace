"""Fetch NASA POWER monthly temperature for a factory location (needs internet).

    uv run python scripts/fetch_weather.py                       # the demo factory (Gazipur)
    uv run python scripts/fetch_weather.py --lat 23.8 --lon 90.4 --start 2022 --end 2026
Cached per 0.5° cell in data/public/weather/ with provenance; C-Trace reads the cache, never the network,
when scoring or reporting.
"""
import argparse
from datetime import date

from ctrace.store.seed import DEMO_LAT, DEMO_LON
from ctrace.weather import cache_path, refresh


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lat", type=float, default=DEMO_LAT)
    ap.add_argument("--lon", type=float, default=DEMO_LON)
    ap.add_argument("--start", type=int, default=2022)
    ap.add_argument("--end", type=int, default=date.today().year)
    a = ap.parse_args()
    df = refresh(a.lat, a.lon, a.start, a.end)
    print(f"[C-Trace] {len(df)} months ({df.period.min()} … {df.period.max()}) → {cache_path(a.lat, a.lon)}")


if __name__ == "__main__":
    main()
