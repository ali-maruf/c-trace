"""C-Trace Verifier v2: registry, acceptance on the demo factory, explanations, bands, pilot schema."""
from __future__ import annotations

import importlib.util
import json

import pandas as pd
import pytest

from ctrace import config
from ctrace.extraction import extract_pdf_rules
from ctrace.pipeline import run
from ctrace.verify import DRIVER_GROUPS, Verifier
from ctrace.weather import monthly

pytestmark = pytest.mark.skipif(not config.verifier_path("v2").exists(), reason="train the model first (make train)")


@pytest.fixture(scope="module")
def demo_inputs():
    recs = pd.DataFrame([extract_pdf_rules(p).as_dict() for p in sorted(config.BILLS_DIR.glob("*.pdf"))])
    prod = pd.read_csv(config.SAMPLES_DIR / "production.csv")
    prof = json.loads((config.SAMPLES_DIR / "factory_profile.json").read_text())
    return recs, prod, prof, monthly(24.0, 90.42, sorted(prod.period))


def test_registry_layout_and_pinned_version():
    for version in ("v1", "v2"):
        d = config.verifier_dir(version)
        assert {"model.joblib", "metrics.json", "MODEL_CARD.md"} <= {p.name for p in d.iterdir()}
    assert config.CTRACE_VERIFIER_VERSION == "v2" and Verifier.load().version == "v2"
    assert Verifier.load(config.verifier_path("v1")).version == "v1"   # old pickle still loads


@pytest.mark.parametrize("version", ["v1", "v2"])
@pytest.mark.parametrize("real_weather", [False, True])
def test_demo_flags_exactly_the_planted_problems(demo_inputs, version, real_weather):
    recs, prod, prof, w = demo_inputs
    g = run(recs, prod, prof, Verifier.load(config.verifier_path(version)), weather=w if real_weather else None)["verification"]
    assert set(g[g.confidence == "Low"].period) == {"2025-09", "2026-07"}


def test_drivers_are_exact_shapley_and_bands_contain_expected(demo_inputs):
    recs, prod, prof, w = demo_inputs
    g = run(recs, prod, prof, Verifier.load(), weather=w, explain=True)["verification"]
    for r in g.itertuples():
        for t in ("gas_m3", "grid_kwh", "diesel_l"):
            d = getattr(r, f"drivers_{t}")
            assert set(d) == {"typical", *DRIVER_GROUPS}
            assert d["typical"] + sum(d[k] for k in DRIVER_GROUPS) == pytest.approx(getattr(r, f"expected_{t}"), abs=0.5)
            assert getattr(r, f"expected_lo_{t}") <= getattr(r, f"expected_{t}") <= getattr(r, f"expected_hi_{t}")
    jan = g[g.period == "2026-01"].iloc[0]["drivers_grid_kwh"]
    assert jan["weather"] < 0          # no cooling load in January


def test_released_metrics_meet_the_release_rules():
    m = json.loads((config.verifier_dir("v2") / "metrics.json").read_text())["metrics"]
    assert m["fairness"]["passes"] and m["fairness"]["after_fix"]["small_to_large_ratio"] <= 1.5
    for t, r in m["regression"].items():
        assert 0.85 <= r["band90_coverage"] <= 0.95, t
    for t, d in m["detection"].items():           # v2 must not be worse than v1 on the same held-out factories
        assert d["F1@low"] >= m["comparison_v1_same_test"][t]["F1@low"] - 1e-9, t
    card = (config.MODELS_DIR / "MODEL_CARD.md").read_text()
    assert "Fairness" in card and f"{m['detection']['gas_m3']['F1@low']:.1%}" in card  # rendered from the metrics


def test_pilot_schema_lists_every_problem(tmp_path):
    spec = importlib.util.spec_from_file_location("ctrace_train", config.ROOT / "ml" / "train.py")
    assert spec and spec.loader
    train = importlib.util.module_from_spec(spec)
    import sys
    sys.path.insert(0, str(config.ROOT / "ml"))
    spec.loader.exec_module(train)
    bad = pd.DataFrame({"factory_id": ["A"] * 2, "period": ["2025-01", "2025-13"], "factory_type": ["knit_composite", "tshirt"],
                        "workers": [100, -5], "has_wet_processing": [1, 1], "captive_gas_gen": [0, 0], "solar_kwp": [0, 0],
                        "pieces": [1000, 1000], "cdd": [0, 10], "gas_m3": [10, 10], "grid_kwh": [10, 10], "diesel_l": [1, 1]})
    path = tmp_path / "pilot.csv"
    bad.to_csv(path, index=False)
    with pytest.raises(SystemExit) as e:
        train.load_pilot(str(path))
    msg = str(e.value)
    assert "period" in msg and "factory_type" in msg and "workers" in msg and "12 months" in msg
