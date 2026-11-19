# C-Trace — Trace every tonne.

**C-Trace** is a verifiable carbon MRV (measurement, reporting and verification) engine for Bangladeshi export factories.
It turns paper utility bills and fuel receipts into verified, buyer-ready and bank-ready carbon reports, and proves real
reductions with statistics, so factories can earn from cutting emissions.

> Core rule: **the AI reads and explains; deterministic code calculates.** Every tonne carries an audit trail back to a
> source-document hash and a cited emission factor.

## Quick start (about 2 minutes, no API key needed)

```bash
make setup          # installs uv if missing, then Python 3.12 + pinned deps from uv.lock
make train          # trains the C-Trace Verifier (~10 s) and writes models/MODEL_CARD.md
make demo-streamlit # open http://localhost:8501 and click "Load demo factory"
make test           # core, API and Streamlit smoke tests
make api            # REST API, docs at http://localhost:8000/docs
make sample-data    # (re)creates the fictional demo factory's 65 sample bills
```
Run `make help` for every target. The repository is a uv workspace: `packages/ctrace` (core),
`services/api` (FastAPI) and `apps/demo-streamlit` (demo UI).

Optional: copy `.env.example` to `.env` and set `ANTHROPIC_API_KEY` to turn on Claude vision extraction (photos,
scanned and Bangla bills) and LLM-written report narratives. Every LLM narrative must pass the C-Trace **number guard**:
any figure that isn't in the verified data is rejected.

## The 3-minute demo story
1. **Extract & review**: 65 sample bills (gas, grid electricity, diesel; Jan 2025 – Oct 2026) are read automatically. C-Trace
   flags `2025-09_natural_gas.pdf`: the implied tariff is BDT 315/m³ instead of ~31.5, so it suggests the right quantity
   (amount ÷ the factory's median tariff). A human accepts it.
2. **Emissions**: monthly Scope 1 and Scope 2 by source, intensity per piece, and a trace ledger that opens the original bill
   behind any tonne.
3. **Verify**: the C-Trace Verifier compares reported energy to what the factory *should* have used given production,
   weather and assets. Only the two planted problems are graded Low: the dropped digit and a missing diesel receipt.
4. **Prove reduction**: an IPMVP Option C-style baseline regression proves the January 2026 boiler retrofit and the April 2026
   solar install with 90% confidence intervals and BDT savings.
5. **Reports**: one dataset produces three outputs (brand supplier disclosure, IFRS S2-aligned lender metrics, BSEC ESG
   section), as PDF and JSON, with methodology, factors and the trace ledger in the appendix.

## Architecture
```
bills (PDF / photo) ─► ctrace.extraction ─► human review ─► ctrace.calculate ─► ledger (trace_id, doc hash, factor, maths)
                         rules | Claude vision                                        │
production + profile ─────────────────────────────► ctrace.verify (GBM + IsolationForest + document rules) ─► confidence
                                                     ctrace.reduction (baseline regression, 90% PI) ─► verified tCO2e
                                                     ctrace.rag + ctrace.report (TF-IDF RAG, number guard) ─► PDF/JSON
```

| Module | What it does |
|---|---|
| `packages/ctrace/ctrace/factors.py` | IPCC 2006 fuel factors + AR6 GWPs; Bangladesh grid intensity from Ember/OWID by year |
| `packages/ctrace/ctrace/extraction.py` | Offline PDF parser and Claude vision path → one `BillRecord` schema; unit normalisation; tariff sanity check |
| `packages/ctrace/ctrace/calculate.py` | Deterministic ledger: every row keeps trace ID, document SHA-256, factor, source and the arithmetic |
| `packages/ctrace/ctrace/simulator.py` | Transparent factory energy simulator used to train and test the verifier |
| `packages/ctrace/ctrace/verify.py` | C-Trace Verifier: expected-energy models, per-period self-calibration, Isolation Forest, grading |
| `packages/ctrace/ctrace/pipeline.py` | Joins everything; gap and duplicate detection; tariff-based correction suggestions |
| `packages/ctrace/ctrace/reduction.py` | Measurement and verification of savings with prediction intervals |
| `packages/ctrace/ctrace/rag.py`, `ctrace/knowledge/kb.md` | Retrieval over C-Trace's own summaries of the GHG Protocol, IFRS S2, BSEC, Bangladesh Bank, IPMVP |
| `packages/ctrace/ctrace/report.py` | Report facts, narrative (template or LLM + number guard), branded PDF |
| `ml/train.py` | Trains, evaluates on held-out factories with injected errors, writes `MODEL_CARD.md` |
| `apps/demo-streamlit/streamlit_app.py` | Demo UI |
| `services/api/ctrace_api/main.py` | FastAPI: `/v1/extract`, `/v1/analyze`, `/v1/report`, `/v1/factors` |

## Data and honesty notes
- **Public data used:** Our World in Data energy and CO2 datasets (Bangladesh grid carbon intensity from Ember, national
  emissions). Refresh them with `python scripts/fetch_public_data.py`. Climate TRACE Bangladesh facility data can be pulled
  with `python scripts/fetch_climate_trace.py` (needs internet) for facility benchmarking.
- **Training data is simulated**, because no open factory-level energy dataset exists for Bangladesh. The assumptions are in
  `packages/ctrace/ctrace/simulator.py` and the limitations are in `models/MODEL_CARD.md`. Retrain on pilot data with
  `uv run python ml/train.py --data pilot.csv`.
- **The demo factory, its suppliers and its bills are fictional** and watermarked "SAMPLE".
- The Ember intensity is lifecycle-based. For statutory filings, use the official national grid emission factor and
  supplier-declared gas calorific values.

## Deploy a live demo link (free)
Push this folder to GitHub (the trained model in `models/` is about 10 MB and is included), then go to share.streamlit.io →
New app → pick `apps/demo-streamlit/streamlit_app.py` (it installs from `requirements.txt`, exported from `uv.lock`). If your
scikit-learn version differs from the one used for training, run `make train` again (about 10 seconds). Or use Docker:
`docker build -t c-trace . && docker run -p 8501:8501 c-trace`.
