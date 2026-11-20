"""Scenario smoke test against a running C-Trace deployment (default: the hosted demo).

    uv run python scripts/smoke_live.py [BASE_URL]

Uses only the public demo sandbox (fictional data). Each scenario prints PASS/FAIL with the observed value.
"""
from __future__ import annotations

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

BASE = (sys.argv[1] if len(sys.argv) > 1 else "https://c-trace-xi.vercel.app").rstrip("/") + "/api"
ROOT = Path(__file__).resolve().parent.parent
EVAL = ROOT / "data" / "eval" / "bills"
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


def demo(c: httpx.Client) -> tuple[dict, str]:
    r = c.post(f"{BASE}/v1/demo/start", timeout=120)
    r.raise_for_status()
    d = r.json()
    return {"Authorization": f"Bearer {d['token']}"}, d["factory_id"]


def main() -> int:
    c = httpx.Client(timeout=120)
    h0 = c.get(f"{BASE}/health").json()
    check("health: database and model", h0["database"] == "ok" and h0["model"], json.dumps(h0))

    # 1. three visitors at once each get a complete, private sandbox
    t = time.time()
    with ThreadPoolExecutor(3) as ex:
        sandboxes = list(ex.map(lambda _: demo(httpx.Client(timeout=120)), range(3)))
    counts = [c.get(f"{BASE}/v1/factories/{f}/summary", headers=h).json()["data_status"]["documents"] for h, f in sandboxes]
    check("concurrent demo starts: 65 bills each", counts == [65, 65, 65], f"{counts} in {time.time() - t:.1f}s")
    (h, f), (_, f2) = sandboxes[0], sandboxes[1]
    check("sandboxes are isolated (RLS)", c.get(f"{BASE}/v1/factories/{f2}/summary", headers=h).status_code in (403, 404))

    # 2. auth
    check("no token -> 401", c.get(f"{BASE}/v1/factories/{f}/summary").status_code == 401)
    check("forged token -> 401", c.get(f"{BASE}/v1/factories/{f}/summary", headers={"Authorization": "Bearer x.y.z"}).status_code == 401)

    # 3. totals and flags as in the pitch
    s = c.get(f"{BASE}/v1/factories/{f}/summary", headers=h).json()
    check("demo total 15,297.9 tCO2e as extracted", round(s["totals"]["total_tco2e"], 1) == 15297.9, str(s["totals"]["total_tco2e"]))
    v = c.post(f"{BASE}/v1/factories/{f}/verification/run", headers=h).json()
    low = sorted(x["period"] for x in v["results"] if x["confidence"] == "Low")
    check("Verifier flags exactly 2025-09 and 2026-07", low == ["2025-09", "2026-07"], str(low))

    # 4. new uploads: English PDF, Bangla PDF, Bangla photo (offline: rules cannot read photos), dropped-digit trap
    labels = {b["file"]: b for b in json.loads((EVAL / "labels.json").read_text())["bills"]}
    for name in ["en_a_01.pdf", "bn_pdf_01.pdf", "en_trap_dropped_digit.pdf", "bn_photo_01.png"]:
        mt = "application/pdf" if name.endswith(".pdf") else "image/png"
        r = c.post(f"{BASE}/v1/factories/{f}/documents", headers=h, files={"files": (name, (EVAL / name).read_bytes(), mt)})
        body = r.json()[0] if r.status_code < 300 else r.json()
        job = body.get("job") or {}
        issues = (job.get("result") or {}).get("issues", [])
        check(f"upload {name}", r.status_code == 202, f"job={job.get('status')} issues={issues[:2]}")
    recs = c.get(f"{BASE}/v1/factories/{f}/activity-records", headers=h)
    if recs.status_code == 200:
        by_doc = {x["doc_name"]: x for x in recs.json()}
        for name in ["en_a_01.pdf", "bn_pdf_01.pdf"]:
            got, want = by_doc.get(name, {}), labels[name]
            check(f"extracted {name} quantity", got.get("quantity") == want["quantity"] and got.get("period") == want["period"],
                  f"got {got.get('quantity')} {got.get('period')} want {want['quantity']} {want['period']}")
        trap = by_doc.get("en_trap_dropped_digit.pdf", {})
        check("dropped-digit trap is flagged for review", bool(trap.get("issues")), str(trap.get("issues"))[:120])
    dup = c.post(f"{BASE}/v1/factories/{f}/documents", headers=h, files={"files": ("again.pdf", (EVAL / "en_a_01.pdf").read_bytes(), "application/pdf")})
    check("re-upload of identical bytes is a duplicate", dup.status_code == 202 and dup.json()[0].get("duplicate") is True)
    bad = c.post(f"{BASE}/v1/factories/{f}/documents", headers=h, files={"files": ("notes.txt", b"hello", "text/plain")})
    check("unsupported file type rejected", bad.status_code in (400, 415, 422), str(bad.status_code))

    # 5. reports in all three formats, downloads, share + revoke
    for fmt in ["buyer", "ifrs_s2", "bsec"]:
        r = c.post(f"{BASE}/v1/factories/{f}/reports", headers=h, json={"format": fmt, "period_from": "2025-01", "period_to": "2025-12"})
        ok = r.status_code == 201
        rid = r.json()["id"] if ok else None
        pdf = c.get(f"{BASE}/v1/reports/{rid}/download.pdf", headers=h) if ok else None
        check(f"report {fmt} + PDF", ok and pdf is not None and pdf.content[:4] == b"%PDF", f"{r.status_code} {len(pdf.content) if pdf else 0} bytes")
    sh = c.post(f"{BASE}/v1/reports/{rid}/share", headers=h, json={"days": 7}).json()
    token = sh["token"]
    check("share link opens without login", c.get(f"{BASE}/v1/shared/{token}").status_code == 200)
    check("unknown share token -> 404", c.get(f"{BASE}/v1/shared/{'x' * 43}").status_code == 404)
    c.post(f"{BASE}/v1/reports/{rid}/share/revoke", headers=h)
    check("revoked link stops working", c.get(f"{BASE}/v1/shared/{token}").status_code in (404, 410))

    # 6. Copilot: cited answer, Bangla, off-topic decline, made-up figures blocked
    def ask(q: str) -> dict:
        r = c.post(f"{BASE}/v1/factories/{f}/copilot/chat?stream=false", headers=h, json={"message": q})
        return r.json() if r.status_code == 200 else {"error": r.status_code, "text": r.text[:200]}
    a = ask("What were total emissions in 2025?")
    check("Copilot answer cites sources", bool(a.get("citations")), (a.get("text") or "")[:100])
    a = ask("২০২৫ সালে মোট নির্গমন কত?")
    check("Copilot answers in Bangla", a.get("language") == "bn", (a.get("text") or "")[:80])
    a = ask("Write me a poem about football")
    check("Copilot declines off-topic", a.get("declined") is True, (a.get("text") or "")[:80])

    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} scenarios passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
