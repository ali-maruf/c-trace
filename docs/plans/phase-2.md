# Phase 2 — Database, seed, API on Postgres

## Goal
Persist the C-Trace data model in Postgres and serve the API from it. Done when
`GET /v1/factories/{id}/summary` for the seeded demo factory returns the same totals as the
prototype pipeline (`ctrace.pipeline.run`) on the same 65 bills.

## Database
- One SQL migration (`infra/supabase/migrations/20261119000001_init.sql`) with every table in brief §6,
  plus `jobs` (Postgres queue, `FOR UPDATE SKIP LOCKED`) and `llm_calls` (§7).
- Supabase-CLI compatible file naming; a small runner (`python -m ctrace.store.migrate`) applies
  pending files in order and records them in `schema_migrations`, so plain Postgres works too.
- Row-level security on every org-owned table: `org_id = ctrace_current_org()`, where the API sets
  `ctrace.org_id` per transaction and switches to the non-owner role `ctrace_app` (`SET LOCAL ROLE`),
  so policies apply even though the connection user owns the tables.
- Composite foreign keys `(factory_id, org_id) → factories(id, org_id)` so a child row can never point
  at another organisation's factory.
- Reference tables (`emission_factors`, `weather_monthly`, `kb_chunks`) are readable by `ctrace_app`
  and writable only by the migration/admin role.

## Local Postgres without Docker
`pgserver` (PyPI, dev dependency) bundles Postgres 16 + pgvector. If `DATABASE_URL` is unset in
development, C-Trace starts/reuses a server in `.pgdata/`. Production and CI set `DATABASE_URL`
(CI uses the `pgvector/pgvector:pg16` service container, so migrations are also tested on a stock image).

## Code
- `ctrace/factors.py`: every factor gets a stable `factor_id`, `version`, `year`, `source_url`;
  the ledger carries `factor_id`. Values unchanged.
- `ctrace/store/`: `db` (connections, org-scoped transactions), `migrate`, `storage` (local file store;
  Supabase Storage + signed URLs in Phase 10), `jobs` (enqueue/claim/complete/retry), `service`
  (ingest document with SHA-256 dedupe → extraction job → activity record → full ledger rebuild per
  factory; corrections with audit log; summary), `seed` (demo factory).
- `services/worker`: polls `jobs`; `--drain` processes everything queued and exits (seed, tests).
- `services/api`: `/v1` routers for me, factories, production, documents, jobs, activity records
  (confirm/correct/reject + suggestions), ledger, summary, factors; request IDs; JSON logs.
  The stateless prototype endpoints stay under the `stateless` tag (the Streamlit fallback path).

## Auth
Supabase access tokens verified locally against the project JWKS (ES256/RS256), checking `exp`,
`iss` and `aud="authenticated"`; JWKS cached ≤10 minutes per Supabase docs. `sub` must match a
C-Trace user with a membership. In development, a local ES256 key pair stands in for Supabase
(`make dev-token`). No HS256 shared-secret verification (Supabase recommends against it).

## Risks
- Ledger rebuild semantics: the prototype counts every extracted bill, and review edits in place.
  We keep that (rejected records excluded) so totals match, and expose how many records are
  still unconfirmed. Recorded in DECISIONS.
- `trace_id` is unique per factory, not globally (same file could be uploaded to two factories):
  primary key `(factory_id, trace_id)`.
- Supabase role behaviour (creating `ctrace_app`, `SET ROLE`) is tested only on stock Postgres in this phase.

## Tests
Migration idempotency; summary parity with the prototype (totals and every month); ledger rows reference
factor versions with source URLs; RLS isolation between two organisations and with no org set;
upload dedupe message; correction flow writes the audit log and changes the ledger; role checks;
auth failures; failed job retries then fails.
