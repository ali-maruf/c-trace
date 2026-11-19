"""Text normalisation shared by every C-Trace extraction path: Bangla digits, months and units, and
removal of personal data from evidence snippets."""
from __future__ import annotations

import re
import unicodedata

BN_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")

# Bangla month names (common spellings), and English abbreviations.
BN_MONTHS = {
    "জানুয়ারি": 1, "জানুয়ারী": 1, "ফেব্রুয়ারি": 2, "ফেব্রুয়ারী": 2, "মার্চ": 3, "এপ্রিল": 4, "মে": 5, "জুন": 6,
    "জুলাই": 7, "আগস্ট": 8, "আগষ্ট": 8, "সেপ্টেম্বর": 9, "অক্টোবর": 10, "নভেম্বর": 11, "ডিসেম্বর": 12,
}
EN_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}


def normalize_text(text: str) -> str:
    """NFC-normalise (so the decomposed য় and precomposed য় compare equal) and convert Bangla digits to ASCII."""
    return unicodedata.normalize("NFC", text).translate(BN_DIGITS)


def parse_number(text: str | None) -> float | None:
    """'১০,০৯,৩৮৬.০০' → 1009386.0. Accepts lakh/crore grouping; returns None if no number is present."""
    if not text:
        return None
    t = normalize_text(text)
    m = re.search(r"-?\d[\d,]*(?:\.\d+)?", t)
    if not m:
        return None
    return float(m.group(0).replace(",", ""))


def digits_of(value: float) -> str:
    """Canonical digit string used to check a value against its evidence: 32044.0 → '32044', 2.5 → '25'."""
    s = f"{value:.4f}".rstrip("0").rstrip(".")
    return s.replace(".", "").lstrip("-")


def evidence_supports(value: float, evidence: str) -> bool:
    """True when the digits of `value` appear in the evidence (after Bangla digit conversion, ignoring
    grouping commas and spaces). Catches a model that 'reads' a number that is not on the page."""
    ev = re.sub(r"[,\s]", "", normalize_text(evidence or ""))
    ev_digits = re.sub(r"[^\d]", "", ev.replace(".", ""))
    return bool(ev) and digits_of(value) in ev_digits


# ------------------------------------------------------------------ personal data
# A label followed by a value that identifies a person. The value is replaced; the label is kept so
# reviewers can see that something was removed.
_PERSONAL_LABELS = (
    r"(?:customer|consumer|subscriber|account\s*holder|owner|contact)['’]?s?\s*name", r"name",
    r"(?:customer|consumer|billing|postal|home)?\s*address", r"mobile(?:\s*no\.?)?", r"phone(?:\s*no\.?)?",
    r"tel(?:ephone)?", r"e-?mail", r"nid(?:\s*no\.?)?", r"national\s*id",
    r"গ্রাহকের\s*নাম", r"নাম", r"ঠিকানা", r"মোবাইল", r"ফোন", r"ইমেইল", r"জাতীয়\s*পরিচয়পত্র",
)
# The label must start the text or follow a separator, so "Company name" / "Supplier name" are left alone.
_PERSONAL_RE = re.compile(r"(?i)(^|[\n;|,(]\s*)(" + "|".join(_PERSONAL_LABELS) + r")\s*[:：\-]\s*[^\n;|]*")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_D = "[0-9০-৯]"
_PHONE_RE = re.compile(rf"(?:\+?[8৮][8৮][0০]|[0০])[1১][3-9৩-৯]{_D}{{8}}")  # Bangladeshi mobile numbers


def redact_personal(text: str | None) -> str:
    """Remove person-identifying values from an evidence snippet; the snippet stays otherwise verbatim."""
    if not text:
        return ""
    t = _PERSONAL_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}: [redacted]", text)
    t = _EMAIL_RE.sub("[redacted]", t)
    return _PHONE_RE.sub("[redacted]", t)
