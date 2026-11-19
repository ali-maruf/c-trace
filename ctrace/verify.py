"""C-Trace verification layer: is each reported month physically plausible?

Signals combined into one confidence grade per month:
  1. Expected-energy models (gradient boosting, trained on the C-Trace fleet): given what the
     factory produced, its type, weather and assets, how much gas / grid power / diesel should
     it have used? Residuals are calibrated to the factory's own history (leave-one-out median)
     so an efficient factory isn't flagged just for being efficient.
  2. Isolation Forest over the residual pattern (catches unusual *combinations*, e.g. gas
     down while production is up and electricity is normal).
  3. Document rules from extraction: implied tariff, missing fields, duplicate bills, gaps.
"""
from __future__ import annotations

from dataclasses import dataclass

import joblib
import numpy as np
import pandas as pd

from . import config

ACTIVITY_TARGETS = {"natural_gas": "gas_m3", "grid_electricity": "grid_kwh", "diesel": "diesel_l"}
FACTORY_TYPES = ["knit_composite", "woven", "denim_wash", "sweater"]
FEATURES = [
    "type_code", "log_workers", "has_wet_processing", "captive_gas_gen", "solar_kwp",
    "log_pieces", "month_sin", "month_cos", "cdd",
]


def make_features(df: pd.DataFrame) -> pd.DataFrame:
    X = pd.DataFrame(index=df.index)
    X["type_code"] = df["factory_type"].map({t: i for i, t in enumerate(FACTORY_TYPES)}).astype(float)
    X["log_workers"] = np.log(df["workers"].astype(float))
    X["has_wet_processing"] = df["has_wet_processing"].astype(float)
    X["captive_gas_gen"] = df["captive_gas_gen"].astype(float)
    X["solar_kwp"] = df["solar_kwp"].astype(float)
    X["log_pieces"] = np.log(df["pieces"].astype(float).clip(lower=1))
    m = df["month"].astype(float)
    X["month_sin"] = np.sin(2 * np.pi * m / 12)
    X["month_cos"] = np.cos(2 * np.pi * m / 12)
    X["cdd"] = df["cdd"].astype(float)
    return X[FEATURES]


@dataclass
class Verifier:
    models: dict          # target -> fitted regressor (predicts log1p(quantity))
    sigma: dict           # target -> robust std of calibrated residuals
    iso: object           # IsolationForest on residual pattern
    iso_threshold: float
    meta: dict

    @classmethod
    def load(cls, path=None) -> "Verifier":
        return joblib.load(path or (config.MODELS_DIR / "ctrace_verifier.joblib"))

    def save(self, path=None):
        joblib.dump(self, path or (config.MODELS_DIR / "ctrace_verifier.joblib"))

    # ---------------------------------------------------------------
    def residuals(self, df: pd.DataFrame) -> pd.DataFrame:
        X = make_features(df)
        out = pd.DataFrame(index=df.index)
        for tgt, model in self.models.items():
            pred = model.predict(X)
            out[f"pred_{tgt}"] = pred
            out[f"resid_{tgt}"] = np.log1p(df[tgt].astype(float)) - pred
        return out

    @staticmethod
    def loo_median(s: pd.Series, segments: pd.Series | None = None, min_others: int = 3) -> pd.Series:
        """Leave-one-out median per element (factory self-calibration).

        If `segments` is given (operating regimes split at declared interventions such as a boiler
        retrofit or new solar), each month is calibrated against other months of the same regime,
        falling back to all months when its regime is too short.
        """
        vals = s.to_numpy(dtype=float)
        seg = segments.to_numpy() if segments is not None else np.zeros(len(vals))
        res = np.zeros_like(vals, dtype=float)
        for i in range(len(vals)):
            mask = np.ones(len(vals), bool)
            mask[i] = False
            same = vals[mask & (seg == seg[i])]
            same = same[~np.isnan(same)]
            if len(same) >= 2:
                res[i] = np.median(same)
                continue
            others = vals[mask]
            others = others[~np.isnan(others)]
            res[i] = np.median(others) if len(others) >= min_others else 0.0
        return pd.Series(res, index=s.index)

    def score_factory(self, df: pd.DataFrame, breakpoints: list[str] | None = None) -> pd.DataFrame:
        """df: one factory's monthly table with profile columns + gas_m3, grid_kwh, diesel_l.
        breakpoints: periods (YYYY-MM) where a declared intervention starts a new operating regime."""
        df = df.sort_values("period").copy()
        segments = df["period"].map(lambda p: sum(p >= b for b in (breakpoints or [])))
        r = self.residuals(df)
        out = df[["period"]].copy()
        out["regime"] = segments.values
        zcols = []
        for tgt in self.models:
            calib = self.loo_median(r[f"resid_{tgt}"], segments)
            z = (r[f"resid_{tgt}"] - calib) / self.sigma[tgt]
            out[f"expected_{tgt}"] = np.expm1(r[f"pred_{tgt}"] + calib).round(1)
            out[f"reported_{tgt}"] = df[tgt].values
            out[f"z_{tgt}"] = z.round(2)
            zcols.append(f"z_{tgt}")
        iso_X = out[zcols].fillna(0).to_numpy()
        out["iso_score"] = -self.iso.score_samples(iso_X)   # higher = more unusual
        out["iso_flag"] = out["iso_score"] > self.iso_threshold
        return out


def grade_months(scored: pd.DataFrame, doc_issues: dict[str, list[str]] | None = None) -> pd.DataFrame:
    """Turn signals into High / Medium / Low confidence with human-readable reasons."""
    doc_issues = doc_issues or {}
    labels = {"gas_m3": "Gas", "grid_kwh": "Grid electricity", "diesel_l": "Diesel"}
    units = {"gas_m3": "m³", "grid_kwh": "kWh", "diesel_l": "L"}
    grades, reasons = [], []
    for _, row in scored.iterrows():
        why, level = [], 0
        for tgt, lab in labels.items():
            z = row.get(f"z_{tgt}")
            if z is None or pd.isna(z):
                continue
            exp, rep = row[f"expected_{tgt}"], row[f"reported_{tgt}"]
            warn, flag = config.Z_THRESHOLDS[tgt]
            if abs(z) >= flag:
                level = max(level, 2)
                why.append(f"{lab}: reported {rep:,.0f} {units[tgt]} vs expected ≈{exp:,.0f} (z={z:+.1f})")
            elif abs(z) >= warn:
                level = max(level, 1)
                why.append(f"{lab}: somewhat unusual, reported {rep:,.0f} vs expected ≈{exp:,.0f} (z={z:+.1f})")
        if row.get("iso_flag"):
            level = max(level, 1)
            why.append("Unusual combination of gas / power / diesel for this production level (Isolation Forest)")
        for issue in doc_issues.get(row["period"], []):
            severe = any(k in issue.lower() for k in ("tariff", "duplicate", "missing"))
            level = max(level, 2 if severe else 1)
            why.append(issue)
        grades.append(["High", "Medium", "Low"][level])
        reasons.append(why or ["All signals consistent with production, weather and assets"])
    out = scored.copy()
    out["confidence"] = grades
    out["reasons"] = reasons
    return out
