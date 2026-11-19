"""Stateless prototype endpoints: send records in, get the ledger / verification / report back.

Nothing is stored. Kept for integrations that hold their own data; the Postgres-backed endpoints in
`v1.py` are the primary API. Authentication is required because `/extract` and `/report` may call
the LLM when ANTHROPIC_API_KEY is set.
"""
from __future__ import annotations

from typing import Annotated

import pandas as pd
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from ctrace import config
from ctrace.extraction import extract_document
from ctrace.pipeline import run
from ctrace.reduction import prove_reduction
from ctrace.report import FORMATS, build_facts, generate, to_pdf
from ctrace.verify import Verifier

from .auth import Principal, current_principal

router = APIRouter(prefix="/v1/stateless", tags=["stateless"])
Auth = Annotated[Principal, Depends(current_principal)]
_verifier: Verifier | None = None


def verifier() -> Verifier:
    global _verifier
    if _verifier is None:
        p = config.MODELS_DIR / "ctrace_verifier.joblib"
        if not p.exists():
            raise HTTPException(503, "C-Trace Verifier not trained. Run: make train")
        _verifier = Verifier.load(p)
    return _verifier


class Record(BaseModel):
    doc_name: str
    doc_sha256: str
    activity: str
    period: str = Field(pattern=r"^\d{4}-\d{2}$")
    quantity: float
    unit: str | None = None
    amount_bdt: float | None = None
    bill_no: str | None = None
    supplier: str | None = None
    issues: list[str] = []


class Production(BaseModel):
    period: str
    pieces: float


class Profile(BaseModel):
    name: str
    location: str = "Bangladesh"
    factory_type: str
    workers: int
    has_wet_processing: int
    captive_gas_gen: int = 0
    solar: list[dict] = []
    interventions: list[dict] = []


class AnalyzeRequest(BaseModel):
    records: list[Record]
    production: list[Production]
    profile: Profile


class ReportRequest(AnalyzeRequest):
    format: str = "buyer"
    period_from: str
    period_to: str
    reporting_start: str | None = None


def _run(req: AnalyzeRequest):
    recs = pd.DataFrame([r.model_dump() for r in req.records])
    prod = pd.DataFrame([p.model_dump() for p in req.production])
    return run(recs, prod, req.profile.model_dump(), verifier()), prod


@router.post("/extract")
async def extract(_: Auth, file: Annotated[UploadFile, File()]) -> dict:
    data = await file.read()
    return extract_document(data, file.filename or "upload", file.content_type or "application/pdf").as_dict()


@router.post("/analyze")
def analyze(_: Auth, req: AnalyzeRequest) -> dict:
    out, _prod = _run(req)
    return {
        "ledger": out["ledger"].to_dict(orient="records"),
        "summary": out["summary"].to_dict(orient="records"),
        "verification": out["verification"].to_dict(orient="records"),
        "suggestions": out["suggestions"],
        "issues": out["issues"],
    }


@router.post("/report", response_model=None)
def report(_: Auth, req: ReportRequest, pdf: bool = False) -> dict | Response:
    if req.format not in FORMATS:
        raise HTTPException(400, f"format must be one of {list(FORMATS)}")
    out, _prod = _run(req)
    reductions = None
    if req.reporting_start:
        periods = out["monthly"].period.tolist()
        base_end = periods[max(periods.index(req.reporting_start) - 1, 0)]
        _, reductions = prove_reduction(out["monthly"].dropna(subset=["pieces"]), base_end, req.reporting_start)
    facts = build_facts(req.profile.model_dump(), out["summary"], out["ledger"], out["verification"],
                        reductions, req.period_from, req.period_to)
    rep = generate(req.format, facts)
    if pdf:
        return Response(to_pdf(rep, out["ledger"]), media_type="application/pdf",
                        headers={"Content-Disposition": f"attachment; filename=C-Trace_{req.format}.pdf"})
    return rep
