"""Shared plumbing for every C-Trace LLM call: client, versioned prompts, call records, caching, media prep.

Model IDs come from `ctrace.config` (environment variables); nothing here hard-codes one.
"""
from __future__ import annotations

import base64
import io
import json
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol

from . import config

MAX_IMAGE_EDGE_PX = 2576          # current models' high-resolution limit (long edge)
MAX_IMAGE_BYTES = 5 * 1024 * 1024  # raw bytes; keeps base64 under every platform's per-image limit
MAX_PDF_BYTES = 30 * 1024 * 1024   # the request limit is 32 MB including the prompt


@dataclass(frozen=True)
class Prompt:
    name: str
    version: str
    system: str
    schema: dict

    @property
    def id(self) -> str:
        return f"{self.name}/{self.version}"


@lru_cache(maxsize=16)
def load_prompt(prompt_id: str) -> Prompt:
    """'extract_bill/v2' → prompts/extract_bill/v2.md (+ v2.schema.json when present)."""
    name, version = prompt_id.split("/", 1)
    base = config.PROMPTS_DIR / name
    system = (base / f"{version}.md").read_text(encoding="utf-8").strip()
    schema_path = base / f"{version}.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8")) if schema_path.exists() else {}
    return Prompt(name, version, system, schema)


@dataclass
class LLMCall:
    """One real request to the model. Logged to `llm_calls` by the store."""
    purpose: str
    model: str
    prompt_version: str
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: int
    document_sha256: str | None
    stop_reason: str | None
    error: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


OnCall = Callable[[LLMCall], None]


class Cache(Protocol):
    def get(self, key: str) -> dict | None: ...
    def put(self, key: str, value: dict) -> None: ...


class FileCache:
    """Local JSON cache (offline demo, eval, stateless API). The store uses an org-scoped table instead."""

    def __init__(self, directory: Path | None = None):
        self.dir = Path(directory or config.DATA_DIR / "cache" / "llm")

    def _path(self, key: str) -> Path:
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in key)
        return self.dir / f"{safe}.json"

    def get(self, key: str) -> dict | None:
        p = self._path(key)
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    def put(self, key: str, value: dict) -> None:
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            tmp = self._path(key).with_suffix(".part")
            tmp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self._path(key))
        except OSError:  # read-only deployment: the cache is an optimisation, never required
            pass


def cache_key(document_sha256: str, model: str, prompt_id: str) -> str:
    return f"{document_sha256}:{model}:{prompt_id}"


def client() -> Any:
    import anthropic
    return anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY or None, timeout=config.CTRACE_LLM_TIMEOUT_S, max_retries=2)


# ------------------------------------------------------------------ media
def prepare_image(data: bytes, media_type: str) -> tuple[bytes, str]:
    """Downscale to the model's long-edge limit and keep the payload under the per-image size limit."""
    from PIL import Image, ImageOps

    img: Image.Image = Image.open(io.BytesIO(data))
    img = ImageOps.exif_transpose(img)  # phone photos carry their rotation in EXIF
    if max(img.size) <= MAX_IMAGE_EDGE_PX and len(data) <= MAX_IMAGE_BYTES and media_type in ("image/jpeg", "image/png"):
        return data, media_type
    img.thumbnail((MAX_IMAGE_EDGE_PX, MAX_IMAGE_EDGE_PX))
    img = img.convert("RGB")
    for quality in (90, 80, 70, 60):
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality, optimize=True)
        if buf.tell() <= MAX_IMAGE_BYTES:
            return buf.getvalue(), "image/jpeg"
    raise ValueError("Image is too large to send even after downscaling")


def content_block(data: bytes, media_type: str) -> dict:
    if media_type == "application/pdf":
        if len(data) > MAX_PDF_BYTES:
            raise ValueError("PDF is larger than the model request limit")
        return {"type": "document", "source": {"type": "base64", "media_type": "application/pdf",
                                               "data": base64.standard_b64encode(data).decode()}}
    if media_type.startswith("image/"):
        img, mt = prepare_image(data, media_type)
        return {"type": "image", "source": {"type": "base64", "media_type": mt, "data": base64.standard_b64encode(img).decode()}}
    raise ValueError(f"Unsupported media type for the model: {media_type}")


# ------------------------------------------------------------------ the call
class ModelRefused(RuntimeError):
    pass


class ModelTruncated(RuntimeError):
    pass


def structured_call(llm: Any, *, model: str, prompt: Prompt, content: list[dict], purpose: str,
                    document_sha256: str | None = None, max_tokens: int = 4096, on_call: OnCall | None = None) -> dict:
    """One Messages request constrained to `prompt.schema` (structured outputs). Returns the parsed JSON.

    Forced tool use is not used: current Sonnet models reject it, and structured outputs give the same
    schema guarantee on every model. Every attempt, failed or not, is reported through `on_call`.
    """
    kwargs: dict[str, Any] = {
        "model": model, "max_tokens": max_tokens, "system": prompt.system,
        "messages": [{"role": "user", "content": content}],
        "output_config": {"effort": config.CTRACE_EXTRACTION_EFFORT, "format": {"type": "json_schema", "schema": prompt.schema}},
    }
    use_fallbacks = config.CTRACE_LLM_FALLBACKS == "default" and model.startswith(("claude-sonnet-5-5", "claude-opus-5", "claude-fable-5-1"))
    start = time.perf_counter()
    stop_reason = error = None
    usage: Any = None
    try:
        if use_fallbacks:
            resp = llm.beta.messages.create(betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs)
        else:
            resp = llm.messages.create(**kwargs)
        stop_reason, usage = resp.stop_reason, resp.usage
        if stop_reason == "refusal":
            raise ModelRefused(f"{model} declined to read this document")
        if stop_reason == "max_tokens":
            raise ModelTruncated(f"{model} output hit max_tokens={max_tokens}")
        text = next(b.text for b in resp.content if getattr(b, "type", None) == "text")
        return json.loads(text)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"[:500]
        raise
    finally:
        if on_call:
            on_call(LLMCall(purpose=purpose, model=model, prompt_version=prompt.id,
                            input_tokens=getattr(usage, "input_tokens", None), output_tokens=getattr(usage, "output_tokens", None),
                            latency_ms=int((time.perf_counter() - start) * 1000), document_sha256=document_sha256,
                            stop_reason=stop_reason, error=error))
