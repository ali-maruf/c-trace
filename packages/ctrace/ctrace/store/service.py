"""C-Trace domain operations on Postgres.

Every function here expects to run inside `org_tx(...)` (role `ctrace_app`, RLS on) unless its
docstring says it needs the owner connection. Emissions numbers are never computed in SQL: the
ledger is built by `ctrace.calculate.build_ledger` and stored exactly as computed.

Ledger semantics (see docs/DECISIONS.md D10): the ledger is rebuilt in full for a factory after any
change, from every activity record that is not rejected — the same rows the prototype counted —
so totals match the prototype. How many of those rows a human has confirmed is reported alongside.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

import numpy as np
import pandas as pd

from ..calculate import build_ledger, monthly_summary
from ..extraction import ACTIVITIES, CANONICAL_UNIT, extract_document, normalize_period, sha256_bytes
from ..llm import LLMCall
from ..pipeline import tariff_corrections
from .db import Conn, org_tx
from .jobs import enqueue, handler
from .storage import EXT, Storage, default_storage, document_key

WRITE_ROLES = {"owner", "manager", "reviewer"}
CORRECTABLE_FIELDS = {"activity", "period", "quantity", "amount_bdt", "bill_no", "supplier"}


class NotFound(LookupError):
    pass


def _jsonable(v: Any) -> Any:
    if v is None or v is pd.NaT:
        return None
    if isinstance(v, np.integer):
        return int(v)
    if isinstance(v, float | np.floating):
        return None if math.isnan(v) else float(v)
    if isinstance(v, UUID):
        return str(v)
    if isinstance(v, datetime):
        return v.isoformat()
    return v


def clean(row: dict) -> dict:
    return {k: _jsonable(v) for k, v in row.items()}


def audit(conn: Conn, user_id: str | None, action: str, entity: str, entity_id: str,
          before: dict | None = None, after: dict | None = None) -> None:
    conn.execute(
        "insert into audit_log (org_id, user_id, action, entity, entity_id, before, after) "
        "values (ctrace_current_org(), %s, %s, %s, %s, %s, %s)",
        (user_id, action, entity, str(entity_id),
         json.dumps(clean(before), default=str) if before is not None else None,
         json.dumps(clean(after), default=str) if after is not None else None),
    )


# ------------------------------------------------------------------ organisations (owner connection)
def ensure_user(conn: Conn, user_id: str, email: str, display_name: str | None = None) -> None:
    """Owner connection. Create or update the C-Trace user for an authenticated identity."""
    conn.execute("insert into users (id, email, display_name) values (%s, %s, %s) "
                 "on conflict (id) do update set email = excluded.email, display_name = coalesce(excluded.display_name, users.display_name)",
                 (user_id, email, display_name))


def create_organization(conn: Conn, name: str, owner_user_id: str) -> str:
    """Owner connection. New organisation with the given user as owner."""
    with conn.transaction():
        row = conn.execute("insert into organizations (name) values (%s) returning id", (name,)).fetchone()
        assert row is not None
        org_id = str(row["id"])
        conn.execute("insert into memberships (org_id, user_id, role) values (%s, %s, 'owner')", (org_id, owner_user_id))
    return org_id


def memberships_for(conn: Conn, user_id: str) -> list[dict]:
    """Owner connection: which organisations a verified identity belongs to (used before an org is chosen)."""
    return [clean(r) for r in conn.execute(
        "select m.org_id, m.role, o.name as org_name from memberships m join organizations o on o.id = m.org_id "
        "where m.user_id = %s order by o.name", (user_id,))]


# ------------------------------------------------------------------ factories
FACTORY_FIELDS = ("name", "location", "lat", "lon", "factory_type", "workers", "has_wet_processing", "captive_gas_gen")


def create_factory(conn: Conn, user_id: str | None, data: dict) -> dict:
    cols = [c for c in FACTORY_FIELDS if c in data]
    row = conn.execute(
        f"insert into factories (org_id, {', '.join(cols)}) values (ctrace_current_org(), {', '.join(['%s'] * len(cols))}) returning *",
        [data[c] for c in cols],
    ).fetchone()
    assert row is not None
    audit(conn, user_id, "factory.create", "factory", row["id"], None, row)
    return clean(row)


def list_factories(conn: Conn) -> list[dict]:
    return [clean(r) for r in conn.execute("select * from factories order by name")]


def get_factory(conn: Conn, factory_id: str) -> dict:
    row = conn.execute("select * from factories where id = %s", (factory_id,)).fetchone()
    if row is None:
        raise NotFound("factory")
    f = clean(row)
    assert f is not None
    f["assets"] = [clean(r) for r in conn.execute("select * from assets where factory_id = %s order by from_period", (factory_id,))]
    f["interventions"] = [clean(r) for r in conn.execute(
        "select * from interventions where factory_id = %s order by from_period", (factory_id,))]
    return f


def add_asset(conn: Conn, user_id: str | None, factory_id: str, kind: str, capacity: float, unit: str, from_period: str) -> dict:
    get_factory(conn, factory_id)
    row = conn.execute("insert into assets (org_id, factory_id, kind, capacity, unit, from_period) "
                       "values (ctrace_current_org(), %s, %s, %s, %s, %s) returning *",
                       (factory_id, kind, capacity, unit, from_period)).fetchone()
    assert row is not None
    audit(conn, user_id, "asset.create", "asset", row["id"], None, row)
    return clean(row)


def add_intervention(conn: Conn, user_id: str | None, factory_id: str, measure: str, from_period: str,
                     capex_bdt: float | None = None, notes: str | None = None) -> dict:
    get_factory(conn, factory_id)
    row = conn.execute("insert into interventions (org_id, factory_id, measure, from_period, capex_bdt, notes) "
                       "values (ctrace_current_org(), %s, %s, %s, %s, %s) returning *",
                       (factory_id, measure, from_period, capex_bdt, notes)).fetchone()
    assert row is not None
    audit(conn, user_id, "intervention.create", "intervention", row["id"], None, row)
    return clean(row)


def upsert_production(conn: Conn, user_id: str | None, factory_id: str, rows: list[tuple[str, float]], source: str) -> int:
    get_factory(conn, factory_id)
    for period, pieces in rows:
        if normalize_period(period) != period:
            raise ValueError(f"Production period must be YYYY-MM, got {period!r}")
        if pieces < 0:
            raise ValueError("Production pieces cannot be negative")
    with conn.cursor() as cur:
        cur.executemany("insert into production_records (org_id, factory_id, period, pieces, source) "
                        "values (ctrace_current_org(), %s, %s, %s, %s) "
                        "on conflict (factory_id, period) do update set pieces = excluded.pieces, source = excluded.source",
                        [(factory_id, p, float(n), source) for p, n in rows])
    audit(conn, user_id, "production.upsert", "factory", factory_id, None, {"months": len(rows), "source": source})
    return len(rows)


def production_frame(conn: Conn, factory_id: str) -> pd.DataFrame:
    rows = conn.execute("select period, pieces, source from production_records where factory_id = %s order by period", (factory_id,)).fetchall()
    return pd.DataFrame(rows, columns=["period", "pieces", "source"])


# ------------------------------------------------------------------ documents
@dataclass
class IngestResult:
    document: dict
    duplicate: bool
    job_id: int | None
    message: str


def ingest_document(conn: Conn, user_id: str | None, factory_id: str, file_name: str, data: bytes,
                    media_type: str, storage: Storage | None = None) -> IngestResult:
    """Store an original document and queue its extraction. Uploading the same bytes twice is a no-op."""
    if media_type not in EXT:
        raise ValueError(f"Unsupported document type {media_type!r}; C-Trace accepts PDF, JPEG and PNG.")
    if not data:
        raise ValueError("Empty file")
    factory = get_factory(conn, factory_id)
    sha = sha256_bytes(data)
    existing = conn.execute("select * from documents where factory_id = %s and sha256 = %s", (factory_id, sha)).fetchone()
    if existing:
        at = existing["uploaded_at"]
        return IngestResult(clean(existing), True, None,
                            f"Already uploaded on {at.day} {at:%b %Y} as {existing['file_name']}")
    key = document_key(factory["org_id"], factory_id, sha, media_type)
    (storage or default_storage()).put(key, data)
    row = conn.execute(
        "insert into documents (org_id, factory_id, storage_path, sha256, file_name, media_type, size_bytes, uploaded_by) "
        "values (ctrace_current_org(), %s, %s, %s, %s, %s, %s, %s) returning *",
        (factory_id, key, sha, file_name, media_type, len(data), user_id),
    ).fetchone()
    assert row is not None
    job_id = enqueue(conn, factory["org_id"], "extract_document", {"document_id": str(row["id"]), "user_id": user_id})
    audit(conn, user_id, "document.upload", "document", row["id"], None, {"file_name": file_name, "sha256": sha, "job_id": job_id})
    return IngestResult(clean(row), False, job_id, "Queued for extraction")


def list_documents(conn: Conn, factory_id: str) -> list[dict]:
    return [clean(r) for r in conn.execute(
        "select d.*, ar.id as activity_record_id, ar.status as record_status from documents d "
        "left join activity_records ar on ar.document_id = d.id where d.factory_id = %s order by d.file_name", (factory_id,))]


def get_document(conn: Conn, document_id: str) -> dict:
    row = conn.execute("select * from documents where id = %s", (document_id,)).fetchone()
    if row is None:
        raise NotFound("document")
    d = clean(row)
    assert d is not None
    d["extractions"] = [clean(r) for r in conn.execute(
        "select id, method, model, prompt_version, field_evidence, issues, confidence, created_at "
        "from extractions where document_id = %s order by created_at", (document_id,))]
    return d


def document_bytes(conn: Conn, document_id: str, storage: Storage | None = None) -> tuple[dict, bytes]:
    d = get_document(conn, document_id)
    return d, (storage or default_storage()).get(d["storage_path"])


@handler("extract_document", own_transactions=True)
def _extract_job(conn: Conn, job: dict) -> dict:
    return run_extraction(conn, str(job["org_id"]), job["payload"].get("user_id"), job["payload"]["document_id"])


class DbCache:
    """Org-scoped cache of model reads (`llm_cache`). Each get/put is its own short transaction."""

    def __init__(self, conn: Conn, org_id: str, user_id: str | None):
        self.conn, self.org_id, self.user_id = conn, org_id, user_id

    def get(self, key: str) -> dict | None:
        sha, model, prompt = key.split(":", 2)
        with org_tx(self.conn, self.org_id, self.user_id):
            row = self.conn.execute("select payload from llm_cache where document_sha256 = %s and model = %s and prompt_version = %s",
                                    (sha, model, prompt)).fetchone()
        return row["payload"] if row else None

    def put(self, key: str, value: dict) -> None:
        sha, model, prompt = key.split(":", 2)
        with org_tx(self.conn, self.org_id, self.user_id):
            self.conn.execute("insert into llm_cache (org_id, document_sha256, model, prompt_version, payload) "
                              "values (ctrace_current_org(), %s, %s, %s, %s) on conflict do nothing",
                              (sha, model, prompt, json.dumps(value, ensure_ascii=False)))


def log_llm_calls(conn: Conn, calls: list[LLMCall]) -> None:
    with conn.cursor() as cur:
        cur.executemany(
            "insert into llm_calls (org_id, purpose, model, prompt_version, input_tokens, output_tokens, latency_ms, "
            "document_sha256, stop_reason, error) values (ctrace_current_org(), %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            [(c.purpose, c.model, c.prompt_version, c.input_tokens, c.output_tokens, c.latency_ms, c.document_sha256,
              c.stop_reason, c.error) for c in calls])


def run_extraction(conn: Conn, org_id: str, user_id: str | None, document_id: str, storage: Storage | None = None) -> dict:
    """Read a stored document, record the extraction, create/refresh its activity record, rebuild the ledger.

    Runs in three steps so no transaction is open during the model call: read the document; extract
    (cache lookups and writes are their own short transactions); write the results and the call log.
    A record a human has already confirmed, corrected or rejected is never overwritten by re-extraction."""
    with org_tx(conn, org_id, user_id):
        doc, data = document_bytes(conn, document_id, storage)
    calls: list[LLMCall] = []
    rec = extract_document(data, doc["file_name"], doc["media_type"], cache=DbCache(conn, org_id, user_id), on_call=calls.append)
    if rec.doc_sha256 != doc["sha256"]:
        raise RuntimeError(f"Stored bytes for document {document_id} do not match its SHA-256")
    method, _, model = rec.extraction_method.partition(":")
    field_evidence = {k: {"text": v, "confidence": rec.field_confidence.get(k)} for k, v in rec.evidence.items()}
    with org_tx(conn, org_id, user_id):
        log_llm_calls(conn, calls)
        ext = conn.execute(
            "insert into extractions (org_id, document_id, method, model, prompt_version, raw_json, field_evidence, issues, confidence) "
            "values (ctrace_current_org(), %s, %s, %s, %s, %s, %s, %s, %s) returning id",
            (document_id, method, model or None, rec.prompt_version, json.dumps(rec.as_dict(), ensure_ascii=False),
             json.dumps(field_evidence, ensure_ascii=False), json.dumps(rec.issues, ensure_ascii=False), rec.extraction_confidence),
        ).fetchone()
        assert ext is not None
        row = conn.execute(
            """insert into activity_records (org_id, factory_id, document_id, extraction_id, activity, period, quantity,
                                             unit, amount_bdt, bill_no, supplier)
               values (ctrace_current_org(), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               on conflict (document_id) do update set
                 extraction_id = excluded.extraction_id, activity = excluded.activity, period = excluded.period,
                 quantity = excluded.quantity, unit = excluded.unit, amount_bdt = excluded.amount_bdt,
                 bill_no = excluded.bill_no, supplier = excluded.supplier, updated_at = now()
               where activity_records.status = 'extracted'
               returning id""",
            (doc["factory_id"], document_id, ext["id"], rec.activity, rec.period, rec.quantity, rec.unit,
             rec.amount_bdt, rec.bill_no, rec.supplier),
        ).fetchone()
        if row is None:  # human-reviewed record kept as is
            row = conn.execute("select id from activity_records where document_id = %s", (document_id,)).fetchone()
        assert row is not None
        n = rebuild_ledger(conn, doc["factory_id"])
    return {"extraction_id": str(ext["id"]), "activity_record_id": str(row["id"]), "issues": rec.issues, "ledger_rows": n,
            "method": rec.extraction_method, "llm_calls": len(calls)}


# ------------------------------------------------------------------ activity records and ledger
def records_frame(conn: Conn, factory_id: str, include_rejected: bool = False) -> pd.DataFrame:
    rows = conn.execute(
        f"""select ar.id, ar.document_id, ar.activity, ar.period, ar.quantity, ar.unit, ar.amount_bdt, ar.bill_no,
                   ar.supplier, ar.status, ar.confirmed_by, ar.confirmed_at, ar.updated_at,
                   d.file_name as doc_name, d.sha256 as doc_sha256,
                   coalesce(e.issues, '[]'::jsonb) as issues, e.confidence as extraction_confidence,
                   e.method as extraction_method, e.field_evidence as evidence
            from activity_records ar
            join documents d on d.id = ar.document_id
            left join extractions e on e.id = ar.extraction_id
            where ar.factory_id = %s {'' if include_rejected else "and ar.status <> 'rejected'"}
            order by ar.period nulls last, ar.activity, d.file_name""",
        (factory_id,),
    ).fetchall()
    cols = ["id", "document_id", "activity", "period", "quantity", "unit", "amount_bdt", "bill_no", "supplier", "status",
            "confirmed_by", "confirmed_at", "updated_at", "doc_name", "doc_sha256", "issues", "extraction_confidence",
            "extraction_method", "evidence"]
    return pd.DataFrame(rows, columns=cols)


def rebuild_ledger(conn: Conn, factory_id: str) -> int:
    recs = records_frame(conn, factory_id)
    usable = recs.dropna(subset=["period", "activity", "quantity"])
    conn.execute("delete from ledger_entries where factory_id = %s", (factory_id,))
    if usable.empty:
        return 0
    ledger = build_ledger(usable)
    record_of = dict(zip(usable.doc_sha256, usable.id, strict=True))
    with conn.cursor() as cur:
        cur.executemany(
            """insert into ledger_entries (factory_id, trace_id, org_id, activity_record_id, factor_id, scope, period,
                                           activity, quantity, unit, energy_gj, kgco2e, tco2e, calculation, doc_sha256)
               values (%s, %s, ctrace_current_org(), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            [(factory_id, r.trace_id, record_of[r.doc_sha256], r.factor_id, int(r.scope), r.period, r.activity,
              float(r.quantity), r.unit, float(r.energy_gj), float(r.kgco2e), float(r.tco2e), r.calculation, r.doc_sha256)
             for r in ledger.itertuples()],
        )
    return len(ledger)


def ledger(conn: Conn, factory_id: str, period: str | None = None, activity: str | None = None) -> list[dict]:
    sql = ["""select le.trace_id, le.period, le.activity, le.scope, le.quantity, le.unit, le.energy_gj, le.kgco2e,
                     le.tco2e, le.calculation, le.doc_sha256, le.activity_record_id, ar.status as record_status,
                     d.id as document_id, d.file_name as doc_name,
                     ef.id as factor_id, ef.kgco2e_per_unit, ef.source as factor_source, ef.source_url as factor_source_url,
                     ef.version as factor_version
              from ledger_entries le
              join activity_records ar on ar.id = le.activity_record_id
              join documents d on d.id = ar.document_id
              join emission_factors ef on ef.id = le.factor_id
              where le.factory_id = %s"""]
    args: list[Any] = [factory_id]
    if period:
        sql.append("and le.period = %s")
        args.append(period)
    if activity:
        sql.append("and le.activity = %s")
        args.append(activity)
    sql.append("order by le.period, le.activity, le.trace_id")
    return [clean(r) for r in conn.execute(" ".join(sql), args)]


def summary(conn: Conn, factory_id: str, period_from: str | None = None, period_to: str | None = None) -> dict:
    get_factory(conn, factory_id)
    led = pd.DataFrame(conn.execute("select period, activity, tco2e, energy_gj from ledger_entries where factory_id = %s",
                                    (factory_id,)).fetchall(), columns=["period", "activity", "tco2e", "energy_gj"])
    status = {r["status"]: r["n"] for r in conn.execute(
        "select status::text, count(*) n from activity_records where factory_id = %s group by status", (factory_id,))}
    docs = conn.execute("select count(*) n from documents where factory_id = %s", (factory_id,)).fetchone()
    assert docs is not None
    data_status = {"records_by_status": status, "unconfirmed_records": int(status.get("extracted", 0)), "documents": int(docs["n"])}
    base = {"factory_id": factory_id, "period_from": period_from, "period_to": period_to, "data_status": data_status}
    if led.empty:
        return {**base, "months": [], "totals": None}
    s = monthly_summary(led, production_frame(conn, factory_id))
    if period_from:
        s = s[s.period >= period_from]
    if period_to:
        s = s[s.period <= period_to]
    total = float(s.total_t.sum())
    pieces = float(s.pieces.sum()) if "pieces" in s else 0.0
    totals = {
        "months": int(len(s)),
        "scope1_tco2e": round(float(s.scope1_t.sum()), 4),
        "scope2_location_tco2e": round(float(s.scope2_t.sum()), 4),
        "total_tco2e": round(total, 4),
        "energy_gj": round(float(s.energy_gj.sum()), 3),
        "pieces": int(pieces) if pieces else None,
        "kgco2e_per_piece": round(total * 1000 / pieces, 4) if pieces else None,
        "by_source_tco2e": {a: round(float(s[a].sum()), 4) for a in ACTIVITIES if a in s},
    }
    months = [clean(r) for r in s.to_dict(orient="records")]
    return {**base, "period_from": period_from or (s.period.min() if len(s) else None),
            "period_to": period_to or (s.period.max() if len(s) else None), "months": months, "totals": totals}


def suggestions(conn: Conn, factory_id: str) -> list[dict]:
    """Tariff-based correction suggestions (prototype logic) for records no human has decided on yet."""
    recs = records_frame(conn, factory_id)
    out = []
    for s in tariff_corrections(recs):
        match = recs[(recs.doc_name == s["doc_name"]) & (recs.period == s["period"]) & (recs.activity == s["activity"])]
        if match.empty or match.iloc[0].status != "extracted":
            continue
        out.append({**s, "activity_record_id": str(match.iloc[0].id)})
    return out


def get_record(conn: Conn, record_id: str, lock: bool = False) -> dict:
    row = conn.execute(f"select * from activity_records where id = %s{' for update' if lock else ''}", (record_id,)).fetchone()
    if row is None:
        raise NotFound("activity record")
    return row


def _review(conn: Conn, user_id: str, record_id: str, action: str, status: str,
            changes: dict | None = None, reason: str | None = None) -> dict:
    before = get_record(conn, record_id, lock=True)
    sets, args = ["status = %s", "confirmed_by = %s", "confirmed_at = now()", "updated_at = now()"], [status, user_id]
    for k, v in (changes or {}).items():
        sets.append(f"{k} = %s")
        args.append(v)
    after = conn.execute(f"update activity_records set {', '.join(sets)} where id = %s returning *", [*args, record_id]).fetchone()
    assert after is not None
    audit(conn, user_id, action, "activity_record", record_id, before, {**after, "reason": reason})
    rebuild_ledger(conn, str(before["factory_id"]))
    return clean(after)


def confirm_record(conn: Conn, user_id: str, record_id: str, reason: str | None = None) -> dict:
    rec = get_record(conn, record_id)
    if rec["quantity"] is None or rec["period"] is None or rec["activity"] is None:
        raise ValueError("A record needs activity, period and quantity before it can be confirmed; use correct instead.")
    return _review(conn, user_id, record_id, "activity_record.confirm", "confirmed", reason=reason)


def correct_record(conn: Conn, user_id: str, record_id: str, changes: dict, reason: str) -> dict:
    unknown = set(changes) - CORRECTABLE_FIELDS
    if unknown:
        raise ValueError(f"Fields cannot be corrected: {sorted(unknown)}")
    if not changes:
        raise ValueError("No changes given")
    if not reason or not reason.strip():
        raise ValueError("A correction needs a reason (it is stored in the audit log)")
    rec = get_record(conn, record_id)
    activity = changes.get("activity", rec["activity"])
    if activity not in ACTIVITIES:
        raise ValueError(f"activity must be one of {ACTIVITIES}")
    if "period" in changes and normalize_period(str(changes["period"])) != changes["period"]:
        raise ValueError("period must be YYYY-MM")
    if "quantity" in changes and not (changes["quantity"] and float(changes["quantity"]) > 0):
        raise ValueError("quantity must be positive")
    if "activity" in changes:
        changes = {**changes, "unit": CANONICAL_UNIT[activity]}
    return _review(conn, user_id, record_id, "activity_record.correct", "corrected", changes, reason)


def reject_record(conn: Conn, user_id: str, record_id: str, reason: str) -> dict:
    if not reason or not reason.strip():
        raise ValueError("A rejection needs a reason (it is stored in the audit log)")
    return _review(conn, user_id, record_id, "activity_record.reject", "rejected", reason=reason)


def audit_trail(conn: Conn, entity: str, entity_id: str) -> list[dict]:
    return [clean(r) for r in conn.execute(
        "select id, user_id, action, before, after, at from audit_log where entity = %s and entity_id = %s order by id",
        (entity, entity_id))]


def job_status(conn: Conn, job_id: int) -> dict:
    row = conn.execute("select id, kind, status, attempts, max_attempts, last_error, result, created_at, finished_at "
                       "from jobs where id = %s", (job_id,)).fetchone()
    if row is None:
        raise NotFound("job")
    return clean(row)
