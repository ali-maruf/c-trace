# C-Trace Model Card

Two learned components: the **C-Trace Verifier** (is each month's energy plausible?) and **document extraction** (what does the bill say?). Neither produces an emissions number.

## C-Trace Verifier v2

*Trained 2026-11-20 on C-Trace simulator (600 factories × 48 months, 2022-01…2025-12, seed 42; weather: NASA POWER monthly T2M at Gazipur / Savar (24.0, 90.5), Dhaka / Narayanganj (23.5, 90.5), Chattogram (22.5, 92.0), Mymensingh / Bhaluka (24.5, 90.5)).*

### What it does
Predicts how much natural gas, grid electricity and diesel a garment factory *should* have used in a month, given its production, type, assets and weather. C-Trace compares that to what the bills say and flags months that don't add up. It does **not** calculate emissions — that is deterministic code with IPCC/Ember factors.

### Algorithm
HistGradientBoostingRegressor (log1p target) + factory LOO-median calibration + IsolationForest with a monotonic solar constraint + heteroscedastic scale σ(x) (quantile GBM) + split-conformal 90% band + group-Shapley drivers.

Features: `type_code, log_workers, has_wet_processing, captive_gas_gen, solar_kwp, log_pieces, month_sin, month_cos, cdd`

Factories — train: 360, calibration: 90, held-out test: 150 (split by factory, never seen in training).

### Prediction quality (held-out factories)

| Energy carrier | MAPE, new factory | MAPE after self-calibration | σ (log residual) |
|---|---|---|---|
| Natural gas | 21.6% | 5.9% | 0.066 |
| Grid electricity | 32.9% | 7.7% | 0.090 |
| Diesel | 46.2% | 27.0% | 0.325 |

Expected band (split-conformal, target 90%, calibrated on factories not used for training), on held-out factories:

| Energy carrier | Coverage | Half-width (≈ ±%) |
|---|---|---|
| Natural gas | 88.5% | ±12% |
| Grid electricity | 89.1% | ±18% |
| Diesel | 89.5% | ±71% |

### Error detection (5% injected errors, held-out factories, at the Low threshold the app uses (|z| ≥ 3 gas/grid, ≥ 5 diesel))

| Energy carrier | ROC-AUC | Precision | Recall | F1 | Naive month-over-month rule F1 |
|---|---|---|---|---|---|
| Natural gas | 0.999 | 89.9% | 98.0% | 93.8% | 40.7% |
| Grid electricity | 0.994 | 82.9% | 86.4% | 84.6% | 54.5% |
| Diesel | 0.865 | 95.3% | 33.1% | 49.1% | 13.9% |

Grading used in the app: gas and grid electricity → Medium at |z| ≥ 2.5, Low at |z| ≥ 3; diesel → Medium at |z| ≥ 3, Low at |z| ≥ 5 (diesel depends on unobserved grid outages). Document checks (implied tariff, missing or duplicate bills) can also set a month to Low.

Recall by error type:

- **Natural gas** — under_reporting (×0.4–0.7): 98%, unit_error (×1000 or ÷1000): 100%, double_count (×2): 99%, over_reporting (×1.4–1.8): 95%
- **Grid electricity** — under_reporting (×0.4–0.7): 78%, unit_error (×1000 or ÷1000): 100%, double_count (×2): 95%, over_reporting (×1.4–1.8): 72%
- **Diesel** — under_reporting (×0.4–0.7): 15%, unit_error (×1000 or ÷1000): 100%, double_count (×2): 7%, over_reporting (×1.4–1.8): 2%

### v2 vs v1 on the same held-out factories (same injected errors)

| Energy carrier | ROC-AUC v1 → v2 | F1 v1 → v2 |
|---|---|---|
| Natural gas | 0.999 → 0.999 | 86.8% → 93.8% |
| Grid electricity | 0.993 → 0.994 | 75.4% → 84.6% |
| Diesel | 0.863 → 0.865 | 42.9% → 49.1% |

### Fairness: false-flag rate on clean held-out months

Share of months with no injected error that the model alone would grade **Low**, by factory size (worker terciles: small ≤ 1,838, medium ≤ 2,988, large > 2,988). Release rule: small factories must not be flagged more than 1.5× as often as large ones.

| Calibration | Small | Medium | Large | Small ÷ large |
|---|---|---|---|---|
| Global σ | 7.73% | 4.17% | 3.33% | 2.32× |
| Heteroscedastic, rate-controlled σ(x) (released) | 2.08% | 1.00% | 1.61% | 1.29× |

| Factory type | False-flag rate | Months |
|---|---|---|
| denim wash | 0.52% | 1,152 |
| knit composite | 1.49% | 3,024 |
| sweater | 2.96% | 912 |
| woven | 1.61% | 2,112 |

Result: **passes** the release rule after the fix (heteroscedastic, rate-controlled scale: σ(x) = shape(x) × k, shape = quantile-GBM (q = 0.9) of |residual| from 4-fold out-of-fold residuals of the training factories, k set on calibration factories per factory type (Mondrian) so that a set share of clean months reach the Low threshold: gas_m3 0.5%, grid_kwh 0.5%, diesel_l 0.1%).

### Monitoring
A weekly job compares the spread of recent calibrated residuals (all factories' latest verification runs) with the training σ per carrier and raises an alert when it moves by more than 30%: Natural gas σ₀ = 0.066, Grid electricity σ₀ = 0.090, Diesel σ₀ = 0.325.

### Limitations — read before quoting these numbers
- **Training data is simulated.** Bangladesh has no open factory-level monthly energy dataset. The C-Trace simulator (`packages/ctrace/ctrace/simulator.py`) encodes transparent engineering assumptions (kWh/piece, steam per piece, captive gas generation, outages, solar yield, Eid shutdowns). Metrics show the method works on data with this structure; they are **not** proof of real-world accuracy. Retrain with pilot data: `uv run python ml/train.py --data pilot.csv`.
- Small under-reporting (<30%) is hard to separate from genuine efficiency gains; C-Trace grades such months Medium and asks for evidence rather than accusing.
- Diesel use depends on grid outages, which are noisy, so diesel flags are weaker.

### Responsible use
- A flag means *please check this document*, never *this factory is cheating*. A human reviews every Low grade.
- No personal data is used. Factory data stays private to the factory unless it chooses to share a report.
- Every reported tonne is traceable to a source document hash and an emission factor with a citation.

## Document extraction (eval set: `data/eval/bills/`)

36 fictional, labelled bills: 16 pdf text (en), 10 photo (bn), 4 photo (en), 4 pdf text (bn), 2 pdf text (mixed). Field accuracy is the share of bills where the field matches the label (quantity within 0.5%, amount within 0.1%). "Critical" means activity, period and quantity are all correct.

| Method | Model(s) | Run | activity | period | quantity | unit | amount_bdt | bill_no | Critical | Privacy leaks | Dropped digit flagged |
|---|---|---|---|---|---|---|---|---|---|---|---|
| rules | — | 2026-11-19 | 61% | 53% | 53% | 61% | 50% | 61% | 50% | 0 | yes |
| haiku | — | not run (needs ANTHROPIC_API_KEY) | — | — | — | — | — | — | — | — | — |
| sonnet | — | not run (needs ANTHROPIC_API_KEY) | — | — | — | — | — | — | — | — | — |
| cascade | — | not run (needs ANTHROPIC_API_KEY) | — | — | — | — | — | — | — | — | — |

Critical accuracy by document group:

| Method | pdf_text/bn | pdf_text/en | pdf_text/mixed | photo/bn | photo/en |
|---|---|---|---|---|---|
| rules | 25% (n=4) | 94% (n=16) | 100% (n=2) | 0% (n=10) | 0% (n=4) |

Read these numbers carefully:
- **The eval set is synthetic and was built by the same team as the rules parser.** Its English layouts are ones the rules parser was written for, so the rules row is an upper bound for those layouts, not an estimate for real bills. Its value is the comparison across methods, Bangla, photos and the traps.
- The rules parser cannot read photos at all; Bangla PDFs often store text in a visual order with private glyphs, which the parser can only partly repair.
- Add consented, anonymised real bills to `data/eval/bills/` (with labels) before quoting accuracy to a customer.

