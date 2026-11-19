"""Fakes shaped like the Anthropic SDK, shared by the extraction tests (offline: no key, no network)."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest


def field(value: str, evidence: str | None = None, confidence: float = 0.95) -> dict:
    return {"value": value, "evidence": value if evidence is None else evidence, "confidence": confidence if value else 0.0}


def bangla_gas_bill(**over: dict) -> dict:
    p = {
        "document_type": field("natural_gas", "গ্যাস বিল"),
        "supplier": field("মেঘনা গ্যাস ডিস্ট্রিবিউশন (কাল্পনিক)"),
        "account_no": field("৪৪১-২০৯"),
        "bill_no": field("GB-2025-09-114"),
        "billing_period": field("সেপ্টেম্বর ২০২৫", "বিলের মাস: সেপ্টেম্বর ২০২৫"),
        "consumption_quantity": field("৩২,০৪৪", "ব্যবহৃত গ্যাস: ৩২,০৪৪ ঘনমিটার"),
        "consumption_unit": field("ঘনমিটার"),
        "amount_bdt": field("১০,০৯,৩৮৬.০০", "মোট টাকা: ১০,০৯,৩৮৬.০০"),
        "language": field("bn", "বাংলা"),
    }
    p.update(over)
    return p


def response(payload: dict | None, stop: str = "end_turn") -> SimpleNamespace:
    text = json.dumps(payload, ensure_ascii=False) if payload is not None else "I can't help with that."
    return SimpleNamespace(stop_reason=stop, usage=SimpleNamespace(input_tokens=1200, output_tokens=310),
                           content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)])


class FakeLLM:
    """Records requests; answers per model. `beta.messages.create` is the same endpoint with fallbacks."""

    def __init__(self, answers: dict):
        self.answers, self.calls = answers, []
        self.messages = self
        self.beta = SimpleNamespace(messages=self)

    def create(self, **kw):  # type: ignore[no-untyped-def]
        self.calls.append(kw)
        a = self.answers[kw["model"]]
        if isinstance(a, Exception):
            raise a
        return a


@pytest.fixture
def fakes() -> SimpleNamespace:
    return SimpleNamespace(field=field, bangla_gas_bill=bangla_gas_bill, response=response, FakeLLM=FakeLLM)
