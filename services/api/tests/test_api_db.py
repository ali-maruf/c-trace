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


# ------------------------------------------------------------------ phase 5: persisted verification
@pytest.fixture(scope="module")
def demo_copy(client, owner_conn, token):
    """The fictional demo factory loaded into its own organisation (so review actions don't touch `demo`)."""
    from ctrace.store.registry import sync_weather
    uid, org = new_member(owner_conn, None, "owner")
    prof = json.loads((config.SAMPLES_DIR / "factory_profile.json").read_text())
    with org_tx(owner_conn, org, uid):
        f = service.create_factory(owner_conn, uid, {"name": "Demo copy (fictional)", "factory_type": prof["factory_type"],
                                                     "workers": prof["workers"], "has_wet_processing": True, "captive_gas_gen": True,
                                                     "lat": 24.0, "lon": 90.42})
        service.add_asset(owner_conn, uid, f["id"], "solar", 600, "kWp", "2026-04")
        for iv in prof["interventions"]:
            service.add_intervention(owner_conn, uid, f["id"], iv["measure"], iv["from"])
        prod = pd.read_csv(config.SAMPLES_DIR / "production.csv")
        service.upsert_production(owner_conn, uid, f["id"], list(zip(prod.period, prod.pieces.astype(float), strict=True)), "fictional")
        for p in sorted(config.BILLS_DIR.glob("*.pdf")):
            service.ingest_document(owner_conn, uid, f["id"], p.name, p.read_bytes(), "application/pdf")
    sync_weather(owner_conn, 24.0, 90.42)
    run_pending(owner_conn)
    return {"user_id": uid, "org_id": org, "factory_id": f["id"], "headers": auth(token(uid))}


def low_months(body: dict) -> set[str]:
    return {r["period"] for r in body["results"] if r["confidence"] == "Low"}


def test_verification_review_loop(client, owner_conn, demo_copy, token, monkeypatch):
    fid, h = demo_copy["factory_id"], demo_copy["headers"]
    assert client.get(f"/v1/factories/{fid}/verification", headers=h).status_code == 404   # nothing run yet
    viewer, _ = new_member(owner_conn, demo_copy["org_id"], "viewer")
    assert client.post(f"/v1/factories/{fid}/verification/run", headers=auth(token(viewer))).status_code == 403

    first = client.post(f"/v1/factories/{fid}/verification/run", headers=h)
    assert first.status_code == 200, first.text
    body = first.json()
    assert low_months(body) == {"2025-09", "2026-07"} and body["stale"] is False
    assert body["run"]["model_version"] == "C-Trace Verifier v2" and "NASA POWER monthly T2M (MERRA-2)" in body["run"]["weather_source"]
    jul = next(r for r in body["results"] if r["period"] == "2025-07")["z"]["grid_kwh"]
    assert jul["expected_lo"] <= jul["expected"] <= jul["expected_hi"] and set(jul["drivers"]) >= {"typical", "production", "weather"}

    s = next(x for x in client.get(f"/v1/factories/{fid}/suggestions", headers=h).json() if x["period"] == "2025-09")
    client.post(f"/v1/activity-records/{s['activity_record_id']}/correct", headers=h,
                json={"quantity": s["suggested_quantity"], "reason": "Accepted tariff-based correction (dropped digit)"})
    assert client.get(f"/v1/factories/{fid}/verification", headers=h).json()["stale"] is True
    second = client.post(f"/v1/factories/{fid}/verification/run", headers=h).json()
    assert low_months(second) == {"2026-07"} and second["stale"] is False

    run_id = second["run"]["id"]
    bad = client.post(f"/v1/verification/{run_id}/2026-07/resolve", headers=h, json={"resolution": "evidence_requested", "comment": ""})
    assert bad.status_code == 422
    ok = client.post(f"/v1/verification/{run_id}/2026-07/resolve", headers=h,
                     json={"resolution": "evidence_requested", "comment": "Asked the store for the July diesel receipt"})
    assert ok.status_code == 200 and ok.json()["resolution"].startswith("evidence_requested: Asked")
    latest = client.get(f"/v1/factories/{fid}/verification", headers=h).json()
    assert next(r for r in latest["results"] if r["period"] == "2026-07")["resolved_by"] == demo_copy["user_id"]
    with org_tx(owner_conn, demo_copy["org_id"], demo_copy["user_id"]):
        actions = [r["action"] for r in owner_conn.execute("select action from audit_log where action like 'verification.%' order by id")]
    assert actions == ["verification.run", "verification.run", "verification.resolve"]

    assert client.get("/v1/admin/drift", headers=h).status_code == 403
    monkeypatch.setenv("CTRACE_PLATFORM_ADMIN_EMAILS", "user@c-trace.test")  # the test tokens' email
    checks = client.get("/v1/admin/drift", headers=h, params={"run": True}).json()
    assert checks[0]["model_version"] == "C-Trace Verifier v2" and checks[0]["n_factories"] >= 1
    assert checks[0]["alert"] is False   # too few months to judge: never a false alarm


# ------------------------------------------------------------------ phase 6: reductions and certificate
def make_demo_copy(client, owner_conn, token) -> dict:  # type: ignore[no-untyped-def]
    from ctrace.store.registry import sync_weather
    uid, org = new_member(owner_conn, None, "owner")
    prof = json.loads((config.SAMPLES_DIR / "factory_profile.json").read_text())
    with org_tx(owner_conn, org, uid):
        f = service.create_factory(owner_conn, uid, {"name": "Reduction demo (fictional)", "factory_type": prof["factory_type"],
                                                     "workers": prof["workers"], "has_wet_processing": True, "captive_gas_gen": True,
                                                     "lat": 24.0, "lon": 90.42, "location": "Gazipur, Bangladesh"})
        service.add_asset(owner_conn, uid, f["id"], "solar", 600, "kWp", "2026-04")
        for iv in prof["interventions"]:
            service.add_intervention(owner_conn, uid, f["id"], iv["measure"], iv["from"])
        prod = pd.read_csv(config.SAMPLES_DIR / "production.csv")
        service.upsert_production(owner_conn, uid, f["id"], list(zip(prod.period, prod.pieces.astype(float), strict=True)), "fictional")
        for p in sorted(config.BILLS_DIR.glob("*.pdf")):
            service.ingest_document(owner_conn, uid, f["id"], p.name, p.read_bytes(), "application/pdf")
    sync_weather(owner_conn, 24.0, 90.42)
    run_pending(owner_conn)
    return {"user_id": uid, "org_id": org, "factory_id": f["id"], "headers": auth(token(uid))}


def test_reduction_proof_sign_off_and_certificate(client, owner_conn, token):
    import io as _io

    import pdfplumber

    from ctrace.factors import factor_for
    d = make_demo_copy(client, owner_conn, token)
    fid, h = d["factory_id"], d["headers"]
    ivs = client.get(f"/v1/factories/{fid}", headers=h).json()["interventions"]
    assert {i["measure"]: i["affects"] for i in ivs} == {"Boiler economiser + steam-trap repair": ["natural_gas"],
                                                         "600 kWp rooftop solar": ["grid_electricity"]}
    assert client.post(f"/v1/factories/{fid}/reductions/compute", headers=h).status_code == 422   # verification first

    client.post(f"/v1/factories/{fid}/verification/run", headers=h)
    before = {r["activity"]: r for r in client.post(f"/v1/factories/{fid}/reductions/compute", headers=h).json()}
    gas0 = before["natural_gas"]
    assert gas0["excluded"]["baseline"] == ["2025-09"] and gas0["n_baseline"] == 11
    assert gas0["verified"] is False and "baseline months" in gas0["status_reason"]
    assert client.post(f"/v1/reductions/{gas0['id']}/sign", headers=h, json={"comment": "looks fine"}).status_code == 422

    s = next(x for x in client.get(f"/v1/factories/{fid}/suggestions", headers=h).json() if x["period"] == "2025-09")
    client.post(f"/v1/activity-records/{s['activity_record_id']}/correct", headers=h,
                json={"quantity": s["suggested_quantity"], "reason": "Accepted tariff-based correction (dropped digit)"})
    assert client.post(f"/v1/factories/{fid}/reductions/compute", headers=h).status_code == 422   # stale verification
    client.post(f"/v1/factories/{fid}/verification/run", headers=h)
    after = {r["activity"]: r for r in client.post(f"/v1/factories/{fid}/reductions/compute", headers=h).json()}
    gas, grid = after["natural_gas"], after["grid_electricity"]
    assert gas["verified"] and grid["verified"] and gas["n_baseline"] == 12 and not gas["excluded"]["baseline"]
    assert 0.08 < gas["avoided"] / gas["predicted"] < 0.16        # planted ~12% boiler saving
    assert gas["low"] > 0 and gas["checks"]["cv_rmse"]["pass"] and abs(gas["nmbe"]) < 0.05
    ef = factor_for("natural_gas", 2026).kgco2e_per_unit / 1000
    assert gas["tco2e"] == pytest.approx(gas["avoided"] * ef, rel=1e-3)
    assert gas["bdt_saved"] == pytest.approx(gas["avoided"] * gas["tariff_bdt"], rel=1e-3)
    assert grid["excluded"]["reporting"] == []                     # July's missing diesel does not touch grid data
    assert {doc["file_name"] for doc in gas["documents"]} >= {"2025-01_natural_gas.pdf", "2026-10_natural_gas.pdf"}

    def text(r) -> str:  # type: ignore[no-untyped-def]
        with pdfplumber.open(_io.BytesIO(r.content)) as pdf:
            return "\n".join(pg.extract_text() or "" for pg in pdf.pages)

    draft = client.get(f"/v1/reductions/{gas['id']}/certificate.pdf", headers=h)
    assert draft.status_code == 200 and "DRAFT" in text(draft) and "_DRAFT.pdf" in draft.headers["content-disposition"]
    viewer, _ = new_member(owner_conn, d["org_id"], "viewer")
    assert client.post(f"/v1/reductions/{gas['id']}/sign", headers=auth(token(viewer)), json={"comment": "ok then"}).status_code == 403
    signed = client.post(f"/v1/reductions/{gas['id']}/sign", headers=h,
                         json={"comment": "Checked baseline bills and the economiser commissioning report"})
    assert signed.status_code == 200 and signed.json()["status"] == "signed"
    cert = text(client.get(f"/v1/reductions/{gas['id']}/certificate.pdf", headers=h))
    assert "Reduction Certificate" in cert and "not a third-party assurance or carbon credit" in cert
    assert "Signed by" in cert and "economiser commissioning" in cert and "DRAFT" not in cert
    assert gas["documents"][0]["trace_id"] in cert and "ipcc2006-natural_gas-v1" in cert

    client.post(f"/v1/factories/{fid}/reductions/compute", headers=h)   # recompute: signed proof is kept
    current = client.get(f"/v1/factories/{fid}/reductions", headers=h).json()
    assert sum(r["status"] == "signed" for r in current) == 1 and {r["status"] for r in current} == {"signed", "draft"}


# ------------------------------------------------------------------ live demo sandboxes, onboarding, inline jobs
def test_demo_sandbox_is_private_and_contained(client, owner_conn, demo, monkeypatch):
    r = client.post("/v1/demo/start")
    assert r.status_code == 200, r.text
    d = r.json()
    h = {"Authorization": f"Bearer {d['token']}"}
    me = client.get("/v1/me", headers=h).json()
    assert me["org_id"] == d["org_id"] and me["role"] == "owner"
    t = client.get(f"/v1/factories/{d['factory_id']}/summary", headers=h).json()["totals"]
    assert t["total_tco2e"] == pytest.approx(15466.6004, abs=1e-3)          # same fictional data as the seed
    second = client.post("/v1/demo/start").json()
    assert second["org_id"] != d["org_id"]                                 # every visitor gets their own sandbox
    assert client.get(f"/v1/factories/{second['factory_id']}", headers=h).status_code == 404

    # even if a demo identity were made a member of a real organisation, its token cannot reach it
    uid = client.get("/v1/me", headers=h).json()["user_id"]
    owner_conn.execute("insert into memberships (org_id, user_id, role) values (%s, %s, 'viewer')", (demo["org_id"], uid))
    assert client.get("/v1/factories", headers={**h, "X-C-Trace-Org": demo["org_id"]}).status_code == 403
    monkeypatch.setenv("CTRACE_PLATFORM_ADMIN_EMAILS", me["email"])
    assert client.post("/v1/admin/factors", headers=h, json={}).status_code in (403, 422)
    assert client.post("/v1/admin/factors", headers=h, json={
        "kgco2e_per_unit": 0.5, "valid_from": "2030-01-01", "source": "x" * 10, "source_url": "https://example.org"}).status_code == 403
    monkeypatch.setenv("CTRACE_DEMO_ENABLED", "0")
    assert client.post("/v1/demo/start").status_code == 404


def test_expired_demo_sandboxes_are_purged(client, owner_conn):
    d = client.post("/v1/demo/start").json()
    owner_conn.execute("update organizations set created_at = now() - interval '25 hours' where id = %s", (d["org_id"],))
    client.post("/v1/demo/start")
    assert owner_conn.execute("select count(*) n from organizations where id = %s", (d["org_id"],)).fetchone()["n"] == 0


def test_onboarding_creates_account_once(client, token):
    uid = str(uuid.uuid4())
    h = auth(token(uid, f"{uid[:8]}@c-trace.test"))
    assert client.get("/v1/me", headers=h).status_code == 403
    r = client.post("/v1/onboarding", headers=h, json={"organization_name": "Test Apparel Ltd. (fictional)", "display_name": "Test"})
    assert r.status_code == 201 and r.json()["role"] == "owner"
    assert client.get("/v1/me", headers=h).json()["role"] == "owner"
    assert client.post("/v1/onboarding", headers=h, json={"organization_name": "Again"}).status_code == 409
    demo_token = client.post("/v1/demo/start").json()["token"]
    assert client.post("/v1/onboarding", headers={"Authorization": f"Bearer {demo_token}"},
                       json={"organization_name": "Nope"}).status_code == 403


def test_inline_jobs_read_bills_in_the_upload_request(client, small_factory, monkeypatch):
    monkeypatch.setenv("CTRACE_INLINE_JOBS", "1")
    p = config.BILLS_DIR / "2025-11_natural_gas.pdf"
    r = client.post(f"/v1/factories/{small_factory['factory_id']}/documents", headers=small_factory["headers"],
                    files=[("files", (p.name, p.read_bytes(), "application/pdf"))]).json()[0]
    assert r["job"]["status"] == "done" and r["job"]["result"]["ledger_rows"] >= 1


def test_db_storage_backend(client, small_factory, monkeypatch):
    monkeypatch.setenv("CTRACE_STORAGE", "db")
    monkeypatch.setenv("CTRACE_INLINE_JOBS", "1")
    p = config.BILLS_DIR / "2025-12_natural_gas.pdf"
    r = client.post(f"/v1/factories/{small_factory['factory_id']}/documents", headers=small_factory["headers"],
                    files=[("files", (p.name, p.read_bytes(), "application/pdf"))]).json()[0]
    assert r["job"]["status"] == "done"
    f = client.get(f"/v1/documents/{r['document']['id']}/file", headers=small_factory["headers"])
    assert f.content == p.read_bytes()
