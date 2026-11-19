"""Property-based tests (hypothesis) for the normalisation every extraction path relies on."""
from hypothesis import given
from hypothesis import strategies as st

from ctrace.extraction import CANONICAL_UNIT, UNIT_ALIASES, BillRecord, finalize, normalize_period
from ctrace.textnorm import BN_MONTHS, EN_MONTHS, digits_of, evidence_supports, parse_number

BN = "০১২৩৪৫৬৭৮৯"
quantities = st.floats(min_value=0.01, max_value=1e9, allow_nan=False, allow_infinity=False).map(lambda x: round(x, 2))
years = st.integers(min_value=2000, max_value=2099)
months = st.integers(min_value=1, max_value=12)


def to_bangla(s: str) -> str:
    return "".join(BN[int(c)] if c.isdigit() else c for c in s)


def grouped(x: float, lakh: bool) -> str:
    whole, _, frac = f"{x:.2f}".partition(".")
    if lakh and len(whole) > 3:  # 12,34,567 (South Asian grouping)
        head, tail = whole[:-3], whole[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        whole = ",".join(([head] if head else []) + parts + [tail])
    elif len(whole) > 3:
        whole = f"{int(whole):,}"
    return f"{whole}.{frac}"


@given(quantities, st.booleans(), st.booleans())
def test_printed_numbers_parse_back_exactly(x, lakh, bangla):
    s = grouped(x, lakh)
    assert parse_number(to_bangla(s) if bangla else s) == x


@given(quantities, st.booleans())
def test_value_is_supported_by_its_own_printed_evidence(x, bangla):
    s = grouped(x, lakh=True)
    assert evidence_supports(x, f"Consumption: {to_bangla(s) if bangla else s} m3")


@given(quantities)
def test_a_tenfold_misread_is_not_supported(x):
    assert not evidence_supports(round(x * 10 + 1, 2), f"Consumption: {grouped(x, False)}")


@given(st.sampled_from(sorted(UNIT_ALIASES)), quantities)
def test_unit_aliases_convert_to_the_canonical_unit(alias, q):
    unit, mult = UNIT_ALIASES[alias]
    activity = next(a for a, u in CANONICAL_UNIT.items() if u == unit)
    rec = finalize(BillRecord("x", "0" * 64, activity=activity, period="2026-01", quantity=q, unit=alias.upper()))
    assert rec.unit == CANONICAL_UNIT[activity]
    assert abs(rec.quantity - round(q * mult, 4)) < 1e-6


@given(years, months)
def test_period_formats_agree(y, m):
    en = list(EN_MONTHS)[m - 1]
    bn = next(k for k, v in BN_MONTHS.items() if v == m)
    expected = f"{y:04d}-{m:02d}"
    for text in (f"{y}-{m:02d}", f"{m:02d}/{y}", f"{en.title()} {y}", f"{en.upper()}, {y}", f"{bn} {to_bangla(str(y))}"):
        assert normalize_period(text) == expected, text


def test_digits_of_is_canonical():
    assert digits_of(32044.0) == "32044" and digits_of(200.5) == "2005"
