"""Extraction v2 through the store: call log, org-scoped cache, prompt version on the extraction row."""
from __future__ import annotations

import uuid

import pytest

from ctrace import config, extraction_llm
from ctrace.store import service
from ctrace.store.db import org_tx
from ctrace.store.jobs import enqueue, run_pending


def make_org(conn) -> tuple[str, str, str]:  # type: ignore[no-untyped-def]
    uid = str(uuid.uuid4())
    service.ensure_user(conn, uid, f"{uid[:8]}@c-trace.test")
    org = service.create_organization(conn, f"LLM test org {uid[:6]}", uid)
    with org_tx(conn, org, uid):
        f = service.create_factory(conn, uid, {"name": "Test (fictional)", "factory_type": "woven", "workers": 900})
    return uid, org, f["id"]


@pytest.fixture
def llm(monkeypatch, fakes):  # type: ignore[no-untyped-def]
    payload = fakes.bangla_gas_bill()
    fake = fakes.FakeLLM({config.CTRACE_EXTRACTION_MODEL: fakes.response(payload)})
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "test-key-not-real")
    monkeypatch.setattr(extraction_llm, "default_client", lambda: fake)
    return fake


def test_worker_logs_calls_caches_per_org_and_records_prompt_version(owner_conn, llm):
    data = b"%PDF-1.4 fictional bangla photo stand-in " + uuid.uuid4().bytes
    results = {}
    for label in ("a", "b"):
        uid, org, fid = make_org(owner_conn)
        with org_tx(owner_conn, org, uid):
            doc = service.ingest_document(owner_conn, uid, fid, "bn_gas.pdf", data, "application/pdf").document
        assert run_pending(owner_conn)["done"] == 1
        results[label] = (uid, org, fid, doc["id"])
    assert len(llm.calls) == 2  # same bytes in two organisations: the cache is not shared across orgs

    uid, org, fid, did = results["a"]
    with org_tx(owner_conn, org, uid):
        calls = owner_conn.execute("select * from llm_calls").fetchall()
        assert len(calls) == 1 and calls[0]["input_tokens"] == 1200 and calls[0]["prompt_version"] == "extract_bill/v2"
        assert calls[0]["purpose"] == "extract_bill" and calls[0]["error"] is None
        ext = service.get_document(owner_conn, did)["extractions"][0]
        assert ext["method"] == "llm" and ext["model"] == config.CTRACE_EXTRACTION_MODEL and ext["prompt_version"] == "extract_bill/v2"
        assert ext["field_evidence"]["consumption_quantity"]["confidence"] == 0.95
        rec = service.records_frame(owner_conn, fid).iloc[0]
        assert (rec.period, rec.quantity, rec.unit) == ("2025-09", 32044.0, "m3")
        assert owner_conn.execute("select count(*) n from llm_cache").fetchone()["n"] == 1
        enqueue(owner_conn, org, "extract_document", {"document_id": did, "user_id": uid})
    assert run_pending(owner_conn)["done"] == 1
    assert len(llm.calls) == 2  # re-extraction in the same org is served from the cache
    with org_tx(owner_conn, org, uid):
        assert owner_conn.execute("select count(*) n from llm_calls").fetchone()["n"] == 1
        assert len(service.get_document(owner_conn, did)["extractions"]) == 2
