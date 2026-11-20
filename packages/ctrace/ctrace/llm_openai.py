"""OpenAI-compatible provider: any `/chat/completions` endpoint (OpenRouter, Groq, Cerebras, OpenAI).

Used when ANTHROPIC_API_KEY is not set and OPENAI_API_KEY is. The same rules apply as for Claude: the model
reads printed text and returns it with verbatim evidence; C-Trace's code parses, checks and calculates.
Endpoint, key and model come from `ctrace.config`.
"""
from __future__ import annotations

import base64
import json
import re
import time
from typing import Any

import httpx

from . import config
from .llm import LLMCall, ModelRefused, ModelTruncated, OnCall, Prompt, load_prompt, prepare_image

JSON_FALLBACK_PROMPT = "openai_json/v1"


class TextOnlyModel(ValueError):
    """The configured model cannot read images."""


class OpenAICompat:
    """Minimal client for the chat-completions wire format (no SDK, so any compatible host works)."""

    def __init__(self, base_url: str | None = None, api_key: str | None = None, transport: httpx.BaseTransport | None = None):
        self.base_url = (base_url or config.CTRACE_OPENAI_BASE_URL).rstrip("/")
        self.http = httpx.Client(timeout=config.CTRACE_LLM_TIMEOUT_S, transport=transport, headers={
            "Authorization": f"Bearer {api_key or config.OPENAI_API_KEY}",
            "X-Title": "C-Trace",  # OpenRouter's app attribution; ignored elsewhere
        })

    def chat(self, **body: Any) -> dict:
        fallbacks = [m for m in config.openai_models() if m != body.get("model")]
        if "openrouter.ai" in self.base_url and fallbacks and body.get("model") in config.openai_models():
            body["models"] = [body["model"], *fallbacks]  # OpenRouter tries these in order if one is unavailable
        last: Exception | None = None
        for attempt in range(3):
            r = self.http.post(f"{self.base_url}/chat/completions", json=body)
            if r.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                last = httpx.HTTPStatusError(f"{r.status_code}", request=r.request, response=r)
                time.sleep(min(float(r.headers.get("retry-after", 2 ** attempt)), 8))
                continue
            if r.status_code >= 400:
                raise httpx.HTTPStatusError(f"{r.status_code}: {r.text[:300]}", request=r.request, response=r)
            return r.json()
        raise last or RuntimeError("chat completion failed")


def client() -> OpenAICompat:
    return OpenAICompat()


def document_content(data: bytes, media_type: str) -> list[dict]:
    """A PDF goes as its text layer; an image only to a vision model."""
    if media_type == "application/pdf":
        from .extraction import pdf_text
        text = pdf_text(data)
        if not text.strip():
            raise TextOnlyModel("This PDF has no text layer and the configured model cannot read images.")
        return [{"type": "text", "text": f"DOCUMENT TEXT (the PDF's text layer, verbatim):\n\n{text}"}]
    if media_type.startswith("image/"):
        if not config.CTRACE_OPENAI_VISION:
            raise TextOnlyModel("The configured model cannot read photos.")
        img, mt = prepare_image(data, media_type)
        return [{"type": "image_url", "image_url": {"url": f"data:{mt};base64,{base64.standard_b64encode(img).decode()}"}}]
    raise ValueError(f"Unsupported media type for the model: {media_type}")


def first_json_object(text: str) -> dict:
    """Parse the model's printed JSON; tolerate a code fence or a short preamble."""
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    try:
        out = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise
        out = json.loads(text[start:end + 1])
    if not isinstance(out, dict):
        raise ValueError("The model did not return a JSON object")
    return out


def structured_call(llm: OpenAICompat, *, model: str, prompt: Prompt, content: list[dict], purpose: str,
                    document_sha256: str | None = None, max_tokens: int = 4096, on_call: OnCall | None = None) -> dict:
    """Ask for JSON that matches `prompt.schema`. Hosts that reject `json_schema` get `json_object` plus the schema
    in the system prompt (versioned in prompts/openai_json). Every attempt is reported through `on_call`."""
    strict = {"type": "json_schema", "json_schema": {"name": prompt.name, "strict": True, "schema": prompt.schema}}
    fallback_system = f"{prompt.system}\n\n{load_prompt(JSON_FALLBACK_PROMPT).system}\n{json.dumps(prompt.schema)}"
    attempts = [(prompt.system, strict), (fallback_system, {"type": "json_object"})]
    for i, (system, fmt) in enumerate(attempts):
        start = time.perf_counter()
        resp: dict = {}
        error = None
        try:
            resp = llm.chat(model=model, max_tokens=max_tokens, response_format=fmt,
                            messages=[{"role": "system", "content": system}, {"role": "user", "content": content}])
            choice = resp["choices"][0]
            finish = choice.get("finish_reason")
            if finish == "length":
                raise ModelTruncated(f"{model} output hit max_tokens={max_tokens}")
            if finish == "content_filter" or choice["message"].get("refusal"):
                raise ModelRefused(f"{model} declined to read this document")
            return first_json_object(choice["message"].get("content") or "")
        except httpx.HTTPStatusError as exc:
            error = f"HTTPStatusError: {exc}"[:500]
            if i == 0 and exc.response.status_code in (400, 404, 422):
                continue  # this host does not support json_schema for this model: try json_object
            raise
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"[:500]
            if i == 0 and isinstance(exc, json.JSONDecodeError | ValueError) and not isinstance(exc, ModelRefused | ModelTruncated):
                continue
            raise
        finally:
            if on_call:
                usage = resp.get("usage") or {}
                on_call(LLMCall(purpose=purpose, model=model, prompt_version=prompt.id,
                                input_tokens=usage.get("prompt_tokens"), output_tokens=usage.get("completion_tokens"),
                                latency_ms=int((time.perf_counter() - start) * 1000), document_sha256=document_sha256,
                                stop_reason=((resp.get("choices") or [{}])[0].get("finish_reason")), error=error))
    raise RuntimeError("unreachable")


def text_call(llm: OpenAICompat, *, model: str, system: str, user: str, max_tokens: int = 700) -> str:
    resp = llm.chat(model=model, max_tokens=max_tokens,
                    messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
    return (resp["choices"][0]["message"].get("content") or "").strip()


def to_openai_tools(specs: list[dict]) -> list[dict]:
    return [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                              "parameters": t["input_schema"]}} for t in specs]
