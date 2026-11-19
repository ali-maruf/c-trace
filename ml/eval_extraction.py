"""Score C-Trace document extraction on the labelled eval set and refresh the model card.

    uv run python ml/eval_extraction.py                         # rules, plus haiku/sonnet/cascade if a key is set
    uv run python ml/eval_extraction.py --methods rules haiku

Methods: `rules` (offline parser), `haiku` and `sonnet` (one model each), `cascade` (the production setting:
the extraction model, escalating to the escalation model below the confidence threshold). Model reads are
cached in data/cache/llm/, so re-running costs nothing unless the prompt or model changes.
Writes data/eval/results/<method>.json and re-renders models/MODEL_CARD.md.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import statistics
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from model_card import RESULTS_DIR, write_model_card  # noqa: E402

from ctrace import config  # noqa: E402
from ctrace.extraction import BillRecord, extract_pdf_rules, sha256_bytes  # noqa: E402
from ctrace.llm import FileCache, LLMCall  # noqa: E402
from ctrace.textnorm import normalize_text  # noqa: E402

BILLS_DIR = config.DATA_DIR / "eval" / "bills"
FIELDS = ["activity", "period", "quantity", "unit", "amount_bdt", "bill_no"]


def models_for(method: str) -> list[str]:
    return {"haiku": [config.CTRACE_EXTRACTION_MODEL], "sonnet": [config.CTRACE_EXTRACTION_ESCALATION_MODEL],
            "cascade": [m for m in (config.CTRACE_EXTRACTION_MODEL, config.CTRACE_EXTRACTION_ESCALATION_MODEL) if m],
            "rules": []}[method]


def read(method: str, path: Path, calls: list[LLMCall], cache: FileCache) -> BillRecord:
    data = path.read_bytes()
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    if method == "rules":
        if media_type != "application/pdf":
            rec = BillRecord(path.name, sha256_bytes(data))
            rec.issues.append("Rules parser cannot read photos")
            return rec
        try:
            return extract_pdf_rules(data, path.name)
        except Exception as exc:  # an unreadable PDF scores zero rather than aborting the eval
            rec = BillRecord(path.name, sha256_bytes(data))
            rec.issues.append(f"{type(exc).__name__}: {exc}")
            return rec
    from ctrace.extraction_llm import extract_with_llm
    try:
        return extract_with_llm(data, path.name, media_type, cache=cache, on_call=calls.append, models=models_for(method))
    except Exception as exc:
        rec = BillRecord(path.name, sha256_bytes(data))
        rec.issues.append(f"{type(exc).__name__}: {exc}"[:300])
        return rec


def _norm_id(s: str | None) -> str:
    return normalize_text(s or "").upper().replace(" ", "")


def score(label: dict, rec: BillRecord) -> dict:
    def close(a: float | None, b: float, tol: float) -> bool:
        return a is not None and abs(a - b) <= tol * abs(b)

    ok = {
        "activity": rec.activity == label["activity"],
        "period": rec.period == label["period"],
        "quantity": close(rec.quantity, label["quantity"], 0.005),
        "unit": rec.unit == label["unit"],
        "amount_bdt": close(rec.amount_bdt, label["amount_bdt"], 0.001),
        "bill_no": _norm_id(rec.bill_no) == _norm_id(label["bill_no"]),
    }
    evidence = json.dumps(rec.evidence, ensure_ascii=False)
    return {
        "file": label["file"], "group": f"{label['kind']}/{label['language']}", "layout": label["layout"],
        "correct": ok, "critical": ok["activity"] and ok["period"] and ok["quantity"],
        "confidence": rec.extraction_confidence, "method": rec.extraction_method,
        "predicted": {f: getattr(rec, f) for f in FIELDS}, "issues": rec.issues,
        "privacy_leak": any(n in evidence for n in label.get("person_names", [])),
        "tariff_flagged": any("tariff" in i.lower() for i in rec.issues),
        "escalated": len(rec.attempts) > 1,
    }


def aggregate(rows: list[dict], calls: list[LLMCall]) -> dict:
    n = len(rows)
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        groups[r["group"]].append(r)
    m: dict = {
        "field_accuracy": {f: round(sum(r["correct"][f] for r in rows) / n, 4) for f in FIELDS},
        "critical_accuracy": round(sum(r["critical"] for r in rows) / n, 4),
        "by_group": {g: {"n": len(rs), "critical_accuracy": round(sum(r["critical"] for r in rs) / len(rs), 4)}
                     for g, rs in sorted(groups.items())},
        "privacy_leaks": sum(r["privacy_leak"] for r in rows),
        "dropped_digit_flagged": all(r["tariff_flagged"] for r in rows if r["layout"] == "trap_dropped_digit"),
    }
    if calls:
        confident = [r for r in rows if r["confidence"] >= config.CTRACE_ESCALATE_BELOW]
        m["llm"] = {
            "calls": len(calls), "errors": sum(1 for c in calls if c.error),
            "input_tokens": sum(c.input_tokens or 0 for c in calls), "output_tokens": sum(c.output_tokens or 0 for c in calls),
            "median_latency_ms": int(statistics.median(c.latency_ms for c in calls)),
            "escalated": sum(r["escalated"] for r in rows),
            "n_at_threshold": len(confident),
            "precision_at_threshold": round(sum(r["critical"] for r in confident) / len(confident), 4) if confident else 0.0,
        }
    return m


def run(method: str, labels: list[dict], out_dir: Path = RESULTS_DIR) -> dict:
    calls: list[LLMCall] = []
    cache = FileCache()
    rows = [score(lb, read(method, BILLS_DIR / lb["file"], calls, cache)) for lb in labels]
    comp = Counter(f"{lb['kind'].replace('_', ' ')} ({lb['language']})" for lb in labels)
    result = {"method": method, "models": models_for(method),
              "prompt_version": config.CTRACE_EXTRACTION_PROMPT if method != "rules" else None,
              "run_at": datetime.now(UTC).isoformat(timespec="seconds"), "n_bills": len(labels),
              "composition": ", ".join(f"{v} {k}" for k, v in comp.most_common()),
              "metrics": aggregate(rows, calls), "bills": rows}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{method}.json").write_text(json.dumps(result, indent=1, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    return result


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--methods", nargs="+", choices=["rules", "haiku", "sonnet", "cascade"])
    args = ap.parse_args(argv)
    methods = args.methods or (["rules", "haiku", "sonnet", "cascade"] if config.llm_enabled() else ["rules"])
    if any(m != "rules" for m in methods) and not config.llm_enabled():
        raise SystemExit("C-Trace: LLM methods need ANTHROPIC_API_KEY.")
    labels = json.loads((BILLS_DIR / "labels.json").read_text(encoding="utf-8"))["bills"]
    for method in methods:
        r = run(method, labels)
        acc = r["metrics"]["field_accuracy"]
        print(f"[C-Trace] {method:8s} critical {r['metrics']['critical_accuracy']:.0%} · "
              + " · ".join(f"{f} {acc[f]:.0%}" for f in FIELDS) + f" · privacy leaks {r['metrics']['privacy_leaks']}")
    print(f"[C-Trace] model card: {write_model_card()}")


if __name__ == "__main__":
    main()
