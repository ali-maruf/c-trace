"""C-Trace end-to-end pipeline: extracted bills + production + factory profile → ledger, verification."""
from __future__ import annotations

from collections import defaultdict

import numpy as np
import pandas as pd

from .calculate import build_ledger, monthly_summary
from .simulator import cdd, periods
from .verify import Verifier, grade_months

TARGET_OF = {"natural_gas": "gas_m3", "grid_electricity": "grid_kwh", "diesel": "diesel_l"}


def carrier_breakpoints(profile: dict) -> dict[str, list[str]]:
    """Regime starts per carrier: an intervention re-baselines only the carriers it affects (declared `affects`, else
    inferred from its description). Solar changes grid and diesel use, and captive-generation gas when there is one."""
    from .reduction import infer_affects

    out: dict[str, set[str]] = {t: set() for t in TARGET_OF.values()}
    for iv in profile.get("interventions", []):
        for act in iv.get("affects") or infer_affects(iv["measure"]):
            out[TARGET_OF[act]].add(iv["from"])
    for s in profile.get("solar", []):
        out["grid_kwh"].add(s["from"])
        out["diesel_l"].add(s["from"])
        if profile.get("captive_gas_gen"):
            out["gas_m3"].add(s["from"])
    return {t: sorted(v) for t, v in out.items()}


def solar_kwp_for(profile: dict, period: str) -> float:
    kwp = 0.0
    for s in profile.get("solar", []):
        if period >= s["from"]:
            kwp = float(s["kwp"])
    return kwp


def document_issues(records: pd.DataFrame, all_periods: list[str]) -> dict[str, list[str]]:
    issues: dict[str, list[str]] = defaultdict(list)
    for _, r in records.iterrows():
        for i in (r.get("issues") or []):
            if r.get("period"):
                issues[r["period"]].append(f"{r['doc_name']}: {i}")
    dup_hash = records[records.duplicated("doc_sha256", keep="first")]
    for _, r in dup_hash.iterrows():
        issues[r["period"]].append(f"Duplicate document uploaded twice: {r['doc_name']}")
    if "bill_no" in records:
        dup_bill = records[records.bill_no.notna() & records.duplicated("bill_no", keep="first")
                           & ~records.duplicated("doc_sha256", keep="first")]
        for _, r in dup_bill.iterrows():
            issues[r["period"]].append(f"Duplicate bill number {r['bill_no']} in {r['doc_name']} (possible double count)")
    present = set(zip(records.period, records.activity, strict=True))
    expected_activities = set(records.activity.dropna())
    for p in all_periods:
        for a in expected_activities:
            if (p, a) not in present:
                issues[p].append(f"Missing {a.replace('_', ' ')} bill for {p} — emissions for this month are incomplete")
    return dict(issues)


def run(records: pd.DataFrame, production: pd.DataFrame, profile: dict, verifier: Verifier | None = None,
        weather: pd.DataFrame | None = None, explain: bool = False) -> dict:
    """`weather`: optional (period, cdd) table, e.g. NASA POWER at the factory; default is the Dhaka climatology."""
    records = records.copy()
    records = records[records.period.notna() & records.activity.notna()]
    ledger = build_ledger(records.drop_duplicates("doc_sha256"))
    summary = monthly_summary(ledger, production)
    all_periods = periods(min(records.period), max(records.period))

    # monthly physical table for the verifier
    q = (records.drop_duplicates("doc_sha256")
         .pivot_table(index="period", columns="activity", values="quantity", aggfunc="sum"))
    monthly = pd.DataFrame({"period": all_periods})
    for a, col in TARGET_OF.items():
        monthly[col] = monthly.period.map(q[a]) if a in q else np.nan
    monthly = monthly.merge(production[["period", "pieces"]], on="period", how="left")
    monthly["month"] = monthly.period.str[5:7].astype(int)
    if weather is None and profile.get("lat") is not None and profile.get("lon") is not None:
        from .weather import monthly as weather_monthly  # NASA POWER at the factory, labelled climatology where missing
        weather = weather_monthly(float(profile["lat"]), float(profile["lon"]), all_periods)
    real_cdd = dict(zip(weather.period, weather.cdd, strict=True)) if weather is not None else {}
    monthly["cdd"] = monthly.period.map(lambda p: real_cdd.get(p, cdd(p)))
    monthly["factory_type"] = profile["factory_type"]
    monthly["workers"] = profile["workers"]
    monthly["has_wet_processing"] = profile["has_wet_processing"]
    monthly["captive_gas_gen"] = profile.get("captive_gas_gen", 0)
    monthly["solar_kwp"] = monthly.period.map(lambda p: solar_kwp_for(profile, p))

    issues = document_issues(records, all_periods)
    graded = None
    if verifier is not None:
        scorable = monthly.dropna(subset=["pieces"])
        breakpoints = carrier_breakpoints(profile)
        graded = grade_months(verifier.score_factory(scorable, breakpoints, explain=explain), issues)
    return {"ledger": ledger, "summary": summary, "monthly": monthly, "issues": issues,
            "verification": graded, "suggestions": tariff_corrections(records)}


def tariff_corrections(records: pd.DataFrame) -> list[dict]:
    """When a bill's implied tariff is far off the factory's own median tariff, the quantity was
    probably mis-printed or mis-read. Suggest quantity = amount ÷ median tariff for a human to confirm."""
    out = []
    df = records.dropna(subset=["quantity", "amount_bdt"]).copy()
    df = df[df.quantity > 0]
    df["tariff"] = df.amount_bdt / df.quantity
    for activity, g in df.groupby("activity"):
        med = g.tariff.median()
        for _, r in g.iterrows():
            ratio = r.tariff / med
            if ratio > 1.5 or ratio < 0.67:
                out.append({
                    "doc_name": r.doc_name, "period": r.period, "activity": activity,
                    "printed_quantity": r.quantity, "suggested_quantity": round(r.amount_bdt / med, 0),
                    "median_tariff": round(med, 2), "implied_tariff": round(r.tariff, 2),
                    "explanation": (f"Amount BDT {r.amount_bdt:,.0f} ÷ this factory's median tariff "
                                    f"{med:,.2f} BDT/unit ≈ {r.amount_bdt / med:,.0f}. Printed value looks off by ×{ratio:.1f}."),
                })
    return out
