"""C-Trace REST API.   Run:  make api  (uvicorn ctrace_api.main:app --reload)   → docs at http://localhost:8000/docs"""
from __future__ import annotations

from typing import Annotated

import pandas as pd
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from ctrace import BRAND, TAGLINE, __version__, config
from ctrace.extraction import extract_document
from ctrace.factors import factor_for
from ctrace.pipeline import run
from ctrace.reduction import prove_reduction
from ctrace.report import FORMATS, build_facts, generate, to_pdf
from ctrace.verify import Verifier

app = FastAPI(title=f"{BRAND} API", version=__version__, description=f"{TAGLINE} Carbon MRV for factories.")
_verifier = None


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


@app.get("/health")
def health():
    return {"service": BRAND, "version": __version__, "llm": config.llm_enabled(),
            "model": (config.MODELS_DIR / "ctrace_verifier.joblib").exists()}


@app.get("/v1/factors")
def factors(year: int = 2026):
    return {a: factor_for(a, year).as_dict() for a in ("natural_gas", "grid_electricity", "diesel")}


@app.post("/v1/extract")
async def extract(file: Annotated[UploadFile, File()]):
    data = await file.read()
    return extract_document(data, file.filename or "upload", file.content_type or "application/pdf").as_dict()


@app.post("/v1/analyze")
def analyze(req: AnalyzeRequest):
    out, _ = _run(req)
    return {
        "ledger": out["ledger"].to_dict(orient="records"),
        "summary": out["summary"].to_dict(orient="records"),
        "verification": out["verification"].to_dict(orient="records"),
        "suggestions": out["suggestions"],
        "issues": out["issues"],
    }


@app.post("/v1/report")
def report(req: ReportRequest, pdf: bool = False):
    if req.format not in FORMATS:
        raise HTTPException(400, f"format must be one of {list(FORMATS)}")
    out, _ = _run(req)
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
