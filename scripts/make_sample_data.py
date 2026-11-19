"""Create the C-Trace demo factory: 22 months of sample bills (PDF), production records and profile.

The factory is FICTIONAL. Bills are watermarked as sample data and use fictional suppliers.
Built-in story for the demo:
  * 2025-01 … 2025-12  baseline year
  * 2025-09            gas bill with a dropped digit (consumption mis-printed ÷10) → C-Trace should flag it
  * 2026-01            boiler economiser + steam-trap repair → ~12% less boiler gas
  * 2026-04            600 kWp rooftop solar comes online → less grid power
  * 2026-07            diesel receipt missing → C-Trace should flag the gap
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from reportlab.lib import colors
from reportlab.lib.pagesizes import A5
from reportlab.pdfgen import canvas

from ctrace import config
from ctrace.simulator import periods, simulate_month

PROFILE = {
    "factory_id": "DEMO-001",
    "name": "Nodi Knit Composite Ltd. (fictional demo factory)",
    "location": "Gazipur, Bangladesh",
    "factory_type": "knit_composite",
    "workers": 3200,
    "has_wet_processing": 1,
    "captive_share": 0.5,
    "solar_kwp_final": 600.0,
    "capacity_pieces": 3200 * 290.0,
    "e_mult": 1.0, "g_mult": 1.0,
    "base_kwh_per_worker": 40.0, "cool_kwh_per_cdd_worker": 0.3,
    "e_int": 0.45, "g_wet": 0.30, "g_dry": 0.03,
    "solar_start_idx": 10**6,
}
SUPPLIERS = {
    "natural_gas": ("Demo Gas Distribution Co.", "m3", 31.50, "GAS"),
    "grid_electricity": ("Demo Power Distribution Co.", "kWh", 11.50, "ELC"),
    "diesel": ("Demo Fuel Station", "litre", 104.00, "DSL"),
}
TITLES = {"natural_gas": "NATURAL GAS BILL", "grid_electricity": "ELECTRICITY BILL", "diesel": "DIESEL FUEL RECEIPT"}


def draw_bill(path: Path, activity: str, period: str, qty: float, amount: float, bill_no: str):
    supplier, unit, rate, _ = SUPPLIERS[activity]
    c = canvas.Canvas(str(path), pagesize=A5)
    w, h = A5
    c.setFillColor(colors.HexColor("#0F766E"))
    c.rect(0, h - 70, w, 70, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 15)
    c.drawString(28, h - 35, supplier)
    c.setFont("Helvetica", 10)
    c.drawString(28, h - 54, TITLES[activity])
    c.setFillColor(colors.black)
    y = h - 105
    month_label = pd.Period(period).strftime("%b %Y")
    fields = [
        ("Bill No", bill_no),
        ("Account No", "ACC-778120" if activity != "diesel" else "CUST-5521"),
        ("Customer", "Nodi Knit Composite Ltd."),
        ("Supplier", supplier),
        ("Billing Period", month_label),
        ("Consumption", f"{qty:,.0f} {unit}"),
        ("Rate", f"BDT {rate:,.2f} per {unit}"),
        ("Total Amount Payable (BDT)", f"{amount:,.2f}"),
    ]
    for label, value in fields:
        c.setFont("Helvetica-Bold", 10)
        c.drawString(28, y, f"{label}:")
        c.setFont("Helvetica", 10)
        c.drawString(190, y, value)
        y -= 22
    c.setFillColor(colors.HexColor("#B91C1C"))
    c.setFont("Helvetica-Bold", 9)
    c.drawString(28, 40, "SAMPLE BILL - C-Trace demo data. Fictional supplier and customer. Not a real bill.")
    c.save()


def main():
    rng = np.random.default_rng(2026)
    config.BILLS_DIR.mkdir(parents=True, exist_ok=True)
    for old in config.BILLS_DIR.glob("*.pdf"):
        old.unlink()
    rows, prod = [], []
    for idx, p in enumerate(periods("2025-01", "2026-10")):
        gain = 0.12 if p >= "2026-01" else 0.0
        kwp = 600.0 if p >= "2026-04" else 0.0
        r = simulate_month(PROFILE, p, idx, rng, gas_efficiency_gain=gain, solar_override_kwp=kwp)
        rows.append(r)
        prod.append({"period": p, "pieces": r["pieces"]})
        for activity, col in (("natural_gas", "gas_m3"), ("grid_electricity", "grid_kwh"), ("diesel", "diesel_l")):
            if activity == "diesel" and p == "2026-07":
                continue  # missing receipt
            qty = r[col]
            supplier, unit, rate, code = SUPPLIERS[activity]
            amount = round(qty * rate, 2)
            printed_qty = qty / 10 if (activity == "natural_gas" and p == "2025-09") else qty
            draw_bill(config.BILLS_DIR / f"{p}_{activity}.pdf", activity, p, printed_qty, amount,
                      f"{code}-{p.replace('-', '')}-{rng.integers(1000, 9999)}")
    pd.DataFrame(prod).to_csv(config.SAMPLES_DIR / "production.csv", index=False)
    pd.DataFrame(rows).to_csv(config.SAMPLES_DIR / "ground_truth_do_not_upload.csv", index=False)
    profile = {k: PROFILE[k] for k in ("factory_id", "name", "location", "factory_type", "workers", "has_wet_processing")}
    profile.update({"captive_gas_gen": 1, "solar": [{"from": "2026-04", "kwp": 600}],
                    "interventions": [{"from": "2026-01", "measure": "Boiler economiser + steam-trap repair"},
                                      {"from": "2026-04", "measure": "600 kWp rooftop solar"}]})
    (config.SAMPLES_DIR / "factory_profile.json").write_text(json.dumps(profile, indent=2))
    print(f"[C-Trace] wrote {len(list(config.BILLS_DIR.glob('*.pdf')))} sample bills to {config.BILLS_DIR}")


if __name__ == "__main__":
    main()
