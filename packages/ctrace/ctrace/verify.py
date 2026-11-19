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

import math
from dataclasses import dataclass
from typing import Any

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


Z90 = 1.6448536  # standard normal 95th percentile: q90 of |N(0, σ)| = 1.645 σ
DRIVER_GROUPS = {
    "production": ["log_pieces"],
    "weather": ["cdd"],
    "solar": ["solar_kwp"],
    "season": ["month_sin", "month_cos"],
}


@dataclass
class Verifier:
    models: dict          # target -> fitted regressor (predicts log1p(quantity))
    sigma: dict           # target -> robust std of calibrated residuals
    iso: Any              # sklearn IsolationForest on the residual pattern
    iso_threshold: float
    meta: dict
    # v2 additions (absent on v1 pickles; read through getattr)
    version: str = "v1"
    conformal_q: dict | None = None        # target -> 90% split-conformal multiplier (of σ(x) when scale_models set)
    scale_models: dict | None = None       # target -> quantile-GBM of |calibrated residual| → shape of σ(x)
    scale_k: dict | None = None            # target -> {factory_type: k}: sets the clean-month Low rate per type (Mondrian)

    @classmethod
    def load(cls, path=None) -> Verifier:
        v = joblib.load(path or config.verifier_path())
        if not hasattr(v, "version"):
            v.version = v.meta.get("version", "v1")
        return v

    def save(self, path) -> None:
        joblib.dump(self, path)

    # ---------------------------------------------------------------
    def sigma_rows(self, tgt: str, X: pd.DataFrame) -> np.ndarray:
        """Residual scale per row. v1: one σ per carrier. v2: σ(x) = shape(x) × k — the shape is the q90 of |residual|
        for factories like this one (÷ 1.645, kept within [0.5σ, 3σ]); k is calibrated so that clean months reach the
        Low threshold at the target rate on held-out calibration factories."""
        sm = getattr(self, "scale_models", None)
        base = float(self.sigma[tgt])
        if not sm:
            return np.full(len(X), base)
        shape = np.clip(sm[tgt].predict(X) / Z90, 0.5 * base, 3.0 * base)
        k = (getattr(self, "scale_k", None) or {}).get(tgt)
        if k is None:
            return shape
        type_code = X["type_code"].to_numpy()
        return shape * np.array([k.get(FACTORY_TYPES[int(c)], k["_all"]) for c in type_code])

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

    def score_factory(self, df: pd.DataFrame, breakpoints: list[str] | dict[str, list[str]] | None = None,
                      explain: bool = False) -> pd.DataFrame:
        """df: one factory's monthly table with profile columns + gas_m3, grid_kwh, diesel_l.
        breakpoints: periods (YYYY-MM) where a declared intervention starts a new operating regime — one list for all
        carriers, or per carrier ({"gas_m3": [...], ...}) so a boiler retrofit only re-baselines gas.
        explain: add per-month driver contributions (`drivers_<target>`), see `explain`."""
        df = df.sort_values("period").copy()

        def seg(bps: list[str]) -> pd.Series:
            return df["period"].map(lambda p: sum(p >= b for b in bps))

        by_target = breakpoints if isinstance(breakpoints, dict) else {t: list(breakpoints or []) for t in self.models}
        segments_of = {t: seg(by_target.get(t, [])) for t in self.models}
        r = self.residuals(df)
        out = df[["period"]].copy()
        out["regime"] = seg(sorted({b for v in by_target.values() for b in v})).values
        X = make_features(df)
        q = getattr(self, "conformal_q", None) or {}
        scaled = bool(getattr(self, "scale_models", None))
        zcols, calibs = [], {}
        for tgt in self.models:
            calib = self.loo_median(r[f"resid_{tgt}"], segments_of[tgt])
            calibs[tgt] = calib
            centre = r[f"pred_{tgt}"] + calib
            sig = self.sigma_rows(tgt, X)
            z = (r[f"resid_{tgt}"] - calib) / sig
            out[f"expected_{tgt}"] = np.expm1(centre).round(1)
            if tgt in q:
                half = q[tgt] * sig if scaled else q[tgt]
                out[f"expected_lo_{tgt}"] = np.expm1(centre - half).clip(lower=0).round(1)
                out[f"expected_hi_{tgt}"] = np.expm1(centre + half).round(1)
            out[f"reported_{tgt}"] = df[tgt].values
            out[f"z_{tgt}"] = z.round(2)
            zcols.append(f"z_{tgt}")
        iso_X = out[zcols].fillna(0).to_numpy()
        out["iso_score"] = -self.iso.score_samples(iso_X)   # higher = more unusual
        out["iso_flag"] = out["iso_score"] > self.iso_threshold
        if explain:
            for tgt, drivers in self.explain(df, calibs).items():
                out[f"drivers_{tgt}"] = drivers
        return out

    def explain(self, df: pd.DataFrame, calibs: dict[str, pd.Series]) -> dict[str, list[dict]]:
        """Exact Shapley values over DRIVER_GROUPS for each month's expected quantity, in the carrier's unit.

        Reference point: the factory's own typical month (mean of each driver feature over the months scored),
        so contributions answer "why is this month's expected use different from usual?". Features outside a
        coalition take their reference value; the factory profile (type, size, wet processing, captive generation)
        is the factory's own and stays fixed. Efficiency holds exactly: typical + Σ contributions = expected.
        """
        X = make_features(df).reset_index(drop=True)
        groups = list(DRIVER_GROUPS)
        n_g = len(groups)
        ref = X[[c for g in groups for c in DRIVER_GROUPS[g]]].mean()
        masks = [tuple(bool(m >> i & 1) for i in range(n_g)) for m in range(2 ** n_g)]
        variants = []
        for mask in masks:
            Xm = X.copy()
            for g, on in zip(groups, mask, strict=True):
                if not on:
                    for c in DRIVER_GROUPS[g]:
                        Xm[c] = ref[c]
            variants.append(Xm)
        big = pd.concat(variants, ignore_index=True)
        n = len(X)
        weight = {k: math.factorial(k) * math.factorial(n_g - k - 1) / math.factorial(n_g) for k in range(n_g)}
        out: dict[str, list[dict]] = {}
        for tgt, model in self.models.items():
            calib = calibs[tgt].to_numpy(float)
            val = np.expm1(model.predict(big).reshape(len(masks), n) + calib)  # (coalition, month)
            index = {m: i for i, m in enumerate(masks)}
            rows = []
            for j in range(n):
                contrib = {}
                for gi, g in enumerate(groups):
                    phi = 0.0
                    for m in masks:
                        if m[gi]:
                            continue
                        with_g = tuple(True if k == gi else v for k, v in enumerate(m))
                        phi += weight[sum(m)] * (val[index[with_g], j] - val[index[m], j])
                    contrib[g] = round(float(phi), 1)
                rows.append({"typical": round(float(val[index[masks[0]], j]), 1), **contrib})
            out[tgt] = rows
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
