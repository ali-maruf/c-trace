# CLAUDE.md — C-Trace

Product: **C-Trace — Trace every tonne.** Verifiable carbon MRV for Bangladeshi factories. The full brief is in
`docs/CLAUDE_CODE_PROMPT.md`; decisions and assumptions go in `docs/DECISIONS.md`; each phase starts with
`docs/plans/phase-N.md`.

## Rules
- Brand is always "C-Trace" (capital C, hyphen, capital T). Package `ctrace`, slug `c-trace`.
- AI reads and explains; deterministic code calculates. No LLM output is ever an emissions number.
- Every ledger row stays traceable: trace_id, document SHA-256, factor version and source, arithmetic.
- LLM text must pass `number_guard` before it reaches a user or a PDF.
- Simulated data is labelled simulated; the demo factory is fictional.
- No placeholder features, fake data in the UI, empty scaffolding or invented API endpoints. Check the docs first.
- Never ship a Verifier version that fails the fairness rule or does worse than the previous version on the same test (see `test_verifier_v2.py`).
- Never regress `make test`. The demo factory must flag exactly 2025-09 (dropped-digit gas bill) and 2026-07 (missing diesel).
- Model IDs live in config / env vars, never in business logic.
- Store functions run inside `org_tx` (role `ctrace_app`, RLS on). Never compute emissions in SQL; never bypass RLS for user requests.
- Every review action (confirm/correct/reject) and factor change writes `audit_log`.
- LLM prompts are versioned files in `packages/ctrace/ctrace/prompts/<name>/vN.md` (+ `vN.schema.json`); a prompt change is a new version.
- LLM calls use structured outputs, never forced `tool_choice` (Sonnet 5.5 rejects it); models return printed text, code parses it.

## Layout
- `packages/ctrace/` — Python core (`import ctrace`), tests in `packages/ctrace/tests/`
- `packages/ctrace/ctrace/store/` — Postgres layer (extra `ctrace[db]`): migrations runner, RLS-scoped `org_tx`, jobs, service, seed
- `infra/supabase/migrations/` — SQL migrations (never edit an applied one; add a new file) · `infra/docker-compose.yml`
- `services/api/` — FastAPI (`ctrace_api.main:app`), endpoints in `v1.py`, auth in `auth.py`; `services/worker/` — job runner
- `apps/demo-streamlit/` — Streamlit demo, the offline hackathon fallback
- `ml/train.py` — C-Trace Verifier training + model card; `scripts/` — data fetchers and sample-data generator
- `data/`, `models/` — resolved via `ctrace.config.find_root()` (`CTRACE_ROOT` overrides)

## Commands (uv workspace, Python 3.12, versions pinned in `uv.lock`)
- `make setup` — install everything from the lockfile
- `make train` — train the C-Trace Verifier (v2 by default; `--version v1` reproduces v1) into `models/ctrace-verifier/<v>/` and regenerate the model card
- `make test` / `make lint` / `make typecheck` / `make check` (all three, same as CI)
- `make demo` — seed the fictional demo factory into Postgres · `make api` (:8000/docs) · `make worker` · `make dev-token`
- Local Postgres needs no Docker: pgserver in `.pgdata/` (`make db-start`/`db-stop`); `DATABASE_URL` overrides
- `make demo-streamlit` — offline Streamlit fallback on :8501
- `make eval-extraction` — score extraction on `data/eval/bills/` and refresh the model card · `make eval-bills` (macOS) regenerates the set
- `make sample-data` — regenerate the demo bills · `make requirements` — re-export `requirements.txt` after changing deps
