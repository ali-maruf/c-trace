"""C-Trace document extraction: utility bills / fuel receipts -> structured activity records.

Two paths:
  1. LLM path (if ANTHROPIC_API_KEY is set): Claude reads PDFs or photos, including Bangla bills,
     and returns strict JSON. The model only *reads*; it never calculates emissions.
  2. Offline path: pdfplumber text layer + rule-based parser. Works without any API key.
Both paths return the same BillRecord schema and are validated identically.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import pdfplumber

from . import config

ACTIVITIES = ("natural_gas", "grid_electricity", "diesel")
CANONICAL_UNIT = {"natural_gas": "m3", "grid_electricity": "kWh", "diesel": "litre"}

# unit alias -> (canonical unit, multiplier)
UNIT_ALIASES = {
    "m3": ("m3", 1.0), "m³": ("m3", 1.0), "cubic meter": ("m3", 1.0), "cubic metre": ("m3", 1.0),
    "scm": ("m3", 1.0), "cft": ("m3", 0.0283168), "mmbtu": ("m3", 1.055056 / 0.036),
    "kwh": ("kWh", 1.0), "mwh": ("kWh", 1000.0), "unit": ("kWh", 1.0), "units": ("kWh", 1.0),
    "litre": ("litre", 1.0), "liter": ("litre", 1.0), "litres": ("litre", 1.0), "l": ("litre", 1.0),
    "ltr": ("litre", 1.0),
}


@dataclass
class BillRecord:
    doc_name: str
    doc_sha256: str
    activity: str | None = None
    supplier: str | None = None
    account_no: str | None = None
    bill_no: str | None = None
    period: str | None = None          # YYYY-MM
    quantity: float | None = None      # in canonical unit
    unit: str | None = None
    amount_bdt: float | None = None
    extraction_method: str = "rules"
    extraction_confidence: float = 0.0
    issues: list = field(default_factory=list)
    evidence: dict = field(default_factory=dict)  # field -> text snippet it came from

    def as_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------- helpers
def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _num(s: str) -> float:
    return float(s.replace(",", "").strip())


MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}


def normalize_period(text: str) -> str | None:
    text = text.strip()
    m = re.search(r"(20\d{2})[-/.](\d{1,2})", text)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}"
    m = re.search(r"([A-Za-z]{3,9})[\s,\-]+(20\d{2})", text)
    if m and m.group(1)[:3].lower() in MONTHS:
        return f"{int(m.group(2)):04d}-{MONTHS[m.group(1)[:3].lower()]:02d}"
    m = re.search(r"(\d{1,2})[-/.](20\d{2})", text)
    if m:
        return f"{int(m.group(2)):04d}-{int(m.group(1)):02d}"
    return None


def normalize_unit(raw_unit: str | None, activity: str | None) -> tuple[str | None, float]:
    if raw_unit:
        key = raw_unit.strip().lower().replace(".", "")
        if key in UNIT_ALIASES:
            return UNIT_ALIASES[key]
    if activity:
        return CANONICAL_UNIT[activity], 1.0
    return None, 1.0


def infer_activity(text: str) -> str | None:
    t = text.lower()
    scores = {
        "natural_gas": sum(k in t for k in ("gas", "m3", "m³", "cubic", "burner", "গ্যাস")),
        "grid_electricity": sum(k in t for k in ("electric", "kwh", "power", "meter reading", "বিদ্যুৎ")),
        "diesel": sum(k in t for k in ("diesel", "litre", "liter", "fuel", "ডিজেল", "generator")),
    }
    best = max(scores, key=lambda k: scores[k])
    return best if scores[best] > 0 else None


# ---------------------------------------------------------------- offline (rules) path
FIELD_PATTERNS = {
    "bill_no": r"(?:bill|invoice|receipt)\s*(?:no|number|#)\.?\s*[:\-]?\s*([A-Z0-9\-/]+)",
    "account_no": r"(?:account|customer|consumer)\s*(?:no|id|number)\.?\s*[:\-]?\s*([A-Z0-9\-/]+)",
    "supplier": r"(?:supplier|issued by|distributor|vendor)\s*[:\-]\s*(.+)",
    "period": r"(?:billing\s*period|bill\s*month|period|month)\s*[:\-]\s*(.+)",
    "quantity": r"(?:consumption|quantity|units?\s*consumed|volume|qty)\s*[:\-]?\s*([\d,]+(?:\.\d+)?)\s*([A-Za-z³]+)?",
    "amount_bdt": r"(?:total\s*(?:amount|payable|due)|amount\s*payable|net\s*payable)\s*(?:\(bdt\)|bdt|tk\.?|৳)?\s*[:\-]?\s*(?:bdt|tk\.?|৳)?\s*([\d,]+(?:\.\d+)?)",
}


def parse_text(text: str, doc_name: str, sha: str) -> BillRecord:
    rec = BillRecord(doc_name=doc_name, doc_sha256=sha, extraction_method="rules")
    found = 0
    for name, pat in FIELD_PATTERNS.items():
        m = re.search(pat, text, flags=re.IGNORECASE)
        if not m:
            continue
        found += 1
        rec.evidence[name] = m.group(0).strip()[:120]
        if name == "quantity":
            rec.quantity = _num(m.group(1))
            rec.unit = m.group(2)
        elif name == "amount_bdt":
            rec.amount_bdt = _num(m.group(1))
        elif name == "period":
            rec.period = normalize_period(m.group(1))
        else:
            setattr(rec, name, m.group(1).strip())
    rec.activity = infer_activity(text)
    rec.extraction_confidence = round(found / len(FIELD_PATTERNS), 2)
    return finalize(rec)


def extract_pdf_rules(path_or_bytes, doc_name: str | None = None) -> BillRecord:
    data = Path(path_or_bytes).read_bytes() if isinstance(path_or_bytes, (str, Path)) else path_or_bytes
    name = doc_name or (Path(path_or_bytes).name if isinstance(path_or_bytes, (str, Path)) else "upload.pdf")
    import io
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        text = "\n".join((p.extract_text() or "") for p in pdf.pages)
    if not text.strip():
        rec = BillRecord(doc_name=name, doc_sha256=sha256_bytes(data))
        rec.issues.append("No text layer found (scanned image). Set ANTHROPIC_API_KEY to enable C-Trace vision extraction.")
        return rec
    return parse_text(text, name, sha256_bytes(data))


# ---------------------------------------------------------------- LLM path
LLM_SYSTEM = """You are the document-reading component of C-Trace, a carbon MRV system.
Read the utility bill or fuel receipt (it may be in English or Bangla) and return ONLY a JSON object:
{"activity": "natural_gas" | "grid_electricity" | "diesel" | null,
 "supplier": string|null, "account_no": string|null, "bill_no": string|null,
 "period": "YYYY-MM"|null, "quantity": number|null, "unit": string|null,
 "amount_bdt": number|null,
 "evidence": {field: "exact text you read it from"}, "confidence": 0..1}
Rules: copy numbers exactly as printed (convert Bangla digits to ASCII digits); never estimate,
never compute emissions, use null when a field is not visible. "quantity" is the billed consumption
for the period, not a meter reading."""


def extract_with_llm(data: bytes, doc_name: str, media_type: str) -> BillRecord:
    import anthropic

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    b64 = base64.standard_b64encode(data).decode()
    block: dict[str, Any]
    if media_type == "application/pdf":
        block = {"type": "document", "source": {"type": "base64", "media_type": media_type, "data": b64}}
    else:
        block = {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": b64}}
    msg = client.messages.create(
        model=config.CTRACE_EXTRACTION_MODEL,
        max_tokens=800,
        system=LLM_SYSTEM,
        messages=[{"role": "user", "content": [block, {"type": "text", "text": "Extract the C-Trace JSON."}]}],  # type: ignore[misc, list-item]
    )
    raw = "".join(getattr(b, "text", "") for b in msg.content)
    m = re.search(r"\{.*\}", raw, flags=re.DOTALL)
    payload = json.loads(m.group(0)) if m else {}
    rec = BillRecord(doc_name=doc_name, doc_sha256=sha256_bytes(data), extraction_method=f"llm:{config.CTRACE_EXTRACTION_MODEL}")
    for k in ("activity", "supplier", "account_no", "bill_no", "unit"):
        setattr(rec, k, payload.get(k))
    rec.period = normalize_period(str(payload.get("period") or "")) if payload.get("period") else None
    for k in ("quantity", "amount_bdt"):
        v = payload.get(k)
        rec.__setattr__(k, _num(str(v)) if v not in (None, "") else None)
    rec.evidence = payload.get("evidence") or {}
    rec.extraction_confidence = float(payload.get("confidence") or 0.0)
    return finalize(rec)


# ---------------------------------------------------------------- shared validation
def finalize(rec: BillRecord) -> BillRecord:
    if rec.activity not in ACTIVITIES:
        rec.issues.append("Could not determine bill type (gas / electricity / diesel).")
        rec.activity = None
    unit, mult = normalize_unit(rec.unit, rec.activity)
    if rec.quantity is not None:
        rec.quantity = round(rec.quantity * mult, 4)
    rec.unit = unit
    if rec.activity and unit and unit != CANONICAL_UNIT[rec.activity]:
        rec.issues.append(f"Unit '{unit}' does not match a {rec.activity} bill.")
    if rec.quantity is None:
        rec.issues.append("Consumption quantity missing.")
    elif rec.quantity <= 0:
        rec.issues.append("Consumption quantity is zero or negative.")
    if rec.period is None:
        rec.issues.append("Billing period missing.")
    if rec.amount_bdt and rec.quantity and rec.activity in config.TARIFF_RANGES_BDT:
        lo, hi = config.TARIFF_RANGES_BDT[rec.activity]
        implied = rec.amount_bdt / rec.quantity
        rec.evidence["implied_tariff_bdt_per_unit"] = round(implied, 2)
        if not (lo <= implied <= hi):
            rec.issues.append(
                f"Implied tariff {implied:,.2f} BDT/{unit} is outside the expected {lo:.0f}–{hi:.0f} range "
                "→ quantity or amount may be mis-read."
            )
    return rec


def extract_document(data: bytes, doc_name: str, media_type: str = "application/pdf") -> BillRecord:
    """Entry point used by the app and the API."""
    if config.llm_enabled():
        try:
            return extract_with_llm(data, doc_name, media_type)
        except Exception as exc:  # fall back to offline path, but say so
            rec = extract_pdf_rules(data, doc_name) if media_type == "application/pdf" else BillRecord(doc_name, sha256_bytes(data))
            rec.issues.append(f"LLM extraction failed ({type(exc).__name__}); used offline parser.")
            return rec
    if media_type != "application/pdf":
        rec = BillRecord(doc_name=doc_name, doc_sha256=sha256_bytes(data))
        rec.issues.append("Photo bills need the LLM path. Set ANTHROPIC_API_KEY, or upload a PDF.")
        return rec
    return extract_pdf_rules(data, doc_name)
