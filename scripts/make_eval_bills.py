"""Build the C-Trace extraction eval set: 36 FICTIONAL labelled bills in data/eval/bills/.

    uv run python scripts/make_eval_bills.py      # macOS only (Bangla shaping uses CoreText via swift)

The generated files and labels.json are committed, so evaluating needs no Mac. Every supplier, customer
and number is invented; each bill says it is a sample. Ground truth comes from the same spec that prints
the bill, so labels cannot drift from what is on the page.

Mix: English text PDFs in three layouts; English traps (subtotal/VAT before the total, a dropped digit,
a receipt with only a purchase date, a bill with a fictional person's name and phone); English phone
photos; Bangla phone photos; Bangla PDFs with a text layer; English labels with Bangla digits.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A5
from reportlab.pdfgen import canvas

from ctrace import config

OUT = config.DATA_DIR / "eval" / "bills"
SWIFT = Path(__file__).resolve().parent / "render_bill.swift"
BN = "০১২৩৪৫৬৭৮৯"
BN_MONTH = ["জানুয়ারি", "ফেব্রুয়ারি", "মার্চ", "এপ্রিল", "মে", "জুন", "জুলাই", "আগস্ট", "সেপ্টেম্বর", "অক্টোবর", "নভেম্বর", "ডিসেম্বর"]
EN_MONTH = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
EN_MONTH_LONG = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
SUPPLIER = {"natural_gas": "Nodi Gas Distribution (fictional)", "grid_electricity": "Alo Power Supply (fictional)",
            "diesel": "Shapla Fuel Station (fictional)"}
SUPPLIER_BN = {"natural_gas": "নদী গ্যাস ডিস্ট্রিবিউশন (কাল্পনিক)", "grid_electricity": "আলো পাওয়ার সাপ্লাই (কাল্পনিক)",
               "diesel": "শাপলা ফুয়েল স্টেশন (কাল্পনিক)"}
TARIFF = {"natural_gas": 31.5, "grid_electricity": 11.5, "diesel": 114.0}
PERSON_EN, PERSON_BN = "Rina Akter (fictional)", "রিনা আক্তার (কাল্পনিক)"
WATERMARK_EN = "SAMPLE - fictional bill for C-Trace evaluation. Not a real bill."
WATERMARK_BN = "নমুনা - C-Trace মূল্যায়নের জন্য কাল্পনিক বিল"


@dataclass
class Bill:
    file: str
    kind: str          # pdf_text | photo
    language: str      # en | bn | mixed
    layout: str
    activity: str
    period: str        # YYYY-MM (truth)
    quantity: float    # canonical unit (truth)
    unit: str          # canonical unit
    amount_bdt: float
    bill_no: str
    printed_quantity: str = ""
    expect_tariff_flag: bool = False
    person_names: list[str] = field(default_factory=list)
    lines: list[tuple[str, float, bool]] = field(default_factory=list)  # text, size, bold (render only)


def bn_digits(s: str) -> str:
    return "".join(BN[int(c)] if c.isdigit() else c for c in s)


def lakh(x: float, decimals: int = 2) -> str:
    whole, _, frac = f"{x:.{decimals}f}".partition(".")
    if len(whole) > 3:
        head, tail, parts = whole[:-3], whole[-3:], []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        whole = ",".join(([head] if head else []) + parts + [tail])
    return whole + (f".{frac}" if frac else "")


def canonical_unit(activity: str) -> str:
    return {"natural_gas": "m3", "grid_electricity": "kWh", "diesel": "litre"}[activity]


# ------------------------------------------------------------------ English layouts
def layout_a(b: Bill, q_printed: str, unit: str, person: bool = False) -> list[tuple[str, str]]:
    rows = [("Bill No", b.bill_no), ("Account No", "ACC-30" + b.bill_no[-4:]), ("Supplier", SUPPLIER[b.activity]),
            ("Billing Period", f"{EN_MONTH[int(b.period[5:]) - 1]} {b.period[:4]}"),
            ("Consumption", f"{q_printed} {unit}"), ("Total Amount Payable (BDT)", f"{b.amount_bdt:,.2f}")]
    if person:
        rows[2:2] = [("Customer name", PERSON_EN), ("Mobile", "01000-000000 (fictional)"),
                     ("Address", "House 0, Road 0, Fictional Lane, Gazipur")]
    return rows


def layout_b(b: Bill, q_printed: str, unit: str) -> list[tuple[str, str]]:
    energy = b.amount_bdt / 1.05
    return [("Invoice #", b.bill_no), ("Consumer ID", "C-" + b.bill_no[-5:]), ("Issued by", SUPPLIER[b.activity]),
            ("Bill month", f"{b.period[5:]}/{b.period[:4]}"), ("Units consumed", f"{q_printed} {unit}"),
            ("Energy charge", f"Tk. {lakh(energy)}"), ("VAT (5%)", f"Tk. {lakh(b.amount_bdt - energy)}"),
            ("Net payable", f"Tk. {lakh(b.amount_bdt)}")]


def layout_c(b: Bill, q_printed: str, unit: str, prev: float) -> list[tuple[str, str]]:
    return [("Bill number", b.bill_no), ("Customer No", "77-" + b.bill_no[-4:]), ("Distributor", SUPPLIER[b.activity]),
            ("Billing month", f"{EN_MONTH_LONG[int(b.period[5:]) - 1]}, {b.period[:4]}"),
            ("Previous reading", f"{prev:,.0f}"), ("Present reading", f"{prev + float(q_printed.replace(',', '')):,.0f}"),
            (f"Consumption ({unit})", q_printed), ("Total amount payable", f"Taka {b.amount_bdt:,.2f}")]


def layout_vat_trap(b: Bill, q_printed: str, unit: str) -> list[tuple[str, str]]:
    sub = b.amount_bdt / 1.05
    return [("Bill No", b.bill_no), ("Supplier", SUPPLIER[b.activity]),
            ("Billing Period", f"{EN_MONTH[int(b.period[5:]) - 1]} {b.period[:4]}"), ("Consumption", f"{q_printed} {unit}"),
            ("Subtotal (before VAT)", f"{sub:,.2f}"), ("VAT 5%", f"{b.amount_bdt - sub:,.2f}"),
            ("Total amount payable (BDT)", f"{b.amount_bdt:,.2f}")]


def layout_memo(b: Bill, q_printed: str, unit: str) -> list[tuple[str, str]]:
    return [("Cash Memo No", b.bill_no), ("Station", SUPPLIER[b.activity]),
            ("Date", f"14 {EN_MONTH[int(b.period[5:]) - 1]} {b.period[:4]}"), ("Fuel", "HSD (Diesel)"),
            ("Quantity", f"{q_printed} {unit}"), ("Rate", f"Tk {TARIFF['diesel']:.2f}/Ltr"),
            ("Total amount payable", f"Tk {b.amount_bdt:,.2f}")]


TITLE_EN = {"natural_gas": "NATURAL GAS BILL", "grid_electricity": "ELECTRICITY BILL", "diesel": "DIESEL FUEL RECEIPT"}
TITLE_BN = {"natural_gas": "গ্যাস বিল", "grid_electricity": "বিদ্যুৎ বিল", "diesel": "ডিজেল ক্রয় রশিদ"}


def layout_bn(b: Bill, person: bool, readings: float | None) -> list[str]:
    m = int(b.period[5:]) - 1
    unit_bn = {"natural_gas": "ঘনমিটার", "grid_electricity": "কিলোওয়াট-ঘণ্টা", "diesel": "লিটার"}[b.activity]
    qty_label = {"natural_gas": "ব্যবহৃত গ্যাস", "grid_electricity": "ব্যবহৃত বিদ্যুৎ", "diesel": "পরিমাণ"}[b.activity]
    lines = [SUPPLIER_BN[b.activity], TITLE_BN[b.activity], f"বিল নং: {bn_digits(b.bill_no)}",
             f"গ্রাহক নং: {bn_digits('441-' + b.bill_no[-3:])}", f"বিলের মাস: {BN_MONTH[m]} {bn_digits(b.period[:4])}"]
    if person:
        lines.append(f"গ্রাহকের নাম: {PERSON_BN}")
    if readings is not None:
        lines += [f"পূর্ববর্তী রিডিং: {bn_digits(lakh(readings, 0))}", f"বর্তমান রিডিং: {bn_digits(lakh(readings + b.quantity, 0))}"]
    lines += [f"{qty_label}: {bn_digits(lakh(b.quantity, 0))} {unit_bn}", f"মোট টাকা: {bn_digits(lakh(b.amount_bdt))}"]
    return lines


def layout_mixed(b: Bill, unit: str) -> list[tuple[str, str]]:
    return [("Bill No", bn_digits(b.bill_no)), ("Supplier", SUPPLIER[b.activity]),
            ("Billing Period", f"{EN_MONTH[int(b.period[5:]) - 1]} {bn_digits(b.period[:4])}"),
            ("Consumption", f"{bn_digits(lakh(b.quantity, 0))} {unit}"), ("Total Amount Payable (BDT)", bn_digits(lakh(b.amount_bdt)))]


# ------------------------------------------------------------------ rendering
def reportlab_pdf(path: Path, b: Bill, rows: list[tuple[str, str]]) -> None:
    c = canvas.Canvas(str(path), pagesize=A5)
    w, h = A5
    c.setFont("Helvetica-Bold", 14)
    c.drawString(28, h - 40, SUPPLIER[b.activity])
    c.setFont("Helvetica", 10)
    c.drawString(28, h - 56, TITLE_EN[b.activity])
    y = h - 95
    for label, value in rows:
        c.setFont("Helvetica-Bold", 10)
        c.drawString(28, y, f"{label}:")
        c.setFont("Helvetica", 10)
        c.drawString(200, y, value)
        y -= 21
    c.setFillColor(colors.HexColor("#B91C1C"))
    c.setFont("Helvetica-Bold", 8)
    c.drawString(28, 36, WATERMARK_EN)
    c.save()


def rows_to_lines(title: str, rows: list[tuple[str, str]], supplier: str, watermark: str) -> list[dict]:
    lines = [{"text": supplier, "size": 34, "bold": True, "gap": 6}, {"text": title, "size": 24, "gap": 30}]
    lines += [{"text": f"{label}: {value}", "size": 26, "gap": 14} for label, value in rows]
    lines.append({"text": watermark, "size": 20, "gap": 0})
    return lines


def photo_effects(png: Path, out: Path, rng: np.random.Generator) -> None:
    """Make a clean render look like a phone photo: tilt, slight blur, uneven light, noise, JPEG."""
    img = Image.open(png).convert("RGB")
    img = img.rotate(float(rng.uniform(-3.5, 3.5)), resample=Image.Resampling.BICUBIC, expand=True, fillcolor=(214, 210, 200))
    img = img.filter(ImageFilter.GaussianBlur(float(rng.uniform(0.4, 1.1))))
    a = np.asarray(img).astype(np.float32)
    h, w = a.shape[:2]
    light = np.linspace(float(rng.uniform(0.82, 0.92)), 1.0, w)[None, :, None] * np.linspace(1.0, float(rng.uniform(0.85, 0.95)), h)[:, None, None]
    a = np.clip(a * light + rng.normal(0, 6, a.shape), 0, 255).astype(np.uint8)
    img = Image.fromarray(a)
    if out.suffix == ".png":  # PNG bills look like greyscale flatbed scans
        img.convert("L").save(out, format="PNG", optimize=True)
    else:
        img.save(out, format="JPEG", quality=int(rng.integers(68, 86)))


def run_swift(jobs: list[dict]) -> None:
    if not shutil.which("swift"):
        raise SystemExit("C-Trace: generating Bangla eval bills needs macOS with swift (CoreText). The committed files are enough to run the eval.")
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(jobs, f, ensure_ascii=False)
    subprocess.run(["swift", str(SWIFT), f.name], check=True)


# ------------------------------------------------------------------ the set
def build() -> list[Bill]:
    rng = np.random.default_rng(20261120)
    bills: list[Bill] = []

    def new(file: str, kind: str, lang: str, layout: str, activity: str, period: str, qty: float, bill_no: str, **kw) -> Bill:
        amount = round(qty * TARIFF[activity] * float(rng.uniform(0.97, 1.03)), 2)
        b = Bill(file, kind, lang, layout, activity, period, round(qty, 4), canonical_unit(activity), amount, bill_no, **kw)
        bills.append(b)
        return b

    q = {"natural_gas": lambda: float(rng.integers(18_000, 42_000)), "grid_electricity": lambda: float(rng.integers(120_000, 260_000)),
         "diesel": lambda: float(rng.integers(300, 2_500))}
    periods = [f"{y}-{m:02d}" for y in (2025, 2026) for m in range(1, 13)]
    pick = lambda: periods[int(rng.integers(0, len(periods)))]  # noqa: E731

    # English text PDFs — three layouts
    for i, act in enumerate(["natural_gas", "grid_electricity", "diesel", "grid_electricity"]):
        new(f"en_a_{i + 1:02d}.pdf", "pdf_text", "en", "A", act, pick(), q[act](), f"A-{2025 + i}-{4100 + i}")
    for i, act in enumerate(["natural_gas", "grid_electricity", "grid_electricity", "natural_gas"]):
        new(f"en_b_{i + 1:02d}.pdf", "pdf_text", "en", "B", act, pick(), q[act](), f"INV{88010 + i}")
    for i, act in enumerate(["grid_electricity", "natural_gas", "grid_electricity", "natural_gas"]):
        new(f"en_c_{i + 1:02d}.pdf", "pdf_text", "en", "C", act, pick(), q[act](), f"C/{7310 + i}")
    # traps
    new("en_trap_vat.pdf", "pdf_text", "en", "trap_vat", "grid_electricity", pick(), q["grid_electricity"](), "V-5521")
    drop = new("en_trap_dropped_digit.pdf", "pdf_text", "en", "trap_dropped_digit", "natural_gas", pick(), q["natural_gas"](), "D-1189",
               expect_tariff_flag=True)
    new("en_trap_memo_date_only.pdf", "pdf_text", "en", "memo", "diesel", pick(), q["diesel"](), "4471")
    new("en_trap_personal.pdf", "pdf_text", "en", "trap_personal", "natural_gas", pick(), q["natural_gas"](), "A-2026-9001",
        person_names=["Rina", "Akter"])
    # English phone photos
    for i, (lay, act) in enumerate([("A", "grid_electricity"), ("B", "natural_gas"), ("C", "grid_electricity"), ("memo", "diesel")]):
        new(f"en_photo_{i + 1:02d}.jpg", "photo", "en", lay, act, pick(), q[act](), f"P-{3300 + i}")
    # Bangla phone photos (some with a fictional customer name)
    for i, act in enumerate(["natural_gas"] * 4 + ["grid_electricity"] * 4 + ["diesel"] * 2):
        ext = "png" if i % 3 == 0 else "jpg"
        new(f"bn_photo_{i + 1:02d}.{ext}", "photo", "bn", "bn", act, pick(), q[act](), f"{8870 + i}21",
            person_names=["রিনা", "আক্তার"] if i % 4 == 1 else [])
    # Bangla PDFs with a text layer
    for i, act in enumerate(["natural_gas", "grid_electricity", "diesel", "grid_electricity"]):
        new(f"bn_pdf_{i + 1:02d}.pdf", "pdf_text", "bn", "bn", act, pick(), q[act](), f"{6610 + i}05")
    # English labels, Bangla digits
    for i, act in enumerate(["natural_gas", "grid_electricity"]):
        new(f"mixed_{i + 1:02d}.pdf", "pdf_text", "mixed", "mixed", act, pick(), q[act](), f"{5120 + i}")

    # the dropped digit: printed quantity is one tenth of the real one; the amount is real
    drop.quantity = round(drop.quantity / 10)  # truth for extraction = what is printed
    return bills


def render(bills: list[Bill]) -> None:
    rng = np.random.default_rng(7)
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    tmp = Path(tempfile.mkdtemp(prefix="ctrace_eval_"))
    swift_jobs, photos = [], []
    for b in bills:
        unit_print = {"natural_gas": "m3", "grid_electricity": "kWh", "diesel": "Litre"}[b.activity]
        qty = b.quantity
        if b.layout == "C" and b.activity == "grid_electricity" and b.file.endswith("01.pdf"):
            unit_print, b.printed_quantity = "MWh", f"{qty / 1000:,.1f}"
            b.quantity = round(float(b.printed_quantity.replace(",", "")) * 1000, 4)
        elif b.layout == "C" and b.activity == "natural_gas" and b.file.endswith("02.pdf"):
            unit_print = "cft"
            cft = round(qty / 0.0283168)
            b.printed_quantity = f"{cft:,.0f}"
            b.quantity = round(cft * 0.0283168, 4)
        elif b.layout == "memo":
            unit_print, b.printed_quantity = "Ltr", f"{qty:,.0f}"
        else:
            b.printed_quantity = f"{qty:,.0f}"
        if b.expect_tariff_flag:
            b.printed_quantity = f"{b.quantity:,.0f}"
        rows: list[tuple[str, str]] | None = None
        if b.layout in ("A", "trap_dropped_digit"):
            rows = layout_a(b, b.printed_quantity, unit_print)
        elif b.layout == "trap_personal":
            rows = layout_a(b, b.printed_quantity, unit_print, person=True)
        elif b.layout == "B":
            rows = layout_b(b, b.printed_quantity, unit_print)
        elif b.layout == "C":
            rows = layout_c(b, b.printed_quantity, unit_print, prev=float(rng.integers(100_000, 900_000)))
        elif b.layout == "trap_vat":
            rows = layout_vat_trap(b, b.printed_quantity, unit_print)
        elif b.layout == "memo":
            rows = layout_memo(b, b.printed_quantity, unit_print)
        elif b.layout == "mixed":
            rows = layout_mixed(b, unit_print)
        if b.layout == "bn":
            text = layout_bn(b, bool(b.person_names), float(rng.integers(100_000, 900_000)) if b.activity != "diesel" else None)
            b.printed_quantity = bn_digits(lakh(b.quantity, 0))
            lines = [{"text": text[0], "size": 34, "bold": True, "gap": 6}, {"text": text[1], "size": 26, "gap": 30}]
            lines += [{"text": t, "size": 26, "gap": 14} for t in text[2:]] + [{"text": WATERMARK_BN, "size": 20, "gap": 0}]
        else:
            assert rows is not None
            lines = rows_to_lines(TITLE_EN[b.activity], rows, SUPPLIER[b.activity], WATERMARK_EN)
        b.lines = [(ln["text"], ln["size"], bool(ln.get("bold"))) for ln in lines]
        if b.kind == "pdf_text" and b.language == "en":
            reportlab_pdf(OUT / b.file, b, rows or [])
        elif b.kind == "pdf_text":
            swift_jobs.append({"out": str(OUT / b.file), "format": "pdf", "width": 595, "height": 842,
                               "lines": [{**ln, "size": ln["size"] * 0.55, "gap": ln.get("gap", 10) * 0.55} for ln in lines]})
        else:
            png = tmp / (Path(b.file).stem + ".png")
            swift_jobs.append({"out": str(png), "format": "png", "width": 1240, "height": 1100, "lines": lines})
            photos.append((png, OUT / b.file))
    run_swift(swift_jobs)
    for png, out in photos:
        photo_effects(png, out, rng)
    shutil.rmtree(tmp)


def main() -> None:
    bills = build()
    render(bills)
    labels = [{k: v for k, v in asdict(b).items() if k != "lines"} for b in bills]
    (OUT / "labels.json").write_text(json.dumps({
        "description": "C-Trace extraction eval set. All bills, suppliers and people are FICTIONAL. "
                       "Truth is what is printed on the bill (quantity in the canonical unit after unit conversion).",
        "generator": "scripts/make_eval_bills.py", "bills": labels}, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    size = sum(p.stat().st_size for p in OUT.iterdir())
    print(f"[C-Trace] wrote {len(bills)} eval bills to {OUT} ({size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
