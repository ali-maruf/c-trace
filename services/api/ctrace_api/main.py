"""C-Trace REST API.   Run:  make api   → docs at http://localhost:8000/docs"""
from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from ctrace import BRAND, TAGLINE, __version__, config
from ctrace.store import service
from ctrace.store.db import configure, connect, database_url, environment
from ctrace.store.factors_sync import sync_factors
from ctrace.store.migrate import migrate

from . import stateless, v1
from .observability import RequestContextMiddleware, setup_logging

log = logging.getLogger("ctrace.api")


def _migrate_on_start() -> bool:
    return os.getenv("CTRACE_MIGRATE_ON_START", "1" if environment() == "development" else "0") == "1"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    setup_logging()
    url = database_url()
    if _migrate_on_start():
        with connect(url) as conn:
            migrate(conn)
            sync_factors(conn)
    pool = ConnectionPool(url, min_size=1, max_size=int(os.getenv("CTRACE_DB_POOL_MAX", "10")), open=True,
                          kwargs={"autocommit": True, "row_factory": dict_row}, configure=configure)
    app.state.pool = pool
    log.info("startup", extra={"fields": {"env": environment(), "llm": config.llm_enabled()}})
    try:
        yield
    finally:
        pool.close()


app = FastAPI(
    title=f"{BRAND} API", version=__version__, lifespan=lifespan,
    description=f"{TAGLINE} Verifiable carbon MRV for factories. Every number traces to a source document and a cited factor.",
    openapi_tags=[
        {"name": "auth"}, {"name": "factories"}, {"name": "production"}, {"name": "documents"},
        {"name": "review", "description": "Human-in-the-loop confirmation and corrections (audit-logged)"},
        {"name": "emissions"}, {"name": "verification", "description": "C-Trace Verifier: expected use, bands, drivers, flags"},
        {"name": "factors"},
        {"name": "stateless", "description": "Prototype endpoints that store nothing"},
    ],
)
app.add_middleware(RequestContextMiddleware)
app.include_router(v1.router)
app.include_router(stateless.router)


@app.exception_handler(service.NotFound)
async def not_found(_: Request, exc: service.NotFound) -> JSONResponse:
    return JSONResponse({"detail": f"{exc} not found"}, status_code=404)


@app.exception_handler(FileNotFoundError)
async def missing_model(_: Request, exc: FileNotFoundError) -> JSONResponse:
    return JSONResponse({"detail": str(exc)}, status_code=503)


@app.exception_handler(ValueError)
async def invalid(_: Request, exc: ValueError) -> JSONResponse:
    return JSONResponse({"detail": str(exc)}, status_code=422)


@app.get("/health", tags=["auth"], summary="Liveness and dependency status (no auth)")
def health(request: Request) -> dict:
    db = "ok"
    try:
        with request.app.state.pool.connection() as conn:
            conn.execute("select 1")
    except Exception as exc:  # report, don't crash the health check
        db = f"error: {type(exc).__name__}"
    return {"service": BRAND, "version": __version__, "llm": config.llm_enabled(),
            "model": config.verifier_path().exists(), "database": db}
