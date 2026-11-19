"""C-Trace worker: runs queued jobs (document extraction, ledger rebuild) from the Postgres `jobs` table.

    python -m ctrace_worker            # poll forever
    python -m ctrace_worker --drain    # process everything due, then exit
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import socket
import time

from ctrace.store.db import connect
from ctrace.store.jobs import run_pending
from ctrace.store.migrate import migrate
from ctrace.store.verification import drift_check, drift_due

log = logging.getLogger("ctrace.worker")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="C-Trace background worker")
    ap.add_argument("--drain", action="store_true", help="process due jobs and exit")
    ap.add_argument("--poll", type=float, default=float(os.getenv("CTRACE_WORKER_POLL_SECONDS", "1.0")))
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    stopping = False

    def stop(*_: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    with connect() as conn:
        migrate(conn)
        while not stopping:
            counts = run_pending(conn, worker_id, max_jobs=50)
            if not args.drain and drift_due(conn):
                with conn.transaction():
                    log.info(json.dumps({"event": "drift_check", **{k: str(v) for k, v in drift_check(conn).items()}}))
            if any(counts.values()):
                log.info(json.dumps({"event": "jobs_processed", "worker": worker_id, **counts}))
            elif args.drain:
                break
            else:
                time.sleep(args.poll)


if __name__ == "__main__":
    main()
