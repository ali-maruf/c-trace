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
