"""Shared pytest fixtures: a throwaway, migrated C-Trace database and an authenticated API client.

Database: `TEST_DATABASE_URL` (CI's pgvector service) or the local pgserver used for development.
Either way each test session gets its own uniquely named database, dropped at the end.
"""
from __future__ import annotations

import os
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def db_url() -> Iterator[str]:
    psycopg = pytest.importorskip("psycopg")
    from psycopg import sql
    from psycopg.conninfo import make_conninfo

    base = os.getenv("TEST_DATABASE_URL")
    if not base:
        pgserver = pytest.importorskip("pgserver", reason="install dev deps (make setup) or set TEST_DATABASE_URL")
        from ctrace import config
        base = pgserver.get_server(config.ROOT / ".pgdata", cleanup_mode=None).get_uri()
    name = f"ctrace_test_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(base, autocommit=True) as c:
        c.execute(sql.SQL("create database {}").format(sql.Identifier(name)))
    url = make_conninfo(base, dbname=name)
    from ctrace.store.db import connect
    from ctrace.store.factors_sync import sync_factors, sync_kb
    from ctrace.store.migrate import migrate
    with connect(url) as conn:
        migrate(conn)
        sync_factors(conn)
        sync_kb(conn)
    try:
        yield url
    finally:
        with psycopg.connect(base, autocommit=True) as c:
            c.execute(sql.SQL("drop database if exists {} with (force)").format(sql.Identifier(name)))


@pytest.fixture(scope="session")
def storage_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    d = tmp_path_factory.mktemp("storage")
    os.environ["CTRACE_STORAGE_DIR"] = str(d)
    return d


@pytest.fixture(scope="session")
def owner_conn(db_url: str, storage_dir: Path):  # type: ignore[no-untyped-def]
    from ctrace.store.db import connect
    with connect(db_url) as conn:
        yield conn


@pytest.fixture(scope="session")
def demo(owner_conn) -> dict:  # type: ignore[no-untyped-def]
    from ctrace.store.seed import seed_demo
    return seed_demo(owner_conn)


@pytest.fixture(scope="session")
def dev_keys(tmp_path_factory: pytest.TempPathFactory) -> bytes:
    from ctrace_api.devtoken import ensure_keys
    d = tmp_path_factory.mktemp("keys")
    private = ensure_keys(d / "dev.pem", d / "dev.pub.pem")
    os.environ["CTRACE_DEV_JWT_PUBLIC_KEY"] = str(d / "dev.pub.pem")
    return private


@pytest.fixture(scope="session")
def token(dev_keys: bytes) -> Callable[..., str]:
    from ctrace_api.devtoken import make_token

    def make(user_id: str, email: str = "user@c-trace.test", hours: float = 1) -> str:
        return make_token(user_id, email, hours, private_key=dev_keys)
    return make


@pytest.fixture(scope="session")
def client(db_url: str, storage_dir: Path, dev_keys: bytes):  # type: ignore[no-untyped-def]
    from fastapi.testclient import TestClient
    os.environ["DATABASE_URL"] = db_url
    os.environ["CTRACE_INLINE_JOBS"] = "0"   # tests drain the queue explicitly; test_inline_jobs turns it on
    from ctrace_api.main import app
    try:
        with TestClient(app) as c:
            yield c
    finally:
        os.environ.pop("DATABASE_URL", None)
