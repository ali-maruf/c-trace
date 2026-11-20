"""C-Trace document extraction: utility bills / fuel receipts -> structured activity records.

Two paths, one `BillRecord` schema, one validation step (`finalize`):
  1. LLM path (`ctrace.extraction_llm`, when ANTHROPIC_API_KEY is set): Claude reads PDFs and photos,
     including Bangla bills, into a strict schema with per-field evidence. The model only *reads*.
  2. Offline path (this module): pdfplumber text layer + rule-based parser, English and Bangla labels.
"""
from __future__ import annotations

import hashlib
import io
import logging
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import pdfplumber

from . import config
from .textnorm import BN_MONTHS, EN_MONTHS, normalize_text, parse_number, redact_personal

ACTIVITIES = ("natural_gas", "grid_electricity", "diesel")
CANONICAL_UNIT = {"natural_gas": "m3", "grid_electricity": "kWh", "diesel": "litre"}

# unit alias -> (canonical unit, multiplier)
UNIT_ALIASES = {
    "m3": ("m3", 1.0), "m³": ("m3", 1.0), "cubic meter": ("m3", 1.0), "cubic metre": ("m3", 1.0),
    "cubic meters": ("m3", 1.0), "cubic metres": ("m3", 1.0), "scm": ("m3", 1.0), "sm3": ("m3", 1.0),
    "cft": ("m3", 0.0283168), "cubic feet": ("m3", 0.0283168), "mmbtu": ("m3", 1.055056 / 0.036),
    "kwh": ("kWh", 1.0), "kw-h": ("kWh", 1.0), "kilowatt-hour": ("kWh", 1.0), "kilowatt hour": ("kWh", 1.0),
    "mwh": ("kWh", 1000.0), "unit": ("kWh", 1.0), "units": ("kWh", 1.0),
    "litre": ("litre", 1.0), "liter": ("litre", 1.0), "litres": ("litre", 1.0), "liters": ("litre", 1.0),
    "l": ("litre", 1.0), "ltr": ("litre", 1.0), "ltrs": ("litre", 1.0),
    # Bangla
    "ঘনমিটার": ("m3", 1.0), "ঘন মিটার": ("m3", 1.0), "ঘনফুট": ("m3", 0.0283168),
    "কিলোওয়াট-ঘণ্টা": ("kWh", 1.0), "কিলোওয়াট ঘণ্টা": ("kWh", 1.0), "কিলোওয়াট-ঘন্টা": ("kWh", 1.0),
    "ইউনিট": ("kWh", 1.0), "লিটার": ("litre", 1.0),
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
    evidence: dict = field(default_factory=dict)          # field -> verbatim snippet (personal data removed)
    field_confidence: dict = field(default_factory=dict)  # field -> 0..1
    language: str | None = None                           # en | bn | mixed
    prompt_version: str | None = None                     # LLM path only
    attempts: list = field(default_factory=list)          # LLM path: one entry per model tried

    def as_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------- helpers
log = logging.getLogger("ctrace.extraction")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def normalize_period(text: str) -> str | None:
    """'Jan 2025', '2026-07', '09/2025', 'September, 2024', 'সেপ্টেম্বর ২০২৫' → 'YYYY-MM'."""
    t = normalize_text(text).strip()
    m = re.search(r"(20\d{2})[-/.](\d{1,2})(?!\d)", t)
    if m and 1 <= int(m.group(2)) <= 12:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}"
    m = re.search(r"([A-Za-z]{3,9})\.?[\s,\-'’]+(20\d{2})", t)
    if m and m.group(1)[:3].lower() in EN_MONTHS:
        return f"{int(m.group(2)):04d}-{EN_MONTHS[m.group(1)[:3].lower()]:02d}"
    for name, num in BN_MONTHS.items():
        m = re.search(re.escape(name) + r"[\s,\-']*(20\d{2})", t)
        if m:
            return f"{int(m.group(1)):04d}-{num:02d}"
    m = re.search(r"(?<!\d)(\d{1,2})[-/.](20\d{2})", t)
    if m and 1 <= int(m.group(1)) <= 12:
        return f"{int(m.group(2)):04d}-{int(m.group(1)):02d}"
    return None


def normalize_unit(raw_unit: str | None, activity: str | None) -> tuple[str | None, float]:
    if raw_unit:
        key = normalize_text(raw_unit).strip().strip("()[]").lower().replace(".", "").replace("^3", "3")
        key = re.sub(r"\s+", " ", key)
        if key in UNIT_ALIASES:
            return UNIT_ALIASES[key]
    if activity:
        return CANONICAL_UNIT[activity], 1.0
    return None, 1.0


def infer_activity(text: str) -> str | None:
    t = normalize_text(text).lower()
    scores = {
        "natural_gas": sum(k in t for k in ("gas", "m3", "m³", "cubic", "burner", "গ্যাস", "ঘনমিটার")),
        "grid_electricity": sum(k in t for k in ("electric", "kwh", "power", "meter reading", "বিদ্যুৎ", "বিদ্যুত", "কিলোওয়াট")),
        "diesel": sum(k in t for k in ("diesel", "litre", "liter", "fuel", "hsd", "ডিজেল", "লিটার", "generator")),
    }
    best = max(scores, key=lambda k: scores[k])
    return best if scores[best] > 0 else None


def detect_language(text: str) -> str:
    bn = sum("ঀ" <= ch <= "৿" for ch in text)
    latin = sum(ch.isascii() and ch.isalpha() for ch in text)
    if bn and bn >= 3 * max(latin, 1):
        return "bn"
    return "mixed" if bn else "en"


# ---------------------------------------------------------------- offline (rules) path
_NUM = r"([\d,]+(?:\.\d+)?)"
_UNIT_IN_BRACKETS = r"(?:\s*\(([^)]{1,20})\))?"
FIELD_PATTERNS = {
    "bill_no": r"(?:bill|invoice|receipt|memo)\s*(?:no|number|#)\.?\s*[:\-]?\s*([A-Z0-9][A-Z0-9\-/]*)"
               r"|(?:বিল|রশিদ)\s*(?:নং|নম্বর)\s*[:\-]?\s*([A-Z0-9][A-Z0-9\-/]*)",
    "account_no": r"(?:account|customer|consumer)\s*(?:no|id|number)\.?\s*[:\-]?\s*([A-Z0-9][A-Z0-9\-/]*)"
                  r"|(?:গ্রাহক|হিসাব)\s*(?:নং|নম্বর)\s*[:\-]?\s*([A-Z0-9][A-Z0-9\-/]*)",
    "supplier": r"(?:supplier|issued by|distributor|vendor)\s*[:\-]\s*(.+)|(?:সরবরাহকারী|প্রতিষ্ঠান)\s*[:\-]\s*(.+)",
    "period": r"(?:billing\s*(?:period|month)|bill\s*month|consumption\s*month|period|month)\s*[:\-]\s*(.+)"
              r"|(?:বিলের\s*মাস|মাস)\s*[:\-]\s*(.+)",
    "quantity": r"(?:consumption|quantity|units?\s*consumed|energy\s*consumed|volume|qty)" + _UNIT_IN_BRACKETS
                + r"\s*[:\-]?\s*" + _NUM + r"\s*([A-Za-z³]+(?:-[A-Za-z]+)?)?"
                r"|(?:ব্যবহৃত\s*(?:গ্যাস|বিদ্যুৎ|ইউনিট|জ্বালানি)?|ব্যবহার|পরিমাণ)\s*[:\-]?\s*" + _NUM + r"\s*([^\s\d,.;:]+(?:[\s-][^\s\d,.;:]+)?)?",
    "amount_bdt": r"(?:total\s*(?:amount|payable|due)|amount\s*payable|net\s*payable)\s*(?:\(\s*(?:bdt|tk\.?|taka)\s*\)|bdt|tk\.?|taka|৳)?"
                  r"\s*[:\-]?\s*(?:bdt|tk\.?|taka|৳)?\s*" + _NUM
                  + r"|(?:মোট\s*(?:টাকা|প্রদেয়|প্রদেয়\s*টাকা|বিল)|সর্বমোট)\s*(?:\(টাকা\))?\s*[:\-]?\s*(?:৳|টাকা)?\s*" + _NUM,
}
_MATCH_FIELDS = tuple(FIELD_PATTERNS)


def _groups(m: re.Match) -> list[str | None]:
    """Groups of whichever alternative matched (English or Bangla), in order."""
    return [g for g in m.groups()] if m else []


def parse_text(text: str, doc_name: str, sha: str) -> BillRecord:
    rec = BillRecord(doc_name=doc_name, doc_sha256=sha, extraction_method="rules")
    norm = normalize_text(text)
    found = 0
    for name, pat in FIELD_PATTERNS.items():
        m = re.search(pat, norm, flags=re.IGNORECASE)
        if not m:
            continue
        found += 1
        rec.evidence[name] = redact_personal(m.group(0).strip()[:120])
        rec.field_confidence[name] = 0.9
        g = [x for x in _groups(m)]
        if name == "quantity":
            # English alternative: (bracket unit, number, trailing unit); Bangla alternative: (number, unit)
            if g[1] is not None:
                rec.quantity, rec.unit = parse_number(g[1]), (g[0] or g[2])
            else:
                rec.quantity, rec.unit = parse_number(g[3]), g[4]
        elif name == "amount_bdt":
            rec.amount_bdt = parse_number(next(x for x in g if x))
        elif name == "period":
            rec.period = normalize_period(next(x for x in g if x))
        else:
            setattr(rec, name, next(x for x in g if x).strip())
    found += _bangla_fallbacks(norm, rec)
    rec.activity = infer_activity(text)
    rec.language = detect_language(text)
    rec.extraction_confidence = round(found / len(FIELD_PATTERNS), 2)
    return finalize(rec)


# Some Bangla PDF fonts don't map conjuncts back to Unicode, so labels such as "ব্যবহৃত" (used) or "মোট" (total) come
# out garbled (e.g. `ব"বBত`, `Eমাট`). The unit words and "টাকা" usually survive, so these fallbacks anchor on them.
_BN_QTY_UNIT = re.compile(r"[:\-]\s*" + _NUM + r"\s*(ঘনমিটার|কিলোওয়াট[^\s\d,;:]*|লিটার)(?=\s|$)", re.M)
_BN_READING = re.compile(r"রিডিং\s*[:\-]\s*" + _NUM)
_BN_TAKA = re.compile(r"টাকা\s*[:\-]\s*(?:৳\s*)?" + _NUM)


def _bangla_fallbacks(norm: str, rec: BillRecord) -> int:
    """Fill quantity / amount when the Bangla labels are unreadable. Lower confidence than a label match; the meter
    readings (current − previous) raise it when they agree. Returns how many fields were filled."""
    filled = 0
    if rec.quantity is None:
        hits = [m for m in _BN_QTY_UNIT.finditer(norm) if "রিডিং" not in norm[norm.rfind("\n", 0, m.start()) + 1:m.start()]]
        if hits:
            m = hits[0]
            rec.quantity, rec.unit = parse_number(m.group(1)), m.group(2)
            line = norm[norm.rfind("\n", 0, m.start()) + 1: (norm.find("\n", m.end()) + 1 or len(norm) + 1) - 1]
            rec.evidence["quantity"] = redact_personal(line.strip()[:120])
            readings = [r for r in (parse_number(x) for x in _BN_READING.findall(norm)) if r is not None]
            agree = len(readings) == 2 and rec.quantity is not None and abs(abs(readings[1] - readings[0]) - rec.quantity) < 0.5
            rec.field_confidence["quantity"] = 0.85 if agree else 0.6
            filled += 1
    if rec.amount_bdt is None:
        amounts = [a for a in (parse_number(x) for x in _BN_TAKA.findall(norm)) if a is not None]
        if amounts:
            rec.amount_bdt = max(amounts)
            rec.evidence["amount_bdt"] = f"টাকা: {rec.amount_bdt:,.2f}"
            rec.field_confidence["amount_bdt"] = 0.6
            filled += 1
    return filled


_PREBASE = "িেৈ"  # Bangla vowel signs drawn before their consonant
_CONSONANT = "ক-হড়ঢ়য়ৎ"


def fix_bangla_visual_order(text: str) -> str:
    """Many PDF generators store Bangla in *visual* order: a pre-base vowel sign comes before its consonant
    ("িবল" instead of "বিল"). Text in that order has vowel signs starting words, which logical text never has;
    only then do we move each pre-base sign back after the consonant it belongs to."""
    if not re.search(rf"(^|[\s:(\-])[{_PREBASE}]", text, flags=re.M):
        return text
    return re.sub(rf"([{_PREBASE}])([{_CONSONANT}](?:\u09cd[{_CONSONANT}])*)", r"\2\1", text)


def pdf_text(data: bytes) -> str:
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        return fix_bangla_visual_order("\n".join((p.extract_text() or "") for p in pdf.pages))


def extract_pdf_rules(path_or_bytes: str | Path | bytes, doc_name: str | None = None) -> BillRecord:
    data = Path(path_or_bytes).read_bytes() if isinstance(path_or_bytes, str | Path) else path_or_bytes
    name = doc_name or (Path(path_or_bytes).name if isinstance(path_or_bytes, str | Path) else "upload.pdf")
    text = pdf_text(data)
    if not text.strip():
        rec = BillRecord(doc_name=name, doc_sha256=sha256_bytes(data))
        rec.issues.append("No text layer found (scanned image). Configure a vision-capable LLM to read scans.")
        return rec
    return parse_text(text, name, sha256_bytes(data))


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


def cross_check(llm: BillRecord, rules: BillRecord) -> BillRecord:
    """Two independent readers of a text-layer PDF disagreeing on the quantity is worth a human look."""
    if (llm.quantity and rules.quantity and rules.extraction_confidence >= 0.8
            and abs(llm.quantity - rules.quantity) > 0.005 * max(llm.quantity, rules.quantity)):
        llm.issues.append(f"The text-layer parser read quantity {rules.quantity:,.2f} but the vision read "
                          f"{llm.quantity:,.2f} — please check the bill.")
        llm.extraction_confidence = min(llm.extraction_confidence, 0.6)
    return llm


def extract_document(data: bytes, doc_name: str, media_type: str = "application/pdf", *,
                     cache: Any = None, on_call: Callable[..., None] | None = None, client: Any = None,
                     offline: bool = False) -> BillRecord:
    """Entry point used by the app, the API and the worker. Uses the LLM when a key is configured and falls
    back to the offline parser (saying so) if the LLM path fails. `offline=True` forces the rules path."""
    if not offline and (config.llm_enabled() or client is not None):
        from .extraction_llm import extract_with_llm
        from .llm_openai import TextOnlyModel
        try:
            rec = extract_with_llm(data, doc_name, media_type, cache=cache, on_call=on_call, client=client)
        except TextOnlyModel as exc:  # e.g. a photo for a text-only model: the same outcome as offline, said plainly
            rec = extract_pdf_rules(data, doc_name) if media_type == "application/pdf" else BillRecord(doc_name, sha256_bytes(data))
            rec.issues.append(f"{exc} Upload a PDF with a text layer, or configure a vision model.")
            return rec
        except Exception as exc:  # fall back to the offline path, but say so
            rec = extract_pdf_rules(data, doc_name) if media_type == "application/pdf" else BillRecord(doc_name, sha256_bytes(data))
            log.warning("llm.extraction_failed", extra={"fields": {"doc": doc_name, "error": f"{type(exc).__name__}: {exc}"[:400]}})
            rec.issues.append(f"LLM extraction failed ({type(exc).__name__}: {str(exc)[:160]}); used offline parser.")
            return rec
        if media_type == "application/pdf":
            try:
                rules = extract_pdf_rules(data, doc_name)
            except Exception:  # an unreadable text layer is not an error for the vision path
                return rec
            rec = cross_check(rec, rules)
        return rec
    if media_type != "application/pdf":
        rec = BillRecord(doc_name=doc_name, doc_sha256=sha256_bytes(data))
        rec.issues.append("Photo bills need the LLM path (a vision-capable model), or upload a PDF.")
        return rec
    return extract_pdf_rules(data, doc_name)
