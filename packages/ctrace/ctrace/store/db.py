"""Postgres connections for C-Trace.

* `DATABASE_URL` is used when set (Supabase, CI, Docker).
* In development without `DATABASE_URL`, C-Trace starts (or reuses) an embedded Postgres 16 + pgvector
  from the `pgserver` dev dependency, with its data in `<workspace>/.pgdata`.

Connections are autocommit, so every `with conn.transaction()` is a real top-level transaction and
`SET LOCAL` settings never leak between requests.
"""
from __future__ import annotations

import contextlib
import io
import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from uuid import UUID

import psycopg
from psycopg.conninfo import make_conninfo
from psycopg.rows import DictRow, dict_row
from psycopg.types.numeric import FloatLoader

from .. import config

LOCAL_DB_NAME = "ctrace"
Conn = psycopg.Connection[DictRow]


def environment() -> str:
    return os.getenv("CTRACE_ENV", "development")


def configure(conn: psycopg.Connection[Any]) -> None:
    """Return NUMERIC as float: ledger arithmetic happens in Python, values are stored as computed.

    Behind a transaction-mode pooler (Supabase port 6543, PgBouncer) server-side prepared statements break,
    so `CTRACE_DB_PREPARE=0` turns them off.
    """
    conn.adapters.register_loader("numeric", FloatLoader)
    if os.getenv("CTRACE_DB_PREPARE", "1") == "0":
        conn.prepare_threshold = None


# libpq accepts only its own URI parameters; providers add extras (e.g. Supabase's `supa=`), which libpq rejects.
_LIBPQ_PARAMS = {"sslmode", "sslrootcert", "sslcert", "sslkey", "connect_timeout", "application_name", "options",
                 "target_session_attrs", "keepalives", "keepalives_idle", "gssencmode", "channel_binding"}


def clean_url(url: str) -> str:
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
    if "://" not in url:
        return url  # key=value conninfo
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query) if k in _LIBPQ_PARAMS]
    netloc = parts.netloc
    # Supabase's pooler serves transaction mode on 6543 and session mode on 5432 (same host, IPv4). C-Trace uses session
    # mode: it behaves like a direct connection (pipelined executemany, server settings), and the pool per instance is small.
    if (parts.hostname or "").endswith(".pooler.supabase.com") and parts.port == 6543 and os.getenv("CTRACE_DB_SESSION_POOLER", "1") == "1":
        netloc = netloc[: netloc.rfind(":")] + ":5432"
    return urlunsplit(parts._replace(netloc=netloc, query=urlencode(query)))


def database_url() -> str:
    """`DATABASE_URL`, else `POSTGRES_URL` (set by Vercel's Supabase and Postgres integrations: the IPv4 pooler)."""
    url = os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL")
    if url:
        return clean_url(url)
    if environment() != "development":
        raise RuntimeError("C-Trace: DATABASE_URL must be set when CTRACE_ENV is not 'development'.")
    return local_database_url()


def local_database_url(dbname: str = LOCAL_DB_NAME) -> str:
    try:
        import pgserver
    except ImportError as exc:  # pragma: no cover - dev dependency missing
        raise RuntimeError("C-Trace: set DATABASE_URL, or run `make setup` to install the local Postgres (pgserver).") from exc
    with contextlib.redirect_stdout(io.StringIO()):  # pgserver prints diagnostics; keep stdout for our JSON logs
        server = pgserver.get_server(config.ROOT / ".pgdata", cleanup_mode=None)
    base = server.get_uri()
    ensure_database(base, dbname)
    return make_conninfo(base, dbname=dbname)


def ensure_database(server_url: str, dbname: str) -> None:
    with psycopg.connect(server_url, autocommit=True) as c:
        exists = c.execute("select 1 from pg_database where datname = %s", (dbname,)).fetchone()
        if not exists:
            c.execute(psycopg.sql.SQL("create database {}").format(psycopg.sql.Identifier(dbname)))


def connect(url: str | None = None) -> Conn:
    conn = psycopg.connect(url or database_url(), autocommit=True, row_factory=dict_row)
    configure(conn)
    return conn


@contextmanager
def org_tx(conn: Conn, org_id: UUID | str, user_id: UUID | str | None = None) -> Iterator[Conn]:
    """A transaction running as the RLS-restricted role `ctrace_app`, scoped to one organisation."""
    with conn.transaction():
        conn.execute("set local role ctrace_app")
        conn.execute("select set_config('ctrace.org_id', %s, true), set_config('ctrace.user_id', %s, true)",
                     (str(org_id), str(user_id) if user_id else ""))
        yield conn
