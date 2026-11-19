"""Render models/MODEL_CARD.md from computed results only: the C-Trace Verifier's metrics.json and the
extraction eval results in data/eval/results/. Nothing in the card is typed in by hand.

    uv run python ml/model_card.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # noqa: E402 - allow `python ml/model_card.py`

from ctrace import config  # noqa: E402

RESULTS_DIR = config.DATA_DIR / "eval" / "results"
METHOD_ORDER = ["rules", "haiku", "sonnet", "cascade"]
FIELDS = ["activity", "period", "quantity", "unit", "amount_bdt", "bill_no"]


NAMES = {"gas_m3": "Natural gas", "grid_kwh": "Grid electricity", "diesel_l": "Diesel"}
HEADER = ["# C-Trace Model Card", "",
          "Two learned components: the **C-Trace Verifier** (is each month's energy plausible?) and **document "
          "extraction** (what does the bill say?). Neither produces an emissions number.", ""]


def verifier_section(meta: dict) -> list[str]:
    reg, det = meta["metrics"]["regression"], meta["metrics"]["detection"]
    names = NAMES
    lines = [
        f"## {meta.get('model', 'C-Trace Verifier v1')}", "",
        f"*Trained {meta['trained_date']} on {meta['trained_on']}.*", "",
        "### What it does",
        "Predicts how much natural gas, grid electricity and diesel a garment factory *should* have used in a month, "
        "given its production, type, assets and weather. C-Trace compares that to what the bills say and flags months "
        "that don't add up. It does **not** calculate emissions — that is deterministic code with IPCC/Ember factors.", "",
        "### Algorithm", meta["algorithm"] + ".", "",
        f"Features: `{', '.join(meta['features'])}`", "",
        f"Factories — train: {meta['n_train_factories']}, calibration: {meta['n_cal_factories']}, "
        f"held-out test: {meta['n_test_factories']} (split by factory, never seen in training).", "",
        "### Prediction quality (held-out factories)", "",
        "| Energy carrier | MAPE, new factory | MAPE after self-calibration | σ (log residual) |", "|---|---|---|---|",
    ]
    for t, m in reg.items():
        lines.append(f"| {names[t]} | {m['MAPE_fleet_model_new_factory']:.1%} | {m['MAPE_after_factory_calibration']:.1%} | {m['sigma_log_residual']:.3f} |")
    if any("band90_coverage" in m for m in reg.values()):
        lines += ["", "Expected band (split-conformal, target 90%, calibrated on factories not used for training), on held-out factories:", "",
                  "| Energy carrier | Coverage | Half-width (≈ ±%) |", "|---|---|---|"]
        lines += [f"| {names[t]} | {m['band90_coverage']:.1%} | ±{m['band90_halfwidth_pct']:.0f}% |" for t, m in reg.items()]
    at = "low" if all("F1@low" in m for m in det.values()) else "z3"
    where = "at the Low threshold the app uses (|z| ≥ 3 gas/grid, ≥ 5 diesel)" if at == "low" else "at |z| ≥ 3"
    lines += ["", f"### Error detection (5% injected errors, held-out factories, {where})", "",
              "| Energy carrier | ROC-AUC | Precision | Recall | F1 | Naive month-over-month rule F1 |", "|---|---|---|---|---|---|"]
    for t, m in det.items():
        lines.append(f"| {names[t]} | {m['ROC_AUC']:.3f} | {m[f'precision@{at}']:.1%} | {m[f'recall@{at}']:.1%} | {m[f'F1@{at}']:.1%} | {m['naive_MoM_rule_F1']:.1%} |")
    lines += ["", "Grading used in the app: gas and grid electricity → Medium at |z| ≥ 2.5, Low at |z| ≥ 3; "
              "diesel → Medium at |z| ≥ 3, Low at |z| ≥ 5 (diesel depends on unobserved grid outages). "
              "Document checks (implied tariff, missing or duplicate bills) can also set a month to Low.",
              "", "Recall by error type:", ""]
    for t, m in det.items():
        kinds = ", ".join(f"{k}: {v:.0%}" for k, v in m["recall_by_anomaly_type"].items())
        lines.append(f"- **{names[t]}** — {kinds}")
    comp = meta["metrics"].get("comparison_v1_same_test")
    if comp:
        lines += ["", f"### {meta['version']} vs v1 on the same held-out factories (same injected errors)", "",
                  f"| Energy carrier | ROC-AUC v1 → {meta['version']} | F1 v1 → {meta['version']} |", "|---|---|---|"]
        lines += [f"| {names[t]} | {comp[t]['ROC_AUC']:.3f} → {det[t]['ROC_AUC']:.3f} | {comp[t]['F1@low']:.1%} → {det[t]['F1@low']:.1%} |"
                  for t in det]
    fair = meta["metrics"].get("fairness")
    if fair:
        def row(label: str, f: dict) -> str:
            s = f["by_size"]
            r = f["small_to_large_ratio"]
            return (f"| {label} | {s['small']['rate']:.2%} | {s['medium']['rate']:.2%} | {s['large']['rate']:.2%} | "
                    f"{'—' if r is None else f'{r:.2f}×'} |")
        e = fair["before_fix"]["worker_tercile_edges"]
        lines += ["", "### Fairness: false-flag rate on clean held-out months", "",
                  f"Share of months with no injected error that the model alone would grade **Low**, by factory size "
                  f"(worker terciles: small ≤ {e[0]:,}, medium ≤ {e[1]:,}, large > {e[1]:,}). Release rule: small "
                  f"factories must not be flagged more than {fair['threshold_ratio']}× as often as large ones.", "",
                  "| Calibration | Small | Medium | Large | Small ÷ large |", "|---|---|---|---|---|",
                  row("Global σ", fair["before_fix"])]
        if fair.get("fix_applied"):
            lines.append(row("Heteroscedastic, rate-controlled σ(x) (released)", fair["after_fix"]))
        final = fair.get("after_fix", fair["before_fix"])
        lines += ["", "| Factory type | False-flag rate | Months |", "|---|---|---|"]
        lines += [f"| {k.replace('_', ' ')} | {v['rate']:.2%} | {v['n_months']:,} |" for k, v in final["by_type"].items()]
        lines += ["", f"Result: **{'passes' if fair['passes'] else 'FAILS'}** the release rule"
                  + (f" after the fix ({fair['method']})." if fair.get("fix_applied") else " without any calibration change.")]
        worse = [k.replace("_", " ") for k, v in final["by_type"].items() if v["rate"] > 2 * max(final["overall"], 1e-9)]
        if worse:
            lines.append(f"\nStill to fix: **{', '.join(worse)}** factories are flagged more than twice as often as the fleet "
                         f"({final['overall']:.2%}). The per-type calibration uses the pooled multiplier where a type has "
                         "too few calibration months; more pilot data for these types is the fix, not a looser threshold.")
    if meta.get("drift_baseline_sigma"):
        lines += ["", "### Monitoring",
                  "A weekly job compares the spread of recent calibrated residuals (all factories' latest verification runs) "
                  "with the training σ per carrier and raises an alert when it moves by more than 30%: "
                  + ", ".join(f"{names[t]} σ₀ = {s:.3f}" for t, s in meta["drift_baseline_sigma"].items()) + "."]
    lines += [
        "", "### Limitations — read before quoting these numbers",
        "- **Training data is simulated.** Bangladesh has no open factory-level monthly energy dataset. The C-Trace "
        "simulator (`packages/ctrace/ctrace/simulator.py`) encodes transparent engineering assumptions (kWh/piece, steam per piece, "
        "captive gas generation, outages, solar yield, Eid shutdowns). Metrics show the method works on data with this "
        "structure; they are **not** proof of real-world accuracy. Retrain with pilot data: `uv run python ml/train.py --data pilot.csv`.",
        "- Small under-reporting (<30%) is hard to separate from genuine efficiency gains; C-Trace grades such months "
        "Medium and asks for evidence rather than accusing.",
        "- Diesel use depends on grid outages, which are noisy, so diesel flags are weaker.",
        "", "### Responsible use",
        "- A flag means *please check this document*, never *this factory is cheating*. A human reviews every Low grade.",
        "- No personal data is used. Factory data stays private to the factory unless it chooses to share a report.",
        "- Every reported tonne is traceable to a source document hash and an emission factor with a citation.",
    ]
    return lines


def extraction_section(results_dir: Path = RESULTS_DIR) -> list[str]:
    runs = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted(results_dir.glob("*.json"))}
    lines = ["", "## Document extraction (eval set: `data/eval/bills/`)", ""]
    if not runs:
        return lines + ["No extraction eval has been run yet: `make eval-extraction`.", ""]
    any_run = next(iter(runs.values()))
    lines += [f"{any_run['n_bills']} fictional, labelled bills: {any_run['composition']}. Field accuracy is the share of "
              "bills where the field matches the label (quantity within 0.5%, amount within 0.1%). "
              "\"Critical\" means activity, period and quantity are all correct.", "",
              "| Method | Model(s) | Run | " + " | ".join(FIELDS) + " | Critical | Privacy leaks | Dropped digit flagged |",
              "|---|---|---|" + "---|" * (len(FIELDS) + 3)]
    for m in METHOD_ORDER:
        r = runs.get(m)
        if r is None:
            lines.append(f"| {m} | — | not run{' (needs ANTHROPIC_API_KEY)' if m != 'rules' else ''} |" + " — |" * (len(FIELDS) + 3))
            continue
        acc = r["metrics"]["field_accuracy"]
        lines.append(f"| {m} | {', '.join(r['models']) or '—'} | {r['run_at'][:10]} | " + " | ".join(f"{acc[f]:.0%}" for f in FIELDS)
                     + f" | {r['metrics']['critical_accuracy']:.0%} | {r['metrics']['privacy_leaks']} | "
                     + ("yes" if r["metrics"]["dropped_digit_flagged"] else "no") + " |")
    lines += ["", "Critical accuracy by document group:", "", "| Method | " + " | ".join(any_run["metrics"]["by_group"]) + " |",
              "|---|" + "---|" * len(any_run["metrics"]["by_group"])]
    for m in METHOD_ORDER:
        if m in runs:
            g = runs[m]["metrics"]["by_group"]
            lines.append(f"| {m} | " + " | ".join(f"{v['critical_accuracy']:.0%} (n={v['n']})" for v in g.values()) + " |")
    for m in METHOD_ORDER:
        r = runs.get(m)
        if r and r["metrics"].get("llm"):
            u = r["metrics"]["llm"]
            lines.append(f"\n{m}: {u['calls']} model calls, {u['input_tokens']:,} input / {u['output_tokens']:,} output tokens, "
                         f"median latency {u['median_latency_ms']} ms; auto-accept precision (confidence ≥ "
                         f"{config.CTRACE_ESCALATE_BELOW}) {u['precision_at_threshold']:.0%} on {u['n_at_threshold']} bills.")
    lines += ["", "Read these numbers carefully:",
              "- **The eval set is synthetic and was built by the same team as the rules parser.** Its English layouts are "
              "ones the rules parser was written for, so the rules row is an upper bound for those layouts, not an estimate "
              "for real bills. Its value is the comparison across methods, Bangla, photos and the traps.",
              "- The rules parser cannot read photos at all; Bangla PDFs often store text in a visual order with private glyphs, "
              "which the parser can only partly repair.",
              "- Add consented, anonymised real bills to `data/eval/bills/` (with labels) before quoting accuracy to a customer.", ""]
    return lines


def write_version_card(version: str) -> Path:
    """models/ctrace-verifier/<version>/MODEL_CARD.md: that version's Verifier section only."""
    meta = json.loads((config.verifier_dir(version) / "metrics.json").read_text())
    out = config.verifier_dir(version) / "MODEL_CARD.md"
    out.write_text("\n".join([f"# {meta.get('model', 'C-Trace Verifier ' + version)} — Model Card", ""]
                             + verifier_section(meta)[2:]) + "\n", encoding="utf-8")
    return out


def write_model_card() -> Path:
    """models/MODEL_CARD.md: the pinned Verifier version plus the extraction eval."""
    meta = json.loads((config.verifier_dir() / "metrics.json").read_text())
    lines = HEADER + verifier_section(meta) + extraction_section()
    out = config.MODELS_DIR / "MODEL_CARD.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


if __name__ == "__main__":
    print(f"[C-Trace] wrote {write_model_card()}")
