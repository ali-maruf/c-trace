"""Postgres-backed job queue (`jobs` table, `FOR UPDATE SKIP LOCKED`). No Redis.

* `enqueue` runs inside an org-scoped transaction (RLS checks the org).
* `claim` / `complete` / `fail` run as the connection owner, because a worker serves every organisation;
  each handler then runs in an org-scoped transaction for the job's organisation.
* Failed jobs retry with exponential backoff until `max_attempts`; jobs left `running` by a crashed
  worker are reclaimed after `STALE_AFTER`.
"""
from __future__ import annotations

import json
import logging
import socket
import time
import traceback
from collections.abc import Callable
from typing import Any

from .db import Conn, org_tx

STALE_AFTER = "10 minutes"
Handler = Callable[[Conn, dict], dict]
HANDLERS: dict[str, tuple[Handler, bool]] = {}


def handler(kind: str, own_transactions: bool = False) -> Callable[[Handler], Handler]:
    """Register a job handler. By default it runs inside one org-scoped transaction and receives the payload.
    With `own_transactions=True` it receives the whole job and opens its own `org_tx` blocks, so slow work
    (an LLM call) does not hold a transaction open."""
    def register(fn: Handler) -> Handler:
        HANDLERS[kind] = (fn, own_transactions)
        return fn
    return register


def enqueue(conn: Conn, org_id: str, kind: str, payload: dict, max_attempts: int = 3) -> int:
    row = conn.execute(
        "insert into jobs (org_id, kind, payload, max_attempts) values (%s, %s, %s, %s) returning id",
        (org_id, kind, json.dumps(payload), max_attempts),
    ).fetchone()
    assert row is not None
    return int(row["id"])


def claim(conn: Conn, worker_id: str, org_id: str | None = None) -> dict | None:
    """Claim one due job (optionally only one organisation's) with FOR UPDATE SKIP LOCKED."""
    with conn.transaction():
        return conn.execute(
            f"""update jobs set status = 'running', locked_at = now(), locked_by = %s, attempts = attempts + 1
                where id = (
                  select id from jobs
                  where ((status = 'queued' and run_after <= now())
                         or (status = 'running' and locked_at < now() - interval '{STALE_AFTER}'))
                    and (%s::uuid is null or org_id = %s::uuid)
                  order by run_after, id
                  for update skip locked
                  limit 1)
                returning *""",
            (worker_id, org_id, org_id),
        ).fetchone()


def pending_count(conn: Conn, org_id: str) -> int:
    row = conn.execute("select count(*) n from jobs where org_id = %s and status in ('queued', 'running') and run_after <= now()",
                       (org_id,)).fetchone()
    return int(row["n"]) if row else 0


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
    entry = HANDLERS.get(job["kind"])
    if entry is None:
        raise LookupError(f"No C-Trace job handler for kind {job['kind']!r}")
    fn, own = entry
    if own:
        return fn(conn, job)
    with org_tx(conn, job["org_id"], job["payload"].get("user_id")):
        return fn(conn, job["payload"])


def run_pending(conn: Conn, worker_id: str | None = None, max_jobs: int | None = None, org_id: str | None = None,
                wait_s: float = 60.0) -> dict[str, int]:
    """Process due jobs until the queue is empty (or `max_jobs`). With `org_id`, only that organisation's jobs, and
    wait (up to `wait_s`) for any of them another process is still running, so the caller sees finished results.
    Returns counts by outcome."""
    from . import service  # noqa: F401  (registers handlers)

    worker_id = worker_id or f"{socket.gethostname()}:drain"
    deadline = time.monotonic() + wait_s
    counts = {"done": 0, "retry": 0, "failed": 0}
    while max_jobs is None or sum(counts.values()) < max_jobs:
        job = claim(conn, worker_id, org_id)
        if job is None:
            if org_id is not None and pending_count(conn, org_id) and time.monotonic() < deadline:
                time.sleep(0.2)   # another worker holds one of this organisation's jobs
                continue
            break
        t_job = time.monotonic()
        try:
            result = run_job(conn, job)
        except Exception as exc:
            outcome = fail(conn, job, f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=5)}")
            counts["failed" if outcome == "failed" else "retry"] += 1
        else:
            complete(conn, job["id"], result)
            counts["done"] += 1
            logging.getLogger("ctrace.jobs").info("job.done", extra={"fields": {"id": job["id"], "s": round(time.monotonic() - t_job, 2)}})
    return counts
