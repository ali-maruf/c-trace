"""C-Trace calculation engine. Deterministic, auditable, no LLM involvement.

Input:  confirmed activity records (one row per bill).
Output: an emissions ledger where every row keeps its source document hash, the factor used,
        the factor's provenance and the exact arithmetic, so any tonne can be traced back.
"""
from __future__ import annotations

import pandas as pd

from .factors import ENERGY_GJ_PER_UNIT, factor_for

LEDGER_COLUMNS = [
    "trace_id", "period", "activity", "scope", "quantity", "unit", "amount_bdt",
    "energy_gj", "ef_kgco2e_per_unit", "kgco2e", "tco2e", "factor_id", "ef_source", "calculation",
    "doc_name", "doc_sha256", "bill_no", "supplier",
]


def build_ledger(records: pd.DataFrame) -> pd.DataFrame:
    """records columns: period, activity, quantity, unit, amount_bdt, doc_name, doc_sha256, bill_no, supplier"""
    rows = []
    for _, r in records.iterrows():
        if pd.isna(r.get("quantity")) or not r.get("activity") or not r.get("period"):
            continue
        year = int(str(r["period"])[:4])
        ef = factor_for(r["activity"], year)
        q = float(r["quantity"])
        kg = q * ef.kgco2e_per_unit
        sha = str(r.get("doc_sha256") or "")
        rows.append({
            "trace_id": f"CT-{r['period']}-{r['activity'][:3].upper()}-{sha[:8]}",
            "period": r["period"],
            "activity": r["activity"],
            "scope": ef.scope,
            "quantity": q,
            "unit": ef.unit,
            "amount_bdt": r.get("amount_bdt"),
            "energy_gj": round(q * ENERGY_GJ_PER_UNIT[r["activity"]], 3),
            "ef_kgco2e_per_unit": ef.kgco2e_per_unit,
            "kgco2e": round(kg, 2),
            "tco2e": round(kg / 1000.0, 4),
            "factor_id": ef.factor_id,
            "ef_source": ef.source,
            "calculation": f"{q:,.2f} {ef.unit} × {ef.kgco2e_per_unit} kgCO2e/{ef.unit} = {kg:,.1f} kgCO2e",
            "doc_name": r.get("doc_name"),
            "doc_sha256": sha,
            "bill_no": r.get("bill_no"),
            "supplier": r.get("supplier"),
        })
    return pd.DataFrame(rows, columns=LEDGER_COLUMNS).sort_values(["period", "activity"]).reset_index(drop=True)


def monthly_summary(ledger: pd.DataFrame, production: pd.DataFrame | None = None) -> pd.DataFrame:
    piv = ledger.pivot_table(index="period", columns="activity", values="tco2e", aggfunc="sum").fillna(0.0)
    for a in ("natural_gas", "grid_electricity", "diesel"):
        if a not in piv:
            piv[a] = 0.0
    piv["scope1_t"] = piv["natural_gas"] + piv["diesel"]
    piv["scope2_t"] = piv["grid_electricity"]
    piv["total_t"] = piv["scope1_t"] + piv["scope2_t"]
    energy = ledger.pivot_table(index="period", values="energy_gj", aggfunc="sum")
    piv = piv.join(energy)
    if production is not None and len(production):
        p = production.set_index("period")[["pieces"]]
        piv = piv.join(p)
        piv["kgco2e_per_piece"] = (piv["total_t"] * 1000 / piv["pieces"]).round(4)
    return piv.reset_index()
