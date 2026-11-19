"""Postgres-backed job queue (`jobs` table, `FOR UPDATE SKIP LOCKED`). No Redis.

* `enqueue` runs inside an org-scoped transaction (RLS checks the org).
* `claim` / `complete` / `fail` run as the connection owner, because a worker serves every organisation;
  each handler then runs in an org-scoped transaction for the job's organisation.
* Failed jobs retry with exponential backoff until `max_attempts`; jobs left `running` by a crashed
  worker are reclaimed after `STALE_AFTER`.
"""
from __future__ import annotations

import json
import socket
import traceback
from collections.abc import Callable
from typing import Any

from .db import Conn, org_tx

STALE_AFTER = "10 minutes"
Handler = Callable[[Conn, dict], dict]
HANDLERS: dict[str, Handler] = {}


def handler(kind: str) -> Callable[[Handler], Handler]:
    def register(fn: Handler) -> Handler:
        HANDLERS[kind] = fn
        return fn
    return register


def enqueue(conn: Conn, org_id: str, kind: str, payload: dict, max_attempts: int = 3) -> int:
    row = conn.execute(
        "insert into jobs (org_id, kind, payload, max_attempts) values (%s, %s, %s, %s) returning id",
        (org_id, kind, json.dumps(payload), max_attempts),
    ).fetchone()
    assert row is not None
    return int(row["id"])


def claim(conn: Conn, worker_id: str) -> dict | None:
    with conn.transaction():
        return conn.execute(
            f"""update jobs set status = 'running', locked_at = now(), locked_by = %s, attempts = attempts + 1
                where id = (
                  select id from jobs
                  where (status = 'queued' and run_after <= now())
                     or (status = 'running' and locked_at < now() - interval '{STALE_AFTER}')
                  order by run_after, id
                  for update skip locked
                  limit 1)
                returning *""",
            (worker_id,),
        ).fetchone()


def complete(conn: Conn, job_id: int, result: dict) -> None:
    conn.execute("update jobs set status = 'done', result = %s, finished_at = now(), last_error = null where id = %s",
                 (json.dumps(result, default=str), job_id))


def fail(conn: Conn, job: dict, error: str) -> str:
    final = job["attempts"] >= job["max_attempts"]
    conn.execute(
        """update jobs set status = %s, last_error = %s, locked_at = null, locked_by = null,
               finished_at = case when %s then now() end,
               run_after = now() + make_interval(secs => power(2, attempts))
           where id = %s""",
        ("failed" if final else "queued", error[-4000:], final, job["id"]),
    )
    return "failed" if final else "queued"


def run_job(conn: Conn, job: dict) -> dict[str, Any]:
    fn = HANDLERS.get(job["kind"])
    if fn is None:
        raise LookupError(f"No C-Trace job handler for kind {job['kind']!r}")
    with org_tx(conn, job["org_id"], job["payload"].get("user_id")):
        return fn(conn, job["payload"])


def run_pending(conn: Conn, worker_id: str | None = None, max_jobs: int | None = None) -> dict[str, int]:
    """Process due jobs until the queue is empty (or `max_jobs`). Returns counts by outcome."""
    from . import service  # noqa: F401  (registers handlers)

    worker_id = worker_id or f"{socket.gethostname()}:drain"
    counts = {"done": 0, "retry": 0, "failed": 0}
    while max_jobs is None or sum(counts.values()) < max_jobs:
        job = claim(conn, worker_id)
        if job is None:
            break
        try:
            result = run_job(conn, job)
        except Exception as exc:
            outcome = fail(conn, job, f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=5)}")
            counts["failed" if outcome == "failed" else "retry"] += 1
        else:
            complete(conn, job["id"], result)
            counts["done"] += 1
    return counts
