# Phase 4 — Factor registry and public data

## Goal
Every ledger row references a versioned factor with a source URL (true since Phase 2; kept under test), the
registry has validity windows and an admin path for the official national grid factor, and the public
datasets C-Trace relies on are fetched by scripts with recorded provenance.

## API facts checked (Nov 2026)
- **NASA POWER** `GET https://power.larc.nasa.gov/api/temporal/monthly/point?parameters=T2M&community=SB&latitude=&longitude=&start=YYYY&end=YYYY&format=JSON`.
  Response `properties.parameter.T2M` keyed `YYYYMM`; month `13` is the annual mean; fill value -999; units °C; source MERRA-2.
  Recent months come back as -999 (data to 2026-09 on 2026-11-20). No key. Repeated requests for one cell should be cached.
- **Climate TRACE** moved to API **v7** (the starter script used v6 `/assets`). `GET https://api.climatetrace.org/v7/sources?gadmId=BGD&subsectors=textiles-leather-apparel&year=YYYY&gas=co2e_100yr&limit=&offset=`
  returns sources with `emissionsQuantity` (t CO2e100), `activity`, `capacity`, `centroid`, `assetType`, `year`. Beta API
  ("keep volume low"); data CC BY 4.0, attribute Climate TRACE.
- **OWID** energy and CO2 CSVs from GitHub (CC BY 4.0).

## Work
- `ctrace/public_data.py`: fetch + parse helpers for all three, and `PROVENANCE.json` (source, URL, retrieved_at, licence).
- `ctrace/weather.py`: monthly T2M and CDD (same definition as the simulator: max(0, T − 24 °C) × days) per
  location, cached per 0.5° cell in `data/public/weather/`; months POWER does not have yet fall back to the
  documented climatology and are labelled as such.
- `scripts/fetch_public_data.py`, `scripts/fetch_weather.py`, `scripts/fetch_climate_trace.py` (v7).
- Migration: `emission_factors.kind` (`default` | `official`), validity windows; `POST /v1/admin/factors` for platform
  admins (`CTRACE_PLATFORM_ADMIN_EMAILS`), audit-logged; the summary reports statutory Scope 2 alongside
  location-based Scope 2 when an official factor covers the period.
- Weather and benchmark data reach the API: `GET /v1/factories/{id}/weather`, `GET /v1/benchmarks/climate-trace`.

## Tests
Recorded real responses (fixtures) parsed offline; live contract tests behind `CTRACE_NETWORK_TESTS=1`; factor
validity selection; official factor admin path (role check, audit, summary); weather fallback labelling.
