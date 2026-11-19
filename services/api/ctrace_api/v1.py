"""C-Trace API v1 — Postgres-backed, organisation-scoped endpoints."""
from __future__ import annotations

import io
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated

import pandas as pd
from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import Response

from ctrace.store import service
from ctrace.store.db import Conn, org_tx

from .auth import MANAGE_ROLES, RAW_DOCUMENT_ROLES, READ_ROLES, REVIEW_ROLES, Principal, current_principal
from .schemas import PERIOD, AssetIn, CorrectionIn, FactoryIn, InterventionIn, ProductionIn, RejectIn, ReviewIn

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
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
    payloads = []
    for f in files:
        data = await f.read(MAX_UPLOAD_BYTES + 1)
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, f"{f.filename} is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
        payloads.append((f.filename or "upload", data, (f.content_type or "application/octet-stream").split(";")[0]))
    out = []
    with scoped(request, p, MANAGE_ROLES, "upload documents") as conn:
        for name, data, media_type in payloads:
            r = service.ingest_document(conn, p.user_id, factory_id, name, data, media_type)
            out.append({"document": r.document, "duplicate": r.duplicate, "job_id": r.job_id, "message": r.message})
    return out


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
