"""Train and evaluate the C-Trace verification models.

    python ml/train.py                 # simulate fleet + train + evaluate + write model card
    python ml/train.py --data my.csv   # train on real pilot data with the same columns

Evaluation is honest by design:
  * split by FACTORY (models are tested on factories they have never seen)
  * anomalies are injected into the held-out factories only, with known labels
  * C-Trace is compared against a naive month-over-month rule that a spreadsheet would use
"""
from __future__ import annotations

import argparse
import json
from datetime import date

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor, IsolationForest
from sklearn.metrics import mean_absolute_percentage_error, precision_recall_fscore_support, roc_auc_score
from sklearn.model_selection import GroupShuffleSplit

from ctrace import config
from ctrace.simulator import simulate_fleet
from ctrace.verify import ACTIVITY_TARGETS, FEATURES, Verifier, make_features

TARGETS = list(ACTIVITY_TARGETS.values())
ANOMALY_TYPES = {
    "under_reporting (×0.4–0.7)": lambda v, r: v * r.uniform(0.4, 0.7),
    "unit_error (×1000 or ÷1000)": lambda v, r: v * (1000 if r.random() < 0.5 else 0.001),
    "double_count (×2)": lambda v, r: v * 2.0,
    "over_reporting (×1.4–1.8)": lambda v, r: v * r.uniform(1.4, 1.8),
}


def robust_std(x: np.ndarray) -> float:
    x = x[~np.isnan(x)]
    return float(1.4826 * np.median(np.abs(x - np.median(x))))


def calibrated_residuals(verifier: Verifier, df: pd.DataFrame) -> pd.DataFrame:
    r = verifier.residuals(df)
    out = pd.DataFrame(index=df.index)
    for tgt in TARGETS:
        calib = r[f"resid_{tgt}"].groupby(df["factory_id"]).transform(Verifier.loo_median)
        out[tgt] = r[f"resid_{tgt}"] - calib
    return out


def inject(df: pd.DataFrame, rate: float, seed: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    df = df.copy()
    labels = pd.DataFrame(0, index=df.index, columns=TARGETS)
    kinds = pd.DataFrame("", index=df.index, columns=TARGETS)
    names = list(ANOMALY_TYPES)
    for tgt in TARGETS:
        mask = rng.random(len(df)) < rate
        mask &= df[tgt].to_numpy() > 0
        for i in np.where(mask)[0]:
            k = names[rng.integers(len(names))]
            df.iloc[i, df.columns.get_loc(tgt)] = ANOMALY_TYPES[k](df.iloc[i][tgt], rng)
            labels.iloc[i, labels.columns.get_loc(tgt)] = 1
            kinds.iloc[i, kinds.columns.get_loc(tgt)] = k
    return df, labels.join(kinds.add_suffix("_kind"))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", help="CSV with fleet data (default: simulate)")
    ap.add_argument("--factories", type=int, default=600)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args(argv)

    if args.data:
        fleet, data_source = pd.read_csv(args.data), f"user data: {args.data}"
    else:
        fleet = simulate_fleet(args.factories, seed=args.seed)
        data_source = f"C-Trace simulator v1 ({args.factories} factories × 36 months, seed {args.seed})"
        fleet.to_csv(config.DATA_DIR / "training_fleet.csv", index=False)
    print(f"[C-Trace] training rows: {len(fleet):,} from {data_source}")

    gss = GroupShuffleSplit(n_splits=1, test_size=0.25, random_state=args.seed)
    tr_idx, te_idx = next(gss.split(fleet, groups=fleet["factory_id"]))
    train, test = fleet.iloc[tr_idx].reset_index(drop=True), fleet.iloc[te_idx].reset_index(drop=True)
    gss2 = GroupShuffleSplit(n_splits=1, test_size=0.2, random_state=args.seed + 1)
    fit_idx, cal_idx = next(gss2.split(train, groups=train["factory_id"]))
    fit, cal = train.iloc[fit_idx].reset_index(drop=True), train.iloc[cal_idx].reset_index(drop=True)

    # ---- 1. expected-energy models
    models, metrics = {}, {"regression": {}, "detection": {}}
    X_fit = make_features(fit)
    for tgt in TARGETS:
        m = HistGradientBoostingRegressor(
            max_iter=600, learning_rate=0.05, max_leaf_nodes=31, min_samples_leaf=30,
            l2_regularization=1.0, categorical_features=[FEATURES.index("type_code")],
            early_stopping=True, validation_fraction=0.15, random_state=args.seed,
        )
        m.fit(X_fit, np.log1p(fit[tgt]))
        models[tgt] = m

    v = Verifier(models=models, sigma={}, iso=None, iso_threshold=0.0, meta={})

    # ---- 2. calibrate residual spread on unseen calibration factories
    cal_res = calibrated_residuals(v, cal)
    v.sigma = {t: robust_std(cal_res[t].to_numpy()) for t in TARGETS}

    # ---- 3. Isolation Forest on clean calibration z-patterns
    z_cal = np.column_stack([cal_res[t] / v.sigma[t] for t in TARGETS])
    iso = IsolationForest(n_estimators=300, contamination="auto", random_state=args.seed).fit(z_cal)
    v.iso = iso
    v.iso_threshold = float(np.quantile(-iso.score_samples(z_cal), 0.99))

    # ---- 4. regression quality on held-out factories (clean)
    X_te = make_features(test)
    for tgt in TARGETS:
        pred = np.expm1(models[tgt].predict(X_te))
        y = test[tgt].to_numpy()
        ok = y > max(1.0, np.percentile(y, 5))
        r = np.log1p(y) - models[tgt].predict(X_te)
        calib = pd.Series(r).groupby(test["factory_id"]).transform(Verifier.loo_median).to_numpy()
        pred_cal = np.expm1(models[tgt].predict(X_te) + calib)
        metrics["regression"][tgt] = {
            "MAPE_fleet_model_new_factory": round(float(mean_absolute_percentage_error(y[ok], pred[ok])), 4),
            "MAPE_after_factory_calibration": round(float(mean_absolute_percentage_error(y[ok], pred_cal[ok])), 4),
            "sigma_log_residual": round(v.sigma[tgt], 4),
        }

    # ---- 5. anomaly detection on held-out factories with injected, labelled errors
    test_bad, lab = inject(test, rate=0.05, seed=args.seed + 7)
    bad_res = calibrated_residuals(v, test_bad)
    prev = test_bad.sort_values(["factory_id", "period"]).groupby("factory_id")[TARGETS].shift(1)
    for tgt in TARGETS:
        z = (bad_res[tgt] / v.sigma[tgt]).abs().to_numpy()
        y = lab[tgt].to_numpy()
        pred_flag = z >= config.Z_FLAG
        p, r, f1, _ = precision_recall_fscore_support(y, pred_flag, average="binary", zero_division=0)
        mom = np.abs(np.log((test_bad[tgt] + 1) / (prev[tgt] + 1))).fillna(0).to_numpy()
        pn, rn, fn, _ = precision_recall_fscore_support(y, mom > np.log(1.5), average="binary", zero_division=0)
        by_kind = {}
        for k in ANOMALY_TYPES:
            sel = lab[f"{tgt}_kind"].to_numpy() == k
            if sel.sum():
                by_kind[k] = round(float(pred_flag[sel].mean()), 3)
        metrics["detection"][tgt] = {
            "ROC_AUC": round(float(roc_auc_score(y, z)), 4),
            "precision@z3": round(float(p), 4), "recall@z3": round(float(r), 4), "F1@z3": round(float(f1), 4),
            "naive_MoM_rule_precision": round(float(pn), 4), "naive_MoM_rule_recall": round(float(rn), 4),
            "naive_MoM_rule_F1": round(float(fn), 4),
            "recall_by_anomaly_type": by_kind,
            "n_injected": int(y.sum()), "n_rows": int(len(y)),
        }

    v.meta = {
        "brand": "C-Trace", "model": "C-Trace Verifier v1", "trained_on": data_source,
        "trained_date": date.today().isoformat(), "features": FEATURES, "targets": TARGETS,
        "algorithm": "HistGradientBoostingRegressor (log1p target) + factory LOO-median calibration + IsolationForest",
        "metrics": metrics, "z_warn": config.Z_WARN, "z_flag": config.Z_FLAG,
        "n_train_factories": int(fit.factory_id.nunique()), "n_cal_factories": int(cal.factory_id.nunique()),
        "n_test_factories": int(test.factory_id.nunique()),
    }
    config.MODELS_DIR.mkdir(exist_ok=True)
    v.save()
    (config.MODELS_DIR / "metrics.json").write_text(json.dumps(v.meta, indent=2))
    write_model_card(v.meta)
    print(json.dumps(metrics, indent=2))


def write_model_card(meta: dict):
    reg, det = meta["metrics"]["regression"], meta["metrics"]["detection"]
    names = {"gas_m3": "Natural gas", "grid_kwh": "Grid electricity", "diesel_l": "Diesel"}
    lines = [
        "# C-Trace Verifier v1 — Model Card", "",
        f"*Trained {meta['trained_date']} on {meta['trained_on']}.*", "",
        "## What it does",
        "Predicts how much natural gas, grid electricity and diesel a garment factory *should* have used in a month, "
        "given its production, type, assets and weather. C-Trace compares that to what the bills say and flags months "
        "that don't add up. It does **not** calculate emissions — that is deterministic code with IPCC/Ember factors.", "",
        "## Algorithm", meta["algorithm"] + ".", "",
        f"Features: `{', '.join(meta['features'])}`", "",
        f"Factories — train: {meta['n_train_factories']}, calibration: {meta['n_cal_factories']}, "
        f"held-out test: {meta['n_test_factories']} (split by factory, never seen in training).", "",
        "## Prediction quality (held-out factories)", "",
        "| Energy carrier | MAPE, new factory | MAPE after self-calibration | σ (log residual) |", "|---|---|---|---|",
    ]
    for t, m in reg.items():
        lines.append(f"| {names[t]} | {m['MAPE_fleet_model_new_factory']:.1%} | {m['MAPE_after_factory_calibration']:.1%} | {m['sigma_log_residual']:.3f} |")
    lines += ["", "## Error detection (5% injected errors, held-out factories)", "",
              "| Energy carrier | ROC-AUC | Precision | Recall | F1 | Naive month-over-month rule F1 |", "|---|---|---|---|---|---|"]
    for t, m in det.items():
        lines.append(f"| {names[t]} | {m['ROC_AUC']:.3f} | {m['precision@z3']:.1%} | {m['recall@z3']:.1%} | {m['F1@z3']:.1%} | {m['naive_MoM_rule_F1']:.1%} |")
    lines += ["", "Grading used in the app: gas and grid electricity → Medium at |z| ≥ 2.5, Low at |z| ≥ 3; "
              "diesel → Medium at |z| ≥ 3, Low at |z| ≥ 5 (diesel depends on unobserved grid outages). "
              "Document checks (implied tariff, missing or duplicate bills) can also set a month to Low.",
              "", "Recall by error type:", ""]
    for t, m in det.items():
        kinds = ", ".join(f"{k}: {v:.0%}" for k, v in m["recall_by_anomaly_type"].items())
        lines.append(f"- **{names[t]}** — {kinds}")
    lines += [
        "", "## Limitations — read before quoting these numbers",
        "- **Training data is simulated.** Bangladesh has no open factory-level monthly energy dataset. The C-Trace "
        "simulator (`packages/ctrace/ctrace/simulator.py`) encodes transparent engineering assumptions (kWh/piece, steam per piece, "
        "captive gas generation, outages, solar yield, Eid shutdowns). Metrics show the method works on data with this "
        "structure; they are **not** proof of real-world accuracy. Retrain with pilot data: `uv run python ml/train.py --data pilot.csv`.",
        "- Small under-reporting (<30%) is hard to separate from genuine efficiency gains; C-Trace grades such months "
        "Medium and asks for evidence rather than accusing.",
        "- Diesel use depends on grid outages, which are noisy, so diesel flags are weaker.",
        "", "## Responsible use",
        "- A flag means *please check this document*, never *this factory is cheating*. A human reviews every Low grade.",
        "- No personal data is used. Factory data stays private to the factory unless it chooses to share a report.",
        "- Every reported tonne is traceable to a source document hash and an emission factor with a citation.",
    ]
    (config.MODELS_DIR / "MODEL_CARD.md").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
