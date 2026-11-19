# C-Trace — Trace every tonne.

**C-Trace** is a verifiable carbon MRV (measurement, reporting and verification) engine for Bangladeshi export factories.
It turns paper utility bills and fuel receipts into verified, buyer-ready and bank-ready carbon reports, and proves real
reductions with statistics, so factories can earn from cutting emissions.

> Core rule: **the AI reads and explains; deterministic code calculates.** Every tonne carries an audit trail back to a
> source-document hash and a cited emission factor.

**Live demo:** https://c-trace-xi.vercel.app → *Try the live demo* (a private sandbox with a fictional factory; no sign-up).
Architecture: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) · Finals script: [docs/PITCH_DEMO.md](docs/PITCH_DEMO.md) ·
Model card: [models/MODEL_CARD.md](models/MODEL_CARD.md) · Decisions: [docs/DECISIONS.md](docs/DECISIONS.md)

## Quick start (about 2 minutes, no API key needed)

```bash
make setup          # installs uv if missing, then Python 3.12 + pinned deps from uv.lock
make train          # trains the C-Trace Verifier (~10 s) and writes models/MODEL_CARD.md
make test           # core, database/API and Streamlit tests (starts a local Postgres, no Docker needed)
make demo           # seeds the fictional demo factory into Postgres: 65 bills through upload → job → ledger
make api            # REST API, docs at http://localhost:8000/docs  (token: make dev-token)
make worker         # background worker for new uploads
make demo-streamlit # offline fallback UI: open http://localhost:8501 and click "Load demo factory"
cd apps/web && npm ci && npm run dev   # web app on http://localhost:3000 (calls the API on :8000)
```
Run `make help` for every target. The repository is a uv workspace: `packages/ctrace` (core and the
`ctrace.store` Postgres layer), `services/api` (FastAPI), `services/worker` (job runner) and
`apps/demo-streamlit` (offline fallback UI), plus the Next.js web app in `apps/web`. Without `DATABASE_URL`, development uses an embedded Postgres 16 + pgvector
(pgserver) in `.pgdata/`. With Docker: `make compose-up`. API reference: [docs/API.md](docs/API.md).

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
Full diagrams and trust boundaries: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
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
| `packages/ctrace/ctrace/extraction.py` | Offline parser (English + Bangla labels and digits) → one `BillRecord` schema; unit normalisation; tariff sanity check |
| `packages/ctrace/ctrace/extraction_llm.py`, `llm.py`, `prompts/` | Claude extraction: versioned prompt, strict schema, verbatim evidence, evidence check, cache, Haiku → Sonnet escalation, call log |
| `ml/eval_extraction.py`, `data/eval/bills/` | 36 labelled fictional bills (English, Bangla, photos, traps); field accuracy per method in the model card |
| `packages/ctrace/ctrace/calculate.py` | Deterministic ledger: every row keeps trace ID, document SHA-256, factor, source and the arithmetic |
| `packages/ctrace/ctrace/simulator.py` | Transparent factory energy simulator used to train and test the verifier |
| `packages/ctrace/ctrace/verify.py` | C-Trace Verifier: expected-energy models, per-period self-calibration, Isolation Forest, grading |
| `packages/ctrace/ctrace/pipeline.py` | Joins everything; gap and duplicate detection; tariff-based correction suggestions |
| `packages/ctrace/ctrace/reduction.py` | Measurement and verification of savings with prediction intervals |
| `packages/ctrace/ctrace/rag.py`, `ctrace/knowledge/kb.md` | Retrieval over C-Trace's own summaries of the GHG Protocol, IFRS S2, BSEC, Bangladesh Bank, IPMVP |
| `packages/ctrace/ctrace/report.py` | Report facts, narrative (template or LLM + number guard), branded PDF |
| `ml/train.py` | Trains, evaluates on held-out factories with injected errors, writes `MODEL_CARD.md` |
| `apps/demo-streamlit/streamlit_app.py` | Demo UI |
| `packages/ctrace/ctrace/store/` | Postgres: migrations, row-level security per organisation, job queue, ingest → ledger, review + audit log, demo seed |
| `infra/supabase/migrations/` | SQL schema (organisations, factories, documents, extractions, activity records, factors, ledger, …) |
| `services/api/ctrace_api/` | FastAPI `/v1`: factories, production, documents, jobs, review, ledger, summary, factors (see docs/API.md) |
| `services/worker/` | Runs extraction jobs from the Postgres `jobs` table |

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

## Deploy
**Vercel + Supabase (the hosted demo).** `vercel.json` defines two Services in one project: the web app (`apps/web`) and the
API (repo root, `asgi.py`), with `/api/*` routed to the API ([docs/plans/phase-10.md](docs/plans/phase-10.md)).
1. Create a Supabase project (region South Asia / Mumbai) and copy *Connect → Session pooler* (URI, port 5432).
2. `npx vercel link`, then add `DATABASE_URL` with `npx vercel env add DATABASE_URL production`, plus `CTRACE_ENV=production`,
   `CTRACE_STORAGE=db`, `CTRACE_INLINE_JOBS=1`, `CTRACE_MIGRATE_ON_START=1`, `CTRACE_API_PREFIX=/api`,
   `CTRACE_DB_PREPARE=0`, `CTRACE_DB_POOL_MIN=0`, `CTRACE_DB_POOL_MAX=3`.
3. `npx vercel deploy --prod`. Migrations and factor sync run on the first request; `/api/health` reports the database.
4. Optional: `ANTHROPIC_API_KEY` for Claude extraction and narratives; `SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_URL` and
   `NEXT_PUBLIC_SUPABASE_ANON_KEY` for real sign-in (without them the hosted app offers the demo sandbox only).

**Streamlit Community Cloud (offline fallback).** New app → `apps/demo-streamlit/streamlit_app.py`; it installs from
`requirements.txt` (exported from `uv.lock` with the `demo` extra). Or Docker: `make compose-up`.
