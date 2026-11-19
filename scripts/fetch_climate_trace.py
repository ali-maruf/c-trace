"""Fetch Climate TRACE (API v7) sources for Bangladeshi textile & garment facilities (needs internet).

    uv run python scripts/fetch_climate_trace.py --years 2025
Writes data/public/climate_trace_bgd_textiles.csv with provenance. C-Trace uses it only as a sector
benchmark: where a factory's own verified total sits in the distribution of modelled estimates. It never
grades a factory, and the UI shows the distribution, not other facilities' names.
Climate TRACE asks API users to keep volume low; one request per year per page.
"""
import argparse

import pandas as pd

from ctrace.config import PUBLIC_DIR
from ctrace.public_data import BENCHMARK_SUBSECTORS, CLIMATE_TRACE, LICENCES, fetch_climate_trace, record_provenance


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, nargs="+", default=[2025])
    years = ap.parse_args().years
    frames = []
    for y in years:
        df = fetch_climate_trace(y)
        print(f"[C-Trace] {y}: {len(df)} sources")
        frames.append(df)
    out = pd.concat(frames, ignore_index=True)
    out.to_csv(PUBLIC_DIR / "climate_trace_bgd_textiles.csv", index=False)
    record_provenance("climate_trace_bgd_textiles", source="Climate TRACE — emissions sources (API v7)",
                      url=f"{CLIMATE_TRACE}/sources?gadmId=BGD&subsectors={','.join(BENCHMARK_SUBSECTORS)}&gas=co2e_100yr",
                      licence=LICENCES["climate_trace"], files=["climate_trace_bgd_textiles.csv"],
                      notes=f"Years {years}; modelled estimates (activity from capacity × utilisation), t CO2e (100-yr GWP).")


if __name__ == "__main__":
    main()
