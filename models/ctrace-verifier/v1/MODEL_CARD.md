# C-Trace Verifier v1 — Model Card

*Trained 2026-11-20 on C-Trace simulator v1 (600 factories × 36 months, seed 42).*

### What it does
Predicts how much natural gas, grid electricity and diesel a garment factory *should* have used in a month, given its production, type, assets and weather. C-Trace compares that to what the bills say and flags months that don't add up. It does **not** calculate emissions — that is deterministic code with IPCC/Ember factors.

### Algorithm
HistGradientBoostingRegressor (log1p target) + factory LOO-median calibration + IsolationForest.

Features: `type_code, log_workers, has_wet_processing, captive_gas_gen, solar_kwp, log_pieces, month_sin, month_cos, cdd`

Factories — train: 360, calibration: 90, held-out test: 150 (split by factory, never seen in training).

### Prediction quality (held-out factories)

| Energy carrier | MAPE, new factory | MAPE after self-calibration | σ (log residual) |
|---|---|---|---|
| Natural gas | 26.1% | 5.8% | 0.066 |
| Grid electricity | 31.4% | 7.9% | 0.088 |
| Diesel | 43.0% | 27.3% | 0.321 |

### Error detection (5% injected errors, held-out factories, at |z| ≥ 3)

| Energy carrier | ROC-AUC | Precision | Recall | F1 | Naive month-over-month rule F1 |
|---|---|---|---|---|---|
| Natural gas | 0.999 | 72.8% | 99.6% | 84.1% | 44.4% |
| Grid electricity | 0.997 | 62.9% | 99.6% | 77.1% | 51.3% |
| Diesel | 0.858 | 82.5% | 36.4% | 50.5% | 13.4% |

Grading used in the app: gas and grid electricity → Medium at |z| ≥ 2.5, Low at |z| ≥ 3; diesel → Medium at |z| ≥ 3, Low at |z| ≥ 5 (diesel depends on unobserved grid outages). Document checks (implied tariff, missing or duplicate bills) can also set a month to Low.

Recall by error type:

- **Natural gas** — under_reporting (×0.4–0.7): 100%, unit_error (×1000 or ÷1000): 100%, double_count (×2): 100%, over_reporting (×1.4–1.8): 98%
- **Grid electricity** — under_reporting (×0.4–0.7): 99%, unit_error (×1000 or ÷1000): 100%, double_count (×2): 100%, over_reporting (×1.4–1.8): 100%
- **Diesel** — under_reporting (×0.4–0.7): 18%, unit_error (×1000 or ÷1000): 100%, double_count (×2): 26%, over_reporting (×1.4–1.8): 8%

### Limitations — read before quoting these numbers
- **Training data is simulated.** Bangladesh has no open factory-level monthly energy dataset. The C-Trace simulator (`packages/ctrace/ctrace/simulator.py`) encodes transparent engineering assumptions (kWh/piece, steam per piece, captive gas generation, outages, solar yield, Eid shutdowns). Metrics show the method works on data with this structure; they are **not** proof of real-world accuracy. Retrain with pilot data: `uv run python ml/train.py --data pilot.csv`.
- Small under-reporting (<30%) is hard to separate from genuine efficiency gains; C-Trace grades such months Medium and asks for evidence rather than accusing.
- Diesel use depends on grid outages, which are noisy, so diesel flags are weaker.

### Responsible use
- A flag means *please check this document*, never *this factory is cheating*. A human reviews every Low grade.
- No personal data is used. Factory data stays private to the factory unless it chooses to share a report.
- Every reported tonne is traceable to a source document hash and an emission factor with a citation.
