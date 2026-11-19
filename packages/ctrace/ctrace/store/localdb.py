"""Local development Postgres (pgserver: Postgres 16 + pgvector, data in `<workspace>/.pgdata`).

    python -m ctrace.store.localdb start   # start (or reuse) and print the connection string
    python -m ctrace.store.localdb stop
"""
from __future__ import annotations

import sys

from .. import config
from .db import local_database_url


def stop() -> None:
    import pgserver

    pgdata = config.ROOT / ".pgdata"
    if not (pgdata / "postmaster.pid").exists():
        print("[C-Trace] local Postgres is not running")
        return
    pgserver.pg_ctl(["-m", "fast", "stop"], pgdata=pgdata)  # type: ignore[attr-defined]  # generated at import
    print("[C-Trace] local Postgres stopped")


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "start"
    if cmd == "start":
        print(local_database_url())
    elif cmd == "stop":
        stop()
    else:
        sys.exit("usage: python -m ctrace.store.localdb [start|stop]")


if __name__ == "__main__":
    main()
