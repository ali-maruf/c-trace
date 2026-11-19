"""Reduction proofs (IPMVP Option C-style, ASHRAE Guideline 14 checks) on stored data, with reviewer sign-off."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from ..reduction import CARRIER_COLUMN, prove, windows
from .db import Conn
from .registry import factory_weather
from .service import NotFound, audit, clean, get_factory, production_frame, records_frame
from .verification import factory_profile, latest

CARRIER_WORDS = {"natural_gas": ("gas:", "natural_gas", "natural gas"),
                 "grid_electricity": ("grid electricity", "grid_electricity"), "diesel": ("diesel",)}


def excluded_months(verification: dict, activity: str) -> set[str]:
    """Months graded Low for a reason that concerns this carrier (its z-score or one of its documents)."""
    out = set()
    for r in verification["results"]:
        if r["confidence"] != "Low":
            continue
        reasons = " ".join(r["reasons"]).lower()
        if any(w in reasons for w in CARRIER_WORDS[activity]):
            out.add(r["period"])
    return out


def compute(conn: Conn, user_id: str | None, factory_id: str) -> list[dict]:
    """Recompute every intervention's proof from confirmed data; previous drafts are replaced, signed proofs kept."""
    f = get_factory(conn, factory_id)
    try:
        ver = latest(conn, factory_id)
    except NotFound as exc:
        raise ValueError("Run verification first: Low-confidence months must be known before a reduction can be proven") from exc
    if ver["stale"]:
        raise ValueError("Data changed since the last verification run; run verification again first")
    profile = factory_profile(conn, factory_id)
    ivs = [{**i, "affects": next((x["affects"] for x in f["interventions"] if x["id"] == i["id"]), [])} for i in profile["interventions"]]
    if not ivs:
        raise ValueError("Declare an intervention (measure and start month) to prove a reduction")
    recs = records_frame(conn, factory_id).dropna(subset=["period", "activity", "quantity"])
    prod = production_frame(conn, factory_id)
    periods = sorted(set(recs.period) | set(prod.period))
    weather = factory_weather(conn, f, periods).set_index("period")
    monthly = pd.DataFrame({"period": periods})
    for act, col in CARRIER_COLUMN.items():
        q = recs[recs.activity == act].groupby("period").quantity.sum()
        monthly[col] = monthly.period.map(q)
    monthly["pieces"] = monthly.period.map(prod.set_index("period").pieces)
    monthly["cdd"] = monthly.period.map(weather.cdd)
    ledger = pd.DataFrame(conn.execute(
        "select le.period, le.activity, le.factor_id, ef.kgco2e_per_unit, le.trace_id, le.doc_sha256, d.file_name "
        "from ledger_entries le join emission_factors ef on ef.id = le.factor_id "
        "join activity_records ar on ar.id = le.activity_record_id join documents d on d.id = ar.document_id "
        "where le.factory_id = %s", (factory_id,)).fetchall(),
        columns=["period", "activity", "factor_id", "kgco2e_per_unit", "trace_id", "doc_sha256", "file_name"])
    conn.execute("update reductions set status = 'superseded' where factory_id = %s and status = 'draft'", (factory_id,))
    out = []
    for w in windows(ivs, periods):
        led = ledger[ledger.activity == w.activity]
        factor_of = {r.period: (r.factor_id, float(r.kgco2e_per_unit)) for r in led.itertuples()}
        tariffs = recs[(recs.activity == w.activity) & recs.amount_bdt.notna() & (recs.quantity > 0)]
        tariff = float(np.median(tariffs.amount_bdt / tariffs.quantity)) if len(tariffs) else None
        proof = prove(monthly, w, excluded_months(ver, w.activity), factor_of, tariff)
        if proof is None:
            continue
        used = set(proof.curve and [c["period"] for c in proof.curve if c["used"]])
        docs = [{"trace_id": r.trace_id, "doc_sha256": r.doc_sha256, "file_name": r.file_name}
                for r in led[led.period.isin(used)].sort_values("period").itertuples()]
        d = proof.as_dict()
        row = conn.execute(
            """insert into reductions (org_id, factory_id, intervention_id, baseline_from, baseline_to, reporting_from, reporting_to,
                   activity, avoided, low, high, tco2e, r2, cv_rmse, verified, unit, n_baseline, n_reporting, nmbe, predicted, actual,
                   tco2e_low, tco2e_high, tariff_bdt, bdt_saved, checks, coefficients, excluded, curve, factor_ids, documents,
                   status_reason, verification_run_id, computed_by)
               values (ctrace_current_org(), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                       %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) returning id""",
            (factory_id, w.intervention_id, d["baseline_from"], d["baseline_to"], d["reporting_from"], d["reporting_to"], d["activity"],
             d["avoided"], d["avoided_low"], d["avoided_high"], d["tco2e"], d["r2"], d["cv_rmse"], d["verified"], d["unit"],
             d["n_baseline"], d["n_reporting"], d["nmbe"], d["predicted"], d["actual"], d["tco2e_low"], d["tco2e_high"],
             d["tariff_bdt"], d["bdt_saved"], json.dumps(d["checks"]), json.dumps(d["coefficients"]), json.dumps(d["excluded"]),
             json.dumps(d["curve"]), json.dumps(d["factor_ids"]), json.dumps(docs), d["status_reason"], ver["run"]["id"], user_id),
        ).fetchone()
        assert row is not None
        out.append(get(conn, str(row["id"])))
    audit(conn, user_id, "reduction.compute", "factory", factory_id, None,
          {"reductions": [{"id": r["id"], "activity": r["activity"], "verified": r["verified"]} for r in out]})
    return out


def get(conn: Conn, reduction_id: str) -> dict:
    row = conn.execute("""select r.*, i.measure, i.from_period as intervention_from, i.capex_bdt
                          from reductions r left join interventions i on i.id = r.intervention_id where r.id = %s""",
                       (reduction_id,)).fetchone()
    if row is None:
        raise NotFound("reduction")
    return clean(row)


def list_current(conn: Conn, factory_id: str) -> list[dict]:
    get_factory(conn, factory_id)
    ids = conn.execute("select id from reductions where factory_id = %s and status <> 'superseded' order by reporting_from, activity",
                       (factory_id,)).fetchall()
    return [get(conn, str(r["id"])) for r in ids]


def sign(conn: Conn, user_id: str, reduction_id: str, comment: str) -> dict:
    r = get(conn, reduction_id)
    if r["status"] != "draft":
        raise ValueError(f"Only a draft can be signed (this one is {r['status']})")
    if not r["verified"]:
        raise ValueError("A reduction that fails the ASHRAE checks cannot be signed as verified")
    if not comment or len(comment.strip()) < 3:
        raise ValueError("Sign-off needs a comment (what the reviewer checked)")
    conn.execute("update reductions set status = 'signed', signed_by = %s, signed_at = now(), sign_comment = %s where id = %s",
                 (user_id, comment.strip(), reduction_id))
    after = get(conn, reduction_id)
    audit(conn, user_id, "reduction.sign", "reduction", reduction_id, {"status": "draft"},
          {"status": "signed", "comment": comment.strip(), "tco2e": after["tco2e"]})
    return after


def signer_name(conn: Conn, user_id: str | None) -> str | None:
    if not user_id:
        return None
    row = conn.execute("select coalesce(display_name, email) n from users where id = %s", (user_id,)).fetchone()
    return row["n"] if row else None
