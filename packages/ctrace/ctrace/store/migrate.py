"""Apply C-Trace SQL migrations in order.   python -m ctrace.store.migrate

Files live in `infra/supabase/migrations/` (Supabase CLI naming, so `supabase db push` works too).
Applied versions are recorded in `ctrace_meta.schema_migrations`. An advisory lock makes concurrent
runs (API + worker starting together) safe.
"""
from __future__ import annotations

from pathlib import Path

from .. import config
from .db import Conn, connect

MIGRATIONS_DIR = config.ROOT / "infra" / "supabase" / "migrations"
_LOCK_KEY = 7_412_026  # arbitrary, constant advisory-lock key for C-Trace migrations


def pending(conn: Conn, directory: Path = MIGRATIONS_DIR) -> list[Path]:
    applied = {r["version"] for r in conn.execute("select version from ctrace_meta.schema_migrations")}
    return [p for p in sorted(directory.glob("*.sql")) if p.stem not in applied]


def migrate(conn: Conn, directory: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply pending migrations; return the versions applied."""
    done: list[str] = []
    with conn.transaction():
        conn.execute("select pg_advisory_xact_lock(%s)", (_LOCK_KEY,))
        conn.execute("create schema if not exists ctrace_meta")
        conn.execute("create table if not exists ctrace_meta.schema_migrations "
                     "(version text primary key, applied_at timestamptz not null default now())")
        for path in pending(conn, directory):
            conn.execute(path.read_text(encoding="utf-8"))  # trusted, repo-owned SQL
            conn.execute("insert into ctrace_meta.schema_migrations (version) values (%s)", (path.stem,))
            done.append(path.stem)
    return done


def main() -> None:
    from .factors_sync import sync_factors

    with connect() as conn:
        applied = migrate(conn)
        n = sync_factors(conn)
    print(f"[C-Trace] migrations applied: {applied or 'none pending'}; emission factors synced: {n}")


if __name__ == "__main__":
    main()
