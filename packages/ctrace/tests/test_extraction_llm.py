"""LLM extraction path, tested offline with a fake client shaped like the Anthropic SDK response."""
from __future__ import annotations

import io
import json

import pytest

from ctrace import config
from ctrace.extraction import extract_document
from ctrace.extraction_llm import extract_with_llm, payload_to_record
from ctrace.llm import MAX_IMAGE_EDGE_PX, FileCache, load_prompt, prepare_image

HAIKU, SONNET = config.CTRACE_EXTRACTION_MODEL, config.CTRACE_EXTRACTION_ESCALATION_MODEL
BILL_PDF = config.BILLS_DIR / "2025-09_natural_gas.pdf"


def test_bangla_bill_is_normalised_in_code(fakes):
    calls: list = []
    llm = fakes.FakeLLM({HAIKU: fakes.response(fakes.bangla_gas_bill())})
    rec = extract_with_llm(b"%PDF-fake", "bn.pdf", "application/pdf", client=llm, on_call=calls.append)
    assert (rec.activity, rec.period, rec.quantity, rec.unit, rec.amount_bdt) == ("natural_gas", "2025-09", 32044.0, "m3", 1009386.0)
    assert rec.language == "bn" and rec.issues == [] and rec.extraction_confidence == 0.95
    assert rec.extraction_method == f"llm:{HAIKU}" and rec.prompt_version == "extract_bill/v2"
    assert len(llm.calls) == 1 and len(calls) == 1
    req = llm.calls[0]
    assert req["output_config"]["format"]["type"] == "json_schema" and "tool_choice" not in req
    assert req["messages"][0]["content"][0]["type"] == "document"
    assert calls[0].input_tokens == 1200 and calls[0].prompt_version == "extract_bill/v2" and calls[0].error is None


def test_value_missing_from_its_evidence_is_capped_and_escalated(fakes):
    bad = fakes.bangla_gas_bill(consumption_quantity=fakes.field("3,20,440", "ব্যবহৃত গ্যাস: ৩২,০৪৪ ঘনমিটার"))
    llm = fakes.FakeLLM({HAIKU: fakes.response(bad), SONNET: fakes.response(fakes.bangla_gas_bill())})
    rec = extract_with_llm(b"%PDF-fake", "bn.pdf", "application/pdf", client=llm)
    assert [c["model"] for c in llm.calls] == [HAIKU, SONNET]
    assert rec.extraction_method == f"llm:{SONNET}" and rec.quantity == 32044.0
    assert [a["model"] for a in rec.attempts] == [HAIKU, SONNET] and rec.attempts[0]["confidence"] == 0.5
    assert llm.calls[1].get("fallbacks") == "default"  # Sonnet 5.5 call opts into server-side refusal fallback


def test_low_confidence_after_cascade_is_flagged_for_a_human(fakes):
    blurry = fakes.bangla_gas_bill(consumption_quantity=fakes.field("৩২,০৪৪", "ব্যবহৃত গ্যাস: ৩২,০৪৪ ঘনমিটার", 0.4))
    llm = fakes.FakeLLM({HAIKU: fakes.response(blurry), SONNET: fakes.response(blurry)})
    rec = extract_with_llm(b"%PDF-fake", "bn.pdf", "application/pdf", client=llm)
    assert rec.extraction_confidence == 0.4 and any("human should check" in i for i in rec.issues)


def test_cache_hit_makes_no_call(fakes, tmp_path):
    cache = FileCache(tmp_path)
    llm = fakes.FakeLLM({HAIKU: fakes.response(fakes.bangla_gas_bill())})
    first = extract_with_llm(b"%PDF-same-bytes", "a.pdf", "application/pdf", client=llm, cache=cache)
    second = extract_with_llm(b"%PDF-same-bytes", "renamed.pdf", "application/pdf", client=llm, cache=cache)
    assert len(llm.calls) == 1 and second.attempts[0]["cached"] is True
    assert (first.quantity, first.period) == (second.quantity, second.period) and second.doc_name == "renamed.pdf"


@pytest.mark.parametrize("stop", ["refusal", "max_tokens"])
def test_refusal_or_truncation_is_not_parsed(fakes, stop):
    calls: list = []
    llm = fakes.FakeLLM({HAIKU: fakes.response(None, stop), SONNET: fakes.response(fakes.bangla_gas_bill())})
    rec = extract_with_llm(b"%PDF-fake", "bn.pdf", "application/pdf", client=llm, on_call=calls.append)
    assert rec.extraction_method == f"llm:{SONNET}"
    assert calls[0].stop_reason == stop and calls[0].error


def test_cascade_failure_falls_back_to_rules_and_says_so(fakes):
    llm = fakes.FakeLLM({HAIKU: fakes.response(None, "refusal"), SONNET: RuntimeError("network down")})
    rec = extract_document(BILL_PDF.read_bytes(), BILL_PDF.name, "application/pdf", client=llm)
    assert rec.extraction_method == "rules" and rec.period == "2025-09"
    assert any("LLM extraction failed" in i for i in rec.issues)


def test_rules_and_vision_disagreement_is_raised(fakes):
    llm = fakes.FakeLLM({HAIKU: fakes.response(fakes.bangla_gas_bill(
        billing_period=fakes.field("Sep 2025"), consumption_quantity=fakes.field("33,000", "Consumption: 33,000 m3"),
        amount_bdt=fakes.field("1,009,386.00")))})
    rec = extract_document(BILL_PDF.read_bytes(), BILL_PDF.name, "application/pdf", client=llm)
    assert any("text-layer parser read quantity" in i for i in rec.issues) and rec.extraction_confidence <= 0.6


def test_personal_data_is_removed_from_evidence(fakes):
    p = fakes.bangla_gas_bill(account_no=fakes.field("441-209", "Customer name: Rina Akter (fictional); Account No: 441-209"))
    rec = payload_to_record(p, "x.pdf", "0" * 64, HAIKU, "extract_bill/v2")
    assert "Rina" not in json.dumps(rec.evidence) and "[redacted]" in rec.evidence["account_no"]


def test_unreadable_period_is_an_issue_not_a_guess(fakes):
    rec = payload_to_record(fakes.bangla_gas_bill(billing_period=fakes.field("Q3")), "x.pdf", "0" * 64, HAIKU, "extract_bill/v2")
    assert rec.period is None and any("could not be read as a month" in i for i in rec.issues)


def test_large_photo_is_downscaled_within_limits(fakes):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (5200, 3900), "white").save(buf, format="PNG")
    out, mt = prepare_image(buf.getvalue(), "image/png")
    img = Image.open(io.BytesIO(out))
    assert mt == "image/jpeg" and max(img.size) == MAX_IMAGE_EDGE_PX


def test_schema_respects_structured_output_limits(fakes):
    """Limits from the structured-outputs docs: additionalProperties false on every object, no numeric/string
    constraints, ≤ 24 optional params, ≤ 16 union-typed params, no oneOf/not."""
    schema = load_prompt("extract_bill/v2").schema
    optional = unions = 0

    def walk(node: dict) -> None:
        nonlocal optional, unions
        assert not {"oneOf", "not", "minimum", "maximum", "minLength", "maxLength", "multipleOf"} & set(node)
        if isinstance(node.get("type"), list) or "anyOf" in node:
            unions += 1
        if node.get("type") == "object":
            assert node.get("additionalProperties") is False
            props = node.get("properties", {})
            optional += len(set(props) - set(node.get("required", [])))
            for child in props.values():
                walk(child)
        if node.get("type") == "array":
            walk(node["items"])

    walk(schema)
    assert optional <= 24 and unions <= 16
    assert set(schema["required"]) >= {"document_type", "billing_period", "consumption_quantity", "consumption_unit", "amount_bdt"}
