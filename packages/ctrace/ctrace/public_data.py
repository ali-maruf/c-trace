"""Public datasets used by C-Trace: fetch, parse, and record provenance.

Every dataset written to `data/public/` gets an entry in `data/public/PROVENANCE.json` with its source,
URL, retrieval time and licence, and the UI and reports cite it from there.

- Our World in Data energy + CO2 (Ember electricity data): Bangladesh grid carbon intensity → Scope 2 factor.
- NASA POWER monthly T2M (MERRA-2): cooling degree days at the factory's location.
- Climate TRACE v7 sources: modelled emissions of Bangladeshi textile and garment facilities, used only as a
  sector benchmark (a signal, never a grade).
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from typing import Any

import pandas as pd

from .config import PUBLIC_DIR

USER_AGENT = "C-Trace/0.1 (carbon MRV; https://github.com/ali-maruf/c-trace)"
PROVENANCE = PUBLIC_DIR / "PROVENANCE.json"

OWID_ENERGY = "https://raw.githubusercontent.com/owid/energy-data/master/owid-energy-data.csv"
OWID_CO2 = "https://raw.githubusercontent.com/owid/co2-data/master/owid-co2-data.csv"
POWER_MONTHLY = "https://power.larc.nasa.gov/api/temporal/monthly/point"
CLIMATE_TRACE = "https://api.climatetrace.org/v7"

LICENCES = {
    "owid": "CC BY 4.0 (Our World in Data); electricity data from Ember (CC BY 4.0)",
    "nasa_power": "NASA POWER data are free and open; cite NASA Langley Research Center POWER Project",
    "climate_trace": "CC BY 4.0 (Climate TRACE); some country-level subsector data from CEDS/EDGAR under their own terms",
}


def http_json(url: str, params: dict[str, Any] | None = None, timeout: float = 90) -> Any:
    q = urllib.parse.urlencode({k: v for k, v in (params or {}).items() if v is not None})
    req = urllib.request.Request(f"{url}?{q}" if q else url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def record_provenance(key: str, *, source: str, url: str, licence: str, files: list[str], notes: str = "") -> None:
    data = json.loads(PROVENANCE.read_text()) if PROVENANCE.exists() else {}
    data[key] = {"source": source, "url": url, "licence": licence, "files": files, "notes": notes,
                 "retrieved_at": datetime.now(UTC).isoformat(timespec="seconds")}
    PUBLIC_DIR.mkdir(parents=True, exist_ok=True)
    PROVENANCE.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def provenance() -> dict:
    return json.loads(PROVENANCE.read_text()) if PROVENANCE.exists() else {}


# ------------------------------------------------------------------ OWID
def fetch_owid() -> None:
    e = pd.read_csv(OWID_ENERGY, storage_options={"User-Agent": USER_AGENT})
    e = e[(e.country == "Bangladesh") & (e.year >= 2000)]
    e[["year", "carbon_intensity_elec", "electricity_generation", "gas_share_elec", "coal_share_elec",
       "oil_share_elec", "renewables_share_elec"]].to_csv(PUBLIC_DIR / "bd_electricity_owid_ember.csv", index=False)
    c = pd.read_csv(OWID_CO2, storage_options={"User-Agent": USER_AGENT})
    c = c[(c.country == "Bangladesh") & (c.year >= 1990)]
    c[["year", "co2", "co2_per_capita", "total_ghg", "methane", "nitrous_oxide", "coal_co2", "gas_co2", "oil_co2",
       "cement_co2"]].to_csv(PUBLIC_DIR / "bd_emissions_owid.csv", index=False)
    record_provenance("owid_energy", source="Our World in Data — energy dataset (Ember electricity data)", url=OWID_ENERGY,
                      licence=LICENCES["owid"], files=["bd_electricity_owid_ember.csv"],
                      notes="carbon_intensity_elec is lifecycle gCO2e/kWh of generation; used as the location-based Scope 2 factor.")
    record_provenance("owid_co2", source="Our World in Data — CO2 and greenhouse gas emissions dataset", url=OWID_CO2,
                      licence=LICENCES["owid"], files=["bd_emissions_owid.csv"], notes="National context only.")


# ------------------------------------------------------------------ NASA POWER
def parse_power_monthly(payload: dict) -> pd.DataFrame:
    """POWER monthly JSON → (period, t2m_c). Drops the annual key (month 13) and fill values."""
    fill = float(payload.get("header", {}).get("fill_value", -999.0))
    t2m = payload["properties"]["parameter"]["T2M"]
    rows = [(f"{k[:4]}-{k[4:]}", float(v)) for k, v in t2m.items() if k[4:] != "13" and float(v) != fill]
    return pd.DataFrame(rows, columns=["period", "t2m_c"]).sort_values("period").reset_index(drop=True)


def fetch_power_monthly(lat: float, lon: float, start_year: int, end_year: int) -> tuple[pd.DataFrame, dict]:
    payload = http_json(POWER_MONTHLY, {"parameters": "T2M", "community": "SB", "latitude": f"{lat:.3f}",
                                        "longitude": f"{lon:.3f}", "start": start_year, "end": end_year, "format": "JSON"})
    return parse_power_monthly(payload), payload.get("header", {})


# ------------------------------------------------------------------ Climate TRACE
BENCHMARK_SUBSECTORS = ("textiles-leather-apparel",)


def parse_climate_trace_sources(items: list[dict]) -> pd.DataFrame:
    rows = [{
        "id": s.get("id"), "name": s.get("name"), "subsector": s.get("subsector"), "asset_type": s.get("assetType"),
        "lat": (s.get("centroid") or {}).get("latitude"), "lon": (s.get("centroid") or {}).get("longitude"),
        "year": s.get("year"), "gas": s.get("gas"), "emissions_t": s.get("emissionsQuantity"),
        "activity": s.get("activity"), "activity_units": s.get("activityUnits"), "capacity": s.get("capacity"),
        "capacity_units": s.get("capacityUnits"),
    } for s in items]
    return pd.DataFrame(rows)


def fetch_climate_trace(year: int, subsectors: tuple[str, ...] = BENCHMARK_SUBSECTORS, page: int = 1000,
                        max_items: int | None = None) -> pd.DataFrame:
    """All BGD sources in the subsectors for one year (paginated, low volume as Climate TRACE asks)."""
    frames, offset = [], 0
    while True:
        items = http_json(f"{CLIMATE_TRACE}/sources", {"gadmId": "BGD", "subsectors": ",".join(subsectors), "year": year,
                                                       "gas": "co2e_100yr", "limit": page, "offset": offset})
        if not items:
            break
        frames.append(parse_climate_trace_sources(items))
        if len(items) < page or (max_items is not None and offset + page >= max_items):
            break
        offset += page
    return pd.concat(frames, ignore_index=True) if frames else parse_climate_trace_sources([])


def climate_trace_benchmark() -> pd.DataFrame | None:
    p = PUBLIC_DIR / "climate_trace_bgd_textiles.csv"
    return pd.read_csv(p) if p.exists() else None
