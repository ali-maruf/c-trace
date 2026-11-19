"""C-Trace Reports from stored, verified data: buyer, IFRS S2 lender and BSEC formats; PDF/JSON/CSV; share links.

Numbers come only from the ledger, the latest verification run and stored reduction proofs (`ctrace.report.build_facts`).
A narrative from an LLM is kept only if it passes the number guard; otherwise the template is used.
Share links are read-only, expiring and revocable; only the SHA-256 of a token is stored, and each view is logged.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import secrets
from types import SimpleNamespace

import pandas as pd

from ..report import FORMATS, build_facts, generate, to_pdf
from .db import Conn
from .service import NotFound, audit, clean, get_factory, ledger, summary
from .verification import factory_profile

MAX_SHARE_DAYS = 90


def _frames(conn: Conn, factory_id: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    s = summary(conn, factory_id)
    months = pd.DataFrame(s["months"])
    led = pd.DataFrame(ledger(conn, factory_id))
    if not led.empty:
        led = led.rename(columns={"factor_source": "ef_source"})
    return months, led


def build(conn: Conn, user_id: str | None, factory_id: str, fmt: str, period_from: str, period_to: str) -> dict:
    if fmt not in FORMATS:
        raise ValueError(f"format must be one of {sorted(FORMATS)}")
    if period_from > period_to:
        raise ValueError("period_from is after period_to")
    months, led = _frames(conn, factory_id)
    if months.empty:
        raise ValueError("No emissions in the ledger yet")
    if "pieces" not in months:
        months["pieces"] = None
    profile = factory_profile(conn, factory_id)
    run = conn.execute("select id, model_version from verification_runs where factory_id = %s order by created_at desc limit 1",
                       (factory_id,)).fetchone()
    ver = None
    if run:
        ver = pd.DataFrame(conn.execute("select period, confidence::text as confidence, reasons from verification_results where run_id = %s",
                                        (run["id"],)).fetchall(), columns=["period", "confidence", "reasons"])
    reds = conn.execute(
        """select * from reductions where factory_id = %s and status <> 'superseded' and verified
           and reporting_from <= %s and reporting_to >= %s order by status = 'signed' desc, created_at desc""",
        (factory_id, period_to, period_from)).fetchall()
    seen, red_objs = set(), []
    for r in reds:
        if r["activity"] in seen:
            continue
        seen.add(r["activity"])
        red_objs.append(SimpleNamespace(activity=r["activity"], avoided_tco2e=float(r["tco2e"]), avoided_tco2e_low=float(r["tco2e_low"]),
                                        avoided_tco2e_high=float(r["tco2e_high"]), verified=True, signed=r["status"] == "signed"))
    facts = build_facts(profile, months, led, ver, red_objs or None, period_from, period_to)
    st = summary(conn, factory_id, period_from, period_to)
    facts["data_quality"] = {
        "records_by_status": st["data_status"]["records_by_status"],
        "human_reviewed_share": _reviewed_share(st["data_status"]["records_by_status"]),
        "verification_model": run["model_version"] if run else None,
    }
    if st["totals"] and st["totals"].get("scope2_statutory"):
        facts["scope2_statutory_tco2e"] = round(st["totals"]["scope2_statutory"]["scope2_statutory_tco2e"], 1)
    if red_objs:
        facts["reductions_signed"] = [r.activity for r in red_objs if r.signed]
    rep = generate(fmt, facts)
    row = conn.execute(
        "insert into reports (org_id, factory_id, format, period_from, period_to, facts, narrative, method, created_by) "
        "values (ctrace_current_org(), %s, %s, %s, %s, %s, %s, %s, %s) returning id",
        (factory_id, fmt, period_from, period_to, json.dumps({**rep["facts"], "_guidance": rep["guidance"]}, default=str),
         rep["narrative"], rep["method"], user_id)).fetchone()
    assert row is not None
    audit(conn, user_id, "report.create", "report", row["id"], None, {"format": fmt, "period_from": period_from, "period_to": period_to})
    return get(conn, str(row["id"]))


def _reviewed_share(by_status: dict) -> float | None:
    total = sum(by_status.values())
    return round((by_status.get("confirmed", 0) + by_status.get("corrected", 0)) / total, 3) if total else None


def get(conn: Conn, report_id: str) -> dict:
    row = conn.execute("select * from reports where id = %s", (report_id,)).fetchone()
    if row is None:
        raise NotFound("report")
    r = clean(row)
    r["title"] = FORMATS[r["format"]]["title"]
    r["shared"] = bool(r.pop("share_token_hash", None)) and r.get("revoked_at") is None
    r.pop("share_token", None)
    return r


def list_reports(conn: Conn, factory_id: str) -> list[dict]:
    get_factory(conn, factory_id)
    ids = conn.execute("select id from reports where factory_id = %s order by created_at desc limit 50", (factory_id,)).fetchall()
    return [get(conn, str(r["id"])) for r in ids]


def _report_dict(r: dict) -> dict:
    facts = {k: v for k, v in r["facts"].items() if k != "_guidance"}
    return {"format": r["format"], "title": r["title"], "narrative": r["narrative"], "method": r["method"],
            "guidance": r["facts"].get("_guidance", []), "facts": facts}


def pdf(conn: Conn, report_id: str) -> tuple[dict, bytes]:
    r = get(conn, report_id)
    _, led = _frames(conn, r["factory_id"])
    return r, to_pdf(_report_dict(r), led)


def as_json(conn: Conn, report_id: str) -> dict:
    r = get(conn, report_id)
    return {"report_id": r["id"], "generated_at": r["created_at"], **_report_dict(r),
            "disclaimer": "Generated by C-Trace from bills traceable by SHA-256; see the trace ledger for every figure."}


def ledger_csv(conn: Conn, report_id: str) -> str:
    r = get(conn, report_id)
    rows = [x for x in ledger(conn, r["factory_id"]) if r["period_from"] <= x["period"] <= r["period_to"]]
    cols = ["trace_id", "period", "activity", "scope", "quantity", "unit", "kgco2e", "tco2e", "factor_id", "factor_version",
            "kgco2e_per_unit", "factor_source", "factor_source_url", "calculation", "doc_name", "doc_sha256", "record_status"]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()


# ------------------------------------------------------------------ sharing
def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def share(conn: Conn, user_id: str, report_id: str, days: int, include_documents: bool) -> dict:
    if not 1 <= days <= MAX_SHARE_DAYS:
        raise ValueError(f"A share link lasts between 1 and {MAX_SHARE_DAYS} days")
    get(conn, report_id)
    token = secrets.token_urlsafe(32)
    row = conn.execute(
        """update reports set share_token_hash = %s, share_created_by = %s, share_created_at = now(), revoked_at = null,
               expires_at = now() + make_interval(days => %s), include_documents = %s where id = %s returning expires_at""",
        (token_hash(token), user_id, days, include_documents, report_id)).fetchone()
    assert row is not None
    audit(conn, user_id, "report.share", "report", report_id, None,
          {"expires_at": row["expires_at"].isoformat(), "include_documents": include_documents})
    return {"token": token, "path": f"/share/{token}", "expires_at": row["expires_at"].isoformat(), "include_documents": include_documents}


def revoke(conn: Conn, user_id: str, report_id: str) -> dict:
    get(conn, report_id)
    conn.execute("update reports set revoked_at = now() where id = %s", (report_id,))
    audit(conn, user_id, "report.share_revoke", "report", report_id, None, {"revoked": True})
    return get(conn, report_id)


def share_views(conn: Conn, report_id: str) -> list[dict]:
    return [clean(r) for r in conn.execute("select viewed_at, what, client_hash, user_agent from report_share_views "
                                           "where report_id = %s order by viewed_at desc limit 200", (report_id,))]


class ShareGone(LookupError):
    pass


def resolve_share(owner_conn: Conn, token: str) -> dict:
    """Owner connection (the viewer has no account). Raises NotFound for an unknown token, ShareGone if expired or revoked."""
    row = owner_conn.execute("select * from reports where share_token_hash = %s", (token_hash(token),)).fetchone()
    if row is None:
        raise NotFound("shared report")
    gone = owner_conn.execute("select (%s::timestamptz is not null) or (%s::timestamptz < now()) g",
                              (row["revoked_at"], row["expires_at"])).fetchone()
    if gone and gone["g"]:
        raise ShareGone("This share link has expired or was revoked by the factory")
    return row


def log_view(owner_conn: Conn, report: dict, what: str, client: str, user_agent: str | None) -> None:
    owner_conn.execute("insert into report_share_views (org_id, report_id, what, client_hash, user_agent) values (%s, %s, %s, %s, %s)",
                       (report["org_id"], report["id"], what, hashlib.sha256(client.encode()).hexdigest()[:16], (user_agent or "")[:200]))


def shared_view(owner_conn: Conn, report: dict) -> dict:
    """What a buyer or lender sees: the report, a data-quality summary, and (only if granted) the document list."""
    facts = {k: v for k, v in report["facts"].items() if k != "_guidance"}
    factory = owner_conn.execute("select name, location, factory_type from factories where id = %s", (report["factory_id"],)).fetchone()
    out = {"report_id": str(report["id"]), "format": report["format"], "title": FORMATS[report["format"]]["title"],
           "period_from": report["period_from"], "period_to": report["period_to"], "narrative": report["narrative"],
           "method": report["method"], "facts": facts, "factory": clean(factory) if factory else None,
           "created_at": report["created_at"].isoformat(), "expires_at": report["expires_at"].isoformat(),
           "include_documents": report["include_documents"]}
    if report["include_documents"]:
        out["ledger"] = [clean(r) for r in owner_conn.execute(
            """select le.trace_id, le.period, le.activity, le.quantity, le.unit, le.tco2e, le.calculation, le.doc_sha256,
                      d.id as document_id, d.file_name, le.factor_id
               from ledger_entries le join activity_records ar on ar.id = le.activity_record_id join documents d on d.id = ar.document_id
               where le.factory_id = %s and le.period between %s and %s order by le.period, le.activity""",
            (report["factory_id"], report["period_from"], report["period_to"]))]
    return out
