"""Bangla PDFs whose fonts garble conjuncts: quantity and amount still come out, anchored on unit words and "টাকা"."""
from ctrace.extraction import parse_text

GARBLED = """নদী গ%াস ডি*বিউশন (কা/নিক)
গ"াস বিল
বিল নং: 661005
বিলের মাস: জানুয়ারি 2026
পূব;বতী> রিডিং: 1,04,212
বত;মান রিডিং: 1,31,727
ব"বBত গ"াস: 27,515 ঘনমিটার
Eমাট টাকা: 8,84,177.08
"""


def test_garbled_labels_still_give_quantity_and_amount():
    r = parse_text(GARBLED, "bn.pdf", "0" * 64)
    assert r.quantity == 27515 and r.amount_bdt == 884177.08 and r.period == "2026-01"
    assert r.field_confidence["quantity"] == 0.85  # meter readings agree (1,31,727 − 1,04,212)


def test_meter_readings_are_not_taken_as_consumption():
    r = parse_text(GARBLED.replace('ব"বBত গ"াস: 27,515 ঘনমিটার\n', ""), "bn.pdf", "0" * 64)
    assert r.quantity is None and "Consumption quantity missing." in r.issues
