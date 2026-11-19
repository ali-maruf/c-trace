# C-Trace — Master Build Prompt for Claude Code

**How to use this prompt**
1. Unzip the `c-trace` starter repo, `cd c-trace`, run `pip install -r requirements.txt && python ml/train.py && pytest -q`. All tests should pass before you start.
2. Open Claude Code in that folder and paste everything below the line. Ask it to work **one phase at a time** and to stop for your review at the end of each phase.
3. `CLAUDE.md` in the repo root holds the short, permanent rules. This file is the full brief.

---

## 0. Your role
You are the founding engineer of **C-Trace**, a carbon measurement, reporting and verification (MRV) startup in Bangladesh. You are turning a working Python prototype (already in this repo) into a production-grade, deployable product for a national AI hackathon (CarbonIQ track, "Verifiable emissions reduction") and then for real pilot customers. Write code a senior engineer would be proud to defend in front of judges. Every feature must work end to end. Nothing is decorative.

## 1. Product in one paragraph
**C-Trace — Trace every tonne.** Bangladeshi garment factories now have to report carbon emissions to keep EU buyers, to get green loans and to meet new BSEC/FRC disclosure rules. Their data is stuck in paper gas bills, electricity bills and diesel receipts, often in Bangla. C-Trace reads these documents with AI, calculates Scope 1 and 2 emissions with deterministic code, verifies every month against what the factory *should* have used (ML trained on factory energy behaviour), proves reductions statistically (IPMVP Option C-style), and generates buyer, bank (IFRS S2-aligned) and BSEC reports from one verified dataset. Every number is traceable to a source document and a cited emission factor.

**Users**
- **Factory sustainability / compliance manager** (primary): uploads bills, reviews flags, exports reports.
- **Factory owner / executive**: dashboard, savings, plain-language answers.
- **Brand sourcing / sustainability team** (buyer): read-only verified supplier data.
- **Bank sustainable-finance officer** (lender): borrower emissions for IFRS S2 and green-loan eligibility.
- **C-Trace reviewer** (internal): resolves Low-confidence months.

## 2. Brand rules (apply everywhere: UI, docs, PDFs, API titles, logs, commits)
- The name is always **C-Trace**: capital C, hyphen, capital T. Never "Ctrace", "C-trace", "CTrace" or "C Trace". Python package: `ctrace`. Slugs and URLs: `c-trace`.
- Tagline: **"Trace every tonne."** Secondary line: "Verifiable carbon MRV for factories."
- Model name: **C-Trace Verifier** (versioned: v1, v2…). Report family: **C-Trace Reports**. Assistant: **C-Trace Copilot**.
- Colours: primary teal `#0F766E`, teal-light `#F0FDFA`, ink `#111827`, slate `#475569`, amber (Medium) `#D97706`, red (Low) `#DC2626`, sky (grid electricity) `#38BDF8`, gas `#0F766E`, diesel `#F59E0B`. Confidence colours are semantic and must be the same everywhere.
- Typography: Inter (UI), JetBrains Mono (trace IDs, hashes).
- Voice: precise, calm, evidence-first. Say "C-Trace flagged this month because…", never "AI detected fraud". No hype words ("revolutionary", "game-changing", "unlock", "seamless").
- Logo: a wordmark "C-Trace" in teal, with a small trace line (a polyline ending in a dot) as the icon. Generate it as an SVG in `apps/web/public/brand/`.

## 3. Non-negotiable engineering principles
1. **AI reads and explains; code calculates.** No LLM ever produces an emissions number, a factor or a sum. LLMs extract fields from documents, retrieve guidance, write narratives and answer questions using tool calls that return computed data.
2. **Traceability.** Every ledger row stores `trace_id`, document SHA-256, the extracted field evidence, the factor ID and version, the factor source and the arithmetic string. Any number in the UI or a report can be clicked back to its bill.
3. **Number guard.** Any LLM-written text containing a number that isn't (a rounding of) a verified fact is rejected and regenerated or replaced with template text. Keep and extend `ctrace/report.py::number_guard`.
4. **Human in the loop.** Low-confidence months and every correction suggestion need explicit human acceptance, stored in the audit log with user and timestamp.
5. **Honest data labelling.** Simulated data is labelled simulated. The demo factory is fictional. Metrics in the model card are computed, never typed in by hand.
6. **No AI slop.** No lorem ipsum, placeholder charts, fake testimonials, fake logos of real companies, invented statistics, dead buttons, unused dependencies or TODO stubs presented as features. If something is out of scope, it doesn't appear in the UI.
7. **Do not invent APIs.** Before integrating any external API (Climate TRACE, NASA POWER, Anthropic, Supabase), read its current documentation and write a small contract test. If an endpoint differs from what this prompt says, follow the docs and note it in `docs/DECISIONS.md`.
8. **Reproducible.** `make setup && make test && make demo` must work on a clean machine. Pin versions.

## 4. Starting point: keep the prototype, don't rewrite it
The repo already contains a working, tested core. Read every file before changing anything:
- `ctrace/factors.py` — IPCC 2006 fuel factors with AR6 GWPs; Bangladesh grid intensity by year from Ember/OWID
- `ctrace/extraction.py` — offline PDF parser + Claude vision path → `BillRecord`; unit normalisation; tariff sanity check
- `ctrace/calculate.py` — deterministic ledger with trace IDs
- `ctrace/simulator.py` — transparent factory energy simulator (training data)
- `ctrace/verify.py` — C-Trace Verifier: HistGradientBoosting expected-energy models, per-operating-period self-calibration, Isolation Forest, grading
- `ctrace/pipeline.py` — joins everything; gap and duplicate detection; tariff-based correction suggestions
- `ctrace/reduction.py` — baseline regression with 90% prediction intervals
- `ctrace/rag.py` + `ctrace/knowledge/kb.md` — TF-IDF retrieval over C-Trace's own guidance summaries
- `ctrace/report.py` — facts → narrative (template or LLM + number guard) → branded PDF
- `ml/train.py` — training, evaluation on held-out factories with injected errors, generated `models/MODEL_CARD.md`
- `app/streamlit_app.py` — prototype UI (keep it working as `apps/demo-streamlit` for the hackathon fallback)
- `api/main.py` — FastAPI
- `tests/test_ctrace.py` — 13 passing tests. Never let them regress.

## 5. Target architecture
```
c-trace/
  apps/
    web/                 Next.js 15 (App Router, TypeScript strict), Tailwind, shadcn/ui, Recharts, TanStack Query
    demo-streamlit/      the existing Streamlit app (hackathon fallback)
  services/
    api/                 FastAPI (Python 3.12), wraps packages/ctrace; OpenAPI → generated TS client for web
    worker/              background jobs (extraction, verification, report rendering) with a Postgres-backed queue
  packages/
    ctrace/              the Python core (moved from ./ctrace, imports unchanged)
  ml/                    training, evaluation, model registry (models/<name>/<version>/)
  data/                  public/ (fetched datasets), samples/ (fictional demo factory)
  infra/                 docker-compose.yml, Supabase migrations, seed scripts
  docs/                  ARCHITECTURE.md (with Mermaid diagrams), DECISIONS.md, MODEL_CARD.md, API.md, PITCH_DEMO.md
```
- **Database:** Supabase Postgres + `pgvector` + Storage (original documents) + Auth (email OTP). Row-level security by `organization_id`.
- **Queue:** Postgres-based (`procrastinate` or a simple `jobs` table with `SKIP LOCKED`). No Redis unless needed.
- **Deployment:** web on Vercel, api + worker on Render or Fly.io (Docker), Supabase cloud. One `docker compose up` for local.

## 6. Data model (create as SQL migrations)
`organizations, users, memberships(role: owner|manager|viewer|reviewer|buyer|lender)`
`factories(id, org_id, name, location, lat, lon, factory_type, workers, has_wet_processing, captive_gas_gen)`
`assets(factory_id, kind: solar|boiler|genset|chiller, capacity, unit, from_period)`
`interventions(factory_id, measure, from_period, capex_bdt, notes)`
`documents(id, factory_id, storage_path, sha256 UNIQUE per factory, media_type, uploaded_by, uploaded_at)`
`extractions(document_id, method, model, raw_json, field_evidence jsonb, confidence, created_at)`
`activity_records(id, document_id, factory_id, activity, period, quantity, unit, amount_bdt, bill_no, status: extracted|confirmed|corrected|rejected, confirmed_by, confirmed_at)`
`emission_factors(id, activity, region, year, kgco2e_per_unit, breakdown jsonb, source, source_url, version, valid_from, valid_to)`
`ledger_entries(trace_id PK, activity_record_id, factor_id, scope, quantity, unit, kgco2e, calculation, created_at)`
`production_records(factory_id, period, pieces, source)`
`weather_monthly(lat, lon, period, t2m_c, cdd, source)`
`verification_runs(id, factory_id, model_version, created_at)`, `verification_results(run_id, period, z jsonb, iso_score, confidence, reasons jsonb)`
`reductions(id, factory_id, intervention_id, baseline_from, baseline_to, reporting_from, reporting_to, activity, avoided, low, high, tco2e, r2, cv_rmse, verified)`
`reports(id, factory_id, format, period_from, period_to, facts jsonb, narrative, method, pdf_path, share_token, expires_at)`
`kb_chunks(id, title, source, source_url, text, embedding vector(384))`
`audit_log(id, org_id, user_id, action, entity, entity_id, before jsonb, after jsonb, at)`

## 7. LLMs and models
| Job | Model | Notes |
|---|---|---|
| Bill extraction (PDF, photo, Bangla) | `claude-haiku-5-5` via Anthropic API (vision + PDF document blocks) | Use **tool use with a strict JSON schema** for output. Return per-field `evidence` (verbatim text) and confidence. Convert Bangla digits (০-৯). Cache by document SHA-256. Escalate to `claude-sonnet-5-5` when confidence < 0.7. |
| Report narratives, Copilot | `claude-sonnet-5-5` | Grounded in retrieved KB chunks and tool results only. Always number-guarded. |
| Embeddings for RAG | `intfloat/multilingual-e5-small` (open, runs locally, supports Bangla, 384-dim) via `sentence-transformers` | Keep TF-IDF as the offline fallback. |
| Expected-energy models | C-Trace Verifier (scikit-learn HistGradientBoosting + IsolationForest) | Already built; extend (section 9). |
| Reduction proof | OLS baseline regression (NumPy/SciPy) | Already built; extend (section 10). |

Put the model IDs in config and environment variables, never in business logic. Every LLM call is logged (model, tokens, latency, document hash, prompt version) to `llm_calls`. Write prompts as versioned files in `packages/ctrace/prompts/`.

## 8. Public datasets (fetch with scripts, store with provenance, cite in UI and PDFs)
| Dataset | Use in C-Trace | Script |
|---|---|---|
| Our World in Data energy data (Ember electricity) | Bangladesh grid carbon intensity by year → Scope 2 location-based factor | `scripts/fetch_public_data.py` (exists) |
| Our World in Data CO2 data | National context charts | same |
| IPCC 2006 Guidelines defaults (Vol. 2 Stationary Combustion) + AR6 GWPs | Fuel factors (already coded; move into the `emission_factors` table with versions) | migration seed |
| Climate TRACE API v6 (Bangladesh, manufacturing and other sectors) | Independent facility-level benchmark: show the factory's C-Trace total next to nearby or same-sector Climate TRACE assets | `scripts/fetch_climate_trace.py` (exists; verify schema against the docs) |
| NASA POWER monthly API (T2M at factory lat/lon) | Real cooling degree days, replacing the climatology in `simulator.py` / pipeline | new `scripts/fetch_weather.py` |
| Copernicus Sentinel-5P (NO2) via Google Earth Engine | **Stretch, behind a feature flag:** activity signal around industrial zones; never used to grade a single factory on its own | new `ml/satellite/` |
| Official Bangladesh grid emission factor (DoE / national publication) | Statutory Scope 2 factor; table field ready, value entered by admin with source URL | admin UI |

Never scrape sites that forbid it. Store `source`, `source_url`, `retrieved_at` and `license` for every dataset.

## 9. C-Trace Verifier v2 (ML)
Keep v1 and its evaluation harness. Add:
1. **Real weather**: CDD from NASA POWER per factory location.
2. **Pilot-data path**: `ml/train.py --data pilot.csv` with schema validation (pandera). A model registry: `models/ctrace-verifier/<version>/{model.joblib, metrics.json, MODEL_CARD.md}`; the API loads the version pinned in config.
3. **Explainability**: for every expected value, show the top drivers (production, weather, solar, wet processing) using per-feature contributions (permutation-based or SHAP for HistGradientBoosting). Render them in the Verify screen as a small waterfall.
4. **Fairness check**: in evaluation, report the false-flag rate by factory size (workers terciles) and type. If small factories are flagged more than 1.5× as often, fix calibration before release. Publish this in the model card.
5. **Drift monitoring**: weekly job comparing residual distributions to training; alert when σ shifts by more than 30%.
6. **Climate TRACE benchmark** (signal, not grade): show where the factory sits in the sector distribution.
Acceptance: all v1 tests pass; the model card is regenerated from code; the demo factory still flags exactly the two planted problems (dropped-digit gas bill 2025-09, missing diesel receipt 2026-07).

## 10. Reduction proof v2
- Per-intervention baselines (choose the baseline window automatically from `interventions`), exclude Low-confidence months, report R², CV(RMSE) and NMBE against ASHRAE Guideline 14 monthly limits, and refuse to mark "Verified" when the baseline model fails those limits.
- Convert avoided energy to tCO2e with the factor valid in each reporting month, and to BDT with the factory's own median tariff.
- Output a **Reduction Certificate** PDF: method, baseline, statistics, interval, documents used, reviewer sign-off. Label it "C-Trace verification statement — not a third-party assurance or carbon credit."

## 11. RAG v2 and C-Trace Copilot
- Knowledge base: keep `knowledge/kb.md` style — **C-Trace's own summaries** with the source named and linked. Do not paste copyrighted standards text. Add chunks for the GHG Protocol Scope 3 Category 1 perspective (brand view), Bangladesh Bank green taxonomy basics, EU product-passport direction (marked "upcoming"), and an FAQ.
- Hybrid retrieval: pgvector cosine plus BM25, reranked; return chunk IDs as citations.
- **C-Trace Copilot** (English and Bangla toggle): executives ask "Why did July's emissions go up?" or "How much did the boiler retrofit save?". Implement as a tool-using agent with tools `get_summary(period)`, `get_ledger(period, activity)`, `get_verification(period)`, `get_reductions()`, `search_guidance(query)`. Answers must cite trace IDs or KB chunk IDs and pass the number guard. Refuse questions outside the factory's own data and guidance.

## 12. Web app (apps/web) — screens and acceptance criteria
1. **Landing** (`/`): one honest page covering the problem, how C-Trace works (4 steps), the live demo button and the model card link. No fake customer logos.
2. **Onboarding**: create a factory profile (type, workers, wet processing, captive generator, location on a map → lat/lon for weather) plus assets and interventions.
3. **Upload centre**: drag-and-drop PDFs and images, mobile camera capture, per-file progress, dedupe by hash with a message ("Already uploaded on 12 Mar").
4. **Review queue**: side-by-side bill viewer and extracted fields; evidence highlighted; tariff warnings; accept, edit or reject; correction suggestions need an explicit Accept; keyboard shortcuts (A accept, E edit, J/K next/previous).
5. **Dashboard**: total, Scope 1, Scope 2 and intensity KPI cards; stacked monthly bars by source; intensity line; intervention markers; period selector.
6. **Trace view**: click any bar or number → ledger rows → bill viewer with the evidence highlighted, factor card with source link, and the arithmetic.
7. **Verify**: confidence timeline (High/Medium/Low pills), expected-vs-reported chart per carrier, reasons in plain language, driver waterfall, reviewer resolution with a comment.
8. **Reductions**: per-intervention baseline vs actual chart, interval, statistics with pass/fail badges, BDT saved, certificate download.
9. **Reports**: choose buyer / IFRS S2 lender / BSEC; pick a period; preview; PDF + JSON + CSV; **share link** (read-only, expiring token, revocable, logged).
10. **Buyer and lender portal**: a shared-link view showing only the shared report plus data-quality summary; no access to raw bills unless granted.
11. **Settings**: users and roles, emission factors (view versions; admin can add an official factor with a source URL), data export, delete factory data.
Quality bar: responsive (works on a 375 px phone for upload and review), accessible (WCAG AA contrast, keyboard navigation, aria labels on charts), loading and empty and error states for every screen, optimistic UI only where safe, and no layout shift on dashboards.

## 13. API (services/api)
Versioned `/v1`. Endpoints: auth passthrough, `factories`, `documents` (upload → job), `extractions`, `activity-records` (confirm, correct, reject), `ledger`, `summary`, `verification` (run, results, resolve), `reductions`, `reports` (create, pdf, share), `factors`, `copilot/chat` (streaming), `health`. Pydantic v2 models, OpenAPI tags, request IDs, structured JSON logs, rate limits on LLM endpoints, idempotency keys on uploads. Generate the TypeScript client from OpenAPI (`openapi-typescript`).

## 14. Security, privacy and responsible AI
- Supabase RLS on every table by `org_id`; documents are served via short-lived signed URLs only.
- No personal data is needed; strip names or addresses of individuals if they appear in extracted text.
- Audit log for confirmations, corrections, resolutions, factor changes and shares.
- Data sharing is opt-in per report; the factory can revoke a share at any time.
- A `/model-card` page renders the generated model card, including limitations and the fairness table.
- Copy guardrail: flags are framed as "needs checking", never as accusations.

## 15. Testing and CI
- Python: pytest (unit + golden tests on the demo factory: totals, flagged months, verified reductions), hypothesis tests for unit conversions, contract tests for external APIs (recorded with VCR, skipped offline).
- **Extraction eval set**: `data/eval/bills/` with 30+ labelled bills (fictional, plus anonymised real ones with consent). The `ml/eval_extraction.py` script reports field-level accuracy per method (rules vs haiku vs sonnet). Show these numbers in the model card.
- Web: Vitest + Playwright e2e for the demo flow (load demo → accept correction → verify → reduction → export report).
- GitHub Actions: lint (ruff, mypy, eslint, tsc), tests, build Docker images, and a Playwright smoke test against preview deploys.

## 16. Seed, demo and pitch assets
- `make demo` seeds the fictional **Nodi Knit Composite Ltd.** with the 65 sample bills, production, profile, interventions and a demo user.
- `docs/PITCH_DEMO.md`: a 3-minute script — upload a Bangla bill photo (LLM path), catch the dropped-digit bill, trace a tonne to its bill, prove the boiler retrofit, export the buyer report, share with a "bank" link.
- `docs/ARCHITECTURE.md` with Mermaid diagrams for the data flow, the verification logic and deployment.
- Keep the Streamlit app working as an offline fallback in case the venue Wi-Fi fails.

## 17. Phases (stop for review after each)
| Phase | Deliverable | Done when |
|---|---|---|
| 1 | Monorepo restructure, Makefile, CI, CLAUDE.md, DECISIONS.md | 13 original tests pass from `packages/ctrace`; CI green |
| 2 | Database migrations, seed, API on Postgres | `/v1/summary` returns the same totals as the prototype for the demo factory |
| 3 | Extraction v2 (tool-use schema, Bangla, evidence, caching, eval set) | Eval report generated; offline path still works without a key |
| 4 | Factors registry + public data fetchers (OWID, NASA POWER, Climate TRACE) | Every ledger row references a factor version with a source URL |
| 5 | Verifier v2 (weather, registry, explainability, fairness, drift) | Model card regenerated with the fairness table; demo flags unchanged |
| 6 | Reduction proof v2 + certificate | ASHRAE checks enforced; certificate PDF renders |
| 7 | Web app screens 1–8 | Playwright demo flow passes on mobile and desktop |
| 8 | Reports, sharing, buyer/lender portal | Share link works, expires and is logged |
| 9 | RAG v2 + C-Trace Copilot (EN/BN) | Copilot answers cite trace IDs; number guard tests pass |
| 10 | Security hardening, deploy, docs, pitch assets | Live URL; `docker compose up` works on a clean machine |

## 18. Working agreement
- Start each phase by writing a short plan in `docs/plans/phase-N.md` (files to touch, risks, tests), then implement.
- Small, focused commits with clear messages (`feat(verify): add NASA POWER weather`).
- Run tests after every meaningful change; never mark a phase done with failing tests.
- When you make an assumption (for example a tariff range or an API field name), record it in `docs/DECISIONS.md` with the reason.
- If something can't be done properly in this phase, say so plainly and leave it out of the UI rather than faking it.
