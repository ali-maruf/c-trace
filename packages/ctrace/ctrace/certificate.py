"""C-Trace Reduction Certificate (PDF): one verified, reviewer-signed reduction proof.

The statement is deliberately labelled for what it is: a C-Trace verification statement, not a third-party
assurance engagement and not a carbon credit. Every figure comes from the stored proof.
"""
from __future__ import annotations

import io
from datetime import date

from reportlab.graphics.charts.legends import Legend
from reportlab.graphics.charts.lineplots import LinePlot
from reportlab.graphics.shapes import Drawing, Line, String
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

TEAL, INK, SLATE, RED = colors.HexColor("#0F766E"), colors.HexColor("#111827"), colors.HexColor("#475569"), colors.HexColor("#DC2626")
DISCLAIMER = "C-Trace verification statement — not a third-party assurance or carbon credit."
LABEL = {"natural_gas": "Natural gas", "grid_electricity": "Grid electricity", "diesel": "Diesel"}


def _table(rows: list[list], widths: list[float], font: float = 9) -> Table:
    t = Table(rows, colWidths=widths, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), TEAL), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), font), ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#CBD5E1")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F0FDFA")]), ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    return t


def _chart(curve: list[dict], unit: str, reporting_from: str) -> Drawing:
    d = Drawing(170 * mm, 62 * mm)
    lp = LinePlot()
    lp.x, lp.y, lp.width, lp.height = 14 * mm, 10 * mm, 150 * mm, 44 * mm
    pts = [(i, c) for i, c in enumerate(curve)]
    lp.data = [[(i, c["baseline"]) for i, c in pts], [(i, c["actual"]) for i, c in pts if c["actual"] is not None]]
    lp.lines[0].strokeColor, lp.lines[0].strokeDashArray, lp.lines[0].strokeWidth = SLATE, (3, 2), 1
    lp.lines[1].strokeColor, lp.lines[1].strokeWidth = TEAL, 1.6
    lp.xValueAxis.valueMin, lp.xValueAxis.valueMax = 0, max(len(curve) - 1, 1)
    lp.xValueAxis.valueSteps = list(range(0, len(curve), 3))
    lp.xValueAxis.labelTextFormat = lambda i: curve[int(i)]["period"] if 0 <= int(i) < len(curve) else ""
    lp.xValueAxis.labels.fontSize = lp.yValueAxis.labels.fontSize = 6.5
    lp.yValueAxis.labelTextFormat = lambda v: f"{v / 1000:,.0f}k"
    d.add(lp)
    split = next((i for i, c in enumerate(curve) if c["period"] >= reporting_from), None)
    if split is not None:
        xs = lp.x + lp.width * split / max(len(curve) - 1, 1)
        d.add(Line(xs, lp.y, xs, lp.y + lp.height, strokeColor=RED, strokeWidth=0.6, strokeDashArray=(2, 2)))
        d.add(String(xs + 2, lp.y + lp.height - 7, f"intervention ({reporting_from})", fontSize=6.5, fillColor=RED))
    leg = Legend()
    leg.x, leg.y, leg.fontSize, leg.alignment = 16 * mm, 60 * mm, 7, "right"
    leg.colorNamePairs = [(SLATE, f"Baseline model (what use would have been, {unit})"), (TEAL, f"Actual, from bills ({unit})")]
    leg.columnMaximum = 1
    d.add(leg)
    return d


def render(reduction: dict, factory: dict, signer: str | None, factor_sources: dict[str, dict]) -> bytes:
    r, f = reduction, factory
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=16 * mm,
                            title=f"C-Trace Reduction Certificate — {f['name']}", author="C-Trace")
    ss = getSampleStyleSheet()
    body = ParagraphStyle("b", parent=ss["BodyText"], fontSize=9.5, leading=13, textColor=INK)
    small = ParagraphStyle("s", parent=body, fontSize=7.5, leading=9.5, textColor=SLATE)
    h1 = ParagraphStyle("h1", parent=ss["Title"], textColor=TEAL, alignment=0, fontSize=18, spaceAfter=2)
    h2 = ParagraphStyle("h2", parent=ss["Heading2"], textColor=TEAL, fontSize=11.5, spaceBefore=8, spaceAfter=4)
    warn = ParagraphStyle("w", parent=body, textColor=RED, fontSize=10)
    act, unit = LABEL[r["activity"]], r["unit"]
    signed = r["status"] == "signed"

    el = [Paragraph("C-Trace · Trace every tonne.", ParagraphStyle("brand", parent=body, textColor=TEAL, fontSize=10)),
          Paragraph("Reduction Certificate", h1), Paragraph(DISCLAIMER, small), Spacer(1, 6)]
    if not signed:
        el.append(Paragraph("DRAFT — not signed by a reviewer. Not for distribution.", warn))
    el += [
        _table([["Factory", "Location", "Intervention", "Carrier"],
                [Paragraph(f["name"], small), Paragraph(f.get("location") or "—", small),
                 Paragraph(f"{r.get('measure') or '—'} (from {r.get('intervention_from') or r['reporting_from']})", small), act]],
               [48 * mm, 34 * mm, 64 * mm, 28 * mm]),
        Paragraph("Result", h2),
        _table([["", "Value", "90% interval"],
                [f"Avoided {act.lower()}", f"{r['avoided']:,.0f} {unit}", f"{r['low']:,.0f} – {r['high']:,.0f} {unit}"],
                ["Avoided emissions", f"{r['tco2e']:,.1f} tCO2e", f"{r['tco2e_low']:,.1f} – {r['tco2e_high']:,.1f} tCO2e"],
                ["Share of counterfactual use", f"{r['avoided'] / r['predicted']:.1%}" if r["predicted"] else "—", ""],
                ["Energy cost avoided", f"BDT {r['bdt_saved']:,.0f}" if r.get("bdt_saved") is not None else "—",
                 f"at the factory's median tariff BDT {r['tariff_bdt']:,.2f}/{unit}" if r.get("tariff_bdt") else ""],
                ["Status", "Verified" if r["verified"] else "Not verified", Paragraph(r.get("status_reason") or "", small)]],
               [52 * mm, 50 * mm, 72 * mm]),
        Paragraph("Baseline and reporting periods", h2),
        Paragraph(f"Baseline: {r['baseline_from']} to {r['baseline_to']} ({r['n_baseline']} months). Reporting: "
                  f"{r['reporting_from']} to {r['reporting_to']} ({r['n_reporting']} months). Months left out because C-Trace "
                  f"verification graded them Low for this carrier: "
                  f"{', '.join(r['excluded'].get('baseline', []) + r['excluded'].get('reporting', [])) or 'none'}.", body),
        Spacer(1, 4), _chart(r["curve"], unit, r["reporting_from"]),
        Paragraph("Method", h2),
        Paragraph("IPMVP Option C-style whole-facility regression. A baseline model of monthly use, "
                  f"{unit} = a + b × pieces produced + c × cooling degree days (NASA POWER at the factory where available), is fitted "
                  "by ordinary least squares on the baseline months and predicts what use would have been in the reporting months "
                  "without the intervention, adjusted for production and weather. Avoided use = predicted − actual; its 90% "
                  "interval is the prediction interval of the summed counterfactual. Emissions use the emission factor valid in "
                  "each reporting month.", body),
        Spacer(1, 4),
        _table([["Statistic", "Value", "ASHRAE Guideline 14 / practice", "Pass"]] +
               [[k.replace("_", " ").replace("cv rmse", "CV(RMSE)").replace("nmbe", "NMBE").replace("r2", "R²"),
                 f"{c['value']:.1%}" if k in ("cv_rmse", "nmbe") else (f"{c['value']:,}" if isinstance(c["value"], int | float) else c["value"]),
                 c["limit"], "✓" if c["pass"] else "✗"] for k, c in r["checks"].items()],
               [52 * mm, 34 * mm, 60 * mm, 28 * mm]),
        Paragraph(f"Coefficients: a = {r['coefficients']['intercept']:,.1f}, b = {r['coefficients']['per_piece']:.5f} {unit}/piece, "
                  f"c = {r['coefficients']['per_cdd']:,.2f} {unit}/CDD.", small),
        Paragraph("Emission factors", h2),
    ]
    el += [Paragraph(f"• {fid}: {factor_sources.get(fid, {}).get('source', '')} — {factor_sources.get(fid, {}).get('source_url', '')}", small)
           for fid in r["factor_ids"]]
    el += [Paragraph("Reviewer sign-off", h2),
           Paragraph(f"Signed by {signer or 'a C-Trace reviewer'} on {str(r['signed_at'])[:10]}: “{r.get('sign_comment')}”" if signed
                     else "Not signed.", body),
           Paragraph(f"Generated {date.today().isoformat()} by C-Trace from reduction {r['id']} (verification run {r.get('verification_run_id')}).", small),
           Paragraph("Appendix — source documents used", h2),
           _table([["Trace ID", "Document", "SHA-256"]] +
                  [[d["trace_id"], Paragraph(d["file_name"], small), d["doc_sha256"][:16] + "…"] for d in r["documents"]],
                  [52 * mm, 70 * mm, 52 * mm], font=7),
           Spacer(1, 6), Paragraph(DISCLAIMER + " It states what C-Trace's data and methods show; it is not an audit opinion, "
                                   "a verified carbon offset, or a claim to any registry.", small)]
    doc.build(el)
    return buf.getvalue()
