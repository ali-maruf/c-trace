"""CI: after retraining, the released Verifier's headline metrics must match the committed ones.

    git show HEAD:models/ctrace-verifier/v2/metrics.json > /tmp/committed.json
    uv run python ml/train.py && uv run python ml/check_reproducible.py /tmp/committed.json
Tolerances allow for floating-point differences between platforms (multi-threaded tree fitting).
"""
import json
import sys
from pathlib import Path

from ctrace import config

TOL = 0.01


def headline(m: dict) -> dict[str, float]:
    out = {f"F1@low/{t}": d["F1@low"] for t, d in m["detection"].items()}
    out |= {f"coverage/{t}": r["band90_coverage"] for t, r in m["regression"].items()}
    out["false_low_rate"] = m["fairness"]["after_fix"]["overall"]
    return out


def main() -> None:
    committed = headline(json.loads(Path(sys.argv[1]).read_text())["metrics"])
    fresh_meta = json.loads((config.verifier_dir() / "metrics.json").read_text())
    fresh = headline(fresh_meta["metrics"])
    bad = {k: (committed[k], fresh[k]) for k in committed if abs(committed[k] - fresh[k]) > TOL}
    if bad or not fresh_meta["metrics"]["fairness"]["passes"]:
        raise SystemExit(f"[C-Trace] retrained Verifier differs from the committed one: {bad or 'fairness rule fails'}")
    print(f"[C-Trace] Verifier {fresh_meta['version']} reproduced within ±{TOL}: {len(fresh)} headline metrics")


if __name__ == "__main__":
    main()
