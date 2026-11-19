# Phase 5 — C-Trace Verifier v2

## Goal
A stronger, explainable, monitored verifier that keeps v1 and its evaluation harness. Done when the model card
is regenerated from code with a fairness table, and the demo factory still flags exactly 2025-09 (dropped-digit
gas bill) and 2026-07 (missing diesel receipt).

## Model changes (each measured against v1 on the same held-out protocol)
1. **Real weather.** The fleet simulator draws each factory's location from four Bangladeshi industrial
   cells and uses NASA POWER monthly T2M (2022-01 → 2025-12, 48 months) for its cooling load. Scoring
   uses the factory's own POWER series (`weather_monthly`), with labelled climatology for unpublished months.
2. **Expected range.** A split-conformal 90% band per carrier, from calibrated residuals on factories never
   seen in training. The Verify chart shows "expected" as a band, not a line.
3. **Drivers.** Exact Shapley values over feature groups (production, weather, solar, season, profile),
   relative to the factory's own typical month, in the carrier's unit. Contributions sum exactly to
   expected − typical. Rendered as a waterfall.
4. **Fairness.** On clean held-out factories: false-flag rate (Low from the model alone) by worker tercile
   and by factory type. If small factories are flagged more than 1.5× as often as large ones, residual
   spread is calibrated per size tercile, and both before and after are published.
5. **Drift.** A job compares recent calibrated residuals (from stored verification runs, across factories)
   with the training σ per carrier and alerts when the ratio moves more than 30%. It runs weekly from the worker.

## Platform
- Registry: `models/ctrace-verifier/<version>/{model.joblib, metrics.json, MODEL_CARD.md}`; v1 kept.
  `CTRACE_VERIFIER_VERSION` (default v2) picks what the API, worker and demo load.
- `ml/train.py --data pilot.csv`: pandera schema validation with readable errors.
- Persisted verification: `POST /v1/factories/{id}/verification/run`, `GET …/verification` (latest run, with
  expected band and drivers), `POST /v1/verification/{run_id}/{period}/resolve` (reviewer comment, audit-logged).

## Risks
- The demo bills were generated with climatology. Scoring them with real weather shifts expected cooling slightly,
  so the acceptance test is what decides. Thresholds are not tuned to pass it.
- Group Shapley needs 2^5 model evaluations per month and carrier, which is cheap for one factory (22 months).
