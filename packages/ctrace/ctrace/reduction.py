"""C-Trace reduction proof (measurement & verification).

Method: IPMVP Option C-style whole-facility regression. For each energy carrier we fit a
baseline model on the pre-intervention period:  quantity ~ production + cooling degree days.
The model predicts what the factory *would have used* in the reporting period with no change
(the counterfactual, adjusted for production and weather). Avoided use = predicted − actual.
Uncertainty is the 90% prediction interval of the summed counterfactual (OLS), following the
spirit of ASHRAE Guideline 14. Savings are only reported as "verified" if the lower bound > 0.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import stats

from .factors import factor_for

CARRIERS = {"natural_gas": "gas_m3", "grid_electricity": "grid_kwh"}


@dataclass
class CarrierResult:
    activity: str
    r2: float
    cv_rmse: float
    predicted: float
    actual: float
    avoided: float
    avoided_low: float
    avoided_high: float
    avoided_tco2e: float
    avoided_tco2e_low: float
    avoided_tco2e_high: float
    verified: bool


def _design(df: pd.DataFrame) -> np.ndarray:
    return np.column_stack([np.ones(len(df)), df["pieces"].to_numpy(float), df["cdd"].to_numpy(float)])


def prove_reduction(monthly: pd.DataFrame, baseline_end: str, reporting_start: str) -> tuple[pd.DataFrame, list[CarrierResult]]:
    """monthly: period, pieces, cdd, gas_m3, grid_kwh (confirmed values only)."""
    base = monthly[monthly.period <= baseline_end].dropna()
    rep = monthly[monthly.period >= reporting_start].dropna()
    curves = monthly[["period"]].copy()
    results = []
    for activity, col in CARRIERS.items():
        X, y = _design(base), base[col].to_numpy(float)
        beta, *_ = np.linalg.lstsq(X, y, rcond=None)
        resid = y - X @ beta
        n, p = X.shape
        dof = max(n - p, 1)
        s2 = float(resid @ resid / dof)
        r2 = 1 - float(resid @ resid) / float(((y - y.mean()) ** 2).sum())
        cv_rmse = float(np.sqrt(s2) / y.mean())
        Xall = _design(monthly)
        curves[f"baseline_{col}"] = Xall @ beta
        Xr = _design(rep)
        pred = Xr @ beta
        XtX_inv = np.linalg.inv(X.T @ X)
        ones = np.ones(len(Xr))
        # variance of the sum of new observations: s2 * (m + 1' Xr (X'X)^-1 Xr' 1)
        var_sum = s2 * (len(Xr) + ones @ Xr @ XtX_inv @ Xr.T @ ones)
        half = stats.t.ppf(0.95, dof) * np.sqrt(var_sum)
        actual = float(rep[col].sum())
        predicted = float(pred.sum())
        avoided = predicted - actual
        # convert with the emission factor of each reporting month
        ef = np.mean([factor_for(activity, int(pp[:4])).kgco2e_per_unit for pp in rep.period]) / 1000.0
        results.append(CarrierResult(
            activity=activity, r2=round(r2, 3), cv_rmse=round(cv_rmse, 3),
            predicted=round(predicted, 1), actual=round(actual, 1), avoided=round(avoided, 1),
            avoided_low=round(avoided - half, 1), avoided_high=round(avoided + half, 1),
            avoided_tco2e=round(avoided * ef, 2), avoided_tco2e_low=round((avoided - half) * ef, 2),
            avoided_tco2e_high=round((avoided + half) * ef, 2), verified=bool(avoided - half > 0),
        ))
    return curves, results


# ====================================================================== v2: per-intervention proof
# ASHRAE Guideline 14-2014 calibration criteria for *monthly* baseline models (widely cited; see DECISIONS D41):
# CV(RMSE) ≤ 15 %, |NMBE| ≤ 5 %. R² ≥ 0.75 and ≥ 12 baseline months follow common IPMVP Option C practice
# (a full operating cycle). A reduction is "Verified" only if all hold AND the 90% interval's lower bound > 0.
LIMITS = {"cv_rmse_max": 0.15, "nmbe_abs_max": 0.05, "r2_min": 0.75, "min_baseline_months": 12}
CARRIER_COLUMN = {"natural_gas": "gas_m3", "grid_electricity": "grid_kwh", "diesel": "diesel_l"}
KEYWORDS_BY_CARRIER = {  # infer which carrier an intervention affects when it was not declared
    "natural_gas": ("boiler", "steam", "economiser", "economizer", "thermic", "condensate", "burner", "dryer", "gas"),
    "grid_electricity": ("solar", "pv", "led", "lighting", "chiller", "vfd", "motor", "compressor", "inverter", "hvac", "kwp"),
    "diesel": ("diesel", "genset", "generator"),
}


def infer_affects(measure: str) -> list[str]:
    m = measure.lower()
    return [c for c, words in KEYWORDS_BY_CARRIER.items() if any(w in m for w in words)]


@dataclass
class Window:
    intervention_id: str | None
    measure: str
    activity: str
    baseline: list[str]
    reporting: list[str]


def windows(interventions: list[dict], all_periods: list[str]) -> list[Window]:
    """For each intervention and each carrier it affects: baseline = months before it, back to the previous
    intervention on the same carrier (or the start of data); reporting = from it to the next one (or the end)."""
    out = []
    for iv in sorted(interventions, key=lambda x: x["from"]):
        for act in iv.get("affects") or infer_affects(iv["measure"]):
            same = sorted(x["from"] for x in interventions if act in (x.get("affects") or infer_affects(x["measure"])))
            prev = max((p for p in same if p < iv["from"]), default=None)
            nxt = min((p for p in same if p > iv["from"]), default=None)
            base = [p for p in all_periods if p < iv["from"] and (prev is None or p >= prev)]
            rep = [p for p in all_periods if p >= iv["from"] and (nxt is None or p < nxt)]
            out.append(Window(iv.get("id"), iv["measure"], act, base, rep))
    return out


@dataclass
class Proof:
    activity: str
    measure: str
    intervention_id: str | None
    baseline_from: str
    baseline_to: str
    reporting_from: str
    reporting_to: str
    n_baseline: int
    n_reporting: int
    excluded: dict                 # {"baseline": [...], "reporting": [...]}: Low-confidence months left out
    coefficients: dict             # intercept, per_piece, per_cdd
    r2: float
    cv_rmse: float
    nmbe: float
    checks: dict                   # check -> {"value", "limit", "pass"}
    predicted: float
    actual: float
    avoided: float
    avoided_low: float
    avoided_high: float
    unit: str
    tco2e: float
    tco2e_low: float
    tco2e_high: float
    factor_ids: list
    tariff_bdt: float | None
    bdt_saved: float | None
    verified: bool
    status_reason: str
    curve: list                    # [{"period", "baseline", "actual", "used"}]

    def as_dict(self) -> dict:
        from dataclasses import asdict
        return asdict(self)


def prove(monthly: pd.DataFrame, w: Window, excluded: set[str], factor_of: dict[str, tuple[str, float]],
          tariff_bdt: float | None) -> Proof | None:
    """monthly: period, pieces, cdd and the carrier column (confirmed values). factor_of: period → (factor_id, kg/unit)."""
    col = CARRIER_COLUMN[w.activity]
    unit = {"natural_gas": "m3", "grid_electricity": "kWh", "diesel": "litre"}[w.activity]
    data = monthly.set_index("period")
    usable = lambda ps: [p for p in ps if p in data.index and p not in excluded  # noqa: E731
                         and pd.notna(data.at[p, col]) and pd.notna(data.at[p, "pieces"])]
    base_p, rep_p = usable(w.baseline), usable(w.reporting)
    if len(base_p) < 4 or not rep_p:
        return None
    base, rep = data.loc[base_p], data.loc[rep_p]
    X, y = _design(base), base[col].to_numpy(float)
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ beta
    n, p = X.shape
    dof = max(n - p, 1)
    s2 = float(resid @ resid / dof)
    sst = float(((y - y.mean()) ** 2).sum())
    r2 = 1 - float(resid @ resid) / sst if sst > 0 else 0.0
    cv = float(np.sqrt(s2) / y.mean())
    nmbe = float(resid.sum() / (dof * y.mean()))
    Xr = _design(rep)
    pred_m = Xr @ beta
    act_m = rep[col].to_numpy(float)
    ones = np.ones(len(Xr))
    var_sum = s2 * (len(Xr) + ones @ Xr @ np.linalg.inv(X.T @ X) @ Xr.T @ ones)
    half = float(stats.t.ppf(0.95, dof) * np.sqrt(var_sum))
    avoided_m = pred_m - act_m
    ef = np.array([factor_of[pp][1] for pp in rep_p]) / 1000.0       # tCO2e per unit, each reporting month's factor
    tco2e = float(avoided_m @ ef)
    ef_mean = float(ef.mean())
    avoided = float(avoided_m.sum())
    checks = {
        "cv_rmse": {"value": round(cv, 4), "limit": f"≤ {LIMITS['cv_rmse_max']:.0%}", "pass": cv <= LIMITS["cv_rmse_max"]},
        "nmbe": {"value": round(nmbe, 4), "limit": f"within ±{LIMITS['nmbe_abs_max']:.0%}", "pass": abs(nmbe) <= LIMITS["nmbe_abs_max"]},
        "r2": {"value": round(r2, 4), "limit": f"≥ {LIMITS['r2_min']}", "pass": r2 >= LIMITS["r2_min"]},
        "baseline_months": {"value": len(base_p), "limit": f"≥ {LIMITS['min_baseline_months']}", "pass": len(base_p) >= LIMITS["min_baseline_months"]},
        "interval_lower_bound": {"value": round(avoided - half, 1), "limit": "> 0", "pass": avoided - half > 0},
    }
    failed = [k for k, c in checks.items() if not c["pass"]]
    reason = ("All ASHRAE Guideline 14 monthly criteria met and the 90% interval excludes zero"
              if not failed else "Not verified: " + ", ".join(failed).replace("_", " ") + " outside the limit")
    fitted_all = _design(data.reindex(w.baseline + w.reporting).dropna(subset=["pieces", "cdd"])) @ beta
    curve_periods = [pp for pp in w.baseline + w.reporting if pp in data.index and pd.notna(data.at[pp, "pieces"])]
    curve = [{"period": pp, "baseline": round(float(b), 1), "actual": None if pd.isna(data.at[pp, col]) else round(float(data.at[pp, col]), 1),
              "used": pp in base_p or pp in rep_p, "phase": "baseline" if pp < w.reporting[0] else "reporting"}
             for pp, b in zip(curve_periods, fitted_all, strict=True)]
    return Proof(
        activity=w.activity, measure=w.measure, intervention_id=w.intervention_id,
        baseline_from=base_p[0], baseline_to=base_p[-1], reporting_from=rep_p[0], reporting_to=rep_p[-1],
        n_baseline=len(base_p), n_reporting=len(rep_p),
        excluded={"baseline": sorted(set(w.baseline) & excluded), "reporting": sorted(set(w.reporting) & excluded)},
        coefficients={"intercept": round(float(beta[0]), 3), "per_piece": round(float(beta[1]), 6), "per_cdd": round(float(beta[2]), 3)},
        r2=round(r2, 4), cv_rmse=round(cv, 4), nmbe=round(nmbe, 5), checks=checks,
        predicted=round(float(pred_m.sum()), 1), actual=round(float(act_m.sum()), 1), avoided=round(avoided, 1),
        avoided_low=round(avoided - half, 1), avoided_high=round(avoided + half, 1), unit=unit,
        tco2e=round(tco2e, 2), tco2e_low=round(tco2e - half * ef_mean, 2), tco2e_high=round(tco2e + half * ef_mean, 2),
        factor_ids=sorted({factor_of[pp][0] for pp in rep_p}),
        tariff_bdt=None if tariff_bdt is None else round(tariff_bdt, 2),
        bdt_saved=None if tariff_bdt is None else round(avoided * tariff_bdt, 0),
        verified=not failed, status_reason=reason, curve=curve,
    )
