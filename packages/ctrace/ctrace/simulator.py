"""C-Trace factory energy simulator.

Purpose: Bangladesh has no open, factory-level monthly energy dataset. To train and *test*
the C-Trace verification models before real pilot data arrives, we simulate a fleet of RMG
factories with a transparent, physics-informed process model. Every assumption is listed
below and in models/MODEL_CARD.md. Replace with pilot-factory data as soon as it exists:
the training script accepts any CSV with the same columns.

Physical structure (per factory, per month):
  electricity demand = base load (people, lighting, HVAC) + process load (pieces × kWh/piece)
                       + cooling load (cooling degree days)
  supplied by        = rooftop solar → captive gas generator share → grid; grid outages
                       are covered by diesel generators
  gas                = boiler/thermic steam for wet processing & ironing + captive generation
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# Approximate Dhaka monthly mean temperature climatology (°C). Assumption for CDD.
DHAKA_TEMP_C = [19.1, 22.3, 26.4, 28.7, 29.0, 29.2, 29.0, 29.1, 28.8, 27.6, 24.2, 20.4]
DAYS = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
CDD_BASE_C = 24.0

# Months with Eid holidays (production dips). Eid ul-Fitr & Eid ul-Adha, approximate.
EID_MONTHS = {"2022-05", "2022-07", "2023-04", "2023-06", "2024-04", "2024-06", "2025-03", "2025-06", "2026-03", "2026-05"}

# Fleet locations for simulation with real weather (Verifier v2): NASA POWER 0.5° cells covering the main RMG
# clusters. (name, lat, lon, share of factories). Shares are an assumption, roughly where RMG factories are.
FLEET_LOCATIONS = [
    ("Gazipur / Savar", 24.0, 90.5, 0.40),
    ("Dhaka / Narayanganj", 23.5, 90.5, 0.30),
    ("Chattogram", 22.5, 92.0, 0.20),
    ("Mymensingh / Bhaluka", 24.5, 90.5, 0.10),
]

FACTORY_TYPES = {
    #                share  kWh/piece  gas m3/piece (wet)  gas m3/piece (dry)  p(wet)
    "knit_composite": (0.40, 0.45, 0.30, 0.03, 1.00),
    "woven":          (0.30, 0.35, 0.12, 0.02, 0.40),
    "denim_wash":     (0.15, 0.60, 0.25, 0.03, 1.00),
    "sweater":        (0.15, 0.50, 0.10, 0.02, 0.20),
}

GAS_GEN_M3_PER_KWH = 0.263   # gas engine ~38% electrical efficiency at 0.036 GJ/m3
DIESEL_L_PER_KWH = 0.28      # small diesel gensets
SOLAR_KWH_PER_KWP_MONTH = 110.0
SOLAR_SEASON = [1.05, 1.10, 1.15, 1.10, 1.00, 0.80, 0.75, 0.80, 0.85, 1.00, 1.05, 1.05]  # monsoon dip
OUTAGE_SEASON = [0.03, 0.03, 0.06, 0.10, 0.12, 0.10, 0.08, 0.08, 0.08, 0.06, 0.04, 0.03]


def cdd(period: str, rng: np.random.Generator | None = None) -> float:
    m = int(period[5:7]) - 1
    t = DHAKA_TEMP_C[m] + (rng.normal(0, 0.6) if rng is not None else 0.0)
    return max(0.0, t - CDD_BASE_C) * DAYS[m]


def periods(start: str, end: str) -> list[str]:
    return [p.strftime("%Y-%m") for p in pd.period_range(start, end, freq="M")]


def sample_factory(rng: np.random.Generator, fid: str, with_location: bool = False) -> dict:
    """A random factory. The location is drawn last and only for real-weather fleets, so the v1 (climatology)
    fleet's random stream, and therefore its training data, are unchanged."""
    types = list(FACTORY_TYPES)
    ftype = rng.choice(types, p=[FACTORY_TYPES[t][0] for t in types])
    _, e_int, g_wet, g_dry, p_wet = FACTORY_TYPES[ftype]
    workers = int(np.clip(rng.lognormal(np.log(2500), 0.6), 400, 12000))
    has_solar = rng.random() < 0.35
    f = {
        "factory_id": fid,
        "factory_type": ftype,
        "workers": workers,
        "has_wet_processing": int(rng.random() < p_wet),
        "captive_share": float(rng.uniform(0.3, 0.9)) if rng.random() < 0.45 else 0.0,
        "solar_kwp_final": float(rng.uniform(100, 2000)) if has_solar else 0.0,
        "solar_start_idx": int(rng.integers(0, 30)) if has_solar else 10**6,
        "capacity_pieces": workers * float(rng.uniform(180, 380)),
        "e_mult": float(rng.lognormal(0, 0.18)),   # efficiency differences between factories
        "g_mult": float(rng.lognormal(0, 0.22)),
        "base_kwh_per_worker": float(rng.uniform(25, 55)),
        "cool_kwh_per_cdd_worker": float(rng.uniform(0.15, 0.45)),
        "e_int": e_int, "g_wet": g_wet, "g_dry": g_dry,
    }
    if with_location:
        loc = FLEET_LOCATIONS[int(rng.choice(len(FLEET_LOCATIONS), p=[x[3] for x in FLEET_LOCATIONS]))]
        f.update(location=loc[0], lat=loc[1], lon=loc[2])
    return f


def simulate_month(f: dict, period: str, idx: int, rng: np.random.Generator, t2m: float | None = None,
                   gas_efficiency_gain: float = 0.0, solar_override_kwp: float | None = None) -> dict:
    m = int(period[5:7]) - 1
    util = np.clip(rng.normal(0.82, 0.07), 0.45, 1.0) * (0.72 if period in EID_MONTHS else 1.0)
    pieces = f["capacity_pieces"] * util
    # v1: Dhaka climatology + noise; v2: the cell's real monthly mean (NASA POWER) + a little site-level variation
    c = cdd(period, rng) if t2m is None else max(0.0, t2m + rng.normal(0, 0.3) - CDD_BASE_C) * DAYS[m]
    demand_kwh = (
        f["workers"] * f["base_kwh_per_worker"]
        + pieces * f["e_int"] * f["e_mult"]
        + c * f["workers"] * f["cool_kwh_per_cdd_worker"]
    ) * rng.lognormal(0, 0.04)
    kwp = solar_override_kwp if solar_override_kwp is not None else (f["solar_kwp_final"] if idx >= f["solar_start_idx"] else 0.0)
    solar = min(demand_kwh * 0.6, kwp * SOLAR_KWH_PER_KWP_MONTH * SOLAR_SEASON[m] * rng.lognormal(0, 0.05))
    rest = demand_kwh - solar
    captive_kwh = rest * f["captive_share"]
    grid_side = rest - captive_kwh
    outage = np.clip(OUTAGE_SEASON[m] * rng.lognormal(0, 0.3), 0, 0.3)
    # captive factories barely need diesel; grid-only factories cover outages with diesel
    diesel_kwh = grid_side * outage * (0.25 if f["captive_share"] > 0 else 1.0)
    grid_kwh = grid_side - diesel_kwh
    g_int = f["g_wet"] if f["has_wet_processing"] else f["g_dry"]
    boiler_m3 = pieces * g_int * f["g_mult"] * (1 - gas_efficiency_gain) * rng.lognormal(0, 0.05)
    gas_m3 = boiler_m3 + captive_kwh * GAS_GEN_M3_PER_KWH
    return {
        "factory_id": f["factory_id"], "period": period, "month": m + 1,
        "factory_type": f["factory_type"], "workers": f["workers"],
        "has_wet_processing": f["has_wet_processing"], "captive_gas_gen": int(f["captive_share"] > 0),
        "solar_kwp": round(kwp, 1), "pieces": round(pieces), "cdd": round(c, 1),
        "gas_m3": round(gas_m3, 1), "grid_kwh": round(grid_kwh, 1), "diesel_l": round(diesel_kwh * DIESEL_L_PER_KWH, 1),
    }


def simulate_fleet(n_factories: int = 600, start: str = "2023-01", end: str = "2025-12", seed: int = 42,
                   real_weather: bool = False) -> pd.DataFrame:
    """Simulated fleet. With `real_weather`, each factory's cooling load follows NASA POWER T2M at its location
    (cached series in data/public/weather/); otherwise the Dhaka climatology (v1)."""
    temps: dict[tuple[float, float], dict[str, float]] = {}
    if real_weather:
        from .weather import load_cached
        for _, lat, lon, _ in FLEET_LOCATIONS:
            series = load_cached(lat, lon).set_index("period")["t2m_c"].to_dict()
            missing = [p for p in periods(start, end) if p not in series]
            if missing:
                raise ValueError(f"No NASA POWER data for ({lat}, {lon}) in {missing[:3]}…; run scripts/fetch_weather.py")
            temps[(lat, lon)] = series
    rng = np.random.default_rng(seed)
    rows = []
    ps = periods(start, end)
    for i in range(n_factories):
        f = sample_factory(rng, f"F{i:04d}", with_location=real_weather)
        for idx, p in enumerate(ps):
            t = temps[(f["lat"], f["lon"])][p] if real_weather else None
            row = simulate_month(f, p, idx, rng, t2m=t)
            row["location"] = f.get("location", "Dhaka climatology")
            rows.append(row)
    return pd.DataFrame(rows)
