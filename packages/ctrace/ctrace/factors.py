"""Emission factors used by C-Trace, each with explicit provenance.

Fuel factors: IPCC 2006 Guidelines, Vol. 2 (Stationary Combustion) default values.
GWPs: IPCC AR6 100-year (CH4 fossil 29.8, N2O 273).
Grid electricity: Bangladesh generation carbon intensity from Ember via Our World in Data
(data/public/bd_electricity_owid_ember.csv). NOTE: Ember intensity is lifecycle-based; for
statutory reporting replace with the official national grid emission factor (location-based).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from functools import lru_cache

import pandas as pd

from .config import PUBLIC_DIR

GWP_AR6 = {"CO2": 1.0, "CH4": 29.8, "N2O": 273.0}


FACTOR_VERSION = "v1"
IPCC_2006_VOL2_URL = "https://www.ipcc-nggip.iges.or.jp/public/2006gl/vol2.html"
OWID_ENERGY_URL = "https://github.com/owid/energy-data"


@dataclass(frozen=True)
class EmissionFactor:
    factor_id: str        # stable key, e.g. "ipcc2006-natural_gas-v1", "ember-bgd-2024-v1"
    activity: str
    unit: str
    scope: int
    kgco2e_per_unit: float
    breakdown: dict
    source: str
    source_url: str
    notes: str
    version: str = FACTOR_VERSION
    region: str = "BGD"
    year: int | None = None   # data year for year-specific factors (grid); None = not year-specific

    def as_dict(self) -> dict:
        return asdict(self)


def _fuel_factor(ncv_gj_per_unit: float, co2_kg_tj: float, ch4_kg_tj: float, n2o_kg_tj: float):
    tj = ncv_gj_per_unit / 1000.0
    co2 = co2_kg_tj * tj
    ch4 = ch4_kg_tj * tj * GWP_AR6["CH4"]
    n2o = n2o_kg_tj * tj * GWP_AR6["N2O"]
    return co2 + ch4 + n2o, {"CO2": round(co2, 5), "CH4_co2e": round(ch4, 6), "N2O_co2e": round(n2o, 6)}


# Natural gas: NCV ~0.036 GJ/m3 (IPCC default 48 TJ/Gg x ~0.75 kg/m3). Bangladesh gas is
# methane-rich; replace with the supplier's declared calorific value when available.
_NG_TOTAL, _NG_BREAK = _fuel_factor(0.036, 56_100, 1.0, 0.1)
# Diesel / gas oil: NCV 43 TJ/Gg x 0.84 kg/L = 0.03612 GJ/L.
_DSL_TOTAL, _DSL_BREAK = _fuel_factor(0.03612, 74_100, 3.0, 0.6)

FUEL_FACTORS = {
    "natural_gas": EmissionFactor(
        factor_id=f"ipcc2006-natural_gas-{FACTOR_VERSION}", region="GLOBAL", source_url=IPCC_2006_VOL2_URL,
        activity="natural_gas", unit="m3", scope=1,
        kgco2e_per_unit=round(_NG_TOTAL, 5), breakdown=_NG_BREAK,
        source="IPCC 2006 GL Vol.2 Ch.2 defaults (CO2 56,100; CH4 1; N2O 0.1 kg/TJ); AR6 GWP100",
        notes="Assumes NCV 0.036 GJ/m3. Covers boilers, thermic heaters and captive gas generators.",
    ),
    "diesel": EmissionFactor(
        factor_id=f"ipcc2006-diesel-{FACTOR_VERSION}", region="GLOBAL", source_url=IPCC_2006_VOL2_URL,
        activity="diesel", unit="litre", scope=1,
        kgco2e_per_unit=round(_DSL_TOTAL, 5), breakdown=_DSL_BREAK,
        source="IPCC 2006 GL Vol.2 Ch.2 defaults (CO2 74,100; CH4 3; N2O 0.6 kg/TJ); AR6 GWP100",
        notes="Assumes NCV 0.03612 GJ/L. Stationary diesel generators.",
    ),
}

ENERGY_GJ_PER_UNIT = {"natural_gas": 0.036, "diesel": 0.03612, "grid_electricity": 0.0036}


@lru_cache(maxsize=1)
def _grid_table() -> pd.DataFrame:
    df = pd.read_csv(PUBLIC_DIR / "bd_electricity_owid_ember.csv")
    return df.dropna(subset=["carbon_intensity_elec"]).sort_values("year")


def grid_factor(year: int) -> EmissionFactor:
    """Location-based Scope 2 factor for Bangladesh. Uses the latest available year <= `year`."""
    t = _grid_table()
    eligible = t[t.year <= year]
    row = eligible.iloc[-1] if len(eligible) else t.iloc[0]
    g_per_kwh = float(row.carbon_intensity_elec)
    return EmissionFactor(
        factor_id=f"ember-bgd-{int(row.year)}-{FACTOR_VERSION}", year=int(row.year), source_url=OWID_ENERGY_URL,
        activity="grid_electricity", unit="kWh", scope=2,
        kgco2e_per_unit=round(g_per_kwh / 1000.0, 5),
        breakdown={"CO2e": round(g_per_kwh / 1000.0, 5)},
        source=f"Ember via Our World in Data, Bangladesh {int(row.year)} ({g_per_kwh:.1f} gCO2e/kWh)",
        notes="Lifecycle generation intensity; swap for official national grid EF for statutory use.",
    )


def all_factors() -> list[EmissionFactor]:
    """Every factor C-Trace can use: the fuel factors plus one grid factor per available data year."""
    return list(FUEL_FACTORS.values()) + [grid_factor(int(y)) for y in _grid_table().year]


def factor_for(activity: str, year: int) -> EmissionFactor:
    if activity == "grid_electricity":
        return grid_factor(year)
    if activity in FUEL_FACTORS:
        return FUEL_FACTORS[activity]
    raise KeyError(f"C-Trace has no emission factor for activity '{activity}'")
