"""The extraction eval set and scorer (offline: rules method only)."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from ctrace import config

ROOT = config.ROOT
BILLS = config.DATA_DIR / "eval" / "bills"


@pytest.fixture(scope="module")
def evalmod():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location("ctrace_eval_extraction", ROOT / "ml" / "eval_extraction.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def labels() -> list[dict]:
    return json.loads((BILLS / "labels.json").read_text(encoding="utf-8"))["bills"]


def test_eval_set_is_complete_and_fictional(labels):
    assert len(labels) >= 30
    assert {(lb["kind"], lb["language"]) for lb in labels} >= {("pdf_text", "en"), ("photo", "bn"), ("photo", "en"), ("pdf_text", "bn")}
    for lb in labels:
        assert (BILLS / lb["file"]).is_file(), lb["file"]
    from ctrace.extraction import pdf_text
    for lb in labels:  # every PDF carries the sample watermark (photos are checked by eye: the watermark is in the image)
        if lb["file"].endswith(".pdf"):
            assert "C-Trace" in pdf_text((BILLS / lb["file"]).read_bytes()), lb["file"]


def test_rules_baseline(evalmod, labels, tmp_path: Path):
    r = evalmod.run("rules", labels, out_dir=tmp_path)
    m = r["metrics"]
    assert (tmp_path / "rules.json").is_file() and r["n_bills"] == len(labels)
    assert m["by_group"]["pdf_text/en"]["critical_accuracy"] >= 0.9   # regression guard on the layouts it supports
    assert m["by_group"]["photo/bn"]["critical_accuracy"] == 0.0      # it must never pretend to read a photo
    assert m["privacy_leaks"] == 0 and m["dropped_digit_flagged"]
    assert "llm" not in m


def test_scorer_tolerances(evalmod):
    from ctrace.extraction import BillRecord
    label = {"file": "x", "kind": "pdf_text", "language": "en", "layout": "A", "activity": "natural_gas", "period": "2025-01",
             "quantity": 1000.0, "unit": "m3", "amount_bdt": 31500.0, "bill_no": "A-1", "person_names": ["Rina"]}
    rec = BillRecord("x", "0" * 64, activity="natural_gas", period="2025-01", quantity=1004.0, unit="m3",
                     amount_bdt=31500.0, bill_no="a-1", evidence={"bill_no": "Customer: Rina"})
    s = evalmod.score(label, rec)
    assert s["critical"] and s["correct"]["bill_no"] and s["privacy_leak"]
    rec.quantity = 1006.0
    assert not evalmod.score(label, rec)["correct"]["quantity"]
