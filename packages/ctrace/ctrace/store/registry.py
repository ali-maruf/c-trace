"""Factor registry (default + official factors), factory weather, and the Climate TRACE sector benchmark.

The ledger always uses the code-defined default factors (IPCC fuel factors, Ember location-based grid),
mirrored into `emission_factors` by `sync_factors`. An **official** national grid factor, entered by a
C-Trace platform admin with its source URL and validity window, is reported *alongside* as statutory
Scope 2. It never silently replaces the location-based figure.
"""
from __future__ import annotations

import json
from datetime import date

import numpy as np
import pandas as pd

from ..public_data import climate_trace_benchmark, provenance
from ..weather import SOURCE_CLIMATOLOGY, cdd_from_t2m, cell, load_cached
from ..weather import monthly as weather_monthly_from_cache
from .db import Conn


class FactorError(ValueError):
    pass


# ------------------------------------------------------------------ official factors
def add_official_factor(owner_conn: Conn, *, user_id: str, activity: str, region: str, kgco2e_per_unit: float,
                        unit: str, valid_from: date, valid_to: date | None, source: str, source_url: str,
                        notes: str | None = None) -> dict:
    """Owner connection (reference data is read-only to the app role). Overlapping official factors for the
    same activity and region are refused; a correction is a new row with a new validity window."""
    if activity != "grid_electricity":
        raise FactorError("Only the national grid factor can be entered as an official factor")
    if not source_url.startswith(("https://", "http://")):
        raise FactorError("An official factor needs the URL of the publication it comes from")
    if kgco2e_per_unit <= 0 or kgco2e_per_unit > 2:
        raise FactorError("Grid factor must be between 0 and 2 kgCO2e/kWh")
    if valid_to is not None and valid_to < valid_from:
        raise FactorError("valid_to is before valid_from")
    overlap = owner_conn.execute(
        """select id from emission_factors where kind = 'official' and activity = %s and region = %s
           and valid_from <= coalesce(%s::date, 'infinity'::date) and coalesce(valid_to, 'infinity'::date) >= %s""",
        (activity, region, valid_to, valid_from)).fetchone()
    if overlap:
        raise FactorError(f"Overlaps official factor {overlap['id']}; close its validity window first")
    n = owner_conn.execute("select count(*) n from emission_factors where kind = 'official' and activity = %s and region = %s",
                           (activity, region)).fetchone()
    fid = f"official-{region.lower()}-grid-{valid_from:%Y%m%d}-v{int(n['n']) + 1 if n else 1}"
    row = owner_conn.execute(
        """insert into emission_factors (id, activity, region, year, unit, scope, kgco2e_per_unit, breakdown, source, source_url,
                                         version, valid_from, valid_to, notes, kind, entered_by)
           values (%s, %s, %s, %s, %s, 2, %s, %s, %s, %s, 'v1', %s, %s, %s, 'official', %s) returning *""",
        (fid, activity, region, valid_from.year, unit, kgco2e_per_unit, json.dumps({"CO2e": kgco2e_per_unit}), source,
         source_url, valid_from, valid_to, notes, user_id)).fetchone()
    assert row is not None
    return row


def official_grid_factors(conn: Conn, region: str = "BGD") -> list[dict]:
    return conn.execute("select * from emission_factors where kind = 'official' and activity = 'grid_electricity' "
                        "and region = %s order by valid_from", (region,)).fetchall()


def official_factor_for(factors: list[dict], period: str) -> dict | None:
    d = date(int(period[:4]), int(period[5:7]), 1)
    return next((f for f in factors if f["valid_from"] <= d and (f["valid_to"] is None or d <= f["valid_to"])), None)


def statutory_scope2(conn: Conn, factory_id: str, periods: list[str]) -> dict | None:
    """Scope 2 recomputed with the official grid factor for the months it covers (None if no official factor)."""
    factors = official_grid_factors(conn)
    if not factors:
        return None
    grid = conn.execute("select period, sum(quantity) kwh from ledger_entries where factory_id = %s and activity = 'grid_electricity' "
                        "group by period", (factory_id,)).fetchall()
    total, covered, used = 0.0, [], set()
    for r in grid:
        if r["period"] not in periods:
            continue
        f = official_factor_for(factors, r["period"])
        if f is None:
            continue
        total += float(r["kwh"]) * float(f["kgco2e_per_unit"]) / 1000
        covered.append(r["period"])
        used.add(f["id"])
    if not covered:
        return None
    return {"scope2_statutory_tco2e": round(total, 4), "months_covered": len(covered), "factor_ids": sorted(used),
            "note": "Official national grid factor, reported alongside location-based Scope 2 for the months it covers."}


# ------------------------------------------------------------------ weather
def sync_weather(owner_conn: Conn, lat: float, lon: float) -> int:
    """Owner connection. Copy the cached NASA POWER series for this location's cell into `weather_monthly`."""
    df = load_cached(lat, lon)
    clat, clon = cell(lat, lon)
    with owner_conn.cursor() as cur:
        cur.executemany(
            "insert into weather_monthly (lat, lon, period, t2m_c, cdd, source) values (%s, %s, %s, %s, %s, %s) "
            "on conflict (lat, lon, period) do update set t2m_c = excluded.t2m_c, cdd = excluded.cdd, source = excluded.source, "
            "retrieved_at = now()",
            [(clat, clon, r.period, float(r.t2m_c), cdd_from_t2m(r.period, float(r.t2m_c)), "NASA POWER monthly T2M (MERRA-2)")
             for r in df.itertuples()])
    return len(df)


def factory_weather(conn: Conn, factory: dict, periods: list[str]) -> pd.DataFrame:
    """period, t2m_c, cdd, source. Database first (POWER), climatology for months POWER has not published."""
    if factory.get("lat") is None or factory.get("lon") is None:
        return weather_monthly_from_cache(None, None, periods)
    clat, clon = cell(float(factory["lat"]), float(factory["lon"]))
    have = {r["period"]: r for r in conn.execute("select period, t2m_c, cdd, source from weather_monthly where lat = %s and lon = %s",
                                                 (clat, clon))}
    fallback = weather_monthly_from_cache(None, None, periods).set_index("period")
    rows = []
    for p in periods:
        r = have.get(p)
        if r is not None:
            rows.append({"period": p, "t2m_c": float(r["t2m_c"]), "cdd": float(r["cdd"]), "source": r["source"]})
        else:
            fb = fallback.loc[p]
            rows.append({"period": p, "t2m_c": float(fb.t2m_c), "cdd": float(fb.cdd), "source": SOURCE_CLIMATOLOGY})
    return pd.DataFrame(rows, columns=["period", "t2m_c", "cdd", "source"])


# ------------------------------------------------------------------ Climate TRACE benchmark
def benchmark(scope1_tco2e_per_year: float | None) -> dict | None:
    """Where a factory's annualised Scope 1 sits among Climate TRACE's modelled estimates for Bangladeshi textile
    and garment facilities. A signal for context, never a grade: Climate TRACE models emissions from capacity."""
    ct = climate_trace_benchmark()
    if ct is None or ct.empty:
        return None
    year = int(ct.year.max())
    e = ct[(ct.year == year) & ct.emissions_t.notna()].emissions_t.to_numpy(float)
    q = {f"p{k}": round(float(np.percentile(e, k)), 1) for k in (10, 25, 50, 75, 90)}
    pct = None if scope1_tco2e_per_year is None else round(float((e < scope1_tco2e_per_year).mean() * 100), 1)
    prov = provenance().get("climate_trace_bgd_textiles", {})
    return {"year": year, "n_facilities": int(len(e)), "quantiles_tco2e": q, "factory_scope1_tco2e_per_year": scope1_tco2e_per_year,
            "factory_percentile": pct, "subsector": "textiles-leather-apparel", "gas": "co2e_100yr",
            "source": prov.get("source", "Climate TRACE"), "source_url": prov.get("url"), "licence": prov.get("licence"),
            "retrieved_at": prov.get("retrieved_at"),
            "caveat": "Climate TRACE figures are modelled from facility capacity and typical utilisation, mostly on-site "
                      "combustion. Compare with the factory's Scope 1 only, as context; it says nothing about data quality."}
