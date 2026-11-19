"""Persisted C-Trace verification: run the Verifier for a factory, store results, resolve flags, monitor drift."""
from __future__ import annotations

import json
from functools import lru_cache

import numpy as np
import pandas as pd

from .. import config
from ..pipeline import run as run_pipeline
from ..verify import ACTIVITY_TARGETS, Verifier
from .db import Conn
from .registry import factory_weather
from .service import NotFound, audit, clean, get_factory, production_frame, records_frame

TARGETS = list(ACTIVITY_TARGETS.values())
RESOLUTIONS = ("confirmed_correct", "corrected", "evidence_requested", "not_an_issue")
DRIFT_ALERT = 0.30           # alert when the spread of z moves more than 30% from the training baseline
DRIFT_MIN_MONTHS = 60        # do not judge drift on too few months


@lru_cache(maxsize=4)
def verifier(version: str | None = None) -> Verifier:
    path = config.verifier_path(version)
    if not path.exists():
        raise FileNotFoundError(f"C-Trace Verifier {version or config.CTRACE_VERIFIER_VERSION} is not trained (make train)")
    return Verifier.load(path)


def factory_profile(conn: Conn, factory_id: str) -> dict:
    """The prototype pipeline's profile dict, from the factory row and its assets and interventions."""
    f = get_factory(conn, factory_id)
    return {
        "name": f["name"], "location": f.get("location"), "factory_type": f["factory_type"], "workers": int(f["workers"]),
        "has_wet_processing": int(bool(f["has_wet_processing"])), "captive_gas_gen": int(bool(f["captive_gas_gen"])),
        "solar": [{"from": a["from_period"], "kwp": float(a["capacity"])} for a in f["assets"] if a["kind"] == "solar"],
        "interventions": [{"from": i["from_period"], "measure": i["measure"], "id": i["id"], "affects": i.get("affects") or []}
                          for i in f["interventions"]],
    }


def run_verification(conn: Conn, user_id: str | None, factory_id: str) -> dict:
    v = verifier()
    f = get_factory(conn, factory_id)
    recs = records_frame(conn, factory_id)
    prod = production_frame(conn, factory_id)
    if recs.empty or prod.empty:
        raise ValueError("Verification needs confirmed or extracted bills and monthly production records")
    usable = recs.dropna(subset=["period", "activity"]).copy()
    # a human has already decided on confirmed and corrected bills: their extraction warnings are resolved
    reviewed = usable.status.isin(["confirmed", "corrected"])
    usable["issues"] = [[] if rv else list(iss) for rv, iss in zip(reviewed, usable.issues, strict=True)]
    periods = sorted(set(usable.period) | set(prod.period))
    weather = factory_weather(conn, f, periods)
    out = run_pipeline(usable, prod[["period", "pieces"]], factory_profile(conn, factory_id), v, weather=weather, explain=True)
    graded = out["verification"]
    as_of = conn.execute("select max(updated_at) t from activity_records where factory_id = %s", (factory_id,)).fetchone()
    src = weather.source.value_counts().to_dict()
    run = conn.execute(
        "insert into verification_runs (org_id, factory_id, model_version, created_by, inputs_as_of, weather_source) "
        "values (ctrace_current_org(), %s, %s, %s, %s, %s) returning *",
        (factory_id, v.meta.get("model", v.version), user_id, as_of["t"] if as_of else None, json.dumps(src)),
    ).fetchone()
    assert run is not None
    rows = []
    for r in graded.itertuples():
        detail = {}
        for t in TARGETS:
            z = getattr(r, f"z_{t}")
            detail[t] = {
                "z": None if pd.isna(z) else float(z),
                "expected": _num(getattr(r, f"expected_{t}")), "reported": _num(getattr(r, f"reported_{t}")),
                "expected_lo": _num(getattr(r, f"expected_lo_{t}", None)), "expected_hi": _num(getattr(r, f"expected_hi_{t}", None)),
                "drivers": getattr(r, f"drivers_{t}", None),
            }
        rows.append((run["id"], r.period, json.dumps(detail), float(r.iso_score), r.confidence, json.dumps(list(r.reasons))))
    with conn.cursor() as cur:
        cur.executemany("insert into verification_results (run_id, org_id, period, z, iso_score, confidence, reasons) "
                        "values (%s, ctrace_current_org(), %s, %s, %s, %s, %s)", rows)
    audit(conn, user_id, "verification.run", "factory", factory_id, None,
          {"run_id": str(run["id"]), "model": run["model_version"], "low": int((graded.confidence == "Low").sum())})
    return latest(conn, factory_id)


def _num(x: object) -> float | None:
    if x is None:
        return None
    try:
        f = float(x)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return None if np.isnan(f) else round(f, 1)


def latest(conn: Conn, factory_id: str) -> dict:
    get_factory(conn, factory_id)
    run = conn.execute("select * from verification_runs where factory_id = %s order by created_at desc limit 1", (factory_id,)).fetchone()
    if run is None:
        raise NotFound("verification run")
    results = conn.execute("select period, z, iso_score, confidence::text, reasons, resolved_by, resolved_at, resolution "
                           "from verification_results where run_id = %s order by period", (run["id"],)).fetchall()
    changed = conn.execute("select max(updated_at) t from activity_records where factory_id = %s", (factory_id,)).fetchone()
    stale = bool(changed and changed["t"] and run["inputs_as_of"] and changed["t"] > run["inputs_as_of"])
    counts = {g: sum(1 for r in results if r["confidence"] == g) for g in ("High", "Medium", "Low")}
    return {"run": clean(run), "stale": stale, "counts": counts, "results": [clean(r) for r in results]}


def resolve(conn: Conn, user_id: str, run_id: str, period: str, resolution: str, comment: str) -> dict:
    if resolution not in RESOLUTIONS:
        raise ValueError(f"resolution must be one of {RESOLUTIONS}")
    if not comment or len(comment.strip()) < 3:
        raise ValueError("A resolution needs a comment (it is stored in the audit log)")
    before = conn.execute("select * from verification_results where run_id = %s and period = %s for update", (run_id, period)).fetchone()
    if before is None:
        raise NotFound("verification result")
    after = conn.execute("update verification_results set resolved_by = %s, resolved_at = now(), resolution = %s "
                         "where run_id = %s and period = %s returning *",
                         (user_id, f"{resolution}: {comment.strip()}", run_id, period)).fetchone()
    assert after is not None
    audit(conn, user_id, "verification.resolve", "verification_result", f"{run_id}:{period}",
          {"resolution": before["resolution"]}, {"resolution": after["resolution"], "confidence": before["confidence"]})
    return clean(after)


# ------------------------------------------------------------------ drift (owner connection: fleet-wide aggregates)
def drift_check(owner_conn: Conn, version: str | None = None) -> dict:
    """Robust spread of z in every factory's latest run, per carrier, vs the clean-calibration baseline."""
    v = verifier(version)
    base = v.meta.get("drift_baseline_z") or {}
    model = v.meta.get("model", v.version)
    rows = owner_conn.execute(
        """select r.z from verification_results r
           join (select distinct on (factory_id) id from verification_runs where model_version = %s
                 order by factory_id, created_at desc) last on last.id = r.run_id""", (model,)).fetchall()
    n_f = owner_conn.execute("select count(distinct factory_id) n from verification_runs where model_version = %s", (model,)).fetchone()
    spread, ratio = {}, {}
    for t in TARGETS:
        z = np.array([r["z"][t]["z"] for r in rows if r["z"].get(t, {}).get("z") is not None], float)
        if len(z) >= DRIFT_MIN_MONTHS and base.get(t):
            s = float(1.4826 * np.median(np.abs(z - np.median(z))))
            spread[t], ratio[t] = round(s, 4), round(s / base[t], 3)
    alert = any(abs(r - 1) > DRIFT_ALERT for r in ratio.values())
    details = ("not enough months to judge drift" if not ratio else
               "; ".join(f"{t}: spread {spread[t]} vs baseline {base[t]} (×{ratio[t]})" for t in ratio))
    row = owner_conn.execute(
        "insert into model_drift_checks (model_version, n_months, n_factories, z_spread, ratio, alert, details) "
        "values (%s, %s, %s, %s, %s, %s, %s) returning *",
        (model, len(rows), int(n_f["n"]) if n_f else 0, json.dumps(spread), json.dumps(ratio), alert, details)).fetchone()
    assert row is not None
    return clean(row)


def drift_due(owner_conn: Conn, days: int = 7) -> bool:
    row = owner_conn.execute("select max(checked_at) t from model_drift_checks").fetchone()
    if row is None or row["t"] is None:
        return True
    due = owner_conn.execute("select %s < now() - make_interval(days => %s) due", (row["t"], days)).fetchone()
    return bool(due and due["due"])
