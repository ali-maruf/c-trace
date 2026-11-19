"""Smoke tests for the C-Trace API as it stands after the Phase 1 restructure."""
import pytest
from fastapi.testclient import TestClient

from ctrace import config
from ctrace_api.main import app

client = TestClient(app)


def test_health_reports_brand_and_model():
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["service"] == "C-Trace"
    assert body["model"] is (config.MODELS_DIR / "ctrace_verifier.joblib").exists()


def test_factors_have_sources():
    r = client.get("/v1/factors", params={"year": 2025})
    assert r.status_code == 200
    factors = r.json()
    assert set(factors) == {"natural_gas", "grid_electricity", "diesel"}
    assert all(f["source"] for f in factors.values())


@pytest.mark.skipif(config.llm_enabled(), reason="asserts the offline rules path")
def test_extract_sample_bill_offline():
    path = config.BILLS_DIR / "2025-09_natural_gas.pdf"
    r = client.post("/v1/extract", files={"file": (path.name, path.read_bytes(), "application/pdf")})
    assert r.status_code == 200
    rec = r.json()
    assert rec["activity"] == "natural_gas" and rec["period"] == "2025-09"
    assert rec["extraction_method"] == "rules"
    assert any("tariff" in i.lower() for i in rec["issues"])  # the planted dropped-digit bill
