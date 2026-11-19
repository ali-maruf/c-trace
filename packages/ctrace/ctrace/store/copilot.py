"""C-Trace Copilot tools bound to one factory's data (org-scoped connection)."""
from __future__ import annotations

from typing import Any

from ..copilot import Tools
from ..rag import retriever
from . import reductions as red
from . import service
from . import verification as verif
from .db import Conn


def tools_for(conn: Conn, factory_id: str) -> Tools:
    service.get_factory(conn, factory_id)

    def get_summary(period: str = "") -> dict:
        s = service.summary(conn, factory_id, period or None, period or None)
        keys = ("natural_gas", "grid_electricity", "diesel", "total_t")
        months = [{k: m.get(k) for k in ("period", *keys, "scope1_t", "scope2_t", "kgco2e_per_piece")} for m in s["months"]]
        for prev, cur in zip(months, months[1:], strict=False):   # month-over-month change, computed here (code), not by the Copilot
            cur["change_vs_previous_t"] = {k: round((cur.get(k) or 0) - (prev.get(k) or 0), 4) for k in keys}
        return {"period_from": s["period_from"], "period_to": s["period_to"], "totals": s["totals"], "months": months}

    def get_ledger(period: str = "", activity: str = "") -> list[dict]:
        rows = service.ledger(conn, factory_id, period or None, activity or None)
        return [{k: r[k] for k in ("trace_id", "period", "activity", "quantity", "unit", "tco2e", "doc_name", "factor_id")} for r in rows]

    def get_verification(period: str = "") -> dict | None:
        try:
            v = verif.latest(conn, factory_id)
        except service.NotFound:
            return None
        res = [r for r in v["results"] if not period or r["period"] == period]
        return {"run": {"id": v["run"]["id"], "model_version": v["run"]["model_version"]}, "stale": v["stale"], "counts": v["counts"],
                "results": [{"period": r["period"], "confidence": r["confidence"], "reasons": r["reasons"], "z": r["z"]} for r in res]}

    def get_reductions() -> list[dict]:
        keep = ("id", "measure", "activity", "unit", "reporting_from", "reporting_to", "avoided", "low", "high", "tco2e", "bdt_saved",
                "verified", "status")
        return [{k: r.get(k) for k in keep} for r in red.list_current(conn, factory_id)]

    def search_guidance(query: str) -> list[dict[str, Any]]:
        return [{"id": c.id, "title": c.title, "source": c.source, "text": c.text, "score": s} for c, s in retriever().search(query, 3)]

    return {"get_summary": get_summary, "get_ledger": get_ledger, "get_verification": get_verification,
            "get_reductions": get_reductions, "search_guidance": search_guidance}
