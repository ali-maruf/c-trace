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
