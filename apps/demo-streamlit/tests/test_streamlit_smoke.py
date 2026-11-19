"""The Streamlit app is the offline hackathon fallback: it must start and run the demo flow."""
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from ctrace import config

APP = Path(__file__).resolve().parent.parent / "streamlit_app.py"


def test_app_starts_without_data():
    at = AppTest.from_file(str(APP), default_timeout=60).run()
    assert not at.exception
    assert any("Load demo factory" in i.value for i in at.info)


@pytest.mark.skipif(not (config.MODELS_DIR / "ctrace_verifier.joblib").exists(), reason="train the model first")
def test_demo_factory_loads_and_flags_planted_months():
    at = AppTest.from_file(str(APP), default_timeout=180).run()
    next(b for b in at.button if "Load demo factory" in b.label).click().run()
    assert not at.exception
    # Verify tab: the only Low months are the two planted problems,
    # 2025-09 (dropped-digit gas bill) and 2026-07 (missing diesel receipt).
    low = next(m for m in at.metric if m.label.startswith("Low"))
    assert low.value == "2"
    low_months = {e.value.split(" · ")[0].strip("*") for e in at.error if " · Low" in e.value}
    assert low_months == {"2025-09", "2026-07"}
