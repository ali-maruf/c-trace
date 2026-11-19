"""The worker process drains queued jobs from Postgres and exits in --drain mode."""


from ctrace.store import service
from ctrace.store.db import org_tx
from ctrace.store.jobs import enqueue
from ctrace_worker.__main__ import main


def test_worker_drains_reextraction_job(owner_conn, demo, db_url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", db_url)
    with org_tx(owner_conn, demo["org_id"], demo["user_id"]):
        doc = service.list_documents(owner_conn, demo["factory_id"])[0]
        job_id = enqueue(owner_conn, demo["org_id"], "extract_document", {"document_id": doc["id"], "user_id": demo["user_id"]})
    main(["--drain"])
    with org_tx(owner_conn, demo["org_id"], demo["user_id"]):
        job = service.job_status(owner_conn, job_id)
        assert job["status"] == "done" and job["result"]["ledger_rows"] == 65
        assert len(service.get_document(owner_conn, doc["id"])["extractions"]) == 2  # original + re-extraction
