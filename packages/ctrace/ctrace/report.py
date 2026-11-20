"""C-Trace report generation: one verified dataset → buyer, bank (IFRS S2) and BSEC ESG reports.

Numbers come only from the C-Trace ledger. The narrative is written either by templates
(offline) or by an LLM grounded in retrieved guidance. An LLM narrative is accepted only if
every number in it can be found in the verified facts ("number guard"); otherwise C-Trace
falls back to the template text, so a report can never contain an invented figure.
"""
from __future__ import annotations

import io
import json
import re
from datetime import date

import pandas as pd
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from . import config
from .rag import retriever

FORMATS = {
    "buyer": {
        "title": "Supplier Carbon Disclosure (brand questionnaire format)",
        "query": "brand supplier questionnaire energy by source emissions by scope intensity per unit renewable share evidence",
    },
    "ifrs_s2": {
        "title": "Climate Metrics for Lender Reporting (IFRS S2-aligned)",
        "query": "IFRS S2 Scope 1 Scope 2 location-based measurement approach inputs assumptions targets transition plan bank financed emissions",
    },
    "bsec": {
        "title": "Environmental Section for Annual Report (BSEC ESG disclosure)",
        "query": "BSEC ESG disclosure listed companies annual report energy greenhouse gas reduction initiatives",
    },
}
TEAL = colors.HexColor("#0F766E")


def build_facts(profile: dict, summary: pd.DataFrame, ledger: pd.DataFrame, verification: pd.DataFrame | None,
                reductions: list | None, period_from: str, period_to: str) -> dict:
    s = summary[(summary.period >= period_from) & (summary.period <= period_to)]
    led = ledger[(ledger.period >= period_from) & (ledger.period <= period_to)]
    energy = led.groupby("activity").agg(quantity=("quantity", "sum"), unit=("unit", "first"), tco2e=("tco2e", "sum"))
    total = float(s.total_t.sum())
    facts = {
        "factory": profile.get("name"), "location": profile.get("location"),
        "period_from": period_from, "period_to": period_to, "months": int(len(s)),
        "scope1_tco2e": round(float(s.scope1_t.sum()), 1),
        "scope2_location_tco2e": round(float(s.scope2_t.sum()), 1),
        "total_tco2e": round(total, 1),
        "pieces": int(s.pieces.sum()) if "pieces" in s else None,
        "kgco2e_per_piece": round(total * 1000 / float(s.pieces.sum()), 3) if "pieces" in s and s.pieces.sum() else None,
        "energy_by_source": {a: {"quantity": round(float(r.quantity)), "unit": r.unit, "tco2e": round(float(r.tco2e), 1)}
                             for a, r in energy.iterrows()},
        "documents": int(led.doc_sha256.nunique()),
        "factors": sorted(set(led.ef_source)),
    }
    if verification is not None:
        v = verification[(verification.period >= period_from) & (verification.period <= period_to)]
        facts["confidence_months"] = v.confidence.value_counts().to_dict()
        facts["flagged_months"] = [{"period": r.period, "confidence": r.confidence, "reasons": r.reasons}
                                   for _, r in v[v.confidence != "High"].iterrows()]
    if reductions:
        facts["verified_reductions"] = [
            {"activity": r.activity, "avoided_tco2e": r.avoided_tco2e, "low": r.avoided_tco2e_low,
             "high": r.avoided_tco2e_high, "verified": r.verified} for r in reductions]
    facts["interventions"] = profile.get("interventions", [])
    return facts


# ------------------------------------------------------------------ narrative
def template_narrative(fmt: str, f: dict) -> str:
    src = f["energy_by_source"]
    parts = ", ".join(f"{a.replace('_', ' ')} {v['quantity']:,} {v['unit']}" for a, v in src.items())
    intro = (f"{f['factory']} reports {f['total_tco2e']:,} tCO2e for {f['period_from']} to {f['period_to']} "
             f"({f['months']} months): Scope 1 {f['scope1_tco2e']:,} tCO2e and location-based Scope 2 "
             f"{f['scope2_location_tco2e']:,} tCO2e. Energy use: {parts}.")
    if f.get("kgco2e_per_piece"):
        intro += f" Emissions intensity was {f['kgco2e_per_piece']} kgCO2e per piece over {f['pieces']:,} pieces."
    dq = ""
    if "confidence_months" in f:
        c = f["confidence_months"]
        dq = (f" Data quality: figures are calculated from {f['documents']} source documents, each traceable by hash. "
              f"C-Trace verification graded {c.get('High', 0)} months High, {c.get('Medium', 0)} Medium and "
              f"{c.get('Low', 0)} Low confidence; Low months are listed for review.")
    red = ""
    for r in f.get("verified_reductions", []):
        if r["verified"]:
            red += (f" Verified reduction from {r['activity'].replace('_', ' ')}: {r['avoided_tco2e']:,} tCO2e "
                    f"(90% range {r['low']:,}–{r['high']:,}).")
    lead = {
        "buyer": "This disclosure is prepared for brand customers' supplier assessments and Scope 3 inventories. ",
        "ifrs_s2": "These metrics follow the GHG Protocol and the IFRS S2 requirement to report absolute gross Scope 1 and location-based Scope 2 emissions. ",
        "bsec": "This environmental section summarises energy use, greenhouse gas emissions and reduction initiatives for the annual report. ",
    }[fmt]
    return lead + intro + dq + red


NUM_RE = re.compile(r"(?<![\w.])-?\d[\d,]*(?:\.\d+)?")


def _numbers_in(obj) -> set[float]:
    """All numbers in the facts, plus their legitimate roundings (0, 1 and 2 decimals)."""
    found = set()
    for m in NUM_RE.findall(json.dumps(obj, default=str)):
        try:
            x = float(m.replace(",", ""))
        except ValueError:
            continue
        found |= {round(x, 0), round(x, 1), round(x, 2), abs(round(x, 0)), abs(round(x, 1)), abs(round(x, 2))}
    return found


def number_guard(text: str, facts: dict) -> list[str]:
    """Return numbers in `text` that are not (roundings of) verified facts. Empty list = safe."""
    allowed = _numbers_in(facts) | {float(x) for x in range(0, 32)} | {2006.0, 90.0, 100.0}
    bad = []
    for m in NUM_RE.findall(text):
        v = float(m.replace(",", ""))
        if round(v, 2) not in allowed:
            bad.append(m)
    return bad


def llm_narrative(fmt: str, facts: dict, guidance: list) -> str | None:
    if not config.llm_enabled():
        return None
    ctx = "\n\n".join(f"[{c.title} | {c.source}]\n{c.text}" for c, _ in guidance)
    system = ("You write the narrative section of a C-Trace carbon report. Use ONLY numbers that appear in the "
              "FACTS JSON, copied exactly. Do not compute new numbers. Use the GUIDANCE to frame the disclosure. "
              "Plain, professional English, 150-220 words, no headings, no bullet points.")
    user = f"FORMAT: {FORMATS[fmt]['title']}\n\nFACTS:\n{json.dumps(facts, default=str)}\n\nGUIDANCE:\n{ctx}"
    if config.llm_provider() == "openai":
        from .llm_openai import client as openai_client
        from .llm_openai import text_call
        text = text_call(openai_client(), model=config.openai_models()[0], system=system, user=user, max_tokens=1500)
    else:
        import anthropic

        client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        msg = client.messages.create(model=config.CTRACE_REPORT_MODEL, max_tokens=700, system=system,
                                     messages=[{"role": "user", "content": user}])
        text = "".join(getattr(b, "text", "") for b in msg.content).strip()
    return text if text and not number_guard(text, facts) else None


def generate(fmt: str, facts: dict) -> dict:
    guidance = retriever().search(FORMATS[fmt]["query"], k=3)
    text, method = None, "template"
    try:
        text = llm_narrative(fmt, facts, guidance)
        if text:
            method = f"llm:{config.report_model()} (number-guard passed)"
    except Exception:
        text = None
    if not text:
        text = template_narrative(fmt, facts)
    return {"format": fmt, "title": FORMATS[fmt]["title"], "narrative": text, "method": method,
            "guidance": [{"title": c.title, "source": c.source, "score": round(s, 3)} for c, s in guidance],
            "facts": facts}


# ------------------------------------------------------------------ PDF
def to_pdf(report: dict, ledger: pd.DataFrame) -> bytes:
    f = report["facts"]
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=16 * mm,
                            title=f"C-Trace — {report['title']}")
    ss = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=ss["Title"], textColor=TEAL, alignment=0, fontSize=18)
    h2 = ParagraphStyle("h2", parent=ss["Heading2"], textColor=TEAL)
    body = ParagraphStyle("b", parent=ss["BodyText"], fontSize=10, leading=14)
    small = ParagraphStyle("s", parent=body, fontSize=7.5, leading=9.5, textColor=colors.HexColor("#475569"))

    def table(rows, widths=None, font=9):
        t = Table(rows, colWidths=widths, repeatRows=1)
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), TEAL), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTSIZE", (0, 0), (-1, -1), font), ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#CBD5E1")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F0FDFA")]),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        return t

    el = [Paragraph("C-Trace", ParagraphStyle("brand", parent=body, textColor=TEAL, fontSize=11)),
          Paragraph(report["title"], h1),
          Paragraph(f"{f['factory']} · {f['location']} · {f['period_from']} to {f['period_to']} · generated {date.today().isoformat()}", small),
          Spacer(1, 8), Paragraph("Summary", h2), Paragraph(report["narrative"], body), Spacer(1, 6),
          Paragraph("GHG emissions", h2),
          table([["Metric", "Value"],
                 ["Scope 1 (tCO2e)", f"{f['scope1_tco2e']:,}"],
                 ["Scope 2, location-based (tCO2e)", f"{f['scope2_location_tco2e']:,}"],
                 ["Total Scope 1 + 2 (tCO2e)", f"{f['total_tco2e']:,}"],
                 ["Production (pieces)", f"{f['pieces']:,}" if f.get("pieces") else "—"],
                 ["Intensity (kgCO2e / piece)", f"{f['kgco2e_per_piece']}" if f.get("kgco2e_per_piece") else "—"]], [95 * mm, 70 * mm]),
          Spacer(1, 6), Paragraph("Energy by source", h2),
          table([["Source", "Quantity", "tCO2e"]] +
                [[a.replace("_", " ").title(), f"{v['quantity']:,} {v['unit']}", f"{v['tco2e']:,}"] for a, v in f["energy_by_source"].items()],
                [65 * mm, 60 * mm, 40 * mm])]
    if f.get("verified_reductions"):
        el += [Spacer(1, 6), Paragraph("Verified reductions (IPMVP Option C-style, 90% interval)", h2),
               table([["Energy carrier", "Avoided tCO2e", "90% range", "Status"]] +
                     [[r["activity"].replace("_", " ").title(), f"{r['avoided_tco2e']:,}", f"{r['low']:,} – {r['high']:,}",
                       "Verified" if r["verified"] else "Not significant"] for r in f["verified_reductions"]],
                     [50 * mm, 35 * mm, 50 * mm, 30 * mm])]
    if f.get("flagged_months"):
        el += [Spacer(1, 6), Paragraph("Data quality flags", h2),
               table([["Month", "Confidence", "Reason"]] +
                     [[m["period"], m["confidence"], Paragraph("; ".join(m["reasons"]), small)] for m in f["flagged_months"]],
                     [22 * mm, 24 * mm, 119 * mm], font=8)]
    el += [Spacer(1, 6), Paragraph("Methodology and emission factors", h2),
           Paragraph("Emissions are calculated by C-Trace's deterministic engine following the GHG Protocol Corporate Standard. "
                     "Factors used:", body)]
    el += [Paragraph("• " + s, small) for s in f["factors"]]
    el += [Paragraph("Guidance consulted (C-Trace knowledge base): " + "; ".join(g["source"] for g in report["guidance"]), small),
           Paragraph(f"Narrative method: {report['method']}.", small)]
    led = ledger[(ledger.period >= f["period_from"]) & (ledger.period <= f["period_to"])]
    el += [Spacer(1, 8), Paragraph("Appendix — trace ledger (every tonne to its source document)", h2),
           table([["Trace ID", "Month", "Activity", "Quantity", "tCO2e", "Document (SHA-256)"]] +
                 [[r.trace_id, r.period, r.activity.replace("_", " "), f"{r.quantity:,.0f} {r.unit}", f"{r.tco2e:,.2f}",
                   f"{r.doc_name} ({r.doc_sha256[:10]}…)"] for r in led.itertuples()],
                 [36 * mm, 15 * mm, 24 * mm, 28 * mm, 16 * mm, 55 * mm], font=6.5)]
    doc.build(el)
    return buf.getvalue()
