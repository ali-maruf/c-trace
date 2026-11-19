"""C-Trace test suite:  pytest -q"""
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ctrace import config  # noqa: E402
from ctrace.calculate import build_ledger  # noqa: E402
from ctrace.extraction import extract_pdf_rules, normalize_period, parse_text  # noqa: E402
from ctrace.factors import FUEL_FACTORS, grid_factor  # noqa: E402
from ctrace.pipeline import run  # noqa: E402
from ctrace.reduction import prove_reduction  # noqa: E402
from ctrace.report import build_facts, number_guard, template_narrative  # noqa: E402
from ctrace.verify import Verifier  # noqa: E402

BILLS = sorted(config.BILLS_DIR.glob("*.pdf"))


def test_fuel_factors_match_ipcc_defaults():
    # ~2.02 kgCO2e/m3 natural gas and ~2.68 kgCO2e/L diesel
    assert FUEL_FACTORS["natural_gas"].kgco2e_per_unit == pytest.approx(2.022, abs=0.01)
    assert FUEL_FACTORS["diesel"].kgco2e_per_unit == pytest.approx(2.688, abs=0.01)


def test_grid_factor_uses_latest_available_year():
    f = grid_factor(2030)
    assert 0.5 < f.kgco2e_per_unit < 0.9
    assert "Ember" in f.source


@pytest.mark.parametrize("text,expected", [("Jan 2025", "2025-01"), ("2026-07", "2026-07"), ("09/2025", "2025-09"),
                                           ("September, 2024", "2024-09")])
def test_normalize_period(text, expected):
    assert normalize_period(text) == expected


def test_parse_text_handles_units_and_tariff():
    txt = "ELECTRICITY BILL\nBill No: E-1\nBilling Period: Mar 2026\nConsumption: 200.5 MWh\nTotal Amount Payable (BDT): 2,305,750.00"
    r = parse_text(txt, "x.pdf", "abc")
    assert r.activity == "grid_electricity" and r.unit == "kWh" and r.quantity == 200500
    assert not r.issues


def test_tariff_check_catches_misread_quantity():
    bad = extract_pdf_rules(config.BILLS_DIR / "2025-09_natural_gas.pdf")
    assert any("tariff" in i.lower() for i in bad.issues)


def test_ledger_is_traceable_and_correct():
    recs = pd.DataFrame([{"period": "2026-01", "activity": "natural_gas", "quantity": 1000.0, "unit": "m3",
                          "amount_bdt": 31500, "doc_name": "a.pdf", "doc_sha256": "f" * 64, "bill_no": "1", "supplier": "s"}])
    led = build_ledger(recs)
    assert led.loc[0, "tco2e"] == pytest.approx(1000 * FUEL_FACTORS["natural_gas"].kgco2e_per_unit / 1000, abs=1e-4)
    assert led.loc[0, "trace_id"].startswith("CT-2026-01-NAT-ffffffff")


@pytest.fixture(scope="module")
def demo():
    if not (config.MODELS_DIR / "ctrace_verifier.joblib").exists():
        pytest.skip("train the model first")
    recs = pd.DataFrame([extract_pdf_rules(p).as_dict() for p in BILLS])
    prod = pd.read_csv(config.SAMPLES_DIR / "production.csv")
    prof = json.loads((config.SAMPLES_DIR / "factory_profile.json").read_text())
    return recs, prod, prof, run(recs, prod, prof, Verifier.load())


def test_demo_flags_planted_errors_only(demo):
    *_, out = demo
    low = set(out["verification"].query("confidence == 'Low'").period)
    assert low == {"2025-09", "2026-07"}


def test_correction_suggestion(demo):
    *_, out = demo
    s = out["suggestions"][0]
    assert s["period"] == "2025-09" and s["suggested_quantity"] == pytest.approx(10 * s["printed_quantity"], rel=0.01)


def test_reduction_is_verified(demo):
    recs, prod, prof, out = demo
    m = out["monthly"].copy()
    m.loc[m.period == "2025-09", "gas_m3"] = out["suggestions"][0]["suggested_quantity"]
    _, res = prove_reduction(m, "2025-12", "2026-01")
    gas = next(r for r in res if r.activity == "natural_gas")
    assert gas.verified and 0.08 < gas.avoided / gas.predicted < 0.16  # planted ~12% boiler saving


def test_number_guard_blocks_invented_numbers(demo):
    recs, prod, prof, out = demo
    facts = build_facts(prof, out["summary"], out["ledger"], out["verification"], None, "2026-01", "2026-10")
    assert number_guard(template_narrative("buyer", facts), facts) == []
    assert number_guard("Emissions fell to 1234.5 tCO2e", facts) == ["1234.5"]
