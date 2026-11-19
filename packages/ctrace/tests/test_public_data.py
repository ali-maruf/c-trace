"""Public data parsers against recorded real responses, plus live contract tests (CTRACE_NETWORK_TESTS=1)."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from ctrace.public_data import fetch_climate_trace, fetch_power_monthly, parse_climate_trace_sources, parse_power_monthly
from ctrace.weather import SOURCE_CLIMATOLOGY, SOURCE_POWER, cdd_from_t2m, cell, monthly

FIX = Path(__file__).parent / "fixtures"
network = pytest.mark.skipif(os.getenv("CTRACE_NETWORK_TESTS") != "1", reason="live API contract test; set CTRACE_NETWORK_TESTS=1")


def test_power_monthly_recorded_response():
    payload = json.loads((FIX / "nasa_power_monthly_t2m_24.0_90.42.json").read_text())
    df = parse_power_monthly(payload)
    assert payload["header"]["fill_value"] == -999.0 and payload["parameters"]["T2M"]["units"] == "C"
    assert not df.period.str.endswith("-13").any()          # annual mean dropped
    assert (df.t2m_c > -50).all()                           # fill values dropped
    assert df.set_index("period").t2m_c["2023-01"] == pytest.approx(17.99)


def test_cdd_definition_matches_simulator():
    assert cdd_from_t2m("2025-07", 28.39) == pytest.approx(round(4.39 * 31, 1))
    assert cdd_from_t2m("2025-01", 18.23) == 0.0
    assert cdd_from_t2m("2024-02", 25.0) == pytest.approx(29.0)  # leap year


def test_weather_falls_back_to_labelled_climatology():
    lat, lon = 24.0, 90.42  # demo factory cell: committed cache runs to 2026-09
    df = monthly(lat, lon, ["2026-09", "2026-10"]).set_index("period")
    assert df.loc["2026-09", "source"] == SOURCE_POWER and df.loc["2026-10", "source"] == SOURCE_CLIMATOLOGY
    assert cell(24.12, 90.38) == (24.0, 90.5)


def test_climate_trace_v7_recorded_response():
    items = json.loads((FIX / "climatetrace_v7_sources_bgd_textiles.json").read_text())
    df = parse_climate_trace_sources(items)
    assert set(df.subsector) == {"textiles-leather-apparel"} and (df.gas == "co2e_100yr").all()
    assert df.emissions_t.gt(0).all() and df.lat.between(20, 27).all() and df.lon.between(88, 93).all()


@network
def test_live_power_contract():
    df, header = fetch_power_monthly(24.0, 90.5, 2024, 2024)
    assert header["fill_value"] == -999.0 and len(df) == 12


@network
def test_live_climate_trace_contract():
    df = fetch_climate_trace(2025, page=5, max_items=5)
    assert len(df) > 0 and {"emissions_t", "lat", "lon", "year"} <= set(df.columns)
