"""C-Trace API v1 — Postgres-backed, organisation-scoped endpoints."""
from __future__ import annotations

import io
import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated, Any

import pandas as pd
from fastapi import APIRouter, Depends, File, HTTPException, Path, Query, Request, UploadFile, status
from fastapi.responses import Response

from ctrace import certificate
from ctrace import copilot as cop
from ctrace.public_data import provenance
from ctrace.simulator import periods as period_range
from ctrace.store import copilot as cop_store
from ctrace.store import demo as demo_store
from ctrace.store import orgadmin, registry, service
from ctrace.store import reductions as red
from ctrace.store import reports as rep
from ctrace.store import verification as verif
from ctrace.store.db import Conn, org_tx
from ctrace.store.jobs import run_pending

from .auth import (
    MANAGE_ROLES,
    RAW_DOCUMENT_ROLES,
    READ_ROLES,
    REVIEW_ROLES,
    Principal,
    current_principal,
    require_platform_admin,
    verified_claims,
)
from .ratelimit import SlidingWindowPerHour
from .schemas import (
    PERIOD,
    AssetIn,
    CopilotIn,
    CorrectionIn,
    DeleteFactoryIn,
    FactoryIn,
    InterventionIn,
    InviteIn,
    OfficialFactorIn,
    OnboardingIn,
    ProductionIn,
    RejectIn,
    ReportIn,
    ResolveIn,
    ReviewIn,
    RoleIn,
    ShareIn,
    SignIn,
)

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_FILES_PER_UPLOAD = 50
router = APIRouter(prefix="/v1")
Auth = Annotated[Principal, Depends(current_principal)]
Period = Annotated[str | None, Query(pattern=PERIOD)]


@contextmanager
def scoped(request: Request, p: Principal, roles: frozenset[str] = READ_ROLES, action: str = "read this") -> Iterator[Conn]:
    p.require(roles, action)
    with request.app.state.pool.connection() as conn, org_tx(conn, p.org_id, p.user_id):
        yield conn


# ------------------------------------------------------------------ identity
@router.get("/me", tags=["auth"], summary="Who am I (verified token → C-Trace user, organisation, role)")
def me(request: Request, p: Auth) -> dict:
    with request.app.state.pool.connection() as conn:
        memberships = service.memberships_for(conn, p.user_id)
    return {"user_id": p.user_id, "email": p.email, "org_id": p.org_id, "role": p.role, "memberships": memberships}


# ------------------------------------------------------------------ factories
@router.get("/factories", tags=["factories"])
def list_factories(request: Request, p: Auth) -> list[dict]:
    with scoped(request, p) as conn:
        return service.list_factories(conn)


@router.post("/factories", tags=["factories"], status_code=status.HTTP_201_CREATED)
def create_factory(request: Request, p: Auth, body: FactoryIn) -> dict:
    with scoped(request, p, MANAGE_ROLES, "create factories") as conn:
        return service.create_factory(conn, p.user_id, body.model_dump())


@router.get("/factories/{factory_id}", tags=["factories"])
def get_factory(request: Request, p: Auth, factory_id: str) -> dict:
    with scoped(request, p) as conn:
        return service.get_factory(conn, factory_id)


@router.post("/factories/{factory_id}/assets", tags=["factories"], status_code=status.HTTP_201_CREATED)
def add_asset(request: Request, p: Auth, factory_id: str, body: AssetIn) -> dict:
    with scoped(request, p, MANAGE_ROLES, "edit the factory profile") as conn:
        return service.add_asset(conn, p.user_id, factory_id, **body.model_dump())


@router.post("/factories/{factory_id}/interventions", tags=["factories"], status_code=status.HTTP_201_CREATED)
def add_intervention(request: Request, p: Auth, factory_id: str, body: InterventionIn) -> dict:
    with scoped(request, p, MANAGE_ROLES, "edit the factory profile") as conn:
        return service.add_intervention(conn, p.user_id, factory_id, **body.model_dump())


# ------------------------------------------------------------------ production
@router.get("/factories/{factory_id}/production", tags=["production"])
def get_production(request: Request, p: Auth, factory_id: str) -> list[dict]:
    with scoped(request, p) as conn:
        service.get_factory(conn, factory_id)
        return service.production_frame(conn, factory_id).to_dict(orient="records")


@router.put("/factories/{factory_id}/production", tags=["production"])
def put_production(request: Request, p: Auth, factory_id: str, body: ProductionIn) -> dict:
    with scoped(request, p, MANAGE_ROLES, "change production records") as conn:
        n = service.upsert_production(conn, p.user_id, factory_id, [(m.period, m.pieces) for m in body.months], body.source)
    return {"months": n}


@router.post("/factories/{factory_id}/production/csv", tags=["production"], summary="Upload production as CSV (period, pieces)")
async def upload_production_csv(request: Request, p: Auth, factory_id: str, file: Annotated[UploadFile, File()]) -> dict:
    raw = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "File too large")
    try:
        df = pd.read_csv(io.BytesIO(raw), dtype={"period": str})
        rows = [(str(r.period), float(r.pieces)) for r in df[["period", "pieces"]].itertuples()]
    except (KeyError, ValueError, pd.errors.ParserError) as exc:
        raise HTTPException(422, "CSV needs columns: period (YYYY-MM), pieces") from exc
    with scoped(request, p, MANAGE_ROLES, "change production records") as conn:
        n = service.upsert_production(conn, p.user_id, factory_id, rows, f"CSV upload: {file.filename}")
    return {"months": n}


# ------------------------------------------------------------------ documents and jobs
@router.post("/factories/{factory_id}/documents", tags=["documents"], status_code=status.HTTP_202_ACCEPTED,
             summary="Upload bills (PDF/JPEG/PNG). Re-uploading identical bytes is idempotent (SHA-256).")
async def upload_documents(request: Request, p: Auth, factory_id: str, files: Annotated[list[UploadFile], File()]) -> list[dict]:
    p.require(MANAGE_ROLES, "upload documents")
    if len(files) > MAX_FILES_PER_UPLOAD:
        raise HTTPException(413, f"Upload at most {MAX_FILES_PER_UPLOAD} files per request")
    payloads = []
    for f in files:
        data = await f.read(MAX_UPLOAD_BYTES + 1)
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, f"{f.filename} is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
        payloads.append((f.filename or "upload", data, (f.content_type or "application/octet-stream").split(";")[0]))
    out: list[dict[str, Any]] = []
    with scoped(request, p, MANAGE_ROLES, "upload documents") as conn:
        for name, data, media_type in payloads:
            r = service.ingest_document(conn, p.user_id, factory_id, name, data, media_type)
            out.append({"document": r.document, "duplicate": r.duplicate, "job_id": r.job_id, "message": r.message})
    new_jobs = [o["job_id"] for o in out if o["job_id"]]
    if new_jobs and inline_jobs():  # hosts without a worker (serverless): read the bills inside this request
        with request.app.state.pool.connection() as conn:
            run_pending(conn, "api-inline", org_id=p.org_id)
        with scoped(request, p) as conn:
            for o in out:
                if o["job_id"]:
                    o["job"] = service.job_status(conn, int(o["job_id"]))
    return out


def inline_jobs() -> bool:
    return os.getenv("CTRACE_INLINE_JOBS", "1") == "1"


# ------------------------------------------------------------------ live demo and onboarding
_demo_limiter = SlidingWindowPerHour(int(os.getenv("CTRACE_DEMO_RATE_PER_HOUR", "10")))


@router.post("/demo/start", tags=["auth"], summary="Start a private live-demo sandbox (fictional data) and get a demo token")
def start_demo(request: Request) -> dict:
    if os.getenv("CTRACE_DEMO_ENABLED", "1") != "1":
        raise HTTPException(404, "The live demo is not enabled on this deployment")
    ip = (request.headers.get("x-forwarded-for") or (request.client.host if request.client else "unknown")).split(",")[0].strip()
    _demo_limiter.check(ip)
    with request.app.state.pool.connection() as conn:
        out = demo_store.start_sandbox(conn)
    return {k: out[k] for k in ("token", "expires_in", "org_id", "factory_id")}


@router.post("/onboarding", tags=["auth"], status_code=status.HTTP_201_CREATED,
             summary="First sign-in: create your C-Trace account and organisation")
def onboarding(request: Request, body: OnboardingIn, claims: Annotated[dict, Depends(verified_claims)]) -> dict:
    if claims.get("iss") == demo_store.DEMO_ISSUER:
        raise HTTPException(403, "Demo sessions cannot create organisations")
    uid, email = str(claims["sub"]), claims.get("email")
    if not email:
        raise HTTPException(422, "Your sign-in has no email address")
    with request.app.state.pool.connection() as conn:
        if service.memberships_for(conn, uid):
            raise HTTPException(409, "You already belong to an organisation")
        with conn.transaction():
            service.ensure_user(conn, uid, email, body.display_name)
            org_id = service.create_organization(conn, body.organization_name, uid)
    return {"org_id": org_id, "role": "owner"}


@router.get("/factories/{factory_id}/documents", tags=["documents"])
def list_documents(request: Request, p: Auth, factory_id: str) -> list[dict]:
    with scoped(request, p) as conn:
        service.get_factory(conn, factory_id)
        return service.list_documents(conn, factory_id)


@router.get("/documents/{document_id}", tags=["documents"])
def get_document(request: Request, p: Auth, document_id: str) -> dict:
    with scoped(request, p) as conn:
        return service.get_document(conn, document_id)


@router.get("/documents/{document_id}/file", tags=["documents"], summary="The original document bytes")
def get_document_file(request: Request, p: Auth, document_id: str) -> Response:
    with scoped(request, p, RAW_DOCUMENT_ROLES, "open original bills") as conn:
        doc, data = service.document_bytes(conn, document_id)
    return Response(data, media_type=doc["media_type"],
                    headers={"Content-Disposition": f'inline; filename="{doc["file_name"]}"', "Cache-Control": "private, no-store"})


@router.get("/jobs/{job_id}", tags=["documents"])
def get_job(request: Request, p: Auth, job_id: int) -> dict:
    with scoped(request, p) as conn:
        return service.job_status(conn, job_id)


# ------------------------------------------------------------------ review
@router.get("/factories/{factory_id}/activity-records", tags=["review"])
def list_records(request: Request, p: Auth, factory_id: str,
                 status_: Annotated[str | None, Query(alias="status", pattern="^(extracted|confirmed|corrected|rejected)$")] = None) -> list[dict]:
    with scoped(request, p) as conn:
        service.get_factory(conn, factory_id)
        df = service.records_frame(conn, factory_id, include_rejected=True)
    if status_:
        df = df[df.status == status_]
    return [service.clean(r) for r in df.to_dict(orient="records")]


@router.get("/factories/{factory_id}/suggestions", tags=["review"],
            summary="Correction suggestions (implied tariff far from this factory's median). Each needs an explicit accept.")
def list_suggestions(request: Request, p: Auth, factory_id: str) -> list[dict]:
    with scoped(request, p) as conn:
        service.get_factory(conn, factory_id)
        return service.suggestions(conn, factory_id)


@router.post("/factories/{factory_id}/activity-records/confirm-clean", tags=["review"],
             summary="Confirm every bill with no warning and no pending suggestion; flagged bills stay in the queue")
def confirm_clean(request: Request, p: Auth, factory_id: str, body: RejectIn) -> dict:
    with scoped(request, p, REVIEW_ROLES, "confirm records") as conn:
        return service.confirm_clean(conn, p.user_id, factory_id, body.reason)


@router.post("/activity-records/{record_id}/confirm", tags=["review"])
def confirm(request: Request, p: Auth, record_id: str, body: ReviewIn | None = None) -> dict:
    with scoped(request, p, REVIEW_ROLES, "confirm records") as conn:
        return service.confirm_record(conn, p.user_id, record_id, body.reason if body else None)


@router.post("/activity-records/{record_id}/correct", tags=["review"])
def correct(request: Request, p: Auth, record_id: str, body: CorrectionIn) -> dict:
    with scoped(request, p, REVIEW_ROLES, "correct records") as conn:
        return service.correct_record(conn, p.user_id, record_id, body.changes(), body.reason)


@router.post("/activity-records/{record_id}/reject", tags=["review"])
def reject(request: Request, p: Auth, record_id: str, body: RejectIn) -> dict:
    with scoped(request, p, REVIEW_ROLES, "reject records") as conn:
        return service.reject_record(conn, p.user_id, record_id, body.reason)


@router.get("/activity-records/{record_id}/audit", tags=["review"])
def record_audit(request: Request, p: Auth, record_id: str) -> list[dict]:
    with scoped(request, p) as conn:
        service.get_record(conn, record_id)
        return service.audit_trail(conn, "activity_record", record_id)


# ------------------------------------------------------------------ emissions
@router.get("/factories/{factory_id}/ledger", tags=["emissions"], summary="Trace ledger: every row links to its bill and factor")
def get_ledger(request: Request, p: Auth, factory_id: str, period: Period = None,
               activity: Annotated[str | None, Query(pattern="^(natural_gas|grid_electricity|diesel)$")] = None) -> list[dict]:
    with scoped(request, p) as conn:
        service.get_factory(conn, factory_id)
        return service.ledger(conn, factory_id, period, activity)


@router.get("/factories/{factory_id}/summary", tags=["emissions"], summary="Monthly Scope 1/2 totals and intensity from the ledger")
def get_summary(request: Request, p: Auth, factory_id: str, period_from: Period = None, period_to: Period = None) -> dict:
    with scoped(request, p) as conn:
        return service.summary(conn, factory_id, period_from, period_to)


@router.get("/factors", tags=["factors"], summary="Emission factor registry (public reference data)")
def list_factors(request: Request,
                 activity: Annotated[str | None, Query(pattern="^(natural_gas|grid_electricity|diesel)$")] = None) -> list[dict]:
    with request.app.state.pool.connection() as conn:
        rows = conn.execute("select * from emission_factors where %s::text is null or activity = %s order by activity, year nulls first",
                            (activity, activity)).fetchall()
    return [service.clean(r) for r in rows]


@router.post("/admin/factors", tags=["factors"], status_code=status.HTTP_201_CREATED,
             summary="Enter the official national grid emission factor (C-Trace platform admins only)")
def add_official_factor(request: Request, p: Auth, body: OfficialFactorIn) -> dict:
    require_platform_admin(p)
    with request.app.state.pool.connection() as conn, conn.transaction():
        try:
            row = registry.add_official_factor(conn, user_id=p.user_id, **body.model_dump())
        except registry.FactorError as exc:
            raise HTTPException(422, str(exc)) from exc
        with org_tx(conn, p.org_id, p.user_id):
            service.audit(conn, p.user_id, "factor.create_official", "emission_factor", row["id"], None, row)
    return service.clean(row)


@router.get("/factories/{factory_id}/weather", tags=["emissions"], summary="Monthly temperature and cooling degree days, with source")
def get_weather(request: Request, p: Auth, factory_id: str, period_from: Period = None, period_to: Period = None) -> list[dict]:
    with scoped(request, p) as conn:
        f = service.get_factory(conn, factory_id)
        known = sorted(set(service.production_frame(conn, factory_id).period) |
                       {r["period"] for r in conn.execute("select distinct period from ledger_entries where factory_id = %s", (factory_id,))})
        if not known and not (period_from and period_to):
            return []
        ps = period_range(period_from or known[0], period_to or known[-1])
        return [service.clean(r) for r in registry.factory_weather(conn, f, ps).to_dict(orient="records")]


@router.get("/factories/{factory_id}/benchmark", tags=["emissions"],
            summary="Climate TRACE sector context: where the factory's annualised Scope 1 sits (a signal, not a grade)")
def get_benchmark(request: Request, p: Auth, factory_id: str) -> dict:
    with scoped(request, p) as conn:
        months = service.summary(conn, factory_id)["months"]
    last = months[-12:]
    scope1 = round(sum(m["scope1_t"] for m in last) * 12 / len(last), 1) if last else None
    out = registry.benchmark(scope1)
    if out is None:
        raise HTTPException(404, "Climate TRACE benchmark data not loaded (scripts/fetch_climate_trace.py)")
    return {**out, "factory_months_used": len(last)}


@router.get("/data-sources", tags=["factors"], summary="Public datasets C-Trace uses, with URL, licence and retrieval time")
def data_sources() -> dict:
    return provenance()


# ------------------------------------------------------------------ verification (C-Trace Verifier)
@router.post("/factories/{factory_id}/verification/run", tags=["verification"],
             summary="Run the C-Trace Verifier on the factory's current data (expected band, drivers, grades)")
def run_verification(request: Request, p: Auth, factory_id: str) -> dict:
    with scoped(request, p, REVIEW_ROLES, "run verification") as conn:
        return verif.run_verification(conn, p.user_id, factory_id)


@router.get("/factories/{factory_id}/verification", tags=["verification"], summary="Latest verification run (stale if data changed since)")
def get_verification(request: Request, p: Auth, factory_id: str) -> dict:
    with scoped(request, p) as conn:
        return verif.latest(conn, factory_id)


@router.post("/verification/{run_id}/{period}/resolve", tags=["verification"],
             summary="Reviewer resolution of a flagged month, with a comment (audit-logged)")
def resolve_month(request: Request, p: Auth, run_id: str, period: Annotated[str, Path(pattern=PERIOD)], body: ResolveIn) -> dict:
    with scoped(request, p, REVIEW_ROLES, "resolve verification flags") as conn:
        return verif.resolve(conn, p.user_id, run_id, period, body.resolution, body.comment)


@router.get("/admin/drift", tags=["verification"], summary="C-Trace Verifier drift checks across the fleet (platform admins)")
def get_drift(request: Request, p: Auth, run: bool = False) -> list[dict]:
    require_platform_admin(p)
    with request.app.state.pool.connection() as conn:
        if run:
            with conn.transaction():
                verif.drift_check(conn)
        return [service.clean(r) for r in conn.execute("select * from model_drift_checks order by checked_at desc limit 20")]


# ------------------------------------------------------------------ reductions (IPMVP Option C-style, ASHRAE G14 checks)
@router.post("/factories/{factory_id}/reductions/compute", tags=["reductions"],
             summary="Prove each declared intervention's saving from verified data (drafts; signed proofs are kept)")
def compute_reductions(request: Request, p: Auth, factory_id: str) -> list[dict]:
    with scoped(request, p, REVIEW_ROLES, "compute reductions") as conn:
        return red.compute(conn, p.user_id, factory_id)


@router.get("/factories/{factory_id}/reductions", tags=["reductions"])
def list_reductions(request: Request, p: Auth, factory_id: str) -> list[dict]:
    with scoped(request, p) as conn:
        return red.list_current(conn, factory_id)


@router.post("/reductions/{reduction_id}/sign", tags=["reductions"], summary="Reviewer sign-off (verified proofs only; audit-logged)")
def sign_reduction(request: Request, p: Auth, reduction_id: str, body: SignIn) -> dict:
    with scoped(request, p, REVIEW_ROLES, "sign reductions") as conn:
        return red.sign(conn, p.user_id, reduction_id, body.comment)


@router.get("/reductions/{reduction_id}/certificate.pdf", tags=["reductions"], summary="Reduction Certificate (PDF); DRAFT until signed")
def reduction_certificate(request: Request, p: Auth, reduction_id: str) -> Response:
    with scoped(request, p) as conn:
        r = red.get(conn, reduction_id)
        f = service.get_factory(conn, r["factory_id"])
        sources = {row["id"]: service.clean(row) for row in conn.execute(
            "select id, source, source_url from emission_factors where id = any(%s)", (r["factor_ids"],))}
        pdf = certificate.render(r, f, red.signer_name(conn, r.get("signed_by")), sources)
    name = f"C-Trace_reduction_{r['activity']}_{r['reporting_from']}{'' if r['status'] == 'signed' else '_DRAFT'}.pdf"
    return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/model-card", tags=["factors"], summary="The C-Trace model card (Verifier + extraction), rendered from computed metrics")
def model_card() -> dict:
    from ctrace import config as cfg
    card = cfg.MODELS_DIR / "MODEL_CARD.md"
    if not card.exists():
        raise HTTPException(404, "Model card not generated yet (make train)")
    return {"markdown": card.read_text(encoding="utf-8"), "verifier_version": cfg.CTRACE_VERIFIER_VERSION}


# ------------------------------------------------------------------ C-Trace Reports and sharing
@router.post("/factories/{factory_id}/reports", tags=["reports"], status_code=status.HTTP_201_CREATED,
             summary="Build a buyer / IFRS S2 lender / BSEC report from verified data (narrative number-guarded)")
def create_report(request: Request, p: Auth, factory_id: str, body: ReportIn) -> dict:
    with scoped(request, p, MANAGE_ROLES | REVIEW_ROLES, "create reports") as conn:
        return rep.build(conn, p.user_id, factory_id, body.format, body.period_from, body.period_to)


@router.get("/factories/{factory_id}/reports", tags=["reports"])
def list_reports(request: Request, p: Auth, factory_id: str) -> list[dict]:
    with scoped(request, p) as conn:
        return rep.list_reports(conn, factory_id)


@router.get("/reports/{report_id}", tags=["reports"])
def get_report(request: Request, p: Auth, report_id: str) -> dict:
    with scoped(request, p) as conn:
        return rep.get(conn, report_id)


@router.get("/reports/{report_id}/download.{fmt}", tags=["reports"], response_model=None, summary="Report as PDF, JSON or ledger CSV")
def download_report(request: Request, p: Auth, report_id: str, fmt: Annotated[str, Path(pattern="^(pdf|json|csv)$")]) -> Response:
    with scoped(request, p) as conn:
        r = rep.get(conn, report_id)
        name = f"C-Trace_{r['format']}_{r['period_from']}_{r['period_to']}"
        if fmt == "pdf":
            _, data = rep.pdf(conn, report_id)
            return Response(data, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{name}.pdf"'})
        if fmt == "json":
            import json as _json
            return Response(_json.dumps(rep.as_json(conn, report_id), indent=1, default=str, ensure_ascii=False), media_type="application/json",
                            headers={"Content-Disposition": f'attachment; filename="{name}.json"'})
        return Response(rep.ledger_csv(conn, report_id), media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="{name}_ledger.csv"'})


@router.post("/reports/{report_id}/share", tags=["reports"], summary="Create a read-only, expiring share link (replaces any previous link)")
def share_report(request: Request, p: Auth, report_id: str, body: ShareIn) -> dict:
    with scoped(request, p, MANAGE_ROLES, "share reports") as conn:
        return rep.share(conn, p.user_id, report_id, body.days, body.include_documents)


@router.post("/reports/{report_id}/share/revoke", tags=["reports"])
def revoke_share(request: Request, p: Auth, report_id: str) -> dict:
    with scoped(request, p, MANAGE_ROLES, "revoke share links") as conn:
        return rep.revoke(conn, p.user_id, report_id)


@router.get("/reports/{report_id}/share/views", tags=["reports"], summary="Who opened the share link, and when (hashed client)")
def share_views(request: Request, p: Auth, report_id: str) -> list[dict]:
    with scoped(request, p, MANAGE_ROLES, "see share views") as conn:
        rep.get(conn, report_id)
        return rep.share_views(conn, report_id)


def _client(request: Request) -> str:
    return (request.headers.get("x-forwarded-for") or (request.client.host if request.client else "unknown")).split(",")[0].strip()


def _shared(request: Request, token: str, what: str) -> dict:
    with request.app.state.pool.connection() as conn:
        try:
            r = rep.resolve_share(conn, token)
        except rep.ShareGone as exc:
            raise HTTPException(410, str(exc)) from exc
        rep.log_view(conn, r, what, _client(request), request.headers.get("user-agent"))
        return r


@router.get("/shared/{token}", tags=["shared"], summary="Public read-only view of a shared report (no account needed)")
def shared_report(request: Request, token: str) -> dict:
    r = _shared(request, token, "page")
    with request.app.state.pool.connection() as conn:
        return rep.shared_view(conn, r)


@router.get("/shared/{token}/report.pdf", tags=["shared"], response_model=None)
def shared_pdf(request: Request, token: str) -> Response:
    r = _shared(request, token, "pdf")
    with request.app.state.pool.connection() as conn, org_tx(conn, r["org_id"]):
        _, data = rep.pdf(conn, str(r["id"]))
    return Response(data, media_type="application/pdf", headers={"Content-Disposition": 'attachment; filename="C-Trace_shared_report.pdf"'})


@router.get("/shared/{token}/documents/{document_id}", tags=["shared"], response_model=None,
            summary="An original bill behind a shared report, only if the factory granted document access")
def shared_document(request: Request, token: str, document_id: str) -> Response:
    r = _shared(request, token, "document")
    if not r["include_documents"]:
        raise HTTPException(403, "The factory did not share its original bills with this link")
    with request.app.state.pool.connection() as conn, org_tx(conn, r["org_id"]):
        ok = conn.execute("""select 1 from documents d join activity_records ar on ar.document_id = d.id
                             where d.id = %s and d.factory_id = %s and ar.period between %s and %s""",
                          (document_id, r["factory_id"], r["period_from"], r["period_to"])).fetchone()
        if not ok:
            raise HTTPException(404, "document not part of this report")
        doc, data = service.document_bytes(conn, document_id)
    return Response(data, media_type=doc["media_type"], headers={"Content-Disposition": f'inline; filename="{doc["file_name"]}"',
                                                                  "Cache-Control": "private, no-store"})


# ------------------------------------------------------------------ settings: members, export, delete
@router.get("/org/members", tags=["settings"])
def get_members(request: Request, p: Auth) -> dict:
    with scoped(request, p) as conn:
        return orgadmin.members(conn)


@router.post("/org/invites", tags=["settings"], status_code=status.HTTP_201_CREATED,
             summary="Invite by email; the person joins with this role at their first sign-in")
def create_invite(request: Request, p: Auth, body: InviteIn) -> dict:
    if p.is_demo:
        raise HTTPException(403, "Invitations are disabled in demo sandboxes")
    with scoped(request, p, frozenset({"owner"}), "invite members") as conn:
        return orgadmin.invite(conn, p.user_id, body.email, body.role)


@router.put("/org/members/{member_id}", tags=["settings"])
def update_member(request: Request, p: Auth, member_id: str, body: RoleIn) -> dict:
    with scoped(request, p, frozenset({"owner"}), "change roles") as conn:
        return orgadmin.set_role(conn, p.user_id, member_id, body.role)


@router.delete("/org/members/{member_id}", tags=["settings"], status_code=status.HTTP_204_NO_CONTENT, response_model=None)
def delete_member(request: Request, p: Auth, member_id: str) -> Response:
    with scoped(request, p, frozenset({"owner"}), "remove members") as conn:
        orgadmin.remove(conn, p.user_id, member_id)
    return Response(status_code=204)


@router.get("/factories/{factory_id}/export.zip", tags=["settings"], response_model=None, summary="Export all of the factory's data")
def export_factory(request: Request, p: Auth, factory_id: str) -> Response:
    with scoped(request, p, MANAGE_ROLES, "export data") as conn:
        data = orgadmin.export_zip(conn, p.user_id, factory_id)
    return Response(data, media_type="application/zip", headers={"Content-Disposition": 'attachment; filename="C-Trace_export.zip"'})


@router.post("/factories/{factory_id}/delete", tags=["settings"], status_code=status.HTTP_204_NO_CONTENT, response_model=None,
             summary="Permanently delete a factory and all its data (owner; confirm with the exact name)")
def delete_factory(request: Request, p: Auth, factory_id: str, body: DeleteFactoryIn) -> Response:
    with scoped(request, p, frozenset({"owner"}), "delete factory data") as conn:
        orgadmin.delete_factory(conn, p.user_id, factory_id, body.confirm_name)
    return Response(status_code=204)


# ------------------------------------------------------------------ C-Trace Copilot
@router.post("/factories/{factory_id}/copilot/chat", tags=["copilot"], response_model=None,
             summary="Ask C-Trace Copilot (EN/BN). Streams server-sent events: one per tool call, then the cited, number-guarded answer")
def copilot_chat(request: Request, p: Auth, factory_id: str, body: CopilotIn, stream: bool = True) -> Response:
    from .ratelimit import limit_llm
    limit_llm(p.user_id)
    with scoped(request, p) as conn:
        events = list(cop.ask(body.message, cop_store.tools_for(conn, factory_id), body.language))
    if not stream:
        import json as _json
        return Response(_json.dumps(events[-1]["answer"], default=str, ensure_ascii=False), media_type="application/json")

    def sse() -> Iterator[bytes]:
        import json as _json
        for e in events:
            yield f"event: {e['type']}\ndata: {_json.dumps(e, default=str, ensure_ascii=False)}\n\n".encode()
    from fastapi.responses import StreamingResponse
    return StreamingResponse(sse(), media_type="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})
