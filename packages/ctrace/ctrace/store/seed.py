"""Seed the FICTIONAL demo factory into Postgres.   python -m ctrace.store.seed [--reset]

Creates the demo user and organisation, Nodi Knit Composite Ltd. (fictional) with its profile, solar asset,
interventions and production, then uploads the 65 sample bills through the same path a real upload takes
(store → extraction job → activity record → ledger) and drains the job queue.
"""
from __future__ import annotations

import argparse
import json
import uuid

import pandas as pd

from .. import config
from . import service
from .db import Conn, connect, org_tx
from .factors_sync import sync_factors
from .jobs import run_pending
from .migrate import migrate
from .registry import sync_weather
from .storage import Storage

DEMO_EMAIL = "demo@c-trace.test"   # reserved .test domain: not a real mailbox
DEMO_USER_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "https://c-trace.test/users/demo"))
DEMO_ORG_NAME = "Nodi Knit Composite Ltd. (fictional demo)"
# Approximate centre of Gazipur district, for weather lookups. The factory itself is fictional.
DEMO_LAT, DEMO_LON = 24.000, 90.420


def find_demo(conn: Conn) -> tuple[str, str] | None:
    """Owner connection. (org_id, factory_id) of an existing demo seed."""
    row = conn.execute("select o.id as org_id, f.id as factory_id from organizations o join factories f on f.org_id = o.id "
                       "where o.name = %s order by f.created_at limit 1", (DEMO_ORG_NAME,)).fetchone()
    return (str(row["org_id"]), str(row["factory_id"])) if row else None


def seed_demo(conn: Conn, reset: bool = False, storage: Storage | None = None) -> dict:
    """Owner connection. Idempotent unless `reset`."""
    migrate(conn)
    sync_factors(conn)
    existing = find_demo(conn)
    if existing and not reset:
        org_id, factory_id = existing
        return {"org_id": org_id, "factory_id": factory_id, "user_id": DEMO_USER_ID, "created": False}
    if reset:
        conn.execute("delete from organizations where name = %s", (DEMO_ORG_NAME,))

    profile = json.loads((config.SAMPLES_DIR / "factory_profile.json").read_text())
    service.ensure_user(conn, DEMO_USER_ID, DEMO_EMAIL, "Demo manager (fictional)")
    org_id = service.create_organization(conn, DEMO_ORG_NAME, DEMO_USER_ID)

    with org_tx(conn, org_id, DEMO_USER_ID):
        factory = service.create_factory(conn, DEMO_USER_ID, {
            "name": profile["name"], "location": profile["location"], "lat": DEMO_LAT, "lon": DEMO_LON,
            "factory_type": profile["factory_type"], "workers": profile["workers"],
            "has_wet_processing": bool(profile["has_wet_processing"]), "captive_gas_gen": bool(profile.get("captive_gas_gen", 0)),
        })
        fid = factory["id"]
        for s in profile.get("solar", []):
            service.add_asset(conn, DEMO_USER_ID, fid, "solar", float(s["kwp"]), "kWp", s["from"])
        for iv in profile.get("interventions", []):
            service.add_intervention(conn, DEMO_USER_ID, fid, iv["measure"], iv["from"])
        prod = pd.read_csv(config.SAMPLES_DIR / "production.csv")
        service.upsert_production(conn, DEMO_USER_ID, fid, list(zip(prod.period, prod.pieces.astype(float), strict=True)),
                                  "Fictional demo data: data/samples/production.csv")
        for path in sorted(config.BILLS_DIR.glob("*.pdf")):
            service.ingest_document(conn, DEMO_USER_ID, fid, path.name, path.read_bytes(), "application/pdf", storage)

    sync_weather(conn, DEMO_LAT, DEMO_LON)
    counts = run_pending(conn, "seed")
    return {"org_id": org_id, "factory_id": fid, "user_id": DEMO_USER_ID, "created": True, "jobs": counts}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--reset", action="store_true", help="delete and recreate the demo organisation")
    args = ap.parse_args()
    with connect() as conn:
        out = seed_demo(conn, reset=args.reset)
        with org_tx(conn, out["org_id"], out["user_id"]):
            totals = service.summary(conn, out["factory_id"])["totals"]
    state = "seeded" if out["created"] else "already seeded (use --reset to recreate)"
    print(f"[C-Trace] demo factory {state}: org {out['org_id']}, factory {out['factory_id']}")
    if out.get("jobs"):
        print(f"[C-Trace] extraction jobs: {out['jobs']}")
    if totals:
        print(f"[C-Trace] {totals['months']} months · total {totals['total_tco2e']:,.1f} tCO2e "
              f"(Scope 1 {totals['scope1_tco2e']:,.1f}, Scope 2 {totals['scope2_location_tco2e']:,.1f})")


if __name__ == "__main__":
    main()
