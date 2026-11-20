"""OpenAI-compatible provider (e.g. gpt-oss-120b on a free tier), tested offline with httpx.MockTransport."""
from __future__ import annotations

import json

import httpx
import pytest

from ctrace import config, copilot
from ctrace.extraction import extract_document
from ctrace.extraction_llm import cascade_models, extract_with_llm
from ctrace.llm_openai import OpenAICompat, first_json_object

BILL_PDF = config.BILLS_DIR / "2025-09_natural_gas.pdf"


def chat_reply(content: str | None = None, tool_calls: list | None = None, finish: str = "stop") -> dict:
    return {"choices": [{"message": {"role": "assistant", "content": content, "tool_calls": tool_calls}, "finish_reason": finish}],
            "usage": {"prompt_tokens": 900, "completion_tokens": 200}}


class Host:
    """A fake /chat/completions host. `replies` are consumed in order; each may be (status, body)."""

    def __init__(self, *replies):
        self.replies, self.requests = list(replies), []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(json.loads(request.content))
        r = self.replies.pop(0)
        status, body = r if isinstance(r, tuple) else (200, r)
        return httpx.Response(status, json=body)

    def client(self) -> OpenAICompat:
        return OpenAICompat("https://llm.test/v1", "test-key", transport=httpx.MockTransport(self))


@pytest.fixture
def openai_on(monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "")
    monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(config, "CTRACE_OPENAI_VISION", False)


def test_provider_selection(monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "")
    assert config.llm_provider() == "" and not config.llm_enabled()
    monkeypatch.setattr(config, "OPENAI_API_KEY", "k")
    assert config.llm_provider() == "openai" and cascade_models() == [config.openai_models()[0]]
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "k")
    assert config.llm_provider() == "anthropic"  # Claude wins when both are set


def test_pdf_goes_as_text_and_numbers_are_parsed_in_code(fakes, openai_on):
    host = Host(chat_reply(json.dumps(fakes.bangla_gas_bill(), ensure_ascii=False)))
    calls: list = []
    rec = extract_with_llm(BILL_PDF.read_bytes(), "bn.pdf", "application/pdf", client=host.client(), on_call=calls.append)
    assert (rec.activity, rec.period, rec.quantity, rec.amount_bdt) == ("natural_gas", "2025-09", 32044.0, 1009386.0)
    assert rec.extraction_method == f"llm:{config.openai_models()[0]}"
    req = host.requests[0]
    assert req["response_format"]["type"] == "json_schema" and "tool_choice" not in req
    assert req["messages"][1]["content"][0]["text"].startswith("DOCUMENT TEXT")
    assert calls[0].input_tokens == 900 and calls[0].error is None


def test_host_without_json_schema_falls_back_to_json_object(fakes, openai_on):
    fenced = "```json\n" + json.dumps(fakes.bangla_gas_bill(), ensure_ascii=False) + "\n```"
    host = Host((400, {"error": "response_format json_schema not supported"}), chat_reply(fenced))
    calls: list = []
    rec = extract_with_llm(BILL_PDF.read_bytes(), "bn.pdf", "application/pdf", client=host.client(), on_call=calls.append)
    assert rec.quantity == 32044.0
    assert [r["response_format"]["type"] for r in host.requests] == ["json_schema", "json_object"]
    assert "JSON Schema" in host.requests[1]["messages"][0]["content"]
    assert len(calls) == 2 and calls[0].error and calls[1].error is None


def test_text_only_model_says_so_for_photos(openai_on, monkeypatch):
    monkeypatch.setattr("ctrace.llm_openai.client", lambda: pytest.fail("no request for a photo to a text-only model"))
    rec = extract_document(b"\x89PNG fake", "bill.png", "image/png")
    assert rec.quantity is None and any("cannot read photos" in i for i in rec.issues)


def test_endpoint_down_falls_back_to_rules_and_says_so(openai_on, monkeypatch):
    host = Host((503, {}), (503, {}), (503, {}))
    monkeypatch.setattr("ctrace.llm_openai.time.sleep", lambda s: None)
    monkeypatch.setattr("ctrace.extraction_llm.openai_client", host.client)
    rec = extract_document(BILL_PDF.read_bytes(), BILL_PDF.name)
    assert rec.extraction_method == "rules" and rec.quantity is not None
    assert any("LLM extraction failed" in i for i in rec.issues)


def test_offline_flag_never_calls_the_model(openai_on, monkeypatch):
    monkeypatch.setattr("ctrace.extraction_llm.openai_client", lambda: pytest.fail("sample bills stay offline"))
    assert extract_document(BILL_PDF.read_bytes(), BILL_PDF.name, offline=True).extraction_method == "rules"


def test_openrouter_gets_the_fallback_list(fakes, openai_on, monkeypatch):
    monkeypatch.setattr(config, "CTRACE_OPENAI_MODEL", "a/one:free, b/two:free")
    host = Host(chat_reply(json.dumps(fakes.bangla_gas_bill(), ensure_ascii=False)))
    cli = OpenAICompat("https://openrouter.ai/api/v1", "k", transport=httpx.MockTransport(host))
    extract_with_llm(BILL_PDF.read_bytes(), "bn.pdf", "application/pdf", client=cli)
    assert host.requests[0]["model"] == "a/one:free" and host.requests[0]["models"] == ["a/one:free", "b/two:free"]


def test_first_json_object_tolerates_preamble():
    assert first_json_object('Here you go: {"a": 1} ') == {"a": 1}
    with pytest.raises(ValueError):
        first_json_object("[1, 2]")


TOOLS = {"get_summary": lambda period="": {"period": period, "total_tco2e": 8123.4, "trace_ids": ["CT-2025-01-GAS"]}}


def test_copilot_tool_loop_cites_and_is_number_guarded(openai_on):
    tool_call = [{"id": "c1", "type": "function", "function": {"name": "get_summary", "arguments": '{"period": "2025"}'}}]
    host = Host(chat_reply(None, tool_call, "tool_calls"), chat_reply("2025 total: 8,123.4 tCO2e [trace:CT-2025-01-GAS]"))
    events = list(copilot.ask("What were total emissions in 2025?", TOOLS, client=host.client()))
    ans = events[-1]["answer"]
    assert ans["citations"] == [{"type": "trace", "id": "CT-2025-01-GAS", "label": "CT-2025-01-GAS"}]
    assert "8,123.4" in ans["text"] and ans["method"] == f"llm:{config.openai_models()[0]}"
    assert host.requests[0]["tools"][0]["function"]["name"] == "get_summary"
    assert host.requests[1]["messages"][-1] == {"role": "tool", "tool_call_id": "c1", "content": json.dumps(TOOLS["get_summary"]("2025"))}


def test_copilot_invented_number_is_blocked(openai_on):
    tool_call = [{"id": "c1", "type": "function", "function": {"name": "get_summary", "arguments": '{"period": "2025"}'}}]
    host = Host(chat_reply(None, tool_call, "tool_calls"), chat_reply("2025 total: 9,999.9 tCO2e [trace:CT-2025-01-GAS]"))
    ans = list(copilot.ask("What were total emissions in 2025?", TOOLS, client=host.client()))[-1]["answer"]
    assert "9,999.9" not in ans["text"] and ans["method"].startswith("offline")


def test_copilot_endpoint_down_answers_offline(openai_on, monkeypatch):
    monkeypatch.setattr("ctrace.llm_openai.time.sleep", lambda s: None)
    host = Host((429, {}), (429, {}), (429, {}))
    ans = list(copilot.ask("Write me a poem about football", TOOLS, client=host.client()))[-1]["answer"]
    assert ans["declined"] is True and "unavailable" in ans["method"]


def test_slow_model_gives_template_narrative(openai_on, monkeypatch):
    from ctrace import report

    def slow(request):
        raise httpx.ReadTimeout("free model queue", request=request)
    monkeypatch.setattr("ctrace.llm_openai.client", lambda **kw: OpenAICompat("https://llm.test/v1", "k", transport=httpx.MockTransport(slow)))
    monkeypatch.setattr(report, "retriever", lambda: type("R", (), {"search": lambda self, q, k=3: []})())
    facts = {"factory": "Demo", "period_from": "2025-01", "period_to": "2025-12", "total_tco2e": 8662.7}
    monkeypatch.setattr(report, "template_narrative", lambda fmt, f: "template text")
    out = report.generate("buyer", facts)
    assert out["method"] == "template" and out["narrative"] == "template text"
