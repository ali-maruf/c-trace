"""C-Trace REST API.   Run:  make api   → docs at http://localhost:8000/docs"""
from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from ctrace import BRAND, TAGLINE, __version__, config
from ctrace.store import service
from ctrace.store.db import configure, connect, database_url, environment
from ctrace.store.factors_sync import sync_factors, sync_kb
from ctrace.store.migrate import migrate

from . import stateless, v1
from .observability import RequestContextMiddleware, setup_logging

log = logging.getLogger("ctrace.api")


def _migrate_on_start() -> bool:
    return os.getenv("CTRACE_MIGRATE_ON_START", "1" if environment() == "development" else "0") == "1"


_start_lock = threading.Lock()


def ensure_started(app: FastAPI) -> None:
    """Migrate (if enabled) and open the connection pool once per process.

    Called from the ASGI lifespan and, as a fallback, on the first request, so a serverless platform that
    skips lifespan events still gets a working app.
    """
    if getattr(app.state, "pool", None) is not None:
        return
    with _start_lock:
        if getattr(app.state, "pool", None) is not None:
            return
        setup_logging()
        url = database_url()
        if _migrate_on_start():
            with connect(url) as conn:
                migrate(conn)
                sync_factors(conn)
                sync_kb(conn)
        pool = ConnectionPool(url, min_size=int(os.getenv("CTRACE_DB_POOL_MIN", "1")), max_size=int(os.getenv("CTRACE_DB_POOL_MAX", "10")),
                              open=True, kwargs={"autocommit": True, "row_factory": dict_row}, configure=configure)
        app.state.pool = pool
        log.info("startup", extra={"fields": {"env": environment(), "llm": config.llm_enabled()}})


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    try:
        ensure_started(app)
    except Exception:  # keep serving: /health reports it and other routes answer 503 until the database is reachable
        log.exception("startup failed; will retry on the next request")
    try:
        yield
    finally:
        pool = getattr(app.state, "pool", None)
        if pool is not None:
            pool.close()
            app.state.pool = None


app = FastAPI(
    title=f"{BRAND} API", version=__version__, lifespan=lifespan,
    description=f"{TAGLINE} Verifiable carbon MRV for factories. Every number traces to a source document and a cited factor.",
    openapi_tags=[
        {"name": "auth"}, {"name": "factories"}, {"name": "production"}, {"name": "documents"},
        {"name": "review", "description": "Human-in-the-loop confirmation and corrections (audit-logged)"},
        {"name": "emissions"}, {"name": "verification", "description": "C-Trace Verifier: expected use, bands, drivers, flags"},
        {"name": "reductions", "description": "Proven savings per intervention, reviewer sign-off, certificate"},
        {"name": "reports", "description": "C-Trace Reports: buyer, IFRS S2 lender, BSEC; share links"},
        {"name": "shared", "description": "Public, read-only views of shared reports"},
        {"name": "copilot", "description": "C-Trace Copilot: answers from the factory's own data, cited and number-guarded"},
        {"name": "settings"},
        {"name": "factors"},
        {"name": "stateless", "description": "Prototype endpoints that store nothing"},
    ],
)
app.add_middleware(RequestContextMiddleware)


@app.middleware("http")
async def _started(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    if getattr(request.app.state, "pool", None) is None:
        from starlette.concurrency import run_in_threadpool
        try:
            await run_in_threadpool(ensure_started, request.app)
        except Exception as exc:
            log.exception("startup failed")
            if not request.url.path.endswith("/health"):
                return JSONResponse({"detail": f"C-Trace could not reach its database ({type(exc).__name__})."}, status_code=503)
    return await call_next(request)


API_PREFIX = os.getenv("CTRACE_API_PREFIX", "").rstrip("/")  # e.g. "/api" behind a path-based rewrite
app.include_router(v1.router, prefix=API_PREFIX)
app.include_router(stateless.router, prefix=API_PREFIX)


@app.exception_handler(service.NotFound)
async def not_found(_: Request, exc: service.NotFound) -> JSONResponse:
    return JSONResponse({"detail": f"{exc} not found"}, status_code=404)


@app.exception_handler(FileNotFoundError)
async def missing_model(_: Request, exc: FileNotFoundError) -> JSONResponse:
    return JSONResponse({"detail": str(exc)}, status_code=503)


@app.exception_handler(ValueError)
async def invalid(_: Request, exc: ValueError) -> JSONResponse:
    return JSONResponse({"detail": str(exc)}, status_code=422)


@app.get(f"{API_PREFIX}/health", tags=["auth"], summary="Liveness and dependency status (no auth)")
def health(request: Request) -> dict:
    db, db_ms = "ok", None
    try:
        pool = getattr(request.app.state, "pool", None)
        if pool is None:
            raise RuntimeError("not started")
        with pool.connection() as conn:
            t = time.perf_counter()
            conn.execute("select 1")
            db_ms = round((time.perf_counter() - t) * 1000, 1)
    except Exception as exc:  # report, don't crash the health check
        db = f"error: {type(exc).__name__}"
    return {"service": BRAND, "version": __version__, "llm": config.llm_enabled(),
            "model": config.verifier_path().exists(), "database": db,
            "database_roundtrip_ms": db_ms}
