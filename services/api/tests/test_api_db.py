"""C-Trace API on Postgres: parity with the prototype, traceability, tenancy, review flow, auth."""
from __future__ import annotations

import json
import time
import uuid

import pandas as pd
import pytest

from ctrace import config
from ctrace.extraction import extract_pdf_rules
from ctrace.factors import FUEL_FACTORS
from ctrace.pipeline import run
from ctrace.store import service
from ctrace.store.db import org_tx
from ctrace.store.jobs import run_pending


def auth(token: str, org: str | None = None) -> dict:
    h = {"Authorization": f"Bearer {token}"}
    if org:
        h["X-C-Trace-Org"] = org
    return h


@pytest.fixture(scope="module")
def prototype() -> dict:
    recs = pd.DataFrame([extract_pdf_rules(p).as_dict() for p in sorted(config.BILLS_DIR.glob("*.pdf"))])
    prod = pd.read_csv(config.SAMPLES_DIR / "production.csv")
    prof = json.loads((config.SAMPLES_DIR / "factory_profile.json").read_text())
    return run(recs, prod, prof, None)


@pytest.fixture(scope="module")
def demo_headers(demo, token) -> dict:
    return auth(token(demo["user_id"], "demo@c-trace.test"))


def new_member(owner_conn, org_id: str | None, role: str, org_name: str | None = None) -> tuple[str, str]:
    """Create a user; add to `org_id` with `role`, or to a new organisation as owner. Returns (user_id, org_id)."""
    uid = str(uuid.uuid4())
    service.ensure_user(owner_conn, uid, f"{uid[:8]}@c-trace.test")
    if org_id is None:
        org_id = service.create_organization(owner_conn, org_name or f"Test org {uid[:6]}", uid)
    else:
        owner_conn.execute("insert into memberships (org_id, user_id, role) values (%s, %s, %s)", (org_id, uid, role))
    return uid, org_id


# ------------------------------------------------------------------ the Phase 2 acceptance check
def test_summary_matches_prototype(client, demo, demo_headers, prototype):
    r = client.get(f"/v1/factories/{demo['factory_id']}/summary", headers=demo_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    proto = prototype["summary"]
    t = body["totals"]
    assert t["months"] == len(proto) == 22
    assert t["total_tco2e"] == pytest.approx(proto.total_t.sum(), abs=1e-4)
    assert t["scope1_tco2e"] == pytest.approx(proto.scope1_t.sum(), abs=1e-4)
    assert t["scope2_location_tco2e"] == pytest.approx(proto.scope2_t.sum(), abs=1e-4)
    by_month = {m["period"]: m for m in body["months"]}
    for row in proto.itertuples():
        assert by_month[row.period]["total_t"] == pytest.approx(row.total_t, abs=1e-6)
        assert by_month[row.period]["kgco2e_per_piece"] == pytest.approx(row.kgco2e_per_piece, abs=1e-6)
    assert body["data_status"]["documents"] == 65
    assert body["data_status"]["unconfirmed_records"] == 65  # nothing reviewed yet


def test_ledger_rows_trace_to_bill_and_cited_factor(client, demo, demo_headers, prototype):
    rows = client.get(f"/v1/factories/{demo['factory_id']}/ledger", headers=demo_headers).json()
    assert {r["trace_id"] for r in rows} == set(prototype["ledger"].trace_id)
    for r in rows:
        assert r["factor_id"] and r["factor_version"] == "v1"
        assert r["factor_source_url"].startswith("https://")
        assert len(r["doc_sha256"]) == 64 and r["doc_name"].endswith(".pdf")
        assert str(r["quantity"]).replace(".0", "") in r["calculation"].replace(",", "")
    one = client.get(f"/v1/factories/{demo['factory_id']}/ledger", params={"period": "2026-07"}, headers=demo_headers).json()
    assert {r["activity"] for r in one} == {"natural_gas", "grid_electricity"}  # the diesel receipt is missing


def test_factors_endpoint_lists_versioned_sourced_factors(client):
    rows = client.get("/v1/factors").json()
    ids = {r["id"] for r in rows}
    assert {"ipcc2006-natural_gas-v1", "ipcc2006-diesel-v1"} <= ids
    assert all(r["source_url"] for r in rows)


def test_health_reports_database(client):
    body = client.get("/health").json()
    assert body["service"] == "C-Trace" and body["database"] == "ok"
    assert "X-Request-ID" in client.get("/health").headers


# ------------------------------------------------------------------ tenancy
def test_other_org_cannot_see_demo_data(client, owner_conn, demo, token):
    uid, other_org = new_member(owner_conn, None, "owner")
    h = auth(token(uid))
    assert client.get("/v1/factories", headers=h).json() == []
    assert client.get(f"/v1/factories/{demo['factory_id']}", headers=h).status_code == 404
    assert client.get(f"/v1/factories/{demo['factory_id']}/summary", headers=h).status_code == 404
    with org_tx(owner_conn, other_org, uid):
        assert owner_conn.execute("select count(*) n from ledger_entries").fetchone()["n"] == 0
        assert owner_conn.execute("select count(*) n from documents").fetchone()["n"] == 0


def test_rls_without_org_sees_nothing(owner_conn, demo):
    with owner_conn.transaction():
        owner_conn.execute("set local role ctrace_app")
        for table in ("factories", "documents", "activity_records", "ledger_entries", "audit_log", "jobs"):
            assert owner_conn.execute(f"select count(*) n from {table}").fetchone()["n"] == 0, table


def test_rls_blocks_writes_into_another_org(owner_conn, demo):
    uid, other_org = new_member(owner_conn, None, "owner")
    import psycopg
    with pytest.raises(psycopg.errors.InsufficientPrivilege), org_tx(owner_conn, other_org, uid):
        owner_conn.execute("insert into production_records (org_id, factory_id, period, pieces, source) "
                           "values (%s, %s, '2030-01', 1, 'x')", (demo["org_id"], demo["factory_id"]))


def test_app_role_cannot_edit_factors_or_audit_log(owner_conn, demo):
    import psycopg
    with pytest.raises(psycopg.errors.InsufficientPrivilege), org_tx(owner_conn, demo["org_id"], demo["user_id"]):
        owner_conn.execute("update emission_factors set kgco2e_per_unit = 0")
    with pytest.raises(psycopg.errors.InsufficientPrivilege), org_tx(owner_conn, demo["org_id"], demo["user_id"]):
        owner_conn.execute("delete from audit_log")


# ------------------------------------------------------------------ upload → review → ledger
@pytest.fixture(scope="module")
def small_factory(client, owner_conn, token):
    """A separate organisation with three gas bills, uploaded through the API (2025-09 has the dropped digit)."""
    uid, org = new_member(owner_conn, None, "owner")
    h = auth(token(uid))
    f = client.post("/v1/factories", headers=h, json={"name": "Test Knit (fictional)", "factory_type": "knit_composite",
                                                      "workers": 1500, "has_wet_processing": True}).json()
    files = [("files", (n, (config.BILLS_DIR / n).read_bytes(), "application/pdf"))
             for n in ("2025-08_natural_gas.pdf", "2025-09_natural_gas.pdf", "2025-10_natural_gas.pdf")]
    r = client.post(f"/v1/factories/{f['id']}/documents", headers=h, files=files)
    assert r.status_code == 202, r.text
    uploads = r.json()
    assert all(not u["duplicate"] and u["job_id"] for u in uploads)
    assert run_pending(owner_conn)["done"] == 3
    for u in uploads:
        assert client.get(f"/v1/jobs/{u['job_id']}", headers=h).json()["status"] == "done"
    return {"user_id": uid, "org_id": org, "factory_id": f["id"], "headers": h}


def test_reupload_is_idempotent(client, small_factory):
    p = config.BILLS_DIR / "2025-08_natural_gas.pdf"
    r = client.post(f"/v1/factories/{small_factory['factory_id']}/documents", headers=small_factory["headers"],
                    files=[("files", ("renamed.pdf", p.read_bytes(), "application/pdf"))])
    u = r.json()[0]
    assert u["duplicate"] is True and u["job_id"] is None
    assert u["message"].startswith("Already uploaded on ") and "2025-08_natural_gas.pdf" in u["message"]


def test_unsupported_upload_rejected(client, small_factory):
    r = client.post(f"/v1/factories/{small_factory['factory_id']}/documents", headers=small_factory["headers"],
                    files=[("files", ("notes.txt", b"hello", "text/plain"))])
    assert r.status_code == 422


def test_correction_needs_review_role_and_is_audited(client, owner_conn, small_factory, token):
    fid, h = small_factory["factory_id"], small_factory["headers"]
    sugg = client.get(f"/v1/factories/{fid}/suggestions", headers=h).json()
    assert [s["period"] for s in sugg] == ["2025-09"]
    s = sugg[0]
    before = client.get(f"/v1/factories/{fid}/summary", headers=h).json()["totals"]["total_tco2e"]

    viewer, _ = new_member(owner_conn, small_factory["org_id"], "viewer")
    body = {"quantity": s["suggested_quantity"], "reason": "Accepted C-Trace tariff suggestion: printed value off by ×10"}
    denied = client.post(f"/v1/activity-records/{s['activity_record_id']}/correct", headers=auth(token(viewer)), json=body)
    assert denied.status_code == 403

    ok = client.post(f"/v1/activity-records/{s['activity_record_id']}/correct", headers=h, json=body)
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == "corrected" and ok.json()["confirmed_by"] == small_factory["user_id"]

    after = client.get(f"/v1/factories/{fid}/summary", headers=h).json()
    delta = (s["suggested_quantity"] - s["printed_quantity"]) * FUEL_FACTORS["natural_gas"].kgco2e_per_unit / 1000
    assert after["totals"]["total_tco2e"] - before == pytest.approx(delta, abs=0.01)
    assert after["data_status"]["records_by_status"] == {"extracted": 2, "corrected": 1}
    assert client.get(f"/v1/factories/{fid}/suggestions", headers=h).json() == []

    trail = client.get(f"/v1/activity-records/{s['activity_record_id']}/audit", headers=h).json()
    assert trail[-1]["action"] == "activity_record.correct" and trail[-1]["user_id"] == small_factory["user_id"]
    assert trail[-1]["before"]["quantity"] == s["printed_quantity"]
    assert trail[-1]["after"]["quantity"] == s["suggested_quantity"] and "tariff" in trail[-1]["after"]["reason"]


def test_reject_removes_record_from_ledger(client, small_factory):
    fid, h = small_factory["factory_id"], small_factory["headers"]
    recs = client.get(f"/v1/factories/{fid}/activity-records", headers=h, params={"status": "extracted"}).json()
    target = next(r for r in recs if r["period"] == "2025-10")
    assert client.post(f"/v1/activity-records/{target['id']}/reject", headers=h, json={"reason": "x"}).status_code == 422
    r = client.post(f"/v1/activity-records/{target['id']}/reject", headers=h, json={"reason": "Duplicate of supplier statement"})
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    periods = {row["period"] for row in client.get(f"/v1/factories/{fid}/ledger", headers=h).json()}
    assert "2025-10" not in periods
    confirm = client.post(f"/v1/activity-records/{target['id']}/confirm", headers=h, json={"reason": "Rejected in error"})
    assert confirm.json()["status"] == "confirmed"
    assert "2025-10" in {row["period"] for row in client.get(f"/v1/factories/{fid}/ledger", headers=h).json()}


def test_production_csv_upload_sets_intensity(client, small_factory):
    fid, h = small_factory["factory_id"], small_factory["headers"]
    csv = b"period,pieces\n2025-08,300000\n2025-09,310000\n2025-10,305000\n"
    r = client.post(f"/v1/factories/{fid}/production/csv", headers=h, files={"file": ("prod.csv", csv, "text/csv")})
    assert r.json() == {"months": 3}
    t = client.get(f"/v1/factories/{fid}/summary", headers=h).json()["totals"]
    assert t["pieces"] == 915000 and t["kgco2e_per_piece"] == pytest.approx(t["total_tco2e"] * 1000 / 915000, abs=1e-4)
    bad = client.post(f"/v1/factories/{fid}/production/csv", headers=h, files={"file": ("p.csv", b"month,qty\n1,2\n", "text/csv")})
    assert bad.status_code == 422


def test_buyer_cannot_open_raw_bills(client, owner_conn, small_factory, token):
    buyer, _ = new_member(owner_conn, small_factory["org_id"], "buyer")
    docs = client.get(f"/v1/factories/{small_factory['factory_id']}/documents", headers=small_factory["headers"]).json()
    did = docs[0]["id"]
    assert client.get(f"/v1/documents/{did}/file", headers=auth(token(buyer))).status_code == 403
    r = client.get(f"/v1/documents/{did}/file", headers=small_factory["headers"])
    assert r.status_code == 200 and r.content.startswith(b"%PDF")


# ------------------------------------------------------------------ jobs
def test_failing_job_retries_then_fails(client, owner_conn, small_factory):
    h = small_factory["headers"]
    r = client.post(f"/v1/factories/{small_factory['factory_id']}/documents", headers=h,
                    files=[("files", ("broken.pdf", b"this is not a pdf", "application/pdf"))])
    job_id = r.json()[0]["job_id"]
    outcomes = []
    for _ in range(3):
        owner_conn.execute("update jobs set run_after = now() where id = %s", (job_id,))
        c = run_pending(owner_conn)
        outcomes.append("failed" if c["failed"] else "retry" if c["retry"] else "none")
    assert outcomes == ["retry", "retry", "failed"]
    job = client.get(f"/v1/jobs/{job_id}", headers=h).json()
    assert job["status"] == "failed" and job["attempts"] == 3 and job["last_error"]


# ------------------------------------------------------------------ auth
def test_auth_failures(client, owner_conn, token, dev_keys):
    assert client.get("/v1/factories").status_code == 401
    assert client.get("/v1/factories", headers=auth("not-a-jwt")).status_code == 401
    assert client.get("/v1/factories", headers=auth(token(str(uuid.uuid4())))).status_code == 403  # no C-Trace account
    expired = token(str(uuid.uuid4()), hours=-1)
    assert client.get("/v1/factories", headers=auth(expired)).status_code == 401
    import jwt
    forged = jwt.encode({"sub": "x", "aud": "authenticated", "iss": "c-trace-dev", "exp": int(time.time()) + 60},
                        "a-guessed-shared-secret-that-is-long-enough", algorithm="HS256")
    assert client.get("/v1/factories", headers=auth(forged)).status_code == 401


def test_multi_org_user_must_choose(client, owner_conn, demo, token):
    uid, org_a = new_member(owner_conn, None, "owner")
    owner_conn.execute("insert into memberships (org_id, user_id, role) values (%s, %s, 'viewer')", (demo["org_id"], uid))
    assert client.get("/v1/me", headers=auth(token(uid))).status_code == 400
    me = client.get("/v1/me", headers=auth(token(uid), demo["org_id"])).json()
    assert me["org_id"] == demo["org_id"] and me["role"] == "viewer" and len(me["memberships"]) == 2
    assert client.post("/v1/factories", headers=auth(token(uid), demo["org_id"]),
                       json={"name": "x", "factory_type": "woven", "workers": 10}).status_code == 403
    assert client.get("/v1/me", headers=auth(token(uid), str(uuid.uuid4()))).status_code == 403


@pytest.mark.skipif(config.llm_enabled(), reason="asserts the offline rules path")
def test_stateless_extract_requires_auth_and_works_offline(client, demo_headers):
    path = config.BILLS_DIR / "2025-09_natural_gas.pdf"
    files = {"file": (path.name, path.read_bytes(), "application/pdf")}
    assert client.post("/v1/stateless/extract", files=files).status_code == 401
    rec = client.post("/v1/stateless/extract", files=files, headers=demo_headers).json()
    assert rec["activity"] == "natural_gas" and rec["period"] == "2025-09"
    assert any("tariff" in i.lower() for i in rec["issues"])


def test_llm_rate_limit_applies_per_user(monkeypatch):
    from fastapi import HTTPException

    from ctrace_api import ratelimit
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "test-key-not-real")
    limiter = ratelimit.SlidingWindow(per_minute=2)
    monkeypatch.setattr(ratelimit, "llm_limiter", limiter)
    ratelimit.limit_llm("u1")
    ratelimit.limit_llm("u1")
    ratelimit.limit_llm("u2")
    with pytest.raises(HTTPException) as e:
        ratelimit.limit_llm("u1")
    assert e.value.status_code == 429 and "Retry-After" in (e.value.headers or {})


def test_too_many_files_in_one_upload(client, small_factory):
    files = [("files", (f"f{i}.pdf", b"%PDF-1.4 x" + bytes([i]), "application/pdf")) for i in range(51)]
    r = client.post(f"/v1/factories/{small_factory['factory_id']}/documents", headers=small_factory["headers"], files=files)
    assert r.status_code == 413


# ------------------------------------------------------------------ phase 4: registry, weather, benchmark
def test_official_grid_factor_admin_path(client, owner_conn, demo, token, monkeypatch):
    payload = {"kgco2e_per_unit": 0.67, "valid_from": "2025-07-01", "valid_to": "2026-06-30",
               "source": "Fictional test notice (not an official value)", "source_url": "https://example.org/grid-ef"}
    member = auth(token(demo["user_id"], "demo@c-trace.test"))
    assert client.post("/v1/admin/factors", headers=member, json=payload).status_code == 403
    monkeypatch.setenv("CTRACE_PLATFORM_ADMIN_EMAILS", "demo@c-trace.test")
    r = client.post("/v1/admin/factors", headers=member, json=payload)
    assert r.status_code == 201, r.text
    f = r.json()
    assert f["kind"] == "official" and f["id"].startswith("official-bgd-grid-20250701")
    assert client.post("/v1/admin/factors", headers=member, json={**payload, "valid_from": "2026-01-01"}).status_code == 422  # overlap

    summary = client.get(f"/v1/factories/{demo['factory_id']}/summary", headers=member).json()["totals"]
    stat = summary["scope2_statutory"]
    assert stat["months_covered"] == 12 and stat["factor_ids"] == [f["id"]]
    led = client.get(f"/v1/factories/{demo['factory_id']}/ledger", headers=member,
                     params={"activity": "grid_electricity"}).json()
    kwh = sum(x["quantity"] for x in led if "2025-07" <= x["period"] <= "2026-06")
    assert stat["scope2_statutory_tco2e"] == pytest.approx(kwh * 0.67 / 1000, rel=1e-6)
    assert all(x["factor_id"].startswith("ember-bgd-") for x in led)  # the ledger itself is unchanged
    with org_tx(owner_conn, demo["org_id"], demo["user_id"]):
        assert owner_conn.execute("select count(*) n from audit_log where action = 'factor.create_official'").fetchone()["n"] == 1
    owner_conn.execute("delete from emission_factors where id = %s", (f["id"],))


def test_factory_weather_has_sources(client, demo, demo_headers):
    rows = client.get(f"/v1/factories/{demo['factory_id']}/weather", headers=demo_headers).json()
    by = {r["period"]: r for r in rows}
    assert len(rows) == 22 and by["2025-07"]["source"].startswith("NASA POWER") and by["2025-07"]["cdd"] > 100
    assert by["2026-10"]["source"].startswith("Dhaka monthly climatology")


def test_benchmark_is_context_not_a_grade(client, demo, demo_headers):
    b = client.get(f"/v1/factories/{demo['factory_id']}/benchmark", headers=demo_headers).json()
    assert b["n_facilities"] > 1000 and 0 <= b["factory_percentile"] <= 100
    assert b["quantiles_tco2e"]["p10"] <= b["quantiles_tco2e"]["p50"] <= b["quantiles_tco2e"]["p90"]
    assert "not" in b["caveat"] and b["licence"].startswith("CC BY 4.0") and b["factory_months_used"] == 12
    sources = client.get("/v1/data-sources").json()
    assert {"owid_energy", "climate_trace_bgd_textiles"} <= set(sources)
