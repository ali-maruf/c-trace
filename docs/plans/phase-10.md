# Phase 10 — Deploy, harden, document

## Goal
One public URL that a judge can open on a phone: the C-Trace web app and its API on Vercel, Postgres on Supabase,
and the live demo sandbox working end to end (bills → review → ledger → Verifier → reductions → reports → share link →
Copilot). Done when the Playwright demo flow passes against the production URL on desktop and mobile.

## Deployment shape (D56–D58)
- **One Vercel project, two Services** (`vercel.json`): `web` = `apps/web` (Next.js) and `api` = the repo root
  (FastAPI via `asgi.py`). A top-level rewrite sends `/api/*` to `api`; everything else goes to `web`. The browser only
  ever calls same-origin `/api/...`, so there is no CORS and no API URL baked into the frontend.
- The API keeps its own path prefix (`CTRACE_API_PREFIX=/api`): Services forward the original path.
- **Function region bom1 (Mumbai)**: closest to Dhaka. Supabase should be created in South Asia (Mumbai) as well.
- **Supabase Postgres** through the Session pooler (IPv4). `CTRACE_DB_PREPARE=0` keeps transaction-mode poolers working
  too; the per-instance pool is small (`CTRACE_DB_POOL_MIN=0`, `CTRACE_DB_POOL_MAX=3`).
- Migrations, factor sync and knowledge-base sync run on startup (`CTRACE_MIGRATE_ON_START=1`) under an advisory lock, so
  the database credential lives only in Vercel's environment and never in CI or on a laptop.
- Documents are stored in Postgres (`CTRACE_STORAGE=db`); the function filesystem is read-only apart from `/tmp`.
- Extraction jobs run inline after upload (`CTRACE_INLINE_JOBS=1`); no separate worker is needed on Vercel.

## Bundle
- The API bundle installs from `uv.lock` (the uv workspace works as-is). The Streamlit fallback's dependencies are now the
  `demo` extra, so the API bundle drops from 752 MB to about 375 MB. Vercel installs the dependencies that don't fit in the
  base bundle at cold start (measured: 3.4 s).
- `excludeFiles` drops the web app, docs, ML training, tests, evaluation bills, the v1 model weights and local state;
  `includeFiles` keeps `data/public/**` (Vercel excludes any `public/` folder by default).

## Hardening
- A missing or unreachable database no longer kills the process: `/api/health` reports it, other routes answer 503.
- Startup also runs on the first request, so the app works even where lifespan events are skipped.
- Caches (weather, LLM) tolerate a read-only disk.
- Demo starts are limited per client IP (`CTRACE_DEMO_RATE_PER_HOUR=30` in production, since a venue's wifi shares an IP).

## CI
- New `web` job: ESLint, `tsc`, Vitest unit tests (formatting, API client), `next build`.

## Docs
- `docs/ARCHITECTURE.md`: system, data flow, trust boundaries (Mermaid).
- `docs/PITCH_DEMO.md`: the 3-minute finals script, with the fallback plan.
- README: live URL, web app, deploy steps.

## Not done / risks
- The demo limiter is per instance (in memory); a shared limiter needs Redis or a Postgres table.
- Supabase Auth sign-in needs `SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_URL` and `NEXT_PUBLIC_SUPABASE_ANON_KEY`; without them
  the hosted app offers the demo sandbox only.
- Vercel caps request bodies at 4.5 MB, so the web app uploads one file per request and refuses files over 4 MB with advice
  (photograph at a lower resolution, or upload the PDF).
- `docker compose up` remains untested locally (no Docker on the build machine); CI validates the compose file and builds
  the image.
