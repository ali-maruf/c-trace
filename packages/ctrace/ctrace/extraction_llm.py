"""C-Trace extraction with an LLM (Claude, or an OpenAI-compatible model): strict schema, verbatim evidence, cache, escalation.

Flow per document:
  1. For each model in the cascade (config: extraction model, then escalation model), look up the cache
     by (document SHA-256, model, prompt version); call the model only on a miss.
  2. Parse the printed text in code: Bangla digits, lakh grouping, Bangla months, units.
  3. Check each numeric value against its own evidence; a value whose digits are not in the quoted
     span gets its confidence capped and an issue for a human.
  4. Remove personal data from evidence, then run the shared `finalize` checks (unit, tariff).
  5. Stop when the read is confident enough; otherwise escalate. The best read wins.
"""
from __future__ import annotations

from typing import Any

from . import config
from .extraction import ACTIVITIES, BillRecord, finalize, normalize_period, sha256_bytes
from .llm import Cache, OnCall, cache_key, content_block, load_prompt, structured_call
from .llm import client as default_client
from .llm_openai import OpenAICompat, document_content
from .llm_openai import client as openai_client
from .llm_openai import structured_call as openai_structured_call
from .textnorm import evidence_supports, parse_number, redact_personal

CRITICAL = ("document_type", "billing_period", "consumption_quantity")
EVIDENCE_CAP = 0.5
INSTRUCTION = "Read this document and return the C-Trace bill fields."


def cascade_models() -> list[str]:
    if config.llm_provider() == "openai":
        return [config.CTRACE_OPENAI_MODEL]
    models = [config.CTRACE_EXTRACTION_MODEL]
    esc = config.CTRACE_EXTRACTION_ESCALATION_MODEL
    if esc and esc not in models:
        models.append(esc)
    return models


def _f(payload: dict, name: str) -> tuple[str, str, float]:
    f = payload.get(name) or {}
    conf = f.get("confidence")
    return (str(f.get("value") or "").strip(), str(f.get("evidence") or ""),
            min(max(float(conf), 0.0), 1.0) if isinstance(conf, int | float) else 0.0)


def payload_to_record(payload: dict, doc_name: str, sha: str, model: str, prompt_id: str) -> BillRecord:
    rec = BillRecord(doc_name=doc_name, doc_sha256=sha, extraction_method=f"llm:{model}", prompt_version=prompt_id)
    conf: dict[str, float] = {}
    for name in payload:
        value, evidence, c = _f(payload, name)
        conf[name] = c if value else 0.0
        if evidence:
            rec.evidence[name] = redact_personal(evidence)[:160]

    doc_type = _f(payload, "document_type")[0].lower()
    rec.activity = doc_type if doc_type in ACTIVITIES else None
    for attr, name in (("supplier", "supplier"), ("account_no", "account_no"), ("bill_no", "bill_no")):
        setattr(rec, attr, _f(payload, name)[0] or None)
    lang = _f(payload, "language")[0].lower()
    rec.language = lang if lang in ("en", "bn", "mixed") else None

    raw_period = _f(payload, "billing_period")[0]
    rec.period = normalize_period(raw_period) if raw_period else None
    if raw_period and rec.period is None:
        rec.issues.append(f"Billing period '{raw_period}' could not be read as a month.")
        conf["billing_period"] = min(conf.get("billing_period", 0.0), EVIDENCE_CAP)

    for attr, name in (("quantity", "consumption_quantity"), ("amount_bdt", "amount_bdt")):
        value, evidence, _ = _f(payload, name)
        number = parse_number(value)
        setattr(rec, attr, number)
        if value and number is None:
            rec.issues.append(f"{name.replace('_', ' ').capitalize()} '{value}' is not a number.")
            conf[name] = 0.0
        elif number is not None and not evidence_supports(number, evidence):
            rec.issues.append(f"{name.replace('_', ' ').capitalize()} {value} does not appear in its quoted evidence — please check.")
            conf[name] = min(conf.get(name, 0.0), EVIDENCE_CAP)
    rec.unit = _f(payload, "consumption_unit")[0] or None

    rec.field_confidence = {k: round(v, 3) for k, v in conf.items()}
    rec.extraction_confidence = round(min(conf.get(k, 0.0) for k in CRITICAL), 3)
    return finalize(rec)


def extract_with_llm(data: bytes, doc_name: str, media_type: str, *, cache: Cache | None = None,
                     on_call: OnCall | None = None, client: Any = None, models: list[str] | None = None) -> BillRecord:
    """`models` overrides the configured cascade (the eval scores each model on its own)."""
    prompt = load_prompt(config.CTRACE_EXTRACTION_PROMPT)
    sha = sha256_bytes(data)
    openai = config.llm_provider() == "openai" if client is None else isinstance(client, OpenAICompat)
    if openai:
        llm: Any = client or openai_client()
        content = [*document_content(data, media_type), {"type": "text", "text": INSTRUCTION}]
        call = openai_structured_call
    else:
        llm = client or default_client()
        content = [content_block(data, media_type), {"type": "text", "text": INSTRUCTION}]
        call = structured_call
    best: BillRecord | None = None
    attempts: list[dict] = []
    for model in models or cascade_models():
        key = cache_key(sha, model, prompt.id)
        payload = cache.get(key) if cache else None
        cached = payload is not None
        if payload is None:
            try:
                payload = call(llm, model=model, prompt=prompt, content=content, purpose="extract_bill",
                                          document_sha256=sha, on_call=on_call)
            except Exception as exc:
                attempts.append({"model": model, "cached": False, "error": f"{type(exc).__name__}: {exc}"[:300]})
                continue
            if cache:
                cache.put(key, payload)
        rec = payload_to_record(payload, doc_name, sha, model, prompt.id)
        attempts.append({"model": model, "cached": cached, "confidence": rec.extraction_confidence,
                         "field_confidence": rec.field_confidence})
        if best is None or rec.extraction_confidence >= best.extraction_confidence:
            best = rec
        if rec.extraction_confidence >= config.CTRACE_ESCALATE_BELOW:
            break
    if best is None:
        raise RuntimeError("Every model in the C-Trace extraction cascade failed: "
                           + "; ".join(a.get("error", "") for a in attempts))
    best.attempts = attempts
    if best.extraction_confidence < config.CTRACE_ESCALATE_BELOW:
        best.issues.append(f"Low reading confidence ({best.extraction_confidence:.2f}) — a human should check this bill.")
    return best
