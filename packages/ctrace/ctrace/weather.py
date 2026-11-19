"""Monthly weather at a factory: NASA POWER T2M → cooling degree days.

CDD uses the same definition as the C-Trace simulator and Verifier features, so real and simulated
weather are comparable: CDD(month) = max(0, mean T2M − 24 °C) × days in month.

Readings are cached per 0.5° grid cell (POWER's MERRA-2 grid is coarser than that) in
`data/public/weather/`. Months POWER has not published yet fall back to the Dhaka climatology in
`ctrace.simulator`, and every row says which source it came from.
"""
from __future__ import annotations

import calendar
from pathlib import Path

import pandas as pd

from .config import PUBLIC_DIR
from .simulator import CDD_BASE_C, DHAKA_TEMP_C

WEATHER_DIR = PUBLIC_DIR / "weather"
SOURCE_POWER = "NASA POWER monthly T2M (MERRA-2)"
SOURCE_CLIMATOLOGY = "Dhaka monthly climatology (C-Trace assumption; POWER value not yet published)"


def cell(lat: float, lon: float) -> tuple[float, float]:
    return round(lat * 2) / 2, round(lon * 2) / 2


def cdd_from_t2m(period: str, t2m_c: float) -> float:
    y, m = int(period[:4]), int(period[5:7])
    return round(max(0.0, t2m_c - CDD_BASE_C) * calendar.monthrange(y, m)[1], 1)


def cache_path(lat: float, lon: float) -> Path:
    clat, clon = cell(lat, lon)
    return WEATHER_DIR / f"power_{clat:+.1f}_{clon:+.1f}.csv"


def load_cached(lat: float, lon: float) -> pd.DataFrame:
    p = cache_path(lat, lon)
    return pd.read_csv(p, dtype={"period": str}) if p.exists() else pd.DataFrame(columns=["period", "t2m_c"])


def refresh(lat: float, lon: float, start_year: int, end_year: int) -> pd.DataFrame:
    """Fetch from NASA POWER (network) and update the cell's cache."""
    from .public_data import fetch_power_monthly, record_provenance

    clat, clon = cell(lat, lon)
    df, _ = fetch_power_monthly(clat, clon, start_year, end_year)
    old = load_cached(lat, lon)
    merged = pd.concat([old, df]).drop_duplicates("period", keep="last").sort_values("period").reset_index(drop=True)
    WEATHER_DIR.mkdir(parents=True, exist_ok=True)
    merged.to_csv(cache_path(lat, lon), index=False)
    record_provenance(f"nasa_power_{clat:+.1f}_{clon:+.1f}", source=SOURCE_POWER,
                      url="https://power.larc.nasa.gov/api/temporal/monthly/point",
                      licence="NASA POWER data are free and open; cite NASA Langley Research Center POWER Project",
                      files=[str(cache_path(lat, lon).relative_to(PUBLIC_DIR))],
                      notes=f"T2M monthly mean at grid cell ({clat}, {clon}); {len(merged)} months.")
    return merged


def monthly(lat: float | None, lon: float | None, periods: list[str]) -> pd.DataFrame:
    """period, t2m_c, cdd, source for each requested month, from cache, else climatology."""
    have = load_cached(lat, lon).set_index("period")["t2m_c"].to_dict() if lat is not None and lon is not None else {}
    rows = []
    for p in periods:
        if p in have:
            t, src = float(have[p]), SOURCE_POWER
        else:
            t, src = DHAKA_TEMP_C[int(p[5:7]) - 1], SOURCE_CLIMATOLOGY
        rows.append({"period": p, "t2m_c": round(t, 2), "cdd": cdd_from_t2m(p, t), "source": src})
    return pd.DataFrame(rows, columns=["period", "t2m_c", "cdd", "source"])
