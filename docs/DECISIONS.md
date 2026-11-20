# C-Trace — Decisions and assumptions

Each entry: what we decided, why, and what would change it. Newest last.

## Phase 1 — repository structure

### D1. uv workspace with a committed lockfile
`pyproject.toml` at the root declares a uv workspace with two members, `packages/ctrace` (core) and
`services/api` (FastAPI). `uv.lock` pins every transitive version; `make setup` runs `uv sync --frozen`.
**Why:** the brief requires pinned, reproducible installs on a clean machine, and one lockfile for
the core, API, demo and dev tools avoids drift between them. scikit-learn is additionally constrained
to `1.9.x` in `packages/ctrace` because the pickled C-Trace Verifier is tied to that minor version.
**Revisit if:** the worker (Phase 2) needs a dependency set that conflicts with the API.

### D2. `requirements.txt` is generated, not edited
It is exported from `uv.lock` (`make requirements`) because Streamlit Community Cloud installs from
`requirements.txt`. CI fails if it drifts from the lockfile.

### D3. Workspace root is discovered, not hard-coded
After the move, `ctrace/config.py` can no longer assume `parent.parent` is the repo root.
`find_root()` uses `CTRACE_ROOT`, else the nearest parent of the package containing `data/samples`,
else the cwd. The Docker image sets `CTRACE_ROOT=/app`. The knowledge base stays package-relative
because it ships with the package.

### D4. `models/` and `scripts/` stay at the root for now
The versioned model registry (`models/ctrace-verifier/<version>/`) is Phase 5. Moving the model now
would be a second move of the same file. The pickled class path `ctrace.verify.Verifier` is unchanged,
so the existing model loads as-is.

### D5. No empty folders for later phases
`apps/web`, `services/worker` and `infra/` are created in the phases that implement them. Empty
scaffolding would look like features that don't exist (brief §3.6).

### D6. Lint and type checks
Ruff (`E, F, W, I, B, UP, SIM`) and mypy run in CI. E501 (line length) is disabled: the prototype uses long,
readable single-line f-strings and reflowing them would be churn without a behaviour change. Everything else
was fixed in code (ambiguous names, `zip(strict=True)`, FastAPI `Annotated[UploadFile, File()]`, typed `Any`
for the sklearn IsolationForest field). One `type: ignore` remains on the Anthropic `messages.create` call in
`extraction.py`; that function is rewritten with strict tool-use schemas in Phase 3.

### D7. Tests live with the code they test
`packages/ctrace/tests` (the 13 original tests + config tests), `services/api/tests` (API smoke tests),
`apps/demo-streamlit/tests` (Streamlit `AppTest`: the demo factory loads and only 2025-09 and 2026-07 are Low).
pytest runs with `--import-mode=importlib` so the three `tests/` folders don't collide.

### D8. Local verification limits
Docker is not installed on the development machine used for Phase 1, so the Dockerfile is verified by the
CI `docker` job only. The CI workflow's Python steps were run locally with the same commands.

## Phase 2 — database, seed, API on Postgres

### D9. Local Postgres without Docker: pgserver
`pgserver` (PyPI, dev dependency only) ships Postgres 16 and pgvector as a wheel. With no `DATABASE_URL` in
development, C-Trace starts or reuses it in `.pgdata/` (`make db-start` / `make db-stop`). So
`make setup && make test && make demo` works on a clean machine with no Docker. CI and production set
`DATABASE_URL`; CI runs the tests against the stock `pgvector/pgvector:pg16` image, so the migrations are
proven on a normal server too. **Risk:** pgserver is a small project (0.1.x); it is never used outside dev and tests.

### D10. Ledger = every non-rejected record, rebuilt per factory
The prototype counted every extracted bill, and review edited values in place. To keep totals identical
(the Phase 2 acceptance check) the ledger is rebuilt in full for a factory after each extraction or review
action, from all activity records whose status is not `rejected`. The summary reports
`records_by_status` and `unconfirmed_records`, so a report can state how much of the data a human has
confirmed. Re-extraction never overwrites a record a human has confirmed, corrected or rejected.
**Revisit if:** reports must only use confirmed data. That would be a filter on status, not a schema change.

### D11. `ledger_entries` primary key is `(factory_id, trace_id)`
The brief says `trace_id` is the primary key. But a trace ID is built from period, activity and document hash,
and documents are unique per factory (§6), so the same file uploaded to two factories would collide. Scoping the
key by factory keeps trace IDs human-readable and still unique.

### D12. Tenancy: RLS keyed on a per-transaction setting, enforced by a non-owner role
Every org-owned table carries `org_id`, with composite foreign keys `(factory_id, org_id)` so a child row can't
point at another organisation's factory. Policies compare `org_id` with `ctrace_current_org()`, which reads
`ctrace.org_id`. The API sets that after verifying the token and membership, inside a transaction that runs
`SET LOCAL ROLE ctrace_app`. A connection owner bypasses RLS; `ctrace_app` does not. Connections are
autocommit, so each request's transaction is top-level and `SET LOCAL` can't leak.
Reference tables (`emission_factors`, `weather_monthly`, `kb_chunks`) are read-only for `ctrace_app`, and
`audit_log` is append-only. Role checks (who may upload, review, open raw bills) are done in the API layer.
Buyers and lenders can't download original bills. **Not yet tested on Supabase:** creating the role and
`GRANT ctrace_app TO current_user`. The migration logs a notice instead of failing if the grant is refused.

### D13. Auth: Supabase JWKS, no HS256
We read the Supabase docs (auth/jwts, auth/signing-keys, auth/jwt-fields, Nov 2026). Current projects sign with
asymmetric keys (ES256 recommended, RS256 supported). Tokens are verified against
`<SUPABASE_URL>/auth/v1/.well-known/jwks.json`, with `iss = <SUPABASE_URL>/auth/v1` and `aud = "authenticated"`,
and keys are cached for at most 10 minutes (Supabase caches the endpoint for 10 minutes and recommends no longer
app caches). The docs advise against verifying legacy HS256 secrets locally, so C-Trace doesn't accept HS256.
Development uses a locally generated ES256 key pair (`make dev-token`), accepted only when `CTRACE_ENV=development`.
A verified `sub` must belong to a C-Trace user with a membership. Users in several organisations send
`X-C-Trace-Org`. Sign-up and onboarding (creating users and organisations) come with the web app (Phase 7).

### D14. Job queue: a plain `jobs` table
Uploads store the file and enqueue `extract_document`. Workers claim jobs with `FOR UPDATE SKIP LOCKED`, retry
with exponential backoff up to `max_attempts` (default 3), and reclaim jobs left `running` for more than
10 minutes. No Redis and no extra dependency. The seed uses the same path (`run_pending`), so the demo exercises
the production code.

### D15. Upload idempotency comes from the content hash
Documents are unique per `(factory_id, sha256)`. Re-uploading the same bytes, under any file name, returns the
existing document with "Already uploaded on 19 Nov 2026 as <name>" and queues nothing. A separate
`Idempotency-Key` header would add nothing for file uploads.

### D16. Document storage is local in this phase
Files go to `data/storage/<org>/<factory>/<sha256>.<ext>` (or `CTRACE_STORAGE_DIR`), and authorised roles
download them through `GET /v1/documents/{id}/file`. Supabase Storage with short-lived signed URLs replaces this
behind the same `Storage` interface in the deployment phase, after reading its docs.

### D17. Emission factors have stable IDs and are immutable
`ipcc2006-natural_gas-v1`, `ipcc2006-diesel-v1` and `ember-bgd-<year>-v1` are mirrored from `ctrace.factors`
into `emission_factors` with source and source URL. If a synced ID's value changes, sync is refused: a new
value needs a new version. Values are unchanged from the prototype. Phase 4 adds validity windows and admin
entry of the official national grid factor.

### D18. Stateless prototype endpoints moved to `/v1/stateless/*`, behind auth
`/extract`, `/analyze` and `/report` were unauthenticated. With an API key set they spend LLM tokens, so they
now require a valid token. They store nothing. The Streamlit fallback calls `ctrace` directly and doesn't use them.

## Phase 3 — extraction v2

### D19. Structured outputs, not forced tool use
The brief asked for "tool use with a strict JSON schema". The current docs (Claude API skill and the
structured-outputs page, Nov 2026) say `claude-sonnet-5-5`, our escalation model, rejects forced
`tool_choice` (`any`/`tool`) with a 400. `tool_choice: auto` doesn't guarantee a call. Structured outputs
(`output_config.format` with a JSON schema) give the same schema guarantee on every model, so both models
use that one mechanism. The schema follows the documented limits: `additionalProperties: false` everywhere,
no numeric or length constraints, and every field required with no union types (the limits are 24 optional
and 16 union parameters). A static test enforces this.

### D20. The model returns printed text; code parses it
Each field is `{value, evidence, confidence}`, and `value` is the string as printed (Bangla digits, lakh
grouping, "MWh"). C-Trace converts digits, numbers, months and units in code (`ctrace.textnorm`,
`ctrace.extraction.finalize`). Rule 1 of the brief ("AI reads; code calculates") then holds down to unit
conversion, and the same normaliser serves the rules path.

### D21. Evidence check and confidence
A numeric value whose digits don't appear in its own quoted evidence has its confidence capped at 0.5 and gets
an issue. This catches a reader that "corrects" a number. A document's confidence is the minimum over
document type, period and quantity. Below `CTRACE_ESCALATE_BELOW` (0.7) the cascade re-reads with the
escalation model and keeps the better read. If that is still below 0.7, the record says a human should check it.
For text-layer PDFs, a disagreement on quantity between the rules parser and the vision read is raised too.

### D22. Model settings
- Extraction runs at `effort: low` on Haiku 5.5 and Sonnet 5.5. Thinking can't be disabled on Sonnet 5.5,
  and effort is the documented control.
- The Sonnet 5.5 escalation call opts into the server-side refusal fallback (`fallbacks: "default"`, beta
  `server-side-fallback-2026-07-01`), as the Claude API guidance recommends for that model. Set
  `CTRACE_LLM_FALLBACKS=off` to remove it. Haiku 5.5 has no server-side fallback; a refusal there escalates to Sonnet.
- `refusal` and `max_tokens` stop reasons are never parsed as data.
- Images are downscaled to 2576 px on the long edge (the current models' limit) and kept under 5 MB, which
  also satisfies Bedrock's per-image limit. PDFs over 30 MB are refused before the call (the request limit is 32 MB).
- System-prompt caching is not set: the prompt is far shorter than the minimum cacheable prefix.

### D23. Caching and the call log
Reads are cached by `(document SHA-256, model, prompt version)`. The store uses an org-scoped table
(`llm_cache`, RLS), so one organisation's paid read is never served to another. The demo, the stateless API and
the eval use a local file cache (`data/cache/llm/`, git-ignored). Every real call writes an `llm_calls` row
(purpose, model, prompt version, tokens, latency, document hash, stop reason, error). The extraction job runs in
three short transactions, so no transaction is open during a model call.

### D24. Personal data
The prompt forbids personal data in evidence, and code removes it again (`redact_personal`): values after
name/address/phone/email/NID labels in English and Bangla, emails, and Bangladeshi mobile numbers in either digit
set. The schema has no field for a person. The eval counts privacy leaks per method.

### D25. Eval set
36 fictional bills (`data/eval/bills/`, 5 MB, committed) with ground truth generated from the same spec that prints
each bill. They cover English text PDFs in three layouts, traps, English and Bangla phone photos, Bangla text-layer
PDFs and mixed-script bills. Bangla is rendered with CoreText through a small Swift script, because Pillow on this
machine lacks raqm shaping. Regenerating the set needs macOS; evaluating doesn't. The brief asked for "anonymised real
ones with consent" too: none are available yet, and the model card says the synthetic set is an upper bound for the
rules parser.
Finding: pdfplumber returns Bangla from these PDFs in visual order, which is common in the field. The rules path
reorders pre-base vowel signs when a document shows that pattern. Conjuncts drawn as private glyphs can't be
recovered, which is why the rules path scores 25% on Bangla PDFs.

### D26. LLM methods are not yet scored
No Anthropic key was available while building Phase 3. The eval, the fake-client tests and the live contract test
(`packages/ctrace/tests/test_anthropic_contract.py`, skipped without a key) are ready. The card shows "not run"
for haiku, sonnet and cascade until `make eval-extraction` runs with `ANTHROPIC_API_KEY`. The brief's "recorded with
VCR" contract tests are replaced by that live test plus SDK-shaped fakes, because a cassette can't be recorded
without a key.

### D27. Rate limits
The stateless `extract` and `report` endpoints call the LLM synchronously. They are limited to
`CTRACE_LLM_RATE_PER_MIN` (20) per user per API instance (in-process sliding window). Uploads are capped at 50 files
per request, and their extraction runs in the worker one job at a time.

## Phase 4 — factor registry and public data

### D28. Climate TRACE v7, not v6
The starter script called `GET /v6/assets`. Climate TRACE's data page now points to API v7, and its official Python
wrapper (`climatetracecoalition/climate-trace-tools`) uses `GET /v7/sources?gadmId=BGD&subsectors=…&year=&gas=co2e_100yr`.
We verified that with a live request and recorded the response as a test fixture. The data is CC BY 4.0 and the
API is beta ("keep volume low"), so we fetch one year, page by page, by script only. It returns 3,379 Bangladeshi
`textiles-leather-apparel` sources whose values are identical across 2023–2025, because they are modelled from capacity.

### D29. The benchmark compares Scope 1 only and names no other factory
Climate TRACE estimates are modelled, mostly from on-site combustion. The benchmark places the factory's annualised
Scope 1 (last 12 months × 12 / n) in that distribution, shows quantiles and a percentile, and carries the caveat and
the attribution. It is never used to grade a month. Other facilities' names stay in the CSV (public data) and are
not shown in the UI.

### D30. NASA POWER weather
`/api/temporal/monthly/point`, `community=SB`, T2M. The response is keyed `YYYYMM`, with month `13` as the annual mean
and fill value -999. On 2026-11-20 values were published through 2026-09. The series is cached per 0.5° cell (the
demo factory uses cell 24.0, 90.5) and copied into `weather_monthly` at seed time. Months without a POWER value fall
back to the documented Dhaka climatology, and the API labels every month with its source. CDD keeps the simulator's
definition (max(0, T − 24 °C) × days), so real and simulated weather are comparable.

### D31. Official grid factor sits alongside, never instead
The ledger keeps the location-based Ember factor. A C-Trace platform admin (`CTRACE_PLATFORM_ADMIN_EMAILS`; factors are
global reference data, so organisation owners cannot write them) can enter the official national grid factor with its
publication URL and validity window. Overlapping windows are refused, and the entry is audit-logged. The summary then
reports `scope2_statutory` for the months it covers. No official value is seeded: we have no verified publication
for it, and typing a number in would break the brief's "do not invent" rule.

### D32. Provenance file
`data/public/PROVENANCE.json` records source, URL, licence, files and retrieval time for every fetched dataset.
`GET /v1/data-sources` serves it, so the UI and reports can cite it.

## Phase 5 — C-Trace Verifier v2

### D33. Real weather in training and scoring
The v2 fleet places each simulated factory in one of four NASA POWER cells (Gazipur/Savar, Dhaka/Narayanganj,
Chattogram, Mymensingh/Bhaluka; shares are an assumption) and drives its cooling load with that cell's real monthly
T2M, 2022-01 → 2025-12 (48 months). The location is drawn after every v1 random draw, so the v1 fleet stays bit-for-bit
reproducible (tested). Scoring uses the factory's own `weather_monthly` series, with labelled climatology for
unpublished months.

### D34. Calibration: heteroscedastic, rate-controlled, per factory type
v1 used one robust (MAD) σ per carrier. On real-weather data that ignored heavy tails: 4.7% of clean held-out months
were graded Low, and small factories were flagged 2.5× as often as large ones, which fails the brief's 1.5× rule.
Two intermediate attempts are recorded here because they failed:
1. σ per size tercile: small/large went 2.5× → 1.66×, still failing.
2. A quantile-GBM scale fitted on half the calibration factories: too little data, and overall false flags *rose* to 6.5%.

Released method: σ(x) = shape(x) × k. The shape is a quantile-GBM (q = 0.9) of |calibrated residual| fitted on
4-fold *out-of-fold* residuals of the 360 training factories. k is set per carrier and per factory type (Mondrian
conformal) on the 90 calibration factories, so that a fixed share of clean months reaches the Low threshold: gas and
grid 0.5%, diesel 0.1%, since diesel is advisory by the prototype's documented policy and should only flag gross
errors. A type with too few calibration months uses the pooled k. Result on the 150 held-out factories: 1.0% false
Lows, small/large 1.01×, and F1 at the app's thresholds better than v1 on every carrier (gas 0.868 → 0.927, grid
0.754 → 0.826, diesel 0.429 → 0.474). Sweater factories remain at 3.2% false Lows; the card says so.

### D35. Expected band
Split-conformal 90% interval in log space, centre ± q·σ(x), with q from the calibration factories. Held-out coverage is
88.9% / 90.6% / 89.9% (gas / grid / diesel). The band replaces the single "expected" line on the Verify chart.

### D36. Drivers: exact group Shapley against the factory's typical month
SHAP for HistGradientBoosting would add a dependency and explain log-space margins. C-Trace computes exact Shapley
values over four feature groups (production, weather, solar, season): 16 coalitions, interventional, with features
outside a coalition set to the factory's own mean over the scored months. Contributions are in the carrier's unit and
sum exactly to expected − typical (tested). The profile (type, size, wet processing, captive generation) is the
factory's own and contributes zero against its own baseline.

### D37. Diesel thresholds measured where they are used
v1's card reported precision/recall at |z| ≥ 3 for every carrier, but the app grades diesel Low at |z| ≥ 5. v2 reports
detection at the app's Low thresholds (and keeps the @z3 numbers in metrics.json). The v1 comparison uses the same
thresholds.

### D38. Acceptance
The demo factory flags exactly 2025-09 and 2026-07 under v1 and v2, with both climatology and NASA POWER weather
(parametrised test). The first v2 calibration flagged 2025-07 diesel (z = 5.6, natural outage variation in the sample
data). The fix was D34's diesel policy, not a threshold tuned to the demo.

### D39. Registry and monitoring
`models/ctrace-verifier/<version>/{model.joblib, metrics.json, MODEL_CARD.md}`; `CTRACE_VERIFIER_VERSION` (default v2).
v1 pickles still load (new attributes are read through `getattr`). Drift: the worker checks weekly whether the robust
spread of z across every factory's latest run moved more than 30% from the spread on clean calibration months
(`drift_baseline_z`). It needs at least 60 months, so a small fleet never raises a false alarm.

### D40. Verification persistence
`verification_runs` remembers the latest activity-record change it saw, so the API reports a run as `stale` after any
correction. A human-reviewed (confirmed/corrected) bill's extraction warnings no longer count as document issues.
Resolution is one of `confirmed_correct | corrected | evidence_requested | not_an_issue` plus a required comment, and
is audit-logged.

## Phase 6 — reduction proof v2 and certificate

### D41. ASHRAE Guideline 14 limits
For monthly whole-facility baselines, ASHRAE Guideline 14-2014 is widely cited for CV(RMSE) ≤ 15% and |NMBE| ≤ 5%. The
standard itself is paywalled, so these are the commonly published values, not a quotation; they are kept in one place
(`ctrace.reduction.LIMITS`). R² ≥ 0.75 and at least 12 baseline months (a full operating cycle) follow common IPMVP
Option C practice. "Verified" needs all of them **and** a 90% interval whose lower bound is above zero; otherwise the
proof is stored as not verified, with the failed checks named, and it cannot be signed.

### D42. Windows come from declared interventions and the carriers they affect
`interventions.affects` (natural_gas / grid_electricity / diesel) is set explicitly or inferred from the measure text
(boiler/steam → gas; solar/LED/chiller/VFD → grid; genset → diesel). If neither works, the API asks. For each intervention
and carrier, the baseline runs from the previous intervention on that carrier (or the start of data) to the month before,
and the reporting period runs to the next intervention on that carrier (or the end). Overlapping effects across carriers
(e.g. solar also lowering captive-generation gas) are attributed to the declared carrier only; the certificate names the
intervention, so a reviewer sees it.

### D43. Low months are excluded per carrier
A month is left out of a carrier's proof only if the latest verification graded it Low for a reason that concerns that
carrier (its z-score, or a problem with one of its documents). July 2026's missing diesel receipt therefore does not remove
July's gas or grid data. A stale verification (data changed since) blocks computing. One consequence is good for the demo
story: until the dropped-digit gas bill is corrected, 2025-09 is excluded, the gas baseline has 11 months, and the boiler
saving is *not verified*. Accepting the correction fixes it.

### D44. Conversion
Avoided tCO2e = Σ over reporting months of (predicted − actual) × the factor valid in that month (the same factor ids the
ledger used). The interval is converted with the mean reporting-month factor. BDT = avoided × the factory's own median
implied tariff for that carrier (amount ÷ quantity over its bills).

### D45. Sign-off and certificate
Proofs are stored as `draft`; recomputing supersedes drafts and keeps signed proofs. Only a verified draft can be signed, by
an owner, manager or reviewer, with a comment, and the signature is audit-logged. The PDF certificate is labelled
"C-Trace verification statement — not a third-party assurance or carbon credit", shows DRAFT until signed, and lists every
document it used by trace ID and hash.

## Phase 7 prerequisites — demo access and serverless hosting

### D46. Live demo sandboxes
Judges and pilot prospects need to try C-Trace in seconds, without an account. `POST /v1/demo/start` creates a private
organisation flagged `is_demo`, loads the fictional demo factory through the normal upload path, and returns a 6-hour
token signed with an ES256 key the API generated and keeps in `app_secrets` (owner-only, no app-role access). The API
accepts that issuer **only** for demo organisations, so a demo token cannot reach a real organisation even if a membership
existed (tested). Demo identities can't create organisations or enter factors. Sandboxes are deleted after 24 hours, on
the next start. Starts are limited to 10 per client IP per hour, per API instance. `CTRACE_DEMO_ENABLED=0` turns the demo off.

### D47. Onboarding
A verified Supabase identity without a membership calls `POST /v1/onboarding` (organisation name) and becomes its owner.
Inviting members to an organisation comes with Settings (Phase 8).

### D48. Serverless: inline jobs and Postgres storage
Vercel has no long-running worker and no persistent disk. `CTRACE_INLINE_JOBS=1` (the default) reads newly uploaded bills
inside the upload request; the worker remains the path for containers. `CTRACE_STORAGE=db` keeps original documents in
`document_blobs` (org-scoped, RLS) instead of local files. The 4.5 MB request-body limit on Vercel Functions caps a single
upload request; the UI sends files one per request. `CTRACE_API_PREFIX` mounts the API under a path (`/api`) for
path-based routing.

## Phases 8–9 — reports, sharing, Copilot

### D49. Share tokens are stored as hashes
A share link carries a 256-bit random token. Only its SHA-256 is stored, so a database leak does not expose working
links. Re-sharing replaces the token, and revoking or expiring returns HTTP 410. Each view (page, PDF, document) is logged
with a truncated hash of the client address and the user agent, which the owner sees in the app. Original bills behind a
shared report are available only when the owner ticks "include documents", and only for documents inside the report's period.

### D50. Invitations
Owners invite by email with a role. The invitation is accepted automatically the first time that verified email signs in.
The last owner cannot be demoted or removed. Invitations are disabled in demo sandboxes.

### D51. The Copilot never computes a number
Tools are deterministic code over the factory's data (summary, ledger, verification, reductions, guidance). Month-over-month
changes are computed in the summary tool, not by the Copilot. Every answer must cite trace, reduction, verification or
knowledge-base IDs, and must pass the number guard against the tool results. A draft that fails gets a safe "could not
answer with verified figures" reply (tested by blocking an invented figure). The guard accepts a value's magnitude (for
example "down 12.3 t" for −12.3), since that is still the verified figure. Offline, intents are routed in English and Bangla
with templated answers. With a key, Claude runs a tool loop (tool_choice auto with strict tool schemas; Sonnet 5.5 rejects
forced tool use), and an uncited or guard-failing answer falls back to the offline answer. Off-topic questions are declined.
The endpoint streams server-sent events: one per tool call, then the answer.

### D52. RAG v2 without dense embeddings, for now
The brief names `intfloat/multilingual-e5-small` via sentence-transformers. That needs PyTorch plus about 470 MB of
weights, which doesn't fit the Vercel Python function budget alongside the scientific stack. Retrieval is hybrid lexical:
BM25 plus TF-IDF uni- and bigrams, fused with reciprocal-rank fusion. It has stable chunk IDs for citations, and a Bangla→English
domain-term map so Bangla questions reach the English guidance. `kb_chunks` mirrors the chunks, with the `vector(384)` column
ready. The upgrade path is to compute embeddings at build time with an ONNX export and serve queries from a separate container.
Guidance chunks remain C-Trace's own summaries with the source to consult; no standards text is copied.

### D53. A monotonic solar constraint, found through the explanations
The driver waterfall showed the unconstrained v2 model giving rooftop solar a *negative* grid contribution both in a month
without panels and in a month with 600 kWp. That's a non-monotonic response no physics supports. HistGradientBoosting's
`monotonic_cst` fixes it. Three constraint sets were evaluated on the same held-out factories:
| Constraints | Gas / grid / diesel F1 | Small ÷ large false Lows | Release |
|---|---|---|---|
| none (previous v2) | 0.927 / 0.826 / 0.474 | 1.01× | — |
| all physical signs (workers, wet, captive, solar, pieces, CDD) | 0.931 / 0.835 / 0.476 | 3.91× | fails fairness |
| pieces, CDD, solar | 0.932 / 0.861 / 0.484 | 1.84× | fails fairness |
| **solar only (released)** | **0.938 / 0.846 / 0.491** | **1.29×** | passes |
The released model is better than both v1 and the unconstrained v2 on every carrier. Its clean-month false-Low rate is
1.56% (vs 1.0% unconstrained), which the card reports.

### D54. Self-calibration regimes are per carrier
The prototype split every carrier's self-calibration at every intervention, so the three months between the boiler retrofit
and the solar install calibrated *grid electricity* on two other months. That was noisy enough to flag February 2026.
Each intervention now starts a new regime only for the carriers it affects (declared or inferred). Solar affects grid and
diesel, plus gas when the factory has captive generation.

### D55. The demo factory now lives in real weather
The fictional demo bills had been generated with the Dhaka climatology, while C-Trace now scores with NASA POWER weather at
the factory, so the demo data was slightly inconsistent with its own weather (March 2026 was cooler than climatology). The
bills are regenerated with the real Gazipur series from the same random stream: production, outages, the 12% boiler saving,
the 600 kWp solar and both planted errors are unchanged, and only cooling-driven use differs. The demo total moves from
15,466.6 to 15,297.9 tCO2e. `pipeline.run` uses the factory's NASA POWER series by default whenever the profile has a
location, so the prototype, the Streamlit fallback and the API see the same weather. Under this, v1 and v2 both flag exactly
2025-09 and 2026-07.

### D56. One Vercel project with two Services, the API at the repo root
Vercel Services host the Next.js app (`apps/web`) and the FastAPI app in one project, with a top-level rewrite sending
`/api/*` to the API. The browser only calls same-origin `/api`, so there is no CORS and no API URL in the bundle. The API
service's root is the repository root, because the API depends on the `ctrace` workspace package and on `data/` and
`models/`; Vercel's Python builder installs the uv workspace straight from `uv.lock` (tested with `vercel build`). `asgi.py`
is the entrypoint. Services forward the original path, so the API keeps `CTRACE_API_PREFIX=/api`. Region bom1 (Mumbai).

### D57. The Streamlit fallback's dependencies are an optional extra
streamlit, plotly and their dependencies (pyarrow alone is 165 MB) took the API bundle to 752 MB, over Vercel's 500 MB.
They move to the `demo` extra: `make setup`, CI and `requirements.txt` (Streamlit Community Cloud) use `--all-extras`, and
the API image and the Vercel function don't install them. The API bundle is about 375 MB; Vercel installs the part that
doesn't fit its base bundle at cold start (measured: 3.4 s).

### D58. Serverless-safe startup
On Vercel the lifespan runs, but a failure there kills the process, so a missing `DATABASE_URL` gave an opaque 500. Startup
(migrate, factor and KB sync, pool) is now idempotent, runs from lifespan and, as a fallback, on the first request, and a
failure is reported: `/api/health` says `database: error`, other routes answer 503 with a clear message. Migrations run on
startup under the existing advisory lock, so the Supabase credential lives only in Vercel's environment. Transaction-mode
poolers are supported with `CTRACE_DB_PREPARE=0` (no server-side prepared statements). Weather and LLM caches skip writing
on a read-only disk.

### D59. Locked out of Supabase's Data API, and tested as a non-superuser
Supabase gives its Data API roles (`anon`, `authenticated`) default privileges on every table in `public`. RLS already gave
them no rows, because every C-Trace policy is `to ctrace_app`, but C-Trace never uses the Data API, so migration
`20261120000007` revokes those grants and default privileges (a no-op where the roles don't exist). All migrations, factor
and KB sync, and the demo flow were run on a fresh Postgres cluster as a non-superuser owner with `CREATEROLE` (how
Supabase's `postgres` role works), with `CTRACE_DB_PREPARE=0`: the owner can `SET ROLE ctrace_app`, no grants remain for
`anon`/`authenticated`, and the demo flags exactly 2025-09 and 2026-07.

### D60. Supabase through Vercel's integration, functions pinned to Mumbai
The Supabase project (`c-trace-db`, free plan, Mumbai) was created through Vercel's Supabase integration, so no credential
passed through anyone's hands: Vercel injects `POSTGRES_URL` and friends. `database_url()` falls back to `POSTGRES_URL`,
drops non-libpq URL parameters (`supa=`), and switches Supabase's transaction pooler (6543) to its session pooler (5432),
which behaves like a direct connection. The project's function default region was `iad1`, so every query crossed from the
US to Mumbai (186 ms round-trip, about 4 s per bill, a demo start over 300 s). The region is now `bom1`: 1.9 ms per
round-trip, and a demo start takes about 7 s. `/api/health` reports `database_roundtrip_ms` so this is visible.

### D61. A free OpenAI-compatible provider beside Claude
The team asked for a free ChatGPT-family model for the hosted demo. GitHub Models was retired on 30 Jul 2026, so the free
option is OpenAI's open-weight `gpt-oss-120b` on a free tier (OpenRouter by default; Groq and Cerebras also serve it).
`ctrace.llm_openai` speaks the plain `/chat/completions` format over httpx (no SDK), so any compatible host works by
changing `CTRACE_OPENAI_BASE_URL`. Claude is used when `ANTHROPIC_API_KEY` is set; otherwise `OPENAI_API_KEY` selects this
path. The rules are unchanged: the model returns printed text with evidence, code parses and checks it, and Copilot and
narrative text pass the number guard. Extraction asks for `json_schema`; a host that rejects it gets `json_object` with
the schema in the versioned `prompts/openai_json/v1.md`. gpt-oss reads text only, so PDFs go as their text layer and
photos are refused with a plain message unless `CTRACE_OPENAI_VISION=1`. A rate-limited or failing endpoint falls back
to the offline parser or the offline Copilot, saying so. The demo factory's sample bills (matched by SHA-256) always use
the deterministic parser: each sandbox re-reads all 65, which would exhaust a free quota and change the pitch numbers.
