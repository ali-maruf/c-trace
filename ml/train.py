"""Train and evaluate the C-Trace Verifier, write it to the model registry, regenerate the model card.

    uv run python ml/train.py                          # v2: simulated fleet with real NASA POWER weather
    uv run python ml/train.py --version v1             # reproduce v1 (Dhaka climatology)
    uv run python ml/train.py --data pilot.csv         # train on real pilot data (validated with pandera)

Evaluation is honest by design:
  * split by FACTORY (models are tested on factories they have never seen)
  * anomalies are injected into the held-out factories only, with known labels
  * C-Trace is compared against a naive month-over-month rule that a spreadsheet would use, and v2 against v1
    on the same held-out factories
  * fairness: false-flag rate on clean held-out data by factory size and type
Output: models/ctrace-verifier/<version>/{model.joblib, metrics.json, MODEL_CARD.md}
"""
from __future__ import annotations

import argparse
import json
from datetime import date

import numpy as np
import pandas as pd
import pandera.pandas as pa
from model_card import write_model_card, write_version_card  # ml/ is on sys.path when run as a script
from sklearn.ensemble import HistGradientBoostingRegressor, IsolationForest
from sklearn.metrics import mean_absolute_percentage_error, precision_recall_fscore_support, roc_auc_score
from sklearn.model_selection import GroupKFold, GroupShuffleSplit

from ctrace import config
from ctrace.simulator import FACTORY_TYPES, FLEET_LOCATIONS, simulate_fleet
from ctrace.verify import ACTIVITY_TARGETS, FEATURES, Verifier, make_features

TARGETS = list(ACTIVITY_TARGETS.values())
ANOMALY_TYPES = {
    "under_reporting (×0.4–0.7)": lambda v, r: v * r.uniform(0.4, 0.7),
    "unit_error (×1000 or ÷1000)": lambda v, r: v * (1000 if r.random() < 0.5 else 0.001),
    "double_count (×2)": lambda v, r: v * 2.0,
    "over_reporting (×1.4–1.8)": lambda v, r: v * r.uniform(1.4, 1.8),
}
VERSIONS = {
    "v1": {"real_weather": False, "start": "2023-01", "end": "2025-12", "conformal": False, "fairness_fix": False},
    "v2": {"real_weather": True, "start": "2022-01", "end": "2025-12", "conformal": True, "fairness_fix": True},
}
ALPHA = 0.10            # conformal: 90% expected band
# v2: target share of clean months the model grades Low, per carrier. Diesel is advisory by policy (config.Z_THRESHOLDS):
# grid outages that drive it are not observed, so only gross errors may make a diesel month Low.
FLAG_RATE = {"gas_m3": 0.005, "grid_kwh": 0.005, "diesel_l": 0.001}
FAIRNESS_MAX_RATIO = 1.5

PILOT_SCHEMA = pa.DataFrameSchema(
    {
        "factory_id": pa.Column(str),
        "period": pa.Column(str, pa.Check.str_matches(r"^\d{4}-(0[1-9]|1[0-2])$")),
        "factory_type": pa.Column(str, pa.Check.isin(list(FACTORY_TYPES))),
        "workers": pa.Column(int, pa.Check.gt(0), coerce=True),
        "has_wet_processing": pa.Column(int, pa.Check.isin([0, 1]), coerce=True),
        "captive_gas_gen": pa.Column(int, pa.Check.isin([0, 1]), coerce=True),
        "solar_kwp": pa.Column(float, pa.Check.ge(0), coerce=True),
        "pieces": pa.Column(float, pa.Check.gt(0), coerce=True),
        "cdd": pa.Column(float, pa.Check.ge(0), coerce=True),
        "gas_m3": pa.Column(float, pa.Check.ge(0), coerce=True),
        "grid_kwh": pa.Column(float, pa.Check.ge(0), coerce=True),
        "diesel_l": pa.Column(float, pa.Check.ge(0), coerce=True),
    },
    checks=[pa.Check(lambda df: ~df.duplicated(["factory_id", "period"]), error="one row per factory and month"),
            pa.Check(lambda df: df.groupby("factory_id").period.transform("count") >= 12,
                     error="each factory needs at least 12 months")],
    strict=False,
)


def load_pilot(path: str) -> pd.DataFrame:
    """Read and validate pilot data. Raises SystemExit with every problem listed, not just the first."""
    df = pd.read_csv(path, dtype={"factory_id": str, "period": str})
    try:
        df = PILOT_SCHEMA.validate(df, lazy=True)
    except pa.errors.SchemaErrors as exc:
        cases = exc.failure_cases[["column", "check", "failure_case"]].drop_duplicates().head(20)
        raise SystemExit(f"[C-Trace] {path} does not match the pilot schema:\n{cases.to_string(index=False)}") from exc
    df["month"] = df.period.str[5:7].astype(int)
    return df


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


def zscores(v: Verifier, df: pd.DataFrame, res: pd.DataFrame) -> pd.DataFrame:
    X = make_features(df)
    return pd.DataFrame({t: res[t].to_numpy() / v.sigma_rows(t, X) for t in TARGETS}, index=df.index)


def fit_scale_models(df: pd.DataFrame, res: pd.DataFrame, seed: int) -> dict:
    """Heteroscedastic residual scale: q90 of |calibrated residual| given the factory's features."""
    X = make_features(df)
    out = {}
    for t in TARGETS:
        ok = res[t].notna().to_numpy()
        m = HistGradientBoostingRegressor(loss="quantile", quantile=0.9, max_iter=300, learning_rate=0.05,
                                          max_leaf_nodes=15, min_samples_leaf=200, l2_regularization=1.0,
                                          categorical_features=[FEATURES.index("type_code")], random_state=seed)
        m.fit(X[ok], res[t].abs().to_numpy()[ok])
        out[t] = m
    return out


def new_regressor(seed: int) -> HistGradientBoostingRegressor:
    return HistGradientBoostingRegressor(
        max_iter=600, learning_rate=0.05, max_leaf_nodes=31, min_samples_leaf=30,
        l2_regularization=1.0, categorical_features=[FEATURES.index("type_code")],
        early_stopping=True, validation_fraction=0.15, random_state=seed,
    )


def oof_residuals(fit: pd.DataFrame, seed: int, folds: int = 4) -> pd.DataFrame:
    """Out-of-fold calibrated residuals for every training factory (cross-fitting), so the scale model learns
    residual spread from models that never saw those factories."""
    res = pd.DataFrame(index=fit.index, columns=TARGETS, dtype=float)
    for tr, ho in GroupKFold(n_splits=folds).split(fit, groups=fit["factory_id"]):
        a, b = fit.iloc[tr], fit.iloc[ho]
        models = {t: new_regressor(seed).fit(make_features(a), np.log1p(a[t])) for t in TARGETS}
        fold = Verifier(models=models, sigma={}, iso=None, iso_threshold=0.0, meta={})
        res.iloc[ho] = calibrated_residuals(fold, b.reset_index(drop=True)).to_numpy()
    return res


def _rank_quantile(a: np.ndarray, level: float) -> float:
    a = np.sort(a[~np.isnan(a)])
    return float(a[min(int(np.ceil((len(a) + 1) * level)), len(a)) - 1])


def flag_multipliers(v: Verifier, df: pd.DataFrame, res: pd.DataFrame) -> dict:
    """Mondrian (group-conditional) calibration: per carrier and factory type, k such that
    P(|r| / (shape(x)·k) ≥ Low threshold) = FLAG_RATE on held-out calibration factories of that type.
    A type with too few calibration months uses the pooled k."""
    X = make_features(df)
    v.scale_k = None
    out = {}
    for t in TARGETS:
        a = res[t].abs().to_numpy() / v.sigma_rows(t, X)
        thr, rate = config.Z_THRESHOLDS[t][1], FLAG_RATE[t]
        k = {"_all": _rank_quantile(a, 1 - rate) / thr}
        for ft in df.factory_type.unique():
            sel = (df.factory_type == ft).to_numpy()
            if sel.sum() >= 5 / rate:  # enough months for ≥5 expected exceedances in the group
                k[ft] = _rank_quantile(a[sel], 1 - rate) / thr
        out[t] = k
    return out


def conformal_multipliers(v: Verifier, df: pd.DataFrame, res: pd.DataFrame) -> dict:
    """Split-conformal: the (1-α) quantile (finite-sample rank) of |residual| / σ(x) on held-out calibration data."""
    X = make_features(df)
    q = {}
    for t in TARGETS:
        a = (res[t].abs().to_numpy() / v.sigma_rows(t, X))
        a = np.sort(a[~np.isnan(a)])
        k = min(int(np.ceil((len(a) + 1) * (1 - ALPHA))), len(a)) - 1
        q[t] = float(a[k])
    return q


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


def detection_metrics(v: Verifier, test_bad: pd.DataFrame, lab: pd.DataFrame) -> dict:
    z_all = zscores(v, test_bad, calibrated_residuals(v, test_bad)).abs()
    prev = test_bad.sort_values(["factory_id", "period"]).groupby("factory_id")[TARGETS].shift(1)
    out = {}
    for tgt in TARGETS:
        z, y = z_all[tgt].to_numpy(), lab[tgt].to_numpy()
        p3, r3, f3, _ = precision_recall_fscore_support(y, z >= config.Z_FLAG, average="binary", zero_division=0)
        pred_flag = z >= config.Z_THRESHOLDS[tgt][1]   # the Low threshold the app actually uses
        p, r, f1, _ = precision_recall_fscore_support(y, pred_flag, average="binary", zero_division=0)
        mom = np.abs(np.log((test_bad[tgt] + 1) / (prev[tgt] + 1))).fillna(0).to_numpy()
        pn, rn, fn, _ = precision_recall_fscore_support(y, mom > np.log(1.5), average="binary", zero_division=0)
        by_kind = {k: round(float(pred_flag[lab[f"{tgt}_kind"].to_numpy() == k].mean()), 3)
                   for k in ANOMALY_TYPES if (lab[f"{tgt}_kind"].to_numpy() == k).sum()}
        out[tgt] = {
            "ROC_AUC": round(float(roc_auc_score(y, z)), 4),
            "precision@low": round(float(p), 4), "recall@low": round(float(r), 4), "F1@low": round(float(f1), 4),
            "low_threshold": config.Z_THRESHOLDS[tgt][1],
            "precision@z3": round(float(p3), 4), "recall@z3": round(float(r3), 4), "F1@z3": round(float(f3), 4),
            "naive_MoM_rule_precision": round(float(pn), 4), "naive_MoM_rule_recall": round(float(rn), 4),
            "naive_MoM_rule_F1": round(float(fn), 4), "recall_by_anomaly_type": by_kind,
            "n_injected": int(y.sum()), "n_rows": int(len(y)),
        }
    return out


def false_flags(v: Verifier, clean: pd.DataFrame, edges: list[float]) -> dict:
    """Share of clean months the model alone would grade Low, by worker tercile and by factory type."""
    z = zscores(v, clean, calibrated_residuals(v, clean)).abs()
    low = np.zeros(len(clean), bool)
    for tgt in TARGETS:
        low |= (z[tgt] >= config.Z_THRESHOLDS[tgt][1]).to_numpy()
    size = np.select([clean.workers <= edges[0], clean.workers <= edges[1]], ["small", "medium"], "large")
    frame = pd.DataFrame({"low": low, "size": size, "type": clean.factory_type.to_numpy()})
    by_size = frame.groupby("size").low.agg(["mean", "size"])
    by_type = frame.groupby("type").low.agg(["mean", "size"])
    rates = {k: round(float(by_size.loc[k, "mean"]), 4) for k in ("small", "medium", "large")}
    return {
        "overall": round(float(low.mean()), 4),
        "by_size": {k: {"rate": rates[k], "n_months": int(by_size.loc[k, "size"])} for k in ("small", "medium", "large")},
        "by_type": {k: {"rate": round(float(r["mean"]), 4), "n_months": int(r["size"])} for k, r in by_type.iterrows()},
        "small_to_large_ratio": round(rates["small"] / rates["large"], 3) if rates["large"] > 0 else None,
        "worker_tercile_edges": [round(float(e)) for e in edges],
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default=config.CTRACE_VERIFIER_VERSION, choices=sorted(VERSIONS))
    ap.add_argument("--data", help="pilot CSV (validated); default: simulate")
    ap.add_argument("--factories", type=int, default=600)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args(argv)
    spec = VERSIONS[args.version]

    if args.data:
        fleet, data_source = load_pilot(args.data), f"pilot data: {args.data}"
    else:
        fleet = simulate_fleet(args.factories, spec["start"], spec["end"], seed=args.seed, real_weather=spec["real_weather"])
        months = fleet.period.nunique()
        weather = ("NASA POWER monthly T2M at " + ", ".join(f"{n} ({lat}, {lon})" for n, lat, lon, _ in FLEET_LOCATIONS)
                   if spec["real_weather"] else "Dhaka monthly climatology")
        data_source = (f"C-Trace simulator ({args.factories} factories × {months} months, {spec['start']}…{spec['end']}, "
                       f"seed {args.seed}; weather: {weather})")
        fleet.to_csv(config.DATA_DIR / f"training_fleet_{args.version}.csv", index=False)
    print(f"[C-Trace] {args.version}: {len(fleet):,} rows from {data_source}")

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
        models[tgt] = new_regressor(args.seed).fit(X_fit, np.log1p(fit[tgt]))
    v = Verifier(models=models, sigma={}, iso=None, iso_threshold=0.0, meta={}, version=args.version)

    # ---- 2. calibrate residual spread (and the conformal band) on unseen calibration factories
    cal_res = calibrated_residuals(v, cal)
    v.sigma = {t: robust_std(cal_res[t].to_numpy()) for t in TARGETS}


    # ---- 3. Isolation Forest on clean calibration z-patterns
    z_cal = np.column_stack([cal_res[t] / v.sigma[t] for t in TARGETS])
    iso = IsolationForest(n_estimators=300, contamination="auto", random_state=args.seed).fit(z_cal)
    v.iso = iso
    v.iso_threshold = float(np.quantile(-iso.score_samples(z_cal), 0.99))

    # ---- 4. fairness on clean held-out factories; fix calibration if small factories are over-flagged
    edges = list(np.quantile(fit.groupby("factory_id").workers.first(), [1 / 3, 2 / 3]))
    fair = {"before_fix": false_flags(v, test, edges), "fix_applied": False}
    if spec["fairness_fix"]:
        # v2 calibration, always applied: scale shape from out-of-fold residuals of the training factories,
        # clean-month Low rate set on the calibration factories (which neither the models nor the shape saw)
        v.scale_models = fit_scale_models(fit, oof_residuals(fit, args.seed), args.seed)
        v.scale_k = flag_multipliers(v, cal, cal_res)
        fair.update(fix_applied=True,
                    method=("heteroscedastic, rate-controlled scale: σ(x) = shape(x) × k, shape = quantile-GBM (q = 0.9) of "
                            "|residual| from 4-fold out-of-fold residuals of the training factories, k set on calibration "
                            "factories per factory type (Mondrian) so that a set share of clean months reach the Low "
                            "threshold: " + ", ".join(f"{t} {r:.1%}" for t, r in FLAG_RATE.items())),
                    after_fix=false_flags(v, test, edges), target_flag_rate_per_carrier=FLAG_RATE)
    if spec["conformal"]:
        v.conformal_q = conformal_multipliers(v, cal, cal_res)
    fair["threshold_ratio"] = FAIRNESS_MAX_RATIO
    final = fair.get("after_fix", fair["before_fix"])
    fair["passes"] = final["small_to_large_ratio"] is None or final["small_to_large_ratio"] <= FAIRNESS_MAX_RATIO
    metrics["fairness"] = fair

    # ---- 5. regression quality and band coverage on held-out factories (clean)
    X_te = make_features(test)
    test_res = calibrated_residuals(v, test)
    for tgt in TARGETS:
        pred = np.expm1(models[tgt].predict(X_te))
        y = test[tgt].to_numpy()
        ok = y > max(1.0, np.percentile(y, 5))
        pred_cal = np.expm1(np.log1p(y) - test_res[tgt].to_numpy())
        metrics["regression"][tgt] = {
            "MAPE_fleet_model_new_factory": round(float(mean_absolute_percentage_error(y[ok], pred[ok])), 4),
            "MAPE_after_factory_calibration": round(float(mean_absolute_percentage_error(y[ok], pred_cal[ok])), 4),
            "sigma_log_residual": round(v.sigma[tgt], 4),
        }
        if v.conformal_q:
            half = v.conformal_q[tgt] * v.sigma_rows(tgt, X_te) if v.scale_models else np.full(len(X_te), v.conformal_q[tgt])
            metrics["regression"][tgt]["band90_coverage"] = round(float((test_res[tgt].abs().to_numpy() <= half).mean()), 4)
            metrics["regression"][tgt]["band90_halfwidth_pct"] = round(float(np.expm1(np.median(half)) * 100), 1)

    # ---- 6. anomaly detection on held-out factories with injected, labelled errors (+ comparison with v1)
    test_bad, lab = inject(test, rate=0.05, seed=args.seed + 7)
    metrics["detection"] = detection_metrics(v, test_bad, lab)
    prev_path = config.verifier_path("v1")
    if args.version != "v1" and prev_path.exists():
        v1 = Verifier.load(prev_path)
        metrics["comparison_v1_same_test"] = {t: {k: d[k] for k in ("ROC_AUC", "F1@low", "recall@low", "precision@low")}
                                              for t, d in detection_metrics(v1, test_bad, lab).items()}

    v.meta = {
        "brand": "C-Trace", "model": f"C-Trace Verifier {args.version}", "version": args.version, "trained_on": data_source,
        "trained_date": date.today().isoformat(), "features": FEATURES, "targets": TARGETS,
        "algorithm": ("HistGradientBoostingRegressor (log1p target) + factory LOO-median calibration + IsolationForest"
                      + (" + heteroscedastic scale σ(x) (quantile GBM) + split-conformal 90% band + group-Shapley drivers" if spec["conformal"] else "")),
        "metrics": metrics, "z_warn": config.Z_WARN, "z_flag": config.Z_FLAG, "z_thresholds": config.Z_THRESHOLDS,
        "n_train_factories": int(fit.factory_id.nunique()), "n_cal_factories": int(cal.factory_id.nunique()),
        "n_test_factories": int(test.factory_id.nunique()),
        "drift_baseline_sigma": {t: round(v.sigma[t], 5) for t in TARGETS},
        # robust spread of z on clean calibration months: what a healthy fleet looks like to the drift check
        "drift_baseline_z": {t: round(robust_std(zscores(v, cal, cal_res)[t].to_numpy()), 4) for t in TARGETS},
    }
    out = config.verifier_dir(args.version)
    out.mkdir(parents=True, exist_ok=True)
    v.save(out / "model.joblib")
    (out / "metrics.json").write_text(json.dumps(v.meta, indent=2, ensure_ascii=False) + "\n")
    write_version_card(args.version)
    if args.version == config.CTRACE_VERIFIER_VERSION:
        write_model_card()
    print(json.dumps({k: metrics[k] for k in ("fairness", "regression")}, indent=1)[:3000])


if __name__ == "__main__":
    main()
